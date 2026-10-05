# Field notes: URL-to-podcast rollout-proof (2026-10-05)

Proof episode for epic `sase-1g7` / phase `sase-1g7.4`: from athena, a full
Gemini edition of https://openai.com/index/harness-engineering/ auto-published
to the AntennaPod feed served on apollo. No feed token or full feed URL is
recorded here.

## Installs

- There is still no PyPI release. apollo was upgraded first:
  `ssh apollo '~/.local/bin/uv tool install --force --reinstall git+https://github.com/sase-org/sase-listen'`
  at `9f491ac` (url-editions). `--version` 0.1.0, local role,
  `receive_protocol: 1`, nine existing episodes. Tailscale serve still maps
  `:8443/<token>` to the feed dir.
- athena had been an editable `uv tool` install of
  `~/projects/github/sase-org/sase-listen` at `d3cb303` (version 0.0.0,
  extras=dev). Production was restored from git, then reinstalled from this
  workspace checkout so the two bugfixes below shipped on the renderer.
- Restore an editable dev install later with:
  `uv tool install --force --reinstall --editable /home/bryan/projects/github/sase-org/sase-listen`

## chezmoi

`home/dot_config/sase-listen/config.yml` now sets `feed.host: apollo`,
`host_ssh: [apollo, apollo-do]`, and `dir: ~/.local/share/sase-listen/feed`.
Applied on athena only via
`chezmoi apply --source <chezmoi checkout> ~/.config/sase-listen/config.yml`.
apollo is the host, so either config works there. After the chezmoi commit
lands, run `chezmoi update -a --force` on athena, apollo, and the Mac when
it is online.

## Credentials

This agent environment exported a stale 39-character `AIza…` `GEMINI_API_KEY`
that overrode `engines.gemini.api_key_command: pass show gemini_cli_api_key`
and made Gemini return HTTP 400 `API_KEY_INVALID`. The manual workaround was:

```
env -u GEMINI_API_KEY -u GOOGLE_API_KEY -u SASE_LISTEN_GEMINI_API_KEY sase-listen …
```

That workaround is superseded by the pinned chezmoi config
(`engines.gemini.api_key_env: [SASE_LISTEN_GEMINI_API_KEY]`), which makes the
pass-managed key win regardless of ambient generic keys. The stale entries
were removed from `~/.profile.local`, `~/.sase/service/env`, and the tmux
global environment.

`pass show gemini_cli_api_key` is the paid key that actually works.

## Bugfixes found in this rollout

1. `sase-listen feed` in remote mode kept `via: local` from the host JSON.
   The client now overwrites `via` with the SSH destination. After the fix,
   athena prints `Host: apollo (via apollo)`.
2. The Gemini writer now disables automatic function calling (the SDK was
   inferring tools from prompt text). HTTP 400 `API_KEY_INVALID` maps to
   `CredentialsError` and names the env override of `api_key_command`.

## Extraction and writer

- Fetch: HTTP 200, Trafilatura 2.3.0. Outline found 14 headings, restored
  11 (including "Increasing levels of autonomy" and "Entropy and garbage
  collection"). Missing page chrome: Author, Acknowledgements, Keep reading.
- Brief script: 520 words, 3 chapters, lint clean, writer attempts=2
  (`gemini-3.1-pro-preview`, prompt_version 1).
- Full script: lint clean. Writer attempts=1 (cached). Key claims present:
  0 lines of manually written code, about 1/10th the time, a million lines,
  five months, third-person attribution.

## Proof episode

- id: `harness-engineering-leveraging-codex-in-an-agent-first-world-ea4e4f`
- title: `Harness engineering: leveraging Codex in an agent-first world (Full)`
- narrator: gemini / gemini-3.8-flash-tts / Charon
- duration 786.64 s (13.1 min), 6,445,892 bytes, 8 chapters, loudness −16.3 LUFS
- chunks: 10 (9 cached on the successful run, 1 synthesized)
- estimated cost ≈ $0.177
- `published: true`, `publish_host: apollo`, `publish_queued: false`
- Served feed: newest item has the `(Full)` title, the openai.com link, and
  the coverage sentence "A full-length narrated adaptation of the whole
  article — not a word-for-word reading." Enclosure HEAD 200 with
  Content-Length equal to the declared length. Cover JPEG and chapters JSON
  both 200. `ssh apollo '~/.local/bin/sase-listen feed --json'` lists the
  episode id. Earlier nine episodes remain in the feed.

## Gemini TTS `content_blocked`

The writer had merged the two source headings into one 311-word chapter
"Autonomy and garbage collection". Gemini TTS returned HTTP 400
`content_blocked` on that packed chunk (exit 4). Cached chunks were reused
on reruns. Splitting the headings back to the source outline left a 198-word
"Entropy and garbage collection" chunk still blocked. Bisect: the last
paragraph's "Technical debt is like a high-interest loan" sentence, in the
same chunk as "background Codex tasks scan for deviations" plus "garbage
collection", was the trigger. Dropping the loan metaphor (keeping garbage
collection and paying technical debt down in small increments) synthesized
cleanly. Follow-up: treat `content_blocked` as a split-and-retry signal
rather than a hard episode failure.

## Mac

`ssh -o ConnectTimeout=10 mac true` timed out
(`kellys-macbook-pro.tail297af1.ts.net:22`). apollo-do is reachable. Mac
install is a proposed follow-up on `sase-1g7.4`, not a blocker.

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
