"""Argparse registry with one module per command. Owner: scaffold phase."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sase_listen.cli import audition as audition_mod
from sase_listen.cli import cache_cmd as cache_mod
from sase_listen.cli import config_cmd as config_mod
from sase_listen.cli import doctor as doctor_mod
from sase_listen.cli import feed_cmd as feed_mod
from sase_listen.cli import guide as guide_mod
from sase_listen.cli import lint as lint_mod
from sase_listen.cli import ls_cmd as ls_mod
from sase_listen.cli import publish_cmd as publish_mod
from sase_listen.cli import render as render_mod
from sase_listen.cli import script_cmd as script_mod
from sase_listen.errors import ExitCode

_Formatter: type[argparse.HelpFormatter]
try:
    from rich_argparse import RichHelpFormatter as _RichImpl

    _Formatter = _RichImpl
except ImportError:  # pragma: no cover - fallback when rich-argparse is missing
    _Formatter = argparse.HelpFormatter

_COMMAND_MODULES = (
    render_mod,
    script_mod,
    lint_mod,
    guide_mod,
    audition_mod,
    ls_mod,
    doctor_mod,
    cache_mod,
    config_mod,
    feed_mod,
    publish_mod,
)


class _VersionAction(argparse.Action):
    """Print the precise build and exit (computed only on --version)."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        from sase_listen.buildinfo import current

        print(f"sase-listen {current().display()}")
        parser.exit()


def build_parser(prog: str = "sase-listen") -> argparse.ArgumentParser:
    """Build the top-level parser (side-effect free)."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Turn Markdown into chaptered, loudness-normalized MP3 audio editions.\n"
            "Render research reports for the commute, then listen in Telegram or a\n"
            "private podcast feed."
        ),
        formatter_class=_Formatter,
    )
    parser.add_argument("--version", action=_VersionAction, nargs=0)
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for mod in _COMMAND_MODULES:
        mod.add_parser(sub, prog=prog)
    return parser


def main(argv: Sequence[str] | None = None, *, prog: str = "sase-listen") -> int:
    """Entry point for the `sase-listen` console script and `sase listen`."""
    from sase_listen.invocation import set_display_prog

    set_display_prog(prog)
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return int(ExitCode.USAGE)
    try:
        func = getattr(args, "func", None)
        if func is None:
            parser.print_help()
            return int(ExitCode.USAGE)
        return int(func(args))
    except BrokenPipeError:
        return int(ExitCode.UNEXPECTED)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
