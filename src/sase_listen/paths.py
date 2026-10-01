"""XDG path resolution. Owner: scaffold phase (shared by every phase)."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg_dir(env_name: str, default_suffix: str) -> Path:
    override = os.environ.get(env_name)
    if override:
        return Path(override).expanduser()
    home = Path.home()
    return home / default_suffix


def config_home() -> Path:
    """Return $XDG_CONFIG_HOME or ~/.config."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser()
    return Path.home() / ".config"


def data_home() -> Path:
    """Return $XDG_DATA_HOME or ~/.local/share."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser()
    return Path.home() / ".local" / "share"


def cache_home() -> Path:
    """Return $XDG_CACHE_HOME or ~/.cache."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser()
    return Path.home() / ".cache"


def state_home() -> Path:
    """Return $XDG_STATE_HOME or ~/.local/state."""
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg).expanduser()
    return Path.home() / ".local" / "state"


def config_path() -> Path:
    """Return the effective config file path.

    Precedence: $SASE_LISTEN_CONFIG, else $XDG_CONFIG_HOME/sase-listen/config.yml.
    """
    override = os.environ.get("SASE_LISTEN_CONFIG")
    if override:
        return Path(override).expanduser()
    return config_home() / "sase-listen" / "config.yml"


def data_dir() -> Path:
    """Return the library parent dir ($XDG_DATA_HOME/sase-listen)."""
    return data_home() / "sase-listen"


def library_dir() -> Path:
    """Return the episode library dir."""
    return data_dir() / "library"


def feed_dir_default() -> Path:
    """Return the default feed dir (the only directory ever served)."""
    return data_dir() / "feed"


def cache_dir() -> Path:
    """Return the chunk cache dir."""
    return cache_home() / "sase-listen" / "chunks"


def state_dir() -> Path:
    """Return the state dir (locks and debug logs)."""
    return state_home() / "sase-listen"


def locks_dir() -> Path:
    """Return the per-episode lock dir."""
    return state_dir() / "locks"
