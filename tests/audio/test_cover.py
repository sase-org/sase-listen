"""Cover-art snapshot tests. Owner: audio phase."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from sase_listen.audio.cover import (
    COVER_SIZE,
    generate_title_card,
    letterbox_cover,
    resolve_cover,
)


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_title_card_is_square_progressive_jpeg() -> None:
    data = generate_title_card(
        "One Updates Tab", kind="research", date_text="September 14, 2026"
    )
    assert data[:2] == b"\xff\xd8"  # JPEG magic.
    with _open(data) as image:
        assert image.size == (COVER_SIZE, COVER_SIZE)
        assert image.format == "JPEG"


def test_title_card_is_deterministic() -> None:
    first = generate_title_card("One Updates Tab", kind="research")
    second = generate_title_card("One Updates Tab", kind="research")
    assert first == second


def test_title_card_varies_by_title_and_kind() -> None:
    base = generate_title_card("One Updates Tab")
    other_title = generate_title_card("Another Episode")
    research = generate_title_card("One Updates Tab", kind="research")
    assert base != other_title
    assert base != research


def test_long_title_still_fits_the_square() -> None:
    title = (
        "A very long episode title that must wrap across several lines "
        "without overflowing the cover art square at all"
    )
    data = generate_title_card(title)
    with _open(data) as image:
        assert image.size == (COVER_SIZE, COVER_SIZE)


def test_title_card_rejects_blank_title() -> None:
    with pytest.raises(ValueError):
        generate_title_card("   ")


def _wide_image() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 400), (200, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_letterbox_keeps_aspect_in_square() -> None:
    data = letterbox_cover(_wide_image())
    with _open(data) as image:
        assert image.size == (COVER_SIZE, COVER_SIZE)


def test_letterbox_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        letterbox_cover(b"not an image")


def test_resolve_cover_prefers_supplied_image(tmp_path: Path) -> None:
    supplied = tmp_path / "cover.png"
    supplied.write_bytes(_wide_image())
    assert resolve_cover(supplied, "Title") == letterbox_cover(_wide_image())
    assert resolve_cover(None, "Title") == generate_title_card("Title")
