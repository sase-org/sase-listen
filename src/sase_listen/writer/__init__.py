"""Article script writers."""

from __future__ import annotations

from collections.abc import Callable

from sase_listen.config import SaseListenConfig
from sase_listen.engines import CredentialsError, resolve_api_key
from sase_listen.engines.retry import RetryWait
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.writer.base import Writer, WriterReply
from sase_listen.writer.gemini import GeminiWriter


def create_writer(
    config: SaseListenConfig,
    *,
    on_retry: Callable[[RetryWait], None] | None = None,
) -> Writer:
    """Build the configured article writer."""
    if config.writer.engine != "gemini":
        raise SaseListenError(
            f"Unknown writer engine '{config.writer.engine}'.",
            ExitCode.CONFIG,
            hint="The only supported writer engine is gemini.",
        )
    engine = config.engines.gemini
    try:
        api_key = resolve_api_key(
            engine="gemini",
            env_names=engine.api_key_env,
            api_key_command=engine.api_key_command,
        )
    except CredentialsError as exc:
        raise SaseListenError(str(exc), ExitCode.CONFIG) from exc
    return GeminiWriter(
        api_key,
        model=config.writer.model,
        temperature=config.writer.temperature,
        timeout_s=config.writer.timeout_s,
        max_retries=engine.max_retries,
        on_retry=on_retry,
    )


__all__ = ["GeminiWriter", "Writer", "WriterReply", "create_writer"]
