#!/usr/bin/env python3
"""Write MCP server for governed documentation. On from the first session.

Implements SPEC-3 (docs/spec-authoring-tools.md) over the transaction core
in doc_txn.py, per D-18. Three verbs, one per lifecycle move in SPEC-1
section 8. Parsing and truth selection come from doc_core, shared with the
validator and the retrieval server (D-18 constraint 7).

**A mount registers this server beside the retrieval server** in the
project's `.mcp.json`, as `radialrig-write` (D-18 constraint 6, owner
ruling 2026-09-04). The retrieval server stays read-only. Capture goes off
when the owner removes that one entry; the seeded route map names the
command so it can come back.

Run standalone:  radialrig-write

Every verb stops at the working tree. No verb writes git; the two
permitted reads are named in SPEC-3 section 1. Every verb takes a
`summary`, one plain sentence saying what the ruling now says, and
echoes it back in the applied result. The agent shows that sentence to
the person and reads the diff, and nothing is committed until the person
says yes; `git checkout -- docs/` reverses a change nobody approved
(D-18 constraint 4, D-35 commitment 2).

The corpus root is RADIALRIG_DOCS_ROOT when set, else the nearest marked
ancestor of the working directory. A write server pointed elsewhere writes
elsewhere. That write still stops at the working tree, so a misdirected
one is a diff a person sees before any commit (D-18, Why).
"""

from __future__ import annotations

import datetime as dt
import re
import string
from dataclasses import asdict
from typing import Annotated, Literal

from pydantic import Field

from radialrig import activity_log as al
from radialrig import doc_txn as txn
from radialrig.doc_core import (
    NOT_UTF8,
    default_visible,
    load_records,
    parse_frontmatter,
    read_governed,
)
from radialrig.mount import installed_version

# Re-exported so existing callers keep reaching the resolver here.
from radialrig.paths import resolve_root
from radialrig.validate_docs import git_head_frontmatter

ARCHIVE_DIR = "docs/archive"

# SPEC-1 8.1 names an archive entry for the record and the month, with an
# optional letter when the month already holds one.
ARCHIVE_ENTRY_RE = re.compile(r"^(?P<stem>.+-history-\d{4}-\d{2})(?:-[a-z])?\.md$")

# An empty path, id or record text is a malformed call, not a change.
# Stating it in the schema refuses it before the verb runs; the in-body
# checks stay, because a caller that bypasses the schema is still refused.
NonEmpty = Annotated[str, Field(min_length=1)]

# The sentence a person approves (D-35 commitment 2). The caller writes
# it (D-18 constraint 5); the verb checks its length and echoes it back.
# The schema carries no length bound on purpose: an empty or overlong
# sentence is a change that does not yet pass, so it comes back as
# `rejected` with the fix named, not as a validation error (SPEC-3
# section 7). The 300-character limit is the plan's (PLAN-0012 phase 2).
SUMMARY_MAX = 300
Summary = Annotated[str, Field(description=(
    "One plain sentence saying what the ruling now says; the person "
    "approves it."
))]

ROOT = resolve_root()

# The date every verb reasons with: C6 on the validation copy, and the
# 8.5 stamp check. None means the real date, read at each call and never
# frozen at import; a test pins it the way test_doc_txn pins its own,
# so the suite does not go red on a calendar date (PLAN-0016 phase 4).
TODAY: dt.date | None = None


# --- Shared helpers ----------------------------------------------------

def _today() -> dt.date:
    return TODAY or dt.date.today()


def _error(message: str) -> dict:
    return {"kind": "error", "message": message}


def _result(outcome: txn.Result) -> dict:
    """A transaction result as the JSON object SPEC-3 section 7 defines."""
    out = asdict(outcome)
    out["kind"] = outcome.kind
    for key in ("paths", "failures", "inherited"):
        if key in out:
            out[key] = list(out[key])
    return out


def _land(summary: str, ops: list[txn.Op]) -> dict:
    """Refuse a summary the person cannot approve, else apply and echo it.

    Runs after every malformed-call check, so a bad summary on a bad call
    still reports the malformation first (SPEC-3 section 7). Surrounding
    whitespace is trimmed before the sentence is measured or echoed.
    """
    sentence = summary.strip()
    paths = txn.targets(ops)
    if not sentence:
        return _result(txn.Rejected(paths=paths, reason=(
            "summary is empty. Send one plain sentence saying what the "
            "ruling now says; the person approves that sentence."
        )))
    if len(sentence) > SUMMARY_MAX:
        return _result(txn.Rejected(paths=paths, reason=(
            f"summary is {len(sentence)} characters and the limit is "
            f"{SUMMARY_MAX}. Cut it to one plain sentence saying what the "
            "ruling now says."
        )))
    out = _result(txn.apply_change(ROOT, ops, today=_today()))
    if out["kind"] == "applied":
        out["summary"] = sentence
    return out


def _archive_taken(rel: str) -> str:
    """The refusal for an archive path a file already holds.

    SPEC-1 8.1 names the entry for the record and the month, so a second
    amendment in one month lands on the same path. An archived position
    is never overwritten (D-18), so the verb refuses before any write and
    names the next free path in the scheme: `-b`, then `-c`, and so on.
    """
    taken = (
        f"archive path '{rel}' already holds an entry, and an archived "
        "position is never overwritten (SPEC-1 8.1)."
    )
    m = ARCHIVE_ENTRY_RE.match(rel)
    if m:
        for letter in string.ascii_lowercase[1:]:
            candidate = f"{m.group('stem')}-{letter}.md"
            if not (ROOT / candidate).exists():
                return f"{taken} The next free path is '{candidate}'."
    return f"{taken} Choose a path no file holds."


def _path_taken(rel: str) -> str | None:
    """The refusal for a new record's path that a decodable file already
    holds, anywhere under docs/, or None.

    SPEC-1 8.4 only ever creates a new file, and a proposal or a successor
    used to replace whatever stood at its path; uncommitted, that text was
    then nowhere (mcp-servers-03). A file whose bytes do not decode holds
    no record a reader can see: a write at its path is the repair, and is
    not refused.
    """
    live = ROOT / rel
    if live.is_file() and read_governed(live) is not None:
        return (
            f"path '{rel}' already holds a file, and a new record only ever "
            "creates one (SPEC-1 8.4). Choose a path no file holds, or amend "
            "the record that stands there."
        )
    return None


def _id_changed(rid: str, text: str) -> str | None:
    """The refusal for a text whose frontmatter id is not `rid`, or None.

    No verb changes an id (SPEC-3 section 1 guarantee 5; D-06). C5 saw a
    rename only on a path git HEAD carries, so an uncommitted record could
    be renamed on the way in (mcp-servers-04). The verb compares first.
    """
    fm, _ = parse_frontmatter(text)
    submitted = (fm or {}).get("id", "")
    if submitted == rid:
        return None
    return (
        f"text carries id '{submitted}', not '{rid}'. No verb changes an id "
        "(SPEC-1 8.1, D-06); a new question takes a new record (8.4)."
    )


def _live_record(rid: str):
    """The in-force record holding `rid`, or None.

    Archived files are invisible here, so no verb can amend or retire a
    record that has already left (D-04, D-13 constraint 3).
    """
    for record in load_records(ROOT):
        if record.id == rid and default_visible(record):
            return record
    return None


def _fm_lines(text: str) -> tuple[list[str], list[str]]:
    """(frontmatter lines, body lines), frontmatter without its fences."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], lines
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:i], lines[i + 1:]
    return [], lines


def _set_field(text: str, field: str, value: str) -> str:
    """Return `text` with one frontmatter field set. Adds nothing new."""
    out, seen = [], False
    fence = 0
    for line in text.splitlines():
        if line.strip() == "---" and fence < 2:
            fence += 1
        elif fence == 1 and line.startswith(f"{field}:") and not seen:
            line = f"{field}: {value}"
            seen = True
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _section(body_lines: list[str], heading: str) -> list[str] | None:
    """The lines under `## heading`, to the next `## `. None when absent."""
    start = None
    for i, line in enumerate(body_lines):
        if line.strip() == f"## {heading}":
            start = i + 1
            break
    if start is None:
        return None
    out = []
    for line in body_lines[start:]:
        if line.startswith("## "):
            break
        out.append(line)
    return out


def _dep_ids(record) -> list[str]:
    """The ids a record names in `depends-on`. Absent means none."""
    raw = record.fm.get("depends-on", "")
    return [part.strip() for part in raw.split(",") if part.strip() not in ("", "none")]


def _dependents(rid: str) -> set[str]:
    """Every in-force record that must be read when `rid` changes.

    SPEC-1 8.5 reads the whole chain, direct and transitive (gap G7). This
    walks it from the declarations the dependents themselves carry, so the
    verb identifies the set rather than trusting the caller to.
    """
    visible = [r for r in load_records(ROOT) if default_visible(r)]
    by_dep: dict[str, list] = {}
    for record in visible:
        for dep in _dep_ids(record):
            by_dep.setdefault(dep, []).append(record)

    out: set[str] = set()
    seen, queue = {rid}, [rid]
    while queue:
        for record in by_dep.get(queue.pop(), []):
            out.add(record.rel)
            if record.id not in seen:
                seen.add(record.id)
                queue.append(record.id)
    return out


def _missing_propagation(rid: str, rel: str, entries: list[dict] | None) -> str | None:
    """The complaint when the caller left a dependent out of an 8.5 pass."""
    missing = sorted(_dependents(rid) - {rel} - {e.get("path", "") for e in entries or []})
    if not missing:
        return None
    return (
        f"propagation is incomplete. Changing '{rid}' requires reading "
        f"{', '.join(missing)}, which the call does not touch. SPEC-1 8.5 "
        "reads the whole depends-on chain and stamps last-verified on every "
        "record read, whether or not its text changed."
    )


def _propagation_ops(entries: list[dict] | None) -> tuple[list[txn.Write], str | None]:
    """Propagation edits as Write ops, or the complaint that stops them.

    SPEC-3 section 4.3, from D-18 constraint 8: propagation may edit a
    dependent's wording and must not change what it rules. The mechanical
    form of "what it rules" is the `## Ruling` section, byte for byte.
    """
    ops: list[txn.Write] = []
    for entry in entries or []:
        rel, text = entry.get("path", ""), entry.get("text", "")
        if not rel or not text:
            return [], "each propagate entry needs a path and text"
        current = ROOT / rel
        if not current.is_file():
            return [], f"propagate target '{rel}' does not exist"

        current_text = read_governed(current)
        if current_text is None:
            return [], (
                f"propagate target '{rel}' {NOT_UTF8}, so what it rules cannot "
                "be read; check_docs names it, and a propose_record at that "
                "path replaces it"
            )
        _, was_body = _fm_lines(current_text)
        _, now_body = _fm_lines(text)
        was, now = _section(was_body, "Ruling"), _section(now_body, "Ruling")
        if was is None:
            if was_body != now_body:
                return [], (
                    f"'{rel}' has no '## Ruling' section, so propagation cannot "
                    "edit its body; amend it in position mode instead"
                )
        elif was != now:
            return [], (
                f"propagation would change what '{rel}' rules. Propagation may "
                "edit wording only (SPEC-1 8.5). Amend that record in position "
                "mode, with its own archive entry."
            )

        # 8.5's stamp is the mark of the read, and the mark is the review
        # date. An entry that leaves last-verified at an earlier date
        # recorded no review, so the set was covered but the mark was not
        # (review finding 2026-08-16). A date already at today is the mark
        # today's review would leave, so it stands unchanged: demanding it
        # move would deadlock with _missing_propagation on a dependent
        # verified the same day (finding 2026-08-18).
        was_fm, _ = parse_frontmatter(current_text)
        now_fm, _ = parse_frontmatter(text)
        now_lv = (now_fm or {}).get("last-verified")
        if (was_fm or {}).get("last-verified") == now_lv and (
            now_lv != _today().isoformat()
        ):
            return [], (
                f"propagation left no mark on '{rel}': SPEC-1 8.5 stamps "
                "last-verified with the review date on every record read, "
                "whether or not its text changed"
            )
        ops.append(txn.Write(rel, text))
    return ops, None


# --- Server ------------------------------------------------------------

def propose_record(path: NonEmpty, text: NonEmpty, summary: Summary) -> dict:
    """Add a record under a new id (SPEC-1 8.4). Whole file text.

    Rejects an id any file already holds, in force or archived.
    """
    fm, _ = parse_frontmatter(text)
    if fm is None or not fm.get("id"):
        return _error("text has no parseable frontmatter with an id")
    rid = fm["id"]
    for record in load_records(ROOT):
        if record.id == rid:
            return _error(
                f"id '{rid}' is already held by {record.rel}. An id is never "
                "reused (D-06). Use a new id, or amend that record."
            )
    taken = _path_taken(path)
    if taken:
        return _error(taken)
    return _land(summary, [txn.Write(path, text)])


def amend_ruling(
    id: NonEmpty,
    mode: Literal["position", "metadata"],
    text: NonEmpty,
    summary: Summary,
    archive: dict | None = None,
    propagate: list[dict] | None = None,
) -> dict:
    """Rewrite an in-force record, keeping its id.

    mode='position': the ruling changes (SPEC-1 8.1). Needs archive
    {path, text}; the verb sets that entry's last-verified itself.
    mode='metadata': a re-check (8.6). Only last-verified may differ.
    propagate: [{path, text}] dependents; wording only.
    """
    record = _live_record(id)
    if record is None:
        return _error(f"no in-force record holds id '{id}'")
    if mode not in ("position", "metadata"):
        return _error(f"mode must be 'position' or 'metadata', not '{mode}'")
    changed = _id_changed(id, text)
    if changed:
        return _error(changed)

    current = record.path.read_text(encoding="utf-8")
    if mode == "metadata":
        if archive or propagate:
            return _error(
                "metadata mode archives nothing and propagates nothing (8.6)"
            )
        was, now = current.splitlines(), text.splitlines()
        differing = [(a, b) for a, b in zip(was, now, strict=False) if a != b]
        if len(was) != len(now) or any(
            not a.startswith("last-verified:") for a, _ in differing
        ):
            return _error(
                "metadata mode permits a last-verified change and nothing "
                "else. A body or grade change is a ruling change: use "
                "position mode, which writes the archive entry (D-18, D-19)."
            )
        return _land(summary, [txn.Write(record.rel, text)])

    if not archive or not archive.get("path") or not archive.get("text"):
        return _error("position mode needs archive {path, text} (SPEC-1 8.1)")
    if (ROOT / archive["path"]).exists():
        return _error(_archive_taken(archive["path"]))

    # Constraint 9: the entry records when the archived position was last
    # checked while in force. Never the archival date, never the caller's,
    # and never a date an 8.5 stamp moved in this same working tree (G18).
    # git HEAD is the last state the owner reviewed, so it is the only
    # copy of the record no uncommitted stamp can have touched. A record
    # HEAD does not carry falls back to the working tree, which is all
    # there is.
    prior_fm = git_head_frontmatter(ROOT, record.rel)
    if prior_fm is None:
        prior_fm, _ = parse_frontmatter(current)
    entry = _set_field(
        archive["text"], "last-verified", (prior_fm or {}).get("last-verified", ""),
    )
    complaint = _missing_propagation(id, record.rel, propagate)
    if complaint:
        return _error(complaint)
    extra, complaint = _propagation_ops(propagate)
    if complaint:
        return _error(complaint)
    ops = [txn.Write(record.rel, text), txn.Write(archive["path"], entry), *extra]
    return _land(summary, ops)


def retire_ruling(
    id: NonEmpty,
    text: NonEmpty,
    summary: Summary,
    successor: dict | None = None,
    propagate: list[dict] | None = None,
) -> dict:
    """Move a record to docs/archive/, past tense (SPEC-1 8.2, 8.3).

    successor {path, text}: the new record taking the question (8.3).
    Both files land together or neither does.
    """
    record = _live_record(id)
    if record is None:
        return _error(f"no in-force record holds id '{id}'")
    changed = _id_changed(id, text)
    if changed:
        return _error(changed)

    # SPEC-1 8.2 and 8.3 fix the destination to the record's own filename,
    # so no -b name is open to this move. A same-named archive file is the
    # transaction's refusal, which names the standing file and the direct
    # edit under SPEC-1 section 8.
    dst = f"{ARCHIVE_DIR}/{record.path.name}"
    ops: list[txn.Op] = [txn.Move(record.rel, dst, text)]
    if successor:
        if not successor.get("path") or not successor.get("text"):
            return _error("successor needs a path and text")
        new_fm, _ = parse_frontmatter(successor["text"])
        new_id = (new_fm or {}).get("id", "")
        if not new_id:
            return _error("successor text has no id")
        if new_id == id:
            return _error(
                f"successor must take a new id, not '{id}'. A change that "
                "keeps the id is an 8.1 amendment (D-06)."
            )
        retired_fm, _ = parse_frontmatter(text)
        if (retired_fm or {}).get("replaced-by") != new_id:
            return _error(
                f"the retired record must set replaced-by to '{new_id}' "
                "(SPEC-1 8.3 step 2)"
            )
        taken = _path_taken(successor["path"])
        if taken:
            return _error(taken)
        ops.append(txn.Write(successor["path"], successor["text"]))
    complaint = _missing_propagation(id, record.rel, propagate)
    if complaint:
        return _error(complaint)
    extra, complaint = _propagation_ops(propagate)
    if complaint:
        return _error(complaint)
    return _land(summary, [*ops, *extra])


VERBS = (propose_record, amend_ruling, retire_ruling)

# The one argument of each verb the log may hold: its target (PLAN-0018
# Decided 3). Never the text, the summary, the archive or a propagation.
TARGETS = {"propose_record": "path", "amend_ruling": "id", "retire_ruling": "id"}


def build_server():
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    # The version a client reads in `serverInfo` is the installed engine's,
    # from the one function every surface reads (PLAN-0018 phase 1).
    mcp = MCPServer("radialrig-write", version=installed_version())
    # Not read-only, and not destructive: no verb deletes a record and the
    # working tree is where every write stops (D-18 constraints 1 and 4).
    writes = ToolAnnotations(
        read_only_hint=False, destructive_hint=False,
        idempotent_hint=False, open_world_hint=False,
    )
    for verb in VERBS:
        # Each call leaves one line in the log, naming its target and no
        # other argument (PLAN-0018 phase 2). The wrapper keeps the tool
        # list byte for byte (Verified 11).
        mcp.tool(annotations=writes)(
            al.logged(verb, al.WRITE_SERVER, target=TARGETS.get(verb.__name__)))
    return mcp


def main() -> None:
    """Console entry point: serve the write verbs over stdio."""
    # The start event (PLAN-0018 phase 2). The log goes to its file and
    # never to stdout, which carries protocol only.
    al.record(al.WRITE_SERVER, "started", folder=str(ROOT))
    build_server().run()


if __name__ == "__main__":
    main()
