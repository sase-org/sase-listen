"""Browser-like, local HTTP fetches for public article pages and PDFs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sase_listen.errors import ExitCode, SaseListenError

MAX_PAGE_BYTES = 20 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
PDF_CONTENT_TYPE = "application/pdf"
_PDF_TYPES = {"application/pdf", "application/x-pdf"}
_OCTET_TYPES = {"application/octet-stream", "binary/octet-stream"}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_CHALLENGE_MARKERS = (
    "cf-chl-",
    "challenge-platform",
    "just a moment",
    "checking your browser",
    "verify you are human",
    "enable javascript and cookies",
)


@dataclass(frozen=True)
class FetchedPage:
    """An HTTP response accepted as an HTML page or PDF document."""

    requested_url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    fetched_at: str


def is_pdf_bytes(data: bytes) -> bool:
    """Return True when the bytes look like a PDF document."""
    return b"%PDF-" in bytes(data[:1024])


def _validate_http_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise SaseListenError(
            f"Not an http(s) URL: {url}.",
            ExitCode.USAGE,
            hint="Pass a public article URL beginning with http:// or https://.",
        )


def _challenge(headers: Any, body: bytes, status: int) -> bool:
    header_value = str(headers.get("cf-mitigated", ""))
    if header_value.casefold() == "challenge":
        return True
    if status not in {403, 429, 503}:
        return False
    sample = body[: 256 * 1024].decode("utf-8", errors="replace").casefold()
    return any(marker in sample for marker in _CHALLENGE_MARKERS)


def _error(message: str, *, hint: str = "") -> SaseListenError:
    return SaseListenError(message, ExitCode.UNEXPECTED, hint=hint)


def _read_local_html(path: str | Path, *, max_bytes: int) -> bytes:
    source = Path(path).expanduser()
    try:
        size = source.stat().st_size
        if size > max_bytes:
            raise SaseListenError(
                f"HTML source is too large ({size} bytes; limit is {max_bytes}).",
                ExitCode.USAGE,
            )
        body = source.read_bytes()
    except FileNotFoundError as exc:
        raise SaseListenError(
            f"HTML source not found: {source}.", ExitCode.USAGE
        ) from exc
    except OSError as exc:
        raise SaseListenError(
            f"Could not read HTML source {source}: {exc}.", ExitCode.USAGE
        ) from exc
    return body


def fetch_page(
    url: str,
    *,
    timeout_s: float = 30,
    max_bytes: int = MAX_PAGE_BYTES,
    max_pdf_bytes: int = MAX_PDF_BYTES,
) -> FetchedPage:
    """Fetch an HTML page or PDF document, impersonating current Chrome."""
    _validate_http_url(url)
    try:
        from curl_cffi import requests
    except ImportError as exc:  # pragma: no cover - locked runtime dependency
        raise _error("The curl_cffi fetch dependency is unavailable.") from exc

    try:
        response: Any = requests.get(
            url,
            impersonate="chrome",
            allow_redirects=True,
            timeout=timeout_s,
            stream=True,
        )
    except Exception as exc:
        raise _error(f"Could not fetch {url}: {exc}.") from exc

    try:
        status = int(response.status_code)
        content_type = (
            str(response.headers.get("content-type", ""))
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        pdf_capable = (
            content_type in _PDF_TYPES
            or content_type in _OCTET_TYPES
            or not content_type
        )
        limit = max_pdf_bytes if pdf_capable else max_bytes
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    raise _error(
                        f"Page is too large ({content_length} bytes; limit is {limit})."
                    )
            except ValueError:
                pass

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                raise _error(
                    f"Page is too large (over {limit} bytes).",
                    hint="Save a smaller HTML page and re-run with `--html FILE`.",
                )
            chunks.append(bytes(chunk))
        body = b"".join(chunks)
        if _challenge(response.headers, body, status):
            raise _error(
                f"The site returned a bot challenge (HTTP {status}).",
                hint=(
                    "Save the page from a browser (HTML only) and re-run with "
                    "`--html FILE`."
                ),
            )
        if status < 200 or status >= 300:
            raise _error(f"The site returned HTTP {status} for {url}.")
        body_is_pdf = is_pdf_bytes(body)
        if content_type in _PDF_TYPES:
            if not body_is_pdf:
                raise _error(
                    f"Expected a PDF document for {url} "
                    f"(content-type {content_type}), but the body is not a PDF.",
                )
            content_type = PDF_CONTENT_TYPE
        elif content_type in _OCTET_TYPES or not content_type:
            if body_is_pdf:
                content_type = PDF_CONTENT_TYPE
            elif content_type not in _HTML_TYPES:
                raise SaseListenError(
                    f"Unsupported page content type: {content_type or '(missing)'}.",
                    ExitCode.USAGE,
                    hint="Only HTML pages and PDF documents are supported.",
                )
        elif content_type not in _HTML_TYPES:
            raise SaseListenError(
                f"Unsupported page content type: {content_type or '(missing)'}.",
                ExitCode.USAGE,
                hint="Only HTML pages and PDF documents are supported.",
            )
        final_url = str(response.url)
        _validate_http_url(final_url)
        return FetchedPage(
            requested_url=url,
            final_url=final_url,
            status=status,
            content_type=content_type,
            body=body,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    finally:
        response.close()


def load_html_file(
    url: str, path: str | Path, *, max_bytes: int = MAX_PAGE_BYTES
) -> FetchedPage:
    """Wrap a saved browser page (HTML or PDF) in the fetched-page shape."""
    _validate_http_url(url)
    source = Path(path).expanduser()
    try:
        size = source.stat().st_size
    except FileNotFoundError as exc:
        raise SaseListenError(
            f"HTML source not found: {source}.", ExitCode.USAGE
        ) from exc
    except OSError as exc:
        raise SaseListenError(
            f"Could not read HTML source {source}: {exc}.", ExitCode.USAGE
        ) from exc
    # Read enough to sniff PDFs; the size cap depends on the sniff result.
    try:
        with source.open("rb") as handle:
            head = handle.read(1024)
    except OSError as exc:
        raise SaseListenError(
            f"Could not read HTML source {source}: {exc}.", ExitCode.USAGE
        ) from exc
    sniff_pdf = is_pdf_bytes(head)
    limit = MAX_PDF_BYTES if sniff_pdf else max_bytes
    if size > limit:
        kind = "PDF" if sniff_pdf else "HTML"
        raise SaseListenError(
            f"{kind} source is too large ({size} bytes; limit is {limit}).",
            ExitCode.USAGE,
        )
    try:
        body = source.read_bytes()
    except OSError as exc:
        raise SaseListenError(
            f"Could not read HTML source {source}: {exc}.", ExitCode.USAGE
        ) from exc
    if is_pdf_bytes(body):
        return FetchedPage(
            requested_url=url,
            final_url=url,
            status=200,
            content_type=PDF_CONTENT_TYPE,
            body=body,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    return FetchedPage(
        requested_url=url,
        final_url=url,
        status=200,
        content_type="text/html",
        body=body,
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
