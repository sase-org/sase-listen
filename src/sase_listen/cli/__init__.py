"""CLI entry point. Owner: scaffold registry; each command owned by its phase."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence


def _is_sase_listen_import(exc: ImportError) -> bool:
    name = getattr(exc, "name", None)
    return isinstance(name, str) and (
        name == "sase_listen" or name.startswith("sase_listen.")
    )


def _stale_message(exc: ImportError) -> str:
    text = str(exc) or ""
    if text.startswith("cannot import name"):
        return (
            f"sase-listen {text}: this installation's Python environment"
            " is out of date with its code"
        )
    module = getattr(exc, "name", None) or "unknown"
    return (
        f"sase-listen cannot import '{module}': this installation's Python"
        " environment is out of date with its code"
    )


def _upgrade_hint() -> str:
    try:
        from sase_listen.buildinfo import current
    except Exception:
        return "Reinstall: reinstall sase-listen with the installer you used."
    try:
        return f"Reinstall: {current().upgrade_command()}"
    except Exception:
        return "Reinstall: reinstall sase-listen with the installer you used."


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `sase-listen` console script."""
    from sase_listen.errors import ExitCode

    try:
        from sase_listen.cli import app as app_mod

        return int(app_mod.main(argv))
    except ImportError as exc:
        if _is_sase_listen_import(exc):
            raise
        message = _stale_message(exc)
        hint = _upgrade_hint()
        probe = list(sys.argv[1:] if argv is None else argv)
        if "--json" in probe:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {"code": 3, "message": message, "hint": hint},
                    }
                )
            )
        else:
            print(f"sase-listen: error: {message}", file=sys.stderr)
            if hint:
                print(f"hint: {hint}", file=sys.stderr)
        return int(ExitCode.CONFIG)


__all__ = ["main"]
