"""Pronunciation lexicon: term to spoken form. Owner: script phase."""

from __future__ import annotations

import hashlib
import re
from importlib.resources import files
from pathlib import Path

import yaml


def _packaged_path() -> Path:
    return Path(str(files("sase_listen") / "data" / "lexicon.yml"))


def _load_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Lexicon {path} must be a mapping.")
    return {str(k): str(v) for k, v in raw.items()}


class Lexicon:
    """Case-sensitive whole-word term replacements for spoken text only."""

    def __init__(self, entries: dict[str, str]) -> None:
        self._entries = dict(entries)
        keys = sorted(self._entries, key=len, reverse=True)
        if keys:
            pattern = r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b"
            self._pattern: re.Pattern[str] | None = re.compile(pattern)
        else:
            self._pattern = None

    @property
    def entries(self) -> dict[str, str]:
        """Return a copy of the term mapping."""
        return dict(self._entries)

    def apply(self, text: str) -> str:
        """Replace whole-word terms with their spoken forms."""
        if self._pattern is None:
            return text
        return self._pattern.sub(lambda m: self._entries[m.group(1)], text)

    def sha256(self) -> str:
        """Return the sha256 of the canonical entry encoding."""
        canonical = "\n".join(f"{k}\t{v}" for k, v in sorted(self._entries.items()))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_default() -> Lexicon:
    """Load the packaged default lexicon."""
    return Lexicon(_load_mapping(_packaged_path()))


def load_merged(user_path: str | Path | None) -> Lexicon:
    """Load the packaged default merged under an optional user file."""
    merged = _load_mapping(_packaged_path())
    if user_path is not None:
        path = Path(str(user_path)).expanduser()
        if path.exists():
            merged.update(_load_mapping(path))
    return Lexicon(merged)


def apply(text: str) -> str:
    """Apply the packaged default lexicon to spoken text."""
    return load_default().apply(text)
