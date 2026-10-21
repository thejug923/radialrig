---
topic: context-rot
pillar: agent-focus
audience: agent
load-when: Work will run long or wide — many tool calls, large file or log reads, deep exploration, or an approaching compaction boundary.
evidence: ./context-rot.md
date: 2026-09-27
status: final
---

# Context Rot — Agent Rules

## Budget the context

- Treat the rated context window as a ceiling for what fits, not a budget for what works: accuracy and instruction-following erode progressively with length, with no safe threshold and no cliff to watch for. Plan real work in the tens of thousands of tokens.
- Curate the smallest set of high-signal tokens that gets the outcome; adding context is a cost, not a hedge.
- Judge progress by what is verified, not by how much has been read or how many steps have run.
- Retrieve just-in-time: carry identifiers, paths, and queries, fetch content at the moment it is used, and read the slice of a file that answers the question rather than the whole file.
- Have checks and tests print a few summary lines and write the detail to a log file you open only when a summary line points there.
- Keep the working tool set minimal and non-overlapping; overlapping tools degrade tool selection as context grows.
- Drop near-miss irrelevant material deliberately; distractors cost accuracy, and they compound.
- Delegate noisy exploration — wide searches, log dumps, bulk audits, dependency spelunking — to a sub-agent and take back a summary of one to two thousand tokens, keeping the raw output out of the main transcript. The memory-management guidance says what the delegation prompt must restate.
- When the harness compacts or saves state for you, do not narrow scope, stop early, or propose a new session because of a remaining-token figure; save progress to files and keep working until the task is complete or a real blocker appears.
- When a wrong fact or stale assumption enters context, correct it explicitly and say it is superseded; working around it silently lets it propagate through every later turn.

## Compact deliberately

- Compact at natural boundaries — a feature finished, a bug resolved, an exploration concluded — rather than waiting for the automatic trigger, which fires near the end of the window, long after quality has fallen.
- If quality degrades before the automatic trigger, lower the trigger (in Claude Code, `/autocompact`); do not raise a trigger to avoid compacting.
- Preserve across a compaction: architectural decisions, unresolved bugs, verified state, next steps, and implementation details still in play. Discard redundant tool output, superseded drafts, and resolved detours.
- Treat compaction as lossy deletion, not lossy compression: what survives is intact, what is dropped is gone. Where the harness keeps earlier messages and tool results searchable, retrieve the exact requirement, error text, or test output before acting on a note's paraphrase of it.
- Keep standing rules in files and re-read them verbatim after every compaction. A summary keeps a fraction of them, drops soft project- or user-specific policy first, and its paraphrase of a rule is not the rule.
- When the person states a standing constraint in chat, write it to the project's durable notes at once; a constraint the compactor has already dropped cannot be re-pinned from memory.
- In Claude Code, compaction re-injects the project-root CLAUDE.md, unscoped rules, auto memory, the plan, and invoked skill bodies, but not path-scoped rules, nested CLAUDE.md files, or context that hooks added earlier; re-read any of those the next step depends on. The documentation-bloat guidance says where a constraint that must survive belongs.
- After compaction, verify state against ground truth — files, tests, git status — before acting on what the summary claims.

## Detect degradation in yourself

- Before ending a turn, check the task checklist. If items remain and nothing blocks them, take the next step with a tool call instead of announcing it, and do not report the task complete while an item or a background job is still open.
- Search with a stop rule: one broad search, then more only while the core question is unanswered or a required fact is missing, and stop when searches stop yielding new evidence.
- Treat unexplained refusals, truncated output, or errors on mechanically simple work as length signals rather than difficulty signals; compact or restart rather than pushing harder.
- When a session turns forgetful or repetitive without a clear cause, check for a recent model, harness, or configuration change and restart before stacking corrections.
- Before acting on a long context, restate the exact facts you will rely on — file path, line, value — in a short note and work from that note; length hurts even when every fact is retrievable.
- When a long session gives inconsistent answers about earlier decisions, re-ground from durable files instead of trusting recall.

## Hand off clean

- Work one feature or task at a time and land it before starting the next.
- See the memory-management guidance for how to open and close a session, what the durable record must contain, and how to keep it trustworthy.
