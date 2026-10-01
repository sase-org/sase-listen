"""Episode manifest: the render commit record. Owner: pipeline phase."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Manifest schema version, bumped when fields change meaning.
SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_manifest(
    *,
    episode_id: str,
    title: str,
    version: str,
    source: dict[str, Any],
    script: dict[str, Any],
    narrator: dict[str, Any],
    lexicon_sha256: str,
    chunks: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    audio: dict[str, Any],
    gates: list[dict[str, Any]],
    omissions: list[dict[str, Any]],
    cost_usd_estimate: float,
    published: bool,
) -> dict[str, Any]:
    """Assemble the manifest payload for one rendered episode."""
    return {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "title": title,
        "sase_listen_version": version,
        "created_at": utc_now_iso(),
        "source": source,
        "script": script,
        "narrator": narrator,
        "lexicon_sha256": lexicon_sha256,
        "chunks": chunks,
        "chapters": chapters,
        "audio": audio,
        "gates": gates,
        "omissions": omissions,
        "cost_usd_estimate": cost_usd_estimate,
        "published": published,
    }


def dumps_manifest(payload: Mapping[str, Any]) -> bytes:
    """Serialize a manifest payload to canonical UTF-8 JSON bytes."""
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_manifest_file(path: str | Path) -> dict[str, Any]:
    """Read and parse a ``manifest.json`` file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Manifest {path} must be a JSON object.")
    return dict(raw)
