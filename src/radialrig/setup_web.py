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
one press: a checkbox per packaged skill, unchecked to begin with. Since
PLAN-0015 phase 3 there is a box per skill and agent, so a person
installs a skill for one agent, the other, or both. The page gains no
behavior the command line lacks — it sequences `mnt.mount` and then the
skill kit's add per ticked pair, which is `radialrig mount` followed by
`radialrig skill add <name> --harness <agent>`.

PLAN-0027 phase 2 draws all of it as one screen, Add a project, which
the app's page serves too: the folder in the heading, two sentences, the
skill grid with a tick box per cell, the Connect box, the one press, and
Details, which holds the two names, the files the press writes and every
command it runs, in order. This module also holds the look every
redesigned screen shares, which the manage page draws its own screens
with.

The process exit code is the outcome the page reached, over both kits:
0 done, 3 a refusal that wrote nothing, 4 nothing to do, 5 something was
left, 1 environment error. The same numbers the command line returns.
"""

from __future__ import annotations

import base64
import html
import re
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
# Every sentence this page says is a line of
# `reports/ui-redesign-2026-11/wording.md`, where a change to a word is
# made first (PLAN-0027). They are here, once, rather than inside the
# builders below. Not one of them names a client, an agent or a skill: a
# client's label arrives from the client table, an agent's from the
# harness map and a skill's name from the catalog, so every line with one
# in it is a template with a slot.

# The masthead's brand, wording.md's shared chrome, the same on every
# page this package serves (PLAN-0027 phase 2; it was "RadialRig Project
# Setup" on this page alone). The endorsement reads on from the name,
# inline, so the masthead says "RadialRig, a Radial 47 product"
# (PLAN-0027 phase 4).
TITLE = "RadialRig"
ENDORSEMENT = ", a Radial 47 product"

# The glyphs the look uses, written as escapes so no editor or checkout
# can swap one for a look-alike.
GLYPH_CHECK = "✓"    # check mark
GLYPH_MINUS = "−"    # minus sign
GLYPH_UP = "↑"       # upwards arrow
GLYPH_STOP = "×"     # multiplication sign
GLYPH_BACK = "←"     # leftwards arrow
MIDDOT = "·"         # middle dot

# The words every redesigned screen shares (wording.md section 2).
DETAILS_LABEL = "Details"
COPY_LABEL = "Copy"
SHOW_COMMANDS = "Show commands"
# A Copy button's accessible name, naming the command it copies: the
# visible text stays the one word, and page.js swaps only that.
COPY_NAME = "Copy: {command}"
BACK_LABEL = "Projects"
CANCEL_LABEL = "Cancel"

# A status's kind, and its mark: a glyph, and the outline the stylesheet
# draws round it. Shape tells them apart; no status has a color of its
# own. The manage page's four project statuses and its gone folder; this
# page uses the same marks for a result line's tone and a stopped setup.
NEEDS_REPAIR = "needs-repair"
UPDATE_READY = "update-ready"
NOT_SET_UP = "not-set-up"
WORKING = "working"
GONE = "gone"
MARKS = {
    NEEDS_REPAIR: ("!", "repair"),
    UPDATE_READY: (GLYPH_UP, "update"),
    NOT_SET_UP: (GLYPH_MINUS, "new"),
    WORKING: (GLYPH_CHECK, "ok"),
    GONE: (GLYPH_STOP, "stopped"),
}

# The result line's three tones, and the mark each takes.
RESULT_DONE = "done"
RESULT_PARTLY = "partly"
RESULT_FAILED = "failed"
RESULT_MARKS = {
    RESULT_DONE: WORKING,
    RESULT_PARTLY: NEEDS_REPAIR,
    RESULT_FAILED: GONE,
}

# A changed path's tag in a result line's Details, by what the engine did
# to it. A row that changed nothing (already as wanted, already gone) is
# not listed.
TAG_ADDED = "added"
TAG_REMOVED = "removed"
TAG_UPDATED = "updated"
TAG_KEPT = "kept"
TAGS = {
    mnt.CREATED: TAG_ADDED,
    mnt.APPENDED: TAG_UPDATED,
    mnt.MERGED: TAG_UPDATED,
    mnt.UPDATED: TAG_UPDATED,
    mnt.RESTORED: TAG_UPDATED,
    mnt.EXCISED: TAG_UPDATED,
    mnt.REMOVED: TAG_REMOVED,
    mnt.LEFT: TAG_KEPT,
}

# The skill grid (wording.md, Screen 3 "Skills"), which the project
# screen and Add a project both draw.
SKILLS_HEAD = "Skills"
ADD_ALL_LABEL = "Add all"
ADD_ALL_NAME = "Add all skills for {agent}"
CELL_ON_LABEL = "On"
CELL_OFF_LABEL = "Off"
CELL_UPDATE_LABEL = "Update"
CELL_NAME = "{skill} for {agent}"
CELL_UPDATE_NAME = "Update {skill} for {agent}"
# A cell the engine won't touch: its reason, and no control.
CELL_FOREIGN = "foreign"
CELL_FILE = "file"
CELL_REASONS = {
    CELL_FOREIGN: "Has a folder RadialRig didn't add",
    CELL_FILE: "A file is in the way",
}

# --- Add a project (wording.md, Screen 4) ------------------------------

# Ready to set up.
SETUP_HEAD = "Set up RadialRig in {project}"
INTRO_ADDS = (
    "RadialRig adds a docs folder with templates, the instructions your "
    "agents read, and its rule lookup and rule editor for {agents}."
)
INTRO_KEEPS = "Your own files stay in place, and you can remove RadialRig later."
SETUP_LABEL = "Set up project"
CHECK_AGAIN_LABEL = "Check again"

# The Connect box. Unticked to begin with (owner, 2026-11-20, issue #62):
# the entries reach every session the client opens, and the sentence
# under the box says so before it is ticked. The sessions it lists are
# the one client's this build knows; a second client moves the list to
# its row of the client table.
CONNECT_BOX = "Connect {label}"
CONNECT_HINT = (
    "Adds a rule lookup and a rule editor to every {label} session: chat, "
    "the Code tab, and local Cowork."
)
# Where the box can't be offered, one sentence in its place says why.
CONNECT_NAME_TAKEN = (
    "{label} already has an entry under this project's name. To connect, "
    "change Project name in Details, then press Refresh."
)
# The same, on a folder that is set up already, whose screen has no
# Project name and no Refresh (PLAN-0027 phase 2 review F3).
CONNECT_NAME_TAKEN_SET_UP = (
    "{label} already has an entry under this project's name, so RadialRig "
    "can't connect it from here. Check Details to see what's in the way."
)
CONNECT_BLOCKED = (
    "RadialRig can't connect {label} from here right now. Check Details to "
    "see what's in the way."
)
CONNECT_NO_PROGRAM = (
    "RadialRig can't connect {label} because it can't find its own rule "
    "lookup program."
)
CONNECT_BLOCKERS_HEAD = "What's in the way of connecting {label}"

# Details on the ready screen: the two names, the files, the commands.
SCOPE_LABEL = "Project name"
OWNER_LABEL = "Owner"
REFRESH_LABEL = "Refresh"
REFRESH_HINT = (
    "After changing a name, press Refresh so the commands and the "
    "connection use it."
)
FILES_HEAD = "Project files"
FILE_ADDS_TO = "adds to your file"
FILE_ALREADY = "already there"
CLIENT_FILES_HEAD = "{label}'s settings"
CLIENT_KEYS = "Adds two entries, {key} and {write_key}, and changes nothing else there."
COMMANDS_HEAD = "Commands"

# A setup that can't start, or stopped: the status over the card.
STOPPED_STATE = "Setup stopped"
BLOCKED_ONE = (
    "1 file in this folder is in the way. Move or clear it, then check "
    "again. Nothing was written."
)
BLOCKED_MANY = (
    "{count} files in this folder are in the way. Move or clear them, then "
    "check again. Nothing was written."
)
BLOCKED_SETTINGS = (
    "RadialRig can't add its entries to {file}, because it isn't valid JSON "
    "or isn't laid out as settings. Fix it, then check again. Nothing was "
    "written."
)
BLOCKERS_HEAD = "What's in the way"
# Details' reason for `.mcp.json`, the page's own (PLAN-0027 phase 2
# review F6). The setup stops on that file when it isn't JSON, when it
# isn't an object, when its servers aren't one, or when it is one file
# on disk with another the setup writes; this says all of it, where the
# command line's reason names the first. That reason stays its own.
MCP_REASON = (
    "this file isn't valid JSON, isn't laid out as settings, or is the same "
    "file on disk as another one setup writes")
INSIDE_SAID = "This folder is already part of {parent}. Nothing was written."
INSIDE_WHY = (
    "RadialRig keeps one set of rules for a project. Keep this folder's "
    "records in a subfolder of that project's docs folders, such as "
    "docs/decisions/{folder}/."
)
INSIDE_WHERE = "That project is at {path}"
OPEN_PROJECT = "Open {parent}"
SKILL_BLOCKED = (
    "Something in this folder is where a skill you ticked would go. Move or "
    "rename it, then check again. Nothing was written."
)
CONNECT_REFUSED = (
    "{label}'s settings are in the way of the connection. Check Details to "
    "see what, then check again. Nothing was written."
)
SETUP_FAILED = (
    "Setup stopped, and nothing was changed. Check Details for what stopped "
    "it."
)
SETUP_PARTIAL = (
    "Setup stopped partway, and RadialRig couldn't put everything back. Some "
    "files in this folder may be left changed. Check Details for the list."
)

# A folder that is already set up, opened from `radialrig setup`: it is
# offered what it doesn't have yet.
ALREADY_OFFERS = (
    "RadialRig is already set up in this folder. Add what it doesn't have "
    "yet."
)
ALREADY_COMPLETE = (
    "RadialRig is already set up in this folder, and there's nothing left to "
    "add from here."
)
ADD_LABEL = "Add to this project"
# The one press of a set-up project that has every skill: its name is the
# box's own, `CONNECT_BOX`.
NOT_CONNECTED = "Not connected"

# The result lines. `{skills}` is `SKILLS_ONE` or `SKILLS_MANY`, counting
# each skill once whichever agents it was added for, the way a row of
# Projects counts them.
SKILLS_ONE = "1 skill"
SKILLS_MANY = "{count} skills"
SET_UP = "Set up RadialRig in {project}."
SET_UP_SKILLS = (
    "Set up RadialRig in {project} and added {skills}. {pick_up}")
SET_UP_CONNECTED = (
    "Set up RadialRig in {project} and connected {label}. Fully quit and "
    "reopen {label} to use the connection.")
SET_UP_BOTH = (
    "Set up RadialRig in {project}, added {skills}, and connected {label}. "
    "{both}")
PICK_UP_ONE = "Start a new agent session to pick it up."
PICK_UP_MANY = "Start a new agent session to pick them up."
BOTH_ONE = (
    "Start a new agent session for the skill, and fully quit and reopen "
    "{label} to use the connection.")
BOTH_MANY = (
    "Start a new agent session for the skills, and fully quit and reopen "
    "{label} to use the connection.")
# A setup whose first step ran and whose skills or connection did not
# (PLAN-0027 phase 2 prescription 4): the line says what was set up and
# what wasn't. A line saying nothing of a step was changed stands only
# where that step refused, or failed and put back everything it wrote.
SET_UP_NO_SKILLS = (
    "Set up RadialRig in {project}, but couldn't add the skills you ticked. "
    "None of them was added. Check Details for what stopped them.")
SET_UP_SKILLS_LEFT = (
    "Set up RadialRig in {project}, but couldn't finish adding the skills "
    "you ticked. Some of their files may be left. Check Details for the "
    "list.")
NOT_CONNECTED_EITHER = "{label} wasn't connected."
SET_UP_NO_CONNECT = (
    "Set up RadialRig in {project}, but couldn't connect {label}. Its "
    "settings weren't changed. Check Details for what stopped it.")
SET_UP_CONNECT_LEFT = (
    "Set up RadialRig in {project}, but couldn't finish connecting {label}. "
    "Some of its settings may be left changed. Check Details for the list.")
SET_UP_SKILLS_NO_CONNECT = (
    "Set up RadialRig in {project} and added {skills}, but couldn't connect "
    "{label}. Its settings weren't changed. {pick_up}")
SET_UP_SKILLS_CONNECT_LEFT = (
    "Set up RadialRig in {project} and added {skills}, but couldn't finish "
    "connecting {label}. Some of its settings may be left changed. Check "
    "Details for the list.")
# The same, for a project that was already set up.
ADDED = "Added {skills}. {pick_up}"
ADDED_CONNECTED = "Added {skills} and connected {label}. {both}"
ADDED_NO_CONNECT = (
    "Added {skills}, but couldn't connect {label}. Its settings weren't "
    "changed. {pick_up}")
ADDED_CONNECT_LEFT = (
    "Added {skills}, but couldn't finish connecting {label}. Some of its "
    "settings may be left changed. Check Details for the list.")
NOTHING_ADDED = "Those skills were already there. Nothing was changed."
SKILLS_FAILED = "Couldn't add the skills. Nothing was changed."
SKILLS_STOPPED = (
    "Couldn't finish adding the skills. Some of their files may be left "
    "changed. Check Details for the list.")
# A ticked skill whose copy was edited after the screen was drawn: the
# engine leaves it alone, and the line says so after whatever it added.
KEPT_EDITED = "Left a skill copy you edited as it was."
# The connection alone (wording.md, Screen 3's lines for the same press).
CONNECTED_SAID = (
    "Connected {label}. Fully quit and reopen {label} to use the connection.")
CONNECT_FAILED_SAID = "Couldn't connect {label}. Nothing was changed."
CONNECT_STOPPED_SAID = (
    "Couldn't finish connecting {label}. Some of its settings may be left "
    "changed. Check Details for the list.")
# After a press that connected while the client was open (BUG-0018). This
# page has no Repair, so the line names where one is.
RUNNING_AFTER_SETUP = (
    "{label} is running and may undo this change. Fully quit it and reopen "
    "it; if the connection is gone, open this project in RadialRig and press "
    "Repair.")

# What a finished page says about its server. `radialrig setup` stops its
# server once a press finishes it; the app's page goes on standing and
# says none of these.
STOPPED = (
    "Setup is finished and the local server has stopped. You can close this page."
)
CLOSED = "The local server has stopped. You can close this page."
CLOSED_RETRY = (
    "The local server has stopped. You can close this page. To try again, "
    "start setup again."
)
# A stop the engine's own words say more about than the screen does.
# The writers put back what they can on a file error and raise their
# kit's own error, whose sentence names what could not be put back; the
# page's outcome follows that sentence (PLAN-0016 phase 4, made exact by
# PLAN-0017 phase 2). The manage page's own stop bodies still say this.
PARTIAL_HEAD = "RadialRig stopped part way, and some of what it wrote may be left."
# After a skill fault on the mount press the ticked connection is not
# attempted. This sentence rides in the state's message for the activity
# log; the screen says the same in its own result line.
CONNECT_NOT_ATTEMPTED = "The registration was not attempted after that step."
# How the engine ends a file error's sentence when nothing is left
# changed (`mnt.stopped_sentence`), and how Details ends it instead
# (PLAN-0027 phase 2 review F7).
ENGINE_NO_WRITE = "; nothing was written"
PAGE_NO_CHANGE = "; nothing was changed"

# The command lines a press runs, as the command line spells them. The
# skill line is the one the manage page's command table composes, and so
# is the connect line.
SKILL_ADD_LINE = "radialrig skill add {name} {root} --harness {harness}"
CONNECT_LINE = "radialrig connect {client} {root}"
MOUNT_LINE = "radialrig mount {root}"

# A checkbox's posted value: the agent's map key and the skill's catalog
# name, joined once. The page looks both halves up before anything
# downstream reads them (`_skill_pairs`), so the join is a spelling for
# the form and never a path: a value the map or the catalog does not
# know is dropped whole.
BOX_SEP = "/"

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

# Which refusal a CONFLICT state is. Each has its own sentence and its
# own next step on the screen, so the state says which it is rather than
# leaving the screen to read it back out of the report's lines.
WHY_FILES = "files"        # files in the way of the setup
WHY_INSIDE = "inside"      # the folder is inside a set-up project
WHY_SKILL = "skill"        # a ticked skill's place is taken
WHY_CONNECT = "connect"    # the client's settings block a ticked connection

# The steps of a press that did not land, after an earlier one did.
MISSED_SKILLS = "skills"
MISSED_CONNECT = "connect"


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
    # off a page that has no client behind it. After a press it is the
    # client the press connected, or one whose settings a connection that
    # stopped partway left changed, with no rows (PLAN-0027 phase 2
    # review F2).
    connect: tuple = ()
    client: object | None = None
    # What the screen needs to say a refusal or a partial result in its
    # own words (PLAN-0027 phase 2). None of it changes what a press
    # does: `why` is which refusal a CONFLICT state is, `parent` the
    # set-up project a refused folder is inside, `withheld` the rows of a
    # connection the plan refused while the setup itself stands, `missed`
    # the steps of a press that did not land after an earlier one did,
    # and `left` whether the step that stopped could not put back
    # everything it wrote.
    why: str = ""
    parent: object | None = None
    withheld: tuple = ()
    missed: tuple = ()
    left: bool = False


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
        # Unticked to begin with, like the skills boxes (owner, 2026-11-20,
        # issue #62): the client's entries reach every session it opens,
        # not just this project's, so the reader opts in after reading the
        # sentence that says so.
        self.connect: bool = False
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


def _connect_plan(session: Session, client, *, drawn: bool = False) -> tuple:
    """What the offer would write. Writes nothing.

    The same `connect_plan` the command line's --dry-run calls, given the
    scope the page is about to mount with: at review time there is no
    mount record to read one from.

    A press plans the connection only where its box is ticked. With
    `drawn`, the screen plans it as it is drawn, ticked or not, so a
    refusal stands in the box's place before any press rather than after
    one (PLAN-0027 phase 2 review F1); the rows come back only where the
    box is ticked, as they always have.
    """
    if client is None or not (session.connect or drawn):
        return ()
    rows = cn.connect_plan(session.root, client.name, scope=session.scope).dispositions
    return rows if session.connect else ()


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

    A stop that left one of the client's own settings files changed
    still names the client, with no rows, so the after-press sentence
    can ask whether it is open (PLAN-0027 phase 2 review F2). No line
    claims the connection from that: the rows are what a claim reads.
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
        return (), _settings_left(client, exc), str(exc), stopped_outcome(exc)
    except cn.ConnectError as exc:
        # The offer was planned before the mount ran. Anything that
        # refuses it now arrived between the two, and a mount that landed
        # is not undone by it.
        return (), None, str(exc), report.OK
    if result.wrote:
        session.record(report.OK, consequential=True)
    ro.record_seen(session.root)
    return result.dispositions, client, "", report.OK


def _settings_left(client, exc):
    """The client, where a connection that stopped left one of its
    settings files changed: a path its writer could not put back is one
    of the files `connect` wrote to. Else None, as for a stop that left
    only the project's own record."""
    try:
        files = {Path(target.path) for target in cn.detect(client)}
    except (cn.ConnectError, OSError):
        return None
    return client if any(Path(path) in files for path in exc.left) else None


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
        why=WHY_CONNECT,
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
        why=WHY_SKILL,
    )


def _mount_conflict_state(exc) -> PageState:
    return PageState(
        CONFLICT, report.REFUSED, False,
        dispositions=report.conflict_dispositions(exc.conflicts),
        lines=tuple(report.conflict_report(exc.conflicts)),
        why=WHY_FILES,
    )


def _inside_mounted_state(exc) -> PageState:
    """A mount refused inside a mounted project, through the same CONFLICT
    state: the command line's head, no table, since no path is in the way,
    and its remedy as the note. No control forces past it."""
    return PageState(
        CONFLICT, report.REFUSED, False,
        lines=tuple(report.inside_mounted_report(exc.root, exc.parent)),
        message=report.inside_mounted_remedy(exc.root, exc.parent),
        why=WHY_INSIDE, parent=exc.parent,
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
    withheld: tuple = ()
    try:
        connect = _connect_plan(session, client, drawn=True)
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        # A file error while the offer is still planned: nothing was
        # written, and the page says so rather than dropping the offer
        # without a word. An unticked box leaves the offer as it stood:
        # the press plans it again once it is ticked, and stops there.
        if session.connect:
            return PageState(FAILED, report.ERROR, True, message=str(exc))
        connect = ()
    except cn.ConnectRefused as exc:
        # A refused registration withdraws the offer; it never takes the
        # mount button with it. The connect is an offer, never a
        # precondition, so the page says why it is not on the table and
        # leaves the press that brought the reader here alone.
        client, connect = None, ()
        withheld = tuple(exc.conflicts)
        notice = report.connect_withheld_notice(
            exc.client.name, session.root, exc.conflicts)
    except cn.ConnectError:
        # Any other failure to plan it takes a ticked box off the screen;
        # an unticked one stands as it did before the screen planned it.
        if session.connect:
            client = None
        connect = ()
    try:
        if mnt.is_mounted(root):
            manifest = mnt.read_manifest(root)
            # A mounted project used to be the end of the walkthrough.
            # It still is, unless the screen draws a tick box for a skill
            # this project does not already have, or a client on this
            # machine is not registered with it: then there is one more
            # thing the page can do, and stopping would hide it. A skill
            # whose place is taken draws no box, so it keeps nothing
            # standing (PLAN-0027 phase 2 review F4).
            return PageState(
                ALREADY, report.NOOP,
                not _ticks(session, catalog) and client is None,
                lines=tuple(report.already_mounted_report(root, manifest)),
                skills=skills, catalog=catalog, connect=connect, client=client,
                message=notice, withheld=withheld,
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
        message=notice, withheld=withheld,
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
        missed = (MISSED_SKILLS,)
        if failed:
            message += " " + failed
            missed += (MISSED_CONNECT,)
        elif client is not None and session.connect:
            message += " " + CONNECT_NOT_ATTEMPTED
            missed += (MISSED_CONNECT,)
        return PageState(
            MOUNTED, report.PARTIAL, True,
            dispositions=result.dispositions(),
            lines=tuple(report.mount_report(result)),
            result=result, message=message,
            missed=missed, left=stopped_outcome(exc) == report.PARTIAL,
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
        missed=(MISSED_CONNECT,) if (failed or error) else (),
        left=stopped == report.PARTIAL,
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
            # outcome follows it. The connection, where one was ticked,
            # is not attempted after it.
            code = stopped_outcome(exc)
            return PageState(
                FAILED, code, True, message=str(exc),
                missed=(MISSED_SKILLS,) + ((MISSED_CONNECT,) if offered else ()),
                left=code == report.PARTIAL)
    connect, used, error, stopped = _connect_checked(session, client)
    failed = failed or error
    missed = (MISSED_CONNECT,) if failed else ()
    if skills:
        return PageState(SKILLED, max(outcome, stopped), True, skills=skills,
                         added=added, connect=connect, client=used, message=failed,
                         missed=missed, left=stopped == report.PARTIAL)
    if failed or not connect:
        # Nothing to claim: the registration was the whole press and it
        # did not run, or stopped part way through its write. The head
        # says which, and the engine's own sentence says why.
        return PageState(CONNECT_FAILED, stopped or report.ERROR, True, message=failed,
                         client=used, missed=(MISSED_CONNECT,),
                         left=stopped == report.PARTIAL)
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

    Drawn by the manage page's plan and result tables, which stay until
    PLAN-0027 phase 3 rebuilds them. Outlined rather than filled: the
    screen's one amber is the press that writes.
    """
    touches = verb in (mnt.MERGED, mnt.APPENDED, mnt.CURRENT, mnt.LEFT)
    cls = "chip touches" if touches else "chip"
    return f'<span class="{cls}">{_esc(verb)}</span>'


# The wrote-table's sentences for the codes the engine leaves empty.
# On the command line "created <path>" is a whole line and needs no
# tail; in a three-column table the empty cell reads as a hole. The page
# fills the cell, and the engine's own wording stays empty for the
# command line. The record rows say what the record is for.
WROTE_REASONS: dict[str, str] = {
    mnt.SKILL_ADDED:
        "one new skill folder; every file in it is this package's",
    mnt.SKILL_RECORD_WRITTEN:
        "the record RadialRig uses to take everything back out",
    mnt.CONNECT_RECORD_WRITTEN:
        "the record RadialRig uses to take the connection back out",
}


def _worded(d, resolve) -> str:
    """A row's sentence: the engine's wording, or this table's sentence
    for a code the engine leaves wordless.

    `resolve` is the engine resolver the caller's rows belong to. The
    manage page passes the orphan kit's, which reads the mount kit's
    beneath it.
    """
    return resolve(d) or WROTE_REASONS.get(d.code, "")


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
    """The command line that adds one skill for one agent: the same line
    the manage page's command table composes for the same press."""
    return SKILL_ADD_LINE.format(
        name=quoted(name), root=quoted(str(root)), harness=quoted(harness))


def connect_line(client_name: str, root) -> str:
    """The command line that connects one client to one project."""
    return CONNECT_LINE.format(client=quoted(client_name), root=quoted(str(root)))


def mount_line(session: Session) -> str:
    """`radialrig mount`, carrying whatever the page's own fields changed.

    The two names are fields a reader can edit, so the command names them
    whenever they are not the ones the bare command would have used. Every
    slot goes through `quoted`: the project name starts as the folder's
    own name, so a folder with a space in it would otherwise put one into
    this line twice. A slot no quoting rule makes safe leaves no line at
    all (setup-web-04).
    """
    try:
        line = MOUNT_LINE.format(root=quoted(str(session.root)))
        if session.scope != mnt.default_scope(session.root):
            line += f" --scope {quoted(session.scope)}"
        if session.owner != mnt.DEFAULT_OWNER:
            line += f" --owner {quoted(session.owner)}"
    except Unquotable:
        return ""
    return line


def _command(text: str, *, name: str = "") -> str:
    """The command frame the manage page's plan and result pages draw:
    the line, and the button that copies it. `name` is the Copy button's
    accessible name (PLAN-0014 phase 2)."""
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

    Only the ready and already-set-up forms draw them, so only those two
    carry this; a refusal's form posts without it, and `_apply` then
    leaves the boxes as the reader left them.
    """
    return f'<input type="hidden" name="{CHOICES_FIELD}" value="1">'


# --- the look every redesigned screen shares (PLAN-0027) ---------------
# The pieces Projects, one project and Add a project are all drawn from:
# a status as a mark and a word, a line a reader may copy, the one switch
# for a screen's commands, the result line, and the skill grid's frame.
# The manage page draws its screens with these same functions, so the two
# pages cannot drift apart in look.

def symbol(kind: str) -> str:
    """A status's mark: a glyph in an outline, found by shape and never
    by color, and hidden from a reader by ear because the words beside it
    say the same thing."""
    glyph, shape = MARKS[kind]
    return f'<span class="symbol {shape}" aria-hidden="true">{glyph}</span>'


def state_mark(kind: str, said: str) -> str:
    """A status as its mark and its words."""
    return f'<span class="state">{symbol(kind)}<span>{_esc(said)}</span></span>'


def copy_line(text: str) -> str:
    """One line a reader may copy, with the button that copies it."""
    return (
        f'<div class="copy-line"><code>{_esc(text)}</code>'
        f'<button class="copy" type="button" data-copy="{_attr(text)}" '
        f'aria-label="{_attr(COPY_NAME.format(command=text))}">'
        f"{_esc(COPY_LABEL)}</button></div>"
    )


def show_commands() -> str:
    """The one switch a screen carries for its commands. The stylesheet
    reads the box, so it works with no script."""
    return (
        '<label class="check"><input type="checkbox" class="show-commands">'
        f"{_esc(SHOW_COMMANDS)}</label>"
    )


def result_line(tone: str, sentence: str, *, warning: str = "",
                changed=(), notes=(), more: str = "") -> str:
    """The one line a press answers with, at the top of the screen it was
    made on (PLAN-0027 phase 1 prescription 6).

    `tone` is `RESULT_DONE`, `RESULT_PARTLY` or `RESULT_FAILED`, and picks
    the mark. `sentence` is the line from `wording.md`. `warning` is the
    after-press sentence for a press that wrote the client's settings
    while it was open. Details opens on `notes`, each a sentence, on
    `more`, markup such as a line to copy, and on `changed`, each a path
    or a `(path, tag)` pair such as `("docs/plan.md", "updated")`; with
    none of them there is no Details.
    """
    said = f"<p>{_esc(sentence)}</p>"
    if warning:
        said += f'<p class="warn">{_esc(warning)}</p>'
    items = "".join(f"<p>{_esc(note)}</p>" for note in notes) + more
    rows = []
    for item in changed:
        path, tag = (item, "") if isinstance(item, str) else item
        shown = _esc(mnt.one_line(str(path)))
        rows.append(
            f'<li><code>{shown}</code>'
            + (f" {MIDDOT} {_esc(tag)}" if tag else "") + "</li>")
    if rows:
        items += f'<ul>{"".join(rows)}</ul>'
    details = (
        f"<details><summary>{_esc(DETAILS_LABEL)}</summary>"
        f'<div class="drawer">{items}</div></details>'
    ) if items else ""
    return (
        f'<div class="result {_attr(tone)}" role="status" aria-live="polite">'
        f'{symbol(RESULT_MARKS.get(tone, WORKING))}<div class="words">{said}</div>'
        f"{details}</div>"
    )


def blocked_above(root, s: sk.SkillState) -> bool:
    """Whether a file stands where a folder on the way to the skill's own
    folder goes, such as the agent's skills folder: the engine refuses to
    add the skill there, as it refuses a file at the skill's folder."""
    base = Path(root)
    parts = s.dir_rel.strip("/").split("/")
    for i in range(1, len(parts)):
        here = base.joinpath(*parts[:i])
        try:
            if here.exists() and not here.is_dir():
                return True
        except OSError:
            return True
    return False


def grid_head(label: str, press: str = "") -> str:
    """One agent's column head of the skill grid: its label, and the
    column's own press beside it where it has one."""
    return (f'<th scope="col"><span class="agent-head">'
            f"<span>{_esc(label)}</span>{press}</span></th>")


def grid_row(name: str, summary: str, cells: str, *, cls: str = "") -> str:
    """One skill's row of the grid: its name and its one line, once, then
    the cells, one per agent."""
    opened = f'<tr class="{cls}">' if cls else "<tr>"
    return (
        f'{opened}<th scope="row"><span class="skill-info"><strong>{_esc(name)}</strong>'
        f"<small>{_esc(summary)}</small></span></th>{cells}</tr>"
    )


def skill_grid(heads: str, body: str) -> str:
    """The skill grid: one row per skill, one column per agent in the
    harness map's order. Both screens that list skills draw this table."""
    return (
        '<table class="skill-grid"><thead><tr>'
        f'<th scope="col"><h2>{_esc(SKILLS_HEAD)}</h2></th>{heads}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


# --- Add a project (PLAN-0027 phase 2) ---------------------------------
# One screen, served by `radialrig setup` and, inside the app, by the
# manage page: a heading naming the folder, two sentences, the skill
# grid, the Connect box, the one amber press, and Details. A folder that
# can't be set up says why on a card, and a press answers with one result
# line. The bodies below draw every state of it; the page around them
# hands in what is its own to add.

@dataclass(frozen=True)
class Frame:
    """What the page around this screen adds to it.

    `radialrig setup` serves the screen bare: leaving the page is its way
    out, and its server stops once a press finishes. The app's page
    stands, so it hands in the way back to Projects and the press that
    opens another project's screen.
    """

    # A GET form, carrying the id `LEAVE_ID`, that leads back to Projects.
    # The screen's back link and Cancel submit it by that id, since they
    # stand inside the screen's own form and a form cannot hold another.
    leave: str = ""
    # Whether the server goes on standing after a finished state.
    stands: bool = False
    # (folder: Path, label: str) -> the press that opens that folder's
    # project screen, or None where there is no such screen to open.
    opener: object | None = None
    # The after-press sentence for a connect made while the client is
    # open, a template with `{label}`.
    running_after: str = RUNNING_AFTER_SETUP


PLAIN = Frame()
LEAVE_ID = "leave"


def _project_word(root) -> str:
    """The folder's own name, or the whole path for a drive root."""
    return Path(root).name or str(root)


def _agents_said() -> str:
    """The agents' labels as one phrase, from the harness map."""
    labels = [harness.label for harness in sk.HARNESSES.values()]
    if len(labels) < 3:
        return " and ".join(labels)
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _skills_said(count: int) -> str:
    return SKILLS_ONE if count == 1 else SKILLS_MANY.format(count=count)


def _name_head(session: Session, said: str) -> str:
    """The screen's heading, with the folder's path beside it."""
    return (
        f'<div class="project-name"><h1>{_esc(said)}</h1>'
        f'<span class="path">{_esc(session.root)}</span></div>'
    )


def _back(frame: Frame) -> str:
    """The way back to Projects, where the page around the screen has one."""
    if not frame.leave:
        return ""
    return (
        f'<button class="link back" type="submit" form="{LEAVE_ID}">'
        f'<span aria-hidden="true">{GLYPH_BACK}</span>{_esc(BACK_LABEL)}</button>'
    )


def _cancel(frame: Frame) -> str:
    if not frame.leave:
        return ""
    return (
        f'<button class="link quiet" type="submit" form="{LEAVE_ID}">'
        f"{_esc(CANCEL_LABEL)}</button>"
    )


def _closing(state: PageState, frame: Frame, said: str) -> str:
    """What a finished page says about its server, where it has stopped."""
    if frame.stands or not state.terminal:
        return ""
    return f'<p class="closing">{_esc(said)}</p>'


def _is_set_up(session: Session) -> bool:
    try:
        return mnt.is_mounted(session.root)
    except OSError:
        return False


# --- the grid, as tick boxes ------------------------------------------

def _face(cls: str, glyph: str, label: str) -> str:
    return (f'<span class="face {cls}" aria-hidden="true">'
            f"<span>{glyph}</span>{_esc(label)}</span>")


def _setup_cell(session: Session, s: sk.SkillState) -> tuple[str, str]:
    """One agent's cell for one skill on this screen, and the box's key
    where the cell is a box.

    The screen adds and never removes, so a skill the folder already has
    for that agent reads On and is no control. One it lacks is a tick box
    that reads Off until ticked; a clean copy the package has moved past
    is a box that reads Update. A place the engine would refuse to add
    into reads as its reason, as on the project screen.
    """
    agent = s.harness.label
    if s.dir_is_file or (s.record_entry is None and blocked_above(session.root, s)):
        return f'<span class="cell-note">{_esc(CELL_REASONS[CELL_FILE])}</span>', ""
    if s.foreign:
        return f'<span class="cell-note">{_esc(CELL_REASONS[CELL_FOREIGN])}</span>', ""
    if s.current or s.edited or (s.record_entry is not None and s.foreign_files):
        return (
            f'<span class="face on has"><span aria-hidden="true">{GLYPH_CHECK}</span>'
            f"{_esc(CELL_ON_LABEL)}</span>", "")
    update = sk.behind(s)
    key = box_value(s.harness.name, s.name)
    ticked = (s.harness.name, s.name) in session.skills
    name = (CELL_UPDATE_NAME if update else CELL_NAME).format(skill=s.name, agent=agent)
    off = (_face("update unticked", GLYPH_UP, CELL_UPDATE_LABEL) if update
           else _face("off unticked", GLYPH_MINUS, CELL_OFF_LABEL))
    on = _face("on ticked", GLYPH_CHECK, CELL_UPDATE_LABEL if update else CELL_ON_LABEL)
    return (
        '<label class="toggle"><input type="checkbox" name="skill" '
        f'value="{_attr(key)}" data-agent="{_attr(s.harness.name)}" '
        f'data-shows="{_attr(key)}" aria-label="{_attr(name)}"'
        f'{" checked" if ticked else ""}>{off}{on}</label>', key)


def _setup_grid(session: Session, catalog) -> tuple[str, tuple]:
    """The skill grid for this screen, and the boxes it drew, in the
    order a press adds them: agent by agent in map order, skill by skill
    in catalog order.

    The same table the project screen draws. There a cell is a press that
    runs at once; here it is a tick box, because nothing is written until
    the screen's one press. Add all ticks its column and writes nothing;
    the script wires it, so it ships hidden and a page with no script
    shows no dead control.
    """
    if not catalog:
        return "", ()
    harnesses = tuple(sk.HARNESSES.values())
    by = {(s.harness.name, s.name): s for s in catalog}
    names: list[str] = []
    for s in catalog:
        if s.name not in names:
            names.append(s.name)
    drawn: dict[tuple[str, str], tuple[str, str]] = {
        pair: _setup_cell(session, s) for pair, s in by.items()}
    body = ""
    for name in names:
        cells = "".join(
            f"<td>{drawn[(h.name, name)][0] if (h.name, name) in drawn else ''}</td>"
            for h in harnesses)
        summary = next(
            (by[(h.name, name)].packaged.summary for h in harnesses
             if (h.name, name) in by), "")
        body += grid_row(name, summary, cells)
    heads = ""
    boxes = []
    for harness in harnesses:
        column = [by[(harness.name, name)] for name in names
                  if drawn.get((harness.name, name), ("", ""))[1]]
        boxes.extend(column)
        press = (
            f'<button class="link" type="button" data-all="{_attr(harness.name)}" '
            f'aria-label="{_attr(ADD_ALL_NAME.format(agent=harness.label))}" hidden>'
            f"{_esc(ADD_ALL_LABEL)}</button>"
        ) if column else ""
        heads += grid_head(harness.label, press)
    return skill_grid(heads, body), tuple(boxes)


def _ticks(session: Session, catalog) -> bool:
    """Whether the grid draws any tick box: the cells `_setup_grid` draws
    as boxes, asked the same way, so a server stops exactly where its
    screen offers no skill to add."""
    return any(_setup_cell(session, s)[1] for s in catalog)


# --- the Connect box ---------------------------------------------------

@dataclass(frozen=True)
class _Offer:
    """The connection, as this screen offers or withholds it."""

    client: object | None = None   # the client the press would connect
    box: str = ""                  # what the screen shows for it
    files: str = ""                # what Details lists for it
    key: str = ""                  # the box's key, for what follows it
    shown: bool = False            # whether what follows it stands now


def _connect_key(client) -> str:
    return f"connect:{client.name}"


def _running_before(client, paths) -> str:
    """The before-press sentence while the client is open (BUG-0018)."""
    if not cn.is_running(client, paths):
        return ""
    return f'<p class="warn">{_esc(report.client_running_before(client))}</p>'


def _connect_gap(session: Session) -> str:
    """Why no box stands, where the reason is one a person can act on:
    the client is on this computer, the folder is not connected, and
    RadialRig cannot find its own rule lookup program. A computer with no
    such client, or a folder already connected, is told nothing."""
    try:
        client = cn.client_for(cn.DEFAULT_CLIENT)
        if not cn.detect(client) or cn.is_connected(session.root, client.name):
            return ""
    except (cn.ConnectError, OSError):
        return ""
    try:
        cn.read_command()
    except cn.ConnectError:
        return CONNECT_NO_PROGRAM.format(label=client.label)
    except OSError:
        return ""
    return ""


def _withheld(state: PageState) -> tuple[str, str]:
    """A connection the engine's plan refused when the screen was drawn:
    the sentence that stands in the box's place, and what Details lists.
    The setup itself is not held back by it."""
    client = cn.client_for(cn.DEFAULT_CLIENT)
    codes = {d.code for d in state.withheld}
    taken = CONNECT_NAME_TAKEN_SET_UP if state.kind == ALREADY else CONNECT_NAME_TAKEN
    said = taken if mnt.CONNECT_KEY_HOST_OWNED in codes else CONNECT_BLOCKED
    rows = "".join(
        f"<li>{_esc(mnt.one_line(d.path))}: "
        f"{_esc(mnt.one_line(mnt.reason_text(d) or ''))}</li>"
        for d in state.withheld)
    files = (
        f"<h3>{_esc(CONNECT_BLOCKERS_HEAD.format(label=client.label))}</h3>"
        f"<ul>{rows}</ul>"
    ) if rows else ""
    return f'<p class="said">{_esc(said.format(label=client.label))}</p>', files


def _offer(session: Session, state: PageState, *, press: bool = False) -> _Offer:
    """The Connect box, unticked to begin with, its sentence under it, and
    the before-press sentence while the client is open.

    With `press`, the offer is the screen's whole press: a set-up project
    with every skill is here for the connection, so it gets the client's
    row with Connect in it, and the form carries the box's field already
    set. The posted value is filtered against the client table before
    anything downstream reads it.
    """
    client = state.client
    if client is None:
        if state.withheld:
            said, files = _withheld(state)
            return _Offer(box=f'<div class="offer">{said}</div>', files=files)
        gap = _connect_gap(session)
        box = f'<div class="offer"><p class="said">{_esc(gap)}</p></div>' if gap else ""
        return _Offer(box=box)
    try:
        targets = cn.detect(client)
        key = cn.entry_key(client, session.scope)
        write_key = cn.write_entry_key(client, session.scope)
    except (cn.ConnectError, OSError):
        return _Offer()
    paths = [target.path for target in targets]
    hint = f'<p class="hint">{_esc(CONNECT_HINT.format(label=client.label))}</p>'
    name = CONNECT_BOX.format(label=client.label)
    box_key = _connect_key(client)
    if press:
        box = (
            '<div class="offer one-press">'
            f'<input type="hidden" name="connect" value="{_attr(client.name)}">'
            f'<div class="link"><strong>{_esc(client.label)}</strong>'
            + state_mark(NOT_SET_UP, NOT_CONNECTED)
            + '<button class="primary" type="submit" name="action" value="skills">'
            + f"{_esc(name)}</button></div>"
            + hint + _running_before(client, paths) + "</div>"
        )
    else:
        checked = " checked" if session.connect else ""
        box = (
            '<div class="offer">'
            '<label class="check"><input type="checkbox" name="connect" '
            f'value="{_attr(client.name)}" data-shows="{_attr(box_key)}"{checked}>'
            f"{_esc(name)}</label>"
            + hint + _running_before(client, paths) + "</div>"
        )
    shown = session.connect or press
    files = (
        f"<div{_follows((box_key,), shown)}>"
        f"<h3>{_esc(CLIENT_FILES_HEAD.format(label=client.label))}</h3>"
        f"<p>{_esc(CLIENT_KEYS.format(key=key, write_key=write_key))}</p><ul>"
        + "".join(f"<li><code>{_esc(path)}</code></li>" for path in paths)
        + "</ul></div>"
    )
    return _Offer(client=client, box=box, files=files, key=box_key, shown=shown)


# --- Details -----------------------------------------------------------

def _follows(keys, shown: bool) -> str:
    """The attributes of a line that stands only while one of its boxes
    is ticked. The server draws it as the last post left the boxes; the
    script keeps it in step with them from there."""
    return f' data-for="{_attr(" ".join(keys))}"' + ("" if shown else " hidden")


FILE_TAGS = {
    mnt.APPENDED: FILE_ADDS_TO,
    mnt.MERGED: FILE_ADDS_TO,
    mnt.CURRENT: FILE_ALREADY,
}


def _file_items(session: Session, state: PageState, boxes, offer: _Offer) -> str:
    """Every file the press writes into the folder: the setup's own, then
    each ticked skill's folder and its agent's record, then the
    connection's record. A skill's lines and the connection's stand only
    while their boxes are ticked."""
    items = ""
    for d in state.dispositions:
        tag = FILE_TAGS.get(d.verb)
        items += (
            f"<li><code>{_esc(mnt.one_line(d.path))}</code>"
            + (f" {MIDDOT} {_esc(tag)}" if tag else "") + "</li>")
    ticked = set(session.skills)
    for harness in sk.HARNESSES.values():
        mine = [s for s in boxes if s.harness.name == harness.name]
        for s in mine:
            key = box_value(harness.name, s.name)
            items += (
                f"<li{_follows((key,), (harness.name, s.name) in ticked)}>"
                f"<code>{_esc(s.dir_rel)}/</code></li>")
        if mine:
            keys = [box_value(harness.name, s.name) for s in mine]
            any_ticked = any((harness.name, s.name) in ticked for s in mine)
            items += (
                f"<li{_follows(keys, any_ticked)}>"
                f"<code>{_esc(harness.record)}</code></li>")
    if offer.key:
        items += (
            f"<li{_follows((offer.key,), offer.shown)}>"
            f"<code>{_esc(cn.CONNECT_REL)}</code></li>")
    if not items:
        return ""
    return f'<h3>{_esc(FILES_HEAD)}</h3><ul class="files">{items}</ul>'


def press_commands(session: Session, state: PageState, boxes=None,
                   client=None) -> tuple[tuple[str, tuple, bool], ...]:
    """Every command the screen's press could run, in the order it runs
    them: the setup, then a line per skill box, then the connection.

    Each is `(line, keys, shown)`: the boxes it follows, and whether the
    last post left one of them ticked. A line with no key always runs. A
    command with no safe spelling is left out rather than drawn as an
    empty frame (setup-web-04).
    """
    out: list[tuple[str, tuple, bool]] = []
    if state.kind == REVIEW:
        out.append((mount_line(session), (), True))
    ticked = set(session.skills)
    if boxes is None:
        boxes = _setup_grid(session, state.catalog)[1]
    for s in boxes:
        pair = (s.harness.name, s.name)
        try:
            line = skill_add_line(s.harness.name, s.name, session.root)
        except Unquotable:
            line = ""
        out.append((line, (box_value(*pair),), pair in ticked))
    if client is None:
        client = state.client
    if client is not None:
        try:
            line = connect_line(client.name, session.root)
        except Unquotable:
            line = ""
        out.append((line, (_connect_key(client),), session.connect))
    return tuple(item for item in out if item[0])


def ran_commands(session: Session, state: PageState) -> tuple[str, ...]:
    """Every command a finished press ran, in the order it ran them: the
    setup, then a line per (agent, skill) pair it wrote for, then the
    connection where it was made.

    A step that did not land earns no line, so no line claims an add or a
    connection that never happened (PLAN-0015 phase 3 prescription 2), and
    a command with no safe spelling is left out (setup-web-04). Running
    these lines does what the press did.
    """
    out: list[str] = []
    if state.kind == MOUNTED:
        out.append(mount_line(session))
    for harness, name in state.added:
        try:
            out.append(skill_add_line(harness, name, session.root))
        except Unquotable:
            continue
    if state.connect and state.client is not None:
        try:
            out.append(connect_line(state.client.name, session.root))
        except Unquotable:
            pass
    return tuple(line for line in out if line)


def _ran_items(lines, *, switch: bool) -> str:
    """The commands a press ran, for its result line's Details, shown
    while the screen's Show commands box is ticked. `switch` draws that
    box here, for a screen that carries none of its own."""
    if not lines:
        return ""
    return ((show_commands() if switch else "")
            + f'<div class="commands"><h3>{_esc(COMMANDS_HEAD)}</h3>'
            + "".join(copy_line(line) for line in lines) + "</div>")


def _command_items(commands, *, always: bool = False) -> str:
    """The commands, under their heading, shown while the screen's Show
    commands box is ticked."""
    lines = ""
    for line, keys, shown in commands:
        if keys and not always:
            lines += f"<div{_follows(keys, shown)}>{copy_line(line)}</div>"
        else:
            lines += copy_line(line)
    if not lines:
        return ""
    return f'<div class="commands"><h3>{_esc(COMMANDS_HEAD)}</h3>{lines}</div>'


def _names(session: Session) -> str:
    """The two names the setup writes into the project, and the press that
    redraws the screen with them. The commands spell the names, and a
    connection is keyed by the project's, so both follow a changed name
    only once the server has drawn the screen again."""
    return (
        '<div class="names">'
        f'<label>{_esc(SCOPE_LABEL)}<input id="scope" name="scope" '
        f'value="{_attr(session.scope)}" autocomplete="off" spellcheck="false"></label>'
        f'<label>{_esc(OWNER_LABEL)}<input id="owner" name="owner" '
        f'value="{_attr(session.owner)}" autocomplete="off" spellcheck="false"></label>'
        '<button class="refresh" type="submit" name="action" value="survey">'
        f"{_esc(REFRESH_LABEL)}</button></div>"
        f'<p class="hint">{_esc(REFRESH_HINT)}</p>'
    )


def _settings(inner: str) -> str:
    """Details, closed to begin with."""
    if not inner:
        return ""
    return (
        f'<details class="settings"><summary>{_esc(DETAILS_LABEL)}</summary>'
        f'<div class="drawer">{inner}</div></details>'
    )


# --- the screen's states ----------------------------------------------

def _review_body(session: Session, state: PageState, frame: Frame = PLAIN) -> str:
    """Ready to set up: the folder, what a setup adds, the skills, the
    Connect box, and the one press."""
    grid, boxes = _setup_grid(session, state.catalog)
    offer = _offer(session, state)
    commands = _command_items(press_commands(session, state, boxes, offer.client))
    details = _settings(
        (show_commands() if commands else "")
        + _names(session)
        + _file_items(session, state, boxes, offer)
        + offer.files
        + commands
    )
    # The two sentences on lines of their own, as mockup 04-add-project
    # sets them (PLAN-0027 phase 5).
    intro = (f"{_esc(INTRO_ADDS.format(agents=_agents_said()))}<br>\n"
             f"{_esc(INTRO_KEEPS)}")
    return (
        '<div class="screen setup">'
        + frame.leave
        + _form(
            session,
            _choices()
            + _back(frame)
            + _name_head(session, SETUP_HEAD.format(project=_project_word(session.root)))
            + f'<p class="intro">{intro}</p>'
            + grid
            + offer.box
            + '<div class="setup-actions">'
            + '<button class="primary" type="submit" name="action" value="mount">'
            + f"{_esc(SETUP_LABEL)}</button>"
            + _cancel(frame)
            + details
            + "</div>",
        )
        + "</div>"
    )


def _already_body(session: Session, state: PageState, frame: Frame = PLAIN) -> str:
    """A folder that is already set up: what it has, and what it can
    still be given from here, the skills it lacks or, when it has every
    skill, the connection (PLAN-0027 phase 2 prescription 3)."""
    grid, boxes = _setup_grid(session, state.catalog)
    only_connect = not boxes and state.client is not None
    offer = _offer(session, state, press=only_connect)
    offered = bool(boxes) or offer.client is not None
    commands = _command_items(
        press_commands(session, state, boxes, offer.client), always=only_connect)
    # A withheld connection's sentence sends the reader to Details, so
    # Details stands for it even where nothing else is offered (review F3).
    details = _settings(
        (show_commands() if commands else "")
        + _file_items(session, state, boxes, offer)
        + offer.files
        + commands
    ) if offered or offer.files else ""
    said = ALREADY_OFFERS if offered else ALREADY_COMPLETE
    press = (
        '<button class="primary" type="submit" name="action" value="skills">'
        f"{_esc(ADD_LABEL)}</button>"
    ) if offered and not only_connect else ""
    actions = press + _cancel(frame) + details
    inner = (
        _back(frame)
        + _name_head(session, _project_word(session.root))
        + f'<p class="intro">{_esc(said)}</p>'
        + grid
        + offer.box
        + (f'<div class="setup-actions">{actions}</div>' if actions else "")
    )
    if offered:
        inner = _form(session, _choices() + inner)
    return (
        '<div class="screen setup">' + frame.leave + inner
        + _closing(state, frame, CLOSED) + "</div>"
    )


def folder_path(path) -> str:
    """A folder's path, drawn so a line wraps only after a separator.

    Each piece up to and including a separator is one the line can't
    split, with a break offered after it, so "lap-notes" never wraps into
    "lap-" and "notes", which read as two names. A piece wider than the
    whole line still wraps inside itself (PLAN-0027 phase 5, C3)."""
    pieces = [piece for piece in re.split(r"(?<=[\\/])", str(path)) if piece]
    return "<wbr>".join(f'<span class="seg">{_esc(piece)}</span>' for piece in pieces)


def stopped_card(frame: Frame, *, head: str, path, said: str,
                 details: str = "", press: str = "", after: str = "",
                 code: str = "") -> str:
    """A setup that can't start, or stopped: one card saying so.

    The status, the heading, the folder, one sentence of why, what is in
    the way behind Details, and the next step. No control forces past a
    refusal, because the command line has none. The card is the state's
    whole message, so it is the live region too. `code` is a file name
    the sentence holds, set as code there, as mockup 04-add-conflict sets
    `.mcp.json` (PLAN-0027 phase 5, C2).
    """
    sentence = _esc(said)
    if code:
        sentence = sentence.replace(_esc(code), f"<code>{_esc(code)}</code>", 1)
    zone = press + _cancel(frame)
    return (
        '<div class="screen setup stopped">' + frame.leave
        + '<div class="stage"><section class="card" role="status" aria-live="polite">'
        + f'<p class="stopped-row">{state_mark(GONE, STOPPED_STATE)}</p>'
        + f"<h1>{_esc(head)}</h1>"
        + f'<p class="path">{folder_path(path)}</p>'
        + f"<p>{sentence}</p>"
        + (f"<details><summary>{_esc(DETAILS_LABEL)}</summary>"
           f'<div class="drawer">{details}</div></details>' if details else "")
        + (f'<div class="action-zone">{zone}</div>' if zone else "")
        + after
        + "</section></div></div>"
    )


def _card(session: Session, frame: Frame, **said) -> str:
    """The card, for this session's folder."""
    return stopped_card(frame, path=session.root, **said)


def _setup_word(session: Session) -> str:
    """The heading of a card: the setup it stopped, or, where the folder
    is set up already and the press was adding to it, the folder's name."""
    name = _project_word(session.root)
    return name if _is_set_up(session) else SETUP_HEAD.format(project=name)


def _check_again(session: Session) -> str:
    """Check again: the survey once more, with the boxes left as they
    were, since this form draws none and carries no marker."""
    return _form(
        session,
        '<button class="primary" type="submit" name="action" value="survey">'
        f"{_esc(CHECK_AGAIN_LABEL)}</button>",
    )


def _blocker_rows(dispositions, resolve) -> str:
    rows = "".join(
        f"<li><code>{_esc(mnt.one_line(d.path))}</code>: "
        f"{_esc(mnt.one_line(resolve(d) or ''))}</li>"
        for d in dispositions)
    return f"<h3>{_esc(BLOCKERS_HEAD)}</h3><ul>{rows}</ul>" if rows else ""


def _conflict_body(session: Session, state: PageState, frame: Frame = PLAIN) -> str:
    """A refused setup. Four refusals reach this state, and each says its
    own sentence: files in the way, a folder inside a set-up project, a
    ticked skill's place taken, and the client's settings blocking a
    ticked connection. Every one of them wrote nothing."""
    head = _setup_word(session)
    if state.why == WHY_INSIDE:
        parent = Path(state.parent)
        word = _project_word(parent)
        details = (
            f"<p>{_esc(INSIDE_WHY.format(folder=_project_word(session.root)))}</p>"
            f"<p>{_esc(INSIDE_WHERE.format(path=parent))}</p>"
        )
        press = (
            frame.opener(parent, OPEN_PROJECT.format(parent=word))
            if frame.opener is not None else _check_again(session))
        return _card(
            session, frame, head=head, said=INSIDE_SAID.format(parent=word),
            details=details, press=press)
    if state.why == WHY_SKILL:
        said = SKILL_BLOCKED
        details = _blocker_rows(state.dispositions, mnt.reason_text)
    elif state.why == WHY_CONNECT:
        client = cn.client_for(cn.DEFAULT_CLIENT)
        said = CONNECT_REFUSED.format(label=client.label)
        details = _blocker_rows(state.dispositions, mnt.reason_text)
    else:
        paths = [d.path for d in state.dispositions]
        code = ""
        if paths == [mnt.MCP_REL]:
            said = BLOCKED_SETTINGS.format(file=mnt.MCP_REL)
            code = mnt.MCP_REL
        elif len(paths) == 1:
            said = BLOCKED_ONE
        else:
            said = BLOCKED_MANY.format(count=len(paths))
        details = _blocker_rows(
            state.dispositions,
            lambda d: MCP_REASON if d.path == mnt.MCP_REL else report.conflict_reason(d.path))
        return _card(
            session, frame, head=head, said=said, details=details,
            press=_check_again(session), code=code)
    return _card(
        session, frame, head=head, said=said, details=details,
        press=_check_again(session))


def _engine_notes(state: PageState, *, log: bool) -> tuple[str, ...]:
    """The engine's own sentences about a stop, for Details, and where
    the log is. The sentence that says the connection was not attempted
    is the result line's to say, in the screen's words.

    The engine ends a file error's sentence "nothing was written" both
    where the stop came before any write and where its writer put back
    every file it wrote, and the page can't tell the two apart. So
    Details says "nothing was changed", which is true of both (PLAN-0027
    phase 2 review F7). The log keeps the engine's words."""
    said = state.message
    if said.endswith(CONNECT_NOT_ATTEMPTED):
        said = said[: -len(CONNECT_NOT_ATTEMPTED)].rstrip()
    said = said.replace(ENGINE_NO_WRITE, PAGE_NO_CHANGE)
    notes = [said] if said else []
    if log:
        line = log_line()
        if line:
            notes.append(line)
    return tuple(notes)


def _changed(*groups) -> tuple[tuple[str, str], ...]:
    """Each path a press changed or kept, tagged with what happened to it."""
    return tuple(
        (d.path, TAGS[d.verb]) for rows in groups for d in rows if d.verb in TAGS)


def _added_count(state: PageState) -> int:
    """How many skills the press added, each counted once whichever
    agents it was added for."""
    return len({name for _, name in state.added})


def _client_label(state: PageState) -> str:
    client = state.client or cn.client_for(cn.DEFAULT_CLIENT)
    return client.label


def _running_after(state: PageState, frame: Frame) -> str:
    """The after-press sentence, for a press that connected while the
    client was open (BUG-0018): a restart alone can lose the change. The
    same for a connection that stopped partway and left the client's
    settings changed, which carries its client for this (PLAN-0027 phase
    2 review F2), as the app asks of the same press."""
    client = state.client
    if client is None or not (state.connect or state.left):
        return ""
    try:
        paths = [target.path for target in cn.detect(client)]
    except OSError:
        return ""
    if not cn.is_running(client, paths):
        return ""
    return frame.running_after.format(label=client.label)


@dataclass(frozen=True)
class Said:
    """A press's answer, ready for `result_line`."""

    tone: str
    sentence: str
    notes: tuple = ()
    changed: tuple = ()
    more: str = ""


def setup_said(session: Session, state: PageState, *, switch: bool = True) -> Said:
    """What the Set up project press did, as one result line.

    The line says what was set up and, where the press's skills or its
    connection did not land, what wasn't (PLAN-0027 phase 2 prescription
    4). Details carries the next step with its command, every command the
    press ran, the engine's own sentence about a step that stopped, and
    every file the press changed. The app lands on the project's screen
    with this same line; that screen has its own Show commands box, so it
    passes `switch=False`.
    """
    project = _project_word(session.root)
    label = _client_label(state)
    count = _added_count(state)
    skills = _skills_said(count)
    pick_up = PICK_UP_ONE if count == 1 else PICK_UP_MANY
    both = (BOTH_ONE if count == 1 else BOTH_MANY).format(label=label)
    tone = RESULT_DONE
    if MISSED_SKILLS in state.missed:
        tone = RESULT_PARTLY
        sentence = (SET_UP_SKILLS_LEFT if state.left else SET_UP_NO_SKILLS).format(
            project=project)
        if MISSED_CONNECT in state.missed:
            sentence += " " + NOT_CONNECTED_EITHER.format(label=label)
    elif MISSED_CONNECT in state.missed:
        tone = RESULT_PARTLY
        if count:
            line = SET_UP_SKILLS_CONNECT_LEFT if state.left else SET_UP_SKILLS_NO_CONNECT
        else:
            line = SET_UP_CONNECT_LEFT if state.left else SET_UP_NO_CONNECT
        sentence = line.format(
            project=project, skills=skills, label=label, pick_up=pick_up)
    elif count and state.connect:
        sentence = SET_UP_BOTH.format(
            project=project, skills=skills, label=label, both=both)
    elif count:
        sentence = SET_UP_SKILLS.format(project=project, skills=skills, pick_up=pick_up)
    elif state.connect:
        sentence = SET_UP_CONNECTED.format(project=project, label=label)
    else:
        sentence = SET_UP.format(project=project)
    if not state.missed and state.outcome == report.PARTIAL:
        tone = RESULT_PARTLY
        sentence += " " + KEPT_EDITED
    step, validate = report.next_step(session.root)
    return Said(
        tone, sentence,
        notes=_engine_notes(state, log=bool(state.missed)),
        changed=_changed(state.dispositions, state.skills, state.connect),
        more=(f"<p>{_esc(step)}</p>" + copy_line(validate)
              + _ran_items(ran_commands(session, state), switch=switch)),
    )


def added_said(session: Session, state: PageState, *, switch: bool = True) -> Said:
    """What a press on a set-up project did: the skills it added, the
    connection it made, or which of them it could not, and the commands
    it ran."""
    label = _client_label(state)
    count = _added_count(state)
    skills = _skills_said(count)
    pick_up = PICK_UP_ONE if count == 1 else PICK_UP_MANY
    both = (BOTH_ONE if count == 1 else BOTH_MANY).format(label=label)
    tone = RESULT_DONE
    if state.kind == CONNECTED:
        sentence = CONNECTED_SAID.format(label=label)
    elif state.kind == CONNECT_FAILED:
        tone = RESULT_PARTLY if state.left else RESULT_FAILED
        sentence = (CONNECT_STOPPED_SAID if state.left else CONNECT_FAILED_SAID).format(
            label=label)
    elif state.kind == FAILED:
        tone = RESULT_PARTLY if state.left else RESULT_FAILED
        sentence = SKILLS_STOPPED if state.left else SKILLS_FAILED
        if MISSED_CONNECT in state.missed:
            sentence += " " + NOT_CONNECTED_EITHER.format(label=label)
    elif MISSED_CONNECT in state.missed and count:
        tone = RESULT_PARTLY
        sentence = (ADDED_CONNECT_LEFT if state.left else ADDED_NO_CONNECT).format(
            skills=skills, label=label, pick_up=pick_up)
    elif MISSED_CONNECT in state.missed:
        tone = RESULT_PARTLY if state.left else RESULT_FAILED
        sentence = (CONNECT_STOPPED_SAID if state.left else CONNECT_FAILED_SAID).format(
            label=label)
    elif not count:
        sentence = (CONNECTED_SAID.format(label=label) if state.connect
                    else NOTHING_ADDED)
    elif state.connect:
        sentence = ADDED_CONNECTED.format(skills=skills, label=label, both=both)
    else:
        sentence = ADDED.format(skills=skills, pick_up=pick_up)
    if (state.kind == SKILLED and not state.missed
            and state.outcome == report.PARTIAL):
        tone = RESULT_PARTLY
        sentence += " " + KEPT_EDITED
    return Said(
        tone, sentence,
        notes=_engine_notes(state, log=tone != RESULT_DONE),
        changed=_changed(state.skills, state.connect),
        more=_ran_items(ran_commands(session, state), switch=switch),
    )


def _finished(session: Session, state: PageState, frame: Frame, said: Said) -> str:
    """A press's answer on this screen: the project's name, the one
    result line, and, from `radialrig setup`, the word that its server
    has stopped."""
    closing = STOPPED if said.tone == RESULT_DONE else (
        CLOSED if said.tone == RESULT_PARTLY else CLOSED_RETRY)
    return (
        '<div class="screen setup done">' + frame.leave + _back(frame)
        + _name_head(session, _project_word(session.root))
        + result_line(
            said.tone, said.sentence, warning=_running_after(state, frame),
            changed=said.changed, notes=said.notes, more=said.more)
        + _closing(state, frame, closing)
        + "</div>"
    )


def _mounted_body(session: Session, state: PageState, frame: Frame = PLAIN) -> str:
    return _finished(session, state, frame, setup_said(session, state))


def _added_body(session: Session, state: PageState, frame: Frame = PLAIN) -> str:
    return _finished(session, state, frame, added_said(session, state))


def log_said() -> str:
    """The log line as a paragraph of a stop body, or nothing where this
    platform keeps no log (PLAN-0018 phase 2). The manage page's stop
    bodies draw it, so the sentence lives once."""
    line = log_line()
    return f'<p class="said">{_esc(line)}</p>' if line else ""


def _failed_body(session: Session, state: PageState, frame: Frame = PLAIN) -> str:
    """A stop. A set-up project's own press that stopped answers with its
    result line; a setup that stopped says so on the card, and whether it
    came before any change or part way through one, which is what the
    outcome carries."""
    if state.missed:
        return _added_body(session, state, frame)
    partial = state.outcome == report.PARTIAL
    notes = "".join(
        f"<p>{_esc(note)}</p>" for note in _engine_notes(state, log=True))
    return _card(
        session, frame, head=_setup_word(session),
        said=SETUP_PARTIAL if partial else SETUP_FAILED, details=notes,
        after=_closing(state, frame, CLOSED if partial else CLOSED_RETRY))


BODIES = {
    REVIEW: _review_body,
    MOUNTED: _mounted_body,
    CONFLICT: _conflict_body,
    ALREADY: _already_body,
    FAILED: _failed_body,
    SKILLED: _added_body,
    CONNECTED: _added_body,
    CONNECT_FAILED: _added_body,
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
