"""Shared parsing, status, and authority logic for governed documentation.

Two consumers import this module: validate_docs.py (mechanical
checks) and the PLAN-0002 retrieval server. One implementation of truth
selection, per D-13 constraint 4 — two implementations is a drift defect.

Authority order (D-09, SPEC-1 s7): correct scope, then status in-force,
then replaced-by links. A remaining tie is a typed conflict, never a
silent winner. Selection never ranks and never returns a best guess
(PLAN-0002 invariant 3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from radialrig.paths import packaged_research

REQUIRED_FIELDS = [
    "id", "title", "type", "status", "grade", "owner", "scope",
    "effective", "last-verified", "supersedes", "replaced-by",
    "depends-on",
]
STATUSES = {"draft", "in-force", "completed", "archived"}
GRADES = {"decided", "verified", "assumed"}
TYPES = {"decision", "plan", "spec", "evidence"}
LEGAL_TRANSITIONS = {
    "draft": {"draft", "in-force", "archived"},
    "in-force": {"in-force", "completed", "archived"},
    "completed": {"completed", "archived"},
    "archived": {"archived"},
}
STALE_DAYS = 90
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

NONE_VALUES = ("", "none")

# The C2 failure text for a governed file whose bytes are not UTF-8.
NOT_UTF8 = "file is not valid UTF-8"


# A CommonMark fence: a run of three or more backticks or tildes, indented
# up to three spaces, closed by a line of the same character at least as
# long. A backtick opener's info string holds no backtick. An unclosed
# fence is left as it is, so its lines are read as text.
_FENCE_RE = re.compile(
    r"^ {0,3}(?:(`{3,})[^`\n]*|(~{3,})[^\n]*)\n"
    r".*?"
    r"^ {0,3}(?:\1`*|\2~*)[ \t]*$",
    re.M | re.S,
)

# An inline code span: a run of N backticks closed by a run of exactly N,
# on one line. A shorter or longer run inside is content.
_SPAN_RE = re.compile(r"(?<!`)(`+)(.+?)(?<!`)\1(?!`)")


def strip_code(text: str) -> str:
    """`text` with fenced blocks and inline code spans removed.

    A link inside a fence or a code span is an example, not a link. C4 and
    the transaction's link scan both read this text, so one illustrative
    link cannot pass the validator and stop every verb (docs-engine-02).
    """
    return _SPAN_RE.sub("", _FENCE_RE.sub("", text))


def read_governed(path: Path) -> str | None:
    """The text of one governed file, or None when its bytes are not UTF-8.

    One byte from another encoding must not stop every reader of the
    corpus. Every reader takes this one road: the loader skips the file,
    the validator names it under C2, the verbs answer from the files that
    decode, and the file is repaired by a write that replaces its bytes.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def governed_files(root: Path, include_archive: bool = True) -> list[Path]:
    files: list[Path] = []
    docs = root / "docs"
    if docs.exists():
        files.extend(sorted(docs.glob("spec*.md")))
    subs = ("decisions", "plans", "archive") if include_archive else ("decisions", "plans")
    for sub in subs:
        d = docs / sub
        if d.exists():
            files.extend(sorted(d.rglob("*.md")))
    return files


def parse_frontmatter(text: str) -> tuple[dict[str, str] | None, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    fm: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return fm, "\n".join(lines[i + 1:])
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"')
    return None, text


def first_body_line(body: str) -> str:
    for line in body.splitlines():
        if line.strip() and not line.strip().startswith(">"):
            return line.strip()
    return ""


def first_body_paragraph(body: str) -> str:
    """The first non-empty block of body text, whitespace collapsed.

    The head-of-file sentence wraps in practice — SPEC-1 sections 5 and 6
    print it wrapped — so a phrase or pointer test must read the paragraph
    rather than the line. Whether the status prefix opens the block stays
    a line test (PLAN-0003 Phase 1, gap G8).
    """
    block: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(">"):
            if block:
                break
            continue
        block.append(stripped)
    return " ".join(" ".join(block).split())


@dataclass
class Record:
    path: Path
    rel: str
    fm: dict[str, str]
    body: str

    @property
    def id(self) -> str:
        return self.fm.get("id", "")

    @property
    def title(self) -> str:
        return self.fm.get("title", "")

    @property
    def status(self) -> str:
        return self.fm.get("status", "")

    @property
    def scope(self) -> str:
        return self.fm.get("scope", "")

    @property
    def replaced_by(self) -> str:
        return self.fm.get("replaced-by", "none")


def load_records(root: Path, include_archive: bool = True) -> list[Record]:
    """Load every governed file that decodes and parses. A file that is
    not UTF-8, or that has no frontmatter, is the validator's problem
    (C2), not the loader's. A caller that only serves in-force material
    can skip the archive walk entirely."""
    records: list[Record] = []
    for path in governed_files(root, include_archive):
        rel = path.relative_to(root).as_posix()
        text = read_governed(path)
        if text is None:
            continue
        fm, body = parse_frontmatter(text)
        if fm is not None:
            records.append(Record(path=path, rel=rel, fm=fm, body=body))
    return records


# --- Typed selection results (D-09 in code) ----------------------------

@dataclass
class Ruling:
    record: Record


@dataclass
class Ambiguous:
    query: str
    candidates: list[tuple[str, str]]  # (key, title) pairs, sorted; never content


@dataclass
class Conflict:
    key: str
    records: list[Record]
    reason: str


@dataclass
class NoRuling:
    query: str


SelectionResult = Ruling | Ambiguous | Conflict | NoRuling

ARCHIVE_SEGMENT = "docs/archive/"


def default_visible(record: Record) -> bool:
    """Visibility rule for default retrieval: in-force status AND a path
    outside docs/archive/. The path test holds even when frontmatter is
    wrong, so a mislabeled archive file cannot leak (D-13 constraint 3)."""
    return record.status == "in-force" and ARCHIVE_SEGMENT not in record.rel


# --- Guidance catalog (D-32) -------------------------------------------

# Bound for an on-demand agent rule file, from the corpus's own
# documentation-bloat guidance (the SKILL.md rule). D-31 requires the
# bound; D-32 requires it enforced where the catalog is built.
GUIDANCE_LINE_BOUND = 500


@dataclass
class Guidance:
    path: Path
    rel: str
    topic: str
    pillar: str
    load_when: str
    # A repo-relative path; "" when the file names none, or when the
    # packaged corpus is answering and ships no file at that path.
    evidence: str
    body: str


@dataclass
class GuidanceRefusal:
    rel: str
    topic: str  # "" when the frontmatter never yielded one
    reason: str


def _guidance_source(root: Path) -> tuple[list[Path], Path | None, bool]:
    """(guidance files, the base their paths are reported against,
    whether that base is the packaged corpus).

    The live tree wins wherever one exists: on this repository the files
    under research/ hold authority and the packaged copies are generated
    output (D-32, D-07). A host with no research/ tree is answered from
    the copies shipped inside the installation, so get_guidance is not
    silent there.
    """
    live = root / "research"
    if live.is_dir():
        # rglob also matches a directory named guidance.md; only files
        # are guidance, and a directory here would crash the read.
        found = sorted(p for p in live.rglob("guidance.md") if p.is_file())
        if found:
            return found, root, False
    packaged = packaged_research()
    if packaged is None:
        return [], None, False
    return (sorted(p for p in packaged.rglob("guidance.md") if p.is_file()),
            packaged.parent, True)


def load_guidance(root: Path) -> tuple[list[Guidance], list[GuidanceRefusal]]:
    """Catalog of research/**/guidance.md, built live from the files.

    The files are the only source (D-32): no index or table is read.
    A file over GUIDANCE_LINE_BOUND, or without a topic and a load-when
    trigger in its frontmatter, is refused with a reason — never served
    and never silently dropped. Frontmatter is still parsed for an
    oversized file so the refusal can name its topic; the body of a
    refused file is never returned.
    """
    entries: list[Guidance] = []
    refused: list[GuidanceRefusal] = []
    paths, base, packaged = _guidance_source(root)
    if base is None:
        return entries, refused
    for path in paths:
        rel = path.relative_to(base).as_posix()
        text = path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        topic = (fm or {}).get("topic", "").strip()
        load_when = (fm or {}).get("load-when", "").strip()
        lines = len(text.splitlines())
        if lines > GUIDANCE_LINE_BOUND:
            refused.append(GuidanceRefusal(
                rel=rel, topic=topic,
                reason=f"{lines} lines over the {GUIDANCE_LINE_BOUND}-line bound (D-31)",
            ))
            continue
        if fm is None:
            refused.append(GuidanceRefusal(rel=rel, topic="", reason="no frontmatter"))
            continue
        if not topic or not load_when:
            refused.append(GuidanceRefusal(
                rel=rel, topic=topic,
                reason="frontmatter must carry topic and load-when (D-32)",
            ))
            continue
        evidence = fm.get("evidence", "").strip()
        if evidence and evidence.lower() not in NONE_VALUES:
            target = (path.parent / evidence).resolve()
            try:
                evidence = target.relative_to(base.resolve()).as_posix()
            except ValueError:
                pass  # points outside the corpus; keep the raw value
            # The installation ships guidance.md alone (sync_package_data
            # copies nothing else), so a pointer the packaged base cannot
            # resolve is served as "" rather than as a path no host holds
            # (packaged-data-01). A live tree keeps its pointer as written.
            if packaged and not target.is_file():
                evidence = ""
        else:
            evidence = ""
        entries.append(Guidance(
            path=path, rel=rel, topic=topic,
            pillar=fm.get("pillar", "").strip(),
            load_when=load_when, evidence=evidence, body=body,
        ))
    entries.sort(key=lambda g: (g.pillar, g.topic))
    return entries, refused


def select_ruling(
    records: list[Record], query: str, scope: str | None = None
) -> SelectionResult:
    """Resolve a query to exactly one in-force record, or a typed result.

    Resolution rules (PLAN-0002 Phase 1):
      1. Only default-visible records take part: status in-force and a
         path outside docs/archive/; scope filters first when given.
         One exception (D-26, SPEC-2 s1): rule 2's exact-key test also
         covers status completed outside the archive, so a closed plan's
         summary stays reachable by its key. Rules 3 and 3a do not.
      2. An exact key match (case-insensitive id) selects that key.
      3. Otherwise a case-insensitive substring match on id and title;
         exactly one matching key selects it, several return Ambiguous,
         none fall through to rule 3a. The key then covers ALL records
         holding it, not only those the query text matched.
      3a. Word-assisted fallback (D-16): every query word of four or
         more characters must appear in a record's id, title, or body.
         Matches return Ambiguous — keys and titles only, even for a
         single candidate. A body match is semantic assistance; it
         never selects content silently. No candidates: NoRuling.
      4. One key, several records (duplicate, or same id across scopes
         with no scope given): Conflict.
      5. An in-force record with replaced-by set is contradictory state:
         Conflict.

    Raises ValueError on an empty or whitespace query.
    """
    q = query.strip().lower()
    if not q:
        raise ValueError("query must be non-empty")
    in_force = [r for r in records if default_visible(r)]
    completed = [
        r for r in records
        if r.status == "completed" and ARCHIVE_SEGMENT not in r.rel
    ]
    if scope is not None:
        in_force = [r for r in in_force if r.scope == scope]
        completed = [r for r in completed if r.scope == scope]

    def candidate_pairs(hits: list[Record]) -> list[tuple[str, str]]:
        return sorted({(r.id, r.title) for r in hits})

    # Exact key reaches completed summaries too (D-26); text rules below
    # stay in-force-only, so a dead plan never answers a live question.
    matched = [r for r in in_force + completed if r.id.lower() == q]
    if not matched:
        hits = [r for r in in_force if q in r.id.lower() or q in r.title.lower()]
        keys = sorted({r.id for r in hits})
        if len(keys) > 1:
            return Ambiguous(query=query, candidates=candidate_pairs(hits))
        if not keys:
            words = [w for w in re.findall(r"[a-z0-9-]+", q) if len(w) >= 4]
            if words:
                # Build each record's haystack once, not once per word.
                assisted = []
                for r in in_force:
                    hay = f"{r.id}\n{r.title}\n{r.body}".lower()
                    if all(w in hay for w in words):
                        assisted.append(r)
                if assisted:
                    return Ambiguous(query=query, candidates=candidate_pairs(assisted))
            return NoRuling(query=query)
        matched = [r for r in in_force if r.id == keys[0]]

    key = matched[0].id
    if len(matched) > 1:
        scopes = {r.scope for r in matched}
        reason = (
            f"{len(matched)} governing records for '{key}' across scopes "
            f"{sorted(scopes)}; pass a scope to disambiguate"
            if len(scopes) > 1
            else f"{len(matched)} governing records for '{key}' in one scope (D-01 defect)"
        )
        return Conflict(key=key, records=matched, reason=reason)

    record = matched[0]
    if record.replaced_by not in NONE_VALUES:
        return Conflict(
            key=key,
            records=[record],
            reason=(
                f"{record.status} record '{key}' names successor "
                f"'{record.replaced_by}'; contradictory state (D-09: stop)"
            ),
        )
    return Ruling(record=record)
