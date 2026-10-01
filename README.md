# sase-listen

[![CI](https://github.com/sase-org/sase-listen/actions/workflows/ci.yml/badge.svg)](https://github.com/sase-org/sase-listen/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/sase-listen.svg)](https://pypi.org/project/sase-listen/)
[![Python](https://img.shields.io/pypi/pyversions/sase-listen.svg)](https://pypi.org/project/sase-listen/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://sase-org.github.io/sase-listen/)

Turn Markdown into chaptered, loudness-normalized MP3 audio editions — narrated by Gemini TTS, built for listening to SASE research on a commute or walk.

> Coming soon: this repo is being scaffolded. The narration pipeline lands in the
> `script`, `audio`, `engines`, and `pipeline` phases.

```bash
uv tool install sase-listen
sase-listen doctor
sase-listen render notes.md
```
