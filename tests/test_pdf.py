"""PDF source extraction, storage, pipeline, and CLI tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import sase_listen.web.store as store_module
import sase_listen.writer
from pdf_builder import PdfTextRun, build_pdf
from sase_listen.cli.app import main
from sase_listen.config import default_config
from sase_listen.engines.tone import ToneEngine
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.pipeline import RenderRequest, RenderResult, load_source, render
from sase_listen.web.fetch import FetchedPage, is_pdf_bytes
from sase_listen.web.pdf import MAX_PDF_PAGES, extract_pdf
from sase_listen.web.store import acquire, acquire_file
from sase_listen.writer.base import WriterReply
from sase_listen.writer.prompt import WRITER_PROMPT_VERSION, system_prompt

PROSE = (
    "The team carefully describes the engineering process and the results "
    "that followed from each decision they made during the study period. "
)


def _para(seed: str, repeats: int = 3) -> str:
    return f"{seed} " + PROSE * repeats


def _lines(
    texts: list[str], *, y: float, size: float = 9.0, x: float = 72.0
) -> list[PdfTextRun]:
    return [
        PdfTextRun(text=text, size=size, x=x, y=y - index * 14.0)
        for index, text in enumerate(texts)
    ]


def _paper_pdf() -> bytes:
    page1: list[PdfTextRun] = [
        PdfTextRun("TestConf 2026 Proceedings", size=8, y=760),
        PdfTextRun("Test Paper on Listening", size=18, y=730),
        PdfTextRun("Ada Lovelace Alan Turing", size=9, y=712),
        PdfTextRun("Department of Testing, Test University", size=9, y=698),
        PdfTextRun("ada@example.test", size=9, y=684),
        PdfTextRun("CCS Concepts: testing and evaluation methods here.", size=9, y=670),
    ]
    page1 += _lines(
        [
            "Abstract",
            _para("This paper studies listening tools for engineers.", 4),
            "1 Introduction",
            _para("Introduction text with LEFTMARKER beginning here.", 3),
        ],
        y=650,
    )
    # Two-column body: left column first in stream order.
    page1 += _lines(
        [_para("Left column text continues", 3)],
        y=470,
        x=72.0,
    )
    page1 += _lines(
        ["across the column break here.", _para("Right column text follows.", 3)],
        y=470,
        x=320.0,
    )
    page1 += [
        PdfTextRun("This work was supported by a testing grant.", size=6, y=120),
        PdfTextRun("arXiv:2608.25174v1 [cs.SE] 25 Aug 2026", size=8, y=50),
        PdfTextRun("1", size=9, y=30),
    ]
    page2: list[PdfTextRun] = [
        PdfTextRun("TestConf 2026 Proceedings", size=8, y=760),
    ]
    page2 += _lines(
        [
            "2.1 Background",
            _para("Background text gives useful context for readers.", 4),
            "2 Methods",
            _para("Methods text explains the procedure in detail.", 4),
            "3 Results",
            "The engine-",
            "ering process works well for the team in practice.",
            _para("Results text as shown [12, 22] in prior work.", 2),
            "- first item",
            "The team reported a strong result",
        ],
        y=730,
    )
    # Superscript marker: small star at the same baseline.
    page2.append(PdfTextRun("The team reported a strong result", size=9, x=72, y=480))
    page2.append(PdfTextRun("*", size=6, x=280, y=480))
    page2 += _lines(
        [
            "4 Discussion",
            _para("Discussion text weighs the tradeoffs involved.", 4),
            "5 Conclusion",
            _para("Conclusion text summarizes the findings.", 4),
            "References",
            "Reference Entry One describes prior testing work at length.",
            "Reference Entry Two continues the prior testing work at length.",
            "A Appendix",
            "Appendix text survives here with extra testing details.",
        ],
        y=440,
    )
    page2.append(PdfTextRun("2", size=9, y=30))
    return build_pdf(
        [page1, page2],
        info={
            "Title": "Test Paper on Listening",
            "Author": ("Ada Lovelace; Alan Turing; Grace Hopper; John von Neumann"),
            "CreationDate": "D:20260825120000Z",
            "arXivID": "2608.25174",
        },
        outline=[
            (1, "Abstract"),
            (1, "1 Introduction"),
            (2, "2.1 Background"),
            (1, "2 Methods"),
            (1, "3 Results"),
            (2, "3.1 Absent Subsection"),
            (1, "4 Discussion"),
            (1, "5 Conclusion"),
            (1, "References"),
            (1, "A Appendix"),
        ],
    )


def test_pdf_bookmark_paper_extraction() -> None:
    extraction = extract_pdf(_paper_pdf(), source_url="https://arxiv.org/pdf/2608.1")
    article = extraction.article
    assert extraction.outline_source == "bookmarks"
    assert extraction.pages == 2
    assert extraction.authors == [
        "Ada Lovelace",
        "Alan Turing",
        "Grace Hopper",
        "John von Neumann",
    ]
    assert article.author == "Ada Lovelace and colleagues"
    assert article.site == "arXiv"
    assert article.date == "2026-08-25"
    assert article.title == "Test Paper on Listening"
    assert extraction.dropped_small_words > 0
    body = article.markdown
    assert "## Abstract" in body
    assert "## Introduction" in body
    assert "### Background" in body
    assert body.index("## Abstract") < body.index("## Introduction")
    assert "LEFTMARKER" in body
    assert body.index("LEFTMARKER") < body.index("Right column text")
    assert "TestConf 2026" not in body
    assert "ada@example.test" not in body
    assert "Department of Testing" not in body
    assert "CCS Concepts" not in body
    assert "arXiv:2608" not in body
    assert "Reference Entry One" not in body
    assert "Appendix text survives" in body
    assert "## References" not in body
    assert "Absent Subsection" in list(extraction.article.outline)
    assert article.missing_headings == ["Absent Subsection"]
    assert "Introduction" in article.restored_headings
    assert "\n1\n" not in f"\n{body}\n"
    assert "[12" not in body
    assert "The engineering process" in body


def test_pdf_trailing_references_cut_to_end() -> None:
    runs = [
        PdfTextRun("Trailing Refs Title", size=18, y=740),
        PdfTextRun("1 Introduction", size=13, bold=True, y=710),
        PdfTextRun(
            "Intro body prose for the trailing references case. " * 20,
            size=9,
            y=690,
        ),
        PdfTextRun(
            "A second intro paragraph adds more body prose here. " * 12,
            size=9,
            y=676,
        ),
        PdfTextRun("References", size=13, bold=True, y=660),
        PdfTextRun(
            "Reference Entry One describes prior testing work at length. " * 6,
            size=9,
            y=640,
        ),
    ]
    data = build_pdf(
        [runs],
        info={"Title": "Trailing Refs Title", "Author": "Solo Author"},
        outline=[(1, "1 Introduction"), (1, "References")],
    )
    body = extract_pdf(data).article.markdown
    assert "## Introduction" in body
    assert "## References" not in body
    assert "Reference Entry One" not in body


def test_pdf_font_fallback_headings() -> None:
    runs = _lines(
        [
            "Fallback Title Here",
            "Abstract",
            _para("Abstract prose for the fallback case.", 6),
            "1 Introduction",
            _para("Intro prose for the fallback case.", 6),
            "2.1 Details",
            _para("Details prose for the fallback case.", 6),
        ],
        y=740,
    )
    runs[0] = PdfTextRun("Fallback Title Here", size=18, y=740)
    runs[1] = PdfTextRun("Abstract", size=9, bold=True, y=726)
    runs[3] = PdfTextRun("1 Introduction", size=13, bold=True, y=698 - 28)
    runs[5] = PdfTextRun("2.1 Details", size=13, bold=True, y=698 - 56)
    data = build_pdf(
        [runs],
        info={"Title": "Fallback Title Here", "Author": "Solo Author"},
    )
    extraction = extract_pdf(data)
    assert extraction.outline_source == "fonts"
    assert "## Abstract" in extraction.article.markdown
    assert "## Introduction" in extraction.article.markdown
    assert "### Details" in extraction.article.markdown


def test_pdf_inline_cleanup_features() -> None:
    runs = _lines(
        [
            "Cleanup Title Here",
            "The engine-",
            "ering process works well for the team in practice.",
            _para("Findings as shown [3] and [4-6] in prior work.", 7),
            "- first item",
            "- second item",
        ],
        y=740,
    )
    runs[0] = PdfTextRun("Cleanup Title Here", size=18, y=740)
    data = build_pdf(
        [runs], info={"Title": "Cleanup Title Here", "Author": "Solo Author"}
    )
    body = extract_pdf(data).article.markdown
    assert "The engineering process" in body
    assert "[3]" not in body
    assert "- first item" in body


@pytest.mark.parametrize(
    ("raw_author", "expected"),
    [
        ("Solo Author", "Solo Author"),
        ("A One and B Two", "A One and B Two"),
        ("A One; B Two; C Three", "A One, B Two, and C Three"),
        ("A One; B Two; C Three; D Four", "A One and colleagues"),
        ("A One, B Two, C Three", "A One, B Two, and C Three"),
    ],
)
def test_pdf_author_credit(raw_author: str, expected: str) -> None:
    runs = [
        PdfTextRun("Credit Title Here", size=18, y=740),
        PdfTextRun(_para("Credit body prose for the author case.", 8), size=9, y=710),
    ]
    data = build_pdf([runs], info={"Title": "Credit Title Here", "Author": raw_author})
    assert extract_pdf(data).article.author == expected


def test_pdf_creation_date_and_implausible_title() -> None:
    runs = [
        PdfTextRun("Real Paper Title Here", size=16, y=740),
        PdfTextRun(_para("Dated body prose for the metadata case.", 8), size=9, y=710),
    ]
    data = build_pdf(
        [runs],
        info={
            "Title": "Microsoft Word - draft.docx",
            "Author": "Solo Author",
            "CreationDate": "D:20260102030405Z",
        },
    )
    article = extract_pdf(data).article
    assert article.title == "Real Paper Title Here"
    assert article.date == "2026-01-02"


def test_pdf_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SaseListenError) as not_pdf:
        extract_pdf(b"not a pdf at all, just text")
    assert not_pdf.value.code == ExitCode.USAGE
    assert "Could not read the PDF" in str(not_pdf.value)

    tiny = build_pdf(
        [[PdfTextRun("Tiny", size=18, y=740)]],
        info={"Title": "Tiny", "Author": "Solo Author"},
    )
    with pytest.raises(SaseListenError) as short:
        extract_pdf(tiny)
    assert short.value.code == ExitCode.UNEXPECTED
    assert "too short" in str(short.value)
    assert "OCR is not supported" in short.value.hint

    two_pages = _paper_pdf()
    monkeypatch.setattr("sase_listen.web.pdf.MAX_PDF_PAGES", 1)
    with pytest.raises(SaseListenError) as capped:
        extract_pdf(two_pages)
    assert capped.value.code == ExitCode.USAGE
    assert "2 pages" in str(capped.value)
    assert str(MAX_PDF_PAGES) != "1"  # sanity: module constant untouched

    import pdfminer.pdfdocument

    def _locked(*args: object, **kwargs: object) -> object:
        raise pdfminer.pdfdocument.PDFPasswordIncorrect("password required")

    monkeypatch.setattr(pdfminer.pdfdocument, "PDFDocument", _locked)
    with pytest.raises(SaseListenError) as locked:
        extract_pdf(b"%PDF-1.4 locked")
    assert locked.value.code == ExitCode.USAGE
    assert "password-protected" in str(locked.value)
    _ = tmp_path


def test_pdf_bytes_sniff() -> None:
    assert is_pdf_bytes(b"%PDF-1.4 hello")
    assert is_pdf_bytes(b"\n\n%PDF-1.4 hello")
    assert not is_pdf_bytes(b"<html>hello</html>")


def _stub_pdf_fetch(url: str, **kwargs: Any) -> FetchedPage:
    return FetchedPage(
        url,
        url,
        200,
        "application/pdf",
        _paper_pdf(),
        "2026-10-05T00:00:00+00:00",
    )


def test_store_pdf_url_acquire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store_module, "fetch_page", _stub_pdf_fetch)
    first = acquire("https://arxiv.org/pdf/2608.25174")
    assert first.page_path.name == "source.pdf"
    assert first.page_path.read_bytes() == _paper_pdf()
    assert first.source_format == "pdf"
    metadata = json.loads((first.directory / "source.json").read_text())
    assert metadata["format"] == "pdf"
    assert metadata["pdf_sha256"] == hashlib.sha256(_paper_pdf()).hexdigest()
    assert metadata["pages"] == 2
    assert metadata["authors"][0] == "Ada Lovelace"
    assert metadata["outline"]["source"] == "bookmarks"
    assert metadata["extractor"]["name"] == "pdfminer.six"
    assert "html_sha256" not in metadata
    second = acquire("https://arxiv.org/pdf/2608.25174")
    assert second.reused is True
    assert second.directory == first.directory


def test_store_acquire_file_caches_by_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    local = tmp_path / "paper.pdf"
    local.write_bytes(_paper_pdf())
    first = acquire_file(local)
    assert first.page_path.name == "source.pdf"
    assert first.metadata["format"] == "pdf"
    assert first.metadata["file"] == str(local.resolve())
    assert first.metadata["url"] == ""
    second = acquire_file(local)
    assert second.reused is True
    assert second.directory == first.directory
    missing = tmp_path / "absent.pdf"
    with pytest.raises(SaseListenError) as error:
        acquire_file(missing)
    assert error.value.code == ExitCode.USAGE
    not_pdf = tmp_path / "notes.pdf"
    not_pdf.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(SaseListenError) as not_pdf_error:
        acquire_file(not_pdf)
    assert not_pdf_error.value.code == ExitCode.USAGE


def test_pipeline_pdf_file_verbatim_stable_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    local = tmp_path / "paper.pdf"
    local.write_bytes(_paper_pdf())
    first = load_source(str(local), edition="verbatim")
    second = load_source(str(local), edition="verbatim")
    assert first.source_key == second.source_key
    assert first.source_key.startswith("pdf:")
    assert first.source_key.endswith("#verbatim")
    assert first.script.meta.kind == "article"
    assert "## Introduction" in first.script_text


def test_pipeline_pdf_url_kinds_and_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store_module, "fetch_page", _stub_pdf_fetch)
    loaded = load_source("https://arxiv.org/pdf/2608.25174", edition="verbatim")
    assert loaded.source_key == ("url:https://arxiv.org/pdf/2608.25174#verbatim")
    assert loaded.script.meta.kind == "article"
    assert "## Introduction" in loaded.script_text

    class FakeWriter:
        def write(self, system: str, user: str) -> WriterReply:
            assert "PDF source rules" in system
            assert "Format: PDF document (2 pages)" in user
            assert "Document outline (headings in source order)" in user
            paragraph = "The team carefully describes the process. "
            return WriterReply(
                "## The question\n\n"
                + paragraph * 25
                + "\n\n## The evidence\n\n"
                + paragraph * 25,
                "stub-pdf-v1",
                50,
                75,
            )

    monkeypatch.setattr(
        sase_listen.writer, "create_writer", lambda cfg, **kwargs: FakeWriter()
    )
    brief = load_source("https://arxiv.org/pdf/2608.25174", edition="brief")
    assert brief.writer["model_version"] == "stub-pdf-v1"
    html_system = system_prompt("full")
    assert "PDF source rules" not in html_system
    pdf_system = system_prompt("full", source_format="pdf")
    assert "PDF source rules" in pdf_system
    assert WRITER_PROMPT_VERSION == 1


def test_cli_script_pdf_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    local = tmp_path / "paper.pdf"
    local.write_bytes(_paper_pdf())
    assert main(["script", str(local), "-e", "verbatim", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["outline"]["found"] >= 8
    assert "## Introduction" in payload["script"]
    assert payload["source_dir"]


def test_render_pdf_url_tone_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(store_module, "fetch_page", _stub_pdf_fetch)
    cfg = default_config()
    cfg.narrator = "tone"
    cfg.feed.auto_publish = False
    outcome = render(
        RenderRequest(source="https://arxiv.org/pdf/2608.25174", edition="verbatim"),
        config=cfg,
        engine=ToneEngine(),
        library_root=tmp_path / "library",
    )
    assert isinstance(outcome, RenderResult)
    assert outcome.title == "Test Paper on Listening (Reading)"
    manifest = json.loads(Path(outcome.manifest_path).read_text(encoding="utf-8"))
    assert manifest["source"]["url"] == "https://arxiv.org/pdf/2608.25174"


def test_store_arxiv_abs_url_fetches_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sase_listen.paths import sources_dir

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    fetched: list[str] = []

    def _recording_fetch(url: str, **kwargs: Any) -> FetchedPage:
        fetched.append(url)
        return _stub_pdf_fetch(url, **kwargs)

    monkeypatch.setattr(store_module, "fetch_page", _recording_fetch)
    steps: list[str] = []
    first = acquire(
        "https://arxiv.org/abs/2608.25174?context=cs.SE", on_step=steps.append
    )
    assert fetched == ["https://arxiv.org/pdf/2608.25174"]
    assert first.source_format == "pdf"
    assert first.metadata["url"] == "https://arxiv.org/pdf/2608.25174"
    assert "fetching the arXiv PDF for 2608.25174" in steps
    second = acquire("https://arxiv.org/pdf/2608.25174")
    third = acquire("https://www.arxiv.org/abs/2608.25174")
    assert second.reused is True
    assert third.reused is True
    assert second.directory == first.directory
    assert third.directory == first.directory
    assert fetched == ["https://arxiv.org/pdf/2608.25174"]
    index = json.loads((sources_dir() / "index.json").read_text(encoding="utf-8"))
    assert not any("/abs/" in key for key in index)


def test_store_arxiv_abs_with_html_file_keeps_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    def _no_fetch(url: str, **kwargs: Any) -> FetchedPage:
        raise AssertionError("fetch_page must not be called with --html FILE")

    monkeypatch.setattr(store_module, "fetch_page", _no_fetch)
    saved = tmp_path / "saved.pdf"
    saved.write_bytes(_paper_pdf())
    acquired = acquire("https://arxiv.org/abs/2608.25174", html_file=saved)
    assert acquired.metadata["url"] == "https://arxiv.org/abs/2608.25174"


def test_pipeline_arxiv_abs_url_matches_pdf_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(store_module, "fetch_page", _stub_pdf_fetch)
    loaded = load_source("https://arxiv.org/abs/2608.25174", edition="verbatim")
    assert loaded.source_key == "url:https://arxiv.org/pdf/2608.25174#verbatim"
    assert loaded.source_url == "https://arxiv.org/pdf/2608.25174"


@pytest.mark.live
def test_live_arxiv_pdf_url_resolves_to_pdf() -> None:
    import os

    if os.environ.get("SASE_LISTEN_LIVE") != "1":
        pytest.skip("set SASE_LISTEN_LIVE=1 to fetch the real arXiv paper")
    from sase_listen.web.arxiv import arxiv_pdf_url
    from sase_listen.web.fetch import fetch_page as live_fetch

    resolved = arxiv_pdf_url("https://arxiv.org/abs/2608.25174")
    assert resolved == "https://arxiv.org/pdf/2608.25174"
    page = live_fetch(resolved or "")
    assert page.content_type == "application/pdf"


@pytest.mark.live
def test_live_arxiv_pdf_extraction() -> None:
    import os

    if os.environ.get("SASE_LISTEN_LIVE") != "1":
        pytest.skip("set SASE_LISTEN_LIVE=1 to fetch the real arXiv paper")
    from sase_listen.web.fetch import fetch_page as live_fetch

    page = live_fetch("https://arxiv.org/pdf/2608.25174")
    assert page.content_type == "application/pdf"
    extraction = extract_pdf(page.body, source_url=page.final_url)
    article = extraction.article
    assert article.title == "Model-Based Agentic Software Engineering"
    assert article.site == "arXiv"
    assert article.date == "2026-08-27"
    assert len(article.restored_headings) >= 20
    assert "Davis et al." not in article.markdown
    assert "@purdue.edu" not in article.markdown
    assert "[12, 22]" not in article.markdown
    assert "## References" not in article.markdown
    assert 4000 <= article.words <= 8000
