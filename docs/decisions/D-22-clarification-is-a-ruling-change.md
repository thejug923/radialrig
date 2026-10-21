---
id: D-22
title: A clarification is a ruling change
type: decision
status: in-force
grade: verified
owner: thejug923
scope: RadialRig
effective: 2026-09-04
last-verified: 2026-09-05
supersedes: none
replaced-by: none
depends-on: D-18
---

**Status: in-force.**

## Question

When a governed record gains text that states a rule where it was
silent, does the change need an archive entry?

## Ruling

Yes. **A clarifying addition is an 8.1 ruling change**, and it archives
the prior position — the record as it stood — like any other.

A plan is the one record class this ruling does not reach. Its execution
log grants no permission, so appending to it withdraws none, and section
8 exempts it ([D-24](D-24-plan-execution-log.md)). A plan's
prescriptions carry this ruling unchanged.

Several clarifications landing together take one amend and one archive
entry. The cost is per amendment, not per sentence.

## Why

**Silence licenses.** A rulebook that does not forbid a thing permits it.
Text that ends a silence withdraws a permission, so the set of compliant
conduct changes — which is what a changed ruling is. "Archiving a
silence" was never the ask; 8.1 step 2 archives *the record as it stood*,
and a record always has a prior text.

This is observed, not argued. Before SPEC-1 gained a test for choosing
between 8.1 and 8.3, PLAN-0003 Phase 1 activity 2 run 4 chose 8.3 and was
correct. After the test, the same change is an 8.1 and that run chose
wrongly *(verified 2026-08-15)*. Before section 3 defined the grades,
promoting `decided` to `verified` on a routine re-check broke no rule,
and one agent proposed it. After [D-19](D-19-grade-semantics.md), it is
forbidden. Two clarifications, two licenses withdrawn.

**The alternative is ungateable.** [D-18](D-18-authoring-write-path.md)
constraint 1 gives `amend_ruling` a `metadata` mode that archives nothing
and is safe because it rejects any call whose body text differs from the
record on disk. A `clarify` mode cannot carry that gate: a clarification's
body *must* differ. Nothing mechanical could separate a clarification
from a reversal, and C5 compares only `status` and `id` against `HEAD`
*(verified 2026-08-15 in the testbed, whose validator then lived at
`tools/validate_docs.py`; the C5 block now sits in
`src/radialrig/validate_docs.py` — body changes pass every check
silently)*. An author wanting to reverse a ruling with no archive record
would only have to call it a clarification. That is D1, the defect
PLAN-0003's Phase 1 verdict says the verbs exist to prevent, reopened on
the prose path.

**The archive is the only history the tools can read.** `search_history`
over `docs/archive/` is the sole archive-serving path, and no tool in
the read path reads git history *(verified 2026-08-15 in the testbed,
whose server then lived at `tools/retrieval_server.py` and made no git
call; the server now at `src/radialrig/retrieval_server.py` makes one,
reading the C5 baseline for `check_docs`, and it serves no archive)*. A
change that archives nothing is invisible to every reader who follows
the route map. Section 8 calls git the trail *underneath* the procedure,
not a substitute for it.

## Consequences

**The verb surface does not change.** `amend_ruling` in `position` mode
already carries this case, with D-18 constraints 8 and 9 applying
unchanged. No fourth verb, no third mode. That PLAN-0003 Phase 2 changes
by zero lines is an argument for this ruling, not a convenience of it.

One residue stays open: an addition that plausibly withdraws no license
at all. This ruling sends it to 8.1 anyway, on the ground that the
author is the worst-placed person to judge which of their own additions
are neutral.
