---
id: D-19
title: Grade records evidence, not recency
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-08-16
last-verified: 2026-09-27
supersedes: none
replaced-by: none
depends-on: D-02
---

**Status: in-force.**

## Question

What do `decided`, `verified` and `assumed` mean, and what moves a record
between them?

## Ruling

`grade` records **what kind of evidence stands behind the ruling**.
`last-verified` records **when the record was last checked**. They answer
different questions and they move for different reasons.

| Grade | Meaning |
| --- | --- |
| `decided` | The owner made a judgment call. The support is argument, not observation. |
| `assumed` | Taken without evidence, as a working position. Carries `verify-by`, naming the event that will settle it. |
| `verified` | Checked against observed reality. The record states what was observed. |

**A re-check never changes `grade`.** It moves `last-verified` and
nothing else. Only new evidence moves `grade`:

- `assumed` → `decided` or `verified` when the `verify-by` event fires.
  The event removes the field (SPEC-1 section 3).
- `decided` → `verified` when an observation is recorded in the record
  itself. Naming the observation is part of the move.
- `verified` → `decided` never happens. A record that has been observed
  does not lose that history because time passed; it goes stale instead,
  which is what C6 measures.

A new record starts at `decided`, unless it is taken without evidence, in
which case it starts at `assumed` and carries `verify-by`.

## Why

The field had three values, no definitions and no stated transitions.
Four independent agents in PLAN-0003 Phase 1 activity 2 flagged the gap
unprompted, and all four defaulted to `decided` — the right guess, made
four times without a rule to make it from. One of them proposed promoting
to `verified` after a routine re-check, which would have made `grade`
drift upward with every gardening pass and turned it into a second, worse
copy of `last-verified`.

Separating the two keeps each field answering one question. A reader who
wants to know how much to trust a ruling reads `grade`. A reader who
wants to know whether it is current reads `last-verified`.

## Consequences

SPEC-1 section 3 states the definitions and this record holds the
reasoning. No mechanical check covers `grade`: nothing can observe
whether an observation happened.
