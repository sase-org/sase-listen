"""script command: deterministic Markdown to narration script. Owner: script phase."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from sase_listen.errors import ExitCode
from sase_listen.normalize import normalize_markdown
from sase_listen.script import parse_script_text


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the script parser."""
    p = sub.add_parser("script", help="Convert Markdown to a narration script.")
    p.add_argument("source", help="Markdown file to normalize.")
    p.add_argument("-o", "--output", default="", help="Write the script to PATH.")
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen script notes.md -o notes_narration.md"
    return p


def run(args: argparse.Namespace) -> int:
    """Normalize Markdown into an edition: verbatim narration script."""
    source_path = Path(args.source)
    if not source_path.exists():
        print(f"sase-listen script: file not found: {source_path}", file=sys.stderr)
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
