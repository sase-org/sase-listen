"""lint command. Owner: script phase."""

from __future__ import annotations

import argparse

from sase_listen.errors import ExitCode


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the lint parser."""
    p = sub.add_parser("lint", help="Validate a narration script.")
    p.add_argument("script", help="Narration script to check.")
    p.add_argument("--source", default="", help="Original report for number fidelity.")
    p.add_argument("--strict", action="store_true", help="Treat warnings as errors.")
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen lint episode_narration.md --source report.md"
    return p


def run(args: argparse.Namespace) -> int:
    """Lint stub (script phase implements)."""
    print("sase-listen lint is not implemented yet (owner: script phase).")
    return int(ExitCode.UNEXPECTED)
