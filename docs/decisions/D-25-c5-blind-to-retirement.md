---
id: D-25
title: C5 is blind to a retirement outside the write path
type: decision
status: in-force
grade: verified
owner: thejug923
scope: RadialRig
effective: 2026-08-29
last-verified: 2026-09-05
supersedes: none
replaced-by: none
depends-on: D-23
---

**Status: in-force.**

## Question

C5 compares a record against the prior state at its path. A retirement
moves the file ([SPEC-1](../spec.md) sections 8.2 and 8.3), so the
destination path has no prior state and the record arrives looking new.
Can the standalone validator close that hole?

## Ruling

**No, and it is accepted as unreachable there.** The write path closes it
and the hand path does not.

- `src/radialrig/doc_txn.py` re-keys the baseline: a `Move` gives the
  destination the source's prior frontmatter, so C5 sees the real
  transition and the real `id`. A move that renames an `id` is rejected
  ([D-23](D-23-c5-baseline-is-supplied.md)).
- `uv run radialrig-validate` and `check_docs` keep the hole. Neither
  is told a move happened, and neither may guess.

**A hand retirement therefore passes C5 without being checked by it.**
That is a stated limit, not a defect to route again. It reopens only if
the validator gains a trustworthy way to learn that a path moved.

## Why

**Git cannot supply the move in time.** Rename detection needs both sides
in the index. An unstaged move reads as a deleted file and an untracked
one — `git status --porcelain -M` reports `D a/x.md` and `?? b/`, and
`git diff -M --name-status HEAD` reports the delete alone. The rename
appears only after `git add` *(verified 2026-08-15 by experiment)*. The
validator runs on the unstaged working tree, which is the point: it
checks what an author just wrote, before anything is staged.

**Pairing by `id` instead of by path would be worse.** The validator
could match a vanished path to a new file carrying the same `id` and
call it a move. That guess fails on exactly the case C5 exists to catch —
a retirement that renames the `id` leaves nothing to pair, so the check
would go quiet on the defect rather than fire. A check that guesses is
worse than one with a stated limit.

**The transaction is not guessing.** It is told. The caller names the
source and the destination in one `Move`, so the link between them is
input, not inference. That difference is why the same hole is closable in
one place and not the other.

## Consequences

**This is a defect class the null arm cannot reach.** PLAN-0003 Phase 4
compares the verb path against a hand baseline, and an 8.3 that renames
its `id` passes the hand path and fails the verb path. It was found by
building the tool, not by running the comparison (testbed gap G17).

The repository has never performed an 8.2 or 8.3 on a live record, so no
existing record slipped through. The archive holds 8.1 history entries,
which are new files beside a record that stayed in place, and C5 reads
those correctly.

A reader who finds C5 quiet on a hand retirement has found this ruling,
not a bug.
