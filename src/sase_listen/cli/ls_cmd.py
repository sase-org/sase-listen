"""ls command. Owner: cli phase."""

from __future__ import annotations

import argparse

from sase_listen.errors import ExitCode


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    prog: str = "sase-listen",
) -> argparse.ArgumentParser:
    """Register the ls parser."""
    p = sub.add_parser("ls", help="List episodes or show one episode.")
    p.add_argument("episode", nargs="?", default="", help="Episode id or path.")
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = f"Example: {prog} ls"
    return p


def run(args: argparse.Namespace) -> int:
    """Ls stub (cli phase implements)."""
    from sase_listen import invocation

    print(f"{invocation.command('ls')} is not implemented yet (owner: cli phase).")
    return int(ExitCode.UNEXPECTED)
