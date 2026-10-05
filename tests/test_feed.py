"""Feed phase tests: feed.xml, publish, retention, auto-publish, doctor.

Owner: feed phase.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sase_listen.cli.app import main
from sase_listen.config import SaseListenConfig, default_config
from sase_listen.errors import SaseListenError
from sase_listen.feed import (
    ITUNES_NS,
    PODCAST_NS,
    build_feed_xml,
    init_feed,
    list_feed_episodes,
    masked_subscribe_url,
    print_qr,
    publish_episode,
    resolve_episode_ref,
    resolve_token,
    source_url_for,
    subscribe_url,
    unpublish_episode,
)

TINY_RESEARCH_SCRIPT = """\
---
narration: 1
title: Research Bite
source: research:202610/fake.md
kind: research
edition: verbatim
producer: agent
---

## Findings

Hello world, this is a short spoken paragraph for the test.
"""

TINY_DOCUMENT_SCRIPT = """\
---
narration: 1
title: Plain Note
kind: document
edition: verbatim
producer: agent
---

## Findings

Hello world, this is a short spoken paragraph for the test.
"""


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XDG dirs and the config at a temp tree (no user state touched)."""
    base = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(base / "missing-config.yml"))
    return base


def _feed_config(
    tmp_path: Path,
    token: str = "test-token-abc",
    auto_publish: bool = False,
    max_episodes: int = 200,
    retention_days: int = 90,
) -> tuple[SaseListenConfig, Path, Path]:
    cfg = default_config()
    feed_dir = tmp_path / "feed"
    lib_dir = tmp_path / "library"
    cfg.feed.dir = str(feed_dir)
    cfg.feed.base_url = "https://example.com:8443"
    cfg.feed.token = token
    cfg.feed.title = "Test Feed"
    cfg.feed.description = "Test description."
    cfg.feed.author = "Test Author"
    cfg.feed.auto_publish = auto_publish
    cfg.feed.max_episodes = max_episodes
    cfg.feed.retention_days = retention_days
    return cfg, feed_dir, lib_dir


def _write_library_episode(
    lib_dir: Path,
    episode_id: str,
    *,
    title: str = "Test Episode",
    created_days_ago: float = 0,
    source_ref: str = "research:202610/fake.md",
    mp3_bytes: bytes = b"FAKE-MP3-DATA-0123456789",
) -> Path:
    created = datetime.now(UTC) - timedelta(days=created_days_ago)
    src = lib_dir / episode_id
    src.mkdir(parents=True, exist_ok=True)
    (src / "test-episode.mp3").write_bytes(mp3_bytes)
    (src / "cover.jpg").write_bytes(b"FAKE-JPEG")
    (src / "chapters.json").write_bytes(
        b'{"version": "1.0", "chapters": [{"startTime": 0.0}]}\n'
    )
    manifest = {
        "schema_version": 1,
        "episode_id": episode_id,
        "title": title,
        "created_at": created.isoformat(timespec="seconds"),
        "source": {"ref": source_ref, "sha256": "abc", "blob": ""},
        "script": {
            "sha256": "def",
            "producer": "agent",
            "edition": "full",
            "words": 10,
        },
        "narrator": {
            "name": "tone",
            "engine": "tone",
            "model": "",
            "voice": "",
            "style_sha256": "00",
        },
        "chunks": [],
        "chapters": [{"title": "Findings", "start_ms": 0, "end_ms": 5000}],
        "audio": {
            "file": "test-episode.mp3",
            "bytes": len(mp3_bytes),
            "duration_s": 5.0,
            "bitrate_kbps": 64,
            "loudness_lufs": -16.0,
            "true_peak_db": -1.5,
        },
        "gates": [],
        "omissions": [],
        "cost_usd_estimate": 0.0,
        "published": False,
    }
    (src / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return src


def _parse(xml_path: Path) -> ET.Element:
    return ET.parse(str(xml_path)).getroot()


def test_init_print_only_creates_nothing(
    isolated: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    info = init_feed("https://example.com:8443", print_only=True)
    assert "token: " in info["snippet"]
    assert info["config_path"] == ""
    assert info["subscribe_url"].endswith("/feed.xml")
    assert "test-token-abc" not in info["subscribe_url"]
    assert "tailscale funnel" in info["funnel_command"]
    assert not (tmp_path / "feed").exists()
    assert main(["feed", "init", "--base-url", "https://h:8443", "--print"]) == 0
    out = capsys.readouterr().out
    assert "feed:" in out and "base_url:" in out


def _subscribe_url_of(output: str) -> str:
    return next(
        line for line in output.splitlines() if line.startswith("Subscribe URL:")
    )


def test_init_writes_config_and_reuses_token(
    isolated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["feed", "init", "--base-url", "https://h:8443"]) == 0
    first = capsys.readouterr().out
    assert "Subscribe URL:" in first and "Serve with:" in first
    assert main(["feed", "init", "--base-url", "https://h:8443"]) == 0
    second = capsys.readouterr().out
    assert "(kept the existing token)" in second
    assert _subscribe_url_of(first) == _subscribe_url_of(second)
    assert main(["feed", "--show-url", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["configured"] is True
    assert payload["url"].startswith("https://h:8443/")
    assert payload["url"].endswith("/feed.xml")


def test_subscribe_url_masking_and_token_command(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _, _ = _feed_config(tmp_path)
    assert "****" in masked_subscribe_url(cfg)
    assert "test-token-abc" in masked_subscribe_url(cfg, show=True)
    cfg.feed.token = ""
    cfg.feed.token_command = "echo commanded-token-9"
    assert resolve_token(cfg) == "commanded-token-9"
    assert subscribe_url(cfg).endswith("/commanded-token-9/feed.xml")
    cfg.feed.token_command = "false"
    try:
        resolve_token(cfg)
    except SaseListenError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected a token error")
    qr = print_qr("https://example.com/x")
    assert qr.strip()


def test_publish_unpublish_roundtrip_and_golden_xml(
    isolated: Path, tmp_path: Path
) -> None:
    cfg, feed_dir, lib_dir = _feed_config(tmp_path)
    _write_library_episode(lib_dir, "ep-one-aaaaaa", title="First Episode")
    result = publish_episode("ep-one-aaaaaa", cfg, library=lib_dir, root=feed_dir)
    assert result["episode_id"] == "ep-one-aaaaaa"
    dest = feed_dir / "episodes" / "ep-one-aaaaaa"
    assert (dest / "test-episode.mp3").is_file()
    assert (dest / "cover.jpg").is_file()
    assert (dest / "chapters.json").is_file()
    assert (feed_dir / "cover.jpg").is_file()
    assert not list(feed_dir.rglob(".tmp-*"))

    root = _parse(feed_dir / "feed.xml")
    assert root.tag == "rss"
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "Test Feed"
    assert channel.findtext(f"{{{ITUNES_NS}}}block") == "yes"
    assert channel.findtext(f"{{{PODCAST_NS}}}locked") == "yes"
    assert channel.findtext(f"{{{ITUNES_NS}}}explicit") == "false"
    items = channel.findall("item")
    assert len(items) == 1
    item = items[0]
    assert item.findtext("title") == "First Episode"
    guid = item.find("guid")
    assert guid is not None and guid.get("isPermaLink") == "false"
    expect_sha = hashlib.sha256(b"FAKE-MP3-DATA-0123456789").hexdigest()[:8]
    assert guid.text == f"ep-one-aaaaaa@{expect_sha}"
    enclosure = item.find("enclosure")
    assert enclosure is not None
    assert enclosure.get("type") == "audio/mpeg"
    assert enclosure.get("length") == str(len(b"FAKE-MP3-DATA-0123456789"))
    assert "/test-token-abc/episodes/ep-one-aaaaaa/" in str(enclosure.get("url"))
    assert item.findtext(f"{{{ITUNES_NS}}}episodeType") == "full"
    assert item.findtext(f"{{{ITUNES_NS}}}duration") == "5"
    img = item.find(f"{{{ITUNES_NS}}}image")
    assert img is not None and img.get("href", "").endswith("cover.jpg")
    chapters = item.find(f"{{{PODCAST_NS}}}chapters")
    assert chapters is not None
    assert chapters.get("type") == "application/json+chapters"
    assert chapters.get("url", "").endswith("chapters.json")
    desc = item.findtext("description") or ""
    assert "Findings" in desc
    assert "https://github.com/sase-org/sase--research/blob/master/" in desc

    out = unpublish_episode("ep-one-aaaaaa", cfg, root=feed_dir)
    assert out["episodes"] == []
    assert not (dest).exists()
    assert _parse(feed_dir / "feed.xml").find("channel/item") is None
    assert not list(feed_dir.rglob(".tmp-*"))


def test_article_feed_item_labels_coverage_byline_and_original_link(
    isolated: Path, tmp_path: Path
) -> None:
    cfg, feed_dir, lib_dir = _feed_config(tmp_path)
    episode = _write_library_episode(lib_dir, "article-full-aaaaaa")
    manifest_path = episode / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["title"] = "Harness Engineering (Full)"
    manifest["source"] = {
        "url": "https://openai.com/index/harness-engineering/",
        "title": "Harness Engineering",
        "author": "Ada Lovelace",
        "site": "OpenAI",
        "date": "2026-02-11",
    }
    manifest["script"]["edition"] = "full"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    publish_episode("article-full-aaaaaa", cfg, library=lib_dir, root=feed_dir)
    item = _parse(feed_dir / "feed.xml").find("channel/item")
    assert item is not None
    assert item.findtext("title") == "Harness Engineering (Full)"
    description = item.findtext("description") or ""
    assert "A full-length narrated adaptation of the whole article" in description
    assert "By Ada Lovelace at OpenAI, published February 11, 2026" in description
    assert (
        '<a href="https://openai.com/index/harness-engineering/">'
        "Read the original article</a>"
    ) in description
    assert "Findings" in description


def test_guid_changes_on_rerender(isolated: Path, tmp_path: Path) -> None:
    cfg, feed_dir, lib_dir = _feed_config(tmp_path)
    _write_library_episode(lib_dir, "ep-re-111111")
    publish_episode("ep-re-111111", cfg, library=lib_dir, root=feed_dir)
    first = _parse(feed_dir / "feed.xml").findtext("channel/item/guid")
    _write_library_episode(
        lib_dir, "ep-re-111111", mp3_bytes=b"RE-RENDERED-MP3-DIFFERENT"
    )
    publish_episode("ep-re-111111", cfg, library=lib_dir, root=feed_dir)
    second = _parse(feed_dir / "feed.xml").findtext("channel/item/guid")
    assert first and second and first != second
    assert first.startswith("ep-re-111111@") and second.startswith("ep-re-111111@")


def test_retention_age_and_max_episodes(isolated: Path, tmp_path: Path) -> None:
    cfg, feed_dir, lib_dir = _feed_config(tmp_path, max_episodes=2)
    _write_library_episode(lib_dir, "ep-old-000001", created_days_ago=100)
    _write_library_episode(lib_dir, "ep-new-000002", created_days_ago=1)
    _write_library_episode(lib_dir, "ep-new-000003", created_days_ago=0)
    publish_episode("ep-old-000001", cfg, library=lib_dir, root=feed_dir)
    # The 100-day-old episode exceeds the 90-day retention immediately.
    assert "ep-old-000001" not in [e.episode_id for e in list_feed_episodes(feed_dir)]
    publish_episode("ep-new-000002", cfg, library=lib_dir, root=feed_dir)
    result = publish_episode("ep-new-000003", cfg, library=lib_dir, root=feed_dir)
    assert result["episodes"] == ["ep-new-000003", "ep-new-000002"]
    assert (lib_dir / "ep-old-000001" / "manifest.json").is_file()


def test_source_url_templates() -> None:
    templates = {"research": "https://example.test/{path}"}
    assert (
        source_url_for({"ref": "research:202610/x.md"}, templates)
        == "https://example.test/202610/x.md"
    )
    assert source_url_for({"path": "/tmp/x.md"}, templates) == ""
    assert source_url_for({"ref": "other:202610/x.md"}, templates) == ""
    assert source_url_for({}, templates) == ""


def test_resolve_episode_ref(isolated: Path, tmp_path: Path) -> None:
    _, _, lib_dir = _feed_config(tmp_path)
    src = _write_library_episode(lib_dir, "ep-x-999999")
    assert resolve_episode_ref("ep-x-999999", library=lib_dir) == "ep-x-999999"
    assert (
        resolve_episode_ref(str(src / "test-episode.mp3"), library=lib_dir)
        == "ep-x-999999"
    )
    assert resolve_episode_ref("", latest=True, library=lib_dir) == "ep-x-999999"
    try:
        resolve_episode_ref("nope-missing", library=lib_dir)
    except SaseListenError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected an unknown-episode error")


def _write_feed_config(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    auto_publish: bool = False,
) -> Path:
    cfg_path = tmp_path / "config.yml"
    feed_dir = tmp_path / "feed"
    cfg_path.write_text(
        "narrator: tone\n"
        "feed:\n"
        f"  dir: {feed_dir}\n"
        "  base_url: https://example.com:8443\n"
        "  token: cli-token-xyz\n"
        "  title: CLI Feed\n"
        f"  auto_publish: {'true' if auto_publish else 'false'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(cfg_path))
    return feed_dir


def _write_script(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_render_publish_and_unpublish_cli(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feed_dir = _write_feed_config(isolated, tmp_path, monkeypatch)
    source = _write_script(tmp_path, "bite_narration.md", TINY_RESEARCH_SCRIPT)
    assert main(["render", source, "-n", "tone", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["published"] is False
    episode_id = payload["episode_id"]
    assert main(["publish", episode_id, "--json"]) == 0
    published = json.loads(capsys.readouterr().out)
    assert published["ok"] is True
    assert published["episode_id"] == episode_id
    assert (feed_dir / "feed.xml").is_file()
    assert main(["publish", "--latest", "--json"]) == 0
    capsys.readouterr()
    assert main(["feed", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["episodes"] == 1
    assert "****" in status["url"]
    assert main(["unpublish", episode_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["feed", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["episodes"] == 0


def test_render_explicit_publish_marks_manifest(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from sase_listen.library import read_manifest

    feed_dir = _write_feed_config(isolated, tmp_path, monkeypatch)
    source = _write_script(tmp_path, "doc_narration.md", TINY_DOCUMENT_SCRIPT)
    assert main(["render", source, "-n", "tone", "--publish", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["published"] is True
    assert read_manifest(payload["episode_id"])["published"] is True
    assert _parse(feed_dir / "feed.xml").find("channel/item") is not None


def test_render_explicit_publish_without_feed_config_fails(
    isolated: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _write_script(tmp_path, "doc_narration.md", TINY_DOCUMENT_SCRIPT)
    assert main(["render", source, "-n", "tone", "--publish", "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == 3


def test_auto_publish_only_for_research(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_feed_config(isolated, tmp_path, monkeypatch, auto_publish=True)
    doc = _write_script(tmp_path, "plain_narration.md", TINY_DOCUMENT_SCRIPT)
    assert main(["render", doc, "-n", "tone", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["published"] is False
    research = _write_script(tmp_path, "res_narration.md", TINY_RESEARCH_SCRIPT)
    assert main(["render", research, "-n", "tone", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["published"] is True
    assert main(["feed", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["episodes"] == 1
    # --no-publish suppresses auto-publish.
    assert main(["render", research, "-n", "tone", "--no-publish", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["published"] is False


def test_feed_status_and_doctor_cli(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feed_dir = _write_feed_config(isolated, tmp_path, monkeypatch)
    assert main(["feed", "--qr"]) == 0
    assert "Episodes: 0" in capsys.readouterr().out
    # Configured but nothing published yet: feed.xml check fails loudly.
    assert main(["doctor", "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    names = {check["name"]: check for check in payload["checks"]}
    for name in ("feed:dir", "feed:base_url", "feed:token", "feed:feed.xml"):
        assert name in names, f"missing doctor check {name}"
    assert names["feed:token"]["ok"] is True
    assert names["feed:feed.xml"]["ok"] is False
    assert feed_dir.is_dir()


def test_empty_feed_xml_is_valid_rss(tmp_path: Path) -> None:
    cfg, _, _ = _feed_config(tmp_path)
    data = build_feed_xml(cfg=cfg, episodes=[], token="tok", now=datetime.now(UTC))
    root = ET.fromstring(data)
    assert root.tag == "rss"
    channel = root.find("channel")
    assert channel is not None
    assert channel.findall("item") == []
    assert channel.findtext(f"{{{ITUNES_NS}}}block") == "yes"
