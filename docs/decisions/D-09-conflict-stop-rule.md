---
id: D-09
title: Authority order and the conflict-stop rule
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-08-16
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: none
---

**Status: in-force.**

## Ruling

An agent selects the governing record by, in order: correct scope, then
status `in-force`, then following `replaced-by` links. When two records
still hold equal authority, the agent stops and reports the conflict. It
never resolves the conflict silently, and never infers authority from
timestamps or confident wording.

## Why

A silent resolution hides a governance defect and converts it into quiet
wrong behavior. A reported conflict gets fixed once; a silent one recurs.
See the testbed research corpus.
