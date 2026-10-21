---
id: D-04
title: The archive lives under one excludable glob
type: decision
status: in-force
grade: verified
owner: thejug923
scope: RadialRig
effective: 2026-09-03
last-verified: 2026-09-03
supersedes: none
replaced-by: none
depends-on: none
---

**Status: in-force.**

## Ruling

All archived material lives under one path, `docs/archive/`, and default
retrieval excludes it. The route map names the exclusion so any agent or
subagent can apply it.

**The exclusion form belongs to the harness, not to this rule.** Each
copy of the form has one home. In a seeded project, the fence section of
the route map carries the form agents apply; that copy is the surviving,
RadialRig-managed one, it upgrades with the section, and the consumer
cannot amend it. That is deliberate. The form a project tests in its own
harness lives as one line in that project's SPEC-1 copy, section 2,
amendable under the project's own section 8.1.

A glob that fails to exclude fails **silently**: nothing reports it, and
the archive simply reappears in results. Test the exclusion on content
that is really in the archive. A mount seeds one archived record under
`docs/archive/` for that, carrying the marker phrase
`radialrig-archive-exclusion-marker`. With the exclusion applied, a
search for the marker returns no hit under `docs/archive/`; a hit there
means the recorded form failed. A hit outside the archive is the prose
that names the marker, and it is expected.

**What the check measures.** The test runs ripgrep with the recorded
glob. Ripgrep is the engine under Claude Code's search, so the run is a
close proxy for that harness rather than the harness itself. A
measurement in the harness still dates the form.

A measured form is a dated harness fact, not a stable property. In the
testbed (2026-08-14), `!**/docs/archive/**` excluded and the rooted
`!docs/archive/**` did not. Re-measured 2026-08-25 in Claude Code on
Windows, in this repository: both forms exclude. Governed text states a
form only with its date and harness; the negative half of the testbed
measurement no longer held eleven days later.

## Why

Prose asking an agent to ignore a search hit is weak. A path filter is not.
Verified in the testbed (2026-08-14): mechanically, only the `!**/...` glob
form excluded archive hits; behaviorally, fresh agents following the route
map read the archive in zero of five layered runs. Limits: one model, one
run per case. See the testbed research corpus.

The split between the rule and the form is the portable part. The rule —
history sits under one excludable path, named in the route map — holds
anywhere. The string that implements it is a property of one search tool.
