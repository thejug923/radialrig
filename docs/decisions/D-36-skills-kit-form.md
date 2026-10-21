---
id: D-36
title: The skills kit and its install form
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-10-02
last-verified: 2026-10-02
supersedes: none
replaced-by: none
depends-on: D-34, D-35
---

**Status: in-force.**

## Question

RadialRig ships a docs kit through two install surfaces — the CLI and
the guided local page ([D-34](D-34-harness-agnostic-package.md)). The
owner has ordered a second kit: agent skills, installed and removed on
the user's request at project scope. In what form does the skills kit
ship, what CLI surface does it get, and where does an installed skill
land?

## Ruling

Ruled by the owner on 2026-08-18; rule 3 amended on the owner's
direction on 2026-09-08; rule 1 amended on 2026-09-22 to follow D-34
rule 3 as the owner clarified it; rule 2 amended on the owner's ruling
of 2026-10-02:

1. **The skills kit ships in the same package and through the same
   install surfaces** as the docs kit. The CLI and the guided page
   drive the same engine functions, and where both offer a capability
   they behave alike (D-34 rule 3).
2. **The CLI surface is a new subcommand family:**
   `radialrig skill add <name>`, `radialrig skill remove <name>`,
   `radialrig skill list`. The docs kit's verbs and the skills kit's
   verbs stay independent, with one crossing the owner ruled on
   2026-10-02: `radialrig upgrade` on a mounted project also brings
   up to the packaged version every skill that, for each agent, the
   project already has with all its files in place and none edited.
   It never adds a skill, never puts back one whose files were
   removed, and never writes a file the person edited.
3. **An installed skill lands where its agent reads it, and each agent
   is its own path.** The target is data behind a harness-to-path map
   with one row per agent — Claude Code at `.claude/skills/<name>/`,
   Codex at `.agents/skills/<name>/` — and each row carries the plain
   label the surfaces show; the engine itself stays harness-agnostic
   (D-34 rule 2 applies unchanged). The guided page and the app offer
   the skills as one list per agent, and a press there acts on the one
   agent its list names, for one skill or for every skill; the command
   line acts on every agent unless `--harness` names one. A skill
   installed for one agent and not another is a state a person chose,
   not a fault, and no surface reports it as one.
4. **Installed skills are tracked with content hashes.** A skill the
   user edited is never silently overwritten or removed — the docs
   kit's edit-detection ethic applies to the skills kit unchanged.
5. **The first skill is session-handoff,** rewritten from its external
   source to conform to the research corpus before it ships.

## Why

The owner ruled the kit into the established install surface rather
than a separate distribution: one package, one mount ethic, one
review surface. The subcommand family was chosen over flags on
`mount`/`unmount` so the two kits' lifecycles stay independent, and
over a general `kit` verb so no migration of the docs kit is implied.
`.claude/skills/` was chosen over a neutral directory because a skill
that no harness reads serves nobody; the harness-to-path map keeps the
neutrality where it belongs, in the engine.

The 2026-09-08 amendment followed
PLAN-0013, which added Codex as
a second row of the map on 2026-09-06 and had every press install for
every agent so the pages needed no change. Shown the issue #41 fix,
which named a skill installed for one agent only in a long sentence
and a command-line block, the owner ruled the opposite: two visible
paths, either or both, with as little reading as possible. One list
per agent puts that choice in the page's structure instead of in a
sentence.

The 2026-09-22 amendment followed the owner's clarification of D-34
rule 3: the two surfaces owe each other parity where a capability
exists on both, and the command line may offer options the page
cannot.

The 2026-10-02 amendment followed BUG-0020. The owner, who upgrades
every project after every release, expected the upgrade to carry the
skills, and it did not. A skill the project already has is a choice
the person made, so bringing it to the packaged version keeps that
choice; adding one, or putting back one they removed, would make a
choice for them.

## Consequences

- The strategy record (D-35) names the
  skills kit in its section 3, amended on the owner's direction the
  day this record landed.
- The execution plan for the build,
  PLAN-0006, landed in
  `docs/plans/` before the implementation merged and met every done-condition
  on 2026-08-18.
- PLAN-0015 builds the
  one-list-per-agent form of rule 3.
- PLAN-0021 phase 1
  builds the 2026-10-02 form of rule 2.
