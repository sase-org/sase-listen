"""Replay a scripted render timeline through LiveProgress (visual QA).

Animated: ``python tools/progress_demo.py [--speed 4]`` runs the full
timeline through ``LiveProgress`` at real speed divided by ``--speed``.
Press Ctrl-C mid-synthesis to check the interrupt frame and block.

Frames: ``python tools/progress_demo.py --svg-dir DIR`` renders the key
frames (writing, mid-synthesis, final, failure) as SVG files under a fake
clock for screenshot review.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rich.console import Console

from sase_listen.cli.progress import LiveProgress, ProgressState, build_view
from sase_listen.engines.retry import RetryWait

TITLE = "An open-source spec for Codex orchestration: Symphony"
CHAPTERS = [
    "Why specs beat prompts",
    "Running Symphony locally",
    "The orchestrator loop",
    "Memory that compounds",
    "Tools that check themselves",
    "Shipping the Symphony",
    "What we cut",
    "Outro",
]
MODEL = "gemini-3.1-pro-preview"
VOICE = "Charon"
TTS_MODEL = "gemini-3.8-flash-tts"


def fake_plan(total: int = 10) -> SimpleNamespace:
    """Build a duck-typed plan (only the fields progress reads)."""
    chunks = []
    for index in range(total):
        if index == 0:
            kind, chapter = "intro", "Intro"
        elif index == total - 1:
            kind, chapter = "outro", "Outro"
        else:
            kind, chapter = "content", CHAPTERS[(index - 1) % len(CHAPTERS)]
        chunks.append(
            SimpleNamespace(index=index, kind=kind, chapter=chapter, words=220)
        )
    narrator = SimpleNamespace(name="charon", voice=VOICE, model=TTS_MODEL)
    return SimpleNamespace(
        title=TITLE,
        episode_id="demo-episode-000001",
        edition="full",
        narrator=narrator,
        chunks=chunks,
    )


STAGES = [
    "source",
    "write",
    "plan",
    "synthesize",
    "gates",
    "master",
    "save",
    "publish",
]


def _tick(clock: object, seconds: float) -> None:
    if clock is not None:
        clock.advance(seconds)  # type: ignore[union-attr]


def feed_to_write(events: object, clock: object = None) -> None:
    """Feed the source and writer phases."""
    events.on_stages(STAGES)  # type: ignore[union-attr]
    events.on_stage("source")  # type: ignore[union-attr]
    events.on_step("source", "fetching openai.com")  # type: ignore[union-attr]
    _tick(clock, 0.1)
    events.on_step("source", "extracting the article text")  # type: ignore[union-attr]
    events.on_title(TITLE)  # type: ignore[union-attr]
    events.on_stage_done("source", "openai.com · 2,364 words")  # type: ignore[union-attr]
    events.on_stage("write")  # type: ignore[union-attr]
    events.on_step("write", "reading the Gemini API key")  # type: ignore[union-attr]
    _tick(clock, 1.0)
    events.on_step(  # type: ignore[union-attr]
        "write", f"attempt 1 of 3 · waiting on {MODEL}"
    )


def feed_writer_backoff(events: object, clock: object = None) -> None:
    """Feed one repair plus one 429 backoff, then the write summary."""
    _tick(clock, 14.0)
    events.on_retry_wait(  # type: ignore[union-attr]
        "write",
        None,
        RetryWait(attempt=1, max_retries=4, delay_s=14.0, reason="rate-limited (HTTP 429)"),
    )
    events.on_step(  # type: ignore[union-attr]
        "write", "attempt 1 of 3 · checking the draft against the article"
    )
    _tick(clock, 40.0)
    events.on_step(  # type: ignore[union-attr]
        "write", f"attempt 2 of 3 · fixing 2 lint findings · waiting on {MODEL}"
    )
    _tick(clock, 47.0)
    events.on_stage_done(  # type: ignore[union-attr]
        "write", "2,198 words · 8 chapters · 2 attempts"
    )


def feed_plan(events: object, clock: object = None) -> None:
    """Feed the plan phase."""
    events.on_stage("plan")  # type: ignore[union-attr]
    events.on_step("plan", "resolving the narrator and API key")  # type: ignore[union-attr]
    _tick(clock, 0.1)
    events.on_step("plan", "splitting 2,198 words into chunks")  # type: ignore[union-attr]
    events.on_plan(fake_plan())  # type: ignore[union-attr]
    events.on_step("plan", "preparing cover art")  # type: ignore[union-attr]
    _tick(clock, 0.2)
    events.on_stage_done(  # type: ignore[union-attr]
        "plan", "10 chunks · 2 cached · ≈14 min · ≈$0.19"
    )


def feed_mid_synthesis(events: object, clock: object = None) -> None:
    """Feed a mid-synthesis board: 6 done, 3 in flight, 1 retry, 1 queued."""
    events.on_stage("synthesize")  # type: ignore[union-attr]
    events.on_step("synthesize", "8 to synthesize · 3 at a time")  # type: ignore[union-attr]
    for index in (0, 1):
        events.on_chunk_finished(index, 10, cached=True)  # type: ignore[union-attr]
    for index in (2, 3, 4, 5):
        events.on_chunk_started(index, 10)  # type: ignore[union-attr]
        _tick(clock, 20.0)
        events.on_chunk_finished(index, 10, cached=False)  # type: ignore[union-attr]
    events.on_chunk_started(6, 10)  # type: ignore[union-attr]
    _tick(clock, 12.0)
    events.on_chunk_started(7, 10)  # type: ignore[union-attr]
    _tick(clock, 3.0)
    events.on_chunk_started(8, 10)  # type: ignore[union-attr]
    events.on_retry_wait(  # type: ignore[union-attr]
        "synthesize",
        8,
        RetryWait(attempt=1, max_retries=4, delay_s=14.0, reason="rate-limited (HTTP 429)"),
    )


def feed_synthesis_done(events: object, clock: object = None) -> None:
    """Finish synthesis, gates, master, save, and publish."""
    events.on_chunk_finished(6, 10, cached=False)  # type: ignore[union-attr]
    _tick(clock, 5.0)
    events.on_chunk_finished(7, 10, cached=False)  # type: ignore[union-attr]
    _tick(clock, 5.0)
    events.on_chunk_finished(8, 10, cached=False)  # type: ignore[union-attr]
    events.on_chunk_started(9, 10)  # type: ignore[union-attr]
    _tick(clock, 20.0)
    events.on_chunk_finished(9, 10, cached=False)  # type: ignore[union-attr]
    events.on_stage_done("synthesize", "8 synthesized · 2 cached · 1 retry")  # type: ignore[union-attr]
    events.on_stage("gates")  # type: ignore[union-attr]
    events.on_stage_done("gates", "all 10 chunks in range")  # type: ignore[union-attr]
    events.on_stage("master")  # type: ignore[union-attr]
    events.on_step("master", "assembling 8 chapter(s)")  # type: ignore[union-attr]
    events.on_step("master", "measuring loudness (pass 1 of 2)")  # type: ignore[union-attr]
    _tick(clock, 3.0)
    events.on_step("master", "encoding the MP3 (pass 2 of 2)")  # type: ignore[union-attr]
    _tick(clock, 3.4)
    events.on_step("master", "writing chapters and cover art")  # type: ignore[union-attr]
    events.on_stage_done("master", "14m 12s · -16.0 LUFS · 6.8 MB")  # type: ignore[union-attr]
    events.on_stage("save")  # type: ignore[union-attr]
    events.on_step("save", "verifying the MP3")  # type: ignore[union-attr]
    events.on_step("save", "saving to the library")  # type: ignore[union-attr]
    _tick(clock, 0.2)
    events.on_step("save", "pruning the chunk cache")  # type: ignore[union-attr]
    events.on_stage_done("save", "verified · 8 chapters")  # type: ignore[union-attr]
    events.on_stage("publish")  # type: ignore[union-attr]
    events.on_step("publish", "packing the episode")  # type: ignore[union-attr]
    events.on_step("publish", "sending to apollo via apollo")  # type: ignore[union-attr]
    _tick(clock, 2.1)
    events.on_stage_done("publish", "apollo (via apollo)")  # type: ignore[union-attr]


class FakeClock:
    """Manual monotonic clock for deterministic frames."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now += seconds


def render_svg_frame(path: Path, title: str, build, *, final: bool = False) -> None:  # type: ignore[no-untyped-def]
    """Render one frame to SVG under a fake clock."""
    from dataclasses import replace

    console = Console(record=True, width=84, force_terminal=True, color_system="truecolor")
    clock = FakeClock()
    state = ProgressState("https://openai.com/symphony", clock=clock)
    build(state, clock)
    snapshot = replace(state.snapshot(clock()), final=final)
    console.print(build_view(snapshot, clock(), 84))
    console.save_svg(str(path), title=title)


def writing_frame(state: ProgressState, clock: FakeClock) -> None:
    """Build the writing-phase frame."""
    feed_to_write(state, clock)
    clock.advance(41.0)


def synthesis_frame(state: ProgressState, clock: FakeClock) -> None:
    """Build the mid-synthesis frame."""
    feed_to_write(state, clock)
    feed_writer_backoff(state, clock)
    feed_plan(state, clock)
    feed_mid_synthesis(state, clock)


def final_frame(state: ProgressState, clock: FakeClock) -> None:
    """Build the finished success frame."""
    feed_to_write(state, clock)
    feed_writer_backoff(state, clock)
    feed_plan(state, clock)
    feed_mid_synthesis(state, clock)
    feed_synthesis_done(state, clock)


def failure_frame(state: ProgressState, clock: FakeClock) -> None:
    """Build the failure frame (master audio failed)."""
    feed_to_write(state, clock)
    feed_writer_backoff(state, clock)
    feed_plan(state, clock)
    feed_mid_synthesis(state, clock)
    state.on_chunk_finished(6, 10, cached=False)  # type: ignore[union-attr]
    state.on_chunk_finished(7, 10, cached=False)  # type: ignore[union-attr]
    state.on_chunk_finished(8, 10, cached=False)  # type: ignore[union-attr]
    state.on_chunk_started(9, 10)  # type: ignore[union-attr]
    state.on_chunk_finished(9, 10, cached=False)  # type: ignore[union-attr]
    state.on_stage_done("synthesize", "8 synthesized · 2 cached · 1 retry")  # type: ignore[union-attr]
    state.on_stage("gates")  # type: ignore[union-attr]
    state.on_stage_done("gates", "all 10 chunks in range")  # type: ignore[union-attr]
    state.on_stage("master")  # type: ignore[union-attr]
    state.on_step("master", "assembling 8 chapter(s)")  # type: ignore[union-attr]
    state.on_step("master", "measuring loudness (pass 1 of 2)")  # type: ignore[union-attr]
    clock.advance(6.0)
    state.mark_active("failed", "[Errno 18] Invalid cross-device link")


def write_svgs(directory: Path) -> list[Path]:
    """Write all key frames; return the SVG paths."""
    directory.mkdir(parents=True, exist_ok=True)
    frames = [
        ("writing.svg", "sase-listen render progress (writing)", writing_frame, False),
        (
            "synthesis.svg",
            "sase-listen render progress (synthesis)",
            synthesis_frame,
            False,
        ),
        ("final.svg", "sase-listen render progress (final)", final_frame, True),
        ("failure.svg", "sase-listen render progress (failure)", failure_frame, True),
    ]
    paths = []
    for name, title, build, final in frames:
        path = directory / name
        render_svg_frame(path, title, build, final=final)
        paths.append(path)
    return paths


def run_animated(speed: float) -> int:
    """Replay the timeline through LiveProgress at real speed / speed."""
    progress = LiveProgress("https://openai.com/symphony")
    script = [
        (0.0, feed_to_write),
        (1.2, feed_writer_backoff),
        (0.6, feed_plan),
        (0.4, feed_mid_synthesis),
        (6.0, feed_synthesis_done),
    ]
    with progress:
        for wait, feed in script:
            time.sleep(wait / speed)
            feed(progress)
        time.sleep(2.0 / speed)
    print("♪ Ready in 3m 12s · 14m 12s of audio · ≈$0.19")
    return 0


def main() -> int:
    """Run the demo (animated) or write SVG frames (--svg-dir)."""
    parser = argparse.ArgumentParser(description="Replay the render progress demo.")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--svg-dir", default="")
    args = parser.parse_args()
    if args.svg_dir:
        for path in write_svgs(Path(args.svg_dir)):
            print(f"wrote {path}")
        return 0
    try:
        return run_animated(max(0.1, args.speed))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
