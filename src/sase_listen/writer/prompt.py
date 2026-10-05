"""Versioned prompts for article edition writing."""

from __future__ import annotations

from sase_listen.cli.guide import render_guide
from sase_listen.web.store import AcquiredSource

WRITER_PROMPT_VERSION = 1

_ARTICLE_RULES = """

Article adaptation rules:
- The source is a published web article; read "report" as "article". Ignore the
  guide's file-naming, source_blob, cover, and lint-command steps.
- Output only the body: `##` chapters and plain paragraphs. Do not add frontmatter,
  an H1, code fences, a preamble, an AI disclosure, an intro, or an outro.
- Turn first-person voice into third person ("the team", "the author"). The narrator
  is not the author. Keep the author's claims separate from interpretation; add no
  opinions.
- Brief: 2 to 3 chapters and about 600 words, covering the article's question, answer,
  central argument, deciding evidence, and takeaway.
- Full: cover every major section in source order. Use speakable source headings as
  chapter titles, merging adjacent sections to stay within 4 to 8 chapters. Stay within
  2,400 words and below about 90% of the source word count.
- Follow the guide's rules for numbers, code, tables, and symbols.
""".strip()


def system_prompt(edition: str) -> str:
    """Return the packaged guide plus the article-specific writing rules."""
    return f"{render_guide(edition).strip()}\n\n{_ARTICLE_RULES}\n"


def user_prompt(article: AcquiredSource, edition: str) -> str:
    """Build a user prompt with metadata, outline, and delimited source text."""
    metadata = article.metadata
    outline = metadata.get("outline", {})
    if not isinstance(outline, dict):
        outline = {}
    headings = outline.get("restored", [])
    if not isinstance(headings, list):
        headings = []
    source = article.markdown_path.read_text(encoding="utf-8")
    return (
        f"Edition: {edition}\n"
        f"Title: {metadata.get('title') or ''}\n"
        f"Author: {metadata.get('author') or ''}\n"
        f"Site: {metadata.get('site') or ''}\n"
        f"Published: {metadata.get('date') or ''}\n"
        f"Canonical URL: {metadata.get('canonical_url') or ''}\n"
        "Never speak the URL.\n\n"
        "Article outline (restored headings in source order):\n"
        + ("\n".join(f"- {heading}" for heading in headings) or "(none)")
        + "\n\nSource Markdown begins:\n<ARTICLE_SOURCE>\n"
        + source
        + "\n</ARTICLE_SOURCE>\nSource Markdown ends."
    )
