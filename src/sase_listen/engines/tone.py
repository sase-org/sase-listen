"""Offline deterministic tone engine for tests, CI smoke, and demos.

Owner: engines phase.

Renders speech-paced tone bursts at about 150 wpm with a longer pause after
sentence punctuation, so durations, gates, and e2e renders behave like real
narration. Costs nothing.
"""

from __future__ import annotations

import zlib

import numpy as np

from sase_listen.engines.base import (
    CANONICAL_SAMPLE_RATE,
    EngineLimits,
    PermanentEngineError,
    SynthesisRequest,
    SynthesisResult,
)

#: Target pace; each word owns a 0.4 s slot (tone plus gap).
WORDS_PER_MINUTE = 150.0
_WORD_SLOT_S = 60.0 / WORDS_PER_MINUTE
_TONE_S = 0.35
_GAP_S = _WORD_SLOT_S - _TONE_S
_SENTENCE_PAUSE_S = 0.30
_SENTENCE_END = frozenset(".?!;:")
_FADE_S = 0.01
_AMPLITUDE = 0.5


def _frequency(word: str) -> float:
    """Deterministic per-word pitch between 330 Hz and 715 Hz."""
    return 330.0 + (zlib.crc32(word.encode("utf-8")) % 8) * 55.0


def _tone(word: str, sample_rate: int) -> np.ndarray:
    n = max(1, int(_TONE_S * sample_rate))
    time = np.arange(n, dtype=np.float64) / sample_rate
    wave = _AMPLITUDE * np.sin(2.0 * np.pi * _frequency(word) * time)
    fade = max(1, int(_FADE_S * sample_rate))
    ramp = np.linspace(0.0, 1.0, fade)
    wave[:fade] *= ramp
    wave[-fade:] *= ramp[::-1]
    return wave


def _silence(seconds: float, sample_rate: int) -> np.ndarray:
    return np.zeros(max(1, int(seconds * sample_rate)), dtype=np.float64)


def render_pcm(text: str, sample_rate: int = CANONICAL_SAMPLE_RATE) -> bytes:
    """Render `text` to s16le mono PCM bytes (pure function, snapshot-tested)."""
    parts: list[np.ndarray] = []
    for word in text.split():
        parts.append(_tone(word, sample_rate))
        pause = _GAP_S + (_SENTENCE_PAUSE_S if word[-1:] in _SENTENCE_END else 0.0)
        parts.append(_silence(pause, sample_rate))
    if not parts:
        parts.append(_silence(_WORD_SLOT_S, sample_rate))
    pcm = np.concatenate(parts)
    clipped = np.clip(pcm, -1.0, 1.0)
    return bytes((clipped * 32767).astype("<i2").tobytes())


class ToneEngine:
    """Deterministic offline engine. Ignores model/voice/style/speed."""

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Render text to s16le mono PCM without any network call."""
        if not request.text.strip():
            raise PermanentEngineError("Tone engine cannot synthesize empty text.")
        return SynthesisResult(
            pcm=render_pcm(request.text),
            sample_rate=CANONICAL_SAMPLE_RATE,
            usage={"engine": "tone"},
        )

    def limits(self, model: str) -> EngineLimits:
        """Generous local limits; chunking still applies for e2e realism."""
        return EngineLimits(max_chars=20000, target_words=400, default_concurrency=4)
