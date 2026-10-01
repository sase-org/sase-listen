"""guide command. Owner: script phase."""

from __future__ import annotations

import argparse

from sase_listen.errors import ExitCode


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the guide parser."""
    p = sub.add_parser("guide", help="Print the packaged authoring guide.")
    p.add_argument(
        "--edition",
        default="full",
        choices=["full", "brief"],
        help="Edition guide to print.",
    )
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen guide --edition full"
    return p


def run(args: argparse.Namespace) -> int:
    """Guide stub (script phase implements)."""
    print("sase-listen guide is not implemented yet (owner: script phase).")
    return int(ExitCode.UNEXPECTED)
