"""Episode library: ids, atomic commits, locks, and manifest reads.

Owner: pipeline phase.

Episodes live under ``$XDG_DATA_HOME/sase-listen/library/<episode-id>/``.
Rendering stages files under ``library/.staging/<episode-id>/`` and then
replaces each file in the final directory with :func:`os.replace` (the
manifest last), so a re-render atomically replaces its episode and a crash
never leaves a half-written manifest behind.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.paths import library_dir, locks_dir


def slugify(title: str) -> str:
    """Turn an episode title into a filesystem-safe slug (up to 60 chars)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or "episode")[:60].strip("-") or "episode"


def compute_episode_id(title: str, source_key: str) -> str:
    """Return ``<slug>-<sha256(source_key)[:6]>`` for an episode.

    ``source_key`` is the artifact ref for ref inputs, else the absolute
    source path, so re-rendering the same source replaces its episode.
    """
    digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:6]
    return f"{slugify(title)}-{digest}"


def episode_mp3_name(slug: str) -> str:
    """Return the MP3 file name stored inside an episode directory."""
    return f"{slug}.mp3"


def episode_path(episode_id: str, root: Path | None = None) -> Path:
    """Return the library directory for ``episode_id``."""
    return (root if root is not None else library_dir()) / episode_id


def staging_path(episode_id: str, root: Path | None = None) -> Path:
    """Return the staging directory for ``episode_id``."""
    return (root if root is not None else library_dir()) / ".staging" / episode_id


@contextmanager
def episode_lock(episode_id: str, root: Path | None = None) -> Iterator[None]:
    """Hold an exclusive, non-blocking per-episode lock.

    Raises :class:`SaseListenError` (exit 1) when another render already
    holds the lock, so concurrent renders fail loudly instead of
    clobbering each other's commit.
    """
    lock_dir = root if root is not None else locks_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / f"{episode_id}.lock"
    with lock_file.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SaseListenError(
                f"Another render of episode '{episode_id}' is already running.",
                ExitCode.UNEXPECTED,
                hint="Wait for it to finish, then re-run.",
            ) from exc
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_commit(
    episode_id: str,
    payloads: Mapping[str, bytes],
    root: Path | None = None,
) -> Path:
    """Stage ``payloads`` and atomically replace the episode directory files.

    Every payload is written to ``.staging/<episode-id>/`` first, then moved
    into place with :func:`os.replace` (``manifest.json`` last). Returns the
    episode directory.
    """
    if "manifest.json" not in payloads:
        raise ValueError("atomic_commit requires a manifest.json payload.")
    library = root if root is not None else library_dir()
    staging = staging_path(episode_id, library)
    final = episode_path(episode_id, library)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        for name, data in payloads.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".tmp-{os.getpid()}-{name.replace('/', '_')}")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        final.mkdir(parents=True, exist_ok=True)
        ordered = [n for n in payloads if n != "manifest.json"] + ["manifest.json"]
        for name in ordered:
            os.replace(staging / name, final / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return final


def read_manifest(episode_id: str, root: Path | None = None) -> dict[str, object]:
    """Read and parse an episode's ``manifest.json``."""
    path = episode_path(episode_id, root) / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Manifest {path} must be a JSON object.")
    return dict(raw)


def list_episode_ids(root: Path | None = None) -> list[str]:
    """Return the sorted episode ids present in the library."""
    library = root if root is not None else library_dir()
    if not library.exists():
        return []
    ids = [
        child.name
        for child in library.iterdir()
        if child.is_dir()
        and child.name != ".staging"
        and (child / "manifest.json").exists()
    ]
    return sorted(ids)
