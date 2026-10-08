# sase-listen - Agent Instructions

## Overview

CLI that turns Markdown into chaptered, loudness-normalized MP3 audio
editions. Two installs, one codebase: the standalone `sase-listen` binary
(`uv tool install sase-listen`) and the `sase listen` command plugin
(`sase plugin install listen`). Never imports `sase` or `sase-core`:
SASE integration lives in sase-research-artifacts (`#research/audio`)
and sase-telegram (`sendAudio`). Rendering audio is not shared domain behavior
another frontend must match (Rust-boundary litmus test).

## Build & Run

```bash
just install    # uv sync --locked --all-groups
just lint       # ruff check + format --check + mypy --strict + codespell
just fmt        # ruff format + fix
just test       # pytest
just test-live  # live network tests (needs SASE_LISTEN_LIVE=1)
just golden     # regenerate golden fixtures (script phase)
just docs       # mkdocs serve
just docs-build # mkdocs build --strict
just build      # uv build
just demo       # tone-engine render help
just check      # lint + test (guarded)
```

## Architecture map

- `src/sase_listen/cli/` — argparse registry (`app.py`) + one module per command.
  Each command stub exits 1 until its owner phase lands it.
- `src/sase_listen/config.py`, `paths.py`, `errors.py` — scaffold-owned shared
  foundation: XDG resolution, typed defaults for every config section, env
  overrides (`SASE_LISTEN_*`), unknown-key errors with did-you-mean, origin
  tracking, exit-code enum.
- `src/sase_listen/script/`, `normalize/`, `lexicon.py` — script phase.
- `src/sase_listen/web/` — local article fetching, extraction (`extract.py`
  for HTML, `pdf.py` for PDFs), outline repair, and per-source storage.
- `src/sase_listen/audio/` — audio phase (pure library, no engine/CLI knowledge).
- `src/sase_listen/engines/`, `cache.py`, `pricing.py` — engines phase.
- `src/sase_listen/pipeline.py`, `library.py`, `manifest.py` — pipeline phase.
- `src/sase_listen/cli/` rich experience + `audition/ls/doctor/cache/config` — cli phase.
- `src/sase_listen/events.py` — render progress event protocol (leaf module).
- `src/sase_listen/cli/progress.py` — live checklist, plain lines, guards.
- `src/sase_listen/feed.py` + feed/publish/unpublish — feed phase.
- `src/sase_listen/feedhost.py` — SSH transport, receive, outbox (feed-host phase).
- `src/sase_listen/ui.py` — one accent color, one glyph set.

## Parallel-phase rules

`script`, `audio`, and `engines` run in parallel against this repo.

- Scaffold declares every runtime dependency and commits `uv.lock`.
- Stubs, test dirs, CLI stubs, and docs pages are pre-created so phases edit
  disjoint files.
- Do not edit `pyproject.toml`, `uv.lock`, `mkdocs.yml` nav, or another phase's
  modules. If unavoidable, keep it minimal and note it in the phase notes.
- Every phase updates its own docs page and tests.

## sase tool runs

Agents run `sase tool run check` here, not bare `just check`: `check` is
guarded and a raw agent invocation is refused with the wrapped and bypass
forms. To run raw on purpose: `SASE_TOOL_BYPASS='<why>' just check`.

## No-sase-import rule

Never import `sase` or `sase_core_rs` here. The only `sase_*` entry point is
`sase_commands` (`listen = "sase_listen.sase_command"` in `pyproject.toml`),
and the repo carries the `sase--plugin` topic. The adapter module
(`src/sase_listen/sase_command.py`) imports only stdlib at module top;
user-facing command suggestions route through `sase_listen/invocation.py`
so they name the invoked binary (`sase-listen` or `sase listen`). Ref
resolution shells out to `sase artifact read` at runtime only.
