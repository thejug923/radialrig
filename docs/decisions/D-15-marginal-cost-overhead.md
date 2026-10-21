---
id: D-15
title: Marginal context cost measured as tool-path overhead
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-08-16
last-verified: 2026-09-27
supersedes: none
replaced-by: none
depends-on: none
---

**Status: in-force.**

## Ruling

The marginal context cost measure counts, per question, all retrieval
tool request and result bytes **except the `body` field of the
controlling record's ruling result**. Catalog calls (`list_rulings`)
count in full — they are genuine overhead — and the route map directs
them to catalog tasks, not to surveys before a lookup.

**This record defines the count. It sets no threshold.** The testbed
measured against a bound of 847 B, derived from its own corpus and its
own questions. That number is a property of that corpus and does not
travel. An adopter who wants a bound measures a baseline first.

## Why

The original baseline measured file content read **before** the
controlling record was first opened: overhead on the way to the answer,
never the answer itself. A later re-measure counted the answer body
inside the tool result, so a tool path that returned the answer in one
call scored worse than a file path that read three files first — the
measure compared incommensurable quantities and punished the better
behavior. Overhead-versus-overhead restores the comparison the measure
was built to make.

The count and the threshold are different objects. Conflating them is
what broke the measure the first time.
