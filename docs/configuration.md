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

## Feed

```yaml
feed:
  host: apollo # empty = this machine serves the feed
  host_ssh: [apollo, apollo-do] # tried in order; empty means [host]
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
[podcast-feed](podcast-feed.md) for serving and AntennaPod setup, and
[Multi-machine publish](multi-machine.md) for `host` / `host_ssh`.

## Article writer

Article URL editions use the Gemini text API. The writer uses the existing
`engines.gemini` credential settings and can be tuned with:

```yaml
writer:
  engine: gemini
  model: gemini-3.1-pro-preview
  temperature: 0.3
  max_attempts: 3 # initial write plus lint repairs
  timeout_s: 300
```

`SASE_LISTEN_WRITER_MODEL` overrides `writer.model`. Cached scripts are reused
when the source hash, prompt version, edition, and model match.

## Paths

All locations follow XDG, overridable per variable:

| Purpose  | Default                                  | Override             |
| -------- | ---------------------------------------- | -------------------- |
| Config   | `~/.config/sase-listen/config.yml`       | `$SASE_LISTEN_CONFIG`|
| Library  | `$XDG_DATA_HOME/sase-listen/library`     | `$XDG_DATA_HOME`     |
| Sources  | `$XDG_DATA_HOME/sase-listen/sources`     | `$XDG_DATA_HOME`     |
| Feed     | `$XDG_DATA_HOME/sase-listen/feed`        | `feed.dir`           |
| Cache    | `$XDG_CACHE_HOME/sase-listen/chunks`     | `$XDG_CACHE_HOME`    |
| State    | `$XDG_STATE_HOME/sase-listen`            | `$XDG_STATE_HOME`    |

## Audio

Mastering and gap defaults (see [Reliability](reliability.md) for what they
do):

```yaml
audio:
  bitrate_kbps: 64
  sample_rate: 24000
  loudness_lufs: -16.0
  true_peak_db: -1.5
  chunk_gap_s: 0.5
  chapter_gap_s: 1.2
  intro_gap_s: 0.9
```

Spoken templates (placeholders `{title}`, `{kind_phrase}`, `{date_phrase}`):

```yaml
intro_template: 'This is an AI-narrated audio edition of {title}{kind_phrase}{date_phrase}.'
outro_template: "That's the end of this audio edition of {title}."
author: '' # ID3 artist / feed performer when set
```

## Cache

```yaml
cache:
  max_gb: 2.0 # LRU bound enforced after each render commit
```

`SASE_LISTEN_CACHE_MAX_GB` overrides `max_gb`. Other useful environment
overrides: `SASE_LISTEN_NARRATOR`, `SASE_LISTEN_AUTHOR`, `SASE_LISTEN_LEXICON`
(custom lexicon file), `SASE_LISTEN_INTRO_TEMPLATE`,
`SASE_LISTEN_OUTRO_TEMPLATE`, `SASE_LISTEN_GEMINI_API_KEY_COMMAND`,
`SASE_LISTEN_OPENAI_API_KEY_COMMAND`, `SASE_LISTEN_FEED_BASE_URL`,
`SASE_LISTEN_FEED_HOST` (empty means this machine).
Unknown config keys are errors with a did-you-mean hint — run
`sase-listen config` to see the effective config and each value's origin.
