"""Narration-script v1 data model. Owner: script phase."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["research", "document"]
Edition = Literal["full", "brief", "digest", "verbatim"]
Producer = Literal["agent", "deterministic"]

VALID_KINDS: tuple[str, ...] = ("research", "document")
VALID_EDITIONS: tuple[str, ...] = ("full", "brief", "digest", "verbatim")
VALID_PRODUCERS: tuple[str, ...] = ("agent", "deterministic")

#: Word budgets at 150 wpm. None means no budget (verbatim).
EDITION_BUDGETS: dict[str, int | None] = {
    "full": 2400,
    "brief": 600,
    "digest": 250,
    "verbatim": None,
}


def count_words(text: str) -> int:
    """Count whitespace-separated words."""
    return len(text.split())


@dataclass
class ScriptMeta:
    """Frontmatter for a narration script v1 document."""

    narration: int = 0
    title: str = ""
    source: str = ""
    source_blob: str = ""
    date: str = ""
    kind: str = "document"
    edition: str = "verbatim"
    producer: str = "deterministic"
    target_minutes: int | None = None
    cover: str = ""


@dataclass
class Chapter:
    """One ``##`` chapter: a speakable title plus plain paragraphs."""

    title: str
    paragraphs: list[str] = field(default_factory=list)
    line: int = 0

    def words(self) -> int:
        """Return the word count of title plus paragraphs."""
        total = count_words(self.title)
        for para in self.paragraphs:
            total += count_words(para)
        return total


@dataclass
class NarrationScript:
    """A parsed narration script: metadata plus chapters."""

    meta: ScriptMeta
    chapters: list[Chapter]
    preamble: str = ""

    def words(self) -> int:
        """Return the total spoken word count (chapters only)."""
        return sum(ch.words() for ch in self.chapters)

    def dumps(self) -> str:
        """Serialize back to narration-script v1 Markdown."""
        import yaml

        mapping: dict[str, object] = {
            "narration": self.meta.narration,
            "title": self.meta.title,
        }
        if self.meta.source:
            mapping["source"] = self.meta.source
        if self.meta.source_blob:
            mapping["source_blob"] = self.meta.source_blob
        if self.meta.date:
            mapping["date"] = self.meta.date
        mapping["kind"] = self.meta.kind
        mapping["edition"] = self.meta.edition
        mapping["producer"] = self.meta.producer
        if self.meta.target_minutes is not None:
            mapping["target_minutes"] = self.meta.target_minutes
        if self.meta.cover:
            mapping["cover"] = self.meta.cover
        front = yaml.safe_dump(
            mapping, sort_keys=False, allow_unicode=True, default_flow_style=False
        ).strip()
        parts: list[str] = ["---", front, "---", ""]
        for chapter in self.chapters:
            parts.append(f"## {chapter.title}")
            parts.append("")
            for para in chapter.paragraphs:
                parts.append(para)
                parts.append("")
        text = "\n".join(parts).strip() + "\n"
        return text
