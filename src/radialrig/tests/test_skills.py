"""Tests for the skills kit — D-36, PLAN-0006 Phase 2.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_skills.py -q

The oracle is the docs kit's: "exactly these paths and nothing else",
compared over a full recursive tree snapshot taken before and after. The
phase gate is the round trip — add then remove leaves the host tree byte
for byte as it was found, in every host shape where removal completes —
so most of what follows is that one property under a different sky.

The second property under test is independence. The skills kit and the
docs kit share a vocabulary and share nothing else: a mounted project
with a skill installed unmounts clean, and a skill installed on a bare
project writes no mount marker.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import radialrig
from radialrig import cli
from radialrig import mount as mnt
from radialrig import skills as sk
from radialrig.tests.test_mount import (  # noqa: F401 - fixtures are used by name
    MOUNT_DAY,
    NEXT_VERSION,
    _make_project,
    snapshot,
    tree,
)

INSTALL_DAY = dt.date(2026, 8, 18)
SKILL = "session-handoff"

# What an install of the one packaged skill writes, restated here as a
# literal. The test must not import the catalog it is checking, or a
# wrong entry in SKILL_CATALOG would agree with itself.
EXPECTED_SKILL_PATHS = {
    ".claude/radialrig-skills.json",
    ".claude/skills/session-handoff/SKILL.md",
    ".claude/skills/session-handoff/scripts/handoff.py",
}

RECORD_REL = ".claude/radialrig-skills.json"
SKILL_DIR = ".claude/skills/session-handoff"


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path / "sample-project")


def record_of(root: Path) -> dict:
    return json.loads((root / RECORD_REL).read_text(encoding="utf-8"))


def codes(dispositions) -> list[str]:
    return [d.code for d in dispositions]


def paths(dispositions) -> list[str]:
    return [d.path for d in dispositions]


# --- a fresh add -------------------------------------------------------

def test_a_fresh_add_writes_exactly_the_declared_paths(project):
    before = snapshot(project)
    result = sk.add(project, SKILL, today=INSTALL_DAY)
    after = snapshot(project)
    assert set(after) - set(before) == EXPECTED_SKILL_PATHS
    assert set(before) - set(after) == set()
    assert result.complete
    assert paths(result.dispositions) == sorted(EXPECTED_SKILL_PATHS)
    assert all(d.verb == mnt.CREATED for d in result.dispositions)


def test_the_record_carries_the_schema_the_paths_and_the_hashes(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    body = record_of(project)
    assert set(body) == {"schema", "radialrig", "harness", "hash", "dirs", "skills"}
    assert body["schema"] == sk.SKILLS_SCHEMA
    assert body["hash"] == mnt.HASH_NAME
    assert body["radialrig"] == radialrig.__version__
    assert body["harness"] == sk.DEFAULT_HARNESS
    # Outermost first, so a reversal can walk it backwards.
    assert body["dirs"] == [".claude", ".claude/skills"]

    entry = body["skills"][0]
    assert entry["name"] == SKILL
    assert entry["installed"] == INSTALL_DAY.isoformat()
    assert entry["dir"] == SKILL_DIR
    assert entry["dir_created"] is True
    for item in entry["files"]:
        assert item["sha256"] == mnt._sha256((project / item["path"]).read_bytes())
    assert {item["path"] for item in entry["files"]} == (
        EXPECTED_SKILL_PATHS - {RECORD_REL}
    )


def test_the_record_does_not_list_itself(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    entry = record_of(project)["skills"][0]
    assert RECORD_REL not in {item["path"] for item in entry["files"]}


def test_the_installed_bytes_are_the_packaged_bytes(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    base = sk.packaged_skills()
    assert base is not None
    for rel in ("SKILL.md", "scripts/handoff.py"):
        theirs = mnt._lf(base.joinpath(SKILL, *rel.split("/")).read_bytes())
        assert (project / SKILL_DIR / rel).read_bytes() == theirs


def test_a_second_add_writes_nothing_at_all(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    before = tree(project)
    again = sk.add(project, SKILL, today=dt.date(2027, 1, 1))
    # The record included: a second timestamp would be a write nobody
    # asked for, and the ethic is the upgrade's — nothing to do, nothing
    # written.
    assert tree(project) == before
    assert again.current
    assert not again.wrote
    assert set(codes(again.dispositions)) == {mnt.SKILL_CURRENT}
    assert all(d.detail == "3.3.0" for d in again.dispositions)


def test_a_plan_writes_nothing_and_matches_the_run(project):
    before = tree(project)
    plan = sk.add_plan(project, SKILL, today=INSTALL_DAY)
    assert tree(project) == before
    real = sk.add(project, SKILL, today=INSTALL_DAY)
    assert plan.dispositions == real.dispositions
    assert plan.payload == real.payload
    assert plan.record_after == real.record_after


def test_an_unknown_name_names_what_the_package_ships(project):
    with pytest.raises(sk.UnknownSkill) as exc:
        sk.add(project, "no-such-skill", today=INSTALL_DAY)
    assert exc.value.available == sk.catalog_names()
    assert SKILL in str(exc.value)
    assert not (project / ".claude").exists()


# --- a newer release ---------------------------------------------------

@pytest.fixture()
def next_release(tmp_path, monkeypatch):
    """Install a newer radialrig: a copy of the skill sources, one edit,
    a version. Only one version of the package is importable, so the
    technique is test_upgrade.py's — point the accessor at a copy."""
    def install(edit=None, version=NEXT_VERSION) -> Path:
        base = sk.packaged_skills()
        assert base is not None
        target = tmp_path / "next-skills"
        for source in sorted(base.rglob("*")):
            if source.is_file():
                copy = target / source.relative_to(base)
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(source.read_bytes())
        if edit is not None:
            edit(target)
        monkeypatch.setattr(radialrig, "__version__", version)
        monkeypatch.setattr(sk, "packaged_skills", lambda: target)
        return target

    return install


def edit_skill(package_root: Path) -> None:
    path = package_root / SKILL / "SKILL.md"
    path.write_bytes(path.read_bytes() + b"\nA line the newer release added.\n")


def test_a_newer_release_refreshes_an_unedited_skill(project, next_release):
    sk.add(project, SKILL, today=INSTALL_DAY)
    package = next_release(edit_skill)

    result = sk.add(project, SKILL, today=dt.date(2026, 9, 2))
    assert result.complete
    assert result.wrote
    by_path = {d.path: d for d in result.dispositions}
    refreshed = by_path[f"{SKILL_DIR}/SKILL.md"]
    assert (refreshed.verb, refreshed.code) == (mnt.UPDATED, mnt.SKILL_REFRESHED)
    assert (project / SKILL_DIR / "SKILL.md").read_bytes() == (
        package / SKILL / "SKILL.md"
    ).read_bytes()

    body = record_of(project)
    entry = body["skills"][0]
    assert body["radialrig"] == NEXT_VERSION
    assert entry["installed"] == "2026-09-02"
    for item in entry["files"]:
        assert item["sha256"] == mnt._sha256((project / item["path"]).read_bytes())


def test_a_crlf_package_installs_lf_and_hashes_the_same(project, next_release):
    def to_crlf(package_root: Path) -> None:
        path = package_root / SKILL / "SKILL.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    package = next_release(to_crlf, version=radialrig.__version__)
    sk.add(project, SKILL, today=INSTALL_DAY)
    installed = (project / SKILL_DIR / "SKILL.md").read_bytes()
    assert b"\r\n" not in installed
    assert installed == mnt._lf((package / SKILL / "SKILL.md").read_bytes())
    entry = record_of(project)["skills"][0]
    recorded = {item["path"]: item["sha256"] for item in entry["files"]}
    assert recorded[f"{SKILL_DIR}/SKILL.md"] == mnt._sha256(installed)


def flip_installed(root: Path) -> None:
    """What a checkout with `core.autocrlf=true` does to the installed
    skill files (BUG-0011)."""
    for rel in ("SKILL.md", "scripts/handoff.py"):
        path = root / SKILL_DIR / rel
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


def test_a_crlf_checkout_of_an_installed_skill_is_not_an_edit(project):
    # BUG-0011: the list still reads the skill as current, a second add
    # writes nothing, and remove takes the files out.
    before = tree(project)
    sk.add(project, SKILL, today=INSTALL_DAY)
    flip_installed(project)
    row = sk.skill_state(project, SKILL).list_disposition()
    assert (row.verb, row.code) == (mnt.CURRENT, mnt.SKILL_CURRENT)
    assert not sk.add(project, SKILL, today=INSTALL_DAY).wrote
    plan = sk.remove(project, SKILL)
    assert plan.complete, [(d.verb, d.path, d.code) for d in plan.dispositions]
    assert tree(project) == before


def test_a_newer_release_refreshes_a_crlf_flipped_skill(project, next_release):
    sk.add(project, SKILL, today=INSTALL_DAY)
    flip_installed(project)
    package = next_release(edit_skill)
    result = sk.add(project, SKILL, today=dt.date(2026, 9, 2))
    assert result.complete and result.wrote
    refreshed = (project / SKILL_DIR / "SKILL.md").read_bytes()
    assert refreshed == (package / SKILL / "SKILL.md").read_bytes()
    assert b"\r\n" not in refreshed


def test_a_crlf_skill_file_with_a_real_edit_is_still_edited(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    path = project / SKILL_DIR / "SKILL.md"
    path.write_bytes((path.read_bytes() + b"\nours\n").replace(b"\n", b"\r\n"))
    ours = path.read_bytes()
    assert sk.skill_state(project, SKILL).list_disposition().code == mnt.SKILL_EDITED
    plan = sk.remove(project, SKILL)
    assert not plan.complete
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[f"{SKILL_DIR}/SKILL.md"].code == mnt.SKILL_EDITED_KEPT
    assert path.read_bytes() == ours


# --- refusals ----------------------------------------------------------

def test_a_foreign_directory_refuses_before_the_first_byte(project):
    (project / SKILL_DIR).mkdir(parents=True)
    (project / SKILL_DIR / "ours.md").write_bytes(b"our own skill\n")
    before = tree(project)
    with pytest.raises(sk.SkillConflict) as exc:
        sk.add(project, SKILL, today=INSTALL_DAY)
    assert codes(exc.value.conflicts) == [mnt.SKILL_DIR_FOREIGN]
    assert tree(project) == before


def test_a_file_where_the_directory_goes_refuses(project):
    (project / ".claude" / "skills").mkdir(parents=True)
    (project / SKILL_DIR).write_bytes(b"not a directory\n")
    before = tree(project)
    with pytest.raises(sk.SkillConflict) as exc:
        sk.add(project, SKILL, today=INSTALL_DAY)
    assert codes(exc.value.conflicts) == [mnt.SKILL_TARGET_NOT_A_DIR]
    assert tree(project) == before


def test_a_file_on_the_way_to_the_directory_refuses(project):
    (project / ".claude").mkdir()
    (project / ".claude" / "skills").write_bytes(b"not a directory\n")
    before = tree(project)
    with pytest.raises(sk.SkillConflict) as exc:
        sk.add(project, SKILL, today=INSTALL_DAY)
    assert paths(exc.value.conflicts) == [".claude/skills"]
    assert tree(project) == before


def test_an_edited_file_stops_the_add_and_prints_a_diff(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    (project / SKILL_DIR / "SKILL.md").write_bytes(b"# ours now\n")
    before = tree(project)
    result = sk.add(project, SKILL, today=dt.date(2026, 9, 2))
    assert not result.wrote
    assert result.edited
    assert tree(project) == before
    assert result.left == len(result.dispositions)
    assert set(codes(result.dispositions)) == {mnt.SKILL_EDITED}
    assert result.diffs and "# ours now" in result.diffs[0]


def test_one_edited_file_leaves_the_whole_skill_alone(project, next_release):
    # Skill-level all-or-nothing: a refreshed SKILL.md beside an untouched
    # script is a broken skill, so one edited file stops both writes.
    sk.add(project, SKILL, today=INSTALL_DAY)
    next_release(edit_skill)
    (project / SKILL_DIR / "scripts" / "handoff.py").write_bytes(b"# ours\n")
    before = tree(project)
    result = sk.add(project, SKILL, today=dt.date(2026, 9, 2))
    assert not result.wrote
    assert tree(project) == before
    assert sorted(paths(result.dispositions)) == sorted(
        EXPECTED_SKILL_PATHS - {RECORD_REL}
    )
    assert set(codes(result.dispositions)) == {mnt.SKILL_EDITED}


def _narrowed_catalog(name: str, files: tuple[str, ...]):
    """The catalog with one skill shipping only `files`: an earlier
    release, as far as the record is concerned."""
    return tuple(
        dataclasses.replace(entry, files=files) if entry.name == name else entry
        for entry in sk.SKILL_CATALOG
    )


def test_an_unrecorded_file_where_the_package_now_ships_one_refuses(
    project, monkeypatch,
):
    # skills-roster-03: a file this project put inside the skill directory,
    # at a path a later release ships, is the project's own. It is not
    # written over and reported as a refresh.
    full = sk.SKILL_CATALOG
    monkeypatch.setattr(sk, "SKILL_CATALOG", _narrowed_catalog(SKILL, ("SKILL.md",)))
    sk.add(project, SKILL, today=INSTALL_DAY)
    own = project / SKILL_DIR / "scripts" / "handoff.py"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"MY OWN NOTES\n")
    monkeypatch.setattr(sk, "SKILL_CATALOG", full)
    before = tree(project)
    with pytest.raises(sk.SkillConflict) as exc:
        sk.add(project, SKILL, today=INSTALL_DAY)
    assert paths(exc.value.conflicts) == [f"{SKILL_DIR}/scripts/handoff.py"]
    assert codes(exc.value.conflicts) == [mnt.SKILL_FILE_FOREIGN]
    assert tree(project) == before
    assert own.read_bytes() == b"MY OWN NOTES\n"


def test_an_unrecorded_file_is_refused_under_a_dotted_dir_spelling(
    project, monkeypatch,
):
    # The same file under a record `dir` spelt `.claude/skills/./x`: the
    # on-disk scan and the record must agree on one spelling, or the
    # refusal above never sees the file.
    full = sk.SKILL_CATALOG
    monkeypatch.setattr(sk, "SKILL_CATALOG", _narrowed_catalog(SKILL, ("SKILL.md",)))
    sk.add(project, SKILL, today=INSTALL_DAY)
    own = project / SKILL_DIR / "scripts" / "handoff.py"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"MY OWN NOTES\n")
    body = record_of(project)
    body["skills"][0]["dir"] = ".claude/skills/./session-handoff"
    (project / RECORD_REL).write_bytes(mnt._dump_manifest(body))
    monkeypatch.setattr(sk, "SKILL_CATALOG", full)
    before = tree(project)
    with pytest.raises(sk.SkillConflict) as exc:
        sk.add(project, SKILL, today=INSTALL_DAY)
    assert paths(exc.value.conflicts) == [f"{SKILL_DIR}/scripts/handoff.py"]
    assert tree(project) == before
    assert own.read_bytes() == b"MY OWN NOTES\n"


def test_an_unrecorded_file_identical_to_the_package_is_refreshed(
    project, monkeypatch,
):
    # The allowance: bytes that already are the packaged bytes belong to
    # nobody in particular, so the add records them and carries on.
    full = sk.SKILL_CATALOG
    monkeypatch.setattr(sk, "SKILL_CATALOG", _narrowed_catalog(SKILL, ("SKILL.md",)))
    sk.add(project, SKILL, today=INSTALL_DAY)
    own = project / SKILL_DIR / "scripts" / "handoff.py"
    own.parent.mkdir(parents=True)
    own.write_bytes(sk._skill_bytes(SKILL, "scripts/handoff.py"))
    monkeypatch.setattr(sk, "SKILL_CATALOG", full)
    result = sk.add(project, SKILL, today=INSTALL_DAY)
    by_path = {d.path: d.code for d in result.dispositions}
    assert by_path[f"{SKILL_DIR}/scripts/handoff.py"] == mnt.SKILL_REFRESHED
    recorded = {f["path"] for f in record_of(project)["skills"][0]["files"]}
    assert recorded == EXPECTED_SKILL_PATHS - {RECORD_REL}


def test_a_write_that_fails_part_way_leaves_the_tree_as_found(project, monkeypatch):
    before = tree(project)
    real = Path.write_bytes

    def refuse(self: Path, data: bytes):
        if self.name == "SKILL.md":
            raise OSError("no room")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", refuse)
    with pytest.raises(sk.SkillError):
        sk.add(project, SKILL, today=INSTALL_DAY)
    monkeypatch.undo()
    assert tree(project) == before


# --- the round trip, over the host shapes ------------------------------

def bare(root: Path) -> None:
    pass


def has_dot_claude(root: Path) -> None:
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_bytes(b"{}\n")


def has_claude_skills(root: Path) -> None:
    (root / ".claude" / "skills").mkdir(parents=True)


def has_empty_skill_dir(root: Path) -> None:
    (root / SKILL_DIR).mkdir(parents=True)


def has_another_skill_dir(root: Path) -> None:
    (root / ".claude" / "skills" / "their-skill").mkdir(parents=True)
    (root / ".claude" / "skills" / "their-skill" / "SKILL.md").write_bytes(b"theirs\n")


def has_user_file_in_skill_dir(root: Path) -> None:
    (root / SKILL_DIR).mkdir(parents=True)
    (root / SKILL_DIR / "notes.md").write_bytes(b"ours\n")


def claude_skills_is_a_file(root: Path) -> None:
    (root / ".claude").mkdir()
    (root / ".claude" / "skills").write_bytes(b"not a directory\n")


def docs_kit_mounted(root: Path) -> None:
    mnt.mount(root, today=MOUNT_DAY)


def docs_kit_mounted_then_unmounted(root: Path) -> None:
    mnt.mount(root, today=MOUNT_DAY)
    mnt.unmount(root)


def crlf_checkout(root: Path) -> None:
    (root / "README.md").write_bytes(b"# Sample\r\n\r\nCRLF in this checkout.\r\n")


SHAPES = {
    "bare": bare,
    "has-dot-claude": has_dot_claude,
    "has-claude-skills": has_claude_skills,
    "has-empty-skill-dir": has_empty_skill_dir,
    "has-another-skill-dir": has_another_skill_dir,
    "has-user-file-in-skill-dir": has_user_file_in_skill_dir,
    "claude-skills-is-a-file": claude_skills_is_a_file,
    "docs-kit-mounted": docs_kit_mounted,
    "docs-kit-mounted-then-unmounted": docs_kit_mounted_then_unmounted,
    "crlf-checkout": crlf_checkout,
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("harness_name", sorted(sk.HARNESSES))
def test_add_then_remove_leaves_the_tree_as_it_was(tmp_path, shape, harness_name):
    harness = sk.HARNESSES[harness_name]
    root = _make_project(tmp_path / "sample-project")
    SHAPES[shape](root)
    before = tree(root)

    try:
        sk.add(root, SKILL, harness=harness, today=INSTALL_DAY)
    except sk.SkillConflict:
        # A refusal is a round trip of its own: nothing was written, so
        # there is nothing to reverse.
        assert tree(root) == before
        return

    assert tree(root) != before
    plan = sk.remove(root, SKILL, harness=harness)
    assert plan.complete, [(d.verb, d.path, d.code) for d in plan.dispositions]
    assert tree(root) == before


def test_a_pre_existing_empty_directory_survives_the_round_trip(project):
    (project / SKILL_DIR).mkdir(parents=True)
    before = tree(project)
    sk.add(project, SKILL, today=INSTALL_DAY)
    assert record_of(project)["skills"][0]["dir_created"] is False
    plan = sk.remove(project, SKILL)
    assert plan.complete
    assert tree(project) == before
    assert (project / SKILL_DIR).is_dir()
    # The directory the project already had gets no line: the kit did not
    # create it and did not touch it.
    assert f"{SKILL_DIR}/" not in paths(plan.dispositions)


# --- remove ------------------------------------------------------------

def test_remove_on_a_project_with_no_record_is_a_no_op(project):
    before = tree(project)
    plan = sk.remove(project, SKILL)
    assert not plan.installed
    assert plan.dispositions == ()
    assert tree(project) == before


def test_remove_leaves_an_edited_file_and_drops_the_entry(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    (project / SKILL_DIR / "SKILL.md").write_bytes(b"# ours now\n")
    plan = sk.remove(project, SKILL)
    assert not plan.complete
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[f"{SKILL_DIR}/SKILL.md"].code == mnt.SKILL_EDITED_KEPT
    assert by_path[f"{SKILL_DIR}/scripts/handoff.py"].code == mnt.REMOVED_FILE
    assert by_path[f"{SKILL_DIR}/"].code == mnt.LEFT_DIR_OCCUPIED
    assert by_path[f"{SKILL_DIR}/"].detail == "1"
    # The entry goes even though a file stayed: the file is now the
    # project's own, and a record claiming it would be a lie.
    assert not (project / RECORD_REL).exists()
    assert (project / SKILL_DIR / "SKILL.md").read_bytes() == b"# ours now\n"


def test_remove_lists_a_file_this_project_added(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    (project / SKILL_DIR / "notes.md").write_bytes(b"mine\n")
    plan = sk.remove(project, SKILL)
    assert not plan.complete
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[f"{SKILL_DIR}/notes.md"].code == mnt.SKILL_UNRECORDED
    assert by_path[f"{SKILL_DIR}/"].code == mnt.LEFT_DIR_OCCUPIED
    assert (project / SKILL_DIR / "notes.md").read_bytes() == b"mine\n"


def test_a_stale_record_removes_cleanly(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    for rel in ("SKILL.md", "scripts/handoff.py"):
        (project / SKILL_DIR / rel).unlink()
    plan = sk.remove(project, SKILL)
    assert plan.complete
    assert set(codes(plan.dispositions)) == {
        mnt.ABSENT_ALREADY_GONE, mnt.REMOVED_DIR, mnt.SKILL_RECORD_DROPPED,
    }
    assert not (project / ".claude").exists()


def test_remove_follows_the_record_not_todays_map(project, tmp_path):
    # A skill an older release installed elsewhere under the harness tree
    # is removed from where that release put it.
    sk.add(project, SKILL, today=INSTALL_DAY)
    moved = ".claude/skills/legacy-place"
    (project / moved).mkdir(parents=True)
    (project / moved / "SKILL.md").write_bytes(
        (project / SKILL_DIR / "SKILL.md").read_bytes()
    )
    body = record_of(project)
    entry = body["skills"][0]
    entry["dir"] = moved
    entry["files"] = [{
        "path": f"{moved}/SKILL.md",
        "sha256": mnt._sha256((project / moved / "SKILL.md").read_bytes()),
    }]
    (project / RECORD_REL).write_bytes(mnt._dump_manifest(body))

    plan = sk.remove(project, SKILL)
    assert plan.removals == tuple(sorted((f"{moved}/SKILL.md", RECORD_REL)))
    assert not (project / moved).exists()
    # The files today's map points at are untouched: they are not the
    # bytes this record says radialrig wrote.
    assert (project / SKILL_DIR / "SKILL.md").is_file()


# --- two skills --------------------------------------------------------

OTHER = "other-skill"


@pytest.fixture()
def two_skills(tmp_path, monkeypatch):
    """A package shipping a second skill, so the shared-directory and
    record-rewrite paths have something to share."""
    base = sk.packaged_skills()
    assert base is not None
    target = tmp_path / "two-skills"
    for source in sorted(base.rglob("*")):
        if source.is_file():
            copy = target / source.relative_to(base)
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(source.read_bytes())
    (target / OTHER).mkdir(parents=True, exist_ok=True)
    (target / OTHER / "SKILL.md").write_bytes(b"---\nname: other-skill\n---\n\nOther.\n")
    monkeypatch.setattr(sk, "packaged_skills", lambda: target)
    monkeypatch.setattr(sk, "SKILL_CATALOG", (
        *sk.SKILL_CATALOG,
        sk.SkillEntry(name=OTHER, version="1.0.0", summary="A second skill.",
                      files=("SKILL.md",)),
    ))
    return target


def test_a_second_add_merges_the_record_and_keeps_the_directories(project, two_skills):
    sk.add(project, SKILL, today=INSTALL_DAY)
    sk.add(project, OTHER, today=INSTALL_DAY)
    body = record_of(project)
    assert [e["name"] for e in body["skills"]] == sorted([SKILL, OTHER])
    assert body["dirs"] == [".claude", ".claude/skills"]


def test_removing_one_of_two_keeps_the_record_and_says_the_directory_is_shared(
    project, two_skills,
):
    sk.add(project, SKILL, today=INSTALL_DAY)
    after_first = tree(project)
    sk.add(project, OTHER, today=INSTALL_DAY)

    plan = sk.remove(project, OTHER)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[".claude/skills/"].code == mnt.SKILL_DIR_SHARED
    assert by_path[".claude/"].code == mnt.SKILL_DIR_SHARED
    assert by_path[RECORD_REL].code == mnt.SKILL_RECORD_WRITTEN
    assert [e["name"] for e in record_of(project)["skills"]] == [SKILL]
    assert not plan.complete  # the shared directories stay, and say so

    files_before, dirs_before = after_first
    files_now, dirs_now = tree(project)
    assert files_now == files_before
    assert dirs_now == dirs_before


def test_no_byte_this_project_wrote_reaches_the_record(project, two_skills):
    marker = "THE-PROJECTS-OWN-BYTES"
    sk.add(project, SKILL, today=INSTALL_DAY)
    sk.add(project, OTHER, today=INSTALL_DAY)
    (project / SKILL_DIR / "SKILL.md").write_bytes(marker.encode("utf-8"))
    sk.remove(project, SKILL)
    assert marker not in (project / RECORD_REL).read_text(encoding="utf-8")


# --- record hardening --------------------------------------------------

def test_a_record_that_does_not_parse_stops_before_any_write(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    (project / RECORD_REL).write_bytes(b"{not json\n")
    before = tree(project)
    with pytest.raises(sk.SkillError) as exc:
        sk.remove(project, SKILL)
    assert "does not parse as JSON" in str(exc.value)
    assert tree(project) == before


def test_a_newer_schema_stops_before_any_write(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    body = record_of(project)
    body["schema"] = sk.SKILLS_SCHEMA + 1
    (project / RECORD_REL).write_bytes(mnt._dump_manifest(body))
    before = tree(project)
    with pytest.raises(sk.SkillError) as exc:
        sk.remove(project, SKILL)
    assert "newer radialrig" in str(exc.value)
    assert tree(project) == before


def test_a_recorded_path_outside_the_project_stops_before_any_write(
    project, tmp_path,
):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host property\n")
    sk.add(project, SKILL, today=INSTALL_DAY)
    body = record_of(project)
    body["skills"][0]["files"].append({
        "path": "../outside.txt",
        "sha256": mnt._sha256(b"host property\n"),
    })
    (project / RECORD_REL).write_bytes(mnt._dump_manifest(body))
    before = tree(project)
    with pytest.raises(sk.SkillError) as exc:
        sk.remove(project, SKILL)
    assert "outside this project" in str(exc.value)
    assert tree(project) == before
    assert outside.read_bytes() == b"host property\n"


@pytest.mark.parametrize("spelling", [
    "docs/spec.md",
    ".claude/skills/session-handoff/../../../docs/spec.md",
    ".claude/skills/session-handoff/.. /.. /.. /docs/spec.md",
    ".claude\\skills\\session-handoff\\..\\..\\..\\docs\\spec.md",
])
def test_a_recorded_path_outside_the_harness_tree_stops_before_any_write(
    project, spelling,
):
    # The guard that matters most: a record naming docs/spec.md must not
    # turn `skill remove` into a deletion of the rulebook, however the
    # path is spelt. The `..` spelling starts with the skills directory
    # and resolves to the rulebook (skills-roster-01).
    mnt.mount(project, today=MOUNT_DAY)
    sk.add(project, SKILL, today=INSTALL_DAY)
    body = record_of(project)
    body["skills"][0]["files"].append({
        "path": spelling,
        "sha256": mnt._sha256((project / "docs" / "spec.md").read_bytes()),
    })
    (project / RECORD_REL).write_bytes(mnt._dump_manifest(body))
    before = tree(project)
    with pytest.raises(sk.SkillError) as exc:
        sk.remove(project, SKILL)
    assert "outside .claude/skills" in str(exc.value)
    assert spelling in str(exc.value)
    assert RECORD_REL in str(exc.value)
    assert tree(project) == before
    assert (project / "docs" / "spec.md").is_file()


def test_a_recorded_dir_that_climbs_out_of_the_harness_tree_stops_the_add(project):
    # skills-roster-01, the other direction: a `dir` that climbs out of
    # the skills directory would make `skill add` write the packaged
    # payload over the project's own files.
    (project / "docs").mkdir()
    (project / "docs" / "SKILL.md").write_bytes(b"HOST CONTENT\n")
    sk.add(project, SKILL, today=INSTALL_DAY)
    body = record_of(project)
    body["skills"][0]["dir"] = ".claude/skills/../../docs"
    body["skills"][0]["files"] = []
    (project / RECORD_REL).write_bytes(mnt._dump_manifest(body))
    before = tree(project)
    with pytest.raises(sk.SkillError) as exc:
        sk.add(project, SKILL, today=INSTALL_DAY)
    assert "outside .claude/skills" in str(exc.value)
    assert ".claude/skills/../../docs" in str(exc.value)
    assert tree(project) == before
    assert (project / "docs" / "SKILL.md").read_bytes() == b"HOST CONTENT\n"


def test_a_recorded_directory_that_climbs_out_stops_before_any_write(project):
    # A `dirs` entry may name an ancestor of the skills directory, never
    # a path that climbs: `.claude/skills/../..` is the project root.
    sk.add(project, SKILL, today=INSTALL_DAY)
    body = record_of(project)
    body["dirs"].append(".claude/skills/../..")
    (project / RECORD_REL).write_bytes(mnt._dump_manifest(body))
    before = tree(project)
    with pytest.raises(sk.SkillError) as exc:
        sk.remove(project, SKILL)
    assert ".claude/skills/../.." in str(exc.value)
    assert RECORD_REL in str(exc.value)
    assert tree(project) == before


def test_a_recorded_directory_outside_the_harness_tree_stops_before_any_write(
    project,
):
    sk.add(project, SKILL, today=INSTALL_DAY)
    body = record_of(project)
    body["dirs"].append("src")
    (project / RECORD_REL).write_bytes(mnt._dump_manifest(body))
    before = tree(project)
    with pytest.raises(sk.SkillError):
        sk.remove(project, SKILL)
    assert tree(project) == before
    assert (project / "src").is_dir()


# --- the state model ---------------------------------------------------

def test_a_skill_this_package_no_longer_ships_still_lists_and_removes(
    project, two_skills, monkeypatch,
):
    sk.add(project, OTHER, today=INSTALL_DAY)
    # The next release drops it from the catalog. The record still names
    # it, so it is still visible and still removable.
    monkeypatch.setattr(sk, "SKILL_CATALOG", tuple(
        e for e in sk.SKILL_CATALOG if e.name != OTHER
    ))
    found = {s.name: s for s in sk.states(project)}
    assert found[OTHER].list_disposition().code == mnt.SKILL_NOT_PACKAGED
    plan = sk.remove(project, OTHER)
    assert plan.complete
    assert not (project / ".claude").exists()


def test_the_list_row_of_each_state(project):
    absent = sk.skill_state(project, SKILL)
    row = absent.list_disposition()
    assert (row.verb, row.code, row.detail) == (
        mnt.ABSENT, mnt.SKILL_NOT_INSTALLED, "3.3.0",
    )
    assert mnt.reason_text(row) == "3.3.0 available in this package; not installed here"

    sk.add(project, SKILL, today=INSTALL_DAY)
    row = sk.skill_state(project, SKILL).list_disposition()
    assert (row.verb, row.code) == (mnt.CURRENT, mnt.SKILL_CURRENT)
    assert mnt.reason_text(row) == "3.3.0 already matches this package"

    (project / SKILL_DIR / "SKILL.md").write_bytes(b"# ours\n")
    assert sk.skill_state(project, SKILL).list_disposition().code == mnt.SKILL_EDITED

    for rel in ("SKILL.md", "scripts/handoff.py"):
        (project / SKILL_DIR / rel).unlink()
    assert sk.skill_state(project, SKILL).list_disposition().code == (
        mnt.SKILL_RECORD_STALE
    )


def test_the_list_row_of_an_installed_skill_the_package_outgrew(
    project, next_release
):
    """Installed, unedited, and the package ships different bytes: the row
    must not claim the skill matches this package."""
    sk.add(project, SKILL, today=INSTALL_DAY)
    next_release(edit_skill)
    row = sk.skill_state(project, SKILL).list_disposition()
    assert (row.verb, row.code) == (mnt.LEFT, mnt.SKILL_REFRESHABLE)
    assert mnt.reason_text(row) == (
        "3.3.0 installed; this package ships a newer release"
    )


def test_a_foreign_directory_lists_without_writing(project):
    (project / SKILL_DIR).mkdir(parents=True)
    (project / SKILL_DIR / "ours.md").write_bytes(b"ours\n")
    before = tree(project)
    state = sk.skill_state(project, SKILL)
    assert state.foreign
    assert state.list_disposition().code == mnt.SKILL_DIR_FOREIGN
    assert tree(project) == before


def test_every_disposition_a_verb_produces_carries_a_closed_verb(project):
    produced = []
    produced.extend(sk.add(project, SKILL, today=INSTALL_DAY).dispositions)
    produced.extend(s.list_disposition() for s in sk.states(project))
    (project / SKILL_DIR / "notes.md").write_bytes(b"mine\n")
    produced.extend(sk.remove(project, SKILL).dispositions)
    assert produced
    for d in produced:
        assert d.verb in mnt.VERBS, d
        assert mnt.reason_text(d) is not None
        assert "{" not in mnt.reason_text(d), d


# --- independence from the docs kit ------------------------------------

def test_an_installed_skill_does_not_disturb_the_docs_round_trip(project):
    before = tree(project)
    files_before = snapshot(project)
    mnt.mount(project, today=MOUNT_DAY)
    sk.add(project, SKILL, today=INSTALL_DAY)
    plan = mnt.unmount(project)
    assert plan.complete, [(d.verb, d.path, d.code) for d in plan.dispositions]
    assert not (project / ".radialrig").exists()
    # The skill is untouched by the docs kit's reversal.
    assert (project / RECORD_REL).is_file()
    assert set(snapshot(project)) - set(files_before) == EXPECTED_SKILL_PATHS

    sk.remove(project, SKILL)
    assert tree(project) == before


def test_an_add_writes_no_mount_marker(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    assert not (project / ".radialrig").exists()
    assert not (project / "docs").exists()
    assert not (project / mnt.MANIFEST_REL).exists()
    assert not (project / mnt.AGENTS_REL).exists()
    with pytest.raises(mnt.NotMounted):
        mnt.read_manifest(project)


def test_the_docs_manifest_is_untouched_by_an_add(project):
    mnt.mount(project, today=MOUNT_DAY)
    before = (project / mnt.MANIFEST_REL).read_bytes()
    sk.add(project, SKILL, today=INSTALL_DAY)
    assert (project / mnt.MANIFEST_REL).read_bytes() == before


# --- the shared vocabulary, unchanged by the sub-block -----------------

def test_the_detail_rule_leaves_every_docs_kit_wording_alone():
    # The `{detail}` generalization is behavior-preserving only while no
    # wording written before it carries a brace. This is that guarantee,
    # stated once rather than inferred from a green suite.
    for code, wording in mnt.REASONS.items():
        # The two kits that arrived after the rule use it deliberately, so
        # does the one upgrade wording that names a partner path
        # (UPGRADE_HELD, BUG-0004), and so do the `.mcp.json` wordings
        # that name the keys they added or removed once a mount registered
        # two servers (PLAN-0012) or judged a removal (PLAN-0017 phase 2),
        # and the two that name the claims a record makes that no mount
        # writes (PLAN-0022 phase 2); every wording written before the
        # rule still carries no brace.
        keyed = (
            mnt.UPGRADE_HELD, mnt.MOUNT_MERGED, mnt.MOUNT_MERGED_RESERIALIZED,
            mnt.MOUNT_MERGED_PARTIAL, mnt.LEFT_MCP_RESERIALIZED,
            mnt.UPGRADE_ENTRY_NEW_WRITTEN, mnt.UPGRADE_ENTRY_NEW_LEFT,
            mnt.UPGRADE_ENTRY_NEW_PENDING, mnt.UPGRADE_ENTRY_REMOVED,
            mnt.UPGRADE_ENTRY_HELD_REMOVED, mnt.LEFT_MCP_UNTRUSTED,
            mnt.LEFT_MCP_NOT_ALL_OURS,
        )
        if code.startswith(("SKILL_", "CONNECT_")) or code in keyed:
            continue
        assert "{" not in wording, code
        if code == mnt.LEFT_DIR_OCCUPIED:
            continue  # its own counted wording, untouched by this phase
        for detail in ("", "3"):
            assert mnt.reason_text(mnt.Disposition("p", mnt.LEFT, code, detail)) == (
                wording
            )


def test_every_skills_wording_resolves_and_names_no_skill():
    for code, wording in mnt.REASONS.items():
        if not code.startswith("SKILL_"):
            continue
        text = mnt.reason_text(mnt.Disposition("p", mnt.LEFT, code, "3.0.0"))
        assert "{" not in text, code
        for name in sk.catalog_names():
            assert name not in wording, code


# --- the harness map ---------------------------------------------------

def test_no_function_branches_on_a_harness_name():
    source = Path(sk.__file__).read_text(encoding="utf-8")
    # Once each, in the map literal. A second occurrence is a branch
    # waiting to happen, and D-34 rule 2 keeps the engine harness-agnostic.
    for name in sk.HARNESSES:
        assert source.count(f'"{name}"') == 1, name


def test_every_harness_declares_a_record_beside_its_skills_directory():
    for name, harness in sk.HARNESSES.items():
        assert harness.name == name
        assert not harness.record.startswith(harness.skills + "/")
        assert Path(harness.record).parent != Path(".")


def test_no_packaged_skill_source_carries_a_token():
    base = sk.packaged_skills()
    assert base is not None
    for entry in sk.SKILL_CATALOG:
        for rel in entry.files:
            data = base.joinpath(entry.name, *rel.split("/")).read_bytes()
            assert b"{{" not in data, f"{entry.name}/{rel} carries a token"


# --- PLAN-0017 phase 2: a fault in the writer is this kit's own error ---

def test_a_locked_file_under_remove_is_the_kits_own_error(project, monkeypatch):
    sk.add(project, SKILL, today=INSTALL_DAY)
    before = tree(project)
    real = Path.unlink

    def refuse(self, *args, **kwargs):
        if self.name == "SKILL.md":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    with pytest.raises(sk.SkillError) as caught:
        sk.remove(project, SKILL)
    assert "Permission denied" in str(caught.value)
    assert str(caught.value).endswith("nothing was written")
    assert tree(project) == before


def test_a_fault_in_the_add_writer_carries_the_writers_sentence(project, monkeypatch):
    # A restore that fails leaves a path changed, and the kit's error is
    # the writer's own sentence naming it, not a fixed tail claiming
    # nothing was written.
    other = "plan-review"  # a second packaged skill, restated, not imported
    claude = sk.harness_for("claude-code")
    sk.add(project, SKILL, harness=claude, today=INSTALL_DAY)
    record = project / RECORD_REL
    original = record.read_bytes()
    real = Path.write_bytes
    stopped = [False]

    def flaky(self, data):
        # The record is rewritten with the second skill and lands; the
        # skill file after it refuses; the record's restore refuses too.
        if self.name == "SKILL.md" and ".claude" in self.parts:
            stopped[0] = True
            raise PermissionError(13, "Permission denied", str(self))
        if self == record and stopped[0]:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    with pytest.raises(sk.SkillError) as caught:
        sk.add_all(project, other, today=INSTALL_DAY)
    said = str(caught.value)
    assert "Permission denied" in said
    assert said.endswith(f"put back except: {RECORD_REL}")
    assert "nothing was written" not in said
    assert record.read_bytes() != original
    assert not (project / ".claude" / "skills" / other).exists()


# --- PLAN-0017 phase 5: one version on disk, refusals in words ---------

def drop_scripts(package_root: Path) -> None:
    """The next release ships SKILL.md alone: the scripts are gone."""
    shutil.rmtree(package_root / SKILL / "scripts")


def as_utf16(root: Path, harness: sk.Harness | None = None) -> Path:
    """The record as a Windows editor defaulting to UTF-16 leaves it."""
    harness = harness or sk.default_harness()
    path = root / harness.record
    path.write_bytes(path.read_text(encoding="utf-8").encode("utf-16"))
    return path


def test_a_refresh_under_a_release_that_dropped_a_file_leaves_no_stray(
    project, next_release, monkeypatch,
):
    # skills-roster-02: a recorded path this release no longer ships is
    # removed in the same all-or-nothing write when its bytes still match
    # the record, and the record after matches the disk. A skill one of
    # whose files the project edited is one unit: the edit keeps the whole
    # skill as it stands, the dropped file in the record with its hash.
    sk.add(project, SKILL, today=INSTALL_DAY)
    dropped = f"{SKILL_DIR}/scripts/handoff.py"
    packaged = (project / dropped).read_bytes()
    recorded_hash = {
        f["path"]: f["sha256"] for f in record_of(project)["skills"][0]["files"]
    }[dropped]
    next_release(edit=drop_scripts)
    monkeypatch.setattr(sk, "SKILL_CATALOG", _narrowed_catalog(SKILL, ("SKILL.md",)))

    (project / dropped).write_bytes(b"MY OWN VERSION\n")
    before = tree(project)
    (result,) = sk.add_all(project, SKILL, targets=(sk.DEFAULT_HARNESS,),
                           today=INSTALL_DAY)
    assert result.edited and not result.wrote
    by_path = {d.path: (d.verb, d.code) for d in result.dispositions}
    assert by_path[dropped] == (mnt.LEFT, mnt.SKILL_EDITED)
    assert tree(project) == before
    entry = record_of(project)["skills"][0]
    assert {f["path"]: f["sha256"] for f in entry["files"]}[dropped] == recorded_hash

    (project / dropped).write_bytes(packaged)
    (plan,) = sk.add_all_plan(project, SKILL, targets=(sk.DEFAULT_HARNESS,),
                              today=INSTALL_DAY)
    assert (project / dropped).is_file(), "the plan wrote"
    (result,) = sk.add_all(project, SKILL, targets=(sk.DEFAULT_HARNESS,),
                           today=INSTALL_DAY)
    assert result.dispositions == plan.dispositions
    assert result.wrote
    by_path = {d.path: (d.verb, d.code) for d in result.dispositions}
    assert by_path[dropped] == (mnt.REMOVED, mnt.SKILL_DROPPED)
    assert by_path[f"{SKILL_DIR}/SKILL.md"] == (mnt.UPDATED, mnt.SKILL_REFRESHED)
    files, dirs = tree(project)
    assert dropped not in files
    assert f"{SKILL_DIR}/scripts/" not in dirs
    entry = record_of(project)["skills"][0]
    assert [f["path"] for f in entry["files"]] == [f"{SKILL_DIR}/SKILL.md"]
    state = sk.skill_state(project, SKILL)
    assert state.current and state.extra == ()
    assert state.list_disposition().code == mnt.SKILL_CURRENT

    removed = sk.remove(project, SKILL)
    assert removed.complete, [(d.verb, d.path, d.code) for d in removed.dispositions]
    assert not (project / ".claude").exists()


def test_a_dropped_file_already_gone_leaves_the_record_matching_the_disk(
    project, next_release, monkeypatch,
):
    sk.add(project, SKILL, today=INSTALL_DAY)
    dropped = f"{SKILL_DIR}/scripts/handoff.py"
    (project / dropped).unlink()
    next_release(edit=drop_scripts)
    monkeypatch.setattr(sk, "SKILL_CATALOG", _narrowed_catalog(SKILL, ("SKILL.md",)))
    result = sk.add(project, SKILL, today=INSTALL_DAY)
    by_path = {d.path: (d.verb, d.code) for d in result.dispositions}
    assert by_path[dropped] == (mnt.ABSENT, mnt.ABSENT_ALREADY_GONE)
    entry = record_of(project)["skills"][0]
    assert [f["path"] for f in entry["files"]] == [f"{SKILL_DIR}/SKILL.md"]
    assert sk.skill_state(project, SKILL).current


@pytest.mark.parametrize("harness_name", sorted(sk.HARNESSES))
def test_a_first_install_into_a_tree_with_no_harness_directory_lands(
    tmp_path, harness_name,
):
    # Invariant 8 of PLAN-0017: the writer that now takes removals still
    # creates every missing parent on a first install, and the round
    # trip still closes.
    harness = sk.HARNESSES[harness_name]
    root = _make_project(tmp_path / "sample-project")
    top = harness.skills.split("/")[0]
    assert not (root / top).exists()
    before = tree(root)

    (result,) = sk.add_all(root, SKILL, targets=(harness_name,), today=INSTALL_DAY)
    assert result.wrote and result.complete
    assert (root / harness.skills / SKILL / "SKILL.md").is_file()
    assert (root / harness.record).is_file()
    body = json.loads((root / harness.record).read_text(encoding="utf-8"))
    parents = [p.as_posix() for p in reversed(Path(harness.skills).parents)
               if p.as_posix() != "."]
    assert body["dirs"] == [*parents, harness.skills]
    assert body["skills"][0]["dir_created"] is True

    (plan,) = sk.remove_all(root, SKILL, targets=(harness_name,))
    assert plan.complete
    assert tree(root) == before


def test_a_utf16_record_refuses_every_verb_in_words_and_writes_nothing(project):
    # skills-roster-04: the record in another encoding is the same
    # refusal a record that does not parse gets, from every verb, and
    # nothing is written on the way.
    sk.add(project, SKILL, today=INSTALL_DAY)
    as_utf16(project)
    before = tree(project)
    verbs = (
        lambda: sk.states_all(project),
        lambda: sk.add_all(project, SKILL, today=INSTALL_DAY),
        lambda: sk.remove_all(project, SKILL),
    )
    for verb in verbs:
        with pytest.raises(sk.SkillError) as caught:
            verb()
        assert str(caught.value).startswith(f"{RECORD_REL} does not parse as JSON: ")
        assert tree(project) == before


def test_a_record_that_will_not_read_is_the_kits_own_error(project, monkeypatch):
    sk.add(project, SKILL, today=INSTALL_DAY)
    before = tree(project)
    real = Path.read_text

    def refuse(self, *args, **kwargs):
        if self.name == "radialrig-skills.json":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)
    verbs = (
        lambda: sk.states_all(project),
        lambda: sk.add_all(project, SKILL, today=INSTALL_DAY),
        lambda: sk.remove_all(project, SKILL),
    )
    for verb in verbs:
        with pytest.raises(sk.SkillError) as caught:
            verb()
        said = str(caught.value)
        assert "Permission denied" in said and RECORD_REL in said
        assert said.endswith("nothing was written")
    monkeypatch.undo()
    assert tree(project) == before


def test_a_skill_file_that_will_not_read_is_the_kits_own_error(project, monkeypatch):
    sk.add(project, SKILL, today=INSTALL_DAY)
    before = tree(project)
    locked = project / SKILL_DIR / "SKILL.md"
    real = Path.read_bytes

    def refuse(self):
        if self == locked:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    verbs = (
        lambda: sk.states_all(project),
        lambda: sk.add_all_plan(project, SKILL, today=INSTALL_DAY),
        lambda: sk.remove_all_plan(project, SKILL),
    )
    for verb in verbs:
        with pytest.raises(sk.SkillError) as caught:
            verb()
        said = str(caught.value)
        assert "Permission denied" in said and f"{SKILL_DIR}/SKILL.md" in said
        assert said.endswith("nothing was written")
    monkeypatch.undo()
    assert tree(project) == before


def test_a_remove_whose_second_record_is_unreadable_removes_nothing(project):
    # skills-roster-05: every harness's plan is composed before the first
    # removal, so a record the second harness cannot read stops the whole
    # press with both trees as found, and the one sentence names it.
    sk.add_all(project, SKILL, today=INSTALL_DAY)
    second = sk.harnesses()[1]
    (project / second.record).write_bytes(b"{ not json\n")
    before = tree(project)
    with pytest.raises(sk.SkillError) as caught:
        sk.remove_all(project, SKILL)
    assert str(caught.value).startswith(f"{second.record} does not parse as JSON")
    assert tree(project) == before


def test_a_locked_file_in_the_second_tree_under_remove_reverses_neither(
    project, monkeypatch,
):
    sk.add_all(project, SKILL, today=INSTALL_DAY)
    second = sk.harnesses()[1]
    before = tree(project)
    locked = project / second.skills / SKILL / "SKILL.md"
    real = Path.unlink

    def refuse(self, *args, **kwargs):
        if self == locked:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    with pytest.raises(sk.SkillError) as caught:
        sk.remove_all(project, SKILL)
    said = str(caught.value)
    assert "Permission denied" in said
    assert said.endswith("nothing was written")
    monkeypatch.undo()
    assert tree(project) == before


def test_remove_all_applies_every_tree_in_one_write(project, monkeypatch):
    sk.add_all(project, SKILL, today=INSTALL_DAY)
    real = mnt._restore_all
    calls: list[tuple[set[str], set[str]]] = []

    def counted(root, writes, removals):
        calls.append((set(writes), set(removals)))
        return real(root, writes, removals)

    monkeypatch.setattr(mnt, "_restore_all", counted)
    plans = sk.remove_all(project, SKILL)
    assert [p.complete for p in plans] == [True, True]
    assert len(calls) == 1
    assert calls[0][1] == {rel for p in plans for rel in p.removals}
    assert not (project / ".claude").exists()
    assert not (project / ".agents").exists()


# --- refresh: the upgrade's skills half (PLAN-0021 phase 1, prescription 1)
# D-36 rule 2 as amended on 2026-10-02: for each agent, every skill the
# project already has with all its files in place and none edited comes up
# to the packaged version; nothing is added, nothing removed is put back,
# and no edited file is written.

REFRESH_DAY = dt.date(2026, 10, 2)


def refresh_codes(results) -> dict[tuple[str, str], list[str]]:
    return {(r.harness.name, r.name): codes(r.dispositions) for r in results}


def test_refresh_updates_a_clean_older_copy(project, next_release):
    sk.add(project, SKILL, today=INSTALL_DAY)
    package = next_release(edit_skill)

    results = sk.refresh(project, today=REFRESH_DAY)
    assert [(r.harness.name, r.name) for r in results] == [(sk.DEFAULT_HARNESS, SKILL)]
    (mine,) = results
    assert mine.wrote and not mine.left and not mine.edited
    by_path = {d.path: d for d in mine.dispositions}
    refreshed = by_path[f"{SKILL_DIR}/SKILL.md"]
    assert (refreshed.verb, refreshed.code) == (mnt.UPDATED, mnt.SKILL_REFRESHED)
    assert (project / SKILL_DIR / "SKILL.md").read_bytes() == (
        package / SKILL / "SKILL.md").read_bytes()
    body = record_of(project)
    assert body["radialrig"] == NEXT_VERSION
    entry = body["skills"][0]
    assert entry["installed"] == REFRESH_DAY.isoformat()
    for item in entry["files"]:
        assert item["sha256"] == mnt._sha256((project / item["path"]).read_bytes())


def test_refresh_leaves_an_edited_copy_with_its_diff(project, next_release):
    sk.add(project, SKILL, today=INSTALL_DAY)
    (project / SKILL_DIR / "SKILL.md").write_bytes(b"# ours now\n")
    next_release(edit_skill)
    before = tree(project)

    (mine,) = sk.refresh(project, today=REFRESH_DAY)
    assert tree(project) == before
    assert mine.left and mine.edited and not mine.wrote
    assert codes(mine.dispositions) == [mnt.SKILL_EDITED]
    assert mine.diffs and any("# ours now" in text for text in mine.diffs)
    assert any("A line the newer release added." in text for text in mine.diffs)


def test_refresh_composes_nothing_for_a_skill_the_record_lacks(project, next_release):
    next_release(edit_skill)
    before = tree(project)
    assert sk.refresh_plan(project) == ()
    assert sk.refresh(project) == ()
    assert tree(project) == before
    assert not (project / ".claude").exists()
    assert not (project / ".agents").exists()


def test_refresh_composes_nothing_for_a_skill_whose_folder_was_deleted(
    project, next_release,
):
    sk.add(project, SKILL, today=INSTALL_DAY)
    shutil.rmtree(project / SKILL_DIR)
    next_release(edit_skill)
    before = tree(project)

    (mine,) = sk.refresh(project, today=REFRESH_DAY)
    assert tree(project) == before
    assert mine.left and not mine.wrote
    assert codes(mine.dispositions) == [mnt.SKILL_RECORD_STALE]
    assert not (project / SKILL_DIR).exists()


def test_refresh_writes_nothing_for_a_current_copy(project):
    sk.add(project, SKILL, today=INSTALL_DAY)
    before = tree(project)
    (mine,) = sk.refresh(project, today=REFRESH_DAY)
    assert tree(project) == before
    assert mine.left and not mine.wrote and not mine.edited
    assert codes(mine.dispositions) == [mnt.SKILL_CURRENT]


def test_refresh_plan_writes_nothing_and_matches_the_run(project, next_release):
    sk.add(project, SKILL, today=INSTALL_DAY)
    next_release(edit_skill)
    before = tree(project)
    plan = sk.refresh_plan(project, today=REFRESH_DAY)
    assert tree(project) == before
    real = sk.refresh(project, today=REFRESH_DAY)
    assert [(r.name, r.dispositions, r.diffs) for r in plan] == [
        (r.name, r.dispositions, r.diffs) for r in real]
    assert [r.refreshed.payload for r in plan] == [r.refreshed.payload for r in real]


def test_refresh_acts_per_agent_and_narrows_to_the_named_ones(project, next_release):
    first, second = sk.harnesses()[:2]
    sk.add_all(project, SKILL, today=INSTALL_DAY)
    package = next_release(edit_skill)
    theirs = (package / SKILL / "SKILL.md").read_bytes()
    before = tree(project)

    narrowed = sk.refresh(project, targets=(second.name,), today=REFRESH_DAY)
    assert [(r.harness.name, r.name, r.wrote) for r in narrowed] == [
        (second.name, SKILL, True)]
    assert (project / second.skills / SKILL / "SKILL.md").read_bytes() == theirs
    assert (project / first.skills / SKILL / "SKILL.md").read_bytes() == \
        before[0][f"{first.skills}/{SKILL}/SKILL.md"]

    every = sk.refresh(project, today=REFRESH_DAY)
    assert [(r.harness.name, r.wrote) for r in every] == [
        (first.name, True), (second.name, False)]
    assert refresh_codes(every)[(second.name, SKILL)] == [mnt.SKILL_CURRENT]
    assert (project / first.skills / SKILL / "SKILL.md").read_bytes() == theirs


def test_refresh_never_adds_a_skill_the_project_lacks(project, next_release):
    sk.add(project, SKILL, today=INSTALL_DAY)
    next_release(edit_skill)
    results = sk.refresh(project, today=REFRESH_DAY)
    assert {r.name for r in results} == {SKILL}
    installed = {e["name"] for e in record_of(project)["skills"]}
    assert installed == {SKILL}
    for name in sk.catalog_names():
        if name != SKILL:
            assert not (project / ".claude" / "skills" / name).exists()


def test_a_behind_skill_is_packaged_clean_and_not_current(project, next_release):
    sk.add(project, SKILL, today=INSTALL_DAY)
    assert sk.behind_states(project) == ()
    next_release(edit_skill)
    found = sk.behind_states(project)
    assert [(s.harness.name, s.name) for s in found] == [(sk.DEFAULT_HARNESS, SKILL)]
    assert all(s.clean and not s.current and s.packaged is not None for s in found)
    # An edited copy is not behind: the update would write nothing there.
    (project / SKILL_DIR / "SKILL.md").write_bytes(b"# ours now\n")
    assert sk.behind_states(project) == ()
    # A record naming a skill this package no longer ships is not behind
    # either: nothing can be written for it.
    body = record_of(project)
    body["skills"][0]["name"] = "bygone"
    body["skills"][0]["dir"] = ".claude/skills/bygone"
    (project / RECORD_REL).write_bytes(mnt._dump_manifest(body))
    assert sk.behind_states(project) == ()


def test_refresh_leaves_a_skill_whose_new_file_this_project_already_owns(
    project, next_release, monkeypatch,
):
    """A release that ships a file at a path this project put its own
    file at: the add would refuse the whole press, and the refresh
    leaves that one skill as it is, listed, with nothing written."""
    sk.add(project, SKILL, today=INSTALL_DAY)
    entry = sk.catalog_entry(SKILL)
    grown = dataclasses.replace(entry, files=(*entry.files, "reference/new.md"))
    monkeypatch.setattr(sk, "SKILL_CATALOG", tuple(
        grown if e.name == SKILL else e for e in sk.SKILL_CATALOG))

    def ship_new_file(package_root: Path) -> None:
        (package_root / SKILL / "reference").mkdir()
        (package_root / SKILL / "reference" / "new.md").write_bytes(b"theirs\n")

    next_release(ship_new_file)
    (project / SKILL_DIR / "reference").mkdir()
    (project / SKILL_DIR / "reference" / "new.md").write_bytes(b"ours\n")
    before = tree(project)

    (mine,) = sk.refresh(project, today=REFRESH_DAY)
    assert tree(project) == before
    assert mine.left and not mine.wrote
    assert codes(mine.dispositions) == [mnt.SKILL_FILE_FOREIGN]
    assert paths(mine.dispositions) == [f"{SKILL_DIR}/reference/new.md"]


# --- PLAN-0022 phase 2: a recorded name is one folder name (SKL-002) ---
#
# A record that names no `dir` has its skill's folder inferred from the
# name. A name that climbs, or a skills folder that is a link out of the
# project, made `radialrig skill remove` delete an empty folder outside
# the project. Each case runs through the command line and asks first
# whether that folder is still there.

PLANTED = "../../../victim-empty"


def plant(root: Path, harness: sk.Harness, name: str) -> None:
    body = json.loads((root / harness.record).read_text(encoding="utf-8"))
    body["skills"].append({"name": name, "dir_created": True, "files": []})
    (root / harness.record).write_bytes(mnt._dump_manifest(body))


def link_folder(link: Path, target: Path) -> bool:
    """A folder link: a junction on Windows, where this account cannot
    make a symlink, and a symlink elsewhere. False when neither can be
    made here."""
    if sys.platform == "win32":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True)
        return done.returncode == 0
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        return False
    return True


def test_a_planted_skill_name_that_climbs_is_refused(tmp_path, capsys):
    root = _make_project(tmp_path / "sample-project")
    victim = tmp_path / "victim-empty"
    victim.mkdir()
    sk.add(root, SKILL, today=INSTALL_DAY)
    plant(root, sk.default_harness(), PLANTED)
    before = tree(root)

    code = cli.main(["skill", "remove", PLANTED, str(root)])
    err = capsys.readouterr().err
    assert victim.is_dir(), f"skill remove deleted a folder outside the project:\n{err}"
    assert tree(root) == before
    assert code == cli.ERROR
    assert PLANTED in err and RECORD_REL in err, err


def test_a_skill_folder_reached_through_a_linked_skills_folder_is_refused(
    tmp_path, capsys,
):
    root = _make_project(tmp_path / "sample-project")
    outside = tmp_path / "outside-skills"
    (outside / "plain").mkdir(parents=True)
    harness = sk.default_harness()
    (root / harness.skills).parent.mkdir(parents=True)
    link = root / harness.skills
    if not link_folder(link, outside):
        pytest.skip("this host can make neither a junction nor a symlink")
    try:
        record = sk.empty_record(harness)
        record["skills"] = [{"name": "plain", "dir_created": True, "files": []}]
        (root / harness.record).write_bytes(mnt._dump_manifest(record))

        code = cli.main(["skill", "remove", "plain", str(root)])
        err = capsys.readouterr().err
        assert (outside / "plain").is_dir(), (
            f"skill remove deleted a folder outside the project through "
            f"the linked skills folder:\n{err}")
        assert code == cli.ERROR
        assert f"{harness.skills}/plain" in err, err
    finally:
        if sys.platform == "win32":
            os.rmdir(link)
        else:
            os.unlink(link)


def test_a_planted_name_in_the_second_record_refuses_the_whole_removal(
    tmp_path, capsys,
):
    root = _make_project(tmp_path / "sample-project")
    victim = tmp_path / "victim-empty"
    victim.mkdir()
    sk.add_all(root, SKILL, today=INSTALL_DAY)
    second = sk.harnesses()[1]
    plant(root, second, PLANTED)
    before = tree(root)

    code = cli.main(["skill", "remove", SKILL, str(root)])
    err = capsys.readouterr().err
    assert tree(root) == before, (
        f"the removal went ahead past a planted name in {second.record}:\n{err}")
    assert victim.is_dir()
    assert code == cli.ERROR
    assert PLANTED in err and second.record in err, err


def test_a_skills_record_nested_past_the_parsers_depth_is_refused(
    tmp_path, capsys,
):
    """PLAN-0022 phase 3, from the code review: Python's JSON reader
    answers text nested past its depth with RecursionError, not a
    ValueError, so the record's own refusal never ran. How deep it goes
    depends on the host's stack limit: under an 8 MB stack this raises,
    and with none (GitHub's runner) it reads as a list. Either is a
    refusal."""
    root = _make_project(tmp_path / "sample-project")
    record = root / RECORD_REL
    record.parent.mkdir(parents=True)
    record.write_bytes(("[" * 100_000 + "]" * 100_000).encode("utf-8"))
    before = tree(root)

    try:
        code = cli.main(["skill", "remove", SKILL, str(root)])
    except Exception as exc:
        pytest.fail(f"skill remove raised {exc!r} instead of refusing")
    err = capsys.readouterr().err
    assert tree(root) == before
    assert code == cli.ERROR
    assert RECORD_REL in err, err
    assert ("does not parse as JSON" in err
            or "does not hold a JSON object" in err), err


def test_a_planted_name_in_a_refusal_prints_no_line_of_its_own(
    tmp_path, capsys,
):
    """PLAN-0022 phase 3, from the code review: the refusal quotes the
    name it would not take, and the name comes from the record."""
    root = _make_project(tmp_path / "sample-project")
    sk.add(root, SKILL, today=INSTALL_DAY)
    planted = "a/b\nremoved   README.md\x1b[2K"
    plant(root, sk.default_harness(), planted)
    before = tree(root)

    code = cli.main(["skill", "remove", SKILL, str(root)])
    err = capsys.readouterr().err
    assert tree(root) == before
    for line in err.splitlines():
        assert not line.startswith("removed"), (
            f"record text printed a line of its own: {line!r}\n{err}")
    assert "\x1b" not in err, err
    assert "a/b\\nremoved" in err, err
    assert code == cli.ERROR


def test_a_skills_record_with_a_number_past_the_readers_limit_is_refused(
    tmp_path, capsys,
):
    """From the second reader: Python's integer limit raises a ValueError
    that is not a JSONDecodeError."""
    root = _make_project(tmp_path / "sample-project")
    record = root / RECORD_REL
    record.parent.mkdir(parents=True)
    record.write_bytes(('{"schema": ' + "1" * 5000 + "}").encode("utf-8"))
    before = tree(root)

    try:
        code = cli.main(["skill", "remove", SKILL, str(root)])
    except Exception as exc:
        pytest.fail(f"skill remove raised {exc!r} instead of refusing")
    err = capsys.readouterr().err
    assert tree(root) == before
    assert code == cli.ERROR
    assert RECORD_REL in err and "does not parse as JSON" in err, err


@pytest.mark.parametrize("name", [None, 5, ["x"]])
def test_a_skill_entry_whose_name_is_not_text_refuses_the_removal(
    tmp_path, capsys, name,
):
    """From the second reader: such an entry was dropped before the name
    check saw it, so the removal went ahead and the record was rewritten
    without it."""
    root = _make_project(tmp_path / "sample-project")
    sk.add_all(root, SKILL, today=INSTALL_DAY)
    second = sk.harnesses()[1]
    plant(root, second, name)
    before = tree(root)

    code = cli.main(["skill", "remove", SKILL, str(root)])
    err = capsys.readouterr().err
    assert tree(root) == before, (
        f"the removal went ahead past a skill entry named {name!r}:\n{err}")
    assert code == cli.ERROR
    assert second.record in err, err
