# sase-listen

[![CI](https://github.com/sase-org/sase-listen/actions/workflows/ci.yml/badge.svg)](https://github.com/sase-org/sase-listen/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/sase-listen.svg)](https://pypi.org/project/sase-listen/)
[![Python](https://img.shields.io/pypi/pyversions/sase-listen.svg)](https://pypi.org/project/sase-listen/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://sase-org.github.io/sase-listen/)

Turn Markdown into chaptered, loudness-normalized MP3 audio editions — narrated
by Gemini TTS, built for listening to SASE research on a commute or walk.

```bash
uv tool install sase-listen
sase-listen doctor
sase-listen render report_narration.md -o episode.mp3
```

Each episode is a mono 24 kHz, 64 kb/s MP3 at −16 LUFS with one ID3 chapter
per `##` heading, an embedded cover, and a spoken AI-disclosure intro. Episodes
reach your phone through Telegram's music player or a private podcast feed for
AntennaPod. Full docs: <https://sase-org.github.io/sase-listen/>.

## How it works

1. **Write a narration script** — by hand for research reports
   (`sase-listen guide` prints the authoring rules, brief by default,
   `sase-listen lint` checks them), or deterministically from any Markdown
   (`sase-listen script`).
2. **Render it** — `sase-listen render` synthesizes each chapter, gates quality
   chunk by chunk, masters to a loudness-normalized MP3, and commits the episode
   to a local library with a manifest.
3. **Listen** — publish to your private feed (`sase-listen publish`) or get the
   MP3 delivered over Telegram.

See [Getting started](https://sase-org.github.io/sase-listen/getting-started/)
and [How it works](https://sase-org.github.io/sase-listen/architecture/).

## Status notes

These commands are not implemented yet and exit 1 with a stub message:

- `sase-listen audition`, `sase-listen ls`, `sase-listen cache`
- `sase-listen doctor --online` (offline checks work; the one-word live synth
  check is still a stub)

Everything else in `sase-listen --help` works. The
[CLI reference](https://sase-org.github.io/sase-listen/cli/) marks each stub.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports and PRs welcome; keep
Conventional Commit titles so release-please can cut releases.

## Background

sase-listen implements the
[commute-audio research](https://github.com/sase-org/sase--research/blob/617bce562f5a01dfae5999fc0caf3f6a12baf1aa/202610/commute_audio_from_markdown/commute_audio_from_markdown.md)
via epic `sase-1e3`. Details and provenance links:
[Background](https://sase-org.github.io/sase-listen/background/).
