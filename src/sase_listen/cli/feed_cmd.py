"""feed command: init, status, rebuild, and prune. Owner: feed phase."""

from __future__ import annotations

import argparse
import json

from sase_listen.config import SaseListenConfig, load_config
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.feed import (
    feed_status,
    init_feed,
    masked_subscribe_url,
    print_qr,
    rebuild_feed,
)


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the feed parser."""
    p = sub.add_parser("feed", help="Manage the private podcast feed.")
    p.add_argument(
        "action", nargs="?", default="", choices=["", "init", "rebuild", "prune"]
    )
    p.add_argument("--base-url", default="", help="Public base URL for feed init.")
    p.add_argument("--qr", action="store_true", help="Print a terminal QR code.")
    p.add_argument("--show-url", action="store_true", help="Show the unmasked URL.")
    p.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Print config instead of writing.",
    )
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen feed init --base-url https://host:8443"
    return p


def _error_payload(exc: SaseListenError) -> dict[str, object]:
    return {
        "ok": False,
        "error": {"code": int(exc.code), "message": str(exc), "hint": exc.hint},
    }


def _run_init(args: argparse.Namespace) -> int:
    as_json = bool(args.json)
    try:
        info = init_feed(args.base_url or "", print_only=bool(args.print_only))
    except SaseListenError as exc:
        if as_json:
            print(json.dumps(_error_payload(exc)))
        else:
            print(f"sase-listen feed init: error: {exc}")
            if exc.hint:
                print(f"hint: {exc.hint}")
        return int(exc.code)
    if as_json:
        print(json.dumps({"ok": True, **info}))
        return int(ExitCode.OK)
    if info["snippet"]:
        print(info["snippet"], end="")
        print("# Add the snippet above to your config, then serve with:")
    elif info["config_path"]:
        print(f"Wrote feed config to {info['config_path']}")
        if info["token_reused"]:
            print("(kept the existing token)")
    print(f"Subscribe URL: {info['subscribe_url']}")
    print(f"Serve with: {info['funnel_command']}")
    print(print_qr(str(info["subscribe_url"])))
    return int(ExitCode.OK)


def _run_status(args: argparse.Namespace, cfg: SaseListenConfig) -> int:
    as_json = bool(args.json)
    show = bool(args.show_url)
    status = feed_status(cfg, show_url=show)
    qr = ""
    if args.qr:
        try:
            url = masked_subscribe_url(cfg, show=True)
        except SaseListenError as exc:
            if as_json:
                print(json.dumps(_error_payload(exc)))
            else:
                print(f"sase-listen feed: error: {exc}")
            return int(exc.code)
        qr = print_qr(url)
        if as_json:
            status["qr"] = qr
    if as_json:
        print(json.dumps({"ok": True, **status}))
        return int(ExitCode.OK)
    if status["url"]:
        print(f"URL: {status['url']}")
    else:
        print("Feed is not configured yet.")
        print("Run `sase-listen feed init --base-url URL` to set it up.")
    print(f"Episodes: {status['episodes']}  Size: {status['size_bytes']} bytes")
    print(f"Last build: {status['last_build'] or 'never'}")
    retention = status["retention"]
    print(
        f"Retention: {retention['retention_days']} days, "
        f"max {retention['max_episodes']} episodes"
    )
    print(f"Feed dir (served-only): {status['feed_dir']}")
    if qr:
        print(qr)
    return int(ExitCode.OK)


def _run_rebuild(
    args: argparse.Namespace, cfg: SaseListenConfig, *, prune_only: bool
) -> int:
    as_json = bool(args.json)
    try:
        result = rebuild_feed(cfg)
    except SaseListenError as exc:
        if as_json:
            print(json.dumps(_error_payload(exc)))
        else:
            print(f"sase-listen feed: error: {exc}")
            if exc.hint:
                print(f"hint: {exc.hint}")
        return int(exc.code)
    episodes = result["episodes"]
    removed = result["removed"]
    if as_json:
        print(json.dumps({"ok": True, **result}))
        return int(ExitCode.OK)
    verb = "Pruned" if prune_only else "Rebuilt"
    print(f"{verb} feed: {len(episodes)} episodes.")
    for episode_id in removed:
        print(f"  removed {episode_id} (retention)")
    print(f"Feed: {result['feed_xml']}")
    return int(ExitCode.OK)


def run(args: argparse.Namespace) -> int:
    """Run feed init, rebuild, prune, or the default status view."""
    action = getattr(args, "action", "")
    if action == "init":
        return _run_init(args)
    try:
        cfg, _ = load_config()
    except ValueError as exc:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"ok": False, "error": {"code": 3, "message": str(exc), "hint": ""}}
                )
            )
        else:
            print(f"sase-listen feed: error: {exc}")
        return int(ExitCode.CONFIG)
    if action in ("rebuild", "prune"):
        return _run_rebuild(args, cfg, prune_only=(action == "prune"))
    return _run_status(args, cfg)
