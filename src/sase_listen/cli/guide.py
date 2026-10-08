"""guide command: print the packaged authoring guide. Owner: script phase."""

from __future__ import annotations

import argparse
import re
import sys
from importlib.resources import files

from sase_listen.errors import ExitCode

_FULL_BEGIN = "<!-- edition:full-begin -->"
_FULL_END = "<!-- edition:full-end -->"
_BRIEF_BEGIN = "<!-- edition:brief-begin -->"
_BRIEF_END = "<!-- edition:brief-end -->"


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    prog: str = "sase-listen",
) -> argparse.ArgumentParser:
    """Register the guide parser."""
    p = sub.add_parser("guide", help="Print the packaged authoring guide.")
    p.add_argument(
        "--edition",
        default="brief",
        choices=["full", "brief"],
        help="Edition guide to print (default: brief).",
    )
    p.set_defaults(func=run)
    p.epilog = f"Example: {prog} guide --edition brief"
    return p


_EDITION_MINUTES = {"brief": "4", "full": "16"}


def render_guide(edition: str = "brief") -> str:
    """Render the packaged guide for one edition."""
    from sase_listen import invocation

    text = (files("sase_listen") / "data" / "guide.md").read_text(encoding="utf-8")
    text = text.replace("{{ edition }}", edition)
    text = text.replace("{{ prog }}", invocation.display_prog())
    text = text.replace("{{ target_minutes }}", _EDITION_MINUTES.get(edition, "4"))
    if edition == "brief":
        text = re.sub(
            f"{re.escape(_FULL_BEGIN)}.*?{re.escape(_FULL_END)}",
            "",
            text,
            flags=re.DOTALL,
        )
        text = text.replace(_BRIEF_BEGIN, "").replace(_BRIEF_END, "")
    else:
        text = re.sub(
            f"{re.escape(_BRIEF_BEGIN)}.*?{re.escape(_BRIEF_END)}",
            "",
            text,
            flags=re.DOTALL,
        )
        text = text.replace(_FULL_BEGIN, "").replace(_FULL_END, "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return text


def run(args: argparse.Namespace) -> int:
    """Print the packaged authoring guide."""
    try:
        sys.stdout.write(render_guide(str(args.edition)))
    except FileNotFoundError:
        from sase_listen import invocation

        print(
            f"{invocation.command('guide')}: packaged guide.md is missing.",
            file=sys.stderr,
        )
        return int(ExitCode.UNEXPECTED)
    return int(ExitCode.OK)
