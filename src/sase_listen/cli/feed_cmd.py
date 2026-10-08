"""feed command: init, status, rebuild, prune, and receive. Owner: feed-host phase."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from sase_listen import invocation
from sase_listen.errors import ExitCode, SaseListenError

if TYPE_CHECKING:
    from sase_listen.config import SaseListenConfig


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    prog: str = "sase-listen",
) -> argparse.ArgumentParser:
    """Register the feed parser."""
    p = sub.add_parser("feed", help="Manage the private podcast feed.")
    p.add_argument(
        "action",
        nargs="?",
        default="",
        choices=["", "init", "prune", "rebuild", "receive"],
        help="init, prune, rebuild, or receive (host-only transport).",
    )
    p.add_argument(
        "episode_id",
        nargs="?",
        default="",
        help="Episode id (required for feed receive).",
    )
    p.add_argument("--base-url", default="", help="Public base URL for feed init.")
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Print config instead of writing.",
    )
    p.add_argument("--qr", action="store_true", help="Print a terminal QR code.")
    p.add_argument("--show-url", action="store_true", help="Show the unmasked URL.")
    p.set_defaults(func=run)
    p.epilog = (
        f"Example: {prog} feed init --base-url https://host:8443. "
        "`feed receive` is the internal SSH transport endpoint."
    )
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
    from sase_listen.config import load_config

    try:
        cfg, _ = load_config()
    except ValueError as exc:
        wrapped = SaseListenError(str(exc), ExitCode.CONFIG)
        _print_error(
            wrapped, as_json=bool(args.json), prefix=invocation.command("feed")
        )
        return None
    return cfg


def _run_init(args: argparse.Namespace, cfg: SaseListenConfig) -> int:
    from sase_listen.feed import init_feed, print_qr
    from sase_listen.feedhost import feed_role

    as_json = bool(args.json)
    if feed_role(cfg) == "remote":
        host = cfg.feed.host.strip()
        return _print_error(
            SaseListenError(
                f"This machine publishes to feed host {host}; "
                f"run `{invocation.command('feed', 'init')}` on {host}.",
                ExitCode.USAGE,
            ),
            as_json=as_json,
            prefix=invocation.command("feed", "init"),
        )
    try:
        info = init_feed(args.base_url or "", print_only=bool(args.print_only))
    except SaseListenError as exc:
        return _print_error(
            exc, as_json=as_json, prefix=invocation.command("feed", "init")
        )
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


def _annotate_status(
    status: dict[str, object], cfg: SaseListenConfig, via: str
) -> None:
    # Host JSON already has via=local; the client must overwrite with the
    # SSH destination actually used (e.g. apollo), not setdefault.
    from sase_listen.feedhost import pending_publishes

    status["via"] = via
    status["outbox_pending"] = len(pending_publishes())
    if "host" not in status:
        status["host"] = cfg.feed.host.strip()


def _print_status(status: dict[str, object], *, qr: str) -> None:
    host = str(status.get("host") or "")
    via = str(status.get("via") or "local")
    if host:
        print(f"Host: {host} (via {via})")
    if status.get("url"):
        print(f"URL: {status['url']}")
    else:
        print("Feed is not configured yet.")
        print(
            f"Run `{invocation.command('feed', 'init')} --base-url URL` to set it up."
        )
    print(f"Episodes: {status['episodes']}  Size: {status['size_bytes']} bytes")
    print(f"Last build: {status['last_build'] or 'never'}")
    retention = status["retention"]
    if isinstance(retention, dict):
        print(
            f"Retention: {retention['retention_days']} days, "
            f"max {retention['max_episodes']} episodes"
        )
    print(f"Feed dir (served-only): {status['feed_dir']}")
    raw_pending = status.get("outbox_pending", 0)
    pending = raw_pending if isinstance(raw_pending, int) else 0
    print(f"Outbox: {pending} pending")
    if qr:
        print(qr)


def _run_status(args: argparse.Namespace, cfg: SaseListenConfig) -> int:
    from sase_listen.cli.progress import activity
    from sase_listen.feed import feed_status, masked_subscribe_url, print_qr
    from sase_listen.feedhost import feed_role, refuse_if_misrouted, run_remote

    as_json = bool(args.json)
    show = bool(args.show_url)
    want_qr = bool(args.qr)
    try:
        refuse_if_misrouted(cfg)
        via = "local"
        qr_url = ""
        if feed_role(cfg) == "remote":
            remote_args = ["feed", "--json"]
            if show or want_qr:
                remote_args.append("--show-url")
            host = cfg.feed.host.strip()
            with activity(
                f"Asking {host} for the feed status…", enabled=not as_json
            ) as act:
                status, dest = run_remote(cfg, remote_args, on_attempt=act.update)
            via = dest
            qr_url = str(status.get("url") or "")
            if not show and qr_url and "/****/" not in qr_url:
                parts = qr_url.split("/")
                # https://host:8443/<token>/feed.xml -> mask the token segment.
                if len(parts) >= 4:
                    parts[-2] = "****"
                    status["url"] = "/".join(parts)
                    status["url_masked"] = True
        else:
            status = feed_status(cfg, show_url=show)
            if want_qr:
                qr_url = masked_subscribe_url(cfg, show=True)
        _annotate_status(status, cfg, via)
        qr = ""
        if want_qr:
            if not qr_url:
                raise SaseListenError(
                    "No feed URL is configured.",
                    ExitCode.CONFIG,
                    hint=(
                        f"Run `{invocation.command('feed', 'init')}"
                        " --base-url URL` first."
                    ),
                )
            qr = print_qr(qr_url)
            if as_json:
                status["qr"] = qr
    except SaseListenError as exc:
        return _print_error(exc, as_json=as_json, prefix=invocation.command("feed"))
    if as_json:
        print(json.dumps({"ok": True, **status}))
        return int(ExitCode.OK)
    _print_status(status, qr=qr)
    return int(ExitCode.OK)


def _run_rebuild(
    args: argparse.Namespace, cfg: SaseListenConfig, *, prune_only: bool
) -> int:
    from sase_listen.cli.progress import activity
    from sase_listen.feed import rebuild_feed
    from sase_listen.feedhost import feed_role, refuse_if_misrouted, run_remote

    as_json = bool(args.json)
    try:
        refuse_if_misrouted(cfg)
        if feed_role(cfg) == "remote":
            action = "prune" if prune_only else "rebuild"
            host = cfg.feed.host.strip()
            with activity(
                f"Asking {host} to {action} the feed…", enabled=not as_json
            ) as act:
                result, dest = run_remote(
                    cfg, ["feed", action, "--json"], on_attempt=act.update
                )
            result["via"] = dest
            result["host"] = cfg.feed.host.strip()
        else:
            result = rebuild_feed(cfg)
    except SaseListenError as exc:
        return _print_error(exc, as_json=as_json, prefix=invocation.command("feed"))
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


def _run_receive(args: argparse.Namespace, cfg: SaseListenConfig) -> int:
    from sase_listen.feedhost import (
        MAX_RECEIVE_BYTES,
        feed_role,
        receive_episode,
        refuse_if_misrouted,
    )

    as_json = bool(args.json)
    episode_id = str(args.episode_id or "")
    if not episode_id:
        return _print_error(
            SaseListenError(
                "feed receive requires an episode id.",
                ExitCode.USAGE,
                hint=(
                    f"Usage: {invocation.command('feed', 'receive')} EPISODE_ID --json"
                ),
            ),
            as_json=as_json,
            prefix=invocation.command("feed"),
        )
    try:
        refuse_if_misrouted(cfg)
        if feed_role(cfg) != "local":
            host = cfg.feed.host.strip()
            raise SaseListenError(
                f"this machine is not the feed host ({host}); fix feed.host",
                ExitCode.CONFIG,
            )
        data = sys.stdin.buffer.read(MAX_RECEIVE_BYTES + 1)
        if len(data) > MAX_RECEIVE_BYTES:
            raise SaseListenError(
                "episode archive exceeds 256 MiB.",
                ExitCode.USAGE,
            )
        result = receive_episode(episode_id, data, cfg)
    except SaseListenError as exc:
        return _print_error(exc, as_json=True, prefix=invocation.command("feed"))
    print(json.dumps({"ok": True, **{k: v for k, v in result.items() if k != "ok"}}))
    return int(ExitCode.OK)


def run(args: argparse.Namespace) -> int:
    """Run feed init, rebuild, prune, receive, or the default status view."""
    cfg = _load_cfg(args)
    if cfg is None:
        return int(ExitCode.CONFIG)
    action = getattr(args, "action", "")
    if action == "init":
        return _run_init(args, cfg)
    if action == "receive":
        return _run_receive(args, cfg)
    if action in ("rebuild", "prune"):
        return _run_rebuild(args, cfg, prune_only=(action == "prune"))
    return _run_status(args, cfg)
