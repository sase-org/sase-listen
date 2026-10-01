---
narration: 1
title: Commute audio rendering (excerpt)
kind: document
edition: verbatim
producer: deterministic
---

## Commute audio rendering (excerpt)

A trimmed, synthetic stand-in for a research-report excerpt. It carries the four residue classes the researchers quoted: empty parens, letter keybindings, section signs, and mid-argument code.

## Rendering pipeline

The renderer shells out to ffmpeg with the loudnorm filter. Gaps measure 0.5 s between chunks and 1.2 s between chapters. Output renders as 64 kb/s mono. The commit left an empty paren pair in the draft.

Operators press the j key to skip back and the k key to pause. Details live in section 6 and in section 12 of the appendix. Mid-argument code like cache get key and dry run flags must never reach the narrator. The build pins imageio ffmpeg 0.6.0 for its static binary.

## Measurements

Two-pass loudness hits the target within 1 LU. Chapter offsets stay exact to the sample. A 15 minute episode measures about 7 MB, well under the limit.
