---
topic: visualizations
pillar: human-focus
audience: agent
load-when: Deciding whether output should be visual at all, or about to render a chart, diagram, dashboard, or published page for a person.
evidence: ./visualizations.md
date: 2026-09-27
status: final
---

# Visualizations — Agent Rules

## Decide whether to visualize

- Default to structured text. Render a visual only when the content is easier to see than to read: relational or comparative material.
- Visualize annotated diffs, side-by-side option comparisons, dependency and handoff graphs, timelines, state-over-time, and status boards.
- Never build a visual for a single recommendation, a short list, a yes/no answer, or one number with caveats. A short table beats a chart.
- Never treat a visual as a trust mechanism. Polish is applied automatically, so a wrong artifact looks as authoritative as a right one, and readers will not detect the flaws for you.

## Choose what to show

- Render plans and actions: the task graph, tool calls made, files touched, decision points. These are checkable against what you did.
- Never render narrated reasoning as a transparency artifact; displayed reasoning does not reliably reflect the computation behind it. When you do surface reasoning, label it unverified narration.
- Tune process visibility to the task, not to maximum exposure. Past a point, more visible process costs the reader effort without adding information.
- For consequential work with no mechanical check, have the person test the output themselves and watch the steps during development rather than trust a summarized artifact or an automated judge; where a mechanical check exists, verify the result instead (see the anti-hobbling guidance).

## Attach provenance and check the render

- Put a short provenance line inside every chart, dashboard, or metric you produce: where the data came from, when it was pulled, and what is unverified.
- Show your work in the visual: filters applied, inclusions and exclusions, and the denominator behind every rate or percentage. Provenance earns trust; presentation does not.
- Before calling a visual done, render it and inspect the rendered output, not its source, for layout, clipping, spacing, and missing content; revise until it shows what was asked. If you cannot render it, say it is unchecked.

## Match format to lifespan and cost

- Pick the cheapest tier that works for how long the output needs to live.
- Default to inline and ephemeral: a mermaid block or small SVG that makes one point mid-conversation and is then discarded.
- Publish a persistent artifact only when the output has an audience beyond the person in the session: a PR walkthrough, an options memo, an investigation timeline.
- Build output that readers will navigate or compare as one self-contained HTML page rather than Markdown, and send the link instead of pasting a wall of text.
- Build a live or connected page only when staleness is the actual failure mode (status boards, tracking dashboards) and viewers hold the relevant connectors themselves.
- Treat a published artifact as a capture of work, not an application. When the need includes viewer authentication or multiple routes, deploy real infrastructure; when it needs saved or shared state, use a capability the harness lets the page declare (in Claude Code, the artifact database).
- Budget output tokens: a styled page costs more than the same content as terminal text, so pay that for output people will navigate or share, not for a short answer.
- Prefer SVG or HTML+CSS over embedded raster images, omit interactivity you do not need, and summarize large datasets in the page rather than inlining them.
