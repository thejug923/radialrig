"""Tests for `radialrig unmount` — PLAN-0005 Phase 3, prescription 3.

Run:  uv run --with pytest pytest src/radialrig/tests/test_unmount.py -q

Two oracles run here. The first is the round trip: mount then unmount
leaves the tree byte-identical, compared as bytes over every file and as
a set over every directory, because an empty directory is invisible to
git and to a file-only snapshot. The second is the refusal matrix: an
unmount never deletes a governed record this project added and never
deletes a seeded file this project edited, and it says what it left and
why.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from radialrig import cli
from radialrig import mount as mnt
from radialrig.tests.test_mount import (  # noqa: F401 - fixtures are used by name
    EXPECTED,
    MOUNT_DAY,
    _make_project,
    host,
    snapshot,
    tree,
)

from radialrig import connect as cn

BASE_FILES = {".gitignore", "README.md", "src/app.py"}
SEEDED_MD = (
    "docs/spec.md",
    "docs/spec-git-environment.md",
    "docs/templates/decision.md",
    "docs/templates/plan.md",
    "docs/templates/spec.md",
)


@pytest.fixture()
def project(tmp_path) -> Path:
    """A fixture project with no git history: the round trip does not
    need one, and `git init` in every case would dominate the run."""
    return _make_project(tmp_path / "sample-project")


def marks(plan) -> dict[str, tuple[str, str]]:
    return {d.path: (d.verb, d.code) for d in plan.dispositions}


def detail(plan, path: str) -> str:
    return next(d.detail for d in plan.dispositions if d.path == path)


def left_paths(plan) -> set[str]:
    return {d.path for d in plan.dispositions if d.verb in (mnt.LEFT, mnt.EXCISED)}


def files_now(root: Path) -> set[str]:
    return set(tree(root)[0])


# --- C1-C6: the created seed files ------------------------------------

def test_an_unchanged_seed_file_is_removed(project):
    mnt.mount(project, today=MOUNT_DAY)
    plan = mnt.unmount(project)
    assert marks(plan)["docs/spec.md"] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert not (project / "docs" / "spec.md").exists()


def test_an_edited_seed_file_is_left_intact(project):
    mnt.mount(project, today=MOUNT_DAY)
    edited = b"# the owner's own rulebook\n"
    (project / "docs" / "spec.md").write_bytes(edited)
    plan = mnt.unmount(project)
    assert marks(plan)["docs/spec.md"] == (mnt.LEFT, mnt.LEFT_EDITED)
    assert (project / "docs" / "spec.md").read_bytes() == edited
    assert plan.complete is False


def test_a_seed_file_this_project_deleted_is_reported_absent(project):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "spec.md").unlink()
    plan = mnt.unmount(project)
    assert marks(plan)["docs/spec.md"] == (mnt.ABSENT, mnt.ABSENT_ALREADY_GONE)
    # An absent path is not something left behind: nothing survives that
    # this project has to decide about.
    assert plan.complete is True


def test_a_directory_standing_at_a_seed_path_is_left(project):
    mnt.mount(project, today=MOUNT_DAY)
    target = project / "docs" / "spec.md"
    target.unlink()
    target.mkdir()
    plan = mnt.unmount(project)
    assert marks(plan)["docs/spec.md"] == (mnt.LEFT, mnt.LEFT_NOT_A_FILE)
    assert target.is_dir()


def crlf(root: Path, rels) -> None:
    """What a checkout with `core.autocrlf=true` does to these files."""
    for rel in rels:
        path = root / rel
        data = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(data.replace(b"\n", b"\r\n"))


# --- BUG-0011: a CRLF checkout is not an edit --------------------------
# The Phase 2 open item, resolved by owner ruling 2026-09-05. A git
# checkout with core.autocrlf=true rewrites every seeded file with CRLF
# and changes no word. The record keeps the LF hash; every verb that
# compares reads the file as it stands or with CRLF read as LF.

def test_a_crlf_normalized_checkout_unmounts_clean(project):
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, EXPECTED)
    plan = mnt.unmount(project)
    for rel in (*SEEDED_MD, "AGENTS.md", ".mcp.json"):
        assert marks(plan)[rel] == (mnt.REMOVED, mnt.REMOVED_FILE), rel
    assert plan.complete is True
    assert files_now(project) == BASE_FILES


def test_a_crlf_file_with_a_real_edit_is_still_left(project):
    mnt.mount(project, today=MOUNT_DAY)
    path = project / "docs" / "spec.md"
    path.write_bytes(path.read_bytes() + b"\nOur own paragraph.\n")
    crlf(project, ["docs/spec.md"])
    edited = path.read_bytes()
    plan = mnt.unmount(project)
    assert marks(plan)["docs/spec.md"] == (mnt.LEFT, mnt.LEFT_EDITED)
    assert path.read_bytes() == edited
    assert plan.complete is False


def test_a_crlf_flip_of_an_appended_agents_file_still_restores(project):
    original = b"# Host rules\n\nA line.\n"
    (project / "AGENTS.md").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, ["AGENTS.md"])
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / "AGENTS.md").read_bytes() == original


def test_a_crlf_flip_with_an_edit_inside_the_fences_is_still_left(project):
    (project / "AGENTS.md").write_bytes(b"# Host rules\n")
    mnt.mount(project, today=MOUNT_DAY)
    data = (project / "AGENTS.md").read_bytes()
    (project / "AGENTS.md").write_bytes(
        data.replace(b"## RadialRig route map", b"## Our route map")
    )
    crlf(project, ["AGENTS.md"])
    changed = (project / "AGENTS.md").read_bytes()
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.LEFT, mnt.LEFT_SECTION_EDITED)
    assert (project / "AGENTS.md").read_bytes() == changed


def test_a_crlf_flip_of_a_merged_mcp_file_still_restores(project):
    # The recorded region is LF text; the flipped file no longer holds
    # it byte for byte. Read as LF it does, and the remainder is exactly
    # the pre-mount file, so the restore path is the right one.
    original = MCP_HOSTS["indent4"]
    (project / ".mcp.json").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, [".mcp.json"])
    assert b"\r\n" in (project / ".mcp.json").read_bytes()
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / ".mcp.json").read_bytes() == original


# Review 2026-09-05 of the first BUG-0011 fix (HOLD): the reversal used
# to work on the LF reading of a flipped file, so a host whose own prose
# carried CRLF came back with every CR outside the fence deleted.
# RadialRig writes nothing outside the fence or the region, so it
# deletes nothing there either (PLAN-0009 constraint 5, D-18 constraint
# 4): the reversal works on the file as it stands, and "restored" writes
# the bytes the pre-mount record hashes.

CRLF_HOST = b"# Ours\r\n\r\nOur line.\r\n"


@pytest.mark.parametrize("original", [CRLF_HOST, CRLF_HOST.rstrip(b"\r\n")])
def test_a_crlf_authored_host_is_restored_byte_for_byte_after_a_flip(
    project, original,
):
    (project / "AGENTS.md").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, ["AGENTS.md"])
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / "AGENTS.md").read_bytes() == original


def test_a_flipped_host_edited_after_the_fence_is_excised_with_its_crs_kept(
    project,
):
    path = project / "AGENTS.md"
    path.write_bytes(CRLF_HOST)
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, ["AGENTS.md"])
    path.write_bytes(path.read_bytes() + b"\r\nMore of ours.\r\n")
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.EXCISED, mnt.EXCISED_SECTION)
    assert path.read_bytes() == CRLF_HOST + b"\r\nMore of ours.\r\n"


def test_a_flipped_mcp_host_edited_elsewhere_is_excised_with_its_crs_kept(
    project,
):
    path = project / ".mcp.json"
    path.write_bytes(MCP_HOSTS["indent4"])
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, [".mcp.json"])
    path.write_bytes(path.read_bytes().replace(b'"command": "x"', b'"command": "y"', 1))
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.EXCISED, mnt.EXCISED_MCP_REGION)
    assert path.read_bytes() == MCP_HOSTS["indent4"].replace(
        b"\n", b"\r\n").replace(b'"command": "x"', b'"command": "y"', 1)


# Second review 2026-09-05 (HOLD): the separator count took a `\r\n`
# wherever the two bytes before the fence's newline read so, and a host
# whose last byte was a bare `\r` lost it to the count. The mount writes
# only `\n` separators; a `\r` counts as the mount's only when the
# remainder then hashes to the pre-mount record. When nothing proves
# it, the `\r` stays with the host.

BARE_CR_HOST = b"# Ours\r\rOur line.\r"


def test_a_host_ending_in_a_bare_cr_is_restored_byte_for_byte(project):
    path = project / "AGENTS.md"
    path.write_bytes(BARE_CR_HOST)
    mnt.mount(project, today=MOUNT_DAY)
    assert path.read_bytes().startswith(BARE_CR_HOST + b"\n\n")
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert path.read_bytes() == BARE_CR_HOST


@pytest.mark.parametrize("original", [BARE_CR_HOST, CRLF_HOST])
def test_a_flipped_separator_after_a_cr_host_still_restores(project, original):
    # The checkout flips the mount's separator too: `\r\n\r\n` after a
    # bare `\r`, or after the host's own CRLF. Either way the reading
    # whose remainder hashes to the record is the one taken.
    path = project / "AGENTS.md"
    path.write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, ["AGENTS.md"])
    assert original.rstrip(b"\r\n") + b"\r\n\r\n" in path.read_bytes()
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert path.read_bytes() == original


def test_a_flipped_cr_host_with_a_real_edit_is_excised_with_its_bytes_kept(
    project,
):
    path = project / "AGENTS.md"
    path.write_bytes(BARE_CR_HOST)
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, ["AGENTS.md"])
    path.write_bytes(path.read_bytes().replace(b"Our line.", b"Our edited line."))
    edited = BARE_CR_HOST.replace(b"Our line.", b"Our edited line.")
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.EXCISED, mnt.EXCISED_SECTION)
    after = path.read_bytes()
    # Every host byte stays, in order. No reading hashes to the record,
    # so only the `\n` bytes count: the one `\n` before the fence goes,
    # the `\r\n\r` the checkout and the host share stays with the host.
    assert after.startswith(edited)
    assert after == edited + b"\n\r"
    assert mnt.FENCE_BEGIN.encode("utf-8") not in after


def test_unmount_removes_no_path_the_manifest_does_not_list(project):
    (project / "docs").mkdir()
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "notes.md").write_bytes(b"kept\n")
    (project / "src" / "extra.py").write_bytes(b"pass\n")
    mnt.unmount(project)
    assert files_now(project) == BASE_FILES | {"docs/notes.md", "src/extra.py"}


# --- C7-C15: the .gitkeep directories ---------------------------------

def test_an_empty_record_directory_goes_with_its_gitkeep(project):
    mnt.mount(project, today=MOUNT_DAY)
    plan = mnt.unmount(project)
    assert marks(plan)["docs/decisions/.gitkeep"] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert marks(plan)["docs/decisions/"] == (mnt.REMOVED, mnt.REMOVED_DIR)
    assert not (project / "docs" / "decisions").exists()


def test_records_this_project_added_keep_their_directory(project):
    mnt.mount(project, today=MOUNT_DAY)
    added = {
        "docs/decisions/D-01-first.md": b"# D-01\n",
        "docs/decisions/D-02-second.md": b"# D-02\n",
        "docs/decisions/D-03-third.md": b"# D-03\n",
    }
    for rel, data in added.items():
        (project / rel).write_bytes(data)
    plan = mnt.unmount(project)
    assert marks(plan)["docs/decisions/.gitkeep"] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert marks(plan)["docs/decisions/"] == (mnt.LEFT, mnt.LEFT_DIR_OCCUPIED)
    assert detail(plan, "docs/decisions/") == "3"
    for rel, data in added.items():
        assert (project / rel).read_bytes() == data
    assert plan.complete is False
    assert plan.orphaned == ("docs/decisions/",)


def test_an_edited_gitkeep_keeps_its_directory(project):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "plans" / ".gitkeep").write_bytes(b"not empty\n")
    plan = mnt.unmount(project)
    assert marks(plan)["docs/plans/.gitkeep"] == (mnt.LEFT, mnt.LEFT_EDITED)
    assert marks(plan)["docs/plans/"] == (mnt.LEFT, mnt.LEFT_DIR_OCCUPIED)
    assert (project / "docs" / "plans").is_dir()


def test_a_deleted_gitkeep_still_lets_its_directory_go(project):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "archive" / ".gitkeep").unlink()
    plan = mnt.unmount(project)
    assert marks(plan)["docs/archive/.gitkeep"] == (mnt.ABSENT, mnt.ABSENT_ALREADY_GONE)
    assert marks(plan)["docs/archive/"] == (mnt.REMOVED, mnt.REMOVED_DIR)


def test_the_templates_directory_goes_only_when_it_is_empty(project, tmp_path):
    mnt.mount(project, today=MOUNT_DAY)
    plan = mnt.unmount(project)
    assert marks(plan)["docs/templates/"] == (mnt.REMOVED, mnt.REMOVED_DIR)

    other = _make_project(tmp_path / "second")
    mnt.mount(other, today=MOUNT_DAY)
    (other / "docs" / "templates" / "review.md").write_bytes(b"# Review\n")
    plan = mnt.unmount(other)
    assert marks(plan)["docs/templates/"] == (mnt.LEFT, mnt.LEFT_DIR_OCCUPIED)
    assert detail(plan, "docs/templates/") == "1"


def test_the_docs_directory_goes_only_when_it_is_empty(project, tmp_path):
    mnt.mount(project, today=MOUNT_DAY)
    plan = mnt.unmount(project)
    assert marks(plan)["docs/"] == (mnt.REMOVED, mnt.REMOVED_DIR)
    assert not (project / "docs").exists()

    other = _make_project(tmp_path / "second")
    mnt.mount(other, today=MOUNT_DAY)
    (other / "docs" / "notes.md").write_bytes(b"mine\n")
    plan = mnt.unmount(other)
    assert marks(plan)["docs/"] == (mnt.LEFT, mnt.LEFT_DIR_OCCUPIED)
    assert (other / "docs").is_dir()


def test_a_pre_existing_empty_directory_survives(project):
    (project / "docs" / "plans").mkdir(parents=True)
    mnt.mount(project, today=MOUNT_DAY)
    plan = mnt.unmount(project)
    assert "docs/plans/" not in marks(plan)
    assert "docs/" not in marks(plan)
    assert (project / "docs" / "plans").is_dir()
    assert list((project / "docs" / "plans").iterdir()) == []


def test_the_radialrig_directory_goes_with_the_record(project):
    mnt.mount(project, today=MOUNT_DAY)
    plan = mnt.unmount(project)
    assert marks(plan)[mnt.MANIFEST_REL] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert marks(plan)[".radialrig/"] == (mnt.REMOVED, mnt.REMOVED_DIR)
    assert not (project / ".radialrig").exists()
    # The record is removed outside the all-or-nothing removal set, last.
    assert mnt.MANIFEST_REL not in plan.removals


def test_a_directory_that_cannot_be_removed_is_reported(project, monkeypatch):
    mnt.mount(project, today=MOUNT_DAY)
    real = Path.rmdir

    def refuse(self):
        if self.name == "archive":
            raise OSError("in use")
        return real(self)

    monkeypatch.setattr(Path, "rmdir", refuse)
    plan = mnt.unmount(project)
    assert marks(plan)["docs/archive/"] == (mnt.LEFT, mnt.LEFT_DIR_LOCKED)
    assert (project / "docs" / "archive").is_dir()
    # The rest of the unmount still completed.
    assert not (project / "docs" / "spec.md").exists()
    assert marks(plan)["docs/decisions/"] == (mnt.REMOVED, mnt.REMOVED_DIR)
    assert plan.complete is False


# --- C16-C26: AGENTS.md -----------------------------------------------

def test_an_agents_file_the_mount_created_is_deleted(project):
    mnt.mount(project, today=MOUNT_DAY)
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert not (project / "AGENTS.md").exists()


@pytest.mark.parametrize("original", [
    b"# Host rules\n\nA line.\n",
    b"# Host rules\n\nNo trailing newline.",
    b"# Host rules\r\n\r\nCRLF and no trailing newline.",
    b"",
])
def test_a_pre_existing_agents_file_is_restored_byte_for_byte(project, original):
    (project / "AGENTS.md").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / "AGENTS.md").read_bytes() == original


def test_an_edit_outside_the_fences_excises_the_section(project):
    original = b"# Host rules\n\nA line.\n"
    (project / "AGENTS.md").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    data = (project / "AGENTS.md").read_bytes()
    changed = data.replace(b"A line.", b"A different line.")
    (project / "AGENTS.md").write_bytes(changed)
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.EXCISED, mnt.EXCISED_SECTION)
    assert (project / "AGENTS.md").read_bytes() == b"# Host rules\n\nA different line.\n"
    assert plan.complete is False


def test_text_appended_after_the_fence_survives(project):
    original = b"# Host rules\n"
    (project / "AGENTS.md").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    with (project / "AGENTS.md").open("ab") as fh:
        fh.write(b"\n## Added later\n")
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.EXCISED, mnt.EXCISED_SECTION)
    assert (project / "AGENTS.md").read_bytes() == b"# Host rules\n\n## Added later\n"


def test_an_edit_inside_the_fences_leaves_the_whole_section(project):
    (project / "AGENTS.md").write_bytes(b"# Host rules\n")
    mnt.mount(project, today=MOUNT_DAY)
    data = (project / "AGENTS.md").read_bytes()
    changed = data.replace(b"## RadialRig route map", b"## Our route map")
    (project / "AGENTS.md").write_bytes(changed)
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.LEFT, mnt.LEFT_SECTION_EDITED)
    assert (project / "AGENTS.md").read_bytes() == changed


@pytest.mark.parametrize("break_it", ["begin", "end", "duplicate"])
def test_broken_fences_leave_the_file_untouched(project, break_it):
    (project / "AGENTS.md").write_bytes(b"# Host rules\n")
    mnt.mount(project, today=MOUNT_DAY)
    data = (project / "AGENTS.md").read_bytes()
    begin = mnt.FENCE_BEGIN.encode("utf-8")
    end = mnt.FENCE_END.encode("utf-8")
    if break_it == "begin":
        changed = data.replace(begin + b"\n", b"")
    elif break_it == "end":
        changed = data.replace(b"\n" + end, b"")
    else:
        changed = data + begin + b"\nsecond\n" + end + b"\n"
    (project / "AGENTS.md").write_bytes(changed)
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.LEFT, mnt.LEFT_FENCES_BROKEN)
    assert (project / "AGENTS.md").read_bytes() == changed


def test_an_agents_file_this_project_deleted_is_reported_absent(project):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "AGENTS.md").unlink()
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.ABSENT, mnt.ABSENT_ALREADY_GONE)


def test_a_created_agents_file_that_was_edited_is_never_deleted(project):
    mnt.mount(project, today=MOUNT_DAY)
    data = (project / "AGENTS.md").read_bytes()
    (project / "AGENTS.md").write_bytes(data + "\nour own note\n".encode("utf-8"))
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.EXCISED, mnt.EXCISED_SECTION)
    assert (project / "AGENTS.md").read_bytes() == b"\nour own note\n"


def test_a_prepend_on_a_mount_created_file_keeps_its_newline(project):
    # The second review's finding: the mount created AGENTS.md whole and
    # wrote no separator, so the newline in front of the fence is the
    # project's own last byte, never the mount's to strip (constraint 5).
    mnt.mount(project, today=MOUNT_DAY)
    data = (project / "AGENTS.md").read_bytes()
    (project / "AGENTS.md").write_bytes(b"# Mine\n" + data)
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.EXCISED, mnt.EXCISED_SECTION)
    assert (project / "AGENTS.md").read_bytes() == b"# Mine\n"


def test_a_prepend_over_an_empty_pre_mount_file_keeps_its_newline(project):
    # Same shape through the other separator-less record: the pre-mount
    # file was empty, so the mount wrote the section and nothing else.
    (project / "AGENTS.md").write_bytes(b"")
    mnt.mount(project, today=MOUNT_DAY)
    data = (project / "AGENTS.md").read_bytes()
    (project / "AGENTS.md").write_bytes(b"# Mine\n" + data)
    plan = mnt.unmount(project)
    assert marks(plan)["AGENTS.md"] == (mnt.EXCISED, mnt.EXCISED_SECTION)
    assert (project / "AGENTS.md").read_bytes() == b"# Mine\n"


def test_unmount_reports_the_connect_record_and_exits_5(project, capsys):
    """The documented interaction between the two kits' records.

    `.radialrig/` is a directory the mount records and the unmount tries
    to remove. A connect record sitting in it keeps the directory, and
    the unmount says so rather than deleting a file it did not write.
    The correct order is unconnect, then unmount, and the report is what
    says so.
    """
    mnt.mount(project, today=MOUNT_DAY)
    (project / cn.CONNECT_REL).write_bytes(b'{"schema": 1}\n')

    code = cli.main(["unmount", str(project)])
    assert code == 5
    assert (project / cn.CONNECT_REL).is_file()
    out = capsys.readouterr().out
    assert "left" in out
    assert ".radialrig/" in out
    assert "holds 1 file(s) this project added" in out


# --- C27-C38: .mcp.json ------------------------------------------------

MCP_HOSTS = {
    "indent4": b'{\n    "mcpServers": {\n        "other": {"command": "x", "args": []}\n    }\n}\n',
    "indent2": b'{\n  "mcpServers": {\n    "other": {"command": "x", "args": []}\n  }\n}\n',
    "crlf": b'{\r\n  "mcpServers": {\r\n    "other": {"command": "x", "args": []}\r\n  }\r\n}\r\n',
    "tabs": b'{\n\t"mcpServers": {\n\t\t"other": {"command": "x", "args": []}\n\t}\n}\n',
    "empty-servers": b'{\n  "mcpServers": {}\n}\n',
    "no-servers": b'{\n  "other": 1\n}\n',
    "empty-root": b"{}\n",
}


def test_an_mcp_file_the_mount_created_is_deleted(project):
    mnt.mount(project, today=MOUNT_DAY)
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert not (project / ".mcp.json").exists()


@pytest.mark.parametrize("name", sorted(MCP_HOSTS))
def test_a_pre_existing_mcp_file_is_restored_byte_for_byte(project, name):
    original = MCP_HOSTS[name]
    (project / ".mcp.json").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    assert (project / ".mcp.json").read_bytes() != original
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / ".mcp.json").read_bytes() == original


def test_host_owned_keys_are_left_and_nothing_is_touched(project):
    theirs = {"command": "their-own-server", "args": []}
    original = (json.dumps({"mcpServers": {
        "radialrig": theirs, "radialrig-write": theirs}}, indent=4)
        + "\n").encode("utf-8")
    (project / ".mcp.json").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    assert (project / ".mcp.json").read_bytes() == original
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.LEFT, mnt.LEFT_MCP_HOST_OWNED)
    assert (project / ".mcp.json").read_bytes() == original


def test_a_host_owned_read_key_stays_when_the_write_key_comes_out(project):
    theirs = {"command": "their-own-server", "args": []}
    original = (json.dumps({"mcpServers": {"radialrig": theirs}}, indent=4)
                + "\n").encode("utf-8")
    (project / ".mcp.json").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    assert (project / ".mcp.json").read_bytes() != original
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / ".mcp.json").read_bytes() == original


def test_a_reformatted_file_loses_only_the_registered_key(project):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    mnt.mount(project, today=MOUNT_DAY)
    data = json.loads((project / ".mcp.json").read_bytes().decode("utf-8"))
    data["other-top-level"] = {"kept": True}
    (project / ".mcp.json").write_bytes(
        (json.dumps(data, indent=8) + "\n").encode("utf-8")
    )
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.LEFT, mnt.LEFT_MCP_RESERIALIZED)
    after = json.loads((project / ".mcp.json").read_bytes().decode("utf-8"))
    assert "radialrig" not in after["mcpServers"]
    assert after["mcpServers"]["other"] == {"command": "x", "args": []}
    assert after["other-top-level"] == {"kept": True}
    assert plan.complete is False


def test_an_emptied_servers_object_stays(project):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["empty-servers"])
    mnt.mount(project, today=MOUNT_DAY)
    data = json.loads((project / ".mcp.json").read_bytes().decode("utf-8"))
    (project / ".mcp.json").write_bytes(
        (json.dumps(data, indent=8) + "\n").encode("utf-8")
    )
    mnt.unmount(project)
    after = json.loads((project / ".mcp.json").read_bytes().decode("utf-8"))
    assert after == {"mcpServers": {}}


def test_a_changed_entry_value_is_left(project):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    mnt.mount(project, today=MOUNT_DAY)
    text = (project / ".mcp.json").read_bytes().decode("utf-8")
    changed = text.replace('"args": []', '"args": ["--theirs"]', 1)
    (project / ".mcp.json").write_bytes(changed.encode("utf-8"))
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.LEFT, mnt.LEFT_MCP_ENTRY_CHANGED)
    assert (project / ".mcp.json").read_bytes().decode("utf-8") == changed


def test_already_removed_keys_are_reported_gone(project):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    mnt.mount(project, today=MOUNT_DAY)
    data = json.loads((project / ".mcp.json").read_bytes().decode("utf-8"))
    del data["mcpServers"]["radialrig"]
    del data["mcpServers"]["radialrig-write"]
    payload = (json.dumps(data, indent=8) + "\n").encode("utf-8")
    (project / ".mcp.json").write_bytes(payload)
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.LEFT, mnt.LEFT_MCP_KEY_GONE)
    assert (project / ".mcp.json").read_bytes() == payload


def test_one_removed_key_leaves_the_other_to_the_structural_path(project):
    # The owner switched capture off by removing the write entry and
    # reformatted the file. The reversal drops the key that is left and
    # names exactly that one.
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    mnt.mount(project, today=MOUNT_DAY)
    data = json.loads((project / ".mcp.json").read_bytes().decode("utf-8"))
    del data["mcpServers"]["radialrig-write"]
    (project / ".mcp.json").write_bytes(
        (json.dumps(data, indent=8) + "\n").encode("utf-8"))
    plan = mnt.unmount(project)
    row = next(d for d in plan.dispositions if d.path == ".mcp.json")
    assert (row.verb, row.code) == (mnt.LEFT, mnt.LEFT_MCP_RESERIALIZED)
    assert mnt.reason_text(row) == (
        "changed since the mount; removed mcpServers.radialrig only")
    after = json.loads((project / ".mcp.json").read_bytes().decode("utf-8"))
    assert set(after["mcpServers"]) == {"other"}


def test_a_file_that_no_longer_parses_is_left(project):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    mnt.mount(project, today=MOUNT_DAY)
    # The inserted region is gone too, so there is nothing to reverse by
    # deletion and the structural path has to read a file that will not
    # parse. It leaves the file exactly as it stands.
    broken = b"{ this is not JSON any more\n"
    (project / ".mcp.json").write_bytes(broken)
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.LEFT, mnt.LEFT_MCP_UNPARSED)
    assert (project / ".mcp.json").read_bytes() == broken


def test_junk_after_an_intact_region_still_reverses_the_insertion(project):
    # The region is still there verbatim, so deleting it is still an
    # exact reversal of what the mount added — but the file no longer
    # equals its pre-mount bytes, so the verb is "excised", not
    # "restored".
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    mnt.mount(project, today=MOUNT_DAY)
    broken = (project / ".mcp.json").read_bytes() + b"trailing junk"
    (project / ".mcp.json").write_bytes(broken)
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.EXCISED, mnt.EXCISED_MCP_REGION)
    assert not plan.complete
    assert (project / ".mcp.json").read_bytes() == MCP_HOSTS["indent4"] + b"trailing junk"


def test_an_mcp_file_this_project_deleted_is_reported_absent(project):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    mnt.mount(project, today=MOUNT_DAY)
    (project / ".mcp.json").unlink()
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.ABSENT, mnt.ABSENT_ALREADY_GONE)


def test_two_identical_regions_are_ambiguous(project):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    result = mnt.mount(project, today=MOUNT_DAY)
    region = next(f for f in result.files if f.path == ".mcp.json").insert
    data = (project / ".mcp.json").read_bytes()
    doubled = data.replace(region.encode("utf-8"),
                           (region + region).encode("utf-8"), 1)
    (project / ".mcp.json").write_bytes(doubled)
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.LEFT, mnt.LEFT_MCP_AMBIGUOUS)
    assert (project / ".mcp.json").read_bytes() == doubled


# --- C39-C48: the manifest and the mechanics --------------------------

def test_a_missing_manifest_is_a_no_op(project, capsys):
    before = tree(project)
    assert cli.main(["unmount", str(project)]) == 4
    assert tree(project) == before
    assert "Not mounted" in capsys.readouterr().out


def test_an_unparseable_manifest_stops_at_exit_one(project, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    (project / mnt.MANIFEST_REL).write_bytes(b"{not json")
    before = tree(project)
    assert cli.main(["unmount", str(project)]) == 1
    assert tree(project) == before
    assert "does not parse" in capsys.readouterr().err


def test_a_newer_schema_stops_at_exit_one(project, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    body = mnt.read_manifest(project)
    body["schema"] = 3
    (project / mnt.MANIFEST_REL).write_bytes(
        (json.dumps(body, indent=2) + "\n").encode("utf-8")
    )
    before = tree(project)
    assert cli.main(["unmount", str(project)]) == 1
    assert tree(project) == before
    err = capsys.readouterr().err
    assert "written by a newer radialrig (schema 3)" in err


def test_a_manifest_path_outside_the_project_stops_at_exit_one(
    project, tmp_path, capsys
):
    # The manifest is host-writable input; an entry pointing above the
    # root must refuse the whole unmount before any write.
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host property\n")
    mnt.mount(project, today=MOUNT_DAY)
    body = mnt.read_manifest(project)
    body["files"].append({
        "path": "../outside.txt", "mode": "created",
        "sha256": mnt._sha256(b"host property\n"), "file_created": True,
    })
    (project / mnt.MANIFEST_REL).write_bytes(mnt._dump_manifest(body))
    before = tree(project)
    assert cli.main(["unmount", str(project)]) == 1
    assert tree(project) == before
    assert outside.read_bytes() == b"host property\n"
    assert "outside this project" in capsys.readouterr().err


def test_a_manifest_dir_outside_the_project_stops_at_exit_one(
    project, tmp_path, capsys
):
    (tmp_path / "escape").mkdir()
    mnt.mount(project, today=MOUNT_DAY)
    body = mnt.read_manifest(project)
    body["dirs"].append("../escape/")
    (project / mnt.MANIFEST_REL).write_bytes(mnt._dump_manifest(body))
    before = tree(project)
    assert cli.main(["unmount", str(project)]) == 1
    assert tree(project) == before
    assert (tmp_path / "escape").is_dir()
    assert "outside this project" in capsys.readouterr().err


def test_an_absolute_manifest_path_stops_at_exit_one(project, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    body = mnt.read_manifest(project)
    body["files"].append({
        "path": str(project.parent / "abs.txt"), "mode": "created",
        "sha256": mnt._sha256(b""), "file_created": True,
    })
    (project / mnt.MANIFEST_REL).write_bytes(mnt._dump_manifest(body))
    assert cli.main(["unmount", str(project)]) == 1
    assert "outside this project" in capsys.readouterr().err


def test_a_host_edit_beside_an_intact_region_reads_excised_not_restored(
    project, capsys
):
    # The region is intact and removable, but the host edited another
    # part of the file, so "restored to its bytes from before the
    # mount" would be false; the honest verb is "excised".
    original = b'{\n  "mcpServers": {\n    "other": {"command": "x", "args": []}\n  }\n}\n'
    (project / ".mcp.json").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    edited = (project / ".mcp.json").read_bytes().replace(
        b'"command": "x"', b'"command": "y"'
    )
    (project / ".mcp.json").write_bytes(edited)
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.EXCISED, mnt.EXCISED_MCP_REGION)
    assert not plan.complete
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"] == {"other": {"command": "y", "args": []}}


def test_an_untouched_merge_still_reads_restored(project):
    original = b'{\n  "mcpServers": {\n    "other": {"command": "x", "args": []}\n  }\n}\n'
    (project / ".mcp.json").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    plan = mnt.unmount(project)
    assert marks(plan)[".mcp.json"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / ".mcp.json").read_bytes() == original


def legacy_manifest(root: Path) -> None:
    """Rewrite the record as a Phase 2 build would have written it."""
    body = mnt.read_manifest(root)
    body["schema"] = 1
    body.pop("dirs", None)
    for entry in body["files"]:
        for key in ("pre_mount_sha256", "newline_added", "written", "insert",
                    "entry", "reserialized"):
            entry.pop(key, None)
    (root / mnt.MANIFEST_REL).write_bytes(
        (json.dumps(body, indent=2) + "\n").encode("utf-8")
    )


def test_a_schema_one_record_runs_with_the_degradation_stated(project, capsys):
    (project / "AGENTS.md").write_bytes(b"# Host rules\n")
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent2"])
    mnt.mount(project, today=MOUNT_DAY)
    legacy_manifest(project)
    plan = mnt.unmount_plan(project)
    assert plan.degraded is True
    assert cli.main(["unmount", str(project)]) in (0, 5)
    out = capsys.readouterr().out
    assert "carries no restore fields" in out
    assert not (project / mnt.MANIFEST_REL).exists()


def test_the_record_goes_even_on_a_partial_unmount(project):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "spec.md").write_bytes(b"ours now\n")
    plan = mnt.unmount(project)
    assert plan.complete is False
    assert not (project / mnt.MANIFEST_REL).exists()
    assert not (project / ".radialrig").exists()


def test_a_second_unmount_is_a_no_op(project, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    assert cli.main(["unmount", str(project)]) == 0
    capsys.readouterr()
    before = tree(project)
    assert cli.main(["unmount", str(project)]) == 4
    assert tree(project) == before


def test_dry_run_reports_the_same_and_writes_nothing(project, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "spec.md").write_bytes(b"ours now\n")
    before = tree(project)

    assert cli.main(["unmount", str(project), "--dry-run"]) == 5
    dry = capsys.readouterr().out
    assert tree(project) == before

    assert cli.main(["unmount", str(project)]) == 5
    real = capsys.readouterr().out
    dry_block = [ln for ln in dry.splitlines() if ln.startswith(mnt.VERBS)]
    real_block = [ln for ln in real.splitlines() if ln.startswith(mnt.VERBS)]
    assert dry_block == real_block
    assert cli.DRY_RUN_TAIL in dry
    assert cli.DRY_RUN_TAIL not in real


def test_a_failure_mid_removal_restores_every_buffered_file(project, monkeypatch):
    mnt.mount(project, today=MOUNT_DAY)
    before = tree(project)
    real = Path.unlink

    def refuse(self, *args, **kwargs):
        if self.name == "spec.md":
            raise OSError("no")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    with pytest.raises(mnt.WriteStopped):
        mnt.unmount(project)
    assert tree(project) == before


def test_the_exit_code_says_whether_anything_was_left(project, capsys, tmp_path):
    mnt.mount(project, today=MOUNT_DAY)
    assert cli.main(["unmount", str(project)]) == 0
    assert "0 left" in capsys.readouterr().out

    other = _make_project(tmp_path / "second")
    mnt.mount(other, today=MOUNT_DAY)
    (other / "docs" / "spec.md").write_bytes(b"ours now\n")
    assert cli.main(["unmount", str(other)]) == 5


def test_the_orphaned_records_note_appears_only_when_earned(project, capsys, tmp_path):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "decisions" / "D-01-first.md").write_bytes(b"# D-01\n")
    cli.main(["unmount", str(project)])
    assert "no longer have their rulebook" in capsys.readouterr().out

    other = _make_project(tmp_path / "second")
    mnt.mount(other, today=MOUNT_DAY)
    (other / "docs" / "templates" / "review.md").write_bytes(b"# Review\n")
    cli.main(["unmount", str(other)])
    assert "no longer have their rulebook" not in capsys.readouterr().out


# --- D: the round trip, the phase's done-condition --------------------

def bare(root: Path) -> None:
    return None


def agents(data: bytes):
    def build(root: Path) -> None:
        (root / "AGENTS.md").write_bytes(data)
    return build


def mcp(name: str):
    def build(root: Path) -> None:
        (root / ".mcp.json").write_bytes(MCP_HOSTS[name])
    return build


def mcp_registered(root: Path) -> None:
    (root / ".mcp.json").write_bytes(
        (json.dumps({"mcpServers": {"radialrig": {"command": "theirs", "args": []}}},
                    indent=2) + "\n").encode("utf-8")
    )


def both_merges(root: Path) -> None:
    (root / "AGENTS.md").write_bytes(b"# Host rules\n\nA line.\n")
    (root / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])


def empty_plans_dir(root: Path) -> None:
    (root / "docs" / "plans").mkdir(parents=True)


def docs_readme(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "README.md").write_bytes(b"# Our docs\n")


ROUND_TRIP_HOSTS = {
    "bare": bare,
    "agents-trailing-newline": agents(b"# Host rules\n\nA line.\n"),
    "agents-no-trailing-newline": agents(b"# Host rules\n\nA line."),
    "agents-crlf-no-trailing-newline": agents(b"# Host rules\r\n\r\nA line."),
    "mcp-indent4": mcp("indent4"),
    "mcp-indent2-lf": mcp("indent2"),
    "mcp-tabs": mcp("tabs"),
    "mcp-crlf": mcp("crlf"),
    "mcp-empty-servers": mcp("empty-servers"),
    "mcp-no-servers-key": mcp("no-servers"),
    "mcp-empty-root": mcp("empty-root"),
    "mcp-already-registered": mcp_registered,
    "both-merges": both_merges,
    "pre-existing-empty-plans-dir": empty_plans_dir,
    "pre-existing-docs-readme": docs_readme,
}


@pytest.mark.parametrize("name", sorted(ROUND_TRIP_HOSTS))
def test_mount_then_unmount_leaves_the_tree_byte_identical(project, name):
    ROUND_TRIP_HOSTS[name](project)
    before = tree(project)
    mnt.mount(project, today=MOUNT_DAY)
    assert tree(project) != before
    mnt.unmount(project)
    assert tree(project) == before


def test_an_upgrade_in_the_middle_does_not_break_the_round_trip(project):
    both_merges(project)
    before = tree(project)
    mnt.mount(project, today=MOUNT_DAY)
    mounted = tree(project)
    plan = mnt.upgrade(project, today=MOUNT_DAY)
    assert all(item.verb == mnt.CURRENT for item in plan.items), plan.items
    assert tree(project) == mounted
    mnt.unmount(project)
    assert tree(project) == before


def test_a_crlf_normalized_host_round_trips(project):
    # BUG-0011: the one host shape the round trip used to exclude.
    before = tree(project)
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, EXPECTED)
    mnt.unmount(project)
    assert tree(project) == before


def test_an_edited_seed_host_keeps_exactly_that_file(project):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "spec.md").write_bytes(b"ours now\n")
    mnt.unmount(project)
    files, dirs = tree(project)
    assert set(files) == BASE_FILES | {"docs/spec.md"}
    assert files["docs/spec.md"] == b"ours now\n"
    assert dirs == {"src/", "docs/"}


# --- PLAN-0022 phase 2: a record claims only what RadialRig writes ----
#
# A mount record is a file in the project, so a cloned project can ship
# one and anyone can edit one (MNT-003). Unmount acts on a file entry
# only when its path and mode are a pair in the seed list, on a
# directory only when a mount could have created it, and in .mcp.json
# only on RadialRig's own two entries carrying the values it writes.
# Every forged case runs through `radialrig unmount` and asks first
# whether what the record forged is still there.

GITHUB = {"command": "gh-mcp", "args": ["--stdio"]}
# Values under the read server's key that RadialRig never writes there:
# a project's own, and the write server's.
NOT_OURS = {
    "theirs": {"command": "theirs", "args": []},
    "write-value": {"command": "radialrig-write", "args": []},
}
ORDINARY = ("src/app.py", ".git/HEAD")


def servers_file(servers: dict) -> bytes:
    """A project's own .mcp.json, one member per line, so a member's text
    can be quoted out of it exactly."""
    members = ",\n".join(f'    "{key}": {json.dumps(value)}'
                         for key, value in servers.items())
    return ('{\n  "mcpServers": {\n' + members + "\n  }\n}\n").encode("utf-8")


def ship_record(root: Path, files: list[dict], dirs: list[str]) -> None:
    """A mount record as a cloned project could carry it: no mount ran."""
    body = {
        "schema": mnt.MANIFEST_SCHEMA, "radialrig": "1.2.3",
        "mounted": "2026-10-06", "scope": "Sample", "owner": "someone",
        "hash": mnt.HASH_NAME, "dirs": dirs, "files": files,
    }
    (root / ".radialrig").mkdir(exist_ok=True)
    (root / mnt.MANIFEST_REL).write_bytes(mnt._dump_manifest(body))


def claim(root: Path, *, files=(), dirs=()) -> None:
    """Claims added to the record a real mount wrote."""
    body = mnt.read_manifest(root)
    body["files"].extend(files)
    body["dirs"].extend(dirs)
    (root / mnt.MANIFEST_REL).write_bytes(mnt._dump_manifest(body))


def created_claim(root: Path, rel: str) -> dict:
    return {"path": rel, "mode": mnt.CREATED, "file_created": True,
            "sha256": mnt._sha256((root / rel).read_bytes())}


def with_git_head(root: Path) -> None:
    (root / ".git").mkdir(exist_ok=True)
    (root / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")


def row_of(out: str, path: str) -> str:
    return "\n".join(line for line in out.splitlines() if path in line)


def test_a_record_claiming_ordinary_files_after_a_mount_leaves_them(
    project, capsys,
):
    with_git_head(project)
    mnt.mount(project, today=MOUNT_DAY)
    kept = {rel: (project / rel).read_bytes() for rel in ORDINARY}
    claim(project, files=[created_claim(project, rel) for rel in ORDINARY])

    code = cli.main(["unmount", str(project)])
    out = capsys.readouterr().out
    for rel, data in kept.items():
        assert (project / rel).is_file(), f"unmount removed {rel}:\n{out}"
        assert (project / rel).read_bytes() == data
    for rel in ORDINARY:
        line = row_of(out, rel)
        assert "left" in line and "never" in line, out
    assert code == 5
    # The honest part of the record still reverses.
    assert not (project / "docs" / "spec.md").exists()


def test_a_record_shipped_with_no_mount_run_removes_no_ordinary_file(
    project, capsys,
):
    with_git_head(project)
    kept = {rel: (project / rel).read_bytes() for rel in ORDINARY}
    ship_record(project, [created_claim(project, rel) for rel in ORDINARY], [])

    code = cli.main(["unmount", str(project)])
    out = capsys.readouterr().out
    for rel, data in kept.items():
        assert (project / rel).is_file(), f"unmount removed {rel}:\n{out}"
        assert (project / rel).read_bytes() == data
    for rel in ORDINARY:
        line = row_of(out, rel)
        assert "left" in line and "never" in line, out
    assert code == 5


def test_a_record_claiming_a_directory_no_mount_creates_leaves_it(
    project, capsys,
):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "src" / "generated").mkdir()
    claim(project, dirs=["src/generated"])

    code = cli.main(["unmount", str(project)])
    out = capsys.readouterr().out
    assert (project / "src" / "generated").is_dir(), (
        f"unmount removed src/generated/:\n{out}")
    line = row_of(out, "src/generated/")
    assert "left" in line and "never" in line, out
    assert code == 5


def mcp_claim(data: bytes, route: str, key: str, value: dict) -> dict:
    """A .mcp.json entry that claims `key` by one route."""
    entry = {"path": mnt.MCP_REL, "mode": mnt.MERGED, "file_created": False,
             "sha256": mnt._sha256(data), "written": True,
             "keys": [f"mcpServers.{key}"]}
    if route == "recorded text":
        entry["insert"] = f'\n    "{key}": {json.dumps(value)}'
    elif route == "entries":
        entry["entries"] = {key: value}
    elif route == "keys and entry":
        entry["entry"] = value
    elif route == "created file":
        entry["file_created"] = True
    return entry  # "keys only": the key list alone


@pytest.mark.parametrize(
    "route", ["recorded text", "entries", "keys only", "created file"])
def test_a_record_claiming_the_projects_own_server_leaves_it(
    project, capsys, route,
):
    data = servers_file({"github": GITHUB})
    (project / ".mcp.json").write_bytes(data)
    ship_record(project, [mcp_claim(data, route, "github", GITHUB)], [])

    code = cli.main(["unmount", str(project)])
    out = capsys.readouterr().out
    assert (project / ".mcp.json").is_file(), f"unmount removed .mcp.json:\n{out}"
    assert (project / ".mcp.json").read_bytes() == data, out
    assert "mcpServers.github" in row_of(out, ".mcp.json"), out
    assert code == 5


@pytest.mark.parametrize("value", sorted(NOT_OURS))
@pytest.mark.parametrize(
    "route", ["recorded text", "entries", "keys and entry", "created file"])
def test_a_record_claiming_a_radialrig_server_it_never_wrote_leaves_it(
    project, capsys, route, value,
):
    data = servers_file({"radialrig": NOT_OURS[value]})
    (project / ".mcp.json").write_bytes(data)
    ship_record(project, [mcp_claim(data, route, "radialrig", NOT_OURS[value])], [])

    code = cli.main(["unmount", str(project)])
    out = capsys.readouterr().out
    assert (project / ".mcp.json").is_file(), f"unmount removed .mcp.json:\n{out}"
    assert (project / ".mcp.json").read_bytes() == data, out
    assert "mcpServers.radialrig" in row_of(out, ".mcp.json"), out
    assert code == 5


# Honest controls the suite did not already hold. They pass before and
# after the fix: an honest record from any release unmounts as before.

@pytest.mark.parametrize("host", ["none", "indent2"])
def test_a_record_from_before_the_write_server_still_unmounts(
    project, monkeypatch, host,
):
    """Before 2026-09-05 a mount registered the read server alone and
    recorded it under `entry`, with no `entries`. The mount is run with
    that one entry, and its record put back in that shape."""
    if host != "none":
        (project / ".mcp.json").write_bytes(MCP_HOSTS[host])
    before = tree(project)
    with monkeypatch.context() as m:
        m.setattr(mnt, "_mcp_entries",
                  lambda: {mnt.SERVER_KEY: mnt._mcp_entry()})
        mnt.mount(project, today=MOUNT_DAY)
    body = mnt.read_manifest(project)
    for entry in body["files"]:
        if entry["path"] == mnt.MCP_REL:
            entry.pop("entries", None)
            entry.pop("held", None)
            assert entry["keys"] == ["mcpServers.radialrig"]
            assert entry["entry"] == mnt._mcp_entry()
    (project / mnt.MANIFEST_REL).write_bytes(mnt._dump_manifest(body))

    assert cli.main(["unmount", str(project)]) == 0
    assert tree(project) == before


def test_a_schema_one_keys_only_record_still_takes_out_both_entries(project):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent2"])
    mnt.mount(project, today=MOUNT_DAY)
    legacy_manifest(project)
    assert cli.main(["unmount", str(project)]) == 5
    after = json.loads((project / ".mcp.json").read_bytes().decode("utf-8"))
    assert after == {"mcpServers": {"other": {"command": "x", "args": []}}}


def test_a_renamed_servers_member_around_untouched_entries_still_reverses(
    project,
):
    host = MCP_HOSTS["indent2"]
    (project / ".mcp.json").write_bytes(host)
    mnt.mount(project, today=MOUNT_DAY)
    mounted = (project / ".mcp.json").read_bytes()
    (project / ".mcp.json").write_bytes(
        mounted.replace(b'"mcpServers"', b'"servers"'))
    cli.main(["unmount", str(project)])
    assert (project / ".mcp.json").read_bytes() == host.replace(
        b'"mcpServers"', b'"servers"')


# --- PLAN-0022 phase 3: the code review's findings on the record checks
#
# The fresh review of phases 1 and 2 drove forged records the phase 2
# tests did not: values of an unexpected type, text nested past the
# parser's depth, a created-file claim on a file holding none of
# RadialRig's entries, a key written twice, and record text that prints
# rows of its own. Each answers with a row or a refusal and changes
# nothing the record forged.

# Nested past the depth Python's JSON reader takes, which it answers
# with RecursionError rather than a ValueError.
DEEP = "[" * 100_000 + "]" * 100_000


def unmount_answering(project: Path, *extra: str) -> int:
    """`radialrig unmount`, held to answering: a traceback where a row or
    a refusal belongs is the finding these tests reproduce."""
    try:
        return cli.main(["unmount", str(project), *extra])
    except Exception as exc:
        pytest.fail(f"unmount raised {exc!r} instead of answering")


def our_claim(data: bytes, *, file_created: bool, **more) -> dict:
    """A .mcp.json entry that claims RadialRig's own two entries, with
    their own values, the way a release records them."""
    ours = mnt._mcp_entries()
    return {"path": mnt.MCP_REL, "mode": mnt.MERGED,
            "file_created": file_created, "sha256": mnt._sha256(data),
            "written": True, "keys": [f"mcpServers.{key}" for key in ours],
            "entries": dict(ours), **more}


@pytest.mark.parametrize("mode", [["created"], {"mode": "created"}])
def test_a_record_entry_whose_mode_is_not_text_is_a_left_row(
    project, capsys, mode,
):
    kept = (project / "src" / "app.py").read_bytes()
    ship_record(project, [{"path": "src/app.py", "mode": mode}], [])

    code = unmount_answering(project)
    out = capsys.readouterr().out
    assert (project / "src" / "app.py").read_bytes() == kept
    line = row_of(out, "src/app.py")
    assert "left" in line and "never" in line, out
    assert code == 5


@pytest.mark.parametrize("route", ["recorded text", "created file", "parsed file"])
def test_mcp_text_nested_past_the_parsers_depth_is_left(project, capsys, route):
    if route == "recorded text":
        data = servers_file({"github": GITHUB})
        entry = our_claim(data, file_created=False,
                          insert=f'\n    "radialrig": {DEEP}')
    else:
        data = ('{"mcpServers": {}, "x": ' + DEEP + "}\n").encode("utf-8")
        entry = our_claim(data, file_created=(route == "created file"))
    (project / ".mcp.json").write_bytes(data)
    ship_record(project, [entry], [])

    code = unmount_answering(project)
    out = capsys.readouterr().out
    assert (project / ".mcp.json").read_bytes() == data, out
    assert row_of(out, ".mcp.json").startswith("left"), out
    assert code == 5


# How deep Python 3.14's reader goes depends on the host's stack limit:
# under an 8 MB stack DEEP raises RecursionError, and with none (GitHub's
# runner) it reads as a list. A record is refused either way.
DEEP_REFUSALS = ("does not parse as JSON", "does not hold a JSON object")


def test_a_mount_record_nested_past_the_parsers_depth_is_refused(
    project, capsys,
):
    (project / ".radialrig").mkdir()
    (project / mnt.MANIFEST_REL).write_bytes(DEEP.encode("utf-8"))
    before = tree(project)

    code = unmount_answering(project)
    err = capsys.readouterr().err
    assert tree(project) == before
    assert code != 0
    assert any(said in err for said in DEEP_REFUSALS), err


@pytest.mark.parametrize("held", ["empty", "empty servers", "a project server"])
def test_a_created_file_claim_removes_only_a_file_of_radialrigs_entries(
    project, capsys, held,
):
    """A digest says which bytes, not whose. A whole-file claim on a file
    holding none of RadialRig's entries, or one of the project's beside
    them, leaves the file. The last case passed before the fix: the
    phase 2 test of the created-file route claimed `github` itself, so a
    check before this one caught it."""
    data = {
        "empty": b"{}\n",
        "empty servers": b'{\n  "mcpServers": {}\n}\n',
        "a project server": servers_file({"github": GITHUB, **mnt._mcp_entries()}),
    }[held]
    (project / ".mcp.json").write_bytes(data)
    ship_record(project, [our_claim(data, file_created=True)], [])

    code = unmount_answering(project)
    out = capsys.readouterr().out
    assert (project / ".mcp.json").is_file(), f"unmount removed .mcp.json:\n{out}"
    assert (project / ".mcp.json").read_bytes() == data, out
    line = row_of(out, ".mcp.json")
    assert line.startswith("left") and "whole file" in line, out
    if held == "a project server":
        assert "mcpServers.github" in line, out
    assert code == 5


# A .mcp.json naming mcpServers twice: the project's server in the
# first, RadialRig's two entries in the second. A reader that keeps the
# last key sees RadialRig's alone.
REPEATED = (
    '{\n  "mcpServers": {\n    "github": ' + json.dumps(GITHUB) + "\n  },\n"
    '  "mcpServers": {\n'
    + ",\n".join(f'    "{key}": {json.dumps(value)}'
                 for key, value in mnt._mcp_entries().items())
    + "\n  }\n}\n"
).encode("utf-8")


@pytest.mark.parametrize("created", [True, False])
def test_a_file_with_a_key_written_twice_is_left_whole(project, capsys, created):
    """The recorded digest is another file's, so neither the whole-file
    route nor the text route applies, and the parsed-file route runs."""
    (project / ".mcp.json").write_bytes(REPEATED)
    entry = our_claim(b"other bytes", file_created=created)
    ship_record(project, [entry], [])

    code = unmount_answering(project)
    out = capsys.readouterr().out
    assert (project / ".mcp.json").is_file(), f"unmount removed .mcp.json:\n{out}"
    assert (project / ".mcp.json").read_bytes() == REPEATED, out
    line = row_of(out, ".mcp.json")
    assert line.startswith("left") and "twice" in line, out
    assert code == 5


def test_record_text_prints_no_row_of_its_own(project, capsys):
    """A forged path or key carrying a line break or a terminal control
    prints as one escaped row, never as a row of its own."""
    data = servers_file({"github": GITHUB})
    (project / ".mcp.json").write_bytes(data)
    forged_path = "src/app.py\nremoved   docs/spec.md\x1b[2K"
    forged_key = "mcpServers.x\nremoved   README.md"
    ship_record(project, [
        {"path": forged_path, "mode": mnt.CREATED, "file_created": True,
         "sha256": mnt._sha256((project / "src" / "app.py").read_bytes())},
        {"path": mnt.MCP_REL, "mode": mnt.MERGED, "file_created": False,
         "sha256": mnt._sha256(data), "written": True, "keys": [forged_key]},
    ], [])

    unmount_answering(project, "--dry-run")
    out = capsys.readouterr().out
    for line in out.splitlines():
        forged = "docs/spec.md" in line or "README.md" in line
        assert not (line.startswith("removed") and forged), (
            f"record text printed a row of its own: {line!r}\n{out}")
    assert "\x1b" not in out, out
    assert "src/app.py\\nremoved" in out and "mcpServers.x\\nremoved" in out, out


def test_a_record_that_names_itself_gets_one_row(project, capsys):
    before = tree(project)
    mnt.mount(project, today=MOUNT_DAY)
    claim(project, files=[{"path": mnt.MANIFEST_REL, "mode": mnt.CREATED,
                           "file_created": True, "sha256": "0" * 64}])

    code = unmount_answering(project)
    out = capsys.readouterr().out
    rows = [line for line in out.splitlines()
            if mnt.MANIFEST_REL in line and line.startswith(("left", "removed"))]
    assert len(rows) == 1 and rows[0].startswith("removed"), out
    assert code == 0
    assert tree(project) == before


def test_a_codex_region_nested_past_the_parsers_depth_is_left(project, capsys):
    """The region is project content, and the TOML reader recurses once
    per level, so a region nested deep enough answered with
    RecursionError where the edited-section row belongs."""
    mnt.mount(project, today=MOUNT_DAY)
    config = project / mnt.CODEX_REL
    text = config.read_bytes().decode("utf-8")
    head, rest = text.split(mnt.CODEX_FENCE_BEGIN, 1)
    _, tail = rest.split(mnt.CODEX_FENCE_END, 1)
    deep = "x = " + "[" * 10_000 + "]" * 10_000 + "\n"
    edited = (head + mnt.CODEX_FENCE_BEGIN + "\n" + deep
              + mnt.CODEX_FENCE_END + tail).encode("utf-8")
    config.write_bytes(edited)

    code = unmount_answering(project)
    out = capsys.readouterr().out
    assert config.read_bytes() == edited, out
    assert row_of(out, mnt.CODEX_REL).startswith("left"), out
    assert code == 5


def test_record_text_in_a_refusal_prints_no_line_of_its_own(project, capsys):
    """A refusal quotes the path it would not follow, and the path comes
    from the record."""
    forged = "../outside\nremoved   README.md\x1b[2K"
    ship_record(project, [{"path": forged, "mode": mnt.CREATED}], [])

    code = unmount_answering(project)
    err = capsys.readouterr().err
    for line in err.splitlines():
        assert not line.startswith("removed"), (
            f"record text printed a line of its own: {line!r}\n{err}")
    assert "\x1b" not in err, err
    assert "../outside\\nremoved" in err, err
    assert code != 0


# From the second reader (GPT): JSON null under a key RadialRig never
# writes, a number past Python's integer limit, and a lone surrogate.

@pytest.mark.parametrize("route", ["recorded text", "created file"])
def test_a_project_member_whose_value_is_null_is_never_ours(
    project, capsys, route,
):
    """A missing key and a null value read alike to `dict.get`, so a
    claim on the project's own `"github": null` member passed as
    RadialRig's."""
    data = servers_file({"github": None, **mnt._mcp_entries()})
    (project / ".mcp.json").write_bytes(data)
    if route == "recorded text":
        entry = our_claim(data, file_created=False,
                          insert='\n    "github": null,')
    else:
        entry = our_claim(data, file_created=True)
    ship_record(project, [entry], [])

    code = unmount_answering(project)
    out = capsys.readouterr().out
    assert (project / ".mcp.json").is_file(), f"unmount removed .mcp.json:\n{out}"
    assert (project / ".mcp.json").read_bytes() == data, out
    assert "mcpServers.github" in row_of(out, ".mcp.json"), out
    assert code == 5


def test_a_mount_record_with_a_number_past_the_readers_limit_is_refused(
    project, capsys,
):
    (project / ".radialrig").mkdir()
    (project / mnt.MANIFEST_REL).write_bytes(
        ('{"schema": ' + "1" * 5000 + "}").encode("utf-8"))
    before = tree(project)

    code = unmount_answering(project)
    err = capsys.readouterr().err
    assert tree(project) == before
    assert code != 0
    assert "does not parse as JSON" in err, err


def test_a_lone_surrogate_in_record_text_is_shown_as_its_escape(
    project, capsys,
):
    """JSON can carry a lone surrogate, which no UTF-8 stream can carry:
    the terminal and the page would both fail to write it."""
    data = servers_file({"github": GITHUB})
    (project / ".mcp.json").write_bytes(data)
    ship_record(project, [
        {"path": mnt.MCP_REL, "mode": mnt.MERGED, "file_created": False,
         "sha256": mnt._sha256(data), "written": True,
         "keys": ["mcpServers.SURROGATE"]},
    ], [])
    record = project / mnt.MANIFEST_REL
    record.write_bytes(record.read_bytes().replace(b"SURROGATE", b"\\ud800"))

    unmount_answering(project, "--dry-run")
    out = capsys.readouterr().out
    assert "\ud800" not in out, out.encode("utf-8", "backslashreplace")
    assert "mcpServers.\\ud800" in out, out
