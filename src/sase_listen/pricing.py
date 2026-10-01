"""Dated USD pricing estimates for TTS models. Owner: engines phase.

Every estimate is approximate (labeled "~" in the UI). Unknown models
(tone, Kokoro self-hosted) cost nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml


@dataclass(frozen=True)
class PriceEntry:
    """One dated USD-per-audio-minute rate."""

    start: date
    usd_per_minute: float


def _parse_entry(raw: Any) -> PriceEntry:
    start = raw["from"]
    if isinstance(start, str):
        start = date.fromisoformat(start)
    return PriceEntry(start=start, usd_per_minute=float(raw["usd_per_minute"]))


@lru_cache(maxsize=1)
def load_table() -> dict[str, list[PriceEntry]]:
    """Load the packaged pricing table (model -> dated rates, oldest first)."""
    data_dir = resources.files("sase_listen") / "data"
    raw = yaml.safe_load((data_dir / "pricing.yml").read_text(encoding="utf-8"))
    table: dict[str, list[PriceEntry]] = {}
    for row in raw or []:
        entries = sorted(
            (_parse_entry(item) for item in row.get("entries", [])),
            key=lambda entry: entry.start,
        )
        table[str(row["model"])] = entries
    return table


def usd_per_minute(model: str, on: date | None = None) -> float:
    """Return the USD-per-audio-minute rate for `model` on `on` (0.0 unknown)."""
    day = on or date.today()
    entries = load_table().get(model)
    if not entries:
        return 0.0
    rate = entries[0].usd_per_minute
    for entry in entries:
        if entry.start <= day:
            rate = entry.usd_per_minute
        else:
            break
    return rate


def estimate(model: str, seconds: float, on: date | None = None) -> float:
    """Estimate the USD cost of `seconds` of audio from `model`."""
    return usd_per_minute(model, on) * max(0.0, seconds) / 60.0
