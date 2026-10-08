# Contributing

## Setup

```bash
just install-venv    # uv sync --locked --all-groups
```

## Checks

```bash
sase tool run check    # guarded: lint + test (agents must use this, not bare just check)
just lint              # ruff check + format --check + mypy --strict + codespell
just test              # pytest
just test-live         # live network tests (needs SASE_LISTEN_LIVE=1)
just docs-build        # mkdocs build --strict
```

## Conventions

- **Conventional Commit** PR titles (`feat:`, `fix:`, …) — release-please cuts
  releases from them, and `pr-title.yml` enforces the shape.
- **One module per CLI command** in `src/sase_listen/cli/` behind the
  `app.py` registry; see `AGENTS.md` for the architecture map.
- **Docs live with the code.** Every behavior change updates its docs page
  and this site builds with `mkdocs build --strict` — no dead links, no
  unlisted pages.
- **No `sase` imports.** The only `sase_*` entry point is `sase_commands`;
  SASE integration lives in `sase-research-artifacts` and `sase-telegram`
  and is reached only by shelling out at runtime.
- **Agent instructions** for working in this repo are in `AGENTS.md`.
