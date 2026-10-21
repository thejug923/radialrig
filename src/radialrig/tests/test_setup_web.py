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

# One agent's group of boxes on the page: its label as the legend, the
# Tick all control beside it, then everything under it.
AGENT_GROUP = re.compile(
    r'<fieldset class="skills agent"><legend>([^<]*)</legend>(<button class="tick" '
    r'type="button" aria-label="[^"]*">[^<]*</button>)?(.*?)</fieldset>', re.S)
BOX = re.compile(r'<input type="checkbox" name="skill" value="([^"]*)"([^>]*)>')


def boxes(*names: str, agents=None) -> list[str]:
    """What the boxes post for these skills: one value per (agent, skill)
    pair, for every agent in the map or the ones given."""
    return [setup_web.box_value(harness.name, name)
            for harness in (agents or AGENTS) for name in names]


def groups(page: str) -> list[tuple[str, bool, list[tuple[str, str]]]]:
    """Every agent's group on the page: its label, whether Tick all stands
    beside it, and its boxes as (value, attributes)."""
    return [
        (html.unescape(label), bool(tick), BOX.findall(inner))
        for label, tick, inner in AGENT_GROUP.findall(page)
    ]

ROW = re.compile(
    r'<tr><td class="verb"><span class="chip[^"]*">([a-z]+)</span></td>'
    r'<td class="path">([^<]*)</td><td class="why">([^<]*)</td></tr>'
)
TAGS = re.compile(r"<[^>]+>")
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
    for name in ("APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)


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

def rows(page: str) -> list[tuple[str, str, str]]:
    import html as _html

    return [
        tuple(_html.unescape(cell) for cell in match)
        for match in ROW.findall(page)
    ]


def expected_rows(dispositions) -> list[tuple[str, str, str]]:
    # The page's fill for the engine's empty wordings rides along here;
    # the sentences themselves are pinned word for word in
    # test_manage_actions' wrote-table test.
    out = []
    for d in dispositions:
        reason = (report.conflict_reason(d.path) if d.code == "CONFLICT"
                  else mnt.reason_text(d)
                  or setup_web.WROTE_REASONS.get(d.code, ""))
        out.append((d.verb, d.path, reason))
    return out


def visible(page: str) -> str:
    body = page.split("<main>", 1)[-1].split("</main>", 1)[0]
    return TAGS.sub(" ", body)


def text_of(page: str) -> str:
    """What the page reads as: tags gone, runs of whitespace collapsed.

    A line of the command line's own report can be split across elements
    — the already-mounted heading sets the project path in its own
    `<code>` — so parity is checked against the rendered text rather than
    against raw markup.
    """
    import html as _html

    return " ".join(_html.unescape(visible(page)).split())


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
    assert setup_web.REVIEW_HEAD in review
    assert tree(page_root) == tree(cli_root), "the review state wrote something"

    status, mounted, _ = run.post(mount_form(run))
    assert status == 200
    assert f"Mounted onto {SCOPE}." in mounted
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

    # And the page reported exactly the command line's report lines.
    assert rows(mounted) == expected_rows(result.dispositions())
    assert report.next_step(page_root)[0] in mounted


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


# The seven rows the accepted wireframe draws over the twelve paths a
# mount writes, in its order and in its words. Spelled out here rather than read
# off `MOUNT_FAMILIES`: a table pinned against the table that renders it
# pins nothing.
FAMILY_ROWS = [
    (mnt.MERGED, ".mcp.json",
     "one RadialRig entry added; nothing else in the file changes"),
    (mnt.CREATED, ".radialrig/mount.json",
     "the record RadialRig uses to take everything back out"),
    (mnt.APPENDED, "AGENTS.md",
     "one fenced section at the end; the rest of the file is unchanged"),
    (mnt.CREATED, "CLAUDE.md",
     "one fenced line importing AGENTS.md, so Claude Code reads the route map"),
    (mnt.CREATED, ".codex/config.toml",
     "the same two servers registered for Codex, in one fenced region at the end"),
    (mnt.CREATED, "docs/decisions/, docs/plans/, docs/archive/",
     "three document folders, kept in git even while empty"),
    (mnt.CREATED, "docs/archive/D-00-archive-exclusion-target.md",
     "one archived record, so the archive search filter has something to find"),
    (mnt.CREATED, "docs/spec.md, docs/spec-git-environment.md",
     "rules for this project's records, and default branch, pull-request "
     "and CI rules that agents follow"),
    (mnt.CREATED, "docs/templates/",
     "three blank templates: decision, plan, spec"),
]


def test_the_review_state_shows_the_whole_plan_and_writes_nothing(
    tmp_path, walk, fixed_day,
):
    root = _host(tmp_path / "host")
    before = tree(root)
    run = walk(root, scope=SCOPE)
    review = run.page()

    plan = mnt.mount_plan(root, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    assert rows(review) == FAMILY_ROWS
    # Seven rows, and between them every path the plan holds: the wireframe
    # groups the plan, it never drops a line of it. A path a later phase
    # adds falls outside every family and takes a row of its own, so the
    # table cannot silently stop reporting one.
    named: set[str] = set()
    for family in setup_web.MOUNT_FAMILIES:
        named.update(family.paths)
    assert named == {d.path for d in plan.dispositions()} == EXPECTED
    assert tree(root) == before
    # The rows that touch a file already standing carry the outlined
    # chip; a created row does not, and the line under the table says
    # which is which.
    assert '<span class="chip touches">merged</span>' in review
    assert '<span class="chip touches">appended</span>' in review
    assert '<span class="chip">created</span>' in review
    # The wireframe's own sentence, spelled out rather than compared with
    # the constant that renders it: a hint pinned against itself pins
    # nothing, the same reason FAMILY_ROWS is written out above.
    assert setup_web.PLAN_HINT == (
        "An outlined chip marks a file this project already has; the mount "
        "changes it only in the way its row says."
    )
    assert setup_web.PLAN_HINT in text_of(review)


def test_a_path_no_family_names_still_gets_its_own_row(tmp_path):
    """The families are a reading of the plan, not a picture over it."""
    extra = mnt.Disposition("docs/new-thing.md", mnt.CREATED, mnt.MOUNT_CREATED)
    plan = mnt.mount_plan(_host(tmp_path / "host"), scope=SCOPE)
    drawn = setup_web._families((*plan.dispositions(), extra))
    assert drawn[:len(FAMILY_ROWS)] == FAMILY_ROWS
    assert drawn[-1] == (mnt.CREATED, "docs/new-thing.md",
                         mnt.reason_text(extra))


def test_the_setup_page_carries_the_owners_naming(tmp_path, walk, fixed_day):
    """The masthead, the heading, the captions and the two presses.

    Title Case throughout, and no count in a caption the owner ruled the
    count out of: the mount table's rows are families of paths, and the
    skills table's caption says what the boxes write rather than how many
    files that is.
    """
    assert setup_web.TITLE == "RadialRig Project Setup"
    assert setup_web.REVIEW_HEAD == "Mount Plan"
    assert setup_web.MOUNT_LABEL == "Mount Project"
    assert setup_web.REFRESH_LABEL == "Refresh Plan"
    assert setup_web.PLAN_CAPTION == "What a mount would write here:"
    assert setup_web.SKILLS_PLAN_CAPTION == "What the checked skills would write:"

    run = walk(_host(tmp_path / "host"), scope=SCOPE)
    review = run.page()
    assert '<span class="name">RadialRig Project Setup</span>' in review
    assert "<title>RadialRig Project Setup</title>" in review
    assert "<h1>Mount Plan</h1>" in review
    assert ">Mount Project</button>" in review
    assert ">Refresh Plan</button>" in review

    # The caption stands alone: no count follows either of the two the
    # owner ruled it off, on this page or on the one the boxes draw.
    assert "<caption>What a mount would write here:</caption>" in review
    status, checked, _ = run.post(
        {"token": run.session.token, "action": "survey", "skill": boxes(SKILL), **CHOSEN},
    )
    assert status == 200
    assert "<caption>What the checked skills would write:</caption>" in checked
    assert "path(s)" not in checked

    # And the lede, the two field hints and the aside under the connect
    # offer are the wireframe's sentences. Spelled out here, then checked
    # against the rendered page: a sentence asserted against the constant
    # that renders it pins nothing, so each one is pinned word for word
    # the way the six naming constants above are.
    assert setup_web.REVIEW_LEDE == (
        "RadialRig adds its document folders and templates to this project "
        "and changes nothing else. It can take all of them back out later."
    )
    assert setup_web.SCOPE_HINT == (
        "The name RadialRig uses for this project; it starts as the folder's "
        "name."
    )
    assert setup_web.OWNER_HINT == (
        "The name the project's documents list as their owner."
    )
    assert setup_web.READ_SERVER_NOTE == (
        "The connection reads the records and can save a ruling. A saved ruling "
        "stops in your working copy; you approve it in one sentence, and nothing "
        "is committed until you say so."
    )

    said = text_of(review)
    assert setup_web.REVIEW_LEDE in said
    assert setup_web.SCOPE_HINT in said
    assert setup_web.OWNER_HINT in said
    assert setup_web.READ_SERVER_NOTE in said


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
    assert report.refusal_head(1) in page
    assert run.running, "a refusal is not the end of the walkthrough"


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

    assert report.refusal_head(1) in page
    assert report.REFUSAL_REMEDY in page
    assert rows(page) == [(mnt.LEFT, "docs/spec.md",
                           report.conflict_reason("docs/spec.md"))]
    for verb, path, reason in rows(page):
        assert path in printed
        assert reason in printed
        assert verb in printed
    assert tree(root) == before

    # Nothing on the page can force past the refusal: the only control is
    # the survey again.
    assert 'value="mount"' not in page
    assert 'value="survey"' in page
    assert 'role="status" aria-live="polite"' in page

    # A client that posts the mount anyway is refused the same way.
    status, again, _ = run.post(mount_form(run))
    assert status == 200
    assert report.refusal_head(1) in again
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
    got = rows(page)
    assert [r[1] for r in got] == [mnt.MCP_REL, mnt.AGENTS_REL, "docs/spec.md"]
    for _, path, reason in got:
        assert reason == report.conflict_reason(path)
    assert report.refusal_head(3) in page
    assert tree(root) == before


# --- C: already mounted ------------------------------------------------

def test_an_already_mounted_project_is_reported_and_left_alone(
    tmp_path, walk, fixed_day,
):
    root = _make_project(tmp_path / "mounted-host")
    mnt.mount(root, scope=SCOPE)
    # Re-pinned for PLAN-0006 Phase 3: this state is terminal when there
    # is nothing left for the page to do, so the fixture now carries every
    # skill this package ships. Byte for byte the state it always was.
    for name in sk.catalog_names():
        sk.add_all(root, name)
    before = tree(root)

    run = walk(root)
    page = run.page()
    manifest = mnt.read_manifest(root)
    said = text_of(page)
    for line in report.already_mounted_report(root, manifest):
        assert line in said, line
    # The path is in the heading, set in mono rather than in the display
    # face, and it is the whole path the command line prints.
    assert f"<code>{str(root)}</code></h1>" in page
    assert tree(root) == before
    assert run.wait() == report.NOOP
    assert setup_web.STOPPED in page


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
    run = walk(_make_project(tmp_path / "host"))
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
    assert setup_web.REVIEW_HEAD in run.page()


# --- PLAN-0016 phase 4: the page tells the truth about a partial press --

def test_a_skill_fault_after_the_mount_reports_the_mount(
    tmp_path, walk, fixed_day, monkeypatch,
):
    # setup-web-01: the mount has landed when the skills' write fails, so
    # the page reports the mount, names the failure, and exits partial;
    # it does not say that nothing was written.
    root = _make_project(tmp_path / "host")
    detail = "the skills directory could not be written"

    def fails(plans):
        raise sk.SkillError(detail)

    monkeypatch.setattr(sk, "write_add_plans", fails)
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post(mount_form(run, skill=boxes(SKILL)))
    assert status == 200
    assert f"Mounted onto {SCOPE}." in page
    assert setup_web.FAILED_HEAD not in page
    assert setup_web.SKILLS_WROTE_CAPTION not in page
    assert detail in text_of(page)
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
    # so the page says nothing was written under the clean-stop head and
    # exits with an error, not a partial; the 500 boundary is not reached.
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
    assert setup_web.FAILED_HEAD in page
    assert setup_web.PARTIAL_HEAD not in page
    assert "could not answer" not in page
    said = text_of(page)
    assert "Permission denied" in said and "docs/spec.md" in said
    assert "nothing was written" in said
    assert tree(root) == before
    assert run.wait() == report.ERROR


def test_a_file_error_from_the_mount_names_what_was_not_put_back(
    tmp_path, walk, monkeypatch,
):
    # The restore of one file refused too. The page says the press
    # stopped part way and names exactly the path the disk shows changed.
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
    assert setup_web.PARTIAL_HEAD in page
    assert setup_web.FAILED_HEAD not in page
    said = text_of(page)
    assert "put back except: .mcp.json" in said
    assert "nothing was written" not in said
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
    # refused, and its restore found it as it stood. The page reports
    # the mount, says nothing was written for the registration, and
    # exits with an error, not a partial: nothing is left part way.
    root = _make_project(tmp_path / "host")
    client_here.write_bytes(CONFIG_HOSTS["indent2"])
    original = client_here.read_bytes()
    refusing_client_writes(monkeypatch, after=0)
    run = walk(root, scope=SCOPE)
    assert 'name="connect"' in run.page(), "the offer is not on the review page"
    status, page, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    assert f"Mounted onto {SCOPE}." in page
    assert setup_web.CONNECT_WROTE_CAPTION not in page
    assert "could not answer" not in page
    said = text_of(page)
    assert "Permission denied" in said and "nothing was written" in said
    assert "part way" not in said
    assert mnt.is_mounted(root)
    assert not cn.is_connected(root)
    assert client_here.read_bytes() == original
    assert run.wait() == report.ERROR


def test_a_file_error_from_the_connect_names_the_copy_not_put_back(
    tmp_path, walk, fixed_day, monkeypatch,
):
    # Two copies: the first lands, the second refuses, and the restore
    # of the first refuses too. The page names that copy, and the disk
    # shows it and nothing else changed.
    root = _make_project(tmp_path / "host")
    visible, hidden = two_copies(tmp_path, monkeypatch)
    before = {visible: visible.read_bytes(), hidden: hidden.read_bytes()}
    seen = refusing_client_writes(monkeypatch, after=1)
    run = walk(root, scope=SCOPE)
    assert 'name="connect"' in run.page(), "the offer is not on the review page"
    status, page, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    assert f"Mounted onto {SCOPE}." in page
    landed = seen[0]
    said = text_of(page)
    assert f"put back except: {landed}" in said
    assert "nothing was written" not in said
    changed = {path for path in before if path.read_bytes() != before[path]}
    assert changed == {landed}
    assert mnt.is_mounted(root)
    assert not cn.is_connected(root)
    assert run.wait() == report.PARTIAL


def test_a_file_error_from_a_registration_on_its_own_is_a_partial_press(
    tmp_path, walk, fixed_day, monkeypatch,
):
    # On a mounted project the registration was the whole press. One
    # copy landed and could not be put back, which is not "did not run"
    # and not a clean error: a partial press, naming the copy.
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(root, name)
    visible, hidden = two_copies(tmp_path, monkeypatch)
    before = {visible: visible.read_bytes(), hidden: hidden.read_bytes()}
    seen = refusing_client_writes(monkeypatch, after=1)
    run = walk(root, scope=SCOPE)
    assert setup_web.CONNECT_LABEL in run.page()
    status, done, _ = run.post({"token": run.session.token, "action": "skills",
                                "connect": cn.DEFAULT_CLIENT, **CHOSEN})
    assert status == 200
    assert setup_web.CONNECT_DONE_HEAD not in done
    assert setup_web.CONNECT_FAILED_HEAD not in done
    assert setup_web.PARTIAL_HEAD in done
    landed = seen[0]
    assert f"put back except: {landed}" in text_of(done)
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
    assert setup_web.CONNECT_LABEL in run.page()
    status, done, _ = run.post({"token": run.session.token, "action": "skills",
                                "connect": cn.DEFAULT_CLIENT, **CHOSEN})
    assert status == 200
    assert setup_web.CONNECT_FAILED_HEAD in done
    assert setup_web.PARTIAL_HEAD not in done
    said = text_of(done)
    assert "Permission denied" in said and "nothing was written" in said
    assert client_here.read_bytes() == original
    assert not cn.is_connected(root)
    assert run.wait() == report.ERROR


def test_a_skill_fault_on_the_mount_press_names_the_registration_not_attempted(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    # The mount landed, the skills' write failed, and the ticked
    # registration is not attempted after a step that failed: the page
    # says so, beside the fault. Without the box ticked it says nothing
    # about a registration.
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
    assert f"Mounted onto {SCOPE}." in page
    assert setup_web.CONNECT_WROTE_CAPTION not in page
    said = text_of(page)
    assert detail in said
    assert setup_web.CONNECT_NOT_ATTEMPTED in said
    assert mnt.is_mounted(ticked)
    assert not cn.is_connected(ticked)
    assert run.wait() == report.PARTIAL

    unticked = _make_project(tmp_path / "unticked")
    other = walk(unticked, scope=SCOPE)
    other.page()
    status, page, _ = other.post(mount_form(other, skill=boxes(SKILL)))
    assert status == 200
    said = text_of(page)
    assert detail in said
    assert setup_web.CONNECT_NOT_ATTEMPTED not in said
    assert other.wait() == report.PARTIAL


def test_a_file_error_while_planning_the_registration_stops_the_page(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    # cli-connect-02, the plan half: the client's file cannot be read
    # while the offer is planned. The survey and the mount press both
    # stop with the sentence, nothing is mounted, and neither reaches
    # the 500 boundary or drops the offer without a word.
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
    status, page, _ = run.get(f"/setup?s={run.session.token}")
    assert status == 200
    assert "could not answer" not in page
    assert setup_web.FAILED_HEAD in page
    said = text_of(page)
    assert "Permission denied" in said and "nothing was written" in said

    pressed = _make_project(tmp_path / "pressed")
    other = walk(pressed, scope=SCOPE)
    status, page, _ = other.post(mount_form(other, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    assert "could not answer" not in page
    assert setup_web.FAILED_HEAD in page
    said = text_of(page)
    assert "Permission denied" in said and "nothing was written" in said
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
    assert setup_web.FAILED_HEAD in page
    assert mnt.MANIFEST_REL in page
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
    """Including the refusal, whose whole message is the head."""
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
    # The refusal head is that heading, not a paragraph under none.
    refusal = pages["conflict-host"]
    assert '<p class="head">' not in refusal
    assert f'<h1 class="head">{report.refusal_head(1)}</h1>' in refusal


def test_the_command_block_carries_its_panel_in_every_state(tmp_path, walk):
    """The already-mounted state emits its command outside the next-step
    list, so a rule scoped to that list leaves the block unstyled — the
    command text and its copy button touching, with no panel."""
    root = _make_project(tmp_path / "mounted-host")
    mnt.mount(root, scope=SCOPE)
    page = walk(root).page()
    assert '<div class="cmd"><code>radialrig upgrade</code>' in page

    css = (packaged_web() / "page.css").read_text(encoding="utf-8")
    assert ".steps .cmd" not in css, "the command block is styled only inside a list"
    assert re.search(r"^\.cmd \{", css, re.M), "no unscoped .cmd rule"


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
    assert "display: none; }" not in css.split("max-width: 34rem", 1)[-1]


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
    listing_rule = css.split(
        "table.plan.projects td.path {", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: break-word" in listing_rule


def test_the_page_is_readable_without_color(tmp_path, walk):
    run = walk(_make_project(tmp_path / "host"))
    page = run.page()
    # Verbs are text, the plan is a table, and the primary control has a
    # visible focus state (wireframe section 7).
    assert "<caption>" in page
    assert page.count('<th scope="col">') == 3
    for verb, _, _ in rows(page):
        assert verb in mnt.VERBS
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

    for harness in AGENTS:
        for name in sk.catalog_names():
            assert f'value="{setup_web.box_value(harness.name, name)}"' in review, name
    assert setup_web.SKILLS_HEAD in review
    # Unchecked to begin with: the mount is what the user came for, and a
    # box that arrives ticked installs something nobody asked for.
    assert " checked>" not in review
    # Nothing is checked, so there is one table and it is the mount's.
    assert review.count("<caption>") == 1
    assert rows(review) == FAMILY_ROWS
    assert tree(root) == before


def test_the_boxes_stand_in_one_group_per_agent_with_a_tick_all(tmp_path, walk):
    """PLAN-0015 phase 3 prescription 1. On a fresh project the skills
    box holds one group per agent, in map order, headed by the agent's
    label, with one box per packaged skill and a Tick all beside the
    heading; the page's own script wires the control to its group and
    nothing else, and no page constant names an agent."""
    root = _host(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()
    assert setup_web.TICK_ALL_LABEL == "Tick all"

    found = groups(review)
    assert [label for label, _, _ in found] == [h.label for h in AGENTS]
    assert len(found) == 2
    for harness, (_, tick, drawn) in zip(AGENTS, found):
        assert tick, harness.label
        assert [value for value, _ in drawn] == boxes(*sk.catalog_names(), agents=(harness,))
        assert all("disabled" not in attrs for _, attrs in drawn)
    # The groups stand inside the one skills box, after its lede.
    box = review.split('<fieldset class="skills">', 1)[1]
    assert box.index(setup_web.SKILLS_LEDE) < box.index('<fieldset class="skills agent">')
    # The control is a plain button, never a submit, so with the script
    # off it changes nothing and the form posts what the reader ticked.
    # Each is named for its agent, so a reader by name hears two
    # different controls, and it stands after the legend rather than in
    # it, so the group's own name is the label alone.
    assert setup_web.TICK_ALL_NAME == "Tick all for {label}"
    assert re.findall(r'<button class="tick" type="button" aria-label="([^"]*)">Tick all'
                      r'</button>', review) == [f"Tick all for {h.label}" for h in AGENTS]
    for label, _, _ in AGENT_GROUP.findall(review):
        assert "Tick all" not in label
    script = review.split("<script", 1)[1]
    assert 'querySelectorAll("button.tick")' in script
    assert 'button.closest("fieldset")' in script
    assert 'group.querySelectorAll("input[type=checkbox]:not(:disabled)")' in script
    # Nothing on the page says "harness"; the labels come from the map.
    assert "harness" not in text_of(review).lower()
    for harness in AGENTS:
        assert harness.label in text_of(review)


def test_a_checked_box_shows_a_second_table_and_writes_nothing(tmp_path, walk):
    root = _host(tmp_path / "host")
    before = tree(root)
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post(
        {"token": run.session.token, "action": "survey", "skill": boxes(SKILL), **CHOSEN},
    )
    assert status == 200
    assert page.count("<caption>") == 2
    assert setup_web.SKILLS_PLAN_CAPTION in page
    assert page.count(" checked>") == len(AGENTS), "a box did not survive the refresh"

    planned = [d for r in sk.add_all_plan(root, SKILL) for d in r.dispositions]
    assert rows(page)[-len(planned):] == expected_rows(planned)
    assert tree(root) == before


def test_two_boxes_for_one_agent_write_one_record_naming_both(tmp_path, walk, fixed_day):
    """The record threads through a press's names for one agent, as it
    does on the command line: two boxes ticked in one group leave one
    record naming both, and the plan table says the record once."""
    root = _make_project(tmp_path / "host")
    twin = _make_project(tmp_path / "twin")
    first_two = sk.catalog_names()[:2]
    run = walk(root, scope=SCOPE)
    run.page()
    status, page, _ = run.post({
        "token": run.session.token, "action": "survey",
        "skill": boxes(*first_two, agents=(SECOND,)), **CHOSEN})
    assert status == 200
    drawn = rows(page)
    record_rows = [r for r in drawn if r[1] == SECOND.record]
    assert len(record_rows) == 1, drawn
    assert len({path for _, path, _ in drawn}) == len(drawn), "a path is drawn twice"

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

    mnt.mount(twin, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    written = [d for r in sk.add_all(twin, SKILL) for d in r.dispositions]

    page_files, page_dirs = tree(page_root)
    twin_files, twin_dirs = tree(twin)
    assert set(page_files) == set(twin_files)
    for rel, data in sorted(twin_files.items()):
        assert page_files[rel] == data, f"{rel} differs from the engine's add"
    assert page_dirs == twin_dirs
    assert snapshot(page_root) == snapshot(twin)

    # Two tables, and the second says exactly what the engine wrote.
    assert setup_web.SKILLS_WROTE_CAPTION in mounted
    assert rows(mounted)[-len(written):] == expected_rows(written)
    assert "radialrig skill list" in mounted
    # And one command line per pair the press wrote for, the command
    # line's own form narrowed to the agent (prescription 2).
    assert setup_web.SKILLS_RAN_HEAD in mounted
    for harness in AGENTS:
        assert f'data-copy="radialrig skill add {SKILL} {page_root} --harness {harness.name}"' \
            in mounted


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
    assert setup_web.SKILLS_PLAN_CAPTION not in page
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

    # Disabled in the one agent's group whose copy was edited; the other
    # agent's box for the same skill is open, because that agent lacks it.
    assert f'value="{setup_web.box_value(FIRST.name, SKILL)}" disabled>' in page
    assert f'value="{setup_web.box_value(SECOND.name, SKILL)}">' in page
    assert mnt.REASONS[mnt.SKILL_EDITED] in page
    assert " checked>" not in page

    # A client that posts the disabled box anyway is not obeyed past the
    # edit: the mount runs, the skill is left, and the page says so.
    status, mounted, _ = run.post(mount_form(run, skill=boxes(SKILL)))
    assert status == 200
    assert (root / SKILL_DIR / "SKILL.md").read_bytes() == b"# ours now\n"
    assert mnt.REASONS[mnt.SKILL_EDITED] in mounted
    assert (root / SECOND.skills / SKILL / "SKILL.md").is_file()
    # The finished page's command lines are the pairs the press wrote
    # for: one for the second agent, none for the copy it left alone.
    lines = [html.unescape(text) for text in re.findall(r'data-copy="([^"]*)"', mounted)
             if "skill add" in text]
    assert lines == [setup_web.skill_add_line(SECOND.name, SKILL, root)]
    assert f"--harness {FIRST.name}" not in mounted
    assert run.wait() == report.PARTIAL


def test_a_foreign_skill_directory_refuses_with_no_force_affordance(tmp_path, walk):
    root = _make_project(tmp_path / "host")
    (root / SKILL_DIR).mkdir(parents=True)
    (root / SKILL_DIR / "ours.md").write_bytes(b"our own skill\n")
    before = tree(root)

    run = walk(root, scope=SCOPE)
    page = run.page()
    assert f'value="{setup_web.box_value(FIRST.name, SKILL)}" disabled>' in page
    assert mnt.REASONS[mnt.SKILL_DIR_FOREIGN] in page

    status, refused, _ = run.post(mount_form(run, skill=boxes(SKILL)))
    assert status == 200
    assert report.refusal_head(1) in refused
    assert report.SKILL_FOREIGN_TAIL in refused
    # The refusal carries the survey and nothing else. There is no page
    # affordance to force past it, because the command line has none.
    assert 'value="mount"' not in refused
    assert 'value="survey"' in refused
    assert tree(root) == before, "the refusal wrote something"
    assert run.stop() == report.REFUSED


def test_a_mounted_project_can_still_add_a_skill(tmp_path, walk, fixed_day):
    root = _make_project(tmp_path / "host")
    twin = _make_project(tmp_path / "twin")
    mnt.mount(root, scope=SCOPE)
    mnt.mount(twin, scope=SCOPE)

    run = walk(root, scope=SCOPE)
    page = run.page()
    # Not terminal any more: there is one thing left the page can do.
    assert setup_web.STOPPED not in page
    assert 'value="skills"' in page
    assert setup_web.SKILLS_LABEL in page
    assert run.running

    status, done, _ = run.post(
        {"token": run.session.token, "action": "skills", "skill": boxes(SKILL), **CHOSEN},
    )
    assert status == 200
    assert setup_web.SKILLS_DONE_HEAD in done
    assert run.wait() == report.OK

    written = [d for r in sk.add_all(twin, SKILL) for d in r.dispositions]
    assert rows(done) == expected_rows(written)
    assert snapshot(root) == snapshot(twin)
    assert setup_web.SKILLS_RAN_HEAD in done
    for harness in AGENTS:
        assert f'data-copy="radialrig skill add {SKILL} {root} --harness {harness.name}"' in done
    # The finished page's next step, like every other sentence on it,
    # says "agent", never "harness"; the flag keeps its name in the
    # command lines alone.
    assert report.SKILL_NEXT_STEP in text_of(done)
    assert "harness" not in text_of(
        re.sub(r'<div class="cmd">.*?</div>', "", done, flags=re.S)).lower()


def test_ticking_all_in_one_group_installs_for_that_agent_alone(
    tmp_path, walk, fixed_day, capsys,
):
    """PLAN-0015 phase 3's done-condition, on `radialrig setup`.

    A fresh project shows two groups of every packaged skill with a Tick
    all beside each heading. Every box in the second agent's group ticked
    and the one press pressed: every skill is installed for that agent
    and none for the first, the finished page prints one `--harness`
    line per pair, and `skill list` shows every skill installed under
    the second agent's directory and absent under the first's. The tree
    is byte for byte what the command line's whole-catalog add leaves.
    """
    root = _host(tmp_path / "host")
    twin = _host(tmp_path / "twin")
    names = sk.catalog_names()
    assert len(names) == 5
    run = walk(root, scope=SCOPE)
    review = run.page()
    assert [(label, tick, len(drawn)) for label, tick, drawn in groups(review)] == [
        (FIRST.label, True, 5), (SECOND.label, True, 5)]

    ticked = boxes(*names, agents=(SECOND,))
    status, mounted, _ = run.post(mount_form(run, skill=ticked))
    assert status == 200
    assert f"Mounted onto {SCOPE}." in mounted
    assert run.wait() == report.OK

    mnt.mount(twin, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    written = list(dict.fromkeys(
        d for r in sk.add_catalog(twin, targets=(SECOND.name,)) for d in r.dispositions))
    assert snapshot(root) == snapshot(twin)
    assert rows(mounted)[-len(written):] == expected_rows(written)

    # Four lines, one per pair, each the command line's own form narrowed
    # to the second agent, and none naming the first.
    lines = [html.unescape(text) for text in re.findall(r'data-copy="([^"]*)"', mounted)
             if "skill add" in text]
    assert lines == [f"radialrig skill add {name} {root} --harness {SECOND.name}"
                     for name in names]
    assert lines == [setup_web.skill_add_line(SECOND.name, name, root) for name in names]
    assert f"--harness {FIRST.name}" not in mounted
    assert "harness" not in text_of(
        re.sub(r'<div class="cmd">.*?</div>', "", mounted, flags=re.S)).lower()

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
    """Prescription 3. One agent complete: its group is one sentence with
    no boxes and no Tick all, the other's has every box. Both complete:
    the one sentence the page has always said, and no groups."""
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    sk.add_catalog(root, targets=(FIRST.name,))
    assert setup_web.SKILLS_AGENT_NONE == "{label} already has every skill this package ships."

    run = walk(root, scope=SCOPE)
    page = run.page()
    found = groups(page)
    assert [(label, tick, len(drawn)) for label, tick, drawn in found] == [
        (FIRST.label, False, 0), (SECOND.label, True, len(sk.catalog_names()))]
    said = text_of(page)
    assert setup_web.SKILLS_AGENT_NONE.format(label=FIRST.label) in said
    assert setup_web.SKILLS_AGENT_NONE.format(label=SECOND.label) not in said
    assert setup_web.SKILLS_NONE not in said
    assert setup_web.SKILLS_LABEL in page
    assert run.stop() == report.NOOP

    sk.add_catalog(root, targets=(SECOND.name,))
    # Still not terminal: the client on this machine is unregistered, so
    # the skills box is drawn, and it says the one sentence.
    again = walk(root, scope=SCOPE)
    page = again.page()
    assert groups(page) == []
    assert page.count(setup_web.SKILLS_NONE) == 1
    assert '<button class="tick"' not in page
    assert setup_web.CONNECT_LABEL in page
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
    # The legend and hint are the wireframe's words, with the client's
    # name arriving from the client table rather than a page constant.
    assert setup_web.connect_head(client) == "Connect Claude Desktop"
    assert setup_web.connect_lede(client) == (
        "RadialRig adds one entry to Claude Desktop's own settings and "
        "changes nothing else there."
    )
    assert setup_web.connect_head(client) in review
    assert setup_web.connect_lede(client) in review
    assert f'name="connect" value="{client.name}" checked>' in review
    # The caption carries no count, like the mount and skills captions.
    assert f"<caption>{setup_web.CONNECT_PLAN_CAPTION}</caption>" in review
    assert review.count("<caption>") == 2, "the connect plan has no table"
    assert tree(root) == before, "the review state wrote something"

    status, mounted, _ = run.post(mount_form(run, connect=client.name))
    assert status == 200
    assert run.wait() == report.OK

    assert setup_web.CONNECT_WROTE_CAPTION in mounted
    # The restart sentence is the page's last line, and it comes out of
    # the client table rather than out of a page constant.
    assert text_of(mounted).endswith(client.restart)
    assert cn.is_connected(root)
    entry = json.loads(client_here.read_text(encoding="utf-8"))["mcpServers"][key]
    assert entry["env"][cn.ROOT_ENV] == str(root)
    assert entry["args"] == []


def test_the_page_offers_no_connect_when_no_client_is_detected(
    tmp_path, walk, fixed_day,
):
    """No client on this machine is not a page failure: the mount still
    runs, and the offer simply is not made."""
    root = _host(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()
    client = cn.client_for(cn.DEFAULT_CLIENT)

    assert setup_web.connect_head(client) not in review
    assert 'name="connect"' not in review
    assert client.label not in review
    assert review.count("<caption>") == 1

    status, mounted, _ = run.post(mount_form(run))
    assert status == 200
    assert f"Mounted onto {SCOPE}." in mounted
    assert client.restart not in mounted
    assert run.wait() == report.OK
    assert not cn.is_connected(root)


def test_an_unreadable_client_profile_reads_as_no_client(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    """`is_dir` raises on a profile directory that denies traversal, and
    the offer asks `detect` before the review and again before the press.
    The walkthrough reads as it does on a machine with no client: no
    offer, and the mount still lands."""
    client = cn.client_for(cn.DEFAULT_CLIENT)
    assert cn.detect(client), "readable, the client is found"

    def denied(client, *, env=None):
        raise PermissionError("the profile directory denies traversal")

    monkeypatch.setattr(cn, "detect", denied)
    root = _host(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()

    assert setup_web.MOUNT_LABEL in review
    assert setup_web.connect_head(client) not in review
    assert 'name="connect"' not in review

    status, mounted, _ = run.post(mount_form(run, connect=client.name))
    assert status == 200
    assert f"Mounted onto {SCOPE}." in mounted
    assert client.restart not in mounted
    assert run.wait() == report.OK
    assert not cn.is_connected(root)


def test_the_page_and_the_command_line_report_the_same_connect_rows(
    tmp_path, walk, fixed_day, client_here,
):
    root = _make_project(tmp_path / "host")
    twin = _make_project(tmp_path / "twin")
    run = walk(root, scope=SCOPE)
    review = run.page()

    plan = cn.connect_plan(root, scope=SCOPE)
    assert rows(review)[-len(plan.dispositions):] == expected_rows(plan.dispositions)

    status, mounted, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    assert status == 200
    run.wait()

    # The same engine call on a twin with its own client directory, so
    # the two runs do not compete for one entry key.
    other = tmp_path / "other" / "Roaming"
    (other / "Claude").mkdir(parents=True)
    mnt.mount(twin, scope=SCOPE, owner=mnt.DEFAULT_OWNER)
    theirs = cn.connect(twin, scope=SCOPE, env={"APPDATA": str(other)})
    assert [(r[0], r[2]) for r in rows(mounted)[-len(theirs.dispositions):]] == [
        (d.verb,
         mnt.reason_text(d) or setup_web.WROTE_REASONS.get(d.code, ""))
        for d in theirs.dispositions
    ]


def test_a_connect_refusal_withdraws_the_offer_and_keeps_the_mount(
    tmp_path, walk, fixed_day, client_here,
):
    """A taken entry key is not a reason to withhold the mount button.

    The connect is an offer, never a precondition: the offer is withdrawn,
    the page says why in the engine's own words, and the press the reader
    came for is still there. A press therefore carries no refused connect.
    """
    root = _make_project(tmp_path / "host")
    client = cn.client_for(cn.DEFAULT_CLIENT)
    key = cn.entry_key(client, SCOPE)
    conflicts = (mnt.Disposition(
        client_here.as_posix(), mnt.LEFT, mnt.CONNECT_KEY_HOST_OWNED),)
    client_here.write_bytes(
        (json.dumps({"mcpServers": {key: {"command": "theirs"}}}, indent=2)
         + "\n").encode("utf-8")
    )
    taken = client_here.read_bytes()

    run = walk(root, scope=SCOPE)
    page = run.page()
    assert 'value="mount"' in page, "a refused registration took the mount with it"
    assert 'name="connect"' not in page, "the refused offer is still on the table"
    assert setup_web.connect_head(client) not in page
    assert report.connect_withheld_notice(client.name, root, conflicts) in text_of(page)

    status, mounted, _ = run.post(mount_form(run))
    assert status == 200
    assert f"Mounted onto {SCOPE}." in mounted
    assert run.wait() == report.OK
    assert client_here.read_bytes() == taken, "the mount touched the client's file"
    assert not cn.is_connected(root)
    assert setup_web.CONNECT_WROTE_CAPTION not in mounted


def test_a_press_that_still_carries_a_refused_connect_writes_nothing(
    tmp_path, walk, fixed_day, client_here,
):
    """The offer is withdrawn on the page, so only a press from a page
    rendered before the key was taken can carry it. That press is refused
    whole: a mount plus a refused registration would be half a press."""
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
    assert report.connect_refusal_head(1) in again
    assert 'value="mount"' not in again
    assert tree(root) == before, "the refusal mounted something"
    assert client_here.read_bytes() == taken
    assert run.stop() == report.REFUSED


def test_a_mounted_project_can_still_register_on_its_own(
    tmp_path, walk, fixed_day, client_here,
):
    """The already-mounted page is not terminal while a client on this
    machine is unregistered, and the one press does only the connect."""
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(root, name)

    run = walk(root, scope=SCOPE)
    page = run.page()
    assert setup_web.STOPPED not in page
    assert setup_web.CONNECT_LABEL in page
    assert run.running

    status, done, _ = run.post({"token": run.session.token, "action": "skills",
                                "connect": cn.DEFAULT_CLIENT, **CHOSEN})
    assert status == 200
    assert setup_web.CONNECT_DONE_HEAD in done
    assert run.wait() == report.OK
    assert cn.is_connected(root)

    # And once it is registered the offer is gone: the page says nothing
    # about a step there is no longer anything to do.
    second = walk(root, scope=SCOPE)
    after = second.page()
    assert setup_web.connect_head(cn.client_for(cn.DEFAULT_CLIENT)) not in after
    assert second.wait() == report.NOOP


def test_a_failed_registration_never_claims_the_project_is_registered(
    tmp_path, walk, fixed_day, client_here, monkeypatch,
):
    """The registration was the whole press, and it failed after the
    offer. The page says that, in the engine's own sentence, and claims
    nothing it cannot show."""
    root = _make_project(tmp_path / "host")
    mnt.mount(root, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(root, name)
    detail = "the settings file could not be opened"

    run = walk(root, scope=SCOPE)
    assert setup_web.CONNECT_LABEL in run.page()

    def fails(*args, **kwargs):
        raise cn.ConnectError(detail)

    monkeypatch.setattr(cn, "connect", fails)
    status, done, _ = run.post({"token": run.session.token, "action": "skills",
                                "connect": cn.DEFAULT_CLIENT, **CHOSEN})
    assert status == 200
    assert setup_web.CONNECT_DONE_HEAD not in done
    assert setup_web.CONNECT_FAILED_HEAD in done
    assert detail in text_of(done)
    assert cn.client_for(cn.DEFAULT_CLIENT).restart not in done
    assert run.wait() == report.ERROR
    assert not cn.is_connected(root)
    assert not client_here.exists()


def test_an_unticked_connect_box_writes_no_entry(tmp_path, walk, fixed_day, client_here):
    root = _make_project(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    run.page()
    # The box is ticked by default; a post without it is a reader who
    # cleared it, and nothing registers.
    status, mounted, _ = run.post(mount_form(run))
    assert status == 200
    assert run.wait() == report.OK
    assert not cn.is_connected(root)
    assert not client_here.exists()
    assert setup_web.CONNECT_WROTE_CAPTION not in mounted


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

    The refusal's form carries no box and no marker, so the survey it
    posts says nothing about the boxes, and what the reader ticked before
    the mount was refused is what the review shows after Check again.
    """
    root = _make_project(tmp_path / "host")
    run = walk(root, scope=SCOPE)
    review = run.page()
    assert 'name="connect"' in review, "no offer to keep"
    ticked = boxes(SKILL, agents=(SECOND,))
    # The rulebook appears after the review was drawn, so the press is
    # what meets it.
    (root / "docs").mkdir()
    (root / "docs" / "spec.md").write_bytes(b"this project's own rulebook\n")

    # The press carries what the review form carries, plus the ticks.
    status, refused, _ = run.post({
        **hidden_fields(review), "action": "mount", "scope": SCOPE,
        "owner": mnt.DEFAULT_OWNER, "skill": ticked, "connect": cn.DEFAULT_CLIENT,
    })
    assert status == 200
    assert report.refusal_head(1) in refused
    assert run.session.skills == ((SECOND.name, SKILL),)
    assert run.session.connect is True
    # The refusal's form carries no box: nothing on it can untick one.
    assert 'name="skill"' not in refused
    assert 'name="connect"' not in refused

    (root / "docs" / "spec.md").unlink()
    status, again, _ = run.post({**hidden_fields(refused), "action": "survey"})
    assert status == 200
    assert setup_web.REVIEW_HEAD in again
    assert run.session.skills == ((SECOND.name, SKILL),)
    assert run.session.connect is True
    checked = [value for value, attrs in BOX.findall(again) if "checked" in attrs]
    assert checked == ticked
    assert re.search(r'name="connect"[^>]*checked', again), "the connect box was cleared"
    assert run.running


def test_a_slot_no_quoting_rule_makes_safe_offers_no_copy_line(tmp_path, walk):
    """setup-web-04: a path or key carrying an unsafe character names no line.

    The finished page still says what it did; it offers no line a reader
    could paste that would run something they did not type.
    """
    state = setup_web.PageState(
        kind=setup_web.MOUNTED, outcome=report.OK, terminal=True,
        added=((SECOND.name, SKILL),))
    for ch in UNSAFE:
        for slot in (rf"C:\dev\proj{ch}x", f"radialrig-foo{ch}calc"):
            session = setup_web.Session(Path(slot), SCOPE, mnt.DEFAULT_OWNER)
            assert setup_web._skill_lines(session, state) == "", repr(slot)
            with pytest.raises(setup_web.Unquotable):
                setup_web.quoted(slot)

    # Over HTTP, on a folder a reader can have: `&` is legal on both
    # systems and runs a second program in cmd.exe.
    root = _make_project(tmp_path / "proj&calc")
    run = walk(root, scope=SCOPE)
    review = run.page()
    status, mounted, _ = run.post({
        **hidden_fields(review), "action": "mount", "scope": SCOPE,
        "owner": mnt.DEFAULT_OWNER, "skill": boxes(SKILL, agents=(SECOND,)),
    })
    assert status == 200
    assert f"Mounted onto {SCOPE}." in mounted
    assert setup_web.SKILLS_WROTE_CAPTION in mounted, "the page stopped saying what it did"
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
    """setup-web-05: F5 on the review page is not the page closing.

    The browser serves the reload's GET first and fires the old
    document's close after it, so the close carries the id of a document
    this session has already replaced, and the server drops it. The
    reader then presses Mount on the reloaded page and it lands.
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
    assert f"Mounted onto {SCOPE}." in mounted
    assert run.wait() == report.OK
    assert mnt.is_mounted(root)


# --- PLAN-0017 phase 5: a skills record the kit cannot read ------------

def test_a_utf16_skills_record_stops_the_page_in_words(tmp_path, walk):
    # skills-roster-04, the page half: the record in another encoding is
    # the same sentence `skill list` prints, under the clean-stop head,
    # on the survey and on the mount press alike; the 500 boundary is
    # not reached and nothing is written.
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
    assert setup_web.FAILED_HEAD in page
    assert f"{record_rel} does not parse as JSON" in text_of(page)
    assert tree(surveyed) == before

    pressed = host("pressed")
    before = tree(pressed)
    other = walk(pressed, scope=SCOPE)
    status, page, _ = other.post(mount_form(other, skill=boxes(SKILL)))
    assert status == 200
    assert "could not answer" not in page
    assert setup_web.FAILED_HEAD in page
    assert f"{record_rel} does not parse as JSON" in text_of(page)
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
    press time. The mount press and the skills press both land what they
    can and both carry the engine's sentence, so the two surfaces agree
    and the registration the page offered does not vanish unsaid."""
    detail = "the settings file could not be opened"
    armed = [False]
    real = cn.connect_plan

    def refuse(*args, **kwargs):
        if armed[0]:
            raise cn.ConnectError(detail)
        return real(*args, **kwargs)

    monkeypatch.setattr(cn, "connect_plan", refuse)

    # The mount press: the mount lands, the registration does not run,
    # and the page says why in the engine's own words.
    pressed = _make_project(tmp_path / "pressed")
    run = walk(pressed, scope=SCOPE)
    assert 'name="connect"' in run.page(), "the offer is not on the review page"
    armed[0] = True
    status, page, _ = run.post(mount_form(run, connect=cn.DEFAULT_CLIENT))
    armed[0] = False
    assert status == 200
    assert f"Mounted onto {SCOPE}." in page
    assert mnt.is_mounted(pressed)
    assert not cn.is_connected(pressed)
    assert setup_web.CONNECT_WROTE_CAPTION not in page
    assert detail in text_of(page)
    assert run.wait() == report.OK

    # The skills press on a project already mounted: the same sentence,
    # under the head that says the registration did not run.
    mounted = _make_project(tmp_path / "mounted")
    mnt.mount(mounted, scope=SCOPE)
    for name in sk.catalog_names():
        sk.add_all(mounted, name)
    other = walk(mounted, scope=SCOPE)
    assert setup_web.CONNECT_LABEL in other.page()
    armed[0] = True
    status, done, _ = other.post({"token": other.session.token, "action": "skills",
                                  "connect": cn.DEFAULT_CLIENT, **CHOSEN})
    armed[0] = False
    assert status == 200
    assert setup_web.CONNECT_DONE_HEAD not in done
    assert setup_web.CONNECT_FAILED_HEAD in done
    assert detail in text_of(done)
    assert not cn.is_connected(mounted)
    assert other.wait() == report.ERROR
