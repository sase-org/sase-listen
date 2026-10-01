"""PCM trim/compress/assemble unit tests. Owner: audio phase."""

from __future__ import annotations

import numpy as np
import pytest

from sase_listen.audio.mastering import (
    ChapterAudio,
    assemble,
    compress_silence,
    resample,
    trim_silence,
)

from .helpers import SAMPLE_RATE, silence, sine


def test_trim_keeps_short_pad_around_speech() -> None:
    pcm = np.concatenate([silence(0.5), sine(1.0), silence(0.5)])
    trimmed = trim_silence(pcm, SAMPLE_RATE)
    assert len(trimmed) == round((1.0 + 0.08 + 0.08) * SAMPLE_RATE)


def test_trim_all_silence_gives_empty() -> None:
    assert len(trim_silence(silence(1.0), SAMPLE_RATE)) == 0
    assert len(trim_silence(np.zeros(0, dtype=np.int16), SAMPLE_RATE)) == 0


def test_trim_leaves_clean_tone_untouched() -> None:
    tone = sine(1.0)
    assert np.array_equal(trim_silence(tone, SAMPLE_RATE), tone)


def test_compress_squeezes_long_pause_without_flag() -> None:
    pcm = np.concatenate([sine(0.5), silence(2.0), sine(0.5)])
    out, flagged = compress_silence(pcm, SAMPLE_RATE)
    assert flagged == []
    assert len(out) == round((0.5 + 0.7 + 0.5) * SAMPLE_RATE)


def test_compress_reports_drowsy_pause_for_gates() -> None:
    pcm = np.concatenate([sine(0.5), silence(5.0), sine(0.5)])
    out, flagged = compress_silence(pcm, SAMPLE_RATE)
    assert flagged == pytest.approx([5.0], abs=0.05)
    assert len(out) == round((0.5 + 0.7 + 0.5) * SAMPLE_RATE)


def test_compress_leaves_short_pauses_alone() -> None:
    pcm = np.concatenate([sine(0.5), silence(1.0), sine(0.5)])
    out, flagged = compress_silence(pcm, SAMPLE_RATE)
    assert np.array_equal(out, pcm)
    assert flagged == []


def test_resample_upscales_length() -> None:
    tone_16k = sine(1.0, rate=16000)
    up = resample(tone_16k, 16000, SAMPLE_RATE)
    assert len(up) == SAMPLE_RATE
    assert up.dtype == np.int16


def test_assemble_offsets_are_exact_and_duration_adds_gaps() -> None:
    chapters = [
        ChapterAudio(title="First", segments=[sine(1.0), sine(0.5)]),
        ChapterAudio(title="Second", segments=[sine(2.0)]),
    ]
    episode, long = assemble(chapters, SAMPLE_RATE, chunk_gap_s=0.5, chapter_gap_s=1.2)
    assert long == []
    assert episode.chapter_start_samples == [0, round(3.2 * SAMPLE_RATE)]
    assert episode.chapter_start_s == pytest.approx([0.0, 3.2])
    assert episode.duration_s == pytest.approx(5.2)
    assert len(episode.pcm) == round(5.2 * SAMPLE_RATE)


def test_assemble_resamples_off_rate_segments() -> None:
    chapters = [
        ChapterAudio(
            title="Mixed",
            segments=[sine(1.0, rate=16000)],
            sample_rates=[16000],
        )
    ]
    episode, _ = assemble(chapters, SAMPLE_RATE)
    assert episode.duration_s == pytest.approx(1.0, abs=0.01)


def test_assemble_rejects_empty_title_and_mismatched_rates() -> None:
    with pytest.raises(ValueError):
        assemble([ChapterAudio(title="", segments=[sine(0.5)])], SAMPLE_RATE)
    with pytest.raises(ValueError):
        assemble([], SAMPLE_RATE)
    with pytest.raises(ValueError):
        assemble(
            [ChapterAudio(title="Bad", segments=[sine(0.5)], sample_rates=[16000, 1])],
            SAMPLE_RATE,
        )
