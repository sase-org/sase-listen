"""Deterministic Markdown to narration-script normalizer. Owner: script phase."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from sase_listen.script.model import Chapter, NarrationScript, ScriptMeta
from sase_listen.script.parser import clean_residual_markdown

_ORPHAN_PARENS_RE = re.compile(r"\(\s*\)|\[\s*\]")
_DANGLING_COMMA_RE = re.compile(r",\s*([.,;:!?])")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_BARE_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_SECTION_SIGN_RE = re.compile(r"§\s*(\d+)")
_SASE_REF_RE = re.compile(r"[A-Za-z_]+:\d{4}(?:\d{2})?/[^\s\])]+")
_FILE_LINE_RE = re.compile(r"[\w.\-/]+\.\w+:\d+(?::\d+)?")
_SHA_RE = re.compile(r"\A[0-9a-fA-F]{7,64}\Z")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SINGLE_KEY_RE = re.compile(r"\A[a-zA-Z0-9]\Z")
_SOURCES_TITLES = ("sources", "references")
_LINK_DEFINITION_RE = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$")

_GENERIC_ALT = {"image", "figure", "photo", "screenshot", "diagram", "picture"}


@dataclass
class Omission:
    """One dropped piece of source, recorded for the omissions report."""

    type: str
    detail: str
    line: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable mapping."""
        return {"type": self.type, "detail": self.detail, "line": self.line}


def humanize_filename(path: str) -> str:
    """Turn a file stem into a title (my_notes -> My Notes)."""
    stem = Path(path).stem
    words = re.split(r"[_\-]+", stem)
    words = [w for w in words if w]
    if not words:
        return "Untitled"
    return " ".join(w[:1].upper() + w[1:] for w in words)


def humanize_identifier(code: str) -> str:
    """Turn an identifier into spoken words (snake_case, CamelCase)."""
    text = code.strip().strip(".,;:!?()[]{}'\"")
    text = text.replace("_", " ").replace("-", " ")
    text = _CAMEL_BOUNDARY_RE.sub(" ", text)
    text = _MULTI_SPACE_RE.sub(" ", text).strip()
    return text


_NUMBERISH_RE = re.compile(r"\A[\d][\d.,%\-+]*\Z")


def spoken_code(code: str) -> str:
    """Convert an inline-code span to spoken text (empty means drop)."""
    text = code.strip()
    if not text:
        return ""
    if _BARE_URL_RE.fullmatch(text) or text.startswith(("http://", "https://")):
        return ""
    if _SASE_REF_RE.fullmatch(text):
        return ""
    if _FILE_LINE_RE.fullmatch(text):
        return ""
    if _SHA_RE.match(text.replace(" ", "")) and re.search(r"[a-fA-F]", text):
        return ""
    if "/" in text and re.search(r"[\w.\-]/[\w.\-]", text):
        return ""
    if _SINGLE_KEY_RE.fullmatch(text):
        return f"the {text} key"
    if _NUMBERISH_RE.match(text):
        # Versions and numbers stay exact for fidelity.
        return text
    # Calls and attribute paths become word sequences: cache.get(key) talks.
    text = re.sub(r"[()\[\]{}'\"`]", " ", text)
    text = text.replace(".", " ")
    return humanize_identifier(text)


def _clean_text(text: str) -> str:
    out = _SECTION_SIGN_RE.sub(r"section \1", text)
    out = _BARE_URL_RE.sub("", out)
    out = _ORPHAN_PARENS_RE.sub("", out)
    out = _DANGLING_COMMA_RE.sub(r"\1", out)
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = _MULTI_SPACE_RE.sub(" ", out)
    return out.strip()


def _inline_children_text(
    children: list[Token], omissions: list[Omission], lineno: int
) -> str:
    parts: list[str] = []
    i = 0
    while i < len(children):
        child = children[i]
        if child.type == "text":
            parts.append(child.content)
        elif child.type == "code_inline":
            spoken = spoken_code(child.content)
            if spoken:
                parts.append(spoken)
        elif child.type == "link_open":
            # Collect anchor text until link_close.
            anchor: list[str] = []
            i += 1
            while i < len(children) and children[i].type != "link_close":
                inner = children[i]
                if inner.type == "text":
                    anchor.append(inner.content)
                elif inner.type == "code_inline":
                    spoken = spoken_code(inner.content)
                    if spoken:
                        anchor.append(spoken)
                elif inner.type == "image":
                    alt = (inner.content or "").strip()
                    if alt:
                        anchor.append(alt)
                i += 1
            parts.append("".join(anchor))
        elif child.type == "image":
            alt = (child.content or "").strip()
            if len(alt) >= 3 and alt.lower() not in _GENERIC_ALT:
                parts.append(f"Figure: {alt}.")
        elif child.type in ("math_inline", "math_block"):
            omissions.append(Omission("math", child.content.strip()[:80], lineno))
        elif child.type == "footnote_ref":
            omissions.append(Omission("footnote", child.markup or "footnote", lineno))
        elif child.type in ("html_inline", "html_block"):
            # Inline tags vanish silently; comments are recorded at block level.
            parts.append(" ")
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        i += 1
    # Newlines inside a paragraph are speech spaces; collapse them now.
    return re.sub(r"\s*\n\s*", " ", "".join(parts))


def _make_parser() -> MarkdownIt:
    md = (
        MarkdownIt()
        .use(front_matter_plugin)
        .use(footnote_plugin)
        .use(tasklists_plugin)
        .use(dollarmath_plugin)
    )
    md.enable("table")
    return md


def _cell_text(token: Token, omissions: list[Omission], lineno: int) -> str:
    raw = _inline_children_text(token.children or [], omissions, lineno)
    cleaned = clean_residual_markdown(raw).text
    return _clean_text(cleaned)


def normalize_markdown(
    source_text: str, filename: str = "notes.md"
) -> tuple[str, list[Omission]]:
    """Normalize Markdown to a lint-clean verbatim narration script.

    Returns (script_markdown, omissions).
    """
    text = source_text.replace("\r\n", "\n")
    md = _make_parser()
    tokens = md.parse(text)

    def _line_of(token: Token | None) -> int:
        if token is not None and token.map:
            return int(token.map[0]) + 1
        return 1

    front_title = ""
    for token in tokens:
        if token.type == "front_matter":
            try:
                loaded = yaml.safe_load(token.content) or {}
            except yaml.YAMLError:
                loaded = {}
            if isinstance(loaded, dict) and str(loaded.get("title", "")).strip():
                front_title = str(loaded["title"]).strip()
            break

    first_h1 = ""
    for idx in range(len(tokens) - 1):
        token = tokens[idx]
        if token.type == "heading_open" and token.tag == "h1":
            nxt = tokens[idx + 1]
            if nxt.type == "inline":
                first_h1 = _cell_text(nxt, [], _line_of(token))
                break
    title = front_title or first_h1 or humanize_filename(filename)

    footnote_def_lines = [
        i + 1
        for i, line in enumerate(text.split("\n"))
        if re.match(r"\s*\[\^[^\]]+\]:", line)
    ]
    footnote_defs_used = 0

    chapters: list[Chapter] = []
    omissions: list[Omission] = []
    current: Chapter | None = None
    first_h1_consumed = False

    def _ensure_chapter() -> Chapter:
        nonlocal current
        if current is None:
            current = Chapter(title=title, line=1)
            chapters.append(current)
        return current

    def _finish_paragraph(raw: str, lineno: int) -> None:
        if _LINK_DEFINITION_RE.match(raw):
            return
        cleaned = clean_residual_markdown(raw).text
        spoken = _clean_text(cleaned)
        if spoken:
            _ensure_chapter().paragraphs.append(spoken)

    idx = 0
    skip_until_h2 = False
    while idx < len(tokens):
        token = tokens[idx]
        ttype = token.type
        if ttype == "front_matter":
            idx += 1
            continue
        if ttype.startswith("footnote"):
            if ttype == "footnote_open":
                if footnote_defs_used < len(footnote_def_lines):
                    def_line = footnote_def_lines[footnote_defs_used]
                    footnote_defs_used += 1
                else:
                    def_line = _line_of(token)
                omissions.append(Omission("footnote", "footnote definition", def_line))
            idx += 1
            continue
        if "math" in ttype and ttype not in ("inline",):
            if ttype in ("math_block", "math_inline"):
                omissions.append(
                    Omission(
                        "math", (token.content or "").strip()[:80], _line_of(token)
                    )
                )
            idx += 1
            continue
        if ttype == "heading_open":
            level = token.tag
            inline = tokens[idx + 1] if idx + 1 < len(tokens) else None
            heading_text = ""
            if inline is not None and inline.type == "inline":
                heading_text = _cell_text(inline, omissions, _line_of(token))
            lineno = _line_of(token)
            if level == "h1":
                skip_until_h2 = False
                if (
                    not first_h1_consumed
                    and heading_text == first_h1
                    and not front_title
                ):
                    first_h1_consumed = True
                    # Title heading is consumed, not spoken.
                else:
                    current = Chapter(title=heading_text or title, line=lineno)
                    chapters.append(current)
                idx += 3
                continue
            if level == "h2":
                low = heading_text.strip().lower()
                if low in _SOURCES_TITLES or low.startswith(
                    ("sources ", "references ")
                ):
                    omissions.append(Omission("sources_section", heading_text, lineno))
                    skip_until_h2 = True
                    idx += 3
                    continue
                skip_until_h2 = False
                current = Chapter(title=heading_text, line=lineno)
                chapters.append(current)
                idx += 3
                continue
            # H3-H6 become a spoken sentence plus a paragraph break.
            if skip_until_h2:
                idx += 3
                continue
            sentence = heading_text.strip()
            if sentence and sentence[-1] not in ".!?":
                sentence += "."
            if sentence:
                _ensure_chapter().paragraphs.append(_clean_text(sentence))
            idx += 3
            continue
        if skip_until_h2:
            idx += 1
            continue
        if ttype in ("paragraph_open",):
            inline = tokens[idx + 1] if idx + 1 < len(tokens) else None
            if inline is not None and inline.type == "inline":
                raw = _inline_children_text(
                    inline.children or [], omissions, _line_of(token)
                )
                if raw.strip():
                    # Split tasklist residual checkboxes defensively.
                    raw = re.sub(r"^\s*\[[ xX]\]\s*", "", raw)
                    _finish_paragraph(raw, _line_of(token))
            idx += 3
            continue
        if ttype in ("bullet_list_open", "ordered_list_open"):
            idx += 1
            while idx < len(tokens) and tokens[idx].type not in (
                "bullet_list_close",
                "ordered_list_close",
            ):
                if tokens[idx].type == "list_item_open":
                    idx += 1
                    item_parts: list[str] = []
                    while idx < len(tokens) and tokens[idx].type != "list_item_close":
                        if tokens[idx].type == "paragraph_open":
                            inner = tokens[idx + 1] if idx + 1 < len(tokens) else None
                            if inner is not None and inner.type == "inline":
                                item_parts.append(
                                    _inline_children_text(
                                        inner.children or [],
                                        omissions,
                                        _line_of(tokens[idx]),
                                    )
                                )
                            idx += 3
                        elif tokens[idx].type == "inline":
                            item_parts.append(
                                _inline_children_text(
                                    tokens[idx].children or [],
                                    omissions,
                                    _line_of(tokens[idx]),
                                )
                            )
                            idx += 1
                        else:
                            idx += 1
                    sentence = _clean_text(
                        clean_residual_markdown(" ".join(item_parts)).text
                    )
                    sentence = re.sub(r"^\s*\[[ xX]\]\s*", "", sentence)
                    if sentence:
                        if sentence[-1] not in ".!?":
                            sentence += "."
                        _ensure_chapter().paragraphs.append(sentence)
                else:
                    idx += 1
            idx += 1
            continue
        if ttype == "fence":
            info = (token.info or "").strip().split()[0] if token.info else ""
            if info.lower() == "mermaid":
                omissions.append(
                    Omission(
                        "mermaid",
                        (token.content or "").split("\n")[0][:80]
                        if token.content
                        else "diagram",
                        _line_of(token),
                    )
                )
            else:
                detail = f"{info} block" if info else "code block"
                if token.content:
                    first = token.content.strip().split("\n")[0][:60]
                    if first:
                        detail = f"{detail}: {first}"
                omissions.append(Omission("fence", detail, _line_of(token)))
            idx += 1
            continue
        if ttype == "code_block":
            detail = "indented code block"
            if token.content:
                first = token.content.strip().split("\n")[0][:60]
                if first:
                    detail = f"{detail}: {first}"
            omissions.append(Omission("fence", detail, _line_of(token)))
            idx += 1
            continue
        if ttype == "table_open":
            headers: list[str] = []
            rows: list[list[str]] = []
            j = idx + 1
            while j < len(tokens) and tokens[j].type != "table_close":
                if tokens[j].type in ("th_open", "td_open"):
                    is_header = tokens[j].type == "th_open"
                    inline = tokens[j + 1] if j + 1 < len(tokens) else None
                    cell = ""
                    if inline is not None and inline.type == "inline":
                        cell = _cell_text(inline, omissions, _line_of(tokens[j]))
                    if is_header:
                        headers.append(cell)
                    elif rows:
                        rows[-1].append(cell)
                    else:
                        rows.append([cell])
                    j += 3
                elif tokens[j].type == "tr_open":
                    # Start a new row unless the next cells belong to header.
                    rows.append([])
                    j += 1
                elif tokens[j].type in ("tr_close",):
                    # Drop empty trailing rows.
                    if rows and not rows[-1]:
                        rows.pop()
                    j += 1
                else:
                    j += 1
            rows = [r for r in rows if r]
            # Header row was collected into `rows` too via tr_open; fix up.
            data_rows = rows[1:] if rows and headers and rows[0] == headers else rows
            if not data_rows and rows and not headers:
                data_rows = rows
            n_cols = max([len(headers)] + [len(r) for r in data_rows] + [0])
            # Remove header duplicated as first data row.
            if data_rows and headers and data_rows[0] == headers:
                data_rows = data_rows[1:]
            lineno = _line_of(token)
            if data_rows and len(data_rows) <= 6 and n_cols <= 4:
                for row in data_rows:
                    label = row[0] if row else ""
                    rest: list[str] = []
                    for k, cell in enumerate(row[1:], start=1):
                        header = headers[k] if k < len(headers) else ""
                        if header and cell:
                            rest.append(f"{header} {cell}")
                        elif cell:
                            rest.append(cell)
                    if label and rest:
                        sentence = f"{label}: {', '.join(rest)}."
                    elif label:
                        sentence = label if label[-1] in ".!?" else label + "."
                    elif rest:
                        sentence = ", ".join(rest) + "."
                    else:
                        continue
                    _ensure_chapter().paragraphs.append(_clean_text(sentence))
            elif data_rows:
                cols = ", ".join(h for h in headers if h) or f"{n_cols} columns"
                sentence = (
                    f"The table with columns {cols} has {len(data_rows)} rows; "
                    "see the written report for details."
                )
                _ensure_chapter().paragraphs.append(sentence)
                omissions.append(
                    Omission(
                        "large_table",
                        f"{len(data_rows)} rows x {n_cols} columns",
                        lineno,
                    )
                )
            else:
                omissions.append(Omission("fence", "empty table", lineno))
            idx = j + 1
            continue
        if ttype == "blockquote_open":
            idx += 1
            continue
        if ttype in ("blockquote_close", "paragraph_close", "heading_close"):
            idx += 1
            continue
        if ttype == "hr":
            idx += 1
            continue
        if ttype == "html_block":
            content = token.content or ""
            if "<!--" in content:
                omissions.append(
                    Omission("html_comment", content.strip()[:80], _line_of(token))
                )
            idx += 1
            continue
        if ttype == "inline":
            raw = _inline_children_text(
                token.children or [], omissions, _line_of(token)
            )
            if raw.strip():
                _finish_paragraph(raw, _line_of(token))
            idx += 1
            continue
        idx += 1

    if not chapters:
        _ensure_chapter()
    # Drop chapters that ended up empty (e.g. sources-only sections leave none).
    chapters = [c for c in chapters if c.paragraphs]
    if not chapters:
        chapters = [
            Chapter(title=title, line=1, paragraphs=["No spoken content found."])
        ]

    meta = ScriptMeta(
        narration=1,
        title=title,
        kind="document",
        edition="verbatim",
        producer="deterministic",
    )
    script = NarrationScript(meta=meta, chapters=chapters)
    return script.dumps(), omissions


def normalize_file(source: Path) -> tuple[str, list[Omission]]:
    """Read a Markdown file and normalize it."""
    return normalize_markdown(source.read_text(encoding="utf-8"), filename=source.name)
