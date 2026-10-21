---
id: D-24
title: A plan's execution log is not a ruling
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-08-16
last-verified: 2026-09-05
supersedes: none
replaced-by: none
depends-on: D-22
---

**Status: in-force.**

## Question

[D-22](D-22-clarification-is-a-ruling-change.md) sends every change to an
in-force record's body to [SPEC-1](../spec.md) section 8.1. A plan
records its own execution as it runs. Does each run table, verdict and
gap closure owe an archive entry?

## Ruling

No. **A plan holds prescriptions and an execution log, and only the
prescriptions carry rulings.**

- A **prescription** says what a later phase builds, or what a phase must
  satisfy before the plan calls it done. Changing one is an 8.1 ruling
  change with its archive entry, like any decision.
- The **execution log** reports what happened when a phase ran: run
  tables, measured results, verdicts, the closure of gaps the plan itself
  opened. Appending to it archives nothing. Correcting it to match what
  was observed archives nothing either — a log that misreports an
  observation is wrong, and a wrong log is repaired, not preserved.

**When the placement is genuinely unclear, it is a prescription change.**
The test mirrors section 8.3's: ask whether the next phase would build
something different after the change. If it would, the text was a
prescription.

This ruling reaches plan records only. Decisions and specs keep D-22
whole.

## Why

**D-22's argument does not extend here.** That ruling rests on silence
licensing: text that ends a silence withdraws a permission, so the set of
compliant conduct changes, and a changed ruling is exactly that. An
execution log grants no permission. Nobody's conduct becomes
non-compliant when a run table gains a row, so nothing is withdrawn and
there is no prior position to archive.

**The rulebook and the practice disagreed, and the practice was right.**
A testbed plan gained a reseed record, run parameters, a run table,
a verdict and four gap closures
across four commits on 2026-08-15, and archived none of them *(verified
2026-08-15: `docs/archive/` holds no PLAN-0003 entry from those
commits)*. Under the text D-22 had installed, every one owed an archive
entry. Writing them would have filled the archive with snapshots of a
plan whose prescriptions never moved, and buried the three decision-class
entries that carry real reversals.

**The cost of the opposite ruling falls on the reader.** `search_history`
is the only archive-serving path. Every plan snapshot it returns is one
more result between a reader and the position they came for.

## Consequences

The first change this ruling governs is its own commit: PLAN-0003's Phase
2 bullet on C5 is a prescription — it tells the transaction what to build
— so it takes 8.1 and its archive entry, while the Phase 2 findings
appended beside it take none.

An archive entry is still cheap and never wrong. An author who cannot
place a change may write one; this ruling removes the obligation, not the
option.

The residue D-22 left open widens slightly here. A prescription can be
edited in ways that read as clerical — a reworded step, a corrected file
path — and this ruling sends all of them to 8.1. That is deliberate, on
the same ground D-22 gave: the author is the worst-placed person to
judge which of their own edits are neutral.
