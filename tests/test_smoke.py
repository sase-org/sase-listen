"""Offline smoke: version + doctor --json. Owner: scaffold phase."""

from __future__ import annotations

import json

from sase_listen.cli.app import main


def test_doctor_json_offline(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["doctor", "--json"]) in (0, 3)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload["ok"], bool)
    assert isinstance(payload["checks"], list)
