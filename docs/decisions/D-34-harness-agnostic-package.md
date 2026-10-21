---
id: D-34
title: RadialRig ships as a harness-agnostic mountable package
type: decision
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-09-22
last-verified: 2026-09-22
supersedes: none
replaced-by: none
depends-on: none
---

**Status: in-force.**

## Question

This repository proves the method on itself: the governed corpus, the
retrieval and authoring servers, the validator, and the generated
catalogs all run here, for this project. The product intent is wider —
a user mounts RadialRig onto their own project so that agent-driven
work avoids the pitfalls that regularly cost such projects accuracy
and capability. In what form does the method and its tooling ship?

## Ruling

**The shipping unit is a package a user installs into an existing
project folder — the mount — and it depends on no single agent
harness.**

1. The mount targets a project the user already has. RadialRig is not
   a template a project starts from; it is added to a project, and it
   must leave the project's own layout alone.
2. No harness dependency. The tooling speaks MCP, and any client that
   speaks it is a peer. Claude Code is one such client, never a
   requirement.
3. Two install surfaces, one engine. A command-line interface serves
   advanced users. A guided, UI-led installer serves everyone else.
   Both drive the same mount step. Where both surfaces offer a
   capability, they behave alike and report alike. The command line
   may also offer options the UI-led surface cannot, and that
   difference is not a defect.
4. The concrete form of the UI-led installer — wizard, local web page,
   or desktop application — is deliberately deferred to the packaging
   plan. This record fixes the direction, not the implementation.

## Why

Ruled by the owner 2026-08-17. The product's usefulness is not limited
to one harness, so the shipping form must not embed one. A
command-line-only install serves the advanced minority and excludes
the users the product most aims to help; the two-surface rule keeps
the simple path first-class without forking the engine.

Rule 3 was clarified by the owner on 2026-09-22. The rule asks for
parity where a capability exists on both surfaces, not for two
identical surfaces: the command line can give options a page cannot,
and holding it back to what a page can offer would cost the advanced
users it serves.

## Consequences

- This record called for a packaging plan, and
  PLAN-0005 was that plan. It
  moved the engine from `tools/` to `src/radialrig/`, defined the seed
  a mount lays down and the mount step, and chose a local page served
  by `radialrig setup` as the first UI-led surface; it completed on
  2026-08-18. PLAN-0008 later
  added the desktop app as the second.
- Until PLAN-0005 landed, this repository was the only mount and the
  engine stayed under `tools/`.
