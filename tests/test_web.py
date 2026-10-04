"""Web article fetching, extraction, storage, and URL rendering."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import sase_listen.web.store as store_module
from sase_listen.cli.app import main
from sase_listen.config import default_config
from sase_listen.engines.tone import ToneEngine
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.library import compute_episode_id
from sase_listen.pipeline import (
    RenderRequest,
    RenderResult,
    build_intro_text,
    load_source,
    looks_like_ref,
    looks_like_url,
    render,
)
from sase_listen.script import ScriptMeta
from sase_listen.web.extract import extract_article, normalize_url, repair_outline
from sase_listen.web.fetch import FetchedPage, fetch_page

URL = "https://example.test/story?utm_source=newsletter#top"


def _article_html() -> bytes:
    prose = (
        "A detailed synthetic paragraph explains the engineering choices and "
        "their effects for readers who need to understand the complete example. "
        "It includes enough meaningful words to pass the article length check. "
    )
    return f"""<!doctype html>
<html><head><title>Article Test</title>
<meta name="description" content="By Ada Lovelace, Editor">
<link rel="canonical" href="https://example.test/story?gclid=abc"></head>
<body><nav><button><span>Repeated hidden heading</span></button></nav>
<article><h1>Article Test</h1><p>By Ada Lovelace, Editor</p>
<h2 class="text-h3"><span>First Section</span></h2><p>{prose * 4}</p>
<h2><span>Second Section</span></h2><p>{prose * 4}</p>
<h3>Third Section</h3><p>{prose * 4}</p>
<h2>Author</h2><h2>Keep reading</h2></article>
</body></html>""".encode()


class _Response:
    def __init__(self, status: int, content_type: str, body: bytes) -> None:
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.url = "https://example.test/final"
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size: int) -> list[bytes]:
        _ = chunk_size
        return [self._body]

    def close(self) -> None:
        self.closed = True


def test_canonical_url_normalization() -> None:
    assert normalize_url("HTTPS://Example.Test/a?utm_source=x&keep=1#here") == (
        "https://example.test/a?keep=1"
    )
    assert normalize_url("https://example.test") == "https://example.test/"


def test_repair_outline_matches_prose_in_order() -> None:
    html = """<article>
<h2>First Section</h2><p>One long paragraph beginning with the first section's
prose and continuing with enough words to exceed forty characters.</p>
<h2>Second Section</h2><p>Another long paragraph beginning with the second
section's prose and continuing with enough words to exceed forty characters.</p>
<h2>Author</h2><h2>Keep reading</h2>
</article>"""
    markdown = (
        "One long paragraph beginning with the first section's prose and continuing "
        "with enough words to exceed forty characters.\n\n"
        "Another long paragraph beginning with the second section's prose "
        "and continuing "
        "with enough words to exceed forty characters."
    )
    repaired, restored, missing = repair_outline(markdown, html)
    assert restored == ["First Section", "Second Section"]
    assert missing == ["Author", "Keep reading"]
    assert repaired.index("## First Section") < repaired.index("## Second Section")


def test_extract_rejects_short_page() -> None:
    with pytest.raises(SaseListenError) as exc_info:
        extract_article(
            "<html><body><article><p>Too short.</p></article></body></html>", URL
        )
    assert exc_info.value.code == ExitCode.UNEXPECTED
    assert "--html" in exc_info.value.hint


def test_fetch_detects_challenge_and_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    import curl_cffi.requests

    challenge = _Response(403, "text/html", b"<title>Just a moment</title>")
    challenge.headers["cf-mitigated"] = "challenge"
    monkeypatch.setattr(curl_cffi.requests, "get", lambda *a, **k: challenge)
    with pytest.raises(SaseListenError) as challenge_error:
        fetch_page("https://example.test")
    assert challenge_error.value.code == ExitCode.UNEXPECTED
    assert "--html FILE" in challenge_error.value.hint
    assert challenge.closed

    pdf = _Response(200, "application/pdf", b"%PDF")
    monkeypatch.setattr(curl_cffi.requests, "get", lambda *a, **k: pdf)
    with pytest.raises(SaseListenError) as pdf_error:
        fetch_page("https://example.test")
    assert pdf_error.value.code == ExitCode.USAGE
    assert "PDF sources are not supported" in str(pdf_error.value)


def test_store_reuse_refresh_and_html_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    calls = 0

    def stub_fetch(url: str, **_: Any) -> FetchedPage:
        nonlocal calls
        calls += 1
        return FetchedPage(
            url, url, 200, "text/html", _article_html(), "2026-10-04T00:00:00+00:00"
        )

    monkeypatch.setattr(store_module, "fetch_page", stub_fetch)
    first = store_module.acquire(URL)
    second = store_module.acquire(URL)
    assert calls == 1
    assert first.directory == second.directory
    assert first.page_path.read_bytes() == _article_html()
    assert first.markdown_path.read_text(encoding="utf-8").startswith("# Article Test")
    assert (
        json.loads((first.directory / "source.json").read_text())["outline"]["found"]
        == 5
    )

    store_module.acquire(URL, refresh=True)
    assert calls == 2

    saved = tmp_path / "browser.html"
    saved.write_bytes(_article_html())
    file_source = store_module.acquire(URL, html_file=saved)
    assert file_source.page_path.read_bytes() == saved.read_bytes()


def test_url_cli_script_json_and_stable_episode_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(
        store_module,
        "fetch_page",
        lambda url, **kwargs: FetchedPage(
            url, url, 200, "text/html", _article_html(), "2026-10-04T00:00:00+00:00"
        ),
    )
    assert main(["script", "https://example.test/story", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["outline"]["found"] == 5
    assert "## First Section" in payload["script"]
    assert "## Second Section" in payload["script"]
    assert "Third Section" in payload["script"]
    assert "By Ada Lovelace" not in payload["script"]
    assert payload["outline"]["missing"] == ["Author", "Keep reading"]
    assert payload["script"].startswith("---\n")
    assert payload["source_dir"]

    url_loaded = load_source("https://example.test/story")
    file_loaded = load_source(str(url_loaded.source_path))
    assert file_loaded.source_key == url_loaded.source_key
    assert file_loaded.source_label == url_loaded.source_label
    assert file_loaded.source_sha256 == url_loaded.source_sha256
    assert compute_episode_id(url_loaded.script.meta.title, file_loaded.source_key) == (
        compute_episode_id(url_loaded.script.meta.title, url_loaded.source_key)
    )
    assert looks_like_url("HTTPS://example.test")
    assert not looks_like_ref("https://example.test")
    assert looks_like_ref("research:202610/report.md")
    with pytest.raises(SaseListenError) as edition_error:
        load_source("https://example.test/story", edition="brief")
    assert edition_error.value.code == ExitCode.USAGE
    assert "only --edition verbatim" in edition_error.value.hint


def test_tone_render_url_manifest_and_intro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(
        store_module,
        "fetch_page",
        lambda url, **kwargs: FetchedPage(
            url, url, 200, "text/html", _article_html(), "2026-10-04T00:00:00+00:00"
        ),
    )
    cfg = default_config()
    cfg.narrator = "tone"
    cfg.feed.auto_publish = False
    outcome = render(
        RenderRequest(source="https://example.test/story"),
        config=cfg,
        engine=ToneEngine(),
        library_root=tmp_path / "library",
    )
    assert isinstance(outcome, RenderResult)
    manifest = json.loads(Path(outcome.manifest_path).read_text(encoding="utf-8"))
    assert manifest["source"]["url"] == "https://example.test/story"
    assert manifest["source"]["title"] == "Article Test"
    assert manifest["source"]["script_path"].endswith("verbatim_narration.md")
    assert manifest["episode_id"] == outcome.episode_id
    assert outcome.episode_id == compute_episode_id(
        "Article Test", "url:https://example.test/story#verbatim"
    )

    article_meta = ScriptMeta(
        title="Harness engineering",
        kind="article",
        edition="verbatim",
        date="2026-02-11",
        author="Ryan Lopopolo",
        site="OpenAI",
    )
    assert build_intro_text(
        article_meta,
        "This is an AI-narrated reading of {title}{kind_phrase}{date_phrase}.",
    ) == (
        "This is an AI-narrated reading of Harness engineering, by Ryan Lopopolo "
        "at OpenAI, published February 11, 2026."
    )


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("SASE_LISTEN_LIVE") != "1",
    reason="set SASE_LISTEN_LIVE=1 to fetch the real OpenAI article",
)
def test_live_harness_article_outline() -> None:
    page = fetch_page("https://openai.com/index/harness-engineering/")
    article = extract_article(
        page.body.decode("utf-8", errors="replace"), page.final_url
    )
    assert article.words >= 2000
    assert len(article.restored_headings) >= 8
