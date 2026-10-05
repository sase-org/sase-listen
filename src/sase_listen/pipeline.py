"""Render orchestration: source loading, planning, synthesis, gates, commit.

Owner: pipeline phase.

:func:`render` takes a narration script, plain Markdown, or a ``kind:path``
artifact ref and produces a chaptered MP3 episode plus its manifest in the
library. Chunk synthesis runs in a thread pool sized to the engine
concurrency; each result is written to the chunk cache immediately so a
killed render resumes paying only for missing chunks. The cli phase renders
progress from :class:`RenderEvents` without touching this orchestration.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from sase_listen import __version__
from sase_listen.audio import (
    ChapterAudio,
    ChapterMark,
    EpisodeMeta,
    assemble,
    compress_silence,
    master_to_mp3,
    resample,
    resolve_cover,
    resolve_ffmpeg,
    trim_silence,
    write_tags,
)
from sase_listen.cache import ChunkCache, cache_key
from sase_listen.config import (
    DEFAULT_INTRO_TEMPLATE,
    SaseListenConfig,
    load_config,
)
from sase_listen.engines import (
    CredentialsError,
    Engine,
    PermanentEngineError,
    ResolvedNarrator,
    SynthesisRequest,
    TransientEngineError,
    create_engine,
    resolve_api_key,
    resolve_narrator,
    synthesize_with_retry,
)
from sase_listen.engines.tone import ToneEngine
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.feed import mark_manifest_published
from sase_listen.feedhost import PENDING_HINT, feed_role, publish_any, queue_publish
from sase_listen.lexicon import Lexicon, load_merged
from sase_listen.library import (
    atomic_commit,
    compute_episode_id,
    episode_lock,
    episode_mp3_name,
    slugify,
    staging_path,
)
from sase_listen.manifest import build_manifest, dumps_manifest
from sase_listen.normalize import Omission, normalize_markdown
from sase_listen.paths import cache_dir
from sase_listen.pricing import estimate
from sase_listen.script import (
    NarrationScript,
    ScriptMeta,
    clean_residual_markdown,
    is_residue,
    is_structural,
    lint_text,
    parse_script_text,
)
from sase_listen.web.editions import (
    article_coverage_sentence,
    article_display_title,
)
from sase_listen.web.extract import normalize_url
from sase_listen.web.store import AcquiredSource, acquire, save_verbatim_script

#: Words per minute assumed for estimates and gate selection.
TARGET_WPM = 150.0
#: Hard gate bounds: outside 60-300 wpm re-synthesizes twice, then exit 5.
HARD_WPM_MIN = 60.0
HARD_WPM_MAX = 300.0
#: Soft gate bounds: outside 90-240 re-synthesizes once, then warns.
SOFT_WPM_MIN = 90.0
SOFT_WPM_MAX = 240.0
#: Soft median-relative bounds, applied with at least 3 chunks.
MEDIAN_LOW = 0.65
MEDIAN_HIGH = 1.5
#: Hard re-syntheses before exit 5; soft gets a single re-synthesis.
HARD_RESYNTH_LIMIT = 2
SIZE_WARN_BYTES = 45 * 1024 * 1024
#: Weekday-free spoken date, e.g. "September 14, 2026".
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_DEFAULT_VERBATIM_INTRO = (
    "This is an AI-narrated reading of {title}{kind_phrase}{date_phrase}."
)
_REF_RE = re.compile(r"^[A-Za-z_][\w+.\-]*:.+")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass
class RenderRequest:
    """One render invocation, mirroring the CLI flags."""

    source: str
    output: str = ""
    narrator: str = ""
    voice_override: str = ""
    cover: str = ""
    dry_run: bool = False
    #: Tri-state: True from --publish, False from --no-publish, None when
    #: neither flag is given (auto_publish then decides for research kinds).
    publish: bool | None = None
    no_cache: bool = False
    force: bool = False
    edition: str | None = None
    html: str = ""
    refresh: bool = False
    generated_cover: bool = False


@dataclass
class PlannedChunk:
    """One synthesis unit with its content-addressed cache key."""

    index: int
    chapter_index: int
    chapter: str
    kind: str  # "intro" | "content" | "outro"
    text: str  # post-lexicon spoken text
    words: int
    cache_key: str


@dataclass
class ChapterPlan:
    """One script chapter's share of the plan."""

    title: str
    words: int
    chunks: int


@dataclass
class RenderPlan:
    """The dry-run plan: what a render would synthesize and master."""

    title: str
    episode_id: str
    source_label: str
    narrator: ResolvedNarrator
    edition: str
    producer: str
    words: int
    chunks: list[PlannedChunk] = field(default_factory=list)
    chapter_plans: list[ChapterPlan] = field(default_factory=list)
    omissions: list[Omission] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    estimated_duration_s: float = 0.0
    estimated_cost_usd: float = 0.0
    cached_chunks: int = 0
    script_path: str = ""
    writer: dict[str, Any] = field(default_factory=dict)

    @property
    def synthesis_needed(self) -> int:
        """Chunks that still need synthesis (cache misses)."""
        return len(self.chunks) - self.cached_chunks


@dataclass
class RenderResult:
    """A completed render, mirroring the `--json` success schema."""

    episode_id: str
    title: str
    audio_path: str
    manifest_path: str
    duration_s: float
    size_bytes: int
    chapters: list[dict[str, Any]] = field(default_factory=list)
    narrator_name: str = ""
    narrator_engine: str = ""
    narrator_model: str = ""
    narrator_voice: str = ""
    total_chunks: int = 0
    cached_chunks: int = 0
    synthesized_chunks: int = 0
    retried_chunks: int = 0
    loudness_lufs: float = 0.0
    cost_usd_estimate: float = 0.0
    published: bool = False
    publish_queued: bool = False
    publish_host: str = ""
    script_path: str = ""
    writer: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class RenderEvents:
    """Progress callbacks; the cli phase overrides these for live display."""

    def on_plan(self, plan: RenderPlan) -> None:
        """Called once chunk planning (and cache lookup) finishes."""

    def on_chunk_started(self, index: int, total: int) -> None:
        """Called when synthesis of chunk `index` starts."""

    def on_chunk_finished(self, index: int, total: int, *, cached: bool) -> None:
        """Called when chunk `index` has audio (cached or synthesized)."""

    def on_chunk_retried(self, index: int, attempt: int, reason: str) -> None:
        """Called before each retry or gate re-synthesis of chunk `index`."""

    def on_stage(self, stage: str) -> None:
        """Called on stage changes: synthesize, gates, master, tag, commit."""

    def on_done(self, result: RenderResult) -> None:
        """Called with the finished result after the commit."""


@dataclass
class LoadedSource:
    """A source resolved to an exact script plus provenance."""

    script_text: str
    script: NarrationScript
    omissions: list[Omission]
    source_label: str  # ref string or absolute path, for display and manifest
    source_key: str  # ref string or absolute path, for the episode id
    source_sha256: str  # sha256 of the raw source bytes or fetched text
    source_path: Path | None  # set for filesystem inputs
    source_url: str = ""
    source_meta: dict[str, Any] = field(default_factory=dict)
    writer: dict[str, Any] = field(default_factory=dict)


@dataclass
class SynthesizedChunk:
    """Chunk audio plus gate inputs."""

    planned: PlannedChunk
    pcm: bytes
    sample_rate: int
    attempts: int
    cached: bool
    duration_s: float = 0.0
    wpm: float = 0.0

    def measure(self) -> None:
        """Fill in duration and pace from the PCM payload."""
        if self.sample_rate:
            self.duration_s = len(self.pcm) / (2 * self.sample_rate)
        else:
            self.duration_s = 0.0
        minutes = self.duration_s / 60.0
        self.wpm = self.planned.words / minutes if minutes > 0 else 0.0


def format_spoken_date(raw: str) -> str:
    """Format a frontmatter date for speech ("2026-09-14" -> "September 14, 2026").

    Anything else passes through unchanged.
    """
    text = raw.strip().strip("\"'")
    match = _DATE_RE.search(text)
    if not match:
        return text
    try:
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return text
    return f"{_MONTHS[day.month - 1]} {day.day}, {day.year}"


def build_intro_text(meta: ScriptMeta, template: str) -> str:
    """Render the spoken intro from the template and script metadata."""
    kind = meta.kind
    edition = meta.edition
    title = meta.title
    raw_date = meta.date
    if kind == "article":
        if meta.author.strip() and meta.site.strip():
            kind_phrase = f", by {meta.author.strip()} at {meta.site.strip()}"
        elif meta.author.strip():
            kind_phrase = f", by {meta.author.strip()}"
        elif meta.site.strip():
            kind_phrase = f", from {meta.site.strip()}"
        else:
            kind_phrase = ""
    else:
        kind_phrase = ", SASE research" if kind == "research" else ""
    date_text = format_spoken_date(raw_date) if raw_date.strip() else ""
    date_phrase = (
        f", published {date_text}"
        if date_text and kind == "article"
        else (f" from {date_text}" if date_text else "")
    )
    effective = template
    if edition == "verbatim" and template == DEFAULT_INTRO_TEMPLATE:
        effective = _DEFAULT_VERBATIM_INTRO
    try:
        return effective.format(
            title=title, kind_phrase=kind_phrase, date_phrase=date_phrase
        )
    except (KeyError, IndexError, ValueError) as exc:
        raise SaseListenError(
            f"Invalid intro template: {exc}.",
            ExitCode.CONFIG,
            hint="Use {title}, {kind_phrase}, and {date_phrase} placeholders.",
        ) from exc


def build_outro_text(meta: ScriptMeta, template: str) -> str:
    """Render the spoken outro; an empty template disables it."""
    if not template:
        return ""
    title = meta.title
    try:
        return template.format(title=title)
    except (KeyError, IndexError, ValueError) as exc:
        raise SaseListenError(
            f"Invalid outro template: {exc}.",
            ExitCode.CONFIG,
            hint="Use the {title} placeholder.",
        ) from exc


def looks_like_ref(source: str) -> bool:
    """Return True for a `kind:path` artifact ref (not an existing path)."""
    return (
        not looks_like_url(source)
        and bool(_REF_RE.match(source))
        and not Path(source).exists()
    )


def looks_like_url(source: str) -> bool:
    """Return True for an absolute http(s) URL."""
    return bool(_URL_RE.match(source))


def looks_like_script(text: str) -> bool:
    """Return True when Markdown carries a narration-script frontmatter marker."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return False
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return False
    return isinstance(loaded, dict) and loaded.get("narration") == 1


def read_artifact_ref(ref: str) -> str:
    """Fetch a `kind:path` ref through `sase artifact read` (audited).

    The subprocess strips managed link tables; this function never imports
    sase, per the no-sase-import rule.
    """
    try:
        proc = subprocess.run(
            ["sase", "artifact", "read", ref, "sase-listen render"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise SaseListenError(
            f"Could not resolve ref '{ref}': the `sase` CLI is not on PATH.",
            ExitCode.CONFIG,
            hint="Install sase, or render a local Markdown file instead.",
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise SaseListenError(
            f"Could not resolve ref '{ref}': {exc}.",
            ExitCode.UNEXPECTED,
            hint="Check that `sase artifact read` works for this ref.",
        ) from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        tail = detail[-1] if detail else f"exit {proc.returncode}"
        raise SaseListenError(
            f"Could not resolve ref '{ref}': {tail}.",
            ExitCode.UNEXPECTED,
            hint="Check that the ref exists with `sase artifact read`.",
        )
    return proc.stdout


def _article_script(acquired: AcquiredSource) -> tuple[str, list[Omission]]:
    """Build or reuse the cached deterministic article narration script."""
    source_text = acquired.markdown_path.read_text(encoding="utf-8")
    script_text, omissions = normalize_markdown(
        source_text, filename=f"{acquired.metadata.get('title', 'article')}.md"
    )
    source_sha = str(acquired.metadata.get("source_sha256", ""))
    cached_sha = str(acquired.metadata.get("verbatim_source_sha256", ""))
    if cached_sha == source_sha and acquired.verbatim_script_path.is_file():
        cached_text = acquired.verbatim_script_path.read_text(encoding="utf-8")
        cached_meta = parse_script_text(cached_text).meta
        if (
            cached_meta.title == acquired.metadata.get("title", "")
            and cached_meta.source == acquired.metadata.get("canonical_url", "")
            and cached_meta.date == acquired.metadata.get("date", "")
            and cached_meta.author == acquired.metadata.get("author", "")
            and cached_meta.site == acquired.metadata.get("site", "")
        ):
            return cached_text, omissions
    script = parse_script_text(script_text)
    script.meta.title = str(acquired.metadata.get("title", script.meta.title))
    script.meta.source = str(acquired.metadata.get("canonical_url", ""))
    script.meta.date = str(acquired.metadata.get("date", ""))
    script.meta.author = str(acquired.metadata.get("author", ""))
    script.meta.site = str(acquired.metadata.get("site", ""))
    script.meta.kind = "article"
    script.meta.edition = "verbatim"
    script.meta.producer = "deterministic"
    result = script.dumps()
    save_verbatim_script(acquired, result)
    return result, omissions


def _read_article_metadata(path: Path, meta: ScriptMeta) -> dict[str, Any]:
    """Read cached source metadata when an article script is rendered directly."""
    source_meta: dict[str, Any] = {
        "canonical_url": normalize_url(meta.source),
        "title": meta.title,
        "author": meta.author,
        "site": meta.site,
        "date": meta.date,
        "fetched_at": "",
    }
    metadata_path = path.parent / "source.json"
    if metadata_path.is_file():
        try:
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (
                isinstance(value, dict)
                and value.get("canonical_url") == source_meta["canonical_url"]
            ):
                source_meta.update(value)
        except (OSError, json.JSONDecodeError):
            pass
    return source_meta


def load_source(
    source: str,
    *,
    edition: str | None = None,
    html_file: str = "",
    refresh: bool = False,
    config: SaseListenConfig | None = None,
) -> LoadedSource:
    """Resolve a render source to an exact narration script plus provenance."""
    if looks_like_url(source):
        selected_edition = edition or "brief"
        if selected_edition not in {"brief", "full", "verbatim"}:
            raise SaseListenError(
                f"URL edition '{selected_edition}' is not available.",
                ExitCode.USAGE,
                hint="Use --edition brief, --edition full, or --edition verbatim.",
            )
        acquired = acquire(source, html_file=html_file or None, refresh=refresh)
        metadata = acquired.metadata
        canonical = str(metadata.get("canonical_url", source))
        writer_summary: dict[str, Any] = {}
        if selected_edition in {"brief", "full"}:
            from sase_listen.writer import create_writer
            from sase_listen.writer.author import author_script, load_cached_script

            cfg = config if config is not None else load_config()[0]
            authored = (
                None if refresh else load_cached_script(acquired, selected_edition, cfg)
            )
            if authored is None:
                authored = author_script(
                    acquired,
                    selected_edition,
                    cfg,
                    create_writer(cfg),
                    refresh=refresh,
                )

            script_text = authored.text
            script_path: Path | None = authored.path
            writer_summary = authored.writer
            omissions: list[Omission] = []
        else:
            script_text, omissions = _article_script(acquired)
            script_path = acquired.verbatim_script_path
        return LoadedSource(
            script_text=script_text,
            script=parse_script_text(script_text),
            omissions=omissions,
            source_label=canonical,
            source_key=f"url:{canonical}#{selected_edition}",
            source_sha256=str(metadata.get("source_sha256", "")),
            source_path=script_path,
            source_url=canonical,
            source_meta=dict(metadata),
            writer=writer_summary,
        )
    if edition in {"brief", "full"}:
        raise SaseListenError(
            "Generated brief and full editions are available for article URLs.",
            ExitCode.USAGE,
            hint=(
                "Pass an article URL, or render an existing narration script "
                "without --edition."
            ),
        )
    if looks_like_ref(source):
        text = read_artifact_ref(source)
        if looks_like_script(text):
            script_text = text
            omissions = []
        else:
            filename = source.split("/")[-1] or "episode.md"
            script_text, omissions = normalize_markdown(text, filename=filename)
        return LoadedSource(
            script_text=script_text,
            script=parse_script_text(script_text),
            omissions=omissions,
            source_label=source,
            source_key=source,
            source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            source_path=None,
        )
    path = Path(source)
    if not path.exists():
        raise SaseListenError(
            f"Source not found: {source}.",
            ExitCode.USAGE,
            hint=(
                "Pass a narration script, Markdown file, kind:path ref, or http(s) URL."
            ),
        )
    raw = path.read_text(encoding="utf-8")
    if looks_like_script(raw):
        script_text = raw
        file_omissions: list[Omission] = []
    else:
        script_text, file_omissions = normalize_markdown(raw, filename=path.name)
    resolved = path.resolve()
    script = parse_script_text(script_text)
    if script.meta.kind == "article" and looks_like_url(script.meta.source):
        canonical = normalize_url(script.meta.source)
        metadata = _read_article_metadata(resolved, script.meta)
        sibling_source = resolved.parent / "source.md"
        source_sha = (
            hashlib.sha256(sibling_source.read_bytes()).hexdigest()
            if sibling_source.is_file()
            else hashlib.sha256(path.read_bytes()).hexdigest()
        )
        saved_writer_summary: dict[str, Any] = {}
        if script.meta.producer == "agent":
            from sase_listen.writer.author import load_writer_summary

            saved_writer_summary = load_writer_summary(resolved, script.meta.edition)
        return LoadedSource(
            script_text=script_text,
            script=script,
            omissions=file_omissions,
            source_label=canonical,
            source_key=f"url:{canonical}#{script.meta.edition}",
            source_sha256=source_sha,
            source_path=resolved,
            source_url=canonical,
            source_meta=metadata,
            writer=saved_writer_summary,
        )
    return LoadedSource(
        script_text=script_text,
        script=script,
        omissions=file_omissions,
        source_label=str(resolved),
        source_key=str(resolved),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_path=resolved,
    )


def check_lint(script_text: str, *, force: bool) -> tuple[list[str], list[str]]:
    """Lint a script; return (warnings, residue_notes), refusing on structure.

    Structural errors raise exit 6 unless `force` is set. Residue findings
    are cleaned during planning and reported, never fatal.
    """
    findings = lint_text(script_text)
    structural = [f for f in findings if f.severity == "error" and is_structural(f)]
    if structural and not force:
        rules = ", ".join(sorted({f.rule for f in structural}))
        raise SaseListenError(
            f"Script has structural errors ({rules}); refusing to render.",
            ExitCode.SCRIPT_STRUCTURAL,
            hint="Fix the script (see `sase-listen lint`) or re-run with --force.",
        )
    warnings = [
        f"{f.rule} {f.line}:{f.col}: {f.message}"
        for f in findings
        if f.severity == "warning"
    ]
    residue = sorted(
        {f.rule for f in findings if f.severity == "error" and is_residue(f)}
    )
    residue_notes = (
        [f"Cleaned residual Markdown ({', '.join(residue)}); speech omits the markup."]
        if residue
        else []
    )
    return warnings, residue_notes


def count_chunk_words(text: str) -> int:
    """Count whitespace-separated words in a chunk."""
    return len(text.split())


def split_sentences(text: str) -> list[str]:
    """Split a paragraph at sentence boundaries, keeping the punctuation."""
    return [part for part in _SENTENCE_SPLIT_RE.split(text.strip()) if part.strip()]


def hard_split(text: str, max_chars: int) -> list[str]:
    """Split over-long text at word boundaries (chars for a giant word)."""
    parts: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip() if current else word
        if len(trial) <= max_chars:
            current = trial
            continue
        if current:
            parts.append(current)
        while len(word) > max_chars:
            parts.append(word[:max_chars])
            word = word[max_chars:]
        current = word
    if current:
        parts.append(current)
    return parts or [text]


def fit_unit(text: str, max_chars: int) -> list[str]:
    """Split one packable unit so every piece fits `max_chars`."""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    for sentence in split_sentences(text):
        if len(sentence) <= max_chars:
            pieces.append(sentence)
        else:
            pieces.extend(hard_split(sentence, max_chars))
    return pieces or [text]


def pack_units(units: list[str], *, target_words: int, max_chars: int) -> list[str]:
    """Pack units greedily up to the engine's target words and max chars."""
    packed: list[str] = []
    current: list[str] = []
    current_words = 0
    current_chars = 0
    for unit in units:
        words = count_chunk_words(unit)
        joined = len(unit) + (2 if current else 0)
        if current and (
            current_words + words > target_words or current_chars + joined > max_chars
        ):
            packed.append("\n\n".join(current))
            current = []
            current_words = 0
            current_chars = 0
        current.append(unit)
        current_words += words
        current_chars += len(unit) + (2 if len(current) > 1 else 0)
    if current:
        packed.append("\n\n".join(current))
    return packed


def chapter_units(
    heading: str, paragraphs: list[str], *, max_chars: int, target_words: int
) -> list[str]:
    """Split a chapter into packable units; the first carries the heading."""
    raw: list[str] = []
    for index, para in enumerate(paragraphs):
        if count_chunk_words(para) > target_words or len(para) > max_chars:
            pieces = fit_unit(para, max_chars)
        else:
            pieces = [para]
        if index == 0 and pieces:
            pieces[0] = f"{heading}\n\n{pieces[0]}" if pieces[0] else heading
        raw.extend(pieces)
    if not raw:
        raw = [heading]
    units: list[str] = []
    for unit in raw:
        units.extend(fit_unit(unit, max_chars) if len(unit) > max_chars else [unit])
    return units


def plan_episode(
    loaded: LoadedSource,
    lexicon: Lexicon,
    narrator: ResolvedNarrator,
    *,
    target_words: int,
    max_chars: int,
    sample_rate: int,
    intro_text: str,
    outro_text: str,
    cache: ChunkCache,
    use_cache: bool,
) -> RenderPlan:
    """Plan chunks: lexicon, intro/outro, greedy packing, and cache lookup."""
    script = loaded.script
    episode_id = compute_episode_id(script.meta.title, loaded.source_key)
    texts: list[tuple[int, str, str]] = []  # (chapter_index, kind, text)
    clean_notes: list[str] = []
    for chapter_index, chapter in enumerate(script.chapters):
        heading = lexicon.apply(chapter.title)
        spoken_paras: list[str] = []
        for para in chapter.paragraphs:
            cleaned = clean_residual_markdown(para)
            spoken = lexicon.apply(cleaned.text)
            if cleaned.touched and spoken.strip():
                clean_notes.append(
                    f"Cleaned residual Markdown ({', '.join(cleaned.touched)}) "
                    f"in chapter '{chapter.title}'."
                )
            if spoken.strip():
                spoken_paras.append(spoken)
        units = chapter_units(
            heading, spoken_paras, max_chars=max_chars, target_words=target_words
        )
        for packed in pack_units(units, target_words=target_words, max_chars=max_chars):
            texts.append((chapter_index, "content", packed))
    if intro_text:
        intro_pieces = [
            (0, "intro", piece)
            for piece in fit_unit(lexicon.apply(intro_text), max_chars)
        ]
        texts = intro_pieces + texts
    if outro_text and script.chapters:
        for piece in fit_unit(lexicon.apply(outro_text), max_chars):
            texts.append((len(script.chapters) - 1, "outro", piece))
    chunks: list[PlannedChunk] = []
    for index, (chapter_index, kind, text) in enumerate(texts):
        words = count_chunk_words(text)
        key = cache_key(
            engine=narrator.engine,
            model=narrator.model,
            voice=narrator.voice,
            style=narrator.style,
            speed=narrator.speed,
            sample_rate=sample_rate,
            text=text,
        )
        if script.chapters:
            chapter_title = script.chapters[chapter_index].title
        else:
            chapter_title = script.meta.title
        chunks.append(
            PlannedChunk(
                index=index,
                chapter_index=chapter_index,
                chapter=chapter_title,
                kind=kind,
                text=text,
                words=words,
                cache_key=key,
            )
        )
    cached = 0
    if use_cache:
        for chunk in chunks:
            if cache.get(chunk.cache_key) is not None:
                cached += 1
    chapter_plans = [
        ChapterPlan(
            title=chapter.title,
            words=sum(c.words for c in chunks if c.chapter_index == i),
            chunks=sum(1 for c in chunks if c.chapter_index == i),
        )
        for i, chapter in enumerate(script.chapters)
    ]
    total_words = sum(chunk.words for chunk in chunks)
    return RenderPlan(
        title=script.meta.title,
        episode_id=episode_id,
        source_label=loaded.source_label,
        narrator=narrator,
        edition=script.meta.edition,
        producer=script.meta.producer,
        words=total_words,
        chunks=chunks,
        chapter_plans=chapter_plans,
        omissions=list(loaded.omissions),
        warnings=clean_notes,
        estimated_duration_s=total_words / TARGET_WPM * 60.0,
        estimated_cost_usd=estimate(narrator.model, total_words / TARGET_WPM * 60.0),
        cached_chunks=cached,
    )


def default_engine(narrator: ResolvedNarrator, config: SaseListenConfig) -> Engine:
    """Build the engine adapter for a resolved narrator (API keys included)."""
    if narrator.engine == "tone":
        return ToneEngine()
    if narrator.engine in ("gemini", "openai"):
        engine_cfg = getattr(config.engines, narrator.engine)
        try:
            key = resolve_api_key(
                engine=narrator.engine,
                env_names=list(engine_cfg.api_key_env),
                api_key_command=engine_cfg.api_key_command,
            )
        except CredentialsError as exc:
            raise SaseListenError(str(exc), ExitCode.CONFIG) from exc
        base_url = narrator.base_url or engine_cfg.base_url
        return create_engine(
            narrator.engine,
            api_key=key,
            base_url=base_url,
            timeout_s=engine_cfg.timeout_s,
        )
    raise SaseListenError(
        f"Unknown engine '{narrator.engine}' for narrator '{narrator.name}'.",
        ExitCode.CONFIG,
        hint="Use one of the built-in narrators: gemini, gemini-lite, openai, tone.",
    )


def transport_tuning(
    narrator: ResolvedNarrator, config: SaseListenConfig, engine: Engine
) -> tuple[int, int]:
    """Return (concurrency, max_retries) for a narrator's engine."""
    if narrator.engine in ("gemini", "openai"):
        engine_cfg = getattr(config.engines, narrator.engine)
        return max(1, engine_cfg.concurrency), max(0, engine_cfg.max_retries)
    return max(1, engine.limits(narrator.model).default_concurrency), 4


def synthesize_one(
    engine: Engine,
    narrator: ResolvedNarrator,
    text: str,
    *,
    max_retries: int,
) -> tuple[bytes, int, int]:
    """Synthesize one chunk; return (pcm, sample_rate, attempts)."""
    calls = 0

    def _operation() -> tuple[bytes, int]:
        nonlocal calls
        calls += 1
        result = engine.synthesize(
            SynthesisRequest(
                text=text,
                model=narrator.model,
                voice=narrator.voice,
                style=narrator.style,
                speed=narrator.speed,
            )
        )
        return result.pcm, result.sample_rate

    try:
        pcm, sample_rate = synthesize_with_retry(
            _operation, max_retries=max_retries, sleep=time.sleep
        )
    except CredentialsError as exc:
        raise SaseListenError(str(exc), ExitCode.CONFIG) from exc
    except (TransientEngineError, PermanentEngineError) as exc:
        raise SaseListenError(
            f"Synthesis failed after retries: {exc}.",
            ExitCode.SYNTHESIS_FAILED,
            hint="Re-run to resume from the chunk cache, or try another narrator.",
        ) from exc
    except Exception as exc:
        raise SaseListenError(
            f"Synthesis failed: {exc}.",
            ExitCode.SYNTHESIS_FAILED,
            hint="Re-run to resume from the chunk cache, or try another narrator.",
        ) from exc
    return pcm, sample_rate, calls


def synthesize_chunks(
    plan: RenderPlan,
    engine: Engine,
    cache: ChunkCache,
    *,
    max_retries: int,
    concurrency: int,
    use_cache: bool,
    events: RenderEvents,
) -> list[SynthesizedChunk]:
    """Synthesize every uncached chunk, caching each result immediately."""
    total = len(plan.chunks)
    out: list[SynthesizedChunk | None] = [None] * total
    pending: list[PlannedChunk] = []
    for chunk in plan.chunks:
        if use_cache:
            hit = cache.get(chunk.cache_key)
            if hit is not None:
                made = SynthesizedChunk(
                    planned=chunk,
                    pcm=hit.pcm,
                    sample_rate=hit.sample_rate,
                    attempts=0,
                    cached=True,
                )
                made.measure()
                out[chunk.index] = made
                events.on_chunk_finished(chunk.index, total, cached=True)
                continue
        pending.append(chunk)
        events.on_chunk_started(chunk.index, total)
    if pending:
        narrator = plan.narrator
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(
                    synthesize_one,
                    engine,
                    narrator,
                    chunk.text,
                    max_retries=max_retries,
                ): chunk
                for chunk in pending
            }
            try:
                for future in concurrent.futures.as_completed(futures):
                    chunk = futures[future]
                    pcm, sample_rate, attempts = future.result()
                    if use_cache:
                        cache.put(
                            chunk.cache_key,
                            pcm,
                            sample_rate=sample_rate,
                            words=chunk.words,
                            usage={"engine": narrator.engine, "model": narrator.model},
                        )
                    made = SynthesizedChunk(
                        planned=chunk,
                        pcm=pcm,
                        sample_rate=sample_rate,
                        attempts=attempts,
                        cached=False,
                    )
                    made.measure()
                    out[chunk.index] = made
                    events.on_chunk_finished(chunk.index, total, cached=False)
            except SaseListenError:
                for future in futures:
                    future.cancel()
                raise
    missing = [
        chunk.index
        for chunk, made in zip(plan.chunks, out, strict=True)
        if made is None
    ]
    if missing:
        raise SaseListenError(
            f"Synthesis left {len(missing)} chunk(s) without audio.",
            ExitCode.SYNTHESIS_FAILED,
            hint="Re-run to resume from the chunk cache.",
        )
    return [made for made in out if made is not None]


def hard_failure(pcm: bytes, wpm: float, sample_rate: int) -> str | None:
    """Return the hard-gate reason, or None when the chunk passes."""
    if not pcm or len(pcm) % 2 != 0:
        return "empty or undecodable PCM"
    if wpm < HARD_WPM_MIN or wpm > HARD_WPM_MAX:
        return f"pace {wpm:.0f} wpm outside {HARD_WPM_MIN:.0f}-{HARD_WPM_MAX:.0f}"
    if sample_rate > 0:
        pcm16 = np.frombuffer(pcm, dtype=np.int16)
        _, flagged = compress_silence(pcm16, sample_rate)
        if flagged:
            longest = max(flagged)
            return f"internal silence {longest:.1f} s exceeds 4 s"
    return None


def soft_failure(wpm: float, median_wpm: float | None) -> str | None:
    """Return the soft-gate reason, or None when the chunk passes."""
    if wpm < SOFT_WPM_MIN or wpm > SOFT_WPM_MAX:
        return f"pace {wpm:.0f} wpm outside {SOFT_WPM_MIN:.0f}-{SOFT_WPM_MAX:.0f}"
    if (
        median_wpm is not None
        and median_wpm > 0
        and (wpm < MEDIAN_LOW * median_wpm or wpm > MEDIAN_HIGH * median_wpm)
    ):
        return (
            f"pace {wpm:.0f} wpm outside "
            f"{MEDIAN_LOW}-{MEDIAN_HIGH}x the episode median {median_wpm:.0f}"
        )
    return None


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _resynthesize(
    made: SynthesizedChunk,
    engine: Engine,
    narrator: ResolvedNarrator,
    cache: ChunkCache,
    *,
    max_retries: int,
    use_cache: bool,
    events: RenderEvents,
    reason: str,
) -> SynthesizedChunk:
    """Re-synthesize one chunk with the cache bypassed, storing the result."""
    events.on_chunk_retried(made.planned.index, made.attempts + 1, reason)
    pcm, sample_rate, attempts = synthesize_one(
        engine, narrator, made.planned.text, max_retries=max_retries
    )
    if use_cache:
        cache.put(
            made.planned.cache_key,
            pcm,
            sample_rate=sample_rate,
            words=made.planned.words,
            usage={"engine": narrator.engine, "model": narrator.model},
        )
    fresh = SynthesizedChunk(
        planned=made.planned,
        pcm=pcm,
        sample_rate=sample_rate,
        attempts=made.attempts + attempts,
        cached=False,
    )
    fresh.measure()
    return fresh


def run_chunk_gates(
    synthesized: list[SynthesizedChunk],
    engine: Engine,
    narrator: ResolvedNarrator,
    cache: ChunkCache,
    *,
    max_retries: int,
    use_cache: bool,
    events: RenderEvents,
) -> tuple[list[SynthesizedChunk], list[str], int]:
    """Apply hard gates (2 re-syntheses, then exit 5) and soft gates (1 + warn).

    Returns (final chunks, warnings, retried-chunk count), keeping the best
    attempt (closest to 150 wpm) for every soft failure.
    """
    current = list(synthesized)
    retried_idxs: set[int] = set()
    hard_report: list[str] = []
    for pos, made in enumerate(current):
        made.measure()
        reason = hard_failure(made.pcm, made.wpm, made.sample_rate)
        tries = 0
        while reason is not None and tries < HARD_RESYNTH_LIMIT:
            tries += 1
            made = _resynthesize(
                made,
                engine,
                narrator,
                cache,
                max_retries=max_retries,
                use_cache=use_cache,
                events=events,
                reason=f"hard gate: {reason}",
            )
            reason = hard_failure(made.pcm, made.wpm, made.sample_rate)
        current[pos] = made
        if tries > 0 or made.attempts > 1:
            retried_idxs.add(made.planned.index)
        if reason is not None:
            hard_report.append(
                f"chunk {made.planned.index} ({made.planned.kind}): {reason}"
            )
    if hard_report:
        detail = "; ".join(hard_report)
        raise SaseListenError(
            f"Quality gate failed for {len(hard_report)} chunk(s): {detail}.",
            ExitCode.QUALITY_GATE_FAILED,
            hint="Inspect the chunk text, or re-render with another narrator.",
        )
    median_wpm: float | None = None
    if len(current) >= 3:
        median_wpm = _median([made.wpm for made in current])
    warnings: list[str] = []
    for pos, made in enumerate(current):
        reason = soft_failure(made.wpm, median_wpm)
        if reason is None:
            continue
        fresh = _resynthesize(
            made,
            engine,
            narrator,
            cache,
            max_retries=max_retries,
            use_cache=use_cache,
            events=events,
            reason=f"soft gate: {reason}",
        )
        retried_idxs.add(made.planned.index)
        best = min((made, fresh), key=lambda m: abs(m.wpm - TARGET_WPM))
        current[pos] = best
        kept = "re-synthesis" if best is fresh else "original"
        warnings.append(
            f"Chunk {made.planned.index} ({made.planned.kind}): {reason}; "
            f"re-synthesized once, kept the {kept} "
            f"({best.wpm:.0f} wpm)."
        )
    return current, warnings, len(retried_idxs)


def _pcm_to_int16(pcm: bytes) -> np.ndarray:
    """View s16le bytes as an int16 array (copying off read-only buffers)."""
    return np.array(np.frombuffer(pcm, dtype=np.int16), dtype=np.int16)


def assemble_episode(
    final: list[SynthesizedChunk],
    plan: RenderPlan,
    config: SaseListenConfig,
) -> tuple[np.ndarray, list[int], int]:
    """Assemble chapter PCM with exact offsets, honoring the intro gap.

    Returns (pcm, chapter_start_samples, sample_rate). The intro opens the
    first chapter and the outro closes the last, so ID3 chapters match the
    script. The intro-to-content pause is `intro_gap_s` (at least the chunk
    gap); later chapter offsets shift by the inserted silence exactly.
    """
    sample_rate = config.audio.sample_rate
    by_chapter: dict[int, list[SynthesizedChunk]] = {}
    for made in final:
        by_chapter.setdefault(made.planned.chapter_index, []).append(made)
    chapters: list[ChapterAudio] = []
    for chapter_index in sorted(by_chapter):
        group = sorted(by_chapter[chapter_index], key=lambda m: m.planned.index)
        chapters.append(
            ChapterAudio(
                title=group[0].planned.chapter,
                segments=[_pcm_to_int16(m.pcm) for m in group],
                sample_rates=[m.sample_rate for m in group],
            )
        )
    assembled, _long = assemble(
        chapters,
        sample_rate=sample_rate,
        chunk_gap_s=config.audio.chunk_gap_s,
        chapter_gap_s=config.audio.chapter_gap_s,
    )
    pcm = np.array(assembled.pcm, dtype=np.int16)
    starts = list(assembled.chapter_start_samples)
    extra_s = config.audio.intro_gap_s - config.audio.chunk_gap_s
    intro = next((m for m in final if m.planned.kind == "intro"), None)
    if intro is not None and extra_s > 1e-9:
        prepared = compress_silence(
            trim_silence(
                resample(_pcm_to_int16(intro.pcm), intro.sample_rate, sample_rate),
                sample_rate,
            ),
            sample_rate,
        )[0]
        offset = len(prepared)
        extra_n = round(extra_s * sample_rate)
        if 0 < offset <= len(pcm) and extra_n > 0:
            pcm = np.concatenate(
                [pcm[:offset], np.zeros(extra_n, dtype=np.int16), pcm[offset:]]
            )
            starts = [s if i == 0 else s + extra_n for i, s in enumerate(starts)]
    return pcm, starts, sample_rate


def resolve_cover_bytes(
    loaded: LoadedSource,
    *,
    cover_option: str,
    title: str,
    kind: str,
    date_text: str,
    generated_cover: bool = False,
) -> bytes:
    """Resolve cover art: --cover, frontmatter, sibling infographic, generated.

    When `generated_cover` is true, the generated title card is returned
    without inspecting frontmatter or sibling artwork.
    """
    if cover_option and generated_cover:
        raise SaseListenError(
            "Conflicting cover options: --cover and --generated-cover.",
            ExitCode.USAGE,
            hint="Use either --cover or --generated-cover, not both.",
        )
    if generated_cover:
        return resolve_cover(
            None,
            title,
            kind=kind,
            date_text=date_text,
            site=loaded.script.meta.site,
        )
    candidates: list[Path] = []
    if cover_option:
        candidates.append(Path(cover_option).expanduser())
    front_cover = str(loaded.script.meta.cover or "").strip()
    if front_cover and loaded.source_path is not None:
        candidates.append(loaded.source_path.parent / front_cover)
    if loaded.source_path is not None:
        sibling = (
            loaded.source_path.parent / f"{loaded.source_path.stem}_infographic.png"
        )
        candidates.append(sibling)
    for candidate in candidates:
        if not candidate.exists():
            if cover_option and candidate == Path(cover_option).expanduser():
                raise SaseListenError(
                    f"Cover image not found: {candidate}.",
                    ExitCode.USAGE,
                    hint="Point --cover at a readable image file.",
                )
            continue
        try:
            return resolve_cover(
                candidate,
                title,
                kind=kind,
                date_text=date_text,
                site=loaded.script.meta.site,
            )
        except ValueError as exc:
            raise SaseListenError(
                f"Cover image could not be used ({candidate}): {exc}.",
                ExitCode.USAGE,
                hint="Use a readable PNG or JPEG image.",
            ) from exc
    return resolve_cover(
        None,
        title,
        kind=kind,
        date_text=date_text,
        site=loaded.script.meta.site,
    )


def run_episode_gates(
    mp3_path: Path,
    *,
    expected_duration_s: float,
    chapter_titles: list[str],
    target_lufs: float,
    loudness_lufs: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Verify the mastered MP3; return (gate records, warnings)."""
    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3

    gates: list[dict[str, Any]] = []
    warnings: list[str] = []

    def _record(name: str, ok: bool, detail: str) -> None:
        gates.append({"name": name, "ok": ok, "detail": detail})

    try:
        loaded_mp3 = MP3(str(mp3_path))  # type: ignore[no-untyped-call]
        length = loaded_mp3.info.length if loaded_mp3.info is not None else None
        if length is None:
            raise ValueError("MP3 has no audio stream")
        actual_s = float(length)
        _record("mp3_decodes", True, f"duration {actual_s:.2f} s")
    except Exception as exc:
        _record("mp3_decodes", False, f"unreadable: {exc}")
        raise SaseListenError(
            f"Quality gate failed: the MP3 does not decode ({exc}).",
            ExitCode.QUALITY_GATE_FAILED,
            hint="Re-run the render; mastering writes are atomic.",
        ) from exc
    tolerance = 1.0 + 0.005 * expected_duration_s
    if abs(actual_s - expected_duration_s) <= tolerance:
        _record(
            "duration_matches",
            True,
            f"MP3 {actual_s:.2f} s within {tolerance:.2f} s "
            f"of {expected_duration_s:.2f} s",
        )
    else:
        _record(
            "duration_matches",
            False,
            f"MP3 {actual_s:.2f} s differs from {expected_duration_s:.2f} s",
        )
        raise SaseListenError(
            "Quality gate failed: MP3 duration "
            f"{actual_s:.2f} s is outside tolerance of {expected_duration_s:.2f} s.",
            ExitCode.QUALITY_GATE_FAILED,
            hint="Re-run the render; a partial mastering write fails this gate.",
        )
    try:
        tag = ID3(str(mp3_path))
        found: list[tuple[str, str]] = []
        for key in tag:
            if key.startswith("CHAP:"):
                frame = tag[key]
                sub = None
                if hasattr(frame, "sub_frames"):
                    sub = frame.sub_frames.get("TIT2")
                text = ""
                if sub is not None and getattr(sub, "text", None):
                    text = str(sub.text[0])
                element = key.split(":", 1)[1]
                found.append((element, text))
        found.sort(key=lambda item: item[0])
        tagged_titles = [title for _, title in found]
    except Exception as exc:
        _record("chapters_match", False, f"unreadable ID3: {exc}")
        raise SaseListenError(
            f"Quality gate failed: ID3 chapters are unreadable ({exc}).",
            ExitCode.QUALITY_GATE_FAILED,
            hint="Re-run the render.",
        ) from exc
    if tagged_titles == chapter_titles:
        _record("chapters_match", True, f"{len(tagged_titles)} chapters in order")
    else:
        _record(
            "chapters_match",
            False,
            f"MP3 has {tagged_titles}; script has {chapter_titles}",
        )
        raise SaseListenError(
            "Quality gate failed: MP3 chapters do not match the script.",
            ExitCode.QUALITY_GATE_FAILED,
            hint="Re-run the render; chapter marks derive from the script.",
        )
    if abs(loudness_lufs - target_lufs) <= 1.0:
        _record(
            "loudness_on_target",
            True,
            f"{loudness_lufs:.1f} LUFS within 1 LU of {target_lufs:.1f}",
        )
    else:
        _record(
            "loudness_on_target",
            False,
            f"{loudness_lufs:.1f} LUFS vs target {target_lufs:.1f}",
        )
        raise SaseListenError(
            "Quality gate failed: loudness "
            f"{loudness_lufs:.1f} LUFS is more than 1 LU from {target_lufs:.1f}.",
            ExitCode.QUALITY_GATE_FAILED,
            hint="Check the ffmpeg loudnorm filter, then re-run.",
        )
    size = mp3_path.stat().st_size
    if size > SIZE_WARN_BYTES:
        warnings.append(
            f"Episode is {size / 1_048_576:.1f} MB; Telegram's bot limit is 50 MB."
        )
    return gates, warnings


@dataclass
class PreparedRender:
    """Everything `render` needs after config and narrator resolution."""

    config: SaseListenConfig
    narrator: ResolvedNarrator
    lexicon: Lexicon
    engine: Engine
    cache: ChunkCache
    concurrency: int
    max_retries: int


def prepare(
    request: RenderRequest,
    *,
    config: SaseListenConfig | None = None,
    engine: Engine | None = None,
    cache: ChunkCache | None = None,
) -> PreparedRender:
    """Resolve config, narrator, lexicon, engine, cache, and transport tuning."""
    cfg = config if config is not None else load_config()[0]
    narrator = resolve_narrator(
        request.narrator or cfg.narrator, cfg, voice_override=request.voice_override
    )
    lexicon = load_merged(cfg.lexicon or None)
    resolved_engine = engine if engine is not None else default_engine(narrator, cfg)
    concurrency, max_retries = transport_tuning(narrator, cfg, resolved_engine)
    resolved_cache = (
        cache if cache is not None else ChunkCache(cache_dir(), max_gb=cfg.cache.max_gb)
    )
    return PreparedRender(
        config=cfg,
        narrator=narrator,
        lexicon=lexicon,
        engine=resolved_engine,
        cache=resolved_cache,
        concurrency=concurrency,
        max_retries=max_retries,
    )


def plan_request(
    request: RenderRequest, prepared: PreparedRender, loaded: LoadedSource
) -> RenderPlan:
    """Lint the source and build the full render plan (no synthesis)."""
    lint_warnings, residue_notes = check_lint(loaded.script_text, force=request.force)
    intro_text = build_intro_text(loaded.script.meta, prepared.config.intro_template)
    outro_text = build_outro_text(loaded.script.meta, prepared.config.outro_template)
    plan = plan_episode(
        loaded,
        prepared.lexicon,
        prepared.narrator,
        target_words=prepared.engine.limits(prepared.narrator.model).target_words,
        max_chars=prepared.engine.limits(prepared.narrator.model).max_chars,
        sample_rate=prepared.config.audio.sample_rate,
        intro_text=intro_text,
        outro_text=outro_text,
        cache=prepared.cache,
        use_cache=not request.no_cache,
    )
    if loaded.script.meta.kind == "article":
        plan.title = article_display_title(plan.title, loaded.script.meta.edition)
    plan.script_path = str(loaded.source_path or "")
    plan.writer = dict(loaded.writer)
    plan.warnings = lint_warnings + residue_notes + plan.warnings
    outline = loaded.source_meta.get("outline", {})
    if (
        isinstance(outline, dict)
        and int(outline.get("found", 0) or 0) >= 3
        and not outline.get("restored")
        and bool(outline.get("missing"))
    ):
        plan.warnings.append(
            "The page had at least three outline headings, but none were restored."
        )
    return plan


def plan_to_json(plan: RenderPlan) -> dict[str, Any]:
    """Serialize a dry-run plan to the `--dry-run --json` object."""
    narrator = plan.narrator
    return {
        "ok": True,
        "dry_run": True,
        "episode_id": plan.episode_id,
        "title": plan.title,
        "source": plan.source_label,
        "narrator": {
            "name": narrator.name,
            "engine": narrator.engine,
            "model": narrator.model,
            "voice": narrator.voice,
        },
        "edition": plan.edition,
        "producer": plan.producer,
        "script_path": plan.script_path,
        "writer": dict(plan.writer),
        "words": plan.words,
        "estimated_duration_s": round(plan.estimated_duration_s, 2),
        "estimated_cost_usd": round(plan.estimated_cost_usd, 6),
        "chunks": {
            "total": len(plan.chunks),
            "cached": plan.cached_chunks,
            "synthesis_needed": plan.synthesis_needed,
        },
        "chapters": [
            {
                "title": chapter.title,
                "words": chapter.words,
                "approx_min": round(chapter.words / TARGET_WPM, 1),
                "chunks": chapter.chunks,
            }
            for chapter in plan.chapter_plans
        ],
        "omissions": [omission.to_dict() for omission in plan.omissions],
        "warnings": list(plan.warnings),
    }


def result_to_json(result: RenderResult) -> dict[str, Any]:
    """Serialize a finished render to the `render --json` success object."""
    return {
        "ok": True,
        "episode_id": result.episode_id,
        "title": result.title,
        "script_path": result.script_path,
        "writer": dict(result.writer),
        "audio_path": result.audio_path,
        "manifest_path": result.manifest_path,
        "duration_s": round(result.duration_s, 2),
        "size_bytes": result.size_bytes,
        "chapters": [
            {"title": chapter["title"], "start_s": round(float(chapter["start_s"]), 3)}
            for chapter in result.chapters
        ],
        "narrator": {
            "name": result.narrator_name,
            "engine": result.narrator_engine,
            "model": result.narrator_model,
            "voice": result.narrator_voice,
        },
        "chunks": {
            "total": result.total_chunks,
            "cached": result.cached_chunks,
            "synthesized": result.synthesized_chunks,
            "retried": result.retried_chunks,
        },
        "loudness_lufs": round(result.loudness_lufs, 2),
        "cost_usd_estimate": round(result.cost_usd_estimate, 6),
        "published": result.published,
        "publish_queued": result.publish_queued,
        "publish_host": result.publish_host,
        "warnings": list(result.warnings),
    }


def error_to_json(exc: SaseListenError) -> dict[str, Any]:
    """Serialize a render failure to the `--json` error object."""
    return {
        "ok": False,
        "error": {"code": int(exc.code), "message": str(exc), "hint": exc.hint},
    }


def render(
    request: RenderRequest,
    *,
    config: SaseListenConfig | None = None,
    engine: Engine | None = None,
    cache: ChunkCache | None = None,
    events: RenderEvents | None = None,
    library_root: Path | None = None,
) -> RenderPlan | RenderResult:
    """Render a source to a chaptered MP3 episode in the library.

    Returns the :class:`RenderPlan` for dry runs, else the
    :class:`RenderResult`. Raises :class:`SaseListenError` with the
    command's exit code on any failure.
    """
    if request.cover and request.generated_cover:
        raise SaseListenError(
            "Conflicting cover options: --cover and --generated-cover.",
            ExitCode.USAGE,
            hint="Use either --cover or --generated-cover, not both.",
        )
    listener = events if events is not None else RenderEvents()
    effective_config = config if config is not None else load_config()[0]
    loaded = load_source(
        request.source,
        edition=request.edition,
        html_file=request.html,
        refresh=request.refresh,
        config=effective_config,
    )
    prepared = prepare(request, config=effective_config, engine=engine, cache=cache)
    cfg = prepared.config
    plan = plan_request(request, prepared, loaded)
    listener.on_plan(plan)
    if request.dry_run:
        return plan
    if not loaded.script.chapters:
        raise SaseListenError(
            "Script has no '##' chapters to render.",
            ExitCode.SCRIPT_STRUCTURAL,
            hint="Add at least one '##' chapter with spoken paragraphs.",
        )
    use_cache = not request.no_cache
    meta = loaded.script.meta
    cover_jpeg = resolve_cover_bytes(
        loaded,
        cover_option=request.cover,
        title=plan.title,
        kind=meta.kind,
        date_text=format_spoken_date(meta.date) if meta.date.strip() else "",
        generated_cover=request.generated_cover,
    )
    with episode_lock(plan.episode_id):
        listener.on_stage("synthesize")
        synthesized = synthesize_chunks(
            plan,
            prepared.engine,
            prepared.cache,
            max_retries=prepared.max_retries,
            concurrency=prepared.concurrency,
            use_cache=use_cache,
            events=listener,
        )
        listener.on_stage("gates")
        final, gate_warnings, retried = run_chunk_gates(
            synthesized,
            prepared.engine,
            prepared.narrator,
            prepared.cache,
            max_retries=prepared.max_retries,
            use_cache=use_cache,
            events=listener,
        )
        listener.on_stage("master")
        pcm, starts, sample_rate = assemble_episode(final, plan, cfg)
        ffmpeg = resolve_ffmpeg()
        slug = slugify(plan.title)
        mp3_name = episode_mp3_name(slug)
        staging = staging_path(plan.episode_id, library_root) / "master.mp3"
        staging.parent.mkdir(parents=True, exist_ok=True)
        stats = master_to_mp3(
            pcm,
            sample_rate,
            staging,
            ffmpeg,
            target_lufs=cfg.audio.loudness_lufs,
            true_peak_db=cfg.audio.true_peak_db,
            bitrate_kbps=cfg.audio.bitrate_kbps,
        )
        expected_s = len(pcm) / sample_rate
        duration_ms = round(expected_s * 1000)
        marks = [
            ChapterMark(
                title=title,
                start_ms=round(start / sample_rate * 1000),
                end_ms=round(
                    (starts[i + 1] if i + 1 < len(starts) else len(pcm))
                    / sample_rate
                    * 1000
                ),
            )
            for i, (title, start) in enumerate(
                zip([c.title for c in loaded.script.chapters], starts, strict=True)
            )
        ]
        listener.on_stage("tag")
        write_tags(
            staging,
            EpisodeMeta(
                title=plan.title,
                author=meta.author or cfg.author,
                date=meta.date,
                description=(
                    article_coverage_sentence(meta.edition)
                    if meta.kind == "article"
                    else f"AI-narrated audio edition of {plan.title}."
                ),
                episode_id=plan.episode_id,
                source_ref=loaded.source_label,
                kind=meta.kind,
            ),
            marks,
            duration_ms,
            cover_jpeg=cover_jpeg,
        )
        listener.on_stage("commit")
        gates, size_warnings = run_episode_gates(
            staging,
            expected_duration_s=expected_s,
            chapter_titles=[c.title for c in loaded.script.chapters],
            target_lufs=cfg.audio.loudness_lufs,
            loudness_lufs=stats.loudness_lufs,
        )
        warnings = plan.warnings + gate_warnings + size_warnings
        actual_cached = sum(1 for made in final if made.cached)
        actual_synthesized = len(final) - actual_cached
        cost = estimate(prepared.narrator.model, stats.duration_s)
        source_payload: dict[str, Any]
        if loaded.source_url:
            source_payload = {
                "url": loaded.source_url,
                "sha256": loaded.source_sha256,
                "title": str(loaded.source_meta.get("title", meta.title)),
                "author": str(loaded.source_meta.get("author", meta.author)),
                "site": str(loaded.source_meta.get("site", meta.site)),
                "date": str(loaded.source_meta.get("date", meta.date)),
                "fetched_at": str(loaded.source_meta.get("fetched_at", "")),
                "script_path": str(loaded.source_path or ""),
            }
        else:
            source_payload = {
                ("ref" if loaded.source_path is None else "path"): loaded.source_label,
                "sha256": loaded.source_sha256,
                "blob": meta.source_blob,
            }
        script_payload: dict[str, Any] = {
            "sha256": hashlib.sha256(loaded.script_text.encode("utf-8")).hexdigest(),
            "producer": meta.producer,
            "edition": meta.edition,
            "words": plan.words,
        }
        if loaded.writer:
            script_payload["writer"] = {
                key: loaded.writer[key]
                for key in ("model", "model_version", "prompt_version", "attempts")
                if key in loaded.writer
            }
        manifest_payload = build_manifest(
            episode_id=plan.episode_id,
            title=plan.title,
            version=__version__,
            source=source_payload,
            script=script_payload,
            narrator={
                "name": prepared.narrator.name,
                "engine": prepared.narrator.engine,
                "model": prepared.narrator.model,
                "voice": prepared.narrator.voice,
                "style_sha256": hashlib.sha256(
                    prepared.narrator.style.encode("utf-8")
                ).hexdigest(),
            },
            lexicon_sha256=prepared.lexicon.sha256(),
            chunks=[
                {
                    "index": made.planned.index,
                    "chapter": made.planned.chapter,
                    "words": made.planned.words,
                    "cache_key": made.planned.cache_key,
                    "duration_s": round(made.duration_s, 3),
                    "attempts": made.attempts,
                    "cached": made.cached,
                }
                for made in final
            ],
            chapters=[
                {
                    "title": mark.title,
                    "start_ms": mark.start_ms,
                    "end_ms": mark.end_ms,
                }
                for mark in marks
            ],
            audio={
                "file": mp3_name,
                "bytes": stats.size_bytes,
                "duration_s": round(stats.duration_s, 3),
                "bitrate_kbps": cfg.audio.bitrate_kbps,
                "loudness_lufs": round(stats.loudness_lufs, 2),
                "true_peak_db": round(stats.true_peak_dbtp, 2),
            },
            gates=gates,
            omissions=[omission.to_dict() for omission in plan.omissions],
            cost_usd_estimate=round(cost, 6),
            published=False,
        )
        chapters_json = (
            json.dumps(
                {
                    "version": "1.0",
                    "chapters": [
                        {
                            "startTime": round(mark.start_ms / 1000, 3),
                            "title": mark.title,
                        }
                        for mark in marks
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        payloads = {
            mp3_name: staging.read_bytes(),
            "script.md": loaded.script_text.encode("utf-8"),
            "cover.jpg": cover_jpeg,
            "chapters.json": chapters_json,
            "manifest.json": dumps_manifest(manifest_payload),
        }
        staged_mp3_size = len(payloads[mp3_name])
        final_dir = atomic_commit(plan.episode_id, payloads, library_root)
        if request.output:
            out_path = Path(request.output).expanduser()
            if out_path.parent != Path("."):
                out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_out = out_path.with_name(f".tmp-{out_path.name}")
            shutil.copyfile(final_dir / mp3_name, tmp_out)
            shutil.move(str(tmp_out), str(out_path))
        prepared.cache.prune()
    want_publish = (
        request.publish
        if request.publish is not None
        else (cfg.feed.auto_publish and meta.kind in {"research", "article"})
    )
    published = False
    publish_queued = False
    publish_host = ""
    if want_publish:
        try:
            published_info = publish_any(plan.episode_id, cfg, library=library_root)
        except SaseListenError as exc:
            if feed_role(cfg) == "remote":
                queue_publish(plan.episode_id, str(exc))
                publish_queued = True
                host = cfg.feed.host.strip()
                if request.publish:
                    if PENDING_HINT not in exc.hint:
                        exc.hint = (
                            f"{exc.hint} {PENDING_HINT}".strip()
                            if exc.hint
                            else PENDING_HINT
                        )
                    raise
                warnings.append(
                    f"Auto-publish to {host} failed ({exc}); queued — "
                    "run `sase-listen publish --pending`"
                )
            elif request.publish:
                raise
            else:
                warnings.append(f"Auto-publish skipped: {exc}")
        else:
            mark_manifest_published(plan.episode_id, library_root)
            published = True
            publish_host = str(published_info.get("host") or "")
    result = RenderResult(
        episode_id=plan.episode_id,
        title=plan.title,
        audio_path=str(final_dir / mp3_name),
        manifest_path=str(final_dir / "manifest.json"),
        duration_s=round(stats.duration_s, 3),
        size_bytes=staged_mp3_size,
        chapters=[
            {"title": mark.title, "start_s": round(mark.start_ms / 1000, 3)}
            for mark in marks
        ],
        narrator_name=prepared.narrator.name,
        narrator_engine=prepared.narrator.engine,
        narrator_model=prepared.narrator.model,
        narrator_voice=prepared.narrator.voice,
        total_chunks=len(final),
        cached_chunks=actual_cached,
        synthesized_chunks=actual_synthesized,
        retried_chunks=retried,
        loudness_lufs=round(stats.loudness_lufs, 2),
        cost_usd_estimate=round(cost, 6),
        published=published,
        publish_queued=publish_queued,
        publish_host=publish_host,
        script_path=str(loaded.source_path or ""),
        writer=dict(loaded.writer),
        warnings=warnings,
    )
    listener.on_done(result)
    return result
