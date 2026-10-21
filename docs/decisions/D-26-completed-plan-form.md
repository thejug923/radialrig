---
id: D-26
title: A completed plan is a summary; its full text goes to cold storage
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-09-27
last-verified: 2026-09-27
supersedes: none
replaced-by: none
depends-on: SPEC-1, SPEC-2, D-13
---

**Status: in-force.**

## Question

When this question was asked, [SPEC-1](../spec.md) section 4 said a
finished plan rests at `status: completed`, "kept for reference", and
did not say in what form or reached how. Before this ruling a
completed plan kept its full text in
`docs/plans/`, where no tool could serve it: too stale for `get_ruling`
(in-force only), outside `search_history`'s scope (`docs/archive/**`
only). The ruling was first made in the research testbed this
repository was derived from, where two closed plans sat in that hole
and two running ones, at 72.5 KB and 27.6 KB, were heading into it, or
would have stayed `in-force` and cost `get_ruling` roughly 18k tokens
per whole-body answer. Those testbed plans are not in this repository;
the question is the same here, and this repository's completed plans
take the form ruled below.

## Ruling

A completed plan takes three parts:

1. **The plan file becomes a closure summary.** On completion the file in
   `docs/plans/` is rewritten in place as a brief record of what shipped:
   the objective in a line, each phase's outcome, the final measured
   results, the rulings the plan produced, and where the full text went.
   It keeps its `id` and sits at `status: completed`.
2. **The full text is compressed into the archive.** The plan file as it
   stood at completion is gzipped to
   `docs/archive/<id>-full-<YYYY-MM>.md.gz`. This is cold storage by
   intent: no tool reads it, `search_history` does not match it, the
   validator does not scan it, and reading it takes a manual decompress.
   The summary names the path and the command.
3. **An exact key reaches the summary.** `get_ruling` serves a
   `completed` record when the query equals its key. Free-text matching
   stays in-force-only, so a dead plan never answers a live question.

The procedure is SPEC-1 section 8.7. The interface change is SPEC-2
section 1. Part 3 is the one carve-out from
[D-13](D-13-retrieval-interface.md) constraint 1, which sends only
`in-force` records through the default retrieval tools; D-13 is named
in `depends-on` so a change there reaches this ruling.

## Why

- **The weight was real and unbounded.** `get_ruling` has no size bound,
  and a plan grows its execution log for as long as it runs. Serving a
  closed plan whole made the largest context event on the tool path a
  document nobody needed whole.
- **The owner chose gzip over a plain archive entry** (2026-08-16),
  knowing the cost: the full text becomes invisible to `search_history`
  and to grep. The stated intent is that the full plan exists only for
  the case where the summary proves insufficient, and the summary says
  where it is. The compression step is the preservation — the summary
  rewrite owes no separate 8.1 archive entry.
- **Keyed-only reach keeps the default-retrieval invariant intact where
  it matters.** Default retrieval still returns in-force records only
  for every query that involves matching; the exception is the one case
  with no selection in it, a caller asking for a record by its exact
  name.

## Consequences

- Every completed plan in this repository is a closure summary under
  this ruling, and each names its own gzip path. This record keeps no
  list of them: the catalog is the files under `docs/plans/` at
  `status: completed`, and `get_ruling` reaches each summary by its
  exact key. A list here went stale every time a plan closed.
- A plan's 8.1 archive entries from its running life
  (`docs/archive/<id>-history-*.md`) stay where they are; the gzip is
  the plan as it stood at the end, not a replacement for them.
- The `ruling` result carries a `status` field so a caller can tell a
  completed summary from an in-force answer without parsing the body.
