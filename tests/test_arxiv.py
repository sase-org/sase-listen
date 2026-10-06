"""arXiv paper URL recognition and canonical PDF resolution."""

from __future__ import annotations

import pytest

from sase_listen.web.arxiv import arxiv_paper_id, arxiv_pdf_url


@pytest.mark.parametrize(
    ("url", "paper_id"),
    [
        ("https://arxiv.org/abs/2602.16844", "2602.16844"),
        ("https://arxiv.org/abs/2602.16844v2", "2602.16844v2"),
        ("http://arxiv.org/abs/0704.0001", "0704.0001"),
        ("https://www.arxiv.org/abs/2602.16844/", "2602.16844"),
        ("https://export.arxiv.org/abs/2602.16844", "2602.16844"),
        ("HTTPS://ArXiv.org/abs/2602.16844", "2602.16844"),
        ("https://arxiv.org/abs/2602.16844?context=cs.AI#x", "2602.16844"),
        ("https://arxiv.org/html/2602.16844v1/#S3", "2602.16844v1"),
        ("https://arxiv.org/pdf/2602.16844.pdf", "2602.16844"),
        ("https://arxiv.org/pdf/2602.16844v3", "2602.16844v3"),
        ("https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001"),
        ("https://arxiv.org/abs/math.GT/0309136v2", "math.GT/0309136v2"),
    ],
)
def test_arxiv_paper_id_positive(url: str, paper_id: str) -> None:
    assert arxiv_paper_id(url) == paper_id
    assert arxiv_pdf_url(url) == f"https://arxiv.org/pdf/{paper_id}"


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/",
        "https://arxiv.org/list/cs.AI/recent",
        "https://arxiv.org/abs/",
        "https://arxiv.org/abs/not-an-id",
        "https://arxiv.org/src/2602.16844",
        "https://example.com/abs/2602.16844",
        "https://notarxiv.org/abs/2602.16844",
        "https://arxiv.org.evil.test/abs/2602.16844",
        "ftp://arxiv.org/abs/2602.16844",
    ],
)
def test_arxiv_paper_id_negative(url: str) -> None:
    assert arxiv_paper_id(url) is None
    assert arxiv_pdf_url(url) is None
