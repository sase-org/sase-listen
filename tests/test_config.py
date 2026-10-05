"""Config tests. Owner: scaffold phase."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sase_listen.config import default_config, load_config, masked_snapshot


def test_defaults_cover_shared_contracts() -> None:
    cfg = default_config()
    assert cfg.narrator == "gemini"
    assert cfg.audio.bitrate_kbps == 64
    assert cfg.audio.sample_rate == 24000
    assert cfg.audio.loudness_lufs == -16.0
    assert cfg.audio.true_peak_db == -1.5
    assert cfg.cache.max_gb == 2.0
    assert cfg.feed.retention_days == 90
    assert cfg.feed.max_episodes == 200
    assert "gemini" in cfg.narrators
    assert cfg.engines.gemini.concurrency == 3
    assert cfg.writer.engine == "gemini"
    assert cfg.writer.model == "gemini-3.1-pro-preview"
    assert cfg.writer.temperature == 0.3
    assert cfg.writer.max_attempts == 3
    assert cfg.writer.timeout_s == 300


def test_writer_config_env_override_and_masked_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SASE_LISTEN_WRITER_MODEL", raising=False)
    p = tmp_path / "config.yml"
    p.write_text(
        yaml.safe_dump(
            {
                "writer": {
                    "engine": "gemini",
                    "model": "gemini-custom-pro",
                    "temperature": 0.2,
                    "max_attempts": 4,
                    "timeout_s": 90,
                }
            }
        ),
        encoding="utf-8",
    )
    cfg, origins = load_config(p)
    assert cfg.writer.model == "gemini-custom-pro"
    assert cfg.writer.temperature == 0.2
    assert cfg.writer.max_attempts == 4
    assert cfg.writer.timeout_s == 90
    assert origins["writer"] == "file"
    assert masked_snapshot(cfg)["writer"]["model"] == "gemini-custom-pro"

    monkeypatch.setenv("SASE_LISTEN_WRITER_MODEL", "gemini-env-pro")
    cfg, origins = load_config(p)
    assert cfg.writer.model == "gemini-env-pro"
    assert origins["writer"] == "env"


def test_writer_config_rejects_unsupported_engine(tmp_path: Path) -> None:
    p = tmp_path / "config.yml"
    p.write_text(yaml.safe_dump({"writer": {"engine": "openai"}}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"writer\.engine"):
        load_config(p)


def test_unknown_key_errors_with_suggestion(tmp_path: Path) -> None:
    p = tmp_path / "config.yml"
    p.write_text("narratr: gemini\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Did you mean"):
        load_config(p)


def test_unknown_nested_key_errors(tmp_path: Path) -> None:
    p = tmp_path / "config.yml"
    p.write_text(yaml.safe_dump({"audio": {"bitrate_kbps_typo": 64}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown config key"):
        load_config(p)


def test_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "config.yml"
    p.write_text(yaml.safe_dump({"narrator": "tone"}), encoding="utf-8")
    monkeypatch.setenv("SASE_LISTEN_NARRATOR", "gemini")
    cfg, origins = load_config(p)
    assert cfg.narrator == "gemini"
    assert origins["narrator"] == "env"


def test_feed_host_and_host_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SASE_LISTEN_FEED_HOST", raising=False)
    p = tmp_path / "config.yml"
    p.write_text(
        yaml.safe_dump(
            {
                "feed": {
                    "host": "Apollo.example",
                    "host_ssh": "apollo",
                }
            }
        ),
        encoding="utf-8",
    )
    cfg, origins = load_config(p)
    assert cfg.feed.host == "Apollo.example"
    assert cfg.feed.host_ssh == ["apollo"]
    assert origins["feed"] == "file"
    snap = masked_snapshot(cfg)
    assert snap["feed"]["host"] == "Apollo.example"
    assert snap["feed"]["host_ssh"] == ["apollo"]

    p.write_text(
        yaml.safe_dump(
            {"feed": {"host": "apollo", "host_ssh": ["apollo", "apollo-do"]}}
        ),
        encoding="utf-8",
    )
    cfg, _ = load_config(p)
    assert cfg.feed.host_ssh == ["apollo", "apollo-do"]

    monkeypatch.setenv("SASE_LISTEN_FEED_HOST", "")
    cfg, origins = load_config(p)
    assert cfg.feed.host == ""
    assert origins["feed"] == "env"


def test_feed_host_ssh_rejects_mapping(tmp_path: Path) -> None:
    p = tmp_path / "config.yml"
    p.write_text(yaml.safe_dump({"feed": {"host_ssh": {"a": "b"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="host_ssh"):
        load_config(p)


def test_origins_track_file_vs_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SASE_LISTEN_NARRATOR", raising=False)
    p = tmp_path / "config.yml"
    p.write_text(yaml.safe_dump({"narrator": "tone"}), encoding="utf-8")
    cfg, origins = load_config(p)
    assert cfg.narrator == "tone"
    assert origins["narrator"] == "file"
    assert origins["audio"] == "default"
