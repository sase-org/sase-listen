# Narrators & voices

A _narrator_ is a named profile: engine, model, voice, and style. One
narrator renders a whole episode; a failure never falls back silently to
another narrator.

## Built-in narrators

| Narrator      | Engine | Model                        | Voice    |
| ------------- | ------ | ---------------------------- | -------- |
| `gemini`      | gemini | `gemini-3.8-flash-tts`       | `Charon` |
| `gemini-lite` | gemini | `gemini-3.8-flash-lite-tts`  | —        |
| `openai`      | openai | `gpt-4o-mini-tts-2025-12-15` | `marin`  |
| `tone`        | tone   | —                            | —        |

`gemini` is the default. `gemini-lite` falls back to the `Charon` voice
when its profile names none. `tone` is the offline deterministic engine
for tests, CI smoke, and demos: it costs nothing and renders at about
150 wpm.

## Engines

- **gemini** calls `client.interactions.create` with turn-level
  `speech_metadata.style` styling and a prebuilt voice. The transcript is
  sent verbatim; style is never inlined into the text.
- **openai** POSTs to `{base_url}/audio/speech` with
  `{model, input, voice, response_format: "pcm"}`, plus `instructions`
  when the narrator has a style and `speed` when it differs from 1.0.
  The default `base_url` is `https://api.openai.com/v1`.
- **tone** renders deterministic speech-paced tone bursts locally.

## The style prompt

The default narrator speaks with a fixed calm technical-briefing style:

> Calm, clear technical-briefing narrator. Moderate pace. Slight emphasis
> on numbers. Read the text exactly as written.

For Gemini it travels as a structured `speech_metadata.style` annotation;
for OpenAI-compatible endpoints as `instructions`. The style applies to
spoken audio only, never to ID3 or feed chapter titles.

## Adding a Kokoro profile

Point a narrator at a local Kokoro-FastAPI server through the OpenAI
adapter with a custom `base_url`:

```yaml
narrators:
  kokoro:
    engine: openai
    model: kokoro
    voice: af_heart
    base_url: http://localhost:8880/v1
```

Kokoro voices may prefer shorter inputs; the adapter's per-call
`max_chars` override (default 3,500) exists for that. Costs nothing:
unknown models estimate at $0.00.
