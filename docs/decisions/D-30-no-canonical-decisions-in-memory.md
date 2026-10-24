---
id: D-30
title: A canonical decision lives in a governed record, never in agent memory
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-08-17
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: D-07
---

**Status: in-force.**

## Question

The research behind this method requires one canonical current source
per governed question, and a 2026-08-16 review found no rule forbidding
the nearest failure mode a governed repository invites: an agent's
persistent memory carrying a project decision as if the memory were its
home. A memory-resident "decision" is invisible to `get_ruling`, to the
validator, to propagation, and to the owner's diff review — every
control the method provides. Where may a canonical decision live?

## Ruling

**Only in a governed record.** Agent memory — any persistent store an
agent carries between sessions — may hold a **pointer** to a record
key, or an observation about how to work, and never the ruling itself
as authority.

- An agent that finds a project decision stated only in its memory
  treats it as ungoverned evidence, exactly as
  [D-07](D-07-generated-never-authoritative.md) treats generated
  summaries: a lead to verify against the corpus, never an answer.
- The durable form of "remember this decision" is `propose_record`
  or a directly authored record through SPEC-1 section 8 — through the
  gate, onto the owner's diff.
- A memory note that restates a record's content is drift waiting to
  happen ([D-29](D-29-declare-or-drift.md) names the mechanism); a
  memory note that names a record key stays true however the record
  changes.

## Why

Every property the testbed measured — selection, temporal validity,
provenance, and obedience — holds because records sit on one path with
one status vocabulary and mechanical checks. Memory sits on none of
those paths. The obedience measurement showed the corpus binds only
while it is present; a memory-resident rule inverts that: it binds
only for the one agent carrying it, invisibly to every other reader,
with no owner review and no archive when it changes.

## Consequences

- No mechanical check can see an agent's memory; this rule is enforced
  the way D-05's voice rule is — by being stated where every agent
  reads, and by review.
- Nothing here forbids memory as such: preferences, workflow lessons,
  and pointers to records are exactly what it is for.
