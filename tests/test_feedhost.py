"""Feed-host phase: SSH publish, receive, outbox, and feed lock."""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import tarfile
import threading
from pathlib import Path
from typing import Any

import pytest

from sase_listen.cli.app import main
from sase_listen.config import default_config
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.feed import feed_lock, list_feed_episodes, publish_episode
from sase_listen.feedhost import (
    FeedHostUnreachable,
    feed_role,
    flush_pending,
    local_hostname,
    pack_episode,
    pending_publishes,
    publish_any,
    queue_publish,
    receive_episode,
    refuse_if_misrouted,
    run_remote,
    ssh_argv,
    ssh_destinations,
)
from sase_listen.paths import library_dir
from test_feed import (
    TINY_RESEARCH_SCRIPT,
    _feed_config,
    _write_library_episode,
    _write_script,
)

FAKE_SSH = r"""#!/usr/bin/env python3
import json, os, shlex, subprocess, sys

def parse(argv):
    i = 0
    while i < len(argv):
        if argv[i] == "-o":
            i += 2
            continue
        dest = argv[i]
        cmd = argv[i + 1] if i + 1 < len(argv) else ""
        return dest, cmd
    return "", ""

dest, cmd = parse(sys.argv[1:])
spec_path = os.environ.get("SASE_LISTEN_FAKE_SSH_SPEC", "")
spec = json.loads(open(spec_path, encoding="utf-8").read()) if spec_path else {}
entry = spec.get(dest, spec.get("*", {"kind": "fail"}))
kind = entry.get("kind", "fail")
if kind == "fail":
    sys.stderr.write(entry.get("stderr", "ssh: failed") + "\n")
    raise SystemExit(int(entry.get("exit", 255)))
if kind == "old":
    sys.stderr.write(
        "sase-listen: error: argument action: invalid choice: 'receive'\n"
    )
    raise SystemExit(2)
if kind == "json":
    sys.stdout.write(entry.get("stdout", "{}"))
    sys.stderr.write(entry.get("stderr", ""))
    raise SystemExit(int(entry.get("exit", 0)))
if kind == "exec":
    env = os.environ.copy()
    for key, val in entry.get("env", {}).items():
        env[key] = val
    env["SASE_LISTEN_REMOTE_CALL"] = "1"
    env["SASE_LISTEN_FEED_HOST"] = ""
    marker = "sase-listen"
    idx = cmd.find(marker)
    rest = cmd[idx + len(marker) :].strip() if idx >= 0 else ""
    args = shlex.split(rest)
    py = entry.get("python", sys.executable)
    blob = sys.stdin.buffer.read()
    proc = subprocess.run(
        [py, "-m", "sase_listen", *args], env=env, input=blob, check=False
    )
    raise SystemExit(proc.returncode)
raise SystemExit(255)
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


def _install_fake_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: dict[str, Any]
) -> Path:
    script = tmp_path / "fake-ssh"
    script.write_text(FAKE_SSH, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    spec_path = tmp_path / "fake-ssh-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setenv("SASE_LISTEN_SSH", str(script))
    monkeypatch.setenv("SASE_LISTEN_FAKE_SSH_SPEC", str(spec_path))
    return script


def test_role_detection_empty_matching_fqdn_env(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = default_config()
    assert feed_role(cfg) == "local"
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "apollo")
    cfg.feed.host = "Apollo.tailnet.example"
    assert feed_role(cfg) == "local"
    cfg.feed.host = "athena"
    assert feed_role(cfg) == "remote"
    monkeypatch.setenv("SASE_LISTEN_FEED_HOST", "apollo")
    from sase_listen.config import load_config

    loaded, _ = load_config()
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    assert feed_role(loaded) == "remote"
    monkeypatch.setenv("SASE_LISTEN_FEED_HOST", "")
    loaded, _ = load_config()
    assert feed_role(loaded) == "local"


def test_remote_call_refuses_when_not_host(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SASE_LISTEN_REMOTE_CALL", "1")
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    cfg = default_config()
    cfg.feed.host = "apollo"
    with pytest.raises(SaseListenError, match="not the feed host") as caught:
        refuse_if_misrouted(cfg)
    assert caught.value.code == ExitCode.CONFIG
    cfg.feed.host = "athena"
    refuse_if_misrouted(cfg)


def test_ssh_argv_and_destinations() -> None:
    cfg = default_config()
    cfg.feed.host = "apollo"
    assert ssh_destinations(cfg) == ["apollo"]
    cfg.feed.host_ssh = ["apollo", "apollo-do"]
    assert ssh_destinations(cfg) == ["apollo", "apollo-do"]
    argv = ssh_argv("apollo", ["feed", "receive", "ep-1", "--json"])
    assert argv[0] == "ssh"
    assert "BatchMode=yes" in argv
    assert "ConnectTimeout=10" in argv
    assert argv[-2] == "apollo"
    cmd = argv[-1]
    assert "SASE_LISTEN_REMOTE_CALL=1" in cmd
    assert "$HOME/.local/bin" in cmd
    assert "sase-listen feed receive ep-1 --json" in cmd


def test_pack_receive_round_trip(isolated: Path, tmp_path: Path) -> None:
    cfg, _feed_dir, lib_dir = _feed_config(tmp_path)
    _write_library_episode(lib_dir, "ep-one-aaaaaa", title="First Episode")
    blob = pack_episode("ep-one-aaaaaa", lib_dir)
    host_lib = tmp_path / "host-library"
    host_feed = tmp_path / "host-feed"
    cfg.feed.dir = str(host_feed)
    result = receive_episode("ep-one-aaaaaa", blob, cfg, library=host_lib)
    assert "****" in result["item_url"]
    assert "test-token-abc" not in result["item_url"]
    assert (host_lib / "ep-one-aaaaaa" / "manifest.json").is_file()
    assert (host_feed / "feed.xml").is_file()
    ids = [entry.episode_id for entry in list_feed_episodes(host_feed)]
    assert ids == ["ep-one-aaaaaa"]


def _malicious_tar(*, name: str, data: bytes = b"x", is_dir: bool = False) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        if is_dir:
            info.type = tarfile.DIRTYPE
            info.size = 0
            tar.addfile(info)
        else:
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_receive_rejects_bad_archives(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _, lib_dir = _feed_config(tmp_path)
    _write_library_episode(lib_dir, "ep-one-aaaaaa")
    good = pack_episode("ep-one-aaaaaa", lib_dir)
    host_lib = tmp_path / "host-library"
    with pytest.raises(SaseListenError, match="plain file name"):
        receive_episode("ep-one-aaaaaa", _malicious_tar(name="../etc/passwd"), cfg)
    with pytest.raises(SaseListenError, match="directory"):
        receive_episode(
            "ep-one-aaaaaa", _malicious_tar(name="subdir", is_dir=True), cfg
        )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="link.mp3")
        info.type = tarfile.SYMTYPE
        info.linkname = "other"
        tar.addfile(info)
    with pytest.raises(SaseListenError, match="symlink"):
        receive_episode("ep-one-aaaaaa", buf.getvalue(), cfg)
    with pytest.raises(SaseListenError, match="does not match"):
        receive_episode("other-id-bbbbbb", good, cfg, library=host_lib)
    with pytest.raises(SaseListenError, match="missing manifest"):
        receive_episode("ep-one-aaaaaa", _malicious_tar(name="only.mp3"), cfg)
    manifest_only = json.dumps(
        {"episode_id": "ep-one-aaaaaa", "audio": {"file": "missing.mp3"}}
    ).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_only)
        tar.addfile(info, io.BytesIO(manifest_only))
    with pytest.raises(SaseListenError, match="missing the manifest audio"):
        receive_episode("ep-one-aaaaaa", buf.getvalue(), cfg)
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    cfg.feed.host = "elsewhere"
    with pytest.raises(SaseListenError, match="not the feed host"):
        receive_episode("ep-one-aaaaaa", good, cfg)


def test_receive_rejects_oversize_and_too_many(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sase_listen import feedhost as fh

    cfg, _, _ = _feed_config(tmp_path)
    monkeypatch.setattr(fh, "MAX_RECEIVE_BYTES", 8)
    with pytest.raises(SaseListenError, match="256 MiB"):
        receive_episode("ep-one-aaaaaa", b"0123456789", cfg)
    monkeypatch.setattr(fh, "MAX_RECEIVE_BYTES", 256 * 1024 * 1024)
    monkeypatch.setattr(fh, "MAX_RECEIVE_MEMBERS", 1)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in ("a.txt", "b.txt"):
            payload = b"x"
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(SaseListenError, match="too many members"):
        receive_episode("ep-one-aaaaaa", buf.getvalue(), cfg)


def test_pack_refuses_missing_manifest(isolated: Path, tmp_path: Path) -> None:
    lib = tmp_path / "library"
    (lib / "ghost-000001").mkdir(parents=True)
    with pytest.raises(SaseListenError, match="no manifest"):
        pack_episode("ghost-000001", lib)


def test_run_remote_fallback_error_and_too_old(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = default_config()
    cfg.feed.host = "apollo"
    cfg.feed.host_ssh = ["down", "ok"]
    _install_fake_ssh(
        tmp_path,
        monkeypatch,
        {
            "down": {"kind": "fail", "exit": 255, "stderr": "ssh: connect failed"},
            "ok": {
                "kind": "json",
                "stdout": json.dumps({"ok": True, "configured": True, "episodes": 3}),
            },
            "err": {
                "kind": "json",
                "exit": 1,
                "stdout": json.dumps(
                    {
                        "ok": False,
                        "error": {"code": 1, "message": "boom", "hint": "fix it"},
                    }
                ),
            },
            "old": {"kind": "old"},
        },
    )
    payload, dest = run_remote(cfg, ["feed"])
    assert dest == "ok"
    assert payload["episodes"] == 3

    cfg.feed.host_ssh = ["err"]
    with pytest.raises(SaseListenError, match="on apollo: boom") as caught:
        run_remote(cfg, ["feed"])
    assert caught.value.hint == "fix it"
    assert caught.value.code == ExitCode.UNEXPECTED

    cfg.feed.host_ssh = ["old"]
    with pytest.raises(SaseListenError, match="too old to receive"):
        run_remote(cfg, ["feed", "receive", "ep-1"])

    cfg.feed.host_ssh = ["down"]
    with pytest.raises(FeedHostUnreachable, match="tried down"):
        run_remote(cfg, ["feed"])


def test_end_to_end_remote_auto_publish(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    host_root = tmp_path / "host"
    host_root.mkdir()
    host_cfg = host_root / "config.yml"
    host_feed = host_root / "feed"
    host_data = host_root / "data"
    host_cfg.write_text(
        "narrator: tone\n"
        "feed:\n"
        f"  dir: {host_feed}\n"
        "  base_url: https://example.com:8443\n"
        "  token: host-token-xyz\n"
        "  title: Host Feed\n",
        encoding="utf-8",
    )
    client_cfg = tmp_path / "client.yml"
    client_cfg.write_text(
        "narrator: tone\n"
        "feed:\n"
        "  host: testhost\n"
        "  host_ssh: [testhost]\n"
        f"  dir: {tmp_path / 'client-feed'}\n"
        "  base_url: https://example.com:8443\n"
        "  token: client-token-should-not-leak\n"
        "  auto_publish: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(client_cfg))
    _install_fake_ssh(
        tmp_path,
        monkeypatch,
        {
            "testhost": {
                "kind": "exec",
                "python": sys.executable,
                "env": {
                    "XDG_CONFIG_HOME": str(host_root / "xdg-config"),
                    "XDG_DATA_HOME": str(host_data),
                    "XDG_CACHE_HOME": str(host_root / "cache"),
                    "XDG_STATE_HOME": str(host_root / "state"),
                    "SASE_LISTEN_CONFIG": str(host_cfg),
                },
            }
        },
    )

    source = _write_script(tmp_path, "bite_narration.md", TINY_RESEARCH_SCRIPT)
    assert main(["render", source, "-n", "tone", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["published"] is True
    assert payload["publish_host"] == "testhost"
    assert payload["publish_queued"] is False
    episode_id = payload["episode_id"]
    from sase_listen.library import read_manifest

    assert read_manifest(episode_id)["published"] is True
    xml = (host_feed / "feed.xml").read_text(encoding="utf-8")
    assert episode_id in xml
    assert "host-token-xyz" in xml
    assert "client-token-should-not-leak" not in xml


def test_outbox_unreachable_then_pending(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    cfg, _feed_dir, lib_dir = _feed_config(tmp_path)
    cfg.feed.host = "apollo"
    cfg.feed.host_ssh = ["down"]
    _write_library_episode(lib_dir, "ep-queued-111111")
    _install_fake_ssh(
        tmp_path,
        monkeypatch,
        {"down": {"kind": "fail", "exit": 255, "stderr": "ssh: down"}},
    )
    with pytest.raises(FeedHostUnreachable):
        publish_any("ep-queued-111111", cfg, library=lib_dir)
    queue_publish("ep-queued-111111", "ssh: down")
    pending = pending_publishes()
    assert len(pending) == 1
    assert pending[0]["episode_id"] == "ep-queued-111111"

    host_lib = tmp_path / "host-lib"
    host_feed = tmp_path / "host-feed"
    host_cfg_path = tmp_path / "host.yml"
    host_cfg_path.write_text(
        "feed:\n"
        f"  dir: {host_feed}\n"
        "  base_url: https://example.com:8443\n"
        "  token: host-token-xyz\n",
        encoding="utf-8",
    )
    _install_fake_ssh(
        tmp_path,
        monkeypatch,
        {
            "apollo": {
                "kind": "exec",
                "python": sys.executable,
                "env": {
                    "XDG_CONFIG_HOME": str(tmp_path / "h-config"),
                    "XDG_DATA_HOME": str(host_lib.parent / "h-data"),
                    "XDG_CACHE_HOME": str(tmp_path / "h-cache"),
                    "XDG_STATE_HOME": str(tmp_path / "h-state"),
                    "SASE_LISTEN_CONFIG": str(host_cfg_path),
                    "SASE_LISTEN_FEED_DIR": str(host_feed),
                },
            }
        },
    )
    cfg.feed.host_ssh = ["apollo"]
    dest = library_dir() / "ep-queued-111111"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(lib_dir / "ep-queued-111111", dest)
    result = flush_pending(cfg)
    assert result["published"] == ["ep-queued-111111"]
    assert result["failed"] == []
    assert pending_publishes() == []
    assert (host_feed / "feed.xml").is_file()


def test_publish_cli_queues_on_remote_failure(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    cfg_path = tmp_path / "config.yml"
    _write_library_episode(
        Path(os.environ["XDG_DATA_HOME"]) / "sase-listen" / "library",
        "ep-cli-222222",
    )
    cfg_path.write_text(
        "feed:\n"
        "  host: apollo\n"
        "  host_ssh: [down]\n"
        "  base_url: https://example.com:8443\n"
        "  token: tok\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(cfg_path))
    _install_fake_ssh(
        tmp_path,
        monkeypatch,
        {"down": {"kind": "fail", "exit": 255, "stderr": "ssh: down"}},
    )
    assert main(["publish", "ep-cli-222222", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "publish --pending" in payload["error"]["hint"]
    assert pending_publishes()[0]["episode_id"] == "ep-cli-222222"


def test_feed_lock_serializes_and_times_out(isolated: Path, tmp_path: Path) -> None:
    cfg, feed_dir, lib_dir = _feed_config(tmp_path)
    _write_library_episode(lib_dir, "ep-a-000001", title="A")
    _write_library_episode(lib_dir, "ep-b-000002", title="B")
    errors: list[BaseException] = []

    def _pub(eid: str) -> None:
        try:
            publish_episode(eid, cfg, library=lib_dir, root=feed_dir)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_pub, args=("ep-a-000001",)),
        threading.Thread(target=_pub, args=("ep-b-000002",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    ids = {entry.episode_id for entry in list_feed_episodes(feed_dir)}
    assert ids == {"ep-a-000001", "ep-b-000002"}

    started = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        with feed_lock(timeout_s=5):
            started.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=_hold)
    holder.start()
    assert started.wait(timeout=2)
    with pytest.raises(SaseListenError, match="feed is busy"), feed_lock(timeout_s=0.2):
        pass
    release.set()
    holder.join()


def test_publish_masks_item_url_unless_show_url(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from test_feed import _write_feed_config

    _write_feed_config(isolated, tmp_path, monkeypatch)
    source = _write_script(tmp_path, "bite_narration.md", TINY_RESEARCH_SCRIPT)
    assert main(["render", source, "-n", "tone", "--json"]) == 0
    episode_id = json.loads(capsys.readouterr().out)["episode_id"]
    assert main(["publish", episode_id, "--json"]) == 0
    published = json.loads(capsys.readouterr().out)
    assert "****" in published["item_url"]
    assert "cli-token-xyz" not in published["item_url"]
    assert main(["publish", episode_id, "--json", "--show-url"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert "cli-token-xyz" in shown["item_url"]


def test_feed_status_remote_via_is_ssh_destination(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Host feed --json reports via=local; the client must show the dest."""
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(
        "feed:\n"
        "  host: apollo\n"
        "  host_ssh: [apollo]\n"
        "  base_url: https://example.com:8443\n"
        "  token: tok\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(cfg_path))
    host_payload = {
        "ok": True,
        "feed_dir": "/host/feed",
        "url": "https://example.com:8443/****/feed.xml",
        "url_masked": True,
        "configured": True,
        "episodes": 9,
        "episode_ids": ["ep-one"],
        "size_bytes": 1,
        "last_build": "",
        "retention": {"retention_days": 90, "max_episodes": 200},
        "host": "apollo",
        "sase_listen_version": "0.1.0",
        "receive_protocol": 1,
        "via": "local",
        "outbox_pending": 0,
    }
    _install_fake_ssh(
        tmp_path,
        monkeypatch,
        {"apollo": {"kind": "json", "stdout": json.dumps(host_payload)}},
    )
    assert main(["feed", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["via"] == "apollo"
    assert payload["host"] == "apollo"
    assert payload["episodes"] == 9
    assert payload["receive_protocol"] == 1
    assert main(["feed"]) == 0
    human = capsys.readouterr().out
    assert "Host: apollo (via apollo)" in human


def test_feed_status_reports_host_and_protocol(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from test_feed import _write_feed_config

    _write_feed_config(isolated, tmp_path, monkeypatch)
    assert main(["feed", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["receive_protocol"] == 1
    assert payload["host"] == local_hostname()
    assert "sase_listen_version" in payload
    assert payload["outbox_pending"] == 0
    assert main(["doctor", "--json"]) == 3
    doctor = json.loads(capsys.readouterr().out)
    names = {check["name"]: check for check in doctor["checks"]}
    assert names["feed:host"]["ok"] is True
    assert names["feed:host"]["detail"] == "this machine"


def test_feed_init_refused_when_remote(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text("feed:\n  host: apollo\n", encoding="utf-8")
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(cfg_path))
    assert main(["feed", "init", "--base-url", "https://h:8443", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert "feed host apollo" in payload["error"]["message"]


def test_auto_publish_queues_when_host_down(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(
        "narrator: tone\n"
        "feed:\n"
        "  host: apollo\n"
        "  host_ssh: [down]\n"
        "  base_url: https://example.com:8443\n"
        "  token: tok\n"
        "  auto_publish: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(cfg_path))
    _install_fake_ssh(
        tmp_path,
        monkeypatch,
        {"down": {"kind": "fail", "exit": 255, "stderr": "ssh: down"}},
    )
    source = _write_script(tmp_path, "bite_narration.md", TINY_RESEARCH_SCRIPT)
    assert main(["render", source, "-n", "tone", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["published"] is False
    assert payload["publish_queued"] is True
    assert payload["ok"] is True
    assert any("queued" in warning for warning in payload["warnings"])
    assert pending_publishes()
