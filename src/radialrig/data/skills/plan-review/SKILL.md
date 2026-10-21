---
name: plan-review
description: Use when reviewing a plan, design doc, spec, or migration plan, when a review enters a second round, or when a finding re-argues a settled decision. Files the plan's structural absences as findings and holds later rounds to a pinned invariant set.
radialrig:
  id: plan-review
  version: 2.1.0
---

## First, the rules that bind

- Name the absence. Do not compensate for it: supplying the missing structure
  yourself and grading against it keeps the gap out of the findings.
- Nothing here edits the document under review; fixes belong to the author,
  between rounds.
- Settled decisions are out of scope absent evidence the decision did not
  have. Decline by citing the decision ID, and route a genuine reopen as a
  new decision rather than as plan content.
- A failed review does not become a pass. An owner override of a failed
  review is recorded as waived, not passed; the review's stated criteria are
  never amended to admit what failed them.

## What this produces

A findings list that reviews the plan as a plan. Reviewing a plan as a plan is
a separate job from reviewing its content: a content review grades what the
document says, and this one grades whether the document states what a plan has
to state.

## When it fires

The user asks for a review of a plan, design doc, spec, or migration plan; a
review cycle enters a second round; or a finding re-argues something the plan
already decided. This governs the reviewer's conduct only.

## Name these three absences

Each is a finding in its own right, filed whether or not the content is good.

1. **No stated outcome.** The plan never says what hurts, for whom, and what
   observable result means it worked. A plan whose completion boundary is an
   inventory of artifacts and process events has no outcome - say so. Nothing
   can be steered to an unstated target, and no amount of process discipline
   substitutes for one.
2. **Ungraded claims.** Load-bearing statements are not marked decided (who,
   when, ID), verified (checked against what), or assumed (why that is safe
   enough). Finding individual undefined terms is not this finding; the
   absence of the grading itself is. A disputed assumption gets the smallest
   probe that settles it, never an architecture.
3. **No pinned invariant set.** Nothing states what must be re-proved on
   every revision regardless of how narrow the round is. Without it, a
   corrections-only pass scores clean and the next full review fails on what
   the correction broke. Ask what round two must re-prove beyond the deltas,
   and pin the answer in the findings.

If the plan supplies one of these, say so and move on. The finding is the
absence, not the quality.

## When the review runs more than one round

These rules govern rounds two onward.

- **Freeze, review whole, batch fixes.** Pin the version under review; the
  round reviews that text; fixes land after the round closes. Never edit
  under review.
- **Fix the class, then sweep.** An accepted finding's fix is its defect
  class, and the round does not close until the document is swept for
  siblings. Fixing the single instance seeds the next round.
- **The correction round is planned work** - bounded, with its own exit
  condition, and with the pinned invariant set re-proved on the revised
  document.
- **Narrow, count, stop.** Round one is full depth; later rounds cover
  blockers plus the pinned invariants. Track new findings against refound
  ones. A full round finding nothing new, with the stated criteria met, is
  done. Two dry rounds is the hard stop; what remains is a preference
  backlog.

## Done when

Each of the three absences is filed as a finding or noted as supplied; every
accepted finding names its defect class; a round past the first states its
scope, the invariant set it re-proved, and the new-versus-refound tally; and
the document under review carries no edit made by the reviewer. Findings are
reported tersely: the absence or defect, where it shows, and what settles it.
