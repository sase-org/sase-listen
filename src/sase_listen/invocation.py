"""Invoked program name for user-facing command hints. Owner: cli phase.

The CLI runs as either the standalone ``sase-listen`` binary or the
``sase listen`` plugin command. Every user-facing command suggestion
("run this next" hints, error prefixes, epilogs built at runtime) routes
through :func:`command` so it names the binary the user actually invoked.

Product and distribution names (``--version`` output, dist metadata, uv/pipx
tool names, XDG paths, the wire protocol) intentionally stay ``sase-listen``.

:func:`build_parser` stays side-effect free: parser epilogs take ``prog``
as an explicit parameter instead of reading this module. Only the runtime
entry points (:func:`sase_listen.cli.main` /
:func:`sase_listen.cli.app.main`) call :func:`set_display_prog`.
"""

from __future__ import annotations

import argparse

STANDALONE_PROG = "sase-listen"

_display_prog = STANDALONE_PROG


def set_display_prog(prog: str) -> None:
    """Record the invoked program name for subsequent hints."""
    global _display_prog
    _display_prog = prog or STANDALONE_PROG


def display_prog() -> str:
    """Return the invoked program name (``sase-listen`` by default)."""
    return _display_prog


def command(*parts: str) -> str:
    """Join the display program name with command parts.

    ``command("render")`` is ``"sase-listen render"`` standalone and
    ``"sase listen render"`` under the plugin.
    """
    return " ".join((_display_prog, *parts))


def mark_path_completion(action: argparse.Action) -> argparse.Action:
    """Flag an argparse action as path-completing for shell completion.

    Sets the ``sase_completion`` attribute the sase completion walker reads
    (``"path"``); anything else still completes from argparse ``choices``.
    """
    action.sase_completion = "path"  # type: ignore[attr-defined]
    return action
