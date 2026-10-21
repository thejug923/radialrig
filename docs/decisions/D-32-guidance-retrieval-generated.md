---
id: D-32
title: Guidance retrieval is generated from the guidance files
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-09-27
last-verified: 2026-09-27
supersedes: none
replaced-by: none
depends-on: D-31, D-13
---

**Status: in-force.**

## Question

Before this ruling no tooling touched `research/`. An agent found
guidance through a table in `research/INDEX.md` that was maintained by
editing it — a second copy of what the agent-written guidance files
already state, in a file no check covers. That is the defect class
[D-20](D-20-route-map-no-enumeration.md) removed from the route map — a
renamed guidance file, or a topic added without a row, drifts silently.
The size bound [D-31](D-31-human-agent-doc-split.md) places on guidance
files was likewise unenforced. When tooling covers guidance, what shape
does it take?

## Ruling

**The guidance files are the only source; every catalog of them is
generated.**

1. Each `guidance.md` carries its own load-when trigger in its
   frontmatter. The file describes itself; there is no second place to
   update.
2. The read server gains a guidance verb: called with a topic it
   returns that guidance text; called without one it returns the topic
   list with triggers, built live from the files. The listing cannot
   drift, because the files are its only source, and it is never
   authoritative ([D-07](D-07-generated-never-authoritative.md)).
3. The size bound is checked where the catalog is built, so an
   unbounded guidance file is refused, not discovered.
4. `research/INDEX.md` is not the mechanism. It is generated output
   under [D-07](D-07-generated-never-authoritative.md), rendered by
   `radialrig-index` from the same loader the verb uses, kept as the
   agents' file fallback when the retrieval tools are absent
   ([D-31](D-31-human-agent-doc-split.md) rule 4), and never
   edited directly.

The rejected alternative: a new mechanical check that validates the
edited table's rows. That makes an ungoverned file check-bearing and
pays for the second copy's upkeep forever — the same trade
[D-20](D-20-route-map-no-enumeration.md) already refused.

## Why

The reading rule in D-31 is only as reliable as the table it routes
through, and a separately edited enumeration in an unchecked file is
the one drift mode this method has already observed twice. Generating
the catalog removes the maintenance obligation instead of policing it.

The purpose of the tooling is what it hands an agent: a live catalog
of the research-grounded methods this repository carries — research
agents produced — so a session decides with the research instead of
from memory. Ruled by the owner 2026-08-17; the build was deferred so
it landed as its own reviewed diff.

## Consequences

- The build landed on 2026-08-17 (commit 4f8bbf7): the `get_guidance`
  verb on the read server, specified in SPEC-2 section 5; the
  `radialrig-index` command that renders `research/INDEX.md`; and the
  refusal of over-long or untagged guidance files where the catalog is
  built. The index has been generated output since that commit, and
  its header says so.
- The route map routes to `get_guidance` first and to the generated
  index only as the fallback without the tools
  ([D-31](D-31-human-agent-doc-split.md) rule 4).
