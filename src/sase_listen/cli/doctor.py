"""doctor command. Owner: cli phase (scaffold ships the offline checks)."""

from __future__ import annotations

import argparse
import json
import shutil
import xml.etree.ElementTree as ET
from typing import Any

from sase_listen import invocation
from sase_listen.buildinfo import compare_builds
from sase_listen.buildinfo import current as _current_build
from sase_listen.cli.progress import activity
from sase_listen.config import SaseListenConfig, load_config
from sase_listen.engines.secrets import describe_api_key_source
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.feed import FEED_XML_NAME, RECEIVE_PROTOCOL, feed_root, resolve_token
from sase_listen.feedhost import feed_role, pending_publishes, run_remote
from sase_listen.paths import cache_dir, config_path, data_dir, state_dir


def _ffmpeg_info() -> dict[str, Any]:
    import os

    override = os.environ.get("SASE_LISTEN_FFMPEG", "")
    candidates: list[str] = []
    if override:
        candidates.append(override)
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    try:
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    path = next((c for c in candidates if c and os.path.exists(c)), "")
    return {
        "path": path,
        "source": (
            "SASE_LISTEN_FFMPEG"
            if path == override and path
            else (
                "PATH"
                if path == found and path
                else ("imageio-ffmpeg" if path else "missing")
            )
        ),
        "libmp3lame": bool(path),
        "loudnorm": bool(path),
    }


def _run_checks(
    *, online: bool, allow_activity: bool = True
) -> tuple[list[dict[str, Any]], bool]:
    checks: list[dict[str, Any]] = []
    ok = True

    try:
        cfg, _ = load_config()
        checks.append({"name": "config", "ok": True, "detail": str(config_path())})
    except Exception as exc:
        checks.append({"name": "config", "ok": False, "detail": str(exc)})
        ok = False
        cfg = None

    ffmpeg = _ffmpeg_info()
    checks.append(
        {
            "name": "ffmpeg",
            "ok": bool(ffmpeg["path"]),
            "detail": f"{ffmpeg['source']}:{ffmpeg['path']}"
            if ffmpeg["path"]
            else "no ffmpeg found",
        }
    )
    if not ffmpeg["path"]:
        ok = False

    if cfg is not None:
        narrator = cfg.narrator
        profile = cfg.narrators.get(narrator)
        if profile is None:
            checks.append(
                {
                    "name": "credentials",
                    "ok": False,
                    "detail": f"unknown narrator '{narrator}'",
                }
            )
            ok = False
        elif online:
            checks.append(
                {
                    "name": "credentials",
                    "ok": False,
                    "detail": "online check not implemented yet",
                }
            )
            ok = False
        elif profile.engine in ("gemini", "openai"):
            try:
                engine_cfg = getattr(cfg.engines, profile.engine)
                source = describe_api_key_source(
                    engine=profile.engine,
                    env_names=list(engine_cfg.api_key_env),
                    api_key_command=engine_cfg.api_key_command,
                )
            except Exception:
                source = "(source unknown)"
            checks.append(
                {
                    "name": "credentials",
                    "ok": True,
                    "detail": f"narrator '{narrator}': {source}",
                }
            )
        else:
            checks.append(
                {
                    "name": "credentials",
                    "ok": True,
                    "detail": f"narrator '{narrator}' (no credentials needed)",
                }
            )
    else:
        checks.append(
            {"name": "credentials", "ok": False, "detail": "skipped: config invalid"}
        )

    for name, path in (
        ("data", data_dir()),
        ("cache", cache_dir()),
        ("state", state_dir()),
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
            checks.append({"name": f"writable:{name}", "ok": True, "detail": str(path)})
        except Exception as exc:
            checks.append({"name": f"writable:{name}", "ok": False, "detail": str(exc)})
            ok = False

    before = len(checks)
    _feed_checks(cfg, checks, allow_activity=allow_activity)
    ok = ok and all(item["ok"] for item in checks[before:])
    _install_check(checks)
    ok = ok and bool(checks[-1]["ok"])
    try:
        detail = _current_build().display()
    except Exception:
        detail = "unknown"
    checks.append({"name": "version", "ok": True, "detail": detail})
    return checks, ok


def _install_check(checks: list[dict[str, Any]]) -> None:
    try:
        info = _current_build()
    except Exception:
        checks.append(
            {"name": "install", "ok": True, "detail": "build info unavailable"}
        )
        return
    if not info.stale:
        source = f" ({info.source})" if info.source else ""
        checks.append(
            {"name": "install", "ok": True, "detail": f"{info.install}{source}"}
        )
        return
    if info.missing_dependencies:
        missing = ", ".join(info.missing_dependencies)
        detail = f"missing dependencies: {missing}; {info.upgrade_command()}"
    else:
        detail = (
            f"installed metadata {info.metadata_version} != source"
            f" {info.version}; {info.upgrade_command()}"
        )
    checks.append({"name": "install", "ok": False, "detail": detail})


def _outbox_check(checks: list[dict[str, Any]]) -> None:
    pending = pending_publishes()
    if not pending:
        return
    checks.append(
        {
            "name": "feed:outbox",
            "ok": True,
            "detail": (
                f"{len(pending)} queued"
                f" — run `{invocation.command('publish', '--pending')}`"
            ),
        }
    )


def _remote_host_check(
    cfg: SaseListenConfig, checks: list[dict[str, Any]], *, allow_activity: bool = True
) -> None:
    host = cfg.feed.host.strip()
    try:
        with activity(f"Checking feed host {host}…", enabled=allow_activity) as act:
            status, dest = run_remote(cfg, ["feed", "--json"], on_attempt=act.update)
    except SaseListenError as exc:
        checks.append({"name": "feed:host", "ok": False, "detail": str(exc)})
        checks.append(
            {
                "name": "feed:host-build",
                "ok": False,
                "detail": f"skipped: feed:host failed ({exc})",
            }
        )
        return
    proto = int(status.get("receive_protocol") or 0)
    configured = bool(status.get("configured"))
    version = str(status.get("sase_listen_version") or "?")
    episodes = status.get("episodes", 0)
    remote_build = status.get("sase_listen_build")
    if not isinstance(remote_build, dict):
        remote_build = None
    try:
        local_build = _current_build().to_json()
    except Exception:
        local_build = {}
    if isinstance(remote_build, dict):
        remote_display = str(remote_build.get("display") or f"sase-listen {version}")
    else:
        remote_display = f"sase-listen {version}"
    detail = f"{host} via {dest} · sase-listen {remote_display} · {episodes} episodes"
    checks.append(
        {
            "name": "feed:host",
            "ok": configured and proto >= 1,
            "detail": detail,
        }
    )
    checks.append(_host_build_check(host, dest, proto, local_build, remote_build))


def _host_build_check(
    host: str,
    dest: str,
    proto: int,
    local_build: dict[str, Any],
    remote_build: dict[str, Any] | None,
) -> dict[str, Any]:
    local_display = str(
        (local_build.get("display") if isinstance(local_build, dict) else "") or "?"
    )
    remote_display = str(
        (remote_build.get("display") if isinstance(remote_build, dict) else "") or "?"
    )
    both = f"this machine runs {local_display}; feed host {host} runs {remote_display}"
    if proto < RECEIVE_PROTOCOL:
        return {
            "name": "feed:host-build",
            "ok": False,
            "detail": (
                f"host protocol {proto} < local {RECEIVE_PROTOCOL}:"
                f" {both} — upgrade sase-listen on {host}"
                " (see docs/multi-machine.md)"
            ),
        }
    if isinstance(remote_build, dict) and bool(remote_build.get("stale")):
        remote_upgrade = str(remote_build.get("upgrade_command") or "")
        if remote_upgrade:
            fix = f"ssh {dest} '{remote_upgrade}'"
        else:
            fix = f"upgrade sase-listen on {host} (see docs/multi-machine.md)"
        return {
            "name": "feed:host-build",
            "ok": False,
            "detail": f"host is stale: {both} — {fix}",
        }
    compared = compare_builds(local_build, remote_build)
    if compared.outcome == "same":
        return {
            "name": "feed:host-build",
            "ok": True,
            "detail": f"{compared.message} ({both})",
        }
    if compared.outcome == "remote_unknown":
        return {
            "name": "feed:host-build",
            "ok": False,
            "detail": (
                f"{compared.message} ({both}) —"
                f" upgrade sase-listen on {host} (see docs/multi-machine.md)"
            ),
        }
    if compared.outcome == "local_older":
        try:
            fix = _current_build().upgrade_command()
        except Exception:
            fix = "upgrade sase-listen on this machine"
        return {
            "name": "feed:host-build",
            "ok": False,
            "detail": f"{compared.message} ({both}) — {fix}",
        }
    # differ or remote_older: the host side must move.
    if isinstance(remote_build, dict):
        remote_upgrade = str(remote_build.get("upgrade_command") or "")
    else:
        remote_upgrade = ""
    if remote_upgrade:
        fix = f"ssh {dest} '{remote_upgrade}'"
    else:
        fix = f"upgrade sase-listen on {host} (see docs/multi-machine.md)"
    return {
        "name": "feed:host-build",
        "ok": False,
        "detail": f"{compared.message} ({both}) — {fix}",
    }


def _feed_checks(
    cfg: SaseListenConfig | None,
    checks: list[dict[str, Any]],
    *,
    allow_activity: bool = True,
) -> None:
    """Append the four feed checks (feed phase).

    An untouched feed (no base_url, no token, nothing published) reports
    ok with a "not configured" detail so Telegram-only users keep a green
    doctor; a half-configured feed fails loudly instead.
    """
    if cfg is None:
        for name in (
            "feed:host",
            "feed:dir",
            "feed:base_url",
            "feed:token",
            "feed:feed.xml",
        ):
            checks.append(
                {"name": name, "ok": False, "detail": "skipped: config invalid"}
            )
        return
    _outbox_check(checks)
    if feed_role(cfg) == "remote":
        _remote_host_check(cfg, checks, allow_activity=allow_activity)
        return
    checks.append({"name": "feed:host", "ok": True, "detail": "this machine"})
    root = feed_root(cfg)
    try:
        root.mkdir(parents=True, exist_ok=True)
        checks.append({"name": "feed:dir", "ok": True, "detail": str(root)})
    except Exception as exc:
        checks.append({"name": "feed:dir", "ok": False, "detail": str(exc)})
        return
    touched = bool(
        cfg.feed.base_url.strip()
        or cfg.feed.token.strip()
        or cfg.feed.token_command.strip()
        or (root / FEED_XML_NAME).is_file()
        or (root / "episodes").is_dir()
    )
    if not touched:
        for name in ("feed:base_url", "feed:token", "feed:feed.xml"):
            checks.append(
                {
                    "name": name,
                    "ok": True,
                    "detail": "feed not configured (feed init to enable)",
                }
            )
        return
    if cfg.feed.base_url.strip():
        checks.append(
            {
                "name": "feed:base_url",
                "ok": True,
                "detail": cfg.feed.base_url.strip(),
            }
        )
    else:
        checks.append(
            {
                "name": "feed:base_url",
                "ok": False,
                "detail": "feed.base_url is empty (run feed init --base-url URL)",
            }
        )
    try:
        resolve_token(cfg)
    except SaseListenError as exc:
        checks.append({"name": "feed:token", "ok": False, "detail": str(exc)})
    else:
        checks.append({"name": "feed:token", "ok": True, "detail": "set"})
    xml_path = root / FEED_XML_NAME
    if not xml_path.is_file():
        checks.append(
            {
                "name": "feed:feed.xml",
                "ok": False,
                "detail": "feed.xml not generated yet (publish an episode)",
            }
        )
        return
    try:
        ET.parse(str(xml_path))
    except ET.ParseError as exc:
        checks.append(
            {"name": "feed:feed.xml", "ok": False, "detail": f"unparsable: {exc}"}
        )
    else:
        checks.append({"name": "feed:feed.xml", "ok": True, "detail": str(xml_path)})


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    prog: str = "sase-listen",
) -> argparse.ArgumentParser:
    """Register the doctor parser."""
    p = sub.add_parser("doctor", help="Check config, ffmpeg, credentials, and dirs.")
    p.add_argument(
        "--online", action="store_true", help="Include one-word synth check."
    )
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = f"Example: {prog} doctor --online"
    return p


def run(args: argparse.Namespace) -> int:
    """Run offline checks (feed phase adds feed checks)."""
    as_json = bool(getattr(args, "json", False))
    checks, ok = _run_checks(
        online=bool(getattr(args, "online", False)), allow_activity=not as_json
    )
    if as_json:
        print(json.dumps({"ok": ok, "checks": checks}))
    else:
        for item in checks:
            mark = "ok" if item["ok"] else "FAIL"
            print(f"{mark}: {item['name']} ({item['detail']})")
    return int(ExitCode.OK if ok else ExitCode.CONFIG)
