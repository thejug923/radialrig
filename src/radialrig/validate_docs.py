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
import os
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


EXTENDED_PREFIXES = ("\\\\?\\", "//?/")


def _link_exists(root: Path, outside: Path | None, base: Path, target: str,
                 folders: dict[tuple[int, int], Path] | None = None) -> bool:
    """Whether the link `target`, written in a file in `base`, exists.

    With `outside` None the file system answers, as it always has. With
    `outside` set, `root` is the authoring verbs' copy of `docs/` (D-18
    constraint 2) and `outside` the project it was copied from. The link
    is read from the record's place in the project, with `..` collapsed
    there, and an absolute target as written. A target spelled inside the
    project's `docs/`, or passing through one of its folders by another
    name (`folders`, from `_docs_folders`), is looked up in the copy,
    which holds the change; any other is looked up where the live check
    finds it, because no verb writes outside `docs/`. So C4 answers the
    same on both sides of the gate, a record may link to the code or
    subproject it is about, and a broken link still fails (BUG-0023).

    Four known differences are left, each needing an unusual setup
    (BUG-0023): a link spelled with a Windows short (8.3) name into
    `docs/` may find another short name on the copy, which rejects a
    change; a change whose own path runs through an alias folder inside
    `docs/` moves the copy's plain duplicate; and on POSIX, `..` after a
    symlinked folder inside `docs/` is collapsed before the symlink is
    followed, and a file symlink outside `docs/` that points at a record
    is looked up live.
    """
    if outside is None:
        return (base / target).exists()
    if os.name == "nt" and target.startswith(EXTENDED_PREFIXES):
        # Read it as the live check does, through pathlib, which drops a
        # `.`. What is left Windows reads as written: it collapses no `..`
        # and trims no trailing dot or space. Where that still decides,
        # answer with the live check's own reading; no verb writes such a
        # name. Otherwise it is an ordinary absolute path.
        spelled = str(base / target)
        rest = spelled[len(EXTENDED_PREFIXES[0]):]
        if any(part == ".." or part.endswith((".", " "))
               for part in rest.split(os.sep) if part):
            return (base / target).exists()
        if rest[:4].upper() == "UNC" + os.sep:
            rest = os.sep * 2 + rest[4:]
        target = rest
    try:
        live = outside / os.path.relpath(base, root) / target
        lexical = Path(os.path.normpath(live))
        # The folder's real place first, so a spelling through an alias
        # inside docs/, which the copy holds as a plain duplicate, still
        # reaches the folder the change moves files in.
        inside = _known_folder_under(lexical, folders) if folders else None
        if inside is None:
            inside = _spelled_under(lexical, outside / "docs")
    except (OSError, ValueError):
        return (base / target).exists()
    if inside is not None:
        return (root / "docs" / inside).exists()
    # The collapsed path: on POSIX the raw one would have to enter a
    # folder the change creates, which exists only on the copy.
    return os.path.exists(lexical)


def _spelled_under(path: Path, folder: Path) -> Path | None:
    """`path` relative to `folder` when it is spelled inside it, or None.

    A junction inside `docs/` that leads out of it is still `docs/` here,
    as it is to the verbs, which stage and move through it.
    """
    p, f = os.path.normpath(path), os.path.normpath(folder)
    if p == f:
        return Path(".")
    prefix = f.rstrip(os.sep) + os.sep
    return Path(p[len(prefix):]) if p.startswith(prefix) else None


def _docs_folders(docs: Path) -> dict[tuple[int, int], Path]:
    """Every folder in the project's `docs/`, by file identity, with its
    place relative to `docs/`.

    The walk enters junctions and symlinked folders, because the verbs
    stage and move through them, and visits each folder once, so a link
    that loops back stops it. Every real folder is placed before any
    folder reached through a link, so a folder `docs/` also links to
    inside itself keeps its real place.
    """
    found: dict[tuple[int, int], Path] = {}
    real = [(docs, Path("."))]
    linked: list[tuple[Path, Path]] = []
    while real or linked:
        folder, rel = real.pop() if real else linked.pop()
        try:
            seen = os.stat(folder)
            entries = list(os.scandir(folder))
        except OSError:
            continue
        key = (seen.st_dev, seen.st_ino)
        if key in found:
            continue
        found[key] = rel
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
                queue = linked if _is_link(entry) else real
            except OSError:
                continue
            queue.append((Path(entry.path), rel / entry.name))
    return found


def _is_link(entry: os.DirEntry) -> bool:
    """A symlink, or a Windows junction (`is_junction` from Python 3.12;
    before that, the reparse-point attribute)."""
    if entry.is_symlink():
        return True
    check = getattr(entry, "is_junction", None)
    if check is not None:
        return check()
    attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _known_folder_under(path: Path, folders: dict[tuple[int, int], Path]) -> Path | None:
    """`path` placed in `docs/` when one of the folders it passes through
    is a folder of `docs/` by another name: a junction, a symlink, another
    letter case on a disk that ignores case, a network spelling. Decided
    by file identity, as the file system decides it, and never by folding
    names, so a disk that tells `DOCS` from `docs` keeps them apart.
    """
    tail: list[str] = []
    for candidate in (path, *path.parents):
        try:
            seen = os.stat(candidate)
        except OSError:
            seen = None
        if seen is not None:
            place = folders.get((seen.st_dev, seen.st_ino))
            if place is not None:
                return place.joinpath(*reversed(tail))
        tail.append(candidate.name)
    return None


def validate(
    root: Path,
    today: dt.date | None = None,
    *,
    baseline: Mapping[str, dict[str, str]],
    files: list[Path] | None = None,
    outside: Path | None = None,
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

    `outside` is the tree C4 looks up a link target outside `docs/` in.
    None looks every target up under `root`. The authoring verbs pass the
    project when `root` is their copy of `docs/`.
    """
    today = today or dt.date.today()
    failures: list[str] = []
    in_force: dict[tuple[str, str], list[str]] = {}
    folders = _docs_folders(outside / "docs") if outside is not None else None

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
            if not _link_exists(root, outside, path.parent, target, folders):
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
