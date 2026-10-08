"""script command: deterministic Markdown to narration script. Owner: script phase."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sase_listen import invocation
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.events import RenderEvents

if TYPE_CHECKING:
    from sase_listen.pipeline import LoadedSource


class _Interrupted(Exception):
    """Internal control flow for script interrupts (maps to exit 130)."""


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    prog: str = "sase-listen",
) -> argparse.ArgumentParser:
    """Register the script parser."""
    p = sub.add_parser("script", help="Create a deterministic narration script.")
    source_action = p.add_argument(
        "source",
        help="Markdown file, PDF file, or http(s) article URL to normalize.",
    )
    invocation.mark_path_completion(source_action)
    p.add_argument(
        "-e",
        "--edition",
        choices=("brief", "full", "verbatim"),
        default=None,
        help="Article edition to create (URL default: brief).",
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
    output_action = p.add_argument(
        "-o", "--output", default="", help="Write the script to PATH."
    )
    invocation.mark_path_completion(output_action)
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
        help="Fetch the URL again and replace the cached source.",
    )
    p.set_defaults(func=run)
    p.epilog = f"Example: {prog} script https://example.com/article -e brief --json"
    return p


def _fetch_source(
    args: argparse.Namespace, events: RenderEvents | None
) -> LoadedSource:
    """Fetch the URL source, announcing the expected source stages first."""
    from sase_listen.pipeline import load_source, source_stages

    if events is not None:
        events.on_stages(
            [stage.value for stage in source_stages(str(args.source), args.edition)]
        )
    return load_source(
        args.source,
        edition=args.edition,
        html_file=args.html or "",
        refresh=bool(args.refresh),
        events=events,
    )


def _load_url_source(args: argparse.Namespace) -> LoadedSource:
    """Load a URL source under the requested progress display."""
    from rich.console import Console

    from sase_listen.cli.progress import (
        LiveProgress,
        build_progress,
        interrupt_guard,
        resolve_mode,
    )

    as_json = bool(args.json)
    probe = Console(stderr=True, highlight=False, emoji=False, markup=False)
    mode = resolve_mode(
        str(getattr(args, "progress", "auto") or "auto"),
        as_json=as_json,
        console=probe,
    )
    display = None if as_json else build_progress(mode, str(args.source))

    def _force_quit() -> None:
        if display is not None:
            display.force_stop()
        print(
            "\u25a0 Stopped immediately.",
            file=sys.stderr,
        )

    try:
        with interrupt_guard(_force_quit):
            if isinstance(display, LiveProgress):
                with display:
                    return _fetch_source(args, display)
            return _fetch_source(args, display)
    except KeyboardInterrupt:
        if as_json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": 130,
                            "message": "Interrupted.",
                            "hint": "Re-run the same command to try again.",
                        },
                    }
                )
            )
        else:
            print(
                f"\u25a0 {invocation.command('script')} interrupted (exit 130)",
                file=sys.stderr,
            )
        raise _Interrupted from None


def run(args: argparse.Namespace) -> int:
    """Normalize Markdown into an edition: verbatim narration script."""
    from rich.console import Console
    from rich.table import Table

    from sase_listen.normalize import normalize_markdown
    from sase_listen.pipeline import looks_like_pdf_file, looks_like_url
    from sase_listen.script import parse_script_text

    if looks_like_url(args.source) or looks_like_pdf_file(args.source):
        try:
            loaded = _load_url_source(args)
        except _Interrupted:
            return int(ExitCode.INTERRUPTED)
        except SaseListenError as exc:
            if args.json:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": {
                                "code": int(exc.code),
                                "message": str(exc),
                                "hint": exc.hint,
                            },
                        }
                    )
                )
            else:
                print(f"{invocation.command('script')}: error: {exc}", file=sys.stderr)
                if exc.hint:
                    print(f"hint: {exc.hint}", file=sys.stderr)
            return int(exc.code)
        script_md = loaded.script_text
        script = loaded.script
        chapters = [
            {"title": c.title, "words": c.words(), "paragraphs": len(c.paragraphs)}
            for c in script.chapters
        ]
        output_path = str(args.output) if args.output else ""
        if output_path:
            Path(output_path).write_text(script_md, encoding="utf-8")
        outline = loaded.source_meta.get("outline", {})
        if not isinstance(outline, dict):
            outline = {}
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "title": script.meta.title,
                        "words": script.words(),
                        "chapters": chapters,
                        "omissions": [o.to_dict() for o in loaded.omissions],
                        "output_path": output_path,
                        "script": script_md,
                        "source_dir": str(
                            loaded.source_path.parent if loaded.source_path else ""
                        ),
                        "script_path": str(loaded.source_path or ""),
                        "writer": dict(loaded.writer),
                        "outline": {
                            "found": outline.get("found", 0),
                            "restored": outline.get("restored", []),
                            "missing": outline.get("missing", []),
                        },
                    }
                )
            )
            return int(ExitCode.OK)
        if not output_path:
            sys.stdout.write(script_md)
            return int(ExitCode.OK)
        Console().print(
            f"[green]✓[/green] Wrote {output_path} ({script.words()} words)."
        )
        return int(ExitCode.OK)

    if args.edition in {"brief", "full"}:
        print(
            f"{invocation.command('script')}: error: generated brief and full editions "
            "are available for article URLs and PDF files only.",
            file=sys.stderr,
        )
        print(
            "hint: Pass an http(s) article URL or PDF file, or use --edition verbatim.",
            file=sys.stderr,
        )
        return int(ExitCode.USAGE)

    source_path = Path(args.source)
    if not source_path.exists():
        print(
            f"{invocation.command('script')}: file not found: {source_path}",
            file=sys.stderr,
        )
        return int(ExitCode.USAGE)
    source_text = source_path.read_text(encoding="utf-8")
    script_md, omissions = normalize_markdown(source_text, filename=source_path.name)
    script = parse_script_text(script_md)
    chapters = [
        {"title": c.title, "words": c.words(), "paragraphs": len(c.paragraphs)}
        for c in script.chapters
    ]
    output_path = str(args.output) if args.output else ""
    if output_path:
        Path(output_path).write_text(script_md, encoding="utf-8")
    if args.json:
        payload = {
            "ok": True,
            "title": script.meta.title,
            "words": script.words(),
            "chapters": chapters,
            "omissions": [o.to_dict() for o in omissions],
            "output_path": output_path,
            "script": script_md,
        }
        print(json.dumps(payload))
        return int(ExitCode.OK)
    if not output_path:
        sys.stdout.write(script_md)
        if omissions:
            console = Console(stderr=True)
            console.print(
                f"[dim]{len(omissions)} omission(s) reported on stderr; "
                "use -o and --json for the full report.[/dim]"
            )
        return int(ExitCode.OK)
    console = Console()
    console.print(f"[green]✓[/green] Wrote {output_path} ({script.words()} words).")
    if omissions:
        table = Table(title=f"omissions ({len(omissions)})")
        table.add_column("Type", style="magenta", no_wrap=True)
        table.add_column("Detail")
        table.add_column("Line", style="cyan", no_wrap=True)
        for omission in omissions:
            table.add_row(omission.type, omission.detail, str(omission.line))
        console.print(table)
    else:
        console.print("[green]✓[/green] No omissions.")
    return int(ExitCode.OK)
