---
id: D-06
title: Identifiers and paths stay stable
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

A record's `id` and path stay stable across revisions. A changed ruling
rewrites the record in place and keeps the `id`. A new `id` means a new
question, never a reversal of the same question. Ids are never reused.

## Why

After context summarization, a path survives and a paragraph does not. A
pointer is enough only if the pointer stays valid. A stable grep key beats
"the auth decision, the later one." The `id` is the handle every reader
and every `depends-on` field follows, which is why SPEC-1 section 8.3
sends an unclear case to 8.1 rather than to a new key.

**Worked case.** "Who approves a deploy?" asked of a new regime of two
countersigning captains still has an answer — the captains. That is a
changed ruling under the same `id` (SPEC-1 8.1), however different the
ruling looks. "Which agent ships container logs?" asked of a managed
pipeline nobody configures has no answer at all; that question leaves
scope (SPEC-1 8.2). A new `id` (SPEC-1 8.3) is for a question that
changed shape, not for a question that changed answer.
