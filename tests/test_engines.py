"""Engines phase tests: adapters, retry, cache, pricing, secrets, narrators.

Owner: engines phase.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from sase_listen.cache import ChunkCache, cache_key
from sase_listen.config import default_config
from sase_listen.engines import (
    CredentialsError,
    PermanentEngineError,
    SynthesisRequest,
    ToneEngine,
    TransientEngineError,
    create_engine,
    resolve_api_key,
    resolve_narrator,
    synthesize_with_retry,
)
from sase_listen.engines.gemini import GeminiEngine, build_interaction_body
from sase_listen.engines.openai import OpenAIEngine
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.pricing import estimate, usd_per_minute


def _request(**overrides: object) -> SynthesisRequest:
    base: dict[str, object] = {
        "text": "Hello world.",
        "model": "gemini-3.8-flash-tts",
        "voice": "Charon",
        "style": "Calm technical narrator.",
    }
    base.update(overrides)
    return SynthesisRequest(**base)  # type: ignore[arg-type]


# --- Gemini adapter ---


class _FakeInteractions:
    def __init__(self, record: dict, response: object) -> None:
        self._record = record
        self._response = response

    def create(self, **kwargs: object) -> object:
        self._record.update(kwargs)
        return self._response


def _wav_bytes(frames: bytes = b"\x01\x02" * 500, rate: int = 24000) -> bytes:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return buffer.getvalue()


def _gemini_interaction(payload: bytes | str = b"") -> object:
    data = payload if isinstance(payload, str) else base64.b64encode(payload).decode()
    return SimpleNamespace(status="completed", output_audio=SimpleNamespace(data=data))


def _gemini_engine(
    record: dict, response: object, key: str = "test-key"
) -> GeminiEngine:
    def factory(api_key: str) -> object:
        return SimpleNamespace(
            api_key_seen=api_key,
            interactions=_FakeInteractions(record, response),
        )

    return GeminiEngine(key, client_factory=factory)


def test_gemini_request_shape_snapshot() -> None:
    record: dict = {}
    engine = _gemini_engine(record, _gemini_interaction(_wav_bytes()))
    engine.synthesize(_request())
    assert record["model"] == "gemini-3.8-flash-tts"
    assert record["response_format"] == {"type": "audio"}
    (turn,) = record["input"]
    assert turn["type"] == "user_input"
    (content,) = turn["content"]
    assert content["type"] == "text"
    assert content["text"] == "Hello world."
    # Style travels in the speech_metadata annotation, never in the transcript.
    assert "Calm technical narrator." not in content["text"]
    assert content["annotations"] == [
        {"type": "speech_metadata", "style": "Calm technical narrator."}
    ]
    speech_config = record["generation_config"]["speech_config"]
    assert speech_config == [{"voice": "Charon"}]


def test_gemini_build_body_empty_style_has_no_annotations() -> None:
    body = build_interaction_body(_request(style=""))
    (content,) = body["input"][0]["content"]
    assert "annotations" not in content


def test_gemini_decodes_wav_container() -> None:
    record: dict = {}
    engine = _gemini_engine(
        record, _gemini_interaction(_wav_bytes(b"\x03\x04" * 100, rate=16000))
    )
    result = engine.synthesize(_request())
    assert result.sample_rate == 16000
    assert result.pcm == b"\x03\x04" * 100


def test_gemini_empty_audio_is_transient() -> None:
    record: dict = {}
    with pytest.raises(TransientEngineError):
        _gemini_engine(
            record, SimpleNamespace(status="completed", output_audio=None)
        ).synthesize(_request())


def test_gemini_error_taxonomy() -> None:
    from google.genai import errors

    record: dict = {}
    interactions = _FakeInteractions(record, _gemini_interaction())

    def factory(api_key: str) -> object:
        return SimpleNamespace(interactions=interactions)

    engine = GeminiEngine("test-key", client_factory=factory)
    for code, kind in (
        (400, PermanentEngineError),
        (404, PermanentEngineError),
        (401, CredentialsError),
        (403, CredentialsError),
        (429, TransientEngineError),
        (500, TransientEngineError),
        (503, TransientEngineError),
    ):

        def boom(*args: object, code: int = code, **kwargs: object) -> object:
            raise errors.APIError(code, {"error": {"message": "x"}})

        interactions.create = boom  # type: ignore[method-assign]
        with pytest.raises(kind):
            engine.synthesize(_request())


def test_gemini_empty_text_is_permanent() -> None:
    record: dict = {}
    with pytest.raises(PermanentEngineError):
        _gemini_engine(record, _gemini_interaction(_wav_bytes())).synthesize(
            _request(text="  ")
        )


def test_gemini_missing_key() -> None:
    with pytest.raises(CredentialsError):
        GeminiEngine("  ")


def test_gemini_limits() -> None:
    limits = GeminiEngine("k").limits("gemini-3.8-flash-tts")
    assert limits.target_words == 400
    assert limits.max_chars > 0
    assert limits.default_concurrency >= 1


# --- OpenAI-compatible adapter ---


def _openai_engine(handler: object, **kwargs: object) -> tuple[OpenAIEngine, list]:
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)  # type: ignore[operator]

    engine = OpenAIEngine(
        "test-key",
        transport=httpx.MockTransport(_handler),
        **kwargs,  # type: ignore[arg-type]
    )
    return engine, captured


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"\x05\x06" * 24000)


def test_openai_request_shape_snapshot() -> None:
    engine, captured = _openai_engine(_ok)
    result = engine.synthesize(
        _request(model="gpt-4o-mini-tts-2025-12-15", voice="marin")
    )
    assert result.sample_rate == 24000
    assert result.pcm == b"\x05\x06" * 24000
    (sent,) = captured
    assert sent.url.path.endswith("/audio/speech")
    body = json.loads(sent.content.decode("utf-8"))
    assert body == {
        "model": "gpt-4o-mini-tts-2025-12-15",
        "input": "Hello world.",
        "voice": "marin",
        "response_format": "pcm",
        "instructions": "Calm technical narrator.",
    }


def test_openai_omits_empty_style_and_default_speed() -> None:
    engine, captured = _openai_engine(_ok)
    engine.synthesize(_request(style="", speed=1.0))
    body = json.loads(captured[0].content.decode("utf-8"))
    assert "instructions" not in body
    assert "speed" not in body


def test_openai_includes_speed_override() -> None:
    engine, captured = _openai_engine(_ok)
    engine.synthesize(_request(style="", speed=1.25))
    body = json.loads(captured[0].content.decode("utf-8"))
    assert body["speed"] == 1.25


def test_openai_status_taxonomy() -> None:
    for status, kind in (
        (400, PermanentEngineError),
        (422, PermanentEngineError),
        (401, CredentialsError),
        (403, CredentialsError),
        (429, TransientEngineError),
        (500, TransientEngineError),
    ):

        def _handler(request: httpx.Request, status: int = status) -> httpx.Response:
            headers = {"retry-after": "9"} if status == 429 else {}
            return httpx.Response(status, headers=headers, text="nope")

        engine, _ = _openai_engine(_handler)
        with pytest.raises(kind):
            engine.synthesize(_request())
    # Retry-After is surfaced for backoff.
    engine, _ = _openai_engine(
        lambda request: httpx.Response(429, headers={"retry-after": "9"})
    )
    try:
        engine.synthesize(_request())
    except TransientEngineError as exc:
        assert exc.retry_after == 9.0
    else:  # pragma: no cover
        raise AssertionError("expected TransientEngineError")


def test_openai_empty_audio_is_transient() -> None:
    engine, _ = _openai_engine(lambda request: httpx.Response(200, content=b""))
    with pytest.raises(TransientEngineError):
        engine.synthesize(_request())


def test_openai_limits_default_and_override() -> None:
    engine = OpenAIEngine("k")
    assert engine.limits("gpt-4o-mini-tts").max_chars == 3500
    assert engine.limits("kokoro", max_chars=1000).max_chars == 1000


# --- Retry ---


def test_retry_succeeds_after_transients() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def operation() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientEngineError("flaky")
        return "ok"

    assert (
        synthesize_with_retry(
            operation,
            max_retries=4,
            sleep=sleeps.append,
            rand=lambda lo, hi: hi,
        )
        == "ok"
    )
    assert calls["n"] == 3
    assert len(sleeps) == 2
    assert all(s >= 0.0 for s in sleeps)


def test_retry_exhausts_and_raises() -> None:
    sleeps: list[float] = []

    def operation() -> str:
        raise TransientEngineError("always")

    with pytest.raises(TransientEngineError):
        synthesize_with_retry(
            operation, max_retries=2, sleep=sleeps.append, rand=lambda lo, hi: 0.0
        )
    assert len(sleeps) == 2


@pytest.mark.parametrize(
    "error", [PermanentEngineError("bad"), CredentialsError("no key")]
)
def test_retry_never_retries_permanent_or_credentials(error: Exception) -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def operation() -> str:
        calls["n"] += 1
        raise error

    with pytest.raises(type(error)):
        synthesize_with_retry(operation, max_retries=3, sleep=sleeps.append)
    assert calls["n"] == 1
    assert sleeps == []


def test_retry_honors_retry_after_floor() -> None:
    sleeps: list[float] = []

    def operation() -> str:
        raise TransientEngineError("slow down", retry_after=30.0)

    with pytest.raises(TransientEngineError):
        synthesize_with_retry(
            operation, max_retries=1, sleep=sleeps.append, rand=lambda lo, hi: 0.0
        )
    assert sleeps == [30.0]


# --- Tone engine ---


def test_tone_is_deterministic_and_speech_paced() -> None:
    engine = ToneEngine()
    text = " ".join(f"word{i}" for i in range(150))
    first = engine.synthesize(_request(text=text, model="", voice=""))
    second = engine.synthesize(_request(text=text, model="", voice=""))
    assert first.pcm == second.pcm
    assert first.sample_rate == 24000
    duration_s = len(first.pcm) / (2 * first.sample_rate)
    wpm = 150 / (duration_s / 60.0)
    assert 100.0 < wpm <= 160.0


def test_tone_sentence_pause_is_longer() -> None:
    engine = ToneEngine()
    plain = engine.synthesize(_request(text="one two three", model="", voice=""))
    stopped = engine.synthesize(_request(text="one two three.", model="", voice=""))
    assert len(stopped.pcm) > len(plain.pcm)


def test_tone_empty_text_is_permanent() -> None:
    with pytest.raises(PermanentEngineError):
        ToneEngine().synthesize(_request(text=" ", model="", voice=""))


# --- Cache ---


def _cache(tmp_path) -> ChunkCache:  # type: ignore[no-untyped-def]
    return ChunkCache(root=tmp_path / "chunks", max_gb=2.0)


def test_cache_key_stable_and_content_addressed() -> None:
    kwargs: dict[str, object] = {
        "engine": "gemini",
        "model": "gemini-3.8-flash-tts",
        "voice": "Charon",
        "style": "calm",
        "speed": 1.0,
        "sample_rate": 24000,
        "text": "Hello.",
    }
    assert cache_key(**kwargs) == cache_key(**kwargs)  # type: ignore[arg-type]
    altered = dict(kwargs, text="Hello!")
    assert cache_key(**altered) != cache_key(**kwargs)  # type: ignore[arg-type]


def test_cache_put_get_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = _cache(tmp_path)
    key = cache_key(
        engine="tone",
        model="",
        voice="",
        style="",
        speed=1.0,
        sample_rate=24000,
        text="hi",
    )
    assert cache.get(key) is None
    pcm = ToneEngine().synthesize(_request(text="hi", model="", voice="")).pcm
    cache.put(key, pcm, sample_rate=24000, words=1, usage={"engine": "tone"})
    hit = cache.get(key)
    assert hit is not None
    assert hit.pcm == pcm
    assert hit.sample_rate == 24000
    assert hit.words == 1
    assert hit.usage == {"engine": "tone"}
    assert hit.duration_s > 0


def test_cache_hit_refreshes_mtime(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = _cache(tmp_path)
    key = cache_key(
        engine="tone",
        model="",
        voice="",
        style="",
        speed=1.0,
        sample_rate=24000,
        text="stale",
    )
    cache.put(key, b"\x01\x02" * 100, sample_rate=24000, words=1)
    wav = tmp_path / "chunks" / key[:2] / f"{key}.wav"
    old = time.time() - 100.0
    os.utime(wav, (old, old))
    assert cache.get(key) is not None
    assert wav.stat().st_mtime > old


def test_cache_lru_evicts_oldest_first(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = ChunkCache(root=tmp_path / "chunks", max_gb=0.00001)
    keys = []
    for i in range(3):
        key = cache_key(
            engine="tone",
            model="",
            voice="",
            style="",
            speed=1.0,
            sample_rate=24000,
            text=f"entry {i}",
        )
        cache.put(key, b"\x01\x02" * 5000, sample_rate=24000, words=2)
        keys.append(key)
    report = cache.prune()
    assert report["removed"] >= 1
    assert cache.get(keys[0]) is None
    assert cache.stats().files < 3


def test_cache_corrupt_entry_is_miss(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = _cache(tmp_path)
    key = cache_key(
        engine="tone",
        model="",
        voice="",
        style="",
        speed=1.0,
        sample_rate=24000,
        text="broken",
    )
    directory = tmp_path / "chunks" / key[:2]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{key}.wav").write_bytes(b"not a wav")
    assert cache.get(key) is None


# --- Pricing ---


def test_pricing_dated_rates() -> None:
    assert usd_per_minute("gemini-3.8-flash-tts", date(2026, 6, 1)) == 0.0135
    assert usd_per_minute("gemini-3.8-flash-tts", date(2027, 1, 1)) == 0.027
    assert usd_per_minute("gemini-3.8-flash-lite-tts", date(2027, 6, 1)) == 0.018
    assert usd_per_minute("gpt-4o-mini-tts-2025-12-15", date(2026, 6, 1)) == 0.015
    assert usd_per_minute("tone") == 0.0
    assert usd_per_minute("kokoro") == 0.0


def test_pricing_estimate_math() -> None:
    assert estimate("gemini-3.8-flash-tts", 600.0, date(2026, 6, 1)) == pytest.approx(
        0.135
    )


# --- Secrets ---


def test_secrets_env_precedence() -> None:
    env = {"FIRST": "  ", "SECOND": "second-key", "THIRD": "third-key"}
    assert (
        resolve_api_key(
            engine="gemini",
            env_names=["FIRST", "SECOND", "THIRD"],
            env=env,
        )
        == "second-key"
    )


def test_secrets_command_fallback() -> None:
    key = resolve_api_key(
        engine="gemini",
        env_names=["MISSING_KEY_NAME"],
        api_key_command=f"{sys.executable} -c \"print('cmd-key')\"",
        env={},
    )
    assert key == "cmd-key"


def test_secrets_missing_never_leaks() -> None:
    secret = "super-secret-value"
    try:
        resolve_api_key(engine="gemini", env_names=["ABSENT_KEY"], env={})
    except CredentialsError as exc:
        assert secret not in str(exc)
        assert "ABSENT_KEY" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected CredentialsError")
    # A resolved secret never surfaces in engine reprs either.
    assert secret not in repr(GeminiEngine(secret))
    assert secret not in repr(OpenAIEngine(secret))


def test_secrets_failing_command() -> None:
    with pytest.raises(CredentialsError):
        resolve_api_key(
            engine="openai",
            env_names=["ABSENT_KEY"],
            api_key_command=f"{sys.executable} -c import sys; sys.exit(3)",
            env={},
        )


# --- Narrators ---


def test_resolve_builtin_gemini_narrator() -> None:
    resolved = resolve_narrator("gemini", default_config())
    assert resolved.engine == "gemini"
    assert resolved.model == "gemini-3.8-flash-tts"
    assert resolved.voice == "Charon"
    assert resolved.style != ""


def test_resolve_voice_override_only() -> None:
    resolved = resolve_narrator("openai", default_config(), voice_override="verse")
    assert resolved.voice == "verse"
    assert resolved.model == "gpt-4o-mini-tts-2025-12-15"


def test_resolve_unknown_narrator_lists_known() -> None:
    with pytest.raises(SaseListenError) as excinfo:
        resolve_narrator("nope", default_config())
    assert excinfo.value.code == ExitCode.CONFIG
    assert "gemini" in str(excinfo.value)


def test_create_engine_factory() -> None:
    assert isinstance(create_engine("tone"), ToneEngine)
    assert isinstance(create_engine("gemini", api_key="k"), GeminiEngine)
    assert isinstance(create_engine("openai", api_key="k"), OpenAIEngine)
    with pytest.raises(ValueError):
        create_engine("nope")


# --- Live verification (one real Gemini call; needs SASE_LISTEN_LIVE=1) ---


@pytest.mark.live
def test_live_gemini_two_sentences() -> None:
    if os.environ.get("SASE_LISTEN_LIVE") != "1":
        pytest.skip("needs SASE_LISTEN_LIVE=1")
    api_key = os.environ.get("SASE_LISTEN_GEMINI_API_KEY", "")
    if not api_key:
        pytest.skip("needs SASE_LISTEN_GEMINI_API_KEY")
    engine = GeminiEngine(api_key)
    result = engine.synthesize(
        SynthesisRequest(
            text="Hello from sase-listen. This is the engines phase live check.",
            model="gemini-3.8-flash-tts",
            voice="Charon",
            style="Calm, clear technical-briefing narrator.",
        )
    )
    assert len(result.pcm) > 1000
    assert result.sample_rate in (8000, 16000, 22050, 24000, 44100, 48000)
