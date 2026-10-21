"""Seeded-defect tests for the transaction core (SPEC-3, PLAN-0003 Phase 2).

Run:  uv run --with pytest pytest src/radialrig/tests/test_doc_txn.py -q

One invalid change per check C1-C7. Each must be rejected with zero files
written — the Phase 2 done-when condition. A valid change must apply, and
an injected mid-transaction failure must leave zero paths changed.

The corpus here is a real git repository, because C5 needs a prior state
and D-23 says a missing baseline is never a passing check.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import pytest

from radialrig import doc_txn as t
from radialrig.doc_core import first_body_paragraph, parse_frontmatter, strip_code

TODAY = dt.date(2026, 8, 15)


def record(
    rid: str = "D-90",
    status: str = "in-force",
    scope: str = "test scope",
    first_line: str | None = None,
    body_extra: str = "",
    omit: str | None = None,
    last_verified: str = "2026-08-01",
    replaced_by: str = "none",
) -> str:
    fields = {
        "id": rid, "title": "Test record", "type": "decision",
        "status": status, "grade": "decided", "owner": "tester",
        "scope": scope, "effective": "2026-08-01",
        "last-verified": last_verified,
        "supersedes": "none", "replaced-by": replaced_by,
        "depends-on": "none",
    }
    if omit:
        fields.pop(omit)
    fm = "\n".join(f"{k}: {v}" for k, v in fields.items())
    first = first_line if first_line is not None else f"**Status: {status}.**"
    return f"---\n{fm}\n---\n\n{first}\n\n## Ruling\n\nTest.\n{body_extra}"


def archived(rid: str = "D-90", replaced_by: str = "none") -> str:
    """A well-formed archive entry: past-tense fence line, C7-clean."""
    head = (
        "**Status: archived.** This is a record of a past position, not an\n"
        f"instruction. The current ruling is {replaced_by}."
        if replaced_by != "none" else
        "**Status: archived.** This is a record of a past position, not an\n"
        "instruction."
    )
    return record(rid=rid, status="archived", first_line=head, replaced_by=replaced_by)


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    """A committed one-record corpus. D-90 is in force."""
    (tmp_path / "docs" / "decisions").mkdir(parents=True)
    (tmp_path / "docs" / "archive").mkdir(parents=True)
    (tmp_path / "docs" / "decisions" / "D-90-test.md").write_text(
        record(), encoding="utf-8", newline="\n",
    )
    for args in (
        ["init", "-q", "-b", "master"], ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted((root / "docs").rglob("*.md"))
    }


def test_a_file_that_is_not_utf8_is_inherited_until_a_write_replaces_it(corpus):
    # docs-engine-01: the gate reads every governed file on both sides,
    # so one cp1252 file used to raise out of every change. Now the file
    # is a C2 line the change inherits, until a Write or a Move replaces
    # its bytes, which repairs it and applies.
    bad = corpus / "docs/decisions/D-93-bad.md"
    raw = record(rid="D-93").replace("Test.", "Caf\u00e9 said \u201cno\u201d.").encode("cp1252")
    bad.write_bytes(raw)
    line = "C2 (metadata) docs/decisions/D-93-bad.md: file is not valid UTF-8"

    beside = t.apply_change(
        corpus, [t.Write("docs/decisions/D-91.md", record(rid="D-91"))], today=TODAY,
    )
    assert beside.kind == "applied", beside
    assert line in beside.inherited
    assert bad.read_bytes() == raw, "a change beside the file touched it"

    repair = t.apply_change(
        corpus, [t.Write("docs/decisions/D-93-bad.md", record(rid="D-93"))], today=TODAY,
    )
    assert repair.kind == "applied", repair
    assert line not in repair.inherited
    assert parse_frontmatter(bad.read_text(encoding="utf-8"))[0]["id"] == "D-93"

    moved = corpus / "docs/decisions/D-94-bad.md"
    moved.write_bytes(record(rid="D-94").replace("Test.", "Caf\u00e9.").encode("cp1252"))
    away = t.apply_change(
        corpus,
        [t.Move("docs/decisions/D-94-bad.md", "docs/archive/D-94-bad.md", archived("D-94"))],
        today=TODAY,
    )
    assert away.kind == "applied", away
    assert not any("D-94-bad" in f for f in away.inherited)
    assert not moved.exists()
    assert (corpus / "docs/archive/D-94-bad.md").read_text(encoding="utf-8")


def rejected(root: Path, ops, check: str) -> None:
    """The change is rejected on `check`, and not one byte moved."""
    before = snapshot(root)
    out = t.apply_change(root, ops, today=TODAY)
    assert out.kind == "rejected", out
    assert any(f.startswith(check) for f in out.failures), out.failures
    assert snapshot(root) == before, "a rejected change wrote to the corpus"


# --- One seeded defect per check ---------------------------------------

def test_c1_duplicate_in_force_id(corpus):
    rejected(corpus, [t.Write("docs/decisions/D-90-copy.md", record())], "C1")


def test_c2_missing_required_field(corpus):
    rejected(corpus, [t.Write("docs/decisions/D-91.md", record(rid="D-91", omit="owner"))], "C2")


def test_c3_wrong_head_of_file_line(corpus):
    ops = [t.Write("docs/decisions/D-91.md", record(rid="D-91", first_line="## Ruling"))]
    rejected(corpus, ops, "C3")


def test_c4_broken_relative_link(corpus):
    ops = [t.Write(
        "docs/decisions/D-91.md",
        record(rid="D-91", body_extra="\nSee [missing](../nowhere.md).\n"),
    )]
    rejected(corpus, ops, "C4")


def commit(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message],
        cwd=root, check=True, capture_output=True,
    )


def test_c5_illegal_transition(corpus):
    """archived is terminal. Reviving a retired record must not pass."""
    move = [t.Move("docs/decisions/D-90-test.md", "docs/archive/D-90-test.md", archived())]
    assert t.apply_change(corpus, move, today=TODAY).kind == "applied"
    commit(corpus, "retire")
    revive = [t.Move("docs/archive/D-90-test.md", "docs/decisions/D-90-test.md", record())]
    rejected(corpus, revive, "C5")


def test_c5_changed_id_on_a_move(corpus):
    """The baseline follows the record to its new path, so a move cannot
    launder an id change past C5 (D-06)."""
    ops = [t.Move(
        "docs/decisions/D-90-test.md", "docs/archive/D-90-test.md", archived(rid="D-99"),
    )]
    rejected(corpus, ops, "C5")


def test_c6_stale_last_verified(corpus):
    ops = [t.Write(
        "docs/decisions/D-91.md", record(rid="D-91", last_verified="2026-01-01"),
    )]
    rejected(corpus, ops, "C6")


def test_c7_archived_record_outside_the_archive(corpus):
    ops = [t.Write("docs/decisions/D-91.md", archived(rid="D-91"))]
    rejected(corpus, ops, "C7")


def test_c7_archive_entry_without_the_fence_line(corpus):
    ops = [t.Write(
        "docs/archive/D-91.md",
        record(rid="D-91", status="archived", first_line="**Status: archived.**"),
    )]
    rejected(corpus, ops, "C7")


# --- The change that should land ---------------------------------------

def test_a_valid_change_applies(corpus):
    ops = [t.Write("docs/decisions/D-91.md", record(rid="D-91"))]
    out = t.apply_change(corpus, ops, today=TODAY)
    assert out.kind == "applied"
    assert out.paths == ("docs/decisions/D-91.md",)
    assert out.inherited == ()
    assert (corpus / "docs/decisions/D-91.md").is_file()


def test_a_move_lands_both_sides(corpus):
    ops = [t.Move(
        "docs/decisions/D-90-test.md", "docs/archive/D-90-test.md", archived(),
    )]
    out = t.apply_change(corpus, ops, today=TODAY)
    assert out.kind == "applied"
    assert not (corpus / "docs/decisions/D-90-test.md").exists()
    landed = (corpus / "docs/archive/D-90-test.md").read_text(encoding="utf-8")
    _, body = parse_frontmatter(landed)
    assert "not an instruction" in first_body_paragraph(body)


def test_files_are_written_with_lf_endings(corpus):
    """A CRLF rewrite would show every line of every amended record as a
    change in the diff the reviewer reads (D-18 constraint 4)."""
    t.apply_change(corpus, [t.Write("docs/decisions/D-91.md", record(rid="D-91"))],
                   today=TODAY)
    assert b"\r\n" not in (corpus / "docs/decisions/D-91.md").read_bytes()


# --- The delta gate (SPEC-3 section 2) ---------------------------------

def seed_stale(corpus: Path) -> None:
    """A pre-existing C6 failure nobody's change caused."""
    (corpus / "docs/decisions/D-80-stale.md").write_text(
        record(rid="D-80", last_verified="2026-01-01"), encoding="utf-8", newline="\n",
    )
    commit(corpus, "stale")


def test_an_inherited_failure_does_not_block_an_unrelated_change(corpus):
    seed_stale(corpus)
    out = t.apply_change(
        corpus, [t.Write("docs/decisions/D-91.md", record(rid="D-91"))], today=TODAY,
    )
    assert out.kind == "applied"
    assert len(out.inherited) == 1
    assert out.inherited[0].startswith("C6 (freshness) docs/decisions/D-80-stale.md")


def test_an_inherited_failure_is_reported_not_hidden(corpus):
    """A caller must not read an applied result as a clean corpus."""
    seed_stale(corpus)
    out = t.apply_change(
        corpus, [t.Write("docs/decisions/D-91.md", record(rid="D-91"))], today=TODAY,
    )
    assert out.inherited, "an applied result hid a failure the corpus still has"


def test_a_change_may_repair_an_inherited_failure(corpus):
    seed_stale(corpus)
    ops = [t.Write("docs/decisions/D-80-stale.md", record(rid="D-80"))]
    out = t.apply_change(corpus, ops, today=TODAY)
    assert out.kind == "applied"
    assert out.inherited == ()


def test_a_new_failure_blocks_even_beside_an_inherited_one(corpus):
    seed_stale(corpus)
    rejected(corpus, [t.Write("docs/decisions/D-91.md", record(rid="D-91", omit="owner"))], "C2")


# --- Atomicity (D-18 constraint 3) -------------------------------------

def fail_on_live(corpus: Path, filename: str, monkeypatch) -> None:
    """Fail the live write of `filename`, letting the copy stage succeed.

    The second path is the one that fails, so the first has already been
    written when the rollback runs. That is the case a partial write
    would leave behind.
    """
    real = t._write_file

    def flaky(path: Path, text: str) -> None:
        if path.name == filename and str(path).startswith(str(corpus)):
            raise OSError("injected failure on the second path")
        real(path, text)

    monkeypatch.setattr(t, "_write_file", flaky)


AMEND_TWO = [
    t.Write("docs/decisions/D-90-test.md", record(body_extra="\nAmended.\n")),
    t.Write("docs/decisions/D-91.md", record(rid="D-91")),
]


def test_an_injected_failure_leaves_zero_paths_changed(corpus, monkeypatch):
    before = snapshot(corpus)
    fail_on_live(corpus, "D-91.md", monkeypatch)
    with pytest.raises(OSError):
        t.apply_change(corpus, AMEND_TWO, today=TODAY)
    assert snapshot(corpus) == before
    assert not (corpus / "docs/decisions/D-91.md").exists()


def test_a_rollback_restores_prior_bytes(corpus, monkeypatch):
    """The first path is overwritten before the second fails, so the
    rollback must put its original bytes back, not a re-render of them."""
    original = (corpus / "docs/decisions/D-90-test.md").read_bytes()
    fail_on_live(corpus, "D-91.md", monkeypatch)
    with pytest.raises(OSError):
        t.apply_change(corpus, AMEND_TWO, today=TODAY)
    assert (corpus / "docs/decisions/D-90-test.md").read_bytes() == original


# --- Malformed calls are errors, not rejections (SPEC-3 section 7) -----

def test_a_path_outside_the_governed_layout_is_an_error(corpus):
    out = t.apply_change(corpus, [t.Write("docs/notes.md", record())], today=TODAY)
    assert out.kind == "error"
    assert "governed layout" in out.message


def test_a_path_escaping_the_root_is_an_error(corpus):
    out = t.apply_change(
        corpus, [t.Write("docs/decisions/../../evil.md", record())], today=TODAY,
    )
    assert out.kind == "error"


def test_two_operations_on_one_path_are_an_error(corpus):
    ops = [
        t.Write("docs/decisions/D-91.md", record(rid="D-91")),
        t.Write("docs/decisions/D-91.md", record(rid="D-91")),
    ]
    out = t.apply_change(corpus, ops, today=TODAY)
    assert out.kind == "error"


def test_a_missing_move_source_is_an_error(corpus):
    out = t.apply_change(
        corpus,
        [t.Move("docs/decisions/nope.md", "docs/archive/nope.md", archived())],
        today=TODAY,
    )
    assert out.kind == "error"


def test_an_empty_operation_set_is_an_error(corpus):
    assert t.apply_change(corpus, [], today=TODAY).kind == "error"


def test_a_corpus_outside_git_is_an_error(tmp_path):
    """C5 cannot run without a prior state, and D-18 constraint 2 requires
    C1-C7. The transaction says so rather than validating six of seven."""
    (tmp_path / "docs" / "decisions").mkdir(parents=True)
    (tmp_path / "docs" / "decisions" / "D-90-test.md").write_text(
        record(), encoding="utf-8", newline="\n",
    )
    out = t.apply_change(
        tmp_path, [t.Write("docs/decisions/D-91.md", record(rid="D-91"))], today=TODAY,
    )
    assert out.kind == "error"
    assert "C5" in out.message


def test_no_delete_operation_exists():
    """D-18 constraint 1 — no verb deletes a record — is carried by the
    type system here, not by a check that could be forgotten. A record
    leaves a path only by arriving at another one."""
    import typing

    assert set(typing.get_args(t.Op)) == {t.Write, t.Move}


# --- Nothing under docs/archive/ is written over (PLAN-0017 phase 1) ----

def blobs(root: Path) -> dict[str, bytes]:
    """Every byte under docs/, so a refusal can be shown to have moved none."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted((root / "docs").rglob("*")) if p.is_file()
    }


def test_a_write_or_a_move_onto_a_file_under_the_archive_is_refused_and_a_bad_byte_there_is_repaired(corpus):
    # mcp-servers-03 (folded): a Write at an uncommitted archive entry, or
    # a Move onto a same-named archive file, replaced it wholesale, and the
    # person's diff showed no deletion to notice. No rule and no verb
    # rewrites a file under docs/archive/, so the transaction refuses
    # before staging, naming the path and the rule. A file there whose
    # bytes do not decode is the repair PLAN-0016 phase 3 proved: it stays
    # writable.
    entry = "docs/archive/D-70-history-2026-07.md"
    (corpus / entry).write_text(
        archived("D-70-history-2026-07"), encoding="utf-8", newline="\n",
    )
    same = "docs/archive/D-90-test.md"
    (corpus / same).write_text(archived("D-90"), encoding="utf-8", newline="\n")
    before = blobs(corpus)

    over = t.apply_change(
        corpus, [t.Write(entry, archived("D-70-history-2026-07-b"))], today=TODAY,
    )
    assert over.kind == "error", over
    assert entry in over.message and "docs/archive/" in over.message
    assert blobs(corpus) == before, "a refused write moved a byte"

    onto = t.apply_change(
        corpus, [t.Move("docs/decisions/D-90-test.md", same, archived())], today=TODAY,
    )
    assert onto.kind == "error", onto
    assert same in onto.message and "docs/archive/" in onto.message
    assert blobs(corpus) == before, "a refused move moved a byte"

    bad = "docs/archive/D-71-bad.md"
    (corpus / bad).write_bytes(archived("D-71").replace("Test.", "Café.").encode("cp1252"))
    repair = t.apply_change(corpus, [t.Write(bad, archived("D-71"))], today=TODAY)
    assert repair.kind == "applied", repair
    assert parse_frontmatter((corpus / bad).read_text(encoding="utf-8"))[0]["id"] == "D-71"


def test_a_committed_archive_path_is_refused_before_the_baseline_is_rekeyed_over_it(corpus):
    # mcp-servers-03 (folded): _baseline_for_copy re-keyed the source's
    # baseline over the destination, so C5 never saw the id the standing
    # archive file held. Now the check over the ops refuses a destination
    # the baseline carries first, and _baseline_for_copy raises rather
    # than re-key over a held key, so that branch is never entered on a
    # live call.
    same = "docs/archive/D-90-test.md"
    (corpus / same).write_text(archived("D-99"), encoding="utf-8", newline="\n")
    bad = "docs/archive/D-71-bad.md"
    (corpus / bad).write_bytes(archived("D-71").replace("Test.", "Café.").encode("cp1252"))
    commit(corpus, "an archive file the baseline carries, and a bad byte")
    before = blobs(corpus)

    onto = t.apply_change(
        corpus, [t.Move("docs/decisions/D-90-test.md", same, archived())], today=TODAY,
    )
    assert onto.kind == "error", onto
    assert same in onto.message
    assert blobs(corpus) == before

    # The key alone refuses when a caller's baseline carries a path no
    # file holds; git_head_baseline keys only the files it finds on disk.
    (corpus / same).unlink()
    held = {same: {"id": "D-99"}}
    complaint = t._archive_overwrite(corpus, held, [t.Write(same, archived("D-99"))])
    assert complaint is not None and same in complaint
    assert t._archive_overwrite(corpus, {}, [t.Write(same, archived("D-99"))]) is None

    baseline = {"docs/decisions/D-90-test.md": {"id": "D-90"}, **held}
    with pytest.raises(ValueError, match=same):
        t._baseline_for_copy(baseline, [t.Move("docs/decisions/D-90-test.md", same, archived())])

    # A committed file whose bytes do not decode is still the repair case.
    repair = t.apply_change(corpus, [t.Write(bad, archived("D-71"))], today=TODAY)
    assert repair.kind == "applied", repair
    assert parse_frontmatter((corpus / bad).read_text(encoding="utf-8"))[0]["id"] == "D-71"


# --- Links: fenced ones stop nothing, escaping ones are named -----------

def test_a_link_inside_a_fence_or_a_code_span_stops_nothing(corpus):
    # docs-engine-02: C4 strips fenced blocks and inline code spans before
    # it looks for links, but _escaping_links ran over the raw text, so one
    # illustrative link inside a fence froze every verb, repo-wide. Both
    # scanners now read the same stripped text.
    example = (
        "\n```\n[readme](../../README.md)\n```\n\n"
        "Not `[inline](../../README.md)` either.\n"
    )
    (corpus / "docs/decisions/D-80-fenced.md").write_text(
        record(rid="D-80", body_extra=example), encoding="utf-8", newline="\n",
    )
    commit(corpus, "a fenced link example")
    assert t._escaping_links(corpus) == []

    out = t.apply_change(
        corpus, [t.Write("docs/decisions/D-91.md", record(rid="D-91"))], today=TODAY,
    )
    assert out.kind == "applied", out
    assert out.inherited == ()


def stops_nothing(corpus: Path, example: str) -> None:
    """A committed record carrying `example` blocks no unrelated change."""
    (corpus / "docs/decisions/D-81-edge.md").write_text(
        record(rid="D-81", body_extra=example), encoding="utf-8", newline="\n",
    )
    commit(corpus, "a code example")
    assert t._escaping_links(corpus) == []
    out = t.apply_change(
        corpus, [t.Write("docs/decisions/D-91.md", record(rid="D-91"))], today=TODAY,
    )
    assert out.kind == "applied", out
    assert out.inherited == ()


def test_a_link_inside_a_tilde_fence_stops_nothing(corpus):
    # PLAN-0017 phase 1 verifier: strip_code read backtick fences alone,
    # so a tilde fence (CommonMark) still froze every verb.
    stops_nothing(corpus, "\n~~~\n[readme](../../README.md)\n~~~\n")


def test_a_link_inside_a_double_backtick_span_stops_nothing(corpus):
    # Same residual: a span opened by two backticks was read as two empty
    # spans with a live link between them.
    stops_nothing(corpus, "\nNot ``[readme](../../README.md)`` either.\n")


def test_a_shorter_backtick_run_does_not_close_a_code_span(corpus):
    # A span closes only on a run of exactly its opener's length, so a
    # shorter run inside it is content, not a closer.
    stops_nothing(corpus, "\nNot ``[readme](../../README.md)`a`` either.\n")
    three = "```[readme](../../README.md)`` still open```"
    assert "README" not in strip_code(three)
    assert "README" in strip_code("~~~\n[readme](../../README.md)\n")  # unclosed stays


def test_a_new_record_linking_outside_docs_is_refused_by_its_true_reason(corpus):
    # docs-engine-04: the copy holds docs/ alone, so C4 on it reported
    # "link target '../../README.md' does not exist" for a target that
    # exists live, and an agent reading that would break a working link.
    # The change's own text is scanned first, and the refusal names the
    # path, the link and the real constraint.
    (corpus / "README.md").write_text("# A readme outside docs/\n", encoding="utf-8")
    before = blobs(corpus)
    link = "\nSee [the readme](../../README.md).\n"

    out = t.apply_change(
        corpus, [t.Write("docs/decisions/D-91.md", record(rid="D-91", body_extra=link))],
        today=TODAY,
    )
    assert out.kind == "error", out
    assert "docs/decisions/D-91.md" in out.message
    assert "../../README.md" in out.message
    assert "docs/" in out.message
    assert "does not exist" not in out.message
    assert blobs(corpus) == before

    moved = t.apply_change(
        corpus,
        [t.Move(
            "docs/decisions/D-90-test.md", "docs/archive/D-90-test.md",
            archived().replace("Test.", f"Test.{link}"),
        )],
        today=TODAY,
    )
    assert moved.kind == "error", moved
    assert "docs/archive/D-90-test.md" in moved.message
    assert "../../README.md" in moved.message
    assert blobs(corpus) == before


# --- A moved record keeps its inherited failures (docs-engine-03) --------

def test_a_retirement_inherits_the_failure_its_record_already_had(corpus):
    # docs-engine-03: failure lines carry the path, and the gate is a
    # multiset difference of whole lines, so a record's inherited failure
    # vanished at the source and reappeared at the destination, counted
    # as added. The before lines are re-keyed the way the baseline is.
    legacy = "docs/decisions/D-40-legacy.md"
    (corpus / legacy).write_text(
        record(rid="D-40", omit="depends-on"), encoding="utf-8", newline="\n",
    )
    commit(corpus, "a legacy record missing a field")
    dst = "docs/archive/D-40-legacy.md"
    line = f"C2 (metadata) {dst}: missing required field 'depends-on'"

    out = t.apply_change(
        corpus,
        [t.Move(legacy, dst, archived("D-40").replace("depends-on: none\n", ""))],
        today=TODAY,
    )
    assert out.kind == "applied", out
    assert line in out.inherited
    assert not (corpus / legacy).exists()
    assert (corpus / dst).is_file()


def test_a_move_rekeys_only_the_line_the_moved_record_owns(corpus):
    # PLAN-0017 phase 1 verifier: the re-key was a substring replace over
    # the whole failure line, so another record's inherited C4 line that
    # quoted the retired record's path was rewritten and counted as added.
    # Only the path in the validator's `CHECK (name) <path>:` prefix moves.
    links = "docs/decisions/D-82-links.md"
    (corpus / links).write_text(
        record(rid="D-82", body_extra="\nSee [x](docs/decisions/D-90-test.md).\n"),
        encoding="utf-8", newline="\n",
    )
    commit(corpus, "a record quoting another record's path in a broken link")
    line = f"C4 (links) {links}: link target 'docs/decisions/D-90-test.md' does not exist"
    assert line in t.validate(corpus, TODAY, baseline={})

    out = t.apply_change(
        corpus,
        [t.Move("docs/decisions/D-90-test.md", "docs/archive/D-90-test.md", archived())],
        today=TODAY,
    )
    assert out.kind == "applied", out
    assert out.inherited == (line,)
