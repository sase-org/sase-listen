"""render command. Owner: pipeline phase (UX: cli phase)."""

from __future__ import annotations

import argparse

from sase_listen.errors import ExitCode


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the render parser."""
    p = sub.add_parser("render", help="Render Markdown into an MP3 episode.")
    p.add_argument("source", help="Narration script, Markdown file, or kind:path ref.")
    p.add_argument("-o", "--output", default="", help="Copy the MP3 to PATH.")
    p.add_argument("-n", "--narrator", default="", help="Narrator profile name.")
    p.add_argument("--voice", default="", help="Override the narrator voice.")
    p.add_argument("--cover", default="", help="Cover image path.")
    p.add_argument("--dry-run", action="store_true", help="Print the plan and stop.")
    pub = p.add_mutually_exclusive_group()
    pub.add_argument("--publish", dest="publish", action="store_true", default=None)
    pub.add_argument("--no-publish", dest="publish", action="store_false")
    p.add_argument("--no-cache", action="store_true", help="Bypass the chunk cache.")
    p.add_argument(
        "--force", action="store_true", help="Render despite structural lint errors."
    )
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen render notes.md -o episode.mp3"
    return p


def run(args: argparse.Namespace) -> int:
    """Render stub (pipeline phase implements)."""
    print("sase-listen render is not implemented yet (owner: pipeline phase).")
    return int(ExitCode.UNEXPECTED)
