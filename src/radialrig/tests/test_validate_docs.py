"""Seeded-defect tests for validate_docs.py.

Run:  uv run --with pytest pytest src/radialrig/tests/test_validate_docs.py -q

Each test seeds exactly one defect class into a temp tree and asserts the
matching check fails. A clean tree must pass. Per the verification rule,
every check is seen failing.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

from radialrig import validate_docs as v

TODAY = dt.date(2026, 8, 14)


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
    fm = "\n".join(f"{k}: {val}" for k, val in fields.items())
    first = first_line if first_line is not None else f"**Status: {status}.**"
    return f"---\n{fm}\n---\n\n{first}\n\n## Ruling\n\nTest.\n{body_extra}"


def make_tree(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "decisions").mkdir(parents=True)
    (tmp_path / "docs" / "archive").mkdir(parents=True)
    return tmp_path


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def checks_hit(root: Path, baseline: dict | None = None) -> set[str]:
    return {f.split()[0] for f in v.validate(root, today=TODAY, baseline=baseline or {})}


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root, check=True, capture_output=True,
    )


def commit_all(root: Path) -> None:
    git(root, "init", "-q", "-b", "master")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")


def test_clean_tree_passes(tmp_path):
    root = make_tree(tmp_path)
    write(root, "docs/decisions/D-90-test.md", record())
    assert v.validate(root, today=TODAY, baseline={}) == []


def test_c1_duplicate_in_force(tmp_path):
    root = make_tree(tmp_path)
    write(root, "docs/decisions/D-90-a.md", record())
    write(root, "docs/decisions/D-90-b.md", record())
    assert "C1" in checks_hit(root)


def test_c2_missing_field(tmp_path):
    root = make_tree(tmp_path)
    write(root, "docs/decisions/D-90-test.md", record(omit="owner"))
    assert "C2" in checks_hit(root)


def test_c2_missing_depends_on(tmp_path):
    # D-28: depends-on is required. A record with no dependencies must
    # say so with an explicit 'none', or C2 cannot tell "none" from
    # "never recorded".
    root = make_tree(tmp_path)
    write(root, "docs/decisions/D-90-test.md", record(omit="depends-on"))
    assert "C2" in checks_hit(root)


def test_c2_bad_status_value(tmp_path):
    root = make_tree(tmp_path)
    write(root, "docs/decisions/D-90-test.md", record(status="current"))
    assert "C2" in checks_hit(root)


def not_utf8(text: str) -> bytes:
    """The record as an editor that writes cp1252 would save it."""
    return text.replace("Test.", "Caf\u00e9 said \u201cno\u201d.").encode("cp1252")


def test_c2_names_a_file_that_is_not_utf8_and_checks_the_rest(tmp_path):
    # docs-engine-01: one byte from another encoding used to raise out of
    # the validator, naming nothing. Now the file is named under C2 and
    # every other file is still checked.
    root = make_tree(tmp_path)
    write(root, "docs/decisions/D-90-test.md", record())
    (root / "docs/decisions/D-91-bad.md").write_bytes(not_utf8(record(rid="D-91")))
    write(root, "docs/decisions/D-92-broken.md", record(rid="D-92", omit="owner"))
    assert v.validate(root, today=TODAY, baseline={}) == [
        "C2 (metadata) docs/decisions/D-91-bad.md: file is not valid UTF-8",
        "C2 (metadata) docs/decisions/D-92-broken.md: missing required field 'owner'",
    ]


def test_a_committed_file_that_is_not_utf8_is_no_baseline_and_the_command_names_it(
    tmp_path, monkeypatch, capsys,
):
    # docs-engine-01: the baseline reader decodes the committed copy, so a
    # committed cp1252 file used to raise through it; now that file has no
    # prior state, and the command's own exit line names it.
    root = make_tree(tmp_path)
    write(root, "docs/decisions/D-90-test.md", record())
    (root / "docs/decisions/D-91-bad.md").write_bytes(not_utf8(record(rid="D-91")))
    commit_all(root)
    baseline = v.git_head_baseline(root)
    assert "docs/decisions/D-90-test.md" in baseline
    assert "docs/decisions/D-91-bad.md" not in baseline
    monkeypatch.setattr(
        sys, "argv", ["radialrig-validate", "--root", str(root), "--today", TODAY.isoformat()],
    )
    assert v.main() == 1
    out = capsys.readouterr().out
    assert "C2 (metadata) docs/decisions/D-91-bad.md: file is not valid UTF-8" in out
    assert "FAIL: 1 failure(s) across 2 governed file(s)" in out


def test_c3_wrong_first_line(tmp_path):
    root = make_tree(tmp_path)
    write(root, "docs/decisions/D-90-test.md", record(first_line="## Ruling"))
    assert "C3" in checks_hit(root)


def test_c4_broken_link(tmp_path):
    root = make_tree(tmp_path)
    write(
        root, "docs/decisions/D-90-test.md",
        record(body_extra="\nSee [missing](../nowhere.md).\n"),
    )
    assert "C4" in checks_hit(root)


def test_c4_ignores_links_in_code_spans(tmp_path):
    root = make_tree(tmp_path)
    write(
        root, "docs/decisions/D-90-test.md",
        record(body_extra="\nExample: `**Status: archived.** Replaced by [D-12](...).`\n"),
    )
    assert "C4" not in checks_hit(root)


REL = "docs/decisions/D-90-test.md"


def test_c5_illegal_transition(tmp_path):
    root = make_tree(tmp_path)
    write(root, REL, record(status="draft"))
    assert "C5" in checks_hit(root, {REL: {"status": "completed", "id": "D-90"}})


def test_c5_id_change(tmp_path):
    root = make_tree(tmp_path)
    write(root, REL, record(rid="D-91"))
    assert "C5" in checks_hit(root, {REL: {"status": "in-force", "id": "D-90"}})


def test_c5_new_file_has_no_prior_state(tmp_path):
    """A record absent from the baseline is new. C5 has nothing to compare
    and must not invent a failure."""
    root = make_tree(tmp_path)
    write(root, REL, record())
    assert v.validate(root, today=TODAY, baseline={}) == []


def test_git_baseline_reads_a_corpus_below_the_repository_root(tmp_path):
    """The PLAN-0003 Phase 1 defect, both halves.

    A corpus under `--root` must read its OWN prior state. Before the fix,
    `git show HEAD:<rel>` resolved against the repository root: it found
    nothing for a corpus in a subdirectory (C5 skipped in silence) and
    found the wrong file when a path collided (C5 compared an unrelated
    record). Here the repository root holds a decoy at the same relative
    path, so a resolution against the wrong root cannot pass this test.
    """
    make_tree(tmp_path)
    write(tmp_path, REL, record(rid="DECOY", status="in-force"))
    corpus = make_tree(tmp_path / "sub")
    write(corpus, REL, record(rid="D-90", status="in-force"))
    commit_all(tmp_path)

    baseline = v.git_head_baseline(corpus)
    assert baseline[REL]["id"] == "D-90", "read the decoy at the repository root"

    write(corpus, REL, record(rid="D-91", status="in-force"))
    failures = v.validate(corpus, today=TODAY, baseline=v.git_head_baseline(corpus))
    assert any(f.startswith("C5") and "'D-90' -> 'D-91'" in f for f in failures)


def test_git_baseline_raises_when_git_cannot_answer(tmp_path):
    """No silent empty baseline: an empty mapping means 'every record is
    new', which is the silence this check exists to end."""
    root = make_tree(tmp_path)
    write(root, REL, record())
    try:
        v.git_head_baseline(root)
    except v.BaselineUnavailable:
        return
    raise AssertionError("expected BaselineUnavailable outside a git repository")


def test_c6_stale_last_verified(tmp_path):
    root = make_tree(tmp_path)
    write(root, "docs/decisions/D-90-test.md", record(last_verified="2026-01-01"))
    assert "C6" in checks_hit(root)


def test_c7_archived_outside_archive(tmp_path):
    root = make_tree(tmp_path)
    write(
        root, "docs/decisions/D-90-test.md",
        record(
            status="archived",
            first_line="**Status: archived.** This is a record of a past position, not an instruction.",
        ),
    )
    assert "C7" in checks_hit(root)


def test_c7_nonarchived_inside_archive(tmp_path):
    root = make_tree(tmp_path)
    write(root, "docs/archive/D-90-test.md", record(status="in-force"))
    assert "C7" in checks_hit(root)


def test_c7_archived_voice_line(tmp_path):
    root = make_tree(tmp_path)
    write(
        root, "docs/archive/D-90-test.md",
        record(status="archived", first_line="**Status: archived.**"),
    )
    assert "C7" in checks_hit(root)


# --- Head sentence wrapping (PLAN-0003 Phase 1, gap G8) ----------------
#
# SPEC-1 sections 5 and 6 print the head sentence wrapped across lines.
# An author who copies the spec's own layout must pass C3 and C7; only a
# genuinely missing pointer or fence phrase may fail them.

WRAPPED_HEAD = (
    "**Status: archived.** This is a record of a past position, not an\n"
    "instruction. The current ruling is D-91."
)


def test_c3_c7_accept_a_wrapped_head_sentence(tmp_path):
    root = make_tree(tmp_path)
    write(
        root, "docs/archive/D-90-past.md",
        record(status="archived", replaced_by="D-91", first_line=WRAPPED_HEAD),
    )
    assert v.validate(root, today=TODAY, baseline={}) == []


def test_c3_replaced_by_absent_from_head_still_fails(tmp_path):
    root = make_tree(tmp_path)
    write(
        root, "docs/archive/D-90-past.md",
        record(
            status="archived", replaced_by="D-91",
            first_line=(
                "**Status: archived.** This is a record of a past\n"
                "position, not an instruction."
            ),
        ),
    )
    assert "C3" in checks_hit(root)


def test_c7_fence_phrase_absent_from_wrapped_head_still_fails(tmp_path):
    root = make_tree(tmp_path)
    write(
        root, "docs/archive/D-90-past.md",
        record(
            status="archived", replaced_by="D-91",
            first_line="**Status: archived.** Replaced by D-91. A past\nposition.",
        ),
    )
    assert "C7" in checks_hit(root)



# --- PLAN-0017 phase 7 (tests-04): C2 with no frontmatter block at all --

def test_c2_a_file_whose_first_line_is_not_a_fence(tmp_path):
    # The one C2 arm nothing seeded: a governed file with no opening
    # fence has no frontmatter to check, and C2 names it as such rather
    # than every later check passing over a record that governs nothing.
    root = make_tree(tmp_path)
    write(root, "docs/decisions/D-90-test.md", "# no frontmatter\n" + record())
    assert v.validate(root, today=TODAY, baseline={}) == [
        "C2 (metadata) docs/decisions/D-90-test.md: no parseable frontmatter block",
    ]


def test_c2_a_file_whose_fence_is_never_closed(tmp_path):
    # The same arm from the other side: an opening fence with no closing
    # one parses as no block, not as a block that runs to the end.
    root = make_tree(tmp_path)
    unclosed = record().replace("\n---\n\n", "\n\n", 1)
    assert unclosed.count("---") == 1
    write(root, "docs/decisions/D-90-test.md", unclosed)
    assert v.validate(root, today=TODAY, baseline={}) == [
        "C2 (metadata) docs/decisions/D-90-test.md: no parseable frontmatter block",
    ]
