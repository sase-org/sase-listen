# Multi-machine publish

Render on any machine; publish to one feed host over SSH. The phone's
AntennaPod subscription, the feed token, and `tailscale serve` stay on
the host. The token never leaves that machine.

## The model

Every machine fetches, writes the script, synthesizes, and masters
locally. When `feed.host` names another machine, `publish` packs the
finished library episode (MP3, cover, chapters, script, manifest) as an
uncompressed tar and streams it to `sase-listen feed receive` on the
host. The host validates the archive, imports it into its own library,
and publishes under a feed lock — the same code path as a local
publish. Only the host writes `feed.xml`.

An empty `feed.host` means this machine, so existing configs keep local
publish.

## Config

```yaml
feed:
  host: apollo
  host_ssh: [apollo, apollo-do]
  dir: ~/.local/share/sase-listen/feed
  base_url: https://apollo.tail297af1.ts.net:8443
  token_command: pass show sase_listen_feed_token
  title: SASE Listen
  auto_publish: true
```

`SASE_LISTEN_FEED_HOST` overrides `feed.host`. An empty value means
this machine. `host_ssh` is tried in order; if it is omitted, the host
name is the only destination.

## Per-machine checklist

On every machine that should publish:

1. Install sase-listen (`uv tool install sase-listen`, or
   `~/.local/bin/uv tool install --force git+https://github.com/sase-org/sase-listen`
   over non-interactive SSH, where `~/.local/bin` is not on `PATH`).
2. Credentials: `pass show gemini_cli_api_key` (or
   `SASE_LISTEN_GEMINI_API_KEY`) on the renderer; the feed token stays
   on the host (`pass show sase_listen_feed_token` or `feed.token`).
3. Prove SSH: `ssh -o BatchMode=yes apollo true` (and `apollo-do` if
   you list it in `host_ssh`).

## What travels over SSH

The tar contains the episode MP3, `cover.jpg`, `chapters.json`,
`script.md`, and `manifest.json`. It never contains the feed token or
API keys. Remote `publish` / `feed` JSON masks the token in every URL.
`--show-url` is local-only; `--qr` builds the code on the client from
the URL the host returns.

`feed receive` is the internal transport endpoint. Do not call it by
hand unless you are debugging the host.

## Outbox

If the host is unreachable, `publish` queues the episode under
`$XDG_STATE_HOME/sase-listen/outbox/` and exits 1 with the hint
`sase-listen publish --pending`. Auto-publish on `render` does the same
without changing the exit code: it warns and leaves `publish_queued:
true`. Retry with `sase-listen publish --pending`.

## Upgrade order

Upgrade the **host first**. An older sase-listen there does not
understand `feed receive` and the client prints:

```text
sase-listen on apollo is too old to receive episodes; upgrade it there:
`~/.local/bin/uv tool install --force git+https://github.com/sase-org/sase-listen`
```

Then upgrade each renderer and point `feed.host` at the host.

## Thin-client fallback

URLs are location-independent, so you can still render on the host:

```bash
ssh apollo '~/.local/bin/sase-listen render <URL> …'
```

Prefer local render plus SSH publish: the host is a small droplet, and
a laptop that sleeps would kill a remote session.
