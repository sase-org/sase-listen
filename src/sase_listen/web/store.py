"""Atomic per-URL article source storage and cache reuse."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.paths import sources_dir
from sase_listen.web.extract import Article, extract_article, normalize_url
from sase_listen.web.fetch import FetchedPage, fetch_page, load_html_file


@dataclass(frozen=True)
class AcquiredSource:
    """Stored article source and its acquisition metadata."""

    directory: Path
    page_path: Path
    markdown_path: Path
    metadata: dict[str, Any]
    reused: bool = False

    @property
    def verbatim_script_path(self) -> Path:
        """Path used to cache the deterministic narration script."""
        return self.directory / "verbatim_narration.md"


def save_verbatim_script(source: AcquiredSource, script_text: str) -> None:
    """Cache a deterministic script and bind it to the current source hash."""
    _atomic_write(source.verbatim_script_path, script_text.encode("utf-8"))
    metadata = dict(source.metadata)
    metadata["verbatim_source_sha256"] = metadata.get("source_sha256", "")
    _atomic_json(source.directory / "source.json", metadata)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".tmp-{os.getpid()}-{path.name}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _atomic_json(path: Path, value: object) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write(path, data)


def _read_index(root: Path) -> dict[str, str]:
    path = root / "index.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SaseListenError(
            f"Could not read the web source index: {exc}.", ExitCode.UNEXPECTED
        ) from exc
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise SaseListenError(
            "The web source index is not a URL-to-directory object.",
            ExitCode.UNEXPECTED,
        )
    return dict(value)


def _cached(root: Path, index: dict[str, str], key: str) -> AcquiredSource | None:
    name = index.get(key)
    if not name or Path(name).name != name:
        return None
    directory = root / name
    metadata_path = directory / "source.json"
    markdown_path = directory / "source.md"
    page_path = directory / "page.html"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            not isinstance(metadata, dict)
            or not markdown_path.is_file()
            or not page_path.is_file()
        ):
            return None
    except (OSError, json.JSONDecodeError):
        return None
    return AcquiredSource(
        directory, page_path, markdown_path, dict(metadata), reused=True
    )


def _source_name(title: str, canonical_url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].strip("-")
    slug = slug or "article"
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{digest}"


def _source_markdown(article: Article) -> str:
    body = article.markdown.strip()
    return f"# {article.title}\n\n{body}\n" if body else f"# {article.title}\n"


def _metadata(
    page: FetchedPage, article: Article, source_markdown: str, html_sha256: str
) -> dict[str, Any]:
    from importlib.metadata import PackageNotFoundError, version

    try:
        extractor_version = version("trafilatura")
    except PackageNotFoundError:  # pragma: no cover - dependency is locked
        extractor_version = "unknown"
    return {
        "url": page.requested_url,
        "canonical_url": article.canonical_url,
        "final_url": page.final_url,
        "title": article.title,
        "author": article.author,
        "site": article.site,
        "date": article.date,
        "description": article.description,
        "fetched_at": page.fetched_at,
        "http_status": page.status,
        "html_sha256": html_sha256,
        "source_sha256": hashlib.sha256(source_markdown.encode("utf-8")).hexdigest(),
        "words": article.words,
        "extractor": {"name": "trafilatura", "version": extractor_version},
        "outline": {
            "found": len(article.outline),
            "restored": list(article.restored_headings),
            "missing": list(article.missing_headings),
        },
    }


def acquire(
    url: str,
    *,
    html_file: str | Path | None = None,
    refresh: bool = False,
    on_step: Callable[[str], None] | None = None,
) -> AcquiredSource:
    """Fetch/extract a URL once, retaining bytes, text and diagnostics locally."""
    requested_key = normalize_url(url)
    root = sources_dir()
    root.mkdir(parents=True, exist_ok=True)
    index = _read_index(root)
    if html_file is None and not refresh:
        cached = _cached(root, index, requested_key)
        if cached is not None:
            return cached

    if on_step is not None:
        if html_file is not None:
            name = Path(str(html_file)).name or "saved HTML"
            on_step(f"reading saved HTML from {name}")
        else:
            host = requested_key.split("://", 1)[-1].split("/", 1)[0]
            on_step(f"fetching {host}")
    page = load_html_file(url, html_file) if html_file is not None else fetch_page(url)
    if on_step is not None:
        on_step("extracting the article text")
    html_text = page.body.decode("utf-8", errors="replace")
    article = extract_article(html_text, page.final_url)
    canonical = normalize_url(article.canonical_url)
    source_markdown = _source_markdown(article)
    html_sha256 = hashlib.sha256(page.body).hexdigest()
    metadata = _metadata(page, article, source_markdown, html_sha256)
    name = _source_name(article.title, canonical)
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(directory / "page.html", page.body)
    _atomic_write(directory / "source.md", source_markdown.encode("utf-8"))
    _atomic_json(directory / "source.json", metadata)
    index[requested_key] = name
    index[canonical] = name
    _atomic_json(root / "index.json", index)
    return AcquiredSource(
        directory,
        directory / "page.html",
        directory / "source.md",
        metadata,
    )
