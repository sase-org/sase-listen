"""``sase_commands`` adapter: mount ``sase listen`` in the sase CLI.

This module is the ``listen = "sase_listen.sase_command"`` entry point. It
imports only the standard library at module top and never imports ``sase``:
sase calls ``main(argv, prog="sase listen")`` to run the command and
``build_parser(prog="sase listen")`` for completion. Both delegate to the
same code paths as the standalone ``sase-listen`` console script, so the
stale-environment guard (exit 3 with a repair hint) covers both names.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

SASE_COMMAND_API = 1
SUMMARY = "Turn Markdown into chaptered MP3 audio editions"


def build_parser(prog: str) -> argparse.ArgumentParser:
    """Build the full listen parser (completion only; never for dispatch)."""
    from sase_listen.cli import app as app_mod

    return app_mod.build_parser(prog=prog)


def main(argv: Sequence[str], prog: str) -> int | None:
    """Run the listen command on the argv after the command word."""
    from sase_listen import cli as cli_mod

    return int(cli_mod.main(argv, prog=prog))
