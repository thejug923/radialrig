---
id: D-02
title: Every governed record carries the metadata contract
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-08-16
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: D-28
---

**Status: in-force.**

## Ruling

Every governed record carries the required frontmatter fields defined in
[SPEC-1](../spec.md) section 3: id, title, type, status, grade, owner,
scope, effective, last-verified, supersedes, replaced-by, depends-on.

## Why

Status, time, and ownership must be machine-readable fields an agent can
filter on. Agents do not reliably infer any of them from prose. The spec
holds the field definitions; this record holds the obligation.
`depends-on` joined the list on 2026-08-16 under
[D-28](D-28-depends-on-required.md), so that an empty dependency list is
a declaration rather than an absence.
