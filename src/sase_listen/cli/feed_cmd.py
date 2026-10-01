"""feed command. Owner: feed phase."""

from __future__ import annotations

import argparse

from sase_listen.errors import ExitCode


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the feed parser."""
    p = sub.add_parser("feed", help="Manage the private podcast feed.")
    p.add_argument(
        "action", nargs="?", default="", choices=["", "init", "rebuild", "prune"]
    )
    p.add_argument("--base-url", default="", help="Public base URL for feed init.")
    p.add_argument("--qr", action="store_true", help="Print a terminal QR code.")
    p.add_argument("--show-url", action="store_true", help="Show the unmasked URL.")
    p.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Print config instead of writing.",
    )
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen feed init --base-url https://host:8443"
    return p


def run(args: argparse.Namespace) -> int:
    """Feed stub (feed phase implements)."""
    print("sase-listen feed is not implemented yet (owner: feed phase).")
    return int(ExitCode.UNEXPECTED)
