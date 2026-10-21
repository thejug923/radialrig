"""Tests for the activity log — PLAN-0018 phase 2 (BUG-0014).

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_activity_log.py -q

Everything RadialRig does on this computer leaves a short written trace a
person can find and read. The oracle is the phase's done-condition, item
by item: one event per command run, per page press, per 500 error, per
server start and per tool call; rotation at the cap; a log that can
never change an exit code, a report, a page or a tool result; several
processes appending at once; the 1-second lock budget; `radialrig log`'s
three exits; the failure wording and the removal list naming the log;
and this developer's own log untouched by the suite.

Nothing here reaches this machine's own profile: the profile variables
point inside tmp_path for every test in the suite (conftest.py), so the
log these tests read lands under tmp_path too. The page and server
fixtures are the ones their own test files drive, imported by name, so
a press here is the press those files prove.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from radialrig import authoring_server as a
from radialrig import cli
from radialrig import manage_web
from radialrig import mount as mnt
from radialrig import report
from radialrig import retrieval_server as rs
from radialrig import roster as ro
from radialrig import setup_web
from radialrig.tests.test_authoring_server import (  # noqa: F401 - fixtures, by name
    S,
    corpus,
)
from radialrig.tests.test_authoring_server import record as write_record
from radialrig.tests.test_connect import (  # noqa: F401 - helpers, by name
    STORE_VAR,
    clear_store_env,
    run,
)
from radialrig.tests.test_manage_web import manage  # noqa: F401 - fixture, by name
from radialrig.tests.test_mount import _make_project
from radialrig.tests.test_retrieval_server import (  # noqa: F401 - fixtures, by name
    record_text,
    root,
    write,
)
from radialrig.tests.test_setup_web import walk  # noqa: F401 - fixture, by name

try:
    from radialrig import activity_log as al
except ImportError:  # the red run: the module does not exist yet
    al = None

ROOT = Path(__file__).resolve().parents[3]

# What a header line's fields are separated by, and which field says
# what ran. The header is one line: the stamp, the engine, the surface,
# the process, what ran, then the folder and the outcome where there is
# one. A test reads the fifth field rather than parsing the whole line.
SEP = "  "
WHAT = 4


# --- helpers ----------------------------------------------------------

def log_file() -> Path:
    path = al.log_path()
    assert path is not None, "this platform keeps no log, and the suite runs on one that does"
    return path


def log_text() -> str:
    path = log_file()
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def events_now() -> list[str]:
    return al.events(log_text())


def what(event: str) -> str:
    return event.splitlines()[0].split(SEP)[WHAT]


def whats(events: list[str]) -> list[str]:
    return [what(event) for event in events]


def sizes(root_: Path) -> dict:
    return {p.name: p.stat().st_size for p in root_.iterdir()}


# --- prescription 1: the module, its folder, its files ------------------

def test_the_log_lives_beside_the_roster_and_nowhere_without_one(monkeypatch):
    """One table serves both files: the log's folder is the roster's."""
    roster = ro.roster_path()
    assert roster is not None
    path = al.log_path()
    assert path == roster.parent / al.LOG_NAME
    assert al.LOG_NAME == "radialrig.log"
    assert al.OLDER_NAME == "radialrig.log.1"
    assert al.LOCK_NAME == "radialrig.log.lock"

    clear_store_env(monkeypatch)
    assert ro.roster_path() is None
    assert al.log_path() is None
    # Nothing to write to is nothing to write: no raise, no file anywhere.
    al.record(al.COMMAND, "nowhere")


def test_mount_connect_skills_and_roster_do_not_import_the_log():
    """The dependency runs one way, and the log's own words take the
    strict sweep the other new sources take (D-33)."""
    base = Path(mnt.__file__).parent
    for name in ("mount.py", "connect.py", "skills.py", "roster.py"):
        assert "activity_log" not in (base / name).read_text(encoding="utf-8"), name
    text = (base / "activity_log.py").read_text(encoding="utf-8").lower()
    for word in ("hand", "manual", "human"):
        assert word not in text, f"activity_log.py uses '{word}'"


# --- prescription 2: the command line -----------------------------------

def test_one_event_per_command_run_with_its_arguments_folder_exit_and_lines(
    tmp_path, capsys,
):
    project = _make_project(tmp_path / "host")

    code, out, err = run(capsys, "mount", str(project), "--dry-run")
    assert (code, err) == (report.OK, "")
    found = events_now()
    assert len(found) == 1
    head, *detail = found[0].splitlines()
    # Invariant 4: the version is the installed one, never a literal.
    assert f"{SEP}radialrig {mnt.installed_version()}{SEP}" in head
    assert f"{SEP}{al.COMMAND}{SEP}pid {os.getpid()}{SEP}" in head
    assert what(found[0]) == f"radialrig mount {project} --dry-run"
    assert f"{SEP}folder {project.resolve()}{SEP}" in head
    assert head.endswith(f"{SEP}exit {report.OK}")
    # The lines the person saw, the engine line included, each indented
    # so no report line can ever read as a header.
    assert detail == [f"{al.INDENT}{line}" for line in out.splitlines()]
    assert detail[-1] == f"{al.INDENT}{report.engine_line()}"

    # A run that failed before any report: its exit code and the one
    # stderr line, through `_fail`.
    code, out, err = run(capsys, "mount", str(tmp_path / "nowhere"))
    assert (code, out) == (report.ERROR, "")
    found = events_now()
    assert len(found) == 2
    head, *detail = found[1].splitlines()
    assert head.endswith(f"{SEP}exit {report.ERROR}")
    assert detail == [f"{al.INDENT}{line}" for line in err.splitlines()]

    # The two-line stderr answers go through the same keeper.
    code, out, err = run(capsys, "skill", "add", "no-such-skill", str(project))
    assert code == report.ERROR
    assert len(err.splitlines()) == 2
    found = events_now()
    assert len(found) == 3
    assert found[2].splitlines()[1:] == [f"{al.INDENT}{line}" for line in err.splitlines()]

    # A run that ran no verb writes nothing: the version and the usage.
    assert run(capsys, "--version")[0] == report.OK
    assert run(capsys)[0] == report.OK
    assert len(events_now()) == 3


def test_a_crash_is_logged_with_its_traceback_and_still_reaches_the_caller(
    tmp_path, monkeypatch,
):
    project = _make_project(tmp_path / "host")

    def boom(args, root_):
        raise RuntimeError("the disk went away")

    monkeypatch.setitem(cli.RUNNERS, "mount", boom)
    with pytest.raises(RuntimeError) as caught:
        cli.main(["mount", str(project)])
    assert str(caught.value) == "the disk went away"
    assert caught.value.__traceback__ is not None

    found = events_now()
    assert len(found) == 1
    head, *detail = found[0].splitlines()
    assert what(found[0]) == f"radialrig mount {project}"
    assert head.endswith(f"{SEP}error RuntimeError: the disk went away")
    assert detail[0] == f"{al.INDENT}Traceback (most recent call last):"
    assert any("in boom" in line for line in detail)
    assert detail[-1] == f"{al.INDENT}RuntimeError: the disk went away"
    assert all(line.startswith(al.INDENT) for line in detail)


# --- prescription 3: the pages -----------------------------------------

def test_a_setup_page_press_writes_one_event_and_the_page_its_start_and_stop(
    tmp_path, walk,
):
    root_ = _make_project(tmp_path / "host").resolve()
    run_ = walk(root_)
    started = events_now()
    assert len(started) == 1
    assert f"{SEP}{al.SETUP_PAGE}{SEP}" in started[0]
    assert what(started[0]) == f"started on {setup_web.HOST}:{run_.session.port}"
    assert f"{SEP}folder {root_}" in started[0]

    run_.page()
    assert len(events_now()) == 1, "a view is not a press"
    status, body, _ = run_.post({"token": run_.session.token, "action": "mount"})
    assert status == 200
    found = events_now()
    assert len(found) == 2
    head, *detail = found[1].splitlines()
    assert f"{SEP}{al.SETUP_PAGE}{SEP}" in head
    assert what(found[1]) == "press mount"
    assert f"{SEP}folder {root_}{SEP}" in head
    assert head.endswith(f"{SEP}{setup_web.MOUNTED} exit {report.OK}")
    # The heading and the rows, in the command line's own words.
    assert detail[0] == f"{al.INDENT}Mounted radialrig {mnt.installed_version()} onto {root_}"
    assert any(line.startswith(f"{al.INDENT}created") for line in detail)
    assert mnt.is_mounted(root_)

    # The mount ends the walkthrough, and the page says it stopped.
    assert run_.wait() == report.OK
    found = events_now()
    assert len(found) == 3
    assert what(found[2]) == "stopped"
    assert found[2].endswith(f"{SEP}exit {report.OK}")


def test_a_500_error_writes_one_event_with_the_traceback_and_names_the_log(
    tmp_path, walk, monkeypatch,
):
    run_ = walk(_make_project(tmp_path / "host"))
    assert run_.page()
    before = len(events_now())

    def boom(session):
        raise RuntimeError("the project went away")

    monkeypatch.setattr(setup_web, "compute_state", boom)
    status, body, _ = run_.get(f"/setup?s={run_.session.token}")
    assert status == 500
    # Invariant 12: the body is `worded(INTERNAL)` byte for byte, and
    # that sentence now names the log.
    assert body == setup_web.worded(setup_web.INTERNAL) + "\n"
    assert str(log_file()) in body
    assert run_.session.token not in body

    found = events_now()
    assert len(found) == before + 1
    head, *detail = found[-1].splitlines()
    assert f"{SEP}{al.SETUP_PAGE}{SEP}" in head
    assert what(found[-1]) == "GET /setup"
    assert run_.session.token not in found[-1]
    assert head.endswith(f"{SEP}error RuntimeError: the project went away")
    assert detail[0] == f"{al.INDENT}Traceback (most recent call last):"
    assert detail[-1] == f"{al.INDENT}RuntimeError: the project went away"
    assert run_.running


def test_a_manage_page_press_writes_one_event_and_a_navigation_none(
    tmp_path, capsys, manage,
):
    root_ = _make_project(tmp_path / "host").resolve()
    assert run(capsys, "mount", str(root_))[0] == report.OK
    run_ = manage()
    found = events_now()
    assert what(found[-1]) == f"started on {setup_web.HOST}:{run_.session.port}"
    assert f"{SEP}{al.MANAGE_PAGE}{SEP}" in found[-1]
    run_.page()
    before = len(events_now())
    token = run_.session.token

    assert run_.post({"token": token, "view": manage_web.ROSTER})[0] == 200
    assert len(events_now()) == before, "a navigation is not a press"

    fields = {"token": token, "action": manage_web.UNMOUNT, "root": str(root_)}
    assert run_.post(fields)[0] == 200
    assert run_.post({**fields, "confirm": "yes"})[0] == 200
    found = events_now()[before:]
    assert len(found) == 2
    plan, done = found
    assert f"{SEP}{al.MANAGE_PAGE}{SEP}" in plan
    assert what(plan) == "press unmount"
    assert f"{SEP}folder {root_}{SEP}" in plan
    assert plan.splitlines()[0].endswith(f"{SEP}{manage_web.PLAN} exit {report.OK}")
    assert what(done) == "press unmount confirm"
    assert done.splitlines()[0].endswith(f"{SEP}{manage_web.DONE} exit {report.OK}")
    assert done.splitlines()[1].startswith(f"{al.INDENT}Unmounted radialrig from {root_}")
    assert not mnt.is_mounted(root_)

    assert run_.stop() == report.OK
    assert what(events_now()[-1]) == "stopped"


def test_a_manage_page_500_writes_one_event_with_the_traceback(
    manage, monkeypatch, capsys,
):
    """The manage page's error boundary is the setup page's, overridden
    to absorb a hang-up and nothing else: every other error leaves the
    same event, under this page's own name."""
    run_ = manage()
    run_.page()
    before = len(events_now())

    def boom(*args, **kwargs):
        raise RuntimeError("the listing went away")

    with monkeypatch.context() as patch:
        patch.setattr(manage_web, "roster_state", boom)
        status, body, _ = run_.get(f"{manage_web.MANAGE_PATH}?s={run_.session.token}")
    assert status == 500
    assert body == setup_web.worded(
        setup_web.INTERNAL, surface=manage_web.SURFACE, page=manage_web.PAGE_NAME) + "\n"
    assert str(log_file()) in body

    found = events_now()
    assert len(found) == before + 1
    head, *detail = found[-1].splitlines()
    assert f"{SEP}{al.MANAGE_PAGE}{SEP}" in head
    assert what(found[-1]) == f"GET {manage_web.MANAGE_PATH}"
    assert run_.session.token not in found[-1]
    assert head.endswith(f"{SEP}error RuntimeError: the listing went away")
    assert detail[0] == f"{al.INDENT}Traceback (most recent call last):"
    assert detail[-1] == f"{al.INDENT}RuntimeError: the listing went away"
    assert run_.running
    assert f"{manage_web.SURFACE}: RuntimeError:" in capsys.readouterr().err


# --- prescription 4: the servers ---------------------------------------

def test_the_read_server_writes_a_start_event_and_one_line_per_tool_call(
    root, capsys,
):
    write(root, "docs/decisions/D-90-test.md", record_text(body="The answer."))

    class Quiet:
        def run(self):
            return None

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(rs, "build_server", lambda: Quiet())
        rs.main()
    found = events_now()
    assert len(found) == 1
    assert "\n" not in found[0]
    assert f"{SEP}radialrig {mnt.installed_version()}{SEP}{al.READ_SERVER}{SEP}pid {os.getpid()}{SEP}" in found[0]
    assert what(found[0]) == "started"
    assert found[0].endswith(f"{SEP}folder {root}")

    asyncio.run(rs.build_server().call_tool("get_ruling", {"query": "D-90"}))
    found = events_now()
    assert len(found) == 2
    line = found[1]
    assert "\n" not in line
    assert f"{SEP}{al.READ_SERVER}{SEP}" in line
    assert what(line) == "get_ruling"
    assert re.search(r"  kind ruling, \d+ ms$", line), line
    # Decided 3: no tool argument and no record text, ever.
    assert "D-90" not in line
    assert "The answer." not in log_text()
    # Invariant 2: nothing but protocol on stdout.
    assert capsys.readouterr().out == ""


def test_the_write_server_logs_each_verbs_target_and_nothing_else(corpus, capsys):
    text = write_record(rid="D-92", ruling="A sentence only the record holds.")
    asyncio.run(a.build_server().call_tool("propose_record", {
        "path": "docs/decisions/D-92-new.md", "text": text, "summary": S,
    }))
    found = events_now()
    assert len(found) == 1
    line = found[0]
    assert "\n" not in line
    assert f"{SEP}{al.WRITE_SERVER}{SEP}" in line
    assert what(line) == "propose_record docs/decisions/D-92-new.md"
    assert re.search(r"  kind applied, \d+ ms$", line), line
    assert "A sentence only the record holds." not in log_text()
    assert S not in log_text()

    asyncio.run(a.build_server().call_tool("amend_ruling", {
        "id": "D-90", "mode": "metadata",
        "text": write_record(last_verified="2026-08-10"), "summary": S,
    }))
    line = events_now()[-1]
    assert what(line) == "amend_ruling D-90"
    assert re.search(r"  kind applied, \d+ ms$", line), line
    assert "metadata" not in log_text()
    assert "2026-08-10" not in log_text()
    assert capsys.readouterr().out == ""


def test_the_write_server_writes_a_start_event(monkeypatch, capsys):
    """The same start event the read server writes, under this server's
    name: its version, the folder it pinned and its process id, one
    line, and nothing on stdout (Invariant 2)."""
    class Quiet:
        def run(self):
            return None

    monkeypatch.setattr(a, "build_server", lambda: Quiet())
    a.main()
    found = events_now()
    assert len(found) == 1
    assert "\n" not in found[0]
    assert f"{SEP}radialrig {mnt.installed_version()}{SEP}{al.WRITE_SERVER}{SEP}pid {os.getpid()}{SEP}" in found[0]
    assert what(found[0]) == "started"
    assert found[0].endswith(f"{SEP}folder {a.ROOT}")
    assert capsys.readouterr().out == ""


def test_the_wrapper_leaves_both_tool_lists_byte_for_byte():
    """Invariant 3, in substance: the wrapped registration lists the
    tools exactly as the bare functions would, under both measures."""
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    def bare(name, tools, **hints):
        server = MCPServer(name)
        for tool in tools:
            server.tool(annotations=ToolAnnotations(**hints))(tool)
        return asyncio.run(server.list_tools())

    def dumped(tools) -> str:
        return json.dumps([t.model_dump(exclude_none=True, by_alias=True) for t in tools])

    def budget(tools) -> int:
        return sum(len(t.model_dump_json(exclude_none=True, by_alias=True)) for t in tools)

    reads = bare("radialrig", rs.READ_TOOLS, read_only_hint=True,
                 destructive_hint=False, idempotent_hint=True, open_world_hint=False)
    served = asyncio.run(rs.build_server().list_tools())
    assert dumped(served) == dumped(reads)
    assert budget(served) == budget(reads)

    writes = bare("radialrig-write", a.VERBS, read_only_hint=False,
                  destructive_hint=False, idempotent_hint=False, open_world_hint=False)
    served = asyncio.run(a.build_server().list_tools())
    assert dumped(served) == dumped(writes)
    assert budget(served) == budget(writes)
    # And the served functions are wrapped ones, so the comparison is
    # not vacuous.
    assert all(hasattr(t.fn, "__wrapped__") for t in rs.build_server()._tool_manager.list_tools())


# --- the file: rotation, whole events, the tail ------------------------

def test_the_log_rotates_at_the_cap_into_one_earlier_file():
    assert al.CAP == 1 << 20
    path = log_file()
    big = ["x" * 1000] * 600

    al.record(al.COMMAND, "first", lines=big)
    al.record(al.COMMAND, "second", lines=big)
    assert path.stat().st_size >= al.CAP
    assert not path.with_name(al.OLDER_NAME).exists()

    al.record(al.COMMAND, "third", lines=["small"])
    older = path.with_name(al.OLDER_NAME)
    assert older.is_file() and older.stat().st_size >= al.CAP
    assert path.stat().st_size < 1000
    assert whats(al.last_events()) == ["first", "second", "third"]

    # The next turn replaces the earlier file rather than keeping a third,
    # so the two together never take much more than 2 MiB.
    al.record(al.COMMAND, "fourth", lines=big)
    al.record(al.COMMAND, "fifth", lines=big)
    al.record(al.COMMAND, "sixth", lines=["small"])
    assert set(sizes(path.parent)) == {al.LOG_NAME, al.OLDER_NAME, al.LOCK_NAME}
    assert whats(al.last_events()) == ["third", "fourth", "fifth", "sixth"]
    assert sum(sizes(path.parent).values()) < 2 * al.CAP + 10_000


def test_the_last_twenty_events_come_whole_across_both_files():
    path = log_file()
    text = [
        f"Mounted radialrig {mnt.installed_version()} onto somewhere",
        "",
        "created   AGENTS.md",
        "2026-01-01 00:00:00  radialrig 0.0.0  reads like a header, and is not one",
    ]
    for n in range(15):
        al.record(al.COMMAND, f"old {n}", lines=text)
    os.replace(path, path.with_name(al.OLDER_NAME))
    for n in range(10):
        al.record(al.COMMAND, f"new {n}", lines=text, error=RuntimeError("boom"))

    found = al.last_events()
    assert len(found) == 20
    assert whats(found) == [f"old {n}" for n in range(5, 15)] + [f"new {n}" for n in range(10)]
    for event in found:
        lines = event.splitlines()
        assert lines[1:5] == [f"{al.INDENT}{line}" for line in text], event
    for event in found[:10]:
        assert len(event.splitlines()) == 5
    for event in found[10:]:
        assert event.splitlines()[0].endswith(f"{SEP}error RuntimeError: boom")
        assert event.splitlines()[-1] == f"{al.INDENT}RuntimeError: boom"
    assert al.last_events(3) == found[-3:]
    assert al.TAIL == 20


# --- Invariant 1: a log failure changes nothing ------------------------

def test_nothing_changes_when_the_log_folder_is_unwritable(
    tmp_path, capsys, walk, root, monkeypatch,
):
    store = Path(os.environ[STORE_VAR])
    store.mkdir(parents=True, exist_ok=True)
    blocked = store / "radialrig"
    blocked.write_bytes(b"a file where the log folder goes\n")
    assert al.log_path() == blocked / al.LOG_NAME

    # The command line: the same exit code and the same report.
    first = _make_project(tmp_path / "one" / "host")
    code, out, err = run(capsys, "mount", str(first))
    assert (code, err) == (report.OK, "")
    assert mnt.is_mounted(first)

    # The page: the press lands, and the error boundary still answers.
    pressed = _make_project(tmp_path / "two" / "host")
    run_ = walk(pressed)
    run_.page()
    status, body, _ = run_.post({"token": run_.session.token, "action": "mount"})
    assert status == 200
    assert "Mounted onto host." in html.unescape(body)
    assert mnt.is_mounted(pressed)
    assert run_.wait() == report.OK

    # The tool: the same result as with a log that writes.
    write(root, "docs/decisions/D-90-test.md", record_text(body="The answer."))
    held = asyncio.run(rs.build_server().call_tool("get_ruling", {"query": "D-90"}))

    assert blocked.read_bytes() == b"a file where the log folder goes\n"
    assert not any(p.name.startswith("radialrig.log") for p in store.iterdir())

    # Now with a folder it can write: everything above says the same.
    blocked.unlink()
    second = _make_project(tmp_path / "three" / "host")
    code2, out2, err2 = run(capsys, "mount", str(second))
    assert (code2, err2) == (code, err)
    assert (out2.replace(str(second.resolve()), "<root>")
            == out.replace(str(first.resolve()), "<root>"))
    free = asyncio.run(rs.build_server().call_tool("get_ruling", {"query": "D-90"}))
    assert free == held
    assert log_file().is_file()
    assert whats(events_now()) == [f"radialrig mount {second}", "get_ruling"]


# --- several processes, and the lock budget ----------------------------

APPENDER = """
import sys
from radialrig import activity_log as al
tag, count = sys.argv[1], int(sys.argv[2])
for n in range(count):
    al.record(al.COMMAND, f"{tag} {n}", lines=["line one", "", "line three"])
"""


def test_several_processes_appending_at_once_leave_every_event_whole():
    path = log_file()
    writers, count = 4, 25
    started = [
        subprocess.Popen(
            [sys.executable, "-c", APPENDER, f"w{i}", str(count)],
            env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for i in range(writers)
    ]
    for proc in started:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err.decode("utf-8", "replace")
        assert out == b""

    found = al.last_events(10_000)
    assert len(found) == writers * count
    seen: dict[str, list[int]] = {f"w{i}": [] for i in range(writers)}
    for event in found:
        lines = event.splitlines()
        assert len(lines) == 4, event
        assert lines[1:] == [f"{al.INDENT}line one", al.INDENT, f"{al.INDENT}line three"]
        tag, n = what(event).split(" ")
        seen[tag].append(int(n))
    assert all(seen[tag] == list(range(count)) for tag in seen), seen
    assert not path.with_name(al.OLDER_NAME).exists()


def test_a_writer_held_off_the_lock_drops_its_event_without_raising():
    assert al.LOCK_BUDGET == 1.0
    al.record(al.COMMAND, "before")
    path = log_file()
    before = path.read_bytes()

    held = al._take(path.with_name(al.LOCK_NAME))
    assert held is not None
    try:
        started = time.monotonic()
        al.record(al.COMMAND, "dropped", lines=["never lands"])
        waited = time.monotonic() - started
    finally:
        al._release(held)
    assert al.LOCK_BUDGET <= waited < al.LOCK_BUDGET + 2, waited
    assert path.read_bytes() == before

    al.record(al.COMMAND, "after")
    assert whats(events_now()) == ["before", "after"]
    assert "never lands" not in log_text()


# --- prescription 5: `radialrig log` -------------------------------------

def test_radialrig_log_prints_where_the_log_is_and_its_last_twenty_events(
    tmp_path, capsys, monkeypatch,
):
    # No log yet: exit 4, the path, the version line, and no event.
    code, out, err = run(capsys, "log")
    assert (code, err) == (report.NOOP, "")
    lines = out.splitlines()
    assert lines[0] == f"Log: {log_file()}"
    assert lines[-1] == report.engine_line()
    assert not log_file().exists()

    for n in range(25):
        al.record(al.COMMAND, f"event {n}", lines=["one", "two"])
    code, out, err = run(capsys, "log")
    assert (code, err) == (report.OK, "")
    lines = out.splitlines()
    assert lines[0] == f"Log: {log_file()}"
    assert lines[1] == ""
    assert lines[-1] == report.engine_line()
    printed = al.events("\n".join(lines[2:-1]))
    assert len(printed) == 20
    assert whats(printed) == [f"event {n}" for n in range(5, 25)]
    assert all(event.splitlines()[1:] == [f"{al.INDENT}one", f"{al.INDENT}two"]
               for event in printed)
    # It wrote no event of its own.
    assert len(events_now()) == 25

    # No folder for it: exit 1, one stderr line, nothing on stdout.
    clear_store_env(monkeypatch)
    code, out, err = run(capsys, "log")
    assert (code, out) == (report.ERROR, "")
    assert err.startswith("radialrig: ") and len(err.splitlines()) == 1


def test_the_usage_the_readme_and_the_notes_name_the_log():
    assert "radialrig log" in cli.USAGE
    assert "log" in cli.RUNNERS
    parsed = cli.build_parser().parse_args(["log"])
    assert parsed.command == "log" and parsed.path == "."

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "| `radialrig log` |" in readme
    assert "`radialrig --version`" in readme
    for spelled in (
        r"%APPDATA%\radialrig\radialrig.log",
        "$XDG_DATA_HOME/radialrig/radialrig.log",
        "~/.local/share/radialrig/radialrig.log",
        "radialrig.log.1",
    ):
        assert spelled in readme, spelled

    template = (ROOT / "scripts" / "release-notes-template.md").read_text(encoding="utf-8")
    assert "the projects listing file, and the log files beside it" in template


# --- prescription 6: where to look, on the pages -------------------------

def test_the_failure_wording_and_the_removal_list_name_the_log(tmp_path, monkeypatch):
    path = log_file()
    note = setup_web.LOG_NOTE.format(path=path)
    line = setup_web.LOG_LINE.format(path=path)
    plain = ("radialrig setup could not answer that request. The terminal "
             "that started setup carries the detail.")

    assert setup_web.worded(setup_web.INTERNAL) == f"{plain} {note}"
    said = setup_web.worded(
        setup_web.INTERNAL, surface=manage_web.SURFACE, page=manage_web.PAGE_NAME)
    assert said.endswith(f" {note}") and "manage" in said and "setup" not in said
    # Only the one refusal carries the slot.
    for template in (setup_web.REFUSE_TOKEN, setup_web.REFUSE_SPENT,
                     setup_web.REFUSE_ORIGIN, setup_web.REFUSE_NOT_FOUND):
        assert str(path) not in setup_web.worded(template)

    session = setup_web.Session(tmp_path / "host", "scope", mnt.DEFAULT_OWNER)
    failed = setup_web.PageState(
        setup_web.FAILED, report.ERROR, True, message="the disk went away")
    assert line in html.unescape(setup_web._failed_body(session, failed))
    partial = setup_web.PageState(
        setup_web.FAILED, report.PARTIAL, True, message="one file is left")
    assert line in html.unescape(setup_web._failed_body(session, partial))
    connect_failed = setup_web.PageState(
        setup_web.CONNECT_FAILED, report.ERROR, True, message="no client")
    assert line in html.unescape(setup_web._connect_failed_body(session, connect_failed))

    act = manage_web.Action(
        name=manage_web.UNMOUNT, root=str(tmp_path / "host"),
        skill="", client="", key="", harness="")
    msession = manage_web.ManageSession()
    stopped = setup_web.PageState(
        manage_web.FAILED, report.ERROR, False, result=act,
        lines=(manage_web.ACTION_FAILED_HEAD,), message="Permission denied")
    assert line in html.unescape(manage_web._action_body(msession, stopped))
    assert line in html.unescape(manage_web._step_report(stopped))
    done = setup_web.PageState(
        manage_web.DONE, report.OK, False, result=act,
        lines=("Unmounted radialrig from somewhere - 1 path(s) removed, 0 left.",))
    assert line not in html.unescape(manage_web._action_body(msession, done))
    assert line not in html.unescape(manage_web._step_report(done))

    remains = html.unescape(manage_web._remains())
    assert manage_web.REMAINS_LOG.format(
        path=path, older=path.with_name(al.OLDER_NAME)) in remains
    assert manage_web.REMAINS_ROSTER.format(path=ro.roster_path()) in remains

    # No folder for it: the sentences say nothing about a log.
    clear_store_env(monkeypatch)
    assert setup_web.worded(setup_web.INTERNAL) == plain
    head = setup_web.LOG_LINE.split("{")[0]
    assert head not in setup_web._failed_body(session, failed)
    assert head not in setup_web._connect_failed_body(session, connect_failed)
    assert head not in manage_web._action_body(msession, stopped)
    assert manage_web.REMAINS_LOG.split("{")[0] not in manage_web._remains()


# --- Invariant 7: the developer's own log ----------------------------------

def test_a_suite_run_never_touches_the_developers_own_log(tmp_path, capsys):
    """The suite-wide isolation, stated where the log can break it, the
    way test_roster_hooks pins the roster."""
    real = Path.home() / "AppData" / "Roaming" / "radialrig" / "radialrig.log"
    before = real.read_bytes() if real.is_file() else None
    project = _make_project(tmp_path / "isolated")

    path = al.log_path()
    assert path is not None
    assert tmp_path in path.parents

    assert run(capsys, "unmount", str(project))[0] == report.NOOP

    assert path.is_file()
    assert (real.read_bytes() if real.is_file() else None) == before
