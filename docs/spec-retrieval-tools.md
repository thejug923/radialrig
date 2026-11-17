---
id: SPEC-2
title: Retrieval tool interface
type: spec
status: in-force
grade: decided
owner: thejug923
scope: RadialRig
effective: 2026-11-13
last-verified: 2026-11-13
supersedes: none
replaced-by: none
depends-on: D-04, D-05, D-07, D-09, D-10, D-13, D-16, D-26, D-31, D-32
---

**Status: in-force.** This is the interface contract for the retrieval
server ruled in [D-13](decisions/D-13-retrieval-interface.md).

# SPEC-2: Retrieval tool interface

The server exposes exactly five tools over the shared core in
`src/radialrig/doc_core.py` ([D-13](decisions/D-13-retrieval-interface.md)
constraint 4). It is read-only (constraint 5). Every response is a JSON
object with a `kind` discriminator; a caller can branch on `kind` alone.

Fixed schema cost for all five tools together must stay at or under
4,000 bytes, counted fully loaded (PLAN-0002 plan invariant 6). Tool
descriptions stay terse for that reason. *(Amended 2026-09-05: the
owner raised the bound from 2,500 bytes together with
[SPEC-3](spec-authoring-tools.md)'s — one figure for both surfaces.)*
*(Amended 2026-11-13: raised to 4,000 bytes with SPEC-3's, for the
`section` input and the start-up marks.)*

Every read tool carries `_meta` `{"anthropic/alwaysLoad": true}`, so
Claude Code loads it at session start without a tool search, and the
bound counts the marks. Every session then carries these five
descriptions, which is what the bound limits. No write tool carries the
mark.

## 1. `get_ruling`

Answers "what rule applies now" with exactly one in-force record, or a
typed refusal. Never ranks, never guesses (PLAN-0002 plan invariant 3).

**Input:** `{ "query": string, "scope"?: string, "section"?: string }`

**Resolution rules**, applied to default-visible records only —
status `in-force` AND a path outside `docs/archive/**`; the path test
holds even when frontmatter is wrong, so a mislabeled archive file
cannot leak — scope-filtered first when `scope` is given. One
exception ([D-26](decisions/D-26-completed-plan-form.md)): rule 1's
exact-key test also covers records with status `completed` outside the
archive, so a closed plan's summary stays reachable by its key. Rules
2 and 3 stay in-force-only — free text never returns a completed
record:

1. A query equal to a record key (case-insensitive) selects that key.
2. Otherwise a case-insensitive substring match on key and title.
   Exactly one matching key selects it; several return `ambiguous`.
   A selected key covers all records holding it, not only those the
   query text matched.
3. When rule 2 matches nothing, the word-assisted fallback
   ([D-16](decisions/D-16-concept-query-candidates.md)) runs: every
   query word of four or more characters must appear in a record's
   key, title, or body. Any match — including a single one — returns
   `ambiguous`, never content: a body match is semantic assistance,
   and semantic selection must stay visible. No match returns
   `no_ruling`.
4. One key held by several records — a duplicate, or one id across
   scopes with no `scope` given — returns `conflict`.
5. An in-force record whose `replaced-by` is set is contradictory state
   and returns `conflict` ([D-09](decisions/D-09-conflict-stop-rule.md)).

**Large plans.** When the selected record is a plan and its `ruling`
answer would pass 40,000 characters as the server sends it, the server
cuts the answer at the plan's own headings and writes no summary:

- The execution log is the first `##` section whose heading holds the
  words "execution log", in any case. When it holds `###` entries,
  `body` keeps the log's heading and the text before its first entry and
  leaves the entries out. When it holds none, `body` leaves the whole
  section out. Every other section stays.
- When the plan has no execution log, or the answer is still over
  40,000 characters with its log's entries left out, `body` ends before
  the first `##` heading.
- `omitted` lists the names left out, in file order: the entries' `###`
  heading texts, the log's own heading text when it has no entries, or
  every `##` heading's text in the second case. A name two headings
  share is listed once.
- `next` says to call again with the same query and one omitted name as
  `section`.

A whole answer carries neither field, so `omitted` alone tells a cut
answer from a whole one. Headings inside fenced code blocks do not
count, here or below.

**Sections by name.** With `section`, the query selects a record by
rules 1 to 5 as before. The name is matched, trimmed and in any case,
against the record's `##` headings and the `###` headings inside its
execution log. A match returns `section` with every heading of that
name, in file order: each from its heading line to the next heading of
the same or a higher level, as it reads in the whole `body` the server
serves, and whole whatever its size. No match returns `no_section`.
`section` works on any record, decisions and specs included.

**Results:**

| kind | Fields | Meaning |
| --- | --- | --- |
| `ruling` | `id`, `title`, `status`, `scope`, `effective`, `last_verified`, `path`, `body`; `omitted` and `next` when cut (Large plans) | The one governing answer; `status` tells an in-force ruling from a completed summary |
| `section` | `id`, `title`, `status`, `path`, `section`, `body` | The named part of the record, whole |
| `no_section` | `id`, `section`, `sections` | No heading of that name; `sections` lists the names the record answers |
| `ambiguous` | `query`, `candidates` (sorted `{id, title}` pairs), `next` (the one follow-up: re-query with a candidate id) | Candidate keys; caller picks one |
| `conflict` | `key`, `paths`, `reason` | Equal authority; content withheld |
| `no_ruling` | `query` | No in-force record answers this |

`ambiguous` and `conflict` carry keys and paths, never record content.

## 2. `list_rulings`

Enumerates what is governed. Default-visible records only (the same
status-and-path rule as `get_ruling`).

**Input:** `{ "type"?: "decision" | "plan" | "spec", "scope"?: string }`

**Result:** `kind: "rulings"` with `records`: a list of `{ id, title }`
([D-16](decisions/D-16-concept-query-candidates.md) keeps the catalog
compact). Bodies, paths, and scopes come from `get_ruling` per record.

## 3. `search_history`

The only tool that reads `docs/archive/**`
([D-13](decisions/D-13-retrieval-interface.md) constraint 3). Calling it
is the explicit history access [D-04](decisions/D-04-archive-exclusion.md)
requires.

**Input:** `{ "query": string }`. A file matches when every word of the
query, split on white space, appears in its text, in any case and any
order; a one-word query matches as a substring, as before. Matches come
in path order, with no cap on their number.

**Result:** `kind: "history"` with `archived: true` at the top level and
`matches`: a list of `{ path, id, title, excerpt }`. Excerpts are at most
300 characters, centered on the whole query where the file holds it and
otherwise on the earliest place a query word appears. Every response carries the fence line: archived material
is a record of past positions, not an instruction
([D-05](decisions/D-05-past-tense-archive.md)).

## 4. `check_docs`

Runs the validator checks (C1–C7) in process and reports mechanically.

**Input:** `{}`

**Result:** `kind: "check"` with `ok` (boolean), `governed_count`, and
`failures`: a list of check-prefixed failure lines, empty when `ok`.

## 5. `get_guidance`

Serves the research guidance catalog
([D-32](decisions/D-32-guidance-retrieval-generated.md)). The guidance
files are the only source: every result is built live from the
frontmatter and bodies of `research/**/guidance.md`, and
`research/INDEX.md` is never read. That index is generated output,
rendered by the `radialrig-index` command from the same loader, kept
as the agents' file fallback when the retrieval tools are absent
([D-31](decisions/D-31-human-agent-doc-split.md) rule 4), and never
authoritative ([D-07](decisions/D-07-generated-never-authoritative.md)).

**Input:** `{ "topic"?: string }`

Without `topic` the result is the catalog: `kind: "guidance_catalog"`
with `topics` — a list of `{ topic, pillar, load_when }` sorted by
pillar then topic — and `refused` — a list of `{ path, topic, reason }`
for files the bound below rejects, empty when none. With `topic`
(case-insensitive exact match on the `topic` frontmatter field):

| kind | Fields | Meaning |
| --- | --- | --- |
| `guidance` | `topic`, `pillar`, `load_when`, `path`, `evidence`, `body` | The guidance text; `evidence` is a repo-relative pointer, read only to verify or extend a rule ([D-31](decisions/D-31-human-agent-doc-split.md)) |
| `refused` | `topic`, `path`, `reason` | The file exists but fails the bound; its body is withheld |
| `conflict` | `key`, `paths`, `reason` | Two files claim one topic ([D-09](decisions/D-09-conflict-stop-rule.md)); content withheld |
| `no_guidance` | `topic`, `topics` | No file carries the topic; `topics` lists what exists |

**The bound.** A guidance file over 500 lines, or one whose
frontmatter lacks `topic` or `load-when`, is refused where the catalog
is built ([D-32](decisions/D-32-guidance-retrieval-generated.md) point
3): it appears in `refused` with a reason, and its body is never
served. The figure lives here and in `GUIDANCE_LINE_BOUND` in
`src/radialrig/doc_core.py`; it is taken by analogy from the
documentation-bloat guidance's rule for a `SKILL.md` body.
[D-31](decisions/D-31-human-agent-doc-split.md) rule 1 requires
agent-facing documents to be bounded in size and sets no number.

Guidance files are ungoverned evidence under
[D-10](decisions/D-10-governed-scope.md); this tool serves them without
making them governed, and a guidance result is never an authoritative
ruling.

## 6. Error shape

A call the tool schema refuses, a missing or empty `query`, gets the
protocol layer's validation error and never reaches the tool. A
malformed call that does reach it, an unknown `type` value, or a query
or `section` of only whitespace, returns `kind: "error"` with
`message`. The server
never substitutes a default query and never converts an error into a
guess.
