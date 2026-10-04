"""publish/unpublish commands. Owner: feed-host phase."""

from __future__ import annotations

import argparse
import json

from sase_listen.config import SaseListenConfig, load_config
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.feed import resolve_episode_ref, unpublish_episode
from sase_listen.feedhost import (
    PENDING_HINT,
    feed_role,
    flush_pending,
    publish_any,
    queue_publish,
    refuse_if_misrouted,
    run_remote,
)


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register publish and unpublish parsers."""
    p = sub.add_parser("publish", help="Publish an episode to the feed.")
    p.add_argument(
        "episode", nargs="?", default="", help="Episode id, MP3 path, or --latest."
    )
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.add_argument("--latest", action="store_true", help="Publish the newest episode.")
    p.add_argument(
        "--pending",
        action="store_true",
        help="Flush the retry outbox instead of publishing one episode.",
    )
    p.add_argument(
        "--show-url",
        action="store_true",
        help="Show the unmasked item URL (local publishes only).",
    )
    p.set_defaults(func=run_publish)
    u = sub.add_parser("unpublish", help="Remove an episode from the feed.")
    u.add_argument("episode", help="Episode id.")
    u.add_argument("--json", action="store_true", help="Emit one JSON object.")
    u.set_defaults(func=run_unpublish)
    return p


def _error_payload(exc: SaseListenError) -> dict[str, object]:
    return {
        "ok": False,
        "error": {"code": int(exc.code), "message": str(exc), "hint": exc.hint},
    }


def _print_error(exc: SaseListenError, *, as_json: bool, prefix: str) -> int:
    if as_json:
        print(json.dumps(_error_payload(exc)))
    else:
        print(f"{prefix}: error: {exc}")
        if exc.hint:
            print(f"hint: {exc.hint}")
    return int(exc.code)


def _load_cfg(args: argparse.Namespace) -> SaseListenConfig | None:
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
            print(f"sase-listen publish: error: {exc}")
        return None
    return cfg


def _queue_remote_failure(
    cfg: SaseListenConfig, episode_id: str, exc: SaseListenError
) -> None:
    queue_publish(episode_id, str(exc))
    if PENDING_HINT not in exc.hint:
        exc.hint = f"{exc.hint} {PENDING_HINT}".strip() if exc.hint else PENDING_HINT


def run_publish(args: argparse.Namespace) -> int:
    """Publish an episode to the private feed, or flush the outbox."""
    cfg = _load_cfg(args)
    if cfg is None:
        return int(ExitCode.CONFIG)
    as_json = bool(args.json)
    episode_id = ""
    try:
        refuse_if_misrouted(cfg)
        if args.pending:
            result = flush_pending(cfg)
            failed = result["failed"]
            if as_json:
                print(json.dumps({"ok": not failed, **result}))
            else:
                print(f"Published {len(result['published'])} pending episode(s).")
                for item in failed:
                    print(f"  failed {item['episode_id']}: {item['error']}")
                if failed:
                    print(f"hint: {PENDING_HINT}")
            return int(ExitCode.OK if not failed else ExitCode.UNEXPECTED)
        episode_id = resolve_episode_ref(args.episode or "", latest=bool(args.latest))
        if not episode_id and not args.latest:
            raise SaseListenError(
                "An episode id, MP3 path, or --latest is required.",
                ExitCode.USAGE,
                hint="Example: sase-listen publish --latest",
            )
        result = publish_any(
            episode_id, cfg, show_url=bool(args.show_url) and feed_role(cfg) == "local"
        )
    except SaseListenError as exc:
        if feed_role(cfg) == "remote" and not args.pending and episode_id:
            _queue_remote_failure(cfg, episode_id, exc)
        return _print_error(exc, as_json=as_json, prefix="sase-listen publish")
    if as_json:
        print(json.dumps({"ok": True, **result}))
    else:
        print(f"Published {result['episode_id']} to the feed.")
        print(f"Audio: {result['item_url']}")
        if result.get("via") and result.get("via") != "local":
            print(f"Host: {result.get('host')} (via {result.get('via')})")
    return int(ExitCode.OK)


def run_unpublish(args: argparse.Namespace) -> int:
    """Remove an episode from the private feed."""
    cfg = _load_cfg(args)
    if cfg is None:
        return int(ExitCode.CONFIG)
    as_json = bool(args.json)
    try:
        refuse_if_misrouted(cfg)
        if feed_role(cfg) == "remote":
            result, dest = run_remote(cfg, ["unpublish", args.episode, "--json"])
            result["host"] = cfg.feed.host.strip()
            result["via"] = dest
        else:
            result = unpublish_episode(args.episode, cfg)
    except SaseListenError as exc:
        return _print_error(exc, as_json=as_json, prefix="sase-listen unpublish")
    if as_json:
        print(json.dumps({"ok": True, **result}))
    else:
        print(f"Unpublished {result['episode_id']} ({result['episodes']} left).")
    return int(ExitCode.OK)
