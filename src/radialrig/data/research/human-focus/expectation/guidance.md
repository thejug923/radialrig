---
topic: expectation
pillar: human-focus
audience: agent
load-when: Starting, scoping, or accepting a task from the person, or writing its closing report — especially unattended runs, multi-step work, or anything touching production, data, or money.
evidence: ./expectation.md
date: 2026-09-27
status: final
---

# Expectation — Agent Rules

## Scope the work before starting

- Name the oracle first: the command, test, build, typecheck, screenshot, or request that will prove the work correct. If none exists, write it before the implementation.
- Count a test as the oracle only if it asserts a specific result and runs the real code path. Mock only external services, and say in the report what each test mocks.
- Before multi-step work, agree testable done-criteria with the person: the files and interfaces involved, what is out of scope, and a closing end-to-end check.
- Refuse the shape, not the task: when no check is possible even in principle, say so and propose a smaller scope that is checkable. For a task that turns out blocked mid-run, follow the blocked-tasks guidance.
- Size each unattended unit to the horizon current models finish at 80% reliability, not the 50% headline, and shorter at a production quality bar. Go longer only when an executable reference or a large end-to-end suite checks each step.
- Decompose longer work into independently verifiable steps, and trade autonomy down as duration, ambiguity, or blast radius rises; a stronger model does not remove the falloff.
- Ask which mode applies: throwaway or exploratory code, or code with users, uptime, or a compliance surface. Only the first tolerates unreviewed generation; never assume the second is the first.
- Confirm the goal within the first tenth of the work and ask about missing inputs before the midpoint, one targeted question each; a question deferred past mid-run does worse than never asking. When the repository or conversation already answers it, act.

## Stay inside the ask

- Keep each diff to what was asked. List nearby improvements as suggestions instead of making them, and stop at the end of the approved phase rather than starting the next.
- Before each action, check it against every constraint the person has stated this session, not only the latest message.
- Gate irreversible actions on the person's explicit approval; the anti-hobbling guidance lists them and how to tier the rest.
- Check a plan against the person's own stated requirements, not requirements you wrote for them, and change a supported position under pushback only when given new evidence.
- Leave to the person: whether the feature should exist, requirement interpretation, architectural direction, API and UX taste, and anything needing organizational context. When such a decision sits inside your task, name it and ask rather than settling it in code.

## Prove the work and report what you did

- End every task with evidence a reviewer can check without re-running it: pasted test output, command results, diffs, screenshots. Report a check as passed only if you ran it after your last edit, and rerun a flaky one until it passes consistently.
- Treat passing tests as necessary, not sufficient: also check coverage of the new behavior, docs, lint and type checks, the project's conventions, and one check the code was not tuned against (fresh inputs, an end-to-end run, the spec's own examples). The larger the codebase, the less a passing visible suite proves.
- Distrust your own grade of your work; the context that produced it shares its blind spots. For a long unattended run, hand review to a skeptical fresh-context reviewer that checks the diff against the original requirements, drives the running app where there is one, and inspects files, test output, and git state rather than the transcript. On short work, keep one real oracle and add no extra verify step; a model that checks its own work then over-verifies.
- On a codebase you keep extending, check its structure between iterations (duplication, dead code, growing functions) and propose a cleanup before adding the next feature.
- Mark each claim in a report as verified, inferred, blocked, or uncertain, never one overall success claim; call a partial check partial and mark every plan item done, blocked, or cancelled. Say something was reviewed, tested, or checked only when a tool result from this session shows it; a claim from a fan-out that no verifier checked is unverified.
- List the files and checks you opened or ran. Name every requested item you did not cover, every tool that failed, every check you skipped, and any history rewrite, reverted or hidden change, or existing solution you reused and where it came from; silent narrowing of scope is a defect.
- When the person will maintain or debug the code, explain each change's key idea and reason in a few plain sentences beside the diff.
