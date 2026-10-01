"""publish/unpublish commands. Owner: feed phase."""

from __future__ import annotations

import argparse

from sase_listen.errors import ExitCode


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register publish and unpublish parsers."""
    p = sub.add_parser("publish", help="Publish an episode to the feed.")
    p.add_argument(
        "episode", nargs="?", default="", help="Episode id, MP3 path, or --latest."
    )
    p.add_argument("--latest", action="store_true", help="Publish the newest episode.")
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run_publish)
    u = sub.add_parser("unpublish", help="Remove an episode from the feed.")
    u.add_argument("episode", help="Episode id.")
    u.add_argument("--json", action="store_true", help="Emit one JSON object.")
    u.set_defaults(func=run_unpublish)
    return p


def run_publish(args: argparse.Namespace) -> int:
    """Publish stub (feed phase implements)."""
    print("sase-listen publish is not implemented yet (owner: feed phase).")
    return int(ExitCode.UNEXPECTED)


def run_unpublish(args: argparse.Namespace) -> int:
    """Unpublish stub (feed phase implements)."""
    print("sase-listen unpublish is not implemented yet (owner: feed phase).")
    return int(ExitCode.UNEXPECTED)
