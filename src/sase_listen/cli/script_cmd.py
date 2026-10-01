"""script command. Owner: script phase."""

from __future__ import annotations

import argparse

from sase_listen.errors import ExitCode


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
    """Script stub (script phase implements)."""
    print("sase-listen script is not implemented yet (owner: script phase).")
    return int(ExitCode.UNEXPECTED)
