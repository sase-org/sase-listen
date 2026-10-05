"""Gemini text writer for article editions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from sase_listen.engines.base import (
    CredentialsError,
    PermanentEngineError,
    TransientEngineError,
    is_invalid_api_key,
)
from sase_listen.engines.retry import synthesize_with_retry
from sase_listen.writer.base import WriterReply


def build_generate_config(system: str, temperature: float) -> Any:
    """Build the Gemini generation config (kept small for request snapshot tests)."""
    from google.genai.types import AutomaticFunctionCallingConfig, GenerateContentConfig

    return GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        max_output_tokens=16384,
        # generate_content warns and can 400 if the SDK infers AFC tools
        # from prompt text; the writer never calls tools.
        automatic_function_calling=AutomaticFunctionCallingConfig(disable=True),
    )


def _retry_after(exc: Any) -> float | None:
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        value = headers.get("retry-after") or headers.get("Retry-After")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _error_detail(exc: Any) -> str:
    """Return a short, secret-free API error body for the user-facing message."""
    text = str(exc).strip()
    if not text:
        message = getattr(exc, "message", None)
        text = str(message).strip() if message else ""
    text = " ".join(text.split())
    if len(text) > 300:
        text = text[:297] + "..."
    return text


def _is_invalid_api_key(code: Any, detail: str) -> bool:
    """Shared credentials check (see engines.base.is_invalid_api_key)."""
    return is_invalid_api_key(code, detail)


def _map_api_error(exc: Any) -> Exception:
    code = getattr(exc, "code", None)
    detail = _error_detail(exc)
    suffix = f" {detail}" if detail else ""
    if _is_invalid_api_key(code, detail):
        return CredentialsError(
            f"Gemini rejected the writer API key (HTTP {code}). "
            "Env vars SASE_LISTEN_GEMINI_API_KEY, GEMINI_API_KEY, and "
            "GOOGLE_API_KEY override engines.gemini.api_key_command."
        )
    if code == 429:
        return TransientEngineError(
            f"Gemini writer was rate-limited (HTTP {code}).{suffix}",
            retry_after=_retry_after(exc),
        )
    if isinstance(code, int) and code >= 500:
        return TransientEngineError(
            f"Gemini writer server error (HTTP {code}).{suffix}"
        )
    return PermanentEngineError(f"Gemini writer request failed (HTTP {code}).{suffix}")


class GeminiWriter:
    """Text generation using the Google GenAI `models.generate_content` API."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        temperature: float = 0.3,
        timeout_s: int = 300,
        max_retries: int = 4,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise CredentialsError(
                "Gemini API key is missing. Set SASE_LISTEN_GEMINI_API_KEY "
                "(or GEMINI_API_KEY / GOOGLE_API_KEY) or configure "
                "engines.gemini.api_key_command."
            )
        self._api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(self._api_key, self.timeout_s)
        from google import genai
        from google.genai import types

        return genai.Client(
            api_key=self._api_key,
            http_options=types.HttpOptions(timeout=self.timeout_s * 1000),
        )

    def _generate(self, system: str, user: str) -> WriterReply:
        from google.genai import errors

        client = self._client()
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=user,
                config=build_generate_config(system, self.temperature),
            )
            text = str(getattr(response, "text", "") or "")
            if not text.strip():
                raise TransientEngineError("Gemini writer returned no text.")
            usage = getattr(response, "usage_metadata", None)
            return WriterReply(
                text=text,
                model_version=str(getattr(response, "model_version", "") or ""),
                input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
            )
        except errors.APIError as exc:
            raise _map_api_error(exc) from exc
        except (TimeoutError, ConnectionError, httpx.TimeoutException) as exc:
            raise TransientEngineError("Gemini writer connection timed out.") from exc
        except httpx.TransportError as exc:
            raise TransientEngineError("Gemini writer transport failed.") from exc
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def write(self, system: str, user: str) -> WriterReply:
        """Generate text with bounded retries for transient failures."""
        return synthesize_with_retry(
            lambda: self._generate(system, user),
            max_retries=self.max_retries,
        )
