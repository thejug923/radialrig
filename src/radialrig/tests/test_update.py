"""Tests for the combined update step — PLAN-0021 phase 1, prescription 2.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_update.py -q

`update.plan` returns the seed upgrade plan and the skills refresh
results and writes nothing; `update.run` runs the seed upgrade and then
the skills refresh; `update.behind` answers Decided 6: a project is
behind when its update would write something. A newer release is
simulated the way test_upgrade.py and test_skills.py simulate one, for
both halves at once.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
import shutil
from pathlib import Path

import pytest

import radialrig
from radialrig import cli
from radialrig import mount as mnt
from radialrig import report
from radialrig import roster as ro
from radialrig import skills as sk
from radialrig.paths import packaged_seed
from radialrig.tests.test_mount import (  # noqa: F401 - fixtures are used by name
    MOUNT_DAY,
    NEXT_VERSION,
    _make_project,
    tree,
)
from radialrig.tests.test_skills import (
    INSTALL_DAY,
    RECORD_REL,
    SKILL,
    SKILL_DIR,
    edit_skill,
    record_of,
)
from radialrig.tests.test_upgrade import edit_spec

UPDATE_DAY = dt.date(2026, 10, 2)


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path / "sample-project")


@pytest.fixture()
def newer_release(tmp_path, monkeypatch):
    """A newer radialrig for both halves: a copy of the seed and of the
    skill sources, each with an optional edit, and a version. Call it
    after the mount and the add, so the records were written by the
    release the project actually took."""
    def install(*, seed_edit=None, skill_edit=None, version=NEXT_VERSION):
        seed_base = packaged_seed()
        skills_base = sk.packaged_skills()
        assert seed_base is not None and skills_base is not None
        copies = {}
        for name, base in (("next-seed", seed_base), ("next-skills", skills_base)):
            target = tmp_path / name
            for source in sorted(base.rglob("*")):
                if source.is_file():
                    copy = target / source.relative_to(base)
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    copy.write_bytes(source.read_bytes())
            copies[name] = target
        if seed_edit is not None:
            seed_edit(copies["next-seed"])
        if skill_edit is not None:
            skill_edit(copies["next-skills"])
        monkeypatch.setattr(radialrig, "__version__", version)
        monkeypatch.setattr(mnt, "packaged_seed", lambda: copies["next-seed"])
        monkeypatch.setattr(sk, "packaged_skills", lambda: copies["next-skills"])
        return copies["next-seed"], copies["next-skills"]

    return install


def ready(project: Path) -> Path:
    """A mounted project with the one skill installed for every agent."""
    mnt.mount(project, today=MOUNT_DAY)
    sk.add_all(project, SKILL, today=INSTALL_DAY)
    return project


def test_plan_writes_nothing_and_carries_both_halves(project, newer_release):
    from radialrig import update

    ready(project)
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    before = tree(project)
    plan = update.plan(project)
    assert tree(project) == before
    assert plan.root == project.resolve()
    assert {i.path: i.verb for i in plan.seed.items}["docs/spec.md"] == mnt.UPDATED
    assert [(r.harness.name, r.name, r.wrote) for r in plan.skills] == [
        (h.name, SKILL, True) for h in sk.harnesses()]
    assert plan.skills_error == ""
    assert plan.wrote is False or plan.seed.writes   # a plan describes, it does not write
    assert plan.refreshed == len(sk.harnesses())
    assert plan.pending_skills == 0 and plan.review is False


def test_run_writes_the_seed_then_refreshes_the_skills(project, newer_release):
    from radialrig import update

    ready(project)
    seed, skills = newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    plan = update.run(project, today=UPDATE_DAY)
    assert b"A sentence the newer release added." in (
        project / "docs" / "spec.md").read_bytes()
    assert mnt.read_manifest(project)["radialrig"] == NEXT_VERSION
    theirs = (skills / SKILL / "SKILL.md").read_bytes()
    for harness in sk.harnesses():
        assert (project / harness.skills / SKILL / "SKILL.md").read_bytes() == theirs
    assert record_of(project)["radialrig"] == NEXT_VERSION
    assert plan.seed.applied >= 1 and plan.seed.pending == 0
    assert plan.refreshed == len(sk.harnesses()) and plan.review is False
    assert plan.wrote

    # Nothing left to do: a second run touches nothing at all.
    before = tree(project)
    again = update.run(project, today=dt.date(2027, 1, 1))
    assert tree(project) == before
    assert not again.wrote and again.refreshed == 0
    # A current skill is not left for review: nothing waits on anyone.
    assert again.pending_skills == 0 and again.review is False


def test_plan_and_run_say_the_same_rows(project, newer_release):
    from radialrig import update

    ready(project)
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    rehearsed = update.plan(project)
    real = update.run(project, today=UPDATE_DAY)
    assert rehearsed.seed.dispositions() == real.seed.dispositions()
    assert [r.dispositions for r in rehearsed.skills] == [r.dispositions for r in real.skills]


def test_an_edited_skill_is_left_with_its_diff_and_earns_review(project, newer_release):
    from radialrig import update

    ready(project)
    first = sk.harnesses()[0]
    (project / first.skills / SKILL / "SKILL.md").write_bytes(b"# ours now\n")
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    plan = update.run(project, today=UPDATE_DAY)
    assert (project / first.skills / SKILL / "SKILL.md").read_bytes() == b"# ours now\n"
    mine = next(r for r in plan.skills if r.harness.name == first.name)
    assert mine.left and mine.edited and "# ours now" in "".join(mine.diffs)
    assert plan.refreshed == len(sk.harnesses()) - 1
    assert plan.pending_skills == 1
    assert plan.review is True
    assert plan.seed.pending == 0


def test_behind_answers_whether_the_update_would_write(project, newer_release):
    from radialrig import update

    # Not mounted: the upgrade does nothing here, so nothing is behind.
    assert update.behind(project) is False
    ready(project)
    assert update.behind(project) is False
    # Behind on a seeded file alone.
    newer_release(seed_edit=edit_spec)
    assert update.behind(project) is True
    update.run(project, today=UPDATE_DAY)
    assert update.behind(project) is False


def test_behind_on_a_skill_alone_and_not_on_an_edited_or_removed_one(
    project, newer_release,
):
    from radialrig import update

    ready(project)
    newer_release(skill_edit=edit_skill)
    assert update.behind(project) is True
    # Edited for every agent: the update would write nothing.
    for harness in sk.harnesses():
        (project / harness.skills / SKILL / "SKILL.md").write_bytes(b"# ours now\n")
    assert update.behind(project) is False
    # Removed for every agent: never put back, so not behind either.
    for harness in sk.harnesses():
        shutil.rmtree(project / harness.skills / SKILL)
    assert update.behind(project) is False


def test_behind_never_raises_on_a_record_this_build_cannot_read(project, newer_release):
    from radialrig import update

    ready(project)
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    for harness in sk.harnesses():
        (project / harness.record).write_bytes("{}".encode("utf-16"))
    # The seed half still answers; the unreadable skills records count
    # for nothing rather than failing the listing.
    assert update.behind(project) is True
    (project / mnt.MANIFEST_REL).write_bytes(b"{ not json\n")
    assert update.behind(project) is False


def stop_the_second_write(monkeypatch) -> str:
    """The skills write stops the way a locked file stops it, after the
    seed's own write went through: the writer's own stop, raised from
    the second call it gets. Returns the sentence the stop carries."""
    real = mnt._write_all
    calls: list[int] = []
    path = sk.harnesses()[0].record

    def second_stops(root, payload, removals=()):
        calls.append(1)
        if len(calls) == 2:
            raise mnt.WriteStopped(path, "locked")
        return real(root, payload, removals)

    monkeypatch.setattr(mnt, "_write_all", second_stops)
    return mnt.stopped_sentence(path, "locked", ())


def test_a_failed_skills_step_after_the_seed_was_written_is_reported(
    project, newer_release, monkeypatch,
):
    from radialrig import update

    ready(project)
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    before = tree(project)
    sentence = stop_the_second_write(monkeypatch)
    plan = update.run(project, today=UPDATE_DAY)
    # The seed was written.
    assert plan.seed.applied >= 1
    assert b"A sentence the newer release added." in (
        project / "docs" / "spec.md").read_bytes()
    # The skills step failed and says so; no skill file changed.
    assert plan.skills == ()
    assert plan.skills_error == sentence
    assert plan.review is True
    after = tree(project)
    for rel, data in before[0].items():
        if rel.startswith(".claude/") or rel.startswith(".agents/"):
            assert after[0][rel] == data, rel
    # The rehearsal composes the half the write stopped, and writes nothing.
    rehearsed = update.plan(project)
    assert rehearsed.skills_error == "" and rehearsed.refreshed == len(sk.harnesses())
    assert tree(project) == after


def test_one_agents_unreadable_record_does_not_stop_the_other_agents_refresh(
    project, newer_release, capsys,
):
    """Each agent stands alone. The agent whose record this build cannot
    read is reported under its own head in the kit's own sentence and
    counts toward exit 5; the other agent's behind copy is refreshed,
    and afterwards the project is no longer behind on it."""
    from radialrig import update

    ready(project)
    first, second = sk.harnesses()[:2]
    _, skills = newer_release(skill_edit=edit_skill)
    (project / first.record).write_bytes("{}".encode("utf-16"))
    held = (project / first.skills / SKILL / "SKILL.md").read_bytes()
    assert update.behind(project) is True

    rehearsed = update.plan(project)
    assert rehearsed.skills_error == ""
    assert [(r.harness.name, r.name, bool(r.error), r.pending) for r in rehearsed.skills] == [
        (first.name, "", True, True), (second.name, SKILL, False, False)]
    assert first.record in rehearsed.skills[0].error
    assert rehearsed.refreshed == 1 and rehearsed.pending_skills == 0
    assert rehearsed.review is True

    code, out, err = run(capsys, "upgrade", str(project))
    assert (code, err) == (5, "")
    theirs = (skills / SKILL / "SKILL.md").read_bytes()
    assert (project / second.skills / SKILL / "SKILL.md").read_bytes() == theirs
    assert (project / first.skills / SKILL / "SKILL.md").read_bytes() == held
    assert f"Skills for {first.label}" in out and f"Skills for {second.label}" in out
    assert out.index(f"Skills for {first.label}") < out.index(first.record)
    assert "does not parse as JSON" in out
    assert f"{mnt.UPDATED:<9} {second.skills}/{SKILL}/SKILL.md" in out
    assert closing_line(out).endswith(". 1 skill(s) refreshed, 0 left for review.")
    assert update.behind(project) is False


def test_a_skill_held_back_by_this_projects_own_file_is_not_behind_and_is_left_for_review(
    project, newer_release, monkeypatch, capsys,
):
    """Decided 6: behind means the update would write something. A
    release that ships a file at a path this project put its own file
    at is refused whole for that skill, so nothing would be written:
    the project is not behind on it, and the upgrade lists the skill as
    left for review and exits 5, rather than reading "Update available"
    after every press."""
    from radialrig import update

    ready(project)
    entry = sk.catalog_entry(SKILL)
    grown = dataclasses.replace(entry, files=(*entry.files, "reference/new.md"))
    monkeypatch.setattr(sk, "SKILL_CATALOG", tuple(
        grown if e.name == SKILL else e for e in sk.SKILL_CATALOG))

    def ship_new_file(package_root: Path) -> None:
        (package_root / SKILL / "reference").mkdir()
        (package_root / SKILL / "reference" / "new.md").write_bytes(b"theirs\n")

    newer_release(skill_edit=ship_new_file)
    for harness in sk.harnesses():
        (project / harness.skills / SKILL / "reference").mkdir()
        (project / harness.skills / SKILL / "reference" / "new.md").write_bytes(b"ours\n")

    assert sk.behind_states(project) == ()
    assert update.behind(project) is False
    before = tree(project)
    code, out, err = run(capsys, "upgrade", str(project))
    assert tree(project) == before
    assert (code, err) == (5, "")
    for harness in sk.harnesses():
        assert f"Skills for {harness.label}" in out
        assert f"{mnt.LEFT:<9} {harness.skills}/{SKILL}/reference/new.md" in out
        assert (project / harness.skills / SKILL / "reference" / "new.md"
                ).read_bytes() == b"ours\n"
    assert closing_line(out).endswith(
        f". 0 skill(s) refreshed, {len(sk.harnesses())} left for review.")
    assert update.behind(project) is False


def test_every_skill_left_for_review_counts_toward_exit_five_and_a_current_one_does_not(
    project, newer_release, capsys,
):
    """Prescription 3's rule, the one the seed half already uses: every
    row LEFT or ABSENT counts. A skill is left for review when its rows
    say so — an edited copy, a folder the person removed, a skill this
    package no longer ships — and a current one is not. The closing
    line counts the skills by the same rule as the exit code."""
    from radialrig import update

    mnt.mount(project, today=MOUNT_DAY)
    first = sk.harnesses()[0]
    edited, removed, bygone, current = (
        "session-handoff", "plan-review", "memory-keeper", "promotion-gate")
    for name in (edited, removed, bygone, current):
        sk.add_all(project, name, targets=(first.name,), today=INSTALL_DAY)
    (project / first.skills / edited / "SKILL.md").write_bytes(b"# ours now\n")
    shutil.rmtree(project / first.skills / removed)
    record = project / first.record
    body = json.loads(record.read_text(encoding="utf-8"))
    for entry in body["skills"]:
        if entry["name"] == bygone:
            entry["name"], entry["dir"] = "bygone", f"{first.skills}/bygone"
    record.write_bytes(mnt._dump_manifest(body))
    newer_release(skill_edit=edit_skill)

    plan = update.run(project, today=UPDATE_DAY)
    by_name = {r.name: r for r in plan.skills}
    assert set(by_name) == {edited, removed, "bygone", current}
    assert [by_name[n].pending for n in (edited, removed, "bygone", current)] == [
        True, True, True, False]
    assert plan.refreshed == 0 and plan.pending_skills == 3
    assert plan.review is True

    code, out, err = run(capsys, "upgrade", str(project))
    assert (code, err) == (5, "")
    assert f"{mnt.LEFT:<9} {first.skills}/{edited}/" in out
    assert f"{mnt.ABSENT:<9} {first.skills}/{removed}/" in out
    assert f"{mnt.LEFT:<9} {first.skills}/bygone/" in out
    assert f"{mnt.CURRENT:<9} {first.skills}/{current}/" in out
    assert closing_line(out).endswith(". 0 skill(s) refreshed, 3 left for review.")


# --- the command line (PLAN-0021 phase 1, prescription 3) --------------
# `radialrig upgrade [PATH]` runs the combined step and reports both
# halves; `--all` does it for every mounted project in the listing whose
# folder is there. Exit codes stay 0, or 5 when anything was left for
# review, an edited skill included.

OTHER = "plan-review"


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def closing_line(out: str) -> str:
    return next(line for line in out.splitlines() if "file(s) applied" in line)


def test_the_upgrade_refreshes_the_skills_this_project_has(
    project, newer_release, capsys,
):
    ready(project)
    _, skills = newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    code, out, err = run(capsys, "upgrade", str(project))
    assert (code, err) == (0, "")
    theirs = (skills / SKILL / "SKILL.md").read_bytes()
    for harness in sk.harnesses():
        assert f"Skills for {harness.label}" in out
        assert f"{mnt.UPDATED:<9} {harness.skills}/{SKILL}/SKILL.md" in out
        assert (project / harness.skills / SKILL / "SKILL.md").read_bytes() == theirs
    # The skills come after the seed block, and the closing line counts both.
    assert out.index("Skills for") > out.index("docs/spec.md")
    assert re.fullmatch(
        r"\d+ file\(s\) applied, 0 left for review\. "
        rf"{len(sk.harnesses())} skill\(s\) refreshed, 0 left for review\.",
        closing_line(out),
    ), closing_line(out)
    assert cli.ENGINE_HALF in out
    assert out.splitlines()[-1] == report.engine_line()


def test_an_edited_skill_is_left_with_its_diff_and_the_upgrade_exits_five(
    project, newer_release, capsys,
):
    ready(project)
    first, second = sk.harnesses()[:2]
    (project / first.skills / SKILL / "SKILL.md").write_bytes(b"# ours now\n")
    newer_release(skill_edit=edit_skill)
    code, out, err = run(capsys, "upgrade", str(project))
    assert (code, err) == (5, "")
    assert f"{mnt.LEFT:<9} {first.skills}/{SKILL}/" in out
    assert "# ours now" in out, "the diff of the edited file is missing"
    assert (project / first.skills / SKILL / "SKILL.md").read_bytes() == b"# ours now\n"
    assert f"{mnt.UPDATED:<9} {second.skills}/{SKILL}/SKILL.md" in out
    assert closing_line(out).endswith(
        f". 1 skill(s) refreshed, {len(sk.harnesses()) - 1} left for review.")


def test_the_upgrade_adds_no_skill_and_puts_none_back(project, newer_release, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    first, second = sk.harnesses()[:2]
    sk.add_all(project, SKILL, targets=(first.name,), today=INSTALL_DAY)
    sk.add_all(project, OTHER, targets=(first.name,), today=INSTALL_DAY)
    shutil.rmtree(project / first.skills / OTHER)
    newer_release(skill_edit=edit_skill)
    code, out, err = run(capsys, "upgrade", str(project))
    # The skill whose folder went is an ABSENT row, and every LEFT or
    # ABSENT row is left for review, as it is in the seed half: exit 5.
    assert (code, err) == (5, "")
    # The one it has comes up; the one whose folder went stays gone and
    # reads as such; the other agent, with no record, gets no block and
    # no folder; no catalog skill is added anywhere.
    assert f"{mnt.UPDATED:<9} {first.skills}/{SKILL}/SKILL.md" in out
    assert f"{mnt.ABSENT:<9} {first.skills}/{OTHER}/" in out
    assert not (project / first.skills / OTHER).exists()
    assert f"Skills for {first.label}" in out
    assert f"Skills for {second.label}" not in out
    assert not (project / second.skills).exists()
    for name in sk.catalog_names():
        if name not in (SKILL, OTHER):
            assert not (project / first.skills / name).exists()
            assert name not in out
    assert closing_line(out).endswith(". 1 skill(s) refreshed, 1 left for review.")


def test_a_project_with_no_skill_says_so_in_the_upgrade_report(project, capsys):
    mnt.mount(project, today=MOUNT_DAY)
    code, out, _ = run(capsys, "upgrade", str(project))
    assert code == 0
    assert report.SKILL_NONE_TAIL in out
    assert "Skills for" not in out
    assert closing_line(out).endswith(". 0 skill(s) refreshed, 0 left for review.")


def test_a_dry_run_shows_both_halves_and_writes_nothing(project, newer_release, capsys):
    ready(project)
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    before = tree(project)
    code, out, err = run(capsys, "upgrade", str(project), "--dry-run")
    assert tree(project) == before
    assert (code, err) == (0, "")
    assert out.startswith("Would check")
    assert f"{mnt.UPDATED:<9} docs/spec.md" in out
    for harness in sk.harnesses():
        assert f"{mnt.UPDATED:<9} {harness.skills}/{SKILL}/SKILL.md" in out
    assert report.DRY_RUN_TAIL in out
    # The run returns the same code and prints the same rows.
    rehearsed = [line for line in out.splitlines() if line.startswith(mnt.UPDATED)]
    code, out, _ = run(capsys, "upgrade", str(project))
    assert code == 0
    assert [line for line in out.splitlines() if line.startswith(mnt.UPDATED)] == rehearsed


def test_a_skills_half_that_fails_after_the_seed_is_written_is_reported(
    project, newer_release, capsys, monkeypatch,
):
    ready(project)
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    sentence = stop_the_second_write(monkeypatch)
    code, out, err = run(capsys, "upgrade", str(project))
    assert (code, err) == (5, "")
    assert f"{mnt.UPDATED:<9} docs/spec.md" in out
    assert report.UPDATE_SKILLS_FAILED.format(error=sentence) in out
    assert "Skills for" not in out


def test_upgrade_all_reports_each_listed_project_and_names_the_skipped(
    tmp_path, newer_release, capsys,
):
    alpha = ready(_make_project(tmp_path / "alpha")).resolve()
    beta = ready(_make_project(tmp_path / "beta")).resolve()
    first = sk.harnesses()[0]
    (beta / first.skills / SKILL / "SKILL.md").write_bytes(b"# ours now\n")
    gamma = _make_project(tmp_path / "gamma").resolve()
    delta = _make_project(tmp_path / "delta").resolve()
    for root in (alpha, beta, gamma, delta):
        ro.record_seen(root)
    shutil.rmtree(delta)
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)

    # The rehearsal first: both reports, the skips, nothing written. The
    # listing drops the folder that is gone on this very read, as
    # `radialrig projects` does, so the run after it names one skip.
    before = {root: tree(root) for root in (alpha, beta, gamma)}
    code, out, err = run(capsys, "upgrade", "--all", "--dry-run")
    assert (code, err) == (5, "")
    assert {root: tree(root) for root in (alpha, beta, gamma)} == before
    assert "Would check 2 listed project(s); 2 skipped." in out
    assert f"Skipped {gamma}: not mounted." in out
    assert f"Skipped {delta}: no project stands at this path." in out
    assert report.DRY_RUN_TAIL in out
    assert out.count(report.DRY_RUN_TAIL) == 1

    code, out, err = run(capsys, "upgrade", "--all")
    assert (code, err) == (5, "")
    assert "Checked 2 listed project(s); 1 skipped." in out
    assert f"Skipped {gamma}: not mounted." in out
    assert str(delta) not in out
    # One report per project, headed by its folder, in listing order.
    assert out.index(f"seed check for {alpha}") < out.index(f"seed check for {beta}")
    assert b"A sentence the newer release added." in (alpha / "docs" / "spec.md").read_bytes()
    assert b"A sentence the newer release added." in (beta / "docs" / "spec.md").read_bytes()
    assert (beta / first.skills / SKILL / "SKILL.md").read_bytes() == b"# ours now\n"
    assert "# ours now" in out
    assert out.count(cli.ENGINE_HALF) == 1
    assert out.splitlines()[-1] == report.engine_line()
    assert tree(gamma) == before[gamma]
    # The listing dropped the folder that is gone, as it always does.
    assert [state.root for state in ro.listing()] == [str(alpha), str(beta), str(gamma)]


def test_upgrade_all_exits_with_the_worst_project_code(tmp_path, newer_release, capsys):
    code, out, err = run(capsys, "upgrade", "--all")
    assert (code, err) == (4, "")
    assert report.PROJECTS_NONE in out

    alpha = ready(_make_project(tmp_path / "alpha")).resolve()
    ro.record_seen(alpha)
    code, out, _ = run(capsys, "upgrade", "--all")
    assert code == 0
    assert "Checked 1 listed project(s); 0 skipped." in out

    beta = ready(_make_project(tmp_path / "beta")).resolve()
    ro.record_seen(beta)
    (beta / mnt.MANIFEST_REL).write_bytes(b"{ not json\n")
    code, out, _ = run(capsys, "upgrade", "--all")
    assert code == 1
    assert f"Skipped {beta}: " in out
    assert "Checked 1 listed project(s); 1 skipped." in out


def test_upgrade_all_takes_no_path(project, capsys, monkeypatch):
    """Any path beside --all is the one usage error, "." included: the
    listing is the command's own argument. A path left out still means
    the current folder."""
    for argv in (
        ["upgrade", str(project), "--all"],
        ["upgrade", ".", "--all"],
        ["upgrade", "--all", "."],
    ):
        with pytest.raises(SystemExit) as caught:
            cli.main(argv)
        assert caught.value.code == 2, argv
        assert "not allowed with a path" in capsys.readouterr().err, argv
    assert not mnt.is_mounted(project)
    monkeypatch.chdir(project)
    assert cli.main(["upgrade"]) == report.NOOP
    assert f"Not mounted: {project.resolve()}" in capsys.readouterr().out


def test_the_help_says_the_upgrade_refreshes_skills_and_what_add_all_does(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["upgrade", "--help"])
    assert caught.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "--all" in text
    assert "skill" in text.lower()
    assert "never adds a skill" in text
    epilog = " ".join(cli.UPGRADE_EPILOG.split())
    assert "every skill this project already has" in epilog
    assert "never adds a skill" in epilog

    with pytest.raises(SystemExit) as caught:
        cli.main(["skill", "add", "--help"])
    assert caught.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "left as it is" not in text
    assert "brings" in text and "packaged version" in text
    usage = " ".join(cli.USAGE.split())
    assert "radialrig upgrade --all" in usage
    assert "left as it is" not in usage


def test_the_skip_words_are_keyed_by_the_update_modules_own_codes():
    from radialrig import update

    assert set(report.UPDATE_SKIP_WORDS) == {update.SKIP_GONE, update.SKIP_NOT_MOUNTED}


# --- the two documents (PLAN-0021 phase 1, prescriptions 5 and 6) ------

REPO = Path(__file__).resolve().parents[3]


def test_the_readme_upgrading_section_names_the_skills_half_and_both_presses():
    from radialrig import manage_web

    text = (REPO / "README.md").read_text(encoding="utf-8")
    section = text.split("## Upgrading", 1)[1].split("\n## ", 1)[0]
    assert "skill" in section.lower()
    assert "already has" in section
    assert "never adds a skill" in section
    assert f'"{manage_web.UPDATE_LABEL}"' in section
    assert f'"{manage_web.UPDATE_ALL_LABEL}"' in section
    assert "upgrade --all" in section
    # An edited skill is left for review, like an edited seeded file.
    assert "edited skill" in section


def test_the_readme_agrees_with_the_amended_ruling():
    """D-36 rule 2 as amended: the upgrade is the one crossing between
    the kits. The README's command row and its sentence about the two
    kits say so (PLAN-0021 phase 1 review, items 6 and 7). The bug
    index's half of this check reads the bug notes, which stay private,
    so it runs in the workshop-only module (PLAN-0023 phase 1)."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines()
               if line.startswith("| `radialrig upgrade [PATH]`"))
    assert "skill" in row
    assert "neither verb family touches the other's" not in text
    kits = next(p for p in text.split("\n\n") if "The docs kit and the skills kit" in p)
    assert "`radialrig upgrade`" in kits and "already has" in kits


def test_skill_add_answers_its_help(capsys):
    """The command half of the BUG-0020 check; the note's half runs in
    the workshop-only module. A usage error also exits, with code 2, so
    the test asks for code 0 and the usage line."""
    with pytest.raises(SystemExit) as exited:
        cli.main(["skill", "add", "--help"])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: radialrig skill add") and "--all" in out, out
