"""Event protocol tests: stage order, summaries, and publish rules."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from sase_listen.config import default_config
from sase_listen.engines.retry import RetryWait
from sase_listen.engines.tone import ToneEngine
from sase_listen.events import RenderEvents
from sase_listen.pipeline import (
    RenderPlan,
    RenderRequest,
    RenderResult,
    expected_stages,
    load_source,
    render,
    should_publish,
)

TINY_SCRIPT = """\
---
narration: 1
title: Tiny Episode
kind: document
edition: verbatim
producer: agent
---

## First chapter

Hello world, this is a short spoken paragraph for the test.

## Second chapter

Another short paragraph with a few more words for good measure.
"""


def _write(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XDG dirs and the config at a temp tree (no user state touched)."""
    base = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(base / "missing-config.yml"))
    for name in (
        "SASE_LISTEN_GEMINI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "SASE_LISTEN_OPENAI_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return base


class RecordingEvents(RenderEvents):
    """Thread-safe event log with worker-thread provenance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.main_thread = threading.get_ident()

    def _record(self, name: str, *args: Any) -> None:
        with self._lock:
            self.calls.append((name, args))

    def names(self) -> list[str]:
        """Return the recorded method names in order."""
        with self._lock:
            return [name for name, _ in self.calls]

    def on_stages(self, stages: Sequence[str]) -> None:
        """Announce (or refine) the full stage list."""
        self._record("on_stages", list(stages))

    def on_stage(self, stage: str) -> None:
        """Mark a stage as started."""
        self._record("on_stage", stage)

    def on_step(self, stage: str, text: str) -> None:
        """Update the live sub-step text for a stage."""
        self._record("on_step", stage, text)

    def on_stage_done(
        self, stage: str, summary: str = "", *, warning: bool = False
    ) -> None:
        """Mark a stage as done with a short summary."""
        self._record("on_stage_done", stage, summary, warning)

    def on_title(self, title: str) -> None:
        """Report the source title once known."""
        self._record("on_title", title)

    def on_plan(self, plan: RenderPlan) -> None:
        """Called once chunk planning (and cache lookup) finishes."""
        self._record("on_plan", plan)

    def on_chunk_started(self, index: int, total: int) -> None:
        """Record a chunk start with its thread identity."""
        self._record("on_chunk_started", index, total, threading.get_ident())

    def on_chunk_finished(self, index: int, total: int, *, cached: bool) -> None:
        """Record a chunk finish."""
        self._record("on_chunk_finished", index, total, cached)

    def on_chunk_retried(self, index: int, attempt: int, reason: str) -> None:
        """Record a retry."""
        self._record("on_chunk_retried", index, attempt, reason)

    def on_retry_wait(self, stage: str, chunk: int | None, wait: RetryWait) -> None:
        """Record a backoff wait."""
        self._record("on_retry_wait", stage, chunk, wait)

    def on_done(self, result: RenderResult) -> None:
        """Record completion."""
        self._record("on_done", result)


def test_tone_render_stage_order_and_summaries(isolated: Path, tmp_path: Path) -> None:
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    events = RecordingEvents()
    outcome = render(
        RenderRequest(source=source, narrator="tone", no_cache=True),
        engine=ToneEngine(),
        events=events,
        library_root=tmp_path / "library",
    )
    assert not isinstance(outcome, RenderPlan)
    names = events.names()
    assert names[0] == "on_stages"
    stages = [args[0] for name, args in events.calls if name == "on_stage"]
    assert stages == ["source", "plan", "synthesize", "gates", "master", "save"]
    done = {args[0]: args[1] for name, args in events.calls if name == "on_stage_done"}
    assert set(done) == set(stages)
    assert all(summary for summary in done.values())
    assert "on_title" in names
    started_threads = {
        args[2]
        for name, args in events.calls
        if name == "on_chunk_started" and len(args) == 3
    }
    assert started_threads and events.main_thread not in started_threads
    steps = [(args[0], args[1]) for name, args in events.calls if name == "on_step"]
    master_steps = [text for stage, text in steps if stage == "master"]
    assert "measuring loudness (pass 1 of 2)" in master_steps
    assert "encoding the MP3 (pass 2 of 2)" in master_steps
    assert "publish" not in stages


def test_publish_stage_present_with_local_feed(isolated: Path, tmp_path: Path) -> None:
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    cfg = default_config()
    cfg.narrator = "tone"
    cfg.feed.dir = str(tmp_path / "feed")
    cfg.feed.base_url = "https://example.com:8443"
    cfg.feed.token = "test-token-abc"
    cfg.feed.title = "Test Feed"
    events = RecordingEvents()
    outcome = render(
        RenderRequest(source=source, publish=True),
        config=cfg,
        engine=ToneEngine(),
        events=events,
        library_root=tmp_path / "library",
    )
    assert not isinstance(outcome, RenderPlan)
    stages = [args[0] for name, args in events.calls if name == "on_stage"]
    assert stages[-1] == "publish"
    done = {args[0]: args[1] for name, args in events.calls if name == "on_stage_done"}
    assert done["publish"] == "local feed"
    assert outcome.published is True


def test_dry_run_stops_at_plan(isolated: Path, tmp_path: Path) -> None:
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    events = RecordingEvents()
    outcome = render(
        RenderRequest(source=source, narrator="tone", dry_run=True),
        engine=ToneEngine(),
        events=events,
        library_root=tmp_path / "library",
    )
    assert isinstance(outcome, RenderPlan)
    stages = [args[0] for name, args in events.calls if name == "on_stage"]
    assert stages == ["source", "plan"]
    assert "synthesize" not in stages


def test_url_brief_write_steps_and_cached_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(base / "missing-config.yml"))

    from sase_listen.writer.base import WriterReply

    prose = (
        "A detailed synthetic paragraph explains the engineering choices and "
        "their effects for readers who need to understand the complete example. "
        "It includes enough meaningful words to pass the article length check. "
    )
    html = (
        "<!doctype html><html><head><title>Cached Story</title></head>"
        "<body><article><h1>Cached Story</h1>"
        f"<h2>First Section</h2><p>{prose * 4}</p>"
        f"<h2>Second Section</h2><p>{prose * 4}</p>"
        f"<h3>Third Section</h3><p>{prose * 4}</p>"
        "</article></body></html>"
    ).encode()
    saved = tmp_path / "browser.html"
    saved.write_bytes(html)

    class FakeWriter:
        def write(self, system: str, user: str) -> WriterReply:
            paragraph = (
                "The team carefully describes the engineering process and results. "
            )
            return WriterReply(
                "## The question\n\n"
                + paragraph * 25
                + "\n\n## The evidence\n\n"
                + paragraph * 25,
                "stub-brief-v1",
                50,
                75,
            )

    import sase_listen.writer

    monkeypatch.setattr(
        sase_listen.writer, "create_writer", lambda cfg, **kwargs: FakeWriter()
    )
    cfg = default_config()
    cfg.narrator = "tone"
    url = "https://example.test/cached-story"
    first = RecordingEvents()
    load_source(url, edition="brief", html_file=str(saved), config=cfg, events=first)
    write_steps = [
        args[1]
        for name, args in first.calls
        if name == "on_step" and args[0] == "write"
    ]
    assert any("attempt 1 of" in step for step in write_steps)
    second = RecordingEvents()
    load_source(url, edition="brief", html_file=str(saved), config=cfg, events=second)
    write_done = [
        args[1]
        for name, args in second.calls
        if name == "on_stage_done" and args[0] == "write"
    ]
    assert write_done and write_done[0].endswith("· cached")
    rerun_steps = [
        args[1]
        for name, args in second.calls
        if name == "on_step" and args[0] == "write"
    ]
    assert not any("attempt 1 of" in step for step in rerun_steps)


def test_cli_render_interrupt_returns_130_and_keeps_cache(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import _thread
    import time as time_module

    from sase_listen.cli.app import main
    from sase_listen.engines import SynthesisRequest, SynthesisResult

    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    calls = {"n": 0}

    class InterruptingEngine(ToneEngine):
        def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
            calls["n"] += 1
            if calls["n"] == 2:
                _thread.interrupt_main()
                time_module.sleep(0.5)
            return super().synthesize(request)

    import sase_listen.pipeline as pipeline_module

    engine = InterruptingEngine()
    monkeypatch.setattr(pipeline_module, "default_engine", lambda narrator, cfg: engine)
    monkeypatch.setattr(
        pipeline_module, "transport_tuning", lambda narrator, cfg, eng: (1, 0)
    )
    code = main(["render", source, "-n", "tone", "--progress", "plain"])
    assert code == 130
    err = capsys.readouterr().err
    assert "cached" in err
    assert "re-run" in err
    assert calls["n"] == 2


def test_should_publish_and_expected_stages_truth_table() -> None:
    cfg = default_config()
    cfg.feed.auto_publish = True
    auto = RenderRequest(source="notes.md", narrator="tone")
    assert should_publish(auto, cfg, "article") is True
    assert should_publish(auto, cfg, "research") is True
    assert should_publish(auto, cfg, "document") is False
    assert should_publish(auto, cfg, None) is True
    explicit_on = RenderRequest(source="notes.md", narrator="tone", publish=True)
    assert should_publish(explicit_on, cfg, "document") is True
    explicit_off = RenderRequest(source="notes.md", narrator="tone", publish=False)
    assert should_publish(explicit_off, cfg, "article") is False
    cfg.feed.auto_publish = False
    assert should_publish(auto, cfg, None) is False

    cfg.feed.auto_publish = True
    url_request = RenderRequest(
        source="https://example.test/x", narrator="tone", edition="brief"
    )
    assert [s.value for s in expected_stages(url_request, cfg, "article")][:2] == [
        "source",
        "write",
    ]
    file_request = RenderRequest(source="notes.md", narrator="tone")
    assert [s.value for s in expected_stages(file_request, cfg, "document")][:2] == [
        "source",
        "plan",
    ]
    assert "publish" in [s.value for s in expected_stages(url_request, cfg, "article")]
    assert "publish" not in [
        s.value for s in expected_stages(file_request, cfg, "document")
    ]
    dry = RenderRequest(source="notes.md", narrator="tone", dry_run=True)
    assert [s.value for s in expected_stages(dry, cfg, "document")] == [
        "source",
        "plan",
    ]


def test_interrupt_regression_queued_chunks_never_start(
    isolated: Path, tmp_path: Path
) -> None:
    import _thread

    from sase_listen.engines import SynthesisRequest, SynthesisResult
    from sase_listen.pipeline import plan_request, prepare, synthesize_chunks

    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    request = RenderRequest(source=source, narrator="tone")
    prepared = prepare(request)
    plan = plan_request(request, prepared, load_source(source))
    assert len(plan.chunks) == 4
    release = threading.Event()
    continued = threading.Event()
    calls = {"n": 0}
    calls_lock = threading.Lock()

    class InterruptingEngine(ToneEngine):
        def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
            with calls_lock:
                calls["n"] += 1
                seen = calls["n"]
            if seen == 2:
                _thread.interrupt_main()
                assert release.wait(timeout=5)
                continued.set()
            return super().synthesize(request)

    events = RecordingEvents()

    def _watch() -> None:
        while not release.is_set():
            if any(
                name == "on_step" and "stopping ·" in str(args)
                for name, args in events.calls
            ):
                release.set()
                return
            time.sleep(0.01)

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    with pytest.raises(KeyboardInterrupt):
        synthesize_chunks(
            plan,
            InterruptingEngine(),
            prepared.cache,
            max_retries=0,
            concurrency=1,
            use_cache=True,
            events=events,
            config=prepared.config,
        )
    assert continued.is_set()
    assert calls["n"] == 2
    cache = prepared.cache
    assert cache.get(plan.chunks[0].cache_key) is not None
    assert cache.get(plan.chunks[1].cache_key) is not None
    assert cache.get(plan.chunks[2].cache_key) is None
    assert cache.get(plan.chunks[3].cache_key) is None
