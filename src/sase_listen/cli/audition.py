"""audition command. Owner: cli phase."""

from __future__ import annotations

import argparse

from sase_listen.errors import ExitCode


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the audition parser."""
    p = sub.add_parser("audition", help="Render the sample passage per voice.")
    p.add_argument("--voices", default="", help="Comma-separated voices.")
    p.add_argument(
        "-n", "--narrator", action="append", default=[], help="Narrator profile."
    )
    p.add_argument("--text", default="", help="Custom passage file.")
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen audition --voices Charon,Kore"
    return p


def run(args: argparse.Namespace) -> int:
    """Audition stub (cli phase implements)."""
    print("sase-listen audition is not implemented yet (owner: cli phase).")
    return int(ExitCode.UNEXPECTED)
