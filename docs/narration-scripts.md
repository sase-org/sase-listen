# Narration scripts

The narration script is the contract between script producers and the
renderer. An agent writes one by hand for research reports; anything else
goes through the deterministic normalizer. The renderer never knows which
producer wrote the script.

## The v1 contract

A script is Markdown with a YAML frontmatter block, then a body of `##`
chapters and plain paragraphs:

```markdown
---
narration: 1 # required schema marker
title: One Updates Tab # required; episode title (ID3 TIT2, feed item title)
source: research:202609/admin_center_updates_tab_unification.md # optional
source_blob: 3f9c2e1... # optional `git hash-object` of the source
date: 2026-09-14 # optional source date, spoken in the intro
kind: research # research | document (default: document)
edition: brief # full | brief | digest | verbatim
producer: agent # agent | deterministic
target_minutes: 4 # optional
cover: one_updates_tab_infographic.png # optional, relative to the script file
---

## The question

Plain spoken prose. Paragraphs are separated by blank lines...
```

Only `##` headings are allowed. Every `##` becomes an ID3 chapter and a
synthesis boundary. Text before the first `##` is a structural error.

The renderer adds the spoken intro (AI disclosure) and outro, so scripts
must not. Agent scripts are named `<stem>_narration.md`, where `<stem>` is
the report stem with any trailing `__final` removed.

Edition budgets at 150 words per minute: `full` is at most 2,400 words
(about 16 minutes), `brief` is about 600 words, `digest` is about 250 words
per item, and `verbatim` has no budget. Lint warns above 115% of the budget
or below 50%.

## Deterministic scripts from Markdown

`script` normalizes any Markdown file into a lint-clean `edition: verbatim`,
`producer: deterministic` script, with an omissions report:

```bash
sase-listen script notes.md -o notes_narration.md
sase-listen script notes.md --json
```

The normalizer keeps the title (frontmatter, else the first H1, else the
filename), turns H2 headings into chapters, H3-H6 into spoken sentences,
list items into sentences, links into anchor text, useful images into
"Figure: ..." sentences, and small tables into row-by-row speech. Fences,
Mermaid diagrams, math, HTML comments, footnotes, `Sources`/`References`
sections, and large tables are dropped and recorded in the omissions report.
Inline code drops paths, `file:line` cites, SHAs, URLs, and SASE refs;
`snake_case` becomes "snake case", `CamelCase` splits, single keys become
"the X key", and `§6` becomes "section 6".

## Lint

`lint` validates a script against the contract and listenability rules. Each
finding carries an id, severity, `line:col`, and fix hint:

```bash
sase-listen lint episode_narration.md --source report.md
sase-listen lint episode_narration.md --strict --json
```

Structural errors cover frontmatter, missing chapters, empty chapters,
non-`##` headings, and preamble text. Residue errors cover list markers,
tables, fences, backticks, link syntax, HTML, and emphasis. Warnings cover
URLs, paths, `file:line`, SHAs, refs, `§`, symbols (`→ ≈ × ≥ ≤ ±`), long
chapters/paragraphs/sentences, and edition budgets. `--source` adds number
fidelity: every number in the script must appear in the source.

`lint` exits 1 on errors, or on warnings with `--strict`. `render` refuses
structural errors, and cleans residue with warnings.

## Authoring guide

`guide` prints the packaged rules agents write by:

```bash
sase-listen guide                  # brief by default
sase-listen guide --edition full   # full-length edition
```

The final step of every hand-written script is
`sase-listen lint <script> --source <report>`, repeated until clean.
