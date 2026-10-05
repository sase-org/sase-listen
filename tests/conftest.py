"""Shared fixtures: emulate a tmpfs system temp dir on another filesystem."""

from __future__ import annotations

import errno
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_on_separate_fs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Emulate ``/tmp`` living on a different filesystem (tmpfs).

    Points ``tempfile`` at ``tmp_path / "system-tmp"`` and makes
    ``os.replace``/``os.rename`` raise ``EXDEV`` when exactly one of
    ``src``/``dst`` resolves under it, matching ``rename(2)`` semantics.
    """
    system_tmp = tmp_path / "system-tmp"
    system_tmp.mkdir(parents=True, exist_ok=True)
    real_tmp = system_tmp.resolve()
    monkeypatch.setattr(tempfile, "tempdir", str(system_tmp))

    real_replace = os.replace
    real_rename = os.rename

    def _straddles(src: object, dst: object) -> bool:
        try:
            src_in = Path(os.fspath(src)).resolve().is_relative_to(real_tmp)
            dst_in = Path(os.fspath(dst)).resolve().is_relative_to(real_tmp)
        except OSError:
            return False
        return src_in != dst_in

    def fake_replace(src: object, dst: object, *args: object, **kwargs: object) -> None:
        if _straddles(src, dst):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_replace(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    def fake_rename(src: object, dst: object, *args: object, **kwargs: object) -> None:
        if _straddles(src, dst):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_rename(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", fake_replace)
    monkeypatch.setattr(os, "rename", fake_rename)
    yield system_tmp
