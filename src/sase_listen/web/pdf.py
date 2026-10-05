"""PDF document extraction producing article Markdown locally."""

from __future__ import annotations

import io
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.web.extract import Article, normalize_url

MAX_PDF_PAGES = 400

_ARXIV_RE = re.compile(
    r"^arXiv:\d{4}\.\d{4,5}(v\d+)?\s*\[[^\]]+\]\s+\d{1,2}\s+\w{3}\s+\d{4}$"
)
_ARXIV_DATE_RE = re.compile(r"(\d{1,2})\s+(\w{3})\s+(\d{4})")
_MONTHS = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}
_CITATION_RE = re.compile(r"\s?\[\d+(?:\s*[-–,]\s*\d+)*\]")  # noqa: RUF001
_CID_RE = re.compile(r"\(cid:\d+\)")
_BOLD_MARKERS = ("Bold", "Black", "Heavy", "Semibold", "Demi", "CMBX")
_SUPERSCRIPT_CHARS = set("0123456789*†‡§")
_NUMBER_STRIP_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?|[A-Z]|[A-Z]\.\d+(?:\.\d+)*|[IVX]+\.?)\s+(?=[A-Za-z])"
)
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(\.\d+)*|[IVX]+\.|[A-Z]\.)\s+[A-Z]")
_NAMED_HEADINGS = {
    "abstract",
    "introduction",
    "conclusion",
    "conclusions",
    "discussion",
    "references",
    "bibliography",
    "acknowledgment",
    "acknowledgments",
    "acknowledgement",
    "acknowledgements",
}
_PAGE_NUMBER_RES = (
    re.compile(r"^\d{1,4}$"),
    re.compile(r"^Page \d+( of \d+)?$", re.IGNORECASE),
)
_BOILERPLATE_PREFIXES = (
    "ccs concepts",
    "keywords",
    "index terms",
    "acm reference format",
    "permission to make digital or hard copies",
    "©",
    "copyright",
)
_REFERENCE_TITLES = {
    "references",
    "bibliography",
    "works cited",
    "literature cited",
}


@dataclass(frozen=True)
class PdfExtraction:
    """Extracted PDF article plus diagnostics."""

    article: Article
    pages: int
    authors: list[str]
    outline_source: str
    dropped_small_words: int


PageBox = tuple[float, float, float, float]


@dataclass
class _Line:
    page: int
    box: int
    x0: float
    y0: float
    y1: float
    size: float
    bold: bool
    text: str


def _normalize_alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _strip_numbering(text: str) -> str:
    return _NUMBER_STRIP_RE.sub("", text.strip()).strip()


def _is_bold_font(name: str) -> bool:
    return (
        any(marker in name for marker in _BOLD_MARKERS)
        or name.endswith("TB")
        or name.endswith("-B")
    )


def _decode_info_value(value: object) -> str:
    from pdfminer.utils import decode_text

    if isinstance(value, bytes):
        try:
            return decode_text(value).strip()
        except Exception:
            return value.decode("utf-8", errors="replace").strip()
    if isinstance(value, str):
        return value.strip()
    return str(value).strip() if value is not None else ""


def _read_info(doc: object) -> tuple[dict[str, str], dict[str, str]]:
    from pdfminer.pdftypes import resolve1

    raw_info: list[object] = list(getattr(doc, "info", []) or [])
    merged: dict[str, str] = {}
    merged_lower: dict[str, str] = {}
    for entry in raw_info:
        resolved = resolve1(entry)
        if not isinstance(resolved, dict):
            continue
        for key, value in resolved.items():
            if isinstance(key, bytes):
                try:
                    from pdfminer.utils import decode_text

                    key_str = decode_text(key)
                except Exception:
                    key_str = key.decode("utf-8", errors="replace")
            else:
                key_str = str(key)
            text = _decode_info_value(resolve1(value))
            if key_str and key_str not in merged:
                merged[key_str] = text
                merged_lower[key_str.lower()] = text
    return merged, merged_lower


def _read_outlines(doc: object) -> list[tuple[int, str]]:
    try:
        get_outlines = getattr(doc, "get_outlines", None)
        if get_outlines is None:
            return []
        raw = list(get_outlines())
    except Exception:
        return []
    entries: list[tuple[int, str]] = []
    for item in raw:
        try:
            level = int(item[0])
            title = item[1]
        except (IndexError, TypeError, ValueError):
            continue
        if isinstance(title, bytes):
            title = _decode_info_value(title)
        else:
            title = str(title).strip()
        if title:
            entries.append((level, title))
    if not entries:
        return []
    minimum = min(level for level, _ in entries)
    if minimum > 1:
        entries = [(level - minimum + 1, title) for level, title in entries]
    elif minimum < 1:
        entries = [(max(1, level - minimum + 1), title) for level, title in entries]
    return entries


def _collect_lines(data: bytes) -> tuple[list[_Line], list[PageBox]]:
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTAnno, LTChar, LTTextBox, LTTextLine

    page_boxes: list[tuple[float, float, float, float]] = []
    lines: list[_Line] = []

    def _boxes(obj: object) -> list[object]:
        from pdfminer.layout import LTFigure

        found: list[object] = []
        children: list[object] = []
        try:
            if isinstance(obj, Iterable):
                children = list(cast("Iterable[object]", obj))
            else:
                return found
        except TypeError:
            return found
        for child in children:
            if isinstance(child, LTTextBox):
                found.append(child)
            elif isinstance(child, LTFigure):
                found.extend(_boxes(child))
        return found

    pages = list(extract_pages(io.BytesIO(data), laparams=LAParams()))
    for page_number, page in enumerate(pages):
        try:
            x0, y0, x1, y1 = page.bbox
        except (AttributeError, TypeError, ValueError):
            continue
        page_boxes.append((float(x0), float(y0), float(x1), float(y1)))
        for box_index, box in enumerate(_boxes(page)):
            try:
                if not isinstance(box, Iterable):
                    continue
                box_children = list(cast("Iterable[object]", box))
            except TypeError:
                continue
            for child in box_children:
                if not isinstance(child, LTTextLine):
                    continue
                chars = [c for c in child if isinstance(c, LTChar)]
                if not chars:
                    continue
                sizes = [round(float(c.size), 1) for c in chars]
                counts: dict[float, int] = {}
                for size in sizes:
                    counts[size] = counts.get(size, 0) + 1
                dominant = max(counts, key=lambda s: counts[s])
                parts: list[str] = []
                for item in child:
                    if isinstance(item, LTAnno):
                        piece = item.get_text()
                        if piece == "\n":
                            continue
                        parts.append(piece)
                        continue
                    if not isinstance(item, LTChar):
                        continue
                    text = item.get_text()
                    if not text or text in ("\n", "\r"):
                        continue
                    if (
                        text in _SUPERSCRIPT_CHARS
                        and float(item.size) <= 0.8 * dominant
                    ):
                        continue
                    parts.append(text)
                text = "".join(parts).strip()
                if not text:
                    continue
                try:
                    _lx0, ly0, _lx1, ly1 = child.bbox
                    x0 = float(_lx0)
                except (AttributeError, TypeError, ValueError):
                    continue
                bold = any(
                    _is_bold_font(str(getattr(c, "fontname", ""))) for c in chars
                )
                lines.append(
                    _Line(
                        page=page_number,
                        box=box_index,
                        x0=x0,
                        y0=float(ly0),
                        y1=float(ly1),
                        size=float(dominant),
                        bold=bold,
                        text=text,
                    )
                )
    return lines, page_boxes


def _body_size(lines: list[_Line]) -> float:
    counts: dict[float, int] = {}
    for line in lines:
        words = len(line.text.split())
        counts[line.size] = counts.get(line.size, 0) + max(1, words)
    if not counts:
        return 0.0
    return max(counts, key=lambda s: counts[s])


def _header_footer_drop(
    lines: list[_Line], page_boxes: list[tuple[float, float, float, float]]
) -> set[int]:
    band: dict[int, bool] = {}
    for index, line in enumerate(lines):
        if line.page >= len(page_boxes):
            band[index] = False
            continue
        _, py0, _, py1 = page_boxes[line.page]
        height = py1 - py0
        if height <= 0:
            band[index] = False
            continue
        in_top = line.y1 >= py1 - 0.08 * height
        in_bottom = line.y0 <= py0 + 0.08 * height
        band[index] = bool(in_top or in_bottom)
    seen: dict[str, int] = {}
    for index, line in enumerate(lines):
        if not band.get(index):
            continue
        key = re.sub(r"[^a-z]", "", re.sub(r"\d+", "", line.text.lower()))
        if len(key) >= 4:
            seen[key] = seen.get(key, 0) + 1
    drop: set[int] = set()
    for index, line in enumerate(lines):
        if not band.get(index):
            continue
        key = re.sub(r"[^a-z]", "", re.sub(r"\d+", "", line.text.lower()))
        if len(key) >= 4 and seen.get(key, 0) >= 2:
            drop.add(index)
            continue
        stripped = line.text.strip()
        if any(pattern.match(stripped) for pattern in _PAGE_NUMBER_RES):
            drop.add(index)
    return drop


def _page_columns(lines: list[_Line]) -> dict[int, list[float]]:
    """Cluster each page's text-box left edges into column starts."""
    boxes: dict[tuple[int, int], list[int]] = {}
    for index, line in enumerate(lines):
        boxes.setdefault((line.page, line.box), []).append(index)
    starts: dict[int, list[float]] = {}
    for (page, _), members in boxes.items():
        x0 = min(lines[i].x0 for i in members)
        starts.setdefault(page, []).append(x0)
    return {page: _page_column_starts(x0s) for page, x0s in starts.items()}


def _reading_order(lines: list[_Line]) -> list[int]:
    """Return line indexes in reading order (page, column, top, left)."""
    columns = _page_columns(lines)

    def _key(index: int) -> tuple[int, int, float, float]:
        line = lines[index]
        starts = columns.get(line.page, [])
        return (line.page, _column_of(starts, line.x0), -line.y1, line.x0)

    return sorted(range(len(lines)), key=_key)


def _join_box_lines(parts: list[str]) -> str:
    acc = ""
    for piece in parts:
        if not piece:
            continue
        if acc:
            if acc.endswith(("-", "\u00ad", "\ufffe")) and piece[:1].islower():
                acc = acc[:-1] + piece
            else:
                acc = acc + " " + piece
        else:
            acc = piece
    return acc.strip()


def _page_column_starts(box_x0s: list[float], *, gap: float = 60.0) -> list[float]:
    """Cluster box left edges into column starts (left to right)."""
    starts: list[float] = []
    for x0 in sorted(box_x0s):
        if not starts or x0 - starts[-1] > gap:
            starts.append(x0)
        elif x0 < starts[-1]:
            starts[-1] = x0
    return starts


def _column_of(starts: list[float], x0: float) -> int:
    column = 0
    for index, start in enumerate(starts):
        if x0 >= start - 1.0:
            column = index
    return column


def _humanize_stem(text: str) -> str:
    cleaned = re.sub(r"[-_]+", " ", text).strip()
    return re.sub(r"\s+", " ", cleaned)


def _choose_title(
    info: dict[str, str],
    lines: list[_Line],
    *,
    filename: str,
    source_url: str,
) -> tuple[str, float]:
    candidate = info.get("Title", "").strip()
    implausible = (
        not candidate
        or len(candidate) < 4
        or candidate.lower() == "untitled"
        or candidate.startswith("Microsoft Word - ")
        or candidate.lower().endswith((".pdf", ".doc", ".docx", ".tex", ".dvi"))
    )
    if not implausible:
        return candidate, 0.0
    page1 = [line for line in lines if line.page == 0]
    if page1:
        top = max(line.size for line in page1)
        group: list[str] = []
        for line in page1:
            if line.size == top:
                group.append(line.text.strip())
            elif group:
                break
        if group:
            return " ".join(group).strip(), top
    if filename:
        stem = Path(filename).stem.strip()
        if stem:
            return _humanize_stem(stem), 0.0
    if source_url:
        segment = urlsplit(source_url).path.rstrip("/").split("/")[-1]
        segment = re.sub(r"\.pdf$", "", segment, flags=re.IGNORECASE)
        if segment:
            return _humanize_stem(segment), 0.0
    return "Untitled PDF", 0.0


def _split_authors(raw: str) -> list[str]:
    parts = [part.strip() for part in raw.split(";") if part.strip()]
    expanded: list[str] = []
    for part in parts:
        expanded.extend(
            [piece.strip() for piece in re.split(r"\s+and\s+", part) if piece.strip()]
        )
    if len(expanded) == 1 and expanded[0].count(",") >= 2:
        expanded = [piece.strip() for piece in expanded[0].split(",") if piece.strip()]
    return [author for author in expanded if author]


def _author_credit(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) == 1:
        return authors[0]
    if len(authors) == 2:
        return f"{authors[0]} and {authors[1]}"
    if len(authors) == 3:
        return f"{authors[0]}, {authors[1]}, and {authors[2]}"
    return f"{authors[0]} and colleagues"


def _arxiv_date(text: str) -> str:
    match = _ARXIV_DATE_RE.search(text)
    if not match:
        return ""
    day, mon, year = match.group(1), match.group(2).lower(), match.group(3)
    month = _MONTHS.get(mon[:3])
    if not month:
        return ""
    return f"{year}-{month}-{int(day):02d}"


def _creation_date(raw: str) -> str:
    match = re.match(r"D:(\d{4})(\d{2})(\d{2})", raw.strip())
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw.strip())
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return ""


def _match_bookmark_headings(
    lines: list[_Line],
    entries: list[tuple[int, str]],
    keep: set[int],
    order: list[int],
) -> tuple[
    list[tuple[int, str, int]],
    list[str],
    list[str],
    list[str],
    dict[int, str],
    set[int],
]:
    order = [index for index in order if index in keep]
    entry_norm = [_normalize_alnum(title) for _, title in entries]
    stripped_entries = [_strip_numbering(title) for _, title in entries]
    consumed = [False] * len(entries)
    headings: list[tuple[int, str, int]] = []
    remainders: dict[int, str] = {}
    consumed_extra: set[int] = set()
    index_pos = 0
    while index_pos < len(order):
        line_index = order[index_pos]
        if line_index in consumed_extra:
            index_pos += 1
            continue
        line_norm = _normalize_alnum(lines[line_index].text)
        unconsumed = [i for i, done in enumerate(consumed) if not done]
        matched: int | None = None
        wrapped = False
        for candidate in unconsumed[:3]:
            want = entry_norm[candidate]
            if not want:
                consumed[candidate] = True
                continue
            if line_norm == want or line_norm.startswith(want):
                matched = candidate
                break
            if index_pos + 1 < len(order):
                next_index = order[index_pos + 1]
                if (
                    lines[next_index].page == lines[line_index].page
                    and lines[next_index].box == lines[line_index].box
                ):
                    joined = _normalize_alnum(
                        lines[line_index].text + " " + lines[next_index].text
                    )
                    if joined == want or joined.startswith(want):
                        matched = candidate
                        wrapped = True
                        break
        if matched is None:
            index_pos += 1
            continue
        consumed[matched] = True
        level, _ = entries[matched]
        want = entry_norm[matched]
        if wrapped:
            next_index = order[index_pos + 1]
            combined = (
                lines[line_index].text.strip() + " " + lines[next_index].text.strip()
            ).strip()
            text = _strip_numbering(combined)
            headings.append((level, text, line_index))
            consumed_extra.add(next_index)
            combined_words = combined.split()
            entry_words = stripped_entries[matched].split()
            if len(combined_words) > len(entry_words):
                remainders[line_index] = " ".join(combined_words[len(entry_words) :])
            index_pos += 2
            continue
        text = _strip_numbering(lines[line_index].text)
        headings.append((level, text, line_index))
        if line_norm.startswith(want) and len(line_norm) > len(want):
            # Line starts with the heading text: split the remainder off as
            # paragraph text.
            heading_words = len(_normalize_alnum(text).strip())
            _ = heading_words
            raw = lines[line_index].text.strip()
            stripped = _strip_numbering(raw)
            norm_stripped = _normalize_alnum(stripped)
            if norm_stripped.startswith(want) and len(norm_stripped) > len(want):
                # Best-effort split: drop the heading prefix words.
                entry_words = stripped_entries[matched].split()
                raw_words = stripped.split()
                if len(raw_words) > len(entry_words):
                    remainders[line_index] = " ".join(raw_words[len(entry_words) :])
        index_pos += 1
    restored = [text for _, text, _ in headings]
    matched_norms = {_normalize_alnum(text) for text in restored}
    missing: list[str] = []
    for stripped in stripped_entries:
        if _normalize_alnum(stripped) not in matched_norms and stripped not in restored:
            missing.append(stripped)
    seen_missing: set[str] = set()
    unique_missing: list[str] = []
    for item in missing:
        if item not in seen_missing:
            seen_missing.add(item)
            unique_missing.append(item)
    return (
        headings,
        stripped_entries,
        restored,
        unique_missing,
        remainders,
        consumed_extra,
    )


def _detect_font_headings(
    lines: list[_Line],
    keep: set[int],
    body: float,
    title_size: float,
    order: list[int],
) -> tuple[list[tuple[int, str, int]], list[str], list[str], str]:
    candidates: list[tuple[int, float]] = []
    for index in order:
        if index not in keep:
            continue
        line = lines[index]
        if line.size == title_size and line.page == 0:
            continue
        text = line.text.strip()
        if not text:
            continue
        words = text.split()
        terminal = text.rstrip().endswith((".", ":", ";"))
        big = line.size >= 1.15 * body
        named = text.lower() in _NAMED_HEADINGS or (
            text.lower().startswith("appendix") and len(text) < 80
        )
        small_bold = (
            line.bold
            and abs(line.size - body) < 0.05 * body + 0.051
            and len(words) <= 12
            and not terminal
            and (bool(_NUMBERED_HEADING_RE.match(text)) or named)
        )
        if big or small_bold:
            candidates.append((index, line.size))
    if not candidates:
        return [], [], [], "none"
    top_size = max(size for _, size in candidates)
    headings: list[tuple[int, str, int]] = []
    for index, size in candidates:
        text = _strip_numbering(lines[index].text.strip())
        raw = lines[index].text.strip()
        dotted = bool(re.match(r"^\d+\.\d+", raw))
        level = 1 if size >= top_size - 0.051 and not dotted else 2
        headings.append((level, text, index))
    outline = [text for _, text, _ in headings]
    return headings, outline, [], "fonts"


def _drop_references(
    headings: list[tuple[int, str, int]],
) -> tuple[set[int], int | None, int | None]:
    start: int | None = None
    start_level = 0
    for pos, (level, text, _) in enumerate(headings):
        if text.strip().lower() in _REFERENCE_TITLES:
            start = pos
            start_level = level
            break
    if start is None:
        return set(), None, None
    end: int | None = None
    for pos in range(start + 1, len(headings)):
        if headings[pos][0] <= start_level:
            end = pos
            break
    dropped = set(range(start, end if end is not None else len(headings)))
    # The references heading itself is dropped from output; a later appendix
    # (deeper or same-or-higher level heading after the gap) survives.
    return dropped, start, end


def _cleanup_text(text: str) -> tuple[str, int]:
    text = unicodedata.normalize("NFKC", text)
    cid_count = len(_CID_RE.findall(text))
    text = _CID_RE.sub("", text)
    text = _CITATION_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text, cid_count


def extract_pdf(
    data: bytes, *, source_url: str = "", filename: str = ""
) -> PdfExtraction:
    """Extract article Markdown from PDF bytes (importing pdfminer lazily)."""
    from pdfminer.pdfdocument import PDFDocument, PDFNoOutlines
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser, PDFSyntaxError
    from pdfminer.pdftypes import resolve1
    from pdfminer.psparser import PSException

    _ = (PDFNoOutlines, resolve1)
    try:
        parser = PDFParser(io.BytesIO(data))
        doc = PDFDocument(parser)
    except Exception as exc:
        name = type(exc).__name__
        if "password" in name.lower() or "Password" in str(exc):
            raise SaseListenError(
                "The PDF is password-protected.",
                ExitCode.USAGE,
                hint="Remove the password and render the file again.",
            ) from exc
        raise SaseListenError(
            f"Could not read the PDF: {exc}.",
            ExitCode.USAGE,
            hint="Convert it to Markdown and render that file.",
        ) from exc
    if getattr(doc, "is_extractable", True) is False:
        pass
    try:
        page_count = len(list(PDFPage.create_pages(doc)))
    except Exception as exc:
        name = type(exc).__name__
        if "password" in name.lower():
            raise SaseListenError(
                "The PDF is password-protected.",
                ExitCode.USAGE,
                hint="Remove the password and render the file again.",
            ) from exc
        if isinstance(exc, (PDFSyntaxError, PSException)):
            raise SaseListenError(
                f"Could not read the PDF: {exc}.",
                ExitCode.USAGE,
                hint="Convert it to Markdown and render that file.",
            ) from exc
        raise SaseListenError(
            f"Could not read the PDF: {exc}.",
            ExitCode.USAGE,
            hint="Convert it to Markdown and render that file.",
        ) from exc
    if page_count > MAX_PDF_PAGES:
        raise SaseListenError(
            f"The PDF has {page_count} pages (limit is {MAX_PDF_PAGES}).",
            ExitCode.USAGE,
            hint="Split the PDF and render one part.",
        )
    if page_count == 0:
        raise SaseListenError(
            "Could not read the PDF: no pages found.",
            ExitCode.USAGE,
            hint="Convert it to Markdown and render that file.",
        )
    info, info_lower = _read_info(doc)
    try:
        outlines = _read_outlines(doc)
    except Exception:
        outlines = []
    try:
        lines, page_boxes = _collect_lines(data)
    except Exception as exc:
        raise SaseListenError(
            f"Could not read the PDF: {exc}.",
            ExitCode.USAGE,
            hint="Convert it to Markdown and render that file.",
        ) from exc
    if not lines:
        raise SaseListenError(
            "The extracted PDF text is too short (0 words).",
            ExitCode.UNEXPECTED,
            hint=(
                "The PDF may be a scanned image without a text layer; "
                "OCR is not supported. Convert it to Markdown and render that file."
            ),
        )
    body = _body_size(lines) or 9.0
    drop = _header_footer_drop(lines, page_boxes)
    dropped_small_words = 0
    arxiv_text: str | None = None
    arxiv_date = ""
    for index, line in enumerate(lines):
        if index in drop:
            continue
        if _ARXIV_RE.match(line.text.strip()):
            arxiv_text = line.text.strip()
            arxiv_date = _arxiv_date(arxiv_text)
            drop.add(index)
    keep: set[int] = set(range(len(lines))) - drop
    # Small text filter.
    for index in list(keep):
        if lines[index].size < 0.85 * body:
            dropped_small_words += len(lines[index].text.split())
            keep.discard(index)
    # Boilerplate filter.
    for index in list(keep):
        lowered = lines[index].text.strip().lower()
        if lowered.startswith(_BOILERPLATE_PREFIXES):
            keep.discard(index)
    full_order = _reading_order(lines)
    rank = {index: pos for pos, index in enumerate(full_order)}
    page1_ordered = [lines[i] for i in full_order if i in keep and lines[i].page == 0]
    title, title_size = _choose_title(
        info, page1_ordered, filename=filename, source_url=source_url
    )
    title_norm = _normalize_alnum(title)
    # Headings.
    headings: list[tuple[int, str, int]] = []
    outline: list[str] = []
    restored: list[str] = []
    missing: list[str] = []
    remainders: dict[int, str] = {}
    wrapped_skip: set[int] = set()
    outline_source = "none"
    if outlines:
        (
            headings,
            outline,
            restored,
            missing,
            remainders,
            wrapped_skip,
        ) = _match_bookmark_headings(lines, outlines, keep, full_order)
        keep -= wrapped_skip
        outline_source = "bookmarks"
        if not headings:
            outline_source = "none"
            outline, restored, missing = [], [], []
    if not headings:
        detected, det_outline, det_missing, det_source = _detect_font_headings(
            lines, keep, body, title_size, full_order
        )
        if detected:
            headings, outline, missing, outline_source = (
                detected,
                det_outline,
                det_missing,
                det_source,
            )
            restored = list(outline)
        else:
            outline_source = "none"
    heading_indexes = {line_index for _, _, line_index in headings}
    # Page-1 front matter: everything on page 1 before the first heading,
    # except title lines.
    page1_headings = [h for h in headings if lines[h[2]].page == 0]
    if page1_headings:
        first_heading = min(page1_headings, key=lambda h: rank[h[2]])
        first_top = lines[first_heading[2]].y1
        for index in list(keep):
            line = lines[index]
            if line.page == 0 and line.y0 >= first_top - 1.0:
                if _normalize_alnum(line.text) == title_norm:
                    continue
                if (
                    title_size
                    and line.size == title_size
                    and _normalize_alnum(line.text)
                ):
                    # Title lines (largest-size group) survive.
                    continue
                keep.discard(index)
                if index in heading_indexes:
                    heading_indexes.discard(index)
        headings = [h for h in headings if h[2] in keep or h[2] in heading_indexes]
        # Rebuild heading index set after the front-matter cut.
        heading_indexes = {h[2] for h in headings}
    # References cut.
    dropped_ref_pos, _, _ = _drop_references(headings)
    ref_line_indexes = (
        {headings[pos][2] for pos in dropped_ref_pos} if dropped_ref_pos else set()
    )
    live_headings = [h for pos, h in enumerate(headings) if pos not in dropped_ref_pos]
    live_heading_indexes = {h[2] for h in live_headings}
    # Drop the body lines inside the references span.
    if dropped_ref_pos:
        ref_positions = sorted(dropped_ref_pos)
        first_ref_rank = rank[headings[ref_positions[0]][2]]
        if ref_positions[-1] + 1 < len(headings):
            end_rank = rank[headings[ref_positions[-1] + 1][2]]
        else:
            end_rank = len(lines)
        for index in list(keep):
            pos = rank[index]
            if first_ref_rank <= pos < end_rank and index not in live_heading_indexes:
                keep.discard(index)
    for index in ref_line_indexes:
        keep.discard(index)
        remainders.pop(index, None)
    headings = live_headings
    heading_indexes = live_heading_indexes
    outline = [h[1] for h in headings] if outline_source == "fonts" else outline
    restored = [h[1] for h in headings] if outline_source == "fonts" else restored
    # Order whole boxes column by column, then interleave headings.
    columns = _page_columns(lines)
    boxes: dict[tuple[int, int], list[int]] = {}
    for index in full_order:
        if index in keep and index not in heading_indexes:
            line = lines[index]
            boxes.setdefault((line.page, line.box), []).append(index)

    def _box_key(key: tuple[int, int]) -> tuple[int, int, float]:
        members = boxes[key]
        top = max(lines[i].y1 for i in members)
        left = min(lines[i].x0 for i in members)
        page = key[0]
        return (page, _column_of(columns.get(page, []), left), -top)

    ordered_boxes = sorted(boxes, key=_box_key)
    heading_text = {line_index: (level, text) for level, text, line_index in headings}

    def _heading_key(line_index: int) -> tuple[int, int, float]:
        line = lines[line_index]
        return (
            line.page,
            _column_of(columns.get(line.page, []), line.x0),
            -line.y1,
        )

    events: list[tuple[tuple[int, int, float], str, int]] = []
    for key in ordered_boxes:
        events.append((_box_key(key), "box", len(events)))
    box_text: dict[int, str] = {}
    for pos, key in enumerate(ordered_boxes):
        members = sorted(boxes[key], key=lambda i: -lines[i].y1)
        parts = [
            remainders.get(i, lines[i].text.strip())
            for i in members
            if remainders.get(i, lines[i].text.strip())
        ]
        box_text[pos] = _join_box_lines(parts)
    for line_index in heading_text:
        events.append((_heading_key(line_index), "heading", line_index))
    events.sort(key=lambda event: (event[0], event[1] == "box"))
    blocks: list[str] = []
    running: str | None = None
    running_col: int | None = None

    def _push_paragraph(text: str, column: int) -> None:
        nonlocal running, running_col
        if re.match(r"^(Figure|Table)\s+\d+\s*:", text):
            if running is not None:
                blocks.append(running)
                running = None
                running_col = None
            blocks.append(text)
            return
        if text[0] in "\u2022\u25e6\u25aa\u2023" or re.match(r"^-\s+", text):
            if running is not None:
                blocks.append(running)
                running = None
                running_col = None
            cleaned = re.sub(r"^[\u2022\u25e6\u25aa\u2023-]\s*", "", text).strip()
            if cleaned:
                blocks.append("- " + cleaned)
            return
        if running is not None and text[:1].islower():
            if running_col == column and running.endswith(("-", "\u00ad", "\ufffe")):
                running = running[:-1] + text
            elif running_col == column:
                running = running + " " + text
            else:
                blocks.append(running)
                running = text
                running_col = column
        else:
            if running is not None:
                blocks.append(running)
            running = text
            running_col = column

    for _key, kind, ref in events:
        if kind == "heading":
            if running is not None:
                blocks.append(running)
                running = None
                running_col = None
            level, text = heading_text[ref]
            blocks.append(f"{'##' if level == 1 else '###'} {text}")
            remainder = remainders.get(ref, "")
            if remainder:
                _push_paragraph(remainder, -1)
            continue
        text = box_text[ref]
        if text:
            _push_paragraph(text, _box_key(ordered_boxes[ref])[1])
    if running is not None:
        blocks.append(running)
        running = None
        running_col = None
    # Inline cleanup + title-line drop.
    cleaned_blocks: list[str] = []
    total_cid = 0
    for block in blocks:
        if block.startswith("##"):
            cleaned, count = _cleanup_text(block)
            total_cid += count
            cleaned_blocks.append(cleaned)
            continue
        cleaned, count = _cleanup_text(block)
        total_cid += count
        if _normalize_alnum(re.sub(r"^-\s+", "", cleaned)) == title_norm:
            continue
        cleaned_blocks.append(cleaned)
    # Drop a leading body line equal to the title.
    if (
        cleaned_blocks
        and not cleaned_blocks[0].startswith("#")
        and _normalize_alnum(cleaned_blocks[0]) == title_norm
    ):
        cleaned_blocks = cleaned_blocks[1:]
    body_md = "\n\n".join(b for b in cleaned_blocks if b).strip()
    words = len(body_md.split())
    if words < 150:
        raise SaseListenError(
            f"The extracted PDF text is too short ({words} words).",
            ExitCode.UNEXPECTED,
            hint=(
                "The PDF may be a scanned image without a text layer; "
                "OCR is not supported. Convert it to Markdown and render that file."
            ),
        )
    if words > 0 and total_cid > 0.02 * words:
        raise SaseListenError(
            "The extracted PDF text is too short "
            f"({words} words; {total_cid} undecodable tokens).",
            ExitCode.UNEXPECTED,
            hint="The PDF fonts could not be decoded; convert it to Markdown instead.",
        )
    authors = _split_authors(info.get("Author", ""))
    credit = _author_credit(authors)
    date = arxiv_date or _creation_date(info.get("CreationDate", ""))
    host = ""
    try:
        host = (urlsplit(source_url).hostname or "") if source_url else ""
    except ValueError:
        host = ""
    has_arxiv_id = any(key.lower() == "arxivid" for key in info_lower)
    site = (
        "arXiv"
        if (arxiv_text is not None or has_arxiv_id or host == "arxiv.org")
        else ""
    )
    canonical = normalize_url(source_url) if source_url else ""
    article = Article(
        markdown=body_md,
        title=title,
        author=credit,
        date=date,
        site=site,
        canonical_url=canonical,
        description="",
        words=words,
        outline=(
            list(outline) if outline_source == "bookmarks" else [h[1] for h in headings]
        ),
        restored_headings=list(restored),
        missing_headings=list(missing),
    )
    return PdfExtraction(
        article=article,
        pages=page_count,
        authors=authors,
        outline_source=outline_source,
        dropped_small_words=dropped_small_words,
    )
