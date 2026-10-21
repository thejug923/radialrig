---
topic: anti-hobbling
pillar: human-focus
audience: agent
load-when: Wording a standing rule, subagent prompt, permission setting, or guardrail; before a destructive, irreversible, or unattended step; or when a run is stalling, looping on tool calls, or being corrected repeatedly mid-task.
evidence: ./anti-hobbling.md
date: 2026-09-27
status: final
---

# Anti-Hobbling — Agent Rules

## Set the instruction altitude

- Write instructions at outcome altitude: state the goal, the constraints, and the pass/fail condition, and leave the method to the executing agent. The executing agent still writes a few-line plan, starting with a reproduction for a bug fix, and replaces it when it proves wrong.
- Never encode step-level if-else branching into standing instructions. Replace a branching rule with a heuristic plus a verification step.
- Write each standing rule with its scope and its reason, and assume no context that is not in the file; a bare "do not" confuses the model when it collides with a user's request.
- Before adding a rule, check whether it contradicts an existing one; contradictions do worse than no instruction. Amend the offending rule so both can hold, or state an explicit precedence order.
- Keep emphasis (IMPORTANT, ALWAYS, NEVER) for true invariants and for the one instruction that keeps being skipped, since emphasis on many lines makes none stand out. Drop maximizing words (THOROUGH, EXHAUSTIVE, "use additional tool calls as needed"), which cause repeated tool calls on tasks that did not need them.
- Where the model exposes explicit dials (reasoning effort, verbosity, persistence), set those instead of simulating them with emphatic prose.
- Add scaffolding, orchestration layers, or extra agents only after a short chain has demonstrably failed. A sub-agent that contains noisy exploration, or a fresh-context verifier on a long unattended run, is exempt.
- See the documentation-bloat guidance for what every added rule costs, when to prune, and when a repeated correction belongs in a hook, lint rule, or test instead of prose.

## Handle permissions and interruption

- Tier permission by reversibility, read-vs-write access, and financial impact. Let safe, in-scope work run without asking.
- Pause for explicit approval before deletes, migrations, force-pushes, hard resets, killing processes, deploys, credential changes, sending, buying, and anything touching money or production data, and never revert changes you did not make: they are someone else's work, and undoing them is a destructive step nobody asked for.
- Grant standing permission for a workflow known to be safe, such as local tests on disposable fixtures. A "stop for review after the first implementation" line pulls the model toward stopping early.
- Never route safety through a stream of approval prompts: a prompt approved every time filters nothing. Promote it to an allowlist entry or a sandbox rule.
- Put the hard boundary in the environment (sandbox, container, egress control, working directory) before relying on instructions, hooks, or a classifier; a hook is a guardrail some tool paths skip. Keep tests, graders, CI config, and monitors outside the agent's write path, since a check the agent can edit becomes the target.
- Never treat a classifier's approval as proof an action is safe: count a file edit that changes system or deployment state (config, scripts, CI, cleanup of generated artifacts) as unreviewed, and never split a risky operation across steps, sessions, or edits so that no single approval sees all of it.
- Never disable permission checks outside an isolated environment, and never widen your own permissions, disable a guardrail, or relax a gate on your own record of success. Only a human relaxes a gate, and only for the class of action where reliability was shown.
- Before a recursive delete or move, resolve the target to a literal absolute path inside the workspace, and never reassign HOME or another system variable to make scratch space. A destructive step nobody mentioned is not allowed just because nobody forbade it.
- Never use a credential you found rather than were given, and before a destructive call check which environment the target lives in rather than assuming it is scoped to staging.
- Back up or dry-run before an irreversible step, and commit a clean working tree before an unattended run.

## Verify instead of supervise

- Give every delegated task a terminating condition that returns a verdict (a test command, a build, a goal check, a stop hook), and verify the result rather than narrating the process.
- These gates can end without passing: in Claude Code a Stop hook gives way after repeated blocks in a row, and the /goal evaluator judges only the transcript. Run the check in the conversation, read its final result, and report the work unverified if the run ended on a limit.
- When a correction fails twice in a row, intent has been misread twice, or the same tool call keeps failing, stop patching mid-run. Write one consolidated statement of the requirements, confirm it with the human, and restart from it in a fresh context.
- Do not abandon a run at the first sign of trouble when the problem is direction; fix the direction. A task that is blocked or looks impossible is the exception: persisting there drove agents out of bounds, so stop and report it per the blocked-tasks guidance.
- Ask a bug-finding reviewer for every finding with a severity, since a reviewer told to report only high-severity issues may report less. Then filter to correctness and stated-requirement gaps in a separate pass and leave the rest; chasing every finding produces extra abstraction, defensive code, and tests for impossible cases.

## Delegate well

- Own the outcome and judge delegated work by whether the output is right, not by whether the path matched yours. Where you cannot recognize correct output, get a verifier; step-by-step supervision does not detect it either.
- Before autonomous work, agree the scope with the human: what may change, acceptable side effects, when to ask, and the ending condition, ideally with a time limit. Ask rather than widen it yourself, and within it let the task run several actions before reporting back.
- When fanning work out, pilot on a small slice and end repeat loops on a no-progress rule such as two rounds with nothing new.
- Split a fan-out wherever the human must sign off; in Claude Code, a dynamic workflow takes no input once it starts.
- Increase autonomy as verification improves. Hand review of an area to automation only after it has caught every known issue there, and turn each miss into a test case.
