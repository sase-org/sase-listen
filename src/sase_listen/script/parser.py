"""Narration-script v1 parser plus a residual-Markdown safety-net cleaner.

Owner: script phase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from sase_listen.script.model import Chapter, NarrationScript, ScriptMeta

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BLANK_RE = re.compile(r"^\s*$")

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_LINK_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)|\[([^\]]*)\]\([^)]*\)")
_AUTOLINK_RE = re.compile(r"<(https?://[^>]+|[^@\s>]+@[^>\s]+)>")
_BACKTICK_RE = re.compile(r"`+([^`]*?)`+")
_EMPHASIS_RE = re.compile(r"(\*\*|__)(.+?)\1|(\*|_)(.+?)\3|~~(.+?)~~")


@dataclass
class CleanedText:
    """Result of the safety-net cleaner."""

    text: str
    touched: list[str]


def clean_residual_markdown(text: str) -> CleanedText:
    """Strip residual Markdown syntax, reporting what was touched.

    Emphasis becomes plain text, backticks are removed, links keep anchor
    text, images keep alt text (or vanish when empty), and HTML tags vanish.
    """
    touched: list[str] = []
    out = text
    if _AUTOLINK_RE.search(out):
        out = _AUTOLINK_RE.sub(lambda m: m.group(1), out)
        touched.append("autolink")

    def _link_sub(m: re.Match[str]) -> str:
        alt = m.group(1) if m.group(1) is not None else m.group(2)
        return alt or ""

    if _LINK_RE.search(out):
        out = _LINK_RE.sub(_link_sub, out)
        touched.append("link")
    if _BACKTICK_RE.search(out):
        out = _BACKTICK_RE.sub(lambda m: m.group(1), out)
        touched.append("code")
    if _EMPHASIS_RE.search(out):
        prev = ""
        while prev != out:
            prev = out
            out = _EMPHASIS_RE.sub(lambda m: m.group(m.lastindex or 0), out)
        touched.append("emphasis")
    if _HTML_TAG_RE.search(out):
        out = _HTML_TAG_RE.sub("", out)
        touched.append("html")
    # Collapse leftover whitespace from removals without joining paragraphs.
    out = re.sub(r"[ \t]{2,}", " ", out)
    return CleanedText(text=out.strip(), touched=touched)


def _parse_meta(raw: str | None) -> ScriptMeta:
    meta = ScriptMeta()
    if raw is None:
        return meta
    try:
        loaded = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return meta
    if not isinstance(loaded, dict):
        return meta
    narration = loaded.get("narration", 0)
    try:
        meta.narration = int(narration)
    except (TypeError, ValueError):
        meta.narration = 0
    meta.title = str(loaded.get("title", ""))
    meta.source = str(loaded.get("source", ""))
    meta.source_blob = str(loaded.get("source_blob", ""))
    meta.date = str(loaded.get("date", ""))
    kind = str(loaded.get("kind", "document") or "document")
    meta.kind = kind
    edition = str(loaded.get("edition", "verbatim") or "verbatim")
    meta.edition = edition
    producer = str(loaded.get("producer", "deterministic") or "deterministic")
    meta.producer = producer
    target = loaded.get("target_minutes")
    if target is not None and str(target).strip() != "":
        try:
            meta.target_minutes = int(target)
        except (TypeError, ValueError):
            meta.target_minutes = None
    meta.cover = str(loaded.get("cover", ""))
    return meta


def parse_script_text(text: str) -> NarrationScript:
    """Parse narration-script v1 Markdown leniently (lint reports problems)."""
    normalized = text.replace("\r\n", "\n")
    meta = ScriptMeta()
    body = normalized
    match = _FRONTMATTER_RE.match(normalized)
    if match:
        meta = _parse_meta(match.group(1))
        body = normalized[match.end() :]
    lines = body.split("\n")
    chapters: list[Chapter] = []
    current: Chapter | None = None
    preamble_parts: list[str] = []
    para_buf: list[str] = []
    in_fence = False

    def _flush_para() -> None:
        if not para_buf:
            return
        para = " ".join(p.strip() for p in para_buf if p.strip()).strip()
        para_buf.clear()
        if not para:
            return
        if current is None:
            preamble_parts.append(para)
        else:
            current.paragraphs.append(para)

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING_RE.match(line)
        if heading and heading.group(1) == "##":
            _flush_para()
            current = Chapter(title=heading.group(2).strip(), line=lineno)
            chapters.append(current)
            continue
        if _BLANK_RE.match(line):
            _flush_para()
            continue
        para_buf.append(line)
    _flush_para()
    preamble = "\n\n".join(preamble_parts)
    # Account for frontmatter offset in chapter lines is handled by lint.
    _ = preamble
    return NarrationScript(meta=meta, chapters=chapters, preamble=preamble)
