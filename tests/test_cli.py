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
    # render stays a pipeline-phase stub; lint/script/guide are implemented
    # by the script phase (missing files are usage errors).
    assert main(["render", "notes.md"]) == 1
    assert main(["lint", "script.md"]) == 2
    assert main(["script", "notes.md"]) == 2
