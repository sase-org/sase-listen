# CLI reference

Every command accepts `--help`. Commands marked **(stub)** are not implemented
yet: they exit 1 with a "not implemented yet" message. Agent-friendly commands
accept `--json` and emit exactly one JSON object on stdout.

Exit codes: `0` ok, `1` unexpected, `2` usage, `3` config/credentials,
`4` synthesis failed after retries, `5` quality gate failed, `6` structural
lint errors (`render` refuses unless `--force`).

## `render` — Markdown in, MP3 out

```bash
sase-listen render SOURCE [-o OUT.mp3] [-n NARRATOR] [--voice VOICE]
  [--cover IMG] [--dry-run] [-e {brief,full,verbatim}] [--html FILE] [--refresh]
  [--publish | --no-publish] [--no-cache] [--force] [--json]
```

`SOURCE` is a narration script, a plain Markdown file (normalized
automatically), or a `kind:path` artifact ref (fetched through audited
`sase artifact read`), or an http(s) article URL. URL rendering defaults to the
AI-written `brief` edition; choose `full` for an adaptation or `verbatim` for a
deterministic article-text reading. `--html FILE` uses saved browser HTML, and
`--refresh` fetches and writes it again. `--dry-run` writes the article script
if needed, prints the chunk plan, and stops.
`--publish` / `--no-publish` override the `feed.auto_publish` config.
See [Reliability](reliability.md) for gates, cache, and manifests.

## `script` — normalize Markdown to a script

```bash
sase-listen script SOURCE [-o notes_narration.md] [-e {brief,full,verbatim}]
  [--html FILE] [--refresh] [--json]
```

For Markdown files, produces an `edition: verbatim`, `producer: deterministic`
script plus an omissions report. For article URLs, defaults to an AI-written
brief script; `--edition full` writes a full adaptation, and `verbatim` selects
deterministic normalization. `SOURCE` can be a Markdown file or an http(s)
article URL. For URL behavior and cached source storage, see [Web articles](web-articles.md)
and [Narration scripts](narration-scripts.md).

## `lint` — validate a script

```bash
sase-listen lint episode_narration.md [--source report.md] [--strict] [--json]
```

Exits 1 on errors, or on warnings with `--strict`. `--source` adds the
number-fidelity check: every number in the script must appear in the source.

## `guide` — print the authoring rules

```bash
sase-listen guide [--edition {brief,full}]
```

Prints the packaged rules hand-written scripts must follow (brief by
default; pass `--edition full` for the full-length edition). The rules ship
in the package so they never drift from the code that enforces them.

## `audition` — compare voices **(stub)**

```bash
sase-listen audition [--voices Charon,Kore] [-n NARRATOR] [--text FILE] [--json]
```

Intended: render the sample passage once per voice for comparison. Not
implemented yet (owner: cli phase follow-up).

## `ls` — list episodes **(stub)**

```bash
sase-listen ls [EPISODE] [--json]
```

Intended: list library episodes, or show one episode's manifest. Not
implemented yet (owner: cli phase follow-up). Meanwhile, episodes live under
`$XDG_DATA_HOME/sase-listen/library/<slug>-<hash>/` with `manifest.json`.

## `doctor` — check the setup

```bash
sase-listen doctor [--online] [--json]
```

Checks config load, ffmpeg resolution, credential presence, writable
directories, and feed configuration. `--online` is intended to add a one-word
live synthesis check; **it is a stub today** and reports
`FAIL: credentials (online check not implemented yet)`.

## `cache` — inspect the chunk cache **(stub)**

```bash
sase-listen cache [prune] [--all] [--older-than 30d] [--json]
```

Intended: show cache stats and prune entries. Not implemented yet (owner: cli
phase follow-up). The cache itself works — `render` reads and writes it; only
the inspection command is missing. `--no-cache` on `render` bypasses it.

## `config` — show or start configuration

```bash
sase-listen config [--json]
sase-listen config init     # write a starter file
sase-listen config path     # print the config path
```

Output annotates every value with its origin (`default`, `env`, `file`).
Secrets never appear. See [Configuration](configuration.md).

## `feed`, `publish`, `unpublish` — the podcast feed

```bash
sase-listen feed [init|prune|rebuild|receive] [--base-url URL] [--json]
  [--print] [--qr] [--show-url]
sase-listen publish EPISODE|--latest|--pending [--json] [--show-url]
sase-listen unpublish EPISODE [--json]
```

`EPISODE` is an episode id, an episode MP3 path, or `--latest`.
`--pending` flushes the retry outbox. `feed receive EPISODE_ID --json`
is the internal host-only transport (stdin tar); do not call it by
hand. See [Podcast feed](podcast-feed.md) and
[Multi-machine publish](multi-machine.md).

## Non-TTY and `NO_COLOR` behavior

Progress rendering degrades gracefully: without a TTY, or with `NO_COLOR`
set, output is plain text with no ANSI escapes. `--json` always emits exactly
one JSON object regardless of TTY state, for agent consumption.
