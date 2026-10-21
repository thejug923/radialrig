---
name: promotion-gate
description: Use when setting up a git repo, choosing a branching strategy, or about to push to master or main. A trunk push is a promotion. It ships via a pull request the user merges, or a marked push where no host exists - verified and approved each time.
radialrig:
  id: promotion-gate
  version: 3.1.0
---

## First, the rules that bind

- A push to the trunk is a promotion: the care of a release, not of a commit.
- Verified and approved are two conditions, not one. Both, every time, in that order.
- A refused push is the system working. Diagnose which layer refused and why; the
  fix is upward toward the approval, never sideways into `--no-verify`, a fresh
  clone, or a tool that does not see the gate.
- Say what you are promoting and why before you ask for approval. "Ready to push to
  master?" is not a request the user can evaluate.

## What this does

Master is outward-facing. Once a change lands there it has shipped, and unshipping it
is a different and worse job than not shipping it. This skill holds the trunk open
only for verified and approved work: a ladder that gives a change somewhere to prove
itself before it is public, and a gate that does not open on an agent's say-so.

The gate belongs to the user. Verification says the change works; approval says the
user wants it out. Different questions, and an agent may answer only the first.

## When it fires

- Setting up a git repository, or deciding where work lands and how it reaches master.
- About to run any `git push` that targets `master` or `main`.
- The user says a change is finished, ready, or approved - check what they approved.
- A push was refused and you are deciding what to do about it.

## The principle

A boundary earns its place only if crossing it runs a check that can say no. A gate
that never fails filters nothing, and it is removed. Every branch and every layer
below exists on that test.

## The ladder

Two long-lived branches by default, named in the direction code flows:
`develop` -> `master`.

- Day-to-day work commits to `develop` and stays there until it is ready to prove
  itself.
- `staging` is conditional, not standard. Cut it only when a check exists that
  `develop` cannot run - tests against a deployed environment, for example. A
  staging branch with no check of its own is a gate that never fails; do not cut it
  in advance of the check that would justify it.
- `master` moves only with verification and approval together: the change exercised
  as it will actually run, the outcome observed, not inferred from a clean diff or a
  passing build, and the user approving this promotion.

A soft rule with the usual precedence: it fills the vacuum in a greenfield
repository; an established repository's branching model wins.

## Approval is per-promotion, never standing

The rule that does the work; everything else here is machinery serving it.
One approval covers one promotion. Yesterday's approval does not cover today's.
Approval of the change is not approval of the push: "looks good" on a diff, a green
CI run, or a pull request merged somewhere other than the trunk is none of it. If
you cannot point at the user approving this specific push, you do not have
approval - ask.

A trunk push always sits in the ask-first tier: visible off this machine and
irreversible in practice. An agent never grants itself the gate, and never arranges
for it to stay open.

## The primary gate - a pull request the user merges

Where the repository's host offers pull requests, promotion is a pull request into
the trunk, and that is the whole gate:

- You open the request and attach the evidence: test output, validator output, what
  was exercised and what was observed.
- The host runs its automatic checks on the request itself.
- A fresh-context reader reads the diff and reports. The user reads the summary and
  the evidence and presses Merge. You never merge it, and never approve it by proxy.
- Host branch protection makes the request the only door - see below.

The merge is the approval, per-promotion by construction: nothing persists that
could wave the next promotion through. Where this route exists, do not also install
the fallback below - a second gate on a boundary the first already checks runs no
check of its own.

## The fallback - the marked push

Where no host offers pull requests - a bare remote, a plain mirror, an offline
repository - the gate is a marked push guarded by a pre-push hook. With approval in
hand:

```
git -c radialrig.promote=1 push origin master
```

Use your trunk's actual name. The `-c` flag is parsed by git itself, so the command
is identical in bash and PowerShell, and sets the value the hook reads. The marker
is per-invocation by construction and cannot linger as a standing approval. Protect
that property: run the push on its own, never chained behind `&&` or `;`; never set
the marker persistently (`git config radialrig.promote 1`), which converts a
per-promotion approval into a standing one; and work inside the repository rather
than promoting another one through `git -C`.

### The pre-push hook

Installing it writes a file into the user's project and changes that repository's
config - the ask-first tier above, so say what you would install and get the
go-ahead first.

Read before you write - `git config --get core.hooksPath`, `git config --get
radialrig.promote`, and the contents of the hook directory in use, which is the path
the first printed or `.git/hooks` when it printed nothing. That read is also how you
check this layer: without it you cannot report the layer present.

- If that directory already holds hooks, do not repoint. Setting `core.hooksPath`
  silently stops every hook the project had, with no git output saying so; add the
  pre-push body to the directory in use instead, and name it when you report.
- Only where no hook directory is in use, write `.githooks/pre-push` and run
  `git config core.hooksPath .githooks`.
- A value printed for `radialrig.promote` means the gate stands open and every push
  passes. Say so, and `git config --unset radialrig.promote` (add `--global` if it
  sits there) before treating this layer as present.
- The file must be executable wherever the host's shell enforces an execute bit.

```
#!/bin/sh
# Replace the two refs below with this repository's own trunk refs before saving.
[ "$(git config --get radialrig.promote)" = "1" ] && exit 0
while read local_ref local_sha remote_ref remote_sha; do
  case "$remote_ref" in
    refs/heads/master|refs/heads/main)
      echo "pre-push: BLOCKED promotion to $remote_ref; marker absent" >&2
      exit 1 ;;
  esac
done
exit 0
```

Its blind spots: `--no-verify` skips it, a clone that never set `core.hooksPath`
never runs it, and a copy still carrying the refs above is inert where the trunk is
named otherwise. A local hook is advisory against a determined caller - which is why
the primary gate lives on the host wherever a host exists.

## Host branch protection

The host's rules run on hardware the agent does not control, so they are the one
layer a client cannot skip. Where the repository has a host, propose protecting the
trunk: require a pull request before merging, block force-pushes and branch
deletion, and keep the bypass list empty - agents push with the user's credentials,
so an admin bypass exempts agent pushes too. Changing host protection is an
outward-facing write to a shared resource: ask-first, like the promotion itself.

Some harnesses can also deny a shell command by its text, refusing a trunk-targeting
push outright. Use that where the harness offers it; it is a capability of the
harness, not something this skill installs.

This skill documents the enabling direction only. If protection is disabled and the
user wants it restored, turn enforcement back on through the host's own protection
settings; on a host the `gh` CLI serves,
`gh api repos/OWNER/REPO/rulesets/ID --method PUT -f enforcement=active` re-enables
an existing ruleset. No disabling command appears here: turning protection off
belongs to the repository's owner, and an agent reaching for it has misdiagnosed the
problem - a refused push is the system working.

## When the machinery is absent

With no pull request route, no hook, and no host protection, nothing mechanical
stands between you and the trunk: the skill degrades to its judgment layer. Every
rule still binds - verification plus per-push approval, the marker command, the
marker never persistent - but only you enforce them. Say so plainly, offer to
install the hook, and where a host exists, offer to propose protection. Never report
a layer as present that you did not verify; for the hook, the read above is that
verification.
