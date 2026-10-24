---
id: D-29
title: A record that relies on another declares it; undeclared references are fenced as unreachable
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-09-04
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: D-28
---

**Status: in-force.**

## Question

G12, recorded in PLAN-0003 Phase 1 and confirmed by its null arm:
when a record is retired, nothing verifies that documents which
referenced it were repaired. C4 catches a dangling link; it cannot
catch prose that still asserts the retired ruling as current. The
retirement verbs walk `depends-on` declarations — so the uncovered
class is exactly the references a record never declared. How does G12
close?

## Ruling

**Declare or drift.**

1. **A governed record that restates, relies on, or names another
   record's ruling as current must declare that record in
   `depends-on`.** The obligation sits with the author at write time.
   [D-28](D-28-depends-on-required.md) makes the field universal, so
   "this record relies on nothing" is now always a declaration, never
   an omission.
2. A declared reliance is covered mechanically: the retirement and
   amendment walk identifies the dependent and refuses a change that
   leaves it untouched, and 8.5 propagation reads it.
3. **An undeclared reference is accepted as unreachable for mechanical
   checking**, in the same class as G10 (past-tense voice) and G17
   (retirement invisible to C5's path comparison): a judgment about
   text that no cheap check can make without flagging every legitimate
   History mention. The residue is fenced, not denied: a record that
   restates a ruling without declaring it will drift silently when the
   ruling changes. The name of this decision is the warning.

G12 is closed by this ruling: covered where declared, fenced where not.

## Why

- The walk architecture reads declarations by design (PLAN-0003's
  control 4 closure). Extending coverage therefore means extending
  declarations, not building a second detection path.
- The rejected alternative was a checker that flags a retired id
  appearing in any live body. Every `## History` section, every
  `supersedes` narrative, and every archive entry names retired
  records legitimately; the checker's noise would bury its signal.
- The instance this ruling was written against was the testbed's
  `docs/README.md`, an **ungoverned** second index that named
  PLAN-0002 as in force — outside this ruling's reach, and resolved
  there the way D-20's reasoning says, by deleting the enumeration.
  This repository carries no such file.

## Consequences

- Authors reviewing a record under 8.5 should add missing `depends-on`
  declarations as they find them; adding one is metadata maintenance
  with a real re-check behind it, not a ruling change.
- PLAN-0003's last open gap is routed, which unblocks its completion
  under SPEC-1 section 8.7.
