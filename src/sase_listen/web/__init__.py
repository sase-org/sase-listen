"""Local web-article acquisition and extraction."""

from sase_listen.web.extract import Article, extract_article, normalize_url
from sase_listen.web.fetch import FetchedPage, fetch_page, is_pdf_bytes
from sase_listen.web.pdf import PdfExtraction, extract_pdf
from sase_listen.web.store import AcquiredSource, acquire, acquire_file

__all__ = [
    "AcquiredSource",
    "Article",
    "FetchedPage",
    "PdfExtraction",
    "acquire",
    "acquire_file",
    "extract_article",
    "extract_pdf",
    "fetch_page",
    "is_pdf_bytes",
    "normalize_url",
]
