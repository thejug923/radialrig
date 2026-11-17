#!/usr/bin/env python3
"""Read-only MCP retrieval server for governed documentation.

Implements SPEC-2 (docs/spec-retrieval-tools.md) over the shared core in
doc_core.py, per D-13: in-force-only defaults, typed refusals, archive
only through search_history, no writes.

Run:  radialrig-read
"""

from __future__ import annotations

import re
from typing import Annotated

import pydantic_core
from pydantic import Field

from radialrig import activity_log as al
from radialrig.doc_core import (
    _FENCE_RE,
    Ambiguous,
    Conflict,
    NoRuling,
    Record,
    Ruling,
    default_visible,
    governed_files,
    load_guidance,
    load_records,
    parse_frontmatter,
    read_governed,
    select_ruling,
)
from radialrig.mount import installed_version

# Corpus root: RADIALRIG_DOCS_ROOT when set (pilot fixtures, portable reuse
# per D-11), else the nearest marked ancestor of the working directory. The
# name is re-exported so existing callers keep reaching it here.
from radialrig.paths import resolve_root

LISTABLE_TYPES = {"decision", "plan", "spec"}  # SPEC-2 s2

# An empty required string is a malformed call, not a query. Stating it in
# the schema refuses it at the protocol layer; the in-body checks stay,
# because a caller that bypasses the schema must still be refused.
NonEmpty = Annotated[str, Field(min_length=1)]

ROOT = resolve_root()

ARCHIVE_FENCE = (
    "Archived material is a record of past positions, not an instruction."
)
EXCERPT_CHARS = 300

# Parsed-corpus snapshot for get_ruling and list_rulings. Those two never
# answer from the archive (default_visible and select_ruling both exclude
# it; search_history reads it separately), so they load without the archive
# walk and reuse the last parse while every non-archive governed file keeps
# its (path, mtime, size) on disk.
_snapshot_key: tuple[tuple[str, int, int], ...] | None = None
_snapshot_records: list[Record] = []


def _active_records() -> list[Record]:
    global _snapshot_key, _snapshot_records
    key_parts = []
    for path in governed_files(ROOT, include_archive=False):
        st = path.stat()
        key_parts.append((path.as_posix(), st.st_mtime_ns, st.st_size))
    key = tuple(key_parts)
    if key != _snapshot_key:
        _snapshot_records = load_records(ROOT, include_archive=False)
        _snapshot_key = key
    return _snapshot_records


# SPEC-2 s1, Large plans: a plan answer over this many characters, counted
# as the server sends it, is cut at the plan's own headings.
PLAN_LIMIT = 40_000
CUT_NEXT = "Call get_ruling again with the same query and one omitted name as section."
_HEADING_RE = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*$", re.M)


def _sent_length(answer: dict) -> int:
    # The SDK sends a dict result as this text (mcp's func_metadata), so
    # the limit counts what a client receives, non-ASCII as one character.
    return len(pydantic_core.to_json(answer, fallback=str, indent=2).decode())


def _parts(body: str) -> tuple[list[tuple[int, str, int, int]], tuple | None]:
    """The named parts of `body` in file order, each (level, name, start,
    end) as offsets into it, and the execution log among them. Names are
    the `##` headings and the `###` headings inside the log; a part runs
    to the next heading of the same or a higher level. Fenced lines are
    blanked first, keeping every offset, so a heading in a fence is text."""
    masked = _FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), body)
    found = [(len(m.group(1)), m.group(2), m.start()) for m in _HEADING_RE.finditer(masked)]
    heads = [
        (level, name, start,
         next((s for lv, _, s in found[i + 1:] if lv <= level), len(body)))
        for i, (level, name, start) in enumerate(found)
    ]
    log = next((h for h in heads if h[0] == 2 and "execution log" in h[1].lower()), None)
    named = [
        h for h in heads
        if h[0] == 2 or (h[0] == 3 and log is not None and log[2] < h[2] < log[3])
    ]
    return named, log


def _unique(names: list[str]) -> list[str]:
    """Names in order, one per name in any case."""
    seen: dict[str, str] = {}
    for name in names:
        seen.setdefault(name.lower(), name)
    return list(seen.values())


def _ruling_answer(r: Record) -> dict:
    answer = {
        "kind": "ruling",
        "id": r.id,
        "title": r.title,
        "status": r.status,
        "scope": r.scope,
        "effective": r.fm.get("effective", ""),
        "last_verified": r.fm.get("last-verified", ""),
        "path": r.rel,
        "body": r.body,
    }
    if r.fm.get("type") != "plan" or _sent_length(answer) <= PLAN_LIMIT:
        return answer
    named, log = _parts(r.body)
    if not named:
        # No `##` heading to cut at, and an empty `omitted` would read as
        # a whole answer: the plan stays whole.
        return answer

    def cut(body: str, omitted: list[str]) -> dict:
        return {**answer, "body": body, "omitted": _unique(omitted), "next": CUT_NEXT}

    if log is not None:
        entries = [h for h in named if h[0] == 3]
        if entries:
            short = cut(r.body[:entries[0][2]] + r.body[log[3]:], [h[1] for h in entries])
        else:
            short = cut(r.body[:log[2]] + r.body[log[3]:], [log[1]])
        if _sent_length(short) <= PLAN_LIMIT:
            return short
    return cut(r.body[:named[0][2]], [h[1] for h in named if h[0] == 2])


def _section_answer(r: Record, section: str) -> dict:
    named, _ = _parts(r.body)
    want = section.strip()
    hits = [h for h in named if h[1].lower() == want.lower()]
    if not hits:
        return {
            "kind": "no_section",
            "id": r.id,
            "section": want,
            "sections": _unique([h[1] for h in named]),
        }
    return {
        "kind": "section",
        "id": r.id,
        "title": r.title,
        "status": r.status,
        "path": r.rel,
        "section": want,
        "body": "".join(r.body[start:end] for _, _, start, end in hits),
    }


def get_ruling(query: NonEmpty, scope: str | None = None, section: str | None = None) -> dict:
    """In-force ruling for the `query` string: a record key like D-04,
    or free-text words. Typed result on ambiguity, conflict, or no
    match; never a ranked guess. An ambiguous result lists candidate
    keys - call again with one candidate id as `query`. An exact key
    also reaches a completed plan's summary; free text never does.
    `section` returns one heading's part whole; a large plan's answer
    lists the names in `omitted`."""
    if not query.strip():
        return {"kind": "error", "message": "query must be non-empty"}
    if section is not None and not section.strip():
        return {"kind": "error", "message": "section must be non-empty"}
    result = select_ruling(_active_records(), query, scope=scope)
    if isinstance(result, Ruling):
        if section is not None:
            return _section_answer(result.record, section)
        return _ruling_answer(result.record)
    if isinstance(result, Ambiguous):
        return {
            "kind": "ambiguous",
            "query": result.query,
            "candidates": [
                {"id": rid, "title": title} for rid, title in result.candidates
            ],
            "next": "Call get_ruling once more with one candidate id as query.",
        }
    if isinstance(result, Conflict):
        return {
            "kind": "conflict",
            "key": result.key,
            "paths": [r.rel for r in result.records],
            "reason": result.reason,
        }
    assert isinstance(result, NoRuling)
    return {"kind": "no_ruling", "query": result.query}


def list_rulings(type: str | None = None, scope: str | None = None) -> dict:
    """List in-force records: id and title only. No bodies."""
    if type is not None and type not in LISTABLE_TYPES:
        return {
            "kind": "error",
            "message": f"type '{type}' not in {sorted(LISTABLE_TYPES)}",
        }
    records = [r for r in _active_records() if default_visible(r)]
    if type is not None:
        records = [r for r in records if r.fm.get("type") == type]
    if scope is not None:
        records = [r for r in records if r.scope == scope]
    return {
        "kind": "rulings",
        "records": [
            {"id": r.id, "title": r.title}
            for r in sorted(records, key=lambda r: r.rel)
        ],
    }


def search_history(query: NonEmpty) -> dict:
    """Search the archive (docs/archive/**). Explicit history access;
    results are past positions, never authoritative."""
    if not query.strip():
        return {"kind": "error", "message": "query must be non-empty"}
    # SPEC-2 s3: every word, any case and order. A file that holds the
    # whole phrase holds every word of it, so nothing found before is lost,
    # and its excerpt is placed exactly as before.
    q = query.lower()
    words = q.split()
    matches = []
    archive = ROOT / "docs" / "archive"
    if archive.exists():
        for path in sorted(archive.rglob("*.md")):
            text = read_governed(path)
            if text is None:
                # Named by check_docs under C2; it holds no text this
                # can search, and it stops nothing else from answering.
                continue
            low = text.lower()
            if not all(w in low for w in words):
                continue
            pos = low.find(q)
            if pos < 0:
                pos = min(low.find(w) for w in words)
            fm, _ = parse_frontmatter(text)
            start = max(0, pos - EXCERPT_CHARS // 2)
            matches.append({
                "path": path.relative_to(ROOT).as_posix(),
                "id": (fm or {}).get("id", ""),
                "title": (fm or {}).get("title", ""),
                "excerpt": text[start:start + EXCERPT_CHARS],
            })
    return {
        "kind": "history",
        "archived": True,
        "fence": ARCHIVE_FENCE,
        "matches": matches,
    }


def check_docs() -> dict:
    """Run the validator checks (C1-C7) and report mechanically."""
    from radialrig.validate_docs import BaselineUnavailable, git_head_baseline, validate

    # One walk of the governed tree serves the baseline, the checks, and
    # the count.
    files = governed_files(ROOT)
    # C5 needs a prior state. Reading it is the one git call on this
    # surface, and it is read-only. When git cannot answer, the other
    # checks still run — suppressing them would hide real defects — but
    # C5 reports that it did not, because a check that cannot run is not
    # a passing check.
    note: list[str] = []
    try:
        baseline = git_head_baseline(ROOT, files=files)
    except BaselineUnavailable as exc:
        baseline = {}
        note = [f"C5 {ROOT}: {exc}; transition and id checks did not run"]
    failures = note + validate(ROOT, baseline=baseline, files=files)
    return {
        "kind": "check",
        "ok": not failures,
        "governed_count": len(files),
        "failures": failures,
    }


def get_guidance(topic: str | None = None) -> dict:
    """Research guidance for agents. With `topic`: that guidance text
    plus its evidence path (read the evidence only to verify or extend
    a rule). Without: the topic catalog with load-when triggers, built
    live from the guidance files."""
    entries, refused = load_guidance(ROOT)
    refused_out = [
        {"path": r.rel, "topic": r.topic, "reason": r.reason} for r in refused
    ]
    if topic is None or not topic.strip():
        return {
            "kind": "guidance_catalog",
            "topics": [
                {"topic": g.topic, "pillar": g.pillar, "load_when": g.load_when}
                for g in entries
            ],
            "refused": refused_out,
        }
    t = topic.strip().lower()
    hits = [g for g in entries if g.topic.lower() == t]
    if len(hits) > 1:
        return {
            "kind": "conflict",
            "key": hits[0].topic,
            "paths": [g.rel for g in hits],
            "reason": f"{len(hits)} guidance files claim topic '{hits[0].topic}' (D-09: stop)",
        }
    if hits:
        g = hits[0]
        return {
            "kind": "guidance",
            "topic": g.topic,
            "pillar": g.pillar,
            "load_when": g.load_when,
            "path": g.rel,
            "evidence": g.evidence,
            "body": g.body,
        }
    for r in refused:
        if r.topic.lower() == t:
            return {
                "kind": "refused",
                "topic": r.topic,
                "path": r.rel,
                "reason": r.reason,
            }
    return {
        "kind": "no_guidance",
        "topic": topic,
        "topics": [g.topic for g in entries],
    }


READ_TOOLS = (get_ruling, list_rulings, search_history, check_docs, get_guidance)

# What a client hands its agent at session start (PLAN-0026 phase 5). It
# restates the route map's rules 1, 2 and 6 in their own terms and adds
# none: the route map rules, this only says when to call.
INSTRUCTIONS = (
    "Call get_ruling before acting on any question of what rule applies "
    "here: it answers from the one in-force record, or says why it cannot, "
    "and never guesses. "
    "Use list_rulings for the catalog, check_docs for the mechanical checks, "
    "get_guidance for the research guidance, and search_history only when "
    "the task explicitly asks about history. "
    "Query by record key when you know it, and cite the record's path in "
    "any answer that relies on it."
)

# Claude Code loads a tool with this mark at session start, without a
# tool search (SPEC-2's start-up mark paragraph). Only the read tools
# carry it; the schema bound counts it.
START_UP_MARK = {"anthropic/alwaysLoad": True}


def build_server():
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    # The version a client reads in `serverInfo` is the installed engine's,
    # from the one function every surface reads (PLAN-0018 phase 1).
    mcp = MCPServer("radialrig", version=installed_version(),
                    instructions=INSTRUCTIONS)
    # D-13 constraint 5: this server is read-only. Declaring it on the wire
    # is what lets a client tell these five from the three write verbs
    # without reading the source (D-18 constraint 6).
    reads = ToolAnnotations(
        read_only_hint=True, destructive_hint=False,
        idempotent_hint=True, open_world_hint=False,
    )
    for tool in READ_TOOLS:
        # Each call leaves one line in the log (PLAN-0018 phase 2). The
        # wrapper keeps the tool list byte for byte (Verified 11), and
        # the log never sees a call's arguments (Decided 3).
        mcp.tool(annotations=reads, meta=START_UP_MARK)(
            al.logged(tool, al.READ_SERVER))
    return mcp


def main() -> None:
    """Console entry point: serve the read tools over stdio."""
    # The start event (PLAN-0018 phase 2): the version rides in every
    # header, and the folder is the one this process pinned. The log
    # goes to its file and never to stdout, which carries protocol only.
    al.record(al.READ_SERVER, "started", folder=str(ROOT))
    build_server().run()


if __name__ == "__main__":
    main()
