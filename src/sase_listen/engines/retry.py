"""Bounded retry with exponential backoff and full jitter.

Owner: engines phase.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable

from sase_listen.engines.base import (
    CredentialsError,
    PermanentEngineError,
    TransientEngineError,
)


def synthesize_with_retry[T](
    operation: Callable[[], T],
    *,
    max_retries: int,
    base_delay_s: float = 1.0,
    max_delay_s: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[float, float], float] = random.uniform,
) -> T:
    """Run `operation`, retrying transient failures up to `max_retries` times.

    Permanent and credentials errors are never retried. A server-provided
    `retry_after` is honored as a floor on the backoff delay. `sleep` and
    `rand` are injectable so tests never wait on real clocks.
    """
    attempt = 0
    while True:
        try:
            return operation()
        except (PermanentEngineError, CredentialsError):
            raise
        except TransientEngineError as exc:
            if attempt >= max_retries:
                raise
            backoff = rand(0.0, min(max_delay_s, base_delay_s * (2.0**attempt)))
            delay = backoff
            if exc.retry_after is not None and exc.retry_after > delay:
                delay = exc.retry_after
            attempt += 1
            sleep(max(0.0, delay))
