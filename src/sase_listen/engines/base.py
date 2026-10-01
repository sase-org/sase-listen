"""Engine protocol, requests, results, limits, and error taxonomy.

Owner: engines phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

#: Canonical sample rate for engine PCM. Mastering resamples anything else.
CANONICAL_SAMPLE_RATE = 24000


@dataclass(frozen=True)
class SynthesisRequest:
    """One chunk of spoken text plus the voice to render it with."""

    text: str
    model: str
    voice: str
    style: str = ""
    speed: float = 1.0


@dataclass(frozen=True)
class SynthesisResult:
    """Decoded synthesis output: s16le mono PCM plus provider usage."""

    pcm: bytes
    sample_rate: int
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineLimits:
    """Per-model chunking guidance for the render planner."""

    max_chars: int
    target_words: int
    default_concurrency: int


class TransientEngineError(Exception):
    """Retryable failure: 429, 5xx, timeouts, or empty audio.

    `retry_after` carries a server-provided Retry-After delay in seconds,
    when one was present.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentEngineError(Exception):
    """Non-retryable failure: bad input, unknown model, undecodable audio."""


class CredentialsError(Exception):
    """Missing or rejected API credentials. Never retried."""


class Engine(Protocol):
    """The interface every TTS adapter implements."""

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Render `request.text` to s16le mono PCM."""
        ...

    def limits(self, model: str) -> EngineLimits:
        """Return chunking guidance for `model`."""
        ...
