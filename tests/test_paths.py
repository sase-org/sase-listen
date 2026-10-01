"""Paths tests. Owner: scaffold phase."""

from __future__ import annotations

import os
from pathlib import Path

from sase_listen.paths import cache_dir, config_path, data_dir, library_dir, state_dir


def test_config_path_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SASE_LISTEN_CONFIG", "/tmp/custom.yml")
    assert config_path() == Path("/tmp/custom.yml")


def test_config_path_xdg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SASE_LISTEN_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "sase-listen" / "config.yml"


def test_library_under_data_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert library_dir() == tmp_path / "sase-listen" / "library"
    assert data_dir() == tmp_path / "sase-listen"


def test_cache_and_state_dirs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert str(cache_dir()).startswith(str(tmp_path))
    assert str(state_dir()).startswith(str(tmp_path))
    assert os.path.isabs(str(cache_dir()))
