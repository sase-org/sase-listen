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
  mastering writes through a temp file plus atomic replace, so an
  interrupted render never leaves a half-written episode.

Other reliability notes are owned by their phases and documented here as
they land.
