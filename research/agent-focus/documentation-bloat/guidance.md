---
topic: documentation-bloat
pillar: agent-focus
audience: agent
load-when: Choosing what an always-loaded instruction file (CLAUDE.md, AGENTS.md, MEMORY.md, rules, skill bodies) holds, reviewing or pruning one, or deciding where a new standing instruction belongs.
evidence: ./documentation-bloat.md
date: 2026-09-27
status: final
---

# Documentation Bloat — Agent Rules

## Decide what earns a place in an always-loaded file

- Include only what the agent would otherwise get wrong: commands it cannot guess, non-default style, testing steps, repo etiquette, project-specific decisions, environment quirks, and gotchas the code does not show.
- Exclude what the code shows: repository overviews, standard conventions, file-by-file descriptions, API docs (link instead), fast-changing facts, and tutorials.
- Do not generate or bulk-expand an instruction file; generated context files add cost and steps without raising task success.
- Apply the per-line test: if removing a line would not cause a mistake, delete it.
- Where agents write most of the code and no rules file is committed, propose a short one and keep it current.
- Flag a standards file that contradicts current code or dependencies as stale rather than following it.

## Budget the always-loaded total

- Budget all always-loaded files together, imports included, against a per-file target of about 200 lines. In Claude Code, @imports load at launch and only path-scoped rules and skills defer; in Codex, AGENTS.md files past a combined size cap (32 KiB by default) are not loaded.
- In Claude Code, only the first 200 lines or 25KB of MEMORY.md load; near the cap, compress to one line per entry, move detail to topic files, and drop stale entries.
- Measure skill budgets — about a hundred tokens per description, a few thousand per SKILL.md body — with the current model's tokenizer, since a new tokenizer can make more tokens from the same text.
- Keep references one level deep, and give a reference file over 100 lines a table of contents.

## Route each rule where it can work

- Route multi-step procedures and anything scoped to part of the codebase into a skill or path-scoped rule; a script costs only its output.
- In Claude Code, put a rule that must survive compaction in the root CLAUDE.md, an unscoped rule, or a SessionStart hook with the `compact` matcher, never in a path-scoped rule or a nested CLAUDE.md, and put a skill's key instructions at the top, since bodies come back truncated from the end; the context-rot guidance lists what compaction re-injects.
- Write documentation pointers as conditions ("read db.md for schema changes"), not as reads required before every edit.
- Keep skills compact and procedural, one to three per task; a skill you write for yourself mid-task scores below no skill at all.
- Load a third-party skill only if it matches the project's library versions.
- Write a skill description as its trigger condition, key words first, since harnesses truncate it, and put gotchas first in the body.
- Put a check that must run every time in a hook, and turn a correction needed three times into a hook, lint rule, or test. A hook that runs after an action cannot stop its side effect.
- Treat instruction files as advisory: a written rule against taking a shortcut barely reduced how often agents took it. Restate a hard limit where it applies.
- Share one file across toolchains through @import or native loading, not a prose "read AGENTS.md" line, and not a symlink where anyone works on Windows. In Claude Code, any project CLAUDE.md or CLAUDE.local.md stops AGENTS.md loading by default.

## Prune and maintain

- Prune by ablation: remove one group of instructions, examples, or tools at a time, re-run representative tasks, and keep each cut that holds. State each instruction once.
- Before adding an instruction, run the task without it to confirm the gap, then write the minimal instruction that passes several scenarios.
- At each model generation, re-run representative tasks with standing instructions, skills, and hooks removed; restore only what stops a repeated mistake.
- Check what loaded and what goes unused: in Claude Code, `/memory`, `/context`, and `/skill-doctor`; in Codex, ask the agent to restate its instructions.
- Edit instruction files by adding, changing, or deleting single lines, shown to the human as a diff; add no directive the user did not ask for.

## Write each rule so it can be checked

- Make every rule concrete and checkable ("Use 2-space indentation," not "format code nicely"); cut redundancy, not the concrete detail the agent needs.
- Write a review rule as an invariant plus the safe alternative, scoped to its directory, and test it on a violating, a safe, and an unrelated change.
- Add no blanket word or length caps to standing instructions; a measured cap cost quality on the vendor's own evals.
- When a skill or rule makes you pause, refuse, or change course, name the file and quote the line.
- See the anti-hobbling guidance for altitude, each rule's scope and reason, emphasis, rule conflicts, and where hard limits belong.
