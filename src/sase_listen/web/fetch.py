"""Browser-like, local HTTP fetches for public article pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sase_listen.errors import ExitCode, SaseListenError

MAX_PAGE_BYTES = 20 * 1024 * 1024
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
    """An HTTP response accepted as an HTML page."""

    requested_url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    fetched_at: str


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
) -> FetchedPage:
    """Fetch an HTML page locally, impersonating a current Chrome browser."""
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
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise _error(
                        f"Page is too large ({content_length} bytes; "
                        f"limit is {max_bytes})."
                    )
            except ValueError:
                pass

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise _error(
                    f"Page is too large (over {max_bytes} bytes).",
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
        if content_type == "application/pdf":
            raise SaseListenError(
                "PDF sources are not supported yet.",
                ExitCode.USAGE,
                hint="Convert the PDF to Markdown and render the file.",
            )
        if content_type not in _HTML_TYPES:
            raise SaseListenError(
                f"Unsupported page content type: {content_type or '(missing)'}.",
                ExitCode.USAGE,
                hint="Only text/html and application/xhtml+xml pages are supported.",
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
    """Wrap saved browser HTML in the fetched-page shape without network access."""
    _validate_http_url(url)
    body = _read_local_html(path, max_bytes=max_bytes)
    return FetchedPage(
        requested_url=url,
        final_url=url,
        status=200,
        content_type="text/html",
        body=body,
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
