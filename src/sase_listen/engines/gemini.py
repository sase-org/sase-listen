"""Gemini TTS adapter (google-genai `interactions` API).

Owner: engines phase.

Deviation from the epic plan (confirmed by the live call in this phase's
notes): the plan assumed `models.generate_content` with style in
`system_instruction`, but the current speech-generation guide documents the
`client.interactions.create` API for Gemini 3.8 TTS models, and the pinned
model rejects `system_instruction` with 400 "Developer instruction is not
enabled for this model". Style therefore travels as a structured
`speech_metadata.style` annotation on the turn -- still never inlined into
the verbatim transcript text. The default output is a WAV container
(`audio/wav`), decoded here to s16le mono PCM.
"""

from __future__ import annotations

import base64
import io
import wave
from collections.abc import Callable
from typing import Any

from sase_listen.engines.base import (
    CANONICAL_SAMPLE_RATE,
    ContentBlockedError,
    CredentialsError,
    EngineLimits,
    PermanentEngineError,
    SynthesisRequest,
    SynthesisResult,
    TransientEngineError,
    is_invalid_api_key,
)

#: Fallback voice when a profile names none (e.g. `gemini-lite`).
DEFAULT_VOICE = "Charon"


def build_interaction_body(request: SynthesisRequest) -> dict[str, Any]:
    """Build the `interactions.create` kwargs for `request` (snapshot-tested)."""
    content: dict[str, Any] = {"type": "text", "text": request.text}
    if request.style:
        content["annotations"] = [{"type": "speech_metadata", "style": request.style}]
    return {
        "model": request.model,
        "input": [{"type": "user_input", "content": [content]}],
        "response_format": {"type": "audio"},
        "generation_config": {
            "speech_config": [{"voice": request.voice or DEFAULT_VOICE}]
        },
    }


def _decode_output(data: str | bytes) -> tuple[bytes, int]:
    raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
    if raw[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(raw), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                    raise PermanentEngineError(
                        "Gemini returned audio in an unexpected WAV layout."
                    )
                return wav.readframes(wav.getnframes()), wav.getframerate()
        except (wave.Error, EOFError) as exc:
            raise TransientEngineError(
                f"Gemini returned an undecodable WAV container: {exc}."
            ) from exc
    if raw:
        return raw, CANONICAL_SAMPLE_RATE
    raise TransientEngineError("Gemini returned no audio for the request.")


def _extract_pcm(interaction: Any) -> tuple[bytes, int]:
    output = getattr(interaction, "output_audio", None)
    data = getattr(output, "data", None) if output is not None else None
    if not data:
        status = getattr(interaction, "status", "unknown")
        raise TransientEngineError(
            f"Gemini returned no audio for the request (status {status})."
        )
    return _decode_output(data)


def _error_detail(exc: Any) -> str:
    """Return a short, secret-free error body for classification and messages."""
    text = str(exc).strip()
    if not text:
        message = getattr(exc, "message", None)
        text = str(message).strip() if message else ""
    text = " ".join(text.split())
    if len(text) > 300:
        text = text[:297] + "..."
    return text


def _status_code(exc: Any) -> int | None:
    """Read `code` (errors.APIError) or `status_code` (interactions compat)."""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    return None


def _is_genai_compat_error(exc: Any) -> bool:
    """Return True for google-genai interactions compat errors.

    Duck-typed on an integer-or-None `status_code` from a `google.genai`
    exception class, without importing the private `_gaos` module.
    """
    if not hasattr(exc, "status_code"):
        return False
    module = getattr(exc.__class__, "__module__", "")
    return isinstance(module, str) and module.startswith("google.genai")


def _is_content_blocked(exc: Any, code: int | None, detail: str) -> bool:
    """Return True when a 400 means the policy filter blocked the text."""
    if code != 400:
        return False
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        try:
            error = body.get("error")
            if isinstance(error, dict) and error.get("code") == "content_blocked":
                return True
        except AttributeError:
            pass
    return "content_blocked" in detail.lower()


def _map_api_error(exc: Any) -> Exception:
    code = _status_code(exc)
    detail = _error_detail(exc)
    if code is None:
        return TransientEngineError(f"Gemini connection failed: {detail or exc}.")
    if is_invalid_api_key(code, detail):
        return CredentialsError(f"Gemini rejected the API key (HTTP {code}).")
    if _is_content_blocked(exc, code, detail):
        return ContentBlockedError(
            "Gemini's policy filter blocked the text (HTTP 400 content_blocked)"
        )
    if code == 429:
        return TransientEngineError(
            f"Gemini rate-limited the request (HTTP {code}).",
            retry_after=_retry_after(exc),
        )
    if code >= 500:
        return TransientEngineError(f"Gemini server error (HTTP {code}).")
    return PermanentEngineError(
        f"Gemini request failed (HTTP {code}): {detail or exc}."
    )


def _retry_after(exc: Any) -> float | None:
    try:
        response = getattr(exc, "response", None)
        headers = (getattr(response, "headers", None) or {}) or (
            getattr(exc, "headers", None) or {}
        )
        value = headers.get("retry-after") or headers.get("Retry-After")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class GeminiEngine:
    """TTS via `client.interactions.create` with AUDIO response format."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: int = 180,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise CredentialsError(
                "Gemini API key is missing. Set SASE_LISTEN_GEMINI_API_KEY "
                "(or GEMINI_API_KEY / GOOGLE_API_KEY) or configure "
                "engines.gemini.api_key_command."
            )
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(self._api_key)
        from google.genai import Client

        return Client(api_key=self._api_key)

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Render text to s16le mono PCM via Gemini TTS."""
        from google.genai import errors

        if not request.text.strip():
            raise PermanentEngineError("Gemini cannot synthesize empty text.")
        # Keep the client alive for the whole call: the SDK closes its HTTP
        # transport when the Client is garbage-collected, so a
        # `self._client().interactions.create(...)` one-liner can fail with
        # "client has been closed" before the request is sent.
        client = self._client()
        try:
            interaction = client.interactions.create(
                **build_interaction_body(request),
                timeout=float(self._timeout_s),
            )
        except errors.APIError as exc:
            raise _map_api_error(exc) from exc
        except (errors.ClientError, errors.ServerError) as exc:
            raise TransientEngineError(f"Gemini transport error: {exc}.") from exc
        except (TimeoutError, ConnectionError) as exc:
            raise TransientEngineError(f"Gemini connection failed: {exc}.") from exc
        except Exception as exc:
            if _is_genai_compat_error(exc):
                raise _map_api_error(exc) from exc
            raise
        pcm, sample_rate = _extract_pcm(interaction)
        return SynthesisResult(
            pcm=pcm, sample_rate=sample_rate, usage={"engine": "gemini"}
        )

    def limits(self, model: str) -> EngineLimits:
        """About 400 target words per chunk under the token caps."""
        return EngineLimits(max_chars=4000, target_words=400, default_concurrency=3)
