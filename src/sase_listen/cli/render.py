"""render command: Markdown to MP3 episode. Owner: pipeline phase (UX: cli phase).

Human output stays plain here; the cli phase owns the rich experience and
renders progress from the pipeline's event protocol without touching the
orchestration.
"""

from __future__ import annotations

import argparse
import json
import sys

from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.pipeline import (
    RenderEvents,
    RenderPlan,
    RenderRequest,
    RenderResult,
    error_to_json,
    plan_to_json,
    render,
    result_to_json,
)


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the render parser."""
    p = sub.add_parser("render", help="Render a source into an MP3 episode.")
    p.add_argument(
        "source",
        help="Narration script, Markdown file, kind:path ref, or http(s) URL",
    )
    cover_group = p.add_mutually_exclusive_group()
    cover_group.add_argument("--cover", default="", help="Cover image path.")
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
        help="Article edition to render (URL default: brief).",
    )
    p.add_argument(
        "--force", action="store_true", help="Render despite structural lint errors."
    )
    p.add_argument(
        "-H",
        "--html",
        default="",
        metavar="FILE",
        help="Use saved browser HTML instead of fetching the URL.",
    )
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.add_argument("-n", "--narrator", default="", help="Narrator profile name.")
    p.add_argument("--no-cache", action="store_true", help="Bypass the chunk cache.")
    pub = p.add_mutually_exclusive_group()
    pub.add_argument("--no-publish", dest="publish", action="store_false")
    p.add_argument("-o", "--output", default="", help="Copy the MP3 to PATH.")
    pub.add_argument("--publish", dest="publish", action="store_true", default=None)
    pub.set_defaults(publish=None)
    p.add_argument(
        "-r",
        "--refresh",
        action="store_true",
        help="Fetch the URL again and replace the cached source.",
    )
    p.add_argument("--voice", default="", help="Override the narrator voice.")
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen render https://example.com/article -e full"
    return p


class _ProgressEvents(RenderEvents):
    """Plain human progress lines for the pre-cli-phase experience."""

    def on_chunk_finished(self, index: int, total: int, *, cached: bool) -> None:
        """Report each finished chunk to stderr."""
        tag = "cached" if cached else "synthesized"
        print(f"[{index + 1}/{total}] chunk {index} {tag}", file=sys.stderr)

    def on_chunk_retried(self, index: int, attempt: int, reason: str) -> None:
        """Report each retry to stderr."""
        print(f"chunk {index}: retry {attempt} ({reason})", file=sys.stderr)

    def on_stage(self, stage: str) -> None:
        """Report stage changes to stderr."""
        print(f"stage: {stage}", file=sys.stderr)


def _print_plan(plan: RenderPlan) -> None:
    """Print the dry-run plan as plain human text."""
    print(f"Title: {plan.title}")
    print(f"Source: {plan.source_label}")
    narrator = plan.narrator
    print(
        f"Narrator: {narrator.name} "
        f"({narrator.engine} / {narrator.model or '-'} / {narrator.voice or '-'})"
    )
    print(f"Edition: {plan.edition}  Producer: {plan.producer}")
    print(f"Words: {plan.words}  About: {plan.estimated_duration_s / 60:.1f} min")
    writer_usage = plan.writer.get("tokens", {})
    if plan.writer and isinstance(writer_usage, dict):
        print(
            f"Writer usage: {writer_usage.get('input', 0)} input + "
            f"{writer_usage.get('output', 0)} output tokens; billed separately."
        )
    print(
        f"Chunks: {len(plan.chunks)} "
        f"({plan.cached_chunks} cached, {plan.synthesis_needed} to synthesize)"
    )
    print(f"Cost: about ${plan.estimated_cost_usd:.4f}")
    print("Chapters:")
    for number, chapter in enumerate(plan.chapter_plans, start=1):
        print(
            f"  {number}. {chapter.title} "
            f"({chapter.words} words, about {chapter.words / 150:.1f} min, "
            f"{chapter.chunks} chunks)"
        )
    if plan.omissions:
        print(f"Omissions ({len(plan.omissions)}):")
        for omission in plan.omissions:
            print(f"  - {omission.type}: {omission.detail} (line {omission.line})")
    if plan.warnings:
        print("Warnings:")
        for warning in plan.warnings:
            print(f"  ! {warning}")


def _print_result(result: RenderResult) -> None:
    """Print the finished render as plain human text."""
    print(f"Done: {result.title}")
    print(f"MP3: {result.audio_path}")
    minutes = result.duration_s / 60
    print(
        f"Duration: {result.duration_s:.1f} s ({minutes:.1f} min)  "
        f"Size: {result.size_bytes} bytes  "
        f"Chapters: {len(result.chapters)}  "
        f"LUFS: {result.loudness_lufs:.1f}"
    )
    print(
        f"Chunks: {result.total_chunks} "
        f"({result.cached_chunks} cached, "
        f"{result.synthesized_chunks} synthesized, "
        f"{result.retried_chunks} retried)"
    )
    print(f"Cost: about ${result.cost_usd_estimate:.4f}")
    if result.published:
        print("Published to feed.")
    for warning in result.warnings:
        print(f"! {warning}")


def run(args: argparse.Namespace) -> int:
    """Render a source into an episode (plain output or one JSON object)."""
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
    events = None if as_json else _ProgressEvents()
    try:
        outcome = render(request, events=events)
    except SaseListenError as exc:
        if as_json:
            print(json.dumps(error_to_json(exc)))
        else:
            print(f"sase-listen render: error: {exc}", file=sys.stderr)
            if exc.hint:
                print(f"hint: {exc.hint}", file=sys.stderr)
        return int(exc.code)
    except ValueError as exc:
        # Config validation surfaces as ValueError; that is exit 3.
        message = f"sase-listen render: error: {exc}"
        if as_json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {"code": 3, "message": str(exc), "hint": ""},
                    }
                )
            )
        else:
            print(message, file=sys.stderr)
        return int(ExitCode.CONFIG)
    except Exception as exc:
        if as_json:
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
        else:
            print(f"sase-listen render: unexpected error: {exc}", file=sys.stderr)
        return int(ExitCode.UNEXPECTED)
    if isinstance(outcome, RenderPlan):
        if as_json:
            print(json.dumps(plan_to_json(outcome)))
        else:
            _print_plan(outcome)
        return int(ExitCode.OK)
    if as_json:
        print(json.dumps(result_to_json(outcome)))
    else:
        _print_result(outcome)
    return int(ExitCode.OK)
