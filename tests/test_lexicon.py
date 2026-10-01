"""Lexicon tests. Owner: script phase."""

from __future__ import annotations

from sase_listen import lexicon as lex_mod
from sase_listen.lexicon import Lexicon, load_default, load_merged


def test_default_entries() -> None:
    entries = load_default().entries
    assert entries["chezmoi"] == "shay-mwah"
    assert entries["uv"] == "you-vee"
    assert entries["PyPI"] == "pie-pee-eye"
    assert entries["mypy"] == "my-pie"
    assert entries["LUFS"] == "loofs"
    assert "SASE" not in entries


def test_apply_replaces_whole_words_case_sensitive() -> None:
    lex = Lexicon({"uv": "you-vee", "PyPI": "pie-pee-eye"})
    assert lex.apply("Install with uv from PyPI.") == (
        "Install with you-vee from pie-pee-eye."
    )
    # Case differs: untouched.
    assert lex.apply("UV upgrade") == "UV upgrade"
    # Substring: untouched.
    assert lex.apply("uvula") == "uvula"


def test_user_file_merges_over_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    user = tmp_path / "lexicon.yml"
    user.write_text("uv: custom-you\nKubernetes: kube\n", encoding="utf-8")
    merged = load_merged(user)
    assert merged.entries["uv"] == "custom-you"
    assert merged.entries["Kubernetes"] == "kube"
    assert merged.entries["mypy"] == "my-pie"


def test_missing_user_file_falls_back_to_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    merged = load_merged(tmp_path / "absent.yml")
    assert merged.entries == load_default().entries


def test_sha256_stable_and_sensitive() -> None:
    assert load_default().sha256() == load_default().sha256()
    assert Lexicon({"a": "b"}).sha256() != Lexicon({"a": "c"}).sha256()


def test_package_apply_uses_default() -> None:
    assert "you-vee" in lex_mod.apply("Use uv today.")
