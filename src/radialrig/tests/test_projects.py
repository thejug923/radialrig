"""Tests for `radialrig projects` — PLAN-0008 Phase 3.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_projects.py -q

Two oracles run here. The first is prescription 2: the listing reports
what it found in the projects' own files, not what the roster remembered,
so an entry the user removed inside the client reads as a mismatch and
never as connected, and the two surfaces say the same thing about the
same project. The second is prescription 4: the verb rewrites the roster
only to drop the gone, and a roster this build cannot read or cannot
write reads as empty and moves neither the report nor the exit code —
which is what makes exit 0 honest.

The machine-readable line is pinned byte for byte, tab characters
included, because Phase 5 parses it.

Nothing here may reach this machine's own profile. `APPDATA` points
inside tmp_path for every test in the suite (conftest.py), and the
fixtures below narrow it to one fake client profile of this file's own,
so the roster and the client's settings file both stand under tmp_path.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from radialrig import cli
from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import report
from radialrig import roster as ro
from radialrig import skills as sk
from radialrig.paths import ROOT_ENV
from radialrig.tests.test_connect import (  # noqa: F401 - helpers, by name
    CONFIG_HOSTS,
    STORE_VAR,
    command,
    fake_client,
    roaming_config,
    run,
)
from radialrig.tests.test_mount import _make_project

CLIENT = cn.DEFAULT_CLIENT
LABEL = cn.CLIENTS[CLIENT].label
PREFIX = cn.CLIENTS[CLIENT].key_prefix

# One packaged skill, restated rather than read from the catalog this
# test checks the report against.
SKILL = "session-handoff"

BLOCK_LINE = re.compile(r"^[a-z]+ {2,}\S")

# A project root no Windows console codepage can spell. The roster stores
# whatever the user's directory is called, so this is the ordinary case
# for anyone whose paths are not Latin-1, not an exotic one.
WIDE_NAME = "项目-测试"


# --- fixtures ---------------------------------------------------------

@pytest.fixture()
def env(tmp_path, monkeypatch) -> dict:
    """One fake client profile, in os.environ where the verbs read it.

    The command surface passes no mapping of its own, so the injection
    has to be the environment itself. The mapping carries both the
    client and the roster, under this platform's own variables, which is
    where a real machine keeps them too.
    """
    mapping = fake_client(tmp_path)
    for name, value in mapping.items():
        monkeypatch.setenv(name, value)
    return mapping


@pytest.fixture()
def client_here(env) -> Path:
    """The fake client's settings file, in the shape this machine's is."""
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["no-servers"])
    return config


def roster_file(env: dict) -> Path:
    return Path(env[STORE_VAR]) / "radialrig" / "roster.json"


def block(text: str) -> list[str]:
    return [line for line in text.splitlines() if BLOCK_LINE.match(line)]


def record_of(project: Path) -> dict:
    return json.loads(cn.record_path(project).read_text(encoding="utf-8"))


def settings_of(config: Path) -> dict:
    return json.loads(config.read_text(encoding="utf-8"))


def drop_entry(config: Path, key: str) -> None:
    """What a user does inside the client: remove the entry, keep the file."""
    body = settings_of(config)
    del body["mcpServers"][key]
    config.write_text(json.dumps(body, indent=2), encoding="utf-8")


def registered(capsys, project: Path, *, skill: bool = False) -> Path:
    """A mounted, registered project, built by the real verbs."""
    assert run(capsys, "mount", str(project))[0] == 0
    assert run(capsys, "connect", CLIENT, str(project))[0] == 0
    if skill:
        assert run(capsys, "skill", "add", SKILL, str(project))[0] == 0
    return project


def out_of_process(profile: Path, *argv: str) -> subprocess.CompletedProcess:
    """One real `radialrig` run, its streams captured as bytes.

    capsys hands back text off an in-process stream, which is exactly the
    layer these two tests are here to check. So the verb runs as its own
    process, against a pipe, with the profile variables pointed at this
    test's own directory and the interpreter's encoding overrides
    cleared — whatever this console is, that is what the run meets.
    """
    env = dict(os.environ)
    env["APPDATA"] = str(profile)
    env["LOCALAPPDATA"] = str(profile / "local")
    env[STORE_VAR] = str(profile)
    for name in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO"):
        env.pop(name, None)
    return subprocess.run(
        [sys.executable, "-m", "radialrig.cli", *argv],
        capture_output=True, env=env,
    )


def wide_roster(tmp_path: Path) -> tuple[Path, Path]:
    """A profile whose roster names one project outside ASCII."""
    profile = tmp_path / "profile"
    root = tmp_path / WIDE_NAME
    root.mkdir()
    path = profile / "radialrig" / "roster.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": 1,
        "projects": [{"root": str(root.resolve()), "seen": "2026-08-19"}],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return profile, root


def porcelain_line(project: Path, mounted: str, connected: str, skills: str) -> str:
    """One expected line, assembled from real tab characters."""
    return (
        f"root={project.resolve()}\tmounted={mounted}\t"
        f"connected={connected}\tskills={skills}\n"
    )


# --- the human report -------------------------------------------------

def test_projects_lists_verified_state_in_closed_verbs(
    tmp_path, env, client_here, command, capsys,
):
    project = registered(capsys, _make_project(tmp_path / "alpha"), skill=True)

    code, out, err = run(capsys, "projects")

    assert code == 0
    assert err == ""
    assert out.splitlines()[0] == f"{report.PROJECTS_HEAD} - 1 listed."
    rows = block(out)
    assert len(rows) == 1
    assert rows[0].split()[0] == mnt.CURRENT
    assert str(project.resolve()) in rows[0]
    assert rows[0].endswith(
        f"mounted; registered with {LABEL}; skills: {SKILL}")
    for line in rows:
        assert line.split()[0] in mnt.VERBS, line
    assert report.PROJECTS_NONE not in out
    assert report.PROJECTS_MISMATCH_TAIL not in out


def test_a_skill_one_agent_has_reads_per_agent_on_the_row(
    tmp_path, env, client_here, command, capsys,
):
    """PLAN-0015 phase 2 prescription 5, on the command line: the row
    says one skills facet where the agents agree and one per agent,
    labelled from the map, where they differ; the machine line keeps
    its one field, the union."""
    project = registered(capsys, _make_project(tmp_path / "alpha"), skill=True)
    first, second = list(sk.HARNESSES.values())[:2]
    assert run(capsys, "skill", "remove", SKILL, str(project),
               "--harness", second.name)[0] == 0

    code, out, _ = run(capsys, "projects")
    assert code == 0
    row = block(out)[0]
    assert row.endswith(
        f"mounted; registered with {LABEL}; {first.label} skills: {SKILL}; "
        f"{second.label} skills: none")
    assert "harness" not in out.lower()
    code, porcelain, _ = run(capsys, "projects", "--porcelain")
    assert code == 0
    assert porcelain == porcelain_line(project, "yes", CLIENT, SKILL)

    assert run(capsys, "skill", "add", SKILL, str(project),
               "--harness", second.name)[0] == 0
    code, out, _ = run(capsys, "projects")
    assert block(out)[0].endswith(f"registered with {LABEL}; skills: {SKILL}")


def test_the_mismatch_row_carries_all_three_facets(
    tmp_path, env, client_here, command, capsys,
):
    project = registered(capsys, _make_project(tmp_path / "beta"), skill=True)
    drop_entry(client_here, record_of(project)["key"])

    code, out, _ = run(capsys, "projects")

    assert code == 0
    row = block(out)[0]
    assert row.split()[0] == mnt.LEFT
    # The clause names the client the record claims (PLAN-0014 phase 3),
    # filled from the table; the porcelain line below keeps its word.
    assert row.endswith(
        f"mounted; {ro.MISMATCH_CLAUSE.format(label=LABEL)}; skills: {SKILL}")
    assert f"{LABEL}'s settings no longer carry" in row
    assert "{label}" not in out
    # The command line's tail keeps its placeholders, as it always has.
    assert report.PROJECTS_MISMATCH_TAIL in out
    assert "<client>" in report.PROJECTS_MISMATCH_TAIL

    code, porcelain, _ = run(capsys, "projects", "--porcelain")

    # The two surfaces agree, facet for facet.
    assert code == 0
    assert porcelain == porcelain_line(
        project, "yes", report.PROJECTS_MISMATCH, SKILL)


def test_a_client_removed_entry_reads_as_mismatch_never_connected(
    tmp_path, env, client_here, command, capsys,
):
    project = registered(capsys, _make_project(tmp_path / "gamma"))
    key = record_of(project)["key"]
    assert key in settings_of(client_here)["mcpServers"]

    _, standing, _ = run(capsys, "projects")
    assert f"registered with {LABEL}" in standing
    _, porcelain, _ = run(capsys, "projects", "--porcelain")
    assert f"connected={CLIENT}" in porcelain

    drop_entry(client_here, key)

    code, out, _ = run(capsys, "projects")
    assert code == 0
    row = block(out)[0]
    assert row.split()[0] == mnt.LEFT
    assert ro.MISMATCH_CLAUSE.format(label=LABEL) in row
    assert report.PROJECTS_MISMATCH_TAIL in out
    assert f"registered with {LABEL}" not in out

    code, porcelain, _ = run(capsys, "projects", "--porcelain")
    assert code == 0
    assert f"connected={report.PROJECTS_MISMATCH}" in porcelain
    assert f"connected={CLIENT}" not in porcelain


def test_a_deleted_project_is_reported_gone_and_dropped(tmp_path, env, capsys):
    kept = _make_project(tmp_path / "kept")
    gone = _make_project(tmp_path / "gone")
    assert run(capsys, "mount", str(kept))[0] == 0
    assert run(capsys, "mount", str(gone))[0] == 0
    stored = str(gone.resolve())
    shutil.rmtree(gone)

    code, out, _ = run(capsys, "projects")

    assert code == 0
    assert out.splitlines()[0] == f"{report.PROJECTS_HEAD} - 1 listed, 1 dropped."
    rows = block(out)
    assert [row.split()[0] for row in rows] == [mnt.CURRENT, mnt.REMOVED]
    assert rows[1].endswith(ro.ROSTER_REASONS[ro.ROSTER_GONE])
    assert stored not in roster_file(env).read_text(encoding="utf-8")

    code, porcelain, _ = run(capsys, "projects", "--porcelain")
    assert code == 0
    assert porcelain == porcelain_line(kept, "yes", "no", "none")


def test_a_deleted_roster_costs_only_the_listing(tmp_path, env, capsys):
    project = _make_project(tmp_path / "delta")
    assert run(capsys, "mount", str(project))[0] == 0
    assert roster_file(env).is_file()

    roster_file(env).unlink()

    code, out, err = run(capsys, "projects")
    assert code == 0
    assert err == ""
    assert out.splitlines()[0] == f"{report.PROJECTS_HEAD} - 0 listed."
    assert report.PROJECTS_NONE in out
    assert block(out) == []

    assert run(capsys, "unmount", str(project))[0] == 0

    code, out, _ = run(capsys, "projects")
    assert code == 0
    assert str(project.resolve()) in out
    assert report.PROJECTS_NONE not in out


def test_an_unreadable_or_unwritable_roster_reads_as_empty(
    tmp_path, env, capsys, monkeypatch,
):
    absent = run(capsys, "projects")
    assert absent[0] == 0

    path = roster_file(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    garbage = b"{ not a roster at all\n"
    path.write_bytes(garbage)

    assert run(capsys, "projects") == absent
    assert path.read_bytes() == garbage
    code, porcelain, _ = run(capsys, "projects", "--porcelain")
    assert (code, porcelain) == (0, "")
    assert path.read_bytes() == garbage

    # Readable but unwritable: the drop is attempted, the swap refuses,
    # and neither the report nor the exit code moves.
    path.unlink()
    gone = _make_project(tmp_path / "vanished")
    assert run(capsys, "mount", str(gone))[0] == 0
    shutil.rmtree(gone)
    before = path.read_bytes()

    def refuse(src, dst):
        raise OSError("the swap failed")

    monkeypatch.setattr(ro.os, "replace", refuse)

    code, out, _ = run(capsys, "projects")
    assert code == 0
    assert out.splitlines()[0] == f"{report.PROJECTS_HEAD} - 0 listed, 1 dropped."
    assert path.read_bytes() == before
    assert list(path.parent.glob("*.tmp")) == []


def test_orphans_are_named(tmp_path, env, client_here, command, capsys):
    code, out, _ = run(capsys, "projects")
    assert code == 0
    assert "orphan" not in out

    body = settings_of(client_here)
    body["mcpServers"] = {
        f"{PREFIX}old": {"command": "x", "args": [],
                         "env": {ROOT_ENV: str(tmp_path / "vanished")}},
        f"{PREFIX}bare": {"command": "x", "args": []},
    }
    client_here.write_text(json.dumps(body, indent=2), encoding="utf-8")

    code, out, _ = run(capsys, "projects")

    assert code == 0
    assert out.splitlines()[0] == (
        f"{report.PROJECTS_HEAD} - 0 listed, 1 orphan entry.")
    rows = block(out)
    assert [row.split()[0] for row in rows] == [mnt.LEFT, mnt.LEFT]
    assert rows[0].split()[1] == f"{PREFIX}bare"
    assert rows[0].endswith(ro.ROSTER_REASONS[ro.ROSTER_UNPINNED])
    assert rows[1].split()[1] == f"{PREFIX}old"
    assert rows[1].endswith(
        f"radialrig unconnect {CLIENT} --orphan {PREFIX}old")

    # An orphan is not a project: the machine-readable line never carries
    # one, because it has no project root to put first.
    code, porcelain, _ = run(capsys, "projects", "--porcelain")
    assert (code, porcelain) == (0, "")


# --- the machine-readable line ----------------------------------------

def test_porcelain_is_pinned_byte_for_byte(
    tmp_path, env, client_here, command, capsys,
):
    """The Phase 5 contract: three projects, three states, exact bytes."""
    alpha = registered(capsys, _make_project(tmp_path / "alpha"), skill=True)

    beta = registered(capsys, _make_project(tmp_path / "beta"))
    drop_entry(client_here, record_of(beta)["key"])

    gamma = _make_project(tmp_path / "gamma")
    assert run(capsys, "unmount", str(gamma))[0] == 4

    code, out, err = run(capsys, "projects", "--porcelain")

    assert code == 0
    assert err == ""
    assert out == (
        porcelain_line(alpha, "yes", CLIENT, SKILL)
        + porcelain_line(beta, "yes", "mismatch", "none")
        + porcelain_line(gamma, "no", "no", "none")
    )
    assert out.count("\t") == 9
    assert out.count("\n") == 3

    # An empty roster prints zero bytes, never one bare newline: a reader
    # counting lines would read that newline as a project.
    roster_file(env).unlink()

    code, out, err = run(capsys, "projects", "--porcelain")
    assert code == 0
    assert out == ""
    assert err == ""


def test_the_machine_line_leaves_as_utf8_bytes_ending_in_one_newline(tmp_path):
    """The Phase 5 byte contract, off a real process rather than capsys.

    An in-process stream hides the two things a parser trips over: the
    console's own encoding, which cannot spell every project root, and
    the translation of "\\n" into two bytes on this operating system.
    Phase 5 splits this stream on the newline byte and decodes it as
    UTF-8, so both are pinned here as bytes.
    """
    profile, root = wide_roster(tmp_path)

    done = out_of_process(profile, "projects", "--porcelain")

    assert done.returncode == 0, done.stderr
    assert done.stderr == b""
    assert done.stdout == (
        f"root={root.resolve()}\tmounted=no\tconnected=no\tskills=none\n"
    ).encode("utf-8")
    assert b"\r" not in done.stdout
    assert done.stdout.count(b"\t") == 3
    assert done.stdout.endswith(b"\n")
    assert done.stdout.count(b"\n") == 1

    # An empty roster prints zero bytes here too, not one bare newline.
    (profile / "radialrig" / "roster.json").unlink()

    empty = out_of_process(profile, "projects", "--porcelain")
    assert empty.returncode == 0, empty.stderr
    assert empty.stdout == b""


def test_the_human_report_answers_zero_whatever_the_console_can_spell(tmp_path):
    """A root the console cannot encode costs the spelling, not the verb.

    `projects` always exits 0, and a stream that raised on the one
    character it could not print would make that promise false on the
    machines least able to check it.
    """
    profile, root = wide_roster(tmp_path)

    done = out_of_process(profile, "projects")

    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith(report.PROJECTS_HEAD.encode("utf-8"))
    assert b"1 listed" in done.stdout


# --- the surface contract ---------------------------------------------

def test_the_wire_word_is_spelled_once_on_each_side_of_the_boundary():
    """The report module never imports the roster, so the word is typed
    twice. Tying them here is what keeps the two copies one word."""
    assert report.PROJECTS_MISMATCH == ro.CONNECTED_MISMATCH


def test_projects_has_no_dry_run_flag(capsys):
    """The verb rewrites its own file only to drop the gone, so there is
    nothing for a rehearsal to withhold."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["projects", "--dry-run"])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        cli.main(["projects", str(Path.cwd())])
    assert exc.value.code == 2


# --- the word checks --------------------------------------------------

def test_no_projects_output_implies_who_typed_a_change(
    tmp_path, env, client_here, command, capsys,
):
    from radialrig.tests.test_cli import FORBIDDEN_WORD

    registered(capsys, _make_project(tmp_path / "alpha"), skill=True)
    beta = registered(capsys, _make_project(tmp_path / "beta"))
    drop_entry(client_here, record_of(beta)["key"])
    bare = _make_project(tmp_path / "bare")
    assert run(capsys, "unconnect", CLIENT, str(bare))[0] == 4
    gone = _make_project(tmp_path / "gone")
    assert run(capsys, "mount", str(gone))[0] == 0
    shutil.rmtree(gone)

    body = settings_of(client_here)
    body["mcpServers"][f"{PREFIX}old"] = {
        "command": "x", "args": [], "env": {ROOT_ENV: str(tmp_path / "vanished")}}
    body["mcpServers"][f"{PREFIX}bare"] = {"command": "x", "args": []}
    client_here.write_text(json.dumps(body, indent=2), encoding="utf-8")

    collected = [run(capsys, "projects"), run(capsys, "projects", "--porcelain")]
    roster_file(env).unlink()
    collected.append(run(capsys, "projects"))
    collected.append(run(capsys, "projects", "--porcelain"))

    text = "\n".join(out + err for _, out, err in collected).lower()
    hit = FORBIDDEN_WORD.search(text)
    assert hit is None, f"a report uses '{hit.group(0) if hit else ''}'"
    # The narrowing is not an amnesty: the substring is still there to be
    # found, inside a skill's name and nowhere else.
    assert "hand" in text


def test_the_projects_constants_carry_no_banned_vocabulary():
    """The constants scan test_setup_web.py runs, over this phase's own.

    The roster module's table is checked in test_roster.py; what is
    checked here is every sentence the report builders wrap around it.
    """
    from radialrig.tests.test_cli import FORBIDDEN_WORD

    names = [name for name in dir(report) if name.startswith("PROJECTS_")]
    assert names, "the projects report carries no constants"
    for name in names:
        text = getattr(report, name)
        assert isinstance(text, str), name
        assert not FORBIDDEN_WORD.search(text), name
        lowered = text.lower()
        for banned in ("magic", "just works", "seamless"):
            assert banned not in lowered, name
        assert not any(
            word.strip('".,:') == "safe" for word in lowered.split()
        ), name
