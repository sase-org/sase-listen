"""Normalizer unit, golden, and property tests. Owner: script phase."""

from __future__ import annotations

import json
from pathlib import Path

from sase_listen.normalize import (
    humanize_filename,
    humanize_identifier,
    normalize_markdown,
    spoken_code,
)
from sase_listen.script import lint_text

FIXTURES = Path(__file__).parent / "fixtures" / "markdown"


def test_humanize_filename() -> None:
    assert humanize_filename("my_notes.md") == "My Notes"
    assert humanize_filename("episode-2.md") == "Episode 2"


def test_humanize_identifier() -> None:
    assert humanize_identifier("snake_case_name") == "snake case name"
    assert humanize_identifier("CamelCaseName") == "Camel Case Name"
    assert humanize_identifier("myFunc") == "my Func"


def test_spoken_code_drops_machine_text() -> None:
    assert spoken_code("src/foo.py:12") == ""
    assert spoken_code("deadbeef1234") == ""
    assert spoken_code("research:202610/x.md") == ""
    assert spoken_code("https://example.com") == ""
    assert spoken_code("out/ep.mp3") == ""


def test_spoken_code_speaks_keys_and_names() -> None:
    assert spoken_code("q") == "the q key"
    assert spoken_code("snake_case") == "snake case"
    assert spoken_code("0.6.0") == "0.6.0"


def test_title_prefers_frontmatter_then_h1_then_filename() -> None:
    fm, _ = normalize_markdown("# H1 Title\n\n## C\n\nBody.\n", filename="f.md")
    assert "title: H1 Title" in fm
    with_title, _ = normalize_markdown(
        "---\ntitle: FM Title\n---\n\n# H1\n\n## C\n\nBody.\n", filename="f.md"
    )
    assert "title: FM Title" in with_title
    bare, _ = normalize_markdown("## C\n\nBody.\n", filename="my_notes.md")
    assert "title: My Notes" in bare


def test_section_sign_and_links() -> None:
    script, _ = normalize_markdown("## C\n\nSee §6 and [docs](http://x).\n")
    assert "section 6" in script
    assert "docs" in script
    assert "http" not in script


def test_golden_fixtures_match() -> None:
    sources = sorted(
        p for p in FIXTURES.glob("*.md") if not p.name.endswith(".narration.md")
    )
    assert len(sources) >= 3
    for source in sources:
        stem = source.stem
        expected_script = (FIXTURES / f"{stem}.narration.md").read_text(
            encoding="utf-8"
        )
        expected_omissions = json.loads(
            (FIXTURES / f"{stem}.omissions.json").read_text(encoding="utf-8")
        )
        script, omissions = normalize_markdown(
            source.read_text(encoding="utf-8"), filename=source.name
        )
        assert script == expected_script, f"script drift in {stem}"
        assert [o.to_dict() for o in omissions] == expected_omissions


def test_every_fixture_output_has_no_errors() -> None:
    """Every golden output is structurally and residue clean (warnings may remain)."""
    for source in FIXTURES.glob("*.narration.md"):
        text = source.read_text(encoding="utf-8")
        errors = [f for f in lint_text(text) if f.severity == "error"]
        assert errors == [], f"{source.name}: {errors}"


def test_deterministic_output() -> None:
    source = (FIXTURES / "everything.md").read_text(encoding="utf-8")
    first, _ = normalize_markdown(source, filename="everything.md")
    second, _ = normalize_markdown(source, filename="everything.md")
    assert first == second
