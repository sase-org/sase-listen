# Getting started

## Install

`sase-listen` is a standalone tool (not a sase plugin), installed with uv:

```bash
uv tool install sase-listen
```

Requirements: Python 3.12+, and `ffmpeg` — or nothing at all, since the
bundled `imageio-ffmpeg` binary is used automatically when no system ffmpeg
is found. Run `sase-listen doctor` to confirm the setup:

```bash
sase-listen doctor
```

## Credentials

The default narrator uses Gemini TTS. Provide a key one of two ways:

```bash
export SASE_LISTEN_GEMINI_API_KEY=...   # or GEMINI_API_KEY / GOOGLE_API_KEY
```

or, for password-manager storage (no shell, 15 s timeout, first output line):

```yaml
# ~/.config/sase-listen/config.yml
engines:
  gemini:
    api_key_command: pass show gemini_cli_api_key
```

No key at all? Use the offline `tone` engine: it renders deterministic
speech-paced audio locally, costs nothing, and needs no credentials — ideal
for trying the pipeline end to end.

## Your first episode (no credentials)

```bash
sase-listen render --dry-run -n tone notes.md   # print the plan, render nothing
sase-listen render -n tone notes.md -o episode.mp3
```

Any Markdown file renders: plain files go through the deterministic
normalizer automatically.

## Your first real narration

For a research report, hand-write the script following the packaged guide,
lint it until clean, then render:

```bash
sase-listen guide   # brief edition by default; --edition full for full length
sase-listen lint report_narration.md --source report.md
sase-listen render report_narration.md -o episode.mp3
```

The full contract lives in [Narration scripts](narration-scripts.md).

## Listen on your phone

- **Private podcast feed:** `sase-listen feed init --base-url
  https://<your-tailnet-name>:8443`, serve the feed directory, then
  `sase-listen publish --latest`. Full setup:
  [Podcast feed](podcast-feed.md).
- **Telegram:** episodes under 50 MB posted as explicit MP3 artifacts arrive
  through Telegram's music player. See
  [SASE integration](sase-integration.md).

## Next steps

- [CLI reference](cli.md) — every command, flag, `--json`, and exit code.
- [Configuration](configuration.md) — config file, environment overrides,
  narrators, and feed settings.
- [Troubleshooting](troubleshooting.md) — fixes for the most common errors.
