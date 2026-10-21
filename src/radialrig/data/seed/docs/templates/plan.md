---
id: PLAN-0000
title: SHORT NAME OF THE PLAN
type: plan
status: draft
grade: decided
owner: {{OWNER}}
scope: {{SCOPE}}
effective: YYYY-MM-DD
last-verified: YYYY-MM-DD
supersedes: none
replaced-by: none
depends-on: none
---

> Skeleton for an execution plan. Copy it to `docs/plans/<id>-<slug>.md`,
> fill every field, and delete this note. Prescriptions and the execution
> log cost different things to change: SPEC-1 section 8.

**Status: draft.**

# PLAN-0000: SHORT NAME OF THE PLAN

## Objective

What this plan delivers, in the terms a reader who has not followed the
work would need.

## Constraints that bind every phase

1. The rulings this plan may not contradict, each named by key.
2. Every phase ends with evidence in the execution log: command output,
   test counts, or a diff — never a bare claim of completion.

## Phase 1 — NAME

**Prescriptions.**

1. What this phase builds. Prescriptions are binding text; the phase
   builds what the plan says.

**Done-condition.** The observable result that closes the phase, stated
so that a fresh reader can check it without asking the author.

## Execution log

Phases append their run records here as they close (SPEC-1 section 8).
