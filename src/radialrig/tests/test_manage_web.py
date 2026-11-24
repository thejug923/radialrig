"""Tests for `radialrig manage` — PLAN-0008 Phase 4.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_manage_web.py -q

Two oracles run here. The first is `radialrig projects`: the page lists
the machine the command line lists, row for row and sentence for
sentence, because both read the same roster through the same builders.
The second is the URL line: Phase 5's app spawns this command, reads one
line off a pipe and drives the page behind it, so that line is pinned as
bytes rather than as text.

Everything drives real HTTP against a real server on a real port.

Nothing here may reach this machine's own profile. `APPDATA` and
`LOCALAPPDATA` point inside tmp_path for every test in the suite
(conftest.py), and the fixtures below narrow `APPDATA` to one fake client
profile of this file's own — so the roster file and the client's settings
file both stand under tmp_path. The two variables are never deleted: the
roster path is built from `APPDATA`, and a test running without it would
assert against a listing that can never hold anything.
"""

from __future__ import annotations

import dataclasses
import html
import http.client
import io
import json
import os
import re
import shutil
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

from radialrig import cli
from radialrig import activity_log as al
from radialrig import connect as cn
from radialrig import manage_web
from radialrig import mount as mnt
from radialrig import report
from radialrig import roster as ro
from radialrig import setup_web
from radialrig import skills as sk
from radialrig import update
from radialrig.paths import ROOT_ENV, packaged_web
from radialrig.tests.test_connect import (  # noqa: F401 - helpers, by name
    CONFIG_HOSTS,
    STORE_VAR,
    command,
    fake_client,
    roaming_config,
    run,
    store_config,
)
from radialrig.tests.test_mount import _make_project

TIMEOUT = 20
PATH = manage_web.MANAGE_PATH

CLIENT = cn.DEFAULT_CLIENT
PREFIX = cn.CLIENTS[CLIENT].key_prefix

# One packaged skill, restated rather than read from the catalog the page
# is checked against.
SKILL = "session-handoff"
SKILL_DIR = ".claude/skills/session-handoff"

# A press's rows on today's plan and result pages: the chip, the path,
# the sentence. Projects and one project draw no such table (PLAN-0027
# phase 1); `ROSTER_ROW` below reads a row of Projects.
ROW = re.compile(
    r'<tr><td class="verb"><span class="chip[^"]*">([a-z]+)</span></td>'
    r'<td class="path">([^<]*)</td><td class="why">([^<]*)</td>'
    r'(?:<td class="act">.*?</td>)?</tr>'
)
# A row of Projects (PLAN-0027 phase 1 prescription 4): the folder's name
# over its parent, one status as a mark and a word, one line of facts,
# and one button where something needs doing.
ROSTER_ROW = re.compile(
    r'<li class="roster-row( gone)?"(?: id="(row-[0-9a-f]{12})")?>(.*?)</li>', re.S)
ROW_NAME = re.compile(r'class="row-name"[^>]*>([^<]*)<')
ROW_PARENT = re.compile(r'<span class="parent">([^<]*)</span>')
# A status, as the look draws it: the mark (hidden from a reader by ear)
# and the word or sentence beside it.
STATE = re.compile(
    r'<span class="state"><span class="symbol ([a-z]+)" aria-hidden="true">'
    r'([^<]*)</span><span>([^<]*)</span></span>')
ROW_ABOUT = re.compile(r'<div class="about"><span>([^<]*)</span>')
ROW_FIX = re.compile(r'<div class="fix">(.*?)</div>', re.S)
# A submit button's words, after the glyph a cell or a back link leads
# with.
BUTTON = re.compile(
    r'<button([^>]*) type="submit">(?:<span aria-hidden="true">[^<]*</span>)?([^<]*)</button>')
ANCHOR = re.compile(r'<a\b[^>]*\bhref="([^"]*)"')
TAGS = re.compile(r"<[^>]+>")
# A folder path drawn in pieces that wrap only after a separator
# (`setup_web.folder_path`, PLAN-0027 phase 5 C3) reads as one path.
PIECES = re.compile(r'<span class="seg">([^<]*)</span>(?:<wbr>)?')
GET_FORM = re.compile(
    r'<form class="nav" method="get" action="([^"]+)">(.*?)</form>', re.S)
HIDDEN = re.compile(r'<input type="hidden" name="([^"]+)" value="([^"]*)">')
EXTERNAL_REF = re.compile(r'(?:href|src)="(?!data:|/manage)[a-z]+:', re.I)
EXTERNAL_CSS = re.compile(r'url\(\s*["\']?(?!data:)[a-z]+:', re.I)
# The render id on the document root — D8's whole apparatus, as it
# reaches a browser.
RENDER = re.compile(r'<html\b[^>]*\s' + manage_web.RENDER_ATTR + r'="(\d+)"')


# --- fixtures ---------------------------------------------------------

@pytest.fixture()
def env(tmp_path, monkeypatch) -> dict:
    """One fake client profile, in os.environ where both surfaces read it.

    `APPDATA` carries the client's settings file and the roster, which is
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


class Manage:
    """One `radialrig manage` run, driven over HTTP from this process."""

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("open_browser", False)
        self.stream = kwargs.pop("stream", None) or io.StringIO()
        self.kwargs = kwargs
        self.session: manage_web.ManageSession | None = None
        self.code: int | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.code = manage_web.serve(
            on_ready=self._on_ready, stream=self.stream, **self.kwargs)

    def _on_ready(self, session) -> None:
        self.session = session
        self._ready.set()

    def start(self) -> "Manage":
        self._thread.start()
        assert self._ready.wait(TIMEOUT), "the server never reported ready"
        return self

    # --- lifecycle ---

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def wait(self) -> int:
        self._thread.join(TIMEOUT)
        assert not self._thread.is_alive(), "the server is still running"
        return self.code

    def stop(self) -> int:
        if self._thread.is_alive():
            self.session.finish(self.session.outcome)
        self._thread.join(TIMEOUT)
        return self.code

    # --- requests ---

    def request(self, method: str, path: str, *, body=None, headers=None):
        conn = http.client.HTTPConnection(
            manage_web.setup_web.HOST, self.session.port, timeout=TIMEOUT)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = response.read().decode("utf-8")
            return response.status, payload, dict(response.getheaders())
        finally:
            conn.close()

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def page(self, query: str = "") -> str:
        status, body, _ = self.get(f"{PATH}?s={self.session.token}{query}")
        assert status == 200, body
        return body

    def post(self, fields, *, headers=None):
        head = {"Content-Type": "application/x-www-form-urlencoded"}
        head.update(headers or {})
        return self.request(
            "POST", PATH, body=urllib.parse.urlencode(fields), headers=head)


@pytest.fixture()
def manage():
    made: list[Manage] = []

    def start(**kwargs) -> Manage:
        run = Manage(**kwargs).start()
        made.append(run)
        return run

    yield start
    for run in made:
        run.stop()


# --- helpers ----------------------------------------------------------

def rows(page: str) -> list[tuple[str, str, str]]:
    import html as _html

    return [
        tuple(_html.unescape(cell) for cell in match) for match in ROW.findall(page)
    ]


# --- the listing's rows (PLAN-0014 phase 1) ---------------------------

def roster_rows(page: str) -> list[tuple[str, str, str, str, str]]:
    """Every row of Projects, in the order drawn: the folder's name, its
    parent, the status word, the line of facts, and the words of the row's
    one button, or "" where it carries none."""
    out = []
    for _, _, inner in ROSTER_ROW.findall(main_of(page)):
        parent = ROW_PARENT.search(inner)
        fix = ROW_FIX.search(inner)
        button = BUTTON.search(fix.group(1)) if fix else None
        out.append(tuple(html.unescape(part) for part in (
            ROW_NAME.search(inner).group(1),
            parent.group(1) if parent else "",
            STATE.search(inner).group(3),
            ROW_ABOUT.search(inner).group(1),
            button.group(2) if button else "",
        )))
    return out


def roster_row(page: str, name: str) -> str:
    """One row's markup, found by the folder's name."""
    for _, _, inner in ROSTER_ROW.findall(main_of(page)):
        if html.unescape(ROW_NAME.search(inner).group(1)) == name:
            return inner
    raise AssertionError(f"no row for {name}")


def listed() -> tuple[ro.ProjectState, ...]:
    """The listing as the page reads it: each standing, mounted project
    knowing whether its update would write something."""
    return tuple(
        dataclasses.replace(s, behind=update.behind(Path(s.root)))
        if s.exists and s.mounted else s
        for s in ro.listing())


def facts_of(state: ro.ProjectState) -> str:
    """A row's line of facts, written out in `wording.md`'s words.

    Literal here rather than read off the module, so this file and not
    the page is the pin. The connection first, named only where a client
    is installed here or the project is connected to one, then the count
    of skills either agent has.
    """
    label = cn.CLIENTS[CLIENT].label
    count = len(state.skills)
    skills = "no skills" if count == 0 else "1 skill" if count == 1 else f"{count} skills"
    if state.connected == ro.CONNECTED_MISMATCH:
        return f"{label} link lost, {skills}"
    if state.connected == CLIENT:
        return f"{label} connected, {skills}"
    if not cn.installed():
        return skills[0].upper() + skills[1:]
    if count == 0:
        return "No connections or skills"
    return f"{label} not connected, {skills}"


STATUS_ORDER = ("Needs repair", "Update ready", "Not set up", "Working", "Folder gone")


def expected_row(state: ro.ProjectState, *, offered: bool = True) -> tuple[str, str, str, str, str]:
    """What Projects draws for one state. `offered` is whether Repair's
    guard offers its press on a lost link."""
    name, parent = Path(state.root).name, str(Path(state.root).parent)
    if not state.exists:
        return (name, parent, "Folder gone",
                "This folder isn't on this computer now, so it's off the project list.", "")
    if state.connected == ro.CONNECTED_MISMATCH:
        status, fix = "Needs repair", "Repair" if offered else ""
    elif state.behind:
        status, fix = "Update ready", "Update"
    elif not state.mounted:
        status, fix = "Not set up", "Set up"
    else:
        status, fix = "Working", ""
    return (name, parent, status, facts_of(state), fix)


def expected_listing(states) -> list[tuple[str, str, str, str, str]]:
    """What Projects draws for these states, in the order it draws them:
    the rows that need attention first, each group in the list's order."""
    return sorted((expected_row(s) for s in states),
                  key=lambda row: STATUS_ORDER.index(row[2]))


def visible(page: str) -> str:
    body = page.split("<main>", 1)[-1].split("</main>", 1)[0]
    return TAGS.sub(" ", PIECES.sub(r"\1", body))


def text_of(page: str) -> str:
    import html as _html

    return " ".join(_html.unescape(visible(page)).split())


def navigations(page: str, token: str) -> list[str]:
    """Every GET form on the page, as the URL its button would request.

    A fragment on the action is dropped before the query is appended: a
    browser keeps it on the address it lands on and never sends it
    (PLAN-0014 phase 2), and this client would.
    """
    out = []
    for action, inner in GET_FORM.findall(page):
        fields = HIDDEN.findall(inner)
        assert ("s", token) in fields, "a navigation carries no session token"
        out.append(f"{action.split('#', 1)[0]}?{urllib.parse.urlencode(fields)}")
    return out


def render_id(page: str) -> str:
    """The id the session issued for this document, off the document."""
    found = RENDER.search(page)
    assert found is not None, "this manage document carries no render id"
    assert page.count(f"{manage_web.RENDER_ATTR}=") == 1, "two render ids"
    return found.group(1)


def registered(capsys, project: Path, *, skill: bool = False) -> Path:
    """A mounted, registered project, built by the real verbs."""
    assert run(capsys, "mount", str(project))[0] == 0
    assert run(capsys, "connect", CLIENT, str(project))[0] == 0
    if skill:
        assert run(capsys, "skill", "add", SKILL, str(project))[0] == 0
    return project


def record_of(project: Path) -> dict:
    return json.loads(cn.record_path(project).read_text(encoding="utf-8"))


def drop_entry(config: Path, key: str) -> None:
    """What a user does inside the client: remove the entry, keep the file."""
    body = json.loads(config.read_text(encoding="utf-8"))
    del body["mcpServers"][key]
    config.write_text(json.dumps(body, indent=2), encoding="utf-8")


# --- the URL line -----------------------------------------------------

@pytest.mark.parametrize("kind", ["text", "bytes"])
@pytest.mark.parametrize("open_browser", [False, True])
def test_the_manage_url_line_is_pinned(manage, monkeypatch, kind, open_browser):
    """The Phase 5 contract: one line, first, and the URL is the rest of it.

    Driven twice over the same assertions. The text run is the ordinary
    one; the byte run is adversarial — a stream that would spell the line
    in cp1252 and end it with two bytes — and the underlying bytes are
    read directly, which is what proves the line went round the text
    layer rather than through it.
    """
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(manage_web.webbrowser, "open", fake_open)

    raw = io.BytesIO()
    stream = (io.StringIO() if kind == "text" else
              io.TextIOWrapper(raw, encoding="cp1252", newline="\r\n"))
    run_ = manage(stream=stream, open_browser=open_browser)

    def printed() -> str:
        if kind == "text":
            return stream.getvalue()
        return raw.getvalue().decode("cp1252")

    lines = printed().splitlines()
    named = [line for line in lines if line.startswith(manage_web.URL_PREFIX)]
    assert len(named) == 1, lines
    assert lines[0] == named[0], "the URL line is not the first line"
    url = named[0][len(manage_web.URL_PREFIX):]
    assert url == run_.session.url

    if kind == "bytes":
        first = raw.getvalue().split(b"\n")[0] + b"\n"
        assert first == f"{manage_web.URL_PREFIX}{url}\n".encode("ascii")
        assert b"\r" not in first

    assert opened == ([url] if open_browser else [])

    # The printed URL is the one the page answers: the launch link
    # redeems once, and the session link it names serves the page.
    parts = urllib.parse.urlsplit(url)
    assert parts.hostname == manage_web.setup_web.HOST
    assert parts.port == run_.session.port
    status, _, headers = run_.get(f"{parts.path}?{parts.query}")
    assert status == 303
    status, page, _ = run_.get(headers["Location"])
    assert status == 200
    assert manage_web.TITLE in page

    assert run_.stop() == report.OK
    after = [line for line in printed().splitlines()
             if line.startswith(manage_web.URL_PREFIX)]
    assert after == named, "a later line also starts with the parsed prefix"
    assert manage_web.STOPPED_LINE in printed()


# --- the listing ------------------------------------------------------

def test_projects_lists_the_roster_one_row_each_and_the_cli_keeps_its_sentences(
    tmp_path, env, client_here, command, capsys, manage,
):
    """PLAN-0027 phase 1 prescription 4: one row per project the roster
    names, in `wording.md`'s words; the command line's listing reads the
    same roster and keeps its own rows, sentence for sentence."""
    alpha = registered(capsys, _make_project(tmp_path / "alpha"), skill=True)
    beta = registered(capsys, _make_project(tmp_path / "beta"))
    drop_entry(client_here, record_of(beta)["key"])

    page = manage().page()

    states = ro.listing()
    found = ro.orphans()
    assert [state.root for state in states] == [
        str(alpha.resolve()), str(beta.resolve())]
    assert roster_rows(page) == expected_listing(listed())
    assert [name for name, *_ in roster_rows(page)] == ["beta", "alpha"]

    code, out, err = run(capsys, "projects")
    assert (code, err) == (0, "")
    # The command line's own rows are unchanged, sentence for sentence,
    # its mismatch clause and remedy tail with them.
    for d in ro.report_rows(states, found):
        assert ro.reason_text(d) in out
    label = cn.CLIENTS[CLIENT].label
    assert ro.MISMATCH_CLAUSE.format(label=label) in out
    assert report.PROJECTS_MISMATCH_TAIL in out
    assert out.splitlines()[0] == report.projects_head(states, found)
    # The page says each state in its own words: a status and a line of
    # facts, never the command line's clause, tail or count line.
    said = text_of(page)
    assert said.startswith(manage_web.ROSTER_HEAD)
    assert ro.MISMATCH_CLAUSE.format(label=label) not in said
    assert report.PROJECTS_MISMATCH_TAIL not in said
    assert out.splitlines()[0] not in said
    assert "<client>" not in said and "<PATH>" not in said

    # One opener per row, and beta's Repair names its root once more; no
    # table on this screen.
    assert page.count(f'value="{alpha.resolve()}"') == 1
    assert page.count(f'value="{beta.resolve()}"') == 2
    assert main_of(page).count('<li class="roster-row"') == 2
    assert rows(page) == []
    assert "<table" not in main_of(page)


def test_an_empty_roster_says_so_in_the_new_words(tmp_path, env, capsys, manage):
    """`wording.md`'s empty list: "No projects yet" and its sentence. The
    command line's own sentence for an empty machine stays on the command
    line (PLAN-0027 phase 1 dropped it from the page)."""
    page = manage().page()
    assert roster_rows(page) == []
    said = text_of(page)
    code, out, _ = run(capsys, "projects")
    assert code == 0
    assert report.PROJECTS_NONE in out
    assert report.PROJECTS_NONE not in said
    assert manage_web.EMPTY_HEAD == "No projects yet"
    assert manage_web.EMPTY_LEDE == "Choose a folder to set up your first project."
    assert manage_web.EMPTY_HEAD in said and manage_web.EMPTY_LEDE in said
    assert said.startswith(manage_web.ROSTER_HEAD)
    assert report.projects_head((), ()) not in said
    assert '<ul class="roster">' not in main_of(page)
    assert "<table" not in main_of(page)


LEFTOVER_ROW = re.compile(r'<li class="roster-row leftover-row">(.*?)</li>', re.S)


def test_a_gone_folders_entries_are_one_row_and_unpinned_ones_one_line(
    tmp_path, env, client_here, manage,
):
    """Entries Claude Desktop still holds for a folder that is gone are
    one row of Projects (PLAN-0027 phase 3 prescription 5): the folder's
    name over its parent, Needs repair, the facts in `wording.md`'s words,
    and Clean up, one press that names the folder and the screen, with
    each entry's command under Show commands, in order. It is the first
    row that needs repair, so it takes the screen's amber. Entries that
    name no project folder stand behind one line under the list with the
    engine's sentence and no press, as today."""
    vanished = tmp_path / "vanished"
    body = json.loads(client_here.read_text(encoding="utf-8"))
    body["mcpServers"] = {
        f"{PREFIX}old": {"command": "x", "args": [], "env": {ROOT_ENV: str(vanished)}},
        f"{PREFIX}old-write": {"command": "x", "args": [], "env": {ROOT_ENV: str(vanished)}},
        f"{PREFIX}bare": {"command": "x", "args": []},
    }
    client_here.write_text(json.dumps(body, indent=2), encoding="utf-8")

    run_ = manage()
    page = run_.page()
    found = ro.orphans()
    assert [orphan.key for orphan in found] == [
        f"{PREFIX}bare", f"{PREFIX}old", f"{PREFIX}old-write"]
    label = cn.CLIENTS[CLIENT].label
    # Not a project, so not one of the list's project rows; a row of its own.
    assert roster_rows(page) == []
    (row,) = LEFTOVER_ROW.findall(main_of(page))
    assert ROW_NAME.search(row).group(1) == "vanished"
    assert html.unescape(ROW_PARENT.search(row).group(1)) == str(tmp_path)
    assert STATE.search(row).groups() == ("repair", "!", "Needs repair")
    assert html.unescape(ROW_ABOUT.search(row).group(1)) == (
        f"Folder gone, {label} entries left")
    (fields,) = [dict(HIDDEN.findall(inner)) for inner in POST_PRESS.findall(row)]
    assert fields == {"token": run_.session.token, "action": manage_web.ORPHAN,
                      "client": CLIENT, "root": str(vanished),
                      "confirm": "yes", "from": manage_web.ROSTER}
    assert '<button class="primary" aria-label="Clean up vanished" type="submit">' in row
    assert [html.unescape(c) for _, c, _ in COPY_LINE.findall(row)] == [
        ro.orphan_command(found[1]), ro.orphan_command(found[2])]
    assert main_of(page).replace(
        f'<button class="primary" type="submit">{manage_web.ADD_LABEL}</button>', ""
    ).count(PRIMARY) == 1

    # The one that names no folder: one line, its sentence, no press.
    block = main_of(page).split('<details class="leftover">', 1)[1].split("</details>", 1)[0]
    said = " ".join(html.unescape(TAGS.sub(" ", block)).split())
    assert f"{label} has 1 RadialRig entry that names no project folder." in said
    assert manage_web.LEFTOVER_SHOW in said
    assert f"{PREFIX}bare" in said
    assert ro.ROSTER_REASONS[ro.ROSTER_UNPINNED] in said
    assert POST_PRESS.search(block) is None
    assert f'data-copy="radialrig unconnect {CLIENT} --orphan {PREFIX}bare"' not in page
    assert manage_web.LEFTOVER_LOOSE_MANY.format(label=label, count=2) == (
        f"{label} has 2 RadialRig entries that name no project folder.")
    assert run_.running


# --- PLAN-0014 phase 1: the listing says which project needs you ------
# The done-condition's pins, on a roster holding a healthy, a mismatched
# and an unmounted project. Each pin is its own test so a failure names
# the prescription it belongs to.

def three_projects(tmp_path, capsys, client_here) -> tuple[Path, Path, Path]:
    """Healthy, mismatched, unmounted — in that roster order.

    The mismatch is made the way the suite makes one: connected, then
    the entry removed from the client's settings. The unmounted project
    was connected and never mounted, which the engine allows: the
    connect verb has no mount check, and a connect records the project.
    """
    alpha = registered(capsys, _make_project(tmp_path / "alpha"), skill=True)
    beta = registered(capsys, _make_project(tmp_path / "beta"))
    drop_entry(client_here, record_of(beta)["key"])
    gamma = _make_project(tmp_path / "gamma")
    assert run(capsys, "connect", CLIENT, str(gamma))[0] == 0
    states = ro.listing()
    assert [state.root for state in states] == [
        str(alpha.resolve()), str(beta.resolve()), str(gamma.resolve())]
    assert [s.connected for s in states] == [CLIENT, ro.CONNECTED_MISMATCH, CLIENT]
    assert [s.mounted for s in states] == [True, True, False]
    return alpha, beta, gamma


def test_the_listing_leads_with_the_heading_and_the_name_first_rows(
    tmp_path, env, client_here, command, capsys, manage,
):
    """"Projects", then each folder's own name first, over its parent
    folder. The name is the control that opens the project; the parent is
    text a reader can select, in the mono treatment, on one line."""
    three_projects(tmp_path, capsys, client_here)
    page = manage().page()
    body = main_of(page)

    assert manage_web.ROSTER_HEAD == "Projects"
    assert f'<header class="page-head"><h1>{manage_web.ROSTER_HEAD}</h1>' in body
    drawn = roster_rows(page)
    assert [name for name, *_ in drawn] == ["beta", "gamma", "alpha"]
    for name, parent, *_ in drawn:
        root = tmp_path.resolve() / name
        assert parent == str(root.parent)
        assert str(Path(parent) / name) == str(root)
        row = roster_row(page, name)
        assert f'<button class="row-name" type="submit">{name}</button>' in row
        assert f'<span class="parent">{html.escape(parent)}</span>' in row
        assert row.index('class="row-name"') < row.index('<span class="parent">')
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    name_rule = css.split(".screen .row-name, .screen button.row-name {", 1)[1].split("}", 1)[0]
    assert "white-space: nowrap" in name_rule
    parent_rule = css.split(".screen .roster-row .parent {", 1)[1].split("}", 1)[0]
    assert "white-space: nowrap" in parent_rule
    assert "var(--muted)" in parent_rule
    mono = css.split(".screen code, .screen .path, .screen .parent {", 1)[1].split("}", 1)[0]
    assert "Cascadia Mono" in mono
    # The name's hit area is stretched over the whole row.
    assert ".screen button.row-name::after { content: \"\"; position: absolute; inset: 0; }" in css


def test_a_drive_root_is_its_own_name():
    """A root with no final name is shown whole, over nothing.

    `Path("C:\\\\").name` is empty, and so is `Path("/").name`; a row
    that read an empty name over a parent that is the root itself would
    say nothing a reader could find.
    """
    for root in ("C:\\", "/"):
        name, parent = manage_web._name_parts(root)
        if Path(root).name == "":
            assert (name, parent) == (root, "")
    name, parent = manage_web._name_parts(str(Path("C:/work/alpha")))
    assert name == "alpha"
    assert Path(parent) == Path("C:/work")


def test_a_row_says_its_facts_in_one_line(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 4: one line of facts, the connection then the count of
    skills. The client's label is the client table's; the skill names stay
    on the project's own screen."""
    three_projects(tmp_path, capsys, client_here)
    page = manage().page()
    label = cn.CLIENTS[CLIENT].label
    by_name = {name: facts for name, _, _, facts, _ in roster_rows(page)}
    assert by_name == {
        "alpha": f"{label} connected, 1 skill",
        "beta": f"{label} link lost, no skills",
        "gamma": f"{label} connected, no skills",
    }
    assert SKILL not in "".join(
        inner for _, _, inner in ROSTER_ROW.findall(main_of(page)))


def test_only_a_lost_link_reads_needs_repair_and_carries_repair(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 4: one status per row as a mark and a word, and one
    button only where something needs doing. The mark is found by shape
    and hidden from a reader by ear; the first Repair is the screen's one
    amber, and it runs on its one press from this screen (prescription 6)."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    page = run_.page()
    assert [(name, status, fix) for name, _, status, _, fix in roster_rows(page)] == [
        ("beta", "Needs repair", "Repair"), ("gamma", "Not set up", "Set up"),
        ("alpha", "Working", "")]
    marks = [STATE.search(inner).groups()[:2] for _, _, inner in ROSTER_ROW.findall(main_of(page))]
    assert marks == [("repair", "!"), ("new", manage_web.GLYPH_MINUS),
                     ("ok", manage_web.GLYPH_CHECK)]
    beta_row = roster_row(page, "beta")
    assert beta_row.count(PRIMARY) == 1
    assert main_of(page).count(PRIMARY) == 2   # beta's Repair, and Continue in the add block
    repair = dict(HIDDEN.findall(POST_PRESS.search(beta_row).group(1)))
    assert repair == {"token": run_.session.token, "action": manage_web.REPAIR,
                      "root": str(beta.resolve()), "confirm": "yes",
                      "from": manage_web.ROSTER}
    assert 'aria-label="Repair beta"' in beta_row
    # No other row carries the mark or a press that writes.
    for name in ("alpha", "gamma"):
        row = roster_row(page, name)
        assert "symbol repair" not in row and "<form method=\"post\"" not in row
    # The client is not open, so no before-press sentence.
    assert 'class="warn"' not in main_of(page)


def test_a_row_says_why_repair_is_withheld(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage,
):
    """Prescription 7 on Projects: where Repair's guard withholds the
    press, the row still reads Needs repair and says why in a few words,
    from the guard (phase 1 review A3)."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    label = cn.CLIENTS[CLIENT].label
    run_ = manage()
    assert roster_rows(run_.page())[0][2:] == (
        "Needs repair", f"{label} link lost, no skills", "Repair")
    assert '<span class="why">' not in main_of(run_.page()), "an offered Repair needs no reason"

    def why(page: str) -> list[str]:
        row = roster_row(page, "beta")
        about = row.split('<div class="about">', 1)[1].split("</div>", 1)[0]
        return [html.unescape(said) for said in re.findall(r'<span class="why">([^<]*)</span>', about)]

    # Claude Desktop's settings file no longer reads as JSON.
    before = client_here.read_bytes()
    client_here.write_bytes(b"{ not json\n")
    page = run_.page()
    by_name = {name: rest for name, _, *rest in roster_rows(page)}
    assert by_name["beta"] == ["Needs repair", f"{label} link lost, no skills", ""]
    assert why(page) == ["RadialRig can't repair it right now. Open it to see why."]
    client_here.write_bytes(before)

    # Claude Desktop isn't found on this computer.
    with monkeypatch.context() as patch:
        patch.setattr(cn, "detect", lambda client, *, env=None: ())
        assert why(run_.page()) == [f"{label} isn't on this computer."]

    # The record names no client this build knows.
    record = cn.record_path(beta.resolve())
    body = json.loads(record.read_text(encoding="utf-8"))
    body["client"] = "some-other-client"
    record.write_text(json.dumps(body, indent=2), encoding="utf-8")
    assert why(run_.page()) == ["RadialRig can't read its connection record."]
    assert run_.running


def test_the_listing_orders_rows_that_need_attention_first(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 4: needs repair, update ready, not set up, then
    working, each group in the roster's order. The page sorts; the
    command line does not.

    Five projects, in a roster order the sort must change: healthy,
    unmounted, mismatched, healthy, and one both mismatched and
    unmounted, which needs repair.
    """
    alpha = registered(capsys, _make_project(tmp_path / "alpha"))
    gamma = _make_project(tmp_path / "gamma")
    assert run(capsys, "connect", CLIENT, str(gamma))[0] == 0
    beta = registered(capsys, _make_project(tmp_path / "beta"))
    drop_entry(client_here, record_of(beta)["key"])
    delta = registered(capsys, _make_project(tmp_path / "delta"))
    epsilon = _make_project(tmp_path / "epsilon")
    assert run(capsys, "connect", CLIENT, str(epsilon))[0] == 0
    drop_entry(client_here, record_of(epsilon)["key"])

    states = ro.listing()
    assert [Path(s.root).name for s in states] == ["alpha", "gamma", "beta", "delta", "epsilon"]
    page = manage().page()
    assert [name for name, *_ in roster_rows(page)] == [
        "beta", "epsilon", "gamma", "alpha", "delta"]
    assert roster_rows(page) == expected_listing(listed())

    # The command line's rows keep the roster's order, unchanged.
    assert [d.path for d in ro.report_rows(states, ())] == [s.root for s in states]
    code, out, _ = run(capsys, "projects")
    assert code == 0
    positions = [out.index(str(root.resolve()))
                 for root in (alpha, gamma, beta, delta, epsilon)]
    assert positions == sorted(positions)


def test_add_a_project_opens_the_folder_field_under_the_heading(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 4: "Add a project" sits above the list and opens the
    folder field. It is a native disclosure, so it changes no view and
    sends nothing; its Continue is a navigation, a form like every other,
    so the server goes on standing."""
    three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    page = run_.page()
    body = main_of(page)

    head = body.split('<header class="page-head">', 1)[1].split("</header>", 1)[0]
    assert head.startswith(f"<h1>{manage_web.ROSTER_HEAD}</h1>"
                           f'<details class="add" id="{manage_web.ADD_ID}">'
                           f'<summary class="button">{manage_web.ADD_HEAD}</summary>')
    assert body.count(f'id="{manage_web.ADD_ID}"') == 1
    assert body.index(f'id="{manage_web.ADD_ID}"') < body.index('<ul class="roster">')
    assert ANCHOR.findall(page) == []
    assert not EXTERNAL_REF.search(page)
    # Continue: a GET to the walkthrough, carrying the field.
    add = head.split(f'<details class="add" id="{manage_web.ADD_ID}">', 1)[1]
    action, inner = GET_FORM.search(add).groups()
    assert action == PATH
    assert dict(HIDDEN.findall(inner)) == {"s": run_.session.token, "view": manage_web.SETUP}
    assert '<input id="root" name="root"' in inner
    assert f'<button class="primary" type="submit">{manage_web.ADD_LABEL}</button>' in inner
    # Every view change on the screen is a form: three openers, gamma's Set
    # up, Continue and Remove RadialRig.
    targets = navigations(page, run_.session.token)
    assert len(targets) == 6
    assert not any("#" in target for target in targets)
    js = (packaged_web() / "page.js").read_text(encoding="utf-8")
    assert 'addEventListener("pagehide"' in js
    assert run_.running


def test_an_empty_roster_shows_the_add_block_in_the_listings_place(
    tmp_path, env, manage,
):
    """The empty shape: the heading alone, then where the list would be,
    "No projects yet", its sentence, and Add a project as the screen's one
    amber, open to its folder field."""
    page = manage().page()
    body = main_of(page)
    assert "<table" not in body and '<ul class="roster">' not in body
    assert body.count(f'id="{manage_web.ADD_ID}"') == 1
    head = body.index(f"<h1>{manage_web.ROSTER_HEAD}</h1>")
    empty = body.index(f'<div class="empty"><h2>{manage_web.EMPTY_HEAD}</h2>'
                       f"<p>{manage_web.EMPTY_LEDE}</p>"
                       f'<details class="add" id="{manage_web.ADD_ID}">'
                       f'<summary class="button primary">{manage_web.ADD_HEAD}</summary>')
    foot = body.index('<footer class="foot">')
    assert head < empty < foot
    assert '<header class="page-head">' f"<h1>{manage_web.ROSTER_HEAD}</h1></header>" in body
    assert ANCHOR.findall(page) == []


def test_the_add_press_is_disabled_by_the_script_until_the_field_names_a_folder(
    manage,
):
    """The press follows the field, in page.js.

    The server renders the press without `disabled`, so a plain browser
    with no script keeps the bare submit it has always had; the script
    sets `disabled` on load while the field is empty and toggles it on
    the field's `input` event, which the shell's picker dispatches after
    writing the path (`app/src-tauri/src/picker.rs`, `eval_script`).
    """
    page = manage().page()
    body = main_of(page)
    # As an attribute: the test's own folder name, in the log path the
    # footer shows, carries the word.
    assert not re.search(r"<[^>]*\sdisabled\b[^>]*>", body)
    assert '<input id="root" name="root" value=""' in body

    js = (packaged_web() / "page.js").read_text(encoding="utf-8")
    assert 'document.getElementById("root")' in js
    assert 'querySelector("button.primary")' in js
    assert 'press.disabled = field.value.trim() === ""' in js
    assert 'field.addEventListener("input", follow)' in js
    # On load, before any event.
    assert js.index('field.addEventListener("input", follow)') < js.index("follow();")
    # And the script is the one the page serves.
    assert js.replace("\r\n", "\n") in page


def test_a_picked_path_opens_the_closed_details_that_holds_the_field(manage):
    """On Projects the folder field sits inside Add a project's Details,
    drawn closed. The shell's picker writes the path into the field and
    dispatches its `input` event (`picker.rs`, `eval_script`), which it can
    do from the app's menu with the Details closed. page.js opens the
    Details that holds the field on that event, so the path shows
    (PLAN-0027 phase 5 review P1)."""
    body = main_of(manage().page())
    add = body.split(f'<details class="add" id="{manage_web.ADD_ID}">', 1)[1]
    assert '<input id="root" name="root"' in add.split("</details>", 1)[0]
    assert f'<details class="add" id="{manage_web.ADD_ID}" open' not in body

    js = (packaged_web() / "page.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert 'var holder = field.closest("details");' in js
    assert 'field.addEventListener("input", function () { holder.open = true; });' in js
    # Wired on load, from the same field the press follows.
    assert js.index('var field = document.getElementById("root");') < js.index(
        'var holder = field.closest("details");')


def test_the_browse_button_says_what_the_shells_menu_item_says():
    """One wording for the picker, on the page and in the shell.

    The shell's menu item keeps its words; the page's button takes them.
    The shell's strings file is read where the checkout carries it.
    """
    assert manage_web.BROWSE_LABEL == "Choose a folder"
    assert manage_web.ADD_FIELD_PLACEHOLDER == "Type or paste a folder path"
    strings = Path(__file__).resolve().parents[3] / "app" / "src" / "strings.json"
    if strings.is_file():
        words = json.loads(strings.read_text(encoding="utf-8"))
        assert words["picker_menu_label"] == manage_web.BROWSE_LABEL


@pytest.mark.parametrize("mounted", [True, False])
@pytest.mark.parametrize("connected", [CLIENT, ro.CONNECTED_NO, ro.CONNECTED_MISMATCH])
@pytest.mark.parametrize("skills", [(), (SKILL,), ("plan-review", SKILL)])
@pytest.mark.parametrize("installed", [True, False])
@pytest.mark.parametrize("behind", [False, True])
def test_the_page_says_a_state_through_the_mapping(
    mounted, connected, skills, installed, behind, tmp_path, monkeypatch,
):
    """`wording.md`'s row, over every state a project can be in: its one
    status, its line of facts, and its statuses on the project's screen,
    the most pressing first.

    A project with no client named is said without a client where none
    is installed here, while the command line names the client either way
    (the owner's answer of 2026-10-01).
    """
    if installed:
        for name, value in fake_client(tmp_path).items():
            monkeypatch.setenv(name, value)
    root = "C:\\work\\alpha"
    state = ro.ProjectState(
        root=root, exists=True, mounted=mounted, connected=connected,
        skills=skills, claimed=CLIENT if connected != ro.CONNECTED_NO else "",
        behind=behind)
    here = cn.installed()
    assert manage_web._about(state, here) == facts_of(state)
    held = []
    if connected == ro.CONNECTED_MISMATCH:
        held.append(manage_web.NEEDS_REPAIR)
    if behind:
        held.append(manage_web.UPDATE_READY)
    if not mounted:
        held.append(manage_web.NOT_SET_UP)
    assert manage_web._statuses(state) == (tuple(held) or (manage_web.WORKING,))
    assert expected_row(state)[2] == manage_web.STATUS_WORDS[manage_web._statuses(state)[0]]
    label = cn.CLIENTS[CLIENT].label
    if connected == ro.CONNECTED_NO and not installed:
        assert label not in manage_web._about(state, here)
    # The command line's own words for the same state are untouched.
    line = ro.reason_text(ro.report_rows((state,), ())[0])
    assert ("mounted" if mounted else "not mounted") in line
    assert ("no skills" if not skills else "skills: " + ", ".join(skills)) in line
    if connected == ro.CONNECTED_NO:
        assert f"; not registered with {label}; " in line
    if connected == ro.CONNECTED_MISMATCH:
        assert f"; {ro.mismatch_clause(state)}; " in line
        # A record naming no client this build knows: the row still says
        # the link is lost, by the table's own phrase.
        unknown = dataclasses.replace(state, claimed="")
        assert manage_web._about(unknown, here) == (
            f"{cn.label_phrase()} link lost, " + facts_of(state).split(", ", 1)[1])


def test_an_unreadable_client_profile_costs_the_row_nothing(monkeypatch):
    """`is_dir` raises on a profile directory that denies traversal, and
    the listing asks about clients once per screen. The row says what it
    can and names no client, rather than failing the page (review of
    2026-10-01)."""
    def denied(client, *, env=None):
        raise PermissionError("the profile directory denies traversal")

    monkeypatch.setattr(cn, "detect", denied)
    assert cn.installed() == ()
    state = ro.ProjectState(
        root="C:\\work\\alpha", exists=True, mounted=True,
        connected=ro.CONNECTED_NO, skills=())
    assert manage_web._about(state, cn.installed()) == "No skills"


def test_an_unreadable_client_profile_costs_the_project_page_nothing(
    tmp_path, env, client_here, command, capsys, manage, monkeypatch,
):
    """The project screen's two gates, Connect and Repair, each ask
    `detect` whether the client is on this machine, and `is_dir` raises on
    a profile directory that denies traversal. Every project's screen
    still opens, and offers no press that writes to a client it could not
    read, the way the listing names none."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    delta = _make_project(tmp_path / "delta")
    assert run(capsys, "mount", str(delta))[0] == 0
    run_ = manage()
    # Readable, the mismatch is offered the repair and the unregistered
    # project the registration, so their absence below is the gates' doing.
    assert f'value="{manage_web.REPAIR}"' in main_of(project_page(run_, beta))
    assert f'value="{manage_web.CONNECT}"' in main_of(project_page(run_, delta))

    def denied(client, *, env=None):
        raise PermissionError("the profile directory denies traversal")

    monkeypatch.setattr(cn, "detect", denied)
    for project in (alpha, beta, gamma, delta):
        body = main_of(project_page(run_, project))
        assert f"<h1>{project.name}</h1>" in body, project.name
        for value in (manage_web.REPAIR, manage_web.CONNECT):
            assert f'value="{value}"' not in body, (project.name, value)
    assert run_.running


def test_the_client_label_is_read_from_the_table_at_render_time(monkeypatch):
    """Constraint 5: the connection fact is a template with a slot.

    Swap the client's label in the table and the fact follows it, with
    no constant edited: the words are the table's, not the page's.
    """
    import dataclasses as dc

    client = cn.CLIENTS[CLIENT]
    renamed = dc.replace(client, label="Some Other Client")
    monkeypatch.setattr(cn, "CLIENTS", {**cn.CLIENTS, CLIENT: renamed})
    state = ro.ProjectState(
        root="C:\\work\\alpha", exists=True, mounted=True,
        connected=CLIENT, skills=())
    assert manage_web._about(state, (renamed,)) == "Some Other Client connected, no skills"
    assert manage_web.FACT_CONNECTED == "{label} connected"
    assert manage_web.CONNECTED_SAID.format(label=renamed.label).startswith(
        "Connected Some Other Client.")


def test_an_unregistered_project_names_only_the_clients_installed_here(monkeypatch):
    """The page names the clients this machine has, the command line
    every client in the table (the owner's answer of 2026-10-01). With
    one client in the table the two read alike wherever it is installed,
    so this table carries a second client this machine does not have.
    """
    import dataclasses as dc

    client = cn.CLIENTS[CLIENT]
    other = dc.replace(client, name="other-client", label="Other Client")
    monkeypatch.setattr(cn, "CLIENTS", {CLIENT: client, other.name: other})
    monkeypatch.setattr(cn, "detect", lambda c, *, env=None: c.name == CLIENT)
    state = ro.ProjectState(
        root="C:\\work\\alpha", exists=True, mounted=True,
        connected=ro.CONNECTED_NO, skills=(SKILL,))
    assert manage_web._about(state, cn.installed()) == (
        f"{client.label} not connected, 1 skill")
    assert f"; not registered with {client.label} or Other Client; " in ro.reason_text(
        ro.report_rows((state,), ())[0])


def test_a_skill_one_agent_has_reads_on_in_its_column_alone(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Where the agents' skills differ, the row on Projects counts the
    skills either agent has, and the project's grid says each agent's own
    state in its own column (D-36). The listing's per-agent line of facts
    is gone with the redesign."""
    alpha = registered(capsys, _make_project(tmp_path / "alpha"))
    first, second = list(sk.HARNESSES.values())[:2]
    assert run(capsys, "skill", "add", SKILL, str(alpha), "--harness", first.name)[0] == 0
    run_ = manage()
    label = cn.CLIENTS[CLIENT].label
    assert [facts for _, _, _, facts, _ in roster_rows(run_.page())] == [
        f"{label} connected, 1 skill"]
    cells = grid_cells(project_page(run_, alpha))
    assert cells[(SKILL, first.label)] == ("on", "On")
    assert cells[(SKILL, second.label)] == ("off", "Off")


def test_no_page_roster_or_report_constant_names_a_client_or_a_skill():
    """Constraint 5, the other half: no constant carries a name.

    A client's name and label, and every packaged skill's name, are
    data; a sentence with one in it is a template filled at render time.
    Walked over the three modules the plan names.
    """
    client = cn.CLIENTS[CLIENT]
    forbidden = [client.name, client.label, *sk.catalog_names()]
    assert forbidden
    from radialrig import roster as roster_module
    for module in (manage_web, roster_module, report):
        for name in dir(module):
            if not name.isupper() or name.startswith("_"):
                continue
            for text in strings_in(getattr(module, name)):
                for word in forbidden:
                    assert word.lower() not in text.lower(), \
                        f"{module.__name__}.{name} names '{word}'"


# --- PLAN-0014 phase 2: a project's page names the project and the way back
# The done-condition's pins, on the same three-project roster. Each pin
# is its own test so a failure names the prescription it belongs to.

# A section's row on the project page: the heading element, then what
# follows it. Read off the raw markup, because `text_of` puts a space at
# every tag and would hide a name run into its sentence.
# The skill grid's rows (PLAN-0027 phase 1 prescription 5): the skill's
# name and its one line, then a cell per agent in the harness map's order.
GRID_ROW = re.compile(
    r'<tr( class="retired")?><th scope="row"><span class="skill-info">'
    r"<strong>([^<]*)</strong><small>([^<]*)</small></span></th>(.*?)</tr>", re.S)
GRID_CELL = re.compile(r"<td>(.*?)</td>", re.S)
CELL_BUTTON = re.compile(
    r'<button class="cell ?([a-z]*)"([^>]*) type="submit">'
    r'(?:<span aria-hidden="true">[^<]*</span>)?([^<]*)</button>')
CELL_NOTE = re.compile(r'<span class="cell-note">([^<]*)</span>')
AGENT_HEAD = re.compile(
    r'<th scope="col"><span class="agent-head"><span>([^<]*)</span>(.*?)</span></th>', re.S)
# The command behind its disclosure, with the Copy button inside it.
# A command a press runs, under the screen's Show commands switch, with
# its Copy button named for it.
COPY_LINE = re.compile(
    r'<div class="copy-line"><code>([^<]*)</code>'
    r'<button class="copy" type="button" data-copy="([^"]*)" '
    r'aria-label="([^"]*)">Copy</button></div>')
POST_PRESS = re.compile(r'<form method="post" action="[^"]+">(.*?)</form>', re.S)
GHOST = re.compile(r'<button class="ghost" type="submit">([^<]*)</button>')


SHOW_COMMANDS_BOX = (
    '<label class="check"><input type="checkbox" class="show-commands">'
    f"{manage_web.SHOW_COMMANDS}</label>")
CONNECTIONS = f'<h2 class="section">{manage_web.CONNECTIONS_HEAD}</h2>'


def project_page(run_, root: Path) -> str:
    return view(run_, manage_web.PROJECT, root=root)


# One agent's list in the Skills section (PLAN-0015 phase 2): the label
# as its caption, and everything under it — the whole-catalog press
# where one stands, then the rows.


def grid_of(page: str) -> str:
    return main_of(page).split('<table class="skill-grid">', 1)[1].split("</table>", 1)[0]


def grid_cells(page: str) -> dict[tuple[str, str], tuple[str, str]]:
    """Every cell of the skill grid, by (skill, agent label): its kind
    (on, off, update, remove, a note, or "" for an empty cell) and its
    words."""
    labels = [h.label for h in sk.HARNESSES.values()]
    out = {}
    for _, name, _, cells in GRID_ROW.findall(grid_of(page)):
        for label, cell in zip(labels, GRID_CELL.findall(cells)):
            found = CELL_BUTTON.search(cell)
            note = CELL_NOTE.search(cell)
            if found:
                out[(html.unescape(name), label)] = (
                    found.group(1) or "remove", html.unescape(found.group(3)))
            elif note:
                out[(html.unescape(name), label)] = ("note", html.unescape(note.group(1)))
            else:
                out[(html.unescape(name), label)] = ("", "")
    return out


def grid_presses(page: str) -> dict[tuple[str, str], dict[str, str]]:
    """The form each cell posts, by (skill, agent label), token dropped."""
    labels = [h.label for h in sk.HARNESSES.values()]
    out = {}
    for _, name, _, cells in GRID_ROW.findall(grid_of(page)):
        for label, cell in zip(labels, GRID_CELL.findall(cells)):
            form = POST_PRESS.search(cell)
            if form is not None:
                fields = dict(HIDDEN.findall(form.group(1)))
                fields.pop("token")
                out[(html.unescape(name), label)] = fields
    return out


def agent_heads(page: str) -> dict[str, dict[str, str] | None]:
    """Each agent's column head: the form its Add all posts, or None."""
    out = {}
    for label, inner in AGENT_HEAD.findall(grid_of(page)):
        form = POST_PRESS.search(inner)
        fields = dict(HIDDEN.findall(form.group(1))) if form else None
        if fields:
            fields.pop("token")
        out[html.unescape(label)] = fields
    return out


def status_band(page: str) -> list[tuple[str, str, str]]:
    """The status band, one row per state that holds: the mark's shape,
    the sentence, and the words of its fix, or ""."""
    band = main_of(page).split('<div class="status-band">', 1)[1].split(
        '<table class="skill-grid">', 1)[0]
    out = []
    for part in band.split('<div class="status-row">')[1:]:
        shape = re.search(r'<span class="symbol ([a-z]+)"', part).group(1)
        said = re.search(r'<div class="words"><p>([^<]*)</p>', part).group(1)
        button = BUTTON.search(part)
        out.append((shape, html.unescape(said), html.unescape(button.group(2)) if button else ""))
    return out


def connections_of(page: str) -> str:
    return main_of(page).split(CONNECTIONS, 1)[1].split('<div class="project-foot">', 1)[0]


def result_of(page: str) -> tuple[str, str, str, str] | None:
    """The result line at the top of a screen: its tone, its sentence,
    the after-press sentence or "", and its Details' text or ""."""
    found = re.search(
        r'<div class="result ([a-z]+)" role="status" aria-live="polite">(.*?)</div>'
        r"((?:<details>.*?</details>)?)</div>", main_of(page), re.S)
    if found is None:
        return None
    tone, words, more = found.groups()
    said = re.findall(r"<p>([^<]*)</p>", words)
    warn = re.findall(r'<p class="warn">([^<]*)</p>', words)
    details = " ".join(html.unescape(TAGS.sub(" ", more)).split())
    return (tone, html.unescape(said[0]), html.unescape(warn[0]) if warn else "", details)


def said_lines(page: str) -> list[str]:
    """Every sentence a press's page prints beneath its table."""
    return [html.unescape(text)
            for text in re.findall(r'<p class="said">([^<]*)</p>', main_of(page))]


def back_links(page: str) -> list[str]:
    """The action of every navigation that is the way back to Projects."""
    out = []
    for action, inner in GET_FORM.findall(page):
        if '<button class="link back" type="submit">' in inner:
            out.append(action)
    return out


def without_commands(page: str) -> str:
    """The page with every command frame cut out, so a path inside a
    command does not count as the page naming it."""
    page = re.sub(r'<div class="cmd">.*?</div>', "", page, flags=re.S)
    return re.sub(r'<div class="copy-line">.*?</div>', "", page, flags=re.S)


def test_the_project_page_leads_with_the_name_and_says_the_path_once(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 5: the heading is the folder's name, the path beside
    it, and nowhere else outside a command."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    for project in (alpha, beta, gamma):
        page = project_page(run_, project)
        body = main_of(page)
        root = str(project.resolve())
        assert (f'<div class="project-name"><h1>{project.name}</h1>'
                f'<span class="path">{html.escape(root, quote=False)}</span></div>') in body
        assert body.count("<h1>") == 1
        assert text_of(without_commands(page)).count(root) == 1, project.name
        # No plan table and no lede on this screen.
        assert '<table class="plan' not in body
        assert '<p class="lede">' not in body
        for gone in ("PROJECT_HEAD", "PROJECT_LEDE", "PROJECT_CAPTION",
                     "ACTIONS_HEAD", "NOTHING_TO_DO", "PROJECT_COLUMNS"):
            assert not hasattr(manage_web, gone), gone
    assert run_.running


def test_the_way_back_stands_above_the_name_and_lands_on_the_row(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 5: "← Projects" above the name, a form to the list
    whose action names the row this screen was opened from by the id that
    row carries. The redesign keeps one way back, at the top; the second
    control the old page drew after its last section is gone."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    listing = run_.page()
    for project in (alpha, beta, gamma):
        root = str(project.resolve())
        row_id = manage_web._row_id(root)
        assert re.fullmatch(r"row-[0-9a-f]{12}", row_id)
        assert main_of(listing).count(f'<li class="roster-row" id="{row_id}">') == 1
        page = project_page(run_, project)
        body = main_of(page)
        assert back_links(page) == [f"{PATH}#{row_id}"], project.name
        assert (f'<button class="link back" type="submit"><span aria-hidden="true">'
                f"{manage_web.GLYPH_BACK}</span>{manage_web.ROSTER_HEAD}</button>") in body
        assert body.index('<button class="link back"') < body.index("<h1>")
        assert body.count(f'#{row_id}"') == 1
        # Same id from the same root however it is spelled, as far as
        # this platform folds a spelling.
        assert manage_web._row_id(os.path.normcase(root)) == row_id
        if os.path.normcase("A") == "a":
            assert manage_web._row_id(root.upper()) == row_id
        else:
            assert manage_web._row_id(root.upper()) != row_id
        # A browser never sends the fragment; the list answers and the
        # row is on it, with its id, so the fragment has somewhere to land.
        targets = [t for t in navigations(page, run_.session.token)
                   if "view=roster" in t]
        assert len(targets) == 1
        assert not any("#" in t for t in targets)
        status, back, _ = run_.get(targets[0])
        assert status == 200
        assert f'<li class="roster-row" id="{row_id}">' in main_of(back)
        assert roster_rows(back) == expected_listing(listed())
    assert run_.running


def test_the_project_page_reads_status_skills_connections_then_its_foot(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 5: the status as one sentence with its fix beside it,
    the skill grid, the Connections row, and Details with the removal at
    the foot, in that order."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    assert (manage_web.SKILLS_HEAD, manage_web.CONNECTIONS_HEAD) == ("Skills", "Connections")
    label = cn.CLIENTS[CLIENT].label
    expected = {
        "alpha": [("ok", "RadialRig is working in this project.", "")],
        "beta": [("repair", f"{label} lost its link to this project.", "Repair")],
        "gamma": [("new", "RadialRig is not set up in this project.", "Set up")],
    }
    for project in (alpha, beta, gamma):
        page = project_page(run_, project)
        body = main_of(page)
        assert status_band(page) == expected[project.name], project.name
        order = [body.index(mark) for mark in (
            '<div class="project-name">', '<div class="status-band">',
            f'<table class="skill-grid"><thead><tr><th scope="col"><h2>{manage_web.SKILLS_HEAD}</h2></th>',
            CONNECTIONS, '<div class="project-foot"><details class="settings">')]
        assert order == sorted(order), project.name
        # The command line's remedy tail, with its placeholders, is on no
        # screen.
        assert report.PROJECTS_MISMATCH_TAIL not in html.unescape(body)
    assert run_.running


def test_a_skill_name_and_its_line_are_separate_blocks(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Each grid row names its skill once, then its one line from the
    catalog, in two elements the stylesheet lays out as blocks (a flex
    container's children), so the accessibility text keeps a word between
    them. The skill names come from the catalog in its order; the column
    heads are the agents' labels from the map."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    for project in (alpha, beta, gamma):
        grid = grid_of(project_page(run_, project))
        found = GRID_ROW.findall(grid)
        assert [name for _, name, _, _ in found] == list(sk.catalog_names()), project.name
        for _, name, said, _ in found:
            assert html.unescape(said) == sk.catalog_entry(name).summary
        assert [label for label, _ in AGENT_HEAD.findall(grid)] == [
            h.label for h in sk.HARNESSES.values()]
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert ".screen .skill-info { display: flex;" in css
    assert run_.running


def test_every_press_names_its_target(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Every button on the screen is read by a name that says what it
    acts on: each cell its skill and agent, each Add all its agent,
    Connect and Disconnect the client. The names are templates with a
    slot, filled from the catalog, the harness map or the client table.
    Every cell runs on its one press, posting `confirm` and `from`."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    label = cn.CLIENTS[CLIENT].label
    assert (manage_web.CELL_NAME, manage_web.CELL_UPDATE_NAME, manage_web.ADD_ALL_NAME,
            manage_web.CONNECT_LABEL, manage_web.UNCONNECT_LABEL,
            manage_web.RETIRED_NAME) == (
        "{skill} for {agent}", "Update {skill} for {agent}", "Add all skills for {agent}",
        "Connect {label}", "Disconnect {label}", "Remove {skill} for {agent}")
    for project in (alpha, beta, gamma):
        page = project_page(run_, project)
        facts = ro.verify(project.resolve())
        cells, presses = grid_cells(page), grid_presses(page)
        for (agent, names) in facts.agents:
            harness = sk.HARNESSES[agent]
            for name in sk.catalog_names():
                on = name in names
                assert cells[(name, harness.label)] == (
                    ("on", "On") if on else ("off", "Off")), (project.name, name)
                assert presses[(name, harness.label)] == {
                    "action": manage_web.SKILL_REMOVE if on else manage_web.SKILL_ADD,
                    "root": str(project.resolve()), "skill": name, "harness": agent,
                    "confirm": "yes", "from": manage_web.PROJECT}
                grid = grid_of(page)
                assert (f'aria-label="{name} for {harness.label}" '
                        f'aria-pressed="{"true" if on else "false"}"') in grid
        heads = agent_heads(page)
        for (agent, names) in facts.agents:
            harness = sk.HARNESSES[agent]
            if len(names) < len(sk.catalog_names()):
                assert heads[harness.label] == {
                    "action": manage_web.SKILL_ADD_ALL, "root": str(project.resolve()),
                    "harness": agent, "confirm": "yes", "from": manage_web.PROJECT}
                assert f'aria-label="Add all skills for {harness.label}"' in grid_of(page)
            else:
                assert heads[harness.label] is None
        connections = connections_of(page)
        if facts.connected == CLIENT:
            assert f'aria-label="Disconnect {label}"' in connections
        assert f'aria-label="Connect {label}"' not in connections
    # A skill a record still lists that the package no longer ships gets
    # its own row, with Remove under the agent that still has it.
    first = next(iter(sk.HARNESSES.values()))
    retire_a_skill(alpha.resolve(), first, SKILL, "lap-checklist")
    page = project_page(run_, alpha)
    cells = grid_cells(page)
    assert cells[("lap-checklist", first.label)] == ("remove", "Remove")
    for other in list(sk.HARNESSES.values())[1:]:
        assert cells[("lap-checklist", other.label)] == ("", "")
    assert grid_presses(page)[("lap-checklist", first.label)] == {
        "action": manage_web.SKILL_REMOVE, "root": str(alpha.resolve()),
        "skill": "lap-checklist", "harness": first.name,
        "confirm": "yes", "from": manage_web.PROJECT}
    assert f'aria-label="Remove lap-checklist for {first.label}"' in grid_of(page)
    assert f"<small>{manage_web.RETIRED_SAID}</small>" in grid_of(page)
    assert run_.running


def retire_a_skill(root: Path, harness, source: str, retired: str) -> None:
    """Make one agent's record list a skill this package no longer ships:
    a copy of an installed skill under a name the catalog doesn't have."""
    record_path = root / harness.record
    record = json.loads(record_path.read_text(encoding="utf-8"))
    template = next(e for e in record["skills"] if e["name"] == source)
    clone = json.loads(json.dumps(template).replace(f"/{source}/", f"/{retired}/")
                       .replace(f'"{source}"', f'"{retired}"'))
    record["skills"].append(clone)
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    shutil.copytree(root / harness.skills / source, root / harness.skills / retired)


def test_the_grid_has_a_column_per_agent_and_add_all_fills_one(
    tmp_path, env, client_here, command, capsys, manage,
):
    """PLAN-0015 phase 2's done-condition, on the redesigned grid.

    A project with every skill for the first agent and none for the
    second: the first column reads On on every row and has no Add all;
    the second reads Off with Add all in its head. Every press in a
    column names that column's agent, and its command narrows to it.
    The two-step press still rehearses first and writes nothing; Add all
    on its one press fills the second column, and Add all is gone.
    Nothing on the screen says "harness" outside a command line.
    """
    alpha, _, _ = three_projects(tmp_path, capsys, client_here)
    first, second = list(sk.HARNESSES.values())[:2]
    root = str(alpha.resolve())
    assert run(capsys, "skill", "remove", SKILL, root, "--harness", second.name)[0] == 0
    assert run(capsys, "skill", "add", "--all", root, "--harness", first.name)[0] == 0
    assert not hasattr(manage_web, "SKILL_SPLIT")
    run_ = manage()

    page = project_page(run_, alpha)
    cells = grid_cells(page)
    for name in sk.catalog_names():
        assert cells[(name, first.label)] == ("on", "On")
        assert cells[(name, second.label)] == ("off", "Off")
    for (name, label), fields in grid_presses(page).items():
        harness = next(h for h in sk.HARNESSES.values() if h.label == label)
        assert fields["harness"] == harness.name
    for shown, copied, _ in COPY_LINE.findall(grid_of(page)):
        line = html.unescape(copied)
        if line.startswith("radialrig skill "):
            assert line.endswith((f" --harness {first.name}", f" --harness {second.name}")), line
    heads = agent_heads(page)
    assert heads[first.label] is None
    assert heads[second.label] == {"action": manage_web.SKILL_ADD_ALL, "root": root,
                                   "harness": second.name, "confirm": "yes",
                                   "from": manage_web.PROJECT}
    act = manage_web.Action(manage_web.SKILL_ADD_ALL, root=root, harness=second.name)
    assert act.command == f"radialrig skill add --all {root} --harness {second.name}"
    assert f'data-copy="{html.escape(act.command, quote=True)}"' in grid_of(page)
    assert "harness" not in text_of(without_commands(page)).lower()

    # The two-step press: the rehearsal first, which writes nothing, and
    # one head line for the press naming every skill it writes.
    press = {"action": manage_web.SKILL_ADD_ALL, "root": root, "harness": second.name}
    before = ro.verify(alpha.resolve())
    plan = pressed(run_, press)
    assert ro.verify(alpha.resolve()) == before, "the rehearsal wrote something"
    head = re.search(r"<h1>([^<]*)</h1>", main_of(plan)).group(1)
    assert head == html.escape(
        f"Would add {report._joined(f'{e.name} {e.version}' for e in sk.SKILL_CATALOG)} "
        f"for {second.label} to {root}", quote=False)
    assert text_of(plan).count("Would add ") == 1
    assert first.label not in text_of(plan)
    drawn = rows(plan)
    created = [path for verb, path, _ in drawn if verb == mnt.CREATED]
    assert created and len(set(created)) == len(created)
    for entry in sk.SKILL_CATALOG:
        for rel in entry.files:
            assert f"{second.skills}/{entry.name}/{rel}" in created
    for line in said_lines(plan):
        for path in created:
            assert path not in line, (line, path)
    assert main_of(plan).count(PRIMARY) == 1

    # The one press, as the grid drew it.
    done = pressed(run_, heads[second.label])
    assert result_of(done)[:2] == (
        manage_web.RESULT_DONE,
        f"Added all skills for {second.label}. Start a new agent session to pick them up.")
    after = ro.verify(alpha.resolve())
    assert after.skills == tuple(sorted(sk.catalog_names()))
    assert ro.agents_agree(after)
    cells = grid_cells(done)
    assert {cells[(name, label)] for name in sk.catalog_names()
            for label in (first.label, second.label)} == {("on", "On")}
    assert agent_heads(done) == {first.label: None, second.label: None}
    assert "harness" not in text_of(without_commands(done)).lower()
    assert run_.running


def test_add_all_speaks_for_the_press_when_the_first_catalog_skill_stands(
    tmp_path, env, client_here, command, capsys, manage,
):
    """The whole-catalog press on a project where the catalog's first
    skill is already installed for that agent. Today's plan page's head
    names the skills the press writes, never "Already installed" for the
    one it does not touch; that skill's rows are not in the table, and the
    outcome is the write's, not a no-op's. After Run, the same head with
    the past verb, and the grid reads On down that agent's column.
    """
    alpha, _, _ = three_projects(tmp_path, capsys, client_here)
    first, second = list(sk.HARNESSES.values())[:2]
    root = str(alpha.resolve())
    assert sk.catalog_names()[0] == SKILL, "the fixture skill leads the catalog"
    assert run(capsys, "skill", "remove", SKILL, root, "--harness", first.name)[0] == 0
    run_ = manage()
    press = {"action": manage_web.SKILL_ADD_ALL, "root": root, "harness": second.name}
    rest = [entry for entry in sk.SKILL_CATALOG if entry.name != SKILL]

    plan = pressed(run_, press)
    head = html.unescape(re.search(r"<h1>([^<]*)</h1>", main_of(plan)).group(1))
    assert head == (f"Would add {report._joined(f'{e.name} {e.version}' for e in rest)} "
                    f"for {second.label} to {root}")
    said = text_of(without_commands(plan))
    assert "Already installed" not in said and report.SKILL_CURRENT_TAIL not in said
    assert report.SKILL_NEXT_STEP in said
    assert main_of(plan).count(PRIMARY) == 1
    drawn = rows(plan)
    assert drawn and all(verb != mnt.CURRENT for verb, _, _ in drawn)
    assert not any(f"/{SKILL}/" in path for _, path, _ in drawn)
    assert [path for verb, path, _ in drawn if verb == mnt.UPDATED] == [second.record]
    for entry in rest:
        assert f"{second.skills}/{entry.name}/SKILL.md" in {path for _, path, _ in drawn}
    # The command line's own answer to the same press keeps its blocks,
    # and the page's is the same engine result in the page's shape.
    results = sk.add_catalog_plan(alpha.resolve(), targets=(second.name,))
    assert [r.current for r in results] == [True] + [False] * len(rest)
    assert report.skill_add_all_report(results, dry_run=True)[0] == head

    done = pressed(run_, press, confirm=True)
    head = html.unescape(re.search(r"<h1>([^<]*)</h1>", main_of(done)).group(1))
    assert head == (f"Added {report._joined(f'{e.name} {e.version}' for e in rest)} "
                    f"for {second.label} to {root}")
    assert "Already installed" not in text_of(done)
    assert all(verb != mnt.CURRENT for verb, _, _ in rows(done))
    page = project_page(run_, alpha)
    assert {grid_cells(page)[(name, second.label)] for name in sk.catalog_names()} == {
        ("on", "On")}
    assert agent_heads(page)[second.label] is None
    # A press that finds every skill standing says so in one head, with
    # the current tail, and writes nothing.
    again = pressed(run_, press)
    head = html.unescape(re.search(r"<h1>([^<]*)</h1>", main_of(again)).group(1))
    assert head.startswith(f"Already installed for {second.label}: ")
    assert report.SKILL_CURRENT_TAIL in text_of(again)
    assert not rows(again)


def test_add_all_is_withheld_while_a_cell_in_that_column_is_blocked(
    tmp_path, env, client_here, command, capsys, manage,
):
    """A column with a cell the engine would refuse — a folder of the
    project's own at a skill's target — shows that cell's reason and no
    button; the column's Add all is withheld too, because the engine
    composes every skill and refuses whole, so the press could only ever
    refuse. The other agent's column, with nothing blocked, keeps its Add
    all, and the cells still add the unblocked skills one by one.
    """
    alpha, _, _ = three_projects(tmp_path, capsys, client_here)
    first, second = list(sk.HARNESSES.values())[:2]
    root = alpha.resolve()
    other = next(name for name in sk.catalog_names() if name != SKILL)
    (root / second.skills / other).mkdir(parents=True)
    (root / second.skills / other / "ours.md").write_bytes(b"our own skill\n")
    run_ = manage()

    page = project_page(run_, alpha)
    cells = grid_cells(page)
    assert manage_web.CELL_REASONS[manage_web.CELL_FOREIGN] == "Has a folder RadialRig didn't add"
    assert cells[(other, second.label)] == ("note", "Has a folder RadialRig didn't add")
    assert cells[(other, first.label)] == ("off", "Off")
    assert agent_heads(page)[second.label] is None
    assert agent_heads(page)[first.label] is not None
    third = next(name for name in sk.catalog_names() if name not in (SKILL, other))
    assert cells[(third, second.label)] == ("off", "Off")
    assert (other, second.label) not in grid_presses(page)
    # The engine's own answer to the press the screen withheld is the
    # refusal, which is why the screen does not offer it.
    act = manage_web.Action(manage_web.SKILL_ADD_ALL, root=str(root), harness=second.name)
    assert manage_web._skill_add_all(act, dry_run=True).kind == manage_web.REFUSED
    assert cli.main(["skill", "add", "--all", str(root), "--harness", second.name,
                     "--dry-run"]) == report.REFUSED
    capsys.readouterr()
    # Once the folder is out of the way, Add all is back.
    shutil.rmtree(root / second.skills / other)
    assert agent_heads(project_page(run_, alpha))[second.label] is not None


def test_a_file_where_the_skills_folder_goes_blocks_the_whole_column(
    tmp_path, env, client_here, command, capsys, manage,
):
    """A file of the project's own where an agent's skills folder goes
    blocks every skill for that agent, as the engine refuses each one: the
    column's cells say why and carry no button, and its Add all is
    withheld (actions.md row 16; phase 1 review A2)."""
    first, second = list(sk.HARNESSES.values())[:2]
    root = _make_project(tmp_path / "alpha").resolve()
    assert run(capsys, "mount", str(root))[0] == 0
    skills = root / first.skills
    skills.parent.mkdir(parents=True, exist_ok=True)
    skills.write_bytes(b"not a folder\n")
    run_ = manage()

    page = project_page(run_, root)
    cells = grid_cells(page)
    for name in sk.catalog_names():
        assert cells[(name, first.label)] == ("note", "A file is in the way"), name
        assert cells[(name, second.label)] == ("off", "Off"), name
    assert agent_heads(page)[first.label] is None
    assert agent_heads(page)[second.label] is not None
    assert not any(label == first.label for _, label in grid_presses(page))
    # Each press the column withholds is one the engine refuses.
    for name in sk.catalog_names():
        act = manage_web.Action(manage_web.SKILL_ADD, root=str(root), skill=name,
                                harness=first.name)
        assert manage_web._skill_add(act, dry_run=True).kind == manage_web.REFUSED, name
    act = manage_web.Action(manage_web.SKILL_ADD_ALL, root=str(root), harness=first.name)
    assert manage_web._skill_add_all(act, dry_run=True).kind == manage_web.REFUSED


def test_no_skill_press_prints_a_path_twice(
    tmp_path, env, client_here, command, capsys, manage,
):
    """PLAN-0015 constraint 4, over every skill press: the rehearsal and
    the run of the add, the whole-catalog add and the removal, and the
    refusal. The table shows the paths; the sentences beneath it never
    say one of them again."""
    alpha, _, _ = three_projects(tmp_path, capsys, client_here)
    first, second = list(sk.HARNESSES.values())[:2]
    root = str(alpha.resolve())
    other = next(name for name in sk.catalog_names() if name != SKILL)
    foreign = _make_project(tmp_path / "foreign").resolve()
    (foreign / second.skills / SKILL).mkdir(parents=True)
    (foreign / second.skills / SKILL / "ours.md").write_bytes(b"our own skill\n")
    run_ = manage()

    presses = [
        {"action": manage_web.SKILL_REMOVE, "root": root, "skill": SKILL,
         "harness": second.name},
        {"action": manage_web.SKILL_ADD, "root": root, "skill": SKILL,
         "harness": second.name},
        {"action": manage_web.SKILL_ADD, "root": root, "skill": other,
         "harness": first.name},
        {"action": manage_web.SKILL_ADD_ALL, "root": root, "harness": second.name},
        {"action": manage_web.SKILL_ADD_ALL, "root": root},
        {"action": manage_web.SKILL_REMOVE, "root": root, "skill": other},
        {"action": manage_web.SKILL_ADD, "root": str(foreign), "skill": SKILL,
         "harness": second.name},
    ]
    walked = 0
    for fields in presses:
        for confirm in (False, True):
            page = pressed(run_, fields, confirm=confirm)
            drawn = rows(page)
            assert drawn, (fields, confirm)
            paths = {path for _, path, _ in drawn}
            for line in said_lines(page):
                for path in paths:
                    assert path not in line, (fields, confirm, line, path)
            # Every row stands once in the table too.
            assert len(drawn) == len({(verb, path) for verb, path, _ in drawn})
            walked += 1
    assert walked == 2 * len(presses)
    # The engine's own report rows are what the sentence filter drops,
    # whatever reason or width the report rendered them with.
    results = sk.add_catalog_plan(alpha.resolve())
    lines = report.skill_add_reports(results, dry_run=True)
    kept = manage_web._sentences(lines, [d for r in results for d in r.dispositions])
    assert kept and all(not line.startswith(tuple(f"{v:<9} " for v in mnt.VERBS))
                        for line in kept)
    assert run_.running


def test_every_command_stands_under_show_commands_with_its_copy_named(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Every command a press on the screen runs stands under the screen's
    one Show commands switch, in Details, in the order the press runs
    them, each with a Copy button named for its command (actions.md row
    24). Repair's are its two verbs, the removal then the fresh connect."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    assert manage_web.SHOW_COMMANDS == "Show commands"
    for project in (alpha, beta, gamma):
        page = project_page(run_, project)
        body = main_of(page)
        assert body.count(SHOW_COMMANDS_BOX) == 1, project.name
        details = body.split('<details class="settings">', 1)[1].split("</details>", 1)[0]
        assert SHOW_COMMANDS_BOX in details
        copied = []
        for shown, copy, named in COPY_LINE.findall(body):
            line = html.unescape(copy)
            assert html.unescape(shown) == line
            assert html.unescape(named) == manage_web.COPY_NAME.format(command=line)
            assert line.startswith("radialrig ")
            copied.append(line)
        assert copied, project.name
        # Every press's command is the one its own fields name; Repair's
        # are the two verbs it runs, in order.
        for inner in POST_PRESS.findall(body):
            fields = dict(HIDDEN.findall(inner))
            if fields["action"] == manage_web.REPAIR:
                acts = manage_web.repair_steps(ro.verify(project.resolve()))
                assert [act.name for act in acts] == [manage_web.UNCONNECT, manage_web.CONNECT]
                assert repair_commands_in(page) == [act.command for act in acts]
            elif fields["action"] == manage_web.REVERSAL:
                # The removal's, under the foot: every verb it runs, in order.
                acts = manage_web.reversal_steps(ro.verify(project.resolve()))
                foot = body.split('<div class="project-foot">', 1)[1]
                assert [html.unescape(copy) for _, copy, _ in COPY_LINE.findall(foot)] == [
                    act.command for act in acts]
            else:
                act = manage_web.Action(
                    fields["action"], root=fields.get("root", ""),
                    skill=fields.get("skill", ""), client=fields.get("client", ""),
                    harness=fields.get("harness", ""))
                assert act.command in copied, act.command
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert ".screen .commands { display: none; }" in css
    # Only from a box in no closed Details (PLAN-0027 phase 5 review P4).
    assert (".screen:has(.show-commands:checked:not(details:not([open]) *)) "
            ".commands { display: block; }") in css
    assert ".screen:has(.show-commands:checked) .commands" not in css
    js = (packaged_web() / "page.js").read_text(encoding="utf-8")
    assert "button.textContent" in js
    assert run_.running


def test_the_plan_confirm_and_report_pages_keep_the_command_in_full(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Constraint 1: the switch is the redesigned screens' alone. A press's
    plan step, the confirm under it, and the report carry the command
    frame whole, and the Copy button keeps its name there too."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    root = str(alpha.resolve())
    fields = {"action": manage_web.SKILL_ADD, "root": root, "skill": "plan-review"}
    act = manage_web.Action(manage_web.SKILL_ADD, root=root, skill="plan-review")
    plan = pressed(run_, fields)
    done = pressed(run_, fields, confirm=True)
    named = manage_web.COPY_NAME.format(command=act.command)
    frame = setup_web._command(act.command, name=named)
    assert f'aria-label="{html.escape(named, quote=True)}"' in frame
    for name, page in (("plan", plan), ("done", done)):
        body = main_of(page)
        assert "<details" not in body, name
        assert manage_web.SHOW_COMMANDS not in text_of(page), name
        assert body.count(frame) == 1, name
    assert main_of(plan).count(PRIMARY) == 1
    assert main_of(done).count(PRIMARY) == 0
    # Confirm a big change keeps every command its press runs whole, in
    # order, each with its named Copy, behind Details and the switch.
    card = pressed(run_, {"action": manage_web.REVERSAL, "root": root,
                          "from": manage_web.PROJECT})
    details = main_of(card).split("<details>", 1)[1].split("</details>", 1)[0]
    acts = manage_web.reversal_steps(ro.verify(alpha.resolve()))
    assert [html.unescape(c) for _, c, _ in COPY_LINE.findall(details)] == [
        *(act.command for act in acts), f"radialrig unmount {root}"]
    for _, line, name in COPY_LINE.findall(details):
        assert html.unescape(name) == manage_web.COPY_NAME.format(command=html.unescape(line))
    assert SHOW_COMMANDS_BOX in details
    assert run_.running


def test_remove_from_this_project_stands_while_radialrig_has_anything_there(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 5: "Remove RadialRig from this project" sits quiet at
    the foot while RadialRig has files, skills or a connection here, and
    opens Confirm a big change (PLAN-0027 phase 3): its form names the
    screen and carries no `confirm`, so it writes nothing. The screen's
    one amber is its status's fix where it has one. The unmount alone is
    the card's keep box, not a press of this screen (actions.md row 22)."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    bare = _make_project(tmp_path / "bare").resolve()
    run_ = manage()
    for project, amber in ((alpha, 0), (beta, 1), (gamma, 1)):
        page = project_page(run_, project)
        body = main_of(page)
        assert body.count(PRIMARY) == amber, project.name
        assert f'value="{manage_web.UNMOUNT}"' not in body
        foot = body.split('<div class="project-foot">', 1)[1]
        press = POST_PRESS.search(foot)
        assert dict(HIDDEN.findall(press.group(1))) == {
            "token": run_.session.token, "action": manage_web.REVERSAL,
            "root": str(project.resolve()), "from": manage_web.PROJECT}
        assert (f'<button class="link quiet" type="submit">'
                f"{manage_web.REMOVE_PROJECT_LABEL}</button>") in foot
    card = pressed(run_, {"action": manage_web.REVERSAL, "root": str(alpha.resolve()),
                          "from": manage_web.PROJECT})
    assert f"<h1>Remove RadialRig from {alpha.name}?</h1>" in main_of(card)
    assert mnt.is_mounted(alpha.resolve()), "the press wrote something"
    # A folder RadialRig has nothing in shows no removal.
    assert manage_web.REMOVE_PROJECT_LABEL not in main_of(project_page(run_, bare))
    assert manage_web.REMOVE_PROJECT_LABEL == "Remove RadialRig from this project"
    assert run_.running


def test_the_foot_of_projects_holds_the_version_the_log_and_remove_radialrig(
    tmp_path, env, client_here, command, capsys, manage,
):
    """actions.md rows 4, 9 and 10: the foot of Projects says the engine's
    version, holds the screen's Show commands switch, shows where the
    activity log is with Copy, and leads to Remove RadialRig. The old
    listing's framed removal, with its count of projects, is gone with
    the redesign."""
    registered(capsys, _make_project(tmp_path / "one"))
    run_ = manage()
    page = run_.page()
    foot = main_of(page).split('<footer class="foot">', 1)[1].split("</footer>", 1)[0]
    assert (f'<span class="build">'
            f"{manage_web.FOOT_VERSION.format(version=mnt.installed_version())}</span>") in foot
    assert SHOW_COMMANDS_BOX in foot
    log = str(al.log_path())
    assert f"<summary>{manage_web.LOG_LABEL}</summary>" in foot
    assert [html.unescape(copy) for _, copy, _ in COPY_LINE.findall(foot)] == [log]
    (target,) = [t for t in navigations(foot, run_.session.token)]
    assert f"view={manage_web.REMOVAL}" in target
    status, removal, _ = run_.get(target)
    assert status == 200
    assert manage_web.REMOVAL_HEAD in text_of(removal)
    for gone in ("REMOVAL_LABEL", "REMOVAL_WHAT", "REMOVAL_ONE", "REMOVAL_MANY"):
        assert not hasattr(manage_web, gone), gone
    assert run_.running


# --- PLAN-0014 phase 3: a mismatch names its client and offers the repair;
# an unmounted project offers the mount. The done-condition's pins, on the
# same three-project roster, one test per pin.

def repair_commands_in(page: str) -> list[str]:
    """The commands under the status band, in order."""
    band = main_of(page).split('<div class="status-band">', 1)[1].split(
        '<table class="skill-grid">', 1)[0]
    return [html.unescape(copy) for _, copy, _ in COPY_LINE.findall(band)]


def withheld(page: str) -> list[str]:
    """What Details lists in a repair's way, where Repair is withheld."""
    details = main_of(page).split('<details class="settings">', 1)[1].split("</details>", 1)[0]
    if manage_web.REPAIR_BLOCKERS_HEAD not in details:
        return []
    part = details.split(f"<h3>{manage_web.REPAIR_BLOCKERS_HEAD}</h3>", 1)[1].split("</ul>", 1)[0]
    return [html.unescape(item) for item in re.findall(r"<li>([^<]*)</li>", part)]


def repair_commands(root: Path) -> list[str]:
    """The two commands the repair runs, in order, as the page spells them."""
    return [
        manage_web.Action(manage_web.UNCONNECT, root=str(root), client=CLIENT).command,
        manage_web.Action(manage_web.CONNECT, root=str(root), client=CLIENT).command,
    ]


def test_a_lost_link_names_its_client_on_every_surface_and_fills_the_commands(
    tmp_path, env, client_here, command, capsys, manage,
):
    """A lost link is said in `wording.md`'s words on the two redesigned
    screens, naming the client the record claims by its label from the
    table: "link lost" on its row and "lost its link to this project" as
    its status. Repair's two commands stand under the status, filled in,
    in the order the press runs them. Removing it says, on Confirm a big
    change, that the removal leaves the client's settings and the
    project's record, and how to clear it, with no disconnect among its
    commands (PLAN-0027 phase 3). The command line keeps the engine's own
    sentence and its remedy tail with the placeholders."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = beta.resolve()
    facts = ro.verify(root)
    assert facts.claimed == CLIENT
    label = cn.CLIENTS[CLIENT].label
    sentence = ro.mismatch_clause(facts)
    assert sentence == f"{label}'s settings no longer carry the entry this project's record claims"
    run_ = manage()

    listing = run_.page()
    by_name = {name: facts_ for name, _, _, facts_, _ in roster_rows(listing)}
    assert by_name["beta"] == f"{label} link lost, no skills"
    assert sentence not in text_of(listing)

    page = project_page(run_, beta)
    assert status_band(page) == [("repair", f"{label} lost its link to this project.", "Repair")]
    assert repair_commands_in(page) == repair_commands(root)
    assert repair_commands(root) == [f"radialrig unconnect {CLIENT} {root}",
                                     f"radialrig connect {CLIENT} {root}"]
    assert ('<div class="connection"><strong>' + label + "</strong>"
            + '<span class="state"><span class="symbol repair" aria-hidden="true">!</span>'
            f"<span>{manage_web.LINK_LOST_STATE}</span></span></div>") in connections_of(page)
    said = text_of(page)
    assert sentence not in said
    assert "<client>" not in said and "<PATH>" not in said
    assert report.PROJECTS_MISMATCH_TAIL not in said
    assert "{label}" not in said and "{file}" not in said

    card = pressed(run_, {"action": manage_web.REVERSAL, "root": str(root),
                          "from": manage_web.PROJECT})
    said = text_of(card)
    assert (f"{label} already lost its link to this project, so this leaves its "
            "settings alone and keeps this project's connection record. To take the "
            "record out too, repair the link first, then remove.") in said
    assert report.PROJECTS_MISMATCH_TAIL not in said
    copied = [html.unescape(c) for c in re.findall(r'data-copy="([^"]*)"', main_of(card))]
    assert copied == [act.command for act in manage_web.reversal_steps(facts)]
    assert not any(line.startswith("radialrig unconnect") for line in copied)

    code, out, _ = run(capsys, "projects")
    assert code == 0
    assert sentence in out
    assert report.PROJECTS_MISMATCH_TAIL in out
    # The healthy and the unmounted project say nothing of the kind.
    for project in (alpha, gamma):
        assert "lost its link" not in text_of(project_page(run_, project))
    assert run_.running


def test_repair_stands_only_where_its_guard_offers_it(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage,
):
    """Prescription 7: Repair's button shows only where today's repair
    plan would offer one, and where it doesn't the status says why.

    With the client detected, the status band carries Repair, the screen's
    one amber, and the Connections row reads Link lost with no press. With
    `APPDATA` narrowed to a profile holding no client, the status says the
    client isn't on this computer; with the read server's command not
    locatable, it says RadialRig can't find its own rule lookup program.
    Neither offers any press that writes to the client.
    """
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = beta.resolve()
    label = cn.CLIENTS[CLIENT].label

    run_ = manage()
    page = project_page(run_, beta)
    assert status_band(page) == [("repair", f"{label} lost its link to this project.", "Repair")]
    presses = [dict(HIDDEN.findall(inner)) for inner in POST_PRESS.findall(main_of(page))]
    assert {"token": run_.session.token, "action": manage_web.REPAIR, "root": str(root),
            "confirm": "yes", "from": manage_web.PROJECT} in presses
    assert POST_PRESS.search(connections_of(page)) is None
    body = main_of(page)
    assert f'value="{manage_web.CONNECT}"' not in body
    assert f'value="{manage_web.UNCONNECT}"' not in body
    assert body.count(f'value="{manage_web.REPAIR}"') == 1
    assert body.count(PRIMARY) == 1

    # The client not detected: the status says so, and nothing is offered.
    monkeypatch.setenv("APPDATA", str(tmp_path / "no-client"))
    assert cn.detect(cn.CLIENTS[CLIENT]) == ()
    assert ro.verify(root).connected == ro.CONNECTED_MISMATCH
    page = project_page(run_, beta)
    assert status_band(page) == [("repair", manage_web.REPAIR_NO_CLIENT.format(label=label), "")]
    assert manage_web.REPAIR_NO_CLIENT.format(label=label) == (
        f"This project is connected to {label}, but {label} isn't on this computer.")
    for value in (manage_web.REPAIR, manage_web.CONNECT, manage_web.UNCONNECT):
        assert f'value="{value}"' not in main_of(page), value

    # The read server's command not locatable: the same, said its own way.
    monkeypatch.setenv("APPDATA", env["APPDATA"])
    assert cn.detect(cn.CLIENTS[CLIENT])
    monkeypatch.setattr(cn, "_which", lambda name: None)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "nowhere" / "python.exe"))
    monkeypatch.setattr(sys, "argv", [""])
    with pytest.raises(cn.ConnectError):
        cn.read_command()
    page = project_page(run_, beta)
    assert status_band(page) == [("repair", manage_web.REPAIR_NO_PROGRAM.format(label=label), "")]
    assert f'value="{manage_web.REPAIR}"' not in main_of(page)
    assert run_.running


def test_the_repair_plan_carries_both_commands_and_one_primary(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 3's rehearsal: two step reports, both commands in
    full, the one amber under them, and the way back to the project.

    The suite's mismatch leaves the client's file holding the write
    server's entry, so the registration's dry run — read on the settings
    as they stand, before the removal — is the engine's own stale-entry
    refusal, whose sentence is to run the removal first. The page draws
    that step as deferred, under its own heading with the sentence that
    says why and its command, never as a refusal panel over the press it
    then offers. Where the file holds neither entry, both steps rehearse
    clean and neither the heading nor the note is drawn.
    """
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = beta.resolve()
    run_ = manage()
    fields = {"action": manage_web.REPAIR, "root": str(root)}
    before = client_here.read_bytes()

    plan = pressed(run_, fields)
    body = main_of(plan)
    assert client_here.read_bytes() == before, "the rehearsal wrote something"
    assert cn.record_path(root).is_file()
    assert body.startswith(f"<h1>{manage_web.REPAIR_PLAN_HEAD}</h1>")
    assert f"<code>{html.escape(str(root), quote=False)}</code>" in body
    assert [html.unescape(c) for c in re.findall(r'data-copy="([^"]*)"', body)] \
        == repair_commands(root)
    assert "<details" not in body
    assert body.count(PRIMARY) == 1
    assert body.count(f'value="{manage_web.REPAIR}"') == 1
    confirm = next(inner for inner in POST_PRESS.findall(body) if PRIMARY in inner)
    assert dict(HIDDEN.findall(confirm)) == {
        "token": run_.session.token, "action": manage_web.REPAIR,
        "root": str(root), "confirm": "yes"}
    assert GHOST.findall(confirm) == []
    assert manage_web.CONFIRM_LABEL in confirm
    # The removal rehearsed clean; the registration met the entry the
    # removal takes out, and the page says so before the press — as a
    # deferred step, never as the engine's refusal panel.
    assert body.count("<h2") == 2
    assert body.count('<div class="refusal"') == 0
    assert f"<h2>{manage_web.REPAIR_AFTER_HEAD}</h2>" in body
    assert "Refused" not in text_of(plan)
    assert manage_web.REPAIR_ORDER_NOTE in text_of(plan)
    assert body.index(manage_web.REPAIR_ORDER_NOTE) < body.index(PRIMARY)
    # The way back: to this project, then to the listing.
    targets = navigations(plan, run_.session.token)
    assert [t.split("?", 1)[1] for t in targets] == [
        urllib.parse.urlencode([("s", run_.session.token), ("view", manage_web.PROJECT),
                                ("root", str(root))]),
        urllib.parse.urlencode([("s", run_.session.token), ("view", manage_web.ROSTER)]),
    ]
    assert manage_web.PROJECT_BACK_LABEL in text_of(plan)

    # Both entries gone from the file: both steps rehearse clean.
    settings = json.loads(client_here.read_text(encoding="utf-8"))
    for key in list(settings["mcpServers"]):
        if key.startswith(PREFIX):
            del settings["mcpServers"][key]
    client_here.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    assert ro.verify(root).connected == ro.CONNECTED_MISMATCH
    plan = pressed(run_, fields)
    body = main_of(plan)
    assert body.count('<div class="refusal"') == 0
    assert manage_web.REPAIR_AFTER_HEAD not in text_of(plan)
    assert manage_web.REPAIR_ORDER_NOTE not in text_of(plan)
    assert body.count(PRIMARY) == 1
    assert [html.unescape(c) for c in re.findall(r'data-copy="([^"]*)"', body)] \
        == repair_commands(root)
    assert run_.running


def test_the_repair_leaves_the_client_carrying_the_records_entries(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Today's two-step repair's confirm, which the redesigned screens no
    longer post but the plan page still does: the two verbs run in order;
    the client's settings carry the record's entries again, the state
    reads the client's name, and the report is the two reports."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = beta.resolve()
    run_ = manage()
    fields = {"action": manage_web.REPAIR, "root": str(root)}

    done = pressed(run_, fields, confirm=True)
    body = main_of(done)
    assert body.startswith(f"<h1>{manage_web.REPAIRED_HEAD}</h1>")
    assert body.count(PRIMARY) == 0
    assert body.count("<h2") == 2
    assert '<div class="refusal"' not in body
    assert manage_web.ACTION_FAILED_HEAD not in text_of(done)
    assert [html.unescape(c) for c in re.findall(r'data-copy="([^"]*)"', body)] \
        == repair_commands(root)
    # The first verb answered partial — the entry was already gone — and
    # the second ran: both reports stand, in the engine's own words.
    said = text_of(done)
    assert "1 left" in said
    assert f"with {cn.CLIENTS[CLIENT].label}" in said

    assert cn.is_connected(root, CLIENT)
    facts = ro.verify(root)
    assert facts.connected == CLIENT
    assert facts.claimed == CLIENT
    record = record_of(beta)
    servers = json.loads(client_here.read_text(encoding="utf-8"))["mcpServers"]
    entries = cn._recorded_entries(record)
    assert entries
    assert all(servers[key] == entry for key, entry in entries.items())
    # Projects no longer reads it as needing repair, and the command line
    # agrees.
    listing = run_.page()
    assert [status for _, _, status, _, _ in roster_rows(listing)] == [
        "Not set up", "Working", "Working"]
    code, porcelain, _ = run(capsys, "projects", "--porcelain")
    assert code == 0
    assert f"root={root}\tmounted=yes\tconnected={CLIENT}\t" in porcelain
    # The project's screen now offers Disconnect, and no Repair.
    page = main_of(project_page(run_, beta))
    assert f'value="{manage_web.REPAIR}"' not in page
    assert f'value="{manage_web.UNCONNECT}"' in page
    assert run_.running


def test_a_refusal_from_the_first_verb_stops_the_second_and_a_partial_does_not(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage,
):
    """Prescription 3's stop rule, on the run.

    The registration's removal is made to refuse, and to fail, the way
    the composer reads a step's answer — through the runner table — and
    the registration is a recorder that fails the test if it is reached.
    Then the real run: the removal answers partial on this fixture, the
    entry being already gone, and the registration runs.
    """
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = beta.resolve()
    run_ = manage()
    fields = {"action": manage_web.REPAIR, "root": str(root)}
    reached: list[manage_web.Action] = []
    real_connect = manage_web.RUNNERS[manage_web.CONNECT]

    def recorder(act, *, dry_run):
        reached.append(act)
        return real_connect(act, dry_run=dry_run)

    def refusing(act, *, dry_run):
        return manage_web._refused(act, ["Refused: the removal is blocked."])

    def failing(act, *, dry_run):
        return manage_web._failed(act, "the removal could not read the record")

    monkeypatch.setitem(manage_web.RUNNERS, manage_web.CONNECT, recorder)
    for stopper, kind in ((refusing, manage_web.REFUSED), (failing, manage_web.FAILED)):
        monkeypatch.setitem(manage_web.RUNNERS, manage_web.UNCONNECT, stopper)
        before = client_here.read_bytes()
        done = pressed(run_, fields, confirm=True)
        body = main_of(done)
        assert reached == [], kind
        assert body.count("<h2") == 1, kind
        assert body.count(PRIMARY) == 0
        assert client_here.read_bytes() == before
        assert ro.verify(root).connected == ro.CONNECTED_MISMATCH
        assert cn.record_path(root).is_file()
        # The one report is the first verb's, with its command; the
        # second verb's command is not on the page, because it never ran.
        copied = [html.unescape(c) for c in re.findall(r'data-copy="([^"]*)"', body)]
        assert copied == repair_commands(root)[:1], kind
        if kind == manage_web.REFUSED:
            assert '<div class="refusal"' in body
        else:
            assert manage_web.ACTION_FAILED_HEAD in text_of(done)
        # The rehearsal stops in the same place and offers no press.
        plan = main_of(pressed(run_, fields))
        assert reached == []
        assert plan.count(PRIMARY) == 0
        assert plan.count("<h2") == 1

    # The real removal answers partial here, and the registration runs.
    monkeypatch.setitem(manage_web.RUNNERS, manage_web.UNCONNECT,
                        manage_web._unconnect)
    done = pressed(run_, fields, confirm=True)
    assert [act.name for act in reached] == [manage_web.CONNECT]
    body = main_of(done)
    assert body.count("<h2") == 2
    assert "1 left" in text_of(done)
    assert ro.verify(root).connected == CLIENT
    assert run_.running


def test_a_partial_first_answer_is_the_engines_partial(
    tmp_path, env, client_here, command, capsys, manage,
):
    """The stop rule read off the states themselves: the first step's
    outcome is the engine's partial code, the kind is a report, and the
    composer goes on to the second."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = beta.resolve()
    state = manage_web.perform_repair(str(root))
    assert state.kind == manage_web.REPAIRED
    _, steps = state.result
    assert [step.result.name for step in steps] == [
        manage_web.UNCONNECT, manage_web.CONNECT]
    assert [step.kind for step in steps] == [manage_web.DONE, manage_web.DONE]
    assert steps[0].outcome == report.PARTIAL
    assert steps[1].outcome == report.OK
    assert ro.verify(root).connected == CLIENT
    # And on a repaired project the sequence has no step left.
    state = manage_web.plan_repair(str(root))
    assert state.result[1] == ()
    assert manage_web.repair_steps(ro.verify(root)) == ()


def test_a_record_naming_no_known_client_shows_the_file_and_no_press(
    tmp_path, env, client_here, command, capsys, manage,
):
    """A record this build cannot read a client off: the row still reads
    Needs repair, and the project's status says RadialRig can't read the
    record, with the record's path in Details and no press — no repair, no
    connect. Its removal takes out the files alone and says the record
    stays (PLAN-0027 phase 3). The command line names the record file in
    the engine's own sentence."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = beta.resolve()
    record = cn.record_path(root)
    body = json.loads(record.read_text(encoding="utf-8"))
    body["client"] = "some-other-client"
    record.write_text(json.dumps(body, indent=2), encoding="utf-8")
    facts = ro.verify(root)
    assert (facts.connected, facts.claimed) == (ro.CONNECTED_MISMATCH, "")
    sentence = f"This project's record names no client this build knows: see {record}"
    assert ro.mismatch_clause(facts) == sentence
    run_ = manage()

    listing = run_.page()
    assert [(status, fix) for name, _, status, _, fix in roster_rows(listing)
            if name == "beta"] == [("Needs repair", "")]

    page = project_page(run_, beta)
    assert status_band(page) == [("repair", manage_web.REPAIR_NO_RECORD, "")]
    assert manage_web.REPAIR_NO_RECORD == (
        "RadialRig can't read this project's connection record, so it can't "
        "repair the link from here.")
    assert withheld(page) == [manage_web.RECORD_AT.format(path=record)]
    assert repair_commands_in(page) == []
    body = main_of(page)
    for value in (manage_web.REPAIR, manage_web.CONNECT, manage_web.UNCONNECT):
        assert f'value="{value}"' not in body, value
    assert body.count(PRIMARY) == 0

    card = pressed(run_, {"action": manage_web.REVERSAL, "root": str(root),
                          "from": manage_web.PROJECT})
    assert "keeps this project's connection record" in text_of(card)
    assert [html.unescape(c) for c in re.findall(r'data-copy="([^"]*)"', main_of(card))] \
        == [f"radialrig unmount {root}"]

    code, out, _ = run(capsys, "projects")
    assert code == 0
    assert sentence in out
    code, porcelain, _ = run(capsys, "projects", "--porcelain")
    assert f"root={root}\tmounted=yes\tconnected={report.PROJECTS_MISMATCH}\t" in porcelain

    # A record that will not parse reads the same way.
    record.write_bytes(b"{ not json\n")
    facts = ro.verify(root)
    assert (facts.connected, facts.claimed) == (ro.CONNECTED_MISMATCH, "")
    assert status_band(project_page(run_, beta)) == [
        ("repair", manage_web.REPAIR_NO_RECORD, "")]
    assert run_.running


def test_a_project_not_set_up_offers_set_up(
    tmp_path, env, client_here, command, capsys, manage,
):
    """actions.md row 11: "Not set up" is the status, and Set up beside
    it, the screen's one amber, is a form to the walkthrough on this
    folder, which writes nothing until its own press. A project that is
    set up has none of it."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = gamma.resolve()
    run_ = manage()

    page = project_page(run_, gamma)
    assert status_band(page) == [("new", "RadialRig is not set up in this project.", "Set up")]
    body = main_of(page)
    assert body.count(PRIMARY) == 1
    band = body.split('<div class="status-band">', 1)[1].split('<table class="skill-grid">', 1)[0]
    action, inner = GET_FORM.search(band).groups()
    assert action == PATH
    assert dict(HIDDEN.findall(inner)) == {
        "s": run_.session.token, "view": manage_web.SETUP, "root": str(root)}
    assert f'<button class="primary" type="submit">{manage_web.SETUP_BUTTON}</button>' in inner
    target = next(t for t in navigations(page, run_.session.token)
                  if f"view={manage_web.SETUP}" in t)
    status_code, walkthrough, _ = run_.get(target)
    assert status_code == 200
    assert f"<h1>Set up RadialRig in {gamma.name}</h1>" in walkthrough
    assert f'value="mount">{setup_web.SETUP_LABEL}</button>' in walkthrough
    assert f'data-copy="radialrig mount {root}"' in walkthrough
    assert not mnt.is_mounted(root), "the press wrote something"
    for gone in ("MOUNT_LABEL", "NO_MOUNT_NEEDED"):
        assert not hasattr(manage_web, gone), gone
    # A project that is set up has none of this.
    for project in (alpha, beta):
        mounted = project_page(run_, project)
        assert manage_web.SETUP_BUTTON not in [fix for _, _, fix in status_band(mounted)]
        assert f"view={manage_web.SETUP}" not in "".join(
            navigations(mounted, run_.session.token))
    assert run_.running


def test_no_offered_press_is_refused_on_a_project_not_set_up(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Every one-press change a screen not set up offers stands without
    the mount. Each is posted as the screen drew it, and each answers with
    a result line that isn't a failure."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = gamma.resolve()
    run_ = manage()
    page = project_page(run_, gamma)
    presses = [dict(HIDDEN.findall(inner)) for inner in POST_PRESS.findall(main_of(page))]
    # The one-press changes carry `confirm`; the removal's first press
    # opens Confirm a big change and carries none (PLAN-0027 phase 3).
    now = [fields for fields in presses if fields.get("confirm")]
    # Per agent, Add all and one cell per skill; then Disconnect.
    assert len(now) == (len(sk.catalog_names()) + 1) * len(sk.HARNESSES) + 1
    assert {fields["action"] for fields in now} == {
        manage_web.SKILL_ADD, manage_web.SKILL_ADD_ALL, manage_web.UNCONNECT}
    for fields in now:
        fields.pop("token")
        done = pressed(run_, fields)
        tone, sentence, _, _ = result_of(done)
        assert tone == manage_web.RESULT_DONE, (fields, sentence)
        assert "Couldn't" not in sentence, fields
        assert f"<h1>{root.name}</h1>" in main_of(done)
    facts = ro.verify(root)
    assert not facts.mounted
    assert facts.skills == tuple(sorted(sk.catalog_names()))
    assert facts.connected == ro.CONNECTED_NO
    assert run_.running


# --- the ruling: an unreadable roster is named ------------------------

@pytest.mark.parametrize("body", [
    b"{ not json\n",
    b'{\n  "schema": 99,\n  "projects": []\n}\n',
])
def test_an_unreadable_roster_is_named_on_the_page_and_not_by_the_cli(
    tmp_path, env, capsys, manage, body,
):
    """Section 2's ruling, both halves.

    The page names the file, because a standing surface showing an empty
    machine while a roster file it cannot read stands on disk would be
    showing a machine other than this one. The command line goes on
    reading it as empty with no remark, on the plan's letter: `projects`
    is a verb, and a broken roster changes nothing it does.
    """
    path = roster_file(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)

    page = manage().page()

    said = text_of(page)
    assert manage_web.UNREADABLE_NOTICE.format(path=path) in said
    assert str(path) in said
    assert roster_rows(page) == []
    assert path.read_bytes() == body, "the page wrote the roster file"

    code, out, err = run(capsys, "projects")
    assert (code, err) == (0, "")
    assert str(path) not in out
    assert out.splitlines()[0] == f"{report.PROJECTS_HEAD} - 0 listed."
    assert path.read_bytes() == body, "the command line wrote the roster file"


def test_a_readable_roster_is_named_nowhere(tmp_path, env, capsys, manage):
    assert run(capsys, "mount", str(_make_project(tmp_path / "alpha")))[0] == 0
    said = text_of(manage().page())
    assert str(roster_file(env)) not in said
    assert "not one this build can read" not in said


# --- local only, token only -------------------------------------------

def test_tokens_and_origins_guard_the_manage_page(manage):
    run_ = manage()
    token = run_.session.token

    assert run_.get(PATH)[0] == 403
    assert run_.get(f"{PATH}?s=not-the-token")[0] == 403

    first, _, headers = run_.get(f"{PATH}?t={run_.session.launch_token}")
    assert first == 303
    second, body, _ = run_.get(f"{PATH}?t={run_.session.launch_token}")
    assert second == 403
    assert "already been used" in body
    assert run_.get(headers["Location"])[0] == 200

    for extra in (
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ):
        assert run_.get(f"{PATH}?s={token}", headers=extra)[0] == 403, extra
        # A cross-origin close would stop the server from another page.
        assert run_.post({"token": token, "action": "close"},
                         headers=extra)[0] == 403, extra
    assert run_.running

    assert run_.get(f"{PATH}?s={token}", headers={"Host": "radialrig.example"})[0] == 403
    for path in ("/", "/setup", f"/manage/?s={token}", f"/../etc/passwd?s={token}"):
        status, body, _ = run_.get(path)
        assert status == 404, path
        assert "<a href" not in body.lower(), path


def test_the_manage_refusals_name_this_page_and_never_the_setup_one(
    manage, monkeypatch, capsys,
):
    """One refusal path, two surfaces, and each says its own name.

    The bodies are `setup_web`'s, inherited rather than copied, so a
    refusal here used to send a reader to run `radialrig setup` — and the
    spent-link remedy told them to run it for a fresh manage link, which
    it does not give. Every one of the five is driven for real and read
    off the wire.
    """
    def said(template: str) -> str:
        return setup_web.worded(
            template, surface=manage_web.SURFACE, page=manage_web.PAGE_NAME) + "\n"

    run_ = manage()
    reached = {
        setup_web.REFUSE_TOKEN: run_.get(PATH),
        setup_web.REFUSE_NOT_FOUND: run_.get("/"),
        setup_web.REFUSE_ORIGIN: run_.get(
            f"{PATH}?s={run_.session.token}",
            headers={"Origin": "http://evil.example"}),
    }
    assert run_.get(f"{PATH}?t={run_.session.launch_token}")[0] == 303
    reached[setup_web.REFUSE_SPENT] = run_.get(
        f"{PATH}?t={run_.session.launch_token}")

    # The error boundary's own body, reached the way one is reached: a
    # request the page could not answer.
    def boom(*args, **kwargs):
        raise RuntimeError("the listing went away")

    # Scoped, never undone whole: `undo()` would also lift conftest's
    # profile isolation for the rest of this test and its teardown, and
    # the page's stop event would then land in the real log (PLAN-0018
    # phase 2, Invariant 7).
    with monkeypatch.context() as patch:
        patch.setattr(manage_web, "roster_state", boom)
        reached[setup_web.INTERNAL] = run_.get(f"{PATH}?s={run_.session.token}")

    assert len(reached) == 5, "two refusals share one wording"
    for template, (status, body, _) in reached.items():
        assert status in (403, 404, 500), template
        assert body == said(template), template
        assert "manage" in body, template
        assert "setup" not in body, template

    assert said(setup_web.REFUSE_SPENT).strip().endswith(
        "Run radialrig manage again for a new one.")
    assert f"{manage_web.SURFACE}: RuntimeError:" in capsys.readouterr().err
    assert run_.running


def test_the_manage_page_carries_no_external_reference(manage):
    run_ = manage()
    page = run_.page()
    assert not EXTERNAL_REF.search(page)
    assert not EXTERNAL_CSS.search(page)
    assert "@font-face" in page
    assert "data:font/woff2;base64," in page
    assert page.count("<script") == 1

    headers = run_.get(f"{PATH}?s={run_.session.token}")[2]
    policy = headers["Content-Security-Policy"]
    assert "default-src 'none'" in policy
    assert "connect-src 'self'" in policy
    assert "form-action 'self'" in policy
    assert headers["Referrer-Policy"] == "strict-origin"
    # The close beacon posts to this page's own route, not to /setup.
    assert f'data-endpoint="{PATH}"' in page


# --- lifecycle and exit codes -----------------------------------------

def test_manage_exits_zero_on_close_and_one_when_it_cannot_bind(
    manage, monkeypatch, capsys,
):
    run_ = manage()
    run_.page()
    status, _, _ = run_.post({"token": run_.session.token, "action": "close"})
    assert status == 200
    assert run_.wait() == report.OK
    assert manage_web.STOPPED_LINE in run_.stream.getvalue()

    # Port 0 never fails on its own, so the bind failure is injected.
    def refuse(*args, **kwargs):
        raise OSError("the address is already in use")

    monkeypatch.setattr(manage_web, "ThreadingHTTPServer", refuse)
    stream = io.StringIO()
    assert manage_web.serve(open_browser=False, stream=stream) == report.ERROR
    assert "could not start the local server" in capsys.readouterr().err
    assert stream.getvalue() == "", "a server that never bound printed a URL"


# --- D8: a reload is not the page closing -----------------------------

def test_a_reload_does_not_stop_the_manage_server(manage):
    """The close a reload sends is posted by a document already replaced.

    The order here is the browser's rather than this test's invention: on
    a reload the new document's GET is served first and the old
    document's `pagehide` fires its close beacon after it. That is why a
    timer armed by the close had nothing left to cancel, and why what
    tells the two apart is the id the close carries rather than when it
    arrives.
    """
    run_ = manage()
    token = run_.session.token
    first = render_id(run_.page())
    reloaded = render_id(run_.page())        # F5: the document that replaced it
    assert int(reloaded) == int(first) + 1

    status, body, _ = run_.post({
        "token": token, "action": "close", manage_web.RENDER_FIELD: first,
    })
    assert (status, body) == (200, "")
    assert run_.running

    # An id this run never issued is stale for the same reason: it is not
    # the document standing now.
    status, body, _ = run_.post({
        "token": token, "action": "close", manage_web.RENDER_FIELD: "9999",
    })
    assert (status, body) == (200, "")
    assert run_.running

    # A close with no valid token keeps the refusal it always had; being
    # stale is not a way to be answered where a request is not entitled
    # to an answer at all.
    status, refused, _ = run_.post({
        "token": "not-the-token", "action": "close",
        manage_web.RENDER_FIELD: first,
    })
    assert status == 403
    assert "manage" in refused

    # And the page the reload landed on is still served by this server.
    assert manage_web.ROSTER_HEAD in text_of(run_.page())
    assert run_.running


def test_a_document_whose_response_never_arrived_replaces_nothing(
    manage, monkeypatch,
):
    """The id a burned render used to take with it.

    A GET is composed and then written, and the write can fail: the peer
    sends an RST mid-write, the reader presses stop, a reload is
    abandoned before its bytes land. Nobody is holding that document. A
    session that had already moved to its id would call the document
    still on screen stale, drop the genuine close that document sends,
    and stand there with nothing left to close it — a terminal-run server
    leaked, on a machine where the reader did nothing wrong.

    So the id becomes the latest in the send path, after the write
    raised nothing, and a write that raised commits none.
    """
    run_ = manage()
    token = run_.session.token
    on_screen = render_id(run_.page())

    dropped: list[str] = []
    real_send = setup_web.SetupHandler._send

    def aborted(self, code, body, ctype, extra=()):
        # The peer went away mid-write. Only the document is lost: a
        # refusal on the way back out is left alone, because this is the
        # send that fails, not the socket forever after.
        if "html" in ctype and not dropped:
            dropped.append(ctype)
            raise ConnectionAbortedError(
                10053, "an established connection was aborted")
        return real_send(self, code, body, ctype, extra)

    monkeypatch.setattr(setup_web.SetupHandler, "_send", aborted)

    # `Connection: close` so the server drops the socket the moment the
    # request is done rather than waiting out its read timeout; the
    # client sees the same nothing a peer that hung up would.
    with pytest.raises((http.client.HTTPException, OSError)):
        run_.get(f"{PATH}?s={token}", headers={"Connection": "close"})
    assert dropped, "the second GET was never composed"

    # The document on screen is still this session's latest, so its close
    # is the genuine one it always was and it stops the server.
    status, body, _ = run_.post({
        "token": token, "action": "close", manage_web.RENDER_FIELD: on_screen,
    })
    assert (status, body) == (200, "")
    assert run_.wait() == report.OK
    assert manage_web.STOPPED_LINE in run_.stream.getvalue()


def test_a_close_from_the_document_on_screen_stops_the_server(manage):
    """A genuine close is always current: nothing replaced it."""
    run_ = manage()
    current = render_id(run_.page())
    status, body, _ = run_.post({
        "token": run_.session.token, "action": "close",
        manage_web.RENDER_FIELD: current,
    })
    assert (status, body) == (200, "")
    assert run_.wait() == report.OK
    assert manage_web.STOPPED_LINE in run_.stream.getvalue()


def test_a_close_carrying_no_render_id_stops_the_server(manage):
    """What a close has always been here, unchanged.

    The desktop shell posts one to quiesce this engine before it closes
    its window, and a probe typed at a terminal posts the same thing.
    Neither is a document, so neither has an id to carry — and both are
    sent while a document that does carry one is standing, which is the
    state a defense reading ids too eagerly would drop them in.
    """
    run_ = manage()
    run_.page()
    render_id(run_.page())
    status, body, _ = run_.post({"token": run_.session.token, "action": "close"})
    assert (status, body) == (200, "")
    assert run_.wait() == report.OK
    assert manage_web.STOPPED_LINE in run_.stream.getvalue()


def test_the_page_posts_the_render_id_it_was_rendered_with(manage):
    """The other half of D8, in the one script the page carries.

    The server can only drop a stale close if the page sends the id it
    was rendered with, and the page may only send one when it has one:
    the setup surface stamps the same id since PLAN-0017 phase 4, and its
    own test pins the stamped form.
    """
    page = manage().page()
    js = (packaged_web() / "page.js").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert js in page
    assert f'root.getAttribute("{manage_web.RENDER_ATTR}")' in js
    assert f'body += "&{manage_web.RENDER_FIELD}="' in js


def test_a_document_with_no_root_element_to_stamp_is_a_hard_stop():
    """The seam fails where it breaks, rather than quietly serving no id.

    The stamp walks over the shared document builder, which the setup
    surface owns. A change there that renamed the root element would
    leave this replacing nothing, every document would go out with no id,
    every close would read as current, and F5 would kill the server again
    — with nothing anywhere saying so.
    """
    with pytest.raises(RuntimeError):
        manage_web._stamped("<!doctype html><body>no root here</body>", "1")
    assert manage_web._ROOT_TAG in manage_web._stamped(
        f"<!doctype html>{manage_web._ROOT_TAG}lang=\"en\">", "1")


# --- D10: a peer that hangs up is not a fault ------------------------

def test_a_loopback_hang_up_is_absorbed_and_bound_on_the_instance(
    manage, monkeypatch, capsys,
):
    """A browser abandoning its beacon is not something to print about.

    The absorber is bound on the server `serve` builds rather than
    declared as a subclass, and that is load-bearing rather than a taste:
    the bind-failure test replaces this module's own `ThreadingHTTPServer`
    name, and a subclass would have captured the real class at import
    time and gone on binding a real port while that test waited for a
    refusal. So the recording stand-in below is exactly what that test
    does, and the server it hands back is asserted to be the real class
    carrying an instance attribute.
    """
    made: list = []
    real = manage_web.ThreadingHTTPServer

    def recording(*args, **kwargs):
        httpd = real(*args, **kwargs)
        made.append(httpd)
        return httpd

    monkeypatch.setattr(manage_web, "ThreadingHTTPServer", recording)
    run_ = manage()
    assert len(made) == 1
    httpd = made[0]
    assert type(httpd) is real, "the server is a subclass"
    assert "handle_error" in vars(httpd), "the absorber is not on the instance"

    def hang_up(error: BaseException) -> None:
        try:
            raise error
        except BaseException:  # noqa: BLE001 - the raise above is the point
            httpd.handle_error(object(), (setup_web.HOST, 0))

    def on_a_handler_thread(error: BaseException) -> None:
        thread = threading.Thread(target=hang_up, args=(error,), daemon=True)
        thread.start()
        thread.join(TIMEOUT)
        assert not thread.is_alive(), error

    # Whenever it arrives, and in every spelling of it. The third is the
    # same fault as the first: a write to a socket the peer already
    # closed, spelled `BrokenPipeError` where this page is served by a
    # browser on a machine that is not Windows.
    for error in (
        ConnectionAbortedError(10053, "an established connection was aborted"),
        ConnectionResetError(10054, "the connection was reset by the peer"),
        BrokenPipeError(32, "broken pipe"),
    ):
        on_a_handler_thread(error)
        assert capsys.readouterr().err == "", error

    # Anything else still reports the way it always did.
    on_a_handler_thread(RuntimeError("the socket layer went away"))
    assert "RuntimeError" in capsys.readouterr().err

    # And the server the absorbed hang-ups happened on goes on serving.
    assert manage_web.ROSTER_HEAD in text_of(run_.page())
    assert run_.running


def test_a_hang_up_inside_a_request_is_absorbed_rather_than_reported(
    manage, monkeypatch, capsys,
):
    """The other place a hang-up surfaces, which never reached the absorber.

    A peer that goes away while its POST body is being read raises inside
    the request, and the inherited `do_POST` catches it before the server
    ever sees it — so the server's own absorber is not on that path at
    all. What was on it printed a line to stderr for something no reader
    did, and then tried to write a refusal to a socket that was gone.

    Absorbed here on the same terms: nothing on stderr, nothing written
    back, the server still standing. Every other exception reports the
    way it always did.
    """
    run_ = manage()
    raises: list[BaseException] = []

    def hung_up(self, length):
        # The body is read to its declared length before the stub raises,
        # so the answer the server writes is not discarded by a close over
        # unread bytes: the tests-02 reset, on a same-origin path.
        self.rfile.read(length)
        if raises:
            raise raises[0]
        return ""

    monkeypatch.setattr(manage_web.ManageHandler, "_read_form", hung_up)

    def post_and_hang_up(error: BaseException):
        raises[:] = [error]
        try:
            return run_.post(
                {"token": run_.session.token, "action": "close"},
                headers={"Connection": "close"},
            )
        finally:
            raises.clear()

    for error in (
        ConnectionAbortedError(10053, "an established connection was aborted"),
        ConnectionResetError(10054, "the connection was reset by the peer"),
        BrokenPipeError(32, "broken pipe"),
    ):
        with pytest.raises((http.client.HTTPException, OSError)):
            post_and_hang_up(error)
        assert capsys.readouterr().err == "", error
        assert run_.running, error

    # Anything else is still a fault, still named on stderr, and still
    # answered with the refusal the error boundary has always sent.
    status, said, _ = post_and_hang_up(RuntimeError("the roster went away"))
    assert status == 500
    assert "RuntimeError" in capsys.readouterr().err
    assert manage_web.SURFACE in said

    # And the page is still served by a server that never stopped.
    assert manage_web.ROSTER_HEAD in text_of(run_.page())
    assert run_.running


# --- D11: the field the desktop app's folder picker writes into -------

def test_the_add_project_field_keeps_the_id_the_picker_writes_into(manage):
    """One id, pinned as a machine contract beside the URL line.

    The desktop shell's folder picker evaluates a single DOM write
    against this field and dispatches an input event on it, so its id is
    an agreement between two programs rather than a styling hook. The
    picker fills the field a typed path already reaches; it adds
    convenience and no behavior, which is why there is one field here and
    not a second one for it.
    """
    page = manage().page()
    assert '<input id="root" name="root"' in page
    assert page.count('id="root"') == 1
    assert page.count('for="root"') == 1
    said = text_of(page)
    assert manage_web.ADD_FIELD_LABEL in said
    # The field a picker fills is the field a path is typed into, so it
    # is never read-only: this page is served to a plain browser as
    # readily as to the shell that carries a picker.
    assert "readonly" not in page
    assert f'placeholder="{manage_web.ADD_FIELD_PLACEHOLDER}"' in page
    # The Browse button ships hidden and carries the id the shell's
    # reveal script looks for - the second half of the same machine
    # contract. Only the desktop shell can answer the button, so only
    # the desktop shell shows it.
    assert 'id="browse" hidden' in page
    assert page.count('id="browse"') == 1
    # page.js asks the shell through the document's name; the marker is
    # the agreement's third pin, spelled the way picker.rs spells it.
    script = (packaged_web() / "page.js").read_text(encoding="utf-8")
    assert '"radialrig-app.pick-folder"' in script
    assert 'getElementById("browse")' in script


def test_projects_reads_the_way_the_design_writes_it(
    tmp_path, env, capsys, manage,
):
    """The redesigned Projects, word for word (`wording.md`): the masthead,
    the heading with Add a project at its end, one row per project, and
    the foot. No count in the heading, no command shown until Show
    commands is ticked, and no plan table."""
    assert run(capsys, "mount", str(_make_project(tmp_path / "alpha")))[0] == 0

    page = manage().page()
    body = main_of(page)
    said = text_of(page)

    assert f'<span class="name">{manage_web.TITLE}</span>' in page
    assert manage_web.TITLE == "RadialRig"
    assert body.startswith('<div class="screen projects"><header class="page-head">'
                           f"<h1>{manage_web.ROSTER_HEAD}</h1>")
    assert roster_rows(page) == [("alpha", str(tmp_path.resolve()), "Working",
                                  "No skills" if not cn.installed() else
                                  "No connections or skills", "")]
    assert "<table" not in body
    assert '<div class="cmd">' not in body
    assert "radialrig projects" not in said
    assert report.PROJECTS_HEAD not in said
    # The folder field behind Add a project: its label for a reader by
    # ear, the picker's button beside it, hidden until the app shows it,
    # and Continue, rendered enabled for the script to hold.
    assert (f'<label class="sr" for="root">{manage_web.ADD_FIELD_LABEL}</label>') in body
    assert (f'<button type="button" id="browse" hidden>{manage_web.BROWSE_LABEL}'
            "</button>") in body
    assert manage_web.ADD_LABEL == "Continue"
    assert not re.search(r"<[^>]*\sdisabled\b[^>]*>", body)
    for gone in ("ADD_LEDE", "ADD_PICKER_NOTE", "ROSTER_COLUMNS", "OPEN_LABEL"):
        assert not hasattr(manage_web, gone), gone
    # And the foot, last.
    assert body.endswith("</footer></div>")
    assert manage_web.REMOVE_ALL_LABEL in body.split('<footer class="foot">', 1)[1]


def test_the_first_set_up_takes_the_amber_when_nothing_else_needs_it(
    tmp_path, env, capsys, manage,
):
    """Projects' one amber, with no row to repair or clean up and nothing
    behind, is the first row's Set up, as mockups 02-updates-done and
    02-projects-repaired draw it (PLAN-0027 phase 5). A row that needs
    repair or an update line keeps it, as before."""
    alpha, beta, gamma = (_make_project(tmp_path / name)
                          for name in ("alpha", "beta", "gamma"))
    for root in (alpha, beta, gamma):
        assert run(capsys, "mount", str(root))[0] == 0
    for root in (beta, gamma):
        assert run(capsys, "unmount", str(root))[0] == 0

    page = manage().page()
    body = main_of(page).replace(
        f'<button class="primary" type="submit">{manage_web.ADD_LABEL}</button>', "")
    assert [(name, status, fix) for name, _, status, _, fix in roster_rows(page)] == [
        ("beta", "Not set up", "Set up"), ("gamma", "Not set up", "Set up"),
        ("alpha", "Working", "")]
    assert body.count(PRIMARY) == 1
    assert f'{PRIMARY} aria-label="Set up beta" type="submit">' in body
    assert '<button aria-label="Set up gamma" type="submit">' in body


def test_the_server_survives_navigation_and_outcomes_never_leak(
    tmp_path, env, capsys, manage,
):
    """A view change is a form submission, never a link.

    `page.js` sends the close beacon on any departure that is not a
    submission, so one bare anchor would stop a standing server the first
    time a reader changed view. A press that refuses and a press that
    fails are answered the same way: in the page's own sentences, on a
    page that goes on standing, and with the exit code untouched.
    """
    assert run(capsys, "mount", str(_make_project(tmp_path / "alpha")))[0] == 0

    run_ = manage()
    pages = [run_.page()]
    targets = navigations(pages[0], run_.session.token)
    assert len(targets) >= 3, "the roster view offers no navigation"
    for target in targets:
        status, page, _ = run_.get(target)
        assert status == 200, target
        pages.append(page)
        assert run_.running, target

    # A refusal: a key this build never writes is not this verb's to take.
    status, refused, _ = run_.post({
        "token": run_.session.token, "action": manage_web.ORPHAN,
        "client": CLIENT, "key": "someone-elses", "confirm": "yes",
    })
    assert status == 200
    assert report.ORPHAN_REFUSAL_HEAD in text_of(refused)
    pages.append(refused)

    # And a failure: a folder that is not on this disk.
    status, failed, _ = run_.post({
        "token": run_.session.token, "action": manage_web.UNMOUNT,
        "root": str(tmp_path / "never-was"), "confirm": "yes",
    })
    assert status == 200
    assert manage_web.NO_DIRECTORY.format(path=tmp_path / "never-was") \
        in text_of(failed)
    pages.append(failed)
    assert run_.running

    for page in pages:
        # The one anchor the page may carry is a same-document fragment
        # (PLAN-0014 phase 1): it leaves nothing, so it fires no
        # `pagehide` and the close beacon stays quiet.
        for href in ANCHOR.findall(page):
            assert href.startswith("#"), href
        assert 'data-terminal="0"' in page
    assert run_.session.outcome == report.OK

    status, _, _ = run_.post({"token": run_.session.token, "action": "close"})
    assert status == 200
    assert run_.wait() == report.OK


def test_a_navigation_to_a_folder_that_is_gone_reads_as_a_navigation(
    tmp_path, env, capsys, manage,
):
    """Nothing was pressed here, so nothing failed.

    The roster's own folder field is where a reader reaches this, by
    typing a path with a letter wrong in it. The press-failure head would
    be describing a press that never happened; the head says what did
    happen, and the command line's own sentence names the path under it.
    """
    missing = tmp_path / "never-was"
    quoted = urllib.parse.quote(str(missing))
    run_ = manage()

    said = text_of(run_.page(f"&view={manage_web.PROJECT}&root={quoted}"))
    assert manage_web.MISSING_HEAD in said
    assert manage_web.NO_DIRECTORY.format(path=missing) in said
    assert manage_web.ACTION_FAILED_HEAD not in said
    # Add a project says it on its own card, Folder not found, with the
    # folder field to mend the path in (PLAN-0027 phase 2).
    said = text_of(run_.page(f"&view={manage_web.SETUP}&root={quoted}"))
    assert manage_web.NOT_FOUND_HEAD in said
    assert manage_web.NOT_FOUND_SAID in said
    assert str(missing) in said
    assert manage_web.ACTION_FAILED_HEAD not in said

    # A press that really did fail still carries the press's own head.
    assert run(capsys, "mount", str(_make_project(tmp_path / "host")))[0] == 0
    status, failed, _ = run_.post({
        "token": run_.session.token, "action": manage_web.UNMOUNT,
        "root": str(missing), "confirm": "yes",
    })
    assert status == 200
    said = text_of(failed)
    assert manage_web.ACTION_FAILED_HEAD in said
    assert manage_web.NO_DIRECTORY.format(path=missing) in said
    assert manage_web.MISSING_HEAD not in said

    # And a field left empty names no folder at all, so it answers with
    # the listing rather than with a sentence that stops at its colon.
    for name in (manage_web.PROJECT, manage_web.SETUP):
        said = text_of(run_.page(f"&view={name}&root="))
        assert "no such directory" not in said, name
        assert manage_web.ROSTER_HEAD in said, name
    assert run_.running


def test_a_navigation_naming_a_relative_folder_opens_no_view_on_it(
    tmp_path, monkeypatch, env, capsys, manage,
):
    """A relative field names this server's own directory and nothing else.

    `.` is a directory, and it resolves to wherever the process is
    standing — so a project view opened on it used to read that directory
    as a project and offer every press over it, and the walkthrough used
    to open on it and offer to seed it. Both views take the guard every
    press takes, so a field that is not a full path answers with the
    listing, where a reader picks a project the roster names.

    The working directory is a mounted project here, so a view that did
    open on it would have something to show and something to offer.
    """
    here = _make_project(tmp_path / "cwd-project").resolve()
    assert run(capsys, "mount", str(here))[0] == 0
    monkeypatch.chdir(here)

    run_ = manage()
    for name in (manage_web.PROJECT, manage_web.SETUP):
        for field in (".", ".."):
            page = run_.page(f"&view={name}&root={field}")
            said = text_of(page)
            # The listing's own heading, and neither of the two views that
            # stand on one project: the walkthrough carries the way back
            # to the listing, and the project page carries its status
            # band (PLAN-0027 phase 1); the listing carries neither.
            assert f"<h1>{manage_web.ROSTER_HEAD}</h1>" in main_of(page), (name, field)
            assert manage_web.ROSTER_BACK_LABEL not in said, (name, field)
            assert '<div class="status-band">' not in page, (name, field)
            assert "no such directory" not in said, (name, field)
    assert run_.running


def test_the_command_dispatches_manage(monkeypatch):
    seen: dict = {}

    def fake_serve(**kwargs):
        seen.clear()
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(manage_web, "serve", fake_serve)
    assert cli.main(["manage", "--no-browser"]) == 0
    assert seen == {"open_browser": False}
    assert cli.main(["manage"]) == 0
    assert seen == {"open_browser": True}


# --- the brand and the words ------------------------------------------
# The setup page's own brand tests, over every state this page can
# render. The states are collected once, by `every_state` below, so a
# state a later phase adds is swept by all of these rather than by
# whichever one remembered to reach for it.

# D-33 as the owner amended it on 2026-08-18: a whole-word check over
# what the page says. The "hand" inside session-handoff is part of a
# skill's name, not a claim about who typed a change. The strict
# substring ban over the source of cli.py is untouched by the amendment.
FORBIDDEN = ("hand", "manual", "human")
FORBIDDEN_WORD = re.compile(r"\b(?:" + "|".join(FORBIDDEN) + r")\b")

# The brand's own vocabulary. "safer" is allowed and "safe" is not; the
# short name is never used; the endorsed name is never half-said.
SAFE_WORD = re.compile(r"\bsafe\b", re.I)
SHORT_NAME = re.compile(r"\bRR\b")
HALF_BRAND = re.compile(r"\bRadial\b(?! 47)")
PUFFERY = ("magic", "just works", "seamless")

# What a state is read as, and what paints it. The style and script
# blocks come out of the text, because a token name is not a sentence a
# reader reads.
STYLED = re.compile(r"<(style|script)\b.*?</\1>", re.S)
CHIP = re.compile(r'<span class="chip( touches)?">([a-z]+)</span>')
FORM_TARGET = re.compile(r'<form method="post" action="([^"]+)">')
PRIMARY = '<button class="primary"'
AMBER = ("#f8bd5c", "#f0a32d", "#a95d00")


def main_of(page: str) -> str:
    """The page's own body, markup and all."""
    return page.split("<main>", 1)[-1].split("</main>", 1)[0]


def reads_as(page: str) -> str:
    """Everything on the page a reader sees, masthead included."""
    return " ".join(html.unescape(TAGS.sub(" ", PIECES.sub(r"\1", STYLED.sub(" ", page)))).split())


def strings_in(value) -> list[str]:
    """Every string a constant carries, table or not.

    The tables in this module are the page's parity claims — the command
    per action, the sentence per step — so a word could reach the page
    through one of them as easily as through a bare constant.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in strings_in(item)]
    if isinstance(value, (list, tuple, set)):
        return [text for item in value for text in strings_in(item)]
    return []


def constants() -> list[tuple[str, str]]:
    return [
        (f"manage_web.{name}", text)
        for name in dir(manage_web)
        if name.isupper() and not name.startswith("_")
        for text in strings_in(getattr(manage_web, name))
    ]


def view(run_, name: str, **extra) -> str:
    """One view, opened the way its own navigation form opens it."""
    query = f"&view={name}" + "".join(
        f"&{key}={urllib.parse.quote(str(value))}" for key, value in extra.items())
    return run_.page(query)


def pressed(run_, fields: dict, *, confirm: bool = False) -> str:
    body = {"token": run_.session.token, **fields}
    if confirm:
        body["confirm"] = "yes"
    status, page, _ = run_.post(body)
    assert status == 200, page
    return page


def every_state(run_, tmp_path, env, capsys, client_here) -> dict[str, str]:
    """One page per state this build renders, on one machine.

    Collected in the order a reader reaches them: the views first, then
    the walkthrough that adds a project, then every two-step press twice
    — the rehearsal and the run — then each one-press change and its
    result line, then the two ways a press stops, then a whole reversal,
    and last the two rosters a reader can be left holding.

    Each press undoes the one before it, so the machine under them stands
    still and every state is reached on the same fixture.
    """
    alpha = registered(capsys, _make_project(tmp_path / "alpha"), skill=True)
    beta = registered(capsys, _make_project(tmp_path / "beta"))
    drop_entry(client_here, record_of(beta)["key"])   # a mismatch row
    fresh = _make_project(tmp_path / "fresh").resolve()
    foreign = _make_project(tmp_path / "foreign").resolve()
    (foreign / SKILL_DIR).mkdir(parents=True)
    (foreign / SKILL_DIR / "ours.md").write_bytes(b"our own skill\n")

    # Two entries whose project is gone, so two leftover rows render too:
    # one for today's two presses, one for Clean up's one press.
    key = f"{PREFIX}old"
    body = json.loads(client_here.read_text(encoding="utf-8"))
    body["mcpServers"][key] = {
        "command": "x", "args": [],
        "env": {ROOT_ENV: str(tmp_path / "vanished")},
    }
    body["mcpServers"][f"{PREFIX}older"] = {
        "command": "x", "args": [],
        "env": {ROOT_ENV: str(tmp_path / "gone-too")},
    }
    client_here.write_text(json.dumps(body, indent=2), encoding="utf-8")

    pages = {
        "roster": run_.page(),
        "project": view(run_, manage_web.PROJECT, root=alpha),
        "mismatch": view(run_, manage_web.PROJECT, root=beta),
        "unmounted": view(run_, manage_web.PROJECT, root=foreign),
        "removal": view(run_, manage_web.REMOVAL),
        "walkthrough": view(run_, manage_web.SETUP, root=fresh),
        "gone": view(run_, manage_web.PROJECT, root=tmp_path / "never-was"),
    }

    # The repair (PLAN-0014 phase 3), in today's two presses on the
    # mismatched project: the rehearsal and the run. It leaves beta
    # registered again.
    repair = {"action": manage_web.REPAIR, "root": str(beta)}
    pages["repair plan"] = pressed(run_, repair)
    pages["repaired"] = pressed(run_, repair, confirm=True)

    # The walkthrough's one press, posted to the target that view drew.
    target = html.unescape(FORM_TARGET.search(pages["walkthrough"]).group(1))
    status, walked, _ = run_.request(
        "POST", target,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urllib.parse.urlencode({
            "token": run_.session.token, "action": "mount",
            "scope": mnt.default_scope(fresh), "owner": mnt.DEFAULT_OWNER,
            "skill": [setup_web.box_value(h.name, SKILL) for h in sk.HARNESSES.values()],
            "connect": CLIENT,
            setup_web.CHOICES_FIELD: "1",
        }, doseq=True),
    )
    assert status == 200, walked
    pages["walkthrough done"] = walked

    for name, fields in (
        ("skill remove", {"action": manage_web.SKILL_REMOVE,
                          "root": str(alpha), "skill": SKILL}),
        ("skill add", {"action": manage_web.SKILL_ADD,
                       "root": str(alpha), "skill": SKILL}),
        ("unconnect", {"action": manage_web.UNCONNECT,
                       "root": str(alpha), "client": CLIENT}),
        ("connect", {"action": manage_web.CONNECT,
                     "root": str(alpha), "client": CLIENT}),
        ("orphan", {"action": manage_web.ORPHAN, "client": CLIENT, "key": key}),
        # The two update presses (PLAN-0021 phase 1), on a machine with
        # nothing behind: Confirm a big change says so and offers no
        # press, and a confirm posted anyway says so on the screen it
        # returns to (PLAN-0027 phase 3).
        ("update", {"action": manage_web.UPDATE, "root": str(alpha)}),
        ("update all", {"action": manage_web.UPDATE_ALL}),
    ):
        pages[f"{name} plan"] = pressed(run_, fields)
        pages[f"{name} done"] = pressed(run_, fields, confirm=True)
    # Clean up, one press from its row of Projects (PLAN-0027 phase 3).
    pages["clean up now"] = pressed(run_, {
        "action": manage_web.ORPHAN, "client": CLIENT, "root": str(tmp_path / "gone-too"),
        "confirm": "yes", "from": manage_web.ROSTER})

    # Each one-press change, from the screen that draws it (PLAN-0027
    # phase 1), each undone by the next.
    first = next(iter(sk.HARNESSES))
    now = {"confirm": "yes", "from": manage_web.PROJECT}
    for name, fields in (
        ("skill off now", {"action": manage_web.SKILL_REMOVE, "root": str(alpha),
                           "skill": SKILL, "harness": first}),
        ("skill on now", {"action": manage_web.SKILL_ADD, "root": str(alpha),
                          "skill": SKILL, "harness": first}),
        ("disconnect now", {"action": manage_web.UNCONNECT, "root": str(alpha),
                            "client": CLIENT}),
        ("connect now", {"action": manage_web.CONNECT, "root": str(alpha),
                         "client": CLIENT}),
        ("refused now", {"action": manage_web.SKILL_ADD, "root": str(foreign),
                         "skill": SKILL, "harness": first}),
    ):
        pages[name] = pressed(run_, {**fields, **now})
    drop_entry(client_here, record_of(beta)["key"])
    pages["repair from projects now"] = pressed(run_, {
        "action": manage_web.REPAIR, "root": str(beta), "confirm": "yes",
        "from": manage_web.ROSTER})

    # Unmount last of the two-step presses: the one-press changes above
    # stand on a mounted alpha.
    unmount = {"action": manage_web.UNMOUNT, "root": str(alpha)}
    pages["unmount plan"] = pressed(run_, unmount)
    pages["unmount done"] = pressed(run_, unmount, confirm=True)

    # The two ways a press stops: refused, and never run at all.
    pages["refused"] = pressed(run_, {
        "action": manage_web.SKILL_ADD, "root": str(foreign), "skill": SKILL})
    pages["orphan refused"] = pressed(run_, {
        "action": manage_web.ORPHAN, "client": CLIENT, "key": "someone-elses",
    }, confirm=True)
    pages["failed"] = pressed(run_, {
        "action": manage_web.UNMOUNT, "root": str(tmp_path / "never-was"),
    }, confirm=True)

    # Remove from this project, on the project the walkthrough walked on:
    # Confirm a big change with its keep box, the box's own press (the
    # unmount alone), then the default over what is left, the skills and
    # the connection (PLAN-0027 phase 3).
    reversal = {"action": manage_web.REVERSAL, "root": str(fresh),
                "from": manage_web.PROJECT}
    pages["reversal plan"] = pressed(run_, reversal)
    pages["files removed now"] = pressed(run_, {
        "action": manage_web.UNMOUNT, "root": str(fresh), **now})
    pages["reversed"] = pressed(run_, reversal, confirm=True)
    # And one from Remove RadialRig's first step, which comes back there.
    pages["removal answered"] = pressed(run_, {
        "action": manage_web.REVERSAL, "root": str(alpha), "confirm": "yes",
        "from": manage_web.REMOVAL})

    # And the two rosters a reader can be left holding.
    path = roster_file(env)
    path.write_bytes(b"{ not json\n")
    pages["unreadable roster"] = run_.page()
    pages["unreadable removal"] = view(run_, manage_web.REMOVAL)
    path.unlink()
    pages["empty roster"] = run_.page()
    pages["empty removal"] = view(run_, manage_web.REMOVAL)
    return pages


def test_the_manage_page_says_nothing_about_who_typed_a_change(
    tmp_path, env, client_here, command, capsys, manage,
):
    pages = every_state(manage(), tmp_path, env, capsys, client_here)
    for name, page in pages.items():
        hit = FORBIDDEN_WORD.search(visible(page).lower())
        assert hit is None, f"the {name} state says '{hit.group(0)}'"
    # The amendment is a narrowing, not an amnesty: the substring is on
    # the page, inside a skill's name and nowhere else.
    assert "hand" in visible(pages["project"]).lower()


def test_no_manage_constant_names_a_client():
    """The scan test_setup_web.py runs over its own page, over this one.

    The client's name, its label, its restart line and every part of
    every settings-file path are data in the client table. A constant
    carrying any of them would be a second client's worth of edits
    waiting to happen — and so would a row in one of this module's own
    tables, which is why those are walked too.
    """
    client = cn.client_for(CLIENT)
    forbidden = [client.name, client.label, client.restart]
    for location in client.locations:
        for part in location.parts:
            bare = part.strip("*?[]")
            if bare:
                forbidden.append(bare)

    checked = constants()
    assert checked, "the manage page carries no constants"
    for where, text in checked:
        for word in forbidden:
            assert word.lower() not in text.lower(), f"{where} names '{word}'"


def test_the_manage_words_hold(
    tmp_path, env, client_here, command, capsys, manage,
):
    """The brand's vocabulary, over the constants and over what renders.

    Both, because a page says more than its own constants: the reports it
    prints and the shell it prints them in are other modules' words, and
    a reader reads all of them off one page.
    """
    pages = every_state(manage(), tmp_path, env, capsys, client_here)
    checked = constants() + [
        (f"the {name} state", reads_as(page)) for name, page in pages.items()
    ]
    for where, text in checked:
        hit = SAFE_WORD.search(text)
        assert hit is None, f"{where} says '{hit.group(0) if hit else ''}'"
        assert not SHORT_NAME.search(text), f"{where} uses the short name"
        hit = HALF_BRAND.search(text)
        assert hit is None, f"{where} says 'Radial' without '47'"
        lowered = text.lower()
        for banned in PUFFERY:
            assert banned not in lowered, f"{where} says '{banned}'"

    # The endorsed name is on every page, said in full.
    for name, page in pages.items():
        assert "Radial 47" in reads_as(page), name
    # The ban is on the claim, not on the comparative the reports make.
    assert SAFE_WORD.search("a safer file") is None


def test_the_endorsement_appears_exactly_once_per_page(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Once, in the shared masthead, and nowhere in the body."""
    marked = f'<span class="endorse">{setup_web.ENDORSEMENT}</span>'
    for name, page in every_state(
        manage(), tmp_path, env, capsys, client_here,
    ).items():
        assert page.count(setup_web.ENDORSEMENT) == 1, name
        assert marked in page, name
        assert setup_web.ENDORSEMENT not in main_of(page), name


def test_the_masthead_carries_the_installed_engine(env, manage):
    """PLAN-0018 phase 1 (BUG-0013): the one stamp every surface shows,
    after the endorsement, in the masthead's own small type."""
    page = manage().page()
    stamp = mnt.version_stamp()
    assert stamp == f"radialrig {mnt.installed_version()}"
    assert (f'<span class="endorse">{setup_web.ENDORSEMENT}</span>'
            f'<span class="version">{stamp}</span></header>') in page
    assert page.count('<span class="version">') == 1
    assert ".masthead .version {" in page


def test_one_amber_meaning_per_screen(
    tmp_path, env, client_here, command, capsys, manage,
):
    """The colors are the packaged file's, and the amber means one thing.

    On a redesigned screen it is the one press the screen is for: the
    first Repair, else Update all, on Projects (Add a project on an empty
    list); the first fix in a project's status. Continue, behind Add a
    project, carries the amber too: it is the one press of the folder
    field, and page.js finds it by that class. On today's plan and result
    pages it is the press that writes, and a state that has already run
    carries none. Nothing on the page paints itself.
    """
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    pages = every_state(manage(), tmp_path, env, capsys, client_here)

    for name, page in pages.items():
        assert page.count("<style") == 1, name
        assert css in page, f"the {name} state serves its own stylesheet"
        body = main_of(page)
        assert "style=" not in body, name
        for hue in AMBER:
            assert hue not in body.lower(), name

    assert "button.primary { background: var(--accent-strong);" in css

    def amber(page: str) -> int:
        """The ambers on a screen, but the Continue inside the folder
        field behind Add a project, and the keep box's press on Confirm a
        big change, which shows only while the box is ticked, in place of
        the default's (PLAN-0027 phase 3)."""
        body = main_of(page).replace(
            f'<button class="primary" type="submit">{manage_web.ADD_LABEL}</button>', "")
        body = re.sub(r'<div class="keep-only"><form .*?</form></div>', "", body, flags=re.S)
        return body.count(PRIMARY) + body.count('<summary class="button primary">')

    wrong = []
    for name, expected in (
        ("roster", 1), ("project", 0), ("mismatch", 1), ("unmounted", 1),
        ("gone", 0),
        ("walkthrough", 1), ("walkthrough done", 0), ("removal", 0),
        ("skill add plan", 1), ("skill add done", 0),
        ("unmount plan", 1), ("unmount done", 0),
        ("refused", 0), ("orphan refused", 0), ("failed", 0),
        ("repair plan", 1), ("repaired", 0),
        # Confirm a big change: its one press where it offers one. On a
        # machine with nothing behind, neither update card offers one.
        # Update all comes back to Projects, where the leftover row left
        # for Clean up is the first that needs repair; after Clean up
        # nothing on the list does.
        ("update plan", 0), ("update done", 0),
        ("update all plan", 0), ("update all done", 1),
        ("reversal plan", 1), ("files removed now", 1), ("reversed", 1),
        ("removal answered", 0), ("empty removal", 0),
        ("clean up now", 0),
        ("skill off now", 0), ("skill on now", 0),
        ("disconnect now", 0), ("connect now", 0),
        ("repair from projects now", 0), ("refused now", 1),
        ("empty roster", 1),
    ):
        if amber(pages[name]) != expected:
            wrong.append((name, amber(pages[name]), expected))
    assert wrong == []
    # Continue is the field's one press, wherever the field is drawn.
    assert main_of(pages["roster"]).count(
        f'<button class="primary" type="submit">{manage_web.ADD_LABEL}</button>') == 1


def test_dark_is_the_default(manage):
    """Dark is the treatment; light is the alternate keyed to the query."""
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert css.index(":root {") < css.index("prefers-color-scheme: light")
    assert "prefers-color-scheme: dark" not in css, \
        "the dark treatment is behind a query rather than the default"

    dark, light = css.split("prefers-color-scheme: light", 1)
    assert "--ground: #111318" in dark
    assert "--ground: #f7f8fc" in light
    # The amber anchor's ink, the pair test_setup_web.py pins.
    assert "--on-accent: #111318" in dark
    assert "--on-accent: #f7f8fc" in light

    page = manage().page()
    assert '<meta name="color-scheme" content="dark light">' in page
    assert page.index(":root {") < page.index("prefers-color-scheme: light")


# --- PLAN-0017 phase 2 (cli-connect-02): the manage page's own connect --
# runner says what a file error left, under the setup page's rule.

def test_a_manage_connect_press_meeting_a_file_error_says_what_is_left(
    tmp_path, monkeypatch, command, capsys, manage,
):
    mapping = fake_client(tmp_path, store=True)
    for name, value in mapping.items():
        monkeypatch.setenv(name, value)
    visible, hidden = roaming_config(mapping), store_config(mapping)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    hidden.write_bytes(CONFIG_HOSTS["empty-servers"])
    root = _make_project(tmp_path / "host").resolve()
    assert run(capsys, "mount", str(root))[0] == 0
    before = {visible: visible.read_bytes(), hidden: hidden.read_bytes()}

    real = Path.write_bytes
    seen: list[Path] = []
    limit = [1]

    def flaky(self, data):
        if self.name == "claude_desktop_config.json":
            seen.append(self)
            if len(seen) > limit[0]:
                raise PermissionError(13, "Permission denied", str(self))
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    run_ = manage()
    fields = {"action": manage_web.CONNECT, "root": str(root), "client": CLIENT}

    # One copy landed and could not be put back: a partial press, and
    # the sentence names the copy the disk shows changed.
    said = text_of(pressed(run_, fields, confirm=True))
    landed = seen[0]
    assert setup_web.PARTIAL_HEAD in said
    assert manage_web.ACTION_FAILED_HEAD not in said
    assert f"put back except: {landed}" in said
    changed = {path for path in before if path.read_bytes() != before[path]}
    assert changed == {landed}
    assert not cn.is_connected(root)
    assert run_.running

    # Every write put back: the press did not run, and the sentence
    # says nothing was written.
    for path, data in before.items():
        real(path, data)
    seen.clear()
    limit[0] = 0
    said = text_of(pressed(run_, fields, confirm=True))
    assert manage_web.ACTION_FAILED_HEAD in said
    assert setup_web.PARTIAL_HEAD not in said
    assert "Permission denied" in said and "nothing was written" in said
    assert {path for path in before if path.read_bytes() != before[path]} == set()
    assert not cn.is_connected(root)
    assert run_.running


# --- PLAN-0017 phase 5: a skills record the kit cannot read ------------

def test_a_utf16_skills_record_shows_the_refusal_on_the_project_page(
    tmp_path, env, client_here, command, capsys, manage,
):
    # skills-roster-04, the manage half: the record in another encoding
    # is the sentence `skill list` prints on this project, on the
    # project's own view, not the internal-error page.
    alpha = registered(capsys, _make_project(tmp_path / "alpha"), skill=True)
    record_rel = sk.default_harness().record
    record = alpha / record_rel
    record.write_bytes(record.read_text(encoding="utf-8").encode("utf-16"))
    run_ = manage()
    page = project_page(run_, alpha)
    assert "could not answer" not in page
    said = text_of(page)
    assert manage_web.ACTION_FAILED_HEAD in said
    assert f"{record_rel} does not parse as JSON" in said
    assert run_.running


# PLAN-0022 phase 3, from the second reader: a row's path or claim can
# come from a record a project carries, and the page draws it too.
FORGED_ROWS = [
    mnt.Disposition("src/app‮.py", mnt.LEFT, mnt.LEFT_NOT_SEEDED),
    mnt.Disposition(".mcp.json", mnt.LEFT, mnt.LEFT_MCP_UNTRUSTED,
                    "mcpServers.x​\ud800"),
]


def test_a_row_carrying_record_text_stands_in_the_table_only():
    """The command line's rows show record text escaped, and the filter
    that lifts them out of the page's sentences read the raw path, so
    such a row printed in the table and again beneath it."""
    lines = report.render_block(FORGED_ROWS)
    assert manage_web._sentences(lines, FORGED_ROWS) == (), lines


def test_the_pages_table_shows_record_text_visibly_and_can_send_it():
    table = manage_web._table(FORGED_ROWS, "forged", mnt.reason_text)
    for hidden in ("‮", "​", "\ud800"):
        assert hidden not in table, table.encode("utf-8", "backslashreplace")
    assert "src/app\\u202e.py" in table, table
    table.encode("utf-8")


# --- BUG-0018: one warning for the whole repair ---------------------------

def test_a_repair_while_the_client_is_open_says_to_run_the_repair_again(
    tmp_path, env, client_here, command, capsys, manage,
):
    """The removal's warning names entries to delete if they come back,
    and the registration that follows writes those same entries, so the
    two must not stand side by side: one sentence speaks for the repair."""
    from radialrig.tests.test_connect import plant_lock

    client = cn.CLIENTS[CLIENT]
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = beta.resolve()
    keys = tuple(record_of(beta)["entries"])
    plant_lock(client_here)
    run_ = manage()

    done = pressed(run_, {"action": manage_web.REPAIR, "root": str(root)}, confirm=True)
    said = text_of(done)
    assert cn.is_connected(root, CLIENT), "a warning, not a refusal"
    assert report.repair_running_after(client) in said
    assert report.unconnect_running_after(client, keys) not in said
    assert report.connect_running_after(client) not in said
    assert said.count(f"{client.label} is running") == 1
