"""Labels and coverage text for article audio editions."""

from __future__ import annotations

from datetime import date

_LABELS = {"brief": "Brief", "full": "Full", "verbatim": "Reading"}


def article_display_title(title: str, edition: str) -> str:
    """Add the article edition label while keeping the bare title elsewhere."""
    label = _LABELS.get(edition)
    return f"{title} ({label})" if label else title


def article_coverage_sentence(edition: str) -> str:
    """Return the stable coverage description used by ID3 and feed items."""
    return {
        "brief": (
            "A short narrated briefing: the article's question, answer, "
            "and deciding evidence."
        ),
        "full": (
            "A full-length narrated adaptation of the whole article — "
            "not a word-for-word reading."
        ),
        "verbatim": (
            "The article text read aloud; code, tables, and figures are omitted."
        ),
    }.get(edition, "")


def display_source_date(raw: str) -> str:
    """Format an ISO source date for display, preserving other date strings."""
    text = raw.strip().strip("\"'")
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return text
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"
