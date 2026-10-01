"""ID3v2.3 round-trip tests. Owner: audio phase."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from mutagen.id3 import ID3

from sase_listen.audio.cover import COVER_SIZE, generate_title_card
from sase_listen.audio.ffmpeg import resolve_ffmpeg
from sase_listen.audio.mastering import ChapterAudio, assemble, master_to_mp3
from sase_listen.audio.tags import ChapterMark, EpisodeMeta, write_tags

from .helpers import SAMPLE_RATE, sine


def _mp3(tmp_path: Path, duration_s: float = 1.0) -> Path:
    episode, _ = assemble(
        [ChapterAudio(title="Only", segments=[sine(duration_s)])],
        SAMPLE_RATE,
    )
    out = tmp_path / "tagged.mp3"
    master_to_mp3(episode.pcm, SAMPLE_RATE, out, resolve_ffmpeg())
    return out


def _chapter_marks() -> list[ChapterMark]:
    return [
        ChapterMark(title="First", start_ms=0, end_ms=2300),
        ChapterMark(title="Second", start_ms=2300, end_ms=5200),
    ]


def _meta() -> EpisodeMeta:
    return EpisodeMeta(
        title="One Updates Tab",
        author="SASE",
        date="2026-09-14",
        description="A field recording.",
        episode_id="one-updates-tab-3f9c2e",
        source_ref="research:202609/updates.md",
    )


def test_tags_round_trip(tmp_path: Path) -> None:
    mp3 = _mp3(tmp_path)
    cover = generate_title_card("One Updates Tab", kind="research")
    tagged = write_tags(
        mp3, _meta(), _chapter_marks(), duration_ms=5200, cover_jpeg=cover
    )
    assert tagged.duration_ms == 5200
    assert tagged.cover_bytes == len(cover)

    tag = ID3(str(mp3))
    assert tag["TIT2"].text == ["One Updates Tab"]
    assert tag["TPE1"].text == ["SASE"]
    assert tag["TALB"].text == ["Audio Editions"]
    assert tag["TCON"].text == ["Podcast"]
    assert tag["COMM::eng"].text == ["A field recording."]
    assert tag["TLEN"].text == ["5200"]
    txxx = {f.desc: f.text for f in tag.getall("TXXX")}
    assert txxx["SASE_LISTEN_EPISODE"] == ["one-updates-tab-3f9c2e"]
    assert txxx["SASE_LISTEN_SOURCE"] == ["research:202609/updates.md"]
    apic = tag["APIC:Cover"]
    assert apic.mime == "image/jpeg"
    assert apic.data == cover


def test_chapters_and_toc_round_trip(tmp_path: Path) -> None:
    mp3 = _mp3(tmp_path)
    write_tags(mp3, _meta(), _chapter_marks(), duration_ms=5200)
    tag = ID3(str(mp3))

    toc = tag["CTOC:toc"]
    assert list(toc.child_element_ids) == ["chap000", "chap001"]
    assert int(toc.flags) & 0x3 == 0x3  # top-level and ordered.

    first = tag["CHAP:chap000"]
    assert (first.start_time, first.end_time) == (0, 2300)
    assert first.sub_frames["TIT2"].text == ["First"]
    second = tag["CHAP:chap001"]
    assert (second.start_time, second.end_time) == (2300, 5200)
    assert second.sub_frames["TIT2"].text == ["Second"]


def test_tags_saved_as_id3v23(tmp_path: Path) -> None:
    mp3 = _mp3(tmp_path, duration_s=0.5)
    write_tags(mp3, _meta(), [], duration_ms=500)
    raw = mp3.read_bytes()
    assert raw[:3] == b"ID3"
    assert raw[3] == 3  # Major version 3 == ID3v2.3.


def test_cover_dimensions(tmp_path: Path) -> None:
    from PIL import Image

    mp3 = _mp3(tmp_path, duration_s=0.5)
    cover = generate_title_card("Dims")
    write_tags(mp3, _meta(), [], duration_ms=500, cover_jpeg=cover)
    apic = ID3(str(mp3))["APIC:Cover"]
    with Image.open(io.BytesIO(apic.data)) as image:
        assert image.size == (COVER_SIZE, COVER_SIZE)


def test_tags_reject_bad_input(tmp_path: Path) -> None:
    mp3 = _mp3(tmp_path, duration_s=0.5)
    with pytest.raises(ValueError):
        write_tags(mp3, EpisodeMeta(title=""), [], duration_ms=500)
    with pytest.raises(ValueError):
        write_tags(
            mp3,
            _meta(),
            [ChapterMark(title="Flat", start_ms=100, end_ms=100)],
            duration_ms=500,
        )
