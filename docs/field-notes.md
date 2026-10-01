# Field notes: rollout on apollo (2026-10-01)

First real-world run of `sase-listen` 0.1.0 on apollo, dogfooding the
commute-audio research report itself (`research:202610/commute_audio_from_markdown`).

## Install

- `sase-listen` 0.1.0 has no PyPI release (trusted-publishing never ran), so
  apollo installed from git source: `uv tool install
  git+https://github.com/sase-org/sase-listen` at `ef84b3b`.
- `sase-telegram` 0.4.24 and `sase-research-artifacts` 0.2.0 were already
  current in sase's tool env; the `sendAudio` path was verified present.
- Config is chezmoi-managed (`home/dot_config/sase-listen/config.yml`):
  `narrator: gemini` with `engines.gemini.api_key_command:
  pass show gemini_cli_api_key`. Secrets stay masked in `config` output.

## Doctor

- `sase-listen doctor` passes offline: config, imageio-ffmpeg 7.0.2 static
  build, credential presence, writable dirs, version.
- `doctor --online` is not implemented in 0.1.0 (exits 3). Live credential
  verification fell back to a real synthesis, below.

## Voice audition (Gemini, live)

- Charon audition of the packaged sample passage: 28.6 s, 311 KB,
  loudness −16.28 LUFS, cost ≈ $0.0064. Saved as `docs/assets/sample.mp3`.
- Kore, Iapetus, and Sadaltager auditions were blocked: the Gemini key is on
  the **free tier, 10 synthesis requests per day**, and the Charon clip plus
  the first edition consumed it. This confirms the report's owed check that
  free-tier quotas are too low for daily use — a paid tier (or Batch) is
  required before routine use.

## First real edition

- Narration script `commute_audio_from_markdown_narration.md` (1,662 words,
  7 chapters) written by hand from the guide and linted clean against the
  source report. Dry-run plan: 9 chunks, ≈ 11.1 min, ≈ $0.15 estimated —
  against the report's ≈ $0.16 estimate.
- The Gemini render stopped after the free-tier quota (exit 4, with resume
  hint). Successfully synthesized chunks stay in the content-addressed cache,
  so re-running the same command after the quota resets finishes the episode:
  `sase-listen render <script> -n gemini`.
- A tone-engine render of the same script completed end to end as a pipeline
  proof: 706.8 s, 5.76 MB, 7 chapters, published to the feed.

## Feed

- `feed init --base-url https://apollo.tail297af1.ts.net:8443` generated a
  token-path URL and QR code; config recorded through chezmoi with
  `auto_publish: true`. The tone-engine episode published cleanly and
  `feed.xml` parses with one item.
- Tailscale Funnel exposure (`tailscale funnel --bg --https=8443
  --set-path=/<token> <feed-dir>`) was deliberately **not** opened: the feed
  holds only a tone-engine placeholder until the Gemini edition lands.

## Gaps found in 0.1.0

- `audition`, `ls`, and `cache` print "not implemented yet (owner: cli
  phase)" despite shipping in 0.1.0. The audition above was done by hand
  with `render -n gemini --voice <V>`.
- `doctor --online` reports "online check not implemented yet".
