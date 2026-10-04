# Troubleshooting

## `No API key found for <engine>`

`render` needs credentials for cloud narrators. Either export the key
(`SASE_LISTEN_GEMINI_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY` for
Gemini; `SASE_LISTEN_OPENAI_API_KEY` or `OPENAI_API_KEY` for OpenAI) or set
`engines.<name>.api_key_command` in the config. To skip credentials entirely,
render with the offline engine: `sase-listen render -n tone notes.md`.
`doctor` reports which source, if any, resolved — without ever printing the
value.

## `render` refuses with structural lint errors (exit 6)

The script violates the [contract](narration-scripts.md): run
`sase-listen lint <script> [--source <report>]` and fix each error (missing
frontmatter marker, text before the first `##`, non-`##` headings, empty
chapters). `--force` renders anyway, cleaning residue with warnings — only
for drafts, never for published episodes.

## Quality gate failed (exit 5)

A chunk failed pacing or silence checks after re-synthesis, or the mastered
episode failed its gates. The per-chunk report names the offender: short,
number-dense, or symbol-heavy passages are the usual cause. Split the chunk's
paragraph at sentence boundaries, spell out symbols as words, and re-render —
unchanged chunks come back from the cache.

## Synthesis failed after retries (exit 4)

Transient API errors are retried with backoff; persistent failure exits 4.
Check the network, confirm the key works, and try a cheaper narrator
(`gemini-lite`) or `--no-cache` to rule out a poisoned cache entry.

## `doctor --online` reports `online check not implemented yet`

Expected: the live one-word synth check is a known stub (see
[CLI reference](cli.md)). Offline checks are authoritative for setup.

## `audition`, `ls`, or `cache` print "not implemented yet"

Expected: these three commands are known stubs. Episodes and their
`manifest.json` files live under `$XDG_DATA_HOME/sase-listen/library/`
(usually `~/.local/share/sase-listen/`); inspect them directly until `ls`
lands.

## Feed host unreachable

`publish` (and auto-publish) could not SSH to `feed.host`. Destinations
in `feed.host_ssh` are tried in order; a move to the next one happens
only on ssh exit 255, a missing `ssh` binary, or a timeout before any
output. Confirm `ssh -o BatchMode=yes apollo true`, then retry with
`sase-listen publish --pending`. The episode stays in the local outbox
until that succeeds.

## sase-listen on the host is too old to receive episodes

The host does not have `feed receive` yet. Upgrade it first:

```bash
ssh apollo '~/.local/bin/uv tool install --force git+https://github.com/sase-org/sase-listen'
```

Then upgrade each renderer. See [Multi-machine publish](multi-machine.md).

## this machine is not the feed host

This process was invoked as a remote call (`SASE_LISTEN_REMOTE_CALL=1`)
on a machine whose `feed.host` points elsewhere, or `feed receive` ran
where `feed.host` is not this machine. Fix `feed.host` (empty means
this machine) so the SSH destination is the host that serves the feed.

## Feed URL doesn't resolve

Confirm `feed.base_url` uses `:8443` (port 443 stays reserved for
`sase_gateway`), the token path in the Funnel `--set-path` matches the
configured token, and the serving machine's tailnet policy grants the
`funnel` node attribute. `sase-listen feed` (without `--show-url`) confirms
the masked URL and last build time without leaking the token.

## Episode over 45 MB warns

Telegram allows 50 MB per audio message; episodes approaching that warn at
publish time. At 64 kb/s mono this means roughly over 90 minutes — split the
source or accept the document-send fallback.

## Still stuck?

Run the failing command with `--json` for the single-object error report, and
file an issue at <https://github.com/sase-org/sase-listen/issues> with the
command, the JSON output, and `sase-listen doctor` results.
