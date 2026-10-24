"""`radialrig setup`: the guided installer, as a local web page.

PLAN-0005 Phase 4. The page is the second install surface D-34 asks for,
and it drives the same engine the command line drives: `is_mounted`,
`read_manifest`, `mount_plan` and `mount` from `radialrig.mount`, with
every verb, path and reason string rendered from `radialrig.report`. Neither surface holds
behavior the other cannot reach — there is no page-only affordance to
force past a refusal, because the command line has none either.

What the server is, exactly:

- one `http.server` bound to 127.0.0.1 on a port the operating system
  picks, reachable from this machine only;
- one page at `/setup`, one form target, no directory listing, no other
  route;
- a single-use launch token in the opened URL, redeemed once for a
  session token; requests without a valid token are refused;
- every byte the page needs — styles, marks, favicons, the brand face —
  inlined from package data, so the page makes no request of its own and
  the server can stop the moment the walkthrough ends.

PLAN-0006 Phase 3 adds the second kit to the same page and to the same
one press: a checkbox per packaged skill, unchecked to begin with, and a
second dispositions table saying what those boxes would write. Since
PLAN-0015 phase 3 the boxes stand in one group per agent, headed by the
agent's label, so a person installs a skill for one agent, the other, or
both. The page gains no behavior the command line lacks — it sequences
`mnt.mount` and then the skill kit's add per ticked pair, which is
`radialrig mount` followed by `radialrig skill add <name> --harness
<agent>`, and the finished page prints one such line per pair.

The process exit code is the outcome the page reached, over both kits:
0 done, 3 a refusal that wrote nothing, 4 nothing to do, 5 something was
left, 1 environment error. The same numbers the command line returns.
"""

from __future__ import annotations

import base64
import html
import secrets
import sys
import threading
import unicodedata
import webbrowser
from dataclasses import dataclass
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from radialrig import activity_log as al
from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import report
from radialrig import roster as ro
from radialrig import skills as sk
from radialrig.paths import packaged_web

HOST = "127.0.0.1"
PAGE_PATH = "/setup"
TOKEN_BYTES = 32
MAX_BODY = 64 * 1024

# The marker the review and already-mounted forms carry (PLAN-0017 phase
# 4, setup-web-03). A post that carries it came from a form that drew the
# boxes, so what it says about them — ticked or not — is what the reader
# left. The refusal's Check again draws no box and carries no marker, and
# says nothing about them.
CHOICES_FIELD = "choices"

# --- the render id (D8) -----------------------------------------------
# A reload is not the page closing, but a browser cannot say so: the new
# document's GET is served before the old document's `pagehide` fires its
# close beacon, so a close always arrives last and a timer armed by it
# would have nothing left to cancel.
#
# What tells the two apart is staleness rather than timing. Every
# document this server renders carries the id its session issued for it,
# `page.js` posts that id back with the close, and a close naming a
# document the session has already replaced is dropped. A reload's close
# is always stale, because its own replacement was delivered first; a
# genuine close is always current.
#
# Replaced means delivered, not merely composed. A response that never
# made it out the socket replaced nothing a reader can see, so its id
# becomes the latest only once its bytes were written without raising —
# otherwise a served-but-undelivered document would stale the one still
# on screen and drop the only close this server will ever be sent.
#
# A close carrying no id at all is what a close has always been here, and
# is honored unchanged: the desktop shell's own quiesce close posts none,
# and neither does a probe typed at a terminal. The manage page carried
# this apparatus first (PLAN-0008 phase 5); the setup page stands too,
# between the review and the press, so it carries the same one
# (PLAN-0017 phase 4, setup-web-05).
RENDER_ATTR = "data-render"
RENDER_FIELD = "render"

# The referrer policy the page is served under. It is not only a privacy
# setting: a browser computes the `Origin` header of a form submission
# under the document's referrer policy, and `no-referrer` makes that
# header the literal string "null", which the origin guard below then
# refuses. `strict-origin` keeps the page's own origin on the POST and
# still sends no path and no query anywhere, so the session token never
# rides in a `Referer`.
REFERRER_POLICY = "strict-origin"

# Launch-token outcomes. A token that was never issued and a token that
# was already redeemed are different facts, and the page says so.
LAUNCH_OK = "ok"
LAUNCH_SPENT = "spent"
LAUNCH_INVALID = "invalid"

# --- page copy --------------------------------------------------------
# These sentences are the accepted screen text; a change to them is a
# change to the page's wording. They are here, once, rather than inside
# the builders below. Where a screen was drawn and accepted, its words
# are carried across as they stand; a state that was not drawn keeps the
# sentences it had and takes only what the shared elements changed.

TITLE = "RadialRig Project Setup"
ENDORSEMENT = "A Radial 47 product"

REVIEW_HEAD = "Mount Plan"
REVIEW_LEDE = (
    "RadialRig adds its document folders and templates to this project and "
    "changes nothing else. It can take all of them back out later."
)
# The one aside under the connect offer. The wireframe puts it where the
# read server's own note used to stand, and says the same thing about the
# same thing in fewer words: what a registration lets a client do. Both
# servers are registered (PLAN-0012); the note says where a write stops.
READ_SERVER_NOTE = (
    "The connection reads the records and can save a ruling. A saved ruling "
    "stops in your working copy; you approve it in one sentence, and nothing "
    "is committed until you say so."
)
# The mount table's caption carries no count: the rows are families of
# paths rather than paths, so a number beside them would count the wrong
# thing. The line under the table says what the chips mean instead.
PLAN_CAPTION = "What a mount would write here:"
PLAN_HINT = (
    "An outlined chip marks a file this project already has; the mount "
    "changes it only in the way its row says."
)
WROTE_CAPTION = "What the mount wrote"
CONFLICT_CAPTION = "The paths that stopped the mount"
MOUNT_LABEL = "Mount Project"
REFRESH_LABEL = "Refresh Plan"
CHECK_AGAIN_LABEL = "Check again"
SCOPE_HINT = (
    "The name RadialRig uses for this project; it starts as the folder's name."
)
OWNER_HINT = "The name the project's documents list as their owner."
REVERSIBLE = (
    "RadialRig can remove all of this precisely: <b>radialrig unmount</b> "
    "reverses the mount and leaves anything you have edited."
)
STOPPED = (
    "Setup is finished and the local server has stopped. You can close this page."
)
FAILED_HEAD = "RadialRig stopped before writing anything."
# A press that stopped after something landed says so. The writers put
# back what they can on a file error and raise their kit's own error,
# whose sentence names what could not be put back; the page's outcome
# follows that sentence (PLAN-0016 phase 4, made exact by PLAN-0017
# phase 2).
PARTIAL_HEAD = "RadialRig stopped part way, and some of what it wrote may be left."
# After a skill fault on the mount press the ticked registration is not
# attempted, and the page says so beside the fault.
CONNECT_NOT_ATTEMPTED = "The registration was not attempted after that step."

# The skills kit's section labels (D-36, PLAN-0006 Phase 3). Labels only:
# every outcome sentence still comes out of `report.py`, and not one of
# these names a skill or an agent — the names come out of the catalog,
# the agents' labels out of the harness map, and the one sentence with
# an agent in it is a template with a slot (PLAN-0015 constraint 3).
SKILLS_HEAD = "Add agent skills"
SKILLS_LEDE = (
    "A skill is an instruction document an agent reads. Each one lands in "
    "its own directory and comes out the same way."
)
SKILLS_DONE_HEAD = "The skills are installed."
SKILLS_LABEL = "Add these skills"
# No count, by the owner's ruling of 2026-08-21: the caption says what
# the checked boxes write, and the table under it is the count.
SKILLS_PLAN_CAPTION = "What the checked skills would write:"
SKILLS_WROTE_CAPTION = "What the skills kit wrote"
SKILLS_NONE = "This project already has every skill this package ships."
# One group of boxes per agent (PLAN-0015 phase 3). The control beside a
# group's heading ticks that group's boxes and nothing in another; a
# group whose agent already has every skill says so in one sentence.
# The control's spoken name carries the agent, since two controls that
# both read "Tick all" are two the same to a reader by name; the label
# is the map's, filled at render time.
TICK_ALL_LABEL = "Tick all"
TICK_ALL_NAME = "Tick all for {label}"
SKILLS_AGENT_NONE = "{label} already has every skill this package ships."
SKILLS_REVERSIBLE = (
    "Each skill comes out on its own: <b>radialrig skill remove &lt;name&gt;</b> "
    "leaves anything you have edited."
)
# The finished page's account of the skill press: one command line per
# skill and agent it wrote for, so the run is reproducible from the
# terminal (PLAN-0015 phase 3 prescription 2). The line is the command
# line's own form, the one the manage page's command table spells.
SKILLS_RAN_HEAD = "The same skills, from the command line"
SKILLS_RAN_LEDE = (
    "Running these lines yourself does the same thing the press just did."
)
SKILL_ADD_LINE = "radialrig skill add {name} {root} --harness {harness}"

# A checkbox's posted value: the agent's map key and the skill's catalog
# name, joined once. The page looks both halves up before anything
# downstream reads them (`_skill_pairs`), so the join is a spelling for
# the form and never a path: a value the map or the catalog does not
# know is dropped whole.
BOX_SEP = "/"

# The connect kit's section labels (PLAN-0007 Phase 2). Labels only, and
# not one of them names a client or a client's settings file: the name,
# the paths and the restart sentence arrive from the client table, the
# same rule that keeps the SKILLS_* labels from naming a skill.
CONNECT_HEAD = "Connect {client}"
CONNECT_LEDE = (
    "RadialRig adds one entry to {client}'s own settings and changes "
    "nothing else there."
)
CONNECT_DONE_HEAD = "The project is registered."
CONNECT_FAILED_HEAD = "The registration did not run."
CONNECT_LABEL = "Register this project"
CONNECT_PLAN_CAPTION = "What the registration would write:"
CONNECT_WROTE_CAPTION = "What the registration wrote"
CONNECT_REVERSIBLE = (
    "The entry comes out precisely: <b>radialrig unconnect</b> removes it and "
    "lists anything it leaves."
)

# --- refusals ---------------------------------------------------------
# Every refusal below is served by one code path, and a second surface
# inherits that path rather than copying it. Only two words differ between
# the surfaces, so those two are slots rather than sentences: `surface` is
# the command that opens the page, and `page` is what the page is called.
# A refusal on another page that named this one would send a reader to run
# the wrong command — and the spent-link remedy would be plainly wrong,
# since running this command opens a different page.

SURFACE = "radialrig setup"
PAGE_NAME = "setup"

REFUSE_TOKEN = (
    "Refused: this request carried no valid {page} token. The {page} page is "
    "reachable only from the URL {surface} printed."
)
REFUSE_SPENT = (
    "Refused: that launch link has already been used. Run {surface} "
    "again for a new one."
)
REFUSE_ORIGIN = (
    "Refused: this request came from another origin. The {page} page answers "
    "only its own page on this machine."
)
REFUSE_NOT_FOUND = (
    "Not found. {surface} serves one page and lists no directories."
)
# The generic boundary cannot know what the request wrote before it
# failed, so it claims nothing about that (PLAN-0016 phase 4). Its last
# slot names the log (PLAN-0018 phase 2), and is empty where this
# platform keeps none, so the sentence reads as it always did there.
INTERNAL = (
    "{surface} could not answer that request. The terminal that started "
    "{page} carries the detail.{log}"
)

# Where else to look (PLAN-0018 phase 2, BUG-0014). The refusal above
# says it through its slot, after the terminal; the stop bodies below say
# it as a line of their own, since a page carries no terminal to name.
LOG_NOTE = "The log at {path} carries it too."
LOG_LINE = "The log at {path} carries the detail."


def log_note() -> str:
    """The refusal's log sentence with its leading space, or nothing
    where this platform keeps no log."""
    path = al.log_path()
    return "" if path is None else " " + LOG_NOTE.format(path=path)


def log_line() -> str:
    """The stop bodies' log sentence, or nothing."""
    path = al.log_path()
    return "" if path is None else LOG_LINE.format(path=path)


def worded(message: str, *, surface: str = SURFACE, page: str = PAGE_NAME,
           log: str | None = None) -> str:
    """One refusal, in the words of the page that is serving it.

    A message carrying no slot comes back as it went in, which is what
    lets every refusal go through here rather than only the five above.
    `log` defaults to the sentence naming the log's path, read at each
    call, so a test that moves the profile moves the sentence with it.
    """
    if log is None:
        log = log_note()
    return message.format(surface=surface, page=page, log=log)

# --- state kinds ------------------------------------------------------

REVIEW = "review"
MOUNTED = "mounted"
CONFLICT = "conflict"
ALREADY = "already"
FAILED = "failed"
SKILLED = "skilled"
CONNECTED = "connected"
CONNECT_FAILED = "connect-failed"


@dataclass(frozen=True)
class PageState:
    """One rendering of the walkthrough, and the exit code it carries."""

    kind: str
    outcome: int
    terminal: bool
    dispositions: tuple = ()
    lines: tuple = ()          # the command line's own lines for this state
    result: object | None = None
    message: str = ""
    # The skills kit's two halves of the same page: what the checked
    # boxes would write or wrote, and every catalog skill for every
    # agent as this project stands — agent by agent in map order, skill
    # by skill in catalog order — which is what the groups of boxes are
    # drawn from.
    skills: tuple = ()
    catalog: tuple = ()
    # The (agent, skill) pairs the press wrote for, which the finished
    # page's command lines are drawn from: a pair the press left alone
    # earns no line.
    added: tuple = ()
    # The connect kit's half: what the offer would write or wrote, and
    # the detected client it would write to. `client` is None whenever
    # there is nothing to offer, which is what keeps the client's name
    # off a page that has no client behind it.
    connect: tuple = ()
    client: object | None = None


class Session:
    """One `radialrig setup` run: its project, its tokens, its outcome."""

    # The one route this session's page lives at. It is a class attribute
    # rather than the module constant read inline, so a subclass can serve
    # a different route through the same server: the form target, the
    # launch URL, the redirect and the document's `data-endpoint` all read
    # it, and `page.js` posts the close beacon to that endpoint — a page
    # served elsewhere with `/setup` baked in would leave its server
    # listening after the tab closed.
    page_path: str = PAGE_PATH

    def __init__(self, root: Path, scope: str, owner: str) -> None:
        self.root = root
        self.scope = scope
        self.owner = owner
        # The boxes checked on the last post, as (agent, skill) pairs of
        # the map's key and the catalog's name, already filtered against
        # both. Nothing else reaches the engine, so a posted value can
        # never become a path fragment.
        self.skills: tuple = ()
        # The connect box, already filtered against the client table.
        # Ticked to begin with, unlike the skills boxes: the Objective
        # promises one press, and a mount that leaves this client with no
        # connection is the gap PLAN-0007 exists to close. The offer is
        # only ever shown when a client is detected and this project is
        # not registered yet, so a ticked box writes nothing surprising.
        self.connect: bool = True
        self.launch_token = secrets.token_urlsafe(TOKEN_BYTES)
        self.token = secrets.token_urlsafe(TOKEN_BYTES)
        self.port = 0
        self.outcome = report.NOOP
        self.done = threading.Event()
        self._lock = threading.Lock()
        self._launch_spent = False
        self._consequential = False
        # How many documents this session has composed, and which of them
        # actually reached a browser. The counter is the id, so ids are
        # unique and monotonic by construction; the second number is the
        # one staleness is measured against, and it moves only when a
        # response left this process whole.
        #
        # The two are separate because composing is not delivering. A GET
        # whose response write fails — the peer sent an RST mid-write, the
        # reader pressed stop, a reload was abandoned — produced a
        # document nobody is holding, and a session that had already moved
        # to its id would call the document still on screen stale and drop
        # its genuine close. The server would then stand with nothing left
        # to close it.
        #
        # Its own lock rather than the one above: these are read and
        # written on the request path, and that lock guards the outcome.
        self._renders = 0
        self._latest = 0
        self._render_lock = threading.Lock()

    # The launch token is redeemed exactly once. A second request holding
    # it is refused rather than served, so a link that leaked out of the
    # terminal after the fact opens nothing. The candidate is compared
    # before the spent flag is read, so a token this run never issued is
    # reported as invalid rather than as already used.
    def spend_launch(self, candidate: str) -> str:
        with self._lock:
            if not secrets.compare_digest(candidate, self.launch_token):
                return LAUNCH_INVALID
            if self._launch_spent:
                return LAUNCH_SPENT
            self._launch_spent = True
            return LAUNCH_OK

    @property
    def launch_spent(self) -> bool:
        with self._lock:
            return self._launch_spent

    def valid(self, candidate: str) -> bool:
        return secrets.compare_digest(candidate, self.token)

    # --- the render id ---------------------------------------------------

    def issue_render(self) -> str:
        """The id for the document about to be composed.

        Issuing is not committing. This hands out the next number and
        moves nothing: until the bytes carrying it are written, the
        document standing on screen is still the latest one.
        """
        with self._render_lock:
            self._renders += 1
            return str(self._renders)

    def commit_render(self, given: str) -> None:
        """Make an issued id the latest, now that its response is out.

        Called from the send path once the write raised nothing, which is
        the only moment a browser can be holding this document. The move
        is `max` rather than an assignment: two renders can be in flight
        on a threading server and their writes can finish in either
        order, and a later document already delivered must not be walked
        back by an earlier one landing second.
        """
        with self._render_lock:
            number = int(given)
            if number > self._latest:
                self._latest = number

    def stale_render(self, given: str) -> bool:
        """Whether a posted id names a document this session has replaced.

        Measured against the latest committed id, never the latest issued
        one: a document whose response never made it out replaced nothing
        a reader can see. An id that was never issued is stale for the
        same reason one that has been passed is — neither is the document
        standing now. An empty id is never stale: a close carrying none is
        a close in the sense this page has always meant, and honoring it
        is what the desktop shell's quiesce and a terminal probe both rely
        on.
        """
        if not given:
            return False
        with self._render_lock:
            return given != str(self._latest)

    @property
    def origin(self) -> str:
        return f"http://{HOST}:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.origin}{self.page_path}?t={self.launch_token}"

    def record(self, outcome: int, *, consequential: bool = False) -> None:
        """The outcome this run will exit with, so far.

        Monotonic in one direction only: once something has been written,
        a later recompute may not lower the answer back to NOOP. Without
        that rule a page that installed a skill and left one edited file
        behind would report 5, and the very next survey — which sees a
        mounted project with nothing left to do — would erase it with a
        4. The number that stands is the one a write earned.
        """
        with self._lock:
            if consequential:
                self._consequential = True
            if self._consequential and outcome == report.NOOP:
                return
            self.outcome = outcome

    def finish(self, outcome: int) -> None:
        self.record(outcome)
        self.done.set()


# --- the engine, once per state --------------------------------------

def _clean(value: str, fallback: str) -> str:
    """A form field as the engine will see it. Whitespace collapses; an
    empty field falls back to the default the command line uses."""
    tidy = " ".join(value.split())
    return tidy or fallback


def box_value(harness: str, name: str) -> str:
    """The value one checkbox posts: which agent, which skill."""
    return f"{harness}{BOX_SEP}{name}"


def _skill_pairs(values) -> tuple[tuple[str, str], ...]:
    """The posted checkboxes, as the engine will see them.

    Filtered against the map and the catalog before anything downstream
    reads them, and returned in map order then catalog order: a value
    naming an agent the map has no row for, or a skill this package does
    not ship, is dropped rather than carried, so a posted string can
    never become a path fragment. `_clean` does this for scope and owner;
    this is the same guard for the boxes.
    """
    posted = set(values)
    return tuple(
        (harness, name)
        for harness in sk.HARNESSES
        for name in sk.catalog_names()
        if box_value(harness, name) in posted
    )


def _skill_states(session: Session) -> tuple:
    """Every skill this package ships, for every agent, as this project
    stands.

    The groups of boxes are drawn from this, one group per agent in map
    order with the catalog's skills in catalog order, so what the page
    offers for an agent is what the engine would do for that agent. A
    skill a record names that this package no longer ships is left out:
    there is no box to offer for it.
    """
    return tuple(s for s in sk.states_all(session.root) if s.packaged is not None)


def _skill_plan(session: Session) -> tuple:
    """What the checked boxes would write. Writes nothing.

    The same composer the command line's --dry-run calls, and it raises
    the same refusals — which is why it runs before the mount does, so a
    blocked skill stops the whole press rather than half of it. One row
    per path (PLAN-0015 constraint 4): an agent's record is one file
    however many skills the press names for that agent, and the engine
    reports it once per agent.
    """
    return tuple(d for result in _skill_plans(session) for d in result.dispositions)


def _skill_plans(session: Session) -> tuple:
    """Every checked pair's plan, one result per pair, agent by agent in
    map order and skill by skill in catalog order. Writes nothing.

    One composition per agent over that agent's ticked names, through
    the composer the command line's two add verbs share, so the record
    the press writes for an agent names every skill of the press.
    """
    out: list = []
    for harness in sk.HARNESSES:
        names = [name for agent, name in session.skills if agent == harness]
        if names:
            out.extend(sk.add_plans(session.root, names, targets=(harness,)))
    return tuple(out)


def _add_checked(
    session: Session, plans: tuple | None = None,
) -> tuple[tuple, int, tuple]:
    """Install every checked pair. The same writer the command line's add
    verbs call, over every plan in one write, and the worst outcome of
    the set. A press that already planned hands its plans in, so the
    trees are composed once and written from that composition; one write
    over every agent's plans, so no press leaves one agent's skills
    installed and the other's not.

    The third value is the (agent, skill) pairs the press wrote for. A
    ticked box whose copy was edited posts like any other and is left
    alone; the finished page prints a command line per pair it wrote,
    not per pair it was asked for, so no line claims an add that never
    happened (PLAN-0015 phase 3 prescription 2)."""
    if plans is None:
        plans = _skill_plans(session)
    sk.write_add_plans(plans)
    outcome = report.OK
    added = []
    for result in plans:
        if result.wrote:
            session.record(report.OK, consequential=True)
            added.append((result.harness.name, result.name))
        if result.edited:
            outcome = report.PARTIAL
    dispositions = tuple(d for result in plans for d in result.dispositions)
    return dispositions, outcome, tuple(added)


def _connect_client(session: Session):
    """The one client this project could still register with, or None.

    Never raises: the connect step is an offer, never a precondition. A
    machine with no client, or with no `radialrig-read` this build can
    locate, still gets the whole mount page — the offer simply is not
    made. A client whose profile directory cannot be read counts as
    absent, as `cn.installed` counts it: `is_dir` raises on a directory
    that denies traversal. A project already registered with this client
    is not offered the step twice.
    """
    try:
        client = cn.client_for(cn.DEFAULT_CLIENT)
        if not cn.detect(client):
            return None
        cn.read_command()
        if cn.is_connected(session.root, client.name):
            return None
    except (cn.ConnectError, OSError):
        return None
    return client


def _connect_plan(session: Session, client) -> tuple:
    """What the offer would write. Writes nothing.

    The same `connect_plan` the command line's --dry-run calls, given the
    scope the page is about to mount with: at review time there is no
    mount record to read one from.
    """
    if client is None or not session.connect:
        return ()
    return cn.connect_plan(session.root, client.name, scope=session.scope).dispositions


def stopped_outcome(exc: BaseException) -> int:
    """The outcome a stop carries: PARTIAL when the writer's error, or
    the kit error wrapping it, names paths not put back; ERROR when the
    tree stands as found (PLAN-0017 phase 2)."""
    for candidate in (exc, exc.__cause__):
        if isinstance(candidate, (mnt.WriteStopped, cn.WriteStopped)):
            return report.PARTIAL if candidate.left else report.ERROR
    return report.ERROR


def _connect_checked(
    session: Session, client,
) -> tuple[tuple, object | None, str, int]:
    """Register the project, when the box is ticked. The same
    `connect.connect` the command line calls.

    Four values, because a caller may not claim the registration ran: the
    rows, the client they belong to, the sentence saying why it did not
    when the connect failed between the plan and the write, and the
    outcome that failure carries. A registration refused before its write
    leaves the outcome alone; one that stopped on a file error carries
    the outcome its sentence earns: partial when a copy is left changed,
    an error when every copy was put back.
    """
    if client is None or not session.connect:
        return (), None, "", report.OK
    try:
        result = cn.connect(session.root, client.name, scope=session.scope)
    except cn.ConnectRefused as exc:
        return (), None, report.connect_refusal_detail(
            exc.client.name, session.root, exc.conflicts), report.OK
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        # The writer put back what it could and its sentence says what
        # it could not. The page prints that sentence; it does not reach
        # the generic boundary, which cannot.
        return (), None, str(exc), stopped_outcome(exc)
    except cn.ConnectError as exc:
        # The offer was planned before the mount ran. Anything that
        # refuses it now arrived between the two, and a mount that landed
        # is not undone by it.
        return (), None, str(exc), report.OK
    if result.wrote:
        session.record(report.OK, consequential=True)
    ro.record_seen(session.root)
    return result.dispositions, client, "", report.OK


def _connect_conflict_state(session: Session, exc) -> PageState:
    """A connect refusal at press time, through the same CONFLICT state a
    mount refusal uses. The head and the remedy differ, so both ride from
    the command line's own report."""
    return PageState(
        CONFLICT, report.REFUSED, False,
        dispositions=exc.conflicts,
        lines=tuple(report.connect_refused_report(
            exc.client, session.root, exc.conflicts)),
        message=report.connect_refusal_remedy(
            exc.client.name, session.root, exc.conflicts),
    )


def _skill_conflict_state(exc) -> PageState:
    """A skill refusal, through the same CONFLICT state a mount refusal
    uses. The remedy differs, so it rides in `message`; the controls do
    not, so there is still no affordance to force past it."""
    return PageState(
        CONFLICT, report.REFUSED, False,
        dispositions=exc.conflicts,
        lines=tuple(report.skill_conflict_report(exc.conflicts)),
        message=report.SKILL_FOREIGN_TAIL,
    )


def _mount_conflict_state(exc) -> PageState:
    return PageState(
        CONFLICT, report.REFUSED, False,
        dispositions=report.conflict_dispositions(exc.conflicts),
        lines=tuple(report.conflict_report(exc.conflicts)),
    )


def _inside_mounted_state(exc) -> PageState:
    """A mount refused inside a mounted project, through the same CONFLICT
    state: the command line's head, no table, since no path is in the way,
    and its remedy as the note. No control forces past it."""
    return PageState(
        CONFLICT, report.REFUSED, False,
        lines=tuple(report.inside_mounted_report(exc.root, exc.parent)),
        message=report.inside_mounted_remedy(exc.root, exc.parent),
    )


def compute_state(session: Session) -> PageState:
    """What this project looks like right now. Writes nothing."""
    root = session.root
    try:
        catalog = _skill_states(session)
        skills = _skill_plan(session)
    except sk.SkillConflict as exc:
        return _skill_conflict_state(exc)
    except sk.SkillError as exc:
        return PageState(FAILED, report.ERROR, True, message=str(exc))
    client = _connect_client(session)
    notice = ""
    try:
        connect = _connect_plan(session, client)
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        # A file error while the offer is still planned: nothing was
        # written, and the page says so rather than dropping the offer
        # without a word.
        return PageState(FAILED, report.ERROR, True, message=str(exc))
    except cn.ConnectRefused as exc:
        # A refused registration withdraws the offer; it never takes the
        # mount button with it. The connect is an offer, never a
        # precondition, so the page says why it is not on the table and
        # leaves the press that brought the reader here alone.
        client, connect = None, ()
        notice = report.connect_withheld_notice(
            exc.client.name, session.root, exc.conflicts)
    except cn.ConnectError:
        client, connect = None, ()
    try:
        if mnt.is_mounted(root):
            manifest = mnt.read_manifest(root)
            # A mounted project used to be the end of the walkthrough.
            # It still is, unless this package ships a skill this project
            # does not already have, or a client on this machine is not
            # registered with it: then there is one more thing the page
            # can do, and stopping would hide it.
            return PageState(
                ALREADY, report.NOOP,
                not any(not s.current for s in catalog) and client is None,
                lines=tuple(report.already_mounted_report(root, manifest)),
                skills=skills, catalog=catalog, connect=connect, client=client,
                message=notice,
            )
        plan = mnt.mount_plan(root, scope=session.scope, owner=session.owner)
    except mnt.MountConflict as exc:
        return _mount_conflict_state(exc)
    except mnt.InsideMountedProject as exc:
        return _inside_mounted_state(exc)
    except mnt.MountError as exc:
        return PageState(FAILED, report.ERROR, True, message=str(exc))
    return PageState(
        REVIEW, report.NOOP, False, dispositions=plan.dispositions(), result=plan,
        skills=skills, catalog=catalog, connect=connect, client=client,
        message=notice,
    )


def perform_mount(session: Session) -> PageState:
    """The one consequential press: the mount, then each checked skill.

    Three CLI-reachable behaviors in sequence — `radialrig mount`, then
    `radialrig skill add <name> --harness <agent>` per ticked pair, then
    `radialrig connect <client>` — and the finished page prints the skill
    lines, so the run is reproducible from the terminal. Both offers are
    planned first: a skill that would land
    on a directory this project owns, or an entry key another server
    already holds, stops the press before the mount writes its first byte.
    """
    client = _connect_client(session)
    try:
        plans = _skill_plans(session)
    except sk.SkillConflict as exc:
        return _skill_conflict_state(exc)
    except sk.SkillError as exc:
        return PageState(FAILED, report.ERROR, True, message=str(exc))
    failed = ""
    try:
        _connect_plan(session, client)
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        return PageState(FAILED, report.ERROR, True, message=str(exc))
    except cn.ConnectRefused as exc:
        return _connect_conflict_state(session, exc)
    except cn.ConnectError as exc:
        # The offer could not be planned. The mount still runs, and the
        # engine's sentence rides to the page the way the skills press
        # carries it (tests-06).
        client, failed = None, str(exc)
    try:
        result = mnt.mount(session.root, scope=session.scope, owner=session.owner)
    except mnt.AlreadyMounted:
        ro.record_seen(session.root)
        return compute_state(session)
    except mnt.MountConflict as exc:
        return _mount_conflict_state(exc)
    except mnt.InsideMountedProject as exc:
        return _inside_mounted_state(exc)
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        # The writer put back what it could and its sentence says what
        # it could not: a partial press when something is left, a clean
        # stop when the tree stands as found.
        return PageState(FAILED, stopped_outcome(exc), True, message=str(exc))
    except mnt.MountError as exc:
        return PageState(FAILED, report.ERROR, True, message=str(exc))
    session.record(report.OK, consequential=True)
    ro.record_seen(session.root)
    try:
        skills, outcome, added = _add_checked(session, plans)
    except sk.SkillError as exc:
        # The mount has landed. The page reports it with the mount's own
        # rows, says which step did not land, and stops there: the
        # registration is not attempted after a step that failed, and
        # when one was ticked the page says that too.
        message = str(exc)
        if failed:
            message += " " + failed
        elif client is not None and session.connect:
            message += " " + CONNECT_NOT_ATTEMPTED
        return PageState(
            MOUNTED, report.PARTIAL, True,
            dispositions=result.dispositions(),
            lines=tuple(report.mount_report(result)),
            result=result, message=message,
        )
    connect, used, error, stopped = _connect_checked(session, client)
    return PageState(
        MOUNTED, max(outcome, stopped), True,
        dispositions=result.dispositions(),
        lines=tuple(report.mount_report(result)),
        result=result, skills=skills, added=added, connect=connect, client=used,
        # The mount's own head reports the mount. A registration that did
        # not run says so here rather than being left unsaid.
        message=failed or error,
    )


def perform_skills(session: Session) -> PageState:
    """The two offers on their own, for a project that is already mounted."""
    client = _connect_client(session)
    offered = client is not None and session.connect
    if not session.skills and not offered:
        return compute_state(session)
    # Both offers are planned before either writes, for the reason the
    # mount press does it: a refusal stops the whole press, not half.
    try:
        plans = _skill_plans(session)
    except sk.SkillConflict as exc:
        return _skill_conflict_state(exc)
    except sk.SkillError as exc:
        return PageState(FAILED, report.ERROR, True, message=str(exc))
    failed = ""
    try:
        _connect_plan(session, client)
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        return PageState(FAILED, report.ERROR, True, message=str(exc))
    except cn.ConnectRefused as exc:
        return _connect_conflict_state(session, exc)
    except cn.ConnectError as exc:
        client, failed = None, str(exc)

    skills: tuple = ()
    added: tuple = ()
    outcome = report.OK
    if session.skills:
        try:
            skills, outcome, added = _add_checked(session, plans)
        except sk.SkillError as exc:
            # The kit's error carries the writer's sentence, and the
            # outcome follows it.
            return PageState(FAILED, stopped_outcome(exc), True, message=str(exc))
    connect, used, error, stopped = _connect_checked(session, client)
    failed = failed or error
    if skills:
        return PageState(SKILLED, max(outcome, stopped), True, skills=skills,
                         added=added, connect=connect, client=used, message=failed)
    if failed or not connect:
        # Nothing to claim: the registration was the whole press and it
        # did not run, or stopped part way through its write. The head
        # says which, and the engine's own sentence says why.
        return PageState(CONNECT_FAILED, stopped or report.ERROR, True, message=failed)
    return PageState(CONNECTED, outcome, True, connect=connect, client=used)


# --- assets -----------------------------------------------------------

FAVICONS = (
    ("favicon.svg", "image/svg+xml", None),
    ("favicon-32x32.png", "image/png", "32x32"),
    ("favicon-16x16.png", "image/png", "16x16"),
)
APPLE_ICON = "apple-touch-icon.png"
FONT_FILE = "fonts/SpaceGrotesk-Medium.woff2"


@lru_cache(maxsize=1)
def assets() -> dict:
    """Every byte the page carries, read once from package data.

    A missing piece degrades rather than fails: no font means the platform
    stack, no marks means a page with no logo. The walkthrough still runs,
    because a mount is what the user came for.
    """
    base = packaged_web()
    out: dict = {
        "css": "", "js": "", "mark_dark": "", "mark_light": "",
        "font": None, "icons": [], "apple": None,
    }
    if base is None:
        return out

    def text(rel: str) -> str:
        path = base / rel
        try:
            return path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        except OSError:
            return ""

    def data(rel: str) -> bytes | None:
        try:
            return (base / rel).read_bytes()
        except OSError:
            return None

    out["css"] = text("page.css")
    out["js"] = text("page.js")
    out["mark_dark"] = text("marks/radialrig-symbol-dark.svg").strip()
    out["mark_light"] = text("marks/radialrig-symbol-light.svg").strip()
    out["font"] = data(FONT_FILE)
    for name, mime, sizes in FAVICONS:
        blob = data(f"favicon/{name}")
        if blob is not None:
            out["icons"].append((mime, sizes, _data_uri(mime, blob)))
    apple = data(f"favicon/{APPLE_ICON}")
    if apple is not None:
        out["apple"] = _data_uri("image/png", apple)
    return out


def _data_uri(mime: str, blob: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"


def font_face(blob: bytes | None) -> str:
    """The vendored brand face, inline. Empty when it is not installed."""
    if not blob:
        return ""
    return (
        "@font-face{font-family:\"RadialRig Space Grotesk\";font-style:normal;"
        "font-weight:500;font-display:swap;src:url(\""
        + _data_uri("font/woff2", blob)
        + "\") format(\"woff2\");}\n"
    )


# --- rendering --------------------------------------------------------

def _esc(value) -> str:
    """Text between tags. Quotes stay as the command line wrote them, so a
    line the page shows reads exactly as the terminal's does."""
    return html.escape(str(value), quote=False)


def _attr(value) -> str:
    """An attribute value. Scope and owner arrive from a form field."""
    return html.escape(str(value), quote=True)


def _chip(verb: str) -> str:
    """An outlined chip marks a row that touches a file already standing.

    Outlined rather than filled: the accepted wireframe spends this
    screen's one amber on the press that writes, and the distinction a
    reader needs — a path this project already has, against a path it
    does not — is carried by the outline and by `PLAN_HINT` under the
    table.
    """
    touches = verb in (mnt.MERGED, mnt.APPENDED, mnt.CURRENT, mnt.LEFT)
    cls = "chip touches" if touches else "chip"
    return f'<span class="{cls}">{_esc(verb)}</span>'


# --- the mount table's families ---------------------------------------
# The accepted wireframe draws the mount plan as six rows, not eleven:
# the three folders a mount creates are one line, the two seeded specs
# are one line, the three templates are one line, and the three files it
# touches keep a line each. What a family row says is the wireframe's own
# sentence for that family, which is why the "What it does" column is
# filled for every row here where the engine's own `MOUNT_CREATED` is
# deliberately empty for a bare create.
#
# The families are a reading of the plan, never a picture pinned over it:
# a family is drawn only when the plan carries its paths, it takes the
# verb of the path it stands for, and any path no family names keeps its
# own row and the engine's own sentence.

@dataclass(frozen=True)
class Family:
    """One row of the wireframe's mount table."""

    paths: tuple[str, ...]  # the plan paths this row stands for
    label: str              # what the path cell reads
    reason: str             # what the "What it does" cell reads


MOUNT_FAMILIES: tuple[Family, ...] = (
    Family(
        (mnt.MCP_REL,), mnt.MCP_REL,
        "one RadialRig entry added; nothing else in the file changes",
    ),
    Family(
        (mnt.MANIFEST_REL,), mnt.MANIFEST_REL,
        "the record RadialRig uses to take everything back out",
    ),
    Family(
        (mnt.AGENTS_REL,), mnt.AGENTS_REL,
        "one fenced section at the end; the rest of the file is unchanged",
    ),
    Family(
        (mnt.CLAUDE_REL,), mnt.CLAUDE_REL,
        "one fenced line importing AGENTS.md, so Claude Code reads the route map",
    ),
    Family(
        (mnt.CODEX_REL,), mnt.CODEX_REL,
        "the same two servers registered for Codex, in one fenced region at the end",
    ),
    Family(
        ("docs/decisions/.gitkeep", "docs/plans/.gitkeep", "docs/archive/.gitkeep"),
        "docs/decisions/, docs/plans/, docs/archive/",
        "three document folders, kept in git even while empty",
    ),
    Family(
        ("docs/archive/D-00-archive-exclusion-target.md",),
        "docs/archive/D-00-archive-exclusion-target.md",
        "one archived record, so the archive search filter has something to find",
    ),
    Family(
        ("docs/spec.md", "docs/spec-git-environment.md"),
        "docs/spec.md, docs/spec-git-environment.md",
        "rules for this project's records, and default branch, pull-request "
        "and CI rules that agents follow",
    ),
    Family(
        ("docs/templates/decision.md", "docs/templates/plan.md",
         "docs/templates/spec.md"),
        "docs/templates/",
        "three blank templates: decision, plan, spec",
    ),
)


# The wrote-table's sentences for the codes the engine leaves empty.
# On the command line "created <path>" is a whole line and needs no
# tail; in a three-column table the empty cell reads as a hole. Same
# rule as the families above: the page fills the cell, the engine's
# own wording stays empty for the command line. The record rows say
# what the record is for, in the mount table's own words.
WROTE_REASONS: dict[str, str] = {
    mnt.SKILL_ADDED:
        "one new skill folder; every file in it is this package's",
    mnt.SKILL_RECORD_WRITTEN:
        "the record RadialRig uses to take everything back out",
    mnt.CONNECT_RECORD_WRITTEN:
        "the record RadialRig uses to take the connection back out",
}


def _resolved(dispositions) -> list[tuple[str, str, str]]:
    """Every disposition as its three cells, in the engine's own words."""
    out = []
    for d in dispositions:
        reason = report.conflict_reason(d.path) if d.code == "CONFLICT" \
            else (mnt.reason_text(d) or WROTE_REASONS.get(d.code, ""))
        out.append((d.verb, d.path, reason))
    return out


def _families(dispositions) -> list[tuple[str, str, str]]:
    """The plan's paths, gathered into the wireframe's family rows."""
    held = {d.path: d for d in dispositions}
    taken: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for family in MOUNT_FAMILIES:
        present = [held[path] for path in family.paths if path in held]
        if not present:
            continue
        taken.update(d.path for d in present)
        lead = present[0]
        # The family's sentence describes a write. A row the engine marks
        # current is a path it will not write (the project already holds
        # what the seed would add), and the engine's own sentence says so.
        reason = family.reason
        if lead.verb == mnt.CURRENT:
            reason = mnt.reason_text(lead) or family.reason
        out.append((lead.verb, family.label, reason))
    out.extend(cells for cells in _resolved(dispositions) if cells[1] not in taken)
    return out


def _rows(cells) -> str:
    return "".join(
        "<tr>"
        f'<td class="verb">{_chip(verb)}</td>'
        f'<td class="path">{_esc(path)}</td>'
        f'<td class="why">{_esc(reason)}</td>'
        "</tr>"
        for verb, path, reason in cells
    )


def _table(cells, caption: str) -> str:
    """One plan table. `caption` is markup: a caption that counts its
    paths carries an entity, and the count is composed by the caller."""
    if not cells:
        return ""
    return (
        '<div class="plan-wrap"><table class="plan">'
        f"<caption>{caption}</caption>"
        '<thead><tr><th scope="col">Change</th><th scope="col">Path</th>'
        '<th scope="col">What it does</th></tr></thead>'
        f"<tbody>{_rows(cells)}</tbody></table></div>"
    )


def _plan_table(dispositions, caption: str, *, count: bool = True) -> str:
    """One row per path, under a caption that counts them.

    The count is dropped where the owner has ruled it out — the mount
    table, the skills table and the connect plan — and kept everywhere else, so a table of
    paths still says how many it holds.
    """
    if not dispositions:
        return ""
    head = _esc(caption)
    if count:
        # A row marked current was not written; the count says how many
        # paths the press touched, and the row's own sentence says why
        # that one was not among them.
        touched = sum(1 for d in dispositions if d.verb != mnt.CURRENT)
        head += f" &mdash; {touched} path(s)"
    return _table(_resolved(dispositions), head)


def _mount_table(dispositions) -> str:
    """The mount plan, as the wireframe draws it: families, no count, and
    the line under it saying what an outlined chip means."""
    if not dispositions:
        return ""
    return (
        _table(_families(dispositions), _esc(PLAN_CAPTION))
        + f'<p class="table-hint">{_esc(PLAN_HINT)}</p>'
    )


def _skill_group(session: Session, harness: sk.Harness, states) -> str:
    """One agent's group: its label as the heading, one checkbox per
    skill that agent lacks, and beside the heading the control that
    ticks them all (PLAN-0015 phase 3 prescription 1).

    Unchecked by default: the mount is what the user came for, and a box
    that arrives already ticked installs something nobody asked for. A
    skill whose copy for this agent was changed, or one whose target
    directory this project already owns, is shown disabled with the
    engine's own reason beside it rather than hidden — the page says
    what it will not do. The control is a plain button the page's script
    wires up; it stands only where there is a box it could tick, and
    with no script the boxes are ticked one by one and the form posts
    the same. It stands after the legend, not inside it, so the group's
    own name stays the agent's label and the control's name is its own.
    An agent that already has every skill is told so in one sentence
    and offered no boxes (prescription 3).
    """
    rows = []
    open_ = 0
    for state in states:
        if state.current:
            continue
        entry = state.packaged
        blocked = state.edited or state.foreign or state.dir_is_file
        open_ += not blocked
        ticked = (harness.name, state.name) in session.skills and not blocked
        note = mnt.reason_text(state.list_disposition()) if blocked else entry.summary
        rows.append(
            '<label class="choice">'
            '<input type="checkbox" name="skill" '
            f'value="{_attr(box_value(harness.name, state.name))}"'
            f'{" disabled" if blocked else ""}{" checked" if ticked else ""}>'
            f'<span class="name">{_esc(state.name)}</span>'
            f'<span class="what">{_esc(entry.version)} &middot; {_esc(note)}</span>'
            "</label>"
        )
    if rows:
        body = "".join(rows)
    else:
        body = f'<p class="hint">{_esc(SKILLS_AGENT_NONE.format(label=harness.label))}</p>'
    tick = (
        '<button class="tick" type="button" '
        f'aria-label="{_attr(TICK_ALL_NAME.format(label=harness.label))}">'
        f"{_esc(TICK_ALL_LABEL)}</button>"
        if open_ else ""
    )
    return (
        '<fieldset class="skills agent">'
        f"<legend>{_esc(harness.label)}</legend>"
        + tick
        + body
        + "</fieldset>"
    )


def _skills_fieldset(session: Session, catalog) -> str:
    """The skills box: one group per agent, in map order, each headed by
    the agent's label.

    A page where every agent already has every skill keeps the one
    sentence it has always said; otherwise every agent gets its group,
    the complete ones saying so in their own sentence, so the two
    headings stand whichever agent a person came for.
    """
    if not catalog:
        return ""
    if all(state.current for state in catalog):
        body = f'<p class="hint">{_esc(SKILLS_NONE)}</p>'
    else:
        body = "".join(
            _skill_group(session, harness, [
                s for s in catalog if s.harness.name == harness.name])
            for harness in sk.HARNESSES.values()
        )
    return (
        '<fieldset class="skills">'
        f"<legend>{_esc(SKILLS_HEAD)}</legend>"
        f'<p class="hint">{_esc(SKILLS_LEDE)}</p>'
        + body
        + "</fieldset>"
    )


# The characters a slot renders bare with, beside letters and digits: a
# path, a catalog name, an agent key or a client key made of these is the
# same argument to cmd.exe and to bash, quoted or not.
BARE_CHARS = "_./\\:~-"
# The characters no one rule makes safe in both shells the page targets
# (PLAN-0017 phase 4, setup-web-04). Double quotes stop cmd.exe splitting
# on a space and do nothing more: `%` and `!` still expand inside them
# there, `$` and a backtick still expand inside them in bash, a double
# quote ends them, and each of `^ & | ; < > ' * ? ( )` means something to
# one shell that a rule written for the other would leave bare.
UNSAFE_CHARS = "\"`$%^&|;<>!'*?()"


class Unquotable(ValueError):
    """A slot no quoting rule makes safe in both shells the page targets."""


def quoted(value: str) -> str:
    r"""One slot of a command line, ready to paste into a shell.

    Two rules. A slot made of letters, digits and `_ . / \ : ~ -` renders
    bare, so every command block a reader has copied before is the line
    it was. A slot with any other character goes in double quotes, which
    both shells take: a space is the ordinary case, since anything under
    `C:\Users\First Last` carries one, and a path bare with a space in it
    is one the argument parser reads as two. A slot carrying a double
    quote, a backtick, one of `$ % ^ & | ; < > ! ' * ? ( )` or a control
    character raises `Unquotable`: no one rule is safe for it in both
    shells, so every builder catches the raise and offers no copy line at
    all rather than a line that runs something the reader did not type
    (PLAN-0017 phase 4, setup-web-04). The manage page's command table
    quotes its slots through this same function.
    """
    for ch in value:
        if ch in UNSAFE_CHARS or unicodedata.category(ch) == "Cc":
            raise Unquotable(value)
    if all(ch.isalnum() or ch in BARE_CHARS for ch in value):
        return value
    return f'"{value}"'


def skill_add_line(harness: str, name: str, root) -> str:
    """The command line that adds one skill for one agent, as the
    finished page prints it: the same line the manage page's command
    table composes for the same press."""
    return SKILL_ADD_LINE.format(
        name=quoted(name), root=quoted(str(root)), harness=quoted(harness))


def _skill_lines(session: Session, state: PageState) -> str:
    """The skill press, as the command line would run it: one line per
    pair the press wrote for, so a finished page says what it did in a
    form a reader can run again elsewhere (PLAN-0015 phase 3
    prescription 2). Nothing where the press wrote for none — a ticked
    box whose copy was edited is left alone, and its line would claim an
    add that did not happen. Nothing either where the project's path is
    one no quoting rule makes safe: the table above still says what the
    press wrote, and no line is offered that would run something else
    (PLAN-0017 phase 4, setup-web-04)."""
    if not state.added:
        return ""
    try:
        items = "".join(
            f"<li>{_command(skill_add_line(harness, name, session.root))}</li>"
            for harness, name in state.added
        )
    except Unquotable:
        return ""
    return (
        '<fieldset class="skills">'
        f"<legend>{_esc(SKILLS_RAN_HEAD)}</legend>"
        f'<p class="hint">{_esc(SKILLS_RAN_LEDE)}</p>'
        f'<ul class="steps">{items}</ul>'
        "</fieldset>"
    )


def connect_head(client) -> str:
    """The connect legend, with the detected client's name in it.

    The constant stays a template so no page constant names a client;
    the name arrives from the client table, the same road the restart
    sentence takes."""
    return CONNECT_HEAD.format(client=client.label)


def connect_lede(client) -> str:
    """The connect hint, with the detected client's name in it."""
    return CONNECT_LEDE.format(client=client.label)


def _connect_fieldset(session: Session, client) -> str:
    """One checkbox for the one detected client, or nothing at all.

    Ticked to begin with, because the offer only appears when a client is
    on this machine and this project is not registered with it yet. The
    sub-line names the settings files that would be edited and the key
    that would be written, so the box says what pressing it does. The
    posted value is filtered against the client table before anything
    downstream reads it, so a posted string can never become a path.
    """
    if client is None:
        return ""
    where = ", ".join(target.path.as_posix() for target in cn.detect(client))
    try:
        key = cn.entry_key(client, session.scope)
    except cn.ConnectError:
        return ""
    checked = " checked" if session.connect else ""
    return (
        '<fieldset class="skills">'
        f"<legend>{_esc(connect_head(client))}</legend>"
        f'<p class="hint">{_esc(connect_lede(client))}</p>'
        '<label class="choice">'
        f'<input type="checkbox" name="connect" value="{_attr(client.name)}"{checked}>'
        f'<span class="name">{_esc(client.label)}</span>'
        f'<span class="what">{_esc(key)} &middot; {_esc(where)}</span>'
        "</label>"
        "</fieldset>"
    )


def _notice(state: PageState) -> str:
    """The page's one aside: an offer that is not on the table, and why.

    The sentence comes out of `report`, so the page and the terminal give
    the same account of the same refusal.
    """
    if not state.message:
        return ""
    return f'<p class="note">{_esc(state.message)}</p>'


def _restart_line(state: PageState) -> str:
    """The client's own restart sentence, from the client table."""
    if state.client is None:
        return ""
    return f'<p class="closing">{_esc(state.client.restart)}</p>'


def _command(text: str, *, name: str = "") -> str:
    """The command frame: the line, and the button that copies it.

    `name` is the Copy button's accessible name, for a surface whose
    buttons have to say which command each one copies (PLAN-0014 phase
    2); left empty, the frame is the bytes this page has always served.
    """
    named = f' aria-label="{_attr(name)}"' if name else ""
    return (
        f'<div class="cmd"><code>{_esc(text)}</code>'
        f'<button class="copy" type="button" data-copy="{_attr(text)}"{named}>Copy</button>'
        "</div>"
    )


def _form(session: Session, inner: str) -> str:
    return (
        f'<form method="post" action="{session.page_path}">'
        f'<input type="hidden" name="token" value="{_attr(session.token)}">'
        f"{inner}</form>"
    )


def _choices() -> str:
    """The marker a form that draws the boxes carries (setup-web-03).

    Only the review and already-mounted forms draw them, so only those
    two carry this; the refusal's form posts without it, and `_apply`
    then leaves the boxes as the reader left them.
    """
    return f'<input type="hidden" name="{CHOICES_FIELD}" value="1">'


def _review_body(session: Session, state: PageState) -> str:
    fields = (
        '<div class="fields">'
        '<div class="field"><label for="scope">Scope</label>'
        f'<input id="scope" name="scope" value="{_attr(session.scope)}" '
        'autocomplete="off" spellcheck="false">'
        f'<p class="hint">{_esc(SCOPE_HINT)}</p></div>'
        '<div class="field"><label for="owner">Owner</label>'
        f'<input id="owner" name="owner" value="{_attr(session.owner)}" '
        'autocomplete="off" spellcheck="false">'
        f'<p class="hint">{_esc(OWNER_HINT)}</p></div>'
        "</div>"
    )
    actions = (
        '<div class="actions">'
        f'<button class="primary" type="submit" name="action" value="mount">'
        f"{_esc(MOUNT_LABEL)}</button>"
        f'<button class="ghost" type="submit" name="action" value="survey">'
        f"{_esc(REFRESH_LABEL)}</button>"
        "</div>"
    )
    return (
        f"<h1>{_esc(REVIEW_HEAD)}</h1>"
        f'<p class="lede">{_esc(REVIEW_LEDE)}</p>'
        '<div class="root"><span class="label">Project</span>'
        f"<code>{_esc(session.root)}</code></div>"
        + _form(
            session,
            _choices()
            + fields
            + _mount_table(state.dispositions)
            + _skills_fieldset(session, state.catalog)
            # Two tables, not one merged one: the mount's caption and the
            # skills' caption each say what their own button writes, and
            # what the tables show is exactly what the one press does.
            + _plan_table(state.skills, SKILLS_PLAN_CAPTION, count=False)
            + _connect_fieldset(session, state.client)
            + _plan_table(state.connect, CONNECT_PLAN_CAPTION, count=False)
            + _notice(state)
            + f'<p class="note">{_esc(READ_SERVER_NOTE)}</p>'
            + actions,
        )
    )


def _mounted_body(session: Session, state: PageState) -> str:
    result = state.result
    step, validate = report.next_step(result.root)
    skills = (
        f'<li><span class="what">{SKILLS_REVERSIBLE}</span>'
        + _command("radialrig skill list") + "</li>"
        if state.skills else ""
    )
    connect = (
        f'<li><span class="what">{CONNECT_REVERSIBLE}</span></li>'
        if state.connect else ""
    )
    return (
        f"<h1>Mounted onto {_esc(result.scope)}.</h1>"
        f'<p class="meta">radialrig {_esc(result.version)} &middot; scope: '
        f"{_esc(result.scope)} &middot; owner: {_esc(result.owner)}</p>"
        + _plan_table(state.dispositions, WROTE_CAPTION)
        + _plan_table(state.skills, SKILLS_WROTE_CAPTION)
        + _skill_lines(session, state)
        + _plan_table(state.connect, CONNECT_WROTE_CAPTION)
        + '<ul class="steps">'
        + f'<li><span class="what">{_esc(step)}</span>'
        + _command(validate) + "</li>"
        + skills
        + connect
        + f'<li><span class="what">{REVERSIBLE}</span>'
        + _command("radialrig unmount") + "</li>"
        + "</ul>"
        + _notice(state)
        + f'<p class="closing">{_esc(STOPPED)}</p>'
        + _restart_line(state)
    )


def _skilled_body(session: Session, state: PageState) -> str:
    connect = (
        f'<li><span class="what">{CONNECT_REVERSIBLE}</span></li>'
        if state.connect else ""
    )
    return (
        f"<h1>{_esc(SKILLS_DONE_HEAD)}</h1>"
        + _plan_table(state.skills, SKILLS_WROTE_CAPTION)
        + _skill_lines(session, state)
        + _plan_table(state.connect, CONNECT_WROTE_CAPTION)
        + '<ul class="steps">'
        + f'<li><span class="what">{_esc(report.SKILL_NEXT_STEP)}</span>'
        + _command("radialrig skill list") + "</li>"
        + f'<li><span class="what">{SKILLS_REVERSIBLE}</span></li>'
        + connect
        + "</ul>"
        + _notice(state)
        + f'<p class="closing">{_esc(STOPPED)}</p>'
        + _restart_line(state)
    )


def _connected_body(session: Session, state: PageState) -> str:
    """The connect step on its own, for a project already mounted."""
    return (
        f"<h1>{_esc(CONNECT_DONE_HEAD)}</h1>"
        + _plan_table(state.connect, CONNECT_WROTE_CAPTION)
        + '<ul class="steps">'
        + f'<li><span class="what">{CONNECT_REVERSIBLE}</span></li>'
        + "</ul>"
        + f'<p class="closing">{_esc(STOPPED)}</p>'
        + _restart_line(state)
    )


def _connect_failed_body(session: Session, state: PageState) -> str:
    """The press was the registration, and it did not run, or it stopped
    part way through its write.

    No table and no restart line: there is nothing to restart for. The
    head states that much and the engine's own sentence says why.
    """
    head = PARTIAL_HEAD if state.outcome == report.PARTIAL else CONNECT_FAILED_HEAD
    return (
        f"<h1>{_esc(head)}</h1>"
        + (f'<p class="said">{_esc(state.message)}</p>' if state.message else "")
        + log_said()
        + f'<p class="closing">{_esc(STOPPED)}</p>'
    )


def _conflict_body(session: Session, state: PageState) -> str:
    # The command line's own first line is the heading, so a refusal that
    # words its head differently — the connect's does, because it inserts
    # and overwrites nothing — reads the same on both surfaces.
    head = state.lines[0] if state.lines else report.refusal_head(
        len(state.dispositions))
    # The refusal is this state's whole message, so it is the document's
    # heading as well as its live region: a reader landing by heading has
    # somewhere to land.
    return (
        '<div class="refusal" role="status" aria-live="polite">'
        f'<h1 class="head">{_esc(head)}</h1>'
        + _plan_table(state.dispositions, CONFLICT_CAPTION)
        + "</div>"
        # One refusal state, two remedies: the skills kit's own sentence
        # rides in `message`, and the mount's is the default. Neither
        # carries a control that could force past the refusal.
        + f'<p class="note">{_esc(state.message or report.REFUSAL_REMEDY)}</p>'
        + _form(
            session,
            '<div class="actions">'
            f'<button class="primary" type="submit" name="action" value="survey">'
            f"{_esc(CHECK_AGAIN_LABEL)}</button></div>",
        )
    )


def _head_with_root(line: str, root) -> str:
    """The command line's first line as a heading, with the project path
    set in mono rather than in the display face.

    The heading reads character for character as the terminal's line
    does; only the path's typography differs. A line that does not end in
    this project's path is left whole, so a change in `report` cannot
    make this render something the command line did not say.
    """
    tail = str(root)
    if line.endswith(tail) and len(line) > len(tail):
        return (
            f'<h1 class="record">{_esc(line[: -len(tail)])}'
            f"<code>{_esc(tail)}</code></h1>"
        )
    return f"<h1>{_esc(line)}</h1>"


def _already_body(session: Session, state: PageState) -> str:
    lines = list(state.lines)
    body = _head_with_root(lines[0], session.root)
    for line in lines[1:]:
        body += f'<p class="said">{_esc(line)}</p>'
    body += _command("radialrig upgrade")
    if state.terminal:
        # Byte for byte what this state has always been: a mounted
        # project with nothing left for the page to do — plus the one
        # aside, when an offer was withdrawn rather than never made.
        return body + _notice(state) + f'<p class="closing">{_esc(STOPPED)}</p>'
    # One button for both offers. Its label names whichever offer stands:
    # a project with every skill installed is here for the registration.
    label = SKILLS_LABEL if any(not s.current for s in state.catalog) else CONNECT_LABEL
    return body + _form(
        session,
        _choices()
        + _skills_fieldset(session, state.catalog)
        + _plan_table(state.skills, SKILLS_PLAN_CAPTION, count=False)
        + _connect_fieldset(session, state.client)
        + _plan_table(state.connect, CONNECT_PLAN_CAPTION, count=False)
        + _notice(state)
        + '<div class="actions">'
        + '<button class="primary" type="submit" name="action" value="skills">'
        + f"{_esc(label)}</button></div>",
    )


def log_said() -> str:
    """The log line as a paragraph of a stop body, or nothing where this
    platform keeps no log (PLAN-0018 phase 2). Both pages' stop bodies
    draw it, so the sentence lives once."""
    line = log_line()
    return f'<p class="said">{_esc(line)}</p>' if line else ""


def _failed_body(session: Session, state: PageState) -> str:
    """A stop. The head says whether it came before any write or part
    way through one, which is what the outcome carries."""
    head = PARTIAL_HEAD if state.outcome == report.PARTIAL else FAILED_HEAD
    return (
        f"<h1>{_esc(head)}</h1>"
        f'<p class="said">{_esc(state.message)}</p>'
        + log_said()
        + f'<p class="closing">{_esc(STOPPED)}</p>'
    )


BODIES = {
    REVIEW: _review_body,
    MOUNTED: _mounted_body,
    CONFLICT: _conflict_body,
    ALREADY: _already_body,
    FAILED: _failed_body,
    SKILLED: _skilled_body,
    CONNECTED: _connected_body,
    CONNECT_FAILED: _connect_failed_body,
}


def document(
    session: Session,
    *,
    body: str,
    terminal: bool,
    nonce: str,
    title: str = TITLE,
) -> str:
    """The shell around a body: one document, no subresource, no external
    request.

    Everything outside `<main>` is the same for every page this package
    serves — the same inlined kit, the same masthead, the same one
    endorsement with the installed engine's stamp beside it (PLAN-0018
    phase 1), the same script — so a second surface wraps its own body
    in this rather than growing a second copy that can drift from it.
    """
    kit = assets()
    links = [
        f'<link rel="icon" type="{mime}"'
        + (f' sizes="{sizes}"' if sizes else "")
        + f' href="{uri}">'
        for mime, sizes, uri in kit["icons"]
    ]
    if kit["apple"]:
        links.append(f'<link rel="apple-touch-icon" href="{kit["apple"]}">')
    marks = (
        f'<span class="mark dark" aria-hidden="true">{kit["mark_dark"]}</span>'
        f'<span class="mark light" aria-hidden="true">{kit["mark_light"]}</span>'
    )
    return (
        "<!doctype html>\n"
        f'<html lang="en" data-token="{_attr(session.token)}" '
        f'data-endpoint="{session.page_path}" '
        f'data-terminal="{"1" if terminal else "0"}">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="dark light">\n'
        f"<title>{_esc(title)}</title>\n"
        + "\n".join(links)
        + f'\n<style nonce="{nonce}">\n'
        + font_face(kit["font"])
        + kit["css"]
        + "</style>\n</head>\n<body>\n"
        '<div class="sheet">\n'
        f'<header class="masthead">{marks}'
        f'<span class="name">{_esc(title)}</span>'
        f'<span class="endorse">{_esc(ENDORSEMENT)}</span>'
        f'<span class="version">{_esc(mnt.version_stamp())}</span></header>\n'
        f"<main>{body}</main>\n</div>\n"
        f'<script nonce="{nonce}">\n{kit["js"]}</script>\n'
        "</body>\n</html>\n"
    )


def render_page(session: Session, state: PageState, *, nonce: str) -> str:
    """One walkthrough state, in the shared shell."""
    return document(
        session,
        body=BODIES[state.kind](session, state),
        terminal=state.terminal,
        nonce=nonce,
    )


# The root element the document builder writes, and the one place a
# rendered document is opened up again.
_ROOT_TAG = "<html "

# What this module says when that element is not there to stamp. A
# replace that matched nothing returns the string it was given, so the
# document would go out with no id on it, the close beacon would post
# none, and every close would read as current again — the reload defense
# gone, silently, on a build where nothing looked wrong.
NO_ROOT_TAG = (
    f"radialrig: the rendered document has no {_ROOT_TAG.strip()!r} "
    "element for the render id, so the reload defense would be off."
)


def _stamped(page: str, render_id: str) -> str:
    """One rendered document, carrying the id its session issued for it.

    The attribute is put onto the root element the builder already wrote
    rather than into the builder, so a second surface that wraps its own
    body in `document` is stamped the same way in the same place. The
    doctype line has no such tag in it, so the first match is the root
    element and there is nothing else to match.

    A document with no such element is a hard stop rather than a page
    served without an id: a change to the builder that renamed the root
    element would leave this replacing nothing and revive the F5 kill
    quietly. So it fails at the point it breaks, loudly, and the request
    boundary turns it into the 500 every other internal fault gets.
    """
    if _ROOT_TAG not in page:
        raise RuntimeError(NO_ROOT_TAG)
    return page.replace(
        _ROOT_TAG, f'{_ROOT_TAG}{RENDER_ATTR}="{_attr(render_id)}" ', 1)


# --- the server -------------------------------------------------------

class SetupHandler(BaseHTTPRequestHandler):
    """One page, one form target, nothing else. No directory listing
    exists here because no path is ever mapped onto the file system."""

    session: Session = None  # bound by serve()
    server_version = "radialrig-setup"
    sys_version = ""
    # The two words every refusal this handler serves says about itself.
    # They are class attributes for the reason `page_path` is one: a
    # subclass serving another route says its own name through the same
    # sentences rather than through a second copy of them.
    surface = SURFACE
    page_name = PAGE_NAME
    # The log's name for this surface (PLAN-0018 phase 2), in the words
    # Constraint 3 fixes; a subclass serving another page says its own.
    log_surface = al.SETUP_PAGE
    # How a row becomes a sentence in the log, for a state that carries
    # rows and no report lines of its own. The mount kit's table here;
    # a subclass whose presses report other kits' rows passes its own.
    _reason = staticmethod(mnt.reason_text)
    # A client that promises a body and does not send it holds a thread
    # until this expires rather than for as long as it likes.
    timeout = 15

    def log_message(self, fmt, *args):  # noqa: A003 - quiet by design
        return

    # --- the log (PLAN-0018 phase 2) ------------------------------------
    # Every press leaves one event, and every error the boundary catches
    # leaves one with its traceback. Both are wrapped whole: the log is a
    # trace of the page, never a condition on it (Invariant 1).

    def _pressed(self, fields) -> str:
        """What this post pressed, for the log. Every post on this page
        is a press; the review form's check is the survey."""
        return (fields.get("action") or ["survey"])[0]

    def _folder(self, fields=None) -> str:
        """The folder a press or a request is about."""
        return str(self.session.root)

    def _state_lines(self, state: PageState) -> list[str]:
        """The state in the command line's words: its own lines where it
        has them, else its rows, and its message."""
        lines = list(state.lines)
        if not lines and state.dispositions:
            lines = report.render_rows(state.dispositions, self._reason)
        if state.message:
            lines.append(state.message)
        return lines

    def _log_press(self, fields, state: PageState) -> None:
        try:
            press = self._pressed(fields)
            if not press:
                return
            al.record(
                self.log_surface, f"press {press}", folder=self._folder(fields),
                outcome=f"{state.kind} exit {state.outcome}",
                lines=self._state_lines(state),
            )
        except Exception:  # noqa: BLE001 - the log never changes a page
            return

    def _log_error(self, exc: Exception) -> None:
        """The request's method and path only, never its query: the
        query carries the session token."""
        try:
            al.record(
                self.log_surface, f"{self.command} {urlparse(self.path).path}",
                folder=self._folder(), error=exc,
            )
        except Exception:  # noqa: BLE001
            return

    # --- guards -------------------------------------------------------

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        return host in (f"{HOST}:{self.session.port}", f"[{HOST}]:{self.session.port}")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.session.origin:
            return False
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            return False
        return True

    # --- responses ----------------------------------------------------

    # The id this request composed a document with and has not delivered
    # yet. Set on the way through `_send_page` and consumed by the very
    # next `_send`, which is the one that carries the document out.
    _pending_render: str | None = None

    def _send(self, code: int, body: bytes, ctype: str, extra=()) -> None:
        """One response, with the render id committed after it.

        A document becomes this session's latest here and nowhere else,
        because here is the first moment a browser can be holding it. A
        write that raises — the peer sent an RST mid-write, the reader
        pressed stop, a reload was abandoned before its bytes landed —
        leaves the session where it was, so the document still on screen
        stays current and its genuine close is honored.

        The id is taken before the write rather than after: if this send
        raises, the request's error path sends a refusal through this same
        method, and an id left standing would be committed by that
        refusal instead — the leak this seam exists to close, one step
        further along.
        """
        pending = self._pending_render
        self._pending_render = None
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", REFERRER_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)
        # Whatever the stream buffers, it has to be past this call before
        # the id moves. A raise here is the same aborted write as any
        # other, and it reaches the caller without committing anything.
        self.wfile.flush()
        if pending is not None:
            self.session.commit_render(pending)

    def _refuse(self, code: int, message: str) -> None:
        said = worded(message, surface=self.surface, page=self.page_name)
        self._send(code, (said + "\n").encode("utf-8"), "text/plain; charset=utf-8")

    # --- the three seams a second surface reaches through ---------------
    # A GET's state, a POST's state, and the document that carries either.
    # Everything else on this class — the guards, the tokens, the headers,
    # the close action, the error boundary — is the same code path for any
    # page this server answers, so a subclass overrides these three and
    # inherits the rest rather than restating it.

    def _survey(self) -> PageState:
        return compute_state(self.session)

    def _apply(self, fields) -> PageState:
        session = self.session
        session.scope = _clean((fields.get("scope") or [""])[0], session.scope)
        session.owner = _clean((fields.get("owner") or [""])[0], session.owner)
        # The boxes are read only off a post from a form that drew them,
        # which is what the marker says. The refusal's Check again draws
        # none and carries no marker, so it says nothing about them and
        # what the reader ticked before the refusal stands (setup-web-03).
        if CHOICES_FIELD in fields:
            session.skills = _skill_pairs(fields.get("skill") or [])
            # The one connect box, filtered against the client table the
            # same way the skill names are filtered against the catalog.
            session.connect = (fields.get("connect") or [""])[0] in cn.CLIENTS
        action = (fields.get("action") or ["survey"])[0]
        if action == "mount":
            return perform_mount(session)
        if action == "skills":
            return perform_skills(session)
        return compute_state(session)

    def _render(self, state: PageState, nonce: str) -> str:
        return render_page(self.session, state, nonce=nonce)

    def _send_page(self, state: PageState) -> None:
        nonce = secrets.token_urlsafe(16)
        # Every document goes out stamped with the id its session issued
        # for it, and the id is committed by the send that carries it.
        render_id = self.session.issue_render()
        page = _stamped(self._render(state, nonce), render_id).encode("utf-8")
        self._pending_render = render_id
        # `connect-src 'self'` is what the close beacon needs: without it
        # the policy falls back to `default-src 'none'` and the browser
        # blocks `navigator.sendBeacon`, which reports success anyway, so
        # an abandoned page would leave the server listening. 'self' is
        # this loopback origin and nothing else.
        policy = (
            "default-src 'none'; img-src data:; font-src data:; "
            f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
            "connect-src 'self'; form-action 'self'; base-uri 'none'; "
            "frame-ancestors 'none'"
        )
        self._send(
            200, page, "text/html; charset=utf-8",
            extra=(("Content-Security-Policy", policy),),
        )
        if state.terminal:
            # Signal only once the socket is flushed and closed, so the
            # page always arrives before the process stops.
            self._stop_with = state.outcome
        else:
            self.session.record(state.outcome)

    _stop_with: int | None = None

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            if self._stop_with is not None:
                self.session.finish(self._stop_with)

    # --- routes -------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
        try:
            self._get()
        except Exception as exc:  # noqa: BLE001 - one page must not kill the run
            self._internal(exc)

    def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
        try:
            self._post()
        except Exception as exc:  # noqa: BLE001
            self._internal(exc)

    def _internal(self, exc: Exception) -> None:
        sys.stderr.write(f"{self.surface}: {type(exc).__name__}: {exc}\n")
        self._log_error(exc)
        self._refuse(500, INTERNAL)

    def _get(self) -> None:
        parsed = urlparse(self.path)
        # The token alone already gates every GET; the origin check on
        # top makes a cross-site navigation that somehow carries a valid
        # token refuse the same way a cross-site POST does.
        if not self._host_ok() or not self._origin_ok():
            return self._refuse(403, REFUSE_ORIGIN)
        if parsed.path != self.session.page_path:
            return self._refuse(404, REFUSE_NOT_FOUND)
        query = parse_qs(parsed.query)
        session = self.session
        given = (query.get("s") or [None])[0]
        launch = (query.get("t") or [None])[0]
        if given is not None:
            if not session.valid(given):
                return self._refuse(403, REFUSE_TOKEN)
        elif launch is not None:
            spent = session.spend_launch(launch)
            if spent == LAUNCH_SPENT:
                return self._refuse(403, REFUSE_SPENT)
            if spent != LAUNCH_OK:
                return self._refuse(403, REFUSE_TOKEN)
            # The launch link is spent here and the browser continues on
            # the session token, so a reload of the address bar works.
            return self._send(
                303, b"", "text/plain; charset=utf-8",
                extra=(("Location", f"{session.page_path}?s={session.token}"),),
            )
        else:
            return self._refuse(403, REFUSE_TOKEN)
        self._send_page(self._survey())

    def _read_form(self, length: int) -> str:
        """This POST's body, decoded: the one read a peer can hang up in."""
        return self.rfile.read(length).decode("utf-8", "replace") if length else ""

    def _drained(self, length: int) -> None:
        """Read and discard the body of a post this page will not act on.

        A refusal that leaves the declared body unread closes the
        connection with bytes still arriving, which on Windows is a reset
        (WinError 10053) that can discard the answer already buffered on
        the client. So a refused post is read to its declared length
        first, bounded by what this page ever accepts (tests-02).
        """
        self._read_form(min(max(length, 0), MAX_BODY))

    def _stale_close(self, fields) -> bool:
        """Whether this post is a close from a document already replaced.

        A reload's close is posted by a document the reload's own GET has
        already replaced, and it arrives after that GET was served. So it
        is told apart by the id it carries rather than by when it
        arrives. A close carrying no id is never stale: it is the one the
        desktop shell posts to quiesce this engine and the one a terminal
        probe posts. The token was checked before this is asked, so a
        post that was never entitled to an answer keeps its refusal.
        """
        if (fields.get("action") or [""])[0] != "close":
            return False
        return self.session.stale_render((fields.get(RENDER_FIELD) or [""])[0])

    def _post(self) -> None:
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not self._host_ok() or not self._origin_ok():
            self._drained(length)
            return self._refuse(403, REFUSE_ORIGIN)
        if parsed.path != self.session.page_path:
            self._drained(length)
            return self._refuse(404, REFUSE_NOT_FOUND)
        if length < 0 or length > MAX_BODY:
            return self._refuse(413, "Refused: the form body is too large.")
        fields = parse_qs(self._read_form(length))
        session = self.session
        given = (fields.get("token") or [""])[0]
        if not session.valid(given):
            return self._refuse(403, REFUSE_TOKEN)

        if self._stale_close(fields):
            # A close from a document this session has already replaced —
            # a reload's — is dropped and answered, and the server stands
            # (setup-web-05).
            return self._send(200, b"", "text/plain; charset=utf-8")
        action = (fields.get("action") or ["survey"])[0]
        if action == "close":
            # The page is gone. Whatever outcome it last reported stands.
            self._stop_with = session.outcome
            return self._send(200, b"", "text/plain; charset=utf-8")
        state = self._apply(fields)
        self._log_press(fields, state)
        self._send_page(state)


# --- the command ------------------------------------------------------

OPENED = "radialrig setup: the plan is open in your browser."
NOT_OPENED = "radialrig setup: open this URL to review the plan."
LOCAL_ONLY = (
    "The server is bound to 127.0.0.1, answers this one link, and stops when "
    "setup ends. Ctrl-C stops it now."
)
NO_BROWSER = "No browser opened. Use the URL above."
STOPPED_LINE = "radialrig setup: the local server has stopped."


def run_server(httpd, session: Session, *, on_ready=None) -> int:
    """Serve until the session says it is done. Returns its outcome.

    The lifecycle only, and no output: the caller has already bound the
    server and printed whatever its own surface prints, so what is left is
    the same for every page — start the worker, say ready, wait, stop. A
    Ctrl-C stops the server here and travels on to the caller, which owns
    the line it prints for one.
    """
    worker = threading.Thread(
        target=httpd.serve_forever, name="radialrig-setup", daemon=True,
    )
    worker.start()

    if on_ready is not None:
        on_ready(session)

    try:
        # A poll rather than a bare wait: on Windows a blocking wait can
        # sit through Ctrl-C.
        while not session.done.wait(0.2):
            pass
    finally:
        httpd.shutdown()
        httpd.server_close()
    return session.outcome


def serve(
    root: Path,
    *,
    scope: str | None = None,
    owner: str = mnt.DEFAULT_OWNER,
    open_browser: bool = True,
    on_ready=None,
    stream=None,
) -> int:
    """Run one walkthrough. Returns the exit code of the outcome reached."""
    out = stream if stream is not None else sys.stdout
    root = Path(root).resolve()
    if not root.is_dir():
        sys.stderr.write(f"radialrig: no such directory: {root}\n")
        return report.ERROR

    session = Session(root, scope or mnt.default_scope(root), owner)
    handler = type("BoundSetupHandler", (SetupHandler,), {"session": session})
    try:
        httpd = ThreadingHTTPServer((HOST, 0), handler)
    except OSError as exc:
        sys.stderr.write(f"radialrig: could not start the local server: {exc}\n")
        return report.ERROR
    httpd.daemon_threads = True
    session.port = httpd.server_port
    # The page's own two events (PLAN-0018 phase 2): serving, and
    # stopped. The port and never the URL: the URL carries the launch
    # token.
    al.record(al.SETUP_PAGE, f"started on {HOST}:{session.port}", folder=str(root))

    opened = False
    if open_browser:
        try:
            opened = webbrowser.open(session.url)
        except Exception:  # a headless box has no browser; the URL still works
            opened = False
    out.write(f"{OPENED if opened else NOT_OPENED}\n")
    out.write(f"  {session.url}\n")
    if open_browser and not opened:
        out.write(f"{NO_BROWSER}\n")
    out.write(f"{LOCAL_ONLY}\n")
    out.flush()

    try:
        outcome = run_server(httpd, session, on_ready=on_ready)
    except KeyboardInterrupt:
        out.write("\n")
        outcome = session.outcome
    out.write(f"{STOPPED_LINE}\n")
    out.flush()
    al.record(al.SETUP_PAGE, "stopped", folder=str(root), outcome=f"exit {outcome}")
    return outcome
