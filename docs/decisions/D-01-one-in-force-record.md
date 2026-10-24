---
id: D-01
title: One in-force record per key and scope
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-09-04
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: none
---

**Status: in-force.**

## Ruling

Exactly one `in-force` record exists per decision key and scope. That is
the mechanical guarantee: C1 in `radialrig-validate` fails on a second
in-force record holding the same `id` in the same scope.

Two records under different keys that answer the same question are also
a defect, but a review-caught one. No check reads what a record means;
the change procedure (SPEC-1 section 8) and the reviewer who reads the
diff catch it. Text that promises "one record per question" describes
that review duty, not the check.

## Why

Agents do not choose between two contradictory documents. They merge them.
Uniqueness must be a mechanical property, not a request. Two independent
model reviews in the testbed research corpus reached this conclusion
separately.
