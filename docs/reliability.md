# reliability

## Audio format notes

- Episodes are MP3, mono, 24 kHz, 64 kb/s CBR — about 7 MB per 15 minutes,
  well under Telegram's 50 MB bot-audio limit.
- Loudness is two-pass EBU R128 `loudnorm` to −16 LUFS integrated with −1.5
  dBTP true-peak ceiling (LRA 11), so episodes match podcast players without
  further normalization.
- Chapter boundaries are exact: offsets derive from post-trim sample counts
  and are stamped as ID3 CHAP frames (plus a CTOC table) in milliseconds.
- Tags are ID3v2.3 for maximum player compatibility (mutagen converts TDRC
  on save); the file carries no source metadata (`-map_metadata -1`) and
  mastering encodes the MP3 to a temp file beside its target and
  atomically replaces it, so a tmpfs `/tmp` cannot break the rename and an
  interrupted render never leaves a half-written episode.

## Render pipeline (`sase-listen render`)

- **Chunk cache and resume:** every synthesized chunk is written to the
  content-addressed LRU cache immediately, keyed on the post-lexicon text
  plus engine coordinates. A killed render re-run pays only for missing
  chunks; `--no-cache` bypasses the cache entirely.
- **Quality gates:** each chunk is paced at words/duration. Hard failures
  (empty PCM, pace outside 60–300 wpm, internal silence over 4 s)
  re-synthesize with the cache bypassed up to twice, then exit 5 with a
  per-chunk report. Soft failures (pace outside 90–240, or outside
  0.65–1.5× the episode median with 3+ chunks) re-synthesize once, then
  warn and keep the attempt closest to 150 wpm. Episode gates re-check the
  mastered MP3: it must decode, match the assembled duration within
  ±(1 s + 0.5%), carry the script's chapters in order, and land within 1 LU
  of the loudness target. Episodes over 45 MB warn (Telegram allows 50 MB).
- **Manifest:** every episode commits `manifest.json` recording the source,
  script, narrator, lexicon, per-chunk attempts and cache keys, chapters,
  audio measurements, gate results, omissions, and cost estimate.
- **Atomicity:** files stage under `library/.staging/<episode-id>/` and move
  into place with atomic replaces, manifest last. An fcntl lock per episode
  id rejects concurrent renders of the same episode instead of clobbering.
- **Exit codes:** 0 ok, 1 unexpected, 2 usage, 3 config/credentials,
  4 synthesis failed after retries, 5 quality gate failed, 6 structural lint
  errors (`render` refuses unless `--force`), 130 interrupted by Ctrl-C.
- **Interrupts:** the first Ctrl-C stops queueing new chunks while running
  chunks finish and cache themselves, then exits 130 with a resume hint
  tuned to the stage (before synthesis: nothing was rendered yet; during
  synthesis and gates: `N of M chunks are cached`; during master and save:
  all chunks are cached; during publish: the episode is saved, publish it
  with `sase-listen publish`). A second Ctrl-C quits immediately without
  waiting for the cache writes. Every paid chunk survives the first press,
  so re-running the same command resumes without paying for it again.
- **Progress can never fail a render:** every progress event handler and
  view build runs behind a guard. The first internal display error
  disables the display, prints one dim `progress display error` line, and
  the render continues unaffected.
