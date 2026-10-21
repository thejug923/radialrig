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
import urllib.parse
from pathlib import Path

import pytest

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
    PREFIX,
    FACT_BLOCK,
    SKILL,
    client_here,
    facts_of,
    command,
    env,
    expected_listing,
    listing_rows,
    manage,
    navigations,
    roster_file,
    rows,
    run,
    text_of,
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


def main_of(page: str) -> str:
    return page.split("<main>", 1)[-1].split("</main>", 1)[0]


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

    # Nothing done yet: no reversal is offered, and the one registration
    # this machine could make is.
    page = opened()
    label = cn.CLIENTS[CLIENT].label
    assert manage_web.UNMOUNT_LABEL not in page
    assert manage_web.SKILL_REMOVE_LABEL.format(name=SKILL) not in page
    assert manage_web.SKILL_ADD_LABEL.format(name=SKILL) in page
    assert label in page
    assert manage_web.CONNECT_LABEL.format(label=label) in page
    assert f'value="{manage_web.CONNECT}"' in page

    assert run(capsys, "mount", str(root))[0] == report.OK
    assert run(capsys, "skill", "add", SKILL, str(root))[0] == report.OK
    assert run(capsys, "connect", CLIENT, str(root))[0] == report.OK

    page = opened()
    said = text_of(page)
    facts = ro.verify(root)
    # The state is the three facts under Status (PLAN-0014 phase 2),
    # said through constraint 3's mapping, where a one-row table used to
    # print the command line's row; a press's rows are the only table.
    assert rows(page) == []
    assert "<table" not in main_of(page)
    assert [(which, said_) for which, said_ in FACT_BLOCK.findall(page)] == facts_of(facts)
    assert manage_web.UNMOUNT_LABEL in page
    assert SKILL in said
    assert manage_web.SKILL_REMOVE_LABEL.format(name=SKILL) in page
    assert manage_web.SKILL_ADD_LABEL.format(name=SKILL) not in page
    assert manage_web.UNCONNECT_LABEL.format(label=label) in page
    assert f'value="{manage_web.UNCONNECT}"' in page
    # Registered already, so the registration is not offered twice.
    assert f'value="{manage_web.CONNECT}"' not in page
    assert manage_web.CONNECT_LABEL.format(label=label) not in page
    assert str(root) in said


def test_the_project_view_names_no_client_where_none_is_detected(
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
    for client in cn.CLIENTS.values():
        assert client.label not in page
    assert f'value="{manage_web.CONNECT}"' not in page
    assert manage_web.UNMOUNT_LABEL in page


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
        status, done, _ = run_.post(press(run_, fields, confirm=True))
        assert status == 200, fields
        assert f'data-copy="{action_of(fields).command}"' in done, fields
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


# --- the embedded walkthrough -----------------------------------------
# The oracle is the three verbs one press runs: mount, then skill add per
# checked box, then connect. The first two compare across twins; the
# registration compares one root twice, the idiom above, because a connect
# record names this project's own absolute paths and no twin can hold them.


def test_the_manage_walkthrough_matches_the_cli_three_verb_sequence(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    page_root = _host(tmp_path / "page-host")
    twin = _host(tmp_path / "twin-host")
    assert tree(page_root) == tree(twin)
    pristine = client_here.read_bytes()

    run_ = manage()
    review = opened(run_, page_root)
    assert setup_web.REVIEW_HEAD in review
    assert tree(page_root) == tree(twin), "the review state wrote something"

    # A refresh carrying the fields the press will carry. The verbs named
    # beside the button follow those fields, because the press does.
    status, again, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "survey",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, "skill": boxes(SKILL), **CHOSEN})
    assert status == 200
    assert commands_on(again) == [
        f"radialrig mount {page_root} --scope {SCOPE}",
        *(f"radialrig skill add {SKILL} {page_root} --harness {harness}"
          for harness in sk.HARNESSES),
    ]
    assert tree(page_root) == tree(twin), "the refresh wrote something"

    status, mounted, _ = walk_press(run_, again, {
        "token": run_.session.token, "action": "mount",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, "skill": boxes(SKILL), **CHOSEN})
    assert status == 200
    assert f"Mounted onto {SCOPE}." in mounted
    # The finished page prints the skill lines it ran, one per pair.
    assert [line for line in commands_on(mounted) if "skill add" in line] == [
        f"radialrig skill add {SKILL} {page_root} --harness {harness}"
        for harness in sk.HARNESSES]

    # The same two verbs, from the command line, on the twin.
    code, out, err = run(capsys, "mount", str(twin), "--scope", SCOPE)
    assert (code, err) == (report.OK, "")
    printed = out
    code, out, err = run(capsys, "skill", "add", SKILL, str(twin))
    assert (code, err) == (report.OK, "")
    printed += out

    assert tree(page_root) == tree(twin)
    assert snapshot(page_root) == snapshot(twin)
    assert client_here.read_bytes() == pristine, "an unticked box registered"

    # Every row the page drew is a row those two verbs printed, in their
    # own words. Both kits reported, each under its own caption.
    assert setup_web.WROTE_CAPTION in mounted
    assert setup_web.SKILLS_WROTE_CAPTION in mounted
    drawn = rows(mounted)
    assert drawn
    for verb, path, reason in drawn:
        assert verb in printed, verb
        assert path in printed, path
        # The page's own fills for the engine's empty wordings are the
        # one thing a table says that the command line does not.
        if reason not in setup_web.WROTE_REASONS.values():
            assert reason in printed, reason

    # The registration, on the one root that can hold it. A refresh
    # first, so the boxes the press carries are the boxes the page names:
    # the skill went in with the mount, and this press is the one offer
    # left standing.
    standing = opened(run_, page_root)
    assert setup_web.connect_head(cn.client_for(CLIENT)) in standing
    status, offered, _ = walk_press(run_, standing, {
        "token": run_.session.token, "action": "survey",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, "connect": CLIENT, **CHOSEN})
    assert status == 200
    named = commands_on(offered)
    assert named[-1] == f"radialrig connect {CLIENT} {page_root}"
    assert f"radialrig skill add {SKILL} {page_root}" not in named
    assert tree(page_root) == tree(twin), "the refresh wrote something"

    status, done, _ = walk_press(run_, offered, {
        "token": run_.session.token, "action": "skills",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, "connect": CLIENT, **CHOSEN})
    assert status == 200
    assert setup_web.CONNECT_WROTE_CAPTION in done
    # The client's own restart line is the walkthrough's last word, as it
    # is on the terminal; only the way back to the listing follows it.
    said = text_of(done)
    restart = cn.CLIENTS[CLIENT].restart
    assert said.index(manage_web.WALK_STANDS) < said.index(restart)
    assert said.endswith(f"{restart} {manage_web.ROSTER_BACK_LABEL}")
    page_tree = tree(page_root)
    page_config = client_here.read_bytes()
    assert page_config != pristine

    assert run(capsys, "unconnect", CLIENT, str(page_root))[0] == report.OK
    assert client_here.read_bytes() == pristine
    code, out, err = run(capsys, "connect", CLIENT, str(page_root))
    assert (code, err) == (report.OK, "")
    assert tree(page_root) == page_tree
    assert client_here.read_bytes() == page_config


def test_the_embedded_walkthrough_ticks_one_agents_group_and_installs_for_it_alone(
    tmp_path, env, manage, fixed_day, capsys,
):
    """PLAN-0015 phase 3's done-condition, on the manage page's embedded
    walkthrough: the same two groups the setup page draws, with a Tick
    all beside each heading; every box in the second agent's group
    ticked and the press pressed installs every skill for that agent and
    none for the first, the finished page prints one `--harness` line
    per pair, and `skill list` says so.
    """
    first, second = list(sk.HARNESSES.values())[:2]
    names = sk.catalog_names()
    root = _make_project(tmp_path / "host").resolve()
    twin = _make_project(tmp_path / "twin").resolve()
    run_ = manage()
    review = opened(run_, root)
    found = re.findall(
        r'<fieldset class="skills agent"><legend>([^<]*)</legend>'
        r'(<button class="tick" type="button" aria-label="([^"]*)">Tick all</button>)?'
        r'(.*?)</fieldset>',
        review, re.S)
    assert [(html.unescape(label), bool(tick), html.unescape(named),
             inner.count('type="checkbox"'))
            for label, tick, named, inner in found] == [
        (first.label, True, f"Tick all for {first.label}", len(names)),
        (second.label, True, f"Tick all for {second.label}", len(names))]
    assert "harness" not in text_of(
        re.sub(r'<div class="cmd">.*?</div>', "", review, flags=re.S)).lower()

    # A refresh with the second agent's boxes ticked names one narrowed
    # line per box beside the press, and none for the first agent.
    ticked = boxes(*names, agents=(second,))
    status, again, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "survey",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, "skill": ticked, **CHOSEN})
    assert status == 200
    assert again.count(" checked>") == len(names)
    assert commands_on(again) == [
        f"radialrig mount {root} --scope {SCOPE}",
        *(f"radialrig skill add {name} {root} --harness {second.name}" for name in names),
    ]
    assert not mnt.is_mounted(root), "the refresh wrote something"

    status, mounted, _ = walk_press(run_, again, {
        "token": run_.session.token, "action": "mount",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, "skill": ticked, **CHOSEN})
    assert status == 200
    assert f"Mounted onto {SCOPE}." in mounted
    assert setup_web.SKILLS_RAN_HEAD in text_of(mounted)
    assert [line for line in commands_on(mounted) if "skill add" in line] == [
        f"radialrig skill add {name} {root} --harness {second.name}" for name in names]
    assert f"--harness {first.name}" not in mounted
    assert manage_web.WALK_STANDS in text_of(mounted)
    assert run_.running

    # The tree is what the command line's whole-catalog add leaves, and
    # `skill list` reads one installed row per skill under the second
    # agent's directory and one absent row under the first's.
    assert run(capsys, "mount", str(twin), "--scope", SCOPE)[0] == report.OK
    assert run(capsys, "skill", "add", "--all", str(twin), "--harness", second.name)[0] \
        == report.OK
    assert snapshot(root) == snapshot(twin)
    assert not (root / first.skills).exists()
    code, listed, _ = run(capsys, "skill", "list", str(root))
    assert code == report.OK
    absent = [line for line in listed.splitlines() if mnt.ABSENT in line]
    current = [line for line in listed.splitlines() if mnt.CURRENT in line]
    assert len(absent) == len(names) and all(first.skills in line for line in absent)
    assert len(current) == len(names) and all(second.skills in line for line in current)


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


def test_the_walkthrough_refusal_reads_as_the_clis(
    tmp_path, env, capsys, manage,
):
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
    assert report.refusal_head(1) in said
    assert report.REFUSAL_REMEDY in said
    for verb, path, reason in rows(refused):
        assert verb in out
        assert path in out
        assert reason in out

    # Nothing here presses again, and nothing here is named as a command
    # to run instead: the command line refuses this too.
    body = main_of(refused)
    assert 'value="mount"' not in body
    assert 'value="survey"' in body
    assert manage_web.WALK_HEAD not in refused
    assert tree(page_root) == before

    # A client that posts the mount anyway is refused the same way.
    status, once_more, _ = walk_press(run_, refused, {
        "token": run_.session.token, "action": "mount",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, **CHOSEN})
    assert status == 200
    assert report.refusal_head(1) in text_of(once_more)
    assert tree(page_root) == before, "the refusal wrote something"
    assert run_.running


def test_no_client_detected_means_no_offer_and_no_client_name(
    tmp_path, monkeypatch, manage, fixed_day,
):
    """A profile with no client directory in it, still inside tmp_path.

    `APPDATA` is narrowed rather than removed: the roster path is built
    from it too, and a run without it would assert against a walkthrough
    whose registration could never have been offered anyway.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "no-client"))
    root = _make_project(tmp_path / "bare").resolve()
    run_ = manage()
    review = opened(run_, root)

    assert cn.detect(cn.CLIENTS[CLIENT]) == ()
    assert setup_web.connect_head(cn.client_for(CLIENT)) not in review
    assert 'name="connect"' not in review
    for client in cn.CLIENTS.values():
        assert client.label not in review
        assert client.restart not in review
    # And the named sequence is the one verb the press actually runs.
    assert commands_on(review) == [f"radialrig mount {root}"]

    # A press that posts the box anyway registers nothing: the offer was
    # never made, so there is no client for the press to write to.
    status, mounted, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "mount", "scope": SCOPE,
        "owner": mnt.DEFAULT_OWNER, "connect": CLIENT, **CHOSEN})
    assert status == 200
    assert f"Mounted onto {SCOPE}." in mounted
    assert not cn.is_connected(root)
    for client in cn.CLIENTS.values():
        assert client.label not in mounted
        assert client.restart not in mounted


def test_a_finished_walkthrough_returns_to_the_roster_and_the_server_stands(
    tmp_path, env, manage, fixed_day,
):
    root = _make_project(tmp_path / "host").resolve()
    run_ = manage()
    review = opened(run_, root)
    status, mounted, _ = walk_press(run_, review, {
        "token": run_.session.token, "action": "mount",
        "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, **CHOSEN})
    assert status == 200
    assert f"Mounted onto {SCOPE}." in mounted

    # The walkthrough ended; the page did not, and it says so rather than
    # repeating the setup command's own closing sentence.
    assert 'data-terminal="0"' in mounted
    assert setup_web.STOPPED not in mounted
    assert manage_web.WALK_STANDS in text_of(mounted)
    assert run_.running

    back = navigations(mounted, run_.session.token)
    assert len(back) == 1, back
    status, listing, _ = run_.get(back[0])
    assert status == 200

    states, found = ro.listing(), ro.orphans()
    assert [state.root for state in states] == [str(root)]
    # The listing says the state through constraint 3's mapping
    # (PLAN-0014 phase 1); the command line's rows are the roster's.
    assert listing_rows(listing) == expected_listing(states)
    assert ro.report_rows(states, found)
    # The listing names the machine; the count line stays on the command
    # line, where it tells a reader how many rows are about to print.
    assert manage_web.ROSTER_HEAD in text_of(listing)
    assert report.projects_head(states, found) not in text_of(listing)

    status, _, _ = run_.post({"token": run_.session.token, "action": "close"})
    assert status == 200
    assert run_.wait() == report.OK


# --- PLAN-0017 phase 4: only safe lines ------------------------------------

def test_a_slot_no_quoting_rule_makes_safe_offers_no_copy_line_on_this_page(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """setup-web-04, on the manage page's builders.

    Every command block here is drawn through one of three builders: the
    action's own `command`, the frame `_command_of` draws beside a press,
    and the walkthrough's mount line. A path or a client key carrying a
    character no quoting rule makes safe in both shells reaches all
    three, and each offers no line rather than one that runs something
    else. The page still says what it did.
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
        review = setup_web.PageState(
            kind=setup_web.REVIEW, outcome=report.NOOP, terminal=False)
        assert manage_web._walk_commands(sub, review) == "", repr(path)

    # Over HTTP, on folders and a key a reader can have: `&` and `$` are
    # legal in a folder name on both systems, `;` in a client's key.
    mounted = _host(tmp_path / "proj$x")
    assert run(capsys, "mount", str(mounted))[0] == report.OK
    fresh = _host(tmp_path / "proj&calc")
    key = f"{PREFIX}foo;calc"
    loose_entry(client_here, key, tmp_path / "vanished")

    run_ = manage()
    roster = run_.page()
    assert key in text_of(roster), "the loose entry is not listed"
    status, project, _ = run_.get(
        f"{manage_web.MANAGE_PATH}?s={run_.session.token}"
        f"&view={manage_web.PROJECT}&root={urllib.parse.quote(str(mounted))}")
    assert status == 200, project
    walk = opened(run_, fresh)
    assert setup_web.REVIEW_HEAD in walk
    status, planned, _ = run_.post(press(
        run_, {"action": manage_web.UNMOUNT, "root": str(mounted)}))
    assert status == 200
    assert mnt.is_mounted(mounted), "the plan press wrote"
    for name, page in (("roster", roster), ("project", project),
                       ("walkthrough", walk), ("plan", planned)):
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
from radialrig.tests.test_manage_web import FACT_BLOCK as _FACT_BLOCK  # noqa: E402,F401
from radialrig.tests.test_manage_web import (  # noqa: E402,F401 - helpers, by name
    AGENT_LIST,
    PRIMARY,
    agent_lists,
    main_of,
    pressed,
    project_page,
)
from radialrig.tests.test_skills import INSTALL_DAY, edit_skill  # noqa: E402
from radialrig.tests.test_update import newer_release  # noqa: E402,F401 - a fixture
from radialrig.tests.test_upgrade import edit_spec  # noqa: E402

UPDATE_FACT = ("update", "Update available")
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


def test_the_listing_marks_behind_projects_and_update_all_updates_them(
    tmp_path, env, capsys, manage, fixed_day, newer_release,
):
    page_set, twin_set = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    run_ = manage()

    page = run_.page()
    drawn = {name: facts for name, _, _, facts in listing_rows(page)}
    assert set(drawn) == set(NAMES)
    listed = {Path(state.root).name: state for state in ro.listing()}
    for name in NAMES:
        assert (UPDATE_FACT in drawn[name]) == (name in ("alpha", "beta")), name
        verified = dc.replace(listed[name], behind=update.behind(page_set[name]))
        assert drawn[name] == facts_of(verified), name
    assert manage_web.UPDATE_AVAILABLE_FACT == "Update available"
    for name in ("alpha", "beta"):
        assert drawn[name][:2] == [("mounted", "Mounted"), UPDATE_FACT]
    assert text_of(page).count(manage_web.UPDATE_AVAILABLE_FACT) == 2
    # The press, drawn while a project is behind, with its command in full.
    body = main_of(page)
    assert manage_web.UPDATE_ALL_LABEL == "Update all projects"
    assert f'value="{manage_web.UPDATE_ALL}"' in body
    assert "radialrig upgrade --all" in commands_on(page)
    assert body.count(PRIMARY) == 1   # the listing's one amber stays the add press

    # The rehearsal: both projects' rows, the skipped one named, nothing written.
    fields = {"action": manage_web.UPDATE_ALL}
    before = {name: tree(page_set[name]) for name in NAMES}
    plan = pressed(run_, fields)
    assert {name: tree(page_set[name]) for name in NAMES} == before
    said = text_of(plan)
    assert "Would check 4 listed project(s); 1 skipped." in said
    assert f"Skipped {page_set['epsilon']}: not mounted." in said
    assert main_of(plan).count(PRIMARY) == 1
    assert "radialrig upgrade --all" in commands_on(plan)
    assert "# ours now" in said, "the edited skill's diff is missing"

    # The run, against the command it discloses on the twin set.
    done = pressed(run_, fields, confirm=True)
    assert "Checked 4 listed project(s); 1 skipped." in text_of(done)
    assert main_of(done).count(PRIMARY) == 0
    clear(env)
    list_projects(twin_set)
    code, out, err = run(capsys, "upgrade", "--all")
    assert (code, err) == (report.PARTIAL, ""), "gamma's edited skill is left"
    for name in NAMES:
        assert tree(page_set[name]) == tree(twin_set[name]), name
        assert snapshot(page_set[name]) == snapshot(twin_set[name]), name
    says(done, twinned(out, twin_set, page_set).splitlines())
    assert b"A sentence the newer release added." in (
        page_set["alpha"] / "docs" / "spec.md").read_bytes()
    assert (page_set["gamma"] / sk.harnesses()[0].skills / SKILL / "SKILL.md"
            ).read_bytes() == b"# ours now\n"

    # Afterwards the listing marks none, and the press is gone.
    clear(env)
    list_projects(page_set)
    page = run_.page()
    assert manage_web.UPDATE_AVAILABLE_FACT not in text_of(page)
    assert f'value="{manage_web.UPDATE_ALL}"' not in main_of(page)
    assert run_.running


def test_the_project_page_marks_a_behind_project_and_one_press_updates_it(
    tmp_path, env, capsys, manage, fixed_day, newer_release,
):
    page_set, twin_set = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    run_ = manage()
    assert manage_web.UPDATE_LABEL == "Update this project"

    for name in NAMES:
        body = main_of(project_page(run_, page_set[name]))
        status = body.split(f"<h2>{manage_web.STATUS_HEAD}</h2>", 1)[1].split("</section>", 1)[0]
        behind = name in ("alpha", "beta")
        assert (f'<p class="fact update">{manage_web.UPDATE_AVAILABLE_FACT}</p>' in status) == behind, name
        assert (manage_web.UPDATE_LABEL in status) == behind, name
        command = f"radialrig upgrade {page_set[name]}"
        assert (command in commands_on(status)) == behind, name
        assert status.count(PRIMARY) == (1 if name == "epsilon" else 0), name

    for name in ("alpha", "beta"):
        root, twin = page_set[name], twin_set[name]
        fields = {"action": manage_web.UPDATE, "root": str(root)}
        before = tree(root)
        plan = pressed(run_, fields)
        assert tree(root) == before, "the plan press wrote something"
        assert main_of(plan).count(PRIMARY) == 1
        assert f"radialrig upgrade {root}" in commands_on(plan)
        rehearsed = update.plan(twin)
        assert rows(plan) == expected_rows(update_rows(rehearsed))

        done = pressed(run_, fields, confirm=True)
        code, out, err = run(capsys, "upgrade", str(twin))
        assert (code, err) == (report.OK, ""), name
        assert tree(root) == tree(twin), name
        assert snapshot(root) == snapshot(twin), name
        assert rows(done) == expected_rows(update_rows(rehearsed))
        says(done, out.replace(str(twin), str(root)).splitlines())
        assert main_of(done).count(PRIMARY) == 0

        body = main_of(project_page(run_, root))
        assert manage_web.UPDATE_AVAILABLE_FACT not in body
        assert manage_web.UPDATE_LABEL not in body
    assert run_.running


def test_a_skill_row_reads_update_available_exactly_for_a_clean_older_copy(
    tmp_path, env, capsys, manage, fixed_day, newer_release,
):
    page_set, twin_set = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    run_ = manage()
    first, second = sk.harnesses()[:2]
    assert manage_web.SKILL_UPDATE_AVAILABLE == "Update available"
    assert manage_web.SKILL_UPDATE_LABEL == "Update {name}"
    row = (f'<h3 class="name">{SKILL}</h3>'
           f'<p class="what">{html.escape(sk.catalog_entry(SKILL).summary, quote=False)}</p>')

    def states_of(body: str) -> dict[str, dict[str, str]]:
        """Each agent's list: the skill's state and the press beside it."""
        out = {}
        for label, inner in agent_lists(body):
            state = inner.split(row, 1)[1].split("</p>", 1)[0].split(">", 1)[1]
            out[label] = {"state": state, "inner": inner}
        return out

    beta = states_of(main_of(project_page(run_, page_set["beta"])))
    for harness in (first, second):
        found = beta[harness.label]
        assert found["state"] == manage_web.SKILL_UPDATE_AVAILABLE
        assert f"Update {SKILL}" in found["inner"]
        assert f"Remove {SKILL}" not in found["inner"] and f"Add {SKILL}" not in found["inner"]
        assert (f"radialrig skill add {SKILL} {page_set['beta']} --harness {harness.name}"
                in commands_on(found["inner"]))
        # The other rows read Not installed with Add, and the whole-catalog
        # press still stands above them: it refreshes this one too.
        assert found["inner"].count(f'<p class="state">{manage_web.SKILL_NOT_INSTALLED}</p>') \
            == len(sk.catalog_names()) - 1
        assert f"Add all for {harness.label}" in found["inner"]
    gamma = states_of(main_of(project_page(run_, page_set["gamma"])))
    delta = states_of(main_of(project_page(run_, page_set["delta"])))
    for harness in (first, second):
        assert gamma[harness.label]["state"] == manage_web.SKILL_INSTALLED
        assert f"Remove {SKILL}" in gamma[harness.label]["inner"]
        assert delta[harness.label]["state"] == manage_web.SKILL_INSTALLED
    assert manage_web.SKILL_UPDATE_AVAILABLE not in main_of(project_page(run_, page_set["delta"]))

    # The press on the first agent's row: the existing skill-add action,
    # narrowed to that agent, byte for byte.
    root, twin = page_set["beta"], twin_set["beta"]
    fields = {"action": manage_web.SKILL_ADD, "root": str(root), "skill": SKILL,
              "harness": first.name}
    before = tree(root)
    plan = pressed(run_, fields)
    assert tree(root) == before, "the plan press wrote something"
    assert main_of(plan).count(PRIMARY) == 1
    done = pressed(run_, fields, confirm=True)
    results = sk.add_all(twin, SKILL, targets=(first.name,))
    assert [r.wrote for r in results] == [True]
    assert tree(root) == tree(twin)
    assert snapshot(root) == snapshot(twin)
    assert rows(done) == expected_rows([d for r in results for d in r.dispositions])
    says(done, [line.replace(str(twin), str(root))
                for line in report.skill_add_reports(results)])

    after = states_of(main_of(project_page(run_, root)))
    assert after[first.label]["state"] == manage_web.SKILL_INSTALLED
    assert after[second.label]["state"] == manage_web.SKILL_UPDATE_AVAILABLE
    assert run_.running


# The report heads each agent's skills with "Skills for <label>". The page
# draws that head as a heading with that agent's rows as a table under it,
# never as a sentence with nothing under it (PLAN-0021 phase 1 review,
# item 5). The parity rule holds unchanged: every line the command line
# prints reads back off the page, and the rows read top to bottom are the
# report's.


def under_heading(body: str, heading: str, stop: str = r"<h[1-3][ >]") -> str:
    """What stands under one heading: up to the next heading, of any
    level unless `stop` names the levels that end it."""
    assert body.count(heading) == 1, heading
    after = body.split(heading, 1)[1]
    return re.split(stop, after, maxsplit=1)[0]


def project_block(body: str, root: Path) -> str:
    """One project's block on the update-all page, under its folder and
    up to the next folder's: the agent headings inside it are its own."""
    return under_heading(body, f"<h2>{manage_web._esc(str(root))}</h2>", stop=r"<h2[ >]")


def test_the_update_page_draws_each_agents_rows_under_its_own_heading(
    tmp_path, env, capsys, manage, fixed_day, newer_release,
):
    page_set, twin_set = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    run_ = manage()
    root, twin = page_set["beta"], twin_set["beta"]
    fields = {"action": manage_web.UPDATE, "root": str(root)}
    rehearsed = update.plan(twin)
    for confirm in (False, True):
        page = pressed(run_, fields, confirm=confirm)
        body = main_of(page)
        assert '<p class="said">Skills for' not in body, "an agent head printed as a sentence"
        for harness in sk.harnesses():
            under = under_heading(body, f"<h2>Skills for {harness.label}</h2>")
            assert "<table" in under, harness.label
            assert f"{harness.skills}/{SKILL}/SKILL.md" in under, harness.label
            for other in sk.harnesses():
                if other is not harness:
                    assert other.skills not in under, (harness.label, other.label)
        # The seed's rows stand above the first agent, under the page's
        # own head, and the rows read top to bottom are still the report's.
        seed = body.split("<h2>Skills for", 1)[0]
        assert "<table" in seed and "docs/spec.md" in seed
        assert rows(page) == expected_rows(update_rows(rehearsed))
        # The closing line follows the agents, as a sentence.
        assert body.index("skill(s) refreshed") > body.rindex("<h2>Skills for")
        assert main_of(page).count(PRIMARY) == (0 if confirm else 1)
    code, out, err = run(capsys, "upgrade", str(twin))
    assert (code, err) == (report.OK, "")
    says(page, out.replace(str(twin), str(root)).splitlines())
    assert tree(root) == tree(twin)
    assert run_.running


def test_the_update_all_page_draws_each_projects_agents_under_their_own_headings(
    tmp_path, env, capsys, manage, fixed_day, newer_release,
):
    page_set, twin_set = two_sets(tmp_path, newer_release)
    list_projects(page_set)
    run_ = manage()
    fields = {"action": manage_web.UPDATE_ALL}
    with_skills = ("beta", "gamma", "delta")
    for confirm in (False, True):
        page = pressed(run_, fields, confirm=confirm)
        body = main_of(page)
        assert '<p class="said">Skills for' not in body, "an agent head printed as a sentence"
        heads = re.findall(r"<h3>(Skills for [^<]+)</h3>", body)
        assert len(heads) == len(with_skills) * len(sk.harnesses()), heads
        for name in with_skills:
            block = project_block(body, page_set[name])
            for harness in sk.harnesses():
                under = under_heading(block, f"<h3>Skills for {harness.label}</h3>")
                assert "<table" in under, (name, harness.label)
                assert f"{harness.skills}/{SKILL}/" in under, (name, harness.label)
            assert block.index("skill(s) refreshed") > block.rindex("<h3>Skills for"), name
        # A project with no skill carries the kit's sentence and no agent heading.
        alpha = project_block(body, page_set["alpha"])
        assert report.SKILL_NONE_TAIL in alpha and "Skills for" not in alpha
        assert "docs/spec.md" in alpha
        assert text_of(page).count(report.ENGINE_HALF) == 1
    assert run_.running
