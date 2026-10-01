"""Script model, parser, and safety-net cleaner tests. Owner: script phase."""

from __future__ import annotations

from sase_listen.script import clean_residual_markdown, count_words, parse_script_text

VALID = """---
narration: 1
title: Episode One
kind: research
edition: full
producer: agent
---

## The question

First paragraph here.

Second paragraph here.

## The answer

One more paragraph.
"""


def test_parse_valid_script() -> None:
    script = parse_script_text(VALID)
    assert script.meta.narration == 1
    assert script.meta.title == "Episode One"
    assert script.meta.kind == "research"
    assert script.meta.edition == "full"
    assert len(script.chapters) == 2
    assert script.chapters[0].title == "The question"
    assert script.chapters[0].paragraphs == [
        "First paragraph here.",
        "Second paragraph here.",
    ]


def test_parse_missing_frontmatter_defaults() -> None:
    script = parse_script_text("## Only chapter\n\nWords here.\n")
    assert script.meta.narration == 0
    assert script.meta.title == ""
    assert len(script.chapters) == 1


def test_parse_preamble_kept_out_of_chapters() -> None:
    script = parse_script_text("Intro words.\n\n## Chapter\n\nBody.\n")
    assert script.preamble == "Intro words."
    assert len(script.chapters) == 1


def test_dumps_round_trip() -> None:
    script = parse_script_text(VALID)
    reparsed = parse_script_text(script.dumps())
    assert reparsed.meta.title == "Episode One"
    assert [c.title for c in reparsed.chapters] == ["The question", "The answer"]
    assert reparsed.words() == script.words()


def test_words_counts_chapters_only() -> None:
    script = parse_script_text(VALID)
    assert script.words() == count_words(
        "The question First paragraph here. "
        "Second paragraph here. "
        "The answer One more paragraph."
    )


def test_cleaner_strips_residue_and_reports() -> None:
    cleaned = clean_residual_markdown(
        "Say **bold** and `code`, keep [anchor](http://x), drop <b>tags</b>."
    )
    assert cleaned.text == "Say bold and code, keep anchor, drop tags."
    assert sorted(cleaned.touched) == ["code", "emphasis", "html", "link"]


def test_cleaner_autolink_keeps_target() -> None:
    cleaned = clean_residual_markdown("Visit <https://example.com> soon.")
    assert cleaned.text == "Visit https://example.com soon."
    assert cleaned.touched == ["autolink"]


def test_cleaner_plain_text_untouched() -> None:
    cleaned = clean_residual_markdown("Plain spoken prose.")
    assert cleaned.text == "Plain spoken prose."
    assert cleaned.touched == []
