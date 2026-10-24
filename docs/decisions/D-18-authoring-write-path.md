---
id: D-18
title: Authoring write path
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-09-05
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: D-03, D-05, D-06, D-13, D-35
---

**Status: in-force.**

## Ruling

Governed truth gets a write path. It is the separate decision that
[D-13](D-13-retrieval-interface.md) constraint 5 deferred. The write path
must:

1. Expose exactly three verbs, one per lifecycle move in
   [SPEC-1](../spec.md) section 8: `propose_record` (8.4),
   `amend_ruling` (8.1 and 8.6) and `retire_ruling` (8.2 and 8.3).
   `amend_ruling` takes an **explicit mode**, `position` or `metadata`,
   required and never inferred: a verb that guesses which kind of
   change it is holding will guess wrong on the case that matters.
   `metadata` archives nothing and propagates nothing, and the verb
   **rejects a `metadata` call whose body text differs from the record
   on disk**, so the mode that archives nothing cannot carry a position
   change through it. `retire_ruling` takes an **optional successor**;
   a successor is a parameter, never a fourth verb. No verb deletes a
   record and no verb changes an `id`
   ([D-06](D-06-stable-identifiers.md)). The inputs and result shapes
   are [SPEC-3](../spec-authoring-tools.md)'s.
2. **Validate before it writes.** Apply the change to a temporary copy of
   the corpus, run checks C1–C7 against the result, and touch the working
   tree only when every check passes. A failure returns a typed `rejected`
   result carrying the failure lines and writes nothing.
3. Be **atomic across files**. `amend_ruling` touches at least two paths.
   Either every path lands or none does. A half-applied change is a
   corpus defect, not a partial success.
4. **Stop at the working tree.** No verb writes git — the two permitted
   reads are named in SPEC-3 section 1. A person approves the ruling
   and a person commits; an agent reads the diff
   (D-35 commitment 2).
5. Take the **whole file text from the caller**. The tool validates and
   rejects; it never authors, rewrites, or repairs a body. Frontmatter,
   the head-of-file line ([D-03](D-03-head-of-file-status.md)), and the
   archive voice line ([D-05](D-05-past-tense-archive.md)) are the
   caller's to get right and the gate's to enforce.
6. Live in a **second MCP server, registered at mount beside the
   retrieval server**, so capture is on from the first session and off
   only when the owner removes the entry
   (D-35 commitment 6; owner ruling
   2026-09-04). The retrieval server stays read-only, so D-13
   constraint 5 binds it unchanged. The write schema stays inside
   SPEC-3's 3,000-byte bound (raised from 2,500 by owner ruling
   2026-09-05), which is what every session pays for carrying it.
7. Share `src/radialrig/doc_core.py` with the validator and the retrieval
   server.
   A third implementation of truth selection is a drift defect
   ([D-13](D-13-retrieval-interface.md) constraint 4).
8. **Refuse a propagation edit that changes a dependent's ruling.**
   Propagation (SPEC-1 section 8.5) may edit a dependent's text and bump
   its `last-verified`. It may not change what that dependent rules. When
   the change contradicts a dependent's ruling rather than its wording,
   the verb rejects the call and names the dependent, and that record
   takes its own `amend_ruling` in `position` mode.
9. **Write the archive entry's `last-verified` itself.** The value is the
   date the archived position was last checked while it was in force,
   carried from the record being archived — never the archival date. The
   caller does not supply it.

Constraints 8 and 9 are the two defects PLAN-0003 Phase 1 activity 2
found surviving the null loop, stated as verb behavior. They are what
this ruling now claims the verbs prevent, and the Abandon test measures
them.

## Why

An agent can already edit these files with ordinary file tools, so the
write path adds one thing: it makes an invalid corpus unreachable. That
is the move [D-13](D-13-retrieval-interface.md) made for the read side.
Advisory prose about the change procedure is exactly what an agent skips
under context pressure, and the cost of skipping it is silent — a broken
`replaced-by` chain or a leaked archive record reads as normal truth
until someone runs the validator.

**The null alternative is edit the file, then call `check_docs`.** It
needs nothing built. This ruling claims two things beat it: `check_docs`
reports on a corpus already broken, where the gate prevents the break;
and an amend spans two files, which the null loop cannot land atomically.
Both are arguments, not evidence *(assumed 2026-08-15 — a review found
this ruling asserted them without measurement)*. The testbed measured the
null loop before any verb code existed, and the verbs beat it. An adopter
who doubts the premise should measure the null loop first.

Constraint 5 is the caller's choice, made 2026-08-15 against the
recommendation that the tool build the frame. The consequence is stated
plainly: C2, C3 and C7 stay *validated*, not *guaranteed*, so a caller
will iterate against the gate. The gate still holds the corpus.

One known risk. A write server pointed at another corpus through
`RADIALRIG_DOCS_ROOT` writes to that corpus. Until 2026-09-04
constraint 6 kept the server off by default for that reason; the owner
then ruled that an empty corpus is the larger risk (D-35 section 10,
first falsifier). The write still stops at the working tree (constraint
4), so a misdirected write is a diff a person sees before any commit.

**The delta gate is evidence, not preference.** SPEC-3 section 2 rejects
a change only for failures it adds, and inherited failures do not block
it. PLAN-0003 Phase 1 seeded one stale record and ran seven changes over
it. Five runs refused to bump a `last-verified` they had not re-checked,
citing the rule against gardening dates. An absolute gate would have
rejected four correct changes on a failure none of them caused
*(verified 2026-08-15)*.

**Constraint 8 is the defect the verbs exist to prevent.** Phase 1 run 2
cut a dependent from two backoff steps to one through an 8.5 propagation
edit, archived nothing, and passed every check.

**Constraint 9 reads git HEAD, not the working tree, because a stamp
gets there first.** A propagation under 8.5 writes `last-verified` on
every dependent it reads. A dependent stamped by one call and amended by
the next carries today's date on disk at the moment of the amend, and
archiving that value claims a verification nobody performed. The two
calls are each correct, so no single-call check sees the pair. HEAD is
the last state the owner reviewed and the one copy no uncommitted stamp
can have touched. When HEAD carries no such record, the disk is the only
date there is, and SPEC-3 section 4.1 names that fallback.

**The id check in `propose_record` covers the negative case Phase 1
never reached.** Run 6 was asked to file a new ruling under an id
already in force. It refused by judgment, citing three spec clauses, so
C1 never fired. Judgment is not a check; SPEC-3 section 6 makes it one.

Admission under [D-10](D-10-governed-scope.md): losing this ruling
returns authoring to ungated direct edits, which regresses the C1–C7
guarantee the read path now depends on.
