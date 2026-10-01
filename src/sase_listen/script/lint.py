"""Lint rules for narration scripts. Owner: script phase."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from sase_listen.script.model import (
    EDITION_BUDGETS,
    VALID_EDITIONS,
    VALID_KINDS,
    VALID_PRODUCERS,
)

Severity = Literal["error", "warning"]


@dataclass
class Finding:
    """One lint finding with a stable rule id and a fix hint."""

    rule: str
    severity: Severity
    line: int
    col: int
    message: str
    hint: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable mapping."""
        return {
            "rule": self.rule,
            "severity": self.severity,
            "line": self.line,
            "col": self.col,
            "message": self.message,
            "hint": self.hint,
        }


def is_structural(finding: Finding) -> bool:
    """Return True for structural errors (render refuses on these)."""
    return finding.rule.startswith("S")


def is_residue(finding: Finding) -> bool:
    """Return True for residue errors (render cleans and warns)."""
    return finding.rule.startswith("R")


_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)?\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]\s+\S|\d+[.)]\s+\S|\[.\]\s+\S)")
_FENCE_RE = re.compile(r"^\s*```")
_LINK_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)|!?\[[^\]]*\]\[[^\]]*\]")
_AUTOLINK_RE = re.compile(r"<(https?://[^>]+|[^@\s>]+@[^>\s]+)>")
_HTML_RE = re.compile(r"<[A-Za-z/!][^>]*>")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_PATH_RE = re.compile(
    r"(?:(?<![\w.\-/])~?/(?:[\w.\-]+/)*[\w.\-]+|\./[\w.\-/]+"
    r"|[\w.\-]+\/[\w.\-/]*\.[\w]+"
    r"|[\w.\-]+\/[\w.\-]+\/[\w.\-/]*)"
)
_FILE_LINE_RE = re.compile(r"[\w.\-/]+\.\w+:\d+(?::\d+)?|[\w.\-/]+:\d+:\d*")
_SHA_RE = re.compile(r"\b[0-9a-fA-F]{7,64}\b")
_REF_RE = re.compile(r"[A-Za-z_]+:\d{4}(?:\d{2})?/[^\s\])]+")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?|\d+\.\d+%?")
_SYMBOL_RES = ("→", "≈", "×", "≥", "≤", "±")  # noqa: RUF001 - spoken symbols under test
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _count_words(text: str) -> int:
    return len(text.split())


def _split_frontmatter(lines: list[str]) -> tuple[int, dict[str, object], str | None]:
    """Return (body_start_1based, mapping, yaml_error)."""
    text = "\n".join(lines)
    match = _FRONTMATTER_RE.match(text + "\n")
    if not match:
        return 1, {}, "missing"
    raw = match.group(1)
    try:
        loaded = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        return 1, {}, str(exc)
    if not isinstance(loaded, dict):
        return 1, {}, "not-a-mapping"
    # Body starts after the closing --- line.
    consumed = match.group(0).count("\n")
    return consumed + 1, loaded, None


def _edition_line(lines: list[str]) -> int:
    for i, line in enumerate(lines, start=1):
        if re.match(r"^\s*edition\s*:", line):
            return i
    return 1


def _normalize_number(candidate: str) -> str:
    return candidate.replace(",", "").replace("_", "")


def _script_numbers(text: str) -> list[str]:
    out: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        cand = match.group(0)
        digits = re.sub(r"\D", "", cand)
        if len(digits) >= 2 or "." in cand or "%" in cand:
            out.append(cand)
    return out


def lint_text(raw_text: str, source_text: str | None = None) -> list[Finding]:
    """Lint narration-script Markdown, optionally checking number fidelity."""
    findings: list[Finding] = []
    text = raw_text.replace("\r\n", "\n")
    lines = text.split("\n")
    body_start, meta, yaml_error = _split_frontmatter(lines)
    if yaml_error == "missing":
        findings.append(
            Finding(
                "S001",
                "error",
                1,
                1,
                "Missing frontmatter block.",
                "Add a --- YAML block with narration: 1 and title: ...",
            )
        )
        meta = {}
        body_start = 1
    elif yaml_error not in (None,):
        findings.append(
            Finding(
                "S001",
                "error",
                1,
                1,
                f"Invalid frontmatter YAML: {yaml_error}.",
                "Fix the YAML indentation and quoting.",
            )
        )
        meta = {}
    else:
        narration = meta.get("narration")
        if narration != 1:
            findings.append(
                Finding(
                    "S002",
                    "error",
                    2,
                    1,
                    "Frontmatter must set 'narration: 1'.",
                    "Set narration: 1 as the schema marker.",
                )
            )
        title = str(meta.get("title", "") or "")
        if not title.strip():
            findings.append(
                Finding(
                    "S003",
                    "error",
                    2,
                    1,
                    "Frontmatter must set a non-empty 'title'.",
                    "Add title: <episode title>.",
                )
            )
        kind = str(meta.get("kind", "document") or "document")
        if kind not in VALID_KINDS:
            findings.append(
                Finding(
                    "S001",
                    "error",
                    2,
                    1,
                    f"Unknown kind '{kind}'.",
                    f"Use one of: {', '.join(VALID_KINDS)}.",
                )
            )
        edition = str(meta.get("edition", "verbatim") or "verbatim")
        if edition not in VALID_EDITIONS:
            findings.append(
                Finding(
                    "S001",
                    "error",
                    _edition_line(lines),
                    1,
                    f"Unknown edition '{edition}'.",
                    f"Use one of: {', '.join(VALID_EDITIONS)}.",
                )
            )
        producer = str(meta.get("producer", "deterministic") or "deterministic")
        if producer not in VALID_PRODUCERS:
            findings.append(
                Finding(
                    "S001",
                    "error",
                    2,
                    1,
                    f"Unknown producer '{producer}'.",
                    f"Use one of: {', '.join(VALID_PRODUCERS)}.",
                )
            )

    body_lines = lines[body_start - 1 :]
    # Structural scan of the body.
    chapter_headings: list[tuple[int, str]] = []
    chapter_paras: dict[int, list[tuple[int, str]]] = {}
    current_idx: int | None = None
    first_chapter_seen = False
    para_buf: list[tuple[int, str]] = []
    in_fence_body = False

    def _flush_para() -> None:
        if not para_buf:
            return
        if current_idx is not None:
            chapter_paras.setdefault(current_idx, []).extend(list(para_buf))
        para_buf.clear()

    for offset, line in enumerate(body_lines):
        lineno = body_start + offset
        stripped = line.strip()
        if _FENCE_RE.match(line):
            in_fence_body = not in_fence_body
            continue
        if in_fence_body:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            hashes = heading.group(1)
            _flush_para()
            if hashes == "##":
                current_idx = len(chapter_headings)
                chapter_headings.append((lineno, (heading.group(2) or "").strip()))
                chapter_paras.setdefault(current_idx, [])
                first_chapter_seen = True
            else:
                findings.append(
                    Finding(
                        "S006",
                        "error",
                        lineno,
                        1,
                        f"Only '##' headings are allowed; found {hashes}.",
                        "Rewrite as a '##' chapter or as a spoken sentence.",
                    )
                )
                if current_idx is not None:
                    para_buf.append((lineno, line))
                # Text outside chapters handled below via S007 when needed.
            continue
        if not stripped:
            _flush_para()
            continue
        if not first_chapter_seen:
            findings.append(
                Finding(
                    "S007",
                    "error",
                    lineno,
                    1,
                    "Text before the first '##' chapter.",
                    "Move it into a '##' chapter or delete it.",
                )
            )
        para_buf.append((lineno, line))
    _flush_para()

    if not chapter_headings:
        findings.append(
            Finding(
                "S004",
                "error",
                body_start,
                1,
                "No '##' chapters found.",
                "Add at least one '## Chapter title' section.",
            )
        )
    for idx, (hlineno, _title) in enumerate(chapter_headings):
        if not chapter_paras.get(idx):
            findings.append(
                Finding(
                    "S005",
                    "error",
                    hlineno,
                    1,
                    "Empty chapter (no paragraphs).",
                    "Add spoken paragraphs or remove the heading.",
                )
            )

    # Residue + warning scan, line by line over body (outside fences).
    in_fence_scan = False
    for offset, line in enumerate(body_lines):
        lineno = body_start + offset
        if _FENCE_RE.match(line):
            findings.append(
                Finding(
                    "R003",
                    "error",
                    lineno,
                    line.find("`") + 1,
                    "Fenced code block.",
                    "Replace the fence with one spoken sentence or nothing.",
                )
            )
            in_fence_scan = not in_fence_scan
            continue
        if in_fence_scan:
            continue
        if not line.strip():
            continue
        heading = _HEADING_RE.match(line)
        check_text = line
        if heading and heading.group(1) == "##":
            check_text = heading.group(2) or ""
            col_base = line.find("##") + 4
        else:
            col_base = 1
        # Residue errors.
        if not heading and _LIST_RE.match(line):
            findings.append(
                Finding(
                    "R001",
                    "error",
                    lineno,
                    1,
                    "List marker.",
                    "Rewrite items as separate spoken sentences.",
                )
            )
        if "|" in line and not heading:
            findings.append(
                Finding(
                    "R002",
                    "error",
                    lineno,
                    line.find("|") + 1,
                    "Table syntax.",
                    "Say small tables row by row; summarize large ones.",
                )
            )
        if "`" in line:
            findings.append(
                Finding(
                    "R004",
                    "error",
                    lineno,
                    line.find("`") + 1,
                    "Backtick code span.",
                    "Say code as one sentence of purpose, or drop it.",
                )
            )
        link_m = _LINK_RE.search(line)
        if link_m:
            findings.append(
                Finding(
                    "R005",
                    "error",
                    lineno,
                    link_m.start() + 1,
                    "Link or image syntax.",
                    "Keep anchor text; drop URLs and paths.",
                )
            )
        html_m = _HTML_RE.search(line)
        if html_m and not _AUTOLINK_RE.search(line):
            findings.append(
                Finding(
                    "R006",
                    "error",
                    lineno,
                    html_m.start() + 1,
                    "HTML tag.",
                    "Remove the tag and say the words.",
                )
            )
        if (
            "**" in line
            or "__" in line
            or "~~" in line
            or re.search(r"(?<!\w)\*[^*\n]+\*(?!\w)", line)
            or re.search(r"(?<!\w)_[^_\n]+_(?!\w)", line)
        ):
            findings.append(
                Finding(
                    "R007",
                    "error",
                    lineno,
                    col_base,
                    "Emphasis markup.",
                    "Remove * _ ~ markers and say the words.",
                )
            )
        # Warnings.
        url_m = _URL_RE.search(line)
        if url_m:
            findings.append(
                Finding(
                    "W001",
                    "warning",
                    lineno,
                    url_m.start() + 1,
                    "URL in spoken text.",
                    "Never say URLs; drop them.",
                )
            )
        fl_m = _FILE_LINE_RE.search(line)
        if fl_m:
            findings.append(
                Finding(
                    "W003",
                    "warning",
                    lineno,
                    fl_m.start() + 1,
                    "file:line citation.",
                    "Never say paths or line numbers; drop them.",
                )
            )
        else:
            path_m = _PATH_RE.search(line)
            if path_m and "://" not in line[max(0, path_m.start() - 6) : path_m.end()]:
                findings.append(
                    Finding(
                        "W002",
                        "warning",
                        lineno,
                        path_m.start() + 1,
                        "Path in spoken text.",
                        "Never say paths; drop them.",
                    )
                )
        sha_m = _SHA_RE.search(line)
        if sha_m and re.search(r"[a-fA-F]", sha_m.group(0)):
            findings.append(
                Finding(
                    "W004",
                    "warning",
                    lineno,
                    sha_m.start() + 1,
                    "Hex SHA in spoken text.",
                    "Never say SHAs; drop them.",
                )
            )
        ref_m = _REF_RE.search(line)
        if ref_m:
            findings.append(
                Finding(
                    "W005",
                    "warning",
                    lineno,
                    ref_m.start() + 1,
                    "SASE ref in spoken text.",
                    "Never say refs; drop them.",
                )
            )
        if "§" in line:
            findings.append(
                Finding(
                    "W006",
                    "warning",
                    lineno,
                    line.find("§") + 1,
                    "Section sign.",
                    "Write 'section N' as words.",
                )
            )
        for sym in _SYMBOL_RES:
            if sym in check_text:
                findings.append(
                    Finding(
                        "W007",
                        "warning",
                        lineno,
                        check_text.find(sym) + col_base,
                        f"Symbol '{sym}' in spoken text.",
                        "Write symbols as words.",
                    )
                )
                break

    # Word-count warnings.
    edition = str(meta.get("edition", "verbatim") or "verbatim")
    total_words = 0
    for idx, (hlineno, htitle) in enumerate(chapter_headings):
        paras = chapter_paras.get(idx, [])
        chap_words = _count_words(htitle)
        for _, pline in paras:
            chap_words += _count_words(pline)
        total_words += chap_words
        if chap_words > 900:
            findings.append(
                Finding(
                    "W008",
                    "warning",
                    hlineno,
                    1,
                    f"Chapter has {chap_words} words (> 900).",
                    "Split the chapter.",
                )
            )
    for paras in chapter_paras.values():
        # Group contiguous lines into paragraphs for W009/W010.
        groups: list[list[tuple[int, str]]] = []
        for plineno, pline in paras:
            if groups and plineno == groups[-1][-1][0] + 1:
                groups[-1].append((plineno, pline))
            else:
                groups.append([(plineno, pline)])
        for group in groups:
            joined = " ".join(t for _, t in group)
            start = group[0][0]
            words = _count_words(joined)
            if words > 180:
                findings.append(
                    Finding(
                        "W009",
                        "warning",
                        start,
                        1,
                        f"Paragraph has {words} words (> 180).",
                        "Split into shorter paragraphs.",
                    )
                )
            for sent in _SENTENCE_SPLIT_RE.split(joined):
                if _count_words(sent) > 45:
                    findings.append(
                        Finding(
                            "W010",
                            "warning",
                            start,
                            1,
                            "Sentence has more than 45 words.",
                            "Split into short sentences.",
                        )
                    )
                    break

    budget = EDITION_BUDGETS.get(edition)
    if budget is not None and chapter_headings:
        if total_words > int(budget * 1.15):
            findings.append(
                Finding(
                    "W011",
                    "warning",
                    _edition_line(lines),
                    1,
                    f"Edition '{edition}' has {total_words} words "
                    f"(budget {budget}; over 115%).",
                    "Cut words to fit the edition budget.",
                )
            )
        elif total_words < int(budget * 0.5) and total_words > 0:
            findings.append(
                Finding(
                    "W012",
                    "warning",
                    _edition_line(lines),
                    1,
                    f"Edition '{edition}' has {total_words} words "
                    f"(budget {budget}; under 50%).",
                    "Expand the script or pick a smaller edition.",
                )
            )

    # Number fidelity against the source report.
    if source_text is not None:
        normalized_source = source_text.replace(",", "").replace("_", "")
        for offset, line in enumerate(body_lines):
            lineno = body_start + offset
            if _FENCE_RE.match(line) or not line.strip():
                continue
            for match in _NUMBER_RE.finditer(line):
                cand = match.group(0)
                digits = re.sub(r"\D", "", cand)
                if not (len(digits) >= 2 or "." in cand or "%" in cand):
                    continue
                # Skip heading markers already excluded; check membership.
                norm = _normalize_number(cand)
                if norm and norm not in normalized_source:
                    findings.append(
                        Finding(
                            "W013",
                            "warning",
                            lineno,
                            match.start() + 1,
                            f"Number '{cand}' not found in the source.",
                            "Keep every argument-carrying number exactly.",
                        )
                    )
    findings.sort(key=lambda f: (f.line, f.col, f.rule))
    return findings


def lint_file(script_path: Path, source_path: Path | None) -> tuple[list[Finding], str]:
    """Read files and lint; return (findings, script_text)."""
    script_text = script_path.read_text(encoding="utf-8")
    source_text: str | None = None
    if source_path is not None:
        source_text = source_path.read_text(encoding="utf-8")
    return lint_text(script_text, source_text), script_text
