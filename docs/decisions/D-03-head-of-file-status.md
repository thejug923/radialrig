---
id: D-03
title: Status appears at the head of every record
type: decision
status: in-force
grade: verified
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

The status, and any `replaced-by` pointer, appear in the frontmatter **and**
in the first body line of every governed record.

## Why

Agents read files from the top and often read only the top. A warning below
the fold does not exist. Originated as model self-report in the testbed
research corpus. Verified by a testbed pilot (2026-08-14): in ten
fresh-agent runs, agents
selected records by frontmatter status, and the conflict-case agent cited
the status fields explicitly. Limits: one model, one run per case,
fixture-scale corpus.
