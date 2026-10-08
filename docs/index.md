# sase-listen

Turn Markdown into chaptered, loudness-normalized MP3 audio editions — built for
listening to SASE research on a commute or walk.

```bash
uv tool install sase-listen
sase-listen doctor
sase-listen render report_narration.md -o episode.mp3
```

Sase users can skip the standalone binary: `sase plugin install listen`
mounts the same CLI as `sase listen`.

One narrator reads the whole episode (Gemini TTS by default). Every `##`
heading becomes an ID3 chapter and a synthesis boundary. The MP3 is mono,
24 kHz, 64 kb/s at −16 LUFS — about 7 MB per 15 minutes — with an embedded
cover and a spoken AI-disclosure intro. Episodes reach your phone through
Telegram's music player or a [private podcast feed](podcast-feed.md).

Hear a sample (Charon voice, recorded during the apollo rollout):

<audio controls src="assets/sample.mp3"></audio>

Start with [Getting started](getting-started.md). To understand the pipeline,
read [Architecture](architecture.md). For the why, read
[Background](background.md). To contribute, read
[CONTRIBUTING](https://github.com/sase-org/sase-listen/blob/master/CONTRIBUTING.md).
