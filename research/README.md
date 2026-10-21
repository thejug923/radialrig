# Agent–Human Collaboration Research

Nine final documents on agentic development ("vibecoding") with frontier agents.
Sources: Anthropic and OpenAI publications, research labs that measure
frontier-model behavior (Chroma, METR), and peer-reviewed and preprint papers.
Where the public record is thin, a document may add RadialRig's own
measurement as a dated plain note, never as a numbered source.
Weighted toward 2025–2026 guidance. Each document carries frontmatter, inline
citations, and a Sources list; where the public record lacks an answer, a dated
"Confirmed gaps" section records the negative finding rather than leaving the
question open.

Produced 2026-08-17 by a three-phase workflow: per-topic research with
URL-verified findings, per-topic writing, then a high-effort editorial pass per
pillar that spot-checked ~70 claims against primary sources and fixed or
deleted every claim a source did not support. A second verification round the
same day drove every remaining open question to either a resolved, sourced
claim or a confirmed-gap statement verified against primary sources.

Refreshed 2026-09-25: five source sweeps (Anthropic, OpenAI, papers, safety
failures in agent-driven development, and a staleness audit of this corpus)
corrected stale claims in all six original topics and added three new ones.
Fresh checkers re-fetched every new or changed claim, and a final review cut
repetition across topics. The sweep trail was stowed on 2026-09-29 in a
private archive, last held in the tree at commit c663b26; the ledger in
`research-staging/README.md` gives its name, location and digest.

Corrected 2026-09-27: the memory-management topic, from the memory-skill
research (PLAN-0020 phase 2). It now says which Claude Code subagents
receive the memory index, gives the current Managed Agents store limits,
records Claude Code's undocumented, switched-off background consolidation
pass, and says how a session opens when an index is already loaded, which
memory lines bind, and that progress belongs in the plan, not the memory
store. A fresh checker re-fetched every changed claim.

## For agents

Call `get_guidance`, or read [INDEX.md](INDEX.md) without the tools, and load the guidance a trigger matches; this file is for humans.

## Pillar 1: Agent focus — what a current session owes a future session

| Document | Core conclusion |
|---|---|
| [Context Rot](agent-focus/context-rot/context-rot.md) | Degradation with context length is a measured gradient, not folklore. Treat context as a budget: small high-signal token sets, just-in-time retrieval, deliberate compaction, and handoff artifacts a future session can read instead of re-exploring. |
| [Memory Management](agent-focus/memory-management/memory-management.md) | The context window is degrading working memory, not storage. Push durable state to external files or stores; write memory assuming interruption; keep a small always-loaded index over on-demand detail; prune rather than append. |
| [Documentation Bloat](agent-focus/documentation-bloat/documentation-bloat.md) | Always-loaded instruction bloat degrades agents through attention dilution and instruction conflict. Keep standing files lean (under ~200 lines), move procedures to on-demand skills, and enforce zero-tolerance rules with hooks, not prose. |

## Pillar 2: Human focus — the agent–human relationship

| Document | Core conclusion |
|---|---|
| [Visualizations](human-focus/visualizations/visualizations.md) | Labs ship rich visual surfaces but publish little evidence that visuals beat prose. Default to structured text with provenance; escalate to visuals only for genuinely relational or comparative content. |
| [Expectation](human-focus/expectation/expectation.md) | Agents are reliable on short, bounded, verifiable tasks; reliability falls as duration, ambiguity, and blast radius grow. The human's non-delegable job is verification, review, and taste. |
| [Anti-Hobbling](human-focus/anti-hobbling/anti-hobbling.md) | Much agent underperformance is human-inflicted: over-specification, micromanagement, and 2023-era permission gates. Give outcome-level instructions at the right altitude and scale autonomy with verification. |
| [Blocked Tasks](human-focus/blocked-tasks/blocked-tasks.md) | When a task cannot be finished as posed, frontier agents often game the check, route around a denial, or invent what is missing, usually knowing it. An explicit way to stop and flag the task, and checks the agent cannot edit, cut this sharply; a written rule against gaming barely does. |
| [Untrusted Content](human-focus/untrusted-content/untrusted-content.md) | Only the person in the chat gives instructions. Pasted text, cloned repositories, and packages installed by a suggested name are each a measured attack path; read before running, confirm names before fetching, and get a plain yes for any instruction found in data. |
| [App Security](human-focus/app-security/app-security.md) | Code an agent writes can work and still be unsafe, and a better model does not close the gap. Treat unstated security rules as required, keep secrets out of code, and get an independent review before calling an app ready. |
