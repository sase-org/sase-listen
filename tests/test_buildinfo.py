"""Build identity, stale-env guard, and drift warnings."""

from __future__ import annotations

import json
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

from sase_listen import buildinfo
from sase_listen.buildinfo import BuildInfo, compare_builds


class _FakeDist:
    def __init__(self, version: str, direct_url: object) -> None:
        self._version = version
        self._direct_url = direct_url

    @property
    def version(self) -> str:
        return self._version

    def read_text(self, name: str) -> str | None:
        if name != "direct_url.json":
            return None
        if self._direct_url is None:
            return None
        return json.dumps(self._direct_url)


def _patch_dist(
    monkeypatch: pytest.MonkeyPatch, version: str, direct_url: object
) -> None:
    real = metadata.distribution

    def _fake(name: str) -> Any:
        if name == "sase-listen":
            return _FakeDist(version, direct_url)
        return real(name)

    monkeypatch.setattr(buildinfo.metadata, "distribution", _fake)


def _no_git(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="git", timeout=2)

    monkeypatch.setattr(buildinfo.subprocess, "run", _fail)


def _write_pyproject(source: Path, version: str, deps: list[str]) -> None:
    lines = ["[project]", 'name = "sase-listen"', f'version = "{version}"']
    if deps:
        lines.append("dependencies = [")
        for dep in deps:
            lines.append(f'  "{dep}",')
        lines.append("]")
    (source / "pyproject.toml").write_text("\n".join(lines) + "\n")


def test_install_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_git(monkeypatch)
    _patch_dist(monkeypatch, "0.1.1", None)
    assert buildinfo._compute().install == "index"

    _patch_dist(monkeypatch, "0.1.1", {"url": "https://x/y.tar.gz", "archive_info": {}})
    assert buildinfo._compute().install == "local"

    _patch_dist(
        monkeypatch,
        "0.1.1",
        {
            "url": "git+https://example.test/r.git",
            "vcs_info": {"vcs": "git", "commit_id": "abc123"},
        },
    )
    info = buildinfo._compute()
    assert info.install == "vcs"
    assert info.commit == "abc123"
    assert info.source == "git+https://example.test/r.git"

    def _missing(name: str) -> Any:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(buildinfo.metadata, "distribution", _missing)
    assert buildinfo._compute().install == "unknown"


def test_editable_version_deps_and_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _write_pyproject(
        src,
        "0.2.0",
        ["PyYAML>=6.0", "definitely-missing-xyz>=1", "cond; python_version < '3.0'"],
    )
    url = f"file://{src}"
    _patch_dist(monkeypatch, "0.1.1", {"url": url, "dir_info": {"editable": True}})

    def _git_ok(*args: Any, **kwargs: Any) -> Any:
        class _P:
            returncode = 0
            stdout = "deadbee\n" if "rev-parse" in str(args) else ""

        return _P()

    monkeypatch.setattr(buildinfo.subprocess, "run", _git_ok)
    info = buildinfo._compute()
    assert info.install == "editable"
    assert info.version == "0.2.0"
    assert info.metadata_version == "0.1.1"
    assert info.stale is True
    assert "definitely-missing-xyz" in info.missing_dependencies
    # Markers are skipped, real deps are present.
    assert all("cond" not in name for name in info.missing_dependencies)
    assert "pyyaml" not in info.missing_dependencies


def test_git_failures_degrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _write_pyproject(src, "0.1.1", [])
    _patch_dist(
        monkeypatch, "0.1.1", {"url": f"file://{src}", "dir_info": {"editable": True}}
    )
    _no_git(monkeypatch)
    info = buildinfo._compute()
    assert info.commit == ""
    assert info.dirty is False


def test_display_strings() -> None:
    assert (
        BuildInfo(install="index", version="0.1.1", metadata_version="0.1.1").display()
        == "0.1.1"
    )
    assert (
        BuildInfo(
            install="vcs",
            version="0.1.1",
            metadata_version="0.1.1",
            commit="3ae7310abc",
        ).display()
        == "0.1.1 (git 3ae7310)"
    )
    assert BuildInfo(
        install="editable",
        version="0.1.1",
        metadata_version="0.1.1",
        commit="3ae7310abc",
    ).display() == ("0.1.1 (editable @ 3ae7310)")
    assert (
        BuildInfo(
            install="editable",
            version="0.1.1",
            metadata_version="0.1.1",
            commit="abc1234",
            dirty=True,
        ).display()
        == "0.1.1 (editable @ abc1234, dirty)"
    )


def test_upgrade_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = tmp_path / "toolenv"
    prefix.mkdir()
    (prefix / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    monkeypatch.setattr(buildinfo.sys, "prefix", str(prefix))
    monkeypatch.setattr(buildinfo.shutil, "which", lambda name: "/bin/uv")
    editable = BuildInfo(
        install="editable",
        source="/home/u/src",
        version="0.1.1",
        metadata_version="0.1.1",
    )
    assert editable.upgrade_command() == (
        "git -C /home/u/src pull --ff-only"
        " && /bin/uv tool upgrade --reinstall sase-listen"
    )
    index = BuildInfo(install="index", version="0.1.1", metadata_version="0.1.1")
    assert index.upgrade_command() == "/bin/uv tool upgrade sase-listen"
    vcs = BuildInfo(
        install="vcs",
        source="git+https://example.test/r.git",
        version="0.1.1",
        metadata_version="0.1.1",
    )
    assert "uv tool install --force" in vcs.upgrade_command()
    # Without a uv receipt the command is generic.
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.setattr(buildinfo.sys, "prefix", str(bare))
    assert "pipx reinstall" in index.upgrade_command()


def test_upgrade_command_plugin_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "sase-toolenv"
    prefix.mkdir()
    (prefix / "uv-receipt.toml").write_text(
        "[tool]\n"
        "requirements = [\n"
        '    { name = "sase", editable = "/home/u/src" },\n'
        "]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(buildinfo.sys, "prefix", str(prefix))
    monkeypatch.setattr(buildinfo.shutil, "which", lambda name: "/bin/uv")
    # Both editable and index installs repair through the plugin manager
    # when the interpreter is the sase tool environment.
    editable = BuildInfo(
        install="editable",
        source="/home/u/src",
        version="0.1.1",
        metadata_version="0.1.1",
    )
    assert editable.upgrade_command() == "sase plugin update listen"
    index = BuildInfo(install="index", version="0.1.1", metadata_version="0.1.1")
    assert index.upgrade_command() == "sase plugin update listen"


def test_upgrade_command_receipt_without_sase_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "toolenv"
    prefix.mkdir()
    (prefix / "uv-receipt.toml").write_text(
        "[tool]\n"
        "requirements = [\n"
        '    { name = "sase-listen", editable = "/home/u/src" },\n'
        "]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(buildinfo.sys, "prefix", str(prefix))
    monkeypatch.setattr(buildinfo.shutil, "which", lambda name: "/bin/uv")
    index = BuildInfo(install="index", version="0.1.1", metadata_version="0.1.1")
    assert index.upgrade_command() == "/bin/uv tool upgrade sase-listen"


def test_compare_builds_outcomes() -> None:
    def _b(version: str, commit: str = "", display: str = "") -> dict[str, Any]:
        return {"version": version, "commit": commit, "display": display or version}

    assert compare_builds(_b("0.1.1"), _b("0.1.1")).outcome == "same"
    # Missing commit on either side is still same.
    assert compare_builds(_b("0.1.1", "abc"), _b("0.1.1")).outcome == "same"
    assert compare_builds(_b("0.1.1", "abc"), _b("0.1.1", "def")).outcome == "differ"
    assert compare_builds(_b("0.1.1"), _b("0.1.0")).outcome == "remote_older"
    assert compare_builds(_b("0.1.0"), _b("0.1.1")).outcome == "local_older"
    assert compare_builds(_b("0.1.1"), None).outcome == "remote_unknown"
    assert compare_builds(_b("0.1.1"), {}).outcome == "remote_unknown"
    bad = compare_builds(_b("weird"), _b("also-weird"))
    assert bad.outcome == "differ"
    assert "order unknown" in bad.message


def test_cli_guard_import_error(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    import sase_listen.cli as cli_entry
    import sase_listen.cli.app as app_mod

    real_main = app_mod.main

    def _boom_import(argv: Any = None, **kwargs: Any) -> int:
        raise ImportError("No module named 'lxml'", name="lxml")

    monkeypatch.setattr(app_mod, "main", _boom_import)
    assert cli_entry.main([]) == 3
    assert "out of date" in capsys.readouterr().err
    assert cli_entry.main([], prog="sase listen") == 3
    assert "sase listen" in capsys.readouterr().err

    monkeypatch.setattr(app_mod, "main", real_main)

    def _boom_cmd(argv: Any = None, **kwargs: Any) -> int:
        raise ImportError("No module named 'trafilatura'", name="trafilatura")

    monkeypatch.setattr(app_mod, "main", _boom_cmd)
    assert cli_entry.main(["render", "--json", "x"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == 3
    assert "reinstall" in payload["error"]["hint"].lower()
    monkeypatch.setattr(app_mod, "main", real_main)

    def _boom_bug(argv: Any = None, **kwargs: Any) -> int:
        raise ImportError("bad", name="sase_listen.foo")

    monkeypatch.setattr(app_mod, "main", _boom_bug)
    with pytest.raises(ImportError):
        cli_entry.main([])
    monkeypatch.setattr(app_mod, "main", real_main)


def test_version_starts_with_release(capsys: Any) -> None:
    from sase_listen.cli.app import main

    with pytest.raises(SystemExit) as exited:
        main(["--version"])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("sase-listen ")
    assert len(out.split()) >= 2


def _doctor_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str = ""
) -> None:
    base = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    cfg = tmp_path / "listen.yml"
    cfg.write_text(f"narrator: tone\nfeed:\n  host: {host}\n", encoding="utf-8")
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(cfg))


def _fake_build(
    version: str = "0.1.1",
    commit: str = "local111",
    stale: bool = False,
) -> BuildInfo:
    display = f"{version} (editable @ {commit[:7]})" if commit else version
    info = BuildInfo(
        install="editable",
        source="/src",
        version=version,
        metadata_version=version,
        commit=commit,
        dirty=False,
        missing_dependencies=(),
    )
    # BuildInfo.to_json derives display/upgrade from fields; stale is
    # forced via missing deps when needed.
    if stale:
        info = BuildInfo(
            install="editable",
            source="/src",
            version=version,
            metadata_version="0.0.0",
            commit=commit,
            dirty=False,
            missing_dependencies=("pdfminer-six",),
        )
    assert (info.stale is True) == stale
    assert info.display() == display or not stale
    return info


def test_doctor_same_build_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from sase_listen.cli.app import main

    _doctor_config(tmp_path, monkeypatch, host="apollo")
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    local = _fake_build("0.1.1", "abc1234")
    remote = dict(local.to_json())
    monkeypatch.setattr("sase_listen.buildinfo.current", lambda: local)
    monkeypatch.setattr(
        "sase_listen.feedhost.run_remote",
        lambda cfg, args, **kw: (
            {
                "configured": True,
                "episodes": 2,
                "sase_listen_version": "0.1.1",
                "sase_listen_build": remote,
                "receive_protocol": 1,
            },
            "apollo",
        ),
    )
    assert main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    names = {c["name"]: c for c in payload["checks"]}
    assert names["feed:host-build"]["ok"] is True
    assert names["install"]["ok"] is True
    assert names["version"]["detail"] == local.display()


def test_doctor_skew_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from sase_listen.cli.app import main

    _doctor_config(tmp_path, monkeypatch, host="apollo")
    monkeypatch.setattr("sase_listen.feedhost.local_hostname", lambda: "athena")
    local = _fake_build("0.1.1", "abc1234")
    monkeypatch.setattr("sase_listen.buildinfo.current", lambda: local)

    # Differing commits fail.
    other = dict(_fake_build("0.1.1", "def5678").to_json())

    def _check(remote: Any, proto: int = 1) -> dict[str, Any]:
        monkeypatch.setattr(
            "sase_listen.feedhost.run_remote",
            lambda cfg, args, **kw: (
                {
                    "configured": True,
                    "episodes": 1,
                    "sase_listen_version": str(
                        remote.get("version") if isinstance(remote, dict) else "?"
                    ),
                    "sase_listen_build": remote,
                    "receive_protocol": proto,
                },
                "apollo",
            ),
        )
        assert main(["doctor", "--json"]) == 3
        out = json.loads(capsys.readouterr().out)
        return {c["name"]: c for c in out["checks"]}

    names = _check(other)
    assert names["feed:host-build"]["ok"] is False
    assert "this machine runs" in names["feed:host-build"]["detail"]
    assert "feed host apollo runs" in names["feed:host-build"]["detail"]
    # No build info fails.
    names = _check(None)
    assert names["feed:host-build"]["ok"] is False
    assert "upgrade sase-listen on apollo" in names["feed:host-build"]["detail"]
    # Stale host fails with its ssh repair command.
    stale_remote = dict(_fake_build("0.1.1", "old0000", stale=True).to_json())
    names = _check(stale_remote)
    assert names["feed:host-build"]["ok"] is False
    assert "ssh apollo" in names["feed:host-build"]["detail"]
    # Lower protocol fails.
    names = _check(dict(local.to_json()), proto=0)
    assert names["feed:host-build"]["ok"] is False
    assert "protocol" in names["feed:host-build"]["detail"]


def test_doctor_local_stale_install_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from sase_listen.cli.app import main

    _doctor_config(tmp_path, monkeypatch, host="")
    stale = _fake_build("0.1.1", "abc1234", stale=True)
    monkeypatch.setattr("sase_listen.buildinfo.current", lambda: stale)
    assert main(["doctor", "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    names = {c["name"]: c for c in payload["checks"]}
    assert names["install"]["ok"] is False
    assert "reinstall" in names["install"]["detail"].lower()


def test_feed_status_and_receive_include_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sase_listen.feed import feed_status
    from sase_listen.feedhost import pack_episode, receive_episode
    from test_feed import _feed_config, _write_library_episode

    cfg, _, lib_dir = _feed_config(tmp_path)
    status = feed_status(cfg)
    assert "sase_listen_build" in status
    assert status["sase_listen_build"]["version"]

    _write_library_episode(lib_dir, "ep-one-aaaaaa")
    blob = pack_episode("ep-one-aaaaaa", lib_dir)
    result = receive_episode("ep-one-aaaaaa", blob, cfg, library=tmp_path / "host-lib")
    assert "sase_listen_build" in result
    assert result["sase_listen_build"]["version"]


def test_publish_skew_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sase_listen.feedhost as fh
    from test_feed import _feed_config, _write_library_episode

    cfg, _, lib_dir = _feed_config(tmp_path)
    cfg.feed.host = "apollo"
    cfg.feed.host_ssh = ["apollo"]
    _write_library_episode(lib_dir, "ep-skew-111111")
    monkeypatch.setattr(fh, "feed_role", lambda cfg: "remote")
    monkeypatch.setattr(fh, "flush_pending", lambda cfg, **kw: {})
    local = _fake_build("0.1.1", "abc1234")
    monkeypatch.setattr(fh, "_current_build", lambda: local)

    same = dict(local.to_json())

    def _remote_same(*args: Any, **kwargs: Any) -> Any:
        return (
            {"ok": True, "episode_id": "ep-skew-111111", "sase_listen_build": same},
            "apollo",
        )

    monkeypatch.setattr(fh, "run_remote", _remote_same)
    payload = fh.publish_any("ep-skew-111111", cfg, library=lib_dir)
    assert "build_warning" not in payload

    other = dict(_fake_build("0.1.0", "old0000").to_json())

    def _remote_old(*args: Any, **kwargs: Any) -> Any:
        return (
            {"ok": True, "episode_id": "ep-skew-111111", "sase_listen_build": other},
            "apollo",
        )

    monkeypatch.setattr(fh, "run_remote", _remote_old)
    payload = fh.publish_any("ep-skew-111111", cfg, library=lib_dir)
    assert "feed host apollo runs" in payload["build_warning"]
    assert "run sase-listen doctor" in payload["build_warning"]
    assert payload["warnings"] == [payload["build_warning"]]


def test_raise_remote_error_surfaces_stale_hint() -> None:
    import subprocess as sp

    from sase_listen.errors import SaseListenError
    from sase_listen.feedhost import _raise_remote_error

    proc = sp.CompletedProcess(args=["ssh"], returncode=3, stdout=b"", stderr=b"")
    stdout = json.dumps(
        {
            "ok": False,
            "error": {
                "code": 3,
                "message": "sase-listen cannot import 'pdfminer': this installation's"
                " Python environment is out of date with its code",
                "hint": "Reinstall: uv tool upgrade --reinstall sase-listen",
            },
        }
    )
    with pytest.raises(SaseListenError) as caught:
        _raise_remote_error("apollo", proc, stdout, "")
    assert "on apollo:" in str(caught.value)
    assert "upgrade --reinstall" in caught.value.hint
    assert caught.value.code == 3
