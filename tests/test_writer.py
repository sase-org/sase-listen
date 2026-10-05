"""Article writer prompts, lint repair, caching, and Gemini adapter."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sase_listen.config import default_config
from sase_listen.engines.base import CredentialsError, TransientEngineError
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.web.store import AcquiredSource
from sase_listen.writer.author import author_script
from sase_listen.writer.base import WriterReply
from sase_listen.writer.gemini import GeminiWriter, _map_api_error


def _article(tmp_path: Path, *, words: int = 200) -> AcquiredSource:
    directory = tmp_path / "article"
    directory.mkdir()
    markdown_path = directory / "source.md"
    markdown_path.write_text(
        "# Article Test\n\nThe article records the original claim that 42 teams "
        "adopted the process. "
        + "It describes the team's engineering decisions and their results. "
        * 20,
        encoding="utf-8",
    )
    return AcquiredSource(
        directory=directory,
        page_path=directory / "page.html",
        markdown_path=markdown_path,
        metadata={
            "canonical_url": "https://example.test/article",
            "title": "Article Test",
            "author": "Ada Lovelace",
            "site": "Example",
            "date": "2026-02-11",
            "source_sha256": "source-sha",
            "words": words,
            "outline": {"restored": ["The claim", "The results"]},
        },
    )


class _Writer:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.users: list[str] = []

    def write(self, system: str, user: str) -> WriterReply:
        assert "Article adaptation rules" in system
        self.users.append(user)
        text = self.replies.pop(0)
        return WriterReply(text, "stub-model-version", 12, 34)


def test_frontmatter_fence_h1_and_metadata(tmp_path: Path) -> None:
    article = _article(tmp_path)
    writer = _Writer(
        "```markdown\n---\ntitle: wrong\n---\n# stray title\n\n"
        "## The evidence\n\nThe team describes its engineering choices.\n```"
    )
    result = author_script(article, "brief", default_config(), writer)
    meta = yaml.safe_load(result.text.split("---\n", 2)[1])
    assert meta == {
        "narration": 1,
        "title": "Article Test",
        "source": "https://example.test/article",
        "date": "2026-02-11",
        "kind": "article",
        "edition": "brief",
        "producer": "agent",
        "target_minutes": 4,
        "author": "Ada Lovelace",
        "site": "Example",
    }
    assert "# stray title" not in result.text
    assert "title: wrong" not in result.text
    assert {
        key: result.writer[key]
        for key in ("model", "model_version", "prompt_version", "attempts")
    } == {
        "model": "gemini-3.1-pro-preview",
        "model_version": "stub-model-version",
        "prompt_version": 1,
        "attempts": 1,
    }
    assert result.writer["tokens"] == {"input": 12, "output": 34}
    record = json.loads((article.directory / "brief_writer.json").read_text())
    assert record["tokens"] == {"input": 12, "output": 34}
    assert record["source_sha256"] == "source-sha"


def test_number_fidelity_repair_and_cached_reuse(tmp_path: Path) -> None:
    article = _article(tmp_path)
    writer = _Writer(
        "## The evidence\n\nThe team reported 17 decisions.",
        "## The evidence\n\nThe team described the decisions.",
    )
    cfg = default_config()
    result = author_script(article, "brief", cfg, writer)
    assert result.writer["attempts"] == 2
    assert "W013" in writer.users[1]
    assert "17" not in result.text

    class _UnexpectedWriter:
        def write(self, system: str, user: str) -> WriterReply:
            raise AssertionError("expected the script cache to be reused")

    cached = author_script(article, "brief", cfg, _UnexpectedWriter())
    assert cached.text == result.text
    assert cached.writer["attempts"] == 2


def test_exhausted_lint_repairs_save_script_and_fail(tmp_path: Path) -> None:
    article = _article(tmp_path)
    cfg = default_config()
    cfg.writer.max_attempts = 1
    with pytest.raises(SaseListenError) as error:
        author_script(
            article,
            "brief",
            cfg,
            _Writer("## The evidence\n\nThe team reported 17 decisions."),
        )
    assert error.value.code == ExitCode.SCRIPT_STRUCTURAL
    saved = article.directory / "brief_narration.md"
    assert saved.is_file()
    assert "17" in saved.read_text(encoding="utf-8")


def test_cache_invalidates_on_prompt_version_and_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sase_listen.writer.author as author

    article = _article(tmp_path)
    cfg = default_config()
    author_script(
        article, "full", cfg, _Writer("## The claim\n\nThe team made a decision.")
    )
    monkeypatch.setattr(author, "WRITER_PROMPT_VERSION", 2)
    second = author_script(
        article, "full", cfg, _Writer("## The claim\n\nThe team made another decision.")
    )
    assert "another decision" in second.text
    assert second.writer["prompt_version"] == 2
    third = author_script(
        article,
        "full",
        cfg,
        _Writer("## The claim\n\nThe team refreshed this decision."),
        refresh=True,
    )
    assert "refreshed this decision" in third.text


def test_full_target_for_short_article(tmp_path: Path) -> None:
    article = _article(tmp_path, words=300)
    result = author_script(
        article,
        "full",
        default_config(),
        _Writer("## The claim\n\nThe team made a decision."),
    )
    meta = yaml.safe_load(result.text.split("---\n", 2)[1])
    assert meta["target_minutes"] == 2


def test_gemini_request_shape_and_response_metadata() -> None:
    calls: list[dict[str, object]] = []

    class Models:
        def generate_content(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(
                text="## The claim\n\nThe team made a decision.",
                model_version="gemini-test-version",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=21, candidates_token_count=34
                ),
            )

    class Client:
        models = Models()

        def close(self) -> None:
            pass

    writer = GeminiWriter(
        "test-key",
        model="gemini-test",
        temperature=0.3,
        timeout_s=123,
        client_factory=lambda key, timeout: Client(),
    )
    reply = writer.write("system prompt", "article body")
    assert calls[0]["model"] == "gemini-test"
    assert calls[0]["contents"] == "article body"
    config = calls[0]["config"]
    assert config.system_instruction == "system prompt"
    assert config.temperature == 0.3
    assert config.max_output_tokens == 16384
    assert reply.model_version == "gemini-test-version"
    assert reply.input_tokens == 21
    assert reply.output_tokens == 34


def test_gemini_error_mapping() -> None:
    unauthorized = _map_api_error(SimpleNamespace(code=403))
    assert isinstance(unauthorized, CredentialsError)
    throttled = _map_api_error(SimpleNamespace(code=429))
    assert isinstance(throttled, TransientEngineError)
    unavailable = _map_api_error(SimpleNamespace(code=503))
    assert isinstance(unavailable, TransientEngineError)
