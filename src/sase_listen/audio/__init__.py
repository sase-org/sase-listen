"""Mastering and MP3 packaging. Owner: audio phase."""

from sase_listen.audio.cover import (
    COVER_QUALITY,
    COVER_SIZE,
    generate_title_card,
    letterbox_cover,
    resolve_cover,
)
from sase_listen.audio.ffmpeg import ENV_VAR, FfmpegInfo, resolve_ffmpeg
from sase_listen.audio.mastering import (
    AssembledEpisode,
    ChapterAudio,
    LongSilence,
    MasterStats,
    assemble,
    compress_silence,
    master_to_mp3,
    resample,
    trim_silence,
    write_wav,
)
from sase_listen.audio.tags import (
    ChapterMark,
    EpisodeMeta,
    TaggedEpisode,
    write_tags,
)

__all__ = [
    "COVER_QUALITY",
    "COVER_SIZE",
    "ENV_VAR",
    "AssembledEpisode",
    "ChapterAudio",
    "ChapterMark",
    "EpisodeMeta",
    "FfmpegInfo",
    "LongSilence",
    "MasterStats",
    "TaggedEpisode",
    "assemble",
    "compress_silence",
    "generate_title_card",
    "letterbox_cover",
    "master_to_mp3",
    "resample",
    "resolve_cover",
    "resolve_ffmpeg",
    "trim_silence",
    "write_tags",
    "write_wav",
]
