---
id: SPEC-3
title: Authoring tool interface
type: spec
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-10-22
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: D-06, D-09, D-18, D-19, D-21, D-22, D-23, D-35
---

**Status: in-force.** This is the interface contract for the authoring
server ruled in [D-18](decisions/D-18-authoring-write-path.md).

# SPEC-3: Authoring tool interface

The server exposes exactly three verbs, one per lifecycle move in
[SPEC-1](spec.md) section 8. It ships separately from the retrieval
server, and a mount registers both (D-18 constraint 6). Both servers
share `src/radialrig/doc_core.py` (constraint 7).

Every response is a JSON object with a `kind` discriminator, as SPEC-2
does. Fixed schema cost for all three verbs together stays at or under
3,000 bytes, counted fully loaded (PLAN-0003 invariant 7) — the same
bound [SPEC-2](spec-retrieval-tools.md) sets for the five read tools.
*(Amended 2026-09-05: the owner raised the bound from 2,500 bytes when
the required `summary` input put the three verbs at 2,720 — smarter
models read larger documents fine, and the ~55 extra tokens each
session carries are acceptable.)*

Every verb declares `readOnlyHint: false` in its tool annotations, and
every read tool declares `readOnlyHint: true`. A client tells the two
surfaces apart from the wire, not from the source.

## 1. What every verb guarantees

These six hold for all three verbs. A verb section below states only
what it adds.

1. **The caller writes the text.** Every verb takes whole file text and
   validates it. No verb authors, rewrites, reformats or repairs a body,
   and no verb fills in a field the caller left wrong (D-18 constraint
   5). The one exception is the archive entry's `last-verified`, in
   section 4.
2. **Validate on a copy, then write.** The verb materializes the proposed
   file set on a temporary copy of the corpus and runs C1–C7 against the
   result. The working tree is touched only after the gate passes
   (constraint 2).
3. **All paths or none.** A verb touching several files either lands
   every one or writes nothing (constraint 3).
4. **The write stops at the working tree.** No verb writes git: none
   stages, commits, moves HEAD or reverts. Two reads are allowed and
   named — the C5 baseline (section 3) and the archive entry's
   `last-verified` (section 4.1). An agent reads the diff; a person
   commits. Reversal is `git checkout -- docs/` (constraint 4).
5. **No verb deletes a record, and no verb changes an `id`**
   ([D-06](decisions/D-06-stable-identifiers.md), constraint 1).
6. **Every verb takes a required `summary`** — one plain sentence saying
   what the ruling now says, at most 300 characters once surrounding
   whitespace is trimmed. The caller writes it (guarantee 1). An empty
   or overlong summary comes back `rejected` with a reason naming the
   fix, and nothing is written. The `applied` result echoes the
   sentence, and the agent shows it to the person for approval before
   anything is committed
   (D-35 commitment 2; PLAN-0012
   phase 2).

## 2. The gate is delta-based

The temporary copy carries the whole corpus, so an absolute gate — reject
unless C1–C7 come back clean — would reject correct changes for failures
they did not cause.

**The rule.** Compute the failure set before the change, on the live
corpus, and after the change, on the copy. Both runs use the same
baseline and the same date, inside one call. **The verb rejects when the
after-set holds any failure line the before-set did not.** A failure
present in both is inherited, and it does not block the write.

Failure lines are compared whole, check prefix and path included. A line
naming a path the change created is new by construction, so a change
cannot inherit an existing failure onto a new file.

**Every applied result reports what it inherited.** A caller must not
read a successful write as a clean corpus. Clearing an inherited failure
is ordinary work, not a precondition. The evidence for the delta rule is
in D-18.

## 3. C5 and the baseline

C5 compares each record against its prior state, which a temporary copy
does not have. The verb reads the prior frontmatter from the **live**
corpus it is amending and passes it to the validator as the required
`baseline` argument ([D-23](decisions/D-23-c5-baseline-is-supplied.md)).

The verb implements no transition or id logic of its own. The rule stays
in the validator and the verb supplies the data.

## 4. `amend_ruling`

Rewrites an in-force record in place and keeps the `id`. **The mode is
required and never inferred** (D-18 constraint 1).

**Input:**

```
{ "id": string,
  "mode": "position" | "metadata",
  "text": string,                       // whole file, rewritten
  "summary": string,                    // one plain sentence (section 1)
  "archive": { "path": string, "text": string },   // position mode only
  "propagate"?: [ { "path": string, "text": string } ] }
```

### 4.1 `position` — the ruling changes (SPEC-1 8.1)

The prior position moves into `docs/archive/` in the same call. A
clarifying addition is this case
([D-22](decisions/D-22-clarification-is-a-ruling-change.md)).

**The verb writes the archive entry's `last-verified` itself** (D-18
constraint 9). The value is the one **git HEAD** holds for the record
being archived — the date that position was last checked while in force,
never the date of archival. A caller-supplied value in that field is
overwritten without error, because there is no case where the caller's
value is the right one. When HEAD carries no such record — proposed and
amended before any commit — the verb falls back to the disk, which is
then the only date there is. D-18 gives the reason HEAD is read and
not the working tree.

Every other field of the archive entry is the caller's, and the gate's
to enforce: C7 for placement and the fence line, C3 for the head
paragraph and its `replaced-by` pointer, C2 for the required fields.

### 4.2 `metadata` — a re-check, no position change (SPEC-1 8.6)

Archives nothing and propagates nothing. **`last-verified` is the only
field that may differ from the record on disk.** The verb rejects a call
whose body text differs by one character, and rejects a change to any
other frontmatter field — `grade` included, because a re-check records
evidence and never moves a grade
([D-19](decisions/D-19-grade-semantics.md)).

No check can observe whether the re-check happened. The mode does not
try. It makes the smuggling structurally impossible: the mode that
archives nothing cannot carry a body change through it.

### 4.3 Propagation (SPEC-1 8.5)

`propagate` carries the dependents read down the `depends-on` chain. Each
entry bumps that record's `last-verified`, whether or not its text
changed, so the review leaves a mark.

**The verb identifies the chain; the caller does not declare it.** It
walks `depends-on` from the changed record, direct and transitive, and
**rejects a call that leaves any dependent untouched**, naming the ones
missed. A caller who propagates to two of three dependents gets an
`error` naming the third, and nothing is written. `metadata` mode
changes no position, so nothing propagates and the chain is not
walked.

**A propagation entry may not change what a dependent rules** (D-18
constraint 8). Mechanically: the dependent's `## Ruling` section must
come back byte-identical. A dependent with no `## Ruling` section admits
no body edit through propagation at all.

When the change contradicts a dependent's ruling rather than its wording,
the verb **rejects the call and names the dependent**. That record takes
its own `amend_ruling` in `position` mode, with its own archive entry.

## 5. `retire_ruling`

Moves a record to `archived` status and into `docs/archive/`, keeping its
filename and its `id`. The body arrives rewritten in the past tense; C7
enforces the fence line and the placement, and nothing enforces the tense
([D-21](decisions/D-21-archive-voice-fenced-not-checked.md)).

**Input:**

```
{ "id": string,
  "text": string,                       // whole file, past tense, status archived
  "summary": string,                    // one plain sentence (section 1)
  "successor"?: { "path": string, "text": string },
  "propagate"?: [ { "path": string, "text": string } ] }
```

**Without `successor`** the record is retired and nothing replaces it
(SPEC-1 8.2). `replaced-by` stays `none`, and the body says why the
question left scope.

**With `successor`** the question passes to a new record (SPEC-1 8.3).
The verb writes the successor under its new `id` and requires the retired
record's `replaced-by` to name it. The two files land together or not at
all: a half-written 8.3 is the contradictory state
[D-09](decisions/D-09-conflict-stop-rule.md) stops on.

Choosing between 8.1 and 8.3 is the caller's, under the test
[SPEC-1](spec.md) section 8.3 states. If the change is an 8.1 it belongs
in `amend_ruling`.

## 6. `propose_record`

Records a new question under a new `id` (SPEC-1 8.4). Nothing is archived
and nothing propagates.

**Input:** `{ "path": string, "text": string, "summary": string }`

**The `id` must be held by no file in the corpus**, in force or archived.
C1 catches only a duplicate among in-force records, so the verb tests the
wider rule SPEC-1 section 8 states: at most one file holds a given `id`,
and an id is never reused
([D-06](decisions/D-06-stable-identifiers.md)). A reused id returns
`error` naming the file that holds it.

## 7. Result shapes

| kind | Fields | Meaning |
| --- | --- | --- |
| `applied` | `paths` (written, sorted), `inherited` (failure lines present before and after), `summary` (the sentence, echoed for the person's approval) | Every path landed |
| `rejected` | `failures` (the lines the change added), `paths` (would have been written), `reason` | Nothing was written |
| `error` | `message` | The verb refused the call before the gate ran |

`rejected` is the ordinary outcome of a change that does not yet pass. It
is not an error, and the caller is expected to iterate against it: C2, C3
and C7 stay *validated*, not *guaranteed*, because the caller authors the
text (D-18 constraint 5, and the consequence recorded there). An empty
or overlong `summary` comes back this way too, with `failures` empty and
the `reason` naming the fix (section 1, guarantee 6).

`error` covers every refusal the verb makes before the gate runs, such
as an `id` no record holds or one the text changes or reuses, a path
already taken or outside the governed layout, a `metadata` call that
changes more than `last-verified`, or a propagation that is
incomplete, unstamped or changes what a dependent rules. A call the
tool schema refuses, a missing field, an empty `id`, `path` or
`text`, or a `mode` other than the two, gets the protocol layer's
validation error and never reaches the verb. The server never
substitutes a default, never guesses a mode, and never converts a
rejection into an error or the reverse.

## 8. The corpus root

No verb reads `RADIALRIG_DOCS_ROOT` as an invitation. Pointed at another
corpus it writes to that corpus; the write stops at the working tree,
and D-18 records the risk.
