---
id: D-13
title: Programmatic retrieval interface
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-09-04
last-verified: 2026-10-22
supersedes: none
replaced-by: none
depends-on: D-04, D-09, D-10, D-26
---

**Status: in-force.**

## Ruling

Governed truth gets a programmatic retrieval interface: a local MCP server,
read side first. The interface must:

1. Return only `in-force` records through its default retrieval tools.
   One exception: a query equal to a record's exact key also reaches a
   `completed` plan summary outside the archive
   ([D-26](D-26-completed-plan-form.md)). Every path that matches or
   selects stays in-force-only, so a closed plan never answers a
   free-text question.
2. Apply the authority order of [D-09](D-09-conflict-stop-rule.md) in code,
   and return a **typed conflict result** — never content — when two
   records tie.
3. Expose archived material only through an explicit history call.
4. Share one parsing and authority implementation with the validator. Two
   implementations of truth selection is a drift defect.
5. Stay read-only. Write transactions are a separate future decision.

## Why

The archive exclusion (D-04) and the conflict stop (D-09) are advisory on
the read path today: route-map prose an agent can ignore. A typed interface
moves both into the structural layer — the tool has no path that leaks the
archive and no way to return a winner on a tie. The interface is also
portable across agent harnesses, which route-map prose is not. Admission
under [D-10](D-10-governed-scope.md): losing this ruling would regress the
verified read-path behavior the PLAN-0001 pilot measured.
