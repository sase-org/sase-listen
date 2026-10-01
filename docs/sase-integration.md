# SASE integration

sase-listen is a standalone tool: it never imports `sase`, declares no
`sase_*` entry points, and SASE-side integration lives in the sibling plugin
repos. sase-listen only shells out to `sase artifact read` at runtime to
resolve `kind:path` refs.

## `#research/audio` xprompt (sase-research-artifacts)

Narrate any research report from inside a SASE session:

1. The xprompt resolves the report (a `@research:` ref or the swarm lead's
   published file), preferring `<name>.md` and falling back to
   `<name>__final.md`.
2. It runs `sase-listen guide --edition <edition>` and writes
   `<stem>_narration.md` next to the report (`__final` stripped from the
   stem), with `source`, `source_blob`, `date`, `kind: research`, and `cover`
   when a `<stem>_infographic.png` exists.
3. It lints with `sase-listen lint <script> --source <report>` until clean,
   renders with `sase-listen render <script> --json`, and registers the MP3
   with `sase artifact create` so it rides the completion notification to
   Telegram.

The narration companion (`<stem>_narration.md`) is excluded from the
`@research` inventory and Highlights hook by glob, like the image companion.

## `research_swarm` audio stage (sase-research-artifacts)

Pass `audio=true` (optionally `audio_model="@audio"`) to add an opt-in stage
after the lead researcher — and after the linker when it runs, so the edition
narrates the published report and can use the infographic as cover. The stage
agent runs the same write → lint → render → register flow as `#research/audio`
and reports duration, chapters, approximate cost, and feed publication.

## Telegram delivery (sase-telegram)

`.mp3`/`.m4a` attachments route to `sendAudio` in the outbound loop, so
episodes arrive in Telegram's music player instead of as generic documents:

- Title, performer, and duration are read from the ID3 tags sase-listen
  writes; any read failure falls back to sending without metadata.
- Files over the 50 MB bot-audio limit fall back to a document send.
- At 64 kb/s mono, a 15-minute episode is about 7 MB — well under the limit.
