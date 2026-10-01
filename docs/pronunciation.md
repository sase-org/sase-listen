# Pronunciation

The lexicon maps written terms to spoken forms so the narrator says jargon
right. Matching is case-sensitive on whole words.

## Packaged default

The default ships in the package (`sase_listen/data/lexicon.yml`):

| Term      | Spoken as   |
| --------- | ----------- |
| `TUI`     | T-U-I       |
| `xprompt` | ex-prompt   |
| `xprompts`| ex-prompts  |
| `chezmoi` | shay-mwah   |
| `uv`      | you-vee     |
| `PyPI`    | pie-pee-eye |
| `mypy`    | my-pie      |
| `LUFS`    | loofs       |

`SASE` is deliberately absent until its pronunciation is decided.

## Your own entries

Point `lexicon:` in the config at a YAML file of `term: spoken form` lines:

```yaml
Kubernetes: kube
Grafana: gra-FAH-na
```

Your file merges over the packaged default, so it can add terms or override
them. The merged sha256 is recorded in each episode manifest, so a lexicon
change re-renders affected chunks instead of reusing stale cache audio.

The lexicon applies to spoken text only, never to ID3 or feed chapter
titles. When a name still sounds wrong, fix the script wording first (write
symbols as words, expand the acronym), and reach for the lexicon second.
