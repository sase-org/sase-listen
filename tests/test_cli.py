"""CLI registry tests. Owner: scaffold phase."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sase_listen.cli.app import build_parser, main

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


@pytest.fixture
def isolated(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Point XDG dirs and the config at a temp tree (no user state touched)."""
    base = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(base / "missing-config.yml"))
    return base


def test_version_flag(capsys) -> None:  # type: ignore[no-untyped-def]
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    else:  # pragma: no cover
        raise AssertionError("expected SystemExit")


def test_no_command_returns_usage() -> None:
    assert main([]) == 2


def test_all_commands_registered() -> None:
    parser = build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert actions
    choices = set(actions[0].choices)
    for name in (
        "render",
        "script",
        "lint",
        "guide",
        "audition",
        "ls",
        "doctor",
        "cache",
        "config",
        "feed",
        "publish",
        "unpublish",
    ):
        assert name in choices, f"missing command {name}"


def test_stubs_exit_nonzero(capsys) -> None:  # type: ignore[no-untyped-def]
    # render is implemented by the pipeline phase; lint/script/guide are
    # implemented by the script phase (missing files are usage errors).
    assert main(["render", "notes.md"]) == 2
    assert main(["lint", "script.md"]) == 2
    assert main(["script", "notes.md"]) == 2


def test_render_generated_cover_option_forms() -> None:
    import pytest

    parser = build_parser()
    assert parser.parse_args(["render", "src.md"]).generated_cover is False
    assert (
        parser.parse_args(["render", "src.md", "--generated-cover"]).generated_cover
        is True
    )
    assert parser.parse_args(["render", "src.md", "-g"]).generated_cover is True
    with pytest.raises(SystemExit) as long_conflict:
        parser.parse_args(["render", "src.md", "--cover", "a.png", "--generated-cover"])
    assert long_conflict.value.code == 2
    with pytest.raises(SystemExit) as short_conflict:
        parser.parse_args(["render", "src.md", "--cover", "a.png", "-g"])
    assert short_conflict.value.code == 2


def test_render_generated_cover_reaches_request(
    tmp_path,
    monkeypatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    from pathlib import Path

    import sase_listen.cli.render as render_mod

    script = (
        "---\nnarration: 1\ntitle: Tiny Episode\nkind: document\n"
        "edition: verbatim\nproducer: agent\n---\n"
        "\n## First chapter\n\nHello world, this is a short spoken paragraph.\n"
        "\n## Second chapter\n\nAnother short paragraph with a few more words.\n"
    )
    source = tmp_path / "tiny_narration.md"
    source.write_text(script, encoding="utf-8")
    base = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(base / "missing-config.yml"))

    captured: dict[str, object] = {}
    original = render_mod.render

    def _capture(request, **kwargs):  # type: ignore[no-untyped-def]
        captured["generated_cover"] = request.generated_cover
        captured["cover"] = request.cover
        return original(request, **kwargs)

    monkeypatch.setattr(render_mod, "render", _capture)
    assert (
        main(["render", str(source), "-n", "tone", "--generated-cover", "--dry-run"])
        == 0
    )
    assert captured["generated_cover"] is True
    assert captured["cover"] == ""
    captured.clear()
    assert main(["render", str(source), "-n", "tone", "-g", "--dry-run"]) == 0
    assert captured["generated_cover"] is True
    assert Path(str(source)).read_text(encoding="utf-8") == script


def _write(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_render_plain_progress_summary(
    isolated: Path,
    tmp_path: Path,
    monkeypatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    assert (
        main(["render", source, "-n", "tone", "--no-publish", "--progress", "plain"])
        == 0
    )
    captured = capsys.readouterr()
    assert "✓ Synthesize" in captured.err
    assert "Ready in" in captured.out
    assert "\x1b" not in captured.err
    library = isolated / "data" / "sase-listen" / "library"
    mp3s = sorted(library.rglob("*.mp3"))
    assert len(mp3s) == 1
    unbroken = [line for line in captured.out.splitlines() if str(mp3s[0]) in line]
    assert len(unbroken) == 1


def test_render_live_progress_final_frame(
    isolated: Path,
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    assert (
        main(["render", source, "-n", "tone", "--no-publish", "--progress", "live"])
        == 0
    )
    captured = capsys.readouterr()
    for row in ("Synthesize", "Quality gates", "Master audio", "Save episode"):
        assert row in captured.err
    assert "Ready in" in captured.out
    assert "Tiny Episode" not in captured.out.split("Ready in")[0]


def test_render_json_stdout_only(
    isolated: Path,
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    assert main(["render", source, "-n", "tone", "--no-publish", "--json"]) == 0
    captured = capsys.readouterr()
    assert len(captured.out.strip().splitlines()) == 1
    assert json.loads(captured.out)["ok"] is True
    assert captured.err == ""


def test_script_url_progress_plain(
    tmp_path: Path,
    monkeypatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    base = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(base / "state"))
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(base / "missing-config.yml"))

    from sase_listen.writer.base import WriterReply

    prose = (
        "A detailed synthetic paragraph explains the engineering choices and "
        "their effects for readers who need to understand the complete example. "
        "It includes enough meaningful words to pass the article length check. "
    )
    html = (
        "<!doctype html><html><head><title>Plain Story</title></head>"
        "<body><article><h1>Plain Story</h1>"
        f"<h2>First Section</h2><p>{prose * 4}</p>"
        f"<h2>Second Section</h2><p>{prose * 4}</p>"
        f"<h3>Third Section</h3><p>{prose * 4}</p>"
        "</article></body></html>"
    ).encode()
    saved = tmp_path / "browser.html"
    saved.write_bytes(html)

    class FakeWriter:
        def write(self, system: str, user: str) -> WriterReply:  # type: ignore[no-untyped-def]
            paragraph = (
                "The team carefully describes the engineering process and results. "
            )
            return WriterReply(
                "## The question\n\n"
                + paragraph * 25
                + "\n\n## The evidence\n\n"
                + paragraph * 25,
                "stub-brief-v1",
                50,
                75,
            )

    import sase_listen.writer

    monkeypatch.setattr(
        sase_listen.writer, "create_writer", lambda cfg, **kwargs: FakeWriter()
    )
    url = "https://example.test/plain-story"
    assert main(["script", url, "--html", str(saved), "--progress", "plain"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("---\n")
    assert "Fetch article" in captured.err


def _write_feed_config(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cfg_path = tmp_path / "xdg" / "config" / "listen.yml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        "narrator: tone\n"
        "feed:\n"
        f"  dir: {tmp_path / 'feed'}\n"
        "  base_url: https://example.com:8443\n"
        "  token: test-token-abc\n"
        "  title: Test Feed\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SASE_LISTEN_CONFIG", str(cfg_path))


def test_publish_activity_plain_and_json_silent(
    isolated: Path,
    tmp_path: Path,
    monkeypatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    _write_feed_config(tmp_path, monkeypatch)
    source = _write(tmp_path, "tiny_narration.md", TINY_SCRIPT)
    assert main(["render", source, "--no-publish", "--progress", "off"]) == 0
    capsys.readouterr()
    assert main(["publish", "--latest"]) == 0
    err = capsys.readouterr().err
    assert "Publishing" in err
    assert main(["publish", "--latest", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ok"] is True
    assert captured.err == ""
