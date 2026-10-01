"""ID3v2.3 tags, chapters, and cover art embedding. Owner: audio phase."""

# mypy: disable-error-code="no-untyped-call"
# mutagen's frame constructors are untyped; the round-trip tests pin behavior.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mutagen.id3 import (
    APIC,
    CHAP,
    COMM,
    CTOC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TLEN,
    TPE1,
    TXXX,
    CTOCFlags,
)

_ALBUM = "Audio Editions"
_GENRE = "Podcast"


@dataclass
class EpisodeMeta:
    """Descriptive metadata stamped into the MP3's ID3 tags."""

    title: str
    author: str = ""
    album: str = _ALBUM
    date: str = ""
    genre: str = _GENRE
    description: str = ""
    episode_id: str = ""
    source_ref: str = ""
    kind: str = "document"


@dataclass(frozen=True)
class ChapterMark:
    """One ID3 chapter with millisecond boundaries."""

    title: str
    start_ms: int
    end_ms: int


@dataclass
class TaggedEpisode:
    """Everything `write_tags` stamped into the file, for manifest use."""

    chapters: list[ChapterMark] = field(default_factory=list)
    duration_ms: int = 0
    cover_bytes: int = 0


def write_tags(
    mp3_path: str | Path,
    meta: EpisodeMeta,
    chapters: list[ChapterMark],
    duration_ms: int,
    cover_jpeg: bytes | None = None,
) -> TaggedEpisode:
    """Stamp ID3v2.3 tags, CHAP/CTOC chapters, and cover art onto an MP3.

    Frames: TIT2, TPE1 (author), TALB, TDRC, TCON ("Podcast"), COMM
    (description), TLEN, TXXX:SASE_LISTEN_EPISODE, TXXX:SASE_LISTEN_SOURCE,
    a front-cover APIC, one CHAP per chapter (each with an embedded TIT2),
    and a top-level ordered CTOC table of contents.
    """
    if not meta.title:
        raise ValueError("Episode metadata needs a title for TIT2.")
    tag = ID3()
    tag.add(TIT2(encoding=3, text=meta.title))
    if meta.author:
        tag.add(TPE1(encoding=3, text=meta.author))
    tag.add(TALB(encoding=3, text=meta.album or _ALBUM))
    if meta.date:
        tag.add(TDRC(encoding=3, text=meta.date))
    tag.add(TCON(encoding=3, text=meta.genre or _GENRE))
    if meta.description:
        tag.add(COMM(encoding=3, lang="eng", desc="", text=meta.description))
    tag.add(TLEN(encoding=3, text=str(duration_ms)))
    if meta.episode_id:
        tag.add(TXXX(encoding=3, desc="SASE_LISTEN_EPISODE", text=meta.episode_id))
    if meta.source_ref:
        tag.add(TXXX(encoding=3, desc="SASE_LISTEN_SOURCE", text=meta.source_ref))
    if cover_jpeg:
        tag.add(
            APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,
                desc="Cover",
                data=cover_jpeg,
            )
        )
    child_ids: list[str] = []
    marks: list[ChapterMark] = []
    for i, chapter in enumerate(chapters):
        if chapter.end_ms <= chapter.start_ms:
            raise ValueError(f"Chapter {chapter.title!r} has a non-positive duration.")
        element_id = f"chap{i:03d}"
        child_ids.append(element_id)
        tag.add(
            CHAP(
                element_id=element_id,
                start_time=chapter.start_ms,
                end_time=chapter.end_ms,
                sub_frames=[TIT2(encoding=3, text=chapter.title)],
            )
        )
        marks.append(chapter)
    if child_ids:
        tag.add(
            CTOC(
                element_id="toc",
                flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
                child_element_ids=child_ids,
                sub_frames=[TIT2(encoding=3, text="Chapters")],
            )
        )
    tag.save(str(mp3_path), v2_version=3)
    return TaggedEpisode(
        chapters=marks,
        duration_ms=duration_ms,
        cover_bytes=len(cover_jpeg) if cover_jpeg else 0,
    )
