"""Generated title cards and supplied-image cover art. Owner: audio phase."""

from __future__ import annotations

import contextlib
import hashlib
import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

COVER_SIZE = 1400
COVER_QUALITY = 88
_MAX_TITLE_LINES = 5

_FONT_PATH = Path(__file__).resolve().parent.parent / "data" / "fonts" / "Inter.ttf"

FontT = ImageFont.FreeTypeFont | ImageFont.ImageFont

#: Curated two-tone gradient palette; the title hash picks deterministically.
_PALETTE: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = [
    ((18, 38, 74), (38, 92, 140)),
    ((52, 20, 68), (112, 44, 120)),
    ((12, 62, 56), (30, 120, 104)),
    ((74, 28, 20), (150, 66, 40)),
    ((30, 30, 46), (74, 74, 110)),
    ((8, 48, 84), (16, 110, 150)),
]

_SEMIBOLD = 600
_REGULAR = 400


def _font(size: int, weight: int) -> FontT:
    """Bundled Inter at a variable weight, with a built-in fallback."""
    if _FONT_PATH.is_file():
        face = ImageFont.truetype(str(_FONT_PATH), size)
        with contextlib.suppress(Exception):  # No variable axes; default cut.
            face.set_variation_by_axes([weight])
        return face
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default font.
        return ImageFont.load_default()


def _palette(title: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()
    return _PALETTE[int(digest, 16) % len(_PALETTE)]


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: FontT,
    max_width: int,
) -> list[str]:
    """Greedy word wrap to pixel width."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _fit_title(
    draw: ImageDraw.ImageDraw, title: str, max_width: int
) -> tuple[list[str], FontT, int]:
    """Largest SemiBold font (stepping down) that fits in five lines."""
    size = 118
    while size > 30:
        font = _font(size, _SEMIBOLD)
        lines = _wrap(draw, title, font, max_width)
        if len(lines) <= _MAX_TITLE_LINES:
            return lines, font, size
        size -= 6
    font = _font(30, _SEMIBOLD)
    return _wrap(draw, title, font, max_width)[:_MAX_TITLE_LINES], font, 30


def _to_jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=COVER_QUALITY,
        progressive=True,
        optimize=True,
    )
    return buffer.getvalue()


def generate_title_card(
    title: str, kind: str = "document", date_text: str = ""
) -> bytes:
    """Render the 1400x1400 generated cover: gradient, title, motif.

    The gradient palette and the waveform-bar motif derive deterministically
    from the title hash, so the same title always yields the same bytes.
    Research episodes get the ``SASE RESEARCH · AUDIO EDITION`` label;
    anything else gets ``AUDIO EDITION``.
    """
    if not title.strip():
        raise ValueError("Cover art needs a non-empty title.")
    top, bottom = _palette(title)
    image = Image.new("RGB", (COVER_SIZE, COVER_SIZE), top)
    gradient = Image.new("RGB", (1, COVER_SIZE))
    for row in range(COVER_SIZE):
        t = row / (COVER_SIZE - 1)
        gradient.putpixel(
            (0, row),
            (
                round(top[0] + (bottom[0] - top[0]) * t),
                round(top[1] + (bottom[1] - top[1]) * t),
                round(top[2] + (bottom[2] - top[2]) * t),
            ),
        )
    image = gradient.resize((COVER_SIZE, COVER_SIZE))

    overlay = Image.new("RGBA", (COVER_SIZE, COVER_SIZE), (0, 0, 0, 0))
    bars = ImageDraw.Draw(overlay)
    seed = int(hashlib.sha256(title.encode("utf-8")).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    n_bars = 72
    band_top = COVER_SIZE - 260
    slot = COVER_SIZE / n_bars
    for i in range(n_bars):
        height = 30 + rng.random() * 170
        x0 = i * slot + slot * 0.22
        x1 = (i + 1) * slot - slot * 0.22
        bars.rounded_rectangle(
            [x0, band_top + (200 - height), x1, band_top + 200],
            radius=8,
            fill=(255, 255, 255, 56),
        )
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(image)
    margin = 130
    max_width = COVER_SIZE - 2 * margin
    label = "SASE RESEARCH · AUDIO EDITION" if kind == "research" else "AUDIO EDITION"
    label_font = _font(40, _SEMIBOLD)
    draw.text(
        (COVER_SIZE / 2, 300),
        label,
        font=label_font,
        fill="white",
        anchor="mm",
    )
    rule_y = 352
    draw.line(
        [(COVER_SIZE / 2 - 70, rule_y), (COVER_SIZE / 2 + 70, rule_y)],
        fill=(255, 255, 255, 200),
        width=3,
    )
    lines, title_font, title_px = _fit_title(draw, title, max_width)
    leading = int(title_px * 1.22)
    block_h = leading * len(lines)
    y = 640 - block_h / 2
    for line in lines:
        draw.text(
            (COVER_SIZE / 2 + 3, y + 3),
            line,
            font=title_font,
            fill=(0, 0, 0),
            anchor="ma",
        )
        draw.text(
            (COVER_SIZE / 2, y),
            line,
            font=title_font,
            fill="white",
            anchor="ma",
        )
        y += leading
    if date_text:
        date_font = _font(44, _REGULAR)
        draw.text(
            (COVER_SIZE / 2, 640 + block_h / 2 + 70),
            date_text,
            font=date_font,
            fill=(255, 255, 255),
            anchor="ma",
        )
    return _to_jpeg(image)


def letterbox_cover(image_bytes: bytes) -> bytes:
    """Fit a supplied image into the square over a blurred, darkened copy.

    The source is scaled up to cover the square, blurred, and darkened as the
    backdrop; the full image is then letterboxed on top, preserving aspect.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            rgb = source.convert("RGB")
    except Exception as exc:
        raise ValueError(f"Cover image could not be decoded: {exc}") from exc
    scale = max(COVER_SIZE / rgb.width, COVER_SIZE / rgb.height)
    backdrop = rgb.resize(
        (round(rgb.width * scale), round(rgb.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (backdrop.width - COVER_SIZE) // 2
    top = (backdrop.height - COVER_SIZE) // 2
    backdrop = backdrop.crop((left, top, left + COVER_SIZE, top + COVER_SIZE))
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=42))
    dark = Image.new("RGB", (COVER_SIZE, COVER_SIZE), (0, 0, 0))
    backdrop = Image.blend(backdrop, dark, 0.45)
    fit = max(1, int(COVER_SIZE * 0.86))
    scale = min(fit / rgb.width, fit / rgb.height)
    foreground = rgb.resize(
        (round(rgb.width * scale), round(rgb.height * scale)), Image.Resampling.LANCZOS
    )
    backdrop.paste(
        foreground,
        ((COVER_SIZE - foreground.width) // 2, (COVER_SIZE - foreground.height) // 2),
    )
    return _to_jpeg(backdrop)


def resolve_cover(
    supplied: bytes | Path | None,
    title: str,
    kind: str = "document",
    date_text: str = "",
) -> bytes:
    """Supplied image (letterboxed) or the generated title card."""
    if supplied is None:
        return generate_title_card(title, kind=kind, date_text=date_text)
    data = supplied.read_bytes() if isinstance(supplied, Path) else supplied
    return letterbox_cover(data)
