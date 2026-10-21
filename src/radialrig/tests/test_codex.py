"""PLAN-0013: a second harness. Codex reads the same servers and skills.

Phase 1 pins `.codex/config.toml`: a mount writes one fenced region of
TOML holding the two server tables, refuses a file it cannot append to,
reverses the region on unmount, moves it on upgrade, adds it to a record
from before the plan, and claims it on adopt. Phase 2 pins the skills
kit's second harness row: one `skill add` writes both trees, one `skill
remove` takes both out, `--harness` narrows either, and no packaged skill
byte names a harness directory. Phase 3 pins `CLAUDE.md`, and the
2026-09-06 review pins follow: a linked CLAUDE.md, the import spellings
Claude Code follows, the survey and the mount agreeing, the upgrade's
result check, the write-server opt-out, one write for every harness.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from radialrig import adopt as ad
from radialrig import cli
from radialrig import mount as mnt
from radialrig import report
from radialrig import setup_web
from radialrig import skills as sk
from radialrig.tests.test_mount import MOUNT_DAY, _make_project, tree
from radialrig.tests.test_skills import INSTALL_DAY, SKILL
from radialrig.tests.test_unmount import crlf, marks
from radialrig.tests.test_upgrade import (  # noqa: F401 - next_release is a fixture
    UPGRADE_DAY,
    codes,
    forget,
    next_release,
    record,
    verbs,
)

CODEX = ".codex/config.toml"
OURS = b'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "other-server"\n'
WRITE_TABLE = f"[mcp_servers.{mnt.WRITE_SERVER_KEY}]".encode("utf-8")


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path / "sample-project")


@pytest.fixture()
def codex_host(project) -> Path:
    """A project that already carries a `.codex/config.toml` of its own."""
    (project / ".codex").mkdir()
    (project / CODEX).write_bytes(OURS)
    return project


def load(root: Path) -> dict:
    return mnt._load_toml((root / CODEX).read_bytes())


def region(root: Path) -> bytes:
    data = (root / CODEX).read_bytes()
    start, end = mnt._locate_section(data, CODEX)
    return data[start:end]


def bump_region(path: Path) -> None:
    """One byte's worth of edit inside the seeded region: the first
    server's empty `args` gains a flag."""
    path.write_bytes(path.read_bytes().replace(b"args = []", b'args = ["-v"]', 1))


def drop_write_table(path: Path) -> None:
    """The documented opt-out: the write server's table deleted from the
    region, blank line and all, and nothing else touched."""
    data = path.read_bytes()
    start = data.index(WRITE_TABLE)
    end = data.index(mnt.CODEX_FENCE_END.encode("utf-8"), start)
    path.write_bytes(data[:start].rstrip(b"\n") + b"\n" + data[end:])


def add_a_comment(seed_root: Path) -> None:
    """The newer release's one edit to the Codex seed: a comment at the
    head, above the tables, where an opt-out leaves the region alone."""
    source = seed_root / mnt.CODEX_SECTION_SOURCE
    source.write_bytes(b"# a comment the newer release added\n" + source.read_bytes())


# --- phase 1: the mount -------------------------------------------------

def test_a_bare_host_gets_the_codex_file_whole(project):
    result = mnt.mount(project, today=MOUNT_DAY)
    servers = load(project)["mcp_servers"]
    assert servers == mnt._mcp_entries()
    data = (project / CODEX).read_bytes()
    assert data.startswith(mnt.CODEX_FENCE_BEGIN.encode("utf-8") + b"\n")
    assert data.endswith(mnt.CODEX_FENCE_END.encode("utf-8") + b"\n")
    assert b"\r\n" not in data
    entry = next(f for f in result.files if f.path == CODEX)
    assert entry.mode == mnt.APPENDED
    assert entry.file_created is True
    assert entry.section_sha256 == mnt._sha256(data)


def test_the_two_tables_match_the_mcp_json_entries(project):
    mnt.mount(project, today=MOUNT_DAY)
    servers = load(project)["mcp_servers"]
    mcp = json.loads((project / mnt.MCP_REL).read_text(encoding="utf-8"))["mcpServers"]
    assert servers == mcp


def test_a_host_file_gains_one_region_and_unmount_gives_it_back(codex_host):
    project = codex_host
    before = tree(project)
    result = mnt.mount(project, today=MOUNT_DAY)
    data = (project / CODEX).read_bytes()
    assert data.startswith(OURS + b"\n" + mnt.CODEX_FENCE_BEGIN.encode("utf-8"))
    servers = load(project)["mcp_servers"]
    assert servers["other"] == {"command": "other-server"}
    assert servers[mnt.SERVER_KEY] == mnt._mcp_entry()
    assert servers[mnt.WRITE_SERVER_KEY] == mnt._write_mcp_entry()
    entry = next(f for f in result.files if f.path == CODEX)
    assert entry.file_created is False
    assert entry.pre_mount_sha256 == mnt._sha256(OURS)

    done = mnt.unmount(project)
    assert marks(done)[CODEX] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert done.complete
    assert tree(project) == before


def test_a_host_file_without_a_final_newline_still_round_trips(project):
    (project / ".codex").mkdir()
    (project / CODEX).write_bytes(OURS.rstrip(b"\n"))
    before = tree(project)
    result = mnt.mount(project, today=MOUNT_DAY)
    entry = next(f for f in result.files if f.path == CODEX)
    assert entry.newline_added is True
    assert load(project)["mcp_servers"][mnt.SERVER_KEY] == mnt._mcp_entry()
    mnt.unmount(project)
    assert tree(project) == before


def test_a_bare_mount_unmounts_to_nothing(project):
    before = tree(project)
    mnt.mount(project, today=MOUNT_DAY)
    done = mnt.unmount(project)
    assert marks(done)[CODEX] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert marks(done)[".codex/"] == (mnt.REMOVED, mnt.REMOVED_DIR)
    assert tree(project) == before


@pytest.mark.parametrize("standing", [
    b'[mcp_servers.radialrig]\ncommand = "mine"\n',
    b'[mcp_servers]\nradialrig-write = { command = "mine" }\n',
    b'this is = not toml = at all\n',
    b'# radialrig:begin\n# radialrig:end\n',
], ids=["read-table-taken", "write-table-taken", "not-toml", "fenced"])
def test_a_file_the_mount_cannot_append_to_refuses_the_whole_mount(project, standing):
    (project / ".codex").mkdir()
    (project / CODEX).write_bytes(standing)
    before = tree(project)
    with pytest.raises(mnt.MountConflict) as caught:
        mnt.mount(project, today=MOUNT_DAY)
    assert caught.value.conflicts == (CODEX,)
    assert tree(project) == before
    assert CODEX in mnt.survey(project).conflicts


def test_a_host_file_with_other_servers_is_not_a_conflict(codex_host):
    survey = mnt.survey(codex_host)
    assert CODEX in survey.merges
    assert CODEX not in survey.conflicts


def test_an_edited_region_is_left_on_unmount(project):
    mnt.mount(project, today=MOUNT_DAY)
    path = project / CODEX
    bump_region(path)
    done = mnt.unmount(project)
    assert marks(done)[CODEX] == (mnt.LEFT, mnt.LEFT_SECTION_EDITED)
    assert path.is_file()


def test_a_crlf_flip_of_the_codex_file_still_restores(codex_host):
    project = codex_host
    mnt.mount(project, today=MOUNT_DAY)
    crlf(project, [CODEX])
    done = mnt.unmount(project)
    assert marks(done)[CODEX] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / CODEX).read_bytes() == OURS


# --- phase 1: upgrade and adopt -------------------------------------------

def test_a_record_from_before_the_plan_gains_the_file_on_upgrade(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    forget(project, CODEX)
    (project / CODEX).unlink()
    (project / ".codex").rmdir()
    next_release()

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)[CODEX] == mnt.CREATED
    assert codes(plan)[CODEX] == mnt.UPGRADE_NEW_WRITTEN
    assert load(project)["mcp_servers"] == mnt._mcp_entries()
    entry = record(project, CODEX)
    assert entry["mode"] == mnt.APPENDED
    assert entry["file_created"] is True
    assert ".codex" in mnt.read_manifest(project)["dirs"]

    done = mnt.unmount(project)
    assert marks(done)[CODEX] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert not (project / ".codex").exists()


def test_a_record_from_before_the_plan_appends_onto_the_projects_file(
    project, next_release,
):
    mnt.mount(project, today=MOUNT_DAY)
    forget(project, CODEX)
    (project / CODEX).write_bytes(OURS)
    next_release()

    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)[CODEX] == mnt.APPENDED
    assert codes(plan)[CODEX] == mnt.UPGRADE_NEW_WRITTEN
    assert plan.applied >= 1
    assert (project / CODEX).read_bytes().startswith(OURS + b"\n")
    assert load(project)["mcp_servers"]["other"] == {"command": "other-server"}
    entry = record(project, CODEX)
    assert entry["pre_mount_sha256"] == mnt._sha256(OURS)

    done = mnt.unmount(project)
    assert marks(done)[CODEX] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / CODEX).read_bytes() == OURS


def test_a_projects_own_registration_is_left_on_upgrade(project, next_release, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    forget(project, CODEX)
    ours = b'[mcp_servers.radialrig]\ncommand = "mine"\n'
    (project / CODEX).write_bytes(ours)
    next_release()

    assert cli.main(["upgrade", str(project)]) == 5
    out = capsys.readouterr().out
    assert "cannot take it" in out
    assert (project / CODEX).read_bytes() == ours
    with pytest.raises(StopIteration):
        record(project, CODEX)


def test_an_edited_region_is_left_with_a_diff_on_upgrade(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    bump_region(project / CODEX)
    next_release(add_a_comment)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)[CODEX] == mnt.LEFT
    assert codes(plan)[CODEX] == mnt.UPGRADE_SECTION_PENDING
    assert next(i for i in plan.items if i.path == CODEX).diff


def test_an_unedited_region_moves_to_the_installed_seed(codex_host, next_release):
    project = codex_host
    mnt.mount(project, today=MOUNT_DAY)
    next_release(add_a_comment)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)[CODEX] == mnt.UPDATED
    assert b"a comment the newer release added" in region(project)
    assert (project / CODEX).read_bytes().startswith(OURS + b"\n")
    done = mnt.unmount(project)
    assert marks(done)[CODEX] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)


def test_adopt_claims_a_pristine_codex_file_and_keeps_an_edited_one(project):
    mnt.mount(project, today=MOUNT_DAY)
    (project / mnt.MANIFEST_REL).unlink()
    plan = ad.adopt_plan(project)
    row = next(d for d in plan.dispositions if d.path == CODEX)
    assert (row.verb, row.code) == (mnt.CURRENT, mnt.ADOPT_SECTION_MATCHED)
    entry = next(f for f in plan.files if f.path == CODEX)
    assert entry.mode == mnt.APPENDED and entry.file_created is True

    bump_region(project / CODEX)
    plan = ad.adopt_plan(project)
    row = next(d for d in plan.dispositions if d.path == CODEX)
    assert (row.verb, row.code) == (mnt.LEFT, mnt.ADOPT_SECTION_EDITED)


def test_adopt_then_unmount_takes_the_codex_region_back_out(codex_host):
    project = codex_host
    mnt.mount(project, today=MOUNT_DAY)
    (project / mnt.MANIFEST_REL).unlink()
    ad.adopt(project)
    done = mnt.unmount(project)
    assert marks(done)[CODEX] == (mnt.EXCISED, mnt.EXCISED_SECTION)
    assert (project / CODEX).read_bytes() == OURS


def test_the_seeded_route_map_names_both_servers_and_no_config_file(project):
    # The section names both servers by their registered keys. Which
    # file registers each is what `.mcp.json` and `.codex/config.toml`
    # themselves show, so the section no longer restates it (the
    # 2026-09-27 documentation review, A-08).
    mnt.mount(project, today=MOUNT_DAY)
    text = (project / mnt.AGENTS_REL).read_text(encoding="utf-8")
    assert "registered as `radialrig`" in text
    assert f"registered as `{mnt.WRITE_SERVER_KEY}`" in text
    assert "`.mcp.json`" not in text
    assert "`.codex/config.toml`" not in text


# --- phase 2: the skills kit's second row ----------------------------------

def test_the_map_carries_codex_beside_claude_code():
    assert set(sk.HARNESSES) == {"claude-code", "codex"}
    codex = sk.harness_for("codex")
    assert codex.skills == ".agents/skills"
    assert codex.record == ".agents/radialrig-skills.json"
    with pytest.raises(sk.UnknownHarness):
        sk.harness_for("cursor")


def test_one_add_writes_every_harness_and_one_remove_takes_them_out(project):
    before = tree(project)
    results = sk.add_all(project, SKILL, today=INSTALL_DAY)
    assert [r.harness.name for r in results] == list(sk.HARNESSES)
    for harness in sk.HARNESSES.values():
        assert (project / harness.skills / SKILL / "SKILL.md").is_file()
        body = json.loads((project / harness.record).read_text(encoding="utf-8"))
        assert body["harness"] == harness.name
        assert [e["dir"] for e in body["skills"]] == [f"{harness.skills}/{SKILL}"]
    plans = sk.remove_all(project, SKILL)
    assert all(p.installed and p.complete for p in plans)
    assert tree(project) == before


def test_a_refusal_in_one_tree_writes_nothing_anywhere(project):
    foreign = project / ".agents" / "skills" / SKILL
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_bytes(b"ours\n")
    before = tree(project)
    with pytest.raises(sk.SkillConflict):
        sk.add_all(project, SKILL, today=INSTALL_DAY)
    assert tree(project) == before
    assert not (project / ".claude").exists()


def test_a_skill_one_harness_has_is_one_state_per_harness_and_no_fold(project):
    """A skill installed for one harness reads current for that harness
    and absent for the other, one state each in map order; the fold
    that showed one state per skill across harnesses (`catalog_states`)
    went with the setup page's groups (PLAN-0015 phase 3)."""
    assert not hasattr(sk, "catalog_states")
    sk.add(project, SKILL, harness=sk.harness_for("claude-code"), today=INSTALL_DAY)
    shown = {(s.harness.name, s.name): s for s in sk.states_all(project)}
    assert [h for h, name in shown if name == SKILL] == list(sk.HARNESSES)
    assert shown[("claude-code", SKILL)].current
    assert not shown[("codex", SKILL)].current
    assert shown[("codex", SKILL)].record_entry is None
    sk.add(project, SKILL, harness=sk.harness_for("codex"), today=INSTALL_DAY)
    shown = {(s.harness.name, s.name): s for s in sk.states_all(project)}
    assert shown[("codex", SKILL)].current


def test_add_plans_threads_the_record_through_a_presss_names(project):
    """The composer the setup page's boxes call: several names for one
    harness compose from one record, so the last plan's record names
    every skill of the press and one write leaves one record."""
    names = sk.catalog_names()[:2]
    plans = sk.add_plans(project, names, targets=("codex",), today=INSTALL_DAY)
    assert [(p.harness.name, p.name) for p in plans] == [("codex", n) for n in names]
    assert [e["name"] for e in plans[-1].record_after["skills"]] == sorted(names)
    sk.write_add_plans(plans)
    record = sk.read_record(project, sk.harness_for("codex"))
    assert [e["name"] for e in record["skills"]] == sorted(names)
    assert sk.read_record(project, sk.harness_for("claude-code")) is None
    # The two command-line verbs are the two ends of it.
    assert sk.add_all_plan(project, SKILL, today=INSTALL_DAY) == \
        sk.add_plans(project, (SKILL,), today=INSTALL_DAY)
    assert sk.add_catalog_plan(project, today=INSTALL_DAY) == \
        sk.add_plans(project, sk.catalog_names(), today=INSTALL_DAY)


def test_the_harness_option_narrows_each_verb(project, capsys):
    assert cli.main(["skill", "add", SKILL, str(project), "--harness", "codex"]) == 0
    assert (project / ".agents" / "skills" / SKILL / "SKILL.md").is_file()
    assert not (project / ".claude").exists()

    assert cli.main(["skill", "list", str(project), "--harness", "codex"]) == 0
    out = capsys.readouterr().out
    assert ".agents/skills/" in out and ".claude/skills/" not in out

    assert cli.main(
        ["skill", "remove", SKILL, str(project), "--harness", "claude-code"]) == cli.NOOP
    assert "Not installed" in capsys.readouterr().out
    assert cli.main(["skill", "remove", SKILL, str(project)]) == 0
    assert not (project / ".agents").exists()


def test_a_bare_add_reports_every_tree_and_one_next_step(project, capsys):
    assert cli.main(["skill", "add", SKILL, str(project)]) == 0
    out = capsys.readouterr().out
    assert out.count("Added ") == 2
    assert out.count("Next: start a fresh agent session") == 1
    assert cli.main(["skill", "add", SKILL, str(project)]) == cli.NOOP
    out = capsys.readouterr().out
    assert out.count("Already installed") == 2


def test_no_packaged_skill_byte_names_a_harness_directory():
    base = sk.packaged_skills()
    assert base is not None
    for entry in sk.SKILL_CATALOG:
        for rel in entry.files:
            data = base.joinpath(entry.name, *rel.split("/")).read_bytes()
            for harness in sk.HARNESSES.values():
                assert harness.skills.encode("utf-8") not in data, (entry.name, rel)


# --- phase 3: Claude Code reads the route map through CLAUDE.md ---------------

CLAUDE = "CLAUDE.md"
HOUSE = b"# House rules\n\nKeep the tests green.\n"


def test_a_bare_host_gets_a_claude_file_importing_the_route_map(project):
    result = mnt.mount(project, today=MOUNT_DAY)
    data = (project / CLAUDE).read_bytes()
    assert data.startswith(mnt.FENCE_BEGIN.encode("utf-8") + b"\n@AGENTS.md\n")
    assert data.endswith(mnt.FENCE_END.encode("utf-8") + b"\n")
    entry = next(f for f in result.files if f.path == CLAUDE)
    assert entry.mode == mnt.APPENDED and entry.file_created is True
    done = mnt.unmount(project)
    assert marks(done)[CLAUDE] == (mnt.REMOVED, mnt.REMOVED_FILE)


def test_a_projects_claude_file_gains_the_import_and_gets_it_back(project):
    (project / CLAUDE).write_bytes(HOUSE)
    before = tree(project)
    mnt.mount(project, today=MOUNT_DAY)
    data = (project / CLAUDE).read_bytes()
    assert data.startswith(HOUSE + b"\n" + mnt.FENCE_BEGIN.encode("utf-8"))
    assert b"\n@AGENTS.md\n" in data
    done = mnt.unmount(project)
    assert marks(done)[CLAUDE] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert tree(project) == before


@pytest.mark.parametrize("importing", [
    b"@AGENTS.md\n",
    b"# Ours\n\n@AGENTS.md\n\nMore rules.\n",
    b"  @./AGENTS.md\r\n",
], ids=["alone", "among-rules", "dot-slash-crlf"])
def test_a_claude_file_that_already_imports_the_route_map_is_left_alone(
    project, importing,
):
    (project / CLAUDE).write_bytes(importing)
    survey = mnt.survey(project)
    assert CLAUDE not in survey.merges and CLAUDE not in survey.conflicts

    result = mnt.mount(project, today=MOUNT_DAY)
    assert (project / CLAUDE).read_bytes() == importing
    assert CLAUDE not in {f.path for f in result.files}
    row = next(d for d in result.dispositions() if d.path == CLAUDE)
    assert (row.verb, row.code) == (mnt.CURRENT, mnt.HOST_HOLDS)
    assert CLAUDE not in {e["path"] for e in mnt.read_manifest(project)["files"]}

    done = mnt.unmount(project)
    assert done.complete
    assert CLAUDE not in marks(done)
    assert (project / CLAUDE).read_bytes() == importing


@pytest.mark.parametrize("importing", [
    b"See @AGENTS.md for the rules.\n",
    b"- @AGENTS.md\n",
    b"Start at @./AGENTS.md then read on.\n",
], ids=["inline", "list-item", "inline-dot-slash"])
def test_an_import_anywhere_in_prose_is_an_import(project, importing):
    # Claude Code follows an `@path` token wherever it stands outside a
    # code span or block; a second import of the same file would be ours.
    (project / CLAUDE).write_bytes(importing)
    result = mnt.mount(project, today=MOUNT_DAY)
    assert CLAUDE not in {f.path for f in result.files}
    assert (project / CLAUDE).read_bytes() == importing


def test_an_absolute_import_of_this_projects_route_map_is_an_import(project):
    absolute = str(project / mnt.AGENTS_REL).encode("utf-8")
    (project / CLAUDE).write_bytes(b"@" + absolute + b"\n")
    result = mnt.mount(project, today=MOUNT_DAY)
    assert CLAUDE not in {f.path for f in result.files}


@pytest.mark.parametrize("not_importing", [
    b"# Notes\n\n```\n@AGENTS.md\n```\n",
    b"Use `@AGENTS.md` in your own file.\n",
    b"See @AGENTS.md.\n",
    b"Someone else's: @../other/AGENTS.md\n",
], ids=["code-block", "code-span", "full-stop", "another-project"])
def test_what_claude_code_does_not_follow_gets_the_seeded_import(project, not_importing):
    # A code block or span is skipped by import parsing; a path that is
    # not this project's AGENTS.md is not this project's route map; and
    # `@AGENTS.md.` is judged the same on every platform: not an import.
    (project / CLAUDE).write_bytes(not_importing)
    result = mnt.mount(project, today=MOUNT_DAY)
    assert CLAUDE in {f.path for f in result.files}
    assert b"\n@AGENTS.md\n" in (project / CLAUDE).read_bytes()


def _link(src: Path, dst: Path, kind: str) -> None:
    if kind == "hard":
        os.link(src, dst)
        return
    try:
        os.symlink(src.name, dst)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable here: {exc}")


@pytest.mark.parametrize("kind", ["hard", "symbolic"])
def test_a_claude_file_that_is_the_route_map_through_a_link_is_held(project, kind):
    # `ln -s AGENTS.md CLAUDE.md` is a documented convention. One file
    # behind two seed paths must get one write, or the second erases
    # the first.
    (project / mnt.AGENTS_REL).write_bytes(HOUSE)
    _link(project / mnt.AGENTS_REL, project / CLAUDE, kind)
    result = mnt.mount(project, today=MOUNT_DAY)
    row = next(d for d in result.dispositions() if d.path == CLAUDE)
    assert (row.verb, row.code) == (mnt.CURRENT, mnt.HOST_HOLDS)
    agents = (project / mnt.AGENTS_REL).read_bytes()
    assert agents.count(mnt.FENCE_BEGIN.encode("utf-8")) == 1
    assert b"## RadialRig route map" in agents
    assert b"\n@AGENTS.md\n" not in agents
    recorded = next(f for f in result.files if f.path == mnt.AGENTS_REL)
    assert recorded.sha256 == mnt._sha256(agents)
    done = mnt.unmount(project)
    assert marks(done)[mnt.AGENTS_REL] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / mnt.AGENTS_REL).read_bytes() == HOUSE


def test_two_seed_paths_that_are_one_file_refuse_rather_than_write_twice(project):
    # The link the mount knows how to hold is CLAUDE.md to AGENTS.md;
    # any other pair sharing one file is named and refused. An empty
    # file passes every per-file check, so only the pairing catches it.
    (project / ".codex").mkdir()
    (project / CLAUDE).write_bytes(b"")
    os.link(project / CLAUDE, project / CODEX)
    with pytest.raises(mnt.MountConflict) as caught:
        mnt.mount(project, today=MOUNT_DAY)
    assert set(caught.value.conflicts) == {CLAUDE, CODEX}


def test_a_held_claude_file_reads_as_held_on_the_guided_page_and_on_adopt(project):
    (project / CLAUDE).write_bytes(b"@AGENTS.md\n\nOur own notes.\n")
    result = mnt.mount(project, today=MOUNT_DAY)
    rows = {label: (verb, reason)
            for verb, label, reason in setup_web._families(result.dispositions())}
    held = mnt.Disposition(CLAUDE, mnt.CURRENT, mnt.HOST_HOLDS)
    assert rows[CLAUDE] == (mnt.CURRENT, mnt.reason_text(held))
    touched = sum(1 for d in result.dispositions() if d.verb != mnt.CURRENT)
    assert f"{touched} path(s)" in setup_web._plan_table(result.dispositions(), "Wrote")

    (project / mnt.MANIFEST_REL).unlink()
    plan = ad.adopt_plan(project)
    row = next(d for d in plan.dispositions if d.path == CLAUDE)
    assert (row.verb, row.code) == (mnt.CURRENT, mnt.HOST_HOLDS)
    assert plan.kept == 0
    assert CLAUDE not in {f.path for f in plan.files}


def test_a_fenced_claude_file_alone_is_no_proof_of_a_mount(project):
    # Three lines a project can carry without ever mounting: adopt
    # writes no record on them, so a later unmount cannot delete the
    # project's own file (BUG-0002's gate, kept narrow).
    context = mnt._survey_context()
    (project / CLAUDE).write_bytes(mnt._section_bytes(context, CLAUDE))
    (project / mnt.MCP_REL).write_text(
        json.dumps({"mcpServers": {mnt.SERVER_KEY: mnt._mcp_entry()}}), encoding="utf-8")
    plan = ad.adopt(project)
    assert not plan.files
    assert not (project / mnt.MANIFEST_REL).exists()
    assert (project / CLAUDE).is_file()


def test_an_upgrade_never_puts_back_a_route_map_the_project_removed(
    project, next_release,
):
    # A record without AGENTS.md is one adopt could not claim; upgrade
    # writes no fresh route map beside a rulebook the project edited
    # (BUG-0004 pairs the two) or over a section the project took out.
    mnt.mount(project, today=MOUNT_DAY)
    spec = project / mnt.SPEC_REL
    spec.write_bytes(spec.read_bytes() + b"\nOur own clause.\n")
    agents = project / mnt.AGENTS_REL
    data = agents.read_bytes()
    start, end = mnt._locate_section(data)
    agents.write_bytes(data[:start] + data[end:])
    (project / mnt.MANIFEST_REL).unlink()
    ad.adopt(project)
    assert mnt.AGENTS_REL not in {e["path"] for e in mnt.read_manifest(project)["files"]}
    next_release()
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert mnt.AGENTS_REL not in verbs(plan)
    assert mnt.FENCE_BEGIN.encode("utf-8") not in agents.read_bytes()
    assert b"Our own clause." in spec.read_bytes()


# --- the review pins: the Codex file under upgrade and opt-out -------------

def test_the_survey_and_the_mount_give_one_answer_for_one_file(project):
    # A host whose bytes parse alone but not once a table header follows
    # (an inline table cannot be extended) is a conflict to both.
    (project / ".codex").mkdir()
    (project / CODEX).write_bytes(b'mcp_servers = { other = { command = "x", args = [] } }\n')
    assert CODEX in mnt.survey(project).conflicts
    with pytest.raises(mnt.MountConflict):
        mnt.mount(project, today=MOUNT_DAY)


def test_an_upgrade_refuses_a_file_that_no_longer_takes_the_section(
    codex_host, next_release,
):
    project = codex_host
    mnt.mount(project, today=MOUNT_DAY)
    path = project / CODEX
    path.write_bytes(b"this is = not toml = at all\n" + path.read_bytes())
    before = path.read_bytes()
    next_release(add_a_comment)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert (verbs(plan)[CODEX], codes(plan)[CODEX]) == (mnt.LEFT, mnt.UPGRADE_SECTION_REFUSED)
    assert path.read_bytes() == before


def test_a_key_after_the_end_fence_falls_into_the_write_table_and_holds_the_upgrade(
    project, next_release,
):
    mnt.mount(project, today=MOUNT_DAY)
    path = project / CODEX
    path.write_bytes(path.read_bytes() + b'approval_policy = "never"\n')
    assert "approval_policy" in load(project)["mcp_servers"][mnt.WRITE_SERVER_KEY]
    next_release(add_a_comment)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert codes(plan)[CODEX] == mnt.UPGRADE_SECTION_REFUSED
    assert "Keep the region last" in path.read_text(encoding="utf-8")


def test_removing_the_write_table_is_an_opt_out_unmount_honors(codex_host):
    project = codex_host
    mnt.mount(project, today=MOUNT_DAY)
    drop_write_table(project / CODEX)
    assert set(load(project)["mcp_servers"]) == {"other", mnt.SERVER_KEY}
    done = mnt.unmount(project)
    assert marks(done)[CODEX] == (mnt.RESTORED, mnt.RESTORED_PREMOUNT)
    assert (project / CODEX).read_bytes() == OURS


def test_an_opted_out_region_upgrades_in_its_opted_out_form(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    drop_write_table(project / CODEX)
    next_release(add_a_comment)
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert (verbs(plan)[CODEX], codes(plan)[CODEX]) == (mnt.UPDATED, mnt.UPGRADE_SECTION_APPLIED)
    assert b"a comment the newer release added" in region(project)
    assert set(load(project)["mcp_servers"]) == {mnt.SERVER_KEY}
    again = mnt.upgrade_plan(project)
    assert codes(again)[CODEX] == mnt.UPGRADE_SECTION_CURRENT
    done = mnt.unmount(project)
    assert marks(done)[CODEX] == (mnt.REMOVED, mnt.REMOVED_FILE)


def test_removing_the_write_key_from_mcp_json_is_an_opt_out_too(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    path = project / mnt.MCP_REL
    loaded = json.loads(path.read_text(encoding="utf-8"))
    del loaded["mcpServers"][mnt.WRITE_SERVER_KEY]
    path.write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")
    next_release()
    plan = mnt.upgrade_plan(project)
    item = next(i for i in plan.items if i.path == mnt.MCP_REL)
    assert (item.verb, item.code) == (mnt.CURRENT, mnt.UPGRADE_ENTRY_REMOVED)
    assert mnt.WRITE_SERVER_KEY in (item.detail or "")
    assert all(i.path != mnt.MCP_REL or i.verb != mnt.ABSENT for i in plan.items)
    done = mnt.unmount(project)
    assert marks(done)[mnt.MCP_REL] == (mnt.REMOVED, mnt.REMOVED_FILE)
    assert not path.exists()


def test_a_refusal_names_what_blocks_the_codex_file(codex_host):
    (codex_host / CODEX).write_bytes(b'[mcp_servers.radialrig]\ncommand = "mine"\n')
    with pytest.raises(mnt.MountConflict) as caught:
        mnt.mount(codex_host, today=MOUNT_DAY)
    said = "\n".join(report.conflict_report(caught.value.conflicts))
    assert "server table" in said and "TOML" in said
    assert "already has a file" not in said
    assert "fenced section" in report.conflict_reason(CLAUDE)


# --- the review pins: one press, every harness -------------------------------

def test_every_harness_is_written_in_one_write(project, monkeypatch):
    calls: list[dict] = []
    real = mnt._write_all

    def counting(root, payload, removals=()):
        calls.append(dict(payload))
        return real(root, payload, removals)

    monkeypatch.setattr(mnt, "_write_all", counting)
    sk.add_all(project, SKILL, today=INSTALL_DAY)
    assert len(calls) == 1
    written = set(calls[0])
    for harness in sk.HARNESSES.values():
        assert f"{harness.skills}/{SKILL}/SKILL.md" in written
        assert harness.record in written


def test_a_fault_while_writing_is_the_kits_own_error(project, monkeypatch, capsys):
    def failing(root, payload, removals=()):
        raise mnt.WriteStopped(root / ".agents", "denied")

    monkeypatch.setattr(mnt, "_write_all", failing)
    with pytest.raises(sk.SkillError):
        sk.add_all(project, SKILL, today=INSTALL_DAY)
    assert cli.main(["skill", "add", SKILL, str(project)]) == cli.ERROR
    assert "nothing was written" in capsys.readouterr().err


def test_a_press_that_writes_one_tree_and_leaves_another_says_both(project, capsys):
    claude = sk.harness_for("claude-code")
    sk.add(project, SKILL, harness=claude, today=INSTALL_DAY)
    (project / claude.skills / SKILL / "SKILL.md").write_bytes(b"# ours\n")
    assert cli.main(["skill", "add", SKILL, str(project)]) == cli.PARTIAL
    out = capsys.readouterr().out
    assert report.SKILL_NEXT_STEP in out
    assert report.SKILL_EDIT_SOME_TAIL in out
    assert report.SKILL_EDIT_TAIL not in out
    assert (project / ".agents" / "skills" / SKILL / "SKILL.md").is_file()


def test_a_claude_file_carrying_the_fence_refuses_the_mount(project):
    (project / CLAUDE).write_bytes(
        b"@AGENTS.md\n" + mnt.FENCE_BEGIN.encode("utf-8") + b"\n"
        + mnt.FENCE_END.encode("utf-8") + b"\n")
    with pytest.raises(mnt.MountConflict) as caught:
        mnt.mount(project, today=MOUNT_DAY)
    assert caught.value.conflicts == (CLAUDE,)


def test_a_record_from_before_the_plan_gains_the_claude_file_on_upgrade(
    project, next_release,
):
    mnt.mount(project, today=MOUNT_DAY)
    forget(project, CLAUDE)
    (project / CLAUDE).unlink()
    next_release()
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)[CLAUDE] == mnt.CREATED
    assert codes(plan)[CLAUDE] == mnt.UPGRADE_NEW_WRITTEN
    assert b"\n@AGENTS.md\n" in (project / CLAUDE).read_bytes()
    assert record(project, CLAUDE)["mode"] == mnt.APPENDED


def test_an_upgrade_leaves_a_claude_file_that_already_imports(project, next_release):
    mnt.mount(project, today=MOUNT_DAY)
    forget(project, CLAUDE)
    (project / CLAUDE).write_bytes(b"@AGENTS.md\n")
    next_release()
    plan = mnt.upgrade(project, today=UPGRADE_DAY)
    assert verbs(plan)[CLAUDE] == mnt.CURRENT
    assert codes(plan)[CLAUDE] == mnt.HOST_HOLDS
    assert plan.pending == 0
    assert (project / CLAUDE).read_bytes() == b"@AGENTS.md\n"
    with pytest.raises(StopIteration):
        record(project, CLAUDE)


def test_the_route_map_says_how_each_harness_reaches_it(project):
    mnt.mount(project, today=MOUNT_DAY)
    text = (project / mnt.AGENTS_REL).read_text(encoding="utf-8")
    assert "`CLAUDE.md`" in text and "Codex reads this file" in text


# --- PLAN-0015 phase 1: two agents, two paths ---------------------------
# A skill installed for one agent and not another is a state a person
# chose, not a fault (D-36 as amended 2026-09-08). The reports name the
# agent in every head from the map's label, a whole-catalog add fills
# one agent's list in one press, and the upgrade says nothing about
# skills. The issue #41 diagnostic these replace is in the bug note.

def claude_only(project: Path) -> None:
    sk.add(project, SKILL, harness=sk.harness_for("claude-code"), today=INSTALL_DAY)


def strings_in(value) -> list[str]:
    """Every string a constant carries, table or not."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in strings_in(item)]
    if isinstance(value, (list, tuple, set)):
        return [text for item in value for text in strings_in(item)]
    return []


def test_every_block_of_a_skill_report_names_its_agent_from_the_label(project, capsys):
    version = sk.catalog_entry(SKILL).version
    root = project.resolve()
    labels = [h.label for h in sk.HARNESSES.values()]
    assert cli.main(["skill", "add", SKILL, str(project), "--dry-run"]) == 0
    out = capsys.readouterr().out
    for label in labels:
        assert f"Would add {SKILL} {version} for {label} to {root}" in out
    assert cli.main(["skill", "add", SKILL, str(project)]) == 0
    out = capsys.readouterr().out
    for label in labels:
        assert f"Added {SKILL} {version} for {label} to {root}" in out
    assert cli.main(["skill", "add", SKILL, str(project)]) == cli.NOOP
    out = capsys.readouterr().out
    for label in labels:
        assert f"Already installed for {label}: {SKILL} {version} in {root}" in out
    assert cli.main(["skill", "remove", SKILL, str(project)]) == 0
    out = capsys.readouterr().out
    for label in labels:
        assert f"Removed {SKILL} for {label} from {root}" in out


def test_an_edited_tree_is_left_alone_for_its_agent_by_name(project, capsys):
    claude = sk.harness_for("claude-code")
    sk.add(project, SKILL, harness=claude, today=INSTALL_DAY)
    (project / claude.skills / SKILL / "SKILL.md").write_bytes(b"# ours\n")
    assert cli.main(["skill", "add", SKILL, str(project)]) == cli.PARTIAL
    out = capsys.readouterr().out
    assert f"Left alone for {claude.label}: {SKILL} in {project.resolve()}" in out


def test_a_current_block_in_a_two_agent_report_is_its_head_line_alone(project, capsys):
    claude_only(project)
    claude, codex = sk.harness_for("claude-code"), sk.harness_for("codex")
    assert cli.main(["skill", "add", SKILL, str(project), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"Already installed for {claude.label}:" in out
    assert f"Would add {SKILL}" in out and f"for {codex.label} to" in out
    # The current tree's rows are not printed; the written tree's are.
    assert f"{claude.skills}/{SKILL}/SKILL.md" not in out
    assert f"{codex.skills}/{SKILL}/SKILL.md" in out
    assert out.count(report.SKILL_NEXT_STEP) == 1
    assert report.SKILL_CURRENT_TAIL not in out
    # Narrowed to the one agent that has it, the report is one block and
    # keeps its rows: there is nothing else on the page to read.
    assert cli.main(
        ["skill", "add", SKILL, str(project), "--harness", "claude-code"]) == cli.NOOP
    out = capsys.readouterr().out
    assert f"{claude.skills}/{SKILL}/SKILL.md" in out
    assert report.SKILL_CURRENT_TAIL in out


def test_add_all_fills_one_agents_list_and_says_nothing_of_the_other(project, capsys):
    """The phase's done-condition, on the command line."""
    claude_only(project)
    claude, codex = sk.harness_for("claude-code"), sk.harness_for("codex")
    before = tree(project)
    root = project.resolve()
    assert cli.main(
        ["skill", "add", "--all", str(project), "--harness", "codex", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert tree(project) == before
    for entry in sk.SKILL_CATALOG:
        assert f"Would add {entry.name} {entry.version} for {codex.label} to {root}" in out
    assert out.count("Would add ") == len(sk.SKILL_CATALOG)
    assert claude.label not in out and claude.skills not in out
    assert cli.DRY_RUN_TAIL in out
    # One record is written per agent, and the report says so once: the
    # first block that writes it carries its row, the blocks after it
    # do not.
    assert out.count(codex.record) == 1
    assert f"{mnt.CREATED:<9} {codex.record}" in out
    results = sk.add_catalog_plan(project, targets=("codex",))
    assert [any(d.path == codex.record for d in r.dispositions) for r in results] == \
        [True] + [False] * (len(results) - 1)
    assert all(dict(r.payload).get(codex.record) for r in results)

    assert cli.main(["skill", "add", "--all", str(project), "--harness", "codex"]) == 0
    out = capsys.readouterr().out
    assert out.count("Added ") == len(sk.SKILL_CATALOG)
    assert out.count(report.SKILL_NEXT_STEP) == 1
    for entry in sk.SKILL_CATALOG:
        assert (project / codex.skills / entry.name / "SKILL.md").is_file()
        assert (project / claude.skills / entry.name).exists() == (entry.name == SKILL)
    body = json.loads((project / codex.record).read_text(encoding="utf-8"))
    assert [e["name"] for e in body["skills"]] == sorted(sk.catalog_names())

    assert cli.main(["skill", "add", "--all", str(project), "--harness", "codex"]) == cli.NOOP
    out = capsys.readouterr().out
    for entry in sk.SKILL_CATALOG:
        assert (f"Already installed for {codex.label}: {entry.name} "
                f"{entry.version} in {root}") in out
    # One head line per skill and none of its rows.
    assert codex.skills not in out
    assert report.SKILL_CURRENT_TAIL in out


def test_add_all_for_every_agent_writes_one_record_per_agent_naming_every_skill(project):
    results = sk.add_catalog(project, today=INSTALL_DAY)
    assert [(r.harness.name, r.name) for r in results] == [
        (h, n) for h in sk.HARNESSES for n in sk.catalog_names()]
    for harness in sk.HARNESSES.values():
        body = json.loads((project / harness.record).read_text(encoding="utf-8"))
        assert [e["name"] for e in body["skills"]] == sorted(sk.catalog_names())
    assert all(state.current for state in sk.states_all(project))
    again = sk.add_catalog_plan(project, today=INSTALL_DAY)
    assert all(r.current for r in again) and not any(r.payload for r in again)


def test_add_all_refuses_whole_when_one_tree_is_blocked(project, capsys):
    other = next(name for name in sk.catalog_names() if name != SKILL)
    foreign = project / ".agents" / "skills" / other
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_bytes(b"theirs\n")
    before = tree(project)
    assert cli.main(["skill", "add", "--all", str(project)]) == cli.REFUSED
    out = capsys.readouterr().out
    assert f".agents/skills/{other}/" in out
    assert report.SKILL_FOREIGN_TAIL in out
    assert tree(project) == before
    assert not (project / ".claude").exists()


def test_add_takes_a_name_or_all_and_never_both_or_neither(project, capsys):
    # A skill's name beside --all is the usage error whichever slot it
    # lands in: never read as a folder, so nobody is told one is missing.
    for argv in (["skill", "add"], ["skill", "add", "--all", SKILL, str(project)],
                 ["skill", "add", SKILL, "--all"], ["skill", "add", "--all", SKILL, "."]):
        with pytest.raises(SystemExit) as caught:
            cli.main(argv)
        assert caught.value.code == 2, argv
        assert "usage:" in capsys.readouterr().err
    assert not (project / ".claude").exists() and not (project / ".agents").exists()


def test_a_folder_in_the_name_slot_is_told_to_name_a_skill_or_use_all(project, capsys):
    # `skill add <folder>` is a forgotten --all or a forgotten name; the
    # answer names both ways out rather than saying the folder is no skill.
    with pytest.raises(SystemExit) as caught:
        cli.main(["skill", "add", str(project)])
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "is a folder, not a skill" in err and "--all" in err
    assert "ships no skill named" not in err
    assert not (project / ".claude").exists() and not (project / ".agents").exists()


def test_the_upgrade_report_lists_skills_under_the_agent_that_has_them(project, capsys):
    """D-36 rule 2 as amended on 2026-10-02 (PLAN-0021 phase 1): the
    upgrade brings up the skills a project already has, per agent. A
    skill one agent has and the other lacks is one block, under that
    agent's label; the other agent, with no record, gets no block and
    no sentence about a split."""
    mnt.mount(project, today=MOUNT_DAY)
    claude_only(project)
    first, second = sk.harnesses()[:2]
    for flags in (["--dry-run"], []):
        assert cli.main(["upgrade", str(project), *flags]) == 0
        out = capsys.readouterr().out
        assert f"Skills for {first.label}" in out
        assert f"{first.skills}/{SKILL}/" in out
        assert f"Skills for {second.label}" not in out
        assert second.skills not in out
        assert cli.ENGINE_HALF in out


def test_skill_list_keeps_one_row_per_skill_per_agent_and_names_all_in_its_tail(
    project, capsys,
):
    claude_only(project)
    assert cli.main(["skill", "list", str(project)]) == 0
    out = capsys.readouterr().out
    for harness in sk.HARNESSES.values():
        assert f"{harness.skills}/{SKILL}/" in out
    assert "--all" in report.SKILL_LIST_TAIL and report.SKILL_LIST_TAIL in out
    for harness in sk.HARNESSES.values():
        assert harness.name not in out and harness.label not in out


def test_no_skill_sentence_names_an_agent_and_the_label_arrives_from_the_map(project):
    """PLAN-0015 constraint 3, the agent half: a skill sentence in the
    four surfaces names no agent by its key or its label. The label is
    filled at render time, so an agent the map has never heard of is
    named by the head all the same, the way a client's label is."""
    from radialrig import manage_web, setup_web
    forbidden = [word for h in sk.HARNESSES.values() for word in (h.name, h.label)]
    assert forbidden
    for module in (report, cli, manage_web, setup_web):
        for name in dir(module):
            if not name.isupper() or "SKILL" not in name:
                continue
            for text in strings_in(getattr(module, name)):
                for word in forbidden:
                    assert word.lower() not in text.lower(), \
                        f"{module.__name__}.{name} names '{word}'"
                # Nor does a sentence a page can print say "harness":
                # the word for the thing that reads a skill is "agent"
                # (PLAN-0015 objective). The flag keeps its own name in
                # a command line, and a template's slot is not a word.
                if module is not cli:
                    spoken = text.replace("--harness", "").replace("{harness}", "")
                    assert "harness" not in spoken.lower(), \
                        f"{module.__name__}.{name} says 'harness'"
    other = sk.Harness(name="other", label="Some Other Agent",
                       skills=".other/skills", record=".other/radialrig-skills.json")
    added = sk.add_plan(project, SKILL, harness=other, today=INSTALL_DAY)
    head = report._skill_add_parts(added, dry_run=True)[0][0]
    assert head.startswith(f"Would add {SKILL} ") and " for Some Other Agent to " in head
    removed = sk.remove_plan(project, SKILL, harness=other)
    assert " for Some Other Agent from " in report._skill_remove_parts(
        removed, dry_run=True)[0][0]
