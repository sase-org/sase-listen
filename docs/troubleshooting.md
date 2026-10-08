# Troubleshooting

## Progress output is garbled or too chatty

The live checklist assumes a responsive terminal. When it flickers, wraps
badly, or hides the lines you care about, drop one level:
`--progress plain` writes one line per event with no ANSI escapes, and
`--progress off` silences stderr progress entirely (the finished checklist
still prints to stdout with the summary). These flags exist on `render`
and, for URL sources, on `script`.

## `API_KEY_INVALID` / rejected credentials (exit 3)

Gemini rejected the API key (HTTP 400 `API_KEY_INVALID`, or 401/403). The
exit-3 hint names the credential source without revealing the value, for
example `env GEMINI_API_KEY (overrides
engines.gemini.api_key_command; presence only)`. When an env var wins while
`api_key_command` is configured, unset it or pin
`engines.<engine>.api_key_env` to the tool-specific variable (see
[Credentials](configuration.md)). `sase-listen doctor` shows the winning
source offline. This supersedes the old `env -u GEMINI_API_KEY …`
workaround.

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

## Synthesis failed (exit 4)

Transient API errors are retried with backoff and exit as
`Synthesis failed after retries`. Permanent rejections (bad input,
policy blocks) are not retried and exit as `Synthesis failed`. Check
the network, confirm the key works, and try a cheaper narrator
(`gemini-lite`) or `--no-cache` to rule out a poisoned cache entry.

## Gemini blocked the text (`content_blocked`)

Gemini TTS sometimes refuses a chunk with HTTP 400 `content_blocked`
(a context-dependent policy filter, most often its music/singing
restriction). The block is deterministic: re-running the same text
fails the same way.

`sase-listen` automatically splits a blocked chunk into smaller pieces
(paragraphs, then sentences), synthesizes each piece, and stitches
them with the normal chunk gap. A successful split leaves a render
warning such as `Chunk 8 (Results from the updated harness): Gemini
blocked the full chunk (content_blocked); synthesized it in 5 pieces.`

When a single sentence is blocked even on its own, the render fails
with `Gemini's policy filter blocked a sentence even on its own`,
naming the chunk position, chapter, and sentence. Rephrase that
sentence in the narration script (path given in the error) and
re-render (unchanged chunks come from the cache), or render with
another narrator (`-n openai`).

## `Invalid cross-device link` while mastering (`Master audio`)

The mastered MP3 was encoded under the system temp dir (`/tmp`, often
tmpfs) and renamed into the library, but `rename(2)` fails with EXDEV
across filesystems. Releases with the beside-the-target mastering fix are
immune: upgrade with `uv tool install --force sase-listen` (or
`uv tool install --force git+https://github.com/sase-org/sase-listen`),
then re-run the same `render` command. Synthesized chunks and article
scripts are cached, so it goes straight to master/save/publish with
no new TTS or writer calls. (The stage now shows as `Master audio` in
the live checklist.)

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

### `Permission denied (publickey)`

SSH reached the host but accepted none of the offered keys. A
passphrase-protected key needs a loaded agent in the environment that
renders: the error names the agent state it found (no identities,
unreachable, or `SSH_AUTH_SOCK` unset). Note that tools which snapshot an
interactive shell (Codex) use whatever `SSH_AUTH_SOCK` the shell rc
exports, so a stale snapshot can point at an empty agent. Check with
`ssh-add -l`, load the key the host accepts, and retry with
`sase-listen publish --pending`.

## sase-listen on the host is too old to receive episodes

The host does not have `feed receive` yet. Upgrade it first:

```bash
ssh apollo '~/.local/bin/uv tool install --force git+https://github.com/sase-org/sase-listen'
```

Then upgrade each renderer. See [Multi-machine publish](multi-machine.md).

## `cannot import '<module>' … out of date with its code` (exit 3)

The install's Python environment is older than its code (usually an
editable install after a `git pull` without a reinstall). Run the
`Reinstall:` command from the hint, for example
`uv tool upgrade --reinstall sase-listen`. The remote variant
`on <host>: sase-listen cannot import …` means the feed host is stale;
run its repair command over SSH first.

## `feed:host-build` / "feed host runs sase-listen X; this machine runs Y"

Renderer and host builds drifted. `doctor` fails `feed:host-build`
(exit 3) and successful publishes warn with both builds. Upgrade the
older side first (host first), then re-run `sase-listen doctor` until
`feed:host` and `feed:host-build` are ok. See
[Multi-machine publish](multi-machine.md).

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

## AntennaPod still plays the old audio after a re-render

A re-render (or a same-title replacement) changes the feed, but
AntennaPod keeps an already-downloaded file: it matches the re-render
by enclosure URL, and a same-day same-title item by title, so the old
audio keeps playing. Delete the episode's download in AntennaPod, then
download it again (or stream it). The fresh download fetches the
current feed audio.

## Episode over 45 MB warns

Telegram allows 50 MB per audio message; episodes approaching that warn at
publish time. At 64 kb/s mono this means roughly over 90 minutes — split the
source or accept the document-send fallback.

## Still stuck?

Run the failing command with `--json` for the single-object error report, and
file an issue at <https://github.com/sase-org/sase-listen/issues> with the
command, the JSON output, and `sase-listen doctor` results.

## `The extracted PDF text is too short` (scanned PDFs)

The PDF has no usable text layer — usually a scanned image. OCR is not
supported: convert the PDF to Markdown (or export the paper as text) and
render that file instead.
