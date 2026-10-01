"""ffmpeg resolution tests. Owner: audio phase."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from sase_listen.audio.ffmpeg import FfmpegInfo, resolve_ffmpeg
from sase_listen.errors import ExitCode, SaseListenError


@pytest.fixture
def _fresh_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    resolve_ffmpeg.cache_clear()
    monkeypatch.delenv("SASE_LISTEN_FFMPEG", raising=False)
    yield
    resolve_ffmpeg.cache_clear()


def test_resolve_finds_a_working_binary(_fresh_env: None) -> None:
    info = resolve_ffmpeg()
    assert info.source in ("env:SASE_LISTEN_FFMPEG", "PATH", "imageio-ffmpeg")
    assert os.path.isfile(info.exe) or info.source == "PATH"
    assert info.has_libmp3lame
    assert info.has_loudnorm


def test_bundled_binary_has_mastering_codecs(_fresh_env: None) -> None:
    from imageio_ffmpeg import get_ffmpeg_exe

    info = FfmpegInfo(
        exe=get_ffmpeg_exe(),
        source="imageio-ffmpeg",
        has_libmp3lame=True,
        has_loudnorm=True,
    )
    info.require_mastering()  # Must not raise.
    described = info.describe()
    assert described["source"] == "imageio-ffmpeg"
    assert described["has_libmp3lame"] is True


def test_missing_capabilities_refuse_mastering() -> None:
    info = FfmpegInfo(
        exe="/bin/false",
        source="PATH",
        has_libmp3lame=False,
        has_loudnorm=True,
    )
    with pytest.raises(SaseListenError) as excinfo:
        info.require_mastering()
    assert excinfo.value.code == ExitCode.CONFIG
    assert "libmp3lame" in str(excinfo.value)


def test_bad_env_override_is_a_config_error(
    _fresh_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SASE_LISTEN_FFMPEG", "/nonexistent/ffmpeg")
    with pytest.raises(SaseListenError) as excinfo:
        resolve_ffmpeg()
    assert excinfo.value.code == ExitCode.CONFIG
