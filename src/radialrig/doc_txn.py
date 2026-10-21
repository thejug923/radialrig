"""Transaction core for the authoring write path.

The substrate the three verbs in SPEC-3 (docs/spec-authoring-tools.md)
are built on, ruled by D-18 and executed by PLAN-0003 Phase 2. It knows
nothing about rulings, modes or archive entries: it takes a set of file
operations, proves they add no validator failure, and lands them all or
lands none.

Parsing and truth selection come from doc_core; the checks come from
validate_docs. Neither is reimplemented here (D-18 constraint 7).

Four properties this module owns:

  1. Validate on a copy, write after (D-18 constraint 2). The working
     tree is never the place a change is tried out.
  2. All paths or none (constraint 3). A failure part-way through
     restores every touched path to its prior bytes.
  3. The gate is delta-based (SPEC-3 section 2). A failure the corpus
     already had does not block a change that did not cause it.
  4. No delete is representable. The operations are Write and Move; the
     type system carries constraint 1 rather than a check
     (D-06, docs/decisions/D-06-stable-identifiers.md).

No function here runs git except through validate_docs.git_head_baseline,
which reads and never writes. Committing is the reviewer's (constraint 4).
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from radialrig.doc_core import LINK_RE, governed_files, read_governed, strip_code
from radialrig.validate_docs import BaselineUnavailable, git_head_baseline, validate

DOCS = "docs"
GOVERNED_DIRS = ("docs/decisions/", "docs/plans/", "docs/archive/")
ARCHIVE = "docs/archive/"


# --- Operations --------------------------------------------------------
#
# There is no Remove. A record leaves a path only by arriving at another
# one, so a verb cannot delete a record even by mistake.

@dataclass(frozen=True)
class Write:
    """Create a path, or replace the whole text at one."""
    path: str
    text: str


@dataclass(frozen=True)
class Move:
    """Move a record to another path, rewriting it there.

    A move always carries new text: SPEC-1 section 6 requires the archive
    voice, so the text at the destination is never the text at the source.
    """
    src: str
    dst: str
    text: str


Op = Write | Move


# --- Results (SPEC-3 section 7) ----------------------------------------

@dataclass(frozen=True)
class Applied:
    kind: str = field(default="applied", init=False)
    paths: tuple[str, ...] = ()
    inherited: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rejected:
    kind: str = field(default="rejected", init=False)
    failures: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class TxnError:
    kind: str = field(default="error", init=False)
    message: str = ""


Result = Applied | Rejected | TxnError


# --- Path rules --------------------------------------------------------

def _is_governed_path(rel: str) -> bool:
    """True when the validator would see a file at this path.

    A write the validator cannot see is an ungoverned write wearing a
    governed path, so the transaction refuses one rather than landing a
    file no check will ever read.
    """
    if rel.startswith(f"{DOCS}/spec") and rel.endswith(".md") and "/" not in rel[5:]:
        return True
    return rel.endswith(".md") and rel.startswith(GOVERNED_DIRS)


def _check_paths(root: Path, ops: Sequence[Op]) -> str | None:
    """The structural complaint about this operation set, or None.

    These are malformed calls, not rejected changes: nothing about the
    corpus would make them valid (SPEC-3 section 7).
    """
    if not ops:
        return "no operations"

    targets: list[str] = []
    sources: list[str] = []
    for op in ops:
        paths = [op.path] if isinstance(op, Write) else [op.src, op.dst]
        for rel in paths:
            if rel != rel.strip() or "\\" in rel or rel.startswith("/"):
                return f"path '{rel}' is not a clean root-relative posix path"
            if ".." in Path(rel).parts:
                return f"path '{rel}' escapes the corpus root"
            if not _is_governed_path(rel):
                return f"path '{rel}' is outside the governed layout"
        if isinstance(op, Write):
            targets.append(op.path)
        else:
            if op.src == op.dst:
                return f"move source and destination are the same path: '{op.src}'"
            if not (root / op.src).is_file():
                return f"move source '{op.src}' does not exist"
            targets.append(op.dst)
            sources.append(op.src)

    for rel, n in Counter(targets).items():
        if n > 1:
            return f"path '{rel}' is written by {n} operations"
    clash = set(sources) & set(targets)
    if clash:
        return f"path '{sorted(clash)[0]}' is both moved away and written"
    return None


def _links_escaping(root: Path, rel: str, text: str) -> list[str]:
    """The link targets in `text`, read as the file at `rel`, that resolve
    outside `docs/`.

    The same text C4 reads: a link inside a fence or a code span is an
    example and stops nothing (docs-engine-02).
    """
    docs = (root / DOCS).resolve()
    parent = (root / rel).parent
    out: list[str] = []
    for target in LINK_RE.findall(strip_code(text)):
        target = target.split("#")[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        resolved = (parent / target).resolve()
        if docs not in resolved.parents and resolved != docs:
            out.append(target)
    return out


def _escaping_links(root: Path) -> list[str]:
    """Governed links whose target resolves outside `docs/`.

    The copy holds `docs/` alone, so such a link would fail C4 on the copy
    and nowhere else — a rejection the change did not earn. No governed
    record has one today; this reports rather than guesses if that changes.
    """
    out: list[str] = []
    for path in governed_files(root):
        text = read_governed(path)
        if text is None:
            # The validator names it under C2 on both sides of the gate;
            # it holds no link this can read, and it stops no change.
            continue
        rel = path.relative_to(root).as_posix()
        out.extend(f"{rel} -> {target}" for target in _links_escaping(root, rel, text))
    return out


def _escaping_in_ops(root: Path, ops: Sequence[Op]) -> str | None:
    """The complaint for a change whose own text links outside `docs/`.

    C4 on the copy would call such a link missing when it exists live,
    and an agent reading that would break a working link to satisfy it
    (docs-engine-04). The real constraint is named instead.
    """
    for op in ops:
        rel = op.path if isinstance(op, Write) else op.dst
        for target in _links_escaping(root, rel, op.text):
            return (
                f"path '{rel}' links to '{target}', which resolves outside "
                f"{DOCS}/. The transaction validates a copy of {DOCS}/ alone "
                "and cannot check a link beyond it, so a record written by a "
                f"verb links only inside {DOCS}/."
            )
    return None


def _archive_overwrite(
    root: Path, baseline: dict[str, dict[str, str]], ops: Sequence[Op]
) -> str | None:
    """The complaint for a change that would write over a file under
    `docs/archive/`, or None.

    No rule and no verb rewrites a file there: SPEC-1 sections 8.1 to 8.3
    each put a new file into the archive. A Write's path or a Move's
    destination that names a file on disk whose bytes decode, or a key
    git HEAD carries, is refused before anything is staged, so an
    uncommitted entry is never replaced with no git copy to restore
    (mcp-servers-03). A file there that does not decode holds no record
    a reader can see; a write at its path is the repair PLAN-0016 phase 3
    proved, and stays open.
    """
    for op in ops:
        rel = op.path if isinstance(op, Write) else op.dst
        if not rel.startswith(ARCHIVE):
            continue
        live = root / rel
        if live.is_file():
            if read_governed(live) is None:
                continue  # the repair case, whether or not HEAD carries it
            held = "already holds a file"
        elif rel in baseline:
            # git_head_baseline keys the files it finds on disk, so this
            # branch answers a caller that passes a baseline of its own.
            held = "is carried by git HEAD"
        else:
            continue
        return (
            f"path '{rel}' {held}, and a file under {ARCHIVE} is never "
            "written over: SPEC-1 section 8 puts a new file there (D-18 "
            "constraint 3). Choose a path no file holds, or make the change "
            "by a direct edit under SPEC-1 section 8."
        )
    return None


# --- Applying ----------------------------------------------------------

def _write_file(path: Path, text: str) -> None:
    """Write UTF-8 with LF endings, whatever the platform prefers.

    Tests monkeypatch this to inject a mid-transaction failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _perform(root: Path, ops: Sequence[Op]) -> None:
    """Apply every operation, or restore every touched path and re-raise."""
    touched: list[str] = []
    for op in ops:
        touched.extend([op.path] if isinstance(op, Write) else [op.src, op.dst])
    prior: dict[str, bytes | None] = {}
    for rel in touched:
        p = root / rel
        prior[rel] = p.read_bytes() if p.is_file() else None

    try:
        for op in ops:
            if isinstance(op, Write):
                _write_file(root / op.path, op.text)
            else:
                _write_file(root / op.dst, op.text)
                (root / op.src).unlink()
    except BaseException:
        for rel, blob in prior.items():
            p = root / rel
            if blob is None:
                if p.is_file():
                    p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(blob)
        raise


def _stage_copy(root: Path, tmp: Path, ops: Sequence[Op]) -> Path:
    """A copy of `docs/` with the change applied. The live tree is untouched."""
    copy = tmp / "corpus"
    shutil.copytree(root / DOCS, copy / DOCS)
    _perform(copy, ops)
    return copy


def _baseline_for_copy(
    baseline: dict[str, dict[str, str]], ops: Sequence[Op]
) -> dict[str, dict[str, str]]:
    """The baseline re-keyed for the copy, so a moved record keeps its past.

    C5 compares by path. Without this a retirement would look like a brand
    new file at the archive path, and the in-force-to-archived transition
    and the `id` behind it would go unchecked — the same silence the
    `--root` defect produced, arriving by another route.
    """
    moved = dict(baseline)
    for op in ops:
        if isinstance(op, Move) and op.src in baseline:
            if op.dst in baseline:
                # Re-keying here would hide the standing record's id from
                # C5. _archive_overwrite refuses such a move before this
                # runs, so a live call never reaches this line.
                raise ValueError(
                    f"move destination '{op.dst}' already has a baseline entry"
                )
            moved[op.dst] = baseline[op.src]
    return moved


def _rekey(lines: Sequence[str], ops: Sequence[Op]) -> list[str]:
    """`lines` with every Move's source path rewritten to its destination.

    Failure lines carry the path, so a moved record's inherited failure
    would vanish at the source and reappear at the destination, counted
    as added. The same mapping _baseline_for_copy applies to baseline
    keys applies here to the line's key: the path in the validator's
    `CHECK (name) <path>:` prefix. Message text is left as it is, so
    another record's line that quotes the moved path keeps it
    (docs-engine-03).
    """
    out = list(lines)
    for op in ops:
        if isinstance(op, Move):
            key = re.compile(rf"^(C\d \([^)]+\) ){re.escape(op.src)}: ")
            dst = op.dst
            out = [key.sub(lambda m: f"{m.group(1)}{dst}: ", line) for line in out]
    return out


def targets(ops: Sequence[Op]) -> tuple[str, ...]:
    """The paths `ops` would write, sorted: a Write's path, a Move's destination."""
    return tuple(sorted(op.path if isinstance(op, Write) else op.dst for op in ops))


def _take(lines: Sequence[str], budget: Counter) -> tuple[str, ...]:
    """The lines of `lines` drawn against `budget`, in order, each counted once.

    Failure lines are compared whole and can legitimately repeat, so the
    delta is a multiset difference rather than a set one.
    """
    left = Counter(budget)
    out: list[str] = []
    for line in lines:
        if left[line]:
            left[line] -= 1
            out.append(line)
    return tuple(out)


def apply_change(
    root: Path,
    ops: Sequence[Op],
    today: dt.date | None = None,
) -> Result:
    """Validate `ops` on a copy of the corpus, then land all of them or none.

    Returns Applied, Rejected or TxnError (SPEC-3 section 7). A Rejected
    or TxnError result has written nothing.
    """
    root = Path(root).resolve()
    complaint = _check_paths(root, ops)
    if complaint:
        return TxnError(message=complaint)

    escaping = _escaping_links(root)
    if escaping:
        return TxnError(
            message=(
                "governed links resolve outside docs/, which the validation "
                f"copy cannot reproduce: {', '.join(escaping)}"
            )
        )
    complaint = _escaping_in_ops(root, ops)
    if complaint:
        return TxnError(message=complaint)

    try:
        baseline = git_head_baseline(root)
    except BaselineUnavailable as exc:
        return TxnError(
            message=(
                f"{exc}; C5 cannot run, and D-18 constraint 2 requires "
                "C1-C7 on the copy"
            )
        )
    complaint = _archive_overwrite(root, baseline, ops)
    if complaint:
        return TxnError(message=complaint)

    today = today or dt.date.today()
    before = _rekey(validate(root, today, baseline=baseline), ops)

    with tempfile.TemporaryDirectory(prefix="doc-txn-") as tmpdir:
        copy = _stage_copy(root, Path(tmpdir), ops)
        after = validate(copy, today, baseline=_baseline_for_copy(baseline, ops))

    paths = targets(ops)
    added = _take(after, Counter(after) - Counter(before))
    if added:
        return Rejected(
            failures=added,
            paths=paths,
            reason=f"the change adds {len(added)} validator failure(s)",
        )

    _perform(root, ops)
    return Applied(paths=paths, inherited=_take(after, Counter(before)))
