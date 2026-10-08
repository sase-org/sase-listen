"""Article script authoring, lint repair, and per-source persistence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from sase_listen import invocation
from sase_listen.config import SaseListenConfig
from sase_listen.engines.base import (
    CredentialsError,
    PermanentEngineError,
    TransientEngineError,
)
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.events import RenderEvents
from sase_listen.script import Finding, lint_text
from sase_listen.web.store import AcquiredSource
from sase_listen.writer.base import Writer
from sase_listen.writer.prompt import WRITER_PROMPT_VERSION, system_prompt, user_prompt

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*\n?", re.DOTALL)
_H1_RE = re.compile(r"^#\s+.*(?:\n|$)", re.MULTILINE)
_FINDING_REPAIR_RULES = {"W011", "W013"}


@dataclass(frozen=True)
class AuthoredScript:
    """Generated script, its cache path, and public manifest summary."""

    text: str
    path: Path
    writer: dict[str, Any]


def _strip_body(text: str) -> str:
    body = text.strip()
    source_lines = body.splitlines()
    if (
        len(source_lines) >= 2
        and source_lines[0].strip().startswith("```")
        and source_lines[-1].strip() == "```"
    ):
        source_lines = source_lines[1:-1]
    lines: list[str] = []
    in_fence = False
    for line in source_lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    body = "\n".join(lines).strip()
    body = _FRONTMATTER_RE.sub("", body, count=1).strip()
    body = _H1_RE.sub("", body).strip()
    return body


def _frontmatter(article: AcquiredSource, edition: str, body: str) -> str:
    metadata = article.metadata
    source_words = int(metadata.get("words", 0) or 0)
    target_minutes = (
        4 if edition == "brief" else min(16, math.ceil(0.9 * source_words / 150))
    )
    fields: dict[str, Any] = {
        "narration": 1,
        "title": str(metadata.get("title") or "Untitled article"),
        "source": str(metadata.get("canonical_url") or ""),
        "date": str(metadata.get("date") or ""),
        "kind": "article",
        "edition": edition,
        "producer": "agent",
    }
    author = str(metadata.get("author") or "")
    site = str(metadata.get("site") or "")
    if author:
        fields["author"] = author
    if site:
        fields["site"] = site
    fields["target_minutes"] = target_minutes
    encoded = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{encoded}\n---\n\n{body.strip()}\n"


def _repair_prompt(base: str, body: str, findings: list[Finding]) -> str:
    details = "\n".join(
        f"{finding.rule} {finding.line}: {finding.message} — {finding.hint}"
        for finding in findings
    )
    return (
        f"{base}\n\nRepair the previous body using these lint findings. "
        "Return the full corrected body only.\n\n"
        f"Findings:\n{details}\n\nPrevious body:\n<PREVIOUS_BODY>\n"
        f"{body}\n</PREVIOUS_BODY>"
    )


def _repair_findings(
    findings: list[Finding], *, repair_under_budget: bool
) -> list[Finding]:
    return [
        finding
        for finding in findings
        if finding.severity == "error"
        or finding.rule in _FINDING_REPAIR_RULES
        or (repair_under_budget and finding.rule == "W012")
    ]


def _required_findings_for_script(
    script: str,
    *,
    source_text: str,
    source_words: int,
    edition: str,
) -> tuple[list[Finding], list[Finding]]:
    """Lint a script and return (final_findings, required_findings)."""
    final_findings = lint_text(script, source_text=source_text)
    budget = 600 if edition == "brief" else 2400
    if edition == "brief":
        repair_under_budget = source_words >= 300
    else:
        repair_under_budget = math.ceil(source_words * 0.9) >= math.ceil(budget * 0.5)
    required = _repair_findings(final_findings, repair_under_budget=repair_under_budget)
    return final_findings, required


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".tmp-{os.getpid()}-{path.name}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _writer_paths(article: AcquiredSource, edition: str) -> tuple[Path, Path]:
    return (
        article.directory / f"{edition}_narration.md",
        article.directory / f"{edition}_writer.json",
    )


def _load_cached(
    script_path: Path,
    metadata_path: Path,
    *,
    source_sha256: str,
    model: str,
    edition: str,
    article: AcquiredSource | None = None,
) -> AuthoredScript | None:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            not isinstance(metadata, dict)
            or metadata.get("source_sha256") != source_sha256
            or metadata.get("prompt_version") != WRITER_PROMPT_VERSION
            or metadata.get("edition") != edition
            or metadata.get("model") != model
            or not script_path.is_file()
        ):
            return None
        if metadata.get("required_findings"):
            if article is None:
                return None
            try:
                cached_text = script_path.read_text(encoding="utf-8")
                source_text = article.markdown_path.read_text(encoding="utf-8")
            except OSError:
                return None
            source_words = int(article.metadata.get("words", 0) or 0)
            final, required = _required_findings_for_script(
                cached_text,
                source_text=source_text,
                source_words=source_words,
                edition=edition,
            )
            if required:
                return None
            try:
                metadata["final_findings"] = [finding.to_dict() for finding in final]
                metadata["required_findings"] = []
                _atomic_write(
                    metadata_path,
                    (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode(
                        "utf-8"
                    ),
                )
            except OSError:
                pass
            script = cached_text
        else:
            script = script_path.read_text(encoding="utf-8")
        summary = {
            key: metadata.get(key)
            for key in ("model", "model_version", "prompt_version", "attempts")
        }
        tokens = metadata.get("tokens")
        if isinstance(tokens, dict):
            summary["tokens"] = {
                "input": int(tokens.get("input", 0) or 0),
                "output": int(tokens.get("output", 0) or 0),
            }
        return AuthoredScript(script, script_path, summary)
    except (OSError, json.JSONDecodeError):
        return None


def load_cached_script(
    article: AcquiredSource,
    edition: str,
    cfg: SaseListenConfig,
) -> AuthoredScript | None:
    """Reuse a writer cache without resolving or contacting API credentials."""
    script_path, metadata_path = _writer_paths(article, edition)
    return _load_cached(
        script_path,
        metadata_path,
        source_sha256=str(article.metadata.get("source_sha256", "")),
        model=cfg.writer.model,
        edition=edition,
        article=article,
    )


def load_writer_summary(script_path: Path, edition: str) -> dict[str, Any]:
    """Load the compact writer record next to a generated edition script."""
    metadata_path = script_path.parent / f"{edition}_writer.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(metadata, dict) or metadata.get("edition") != edition:
        return {}
    summary = {
        key: metadata.get(key)
        for key in ("model", "model_version", "prompt_version", "attempts")
    }
    tokens = metadata.get("tokens")
    if isinstance(tokens, dict):
        summary["tokens"] = {
            "input": int(tokens.get("input", 0) or 0),
            "output": int(tokens.get("output", 0) or 0),
        }
    return summary


def author_script(
    article: AcquiredSource,
    edition: str,
    cfg: SaseListenConfig,
    writer: Writer,
    *,
    refresh: bool = False,
    events: RenderEvents | None = None,
) -> AuthoredScript:
    """Write, lint, repair, and cache one article edition."""
    if edition not in {"brief", "full"}:
        raise SaseListenError(
            f"Unsupported generated article edition '{edition}'.",
            ExitCode.USAGE,
            hint="Use --edition brief, --edition full, or --edition verbatim.",
        )
    script_path, writer_path = _writer_paths(article, edition)
    source_sha = str(article.metadata.get("source_sha256", ""))
    if not refresh:
        cached = load_cached_script(article, edition, cfg)
        if cached is not None:
            return cached

    system = system_prompt(
        edition, source_format=str(article.metadata.get("format", "html"))
    )
    base_user = user_prompt(article, edition)
    prompt_sha = hashlib.sha256(f"{system}\0{base_user}".encode()).hexdigest()
    current_user = base_user
    input_tokens = 0
    output_tokens = 0
    reply_model_version = ""
    final_findings: list[Finding] = []
    required_findings: list[Finding] = []
    body = ""
    attempts = 0
    model = cfg.writer.model
    total_attempts = cfg.writer.max_attempts
    for attempts in range(1, total_attempts + 1):
        if events is not None:
            if attempts == 1:
                events.on_step(
                    "write", f"attempt 1 of {total_attempts} · waiting on {model}"
                )
            else:
                count = len(required_findings)
                events.on_step(
                    "write",
                    f"attempt {attempts} of {total_attempts} · fixing {count} "
                    f"lint finding(s) · waiting on {model}",
                )
        try:
            reply = writer.write(system, current_user)
        except CredentialsError as exc:
            raise SaseListenError(str(exc), ExitCode.CONFIG) from exc
        except (TransientEngineError, PermanentEngineError) as exc:
            raise SaseListenError(
                f"Article script writing failed: {exc}.",
                ExitCode.UNEXPECTED,
                hint="Re-run the command or choose another Gemini writer model.",
            ) from exc
        input_tokens += reply.input_tokens
        output_tokens += reply.output_tokens
        reply_model_version = reply.model_version or cfg.writer.model
        if events is not None:
            events.on_step(
                "write",
                f"attempt {attempts} of {total_attempts} · "
                "checking the draft against the article",
            )
        body = _strip_body(reply.text)
        script = _frontmatter(article, edition, body)
        source_text = article.markdown_path.read_text(encoding="utf-8")
        source_words = int(article.metadata.get("words", 0) or 0)
        final_findings, required_findings = _required_findings_for_script(
            script,
            source_text=source_text,
            source_words=source_words,
            edition=edition,
        )
        if not required_findings:
            break
        if attempts < cfg.writer.max_attempts:
            current_user = _repair_prompt(base_user, body, required_findings)

    writer_record: dict[str, Any] = {
        "model": cfg.writer.model,
        "model_version": reply_model_version,
        "prompt_version": WRITER_PROMPT_VERSION,
        "prompt_sha256": prompt_sha,
        "attempts": attempts,
        "final_findings": [finding.to_dict() for finding in final_findings],
        "required_findings": [finding.to_dict() for finding in required_findings],
        "tokens": {"input": input_tokens, "output": output_tokens},
        "source_sha256": source_sha,
        "edition": edition,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _atomic_write(script_path, script.encode("utf-8"))
    _atomic_write(
        writer_path,
        (json.dumps(writer_record, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    summary = {
        key: writer_record[key]
        for key in ("model", "model_version", "prompt_version", "attempts")
    }
    summary["tokens"] = {"input": input_tokens, "output": output_tokens}
    if required_findings:
        raise SaseListenError(
            "Generated article script still has required lint findings after "
            f"{attempts} attempt(s).",
            ExitCode.SCRIPT_STRUCTURAL,
            hint=(
                "Edit the saved script and rerun the same command, or run "
                f"`{invocation.command('render', str(script_path))}`."
            ),
        )
    return AuthoredScript(script, script_path, summary)
