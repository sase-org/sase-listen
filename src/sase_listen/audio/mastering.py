"""PCM preparation and two-pass loudnorm mastering. Owner: audio phase."""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy import typing as npt

from sase_listen.audio.ffmpeg import FfmpegInfo
from sase_listen.errors import ExitCode, SaseListenError

Int16PCM = npt.NDArray[np.int16]

_INT16_SCALE = 32768.0
_WINDOW_S = 0.02
_THRESHOLD_DBFS = -50.0
_TRIM_KEEP_MS = 80.0
_COMPRESS_ABOVE_S = 1.5
_COMPRESS_TO_S = 0.7
_REPORT_ABOVE_S = 4.0
_LRA_DB = 11.0
_MASTER_TIMEOUT_S = 600
_LOUDNORM_JSON_RE = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


@dataclass
class ChapterAudio:
    """One chapter: a spoken heading plus its synthesis chunks as PCM.

    Segments are int16 mono arrays; ``sample_rates`` optionally records each
    segment's native rate (defaulting to the assembly rate) so mastering can
    resample off-rate engine output to the canonical rate.
    """

    title: str
    segments: list[Int16PCM] = field(default_factory=list)
    sample_rates: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class LongSilence:
    """An internal silence worth flagging to quality gates."""

    chapter: str
    seconds: float


@dataclass(frozen=True)
class AssembledEpisode:
    """Trimmed PCM with exact chapter offsets measured in samples."""

    pcm: Int16PCM
    sample_rate: int
    chapter_titles: list[str]
    chapter_start_samples: list[int]

    @property
    def chapter_start_s(self) -> list[float]:
        """Chapter offsets in seconds derived from sample counts."""
        return [s / self.sample_rate for s in self.chapter_start_samples]

    @property
    def duration_s(self) -> float:
        """Total assembled duration in seconds."""
        return len(self.pcm) / self.sample_rate


@dataclass(frozen=True)
class MasterStats:
    """Measurements from the second loudnorm pass."""

    duration_s: float
    loudness_lufs: float
    true_peak_dbtp: float
    size_bytes: int


def _silent_mask(pcm: Int16PCM, sample_rate: int) -> npt.NDArray[np.bool_]:
    """Per-sample silence mask from RMS over 20 ms windows at -50 dBFS."""
    window = max(1, round(_WINDOW_S * sample_rate))
    n_windows = (len(pcm) + window - 1) // window
    padded = np.zeros(n_windows * window, dtype=np.float32)
    padded[: len(pcm)] = pcm.astype(np.float32) / _INT16_SCALE
    frames = padded.reshape(n_windows, window)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    threshold = 10.0 ** (_THRESHOLD_DBFS / 20.0)
    silent_frames = rms < threshold
    return np.repeat(silent_frames, window)[: len(pcm)]


def trim_silence(
    pcm: Int16PCM,
    sample_rate: int,
    keep_ms: float = _TRIM_KEEP_MS,
) -> Int16PCM:
    """Trim leading/trailing silence, keeping a short natural pad."""
    if len(pcm) == 0:
        return pcm
    silent = _silent_mask(pcm, sample_rate)
    if bool(np.all(silent)):
        return np.zeros(0, dtype=np.int16)
    keep = round(keep_ms / 1000.0 * sample_rate)
    first = int(np.argmax(~silent))
    last = int(len(pcm) - 1 - np.argmax(~silent[::-1]))
    start = max(0, first - keep)
    end = min(len(pcm), last + 1 + keep)
    return np.ascontiguousarray(pcm[start:end])


def compress_silence(
    pcm: Int16PCM,
    sample_rate: int,
    max_keep_s: float = _COMPRESS_ABOVE_S,
    target_s: float = _COMPRESS_TO_S,
    report_above_s: float = _REPORT_ABOVE_S,
) -> tuple[Int16PCM, list[float]]:
    """Squeeze long internal pauses; report originals above the gate limit.

    Returns the compressed PCM plus the original durations (seconds) of any
    internal silence longer than ``report_above_s``, so quality gates can
    request re-synthesis of drowsy chunks.
    """
    if len(pcm) == 0:
        return pcm, []
    silent = _silent_mask(pcm, sample_rate)
    max_keep = round(max_keep_s * sample_rate)
    target = round(target_s * sample_rate)
    report = round(report_above_s * sample_rate)
    parts: list[Int16PCM] = []
    flagged: list[float] = []
    i = 0
    n = len(pcm)
    while i < n:
        if not bool(silent[i]):
            j = i
            while j < n and not bool(silent[j]):
                j += 1
            parts.append(pcm[i:j])
            i = j
            continue
        j = i
        while j < n and bool(silent[j]):
            j += 1
        # Leading/trailing silence is the trimmer's job; keep it intact here.
        if i == 0 or j == n:
            parts.append(pcm[i:j])
        else:
            run = j - i
            if run > report:
                flagged.append(run / sample_rate)
            if run > max_keep:
                parts.append(pcm[i : i + target])
            else:
                parts.append(pcm[i:j])
        i = j
    if not parts:
        return np.zeros(0, dtype=np.int16), flagged
    return np.ascontiguousarray(np.concatenate(parts)), flagged


def resample(pcm: Int16PCM, src_rate: int, dst_rate: int) -> Int16PCM:
    """Linear-interpolation resampler for off-rate engine output."""
    if src_rate == dst_rate:
        return np.ascontiguousarray(pcm)
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("Sample rates must be positive.")
    if len(pcm) == 0:
        return pcm
    duration = len(pcm) / src_rate
    out_len = max(1, round(duration * dst_rate))
    src_pos = np.linspace(0, len(pcm) - 1, out_len)
    mono = pcm.astype(np.float32)
    out = np.interp(src_pos, np.arange(len(pcm)), mono)
    return np.ascontiguousarray(np.clip(out, -32768, 32767).astype(np.int16))


def _gap(seconds: float, sample_rate: int) -> Int16PCM:
    """Exact-length digital silence for inter-chunk and inter-chapter gaps."""
    return np.zeros(max(0, round(seconds * sample_rate)), dtype=np.int16)


def assemble(
    chapters: list[ChapterAudio],
    sample_rate: int = 24000,
    chunk_gap_s: float = 0.5,
    chapter_gap_s: float = 1.2,
) -> tuple[AssembledEpisode, list[LongSilence]]:
    """Trim silence, compress long pauses, and join chunks with exact gaps.

    Segments inside a chapter are joined with ``chunk_gap_s``; chapters are
    joined with ``chapter_gap_s``. Chapter start offsets are computed from
    sample counts after trimming, so they stay exact. Returns the assembled
    episode plus any long silences found (with their chapter titles).
    """
    if not chapters:
        raise ValueError("Cannot assemble an episode with no chapters.")
    rendered: list[Int16PCM] = []
    starts: list[int] = []
    titles: list[str] = []
    long: list[LongSilence] = []
    cursor = 0
    for ci, chapter in enumerate(chapters):
        if not chapter.title:
            raise ValueError(f"Chapter {ci} has an empty title.")
        if chapter.sample_rates and len(chapter.sample_rates) != len(chapter.segments):
            raise ValueError(
                f"Chapter {chapter.title!r}: sample_rates must parallel segments."
            )
        starts.append(cursor)
        titles.append(chapter.title)
        for si, segment in enumerate(chapter.segments):
            if segment.dtype != np.int16:
                raise ValueError(
                    f"Chapter {chapter.title!r} segment {si} must be int16 PCM."
                )
            src_rate = chapter.sample_rates[si] if chapter.sample_rates else sample_rate
            prepared = trim_silence(
                resample(segment, src_rate, sample_rate), sample_rate
            )
            prepared, flagged = compress_silence(prepared, sample_rate)
            for seconds in flagged:
                long.append(LongSilence(chapter=chapter.title, seconds=seconds))
            if si > 0:
                gap = _gap(chunk_gap_s, sample_rate)
                rendered.append(gap)
                cursor += len(gap)
            rendered.append(prepared)
            cursor += len(prepared)
        if ci < len(chapters) - 1:
            gap = _gap(chapter_gap_s, sample_rate)
            rendered.append(gap)
            cursor += len(gap)
    pcm = (
        np.ascontiguousarray(np.concatenate(rendered))
        if rendered
        else np.zeros(0, dtype=np.int16)
    )
    return (
        AssembledEpisode(
            pcm=pcm,
            sample_rate=sample_rate,
            chapter_titles=titles,
            chapter_start_samples=starts,
        ),
        long,
    )


def write_wav(pcm: Int16PCM, sample_rate: int, path: str | Path) -> None:
    """Write mono s16le PCM to a WAV file for the mastering passes."""
    if pcm.dtype != np.int16:
        raise ValueError("Mastering input must be int16 mono PCM.")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def _parse_loudnorm_json(stderr: str) -> dict[str, float]:
    """Extract the loudnorm JSON block from ffmpeg stderr.

    Pass 1 reports the input measurement (``input_*``); pass 2 echoes that
    measurement and reports the normalized result (``output_*``).
    """
    matches = _LOUDNORM_JSON_RE.findall(stderr)
    if not matches:
        raise SaseListenError(
            "ffmpeg loudnorm pass produced no measurements.",
            ExitCode.UNEXPECTED,
            hint="Check that the ffmpeg binary supports the loudnorm filter.",
        )
    try:
        raw: dict[str, str] = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        raise SaseListenError(
            f"Could not parse loudnorm measurements: {exc}",
            ExitCode.UNEXPECTED,
        ) from exc
    try:
        return {
            "input_i": float(raw["input_i"]),
            "input_tp": float(raw["input_tp"]),
            "input_lra": float(raw["input_lra"]),
            "input_thresh": float(raw["input_thresh"]),
            "output_i": float(raw["output_i"]),
            "output_tp": float(raw["output_tp"]),
            "target_offset": float(raw["target_offset"]),
        }
    except KeyError as exc:
        raise SaseListenError(
            f"loudnorm measurements are missing {exc}.",
            ExitCode.UNEXPECTED,
        ) from exc


def _run_ffmpeg(args: list[str], what: str) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg, raising a friendly error with the stderr tail on failure."""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=_MASTER_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SaseListenError(
            f"Could not run ffmpeg ({what}): {exc}", ExitCode.UNEXPECTED
        ) from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise SaseListenError(
            f"ffmpeg {what} failed (exit {proc.returncode}):\n{tail}",
            ExitCode.UNEXPECTED,
            hint="Re-run with --force after checking the input audio.",
        )
    return proc


def master_to_mp3(
    pcm: Int16PCM,
    sample_rate: int,
    out_path: str | Path,
    ffmpeg: FfmpegInfo,
    target_lufs: float = -16.0,
    true_peak_db: float = -1.5,
    bitrate_kbps: int = 64,
) -> MasterStats:
    """Two-pass loudnorm to a 64 kb/s mono MP3 with atomic replace.

    Pass 1 measures integrated loudness; pass 2 applies the measured values
    with ``linear=true`` and encodes ``-ar <rate> -ac 1 -c:a libmp3lame`` at
    constant bitrate with a Xing header and no source metadata. The MP3 is
    encoded to a temp file beside ``out_path`` so the final replace stays on
    one filesystem. Returns the pass-2 output loudness, true peak, duration,
    and file size.
    """
    ffmpeg.require_mastering()
    if len(pcm) == 0:
        raise ValueError("Cannot master empty PCM.")
    out = Path(out_path)
    base_filter = f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA={_LRA_DB}"
    with tempfile.TemporaryDirectory(prefix="sase-listen-master-") as tmp:
        wav_path = str(Path(tmp) / "episode.wav")
        write_wav(pcm, sample_rate, wav_path)
        pass1 = _run_ffmpeg(
            [
                ffmpeg.exe,
                "-y",
                "-hide_banner",
                "-i",
                wav_path,
                "-map",
                "0:a",
                "-af",
                f"{base_filter}:print_format=json",
                "-f",
                "null",
                "-",
            ],
            "loudness measurement pass",
        )
        measured = _parse_loudnorm_json(pass1.stderr)
        measured_filter = (
            f"{base_filter}"
            f":measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}"
            ":linear=true:print_format=json"
        )
        # Encode beside the target so the final replace never crosses
        # filesystems: the system temp dir is often tmpfs while the library
        # is not, and rename(2) fails with EXDEV across devices.
        fd, staging = tempfile.mkstemp(dir=out.parent, prefix=".tmp-", suffix=".mp3")
        os.close(fd)
        try:
            pass2 = _run_ffmpeg(
                [
                    ffmpeg.exe,
                    "-y",
                    "-hide_banner",
                    "-i",
                    wav_path,
                    "-af",
                    measured_filter,
                    "-ar",
                    str(sample_rate),
                    "-ac",
                    "1",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    f"{bitrate_kbps}k",
                    "-write_xing",
                    "1",
                    "-map_metadata",
                    "-1",
                    staging,
                ],
                "loudness normalization pass",
            )
            applied = _parse_loudnorm_json(pass2.stderr)
            os.replace(staging, out)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(staging)
            raise
    return MasterStats(
        duration_s=len(pcm) / sample_rate,
        loudness_lufs=applied["output_i"],
        true_peak_dbtp=applied["output_tp"],
        size_bytes=out.stat().st_size,
    )
