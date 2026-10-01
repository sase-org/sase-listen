# Background

## Where sase-listen comes from

sase-listen implements the **commute-audio** research: the user commutes and
walks very often and wanted full-length (about 16-minute) narrated editions
of SASE research reports for the road — one CLI command or Telegram message
in, chaptered MP3 out, phone delivery through Telegram's music player and a
private podcast feed.

- **Originating research:**
  [`research:202610/commute_audio_from_markdown`](https://github.com/sase-org/sase--research/blob/617bce562f5a01dfae5999fc0caf3f6a12baf1aa/202610/commute_audio_from_markdown/commute_audio_from_markdown.md),
  which consolidates five researcher reports (strongest renderer engineering
  from `__cdx`, measurements and pipeline from `__cld`) and builds on the
  earlier `research:202606/sase_audio_generation_consolidated.md`.
- **Epic plan:** `plan:202610/sase_listen.md` (epic bead `sase-1e3`,
  [bead page](https://github.com/sase-org/sase--beads/blob/main/pages/sase-1e3/README.md)),
  which fixed the design decisions this repo implements: a standalone tool
  (not a sase plugin), the narration script as the renderer contract, one
  narrator per episode, Gemini 3.8 Flash TTS by default, MP3 mono 24 kHz
  64 kb/s at −16 LUFS, and delivery via Telegram `sendAudio` plus a
  Tailscale-Funnel-served private feed.

## Key decisions inherited from the plan

- **Standalone tool.** `uv tool install sase-listen` puts the CLI on `PATH`;
  SASE integration lives in `sase-research-artifacts` (`#research/audio`,
  swarm stage) and `sase-telegram` (`sendAudio`). See
  [SASE integration](sase-integration.md).
- **The narration script is the contract, and the CLI owns it.** `guide` and
  `lint` ship in this package so the rules never drift from the renderer.
- **One narrator per episode** — a failure never falls back silently to
  another narrator. See [Narrators & voices](narrators.md).
- **Beautiful is a requirement:** spoken AI-disclosure intro/outro, measured
  pauses, consistent loudness, generated cover art, rich terminal output, and
  this styled docs site.
