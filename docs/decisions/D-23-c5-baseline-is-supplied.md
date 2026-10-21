---
id: D-23
title: C5's prior state is supplied by the caller
type: decision
status: in-force
grade: verified
owner: thejug923
scope: RadialRig
effective: 2026-08-16
last-verified: 2026-09-05
supersedes: none
replaced-by: none
depends-on: D-18
---

**Status: in-force.**

## Question

C5 checks that a record's status transition is legal and its `id` stable,
by comparing the record against its previous state. Where does that
previous state come from?

## Ruling

**The caller supplies it.** `validate()` takes a required, keyword-only
`baseline`: a mapping from root-relative path to the record's prior
frontmatter. A path absent from the mapping has no prior state, so C5 has
nothing to compare for it and reports nothing. An empty mapping states
that no record has a prior state.

Reading git is a separate, named provider, `git_head_baseline(root)`. It
**raises** when git cannot answer for that root. Returning an empty
mapping on failure would read as "every record is new", which is the
silence this ruling ends.

Two consequences bind:

- **The argument is required.** An optional baseline is a check that
  skips itself when a caller forgets, and forgetting is exactly what
  happened.
- **A check that cannot run is not a passing check.** `check_docs`
  outside a git work tree runs C1–C4, C6 and C7 and reports one C5 line
  saying the transition and id checks did not run. It used to report
  `ok=true`.

## Why

**C5 was silently dead on every corpus but one.** The old code ran `git
show HEAD:<rel>` with `<rel>` relative to the corpus root, and git
resolves that path against the *repository* root. A corpus addressed by
`--root` therefore matched nothing and C5 skipped in silence — or, on a
colliding path, compared a fixture record against an unrelated governed
file. A testbed pilot ran fourteen changes with C5 covering none of
them *(verified 2026-08-15 by
experiment)*. The path bug is fixed with `HEAD:./<rel>`, and a regression
test seeds a decoy at the repository root so a wrong resolution cannot
pass it.

**The path fix alone does not reach Phase 2.** [D-18](D-18-authoring-write-path.md)
constraint 2 validates the change on a temporary copy of the corpus. A
copy has no history of its own, whatever the path resolution does, so a
check that derives history from where the files sit can never be right
there. Supplying the prior state separates the question "what changed"
from the question "where does this corpus live".

**The alternative was a third implementation.** PLAN-0003's Phase 2 text
had the transaction enforce `LEGAL_TRANSITIONS` and id stability itself,
against the live corpus. That puts the same rule in two places and drifts
— the defect [D-13](D-13-retrieval-interface.md) constraint 4 and
[D-18](D-18-authoring-write-path.md) constraint 7 both exist to prevent.
The validator keeps owning the rule; the caller supplies only the data.

## Consequences

The transaction builds its baseline from the **live** corpus it is
amending and passes it to `validate()` against the temporary copy. That
is the whole of what Phase 2 needs, and it removes the C5 exception the
plan had carried.

One limit stays open and is not closed by this ruling. A temporary copy
placed inside an unrelated git work tree yields a readable `HEAD` and no
matching files, so `git_head_baseline` would return an empty mapping
rather than raise. Nothing in the transaction path calls it that way —
the transaction supplies the live corpus baseline instead — but a future
caller could. The guard is the required argument, which forces every
caller to say where its prior state came from.
