"""Live and plain render progress displays. Owner: cli phase (tale)."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from types import FrameType, TracebackType
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from sase_listen.engines.retry import RetryWait
from sase_listen.events import RenderEvents, Stage
from sase_listen.pipeline import (
    RenderPlan,
    RenderResult,
    looks_like_ref,
    looks_like_url,
)
from sase_listen.ui import ACCENT, SPINNER, format_duration

__all__ = [
    "LiveProgress",
    "PlainProgress",
    "ProgressState",
    "Snapshot",
    "activity",
    "build_progress",
    "build_view",
    "interrupt_guard",
    "resolve_mode",
]

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_WARNING = "warning"
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"

STAGE_LABELS: dict[str, str] = {
    Stage.SOURCE.value: "Fetch article",
    Stage.WRITE.value: "Write script",
    Stage.PLAN.value: "Plan episode",
    Stage.SYNTHESIZE.value: "Synthesize",
    Stage.GATES.value: "Quality gates",
    Stage.MASTER.value: "Master audio",
    Stage.SAVE.value: "Save episode",
    Stage.PUBLISH.value: "Publish",
}

GLYPH_UNICODE: dict[str, str] = {
    "audio": "\u266a",
    "arrow": "\u2192",
    "pending": "\u00b7",
    "retry": "\u21bb",
    "ok": "\u2713",
    "warn": "\u26a0",
    "fail": "\u2717",
    "stop": "\u25a0",
    "bar": "\u2501",
}

GLYPH_ASCII: dict[str, str] = {
    "audio": "*",
    "arrow": "->",
    "pending": "-",
    "retry": "~",
    "ok": "ok",
    "warn": "!!",
    "fail": "x",
    "stop": "##",
    "bar": "=",
}


def source_stage_label(source: str) -> str:
    """Return the SOURCE row label for a render source."""
    if looks_like_url(source):
        return "Fetch article"
    if looks_like_ref(source):
        return "Read ref"
    return "Read file"


def source_display(source: str) -> str:
    """Return the short source text (URL without scheme, or file name)."""
    if looks_like_url(source):
        return source.split("://", 1)[-1]
    name = source.split("/")[-1]
    return name or source


def source_host(source: str) -> str:
    """Return the subtitle source part (site/host, or file name)."""
    if looks_like_url(source):
        return source.split("://", 1)[-1].split("/", 1)[0]
    name = source.split("/")[-1]
    return name or source


def _label_for(stage: str, source: str) -> str:
    if stage == Stage.SOURCE.value:
        return source_stage_label(source)
    return STAGE_LABELS.get(stage, stage)


@dataclass
class _Row:
    id: str
    label: str
    status: str = STATUS_PENDING
    started: float | None = None
    ended: float | None = None
    step: str = ""
    summary: str = ""
    error: str = ""


@dataclass(frozen=True)
class Snapshot:
    """Thread-safe copy of display state for pure view rendering."""

    title: str
    subtitle: str
    episode_id: str
    elapsed_s: float
    rows: tuple[_Row, ...]
    total: int
    done: int
    cached: int
    in_flight: tuple[tuple[int, float], ...]
    waits: tuple[tuple[int, float, RetryWait], ...]
    stage_waits: tuple[tuple[str, float, RetryWait], ...]
    finished_durations: tuple[float, ...]
    max_parallel: int
    chapters: dict[int, str]
    active_stage: str
    final: bool = False


class ProgressState(RenderEvents):
    """The progress model: stage rows, header, and a synthesis chunk board.

    Every mutation goes under one lock; `clock` is injectable for tests.
    """

    def __init__(
        self, source: str = "", *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._source = source
        self._title = ""
        self._edition = ""
        self._voice = ""
        self._voice_name = ""
        self._model = ""
        self._rows: list[_Row] = []
        self._total = 0
        self._done = 0
        self._cached = 0
        self._in_flight: dict[int, float] = {}
        self._waits: dict[int, tuple[float, RetryWait]] = {}
        self._stage_waits: dict[str, tuple[float, RetryWait]] = {}
        self._chapters: dict[int, str] = {}
        self._finished_durations: list[float] = []
        self._max_parallel = 0
        self._active_stage = ""
        self._start: float | None = None
        self._episode_id = ""

    def _touch(self, now: float) -> None:
        if self._start is None:
            self._start = now

    def _find(self, stage: str) -> _Row | None:
        for row in self._rows:
            if row.id == stage:
                return row
        return None

    def _ensure(self, stage: str, now: float) -> _Row:
        row = self._find(stage)
        if row is None:
            row = _Row(id=stage, label=_label_for(stage, self._source))
            self._rows.append(row)
        return row

    def _subtitle(self) -> str:
        parts: list[str] = []
        if self._source:
            parts.append(source_host(self._source))
        if self._edition:
            parts.append(f"{self._edition} edition")
        voice = self._voice or self._voice_name
        if voice:
            parts.append(voice)
        if self._model:
            parts.append(self._model)
        return " \u00b7 ".join(part for part in parts if part)

    def on_stages(self, stages: Sequence[str]) -> None:
        """Replace only the pending tail; completed and active rows never move."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            wanted = list(stages)
            kept = [row for row in self._rows if row.status != STATUS_PENDING]
            known = {row.id for row in kept}
            self._rows = list(kept)
            for stage in wanted:
                if stage not in known:
                    self._rows.append(
                        _Row(id=stage, label=_label_for(stage, self._source))
                    )
                    known.add(stage)

    def on_stage(self, stage: str) -> None:
        """Mark a stage active, appending unannounced stages."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            row = self._ensure(stage, now)
            row.status = STATUS_ACTIVE
            if row.started is None:
                row.started = now
            self._active_stage = stage

    def on_step(self, stage: str, text: str) -> None:
        """Update the live sub-step text for a stage."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            row = self._ensure(stage, now)
            if row.status == STATUS_PENDING:
                row.status = STATUS_ACTIVE
                row.started = now
                self._active_stage = stage
            row.step = text

    def on_stage_done(
        self, stage: str, summary: str = "", *, warning: bool = False
    ) -> None:
        """Mark a stage done with a short summary."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            row = self._ensure(stage, now)
            row.status = STATUS_WARNING if warning else STATUS_DONE
            row.summary = summary
            row.ended = now
            self._stage_waits.pop(stage, None)
            if self._active_stage == stage:
                self._active_stage = ""

    def on_title(self, title: str) -> None:
        """Record the source title once known."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            self._title = title

    def on_plan(self, plan: RenderPlan) -> None:
        """Record header facts and the chunk chapter map."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            self._total = len(plan.chunks)
            for chunk in plan.chunks:
                if chunk.kind == "intro":
                    self._chapters[chunk.index] = "Intro"
                elif chunk.kind == "outro":
                    self._chapters[chunk.index] = "Outro"
                else:
                    self._chapters[chunk.index] = chunk.chapter
            if not self._title:
                self._title = plan.title
            self._episode_id = plan.episode_id
            self._edition = plan.edition
            self._voice = plan.narrator.voice
            self._voice_name = plan.narrator.name
            self._model = plan.narrator.model

    def on_chunk_started(self, index: int, total: int) -> None:
        """Record a chunk request that really began."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            self._total = total
            self._in_flight[index] = now
            self._max_parallel = max(self._max_parallel, len(self._in_flight))

    def on_chunk_finished(self, index: int, total: int, *, cached: bool) -> None:
        """Record a chunk with audio (cached or synthesized)."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            self._total = total
            started = self._in_flight.pop(index, None)
            self._done += 1
            if cached:
                self._cached += 1
            else:
                elapsed = now - started if started is not None else 0.0
                self._finished_durations.append(elapsed)
            self._waits.pop(index, None)

    def on_chunk_retried(self, index: int, attempt: int, reason: str) -> None:
        """Note a gate re-synthesis on the gates row."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            if self._active_stage == Stage.GATES.value:
                row = self._ensure(Stage.GATES.value, now)
                total = max(self._total, 1)
                row.step = (
                    f"re-synthesizing chunk {index + 1} of {total} \u00b7 {reason}"
                )

    def on_retry_wait(self, stage: str, chunk: int | None, wait: RetryWait) -> None:
        """Record a pending backoff sleep with its deadline."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            deadline = now + max(0.0, wait.delay_s)
            if chunk is None:
                self._stage_waits[stage] = (deadline, wait)
            else:
                self._waits[chunk] = (deadline, wait)

    def on_done(self, result: RenderResult) -> None:
        """Record completion (rows already carry the outcome)."""
        now = self._clock()
        with self._lock:
            self._touch(now)
            if not self._title:
                self._title = result.title

    def mark_active(self, status: str, detail: str) -> None:
        """Mark the active row failed or interrupted."""
        now = self._clock()
        with self._lock:
            for row in self._rows:
                if row.status == STATUS_ACTIVE:
                    row.status = status
                    row.ended = now
                    if status == STATUS_FAILED:
                        row.error = detail.splitlines()[0] if detail else "failed"
                    else:
                        row.summary = detail
            self._active_stage = ""

    def finish_active(self) -> None:
        """Mark leftover active rows done (clean exit with nothing pending)."""
        now = self._clock()
        with self._lock:
            for row in self._rows:
                if row.status == STATUS_ACTIVE:
                    row.status = STATUS_DONE
                    row.ended = now
            self._active_stage = ""

    def snapshot(self, now: float) -> Snapshot:
        """Copy the current state for pure rendering."""
        with self._lock:
            rows = tuple(
                _Row(
                    id=row.id,
                    label=row.label,
                    status=row.status,
                    started=row.started,
                    ended=row.ended,
                    step=row.step,
                    summary=row.summary,
                    error=row.error,
                )
                for row in self._rows
            )
            in_flight = tuple(
                (index, now - started)
                for index, started in sorted(self._in_flight.items())
            )
            waits = tuple(
                (index, deadline - now, wait)
                for index, (deadline, wait) in sorted(self._waits.items())
            )
            stage_waits = tuple(
                (stage, deadline - now, wait)
                for stage, (deadline, wait) in sorted(self._stage_waits.items())
            )
            elapsed = now - self._start if self._start is not None else 0.0
            return Snapshot(
                title=self._title or source_display(self._source),
                subtitle=self._subtitle(),
                episode_id=self._episode_id,
                elapsed_s=max(0.0, elapsed),
                rows=rows,
                total=self._total,
                done=self._done,
                cached=self._cached,
                in_flight=in_flight,
                waits=waits,
                stage_waits=stage_waits,
                finished_durations=tuple(self._finished_durations),
                max_parallel=self._max_parallel,
                chapters=dict(self._chapters),
                active_stage=self._active_stage,
            )


def _eta_text(snapshot: Snapshot) -> str:
    """Return the synthesize ETA detail ("" until the first synthesis)."""
    if not snapshot.finished_durations or snapshot.total <= 0:
        return ""
    finished = [d for d in snapshot.finished_durations if d > 0]
    if not finished:
        return ""
    mean_done = sum(finished) / len(finished)
    parallel = max(1, snapshot.max_parallel)
    queued = max(0, snapshot.total - snapshot.done - len(snapshot.in_flight))
    mean_live = (
        sum(elapsed for _, elapsed in snapshot.in_flight) / len(snapshot.in_flight)
        if snapshot.in_flight
        else 0.0
    )
    eta = queued * mean_done / parallel + max(0.0, mean_done - mean_live)
    if eta <= 0:
        return ""
    if eta < 60:
        return f"~{max(5, 5 * round(eta / 5))}s left"
    minutes = max(1, round(eta / 60))
    return f"~{minutes}m left"


def _retry_text(wait: RetryWait, remaining: float) -> str:
    seconds = max(0, round(remaining))
    return (
        f"retry {wait.attempt} of {wait.max_retries} in {seconds}s \u00b7 {wait.reason}"
    )


def build_view(snapshot: Snapshot, now: float, width: int) -> Group:
    """Build the live checklist view (pure: snapshot, clock, width)."""
    width = max(40, width)
    header = Text()
    header.append("\u266a ", style=f"bold {ACCENT}")
    title = snapshot.title
    elapsed = "" if snapshot.final else format_duration(snapshot.elapsed_s, active=True)
    reserve = len(elapsed) + (3 if elapsed else 0)
    title_room = max(8, width - 2 - reserve)
    if len(title) > title_room:
        title = title[: max(1, title_room - 1)] + "\u2026"
    header.append(title, style="bold")
    if elapsed:
        header.append(" " * max(2, width - 2 - len(title) - len(elapsed)))
        header.append(elapsed, style="dim")
    lines: list[Any] = [header]
    if snapshot.subtitle:
        lines.append(Text("  " + snapshot.subtitle, style="dim"))
    lines.append(Text(""))
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(width=1, no_wrap=True)
    label_width = 8
    for row in snapshot.rows:
        label_width = max(label_width, len("  " + row.label))
    if snapshot.active_stage == Stage.SYNTHESIZE.value and not snapshot.final:
        for index, _ in snapshot.in_flight[:4]:
            label_width = max(label_width, len(f"    chunk {index + 1}"))
        extra_rows = len(snapshot.in_flight) - 4
        if extra_rows > 0:
            label_width = max(label_width, len(f"    +{extra_rows} more in flight"))
        queued = max(0, snapshot.total - snapshot.done - len(snapshot.in_flight))
        if queued > 0:
            label_width = max(label_width, len(f"    {queued} queued"))
    grid.add_column(width=label_width, no_wrap=True, overflow="ellipsis")
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_column(justify="right", min_width=6, no_wrap=True)
    frame = Spinner(SPINNER, style=ACCENT).render(now)
    for row in snapshot.rows:
        label = "  " + row.label
        if row.status == STATUS_PENDING:
            grid.add_row(
                Text("\u00b7", style="dim"),
                Text(label, style="dim"),
                Text(""),
                Text(""),
            )
        elif row.status == STATUS_ACTIVE:
            if row.id == Stage.SYNTHESIZE.value and not snapshot.final:
                detail: Any = _synthesize_detail(snapshot, width)
            else:
                detail = _active_detail(snapshot, row)
            grid.add_row(
                frame if not snapshot.final else Text("\u00b7", style=ACCENT),
                Text(label, style=f"bold {ACCENT}"),
                detail,
                Text(
                    format_duration(now - row.started, active=True)
                    if row.started is not None
                    else "",
                    style="dim",
                ),
            )
        elif row.status == STATUS_DONE:
            grid.add_row(
                Text("\u2713", style="green"),
                Text(label),
                Text(row.summary, style="dim"),
                Text(_row_duration(row), style="dim"),
            )
        elif row.status == STATUS_WARNING:
            grid.add_row(
                Text("\u26a0", style="yellow"),
                Text(label),
                Text(row.summary, style="yellow"),
                Text(_row_duration(row), style="dim"),
            )
        elif row.status == STATUS_FAILED:
            grid.add_row(
                Text("\u2717", style="red"),
                Text(label, style="bold"),
                Text(row.error, style="red"),
                Text(_row_duration(row), style="dim"),
            )
        else:
            grid.add_row(
                Text("\u25a0", style="yellow"),
                Text(label),
                Text(row.summary or "interrupted", style="dim"),
                Text(_row_duration(row), style="dim"),
            )
    if (
        snapshot.active_stage == Stage.SYNTHESIZE.value
        and not snapshot.final
        and snapshot.in_flight
    ):
        for cells in _chunk_rows(snapshot):
            grid.add_row(*cells)
    lines.append(grid)
    return Group(*lines)


def _row_duration(row: _Row) -> str:
    if row.started is None or row.ended is None:
        return ""
    return format_duration(row.ended - row.started)


def _active_detail(snapshot: Snapshot, row: _Row) -> Text:
    for stage, remaining, wait in snapshot.stage_waits:
        if stage == row.id and remaining > 0:
            return Text(_retry_text(wait, remaining))
    return Text(row.step)


def _synthesize_detail(snapshot: Snapshot, width: int) -> Any:
    bar_width = min(28, max(10, width - 52))
    bar = ProgressBar(
        total=max(1, snapshot.total),
        completed=min(snapshot.done, snapshot.total),
        complete_style=ACCENT,
        finished_style="green",
        width=bar_width,
    )
    eta = _eta_text(snapshot)
    extra = f" {snapshot.done}/{snapshot.total}"
    if eta:
        extra += f" \u00b7 {eta}"
    inner = Table.grid(padding=(0, 1))
    inner.add_column(width=bar_width, no_wrap=True)
    inner.add_column(no_wrap=True, overflow="ellipsis")
    inner.add_row(bar, Text(extra))
    return inner


def _chunk_rows(snapshot: Snapshot) -> list[tuple[Any, Any, Any, Any]]:
    """Build the synthesize sub-rows (same grid, so times align)."""
    rows: list[tuple[Any, Any, Any, Any]] = []
    waiting = {
        index: (remaining, wait)
        for index, remaining, wait in snapshot.waits
        if remaining > 0
    }
    for index, elapsed in snapshot.in_flight[:4]:
        label = f"    chunk {index + 1}"
        if index in waiting:
            remaining, wait = waiting[index]
            rows.append(
                (
                    Text("\u21bb", style="yellow"),
                    Text(label),
                    Text(_retry_text(wait, remaining)),
                    Text(format_duration(elapsed, active=True), style="dim"),
                )
            )
        else:
            rows.append(
                (
                    Text("\u266a", style=ACCENT),
                    Text(label),
                    Text(snapshot.chapters.get(index, "")),
                    Text(format_duration(elapsed, active=True), style="dim"),
                )
            )
    extra = len(snapshot.in_flight) - 4
    if extra > 0:
        rows.append(
            (
                Text(""),
                Text(f"    +{extra} more in flight", style="dim"),
                Text(""),
                Text(""),
            )
        )
    queued = max(0, snapshot.total - snapshot.done - len(snapshot.in_flight))
    if queued > 0:
        rows.append(
            (
                Text(""),
                Text(f"    {queued} queued", style="dim"),
                Text(""),
                Text(""),
            )
        )
    return rows


class LiveProgress(RenderEvents):
    """A Rich live checklist rendered to stderr (TTY)."""

    def __init__(
        self, source: str = "", *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._state = ProgressState(source, clock=clock)
        self._clock = clock
        self._console = Console(stderr=True, highlight=False, emoji=False, markup=False)
        self._live = Live(
            self,
            console=self._console,
            refresh_per_second=10,
            transient=False,
        )
        self._live_started = False
        self._final = False
        self._disabled = False
        self._reported = False

    def _safe(self, action: Callable[[], None]) -> None:
        if self._disabled:
            return
        try:
            action()
        except Exception as exc:
            self._disable(exc)

    def _disable(self, exc: BaseException) -> None:
        self._disabled = True
        with contextlib.suppress(Exception):
            if self._live_started:
                self._live.stop()
                self._live_started = False
        if not self._reported:
            self._reported = True
            with contextlib.suppress(Exception):
                self._console.print(
                    f"progress display error: {exc} (the render was not affected)",
                    style="dim",
                )

    def __rich__(self) -> Any:
        try:
            snapshot = replace(self._state.snapshot(self._clock()), final=self._final)
            width = self._console.width or 80
            return build_view(snapshot, self._clock(), width)
        except Exception as exc:
            self._disable(exc)
            return Text("progress display unavailable", style="dim")

    def __enter__(self) -> LiveProgress:
        try:
            self._live.start()
            self._live_started = True
        except Exception as exc:
            self._disable(exc)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None and issubclass(exc_type, KeyboardInterrupt):
                self._state.mark_active(STATUS_INTERRUPTED, "interrupted")
            elif exc_type is not None:
                self._state.mark_active(STATUS_FAILED, str(exc) if exc else "failed")
            else:
                self._state.finish_active()
            self._final = True
        except Exception as err:
            self._disable(err)
        finally:
            with contextlib.suppress(Exception):
                if self._live_started:
                    self._live.stop()
                    self._live_started = False

    def force_stop(self) -> None:
        """Stop the live region immediately (second Ctrl-C path)."""
        try:
            if self._live_started:
                self._live.stop()
                self._live_started = False
        except Exception:
            pass

    def on_stages(self, stages: Sequence[str]) -> None:
        """Announce (or refine) the full stage list."""
        self._safe(lambda: self._state.on_stages(stages))

    def on_stage(self, stage: str) -> None:
        """Mark a stage as started."""
        self._safe(lambda: self._state.on_stage(stage))

    def on_step(self, stage: str, text: str) -> None:
        """Update the live sub-step text for a stage."""
        self._safe(lambda: self._state.on_step(stage, text))

    def on_stage_done(
        self, stage: str, summary: str = "", *, warning: bool = False
    ) -> None:
        """Mark a stage as done with a short summary."""
        self._safe(lambda: self._state.on_stage_done(stage, summary, warning=warning))

    def on_title(self, title: str) -> None:
        """Report the source title once known."""
        self._safe(lambda: self._state.on_title(title))

    def on_plan(self, plan: RenderPlan) -> None:
        """Record header facts and the chunk chapter map."""
        self._safe(lambda: self._state.on_plan(plan))

    def on_chunk_started(self, index: int, total: int) -> None:
        """Record a chunk request that really began."""
        self._safe(lambda: self._state.on_chunk_started(index, total))

    def on_chunk_finished(self, index: int, total: int, *, cached: bool) -> None:
        """Record a chunk with audio (cached or synthesized)."""
        self._safe(lambda: self._state.on_chunk_finished(index, total, cached=cached))

    def on_chunk_retried(self, index: int, attempt: int, reason: str) -> None:
        """Note a gate re-synthesis on the gates row."""
        self._safe(lambda: self._state.on_chunk_retried(index, attempt, reason))

    def on_retry_wait(self, stage: str, chunk: int | None, wait: RetryWait) -> None:
        """Record a pending backoff sleep with its deadline."""
        self._safe(lambda: self._state.on_retry_wait(stage, chunk, wait))

    def on_done(self, result: RenderResult) -> None:
        """Record completion (rows already carry the outcome)."""
        self._safe(lambda: self._state.on_done(result))


class PlainProgress(RenderEvents):
    """Line-oriented progress for non-TTY stderr (no ANSI, thread-safe)."""

    def __init__(
        self,
        source: str = "",
        *,
        file: Any | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._source = source
        self._file = file
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._glyphs: dict[str, str] | None = None
        self._title = ""
        self._header_shown = ""
        self._starts: dict[str, float] = {}
        self._chunk_starts: dict[int, float] = {}
        self._total = 0
        self._done = 0
        self._cached_pending = 0

    def _stream(self) -> Any:
        return self._file if self._file is not None else sys.stderr

    def _marks(self) -> dict[str, str]:
        if self._glyphs is None:
            stream = self._stream()
            encoding = getattr(stream, "encoding", None) or "ascii"
            try:
                "\u266a\u2192\u00b7\u21bb\u2713\u26a0\u2717\u25a0\u2501".encode(
                    encoding
                )
            except (LookupError, UnicodeEncodeError, TypeError):
                self._glyphs = dict(GLYPH_ASCII)
            else:
                self._glyphs = dict(GLYPH_UNICODE)
        return self._glyphs

    def _write(self, line: str) -> None:
        stream = self._stream()
        stream.write(line + "\n")
        with contextlib.suppress(ValueError, OSError):
            stream.flush()

    def _emit(self, line: str) -> None:
        title = self._title or source_display(self._source)
        if title != self._header_shown:
            self._header_shown = title
            self._write(f"{self._marks()['audio']} {title}")
        self._write(line)

    def _label(self, stage: str) -> str:
        return _label_for(stage, self._source)

    def _flush_cached(self) -> None:
        if self._cached_pending:
            count = self._cached_pending
            self._cached_pending = 0
            noun = "chunk" if count == 1 else "chunks"
            self._emit(f"  {self._marks()['pending']} {count} {noun} cached")

    def force_stop(self) -> None:
        """Flush buffered lines (plain mode has no live region)."""
        with self._lock:
            self._flush_cached()

    def on_stages(self, stages: Sequence[str]) -> None:
        """Announce (or refine) the full stage list (no output)."""

    def on_stage(self, stage: str) -> None:
        """Mark a stage as started."""
        with self._lock:
            self._starts.setdefault(stage, self._clock())
            self._emit(f"{self._marks()['arrow']} {self._label(stage)}")

    def on_step(self, stage: str, text: str) -> None:
        """Update the live sub-step text for a stage."""
        with self._lock:
            self._starts.setdefault(stage, self._clock())
            self._emit(f"  {self._marks()['pending']} {text}")

    def on_stage_done(
        self, stage: str, summary: str = "", *, warning: bool = False
    ) -> None:
        """Mark a stage as done with a short summary."""
        with self._lock:
            self._flush_cached()
            started = self._starts.pop(stage, self._clock())
            duration = format_duration(self._clock() - started)
            mark = self._marks()["warn"] if warning else self._marks()["ok"]
            self._emit(f"{mark} {self._label(stage)} ({duration}): {summary}")

    def on_title(self, title: str) -> None:
        """Report the source title once known."""
        with self._lock:
            self._title = title

    def on_plan(self, plan: RenderPlan) -> None:
        """Record header facts and the chunk totals."""
        with self._lock:
            self._total = len(plan.chunks)
            if not self._title:
                self._title = plan.title

    def on_chunk_started(self, index: int, total: int) -> None:
        """Record a chunk request that really began."""
        with self._lock:
            self._total = total
            self._chunk_starts[index] = self._clock()

    def on_chunk_finished(self, index: int, total: int, *, cached: bool) -> None:
        """Record a chunk with audio (cached or synthesized)."""
        with self._lock:
            self._total = total
            self._done += 1
            if cached:
                self._cached_pending += 1
                return
            self._flush_cached()
            started = self._chunk_starts.pop(index, None)
            now = self._clock()
            duration = format_duration(now - started if started is not None else 0.0)
            number = index + 1
            dot = self._marks()["pending"]
            self._emit(
                f"  {dot} chunk {number} of {total} "
                f"synthesized ({duration}) {dot} {self._done}/{total} done"
            )

    def on_chunk_retried(self, index: int, attempt: int, reason: str) -> None:
        """Note a gate re-synthesis."""
        with self._lock:
            total = max(self._total, 1)
            dot = self._marks()["pending"]
            self._emit(
                f"  {dot} re-synthesizing chunk {index + 1} of {total} {dot} {reason}"
            )

    def on_retry_wait(self, stage: str, chunk: int | None, wait: RetryWait) -> None:
        """Record a pending backoff sleep."""
        with self._lock:
            seconds = max(0, round(wait.delay_s))
            dot = self._marks()["pending"]
            countdown = (
                f"retry {wait.attempt} of {wait.max_retries} in {seconds}s "
                f"{dot} {wait.reason}"
            )
            if chunk is None:
                self._emit(f"  {self._marks()['retry']} {countdown}")
            else:
                total = max(self._total, 1)
                self._emit(
                    f"  {self._marks()['retry']} chunk {chunk + 1} of {total}: "
                    f"{countdown}"
                )

    def on_done(self, result: RenderResult) -> None:
        """Record completion (no output; the summary goes to stdout)."""
        with self._lock:
            if not self._title:
                self._title = result.title


def resolve_mode(flag: str, *, as_json: bool, console: Console) -> str:
    """Resolve `--progress` to `live`, `plain`, or `off`."""
    if flag == "live":
        return "live"
    if flag == "plain":
        return "plain"
    if flag == "off":
        return "off"
    if as_json:
        return "off"
    if console.is_terminal and os.environ.get("TERM") != "dumb":
        return "live"
    return "plain"


def build_progress(mode: str, source: str) -> LiveProgress | PlainProgress | None:
    """Build the progress sink for a resolved mode (None for `off`)."""
    if mode == "live":
        return LiveProgress(source)
    if mode == "plain":
        return PlainProgress(source)
    return None


@contextmanager
def interrupt_guard(
    on_force_quit: Callable[[], None],
) -> Iterator[None]:
    """Guard Ctrl-C: first SIGINT raises, second force-quits via callback."""
    previous = signal.getsignal(signal.SIGINT)
    state = {"count": 0}

    def _handler(signum: int, frame: FrameType | None) -> None:
        state["count"] += 1
        if state["count"] == 1:
            raise KeyboardInterrupt
        on_force_quit()
        os._exit(130)

    signal.signal(signal.SIGINT, _handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


class _Activity:
    """One short network wait with live updates (TTY status or plain lines)."""

    def __init__(self, text: str, *, enabled: bool = True) -> None:
        self._text = text
        self._enabled = enabled
        self._console = Console(stderr=True, highlight=False, emoji=False, markup=False)
        self._tty = (
            enabled and self._console.is_terminal and os.environ.get("TERM") != "dumb"
        )
        self._status: Any | None = None

    def __enter__(self) -> _Activity:
        if not self._enabled:
            return self
        if self._tty:
            self._status = self._console.status(
                self._text, spinner=SPINNER, spinner_style=ACCENT
            )
            self._status.__enter__()
        else:
            self._console.print(self._text)
        return self

    def update(self, text: str) -> None:
        """Replace the wait text (a plain line when not a TTY)."""
        if not self._enabled:
            return
        if self._status is not None:
            self._status.update(text)
        else:
            self._console.print(text)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._status is not None:
            try:
                self._status.__exit__(exc_type, exc, tb)
            finally:
                self._status = None


def activity(text: str, *, enabled: bool = True) -> _Activity:
    """Wrap one short network wait with live updates on stderr."""
    return _Activity(text, enabled=enabled)
