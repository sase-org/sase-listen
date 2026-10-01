"""Typed config with XDG resolution, env overrides, and origin tracking.

Owner: scaffold phase (shared by every phase).
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sase_listen.paths import config_path

DEFAULT_INTRO_TEMPLATE = (
    "This is an AI-narrated audio edition of {title}{kind_phrase}{date_phrase}."
)
DEFAULT_OUTRO_TEMPLATE = "That's the end of this audio edition of {title}."


def _suggest(name: str, options: list[str]) -> str:
    matches = difflib.get_close_matches(name, options, n=1, cutoff=0.6)
    if matches:
        return f" Did you mean '{matches[0]}'?"
    return ""


@dataclass
class NarratorProfile:
    """A named narrator: engine, model, voice, and style."""

    engine: str = "gemini"
    model: str = ""
    voice: str = ""
    style: str = ""
    speed: float = 1.0
    base_url: str = ""


@dataclass
class EngineConfig:
    """Per-engine credentials and transport tuning."""

    api_key_env: list[str] = field(default_factory=list)
    api_key_command: str = ""
    base_url: str = ""
    concurrency: int = 3
    timeout_s: int = 180
    max_retries: int = 4


@dataclass
class EnginesConfig:
    """Cloud engine configs."""

    gemini: EngineConfig = field(
        default_factory=lambda: EngineConfig(
            api_key_env=[
                "SASE_LISTEN_GEMINI_API_KEY",
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
            ]
        )
    )
    openai: EngineConfig = field(
        default_factory=lambda: EngineConfig(
            api_key_env=["SASE_LISTEN_OPENAI_API_KEY", "OPENAI_API_KEY"]
        )
    )


@dataclass
class AudioConfig:
    """Mastering and gap configuration."""

    bitrate_kbps: int = 64
    sample_rate: int = 24000
    loudness_lufs: float = -16.0
    true_peak_db: float = -1.5
    chunk_gap_s: float = 0.5
    chapter_gap_s: float = 1.2
    intro_gap_s: float = 0.9


@dataclass
class CacheConfig:
    """Chunk cache configuration."""

    max_gb: float = 2.0


@dataclass
class FeedConfig:
    """Private podcast feed configuration (feed phase extends behavior)."""

    dir: str = ""
    base_url: str = ""
    token: str = ""
    token_command: str = ""
    title: str = "SASE Listen"
    description: str = "Narrated audio editions of Markdown."
    author: str = ""
    language: str = "en"
    retention_days: int = 90
    max_episodes: int = 200
    auto_publish: bool = False
    source_url_templates: dict[str, str] = field(
        default_factory=lambda: {
            "research": "https://github.com/sase-org/sase--research/blob/master/{path}"
        }
    )


@dataclass
class SaseListenConfig:
    """Effective configuration with built-in defaults for every section."""

    narrator: str = "gemini"
    narrators: dict[str, NarratorProfile] = field(
        default_factory=lambda: {
            "gemini": NarratorProfile(
                engine="gemini",
                model="gemini-3.8-flash-tts",
                voice="Charon",
                style=(
                    "Calm, clear technical-briefing narrator. Moderate pace. "
                    "Slight emphasis on numbers. Read the text exactly as written."
                ),
            ),
            "gemini-lite": NarratorProfile(
                engine="gemini",
                model="gemini-3.8-flash-lite-tts",
            ),
            "openai": NarratorProfile(
                engine="openai",
                model="gpt-4o-mini-tts-2025-12-15",
                voice="marin",
            ),
            "tone": NarratorProfile(engine="tone"),
        }
    )
    engines: EnginesConfig = field(default_factory=EnginesConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    intro_template: str = DEFAULT_INTRO_TEMPLATE
    outro_template: str = DEFAULT_OUTRO_TEMPLATE
    author: str = ""
    lexicon: str = ""
    cache: CacheConfig = field(default_factory=CacheConfig)
    feed: FeedConfig = field(default_factory=FeedConfig)


_TOP_LEVEL_KEYS = [
    "narrator",
    "narrators",
    "engines",
    "audio",
    "intro_template",
    "outro_template",
    "author",
    "lexicon",
    "cache",
    "feed",
]

_NARRATOR_KEYS = ["engine", "model", "voice", "style", "speed", "base_url"]
_ENGINE_KEYS = [
    "api_key_env",
    "api_key_command",
    "base_url",
    "concurrency",
    "timeout_s",
    "max_retries",
]
_AUDIO_KEYS = [
    "bitrate_kbps",
    "sample_rate",
    "loudness_lufs",
    "true_peak_db",
    "chunk_gap_s",
    "chapter_gap_s",
    "intro_gap_s",
]
_CACHE_KEYS = ["max_gb"]
_FEED_KEYS = [
    "dir",
    "base_url",
    "token",
    "token_command",
    "title",
    "description",
    "author",
    "language",
    "retention_days",
    "max_episodes",
    "auto_publish",
    "source_url_templates",
]


def _check_keys(mapping: dict[str, Any], allowed: list[str], where: str) -> None:
    for key in mapping:
        if key not in allowed:
            raise ValueError(
                f"Unknown config key '{where}.{key}'.{_suggest(key, allowed)}"
            )


def _coerce_narrator(name: str, raw: Any) -> NarratorProfile:
    if not isinstance(raw, dict):
        raise ValueError(f"Unknown config key 'narrators.{name}': expected a mapping.")
    _check_keys(raw, _NARRATOR_KEYS, f"narrators.{name}")
    profile = NarratorProfile()
    if "engine" in raw:
        profile.engine = str(raw["engine"])
    if "model" in raw:
        profile.model = str(raw["model"])
    if "voice" in raw:
        profile.voice = str(raw["voice"])
    if "style" in raw:
        profile.style = str(raw["style"])
    if "speed" in raw:
        profile.speed = float(raw["speed"])
    if "base_url" in raw:
        profile.base_url = str(raw["base_url"])
    return profile


def _coerce_engine(raw: Any, where: str, defaults: EngineConfig) -> EngineConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"Unknown config key '{where}': expected a mapping.")
    _check_keys(raw, _ENGINE_KEYS, where)
    cfg = EngineConfig(
        api_key_env=list(defaults.api_key_env),
        api_key_command=defaults.api_key_command,
        base_url=defaults.base_url,
        concurrency=defaults.concurrency,
        timeout_s=defaults.timeout_s,
        max_retries=defaults.max_retries,
    )
    if "api_key_env" in raw:
        env_val = raw["api_key_env"]
        cfg.api_key_env = (
            [str(v) for v in env_val] if isinstance(env_val, list) else [str(env_val)]
        )
    if "api_key_command" in raw:
        cfg.api_key_command = str(raw["api_key_command"])
    if "base_url" in raw:
        cfg.base_url = str(raw["base_url"])
    if "concurrency" in raw:
        cfg.concurrency = int(raw["concurrency"])
    if "timeout_s" in raw:
        cfg.timeout_s = int(raw["timeout_s"])
    if "max_retries" in raw:
        cfg.max_retries = int(raw["max_retries"])
    return cfg


def default_config() -> SaseListenConfig:
    """Return the built-in defaults."""
    return SaseListenConfig()


def load_config(path: Path | None = None) -> tuple[SaseListenConfig, dict[str, str]]:
    """Load config from file plus env, returning (config, origins).

    Precedence: env > file > built-in defaults. Origins maps dotted field
    paths to one of "default", "file", or "env".
    """
    cfg = default_config()
    origins: dict[str, str] = {}
    for top in _TOP_LEVEL_KEYS:
        origins[top] = "default"

    resolved = path if path is not None else config_path()
    raw: dict[str, Any] = {}
    if resolved.exists():
        with resolved.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Config {resolved} must be a mapping.")
        _check_keys(loaded, _TOP_LEVEL_KEYS, "config")
        raw = loaded
        for key in loaded:
            origins[key] = "file"

    if "narrator" in raw:
        cfg.narrator = str(raw["narrator"])
    if "narrators" in raw:
        if not isinstance(raw["narrators"], dict):
            raise ValueError("Unknown config key 'narrators': expected a mapping.")
        for name, entry in raw["narrators"].items():
            cfg.narrators[str(name)] = _coerce_narrator(str(name), entry)
            origins[f"narrators.{name}"] = "file"
    if "engines" in raw:
        if not isinstance(raw["engines"], dict):
            raise ValueError("Unknown config key 'engines': expected a mapping.")
        for engine_name in raw["engines"]:
            if engine_name not in ("gemini", "openai"):
                raise ValueError(
                    f"Unknown config key 'engines.{engine_name}'."
                    f"{_suggest(engine_name, ['gemini', 'openai'])}"
                )
        defaults = EnginesConfig()
        if "gemini" in raw["engines"]:
            cfg.engines.gemini = _coerce_engine(
                raw["engines"]["gemini"], "engines.gemini", defaults.gemini
            )
            origins["engines.gemini"] = "file"
        if "openai" in raw["engines"]:
            cfg.engines.openai = _coerce_engine(
                raw["engines"]["openai"], "engines.openai", defaults.openai
            )
            origins["engines.openai"] = "file"
    if "audio" in raw:
        if not isinstance(raw["audio"], dict):
            raise ValueError("Unknown config key 'audio': expected a mapping.")
        _check_keys(raw["audio"], _AUDIO_KEYS, "audio")
        for key, value in raw["audio"].items():
            setattr(
                cfg.audio,
                key,
                float(value)
                if isinstance(getattr(cfg.audio, key), float)
                else int(value),
            )
        origins["audio"] = "file"
    if "intro_template" in raw:
        cfg.intro_template = str(raw["intro_template"])
    if "outro_template" in raw:
        cfg.outro_template = str(raw["outro_template"])
    if "author" in raw:
        cfg.author = str(raw["author"])
    if "lexicon" in raw:
        cfg.lexicon = str(raw["lexicon"])
    if "cache" in raw:
        if not isinstance(raw["cache"], dict):
            raise ValueError("Unknown config key 'cache': expected a mapping.")
        _check_keys(raw["cache"], _CACHE_KEYS, "cache")
        if "max_gb" in raw["cache"]:
            cfg.cache.max_gb = float(raw["cache"]["max_gb"])
        origins["cache"] = "file"
    if "feed" in raw:
        if not isinstance(raw["feed"], dict):
            raise ValueError("Unknown config key 'feed': expected a mapping.")
        _check_keys(raw["feed"], _FEED_KEYS, "feed")
        for key, value in raw["feed"].items():
            if key == "source_url_templates":
                if not isinstance(value, dict):
                    msg = "Unknown config key 'feed.source_url_templates'."
                    raise ValueError(msg)
                cfg.feed.source_url_templates = {
                    str(k): str(v) for k, v in value.items()
                }
            else:
                setattr(cfg.feed, key, value)
        origins["feed"] = "file"

    _apply_env_overrides(cfg, origins)
    return cfg, origins


def _apply_env_overrides(cfg: SaseListenConfig, origins: dict[str, str]) -> None:
    """Apply SASE_LISTEN_* env overrides in place."""
    env = os.environ
    if "SASE_LISTEN_NARRATOR" in env:
        cfg.narrator = env["SASE_LISTEN_NARRATOR"]
        origins["narrator"] = "env"
    if "SASE_LISTEN_AUTHOR" in env:
        cfg.author = env["SASE_LISTEN_AUTHOR"]
        origins["author"] = "env"
    if "SASE_LISTEN_LEXICON" in env:
        cfg.lexicon = env["SASE_LISTEN_LEXICON"]
        origins["lexicon"] = "env"
    if "SASE_LISTEN_INTRO_TEMPLATE" in env:
        cfg.intro_template = env["SASE_LISTEN_INTRO_TEMPLATE"]
        origins["intro_template"] = "env"
    if "SASE_LISTEN_OUTRO_TEMPLATE" in env:
        cfg.outro_template = env["SASE_LISTEN_OUTRO_TEMPLATE"]
        origins["outro_template"] = "env"
    if "SASE_LISTEN_CACHE_MAX_GB" in env:
        cfg.cache.max_gb = float(env["SASE_LISTEN_CACHE_MAX_GB"])
        origins["cache"] = "env"
    if "SASE_LISTEN_GEMINI_API_KEY_COMMAND" in env:
        cfg.engines.gemini.api_key_command = env["SASE_LISTEN_GEMINI_API_KEY_COMMAND"]
        origins["engines.gemini"] = "env"
    if "SASE_LISTEN_OPENAI_API_KEY_COMMAND" in env:
        cfg.engines.openai.api_key_command = env["SASE_LISTEN_OPENAI_API_KEY_COMMAND"]
        origins["engines.openai"] = "env"
    if "SASE_LISTEN_FEED_BASE_URL" in env:
        cfg.feed.base_url = env["SASE_LISTEN_FEED_BASE_URL"]
        origins["feed"] = "env"
    if "SASE_LISTEN_FEED_DIR" in env:
        cfg.feed.dir = env["SASE_LISTEN_FEED_DIR"]
        origins["feed"] = "env"


MASKED_FIELDS = {"token", "api_key_command"}


def masked_snapshot(cfg: SaseListenConfig) -> dict[str, Any]:
    """Return the effective config as a dict with secrets masked."""
    return {
        "narrator": cfg.narrator,
        "narrators": {
            name: {
                "engine": p.engine,
                "model": p.model,
                "voice": p.voice,
                "speed": p.speed,
                "base_url": p.base_url,
            }
            for name, p in cfg.narrators.items()
        },
        "engines": {
            "gemini": {
                "api_key_env": cfg.engines.gemini.api_key_env,
                "api_key_command": "***" if cfg.engines.gemini.api_key_command else "",
                "base_url": cfg.engines.gemini.base_url,
                "concurrency": cfg.engines.gemini.concurrency,
                "timeout_s": cfg.engines.gemini.timeout_s,
                "max_retries": cfg.engines.gemini.max_retries,
            },
            "openai": {
                "api_key_env": cfg.engines.openai.api_key_env,
                "api_key_command": "***" if cfg.engines.openai.api_key_command else "",
                "base_url": cfg.engines.openai.base_url,
                "concurrency": cfg.engines.openai.concurrency,
                "timeout_s": cfg.engines.openai.timeout_s,
                "max_retries": cfg.engines.openai.max_retries,
            },
        },
        "audio": {
            "bitrate_kbps": cfg.audio.bitrate_kbps,
            "sample_rate": cfg.audio.sample_rate,
            "loudness_lufs": cfg.audio.loudness_lufs,
            "true_peak_db": cfg.audio.true_peak_db,
            "chunk_gap_s": cfg.audio.chunk_gap_s,
            "chapter_gap_s": cfg.audio.chapter_gap_s,
            "intro_gap_s": cfg.audio.intro_gap_s,
        },
        "intro_template": cfg.intro_template,
        "outro_template": cfg.outro_template,
        "author": cfg.author,
        "lexicon": cfg.lexicon,
        "cache": {"max_gb": cfg.cache.max_gb},
        "feed": {
            "dir": cfg.feed.dir,
            "base_url": cfg.feed.base_url,
            "token": "***" if cfg.feed.token else "",
            "token_command": "***" if cfg.feed.token_command else "",
            "title": cfg.feed.title,
            "description": cfg.feed.description,
            "author": cfg.feed.author,
            "language": cfg.feed.language,
            "retention_days": cfg.feed.retention_days,
            "max_episodes": cfg.feed.max_episodes,
            "auto_publish": cfg.feed.auto_publish,
            "source_url_templates": cfg.feed.source_url_templates,
        },
    }
