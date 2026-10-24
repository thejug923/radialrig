---
id: D-33
title: Agents author everything in this repository
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-08-18
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: D-07, D-10, D-18
---

**Status: in-force.**

## Question

Records and repository documents described content as "hand-kept",
"tested by hand", "hand edits", or "hand-authored", implying a human
typed it. In this repository agents author everything — records,
specs, research, guidance, code, and visualizations. The owner directs
the work, answers decision questions, reads diffs, and merges; the
owner does not type file content. May repository text describe
authorship as manual?

## Ruling

**No document in this repository describes content as written by
hand.** Authorship distinctions carry no authority, and live text does
not record them. The contrasts that matter, and the words for them:

- **Generated vs authored** — built live from sources vs written as
  text ([D-07](D-07-generated-never-authoritative.md) governs the
  first).
- **Gated vs direct** — through an authoring verb vs edited with file
  tools ([D-18](D-18-authoring-write-path.md) governs the gate; a
  direct edit remains the lawful fallback under SPEC-1 section 8).
- **Governed vs ungoverned** — inside the corpus vs evidence
  ([D-10](D-10-governed-scope.md)).

An archive entry preserving a prior position may quote the phrasing it
retires. That is history, not use.

**A proper noun is not a claim of authorship.** A name that contains
one of the retired words as a substring — the skill name
`session-handoff` contains `hand` — may appear in reports, on the
rendered page, and in any document, because a name asserts nothing
about who typed what. The mechanical checks over output text
therefore match whole words, never substrings. The substring ban on
the source of the modules that predate the skills kit stays strict:
those modules name no skill literally, so the stricter test keeps its
reach there.

## Why

Ruled by the owner 2026-08-17. Agents produced the research corpus,
the engine, and every record here. Phrasing that implies human typing
invites a false inference: that human-authored text carries special
authority, or that agent-authored text needs less review. Neither is
true. Authority comes from scope and status; review comes from the
owner's diff gate; both apply regardless of author.

The proper-noun clarification was ruled by the owner on 2026-08-18,
when the skills kit ([D-36](D-36-skills-kit-form.md)) made
`session-handoff` a name the product must print. Renaming the skill
was considered and declined: the ruling retires descriptions of
authorship, and a name describes nothing.

## Consequences

- Four in-force records carried the phrasing (D-18, D-20, D-21, D-30)
  and were amended under 8.1 the day this record took effect; their
  prior wording lives in the archive.
- The route map's fallback line says "direct edit", not "hand edit".
- The mechanical output checks match whole words; the source checks
  on the pre-kit modules keep the substring form.
