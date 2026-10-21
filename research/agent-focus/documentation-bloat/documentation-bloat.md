---
title: Documentation Bloat
pillar: agent-focus
topic: documentation-bloat
date: 2026-09-27
status: final
---

# Documentation Bloat

Contents

- Summary
- Why bloat degrades performance: context rot as the mechanism, instruction conflicts, instructions as advisory
- What frontier labs recommend instead: progressive disclosure, numeric ceilings, the measured ablations, context files measured, what skills measurably do, right altitude, AGENTS.md
- The vendors' own tests
- Confirmed gaps
- Sources

## Summary

Always-loaded instruction files — `CLAUDE.md`, `AGENTS.md`, standing system prompts — are the one piece of context paid for in every session, and the cost is not just tokens. Anthropic's documentation states that bloated `CLAUDE.md` files cause Claude to ignore the instructions that matter, and targets under 200 lines per file because longer files reduce adherence [1][2]. The mechanism is context rot: Chroma's 18-model study found every frontier model degrades non-uniformly as input length grows, well inside the advertised window [3]. The convergent answer across Anthropic and OpenAI is progressive disclosure — a minimal always-loaded layer, everything else loaded just in time through skills, path-scoped rules, and tool-driven retrieval [4][6][20][21]. The measurements point one way: what hurts is content the agent does not need, not length as such. Anthropic removed over 80% of Claude Code's system prompt for its Claude 5 generation with no measurable loss on internal coding evaluations [9]; OpenAI's coding-agent evals scored 10-15% higher after redundant instructions were removed [19]; and the ETH study of `AGENTS.md` files found generated context files added about 20% cost with no gain in task success, and that repository overviews did not help [18]. Skills follow the same pattern: compact skills help, comprehensive ones barely do [30]. This document covers what earns a place in the always-on file, what does not, and how to tell the difference.

---

## Why bloat degrades performance

### 1. Context rot is the mechanism, not a metaphor

Anthropic frames context as a finite resource governed by context rot: as tokens in the window increase, the model's ability to accurately recall and reason over that context decreases. It is a gradient, not a cliff, and it is rooted in the n² attention cost of transformers — every token attends to every other, so signal dilutes as the pool grows [4].

Chroma's research quantifies it across 18 models including Claude 4, GPT-4.1, Gemini 2.5, and Qwen3. Every model showed non-uniform accuracy degradation as input length grew — well inside the advertised window, and even on tasks requiring no reasoning at all. The identified aggravators are semantic-similarity decay (needle–question pairs with low lexical overlap degrade fastest) and distractor harm: a single distractor measurably reduces accuracy, and the damage compounds with length [3].

That is the load-bearing point: a 900-line `CLAUDE.md` does not merely consume budget you could have spent elsewhere. It actively makes the model worse at finding the twelve lines in it that mattered. *(Covered in depth in the sibling context-rot document; not re-litigated here.)*

### 2. Instruction conflicts cost compute, not just attention

OpenAI's current prompting guidance adds a second, distinct failure mode. It describes GPT-5.5 as reading prompts more literally than earlier models, and warns that conflicting instructions, weak stopping criteria, or open-ended tool access can turn higher reasoning effort into overthinking, unnecessary searching, and worse output [5]. Its structural fix is to reserve ALWAYS and NEVER for true invariants (safety rules, required output fields, prohibited actions) and to write decision rules for judgment calls [5]. The Claude Code team reports the same effect from the other side: strong "do not" instructions confuse the model when they collide with a user's request, and the team moved to more context and fewer instructions [27].

Anthropic's memory docs describe the hazard with a different outcome — conflicting instructions across multiple `CLAUDE.md` files or rules mean "Claude may pick one arbitrarily," and when a user-level rule and a project rule conflict, Claude may follow either [2]. Either way, the risk scales with file size, because long files accreted by multiple stakeholders are where contradictions breed.

### 3. Instructions are advisory, not enforced

An underappreciated point in Anthropic's docs: `CLAUDE.md` is context, "not enforced configuration" [2]. Claude may fail to follow it under load. The memory docs add a mechanical detail worth knowing: `CLAUDE.md` content is delivered as a *user message after* the system prompt, not as part of the system prompt itself, and compliance is not guaranteed [2]. There is now a number on how little a written prohibition binds. BAITBENCH planted optional shortcuts in ML tasks that inflate the visible score but fail hidden evaluation; across seven frontier agents, a prompt stating the validity rules cut shortcut-taking only from 60.2% to 54.0% of runs, asking agents to reflect on validity changed nothing, and 92.4% of the runs that took the shortcut showed awareness of it [34].

Hooks, by contrast, are deterministic and "guarantee the action happens" — so rules with zero tolerance for failure belong in hooks, not in prose [1]. The Codex hooks docs qualify how far that goes: a hook is a useful guardrail, not a complete enforcement boundary, because some tool paths (hosted tools such as web search) skip the hook path, and a hook that runs after a tool cannot undo the side effect that already happened [22]. The hardest limits belong in the sandbox and permission layer *(see the sibling anti-hobbling document)*.

This reframes a chunk of what looks like bloat. A lot of it is rules that should never have been prose in the first place.

---

## What frontier labs recommend instead

### Progressive disclosure, three ways

Anthropic implements the same load-only-what's-relevant pattern at three layers of its stack:

**Skills** use explicit three-tier loading: (1) only name and description preloaded into the system prompt at startup — cheap enough to scale across 100+ skills; (2) the full `SKILL.md` body loaded only when the skill becomes relevant; (3) bundled files and scripts loaded or executed only as needed, with zero context cost until accessed. Reference files and data "don't consume context tokens until actually read," and scripts execute via bash without loading their contents at all — only output costs tokens [6]. Anthropic publishes a per-tier budget for this: roughly 100 tokens per skill for the always-loaded metadata, under 5k for a triggered `SKILL.md` body, and nothing at all for bundled files until they are read [10]. Those budgets are denominated in tokens, and the tokenizer changed underneath them: since Claude Opus 4.7, the same text produces roughly 30% more tokens, and 1M tokens holds about 555k words against about 750k before [16]. The published budgets did not change, so each now holds roughly a quarter less text. Skills were announced 16 October 2025, positioned as modular, discoverable expertise packages *instead of* ever-larger standing prompts — "custom onboarding materials" kept architecturally separate from core system instructions [7].

**Instruction files** have their own hierarchy, which is easy to miss. Managed org policy, user (`~/.claude/CLAUDE.md`), project, and `CLAUDE.local.md` files concatenate in scope order — but nested subdirectory `CLAUDE.md` files and `.claude/rules/` entries with `paths:` frontmatter load *on demand*, only when Claude touches matching files [2]. An `@import` does not defer anything: imported files expand into context at launch, so splitting a `CLAUDE.md` into imports helps organization and saves nothing [2]. Claude Code's large-`CLAUDE.md` startup notice now counts instruction files together, so many mid-sized files and imports trigger it (v2.1.281) [15]. Codex draws the same line harder: it concatenates `AGENTS.md` files from the repository root down to the working directory and stops adding files once their combined size reaches `project_doc_max_bytes`, 32 KiB by default [20].

**Runtime context** in Claude Code follows the same rule. After compaction, the project-root `CLAUDE.md`, unscoped rules, and auto memory are re-injected from disk while path-scoped rules and nested `CLAUDE.md` files come back only when retriggered, and invoked skill bodies return truncated from the end [14]; the full table of what survives is in the sibling context-rot document. For authoring, that means a rule that must survive compaction cannot be path-scoped, and a skill's key instructions go at the top.

### Converging numeric ceilings

The harnesses publish concrete limits, some advisory and some enforced:

| Artifact | Ceiling | Source |
|---|---|---|
| `CLAUDE.md` | target under 200 lines per file; `@imports` count, since they load at launch; a file over 4 MiB is skipped | [2] |
| All Claude Code instruction files | the large-file startup notice counts them together | [15] |
| `MEMORY.md` (auto-memory) | first 200 lines or 25KB, whichever comes first — hard-enforced; content past the limit silently does not load next session | [2] |
| `SKILL.md` body | under 500 lines, or under 5k tokens; split into reference files linked one level deep beyond that | [6][10] |
| Skill listing | Claude Code: description plus `when_to_use` cut at 1,536 characters per skill. Codex: whole list capped at 2% of the context window (8,000 characters if the window is unknown) | [17][21] |
| Codex `AGENTS.md` chain | 32 KiB combined; files past the cap are not added | [20] |

The `MEMORY.md` behaviour is the most instructive because it is enforced, not advisory: writing past the limit succeeds but returns an error, since the overflow will not load. When the file nears the cap, Claude Code prompts for one line per entry, detail moved to topic files, and stale entries dropped [2]. That is a working model of a context budget with a hard edge. The Codex skill cap works the same way: past it, Codex shortens descriptions first and then leaves skills out of the list with a warning, so both vendors advise putting the key use case and trigger words first in each description [17][21].

One caveat from the skills guidance: avoid *deeply nested* references. Claude may only partially read files reached through a chain of references, using `head -100`-style previews and missing information further down. Keep references one level deep, and give reference files over 100 lines a table of contents [6].

### The measured ablations

The ceilings above are stated as guidance. Two vendor measurements now sit behind the general claim. For its Claude 5 generation models, Anthropic removed **over 80% of Claude Code's system prompt** "with no measurable loss on our coding evaluations" [9]. The diagnosis in that post is not that length is inherently toxic but that the team had been *overconstraining* Claude — through the system prompt, `CLAUDE.md` files, and skills alike — and that newer models do better when overlapping constraints are deleted and the model is left to use surrounding context and judgement instead. Skills get the same treatment — "lightweight guides to let Claude find information when needed," split across files via progressive disclosure when long, and not overconstrained except in high-stakes areas [9].

OpenAI reports the same direction with a positive sign. In its internal coding-agent runs, leaner system prompts, with redundant instructions, examples, and tool descriptions removed, scored roughly **10-15% higher** while using 41-66% fewer tokens and costing 33-67% less; the numbers vary by workload [19]. Its method is the part worth copying: start from a working setup, remove one group of instructions, examples, or tools at a time, and re-run the same evals [19]. Claude Code's lead, Boris Cherny, described the same loop at YC Startup School 2026: delete most of the prompt at each model generation and add a line back only when the model repeatedly stumbles; he advised users to delete their `CLAUDE.md`, skills, and hooks every six months and watch what the model does before adding anything back [28]. OpenAI's Codex guidance for GPT-6 Astra gives the concrete reason: instructions that pushed older models to test or to stop for review now cause over-testing and early stopping, so skills and `AGENTS.md` need an audit at each model change [24].

Two limits on generalising. These are removal ablations on vendors' own prompts scored against their own evals, not length gradients across arbitrary instruction files. And they are model-generation-specific: the heavier prompts were doing real work for earlier models. The reading that holds up is that headroom to cut grows with each generation and is found by ablation — remove, re-run, restore only what a repeated mistake shows is needed — not by cutting a fixed share.

### Context files, measured

The ETH Zurich study of `AGENTS.md` files is the direct measurement this document previously listed as missing. Four agent-model pairs (Claude Code with Sonnet 4.5; Codex with GPT-5.2 and GPT-5.1 mini; Qwen Code) ran on SWE-bench Lite and on a new 138-task benchmark from 12 repositories that ship developer-written context files [18]. In the revised version (v2, 23 June 2026; the widely quoted v1 figures differ), LLM-generated context files changed success by -0.5 and -2 points, neither significant, while raising cost 20-23% and adding 2.45-3.92 steps per task. Developer-written files gained about 2.4 points, also not significant, at up to 19% more cost [18]. Agents did follow the files — tools named in them got used — so the files change behaviour; they did not change outcomes. Repository overviews did not reduce the steps needed to find relevant files, and the authors found no clear dependency between context-file length and success or cost [18]. Their advice: skip generated files, and use a developer-written one only for requirements the codebase does not already show [18].

Length was not a controlled factor there, so "no dependency" is observational, and the outcome is task success, not adherence. The one study that varies length as a controlled factor is an unrefereed preprint. A factorial design over 1,650 Claude Code sessions (16,050 function-level observations, two TypeScript codebases, Sonnet 4.6 / Opus 4.6 / Opus 4.7) manipulated `CLAUDE.md` size across 25, 100, 250, and 500 lines alongside instruction position, file architecture, and deliberate contradictions in adjacent files — and found **no detectable adherence effect from any of them** after multiple-testing correction. Its one robust effect was within-session decay: roughly 5.6% lower odds of compliance per additional function the agent generates (OR ≈ 0.944) [11]. Taken with [9], [18], and [19], the position is this: every always-loaded line has a measured cost in tokens and steps on every task, its benefit has to be shown, and pruning redundant content can itself raise quality. Length is a proxy; unneeded content is the problem.

Over months, a short file still earns its place. A panel study of 441 GitHub repositories after coding-agent adoption found that agents raised commits 28-38% at every level of configuration, but cognitive complexity rose about 53% in repositories with no committed AI configuration against about 27% in those with at least a rules or coding-standards file, and static-analysis warnings rose 1.7 times as much [33]. The decisive gap was between nothing and a few pages of rules, and 73.8% of configuration files were committed once and never edited [33]. It is observational — configuration was not randomized, and engineering discipline may explain part of the gap — but it points the same way as [18]: a short file of real rules, kept current.

A stale file is worse than none. With needed facts deliberately withheld, seven models across five harnesses followed outdated standards files even when that produced worse code, and when a fact was missing from both the context and training, agents mostly invented it rather than report the gap, though the rate of reporting it ran from every trial to none depending on the model [32]. That is the case for excluding fast-changing facts, and for flagging a standards file that contradicts the code instead of obeying it.

### What skills measurably do

Two benchmarks now measure skills directly. SkillsBench ran 87 tasks with deterministic verifiers across 18 model-harness configurations, with and without skills (v4, June 2026) [30]. Curated skills raised the average pass rate from 33.9% to 50.5%, with software engineering among the smallest gains (+11.6 points). Size decided much of it: compact and standard-length skills gained about 19-21.5 points, detailed ones 14.5, comprehensive ones 0.7; one to three skills gained 18-19 points, four or more 10.1 [30]. Skills the model wrote for itself scored below no skills at all on all three configurations tested (-8.1 to -11.5 points), and 13 of 87 tasks got worse, typically when a skill prescribed a heavier pipeline than the task needed or displaced a stronger default [30]. SWE-Skills-Bench tested 49 public software-engineering skills on about 565 tasks in real repositories: 39 gave no improvement, the mean gain was 1.2%, three made results worse, and token overhead reached 451% with pass rates unchanged. The main failure was guidance written for a different library version than the project used [31]. Routing a procedure into a skill is not itself a gain; fit and brevity decide.

Anthropic's own practice matches. Thariq Shihipar, drawing on hundreds of skills in use inside Anthropic, advises writing descriptions as triggers for the model rather than summaries for humans, treating the Gotchas section as the highest-signal content, and stating goals and constraints rather than every step; the team logs skill use from a PreToolUse hook to find skills that under-trigger [26]. Claude Code's `/skill-doctor` reports which loaded skills go unused and what they cost in context [15][17]. OpenAI adds that contradictory or over-eager descriptions lead the model to load skills that do not help, and that documentation pointers work best as conditions (use this file for that kind of change) rather than blanket rules to read several docs before every edit, which burn context [24].

### Right altitude

Anthropic frames prompt design as finding the "right altitude" — a Goldilocks zone between hardcoded, brittle, overly-specific logic and vague high-level guidance that gives the model no concrete signal. The target is "the minimal set of information that fully outlines your expected behavior," organized into distinct sections via XML tags or Markdown headers [4]. Bloat is one failure mode of altitude; over-abstraction is the other, and cutting badly produces the second problem while fixing the first.

Recent guidance sharpens what a good line looks like. Emphasis such as IMPORTANT belongs on the one instruction Claude keeps skipping, because emphasizing many lines makes none stand out [1]. Cat Wu of the Claude Code team described rewriting a blanket "always verify" rule into scoped guidance with its reason — backend checks cannot show the user experience, so run the app locally for larger UX changes — aiming for a rule that is true in every case it covers [27]. Blanket caps backfire: a system-prompt instruction limiting text between tool calls to 25 words and final responses to 100 cost about 3% on Anthropic's evals for Opus 4.6 and 4.7 and was reverted after four days [29]. For review rules, OpenAI measured the pattern: rule-guided review from `AGENTS.md` recovered 98% of required custom findings against 58.3% for the baseline, broad instructions created noise, and small scoped sets that name an invariant and a safe alternative worked best; each rule was tested with one violating change, one safe counterexample, and one unrelated change [23]. When a skill makes the agent pause or change course, OpenAI's current guidance asks it to name the `SKILL.md`, quote the instruction, and explain how it applied [25], which turns a bad rule into a visible one.

### Ecosystem convergence: AGENTS.md

`AGENTS.md` is an open, tool-agnostic "README for agents" that emerged from collaboration across OpenAI Codex, Amp, Google's Jules, Cursor, and Factory, is now stewarded by the Agentic AI Foundation under the Linux Foundation, and is used by 60,000+ open-source projects [8]. Its recommended sections are deliberately operational — build/test commands, code style, testing, security, PR conventions, deployment — explicitly kept separate from human-facing README content.

Claude Code now reads `AGENTS.md` itself (v2.1.277, extended to Bedrock, Vertex, Foundry, gateways, and telemetry-off sessions in v2.1.281), but by default only when no `CLAUDE.md`, `.claude/CLAUDE.md`, or `CLAUDE.local.md` exists in the working directory or above [2][15]. The trap is that adding a personal `CLAUDE.local.md` silently stops `AGENTS.md` from loading, unless the Project instructions setting is changed to load both [2]. The docs rank the ways to link the two files. An `@AGENTS.md` import in `CLAUDE.md` is reliable, with Claude-specific additions below it. A `CLAUDE.md` that tells Claude in words to read `AGENTS.md` works only if Claude decides to open the file. A committed symlink needs admin rights or Developer Mode on Windows, and Git checks it out as a one-line text file where `core.symlinks` is off. A SessionStart hook that prints `AGENTS.md` now loads it twice [2]. Running `/memory` shows whether `AGENTS.md` loaded [2]. This matters for bloat specifically: duplicated standing instructions per tool are where drift and contradiction start.

---

## The vendors' own tests

Anthropic's include/exclude table is the operational core [1]. Include: bash commands Claude cannot guess, non-default code style rules, testing instructions, repo etiquette, project-specific architectural decisions, environment quirks, and common gotchas. Exclude: anything Claude can infer from the code, standard conventions, detailed API docs (link instead), frequently-changing information, long tutorials, file-by-file descriptions, and self-evident practices. Repository overviews belong in the exclude column — they did not help agents find files [18], and Claude Code's `/doctor` proposes cutting directory layouts and architecture overviews from a checked-in `CLAUDE.md` [2]. The per-line question follows: would removing this line cause Claude to make mistakes? If not, cut it, and if Claude already does the thing correctly without the instruction, delete the line or convert it to a hook [1]. The token-cost framing is that Claude is already very smart and only needs context it does not have; Anthropic's worked example contrasts a ~50-token instruction with a ~150-token version of the same rule, the longer one faulted for re-explaining what the model already knows (an illustration, not a measurement — see Confirmed gaps), and treats the context window as a public good shared with the system prompt, conversation history, other skills' metadata, and the user's request [6]. Counts belong to the current model's tokenizer, not old estimates [16].

Routing follows the same table: a multi-step procedure, or anything that matters for one part of the codebase, belongs in a skill or a path-scoped rule rather than the always-loaded file [2]; anything that must happen every time with zero exceptions belongs in a hook [1], which is a guardrail rather than a boundary [22], so a limit that must never be crossed goes in the environment *(see the sibling anti-hobbling document)*. A rule should be concrete enough to verify — "Use 2-space indentation," not "format code nicely" — because vague instructions are harder to follow and hide conflicts [2], and should carry its scope and reason [27]. Loading is checked rather than assumed: in Claude Code, `/context` or `/memory` shows which files loaded, and an audit for conflicting instructions across `CLAUDE.md` files and rules goes with it [2]; in Codex, ask the agent to summarize its current instructions, or read the session log [20].

Adding and removing both run on evals. Anthropic's five-step loop is to run Claude *without* the instruction to identify the real gap, build 3+ test scenarios, establish a baseline, write minimal instructions to pass, and iterate [6] — the defence against the accretion pattern in which most bloat is instructions written for imagined failures that were never observed. Pruning runs the same loop in reverse: remove one group at a time and re-run [19]. Editing is line-at-a-time: when an LLM rewrote an agent's accumulated context as a whole, the context collapsed from 18,282 tokens to 122 and accuracy fell from 66.7% to 57.1%, below the 63.7% with no adaptation at all, while keeping the context as itemized bullets updated one at a time avoided the collapse [35]. The human sees every diff: Anthropic's Opus 5.5 system card reports rare pre-release snapshots inserting user-hostile directives into agent-directed files such as `CLAUDE.md` after trivial copying mistakes [36].

---

## Confirmed gaps (verified 2026-09-25)

- **No peer-reviewed study varies instruction-file length as a controlled factor.** The ETH study [18] now measures context files directly and finds no clear dependency of success or cost on length, but length was observed rather than manipulated. Among the studies read for this document, only [11] manipulates length, and it is an unrefereed preprint below this document's source bar. Established by reading [9], [11], [18], and [19].

- **No primary source publishes measured context savings for Skills.** The October 2025 announcement [7], the accompanying engineering post [12], the Skills overview [10], and the authoring best-practices guide [6] were each read end to end, and [10] was re-checked on 2026-09-25. The only numbers any of them carry are per-tier design budgets — ~100 tokens of always-loaded metadata per skill, under 5k for a triggered `SKILL.md`, zero for bundled files until read [10] — and one illustrative ~50-token versus ~150-token rewrite of the same instruction [6]. The skill benchmarks [30][31] measure pass rate and token overhead, not savings against an always-loaded equivalent. The measured progressive-disclosure figures Anthropic does publish belong to adjacent mechanisms: an 85% context reduction from the Tool Search Tool (77K → 8.7K tokens) and a 37% token reduction from programmatic tool calling (43,588 → 27,297 tokens on complex research tasks) [13]. Any "X% context saved by Skills" figure is therefore borrowed from those tool-use results or unsourced.

---

## Sources

1. Anthropic (Claude Code Docs), *Best practices for Claude Code*, 2026 (current docs; redirect target of the original April 2025 "Claude Code: Best practices for agentic coding" post) — https://code.claude.com/docs/en/best-practices
2. Anthropic (Claude Code Docs), *How Claude remembers your project (Memory / CLAUDE.md / AGENTS.md)*, 2026 (current docs) — https://code.claude.com/docs/en/memory
3. Chroma Research (Kelly Hong, Anton Troynikov, Jeff Huber), *Context Rot: How Increasing Input Tokens Impacts LLM Performance*, 14 July 2025 — https://www.trychroma.com/research/context-rot
4. Anthropic (Engineering blog), *Effective context engineering for AI agents*, September 2025 — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
5. OpenAI (API docs), *GPT-5.5 prompt guidance*, 2026 (current docs; supersedes the August 2025 *GPT-5 prompting guide* cited earlier) — https://developers.openai.com/api/docs/guides/prompt-guidance?model=gpt-5.5
6. Anthropic (Claude Platform Docs), *Skill authoring best practices*, current docs (Skills launched October 2025) — https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
7. Anthropic (claude.com/blog), *Claude Skills announcement*, 16 October 2025 — https://claude.com/blog/skills
8. AGENTS.md (open standard co-created by OpenAI Codex, Amp, Google Jules, Cursor, Factory; stewarded by the Agentic AI Foundation under the Linux Foundation), *AGENTS.md* — https://agents.md/
9. Anthropic (claude.com/blog), Thariq Shihipar, *The new rules of context engineering for Claude 5 generation models*, 24 July 2026 — https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models
10. Anthropic (Claude Platform Docs), *Agent Skills* (overview, incl. progressive-disclosure token-cost table), current docs — https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
11. Damon McMillan, *Instruction Adherence in Coding Agent Configuration Files: A Factorial Study of Four File-Structure Variables*, arXiv:2605.10039 [cs.SE], 11 May 2026 — https://arxiv.org/abs/2605.10039 — **unrefereed preprint, no listed venue; below this document's primary source bar and cited only as corroboration**
12. Anthropic (Engineering blog), Barry Zhang, Keith Lazuka, Mahesh Murag, *Equipping agents for the real world with Agent Skills*, 16 October 2025 — https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
13. Anthropic (Engineering blog), *Introducing advanced tool use on the Claude Developer Platform*, 24 November 2025 — https://www.anthropic.com/engineering/advanced-tool-use
14. Anthropic (Claude Code Docs), *Explore the context window* (incl. "What survives compaction"), current docs — https://code.claude.com/docs/en/context-window
15. Anthropic, *claude-code CHANGELOG.md*, entries v2.1.261 (`/skill-doctor`), v2.1.277 (AGENTS.md support), v2.1.281 (combined instruction-file notice; AGENTS.md on Bedrock, Vertex, Foundry, gateways) — https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md
16. Anthropic (Claude Platform Docs), *Models overview* and *release notes* (tokenizer introduced with Claude Opus 4.7; roughly 30% more tokens for the same text), current docs — https://platform.claude.com/docs/en/about-claude/models/overview and https://platform.claude.com/docs/en/release-notes/overview
17. Anthropic (Claude Code Docs), *Extend Claude with skills*, current docs — https://code.claude.com/docs/en/skills
18. Thibaud Gloaguen, Niels Mündler, Mark Müller, Veselin Raychev, Martin Vechev (ETH Zurich), *Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?*, arXiv:2602.11988, v2 23 June 2026 (v1 12 February 2026) — https://arxiv.org/abs/2602.11988
19. OpenAI (API docs), *GPT-5.6 prompt guidance*, 2026 (current docs) — https://developers.openai.com/api/docs/guides/prompt-guidance?model=gpt-5.6
20. OpenAI (Codex docs), *Custom instructions with AGENTS.md*, current docs — https://learn.chatgpt.com/docs/agent-configuration/agents-md
21. OpenAI (Codex docs), *Build skills*, current docs — https://learn.chatgpt.com/docs/build-skills
22. OpenAI (Codex docs), *Hooks*, current docs — https://learn.chatgpt.com/docs/hooks
23. OpenAI (Developers blog), *Custom Code Review rules for Codex*, 2026 — https://developers.openai.com/blog/custom-code-review-rules-for-codex
24. OpenAI (Developers blog), *Rethinking skills and prompts for GPT-6 Astra*, 2026 — https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra
25. OpenAI (API docs), *Latest model guidance* (GPT-6 Astra, Sol, Luna), current docs — https://developers.openai.com/api/docs/guides/latest-model
26. Thariq Shihipar (Anthropic), *Lessons from building Claude Code: How we use skills*, 3 June 2026 — https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/
27. Simon Willison, notes on *A Fireside Chat with Cat and Thariq from the Claude Code team* (AI Engineer World's Fair), 21 July 2026 — https://simonwillison.net/2026/Jul/21/cat-and-thariq/ — **third-party notes of a talk**
28. Boris Cherny interviewed by Diana Hu, *We Cut 80% of Claude Code's Prompt* (YC Startup School 2026), 2026; summary dated 2 August 2026 — https://www.youtube.com/watch?v=qyPCVqFUyDo — **video; content checked against a third-party summary (https://www.barath.ai/learnings/boris-cherny-yc-startup-school-2026) and corroborated by [24]**
29. Anthropic (Engineering blog), *An update on recent Claude Code quality reports*, 23 April 2026 — https://www.anthropic.com/engineering/april-23-postmortem
30. Xiangyi Li et al., *SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks*, arXiv:2602.12670, v4 14 June 2026 — https://arxiv.org/abs/2602.12670
31. Tingxu Han, Yi Zhang, Wei Song, Chunrong Fang, Zhenyu Chen, Youcheng Sun, Lijie Hu, *SWE-Skills-Bench: Do Agent Skills Actually Help in Real-World Software Engineering?*, arXiv:2603.15401, 16 March 2026 — https://arxiv.org/abs/2603.15401
32. Bardia Mohammadi, Lars Klein, Aman Chadha, Akhil Arora, Laurent Bindschaedler, *The Working Set of a Coding Agent: Coherence Debt in Repository-Scale Tasks*, arXiv:2608.16630, 17 August 2026 — https://arxiv.org/abs/2608.16630
33. Yegor Denisov-Blanch, Shyam Agarwal, Pavel Azaletskiy, Hao He, Rylan Schaeffer, Brando Miranda, Bogdan Vasilescu, Sanmi Koyejo, *A Few Pages of Markdown: Committed AI Configuration and Lower Quality Cost after Coding-Agent Adoption*, arXiv:2608.25241, v2 14 September 2026 — https://arxiv.org/abs/2608.25241 — **observational panel study**
34. Pradyumna Shyama Prasad, Meiri Anto, Leon Eshuijs, Julian Moncarz, Kaustubh Kislay, Juan J. Vazquez, *BAITBENCH: Measuring Agent Reward Hacking with Optional Shortcuts Planted in ML Tasks*, arXiv:2608.30724, 31 August 2026 — https://arxiv.org/abs/2608.30724
35. Qizheng Zhang, Changran Hu, Shubhangi Upasani, et al., *Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models*, ICLR 2026, arXiv:2510.04618 (v3 29 March 2026) — https://arxiv.org/abs/2510.04618
36. Anthropic, *System Card: Claude Opus 5.5* (section 6.3.1.1, spontaneous prompt injections), 22 September 2026 — https://www-cdn.anthropic.com/fc1b44717c85dc068bc6ba5024219938094694bd/Claude%20Opus%205.5%20System%20Card.pdf
