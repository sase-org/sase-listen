# podcast-feed

A private podcast feed for AntennaPod (or any podcast app that accepts a
URL): publish an episode once, then new editions arrive on the phone
over Tailscale Funnel.

## Quickstart

```bash
# 1. Initialize the feed. This generates a secret token, creates the
#    served-only feed directory, and stores both in your config.
sase-listen feed init --base-url https://<your-tailnet-name>:8443

# 2. Serve the feed directory on the secret path (see below for why).
tailscale funnel --bg --https=8443 --set-path=/<token> \
  ~/.local/share/sase-listen/feed

# 3. Publish episodes. The QR code goes straight into the phone camera.
sase-listen publish --latest
sase-listen feed --qr
```

The exact Funnel command and a terminal QR code are printed by
`feed init`, so step 2 is copy-paste. If your config is managed by
chezmoi, use `feed init --base-url URL --print` and paste the printed
`feed:` snippet into your config file instead of writing it directly.

## Commands

- `sase-listen feed` — status: the subscribe URL (masked unless
  `--show-url`), episode count, size on disk, last build time, and the
  retention policy. Add `--qr` for a scannable subscribe code.
- `sase-listen feed init --base-url URL` — generate the token, create
  the feed directory, and write the `feed:` config section.
- `sase-listen feed rebuild` — regenerate `feed.xml` and the channel
  art from what is published (also re-applies retention).
- `sase-listen feed prune` — drop episodes outside retention and
  regenerate, without publishing anything new.
- `sase-listen publish EPISODE` — publish one episode, where `EPISODE`
  is an episode id, an episode MP3 path, or `--latest`.
- `sase-listen unpublish EPISODE` — remove an episode id from the feed
  and regenerate.
- `sase-listen render SOURCE --publish` — render and publish in one
  step; `--no-publish` suppresses it.

Every command accepts `--json` and emits exactly one JSON object.

## What publishing writes

The feed directory is the **only** directory ever served. Publishing
copies the episode MP3, its cover, and its chapters JSON into
`<feed-dir>/episodes/<episode-id>/` and regenerates `feed.xml`
atomically (temp file plus rename, so readers never see a half-written
feed). The channel art is a generated 1400×1400 cover from the same art
system as episode covers.

`feed.xml` is RSS 2.0 with the iTunes and Podcasting 2.0 namespaces:

- Channel: `itunes:block` (`yes`), `podcast:locked` (`yes`),
  `itunes:explicit` (`false`) — the feed stays out of public indexes.
- Items, newest first: title, an HTML description with the chapter list
  and a link to the written report, a `guid` of
  `<episode-id>@<audio-sha256[:8]>` (re-renders show up as fresh audio),
  `pubDate`, an `enclosure` with the real byte length, `itunes:duration`,
  `itunes:image`, `itunes:episodeType full`, and a `podcast:chapters`
  link to the per-episode chapters JSON.

## Retention and auto-publish

Two config knobs bound the feed's size (`feed.retention_days`, default
90; `feed.max_episodes`, default 200). They apply on every publish,
rebuild, and prune: episodes older than the retention window go first,
then the oldest beyond the episode cap. Retention only ever removes
feed copies — the library keeps every episode.

With `feed.auto_publish: true`, `render` publishes `kind: research`
episodes automatically. Anything else (plain Markdown, `kind:
document`) still needs an explicit `publish` or `--publish`.

## Serving with Tailscale Funnel (and why `:8443`)

Port 443 on the tailnet stays reserved for `sase_gateway`, so the feed
is served on `:8443` instead:

```bash
tailscale funnel --bg --https=8443 --set-path=/<token> <feed-dir>
```

The tailnet policy needs the `funnel` node attribute for the serving
machine. Funnel terminates TLS and serves the static files; the
`--set-path` flag mounts the feed directory exactly at the secret token
path, so the subscribe URL is
`https://<tailnet-name>:8443/<token>/feed.xml`.

Static-server alternative: any HTTPS static host works. Copy the feed
directory to it preserving the `<token>/` prefix (or drop the prefix
and set `feed.base_url` to the directory URL plus `feed.token` to `""`
with a `token_command` that prints nothing — simpler to keep the token
path everywhere).

## AntennaPod setup

1. Add a podcast by URL and paste (or scan) the subscribe URL.
2. Under the feed settings, enable auto-download on Wi-Fi only.
3. Add new episodes to the queue automatically.
4. Chapters appear in the player from the embedded MP3 chapters and, on
   supported clients, from the linked chapters JSON.

## Security model

- The token path is the password: `secrets.token_urlsafe(24)`, stored in
  your config (or produced by `feed.token_command`), never in git, logs,
  or error output. `feed` masks it unless `--show-url` is given.
- `itunes:block` and `podcast:locked` keep the feed out of the iTunes
  directory and Podcasting 2.0 indexes.
- Only what you publish is public. The library, narration scripts, and
  source reports are never under the served directory.
- Rotate the token by replacing `feed.token` with a fresh value (or
  pointing `feed.token_command` at a new secret), then re-serving with
  the new `--set-path` and re-subscribing the phone. Old URLs stop
  working immediately. (`feed init` keeps an already-configured token.)
- `sase-listen doctor` checks the feed: directory writable, `base_url`
  set, token resolvable (via `token` or `token_command`), and
  `feed.xml` parses.
