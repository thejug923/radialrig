---
id: SPEC-1
title: Minimal documentation spec
type: spec
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-10-22
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: D-01, D-02, D-03, D-04, D-05, D-06, D-09, D-10, D-19, D-22, D-24, D-26, D-28, SPEC-3
---

**Status: in-force.** This is the operational rulebook for governed documents.

# SPEC-1: Minimal documentation spec

The decisions behind each rule live in [docs/decisions/](decisions/).

Sections:

1. What is governed
2. Document classes and layout
3. Metadata contract: fields, grades, freshness
4. Status vocabulary
5. Head-of-file rule
6. Voice rule for the archive
7. Authority order
8. Change procedure: 8.1 ruling change, 8.2 retirement, 8.3 retirement
   with a successor, 8.4 new record, 8.5 propagation, 8.6 metadata
   maintenance, 8.7 plan completion

## 1. What is governed

A document is governed when it answers a question admitted under
[D-10](decisions/D-10-governed-scope.md): losing or contradicting its answer
would regress ratified behavior. Everything else (notes, research, generated
output) is ungoverned evidence and must not claim authority.

## 2. Document classes and layout

| Class | Lives in | Purpose |
| --- | --- | --- |
| decision | `docs/decisions/` | One current answer to one governed question |
| plan | `docs/plans/` | Execution plan with phases and done-conditions |
| spec | `docs/` | Operational rulebook (this file) |
| archive | `docs/archive/**` | Past positions and completed material |
| evidence | `docs/archive/**` | Archived source material; fenced, never authoritative |

A spec states rules an agent applies, not the argument behind them; the
argument belongs in a decision record. A spec's filename begins with
`spec`. The checks collect `docs/spec*.md` at the top level of `docs/`,
and the three record directories recursively; a governed file placed
anywhere else is not checked.

Default retrieval excludes the archive. The form this repository's
search tooling honors is `!**/docs/archive/**`
([D-04](decisions/D-04-archive-exclusion.md), which carries the test
for it and the dated measurement).

Filenames carry the record key: `D-10-governed-scope.md`. A filename must
never make a superseded record read as current truth.

Skeletons for the decision, plan and spec classes ship in the package
at `src/radialrig/data/seed/docs/templates/`. A mount writes them to
`docs/templates/`, which this repository ignores as mount output; edit
the packaged copies.

## 3. Metadata contract

Required frontmatter on every governed record:

| Field | Meaning |
| --- | --- |
| `id` | Stable key, e.g. `D-10`. Never reused, never renumbered. |
| `title` | Short name of the question or artifact |
| `type` | `decision`, `plan`, `spec`, or `evidence`; `evidence` appears only under `docs/archive/` |
| `status` | One value from section 4 |
| `grade` | `decided`, `verified`, or `assumed` |
| `owner` | Who can change the record |
| `scope` | Where the record applies |
| `effective` | Date the ruling took effect |
| `last-verified` | Date the record was last checked against reality |
| `supersedes` | Key of the question this replaced, or `none` |
| `replaced-by` | Key of the successor, or `none` |
| `depends-on` | Records whose change must propagate here, or `none` |

One optional field: `verify-by` (the event that regrades an `assumed`
record; the event removes the field). `depends-on` is required: a record
with no dependencies declares `none`
([D-28](decisions/D-28-depends-on-required.md)).

`grade` records what kind of evidence stands behind the ruling.
`last-verified` records when the record was last checked. **A re-check
moves `last-verified` and never `grade`** ([D-19](decisions/D-19-grade-semantics.md)).

| Grade | Meaning | What moves it |
| --- | --- | --- |
| `decided` | The owner's judgment. Argument, not observation. | An observation recorded in the record moves it to `verified`. |
| `assumed` | A working position taken without evidence. Carries `verify-by`. | The `verify-by` event, which moves it to `decided` or `verified` and removes the field. |
| `verified` | Checked against observed reality. The record states what was observed. | Nothing. A record does not lose that history because time passed; it goes stale instead, which C6 measures. |

A new record starts at `decided`, or at `assumed` with a `verify-by` when
it is taken without evidence.

C6 fails an in-force or draft record whose `last-verified` date is
more than 90 days old. The validator runs on every pull request into
the protected branch, so a stale record fails that gate until a real
re-check under section 8.6 moves the date; editing the date without the
re-check is a defect.

## 4. Status vocabulary

Exactly one value means "this governs now": **`in-force`**.

| Status | Meaning | Legal transitions |
| --- | --- | --- |
| `draft` | Proposed, governs nothing | → `in-force`, → `archived` |
| `in-force` | The current answer | → `completed`, → `archived` |
| `completed` | A finished plan, kept as a closure summary (section 8.7) | → `archived` |
| `archived` | A past position; evidence only | none |

At most one `in-force` record may exist per `id` and scope
([D-01](decisions/D-01-one-in-force-record.md)).

## 5. Head-of-file rule

The status, and the `replaced-by` pointer when one exists, appear in the
frontmatter **and** in the first body line, bold:
`**Status: archived.** Replaced by [D-12](...).`
Agents read from the top and often read only the top
([D-03](decisions/D-03-head-of-file-status.md)).

## 6. Voice rule for the archive

Archived decision records use the past tense and open with:
`**Status: archived.** This is a record of a past position, not an
instruction.` Imperative text in a dead record still commands an agent
([D-05](decisions/D-05-past-tense-archive.md)). Evidence files carry the
same fence line but keep their original text; the past-tense rewrite
applies to decision records only.

The sentence may wrap across lines, as it does above. C7 and the
`replaced-by` half of C3 read the whole head paragraph, not the first
line.

## 7. Authority order

When records seem to compete, apply in order:

1. Correct scope.
2. Status `in-force`.
3. Follow `replaced-by` links before using content.
4. Still tied? **Stop and report the conflict.** Never resolve it silently
   ([D-09](decisions/D-09-conflict-stop-rule.md)).

## 8. Change procedure

Four kinds of change reach a governed record. Git preserves the full audit
trail under all four. **Every change to an in-force record's body takes 8.1
or 8.3, with two plan exemptions: a plan meeting its done-conditions
takes 8.7, and a plan's execution log — run tables, results, verdicts,
the closure of its own gaps — is appended to, or corrected to match
what was observed, without archiving.** A plan's prescriptions, which
say what the next phase builds, take 8.1 like any ruling; a change that
is hard to place is a prescription change
([D-24](decisions/D-24-plan-execution-log.md)). Beyond those, only 8.6
touches a record without archiving, and 8.6 changes no body text. One
rule spans them all: **at most one file holds a given `id`.** That is
why 8.1 gives its archive entry a derived key and 8.2 and 8.3, which
move the whole file, do not.

### 8.1 The ruling changes, the question stays the same

**A clarifying addition is this case**
([D-22](decisions/D-22-clarification-is-a-ruling-change.md)). The prior
position is the record as it stood, and step 2 archives it like any
other; several clarifications landing together take one amend and one
archive entry.

1. Rewrite the in-force record in place. Keep the `id`. Move `effective`
   and `last-verified` to the date of the change.
2. Write the prior position into `docs/archive/` as a past-tense entry
   (section 6). Name the file for the record and the period it held:
   `D-10-history-2026-03.md`.
3. That entry takes a **derived id** — `D-10-history-2026-03` — never the
   record's own id, which the live file still holds. It sets `replaced-by`
   to the record's id, and names that record in its head paragraph, which
   C3 enforces.
4. The rewritten record links forward to the entry under a `## History`
   heading.
5. Propagate (section 8.5).

### 8.2 The record is retired and nothing replaces it

The question has left scope. No successor answers it.

1. Move the whole file into `docs/archive/`, keeping its filename. The
   file keeps its `id`, because no other file now holds that key.
2. Set `status: archived` and leave `replaced-by` at `none`.
3. Rewrite the body in the past tense (section 6) and say in it why the
   question left scope.
4. Propagate (section 8.5).

### 8.3 The record is retired and a successor takes the question

Use this when the question itself changes shape. A reversal of the same
question is 8.1.

**The test.** Write the record's question as an interrogative, as its
`## Question` section does where one exists; otherwise derive the
interrogative from the title. Ask it of the new regime. If the new
regime answers it, the question stands and this is **8.1** — keep the
`id`. If the question no longer has an answer, because what it asks
about is gone or no longer decides anything, the record is retired:
this is **8.3** when a new record takes the question up in its new
shape, and **8.2** when nothing does. When the test is genuinely
unclear, use 8.1 ([D-06](decisions/D-06-stable-identifiers.md)
carries the worked case).

1. Write the successor as a new record under a new `id`, with
   `supersedes` set to the retired record's id.
2. Move the retired file into `docs/archive/`, keeping its filename and
   its `id`. Set `status: archived` and `replaced-by` to the successor's
   id, which C3 forces into its head paragraph.
3. Rewrite the retired body in the past tense (section 6). The successor
   links back to it under a `## History` heading.
4. Propagate (section 8.5).

### 8.4 A new record

A new question gets a new `id`, never a reused one. Set `supersedes` and
`replaced-by` to `none`, and `effective` and `last-verified` to the date
the record takes effect. Nothing is archived and nothing propagates.

### 8.5 Propagation

Read every record naming the changed record in `depends-on`, then every
record depending on those, to the end of the chain. Edit the text wherever
the change now contradicts it. Update `last-verified` on **every record
read**, whether or not its text changed, so the review leaves a mark a
reader can see. No validator check covers this step, and a direct
edit has no guard. The authoring verbs walk the chain themselves and
refuse a call that leaves a dependent untouched or unstamped
([SPEC-3](spec-authoring-tools.md) section 4.3); whether the text was
edited wherever the change contradicts it stays a judgment.

### 8.6 Metadata maintenance

A `last-verified` bump after a real re-check changes no position. It
archives nothing and propagates nothing. Editing the date without the
re-check is a defect, not a shortcut.

A new question gets a new `id`. A reversal of the same question keeps its
`id` ([D-06](decisions/D-06-stable-identifiers.md)).

### 8.7 Plan completion

When every phase of a plan has met its done-condition, the plan moves to
`completed` in three steps
([D-26](decisions/D-26-completed-plan-form.md)):

1. Compress the plan file, as it stands, into
   `docs/archive/<id>-full-<YYYY-MM>.md.gz`
   (`gzip -c docs/plans/<file>.md > docs/archive/<id>-full-<YYYY-MM>.md.gz`).
   The gzip is cold storage: no tool reads it, `search_history` does not
   match it, and reading it again is a manual `gunzip`.
2. Rewrite the plan file in place as a closure summary: what shipped,
   each phase's outcome, the final measured results, the rulings the
   plan produced, and the path from step 1 with the decompress command.
   A summary states every status it reports as of the completion date
   and says so ("at closure on 2026-09-22, none had shipped in a
   release"), never as a standing fact, because the summary is not
   maintained after closure. Keep the `id`. Set `status: completed` and
   move `last-verified` to the completion date.
3. Step 1 is the preservation. The rewrite owes no 8.1 archive entry,
   and the summary changes again only to move to `archived`, with one
   further edit permitted: a status sentence that has come to read as
   current is dated to the completion date, and `last-verified` moves
   to the date of that edit.

A completed summary is reachable through `get_ruling` by exact key only
(SPEC-2 section 1). Free-text queries never return it.
