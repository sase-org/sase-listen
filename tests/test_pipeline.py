"""Pipeline phase tests: orchestration, gates, manifest, and library.

Owner: pipeline phase.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sase_listen.cache import ChunkCache
from sase_listen.cli.app import main
from sase_listen.config import DEFAULT_INTRO_TEMPLATE, default_config
from sase_listen.engines import (
    ContentBlockedError,
    CredentialsError,
    EngineLimits,
    PermanentEngineError,
    SynthesisRequest,
    SynthesisResult,
    ToneEngine,
)
from sase_listen.engines.tone import render_pcm
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.library import (
    atomic_commit,
    compute_episode_id,
    episode_lock,
    list_episode_ids,
    read_manifest,
    slugify,
)
from sase_listen.pipeline import (
    RenderEvents,
    RenderPlan,
    RenderRequest,
    RenderResult,
    build_intro_text,
    build_outro_text,
    check_lint,
    fit_unit,
    format_spoken_date,
    hard_failure,
    hard_split,
    pack_units,
    plan_to_json,
    render,
    soft_failure,
    split_sentences,
)
from sase_listen.script import ScriptMeta

TINY_SCRIPT = """\
---
narration: 1
title: Tiny Episode
kind: document
edition: verbatim
producer: agent
---

## First chapter

Hello world, this is a short spoken paragraph for the test.

## Second chapter

Another short paragraph with a few more words for good measure.
"""

BAD_SCRIPT = """\
---
narration: 1
title: Bad Episode
kind: document
edition: verbatim
producer: agent
---

Junk before any chapter.

## Only chapter

A paragraph here.
"""


@pytest.fixture
def isolated(tmp_path, monkeypatch) -> Path:
    """Point XDG dirs and the config at a temp tree (no user state touched)."""
    base = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(base / "missing-config.yml"))
    for name in (
        "SASE_LISTEN_GEMINI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "SASE_LISTEN_OPENAI_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return base


def _write(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _tone_request(text: str = "Hello world.") -> SynthesisRequest:
    return SynthesisRequest(text=text, model="", voice="", style="")


class CountingEngine(ToneEngine):
    """Tone engine that counts synthesis calls."""

    def __init__(self, concurrency: int = 4) -> None:
        self.calls = 0
        self._concurrency = concurrency

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.calls += 1
        return super().synthesize(request)

    def limits(self, model: str) -> EngineLimits:
        return EngineLimits(
            max_chars=20000, target_words=400, default_concurrency=self._concurrency
        )


class MarkerFailEngine(ToneEngine):
    """Fails permanently on a marker word; sequential for determinism."""

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if self.marker in request.text:
            raise PermanentEngineError("Simulated crash mid-synthesis.")
        return super().synthesize(request)

    def limits(self, model: str) -> EngineLimits:
        return EngineLimits(max_chars=20000, target_words=400, default_concurrency=1)


class BlipEngine(ToneEngine):
    """Returns a 0.05 s blip on the first call per text, good audio after."""

    def __init__(self) -> None:
        self.seen: set[str] = set()

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if request.text not in self.seen:
            self.seen.add(request.text)
            return SynthesisResult(pcm=render_pcm("blip")[:2400], sample_rate=24000)
        return super().synthesize(request)

    def limits(self, model: str) -> EngineLimits:
        return EngineLimits(max_chars=20000, target_words=400, default_concurrency=2)


class DoubledEngine(ToneEngine):
    """Returns 2x-duration audio: a soft (not hard) pace failure."""

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        pcm = render_pcm(request.text)
        return SynthesisResult(pcm=pcm + pcm, sample_rate=24000)

    def limits(self, model: str) -> EngineLimits:
        return EngineLimits(max_chars=20000, target_words=400, default_concurrency=2)


class SilentEngine(ToneEngine):
    """Always returns empty PCM: a persistent hard failure."""

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        return SynthesisResult(pcm=b"", sample_rate=24000)


class _CredentialsEngine(ToneEngine):
    """Raises credentials errors instead of synthesizing."""

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        raise CredentialsError("No key for tests.")


def _render_tiny(
    tmp_path: Path, engine: ToneEngine | None = None, **overrides: object
) -> RenderResult:
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    params: dict[str, object] = {"source": source, "narrator": "tone"}
    params.update(overrides)
    outcome = render(RenderRequest(**params), engine=engine)  # type: ignore[arg-type]
    assert isinstance(outcome, RenderResult)
    return outcome


# --- Pure planning units ---


def test_split_and_pack() -> None:
    assert split_sentences("Hello world. Bye now! Really?") == [
        "Hello world.",
        "Bye now!",
        "Really?",
    ]
    assert hard_split("aa bb cc", 5) == ["aa bb", "cc"]
    assert hard_split("supercalifragilistic", 5) == ["super", "calif", "ragil", "istic"]
    assert fit_unit("short", 100) == ["short"]
    packed = pack_units(
        ["one two", "three four", "five six"], target_words=4, max_chars=100
    )
    assert packed == ["one two\n\nthree four", "five six"]
    packed = pack_units(["aaa", "bbb"], target_words=100, max_chars=5)
    assert packed == ["aaa", "bbb"]


def test_intro_outro_and_dates() -> None:
    assert format_spoken_date("2026-09-14") == "September 14, 2026"
    assert format_spoken_date("not a date") == "not a date"
    assert format_spoken_date("2026-13-40") == "2026-13-40"
    meta = ScriptMeta(
        narration=1, title="T", kind="research", edition="full", date="2026-09-14"
    )
    intro = build_intro_text(meta, "Edition of {title}{kind_phrase}{date_phrase}.")
    assert intro == "Edition of T, SASE research from September 14, 2026."
    verbatim = ScriptMeta(narration=1, title="T", kind="document", edition="verbatim")
    assert build_intro_text(verbatim, DEFAULT_INTRO_TEMPLATE) == (
        "This is an AI-narrated reading of T."
    )
    assert build_outro_text(verbatim, "") == ""
    assert build_outro_text(verbatim, "End of {title}.") == "End of T."
    with pytest.raises(SaseListenError):
        build_intro_text(meta, "Broken {nope}.")
    with pytest.raises(SaseListenError):
        build_outro_text(meta, "Broken {nope}.")


def test_gate_predicates() -> None:
    assert hard_failure(b"", 150.0, 24000) == "empty or undecodable PCM"
    assert hard_failure(b"\x00", 150.0, 24000) == "empty or undecodable PCM"
    good = render_pcm("hello world")
    assert hard_failure(good, 150.0, 24000) is None
    assert hard_failure(good, 59.0, 24000) is not None
    assert hard_failure(good, 301.0, 24000) is not None
    assert hard_failure(good, 60.0, 24000) is None
    assert soft_failure(150.0, None) is None
    assert soft_failure(89.0, None) is not None
    assert soft_failure(241.0, None) is not None
    assert soft_failure(200.0, 100.0) is not None
    assert soft_failure(120.0, 100.0) is None
    assert soft_failure(60.0, 100.0) is not None


def test_lint_refusal(tmp_path: Path) -> None:
    source = _write(tmp_path, "bad.md", BAD_SCRIPT)
    with pytest.raises(SaseListenError) as exc_info:
        render(RenderRequest(source=source, narrator="tone"))
    assert exc_info.value.code == ExitCode.SCRIPT_STRUCTURAL
    warnings, _ = check_lint(TINY_SCRIPT, force=False)
    assert isinstance(warnings, list)


def test_slug_and_episode_id() -> None:
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("") == "episode"
    assert slugify("!!!") == "episode"
    assert len(slugify("x" * 200)) <= 60
    left = compute_episode_id("Title", "/abs/a.md")
    assert left == compute_episode_id("Title", "/abs/a.md")
    assert left != compute_episode_id("Title", "/abs/b.md")
    assert left != compute_episode_id("Other", "/abs/a.md")


# --- End to end with the tone engine ---


def test_render_tone_e2e(isolated: Path, tmp_path: Path) -> None:
    result = _render_tiny(tmp_path)
    assert result.title == "Tiny Episode"
    assert result.duration_s > 5.0
    assert result.size_bytes > 1000
    assert len(result.chapters) == 2
    assert result.chapters[0]["title"] == "First chapter"
    assert result.chapters[0]["start_s"] == 0.0
    assert result.chapters[1]["start_s"] > 1.0
    assert result.total_chunks == result.cached_chunks + result.synthesized_chunks
    assert result.synthesized_chunks == result.total_chunks
    assert result.cached_chunks == 0
    assert result.published is False
    assert abs(result.loudness_lufs - -16.0) <= 1.0

    library = Path(os.environ["XDG_DATA_HOME"]) / "sase-listen" / "library"
    episode = library / result.episode_id
    assert episode.is_dir()
    slug = "tiny-episode"
    mp3 = episode / f"{slug}.mp3"
    assert mp3.exists()
    assert (episode / "script.md").read_text(encoding="utf-8") == TINY_SCRIPT
    assert (episode / "cover.jpg").stat().st_size > 1000
    chapters_doc = json.loads((episode / "chapters.json").read_text(encoding="utf-8"))
    assert chapters_doc["version"] == "1.0"
    assert [c["title"] for c in chapters_doc["chapters"]] == [
        "First chapter",
        "Second chapter",
    ]
    manifest = json.loads((episode / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["episode_id"] == result.episode_id
    assert manifest["title"] == "Tiny Episode"
    assert set(manifest["source"]) == {"path", "sha256", "blob"}
    assert set(manifest["script"]) == {"sha256", "producer", "edition", "words"}
    assert set(manifest["narrator"]) == {
        "name",
        "engine",
        "model",
        "voice",
        "style_sha256",
    }
    assert manifest["narrator"]["name"] == "tone"
    assert set(manifest["audio"]) == {
        "file",
        "bytes",
        "duration_s",
        "bitrate_kbps",
        "loudness_lufs",
        "true_peak_db",
    }
    assert manifest["audio"]["file"] == f"{slug}.mp3"
    assert {c["chapter"] for c in manifest["chunks"]} == {
        "First chapter",
        "Second chapter",
    }
    assert set(manifest["chunks"][0]) == {
        "index",
        "chapter",
        "words",
        "cache_key",
        "duration_s",
        "attempts",
        "cached",
        "pieces",
    }
    assert {c["title"] for c in manifest["chapters"]} == {
        "First chapter",
        "Second chapter",
    }
    assert {g["name"] for g in manifest["gates"]} == {
        "mp3_decodes",
        "duration_matches",
        "chapters_match",
        "loudness_on_target",
    }
    assert all(g["ok"] for g in manifest["gates"])
    assert manifest["published"] is False

    from mutagen.id3 import ID3

    tag = ID3(str(mp3))
    chap_keys = sorted(k for k in tag if k.startswith("CHAP:"))
    assert len(chap_keys) == 2
    assert any(k.startswith("CTOC") for k in tag)


def test_render_tone_e2e_separate_tmp_filesystem(
    isolated: Path, tmp_path: Path, tmp_on_separate_fs: Path
) -> None:
    result = _render_tiny(tmp_path)
    assert isinstance(result, RenderResult)
    library = Path(os.environ["XDG_DATA_HOME"]) / "sase-listen" / "library"
    mp3s = list((library / result.episode_id).glob("*.mp3"))
    assert mp3s
    assert all(p.stat().st_size > 1000 for p in mp3s)


def test_render_golden_fixture_e2e(isolated: Path) -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "markdown"
        / "research_excerpt.narration.md"
    )
    outcome = render(RenderRequest(source=str(fixture), narrator="tone"))
    assert isinstance(outcome, RenderResult)
    assert outcome.title == "Commute audio rendering (excerpt)"
    assert len(outcome.chapters) == 3
    assert outcome.duration_s > 10.0
    assert outcome.synthesized_chunks == outcome.total_chunks


def test_render_is_cached_and_atomic(isolated: Path, tmp_path: Path) -> None:
    first = _render_tiny(tmp_path)
    engine = CountingEngine()
    params = {"source": str(tmp_path / "tiny_narration.md"), "narrator": "tone"}
    outcome = render(RenderRequest(**params), engine=engine)  # type: ignore[arg-type]
    assert isinstance(outcome, RenderResult)
    assert outcome.episode_id == first.episode_id
    assert engine.calls == 0
    assert outcome.cached_chunks == outcome.total_chunks
    assert outcome.synthesized_chunks == 0


def test_render_no_cache(isolated: Path, tmp_path: Path) -> None:
    _render_tiny(tmp_path)
    engine = CountingEngine()
    source = str(tmp_path / "tiny_narration.md")
    outcome = render(
        RenderRequest(source=source, narrator="tone", no_cache=True), engine=engine
    )
    assert isinstance(outcome, RenderResult)
    assert engine.calls == outcome.total_chunks


def test_render_resume_after_crash(isolated: Path, tmp_path: Path) -> None:
    script = TINY_SCRIPT.replace("good measure.", "good measure MELTDOWN.")
    source = _write(tmp_path, "crash_narration.md", script)
    with pytest.raises(SaseListenError) as exc_info:
        render(
            RenderRequest(source=source, narrator="tone"),
            engine=MarkerFailEngine("MELTDOWN"),
        )
    assert exc_info.value.code == ExitCode.SYNTHESIS_FAILED
    engine = CountingEngine()
    outcome = render(RenderRequest(source=source, narrator="tone"), engine=engine)
    assert isinstance(outcome, RenderResult)
    assert 0 < outcome.cached_chunks < outcome.total_chunks
    assert outcome.cached_chunks + outcome.synthesized_chunks == outcome.total_chunks


def test_hard_gate_resynthesis(isolated: Path, tmp_path: Path) -> None:
    result = _render_tiny(tmp_path, engine=BlipEngine())
    assert result.retried_chunks == result.total_chunks
    library = Path(os.environ["XDG_DATA_HOME"]) / "sase-listen" / "library"
    manifest = read_manifest(result.episode_id, library)
    attempts = manifest["chunks"]
    assert isinstance(attempts, list)
    assert len(attempts) == 4
    assert all(c["attempts"] == 2 for c in attempts if isinstance(c, dict))


def test_hard_gate_exit_five(isolated: Path, tmp_path: Path) -> None:
    source = _write(tmp_path, "silent_narration.md", TINY_SCRIPT)
    with pytest.raises(SaseListenError) as exc_info:
        render(RenderRequest(source=source, narrator="tone"), engine=SilentEngine())
    assert exc_info.value.code == ExitCode.QUALITY_GATE_FAILED
    assert "chunk" in str(exc_info.value)


def test_soft_gate_warns_and_keeps_best(isolated: Path, tmp_path: Path) -> None:
    result = _render_tiny(tmp_path, engine=DoubledEngine())
    assert result.retried_chunks == result.total_chunks
    assert any("re-synthesized once" in w for w in result.warnings)


def test_render_plain_markdown(isolated: Path, tmp_path: Path, capsys) -> None:
    source = _write(
        tmp_path,
        "notes.md",
        "# Field Notes\n\n## Arrival\n\nPlain observations in sentences.\n",
    )
    assert main(["render", source, "-n", "tone", "--dry-run", "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["title"] == "Field Notes"
    assert [c["title"] for c in payload["chapters"]] == ["Arrival"]
    assert payload["chunks"]["total"] == payload["chunks"]["synthesis_needed"]
    assert set(payload) == {
        "ok",
        "dry_run",
        "episode_id",
        "title",
        "source",
        "narrator",
        "edition",
        "producer",
        "script_path",
        "writer",
        "words",
        "estimated_duration_s",
        "estimated_cost_usd",
        "chunks",
        "chapters",
        "omissions",
        "warnings",
    }


def test_render_json_schema(isolated: Path, tmp_path: Path, capsys) -> None:
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    out_path = str(tmp_path / "episode.mp3")
    assert main(["render", source, "-n", "tone", "--json", "-o", out_path]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert set(payload) == {
        "ok",
        "episode_id",
        "title",
        "audio_path",
        "manifest_path",
        "duration_s",
        "size_bytes",
        "chapters",
        "narrator",
        "chunks",
        "loudness_lufs",
        "cost_usd_estimate",
        "published",
        "publish_queued",
        "publish_host",
        "script_path",
        "writer",
        "warnings",
    }
    assert set(payload["chunks"]) == {"total", "cached", "synthesized", "retried"}
    assert set(payload["narrator"]) == {"name", "engine", "model", "voice"}
    assert Path(out_path).exists()
    assert Path(payload["audio_path"]).exists()


def test_render_force_and_publish(
    isolated: Path, tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write(tmp_path, "bad.md", BAD_SCRIPT)
    assert main(["render", source, "-n", "tone", "--json"]) == 6
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload == {
        "ok": False,
        "error": {
            "code": 6,
            "message": payload["error"]["message"],
            "hint": payload["error"]["hint"],
        },
    }
    # --publish is real since the feed phase: configure the feed first.
    cfg_path = tmp_path / "feed-config.yml"
    cfg_path.write_text(
        "narrator: tone\n"
        "feed:\n"
        f"  dir: {tmp_path / 'feed'}\n"
        "  base_url: https://example.com:8443\n"
        "  token: pipeline-phase-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(cfg_path))
    assert main(["render", source, "-n", "tone", "--force", "--publish"]) == 0


def test_render_exit_codes(isolated: Path, tmp_path: Path, capsys) -> None:
    assert main(["render", str(tmp_path / "missing.md"), "-n", "tone"]) == 2
    assert main(["render", str(tmp_path / "missing.md"), "-n", "tone", "--json"]) == 2
    out = capsys.readouterr().out
    assert json.loads(out)["ok"] is False
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    assert main(["render", source, "-n", "nope"]) == 3
    assert main(["render", source, "-n", "gemini"]) == 3


def test_ref_resolution_with_fake_sase(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "sase"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "artifact" ] && [ "$2" = "read" ]'
        ' && [ "$3" = "research:202610/fake.md" ]; then\n'
        'printf "# Fake Report\\n\\n## Results\\n\\nPlain findings here.\\n";\n'
        "exit 0\nfi\n"
        'echo "ref not found" >&2\nexit 1\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert main(["render", "research:202610/fake.md", "-n", "tone", "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["title"] == "Fake Report"
    assert main(["render", "research:202610/gone.md", "-n", "tone"]) == 1


def test_ref_without_sase(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(SaseListenError) as exc_info:
        render(RenderRequest(source="research:202610/fake.md", narrator="tone"))
    assert exc_info.value.code == ExitCode.CONFIG


def test_cover_option(isolated: Path, tmp_path: Path) -> None:
    from PIL import Image

    cover = tmp_path / "cover.png"
    Image.new("RGB", (64, 64), (200, 30, 30)).save(cover)
    result = _render_tiny(tmp_path, cover=str(cover))
    assert result.duration_s > 0
    with pytest.raises(SaseListenError) as exc_info:
        _render_tiny(tmp_path, cover=str(tmp_path / "missing.png"))
    assert exc_info.value.code == ExitCode.USAGE


def test_plan_and_result_json_shapes(isolated: Path, tmp_path: Path) -> None:
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    outcome = render(RenderRequest(source=source, narrator="tone", dry_run=True))
    assert isinstance(outcome, RenderPlan)
    assert outcome.chunks
    assert outcome.chunks[0].kind == "intro"
    assert outcome.chunks[-1].kind == "outro"
    first_content = next(c for c in outcome.chunks if c.kind == "content")
    assert "First chapter" in first_content.text
    payload = plan_to_json(outcome)
    assert payload["chunks"]["total"] == len(outcome.chunks)


def test_library_helpers(isolated: Path, tmp_path: Path) -> None:
    root = tmp_path / "library"
    locks = tmp_path / "locks"
    payloads = {
        "episode.mp3": b"ID3fake",
        "script.md": b"# x",
        "cover.jpg": b"jpeg",
        "chapters.json": b"{}",
        "manifest.json": b'{"episode_id": "e1"}',
    }
    final = atomic_commit("e1", payloads, root)
    assert (final / "manifest.json").exists()
    assert (final / "episode.mp3").read_bytes() == b"ID3fake"
    assert list_episode_ids(root) == ["e1"]
    with pytest.raises(ValueError):
        atomic_commit("e2", {"script.md": b"x"}, root)
    with (
        episode_lock("e1", locks),
        pytest.raises(SaseListenError) as exc_info,
        episode_lock("e1", locks),
    ):
        pass
    assert exc_info.value.code == ExitCode.UNEXPECTED


def test_chunk_cache_roundtrip(isolated: Path) -> None:
    cache = ChunkCache(root=Path(os.environ["XDG_CACHE_HOME"]) / "chunks")
    key = "0" * 64
    assert cache.get(key) is None
    cache.put(key, render_pcm("hello"), sample_rate=24000, words=1)
    hit = cache.get(key)
    assert hit is not None
    assert hit.words == 1
    stats = cache.stats()
    assert stats.files >= 1
    assert cache.prune(max_gb=0)["removed"] >= 1


def test_default_config_tone_engine() -> None:
    config = default_config()
    assert "tone" in config.narrators
    assert config.audio.chunk_gap_s == 0.5
    assert config.audio.chapter_gap_s == 1.2
    assert config.audio.intro_gap_s == 0.9


def test_human_output_paths(isolated: Path, tmp_path: Path, capsys) -> None:
    from sase_listen.cli.progress import PlainProgress
    from sase_listen.engines.retry import RetryWait

    events = PlainProgress("notes.md")
    events.on_title("Notes")
    events.on_stage("synthesize")
    events.on_chunk_started(0, 2)
    events.on_chunk_finished(0, 2, cached=True)
    events.on_chunk_started(1, 2)
    events.on_retry_wait(
        "synthesize",
        1,
        RetryWait(attempt=1, max_retries=4, delay_s=14.0, reason="slow"),
    )
    events.on_chunk_finished(1, 2, cached=False)
    events.on_stage_done("synthesize", "1 synthesized · 1 cached · 0 retries")
    events.on_stage("gates")
    err = capsys.readouterr().err
    assert "chunk 2 of 2" in err and "retry 1 of 4 in 14s" in err
    assert "1 chunk cached" in err and "Synthesize" in err

    source = _write(
        tmp_path,
        "fence.md",
        "# Fence Notes\n\n## Code\n\n```python\nprint('hi')\n```\n\nSpoken words.\n",
    )
    assert main(["render", source, "-n", "tone", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Chapters:" in out and "Omissions" in out
    warned = TINY_SCRIPT.replace("good measure.", "good measure \u2248 exactly.")
    bad = _write(tmp_path, "badhuman.md", warned)
    assert main(["render", bad, "-n", "tone", "--dry-run"]) == 0
    assert "Warnings:" in capsys.readouterr().out
    assert main(["render", str(tmp_path / "missing.md"), "-n", "tone"]) == 2
    assert "hint:" in capsys.readouterr().err


def test_bad_config_file(isolated: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    import sase_listen.pipeline as pipeline_mod

    bad_cfg = tmp_path / "bad-config.yml"
    bad_cfg.write_text("nope: 1\n", encoding="utf-8")
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(bad_cfg))
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    assert main(["render", source, "-n", "tone", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert main(["render", source, "-n", "tone"]) == 3

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline_mod, "render", _boom)
    assert main(["render", source, "-n", "tone", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == 1
    assert main(["render", source, "-n", "tone"]) == 1


def test_manifest_file_helpers(tmp_path: Path) -> None:
    from sase_listen.manifest import dumps_manifest, read_manifest_file

    path = tmp_path / "manifest.json"
    path.write_bytes(dumps_manifest({"episode_id": "e1"}))
    assert read_manifest_file(path)["episode_id"] == "e1"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        read_manifest_file(path)
    assert list_episode_ids(tmp_path / "absent") == []


def test_read_manifest_not_a_mapping(tmp_path: Path) -> None:
    from sase_listen import library as library_mod

    episode = tmp_path / "library" / "e9"
    episode.mkdir(parents=True)
    (episode / "manifest.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError):
        library_mod.read_manifest("e9", tmp_path / "library")


def test_source_helpers_edge_cases() -> None:
    import sase_listen.pipeline as pipeline_mod

    assert pipeline_mod.looks_like_script("---\n: bad: [\n---\n\n## X\n\nY.\n") is False
    assert pipeline_mod.looks_like_script("---\n- just\n- a\n- list\n---\n") is False
    assert pipeline_mod.looks_like_script("no frontmatter\n\n## X\n\nY.\n") is False
    assert pack_units([], target_words=10, max_chars=10) == []
    made = pipeline_mod.SynthesizedChunk(
        planned=pipeline_mod.PlannedChunk(
            index=0,
            chapter_index=0,
            chapter="C",
            kind="intro",
            text="hi",
            words=1,
            cache_key="k",
        ),
        pcm=b"\x00\x00",
        sample_rate=0,
        attempts=0,
        cached=False,
    )
    made.measure()
    assert made.duration_s == 0.0
    assert made.wpm == 0.0


def test_check_lint_residue_note() -> None:
    script = TINY_SCRIPT.replace("Hello world,", "[Hello world](https://example.com),")
    _, residue = check_lint(script, force=False)
    assert residue and "R005" in residue[0]


def test_ref_script_passthrough(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindir = tmp_path / "bin2"
    bindir.mkdir()
    fake = bindir / "sase"
    fake.write_text(
        "#!/bin/sh\ncat <<'SCRIPT_EOF'\n" + TINY_SCRIPT + "SCRIPT_EOF\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    outcome = render(RenderRequest(source="research:202610/script.md", narrator="tone"))
    assert isinstance(outcome, RenderResult)
    assert outcome.title == "Tiny Episode"


def test_ref_unreadable_sase(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindir = tmp_path / "bin3"
    bindir.mkdir()
    (bindir / "sase").mkdir()  # a directory: exec fails with OSError
    monkeypatch.setenv("PATH", str(bindir))
    with pytest.raises(SaseListenError) as exc_info:
        render(RenderRequest(source="research:202610/fake.md", narrator="tone"))
    assert exc_info.value.code == ExitCode.UNEXPECTED


def test_engine_selection_edges() -> None:
    from sase_listen.engines import ResolvedNarrator
    from sase_listen.pipeline import default_engine, prepare, synthesize_one

    config = default_config()
    bogus = ResolvedNarrator(
        name="bogus",
        engine="bogus",
        model="m",
        voice="v",
        style="",
        speed=1.0,
        base_url="",
    )
    with pytest.raises(SaseListenError) as exc_info:
        default_engine(bogus, config)
    assert exc_info.value.code == ExitCode.CONFIG
    opened = prepare(
        RenderRequest(source="x", narrator="openai"), engine=CountingEngine()
    )
    assert opened.concurrency == 3
    assert opened.max_retries == 4
    with pytest.raises(SaseListenError) as cred_info:
        synthesize_one(_CredentialsEngine(), opened.narrator, "hi", max_retries=0)
    assert cred_info.value.code == ExitCode.CONFIG


def test_cover_frontmatter_and_sibling(isolated: Path, tmp_path: Path) -> None:
    from PIL import Image

    front = tmp_path / "front.png"
    Image.new("RGB", (32, 32), (30, 200, 30)).save(front)
    script = TINY_SCRIPT.replace("producer: agent", "producer: agent\ncover: front.png")
    front_source = _write(tmp_path, "coverfront_narration.md", script)
    fronted = render(RenderRequest(source=front_source, narrator="tone"))
    assert isinstance(fronted, RenderResult)
    sibling = tmp_path / "sibling_narration_infographic.png"
    Image.new("RGB", (32, 32), (30, 30, 200)).save(sibling)
    plain_source = _write(tmp_path, "sibling_narration.md", TINY_SCRIPT)
    outcome = render(RenderRequest(source=plain_source, narrator="tone"))
    assert isinstance(outcome, RenderResult)
    assert outcome.episode_id != fronted.episode_id


def test_no_chapters_guard(isolated: Path, tmp_path: Path) -> None:
    script = (
        "---\nnarration: 1\ntitle: Empty\nkind: document\n"
        "edition: verbatim\nproducer: agent\n---\n"
    )
    source = _write(tmp_path, "empty_narration.md", script)
    with pytest.raises(SaseListenError) as exc_info:
        render(RenderRequest(source=source, narrator="tone", force=True))
    assert exc_info.value.code == ExitCode.SCRIPT_STRUCTURAL


def test_episode_gate_undecodable(tmp_path: Path) -> None:
    from sase_listen.pipeline import run_episode_gates

    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio at all")
    with pytest.raises(SaseListenError) as exc_info:
        run_episode_gates(
            junk,
            expected_duration_s=10.0,
            chapter_titles=["A"],
            target_lufs=-16.0,
            loudness_lufs=-16.0,
        )
    assert exc_info.value.code == ExitCode.QUALITY_GATE_FAILED


def test_assemble_small_intro_gap(isolated: Path, tmp_path: Path) -> None:
    from sase_listen.pipeline import (
        assemble_episode,
        load_source,
        plan_request,
        prepare,
    )

    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    request = RenderRequest(source=source, narrator="tone")
    prepared = prepare(request)
    prepared.config.audio.intro_gap_s = 0.1
    plan = plan_request(request, prepared, load_source(source))
    assert len(plan.chunks) == 4
    from sase_listen.pipeline import SynthesizedChunk

    made = []
    for chunk in plan.chunks:
        item = SynthesizedChunk(
            planned=chunk,
            pcm=render_pcm(chunk.text),
            sample_rate=24000,
            attempts=1,
            cached=False,
        )
        item.measure()
        made.append(item)
    pcm, starts, rate = assemble_episode(made, plan, prepared.config)
    assert rate == 24000
    assert len(starts) == 2
    assert len(pcm) > 0


def test_splitter_edges() -> None:
    from sase_listen.pipeline import chapter_units

    assert hard_split("a bcdef", 5) == ["a", "bcdef"]
    assert hard_split("", 5) == [""]
    assert chapter_units("H", [], max_chars=100, target_words=100) == ["H"]
    assert chapter_units("H", [""], max_chars=100, target_words=100) == ["H"]
    paras = [" ".join(f"a{i}" for i in range(12)), " ".join(f"b{i}" for i in range(12))]
    units = chapter_units("H", paras, max_chars=200, target_words=10)
    assert len(units) == 2
    assert units[0].startswith("H\n\n")
    tiny = [" ".join(f"word{i}" for i in range(50))]
    tiny_units = chapter_units("H", tiny, max_chars=60, target_words=10)
    assert all(len(u) <= 60 for u in tiny_units)


def test_vanishing_paragraph_skipped(isolated: Path, tmp_path: Path) -> None:
    script = TINY_SCRIPT.replace(
        "Hello world, this is a short spoken paragraph for the test.",
        "<!-- stage direction -->",
    )
    source = _write(tmp_path, "vanish_narration.md", script)
    outcome = render(RenderRequest(source=source, narrator="tone", dry_run=True))
    assert isinstance(outcome, RenderPlan)
    first = next(c for c in outcome.chunks if c.kind == "content")
    assert "First chapter" in first.text


def test_hard_failure_silence_edges() -> None:
    import numpy as np

    from sase_listen.pipeline import hard_failure

    good = render_pcm("hello world")
    assert hard_failure(good, 150.0, 0) is None
    silence = np.zeros(5 * 24000, dtype=np.int16).tobytes()
    pcm = good + silence + good
    reason = hard_failure(pcm, 150.0, 24000)
    assert reason is not None and "silence" in reason


def test_gates_without_median(isolated: Path, tmp_path: Path) -> None:
    from sase_listen.cache import ChunkCache
    from sase_listen.pipeline import (
        SynthesizedChunk,
        load_source,
        plan_request,
        prepare,
        run_chunk_gates,
    )

    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    request = RenderRequest(source=source, narrator="tone")
    prepared = prepare(request)
    plan = plan_request(request, prepared, load_source(source))
    pair = []
    for chunk in plan.chunks[:2]:
        item = SynthesizedChunk(
            planned=chunk,
            pcm=render_pcm(chunk.text),
            sample_rate=24000,
            attempts=1,
            cached=False,
        )
        item.measure()
        pair.append(item)
    cache = ChunkCache(root=tmp_path / "chunks")
    final, warnings, retried = run_chunk_gates(
        pair,
        ToneEngine(),
        prepared.narrator,
        cache,
        max_retries=0,
        use_cache=True,
        events=RenderEvents(),
    )
    assert len(final) == 2
    assert warnings == []
    assert retried == 0


def test_soft_gate_no_cache(isolated: Path, tmp_path: Path) -> None:
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    outcome = render(
        RenderRequest(source=source, narrator="tone", no_cache=True),
        engine=DoubledEngine(),
    )
    assert isinstance(outcome, RenderResult)
    assert outcome.retried_chunks == outcome.total_chunks


def test_cover_fallbacks(tmp_path: Path) -> None:
    from sase_listen.pipeline import load_source, resolve_cover_bytes

    script = TINY_SCRIPT.replace("producer: agent", "producer: agent\ncover: gone.png")
    source = _write(tmp_path, "covermiss_narration.md", script)
    loaded = load_source(source)
    assert (
        resolve_cover_bytes(
            loaded, cover_option="", title="T", kind="document", date_text=""
        )
        != b""
    )
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not an image")
    with pytest.raises(SaseListenError) as exc_info:
        resolve_cover_bytes(
            loaded, cover_option=str(junk), title="T", kind="document", date_text=""
        )
    assert exc_info.value.code == ExitCode.USAGE


def test_output_bare_filename(
    isolated: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    assert main(["render", source, "-n", "tone", "--json", "-o", "bare.mp3"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["ok"] is True
    assert (tmp_path / "bare.mp3").exists()


def test_synthesize_runtime_error() -> None:
    from sase_listen.pipeline import prepare, synthesize_one

    class _RuntimeEngine(ToneEngine):
        def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
            raise RuntimeError("boom")

    opened = prepare(RenderRequest(source="x", narrator="tone"), engine=ToneEngine())
    with pytest.raises(SaseListenError) as exc_info:
        synthesize_one(_RuntimeEngine(), opened.narrator, "hi", max_retries=0)
    assert exc_info.value.code == ExitCode.SYNTHESIS_FAILED


def test_entry_points() -> None:
    import sase_listen.ui as ui
    from sase_listen import feed
    from sase_listen.cli import main as cli_main

    assert ui.GLYPH_OK == "\u2713"
    assert ui.GLYPH_AUDIO == "\u266a"
    assert cli_main([]) == 2
    # The feed phase implemented the module, replacing the scaffold stub.
    assert not hasattr(feed, "not_implemented")
    for name in (
        "resolve_token",
        "subscribe_url",
        "init_feed",
        "publish_episode",
        "unpublish_episode",
        "rebuild_feed",
        "feed_status",
        "build_feed_xml",
    ):
        assert callable(getattr(feed, name)), f"missing feed API {name}"


def test_generated_cover_conflicts_are_usage_errors(
    isolated: Path, tmp_path: Path
) -> None:
    from sase_listen.pipeline import load_source, resolve_cover_bytes

    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    loaded = load_source(source)
    with pytest.raises(SaseListenError) as exc_info:
        resolve_cover_bytes(
            loaded,
            cover_option=str(tmp_path / "x.png"),
            title="T",
            kind="document",
            date_text="",
            generated_cover=True,
        )
    assert exc_info.value.code == ExitCode.USAGE
    with pytest.raises(SaseListenError) as dry_info:
        render(
            RenderRequest(
                source=source,
                narrator="tone",
                cover=str(tmp_path / "x.png"),
                generated_cover=True,
                dry_run=True,
            )
        )
    assert dry_info.value.code == ExitCode.USAGE
    with pytest.raises(SaseListenError) as missing_info:
        render(
            RenderRequest(
                source=str(tmp_path / "missing.md"),
                narrator="tone",
                cover="x.png",
                generated_cover=True,
                dry_run=True,
            )
        )
    assert missing_info.value.code == ExitCode.USAGE


def test_generated_cover_ignores_frontmatter_and_sibling(tmp_path: Path) -> None:
    from PIL import Image

    from sase_listen.audio.cover import resolve_cover
    from sase_listen.pipeline import (
        format_spoken_date,
        load_source,
        resolve_cover_bytes,
    )

    front = tmp_path / "front.png"
    Image.new("RGB", (32, 32), (30, 200, 30)).save(front)
    script = TINY_SCRIPT.replace("producer: agent", "producer: agent\ncover: front.png")
    source = _write(tmp_path, "covergen_narration.md", script)
    sibling = tmp_path / "covergen_narration_infographic.png"
    Image.new("RGB", (32, 32), (30, 30, 200)).save(sibling)
    loaded = load_source(source)
    meta = loaded.script.meta
    date_text = format_spoken_date(meta.date) if meta.date.strip() else ""
    expected = resolve_cover(
        None, meta.title, kind=meta.kind, date_text=date_text, site=meta.site
    )
    actual = resolve_cover_bytes(
        loaded,
        cover_option="",
        title=meta.title,
        kind=meta.kind,
        date_text=date_text,
        generated_cover=True,
    )
    assert actual == expected

    missing_script = TINY_SCRIPT.replace(
        "producer: agent", "producer: agent\ncover: gone.png"
    )
    missing_source = _write(tmp_path, "covermiss2_narration.md", missing_script)
    missing_loaded = load_source(missing_source)
    missing_meta = missing_loaded.script.meta
    missing_date = (
        format_spoken_date(missing_meta.date) if missing_meta.date.strip() else ""
    )
    missing_expected = resolve_cover(
        None,
        missing_meta.title,
        kind=missing_meta.kind,
        date_text=missing_date,
        site=missing_meta.site,
    )
    assert (
        resolve_cover_bytes(
            missing_loaded,
            cover_option="",
            title=missing_meta.title,
            kind=missing_meta.kind,
            date_text=missing_date,
            generated_cover=True,
        )
        == missing_expected
    )

    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not an image")
    junk_script = TINY_SCRIPT.replace(
        "producer: agent", "producer: agent\ncover: junk.png"
    )
    junk_source = _write(tmp_path, "coverjunk_narration.md", junk_script)
    junk_loaded = load_source(junk_source)
    junk_meta = junk_loaded.script.meta
    junk_date = format_spoken_date(junk_meta.date) if junk_meta.date.strip() else ""
    junk_expected = resolve_cover(
        None,
        junk_meta.title,
        kind=junk_meta.kind,
        date_text=junk_date,
        site=junk_meta.site,
    )
    assert (
        resolve_cover_bytes(
            junk_loaded,
            cover_option="",
            title=junk_meta.title,
            kind=junk_meta.kind,
            date_text=junk_date,
            generated_cover=True,
        )
        == junk_expected
    )

    front.unlink()
    sibling.unlink()
    reloaded = load_source(source)
    reloaded_meta = reloaded.script.meta
    reloaded_date = (
        format_spoken_date(reloaded_meta.date) if reloaded_meta.date.strip() else ""
    )
    assert (
        resolve_cover_bytes(
            reloaded,
            cover_option="",
            title=reloaded_meta.title,
            kind=reloaded_meta.kind,
            date_text=reloaded_date,
            generated_cover=True,
        )
        == expected
    )


def test_generated_cover_tone_render_embeds_card(
    isolated: Path, tmp_path: Path
) -> None:
    import os

    from mutagen.id3 import ID3
    from PIL import Image

    from sase_listen.audio.cover import resolve_cover
    from sase_listen.pipeline import format_spoken_date, load_source

    info = tmp_path / "info.png"
    Image.new("RGB", (32, 32), (200, 30, 30)).save(info)
    sibling = tmp_path / "reuse_narration_infographic.png"
    Image.new("RGB", (32, 32), (30, 30, 200)).save(sibling)
    script = TINY_SCRIPT.replace("producer: agent", "producer: agent\ncover: info.png")
    source = _write(tmp_path, "reuse_narration.md", script)
    before = Path(source).read_text(encoding="utf-8")
    loaded = load_source(source)
    meta = loaded.script.meta
    date_text = format_spoken_date(meta.date) if meta.date.strip() else ""
    expected = resolve_cover(
        None, meta.title, kind=meta.kind, date_text=date_text, site=meta.site
    )
    outcome = render(
        RenderRequest(source=source, narrator="tone", generated_cover=True)
    )
    assert isinstance(outcome, RenderResult)
    assert outcome.published is False
    library = Path(os.environ["XDG_DATA_HOME"]) / "sase-listen" / "library"
    episode = library / outcome.episode_id
    cover_bytes = (episode / "cover.jpg").read_bytes()
    assert cover_bytes == expected
    tag = ID3(str(Path(outcome.audio_path)))
    assert tag["APIC:Cover"].data == expected  # type: ignore[attr-defined]
    assert Path(source).read_text(encoding="utf-8") == before


# --- Content-blocked split-and-retry ---


class PolicyEngine(ToneEngine):
    """Fails when the text contains both markers (context-dependent block)."""

    def __init__(self, marker_a: str = "MELODY", marker_b: str = "DRUMS") -> None:
        self.marker_a = marker_a
        self.marker_b = marker_b
        self.seen: list[str] = []

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.seen.append(request.text)
        if self.marker_a in request.text and self.marker_b in request.text:
            raise ContentBlockedError(
                "Gemini's policy filter blocked the text (HTTP 400 content_blocked)"
            )
        return super().synthesize(request)

    def limits(self, model: str) -> EngineLimits:
        return EngineLimits(max_chars=20000, target_words=400, default_concurrency=1)


def _split_script(title: str, body: str) -> str:
    return (
        "---\nnarration: 1\n"
        f"title: {title}\nkind: document\nedition: verbatim\nproducer: agent\n---\n"
        f"\n## Chapter One\n\n{body}\n"
    )


_PARA_BODY = (
    "First paragraph carries MELODY here.\n\nSecond paragraph carries DRUMS here."
)
_SENT_BODY = "First sentence carries MELODY here. Second sentence carries DRUMS here."
_TERM_BODY = "This sentence carries MELODY and DRUMS together in one breath."


def test_blocked_recovers_across_paragraphs(isolated: Path, tmp_path: Path) -> None:
    from sase_listen.library import read_manifest

    source = _write(
        tmp_path, "split_para_narration.md", _split_script("Split Para", _PARA_BODY)
    )
    engine = PolicyEngine()
    outcome = render(RenderRequest(source=source, narrator="tone"), engine=engine)
    assert isinstance(outcome, RenderResult)
    # Each paragraph was sent separately after the blocked whole-chunk call.
    assert any(text == "First paragraph carries MELODY here." for text in engine.seen)
    assert any(text == "Second paragraph carries DRUMS here." for text in engine.seen)
    split_warnings = [w for w in outcome.warnings if "content_blocked" in w]
    assert len(split_warnings) == 1
    assert "Chapter One" in split_warnings[0]
    assert "pieces." in split_warnings[0]
    library = Path(os.environ["XDG_DATA_HOME"]) / "sase-listen" / "library"
    manifest = read_manifest(outcome.episode_id, library)
    assert isinstance(manifest["chunks"], list)
    pieces = [c["pieces"] for c in manifest["chunks"]]
    assert max(pieces) > 1


def test_blocked_recovers_across_sentences(isolated: Path, tmp_path: Path) -> None:
    source = _write(
        tmp_path, "split_sent_narration.md", _split_script("Split Sent", _SENT_BODY)
    )
    engine = PolicyEngine()
    outcome = render(RenderRequest(source=source, narrator="tone"), engine=engine)
    assert isinstance(outcome, RenderResult)
    assert any("content_blocked" in w for w in outcome.warnings)


def test_blocked_cached_on_rerun(isolated: Path, tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "split_cache_narration.md",
        _split_script("Split Cache", _PARA_BODY),
    )
    first = render(RenderRequest(source=source, narrator="tone"), engine=PolicyEngine())
    assert isinstance(first, RenderResult)
    assert any("content_blocked" in w for w in first.warnings)
    counting = CountingEngine()
    second = render(RenderRequest(source=source, narrator="tone"), engine=counting)
    assert isinstance(second, RenderResult)
    assert counting.calls == 0
    assert any("content_blocked" in w for w in second.warnings)


def test_blocked_stitch_shape() -> None:
    import numpy as np

    from sase_listen.audio import trim_silence
    from sase_listen.config import default_config
    from sase_listen.engines.tone import render_pcm
    from sase_listen.pipeline import prepare, synthesize_one

    opened = prepare(RenderRequest(source="x", narrator="tone"), engine=ToneEngine())
    pcm, rate, _attempts, pieces = synthesize_one(
        PolicyEngine(), opened.narrator, _PARA_BODY, max_retries=0
    )
    assert pieces == 2
    assert rate == 24000
    first_raw = render_pcm("First paragraph carries MELODY here.")
    second_raw = render_pcm("Second paragraph carries DRUMS here.")
    first = trim_silence(
        np.frombuffer(first_raw, dtype=np.int16).copy(),
        24000,
    )
    second = trim_silence(
        np.frombuffer(second_raw, dtype=np.int16).copy(),
        24000,
    )
    gap_s = default_config().audio.chunk_gap_s
    expected_s = (len(first) + len(second)) / rate + gap_s
    actual_s = len(np.frombuffer(pcm, dtype=np.int16)) / rate
    assert abs(actual_s - expected_s) < 0.05
    assert hard_failure(pcm, 150.0, rate) is None or "pace" in str(
        hard_failure(pcm, 150.0, rate)
    )


def test_blocked_terminal_failure(isolated: Path, tmp_path: Path) -> None:
    source = _write(
        tmp_path, "split_term_narration.md", _split_script("Split Term", _TERM_BODY)
    )
    with pytest.raises(SaseListenError) as exc_info:
        render(RenderRequest(source=source, narrator="tone"), engine=PolicyEngine())
    err = exc_info.value
    assert err.code == ExitCode.SYNTHESIS_FAILED
    message = str(err)
    assert "Chapter One" in message
    assert "MELODY" in message or "melody" in message.lower()
    assert "Chunk" in message and "/" in message
    assert "rephrase" in err.hint.lower()
    assert str(tmp_path) in err.hint or "split_term_narration.md" in err.hint
    assert "Re-run to resume" not in err.hint


def test_permanent_error_message_hygiene(isolated: Path, tmp_path: Path) -> None:
    script = TINY_SCRIPT.replace("good measure.", "good measure MELTDOWN.")
    source = _write(tmp_path, "hygiene_narration.md", script)
    with pytest.raises(SaseListenError) as exc_info:
        render(
            RenderRequest(source=source, narrator="tone"),
            engine=MarkerFailEngine("MELTDOWN"),
        )
    err = exc_info.value
    assert err.code == ExitCode.SYNTHESIS_FAILED
    assert "after retries" not in str(err)
    assert ".." not in str(err)
