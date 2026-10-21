---
id: D-31
title: Human-facing and agent-facing documents stay separated
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-09-27
last-verified: 2026-09-27
supersedes: none
replaced-by: none
depends-on: D-10, D-20
---

**Status: in-force.**

## Question

The owner requires human-focused and agent-focused documents to stay
separated, with agents reading human-focused material only when the
task needs it. `research/human-focus/` and `research/agent-focus/`
already embody a split, but no governed record rules it: the layout
could be flattened, or a merged document written, and no rule would say
no. The route map does not mention `research/` at all, so an agent
finds the research corpus only by listing directories. What rule
governs the separation, how does an agent reach the right side of it,
and to which side does an agent write new material?

## Ruling

**A document addresses one audience.** Human-facing and agent-facing
text live in separate files; a document that must serve both audiences
is written as two files from the start.

Four parts:

1. **The separation.** Agent-facing documents are operative rule sets:
   technical, bounded in size, written to be loaded into context and
   obeyed. Human-facing documents are explanative: they capture the
   meaningful functional components and the major directional elements
   — what we are building, what we have done, where we are going.
   In `research/`, each topic carries an agent-facing `guidance.md`
   with the human-facing evidence documents beside it.
2. **Reading.** An agent loads an agent-facing document when its task
   matches the `load-when` trigger the guidance file carries in its
   own frontmatter, served through `get_guidance` (SPEC-2 section 5).
   It loads a human-facing document only when the task needs it — to
   verify or extend a rule the guidance file states, or because the
   task is about that document itself. Human-facing material is never
   background reading.
3. **Writing.** A session that produces durable material routes it by
   what it is:
   - A **decision** — losing it would regress ratified behavior
     ([D-10](D-10-governed-scope.md)) — becomes a governed record,
     through the write path.
   - Durable **how-to guidance for future agents** that is not a
     decision becomes an agent-facing document: one bounded
     `guidance.md` under its topic, carrying its own `load-when`
     trigger in its frontmatter. `research/INDEX.md` is regenerated
     from those files (`radialrig-index`) and never edited directly
     ([D-32](D-32-guidance-retrieval-generated.md)). Evidence, when
     there is any, sits beside the guidance file as separate files.
   - **Explanation or direction for the human** is not authored
     document by document. The human surface is generated from the
     governed corpus, so "what we built and where we are going" lives
     in records and derives from them. A directly authored human-facing
     document exists only on the owner's direction.
4. **The route.** The route map routes to `get_guidance` as the
   mechanism, with `research/INDEX.md` as the file fallback when the
   tools are absent. The guidance files, not the route map, carry the
   per-topic triggers. One call from the route map reaches the guidance
   text, which is what keeps the reference depth at one — the corpus's
   own documentation-bloat rule — instead of the chain route map, index,
   guidance file. This keeps the route map in the
   [D-20](D-20-route-map-no-enumeration.md) shape: directories, files
   and verbs, no enumeration.

Research files themselves remain ungoverned evidence under
[D-10](D-10-governed-scope.md) and SPEC-1 section 1. This record
governs the separation and the load rule, not their content.

## Why

The two audiences need different things from a document. An agent
needs operative, technical text in bounded size: it pays for every
loaded token all session, and the research corpus's documentation-bloat
finding is that always-loaded prose degrades agents through attention
dilution and instruction conflict. A human needs explanation and
direction: what the project is building, what it has done, where it is
going. A document bounded enough for the agent explains nothing; a
document explanative enough for the human is context an agent should
never pay for by default. One file cannot serve both readers.

Ruled by the owner as a standing requirement; recorded 2026-08-17. The
research corpus's structure is the working demonstration: each topic
pairs one guidance file with its evidence document, and one generated
index lists their triggers.

## Consequences

- The route map gains one Paths line routing to `get_guidance`, with
  `research/INDEX.md` as the fallback. Nothing else about `research/`
  enters the route map.
- The capture loop's "record written" step is this router. Because the
  human surface derives from the corpus, the corpus stays the one
  source of truth, and capture stays at zero ceremony for the human.
- A guidance file may cite its evidence documents; the split never
  requires duplicating evidence into the guidance file.
- The catalog build refuses a guidance file over the size bound or one
  whose frontmatter lacks `topic` or `load-when`
  ([D-32](D-32-guidance-retrieval-generated.md) point 3, SPEC-2
  section 5). The audience split itself has no mechanical check; it is
  enforced the way D-05's voice rule is — by being stated where every
  agent reads, and by review.
