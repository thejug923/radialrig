"""Measure whether one memory entry changes a fresh session's answer.

The paired fresh-session ablation: copy the fixture store twice, remove
one entry (its index line and its detail file) from one copy, point the
harness at each copy, ask the same inviting prompt in fresh print
sessions, and grade the answers blind against the rubric written before
the runs. Two graders on different models grade every answer. The
runner reports hits per arm for each grader, their agreement, a
two-sided Fisher exact p, and the cost.

    uv run python evals/run_memory_ablation.py --dry-run
    uv run python evals/run_memory_ablation.py --n 5 --work-dir <scratch>
    uv run python evals/run_memory_ablation.py --n 5 --work-dir <scratch> --install-skill

With `--install-skill` the memory-keeper skill is installed into each
arm's scratch project through the product's own CLI, with the per-user
profile folders pointed at scratch, and its bytes are checked against
the package; the subject sessions' tools do not change. `--fixture` may
name a copy of the fixture whose store an upkeep pass has maintained.

It measures one thing: whether a fresh session's first answer changes
when the entry is present. It says nothing about whether an agent writes
good entries, and nothing about long sessions. Every session runs
against a scratch copy of the fixture; no live store is touched. The
harness's `--restricted` flag is not used: it switches auto memory off,
so the store would never load (probed 2026-09-27). The person's own
user-level instruction files load as they would in any session. The
directory is ungoverned evidence and not part of the pytest suite.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
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
FIXTURE_DIR = EVALS_DIR / "memory-ablation"
SKILL_ID = "memory-keeper"
SKILL_SOURCE = EVALS_DIR.parent / "src" / "radialrig" / "data" / "skills" / SKILL_ID
PROFILE_VARS = ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "HOME")
SUBJECT_TOOLS = "Read,Grep,Glob"
GRADER_SETTINGS = json.dumps({"autoMemoryEnabled": False})
GRADER_PREFIX = (
    "You are grading one answer an AI coding assistant gave to a project owner. "
    "Apply the rubric to the answer and output ONLY a JSON object on one line, "
    'no prose, no code fence: {"hit": 0} or {"hit": 1}.\n\nRUBRIC:\n'
)
CANARY_TOKEN = "PERIWINKLE-42"


@dataclass
class Arm:
    name: str
    project: Path
    memory: Path
    settings: Path


@dataclass
class Session:
    blind_id: str
    case_id: str
    arm: str
    answer: str
    cost_usd: float
    duration_ms: int
    num_turns: int
    is_error: bool


def load_cases(fixture_dir: Path) -> dict:
    return json.loads((fixture_dir / "cases.json").read_text(encoding="utf-8"))


def remove_entry(memory_dir: Path, index_line_prefix: str, detail_file: str) -> None:
    """Drop one entry: its index line and its detail file. Nothing else moves."""
    index = memory_dir / "MEMORY.md"
    lines = index.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for line in lines if not line.startswith(index_line_prefix)]
    if len(kept) != len(lines) - 1:
        raise SystemExit(f"index line not found exactly once: {index_line_prefix!r}")
    index.write_text("".join(kept), encoding="utf-8", newline="\n")
    target = memory_dir / detail_file
    if not target.exists():
        raise SystemExit(f"detail file missing: {target}")
    target.unlink()


def tree_digests(root: Path) -> dict[str, str]:
    """sha256 per file under root, keyed by its posix path relative to root."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


@contextlib.contextmanager
def scratch_profile(root: Path):
    """Point the per-user profile variables at scratch, so an install writes no real log or roster."""
    saved = {name: os.environ.get(name) for name in PROFILE_VARS}
    try:
        for name in PROFILE_VARS:
            path = root / name.lower()
            path.mkdir(parents=True, exist_ok=True)
            os.environ[name] = str(path)
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def install_skill(project: Path, profile: Path) -> None:
    """Install the skill for Claude Code through the product's own CLI, then check its bytes."""
    from radialrig import cli

    out, err = io.StringIO(), io.StringIO()
    with scratch_profile(profile), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["skill", "add", SKILL_ID, str(project), "--harness", "claude-code"])
    if code != 0:
        raise SystemExit(f"skill install failed with exit {code}:\n{out.getvalue()}{err.getvalue()}")
    if tree_digests(project / ".claude" / "skills" / SKILL_ID) != tree_digests(SKILL_SOURCE):
        raise SystemExit("the installed skill's bytes differ from the package source")


def build_arms(
    fixture_dir: Path, work_dir: Path, entry: dict, label: str, install: bool = False
) -> dict[str, Arm]:
    """Two scratch copies of project and store; the entry is absent from one.

    With `install`, the skill goes into both arms' projects: the arms still
    differ only by the entry.
    """
    arms: dict[str, Arm] = {}
    for name in ("present", "absent"):
        root = work_dir / "arms" / label / name
        if root.exists():
            shutil.rmtree(root)
        project = root / "project"
        memory = root / "memory"
        shutil.copytree(
            fixture_dir / "fixture" / "project", project, ignore=shutil.ignore_patterns("__pycache__")
        )
        shutil.copytree(fixture_dir / "fixture" / "memory", memory)
        if install:
            install_skill(project, work_dir / "profile")
        if name == "absent":
            remove_entry(memory, entry["index_line_prefix"], entry["detail_file"])
        settings = root / "settings.json"
        settings.write_text(
            json.dumps({"autoMemoryDirectory": memory.resolve().as_posix()}),
            encoding="utf-8",
        )
        arms[name] = Arm(name, project, memory, settings)
    return arms


def claude_command() -> str:
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("claude is not on PATH")
    return exe


def run_print(args: list[str], cwd: Path, timeout: int) -> dict:
    """One `claude -p` call with JSON output, parsed; a parse failure is an error result."""
    try:
        proc = subprocess.run(
            [claude_command(), "-p", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"result": "", "is_error": True, "total_cost_usd": 0.0, "stderr": "timeout"}
    text = proc.stdout.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {
            "result": text,
            "is_error": True,
            "total_cost_usd": 0.0,
            "stderr": proc.stderr[-2000:],
            "exit": proc.returncode,
        }
    return data


def run_subject(prompt: str, arm: Arm, opts: argparse.Namespace) -> dict:
    args = [
        prompt,
        "--model", opts.model,
        "--effort", opts.effort,
        "--tools", SUBJECT_TOOLS,
        "--allowedTools", *SUBJECT_TOOLS.split(","),
        "--permission-mode", "dontAsk",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format", "json",
        "--max-budget-usd", str(opts.budget_usd),
        "--settings", arm.settings.resolve().as_posix(),
    ]
    return run_print(args, arm.project, opts.timeout)


def grade(answer: str, rubric: str, model: str, grader_dir: Path, timeout: int) -> tuple[int | None, float]:
    """Blind grade: the grader sees the rubric and the answer text, nothing else."""
    prompt = f"{GRADER_PREFIX}{rubric}\n\nANSWER:\n{answer}"
    data = run_print(
        [
            prompt,
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
    cost = float(data.get("total_cost_usd") or 0.0)
    match = re.search(r"\{[^{}]*\"hit\"\s*:\s*([01])[^{}]*\}", str(data.get("result", "")))
    return (int(match.group(1)) if match else None), cost


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for the table [[a, b], [c, d]]."""

    def log_comb(n: int, k: int) -> float:
        return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)

    row1, row2, col1, n = a + b, c + d, a + c, a + b + c + d
    if n == 0:
        return 1.0

    def prob(x: int) -> float:
        return math.exp(log_comb(row1, x) + log_comb(row2, col1 - x) - log_comb(n, col1))

    observed = prob(a)
    total = 0.0
    for x in range(max(0, col1 - row2), min(row1, col1) + 1):
        p = prob(x)
        if p <= observed * (1 + 1e-9):
            total += p
    return min(1.0, total)


def load_or_run(
    session_path: Path, blind_id: str, case_id: str, arm: Arm, prompt: str, opts: argparse.Namespace
) -> Session:
    """Run one subject session, or reuse the saved one so an interrupted run resumes."""
    if session_path.exists():
        raw = json.loads(session_path.read_text(encoding="utf-8"))
    else:
        raw = run_subject(prompt, arm, opts)
        raw["_blind_id"] = blind_id
        if raw.get("is_error"):
            # Kept for inspection under another name; a resume runs the session again.
            error_path = session_path.with_suffix(".error.json")
            error_path.write_text(json.dumps(raw, indent=1), encoding="utf-8")
        else:
            session_path.write_text(json.dumps(raw, indent=1), encoding="utf-8")
    return Session(
        blind_id=blind_id,
        case_id=case_id,
        arm=arm.name,
        answer=str(raw.get("result", "")),
        cost_usd=float(raw.get("total_cost_usd") or 0.0),
        duration_ms=int(raw.get("duration_ms") or 0),
        num_turns=int(raw.get("num_turns") or 0),
        is_error=bool(raw.get("is_error", False)),
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture", type=Path, default=FIXTURE_DIR,
                        help="directory holding cases.json, rubric.md and fixture/")
    parser.add_argument(
        "--work-dir", type=Path, default=None,
        help="scratch directory for arms, sessions and grades (default: a temp dir)",
    )
    parser.add_argument("--n", type=int, default=5, help="sessions per arm per case")
    parser.add_argument("--case", action="append", default=None, help="case id to run (default: all)")
    parser.add_argument("--model", default="opus", help="model alias for the subject sessions")
    parser.add_argument("--effort", default="high", help="effort level for the subject sessions")
    parser.add_argument("--graders", default="haiku,sonnet", help="two grader models, comma separated")
    parser.add_argument("--budget-usd", type=float, default=2.0, help="cap per subject session")
    parser.add_argument("--timeout", type=int, default=600, help="seconds per session")
    parser.add_argument("--skip-canary", action="store_true",
                        help="skip the store-loads check; only for a run whose canary already passed")
    parser.add_argument("--seed", type=int, default=20260927,
                        help="shuffle seed; a resumed run must repeat it with the same --n and --case")
    parser.add_argument("--install-skill", action="store_true",
                        help=f"install {SKILL_ID} into each arm's scratch project through the product's CLI")
    parser.add_argument("--dry-run", action="store_true", help="build the arms and print the plan; run nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    fixture = opts.fixture.resolve()
    spec = load_cases(fixture)
    cases = [c for c in spec["cases"] if not opts.case or c["id"] in opts.case]
    if not cases:
        raise SystemExit("no case matched")
    work = (opts.work_dir or Path(tempfile.mkdtemp(prefix="memory-ablation-"))).resolve()
    (work / "sessions").mkdir(parents=True, exist_ok=True)
    (work / "answers").mkdir(exist_ok=True)
    grader_dir = work / "grader"
    grader_dir.mkdir(exist_ok=True)
    rng = random.Random(opts.seed)
    graders = [g.strip() for g in opts.graders.split(",") if g.strip()]
    if len(graders) != 2:
        raise SystemExit("--graders needs exactly two models")

    canary_arms = build_arms(fixture, work, spec["canary"], "canary", opts.install_skill)
    case_arms = {c["id"]: build_arms(fixture, work, c, c["id"], opts.install_skill) for c in cases}
    skill: dict = {"installed": opts.install_skill}
    if opts.install_skill:
        skill.update({"id": SKILL_ID, "files": tree_digests(SKILL_SOURCE)})
    skill_line = (
        f"{SKILL_ID} installed in every arm's project, {len(skill.get('files', {}))} files, "
        "bytes match the package"
        if opts.install_skill else "no skill installed"
    )
    plan: list[tuple[str, str, Arm]] = []
    for case in cases:
        for arm in case_arms[case["id"]].values():
            for _ in range(opts.n):
                plan.append((case["id"], case["prompt"], arm))
    rng.shuffle(plan)

    print(f"work dir: {work}")
    print(f"model {opts.model} effort {opts.effort}; graders {graders}")
    print(f"fixture {fixture}; {skill_line}")
    print(f"{len(cases)} case(s) x 2 arms x n={opts.n} = {len(plan)} sessions, plus 2 canary sessions")
    if opts.dry_run:
        for label, arms in [("canary", canary_arms), *case_arms.items()]:
            for arm in arms.values():
                lines = (arm.memory / "MEMORY.md").read_text(encoding="utf-8").splitlines()
                files = sorted(p.name for p in arm.memory.iterdir())
                print(f"  {label}/{arm.name}: {len(lines)} index lines, files {files}")
        return 0

    started = time.time()
    total_cost = 0.0

    if not opts.skip_canary:
        outcome: dict[str, bool] = {}
        for arm in canary_arms.values():
            path = work / "sessions" / f"canary-{arm.name}.json"
            s = load_or_run(path, f"canary-{arm.name}", "canary", arm, spec["canary"]["prompt"], opts)
            total_cost += s.cost_usd
            outcome[arm.name] = CANARY_TOKEN in s.answer.upper()
            print(f"canary/{arm.name}: hit={outcome[arm.name]} cost=${s.cost_usd:.3f} turns={s.num_turns}")
        if not (outcome["present"] and not outcome["absent"]):
            print("canary failed: the store did not load in the present arm, or leaked into the absent arm")
            return 2

    sessions: list[Session] = []
    errors: list[Session] = []
    key_path = work / "key.json"
    key: dict[str, dict[str, str]] = (
        json.loads(key_path.read_text(encoding="utf-8")) if key_path.exists() else {}
    )
    # A resumed run reuses its saved blind ids in plan order; the saved
    # labels must agree with the plan, or the seed, n or case set changed.
    saved_ids = [bid for bid in key if not bid.startswith("canary")]
    for i, (case_id, prompt, arm) in enumerate(plan, start=1):
        if i <= len(saved_ids):
            blind_id = saved_ids[i - 1]
            saved = key[blind_id]
            if saved != {"case": case_id, "arm": arm.name}:
                raise SystemExit(
                    f"resume mismatch at session {i}: key.json says {saved}, the plan says "
                    f"{{'case': {case_id!r}, 'arm': {arm.name!r}}}; repeat --seed, --n and --case"
                )
        else:
            blind_id = f"s{rng.randrange(16**6):06x}"
            while blind_id in key:
                blind_id = f"s{rng.randrange(16**6):06x}"
            key[blind_id] = {"case": case_id, "arm": arm.name}
            key_path.write_text(json.dumps(key, indent=1), encoding="utf-8")
        s = load_or_run(work / "sessions" / f"{blind_id}.json", blind_id, case_id, arm, prompt, opts)
        total_cost += s.cost_usd
        print(f"[{i}/{len(plan)}] {blind_id} cost=${s.cost_usd:.3f} turns={s.num_turns} error={s.is_error}")
        if s.is_error:
            errors.append(s)
            continue
        sessions.append(s)
        (work / "answers" / f"{blind_id}.txt").write_text(s.answer, encoding="utf-8")

    rubric_by_case = {c["id"]: c["rubric"] for c in cases}
    grades_path = work / "grades.json"
    grades: dict[str, dict[str, int | None]] = (
        json.loads(grades_path.read_text(encoding="utf-8")) if grades_path.exists() else {}
    )
    # Grading cost is kept per grade so a resumed run still reports it.
    costs_path = work / "grade_costs.json"
    grade_costs: dict[str, float] = (
        json.loads(costs_path.read_text(encoding="utf-8")) if costs_path.exists() else {}
    )
    for s in sorted(sessions, key=lambda s: s.blind_id):
        for g in graders:
            if grades.get(s.blind_id, {}).get(g) is not None:
                continue
            hit, cost = grade(s.answer, rubric_by_case[s.case_id], g, grader_dir, opts.timeout)
            grade_costs[f"{s.blind_id}/{g}"] = cost
            grades.setdefault(s.blind_id, {})[g] = hit
            grades_path.write_text(json.dumps(grades, indent=1), encoding="utf-8")
            costs_path.write_text(json.dumps(grade_costs, indent=1), encoding="utf-8")
            print(f"graded {s.blind_id} by {g}: hit={hit}")
    grade_cost = sum(grade_costs.values())
    total_cost += grade_cost

    report: list[str] = []
    results: dict = {
        "model": opts.model, "effort": opts.effort, "graders": graders, "n": opts.n,
        "fixture": fixture.as_posix(), "skill": skill, "cases": {},
    }
    g0, g1 = graders
    for case in cases:
        cid = case["id"]
        by_arm = {a: [s for s in sessions if s.case_id == cid and s.arm == a] for a in ("present", "absent")}
        row: dict = {"kind": case["kind"], "effect": case.get("effect", ""), "arms": {}}
        for a, ss in by_arm.items():
            row["arms"][a] = {
                "n": len(ss),
                "hits": {g: sum(1 for s in ss if grades[s.blind_id][g] == 1) for g in graders},
                "ungraded": {g: sum(1 for s in ss if grades[s.blind_id][g] is None) for g in graders},
                "cost_usd": round(sum(s.cost_usd for s in ss), 4),
                "errors": sum(1 for e in errors if e.case_id == cid and e.arm == a),
            }
        graded_pairs = [s for s in sessions if s.case_id == cid]
        agree = sum(1 for s in graded_pairs if grades[s.blind_id][g0] == grades[s.blind_id][g1])
        row["grader_agreement"] = f"{agree}/{len(graded_pairs)}"
        npres, nabs = row["arms"]["present"]["n"], row["arms"]["absent"]["n"]
        row["fisher_p"] = {}
        for g in graders:
            hp, hn = row["arms"]["present"]["hits"][g], row["arms"]["absent"]["hits"][g]
            row["fisher_p"][g] = fisher_two_sided(hp, npres - hp, hn, nabs - hn)
        results["cases"][cid] = row
        report.append(f"## {cid} ({case['kind']}; {case.get('effect', '')})")
        report.append("")
        report.append(f"| Arm | n | hits ({g0}) | hits ({g1}) | cost |")
        report.append("|---|---|---|---|---|")
        for a in ("present", "absent"):
            r = row["arms"][a]
            report.append(f"| entry {a} | {r['n']} | {r['hits'][g0]} | {r['hits'][g1]} | ${r['cost_usd']:.2f} |")
        report.append("")
        report.append(
            f"Grader agreement {row['grader_agreement']}; two-sided Fisher p "
            f"{g0} = {row['fisher_p'][g0]:.3g}, {g1} = {row['fisher_p'][g1]:.3g}; "
            f"sessions that errored and were left out: "
            f"{sum(r['errors'] for r in row['arms'].values())}."
        )
        report.append("")
    results["subject_cost_usd"] = round(sum(s.cost_usd for s in sessions), 4)
    results["grading_cost_usd"] = round(grade_cost, 4)
    results["total_cost_usd"] = round(total_cost, 4)
    results["elapsed_s"] = round(time.time() - started)
    (work / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    header = [
        "# Memory ablation run",
        "",
        f"Model {opts.model}, effort {opts.effort}, tools {SUBJECT_TOOLS}; "
        f"graders {g0} and {g1}; n={opts.n} per arm.",
        "",
        f"Fixture {fixture.as_posix()}; {skill_line}.",
        "",
        f"Cost: subjects ${results['subject_cost_usd']:.2f}, grading ${results['grading_cost_usd']:.2f}, "
        f"total ${results['total_cost_usd']:.2f} (canary included); {results['elapsed_s']} s wall clock.",
        "",
    ]
    (work / "report.md").write_text("\n".join(header + report), encoding="utf-8")
    print("\n".join(header + report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
