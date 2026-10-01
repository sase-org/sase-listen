"""doctor command. Owner: cli phase (scaffold ships the offline checks)."""

from __future__ import annotations

import argparse
import json
import shutil
import xml.etree.ElementTree as ET
from typing import Any

from sase_listen import __version__
from sase_listen.config import SaseListenConfig, load_config
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.feed import FEED_XML_NAME, feed_root, resolve_token
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


def _run_checks(*, online: bool) -> tuple[list[dict[str, Any]], bool]:
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
        else:
            checks.append(
                {
                    "name": "credentials",
                    "ok": True,
                    "detail": f"narrator '{narrator}' (presence only)",
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
    _feed_checks(cfg, checks)
    ok = ok and all(item["ok"] for item in checks[before:])
    checks.append({"name": "version", "ok": True, "detail": __version__})
    return checks, ok


def _feed_checks(cfg: SaseListenConfig | None, checks: list[dict[str, Any]]) -> None:
    """Append the four feed checks (feed phase).

    An untouched feed (no base_url, no token, nothing published) reports
    ok with a "not configured" detail so Telegram-only users keep a green
    doctor; a half-configured feed fails loudly instead.
    """
    if cfg is None:
        for name in ("feed:dir", "feed:base_url", "feed:token", "feed:feed.xml"):
            checks.append(
                {"name": name, "ok": False, "detail": "skipped: config invalid"}
            )
        return
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
) -> argparse.ArgumentParser:
    """Register the doctor parser."""
    p = sub.add_parser("doctor", help="Check config, ffmpeg, credentials, and dirs.")
    p.add_argument(
        "--online", action="store_true", help="Include one-word synth check."
    )
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen doctor --online"
    return p


def run(args: argparse.Namespace) -> int:
    """Run offline checks (feed phase adds feed checks)."""
    checks, ok = _run_checks(online=bool(getattr(args, "online", False)))
    if getattr(args, "json", False):
        print(json.dumps({"ok": ok, "checks": checks}))
    else:
        for item in checks:
            mark = "ok" if item["ok"] else "FAIL"
            print(f"{mark}: {item['name']} ({item['detail']})")
    return int(ExitCode.OK if ok else ExitCode.CONFIG)
