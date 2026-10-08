"""Fast-start guard: parser construction and --help stay free of heavy imports.

Owner: listen-fast-start phase (epic sase-1if). Building the parser and
rendering help must not import numpy, PIL, mutagen, httpx, lxml,
trafilatura, pdfminer, google-genai, or the pipeline package; those stay
deferred into command handler bodies. Each check runs in a fresh
subprocess so earlier tests cannot pollute ``sys.modules``.
"""

from __future__ import annotations

import json
import subprocess
import sys

#: Third-party top levels that must stay out of parser construction and help.
HEAVY_TOP_LEVELS = frozenset(
    {
        "numpy",
        "PIL",
        "mutagen",
        "httpx",
        "lxml",
        "trafilatura",
        "pdfminer",
        "google",
    }
)

#: First-party modules that must stay out (they drag the above with them).
HEAVY_FIRST_PARTY = ("sase_listen.pipeline",)

_PROBE = (
    "import json, sys\n"
    "from sase_listen.cli import app as app_mod\n"
    "app_mod.build_parser(prog='sase listen')\n"
    "print(json.dumps(sorted(sys.modules)))\n"
)

_HELP_PROBE = (
    "import json, sys\n"
    "from sase_listen.cli import app as app_mod\n"
    "try:\n"
    "    app_mod.main(['--help'], prog='sase listen')\n"
    "except SystemExit as exc:\n"
    "    assert exc.code == 0, exc.code\n"
    "print(json.dumps(sorted(sys.modules)))\n"
)


def _loaded_modules(probe: str) -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return list(json.loads(proc.stdout.strip().splitlines()[-1]))


def _assert_no_heavy(modules: list[str]) -> None:
    heavy = [
        name
        for name in modules
        if name.split(".")[0] in HEAVY_TOP_LEVELS
        or name == "sase_listen.pipeline"
        or name.startswith("sase_listen.pipeline.")
    ]
    assert not heavy, f"heavy modules imported: {heavy}"


def test_build_parser_imports_no_heavy_modules() -> None:
    """`build_parser` alone must not pull the deferred imports."""
    _assert_no_heavy(_loaded_modules(_PROBE))


def test_help_imports_no_heavy_modules() -> None:
    """Top-level `--help` must not pull the deferred imports either."""
    _assert_no_heavy(_loaded_modules(_HELP_PROBE))
