---
id: D-40
title: The memory skill's design rulings
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-10-22
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: D-30, D-36, D-37
---

**Status: in-force.**

## Question

The owner ordered a new shipped skill that keeps an agent's own
per-project memory store useful: what gets read, what is junk, and how
memory is selected, written, organized, consolidated and retrieved. Two
independent research passes (one on the Anthropic side by this
repository's agents, one on the OpenAI side by an outside model) were
merged on 2026-09-27 and left six questions only the owner could answer.
Losing the answers would send the design session back to re-arguing them
from the research, which admits them under
[D-10](D-10-governed-scope.md).

## Ruling

Ruled by the owner on 2026-09-27, one question at a time, with the
trade-offs stated:

1. **Which memory lines bind.** A memory line that records the owner's
   own decision (an idea the owner killed, a preference the owner stated)
   binds the agent that reads it. A lesson the agent inferred on its own
   is a claim to verify against the code, the record or the person before
   it is acted on. The skill writes each kind so a reader can tell them
   apart. [D-30](D-30-no-canonical-decisions-in-memory.md) is unchanged:
   the ruling itself lives in a governed record, and the binding memory
   line points at it or records the owner's words.
2. **Standing changes are suggested once and applied only on a yes.**
   When the skill finds that the same correction has recurred, it
   suggests, in plain words and once, moving it into a permanent place:
   the person's instruction file, an enforced check such as a hook or
   test, or a governed record through the authoring path. It applies
   nothing until the person says yes. This is the ask-first tier of the
   consent vocabulary [D-37](D-37-shipped-skill-constraints.md) rule 6
   states for session-handoff.
3. **The promotion threshold is the second recurrence.** The first time
   a correction is saved it stays in memory; the second time the same
   correction is needed, the skill makes the suggestion in rule 2.
4. **Store shape follows the harness.** On Claude Code the skill works
   only on the harness's native auto-memory store, the one the harness
   loads into every session, and builds no parallel store. On Codex the
   skill writes nothing into the harness's native memory store, which the
   agent may not edit, and instead keeps one small RadialRig-kept,
   project-scoped memory file that is local to the machine and ignored
   by git; durable notes that must reach the person go to the handoff,
   the instruction file (with consent under rule 2), or a governed
   record.
5. **Task state is not a memory kind.** Current progress, failing checks,
   blockers and the next action live in the project's plan or report and
   in the session handoff, never in the memory store. The running-notes
   companion the owner declined on 2026-08-18 stays declined; the skill
   removes that class of entry from memory rather than creating it.
6. **The skill ships only after its effect is measured here.** The paired
   fresh-session ablation the research used (the same inviting prompt run
   in fresh sessions against a scratch copy of a store with and without
   one memory entry, graded blind) is ported into `evals/` before the
   skill's first release, so the skill may state a measured effect under
   [D-37](D-37-shipped-skill-constraints.md) rule 2.

## Why

The owner chose the recommended option on five of the six trades and, on
the store shape, chose to give Codex its RadialRig-kept file from the
first version rather than after a pilot. The research behind each trade
is ungoverned; on 2026-09-29 it was stowed in a private cold-storage
archive (the ledger in `research-staging/README.md` gives its name,
location and digest), last held in the tree at commit c663b26. The findings that
decided the trades, in brief: a one-line owner decision in the always-loaded index measurably
stopped fresh sessions from re-proposing a killed idea, while corrections
the agent wrote for itself were violated about half the time in measured
settings, which separates rule 1's two kinds; the harness delivers its
native index to every session and nearly every helper agent for free,
which nothing a skill builds can match, and Codex forbids its agent from
writing the native store, which fixes rule 4; running logs of work in
progress were the most-written, least-read and most-stale entries in this
repository's own store, which settles rule 5; and D-37 rule 2 forbids an
unmeasured claim, which requires rule 6.

## Consequences

- The execution plan for the skill, PLAN-0020, executed under this
  record and [D-36](D-36-skills-kit-form.md),
  [D-37](D-37-shipped-skill-constraints.md) and
  D-38, and closed on
  2026-09-29.
- `evals/` gained the model-driven ablation of rule 6 on 2026-09-27,
  before the skill entered the skill catalog on 2026-09-28.
- The memory-management research topic under `research/` carries lines
  the same research found wrong or incomplete; those corrections go
  through the promotion procedure in `research-staging/README.md`, not
  through this record.
