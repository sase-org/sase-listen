"""Writer protocol and response metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WriterReply:
    """Text and usage metadata returned by a script writer."""

    text: str
    model_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class Writer(Protocol):
    """One-turn text generation interface."""

    def write(self, system: str, user: str) -> WriterReply:
        """Generate a script body from the system and user prompts."""
        ...
