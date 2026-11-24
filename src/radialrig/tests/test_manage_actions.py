"""Per-action byte parity for `radialrig manage` — PLAN-0008 Phase 4.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_manage_actions.py -q

The oracle is the command line, once per press. A press on the page calls
the engine function the verb calls, so the bytes it leaves must be the
bytes the verb leaves on a twin built from the same maker, and the words
it prints must be the verb's own report lines. Where a record embeds this
project's absolute paths — the connect record does — a twin can never
compare, so that leg compares the same root twice instead: page, reverse,
command line.

Everything drives real HTTP against a real server on a real port.

Nothing here may reach this machine's own profile. `APPDATA` and
`LOCALAPPDATA` point inside tmp_path for every test in the suite
(conftest.py), and the fixtures below narrow `APPDATA` to one fake client
profile. The two variables are never deleted: the roster path is built
from `APPDATA`, and a test running without it would assert against a
roster that can never hold anything.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import shutil
import threading
import urllib.parse
from pathlib import Path

import pytest

from radialrig import cli
from radialrig import connect as cn
from radialrig import manage_web
from radialrig import mount as mnt
from radialrig import orphan
from radialrig import report
from radialrig import roster as ro
from radialrig import setup_web
from radialrig import skills as sk
from radialrig._shell import split_command
from radialrig.paths import ROOT_ENV
from radialrig.tests.test_manage_web import (  # noqa: F401 - helpers, by name
    CLIENT,
    GET_FORM,
    HIDDEN,
    PREFIX,
    SKILL,
    client_here,
    command,
    connections_of,
    drop_entry,
    env,
    expected_listing,
    grid_cells,
    grid_presses,
    listed,
    manage,
    result_of,
    roster_row,
    roster_rows,
    status_band,
    navigations,
    roster_file,
    rows,
    run,
    text_of,
    view,
)
from radialrig.tests.test_connect import (  # noqa: F401 - helpers, by name
    CONFIG_HOSTS,
    fake_client,
    roaming_config,
    store_config,
)
from radialrig.tests.test_setup_web import UNSAFE
from radialrig.tests.test_mount import (  # noqa: F401 - helpers, by name
    MOUNT_DAY,
    _make_project,
    snapshot,
    tree,
)

SCOPE = "twin-scope"
SKILL_DIR = ".claude/skills/session-handoff"

# A folder name with a space in it, which is the ordinary case on this
# machine: anything a reader keeps under their own name carries one.
SPACED = "My Project"

# The walkthrough's own form target, and every command block on a page.
FORM_TARGET = re.compile(r'<form method="post" action="([^"]+)">')
COPY = re.compile(r'data-copy="([^"]*)"')
FORM_HEAD = {"Content-Type": "application/x-www-form-urlencoded"}
# The marker the walkthrough's review form carries: a press posted with
# it says what the boxes hold, ticked or not (PLAN-0017 phase 4).
CHOSEN = {setup_web.CHOICES_FIELD: "1"}


# --- fixtures ---------------------------------------------------------

@pytest.fixture()
def fixed_day(monkeypatch):
    """One date for the page and its twin.

    The engine modules share one `datetime`, so one substitution reaches
    the mount record, the skills record and the connect record alike, and
    a run that crosses midnight cannot fail a byte comparison for the
    wrong reason.
    """

    class _Day(dt.date):
        @classmethod
        def today(cls):
            return MOUNT_DAY

    monkeypatch.setattr(mnt.dt, "date", _Day)
    return MOUNT_DAY


def _host(root: Path) -> Path:
    """A project that already has the two files a mount merges into, so
    the byte oracle covers the merge paths and not only the created ones.
    """
    _make_project(root)
    (root / "AGENTS.md").write_bytes(b"# Sample\n\nHouse rules.\n")
    (root / ".mcp.json").write_bytes(
        b'{\n    "mcpServers": {\n        "other": {"command": "elsewhere"}\n    }\n}\n'
    )
    return root.resolve()


# --- helpers ----------------------------------------------------------

def press(run_, fields: dict, *, confirm: bool = False) -> dict:
    """One press, as the form the page would have posted."""
    out = {"token": run_.session.token, **fields}
    if confirm:
        out["confirm"] = "yes"
    return out


def action_of(fields: dict) -> manage_web.Action:
    return manage_web.Action(
        fields["action"], root=fields.get("root", ""),
        skill=fields.get("skill", ""), client=fields.get("client", ""),
        key=fields.get("key", ""), harness=fields.get("harness", ""),
    )


# The first agent in the harness map, for the presses that name one
# (PLAN-0015 phase 2). Read off the map rather than spelled here.
AGENT = next(iter(sk.HARNESSES))


def says(page: str, lines) -> None:
    """Every line the command line printed reads back off the page.

    Whitespace is collapsed on both sides because the row block is drawn
    as a table here and padded into columns there. The words, their order
    and the row they belong to are the same either way. The engine line
    the command line ends on is skipped: the page carries the version in
    its masthead, outside the text read here (PLAN-0018 phase 1).
    """
    said = text_of(page)
    for line in lines:
        wanted = " ".join(line.split())
        if wanted and wanted != report.engine_line():
            assert wanted in said, wanted


def expected_rows(dispositions) -> list[tuple[str, str, str]]:
    return [
        (d.verb, d.path, manage_web._reason(d)) for d in dispositions
    ]


def clear(env: dict) -> None:
    """An empty roster before a press, so the press's own write is proven."""
    roster_file(env).unlink(missing_ok=True)


def roots(env: dict) -> list[str]:
    path = roster_file(env)
    if not path.is_file():
        return []
    body = json.loads(path.read_text(encoding="utf-8"))
    return [entry["root"] for entry in body["projects"]]


def opened(run_, root: Path) -> str:
    """The walkthrough view for a folder, opened the way the field does."""
    status, page, _ = run_.get(
        f"{manage_web.MANAGE_PATH}?s={run_.session.token}"
        f"&view={manage_web.SETUP}&root={urllib.parse.quote(str(root))}"
    )
    assert status == 200, page
    return page


def target_of(page: str) -> str:
    """Where a press goes: the form target the page itself drew."""
    found = FORM_TARGET.search(page)
    assert found is not None, "this state carries no press"
    return html.unescape(found.group(1))


def walk_press(run_, page: str, fields: dict):
    """One walkthrough press, posted to the target that page named."""
    return run_.request(
        "POST", target_of(page),
        body=urllib.parse.urlencode(fields, doseq=True), headers=FORM_HEAD,
    )


def boxes(*names: str, agents=None) -> list[str]:
    """What the walkthrough's boxes post for these skills: one value per
    (agent, skill) pair, for every agent in the map or the ones given."""
    return [setup_web.box_value(harness.name, name)
            for harness in (agents or sk.HARNESSES.values()) for name in names]


def commands_on(page: str) -> list[str]:
    return [html.unescape(text) for text in COPY.findall(page)]


def arguments(line: str) -> list[str]:
    """One command line, split the way the shell a reader pastes it into
    splits it. `posix=False` because these are Windows paths: a backslash
    separates directories here and escapes nothing, and a quoted argument
    keeps the quotes that made it one, which is what `split_command`
    strips."""
    return split_command(line, posix=False)


def loose_entry(config: Path, key: str, pinned: Path) -> None:
    """One client entry pinning a project root, as the client would hold it."""
    body = json.loads(config.read_text(encoding="utf-8"))
    body.setdefault("mcpServers", {})[key] = {
        "command": "x", "args": [], "env": {ROOT_ENV: str(pinned)},
    }
    config.write_text(json.dumps(body, indent=2), encoding="utf-8")


# --- unmount ----------------------------------------------------------

def test_a_page_unmount_matches_the_cli_byte_for_byte(
    tmp_path, env, capsys, manage, fixed_day,
):
    page_root = _host(tmp_path / "page-host")
    twin = _host(tmp_path / "twin-host")
    mnt.mount(page_root, scope=SCOPE)
    mnt.mount(twin, scope=SCOPE)
    assert tree(page_root) == tree(twin)

    run_ = manage()
    fields = {"action": manage_web.UNMOUNT, "root": str(page_root)}
    status, plan, _ = run_.post(press(run_, fields))
    assert status == 200
    assert tree(page_root) == tree(twin), "the plan press wrote something"

    status, done, _ = run_.post(press(run_, fields, confirm=True))
    assert status == 200

    code, out, err = run(capsys, "unmount", str(twin))
    assert (code, err) == (report.OK, "")
    assert tree(page_root) == tree(twin)
    assert snapshot(page_root) == snapshot(twin)
    says(done, out.replace(str(twin), str(page_root)).splitlines())


# --- the skills kit ---------------------------------------------------

def test_a_page_skill_add_and_remove_match_the_engine_byte_for_byte(
    tmp_path, env, manage, fixed_day,
):
    page_root = _host(tmp_path / "page-host")
    twin = _host(tmp_path / "twin-host")
    mnt.mount(page_root, scope=SCOPE)
    mnt.mount(twin, scope=SCOPE)

    run_ = manage()
    add = {"action": manage_web.SKILL_ADD, "root": str(page_root), "skill": SKILL}
    assert run_.post(press(run_, add))[0] == 200
    assert tree(page_root) == tree(twin), "the plan press wrote something"

    status, done, _ = run_.post(press(run_, add, confirm=True))
    assert status == 200
    results = sk.add_all(twin, SKILL)
    written = [d for r in results for d in r.dispositions]
    assert tree(page_root) == tree(twin)
    assert snapshot(page_root) == snapshot(twin)
    assert rows(done) == expected_rows(written)
    says(done, [line.replace(str(twin), str(page_root))
                for line in report.skill_add_reports(results)])

    drop = {"action": manage_web.SKILL_REMOVE, "root": str(page_root),
            "skill": SKILL}
    assert run_.post(press(run_, drop))[0] == 200
    assert tree(page_root) == tree(twin), "the plan press wrote something"

    status, gone, _ = run_.post(press(run_, drop, confirm=True))
    assert status == 200
    plans = sk.remove_all(twin, SKILL)
    removed = [d for p in plans for d in p.dispositions]
    assert tree(page_root) == tree(twin)
    assert rows(gone) == expected_rows(removed)
    says(gone, [line.replace(str(twin), str(page_root))
                for line in report.skill_remove_reports(plans)])


def test_a_page_press_naming_an_agent_matches_the_narrowed_engine_byte_for_byte(
    tmp_path, env, manage, fixed_day,
):
    """PLAN-0015 phase 2: a press from one agent's list acts on that
    agent alone, through the same engine call `--harness` narrows, and
    the whole-catalog press is `skill add --all` narrowed the same way."""
    page_root = _host(tmp_path / "page-host")
    twin = _host(tmp_path / "twin-host")
    mnt.mount(page_root, scope=SCOPE)
    mnt.mount(twin, scope=SCOPE)
    run_ = manage()

    add = {"action": manage_web.SKILL_ADD, "root": str(page_root), "skill": SKILL,
           "harness": AGENT}
    assert run_.post(press(run_, add))[0] == 200
    assert tree(page_root) == tree(twin), "the plan press wrote something"
    status, done, _ = run_.post(press(run_, add, confirm=True))
    assert status == 200
    results = sk.add_all(twin, SKILL, targets=(AGENT,))
    assert [r.harness.name for r in results] == [AGENT]
    assert tree(page_root) == tree(twin)
    assert snapshot(page_root) == snapshot(twin)
    assert rows(done) == expected_rows([d for r in results for d in r.dispositions])
    says(done, [line.replace(str(twin), str(page_root))
                for line in report.skill_add_reports(results)])
    assert f"radialrig skill add {SKILL} {page_root} --harness {AGENT}" in commands_on(done)

    every = {"action": manage_web.SKILL_ADD_ALL, "root": str(page_root),
             "harness": AGENT}
    assert run_.post(press(run_, every))[0] == 200
    assert tree(page_root) == tree(twin), "the plan press wrote something"
    status, done, _ = run_.post(press(run_, every, confirm=True))
    assert status == 200
    results = sk.add_catalog(twin, targets=(AGENT,))
    assert {r.harness.name for r in results} == {AGENT}
    assert tree(page_root) == tree(twin)
    assert snapshot(page_root) == snapshot(twin)
    # Every row once, as the engine gave it: the record's row stands in
    # the first block that writes it and in none after (PLAN-0015
    # constraint 4). The skill already standing is nothing the press
    # wrote, so its rows are not drawn. The page's words are the
    # whole-catalog report's, one head for the agent rather than one
    # block per skill.
    assert [r.current for r in results].count(True) == 1
    written = [d for r in results if not r.current for d in r.dispositions]
    assert len(written) == len(set(written))
    assert [d.path for d in written].count(sk.harness_for(AGENT).record) == 1
    assert rows(done) == expected_rows(written)
    says(done, [line.replace(str(twin), str(page_root))
                for line in report.skill_add_all_report(results)])
    assert text_of(done).count("Added ") == 1
    assert f"radialrig skill add --all {page_root} --harness {AGENT}" in commands_on(done)
    for harness in sk.HARNESSES.values():
        assert (page_root / harness.skills).is_dir() == (harness.name == AGENT)

    drop = {"action": manage_web.SKILL_REMOVE, "root": str(page_root),
            "skill": SKILL, "harness": AGENT}
    status, gone, _ = run_.post(press(run_, drop, confirm=True))
    assert status == 200
    plans = sk.remove_all(twin, SKILL, targets=(AGENT,))
    assert tree(page_root) == tree(twin)
    assert rows(gone) == expected_rows([d for p in plans for d in p.dispositions])
    assert f"radialrig skill remove {SKILL} {page_root} --harness {AGENT}" in commands_on(gone)


# --- the connect kit --------------------------------------------------

def test_a_page_connect_and_unconnect_match_the_engine_byte_for_byte(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Two comparisons, because the two verbs can carry different ones.

    A connect record names this project's own absolute root and the
    absolute path of every settings copy it wrote, so no two roots can
    ever hold the same bytes: the connect leg runs page, reverse, command
    line, on one root against one profile. The reversal's end state has
    no such record left in it, so it compares across twins as usual.
    """
    root = _host(tmp_path / "page-host")
    twin = _host(tmp_path / "twin-host")
    mnt.mount(root, scope=SCOPE)
    mnt.mount(twin, scope=SCOPE)
    pristine = client_here.read_bytes()

    run_ = manage()
    fields = {"action": manage_web.CONNECT, "root": str(root), "client": CLIENT}
    assert run_.post(press(run_, fields))[0] == 200
    assert tree(root) == tree(twin), "the plan press wrote something"
    assert client_here.read_bytes() == pristine

    status, done, _ = run_.post(press(run_, fields, confirm=True))
    assert status == 200
    page_tree = tree(root)
    page_config = client_here.read_bytes()
    assert client_here.read_bytes() != pristine

    assert run(capsys, "unconnect", CLIENT, str(root))[0] == report.OK
    assert client_here.read_bytes() == pristine
    code, out, err = run(capsys, "connect", CLIENT, str(root))
    assert (code, err) == (report.OK, "")
    assert tree(root) == page_tree
    assert client_here.read_bytes() == page_config
    says(done, out.splitlines())

    # The twin registers against a profile of its own, so the reversal
    # this test compares is the one each root can actually run.
    other = tmp_path / "other-profile"
    (other / "Claude").mkdir(parents=True)
    cn.connect(twin, CLIENT, env={"APPDATA": str(other)})

    fields = {"action": manage_web.UNCONNECT, "root": str(root),
              "client": CLIENT}
    assert run_.post(press(run_, fields))[0] == 200
    assert client_here.read_bytes() == page_config, "the plan press wrote something"

    expected = report.unconnect_report(cn.unconnect_plan(root, CLIENT))
    status, undone, _ = run_.post(press(run_, fields, confirm=True))
    assert status == 200
    cn.unconnect(twin, CLIENT, env={"APPDATA": str(other)})
    assert tree(root) == tree(twin)
    assert client_here.read_bytes() == pristine
    says(undone, expected)

    # The client's own restart line is last, as it is on the terminal.
    said = text_of(undone)
    restart = cn.CLIENTS[CLIENT].restart
    assert said.index(report.CONNECT_CLEAN_TAIL) < said.index(restart)


def test_a_wrote_table_fills_the_cells_the_command_line_leaves_empty(
    tmp_path, env, client_here, manage, fixed_day,
):
    """The page's own sentences for the codes the engine leaves wordless.

    On the command line "created <path>" is a whole line; in a
    three-column table the empty cell reads as a hole. The fill is the
    page's, never the engine's: `REASONS` keeps its empty wordings, and
    the sentences are pinned here word for word like every other
    user-facing sentence.
    """
    assert setup_web.WROTE_REASONS == {
        mnt.SKILL_ADDED:
            "one new skill folder; every file in it is this package's",
        mnt.SKILL_RECORD_WRITTEN:
            "the record RadialRig uses to take everything back out",
        mnt.CONNECT_RECORD_WRITTEN:
            "the record RadialRig uses to take the connection back out",
    }
    for code in setup_web.WROTE_REASONS:
        assert mnt.REASONS[code] == "", code

    root = _host(tmp_path / "page-host")
    mnt.mount(root, scope=SCOPE)
    run_ = manage()

    add = {"action": manage_web.SKILL_ADD, "root": str(root), "skill": SKILL}
    status, done, _ = run_.post(press(run_, add, confirm=True))
    assert status == 200
    said = text_of(done)
    assert setup_web.WROTE_REASONS[mnt.SKILL_ADDED] in said
    assert setup_web.WROTE_REASONS[mnt.SKILL_RECORD_WRITTEN] in said
    assert '<td class="why"></td>' not in done

    fields = {"action": manage_web.CONNECT, "root": str(root),
              "client": CLIENT}
    status, done, _ = run_.post(press(run_, fields, confirm=True))
    assert status == 200
    assert setup_web.WROTE_REASONS[mnt.CONNECT_RECORD_WRITTEN] in text_of(done)
    assert '<td class="why"></td>' not in done


# --- the entry whose project is gone ----------------------------------

def test_a_page_orphan_removal_matches_the_engine_byte_for_byte(
    tmp_path, env, client_here, manage,
):
    key = f"{PREFIX}old"
    body = {"mcpServers": {
        key: {"command": "x", "args": [],
              "env": {ROOT_ENV: str(tmp_path / "vanished")}},
        "someone-elses": {"command": "y"},
    }}
    original = (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    client_here.write_bytes(original)

    run_ = manage()
    fields = {"action": manage_web.ORPHAN, "client": CLIENT, "key": key}
    assert run_.post(press(run_, fields))[0] == 200
    assert client_here.read_bytes() == original, "the plan press wrote something"

    expected = report.orphan_report(
        orphan.remove_plan(CLIENT, key), resolve=orphan.reason_text)
    status, done, _ = run_.post(press(run_, fields, confirm=True))
    assert status == 200
    page_bytes = client_here.read_bytes()
    assert key not in page_bytes.decode("utf-8")
    assert "someone-elses" in page_bytes.decode("utf-8")

    # The same removal, run by the engine over the same bytes.
    client_here.write_bytes(original)
    orphan.remove(CLIENT, key)
    assert client_here.read_bytes() == page_bytes
    says(done, expected)

    # Both refusals read as the command line's, and neither writes.
    client_here.write_bytes(original)
    standing = _make_project(tmp_path / "standing").resolve()
    loose_entry(client_here, f"{PREFIX}live", standing)
    guarded = client_here.read_bytes()

    status, refused, _ = run_.post(press(
        run_, {"action": manage_web.ORPHAN, "client": CLIENT,
               "key": "someone-elses"}, confirm=True))
    assert status == 200
    said = text_of(refused)
    assert report.ORPHAN_REFUSAL_HEAD in said
    assert report.orphan_outside_prefix(cn.CLIENTS[CLIENT]) in said

    status, alive, _ = run_.post(press(
        run_, {"action": manage_web.ORPHAN, "client": CLIENT,
               "key": f"{PREFIX}live"}, confirm=True))
    assert status == 200
    said = text_of(alive)
    assert report.ORPHAN_REFUSAL_HEAD in said
    assert report.orphan_root_alive(cn.CLIENTS[CLIENT], str(standing)) in said
    assert client_here.read_bytes() == guarded


# --- the first press is a rehearsal -----------------------------------

def test_the_plan_state_writes_nothing(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    root = _host(tmp_path / "host")
    assert run(capsys, "mount", str(root))[0] == report.OK
    assert run(capsys, "skill", "add", SKILL, str(root))[0] == report.OK
    assert run(capsys, "connect", CLIENT, str(root))[0] == report.OK
    key = f"{PREFIX}old"
    loose_entry(client_here, key, tmp_path / "vanished")

    before = tree(root)
    config = client_here.read_bytes()
    run_ = manage()
    for fields in (
        {"action": manage_web.UNMOUNT, "root": str(root)},
        {"action": manage_web.SKILL_ADD, "root": str(root), "skill": SKILL},
        {"action": manage_web.SKILL_ADD_ALL, "root": str(root), "harness": AGENT},
        {"action": manage_web.SKILL_REMOVE, "root": str(root), "skill": SKILL},
        {"action": manage_web.CONNECT, "root": str(root), "client": CLIENT},
        {"action": manage_web.UNCONNECT, "root": str(root), "client": CLIENT},
        {"action": manage_web.ORPHAN, "client": CLIENT, "key": key},
    ):
        status, page, _ = run_.post(press(run_, fields))
        assert status == 200, fields
        assert tree(root) == before, fields
        assert client_here.read_bytes() == config, fields
        # And it says what the confirm press would do, beside its command.
        assert 'name="confirm"' in page, fields
        assert f'data-copy="{action_of(fields).command}"' in page, fields


# --- the roster side entry --------------------------------------------

def test_actions_maintain_the_roster_side_entry(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """The page presses the seams `cli.py` presses, and no others.

    Unmount, connect and unconnect keep the entry on a real run and on an
    already-done exit alike; the two skill verbs keep none, because the
    command line keeps none.
    """
    standing = _make_project(tmp_path / "standing").resolve()
    untouched = _make_project(tmp_path / "untouched").resolve()
    assert run(capsys, "mount", str(standing))[0] == report.OK
    assert run(capsys, "skill", "add", SKILL, str(standing))[0] == report.OK
    assert run(capsys, "connect", CLIENT, str(standing))[0] == report.OK

    run_ = manage()
    for fields, root in (
        # The already-done exits first, while the project still stands.
        ({"action": manage_web.CONNECT, "root": str(standing),
          "client": CLIENT}, standing),
        ({"action": manage_web.UNMOUNT, "root": str(untouched)}, untouched),
        ({"action": manage_web.UNCONNECT, "root": str(untouched),
          "client": CLIENT}, untouched),
        # Then the real runs, each reversing the last.
        ({"action": manage_web.UNCONNECT, "root": str(standing),
          "client": CLIENT}, standing),
        ({"action": manage_web.CONNECT, "root": str(standing),
          "client": CLIENT}, standing),
        ({"action": manage_web.UNMOUNT, "root": str(standing)}, standing),
    ):
        clear(env)
        assert run_.post(press(run_, fields, confirm=True))[0] == 200, fields
        assert roots(env) == [str(root)], fields

    # The two skill verbs, on a project the roster has never heard of.
    quiet = _make_project(tmp_path / "quiet").resolve()
    mnt.mount(quiet, scope=SCOPE)
    for fields in (
        {"action": manage_web.SKILL_ADD, "root": str(quiet), "skill": SKILL},
        {"action": manage_web.SKILL_REMOVE, "root": str(quiet), "skill": SKILL},
    ):
        clear(env)
        assert run_.post(press(run_, fields, confirm=True))[0] == 200, fields
        assert roots(env) == [], fields


# --- refusals carry no way past themselves ----------------------------

def test_a_refusal_offers_no_force_affordance(tmp_path, env, manage):
    root = _make_project(tmp_path / "host").resolve()
    (root / SKILL_DIR).mkdir(parents=True)
    (root / SKILL_DIR / "ours.md").write_bytes(b"our own skill\n")
    before = tree(root)

    run_ = manage()
    fields = {"action": manage_web.SKILL_ADD, "root": str(root), "skill": SKILL}
    for posted in (press(run_, fields), press(run_, fields, confirm=True)):
        status, refused, _ = run_.post(posted)
        assert status == 200
        said = text_of(refused)
        assert report.refusal_head(1) in said
        assert report.SKILL_FOREIGN_TAIL in said
        assert mnt.REASONS[mnt.SKILL_DIR_FOREIGN] in said
        # Nothing here presses again. The command line has no force flag,
        # so the page carries no control that stands in for one.
        body = main_of(refused)
        assert 'method="post"' not in body
        assert 'name="confirm"' not in body
        assert tree(root) == before, "the refusal wrote something"


# --- the already-done exits -------------------------------------------

def test_already_done_exits_read_as_the_clis(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    untouched = _make_project(tmp_path / "untouched").resolve()
    connected = _make_project(tmp_path / "connected").resolve()
    assert run(capsys, "mount", str(connected))[0] == report.OK
    assert run(capsys, "connect", CLIENT, str(connected))[0] == report.OK

    run_ = manage()
    for fields, argv in (
        ({"action": manage_web.UNMOUNT, "root": str(untouched)},
         ("unmount", str(untouched))),
        ({"action": manage_web.UNCONNECT, "root": str(untouched),
          "client": CLIENT}, ("unconnect", CLIENT, str(untouched))),
        ({"action": manage_web.CONNECT, "root": str(connected),
          "client": CLIENT}, ("connect", CLIENT, str(connected))),
    ):
        status, done, _ = run_.post(press(run_, fields, confirm=True))
        assert status == 200, fields
        code, out, err = run(capsys, *argv)
        assert (code, err) == (report.NOOP, ""), (fields, out)
        says(done, out.splitlines())


# --- the project view -------------------------------------------------

def test_the_project_view_offers_only_what_the_facts_leave(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    root = _make_project(tmp_path / "host").resolve()
    run_ = manage()

    def opened() -> str:
        status, page, _ = run_.get(
            f"{manage_web.MANAGE_PATH}?s={run_.session.token}"
            f"&view={manage_web.PROJECT}&root={root}")
        assert status == 200, page
        return page

    # Nothing done yet: no removal is offered, every cell reads Off, and
    # the one connection this machine could make is.
    page = opened()
    label = cn.CLIENTS[CLIENT].label
    assert manage_web.REMOVE_PROJECT_LABEL not in page
    cells = grid_cells(page)
    assert {cells[(SKILL, h.label)] for h in sk.HARNESSES.values()} == {("off", "Off")}
    connections = connections_of(page)
    assert f'aria-label="{manage_web.CONNECT_LABEL.format(label=label)}"' in connections
    assert f'value="{manage_web.CONNECT}"' in connections
    assert f'value="{manage_web.UNCONNECT}"' not in page

    assert run(capsys, "mount", str(root))[0] == report.OK
    assert run(capsys, "skill", "add", SKILL, str(root))[0] == report.OK
    assert run(capsys, "connect", CLIENT, str(root))[0] == report.OK

    page = opened()
    said = text_of(page)
    # The state is the status band, the grid and the connection row; a
    # press's rows are the only table of today's kind, and none is here.
    assert rows(page) == []
    assert '<table class="plan' not in main_of(page)
    assert status_band(page) == [("ok", "RadialRig is working in this project.", "")]
    cells = grid_cells(page)
    assert {cells[(SKILL, h.label)] for h in sk.HARNESSES.values()} == {("on", "On")}
    assert SKILL in said
    assert manage_web.REMOVE_PROJECT_LABEL in page
    connections = connections_of(page)
    assert f'aria-label="{manage_web.UNCONNECT_LABEL.format(label=label)}"' in connections
    assert f'value="{manage_web.UNCONNECT}"' in connections
    # Connected already, so the connection is not offered twice.
    assert f'value="{manage_web.CONNECT}"' not in page
    assert manage_web.CONNECT_LABEL.format(label=label) not in page
    assert str(root) in said


def test_the_project_view_says_the_client_is_not_found_where_none_is_detected(
    tmp_path, monkeypatch, manage,
):
    """A profile with no client directory in it, still inside tmp_path.

    `APPDATA` is narrowed rather than removed: the roster path is built
    from it too, and a run without it would assert against a page that
    could never list anything.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "no-client"))
    root = _make_project(tmp_path / "bare").resolve()
    mnt.mount(root, scope=SCOPE)
    run_ = manage()
    status, page, _ = run_.get(
        f"{manage_web.MANAGE_PATH}?s={run_.session.token}"
        f"&view={manage_web.PROJECT}&root={root}")
    assert status == 200
    assert cn.detect(cn.CLIENTS[CLIENT]) == ()
    # The connection row says the client isn't here, by the table's
    # label, and offers nothing; the label is the only mention.
    for client in cn.CLIENTS.values():
        assert page.count(client.label) == 1
        assert (f'<div class="connection"><strong>{client.label}</strong>'
                f'<span class="state">{manage_web.NOT_FOUND_STATE}</span></div>') in page
    assert f'value="{manage_web.CONNECT}"' not in page
    assert manage_web.REMOVE_PROJECT_LABEL in page


def test_a_press_with_a_hole_in_it_names_no_command(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """A block a reader copies has to run the thing the button ran.

    Every one of these verbs defaults its path to the directory it is run
    in, so a command rendered with an empty field — `radialrig unmount`
    with nothing after it, `radialrig skill add` with a gap where the name
    goes — is a line that does something else wherever it is pasted. A
    press that arrived without a field its command names offers no block
    at all, and it still says what it did.
    """
    root = _host(tmp_path / "host")
    assert run(capsys, "mount", str(root))[0] == report.OK
    before = tree(root)

    whole = manage_web.Action(manage_web.UNMOUNT, root=str(root))
    assert whole.named is True
    for holed in (
        manage_web.Action(manage_web.UNMOUNT),
        manage_web.Action(manage_web.SKILL_ADD, root=str(root)),
        manage_web.Action(manage_web.SKILL_REMOVE, skill=SKILL),
        manage_web.Action(manage_web.CONNECT, root=str(root)),
        manage_web.Action(manage_web.ORPHAN, client=CLIENT),
    ):
        assert holed.named is False, holed
        # The hole is real: the command it would have rendered carries it.
        assert "  " in holed.command or holed.command.endswith(" "), holed

    run_ = manage()
    for fields in (
        {"action": manage_web.UNMOUNT, "root": ""},
        {"action": manage_web.SKILL_ADD, "root": str(root), "skill": ""},
        {"action": manage_web.ORPHAN, "client": CLIENT, "key": ""},
    ):
        for confirm in (False, True):
            status, page, _ = run_.post(press(run_, fields, confirm=confirm))
            assert status == 200, (fields, confirm)
            for copied in COPY.findall(page):
                assert "  " not in copied, (fields, copied)
                assert not copied.endswith(" "), (fields, copied)
            assert tree(root) == before, (fields, confirm)

    # A press that really did fail still says so, in its own head.
    status, failed, _ = run_.post(press(
        run_, {"action": manage_web.SKILL_ADD, "root": str(root), "skill": ""},
        confirm=True))
    assert manage_web.ACTION_FAILED_HEAD in text_of(failed)
    assert run_.running


def test_a_press_that_names_no_project_never_reaches_this_directory(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    r"""The field a press has to guard is not only the empty one.

    `Path("")` is `Path(".")`, and every relative path resolves against
    the directory this server was started in. So the working directory
    here is a real mounted project: a press that named `.` rather than a
    project would unmount it, and one that named `..` would find no mount
    above it and write that directory into the roster instead. Both are
    the project nobody named.

    The rehearsal and the run are both driven, because the rehearsal is
    the half a reader presses first and the run is the half that writes.
    """
    here = _make_project(tmp_path / "cwd-project").resolve()
    assert run(capsys, "mount", str(here))[0] == report.OK
    monkeypatch.chdir(here)
    before = tree(here)
    listed = roots(env)
    assert listed == [str(here)]

    run_ = manage()
    for root in ("", ".", ".."):
        for confirm in (False, True):
            status, page, _ = run_.post(press(
                run_, {"action": manage_web.UNMOUNT, "root": root},
                confirm=confirm))
            assert status == 200, (root, confirm)
            assert mnt.is_mounted(here), (root, confirm)
            assert tree(here) == before, (root, confirm)
            assert roots(env) == listed, (root, confirm)
            # The listing, which is where a reader picks a project by
            # name. A folder that is standing is never called missing.
            said = text_of(page)
            assert manage_web.ROSTER_HEAD in said, (root, confirm)
            assert "no such directory" not in said, (root, confirm)

    # The same press naming that project by its full path still runs.
    status, done, _ = run_.post(press(
        run_, {"action": manage_web.UNMOUNT, "root": str(here)}, confirm=True))
    assert status == 200
    assert not mnt.is_mounted(here)
    assert manage_web.ROSTER_HEAD not in text_of(done)
    assert run_.running


# --- the parity table --------------------------------------------------

def test_every_dispatched_action_has_a_command_row(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    assert set(manage_web.RUNNERS) <= set(manage_web.COMMANDS)

    # The orphan row is the roster's own spelling, not a second one.
    loose = ro.Orphan(client=CLIENT, key=f"{PREFIX}old", root="x", copies=())
    assert manage_web.Action(
        manage_web.ORPHAN, client=loose.client, key=loose.key,
    ).command == ro.orphan_command(loose)

    root = _host(tmp_path / "host")
    assert run(capsys, "mount", str(root))[0] == report.OK
    key = f"{PREFIX}old"
    loose_entry(client_here, key, tmp_path / "vanished")

    run_ = manage()
    seen: set[str] = set()
    for fields in (
        {"action": manage_web.SKILL_ADD, "root": str(root), "skill": SKILL},
        {"action": manage_web.SKILL_REMOVE, "root": str(root), "skill": SKILL},
        # The whole-catalog row (PLAN-0015 phase 2), narrowed to the one
        # agent a page press names; its command says so.
        {"action": manage_web.SKILL_ADD_ALL, "root": str(root), "harness": AGENT},
        {"action": manage_web.CONNECT, "root": str(root), "client": CLIENT},
        {"action": manage_web.UNCONNECT, "root": str(root), "client": CLIENT},
        {"action": manage_web.ORPHAN, "client": CLIENT, "key": key},
        # The two update presses (PLAN-0021 phase 1): one project, and
        # every listed project.
        {"action": manage_web.UPDATE, "root": str(root)},
        {"action": manage_web.UPDATE_ALL},
        {"action": manage_web.UNMOUNT, "root": str(root)},
    ):
        # The first press carries the command it runs: today's plan page,
        # or, for the big changes, Confirm a big change behind its
        # switch (PLAN-0027 phase 3). The confirm press then runs.
        status, plan, _ = run_.post(press(run_, fields))
        assert status == 200, fields
        assert f'data-copy="{action_of(fields).command}"' in plan, fields
        status, _, _ = run_.post(press(run_, fields, confirm=True))
        assert status == 200, fields
        seen.add(fields["action"])
    assert seen == set(manage_web.COMMANDS)
    assert manage_web.COMMANDS[manage_web.SKILL_ADD_ALL] == "radialrig skill add --all {root}"
    assert manage_web.COMMANDS[manage_web.UPDATE] == "radialrig upgrade {root}"
    assert manage_web.COMMANDS[manage_web.UPDATE_ALL] == "radialrig upgrade --all"
    assert manage_web.SKILL_ACTIONS == (
        manage_web.SKILL_ADD, manage_web.SKILL_ADD_ALL, manage_web.SKILL_REMOVE)
    # The agent rides as a suffix on the skill verbs alone: a press
    # naming none is the bare verb over every agent, and a client verb
    # never carries one.
    for name in manage_web.SKILL_ACTIONS:
        narrowed = manage_web.Action(name, root="x", skill=SKILL, harness=AGENT)
        bare = manage_web.Action(name, root="x", skill=SKILL)
        assert narrowed.command == f"{bare.command} --harness {AGENT}"
        assert narrowed.targets == (AGENT,) and bare.targets is None
        assert ("harness", AGENT) in narrowed.fields
    assert manage_web.Action(manage_web.CONNECT, root="x", client=CLIENT,
                             harness=AGENT).command == f"radialrig connect {CLIENT} x"

    # The two sequence presses stand outside the table, as the walkthrough's
    # do, because each is several verbs and the view names every one of
    # them beside its button. The repair (PLAN-0014 phase 3) is
    # `COMMANDS[UNCONNECT]` then `COMMANDS[CONNECT]`, filled with the
    # client the record claims and the root: its plan page and its
    # report page carry both, in that order, in full.
    assert set(manage_web.SEQUENCES) == {manage_web.REVERSAL, manage_web.REPAIR}
    assert not set(manage_web.SEQUENCES) & set(manage_web.COMMANDS)
    crossed = _host(tmp_path / "crossed")
    assert run(capsys, "mount", str(crossed))[0] == report.OK
    assert run(capsys, "connect", CLIENT, str(crossed))[0] == report.OK
    settings = json.loads(client_here.read_text(encoding="utf-8"))
    del settings["mcpServers"][json.loads(
        cn.record_path(crossed).read_text(encoding="utf-8"))["key"]]
    client_here.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    facts = ro.verify(crossed)
    assert facts.connected == ro.CONNECTED_MISMATCH
    both = [
        manage_web.COMMANDS[manage_web.UNCONNECT].format(client=CLIENT, root=crossed),
        manage_web.COMMANDS[manage_web.CONNECT].format(client=CLIENT, root=crossed),
    ]
    assert [act.command for act in manage_web.repair_steps(facts)] == both
    repair = {"action": manage_web.REPAIR, "root": str(crossed)}
    for confirm in (False, True):
        status, page, _ = run_.post(press(run_, repair, confirm=confirm))
        assert status == 200, confirm
        assert commands_on(page) == both, confirm
    assert ro.verify(crossed).connected == CLIENT


def test_every_command_over_a_spaced_path_parses_as_one_argument(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    r"""A block a reader copies has to survive the shell they paste it into.

    A project under a folder with a space in its name is the ordinary case
    on this machine — `C:\Users\First Last\proj` is where a reader keeps
    one — and the line the page draws beside a press is the page's parity
    claim. Pasted bare, that line reaches the argument parser as two
    arguments and is refused, so the claim would be false exactly where a
    reader most needs it.
    """
    root = _host(tmp_path / SPACED)
    assert " " in str(root)
    assert run(capsys, "mount", str(root))[0] == report.OK
    key = f"{PREFIX}old"
    loose_entry(client_here, key, tmp_path / "vanished")

    run_ = manage()
    for fields in (
        {"action": manage_web.SKILL_ADD, "root": str(root), "skill": SKILL,
         "harness": AGENT},
        {"action": manage_web.SKILL_ADD_ALL, "root": str(root), "harness": AGENT},
        {"action": manage_web.SKILL_REMOVE, "root": str(root), "skill": SKILL,
         "harness": AGENT},
        {"action": manage_web.CONNECT, "root": str(root), "client": CLIENT},
        {"action": manage_web.UNCONNECT, "root": str(root), "client": CLIENT},
        {"action": manage_web.ORPHAN, "client": CLIENT, "key": key},
        {"action": manage_web.UNMOUNT, "root": str(root)},
    ):
        status, done, _ = run_.post(press(run_, fields, confirm=True))
        assert status == 200, fields
        line = action_of(fields).command
        assert line in commands_on(done), fields
        # Every field the press named is one whole argument of that line.
        parts = arguments(line)
        assert parts[0] == "radialrig", fields
        for value in (fields.get("root"), fields.get("skill"),
                      fields.get("client"), fields.get("key"),
                      fields.get("harness")):
            if value:
                assert parts.count(value) == 1, (fields, parts)

    # A slot with no space in it renders the line it always rendered, so
    # every command block a reader has copied before stands unchanged.
    # Nothing here is read off the disk: a command is a string either way.
    plain = r"C:\projects\plain"
    for act, line in (
        (manage_web.Action(manage_web.UNMOUNT, root=plain),
         f"radialrig unmount {plain}"),
        (manage_web.Action(manage_web.SKILL_ADD, root=plain, skill=SKILL),
         f"radialrig skill add {SKILL} {plain}"),
        (manage_web.Action(manage_web.SKILL_ADD, root=plain, skill=SKILL, harness=AGENT),
         f"radialrig skill add {SKILL} {plain} --harness {AGENT}"),
        (manage_web.Action(manage_web.SKILL_ADD_ALL, root=plain, harness=AGENT),
         f"radialrig skill add --all {plain} --harness {AGENT}"),
        (manage_web.Action(manage_web.SKILL_REMOVE, root=plain, skill=SKILL),
         f"radialrig skill remove {SKILL} {plain}"),
        (manage_web.Action(manage_web.SKILL_REMOVE, root=plain, skill=SKILL, harness=AGENT),
         f"radialrig skill remove {SKILL} {plain} --harness {AGENT}"),
        (manage_web.Action(manage_web.CONNECT, root=plain, client=CLIENT),
         f"radialrig connect {CLIENT} {plain}"),
        (manage_web.Action(manage_web.UNCONNECT, root=plain, client=CLIENT),
         f"radialrig unconnect {CLIENT} {plain}"),
        (manage_web.Action(manage_web.ORPHAN, client=CLIENT, key=key),
         f"radialrig unconnect {CLIENT} --orphan {key}"),
    ):
        assert act.command == line, act
        assert '"' not in act.command, act


# --- Add a project, inside the app (PLAN-0027 phase 2) ------------------
# The oracle is still the three verbs one press runs: mount, then skill
# add per ticked box, then connect. The first two compare across twins;
# the connection compares one root twice, the idiom above, because a
# connect record names this project's own absolute paths and no twin can
# hold them. What the app adds is where a setup lands: the new project's
# own screen, carrying the press's one result line (prescription 3).

COMMANDS_BLOCK = '<div class="commands"><h3>Commands</h3>'
COPY_ITEM = re.compile(
    r'(<div data-for="[^"]*"( hidden)?>)?<div class="copy-line"><code>[^<]*</code>'
    r'<button class="copy" type="button" data-copy="([^"]*)"')


def screen_kind(page: str) -> str:
    """Which screen a page is, by its root element: `setup`, `project` or
    `projects`, whatever state it is in."""
    found = re.search(r'<div class="screen ([a-z]+)[" ]', main_of(page))
    return found.group(1) if found else ""


def shown_commands(page: str) -> list[str]:
    """The commands Details lists before the press, as the last post left
    the boxes: a line whose box is unticked is drawn hidden."""
    body = main_of(page)
    if COMMANDS_BLOCK not in body:
        return []
    block = body.split(COMMANDS_BLOCK, 1)[1].split("</details>", 1)[0]
    return [html.unescape(line) for _, hidden, line in COPY_ITEM.findall(block)
            if not hidden]


def ran_commands(page: str) -> list[str]:
    """The commands a press ran, as its result line's Details lists them."""
    body = main_of(page)
    found = re.search(r'<div class="result [a-z]+".*?</details>', body, re.S)
    if found is None or COMMANDS_BLOCK not in found.group(0):
        return []
    block = found.group(0).split(COMMANDS_BLOCK, 1)[1]
    return [html.unescape(line) for _, _, line in COPY_ITEM.findall(block)]


def setup_events(before: int) -> list[tuple[str, str, list[str]]]:
    """The app's setup presses since `before`, from the activity log."""
    return [event for event in logged(before)
            if event[0].split()[1] in manage_web.WALK_ACTIONS]


def test_add_a_project_in_the_app_matches_the_cli_three_verb_sequence(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescriptions 1 and 3, against the command line. Opening the
    screen and a Refresh write nothing; Details names the commands the
    press will run, in order; the press writes what `radialrig mount`,
    `radialrig skill add` and `radialrig connect` write, and lands on the
    new project's screen with its one result line, which names every
    command it ran."""
    page_root = _host(tmp_path / "page-host")
    twin = _host(tmp_path / "twin-host")
    assert tree(page_root) == tree(twin)
    pristine = client_here.read_bytes()

    run_ = manage()
    review = opened(run_, page_root)
    assert screen_kind(review) == "setup"
    assert f"<h1>Set up RadialRig in {page_root.name}</h1>" in review
    assert tree(page_root) == tree(twin), "opening the screen wrote something"

    # A Refresh carrying the fields the press will carry. The commands in
    # Details follow those fields, because the press does.
    status, again, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "survey",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, "skill": boxes(SKILL), **CHOSEN})
    assert status == 200
    assert shown_commands(again) == [
        f"radialrig mount {page_root} --scope {SCOPE}",
        *(f"radialrig skill add {SKILL} {page_root} --harness {harness}"
          for harness in sk.HARNESSES),
    ]
    assert tree(page_root) == tree(twin), "the refresh wrote something"

    status, landed, _ = walk_press(run_, again, {
        "token": run_.session.token, "action": "mount", "scope": SCOPE,
        "owner": mnt.DEFAULT_OWNER, "skill": boxes(SKILL), "connect": CLIENT, **CHOSEN})
    assert status == 200
    assert screen_kind(landed) == "project"
    tone, sentence, warning, details = result_of(landed)
    label = cn.CLIENTS[CLIENT].label
    assert (tone, sentence, warning) == (
        manage_web.RESULT_DONE,
        f"Set up RadialRig in {page_root.name}, added 1 skill, and connected {label}. "
        f"Start a new agent session for the skill, and fully quit and reopen "
        f"{label} to use the connection.", "")
    assert report.next_step(page_root)[0] in details
    # Every command the press ran, in the order it ran them.
    assert ran_commands(landed) == [
        f"radialrig mount {page_root} --scope {SCOPE}",
        *(f"radialrig skill add {SKILL} {page_root} --harness {harness}"
          for harness in sk.HARNESSES),
        f"radialrig connect {CLIENT} {page_root}",
    ]

    # The same two verbs, from the command line, on the twin.
    code, out, err = run(capsys, "mount", str(twin), "--scope", SCOPE)
    assert (code, err) == (report.OK, "")
    printed = out
    code, out, err = run(capsys, "skill", "add", SKILL, str(twin))
    assert (code, err) == (report.OK, "")
    printed += out
    assert files(page_root) == files(twin)

    # Every project file the line lists is one those verbs reported, under
    # the tag the result line's grammar gives its verb. The connection's
    # rows are its record and the client's settings file, compared below.
    listed_ = re.findall(r"<li><code>([^<]*)</code> " + manage_web.MIDDOT
                         + r" ([a-z]+)</li>", landed)
    assert listed_
    for path, tag in listed_:
        path = html.unescape(path)
        if path != cn.CONNECT_REL and not Path(path).is_absolute():
            assert path in printed, path
        assert tag in (manage_web.TAG_ADDED, manage_web.TAG_UPDATED), tag
    assert (cn.CONNECT_REL, manage_web.TAG_ADDED) in listed_

    # The connection, on the one root that can hold it: out by the command
    # line, then in again by it, to the same bytes the press left.
    page_tree, page_config = tree(page_root), client_here.read_bytes()
    assert page_config != pristine
    assert run(capsys, "unconnect", CLIENT, str(page_root))[0] == report.OK
    assert client_here.read_bytes() == pristine
    assert tree(page_root) == tree(twin)
    code, out, err = run(capsys, "connect", CLIENT, str(page_root))
    assert (code, err) == (report.OK, "")
    assert tree(page_root) == page_tree
    assert client_here.read_bytes() == page_config


def test_a_setup_in_the_app_lands_on_the_projects_screen_and_the_server_stands(
    tmp_path, env, manage, fixed_day,
):
    """Prescription 3 with the defaults: the press lands on the new
    project's screen with "Set up RadialRig in {project}." at the top,
    the project is on Projects, the server stands, and the press leaves
    its one event in the activity log with the mount's own lines."""
    root = _make_project(tmp_path / "host").resolve()
    run_ = manage()
    review = opened(run_, root)
    before = len(log_events())
    status, landed, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "mount",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, **CHOSEN})
    assert status == 200
    assert mnt.is_mounted(root)

    assert screen_kind(landed) == "project"
    body = main_of(landed)
    assert (f'<div class="project-name"><h1>{root.name}</h1>'
            f'<span class="path">{html.escape(str(root), quote=False)}</span></div>') in body
    tone, sentence, warning, details = result_of(landed)
    assert (tone, sentence, warning) == (
        manage_web.RESULT_DONE, f"Set up RadialRig in {root.name}.", "")
    assert report.next_step(root)[0] in details
    assert f'data-copy="{report.next_step(root)[1]}"' in landed
    assert ran_commands(landed) == [f"radialrig mount {root} --scope {SCOPE}"]
    assert body.index('<div class="project-name">') < body.index('<div class="result done"')
    assert body.index('<div class="result done"') < body.index('<div class="status-band">')
    assert status_band(landed)[0][1] == "RadialRig is working in this project."

    # The walkthrough ended; the page did not.
    assert 'data-terminal="0"' in landed
    assert setup_web.STOPPED not in landed
    assert run_.running

    ((what, outcome, lines),) = setup_events(before)
    assert what == "press mount"
    assert outcome == f"{setup_web.MOUNTED} exit {report.OK}"
    twin = _make_project(tmp_path / "twin").resolve()
    said = report.mount_report(mnt.mount(twin, scope=SCOPE, owner=mnt.DEFAULT_OWNER))
    assert [line for line in lines if line] == [
        line.replace(str(twin), str(root)).strip() for line in said if line.strip()]

    back = [t for t in navigations(landed, run_.session.token)
            if f"view={manage_web.ROSTER}" in t]
    status, listing, _ = run_.get(back[0])
    assert status == 200
    assert [state.root for state in ro.listing()] == [str(root)]
    assert roster_rows(listing) == expected_listing(listed())

    status, _, _ = run_.post({"token": run_.session.token, "action": "close"})
    assert status == 200
    assert run_.wait() == report.OK


def test_a_set_up_folder_opens_its_project_screen_from_add_a_project(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 3, the other half: in the app a folder already set up
    opens its own project screen from Add a project, whatever it still
    lacks, and no walkthrough is opened on it."""
    every = _host(tmp_path / "telemetry")
    assert run(capsys, "mount", str(every))[0] == report.OK
    assert run(capsys, "skill", "add", "--all", str(every))[0] == report.OK
    some = _host(tmp_path / "weather-radar")
    assert run(capsys, "mount", str(some))[0] == report.OK
    run_ = manage()
    for root in (every, some):
        before = tree(root)
        page = opened(run_, root)
        assert screen_kind(page) == "project", root
        assert result_of(page) is None
        assert f"<h1>{root.name}</h1>" in main_of(page)
        assert 'value="mount"' not in page
        assert os.path.normcase(str(root)) not in run_.session.setups
        assert tree(root) == before
    # The set-up project with every skill and no connection is offered
    # Connect where the app offers it: its project screen's own row.
    assert "Connect Claude Desktop" in connections_of(opened(run_, every))
    assert run_.running


def test_a_setup_that_connected_while_claude_desktop_runs_carries_both_warnings(
    tmp_path, env, client_here, command, manage, fixed_day,
):
    """Prescription 5, as BUG-0018 built it: the before-press sentence
    under the Connect box while the client is open, and the after-press
    sentence on the line of a setup that connected while it was. A setup
    that left the client's settings alone says nothing about it."""
    lock = plant_lock(client_here)
    label = cn.CLIENTS[CLIENT].label
    root = _make_project(tmp_path / "pit-strategy").resolve()
    run_ = manage()
    review = opened(run_, root)
    assert (f'<p class="warn">{report.client_running_before(cn.CLIENTS[CLIENT])}</p>'
            in review)
    status, landed, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "mount", "scope": SCOPE,
        "owner": mnt.DEFAULT_OWNER, "connect": CLIENT, **CHOSEN})
    assert status == 200
    assert screen_kind(landed) == "project"
    tone, sentence, warning, _ = result_of(landed)
    assert tone == manage_web.RESULT_DONE
    assert sentence.endswith(f"and connected {label}. Fully quit and reopen {label} "
                             "to use the connection.")
    assert warning == manage_web.RUNNING_AFTER_CONNECT.format(label=label)

    # No connection ticked: nothing written to the client, nothing said.
    other = _make_project(tmp_path / "lap-notes").resolve()
    status, quiet, _ = walk_press(run_, opened(run_, other), {
        "token": run_.session.token, "action": "mount",
        "scope": "lap-notes", "owner": mnt.DEFAULT_OWNER, **CHOSEN})
    assert status == 200
    assert result_of(quiet)[:3] == (
        manage_web.RESULT_DONE, f"Set up RadialRig in {other.name}.", "")
    lock.unlink()


def test_a_setup_whose_skills_failed_says_what_was_set_up_and_what_wasnt(
    tmp_path, env, client_here, command, manage, fixed_day, monkeypatch,
):
    """Prescription 4, after the mount landed: a skill write that stops
    leaves the setup standing, the line says so and that the ticked
    connection wasn't tried, and the engine's sentence and the log are
    behind Details. The press lands on the project's screen all the same."""
    root = _make_project(tmp_path / "host").resolve()
    label = cn.CLIENTS[CLIENT].label

    def refuse(plans):
        raise sk.SkillError(f"file error at {root / SKILL_DIR}: denied; nothing was written")

    monkeypatch.setattr(sk, "write_add_plans", refuse)
    run_ = manage()
    review = opened(run_, root)
    before = len(log_events())
    status, landed, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "mount", "scope": SCOPE,
        "owner": mnt.DEFAULT_OWNER, "skill": boxes(SKILL), "connect": CLIENT, **CHOSEN})
    assert status == 200
    assert mnt.is_mounted(root)
    assert not cn.is_connected(root)
    assert screen_kind(landed) == "project"
    tone, sentence, warning, details = result_of(landed)
    assert (tone, sentence, warning) == (
        manage_web.RESULT_PARTLY,
        f"Set up RadialRig in {root.name}, but couldn't add the skills you ticked. "
        "None of them was added. Check Details for what stopped them. "
        f"{label} wasn't connected.", "")
    assert "denied; nothing was changed" in details
    assert str(al.log_path()) in details
    assert setup_web.CONNECT_NOT_ATTEMPTED not in details
    # Only the mount ran, so only its command is named.
    assert ran_commands(landed) == [f"radialrig mount {root} --scope {SCOPE}"]
    ((what, outcome, lines),) = setup_events(before)
    assert (what, outcome) == ("press mount", f"{setup_web.MOUNTED} exit {report.PARTIAL}")
    assert setup_web.CONNECT_NOT_ATTEMPTED in lines[-1]


def test_a_setup_whose_connection_failed_says_its_settings_werent_changed(
    tmp_path, env, client_here, command, manage, fixed_day, monkeypatch,
):
    """Prescription 4, the connection's half: the setup and its skills
    landed, the connection was refused at its write, and the line says
    which landed and that Claude Desktop's settings weren't changed."""
    root = _make_project(tmp_path / "host").resolve()
    pristine = client_here.read_bytes()
    label = cn.CLIENTS[CLIENT].label

    def refuse(*args, **kwargs):
        raise cn.ConnectError("the settings file changed under the press")

    monkeypatch.setattr(cn, "connect", refuse)
    run_ = manage()
    status, landed, _ = walk_press(run_, opened(run_, root), {
        "token": run_.session.token, "action": "mount", "scope": SCOPE,
        "owner": mnt.DEFAULT_OWNER, "skill": boxes(SKILL), "connect": CLIENT, **CHOSEN})
    assert status == 200
    assert client_here.read_bytes() == pristine
    assert screen_kind(landed) == "project"
    tone, sentence, _, details = result_of(landed)
    assert (tone, sentence) == (
        manage_web.RESULT_PARTLY,
        f"Set up RadialRig in {root.name} and added 1 skill, but couldn't connect "
        f"{label}. Its settings weren't changed. Start a new agent session to pick it up.")
    assert "the settings file changed under the press" in details
    assert f"radialrig connect {CLIENT} {root}" not in ran_commands(landed)


def test_add_a_project_in_the_app_withholds_connect_under_a_taken_name(
    tmp_path, env, client_here, command, manage, fixed_day,
):
    """Phase 2 review F1, in the app. Claude Desktop already has another
    entry under the new folder's name: the screen plans the connection as
    it is drawn, with the press's own read-only plan, and the name-taken
    sentence stands in the box's place before any press. Set up project
    stays, and nothing is written to the client."""
    root = _make_project(tmp_path / "name-taken").resolve()
    client = cn.CLIENTS[CLIENT]
    key = cn.entry_key(client, mnt.default_scope(root))
    client_here.write_bytes(
        (json.dumps({"mcpServers": {key: {"command": "theirs"}}}, indent=2)
         + "\n").encode("utf-8"))
    taken = client_here.read_bytes()
    run_ = manage()
    review = opened(run_, root)
    assert screen_kind(review) == "setup"
    assert 'value="mount"' in review
    assert 'name="connect"' not in review, "the box is offered under a taken name"
    assert setup_web.CONNECT_NAME_TAKEN.format(label=client.label) in text_of(review)
    assert client_here.read_bytes() == taken


def test_a_folder_set_up_since_its_screen_was_drawn_lands_on_its_project(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """A press on a screen drawn before the folder was set up elsewhere:
    the engine finds it set up and writes nothing more, and the app opens
    the project's screen. A set-up project's own press, posted anyway,
    answers on that screen with its line."""
    root = _make_project(tmp_path / "host").resolve()
    run_ = manage()
    review = opened(run_, root)
    assert run(capsys, "mount", str(root))[0] == report.OK
    before = tree(root)
    status, landed, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "mount",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, **CHOSEN})
    assert status == 200
    assert screen_kind(landed) == "project"
    assert result_of(landed) is None
    assert tree(root) == before

    status, added, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "skills",
        "skill": boxes(SKILL), **CHOSEN})
    assert status == 200
    assert screen_kind(added) == "project"
    assert result_of(added)[:2] == (
        manage_web.RESULT_DONE, "Added 1 skill. Start a new agent session to pick it up.")
    assert ran_commands(added) == [
        f"radialrig skill add {SKILL} {root} --harness {harness}"
        for harness in sk.HARNESSES]


def test_a_setup_press_waits_its_turn_in_the_one_write_queue(
    tmp_path, env, client_here, command, manage, fixed_day,
):
    """The page server's one-write queue, unchanged, for Set up project:
    the press waits while another write holds the queue, writes nothing
    while it waits, and runs once the queue is free."""
    root = _make_project(tmp_path / "host").resolve()
    run_ = manage()
    review = opened(run_, root)
    real = run_.session.actions
    gate = Gate(real)
    run_.session.actions = gate
    answers: list[tuple] = []
    try:
        before = tree(root)
        with real:
            worker = threading.Thread(target=lambda: answers.append(walk_press(run_, review, {
                "token": run_.session.token, "action": "mount",
                "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, **CHOSEN})))
            worker.start()
            assert gate.waiting.wait(20), "the setup press never reached the queue"
            assert worker.is_alive() and answers == []
            assert tree(root) == before
        worker.join(20)
        assert not worker.is_alive()
    finally:
        run_.session.actions = real
    status, landed, _ = answers[0]
    assert status == 200
    assert mnt.is_mounted(root)
    assert result_of(landed)[0] == manage_web.RESULT_DONE


def test_the_embedded_screen_ticks_one_agents_column_and_installs_for_it_alone(
    tmp_path, env, manage, fixed_day, capsys,
):
    """PLAN-0015 phase 3's done-condition, on the app's Add a project: the
    grid has a column per agent with Add all at its head; every box in the
    second agent's column ticked and the press pressed installs every
    skill for that agent and none for the first, the line names one
    `--harness` command per pair it wrote, and `skill list` says so."""
    first, second = list(sk.HARNESSES.values())[:2]
    names = sk.catalog_names()
    root = _make_project(tmp_path / "host").resolve()
    twin = _make_project(tmp_path / "twin").resolve()
    run_ = manage()
    review = opened(run_, root)
    heads = re.findall(
        r'<span class="agent-head"><span>([^<]*)</span>(<button class="link" '
        r'type="button" data-all="([^"]*)" aria-label="([^"]*)" hidden>Add all</button>)?',
        review)
    assert [(label, agent, named) for label, _, agent, named in heads] == [
        (first.label, first.name, f"Add all skills for {first.label}"),
        (second.label, second.name, f"Add all skills for {second.label}")]
    for harness in (first, second):
        assert review.count(f'data-agent="{harness.name}"') == len(names)
    assert "harness" not in text_of(
        re.sub(r'<div class="copy-line">.*?</div>', "", review, flags=re.S)).lower()

    ticked = boxes(*names, agents=(second,))
    status, again, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "survey",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, "skill": ticked, **CHOSEN})
    assert status == 200
    assert again.count(" checked>") == len(names)
    assert shown_commands(again) == [
        f"radialrig mount {root} --scope {SCOPE}",
        *(f"radialrig skill add {name} {root} --harness {second.name}" for name in names),
    ]
    assert not mnt.is_mounted(root), "the refresh wrote something"

    status, landed, _ = walk_press(run_, again, {
        "token": run_.session.token, "action": "mount",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, "skill": ticked, **CHOSEN})
    assert status == 200
    assert result_of(landed)[:2] == (
        manage_web.RESULT_DONE,
        f"Set up RadialRig in {root.name} and added {len(names)} skills. "
        "Start a new agent session to pick them up.")
    assert ran_commands(landed) == [
        f"radialrig mount {root} --scope {SCOPE}",
        *(f"radialrig skill add {name} {root} --harness {second.name}" for name in names)]
    assert f"--harness {first.name}" not in "".join(ran_commands(landed))
    assert run_.running

    assert run(capsys, "mount", str(twin), "--scope", SCOPE)[0] == report.OK
    assert run(capsys, "skill", "add", "--all", str(twin), "--harness", second.name)[0] \
        == report.OK
    assert snapshot(root) == snapshot(twin)
    assert not (root / first.skills).exists()
    code, listed_, _ = run(capsys, "skill", "list", str(root))
    assert code == report.OK
    absent = [line for line in listed_.splitlines() if mnt.ABSENT in line]
    current = [line for line in listed_.splitlines() if mnt.CURRENT in line]
    assert len(absent) == len(names) and all(first.skills in line for line in absent)
    assert len(current) == len(names) and all(second.skills in line for line in current)


def test_add_a_projects_lines_are_the_lines_the_project_screen_spells(tmp_path):
    """One spelling per command across the two screens: the connection
    and the skill lines Add a project lists are the lines the project
    screen's own presses name, for a plain path, a spaced one and a path
    no rule makes safe (which has none on either)."""
    for root in (r"C:\projects\plain", str(tmp_path / SPACED), r"C:\dev\proj&x"):
        connect = manage_web.Action(manage_web.CONNECT, root=root, client=CLIENT).command
        add = manage_web.Action(
            manage_web.SKILL_ADD, root=root, skill=SKILL, harness=AGENT).command
        try:
            assert setup_web.connect_line(CLIENT, root) == connect, root
            assert setup_web.skill_add_line(AGENT, SKILL, root) == add, root
        except setup_web.Unquotable:
            assert connect == add == "", root
    assert setup_web.CONNECT_LINE == manage_web.COMMANDS[manage_web.CONNECT]


def test_the_walkthrough_commands_quote_a_spaced_folder(
    tmp_path, env, manage, fixed_day,
):
    """The scope starts as the folder's own name, so one space lands twice.

    A folder named with a space in it puts that space in the path the
    mount command names and in the scope it would carry, and the verbs
    named beneath the button are the ones a reader runs instead of
    pressing it. So each of them is one argument of that line here, the
    way each is one argument of the command the button runs.
    """
    root = _make_project(tmp_path / SPACED).resolve()
    assert " " in str(root)
    assert mnt.default_scope(root) == SPACED

    before = tree(root)
    run_ = manage()
    review = opened(run_, root)
    named = commands_on(review)
    assert named[0] == f'radialrig mount "{root}"'
    for line in named:
        assert arguments(line).count(str(root)) == 1, line

    # The scope as a reader edited it: both slots carry a space now, and
    # both are one argument of the one line that names them.
    status, edited, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "survey",
        "scope": "Other Scope", "owner": mnt.DEFAULT_OWNER})
    assert status == 200
    line = commands_on(edited)[0]
    assert line == f'radialrig mount "{root}" --scope "Other Scope"'
    assert arguments(line) == [
        "radialrig", "mount", str(root), "--scope", "Other Scope"]
    assert tree(root) == before, "the refresh wrote something"


def test_a_fabricated_token_still_403s_on_a_setup_view_post(
    tmp_path, env, manage,
):
    """One page, one token. The walkthrough carries the manage session's
    own, so there is no second scheme and nothing else opens it."""
    root = _make_project(tmp_path / "host").resolve()
    before = tree(root)
    run_ = manage()
    review = opened(run_, root)
    assert f'name="token" value="{run_.session.token}"' in review

    sub = run_.session.setups[os.path.normcase(str(root))]
    assert sub.token == run_.session.token
    assert sub.launch_token != run_.session.token

    for token in ("not-the-token", sub.launch_token):
        status, body, _ = walk_press(run_, review, {
            "token": token, "action": "mount",
            "scope": SCOPE, "owner": mnt.DEFAULT_OWNER})
        assert status == 403, token
        assert setup_web.worded(
            setup_web.REFUSE_TOKEN,
            surface=manage_web.SURFACE, page=manage_web.PAGE_NAME) in body
        assert tree(root) == before, "a refused press wrote something"
    assert run_.running


def test_a_refused_folder_in_the_app_says_why_and_offers_its_next_step(
    tmp_path, env, capsys, manage,
):
    """Prescription 4 in the app, against the command line's refusal: a
    file in the way says so in one sentence and offers Check again, which
    keeps the card while the file stands and brings back the screen once
    it's gone; nothing presses past it, and nothing is written."""
    page_root = _make_project(tmp_path / "page-host").resolve()
    twin = _make_project(tmp_path / "twin-host").resolve()
    for host in (page_root, twin):
        (host / "docs").mkdir()
        (host / "docs" / "spec.md").write_bytes(b"this project's own rulebook\n")
    before = tree(page_root)

    run_ = manage()
    refused = opened(run_, page_root)
    code, out, err = run(capsys, "mount", str(twin))
    assert (code, err) == (report.REFUSED, "")
    said = text_of(refused)
    assert setup_web.STOPPED_STATE in said
    assert setup_web.BLOCKED_ONE in said
    assert setup_web.BLOCKERS_HEAD in said
    assert f"docs/spec.md : {report.conflict_reason('docs/spec.md')}" in said
    assert "docs/spec.md" in out and report.conflict_reason("docs/spec.md") in out
    body = main_of(refused)
    assert 'value="mount"' not in body
    assert f'value="survey">{setup_web.CHECK_AGAIN_LABEL}</button>' in body
    assert f">{setup_web.CANCEL_LABEL}</button>" in body
    assert tree(page_root) == before

    # A client that posts the setup anyway is refused the same way.
    status, once_more, _ = walk_press(run_, refused, {
        "token": run_.session.token, "action": "mount",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, **CHOSEN})
    assert status == 200
    assert setup_web.BLOCKED_ONE in text_of(once_more)
    assert tree(page_root) == before, "the refusal wrote something"

    # Check again, while the file stands, and once it is moved.
    status, again, _ = walk_press(run_, refused, {
        "token": run_.session.token, "action": "survey"})
    assert setup_web.BLOCKED_ONE in text_of(again)
    (page_root / "docs" / "spec.md").unlink()
    status, ready, _ = walk_press(run_, refused, {
        "token": run_.session.token, "action": "survey"})
    assert status == 200
    assert f'value="mount">{setup_web.SETUP_LABEL}</button>' in ready
    assert not mnt.is_mounted(page_root)
    assert run_.running


def test_a_folder_inside_a_set_up_project_names_it_and_opens_it_in_the_app(
    tmp_path, env, capsys, manage,
):
    """Prescription 4: a folder inside a set-up project names that
    project, and Open {parent} opens the project's own screen."""
    parent = _make_project(tmp_path / "team-radio").resolve()
    assert run(capsys, "mount", str(parent))[0] == report.OK
    nested = parent / "briefings"
    nested.mkdir()
    before = tree(parent)
    run_ = manage()
    card = opened(run_, nested)
    said = text_of(card)
    assert setup_web.INSIDE_SAID.format(parent="team-radio") in said
    assert setup_web.INSIDE_WHERE.format(path=parent) in said
    assert 'value="mount"' not in main_of(card)
    opens = [t for t in navigations(card, run_.session.token)
             if f"view={manage_web.PROJECT}" in t]
    assert len(opens) == 1
    assert urllib.parse.urlencode({"root": str(parent)}) in opens[0]
    assert '<button class="primary" type="submit">Open team-radio</button>' in card
    status, project, _ = run_.get(opens[0])
    assert status == 200
    assert screen_kind(project) == "project"
    assert "<h1>team-radio</h1>" in main_of(project)
    assert tree(parent) == before


def test_a_folder_that_isnt_there_offers_the_folder_field_and_choose_a_folder(
    tmp_path, env, manage,
):
    """Prescriptions 2 and 4: a full path naming no folder gets the Folder
    not found card, holding Projects' own field under the ids the app's
    picker writes into (`#root`, `#browse`), once each, with Continue; a
    path that names nothing comes back to Projects with one line, and an
    empty field with none."""
    missing = tmp_path / "not-here"
    a_file = tmp_path / "notes.txt"
    a_file.write_text("not a folder\n", encoding="utf-8")
    run_ = manage()
    for path in (missing, a_file):
        card = view(run_, manage_web.SETUP, root=path)
        body = main_of(card)
        said = text_of(card)
        assert setup_web.STOPPED_STATE in said
        assert manage_web.NOT_FOUND_HEAD in said
        assert manage_web.NOT_FOUND_SAID in said
        assert body.count('id="root"') == 1 and body.count('id="browse"') == 1
        assert f'value="{html.escape(str(path))}"' in body
        assert f'<button class="primary" type="submit">{manage_web.ADD_LABEL}</button>' in body
        assert f">{setup_web.CANCEL_LABEL}</button>" in body
    # Continue, with the slip mended, opens Add a project on that folder.
    fresh = _make_project(tmp_path / "pit-strategy").resolve()
    action, inner = next(
        (a, i) for a, i in GET_FORM.findall(card) if 'id="root"' in i)
    fields = dict(HIDDEN.findall(inner))
    assert fields == {"s": run_.session.token, "view": manage_web.SETUP}
    status, ready, _ = run_.get(
        f"{action}?{urllib.parse.urlencode({**fields, 'root': str(fresh)})}")
    assert status == 200
    assert f"<h1>Set up RadialRig in {fresh.name}</h1>" in ready

    quoted = view(run_, manage_web.SETUP, root=f'"{fresh}"')
    assert screen_kind(quoted) == "projects"
    assert result_of(quoted)[:2] == (manage_web.RESULT_FAILED, manage_web.PATH_UNUSABLE)
    empty = view(run_, manage_web.SETUP, root="")
    assert screen_kind(empty) == "projects"
    assert result_of(empty) is None


def test_no_client_detected_means_no_offer_and_no_client_name(
    tmp_path, monkeypatch, manage, fixed_day,
):
    """A profile with no client directory in it, still inside tmp_path.

    `APPDATA` is narrowed rather than removed: the roster path is built
    from it too, and a run without it would assert against a screen whose
    connection could never have been offered anyway.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "no-client"))
    root = _make_project(tmp_path / "bare").resolve()
    run_ = manage()
    review = opened(run_, root)

    assert cn.detect(cn.CLIENTS[CLIENT]) == ()
    assert 'name="connect"' not in review
    for client in cn.CLIENTS.values():
        assert client.label not in review
        assert client.restart not in review
    # And Details names the one command the press would run.
    assert shown_commands(review) == [f"radialrig mount {root}"]

    # A press that posts the box anyway connects nothing: the offer was
    # never made, so there is no client for the press to write to.
    status, landed, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "mount", "scope": SCOPE,
        "owner": mnt.DEFAULT_OWNER, "connect": CLIENT, **CHOSEN})
    assert status == 200
    assert result_of(landed)[:2] == (
        manage_web.RESULT_DONE, f"Set up RadialRig in {root.name}.")
    assert not cn.is_connected(root)
    words = main_of(landed).split('<div class="result', 1)[1].split("</details>", 1)[0]
    for client in cn.CLIENTS.values():
        assert client.label not in words
        assert client.restart not in words
    assert ran_commands(landed) == [f"radialrig mount {root} --scope {SCOPE}"]


# --- PLAN-0017 phase 4: only safe lines ------------------------------------

def test_a_slot_no_quoting_rule_makes_safe_offers_no_copy_line_on_this_page(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """setup-web-04, on the manage page's builders.

    Every command block here is drawn through one of three builders: the
    action's own `command`, the frame `_command_of` draws beside a press,
    and Add a project's command lists, before the press and after it. A
    path or a client key carrying a character no quoting rule makes safe
    in both shells reaches all three, and each offers no line rather than
    one that runs something else. The page still says what it did.
    """
    for ch in UNSAFE:
        path, key = rf"C:\dev\proj{ch}x", f"{PREFIX}foo{ch}calc"
        for act in (
            manage_web.Action(manage_web.UNMOUNT, root=path),
            manage_web.Action(manage_web.SKILL_ADD, root=path, skill=SKILL, harness=AGENT),
            manage_web.Action(manage_web.CONNECT, root=path, client=CLIENT),
            manage_web.Action(manage_web.ORPHAN, client=CLIENT, key=key),
        ):
            assert act.named, act
            assert act.command == "", act
            assert manage_web._command_of(act) == "", act
        sub = setup_web.Session(Path(path), SCOPE, mnt.DEFAULT_OWNER)
        sub.skills = ((AGENT, SKILL),)
        sub.connect = True
        review = setup_web.PageState(
            kind=setup_web.REVIEW, outcome=report.NOOP, terminal=False,
            client=cn.CLIENTS[CLIENT])
        assert setup_web.press_commands(sub, review, ()) == (), repr(path)
        done = setup_web.PageState(
            kind=setup_web.MOUNTED, outcome=report.OK, terminal=True,
            added=((AGENT, SKILL),), connect=("row",), client=cn.CLIENTS[CLIENT])
        assert setup_web.ran_commands(sub, done) == (), repr(path)

    # Over HTTP, on folders and a key a reader can have: `&` and `$` are
    # legal in a folder name on both systems, `;` in a client's key.
    mounted = _host(tmp_path / "proj$x")
    assert run(capsys, "mount", str(mounted))[0] == report.OK
    fresh = _host(tmp_path / "proj&calc")
    key = f"{PREFIX}foo;calc"
    loose_entry(client_here, key, tmp_path / "vanished")

    run_ = manage()
    roster = run_.page()
    # The entry is the gone folder's row (PLAN-0027 phase 3), named by the
    # folder; its key would stand only in its command, which it has none of.
    assert "vanished" in text_of(roster), "the loose entry is not listed"
    status, project, _ = run_.get(
        f"{manage_web.MANAGE_PATH}?s={run_.session.token}"
        f"&view={manage_web.PROJECT}&root={urllib.parse.quote(str(mounted))}")
    assert status == 200, project
    walk = opened(run_, fresh)
    assert f"<h1>Set up RadialRig in {html.escape(fresh.name, quote=False)}</h1>" in walk
    status, planned, _ = run_.post(press(
        run_, {"action": manage_web.UNMOUNT, "root": str(mounted)}))
    assert status == 200
    assert mnt.is_mounted(mounted), "the plan press wrote"
    status, landed, _ = walk_press(run_, walk, {
        "token": run_.session.token, "action": "mount", "scope": "plain",
        "owner": mnt.DEFAULT_OWNER, "skill": boxes(SKILL), **CHOSEN})
    assert status == 200
    assert result_of(landed)[0] == manage_web.RESULT_DONE
    for name, page in (("roster", roster), ("project", project),
                       ("walkthrough", walk), ("plan", planned), ("landed", landed)):
        for line in commands_on(page):
            assert str(mounted) not in line, (name, line)
            assert str(fresh) not in line, (name, line)
            assert key not in line, (name, line)
    assert run_.running


# --- the update presses (PLAN-0021 phase 1, prescription 4) -------------
# The listing and the project page read "Update available" exactly for a
# project that is behind, and a skill row reads it exactly for a clean
# copy that is not current. Each new press writes what the command it
# discloses writes, byte for byte, and goes through the plan-then-confirm
# step like every press before it.

import dataclasses as dc  # noqa: E402

from radialrig import update  # noqa: E402
from radialrig.tests.test_manage_web import (  # noqa: E402,F401 - helpers, by name
    PRIMARY,
    agent_heads,
    main_of,
    pressed,
    project_page,
    record_of,
)
from radialrig.tests.test_skills import INSTALL_DAY, edit_skill  # noqa: E402
from radialrig.tests.test_update import newer_release  # noqa: E402,F401 - a fixture
from radialrig.tests.test_upgrade import edit_spec  # noqa: E402

UPDATE_LINE = re.compile(r'<div class="update-note"><span>([^<]*)</span>(.*?)</div>', re.S)
NAMES = ("alpha", "beta", "gamma", "delta", "epsilon")


def seed_projects(base: Path) -> dict[str, Path]:
    """Five projects, the half of each that stands before the newer
    release: alpha mounted on the old seed, beta and gamma with the old
    skill for every agent, gamma's copy edited. Delta and epsilon wait."""
    roots = {name: _make_project(base / name).resolve() for name in NAMES}
    mnt.mount(roots["alpha"], today=MOUNT_DAY)
    sk.add_all(roots["beta"], SKILL, today=INSTALL_DAY)
    sk.add_all(roots["gamma"], SKILL, today=INSTALL_DAY)
    for harness in sk.harnesses():
        (roots["gamma"] / harness.skills / SKILL / "SKILL.md").write_bytes(b"# ours now\n")
    return roots


def finish_projects(roots: dict[str, Path]) -> None:
    """The other half, on the newer release: beta, gamma and delta mount
    on the new seed, delta takes the new skill, epsilon stays unmounted.
    So alpha is behind on a seeded file, beta on a skill, gamma has an
    edited older skill, delta is current and epsilon is not mounted."""
    for name in ("beta", "gamma", "delta"):
        mnt.mount(roots[name], today=MOUNT_DAY)
    sk.add_all(roots["delta"], SKILL, today=INSTALL_DAY)


def two_sets(tmp_path, newer_release) -> tuple[dict[str, Path], dict[str, Path]]:
    page_set = seed_projects(tmp_path / "page")
    twin_set = seed_projects(tmp_path / "twin")
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    finish_projects(page_set)
    finish_projects(twin_set)
    for name in NAMES:
        assert tree(page_set[name]) == tree(twin_set[name]), name
    assert [update.behind(page_set[name]) for name in NAMES] == [
        True, True, False, False, False]
    return page_set, twin_set


def list_projects(roots: dict[str, Path]) -> None:
    for name in NAMES:
        ro.record_seen(roots[name])


def twinned(text: str, twin_set, page_set) -> str:
    for name in NAMES:
        text = text.replace(str(twin_set[name]), str(page_set[name]))
    return text


def update_rows(plan) -> list:
    return [*plan.seed.dispositions(), *(d for r in plan.skills for d in r.dispositions)]


def card_presses(page: str) -> list[dict[str, str]]:
    """Every post form on a card, token dropped: its press, where it has one."""
    out = []
    for inner in re.findall(r'<form method="post" action="[^"]+">(.*?)</form>',
                            main_of(page), re.S):
        fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', inner))
        fields.pop("token")
        out.append(fields)
    return out


def attention_rows(page: str) -> list[tuple[str, str]]:
    """A card's rows that need attention: the file and why it is left."""
    block = re.search(r'<ul class="attention">(.*?)</ul>', main_of(page), re.S)
    if block is None:
        return []
    return [(html.unescape(path), html.unescape(why)) for path, why in re.findall(
        r"<li><code>([^<]*)</code><span>([^<]*)</span></li>", block.group(1))]


def card_details(page: str) -> str:
    return main_of(page).split("<details>", 1)[1].split("</details>", 1)[0]


def test_the_listing_marks_behind_projects_and_update_all_updates_them(
    tmp_path, env, capsys, manage, fixed_day, newer_release,
):
    """actions.md row 3: the line above the list while any project is
    behind, with Update all, which opens Confirm a big change (PLAN-0027
    phase 3). The card asks how many projects it updates, says what in one
    sentence, lists only the rows that need attention, every file and the
    command behind Details, and runs on its press inside the queue: the
    bytes `radialrig upgrade --all` writes, the line on Projects, and the
    command's own report in the log."""
    page_set, twin_set = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    run_ = manage()

    page = run_.page()
    drawn = {name: (status, fix) for name, _, status, _, fix in roster_rows(page)}
    assert set(drawn) == set(NAMES)
    for name in NAMES:
        behind = name in ("alpha", "beta")
        assert (drawn[name] == ("Update ready", "Update")) == behind, name
    assert roster_rows(page) == expected_listing(listed())
    assert [name for name, *_ in roster_rows(page)][:2] == ["alpha", "beta"]
    said, inner = UPDATE_LINE.search(main_of(page)).groups()
    assert html.unescape(said) == "2 projects have an update."
    assert manage_web.UPDATE_ALL_BUTTON == "Update all"
    # The line's press names the screen and carries no confirm.
    (fields,) = [dict(HIDDEN.findall(f)) for f in re.findall(
        r'<form method="post" action="[^"]+">(.*?)</form>', inner, re.S)]
    assert fields == {"token": run_.session.token, "action": manage_web.UPDATE_ALL,
                      "from": manage_web.ROSTER}
    assert "radialrig upgrade --all" in commands_on(inner)
    body = main_of(page).replace(
        f'<button class="primary" type="submit">{manage_web.ADD_LABEL}</button>', "")
    assert body.count(PRIMARY) == 1
    assert PRIMARY in inner
    alpha_row = roster_row(page, "alpha")
    assert f'value="{manage_web.UPDATE}"' in alpha_row
    assert f'value="{manage_web.ROSTER}"' in alpha_row
    assert 'aria-label="Update alpha"' in alpha_row
    assert f"radialrig upgrade {page_set['alpha']}" in commands_on(alpha_row)

    # The card: nothing written, and the engine's own plan said plainly.
    before = {name: tree(page_set[name]) for name in NAMES}
    logged_before = len(log_events())
    card = pressed(run_, {"action": manage_web.UPDATE_ALL, "from": manage_web.ROSTER})
    assert {name: tree(page_set[name]) for name in NAMES} == before
    body = main_of(card)
    assert '<div class="screen confirm">' in body
    assert "<h1>Update 2 projects?</h1>" in body
    assert "<p>1 file and 1 skill will be updated. Your edits stay in place.</p>" in body
    harnesses = sk.harnesses()
    assert attention_rows(card) == [
        (f"gamma/{h.skills}/{SKILL}/", manage_web.KEEPING_EDITS) for h in harnesses]
    assert body.count(PRIMARY) == 1
    assert card_presses(card) == [{"action": manage_web.UPDATE_ALL, "confirm": "yes",
                                   "from": manage_web.ROSTER}]
    assert f'<button class="link quiet" type="submit">{manage_web.CANCEL_LABEL}</button>' in body
    details = card_details(card)
    assert "radialrig upgrade --all" in commands_on(details)
    for name in ("alpha", "beta", "gamma", "delta"):
        assert f"<h3>{name}</h3>" in details, name
    assert "<h3>epsilon</h3>" not in details, "a project not set up is not checked"
    assert f"docs/spec.md</code> {manage_web.MIDDOT} will update" in details
    assert f"{harnesses[0].skills}/{SKILL}/</code> {manage_web.MIDDOT} keeping your edits" in details
    ((what, outcome, lines),) = logged(logged_before)
    assert (what, outcome) == ("press update-all", f"{manage_web.UPDATE_ALL_PLAN} exit {report.PARTIAL}")

    # The run, against the command it discloses on the twin set.
    logged_before = len(log_events())
    done = pressed(run_, card_presses(card)[0])
    assert screen_of(done) == "projects"
    assert result_of(done)[:3] == (
        manage_web.RESULT_DONE,
        "Updated 1 file and 1 skill in 2 projects. Kept 2 edited files.", "")
    assert f"alpha/docs/spec.md {manage_web.MIDDOT} updated" in result_of(done)[3]
    ((what, outcome, lines),) = logged(logged_before)
    assert (what, outcome) == ("press update-all confirm",
                               f"{manage_web.UPDATED_ALL} exit {report.PARTIAL}")
    clear(env)
    list_projects(twin_set)
    code, out, err = run(capsys, "upgrade", "--all")
    assert (code, err) == (report.PARTIAL, ""), "gamma's edited skill is left"
    for name in NAMES:
        assert tree(page_set[name]) == tree(twin_set[name]), name
        assert snapshot(page_set[name]) == snapshot(twin_set[name]), name
    assert lines[0] == twinned(out, twin_set, page_set).splitlines()[0]
    assert b"A sentence the newer release added." in (
        page_set["alpha"] / "docs" / "spec.md").read_bytes()
    assert (page_set["gamma"] / sk.harnesses()[0].skills / SKILL / "SKILL.md"
            ).read_bytes() == b"# ours now\n"

    # Afterwards the list marks none, the line is gone, and a card asked
    # for anyway has no press. Gamma's edited skill is still left, so
    # neither the card nor the press says every project is up to date
    # (phase 3 review R1): each says what stays as it is.
    clear(env)
    list_projects(page_set)
    page = run_.page()
    assert "Update ready" not in text_of(page)
    assert UPDATE_LINE.search(main_of(page)) is None
    assert f'value="{manage_web.UPDATE_ALL}"' not in main_of(page)
    card = pressed(run_, {"action": manage_web.UPDATE_ALL, "from": manage_web.ROSTER})
    assert "<h1>Update all projects?</h1>" in main_of(card)
    assert ("<p>Nothing will be updated. RadialRig leaves what's listed below as it "
            "is.</p>") in main_of(card)
    assert manage_web.UPDATE_ALL_NOTHING not in text_of(card)
    assert attention_rows(card) == [
        (f"gamma/{h.skills}/{SKILL}/", manage_web.KEEPING_EDITS) for h in harnesses]
    assert card_presses(card) == []
    done = pressed(run_, {"action": manage_web.UPDATE_ALL, "confirm": "yes",
                          "from": manage_web.ROSTER})
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_DONE,
        "Nothing was updated. RadialRig left what Details lists as it is. Kept 2 edited files.")
    for h in harnesses:
        assert f"gamma/{h.skills}/{SKILL}/ {manage_web.MIDDOT} Keeping your edits" in details
    # With gamma off the list, nothing is left anywhere: up to date.
    clear(env)
    for name in NAMES:
        if name != "gamma":
            ro.record_seen(page_set[name])
    card = pressed(run_, {"action": manage_web.UPDATE_ALL, "from": manage_web.ROSTER})
    assert f"<p>{manage_web.UPDATE_ALL_NOTHING}</p>" in main_of(card)
    assert card_presses(card) == []
    done = pressed(run_, {"action": manage_web.UPDATE_ALL, "confirm": "yes",
                          "from": manage_web.ROSTER})
    assert result_of(done)[:2] == (
        manage_web.RESULT_DONE, "Every project is already up to date. Nothing was changed.")
    assert run_.running


def test_the_project_page_marks_a_behind_project_and_its_update_runs(
    tmp_path, env, capsys, manage, fixed_day, newer_release,
):
    """actions.md row 21: a behind project's status says an update is
    ready, with Update beside it, which opens Confirm a big change and
    runs on its press (PLAN-0027 phase 3): what `radialrig upgrade`
    writes, back on the project's screen with its line. From a row of
    Projects the same press comes back to Projects, naming the project."""
    page_set, twin_set = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    run_ = manage()
    assert manage_web.UPDATE_BUTTON == "Update"

    for name in NAMES:
        page = project_page(run_, page_set[name])
        band = status_band(page)
        behind = name in ("alpha", "beta")
        assert (("update", "An update is ready for this project.", "Update") in band) == behind, name
        band_html = main_of(page).split('<div class="status-band">', 1)[1].split(
            '<table class="skill-grid">', 1)[0]
        command = f"radialrig upgrade {page_set[name]}"
        assert (command in commands_on(band_html)) == behind, name
        assert band_html.count(PRIMARY) == (1 if behind or name == "epsilon" else 0), name

    labels = {"alpha": "1 file", "beta": "1 skill"}
    for name, origin in (("alpha", manage_web.PROJECT), ("beta", manage_web.ROSTER)):
        root, twin = page_set[name], twin_set[name]
        if origin == manage_web.PROJECT:
            band = main_of(project_page(run_, root)).split('<div class="status-band">', 1)[1]
            form = re.search(r'<form method="post" action="[^"]+">(.*?)</form>', band, re.S)
        else:
            form = re.search(r'<form method="post" action="[^"]+">(.*?)</form>',
                             roster_row(run_.page(), name), re.S)
        fields = dict(HIDDEN.findall(form.group(1)))
        fields.pop("token")
        assert fields == {"action": manage_web.UPDATE, "root": str(root), "from": origin}
        before = tree(root)
        card = pressed(run_, fields)
        assert tree(root) == before, "the card wrote something"
        assert f"<h1>Update {name}?</h1>" in main_of(card)
        assert (f"<p>{labels[name]} will be updated. Your edits stay in place.</p>"
                in main_of(card))
        assert attention_rows(card) == []
        assert main_of(card).count(PRIMARY) == 1
        assert f"radialrig upgrade {root}" in commands_on(card_details(card))
        # Details lists every file of the plan, then every file of each
        # skill copy the engine names (phase 3 review R9).
        rehearsed = update.plan(twin)
        details = card_details(card)
        for item in rehearsed.seed.items:
            assert f"<code>{html.escape(item.path, quote=False)}</code>" in details, item.path
        for r in rehearsed.skills:
            for d in r.dispositions:
                assert f"<code>{html.escape(d.path, quote=False)}</code>" in details, d.path

        logged_before = len(log_events())
        (confirm,) = card_presses(card)
        assert confirm == {"action": manage_web.UPDATE, "root": str(root),
                           "confirm": "yes", "from": origin}
        done = pressed(run_, confirm)
        code, out, err = run(capsys, "upgrade", str(twin))
        assert (code, err) == (report.OK, ""), name
        assert tree(root) == tree(twin), name
        assert snapshot(root) == snapshot(twin), name
        ((what, outcome, lines),) = logged(logged_before)
        assert (what, outcome) == ("press update confirm", f"{manage_web.UPDATED} exit {report.OK}")
        assert lines[0] == out.splitlines()[0].replace(str(twin), str(root))
        if origin == manage_web.PROJECT:
            assert screen_of(done) == "project"
            assert result_of(done)[:2] == (manage_web.RESULT_DONE, "Updated 1 file.")
            assert status_band(done) == [("ok", "RadialRig is working in this project.", "")]
        else:
            assert screen_of(done) == "projects"
            assert result_of(done)[:2] == (manage_web.RESULT_DONE, "Updated 1 skill in beta.")
        page = project_page(run_, root)
        assert status_band(page) == [("ok", "RadialRig is working in this project.", "")]
        assert f'value="{manage_web.UPDATE}"' not in main_of(page)

    # Gamma's only change is its edited skill, which the update keeps:
    # the card offers no press and says nothing will be updated, not that
    # the project is up to date (phase 3 review R1); a confirm posted
    # anyway writes nothing and says what it left, from Projects too.
    root = page_set["gamma"]
    card = pressed(run_, {"action": manage_web.UPDATE, "root": str(root),
                          "from": manage_web.PROJECT})
    assert ("<p>Nothing will be updated. RadialRig leaves what's listed below as it "
            "is.</p>") in main_of(card)
    assert manage_web.UPDATE_NOTHING not in text_of(card)
    assert card_presses(card) == []
    assert attention_rows(card) == [
        (f"{h.skills}/{SKILL}/", manage_web.KEEPING_EDITS) for h in sk.harnesses()]
    before = tree(root)
    for origin, this in ((manage_web.PROJECT, "this project"), (manage_web.ROSTER, "gamma")):
        done = pressed(run_, {"action": manage_web.UPDATE, "root": str(root),
                              "confirm": "yes", "from": origin})
        assert tree(root) == before
        tone, sentence, _, details = result_of(done)
        assert (tone, sentence) == (
            manage_web.RESULT_DONE,
            f"Nothing in {this} was updated. RadialRig left what Details lists as it is. "
            "Kept 2 edited files."), origin
        for h in sk.harnesses():
            assert f"{h.skills}/{SKILL}/ {manage_web.MIDDOT} Keeping your edits" in details
    # Delta has nothing left anywhere: it is up to date.
    done = pressed(run_, {"action": manage_web.UPDATE, "root": str(page_set["delta"]),
                          "confirm": "yes", "from": manage_web.PROJECT})
    assert result_of(done)[:2] == (
        manage_web.RESULT_DONE, "This project is already up to date. Nothing was changed.")
    # Epsilon isn't set up: the card says there's nothing to update.
    card = pressed(run_, {"action": manage_web.UPDATE, "root": str(page_set["epsilon"]),
                          "from": manage_web.PROJECT})
    assert f"<p>{manage_web.UPDATE_NOT_SET_UP}</p>" in main_of(card)
    assert card_presses(card) == []
    assert run_.running


def test_a_cell_reads_update_exactly_for_a_clean_older_copy(
    tmp_path, env, capsys, manage, fixed_day, newer_release,
):
    """actions.md row 14: a cell reads Update exactly where the agent's
    copy is clean and the package ships a newer one, and the cell runs
    the agent's skill add on its one press, byte for byte what the engine
    writes for that agent."""
    page_set, twin_set = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    run_ = manage()
    first, second = sk.harnesses()[:2]
    assert manage_web.CELL_UPDATE_LABEL == "Update"
    assert manage_web.CELL_UPDATE_NAME == "Update {skill} for {agent}"

    beta_page = project_page(run_, page_set["beta"])
    cells, presses = grid_cells(beta_page), grid_presses(beta_page)
    for harness in (first, second):
        assert cells[(SKILL, harness.label)] == ("update", "Update")
        assert f'aria-label="Update {SKILL} for {harness.label}"' in beta_page
        assert presses[(SKILL, harness.label)] == {
            "action": manage_web.SKILL_ADD, "root": str(page_set["beta"]), "skill": SKILL,
            "harness": harness.name, "confirm": "yes", "from": manage_web.PROJECT}
        assert (f"radialrig skill add {SKILL} {page_set['beta']} --harness {harness.name}"
                in commands_on(beta_page))
        # The other cells read Off, and Add all still stands in the
        # column head: it refreshes this one too.
        assert [cells[(name, harness.label)] for name in sk.catalog_names()
                if name != SKILL] == [("off", "Off")] * (len(sk.catalog_names()) - 1)
        assert agent_heads(beta_page)[harness.label] is not None
    for name in ("gamma", "delta"):
        cells = grid_cells(project_page(run_, page_set[name]))
        for harness in (first, second):
            assert cells[(SKILL, harness.label)] == ("on", "On"), name

    # The two-step press still rehearses and writes nothing.
    root, twin = page_set["beta"], twin_set["beta"]
    fields = {"action": manage_web.SKILL_ADD, "root": str(root), "skill": SKILL,
              "harness": first.name}
    before = tree(root)
    plan = pressed(run_, fields)
    assert tree(root) == before, "the plan press wrote something"
    assert main_of(plan).count(PRIMARY) == 1
    # The cell's own one press, against the engine on the twin.
    logged_before = len(log_events())
    done = pressed(run_, presses[(SKILL, first.label)])
    results = sk.add_all(twin, SKILL, targets=(first.name,))
    assert [r.wrote for r in results] == [True]
    ((what, outcome, lines),) = logged(logged_before)
    assert (what, outcome) == ("press skill-add confirm", f"{manage_web.DONE} exit {report.OK}")
    assert lines[0] == report.skill_add_reports(results)[0].replace(str(twin), str(root))
    assert tree(root) == tree(twin)
    assert snapshot(root) == snapshot(twin)
    assert result_of(done)[:2] == (
        manage_web.RESULT_DONE,
        f"Updated {SKILL} for {first.label}. Start a new agent session to pick it up.")
    cells = grid_cells(done)
    assert cells[(SKILL, first.label)] == ("on", "On")
    assert cells[(SKILL, second.label)] == ("update", "Update")
    assert run_.running


# Confirm a big change draws an update's whole plan behind Details, one
# heading per project and a tagged row per file and per skill copy, so
# "every file and every command, in order" holds there (PLAN-0027 phase
# 3 prescription 1). Its first lines are counts and the rows that need
# attention, never the report's rows.


def test_an_update_cards_details_list_every_file_and_skill_copy_tagged(
    tmp_path, env, capsys, manage, fixed_day, newer_release,
):
    page_set, twin_set = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    run_ = manage()
    card = pressed(run_, {"action": manage_web.UPDATE_ALL, "from": manage_web.ROSTER})
    details = card_details(card)
    for name in ("alpha", "beta", "gamma", "delta"):
        plan = update.plan(page_set[name])
        block = details.split(f"<h3>{name}</h3>", 1)[1].split("<h3>", 1)[0]
        for item in plan.seed.items:
            path, tag, _ = manage_web._seed_row(item)
            assert f"<code>{html.escape(path, quote=False)}</code> {manage_web.MIDDOT} {tag}" in block, (
                name, item.path)
        for r in plan.skills:
            if not r.wrote:
                path, tag, _ = manage_web._skill_row(r)
                assert f"<code>{path}</code> {manage_web.MIDDOT} {tag}" in block, (name, r.name)
                continue
            # A copy the update writes lists every file the engine names
            # for it, each with what will happen to it (phase 3 review R9).
            for d in r.dispositions:
                tag = {mnt.CURRENT: "up to date", mnt.CREATED: "will add"}.get(
                    d.verb, "will update")
                assert (f"<code>{html.escape(d.path, quote=False)}</code> "
                        f"{manage_web.MIDDOT} {tag}") in block, (name, d.path)
    beta = details.split("<h3>beta</h3>", 1)[1].split("<h3>", 1)[0]
    for harness in sk.harnesses():
        assert (f"<code>{harness.skills}/{SKILL}/SKILL.md</code> {manage_web.MIDDOT} "
                f"{manage_web.TAG_WILL_UPDATE}") in beta
    delta = details.split("<h3>delta</h3>", 1)[1].split("<h3>", 1)[0]
    assert manage_web.TAG_WILL_UPDATE not in delta and manage_web.TAG_KEEPING not in delta
    # The body says counts, never the report's own head or its rows.
    head = main_of(card).split("<details>", 1)[0]
    assert "listed project(s)" not in head and "<table" not in main_of(card)
    assert run_.running


def test_each_reason_an_update_leaves_a_file_has_its_own_words():
    """Only a file the person edited is "Keeping your edits"; a file kept
    for another reason says that reason (wording.md, Screen 5)."""
    row = manage_web._seed_row
    item = mnt.UpgradeItem
    assert row(item("docs/a.md", mnt.UPDATED, mnt.UPGRADE_APPLIED)) == (
        "docs/a.md", "will update", "")
    assert row(item("docs/a.md", mnt.CREATED, mnt.UPGRADE_NEW_WRITTEN)) == (
        "docs/a.md", "will add", "")
    assert row(item("docs/a.md", mnt.CURRENT, mnt.UPGRADE_CURRENT)) == (
        "docs/a.md", "up to date", "")
    assert row(item("docs/a.md", mnt.LEFT, mnt.UPGRADE_PENDING)) == (
        "docs/a.md", "keeping your edits", "Keeping your edits")
    assert row(item("CLAUDE.md", mnt.LEFT, mnt.UPGRADE_HELD, detail="AGENTS.md")) == (
        "CLAUDE.md", "kept as it is", "Kept with your edited AGENTS.md")
    assert row(item("docs/a.md", mnt.ABSENT, mnt.UPGRADE_ABSENT)) == (
        "docs/a.md", "kept as it is", "You removed it, so it stays removed")
    assert row(item("docs/a.md", mnt.LEFT, mnt.UPGRADE_NEW_LEFT)) == (
        "docs/a.md", "kept as it is", "Your own file is already here, so it stays as it is")
    assert row(item(".mcp.json", mnt.LEFT, mnt.UPGRADE_MCP_UNREADABLE)) == (
        ".mcp.json", "kept as it is", "RadialRig can't read it, so it stays as it is")
    harness = sk.harnesses()[0]
    unread = sk.RefreshResult(harness=harness, name="", refreshed=None,
                              dispositions=(), error="the record could not be read")
    assert manage_web._skill_row(unread) == (
        harness.record, "kept as it is",
        f"RadialRig can't read the skill record for {harness.label}, so its skills won't "
        "be updated")


def test_an_update_whose_only_pending_work_is_an_unread_record_says_so(
    tmp_path, env, capsys, manage, fixed_day,
):
    """Phase 3 review R1: a project whose files and skills are current,
    but one agent's skill record can't be read, has nothing to write and
    still has something left for review (`UpdatePlan.review`, the command
    line's exit 5). The card says nothing will be updated and why; the
    press writes nothing and says the same, never that the project is up
    to date."""
    root = _make_project(tmp_path / "pit-wall").resolve()
    mnt.mount(root)
    sk.add_all(root, SKILL)
    ro.record_seen(root)
    second = sk.harnesses()[1]
    (root / second.record).write_bytes(b"{ not a record\n")
    plan = update.plan(root)
    assert not plan.wrote and plan.review
    why = (f"RadialRig can't read the skill record for {second.label}, so its skills won't "
           "be updated")
    run_ = manage()

    card = pressed(run_, {"action": manage_web.UPDATE, "root": str(root),
                          "from": manage_web.PROJECT})
    assert ("<p>Nothing will be updated. RadialRig leaves what's listed below as it "
            "is.</p>") in main_of(card)
    assert manage_web.UPDATE_NOTHING not in text_of(card)
    assert attention_rows(card) == [(second.record, why)]
    assert card_presses(card) == []

    before = tree(root)
    done = pressed(run_, {"action": manage_web.UPDATE, "root": str(root),
                          "confirm": "yes", "from": manage_web.PROJECT})
    assert tree(root) == before
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_PARTLY,
        "Nothing in this project was updated. RadialRig left what Details lists as it is. "
        f"Couldn't read the skill record for {second.label}, so its skills weren't updated. "
        "Details names it.")
    assert f"{second.record} {manage_web.MIDDOT} {why}" in details
    assert run_.running


def test_update_all_counts_a_stopped_skills_write_among_the_projects_it_couldnt_update(
    tmp_path, monkeypatch, env, capsys, manage, fixed_day, newer_release,
):
    """Phase 3 review R2: alpha's record refuses the update, and beta's
    skills write stops, its seeded files having nothing to write. No
    project wrote, but beta's stop may have left a file changed, so the
    line counts both projects and says so, never "Nothing was changed".
    Where beta's stop put everything back, "Nothing was changed" holds,
    still for both projects."""
    page_set, _ = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    path = page_set["alpha"] / mnt.MANIFEST_REL
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["schema"] = int(manifest["schema"]) + 1
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    run_ = manage()
    card = pressed(run_, {"action": manage_web.UPDATE_ALL, "from": manage_web.ROSTER})
    (confirm,) = card_presses(card)
    landed = f"{sk.harnesses()[0].skills}/{SKILL}/SKILL.md"
    real_write = sk.write_add_plans
    stopped = [""]

    def stop(plans):
        plans = list(plans)
        if plans:
            raise sk.SkillError(stopped[0])
        return real_write(plans)

    monkeypatch.setattr(sk, "write_add_plans", stop)
    for left, sentence in (
        ((landed,), "Couldn't update 2 projects. Some of their files may be left changed. "
                    "Check Details for the list."),
        ((), "Couldn't update 2 projects. Nothing was changed. Check Details for what "
             "stopped them."),
    ):
        stopped[0] = mnt.stopped_sentence(landed, "denied", left)
        done = pressed(run_, confirm)
        tone, said, _, details = result_of(done)
        assert said == sentence, left
        assert tone == (manage_web.RESULT_PARTLY if left else manage_web.RESULT_FAILED)
        assert f"beta: {stopped[0]}" in details
        assert "alpha: " in details
    assert run_.running


# --- Clean up, from a row of Projects (PLAN-0027 phase 3, prescription 5)


def gone_entries(config: Path, root: Path, *keys: str) -> None:
    """A gone folder's entries, one per key, pinned to it as a connect
    leaves them."""
    for key in keys:
        loose_entry(config, key, root)


def test_clean_up_takes_out_every_entry_of_a_gone_folder_on_one_press(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """One press runs each entry's `--orphan` command in turn, the bytes
    the command line writes, and answers on Projects with the line from
    `wording.md`; each command leaves its own event in the activity log,
    inside the one-write queue as every press does."""
    vanished = tmp_path / "vanished"
    keys = (f"{PREFIX}vanished", f"{PREFIX}vanished-write")
    gone_entries(client_here, vanished, *keys)
    pristine = client_here.read_bytes()
    label = cn.CLIENTS[CLIENT].label
    run_ = manage()
    page = run_.page()
    row = re.search(r'<li class="roster-row leftover-row">(.*?)</li>', main_of(page), re.S).group(1)
    form = dict(HIDDEN.findall(re.search(
        r'<form method="post" action="[^"]+">(.*?)</form>', row, re.S).group(1)))
    form.pop("token")
    assert form == {"action": manage_web.ORPHAN, "client": CLIENT, "root": str(vanished),
                    "confirm": "yes", "from": manage_web.ROSTER}

    logged_before = len(log_events())
    done = pressed(run_, form)
    page_bytes = client_here.read_bytes()
    for key in keys:
        assert key not in page_bytes.decode("utf-8"), key
    assert screen_of(done) == "projects"
    tone, sentence, warning, details = result_of(done)
    assert (tone, sentence, warning) == (
        manage_web.RESULT_DONE,
        f"Removed the leftover {label} connection for vanished. Fully quit and reopen "
        f"{label} to apply the change.", "")
    assert '<li class="roster-row leftover-row">' not in main_of(done)
    events = logged(logged_before)
    assert [(what, outcome) for what, outcome, _ in events] == [
        ("press orphan confirm", f"{manage_web.DONE} exit {report.OK}")] * 2
    # The command line, on the same entries, writes the same bytes.
    client_here.write_bytes(pristine)
    for key in keys:
        code, out, err = run(capsys, "unconnect", CLIENT, "--orphan", key)
        assert (code, err) == (report.OK, ""), key
    assert client_here.read_bytes() == page_bytes
    # Each event is its command's own report, in the order they ran.
    for (_, _, lines), key in zip(events, keys):
        assert lines[0].startswith(f"Removed the entry {key} from {label}"), lines[0]
    assert run_.running


def test_clean_up_says_what_it_did_when_it_stops_or_finds_nothing(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    """Each of Clean up's other lines, true of what happened: a stop
    between a folder's entries leaves the first out and says so; a folder
    that is back refuses and changes nothing; entries gone since the row
    was drawn change nothing; a stop at the first entry changes nothing."""
    label = cn.CLIENTS[CLIENT].label
    run_ = manage()

    # A stop at the second entry, after the first went.
    vanished = tmp_path / "vanished"
    keys = (f"{PREFIX}vanished", f"{PREFIX}vanished-write")
    gone_entries(client_here, vanished, *keys)
    real = orphan.remove

    def second_stops(client, key, **kwargs):
        if key == keys[1]:
            raise cn.WriteStopped(str(client_here), "denied")
        return real(client, key, **kwargs)

    monkeypatch.setattr(orphan, "remove", second_stops)
    fields = {"action": manage_web.ORPHAN, "client": CLIENT, "root": str(vanished),
              "confirm": "yes", "from": manage_web.ROSTER}
    done = pressed(run_, fields)
    held = client_here.read_text(encoding="utf-8")
    assert keys[0] not in held.replace(keys[1], "") and keys[1] in held
    assert result_of(done)[:2] == (
        manage_web.RESULT_PARTLY,
        f"Removed some of vanished's leftover {label} entries, but couldn't remove the "
        f"rest. Fully quit and reopen {label} to apply the change. Check Details for "
        "what stopped it.")
    monkeypatch.setattr(orphan, "remove", real)

    # The same press at the first entry, its write put back: nothing changed.
    monkeypatch.setattr(orphan, "remove", lambda client, key, **kw: (_ for _ in ()).throw(
        cn.WriteStopped(str(client_here), "denied")))
    before = client_here.read_bytes()
    done = pressed(run_, fields)
    assert client_here.read_bytes() == before
    assert result_of(done)[:2] == (
        manage_web.RESULT_FAILED,
        "Couldn't remove the leftover connection for vanished. Nothing was changed.")
    monkeypatch.setattr(orphan, "remove", real)

    # The folder is back: its entries aren't leftovers now.
    vanished.mkdir()
    before = client_here.read_bytes()
    done = pressed(run_, fields)
    assert client_here.read_bytes() == before
    assert result_of(done)[:2] == (
        manage_web.RESULT_FAILED,
        f"vanished's folder is back on this computer, so its {label} entries aren't "
        "leftovers now. Nothing was changed.")
    vanished.rmdir()

    # The entries went since the row was drawn.
    assert run(capsys, "unconnect", CLIENT, "--orphan", keys[1])[0] == report.OK
    done = pressed(run_, fields)
    assert result_of(done)[:2] == (
        manage_web.RESULT_DONE,
        f"vanished's leftover {label} entries are already gone. Nothing was changed.")
    assert run_.running


def test_clean_up_carries_the_running_warnings(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Clean up writes Claude Desktop's settings, so while it is open the
    row carries the before-press sentence and the result line the
    after-press one from `wording.md`, once."""
    vanished = tmp_path / "vanished"
    gone_entries(client_here, vanished, f"{PREFIX}vanished")
    plant_lock(client_here)
    client = cn.CLIENTS[CLIENT]
    run_ = manage()
    row = re.search(r'<li class="roster-row leftover-row">(.*?)</li>',
                    main_of(run_.page()), re.S).group(1)
    ahead = report.client_running_before(client)
    assert f'<p class="warn">{html.escape(ahead, quote=False)}</p>' in row
    done = pressed(run_, {"action": manage_web.ORPHAN, "client": CLIENT,
                          "root": str(vanished), "confirm": "yes", "from": manage_web.ROSTER})
    tone, _, warning, details = result_of(done)
    assert warning == (f"{client.label} is running and may put the leftover entry back. Fully "
                       "quit it and reopen it; if the entry is back on Projects, press Clean "
                       "up again.")
    assert report.orphan_running_after(client) not in details
    assert text_of(done).count(f"{client.label} is running and may") == 1
    assert run_.running


# --- the big changes wait their turn (PLAN-0027 phase 3) ----------------

def test_the_big_changes_wait_their_turn_in_the_one_write_queue(
    tmp_path, env, client_here, command, capsys, manage, fixed_day, newer_release,
):
    """Update, Update all, Remove from this project, its keep box and
    Clean up each wait at the queue while another write holds it, write
    nothing while they wait, and run once it is free."""
    page_set, _ = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    keep = connected_host(tmp_path, "keep-host")
    gone = connected_host(tmp_path, "gone-host")
    vanished = tmp_path / "vanished"
    gone_entries(client_here, vanished, f"{PREFIX}vanished")
    run_ = manage()
    real = run_.session.actions
    gate = Gate(real)
    run_.session.actions = gate
    try:
        for fields, settled in (
            ({"action": manage_web.UPDATE, "root": str(page_set["alpha"]),
              "from": manage_web.PROJECT}, lambda: not update.behind(page_set["alpha"])),
            ({"action": manage_web.UPDATE_ALL, "from": manage_web.ROSTER},
             lambda: not update.behind(page_set["beta"])),
            ({"action": manage_web.UNMOUNT, "root": str(keep), "from": manage_web.PROJECT},
             lambda: not mnt.is_mounted(keep) and cn.is_connected(keep, CLIENT)),
            ({"action": manage_web.REVERSAL, "root": str(gone), "from": manage_web.PROJECT},
             lambda: not mnt.is_mounted(gone) and not cn.is_connected(gone)),
            ({"action": manage_web.ORPHAN, "client": CLIENT, "root": str(vanished),
              "from": manage_web.ROSTER},
             lambda: f"{PREFIX}vanished" not in client_here.read_text(encoding="utf-8")),
        ):
            answers: list[str] = []
            gate.waiting.clear()
            before = (tree(keep), tree(gone), client_here.read_bytes())
            with real:
                worker = threading.Thread(target=lambda fields=fields: answers.append(
                    pressed(run_, {**fields, "confirm": "yes"})))
                worker.start()
                assert gate.waiting.wait(20), f"{fields['action']} never reached the queue"
                assert worker.is_alive() and answers == []
                assert (tree(keep), tree(gone), client_here.read_bytes()) == before, fields
            worker.join(20)
            assert not worker.is_alive()
            assert settled(), fields
            assert result_of(answers[0])[0] == manage_web.RESULT_DONE, fields
    finally:
        run_.session.actions = real
    assert run_.running


# --- PLAN-0025 phase 2, group 3: every press's answer to its kit's errors
# Each press runs one engine call and reads a kit error as one page state.
# The call is replaced by one that raises, rehearsed and run alike, so each
# rung of each press's ladder is pinned without a disk that fails. A
# stopped write is a `MountError` or a `ConnectError` too, which is why the
# order of the rungs matters: read after them, a partial stop would show
# as a plain failure.

LEFT_BEHIND = ("C:/stand-in/claude_desktop_config.json",)
CONFLICTS = (mnt.Disposition(f".claude/skills/{SKILL}/", mnt.LEFT, mnt.SKILL_DIR_FOREIGN),)


def _raising(exc):
    def raise_(*args, **kwargs):
        raise exc
    return raise_


def _skill_stop():
    """A skills-kit error carrying a stopped write as its cause."""
    exc = sk.SkillError("the skill stopped")
    exc.__cause__ = mnt.WriteStopped("C:/stand-in/SKILL.md", "denied", LEFT_BEHIND)
    return exc


PRESS_CALLS = {
    manage_web.UNMOUNT: ((mnt, "unmount_plan"), (mnt, "unmount")),
    manage_web.UPDATE: ((update, "plan"), (update, "run")),
    manage_web.UNCONNECT: ((cn, "unconnect_plan"), (cn, "unconnect")),
    manage_web.ORPHAN: ((orphan, "remove_plan"), (orphan, "remove")),
    manage_web.CONNECT: ((cn, "connect_plan"), (cn, "connect")),
    manage_web.SKILL_ADD: ((sk, "add_all_plan"), (sk, "add_all")),
    manage_web.SKILL_ADD_ALL: ((sk, "add_catalog_plan"), (sk, "add_catalog")),
    manage_web.SKILL_REMOVE: ((sk, "remove_all_plan"), (sk, "remove_all")),
}
MOUNT_KIT = (manage_web.UNMOUNT, manage_web.UPDATE)
CLIENT_KIT = (manage_web.UNCONNECT, manage_web.ORPHAN, manage_web.CONNECT)
SKILL_KIT = (manage_web.SKILL_ADD, manage_web.SKILL_ADD_ALL, manage_web.SKILL_REMOVE)

# (presses, the error, what the state says: "partial", "failed" or "refused")
KIT_ERRORS = [
    *((name, lambda: mnt.WriteStopped("C:/x", "denied", LEFT_BEHIND), "partial")
      for name in MOUNT_KIT + CLIENT_KIT + SKILL_KIT),
    *((name, lambda: cn.WriteStopped("C:/x", "denied", LEFT_BEHIND), "partial")
      for name in MOUNT_KIT + CLIENT_KIT + SKILL_KIT),
    *((name, lambda: mnt.WriteStopped("C:/x", "denied"), "failed") for name in MOUNT_KIT),
    *((name, lambda: cn.WriteStopped("C:/x", "denied"), "failed") for name in CLIENT_KIT),
    *((name, lambda: mnt.MountError("the record could not be read"), "failed")
      for name in MOUNT_KIT),
    *((name, lambda: cn.ConnectError("the settings could not be read"), "failed")
      for name in CLIENT_KIT),
    *((name, lambda: cn.UnknownClient("no-such-client", ("a", "b")), "failed")
      for name in CLIENT_KIT),
    (manage_web.CONNECT, lambda: cn.ConnectRefused(cn.CLIENTS[CLIENT], CONFLICTS), "refused"),
    (manage_web.ORPHAN, lambda: orphan.OrphanRefused("that project still stands"), "refused"),
    *((name, lambda: sk.SkillError("the skill could not be read"), "failed")
      for name in SKILL_KIT),
    *((name, _skill_stop, "partial") for name in SKILL_KIT),
    *((name, lambda: sk.UnknownSkill("no-such-skill", ("a",)), "failed") for name in SKILL_KIT),
    *((name, lambda: sk.SkillConflict(CONFLICTS), "refused") for name in SKILL_KIT),
]


@pytest.mark.parametrize("name, make, says", KIT_ERRORS)
def test_every_press_reads_its_kits_errors_as_the_page_says_them(
    name, make, says, tmp_path, monkeypatch,
):
    exc = make()
    for module, attr in PRESS_CALLS[name]:
        monkeypatch.setattr(module, attr, _raising(exc))
    monkeypatch.setattr(mnt, "is_mounted", lambda root: True)
    root = tmp_path.resolve()
    act = manage_web.Action(
        name, root=str(root), skill=SKILL, client=CLIENT, key=f"{PREFIX}gone")
    for dry_run in (True, False):
        state = manage_web.RUNNERS[name](act, dry_run=dry_run)
        if says == "partial":
            assert state == setup_web.PageState(
                manage_web.FAILED, report.PARTIAL, False, result=act,
                lines=(setup_web.PARTIAL_HEAD,), message=str(exc)), (name, dry_run)
        elif says == "failed":
            if isinstance(exc, cn.UnknownClient):
                message = "; ".join(report.connect_unknown_client_report(exc.name, exc.available))
            elif isinstance(exc, sk.UnknownSkill):
                message = "; ".join(report.skill_unknown_report(exc.name, exc.available))
            else:
                message = str(exc)
            assert state == setup_web.PageState(
                manage_web.FAILED, report.ERROR, False, result=act,
                message=message), (name, dry_run)
        else:
            if isinstance(exc, cn.ConnectRefused):
                lines = report.connect_refused_report(
                    exc.client, root, exc.conflicts, dry_run=dry_run)
            elif isinstance(exc, orphan.OrphanRefused):
                lines = report.orphan_refused_report(str(exc), dry_run=dry_run)
            else:
                lines = report.skill_conflict_report(exc.conflicts, dry_run=dry_run)
            conflicts = () if isinstance(exc, orphan.OrphanRefused) else CONFLICTS
            assert state == setup_web.PageState(
                manage_web.REFUSED, report.REFUSED, False, dispositions=conflicts,
                lines=manage_web._sentences(lines, conflicts), result=act), (name, dry_run)


def test_a_press_with_nothing_to_do_answers_the_no_op_code(tmp_path, env, command):
    """The command line's exit 4 for the same press, on the page's state:
    nothing mounted to unmount or update, nothing registered to remove."""
    untouched = _make_project(tmp_path / "untouched").resolve()
    for name in (manage_web.UNMOUNT, manage_web.UPDATE, manage_web.UNCONNECT):
        act = manage_web.Action(name, root=str(untouched), client=CLIENT)
        for dry_run in (True, False):
            state = manage_web.RUNNERS[name](act, dry_run=dry_run)
            assert (state.kind, state.outcome) == (
                manage_web.PLAN if dry_run else manage_web.DONE, report.NOOP), (name, dry_run)


def test_unmount_and_unconnect_list_the_project_only_when_run(tmp_path, env, command):
    """Unmount and unconnect keep the roster's side entry on a real run
    and on their already-done exits, and a rehearsal writes none. The
    projects are mounted and connected through the engine, which keeps no
    side entry, so the listing starts without them."""
    mounted = _make_project(tmp_path / "mounted").resolve()
    mnt.mount(mounted)
    connected = _make_project(tmp_path / "connected").resolve()
    mnt.mount(connected)
    cn.connect(connected, CLIENT)
    presses = [(manage_web.UNMOUNT, mounted), (manage_web.UNCONNECT, connected)]
    for name in (manage_web.UNMOUNT, manage_web.UNCONNECT):
        presses.append((name, _make_project(tmp_path / f"untouched-{name}").resolve()))
    for name, root in presses:
        act = manage_web.Action(name, root=str(root), client=CLIENT)
        manage_web.RUNNERS[name](act, dry_run=True)
        assert str(root) not in [state.root for state in ro.listing()], (name, root.name)
        manage_web.RUNNERS[name](act, dry_run=False)
        assert str(root) in [state.root for state in ro.listing()], (name, root.name)


def test_an_unmount_press_answers_in_the_reports_code_rows_and_lines(tmp_path, env):
    """An unmount that leaves an edited file, rehearsed and run: the
    partial code, the plan's rows, and the report's own lines."""
    root = _make_project(tmp_path / "host").resolve()
    mnt.mount(root)
    (root / "docs" / "spec.md").write_bytes(b"# ours now\n")
    plan = mnt.unmount_plan(root)
    assert not plan.complete and plan.dispositions
    act = manage_web.Action(manage_web.UNMOUNT, root=str(root))
    for dry_run in (True, False):
        state = manage_web.RUNNERS[manage_web.UNMOUNT](act, dry_run=dry_run)
        assert state == setup_web.PageState(
            manage_web.PLAN if dry_run else manage_web.DONE, report.PARTIAL, False,
            dispositions=plan.dispositions,
            lines=manage_web._sentences(
                cli._unmount_report(plan, dry_run=dry_run), plan.dispositions),
            result=act), dry_run


def test_an_orphan_press_answers_in_the_reports_code_rows_and_lines(tmp_path, monkeypatch):
    """A key no copy carries is the no-op code and the not-found
    sentence. A key one copy carries, beside a copy that does not read,
    is removed where it can be and the other copy is left: the partial
    code, the plan's rows, and the report's lines in the orphan kit's
    own wording."""
    mapping = fake_client(tmp_path, store=True)
    for name, value in mapping.items():
        monkeypatch.setenv(name, value)
    key = f"{PREFIX}old"
    body = {"mcpServers": {key: {"command": "x", "args": [],
                                 "env": {ROOT_ENV: str(tmp_path / "vanished")}}}}
    roaming_config(mapping).write_bytes((json.dumps(body, indent=2) + "\n").encode("utf-8"))
    store_config(mapping).write_bytes(b"\xff\xfe not text\n")

    missing = manage_web.Action(manage_web.ORPHAN, client=CLIENT, key=f"{PREFIX}none")
    for dry_run in (True, False):
        state = manage_web.RUNNERS[manage_web.ORPHAN](missing, dry_run=dry_run)
        assert state == setup_web.PageState(
            manage_web.PLAN if dry_run else manage_web.DONE, report.NOOP, False,
            lines=manage_web._sentences(
                report.orphan_not_found_report(cn.CLIENTS[CLIENT], missing.key), ()),
            result=missing), dry_run

    plan = orphan.remove_plan(CLIENT, key)
    assert plan.found and not plan.complete and plan.dispositions
    act = manage_web.Action(manage_web.ORPHAN, client=CLIENT, key=key)
    for dry_run in (True, False):
        state = manage_web.RUNNERS[manage_web.ORPHAN](act, dry_run=dry_run)
        assert state == setup_web.PageState(
            manage_web.PLAN if dry_run else manage_web.DONE, report.PARTIAL, False,
            dispositions=plan.dispositions,
            lines=manage_web._sentences(report.orphan_report(
                plan, resolve=orphan.reason_text, dry_run=dry_run), plan.dispositions),
            result=act), dry_run


def test_an_update_press_answers_in_the_reports_code_rows_and_lines(
    tmp_path, env, newer_release, fixed_day,
):
    """One project's update with an edited skill left for review,
    rehearsed and run: the partial code, both halves' rows, and the
    report's own lines for a rehearsal and for a run."""
    roots = seed_projects(tmp_path / "page")
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    finish_projects(roots)
    act = manage_web.Action(manage_web.UPDATE, root=str(roots["gamma"]))
    for dry_run in (True, False):
        state = manage_web.RUNNERS[manage_web.UPDATE](act, dry_run=dry_run)
        plan = state.result[1]
        assert update_rows(plan), dry_run
        assert (state.kind, state.outcome, state.dispositions, state.lines) == (
            manage_web.UPDATE_PLAN if dry_run else manage_web.UPDATED, report.PARTIAL,
            tuple(update_rows(plan)), tuple(report.update_report(plan, dry_run=dry_run)),
        ), dry_run


def test_an_update_all_press_answers_in_the_reports_code_and_lines(
    tmp_path, env, newer_release, fixed_day,
):
    """Every listed project's update: with nothing listed, the no-op code
    and the one sentence; with a project whose edited skill is left for
    review, the worst project's code and the report's own lines."""
    act = manage_web.Action(manage_web.UPDATE_ALL)
    for dry_run in (True, False):
        state = manage_web.RUNNERS[manage_web.UPDATE_ALL](act, dry_run=dry_run)
        assert (state.outcome, state.lines) == (report.NOOP, (report.PROJECTS_NONE,)), dry_run
    roots = seed_projects(tmp_path / "page")
    newer_release(seed_edit=edit_spec, skill_edit=edit_skill)
    finish_projects(roots)
    list_projects(roots)
    for dry_run in (True, False):
        state = manage_web.RUNNERS[manage_web.UPDATE_ALL](act, dry_run=dry_run)
        found = state.result[1]
        assert (state.outcome, state.lines) == (
            report.PARTIAL, tuple(report.update_all_report(found, dry_run=dry_run))), dry_run


# --- BUG-0018: the warning reaches the page before the press -------------

def test_an_open_client_is_named_on_the_plan_before_the_confirm_press(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    from radialrig.tests.test_connect import plant_lock

    client = cn.CLIENTS[CLIENT]
    root = _host(tmp_path / "page-host")
    mnt.mount(root, scope=SCOPE)
    cn.connect(root, CLIENT)
    plant_lock(client_here)
    ahead = report.client_running_before(client)

    run_ = manage()
    fields = {"action": manage_web.UNCONNECT, "root": str(root), "client": CLIENT}
    status, plan, _ = run_.post(press(run_, fields))
    assert status == 200
    said = text_of(plan)
    assert ahead in said
    assert said.index(ahead) < said.index(manage_web.CONFIRM_LABEL)

    status, done, _ = run_.post(press(run_, fields, confirm=True))
    assert status == 200
    key = cn.entry_key(client, SCOPE)
    assert report.unconnect_running_after(
        client, (key, key + cn.WRITE_KEY_SUFFIX)) in text_of(done)


# --- one press, one result line (PLAN-0027 phase 1) ----------------------
# A small change runs on its one press and brings back the screen it was
# made on, with one line at the top saying what happened (prescriptions 6
# to 8). Every press here is posted as the screen drew it, runs inside
# the one-write queue, writes what its command writes, and leaves its line
# in the activity log.

from radialrig import activity_log as al  # noqa: E402
from radialrig.tests.test_connect import plant_lock  # noqa: E402

NOW = {"confirm": "yes", "from": manage_web.PROJECT}


def log_events() -> list[str]:
    path = al.log_path()
    assert path is not None, "the suite runs on a platform that keeps a log"
    return al.events(path.read_text(encoding="utf-8")) if path.is_file() else []


def logged(before: int) -> list[tuple[str, str, list[str]]]:
    """The manage page's press events since `before`: what ran, its
    outcome field, and its detail lines."""
    out = []
    for event in log_events()[before:]:
        head, *detail = event.splitlines()
        fields = head.split("  ")
        if fields[2] == al.MANAGE_PAGE and fields[4].startswith("press "):
            out.append((fields[4], fields[-1], [line.strip() for line in detail]))
    return out


def connected_host(tmp_path: Path, name: str) -> Path:
    """A mounted project with one skill for every agent and its client
    connection, made by the engine the command line drives."""
    root = _host(tmp_path / name)
    mnt.mount(root, scope=name)
    sk.add_all(root, SKILL)
    cn.connect(root, CLIENT)
    ro.record_seen(root)
    return root


def files(root: Path) -> dict[str, str]:
    """Every file but the connection record, which names its own root
    and so differs between any two projects."""
    return {k: v for k, v in snapshot(root).items() if k != cn.CONNECT_REL}


def screen_of(page: str) -> str:
    """Which redesigned screen a page is, by its root element."""
    found = re.search(r'<div class="screen ([a-z]+)">', main_of(page))
    return found.group(1) if found else ""


def test_each_small_change_runs_on_one_press_and_answers_on_its_screen(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescription 6, each one-press change once: a skill on, a skill
    off, Add all, Disconnect and Connect. Each answers on the project's
    screen with its line from `wording.md` at the top, done in tone, the
    files it changed behind Details with their tags, and the grid or the
    row redrawn as the project now stands. What it wrote is what the
    engine writes for the same command, and each leaves its event in the
    activity log, under the runner's own answer."""
    root = connected_host(tmp_path, "page-host")
    twin = _host(tmp_path / "twin-host")
    mnt.mount(twin, scope="page-host")
    sk.add_all(twin, SKILL)
    first, second = sk.harnesses()[:2]
    other = next(name for name in sk.catalog_names() if name != SKILL)
    label = cn.CLIENTS[CLIENT].label
    run_ = manage()

    # A skill on, from its Off cell.
    page = project_page(run_, root)
    cell = grid_presses(page)[(other, first.label)]
    assert cell == {"action": manage_web.SKILL_ADD, "root": str(root), "skill": other,
                    "harness": first.name, **NOW}
    before = len(log_events())
    done = pressed(run_, cell)
    results = sk.add_all(twin, other, targets=(first.name,))
    assert files(root) == files(twin)
    tone, sentence, warning, details = result_of(done)
    assert (tone, sentence, warning) == (
        manage_web.RESULT_DONE,
        f"Added {other} for {first.label}. Start a new agent session to pick it up.", "")
    for d in (d for r in results for d in r.dispositions):
        if d.verb == mnt.CREATED:
            assert f"{d.path} {manage_web.MIDDOT} added" in details, d.path
    assert screen_of(done) == "project"
    body = main_of(done)
    assert body.index('<div class="result done"') < body.index('<div class="status-band">')
    assert body.index('<div class="project-name">') < body.index('<div class="result done"')
    assert grid_cells(done)[(other, first.label)] == ("on", "On")
    assert grid_cells(done)[(other, second.label)] == ("off", "Off")
    ((what, outcome, lines),) = logged(before)
    assert what == "press skill-add confirm"
    assert outcome == f"{manage_web.DONE} exit {report.OK}"
    assert lines[0] == report.skill_add_reports(results)[0].replace(str(twin), str(root))

    # The same skill off again, from its On cell.
    cell = grid_presses(done)[(other, first.label)]
    assert cell["action"] == manage_web.SKILL_REMOVE
    before = len(log_events())
    gone = pressed(run_, cell)
    plans = sk.remove_all(twin, other, targets=(first.name,))
    assert files(root) == files(twin)
    assert result_of(gone)[:3] == (
        manage_web.RESULT_DONE,
        f"Removed {other} for {first.label}. Start a new agent session to pick up the change.",
        "")
    assert f"{first.skills}/{other}/SKILL.md {manage_web.MIDDOT} removed" in result_of(gone)[3]
    assert grid_cells(gone)[(other, first.label)] == ("off", "Off")
    ((what, outcome, _),) = logged(before)
    assert (what, outcome) == ("press skill-remove confirm",
                               f"{manage_web.DONE} exit {report.OK if all(p.complete for p in plans) else report.PARTIAL}")

    # Add all, for the second agent, from its column head.
    head = agent_heads(gone)[second.label]
    assert head == {"action": manage_web.SKILL_ADD_ALL, "root": str(root),
                    "harness": second.name, **NOW}
    before = len(log_events())
    filled = pressed(run_, head)
    sk.add_catalog(twin, targets=(second.name,))
    assert files(root) == files(twin)
    assert result_of(filled)[:3] == (
        manage_web.RESULT_DONE,
        f"Added all skills for {second.label}. Start a new agent session to pick them up.", "")
    assert {grid_cells(filled)[(name, second.label)] for name in sk.catalog_names()} == {
        ("on", "On")}
    assert agent_heads(filled)[second.label] is None
    assert [what for what, _, _ in logged(before)] == ["press skill-add-all confirm"]

    # Disconnect, from the Connections row.
    pristine_record = cn.record_path(root).read_bytes()
    form = re.search(r'<form method="post" action="[^"]+">(.*?)</form>',
                     connections_of(filled), re.S).group(1)
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form))
    fields.pop("token")
    assert fields == {"action": manage_web.UNCONNECT, "root": str(root),
                      "client": CLIENT, **NOW}
    before = len(log_events())
    undone = pressed(run_, fields)
    assert not cn.is_connected(root)
    assert not cn.record_path(root).is_file()
    tone, sentence, warning, details = result_of(undone)
    assert (tone, sentence, warning) == (
        manage_web.RESULT_DONE,
        f"Disconnected {label}. Fully quit and reopen {label} to apply the change.", "")
    assert f"{cn.CONNECT_REL} {manage_web.MIDDOT} removed" in details
    assert f"{client_here.as_posix()} {manage_web.MIDDOT} updated" in details
    assert f'aria-label="Connect {label}"' in connections_of(undone)
    ((what, outcome, lines),) = logged(before)
    assert (what, outcome) == ("press unconnect confirm", f"{manage_web.DONE} exit {report.OK}")

    # Connect, from the same row, and the same bytes the command writes.
    fields["action"] = manage_web.CONNECT
    before = len(log_events())
    joined = pressed(run_, fields)
    assert cn.is_connected(root, CLIENT)
    assert result_of(joined)[:3] == (
        manage_web.RESULT_DONE,
        f"Connected {label}. Fully quit and reopen {label} to use the connection.", "")
    page_record, page_config = cn.record_path(root).read_bytes(), client_here.read_bytes()
    assert page_record == pristine_record
    assert run(capsys, "unconnect", CLIENT, str(root))[0] == report.OK
    assert run(capsys, "connect", CLIENT, str(root))[0] == report.OK
    assert (cn.record_path(root).read_bytes(), client_here.read_bytes()) == (
        page_record, page_config)
    assert [what for what, _, _ in logged(before)] == ["press connect confirm"]

    # A confirm press with no `from` still lands on today's result page,
    # which the plan pages post.
    fields = {"action": manage_web.SKILL_ADD, "root": str(root), "skill": other,
              "harness": first.name}
    today = pressed(run_, fields, confirm=True)
    assert result_of(today) is None
    assert screen_of(today) == ""
    assert manage_web.DONE_CAPTION in text_of(today)
    assert run_.running


def test_repair_runs_on_one_press_from_the_project_and_from_projects(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescription 6 for the repair: from the project's status and from
    a row of Projects, each answers on the screen it came from with its
    line, the project reconnected, and the repair's event in the log."""
    label = cn.CLIENTS[CLIENT].label
    roots = {}
    for name in ("pit-wall", "brake-bias"):
        roots[name] = connected_host(tmp_path, name)
        drop_entry(client_here, record_of(roots[name])["key"])
        assert ro.verify(roots[name]).connected == ro.CONNECTED_MISMATCH
    run_ = manage()

    page = project_page(run_, roots["pit-wall"])
    form = [dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', inner))
            for inner in re.findall(r'<form method="post" action="[^"]+">(.*?)</form>',
                                    main_of(page), re.S)]
    repair = next(f for f in form if f.get("action") == manage_web.REPAIR)
    repair.pop("token")
    assert repair == {"action": manage_web.REPAIR, "root": str(roots["pit-wall"]), **NOW}
    before = len(log_events())
    done = pressed(run_, repair)
    assert cn.is_connected(roots["pit-wall"], CLIENT)
    tone, sentence, warning, details = result_of(done)
    assert (tone, sentence, warning) == (
        manage_web.RESULT_DONE,
        f"Reconnected {label}. Fully quit and reopen {label} to use the connection.", "")
    assert f"{cn.CONNECT_REL} {manage_web.MIDDOT}" in details
    assert screen_of(done) == "project"
    assert status_band(done) == [("ok", "RadialRig is working in this project.", "")]
    ((what, outcome, _),) = logged(before)
    assert (what, outcome) == ("press repair confirm", f"{manage_web.REPAIRED} exit {report.OK}")

    listing = run_.page()
    row = roster_row(listing, "brake-bias")
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', row))
    fields.pop("token")
    fields.pop("s", None)
    fields.pop("view", None)
    assert fields == {"action": manage_web.REPAIR, "root": str(roots["brake-bias"]),
                      "confirm": "yes", "from": manage_web.ROSTER}
    before = len(log_events())
    done = pressed(run_, fields)
    assert cn.is_connected(roots["brake-bias"], CLIENT)
    assert result_of(done)[:3] == (
        manage_web.RESULT_DONE,
        f"Reconnected brake-bias to {label}. Fully quit and reopen {label} to use it.", "")
    assert screen_of(done) == "projects"
    body = main_of(done)
    assert body.index("</header>") < body.index('<div class="result done"') < body.index(
        '<ul class="roster">')
    assert [status for _, _, status, _, _ in roster_rows(done)] == ["Working", "Working"]
    assert [what for what, _, _ in logged(before)] == ["press repair confirm"]
    assert run_.running


def test_repairs_guard_is_asked_again_when_the_press_arrives(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescription 7: the screen was drawn earlier, so the guard is asked
    again inside the queue. Where it withholds Repair now, nothing runs,
    nothing is written, and the line says why in the words the status
    uses, with what's in the way behind Details; from Projects, the line
    names the project. The log keeps the refusal."""
    label = cn.CLIENTS[CLIENT].label
    root = connected_host(tmp_path, "pit-wall")
    drop_entry(client_here, record_of(root)["key"])
    run_ = manage()
    assert status_band(project_page(run_, root))[0][2] == "Repair"
    assert roster_rows(run_.page())[0][4] == "Repair"

    # Something else rewrites the settings file into text that isn't
    # JSON after both screens were drawn.
    client_here.write_bytes(b"{ not json\n")
    before_bytes = client_here.read_bytes()
    record = cn.record_path(root).read_bytes()
    reached = []
    for verb in (manage_web.UNCONNECT, manage_web.CONNECT):
        real = manage_web.RUNNERS[verb]
        monkeypatch.setitem(manage_web.RUNNERS, verb,
                            lambda act, *, dry_run, real=real: (
                                reached.append((act.name, dry_run)) or real(act, dry_run=dry_run)))
    guard = manage_web._repair_guard(ro.verify(root))
    assert not guard.offered and guard.blockers
    reached.clear()

    before = len(log_events())
    done = pressed(run_, {"action": manage_web.REPAIR, "root": str(root), **NOW})
    assert all(dry_run for _, dry_run in reached), "a verb ran for real"
    assert client_here.read_bytes() == before_bytes
    assert cn.record_path(root).read_bytes() == record
    tone, sentence, warning, details = result_of(done)
    assert tone == manage_web.RESULT_FAILED
    assert sentence == manage_web.REPAIR_BLOCKED.format(label=label) + " " + manage_web.REPAIR_SEE_DETAILS
    assert sentence == guard.why
    for line in guard.blockers:
        assert " ".join(line.split()) in details
    assert status_band(done)[0] == ("repair", guard.why, "")
    ((what, outcome, lines),) = logged(before)
    assert (what, outcome) == ("press repair confirm",
                               f"{manage_web.REFUSED} exit {report.REFUSED}")
    assert lines[0] == guard.why

    reached.clear()
    done = pressed(run_, {"action": manage_web.REPAIR, "root": str(root),
                          "confirm": "yes", "from": manage_web.ROSTER})
    assert all(dry_run for _, dry_run in reached), "a verb ran for real"
    assert client_here.read_bytes() == before_bytes
    tone, sentence, _, details = result_of(done)
    assert tone == manage_web.RESULT_FAILED
    assert sentence == (f"Couldn't repair pit-wall's {label} link right now. Nothing was "
                        "changed. Check Details to see why.")
    assert " ".join(guard.why.split()) in details
    assert screen_of(done) == "projects"

    # And where the link isn't lost any more, there is nothing to repair.
    client_here.write_bytes(CONFIG_HOSTS["no-servers"])
    cn.record_path(root).unlink()
    done = pressed(run_, {"action": manage_web.REPAIR, "root": str(root), **NOW})
    assert result_of(done)[:2] == (
        manage_web.RESULT_DONE,
        f"This project's {label} link no longer needs repair. Nothing was changed.")
    assert run_.running


def test_a_press_says_nothing_was_written_only_where_nothing_was(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescription 6, every sentence true: "Nothing was changed" only
    where the engine refused, or failed and put back what it wrote; a press that
    changed something and then stopped, or changed less than asked, says
    what it did."""
    label = cn.CLIENTS[CLIENT].label
    first, second = sk.harnesses()[:2]
    other = next(name for name in sk.catalog_names() if name != SKILL)
    run_ = manage()

    # A refusal: a folder of the project's own where the skill goes.
    root = connected_host(tmp_path, "pit-wall")
    (root / first.skills / other).mkdir(parents=True)
    (root / first.skills / other / "ours.md").write_bytes(b"our own skill\n")
    before = tree(root)
    done = pressed(run_, {"action": manage_web.SKILL_ADD, "root": str(root),
                          "skill": other, "harness": first.name, **NOW})
    assert tree(root) == before
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (manage_web.RESULT_FAILED, f"Couldn't add {other}. Nothing was changed.")
    assert mnt.REASONS[mnt.SKILL_DIR_FOREIGN] in details
    shutil.rmtree(root / first.skills / other)

    # A removal that keeps an edited file: partly, and it says so.
    edited = root / first.skills / SKILL / "SKILL.md"
    edited.write_bytes(edited.read_bytes() + b"\nOur own line.\n")
    done = pressed(run_, {"action": manage_web.SKILL_REMOVE, "root": str(root),
                          "skill": SKILL, "harness": first.name, **NOW})
    assert edited.is_file()
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_PARTLY,
        f"Removed unedited files for {SKILL} in {first.label}. Kept your edited file.")
    assert f"{first.skills}/{SKILL}/SKILL.md {manage_web.MIDDOT} kept" in details

    # Add all over an edited copy: the rest are added, the edited one kept.
    edited2 = root / second.skills / SKILL / "SKILL.md"
    edited2.write_bytes(edited2.read_bytes() + b"\nOur own line.\n")
    done = pressed(run_, {"action": manage_web.SKILL_ADD_ALL, "root": str(root),
                          "harness": second.name, **NOW})
    assert result_of(done)[:2] == (
        manage_web.RESULT_PARTLY,
        f"Added the missing skills for {second.label} and kept your edited {SKILL}. "
        "Start a new agent session to pick them up.")
    assert edited2.read_bytes().endswith(b"Our own line.\n")

    # Nothing to do: the screen was drawn before another press changed it.
    done = pressed(run_, {"action": manage_web.SKILL_ADD, "root": str(root),
                          "skill": other, "harness": second.name, **NOW})
    assert result_of(done)[:2] == (manage_web.RESULT_DONE,
                                   f"{other} is already on for {second.label}.")
    assert run(capsys, "skill", "remove", other, str(root), "--harness", second.name)[0] in (
        report.OK, report.PARTIAL)
    done = pressed(run_, {"action": manage_web.SKILL_REMOVE, "root": str(root),
                          "skill": other, "harness": second.name, **NOW})
    assert result_of(done)[:2] == (manage_web.RESULT_DONE,
                                   f"{other} is already off for {second.label}.")

    # Disconnect, whose record will not delete after the settings changed.
    config_before = client_here.read_bytes()
    record = cn.record_path(root)
    real_unlink = Path.unlink

    def stuck(self, *args, **kwargs):
        if self == record:
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", stuck)
    done = pressed(run_, {"action": manage_web.UNCONNECT, "root": str(root),
                          "client": CLIENT, **NOW})
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert client_here.read_bytes() != config_before, "the settings were not changed"
    assert record.is_file()
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_PARTLY,
        f"Removed this project from {label}'s settings, but couldn't delete its "
        "connection record. The link will show as lost. Check Details for the record.")
    assert str(record) in details
    record.unlink()

    # Connect, whose write fails and is put back: nothing was written. Then
    # whose second copy fails after the first landed: partly.
    mapping = fake_client(tmp_path / "two-copies", store=True)
    for name, value in mapping.items():
        monkeypatch.setenv(name, value)
    visible, hidden = roaming_config(mapping), store_config(mapping)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    hidden.write_bytes(CONFIG_HOSTS["empty-servers"])
    host = _host(tmp_path / "two-copy-host")
    mnt.mount(host, scope="two-copy-host")
    pristine = {visible: visible.read_bytes(), hidden: hidden.read_bytes()}
    real_write = Path.write_bytes
    seen: list[Path] = []
    limit = [0]

    def flaky(self, data):
        if self.name == "claude_desktop_config.json":
            seen.append(self)
            if len(seen) > limit[0]:
                raise PermissionError(13, "Permission denied", str(self))
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    fields = {"action": manage_web.CONNECT, "root": str(host), "client": CLIENT, **NOW}
    done = pressed(run_, fields)
    assert {p for p in pristine if p.read_bytes() != pristine[p]} == set()
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (manage_web.RESULT_FAILED,
                                f"Couldn't connect {label}. Nothing was changed.")
    assert "nothing was written" in details
    seen.clear()
    limit[0] = 1
    done = pressed(run_, fields)
    landed = seen[0]
    assert {p for p in pristine if p.read_bytes() != pristine[p]} == {landed}
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_PARTLY,
        f"Couldn't finish connecting {label}. Some of its settings may be left changed. "
        "Check Details for the list.")
    assert f"put back except: {landed}" in details
    assert "Nothing was changed" not in sentence
    assert run_.running


def test_a_press_while_the_client_is_open_carries_the_warning_before_and_after(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescription 8: while the client is open, the before-press sentence
    stands beside every press on these screens that writes its settings,
    and the result line of each such press carries its after-press
    sentence from `wording.md`, once, in place of the engine's own."""
    client = cn.CLIENTS[CLIENT]
    label = client.label
    root = connected_host(tmp_path, "pit-wall")
    lost = connected_host(tmp_path, "brake-bias")
    drop_entry(client_here, record_of(lost)["key"])
    keys = tuple(record_of(root)["entries"])
    plant_lock(client_here)
    ahead = report.client_running_before(client)
    assert ahead == f"{label} is running. Fully quit it first, or it may undo this change."
    run_ = manage()

    page = project_page(run_, root)
    assert f'<p class="warn">{html.escape(ahead, quote=False)}</p>' in connections_of(page)
    lost_page = project_page(run_, lost)
    band = main_of(lost_page).split('<div class="status-band">', 1)[1].split(
        '<table class="skill-grid">', 1)[0]
    assert f'<p class="warn">{html.escape(ahead, quote=False)}</p>' in band
    row = roster_row(run_.page(), "brake-bias")
    assert f'<p class="warn">{html.escape(ahead, quote=False)}</p>' in row
    # A skill press writes no client settings, so it carries none.
    assert ahead not in grid_of_page(page)

    done = pressed(run_, {"action": manage_web.UNCONNECT, "root": str(root),
                          "client": CLIENT, **NOW})
    tone, sentence, warning, details = result_of(done)
    assert tone == manage_web.RESULT_DONE
    assert warning == (f"{label} is running and may put back what this removed. Fully quit "
                       f"it, then remove {' and '.join(keys)} from its settings file if they're back.")
    assert report.unconnect_running_after(client, keys) not in details
    assert text_of(done).count(f"{label} is running and may") == 1

    done = pressed(run_, {"action": manage_web.CONNECT, "root": str(root),
                          "client": CLIENT, **NOW})
    tone, sentence, warning, details = result_of(done)
    assert cn.is_connected(root, CLIENT), "a warning, not a refusal"
    assert warning == (f"{label} is running and may undo this change. Fully quit it and "
                       "reopen it, then check this project: if its link shows as lost, press Repair.")
    assert report.connect_running_after(client) not in details

    done = pressed(run_, {"action": manage_web.REPAIR, "root": str(lost),
                          "confirm": "yes", "from": manage_web.ROSTER})
    tone, sentence, warning, details = result_of(done)
    assert cn.is_connected(lost, CLIENT)
    assert warning == (f"{label} is running and may undo this change. Fully quit it and "
                       "reopen it, then check brake-bias: if its link shows as lost, press Repair.")
    assert text_of(done).count(f"{label} is running and may") == 1
    assert run_.running


def grid_of_page(page: str) -> str:
    return main_of(page).split('<table class="skill-grid">', 1)[1].split("</table>", 1)[0]


class Gate:
    """The session's one-write lock, standing in for it, which says when a
    request has reached it and is waiting."""

    def __init__(self, lock) -> None:
        self.lock = lock
        self.waiting = threading.Event()

    def __enter__(self):
        self.waiting.set()
        self.lock.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.lock.release()


def test_one_press_changes_wait_their_turn_in_the_one_write_queue(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """The page server's one-write queue, unchanged: every kind of
    one-press change waits at the queue while another write holds it,
    writes nothing while it waits, and runs once the queue is free. The
    test knows the request reached the queue, rather than sleeping."""
    root = connected_host(tmp_path, "pit-wall")
    lost = connected_host(tmp_path, "brake-bias")
    drop_entry(client_here, record_of(lost)["key"])
    other = next(name for name in sk.catalog_names() if name != SKILL)
    first, second = sk.harnesses()[:2]
    run_ = manage()
    real = run_.session.actions
    gate = Gate(real)
    run_.session.actions = gate

    def agent(name):
        return dict(ro.verify(root).agents)[name]

    try:
        for fields, settled in (
            ({"action": manage_web.SKILL_ADD, "root": str(root), "skill": other,
              "harness": first.name}, lambda: other in agent(first.name)),
            ({"action": manage_web.SKILL_REMOVE, "root": str(root), "skill": other,
              "harness": first.name}, lambda: other not in agent(first.name)),
            ({"action": manage_web.SKILL_ADD_ALL, "root": str(root), "harness": second.name},
             lambda: set(agent(second.name)) == set(sk.catalog_names())),
            ({"action": manage_web.UNCONNECT, "root": str(root), "client": CLIENT},
             lambda: not cn.is_connected(root)),
            ({"action": manage_web.CONNECT, "root": str(root), "client": CLIENT},
             lambda: cn.is_connected(root, CLIENT)),
            ({"action": manage_web.REPAIR, "root": str(lost)},
             lambda: ro.verify(lost).connected == CLIENT),
        ):
            answers: list[str] = []
            gate.waiting.clear()
            before = (tree(root), tree(lost), client_here.read_bytes())
            with real:
                worker = threading.Thread(
                    target=lambda fields=fields: answers.append(pressed(run_, {**fields, **NOW})))
                worker.start()
                assert gate.waiting.wait(20), f"{fields['action']} never reached the queue"
                assert worker.is_alive() and answers == []
                assert (tree(root), tree(lost), client_here.read_bytes()) == before, fields
            worker.join(20)
            assert not worker.is_alive()
            assert settled(), fields
            assert result_of(answers[0])[0] == manage_web.RESULT_DONE, fields
    finally:
        run_.session.actions = real
    assert run_.running


def stuck_records(monkeypatch, *roots: Path) -> None:
    """Make each project's connection record refuse to be deleted."""
    records = {cn.record_path(root) for root in roots}
    real_unlink = Path.unlink

    def stuck(self, *args, **kwargs):
        if self in records:
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", stuck)


def test_a_press_that_stops_after_writing_while_the_client_is_open_still_warns(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescription 8: a press that changed Claude Desktop's settings and
    then stopped carries the after-press sentence too. Whether the client
    was open is read off the settings files the press changed, not off
    the engine's report, which a stop never reaches (phase 1 review A1)."""
    label = cn.CLIENTS[CLIENT].label
    root = connected_host(tmp_path, "pit-wall")
    keys = tuple(record_of(root)["entries"])
    lost = connected_host(tmp_path, "brake-bias")
    drop_entry(client_here, record_of(lost)["key"])
    plant_lock(client_here)
    run_ = manage()
    stuck_records(monkeypatch, root, lost)

    before = client_here.read_bytes()
    done = pressed(run_, {"action": manage_web.UNCONNECT, "root": str(root),
                          "client": CLIENT, **NOW})
    assert client_here.read_bytes() != before, "the settings were not changed"
    tone, sentence, warning, _ = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_PARTLY,
        f"Removed this project from {label}'s settings, but couldn't delete its "
        "connection record. The link will show as lost. Check Details for the record.")
    assert warning == (f"{label} is running and may put back what this removed. Fully quit "
                       f"it, then remove {' and '.join(keys)} from its settings file if they're back.")

    # Repair's removal rewrites the file for the entry still there, then
    # stops at the record.
    before = client_here.read_bytes()
    done = pressed(run_, {"action": manage_web.REPAIR, "root": str(lost), **NOW})
    assert client_here.read_bytes() != before, "the removal wrote nothing"
    tone, sentence, warning, _ = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_PARTLY,
        f"Removed the old entry from {label}'s settings, but couldn't delete this "
        "project's connection record. The repair stopped. Check Details for the record.")
    assert warning == (f"{label} is running and may undo this change. Fully quit it and "
                       "reopen it, then check this project: if its link shows as lost, press Repair.")

    # A press that changed nothing warns of nothing, open or not.
    done = pressed(run_, {"action": manage_web.SKILL_ADD, "root": str(root),
                          "skill": SKILL, "harness": sk.harnesses()[0].name, **NOW})
    assert result_of(done)[2] == ""
    assert run_.running


def test_a_write_put_back_after_a_later_one_failed_says_nothing_was_changed(
    tmp_path, monkeypatch, env, command, capsys, manage, fixed_day,
):
    """A connect writes the first settings copy, fails on the second, and
    puts the first back: the engine says ERROR, and a write did happen, so
    the line says nothing was changed rather than nothing was written
    (phase 1 review A4)."""
    mapping = fake_client(tmp_path, store=True)
    for name, value in mapping.items():
        monkeypatch.setenv(name, value)
    visible, hidden = roaming_config(mapping), store_config(mapping)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    hidden.write_bytes(CONFIG_HOSTS["empty-servers"])
    host = _host(tmp_path / "host")
    mnt.mount(host, scope="host")
    pristine = {visible: visible.read_bytes(), hidden: hidden.read_bytes()}
    real_write = Path.write_bytes
    written: list[Path] = []

    def second_fails(self, data):
        if self.name == "claude_desktop_config.json":
            written.append(self)
            if len(written) == 2:
                raise PermissionError(13, "Permission denied", str(self))
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", second_fails)
    run_ = manage()
    done = pressed(run_, {"action": manage_web.CONNECT, "root": str(host),
                          "client": CLIENT, **NOW})
    assert set(written[:2]) == {visible, hidden}, written
    assert written[2:3] == written[:1], "the first copy was not put back"
    assert {p for p in pristine if p.read_bytes() != pristine[p]} == set()
    assert not cn.is_connected(host)
    label = cn.CLIENTS[CLIENT].label
    assert result_of(done)[:2] == (manage_web.RESULT_FAILED,
                                   f"Couldn't connect {label}. Nothing was changed.")
    assert run_.running


def test_switching_off_a_skill_whose_every_file_was_edited_says_what_stayed(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """The removal keeps an edited file and drops the skill from the
    record; where every file of the skill was edited, nothing of it was
    removed, and the line says so rather than "Removed unedited files"
    (phase 1 review A5)."""
    first = sk.harnesses()[0]
    root = connected_host(tmp_path, "pit-wall")
    sk.add_all(root, "plan-review", targets=(first.name,))
    assert sk.catalog_entry("plan-review").files == ("SKILL.md",)
    edited = root / first.skills / "plan-review" / "SKILL.md"
    edited.write_bytes(edited.read_bytes() + b"\nOur own line.\n")
    run_ = manage()

    done = pressed(run_, {"action": manage_web.SKILL_REMOVE, "root": str(root),
                          "skill": "plan-review", "harness": first.name, **NOW})
    assert edited.read_bytes().endswith(b"Our own line.\n")
    assert "plan-review" not in dict(ro.verify(root).agents)[first.name]
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_PARTLY,
        f"Kept your edited file for plan-review in {first.label}. RadialRig no longer "
        "tracks the skill there. Details names the file.")
    assert f"{first.skills}/plan-review/SKILL.md {manage_web.MIDDOT} kept" in details
    assert run_.running


def test_a_repair_whose_fresh_connect_fails_says_the_project_is_disconnected(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescription 7: the removal runs, then the fresh connect's write
    fails. The project is disconnected and its record is gone, and the
    line says so: naming the old entry where the removal took one out of
    Claude Desktop's settings, and naming the record where the entries
    were gone already (phase 1 review B4)."""
    label = cn.CLIENTS[CLIENT].label
    # Pit-wall's settings still hold one entry the removal takes out;
    # brake-bias's hold neither.
    taken = connected_host(tmp_path, "pit-wall")
    bare = connected_host(tmp_path, "brake-bias")
    drop_entry(client_here, record_of(taken)["key"])
    for key in record_of(bare)["entries"]:
        drop_entry(client_here, key)
    run_ = manage()
    for root in (taken, bare):
        assert status_band(project_page(run_, root))[0][2] == "Repair"

    real_write, real_unlink = Path.write_bytes, Path.unlink
    repairing: list[Path] = []
    removed: list[Path] = []

    def noted(self, *args, **kwargs):
        if repairing and self == cn.record_path(repairing[0]):
            removed.append(self)
        return real_unlink(self, *args, **kwargs)

    def fails_once_the_record_is_gone(self, data):
        # The removal's own writes land; every settings write after the
        # removal deleted the record, which is the fresh connect's, fails.
        if self.name == "claude_desktop_config.json" and removed:
            raise PermissionError(13, "Permission denied", str(self))
        return real_write(self, data)

    monkeypatch.setattr(Path, "unlink", noted)
    monkeypatch.setattr(Path, "write_bytes", fails_once_the_record_is_gone)

    repairing[:] = [taken]
    before = client_here.read_bytes()
    done = pressed(run_, {"action": manage_web.REPAIR, "root": str(taken), **NOW})
    assert client_here.read_bytes() != before, "the removal took nothing out"
    assert removed, "the removal never deleted the record"
    assert not cn.record_path(taken).is_file()
    assert not cn.is_connected(taken)
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_PARTLY,
        f"Removed the old {label} entry, but couldn't connect again. This project "
        "isn't connected now. Press Connect to try again.")
    assert "Permission denied" in details
    assert f'aria-label="Connect {label}"' in connections_of(done)

    repairing[:] = [bare]
    removed.clear()
    before = client_here.read_bytes()
    done = pressed(run_, {"action": manage_web.REPAIR, "root": str(bare),
                          "confirm": "yes", "from": manage_web.ROSTER})
    assert client_here.read_bytes() == before, "the removal wrote to the settings"
    assert not cn.record_path(bare).is_file()
    assert result_of(done)[:2] == (
        manage_web.RESULT_PARTLY,
        "Removed the old connection record for brake-bias, but couldn't connect "
        "again. Press Connect on its screen to try again.")
    assert run_.running
