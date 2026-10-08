"""audition command. Owner: cli phase."""

from __future__ import annotations

import argparse

from sase_listen import invocation
from sase_listen.errors import ExitCode


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    prog: str = "sase-listen",
) -> argparse.ArgumentParser:
    """Register the audition parser."""
    p = sub.add_parser("audition", help="Render the sample passage per voice.")
    p.add_argument("--voices", default="", help="Comma-separated voices.")
    p.add_argument(
        "-n", "--narrator", action="append", default=[], help="Narrator profile."
    )
    text_action = p.add_argument("--text", default="", help="Custom passage file.")
    invocation.mark_path_completion(text_action)
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = f"Example: {prog} audition --voices Charon,Kore"
    return p


def run(args: argparse.Namespace) -> int:
    """Audition stub (cli phase implements)."""
    print(
        f"{invocation.command('audition')} is not implemented yet (owner: cli phase)."
    )
    return int(ExitCode.UNEXPECTED)
