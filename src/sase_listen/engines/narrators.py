"""Narrator profile resolution over the scaffold's config.

Owner: engines phase.
"""

from __future__ import annotations

from dataclasses import dataclass

from sase_listen.config import SaseListenConfig
from sase_listen.errors import ExitCode, SaseListenError


@dataclass(frozen=True)
class ResolvedNarrator:
    """A narrator name resolved to concrete engine coordinates."""

    name: str
    engine: str
    model: str
    voice: str
    style: str
    speed: float
    base_url: str


def resolve_narrator(
    name: str,
    config: SaseListenConfig,
    *,
    voice_override: str = "",
) -> ResolvedNarrator:
    """Merge built-in and user narrator profiles; `--voice` overrides voice only."""
    profile = config.narrators.get(name)
    if profile is None:
        known = ", ".join(sorted(config.narrators)) or "(none)"
        raise SaseListenError(
            f"Unknown narrator '{name}'. Known narrators: {known}.",
            code=ExitCode.CONFIG,
            hint=f"Pick one of: {known}.",
        )
    return ResolvedNarrator(
        name=name,
        engine=profile.engine,
        model=profile.model,
        voice=voice_override or profile.voice,
        style=profile.style,
        speed=profile.speed,
        base_url=profile.base_url,
    )
