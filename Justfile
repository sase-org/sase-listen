# sase-listen task runner

repo_dir := justfile_directory()
venv_bin := repo_dir / ".venv" / "bin"

default:
    @just --list

[group('install')]
[doc("Sync this checkout's .venv for tests and lint")]
install-venv:
    uv sync --locked --all-groups

# Private alias kept for one release; remove after the next release.
[private]
install:
    @echo '`just install` is now `just install-venv`' >&2
    @just install-venv

fmt:
    {{ venv_bin }}/ruff format src/ tests/
    {{ venv_bin }}/ruff check --fix src/ tests/

lint:
    {{ venv_bin }}/ruff check src/ tests/
    {{ venv_bin }}/ruff format --check src/ tests/
    {{ venv_bin }}/mypy --strict src
    {{ venv_bin }}/codespell

test *args:
    {{ venv_bin }}/pytest {{ args }}

test-live *args:
    # Marker `live` hits real network APIs; skipped without SASE_LISTEN_LIVE=1.
    @if [ "${SASE_LISTEN_LIVE:-}" != "1" ]; then echo "Skipping live tests (set SASE_LISTEN_LIVE=1 to run)."; exit 0; fi
    {{ venv_bin }}/pytest -m live {{ args }}

golden *args:
    {{ venv_bin }}/python tools/golden.py {{ args }}

docs:
    {{ venv_bin }}/mkdocs serve

docs-build:
    {{ venv_bin }}/mkdocs build --strict

build:
    {{ venv_bin }}/python -m build

demo *args:
    {{ venv_bin }}/sase-listen render --help {{ args }}

# Agents must run guarded tools through `sase tool run` (sase docs/tool.md).
# The guard is the first dependency, ahead of any setup, so a refusal costs
# milliseconds rather than a dependency sync.
_require-tool-run name:
    @tools/require_tool_run {{ name }}

check: (_require-tool-run "check") lint test

clean:
    rm -rf build/ dist/ site/ *.egg-info src/*.egg-info .mypy_cache/ .ruff_cache/ .pytest_cache/ htmlcov/ .coverage
