"""Render progress event protocol (leaf module, stdlib-only).

Owner: listen_live_progress tale.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from sase_listen.pipeline import RenderPlan, RenderResult

from sase_listen.engines.retry import RetryWait

__all__ = ["RenderEvents", "RetryWait", "Stage"]


class Stage(StrEnum):
    """Named render stages in display order."""

    SOURCE = "source"
    WRITE = "write"
    PLAN = "plan"
    SYNTHESIZE = "synthesize"
    GATES = "gates"
    MASTER = "master"
    SAVE = "save"
    PUBLISH = "publish"


class RenderEvents:
    """No-op progress callbacks. Frontends override what they show.

    Order: on_stages, then per stage on_stage -> (on_step | on_retry_wait |
    chunk events)* -> on_stage_done; a failing stage simply raises.
    on_chunk_started, on_chunk_finished and on_retry_wait may be called from
    worker threads; implementations must be thread-safe.
    """

    def on_stages(self, stages: Sequence[str]) -> None:
        """Announce (or refine) the full stage list."""

    def on_stage(self, stage: str) -> None:
        """Mark a stage as started."""

    def on_step(self, stage: str, text: str) -> None:
        """Update the live sub-step text for a stage."""

    def on_stage_done(
        self, stage: str, summary: str = "", *, warning: bool = False
    ) -> None:
        """Mark a stage as done with a short summary."""

    def on_title(self, title: str) -> None:
        """Report the source title once known."""

    def on_plan(self, plan: RenderPlan) -> None:
        """Called once chunk planning (and cache lookup) finishes."""

    def on_chunk_started(self, index: int, total: int) -> None:
        """Called when a chunk request really begins (worker thread)."""

    def on_chunk_finished(self, index: int, total: int, *, cached: bool) -> None:
        """Called when a chunk has audio (cached or synthesized)."""

    def on_chunk_retried(self, index: int, attempt: int, reason: str) -> None:
        """Called before each retry or gate re-synthesis of a chunk."""

    def on_retry_wait(self, stage: str, chunk: int | None, wait: RetryWait) -> None:
        """Called right before a backoff sleep (worker threads allowed)."""

    def on_done(self, result: RenderResult) -> None:
        """Called with the finished result after the commit."""
