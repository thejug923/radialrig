---
id: SPEC-2
title: Default git environment
type: spec
status: in-force
grade: decided
owner: {{OWNER}}
scope: {{SCOPE}}
effective: {{DATE}}
last-verified: {{DATE}}
supersedes: none
replaced-by: none
depends-on: SPEC-1
---

**Status: in-force.** This is the operational rulebook for the git
environment of {{SCOPE}}.

# SPEC-2: Default git environment

This is the shipped default. Where this project already runs a different
regime, amend this record under [SPEC-1](spec.md) section 8.1 rather than
leaving it in force and unfollowed.

## 1. The principle

A boundary earns its place only if crossing it runs a check that can say
no. A gate that never fails filters nothing, and it is removed. Every
branch below exists because its boundary runs a check the previous branch
did not.

## 2. Branches

| Branch | Purpose | Check at the boundary into it |
| --- | --- | --- |
| `develop` | The shared integration line, where day-to-day commits land. The current working state. | Local checks pass (tests, validator). No human gate. No pull request. |
| `staging` | Conditional. Created only when a check exists that `develop` cannot run, for example tests against a deployed environment. | That environment's checks, run on a pull request. |
| the default branch | Ratified truth. Direct pushes are blocked. | Full checks in CI, on a pull request. A fresh-context reader reads the diff. A human approves from the summary and presses Merge. |

The default branch is whichever this project already uses — commonly
`main` or `master`. Protecting it against direct pushes is configured at
the hosting service, not in this repository; until that protection
exists, the gate in section 5 is a convention rather than a boundary.

A short-lived branch an agent cuts for its own isolation is a working
convenience, not a rung: it runs no check of its own and merges into
`develop` with a plain git merge.

When `staging` does not exist, promotion is one step: `develop` to the
default branch.

## 3. Pull requests, and where we use them

Use pull requests only at protected boundaries: into the default branch,
and into `staging` when it exists. The agent opens the pull request and
attaches the evidence: test output, validator output, and one plain
sentence for each ruling the request carries. CI runs the checks on the
request itself. A fresh-context agent reads the diff against what was
asked and reports what it found. The human reads the sentences and the
evidence and presses Merge.

Merges into `develop` use plain git merges. A pull request there would be
a gate the human approves every time, and an always-approved gate filters
nothing.

## 4. CI budget

Hosted CI runs in exactly three cases:

1. A pull request into a protected branch.
2. The scheduled gardening run that re-runs the checks against the clock.
3. A manual trigger.

Never on every push. The inner loop runs the same checks locally, which
costs nothing. A record the validator reports stale (C6) fails the
pull-request gate until a real re-check under [SPEC-1](spec.md) section
8.6 moves its `last-verified` date.

## 5. The human gate

There is one human gate: the pull request into the default branch. An
agent never merges it. The human approves the request from the summary
sentences and the evidence attached to it. CI's checks and a
fresh-context reader of the diff carry what the human does not read. The
gate stays meaningful while those two run and can say no. A human who
presses Merge over a failed check or without the reader's report has
rebuilt an always-approved gate with more steps; the fix at that point
is to shrink the diffs, not to add another gate.

## 6. Governed records in this project

Governed records keep their stricter rule, unchanged by this spec. A
change to a governed record stops at the working tree; a person approves
the ruling in one sentence, an agent reads the diff, and the commit
follows, on whatever branch the work runs. That holds for a change made
through the authoring verbs and for a direct edit under [SPEC-1](spec.md)
section 8 alike. The committed record then travels to the default branch
through the same pull request as everything else.
