"""TTS engine protocol and adapters. Owner: engines phase."""

from __future__ import annotations

from typing import Any

import httpx

from sase_listen.engines.base import (
    CANONICAL_SAMPLE_RATE,
    CredentialsError,
    Engine,
    EngineLimits,
    PermanentEngineError,
    SynthesisRequest,
    SynthesisResult,
    TransientEngineError,
)
from sase_listen.engines.gemini import GeminiEngine
from sase_listen.engines.narrators import ResolvedNarrator, resolve_narrator
from sase_listen.engines.openai import OpenAIEngine
from sase_listen.engines.retry import synthesize_with_retry
from sase_listen.engines.secrets import resolve_api_key
from sase_listen.engines.tone import ToneEngine

__all__ = [
    "CANONICAL_SAMPLE_RATE",
    "CredentialsError",
    "Engine",
    "EngineLimits",
    "GeminiEngine",
    "OpenAIEngine",
    "PermanentEngineError",
    "ResolvedNarrator",
    "SynthesisRequest",
    "SynthesisResult",
    "ToneEngine",
    "TransientEngineError",
    "create_engine",
    "resolve_api_key",
    "resolve_narrator",
    "synthesize_with_retry",
]


def create_engine(
    name: str,
    *,
    api_key: str = "",
    base_url: str = "",
    timeout_s: int = 180,
    transport: httpx.BaseTransport | None = None,
    client_factory: Any = None,
) -> Engine:
    """Build the named engine adapter (`gemini`, `openai`, or `tone`)."""
    if name == "gemini":
        return GeminiEngine(api_key, timeout_s=timeout_s, client_factory=client_factory)
    if name == "openai":
        kwargs: dict[str, Any] = {"timeout_s": timeout_s, "transport": transport}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAIEngine(api_key, **kwargs)
    if name == "tone":
        return ToneEngine()
    raise ValueError(f"Unknown engine '{name}'. Known engines: gemini, openai, tone.")
