#!/usr/bin/env python3
"""Mechanical checks for governed documentation.

One command, per PLAN-0001 Phase 3:  radialrig-validate
Exit 0 when every check passes; exit 1 with one line per failure.

Parsing, status, and authority logic live in doc_core.py, shared with the
retrieval server (D-13 constraint 4).

Checks (each names the decision record it enforces):
  C1 (uniqueness)  one in-force record per (id, scope)  D-01
  C2 (metadata)    required fields present and valid    D-02
  C3 (head line)   status, replaced-by in first line    D-03
  C4 (links)       relative links resolve               SPEC-1 s8
  C5 (lifecycle)   transitions legal vs baseline; id stable  D-06, SPEC-1 s4
  C6 (freshness)   last-verified within 90 days         SPEC-1 / PLAN-0001
  C7 (archive)     placement and archived voice line    D-04, D-05
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from radialrig.doc_core import (
    DATE_RE,
    GRADES,
    LEGAL_TRANSITIONS,
    LINK_RE,
    NOT_UTF8,
    REQUIRED_FIELDS,
    STALE_DAYS,
    STATUSES,
    TYPES,
    first_body_line,
    first_body_paragraph,
    governed_files,
    parse_frontmatter,
    read_governed,
    strip_code,
)
from radialrig.paths import resolve_root


class BaselineUnavailable(RuntimeError):
    """No prior state can be read for a corpus root."""


def _git(root: Path, *args: str) -> str | None:
    """stdout of a git command run at `root`, or None when it fails.

    Output that is not UTF-8 is None too: a committed copy of a governed
    file in another encoding is no baseline, and the caller falls back to
    what it has rather than raising out of every check.
    """
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True)
    except OSError:
        return None
    if out.returncode != 0:
        return None
    try:
        return out.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def under_git(root: Path) -> bool:
    """True when git answers for `root`, with or without a commit yet.

    `main` asks this before it refuses for want of a baseline, and the
    mount report asks it before naming the command to run next, so the
    two cannot disagree on which projects need `--no-baseline`.
    """
    return _git(root, "rev-parse", "--git-dir") is not None


def git_head_frontmatter(root: Path, rel: str) -> dict[str, str] | None:
    """Frontmatter of one governed file as git HEAD holds it, or None.

    None covers three cases the caller must not tell apart: no git, no
    HEAD, and a file HEAD does not carry. All three mean "no committed
    state for this path", and the caller falls back to what it has.

    `HEAD:./<rel>` resolves against the working directory; see
    `git_head_baseline` for what the rooted form cost us.
    """
    blob = _git(root, "show", f"HEAD:./{rel}")
    if blob is None:
        return None
    fm, _ = parse_frontmatter(blob)
    return fm


def git_head_baseline(
    root: Path, files: list[Path] | None = None
) -> dict[str, dict[str, str]]:
    """Prior frontmatter per governed file, read from git HEAD at `root`.

    Keys are root-relative posix paths. A file absent from the mapping has
    no prior state, so C5 has nothing to compare for it. `files` lets a
    caller that already walked the governed tree pass its list in; None
    walks it here.

    `HEAD:./<rel>` resolves against the working directory, so a corpus root
    below the repository root reads its own files. Plain `HEAD:<rel>`
    resolves against the *repository* root: it missed every file under
    `--root` and, on a colliding path, returned an unrelated governed file.
    That is how C5 skipped fourteen runs in silence (PLAN-0003 Phase 1).

    Raises BaselineUnavailable when git cannot answer for this root at all.
    Returning an empty mapping on failure would read as "every record is
    new", which is the same silence in a new place.
    """
    if _git(root, "rev-parse", "--verify", "HEAD") is None:
        raise BaselineUnavailable(f"no readable git HEAD at {root}")
    baseline: dict[str, dict[str, str]] = {}
    for path in (governed_files(root) if files is None else files):
        rel = path.relative_to(root).as_posix()
        fm = git_head_frontmatter(root, rel)
        if fm is not None:
            baseline[rel] = fm  # absent: a new file, nothing to compare
    return baseline


def validate(
    root: Path,
    today: dt.date | None = None,
    *,
    baseline: Mapping[str, dict[str, str]],
    files: list[Path] | None = None,
) -> list[str]:
    """Run C1-C7 over the corpus at `root`.

    `baseline` carries the prior frontmatter C5 compares against, keyed by
    root-relative posix path; `{}` states that no record has a prior state.
    The caller supplies it, because the corpus under check is not always
    the working tree — D-18 constraint 2 validates a temporary copy, which
    has no history of its own. The argument is required and keyword-only:
    an optional baseline is a check that skips itself when a caller
    forgets, which is the defect this signature exists to close.

    `files` lets a caller that already walked the governed tree pass its
    list in; None walks it here.
    """
    today = today or dt.date.today()
    failures: list[str] = []
    in_force: dict[tuple[str, str], list[str]] = {}

    for path in (governed_files(root) if files is None else files):
        rel = path.relative_to(root).as_posix()
        text = read_governed(path)
        if text is None:
            failures.append(f"C2 (metadata) {rel}: {NOT_UTF8}")
            continue
        fm, body = parse_frontmatter(text)

        if fm is None:
            failures.append(f"C2 (metadata) {rel}: no parseable frontmatter block")
            continue

        # C2 — contract
        for field in REQUIRED_FIELDS:
            if not fm.get(field):
                failures.append(f"C2 (metadata) {rel}: missing required field '{field}'")
        status = fm.get("status", "")
        if status and status not in STATUSES:
            failures.append(f"C2 (metadata) {rel}: status '{status}' not in {sorted(STATUSES)}")
        if fm.get("grade") and fm["grade"] not in GRADES:
            failures.append(f"C2 (metadata) {rel}: grade '{fm['grade']}' not in {sorted(GRADES)}")
        if fm.get("type") and fm["type"] not in TYPES:
            failures.append(f"C2 (metadata) {rel}: type '{fm['type']}' not in {sorted(TYPES)}")
        for field in ("effective", "last-verified"):
            v = fm.get(field, "")
            if v and not DATE_RE.match(v):
                failures.append(f"C2 (metadata) {rel}: {field} '{v}' is not YYYY-MM-DD")

        # C1 — uniqueness
        if status == "in-force" and fm.get("id"):
            in_force.setdefault((fm["id"], fm.get("scope", "")), []).append(rel)

        # C3 — head of file. The status prefix must open the block; the
        # replaced-by pointer may fall anywhere in the head paragraph,
        # because the sentence wraps in practice (G8).
        first = first_body_line(body)
        head = first_body_paragraph(body)
        if status:
            if not first.startswith(f"**Status: {status}.**"):
                failures.append(
                    f"C3 (head line) {rel}: first body line must start with '**Status: {status}.**'"
                )
            replaced = fm.get("replaced-by", "none")
            if replaced not in ("", "none") and replaced not in head:
                failures.append(
                    f"C3 (head line) {rel}: replaced-by '{replaced}' absent from head paragraph"
                )

        # C4 — links resolve (ignore fenced blocks and inline code spans)
        for target in LINK_RE.findall(strip_code(text)):
            target = target.split("#")[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (path.parent / target).exists():
                failures.append(f"C4 (links) {rel}: link target '{target}' does not exist")

        # C5 — transitions and stable id vs the baseline
        prev_fm = baseline.get(rel)
        prev_status = prev_fm.get("status") if prev_fm else None
        if prev_status in STATUSES and status in STATUSES:
            if status not in LEGAL_TRANSITIONS[prev_status]:
                failures.append(
                    f"C5 (lifecycle) {rel}: illegal transition '{prev_status}' -> '{status}'"
                )
        prev_id = prev_fm.get("id") if prev_fm else None
        if prev_id and fm.get("id") and prev_id != fm["id"]:
            failures.append(f"C5 (lifecycle) {rel}: id changed '{prev_id}' -> '{fm['id']}'")

        # C6 — staleness
        lv = fm.get("last-verified", "")
        if DATE_RE.match(lv):
            age = (today - dt.date.fromisoformat(lv)).days
            if age > STALE_DAYS and status in ("in-force", "draft"):
                failures.append(f"C6 (freshness) {rel}: last-verified {lv} is {age} days old (max {STALE_DAYS})")

        # C7 — archive placement and voice
        under_archive = "docs/archive/" in rel
        if status == "archived" and not under_archive:
            failures.append(f"C7 (archive) {rel}: archived record outside docs/archive/")
        if under_archive and status != "archived":
            failures.append(f"C7 (archive) {rel}: record under docs/archive/ has status '{status}'")
        if status == "archived" and "not an instruction" not in head:
            failures.append(f"C7 (archive) {rel}: archived head paragraph lacks 'not an instruction'")

    for (key, scope), paths in in_force.items():
        if len(paths) > 1:
            failures.append(
                f"C1 (uniqueness) ({key}, {scope}): {len(paths)} in-force records: {', '.join(paths)}"
            )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=None)
    parser.add_argument(
        "--no-baseline", action="store_true",
        help="validate with no prior state; C5 transition and id checks do not run",
    )
    args = parser.parse_args()

    root = (args.root or resolve_root()).resolve()
    if args.no_baseline:
        baseline: Mapping[str, dict[str, str]] = {}
        print("note: --no-baseline, so C5 (lifecycle) transition and id checks did not run")
    else:
        try:
            baseline = git_head_baseline(root)
        except BaselineUnavailable as exc:
            if under_git(root):
                # A repository with no commit yet: git answered, and the
                # answer is that no record has a prior state, so the
                # empty baseline is the truthful one (D-23 lets the
                # caller supply it). The note keeps the skip loud.
                baseline = {}
                print("note: no commit here yet, so C5 (lifecycle) "
                      "transition and id checks did not run")
            else:
                print(f"C5 (lifecycle) {root}: {exc}; pass --no-baseline to validate without it")
                return 1

    failures = validate(root, args.today, baseline=baseline)
    for f in failures:
        print(f)
    n = len(list(governed_files(root)))
    if failures:
        print(f"FAIL: {len(failures)} failure(s) across {n} governed file(s)")
        return 1
    print(f"OK: {n} governed file(s), all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
