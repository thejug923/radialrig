# evals/

Ungoverned evidence. Nothing here is a governed record: the validator
does not read this directory, and no rule in `docs/` may cite it as
authority. It holds the evaluation suite for the packaged read server
(`uv run radialrig-read`), built under PLAN-0005 Phase 5.

## What is here

- `retrieval-evals.xml` — six question-answer pairs in the mcp-builder
  skill's shape (`<evaluation><qa_pair><question/><answer/></qa_pair>`).
  Each question is read-only, independent of the others, and needs
  several tool calls across the ruling catalog, the records and the
  guidance. Every answer rests on settled rule text — never on a live
  count that routine governance work would move — and every answer was
  solved against the live server before it was recorded.
- `run_retrieval_evals.py` — a deterministic runner. It parses the XML,
  spawns the packaged read server over stdio, replays the recorded
  resolution for each question, and compares by exact string.

This copy leaves out four more questions that the project keeps. Their
answers sit in history this repository does not carry: the archive of
past positions, and the History section each record loses when it is
published here. They ask which record forced four archived wording
changes, what run count an archived entry kept, which tool an amendment
added to an archived tool contract, and which record began as a file
outside the governed set. The runner keeps their solvers, so one runner
serves both question files.

## Running it

From the repository root:

```
uv run --with "mcp>=2,<3" python evals/run_retrieval_evals.py
```

It prints one line per pair — outcome, tool-call count, expected and
actual — then a `6/6 reproduced` summary. It exits non-zero if any
pair misses.

Options: `--command` (default `uv run radialrig-read`), `--cwd`
(default the repository root, which is where the server resolves the
corpus from), `--evals` (default `retrieval-evals.xml` beside the
runner).

The runner is not part of the pytest suite and is not wired into it.

## The XML also drives the skill's LLM harness

`retrieval-evals.xml` is in the format the mcp-builder skill's
evaluation harness reads, so the same pairs can be run against a model
instead of against a recorded procedure. `evaluation.py` ships with the
mcp-builder skill, not with this repository — run it from a checkout of
that skill, not from this repository root:

```
python scripts/evaluation.py -t stdio -c uv -a run radialrig-read \
  <path to this repository>/evals/retrieval-evals.xml
```

That measures whether a model with only these tools reaches the same
answers. This runner measures something narrower and cheaper: that the
answers are still reachable from the server at all.

## The memory ablation

`run_memory_ablation.py` measures one thing: whether one memory entry
changes the first answer a fresh session gives. It was built under
PLAN-0020 phase 1 so the memory skill can state a measured effect
(D-37 rule 2, D-40 rule 6). It does not measure whether an agent writes
good entries, whether an entry survives a long session, or anything
about the skill's upkeep pass; those are separate questions, and the
memory writer below takes the first.

How it works: the fixture under `memory-ablation/` is a small made-up
project (Tidepool, a bookmark-sync tool) with a memory store in the
Claude Code layout: an index plus detail files. The index carries one
owner-decision line (an email digest the owner killed), one
agent-lesson line (a staging bucket name, written as a claim to
verify), two filler entries and a canary. For each case the runner
copies project and store to scratch twice, removes the case's index
line and detail file from one copy, points the harness at each copy
with a settings override, and runs the case's inviting prompt in fresh
print sessions: no session persistence, tools limited to Read, Grep and
Glob, MCP servers off, the session's working directory the scratch
project. The prompts invite the idea without naming it. A canary pair
runs first and the run aborts unless the store loads in the present arm
and not in the absent one. Answers are stored under random ids and
graded blind against `memory-ablation/rubric.md`, written before any
run, by two graders on different models; the report gives hits per arm
for each grader, their agreement, a two-sided Fisher exact p and the
cost. No live store is touched. The harness's `--restricted` flag is
not used because it switches auto memory off; the cost of that choice
is that the file tools are not fenced to the scratch project, and that
the person's own user-level instruction files load into the subject
sessions as in any session.

Run it from the repository root:

```
uv run python evals/run_memory_ablation.py --dry-run
uv run python evals/run_memory_ablation.py --n 5 --seed 20260927 --work-dir <scratch>
```

Options: `--n` sessions per arm per case (default 5), `--case` to run
one case, `--model` and `--effort` for the subject sessions (default
`opus` at `high`), `--graders` (default `haiku,sonnet`), `--budget-usd`
per session, `--work-dir` to keep the arms, answers, key and grades; a
run in an existing work dir resumes after an interruption. `--fixture`
points the run at another copy of the fixture, such as a scratch copy
whose store an upkeep pass has maintained, and `--install-skill` puts
`memory-keeper` into every arm's scratch project through the product's
own installer (profile folders pointed at scratch, bytes checked against
the package); the subject sessions keep their read-only tools with no
Skill tool, so the skill sits in the project as a file, and whether a
session reads it is not traced. The
deterministic half (arm building, the Fisher test, the fixture's
self-containment) is covered by
`src/radialrig/tests/test_memory_ablation_fixture.py`; the model-driven
run is by hand and its result goes in a dated
`memory-ablation-run-<date>.md` file here, with the graded answers in
a companion `-answers.md` file. This project's own run records are not
published here. At the default size (two cases, n=5, sessions one at a
time) the baseline of 2026-09-27 cost $1.98 and took about twelve
minutes; that run is the baseline the skill cites. The re-run of
2026-09-28, with the skill installed and the store maintained by one
upkeep pass (PLAN-0020 phase 4 prescription 3), reproduced every count
of the baseline for $2.04 in about ten minutes.

## The memory writer

`run_memory_writer.py` measures the skill's own work, which the
ablation does not: whether `memory-keeper` loads when a session writes
to its memory store, and whether what the session writes is the kind of
line its source allows, with the date, the scope and the reason. It was
built under PLAN-0020 phase 4. It runs Claude Code only (Codex is not
measured), says nothing about long sessions or the upkeep pass, and
rests on one small fixture and three prompts.

How it works: two arms copy the Tidepool project from `memory-ablation/`
and the plain one-entry store under `memory-writer/store/`. The
with-skill arm gets the skill through the product's own installer, with
the per-user profile folders pointed at scratch so the install writes
no real activity log or project list, and its bytes are checked against
the package. Every session runs on its own fresh copy of its arm, so no
session sees another's writes; the tools are Read, Grep, Glob, Write,
Edit and Skill, the scratch store is added as a working directory, MCP
servers are off and the trace comes back as stream-json. The runner
reads the trace for a call to the skill (or a read of its `SKILL.md`),
diffs the scratch store against the fixture store for what was written,
and lists any project file that changed. Three cases: the person asks
the agent to remember a decision; the person pastes a log and asks a
question, so any memory is the agent's own idea; and the person ends a
work session and asks the agent to save what the next session needs,
which should keep the task state out of memory and save the one dead
end. Two graders on different models answer each case's yes-or-no
questions per session, blind, against `memory-writer/rubric.md`, written
before the case's first run; a hit needs every yes, and a session that
wrote nothing is a miss.
A canary pair runs first and the run stops unless both arms wrote the
canary into their own scratch store, the skill is listed in one arm
only, and no memory directory appeared at the harness's default
location. The report gives per arm the loads, the writes, hits and
yeses per question for each grader, their agreement, a two-sided Fisher
exact p, the cost, any change to a live memory directory during the
run, and the release gate the rubric states: the skill loads in at
least half its arm's sessions, and that arm's hits exceed the other's
for both graders, or the release waits for the owner's word.

Run it from the repository root, with a work dir outside the
repository (the runner refuses one inside it):

```
uv run python evals/run_memory_writer.py --dry-run
uv run python evals/run_memory_writer.py --n 5 --seed 20260928 --work-dir <scratch>
```

The options match the ablation's, except that the graders default to
`sonnet,opus`: on the end-of-session case `haiku` answered against text
that plainly stated the answer, and the rubric replaced it. The
deterministic half (arm building,
the scratch-profile install, fresh copies, the trace and store readers,
grade parsing, the gate, a stubbed run and its resume) is covered by
`src/radialrig/tests/test_memory_writer_fixture.py`; the model-driven
run is by hand and its result goes in a dated
`memory-writer-run-<date>.md` file here, with what each session wrote
in a companion `-materials.md` file; as with the ablation, this
project's own records stay unpublished. The first run, on 2026-09-28,
had the two cases there were then, n=5, one session at a time. It cost
$4.20 with its canary and took about eight minutes. The end-of-session
case came later; alone, at n=5, it cost $3.06 with its canary and took
about nine minutes. The default now runs all three cases.
