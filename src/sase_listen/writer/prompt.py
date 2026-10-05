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

_PDF_RULES = """

PDF source rules:
- The text was extracted from a PDF, often an academic paper; say "the paper"
  and "the authors" when it is one.
- Ignore layout residue (stray figure labels, table fragments, repeated
  captions, broken hyphenation).
- Convey figures and tables only through captions and the surrounding prose.
- Never read citation numbers, emails, affiliations, or copyright notices.
""".strip()


def system_prompt(edition: str, source_format: str = "html") -> str:
    """Return the packaged guide plus the article-specific writing rules."""
    base = f"{render_guide(edition).strip()}\n\n{_ARTICLE_RULES}\n"
    if source_format == "pdf":
        return f"{base}\n{_PDF_RULES}\n"
    return base


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
    is_pdf = metadata.get("format", "html") == "pdf"
    outline_label = (
        "Document outline (headings in source order):"
        if is_pdf
        else "Article outline (restored headings in source order):"
    )
    format_line = ""
    if is_pdf:
        try:
            pages = int(metadata.get("pages", 0) or 0)
        except (TypeError, ValueError):
            pages = 0
        format_line = f"Format: PDF document ({pages} pages)\n"
    return (
        f"Edition: {edition}\n"
        f"Title: {metadata.get('title') or ''}\n"
        f"Author: {metadata.get('author') or ''}\n"
        f"Site: {metadata.get('site') or ''}\n"
        f"Published: {metadata.get('date') or ''}\n"
        f"Canonical URL: {metadata.get('canonical_url') or ''}\n"
        f"{format_line}"
        "Never speak the URL.\n\n"
        f"{outline_label}\n"
        + ("\n".join(f"- {heading}" for heading in headings) or "(none)")
        + "\n\nSource Markdown begins:\n<ARTICLE_SOURCE>\n"
        + source
        + "\n</ARTICLE_SOURCE>\nSource Markdown ends."
    )
