---
name: plan-review
description: Use when reviewing a plan, design doc, spec, or migration plan, when a review enters a second round, or when a finding re-argues a settled decision. Files the plan's structural absences as findings, keeps to a stated scope, stops after two rounds.
radialrig:
  id: plan-review
  version: 3.0.0
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
- State the scope before reading: what is reviewed, and the requirement it is
  read against (the plan's stated goal, the request or issue that asked for
  it, the person's decisions). Review the work against that requirement and
  check that it is complete. Anything noticed outside the scope gets a line
  or two in a separate list, where it is and what it seems to be; it is not
  investigated, tested, rated, or given a suggested fix. A review asked to
  look for problems keeps finding more, and the plan grows with every round.
- A brief that hands the review to another agent says the same: it names the
  scope and the requirement, asks for the work to be reviewed against it and
  checked for completeness, asks for the separate outside list, and never
  asks the agent to look for problems.
- Two rounds, then stop. If something still seems to need review after the
  second, say so to the person instead of starting a third.

## What this produces

A findings list that reviews the plan as a plan. Reviewing a plan as a plan is
a separate job from reviewing its content: a content review grades what the
document says, and this one grades whether the document states what a plan has
to state. Every finding inside the scope carries its severity and its mark
against the fix test below; the list of what lies outside the scope sits apart.

## When it fires

The user asks for a review of a plan, design doc, spec, or migration plan; a
review cycle enters a second round; or a finding re-argues something the plan
already decided. This governs the reviewer's conduct only, including a
brief that hands the review on.

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

## Which findings get fixed

Report every finding inside the scope, each with its severity: a reviewer told
to report only the serious ones reports less. Then mark the ones that would
make the result wrong, because the plan would build the wrong thing,
contradict a decision in force, or leave a check unable to tell pass from
fail. The author fixes those between rounds. Every other finding, and every
item on the outside list, goes into the review's record unfixed, one line
each, and the person decides where it goes. Nothing outside the scope is
fixed without the person's yes.

## When the review runs a second round

- **Freeze, then batch fixes.** Pin the version under review; fixes land after
  the round closes. Never edit under review.
- **Round two checks the fixes.** It asks whether each fix from round one
  closes its finding, and re-proves the pinned invariant set on the revised
  document. It does not look for new problems.

## Done when

The scope is stated at the top of the findings; each of the three absences is
filed as a finding or noted as supplied; every finding inside the scope
carries its severity and its mark against the fix test; the outside list is
present, even when empty; a second round names the fixes it checked and the
invariant set it re-proved; no third round ran without the person's yes; and
the document under review carries no edit made by the reviewer. Findings are
reported tersely: the absence or defect, where it shows, and what settles it.
