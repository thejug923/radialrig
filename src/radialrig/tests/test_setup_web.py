"""Tests for `radialrig setup` — PLAN-0005 Phase 4.

Run:  uv run --with pytest pytest src/radialrig/tests/test_setup_web.py -q

The oracle for the page is the command line. A walkthrough that ends in a
mount must leave a tree byte-identical to the one `mount()` leaves on a
twin fixture, and every refusal must carry the strings the command line
prints. Everything here drives real HTTP against a real server on a real
port; nothing is asserted against a mocked handler.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import http.client
import io
import json
import os
import re
import socket
import subprocess
import threading
import urllib.parse
from pathlib import Path

import pytest

from radialrig import cli
from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import report
from radialrig import setup_web
from radialrig import skills as sk
from radialrig.paths import packaged_web, resolve_root
from radialrig.tests.test_connect import (  # noqa: F401 - helpers, by name
    CONFIG_HOSTS,
    roaming_config,
    store_config,
    unset,
)
from radialrig.tests.test_connect import fake_client as client_env
from radialrig.tests.test_mount import (  # noqa: F401 - helpers, by name
    EXPECTED,
    MOUNT_DAY,
    _make_project,
    snapshot,
    tree,
)

SCOPE = "twin-scope"
TIMEOUT = 20

# The one packaged skill, restated rather than imported from the catalog
# it is used to check.
SKILL = "session-handoff"
SKILL_DIR = ".claude/skills/session-handoff"

# The agents in the harness map, read off it rather than spelled here:
# the first is the map's default, whose tree SKILL_DIR names; the second
# is the one the boxes are ticked for alone (PLAN-0015 phase 3).
AGENTS = tuple(sk.HARNESSES.values())
FIRST, SECOND = AGENTS[0], AGENTS[1]


def boxes(*names: str, agents=None) -> list[str]:
    """What the boxes post for these skills: one value per (agent, skill)
    pair, for every agent in the map or the ones given."""
    return [setup_web.box_value(harness.name, name)
            for harness in (agents or AGENTS) for name in names]


TAGS = re.compile(r"<[^>]+>")
# A folder path drawn in pieces that wrap only after a separator
# (`setup_web.folder_path`, PLAN-0027 phase 5 C3) reads as one path.
PIECES = re.compile(r'<span class="seg">([^<]*)</span>(?:<wbr>)?')
EXTERNAL_REF = re.compile(r'(?:href|src)="(?!data:|/setup)[a-z]+:', re.I)
EXTERNAL_CSS = re.compile(r'url\(\s*["\']?(?!data:)[a-z]+:', re.I)

# D-33: nothing the page says implies who typed a change.
#
# As amended by the owner on 2026-08-18, this output check matches whole
# words. The page prints a skill's name, and the "hand" inside
# session-handoff is part of a name, not a claim about who typed a
# change. The strict substring ban over the source of cli.py and mount.py
# (test_cli.py) is untouched by the amendment.
FORBIDDEN = ("hand", "manual", "human")
FORBIDDEN_WORD = re.compile(r"\b(?:" + "|".join(FORBIDDEN) + r")\b")


# --- fixtures ---------------------------------------------------------

@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    """Belt and braces: no test here may reach this machine's own client.

    The page's connect offer reads these two variables. Without this,
    a developer with the client installed would run a different suite
    from one without it, and the offer would name a real settings file.
    A test that wants the offer injects its own directories.
    """
    unset(monkeypatch, "APPDATA", "LOCALAPPDATA")


def test_no_real_client_leaves_the_profile_variables_absent():
    """Absent, not pointed at an empty folder as conftest.py leaves them."""
    assert "APPDATA" not in os.environ
    assert "LOCALAPPDATA" not in os.environ


def fake_client(tmp_path: Path) -> Path:
    """A client settings directory under tmp_path, and the env to find it."""
    roaming = tmp_path / "AppData" / "Roaming"
    (roaming / "Claude").mkdir(parents=True, exist_ok=True)
    return roaming


@pytest.fixture()
def client_here(tmp_path, monkeypatch):
    """One detected client and one locatable read command, both fake."""
    monkeypatch.setenv("APPDATA", str(fake_client(tmp_path)))
    exe = tmp_path / "bin" / "radialrig-read.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(cn, "_which", lambda name: str(exe))
    return tmp_path / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"


def _host(root: Path) -> Path:
    """A project that already has the two files a mount merges into, so
    the byte oracle covers the merge paths and not only the created ones.
    """
    _make_project(root)
    (root / "AGENTS.md").write_bytes(b"# Sample\n\nHouse rules.\n")
    (root / ".mcp.json").write_bytes(
        b'{\n    "mcpServers": {\n        "other": {"command": "elsewhere"}\n    }\n}\n'
    )
    return root


@pytest.fixture()
def fixed_day(monkeypatch):
    """One mount date for the page and its twin, so a run across midnight
    cannot make the byte comparison fail for the wrong reason."""

    class _Day(dt.date):
        @classmethod
        def today(cls):
            return MOUNT_DAY

    monkeypatch.setattr(mnt.dt, "date", _Day)
    return MOUNT_DAY


class Walkthrough:
    """One `radialrig setup` run, driven over HTTP from this process."""

    def __init__(self, root: Path, **kwargs) -> None:
        self.root = Path(root)
        self.kwargs = kwargs
        self.stream = io.StringIO()
        self.session: setup_web.Session | None = None
        self.code: int | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.code = setup_web.serve(
            self.root, open_browser=False, on_ready=self._on_ready,
            stream=self.stream, **self.kwargs,
        )

    def _on_ready(self, session) -> None:
        self.session = session
        self._ready.set()

    def start(self) -> "Walkthrough":
        self._thread.start()
        assert self._ready.wait(TIMEOUT), "the server never reported ready"
        return self

    # --- lifecycle ---

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def wait(self) -> int:
        """The server is expected to stop itself. Returns the exit code."""
        self._thread.join(TIMEOUT)
        assert not self._thread.is_alive(), "the server is still running"
        return self.code

    def stop(self) -> int:
        if self._thread.is_alive():
            self.session.finish(self.session.outcome)
        self._thread.join(TIMEOUT)
        return self.code

    # --- requests ---

    def _connect(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(
            setup_web.HOST, self.session.port, timeout=TIMEOUT,
        )

    def request(self, method: str, path: str, *, body=None, headers=None):
        conn = self._connect()
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = response.read().decode("utf-8")
            return response.status, payload, dict(response.getheaders())
        finally:
            conn.close()

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def page(self):
        status, body, headers = self.get(f"/setup?s={self.session.token}")
        assert status == 200, body
        return body

    def post(self, fields: dict, *, headers=None, path: str = "/setup"):
        head = {"Content-Type": "application/x-www-form-urlencoded"}
        head.update(headers or {})
        # `doseq`, so a field holding a list posts once per value, the
        # way a form with several boxes ticked does.
        body = urllib.parse.urlencode(fields, doseq=True)
        return self.request("POST", path, body=body, headers=head)

    def announce(self, fields: dict, *, path: str = "/setup") -> int:
        """Post a form's head only, its body declared and never sent.

        The server refuses an oversized body by its declared length,
        before reading a byte of it, and closes the connection behind
        that answer. A client still sending the body at that moment gets
        the close as a reset (WinError 10053 here), and on Windows the
        reset can also discard the answer already sitting in the receive
        buffer, so sending the body is a race the test cannot win every
        time. Declaring the length and stopping is the same request as
        far as the guard can see, with nothing left unread for the close
        to reset. Returns the status.
        """
        body = urllib.parse.urlencode(fields).encode("ascii")
        conn = self._connect()
        try:
            conn.putrequest("POST", path)
            conn.putheader("Content-Type", "application/x-www-form-urlencoded")
            conn.putheader("Content-Length", str(len(body)))
            conn.endheaders()
            response = conn.getresponse()
            response.read()
            return response.status
        finally:
            conn.close()


@pytest.fixture()
def walk():
    made: list[Walkthrough] = []

    def start(root: Path, **kwargs) -> Walkthrough:
        run = Walkthrough(root, **kwargs).start()
        made.append(run)
        return run

    yield start
    for run in made:
        run.stop()


# --- helpers ----------------------------------------------------------

def visible(page: str) -> str:
    body = page.split("<main>", 1)[-1].split("</main>", 1)[0]
    return TAGS.sub(" ", PIECES.sub(r"\1", body))


def text_of(page: str) -> str:
    """What the page reads as: tags gone, runs of whitespace collapsed.

    A line of the command line's own report can be split across elements
    — the already-mounted heading sets the project path in its own
    `<code>` — so parity is checked against the rendered text rather than
    against raw markup.
    """
    import html as _html

    return " ".join(_html.unescape(visible(page)).split())


# --- the screen, read back (PLAN-0027 phase 2) -------------------------
# Add a project is one screen with a card for a setup that can't start
# and one result line for a press. These read each piece back off the
# markup the way a reader meets it.

RESULT = re.compile(
    r'<div class="result ([a-z]+)" role="status" aria-live="polite">(.*?)</div>'
    r"((?:<details>.*?</details>)?)</div>", re.S)
CHANGED = re.compile(
    r"<li><code>([^<]*)</code> " + setup_web.MIDDOT + r" ([^<]*)</li>")
FILE_ITEM = re.compile(
    r'<li(?: data-for="[^"]*")?( hidden)?><code>([^<]*)</code>'
    r"(?: " + setup_web.MIDDOT + r" ([^<]*))?</li>")
BOX = re.compile(
    r'<input type="checkbox" name="skill" value="([^"]*)" data-agent="([^"]*)" '
    r'data-shows="[^"]*" aria-label="([^"]*)"( checked)?>')
COMMANDS_BLOCK = '<div class="commands"><h3>Commands</h3>'
COPY_ITEM = re.compile(
    r'(<div data-for="[^"]*"( hidden)?>)?<div class="copy-line"><code>[^<]*</code>'
    r'<button class="copy" type="button" data-copy="([^"]*)"')


def main_of(page: str) -> str:
    return page.split("<main>", 1)[-1].split("</main>", 1)[0]


def flat(markup: str) -> str:
    return " ".join(html.unescape(TAGS.sub(" ", markup)).split())


def result_of(page: str) -> tuple[str, str, str, str] | None:
    """The press's one result line: its tone, its sentence, the
    after-press sentence or "", and its Details' text or ""."""
    found = RESULT.search(main_of(page))
    if found is None:
        return None
    tone, words, more = found.groups()
    said = re.findall(r"<p>([^<]*)</p>", words)
    warn = re.findall(r'<p class="warn">([^<]*)</p>', words)
    return (tone, html.unescape(said[0]), html.unescape(warn[0]) if warn else "",
            flat(more))


def changed_of(page: str) -> list[tuple[str, str]]:
    """The files a result line's Details lists, each with its tag."""
    found = RESULT.search(main_of(page))
    assert found is not None, "the page carries no result line"
    return [(html.unescape(path), tag) for path, tag in CHANGED.findall(found.group(3))]


def expected_changed(*groups) -> list[tuple[str, str]]:
    """What a result line lists for these rows: each one that changed or
    kept something, tagged as the result line's grammar tags it."""
    return [(mnt.one_line(d.path), setup_web.TAGS[d.verb])
            for rows_ in groups for d in rows_ if d.verb in setup_web.TAGS]


def card_of(page: str) -> tuple[str, str, str] | None:
    """The card a setup that can't start, or stopped, says it on: its
    heading, its one sentence, and its Details' text."""
    body = main_of(page)
    if '<section class="card"' not in body:
        return None
    card = body.split('<section class="card"', 1)[1].split("</section>", 1)[0]
    assert f"<span>{setup_web.STOPPED_STATE}</span>" in card
    head = re.search(r"<h1>([^<]*)</h1>", card).group(1)
    said = TAGS.sub("", re.search(r'<p class="path">.*?</p><p>(.*?)</p>', card).group(1))
    details = re.search(r'<details><summary>Details</summary><div class="drawer">'
                        r"(.*?)</div></details>", card, re.S)
    return html.unescape(head), html.unescape(said), flat(details.group(1)) if details else ""


def closing_of(page: str) -> str:
    """What a finished page says about its server, or ""."""
    found = re.search(r'<p class="closing">([^<]*)</p>', main_of(page))
    return html.unescape(found.group(1)) if found else ""


def files_listed(page: str) -> list[tuple[str, str, bool]]:
    """Details' Project files: each file, its tag, and whether it shows
    as the last post left the boxes."""
    body = main_of(page)
    block = body.split('<ul class="files">', 1)[1].split("</ul>", 1)[0]
    return [(html.unescape(path), tag, not hidden)
            for hidden, path, tag in FILE_ITEM.findall(block)]


def grid_boxes(page: str) -> list[tuple[str, str, bool]]:
    """Every skill box on the screen, in order: what it posts, its name
    for a reader by ear, and whether it is ticked."""
    return [(value, html.unescape(name), bool(checked))
            for value, _, name, checked in BOX.findall(page)]


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
    found = RESULT.search(main_of(page))
    if found is None or COMMANDS_BLOCK not in found.group(3):
        return []
    block = found.group(3).split(COMMANDS_BLOCK, 1)[1]
    return [html.unescape(line) for _, _, line in COPY_ITEM.findall(block)]


def ready(root: Path) -> str:
    """The ready screen's heading for a folder."""
    return f"<h1>Set up RadialRig in {html.escape(root.name, quote=False)}</h1>"


# The marker the review and already-mounted forms carry: a post from a
# form that drew the boxes says what they hold, ticked or not (PLAN-0017
# phase 4). A press posted here without it says nothing about them.
CHOSEN = {setup_web.CHOICES_FIELD: "1"}


def mount_form(run: Walkthrough, **extra) -> dict:
    fields = {"token": run.session.token, "action": "mount",
              "scope": SCOPE, "owner": mnt.DEFAULT_OWNER, **CHOSEN}
    fields.update(extra)
    return fields


# --- A: the byte oracle -----------------------------------------------

def test_a_page_walkthrough_matches_the_command_line_byte_for_byte(
    tmp_path, walk, fixed_day,
):
    page_root = _host(tmp_path / "page-host")
    cli_root = _host(tmp_path / "cli-host")
    assert tree(page_root) == tree(cli_root)

    run = walk(page_root, scope=SCOPE)

    # The opened URL: one launch token, redeemed for the session token.
    status, _, headers = run.get(f"/setup?t={run.session.launch_token}")
    assert status == 303
    assert headers["Location"] == f"/setup?s={run.session.token}"

    review = run.page()
    assert ready(page_root) in review
    assert tree(page_root) == tree(cli_root), "the ready screen wrote something"

    status, mounted, _ = run.post(mount_form(run))
    assert status == 200
    assert result_of(mounted)[:3] == (
        setup_web.RESULT_DONE, f"Set up RadialRig in {page_root.name}.", "")
    assert run.wait() == report.OK

    result = mnt.mount(cli_root, scope=SCOPE, owner=mnt.DEFAULT_OWNER)

    page_files, page_dirs = tree(page_root)
    cli_files, cli_dirs = tree(cli_root)
    assert set(page_files) == set(cli_files) == EXPECTED | {
        "README.md", "src/app.py", ".gitignore",
    }
    assert page_dirs == cli_dirs
    for rel, data in sorted(cli_files.items()):
        assert page_files[rel] == data, f"{rel} differs from the command line's mount"
    assert snapshot(page_root) == snapshot(cli_root)

    # And the line's Details lists exactly what the command line reported
    # it wrote, with the next step and the one command the press ran.
    assert changed_of(mounted) == expected_changed(result.dispositions())
    assert report.next_step(page_root)[0] in result_of(mounted)[3]
    assert ran_commands(mounted) == [f"radialrig mount {page_root} --scope {SCOPE}"]


@pytest.mark.parametrize("tracked", [False, True])
def test_the_mounted_page_names_the_validate_command_this_project_can_run(
    tmp_path, walk, fixed_day, monkeypatch, tracked,
):
    """The closing step's sentence and the command its Copy button
    copies name `--no-baseline` where git has no history for the folder,
    and only there (an outside tester, 2026-10-01). Literal here, so the
    page and not `report.next_step` is pinned; the ceiling keeps git from
    finding a repository above the temporary folder."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    root = _host(tmp_path / "host")
    if tracked:
        subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
    run = walk(root, scope=SCOPE)
    run.get(f"/setup?t={run.session.launch_token}")
    status, mounted, _ = run.post(mount_form(run))
    assert status == 200
    assert run.wait() == report.OK
    plain = ('data-copy="radialrig-validate"', report.NEXT_STEP)
    flagged = ('data-copy="radialrig-validate --no-baseline"',
               report.NEXT_STEP_NO_GIT)
    shown, hidden = (plain, flagged) if tracked else (flagged, plain)
    page = html.unescape(mounted)
    assert shown[0] in mounted and shown[1] in page
    assert hidden[0] not in mounted and hidden[1] not in page


def test_the_review_state_shows_the_whole_plan_and_writes_nothing(
    tmp_path, walk, fixed_day,
):
    """The ready screen: the folder in the heading, the two sentences,
    and Details' Project files naming every path the setup writes, in the
    plan's order, the files the folder already has tagged with what the
    setup does to them. Nothing is written."""
    root = _host(tmp_path / "host")
    before = tree(root)
    run = walk(root, scope=SCOPE)
    review = run.page()

    plan = mnt.mount_plan(root, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    assert ready(root) in review
    assert (f'<span class="path">{html.escape(str(root), quote=False)}</span>') in review
    # The two sentences, spelled out: a sentence asserted against the
    # constant that renders it pins nothing.
    assert (
        "RadialRig adds a docs folder with templates, the instructions your agents "
        "read, and its rule lookup and rule editor for Claude Code and Codex. Your "
        "own files stay in place, and you can remove RadialRig later."
    ) in text_of(review)
    shown = [(path, tag) for path, tag, on in files_listed(review) if on]
    assert shown == [
        (mnt.one_line(d.path), setup_web.FILE_TAGS.get(d.verb, ""))
        for d in plan.dispositions()]
    assert {path for path, _ in shown} == EXPECTED
    assert (mnt.MCP_REL, setup_web.FILE_ADDS_TO) in shown
    assert (mnt.AGENTS_REL, setup_web.FILE_ADDS_TO) in shown
    assert tree(root) == before
    # Details is closed to begin with.
    assert '<details class="settings"><summary>Details</summary>' in review
    assert "<details class=\"settings\" open" not in review


def test_every_path_a_plan_holds_is_listed_once(tmp_path):
    """Details' list is the plan itself, not a picture over it: a path a
    later phase adds takes a line of its own without a word of this
    screen changing."""
    root = _host(tmp_path / "host")
    session = setup_web.Session(root, SCOPE, mnt.DEFAULT_OWNER)
    extra = mnt.Disposition("docs/new-thing.md", mnt.CREATED, mnt.MOUNT_CREATED)
    plan = mnt.mount_plan(root, scope=SCOPE)
    state = setup_web.PageState(
        setup_web.REVIEW, report.NOOP, False,
        dispositions=(*plan.dispositions(), extra))
    drawn = setup_web._file_items(session, state, (), setup_web._Offer())
    paths = re.findall(r"<li><code>([^<]*)</code>", drawn)
    assert paths == [mnt.one_line(d.path) for d in state.dispositions]
    assert paths[-1] == "docs/new-thing.md"
    assert len(set(paths)) == len(paths)


def test_the_setup_page_reads_the_way_the_design_writes_it(tmp_path, walk, fixed_day):
    """The masthead, the heading, the press and Details, in wording.md's
    words: the masthead is the shared chrome's `RadialRig`, the press is
    Set up project, and Details holds Project name, Owner and Refresh.
    No table and no caption, no count, and no word the glossary keeps off
    the screens outside a command."""
    assert setup_web.TITLE == "RadialRig"
    assert setup_web.SETUP_HEAD == "Set up RadialRig in {project}"
    assert setup_web.SETUP_LABEL == "Set up project"
    assert setup_web.REFRESH_LABEL == "Refresh"
    assert setup_web.SCOPE_LABEL == "Project name"
    assert setup_web.OWNER_LABEL == "Owner"
    assert setup_web.FILES_HEAD == "Project files"
    assert setup_web.COMMANDS_HEAD == "Commands"
    assert setup_web.REFRESH_HINT == (
        "After changing a name, press Refresh so the commands and the "
        "connection use it.")

    root = _host(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()
    assert '<span class="name">RadialRig</span>' in review
    assert "<title>RadialRig</title>" in review
    assert "Project Setup" not in review
    assert ready(root) in review
    assert '<button class="primary" type="submit" name="action" value="mount">' \
        "Set up project</button>" in review
    assert ('<button class="refresh" type="submit" name="action" value="survey">'
            "Refresh</button>") in review
    said = text_of(review)
    for words in ("Project name", "Owner", setup_web.REFRESH_HINT, "Project files",
                  "Show commands", "Details"):
        assert words in said, words
    assert "<table" not in main_of(review).replace('<table class="skill-grid">', "")
    assert "<caption>" not in review
    status, checked, _ = run.post(
        {"token": run.session.token, "action": "survey", "skill": boxes(SKILL), **CHOSEN},
    )
    assert status == 200
    for page in (review, checked):
        # Commands and file names keep their own spelling; the words
        # around them are the glossary's.
        words = text_of(re.sub(r'<div class="copy-line">.*?</div>|<code>[^<]*</code>',
                               "", page, flags=re.S))
        assert "path(s)" not in words
        for gone in ("mount", "harness", "register", "seed"):
            assert not re.search(rf"\b{gone}", words, re.I), gone


def test_the_masthead_carries_the_installed_engine(tmp_path, walk):
    """PLAN-0018 phase 1 (BUG-0013): the one stamp every surface shows,
    after the endorsement, in the masthead's own small type."""
    review = walk(_host(tmp_path / "host"), scope=SCOPE).page()
    stamp = mnt.version_stamp()
    assert stamp == f"radialrig {mnt.installed_version()}"
    assert (f'<span class="endorse">{setup_web.ENDORSEMENT}</span>'
            f'<span class="version">{stamp}</span></header>') in review
    assert review.count('<span class="version">') == 1
    assert ".masthead .version {" in review


def test_the_page_defaults_match_the_command_line_flags(tmp_path, walk):
    root = _make_project(tmp_path / "sample-host")
    run = walk(root)
    review = run.page()
    assert f'id="scope" name="scope" value="{root.name}"' in review
    assert f'id="owner" name="owner" value="{mnt.DEFAULT_OWNER}"' in review


def test_refresh_the_plan_re_runs_the_survey(tmp_path, walk):
    root = _make_project(tmp_path / "sample-host")
    run = walk(root)
    run.page()
    (root / "docs").mkdir()
    (root / "docs" / "spec.md").write_bytes(b"ours\n")
    status, page, _ = run.post({"token": run.session.token, "action": "survey"})
    assert status == 200
    assert card_of(page)[1] == setup_web.BLOCKED_ONE
    assert run.running, "a refusal is not the end of the walkthrough"


def test_refresh_redraws_the_commands_and_the_connection_for_a_changed_name(
    tmp_path, walk, client_here,
):
    """Why Refresh stays (PLAN-0027 phase 2 prescription 6): the setup's
    command spells a changed Project name and Owner, the connection's two
    entries are named after the project, and a connection withheld
    because its name is taken comes back only under a new name. All three
    are the server's to draw again; the screen's script only follows the
    boxes."""
    client = cn.client_for(cn.DEFAULT_CLIENT)
    root = _make_project(tmp_path / "pit-wall")
    taken = cn.entry_key(client, root.name)
    client_here.write_bytes(
        (json.dumps({"mcpServers": {taken: {"command": "theirs"}}}, indent=2)
         + "\n").encode("utf-8"))
    before = tree(root)
    run = walk(root)
    review = run.page()
    assert shown_commands(review) == [f"radialrig mount {root}"]
    # The box is offered; ticked, the plan meets the taken name, and one
    # sentence stands in the box's place.
    status, withheld, _ = run.post({
        "token": run.session.token, "action": "survey", "scope": root.name,
        "owner": mnt.DEFAULT_OWNER, "connect": client.name, **CHOSEN})
    assert status == 200
    assert 'name="connect"' not in withheld
    assert setup_web.CONNECT_NAME_TAKEN.format(label=client.label) in text_of(withheld)
    assert 'value="mount"' in withheld, "a withheld connection took the setup with it"

    status, renamed, _ = run.post({
        "token": run.session.token, "action": "survey", "scope": "Pit Wall 2",
        "owner": "Race Ops", "connect": client.name, **CHOSEN})
    assert status == 200
    assert shown_commands(renamed)[0] == (
        f'radialrig mount {root} --scope "Pit Wall 2" --owner "Race Ops"')
    assert f'name="connect" value="{client.name}" data-shows=' in renamed
    key = cn.entry_key(client, "Pit Wall 2")
    write_key = cn.write_entry_key(client, "Pit Wall 2")
    assert setup_web.CLIENT_KEYS.format(key=key, write_key=write_key) in html.unescape(renamed)
    assert setup_web.CONNECT_NAME_TAKEN.format(label=client.label) not in text_of(renamed)
    assert tree(root) == before, "a refresh wrote something"


# --- B: the conflict refusal ------------------------------------------

def test_a_conflict_shows_the_command_lines_refusal_and_writes_nothing(
    tmp_path, walk, capsys,
):
    root = _make_project(tmp_path / "conflict-host")
    (root / "docs").mkdir()
    (root / "docs" / "spec.md").write_bytes(b"this project's own rulebook\n")
    twin = _make_project(tmp_path / "conflict-twin")
    (twin / "docs").mkdir()
    (twin / "docs" / "spec.md").write_bytes(b"this project's own rulebook\n")

    before = tree(root)
    run = walk(root)
    page = run.page()

    assert cli.main(["mount", str(twin)]) == report.REFUSED
    printed = capsys.readouterr().out
    assert tree(twin) == before

    # One sentence of why, and behind Details each file in the way with
    # the reason the command line prints for it.
    head, said, details = card_of(page)
    assert head == f"Set up RadialRig in {root.name}"
    assert said == setup_web.BLOCKED_ONE
    reason = report.conflict_reason("docs/spec.md")
    assert details == f"{setup_web.BLOCKERS_HEAD} docs/spec.md : {reason}"
    assert "docs/spec.md" in printed and reason in printed
    assert tree(root) == before

    # Nothing on the page can force past the refusal: the only control is
    # the survey again.
    assert 'value="mount"' not in page
    assert f'value="survey">{setup_web.CHECK_AGAIN_LABEL}</button>' in page
    assert 'role="status" aria-live="polite"' in page
    # `radialrig setup` has no Projects to go back to, so no Cancel.
    assert setup_web.CANCEL_LABEL not in text_of(page)

    # A client that posts the mount anyway is refused the same way.
    status, again, _ = run.post(mount_form(run))
    assert status == 200
    assert card_of(again)[1] == setup_web.BLOCKED_ONE
    assert tree(root) == before
    assert run.stop() == report.REFUSED


def test_a_folder_inside_a_mounted_project_is_refused_with_no_way_past(
    tmp_path, walk,
):
    # Issue #65: the page refuses what the command line refuses. From
    # `radialrig setup` there is no app page to open the project in, so
    # the card's press is Check again.
    root = _make_project(tmp_path / "monorepo")
    mnt.mount(root, scope=SCOPE)
    part = root / "apps" / "website"
    part.mkdir(parents=True)
    before = tree(root)

    run = walk(part)
    page = run.page()
    head, said, details = card_of(page)
    assert head == "Set up RadialRig in website"
    assert said == "This folder is already part of monorepo. Nothing was written."
    assert setup_web.INSIDE_WHY.format(folder="website") in details
    assert f"That project is at {root.resolve()}" in details
    assert 'value="mount"' not in page
    assert f'value="survey">{setup_web.CHECK_AGAIN_LABEL}</button>' in page
    assert "Open monorepo" not in page

    status, again, _ = run.post(mount_form(run))
    assert status == 200
    assert card_of(again)[1] == said
    assert tree(root) == before
    assert run.stop() == report.REFUSED


def test_every_conflict_reason_the_engine_knows_reaches_the_page(tmp_path, walk):
    root = _make_project(tmp_path / "many-conflicts")
    (root / "docs").mkdir()
    (root / "docs" / "spec.md").write_bytes(b"ours\n")
    (root / mnt.AGENTS_REL).write_bytes(
        f"# Ours\n{mnt.FENCE_BEGIN}\nold\n{mnt.FENCE_END}\n".encode("utf-8")
    )
    (root / mnt.MCP_REL).write_bytes(b"not json at all\n")
    before = tree(root)

    run = walk(root)
    page = run.page()
    _, said, details = card_of(page)
    assert said == setup_web.BLOCKED_MANY.format(count=3)
    # `.mcp.json` takes the page's own reason, true of every way that
    # file stops a setup (phase 2 review F6); the rest are the engine's.
    own = {mnt.MCP_REL: setup_web.MCP_REASON}
    assert details == setup_web.BLOCKERS_HEAD + "".join(
        f" {path} : {own.get(path) or report.conflict_reason(path)}"
        for path in (mnt.MCP_REL, mnt.AGENTS_REL, "docs/spec.md"))
    assert tree(root) == before

    # `.mcp.json` alone has a sentence of its own, true whether the file
    # isn't JSON or is JSON not laid out as settings.
    for body in (b"not json at all\n", b"[1, 2]\n", b'{"mcpServers": []}\n'):
        alone = _make_project(tmp_path / f"settings-{len(body)}")
        (alone / mnt.MCP_REL).write_bytes(body)
        single = walk(alone)
        _, said, details = card_of(single.page())
        assert said == setup_web.BLOCKED_SETTINGS.format(file=mnt.MCP_REL), body
        assert said == (
            "RadialRig can't add its entries to .mcp.json, because it isn't valid "
            "JSON or isn't laid out as settings. Fix it, then check again. Nothing "
            "was written.")
        assert details.startswith(f"{setup_web.BLOCKERS_HEAD} {mnt.MCP_REL} : ")
        assert single.stop() == report.REFUSED


# --- C: already mounted ------------------------------------------------

def test_an_already_mounted_project_is_reported_and_left_alone(
    tmp_path, walk, fixed_day,
):
    root = _make_project(tmp_path / "mounted-host")
    mnt.mount(root, scope=SCOPE)
    # This state is terminal when there is nothing left for the page to
    # do, so the fixture carries every skill this package ships and no
    # client is on this machine.
    for name in sk.catalog_names():
        sk.add_all(root, name)
    before = tree(root)

    run = walk(root)
    page = run.page()
    body = main_of(page)
    # The folder's own name is the heading, its path beside it, and one
    # sentence says there is nothing left to add from here.
    assert (f'<div class="project-name"><h1>{root.name}</h1>'
            f'<span class="path">{html.escape(str(root), quote=False)}</span></div>') in body
    assert setup_web.ALREADY_COMPLETE in text_of(page)
    assert "<form" not in body
    assert "upgrade" not in text_of(page)
    assert closing_of(page) == setup_web.CLOSED
    assert tree(root) == before
    assert run.wait() == report.NOOP


# --- D: local only, token only ----------------------------------------

def test_a_request_without_a_token_is_refused(tmp_path, walk):
    run = walk(_make_project(tmp_path / "host"))
    status, body, _ = run.get("/setup")
    assert status == 403
    assert setup_web.worded(setup_web.REFUSE_TOKEN).split(".")[0] in body

    status, body, _ = run.get("/setup?s=not-the-token")
    assert status == 403

    status, body, _ = run.post({"token": "not-the-token", "action": "mount"})
    assert status == 403
    assert not (run.root / mnt.MANIFEST_REL).exists()


def test_the_launch_token_is_single_use(tmp_path, walk):
    run = walk(_make_project(tmp_path / "host"))
    first, _, headers = run.get(f"/setup?t={run.session.launch_token}")
    assert first == 303
    second, body, _ = run.get(f"/setup?t={run.session.launch_token}")
    assert second == 403
    assert "already been used" in body
    # The session token it was redeemed for still works.
    assert run.get(headers["Location"])[0] == 200


def test_a_cross_origin_post_is_refused(tmp_path, walk):
    root = _make_project(tmp_path / "host")
    before = tree(root)
    run = walk(root)
    run.page()
    for headers in (
        {"Origin": "http://evil.example"},
        {"Origin": "https://127.0.0.1"},
        # An opaque origin. A sandboxed frame or a document served under
        # a no-referrer policy sends this; it is not this page.
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ):
        status, body, _ = run.post(mount_form(run), headers=headers)
        assert status == 403, headers
        assert setup_web.worded(setup_web.REFUSE_ORIGIN).split(".")[0] in body
    assert tree(root) == before

    # A same-origin post from the page itself is accepted.
    status, _, _ = run.post(
        {"token": run.session.token, "action": "survey"},
        headers={"Origin": run.session.origin, "Sec-Fetch-Site": "same-origin"},
    )
    assert status == 200


def test_a_cross_origin_get_is_refused(tmp_path, walk):
    # A GET writes nothing, but a valid token arriving on a cross-site
    # navigation is not this page either; it refuses like the POST.
    run = walk(_make_project(tmp_path / "host"))
    for headers in (
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ):
        status, body, _ = run.get(f"/setup?s={run.session.token}", headers=headers)
        assert status == 403, headers
        assert setup_web.worded(setup_web.REFUSE_ORIGIN).split(".")[0] in body

    # The browser's own navigations stay served: the address bar sends
    # Sec-Fetch-Site none and no Origin, a reload sends same-origin.
    for headers in ({"Sec-Fetch-Site": "none"}, {"Sec-Fetch-Site": "same-origin"}, {}):
        status, _, _ = run.get(f"/setup?s={run.session.token}", headers=headers)
        assert status == 200, headers


def test_a_request_for_another_host_is_refused(tmp_path, walk):
    run = walk(_make_project(tmp_path / "host"))
    status, _, _ = run.get(
        f"/setup?s={run.session.token}", headers={"Host": "radialrig.example"},
    )
    assert status == 403


def test_no_route_lists_a_directory(tmp_path, walk):
    run = walk(_make_project(tmp_path / "host"))
    token = run.session.token
    for path in (
        "/", "/assets/", "/docs/", "/setup/", f"/?s={token}", f"/assets/?s={token}",
        "/setup/../.gitattributes", "/..%2f..%2fwindows/win.ini",
        f"/../../../etc/passwd?s={token}",
    ):
        status, body, _ = run.get(path)
        assert status == 404, path
        assert "<a href" not in body.lower(), path
        assert "directory listing" not in body.lower(), path


def test_the_page_carries_no_external_reference(tmp_path, walk):
    run = walk(_make_project(tmp_path / "host"))
    page = run.page()
    assert not EXTERNAL_REF.search(page), EXTERNAL_REF.search(page).group(0)
    assert not EXTERNAL_CSS.search(page)
    assert "@font-face" in page
    assert "data:font/woff2;base64," in page
    headers = run.get(f"/setup?s={run.session.token}")[2]
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "strict-origin"


def test_the_response_headers_let_the_page_use_its_own_controls(tmp_path, walk):
    """Two headers decide whether the page's own buttons work.

    A browser computes the `Origin` header of a form submission under the
    document's referrer policy, so `Referrer-Policy: no-referrer` makes
    every POST from this page arrive as `Origin: null` and the origin
    guard refuses it. `strict-origin` keeps the origin on the POST and
    still sends no path and no query, so the session token never rides in
    a `Referer`.

    The close beacon needs `connect-src`: without it the policy falls
    back to `default-src 'none'`, the browser blocks `sendBeacon`, and
    `sendBeacon` reports success anyway, so an abandoned page would leave
    the server listening.
    """
    run = walk(_make_project(tmp_path / "host"))
    headers = run.get(f"/setup?s={run.session.token}")[2]

    assert headers["Referrer-Policy"] != "no-referrer"
    assert headers["Referrer-Policy"] == "strict-origin"

    policy = headers["Content-Security-Policy"]
    assert "connect-src 'self'" in policy
    assert "form-action 'self'" in policy
    # Still no fetch of anything but this page's own inlined bytes.
    assert "default-src 'none'" in policy
    assert "img-src data:" in policy

    # The same headers ride on a refusal, not only on the page.
    refused = run.get("/setup")[2]
    assert refused["Referrer-Policy"] == "strict-origin"


def test_a_launch_token_this_run_never_issued_is_not_called_spent(tmp_path, walk):
    """A token that was never issued and one that was already redeemed
    are different facts. Saying "already used" of the first is wrong."""
    run = walk(_make_project(tmp_path / "host"))
    status, body, _ = run.get("/setup?t=" + "deadbeef" * 6)
    assert status == 403
    assert "already been used" not in body
    assert setup_web.worded(setup_web.REFUSE_TOKEN).split(".")[0] in body
    # The real launch token is untouched by the attempt.
    assert run.get(f"/setup?t={run.session.launch_token}")[0] == 303
    assert run.get(f"/setup?t={run.session.launch_token}")[1].strip() == (
        setup_web.worded(setup_web.REFUSE_SPENT)
    )


def test_every_setup_refusal_body_is_pinned_byte_for_byte(tmp_path, walk):
    """The five refusals this page serves, written out in full.

    They carry slots now, so a second surface says its own name through
    this same code path rather than through a copy of it. What a slot may
    never do is move a byte of what this page has always served, so the
    five sentences are spelled here rather than assembled out of the
    constants they are pinning — and the two a request can reach are read
    back off the wire, where the bytes actually go.
    """
    from radialrig.activity_log import log_path

    pinned = {
        setup_web.REFUSE_TOKEN: (
            b"Refused: this request carried no valid setup token. The setup"
            b" page is reachable only from the URL radialrig setup printed.\n"
        ),
        setup_web.REFUSE_SPENT: (
            b"Refused: that launch link has already been used. Run radialrig"
            b" setup again for a new one.\n"
        ),
        setup_web.REFUSE_ORIGIN: (
            b"Refused: this request came from another origin. The setup page"
            b" answers only its own page on this machine.\n"
        ),
        setup_web.REFUSE_NOT_FOUND: (
            b"Not found. radialrig setup serves one page and lists no"
            b" directories.\n"
        ),
        # PLAN-0018 phase 2: the one refusal that names the log, built
        # from the same path the page reads, so the two cannot drift.
        # This file clears the profile variables, so on a platform whose
        # store row reads one of them there is no log and no sentence.
        setup_web.INTERNAL: (
            b"radialrig setup could not answer that request. The terminal"
            b" that started setup carries the detail."
            + (b" The log at " + str(log_path()).encode("utf-8") + b" carries it too."
               if log_path() is not None else b"")
            + b"\n"
        ),
    }
    assert len(pinned) == 5, "two refusals share one wording"
    for template, wanted in pinned.items():
        served = (setup_web.worded(template) + "\n").encode("utf-8")
        assert served == wanted, template

    run = walk(_make_project(tmp_path / "host"))
    assert run.get("/setup")[1].encode("utf-8") == pinned[setup_web.REFUSE_TOKEN]
    assert run.get("/nowhere")[1].encode("utf-8") == \
        pinned[setup_web.REFUSE_NOT_FOUND]


def test_an_oversized_form_body_is_refused(tmp_path, walk):
    root = _make_project(tmp_path / "host")
    before = tree(root)
    run = walk(root)
    run.page()
    status = run.announce(mount_form(run, scope="x" * (setup_web.MAX_BODY + 1)))
    assert status == 413
    assert tree(root) == before
    assert run.running


def test_one_failed_request_does_not_end_the_walkthrough(tmp_path, walk, monkeypatch):
    root = _make_project(tmp_path / "host")
    run = walk(root)
    assert run.page()

    def boom(session):
        raise RuntimeError("the project went away")

    # Scoped, never undone whole: `undo()` would also lift conftest's
    # profile isolation for the rest of this test and its teardown, and
    # the page's stop event would then land in the real log (PLAN-0018
    # phase 2, Invariant 7).
    with monkeypatch.context() as patch:
        patch.setattr(setup_web, "compute_state", boom)
        status, body, _ = run.get(f"/setup?s={run.session.token}")
        assert status == 500
        assert "could not answer" in body
    assert run.running
    assert ready(root) in run.page()


# --- PLAN-0016 phase 4: the page tells the truth about a partial press --

def test_a_skill_fault_after_the_mount_reports_the_mount(
    tmp_path, walk, fixed_day, monkeypatch,
):
    # setup-web-01: the mount has landed when the skills' write fails, so
    # the line says the setup stands, names the failure behind Details,
    # and exits partial; it does not say that nothing was written.
    root = _make_project(tmp_path / "host")
    detail = "the skills directory could not be written"

    def fails(plans):
        raise sk.SkillError(detail)

    monkeypatch.setattr(sk, "write_add_plans", fails)
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post(mount_form(run, skill=boxes(SKILL)))
    assert status == 200
    tone, sentence, _, details = result_of(page)
    assert (tone, sentence) == (
        setup_web.RESULT_PARTLY,
        f"Set up RadialRig in {root.name}, but couldn't add the skills you ticked. "
        "None of them was added. Check Details for what stopped them.")
    assert detail in details
    assert card_of(page) is None
    assert ran_commands(page) == [f"radialrig mount {root} --scope {SCOPE}"]
    assert closing_of(page) == setup_web.CLOSED
    assert mnt.is_mounted(root)
    assert not (root / SKILL_DIR).exists()
    assert run.wait() == report.PARTIAL


def two_copies(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """One client with both of its settings files on this machine, each
    holding a host entry, and one locatable read command."""
    env = client_env(tmp_path, store=True)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    exe = tmp_path / "bin" / "radialrig-read.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(cn, "_which", lambda name: str(exe))
    visible, hidden = roaming_config(env), store_config(env)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    hidden.write_bytes(CONFIG_HOSTS["empty-servers"])
    return visible, hidden


def refusing_client_writes(monkeypatch, *, after: int) -> list[Path]:
    """Every write to a client settings file past the first `after`
    refuses, the restores included. Returns the writes in order."""
    real = Path.write_bytes
    seen: list[Path] = []

    def flaky(self, data):
        if self.name == "claude_desktop_config.json":
            seen.append(self)
            if len(seen) > after:
                raise PermissionError(13, "Permission denied", str(self))
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    return seen


def test_a_file_error_from_the_mount_that_was_put_back_is_a_clean_stop(
    tmp_path, walk, monkeypatch,
):
    # cli-connect-02, the mount half: the writer put every write back,
    # so the card says nothing was changed and the page exits with an
    # error, not a partial; the 500 boundary is not reached.
    root = _make_project(tmp_path / "host")
    before = tree(root)
    real = Path.write_bytes

    def refuse(self, data):
        if self.name == "spec.md":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", refuse)
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post(mount_form(run))
    assert status == 200
    head, said, details = card_of(page)
    assert head == f"Set up RadialRig in {root.name}"
    assert said == setup_web.SETUP_FAILED
    assert "could not answer" not in page
    assert "Permission denied" in details and "docs/spec.md" in details
    assert "nothing was changed" in details
    assert closing_of(page) == setup_web.CLOSED_RETRY
    assert tree(root) == before
    assert run.wait() == report.ERROR


def test_a_file_error_from_the_mount_names_what_was_not_put_back(
    tmp_path, walk, monkeypatch,
):
    # The restore of one file refused too. The card says the setup
    # stopped partway and Details names exactly the path the disk shows
    # changed.
    root = _make_project(tmp_path / "host")
    original = b'{\n  "mcpServers": {}\n}\n'
    (root / ".mcp.json").write_bytes(original)
    before_files, before_dirs = tree(root)
    real = Path.write_bytes

    def flaky(self, data):
        if self.name == "spec.md":
            raise PermissionError(13, "Permission denied", str(self))
        if self.name == ".mcp.json" and data == original:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post(mount_form(run))
    assert status == 200
    _, said, details = card_of(page)
    assert said == setup_web.SETUP_PARTIAL
    assert "put back except: .mcp.json" in details
    assert "nothing was written" not in details
    assert closing_of(page) == setup_web.CLOSED
    after_files, after_dirs = tree(root)
    assert set(after_files) == set(before_files)
    assert after_dirs == before_dirs
    changed = {rel for rel in before_files if before_files[rel] != after_files[rel]}
    assert changed == {".mcp.json"}
    assert not mnt.is_mounted(root)
    assert run.wait() == report.PARTIAL


def test_a_file_error_from_the_connect_after_the_mount_reports_both(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    # cli-connect-02: the mount landed and the client's settings file
    # refused, and its restore found it as it stood. The line says the
    # setup stands and the client's settings weren't changed, and the page
    # exits with an error, not a partial: nothing is left part way.
    root = _make_project(tmp_path / "host")
    client_here.write_bytes(CONFIG_HOSTS["indent2"])
    original = client_here.read_bytes()
    refusing_client_writes(monkeypatch, after=0)
    run = walk(root, scope=SCOPE)
    assert 'name="connect"' in run.page(), "the offer is not on the review page"
    status, page, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    tone, sentence, warning, details = result_of(page)
    assert (tone, sentence, warning) == (
        setup_web.RESULT_PARTLY,
        f"Set up RadialRig in {root.name}, but couldn't connect Claude Desktop. Its "
        "settings weren't changed. Check Details for what stopped it.", "")
    assert "could not answer" not in page
    assert "Permission denied" in details and "nothing was changed" in details
    assert "part way" not in details
    assert f"radialrig connect {cn.DEFAULT_CLIENT} {root}" not in ran_commands(page)
    assert mnt.is_mounted(root)
    assert not cn.is_connected(root)
    assert client_here.read_bytes() == original
    assert run.wait() == report.ERROR


def test_a_file_error_from_the_connect_names_the_copy_not_put_back(
    tmp_path, walk, fixed_day, monkeypatch,
):
    # Two copies: the first lands, the second refuses, and the restore
    # of the first refuses too. The line says connecting stopped partway,
    # Details names that copy, and the disk shows it and nothing else
    # changed.
    root = _make_project(tmp_path / "host")
    visible, hidden = two_copies(tmp_path, monkeypatch)
    before = {visible: visible.read_bytes(), hidden: hidden.read_bytes()}
    seen = refusing_client_writes(monkeypatch, after=1)
    run = walk(root, scope=SCOPE)
    assert 'name="connect"' in run.page(), "the offer is not on the review page"
    status, page, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    landed = seen[0]
    tone, sentence, _, details = result_of(page)
    assert (tone, sentence) == (
        setup_web.RESULT_PARTLY,
        f"Set up RadialRig in {root.name}, but couldn't finish connecting Claude "
        "Desktop. Some of its settings may be left changed. Check Details for the "
        "list.")
    assert f"put back except: {landed}" in details
    assert "nothing was written" not in details
    changed = {path for path in before if path.read_bytes() != before[path]}
    assert changed == {landed}
    assert mnt.is_mounted(root)
    assert not cn.is_connected(root)
    assert run.wait() == report.PARTIAL


def test_a_file_error_from_a_registration_on_its_own_is_a_partial_press(
    tmp_path, walk, fixed_day, monkeypatch,
):
    # On a set-up project the connection was the whole press. One copy
    # landed and could not be put back, which is neither "couldn't
    # connect" nor a clean error: a partial press, naming the copy.
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(root, name)
    visible, hidden = two_copies(tmp_path, monkeypatch)
    before = {visible: visible.read_bytes(), hidden: hidden.read_bytes()}
    seen = refusing_client_writes(monkeypatch, after=1)
    run = walk(root, scope=SCOPE)
    assert ">Connect Claude Desktop</button>" in run.page()
    status, done, _ = run.post({"token": run.session.token, "action": "skills",
                                "connect": cn.DEFAULT_CLIENT, **CHOSEN})
    assert status == 200
    landed = seen[0]
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        setup_web.RESULT_PARTLY,
        "Couldn't finish connecting Claude Desktop. Some of its settings may be "
        "left changed. Check Details for the list.")
    assert f"put back except: {landed}" in details
    changed = {path for path in before if path.read_bytes() != before[path]}
    assert changed == {landed}
    assert not cn.is_connected(root)
    assert run.wait() == report.PARTIAL


def test_a_registration_on_its_own_that_was_put_back_did_not_run(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(root, name)
    client_here.write_bytes(CONFIG_HOSTS["indent2"])
    original = client_here.read_bytes()
    refusing_client_writes(monkeypatch, after=0)
    run = walk(root, scope=SCOPE)
    assert ">Connect Claude Desktop</button>" in run.page()
    status, done, _ = run.post({"token": run.session.token, "action": "skills",
                                "connect": cn.DEFAULT_CLIENT, **CHOSEN})
    assert status == 200
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        setup_web.RESULT_FAILED, "Couldn't connect Claude Desktop. Nothing was changed.")
    assert "Permission denied" in details and "nothing was changed" in details
    assert closing_of(done) == setup_web.CLOSED_RETRY
    assert client_here.read_bytes() == original
    assert not cn.is_connected(root)
    assert run.wait() == report.ERROR


def test_a_skill_fault_on_the_mount_press_names_the_registration_not_attempted(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    # The mount landed, the skills' write failed, and the ticked
    # connection is not attempted after a step that failed: the line says
    # so. Without the box ticked it says nothing about a connection.
    detail = "the skills directory could not be written"

    def fails(plans):
        raise sk.SkillError(detail)

    monkeypatch.setattr(sk, "write_add_plans", fails)
    ticked = _make_project(tmp_path / "ticked")
    run = walk(ticked, scope=SCOPE)
    assert 'name="connect"' in run.page(), "the offer is not on the review page"
    status, page, _ = run.post(
        mount_form(run, skill=boxes(SKILL), connect=cn.DEFAULT_CLIENT))
    assert status == 200
    tone, sentence, _, details = result_of(page)
    assert tone == setup_web.RESULT_PARTLY
    assert sentence.endswith(" Claude Desktop wasn't connected.")
    assert detail in details
    # The engine's own sentence about it stays in the log, not on screen.
    assert setup_web.CONNECT_NOT_ATTEMPTED not in text_of(page)
    assert mnt.is_mounted(ticked)
    assert not cn.is_connected(ticked)
    assert run.wait() == report.PARTIAL

    unticked = _make_project(tmp_path / "unticked")
    other = walk(unticked, scope=SCOPE)
    other.page()
    status, page, _ = other.post(mount_form(other, skill=boxes(SKILL)))
    assert status == 200
    tone, sentence, _, details = result_of(page)
    assert detail in details
    assert "wasn't connected" not in sentence
    assert other.wait() == report.PARTIAL


def test_a_file_error_while_planning_the_registration_stops_the_page(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    # cli-connect-02, the plan half: the client's file cannot be read
    # while the offer is planned. The survey and the mount press both
    # stop on the card with the sentence, nothing is set up, and neither
    # reaches the 500 boundary or drops the offer without a word.
    client_here.write_bytes(CONFIG_HOSTS["indent2"])
    real = Path.read_bytes
    armed = [True]

    def refuse(self):
        if armed[0] and self.name == client_here.name:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)

    surveyed = _make_project(tmp_path / "surveyed")
    run = walk(surveyed, scope=SCOPE)
    # Issue #62: the box starts unticked. The first survey plans the
    # connection too (phase 2 review F1), and a file error there leaves
    # the offer as it stood: only a ticked box stops the page on it.
    status, page, _ = run.get(f"/setup?s={run.session.token}")
    assert status == 200
    assert card_of(page) is None
    assert 'name="connect"' in page, "the offer was dropped"
    # Ticked, the survey plans it and meets the error.
    status, page, _ = run.post(
        mount_form(run, action="survey", connect=cn.DEFAULT_CLIENT))
    assert status == 200
    assert "could not answer" not in page
    _, said, details = card_of(page)
    assert said == setup_web.SETUP_FAILED
    assert "Permission denied" in details and "nothing was changed" in details

    pressed = _make_project(tmp_path / "pressed")
    other = walk(pressed, scope=SCOPE)
    status, page, _ = other.post(mount_form(other, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    assert "could not answer" not in page
    _, said, details = card_of(page)
    assert said == setup_web.SETUP_FAILED
    assert "Permission denied" in details and "nothing was changed" in details
    assert not mnt.is_mounted(pressed)

    armed[0] = False
    assert run.wait() == report.ERROR
    assert other.wait() == report.ERROR


# --- E: lifecycle ------------------------------------------------------

def test_the_server_stops_after_a_terminal_state(tmp_path, walk, fixed_day):
    root = _make_project(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    run.page()
    status, _, _ = run.post(mount_form(run))
    assert status == 200
    assert run.wait() == report.OK

    with pytest.raises(OSError):
        conn = http.client.HTTPConnection(setup_web.HOST, run.session.port, timeout=5)
        conn.request("GET", f"/setup?s={run.session.token}")
        conn.getresponse()
    assert setup_web.STOPPED_LINE in run.stream.getvalue()


def test_the_server_stops_when_the_page_reports_it_is_closing(tmp_path, walk):
    root = _make_project(tmp_path / "host")
    run = walk(root)
    run.page()
    status, _, _ = run.post({"token": run.session.token, "action": "close"})
    assert status == 200
    assert run.wait() == report.NOOP
    assert not (root / mnt.MANIFEST_REL).exists()


# The close beacon's body, spelled here rather than assembled out of the
# script it is pinning. The body is the bytes it has always been, and the
# one line after it adds the render id the document root carries: this
# page stands between the review and the press, so its documents carry
# one and its server drops a close from a document it has already
# replaced (PLAN-0017 phase 4, setup-web-05; the manage page's D8). A
# close carrying no id is still the close it has always been.
BEACON_BODY = 'var body = "token=" + encodeURIComponent(token) + "&action=close";'
BEACON_RENDER = 'if (render) { body += "&render=" + encodeURIComponent(render); }'


def test_the_setup_close_beacon_body_is_the_bytes_it_has_always_been(
    tmp_path, walk,
):
    js = (packaged_web() / "page.js").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert BEACON_BODY in js
    assert BEACON_RENDER in js
    assert js.index(BEACON_BODY) < js.index(BEACON_RENDER)

    root = _make_project(tmp_path / "host")
    run = walk(root)
    page = run.page()
    assert js in page, "this page serves a script of its own"
    # The script reads the attribute off the root, and this document's
    # root carries it, once.
    root_tag = re.search(r"<html\b[^>]*>", page).group(0)
    assert f'root.getAttribute("{setup_web.RENDER_ATTR}")' in js
    assert root_tag.count(f'{setup_web.RENDER_ATTR}="') == 1, root_tag
    assert render_id(page) == "1"

    # A close from a document this server has replaced is dropped.
    replaced = render_id(run.page())
    status, body, _ = run.post({
        "token": run.session.token, "action": "close",
        setup_web.RENDER_FIELD: "1",
    })
    assert (status, body) == (200, "")
    assert run.running
    # The body with no id in it is the close it has always been, and
    # still stops this server.
    assert replaced == "2"
    status, _, _ = run.post({"token": run.session.token, "action": "close"})
    assert status == 200
    assert run.wait() == report.NOOP
    assert not (root / mnt.MANIFEST_REL).exists()


# --- F: exit codes -----------------------------------------------------

def test_the_exit_code_is_the_outcome_reached(tmp_path, walk, fixed_day):
    mounted = _make_project(tmp_path / "will-mount")
    run = walk(mounted, scope=SCOPE)
    run.page()
    run.post(mount_form(run))
    assert run.wait() == 0

    conflicted = _make_project(tmp_path / "will-refuse")
    (conflicted / "docs").mkdir()
    (conflicted / "docs" / "spec.md").write_bytes(b"ours\n")
    run = walk(conflicted)
    run.page()
    assert run.stop() == 3

    already = _make_project(tmp_path / "already")
    mnt.mount(already, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(already, name)
    run = walk(already)
    run.page()
    assert run.wait() == 4

    assert setup_web.serve(tmp_path / "no-such-directory", open_browser=False) == 1


def test_an_unreadable_mount_record_stops_the_walkthrough(tmp_path, walk):
    root = _make_project(tmp_path / "corrupt")
    (root / ".radialrig").mkdir()
    (root / mnt.MANIFEST_REL).write_bytes(b"{ not json\n")
    run = walk(root)
    page = run.page()
    _, said, details = card_of(page)
    assert said == setup_web.SETUP_FAILED
    assert mnt.MANIFEST_REL in details
    assert run.wait() == report.ERROR


def test_the_command_dispatches_setup(monkeypatch, tmp_path):
    seen = {}

    def fake_serve(root, **kwargs):
        seen["root"] = root
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(setup_web, "serve", fake_serve)
    root = _make_project(tmp_path / "host")
    assert cli.main(["setup", str(root), "--scope", "s", "--owner", "o",
                     "--no-browser"]) == 0
    assert seen["root"] == root.resolve()
    assert seen["scope"] == "s"
    assert seen["owner"] == "o"
    assert seen["open_browser"] is False
    assert cli.main(["setup", str(tmp_path / "missing")]) == report.ERROR


# --- G: what the page is made of --------------------------------------

# The check that the packaged marks and favicons match the brand sheet
# reads brand/, which stays private, so it runs in the workshop-only
# module (PLAN-0023 phase 1).


def test_the_vendored_font_survives_a_checkout():
    web = packaged_web()
    blob = (web / setup_web.FONT_FILE).read_bytes()
    assert blob[:4] == b"wOF2", "the vendored font is not a woff2"
    digest = hashlib.sha256(blob).hexdigest()
    source = (web / "fonts" / "SOURCE.txt").read_text(encoding="utf-8")
    assert digest in source, "the recorded sha256 is not this file's"
    assert "space-grotesk" in source

    attributes = resolve_root() / ".gitattributes"
    if not attributes.is_file():
        pytest.skip("no .gitattributes at the corpus root")
    rules = attributes.read_text(encoding="utf-8")
    for pattern in ("*.woff2 binary", "*.png binary", "*.ico binary"):
        assert pattern in rules, f"{pattern} is not pinned; a checkout could rewrite it"


def test_the_page_carries_both_marks_and_the_light_alternate(tmp_path, walk):
    web = packaged_web()
    run = walk(_make_project(tmp_path / "host"))
    page = run.page()
    for name in ("radialrig-symbol-dark.svg", "radialrig-symbol-light.svg"):
        mark = (web / "marks" / name).read_bytes().decode("utf-8")
        mark = mark.replace("\r\n", "\n").strip()
        assert mark in page, f"{name} is not inlined verbatim"
    assert "prefers-color-scheme: light" in page
    assert "#a95d00" in page.lower(), "the light-context amber anchor is missing"
    assert "#f0a32d" in page.lower(), "the primary action's amber is missing"


def test_every_state_leads_with_a_heading(tmp_path, walk, fixed_day):
    """One heading per screen, the card's included: a refusal's whole
    message is its card, headed by the setup it stopped."""
    conflicted = _make_project(tmp_path / "conflict-host")
    (conflicted / "docs").mkdir()
    (conflicted / "docs" / "spec.md").write_bytes(b"ours\n")
    already = _make_project(tmp_path / "already-host")
    mnt.mount(already, scope=SCOPE)

    pages = {}
    for root in (_host(tmp_path / "review-host"), conflicted, already):
        pages[root.name] = walk(root, scope=SCOPE).page()
    for name, page in pages.items():
        assert page.count("<h1") == 1, name
        assert "</h1>" in page, name
    refusal = pages["conflict-host"]
    assert '<section class="card" role="status" aria-live="polite">' in refusal
    assert "<h1>Set up RadialRig in conflict-host</h1>" in refusal
    assert "<h1>already-host</h1>" in pages["already-host"]


def test_the_command_block_carries_its_panel_in_every_state(tmp_path, walk):
    """Every command on the screen is one line with its Copy beside it,
    under one rule that isn't scoped to a list, so a command drawn
    anywhere on the screen gets its panel."""
    root = _make_project(tmp_path / "mounted-host")
    mnt.mount(root, scope=SCOPE)
    page = walk(root).page()
    assert shown_commands(page) == []
    assert f'<div class="copy-line"><code>radialrig skill add {SKILL} {root} ' \
        f"--harness {FIRST.name}</code>" in page

    css = (packaged_web() / "page.css").read_text(encoding="utf-8")
    assert ".steps .cmd" not in css, "the command block is styled only inside a list"
    assert re.search(r"^\.cmd \{", css, re.M), "no unscoped .cmd rule"
    assert re.search(r"^\.screen \.copy-line \{", css, re.M), "no rule for a copy line"


def test_the_light_alternate_keeps_its_ink_off_the_amber_anchor(tmp_path, walk):
    """Rig Black on Amber 700 is 3.76:1, under AA at chip size; Rig White
    on it is 4.65:1. The dark alternate's anchor is light enough to keep
    the black ink, so only the light block swaps.

    The chip no longer sits on the anchor — the accepted wireframe
    outlines it and spends the screen's one amber on the press — so the
    element the ink pairs with is the press's hover fill.
    """
    css = (packaged_web() / "page.css").read_text(encoding="utf-8")
    dark, light = css.split("prefers-color-scheme: light", 1)
    assert "--on-accent: #111318" in dark
    assert "--on-accent: #f7f8fc" in light
    assert re.search(
        r"button\.primary:hover \{[^}]*color: var\(--on-accent\);", css)
    # The endorsed first mention reflows on a narrow viewport; it is part
    # of the header treatment, so it is not dropped.
    assert ".endorse { display: none" not in css
    # No narrow-viewport block hides anything. The redesigned screens'
    # rules follow the first block now (PLAN-0027 phase 1), so each block
    # is read on its own.
    for width in ("34rem", "46rem"):
        narrow = css.split(f"@media (max-width: {width}) {{", 1)[1].split("\n}", 1)[0]
        assert "display: none" not in narrow, width


def test_one_amber_carries_meaning_on_the_review_screen(
    tmp_path, walk, fixed_day, client_here,
):
    """The press that writes, and nothing else.

    The accepted wireframe moves three elements off the accent — the
    project rail, the checked choice and the chip on a row that touches a
    file already standing — so the review screen has one amber element
    carrying meaning. The focus ring is the accent's one other
    appearance, and it says nothing until a reader has moved to a
    control.
    """
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")

    # The three the wireframe moved off, each named with what it took.
    assert ".chip.touches {\n  background: transparent;" in css
    assert "border-color: var(--chip-ink);" in css
    assert ".skills .choice:has(input:checked) { border-color: var(--muted); }" in css
    assert ".skills .choice input { accent-color: var(--muted); margin: 0; }" in css
    assert "border-left: 3px solid var(--accent)" not in css.split(".root {", 1)[-1] \
        .split("}", 1)[0]

    # And the one that keeps it.
    assert "button.primary { background: var(--accent-strong);" in css

    run = walk(_host(tmp_path / "host"), scope=SCOPE)
    review = run.page()
    # Nothing on the page paints itself, so the stylesheet is the only
    # place the amber can arrive from.
    assert "style=" not in visible(review)
    for hue in ("#f8bd5c", "#f0a32d", "#a95d00"):
        assert hue not in visible(review).lower()
    assert review.count(
        '<button class="primary" type="submit" name="action" value="mount">') == 1


def test_a_long_path_wraps_inside_its_own_column():
    """A path with no break opportunity widened the whole table past the
    sheet and pushed "What it does" off the page (BUG-0012): the table is
    auto-laid-out, and under `break-word` a cell grows to its unbroken
    content before any wrap applies. `anywhere` lets the browser narrow
    the path column and break the path inside itself (PLAN-0018 phase 3,
    prescription 1). The listing's own `td.path` rule is out of that
    plan's scope and stays as it is.
    """
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    path_rule = css.split("\ntd.path {", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: anywhere" in path_rule
    assert "break-word" not in path_rule
    # The rest of the rule is untouched.
    assert "min-width: 18ch" in path_rule
    assert "Cascadia Mono" in path_rule
    # The comment above the rule no longer argues for break-word.
    assert "break-word, not anywhere" not in css
    # The listing that had its own `td.path` rule is a list of rows since
    # PLAN-0027 phase 1, and its rule went with its table.
    assert "table.plan.projects" not in css


def test_the_page_is_readable_without_color(tmp_path, walk):
    run = walk(_make_project(tmp_path / "host"))
    page = run.page()
    # Each cell says its state in a word beside its mark, every box has a
    # name a reader by ear hears, and the press has a visible focus state.
    assert grid_boxes(page)
    for _, name, _ in grid_boxes(page):
        assert name.endswith((f"for {FIRST.label}", f"for {SECOND.label}")), name
    assert page.count(f"<span>{setup_web.GLYPH_MINUS}</span>"
                      f"{setup_web.CELL_OFF_LABEL}</span>") == len(grid_boxes(page))
    assert page.count('<th scope="col">') == 1 + len(AGENTS)
    assert ":focus-visible" in page
    assert "outline: 2px solid var(--accent)" in page
    assert '<button class="primary" type="submit" name="action" value="mount">' in page


def test_the_page_says_nothing_about_who_typed_a_change(
    tmp_path, walk, fixed_day, client_here,
):
    root = _host(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    pages = [run.page()]
    # The connect offer's own states are collected too, so every sentence
    # PLAN-0007 Phase 2 adds passes the same check the rest already do.
    pages.append(run.post(mount_form(run, skill=boxes(SKILL), connect=cn.DEFAULT_CLIENT))[1])
    run.wait()

    # Its own scope, so the two runs do not compete for one entry key.
    second = _make_project(tmp_path / "second")
    mnt.mount(second, scope="second-scope")
    later = walk(second, scope="second-scope")
    pages.append(later.page())
    pages.append(later.post({"token": later.session.token, "action": "skills",
                             "connect": cn.DEFAULT_CLIENT, **CHOSEN})[1])
    later.wait()
    for page in pages:
        text = visible(page).lower()
        hit = FORBIDDEN_WORD.search(text)
        assert hit is None, f"the page says '{hit.group(0) if hit else ''}'"
    # The amendment is a narrowing, not an amnesty: the substring is on
    # the page, inside a name and nowhere else.
    assert "hand" in visible(pages[0]).lower()


def test_the_page_reads_its_bytes_from_package_data():
    web = packaged_web()
    assert web is not None
    kit = setup_web.assets()
    assert kit["css"] == (web / "page.css").read_bytes().decode("utf-8").replace(
        "\r\n", "\n")
    assert kit["js"].startswith("/* The setup page's only script")
    assert kit["font"] == (web / setup_web.FONT_FILE).read_bytes()
    assert len(kit["icons"]) == len(setup_web.FAVICONS)
    assert kit["apple"].startswith("data:image/png;base64,")


def test_the_manifest_the_page_writes_is_the_manifest_mount_writes(
    tmp_path, walk, fixed_day,
):
    root = _make_project(tmp_path / "host")
    twin = _make_project(tmp_path / "twin")
    run = walk(root, scope=SCOPE)
    run.page()
    run.post(mount_form(run))
    run.wait()
    mnt.mount(twin, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    mine = json.loads((root / mnt.MANIFEST_REL).read_text(encoding="utf-8"))
    theirs = json.loads((twin / mnt.MANIFEST_REL).read_text(encoding="utf-8"))
    assert mine == theirs
    assert mine["scope"] == SCOPE


def test_a_mounted_page_unmounts_from_the_command_line(tmp_path, walk, fixed_day):
    """The two surfaces leave one project, not two dialects of one."""
    root = _host(tmp_path / "host")
    before = tree(root)
    run = walk(root, scope=SCOPE)
    run.page()
    run.post(mount_form(run))
    assert run.wait() == report.OK
    assert cli.main(["unmount", str(root)]) == report.OK
    assert tree(root) == before


# --- I: the skills kit on the page -------------------------------------
# D-36 and PLAN-0006 Phase 3. The oracle is the engine, exactly as the
# mount's is: what the page writes is what `skills.add()` writes on a
# twin, and what the second table shows is what the engine computed.


def test_the_review_state_offers_every_packaged_skill_unchecked(tmp_path, walk):
    root = _host(tmp_path / "host")
    before = tree(root)
    run = walk(root, scope=SCOPE)
    review = run.page()

    # Row by row, one skill per row and one cell per agent.
    assert [value for value, _, _ in grid_boxes(review)] == [
        setup_web.box_value(harness.name, name)
        for name in sk.catalog_names() for harness in AGENTS]
    assert f"<h2>{setup_web.SKILLS_HEAD}</h2>" in review
    # Unchecked to begin with: the setup is what the user came for, and a
    # box that arrives ticked installs something nobody asked for.
    assert not any(checked for _, _, checked in grid_boxes(review))
    assert " checked>" not in review
    # Nothing is ticked, so every skill's line in Details is drawn hidden
    # and the files that show are the setup's own.
    plan = mnt.mount_plan(root, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    assert [path for path, _, on in files_listed(review) if on] == [
        mnt.one_line(d.path) for d in plan.dispositions()]
    assert tree(root) == before


def test_the_boxes_stand_in_one_group_per_agent_with_a_tick_all(tmp_path, walk):
    """PLAN-0015 phase 3 prescription 1, as the grid draws it: one row per
    packaged skill with its line once, one column per agent in map order
    headed by the agent's label, a tick box per cell, and Add all at the
    head of each column, which ticks that column and nothing else and
    ships hidden for the script to wire. No page constant names an agent."""
    root = _host(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()
    assert setup_web.ADD_ALL_LABEL == "Add all"
    assert setup_web.ADD_ALL_NAME == "Add all skills for {agent}"

    heads = re.findall(
        r'<span class="agent-head"><span>([^<]*)</span>(<button class="link" '
        r'type="button" data-all="([^"]*)" aria-label="([^"]*)" hidden>Add all</button>)?',
        review)
    assert [(label, agent, named) for label, _, agent, named in heads] == [
        (h.label, h.name, f"Add all skills for {h.label}") for h in AGENTS]
    names = sk.catalog_names()
    assert [name for _, name, _ in grid_boxes(review)] == [
        f"{name} for {h.label}" for name in names for h in AGENTS]
    for harness in AGENTS:
        assert review.count(f'data-agent="{harness.name}"') == len(names)
    for name in names:
        assert review.count(f"<strong>{name}</strong>") == 1, name
    # The control is a plain button, never a submit, so with the script
    # off it changes nothing and the form posts what the reader ticked.
    script = review.split("<script", 1)[1]
    assert 'querySelectorAll("button[data-all]")' in script
    assert 'boxes[b].getAttribute("data-agent") === agent' in script
    # Nothing on the page says "harness"; the labels come from the map.
    said = text_of(re.sub(r'<div class="copy-line">.*?</div>', "", review, flags=re.S))
    assert "harness" not in said.lower()
    for harness in AGENTS:
        assert harness.label in said


def test_a_checked_box_shows_a_second_table_and_writes_nothing(tmp_path, walk):
    """A ticked box and Refresh: the box comes back ticked, Details shows
    the skill's folder and its agent's record, and the command that adds
    it, and nothing is written."""
    root = _host(tmp_path / "host")
    before = tree(root)
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post(
        {"token": run.session.token, "action": "survey", "skill": boxes(SKILL), **CHOSEN},
    )
    assert status == 200
    assert page.count(" checked>") == len(AGENTS), "a box did not survive the refresh"
    shown = [path for path, _, on in files_listed(page) if on]
    for harness in AGENTS:
        assert f"{harness.skills}/{SKILL}/" in shown
        assert harness.record in shown
    assert shown_commands(page)[1:] == [
        f"radialrig skill add {SKILL} {root} --harness {harness.name}"
        for harness in AGENTS]
    assert tree(root) == before


def test_two_boxes_for_one_agent_write_one_record_naming_both(tmp_path, walk, fixed_day):
    """The record threads through a press's names for one agent, as it
    does on the command line: two boxes ticked for one agent leave one
    record naming both, and Details lists the record once."""
    root = _make_project(tmp_path / "host")
    twin = _make_project(tmp_path / "twin")
    first_two = sk.catalog_names()[:2]
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post({
        "token": run.session.token, "action": "survey",
        "skill": boxes(*first_two, agents=(SECOND,)), **CHOSEN})
    assert status == 200
    drawn = files_listed(page)
    assert [path for path, _, _ in drawn].count(SECOND.record) == 1, drawn
    assert len({path for path, _, _ in drawn}) == len(drawn), "a path is drawn twice"
    assert (SECOND.record, "", True) in drawn
    assert (FIRST.record, "", False) in drawn

    status, mounted, _ = run.post(mount_form(run, skill=boxes(*first_two, agents=(SECOND,))))
    assert status == 200
    assert run.wait() == report.OK
    record = sk.read_record(root, SECOND)
    assert [entry["name"] for entry in record["skills"]] == sorted(first_two)
    assert sk.read_record(root, FIRST) is None

    mnt.mount(twin, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    sk.write_add_plans(sk.add_plans(twin, first_two, targets=(SECOND.name,)))
    assert snapshot(root) == snapshot(twin)


def test_a_walkthrough_with_a_box_checked_matches_the_engine_byte_for_byte(
    tmp_path, walk, fixed_day,
):
    page_root = _host(tmp_path / "page-host")
    twin = _host(tmp_path / "twin-host")
    assert tree(page_root) == tree(twin)

    run = walk(page_root, scope=SCOPE)
    run.page()
    status, mounted, _ = run.post(mount_form(run, skill=boxes(SKILL)))
    assert status == 200
    assert run.wait() == report.OK

    setup = mnt.mount(twin, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    written = [d for r in sk.add_all(twin, SKILL) for d in r.dispositions]

    page_files, page_dirs = tree(page_root)
    twin_files, twin_dirs = tree(twin)
    assert set(page_files) == set(twin_files)
    for rel, data in sorted(twin_files.items()):
        assert page_files[rel] == data, f"{rel} differs from the engine's add"
    assert page_dirs == twin_dirs
    assert snapshot(page_root) == snapshot(twin)

    # The line counts the skill once, whichever agents it went in for,
    # and its Details lists exactly what the engine wrote.
    assert result_of(mounted)[:2] == (
        setup_web.RESULT_DONE,
        f"Set up RadialRig in {page_root.name} and added 1 skill. Start a new agent "
        "session to pick it up.")
    assert changed_of(mounted) == expected_changed(setup.dispositions(), written)
    # And one command line per pair the press wrote for, the command
    # line's own form narrowed to the agent (PLAN-0015 phase 3), after the
    # setup's own, behind the Show commands box the line carries.
    assert ran_commands(mounted) == [
        f"radialrig mount {page_root} --scope {SCOPE}",
        *(f"radialrig skill add {SKILL} {page_root} --harness {harness.name}"
          for harness in AGENTS)]
    details = mounted.split('<div class="result', 1)[1].split("</details>", 1)[0]
    assert details.count('class="show-commands"') == 1


def test_a_posted_name_the_catalog_does_not_know_is_dropped(tmp_path, walk):
    root = _make_project(tmp_path / "host")
    before = tree(root)
    run = walk(root, scope=SCOPE)
    run.page()
    # A path, a skill the catalog does not carry, an agent the map has no
    # row for, and the bare name a box posted before the groups: dropped.
    status, page, _ = run.post([
        ("token", run.session.token), ("action", "survey"), *CHOSEN.items(),
        ("skill", "../../elsewhere"),
        ("skill", setup_web.box_value(SECOND.name, "no-such-skill")),
        ("skill", setup_web.box_value("no-such-agent", SKILL)),
        ("skill", SKILL),
    ])
    assert status == 200
    assert run.session.skills == ()
    assert " checked>" not in page
    assert shown_commands(page) == [f"radialrig mount {root} --scope {SCOPE}"]
    assert tree(root) == before

    # And a good pair beside a bad one keeps only the good one, whichever
    # order they were posted in: the pairs come back in map order, then
    # catalog order, never the order of the post.
    last, first = sk.catalog_names()[-1], sk.catalog_names()[0]
    status, page, _ = run.post([
        ("token", run.session.token), ("action", "survey"), *CHOSEN.items(),
        ("skill", setup_web.box_value(SECOND.name, last)),
        ("skill", "../../elsewhere"),
        ("skill", setup_web.box_value(FIRST.name, last)),
        ("skill", setup_web.box_value(SECOND.name, first)),
    ])
    assert status == 200
    assert run.session.skills == (
        (FIRST.name, last), (SECOND.name, first), (SECOND.name, last))
    assert tree(root) == before


def test_an_edited_skill_is_offered_disabled_and_is_never_overwritten(
    tmp_path, walk, fixed_day,
):
    root = _make_project(tmp_path / "host")
    sk.add(root, SKILL)
    (root / SKILL_DIR / "SKILL.md").write_bytes(b"# ours now\n")
    run = walk(root, scope=SCOPE)
    page = run.page()

    # The one agent whose copy was edited has the skill, so its cell reads
    # On and is no box; the other agent's cell is a box, because that
    # agent lacks it.
    values = [value for value, _, _ in grid_boxes(page)]
    assert setup_web.box_value(FIRST.name, SKILL) not in values
    assert setup_web.box_value(SECOND.name, SKILL) in values
    assert '<span class="face on has">' in page
    assert " checked>" not in page

    # A client that posts the missing box anyway is not obeyed past the
    # edit: the setup runs, the edited copy is left, the line says so, and
    # it names the one pair it wrote for and none for the copy it left.
    status, mounted, _ = run.post(mount_form(run, skill=boxes(SKILL)))
    assert status == 200
    assert (root / SKILL_DIR / "SKILL.md").read_bytes() == b"# ours now\n"
    assert (root / SECOND.skills / SKILL / "SKILL.md").is_file()
    tone, sentence, _, _ = result_of(mounted)
    assert tone == setup_web.RESULT_PARTLY
    assert sentence.endswith(setup_web.KEPT_EDITED)
    assert setup_web.KEPT_EDITED == "Left a skill copy you edited as it was."
    assert [line for line in ran_commands(mounted) if "skill add" in line] == [
        setup_web.skill_add_line(SECOND.name, SKILL, root)]
    assert run.wait() == report.PARTIAL


def test_a_foreign_skill_directory_refuses_with_no_force_affordance(tmp_path, walk):
    root = _make_project(tmp_path / "host")
    (root / SKILL_DIR).mkdir(parents=True)
    (root / SKILL_DIR / "ours.md").write_bytes(b"our own skill\n")
    before = tree(root)

    run = walk(root, scope=SCOPE)
    page = run.page()
    assert setup_web.box_value(FIRST.name, SKILL) not in [v for v, _, _ in grid_boxes(page)]
    assert (f'<span class="cell-note">'
            f'{html.escape(setup_web.CELL_REASONS[setup_web.CELL_FOREIGN], quote=False)}'
            "</span>") in page

    # Posted anyway, the press meets the engine's refusal: one sentence,
    # what stands there behind Details, and Check again.
    status, refused, _ = run.post(mount_form(run, skill=boxes(SKILL)))
    assert status == 200
    _, said, details = card_of(refused)
    assert said == setup_web.SKILL_BLOCKED
    assert SKILL_DIR in details
    assert 'value="mount"' not in refused
    assert f'value="survey">{setup_web.CHECK_AGAIN_LABEL}</button>' in refused
    assert tree(root) == before, "the refusal wrote something"
    assert run.stop() == report.REFUSED


def test_a_mounted_project_can_still_add_a_skill(tmp_path, walk, fixed_day):
    root = _make_project(tmp_path / "host")
    twin = _make_project(tmp_path / "twin")
    mnt.mount(root, scope=SCOPE)
    mnt.mount(twin, scope=SCOPE)

    run = walk(root, scope=SCOPE)
    page = run.page()
    # Not terminal any more: there is something left the page can add.
    assert setup_web.ALREADY_OFFERS in text_of(page)
    assert closing_of(page) == ""
    assert ('<button class="primary" type="submit" name="action" value="skills">'
            f"{setup_web.ADD_LABEL}</button>") in page
    assert run.running

    status, done, _ = run.post(
        {"token": run.session.token, "action": "skills", "skill": boxes(SKILL), **CHOSEN},
    )
    assert status == 200
    assert result_of(done)[:2] == (
        setup_web.RESULT_DONE, "Added 1 skill. Start a new agent session to pick it up.")
    assert closing_of(done) == setup_web.STOPPED
    assert run.wait() == report.OK

    written = [d for r in sk.add_all(twin, SKILL) for d in r.dispositions]
    assert changed_of(done) == expected_changed(written)
    assert snapshot(root) == snapshot(twin)
    assert ran_commands(done) == [
        f"radialrig skill add {SKILL} {root} --harness {harness.name}" for harness in AGENTS]
    # The finished line, like every other sentence on the page, says
    # "agent", never "harness"; the flag keeps its name in the commands.
    assert "harness" not in text_of(
        re.sub(r'<div class="copy-line">.*?</div>', "", done, flags=re.S)).lower()


def test_ticking_all_in_one_group_installs_for_that_agent_alone(
    tmp_path, walk, fixed_day, capsys,
):
    """PLAN-0015 phase 3's done-condition, on `radialrig setup`.

    A fresh project shows a column of every packaged skill per agent with
    Add all at each head. Every box in the second agent's column ticked
    and the one press pressed: every skill is installed for that agent
    and none for the first, the line names one `--harness` command per
    pair, and `skill list` shows every skill installed under the second
    agent's directory and absent under the first's. The tree is byte for
    byte what the command line's whole-catalog add leaves.
    """
    root = _host(tmp_path / "host")
    twin = _host(tmp_path / "twin")
    names = sk.catalog_names()
    assert len(names) == 5
    run = walk(root, scope=SCOPE)
    review = run.page()
    assert len(grid_boxes(review)) == 2 * len(names)

    ticked = boxes(*names, agents=(SECOND,))
    status, mounted, _ = run.post(mount_form(run, skill=ticked))
    assert status == 200
    assert result_of(mounted)[:2] == (
        setup_web.RESULT_DONE,
        f"Set up RadialRig in {root.name} and added 5 skills. Start a new agent "
        "session to pick them up.")
    assert run.wait() == report.OK

    setup = mnt.mount(twin, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    written = list(dict.fromkeys(
        d for r in sk.add_catalog(twin, targets=(SECOND.name,)) for d in r.dispositions))
    assert snapshot(root) == snapshot(twin)
    assert changed_of(mounted) == expected_changed(setup.dispositions(), written)

    # Five lines, one per pair, each the command line's own form narrowed
    # to the second agent, and none naming the first.
    lines = [line for line in ran_commands(mounted) if "skill add" in line]
    assert lines == [f"radialrig skill add {name} {root} --harness {SECOND.name}"
                     for name in names]
    assert lines == [setup_web.skill_add_line(SECOND.name, name, root) for name in names]
    assert f"--harness {FIRST.name}" not in mounted
    assert "harness" not in text_of(
        re.sub(r'<div class="copy-line">.*?</div>', "", mounted, flags=re.S)).lower()

    # On disk and in `skill list`: installed for the second agent, absent
    # for the first.
    for name in names:
        assert (root / SECOND.skills / name / "SKILL.md").is_file()
    assert not (root / FIRST.skills).exists()
    assert cli.main(["skill", "list", str(root)]) == 0
    listed = capsys.readouterr().out
    for name in names:
        assert f"{FIRST.skills}/{name}/" in listed
        assert f"{SECOND.skills}/{name}/" in listed
    states = {(s.harness.name, s.name): s for s in sk.states_all(root)}
    assert all(states[(SECOND.name, name)].current for name in names)
    assert all(states[(FIRST.name, name)].record_entry is None for name in names)
    absent = [line for line in listed.splitlines() if mnt.ABSENT in line]
    current = [line for line in listed.splitlines() if mnt.CURRENT in line]
    assert len(absent) == 5 and all(FIRST.skills in line for line in absent)
    assert len(current) == 5 and all(SECOND.skills in line for line in current)


def test_a_group_whose_agent_has_every_skill_says_so_and_offers_no_boxes(
    tmp_path, walk, fixed_day, client_here,
):
    """Prescription 3 from `radialrig setup`. One agent complete: its
    column reads On in every cell, with no box and no Add all, and the
    other's has every box. Both complete and not connected: the screen
    offers the connection alone, Claude Desktop's row with Connect."""
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    sk.add_catalog(root, targets=(FIRST.name,))
    names = sk.catalog_names()

    run = walk(root, scope=SCOPE)
    page = run.page()
    assert [value for value, _, _ in grid_boxes(page)] == boxes(*names, agents=(SECOND,))
    assert page.count('<span class="face on has">') == len(names)
    assert f'data-all="{FIRST.name}"' not in page
    assert f'data-all="{SECOND.name}"' in page
    assert setup_web.ALREADY_OFFERS in text_of(page)
    assert f">{setup_web.ADD_LABEL}</button>" in page
    assert 'name="connect"' in page
    assert run.stop() == report.NOOP

    sk.add_catalog(root, targets=(SECOND.name,))
    # Still not terminal: the client on this machine isn't connected, so
    # its row is the screen's one press.
    again = walk(root, scope=SCOPE)
    page = again.page()
    assert grid_boxes(page) == []
    assert page.count('<span class="face on has">') == 2 * len(names)
    assert "data-all=" not in page
    assert f">{setup_web.ADD_LABEL}</button>" not in page
    assert ('<button class="primary" type="submit" name="action" value="skills">'
            "Connect Claude Desktop</button>") in page
    assert f"<span>{setup_web.NOT_CONNECTED}</span>" in page
    assert again.stop() == report.NOOP


def test_the_add_button_on_a_mounted_project_with_no_box_checked_writes_nothing(
    tmp_path, walk,
):
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    before = tree(root)
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post({"token": run.session.token, "action": "skills", **CHOSEN})
    assert status == 200
    assert tree(root) == before
    assert run.running, "an empty press is not the end of the walkthrough"


def test_a_recorded_outcome_is_never_lowered_after_a_write(tmp_path):
    """The monotonic rule. Without it, a page that installed a skill and
    left one edited file behind would report 5, and the very next survey
    — a mounted project with nothing left to do — would erase it with 4."""
    session = setup_web.Session(tmp_path, "scope", "owner")
    session.record(report.NOOP)
    assert session.outcome == report.NOOP

    session.record(report.PARTIAL, consequential=True)
    assert session.outcome == report.PARTIAL
    session.record(report.NOOP)
    assert session.outcome == report.PARTIAL

    # Everything except the erasure still moves the number.
    session.record(report.REFUSED)
    assert session.outcome == report.REFUSED
    session.finish(report.OK)
    assert session.outcome == report.OK


def test_the_skills_section_makes_no_new_request_and_keeps_the_policy(
    tmp_path, walk,
):
    root = _host(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, headers = run.post(
        {"token": run.session.token, "action": "survey", "skill": boxes(SKILL), **CHOSEN},
    )
    assert status == 200
    assert not EXTERNAL_REF.search(page)
    assert not EXTERNAL_CSS.search(page)
    policy = headers["Content-Security-Policy"]
    assert "default-src 'none'" in policy
    assert "connect-src 'self'" in policy
    assert headers["Referrer-Policy"] == "strict-origin"
    assert "<script" in page and page.count("<script") == 1


def test_the_skills_fieldset_is_styled_in_the_pages_own_idiom():
    css = (packaged_web() / "page.css").read_text(encoding="utf-8")
    # Qualified to the fieldset since PLAN-0014 phase 1, so a listing
    # row's `fact skills` span does not take the panel; still unnested.
    assert re.search(r"^fieldset\.skills \{", css, re.M), "no unnested .skills rule"
    dark, light = css.split("prefers-color-scheme: light", 1)
    assert "--choice: " in dark, "the choice surface has no dark token"
    assert "--choice: " in light, "the choice surface has no light companion"
    # The pinned command-block rule is untouched by this phase.
    assert re.search(r"^\.cmd \{", css, re.M)
    assert ".steps .cmd" not in css


# --- J: the connect kit on the page ------------------------------------
# PLAN-0007 Phase 2 prescription 5. The oracle is the command line's own
# engine, exactly as the mount's and the skills kit's are: what the page
# writes is what `connect.connect()` writes, and what the third table
# shows is what `connect.connect_plan()` computed.


def test_no_page_constant_names_a_client():
    """The client's name, its label, its restart line and every part of
    every settings-file path are data in the client table. A constant
    carrying any of them would be a second client's worth of edits
    waiting to happen.

    Every module-level constant of both renderers, not only the ones
    named CONNECT_*: a page string naming the client under some other
    name would slip a prefix check, and so would anything in report.py.
    """
    client = cn.client_for(cn.DEFAULT_CLIENT)
    forbidden = [client.name, client.label, client.restart]
    for location in client.locations:
        for part in location.parts:
            bare = part.strip("*?[]")
            if bare:
                forbidden.append(bare)
    for module in (setup_web, report):
        for name in dir(module):
            if not name.isupper() or name.startswith("_"):
                continue
            value = getattr(module, name)
            if not isinstance(value, str):
                continue
            for word in forbidden:
                assert word.lower() not in value.lower(), \
                    f"{module.__name__}.{name} names '{word}'"


def test_the_page_offers_connect_when_a_client_is_detected(
    tmp_path, walk, fixed_day, client_here,
):
    root = _host(tmp_path / "host")
    before = tree(root)
    run = walk(root, scope=SCOPE)
    review = run.page()

    client = cn.client_for(cn.DEFAULT_CLIENT)
    key = cn.entry_key(client, SCOPE)
    write_key = cn.write_entry_key(client, SCOPE)
    # The box and its hint are the owner's words, with the client's name
    # arriving from the client table rather than a page constant (issue
    # #62): the hint says every session the client opens gets both.
    assert ('<label class="check"><input type="checkbox" name="connect" '
            f'value="{client.name}" data-shows="connect:{client.name}">'
            "Connect Claude Desktop</label>") in review
    assert ('<p class="hint">Adds a rule lookup and a rule editor to every Claude '
            "Desktop session: chat, the Code tab, and local Cowork.</p>") in review
    # Unticked to begin with, so what follows it in Details is drawn
    # hidden: the two entries it adds, by name, and the files they go in.
    assert f'name="connect" value="{client.name}" data-shows="connect:{client.name}" checked' \
        not in review
    said = html.unescape(review)
    assert setup_web.CLIENT_KEYS.format(key=key, write_key=write_key) in said
    assert f'<div data-for="connect:{client.name}" hidden><h3>' in review
    assert (cn.CONNECT_REL, "", False) in files_listed(review)
    assert tree(root) == before, "the ready screen wrote something"

    # Ticked and refreshed, the box stays ticked and what follows it shows.
    status, ticked, _ = run.post(
        mount_form(run, action="survey", connect=client.name))
    assert status == 200
    assert f'data-shows="connect:{client.name}" checked>' in ticked
    assert f'<div data-for="connect:{client.name}"><h3>' in ticked
    assert (cn.CONNECT_REL, "", True) in files_listed(ticked)
    assert shown_commands(ticked)[-1] == f"radialrig connect {client.name} {root}"
    assert tree(root) == before, "the refreshed ready screen wrote something"

    status, mounted, _ = run.post(mount_form(run, connect=client.name))
    assert status == 200
    assert run.wait() == report.OK
    # The line says it connected and what to do next, in the client
    # table's name for it.
    assert result_of(mounted)[:3] == (
        setup_web.RESULT_DONE,
        f"Set up RadialRig in {root.name} and connected Claude Desktop. Fully quit "
        "and reopen Claude Desktop to use the connection.", "")
    assert ran_commands(mounted)[-1] == f"radialrig connect {client.name} {root}"
    assert cn.is_connected(root)
    servers = json.loads(client_here.read_text(encoding="utf-8"))["mcpServers"]
    entry = servers[key]
    assert entry["env"][cn.ROOT_ENV] == str(root)
    assert entry["args"] == []
    # The second key Details named is the entry the press wrote beside it.
    assert sorted(servers) == sorted([key, write_key])


def test_the_page_offers_no_connect_when_no_client_is_detected(
    tmp_path, walk, fixed_day,
):
    """No client on this machine is not a page failure: the setup still
    runs, and the offer simply is not made, with no word about it."""
    root = _host(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()
    client = cn.client_for(cn.DEFAULT_CLIENT)

    assert 'name="connect"' not in review
    assert client.label not in review
    assert '<div class="offer">' not in review

    status, mounted, _ = run.post(mount_form(run))
    assert status == 200
    assert result_of(mounted)[:2] == (
        setup_web.RESULT_DONE, f"Set up RadialRig in {root.name}.")
    assert client.label not in mounted
    assert run.wait() == report.OK
    assert not cn.is_connected(root)


def test_an_unreadable_client_profile_reads_as_no_client(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    """`is_dir` raises on a profile directory that denies traversal, and
    the offer asks `detect` before the ready screen and again before the
    press. The screen reads as it does on a machine with no client: no
    offer, and the setup still lands."""
    client = cn.client_for(cn.DEFAULT_CLIENT)
    assert cn.detect(client), "readable, the client is found"

    def denied(client, *, env=None):
        raise PermissionError("the profile directory denies traversal")

    monkeypatch.setattr(cn, "detect", denied)
    root = _host(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()

    assert f">{setup_web.SETUP_LABEL}</button>" in review
    assert 'name="connect"' not in review
    assert client.label not in review

    status, mounted, _ = run.post(mount_form(run, connect=client.name))
    assert status == 200
    assert result_of(mounted)[:2] == (
        setup_web.RESULT_DONE, f"Set up RadialRig in {root.name}.")
    assert client.label not in mounted
    assert run.wait() == report.OK
    assert not cn.is_connected(root)


def test_the_page_and_the_command_line_report_the_same_connect_rows(
    tmp_path, walk, fixed_day, client_here,
):
    root = _make_project(tmp_path / "host")
    twin = _make_project(tmp_path / "twin")
    run = walk(root, scope=SCOPE)
    run.page()
    # The box starts unticked (issue #62); ticked, Details lists the
    # files the connection's plan writes: its record in the folder, and
    # each settings file the client keeps.
    status, review, _ = run.post(
        mount_form(run, action="survey", connect=cn.DEFAULT_CLIENT))
    assert status == 200

    plan = cn.connect_plan(root, scope=SCOPE)
    for d in plan.dispositions:
        if d.path == cn.CONNECT_REL:
            assert (cn.CONNECT_REL, "", True) in files_listed(review)
        else:
            assert f"<li><code>{html.escape(str(Path(d.path)), quote=False)}</code></li>" \
                in review, d.path

    status, mounted, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    run.wait()

    # The same engine call on a twin with its own client directory, so
    # the two runs do not compete for one entry key: the line lists what
    # it wrote with the tag its verb takes.
    other = tmp_path / "other" / "Roaming"
    (other / "Claude").mkdir(parents=True)
    mnt.mount(twin, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    theirs = cn.connect(twin, scope=SCOPE, env={"APPDATA": str(other)})
    connected = [(path, tag) for path, tag in changed_of(mounted)
                 if path == cn.CONNECT_REL or path.endswith("claude_desktop_config.json")]
    assert [tag for _, tag in connected] == [
        setup_web.TAGS[d.verb] for d in theirs.dispositions if d.verb in setup_web.TAGS]


def test_a_connect_refusal_withdraws_the_offer_and_keeps_the_mount(
    tmp_path, walk, fixed_day, client_here,
):
    """A taken entry key is not a reason to withhold Set up project.

    The connection is an offer, never a precondition: the offer is
    withdrawn, one sentence says why in the screen's words, and the press
    the reader came for is still there. A press therefore carries no
    refused connection.

    The screen plans the connection when it is drawn, ticked or not,
    through the same read-only plan the press runs, so a taken name
    replaces the box before any press (phase 2 review F1). The sentence
    says to change Project name and press Refresh, and that brings the
    box back.
    """
    root = _make_project(tmp_path / "host")
    client = cn.client_for(cn.DEFAULT_CLIENT)
    key = cn.entry_key(client, SCOPE)
    client_here.write_bytes(
        (json.dumps({"mcpServers": {key: {"command": "theirs"}}}, indent=2)
         + "\n").encode("utf-8")
    )
    taken = client_here.read_bytes()

    run = walk(root, scope=SCOPE)
    first = run.page()
    assert 'value="mount"' in first, "a refused connection took the setup with it"
    assert 'name="connect"' not in first, "the box is offered under a taken name"
    assert setup_web.CONNECT_NAME_TAKEN.format(label=client.label) in text_of(first)
    assert (f"{setup_web.CONNECT_BLOCKERS_HEAD.format(label=client.label)} "
            f"{client_here.as_posix()}: ") in html.unescape(text_of(first))
    assert client_here.read_bytes() == taken, "drawing the screen wrote to the client"
    # A post that still carries the box, from a screen drawn before the
    # name was taken, meets the same plan and the same sentence.
    status, page, _ = run.post(
        mount_form(run, action="survey", connect=cn.DEFAULT_CLIENT))
    assert status == 200
    assert 'value="mount"' in page, "a refused connection took the setup with it"
    assert 'name="connect"' not in page, "the refused offer is still on the table"
    assert setup_web.CONNECT_NAME_TAKEN.format(label=client.label) in text_of(page)
    # What the sentence says to do: another name, then Refresh. The box
    # is back, unticked, and the client's file is as it was.
    status, renamed, _ = run.post(
        mount_form(run, action="survey", scope=f"{SCOPE}-two"))
    assert status == 200
    assert re.search(r'<input type="checkbox" name="connect" value="'
                     + cn.DEFAULT_CLIENT + r'" data-shows="[^"]*">', renamed), (
        "the box did not come back under a free name")
    assert setup_web.CONNECT_NAME_TAKEN.format(label=client.label) not in text_of(renamed)
    assert client_here.read_bytes() == taken

    status, mounted, _ = run.post(mount_form(run))
    assert status == 200
    assert result_of(mounted)[:2] == (
        setup_web.RESULT_DONE, f"Set up RadialRig in {root.name}.")
    assert run.wait() == report.OK
    assert client_here.read_bytes() == taken, "the setup touched the client's file"
    assert not cn.is_connected(root)


def test_a_press_that_still_carries_a_refused_connect_writes_nothing(
    tmp_path, walk, fixed_day, client_here,
):
    """The offer is withdrawn on the screen, so only a press from a screen
    drawn before the key was taken can carry it. That press is refused
    whole: a setup plus a refused connection would be half a press."""
    root = _make_project(tmp_path / "host")
    key = cn.entry_key(cn.client_for(cn.DEFAULT_CLIENT), SCOPE)
    before = tree(root)

    run = walk(root, scope=SCOPE)
    run.page()
    client_here.write_bytes(
        (json.dumps({"mcpServers": {key: {"command": "theirs"}}}, indent=2)
         + "\n").encode("utf-8")
    )
    taken = client_here.read_bytes()

    status, again, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    _, said, details = card_of(again)
    assert said == setup_web.CONNECT_REFUSED.format(label="Claude Desktop")
    assert client_here.as_posix() in details
    assert 'value="mount"' not in again
    assert tree(root) == before, "the refusal set something up"
    assert client_here.read_bytes() == taken
    assert run.stop() == report.REFUSED


def test_a_mounted_project_can_still_register_on_its_own(
    tmp_path, walk, fixed_day, client_here,
):
    """PLAN-0027 phase 2's done-condition, from `radialrig setup`: a set-up
    project with every skill and no connection is offered Connect, as
    today, and the one press does only the connection."""
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(root, name)

    run = walk(root, scope=SCOPE)
    page = run.page()
    assert setup_web.ALREADY_OFFERS in text_of(page)
    assert ('<button class="primary" type="submit" name="action" value="skills">'
            "Connect Claude Desktop</button>") in page
    assert '<input type="hidden" name="connect" value="claude-desktop">' in page
    # The command it runs is shown with its switch, whatever the boxes.
    assert shown_commands(page) == [f"radialrig connect {cn.DEFAULT_CLIENT} {root}"]
    assert run.running

    status, done, _ = run.post({**hidden_fields(page), "action": "skills"})
    assert status == 200
    assert result_of(done)[:3] == (
        setup_web.RESULT_DONE,
        "Connected Claude Desktop. Fully quit and reopen Claude Desktop to use the "
        "connection.", "")
    assert ran_commands(done) == [f"radialrig connect {cn.DEFAULT_CLIENT} {root}"]
    assert run.wait() == report.OK
    assert cn.is_connected(root)

    # And once it is connected the offer is gone: the page says there is
    # nothing left to add from here.
    second = walk(root, scope=SCOPE)
    after = second.page()
    assert setup_web.ALREADY_COMPLETE in text_of(after)
    assert "Connect Claude Desktop" not in after
    assert second.wait() == report.NOOP


def test_a_failed_registration_never_claims_the_project_is_registered(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    """The connection was the whole press, and it failed after the
    offer. The line says so, the engine's sentence is behind Details, and
    it claims nothing it cannot show."""
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(root, name)
    detail = "the settings file could not be opened"

    run = walk(root, scope=SCOPE)
    assert ">Connect Claude Desktop</button>" in run.page()

    def fails(*args, **kwargs):
        raise cn.ConnectError(detail)

    monkeypatch.setattr(cn, "connect", fails)
    status, done, _ = run.post({"token": run.session.token, "action": "skills",
                                "connect": cn.DEFAULT_CLIENT, **CHOSEN})
    assert status == 200
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        setup_web.RESULT_FAILED, "Couldn't connect Claude Desktop. Nothing was changed.")
    assert detail in details
    assert ran_commands(done) == []
    assert run.wait() == report.ERROR
    assert not cn.is_connected(root)
    assert not client_here.exists()


def test_an_unticked_connect_box_writes_no_entry(tmp_path, walk, fixed_day, client_here):
    root = _make_project(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()
    # Issue #62: the box starts unticked, so the page's own form, posted
    # as drawn with only its hidden fields, carries no connect value and
    # nothing connects.
    assert re.search(r'name="connect"[^>]*>', review), "no offer drawn"
    assert not re.search(r'name="connect"[^>]*checked', review), "the box starts ticked"
    status, mounted, _ = run.post({**hidden_fields(review), "action": "mount"})
    assert status == 200
    assert run.wait() == report.OK
    assert not cn.is_connected(root)
    assert not client_here.exists()
    assert result_of(mounted)[:2] == (
        setup_web.RESULT_DONE, f"Set up RadialRig in {root.name}.")
    assert not [line for line in ran_commands(mounted)
                if line.startswith("radialrig connect")]


# --- H: the terminal it prints ----------------------------------------

def test_the_command_prints_the_url_it_opened(tmp_path, walk):
    run = walk(_make_project(tmp_path / "host"))
    printed = run.stream.getvalue()
    assert run.session.url in printed
    assert f"http://{setup_web.HOST}:{run.session.port}" in printed
    assert setup_web.LOCAL_ONLY in printed
    assert run.session.port != 0

    # Bound to the loopback address only.
    with socket.socket() as probe:
        probe.settimeout(2)
        assert probe.connect_ex((setup_web.HOST, run.session.port)) == 0


# --- PLAN-0017 phase 4: the page keeps the reader's choices and offers ---
# --- only safe lines ------------------------------------------------------

HIDDEN = re.compile(r'<input type="hidden" name="([^"]+)" value="([^"]*)">')


def hidden_fields(page: str) -> dict:
    """What a form on this page posts without the reader touching it."""
    return {name: html.unescape(value) for name, value in HIDDEN.findall(page)}


def render_id(page: str) -> str:
    """The id the document root carries, which the close beacon posts back."""
    found = re.search(r'<html\b[^>]*\sdata-render="(\d+)"', page)
    assert found is not None, "the document carries no render id"
    return found.group(1)


# The characters no one quoting rule makes safe in both shells the page
# targets (setup-web-04): bare or in double quotes, cmd.exe or bash reads
# each as something other than the slot it sits in. A control character
# ends or splits a line the same way.
UNSAFE = tuple('"`$%^&|;<>!\'*?()') + ("\t", "\n", "\x1b")


def test_a_refusal_and_a_check_again_keep_every_tick_and_the_connect_box(
    tmp_path, walk, client_here,
):
    """setup-web-03: the boxes a reader ticked survive a refusal.

    The card's form carries no box and no marker, so the survey it posts
    says nothing about the boxes, and what the reader ticked before the
    setup was refused is what the screen shows after Check again.
    """
    root = _make_project(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()
    assert 'name="connect"' in review, "no offer to keep"
    ticked = boxes(SKILL, agents=(SECOND,))
    # The rulebook appears after the screen was drawn, so the press is
    # what meets it.
    (root / "docs").mkdir()
    (root / "docs" / "spec.md").write_bytes(b"this project's own rulebook\n")

    # The press carries what the ready form carries, plus the ticks.
    status, refused, _ = run.post({
        **hidden_fields(review), "action": "mount", "scope": SCOPE,
        "owner": mnt.DEFAULT_OWNER, "skill": ticked, "connect": cn.DEFAULT_CLIENT,
    })
    assert status == 200
    assert card_of(refused)[1] == setup_web.BLOCKED_ONE
    assert run.session.skills == ((SECOND.name, SKILL),)
    assert run.session.connect is True
    # The card's form carries no box: nothing on it can untick one.
    assert 'name="skill"' not in refused
    assert 'name="connect"' not in refused
    assert f'name="{setup_web.CHOICES_FIELD}"' not in refused

    (root / "docs" / "spec.md").unlink()
    status, again, _ = run.post({**hidden_fields(refused), "action": "survey"})
    assert status == 200
    assert ready(root) in again
    assert run.session.skills == ((SECOND.name, SKILL),)
    assert run.session.connect is True
    checked = [value for value, _, on in grid_boxes(again) if on]
    assert checked == ticked
    assert re.search(r'name="connect"[^>]*checked', again), "the connect box was cleared"
    assert run.running


def test_a_slot_no_quoting_rule_makes_safe_offers_no_copy_line(tmp_path, walk):
    """setup-web-04: a path or key carrying an unsafe character names no line.

    The finished page still says what it did; it offers no line a reader
    could paste that would run something they did not type.
    """
    for ch in UNSAFE:
        for slot in (rf"C:\dev\proj{ch}x", f"radialrig-foo{ch}calc"):
            session = setup_web.Session(Path(slot), SCOPE, mnt.DEFAULT_OWNER)
            session.skills = ((SECOND.name, SKILL),)
            for kind in (setup_web.MOUNTED, setup_web.SKILLED):
                state = setup_web.PageState(
                    kind=kind, outcome=report.OK, terminal=True,
                    added=((SECOND.name, SKILL),))
                assert setup_web.ran_commands(session, state) == (), repr(slot)
            review = setup_web.PageState(
                kind=setup_web.REVIEW, outcome=report.NOOP, terminal=False)
            assert setup_web.press_commands(session, review, ()) == (), repr(slot)
            with pytest.raises(setup_web.Unquotable):
                setup_web.quoted(slot)

    # Over HTTP, on a folder a reader can have: `&` is legal on both
    # systems and runs a second program in cmd.exe.
    root = _make_project(tmp_path / "proj&calc")
    run = walk(root, scope=SCOPE)
    review = run.page()
    assert shown_commands(review) == []
    status, mounted, _ = run.post({
        **hidden_fields(review), "action": "mount", "scope": SCOPE,
        "owner": mnt.DEFAULT_OWNER, "skill": boxes(SKILL, agents=(SECOND,)),
    })
    assert status == 200
    assert result_of(mounted)[:2] == (
        setup_web.RESULT_DONE,
        f"Set up RadialRig in {root.name} and added 1 skill. Start a new agent "
        "session to pick it up."), "the page stopped saying what it did"
    assert changed_of(mounted), "the page stopped listing what it changed"
    copied = [html.unescape(text) for text in re.findall(r'data-copy="([^"]*)"', mounted)]
    assert copied, "the slot-free lines went with it"
    for line in copied:
        assert str(root) not in line, line
        assert "skill add" not in line, line
    assert run.wait() == report.OK


def test_a_slot_outside_the_bare_set_is_double_quoted():
    """setup-web-04: the one rule for the characters a shell does read.

    Letters, digits and `_ . / \\ : ~ -` render bare, so every line a
    reader has copied before is the line it was; anything else safe goes
    in double quotes, which both shells take.
    """
    bare = r"C:\Users\p_1\proj.2~x-y:/z"
    assert setup_web.quoted(bare) == bare
    assert setup_web.quoted("") == ""
    for slot in (r"C:\Users\First Last\p", "a,b", "k@v", "a+b", "x=y",
                 "[x]", "{x}", "#x"):
        assert setup_web.quoted(slot) == f'"{slot}"', repr(slot)


def test_a_reload_of_the_review_page_leaves_the_server_standing_and_the_next_mount_lands(
    tmp_path, walk, fixed_day,
):
    """setup-web-05: F5 on the ready screen is not the page closing.

    The browser serves the reload's GET first and fires the old
    document's close after it, so the close carries the id of a document
    this session has already replaced, and the server drops it. The
    reader then presses Set up project on the reloaded page and it lands.
    """
    root = _make_project(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    first = render_id(run.page())
    reloaded = render_id(run.page())          # F5: the document that replaced it
    assert int(reloaded) == int(first) + 1

    status, body, _ = run.post({
        "token": run.session.token, "action": "close",
        setup_web.RENDER_FIELD: first,
    })
    assert (status, body) == (200, "")
    assert run.running, "the reload's close stopped the server"

    status, mounted, _ = run.post(mount_form(run))
    assert status == 200
    assert result_of(mounted)[0] == setup_web.RESULT_DONE
    assert run.wait() == report.OK
    assert mnt.is_mounted(root)


# --- PLAN-0017 phase 5: a skills record the kit cannot read ------------

def test_a_utf16_skills_record_stops_the_page_in_words(tmp_path, walk):
    # skills-roster-04, the page half: the record in another encoding is
    # the same sentence `skill list` prints, behind the card that says
    # setup stopped and nothing was changed, on the survey and on the
    # press alike; the 500 boundary is not reached and nothing is written.
    record_rel = sk.default_harness().record

    def host(name: str) -> Path:
        root = _make_project(tmp_path / name)
        sk.add(root, SKILL)
        record = root / record_rel
        record.write_bytes(record.read_text(encoding="utf-8").encode("utf-16"))
        return root

    surveyed = host("surveyed")
    before = tree(surveyed)
    run = walk(surveyed, scope=SCOPE)
    status, page, _ = run.get(f"/setup?s={run.session.token}")
    assert status == 200
    assert "could not answer" not in page
    _, said, details = card_of(page)
    assert said == setup_web.SETUP_FAILED
    assert f"{record_rel} does not parse as JSON" in details
    assert tree(surveyed) == before

    pressed = host("pressed")
    before = tree(pressed)
    other = walk(pressed, scope=SCOPE)
    status, page, _ = other.post(mount_form(other, skill=boxes(SKILL)))
    assert status == 200
    assert "could not answer" not in page
    _, said, details = card_of(page)
    assert said == setup_web.SETUP_FAILED
    assert f"{record_rel} does not parse as JSON" in details
    assert not mnt.is_mounted(pressed)
    assert tree(pressed) == before


# --- PLAN-0017 phase 7: the guards nobody has seen fail ----------------

def test_a_refused_post_reads_its_body_before_the_refusal(tmp_path):
    """tests-02: a cross-origin post carries its body all the way in.

    The origin guard used to answer before reading a byte, and the close
    behind that answer met the unread body as a reset on Windows
    (WinError 10053), which can discard the 403 already sitting in the
    client's receive buffer, so a guard test failed for a break that was
    not there. The handler is driven directly here, its socket a buffer,
    so the read position at the moment the answer goes out is exact.
    """
    session = setup_web.Session(
        _make_project(tmp_path / "host"), SCOPE, mnt.DEFAULT_OWNER)
    session.port = 4321
    body = urllib.parse.urlencode(
        {"token": session.token, "action": "mount", "scope": SCOPE}).encode("ascii")
    incoming = io.BytesIO(body)
    read_when_answered: list[int] = []

    class Answer(io.BytesIO):
        def write(self, data):
            if not read_when_answered:
                read_when_answered.append(incoming.tell())
            return super().write(data)

    headers = http.client.HTTPMessage()
    headers["Host"] = f"{setup_web.HOST}:{session.port}"
    headers["Origin"] = "http://evil.example"
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    headers["Content-Length"] = str(len(body))
    handler = setup_web.SetupHandler.__new__(setup_web.SetupHandler)
    handler.session = session
    handler.headers = headers
    handler.path = setup_web.PAGE_PATH
    handler.command = "POST"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"POST {setup_web.PAGE_PATH} HTTP/1.1"
    handler.client_address = (setup_web.HOST, 0)
    handler.rfile = incoming
    handler.wfile = Answer()

    handler._post()

    answered = handler.wfile.getvalue()
    assert answered.split(b"\r\n", 1)[0].endswith(b" 403 Forbidden"), answered
    assert setup_web.worded(setup_web.REFUSE_ORIGIN).encode("utf-8") in answered
    assert read_when_answered == [len(body)]
    assert incoming.read() == b"", "bytes left unread for the close to reset"


def test_a_registration_that_cannot_be_planned_is_reported_by_both_presses(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    """tests-06: the offer was on the page and could not be planned at
    press time. The setup press and a set-up project's press both land
    what they can and both carry the engine's sentence, so the two
    surfaces agree and the connection the page offered does not vanish
    unsaid."""
    detail = "the settings file could not be opened"
    armed = [False]
    real = cn.connect_plan

    def refuse(*args, **kwargs):
        if armed[0]:
            raise cn.ConnectError(detail)
        return real(*args, **kwargs)

    monkeypatch.setattr(cn, "connect_plan", refuse)

    # The setup press: the setup lands, the connection does not run, and
    # the line says so with the engine's own words behind Details.
    pressed = _make_project(tmp_path / "pressed")
    run = walk(pressed, scope=SCOPE)
    assert 'name="connect"' in run.page(), "the offer is not on the review page"
    armed[0] = True
    status, page, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    armed[0] = False
    assert status == 200
    tone, sentence, _, details = result_of(page)
    assert (tone, sentence) == (
        setup_web.RESULT_PARTLY,
        f"Set up RadialRig in {pressed.name}, but couldn't connect Claude Desktop. "
        "Its settings weren't changed. Check Details for what stopped it.")
    assert detail in details
    assert mnt.is_mounted(pressed)
    assert not cn.is_connected(pressed)
    assert run.wait() == report.OK

    # The press on a project already set up: the same sentence, under the
    # line that says the connection didn't run.
    mounted = _make_project(tmp_path / "mounted")
    mnt.mount(mounted, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(mounted, name)
    other = walk(mounted, scope=SCOPE)
    assert ">Connect Claude Desktop</button>" in other.page()
    armed[0] = True
    status, done, _ = other.post({"token": other.session.token, "action": "skills",
                                  "connect": cn.DEFAULT_CLIENT, **CHOSEN})
    armed[0] = False
    assert status == 200
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        setup_web.RESULT_FAILED, "Couldn't connect Claude Desktop. Nothing was changed.")
    assert detail in details
    assert not cn.is_connected(mounted)
    assert other.wait() == report.ERROR


# --- BUG-0018: an open client can undo the connect -----------------------

def test_an_open_client_is_named_on_the_review_and_the_done_page(
    tmp_path, walk, fixed_day, client_here,
):
    """BUG-0018 on `radialrig setup`: the before-press sentence under the
    Connect box while the client is open, and the after-press sentence on
    the line of a setup that connected while it was. This page has no
    Repair, so its sentence says where one is."""
    from radialrig.tests.test_connect import plant_lock

    client = cn.client_for(cn.DEFAULT_CLIENT)
    plant_lock(client_here)
    root = _host(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()
    assert (f'<p class="warn">{report.client_running_before(client)}</p>'
            in review.split('<div class="offer">', 1)[1].split("</div>", 1)[0])
    assert report.client_running_before(client) == (
        "Claude Desktop is running. Fully quit it first, or it may undo this change.")

    status, mounted, _ = run.post(mount_form(run, connect=client.name))
    assert status == 200
    assert run.wait() == report.OK, "a warning, not a refusal"
    assert cn.is_connected(root)
    tone, _, warning, _ = result_of(mounted)
    assert tone == setup_web.RESULT_DONE
    assert warning == (
        "Claude Desktop is running and may undo this change. Fully quit it and "
        "reopen it; if the connection is gone, open this project in RadialRig and "
        "press Repair.")
    assert warning == setup_web.RUNNING_AFTER_SETUP.format(label=client.label)


def test_a_setup_that_left_the_client_alone_says_nothing_about_it_running(
    tmp_path, walk, fixed_day, client_here,
):
    """The after-press sentence is for a press that connected: a setup
    with the box unticked leaves the client's settings alone, so its line
    carries no warning even while the client is open."""
    from radialrig.tests.test_connect import plant_lock

    plant_lock(client_here)
    root = _make_project(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    run.page()
    status, mounted, _ = run.post(mount_form(run))
    assert status == 200
    assert result_of(mounted)[:3] == (
        setup_web.RESULT_DONE, f"Set up RadialRig in {root.name}.", "")
    assert run.wait() == report.OK


def test_a_setup_with_skills_and_the_connection_ticked_answers_in_one_line(
    tmp_path, walk, fixed_day, client_here,
):
    """Prescription 3 from `radialrig setup`: the result shows on the same
    screen, as one line saying all three landed, with every command the
    press ran behind Details, and the word that the server has stopped."""
    root = _make_project(tmp_path / "pit-strategy")
    run = walk(root, scope=SCOPE)
    run.page()
    status, done, _ = run.post(mount_form(
        run, skill=boxes(*sk.catalog_names()), connect=cn.DEFAULT_CLIENT))
    assert status == 200
    assert result_of(done)[:3] == (
        setup_web.RESULT_DONE,
        f"Set up RadialRig in {root.name}, added {len(sk.catalog_names())} skills, "
        "and connected Claude Desktop. Start a new agent session for the skills, and "
        "fully quit and reopen Claude Desktop to use the connection.", "")
    assert ran_commands(done) == [
        f"radialrig mount {root} --scope {SCOPE}",
        *(setup_web.skill_add_line(harness.name, name, root)
          for harness in AGENTS for name in sk.catalog_names()),
        f"radialrig connect {cn.DEFAULT_CLIENT} {root}"]
    assert closing_of(done) == setup_web.STOPPED
    assert '<div class="screen setup done">' in done
    assert run.wait() == report.OK
    assert cn.is_connected(root)


# --- PLAN-0027 phase 2, review round 1 -----------------------------------
# One test per finding the review raised and the triage kept. F1's is
# `test_a_connect_refusal_withdraws_the_offer_and_keeps_the_mount` above.

def _take_the_name(config: Path, scope: str) -> bytes:
    """Another server's entry under the key this project's name gives."""
    key = cn.entry_key(cn.client_for(cn.DEFAULT_CLIENT), scope)
    config.write_bytes(
        (json.dumps({"mcpServers": {key: {"command": "theirs"}}}, indent=2)
         + "\n").encode("utf-8"))
    return config.read_bytes()


def test_a_set_up_project_whose_name_is_taken_is_told_what_its_screen_holds(
    tmp_path, walk, fixed_day, client_here,
):
    """Review F3. A set-up folder's screen has no Project name and no
    Refresh, so the sentence in the Connect box's place sends no one to
    them. It sends the reader to Details for what's in the way, and
    Details is there, with skills left to add and without."""
    client = cn.client_for(cn.DEFAULT_CLIENT)
    owed = getattr(setup_web, "CONNECT_NAME_TAKEN_SET_UP", None)
    blockers = f"{setup_web.CONNECT_BLOCKERS_HEAD.format(label=client.label)} "

    # Skills left to add. The reviewer's way in: Connect posted with Add
    # to this project, refused on the card, then Check again.
    root = _make_project(tmp_path / "missing-skills")
    mnt.mount(root, scope=SCOPE)
    taken = _take_the_name(client_here, SCOPE)
    run = walk(root, scope=SCOPE)
    run.page()
    status, refused, _ = run.post({"token": run.session.token, "action": "skills",
                                   "skill": boxes(SKILL), "connect": cn.DEFAULT_CLIENT,
                                   **CHOSEN})
    assert status == 200
    assert card_of(refused)[1] == setup_web.CONNECT_REFUSED.format(label=client.label)
    status, again, _ = run.post({"token": run.session.token, "action": "survey"})
    assert status == 200
    said = text_of(again)
    assert 'id="scope"' not in again and 'value="survey"' not in again
    assert "Project name" not in said and "Refresh" not in said, (
        "the sentence names controls this screen doesn't have")
    assert owed is not None and owed.format(label=client.label) in said
    assert blockers + client_here.as_posix() in html.unescape(said)
    assert 'value="skills"' in again, "the skills it can still add went with the box"
    run.stop()

    # Every skill there already: nothing to add, and Details still holds
    # what's in the way. The screen as first drawn says it (review F1);
    # before that fix, a post carrying the box brought it about.
    full = _make_project(tmp_path / "every-skill")
    mnt.mount(full, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(full, name)
    other = walk(full, scope=SCOPE)
    page = other.page()
    said = text_of(page)
    assert "Project name" not in said and "Refresh" not in said
    assert '<details class="settings">' in main_of(page), (
        "the sentence sends the reader to Details and there is none")
    assert blockers + client_here.as_posix() in html.unescape(said)
    assert client_here.read_bytes() == taken


def test_a_set_up_project_with_nothing_it_can_add_stops_its_server(
    tmp_path, walk, fixed_day,
):
    """Review F4. A regular file stands where each agent's skills folder
    would go, and no client is on this computer: the screen offers no
    press, so it says the closing line and the server stops, as the other
    finished screens do."""
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    for harness in sk.HARNESSES.values():
        blocked = root / harness.skills
        blocked.parent.mkdir(parents=True, exist_ok=True)
        blocked.write_bytes(b"not a folder\n")
    before = tree(root)
    run = walk(root, scope=SCOPE)
    page = run.page()
    assert grid_boxes(page) == [] and 'value="skills"' not in page
    assert setup_web.ALREADY_COMPLETE in text_of(page)
    assert closing_of(page) == setup_web.CLOSED, (
        "nothing can be added and the page doesn't say its server stopped")
    assert run.wait() == report.NOOP
    assert tree(root) == before


def test_a_connection_that_stopped_partway_while_the_client_runs_carries_the_warning(
    tmp_path, walk, fixed_day, monkeypatch,
):
    """Review F2. The before-and-after rule covers every press that writes
    the client's settings. A connection that stopped partway and left one
    of the client's settings files changed, while the client looks open,
    carries the after-press sentence. One that left only the project's
    own record changed left the client's settings as they were, and says
    nothing about it."""
    from radialrig.tests.test_connect import plant_lock

    client = cn.client_for(cn.DEFAULT_CLIENT)
    real_write = Path.write_bytes
    visible, hidden = two_copies(tmp_path, monkeypatch)
    plant_lock(visible)
    plant_lock(hidden)
    before = {visible: visible.read_bytes(), hidden: hidden.read_bytes()}
    seen = refusing_client_writes(monkeypatch, after=1)
    root = _make_project(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    assert {p for p in before if p.read_bytes() != before[p]} == {seen[0]}
    tone, sentence, warning, _ = result_of(page)
    assert (tone, sentence) == (
        setup_web.RESULT_PARTLY,
        f"Set up RadialRig in {root.name}, but couldn't finish connecting Claude "
        "Desktop. Some of its settings may be left changed. Check Details for the "
        "list.")
    assert warning == setup_web.RUNNING_AFTER_SETUP.format(label=client.label), (
        "the client's settings were left changed while it runs, with no warning")
    assert run.wait() == report.PARTIAL
    monkeypatch.setattr(Path, "write_bytes", real_write)

    # The project's own record left, the client's settings put back.
    visible, hidden = two_copies(tmp_path / "record-only", monkeypatch)
    plant_lock(visible)
    record_only = _make_project(tmp_path / "record-only" / "host")
    record = cn.record_path(record_only)

    def stopped_at_the_record(*args, **kwargs):
        raise cn.WriteStopped(record, "Permission denied", (record,))

    monkeypatch.setattr(cn, "connect", stopped_at_the_record)
    other = walk(record_only, scope=SCOPE)
    other.page()
    status, page, _ = other.post(mount_form(other, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    assert result_of(page)[0] == setup_web.RESULT_PARTLY
    assert result_of(page)[2] == "", "a warning for a change the client's settings never had"
    assert other.wait() == report.PARTIAL


def test_the_stopped_card_sets_the_file_as_code_and_breaks_its_path_after_separators(
    tmp_path, walk,
):
    """PLAN-0027 phase 5, C2 and C3. Mockup 04-add-conflict sets
    `.mcp.json` as code in the card's sentence. The folder's path is drawn
    in pieces that each end at a separator, with a break offered after
    each, so a name such as lap-notes never wraps into "lap-" and "notes";
    the pieces read back as the path, whole."""
    root = _make_project(tmp_path / "lap-notes")
    (root / mnt.MCP_REL).write_bytes(b"not json at all\n")
    card = main_of(walk(root).page()).split('<section class="card"', 1)[1]
    assert ("<p>RadialRig can't add its entries to <code>.mcp.json</code>, "
            "because it isn't valid JSON") in card

    drawn = re.search(r'<p class="path">(.*?)</p>', card).group(1)
    pieces = re.findall(r'<span class="seg">([^<]*)</span>', drawn)
    assert "".join(html.unescape(piece) for piece in pieces) == str(root)
    assert pieces[-1] == "lap-notes"
    assert all(piece.endswith(("\\", "/")) for piece in pieces[:-1])
    assert drawn == "<wbr>".join(f'<span class="seg">{piece}</span>' for piece in pieces)
    css = (packaged_web() / "page.css").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert (".screen .card .path .seg { display: inline-block; max-width: 100%; "
            "overflow-wrap: anywhere; }") in css


def test_add_a_project_sets_its_folder_under_the_heading_and_its_note_to_a_reading_width():
    """PLAN-0027 phase 5, C1 and C4: mockup 04-add-project puts the folder
    on a line of its own under the heading, and holds the Connect note to
    a reading width."""
    css = (packaged_web() / "page.css").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert (".screen.setup .project-name { flex-direction: column; "
            "align-items: flex-start; gap: 2px; }") in css
    assert ".screen .offer .hint { margin-left: 24px; max-width: 62ch;" in css


def test_a_mcp_json_laid_out_wrong_gets_a_true_reason_in_details(tmp_path, walk):
    """Review F6. `{"mcpServers": []}` is valid JSON whose servers are not
    laid out as settings. The card's sentence already allows for that;
    Details gives the file the page's own reason, true of every way the
    file can stop a setup. The command line's reason stays its own."""
    cli_reason = report.conflict_reason(mnt.MCP_REL)
    root = _make_project(tmp_path / "host")
    (root / mnt.MCP_REL).write_bytes(b'{"mcpServers": []}\n')
    run = walk(root, scope=SCOPE)
    page = run.page()
    _, said, details = card_of(page)
    assert said == setup_web.BLOCKED_SETTINGS.format(file=mnt.MCP_REL)
    assert "does not parse as a JSON object" not in details, (
        "Details says a file that parses does not parse")
    reason = getattr(setup_web, "MCP_REASON", None)
    assert reason and reason in details
    assert cli_reason == (
        f"this project's {mnt.MCP_REL} does not parse as a JSON object")


def _refuse_once(monkeypatch, inside: Path, *, at: int) -> list[tuple[str, Path]]:
    """The write numbered `at` (from 1) to a file under `inside` refuses,
    once; every other write lands, the restores included. Returns each
    such write in order, marked as it went."""
    real = Path.write_bytes
    seen: list[tuple[str, Path]] = []

    def once(self, data):
        if inside in self.parents:
            if len(seen) + 1 == at and not any(m == "refused" for m, _ in seen):
                seen.append(("refused", self))
                raise PermissionError(13, "Permission denied", str(self))
            seen.append(("wrote", self))
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", once)
    return seen


# Review F7. A stop whose writer put back every file it wrote left
# nothing changed, and the line says so; but a file was written and
# removed, so neither the line nor its Details says nothing was written.
# The same for the setup, for its skills, and for its connection.

def test_a_setup_that_put_back_what_it_wrote_never_says_nothing_was_written(
    tmp_path, walk, fixed_day, monkeypatch,
):
    """The setup's first file lands, its second refuses, and the first is
    taken out again."""
    root = _make_project(tmp_path / "host")
    before = tree(root)
    seen = _refuse_once(monkeypatch, root, at=2)
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post(mount_form(run))
    assert status == 200
    assert [mark for mark, _ in seen[:2]] == ["wrote", "refused"]
    assert tree(root) == before
    _, said, details = card_of(page)
    assert said == setup_web.SETUP_FAILED
    assert "Permission denied" in details
    assert "nothing was written" not in text_of(page).lower(), (
        "a file was written and removed, and the card says nothing was written")
    assert "nothing was changed" in details
    assert run.wait() == report.ERROR


def test_skills_that_were_put_back_never_say_nothing_was_written(
    tmp_path, walk, fixed_day, monkeypatch,
):
    """After the setup landed, the first skill file lands, the second
    refuses, and the first is taken out again."""
    skilled = _make_project(tmp_path / "skilled")
    seen = _refuse_once(monkeypatch, skilled / SKILL_DIR, at=2)
    run = walk(skilled, scope=SCOPE)
    run.page()
    status, page, _ = run.post(mount_form(run, skill=boxes(SKILL, agents=(FIRST,))))
    assert status == 200
    assert [mark for mark, _ in seen[:2]] == ["wrote", "refused"]
    assert not [p for p in (skilled / SKILL_DIR).rglob("*") if p.is_file()]
    _, sentence, _, details = result_of(page)
    assert sentence == setup_web.SET_UP_NO_SKILLS.format(project=skilled.name)
    assert "Permission denied" in details
    assert "nothing was written" not in details.lower(), (
        "a skill file was written and removed, and Details says nothing was written")
    assert run.wait() == report.PARTIAL


def test_a_connection_that_was_put_back_never_says_nothing_was_written(
    tmp_path, walk, fixed_day, monkeypatch,
):
    """After the setup landed, the first of the client's two settings
    files lands, the second refuses, and the first is put back as it
    was."""
    visible, hidden = two_copies(tmp_path / "two", monkeypatch)
    pristine = {visible: visible.read_bytes(), hidden: hidden.read_bytes()}
    seen = _refuse_once(monkeypatch, tmp_path / "two", at=2)
    connected = _make_project(tmp_path / "connected")
    run = walk(connected, scope=SCOPE)
    run.page()
    status, page, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    assert [mark for mark, _ in seen[:2]] == ["wrote", "refused"]
    assert {p for p in pristine if p.read_bytes() != pristine[p]} == set()
    _, sentence, _, details = result_of(page)
    assert sentence == setup_web.SET_UP_NO_CONNECT.format(
        project=connected.name, label="Claude Desktop")
    assert "Permission denied" in details
    assert "nothing was written" not in details.lower(), (
        "a settings file was written and put back, and Details says nothing was written")
    assert not cn.is_connected(connected)
    assert run.wait() == report.ERROR
