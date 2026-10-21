"""Measure whether the memory skill loads when a session writes a memory, and what it writes.

The phase 1 ablation (`run_memory_ablation.py`) shows that a line already
in a store changes a fresh session's answer. This runner measures the
writing side: whether `memory-keeper` loads when a session writes to its
store, whether what it writes is the kind of line its source allows,
with the date, the scope and the reason, and whether task state stays
out of the store at the end of a work session (PLAN-0020 phase 4,
prescription 4). Each case states its own yes-or-no questions.

Two arms: the fixture project with the skill installed through the
product's own installer, and the same project without it. Every session
runs on its own fresh scratch copy of project and store, so no session
sees another's writes. The runner reads each session's stream-json trace
for a call to the skill, diffs the scratch store against the fixture
store for what was written, and has two graders on different models
score the written material blind against the rubric written before the
runs. It reports per arm how often the skill loaded, how often the
session wrote, hits per grader and per question, grader agreement, a
two-sided Fisher exact p, the cost, and the release gate the rubric
states.

    uv run python evals/run_memory_writer.py --dry-run
    uv run python evals/run_memory_writer.py --n 5 --work-dir <scratch>

It runs Claude Code only; Codex is not measured. The session's working
directories are the scratch project and, through `--add-dir`, the
scratch store; after the run the runner reports any change to a live
memory directory. The person's own user-level instruction files, skills
and plugins load as in any session, in both arms. The harness's
`--restricted` flag is not used: it switches auto memory off. The
directory is ungoverned evidence and not part of the pytest suite.
"""

from __future__ import annotations

import argparse
import difflib
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
FIXTURE_DIR = EVALS_DIR / "memory-writer"
PROJECT_DIR = EVALS_DIR / "memory-ablation" / "fixture" / "project"
SUBJECT_TOOLS = "Read,Grep,Glob,Write,Edit,Skill"
CRITERIA = ("kind", "date", "scope", "reason")
ARMS = ("with", "without")
GRADER_SETTINGS = json.dumps({"autoMemoryEnabled": False})


def grader_prefix(names: tuple[str, ...]) -> str:
    shape = "{" + ", ".join(f'"{name}": 0' for name in names) + "}"
    return (
        "You are grading what an AI coding assistant wrote into a project's memory store. "
        "Judge all the written material below together as one entry. Answer each rubric "
        "question with 1 or 0 and output ONLY a JSON object on one line, no prose, no code "
        f"fence: {shape} with your answers in place of the zeros.\n\nRUBRIC:\n"
    )

# Fields Claude Code stamps into a memory file's frontmatter itself (seen in
# the canary of 2026-09-28: `modified` is rewritten on every write). Neither
# arm writes them, so a grader never sees them.
HARNESS_STAMPS = re.compile(r"^\s*(?:node_type|originSessionId|modified):")

sys.path.insert(0, str(EVALS_DIR))
from run_memory_ablation import (  # noqa: E402
    SKILL_ID,
    SKILL_SOURCE,
    claude_command,
    fisher_two_sided,
    install_skill,
    run_print,
    scratch_profile,
    tree_digests,
)


@dataclass
class Arm:
    name: str
    root: Path
    project: Path
    memory: Path
    settings: Path


def load_cases(fixture_dir: Path) -> dict:
    return json.loads((fixture_dir / "cases.json").read_text(encoding="utf-8"))


def tree_texts(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def arm_at(root: Path, name: str) -> Arm:
    return Arm(name, root, root / "project", root / "memory", root / "settings.json")


def write_settings(arm: Arm) -> None:
    arm.settings.write_text(
        json.dumps({"autoMemoryDirectory": arm.memory.resolve().as_posix()}),
        encoding="utf-8",
    )


def build_arms(fixture_dir: Path, project_dir: Path, work_dir: Path) -> dict[str, Arm]:
    """Two template copies of project and store; the skill is installed in one."""
    arms: dict[str, Arm] = {}
    for name in ARMS:
        root = work_dir / "arms" / name
        if root.exists():
            shutil.rmtree(root)
        arm = arm_at(root, name)
        shutil.copytree(project_dir, arm.project, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(fixture_dir / "store", arm.memory)
        if name == "with":
            install_skill(arm.project, work_dir / "profile")
        write_settings(arm)
        arms[name] = arm
    return arms


def fresh_copy(template: Arm, dest: Path) -> Arm:
    """One session's own copy of an arm, so no session sees another's writes."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(template.root, dest)
    arm = arm_at(dest, template.name)
    write_settings(arm)
    return arm


def default_store_root(project: Path) -> Path:
    """Where the harness would keep this project's memory without the override (best effort)."""
    key = re.sub(r"[^A-Za-z0-9]", "-", str(project.resolve()))
    return Path.home() / ".claude" / "projects" / key


def live_store_snapshot() -> dict[str, list[int]]:
    """Size and mtime of every file in every memory directory at the harness's default location."""
    base = Path.home() / ".claude" / "projects"
    snap: dict[str, list[int]] = {}
    if base.is_dir():
        for p in base.glob("*/memory/**/*"):
            if p.is_file():
                st = p.stat()
                snap[str(p)] = [st.st_size, st.st_mtime_ns]
    return snap


def skill_listed(init: dict) -> bool | None:
    """Whether the init event lists the skill as available; None when it names no skills at all."""
    names: list[str] = []
    seen = False
    for key in ("skills", "slash_commands"):
        if key in init:
            seen = True
            for item in init.get(key) or []:
                name = item.get("name", "") if isinstance(item, dict) else str(item)
                names.append(name.split(":")[-1].lstrip("/"))
    return (SKILL_ID in names) if seen else None


def skill_name(call: dict) -> str:
    return str((call.get("input") or {}).get("skill", "")).split(":")[-1].lstrip("/")


def skill_loaded(calls: list[dict]) -> bool:
    """A Skill tool call for the skill, or a read of its installed SKILL.md."""
    for call in calls:
        if call.get("name") == "Skill" and skill_name(call) == SKILL_ID:
            return True
        if call.get("name") == "Read":
            path = str((call.get("input") or {}).get("file_path", "")).replace("\\", "/").lower()
            if path.endswith(f"/skills/{SKILL_ID}/skill.md"):
                return True
    return False


def other_skills(calls: list[dict]) -> list[str]:
    return sorted({skill_name(c) for c in calls if c.get("name") == "Skill" and skill_name(c) != SKILL_ID})


def parse_stream(text: str) -> dict:
    """Fold a stream-json run into the result fields, the skills the init event lists, and every tool call."""
    init: dict = {}
    calls: list[dict] = []
    result: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            init = event
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append({"name": block.get("name", ""), "input": block.get("input") or {}})
        elif kind == "result":
            result = event
    parsed = {"tool_calls": calls, "listed": skill_listed(init)}
    if result is None:
        return {**parsed, "result": "", "is_error": True, "total_cost_usd": 0.0}
    return {
        **parsed,
        "result": str(result.get("result", "")),
        "is_error": bool(result.get("is_error", False)),
        "subtype": str(result.get("subtype", "")),
        "total_cost_usd": float(result.get("total_cost_usd") or 0.0),
        "duration_ms": int(result.get("duration_ms") or 0),
        "num_turns": int(result.get("num_turns") or 0),
    }


def run_session(prompt: str, arm: Arm, opts: argparse.Namespace) -> dict:
    """One fresh print session with a stream-json trace."""
    args = [
        claude_command(), "-p", prompt,
        "--model", opts.model,
        "--effort", opts.effort,
        "--tools", SUBJECT_TOOLS,
        "--allowedTools", *SUBJECT_TOOLS.split(","),
        "--permission-mode", "dontAsk",
        "--add-dir", arm.memory.resolve().as_posix(),
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format", "stream-json",
        "--verbose",
        "--max-budget-usd", str(opts.budget_usd),
        "--settings", arm.settings.resolve().as_posix(),
    ]
    try:
        proc = subprocess.run(
            args,
            cwd=str(arm.project),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=opts.timeout,
        )
    except subprocess.TimeoutExpired:
        return {"result": "", "is_error": True, "total_cost_usd": 0.0, "tool_calls": [], "stderr": "timeout"}
    data = parse_stream(proc.stdout)
    data["stderr"] = proc.stderr[-2000:]
    data["exit"] = proc.returncode
    return data


def store_changes(before: Path, after: Path) -> dict:
    """What a session wrote: new files in full, lines added to changed files, removed file names."""
    old, new = tree_texts(before), tree_texts(after)
    changes: dict = {"new": {}, "changed": [], "added_lines": {}, "removed": sorted(set(old) - set(new))}
    for rel, text in new.items():
        if rel not in old:
            changes["new"][rel] = text
        elif text != old[rel]:
            changes["changed"].append(rel)
            changes["added_lines"][rel] = [
                line[2:] for line in difflib.ndiff(old[rel].splitlines(), text.splitlines())
                if line.startswith("+ ")
            ]
    return changes


def wrote(changes: dict) -> bool:
    return bool(changes["new"] or changes["changed"] or changes["removed"])


def unstamped(lines: list[str]) -> list[str]:
    return [line for line in lines if not HARNESS_STAMPS.match(line)]


def material(changes: dict) -> str:
    """The written material a grader sees, less the harness's own stamps, and nothing else."""
    parts = [
        f"FILE {rel} (new):\n" + "\n".join(unstamped(text.strip().splitlines()))
        for rel, text in sorted(changes["new"].items())
    ]
    for rel in sorted(changes["changed"]):
        lines = unstamped(changes["added_lines"].get(rel) or [])
        parts.append(f"FILE {rel} (lines added):\n" + ("\n".join(lines) if lines else "(none)"))
    parts.extend(f"FILE {rel} (removed)" for rel in changes["removed"])
    return "\n\n".join(parts)


def project_changes(before: Path, after: Path) -> list[str]:
    old, new = tree_digests(before), tree_digests(after)
    return [
        *(f"new {rel}" for rel in sorted(set(new) - set(old))),
        *(f"changed {rel}" for rel in sorted(set(old) & set(new)) if old[rel] != new[rel]),
        *(f"removed {rel}" for rel in sorted(set(old) - set(new))),
    ]


def parse_grade(text: str, names: tuple[str, ...] = CRITERIA) -> dict[str, int | None]:
    for match in re.finditer(r"\{[^{}]*\}", text):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and all(obj.get(name) in (0, 1) for name in names):
            return {name: int(obj[name]) for name in names}
    return {name: None for name in names}


def hit(scores: dict[str, int | None] | None) -> int | None:
    """1 only when every question is yes; None when the grade is missing or any question is.

    A session that wrote nothing is never graded; the report counts it a miss.
    """
    if not scores or any(value is None for value in scores.values()):
        return None
    return int(all(value == 1 for value in scores.values()))


def grade(text: str, criteria: dict[str, str], model: str, grader_dir: Path, timeout: int) -> tuple[dict[str, int | None], float]:
    """Blind grade: the grader sees the case's questions and the written material, nothing else."""
    names = tuple(criteria)
    rubric = "\n".join(f"- {name}: {criteria[name]}" for name in names)
    data = run_print(
        [
            f"{grader_prefix(names)}{rubric}\n\nWRITTEN MATERIAL:\n{text}",
            "--model", model,
            "--effort", "low",
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--safe-mode",
            "--output-format", "json",
            "--max-budget-usd", "0.5",
            "--settings", GRADER_SETTINGS,
        ],
        grader_dir,
        timeout,
    )
    return parse_grade(str(data.get("result", "")), names), float(data.get("total_cost_usd") or 0.0)


def release_gate(rows: dict, graders: list[str]) -> dict:
    """The rubric's gate: the skill loads in at least half its arm's sessions, and that arm out-hits the other for both graders."""
    n_with = sum(r["arms"]["with"]["n"] for r in rows.values())
    loaded = sum(r["arms"]["with"]["loaded"] for r in rows.values())
    hits = {a: {g: sum(r["arms"][a]["hits"][g] for r in rows.values()) for g in graders} for a in ARMS}
    loads_ok = n_with > 0 and 2 * loaded >= n_with
    better = all(hits["with"][g] > hits["without"][g] for g in graders)
    return {
        "loaded": loaded,
        "n_with": n_with,
        "hits": hits,
        "loads_ok": loads_ok,
        "better": better,
        "verdict": "the release may go ahead" if loads_ok and better else "the release waits for the owner's word",
    }


def load_or_run(work: Path, blind_id: str, template: Arm, prompt: str, opts: argparse.Namespace) -> dict:
    """Run one session on its own fresh copy, or reuse the saved record so an interrupted run resumes."""
    path = work / "sessions" / f"{blind_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    arm = fresh_copy(template, work / "runs" / blind_id)
    raw = run_session(prompt, arm, opts)
    raw["_blind_id"] = blind_id
    raw["store"] = store_changes(template.memory, arm.memory)
    raw["project_changed"] = project_changes(template.project, arm.project)
    raw["loaded"] = skill_loaded(raw.get("tool_calls", []))
    raw["other_skills"] = other_skills(raw.get("tool_calls", []))
    raw["default_store_created"] = default_store_root(arm.project).exists()
    # An errored session is kept for inspection under another name; a resume runs it again.
    target = path.with_suffix(".error.json") if raw.get("is_error") else path
    target.write_text(json.dumps(raw, indent=1), encoding="utf-8")
    return raw


def run_canary(spec: dict, templates: dict[str, Arm], work: Path, opts: argparse.Namespace) -> tuple[bool, float]:
    """Both arms write the canary into their own scratch store; the skill is listed in one arm only."""
    token = spec["canary"]["token"]
    problems: list[str] = []
    cost = 0.0
    for name in ARMS:
        raw = load_or_run(work, f"canary-{name}", templates[name], spec["canary"]["prompt"], opts)
        cost += float(raw.get("total_cost_usd") or 0.0)
        landed = token in material(raw["store"]).upper()
        print(
            f"canary/{name}: wrote={landed} listed={raw.get('listed')} loaded={raw.get('loaded')} "
            f"cost=${float(raw.get('total_cost_usd') or 0.0):.3f} turns={raw.get('num_turns', 0)}"
        )
        if raw.get("is_error"):
            problems.append(f"{name}: the session errored ({raw.get('subtype') or raw.get('stderr', '')[:200]})")
        if not landed:
            problems.append(f"{name}: the canary did not land in the scratch store")
        if raw.get("listed") is None:
            problems.append(f"{name}: the init event names no skills, so availability cannot be checked")
        elif raw.get("listed") != (name == "with"):
            problems.append(f"{name}: the skill is {'not ' if name == 'with' else ''}listed as available")
        if raw.get("default_store_created"):
            problems.append(f"{name}: a memory directory appeared at the harness's default location")
    for problem in problems:
        print(f"canary failed: {problem}")
    return not problems, cost


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture", type=Path, default=FIXTURE_DIR,
                        help="directory holding cases.json, rubric.md and store/")
    parser.add_argument("--project", type=Path, default=PROJECT_DIR, help="the project every arm copies")
    parser.add_argument(
        "--work-dir", type=Path, default=None,
        help="scratch directory outside this repository for arms, sessions and grades (default: a temp dir)",
    )
    parser.add_argument("--n", type=int, default=5, help="sessions per arm per case")
    parser.add_argument("--case", action="append", default=None, help="case id to run (default: all)")
    parser.add_argument("--model", default="opus", help="model alias for the subject sessions")
    parser.add_argument("--effort", default="high", help="effort level for the subject sessions")
    parser.add_argument("--graders", default="sonnet,opus",
                        help="two grader models, comma separated (the rubric says why not haiku)")
    parser.add_argument("--budget-usd", type=float, default=2.0, help="cap per subject session")
    parser.add_argument("--timeout", type=int, default=600, help="seconds per session")
    parser.add_argument("--skip-canary", action="store_true",
                        help="skip the canary; only for a run whose canary already passed")
    parser.add_argument("--seed", type=int, default=20260928,
                        help="shuffle seed; a resumed run must repeat it with the same --n and --case")
    parser.add_argument("--dry-run", action="store_true", help="build the arms and print the plan; run nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    fixture = opts.fixture.resolve()
    spec = load_cases(fixture)
    cases = [c for c in spec["cases"] if not opts.case or c["id"] in opts.case]
    if not cases:
        raise SystemExit("no case matched")
    work = (opts.work_dir or Path(tempfile.mkdtemp(prefix="memory-writer-"))).resolve()
    if work == REPO_ROOT or REPO_ROOT in work.parents:
        raise SystemExit("--work-dir must be outside this repository, or its sessions load the repository's instructions")
    for sub in ("sessions", "materials", "grader"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    rng = random.Random(opts.seed)
    graders = [g.strip() for g in opts.graders.split(",") if g.strip()]
    if len(graders) != 2:
        raise SystemExit("--graders needs exactly two models")

    templates = build_arms(fixture, opts.project.resolve(), work)
    plan: list[tuple[str, str, str]] = []
    for case in cases:
        for arm in ARMS:
            for _ in range(opts.n):
                plan.append((case["id"], case["prompt"], arm))
    rng.shuffle(plan)

    print(f"work dir: {work}")
    print(f"model {opts.model} effort {opts.effort}; graders {graders}")
    print(f"{len(cases)} case(s) x 2 arms x n={opts.n} = {len(plan)} sessions, plus 2 canary sessions")
    if opts.dry_run:
        package = tree_digests(SKILL_SOURCE)
        for arm in templates.values():
            lines = (arm.memory / "MEMORY.md").read_text(encoding="utf-8").splitlines()
            installed = arm.project / ".claude" / "skills" / SKILL_ID
            skill = (
                f"skill installed, {len(package)} files, bytes match the package: "
                f"{'yes' if tree_digests(installed) == package else 'NO'}"
                if installed.is_dir() else "no skill"
            )
            print(f"  {arm.name}: {len(lines)} index lines, store files {sorted(tree_texts(arm.memory))}; {skill}")
            print(f"    project {arm.project}")
            print(f"    settings {arm.settings.read_text(encoding='utf-8')}")
        return 0

    started = time.time()
    total_cost = 0.0
    live_before = live_store_snapshot()

    if not opts.skip_canary:
        ok, cost = run_canary(spec, templates, work, opts)
        total_cost += cost
        if not ok:
            return 2

    sessions: list[tuple[str, str, str, dict]] = []
    errors: list[tuple[str, str]] = []
    key_path = work / "key.json"
    key: dict[str, dict[str, str]] = (
        json.loads(key_path.read_text(encoding="utf-8")) if key_path.exists() else {}
    )
    # A resumed run reuses its saved blind ids in plan order; the saved
    # labels must agree with the plan, or the seed, n or case set changed.
    saved_ids = list(key)
    for i, (case_id, prompt, arm_name) in enumerate(plan, start=1):
        if i <= len(saved_ids):
            blind_id = saved_ids[i - 1]
            saved = key[blind_id]
            if saved != {"case": case_id, "arm": arm_name}:
                raise SystemExit(
                    f"resume mismatch at session {i}: key.json says {saved}, the plan says "
                    f"{{'case': {case_id!r}, 'arm': {arm_name!r}}}; repeat --seed, --n and --case"
                )
        else:
            blind_id = f"w{rng.randrange(16**6):06x}"
            while blind_id in key:
                blind_id = f"w{rng.randrange(16**6):06x}"
            key[blind_id] = {"case": case_id, "arm": arm_name}
            key_path.write_text(json.dumps(key, indent=1), encoding="utf-8")
        raw = load_or_run(work, blind_id, templates[arm_name], prompt, opts)
        total_cost += float(raw.get("total_cost_usd") or 0.0)
        print(
            f"[{i}/{len(plan)}] {blind_id} cost=${float(raw.get('total_cost_usd') or 0.0):.3f} "
            f"turns={raw.get('num_turns', 0)} wrote={wrote(raw['store'])} error={bool(raw.get('is_error'))}"
        )
        if raw.get("is_error"):
            errors.append((case_id, arm_name))
            continue
        sessions.append((blind_id, case_id, arm_name, raw))
        (work / "materials" / f"{blind_id}.md").write_text(
            f"## Written to the store\n\n{material(raw['store']) or '(nothing)'}\n\n"
            f"## Answer\n\n{raw.get('result', '')}\n",
            encoding="utf-8",
        )

    criteria_by_case = {c["id"]: c["criteria"] for c in cases}
    grades_path = work / "grades.json"
    grades: dict[str, dict[str, dict[str, int | None]]] = (
        json.loads(grades_path.read_text(encoding="utf-8")) if grades_path.exists() else {}
    )
    # Grading cost is kept per grade so a resumed run still reports it.
    costs_path = work / "grade_costs.json"
    grade_costs: dict[str, float] = (
        json.loads(costs_path.read_text(encoding="utf-8")) if costs_path.exists() else {}
    )
    for blind_id, case_id, _arm, raw in sorted(sessions, key=lambda s: s[0]):
        if not wrote(raw["store"]):
            continue
        for g in graders:
            saved = grades.get(blind_id, {}).get(g)
            if saved is not None and all(v is not None for v in saved.values()):
                continue
            scores, cost = grade(material(raw["store"]), criteria_by_case[case_id], g, work / "grader", opts.timeout)
            grade_costs[f"{blind_id}/{g}"] = cost
            grades.setdefault(blind_id, {})[g] = scores
            grades_path.write_text(json.dumps(grades, indent=1), encoding="utf-8")
            costs_path.write_text(json.dumps(grade_costs, indent=1), encoding="utf-8")
            print(f"graded {blind_id} by {g}: {scores}")
    grade_cost = sum(grade_costs.values())
    total_cost += grade_cost

    g0, g1 = graders
    rows: dict = {}
    report: list[str] = []
    for case in cases:
        cid = case["id"]
        names = tuple(case["criteria"])
        row: dict = {"kind": case["kind"], "arms": {}}
        for a in ARMS:
            ss = [(b, r) for b, c, arm, r in sessions if c == cid and arm == a]
            writers = [(b, r) for b, r in ss if wrote(r["store"])]
            scores = {g: [grades.get(b, {}).get(g) for b, _ in writers] for g in graders}
            row["arms"][a] = {
                "n": len(ss),
                "loaded": sum(1 for _, r in ss if r.get("loaded")),
                "wrote": len(writers),
                "loaded_when_wrote": sum(1 for _, r in writers if r.get("loaded")),
                "hits": {g: sum(1 for s in scores[g] if hit(s) == 1) for g in graders},
                "ungraded": {g: sum(1 for s in scores[g] if hit(s) is None) for g in graders},
                "criteria": {
                    g: {name: sum(1 for s in scores[g] if s and s.get(name) == 1) for name in names}
                    for g in graders
                },
                "project_changed": sum(1 for _, r in ss if r.get("project_changed")),
                "other_skills": sorted({name for _, r in ss for name in r.get("other_skills", [])}),
                "default_store_created": sum(1 for _, r in ss if r.get("default_store_created")),
                "cost_usd": round(sum(float(r.get("total_cost_usd") or 0.0) for _, r in ss), 4),
                "errors": sum(1 for c, arm in errors if c == cid and arm == a),
            }
        graded = [b for b, c, _arm, r in sessions if c == cid and wrote(r["store"])]
        agree = sum(1 for b in graded if hit(grades.get(b, {}).get(g0)) == hit(grades.get(b, {}).get(g1)))
        row["grader_agreement"] = f"{agree}/{len(graded)}"
        nw, nwo = row["arms"]["with"]["n"], row["arms"]["without"]["n"]
        row["fisher_p"] = {}
        for g in graders:
            hw, hwo = row["arms"]["with"]["hits"][g], row["arms"]["without"]["hits"][g]
            row["fisher_p"][g] = fisher_two_sided(hw, nw - hw, hwo, nwo - hwo)
        rows[cid] = row
        report.append(f"## {cid} ({case['kind']})")
        report.append("")
        report.append(f"| Arm | n | skill loaded | wrote | hits ({g0}) | hits ({g1}) | cost |")
        report.append("|---|---|---|---|---|---|---|")
        for a in ARMS:
            r = row["arms"][a]
            report.append(
                f"| {a} skill | {r['n']} | {r['loaded']} | {r['wrote']} | {r['hits'][g0]} | {r['hits'][g1]} "
                f"| ${r['cost_usd']:.2f} |"
            )
        report.append("")
        for a in ARMS:
            r = row["arms"][a]
            per = "; ".join(
                f"{g}: " + ", ".join(f"{name} {r['criteria'][g][name]}" for name in names) for g in graders
            )
            report.append(
                f"- {a} skill, yes per question ({per}); project files changed in {r['project_changed']}; "
                f"other skills loaded: {', '.join(r['other_skills']) or 'none'}."
            )
        report.append(
            f"- Grader agreement on hits {row['grader_agreement']} (sessions that wrote); two-sided Fisher p "
            f"{g0} = {row['fisher_p'][g0]:.3g}, {g1} = {row['fisher_p'][g1]:.3g}; sessions that errored and "
            f"were left out: {sum(r['errors'] for r in row['arms'].values())}."
        )
        report.append("")

    gate = release_gate(rows, graders)
    live_after = live_store_snapshot()
    live_changed = sorted(
        path for path in set(live_before) | set(live_after) if live_before.get(path) != live_after.get(path)
    )
    leaked = sum(r["arms"][a]["default_store_created"] for r in rows.values() for a in ARMS)
    results: dict = {
        "model": opts.model, "effort": opts.effort, "graders": graders, "n": opts.n, "seed": opts.seed,
        "cases": rows, "gate": gate, "live_memory_changed": live_changed, "default_store_created": leaked,
        "subject_cost_usd": round(sum(float(s[3].get("total_cost_usd") or 0.0) for s in sessions), 4),
        "grading_cost_usd": round(grade_cost, 4),
        "total_cost_usd": round(total_cost, 4),
        "elapsed_s": round(time.time() - started),
    }
    (work / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    header = [
        "# Memory writer run",
        "",
        f"Model {opts.model}, effort {opts.effort}, tools {SUBJECT_TOOLS}; graders {g0} and {g1}; "
        f"n={opts.n} per arm; seed {opts.seed}.",
        "",
        f"Cost: subjects ${results['subject_cost_usd']:.2f}, grading ${results['grading_cost_usd']:.2f}, "
        f"total ${results['total_cost_usd']:.2f} (canary included); {results['elapsed_s']} s wall clock.",
        "",
    ]
    footer = [
        "## Release gate",
        "",
        f"The skill loaded in {gate['loaded']} of {gate['n_with']} with-skill sessions "
        f"(at least half needed: {'met' if gate['loads_ok'] else 'not met'}). Hits, both cases together: "
        + "; ".join(f"{g} with {gate['hits']['with'][g]}, without {gate['hits']['without'][g]}" for g in graders)
        + f" (with must exceed without for both: {'met' if gate['better'] else 'not met'}). "
        f"Verdict: {gate['verdict']}.",
        "",
        f"Live memory files changed during the run: {len(live_changed)}"
        + (f" (check none is this run's: {', '.join(live_changed)})" if live_changed else "")
        + f". Memory directories created at the default location: {leaked}.",
        "",
    ]
    text = "\n".join(header + report + footer)
    (work / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
