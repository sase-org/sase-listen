"""cache command. Owner: cli phase."""

from __future__ import annotations

import argparse

from sase_listen.errors import ExitCode


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the cache parser."""
    p = sub.add_parser("cache", help="Show cache stats and prune.")
    p.add_argument(
        "action", nargs="?", default="", choices=["", "prune"], help="Cache action."
    )
    p.add_argument("--all", action="store_true", help="Prune everything.")
    p.add_argument(
        "--older-than", default="", help="Prune entries older than DURATION (e.g. 30d)."
    )
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen cache prune --older-than 30d"
    return p


def run(args: argparse.Namespace) -> int:
    """Cache stub (cli phase implements)."""
    print("sase-listen cache is not implemented yet (owner: cli phase).")
    return int(ExitCode.UNEXPECTED)
