"""End-to-end mastering tests on the real ffmpeg. Owner: audio phase."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from sase_listen.audio.ffmpeg import resolve_ffmpeg
from sase_listen.audio.mastering import (
    AssembledEpisode,
    ChapterAudio,
    assemble,
    master_to_mp3,
)

from .helpers import SAMPLE_RATE, sine

CHUNK_GAP = 0.5
CHAPTER_GAP = 1.2


def _build_episode() -> AssembledEpisode:
    chapters = [
        ChapterAudio(title="First", segments=[sine(1.0), sine(0.5)]),
        ChapterAudio(title="Second", segments=[sine(2.0)]),
    ]
    episode, long = assemble(
        chapters,
        SAMPLE_RATE,
        chunk_gap_s=CHUNK_GAP,
        chapter_gap_s=CHAPTER_GAP,
    )
    assert long == []
    return episode


def _measure_loudness(ffmpeg_exe: str, mp3: Path) -> float:
    """Independent loudness measurement: fresh loudnorm pass over the MP3."""
    proc = subprocess.run(
        [
            ffmpeg_exe,
            "-hide_banner",
            "-i",
            str(mp3),
            "-map",
            "0:a",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    match = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr)
    assert match, proc.stderr[-2000:]
    return float(json.loads(match[-1])["input_i"])


def test_two_pass_master_lands_at_minus_16_lufs(tmp_path: Path) -> None:
    episode = _build_episode()
    out = tmp_path / "episode.mp3"
    stats = master_to_mp3(episode.pcm, SAMPLE_RATE, out, resolve_ffmpeg())
    assert out.stat().st_size > 10_000
    # The pass-2 report and an independent measurement must both agree.
    assert stats.loudness_lufs == pytest.approx(-16.0, abs=1.0)
    measured = _measure_loudness(resolve_ffmpeg().exe, out)
    assert measured == pytest.approx(-16.0, abs=1.0)


def test_mastered_duration_matches_segments_plus_gaps(tmp_path: Path) -> None:
    from mutagen.mp3 import MP3

    episode = _build_episode()
    out = tmp_path / "episode.mp3"
    stats = master_to_mp3(episode.pcm, SAMPLE_RATE, out, resolve_ffmpeg())
    expected_s = 1.0 + 0.5 + CHUNK_GAP + CHAPTER_GAP + 2.0
    assert stats.duration_s == pytest.approx(expected_s)
    assert MP3(str(out)).info.length == pytest.approx(expected_s, abs=0.15)


def test_chapter_offsets_are_exact(tmp_path: Path) -> None:
    episode = _build_episode()
    assert episode.chapter_start_s == pytest.approx([0.0, 3.2])
    assert episode.chapter_start_samples == [0, round(3.2 * SAMPLE_RATE)]
    out = tmp_path / "episode.mp3"
    master_to_mp3(episode.pcm, SAMPLE_RATE, out, resolve_ffmpeg())
    assert out.exists()


def test_master_refuses_empty_pcm(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        master_to_mp3(
            np.zeros(0, dtype=np.int16),
            SAMPLE_RATE,
            tmp_path / "empty.mp3",
            resolve_ffmpeg(),
        )
