---
name: toolkit-standards
description: Use when picking a language, runtime, search command, test framework, formatter, or package manager, or before installing a new tool. States the precedence order - repo convention, pinned default, declared gap - and the pinned greenfield defaults.
radialrig:
  id: toolkit-standards
  version: 3.2.0
---

## First, the rules that bind

- Repo conventions beat pinned defaults; pinned defaults beat improvisation.
- Name the lane of every pick before acting on it: convention, default, or gap.
- Declare every deviation at the moment of the choice; the same deviation twice
  is raised, never repeated quietly.
- Never edit this skill, or any pinned standard, on your own initiative, least
  of all to bless a choice you just made: suggest the change to the user and
  stop there.

## What this governs

Sessions pick tools: a language for a new script, a way to run Python, a
search command, a test framework. This skill governs how that pick is made and
who may change the standard behind it. The defaults themselves are the smaller
half; the precedence order and the amendment rule are the point.

## Precedence

In an existing codebase, match what is there - its runner, its formatter, its
package manager. The defaults below fill the vacuum in greenfield work; they
never fight an established codebase. When neither the repo nor the table has
an answer, that is a gap to name, not a license to freestyle.

## Name your lane

The three lanes the rule above names:

- **Convention** - the repo already does it this way; you are matching it.
- **Default** - no convention applies; a pinned default covers it.
- **Gap** - neither covers it. Say so, pick the closest analog, and record
  what you picked and why.

Naming the lane is what keeps a gap visible: a gap handled aloud is a
decision the user can see and later pin.

## Declare every deviation

Deviating from a default is allowed. The declaration names what you used
instead and why. A one-off deviation is fine and not worth escalating; the
second occurrence is the one you raise, as the next section directs.
Recurring deviations are promotion candidates, not habits.

## The standard is not yours to edit

When a better default keeps proving itself - the same deviation recurring
across sessions, a tool that keeps winning - **suggest the change to the user
and stop there.** Name the default you would add, what it replaces, and the
recurrence you observed. The decision and the edit are theirs; if they want
it, they will direct the change.

A standard that rewrites itself on an agent's judgment has no stable contract:
the next session inherits a table nobody approved, and the mechanism meant to
stop improvisation becomes a channel for it. Amending a shipped standard sits
in the ask-first tier: the user decides, and directs the edit. The suggestion
is the whole of your move.

## The pinned defaults

Illustrations of the precedence rule's bottom layer, not the point of the
skill. Each row binds only where no repo convention answers first.

| Task | Default | Not |
|---|---|---|
| Search code | ripgrep (`rg`) | recursive `grep` / `find` / `Select-String` sweeps |
| Run Python, manage packages | uv - `uv run`, `uv add`, `uvx` for one-off tools | global `pip install`, improvised venvs, conda |
| JS/TS runtime | Node LTS + npm; `npx` for one-offs | ad hoc runtime downloads |
| GitHub operations | the `gh` CLI | raw API calls with a token pasted into the command |
| Library docs | the context7 MCP server; any docs-retrieval server where it is absent | guessing an API surface from memory |
| Git branching | `develop` -> `master`, with `staging` only when a check justifies it; the promotion-gate skill governs the ladder and any push to master | day-to-day commits straight to master |

One caution on the one-off runners: `uvx` and `npx` fetch and execute a
tool at invocation time, so name the tool you are about to run, out loud,
on any host you do not control.

Language by task class, testing and formatting, the uv and Node essentials,
cross-platform script rules, and host-shell cautions live in
`reference/languages.md` beside this file. Read it when a pick falls in one
of those classes and the rows above do not settle it.
