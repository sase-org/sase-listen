"""sase_commands adapter contract and prog-parity tests. Owner: cli phase."""

from __future__ import annotations

import argparse
import ast
import sys
from importlib import metadata
from pathlib import Path

import pytest

from sase_listen import invocation
from sase_listen.cli.app import build_parser, main

STANDALONE = "sase-listen"
PLUGIN = "sase listen"

ALL_COMMANDS = (
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
)

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


def _normalize(text: str) -> str:
    return text.replace(PLUGIN, STANDALONE)


def _canon(text: str) -> str:
    """Normalize prog name and wrapping (help reflows around the prog)."""
    collapsed = " ".join(text.split())
    return collapsed.replace("sase- listen", STANDALONE).replace(PLUGIN, STANDALONE)


def _run(
    prog: str, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    try:
        code = main(argv, prog=prog)
    except SystemExit as exc:
        assert isinstance(exc.code, int)
        code = exc.code
    captured = capsys.readouterr()
    return code, _normalize(captured.out), _normalize(captured.err)


def test_entry_point_declared() -> None:
    points = metadata.entry_points(group="sase_commands")
    listen = [point for point in points if point.name == "listen"]
    assert listen, "sase_commands entry point 'listen' is not installed"
    assert listen[0].value == "sase_listen.sase_command"
    dist = listen[0].dist
    assert dist is not None and "sase-listen" in dist.name


def test_adapter_shape() -> None:
    import sase_listen.sase_command as adapter

    assert adapter.SASE_COMMAND_API == 1
    assert adapter.SUMMARY.strip()
    assert callable(adapter.main)
    assert callable(adapter.build_parser)


def test_adapter_imports_only_stdlib_at_top() -> None:
    import sase_listen.sase_command as adapter

    source = Path(adapter.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    stdlib = set(sys.stdlib_module_names)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in stdlib, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            top = (node.module or "").split(".")[0]
            assert top != "sase" and not top.startswith("sase_"), node.module
            assert top in stdlib, node.module


def test_adapter_help_names_plugin_prog(capsys: pytest.CaptureFixture[str]) -> None:
    import sase_listen.sase_command as adapter

    try:
        adapter.main(["--help"], prog=PLUGIN)
    except SystemExit as exc:
        assert exc.code == 0
    else:  # pragma: no cover
        raise AssertionError("expected SystemExit")
    out = capsys.readouterr().out
    assert "usage: sase listen" in out.lower()


def test_parser_prog_flows_to_epilogs() -> None:
    parser = build_parser(prog=PLUGIN)
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert sub.choices["render"].epilog.startswith(f"Example: {PLUGIN} render")
    assert "sase-listen" not in (sub.choices["render"].epilog or "")
    default = build_parser()
    assert default.prog == STANDALONE


def test_bare_invocation_parity(
    isolated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(STANDALONE, [], capsys) == _run(PLUGIN, [], capsys)
    assert _run(STANDALONE, [], capsys)[0] == 2


def test_nested_help_parity(capsys: pytest.CaptureFixture[str]) -> None:
    for command in ALL_COMMANDS:
        first = _run(STANDALONE, [command, "-h"], capsys)
        second = _run(PLUGIN, [command, "-h"], capsys)
        assert first[0] == second[0] == 0
        assert _canon(first[1]) == _canon(second[1])
        assert first[2] == second[2] == ""


def test_version_and_unknown_flag_parity(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(STANDALONE, ["--version"], capsys) == _run(
        PLUGIN, ["--version"], capsys
    )
    assert _run(STANDALONE, ["render", "--bogus-flag"], capsys) == _run(
        PLUGIN, ["render", "--bogus-flag"], capsys
    )


def test_config_json_parity(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(STANDALONE, ["config", "--json"], capsys) == _run(
        PLUGIN, ["config", "--json"], capsys
    )


def test_lint_error_parity(
    isolated: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = tmp_path / "bad.md"
    bad.write_text("no frontmatter here\n", encoding="utf-8")
    first = _run(STANDALONE, ["lint", str(bad)], capsys)
    second = _run(PLUGIN, ["lint", str(bad)], capsys)
    assert first == second
    assert first[0] != 0
    missing = _run(STANDALONE, ["lint", str(tmp_path / "nope.md")], capsys)
    assert missing == _run(PLUGIN, ["lint", str(tmp_path / "nope.md")], capsys)
    assert missing[0] == 2


def test_offline_tone_render_parity(
    isolated: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = []
    for prog in (STANDALONE, PLUGIN):
        tag = "standalone" if prog == STANDALONE else "plugin"
        run_xdg = tmp_path / f"xdg-{tag}"
        for name in ("config", "data", "cache", "state"):
            monkeypatch.setenv(f"XDG_{name.upper()}_HOME", str(run_xdg / name))
        monkeypatch.setenv("SASE_LISTEN_CONFIG", str(run_xdg / "missing.yml"))
        source = tmp_path / f"tiny-{tag}.md"
        source.write_text(TINY_SCRIPT, encoding="utf-8")
        try:
            code = main(
                ["render", str(source), "-n", "tone", "--no-publish", "--json"],
                prog=prog,
            )
        except SystemExit as exc:
            assert isinstance(exc.code, int)
            code = exc.code
        captured = capsys.readouterr()
        payload_out = _normalize(captured.out)
        results.append((code, payload_out, _normalize(captured.err)))
    assert results[0][0] == results[1][0] == 0
    import json

    first_payload = json.loads(results[0][1])
    second_payload = json.loads(results[1][1])
    assert first_payload["ok"] is True and second_payload["ok"] is True
    assert set(first_payload) == set(second_payload)


def test_broken_pipe_parity(
    isolated: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import sase_listen.cli.guide as guide_mod

    def _raise(args: argparse.Namespace) -> int:
        raise BrokenPipeError("closed")

    monkeypatch.setattr(guide_mod, "run", _raise)
    codes = [main(["guide"], prog=prog) for prog in (STANDALONE, PLUGIN)]
    assert codes[0] == codes[1] != 0


def _subparser(name: str) -> argparse.ArgumentParser:
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sub.choices[name]


def _completion(action: argparse.Action) -> str | None:
    return getattr(action, "sase_completion", None)  # type: ignore[no-any-return]


def test_path_completion_marks() -> None:
    render = _subparser("render")
    by_dest = {a.dest: a for a in render._actions}
    assert _completion(by_dest["source"]) == "path"
    assert _completion(by_dest["cover"]) == "path"
    assert _completion(by_dest["output"]) == "path"
    assert _completion(by_dest["html"]) == "path"

    script = _subparser("script")
    script_by_dest = {a.dest: a for a in script._actions}
    assert _completion(script_by_dest["source"]) == "path"

    lint = _subparser("lint")
    lint_by_dest = {a.dest: a for a in lint._actions}
    assert _completion(lint_by_dest["script"]) == "path"
    assert _completion(lint_by_dest["source"]) == "path"

    audition = _subparser("audition")
    audition_by_dest = {a.dest: a for a in audition._actions}
    assert _completion(audition_by_dest["text"]) == "path"


def test_invocation_defaults_to_standalone() -> None:
    invocation.set_display_prog(STANDALONE)
    assert invocation.display_prog() == STANDALONE
    assert invocation.command("render") == "sase-listen render"
