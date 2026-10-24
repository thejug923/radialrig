---
id: SPEC-4
title: Default git environment
type: spec
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-10-21
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: D-18, D-35, D-39, SPEC-1
---

**Status: in-force.** This is the operational rulebook for the git
environment. Sections 2 to 6 are the regime of the private repository
where RadialRig is built, and the shipped default of the RadialRig
product. Section 7 covers the public repository that RadialRig's base is
exported to. The principle in section 1 holds for both.

# SPEC-4: Default git environment

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
| `master` | Ratified truth. Direct pushes are blocked by host branch protection. | Full checks in CI, on a pull request. A fresh-context reader reads the diff. A human approves from the summary and presses Merge. |

A short-lived branch an agent cuts for its own isolation is a working
convenience, not a rung: it runs no check of its own and merges into
`develop` with a plain git merge.

When `staging` does not exist, promotion is one step: `develop` to
`master`.

## 3. Pull requests, and where we use them

We use pull requests only at protected boundaries: into `master`, and
into `staging` when it exists. The agent opens the pull request and
attaches the evidence: test output, validator output, and one plain
sentence for each ruling the request carries. CI runs the checks on the
request itself. A fresh-context agent reads the diff against what was
asked and reports what it found. The human reads the sentences and the
evidence and presses Merge.

Merges into `develop` use plain git merges. A pull request there would be
a gate the human approves every time, and an always-approved gate filters
nothing.

## 4. CI budget

Hosted CI (GitHub Actions) runs in exactly three cases:

1. A pull request into a protected branch.
2. The monthly gardening run.
3. A manual trigger.

Never on every push. The inner loop runs the same checks locally, which
costs nothing.

The checks on a pull request include the validator, so a C6 freshness
failure ([SPEC-1](spec.md) section 3) fails the gate into `master`. The
remedy is a real re-check of each stale record under SPEC-1 section
8.6, never a date edit.

## 5. The human gate

There is one human gate: the pull request into `master`. An agent never
merges it. The human approves the request from the summary sentences and
the evidence attached to it. CI's checks and a fresh-context reader of
the diff carry what the human does not read
(D-35 section 6). The gate stays
meaningful while those two run and can say no. A human who presses Merge
over a failed check or without the reader's report has rebuilt an
always-approved gate with more steps; the fix at that point is to shrink
the diffs, not to add another gate.

## 6. Governed records

Governed records keep their stricter rule, unchanged by this spec. An
authoring verb stops at the working tree; a person approves the ruling
in one sentence, an agent reads the diff, and the commit follows
([D-18](decisions/D-18-authoring-write-path.md)) — on whatever branch
the work runs. The committed record then travels to `master` through
the same pull request as everything else.

## 7. The public repository

The public repository, `radialrig` under the owner's GitHub account,
holds the releases and the public base
([D-39](decisions/D-39-release-channel.md) rule 1). No work is done
there, so its environment is smaller.

- **One branch, `main`.** It holds the base as exported from a `master`
  commit of the private repository. It has no `develop` and no
  `staging`: the work, its integration and the approval of its records
  happen in the private repository. The host protects `main`: a pull
  request is required, and force-pushes and deletion are blocked.
  No export branch is pushed there before that protection is in
  place.
- **Pull requests come from export branches.** The export writes the
  public tree of one `master` commit onto a branch named
  `export/<short commit>` in a fresh clone, commits it once, and checks
  it there. The branch is pushed and its pull request opened only after
  the owner's yes in chat for that push, given to a request that names
  the commit and the branch, because a pushed branch is public the
  moment it lands. An agent never merges the pull request; the owner
  does.
- **The same checks run on them.** The workflows run on a pull request
  into `main` as they do on one into `master`: the validator and the
  suite on Windows, Ubuntu and Debian, and the app's tests with a
  headless first run on Debian, all on the exported tree. The Linux
  release build runs too when the pull request changes its workflow. A
  run in the public repository saves no cache. Section 4 holds there
  whole: the same budget, and a C6 freshness failure fails the gate
  into `main` as it fails the gate into `master`.
- **Forks, not outside pull requests.** The repository is fork-only: a
  pull request from anyone else is closed with a pointer to forking.

The pull request into the public `main` adds no second gate on the
work, which passed the gate of section 5 when it reached `master`. It
is where the exported tree is checked as the public will see it, and
where the owner approves what is published.
