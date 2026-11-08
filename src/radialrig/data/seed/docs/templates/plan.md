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

## What this asks of the owner

Each decision or step only the owner can take, and where in the plan it
falls: an approval, a choice between options, a step on their own machine
or account. Write "Nothing" when the plan needs none.

## What this plan rests on

Each statement the plan depends on, marked by what backs it, in a line or
two. A statement with nothing behind it is an assumption and says so.

- **Decided:** who decided it and when, with the record key or the
  person's own words.
- **Verified:** what it was checked against and how: read in a file at a
  named commit, or run as a command or test, with its result; who
  checked it, and when.
- **Assumed:** why it is safe enough to build on, and the smallest check
  that would settle it.

## What must stay true

Every phase keeps these, and every revision of this plan re-checks all of
them, however narrow the change.

1. The rulings this plan may not contradict, each named by key.
2. Every phase ends with evidence in the execution log: command output,
   test counts, or a diff — never a bare claim of completion.
3. What a fix elsewhere in this plan could quietly break: a result a later
   phase relies on, or something the plan must leave alone.

## Phase 1 — NAME

**Prescriptions.**

1. What this phase builds. Prescriptions are binding text; the phase
   builds what the plan says.

**Done-condition.** The observable result that closes the phase, stated
so that a fresh reader can check it without asking the author, and what
running that check takes: where it runs, any wait, and any step only a
person can take.

## Execution log

Phases append their run records here as they close (SPEC-1 section 8).
