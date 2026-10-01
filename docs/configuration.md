# configuration

## Credentials

API keys resolve from the first non-empty source in this order:

1. Environment variables, per engine:
   - Gemini: `SASE_LISTEN_GEMINI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`.
   - OpenAI: `SASE_LISTEN_OPENAI_API_KEY`, `OPENAI_API_KEY`.
2. `api_key_command`, for example `pass show gemini_cli_api_key`. The
   command runs without a shell, has a 15 s timeout, and contributes its
   first output line.

```yaml
engines:
  gemini:
    api_key_command: pass show gemini_cli_api_key
```

Secrets never appear in logs, manifests, errors, or `config` output —
failures name the missing source, never the value. `sase-listen doctor`
checks credential presence offline today; the cli phase adds a
`--online` one-word synth check that proves the key works.

(Further sections — paths, audio, cache, feed — land with their owner
phases. Scaffold placeholder below.)
