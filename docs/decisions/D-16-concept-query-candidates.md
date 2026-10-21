---
id: D-16
title: Concept queries resolve through typed candidates
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-08-16
last-verified: 2026-09-27
supersedes: none
replaced-by: none
depends-on: none
---

**Status: in-force.**

## Ruling

`get_ruling` gains a word-assisted fallback: when no substring of the
query appears in any key or title, a record whose key, title, or body
contains **every** query word of four or more characters becomes a
candidate. Candidates come back as a typed `ambiguous` result carrying
keys and titles only — **even when there is exactly one candidate**. A
body match never selects content silently. `list_rulings` returns the
compact `{id, title}` catalog; bodies, paths, and scopes come from
`get_ruling`.

## Why

A testbed re-measure exposed the gap: three reasonable concept
queries for the portability rule returned `no_ruling` because the
controlling record's title shared no substring with "portable
template", and the agent's only tool-path recovery was the
full catalog at 3,866 B. Word-assisted candidates close that path for a
few hundred bytes. Candidates stay typed rather than auto-selected
because silent semantic selection is the defect this system exists to
prevent ([D-09](D-09-conflict-stop-rule.md), PLAN-0002 invariant 3): a
body match that auto-served content would change behavior silently
whenever any record's body gains a word.
