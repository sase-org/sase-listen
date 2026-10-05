"""Progress display tests: state, views, plain lines, and guards."""

from __future__ import annotations

import io
import signal
from typing import Any

import pytest
from rich.console import Console

from sase_listen.cli.progress import (
    PlainProgress,
    ProgressState,
    Snapshot,
    _eta_text,
    build_view,
    interrupt_guard,
    resolve_mode,
)
from sase_listen.engines.retry import RetryWait


class FakeClock:
    """Manual monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now += seconds


def _render_text(snapshot: Snapshot, now: float, width: int = 80) -> str:
    console = Console(
        file=io.StringIO(), width=width, force_terminal=False, color_system=None
    )
    with console.capture() as capture:
        console.print(build_view(snapshot, now, width))
    return capture.get()


def _writing_state() -> tuple[ProgressState, FakeClock]:
    clock = FakeClock()
    state = ProgressState("notes.md", clock=clock)
    state.on_stages(["source", "plan", "synthesize"])
    state.on_stage("source")
    state.on_title("Notes")
    clock.advance(1.2)
    state.on_stage_done("source", "notes.md · 12 words · normalized from Markdown")
    state.on_stage("write")
    state.on_step("write", "attempt 1 of 3 · waiting on gemini-3.1-pro-preview")
    return state, clock


def test_writing_view_golden() -> None:
    state, clock = _writing_state()
    clock.advance(6.4)
    text = _render_text(state.snapshot(clock()), clock(), 80)
    assert "Notes" in text
    assert "Fetch article" not in text
    assert "Read file" in text
    assert "attempt 1 of 3" in text
    assert "6s" in text


def test_mid_synthesis_view() -> None:
    clock = FakeClock()
    state = ProgressState("https://openai.com/symphony", clock=clock)
    state.on_stages(["source", "write", "plan", "synthesize", "gates"])
    state.on_title("Symphony")
    state.on_stage("synthesize")
    state.on_chunk_started(6, 10)
    clock.advance(12.0)
    state.on_chunk_started(7, 10)
    clock.advance(3.0)
    state.on_chunk_started(8, 10)
    state.on_retry_wait(
        "synthesize",
        8,
        RetryWait(attempt=1, max_retries=4, delay_s=14.0, reason="rate-limited"),
    )
    clock.advance(1.0)
    state.on_chunk_finished(0, 10, cached=False)
    text = _render_text(state.snapshot(clock()), clock(), 80)
    assert "chunk 7" in text
    assert "chunk 8" in text
    assert "retry 1 of 4 in 13s" in text
    assert "6 queued" in text


def test_more_in_flight_and_queued() -> None:
    clock = FakeClock()
    state = ProgressState("notes.md", clock=clock)
    state.on_stages(["synthesize"])
    state.on_stage("synthesize")
    for index in range(6):
        state.on_chunk_started(index, 10)
    text = _render_text(state.snapshot(clock()), clock(), 80)
    assert "+2 more in flight" in text
    assert "4 queued" in text


def test_failure_and_interrupt_views() -> None:
    clock = FakeClock()
    state = ProgressState("notes.md", clock=clock)
    state.on_stages(["source", "plan"])
    state.on_stage("source")
    state.mark_active("failed", "boom happened\nsecond line")
    text = _render_text(state.snapshot(clock()), clock(), 80)
    assert "boom happened" in text
    assert "second line" not in text

    clock2 = FakeClock()
    state2 = ProgressState("notes.md", clock=clock2)
    state2.on_stages(["source", "plan"])
    state2.on_stage("source")
    state2.mark_active("interrupted", "interrupted")
    text2 = _render_text(state2.snapshot(clock2()), clock2(), 80)
    assert "interrupted" in text2


def test_final_frame_has_no_subrows_or_elapsed() -> None:
    from dataclasses import replace

    clock = FakeClock()
    state = ProgressState("notes.md", clock=clock)
    state.on_stages(["synthesize"])
    state.on_stage("synthesize")
    state.on_chunk_started(0, 2)
    clock.advance(31.0)
    snapshot = replace(state.snapshot(clock()), final=True)
    text = _render_text(snapshot, clock(), 80)
    assert "chunk 1" not in text
    assert "31s" not in text.splitlines()[0]


def test_narrow_width_keeps_time_column() -> None:
    state, clock = _writing_state()
    text = _render_text(state.snapshot(clock()), clock(), 50)
    assert "Read file" in text


def test_markup_titles_render_literally() -> None:
    clock = FakeClock()
    state = ProgressState("notes.md", clock=clock)
    state.on_stages(["source"])
    state.on_stage("source")
    state.on_title("[bold]x[/bold] :smile:")
    text = _render_text(state.snapshot(clock()), clock(), 80)
    assert "[bold]x[/bold] :smile:" in text


def test_on_stages_refinement_keeps_done_rows() -> None:
    clock = FakeClock()
    state = ProgressState("https://example.test/x", clock=clock)
    state.on_stages(["source", "plan", "synthesize"])
    state.on_stage("source")
    state.on_stage_done("source", "example.test · 10 words")
    state.on_stages(["source", "write", "plan", "synthesize"])
    snapshot = state.snapshot(clock())
    labels = [row.label for row in snapshot.rows]
    assert labels[0] == "Fetch article"
    assert "Write script" in labels
    assert snapshot.rows[0].status == "done"


def test_eta_rounding() -> None:
    from sase_listen.cli.progress import Snapshot

    def _snap(durations: tuple[float, ...], parallel: int) -> Snapshot:
        return Snapshot(
            title="t",
            subtitle="",
            episode_id="",
            elapsed_s=0.0,
            rows=(),
            total=10,
            done=len(durations),
            cached=0,
            in_flight=((5, 2.0),),
            waits=(),
            stage_waits=(),
            finished_durations=durations,
            max_parallel=parallel,
            chapters={},
            active_stage="synthesize",
        )

    assert _eta_text(_snap((), 3)) == ""
    assert _eta_text(_snap((12.0,), 3)) == "~40s left"
    assert _eta_text(_snap((200.0,) * 3, 2)) == "~13m left"


def test_plain_exact_lines() -> None:
    class Utf8Stream(io.StringIO):
        encoding = "utf-8"

    stream = Utf8Stream()
    events = PlainProgress("notes.md", file=stream)
    events.on_title("Notes")
    events.on_stage("source")
    events.on_stage_done("source", "notes.md · 12 words")
    events.on_stage("synthesize")
    events.on_chunk_finished(0, 3, cached=True)
    events.on_chunk_finished(1, 3, cached=True)
    events.on_chunk_started(2, 3)
    events.on_chunk_finished(2, 3, cached=False)
    events.on_stage_done("synthesize", "1 synthesized · 2 cached · 0 retries")
    lines = stream.getvalue().splitlines()
    assert lines[0] == "♪ Notes"
    assert lines[1] == "→ Read file"
    assert lines[2] == "✓ Read file (0.0s): notes.md · 12 words"
    assert lines[3] == "→ Synthesize"
    assert lines[4] == "  · 2 chunks cached"
    assert lines[5].startswith("  · chunk 3 of 3 synthesized (")
    assert lines[5].endswith("· 3/3 done")
    assert lines[6].startswith("✓ Synthesize (")
    assert lines[6].endswith(": 1 synthesized · 2 cached · 0 retries")
    assert "\x1b" not in stream.getvalue()


def test_plain_ascii_fallback() -> None:
    class AsciiStream(io.StringIO):
        encoding = "ascii"

    stream = AsciiStream()
    events = PlainProgress("notes.md", file=stream)
    events.on_title("Notes")
    events.on_stage("source")
    events.on_retry_wait(
        "source", None, RetryWait(attempt=1, max_retries=3, delay_s=5.0, reason="slow")
    )
    events.on_stage_done("source", "done", warning=True)
    text = stream.getvalue()
    text.encode("ascii")
    assert "-> Read file" in text
    assert "~ retry 1 of 3 in 5s - slow" in text
    assert text.splitlines()[-1].startswith("!! Read file (")


def test_plain_unicode_stream() -> None:
    class Utf8Stream(io.StringIO):
        encoding = "utf-8"

    stream = Utf8Stream()
    events = PlainProgress("https://openai.com/x", file=stream)
    events.on_title("Symphony")
    events.on_stage("source")
    text = stream.getvalue()
    assert text.splitlines()[0] == "♪ Symphony"
    assert "→ Fetch article" in text


def test_display_guard_never_propagates(capsys: Any) -> None:
    from sase_listen.cli.progress import LiveProgress

    progress = LiveProgress("notes.md")
    progress.on_stage("source")
    with progress._state._lock:
        progress._state._rows.clear()
        progress._state._rows.append(None)  # type: ignore[list-item]
    progress.on_step("source", "boom")
    progress.on_stage_done("source", "done")
    err = capsys.readouterr().err
    assert "progress display error" in err
    assert "not affected" in err


def test_resolve_mode_table(monkeypatch: pytest.MonkeyPatch) -> None:
    live_console = Console(
        file=io.StringIO(), force_terminal=True, color_system="truecolor"
    )
    plain_console = Console(file=io.StringIO(), force_terminal=False)
    assert resolve_mode("auto", as_json=True, console=live_console) == "off"
    assert resolve_mode("auto", as_json=False, console=plain_console) == "plain"
    assert resolve_mode("live", as_json=True, console=plain_console) == "live"
    assert resolve_mode("plain", as_json=False, console=live_console) == "plain"
    assert resolve_mode("off", as_json=False, console=live_console) == "off"
    monkeypatch.setenv("TERM", "dumb")
    assert resolve_mode("auto", as_json=False, console=live_console) == "plain"


def test_interrupt_guard_first_raises_second_quits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exited: list[int] = []
    monkeypatch.setattr(
        "sase_listen.cli.progress.os._exit", lambda code: exited.append(code)
    )
    called: list[bool] = []
    with interrupt_guard(lambda: called.append(True)):
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)  # type: ignore[operator]
        handler(signal.SIGINT, None)  # type: ignore[operator]
    assert called == [True]
    assert exited == [130]


def test_interrupt_guard_restores_handler() -> None:
    previous = signal.getsignal(signal.SIGINT)
    with interrupt_guard(lambda: None):
        pass
    assert signal.getsignal(signal.SIGINT) is previous


def test_activity_plain_and_disabled(capsys: Any) -> None:
    from sase_listen.cli.progress import activity

    with activity("Fetching…", enabled=True) as act:
        act.update("Still fetching…")
    err = capsys.readouterr().err
    assert "Fetching…" in err
    assert "Still fetching…" in err
    with activity("Silent…", enabled=False) as act:
        act.update("Still silent…")
    assert capsys.readouterr().err == ""


def test_no_color_keeps_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm")
    live_console = Console(file=io.StringIO(), force_terminal=True, color_system=None)
    assert resolve_mode("auto", as_json=False, console=live_console) == "live"


def test_no_color_env_keeps_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("NO_COLOR", "1")
    live_console = Console(
        file=io.StringIO(), force_terminal=True, color_system="truecolor"
    )
    assert resolve_mode("auto", as_json=False, console=live_console) == "live"
