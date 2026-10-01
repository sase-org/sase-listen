"""publish/unpublish commands. Owner: feed phase."""

from __future__ import annotations

import argparse
import json

from sase_listen.config import SaseListenConfig, load_config
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.feed import (
    publish_episode,
    resolve_episode_ref,
    unpublish_episode,
)


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register publish and unpublish parsers."""
    p = sub.add_parser("publish", help="Publish an episode to the feed.")
    p.add_argument(
        "episode", nargs="?", default="", help="Episode id, MP3 path, or --latest."
    )
    p.add_argument("--latest", action="store_true", help="Publish the newest episode.")
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run_publish)
    u = sub.add_parser("unpublish", help="Remove an episode from the feed.")
    u.add_argument("episode", help="Episode id.")
    u.add_argument("--json", action="store_true", help="Emit one JSON object.")
    u.set_defaults(func=run_unpublish)
    return p


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


def run_publish(args: argparse.Namespace) -> int:
    """Publish an episode to the private feed."""
    cfg = _load_cfg(args)
    if cfg is None:
        return int(ExitCode.CONFIG)
    as_json = bool(args.json)
    try:
        episode_id = resolve_episode_ref(args.episode or "", latest=bool(args.latest))
        if not episode_id and not args.latest:
            raise SaseListenError(
                "An episode id, MP3 path, or --latest is required.",
                ExitCode.USAGE,
                hint="Example: sase-listen publish --latest",
            )
        result = publish_episode(episode_id, cfg)
    except SaseListenError as exc:
        if as_json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": int(exc.code),
                            "message": str(exc),
                            "hint": exc.hint,
                        },
                    }
                )
            )
        else:
            print(f"sase-listen publish: error: {exc}")
            if exc.hint:
                print(f"hint: {exc.hint}")
        return int(exc.code)
    if as_json:
        print(json.dumps({"ok": True, **result}))
    else:
        print(f"Published {result['episode_id']} to the feed.")
        print(f"Audio: {result['item_url']}")
    return int(ExitCode.OK)


def run_unpublish(args: argparse.Namespace) -> int:
    """Remove an episode from the private feed."""
    cfg = _load_cfg(args)
    if cfg is None:
        return int(ExitCode.CONFIG)
    as_json = bool(args.json)
    try:
        result = unpublish_episode(args.episode, cfg)
    except SaseListenError as exc:
        if as_json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": int(exc.code),
                            "message": str(exc),
                            "hint": exc.hint,
                        },
                    }
                )
            )
        else:
            print(f"sase-listen unpublish: error: {exc}")
            if exc.hint:
                print(f"hint: {exc.hint}")
        return int(exc.code)
    if as_json:
        print(json.dumps({"ok": True, **result}))
    else:
        print(f"Unpublished {result['episode_id']} ({result['episodes']} left).")
    return int(ExitCode.OK)
