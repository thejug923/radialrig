---
id: D-28
title: depends-on is required; an empty dependency list is declared, not absent
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-08-29
last-verified: 2026-09-27
supersedes: none
replaced-by: none
depends-on: SPEC-1, D-02
---

**Status: in-force.**

## Question

PLAN-0003's alignment review (2026-08-15) found that a record with no
`depends-on` was indistinguishable from one whose dependencies were
never recorded, which made the research's control 4 unenforceable
rather than merely unenforced. The owner leaned toward requiring the
field and deferred it, because the Phase 1 baseline arm had already run
against the seeded fixture: a backfill then would have handed the verb
arm information the null arm never had. The plan said the migration
runs "after Phase 4 or not at all". Phase 4 ran. Which is it?

## Ruling

1. **`depends-on` is required on every governed record.** A record with
   no dependencies declares `depends-on: none`. C2 enforces presence;
   SPEC-1 section 3 and [D-02](D-02-metadata-contract.md) are amended
   accordingly.
2. **Adding the field to an existing corpus is one schema migration
   under this ruling.** Inserting the explicit `none` states what every
   record already silently was; it changes no ruling, archives nothing
   per record, and moves no `last-verified` — no re-check happened
   (SPEC-1 8.6). Run it as a single change, not record by record.

## Why

- **The value is hygiene, not enforcement, and the ruling says so.** The
  8.5 walk finds a dependent from the dependent's own declaration. A
  false `none` is as invisible to it as a missing field was, so
  requiring the field does not enforce control 4 — it makes the corpus
  state legible: "none" now means someone said so.
- **The asymmetry that deferred it has expired.** The only argument
  against the backfill was the live Phase 4 comparison. That
  measurement is closed.

## Consequences

- `REQUIRED_FIELDS` in `src/radialrig/doc_core.py` carries the twelfth
  field; one new C2 test was seen failing before the change.
- Records proposed through `propose_record` must now carry the field or
  the C2 gate refuses them. No verb change was needed; the verbs gate on
  C1–C7 and callers author the text.
- The migration script was transient and deleted after its run; the
  diff is its record.
