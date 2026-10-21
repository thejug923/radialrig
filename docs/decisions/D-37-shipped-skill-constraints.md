---
id: D-37
title: What a shipped skill may assert, and the session-handoff rewrite constraints
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-09-04
last-verified: 2026-10-02
supersedes: none
replaced-by: none
depends-on: D-36
---

**Status: in-force.**

## Question

The skills kit ([D-36](D-36-skills-kit-form.md)) ships skills as
product content. A skill is an instruction document an agent obeys, so
what it asserts about itself, what it may do without asking, and what
it may carry from its sources are governed questions: losing these
answers would regress ratified behavior. What binds a shipped skill,
and what binds the session-handoff rewrite specifically?

## Ruling

### Every shipped skill

1. **Frontmatter is closed.** Keys are exactly `name`, `description`,
   and a `radialrig` block carrying at least `id` and `version`.
   Versions follow semantic versioning; a rewrite-class change bumps
   the major.
2. **No unmeasured claim.** A skill asserts a performance property
   only when this repository's `evals/` measured it. Conformance to
   the research corpus may be stated as conformance, never as
   measured effect.
3. **The scrub.** No shipped skill byte names an outside repository,
   a private testbed, a drive-letter or home-relative absolute path,
   or the internal directories of the source it was rewritten from.
   A mechanical sweep enforces this.
4. **Size.** A `SKILL.md` stays under 500 lines and its description
   under 250 characters, per the documentation-bloat guidance the
   research corpus states.

### The session-handoff rewrite

5. **Offer, never fire.** On completion of a substantive unit of
   work the agent may offer the handoff once, as an option. The human
   drives the handoff; the skill never runs unasked, never reads its
   own transcript, and never measures context. Ruled by the owner
   2026-08-18: the harness does not report context reliably, so
   completion of work is the offer point.
6. **Consent runs in three tiers**, replacing the source's blanket
   gate (ruled by the owner 2026-08-18):
   - **Run, then report:** teardown of what this session started, the
     retention move into the archive, the handoff write itself, and
     the one ignore-rule line for an untracked `handoffs/`.
   - **Ask first:** anything irreversible or externally visible — a
     commit, a stash, a worktree removal, any write outside
     `handoffs/` other than that ignore-rule line.
   - **Never:** deleting a handoff, and killing anything this session
     did not start.

   Where the first two tiers meet, the named item wins: the
   ignore-rule line runs and is reported; every other write outside
   `handoffs/` asks first.
7. **Retention moves, never deletes.** Surplus handoffs move to the
   archive directory; nothing is deleted.
8. **The ignore-rule control is honest.** Where the host already
   tracks `handoffs/`, the skill reports the unenforced control and
   writes nothing.

## Why

A skill ships to strangers' projects, where no owner reads a diff
before an agent obeys it. The constraints that this repository
enforces by review must therefore ride inside the skill's own
mechanical checks. The evidence limit exists because the source
skill's measurements graded a different text under a contaminated
baseline; the rewrite is conformant, not measured, until `evals/`
says otherwise.

## Consequences

- `src/radialrig/tests/test_skill_sources.py` is the mechanical sweep
  for rules 1–4 and the rewrite's structural rules.
- The rewrite shipped as version 3.0.0; later versions move under
  rule 1.
- The execution plan PLAN-0006, in `docs/plans/`, executed under this
  record.
