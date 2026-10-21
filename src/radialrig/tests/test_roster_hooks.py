"""Tests for the roster side-write — PLAN-0008 Phase 3.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_roster_hooks.py -q

The oracle here is prescription 3: the four verbs keep the roster as a
side entry, on their real runs and on their already-done exits alike, and
the entry costs the verb nothing. So every test below asserts two things
at once — what the roster gained, and that the verb's exit code, its
report and the bytes it wrote are the ones it would have produced with no
roster at all.

The page surface is held to the same line (D-34): a press on the setup
page is the verb's engine call and the same side entry, so the roster
cannot tell the two surfaces apart.

Nothing here may reach this machine's own profile. `APPDATA` points
inside tmp_path for every test in the suite (conftest.py), and the
fixtures below narrow it to one fake client profile of this file's own,
so the roster path and the client's settings file are both under
tmp_path.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from radialrig import cli
from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import roster as ro
from radialrig import setup_web
from radialrig.tests.test_connect import (  # noqa: F401 - helpers, by name
    CONFIG_HOSTS,
    STORE_VAR,
    clear_store_env,
    command,
    fake_client,
    roaming_config,
    run,
)
from radialrig.tests.test_mount import _make_project

CLIENT = cn.DEFAULT_CLIENT
SCOPE = "page-scope"


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


def body_of(env: dict) -> dict:
    return json.loads(roster_file(env).read_text(encoding="utf-8"))


def roots(env: dict) -> list[str]:
    path = roster_file(env)
    if not path.is_file():
        return []
    return [entry["root"] for entry in body_of(env)["projects"]]


def dates(env: dict) -> list[str]:
    path = roster_file(env)
    if not path.is_file():
        return []
    return [entry["seen"] for entry in body_of(env)["projects"]]


def clear(env: dict) -> None:
    """An empty roster before a verb, so the verb's own write is proven."""
    roster_file(env).unlink(missing_ok=True)


def spanning(start: dt.date) -> set[str]:
    """The dates a run begun on `start` may honestly have stored.

    A run that starts a moment before midnight ends after it, and the
    entry carries whichever day the write landed on. Both are correct,
    so both are accepted rather than pinning the suite to the hours it
    usually runs in.
    """
    return {start.isoformat(), dt.date.today().isoformat()}


# --- the four verbs, on both kinds of exit -----------------------------

def test_each_verb_real_run_writes_the_roster(tmp_path, env, client_here,
                                              command, capsys):
    project = _make_project(tmp_path / "adopted")
    listed = [str(project.resolve())]

    for argv in (
        ("mount", str(project)),
        ("connect", CLIENT, str(project)),
        ("unconnect", CLIENT, str(project)),
        ("unmount", str(project)),
    ):
        clear(env)
        start = dt.date.today()
        code, out, err = run(capsys, *argv)
        assert code == 0, (argv, out, err)
        assert roots(env) == listed, argv
        assert set(dates(env)) <= spanning(start), argv
        assert len(dates(env)) == 1, argv


def test_each_noop_exit_adopts_the_project(tmp_path, env, client_here,
                                           command, capsys):
    standing = _make_project(tmp_path / "standing")
    assert run(capsys, "mount", str(standing))[0] == 0
    assert run(capsys, "connect", CLIENT, str(standing))[0] == 0
    untouched = _make_project(tmp_path / "untouched")

    for argv, root in (
        (("mount", str(standing)), standing),
        (("connect", CLIENT, str(standing)), standing),
        (("unmount", str(untouched)), untouched),
        (("unconnect", CLIENT, str(untouched)), untouched),
    ):
        clear(env)
        start = dt.date.today()
        code, out, err = run(capsys, *argv)
        assert code == 4, (argv, out, err)
        assert roots(env) == [str(root.resolve())], argv
        assert set(dates(env)) <= spanning(start), argv
        assert len(dates(env)) == 1, argv


def test_dry_runs_never_touch_it(tmp_path, env, client_here, command, capsys):
    project = _make_project(tmp_path / "rehearsed")

    # Nothing done yet: mount and connect rehearse their real runs, the
    # reversals rehearse their already-done exits.
    for argv in (
        ("mount", str(project), "--dry-run"),
        ("connect", CLIENT, str(project), "--dry-run"),
        ("unmount", str(project), "--dry-run"),
        ("unconnect", CLIENT, str(project), "--dry-run"),
    ):
        run(capsys, *argv)
        # The roster alone: its folder also holds the activity log, which
        # every run writes, rehearsal or not (PLAN-0018 phase 2).
        assert not roster_file(env).exists(), argv

    assert run(capsys, "mount", str(project))[0] == 0
    assert run(capsys, "connect", CLIENT, str(project))[0] == 0
    clear(env)

    # Done: the same four rehearsals now meet the other four seams.
    for argv in (
        ("mount", str(project), "--dry-run"),
        ("connect", CLIENT, str(project), "--dry-run"),
        ("unconnect", CLIENT, str(project), "--dry-run"),
        ("unmount", str(project), "--dry-run"),
    ):
        run(capsys, *argv)
        assert not roster_file(env).exists(), argv


def test_refusals_and_errors_adopt_nothing(tmp_path, env, client_here,
                                           command, capsys):
    conflicted = _make_project(tmp_path / "conflicted")
    (conflicted / "docs").mkdir()
    (conflicted / "docs" / "spec.md").write_bytes(b"ours\n")

    code, out, _ = run(capsys, "mount", str(conflicted))
    assert code == 3, out
    assert not roster_file(env).exists()

    code, _, err = run(capsys, "connect", "no-such-client", str(conflicted))
    assert code == 1, err
    assert not roster_file(env).exists()

    code, _, err = run(capsys, "unconnect", "no-such-client", str(conflicted))
    assert code == 1, err
    assert not roster_file(env).exists()


# --- the entry costs the verb nothing ---------------------------------

def test_a_roster_failure_never_changes_a_verb(tmp_path, env, client_here,
                                               command, capsys):
    """A roster this build cannot write leaves the verb byte-identical.

    The verb is run once with the roster reachable, then reversed to the
    state it started from, so each wrecked run is the same run again and
    the comparison is exact rather than approximate. Three failures: the
    atomic swap refused, the writer itself raising, and a file standing
    where the roster's own directory goes.
    """
    project = _make_project(tmp_path / "unmoved")
    pristine = client_here.read_bytes()

    clean = run(capsys, "connect", CLIENT, str(project))
    assert clean[0] == 0, clean
    assert run(capsys, "unconnect", CLIENT, str(project))[0] == 0
    assert client_here.read_bytes() == pristine
    clear(env)

    def refuse(src, dst):
        raise OSError("the swap failed")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ro.os, "replace", refuse)
        assert run(capsys, "connect", CLIENT, str(project)) == clean
    assert not roster_file(env).exists()
    assert list(roster_file(env).parent.glob("*.tmp")) == []
    assert run(capsys, "unconnect", CLIENT, str(project))[0] == 0
    assert client_here.read_bytes() == pristine
    clear(env)

    def explode(body, *, env=None):
        raise RuntimeError("the writer itself failed")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ro, "_write", explode)
        assert run(capsys, "connect", CLIENT, str(project)) == clean
    assert not roster_file(env).exists()
    assert run(capsys, "unconnect", CLIENT, str(project))[0] == 0
    assert client_here.read_bytes() == pristine
    clear(env)

    blocked = roster_file(env).parent
    if blocked.is_dir():
        # With its contents: the folder holds the activity log too
        # (PLAN-0018 phase 2).
        shutil.rmtree(blocked)
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_bytes(b"a file where the roster directory goes\n")

    assert run(capsys, "connect", CLIENT, str(project)) == clean
    assert blocked.read_bytes() == b"a file where the roster directory goes\n"


def test_no_store_variable_means_no_roster_and_no_error(
    tmp_path, monkeypatch, capsys,
):
    clear_store_env(monkeypatch)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    project = _make_project(tmp_path / "unrostered")

    assert ro.roster_path() is None
    assert run(capsys, "mount", str(project))[0] == 0
    assert run(capsys, "mount", str(project))[0] == 4
    assert run(capsys, "unmount", str(project))[0] == 0
    assert run(capsys, "unmount", str(project))[0] == 4
    assert list(tmp_path.glob("**/roster.json")) == []


def test_a_verb_run_creates_no_file_outside_tmp(tmp_path, capsys):
    """The suite-wide isolation, stated where the hooks can break it.

    Before this phase no verb wrote outside the project it was given.
    Now `unmount` keeps the roster too, and the only path it could reach
    is the one `APPDATA` names — so this pins that the name is tmp_path's
    and that the developer's own roster is untouched by a test run.
    """
    real = Path.home() / "AppData" / "Roaming" / "radialrig" / "roster.json"
    before = real.read_bytes() if real.is_file() else None
    project = _make_project(tmp_path / "isolated")

    path = ro.roster_path()
    assert path is not None
    assert tmp_path in path.parents

    assert run(capsys, "unmount", str(project))[0] == 4

    assert path.is_file()
    assert (real.read_bytes() if real.is_file() else None) == before


# --- the page presses the same hook ------------------------------------

def test_the_page_press_writes_the_same_entry(tmp_path, env, client_here,
                                              command):
    project = _make_project(tmp_path / "pressed")
    session = setup_web.Session(project, SCOPE, mnt.DEFAULT_OWNER)
    listed = [str(project.resolve())]

    # The page's own dry run: every GET state writes nothing.
    setup_web.compute_state(session)
    assert not roster_file(env).exists()

    start = dt.date.today()
    state = setup_web.perform_mount(session)
    assert state.kind == setup_web.MOUNTED
    assert roots(env) == listed
    assert set(dates(env)) <= spanning(start)
    assert len(dates(env)) == 1

    # The connect seam alone, on the already-current outcome.
    clear(env)
    dispositions, client, failed, stopped = setup_web._connect_checked(
        session, cn.client_for(CLIENT))
    assert failed == ""
    assert stopped == setup_web.report.OK
    assert client is not None
    assert roots(env) == listed

    # The press that meets an already-mounted project.
    clear(env)
    setup_web.perform_mount(session)
    assert roots(env) == listed
