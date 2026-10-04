"""Local web-article acquisition and extraction."""

from sase_listen.web.extract import Article, extract_article, normalize_url
from sase_listen.web.fetch import FetchedPage, fetch_page
from sase_listen.web.store import AcquiredSource, acquire

__all__ = [
    "AcquiredSource",
    "Article",
    "FetchedPage",
    "acquire",
    "extract_article",
    "fetch_page",
    "normalize_url",
]
