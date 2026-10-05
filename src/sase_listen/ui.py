"""Shared terminal theme and plain-English formatters (no deps).

Owner: cli phase (scaffold provides constants).
"""

ACCENT = "cyan"
GLYPH_AUDIO = "\u266a"
GLYPH_OK = "\u2713"
GLYPH_WARN = "\u26a0"
GLYPH_FAIL = "\u2717"
GLYPH_APPROX = "\u2248"
GLYPH_PENDING = "\u00b7"
GLYPH_RETRY = "\u21bb"
GLYPH_STOP = "\u25a0"
GLYPH_ARROW = "\u2192"
SPINNER = "dots"


def plural(n: int, one: str, many: str | None = None) -> str:
    """Return "1 attempt" or "2 attempts" style text."""
    word = one if n == 1 else (many if many is not None else one + "s")
    return f"{n} {word}"


def format_words(n: int) -> str:
    """Format a word count with thousands separators ("2,364 words")."""
    return f"{n:,} words"


def format_bytes(n: int) -> str:
    """Format a byte count ("6.8 MB", "512 bytes")."""
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def format_duration(seconds: float, *, active: bool = False) -> str:
    """Format a duration ("0.3s", "41s", "1m 42s", "1h 02m").

    Below 10 s one decimal is shown; active rows use whole seconds so the
    display does not flicker.
    """
    value = max(0.0, seconds)
    if active:
        total = int(value)
        if total < 60:
            return f"{total}s"
        if total < 3600:
            return f"{total // 60}m {total % 60:02d}s"
        return f"{total // 3600}h {(total % 3600) // 60:02d}m"
    if value < 10:
        return f"{value:.1f}s"
    total = int(value)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60:02d}s"
    return f"{total // 3600}h {(total % 3600) // 60:02d}m"


def approx_minutes(seconds: float) -> str:
    """Format an ETA duration ("<1 min", "~40s left" handled by callers)."""
    minutes = seconds / 60.0
    if minutes < 0.5:
        return "<1 min"
    rounded = round(minutes)
    return f"{GLYPH_APPROX}{rounded} min" if rounded != 1 else f"{GLYPH_APPROX}1 min"


def approx_cost(usd: float) -> str:
    """Format a USD estimate ("~$0.19")."""
    return f"{GLYPH_APPROX}${usd:.2f}"
