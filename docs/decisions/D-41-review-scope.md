---
id: D-41
title: How review agents are scoped
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-11-01
last-verified: 2026-11-01
supersedes: none
replaced-by: none
depends-on: none
---

**Status: in-force.**

## Question

What may an agent asked to review work look at, what happens to what it
finds, and how many rounds does a review run?

## Ruling

1. **A review brief names its scope.** Every brief that asks an agent to
   review or check work names what the agent reads and the requirement it
   reads it against: a plan's prescription, a phase's brief, an issue, or
   the owner's decisions. That holds for a code review, a review of a plan
   or of records, a security review, a release review, and any agent asked
   to check work. The brief asks the agent to review the work that was
   done against that requirement and to check that it is complete. It
   never asks the agent to look for problems, or to look beyond the scope.
2. **Inside the scope, the reviewer reports every finding**, each with its
   severity.
3. **Outside the scope, the reviewer only lists.** It keeps a separate
   list, a line or two for each item it noticed: where it is and what it
   seems to be. It does not investigate, test, rate or suggest a fix for
   anything outside its scope.
4. **Only what would make the result wrong is fixed.** Of the findings
   inside the scope, the work fixes the ones that would make its result
   wrong or break a hard requirement. The others, and every listed item
   from outside the scope, go into the review's record unfixed, and the
   owner decides where each one goes. Nothing outside the scope is fixed
   without the owner's yes.
5. **A confirm round checks the fixes only.** A round after the first asks
   only whether each known finding's fix works, its probe or test failing
   before the fix and passing after. It does not look for new problems.
6. **Two rounds, then stop.** A review runs at most two rounds. If
   something after the second still seems to need review, the agent says
   so to the owner instead of starting a third.

## Why

The owner ruled this first in another of his projects, on 2026-10-27,
after a code review's second round returned 22 findings more: "any time we
are using review agents, we must present them with a limited scope and
specify that they are only to catalog items outside of their scope, not
review them." The same day he set how a brief words its ask: "ensure that
we use language that does not ask an agent to look for problems or expand
the scope, but specifically to review the work that was done, and ensure
completeness." Rules 1 to 4 are that ruling.

Rule 5 is his ruling in this repository on 2026-10-23, during the 0.1.20
release review, whose confirm rounds kept returning ever-rarer filesystem
cases: "If you keep asking sub-agents to find problems, they will find
problems. Make sure they're looking for the solutions to the known
problems, not looking for new ones." Rule 6 is his rule from another of
his projects on 2026-10-26, "we usually get degradation" after a second
round: later rounds re-argue settled points rather than find real defects.

What brought them here: the review of issue #72 on 2026-11-01. Its brief
listed what to look for, and round one returned one major finding from one
reader and five majors and two minors from the other. All were fixed. Two
would have made the result wrong; most of the rest were cases an ordinary
project would not meet, and the code roughly doubled. The owner: "I would
like to make sure we didn't end up inviting scope creep somewhere because
I was not anticipating this effort to take this long." He adopted all six
rules the same day: "we need our own version of that here, most
certainly."

Rule 2 keeps asking for every finding inside the scope, with its severity,
and rule 4 sorts them in a separate pass, because a reviewer told to report
only the serious ones may report less (the research guidance on
anti-hobbling). The limit is on where a reviewer looks, not on how much it
reports there.

Admission under [D-10](D-10-governed-scope.md): without this record,
reviews here return to open-ended problem hunting, which the owner has
corrected four times since 2026-10-23.

## Consequences

- Each review brief gains a scope section and asks for an "outside the
  scope" list in its output.
- The person running the review sorts the in-scope findings by rule 4
  before anything is fixed, and the review's record says which were fixed
  and which were left for the owner.
- A security review's scope is security, so its findings there are inside
  its scope.
- This record does not say who reviews; the owner chooses the readers for
  each review.
- The issue #72 review ran before this record, and its record
  (`reports/issue-72-2026-11-01/review/`) shows every finding fixed.
- The shipped plan-review skill states round rules of its own for the
  projects it ships to. This record governs reviews run in this
  repository; bringing the skill in line is a separate change.
