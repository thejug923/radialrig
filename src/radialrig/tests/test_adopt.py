"""Tests for the adopt flow — BUG-0001, the constraint 5 ruling of
2026-08-22 (PLAN-0008).

Run:  uv run --with pytest pytest src/radialrig/tests/test_adopt.py -q

The ruling's boundary is the oracle: a path is recorded as the mount's
own only when its bytes match what this build's seed would have
written, proven by hash; every other path lands on the careful side.
The gold test is the round trip — mount, lose the record, adopt,
unmount — which must end at the host's original tree.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from radialrig import adopt as ad
from radialrig import cli
from radialrig import mount as mnt
from radialrig import report
from radialrig.tests.test_cli import FORBIDDEN_WORD, block
from radialrig.tests.test_codex import bump_region
from radialrig.tests.test_connect import STORE_VAR
from radialrig.tests.test_mount import EXPECTED, MOUNT_DAY, _make_project, tree
from radialrig.tests.test_unmount import crlf

ADOPT_DAY = dt.date(2026, 8, 22)


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    """No test here may reach this machine's own client or roster state."""
    for name in ("APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path / "sample-project")


@pytest.fixture()
def orphaned(project) -> Path:
    """The bug's shape: a mounted project whose record is gone."""
    mnt.mount(project, today=MOUNT_DAY)
    (project / mnt.MANIFEST_REL).unlink()
    return project


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- the round trip ---------------------------------------------------

def test_adopt_then_unmount_restores_the_original_tree(project):
    before = tree(project)
    mnt.mount(project, today=MOUNT_DAY)
    (project / mnt.MANIFEST_REL).unlink()

    plan = ad.adopt(project, today=ADOPT_DAY)
    assert plan.kept == 0
    assert mnt.is_mounted(project)

    done = mnt.unmount(project)
    assert done.complete
    files_after, dirs_after = tree(project)
    assert files_after == before[0]
    # The seed's directories stay: adopt claimed no directory it did not
    # itself create, so unmount removes none of them. Cautious by design.
    assert set(before[1]) <= set(dirs_after)


def test_a_pristine_orphan_is_claimed_whole(orphaned):
    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    assert plan.claimed == len(mnt.SEED_FILES)
    assert plan.kept == 0
    codes = {d.code for d in plan.dispositions}
    assert codes == {
        mnt.ADOPT_MATCHED, mnt.ADOPT_SECTION_MATCHED,
        mnt.ADOPT_MCP_SEED_ENTRY, mnt.ADOPT_RECORD_WRITTEN,
    }


def test_adopt_writes_the_record_and_no_other_byte(orphaned):
    before = tree(orphaned)
    ad.adopt(orphaned, today=ADOPT_DAY)
    files_after, _ = tree(orphaned)
    added = set(files_after) - set(before[0])
    assert added == {mnt.MANIFEST_REL}
    unchanged = {rel: data for rel, data in files_after.items()
                 if rel != mnt.MANIFEST_REL}
    assert unchanged == before[0]


def test_the_record_recovers_the_original_mount_day(orphaned):
    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    record = json.loads((orphaned / mnt.MANIFEST_REL).read_text(encoding="utf-8"))
    assert plan.mounted == MOUNT_DAY.isoformat()
    assert record["mounted"] == MOUNT_DAY.isoformat()
    assert record["adopted"] == ADOPT_DAY.isoformat()
    assert record["schema"] == mnt.MANIFEST_SCHEMA


# --- the careful side -------------------------------------------------

def test_an_edited_file_is_recorded_as_the_projects_own(orphaned):
    spec = orphaned / "docs" / "spec.md"
    edited = spec.read_bytes() + b"\nOur own closing rule.\n"
    spec.write_bytes(edited)

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path["docs/spec.md"].code == mnt.ADOPT_EDITED
    assert plan.kept == 1

    done = mnt.unmount(orphaned)
    assert not done.complete
    assert spec.read_bytes() == edited


def test_an_edited_fence_is_recorded_as_the_projects_own(orphaned):
    agents = orphaned / mnt.AGENTS_REL
    data = agents.read_bytes().replace(b"## RadialRig route map",
                                       b"## Our route map")
    agents.write_bytes(data)

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.AGENTS_REL].code == mnt.ADOPT_SECTION_EDITED

    mnt.unmount(orphaned)
    assert agents.read_bytes() == data


def test_a_file_without_the_fence_is_not_recorded(orphaned):
    (orphaned / mnt.AGENTS_REL).write_bytes(b"# Ours alone\n")
    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.AGENTS_REL].code == mnt.ADOPT_NO_FENCE
    assert mnt.AGENTS_REL not in {f.path for f in plan.files}


def test_a_foreign_server_entry_stays_the_projects_own(orphaned):
    target = orphaned / mnt.MCP_REL
    loaded = json.loads(target.read_text(encoding="utf-8"))
    loaded["mcpServers"][mnt.SERVER_KEY] = {"command": "our-own", "args": []}
    target.write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")
    edited = target.read_bytes()

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.MCP_REL].code == mnt.ADOPT_MCP_PROJECT
    entry = next(f for f in plan.files if f.path == mnt.MCP_REL)
    assert entry.written is False

    mnt.unmount(orphaned)
    assert target.read_bytes() == edited


def test_an_orphan_from_before_the_write_server_is_claimed_with_its_one_key(
    orphaned,
):
    """PLAN-0012 phase 1: a project mounted by a release that registered
    the read server alone, and whose record is gone. Adopt recognizes
    that fresh file too, claims exactly the key it holds, and the next
    upgrade adds the other."""
    target = orphaned / mnt.MCP_REL
    target.write_bytes(mnt._fresh_mcp_bytes({mnt.SERVER_KEY: mnt._mcp_entry()}))

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.MCP_REL].code == mnt.ADOPT_MCP_SEED_ENTRY
    entry = next(f for f in plan.files if f.path == mnt.MCP_REL)
    assert entry.keys == ("mcpServers.radialrig",)
    assert entry.entries == {mnt.SERVER_KEY: mnt._mcp_entry()}

    done = mnt.upgrade(orphaned)
    added = next(i for i in done.items if mnt.WRITE_SERVER_KEY in i.path)
    assert added.code == mnt.UPGRADE_ENTRY_NEW_WRITTEN
    assert target.read_bytes() == mnt._fresh_mcp_bytes(mnt._mcp_entries())


def test_a_pristine_orphan_claims_both_keys(orphaned):
    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    entry = next(f for f in plan.files if f.path == mnt.MCP_REL)
    assert entry.keys == ("mcpServers.radialrig", "mcpServers.radialrig-write")
    assert entry.entries == mnt._mcp_entries()


def test_a_wrong_scope_lands_on_the_careful_side(orphaned):
    plan = ad.adopt_plan(orphaned, scope="another-name", today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path["docs/spec.md"].code == mnt.ADOPT_EDITED


def test_a_hosts_own_mcp_file_is_never_touched_again(project):
    # The review's boundary case: the host had its own .mcp.json before
    # the mount, so the spliced key sits in a file the hash cannot claim.
    # Adopt lands it host-owned, and unmount leaves every byte standing.
    target = project / mnt.MCP_REL
    target.write_bytes(
        b'{\n    "mcpServers": {\n        "ours": {"command": "x"}\n    }\n}\n'
    )
    mnt.mount(project, today=MOUNT_DAY)
    merged = target.read_bytes()
    (project / mnt.MANIFEST_REL).unlink()

    plan = ad.adopt(project, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.MCP_REL].code == mnt.ADOPT_MCP_UNPROVEN
    entry = next(f for f in plan.files if f.path == mnt.MCP_REL)
    assert entry.written is False

    mnt.unmount(project)
    assert target.read_bytes() == merged


def test_host_content_around_the_fence_survives_the_round_trip(project):
    agents = project / mnt.AGENTS_REL
    agents.write_bytes(b"# House\n\nOur rules.\n")
    original = agents.read_bytes()
    mnt.mount(project, today=MOUNT_DAY)
    (project / mnt.MANIFEST_REL).unlink()

    plan = ad.adopt(project, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.AGENTS_REL].code == mnt.ADOPT_SECTION_MATCHED

    mnt.unmount(project)
    assert agents.read_bytes() == original


# --- upgrade after adopt ----------------------------------------------

def test_upgrade_after_adopt_is_current_at_the_same_version(orphaned):
    ad.adopt(orphaned, today=ADOPT_DAY)
    plan = mnt.upgrade(orphaned)
    assert plan.pending == 0
    assert plan.applied == 0


def test_upgrade_after_adopt_leaves_an_edited_file(orphaned):
    spec = orphaned / "docs" / "spec.md"
    edited = spec.read_bytes() + b"\nOur own closing rule.\n"
    spec.write_bytes(edited)
    ad.adopt(orphaned, today=ADOPT_DAY)

    plan = mnt.upgrade(orphaned)
    by_path = {i.path: i for i in plan.items}
    assert by_path["docs/spec.md"].code == mnt.UPGRADE_PENDING
    assert spec.read_bytes() == edited


def test_upgrade_after_adopt_moves_a_matched_file_to_a_newer_seed(
    orphaned, monkeypatch,
):
    ad.adopt(orphaned, today=ADOPT_DAY)
    real = mnt._seed_bytes

    def newer(source):
        data = real(source)
        if source == "docs/spec.md":
            data += b"\nA newer rule.\n"
        return data

    monkeypatch.setattr(mnt, "_seed_bytes", newer)
    plan = mnt.upgrade(orphaned)
    by_path = {i.path: i for i in plan.items}
    assert by_path["docs/spec.md"].code == mnt.UPGRADE_APPLIED
    assert b"A newer rule." in (orphaned / "docs" / "spec.md").read_bytes()


# --- BUG-0011: a CRLF checkout is not an edit --------------------------
# The likely orphan is a clone, and a checkout with core.autocrlf=true
# rewrites every seeded file with CRLF and changes no word. Adopt reads
# a file as it stands or with CRLF read as LF, the rule every other verb
# takes (owner ruling 2026-09-05); a real edit still differs both ways.

def _flip_the_orphan(root: Path) -> None:
    """What a checkout does to every seeded file the lost record named."""
    crlf(root, EXPECTED - {mnt.MANIFEST_REL})


def test_a_crlf_orphan_is_claimed_whole(project, capsys):
    before = tree(project)
    mnt.mount(project, today=MOUNT_DAY)
    (project / mnt.MANIFEST_REL).unlink()
    _flip_the_orphan(project)
    for rel in ("docs/spec.md", mnt.AGENTS_REL, mnt.MCP_REL):
        assert b"\r\n" in (project / rel).read_bytes(), rel
    standing = tree(project)

    plan = ad.adopt(project, today=ADOPT_DAY)
    assert plan.claimed == len(mnt.SEED_FILES)
    assert plan.kept == 0
    codes = {d.code for d in plan.dispositions}
    assert codes == {
        mnt.ADOPT_MATCHED, mnt.ADOPT_SECTION_MATCHED,
        mnt.ADOPT_MCP_SEED_ENTRY, mnt.ADOPT_RECORD_WRITTEN,
    }
    # The date recovery reads through the CRLF: the record still carries
    # the original mount day, never the adopt day.
    assert plan.mounted == MOUNT_DAY.isoformat()
    # Adopt wrote the record and no other byte: the files stay CRLF.
    files_after, _ = tree(project)
    assert {rel: data for rel, data in files_after.items()
            if rel != mnt.MANIFEST_REL} == standing[0]

    # The rebuilt record reads the same way to upgrade at this version.
    upgrade = mnt.upgrade_plan(project)
    assert {i.verb for i in upgrade.items} == {mnt.CURRENT}, {
        i.path: i.code for i in upgrade.items}
    assert upgrade.pending == 0
    assert run(capsys, "upgrade", str(project), "--dry-run")[0] == 0
    assert tree(project)[0] == files_after

    # And to unmount: the tree comes back as it stood before the mount.
    done = mnt.unmount(project)
    assert done.complete
    files_after, dirs_after = tree(project)
    assert files_after == before[0]
    assert set(before[1]) <= set(dirs_after)


def test_a_crlf_orphan_records_lf_hashes(project):
    # Review 2026-09-05: the record keeps the LF digest, whatever the
    # checkout's endings, so a later checkout that puts LF back still
    # reads as unedited to unmount and upgrade.
    mnt.mount(project, today=MOUNT_DAY)
    (project / mnt.MANIFEST_REL).unlink()
    _flip_the_orphan(project)
    plan = ad.adopt(project, today=ADOPT_DAY)
    for rel in (mnt.AGENTS_REL, mnt.MCP_REL):
        raw = (project / rel).read_bytes()
        assert b"\r\n" in raw
        recorded = next(f for f in plan.files if f.path == rel)
        assert recorded.sha256 == mnt._sha256(mnt._lf(raw)), rel
        assert recorded.sha256 != mnt._sha256(raw), rel

    for rel in EXPECTED - {mnt.MANIFEST_REL}:
        path = project / rel
        path.write_bytes(mnt._lf(path.read_bytes()))
    upgrade = mnt.upgrade_plan(project)
    assert {i.verb for i in upgrade.items} == {mnt.CURRENT}, {
        i.path: i.code for i in upgrade.items}
    done = mnt.unmount(project)
    assert done.complete
    assert not (project / mnt.MCP_REL).exists()
    assert not (project / mnt.AGENTS_REL).exists()


def test_a_crlf_file_with_a_real_edit_still_adopts_as_edited(orphaned):
    spec = orphaned / "docs" / "spec.md"
    spec.write_bytes(spec.read_bytes() + b"\nOur own closing rule.\n")
    agents = orphaned / mnt.AGENTS_REL
    agents.write_bytes(agents.read_bytes().replace(b"## RadialRig route map",
                                                   b"## Our route map"))
    _flip_the_orphan(orphaned)
    edited = {rel: (orphaned / rel).read_bytes()
              for rel in ("docs/spec.md", mnt.AGENTS_REL)}

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path["docs/spec.md"].code == mnt.ADOPT_EDITED
    assert by_path[mnt.AGENTS_REL].code == mnt.ADOPT_SECTION_EDITED
    # Every other flipped file is unedited, and claimed.
    assert plan.kept == 2
    assert plan.claimed == len(mnt.SEED_FILES) - 2
    assert plan.mounted == MOUNT_DAY.isoformat()

    done = mnt.unmount(orphaned)
    assert not done.complete
    for rel, data in edited.items():
        assert (orphaned / rel).read_bytes() == data, rel


def test_a_crlf_mcp_file_with_a_changed_key_stays_the_projects_own(orphaned):
    target = orphaned / mnt.MCP_REL
    loaded = json.loads(target.read_text(encoding="utf-8"))
    loaded["mcpServers"][mnt.SERVER_KEY] = {"command": "our-own", "args": []}
    target.write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")
    _flip_the_orphan(orphaned)
    changed = target.read_bytes()

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.MCP_REL].code == mnt.ADOPT_MCP_PROJECT
    assert plan.kept == 1

    done = mnt.unmount(orphaned)
    assert not done.complete
    assert target.read_bytes() == changed


# --- more careful-side landings ---------------------------------------

def test_a_directory_at_a_seed_path_is_left(orphaned):
    spec = orphaned / "docs" / "spec.md"
    spec.unlink()
    spec.mkdir()
    plan = ad.adopt_plan(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path["docs/spec.md"].code == mnt.LEFT_NOT_A_FILE
    # With no readable spec, the date recovery falls back to the adopt day.
    assert plan.mounted == ADOPT_DAY.isoformat()


# --- shapes no mount writes (the second review's findings) ------------

def test_a_one_newline_prefix_is_not_claimed(orphaned):
    # The mount created AGENTS.md whole here, so the file is exactly the
    # section; one newline between host bytes and the fence is a shape
    # no mount leaves. Claiming it would let unmount delete that
    # host-authored newline (constraint 5).
    agents = orphaned / mnt.AGENTS_REL
    section = agents.read_bytes()
    shaped = b"# Mine\n" + section
    agents.write_bytes(shaped)

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.AGENTS_REL].code == mnt.ADOPT_SECTION_UNPROVEN
    assert mnt.AGENTS_REL not in {f.path for f in plan.files}

    mnt.unmount(orphaned)
    assert agents.read_bytes() == shaped


def test_a_bare_prefix_with_a_tail_is_not_claimed(orphaned):
    # The review's repro: b"A\n" + section + b"B\n" adopted as matched,
    # and unmount then merged the two host lines to b"AB\n".
    agents = orphaned / mnt.AGENTS_REL
    section = agents.read_bytes()
    shaped = b"A\n" + section + b"B\n"
    agents.write_bytes(shaped)

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.AGENTS_REL].code == mnt.ADOPT_SECTION_UNPROVEN
    assert mnt.AGENTS_REL not in {f.path for f in plan.files}

    mnt.unmount(orphaned)
    assert agents.read_bytes() == shaped


def test_a_missing_trailing_newline_keeps_the_mounts_newline(project):
    # BUG-0001's third bound: a pre-mount AGENTS.md that ended without a
    # newline keeps the one newline the mount added, after the adopt
    # round trip. The separator the mount wrote is still removed.
    agents = project / mnt.AGENTS_REL
    original = b"# House\nNo trailing newline"
    agents.write_bytes(original)
    mnt.mount(project, today=MOUNT_DAY)
    (project / mnt.MANIFEST_REL).unlink()

    plan = ad.adopt(project, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.AGENTS_REL].code == mnt.ADOPT_SECTION_MATCHED

    mnt.unmount(project)
    assert agents.read_bytes() == original + b"\n"


def test_a_re_dated_spec_is_not_claimed(orphaned):
    # The self-reference finding: the recovered day comes out of
    # docs/spec.md itself, so a spec whose two date fields were re-dated
    # to one valid day matches a seed rendered with its own edit. The
    # claim needs the second dated file to prove the day; without that,
    # the file stays this project's own and unmount leaves it.
    spec = orphaned / "docs" / "spec.md"
    re_dated = spec.read_bytes().replace(
        MOUNT_DAY.isoformat().encode("ascii"), b"2026-03-03")
    spec.write_bytes(re_dated)

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path["docs/spec.md"].code == mnt.ADOPT_DATE_UNPROVEN
    assert "docs/spec.md" not in {f.path for f in plan.files}
    # The other dated file still holds the true day, so under the
    # re-dated render it reads as edited - the contradiction that keeps
    # spec.md's own match from standing as proof.
    assert by_path["docs/spec-git-environment.md"].code == mnt.ADOPT_EDITED

    mnt.unmount(orphaned)
    assert spec.read_bytes() == re_dated


def test_a_hosts_own_empty_gitkeep_alone_is_not_claimed(project):
    # An empty file matching an empty seed proves nothing. Mount refuses
    # on this exact file; adopt must not claim it, write a record over
    # it, and give unmount a warrant to delete it.
    keep = project / "docs" / "plans" / ".gitkeep"
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"")

    plan = ad.adopt(project, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path["docs/plans/.gitkeep"].code == mnt.ADOPT_EMPTY_UNPROVEN
    assert plan.files == ()
    assert not mnt.is_mounted(project)
    assert keep.read_bytes() == b""


def test_gitkeeps_stay_claimed_beside_a_contentful_claim(orphaned):
    # The demotion is for empty-for-empty claims standing alone; a
    # genuine orphan's other matches carry the evidence.
    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path["docs/plans/.gitkeep"].code == mnt.ADOPT_MATCHED


def test_broken_fences_record_nothing(orphaned):
    agents = orphaned / mnt.AGENTS_REL
    shaped = f"# X\n\n{mnt.FENCE_BEGIN}\nstuff, no closing fence\n".encode("utf-8")
    agents.write_bytes(shaped)

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.AGENTS_REL].code == mnt.ADOPT_FENCES_BROKEN
    assert mnt.AGENTS_REL not in {f.path for f in plan.files}

    mnt.unmount(orphaned)
    assert agents.read_bytes() == shaped


def test_an_mcp_file_without_the_key_records_nothing(orphaned):
    target = orphaned / mnt.MCP_REL
    shaped = b'{\n  "mcpServers": {\n    "other": {"command": "x"}\n  }\n}\n'
    target.write_bytes(shaped)

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.MCP_REL].code == mnt.ADOPT_MCP_NO_KEY
    assert mnt.MCP_REL not in {f.path for f in plan.files}

    mnt.unmount(orphaned)
    assert target.read_bytes() == shaped


@pytest.mark.parametrize("shaped", [
    b"not json\n",
    b"[]\n",
    b"\xff\xfe{}",  # not UTF-8; the decode failure lands the same way
])
def test_an_unparsed_mcp_file_records_nothing(orphaned, shaped):
    target = orphaned / mnt.MCP_REL
    target.write_bytes(shaped)

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.MCP_REL].code == mnt.ADOPT_MCP_UNPARSED
    assert mnt.MCP_REL not in {f.path for f in plan.files}

    mnt.unmount(orphaned)
    assert target.read_bytes() == shaped


# --- refusals and no-ops ----------------------------------------------

def test_adopt_refuses_a_mounted_project(project):
    mnt.mount(project, today=MOUNT_DAY)
    with pytest.raises(mnt.AlreadyMounted):
        ad.adopt(project, today=ADOPT_DAY)


def test_a_bare_project_gets_no_record(project):
    before = tree(project)
    plan = ad.adopt(project, today=ADOPT_DAY)
    assert plan.files == ()
    assert tree(project) == before
    assert not mnt.is_mounted(project)


# --- BUG-0002: a record needs evidence of a mount ---------------------
# Owner ruling 2026-09-05. A .mcp.json key adopt cannot tie to a mount
# is a row that needs no proof, and before the fix that row alone wrote
# a record with zero claimed paths over a tree no mount ever touched;
# mount then refused with "Already mounted". The gate: a record is
# written only when at least one row comes from a seeded doc file or a
# hash-proven claim.

def _self_registered(project: Path, entry: dict) -> bytes:
    """A project that typed the radialrig key into its own .mcp.json and
    never ran mount: the only row adopt can find here."""
    target = project / mnt.MCP_REL
    body = {"mcpServers": {"ours": {"command": "x"}, mnt.SERVER_KEY: entry}}
    target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return target.read_bytes()


@pytest.mark.parametrize("entry", [
    {"command": "our-own", "args": []},   # a key of the project's own shape
    mnt._mcp_entry(),                      # the seed's entry, in a host file
])
def test_a_never_mounted_project_with_its_own_key_gets_no_record(
    project, entry,
):
    written = _self_registered(project, entry)
    before = tree(project)

    plan = ad.adopt(project, today=ADOPT_DAY)
    assert plan.files == ()
    assert plan.dirs == ()
    assert plan.claimed == 0
    assert mnt.ADOPT_RECORD_WRITTEN not in {d.code for d in plan.dispositions}
    assert tree(project) == before
    assert not mnt.is_mounted(project)
    assert (project / mnt.MCP_REL).read_bytes() == written

    # The stuck state is gone: mount works on this tree, and the key the
    # project registered itself stays its own.
    mnt.mount(project, today=MOUNT_DAY)
    assert mnt.is_mounted(project)
    assert json.loads((project / mnt.MCP_REL).read_text(encoding="utf-8"))[
        "mcpServers"][mnt.SERVER_KEY] == entry


def test_cli_adopt_on_a_never_mounted_project_with_its_own_key_is_a_noop(
    project, capsys,
):
    # The same answer, exit code and sentence a bare tree gets, on the
    # dry run and the real run.
    _self_registered(project, {"command": "our-own", "args": []})
    before = tree(project)
    for extra in (("--dry-run",), ()):
        code, out, err = run(capsys, "adopt", str(project), *extra)
        assert code == 4
        assert err == ""
        assert f"Nothing to adopt: {project}" in out
        assert report.NOTHING_TO_ADOPT in out
        assert "Rebuilt" not in out and "Would rebuild" not in out
    assert tree(project) == before
    assert not mnt.is_mounted(project)


def _edit_every_seeded_file(orphaned: Path) -> None:
    """A lost mount whose every standing file was changed afterwards."""
    for entry in mnt.SEED_FILES:
        path = orphaned / entry.rel
        if entry.mode == mnt.CREATED:
            path.write_bytes(path.read_bytes() + b"ours\n")
    agents = orphaned / mnt.AGENTS_REL
    agents.write_bytes(agents.read_bytes().replace(
        b"## RadialRig route map", b"## Our route map"))
    bump_region(orphaned / mnt.CODEX_REL)
    claude = orphaned / mnt.CLAUDE_REL
    claude.write_bytes(claude.read_bytes().replace(b"edit outside", b"leave", 1))
    target = orphaned / mnt.MCP_REL
    loaded = json.loads(target.read_text(encoding="utf-8"))
    loaded["mcpServers"][mnt.SERVER_KEY] = {"command": "our-own", "args": []}
    target.write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")


def test_a_lost_mount_with_only_an_edited_section_left_keeps_its_record(
    orphaned,
):
    # Review 2026-09-05: docs/ gone, one word changed inside the fences,
    # a .mcp.json key the project owns. The fences are a mount's mark
    # as much as docs/**, and mount refuses to write a second pair, so
    # "nothing to adopt, run mount" was a dead end. The section counts
    # as evidence, edited or not.
    shutil.rmtree(orphaned / "docs")
    agents = orphaned / mnt.AGENTS_REL
    agents.write_bytes(agents.read_bytes().replace(
        b"## RadialRig route map", b"## Our route map"))
    bump_region(orphaned / mnt.CODEX_REL)
    claude = orphaned / mnt.CLAUDE_REL
    claude.write_bytes(claude.read_bytes().replace(b"edit outside", b"leave", 1))
    target = orphaned / mnt.MCP_REL
    loaded = json.loads(target.read_text(encoding="utf-8"))
    loaded["mcpServers"][mnt.SERVER_KEY] = {"command": "our-own", "args": []}
    target.write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")
    before = tree(orphaned)

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    assert plan.claimed == 0
    by_path = {d.path: d for d in plan.dispositions}
    assert by_path[mnt.AGENTS_REL].code == mnt.ADOPT_SECTION_EDITED
    assert by_path[mnt.CODEX_REL].code == mnt.ADOPT_SECTION_EDITED
    assert by_path[mnt.MCP_REL].code == mnt.ADOPT_MCP_PROJECT
    assert by_path[mnt.MANIFEST_REL].code == mnt.ADOPT_RECORD_WRITTEN
    assert {mnt.AGENTS_REL, mnt.MCP_REL} <= {f.path for f in plan.files}
    assert mnt.is_mounted(orphaned)

    # The record says both files are this project's own: unmount leaves
    # them and takes the record away.
    done = mnt.unmount(orphaned)
    assert not done.complete
    assert not mnt.is_mounted(orphaned)
    assert tree(orphaned)[0] == before[0]


def test_a_lost_mount_with_every_seeded_file_edited_keeps_its_record(
    orphaned,
):
    _edit_every_seeded_file(orphaned)
    before = tree(orphaned)

    plan = ad.adopt(orphaned, today=ADOPT_DAY)
    assert plan.claimed == 0
    assert plan.files != ()
    assert mnt.is_mounted(orphaned)
    by_path = {d.path: d for d in plan.dispositions}
    for entry in mnt.SEED_FILES:
        if entry.mode == mnt.CREATED:
            assert by_path[entry.rel].code == mnt.ADOPT_EDITED, entry.rel
    assert by_path[mnt.AGENTS_REL].code == mnt.ADOPT_SECTION_EDITED
    assert by_path[mnt.CODEX_REL].code == mnt.ADOPT_SECTION_EDITED
    assert by_path[mnt.MCP_REL].code == mnt.ADOPT_MCP_PROJECT
    assert by_path[mnt.MANIFEST_REL].code == mnt.ADOPT_RECORD_WRITTEN
    recorded = {f.path for f in plan.files}
    assert {e.rel for e in mnt.SEED_FILES} <= recorded

    # The record says every path is this project's own, so unmount
    # removes the record and no other byte.
    done = mnt.unmount(orphaned)
    assert not done.complete
    assert not mnt.is_mounted(orphaned)
    assert tree(orphaned)[0] == before[0]


def test_cli_adopt_on_an_everything_edited_lost_mount_reports_kept_paths(
    orphaned, capsys,
):
    _edit_every_seeded_file(orphaned)
    code, out, err = run(capsys, "adopt", str(orphaned))
    assert code == 5
    assert err == ""
    assert "Rebuilt the mount record for" in out
    assert report.ADOPT_KEPT_TAIL in out
    assert mnt.is_mounted(orphaned)


def test_adopt_plan_writes_nothing(orphaned):
    before = tree(orphaned)
    ad.adopt_plan(orphaned, today=ADOPT_DAY)
    assert tree(orphaned) == before


def test_an_adopted_record_states_the_degraded_reversal(orphaned, capsys):
    # An adopted record carries no restore fields, the same reversal
    # bound a schema-1 record has, so the same note states it.
    ad.adopt(orphaned, today=ADOPT_DAY)
    plan = mnt.unmount_plan(orphaned)
    assert plan.degraded is True
    assert cli.main(["unmount", str(orphaned)]) in (0, 5)
    assert mnt.DEGRADED_NOTE in capsys.readouterr().out


# --- the command line -------------------------------------------------

def test_cli_adopt_claims_a_pristine_orphan(orphaned, capsys):
    code, out, err = run(capsys, "adopt", str(orphaned))
    assert code == 0
    assert err == ""
    assert "Rebuilt the mount record for" in out
    assert (f"scope: {orphaned.name}    owner: {mnt.DEFAULT_OWNER}    "
            f"mounted: {MOUNT_DAY.isoformat()}") in out
    assert report.ADOPT_CLEAN_TAIL in out
    assert mnt.is_mounted(orphaned)


def test_cli_adopt_reports_the_kept_paths(orphaned, capsys):
    spec = orphaned / "docs" / "spec.md"
    spec.write_bytes(spec.read_bytes() + b"\nOurs.\n")
    code, out, err = run(capsys, "adopt", str(orphaned))
    assert code == 5
    assert report.ADOPT_KEPT_TAIL in out
    assert mnt.REASONS[mnt.ADOPT_EDITED] in out


def test_cli_adopt_on_a_mounted_project_is_a_noop(project, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    code, out, err = run(capsys, "adopt", str(project))
    assert code == 4
    assert "Already mounted" in out


def test_cli_adopt_on_a_bare_project_is_a_noop(project, capsys):
    code, out, err = run(capsys, "adopt", str(project))
    assert code == 4
    assert f"Nothing to adopt: {project}" in out
    assert report.NOTHING_TO_ADOPT in out
    assert not mnt.is_mounted(project)


def test_cli_adopt_dry_run_writes_nothing(orphaned, capsys):
    before = tree(orphaned)
    code, out, err = run(capsys, "adopt", str(orphaned), "--dry-run")
    assert code == 0
    assert "Would rebuild the mount record for" in out
    assert report.DRY_RUN_TAIL in out
    assert tree(orphaned) == before


def test_cli_adopt_dry_run_reports_the_kept_exit(orphaned, capsys):
    # The preview's exit code must match the real run's: a kept path is
    # exit 5 in both.
    spec = orphaned / "docs" / "spec.md"
    spec.write_bytes(spec.read_bytes() + b"\nOurs.\n")
    before = tree(orphaned)
    code, out, err = run(capsys, "adopt", str(orphaned), "--dry-run")
    assert code == 5
    assert report.ADOPT_KEPT_TAIL in out
    assert report.DRY_RUN_TAIL in out
    assert tree(orphaned) == before


def test_cli_adopt_registers_the_project_in_the_roster(
    orphaned, tmp_path, monkeypatch, capsys,
):
    roaming = tmp_path / "roaming"
    monkeypatch.setenv(STORE_VAR, str(roaming))
    roster = roaming / "radialrig" / "roster.json"

    code, _, _ = run(capsys, "adopt", str(orphaned), "--dry-run")
    assert code == 0
    assert not roster.exists()

    code, _, _ = run(capsys, "adopt", str(orphaned))
    assert code == 0
    listed = json.loads(roster.read_text(encoding="utf-8"))
    assert str(orphaned) in {p["root"] for p in listed["projects"]}


@pytest.mark.parametrize("record", [
    b"{not json\n",
    b"\xff\xfe{}",  # UTF-16-shaped bytes: JSON is UTF-8, same refusal
])
def test_cli_adopt_refuses_an_unreadable_record_in_one_line(
    project, record, capsys,
):
    # ADOPT_EPILOG's contract: exit 1, one refusal line, no traceback.
    mnt.mount(project, today=MOUNT_DAY)
    manifest = project / mnt.MANIFEST_REL
    manifest.write_bytes(record)
    code, out, err = run(capsys, "adopt", str(project))
    assert code == cli.ERROR
    assert err.startswith("radialrig: ")
    assert "does not parse as JSON" in err
    assert manifest.read_bytes() == record


def test_cli_adopt_refuses_a_file_at_the_reserved_name(project, capsys):
    # A regular file named .radialrig blocks the record's directory. The
    # refusal is the same sentence on the dry run and the real run, and
    # neither touches the file.
    reserved = project / ".radialrig"
    reserved.write_bytes(b"the project's own bytes\n")
    sentence = (
        ".radialrig stands here as a file, and the rebuilt record "
        "needs that name for its directory; nothing was written"
    )
    for extra in ((), ("--dry-run",)):
        code, out, err = run(capsys, "adopt", str(project), *extra)
        assert code == cli.ERROR
        assert err == f"radialrig: {sentence}\n"
    assert reserved.read_bytes() == b"the project's own bytes\n"


def test_adopt_output_uses_the_closed_vocabulary(project, capsys):
    # The two output sweeps test_cli runs over every other verb: no
    # authorship vocabulary (D-33, whole words), and every report row
    # starts with a closed verb.
    outputs = []
    outputs.append(run(capsys, "adopt", str(project))[1])           # nothing to adopt
    mnt.mount(project, today=MOUNT_DAY)
    outputs.append(run(capsys, "adopt", str(project))[1])           # already mounted
    (project / mnt.MANIFEST_REL).unlink()
    (project / mnt.AGENTS_REL).write_bytes(b"# Ours alone\n")
    spec = project / "docs" / "spec.md"
    spec.write_bytes(spec.read_bytes() + b"\nOurs.\n")
    outputs.append(run(capsys, "adopt", str(project), "--dry-run")[1])
    outputs.append(run(capsys, "adopt", str(project))[1])           # kept paths
    for out in outputs:
        assert not FORBIDDEN_WORD.search(out.lower()), out
        for line in block(out):
            assert line.split()[0] in mnt.VERBS, line


# --- vocabulary -------------------------------------------------------

def test_every_adopt_code_has_a_wording():
    assert mnt.ADOPT_CODES
    for code in mnt.ADOPT_CODES:
        assert code in mnt.REASONS, code


def test_the_pinned_adopt_sentences():
    # The output pins for this kit's fixed sentences: a copy change must
    # change this test in the same commit.
    assert report.ADOPT_CLEAN_TAIL == (
        "The mount record is rebuilt. Every path it claims matches the "
        "installed seed, and unmount and upgrade work here again."
    )
    assert report.ADOPT_KEPT_TAIL == (
        "The mount record is rebuilt. A path recorded as, or left as, this "
        "project's own is never removed by unmount and never rewritten by "
        "upgrade."
    )
    assert report.NOTHING_TO_ADOPT == (
        'No seeded content stands here. Run "radialrig mount" to lay the seed.'
    )
    assert mnt.REASONS[mnt.ADOPT_MATCHED] == (
        "matches the installed seed; recorded as the mount's own"
    )
    assert mnt.REASONS[mnt.ADOPT_EDITED] == (
        "differs from the installed seed; recorded as this project's own"
    )
    assert mnt.REASONS[mnt.ADOPT_MCP_UNPROVEN] == (
        "the registered entry is the seed's, in a file with other content "
        "the hash cannot prove; recorded as this project's own"
    )
    assert mnt.REASONS[mnt.ADOPT_SECTION_UNPROVEN] == (
        "the fenced section matches the installed seed, in a shape no "
        "mount writes; nothing recorded for this file"
    )
    assert mnt.REASONS[mnt.ADOPT_DATE_UNPROVEN] == (
        "matches a seed dated by its own effective line, and no second "
        "dated file proves that day; nothing recorded for this file"
    )
    assert mnt.REASONS[mnt.ADOPT_EMPTY_UNPROVEN] == (
        "matches the installed seed only because both are empty, and no "
        "other path proves a mount; nothing recorded for this file"
    )
