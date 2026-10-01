"""config command. Owner: scaffold phase (effective config + init)."""

from __future__ import annotations

import argparse
import json

from sase_listen.config import load_config, masked_snapshot
from sase_listen.errors import ExitCode
from sase_listen.paths import config_path

_STARTER = """\
# sase-listen config. Run `sase-listen config` to see origins.
# Secrets: prefer SASE_LISTEN_GEMINI_API_KEY / SASE_LISTEN_OPENAI_API_KEY env,
# or engines.gemini.api_key_command like `pass show gemini_cli_api_key`.
narrator: gemini
author: ""
lexicon: ""
engines:
  gemini:
    concurrency: 3
    timeout_s: 180
    max_retries: 4
  openai:
    concurrency: 3
    timeout_s: 180
    max_retries: 4
audio:
  bitrate_kbps: 64
  sample_rate: 24000
  loudness_lufs: -16.0
  true_peak_db: -1.5
  chunk_gap_s: 0.5
  chapter_gap_s: 1.2
  intro_gap_s: 0.9
cache:
  max_gb: 2.0
feed:
  title: SASE Listen
  auto_publish: false
  retention_days: 90
  max_episodes: 200
"""


def add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the config parser."""
    p = sub.add_parser("config", help="Show effective config or write a starter file.")
    p.add_argument("action", nargs="?", default="", choices=["", "init", "path"])
    p.add_argument("--json", action="store_true", help="Emit one JSON object.")
    p.set_defaults(func=run)
    p.epilog = "Example: sase-listen config init"
    return p


def run(args: argparse.Namespace) -> int:
    """Show config with origins, or init/path helpers."""
    action = getattr(args, "action", "")
    if action == "path":
        if getattr(args, "json", False):
            print(json.dumps({"ok": True, "path": str(config_path())}))
        else:
            print(str(config_path()))
        return int(ExitCode.OK)
    if action == "init":
        target = config_path()
        if target.exists():
            print(f"Refusing to overwrite existing config: {target}")
            return int(ExitCode.USAGE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_STARTER, encoding="utf-8")
        print(str(target))
        return int(ExitCode.OK)
    try:
        cfg, origins = load_config()
    except ValueError as exc:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"ok": False, "error": {"code": 3, "message": str(exc), "hint": ""}}
                )
            )
        else:
            print(f"config error: {exc}")
        return int(ExitCode.CONFIG)
    snapshot = masked_snapshot(cfg)
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "config": snapshot, "origins": origins}))
    else:
        print(f"# {config_path()}")
        for key, value in snapshot.items():
            print(f"{key}: {value!r}  # origin={origins.get(key, 'default')}")
    return int(ExitCode.OK)
