"""arXiv paper URL recognition and canonical PDF resolution.

Pure string and URL logic only: no I/O and no new dependencies.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}

_NEW_ID = r"\d{4}\.\d{4,5}(?:v\d+)?"
_OLD_ID = r"[a-z]+(?:-[a-z]+)*(?:\.[A-Za-z]{2})?/\d{7}(?:v\d+)?"
_ID = f"(?:{_NEW_ID}|{_OLD_ID})"
_PATH_RE = re.compile(f"^/(abs|html|pdf)/({_ID})(\\.pdf)?/?$")


def arxiv_paper_id(url: str) -> str | None:
    """Return the arXiv identifier for an arXiv paper page URL, else None."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    host = parsed.hostname
    if host is None:
        return None
    if host.lower() not in _ARXIV_HOSTS:
        return None
    match = _PATH_RE.match(parsed.path)
    if match is None:
        return None
    kind, paper_id, pdf_suffix = match.group(1), match.group(2), match.group(3)
    if pdf_suffix is not None and kind != "pdf":
        return None
    return paper_id


def arxiv_pdf_url(url: str) -> str | None:
    """Return https://arxiv.org/pdf/<id> for an arXiv paper page URL, else None."""
    paper_id = arxiv_paper_id(url)
    if paper_id is None:
        return None
    return f"https://arxiv.org/pdf/{paper_id}"
