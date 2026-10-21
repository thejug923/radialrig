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
from radialrig import connect as cn
from radialrig import manage_web
from radialrig import mount as mnt
from radialrig import report
from radialrig import roster as ro
from radialrig import setup_web
from radialrig import skills as sk
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

# A press's rows: the chip, the path, the sentence. The listing no longer
# draws this shape (PLAN-0014 phase 1) and the project page no longer
# draws its one-row table (phase 2); `LISTING_ROW` below reads the
# listing, and `FACT_BLOCK` the project page's Status section.
ROW = re.compile(
    r'<tr><td class="verb"><span class="chip[^"]*">([a-z]+)</span></td>'
    r'<td class="path">([^<]*)</td><td class="why">([^<]*)</td>'
    r'(?:<td class="act">.*?</td>)?</tr>'
)
# The listing's row (PLAN-0014 phase 1): the folder's name in its own
# element, the repair chip beside it where the row carries one, the
# parent path beneath, the three facts, and the row's control.
LISTING_ROW = re.compile(
    r'<tr id="row-[0-9a-f]{12}"><td class="project"><span class="title"><span class="name">([^<]*)</span>'
    r'(.*?)</span>(?:<span class="parent">([^<]*)</span>)?</td>'
    r'<td class="status">(.*?)</td><td class="act">.*?</td></tr>', re.S
)
FACT = re.compile(r'<span class="fact ([a-z]+)">([^<]*)</span>')
# The project page's Status section says the same three facts as blocks
# (PLAN-0014 phase 2).
FACT_BLOCK = re.compile(r'<p class="fact ([a-z]+)">([^<]*)</p>')
# The chip's whole text, glyph first: what a reader scanning by shape
# meets, and what the words say once past it.
REPAIR = f"{manage_web.REPAIR_GLYPH} {manage_web.REPAIR_CHIP}"
ANCHOR = re.compile(r'<a\b[^>]*\bhref="([^"]*)"')
TAGS = re.compile(r"<[^>]+>")
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

def listing_rows(page: str) -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    """Every row of the listing: name, parent, chip text, and the facts."""
    out = []
    for name, chip, parent, status in LISTING_ROW.findall(page):
        facts = [(which, html.unescape(said)) for which, said in FACT.findall(status)]
        out.append((
            html.unescape(name), html.unescape(parent),
            " ".join(html.unescape(TAGS.sub(" ", chip)).split()), facts,
        ))
    return out


def facts_of(state: ro.ProjectState) -> list[tuple[str, str]]:
    """Constraint 3's mapping, written out in the page's words.

    Literal here rather than read off the module, so this file and not
    the page is the pin. The mismatch line is the repair sentence of
    phase 3, written out: the label of the client the record claims,
    filled from the table, or the record file where it claims none this
    build knows.
    """
    client = cn.CLIENTS.get(state.connected)
    claimed = cn.CLIENTS.get(state.claimed)
    if state.connected == ro.CONNECTED_MISMATCH and claimed is not None:
        connection = (f"{claimed.label}'s settings no longer carry the entry "
                      "this project's record claims")
    elif state.connected == ro.CONNECTED_MISMATCH:
        connection = ("This project's record names no client this build knows: "
                      f"see {Path(state.root) / cn.CONNECT_REL}")
    elif client is None:
        # Named only where the client is installed on this machine, and
        # left out where it is not (the owner's answer of 2026-10-01).
        connection = (f"Not connected to {cn.CLIENTS[CLIENT].label}"
                      if cn.installed() else None)
    else:
        connection = f"Connected to {client.label}"
    return [
        ("mounted", "Mounted" if state.mounted else "Not mounted"),
        # A project whose update would write something says so, right
        # after the mount fact (PLAN-0021 phase 1 prescription 4).
        *([("update", "Update available")] if getattr(state, "behind", False) else []),
        *([("connection", connection)] if connection else []),
        *(("skills", said) for said in skills_facts_of(state)),
    ]


def skills_facts_of(state: ro.ProjectState) -> list[str]:
    """The skills fact as PLAN-0015 phase 2 prescription 5 writes it: one
    count where every agent has the same skills, one line per agent,
    labelled from the map, where they differ."""
    def count(n: int) -> str:
        return "no skills" if n == 0 else "1 skill" if n == 1 else f"{n} skills"

    if len({names for _, names in state.agents}) <= 1:
        return [count(len(state.skills)).replace("no skills", "No skills")]
    return [f"{sk.HARNESSES[name].label}: {count(len(names))}"
            for name, names in state.agents]


def group_of(state: ro.ProjectState) -> int:
    """Needs repair, then not mounted, then the rest."""
    if state.connected == ro.CONNECTED_MISMATCH:
        return 0
    return 1 if not state.mounted else 2


def expected_listing(states) -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    """What the listing draws for these states, in the order it draws them."""
    return [
        (Path(state.root).name, str(Path(state.root).parent),
         REPAIR if state.connected == ro.CONNECTED_MISMATCH else "",
         facts_of(state))
        for state in sorted(states, key=group_of)
    ]


def visible(page: str) -> str:
    body = page.split("<main>", 1)[-1].split("</main>", 1)[0]
    return TAGS.sub(" ", body)


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

def test_the_manage_page_lists_the_roster_with_the_cli_sentences(
    tmp_path, env, client_here, command, capsys, manage,
):
    alpha = registered(capsys, _make_project(tmp_path / "alpha"), skill=True)
    beta = registered(capsys, _make_project(tmp_path / "beta"))
    drop_entry(client_here, record_of(beta)["key"])

    page = manage().page()

    states = ro.listing()
    found = ro.orphans()
    # The page says each project's state through constraint 3's mapping
    # (PLAN-0014 phase 1), where it used to print the command line's rows
    # word for word; the roster's order is the command line's, and the
    # page's order puts the row that needs a reader first.
    assert listing_rows(page) == expected_listing(states)
    assert [state.root for state in states] == [
        str(alpha.resolve()), str(beta.resolve())]
    assert [name for name, *_ in listing_rows(page)] == ["beta", "alpha"]

    code, out, err = run(capsys, "projects")
    assert (code, err) == (0, "")
    # The command line's own rows are unchanged, sentence for sentence.
    for d in ro.report_rows(states, found):
        assert ro.reason_text(d) in out
    said = text_of(page)
    # The mismatch row says the same sentence on both surfaces, naming
    # the client the record claims (PLAN-0014 phase 3). The command
    # line keeps its remedy tail with the placeholders; the listing drops
    # it, because the row's chip and sentence say what to do and where.
    label = cn.CLIENTS[CLIENT].label
    assert ro.MISMATCH_CLAUSE.format(label=label) in out
    assert ro.MISMATCH_CLAUSE.format(label=label) in said
    assert report.PROJECTS_MISMATCH_TAIL in out
    assert report.PROJECTS_MISMATCH_TAIL not in said
    assert "<client>" not in said and "<PATH>" not in said

    # The page names the machine and lets the rows be the count; the
    # command line's count line stays on the command line.
    assert said.startswith(manage_web.ROSTER_HEAD)
    assert out.splitlines()[0] == report.projects_head(states, found)
    assert out.splitlines()[0] not in said
    assert manage_web.ROSTER_CAPTION in said

    # One control per standing project, on the project's own row. One
    # chip on the whole listing, outlined, on the row that needs repair;
    # the neutral chip and the state column are gone with the hint that
    # explained them.
    assert page.count(f'value="{alpha.resolve()}"') == 1
    assert page.count(f'value="{beta.resolve()}"') == 1
    assert page.count('<td class="act">') == 2
    assert page.count('<span class="chip touches">') == 1
    assert page.count('<span class="chip">') == 0
    assert rows(page) == []
    assert '<th scope="col">State</th>' not in page


def test_an_empty_roster_says_so_the_way_the_command_line_does(
    tmp_path, env, capsys, manage,
):
    page = manage().page()
    assert listing_rows(page) == []
    said = text_of(page)
    code, out, _ = run(capsys, "projects")
    assert code == 0
    # Every sentence the command line says about an empty machine, said
    # here too. Its count line is the one line that stays behind: this
    # page names the machine in its heading and lets the rows count
    # themselves, and with no row there is no table to count.
    counted = report.projects_head((), ())
    for line in out.splitlines():
        # The engine line stays behind with it: this page carries the
        # version in its masthead, outside the text read here
        # (PLAN-0018 phase 1).
        if line and line not in (counted, report.engine_line()):
            assert line in said, line
    assert report.PROJECTS_NONE in said
    assert counted not in said
    assert manage_web.ROSTER_HEAD in said
    # No rows, so no table; the add block stands in its place.
    assert "<table" not in main_of(page)
    assert manage_web.ROSTER_CAPTION not in said


def test_orphan_entries_are_listed_with_their_command(
    tmp_path, env, client_here, manage,
):
    body = json.loads(client_here.read_text(encoding="utf-8"))
    body["mcpServers"] = {
        f"{PREFIX}old": {"command": "x", "args": [],
                         "env": {ROOT_ENV: str(tmp_path / "vanished")}},
        f"{PREFIX}bare": {"command": "x", "args": []},
    }
    client_here.write_text(json.dumps(body, indent=2), encoding="utf-8")

    page = manage().page()

    found = ro.orphans()
    assert [orphan.key for orphan in found] == [f"{PREFIX}bare", f"{PREFIX}old"]
    loose = found[1]
    # A loose entry is not a project, so it is not a row of the listing:
    # it stands in its own block beneath, with its command (PLAN-0014
    # phase 1 prescription 4).
    assert listing_rows(page) == []
    assert "<table" not in main_of(page)
    said = text_of(page)
    assert ro.orphan_command(loose) in said
    assert manage_web.ORPHAN_HEAD in said
    # The command is offered as a copy block, the setup page's idiom,
    # and the press names the entry it removes (PLAN-0014 phase 2).
    assert f'data-copy="{ro.orphan_command(loose)}"' in page
    assert manage_web.ORPHAN_LABEL.format(key=loose.key) in said
    assert f'<h3 class="name">{loose.key}</h3><p class="what">' in page
    # An entry pinning no root is named and offered nothing: radialrig
    # cannot show it is an orphan.
    assert ro.ROSTER_REASONS[ro.ROSTER_UNPINNED] in said
    assert f'data-copy="radialrig unconnect {CLIENT} --orphan {PREFIX}bare"' \
        not in page


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
    """Prescription 1: "Projects", then each folder's own name first.

    The name is one element and the parent path another beneath it; the
    two together are the whole path, on the row as text a reader can
    select. The stylesheet keeps the name on one line and sets the
    parent in the secondary mono treatment.
    """
    three_projects(tmp_path, capsys, client_here)
    page = manage().page()
    body = main_of(page)

    assert manage_web.ROSTER_HEAD == "Projects"
    assert f"<h1>{manage_web.ROSTER_HEAD}</h1>" in body
    drawn = listing_rows(page)
    assert [name for name, *_ in drawn] == ["beta", "gamma", "alpha"]
    for name, parent, _, _ in drawn:
        root = tmp_path.resolve() / name
        assert parent == str(root.parent)
        assert str(Path(parent) / name) == str(root)
        assert f'<span class="name">{name}</span>' in body
        assert f'<span class="parent">{html.escape(parent)}</span>' in body
    # Each name leads its row: the name element comes before the parent
    # element, and the full path is on the row as text.
    for row in LISTING_ROW.finditer(body):
        assert row.group(1) and row.group(3)
        assert body.index(row.group(1), row.start()) < body.index(row.group(3), row.start())

    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    name_rule = css.split("table.plan.projects .name {", 1)[1].split("}", 1)[0]
    assert "white-space: nowrap" in name_rule
    parent_rule = css.split("table.plan.projects .parent {", 1)[1].split("}", 1)[0]
    assert "Cascadia Mono" in parent_rule
    assert "var(--faint)" in parent_rule
    assert "overflow-wrap: anywhere" in parent_rule


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


def test_a_row_says_its_three_facts_in_the_mapping_order(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 2: mounted, connection, skills — three elements.

    The client's label is the client table's; the skill names stay on
    the project's page, and the listing counts them.
    """
    three_projects(tmp_path, capsys, client_here)
    page = manage().page()
    drawn = listing_rows(page)
    assert len(drawn) == 3
    for _, _, _, facts in drawn:
        assert [which for which, _ in facts] == ["mounted", "connection", "skills"]
    by_name = {name: facts for name, _, _, facts in drawn}
    label = cn.CLIENTS[CLIENT].label
    assert by_name["alpha"] == [
        ("mounted", "Mounted"), ("connection", f"Connected to {label}"),
        ("skills", "1 skill")]
    assert by_name["beta"] == [
        ("mounted", "Mounted"),
        ("connection", f"{label}'s settings no longer carry the entry this "
                       "project's record claims"),
        ("skills", "No skills")]
    assert by_name["gamma"] == [
        ("mounted", "Not mounted"), ("connection", f"Connected to {label}"),
        ("skills", "No skills")]
    # The skill's name is the project page's to list, not this table's.
    table = main_of(page).split("</table>", 1)[0]
    assert SKILL not in table
    assert "skills:" not in text_of(page)


def test_only_a_mismatched_row_carries_the_repair_chip(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 3: one outlined chip, led by a glyph, and no other.

    The glyph is its own element hidden from the accessibility tree, so
    the chip is found by shape on the screen and read as its words. The
    outline is the setup page's `touches` treatment; the amber stays on
    the press.
    """
    three_projects(tmp_path, capsys, client_here)
    page = manage().page()
    body = main_of(page)

    chip = ('<span class="chip touches">'
            f'<span class="glyph" aria-hidden="true">{manage_web.REPAIR_GLYPH}</span> '
            f"{manage_web.REPAIR_CHIP}</span>")
    assert body.count(chip) == 1
    assert body.count('<span class="chip') == 1
    assert manage_web.REPAIR_CHIP == "Needs repair"
    assert manage_web.REPAIR_GLYPH == "!"
    assert [chipped_ for _, _, chipped_, _ in listing_rows(page)] == [REPAIR, "", ""]
    assert REPAIR in text_of(page)
    # The chip sits in the mismatched row's project cell, beside the name.
    row = LISTING_ROW.search(body)
    assert row.group(1) == "beta"
    assert chip in row.group(2)
    assert PRIMARY not in row.group(0)
    assert "State" not in [head for head in manage_web.ROSTER_COLUMNS]
    assert not hasattr(manage_web, "ROSTER_HINT")


def test_the_listing_orders_needs_repair_then_not_mounted_then_the_rest(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 4: the page sorts; the command line does not.

    Five projects, in a roster order the sort must change: healthy,
    unmounted, mismatched, healthy, and one both mismatched and
    unmounted, which needs repair. Each group keeps roster order.
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
    roster_order = [Path(s.root).name for s in states]
    assert roster_order == ["alpha", "gamma", "beta", "delta", "epsilon"]
    assert [s.connected == ro.CONNECTED_MISMATCH for s in states] == [
        False, False, True, False, True]
    assert [s.mounted for s in states] == [True, False, True, True, False]

    page = manage().page()
    assert [name for name, *_ in listing_rows(page)] == [
        "beta", "epsilon", "gamma", "alpha", "delta"]
    assert listing_rows(page) == expected_listing(states)

    # The command line's rows keep the roster's order, unchanged.
    assert [d.path for d in ro.report_rows(states, ())] == [s.root for s in states]
    code, out, _ = run(capsys, "projects")
    assert code == 0
    positions = [out.index(str(root.resolve()))
                 for root in (alpha, gamma, beta, delta, epsilon)]
    assert positions == sorted(positions)


def test_the_top_control_is_a_fragment_link_to_the_add_block(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 5: "Add a project" is reachable from the top.

    A same-document fragment link, styled as the ghost button, beside
    the heading. It changes no view, so it is not one of the navigations
    that must be a form (`navigations()` counts only GET forms), and it
    is not an external reference (`EXTERNAL_REF` matches a scheme). It
    fires no `pagehide`, which is the event the close beacon keys on.
    """
    three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    page = run_.page()
    body = main_of(page)

    link = f'<a class="ghost" href="#{manage_web.ADD_ID}">{manage_web.ADD_HEAD}</a>'
    assert body.count(link) == 1
    assert body.index(link) < body.index("<table")
    assert body.index(link) < body.index(f'id="{manage_web.ADD_ID}"')
    assert f'<fieldset class="skills" id="{manage_web.ADD_ID}">' in body
    assert body.count(f'id="{manage_web.ADD_ID}"') == 1
    assert ANCHOR.findall(page) == [f"#{manage_web.ADD_ID}"]
    assert not EXTERNAL_REF.search(page)
    # Not a navigation: those are forms, and each one still is.
    targets = navigations(page, run_.session.token)
    assert len(targets) == 5   # three Open, the add press, the reversal
    assert not any("#" in target for target in targets)
    js = (packaged_web() / "page.js").read_text(encoding="utf-8")
    assert 'addEventListener("pagehide"' in js
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert "a.ghost {" in css
    assert ".head-row {" in css
    assert run_.running


def test_an_empty_roster_shows_the_add_block_in_the_listings_place(
    tmp_path, env, manage,
):
    """Prescription 5, the empty shape: the sentence, then the add block.

    No table, no control beside the heading — there is nothing to scroll
    past — and the add block stands where the listing would, under the
    command line's own empty sentence, before anything else.
    """
    page = manage().page()
    body = main_of(page)
    assert "<table" not in body
    assert '<div class="head-row">' not in body
    assert ANCHOR.findall(page) == []
    head = body.index(f"<h1>{manage_web.ROSTER_HEAD}</h1>")
    empty = body.index(html.escape(report.PROJECTS_NONE))
    add = body.index(f'id="{manage_web.ADD_ID}"')
    frame = body.index('<section class="reversal">')
    assert head < empty < add < frame
    # Nothing stands between the sentence and the block.
    between = body[empty + len(html.escape(report.PROJECTS_NONE)):add]
    assert between == f'</p><fieldset class="skills" '
    assert ('<div class="actions"><button class="primary" type="submit">'
            f"{manage_web.ADD_LABEL}</button></div>") in body


def test_the_add_press_is_disabled_by_the_script_until_the_field_names_a_folder(
    manage,
):
    """Prescription 5: the press follows the field, in page.js.

    The server renders the press without `disabled`, so a plain browser
    with no script keeps the bare submit it has always had; the script
    sets `disabled` on load while the field is empty and toggles it on
    the field's `input` event, which the shell's picker dispatches after
    writing the path (`app/src-tauri/src/picker.rs`, `eval_script`).
    """
    page = manage().page()
    body = main_of(page)
    assert "disabled" not in body
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


def test_the_browse_button_says_what_the_shells_menu_item_says():
    """Prescription 6: one wording for the picker, on the page and in the shell.

    The shell's menu item keeps its words; the page's button takes them.
    The shell's strings file is read where the checkout carries it.
    """
    assert manage_web.BROWSE_LABEL == "Choose a folder"
    assert manage_web.ADD_PICKER_NOTE
    strings = Path(__file__).resolve().parents[3] / "app" / "src" / "strings.json"
    if strings.is_file():
        words = json.loads(strings.read_text(encoding="utf-8"))
        assert words["picker_menu_label"] == manage_web.BROWSE_LABEL


@pytest.mark.parametrize("mounted", [True, False])
@pytest.mark.parametrize("connected", [CLIENT, ro.CONNECTED_NO, ro.CONNECTED_MISMATCH])
@pytest.mark.parametrize("skills", [(), (SKILL,), ("plan-review", SKILL)])
@pytest.mark.parametrize("installed", [True, False])
def test_the_page_says_a_state_through_the_mapping(
    mounted, connected, skills, installed, tmp_path, monkeypatch,
):
    """Constraint 3's mapping, over every state a project can be in.

    Mounted by connected by skills, each cell of the table pinned in the
    page's own words. The mismatch row is the repair sentence of phase 3,
    filled: the client's label where the record claims one this build
    knows, and the record file where it claims none. Both spellings are
    pinned, and the command line's clause is the same sentence.

    A project registered with no client names the client only on a
    machine that has it installed; elsewhere the page drops the fact,
    while the command line names the client either way (the owner's
    answer of 2026-10-01).
    """
    if installed:
        for name, value in fake_client(tmp_path).items():
            monkeypatch.setenv(name, value)
    root = "C:\\work\\alpha"
    state = ro.ProjectState(
        root=root, exists=True, mounted=mounted, connected=connected,
        skills=skills, claimed=CLIENT if connected != ro.CONNECTED_NO else "")
    said = dict(manage_web._facts(state))
    silent = connected == ro.CONNECTED_NO and not installed
    assert list(dict(manage_web._facts(state))) == (
        ["mounted", "skills"] if silent else ["mounted", "connection", "skills"])
    assert said["mounted"] == ("Mounted" if mounted else "Not mounted")
    label = cn.CLIENTS[CLIENT].label
    if connected == CLIENT:
        assert said["connection"] == f"Connected to {label}"
    elif connected == ro.CONNECTED_NO:
        if installed:
            assert said["connection"] == f"Not connected to {label}"
        else:
            assert not any(label in fact for fact in said.values())
        assert f"; not registered with {label}; " in ro.reason_text(
            ro.report_rows((state,), ())[0])
    else:
        assert said["connection"] == (
            f"{label}'s settings no longer carry the entry this project's "
            "record claims")
        # The same state where the record names no client this build
        # knows: the sentence names the record file instead.
        unknown = ro.ProjectState(
            root=root, exists=True, mounted=mounted, connected=connected,
            skills=skills)
        # The file is spelled the way this platform spells a path under
        # the root, so the pin holds on Linux as on Windows.
        assert dict(manage_web._facts(unknown))["connection"] == (
            "This project's record names no client this build knows: "
            f"see {Path(root) / cn.CONNECT_REL}")
        assert ro.reason_text(ro.report_rows((unknown,), ())[0]).count(
            dict(manage_web._facts(unknown))["connection"]) == 1
    assert said["skills"] == {0: "No skills", 1: "1 skill", 2: "2 skills"}[len(skills)]
    # The command line's own words for the same state are untouched, and
    # its mismatch clause is the page's sentence.
    row = ro.report_rows((state,), ())[0]
    line = ro.reason_text(row)
    assert ("mounted" if mounted else "not mounted") in line
    assert ("no skills" if not skills else "skills: " + ", ".join(skills)) in line
    if connected == ro.CONNECTED_MISMATCH:
        assert f"; {said['connection']}; " in line


def test_an_unreadable_client_profile_costs_the_row_nothing(monkeypatch):
    """`is_dir` raises on a profile directory that denies traversal, and
    the listing asks about clients once per row. The row says what it
    can and names no client, rather than failing the page (review of
    2026-10-01)."""
    def denied(client, *, env=None):
        raise PermissionError("the profile directory denies traversal")

    monkeypatch.setattr(cn, "detect", denied)
    assert cn.installed() == ()
    state = ro.ProjectState(
        root="C:\\work\\alpha", exists=True, mounted=True,
        connected=ro.CONNECTED_NO, skills=())
    assert manage_web._facts(state) == (
        ("mounted", "Mounted"), ("skills", "No skills"))


def test_an_unreadable_client_profile_costs_the_project_page_nothing(
    tmp_path, env, client_here, command, capsys, manage, monkeypatch,
):
    """The project page's two gates, the registration offer and the
    repair, each ask `detect` whether the client is on this machine, and
    `is_dir` raises on a profile directory that denies traversal. Every
    project's page still opens, and offers no press that writes to a
    client it could not read, the way the listing names none."""
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
    assert dict(manage_web._facts(state))["connection"] == "Connected to Some Other Client"
    assert manage_web.CONNECTED_FACT == "Connected to {label}"


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
ROW_HEAD = re.compile(r'<h3 class="(name|label)">([^<]*)</h3>(<[a-z]+)')
# The command behind its disclosure, with the Copy button inside it.
DISCLOSED = re.compile(
    r'<details class="command"><summary>([^<]*)</summary>'
    r'<div class="cmd"><code>([^<]*)</code>'
    r'<button class="copy" type="button" data-copy="([^"]*)" '
    r'aria-label="([^"]*)">Copy</button></div></details>'
)
COPY_BUTTON = re.compile(r'<button class="copy"[^>]*>')
POST_PRESS = re.compile(r'<form method="post" action="[^"]+">(.*?)</form>', re.S)
GHOST = re.compile(r'<button class="ghost" type="submit">([^<]*)</button>')
SECTION_HEAD = re.compile(r"<section[^>]*><h2>([^<]*)</h2>")


def project_page(run_, root: Path) -> str:
    return view(run_, manage_web.PROJECT, root=root)


# One agent's list in the Skills section (PLAN-0015 phase 2): the label
# as its caption, and everything under it — the whole-catalog press
# where one stands, then the rows.
AGENT_LIST = re.compile(
    r'<fieldset class="skills agent"><legend>([^<]*)</legend>(.*?)</fieldset>', re.S)


def agent_lists(body: str) -> list[tuple[str, str]]:
    return [(html.unescape(label), inner) for label, inner in AGENT_LIST.findall(body)]


def said_lines(page: str) -> list[str]:
    """Every sentence a press's page prints beneath its table."""
    return [html.unescape(text)
            for text in re.findall(r'<p class="said">([^<]*)</p>', main_of(page))]


def projects_controls(page: str) -> list[str]:
    """The action of every navigation labelled with the listing's word."""
    out = []
    for action, inner in GET_FORM.findall(page):
        button = GHOST.search(inner)
        if button is not None and button.group(1) == manage_web.ROSTER_HEAD:
            out.append(action)
    return out


def without_commands(page: str) -> str:
    """The page with every command frame cut out, so a path inside a
    command does not count as the page naming it."""
    return re.sub(r'<div class="cmd">.*?</div>', "", page, flags=re.S)


def test_the_project_page_leads_with_the_name_and_says_the_path_once(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 1: the heading is the folder's name; the path stands
    beneath it in the root block, and nowhere else outside a command."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    for project in (alpha, beta, gamma):
        page = project_page(run_, project)
        body = main_of(page)
        root = str(project.resolve())
        assert f"<h1>{project.name}</h1>" in body
        assert body.count("<h1>") == 1
        assert ('<div class="root"><span class="label">Project</span>'
                f"<code>{html.escape(root, quote=False)}</code></div>") in body
        assert body.index("<h1>") < body.index('<div class="root">')
        assert text_of(without_commands(page)).count(root) == 1, project.name
        # The lede, the caption and the one-row table are gone with
        # their constants.
        assert "<table" not in body
        assert '<p class="lede">' not in body
        for gone in ("PROJECT_HEAD", "PROJECT_LEDE", "PROJECT_CAPTION",
                     "ACTIONS_HEAD", "NOTHING_TO_DO", "PROJECT_COLUMNS"):
            assert not hasattr(manage_web, gone), gone
    assert run_.running


def test_the_projects_control_stands_top_and_bottom_and_lands_on_the_row(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 2: "Projects" above the heading and after the last
    section, both forms to the listing, whose action names the row this
    page was opened from by the id the listing row carries."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    listing = run_.page()
    for project in (alpha, beta, gamma):
        root = str(project.resolve())
        row_id = manage_web._row_id(root)
        assert re.fullmatch(r"row-[0-9a-f]{12}", row_id)
        assert main_of(listing).count(f'<tr id="{row_id}">') == 1
        page = project_page(run_, project)
        body = main_of(page)
        controls = projects_controls(page)
        assert controls == [f"{PATH}#{row_id}", f"{PATH}#{row_id}"], project.name
        # The first is the page's first control, above the heading; the
        # last is the page's last, after everything else.
        top = body.index('<div class="topbar">')
        assert top < body.index("<h1>")
        assert body.index("<form", top) < body.index("<h1>")
        bottom = body.rindex('<div class="bottombar">')
        assert bottom > body.rindex("</section>")
        assert body.rstrip().endswith("</form></div>")
        assert body.count(f'#{row_id}"') == 2
        # Same id from the same root however it is spelled, as far as
        # this platform folds a spelling: `normcase` folds case on
        # Windows and nothing on Linux, where two cases are two folders.
        assert manage_web._row_id(os.path.normcase(root)) == row_id
        if os.path.normcase("A") == "a":
            assert manage_web._row_id(root.upper()) == row_id
        else:
            assert manage_web._row_id(root.upper()) != row_id
        # A browser never sends the fragment; the listing answers and the
        # row is on it, with its id, so the fragment has somewhere to land.
        targets = [t for t in navigations(page, run_.session.token)
                   if "view=roster" in t]
        assert len(targets) == 2
        assert not any("#" in t for t in targets)
        status, back, _ = run_.get(targets[0])
        assert status == 200
        assert f'<tr id="{row_id}">' in main_of(back)
        assert listing_rows(back) == expected_listing(ro.listing())
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    bar = css.split(".topbar {", 1)[1].split("}", 1)[0]
    assert "position: sticky" in bar
    assert "top: 0" in bar
    assert "background: var(--ground)" in bar
    assert run_.running


def test_the_project_page_has_three_sections_in_order(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 3: Status, Skills, Connections, each a section with
    its heading; Status says the three facts of constraint 3 as blocks,
    and a section with nothing to list says so."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    assert (manage_web.STATUS_HEAD, manage_web.SKILLS_HEAD,
            manage_web.CONNECTIONS_HEAD) == ("Status", "Skills", "Connections")
    for project in (alpha, beta, gamma):
        page = project_page(run_, project)
        body = main_of(page)
        heads = SECTION_HEAD.findall(body)
        assert heads[:3] == ["Status", "Skills", "Connections"], project.name
        facts = ro.verify(project.resolve())
        assert FACT_BLOCK.findall(body) == facts_of(facts), project.name
        status = body.split(f"<h2>{manage_web.STATUS_HEAD}</h2>", 1)[1] \
            .split("</section>", 1)[0]
        assert status.count('<p class="fact ') == len(facts_of(facts)) == 3
        # The command line's remedy tail, with its placeholders, is on no
        # page since phase 3: the Status section writes the remedy out.
        assert report.PROJECTS_MISMATCH_TAIL not in html.unescape(body)
        if facts.connected == ro.CONNECTED_MISMATCH:
            assert ro.mismatch_clause(facts) in html.unescape(status)
            assert '<details class="command">' in status
        else:
            assert "<details" not in status
    # The unmounted project opens its Status with the word for it.
    assert '<p class="fact mounted">Not mounted</p>' in project_page(run_, gamma)
    # The mismatched project's Connections section is the repair's row,
    # since the record's client is detected here (phase 3): its label,
    # the repair sentence, and the one press.
    mismatch = main_of(project_page(run_, beta))
    connections = mismatch.split(f"<h2>{manage_web.CONNECTIONS_HEAD}</h2>", 1)[1] \
        .split("</section>", 1)[0]
    assert manage_web.CONNECTIONS_NONE not in connections
    assert connections.count("<h3") == 1
    assert run_.running


def test_a_name_and_its_sentence_are_separated_by_a_block_boundary(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 3, on the raw markup: every name on the page is a
    heading element, and what follows it is a block, never an inline
    span, so the accessibility text keeps a word between them."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    for project in (alpha, beta, gamma):
        body = main_of(project_page(run_, project))
        heads = ROW_HEAD.findall(body)
        assert heads, project.name
        # One skill row per catalog skill per agent (PLAN-0015 phase 2),
        # one client row on a registered project, and one on a mismatched
        # one whose client is detected here: the repair's (phase 3).
        expected = len(sk.catalog_names()) * len(sk.HARNESSES) + (
            1 if ro.verify(project.resolve()).connected in (
                CLIENT, ro.CONNECTED_MISMATCH) else 0)
        assert body.count("<h3 ") == expected == len(heads)
        for kind, name, following in heads:
            assert following == "<p", (name, following)
            assert f'<h3 class="{kind}">{name}</h3><p' in body
        assert '</span><span class="what">' not in body
        assert '<span class="name">' not in body
        # The skill names in mono, the client's label not. The agent's
        # label heads its list as a caption, never as a row heading.
        assert [name for kind, name, _ in heads if kind == "name"] == \
            list(sk.catalog_names()) * len(sk.HARNESSES)
        assert [label for label, _ in agent_lists(body)] == \
            [h.label for h in sk.HARNESSES.values()]
        for kind, name, _ in heads:
            if kind == "label":
                assert name == cn.CLIENTS[CLIENT].label
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert "Cascadia Mono" in css.split(".row h3.name {", 1)[1].split("}", 1)[0]
    assert run_.running


def test_every_press_names_its_target(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 4: each label is a template with a slot, filled from
    the catalog or the client table; each row says the skill's purpose
    from the catalog and its state, and offers one press."""
    import dataclasses as dc

    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    label = cn.CLIENTS[CLIENT].label
    assert manage_web.UNMOUNT_LABEL == "Unmount this project"
    assert (manage_web.SKILL_ADD_LABEL, manage_web.SKILL_REMOVE_LABEL,
            manage_web.CONNECT_LABEL, manage_web.UNCONNECT_LABEL) == (
        "Add {name}", "Remove {name}", "Connect {label}", "Disconnect {label}")
    assert manage_web.SKILL_ADD_ALL_LABEL == "Add all for {label}"
    for project in (alpha, beta, gamma):
        page = project_page(run_, project)
        body = main_of(page)
        facts = ro.verify(project.resolve())
        presses = [GHOST.search(inner).group(1) for inner in POST_PRESS.findall(body)]
        # One list per agent (PLAN-0015 phase 2): the whole-catalog press
        # above a list with a skill missing, then one press per row.
        wanted = []
        for agent, names in facts.agents:
            missing = [name for name in sk.catalog_names() if name not in names]
            if missing:
                wanted.append(f"Add all for {sk.HARNESSES[agent].label}")
            for name in sk.catalog_names():
                wanted.append(f"Remove {name}" if name in names else f"Add {name}")
        if facts.connected == CLIENT:
            wanted.append(f"Disconnect {label}")
        if facts.connected == ro.CONNECTED_MISMATCH:
            wanted.append(f"Repair the {label} connection")   # phase 3
        if facts.mounted:
            wanted.append(manage_web.UNMOUNT_LABEL)
        assert presses == wanted, project.name
        assert "Add it" not in body and "Remove it" not in body
        assert "Register it" not in body and "Remove the entries" not in body
        # Each skill row, in each agent's list: the catalog's one-line
        # purpose, then its state for that agent.
        for (agent, names), (heading, inner) in zip(facts.agents, agent_lists(body)):
            assert heading == sk.HARNESSES[agent].label
            for name in sk.catalog_names():
                entry = sk.catalog_entry(name)
                state = (manage_web.SKILL_INSTALLED if name in names
                         else manage_web.SKILL_NOT_INSTALLED)
                assert (f'<h3 class="name">{name}</h3>'
                        f'<p class="what">{html.escape(entry.summary, quote=False)}</p>'
                        f'<p class="state">{state}</p>') in inner, (agent, name)
        if facts.connected == CLIENT:
            assert (f'<h3 class="label">{label}</h3>'
                    f'<p class="state">{manage_web.CONNECTED_STATE}</p>') in body
    # The healthy project carries the one skill the fixture installed,
    # for every agent, so it reads Installed once per list.
    assert manage_web.SKILL_INSTALLED == "Installed"
    assert main_of(project_page(run_, alpha)).count(
        f'<p class="state">{manage_web.SKILL_INSTALLED}</p>') == len(sk.HARNESSES)
    # A skill one agent's record lists that the catalog does not carry
    # still gets a row in that agent's list: its state, no purpose, and
    # its removal for that agent alone.
    first = next(iter(sk.HARNESSES.values()))
    verified = ro.verify(alpha.resolve())
    alpha_state = dc.replace(
        verified, skills=("bygone", SKILL),
        agents=tuple((agent, ("bygone", SKILL) if agent == first.name else names)
                     for agent, names in verified.agents))
    page_state = setup_web.PageState(
        manage_web.PROJECT, report.OK, False, result=alpha_state,
        catalog=sk.states_all(alpha.resolve()))
    rows_ = manage_web._skill_rows(run_.session, page_state, first)
    assert len(rows_) == len(sk.catalog_names()) + 1
    assert ('<h3 class="name">bygone</h3>'
            f'<p class="state">{manage_web.SKILL_INSTALLED}</p>') in rows_[-1]
    assert "Remove bygone" in rows_[-1]
    assert f'name="harness" value="{first.name}"' in rows_[-1]
    assert '<p class="what">' not in rows_[-1]
    for other in list(sk.HARNESSES.values())[1:]:
        assert "bygone" not in "".join(
            manage_web._skill_rows(run_.session, page_state, other))
    # The orphan block on the listing names its target too.
    assert manage_web.ORPHAN_LABEL == "Remove {key}"
    assert run_.running


def test_the_skills_are_two_lists_and_add_all_fills_one_agents(
    tmp_path, env, client_here, command, capsys, manage,
):
    """PLAN-0015 phase 2's done-condition, on the page.

    A project with the one skill installed for the first agent only, and
    every skill for that agent once its whole-catalog press has run: the
    skill reads Installed with Remove under that agent's label and Not
    installed with Add under the other's; "Add all for <label>" stands
    above the other's rows and not above the first's; pressing it
    previews one head line for that agent and the created paths once,
    and after Run its list reads Installed on every row and the press is
    gone. The Status fact reads per agent while the lists differ and as
    one count when they match. No sentence about a split anywhere.
    """
    alpha, _, _ = three_projects(tmp_path, capsys, client_here)
    first, second = list(sk.HARNESSES.values())[:2]
    root = str(alpha.resolve())
    assert run(capsys, "skill", "remove", SKILL, root, "--harness", second.name)[0] == 0
    assert run(capsys, "skill", "add", "--all", root, "--harness", first.name)[0] == 0
    assert not hasattr(manage_web, "SKILL_SPLIT")
    run_ = manage()

    page = project_page(run_, alpha)
    body = main_of(page)
    lists = dict(agent_lists(body))
    assert list(lists) == [h.label for h in sk.HARNESSES.values()]
    assert (f'<h3 class="name">{SKILL}</h3>'
            f'<p class="what">{html.escape(sk.catalog_entry(SKILL).summary, quote=False)}</p>'
            f'<p class="state">{manage_web.SKILL_INSTALLED}</p>') in lists[first.label]
    assert (f'<h3 class="name">{SKILL}</h3>'
            f'<p class="what">{html.escape(sk.catalog_entry(SKILL).summary, quote=False)}</p>'
            f'<p class="state">{manage_web.SKILL_NOT_INSTALLED}</p>') in lists[second.label]
    assert f"Remove {SKILL}" in lists[first.label] and f"Add {SKILL}" not in lists[first.label]
    assert f"Add {SKILL}" in lists[second.label] and f"Remove {SKILL}" not in lists[second.label]
    assert lists[first.label].count('<p class="state">') == len(sk.catalog_names())
    assert lists[first.label].count(f'<p class="state">{manage_web.SKILL_INSTALLED}</p>') \
        == len(sk.catalog_names())
    assert lists[second.label].count(
        f'<p class="state">{manage_web.SKILL_NOT_INSTALLED}</p>') == len(sk.catalog_names())
    # Every row's press names its agent, and its command narrows to it.
    for label, inner in lists.items():
        harness = next(h for h in sk.HARNESSES.values() if h.label == label)
        for press in POST_PRESS.findall(inner):
            assert dict(HIDDEN.findall(press))["harness"] == harness.name
        for copied in re.findall(r'data-copy="([^"]*)"', inner):
            assert html.unescape(copied).endswith(f" --harness {harness.name}"), copied
    # The whole-catalog press: above the second agent's rows, its own
    # command behind the disclosure, and none above the first's.
    add_all = f"Add all for {second.label}"
    assert add_all in lists[second.label]
    assert f"Add all for {first.label}" not in body
    assert lists[second.label].index(add_all) < lists[second.label].index('<ul class="rows">')
    assert "Add all for" not in lists[first.label]
    act = manage_web.Action(manage_web.SKILL_ADD_ALL, root=root, harness=second.name)
    assert act.command == f"radialrig skill add --all {root} --harness {second.name}"
    assert f'data-copy="{html.escape(act.command, quote=True)}"' in lists[second.label]
    # The Status fact, per agent while the lists differ.
    facts = ro.verify(alpha.resolve())
    assert not ro.agents_agree(facts)
    assert FACT_BLOCK.findall(body) == facts_of(facts)
    assert [said for which, said in FACT_BLOCK.findall(body) if which == "skills"] == [
        f"{first.label}: {len(sk.catalog_names())} skills", f"{second.label}: no skills"]
    # Nothing on the page says "harness" outside a command line, where
    # `--harness` is the flag's own name; the labels come from the map.
    assert "harness" not in text_of(without_commands(page)).lower()
    # The press, as the page drew it: the rehearsal first.
    press = next(dict(HIDDEN.findall(inner)) for inner in POST_PRESS.findall(body)
                 if add_all in inner)
    press.pop("token")
    assert press == {"action": manage_web.SKILL_ADD_ALL, "root": root,
                     "harness": second.name}
    before = ro.verify(alpha.resolve())
    plan = pressed(run_, press)
    assert ro.verify(alpha.resolve()) == before, "the rehearsal wrote something"
    # One head line for the press, naming every skill it writes and the
    # agent once; no per-skill head beneath the table repeats the root,
    # and nothing on the page says "harness".
    head = re.search(r"<h1>([^<]*)</h1>", main_of(plan)).group(1)
    missing = [entry for entry in sk.SKILL_CATALOG]
    assert head == html.escape(
        f"Would add {report._joined(f'{e.name} {e.version}' for e in missing)} "
        f"for {second.label} to {root}", quote=False)
    assert text_of(plan).count("Would add ") == 1
    assert text_of(without_commands(plan)).count(root) == 1
    assert first.label not in text_of(plan)
    assert "harness" not in text_of(without_commands(plan)).lower()
    drawn = rows(plan)
    created = [path for verb, path, _ in drawn if verb == mnt.CREATED]
    assert created and len(set(created)) == len(created)
    for entry in sk.SKILL_CATALOG:
        for rel in entry.files:
            assert f"{second.skills}/{entry.name}/{rel}" in created
    assert first.skills not in text_of(plan)
    # Constraint 4: the paths stand in the table and nowhere beneath it.
    for line in said_lines(plan):
        for path in created:
            assert path not in line, (line, path)
    assert main_of(plan).count(PRIMARY) == 1
    # Then the run.
    done = pressed(run_, press, confirm=True)
    assert manage_web.DONE_CAPTION in text_of(done)
    assert report.SKILL_NEXT_STEP in text_of(done)
    assert "harness" not in text_of(without_commands(done)).lower()
    assert re.search(r"<h1>([^<]*)</h1>", main_of(done)).group(1).startswith("Added ")
    assert text_of(done).count("Added ") == 1
    for line in said_lines(done):
        for path in created:
            assert path not in line, (line, path)
    after = ro.verify(alpha.resolve())
    assert after.skills == tuple(sorted(sk.catalog_names()))
    assert ro.agents_agree(after)
    # And the page, with every row Installed in both lists and the
    # press gone, and the Status fact one count again.
    page = project_page(run_, alpha)
    body = main_of(page)
    for label, inner in agent_lists(body):
        assert inner.count(f'<p class="state">{manage_web.SKILL_INSTALLED}</p>') \
            == len(sk.catalog_names()), label
        assert manage_web.SKILL_NOT_INSTALLED not in inner
    assert "Add all for" not in body
    assert [said for which, said in FACT_BLOCK.findall(body) if which == "skills"] == [
        f"{len(sk.catalog_names())} skills"]
    assert run_.running


def test_add_all_speaks_for_the_press_when_the_first_catalog_skill_stands(
    tmp_path, env, client_here, command, capsys, manage,
):
    """The whole-catalog press on a project where the catalog's first
    skill is already installed for that agent. The page's head names the
    skills the press writes, never "Already installed" for the one it
    does not touch; that skill's rows are not in the table, and the
    outcome is the write's, not a no-op's. After Run, the same head
    with the past verb, and the list reads Installed on every row.
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
    lists = dict(agent_lists(main_of(project_page(run_, alpha))))
    assert lists[second.label].count(f'<p class="state">{manage_web.SKILL_INSTALLED}</p>') \
        == len(sk.catalog_names())
    assert f"Add all for {second.label}" not in lists[second.label]
    # A press that finds every skill standing says so in one head, with
    # the current tail, and writes nothing.
    again = pressed(run_, press)
    head = html.unescape(re.search(r"<h1>([^<]*)</h1>", main_of(again)).group(1))
    assert head.startswith(f"Already installed for {second.label}: ")
    assert report.SKILL_CURRENT_TAIL in text_of(again)
    assert not rows(again)


def test_add_all_is_withheld_while_a_row_in_that_list_is_blocked(
    tmp_path, env, client_here, command, capsys, manage,
):
    """A list with a row the engine would refuse — a directory of the
    project's own at a skill's target — shows that row's reason and no
    press on it, as prescription 1 says; the whole-catalog press above
    the list is withheld too, because the engine composes every skill
    and refuses whole, so the press could only ever refuse. The other
    agent's list, with nothing blocked, keeps its press, and the rows
    still add the unblocked skills one by one.
    """
    alpha, _, _ = three_projects(tmp_path, capsys, client_here)
    first, second = list(sk.HARNESSES.values())[:2]
    root = alpha.resolve()
    other = next(name for name in sk.catalog_names() if name != SKILL)
    (root / second.skills / other).mkdir(parents=True)
    (root / second.skills / other / "ours.md").write_bytes(b"our own skill\n")
    run_ = manage()

    lists = dict(agent_lists(main_of(project_page(run_, alpha))))
    reason = mnt.REASONS[mnt.SKILL_DIR_FOREIGN]
    assert reason in lists[second.label] and reason not in lists[first.label]
    assert f"Add all for {second.label}" not in lists[second.label]
    assert f"Add all for {first.label}" in lists[first.label]
    assert f"Add {other}" not in lists[second.label]
    third = next(name for name in sk.catalog_names() if name not in (SKILL, other))
    assert f"Add {third}" in lists[second.label]
    # The engine's own answer to the press the page withheld is the
    # refusal, which is why the page does not offer it.
    act = manage_web.Action(manage_web.SKILL_ADD_ALL, root=str(root), harness=second.name)
    assert manage_web._skill_add_all(act, dry_run=True).kind == manage_web.REFUSED
    assert cli.main(["skill", "add", "--all", str(root), "--harness", second.name,
                     "--dry-run"]) == report.REFUSED
    capsys.readouterr()
    # Once the directory is out of the way, the press is back.
    shutil.rmtree(root / second.skills / other)
    lists = dict(agent_lists(main_of(project_page(run_, alpha))))
    assert f"Add all for {second.label}" in lists[second.label]


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


def test_the_command_sits_behind_a_disclosure_with_its_copy_button_named(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 5, and prescription 4's Copy button: every command on
    the project page is inside a native disclosure reading "Show
    command", with the Copy button inside it, named for its command.

    On the mismatched project (phase 3) one disclosure holds two frames:
    the remedy's, under the Status facts, and the repair press in the
    Connections section runs those two commands rather than carrying a
    second copy of them beside it."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    assert manage_web.SHOW_COMMAND == "Show command"
    for project in (alpha, beta, gamma):
        page = project_page(run_, project)
        body = main_of(page)
        mismatched = ro.verify(project.resolve()).connected == ro.CONNECTED_MISMATCH
        disclosed = DISCLOSED.findall(body)
        presses = POST_PRESS.findall(body)
        # One single-command disclosure per single-verb press; on the
        # mismatched project the repair press is the one press without
        # its own, and the remedy's disclosure stands in its place.
        singles = len(presses) - (1 if mismatched else 0)
        assert len(disclosed) == singles > 0, project.name
        assert body.count("<details") == len(presses)
        assert body.count('<details class="command">') == len(presses)
        frames = singles + (2 if mismatched else 0)
        assert len(COPY_BUTTON.findall(body)) == frames
        assert body.count('<div class="cmd">') == frames
        assert body.count(f"<summary>{manage_web.SHOW_COMMAND}</summary>") == len(presses)
        for summary, shown, copied, named in disclosed:
            assert summary == manage_web.SHOW_COMMAND
            assert html.unescape(shown) == html.unescape(copied)
            assert html.unescape(named) == manage_web.COPY_NAME.format(
                command=html.unescape(copied))
            assert html.unescape(copied).startswith("radialrig ")
        # Every press's command is the one its own fields name; the
        # repair's are the two verbs it runs, in order.
        for inner in presses:
            fields = dict(HIDDEN.findall(inner))
            if fields["action"] == manage_web.REPAIR:
                acts = manage_web.repair_steps(ro.verify(project.resolve()))
                assert [act.name for act in acts] == [
                    manage_web.UNCONNECT, manage_web.CONNECT]
            else:
                acts = (manage_web.Action(
                    fields["action"], root=fields.get("root", ""),
                    skill=fields.get("skill", ""), client=fields.get("client", ""),
                    harness=fields.get("harness", "")),)
            for act in acts:
                assert f'data-copy="{html.escape(act.command, quote=True)}"' in body
    js = (packaged_web() / "page.js").read_text(encoding="utf-8")
    assert "button.textContent" in js
    assert "aria-label" not in js
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert "max-width: none" in css.split(
        "details.command .cmd {", 1)[1].split("}", 1)[0]
    assert re.search(r"^\.cmd \{", css, re.M)
    assert ".steps .cmd" not in css
    assert run_.running


def test_the_plan_confirm_and_report_pages_keep_the_command_in_full(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Constraint 1: the disclosure is the project page's alone. A press's
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
        assert manage_web.SHOW_COMMAND not in text_of(page), name
        assert body.count(frame) == 1, name
    assert main_of(plan).count(PRIMARY) == 1
    assert main_of(done).count(PRIMARY) == 0
    # The removal view names each step by the same label the project
    # page's press carries, its slot filled, beside its whole command.
    removal = view(run_, manage_web.REMOVAL)
    body = main_of(removal)
    assert "<details" not in body
    assert '<span class="what">Remove plan-review</span>' in body
    assert f'<span class="what">Disconnect {cn.CLIENTS[CLIENT].label}</span>' in body
    assert f'<span class="what">{manage_web.UNMOUNT_LABEL}</span>' in body
    assert run_.running


def test_the_framed_unmount_stands_only_on_a_mounted_project(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 6: the unmount leaves the sections and stands framed
    alone at the page end, a ghost press under one sentence, on a
    mounted project and on no other; a mounted project's page keeps no
    amber, and an unmounted one's amber is the mount press (phase 3)."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    run_ = manage()
    for project, mounted in ((alpha, True), (beta, True), (gamma, False)):
        page = project_page(run_, project)
        body = main_of(page)
        assert body.count(PRIMARY) == (0 if mounted else 1), project.name
        assert body.count('<section class="reversal">') == (1 if mounted else 0)
        assert (manage_web.UNMOUNT_LABEL in body) is mounted, project.name
        if not mounted:
            assert f'value="{manage_web.UNMOUNT}"' not in body
            continue
        frame = body.split('<section class="reversal">', 1)[1].split("</section>", 1)[0]
        assert frame.startswith(
            f"<h2>{manage_web.UNMOUNT_LABEL}</h2>"
            f"<p>{html.escape(manage_web.UNMOUNT_WHAT, quote=False)}</p>")
        assert ('<div class="actions"><button class="ghost" type="submit">'
                f"{manage_web.UNMOUNT_LABEL}</button></div>") in frame
        assert f'value="{manage_web.UNMOUNT}"' in frame
        assert body.count(f'value="{manage_web.UNMOUNT}"') == 1
        assert '<details class="command">' in frame
        # After the last section, before the bottom control.
        assert body.index('<section class="reversal">') > body.index(
            f"<h2>{manage_web.CONNECTIONS_HEAD}</h2>")
        assert body.index('<section class="reversal">') < body.index(
            '<div class="bottombar">')
    assert "your own files" in manage_web.UNMOUNT_WHAT
    assert run_.running


def test_the_listing_frame_counts_the_projects_it_would_reverse(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 6, on the listing: the frame's sentence carries the
    count of standing projects, and says none where none is listed."""
    run_ = manage()

    def frame_sentence() -> str:
        body = main_of(run_.page())
        frame = body.split('<section class="reversal">', 1)[1]
        return html.unescape(frame.split("<p>", 1)[1].split("</p>", 1)[0])

    none = manage_web.REMOVAL_WHAT.format(scope=manage_web.REMOVAL_NONE_LISTED)
    assert frame_sentence() == none
    assert "none is listed" in none
    registered(capsys, _make_project(tmp_path / "one"))
    one = manage_web.REMOVAL_WHAT.format(scope=manage_web.REMOVAL_ONE)
    assert frame_sentence() == one
    assert "1 project" in one and "projects" not in one
    registered(capsys, _make_project(tmp_path / "two"))
    registered(capsys, _make_project(tmp_path / "three"))
    assert len(ro.listing()) == 3
    many = manage_web.REMOVAL_WHAT.format(
        scope=manage_web.REMOVAL_MANY.format(count=3))
    assert frame_sentence() == many
    assert "3 projects" in many
    assert "{" not in none + one + many
    assert run_.running


# --- PLAN-0014 phase 3: a mismatch names its client and offers the repair;
# an unmounted project offers the mount. The done-condition's pins, on the
# same three-project roster, one test per pin.

def status_of(page: str) -> str:
    """The Status section's markup, heading excluded."""
    return main_of(page).split(f"<h2>{manage_web.STATUS_HEAD}</h2>", 1)[1] \
        .split("</section>", 1)[0]


def section_of(page: str, head: str) -> str:
    """One section's markup, heading excluded."""
    return main_of(page).split(f"<h2>{head}</h2>", 1)[1].split("</section>", 1)[0]


def repair_commands(root: Path) -> list[str]:
    """The two commands the repair runs, in order, as the page spells them."""
    return [
        manage_web.Action(manage_web.UNCONNECT, root=str(root), client=CLIENT).command,
        manage_web.Action(manage_web.CONNECT, root=str(root), client=CLIENT).command,
    ]


def test_a_mismatch_names_its_client_on_every_surface_and_fills_the_commands(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescriptions 1 and 2: one sentence, three surfaces, filled in.

    The listing row, the project page and the removal page say the same
    sentence about the mismatched record, naming the client the record
    claims by its label from the table; the command line's row says it
    too. The project page writes the two-verb remedy out under the
    Status facts with the client and the path filled, behind the phase 2
    disclosure; the listing drops the tail with the placeholders and the
    command line keeps it.
    """
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = beta.resolve()
    facts = ro.verify(root)
    assert facts.claimed == CLIENT
    label = cn.CLIENTS[CLIENT].label
    sentence = f"{label}'s settings no longer carry the entry this project's record claims"
    assert ro.mismatch_clause(facts) == sentence
    assert ro.MISMATCH_CLAUSE == \
        "{label}'s settings no longer carry the entry this project's record claims"
    run_ = manage()

    listing = run_.page()
    by_name = {name: facts_ for name, _, _, facts_ in listing_rows(listing)}
    assert ("connection", sentence) in by_name["beta"]
    assert sentence in text_of(listing)
    assert report.PROJECTS_MISMATCH_TAIL not in text_of(listing)

    page = project_page(run_, beta)
    status = status_of(page)
    assert f'<p class="fact connection">{html.escape(sentence, quote=False)}</p>' in status
    assert manage_web.REPAIR_NOTE in html.unescape(status)
    # The remedy: both commands, in the order they run, behind one
    # disclosure, each with its Copy button named for it.
    assert status.count('<details class="command">') == 1
    disclosed = status.split('<details class="command">', 1)[1].split("</details>", 1)[0]
    assert disclosed.startswith(f"<summary>{manage_web.SHOW_COMMAND}</summary>")
    copied = [html.unescape(c) for c in re.findall(r'data-copy="([^"]*)"', disclosed)]
    assert copied == repair_commands(root)
    assert copied == [f"radialrig unconnect {CLIENT} {root}",
                      f"radialrig connect {CLIENT} {root}"]
    for line in copied:
        assert f'aria-label="{html.escape(manage_web.COPY_NAME.format(command=line), quote=True)}"' in disclosed
    said = text_of(page)
    assert "<client>" not in said and "<PATH>" not in said
    assert report.PROJECTS_MISMATCH_TAIL not in said
    assert "{label}" not in said and "{file}" not in said

    removal = view(run_, manage_web.REMOVAL)
    assert sentence in text_of(removal)
    assert report.PROJECTS_MISMATCH_TAIL not in text_of(removal)
    block = next(part for part in main_of(removal).split("<fieldset")
                 if f"<legend>{html.escape(str(root), quote=False)}</legend>" in part)
    copied = [html.unescape(c) for c in re.findall(r'data-copy="([^"]*)"', block)]
    assert copied[:2] == repair_commands(root)
    assert "<details" not in block

    code, out, _ = run(capsys, "projects")
    assert code == 0
    assert sentence in out
    assert report.PROJECTS_MISMATCH_TAIL in out
    # The healthy and the unmounted project say nothing of the kind.
    for project in (alpha, gamma):
        assert sentence not in text_of(project_page(run_, project))
        assert manage_web.REPAIR_NOTE not in text_of(project_page(run_, project))
    assert run_.running


def test_the_repair_row_stands_only_where_the_client_is_detected(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage,
):
    """Prescription 3's gate, and constraint 7 narrowed.

    With the client detected here, the Connections section is one row:
    the client's label, the repair sentence, the one press, and no
    connect or disconnect press. With `APPDATA` narrowed to a profile
    holding no client — the way the no-client test narrows it — the
    label still stands in the sentence about the record, and no press
    for that client stands anywhere on the page. With the read server's
    command unlocatable, the same.
    """
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = beta.resolve()
    label = cn.CLIENTS[CLIENT].label
    sentence = ro.mismatch_clause(ro.verify(root))
    assert manage_web.REPAIR_LABEL == "Repair the {label} connection"

    run_ = manage()
    page = project_page(run_, beta)
    connections = section_of(page, manage_web.CONNECTIONS_HEAD)
    assert connections.count("<h3") == 1
    assert (f'<h3 class="label">{label}</h3>'
            f'<p class="state">{html.escape(sentence, quote=False)}</p>') in connections
    presses = POST_PRESS.findall(connections)
    assert len(presses) == 1
    fields = dict(HIDDEN.findall(presses[0]))
    assert fields == {"token": run_.session.token, "action": manage_web.REPAIR,
                      "root": str(root)}
    assert GHOST.search(presses[0]).group(1) == f"Repair the {label} connection"
    assert "<details" not in connections
    body = main_of(page)
    assert f'value="{manage_web.CONNECT}"' not in body
    assert f'value="{manage_web.UNCONNECT}"' not in body
    assert body.count(f'value="{manage_web.REPAIR}"') == 1
    assert main_of(page).count(PRIMARY) == 0

    # The client not detected: the sentence still names it; no press.
    monkeypatch.setenv("APPDATA", str(tmp_path / "no-client"))
    assert cn.detect(cn.CLIENTS[CLIENT]) == ()
    assert ro.verify(root).connected == ro.CONNECTED_MISMATCH
    page = project_page(run_, beta)
    body = main_of(page)
    assert sentence in text_of(page)
    assert label in text_of(page)
    assert f'<p class="hint">{manage_web.CONNECTIONS_NONE}</p>' in \
        section_of(page, manage_web.CONNECTIONS_HEAD)
    for value in (manage_web.REPAIR, manage_web.CONNECT, manage_web.UNCONNECT):
        assert f'value="{value}"' not in body, value
    assert manage_web.REPAIR_LABEL.format(label=label) not in body
    # The Status section still carries the sentence and the filled remedy.
    status = status_of(page)
    assert html.escape(sentence, quote=False) in status
    assert [html.unescape(c) for c in re.findall(r'data-copy="([^"]*)"', status)] \
        == repair_commands(root)

    # The read server's command not locatable: the same.
    monkeypatch.setenv("APPDATA", env["APPDATA"])
    assert cn.detect(cn.CLIENTS[CLIENT])
    monkeypatch.setattr(cn, "_which", lambda name: None)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "nowhere" / "python.exe"))
    monkeypatch.setattr(sys, "argv", [""])
    with pytest.raises(cn.ConnectError):
        cn.read_command()
    page = project_page(run_, beta)
    assert sentence in text_of(page)
    assert f'value="{manage_web.REPAIR}"' not in main_of(page)
    assert manage_web.CONNECTIONS_NONE in section_of(page, manage_web.CONNECTIONS_HEAD)
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
    assert manage_web.REMOVAL_BACK_LABEL not in text_of(plan)

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
    """Prescription 3's confirm: the two verbs run in order; the client's
    settings carry the record's entries again, the state reads the
    client's name, and the report is the two reports."""
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
    # The listing no longer carries the chip, and the command line agrees.
    listing = run_.page()
    assert REPAIR not in text_of(listing)
    assert [chip for _, _, chip, _ in listing_rows(listing)] == ["", "", ""]
    code, porcelain, _ = run(capsys, "projects", "--porcelain")
    assert code == 0
    assert f"root={root}\tmounted=yes\tconnected={CLIENT}\t" in porcelain
    # The project page now offers the disconnect, no repair.
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
    """Prescription 1's other sentence: a record this build cannot read a
    client off names the record file, on every surface, and the page
    offers no press for a client it cannot name — no remedy, no repair,
    no connect."""
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
    assert ro.UNKNOWN_CLIENT_CLAUSE == \
        "This project's record names no client this build knows: see {file}"
    label = cn.CLIENTS[CLIENT].label
    run_ = manage()

    listing = run_.page()
    by_name = {name: facts_ for name, _, _, facts_ in listing_rows(listing)}
    assert ("connection", sentence) in by_name["beta"]
    assert [chip for name, _, chip, _ in listing_rows(listing) if name == "beta"] == [REPAIR]

    page = project_page(run_, beta)
    status = status_of(page)
    assert f'<p class="fact connection">{html.escape(sentence, quote=False)}</p>' in status
    assert "<details" not in status
    assert manage_web.REPAIR_NOTE not in text_of(page)
    assert "data-copy" not in status
    connections = section_of(page, manage_web.CONNECTIONS_HEAD)
    assert connections == f'<p class="hint">{manage_web.CONNECTIONS_NONE}</p>'
    body = main_of(page)
    for value in (manage_web.REPAIR, manage_web.CONNECT, manage_web.UNCONNECT):
        assert f'value="{value}"' not in body, value
    assert label not in text_of(page)
    assert body.count(PRIMARY) == 0

    removal = view(run_, manage_web.REMOVAL)
    assert sentence in text_of(removal)
    block = next(part for part in main_of(removal).split("<fieldset")
                 if f"<legend>{html.escape(str(root), quote=False)}</legend>" in part)
    assert re.findall(r'data-copy="([^"]*)"', block) == [f"radialrig unmount {root}"]

    code, out, _ = run(capsys, "projects")
    assert code == 0
    assert sentence in out
    code, porcelain, _ = run(capsys, "projects", "--porcelain")
    assert f"root={root}\tmounted=yes\tconnected={report.PROJECTS_MISMATCH}\t" in porcelain

    # A record that will not parse reads the same way.
    record.write_bytes(b"{ not json\n")
    facts = ro.verify(root)
    assert (facts.connected, facts.claimed) == (ro.CONNECTED_MISMATCH, "")
    assert sentence in text_of(project_page(run_, beta))
    assert run_.running


def test_an_unmounted_project_offers_the_mount_press(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 4: "Not mounted" opens the Status section and one
    press under it, the page's one amber, a form to the walkthrough on
    this folder; the Skills and Connections sections say once that their
    presses stand without the mount; a mounted project has none of it."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = gamma.resolve()
    assert manage_web.MOUNT_LABEL == "Mount this project"
    assert manage_web.NO_MOUNT_NEEDED == \
        "Each of these stands on its own and does not need the mount."
    run_ = manage()

    page = project_page(run_, gamma)
    body = main_of(page)
    status = status_of(page)
    assert status.startswith('<div class="facts"><p class="fact mounted">Not mounted</p>')
    assert body.count(PRIMARY) == 1
    assert PRIMARY in status
    press = next(
        (action, inner) for action, inner in GET_FORM.findall(status)
        if PRIMARY in inner)
    action, inner = press
    assert action == PATH
    assert dict(HIDDEN.findall(inner)) == {
        "s": run_.session.token, "view": manage_web.SETUP, "root": str(root)}
    assert (f'<div class="actions"><button class="primary" type="submit">'
            f"{manage_web.MOUNT_LABEL}</button></div>") in inner
    assert status.index('<div class="facts">') < status.index(PRIMARY)
    # Once above the rows of each of the two sections, and nowhere else:
    # above the agents' lists in Skills, above the rows in Connections.
    assert section_of(page, manage_web.SKILLS_HEAD).startswith(
        f'<p class="hint">{manage_web.NO_MOUNT_NEEDED}</p>'
        '<fieldset class="skills agent"><legend>')
    assert section_of(page, manage_web.CONNECTIONS_HEAD).startswith(
        f'<p class="hint">{manage_web.NO_MOUNT_NEEDED}</p><ul class="rows">')
    assert body.count(manage_web.NO_MOUNT_NEEDED) == 2
    assert manage_web.NO_MOUNT_NEEDED not in status
    # The press leads to the walkthrough on this folder: the mount plan.
    target = next(t for t in navigations(page, run_.session.token)
                  if f"view={manage_web.SETUP}" in t)
    status_code, walkthrough, _ = run_.get(target)
    assert status_code == 200
    assert setup_web.MOUNT_LABEL in walkthrough
    assert f"radialrig mount {root}" in text_of(walkthrough)
    assert not mnt.is_mounted(root), "the press wrote something"
    # A mounted project's page has none of this.
    for project in (alpha, beta):
        mounted = project_page(run_, project)
        assert main_of(mounted).count(PRIMARY) == 0, project.name
        assert manage_web.MOUNT_LABEL not in main_of(mounted)
        assert manage_web.NO_MOUNT_NEEDED not in main_of(mounted)
        assert f"view={manage_web.SETUP}" not in "".join(
            navigations(mounted, run_.session.token))
    css = (packaged_web() / "page.css").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert ".facts + form.nav .actions {" in css
    assert run_.running


def test_no_offered_press_is_refused_on_an_unmounted_project(
    tmp_path, env, client_here, command, capsys, manage,
):
    """Prescription 4's other half: every press the unmounted page
    offers stands without the mount. Each confirm is posted as the page
    drew it, and the engine answers with a report, never a refusal."""
    alpha, beta, gamma = three_projects(tmp_path, capsys, client_here)
    root = gamma.resolve()
    run_ = manage()
    page = project_page(run_, gamma)
    body = main_of(page)
    presses = [dict(HIDDEN.findall(inner)) for inner in POST_PRESS.findall(body)]
    # Per agent, the whole-catalog press and one add per skill; then the
    # registration's removal.
    assert len(presses) == (len(sk.catalog_names()) + 1) * len(sk.HARNESSES) + 1
    assert {press["action"] for press in presses} == {
        manage_web.SKILL_ADD, manage_web.SKILL_ADD_ALL, manage_web.UNCONNECT}
    assert {press.get("harness") for press in presses[:-1]} == set(sk.HARNESSES)
    for fields in presses:
        fields.pop("token")
        done = pressed(run_, fields, confirm=True)
        said = text_of(done)
        assert manage_web.DONE_CAPTION in said, fields
        assert '<div class="refusal"' not in done, fields
        assert manage_web.REFUSED_CAPTION not in said, fields
        assert manage_web.ACTION_FAILED_HEAD not in said, fields
        assert manage_web.ROSTER_HEAD not in said, fields
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
    assert listing_rows(page) == []
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


def test_the_listing_reads_the_way_the_accepted_wireframe_writes_it(
    tmp_path, env, capsys, manage,
):
    """The manage surface's own shape, word for word.

    PLAN-0014 phase 1's listing, which replaces the wireframe of
    2026-08-21: one table carrying the project, its three facts and the
    control that opens it, under a heading with the add control beside
    it; the buttons named the way the plan names them; no count in the
    heading and no command frame anywhere; and the one destructive press
    framed alone at the page end.
    """
    assert run(capsys, "mount", str(_make_project(tmp_path / "alpha")))[0] == 0

    page = manage().page()
    body = main_of(page)
    said = text_of(page)

    # The masthead, the heading row, and the columns.
    assert f'<span class="name">{manage_web.TITLE}</span>' in page
    assert (f'<div class="head-row"><h1>{manage_web.ROSTER_HEAD}</h1>'
            f'<a class="ghost" href="#{manage_web.ADD_ID}">'
            f"{manage_web.ADD_HEAD}</a></div>") in body
    assert manage_web.ROSTER_COLUMNS == ("Project", "Status")
    for head in manage_web.ROSTER_COLUMNS:
        assert f'<th scope="col">{head}</th>' in body
    assert ('<th scope="col"><span class="sr">'
            f"{manage_web.OPEN_LABEL}</span></th>") in body

    # One table, and the control is a cell on the row rather than a
    # second copy of the listing beneath it.
    assert body.count("<table") == 1
    assert ('<button class="ghost row-open" type="submit">'
            f"{manage_web.OPEN_LABEL}</button>") in body
    assert body.count(manage_web.OPEN_LABEL) == 2   # the row, and the head

    # No command frame on this surface, and no count in the heading.
    assert '<div class="cmd">' not in body
    assert "radialrig projects" not in said
    assert report.PROJECTS_HEAD not in said

    # The folder row, with the picker's button beside the field, the line
    # saying the picker fills it, and the one amber press under it, which
    # opens the mount plan and is rendered enabled for the script to hold.
    assert f'<fieldset class="skills" id="{manage_web.ADD_ID}">' in body
    assert '<div class="picker">' in body
    assert (f'<button class="ghost" type="button" id="browse" hidden>'
            f"{manage_web.BROWSE_LABEL}") in body
    assert ('<div class="actions"><button class="primary" type="submit">'
            f"{manage_web.ADD_LABEL}</button></div>") in body
    assert manage_web.ADD_LABEL == "Review the mount plan"
    assert "disabled" not in body
    assert manage_web.ADD_LEDE in said
    assert setup_web.MOUNT_LABEL in manage_web.ADD_LEDE
    assert manage_web.ADD_PICKER_NOTE in said

    # And the reversal, framed and last.
    assert body.rstrip().endswith("</section>")
    frame = body.split('<section class="reversal">', 1)[1]
    assert f"<h2>{manage_web.REMOVAL_LABEL}</h2>" in frame
    assert manage_web.REMOVAL_WHAT.format(scope=manage_web.REMOVAL_ONE) in said
    assert ('<div class="actions"><button class="ghost" type="submit">'
            f"{manage_web.REMOVAL_LABEL}</button></div>") in frame


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

    for name in (manage_web.PROJECT, manage_web.SETUP):
        said = text_of(run_.page(f"&view={name}&root={quoted}"))
        assert manage_web.MISSING_HEAD in said, name
        assert manage_web.NO_DIRECTORY.format(path=missing) in said, name
        assert manage_web.ACTION_FAILED_HEAD not in said, name

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
            # to the listing, and the project page carries its Status
            # section (PLAN-0014 phase 2); the listing carries neither.
            assert f"<h1>{manage_web.ROSTER_HEAD}</h1>" in main_of(page), (name, field)
            assert manage_web.ROSTER_BACK_LABEL not in said, (name, field)
            assert f"<h2>{manage_web.STATUS_HEAD}</h2>" not in page, (name, field)
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
    return " ".join(html.unescape(TAGS.sub(" ", STYLED.sub(" ", page))).split())


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
    the walkthrough that adds a project, then every press twice — the
    rehearsal and the run — then the two ways a press stops, then a whole
    reversal, and last the two rosters a reader can be left holding.

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

    # One entry whose project is gone, so the orphan block renders too.
    key = f"{PREFIX}old"
    body = json.loads(client_here.read_text(encoding="utf-8"))
    body["mcpServers"][key] = {
        "command": "x", "args": [],
        "env": {ROOT_ENV: str(tmp_path / "vanished")},
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

    # The repair (PLAN-0014 phase 3), in its two presses on the
    # mismatched project: the rehearsal and the run. It leaves beta
    # registered again, which nothing below reads.
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
        # nothing behind: the rehearsal and the run each say so.
        ("update", {"action": manage_web.UPDATE, "root": str(alpha)}),
        ("update all", {"action": manage_web.UPDATE_ALL}),
        ("unmount", {"action": manage_web.UNMOUNT, "root": str(alpha)}),
    ):
        pages[f"{name} plan"] = pressed(run_, fields)
        pages[f"{name} done"] = pressed(run_, fields, confirm=True)

    # The two ways a press stops: refused, and never run at all.
    pages["refused"] = pressed(run_, {
        "action": manage_web.SKILL_ADD, "root": str(foreign), "skill": SKILL})
    pages["orphan refused"] = pressed(run_, {
        "action": manage_web.ORPHAN, "client": CLIENT, "key": "someone-elses",
    }, confirm=True)
    pages["failed"] = pressed(run_, {
        "action": manage_web.UNMOUNT, "root": str(tmp_path / "never-was"),
    }, confirm=True)

    # The whole reversal, on the project the walkthrough walked on, in
    # the two presses it takes: the rehearsal and the run.
    reversal = {"action": manage_web.REVERSAL, "root": str(fresh)}
    pages["reversal plan"] = pressed(run_, reversal)
    pages["reversed"] = pressed(run_, reversal, confirm=True)

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

    On the listing the accepted wireframe draws it exactly once: the
    press that writes. The stylesheet is shared with the setup surface,
    whose own accepted wireframe outlines the chip on a row that touches
    a file already standing rather than filling it, so a plan table under
    a press here brings no second amber with it. Nothing on the page
    paints itself, so there is no third way for the amber to appear.
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
    assert ".chip.touches {\n  background: transparent;" in css

    # The amber means one thing on every screen: the press that writes,
    # or — on a view that only leads onward — the one press that view is
    # for. A rehearsal carries its confirm, the whole-project rehearsal as
    # much as a single verb's, which is why the view that lists the
    # reversals carries none: each press there leads to that project's
    # rehearsal, and the button that writes lives under it. A state that
    # has already run carries none, and so does a refusal, which has
    # nothing to press.
    # An unmounted project's page carries one: the press that opens its
    # mount plan (PLAN-0014 phase 3), the way the listing's does. The
    # repair's rehearsal carries its confirm, and its report none.
    for name, expected in (
        ("roster", 1), ("project", 0), ("mismatch", 0), ("unmounted", 1),
        ("gone", 0),
        ("walkthrough", 1), ("walkthrough done", 0), ("removal", 0),
        ("skill add plan", 1), ("skill add done", 0),
        ("unmount plan", 1), ("unmount done", 0),
        ("refused", 0), ("orphan refused", 0), ("failed", 0),
        ("reversal plan", 1), ("reversed", 0), ("empty removal", 0),
        ("repair plan", 1), ("repaired", 0),
        ("update plan", 1), ("update done", 0),
        ("update all plan", 1), ("update all done", 0),
    ):
        assert pages[name].count(PRIMARY) == expected, name

    # And the listing's one chip (PLAN-0014 phase 1, under the wireframe
    # revisit the plan's Objective names): the row whose record the
    # client contradicts carries the outlined `touches` chip and its
    # words, and no other row carries any. The neutral chip that used to
    # read `current` or `mismatch` on every row is gone from this screen,
    # and the outline is the setup page's own treatment, not the amber:
    # the amber on this screen is still the press, counted above.
    roster = pages["roster"]
    assert CHIP.findall(roster) == [], "a neutral chip is back on the listing"
    assert roster.count('<span class="chip touches">') == 1
    assert roster.count(manage_web.REPAIR_CHIP) == 1
    assert '<span class="chip">' not in roster


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
