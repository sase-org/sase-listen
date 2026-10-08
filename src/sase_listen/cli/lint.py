"""lint command. Owner: script phase."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sase_listen import invocation
from sase_listen.errors import ExitCode

if TYPE_CHECKING:
    from sase_listen.script import Finding


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    prog: str = "sase-listen",
) -> argparse.ArgumentParser:
    """Register the lint parser."""
    p = sub.add_parser("lint", help="Validate a narration script.")
    script_action = p.add_argument("script", help="Narration script to check.")
    invocation.mark_path_completion(script_action)
    source_action = p.add_argument(
        "--source", default="", help="Original report for number fidelity."
    )
    invocation.mark_path_completion(source_action)
    p.add_argument("--strict", action="store_true", help="Treat warnings as errors.")
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = f"Example: {prog} lint episode_narration.md --source report.md"
    return p


def _failed(findings: list[Finding], strict: bool) -> bool:
    if any(f.severity == "error" for f in findings):
        return True
    return bool(strict and any(f.severity == "warning" for f in findings))


def run(args: argparse.Namespace) -> int:
    """Validate a script against the contract and listenability rules."""
    from rich.console import Console
    from rich.table import Table

    from sase_listen.script import lint_file

    prefix = invocation.command("lint")
    script_path = Path(args.script)
    if not script_path.exists():
        print(f"{prefix}: file not found: {script_path}", file=sys.stderr)
        return int(ExitCode.USAGE)
    source_path = Path(args.source) if args.source else None
    if source_path is not None and not source_path.exists():
        print(f"{prefix}: source not found: {source_path}", file=sys.stderr)
        return int(ExitCode.USAGE)
    findings, _text = lint_file(script_path, source_path)
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    failed = _failed(findings, bool(args.strict))
    if args.json:
        payload = {
            "ok": not failed,
            "path": str(script_path),
            "errors": errors,
            "warnings": warnings,
            "findings": [f.to_dict() for f in findings],
        }
        print(json.dumps(payload))
        return int(ExitCode.UNEXPECTED) if failed else int(ExitCode.OK)
    console = Console()
    if not findings:
        console.print("[green]✓[/green] Script is clean.")
        return int(ExitCode.OK)
    for severity in ("error", "warning"):
        group = [f for f in findings if f.severity == severity]
        if not group:
            continue
        table = Table(title=f"{severity}s ({len(group)})", show_lines=False)
        table.add_column("Location", style="cyan", no_wrap=True)
        table.add_column("Rule", style="magenta", no_wrap=True)
        table.add_column("Message")
        table.add_column("Fix hint", style="dim")
        for finding in group:
            table.add_row(
                f"{finding.line}:{finding.col}",
                finding.rule,
                finding.message,
                finding.hint,
            )
        console.print(table)
    summary = f"{errors} error(s), {warnings} warning(s)"
    if failed:
        console.print(f"[red]✗[/red] {summary}")
        return int(ExitCode.UNEXPECTED)
    console.print(f"[yellow]⚠[/yellow] {summary} (passing without --strict)")
    return int(ExitCode.OK)
