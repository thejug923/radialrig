"""Verb tests for the authoring server (SPEC-3, PLAN-0003 Phase 3).

Run:  uv run --with pytest --with "mcp>=2,<3" pytest src/radialrig/tests/test_authoring_server.py -q

The three verbs run end to end against a fixture corpus. Every verb is
checked for git isolation: HEAD unmoved, index empty, change present in
the working tree only (D-18 constraint 4).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import subprocess
from pathlib import Path

import pytest

from radialrig import authoring_server as a
from radialrig import doc_txn as txn
from radialrig import validate_docs as v
from radialrig.doc_core import parse_frontmatter

SCHEMA_BUDGET = 3000  # raised from 2,500 by owner ruling 2026-09-05 (SPEC-3)

# The sentence a person approves (PLAN-0012 phase 2). Every call carries one.
S = "The test record now rules what the test needs."

# The date the verbs reason with here, pinned as test_doc_txn pins its own:
# every fixture date below is measured against this one, never the clock.
TODAY = dt.date(2026, 8, 15)


def record(
    rid: str = "D-90",
    status: str = "in-force",
    grade: str = "decided",
    last_verified: str = "2026-08-01",
    replaced_by: str = "none",
    depends_on: str | None = None,
    first_line: str | None = None,
    ruling: str = "Test.",
    tail: str = "",
) -> str:
    fields = {
        "id": rid, "title": "Test record", "type": "decision",
        "status": status, "grade": grade, "owner": "tester",
        "scope": "test scope", "effective": "2026-08-01",
        "last-verified": last_verified,
        "supersedes": "none", "replaced-by": replaced_by,
        "depends-on": "none",
    }
    if depends_on:
        fields["depends-on"] = depends_on
    fm = "\n".join(f"{k}: {v}" for k, v in fields.items())
    first = first_line if first_line is not None else f"**Status: {status}.**"
    return f"---\n{fm}\n---\n\n{first}\n\n## Ruling\n\n{ruling}\n{tail}"


def archived_text(rid: str, replaced_by: str = "none", last_verified: str = "2026-08-01") -> str:
    head = (
        "**Status: archived.** This is a record of a past position, not an\n"
        "instruction."
    )
    if replaced_by != "none":
        head += f" The current ruling is {replaced_by}."
    return record(
        rid=rid, status="archived", first_line=head,
        replaced_by=replaced_by, last_verified=last_verified,
    )


@pytest.fixture()
def corpus(tmp_path: Path, monkeypatch) -> Path:
    """D-90 in force, D-91 depending on it. Committed."""
    (tmp_path / "docs" / "decisions").mkdir(parents=True)
    (tmp_path / "docs" / "archive").mkdir(parents=True)
    write(tmp_path, "docs/decisions/D-90-test.md", record())
    write(
        tmp_path, "docs/decisions/D-91-dependent.md",
        record(
            rid="D-91", depends_on="D-90", ruling="Depends on D-90.",
            tail="\n## Why\n\nBecause D-90 says so.\n",
        ),
    )
    for args in (
        ["init", "-q", "-b", "master"], ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setattr(a, "ROOT", tmp_path)
    monkeypatch.setattr(a, "TODAY", TODAY)
    return tmp_path


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def isolation(root: Path) -> tuple[str, str]:
    """(HEAD, staged paths). Both must be unchanged by every verb."""
    return git(root, "rev-parse", "HEAD"), git(root, "diff", "--cached", "--name-only")


def read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def fm(root: Path, rel: str) -> dict:
    parsed, _ = parse_frontmatter(read(root, rel))
    return parsed or {}


def blobs(root: Path) -> dict[str, bytes]:
    """Every byte under docs/, so a refusal can be shown to have moved none."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted((root / "docs").rglob("*")) if p.is_file()
    }


# --- propose_record (SPEC-1 8.4) ---------------------------------------

def test_propose_record_adds_a_record(corpus):
    before = isolation(corpus)
    out = a.propose_record("docs/decisions/D-92-new.md", record(rid="D-92"), S)
    assert out["kind"] == "applied", out
    assert fm(corpus, "docs/decisions/D-92-new.md")["id"] == "D-92"
    assert isolation(corpus) == before, "a verb touched git"


def test_propose_record_refuses_an_id_in_force(corpus):
    out = a.propose_record("docs/decisions/D-90-again.md", record(rid="D-90"), S)
    assert out["kind"] == "error"
    assert "D-90-test.md" in out["message"]
    assert not (corpus / "docs/decisions/D-90-again.md").exists()


def test_propose_record_refuses_an_id_held_by_an_archived_file(corpus):
    """C1 sees in-force duplicates only. The verb tests the wider rule:
    at most one file holds a given id, and an id is never reused."""
    write(corpus, "docs/archive/D-70-old.md", archived_text("D-70"))
    out = a.propose_record("docs/decisions/D-70-again.md", record(rid="D-70"), S)
    assert out["kind"] == "error"
    assert "never reused" in out["message"]


def test_propose_record_rejects_an_invalid_record(corpus):
    bad = record(rid="D-92", first_line="## Ruling")  # C3
    out = a.propose_record("docs/decisions/D-92-new.md", bad, S)
    assert out["kind"] == "rejected"
    assert any(f.startswith("C3") for f in out["failures"])
    assert not (corpus / "docs/decisions/D-92-new.md").exists()


def test_propose_record_refuses_a_path_a_decodable_file_holds(corpus):
    # mcp-servers-03: the verb refused a held id and never a held path, so
    # a second proposal at the path of an uncommitted first one replaced
    # it, and the text existed nowhere. SPEC-1 8.4 only ever creates a new
    # file: a path a decodable file holds is refused, naming the file.
    path = "docs/decisions/D-92-new.md"
    first = a.propose_record(path, record(rid="D-92"), S)
    assert first["kind"] == "applied", first
    before = blobs(corpus)

    second = a.propose_record(path, record(rid="D-93"), S)
    assert second["kind"] == "error", second
    assert path in second["message"]
    assert blobs(corpus) == before, "a refused proposal moved a byte"
    assert fm(corpus, path)["id"] == "D-92"

    committed = a.propose_record("docs/decisions/D-90-test.md", record(rid="D-93"), S)
    assert committed["kind"] == "error", committed
    assert "docs/decisions/D-90-test.md" in committed["message"]
    assert blobs(corpus) == before


# --- amend_ruling, position mode (SPEC-1 8.1) --------------------------

ARCHIVE_REL = "docs/archive/D-90-history-2026-08.md"


def dep_entry(last_verified: str = "2026-08-14", why: str = "Because D-90 says so.") -> dict:
    """D-91 read under 8.5, its ruling untouched. Every amend of D-90 needs
    one of these now: the verb walks the chain and refuses without it."""
    return {
        "path": DEP,
        "text": record(
            rid="D-91", depends_on="D-90", last_verified=last_verified,
            ruling="Depends on D-90.", tail=f"\n## Why\n\n{why}\n",
        ),
    }


def amend_position(corpus, **kw):
    kw.setdefault("propagate", [dep_entry()])
    kw.setdefault("summary", S)
    return a.amend_ruling(
        id="D-90",
        mode="position",
        text=record(ruling="The ruling changed."),
        archive={
            "path": ARCHIVE_REL,
            "text": archived_text("D-90-history-2026-08", replaced_by="D-90"),
        },
        **kw,
    )


def test_a_second_amendment_in_one_month_never_overwrites_the_first(corpus):
    # mcp-servers-01: SPEC-1 8.1 names one archive path per record and
    # month, and the second amendment used to land on it silently. Now
    # the verb refuses before any write and names the next free path.
    first = amend_position(corpus)
    assert first["kind"] == "applied", first
    entry = (corpus / ARCHIVE_REL).read_bytes()
    live = read(corpus, "docs/decisions/D-90-test.md")

    def again(path: str, ruling: str) -> dict:
        return a.amend_ruling(
            id="D-90", mode="position", text=record(ruling=ruling),
            archive={"path": path, "text": archived_text("D-90-history-2026-08-b", replaced_by="D-90")},
            propagate=[dep_entry("2026-08-15")], summary=S,
        )

    second = again(ARCHIVE_REL, "Changed again.")
    assert second["kind"] == "error", second
    assert "docs/archive/D-90-history-2026-08-b.md" in second["message"]
    assert (corpus / ARCHIVE_REL).read_bytes() == entry, "the first entry was overwritten"
    assert read(corpus, "docs/decisions/D-90-test.md") == live
    assert "Changed again." not in live

    third = again("docs/archive/D-90-history-2026-08-b.md", "Changed again.")
    assert third["kind"] == "applied", third
    assert (corpus / ARCHIVE_REL).read_bytes() == entry
    assert (corpus / "docs/archive/D-90-history-2026-08-b.md").is_file()

    fourth = again("docs/archive/D-90-history-2026-08-b.md", "And again.")
    assert fourth["kind"] == "error", fourth
    assert "docs/archive/D-90-history-2026-08-c.md" in fourth["message"]


def test_an_amendment_that_changes_the_id_is_refused_before_any_write(corpus):
    # mcp-servers-04: the verb resolved the record by id and wrote the
    # caller's text over it without comparing ids, so an uncommitted
    # record could be amended to a new id, leaving the archive entry's
    # replaced-by pointing at an id no file held. C5 saw only committed
    # paths. Both modes now refuse a mismatch first, naming both ids.
    path = "docs/decisions/D-92-new.md"
    assert a.propose_record(path, record(rid="D-92"), S)["kind"] == "applied"
    before = blobs(corpus)

    position = a.amend_ruling(
        id="D-92", mode="position", text=record(rid="D-99", ruling="Renamed."),
        archive={
            "path": "docs/archive/D-92-history-2026-08.md",
            "text": archived_text("D-92-history-2026-08", replaced_by="D-92"),
        },
        summary=S,
    )
    assert position["kind"] == "error", position
    assert "D-92" in position["message"] and "D-99" in position["message"]
    assert blobs(corpus) == before, "a refused amendment moved a byte"
    assert not (corpus / "docs/archive/D-92-history-2026-08.md").exists()

    metadata = a.amend_ruling(
        id="D-90", mode="metadata", text=record(rid="D-99", last_verified="2026-08-15"),
        summary=S,
    )
    assert metadata["kind"] == "error", metadata
    assert "D-90" in metadata["message"] and "D-99" in metadata["message"]
    assert blobs(corpus) == before


BAD = "docs/decisions/D-93-bad.md"
BAD_LINE = f"C2 (metadata) {BAD}: file is not valid UTF-8"


def seed_bad(corpus) -> bytes:
    raw = record(rid="D-93").replace("Test.", "Caf\u00e9 said \u201cno\u201d.").encode("cp1252")
    (corpus / BAD).write_bytes(raw)
    return raw


def test_a_file_that_is_not_utf8_is_inherited_and_a_write_at_its_path_repairs_it(corpus):
    # docs-engine-01: every verb used to raise while one cp1252 file stood
    # in the corpus. A change beside it applies and inherits the C2 line;
    # a propose_record at its path replaces the bytes and the line is gone.
    raw = seed_bad(corpus)
    beside = a.propose_record("docs/decisions/D-92-new.md", record(rid="D-92"), S)
    assert beside["kind"] == "applied", beside
    assert BAD_LINE in beside["inherited"]
    assert (corpus / BAD).read_bytes() == raw

    repair = a.propose_record(BAD, record(rid="D-93"), S)
    assert repair["kind"] == "applied", repair
    assert BAD_LINE not in repair["inherited"]
    assert fm(corpus, BAD)["id"] == "D-93"
    assert v.validate(corpus, today=TODAY, baseline={}) == []


def test_amend_and_retire_cannot_reach_a_file_that_is_not_utf8(corpus):
    # docs-engine-01: the loader skips the file, so neither verb finds its
    # id; the existing refusal answers, and nothing raises.
    seed_bad(corpus)
    amend = a.amend_ruling(id="D-93", mode="metadata", text=record(rid="D-93"), summary=S)
    assert amend == {"kind": "error", "message": "no in-force record holds id 'D-93'"}
    retire = a.retire_ruling(id="D-93", text=archived_text("D-93"), summary=S)
    assert retire == {"kind": "error", "message": "no in-force record holds id 'D-93'"}


def test_a_propagate_entry_naming_a_file_that_is_not_utf8_does_not_raise(corpus):
    # docs-engine-01: the propagate read decodes the standing file to
    # compare what it rules. A file that does not decode is refused by
    # name, and the amendment writes nothing.
    raw = seed_bad(corpus)
    live = read(corpus, "docs/decisions/D-90-test.md")
    out = amend_position(
        corpus, propagate=[dep_entry(), {"path": BAD, "text": record(rid="D-93")}],
    )
    assert out["kind"] == "error", out
    assert BAD in out["message"] and "not valid UTF-8" in out["message"]
    assert read(corpus, "docs/decisions/D-90-test.md") == live
    assert not (corpus / ARCHIVE_REL).exists()
    assert (corpus / BAD).read_bytes() == raw


def test_amend_position_writes_both_files(corpus):
    before = isolation(corpus)
    out = amend_position(corpus)
    assert out["kind"] == "applied", out
    assert "The ruling changed." in read(corpus, "docs/decisions/D-90-test.md")
    assert (corpus / ARCHIVE_REL).is_file()
    assert isolation(corpus) == before


def test_the_verb_writes_the_archive_entry_last_verified(corpus):
    """D-18 constraint 9. The value is carried from the record being
    archived as git HEAD holds it, never the archival date and never the
    caller's. The setup commits, because HEAD is now the source (G18)."""
    write(corpus, "docs/decisions/D-90-test.md", record(last_verified="2026-06-15"))
    git(corpus, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "recheck")
    out = a.amend_ruling(
        id="D-90",
        mode="position",
        text=record(ruling="New.", last_verified="2026-08-01"),
        summary=S,
        archive={
            "path": ARCHIVE_REL,
            "text": archived_text(
                "D-90-history-2026-08", replaced_by="D-90", last_verified="2026-08-01",
            ),
        },
        propagate=[dep_entry()],
    )
    assert out["kind"] == "applied", out
    assert fm(corpus, ARCHIVE_REL)["last-verified"] == "2026-06-15"


def test_a_propagation_stamp_does_not_reach_the_archive_entry(corpus):
    """G18. An 8.5 stamp moves the live date to today. A later 8.1 on that
    same record must not archive the stamped value: the position it retires
    was last really checked on the committed date, and constraint 9 asks
    for that one. The stamp and the amend are two calls, so no single-call
    check can see the pair; the entry is dated from git HEAD instead."""
    stamped = record(
        rid="D-91", depends_on="D-90", last_verified="2026-08-15",
        ruling="Depends on D-90.", tail="\n## Why\n\nBecause D-90 says so.\n",
    )
    first = amend_position(corpus, propagate=[{"path": DEP, "text": stamped}])
    assert first["kind"] == "applied", first
    assert fm(corpus, DEP)["last-verified"] == "2026-08-15"

    dep_archive = "docs/archive/D-91-history-2026-08.md"
    second = a.amend_ruling(
        id="D-91",
        mode="position",
        text=record(
            rid="D-91", depends_on="D-90", last_verified="2026-08-15",
            ruling="D-91 now rules for itself.",
            tail="\n## Why\n\nBecause D-90 says so.\n",
        ),
        summary=S,
        archive={
            "path": dep_archive,
            "text": archived_text("D-91-history-2026-08", replaced_by="D-91"),
        },
    )
    assert second["kind"] == "applied", second
    assert fm(corpus, dep_archive)["last-verified"] == "2026-08-01"


def test_a_record_git_head_does_not_carry_falls_back_to_the_working_tree(corpus):
    """The HEAD lookup has one gap: a record proposed and amended before
    any commit. The working-tree date is then the only date there is, and
    no stamp can have moved it, because nothing depends on the record."""
    new = "docs/decisions/D-92-new.md"
    assert a.propose_record(new, record(rid="D-92", last_verified="2026-07-04"), S)[
        "kind"
    ] == "applied"
    out = a.amend_ruling(
        id="D-92",
        mode="position",
        text=record(rid="D-92", last_verified="2026-07-04", ruling="Reversed."),
        summary=S,
        archive={
            "path": "docs/archive/D-92-history-2026-08.md",
            "text": archived_text("D-92-history-2026-08", replaced_by="D-92"),
        },
    )
    assert out["kind"] == "applied", out
    assert fm(corpus, "docs/archive/D-92-history-2026-08.md")[
        "last-verified"
    ] == "2026-07-04"


def test_amend_position_needs_an_archive_entry(corpus):
    out = a.amend_ruling(id="D-90", mode="position", text=record(ruling="New."), summary=S)
    assert out["kind"] == "error"
    assert "8.1" in out["message"]


def test_amend_refuses_an_unknown_id(corpus):
    out = a.amend_ruling(id="D-99", mode="metadata", text=record(rid="D-99"), summary=S)
    assert out["kind"] == "error"
    assert "D-99" in out["message"]


def test_amend_refuses_an_archived_record(corpus):
    """A record that has left cannot be amended back into force."""
    write(corpus, "docs/archive/D-70-old.md", archived_text("D-70"))
    out = a.amend_ruling(id="D-70", mode="metadata", text=archived_text("D-70"), summary=S)
    assert out["kind"] == "error"


# --- amend_ruling, metadata mode (SPEC-1 8.6) --------------------------

def test_metadata_accepts_a_last_verified_bump(corpus):
    before = isolation(corpus)
    out = a.amend_ruling(
        id="D-90", mode="metadata", text=record(last_verified="2026-08-14"), summary=S,
    )
    assert out["kind"] == "applied", out
    assert fm(corpus, "docs/decisions/D-90-test.md")["last-verified"] == "2026-08-14"
    assert isolation(corpus) == before


def test_metadata_refuses_a_body_change(corpus):
    """The mode that archives nothing cannot carry a position change
    through it. This is D1, made structurally impossible."""
    original = read(corpus, "docs/decisions/D-90-test.md")
    out = a.amend_ruling(
        id="D-90", mode="metadata",
        text=record(last_verified="2026-08-14", ruling="Quietly reversed."),
        summary=S,
    )
    assert out["kind"] == "error"
    assert "position mode" in out["message"]
    assert read(corpus, "docs/decisions/D-90-test.md") == original


def test_metadata_refuses_a_grade_change(corpus):
    """A re-check records evidence and never moves a grade (D-19)."""
    out = a.amend_ruling(
        id="D-90", mode="metadata",
        text=record(last_verified="2026-08-14", grade="verified"),
        summary=S,
    )
    assert out["kind"] == "error"


def test_metadata_refuses_to_archive_or_propagate(corpus):
    out = a.amend_ruling(
        id="D-90", mode="metadata", text=record(last_verified="2026-08-14"),
        summary=S, archive={"path": ARCHIVE_REL, "text": archived_text("x")},
    )
    assert out["kind"] == "error"
    assert "8.6" in out["message"]


# --- Propagation (SPEC-1 8.5, D-18 constraint 8) -----------------------

DEP = "docs/decisions/D-91-dependent.md"


def test_propagation_may_edit_text_outside_the_ruling(corpus):
    out = amend_position(corpus, propagate=[{
        "path": DEP,
        "text": record(
            rid="D-91", depends_on="D-90", last_verified="2026-08-14",
            ruling="Depends on D-90.",
            tail="\n## Why\n\nBecause D-90 says so, as amended today.\n",
        ),
    }])
    assert out["kind"] == "applied", out
    assert fm(corpus, DEP)["last-verified"] == "2026-08-14"
    assert "as amended today" in read(corpus, DEP)


def test_propagation_may_bump_the_date_alone(corpus):
    """8.5 stamps last-verified on every record read, whether or not its
    text changed, so the review leaves a mark."""
    out = amend_position(corpus, propagate=[{
        "path": DEP,
        "text": record(
            rid="D-91", depends_on="D-90", last_verified="2026-08-14",
            ruling="Depends on D-90.", tail="\n## Why\n\nBecause D-90 says so.\n",
        ),
    }])
    assert out["kind"] == "applied", out
    assert fm(corpus, DEP)["last-verified"] == "2026-08-14"


def test_propagation_without_a_stamp_is_refused(corpus):
    """Review finding 2026-08-16: a propagate entry byte-identical to
    disk was accepted, so 8.5's set was enforced but not its mark. The
    stamp is the mark of the read; an entry that does not move
    last-verified is refused."""
    original = read(corpus, DEP)
    out = amend_position(corpus, propagate=[{"path": DEP, "text": original}])
    assert out["kind"] == "error"
    assert "last-verified" in out["message"]
    assert read(corpus, DEP) == original


def test_propagation_accepts_a_date_already_at_today(corpus):
    """Finding 2026-08-18 (amending D-35 with D-36 landed the same day):
    a dependent verified today already carries the stamp 8.5 would leave,
    so no entry can move its date. The mark check accepts a date equal to
    today, while _missing_propagation still requires the entry; without
    this the two checks deadlock and force a direct-edit fallback."""
    today = TODAY.isoformat()
    stamped = record(
        rid="D-91", depends_on="D-90", last_verified=today,
        ruling="Depends on D-90.", tail="\n## Why\n\nBecause D-90 says so.\n",
    )
    write(corpus, DEP, stamped)
    out = amend_position(corpus, propagate=[{"path": DEP, "text": stamped}])
    assert out["kind"] == "applied", out
    assert fm(corpus, DEP)["last-verified"] == today


def test_no_verb_reads_the_wall_clock(corpus, monkeypatch):
    # tests-01: the verbs used to land with the real date, so every fixture
    # date here went stale on a calendar day. With the seam pinned, a wall
    # clock a year on changes nothing; cleared, the seam reads the clock at
    # the call, so it was never frozen at import.
    class _Day(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2027, 6, 1)

    monkeypatch.setattr(txn.dt, "date", _Day)
    monkeypatch.setattr(a.dt, "date", _Day)
    out = a.propose_record("docs/decisions/D-92-new.md", record(rid="D-92"), S)
    assert out["kind"] == "applied", out
    out = amend_position(corpus)
    assert out["kind"] == "applied", out

    monkeypatch.setattr(a, "TODAY", None)
    late = a.propose_record("docs/decisions/D-93-late.md", record(rid="D-93"), S)
    assert late["kind"] == "rejected", late
    assert any(f.startswith("C6 (freshness)") for f in late["failures"]), late


def test_propagation_may_not_reword_a_ruling_even_harmlessly(corpus):
    """The boundary is the section, not the meaning. No check can tell a
    reword from a reversal inside `## Ruling`, so propagation may not
    enter it at all. A genuine reword takes its own 8.1 amendment."""
    out = amend_position(corpus, propagate=[{
        "path": DEP,
        "text": record(
            rid="D-91", depends_on="D-90", last_verified="2026-08-14",
            ruling="Depends upon D-90.",  # one word, same meaning
            tail="\n## Why\n\nBecause D-90 says so.\n",
        ),
    }])
    assert out["kind"] == "error"
    assert "wording only" in out["message"]


def test_the_verb_identifies_a_dependent_the_caller_left_out(corpus):
    """Research control 4: a changed decision identifies and updates its
    dependents in the same reviewed change. The caller declares nothing
    here, and the verb finds D-91 on its own."""
    out = amend_position(corpus, propagate=[])
    assert out["kind"] == "error"
    assert DEP in out["message"]
    assert "8.5" in out["message"]
    assert not (corpus / ARCHIVE_REL).exists()


def test_the_chain_is_transitive(corpus):
    """G7: propagation reads the whole chain, direct and transitive. D-94
    depends on D-91, which depends on D-90."""
    write(
        corpus, "docs/decisions/D-94-far.md",
        record(rid="D-94", depends_on="D-91", ruling="Depends on D-91."),
    )
    out = amend_position(corpus)  # declares D-91 only
    assert out["kind"] == "error"
    assert "D-94-far.md" in out["message"]


def test_a_record_with_no_dependents_needs_no_propagation(corpus):
    out = a.amend_ruling(
        id="D-91", mode="position",
        text=record(rid="D-91", depends_on="D-90", ruling="Changed."),
        summary=S,
        archive={
            "path": "docs/archive/D-91-history-2026-08.md",
            "text": archived_text("D-91-history-2026-08", replaced_by="D-91"),
        },
    )
    assert out["kind"] == "applied", out


def test_metadata_mode_needs_no_propagation(corpus):
    """8.6 changes no position, so nothing propagates and the chain is
    not walked."""
    out = a.amend_ruling(
        id="D-90", mode="metadata", text=record(last_verified="2026-08-14"), summary=S,
    )
    assert out["kind"] == "applied", out


def test_propagation_refuses_to_change_what_a_dependent_rules(corpus):
    """The defect Phase 1 run 2 left standing: an 8.5 edit rewrote a
    dependent's ruling and archived nothing."""
    original = read(corpus, DEP)
    out = amend_position(corpus, propagate=[{
        "path": DEP,
        "text": record(
            rid="D-91", depends_on="D-90", ruling="Now rules something else.",
            tail="\n## Why\n\nBecause D-90 says so.\n",
        ),
    }])
    assert out["kind"] == "error"
    assert DEP in out["message"]
    assert "position mode" in out["message"]
    assert read(corpus, DEP) == original
    assert not (corpus / ARCHIVE_REL).exists(), "a rejected call wrote the archive entry"


def test_propagation_refuses_a_body_edit_where_there_is_no_ruling_section(corpus):
    write(
        corpus, "docs/decisions/D-93-plain.md",
        "---\nid: D-93\ntitle: Plain\ntype: decision\nstatus: in-force\n"
        "grade: decided\nowner: tester\nscope: test scope\neffective: 2026-08-01\n"
        "last-verified: 2026-08-01\nsupersedes: none\nreplaced-by: none\n---\n\n"
        "**Status: in-force.**\n\nNo ruling heading here.\n",
    )
    out = amend_position(corpus, propagate=[dep_entry(), {
        "path": "docs/decisions/D-93-plain.md",
        "text": "---\nid: D-93\ntitle: Plain\ntype: decision\nstatus: in-force\n"
                "grade: decided\nowner: tester\nscope: test scope\n"
                "effective: 2026-08-01\nlast-verified: 2026-08-14\n"
                "supersedes: none\nreplaced-by: none\n---\n\n"
                "**Status: in-force.**\n\nRewritten body.\n",
    }])
    assert out["kind"] == "error"
    assert "no '## Ruling' section" in out["message"]


# --- retire_ruling (SPEC-1 8.2 and 8.3) --------------------------------

def test_retire_without_a_successor(corpus):
    before = isolation(corpus)
    out = a.retire_ruling(
        id="D-90", text=archived_text("D-90"), summary=S, propagate=[dep_entry()],
    )
    assert out["kind"] == "applied", out
    assert not (corpus / "docs/decisions/D-90-test.md").exists()
    assert fm(corpus, "docs/archive/D-90-test.md")["status"] == "archived"
    assert isolation(corpus) == before


def test_retire_with_a_successor_lands_both(corpus):
    before = isolation(corpus)
    out = a.retire_ruling(
        id="D-90",
        text=archived_text("D-90", replaced_by="D-92"),
        summary=S,
        successor={
            "path": "docs/decisions/D-92-successor.md",
            "text": record(rid="D-92", ruling="The successor rules."),
        },
        propagate=[dep_entry()],
    )
    assert out["kind"] == "applied", out
    assert not (corpus / "docs/decisions/D-90-test.md").exists()
    assert fm(corpus, "docs/archive/D-90-test.md")["replaced-by"] == "D-92"
    assert fm(corpus, "docs/decisions/D-92-successor.md")["id"] == "D-92"
    assert isolation(corpus) == before


def test_a_successor_may_not_reuse_the_id(corpus):
    out = a.retire_ruling(
        id="D-90",
        text=archived_text("D-90", replaced_by="D-90"),
        summary=S,
        successor={"path": "docs/decisions/D-90-new.md", "text": record(rid="D-90")},
    )
    assert out["kind"] == "error"
    assert "8.1" in out["message"]


def test_the_retired_record_must_point_at_its_successor(corpus):
    out = a.retire_ruling(
        id="D-90",
        text=archived_text("D-90"),  # replaced-by left at none
        summary=S,
        successor={
            "path": "docs/decisions/D-92-successor.md", "text": record(rid="D-92"),
        },
    )
    assert out["kind"] == "error"
    assert "replaced-by" in out["message"]
    assert (corpus / "docs/decisions/D-90-test.md").is_file()


def test_a_retirement_that_renames_its_id_is_refused_before_any_write(corpus):
    # G17, then mcp-servers-04: C5 caught the rename only on a committed
    # path. The verb now compares the moved text's id with the id argument
    # and refuses first, naming both, whether or not HEAD carries the file.
    before = blobs(corpus)
    out = a.retire_ruling(
        id="D-90", text=archived_text("D-99"), summary=S, propagate=[dep_entry()],
    )
    assert out["kind"] == "error", out
    assert "D-90" in out["message"] and "D-99" in out["message"]
    assert blobs(corpus) == before, "a refused retirement moved a byte"

    path = "docs/decisions/D-92-new.md"
    assert a.propose_record(path, record(rid="D-92"), S)["kind"] == "applied"
    before = blobs(corpus)
    uncommitted = a.retire_ruling(id="D-92", text=archived_text("D-99"), summary=S)
    assert uncommitted["kind"] == "error", uncommitted
    assert "D-92" in uncommitted["message"] and "D-99" in uncommitted["message"]
    assert blobs(corpus) == before


def test_a_retirement_onto_a_same_named_archive_file_is_refused_with_the_direct_edit_named(corpus):
    # mcp-servers-03 (folded): SPEC-1 8.2 and 8.3 fix the destination to
    # the record's own filename, and the move wrote over an archive file
    # of that name. No -b name is open to a move that keeps its filename,
    # so the verb returns the transaction's refusal, naming the standing
    # file and the direct edit under SPEC-1 section 8.
    standing = "docs/archive/D-90-test.md"
    write(corpus, standing, archived_text("D-90").replace("Test.", "The standing entry."))
    before = blobs(corpus)

    out = a.retire_ruling(
        id="D-90", text=archived_text("D-90"), summary=S, propagate=[dep_entry()],
    )
    assert out["kind"] == "error", out
    assert standing in out["message"]
    assert "SPEC-1 section 8" in out["message"] and "direct edit" in out["message"]
    assert blobs(corpus) == before, "a refused retirement moved a byte"
    assert "The standing entry." in read(corpus, standing)
    assert (corpus / "docs/decisions/D-90-test.md").is_file()


def test_a_successor_never_lands_on_an_uncommitted_file(corpus):
    # mcp-servers-03 (folded): a retirement's successor was a plain Write,
    # so it replaced an uncommitted archive entry, or an uncommitted
    # proposal under docs/decisions/, with no git copy to restore. The
    # successor's path is refused like a proposal's when a decodable file
    # holds it, and both files of the retirement stay where they were.
    entry = "docs/archive/D-92-history-2026-07.md"
    write(corpus, entry, archived_text("D-92-history-2026-07"))
    proposal = "docs/decisions/D-92-new.md"
    assert a.propose_record(proposal, record(rid="D-92"), S)["kind"] == "applied"
    before = blobs(corpus)

    def retire(path: str, rid: str) -> dict:
        return a.retire_ruling(
            id="D-90", text=archived_text("D-90", replaced_by=rid), summary=S,
            successor={"path": path, "text": record(rid=rid, ruling="The successor rules.")},
            propagate=[dep_entry()],
        )

    archive = retire(entry, "D-93")
    assert archive["kind"] == "error", archive
    assert entry in archive["message"]
    assert blobs(corpus) == before, "a refused successor moved a byte"

    decisions = retire(proposal, "D-93")
    assert decisions["kind"] == "error", decisions
    assert proposal in decisions["message"]
    assert blobs(corpus) == before, "a refused successor moved a byte"
    assert fm(corpus, proposal)["id"] == "D-92"
    assert (corpus / "docs/decisions/D-90-test.md").is_file()


# --- The summary (PLAN-0012 phase 2, D-35 commitment 2) ----------------

def tool_list():
    return asyncio.run(a.build_server().list_tools())


def call(name: str, arguments: dict):
    return asyncio.run(a.build_server().call_tool(name, arguments))


def test_a_call_without_a_summary_fails_validation(corpus):
    """The field is required. A call that leaves it out never reaches the
    verb: the framework refuses it, as it refuses a missing path or text."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="summary"):
        call("propose_record", {
            "path": "docs/decisions/D-92-new.md", "text": record(rid="D-92"),
        })
    assert not (corpus / "docs/decisions/D-92-new.md").exists()


@pytest.mark.parametrize("summary", ["", "   ", "\n\t"])
def test_an_empty_summary_is_rejected(corpus, summary):
    """Present but empty is a change that does not yet pass, so it comes
    back rejected with the fix named, not as an error (SPEC-3 section 7).
    Whitespace alone is empty."""
    out = a.propose_record("docs/decisions/D-92-new.md", record(rid="D-92"), summary)
    assert out["kind"] == "rejected", out
    assert "empty" in out["reason"] and "one plain sentence" in out["reason"]
    assert out["paths"] == ["docs/decisions/D-92-new.md"]
    assert out["failures"] == []
    assert not (corpus / "docs/decisions/D-92-new.md").exists()


def test_a_summary_over_the_limit_is_rejected(corpus):
    long = "x" * (a.SUMMARY_MAX + 1)
    out = a.propose_record("docs/decisions/D-92-new.md", record(rid="D-92"), long)
    assert out["kind"] == "rejected", out
    assert str(a.SUMMARY_MAX + 1) in out["reason"] and str(a.SUMMARY_MAX) in out["reason"]
    assert not (corpus / "docs/decisions/D-92-new.md").exists()


def test_a_summary_at_the_limit_is_applied(corpus):
    """Surrounding whitespace is trimmed before the length is measured."""
    exact = "y" * a.SUMMARY_MAX
    out = a.propose_record("docs/decisions/D-92-new.md", record(rid="D-92"), f"  {exact}\n")
    assert out["kind"] == "applied", out
    assert out["summary"] == exact


def test_the_applied_result_echoes_the_summary(corpus):
    """The verb returns the sentence the agent shows the person."""
    out = a.propose_record("docs/decisions/D-92-new.md", record(rid="D-92"), S)
    assert out["kind"] == "applied", out
    assert out["summary"] == S


def test_every_verb_rejects_an_empty_summary_and_writes_nothing(corpus):
    before = {rel: read(corpus, rel) for rel in ("docs/decisions/D-90-test.md", DEP)}
    amend = amend_position(corpus, summary=" ")
    retire = a.retire_ruling(
        id="D-90", text=archived_text("D-90"), summary="", propagate=[dep_entry()],
    )
    assert amend["kind"] == "rejected" and retire["kind"] == "rejected"
    assert amend["paths"] == sorted([ARCHIVE_REL, DEP, "docs/decisions/D-90-test.md"])
    assert retire["paths"] == sorted([DEP, "docs/archive/D-90-test.md"])
    assert not (corpus / ARCHIVE_REL).exists()
    assert (corpus / "docs/decisions/D-90-test.md").is_file()
    assert {rel: read(corpus, rel) for rel in before} == before


def test_a_malformed_call_is_reported_before_its_summary(corpus):
    """A bad summary on a bad call still gets the malformation first; the
    server never converts an error into a rejection (SPEC-3 section 7)."""
    out = a.amend_ruling(id="D-99", mode="metadata", text=record(rid="D-99"), summary="")
    assert out["kind"] == "error"
    assert "D-99" in out["message"]


def test_the_summary_is_carried_by_the_schema():
    """Required on every verb, described for the model, and unbounded in
    the schema on purpose: the bound is the verb's, so an overlong
    sentence comes back rejected with the fix named."""
    for tool in tool_list():
        spec = tool.input_schema["properties"]["summary"]
        assert "summary" in tool.input_schema["required"], tool.name
        assert spec["type"] == "string" and "sentence" in spec["description"], tool.name
        assert "minLength" not in spec and "maxLength" not in spec, tool.name


# --- Schema cost and the read-only session -----------------------------


def test_the_three_verbs_are_exposed():
    assert {t.name for t in tool_list()} == {
        "propose_record", "amend_ruling", "retire_ruling",
    }


def test_the_mode_is_carried_by_the_schema(corpus):
    """D-18: the mode is required and never inferred. The schema says so,
    rather than the prose alone."""
    amend = next(t for t in tool_list() if t.name == "amend_ruling")
    assert amend.input_schema["properties"]["mode"]["enum"] == ["position", "metadata"]
    assert "mode" in amend.input_schema["required"]


def test_every_verb_declares_itself_not_read_only():
    for tool in tool_list():
        assert tool.annotations is not None, f"{tool.name} declares no annotations"
        assert tool.annotations.read_only_hint is False, tool.name


def test_required_strings_may_not_be_empty():
    """An empty id, path or record text is a malformed call. The schema
    refuses it before the verb runs. `mode` is exempt: its enum is a
    tighter constraint than a length. `summary` is exempt: an empty one
    is a rejection the caller iterates against, not a malformed call."""
    for tool in tool_list():
        for name in tool.input_schema.get("required", []):
            spec = tool.input_schema["properties"][name]
            if spec.get("type") == "string" and "enum" not in spec and name != "summary":
                assert spec.get("minLength") == 1, f"{tool.name}.{name}"


def test_write_schema_stays_within_budget():
    total = sum(
        len(t.model_dump_json(exclude_none=True, by_alias=True)) for t in tool_list()
    )
    assert total <= SCHEMA_BUDGET, f"{total} B over the {SCHEMA_BUDGET} B bound"


def test_a_read_only_session_loads_no_write_schema():
    """D-18 constraint 6. The retrieval server is a separate process and
    exposes five read tools; none of the verbs reaches it."""
    from radialrig import retrieval_server as rs

    read_tools = {"get_ruling", "list_rulings", "search_history", "check_docs"}
    exposed = {v.__name__ for v in a.VERBS}
    assert not (exposed & read_tools)
    assert all(not hasattr(rs, name) for name in exposed)


def test_the_handshake_names_the_installed_engine():
    """PLAN-0018 phase 1 (BUG-0013): the write server's `serverInfo`
    carries the installed engine's version, the read server's way."""
    from radialrig import mount as mnt
    from radialrig.tests.test_retrieval_server import handshake

    info = handshake(a.build_server())
    assert info.name == "radialrig-write"
    assert info.version == mnt.installed_version()
