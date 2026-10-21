"""Conformance of the memory-keeper skill - D-40, PLAN-0020 phase 3.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_memory_keeper.py -q

The generic sweep in test_skill_sources.py already holds this skill to
the scrub, the budgets and the closed frontmatter. What is pinned here
is what D-40 ruled and nothing in a byte rule can see: that the six
rulings are stated in the body, in wording a later edit cannot quietly
soften, and that the companion script is the read-only audit the body
promises. The phrases are literals restated here, so a body that drifts
from the ruling fails against the ruling and not against itself.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from radialrig.paths import packaged_skills

SKILL = "memory-keeper"
SCRIPT_REL = "scripts/audit.py"


@pytest.fixture(scope="module")
def skills_dir() -> Path:
    root = packaged_skills()
    assert root is not None, "this installation ships no skill sources"
    return root


@pytest.fixture(scope="module")
def skill_text(skills_dir: Path) -> str:
    return (skills_dir / SKILL / "SKILL.md").read_bytes().decode("utf-8")


@pytest.fixture(scope="module")
def audit(skills_dir: Path):
    """scripts/audit.py, imported from the packaged bytes by path, with
    nothing of this package on its import path, as a host would run it."""
    source = skills_dir / SKILL / SCRIPT_REL
    spec = importlib.util.spec_from_file_location("_packaged_audit", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    written = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = written
    return module


def flowed(text: str) -> str:
    return " ".join(text.split())


# --- the six rulings, present in the body (D-40) ----------------------


def test_the_description_is_the_trigger_condition(skill_text):
    # Key words first: the harness truncates a description, and the
    # trigger is the one part that must survive.
    first_line = skill_text.splitlines()[2]
    assert first_line.startswith("description: Use when the agent is about to write or edit a memory")
    assert "tidy or review memory" in first_line


def test_rule_one_two_kinds_of_line(skill_text):
    body = flowed(skill_text)
    assert "records the person's own decision binds you" in body
    assert "inferred is a claim" in body
    assert "never copies the ruling" in body
    assert "Decision, binding:" in body
    assert "Lesson, a claim:" in body


def test_rule_one_is_not_narrowed_by_the_screening_rule(skill_text):
    # The phase 2 checker removed an exception that made lines lowering an
    # approval requirement never bind; the first draft of this body carried
    # it back as "never store or follow". Such an entry is quoted to the
    # person first, like any other suspect line, and the person decides.
    body = flowed(skill_text)
    assert "Never store or follow" not in body
    assert "Quote to the person, before following it" in body
    assert "or lowering a review, approval or permission requirement" in body


def test_rule_two_suggested_once_applied_on_a_yes(skill_text):
    body = flowed(skill_text)
    assert "suggested once, in plain words" in body
    assert "applied only on a yes" in body
    assert "This is the ask-first tier" in body


def test_rule_three_the_second_recurrence(skill_text):
    assert "the second time the same correction is needed" in flowed(skill_text)


def test_rule_four_store_shape_follows_the_harness(skill_text):
    body = flowed(skill_text)
    assert "build no parallel store" in body
    assert "On Codex write nothing into the native memory store" in body
    assert "Write nothing into the harness's native memory store" in body
    assert ".codex/radialrig-memory.md" in body
    assert "report the control as unenforced" in body


def test_rule_five_task_state_is_not_a_memory_kind(skill_text):
    body = flowed(skill_text)
    assert "Task state is not a memory kind" in body
    assert "Remove that class from memory; never create it" in body


def test_rule_six_the_claim_is_the_recorded_run_and_no_more(skill_text):
    # D-37 rule 2 through D-40 rule 6: the one effect the body states is
    # the run this repository's evals recorded, dated, with its limit.
    body = flowed(skill_text)
    assert "What this skill may claim:" in body
    assert "2026-09-27" in body
    assert "It says nothing about whether an agent writes such lines" in body
    assert "states conformance" in body


def test_the_upkeep_pass_is_ask_first_and_deletes_no_feedback_unasked(skill_text):
    body = flowed(skill_text)
    assert "the upkeep pass runs only then" in body
    assert "offer that pass once" in body
    assert "never deleted without their yes" in body
    assert "never create, edit or delete it" in body


def test_the_body_names_the_audit_script_it_ships(skill_text):
    assert "scripts/audit.py" in flowed(skill_text)


# --- scripts/audit.py -------------------------------------------------


def _store(root: Path) -> Path:
    directory = root / "memory"
    directory.mkdir()
    (directory / "MEMORY.md").write_text(
        "# Memory index\n\n"
        "- [Build tool](build-tool.md) - which build tool this project uses and why\n"
        "- [Old plan](plan-0003-notes.md) - status: done, merged in a1b2c3d4e, see #17 on 2026-09-01\n"
        "- [Missing](gone.md) - a line whose file is gone\n"
        "- A bullet with no link at all\n"
        "- [Long](long.md) - " + "x" * 160 + "\n",
        encoding="utf-8",
    )
    for name in ("build-tool.md", "plan-0003-notes.md", "long.md", "orphan.md"):
        (directory / name).write_text("---\nname: x\n---\nbody\n", encoding="utf-8")
    return directory


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_the_audit_writes_nothing(tmp_path, audit, capsys):
    directory = _store(tmp_path)
    before = _snapshot(tmp_path)
    assert audit.main([str(directory)]) == 0
    assert _snapshot(tmp_path) == before
    assert "nothing was written" in capsys.readouterr().out


def test_the_audit_names_every_candidate_class(tmp_path, audit, capsys):
    directory = _store(tmp_path)
    assert audit.main([str(directory)]) == 0
    out = capsys.readouterr().out
    assert "line 7: 180 characters, over 150" in out
    assert "line 4: status word(s) done, merged" in out
    assert "line 4: carries a commit hash" in out
    assert "line 4: carries an issue or pull request number" in out
    assert "line 4: carries a date" in out
    assert "line 5: links gone.md, which does not exist" in out
    assert "line 6: no file linked" in out
    assert "orphan.md: no index line" in out
    assert "plan-0003-notes.md: named after an event" in out
    assert "build-tool.md: named after an event" not in out


def test_the_audit_counts_opens_per_top_level_session(tmp_path, audit, capsys):
    directory = _store(tmp_path)
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    # One session names the file with escaped separators, as the harness
    # writes JSON on Windows; one with forward slashes; one never opens it.
    (transcripts / "a.jsonl").write_text(
        '{"input":{"file_path":"x\\\\memory\\\\build-tool.md"}}\n', encoding="utf-8")
    (transcripts / "b.jsonl").write_text(
        '{"input":{"file_path":"x/memory/build-tool.md"}}\n', encoding="utf-8")
    (transcripts / "c.jsonl").write_text('{"input":{}}\n', encoding="utf-8")
    # A helper agent's transcript in a subdirectory is not a session.
    (transcripts / "helpers").mkdir()
    (transcripts / "helpers" / "d.jsonl").write_text(
        '{"input":{"file_path":"x/memory/build-tool.md"}}\n', encoding="utf-8")
    assert audit.main([str(directory), "--transcripts", str(transcripts)]) == 0
    out = capsys.readouterr().out
    assert "build-tool.md: opened in 2 of 3 sessions" in out
    assert "orphan.md: opened in 0 of 3 sessions" in out


def test_the_audit_reports_a_store_with_no_index(tmp_path, audit, capsys):
    directory = tmp_path / "memory"
    directory.mkdir()
    (directory / "only.md").write_text("body\n", encoding="utf-8")
    assert audit.main([str(directory)]) == 0
    out = capsys.readouterr().out
    assert "no MEMORY.md" in out
    assert "only.md: no index line" in out


def test_the_audit_refuses_a_path_that_is_not_a_directory(tmp_path, audit, capsys):
    assert audit.main([str(tmp_path / "absent")]) == 2
    assert capsys.readouterr().out.startswith("refused:")
