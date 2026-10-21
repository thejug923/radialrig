## RadialRig route map

**This section routes; it does not rule.** The rulebook is
[docs/spec.md](docs/spec.md). RadialRig maintains this section between
its fence comments; edit outside them freely. Codex reads this file
directly, Claude Code through the `@AGENTS.md` import in `CLAUDE.md`.

### Routing rules

1. Answer "what rule applies now" only from a governed record with
   `status: in-force` in its frontmatter; one such record exists per
   key and scope. Cite the record's path in every answer that relies
   on it.
2. Default searches exclude the archive. In Claude Code the working form
   is `!**/docs/archive/**`; the form this project relies on is recorded
   in `docs/spec.md` section 2. **Test the exclusion on content that is
   really in the archive.** A glob that fails to exclude fails silently,
   so the mount seeded one archived record under `docs/archive/` to test
   against, carrying the marker phrase
   `radialrig-archive-exclusion-marker`. A search for that marker with
   the form applied finds no hit under `docs/archive/`; a hit there
   means the form failed. Read the archive only when the task explicitly
   asks about history.
3. When records seem to compete, apply SPEC-1 section 7; a tie is
   reported, never resolved silently.
4. Generated summaries and indexes are never authoritative.
5. A canonical decision lives in a governed record, never in agent
   memory. Memory may hold a pointer to a record key, never the ruling.
6. Prefer the retrieval tools when the harness provides them:
   `get_ruling` answers "what rule applies now", `search_history` is the
   explicit history access, `check_docs` runs the mechanical checks,
   `get_guidance` serves the research guidance. Query by record key when
   you know it. The files under Paths remain the fallback, and rules 1–3
   apply to them unchanged.
7. Change a governed record through the authoring verbs when the harness
   provides them (`propose_record`, `amend_ruling`, `retire_ruling`); a
   direct edit under SPEC-1 section 8 is the fallback. Either way the
   change stops at the working tree. **The verb returns the ruling as
   one plain sentence; the agent shows that sentence to the person and
   reads the diff, and nothing is committed until the person says
   yes.**

### Paths

- `docs/spec.md` — SPEC-1: record format, status vocabulary, lifecycle.
- `docs/spec-git-environment.md` — SPEC-2: branches, the human gate, the
  CI budget. A project can hold further specs; this file names no
  complete list of them. `list_rulings` carries the catalog.
- `docs/decisions/` — current truth, one in-force record per key and
  scope. This file names no record ids; call `list_rulings` or read
  the directory for the catalog.
- `docs/plans/` — execution plans.
- `docs/archive/` — history and evidence. Excluded by default (rule 2).
- `docs/templates/` — skeletons for a new decision, plan, or spec.
- Checks (C1–C7): run `radialrig-validate`. In a project not under git,
  add `--no-baseline`; the lifecycle check then reports that it did not
  run.

### Tools

The retrieval server is registered as `radialrig`, running
`{{TOOL_CMD}}`. It is read-only. It resolves the corpus from the working
directory the client starts it in; when a client cannot set that
directory, set `RADIALRIG_DOCS_ROOT` to this project's root.

### The authoring server

The authoring server is registered as `radialrig-write`, running
`{{WRITE_TOOL_CMD}}`. It writes governed records and stops at the working
tree: no verb touches git. To switch capture off, remove the
`radialrig-write` entry from the client configuration; nothing else
changes, and unmount and upgrade keep working around the removal.
