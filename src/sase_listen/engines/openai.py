"""OpenAI-compatible TTS adapter (OpenAI, Kokoro-FastAPI via base_url).

Owner: engines phase.
"""

from __future__ import annotations

from typing import Any

import httpx

from sase_listen.engines.base import (
    CANONICAL_SAMPLE_RATE,
    CredentialsError,
    EngineLimits,
    PermanentEngineError,
    SynthesisRequest,
    SynthesisResult,
    TransientEngineError,
)

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MAX_CHARS = 3500


def _retry_after_seconds(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, TypeError, ValueError):
        return None


class OpenAIEngine:
    """TTS via POST {base_url}/audio/speech returning 24 kHz s16le PCM."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = OPENAI_DEFAULT_BASE_URL,
        timeout_s: int = 180,
        max_chars: int = DEFAULT_MAX_CHARS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise CredentialsError(
                "OpenAI API key is missing. Set SASE_LISTEN_OPENAI_API_KEY "
                "(or OPENAI_API_KEY) or configure engines.openai.api_key_command."
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/") or OPENAI_DEFAULT_BASE_URL
        self._timeout_s = timeout_s
        self._max_chars = max_chars
        self._transport = transport

    def _body(self, request: SynthesisRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "input": request.text,
            "voice": request.voice,
            "response_format": "pcm",
        }
        if request.style:
            body["instructions"] = request.style
        if request.speed != 1.0:
            body["speed"] = request.speed
        return body

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Render text to s16le mono PCM via /audio/speech."""
        if not request.text.strip():
            raise PermanentEngineError("OpenAI cannot synthesize empty text.")
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout_s,
                transport=self._transport,
            ) as client:
                client.headers["Authorization"] = f"Bearer {self._api_key}"
                response = client.post("/audio/speech", json=self._body(request))
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise TransientEngineError(
                f"OpenAI-compatible endpoint unreachable: {exc}."
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientEngineError(
                f"OpenAI-compatible transport error: {exc}."
            ) from exc
        status = response.status_code
        if status == 200:
            if not response.content:
                raise TransientEngineError(
                    "OpenAI-compatible endpoint returned empty audio."
                )
            return SynthesisResult(
                pcm=response.content,
                sample_rate=CANONICAL_SAMPLE_RATE,
                usage={"engine": "openai"},
            )
        if status in (401, 403):
            raise CredentialsError(
                f"OpenAI-compatible endpoint rejected the API key (HTTP {status})."
            )
        if status == 429 or status >= 500:
            raise TransientEngineError(
                f"OpenAI-compatible endpoint failed (HTTP {status}).",
                retry_after=_retry_after_seconds(response),
            )
        raise PermanentEngineError(f"OpenAI-compatible request failed (HTTP {status}).")

    def limits(self, model: str, *, max_chars: int | None = None) -> EngineLimits:
        """3,500 chars by default; pass `max_chars` for Kokoro-FastAPI voices."""
        return EngineLimits(
            max_chars=max_chars or self._max_chars,
            target_words=600,
            default_concurrency=3,
        )
