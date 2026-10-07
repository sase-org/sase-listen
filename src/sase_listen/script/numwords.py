"""Spelled-out source numbers for W013 number fidelity.

Owner: script phase.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_ONES: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_TENS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_ORDINAL_ONES: dict[str, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "zeroth": 0,
}

_ORDINAL_TENS: dict[str, int] = {
    "twentieth": 20,
    "thirtieth": 30,
    "fortieth": 40,
    "fiftieth": 50,
    "sixtieth": 60,
    "seventieth": 70,
    "eightieth": 80,
    "ninetieth": 90,
}

_SCALES: dict[str, int] = {
    "hundred": 100,
    "thousand": 1000,
    "million": 1000000,
    "billion": 1000000000,
    "trillion": 1000000000000,
}

_ORDINAL_SCALES: dict[str, int] = {
    "hundredth": 100,
    "thousandth": 1000,
    "millionth": 1000000,
    "billionth": 1000000000,
    "trillionth": 1000000000000,
}

_WORD_VALUES: dict[str, int] = {**_ONES, **_TENS, **_ORDINAL_ONES, **_ORDINAL_TENS}
_SCALE_VALUES: dict[str, int] = {**_SCALES, **_ORDINAL_SCALES}

_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+")
_DIGIT_RE = re.compile(r"^\d[\d,]*(\.\d+)?$")
_HUNDRED = Decimal(100)


def _parse_base(tokens: list[str], start: int) -> tuple[Decimal, int] | None:
    """Parse one English number phrase; return (value, end_index)."""
    total = Decimal(0)
    current = Decimal(0)
    seen = False
    pos = start
    while pos < len(tokens):
        token = tokens[pos]
        if token == "and":
            nxt = tokens[pos + 1] if pos + 1 < len(tokens) else None
            if (
                seen
                and nxt is not None
                and (
                    nxt in _WORD_VALUES
                    or nxt in _SCALE_VALUES
                    or nxt in ("a", "an")
                    or _DIGIT_RE.match(nxt) is not None
                )
            ):
                pos += 1
                continue
            break
        if token in ("a", "an"):
            nxt = tokens[pos + 1] if pos + 1 < len(tokens) else None
            if nxt is not None and (
                nxt in _SCALE_VALUES
                or nxt in _WORD_VALUES
                or _DIGIT_RE.match(nxt) is not None
            ):
                current += Decimal(1)
                seen = True
                pos += 1
                continue
            break
        if token in _WORD_VALUES:
            current += Decimal(_WORD_VALUES[token])
            seen = True
            pos += 1
            continue
        if token in _SCALE_VALUES:
            scale = Decimal(_SCALE_VALUES[token])
            if scale == _HUNDRED:
                current = Decimal(100) if current == 0 else current * _HUNDRED
            else:
                if current == 0:
                    current = Decimal(1)
                total += current * scale
                current = Decimal(0)
            seen = True
            pos += 1
            continue
        if _DIGIT_RE.match(token) is not None:
            if current != 0:
                break
            try:
                current = Decimal(token.replace(",", ""))
            except InvalidOperation:
                break
            seen = True
            pos += 1
            continue
        break
    if not seen:
        return None
    return (total + current, pos)


def _format_plain(value: Decimal) -> str | None:
    """Format a parsed value as canonical digits, or None when not exact."""
    if value == value.to_integral_value():
        return str(int(value))
    return None


def _format_decimal(value: Decimal) -> str:
    """Format a non-integral value without exponent or trailing zeros."""
    normalized = value.normalize()
    text = format(normalized, "f")
    return text


def source_number_forms(text: str) -> set[str]:
    """Return canonical digit strings for English number phrases in text."""
    tokens = _TOKEN_RE.findall(text.lower())
    forms: set[str] = set()
    for start in range(len(tokens)):
        parsed = _parse_base(tokens, start)
        if parsed is None:
            continue
        value, end = parsed
        is_percent = False
        if end < len(tokens) and tokens[end] == "percent":
            is_percent = True
            end += 1
        elif (
            end + 1 < len(tokens) and tokens[end] == "per" and tokens[end + 1] == "cent"
        ):
            is_percent = True
            end += 2
        plain = _format_plain(value)
        if plain is not None:
            forms.add(plain)
            if is_percent:
                forms.add(plain + "%")
        elif is_percent:
            forms.add(_format_decimal(value) + "%")
    return forms
