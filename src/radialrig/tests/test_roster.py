"""Tests for the projects roster — PLAN-0008 Phase 3.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_roster.py -q

Two oracles run here. The first is constraint 5: a roster file this build
cannot read is never overwritten, by any write path, so the only file the
module ever creates is one that was absent. The second is D-07: the
listing reports what it found in the projects' own files, so a connection
the user removed inside the client reads as a mismatch and never as
connected.

The suite-wide fixture in conftest.py already points the profile
variables inside tmp_path. The fixtures below narrow that further, per
test and through this platform's own store variable, so the exact
roster path is one this file names.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import roster as ro
from radialrig import skills as sk
from radialrig.paths import ROOT_ENV
from radialrig.tests.test_connect import (  # noqa: F401 - helpers, by name
    CONFIG_HOSTS,
    STORE_VAR,
    clear_store_env,
    command,
    fake_client,
    roaming_config,
    store_config,
    store_row,
)
from radialrig.tests.test_mount import _make_project

DAY = dt.date(2026, 8, 19)
SEEN = DAY.isoformat()
CLIENT = cn.DEFAULT_CLIENT

# The shapes an existing roster file arrives in that this build cannot
# read. Each one reads as empty, and none of them is ever rewritten.
UNREADABLE_FORMS: dict[str, bytes] = {
    "garbage": b"{not json at all\n",
    "not-an-object": b"[1, 2, 3]\n",
    "no-schema": b'{"projects": []}\n',
    "schema-text": b'{"schema": "1", "projects": []}\n',
    "schema-zero": b'{"schema": 0, "projects": []}\n',
    "schema-negative": b'{"schema": -3, "projects": []}\n',
    "schema-boolean": b'{"schema": true, "projects": []}\n',
    "newer-schema": b'{"schema": 2, "projects": [{"root": "X", "seen": "2026-01-01"}]}\n',
    "projects-not-a-list": b'{"schema": 1, "projects": {"root": "X"}}\n',
}


# --- fixtures ---------------------------------------------------------

@pytest.fixture()
def profile(tmp_path, monkeypatch) -> Path:
    """One roster file of this test's own, and the path it stands at.

    Set through this platform's own store variable, so the same test
    asserts the same behavior against whichever row the table serves
    (PLAN-0009 Phase 2 prescription 5).
    """
    home = tmp_path / "store-base"
    monkeypatch.setenv(STORE_VAR, str(home))
    return home / "radialrig" / "roster.json"


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path / "sample-project")


def plant(path: Path, body) -> bytes:
    """A roster file written by something other than this module."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = body if isinstance(body, bytes) else (
        json.dumps(body, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    return data


def parsed(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def record_of(project: Path) -> dict:
    return json.loads(cn.record_path(project).read_text(encoding="utf-8"))


def rewrite_record(project: Path, body: dict) -> None:
    cn.record_path(project).write_bytes(mnt._dump_manifest(body))


def connected_project(tmp_path, name: str, *, store: bool = False):
    """A project registered with a fake client, the real verb's own way."""
    project = _make_project(tmp_path / name)
    mapping = fake_client(tmp_path, store=store)
    roaming_config(mapping).write_bytes(CONFIG_HOSTS["no-servers"])
    if store:
        store_config(mapping).write_bytes(CONFIG_HOSTS["no-servers"])
    cn.connect(project, env=mapping, today=DAY)
    return project, mapping


# --- the path and the file shape --------------------------------------

def test_the_roster_path_comes_from_the_table(tmp_path, monkeypatch):
    # The whole table, pinned row by row: one row per operating system,
    # a row's sources in fallback order (PLAN-0009 constraint 3).
    assert [
        (s.platform, tuple((src.var, src.prefix) for src in s.sources), s.parts)
        for s in ro.STORES
    ] == [
        ("win32", (("APPDATA", ()),), ("radialrig", "roster.json")),
        ("linux",
         (("XDG_DATA_HOME", ()), ("HOME", (".local", "share"))),
         ("radialrig", "roster.json")),
    ]

    # Whichever row this machine serves: the first source set non-empty
    # wins, and no source at all means no path.
    row = store_row()
    home = tmp_path / "store-base"
    monkeypatch.setenv(STORE_VAR, str(home))
    for source in row.sources[1:]:
        monkeypatch.delenv(source.var, raising=False)
    assert ro.roster_path() == home / "radialrig" / "roster.json"
    assert ro.roster_path(env={STORE_VAR: str(home)}) == (
        home / "radialrig" / "roster.json")

    # Each fallback source alone answers its own path, literal prefix
    # included, and the first source wins over all of them together.
    for source in row.sources[1:]:
        base = tmp_path / f"fallback-{source.var.lower()}"
        assert ro.roster_path(env={source.var: str(base)}) == (
            base.joinpath(*source.prefix, *row.parts))
    crowded = {
        source.var: str(tmp_path / source.var.lower()) for source in row.sources
    }
    assert ro.roster_path(env=crowded) == (
        Path(crowded[STORE_VAR]) / "radialrig" / "roster.json")

    clear_store_env(monkeypatch)
    assert ro.roster_path() is None
    assert ro.roster_path(env={}) is None
    assert ro.roster_path(env={STORE_VAR: "   "}) is None


def test_the_file_shape_is_pinned(profile, project):
    ro.record_seen(project, today=DAY)

    body = parsed(profile)
    assert body == {
        "schema": 1,
        "projects": [{"root": str(project.resolve()), "seen": SEEN}],
    }
    assert set(body["projects"][0]) == {"root", "seen"}
    assert profile.read_bytes().endswith(b"\n")


def test_unknown_fields_survive_a_rewrite(profile, project):
    plant(profile, {
        "schema": 1,
        "note": "written by a later build",
        "projects": [
            {"root": str(project.resolve()), "seen": "2020-01-01", "tag": "keep"},
        ],
    })

    ro.record_seen(project, today=DAY)

    body = parsed(profile)
    assert body["note"] == "written by a later build"
    assert len(body["projects"]) == 1
    assert body["projects"][0]["tag"] == "keep"
    assert body["projects"][0]["seen"] == SEEN


def test_unreadable_absent_and_newer_read_as_empty(tmp_path, project):
    absent = {STORE_VAR: str(tmp_path / "absent")}
    empty = ro.load(env=absent)
    assert empty.entries == ()
    assert empty.body == {"schema": 1, "projects": []}
    assert empty.writable is True

    for name, data in UNREADABLE_FORMS.items():
        home = tmp_path / f"unreadable-{name}"
        plant(home / "radialrig" / "roster.json", data)
        roster = ro.load(env={STORE_VAR: str(home)})
        assert roster.entries == (), name
        assert roster.body == {"schema": 1, "projects": []}, name
        assert roster.writable is False, name

    home = tmp_path / "readable"
    plant(home / "radialrig" / "roster.json", {
        "schema": 1,
        "projects": [
            {"root": str(project.resolve())},
            {"root": str(project.resolve()), "seen": 7},
        ],
    })
    roster = ro.load(env={STORE_VAR: str(home)})
    assert roster.writable is True
    assert [e["seen"] for e in roster.entries] == ["", ""]


def test_a_body_with_no_projects_member_stays_writable(tmp_path, project):
    """Adding a member destroys nothing; replacing one of another shape does.

    That is the line between this case and the `projects-not-a-list`
    form above: one roster is missing a member this build knows how to
    add, and the other carries a member holding something this build
    cannot read.
    """
    home = tmp_path / "no-member"
    path = home / "radialrig" / "roster.json"
    plant(path, {"schema": 1, "note": "written by a later build"})
    env = {STORE_VAR: str(home)}

    roster = ro.load(env=env)
    assert roster.entries == ()
    assert roster.writable is True

    ro.record_seen(project, today=DAY, env=env)

    body = parsed(path)
    assert body["note"] == "written by a later build"
    assert body["projects"] == [{"root": str(project.resolve()), "seen": SEEN}]


def test_an_existing_file_this_build_cannot_read_is_never_overwritten(
    tmp_path, project,
):
    for name, data in UNREADABLE_FORMS.items():
        home = tmp_path / f"kept-{name}"
        path = home / "radialrig" / "roster.json"
        plant(path, data)
        env = {STORE_VAR: str(home)}

        ro.record_seen(project, today=DAY, env=env)
        assert ro.listing(env=env) == ()

        assert path.read_bytes() == data, name
        assert list(path.parent.iterdir()) == [path], name


def test_an_unreadable_entry_survives_a_rewrite(profile, tmp_path):
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    beta = tmp_path / "beta"
    beta.mkdir()
    plant(profile, {
        "schema": 1,
        "projects": [
            "not an entry at all",
            {"seen": "2020-01-01"},
            {"root": str(alpha.resolve()), "seen": "2020-01-01"},
        ],
    })

    ro.record_seen(beta, today=DAY)

    body = parsed(profile)
    assert body["projects"][0] == "not an entry at all"
    assert body["projects"][1] == {"seen": "2020-01-01"}
    assert body["projects"][2] == {"root": str(alpha.resolve()), "seen": "2020-01-01"}
    assert body["projects"][3] == {"root": str(beta.resolve()), "seen": SEEN}


def test_record_seen_upserts_by_normcase(profile, tmp_path):
    """The upsert key is `os.path.normcase` of the resolved root.

    On a platform that folds case, a spelling that differs only by case
    is the same root and the upsert rewrites its `seen` alone. On one
    that does not, it is a different root and the entry is appended.
    Both branches are the same rule, asserted as this platform reads it.
    """
    alpha = tmp_path / "Alpha"
    alpha.mkdir()
    resolved = str(alpha.resolve())
    stored = resolved.upper()
    plant(profile, {"schema": 1, "projects": [{"root": stored, "seen": "2020-01-01"}]})

    ro.record_seen(alpha, today=DAY)

    body = parsed(profile)
    if os.path.normcase(stored) == os.path.normcase(resolved):
        assert body["projects"] == [{"root": stored, "seen": SEEN}]
    else:
        assert body["projects"] == [
            {"root": stored, "seen": "2020-01-01"},
            {"root": resolved, "seen": SEEN},
        ]


def test_dry_run_touches_nothing(profile, project):
    ro.record_seen(project, today=DAY, dry_run=True)

    assert not profile.exists()
    assert not profile.parent.exists()


def test_the_write_is_atomic_and_best_effort(profile, tmp_path, monkeypatch):
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    ro.record_seen(alpha, today=DAY)
    before = profile.read_bytes()

    def refuse(src, dst):
        raise OSError("the swap failed")

    monkeypatch.setattr(ro.os, "replace", refuse)
    beta = tmp_path / "beta"
    beta.mkdir()
    ro.record_seen(beta, today=DAY)

    assert profile.read_bytes() == before
    assert list(profile.parent.glob("*.tmp")) == []

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "radialrig").write_bytes(b"a file where the directory goes\n")
    ro.record_seen(alpha, today=DAY, env={STORE_VAR: str(blocked)})
    assert (blocked / "radialrig").read_bytes() == b"a file where the directory goes\n"


def test_two_threads_writing_at_once_leave_a_readable_roster(profile, tmp_path):
    """One process, several threads: the manage server's own shape.

    That server answers its listing views unlocked, deliberately, so a
    view's drop-gone rewrite runs beside a press's side entry inside one
    process. Both go through `_write`, and a temporary file named by the
    process alone is one file for both of them: they write over one
    another and the mixture is what the swap puts where the roster was.
    That file is one this build cannot read, so every write after it
    declines under constraint 5 and the listing is gone for good.

    The rounds are gated rather than raced: each thread does exactly one
    operation per round, and the file is read between rounds, when
    nothing is writing it.
    """
    gone = tmp_path / "no-longer-here"
    roots = []
    for index in range(6):
        root = tmp_path / f"live-{index}"
        root.mkdir()
        roots.append(root)

    rounds = 20
    gate = threading.Barrier(len(roots) + 1, timeout=60)
    trouble: list[BaseException] = []

    def worker(index: int) -> None:
        for _ in range(rounds):
            gate.wait()
            try:
                # Half the threads list and half record, which is the
                # pair the server actually runs at once.
                if index % 2:
                    ro.listing()
                else:
                    ro.record_seen(roots[index], today=DAY)
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                trouble.append(exc)
            gate.wait()

    threads = [
        threading.Thread(target=worker, args=(index,), daemon=True)
        for index in range(len(roots))
    ]
    for thread in threads:
        thread.start()

    for turn in range(rounds):
        # One entry whose directory is not there, so every listing in the
        # round fires the drop-gone rewrite rather than only reading.
        ro.record_seen(gone, today=DAY)
        gate.wait()
        gate.wait()
        assert not trouble, trouble
        assert ro.load().writable, \
            f"round {turn} left a roster this build cannot read"
        assert parsed(profile)["schema"] == ro.ROSTER_SCHEMA

    for thread in threads:
        thread.join(60)
        assert not thread.is_alive()
    assert list(profile.parent.glob("*.tmp")) == []


# --- verification -----------------------------------------------------

def test_verify_the_connected_matrix(tmp_path, command):
    project, mapping = connected_project(tmp_path, "agreed")
    assert ro.verify(project).connected == CLIENT

    bare = _make_project(tmp_path / "bare")
    assert ro.verify(bare).connected == ro.CONNECTED_NO

    unreadable = _make_project(tmp_path / "unreadable")
    cn.record_path(unreadable).parent.mkdir(parents=True, exist_ok=True)
    cn.record_path(unreadable).write_bytes(b"{ not json\n")
    assert ro.verify(unreadable).connected == ro.CONNECTED_MISMATCH

    body = record_of(project)
    rewrite_record(project, {**body, "client": "no-such-client"})
    assert ro.verify(project).connected == ro.CONNECTED_MISMATCH

    rewrite_record(project, {**body, "copies": []})
    assert ro.verify(project).connected == ro.CONNECTED_MISMATCH

    rewrite_record(project, {**body, "key": 7})
    assert ro.verify(project).connected == ro.CONNECTED_MISMATCH

    rewrite_record(project, body)
    assert ro.verify(project).connected == CLIENT

    config = roaming_config(mapping)
    settings = json.loads(config.read_text(encoding="utf-8"))
    kept = dict(settings["mcpServers"])
    del settings["mcpServers"][body["key"]]
    config.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    assert ro.verify(project).connected == ro.CONNECTED_MISMATCH

    settings["mcpServers"] = dict(kept)
    settings["mcpServers"][body["key"]]["args"] = ["--changed"]
    config.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    assert ro.verify(project).connected == ro.CONNECTED_MISMATCH

    pair, both = connected_project(tmp_path, "two-copies", store=True)
    assert len(record_of(pair)["copies"]) == 2
    assert ro.verify(pair).connected == CLIENT
    hidden = store_config(both)
    settings = json.loads(hidden.read_text(encoding="utf-8"))
    del settings["mcpServers"][record_of(pair)["key"]]
    hidden.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    assert ro.verify(pair).connected == ro.CONNECTED_MISMATCH


def test_a_copy_appearing_after_the_connect_does_not_flip_the_verdict(
    tmp_path, command,
):
    project, mapping = connected_project(tmp_path, "adopted")
    assert len(record_of(project)["copies"]) == 1

    later = fake_client(tmp_path, store=True)
    store_config(later).write_bytes(CONFIG_HOSTS["no-servers"])
    assert len(cn.detect(cn.CLIENTS[CLIENT], env=later)) == 2

    assert ro.verify(project).connected == CLIENT


def test_a_malformed_copies_list_reads_as_mismatch(tmp_path, command):
    """A record whose own list of copies is damaged claims nothing.

    Filtering the damaged items out and checking what survived would let
    the record go on answering a client's name off a claim this listing
    never read.
    """
    project, _ = connected_project(tmp_path, "damaged")
    body = record_of(project)
    assert ro.verify(project).connected == CLIENT

    for copies in (
        "not a list at all",
        ["a bare string"],
        [*body["copies"], {"path": ""}],
        [*body["copies"], {"path": 7}],
        [*body["copies"], {"host": "no path at all"}],
        [*body["copies"], None],
    ):
        rewrite_record(project, {**body, "copies": copies})
        assert ro.verify(project).connected == ro.CONNECTED_MISMATCH, copies

    rewrite_record(project, body)
    assert ro.verify(project).connected == CLIENT


def test_verify_mounted_and_skills(tmp_path, project):
    state = ro.verify(project)
    assert (state.exists, state.mounted, state.connected, state.skills) == (
        True, False, ro.CONNECTED_NO, ())
    assert state.root == str(project)

    manifest = project / mnt.MANIFEST_REL
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(b'{"schema": 2}\n')
    assert ro.verify(project).mounted is True

    harness = sk.default_harness()
    installed = project / harness.skills / "plan-review" / "SKILL.md"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_bytes(b"# a skill\n")
    record = project / harness.record
    record.write_bytes(json.dumps({
        "schema": 1,
        "skills": [
            {"name": "plan-review", "files": [
                {"path": f"{harness.skills}/plan-review/SKILL.md"}]},
            {"name": "session-handoff", "files": [
                {"path": f"{harness.skills}/session-handoff/SKILL.md"}]},
            {"name": "no-files-at-all", "files": []},
        ],
    }, indent=2).encode("utf-8"))
    assert ro.verify(project).skills == ("plan-review",)
    # The same names agent by agent, in map order (PLAN-0015 phase 2):
    # this one's record lists the skill, every other agent's lists none.
    assert ro.verify(project).agents == tuple(
        (h.name, ("plan-review",) if h is harness else ())
        for h in sk.HARNESSES.values())
    assert not ro.agents_agree(ro.verify(project))

    record.write_bytes(b"{ not json\n")
    assert ro.verify(project).skills == ()
    assert ro.verify(project).agents == tuple((h.name, ()) for h in sk.HARNESSES.values())
    assert ro.agents_agree(ro.verify(project))

    shutil.rmtree(project)
    gone = ro.verify(project)
    assert (gone.exists, gone.mounted, gone.connected, gone.skills) == (
        False, False, ro.CONNECTED_NO, ())


# --- the listing ------------------------------------------------------

def test_listing_drops_the_gone_and_rewrites_once(profile, tmp_path, monkeypatch):
    roots = []
    for name in ("alpha", "beta", "gone"):
        root = tmp_path / name
        root.mkdir()
        roots.append(root)
        ro.record_seen(root, today=DAY)
    shutil.rmtree(roots[2])

    written: list[dict] = []
    real = ro._write

    def counted(body, *, env=None):
        written.append(body)
        real(body, env=env)

    monkeypatch.setattr(ro, "_write", counted)
    states = ro.listing()

    assert [s.root for s in states] == [str(r.resolve()) for r in roots]
    assert [s.exists for s in states] == [True, True, False]
    assert len(written) == 1

    body = parsed(profile)
    assert [e["root"] for e in body["projects"]] == [
        str(roots[0].resolve()), str(roots[1].resolve())]

    written.clear()
    ro.listing()
    assert written == []


def test_two_entries_for_one_root_are_listed_once(profile, tmp_path):
    """A roster edited outside this build can name one root twice.

    Both surfaces read this listing, so a second row would read as a
    second project. The first entry in file order is the one that
    answers, and the file itself is left exactly as it stood.
    """
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    stored = str(alpha.resolve())
    before = plant(profile, {"schema": 1, "projects": [
        {"root": stored, "seen": "2020-01-01"},
        {"root": stored.upper(), "seen": "2021-01-01"},
        {"root": stored, "seen": "2022-01-01"},
    ]})

    states = ro.listing()

    if os.path.normcase(stored.upper()) == os.path.normcase(stored):
        # A case-folding platform reads all three entries as one root:
        # the first answers, and the file stands untouched.
        assert [state.root for state in states] == [stored]
        assert profile.read_bytes() == before
    else:
        # Here the upper spelling is its own root, no project stands at
        # it, and the drop-gone rewrite takes exactly that entry out.
        # The two spellings of the standing root still collapse to one
        # state, first in file order.
        assert [state.root for state in states] == [stored, stored.upper()]
        assert states[1].exists is False
        assert [e["root"] for e in parsed(profile)["projects"]] == [stored, stored]


# --- orphans ----------------------------------------------------------

def test_orphans_are_found_by_prefix_and_dead_root(tmp_path):
    mapping = fake_client(tmp_path, store=True)
    dead = tmp_path / "vanished"
    alive = tmp_path / "still-here"
    alive.mkdir()
    config = roaming_config(mapping)
    config.write_text(json.dumps({
        "mcpServers": {
            "radialrig-dead": {"command": "x", "args": [],
                               "env": {ROOT_ENV: str(dead)}},
            "radialrig-alive": {"command": "x", "args": [],
                                "env": {ROOT_ENV: str(alive)}},
            "radialrig-unpinned": {"command": "x", "args": []},
            "someone-elses": {"command": "x", "args": [],
                              "env": {ROOT_ENV: str(dead)}},
        },
    }, indent=2), encoding="utf-8")
    store_config(mapping).write_bytes(b"{ not json at all\n")

    found = ro.orphans(env=mapping)

    assert [o.key for o in found] == ["radialrig-dead", "radialrig-unpinned"]
    assert found[0].client == CLIENT
    assert found[0].root == str(dead)
    assert found[0].copies == (config.resolve().as_posix(),)
    assert found[1].root == ""
    assert found[1].copies == (config.resolve().as_posix(),)

    assert ro.orphans(env={}) == ()


def test_one_key_is_decided_over_every_copy_that_carries_it(tmp_path):
    """The listing may never advertise a removal the verb would refuse.

    The verb reads every copy of a client's settings and refuses the
    whole key when any of them pins a project that still stands, so a
    listing deciding copy by copy would name an entry and then be told
    no. One key, one verdict, over all of them.
    """
    mapping = fake_client(tmp_path, store=True)
    dead = tmp_path / "vanished"
    alive = tmp_path / "still-here"
    alive.mkdir()

    roaming = roaming_config(mapping)
    roaming.write_text(json.dumps({"mcpServers": {
        "radialrig-split": {"command": "x", "args": [],
                            "env": {ROOT_ENV: str(dead)}},
        "radialrig-half": {"command": "x", "args": [],
                           "env": {ROOT_ENV: str(dead)}},
        "radialrig-both-dead": {"command": "x", "args": [],
                                "env": {ROOT_ENV: str(dead)}},
    }}, indent=2), encoding="utf-8")
    store = store_config(mapping)
    store.write_text(json.dumps({"mcpServers": {
        "radialrig-split": {"command": "x", "args": [],
                            "env": {ROOT_ENV: str(alive)}},
        "radialrig-half": {"command": "x", "args": []},
        "radialrig-both-dead": {"command": "x", "args": [],
                                "env": {ROOT_ENV: str(dead)}},
    }}, indent=2), encoding="utf-8")

    found = ro.orphans(env=mapping)

    # The split key is not listed at all: one copy pins a live project.
    assert [orphan.key for orphan in found] == [
        "radialrig-both-dead", "radialrig-half"]
    assert found[0].root == str(dead)
    assert found[1].root == ""
    for orphan in found:
        assert set(orphan.copies) == {
            roaming.resolve().as_posix(), store.resolve().as_posix()}


# --- the rows and their wordings --------------------------------------

def test_report_rows_carry_the_pinned_wordings(tmp_path, command):
    project, _ = connected_project(tmp_path, "listed")
    listed = ro.verify(project)
    row = ro.report_rows((listed,), ())[0]
    assert (row.path, row.verb, row.code) == (
        str(project), mnt.CURRENT, ro.ROSTER_LISTED)
    label = cn.CLIENTS[CLIENT].label
    assert ro.reason_text(row) == f"not mounted; registered with {label}; no skills"

    # The mismatch clause is a template (PLAN-0014 phase 3): the client's
    # label where the record names one this build knows, filled from the
    # table at render time; the record file where it names none.
    mismatched = ro.ProjectState(
        root=str(project), exists=True, mounted=True,
        connected=ro.CONNECTED_MISMATCH, skills=("plan-review", "toolkit-standards"),
        claimed=CLIENT,
    )
    row = ro.report_rows((mismatched,), ())[0]
    assert (row.verb, row.code) == (mnt.LEFT, ro.ROSTER_MISMATCH)
    assert ro.reason_text(row) == (
        f"mounted; {label}'s settings no longer carry the entry this project's "
        "record claims; skills: plan-review, toolkit-standards")
    assert ro.mismatch_clause(mismatched) == ro.MISMATCH_CLAUSE.format(label=label)
    assert ro.MISMATCH_CLAUSE == (
        "{label}'s settings no longer carry the entry this project's record claims")

    unknown = ro.ProjectState(
        root=str(project), exists=True, mounted=True,
        connected=ro.CONNECTED_MISMATCH, skills=(),
    )
    assert unknown.claimed == ""
    row = ro.report_rows((unknown,), ())[0]
    assert (row.verb, row.code) == (mnt.LEFT, ro.ROSTER_MISMATCH)
    record = project / cn.CONNECT_REL
    assert ro.reason_text(row) == (
        f"mounted; This project's record names no client this build knows: "
        f"see {record}; no skills")
    assert ro.mismatch_clause(unknown) == ro.UNKNOWN_CLIENT_CLAUSE.format(file=record)
    assert "{label}" not in ro.reason_text(row)
    assert not hasattr(ro, "_connected")

    gone = ro.ProjectState(
        root=r"C:\dev\gone", exists=False, mounted=False,
        connected=ro.CONNECTED_NO, skills=(),
    )
    row = ro.report_rows((gone,), ())[0]
    assert (row.path, row.verb, row.code) == (
        r"C:\dev\gone", mnt.REMOVED, ro.ROSTER_GONE)
    assert ro.reason_text(row) == ro.ROSTER_REASONS[ro.ROSTER_GONE]

    orphan = ro.Orphan(client=CLIENT, key="radialrig-old", root=r"C:\dev\old",
                       copies=("C:/settings.json",))
    unpinned = ro.Orphan(client=CLIENT, key="radialrig-bare", root="", copies=())
    rows = ro.report_rows((), (orphan, unpinned))
    assert [(r.path, r.verb, r.code) for r in rows] == [
        ("radialrig-old", mnt.LEFT, ro.ROSTER_ORPHAN),
        ("radialrig-bare", mnt.LEFT, ro.ROSTER_UNPINNED),
    ]
    assert ro.reason_text(rows[0]).endswith(
        f"radialrig unconnect {CLIENT} --orphan radialrig-old")
    assert ro.reason_text(rows[1]) == ro.ROSTER_REASONS[ro.ROSTER_UNPINNED]

    # The skills facet splits per agent only where the agents' lists
    # differ (PLAN-0015 phase 2 prescription 5), labelled from the map;
    # a state built without the per-agent pairs reads as one facet.
    first, second = list(sk.HARNESSES.values())[:2]
    split = ro.ProjectState(
        root=str(project), exists=True, mounted=True, connected=ro.CONNECTED_NO,
        skills=("plan-review",),
        agents=((first.name, ("plan-review",)), (second.name, ())),
    )
    assert not ro.agents_agree(split)
    assert ro.reason_text(ro.report_rows((split,), ())[0]) == (
        f"mounted; not registered with {cn.CLIENTS[CLIENT].label}; "
        f"{first.label} skills: plan-review; "
        f"{second.label} skills: none")
    agreed = ro.ProjectState(
        root=str(project), exists=True, mounted=True, connected=ro.CONNECTED_NO,
        skills=("plan-review",),
        agents=((first.name, ("plan-review",)), (second.name, ("plan-review",))),
    )
    assert ro.agents_agree(agreed) and ro.agents_agree(listed)
    assert ro.reason_text(ro.report_rows((agreed,), ())[0]).endswith(
        "; skills: plan-review")
    for harness in sk.HARNESSES.values():
        assert harness.name not in ro.reason_text(ro.report_rows((split,), ())[0])

    borrowed = mnt.Disposition(".mcp.json", mnt.LEFT, mnt.LEFT_EDITED)
    assert ro.reason_text(borrowed) == mnt.reason_text(borrowed)

    assert all(row.verb in mnt.VERBS for row in ro.report_rows(
        (listed, mismatched, gone), (orphan, unpinned)))


def test_no_roster_wording_implies_who_typed_a_change():
    from radialrig.tests.test_cli import FORBIDDEN_WORD

    # The two mismatch templates, checked as the page and the row say
    # them: the label filled, and the record file filled (PLAN-0014
    # phase 3).
    fragments = [
        *ro.ROSTER_REASONS.values(),
        ro.MISMATCH_CLAUSE.format(label=cn.CLIENTS[CLIENT].label),
        ro.UNKNOWN_CLIENT_CLAUSE.format(file=r"C:\dev\alpha\.radialrig\connect.json"),
        "mounted", "not mounted", "no skills",
        f"not registered with {cn.CLIENTS[CLIENT].label}",
        f"registered with {cn.CLIENTS[CLIENT].label}",
    ]
    for text in fragments:
        assert not FORBIDDEN_WORD.search(text), text
        lowered = text.lower()
        for banned in ("magic", "just works", "seamless"):
            assert banned not in lowered, text
        assert not any(
            word == "safe" for word in lowered.replace(",", " ").split()
        ), text


def test_a_missing_store_variable_costs_the_roster_and_nothing_else(
    monkeypatch, project,
):
    clear_store_env(monkeypatch)

    assert ro.roster_path() is None
    assert ro.load().entries == ()
    assert ro.record_seen(project, today=DAY) is None
    assert ro.listing() == ()
    assert ro.orphans() == ()
    assert not (Path(os.environ.get("LOCALAPPDATA", str(project))) /
                "radialrig").exists()
