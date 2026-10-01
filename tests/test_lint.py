"""Lint rule tests. Owner: script phase."""

from __future__ import annotations

from sase_listen.script import is_residue, is_structural, lint_text

CLEAN = """---
narration: 1
title: Clean Episode
kind: document
edition: verbatim
producer: deterministic
---

## First chapter

Short spoken prose here.

## Second chapter

More short prose here.
"""


def _rules(text: str, source: str | None = None) -> dict[str, str]:
    return {f.rule: f.severity for f in lint_text(text, source)}


def _errors(text: str, source: str | None = None) -> list[str]:
    return [f.rule for f in lint_text(text, source) if f.severity == "error"]


def test_clean_script_has_no_findings() -> None:
    assert lint_text(CLEAN) == []


def test_missing_frontmatter_is_structural_error() -> None:
    findings = lint_text("## Chapter\n\nBody.\n")
    assert "S001" in _rules("## Chapter\n\nBody.\n")
    assert all(is_structural(f) for f in findings if f.rule == "S001")


def test_bad_narration_marker_and_empty_title() -> None:
    text = "---\nnarration: 2\ntitle: ''\n---\n\n## Chapter\n\nBody.\n"
    rules = _rules(text)
    assert rules["S002"] == "error"
    assert rules["S003"] == "error"


def test_no_chapters_empty_chapter_and_preamble() -> None:
    assert "S004" in _rules("---\nnarration: 1\ntitle: T\n---\n\nBody only.\n")
    no_body = "---\nnarration: 1\ntitle: T\n---\n\n## Empty\n"
    assert "S005" in _rules(no_body)
    preamble = "---\nnarration: 1\ntitle: T\n---\n\nIntro.\n\n## C\n\nBody.\n"
    assert "S007" in _rules(preamble)


def test_non_h2_heading_is_structural() -> None:
    text = "---\nnarration: 1\ntitle: T\n---\n\n## C\n\n### Deep\n\nBody.\n"
    assert "S006" in _rules(text)


def test_residue_errors() -> None:
    body = (
        "## C\n\n- item\n\n| a |\n\n```\ncode\n```\n\nUse `x` here.\n\n"
        "See [a](http://x).\n\n<b>tag</b>\n\nSay **hi**.\n"
    )
    text = f"---\nnarration: 1\ntitle: T\n---\n\n{body}"
    errors = _errors(text)
    for rule in ("R001", "R002", "R003", "R004", "R005", "R006", "R007"):
        assert rule in errors, f"missing {rule}"
    assert all(is_residue(f) for f in lint_text(text) if f.rule.startswith("R"))


def test_warnings_for_urls_paths_shas_refs_and_sign() -> None:
    body = (
        "## C\n\nVisit https://example.com/a and src/foo.py:12 plus deadbeef01 "
        "and research:202610/x.md near §6.\n"
    )
    text = f"---\nnarration: 1\ntitle: T\n---\n\n{body}"
    rules = _rules(text)
    assert rules.get("W001") == "warning"
    assert rules.get("W003") == "warning"
    assert rules.get("W004") == "warning"
    assert rules.get("W005") == "warning"
    assert rules.get("W006") == "warning"


def test_symbol_and_length_warnings() -> None:
    long_sentence = " ".join(["word"] * 46) + "."
    long_para = " ".join(["word"] * 181)
    text = (
        "---\nnarration: 1\ntitle: T\n---\n\n## C\n\n"
        f"Arrow → here. {long_sentence}\n\n{long_para}\n"
    )
    rules = _rules(text)
    assert rules.get("W007") == "warning"
    assert rules.get("W010") == "warning"
    assert rules.get("W009") == "warning"


def test_edition_budget_warnings() -> None:
    words = " ".join(["word"] * 3000)
    text = "---\nnarration: 1\ntitle: T\nedition: full\n---\n\n## C\n\n" + words + "\n"
    assert _rules(text).get("W011") == "warning"
    short = "---\nnarration: 1\ntitle: T\nedition: full\n---\n\n## C\n\nHi there.\n"
    assert _rules(short).get("W012") == "warning"
    verbatim = "---\nnarration: 1\ntitle: T\n---\n\n## C\n\n" + words + "\n"
    assert "W011" not in _rules(verbatim)


def test_number_fidelity() -> None:
    script = (
        "---\nnarration: 1\ntitle: T\n---\n\n## C\n\nCosts 42 ships and 3.14 stars.\n"
    )
    source = "The report costs 42 ships under 3.14 stars."
    assert lint_text(script, source) == []
    other = "An unrelated report with nothing matching."
    rules = _rules(script, other)
    assert rules.get("W013") == "warning"


def test_findings_carry_line_col_and_hint() -> None:
    findings = lint_text("## C\n\nBody.\n")
    assert findings
    for finding in findings:
        assert finding.line >= 1
        assert finding.col >= 1
        assert finding.hint
        assert finding.message
