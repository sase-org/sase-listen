# sase-listen narration-script authoring guide

This guide ships with the `sase-listen` package so the rules never drift from
the code that enforces them. Follow it exactly, then run
`sase-listen lint <script> --source <report>` until it is clean.

## Who the listener is

The listener is walking or commuting, playing at 1.25-1.5x speed, and cannot
glance back. Every sentence must stand on its own. Say everything the listener
needs; nothing they would need to rewind for.

## The contract

A narration script is Markdown with YAML frontmatter plus a body of `##`
chapters and plain paragraphs:

```markdown
---
narration: 1 # required schema marker
title: One Updates Tab # required; episode title
source: research:202609/admin_center_updates_tab_unification.md # optional
source_blob: 3f9c2e1... # optional `git hash-object` of the source
date: 2026-09-14 # optional source date, spoken in the intro
kind: research # research | document (default: document)
edition: full # full | brief | digest | verbatim
producer: agent # agent | deterministic
target_minutes: 15 # optional
cover: one_updates_tab_infographic.png # optional, relative to the script file
---

## The question

Plain spoken prose. Paragraphs are separated by blank lines...
```

Rules:

- The body holds only `##` headings (chapters) and plain paragraphs.
- Every `##` is an ID3 chapter and a synthesis boundary.
- Text before the first `##` is a structural error.
- The renderer adds the spoken AI disclosure. Scripts must not.
  `full`, `brief`, and `digest` episodes open with "This is an AI-narrated
  audio edition of _Title_, SASE research from _September 14, 2026_." The kind
  phrase is omitted when `kind` is not research, and the date phrase is
  omitted when there is no `date`. `verbatim` episodes open with "This is an
  AI-narrated reading of _Title_ ...". Episodes close with "That's the end of
  this audio edition of _Title_." Never write these lines yourself.
- Agent scripts are named `<stem>_narration.md`, where `<stem>` is the report
  stem with any trailing `__final` removed.
- Record the source version with `git hash-object <report>` into
  `source_blob`.
- When a `<stem>_infographic.png` sits next to the report, set
  `cover: <stem>_infographic.png`.

## Shape

<!-- edition:full-begin -->
Use 4-8 chapters whose speakable titles echo the report's section names:

1. The question, then the short answer, up front.
2. Signposted reasons: one chapter per argument, in report order.
3. Costs and caveats: what it costs, what could go wrong.
4. A recap of what to do.
<!-- edition:full-end -->
<!-- edition:brief-begin -->
Use 2-3 chapters: the question and short answer, then the deciding reason,
then the recap of what to do. Keep titles short and speakable.
<!-- edition:brief-end -->

Chapter titles are spoken at chapter starts. Keep them short, speakable, and
close to the report's section names. Never title a chapter "See section 6" or
anything that points at the page.

## Fidelity rules

- No new claims. If the report does not say it, the script does not say it.
- Keep every argument-carrying number exactly, in digits (`2400`, `3.14`,
  `42%`). Do not round, and do not spell numbers out.
- Preserve stated uncertainty ("about", "roughly", "up to" stay).
- Never soften or strengthen the recommendation.
- Say tables as comparisons: the winner, the runner-up, and the deciding
  numbers. Never read a table cell by cell.
- Code becomes one sentence of purpose, or nothing.
- Never say paths, SHAs, line numbers, URLs, or refs.

## Listenability

- Short sentences. One idea per sentence, about 20 words at most.
- Expand acronyms on first use ("the updates tab unifies..."), then use the
  short form.
- Write symbols as words: "arrow" for `->`, "about" for `~`, "times" for `x`,
  "at least" for `>=`, "at most" for `<=`, "plus or minus" for `+-`.
- Never say "see section 6". Say "as we heard earlier" or restate the point.
- Read list items as separate sentences in order, without saying "bullet".

## Edition budgets

At 150 words per minute:

| Edition    | Word budget              | About  |
| ---------- | ------------------------ | ------ |
| `full`     | <= 2,400 words           | 16 min |
| `brief`    | about 600 words          | 4 min  |
| `digest`   | about 250 words per item | -      |
| `verbatim` | no budget                | -      |

<!-- edition:full-begin -->
Write the `full` edition at up to 2,400 words. Lint warns above 115% of the
budget and below 50%.
<!-- edition:full-end -->
<!-- edition:brief-begin -->
Write the `brief` edition at about 600 words: the question, the answer, and
only the deciding evidence. Lint warns above 115% of the budget and below
50%.
<!-- edition:brief-end -->

## Worked example

Report sentence: "The tab unifies 6 entry points; rollout took 3 weeks (§6)."

Script:

```markdown
## The short answer

The tab unifies 6 entry points. Rollout took 3 weeks.
```

The section sign became the words the listener hears, and the sentence was
split in two.

## Final step

Run `sase-listen lint <script> --source <report>` and fix every finding.
Repeat until clean:

```bash
sase-listen lint one_updates_tab_narration.md --source report.md
```
