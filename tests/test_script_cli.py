"""script/lint/guide command tests. Owner: script phase."""

from __future__ import annotations

import json

from sase_listen.cli.app import main
from sase_listen.cli.guide import render_guide

SCRIPT = """---
narration: 1
title: Cmd Episode
kind: document
edition: verbatim
producer: deterministic
---

## First chapter

Short spoken prose.
"""

SOURCE = """# Report

Short spoken prose.
"""


def _write(tmp_path, name, text):  # type: ignore[no-untyped-def]
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_script_writes_file_and_json(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    src = _write(tmp_path, "notes.md", "# Notes\n\n## C\n\nBody words.\n")
    out = tmp_path / "notes_narration.md"
    assert main(["script", str(src), "-o", str(out)]) == 0
    capsys.readouterr()
    assert out.exists()
    assert main(["script", str(src), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["title"] == "Notes"
    assert isinstance(payload["omissions"], list)
    assert "narration: 1" in payload["script"]


def test_script_json_is_exactly_one_object(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    src = _write(tmp_path, "notes.md", "## C\n\nBody.\n")
    assert main(["script", str(src), "--json"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    json.loads(out)


def test_script_missing_file_is_usage(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["script", "absent.md"]) == 2


def test_lint_clean_and_json(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    script = _write(tmp_path, "ep_narration.md", SCRIPT)
    assert main(["lint", str(script)]) == 0
    capsys.readouterr()
    assert main(["lint", str(script), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["errors"] == 0


def test_lint_errors_exit_1_and_strict_warnings(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    bad = _write(tmp_path, "bad.md", "## C\n\nUse `code` here.\n")
    assert main(["lint", str(bad)]) == 1
    capsys.readouterr()
    warned = _write(
        tmp_path, "warn.md", SCRIPT.replace("Short spoken prose.", "See https://x.io.")
    )
    assert main(["lint", str(warned)]) == 0
    capsys.readouterr()
    assert main(["lint", str(warned), "--strict"]) == 1
    capsys.readouterr()


def test_lint_source_number_fidelity(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    script = _write(
        tmp_path,
        "ep_narration.md",
        SCRIPT.replace("Short spoken prose.", "It costs 42 ships."),
    )
    source = _write(tmp_path, "report.md", "Nothing matching here.")
    assert main(["lint", str(script), "--source", str(source), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert any(f["rule"] == "W013" for f in payload["findings"])


def test_guide_prints_and_editions_differ(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["guide"]) == 0
    full = capsys.readouterr().out
    assert "sase-listen lint" in full
    assert main(["guide", "--edition", "brief"]) == 0
    brief = capsys.readouterr().out
    assert brief != full
    assert "600" in brief
    assert render_guide("full") != render_guide("brief")
