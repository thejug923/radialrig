"""Tests for `radialrig upgrade` — PLAN-0005 Phase 3, prescription 4.

Run:  uv run --with pytest pytest src/radialrig/tests/test_upgrade.py -q

An upgrade applies changes only to files this project never edited, and
prints a diff for the rest. Only one version of the package is ever
importable, so a newer release is simulated the way the Phase 2 CRLF test
simulates a different checkout: `packaged_seed()` is pointed at a
modified copy of the seed sources and `__version__` is moved with it.

The comparison renders the installed seed with the **mount date** the
record carries, never today's. `{{DATE}}` fills `effective:` and
`last-verified:`; re-dating those because a package moved would make
every file differ on every upgrade, and it is exactly the date-gardening
SPEC-3 refuses.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import radialrig
from radialrig import cli
from radialrig import mount as mnt
from radialrig.paths import packaged_seed
from radialrig.tests.test_mount import (  # noqa: F401 - fixtures are used by name
    EXPECTED,
    MOUNT_DAY,
    NEXT_VERSION,
    _make_project,
    frontmatter,
    tree,
)
from radialrig.tests.test_unmount import (
    MCP_HOSTS,
    both_merges,
    crlf,
    legacy_manifest,
    marks,
)

UPGRADE_DAY = dt.date(2026, 9, 2)


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path / "sample-project")


@pytest.fixture()
def next_release(tmp_path, monkeypatch):
    """Install a newer radialrig: a copy of the seed, one edit, a version.

    Call it *after* the mount, so the record under test was written by
    the release the project actually mounted.
    """
    def install(edit=None, version=NEXT_VERSION) -> Path:
        base = packaged_seed()
        assert base is not None
        target = tmp_path / "next-seed"
        for source in sorted(base.rglob("*")):
            if source.is_file():
                copy = target / source.relative_to(base)
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(source.read_bytes())
        if edit is not None:
            edit(target)
        monkeypatch.setattr(radialrig, "__version__", version)
        monkeypatch.setattr(mnt, "packaged_seed", lambda: target)
        return target

    return install


def edit_spec(seed_root: Path) -> None:
    path = seed_root / "docs" / "spec.md"
    text = path.read_bytes().decode("utf-8")
    path.write_bytes(text.replace(
        "# SPEC-1", "# SPEC-1\n\nA sentence the newer release added.", 1
    ).encode("utf-8"))


def edit_section(seed_root: Path) -> None:
    path = seed_root / "AGENTS.section.md"
    text = path.read_bytes().decode("utf-8")
    path.write_bytes(text.replace(
        "### Paths", "### Paths\n\nA line the newer release added.", 1
    ).encode("utf-8"))


def record(root: Path, rel: str) -> dict:
    return next(e for e in mnt.read_manifest(root)["files"] if e["path"] == rel)


def verbs(plan) -> dict[str, str]:
    return {item.path: item.verb for item in plan.items}


def codes(plan) -> dict[str, str]:
    return {item.path: item.code for item in plan.items}


# --- E1-E2: the baseline ----------------------------------------------

def test_an_unmounted_project_is_a_no_op(project, capsys):
    before = tree(project)
    assert cli.main(["upgrade", str(project)]) == 4
    assert tree(project) == before
    assert "Not mounted" in capsys.readouterr().out


def test_the_same_version_and_seed_changes_nothing(project):
    mnt.mount(project, today=MOUNT_DAY)
    before = tree(project)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert set(verbs(plan).values()) == {mnt.CURRENT}
    assert plan.pending == 0
    assert tree(project) == before


# --- E3-E6: the created seed files ------------------------------------

def test_a_never_edited_file_moves_to_the_installed_seed(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    seed = next_release(edit_spec)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)["docs/spec.md"] == mnt.UPDATED
    assert codes(plan)["docs/spec.md"] == mnt.UPGRADE_APPLIED

    context = {"SCOPE": "sample-project", "OWNER": mnt.DEFAULT_OWNER,
               "DATE": MOUNT_DAY.isoformat(), "TOOL_CMD": mnt.SERVER_COMMAND}
    expected = mnt._render("docs/spec.md", context)
    assert (project / "docs" / "spec.md").read_bytes() == expected
    assert record(project, "docs/spec.md")["sha256"] == mnt._sha256(expected)
    assert seed.is_dir()


def test_an_edited_file_is_left_with_a_diff(project, next_release, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    edited = (project / "docs" / "spec.md").read_bytes() + b"\nOur own paragraph.\n"
    (project / "docs" / "spec.md").write_bytes(edited)
    before_hash = record(project, "docs/spec.md")["sha256"]
    next_release(edit_spec)

    assert cli.main(["upgrade", str(project)]) == 5
    out = capsys.readouterr().out
    assert "docs/spec.md (this project)" in out
    assert f"docs/spec.md (radialrig {NEXT_VERSION} seed)" in out
    assert (project / "docs" / "spec.md").read_bytes() == edited
    assert record(project, "docs/spec.md")["sha256"] == before_hash


def test_a_file_edited_into_the_new_seed_keeps_its_recorded_hash(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    before_hash = record(project, "docs/spec.md")["sha256"]
    next_release(edit_spec)
    context = {"SCOPE": "sample-project", "OWNER": mnt.DEFAULT_OWNER,
               "DATE": MOUNT_DAY.isoformat(), "TOOL_CMD": mnt.SERVER_COMMAND}
    theirs = mnt._render("docs/spec.md", context)
    (project / "docs" / "spec.md").write_bytes(theirs)

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)["docs/spec.md"] == mnt.CURRENT
    # The recorded hash means "the bytes radialrig last wrote here", and
    # radialrig did not write these. Keeping it exact costs one thing:
    # a later unmount leaves the file, and says why.
    assert record(project, "docs/spec.md")["sha256"] == before_hash
    unmounted = mnt.unmount(project)
    assert marks(unmounted)["docs/spec.md"] == (mnt.LEFT, mnt.LEFT_EDITED)


def test_a_deleted_file_is_not_restored(project, next_release, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "templates" / "spec.md").unlink()
    next_release(edit_spec)
    assert cli.main(["upgrade", str(project)]) == 5
    assert "does not restore a removed file" in capsys.readouterr().out
    assert not (project / "docs" / "templates" / "spec.md").exists()


# --- E6a-E6c: a seed path the record never carried ---------------------

NEW_REL = "docs/archive/D-00-archive-exclusion-target.md"


def forget(root: Path, rel: str) -> None:
    """Make the record read like one an earlier release wrote.

    That release shipped no such path, so its record names no entry for
    it. Everything else in the record stays as the mount wrote it.
    """
    path = root / mnt.MANIFEST_REL
    body = json.loads(path.read_text(encoding="utf-8"))
    body["files"] = [e for e in body["files"] if e.get("path") != rel]
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def test_a_seed_path_the_record_never_carried_is_written_and_recorded(
    project, next_release,
):
    mnt.mount(project, today=MOUNT_DAY)
    forget(project, NEW_REL)
    (project / NEW_REL).unlink()
    next_release(edit_spec)

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)[NEW_REL] == mnt.CREATED
    assert codes(plan)[NEW_REL] == mnt.UPGRADE_NEW_WRITTEN
    # The summary line counts the written file: docs/spec.md updated and
    # the new path created make two applied, none left for review.
    assert plan.applied == 2
    assert plan.pending == 0

    context = {"SCOPE": "sample-project", "OWNER": mnt.DEFAULT_OWNER,
               "DATE": MOUNT_DAY.isoformat(), "TOOL_CMD": mnt.SERVER_COMMAND}
    expected = mnt._render(NEW_REL, context)
    assert (project / NEW_REL).read_bytes() == expected
    entry = record(project, NEW_REL)
    assert entry["mode"] == mnt.CREATED
    assert entry["file_created"] is True
    assert entry["sha256"] == mnt._sha256(expected)


def test_a_file_already_standing_at_a_new_seed_path_is_left(
    project, next_release, capsys,
):
    mnt.mount(project, today=MOUNT_DAY)
    forget(project, NEW_REL)
    ours = b"---\nid: D-00\n---\n\nOur own file, at that path first.\n"
    (project / NEW_REL).write_bytes(ours)
    next_release(edit_spec)

    assert cli.main(["upgrade", str(project)]) == 5
    out = capsys.readouterr().out
    assert "already has a file there" in out
    assert (project / NEW_REL).read_bytes() == ours
    with pytest.raises(StopIteration):
        record(project, NEW_REL)


def test_unmount_removes_what_the_upgrade_created(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    forget(project, NEW_REL)
    (project / NEW_REL).unlink()
    next_release(edit_spec)
    mnt.upgrade(project, today=UPGRADE_DAY)

    done = mnt.unmount(project)
    assert marks(done)[NEW_REL] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert not (project / NEW_REL).exists()


# --- E7-E10: AGENTS.md -------------------------------------------------

def test_the_fenced_section_is_spliced_in_place(project, next_release):
    original = b"# Host rules\n\nOurs, above.\n"
    (project / "AGENTS.md").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    before = mnt.read_manifest(project)
    before_entry = next(e for e in before["files"] if e["path"] == "AGENTS.md")

    next_release(edit_section)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)["AGENTS.md"] == mnt.UPDATED
    assert codes(plan)["AGENTS.md"] == mnt.UPGRADE_SECTION_APPLIED

    data = (project / "AGENTS.md").read_bytes()
    assert data.startswith(original)
    assert b"A line the newer release added." in data
    after = record(project, "AGENTS.md")
    assert after["section_sha256"] != before_entry["section_sha256"]
    assert after["sha256"] != before_entry["sha256"]
    assert after["pre_mount_sha256"] == before_entry["pre_mount_sha256"]
    assert after["newline_added"] == before_entry["newline_added"]


def test_an_upgraded_section_still_names_the_write_command(project, next_release):
    # BUG-0005: the upgrade renders the section with the same context the
    # mount used, so the substituted write command survives the splice.
    mnt.mount(project, today=MOUNT_DAY)
    next_release(edit_section)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)["AGENTS.md"] == mnt.UPDATED
    text = (project / "AGENTS.md").read_text(encoding="utf-8").replace("\n", " ")
    assert "A line the newer release added." in text
    assert f"running `{mnt.WRITE_SERVER_COMMAND}`" in text
    assert "{{" not in text


def test_an_upgraded_section_still_unmounts_to_the_original(project, next_release):
    original = b"# Host rules\n\nOurs, above."
    (project / "AGENTS.md").write_bytes(original)
    before = tree(project)
    mnt.mount(project, today=MOUNT_DAY)
    next_release(edit_section)
    mnt.upgrade(project, today=UPGRADE_DAY)
    mnt.unmount(project)
    assert tree(project) == before


def test_an_edited_section_is_left_with_a_diff(project, next_release, capsys):
    (project / "AGENTS.md").write_bytes(b"# Host rules\n")
    mnt.mount(project, today=MOUNT_DAY)
    data = (project / "AGENTS.md").read_bytes()
    changed = data.replace(b"## RadialRig route map", b"## Our route map")
    (project / "AGENTS.md").write_bytes(changed)
    next_release(edit_section)

    assert cli.main(["upgrade", str(project)]) == 5
    out = capsys.readouterr().out
    assert "AGENTS.md (this project)" in out
    assert (project / "AGENTS.md").read_bytes() == changed


@pytest.mark.parametrize("wreck", ["fences", "missing"])
def test_an_unreadable_section_is_left(project, next_release, wreck):
    (project / "AGENTS.md").write_bytes(b"# Host rules\n")
    mnt.mount(project, today=MOUNT_DAY)
    if wreck == "fences":
        data = (project / "AGENTS.md").read_bytes()
        (project / "AGENTS.md").write_bytes(
            data.replace(mnt.FENCE_END.encode("utf-8"), b"")
        )
    else:
        (project / "AGENTS.md").unlink()
    kept = (project / "AGENTS.md").read_bytes() if wreck == "fences" else None
    next_release(edit_section)

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)["AGENTS.md"] == mnt.LEFT
    assert codes(plan)["AGENTS.md"] == mnt.UPGRADE_SECTION_UNREADABLE
    if kept is not None:
        assert (project / "AGENTS.md").read_bytes() == kept


# --- BUG-0004: the fence and docs/spec.md move together ----------------

def edit_pair(seed_root: Path) -> None:
    edit_spec(seed_root)
    edit_section(seed_root)


def amend_spec(project: Path) -> bytes:
    path = project / "docs" / "spec.md"
    edited = path.read_bytes() + b"\nOur own paragraph.\n"
    path.write_bytes(edited)
    return edited


def amend_section(project: Path) -> bytes:
    path = project / "AGENTS.md"
    changed = path.read_bytes().replace(b"## RadialRig route map", b"## Our route map")
    path.write_bytes(changed)
    return changed


def test_an_edited_spec_holds_the_unedited_fence_back(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    fence_before = (project / "AGENTS.md").read_bytes()
    record_before = record(project, "AGENTS.md")
    amend_spec(project)
    next_release(edit_pair)

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert codes(plan)["docs/spec.md"] == mnt.UPGRADE_PENDING
    assert codes(plan)["AGENTS.md"] == mnt.UPGRADE_HELD
    assert verbs(plan)["AGENTS.md"] == mnt.LEFT
    assert plan.pending == 2
    held = next(i for i in plan.items if i.path == "AGENTS.md")
    assert held.detail == "docs/spec.md"
    assert "docs/spec.md changed in this project" in mnt.reason_text(held.disposition())
    assert "AGENTS.md (this project)" in held.diff
    assert "A line the newer release added." in held.diff
    assert (project / "AGENTS.md").read_bytes() == fence_before
    assert record(project, "AGENTS.md") == record_before


def test_an_edited_fence_holds_the_unedited_spec_back(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    spec_before = (project / "docs" / "spec.md").read_bytes()
    record_before = record(project, "docs/spec.md")
    amend_section(project)
    next_release(edit_pair)

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert codes(plan)["AGENTS.md"] == mnt.UPGRADE_SECTION_PENDING
    assert codes(plan)["docs/spec.md"] == mnt.UPGRADE_HELD
    assert plan.pending == 2
    held = next(i for i in plan.items if i.path == "docs/spec.md")
    assert held.detail == "AGENTS.md"
    assert "docs/spec.md (this project)" in held.diff
    assert (project / "docs" / "spec.md").read_bytes() == spec_before
    assert record(project, "docs/spec.md") == record_before


def test_an_unedited_pair_moves_together(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    next_release(edit_pair)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert codes(plan)["docs/spec.md"] == mnt.UPGRADE_APPLIED
    assert codes(plan)["AGENTS.md"] == mnt.UPGRADE_SECTION_APPLIED
    assert plan.pending == 0
    assert b"A sentence the newer release added." in (
        project / "docs" / "spec.md").read_bytes()
    assert b"A line the newer release added." in (project / "AGENTS.md").read_bytes()


def test_an_edited_pair_parks_both_as_before(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    spec = amend_spec(project)
    fence = amend_section(project)
    next_release(edit_pair)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert codes(plan)["docs/spec.md"] == mnt.UPGRADE_PENDING
    assert codes(plan)["AGENTS.md"] == mnt.UPGRADE_SECTION_PENDING
    assert mnt.UPGRADE_HELD not in codes(plan).values()
    assert (project / "docs" / "spec.md").read_bytes() == spec
    assert (project / "AGENTS.md").read_bytes() == fence


def test_the_dry_run_report_says_why_the_partner_waits(project, next_release, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    amend_spec(project)
    next_release(edit_pair)
    before = tree(project)

    assert cli.main(["upgrade", str(project), "--dry-run"]) == 5
    out = capsys.readouterr().out
    assert tree(project) == before
    assert ("unchanged here, but docs/spec.md changed in this project and "
            "the two upgrade together; waits for that merge; the diff follows") in out
    assert "AGENTS.md (this project)" in out
    assert f"AGENTS.md (radialrig {NEXT_VERSION} seed)" in out
    assert "docs/spec.md (this project)" in out
    assert "0 file(s) applied, 2 left for review." in out


# --- E11-E13: .mcp.json ------------------------------------------------

def test_a_changed_entry_replaces_only_its_region(project, next_release, monkeypatch):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    mnt.mount(project, today=MOUNT_DAY)
    before = (project / ".mcp.json").read_bytes()
    old_insert = record(project, ".mcp.json")["insert"]

    next_release()
    monkeypatch.setattr(
        mnt, "_mcp_entry", lambda: {"command": "radialrig-read", "args": ["--stdio"]}
    )
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)[".mcp.json"] == mnt.UPDATED
    assert codes(plan)[".mcp.json"] == mnt.UPGRADE_ENTRY_APPLIED

    after = (project / ".mcp.json").read_bytes()
    entry = record(project, ".mcp.json")
    assert entry["entry"] == {"command": "radialrig-read", "args": ["--stdio"]}
    assert entry["insert"] != old_insert
    # Every byte outside the region survived: removing the new region
    # gives back the file as it stood before the mount.
    assert after.replace(entry["insert"].encode("utf-8"), b"", 1) == MCP_HOSTS["indent4"]
    assert before != after
    data = json.loads(after.decode("utf-8"))
    assert data["mcpServers"]["other"] == {"command": "x", "args": []}


def test_an_entry_this_project_changed_is_left(project, next_release, monkeypatch, capsys):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    mnt.mount(project, today=MOUNT_DAY)
    text = (project / ".mcp.json").read_bytes().decode("utf-8")
    changed = text.replace('"radialrig": {"command": "radialrig-read", "args": []}',
                           '"radialrig": {"command": "our-wrapper", "args": []}', 1)
    (project / ".mcp.json").write_bytes(changed.encode("utf-8"))
    next_release()
    monkeypatch.setattr(
        mnt, "_mcp_entry", lambda: {"command": "radialrig-read", "args": ["--stdio"]}
    )

    assert cli.main(["upgrade", str(project)]) == 5
    out = capsys.readouterr().out
    assert "mcpServers.radialrig" in out
    assert "our-wrapper" in out
    assert (project / ".mcp.json").read_bytes().decode("utf-8") == changed


def test_a_host_owned_key_is_never_touched(project, next_release, monkeypatch):
    original = (json.dumps(
        {"mcpServers": {"radialrig": {"command": "theirs", "args": []},
                        "radialrig-write": {"command": "theirs", "args": []}}},
        indent=2,
    ) + "\n").encode("utf-8")
    (project / ".mcp.json").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    next_release(edit_spec)
    monkeypatch.setattr(
        mnt, "_mcp_entry", lambda: {"command": "radialrig-read", "args": ["--stdio"]}
    )
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)[".mcp.json"] == mnt.CURRENT
    assert codes(plan)[".mcp.json"] == mnt.UPGRADE_MCP_HOST_OWNED
    assert (project / ".mcp.json").read_bytes() == original


# --- PLAN-0012 phase 1: the write entry an older mount never wrote ----
# A mount made before the write server was registered carries one key in
# its record. Upgrade adds the other as a seed path the record never
# carried, in the shape of E3-E6, one level down: inside the file.

WRITE_ROW = f".mcp.json :: mcpServers.{mnt.WRITE_SERVER_KEY}"
WRITE_ENTRY = {"command": "radialrig-write", "args": []}


def old_mount(project: Path, monkeypatch) -> None:
    """A mount as the release before PLAN-0012 wrote it: the read server
    alone in `.mcp.json`, and a record that knows nothing of `entries`."""
    with monkeypatch.context() as patched:
        patched.setattr(mnt, "_mcp_entries",
                        lambda: {mnt.SERVER_KEY: mnt._mcp_entry()})
        mnt.mount(project, today=MOUNT_DAY)
    body = mnt.read_manifest(project)
    for entry in body["files"]:
        entry.pop("entries", None)
    (project / mnt.MANIFEST_REL).write_bytes(mnt._dump_manifest(body))
    assert WRITE_ROW.split(" :: ")[1] not in record(project, ".mcp.json")["keys"]


def test_upgrade_adds_the_write_entry_to_a_region_the_mount_inserted(
    project, next_release, monkeypatch,
):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    old_mount(project, monkeypatch)
    old_insert = record(project, ".mcp.json")["insert"]
    next_release()

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)[".mcp.json"] == mnt.CURRENT
    assert (verbs(plan)[WRITE_ROW], codes(plan)[WRITE_ROW]) == (
        mnt.CREATED, mnt.UPGRADE_ENTRY_NEW_WRITTEN)
    assert plan.applied == 1 and plan.pending == 0

    after = (project / ".mcp.json").read_bytes()
    data = json.loads(after.decode("utf-8"))
    assert data["mcpServers"]["radialrig-write"] == WRITE_ENTRY
    assert data["mcpServers"]["other"] == {"command": "x", "args": []}
    entry = record(project, ".mcp.json")
    assert entry["keys"] == ["mcpServers.radialrig", "mcpServers.radialrig-write"]
    assert entry["entries"] == {"radialrig": mnt._mcp_entry(),
                                "radialrig-write": WRITE_ENTRY}
    assert entry["insert"] != old_insert
    # One region holds both keys, and every byte outside it is the host's:
    # removing the region gives back the file from before the old mount.
    assert after.replace(entry["insert"].encode("utf-8"), b"", 1) == MCP_HOSTS["indent4"]

    done = mnt.unmount(project)
    assert marks(done)[".mcp.json"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / ".mcp.json").read_bytes() == MCP_HOSTS["indent4"]


def test_upgrade_adds_the_write_entry_to_a_file_the_mount_created(
    project, next_release, monkeypatch,
):
    old_mount(project, monkeypatch)
    next_release()

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert codes(plan)[WRITE_ROW] == mnt.UPGRADE_ENTRY_NEW_WRITTEN
    after = (project / ".mcp.json").read_bytes()
    assert after == mnt._fresh_mcp_bytes(mnt._mcp_entries())
    entry = record(project, ".mcp.json")
    assert entry["file_created"] is True
    assert entry["sha256"] == mnt._sha256(after)

    done = mnt.unmount(project)
    assert marks(done)[".mcp.json"] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert not (project / ".mcp.json").exists()


def test_upgrade_adds_the_write_entry_beside_a_host_owned_read_key(
    project, next_release, monkeypatch,
):
    original = (json.dumps(
        {"mcpServers": {"radialrig": {"command": "theirs", "args": []}}}, indent=2
    ) + "\n").encode("utf-8")
    (project / ".mcp.json").write_bytes(original)
    old_mount(project, monkeypatch)
    assert record(project, ".mcp.json")["written"] is False
    next_release()

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert codes(plan)[".mcp.json"] == mnt.UPGRADE_MCP_HOST_OWNED
    assert codes(plan)[WRITE_ROW] == mnt.UPGRADE_ENTRY_NEW_WRITTEN
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["radialrig"] == {"command": "theirs", "args": []}
    assert data["mcpServers"]["radialrig-write"] == WRITE_ENTRY
    entry = record(project, ".mcp.json")
    assert entry["written"] is True
    assert entry["keys"] == ["mcpServers.radialrig-write"]
    assert entry["pre_mount_sha256"] == mnt._sha256(original)

    done = mnt.unmount(project)
    assert marks(done)[".mcp.json"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / ".mcp.json").read_bytes() == original


def test_a_write_key_the_project_already_has_is_left(
    project, next_release, monkeypatch, capsys,
):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    old_mount(project, monkeypatch)
    theirs = {"command": "our-own-writer", "args": []}
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    data["mcpServers"]["radialrig-write"] = theirs
    edited = (json.dumps(data, indent=4) + "\n").encode("utf-8")
    (project / ".mcp.json").write_bytes(edited)
    next_release()

    assert cli.main(["upgrade", str(project)]) == 5
    out = capsys.readouterr().out
    assert "already has an entry under that key" in out
    assert (project / ".mcp.json").read_bytes() == edited
    entry = record(project, ".mcp.json")
    assert entry["keys"] == ["mcpServers.radialrig"]
    assert "entries" not in entry


def test_a_region_the_project_broke_parks_the_write_entry_with_a_diff(
    project, next_release, monkeypatch, capsys,
):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    old_mount(project, monkeypatch)
    # The whole file reformatted: the recorded region is nowhere to be
    # found, so nothing is spliced and the entry to add is shown.
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    reformatted = (json.dumps(data, indent=8) + "\n").encode("utf-8")
    (project / ".mcp.json").write_bytes(reformatted)
    next_release()

    assert cli.main(["upgrade", str(project)]) == 5
    out = capsys.readouterr().out
    assert "the entry to add follows" in out
    assert f'+  "command": "{mnt.WRITE_SERVER_COMMAND}"' in out
    assert (project / ".mcp.json").read_bytes() == reformatted


def test_the_write_entry_addition_is_the_same_on_a_rehearsal(
    project, next_release, monkeypatch, capsys,
):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent2"])
    old_mount(project, monkeypatch)
    next_release()
    before = tree(project)

    assert cli.main(["upgrade", str(project), "--dry-run"]) == 0
    dry = capsys.readouterr().out
    assert tree(project) == before
    assert WRITE_ROW in dry
    assert "1 file(s) applied, 0 left for review." in dry

    assert cli.main(["upgrade", str(project)]) == 0
    real = capsys.readouterr().out
    dry_block = [ln for ln in dry.splitlines() if ln.startswith(mnt.VERBS)]
    real_block = [ln for ln in real.splitlines() if ln.startswith(mnt.VERBS)]
    assert dry_block == real_block

    # And the second run finds nothing to add.
    assert cli.main(["upgrade", str(project)]) == 0
    assert WRITE_ROW not in capsys.readouterr().out


def test_a_current_mount_has_no_write_entry_to_add(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    next_release()
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert WRITE_ROW not in codes(plan)
    assert plan.writes == ()


def test_a_partial_mount_does_not_park_on_later_upgrades(project, next_release):
    # A mount today onto a host that already had the read key writes the
    # write key alone and records that judgment under `entries`. A later
    # upgrade sees the read key the record omits, knows it was left as
    # the host's, and says nothing: no row, no write, exit 0 every time.
    theirs = {"command": "theirs", "args": []}
    original = (json.dumps({"mcpServers": {"radialrig": theirs}}, indent=2)
                + "\n").encode("utf-8")
    (project / ".mcp.json").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    assert record(project, ".mcp.json")["keys"] == ["mcpServers.radialrig-write"]
    after_mount = (project / ".mcp.json").read_bytes()
    next_release()

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert [i.path for i in plan.items if i.path.startswith(".mcp.json")] == [".mcp.json"]
    assert codes(plan)[".mcp.json"] == mnt.UPGRADE_ENTRY_CURRENT
    assert plan.writes == ()
    assert (project / ".mcp.json").read_bytes() == after_mount

    done = mnt.unmount(project)
    assert marks(done)[".mcp.json"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / ".mcp.json").read_bytes() == original


def test_an_old_host_owned_mount_holding_both_keys_gets_one_row(
    project, next_release, monkeypatch,
):
    # Before the write server, a host that had the read key got nothing
    # written. If it also holds a write key of its own, the read step's
    # host-owned row covers the file; the addition adds no second row.
    theirs = {"command": "theirs", "args": []}
    original = (json.dumps({"mcpServers": {
        "radialrig": theirs, "radialrig-write": theirs}}, indent=2) + "\n").encode("utf-8")
    (project / ".mcp.json").write_bytes(original)
    old_mount(project, monkeypatch)
    next_release()

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert [i.path for i in plan.items if i.path.startswith(".mcp.json")] == [".mcp.json"]
    assert codes(plan)[".mcp.json"] == mnt.UPGRADE_MCP_HOST_OWNED
    assert (project / ".mcp.json").read_bytes() == original


# --- E14-E19: the record and the mechanics ----------------------------

def test_the_record_after_a_write(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    before = mnt.read_manifest(project)
    next_release(edit_spec)
    mnt.upgrade(project, today=UPGRADE_DAY)
    after = mnt.read_manifest(project)
    assert after["radialrig"] == NEXT_VERSION
    assert after["upgraded"] == UPGRADE_DAY.isoformat()
    assert after["mounted"] == before["mounted"] == MOUNT_DAY.isoformat()
    assert after["scope"] == before["scope"]
    assert after["owner"] == before["owner"]
    assert after["dirs"] == before["dirs"]


def test_a_pure_current_run_rewrites_nothing(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    before = tree(project)
    # The version the mount recorded is whatever release is installed,
    # so it is read back rather than written as a literal here: a literal
    # breaks on every release bump without saying anything new.
    mounted = mnt.read_manifest(project)["radialrig"]
    next_release()  # a version bump with no seed change
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert plan.writes == ()
    assert tree(project) == before
    assert mounted != NEXT_VERSION, "the mount already carried the next version"
    assert mnt.read_manifest(project)["radialrig"] == mounted
    assert "upgraded" not in mnt.read_manifest(project)


def test_upgrade_writes_all_or_nothing(project, next_release, monkeypatch):
    mnt.mount(project, today=MOUNT_DAY)

    def edit_both(seed_root: Path) -> None:
        edit_spec(seed_root)
        path = seed_root / "docs" / "spec-git-environment.md"
        text = path.read_bytes().decode("utf-8")
        path.write_bytes((text + "\nA newer paragraph.\n").encode("utf-8"))

    next_release(edit_both)
    before = tree(project)
    real = Path.write_bytes

    def refuse(self, data):
        if self.name == "spec.md":
            raise OSError("disk full")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", refuse)
    with pytest.raises(mnt.WriteStopped):
        mnt.upgrade(project, today=UPGRADE_DAY)
    monkeypatch.undo()
    assert tree(project) == before


def test_upgrade_touches_no_path_outside_the_record(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "decisions" / "D-01-ours.md").write_bytes(b"# D-01\n")
    before_files, before_dirs = tree(project)
    next_release(edit_spec)
    mnt.upgrade(project, today=UPGRADE_DAY)
    after_files, after_dirs = tree(project)
    assert set(after_files) == set(before_files)
    assert after_dirs == before_dirs
    changed = {rel for rel in before_files if before_files[rel] != after_files[rel]}
    assert changed == {"docs/spec.md", mnt.MANIFEST_REL}


def test_dry_run_reports_the_same_and_writes_nothing(project, next_release, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    (project / "docs" / "spec-git-environment.md").write_bytes(b"ours now\n")
    next_release(edit_spec)
    before = tree(project)

    assert cli.main(["upgrade", str(project), "--dry-run"]) == 5
    dry = capsys.readouterr().out
    assert tree(project) == before

    assert cli.main(["upgrade", str(project)]) == 5
    real = capsys.readouterr().out
    dry_block = [ln for ln in dry.splitlines() if ln.startswith(mnt.VERBS)]
    real_block = [ln for ln in real.splitlines() if ln.startswith(mnt.VERBS)]
    assert dry_block == real_block
    assert "@@" in dry and "@@" in real
    assert cli.DRY_RUN_TAIL in dry
    assert cli.DRY_RUN_TAIL not in real


def test_a_second_upgrade_is_all_current(project, next_release, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    next_release(edit_spec)
    assert cli.main(["upgrade", str(project)]) == 0
    capsys.readouterr()
    before = tree(project)
    assert cli.main(["upgrade", str(project)]) == 0
    assert tree(project) == before


# --- BUG-0011: a CRLF checkout is not an edit --------------------------
# A git checkout with core.autocrlf=true rewrites every seeded file with
# CRLF and changes no word. The record keeps the LF hash; the comparison
# reads the file as it stands or with CRLF read as LF. A file that is
# radialrig's whole comes back LF, as the mount wrote it; in a file the
# project owns, only the fenced section or the region is rewritten, and
# every byte outside it stays as the checkout left it, CR included
# (review 2026-09-05 of the first fix).

def test_a_crlf_checkout_is_current_at_the_same_version(project, capsys):
    both_merges(project)
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, EXPECTED)
    before = tree(project)
    plan = mnt.upgrade_plan(project)
    assert set(verbs(plan).values()) == {mnt.CURRENT}, codes(plan)
    assert plan.pending == 0
    assert cli.main(["upgrade", str(project), "--dry-run"]) == 0
    assert tree(project) == before


def test_a_crlf_checkout_upgrades_clean_to_a_newer_seed(
    project, next_release, monkeypatch
):
    both_merges(project)
    pristine = tree(project)
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, EXPECTED)
    next_release(edit_pair)
    monkeypatch.setattr(
        mnt, "_mcp_entry", lambda: {"command": "radialrig-read", "args": ["--stdio"]}
    )

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert plan.pending == 0, codes(plan)
    assert codes(plan)["docs/spec.md"] == mnt.UPGRADE_APPLIED
    assert codes(plan)["AGENTS.md"] == mnt.UPGRADE_SECTION_APPLIED
    assert codes(plan)[".mcp.json"] == mnt.UPGRADE_ENTRY_APPLIED
    # docs/spec.md is radialrig's whole file: written LF, as the mount
    # wrote it. (The first fix asserted the same of AGENTS.md and
    # .mcp.json, which pinned the CR deletion the review found.)
    assert b"\r\n" not in (project / "docs" / "spec.md").read_bytes()
    context = {"SCOPE": "sample-project", "OWNER": mnt.DEFAULT_OWNER,
               "DATE": MOUNT_DAY.isoformat(), "TOOL_CMD": mnt.SERVER_COMMAND,
               "WRITE_TOOL_CMD": mnt.WRITE_SERVER_COMMAND}
    expected = mnt._render("docs/spec.md", context)
    assert (project / "docs" / "spec.md").read_bytes() == expected
    assert record(project, "docs/spec.md")["sha256"] == mnt._sha256(expected)
    # AGENTS.md: the project's prose stays as the checkout left it, the
    # new section goes in LF as the mount writes it.
    agents = (project / "AGENTS.md").read_bytes()
    start, end = mnt._locate_section(agents)
    assert agents[:start] == b"# Host rules\r\n\r\nA line.\r\n\r\n"
    assert agents[end:] == b""
    assert agents[start:end] == mnt._section_bytes(context)
    # .mcp.json: the region is rewritten in the file's own endings and
    # recorded as written; every other byte is the flipped host file.
    after = (project / ".mcp.json").read_bytes()
    insert = record(project, ".mcp.json")["insert"]
    assert "\r\n" in insert
    assert after.replace(insert.encode("utf-8"), b"", 1) == MCP_HOSTS[
        "indent4"].replace(b"\n", b"\r\n")

    # The files the upgrade did not touch are still CRLF, and unmount
    # reads them the same way: the tree comes back as it was found.
    unmounted = mnt.unmount(project)
    assert unmounted.complete, marks(unmounted)
    assert tree(project) == pristine


def test_a_crlf_authored_host_keeps_its_bytes_outside_the_fence(
    project, next_release,
):
    # The review's shape: the host's own prose carried CRLF before the
    # mount. The splice touches the section alone.
    original = b"# Ours\r\n\r\nOur line.\r\n"
    (project / "AGENTS.md").write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, ["AGENTS.md"])
    flipped = (project / "AGENTS.md").read_bytes()
    prefix = flipped[:mnt._locate_section(flipped)[0]]
    assert prefix == original + b"\r\n"
    next_release(edit_section)

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert codes(plan)["AGENTS.md"] == mnt.UPGRADE_SECTION_APPLIED
    after = (project / "AGENTS.md").read_bytes()
    start, end = mnt._locate_section(after)
    assert after[:start] == prefix
    assert after[end:] == b""
    assert b"A line the newer release added." in after[start:end]
    assert b"\r\n" not in after[start:end]

    done = mnt.unmount(project)
    assert marks(done)["AGENTS.md"] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / "AGENTS.md").read_bytes() == original


def test_a_held_fence_on_a_crlf_checkout_shows_the_whole_section(
    project, next_release,
):
    # The hold's diff used to slice the raw section one byte short of
    # the closing fence's CRLF; the shown side is now the whole section.
    mnt.mount(project, today=MOUNT_DAY)
    amend_spec(project)
    crlf(project, ["AGENTS.md"])
    next_release(edit_pair)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert codes(plan)["AGENTS.md"] == mnt.UPGRADE_HELD
    held = next(i for i in plan.items if i.path == "AGENTS.md")
    assert "AGENTS.md (this project)" in held.diff
    assert f"-{mnt.FENCE_END}\r\n" in held.diff


def test_a_crlf_file_with_a_real_edit_is_still_left(project, next_release, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    path = project / "docs" / "spec.md"
    path.write_bytes(path.read_bytes() + b"\nOur own paragraph.\n")
    crlf(project, EXPECTED)
    edited = path.read_bytes()
    before_hash = record(project, "docs/spec.md")["sha256"]
    next_release(edit_spec)

    assert cli.main(["upgrade", str(project)]) == 5
    out = capsys.readouterr().out
    assert "docs/spec.md (this project)" in out
    assert path.read_bytes() == edited
    assert record(project, "docs/spec.md")["sha256"] == before_hash


# --- E20-E22: degradation, documentation, dates -----------------------

def test_a_schema_one_record_upgrades_its_created_files(project, next_release, capsys):
    (project / ".mcp.json").write_bytes(MCP_HOSTS["indent4"])
    (project / "AGENTS.md").write_bytes(b"# Host rules\n")
    mnt.mount(project, today=MOUNT_DAY)
    legacy_manifest(project)
    next_release(edit_spec)

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert plan.degraded is True
    assert verbs(plan)["docs/spec.md"] == mnt.UPDATED
    assert b"A sentence the newer release added." in (
        project / "docs" / "spec.md").read_bytes()
    assert verbs(plan)[".mcp.json"] == mnt.CURRENT
    assert verbs(plan)["AGENTS.md"] == mnt.CURRENT

    cli.main(["upgrade", str(project)])
    assert "carries no restore fields" in capsys.readouterr().out


def test_the_report_documents_the_engine_half(project, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    assert cli.main(["upgrade", str(project)]) == 0
    out = capsys.readouterr().out
    assert cli.ENGINE_HALF in out
    assert 'uv tool install "radialrig @' in out
    # BUG-0010: the install replaces executables a running app holds.
    assert (
        "Close the RadialRig app and any manage page before that install: "
        "they hold the executables it replaces."
    ) in out
    assert (
        "Close the RadialRig app and any manage page before that install: "
        "they hold the executables it replaces."
    ) in " ".join(cli.UPGRADE_EPILOG.split())
    assert "uv tool upgrade radialrig" not in out


def test_an_upgrade_does_not_re_date_a_record(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    before = frontmatter(project / "docs" / "spec.md")
    next_release(edit_spec)
    mnt.upgrade(project, today=UPGRADE_DAY)
    after = frontmatter(project / "docs" / "spec.md")
    assert after["last-verified"] == before["last-verified"] == MOUNT_DAY.isoformat()
    assert after["effective"] == before["effective"] == MOUNT_DAY.isoformat()


# --- PLAN-0017 phase 2 (mount-02): the manifest lands last -------------

def test_upgrade_writes_the_manifest_after_every_other_path(
    project, next_release, monkeypatch,
):
    mnt.mount(project, today=MOUNT_DAY)
    next_release(edit_spec)
    real = Path.write_bytes
    seen: list[str] = []

    def record(self, data):
        seen.append(self.name)
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", record)
    mnt.upgrade(project, today=UPGRADE_DAY)
    assert seen == ["spec.md", "mount.json"]


# --- PLAN-0017 phase 2 (mount-01): a removed entry is not put back -----
# The discriminator is the `entries` field, never the manifest's stamp:
# neither test moves the version, so the stamp is the running one.

def _both_keys_host(project: Path) -> None:
    theirs = {"command": "theirs", "args": []}
    (project / ".mcp.json").write_bytes((json.dumps({"mcpServers": {
        "radialrig": theirs, "radialrig-write": theirs}}, indent=2) + "\n").encode("utf-8"))


def _drop_write_key(project: Path) -> bytes:
    """What the owner does to switch capture off (D-35 commitment 6)."""
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    del data["mcpServers"][mnt.WRITE_SERVER_KEY]
    edited = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    (project / ".mcp.json").write_bytes(edited)
    return edited


def test_upgrade_does_not_restore_a_write_entry_removed_from_a_judged_record(
    project, capsys,
):
    # A written: false record carrying `entries` was judged by a build
    # that knew every seed key, so a key absent from its file now is the
    # owner's removal: a LEFT row naming that, nothing written, nothing
    # recorded, exit 5.
    _both_keys_host(project)
    mnt.mount(project, today=MOUNT_DAY)
    entry = record(project, ".mcp.json")
    assert entry["written"] is False and entry["entries"] == {}
    edited = _drop_write_key(project)
    before = mnt.read_manifest(project)

    plan = mnt.upgrade_plan(project)
    assert (verbs(plan)[WRITE_ROW], codes(plan)[WRITE_ROW]) == (
        mnt.LEFT, mnt.UPGRADE_ENTRY_HELD_REMOVED)
    assert codes(plan)[".mcp.json"] == mnt.UPGRADE_MCP_HOST_OWNED
    assert plan.writes == ()

    assert cli.main(["upgrade", str(project)]) == 5
    out = capsys.readouterr().out
    assert WRITE_ROW in out
    assert "removed" in out and "nothing recorded" in out
    assert "first to register" not in out
    assert (project / ".mcp.json").read_bytes() == edited
    assert mnt.read_manifest(project) == before


def test_an_unjudged_host_owned_record_gets_the_write_entry_once_then_honours_its_removal(
    project, capsys,
):
    # The accepted window (mount-01, Decided): a written: false record
    # with no `entries`, as v0.1.8 to v0.1.13 wrote it, proves nothing
    # about the write key whatever its stamp says. The first upgrade
    # adds it once and records it; a second removal is then the read
    # step's ABSENT row, nothing written.
    _both_keys_host(project)
    mnt.mount(project, today=MOUNT_DAY)
    body = mnt.read_manifest(project)
    for entry in body["files"]:
        if entry["path"] == ".mcp.json":
            entry.pop("entries", None)
            entry.pop("held", None)
    (project / mnt.MANIFEST_REL).write_bytes(mnt._dump_manifest(body))
    assert body["radialrig"] == mnt.installed_version()
    _drop_write_key(project)

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert (verbs(plan)[WRITE_ROW], codes(plan)[WRITE_ROW]) == (
        mnt.CREATED, mnt.UPGRADE_ENTRY_NEW_WRITTEN)
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"][mnt.WRITE_SERVER_KEY] == WRITE_ENTRY
    entry = record(project, ".mcp.json")
    assert entry["written"] is True
    assert entry["entries"] == {mnt.WRITE_SERVER_KEY: WRITE_ENTRY}

    edited = _drop_write_key(project)
    assert cli.main(["upgrade", str(project)]) == 5
    out = capsys.readouterr().out
    assert mnt.REASONS[mnt.UPGRADE_ENTRY_ABSENT] in out
    assert WRITE_ROW not in out
    assert (project / ".mcp.json").read_bytes() == edited
    assert record(project, ".mcp.json") == entry
