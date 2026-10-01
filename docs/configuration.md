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

(Further sections — paths, audio, cache — land with their owner
phases. Scaffold placeholder below.)

## Feed

```yaml
feed:
  dir: ~/.local/share/sase-listen/feed # the only directory ever served
  base_url: https://<tailnet-name>:8443 # public base; :8443 reserves 443
  token: <secret> # or token_command, e.g. `pass show listen_feed_token`
  title: SASE Listen
  description: Narrated audio editions of Markdown.
  author: ""
  language: en
  retention_days: 90
  max_episodes: 200
  auto_publish: false # true: render publishes kind: research automatically
  source_url_templates:
    research: https://github.com/sase-org/sase--research/blob/master/{path}
```

`dir` defaults to `$XDG_DATA_HOME/sase-listen/feed`. `base_url` plus
the secret token path form the subscribe URL
(`<base>/<token>/feed.xml`); the token resolves from `token`, else from
`token_command` (no shell, 15 s timeout, first output line), and never
appears in logs or `config` output. `source_url_templates` maps a
`kind:path` ref prefix to the report link shown in each feed item. See
[podcast-feed](podcast-feed.md) for serving and AntennaPod setup.
