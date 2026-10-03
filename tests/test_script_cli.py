"""script/lint/guide command tests. Owner: script phase."""

from __future__ import annotations

import json
import re

import pytest

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
    assert "edition: verbatim" in payload["script"]


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


def _fenced_frontmatter(guide_text: str) -> dict[str, str]:
    match = re.search(r"```markdown\n---\n(.*?)\n---", guide_text, re.DOTALL)
    assert match is not None, "expected a fenced frontmatter example"
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.split("#")[0].strip()
    return meta


def _assert_no_unresolved_tokens(guide_text: str) -> None:
    assert "{{" not in guide_text
    assert "}}" not in guide_text
    assert "<!-- edition:" not in guide_text
    assert "edition:full-begin" not in guide_text
    assert "edition:brief-begin" not in guide_text


def test_guide_defaults_to_brief(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["guide"]) == 0
    default = capsys.readouterr().out
    assert main(["guide", "--edition", "brief"]) == 0
    explicit_brief = capsys.readouterr().out
    assert default == explicit_brief
    assert render_guide() == render_guide("brief")
    assert default == render_guide("brief")


def test_guide_brief_edition_content() -> None:
    brief = render_guide("brief")
    meta = _fenced_frontmatter(brief)
    assert meta["edition"] == "brief"
    assert meta["target_minutes"] == "4"
    assert "Use 2-3 chapters" in brief
    assert "Write the `brief` edition at about 600 words" in brief
    assert "Use 4-8 chapters" not in brief
    assert "Write the `full` edition" not in brief
    assert "sase-listen lint" in brief
    _assert_no_unresolved_tokens(brief)


def test_guide_full_edition_content() -> None:
    full = render_guide("full")
    meta = _fenced_frontmatter(full)
    assert meta["edition"] == "full"
    assert meta["target_minutes"] == "16"
    assert "Use 4-8 chapters" in full
    assert "Write the `full` edition at up to 2,400 words" in full
    assert "Use 2-3 chapters" not in full
    assert "Write the `brief` edition" not in full
    assert "sase-listen lint" in full
    _assert_no_unresolved_tokens(full)


def test_guide_prints_and_editions_differ(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["guide"]) == 0
    brief = capsys.readouterr().out
    assert "sase-listen lint" in brief
    assert main(["guide", "--edition", "full"]) == 0
    full = capsys.readouterr().out
    assert brief != full
    assert render_guide("full") != render_guide("brief")


def test_guide_help_identifies_brief_default(capsys) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit) as exc:
        main(["guide", "--help"])
    assert exc.value.code == 0
    assert "default: brief" in capsys.readouterr().out
