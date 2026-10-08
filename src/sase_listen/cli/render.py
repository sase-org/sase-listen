"""render command: Markdown to MP3 episode. Owner: pipeline phase (UX: cli phase)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

from sase_listen import invocation
from sase_listen.cli.progress import (
    LiveProgress,
    ProgressState,
    Snapshot,
    build_progress,
    build_view,
    interrupt_guard,
    resolve_mode,
)
from sase_listen.engines.retry import RetryWait
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.events import RenderEvents, Stage
from sase_listen.pipeline import (
    RenderPlan,
    RenderRequest,
    RenderResult,
    error_to_json,
    plan_to_json,
    render,
    result_to_json,
)
from sase_listen.ui import GLYPH_AUDIO, approx_cost, format_duration


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    prog: str = "sase-listen",
) -> argparse.ArgumentParser:
    """Register the render parser."""
    p = sub.add_parser("render", help="Render a source into an MP3 episode.")
    source_action = p.add_argument(
        "source",
        help=(
            "Narration script, Markdown file, PDF file, kind:path ref, or http(s) URL"
        ),
    )
    invocation.mark_path_completion(source_action)
    cover_group = p.add_mutually_exclusive_group()
    cover_action = cover_group.add_argument(
        "--cover", default="", help="Cover image path."
    )
    invocation.mark_path_completion(cover_action)
    cover_group.add_argument(
        "-g",
        "--generated-cover",
        action="store_true",
        help="Generate the title card and ignore frontmatter and sibling artwork.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print the plan and stop.")
    p.add_argument(
        "-e",
        "--edition",
        choices=("brief", "full", "verbatim"),
        default=None,
        help="Edition for URL and PDF sources (default: brief).",
    )
    p.add_argument(
        "--force", action="store_true", help="Render despite structural lint errors."
    )
    html_action = p.add_argument(
        "-H",
        "--html",
        default="",
        metavar="FILE",
        help="Use a saved browser page (HTML or PDF) instead of fetching the URL.",
    )
    invocation.mark_path_completion(html_action)
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.add_argument("-n", "--narrator", default="", help="Narrator profile name.")
    p.add_argument("--no-cache", action="store_true", help="Bypass the chunk cache.")
    pub = p.add_mutually_exclusive_group()
    pub.add_argument("--no-publish", dest="publish", action="store_false")
    output_action = p.add_argument(
        "-o", "--output", default="", help="Copy the MP3 to PATH."
    )
    invocation.mark_path_completion(output_action)
    pub.add_argument("--publish", dest="publish", action="store_true", default=None)
    pub.set_defaults(publish=None)
    p.add_argument(
        "--progress",
        choices=("auto", "live", "plain", "off"),
        default="auto",
        help="Progress display: live checklist, plain lines, or off (default: auto).",
    )
    p.add_argument(
        "-r",
        "--refresh",
        action="store_true",
        help="Fetch or extract the source again and replace the cached copy.",
    )
    p.add_argument("--voice", default="", help="Override the narrator voice.")
    p.set_defaults(func=run)
    p.epilog = (
        f"Example: {prog} render https://example.com/article -e full\n"
        f"Example: {prog} render https://arxiv.org/abs/2608.25174 -e full"
    )
    return p


class _Fanout(RenderEvents):
    """Forward every event to each sink (display plus state tracking)."""

    def __init__(self, sinks: list[RenderEvents]) -> None:
        self._sinks = sinks

    def on_stages(self, stages: Sequence[str]) -> None:
        """Announce (or refine) the full stage list."""
        for sink in self._sinks:
            sink.on_stages(stages)

    def on_stage(self, stage: str) -> None:
        """Mark a stage as started."""
        for sink in self._sinks:
            sink.on_stage(stage)

    def on_step(self, stage: str, text: str) -> None:
        """Update the live sub-step text for a stage."""
        for sink in self._sinks:
            sink.on_step(stage, text)

    def on_stage_done(
        self, stage: str, summary: str = "", *, warning: bool = False
    ) -> None:
        """Mark a stage as done with a short summary."""
        for sink in self._sinks:
            sink.on_stage_done(stage, summary, warning=warning)

    def on_title(self, title: str) -> None:
        """Report the source title once known."""
        for sink in self._sinks:
            sink.on_title(title)

    def on_plan(self, plan: RenderPlan) -> None:
        """Record header facts and the chunk chapter map."""
        for sink in self._sinks:
            sink.on_plan(plan)

    def on_chunk_started(self, index: int, total: int) -> None:
        """Record a chunk request that really began."""
        for sink in self._sinks:
            sink.on_chunk_started(index, total)

    def on_chunk_finished(self, index: int, total: int, *, cached: bool) -> None:
        """Record a chunk with audio (cached or synthesized)."""
        for sink in self._sinks:
            sink.on_chunk_finished(index, total, cached=cached)

    def on_chunk_retried(self, index: int, attempt: int, reason: str) -> None:
        """Note a gate re-synthesis."""
        for sink in self._sinks:
            sink.on_chunk_retried(index, attempt, reason)

    def on_retry_wait(self, stage: str, chunk: int | None, wait: RetryWait) -> None:
        """Record a pending backoff sleep."""
        for sink in self._sinks:
            sink.on_retry_wait(stage, chunk, wait)

    def on_done(self, result: RenderResult) -> None:
        """Record completion."""
        for sink in self._sinks:
            sink.on_done(result)


def _stage_label(stage: str, source: str) -> str:
    from sase_listen.cli.progress import STAGE_LABELS, source_stage_label

    if stage == Stage.SOURCE.value:
        return source_stage_label(source)
    return STAGE_LABELS.get(stage, stage)


def _print_plan(plan: RenderPlan) -> None:
    """Print the dry-run plan as a Rich layout."""
    console = Console(highlight=False, emoji=False, markup=False)
    console.print(f"{GLYPH_AUDIO} {plan.title}", style="bold")
    narrator = plan.narrator
    facts = (
        f"{plan.source_label} \u00b7 {plan.edition} edition \u00b7 "
        f"{narrator.voice or narrator.name} \u00b7 {narrator.model}"
    )
    console.print(f"  {facts}", style="dim")
    console.print("")
    words_min = plan.estimated_duration_s / 60
    console.print(
        f"Facts: {plan.words:,} words \u00b7 about {words_min:.1f} min \u00b7 "
        f"{len(plan.chunks)} chunks "
        f"({plan.cached_chunks} cached, {plan.synthesis_needed} to synthesize) \u00b7 "
        f"about ${plan.estimated_cost_usd:.4f}"
    )
    writer_usage = plan.writer.get("tokens", {})
    if plan.writer and isinstance(writer_usage, dict):
        console.print(
            f"Writer usage: {writer_usage.get('input', 0)} input + "
            f"{writer_usage.get('output', 0)} output tokens; billed separately."
        )
    if plan.script_path:
        console.print(f"Script: {plan.script_path}")
    console.print("Chapters:")
    table = Table(box=None, show_header=True, pad_edge=False)
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Title", overflow="ellipsis")
    table.add_column("Words", justify="right", no_wrap=True)
    table.add_column("~min", justify="right", no_wrap=True)
    table.add_column("Chunks", justify="right", no_wrap=True)
    for number, chapter in enumerate(plan.chapter_plans, start=1):
        table.add_row(
            str(number),
            Text(chapter.title),
            str(chapter.words),
            f"{chapter.words / 150:.1f}",
            str(chapter.chunks),
        )
    console.print(table)
    if plan.omissions:
        console.print(f"Omissions ({len(plan.omissions)}):")
        for omission in plan.omissions:
            console.print(
                f"  - {omission.type}: {omission.detail} (line {omission.line})"
            )
    if plan.warnings:
        console.print("Warnings:")
        for warning in plan.warnings:
            console.print(f"  ! {warning}")


def _is_remote_publish() -> bool:
    """Return True when the feed role is remote (best effort)."""
    try:
        from sase_listen.config import load_config
        from sase_listen.feedhost import feed_role

        cfg, _ = load_config()
    except Exception:
        return False
    try:
        return feed_role(cfg) == "remote"
    except Exception:
        return False


def _print_final_frame(state: ProgressState, now: float) -> None:
    """Print the finished checklist to stdout (plain/off modes)."""
    from dataclasses import replace

    console = Console(highlight=False, emoji=False, markup=False)
    snapshot = replace(state.snapshot(now), final=True)
    width = console.width or 80
    console.print(build_view(snapshot, now, width))


def _print_result(
    result: RenderResult, *, live: bool, elapsed_s: float, output: str = ""
) -> None:
    """Print the finished render summary to stdout."""
    console = Console(highlight=False, emoji=False, markup=False)
    audio_min = result.duration_s / 60
    if audio_min < 1:
        audio_text = f"{result.duration_s:.0f}s of audio"
    else:
        audio_text = f"{format_duration(result.duration_s)} of audio"
    parts = [f"Ready in {format_duration(elapsed_s)}", audio_text]
    if result.cost_usd_estimate > 0:
        parts.append(approx_cost(result.cost_usd_estimate))
    head = Text()
    head.append(f"{GLYPH_AUDIO} {' \u00b7 '.join(parts)}", style="bold green")
    if not live:
        head.append(f" \u2014 {result.title}", style="bold green")
    console.print(head)
    console.print(f"  {result.audio_path}", soft_wrap=True)
    if result.published:
        if result.publish_host and _is_remote_publish():
            console.print(
                f"  Published to {result.publish_host} \u2014 refresh the feed "
                "in AntennaPod to download it."
            )
        else:
            console.print("  Published to the local feed.")
    elif result.publish_queued:
        pending = invocation.command("publish", "--pending")
        console.print(
            f"  \u26a0 Publish queued \u2014 run {pending}",
            style="yellow",
        )
    if output:
        console.print(f"  Copied to {output}")
    warnings = [
        warning for warning in result.warnings if "queued \u2014" not in warning
    ]
    if warnings:
        noun = "warning" if len(warnings) == 1 else "warnings"
        console.print(f"  \u26a0 {len(warnings)} {noun}", style="yellow")
        for warning in warnings:
            console.print(f"    \u00b7 {warning}")


def _failure_lines(
    message: str,
    hint: str,
    state: ProgressState,
    now: float,
    source: str,
    code: int,
    *,
    kind: str,
) -> list[str]:
    """Build the stderr failure or interrupt block lines."""
    snapshot = state.snapshot(now)
    stage = snapshot.active_stage
    if not stage:
        for row in reversed(snapshot.rows):
            if row.status in ("failed", "interrupted", "active", "done", "warning"):
                stage = row.id
                break
    lines: list[str] = []
    if kind == "interrupt":
        head = f"\u25a0 {invocation.command('render')} interrupted"
    else:
        head = f"\u2717 {invocation.command('render')} failed"
    if stage:
        head += f" during {_stage_label(stage, source)}"
    head += f" (exit {code})"
    lines.append(head)
    first = message.splitlines()[0] if message.strip() else ""
    if first and kind != "interrupt":
        lines.append(f"  {first}")
    cache_line = _cache_line(snapshot, kind=kind)
    for hint_line in _hint_lines(kind, stage, snapshot, has_cache=bool(cache_line)):
        lines.append(f"  {hint_line}")
    if hint and kind != "interrupt":
        lines.append(f"  hint: {hint}")
    if cache_line:
        lines.append(f"  {cache_line}")
    return lines


def _hint_lines(
    kind: str, stage: str, snapshot: Snapshot, *, has_cache: bool
) -> list[str]:
    if kind != "interrupt":
        return []
    if stage in (Stage.SYNTHESIZE.value, Stage.GATES.value):
        if has_cache:
            return []
        return ["Nothing was rendered yet; re-run to start again."]
    if stage == Stage.MASTER.value or stage == Stage.SAVE.value:
        return ["All chunks are cached; re-run to finish without new synthesis."]
    if stage == Stage.PUBLISH.value:
        episode = snapshot.episode_id
        if episode:
            return [
                "The episode is saved in your library; publish it with "
                f"`{invocation.command('publish', episode)}`."
            ]
        return ["The episode is saved in your library; publish it when ready."]
    if stage == Stage.WRITE.value:
        return [
            "Nothing was rendered yet; re-run to start again. "
            "The fetched article is cached."
        ]
    return ["Nothing was rendered yet; re-run to start again."]


def _cache_line(snapshot: Snapshot, *, kind: str) -> str:
    at_or_after_synth = any(
        row.id == Stage.SYNTHESIZE.value and row.status != "pending"
        for row in snapshot.rows
    )
    if not at_or_after_synth or snapshot.cached < 1 or snapshot.total < 1:
        return ""
    if kind == "interrupt":
        return (
            f"{snapshot.cached} of {snapshot.total} chunks are cached \u2014 "
            "re-run the same command to resume without paying for them again."
        )
    return (
        f"{snapshot.cached} of {snapshot.total} chunks are cached, "
        "so re-running will not synthesize them again."
    )


def run(args: argparse.Namespace) -> int:
    """Render a source into an episode (live checklist or one JSON object)."""
    request = RenderRequest(
        source=args.source,
        output=args.output or "",
        narrator=args.narrator or "",
        voice_override=args.voice or "",
        cover=args.cover or "",
        dry_run=bool(args.dry_run),
        publish=args.publish,
        no_cache=bool(args.no_cache),
        force=bool(args.force),
        edition=args.edition,
        html=args.html or "",
        refresh=bool(args.refresh),
        generated_cover=bool(getattr(args, "generated_cover", False)),
    )
    as_json = bool(args.json)
    started = time.monotonic()
    if as_json:
        try:
            outcome = render(request)
        except SaseListenError as exc:
            print(json.dumps(error_to_json(exc)))
            return int(exc.code)
        except ValueError as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {"code": 3, "message": str(exc), "hint": ""},
                    }
                )
            )
            return int(ExitCode.CONFIG)
        except KeyboardInterrupt:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": 130,
                            "message": "Interrupted.",
                            "hint": (
                                "Re-run the same command; finished chunks are cached."
                            ),
                        },
                    }
                )
            )
            return int(ExitCode.INTERRUPTED)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": 1,
                            "message": f"Unexpected failure: {exc}",
                            "hint": "Re-run; a killed render resumes from the cache.",
                        },
                    }
                )
            )
            return int(ExitCode.UNEXPECTED)
        if isinstance(outcome, RenderPlan):
            print(json.dumps(plan_to_json(outcome)))
        else:
            print(json.dumps(result_to_json(outcome)))
        return int(ExitCode.OK)

    probe = Console(stderr=True, highlight=False, emoji=False, markup=False)
    mode = resolve_mode(str(args.progress or "auto"), as_json=False, console=probe)
    display = build_progress(mode, request.source)
    state = ProgressState(request.source)
    sinks: list[RenderEvents] = [state]
    if display is not None:
        sinks.append(display)
    events: RenderEvents = _Fanout(sinks)

    def _force_quit() -> None:
        if display is not None:
            display.force_stop()
        print(
            "\u25a0 Stopped immediately; in-flight chunks were not cached.",
            file=sys.stderr,
        )

    try:
        with interrupt_guard(_force_quit):
            if isinstance(display, LiveProgress):
                with display:
                    outcome = render(request, events=events)
            else:
                outcome = render(request, events=events)
    except KeyboardInterrupt:
        elapsed = time.monotonic() - started
        for line in _failure_lines(
            "",
            "",
            state,
            started + elapsed,
            request.source,
            int(ExitCode.INTERRUPTED),
            kind="interrupt",
        ):
            print(line, file=sys.stderr)
        return int(ExitCode.INTERRUPTED)
    except SaseListenError as exc:
        elapsed = time.monotonic() - started
        for line in _failure_lines(
            str(exc),
            exc.hint,
            state,
            started + elapsed,
            request.source,
            int(exc.code),
            kind="fail",
        ):
            print(line, file=sys.stderr)
        return int(exc.code)
    except ValueError as exc:
        elapsed = time.monotonic() - started
        for line in _failure_lines(
            str(exc),
            "",
            state,
            started + elapsed,
            request.source,
            int(ExitCode.CONFIG),
            kind="fail",
        ):
            print(line, file=sys.stderr)
        return int(ExitCode.CONFIG)
    except Exception as exc:
        elapsed = time.monotonic() - started
        for line in _failure_lines(
            f"Unexpected failure: {exc}",
            "Re-run; a killed render resumes from the cache.",
            state,
            started + elapsed,
            request.source,
            int(ExitCode.UNEXPECTED),
            kind="fail",
        ):
            print(line, file=sys.stderr)
        return int(ExitCode.UNEXPECTED)
    elapsed = time.monotonic() - started
    if isinstance(outcome, RenderPlan):
        _print_plan(outcome)
        return int(ExitCode.OK)
    if mode != "live":
        _print_final_frame(state, started + elapsed)
    _print_result(
        outcome, live=(mode == "live"), elapsed_s=elapsed, output=request.output
    )
    return int(ExitCode.OK)
