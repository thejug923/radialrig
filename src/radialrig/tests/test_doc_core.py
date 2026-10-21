"""Tests for doc_core: shared parsing and authority-order selection.

Run:  uv run --with pytest pytest src/radialrig/tests/test_doc_core.py -q

Selection tests exercise D-09 / SPEC-1 s7 in code, per PLAN-0002 Phase 1:
exact key, unique non-key match, typed ambiguity, typed conflict, typed
no-ruling. No ranking, no best guess (plan invariant 3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from radialrig import doc_core as dc


def rec(
    rid: str = "D-90",
    title: str = "Test record",
    status: str = "in-force",
    scope: str = "test scope",
    replaced_by: str = "none",
    rel: str | None = None,
) -> dc.Record:
    fm = {
        "id": rid, "title": title, "type": "decision", "status": status,
        "grade": "decided", "owner": "tester", "scope": scope,
        "effective": "2026-08-01", "last-verified": "2026-08-01",
        "supersedes": "none", "replaced-by": replaced_by,
        "depends-on": "none",
    }
    rel = rel or f"docs/decisions/{rid}-test.md"
    return dc.Record(path=Path(rel), rel=rel, fm=fm, body=f"**Status: {status}.**\n\nTest.")


# --- exact key ---------------------------------------------------------

def test_exact_key_returns_ruling():
    records = [rec("D-01"), rec("D-02")]
    result = dc.select_ruling(records, "D-01")
    assert isinstance(result, dc.Ruling)
    assert result.record.id == "D-01"


def test_exact_key_is_case_insensitive():
    result = dc.select_ruling([rec("D-01")], "d-01")
    assert isinstance(result, dc.Ruling)
    assert result.record.id == "D-01"


# --- non-key queries ---------------------------------------------------

def test_unique_title_match_returns_ruling():
    records = [rec("D-01", title="Archive exclusion"), rec("D-02", title="Metadata contract")]
    result = dc.select_ruling(records, "archive exclusion")
    assert isinstance(result, dc.Ruling)
    assert result.record.id == "D-01"


def test_multiple_matches_return_typed_ambiguity_not_content():
    records = [rec("D-01", title="Archive exclusion"), rec("D-02", title="Archive voice rule")]
    result = dc.select_ruling(records, "archive")
    assert isinstance(result, dc.Ambiguous)
    assert result.candidates == [("D-01", "Archive exclusion"), ("D-02", "Archive voice rule")]


def test_no_match_returns_typed_no_ruling():
    result = dc.select_ruling([rec("D-01")], "quantum governance")
    assert isinstance(result, dc.NoRuling)


# --- word-assisted concept queries (D-16) -------------------------------

def body_rec(rid: str, title: str, body: str) -> dc.Record:
    r = rec(rid, title=title)
    r.body = f"**Status: in-force.**\n\n{body}"
    return r


def test_concept_query_matching_body_returns_candidates_not_content():
    # The D-11 gap: no substring of the query appears in id or title,
    # but the body holds every query word. Body-assisted matches return
    # typed candidates, never content — even for a single candidate.
    records = [
        body_rec("D-11", "The process applies to this repository first",
                 "A portable template for other projects comes only after the pilot."),
        body_rec("D-04", "Archive exclusion", "The archive glob rules."),
    ]
    result = dc.select_ruling(records, "portable template")
    assert isinstance(result, dc.Ambiguous)
    assert result.candidates == [("D-11", "The process applies to this repository first")]


def test_word_match_requires_every_word():
    records = [body_rec("D-11", "Repository first", "A portable template after the pilot.")]
    result = dc.select_ruling(records, "portable retention period")
    assert isinstance(result, dc.NoRuling)


def test_word_match_ignores_short_words():
    records = [body_rec("D-11", "Repository first", "A portable template.")]
    result = dc.select_ruling(records, "is a the of")
    assert isinstance(result, dc.NoRuling)


def test_unique_title_substring_still_selects_directly():
    # Lexical metadata matching keeps returning the ruling; only
    # body-assisted matches demote to candidates.
    records = [body_rec("D-04", "Archive exclusion", "Glob body."),
               body_rec("D-11", "Repository first", "Portable body.")]
    result = dc.select_ruling(records, "archive exclusion")
    assert isinstance(result, dc.Ruling)
    assert result.record.id == "D-04"


# --- authority order and typed conflict --------------------------------

def test_duplicate_in_force_same_scope_returns_typed_conflict():
    records = [
        rec("D-01", rel="docs/decisions/D-01-a.md"),
        rec("D-01", rel="docs/decisions/D-01-b.md"),
    ]
    result = dc.select_ruling(records, "D-01")
    assert isinstance(result, dc.Conflict)
    assert len(result.records) == 2


def test_same_id_two_scopes_without_scope_is_conflict():
    records = [rec("D-01", scope="repo A"), rec("D-01", scope="repo B")]
    result = dc.select_ruling(records, "D-01")
    assert isinstance(result, dc.Conflict)


def test_same_id_two_scopes_with_scope_resolves():
    records = [rec("D-01", scope="repo A"), rec("D-01", scope="repo B")]
    result = dc.select_ruling(records, "D-01", scope="repo B")
    assert isinstance(result, dc.Ruling)
    assert result.record.scope == "repo B"


def test_title_query_against_duplicated_key_is_conflict():
    # Review finding (blocker): the substring path must re-collect every
    # record holding the resolved key, or a title query serves content
    # on an equal-authority tie.
    records = [
        rec("D-01", title="Archive exclusion", scope="repo A", rel="docs/decisions/D-01-a.md"),
        rec("D-01", title="Old naming", scope="repo B", rel="docs/decisions/D-01-b.md"),
    ]
    result = dc.select_ruling(records, "archive exclusion")
    assert isinstance(result, dc.Conflict)


def test_in_force_record_with_replaced_by_is_conflict():
    # An in-force record pointing at a successor is contradictory state;
    # D-09 says stop and report, never resolve silently.
    records = [rec("D-01", replaced_by="D-02"), rec("D-02")]
    result = dc.select_ruling(records, "D-01")
    assert isinstance(result, dc.Conflict)


# --- completed records: keyed reach only (D-26, SPEC-2 s1) --------------

def test_completed_record_reachable_by_exact_key():
    records = [rec("PLAN-0090", title="Closed plan", status="completed",
                   rel="docs/plans/PLAN-0090-test.md")]
    result = dc.select_ruling(records, "PLAN-0090")
    assert isinstance(result, dc.Ruling)
    assert result.record.status == "completed"


def test_completed_record_invisible_to_text_queries():
    # A dead plan must never answer a live free-text question; only its
    # exact key reaches it.
    records = [rec("PLAN-0090", title="Unmistakable closure phrase",
                   status="completed", rel="docs/plans/PLAN-0090-test.md")]
    assert isinstance(
        dc.select_ruling(records, "unmistakable closure"), dc.NoRuling)
    assert isinstance(
        dc.select_ruling(records, "closure phrase test"), dc.NoRuling)


def test_completed_record_under_archive_path_stays_invisible():
    records = [rec("PLAN-0090", status="completed",
                   rel="docs/archive/PLAN-0090-test.md")]
    assert isinstance(dc.select_ruling(records, "PLAN-0090"), dc.NoRuling)


def test_completed_and_in_force_sharing_a_key_is_conflict():
    # One id in two files is contradictory state whatever the statuses;
    # D-09 says stop and report.
    records = [
        rec("PLAN-0090", rel="docs/plans/PLAN-0090-a.md"),
        rec("PLAN-0090", status="completed", rel="docs/plans/PLAN-0090-b.md"),
    ]
    assert isinstance(dc.select_ruling(records, "PLAN-0090"), dc.Conflict)


# --- default visibility -------------------------------------------------

def test_archived_records_are_invisible_to_selection():
    records = [rec("D-01", status="archived", rel="docs/archive/D-01-test.md")]
    result = dc.select_ruling(records, "D-01")
    assert isinstance(result, dc.NoRuling)


def test_draft_records_are_invisible_to_selection():
    result = dc.select_ruling([rec("D-01", status="draft")], "D-01")
    assert isinstance(result, dc.NoRuling)


def test_in_force_status_under_archive_path_is_invisible():
    # D-13 constraint 3 enforced by path, not frontmatter alone: a file
    # under docs/archive/ never reaches default selection, whatever its
    # status field says.
    records = [rec("D-01", rel="docs/archive/D-01-old.md")]
    assert isinstance(dc.select_ruling(records, "D-01"), dc.NoRuling)


def test_empty_query_raises_value_error():
    with pytest.raises(ValueError):
        dc.select_ruling([rec("D-01")], "   ")


# --- loading ------------------------------------------------------------

def test_load_records_reads_tree(tmp_path):
    d = tmp_path / "docs" / "decisions"
    d.mkdir(parents=True)
    (d / "D-90-test.md").write_text(
        "---\nid: D-90\ntitle: T\ntype: decision\nstatus: in-force\n"
        "grade: decided\nowner: t\nscope: s\neffective: 2026-08-01\n"
        "last-verified: 2026-08-01\nsupersedes: none\nreplaced-by: none\n---\n\n"
        "**Status: in-force.**\n",
        encoding="utf-8",
    )
    records = dc.load_records(tmp_path)
    assert [r.id for r in records] == ["D-90"]


def test_governed_files_picks_up_additional_spec_files(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "spec.md").write_text("x", encoding="utf-8")
    (docs / "spec-retrieval-tools.md").write_text("x", encoding="utf-8")
    (docs / "README.md").write_text("x", encoding="utf-8")
    names = [p.name for p in dc.governed_files(tmp_path)]
    assert "spec.md" in names
    assert "spec-retrieval-tools.md" in names
    assert "README.md" not in names
