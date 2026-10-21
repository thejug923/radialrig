# Route map

**This file is the always-loaded entry point. It routes; it does not rule.**
The rulebook is [docs/spec.md](docs/spec.md).

## Rules that always apply

1. Answer "what rule applies now" only from a governed record with
   `status: in-force` in its frontmatter; one such record exists per key
   and scope (D-01). Cite the record's path in every answer that relies
   on it.
2. Default searches exclude the archive; in Claude Code the working form
   is `!**/docs/archive/**` (D-04 carries the test for it). Read the
   archive only when the task explicitly asks about history.
3. When records seem to compete, apply SPEC-1 section 7; a tie is
   reported, never resolved silently (D-09).
4. Generated summaries and indexes are never authoritative (D-07).
5. A canonical decision lives in a governed record, never in agent memory.
   Memory may hold a pointer to a record key, never the ruling (D-30).
6. Prefer the retrieval tools when the harness provides them: `get_ruling`
   answers "what rule applies now", `search_history` is the explicit
   history access, `check_docs` runs the mechanical checks,
   `get_guidance` serves the research guidance. Query by record key when
   you know it. The files under Paths remain the fallback, and rules 1–3
   apply to them unchanged (D-13, SPEC-2).
7. Change a governed record through the authoring verbs when the harness
   provides them — `amend_ruling`, `retire_ruling`, `propose_record`
   (SPEC-3); a direct edit under SPEC-1 section 8 is the fallback. **The
   verb returns the ruling as one plain sentence; show it to the person,
   read the diff, and commit nothing until the person says yes** (D-18).

This repository is the public base of RadialRig, exported from the
workshop where it is built. Some records cite records, plans and history
this repository does not carry: the execution plans, the bug notes, the
archive's past positions, and two records kept in the workshop. Read such
an id or path as the source of a rule the citing record states in full,
not as a file to open.

## Paths

- `docs/spec.md` — SPEC-1: record format, status vocabulary, lifecycle.
- `docs/spec-retrieval-tools.md` — SPEC-2: the retrieval verbs.
- `docs/spec-authoring-tools.md` — SPEC-3: the authoring verbs.
- `docs/spec-git-environment.md` — SPEC-4: branches, the human gate, CI.
- `docs/decisions/` — current truth, one in-force record per key and
  scope. No record ids are listed here (D-20); call `list_rulings` or
  read the directory for the catalog.
- `docs/archive/` — the archive's marker file only (rule 2).
- `evals/` — the retrieval and memory eval suites; see its README.
- Research guidance: call `get_guidance`, or read `research/INDEX.md`
  when the tools are absent. Load the guidance a trigger matches and
  stop (D-31).
- `src/radialrig/validate_docs.py` — mechanical checks (C1–C7).
- `README.md` — written for people: installing a release, the fork-only
  rule, and the values a fork changes to make the build its own. Open it
  when the task is a fork's own build.

## Checks before a merge

Run both and read each command's exit code:

- `uv run --with pytest pytest -q` — the test suite.
- `uv run radialrig-validate` — the mechanical checks.

While an MCP server from this repository runs, `uv sync` cannot replace
its executable; after a `pyproject.toml` change, run the checks with
`--no-sync` or stop the server first.
