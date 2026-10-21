"""The memory-writer fixture is self-contained and the runner's deterministic half holds.

Run:  uv run --with pytest pytest src/radialrig/tests/test_memory_writer_fixture.py -q

PLAN-0020 phase 4 measures the skill's own work: whether `memory-keeper`
loads when a session writes a memory, and what it writes. The runner
under `evals/` installs the skill in one arm through the product's own
installer, gives every session a fresh scratch copy, reads the
session's trace and diffs its store. These tests hold the fixture to the
same line as the phase 1 fixture and check everything that needs no
model call; the model-driven run is by hand and records its result in a
dated file beside the runner.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
EVALS = REPO_ROOT / "evals"
FIXTURE = EVALS / "memory-writer"
RUNNER = EVALS / "run_memory_writer.py"

pytestmark = pytest.mark.skipif(
    not FIXTURE.is_dir() or not RUNNER.is_file(),
    reason="the evals directory is not present in this checkout",
)

# Shapes the fixture may not carry. Each is named for the failure report.
# The owner's handles and this machine's memory key are swept by a module
# that stays in the private workshop, since spelling them here would
# publish them (PLAN-0023 phase 1, prescription 3).
FORBIDDEN: dict[str, re.Pattern[str]] = {
    "drive path": re.compile(r"\b[A-Za-z]:[\\/]"),
    "home path": re.compile(r"/(?:Users|home)/"),
    "URL": re.compile(r"https?://", re.IGNORECASE),
    "this product's name": re.compile(r"radialrig", re.IGNORECASE),
}


def _runner():
    spec = importlib.util.spec_from_file_location("run_memory_writer", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # dataclasses resolve deferred annotations here
    spec.loader.exec_module(module)
    return module


def _spec() -> dict:
    return json.loads((FIXTURE / "cases.json").read_text(encoding="utf-8"))


def test_fixture_names_nothing_outside_itself():
    hits: list[str] = []
    for path in sorted(p for p in FIXTURE.rglob("*") if p.is_file()):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for name, pattern in FORBIDDEN.items():
                if pattern.search(line):
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {name}")
    assert not hits, "\n".join(hits)


def test_cases_ask_the_four_questions_and_the_unprompted_case_asks_for_no_memory():
    runner = _runner()
    spec = _spec()
    assert spec["canary"]["token"] in spec["canary"]["prompt"]
    ids = [c["id"] for c in spec["cases"]]
    assert ids == ["asked-decision", "unprompted-observation", "end-of-session"]
    for case in spec["cases"]:
        assert case["criteria"] and all(text.strip() for text in case["criteria"].values())
    asked, unprompted, end = spec["cases"]
    assert tuple(asked["criteria"]) == tuple(unprompted["criteria"]) == runner.CRITERIA
    assert tuple(end["criteria"]) == ("no_task_state", "dead_end", "date")
    assert "remember" in asked["prompt"].lower()
    assert not re.search(r"remember|memory|note|save|keep", unprompted["prompt"], re.IGNORECASE)
    # The end-of-session prompt leaves the choice of home to the agent.
    assert not re.search(r"memory|remember", end["prompt"], re.IGNORECASE)


def test_arms_differ_only_by_the_installed_skill(tmp_path: Path):
    runner = _runner()
    work = tmp_path / "work"
    arms = runner.build_arms(FIXTURE, runner.PROJECT_DIR, work)
    with_arm, without_arm = arms["with"], arms["without"]

    package = runner.tree_digests(runner.SKILL_SOURCE)
    assert "SKILL.md" in package
    assert runner.tree_digests(with_arm.project / ".claude" / "skills" / "memory-keeper") == package
    assert not (without_arm.project / ".claude").exists()

    store = runner.tree_digests(FIXTURE / "store")
    for arm in arms.values():
        assert runner.tree_digests(arm.memory) == store
        settings = json.loads(arm.settings.read_text(encoding="utf-8"))
        assert Path(settings["autoMemoryDirectory"]) == arm.memory.resolve()
    outside = {rel: d for rel, d in runner.tree_digests(with_arm.project).items() if not rel.startswith(".claude/")}
    assert outside == runner.tree_digests(without_arm.project)

    # The install ran against a scratch profile: its activity-log line landed there.
    assert list((work / "profile").rglob("radialrig.log"))


def test_a_fresh_copy_sees_no_other_session_s_writes(tmp_path: Path):
    runner = _runner()
    arms = runner.build_arms(FIXTURE, runner.PROJECT_DIR, tmp_path / "work")
    first = runner.fresh_copy(arms["with"], tmp_path / "runs" / "a")
    (first.memory / "note.md").write_text("written by session a", encoding="utf-8")
    second = runner.fresh_copy(arms["with"], tmp_path / "runs" / "b")
    assert not (second.memory / "note.md").exists()
    assert not (arms["with"].memory / "note.md").exists()
    settings = json.loads(second.settings.read_text(encoding="utf-8"))
    assert Path(settings["autoMemoryDirectory"]) == second.memory.resolve()
    assert (second.project / ".claude" / "skills" / "memory-keeper" / "SKILL.md").is_file()


def test_work_dir_inside_the_repository_refuses():
    runner = _runner()
    with pytest.raises(SystemExit, match="outside this repository"):
        runner.main(["--work-dir", str(REPO_ROOT / "evals" / "scratch"), "--dry-run"])


def _event(**fields) -> str:
    return json.dumps(fields)


def test_the_trace_shows_the_skill_listed_and_loaded():
    runner = _runner()
    stream = "\n".join([
        _event(type="system", subtype="init", tools=["Read", "Skill"], skills=["memory-keeper", "other"]),
        _event(type="assistant", message={"content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": "memory-keeper"}},
        ]}),
        _event(type="assistant", message={"content": [
            {"type": "text", "text": "saving"},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "m/x.md", "content": "c"}},
        ]}),
        "not json",
        _event(type="result", subtype="success", result="done", is_error=False,
               total_cost_usd=0.12, duration_ms=900, num_turns=3),
    ])
    data = runner.parse_stream(stream)
    assert data["listed"] is True
    assert data["result"] == "done" and data["num_turns"] == 3 and data["total_cost_usd"] == 0.12
    assert [c["name"] for c in data["tool_calls"]] == ["Skill", "Write"]
    assert runner.skill_loaded(data["tool_calls"])
    assert runner.other_skills(data["tool_calls"]) == []


def test_the_trace_without_the_skill_or_a_result():
    runner = _runner()
    data = runner.parse_stream(_event(type="system", subtype="init", slash_commands=["/compact", "plugin:tidy"]))
    assert data["listed"] is False
    assert data["is_error"] is True
    assert runner.parse_stream("")["listed"] is None
    calls = [{"name": "Skill", "input": {"skill": "anthropic-skills:consolidate-memory"}}]
    assert not runner.skill_loaded(calls)
    assert runner.other_skills(calls) == ["consolidate-memory"]
    read = [{"name": "Read", "input": {"file_path": "C:\\p\\.claude\\skills\\memory-keeper\\SKILL.md"}}]
    assert runner.skill_loaded(read)


def test_store_changes_and_the_graders_material(tmp_path: Path):
    runner = _runner()
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    (before / "MEMORY.md").write_text("# Memory index\n\n- [A](a.md) - a\n", encoding="utf-8")
    (before / "a.md").write_text("a\n", encoding="utf-8")
    after.mkdir()
    (after / "MEMORY.md").write_text("# Memory index\n\n- [A](a.md) - a\n- [B](b.md) - b\n", encoding="utf-8")
    (after / "a.md").write_text("a\n", encoding="utf-8")
    (after / "b.md").write_text("Decided (owner, 2026-06-02): no dark mode\n", encoding="utf-8")

    changes = runner.store_changes(before, after)
    assert runner.wrote(changes)
    assert set(changes["new"]) == {"b.md"} and changes["changed"] == ["MEMORY.md"]
    text = runner.material(changes)
    assert "FILE b.md (new):\nDecided (owner, 2026-06-02): no dark mode" in text
    assert "FILE MEMORY.md (lines added):\n- [B](b.md) - b" in text
    assert "- [A](a.md)" not in text
    assert not runner.wrote(runner.store_changes(before, before))


def test_the_grader_never_sees_the_harness_s_own_stamps(tmp_path: Path):
    runner = _runner()
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    (before / "a.md").write_text("---\nname: a\nmetadata:\n  modified: 2026-01-01T00:00:00Z\n---\nbody\n", encoding="utf-8")
    after.mkdir()
    (after / "a.md").write_text("---\nname: a\nmetadata:\n  modified: 2026-09-28T23:46:58.592Z\n---\nbody\n", encoding="utf-8")
    (after / "b.md").write_text(
        "---\nname: b\nmetadata:\n  node_type: memory\n  type: project\n"
        "  originSessionId: 5c9234d7\n  modified: 2026-09-28T23:46:58.592Z\n---\n\nno dark mode\n",
        encoding="utf-8",
    )
    text = runner.material(runner.store_changes(before, after))
    assert "type: project" in text and "no dark mode" in text
    assert not re.search(r"node_type|originSessionId|modified|2026-09-28", text)
    assert "FILE a.md (lines added):\n(none)" in text


def test_grades_parse_and_a_hit_needs_all_four():
    runner = _runner()
    good = runner.parse_grade('Here: {"kind": 1, "date": 1, "scope": 1, "reason": 1}')
    assert runner.hit(good) == 1
    assert runner.hit({**good, "date": 0}) == 0
    assert runner.parse_grade("no json") == {name: None for name in runner.CRITERIA}
    assert runner.hit(runner.parse_grade('{"kind": 1}')) is None
    assert runner.hit(None) is None
    # A case with its own questions: the prompt asks for them and the parse reads them.
    names = ("no_task_state", "dead_end", "date")
    assert '{"no_task_state": 0, "dead_end": 0, "date": 0}' in runner.grader_prefix(names)
    three = runner.parse_grade('{"no_task_state": 1, "dead_end": 1, "date": 0}', names)
    assert three == {"no_task_state": 1, "dead_end": 1, "date": 0} and runner.hit(three) == 0


def _rows(loaded: int, n: int, hits_with: dict, hits_without: dict) -> dict:
    return {"case": {"arms": {
        "with": {"n": n, "loaded": loaded, "hits": hits_with},
        "without": {"n": n, "loaded": 0, "hits": hits_without},
    }}}


def test_the_release_gate():
    runner = _runner()
    g = ["haiku", "sonnet"]
    ok = runner.release_gate(_rows(3, 5, {"haiku": 4, "sonnet": 3}, {"haiku": 1, "sonnet": 1}), g)
    assert ok["loads_ok"] and ok["better"] and "go ahead" in ok["verdict"]
    few = runner.release_gate(_rows(2, 5, {"haiku": 4, "sonnet": 3}, {"haiku": 1, "sonnet": 1}), g)
    assert not few["loads_ok"] and "waits" in few["verdict"]
    tie = runner.release_gate(_rows(5, 5, {"haiku": 4, "sonnet": 2}, {"haiku": 1, "sonnet": 2}), g)
    assert not tie["better"] and "waits" in tie["verdict"]


def _stubbed_runner(monkeypatch: pytest.MonkeyPatch):
    """The runner with the model calls replaced: the with-skill arm writes a labelled line."""
    runner = _runner()

    def fake_session(prompt: str, arm, opts):
        has_skill = (arm.project / ".claude" / "skills" / "memory-keeper").is_dir()
        if "PERIWINKLE-42" in prompt:
            line = "canary word PERIWINKLE-42"
        elif has_skill:
            line = "Decided (owner, 2026-09-28): no dark mode; scope: status page; reason: printed"
        else:
            line = "no dark mode"
        (arm.memory / "entry.md").write_text(line + "\n", encoding="utf-8")
        calls = [{"name": "Skill", "input": {"skill": "memory-keeper"}}] if has_skill else []
        return {
            "result": "done", "is_error": False, "total_cost_usd": 0.01, "num_turns": 2,
            "tool_calls": calls, "listed": has_skill,
        }

    def fake_grade(text: str, criteria, model: str, grader_dir: Path, timeout: int):
        yes = int("Decided" in text)
        return {name: yes for name in criteria}, 0.001

    monkeypatch.setattr(runner, "run_session", fake_session)
    monkeypatch.setattr(runner, "grade", fake_grade)
    return runner


def test_a_stubbed_run_reports_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = _stubbed_runner(monkeypatch)
    work = tmp_path / "work"
    args = ["--work-dir", str(work), "--n", "2", "--seed", "5"]
    assert runner.main(args) == 0
    first = json.loads((work / "results.json").read_text(encoding="utf-8"))
    for case in first["cases"].values():
        assert case["arms"]["with"]["loaded"] == 2 and case["arms"]["without"]["loaded"] == 0
        assert case["arms"]["with"]["wrote"] == 2
        assert case["arms"]["with"]["hits"] == {"sonnet": 2, "opus": 2}
        assert case["arms"]["without"]["hits"] == {"sonnet": 0, "opus": 0}
        assert case["grader_agreement"] == "4/4"
    assert "go ahead" in first["gate"]["verdict"]
    assert first["default_store_created"] == 0
    assert len(list((work / "materials").glob("*.md"))) == 12
    assert first["graders"] == ["sonnet", "opus"]
    assert set(first["cases"]["end-of-session"]["arms"]["with"]["criteria"]["opus"]) == {
        "no_task_state", "dead_end", "date",
    }

    # Same plan again: every session is reloaded, nothing re-runs or re-grades, the labels hold.
    key = json.loads((work / "key.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(runner, "run_session", lambda *a, **k: pytest.fail("a resume re-ran a saved session"))
    monkeypatch.setattr(runner, "grade", lambda *a, **k: pytest.fail("a resume re-graded a saved grade"))
    assert runner.main(args) == 0
    second = json.loads((work / "results.json").read_text(encoding="utf-8"))
    assert second["cases"] == first["cases"]
    assert json.loads((work / "key.json").read_text(encoding="utf-8")) == key

    with pytest.raises(SystemExit, match="resume mismatch"):
        runner.main([*args[:-1], "6"])


def test_the_canary_stops_a_run_whose_writes_miss_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = _stubbed_runner(monkeypatch)

    def writes_nothing(prompt: str, arm, opts):
        has_skill = (arm.project / ".claude" / "skills" / "memory-keeper").is_dir()
        return {"result": "done", "is_error": False, "total_cost_usd": 0.01, "tool_calls": [], "listed": has_skill}

    monkeypatch.setattr(runner, "run_session", writes_nothing)
    assert runner.main(["--work-dir", str(tmp_path / "work"), "--n", "1"]) == 2
    assert not (tmp_path / "work" / "key.json").exists()
