# architecture

## Audio mastering and MP3 packaging (`src/sase_listen/audio/`)

Pure library with no engine or CLI knowledge. Engines hand over
`ChapterAudio(title, segments)` sequences plus `EpisodeMeta`; the pipeline
gets back a mastered MP3 path, `MasterStats`, and millisecond chapter marks.

- `ffmpeg.py` — `resolve_ffmpeg()`: `$SASE_LISTEN_FFMPEG`, else `ffmpeg` on
  `PATH`, else the bundled `imageio-ffmpeg` binary. Probes `libmp3lame` and
  `loudnorm` once, caches the result, and `describe()` reports the source
  for `doctor`.
- `mastering.py` — numpy PCM utilities: RMS silence detection over 20 ms
  windows at −50 dBFS, trim of leading/trailing silence to 80 ms, internal
  pauses over 1.5 s compressed to 0.7 s (originals over 4 s reported as
  `LongSilence` so gates can request re-synthesis), exact-length gap
  insertion from config (`chunk_gap_s`, `chapter_gap_s`), and a linear
  resampler for off-rate engine output. `assemble()` joins chunks with gaps
  and computes chapter offsets from sample counts after trimming, so they
  stay exact. `master_to_mp3()` runs two-pass `loudnorm` to −16 LUFS / −1.5
  dBTP (LRA 11), encodes `-ar 24000 -ac 1 -c:a libmp3lame -b:a 64k` CBR with
  a Xing header and `-map_metadata -1`, and atomically replaces the target.
- `tags.py` — ID3v2.3 via mutagen: TIT2, TPE1 (author), TALB
  ("Audio Editions"), TDRC, TCON ("Podcast"), COMM (description), TLEN,
  `TXXX:SASE_LISTEN_EPISODE` / `TXXX:SASE_LISTEN_SOURCE`, a JPEG front-cover
  APIC, one CHAP per chapter with an embedded TIT2, and a top-level ordered
  CTOC table of contents.
- `cover.py` — 1400×1400 JPEG (quality 88, progressive) title cards: a
  two-tone gradient from a curated palette picked by the title hash, the
  title in bundled Inter SemiBold (`data/fonts/`, OFL) auto-fitted to at
  most five lines, the edition label and date, and a waveform-bar motif
  generated from the title hash (same title, same bytes). Supplied images
  are letterboxed over a blurred, darkened, scaled copy of themselves.

Other subsystems are owned by their phases and documented here as they land.
