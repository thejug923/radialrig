---
id: SPEC-1
title: Minimal documentation spec
type: spec
status: in-force
grade: decided
owner: {{OWNER}}
scope: {{SCOPE}}
effective: {{DATE}}
last-verified: {{DATE}}
supersedes: none
replaced-by: none
depends-on: none
---

**Status: in-force.** This is the operational rulebook for governed
documents in {{SCOPE}}.

# SPEC-1: Minimal documentation spec

This file states the rules. The records that answer this project's own
governed questions live in [docs/decisions/](decisions/), which starts
empty. The reasoning behind each rule below ships with RadialRig; what
governs here is the text in this file.

The `owner` field above names the role that can change this record.
Replace it with whoever holds that role in this project.

## 1. What is governed

A document is governed when losing or contradicting its answer would
regress ratified behavior. Everything else — notes, research, generated
output — is ungoverned evidence and must not claim authority.

## 2. Document classes and layout

| Class | Lives in | Purpose |
| --- | --- | --- |
| decision | `docs/decisions/` | One current answer to one governed question |
| plan | `docs/plans/` | Execution plan with phases and done-conditions |
| spec | `docs/` | Operational rulebook (this file) |
| archive | `docs/archive/**` | Past positions and completed material |
| evidence | `docs/archive/**` | Archived source material; fenced, never authoritative |

A spec's filename begins with `spec`. The checks collect `docs/spec*.md`
at the top level of `docs/`, and the three record directories
recursively; a governed file placed anywhere else is not checked.

The git environment this project runs on — branches, the human gate, the
CI budget — is [SPEC-2](spec-git-environment.md).

Default retrieval excludes the archive. The exclusion rule itself lives
in the route map (root `AGENTS.md`), inside the fenced section that
RadialRig maintains. That copy survives upgrades, and this file cannot
amend it; that is deliberate. What this file holds is the one part the
fence cannot: the exclusion form this project uses in its own search
tooling. The seed wrote the line below untested; when a measurement in
this project's harness disagrees with it, amend it under section 8.1.

This project's exclusion form is `!**/docs/archive/**`.

The mount seeded one archived record under `docs/archive/` so the form
has something to be tested against, and that record carries the marker
phrase `radialrig-archive-exclusion-marker`. A search for the marker
with the form applied finds no hit under `docs/archive/`; a hit there
means the form failed.

Filenames carry the record key: `D-10-example-question.md`. A filename
must never make a superseded record read as current truth.

Skeletons for the decision, plan and spec classes live in
[docs/templates/](templates/).

## 3. Metadata contract

Required frontmatter on every governed record:

| Field | Meaning |
| --- | --- |
| `id` | Stable key, e.g. `D-10`. Never reused, never renumbered. |
| `title` | Short name of the question or artifact |
| `type` | `decision`, `plan`, `spec`, or `evidence` (the last only under `docs/archive/`) |
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
record; the event removes the field). `depends-on` is required so that
"no dependencies" is a declaration, never an absence: a record that omits
the field is indistinguishable from one whose dependencies were never
recorded. The declaration binds only the record that carries it; the 8.5
walk reads declarations, and a false `none` is as invisible to it as a
missing field.

`grade` records what kind of evidence stands behind the ruling.
`last-verified` records when the record was last checked. **A re-check
moves `last-verified` and never `grade`.**

| Grade | Meaning | What moves it |
| --- | --- | --- |
| `decided` | The owner's judgment. Argument, not observation. | An observation recorded in the record moves it to `verified`. |
| `assumed` | A working position taken without evidence. Carries `verify-by`. | The `verify-by` event, which moves it to `decided` or `verified` and removes the field. |
| `verified` | Checked against observed reality. The record states what was observed. | Nothing. A record does not lose that history because time passed; it goes stale instead, which C6 measures. |

A new record starts at `decided`, or at `assumed` with a `verify-by` when
it is taken without evidence.

C6 fails an in-force or draft record whose `last-verified` date is more
than 90 days old. The validator runs on every pull request into the
protected branch ([SPEC-2](spec-git-environment.md) section 4), so a
stale record fails that gate until a real re-check under section 8.6
moves the date; editing the date without the re-check is a defect.

## 4. Status vocabulary

Exactly one value means "this governs now": **`in-force`**.

| Status | Meaning | Legal transitions |
| --- | --- | --- |
| `draft` | Proposed, governs nothing | → `in-force`, → `archived` |
| `in-force` | The current answer | → `completed`, → `archived` |
| `completed` | A finished plan, kept as a closure summary (section 8.7) | → `archived` |
| `archived` | A past position; evidence only | none |

At most one `in-force` record may exist per `id` and scope.

## 5. Head-of-file rule

The status, and the `replaced-by` pointer when one exists, appear in the
frontmatter **and** in the first body line, bold:
`**Status: archived.** Replaced by [D-12](...).`
Agents read from the top and often read only the top.

## 6. Voice rule for the archive

Archived decision records use the past tense and open with:
`**Status: archived.** This is a record of a past position, not an
instruction.` Imperative text in a dead record still commands an agent.
Evidence files carry the same fence line but keep their original text;
the past-tense rewrite applies to decision records only.

The sentence may wrap across lines, as it does above. C7 and the
`replaced-by` half of C3 read the whole head paragraph, not the first
line.

## 7. Authority order

When records seem to compete, apply in order:

1. Correct scope.
2. Status `in-force`.
3. Follow `replaced-by` links before using content.
4. Still tied? **Stop and report the conflict.** Never resolve it
   silently.

## 8. Change procedure

Four kinds of change reach a governed record. Git preserves the full
audit trail under all four. **Every change to an in-force record's body
takes 8.1 or 8.3 — except a plan meeting its done-conditions, which takes
8.7.** Only 8.6 touches a record without archiving, and 8.6 changes no
body text. One rule spans them all: **at most one file holds a given
`id`.** That is why 8.1 gives its archive entry a derived key and 8.2 and
8.3, which move the whole file, do not.

**A plan is the one exception, and it runs in one direction only.** A plan
holds two kinds of text. Its **prescriptions** say what the next phase
builds; changing one is an 8.1 ruling change like any other, because the
next phase builds what the plan says. Its **execution log** — run tables,
results, verdicts, the closure of its own gaps — records what happened
when a phase ran. Appending to the log archives nothing, and correcting
the log to match what was observed archives nothing. When a change is
hard to place, treat it as a prescription change.

### 8.1 The ruling changes, the question stays the same

**A clarifying addition is this case.** Text that states a rule where the
record was silent changes the ruling, because the silence licensed what
the new text forbids. The prior position is the record as it stood, and
step 2 archives it like any other. Several clarifications landing
together take one amend and one archive entry.

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
`## Question` section already does. Then ask it of the new regime. If the
new regime answers it, the question stands and this is **8.1** — keep the
`id`. If the question no longer has an answer, because what it asks about
is gone or no longer decides anything, this is **8.3**.

Worked case: "Who approves a deploy?" against a new regime of two
countersigning captains still has an answer — the captains. That is 8.1,
not 8.3, however different the ruling looks. Contrast "Which agent ships
container logs?" against a managed pipeline nobody configures: the
question has no answer, and it leaves scope under 8.2.

When the test is genuinely unclear, use 8.1. It keeps the `id`, and the
`id` is the handle every reader and every `depends-on` field follows.

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
Start from the matching skeleton in [docs/templates/](templates/).

### 8.5 Propagation

Read every record naming the changed record in `depends-on`, then every
record depending on those, to the end of the chain. Edit the text wherever
the change now contradicts it. Update `last-verified` on **every record
read**, whether or not its text changed, so the review leaves a mark a
reader can see. No mechanical check covers this step.

### 8.6 Metadata maintenance

A `last-verified` bump after a real re-check changes no position. It
archives nothing and propagates nothing. Editing the date without the
re-check is a defect, not a shortcut.

A new question gets a new `id`. A reversal of the same question keeps its
`id`.

### 8.7 Plan completion

When every phase of a plan has met its done-condition, the plan moves to
`completed` in three steps:

1. Compress the plan file, as it stands, into
   `docs/archive/<id>-full-<YYYY-MM>.md.gz`
   (`gzip -c docs/plans/<file>.md > docs/archive/<id>-full-<YYYY-MM>.md.gz`).
   The gzip is cold storage: no tool reads it, `search_history` does not
   match it, and reading it again is a manual `gunzip`.
2. Rewrite the plan file in place as a closure summary: what shipped,
   each phase's outcome, the final measured results, the rulings the
   plan produced, and the path from step 1 with the decompress command.
   Keep the `id`. Set `status: completed` and move `last-verified` to
   the completion date.
3. Step 1 is the preservation. The rewrite owes no 8.1 archive entry,
   and the summary changes again only to move to `archived`.

A completed summary is reachable through `get_ruling` by exact key only.
Free-text queries never return it.
