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
    RenderPlan,
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
from sase_listen.web.fetch import FetchedPage, fetch_page, load_html_file
from sase_listen.writer.base import WriterReply

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

    pdf = _Response(200, "application/pdf", b"%PDF-1.4 body")
    monkeypatch.setattr(curl_cffi.requests, "get", lambda *a, **k: pdf)
    page = fetch_page("https://example.test")
    assert page.content_type == "application/pdf"
    assert page.body == b"%PDF-1.4 body"
    assert pdf.closed

    octet = _Response(200, "application/octet-stream", b"%PDF-1.4 body")
    monkeypatch.setattr(curl_cffi.requests, "get", lambda *a, **k: octet)
    assert fetch_page("https://example.test").content_type == "application/pdf"

    declared = _Response(200, "application/pdf", b"<html>not a pdf</html>")
    monkeypatch.setattr(curl_cffi.requests, "get", lambda *a, **k: declared)
    with pytest.raises(SaseListenError) as mismatch_error:
        fetch_page("https://example.test")
    assert mismatch_error.value.code == ExitCode.UNEXPECTED
    assert "not a PDF" in str(mismatch_error.value)

    other = _Response(200, "application/json", b"{}")
    monkeypatch.setattr(curl_cffi.requests, "get", lambda *a, **k: other)
    with pytest.raises(SaseListenError) as other_error:
        fetch_page("https://example.test")
    assert other_error.value.code == ExitCode.USAGE
    assert "Only HTML pages and PDF documents" in other_error.value.hint


def test_load_html_file_accepts_pdf(tmp_path: Path) -> None:
    saved = tmp_path / "saved.pdf"
    saved.write_bytes(b"%PDF-1.4 body")
    page = load_html_file("https://example.test/paper", saved)
    assert page.content_type == "application/pdf"
    assert page.final_url == "https://example.test/paper"


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
    assert (
        main(["script", "https://example.test/story", "-e", "verbatim", "--json"]) == 0
    )
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

    url_loaded = load_source("https://example.test/story", edition="verbatim")
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
        load_source("https://example.test/story", edition="digest")
    assert edition_error.value.code == ExitCode.USAGE
    assert "brief, --edition full" in edition_error.value.hint


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
        RenderRequest(source="https://example.test/story", edition="verbatim"),
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


def test_render_full_article_with_fake_writer(
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

    class FakeWriter:
        def write(self, system: str, user: str) -> WriterReply:
            assert "Full" in system
            return WriterReply(
                "## First Section\n\nThe team chose a careful engineering process.\n\n"
                "## The Results\n\nThe team describes what changed for readers.",
                "stub-model-v1",
                100,
                200,
            )

    import sase_listen.writer

    monkeypatch.setattr(
        sase_listen.writer, "create_writer", lambda cfg, **kwargs: FakeWriter()
    )
    cfg = default_config()
    cfg.narrator = "tone"
    result = render(
        RenderRequest(
            source="https://example.test/story",
            edition="full",
            dry_run=False,
        ),
        config=cfg,
        engine=ToneEngine(),
        library_root=tmp_path / "library",
    )
    assert isinstance(result, RenderResult)
    assert result.title == "Article Test (Full)"
    assert result.writer["model_version"] == "stub-model-v1"
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["script"]["edition"] == "full"
    assert manifest["script"]["writer"] == {
        key: result.writer[key]
        for key in ("model", "model_version", "prompt_version", "attempts")
    }
    assert manifest["title"] == "Article Test (Full)"
    assert manifest["episode_id"] == compute_episode_id(
        "Article Test", "url:https://example.test/story#full"
    )
    assert result.script_path.endswith("full_narration.md")
    from mutagen.id3 import ID3

    from sase_listen.web.editions import article_coverage_sentence

    tags = ID3(result.audio_path)
    assert tags.getall("COMM")[0].text == [article_coverage_sentence("full")]


def test_url_defaults_to_brief_and_dry_run_reuses_writer_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(base / "missing-config.yml"))
    monkeypatch.setattr(
        store_module,
        "fetch_page",
        lambda url, **kwargs: FetchedPage(
            url, url, 200, "text/html", _article_html(), "2026-10-04T00:00:00+00:00"
        ),
    )
    calls = 0

    class FakeWriter:
        def write(self, system: str, user: str) -> WriterReply:
            paragraph = (
                "The team carefully describes the engineering process and results. "
            )
            return WriterReply(
                "## The question\n\n"
                + paragraph * 25
                + "\n\n## The evidence\n\n"
                + paragraph * 25,
                "stub-brief-v1",
                50,
                75,
            )

    import sase_listen.writer

    def make_writer(cfg: object, **kwargs: object) -> FakeWriter:
        nonlocal calls
        calls += 1
        return FakeWriter()

    monkeypatch.setattr(sase_listen.writer, "create_writer", make_writer)
    url = "https://example.test/story"
    assert main(["script", url, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["script"].startswith("---\n")
    assert "edition: brief" in payload["script"]
    assert payload["writer"]["model_version"] == "stub-brief-v1"
    assert calls == 1

    cfg = default_config()
    cfg.narrator = "tone"
    outcome = render(
        RenderRequest(source=url, dry_run=True), config=cfg, engine=ToneEngine()
    )
    assert isinstance(outcome, RenderPlan)
    assert outcome.edition == "brief"
    assert outcome.title == "Article Test (Brief)"
    assert outcome.script_path.endswith("brief_narration.md")
    assert outcome.writer["tokens"] == {"input": 50, "output": 75}
    assert calls == 1


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
