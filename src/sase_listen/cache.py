"""Content-addressed LRU chunk cache (also the render resume mechanism).

Owner: engines phase.

The key is the sha256 of canonical JSON
`{"v": CACHE_VERSION, engine, model, voice, style, speed, sample_rate, text}`
where `text` is *after* lexicon application. Entries live at
`<root>/<key[:2]>/<key>.wav` plus a `.json` sidecar. Writes are atomic
(temp file plus rename), so concurrent writers always leave valid files;
hits refresh mtime for LRU ordering.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sase_listen.paths import cache_dir

#: Bump when the key schema or stored audio contract changes.
CACHE_VERSION = 1


def cache_key(
    *,
    engine: str,
    model: str,
    voice: str,
    style: str,
    speed: float,
    sample_rate: int,
    text: str,
) -> str:
    """Return the content-addressed key for one synthesis chunk."""
    canonical = {
        "v": CACHE_VERSION,
        "engine": engine,
        "model": model,
        "voice": voice,
        "style": style,
        "speed": speed,
        "sample_rate": sample_rate,
        "text": text,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class CacheHit:
    """A cache hit: s16le mono PCM payload plus its stored metadata."""

    pcm: bytes
    sample_rate: int
    duration_s: float
    words: int
    usage: dict[str, Any]


@dataclass
class CacheStats:
    """Point-in-time cache size counters."""

    files: int
    bytes: int


def _valid_key(key: str) -> bool:
    return len(key) == 64 and all(c in "0123456789abcdef" for c in key)


class ChunkCache:
    """Atomic-write, mtime-LRU chunk cache rooted at `root`."""

    def __init__(self, root: Path | None = None, *, max_gb: float = 2.0) -> None:
        self.root = root if root is not None else cache_dir()
        self.max_gb = max_gb

    def _paths(self, key: str) -> tuple[Path, Path]:
        if not _valid_key(key):
            raise ValueError(f"Invalid cache key: {key!r}.")
        directory = self.root / key[:2]
        return directory / f"{key}.wav", directory / f"{key}.json"

    def get(self, key: str) -> CacheHit | None:
        """Return the hit, refreshing mtime; None on miss or corruption."""
        wav_path, json_path = self._paths(key)
        try:
            with wave.open(str(wav_path), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                    raise ValueError("unexpected WAV layout")
                sample_rate = wav.getframerate()
                pcm = wav.readframes(wav.getnframes())
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            now = time.time()
            os.utime(wav_path, (now, now))
            os.utime(json_path, (now, now))
        except (OSError, ValueError, KeyError, json.JSONDecodeError, wave.Error):
            return None
        duration_s = len(pcm) / (2 * sample_rate) if sample_rate else 0.0
        usage = meta.get("usage", {})
        return CacheHit(
            pcm=pcm,
            sample_rate=sample_rate,
            duration_s=duration_s,
            words=int(meta.get("words", 0)),
            usage=usage if isinstance(usage, dict) else {},
        )

    def put(
        self,
        key: str,
        pcm: bytes,
        *,
        sample_rate: int,
        words: int,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Store `pcm` (s16le mono) atomically under `key`."""
        wav_path, json_path = self._paths(key)
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(wav_path, _encode_wav(pcm, sample_rate))
        meta = {
            "v": CACHE_VERSION,
            "sample_rate": sample_rate,
            "duration_s": len(pcm) / (2 * sample_rate) if sample_rate else 0.0,
            "words": words,
            "usage": usage or {},
            "created": time.time(),
        }
        _atomic_write_bytes(json_path, json.dumps(meta, sort_keys=True).encode("utf-8"))

    def stats(self) -> CacheStats:
        """Count stored entries and their total bytes."""
        files = 0
        total = 0
        if self.root.exists():
            for wav_path in self.root.rglob("*.wav"):
                try:
                    total += wav_path.stat().st_size
                    sidecar = wav_path.with_suffix(".json")
                    if sidecar.exists():
                        total += sidecar.stat().st_size
                    files += 1
                except OSError:
                    continue
        return CacheStats(files=files, bytes=total)

    def prune(
        self, *, max_gb: float | None = None, older_than_s: float | None = None
    ) -> dict[str, int]:
        """Evict oldest-first to `max_gb` and drop entries older than `older_than_s`.

        Returns `{"removed": n, "bytes_freed": b}`.
        """
        removed = 0
        freed = 0
        limit = int((self.max_gb if max_gb is None else max_gb) * 1_000_000_000)
        entries: list[tuple[float, Path, int]] = []
        if self.root.exists():
            for wav_path in self.root.rglob("*.wav"):
                try:
                    stat = wav_path.stat()
                    size = stat.st_size
                    sidecar = wav_path.with_suffix(".json")
                    if sidecar.exists():
                        size += sidecar.stat().st_size
                    entries.append((stat.st_mtime, wav_path, size))
                except OSError:
                    continue
        now = time.time()
        aged = (
            {p for mtime, p, _ in entries if now - mtime > older_than_s}
            if older_than_s is not None
            else set()
        )
        entries.sort(key=lambda item: item[0])
        total = sum(size for _, _, size in entries)
        for _mtime, wav_path, size in entries:
            if wav_path in aged or total > limit:
                freed += size
                total -= size
                removed += 1
                _remove_entry(wav_path)
        return {"removed": removed, "bytes_freed": freed}


def _encode_wav(pcm: bytes, sample_rate: int) -> bytes:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _remove_entry(wav_path: Path) -> None:
    for path in (wav_path, wav_path.with_suffix(".json")):
        with contextlib.suppress(OSError):
            path.unlink()
