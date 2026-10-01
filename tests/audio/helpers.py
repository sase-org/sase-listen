"""Shared synthetic PCM fixtures. Owner: audio phase."""

from __future__ import annotations

import numpy as np
from numpy import typing as npt

Int16PCM = npt.NDArray[np.int16]

SAMPLE_RATE = 24000


def sine(
    seconds: float,
    freq: float = 440.0,
    rate: int = SAMPLE_RATE,
    amplitude: float = 0.5,
) -> Int16PCM:
    """Full-scale-ish sine tone with no leading or trailing silence."""
    n = round(seconds * rate)
    t = np.arange(n) / rate
    wave = np.sin(2.0 * np.pi * freq * t) * amplitude
    return np.ascontiguousarray((wave * 32767).astype(np.int16))


def silence(seconds: float, rate: int = SAMPLE_RATE) -> Int16PCM:
    """Exact-length digital silence."""
    return np.zeros(round(seconds * rate), dtype=np.int16)
