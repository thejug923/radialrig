"""The memory-ablation fixture is self-contained and the runner builds its arms.

Run:  uv run --with pytest pytest src/radialrig/tests/test_memory_ablation_fixture.py -q

PLAN-0020 phase 1 ships a fixture store for the memory ablation under
`evals/memory-ablation/`. The fixture must name no outside repository,
private path or person, and must not be a copy of a live store. These
tests hold that line mechanically and check the deterministic half of
the runner: that removing one entry touches its index line and its
detail file and nothing else. The model-driven half runs by hand and
records its result in a dated file beside the runner.
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
FIXTURE = EVALS / "memory-ablation"
RUNNER = EVALS / "run_memory_ablation.py"

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
    spec = importlib.util.spec_from_file_location("run_memory_ablation", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # dataclasses resolve deferred annotations here
    spec.loader.exec_module(module)
    return module


def _fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURE.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def test_fixture_names_nothing_outside_itself():
    hits: list[str] = []
    for path in _fixture_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name, pattern in FORBIDDEN.items():
                if pattern.search(line):
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {name}")
    assert not hits, "\n".join(hits)


def test_every_case_points_at_one_index_line_and_an_existing_file():
    spec = json.loads((FIXTURE / "cases.json").read_text(encoding="utf-8"))
    index_lines = (FIXTURE / "fixture" / "memory" / "MEMORY.md").read_text(encoding="utf-8").splitlines()
    entries = [spec["canary"], *spec["cases"]]
    kinds = {c.get("kind") for c in spec["cases"]}
    assert {"owner decision", "agent lesson"} <= kinds, "phase 1 asks for both kinds of line"
    for entry in entries:
        matches = [line for line in index_lines if line.startswith(entry["index_line_prefix"])]
        assert len(matches) == 1, entry["index_line_prefix"]
        assert (FIXTURE / "fixture" / "memory" / entry["detail_file"]).is_file(), entry["detail_file"]
        assert entry["prompt"].strip() and entry["rubric"].strip()


def test_absent_arm_lacks_exactly_the_entry(tmp_path: Path):
    runner = _runner()
    spec = json.loads((FIXTURE / "cases.json").read_text(encoding="utf-8"))
    case = spec["cases"][0]
    arms = runner.build_arms(FIXTURE, tmp_path, case, case["id"])
    present, absent = arms["present"], arms["absent"]

    fixture_files = {p.relative_to(FIXTURE / "fixture" / "memory") for p in (FIXTURE / "fixture" / "memory").rglob("*") if p.is_file()}
    present_files = {p.relative_to(present.memory) for p in present.memory.rglob("*") if p.is_file()}
    absent_files = {p.relative_to(absent.memory) for p in absent.memory.rglob("*") if p.is_file()}
    assert present_files == fixture_files
    assert absent_files == fixture_files - {Path(case["detail_file"])}

    present_index = (present.memory / "MEMORY.md").read_text(encoding="utf-8").splitlines()
    absent_index = (absent.memory / "MEMORY.md").read_text(encoding="utf-8").splitlines()
    removed = [line for line in present_index if line not in absent_index]
    assert len(removed) == 1 and removed[0].startswith(case["index_line_prefix"])
    assert [line for line in present_index if line in absent_index] == absent_index

    for name in ("present", "absent"):
        settings = json.loads(arms[name].settings.read_text(encoding="utf-8"))
        assert Path(settings["autoMemoryDirectory"]) == arms[name].memory.resolve()
    # The project copy is untouched in both arms.
    for rel in ("CLAUDE.md", "README.md", "src/tidepool/deploy.py"):
        assert (present.project / rel).read_bytes() == (absent.project / rel).read_bytes()


def test_removing_an_unknown_entry_refuses(tmp_path: Path):
    runner = _runner()
    with pytest.raises(SystemExit):
        runner.build_arms(FIXTURE, tmp_path, {"index_line_prefix": "- [Nothing]", "detail_file": "canary.md"}, "x")


def test_install_skill_puts_the_package_in_both_arms(tmp_path: Path):
    """PLAN-0020 phase 4 prescription 3: the re-run with the skill installed."""
    runner = _runner()
    spec = json.loads((FIXTURE / "cases.json").read_text(encoding="utf-8"))
    case = spec["cases"][0]
    work = tmp_path / "work"
    arms = runner.build_arms(FIXTURE, work, case, case["id"], install=True)

    package = runner.tree_digests(runner.SKILL_SOURCE)
    assert "SKILL.md" in package
    for arm in arms.values():
        assert runner.tree_digests(arm.project / ".claude" / "skills" / runner.SKILL_ID) == package
    # The arms still differ only by the entry: same project outside the skill, one file fewer in the store.
    outside = {
        name: {rel: d for rel, d in runner.tree_digests(arm.project).items() if not rel.startswith(".claude/")}
        for name, arm in arms.items()
    }
    assert outside["present"] == outside["absent"] == runner.tree_digests(FIXTURE / "fixture" / "project")
    assert not (arms["absent"].memory / case["detail_file"]).exists()
    # The install ran against a scratch profile: its activity-log line landed there, not in the real one.
    assert list((work / "profile").rglob("radialrig.log"))

    # Without the flag no skill is installed, as the baseline ran.
    plain = runner.build_arms(FIXTURE, tmp_path / "plain", case, case["id"])
    assert not (plain["present"].project / ".claude").exists()


def test_fisher_exact_behaves():
    runner = _runner()
    assert runner.fisher_two_sided(0, 5, 5, 0) < 0.01
    assert runner.fisher_two_sided(5, 0, 0, 5) < 0.01
    assert abs(runner.fisher_two_sided(2, 3, 2, 3) - 1.0) < 1e-9
    assert runner.fisher_two_sided(0, 0, 0, 0) == 1.0


def _stubbed_runner(monkeypatch: pytest.MonkeyPatch, answers: dict[str, str]):
    """The runner with the model calls replaced: the answer names the arm's memory content."""
    runner = _runner()

    def fake_subject(prompt: str, arm, opts):
        text = answers["present" if (arm.memory / "digest-email-killed.md").exists() else "absent"]
        return {"result": text, "total_cost_usd": 0.01, "duration_ms": 1, "num_turns": 1, "is_error": False}

    def fake_grade(answer: str, rubric: str, model: str, grader_dir: Path, timeout: int):
        return (1 if "email" in answer else 0), 0.001

    monkeypatch.setattr(runner, "run_subject", fake_subject)
    monkeypatch.setattr(runner, "grade", fake_grade)
    return runner


def test_resume_keeps_labels_and_refuses_a_changed_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = _stubbed_runner(monkeypatch, {"present": "use the status page", "absent": "send an email"})
    work = tmp_path / "work"
    args = ["--work-dir", str(work), "--n", "2", "--case", "owner-decision", "--skip-canary", "--seed", "7"]
    assert runner.main(args) == 0
    key = json.loads((work / "key.json").read_text(encoding="utf-8"))
    first = json.loads((work / "results.json").read_text(encoding="utf-8"))
    arms = first["cases"]["owner-decision"]["arms"]
    assert arms["present"]["hits"] == {"haiku": 0, "sonnet": 0}
    assert arms["absent"]["hits"] == {"haiku": 2, "sonnet": 2}

    # Same plan again: every session is reloaded, nothing re-runs, the labels hold.
    monkeypatch.setattr(runner, "run_subject", lambda *a, **k: pytest.fail("a resume re-ran a saved session"))
    assert runner.main(args) == 0
    second = json.loads((work / "results.json").read_text(encoding="utf-8"))
    assert second["cases"] == first["cases"]
    assert json.loads((work / "key.json").read_text(encoding="utf-8")) == key

    # A different seed reorders the plan; the saved labels no longer line up and the run refuses.
    with pytest.raises(SystemExit, match="resume mismatch"):
        runner.main([*args[:-1], "8"])


def test_an_errored_session_is_set_aside_and_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = _stubbed_runner(monkeypatch, {"present": "use the status page", "absent": "send an email"})
    calls = {"n": 0}
    good = runner.run_subject

    def flaky(prompt, arm, opts):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"result": "", "is_error": True, "total_cost_usd": 0.0}
        return good(prompt, arm, opts)

    monkeypatch.setattr(runner, "run_subject", flaky)
    work = tmp_path / "work"
    args = ["--work-dir", str(work), "--n", "1", "--case", "agent-lesson", "--skip-canary", "--seed", "3"]
    assert runner.main(args) == 0
    results = json.loads((work / "results.json").read_text(encoding="utf-8"))
    arms = results["cases"]["agent-lesson"]["arms"]
    assert sum(a["errors"] for a in arms.values()) == 1
    assert sum(a["n"] for a in arms.values()) == 1
    assert len(list((work / "sessions").glob("*.error.json"))) == 1
    # The resume runs the failed session again and the arm fills in.
    assert runner.main(args) == 0
    results = json.loads((work / "results.json").read_text(encoding="utf-8"))
    arms = results["cases"]["agent-lesson"]["arms"]
    assert sum(a["n"] for a in arms.values()) == 2
    assert sum(a["errors"] for a in arms.values()) == 0
