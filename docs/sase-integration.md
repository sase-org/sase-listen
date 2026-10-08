# SASE integration

sase-listen never imports `sase`: the only `sase_*` entry point is
`sase_commands` (which mounts the `sase listen` command plugin), and
SASE-side integration lives in the sibling plugin repos. sase-listen only
shells out to `sase artifact read` at runtime to resolve `kind:path` refs.

Two installs, one codebase:

- `sase plugin install listen` gives `sase listen` — the first-class
  command plugin for people who use sase.
- `uv tool install sase-listen` gives the standalone binary for people who
  don't use sase.

Both run the same code; help text and "run this next" hints name whichever
binary was invoked.

## `#research/audio` xprompt (sase-research-artifacts)

Narrate any research report from inside a SASE session:

1. The xprompt resolves the report: an explicit `@research:` ref selects
   exactly that file; swarm audio narrates the lead's `<name>__final.md`
   (the file the lead wrote, even if `<name>.md` has since appeared).
2. It runs `sase-listen guide --edition <edition>` (brief by default;
   `full` and `brief` are the supported guide-backed authoring choices) and
   writes `<stem>_narration.md` next to the report (`__final` stripped from
   the stem), with `source`, `source_blob`, `date`, `kind: research`,
   `edition` matching the selected edition, and `cover` when a
   `<stem>_infographic.png` exists.
3. It lints with `sase-listen lint <script> --source <report>` until clean,
   renders with `sase-listen render <script> --json`, registers the MP3 with
   `sase artifact create -k file -l "audio:<episode_id>"`, and sets
   `sase var set audio` from the render JSON. A failed render sets
   `audio.ok=false`, registers no artifact, and completes normally.

The narration companion (`<stem>_narration.md`) is excluded from the
`@research` inventory and Highlights hook by glob, like the image companion.

## `research_swarm` audio stage (sase-research-artifacts)

Pass `audio=true` (optionally `audio_model="@audio"`,
`audio_edition="brief"` or `"full"`) to add an opt-in stage after the lead
researcher — and after the image agent when `image=true`, so the infographic
can be the cover. `audio=true` implies the linker: the linker waits on audio
and publishes a listen card plus `audio:` frontmatter on the canonical
`<name>.md`. bob later binds the library MP3 for the Highlights PDF. The
stage agent runs the same write → lint → render → register flow as
`#research/audio` (brief by default), sets `sase var set audio`, and reports
duration, chapters, approximate cost, and feed publication. A failed TTS
render completes the audio agent so the linker can still publish, without a
card. Edition selection affects newly authored narration, not whether audio
is enabled.

## Telegram delivery (sase-telegram)

`.mp3`/`.m4a` attachments route to `sendAudio` in the outbound loop, so
episodes arrive in Telegram's music player instead of as generic documents:

- Title, performer, and duration are read from the ID3 tags sase-listen
  writes; any read failure falls back to sending without metadata.
- Files over the 50 MB bot-audio limit fall back to a document send.
- At 64 kb/s mono, a 15-minute episode is about 7 MB — well under the limit.
