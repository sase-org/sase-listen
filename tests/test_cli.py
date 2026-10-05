"""CLI registry tests. Owner: scaffold phase."""

from __future__ import annotations

from sase_listen.cli.app import build_parser, main


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
