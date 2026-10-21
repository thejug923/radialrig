---
id: D-20
title: The route map enumerates no record ids
type: decision
status: in-force
grade: verified
owner: thejug923
scope: RadialRig
effective: 2026-08-17
last-verified: 2026-09-27
supersedes: none
replaced-by: none
depends-on: D-08
---

**Status: in-force.**

## Question

May the route map name individual record ids or a range of them?

## Ruling

The route map names **directories and files**. It never enumerates record
ids and never states a range of them.

A citation is different and stays allowed: a rule in the route map may
cite the record that grants it, as rule 2 cites D-04. That is one id
attached to one rule, and it goes stale only if that record is retired —
which the change procedure catches. An enumeration goes stale every time
a record is added.

Where a reader needs the catalog, they call `list_rulings` or read
`docs/decisions/` directly.

## Why

An enumeration is a second copy of the corpus contents, kept by
separate edits, in a file no check covers. The route map is not a governed record, so C1–C7
never read it, and drift there is invisible.

This is observed, not predicted. `AGENTS.md` described
`docs/decisions/` as holding "D-01 … D-12" while D-13 through D-18 were
in force — stale by six records, in the one file every agent loads first
*(verified 2026-08-15)*. In PLAN-0003 Phase 1 activity 2 the same defect
appeared in the fixture corpus: two of four agents whose change made the
route map's range wrong repaired it, and two left it, one of them
flagging the staleness and declining to act because no rule told it to.
Four agents, one situation, two different answers.

Removing the enumeration removes the maintenance obligation rather than
adding a rule to enforce it. Nothing to maintain cannot drift.

## Consequences

The alternative was a new check that resolves every id the route map
names. That was rejected: it makes an ungoverned file check-bearing, and
it pays for the enumeration's upkeep forever instead of deleting the
enumeration once.
