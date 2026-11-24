r"""`radialrig manage`: the standing page for this machine's projects.

PLAN-0008 Phase 4. `radialrig setup` walks one project onto the engine
and stops. This is the surface that comes after it: one local page that
lists every project the roster names, opens any of them, and reverses
what was done — through the same engine calls the command line makes,
never a second implementation of them (D-34). Every control on the page
names the command that does the same thing.

What this module is, exactly:

- one route, `/manage`, answered by `setup_web`'s own handler: the
  tokens, the origin guards, the headers, the close beacon and the error
  boundary are that class's, inherited rather than restated;
- one stdout line a program can parse — `manage: <url>` — printed first
  and in ASCII bytes with one LF, because Phase 5's app spawns this
  command and reads that line off a pipe;
- a page that stands rather than ends: every state renders with
  `terminal=False`, the recorded outcome is pinned at 0, and the process
  stops when the page closes or on Ctrl-C. An action's outcome rides in
  the page's own sentences, never in the exit code. A reload is not the
  page closing, so every document carries the render id its session
  issued for it and a close from a document already replaced is dropped
  (D8); a peer that simply hangs up is absorbed rather than reported
  (D10).

The listing is `radialrig projects` — the same projects, their state
said through one fixed mapping of the command line's words. What the
page adds is a control on each row, so a reader who can see the state
can act on it without retyping the path. The count line stays on the
command line: it tells a reader how many rows are about to print, and
here the rows are already on screen. So does the remedy's tail with its
placeholders: here the row names the client and the project's page
writes the remedy out, filled in, beside the press that runs it.

A folder the roster has never heard of is not a row this page can act on;
it is a project `radialrig setup` walks onto the engine. So the page
embeds that walkthrough rather than restating it — that page's states,
that page's presses, that page's words — inside this page's shell, on this
page's one token, and standing rather than ending when it is done.

One view walks the whole way back out: every listed project's reversal,
in the order the verbs reverse, run by the same presses. It stops two
steps short and names them instead — the engine's own removal and the
app's uninstaller — because a page cannot remove the process serving it,
and it says exactly what is still standing when everything it can run
has run.

Every consequential press runs in two steps. The first answers the engine's
own `*_plan` call — the rows `--dry-run` prints, and nothing written. The
second runs the verb itself and answers the engine's own report lines. Both
carry the command from `COMMANDS`, which is this module's parity claim in
code: a press with no row there has no command a reader could run instead.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import string
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from radialrig import activity_log as al
from radialrig import cli
from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import orphan
from radialrig import report
from radialrig import roster as ro
from radialrig import setup_web
from radialrig import skills as sk
from radialrig import update

MANAGE_PATH = "/manage"
# The masthead and the browser tab: the product's name, as the redesign's
# wording gives the masthead (PLAN-0027). The shared document builder
# writes this into both, so the name a reader reads on the page is the
# name the tab carries.
TITLE = "RadialRig"

# The one line Phase 5's app parses. It is a prefix rather than a whole
# sentence because everything after it is the URL and nothing else.
URL_PREFIX = "manage: "

# The two words the inherited refusals say about the page they refused
# for. Every refusal this route serves is `setup_web`'s own code path, and
# only these differ: a reader refused here is holding a manage page, and
# the command that opens a fresh one is this one — not the one that walks
# a single project onto the engine.
SURFACE = "radialrig manage"
PAGE_NAME = "manage"

# --- the render id (D8) -----------------------------------------------
# A reload is not the page closing, and the id every document carries is
# what tells the two apart. The apparatus was this page's first
# (PLAN-0008 phase 5); since PLAN-0017 phase 4 it is the setup server's,
# which both surfaces inherit — the session issues the ids, the handler
# stamps them and drops a stale close — so it is named here for the
# tests that read it and defined there.
RENDER_ATTR = setup_web.RENDER_ATTR
RENDER_FIELD = setup_web.RENDER_FIELD

# --- the loopback hang-up (D10) ---------------------------------------
# The three ways a peer on this loopback drops a connection. A browser
# abandoning its close beacon does it, and so does a probe that closes
# early; neither is a fault of the page, and the default handler would
# print a traceback for something no reader did and nothing failed at.
#
# D10 named two, which are the two Windows raises for a peer that went
# away mid-request. The third is the same fault's other spelling: a write
# to a socket the peer already closed raises `BrokenPipeError` on POSIX
# where it raises `ConnectionAbortedError` here, and this page is served
# by a plain browser on any machine the engine runs on.
HANGUPS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)

# --- the views --------------------------------------------------------
# One route, four views, chosen by the `view` field a navigation form
# carries. A view this build does not answer falls back to the roster,
# which is what makes a control for a view a later task builds inert
# rather than broken.

ROSTER = "roster"
PROJECT = "project"
SETUP = "setup"
REMOVAL = "removal"

# --- the states a press answers ---------------------------------------
# Two per press and two for the ways one can stop: what the verb would do,
# what it did, what refused it, and what it could not read.

PLAN = "action-plan"
DONE = "action-done"
REFUSED = "action-refused"
FAILED = "action-failed"

# The removal flow's own answer: one project's whole reversal, step by
# step. It is not one of the four above because it is not one press over
# one verb — it is the sequence, and every step in it answers as itself.
REVERSED = "reversal-done"

# Its rehearsal, on the same footing and for the same reason: the whole
# sequence as the engine's own dry run, every step answering with the plan
# state its own single press answers with.
REHEARSAL = "reversal-plan"

# The repair's two answers (PLAN-0014 phase 3), on the same footing again:
# the registration's removal and a fresh registration, rehearsed as two
# plan states and run as two reports. Their own kinds rather than the
# reversal's, because the page they land on leads back to the project
# and not to the reversals, and its heading says which sequence ran.
REPAIR_PLAN = "repair-plan"
REPAIRED = "repair-done"

# The two update presses (PLAN-0021 phase 1): one project's update, and
# every listed project's, rehearsed and run. Their own kinds for the
# reversal's reason: an update's report is several blocks — the seed's
# rows, then each agent's skills under that agent's own head — and each
# block is drawn as itself, its rows a table under its head, so no head
# stands over nothing. The listing's press draws one such report per
# project under the folder it is about.
UPDATE_PLAN = "update-plan"
UPDATED = "update-done"
UPDATE_ALL_PLAN = "update-all-plan"
UPDATED_ALL = "update-all-done"

# --- the actions, and the command each one names ----------------------
# One key per consequential press, and one command line per key. This
# table is D-34 in code: a press the handler dispatches with no row here
# would be a page-only power, so a test walks the dispatch against these
# keys. The orphan row spells `roster.orphan_command`, and a test pins the
# two spellings equal.

UNMOUNT = "unmount"
SKILL_ADD = "skill-add"
SKILL_ADD_ALL = "skill-add-all"
SKILL_REMOVE = "skill-remove"
CONNECT = "connect"
UNCONNECT = "unconnect"
ORPHAN = "orphan"
# The two update presses (PLAN-0021 phase 1 prescription 4): one project's
# RadialRig files and the skills it already has, and every listed
# project's. The second names no slot: the listing is the command's own
# argument, as it is for `radialrig upgrade --all`.
UPDATE = "update"
UPDATE_ALL = "update-all"

COMMANDS: dict[str, str] = {
    UNMOUNT: "radialrig unmount {root}",
    SKILL_ADD: "radialrig skill add {name} {root}",
    SKILL_ADD_ALL: "radialrig skill add --all {root}",
    SKILL_REMOVE: "radialrig skill remove {name} {root}",
    CONNECT: "radialrig connect {client} {root}",
    UNCONNECT: "radialrig unconnect {client} {root}",
    ORPHAN: "radialrig unconnect {client} --orphan {key}",
    UPDATE: "radialrig upgrade {root}",
    UPDATE_ALL: "radialrig upgrade --all",
}

# The skill verbs act on every agent bare and on one agent narrowed, the
# way the command line reads `--harness` (D-36 as amended for PLAN-0015).
# A press drawn in one agent's list names that agent, so its command
# carries this suffix and its runner narrows to that agent; a press that
# names none is the bare verb, which is what the reversal runs and what
# the walkthrough lists. The suffix is not a slot of the rows above,
# because an empty harness is not a hole in the command: the bare line
# is whole and does what the bare press does.
HARNESS_SUFFIX = " --harness {harness}"
SKILL_ACTIONS = (SKILL_ADD, SKILL_ADD_ALL, SKILL_REMOVE)

# The embedded walkthrough's own presses, spelled the way `setup_web`'s
# forms post them and its own POST tail reads them. They carry no row in
# the table above because they are not one verb: a walkthrough press is a
# sequence, and the view names every verb in it beside the button.
WALK_ACTIONS = ("mount", "skills", "survey")

# The removal flow's press, on the same footing and for the same reason:
# a reversal is three verbs at most, each already carrying its own row
# above, and the view names every one of them beside the button.
REVERSAL = "reversal"

# The repair press (PLAN-0014 phase 3), the same shape once more: two
# verbs the table above already carries — the registration's removal,
# then a fresh registration — run in that order by one press, and the
# view names both beside the button. No new engine rides under it.
REPAIR = "repair"


# One slot of a command line, ready to paste into a shell. The setup
# page's own, since PLAN-0015 phase 3: its finished page prints the
# skill lines this page's command table composes, and one quoting rule
# is what keeps the two surfaces printing the same line for the same
# press.
_quoted = setup_web.quoted


@dataclasses.dataclass(frozen=True)
class Action:
    """One consequential press, said twice: as fields, and as a command.

    The confirm press has to name the same thing the plan press named, and
    the block a reader copies has to run the same thing the button runs.
    Both come off this one object, so the two cannot drift.
    """

    name: str
    root: str = ""
    skill: str = ""
    client: str = ""
    key: str = ""
    # The harness map's key for the one agent a skill press acts on, or
    # "" for every agent (PLAN-0015 phase 2). Never a label: the label is
    # read off the map where the press is drawn.
    harness: str = ""

    @property
    def _slots(self) -> dict[str, str]:
        return {"root": self.root, "name": self.skill,
                "client": self.client, "key": self.key}

    @property
    def command(self) -> str:
        """The line this press runs, or "" where no safe line exists.

        A slot no quoting rule makes safe in both shells — a folder or a
        client key carrying `&`, `$`, a quote — has no line: offering one
        would offer something else to run (PLAN-0017 phase 4,
        setup-web-04). The empty string is what a hole renders as too,
        and every frame reads it the same way: no block.
        """
        try:
            line = COMMANDS[self.name].format(
                **{slot: _quoted(value) for slot, value in self._slots.items()})
            if self.harness and self.name in SKILL_ACTIONS:
                line += HARNESS_SUFFIX.format(harness=_quoted(self.harness))
        except setup_web.Unquotable:
            return ""
        return line

    @property
    def targets(self) -> tuple[str, ...] | None:
        """What the engine's `targets` reads off this press: the one
        agent it names, or None for the bare verb's every agent."""
        return (self.harness,) if self.harness else None

    @property
    def named(self) -> bool:
        """Whether this press filled every field its command names.

        A command with an empty field is worse than no command at all:
        `radialrig unmount ` with nothing after it is a line a reader can
        copy, and pasted somewhere else it acts on whatever directory
        they are standing in, because the command line's own path
        defaults to this one. So a press that arrived naming nothing has
        no block to offer. The fields are read off the table's own
        template rather than listed again here, so a row that grows a
        slot is covered by that alone.
        """
        slots = self._slots
        return all(
            slots[field]
            for _, field, _, _ in string.Formatter().parse(COMMANDS[self.name])
            if field
        )

    @property
    def fields(self) -> tuple[tuple[str, str], ...]:
        """What the form posts back to name this press again."""
        return (
            ("action", self.name), ("root", self.root), ("skill", self.skill),
            ("client", self.client), ("key", self.key), ("harness", self.harness),
        )


# --- page copy --------------------------------------------------------
# The sentences the pages this module still draws in the engine's voice
# say: the plan and result pages and the removal page, which stay until
# PLAN-0027 phases 2 and 3 rebuild them. Projects and one project take
# their words from the block further down.

ROSTER_HEAD = "Projects"

# How many skills a project has, in a row's line of facts. The count is
# the skills either agent has.
SKILLS_FACT = "{count} skills"
ONE_SKILL_FACT = "1 skill"
NO_SKILLS_FACT = "No skills"

ADD_HEAD = "Add a project"
# The add block's own id, kept on the element that holds the folder
# field.
ADD_ID = "add"
ADD_FIELD_LABEL = "Project folder"
# The field a reader types or pastes a path into and the desktop shell's
# own picker writes into (D11). The placeholder is what it reads before
# either has happened. The button beside it is the shell's picker said on
# the page: it ships hidden, because a browser cannot open a folder, and
# the shell reveals it after the page loads inside its window. A press
# asks the shell through the document's own name; page.js writes the
# marker and puts the name back the moment it is read. The button says
# what the shell's own menu item says, so the two are one thing to a
# reader.
ADD_FIELD_PLACEHOLDER = "Type or paste a folder path"
BROWSE_LABEL = "Choose a folder"
# The press under the field. It opens the setup walkthrough on that
# folder and writes nothing itself.
ADD_LABEL = "Continue"

# The project screen's two section heads. Not one of this module's lines
# names a skill or a client: a skill's name arrives from the catalog and
# a client's label from the client table, and every label with one in it
# is a template with a slot (PLAN-0014 constraint 5). The grid's head is
# the setup page's, since Add a project draws the same grid.
SKILLS_HEAD = setup_web.SKILLS_HEAD
CONNECTIONS_HEAD = "Connections"

# A connection row's state. The row leads with the client's label, so
# the state says only which way it stands.
CONNECTED_STATE = "Connected"
NOT_CONNECTED_STATE = "Not connected"

# A grid with nothing to list says so rather than disappearing.
SKILLS_NONE = "No skill is listed for this project."

# The name a Connect or Disconnect button is read by. The client slot is
# filled at render time from the client table.
CONNECT_LABEL = "Connect {label}"
UNCONNECT_LABEL = "Disconnect {label}"

# The repair's plan and result pages (PLAN-0014 phase 3): the
# registration's removal, then a fresh registration. The sentence for a
# rehearsal that finds nothing left to repair is the one the record and
# the settings agreeing leaves.
REPAIR_PLAN_HEAD = "What repairing this connection would change."
REPAIRED_HEAD = "That connection is repaired."
NOTHING_TO_REPAIR = (
    "This project's record and the client's settings agree, so there is "
    "nothing to repair."
)
# A rehearsal writes nothing, so the registration's dry run reads the
# settings file as it stands, before the removal has taken the earlier
# entry out; where the file still holds one, the engine refuses that dry
# run with its own sentence — run the removal first, then this command —
# which is the order the press runs them in. That answer is the
# rehearsal reading a disk the removal has not changed yet, not a
# refusal the run would meet, so the page does not draw it as one: the
# step stands under this heading, with this sentence and its command,
# and the press follows.
REPAIR_AFTER_HEAD = "The registration, after the removal has run."
REPAIR_ORDER_NOTE = (
    "The registration cannot be rehearsed until the removal has taken the "
    "earlier entry out, so its rows are not shown here. The press below runs "
    "the removal first, then the registration."
)

# A Copy button's accessible name, naming the command it copies: the
# visible text stays the one word, and page.js swaps only that.
COPY_NAME = setup_web.COPY_NAME

# Remove RadialRig (wording.md, Screen 6; PLAN-0027 phase 3 prescription
# 3): numbered steps. Each project comes out through its own Remove, which
# leads to Confirm a big change; then the engine's removal commands, which
# this page names and never runs, because a page cannot remove the process
# serving it; then, where this system has one, the app's own uninstall.
# The app's step is last (prescription 4): its uninstaller takes the folder
# that holds uv off the user Path, and step 2's uv commands need it in any
# terminal opened after that.
REMOVAL_HEAD = "Remove RadialRig"
STEP_PROJECTS = "Take RadialRig out of your projects"
STEP_PROJECTS_NONE = "No projects are listed."
STEP_REMOVE = "Remove"
STEP_NOTHING_LEFT = "Nothing left to remove"
STEP_RECORD_LEFT = (
    "Only its connection record is left. Open the project to repair the link, "
    "then remove it.")
# The same where Repair is withheld, so the project's screen can't clear
# it: What stays afterward holds the filled disconnect (review R5).
STEP_RECORD_RUN = (
    "Only its connection record is left. What stays afterward has the command "
    "that takes it out.")
STEP_ENGINE = "Remove the engine and its tools"
# Which shell the commands are pasted into, by system: the delete line is
# written for PowerShell and Command Prompt alike on Windows.
STEP_ENGINE_WINDOWS = (
    "Copy these commands, close RadialRig, then run them in PowerShell. Remove "
    "Python and uv only if RadialRig installed them and you don't use them for "
    "anything else.")
STEP_ENGINE_TERMINAL = (
    "Copy these commands, close RadialRig, then run them in your terminal. "
    "Remove Python and uv only if RadialRig installed them and you don't use "
    "them for anything else.")
STEP_APP = "Uninstall the Windows app"
# The step's lead; the Machine row's Path sentence follows it.
STEP_APP_SAID = (
    "When step 2 is done, open Windows Settings, go to Apps, and uninstall "
    "RadialRig.")
STAYS_LABEL = "What stays afterward"
STAYS_SAID = (
    "Files you edited stay in their projects. The project list and activity "
    "logs stay in {data_dir} so you can see what ran. Deleting that folder "
    "removes only that history.")
STAYS_LEFTOVER_ONE = (
    "{label} still has 1 leftover RadialRig entry for a folder that's gone. "
    "Clean it up on Projects.")
STAYS_LEFTOVER_MANY = (
    "{label} still has {count} leftover RadialRig entries for folders that are "
    "gone. Clean them up on Projects.")
STAYS_RECORD = (
    "{project}'s connection record stays, because RadialRig can't repair its "
    "link from here. Run this command to take it out.")

ENGINE_REMOVAL = "uv tool uninstall radialrig"
ENGINE_WHAT = "Remove the engine and the commands it installed."
APP_WHAT = "Remove the desktop app from {place}."

# The Python uv installed for this engine, named at the version the
# process reading this line is running rather than at a version written
# down here — the command has to name the Python this engine is on. The
# slot is filled at render time, and the switch that would take every
# Python uv manages is never passed: Pythons installed for other work are
# not this removal's to take.
PYTHON_REMOVAL = "uv python uninstall {version}"
PYTHON_WHAT = (
    "Remove the Python uv installed for the engine, where uv installed one. A "
    "Python that was already on this machine is not uv's to take, and this "
    "names one version rather than every version."
)

# uv's own two pieces, in the order that leaves each command something to
# run it: the cache first, because emptying it is a uv command, and then
# the two programs themselves.
UV_CACHE_REMOVAL = "uv cache clean"
UV_CACHE_WHAT = "Empty the download cache uv keeps under {cache}."
UV_WHAT = "Remove uv itself, once nothing above still needs it to run."

# The one machine change with no command behind it, where this operating
# system has one. The app recorded the exact value it wrote, and its own
# removal flow is the only thing that should take that entry back out —
# a line typed over a Path value is one keystroke from taking everything
# else on it too. In wording.md's words (Screen 6, step 3), since PLAN-0027
# phase 3: the uninstaller checks the whole Path value, not the entry
# alone (`removal.rs`), and step 3 says this after its lead.
PATH_WHAT = (
    "It also takes RadialRig's entry off your user Path, unless something "
    "changed your Path after RadialRig added it. Then it leaves your Path as it "
    "is and tells you which entry to remove."
)

# The same slot on Linux, stated rather than left implied (PLAN-0009
# phase 3 prescription 5): the app's first run on Linux reads PATH and
# writes nothing, so this view says there is nothing to take back out
# instead of silently dropping the row. In wording.md's words since
# PLAN-0027 phase 3, first in What stays afterward.
LINUX_PATH_WHAT = (
    "RadialRig didn't change your PATH on this computer, so there's nothing to "
    "take off it."
)

# The remains line for the same entry, sitting here beside the line it
# pairs with because the `MACHINES` rows below carry both. Remove
# RadialRig no longer draws it (PLAN-0027 phase 3): step 3 says what the
# uninstall does with the entry, and What stays afterward lists only what
# no step takes out. The row keeps it, since the rows stay as they are.
REMAINS_PATH = (
    "One entry on your user Path, until the app's own removal flow takes it "
    "out. No command here removes it."
)


# What this operating system calls the machine-level pieces. Per-OS
# knowledge lives in rows, on PLAN-0009 constraint 3's rule, the way
# `roster.STORES` and `connect.CLIENTS` carry theirs. The two programs
# are named by their real paths rather than by the variable that spells
# them: every command line on the removal view is one a reader copies
# into whichever shell is open, and the default Windows shell does not
# expand `%USERPROFILE%` at all. So the paths are expanded where the
# page is drawn, on the machine the page is describing. The quoting
# stays: a profile directory with a space in it puts one in both paths.
@dataclasses.dataclass(frozen=True)
class Machine:
    """One operating system's rows on the removal view."""

    platform: str                  # sys.platform prefix this row serves
    uv_cache: str                  # where uv keeps its cache, as the reader sees it
    uv_bin: tuple[str, ...]        # under the home directory, where the two programs land
    uv_programs: tuple[str, str]   # the two program filenames
    uv_delete: str                 # the delete line; {uv} and {uvx} filled at render
    app_place: str                 # where the app comes out; "" when this OS has no row
    path_what: str                 # what stands on the Path here, stated either way
    path_remains: str              # the remains line for it; "" when nothing stands


MACHINES: tuple[Machine, ...] = (
    Machine(
        platform="win32",
        uv_cache="%LOCALAPPDATA%\\uv\\cache",
        uv_bin=(".local", "bin"),
        uv_programs=("uv.exe", "uvx.exe"),
        # The comma is what makes one line serve both Windows shells:
        # `del` in cmd takes a comma as an argument separator, and in
        # PowerShell `del` is `Remove-Item`, whose `-Path` is a list the
        # comma builds — where two space-separated paths bind the second
        # to another parameter and refuse the command outright.
        uv_delete='del "{uv}","{uvx}"',
        app_place="the app's row in Windows Settings, Apps",
        path_what=PATH_WHAT,
        path_remains=REMAINS_PATH,
    ),
    # PLAN-0009 Phase 2 prescription 4. No app row: the Linux bundle's
    # removal is phase 4's business and this view claims nothing before
    # it ships. The Path row states that nothing was written (PLAN-0009
    # phase 3 prescription 5), and nothing stands afterward, so the
    # remains line is empty.
    Machine(
        platform="linux",
        uv_cache="~/.cache/uv",
        uv_bin=(".local", "bin"),
        uv_programs=("uv", "uvx"),
        uv_delete='rm "{uv}" "{uvx}"',
        app_place="",
        path_what=LINUX_PATH_WHAT,
        path_remains="",
    ),
)


def machine() -> Machine | None:
    """The row serving this platform, or None where the table has none.

    None drops the machine-level rows from the view rather than showing
    another operating system's words: a page that names a path this
    machine does not have invites a copy and paste that fails.
    """
    for row in MACHINES:
        if sys.platform.startswith(row.platform):
            return row
    return None


# The two-step press. The plan state is the engine's rehearsal and the
# confirm button is the one press that writes, which is why the amber is
# there and nowhere else on that screen.
PLAN_CAPTION = "What this would change"
DONE_CAPTION = "What changed"
REFUSED_CAPTION = "What stopped it"
CONFIRM_LABEL = "Run this change"
PROJECT_BACK_LABEL = "Back to this project"
ROSTER_BACK_LABEL = "Back to the projects"
ACTION_FAILED_HEAD = "That press did not run. Nothing was written."

# Navigating to a folder this disk does not carry is not a press that
# failed — nothing was pressed. The roster's own folder field is where a
# reader most often reaches this, by typing a path with a letter wrong in
# it, so the head says what happened in the words of the thing they did.
# The command line's sentence beneath it says which path.
MISSING_HEAD = "That folder is not on this disk."

# Add a project for a folder that is not there (wording.md, Screen 4,
# "Folder not found"): the card's heading, its sentence, and the press
# beside the folder field, which is the one Projects' own field carries.
# A path that names no folder at all, such as one pasted in quotes, comes
# back to Projects with the last of these as its result line.
NOT_FOUND_HEAD = "Folder not found"
NOT_FOUND_SAID = (
    "There's no folder at that path on this computer. Choose another "
    "folder. Nothing was written."
)
PATH_UNUSABLE = (
    "RadialRig can't use that as a folder path. Type or paste the folder's "
    "full path, with no quotes around it."
)

# The command line's own wording for a path that is not a directory, so a
# posted folder that is gone reads the same on both surfaces. It is only
# ever rendered for a path that was named: a colon with nothing after it
# tells a reader nothing.
NO_DIRECTORY = "no such directory: {path}"

# Section 2's ruling: the page names a project list file this build
# cannot read, and the command line goes on reading it as empty with no
# remark. The path, what the list does about it, that the file is not
# overwritten (constraint 5), and what moving it aside buys.
UNREADABLE_NOTICE = (
    "Couldn't read the project list at {path}, so this list starts empty. "
    "RadialRig left that file untouched. Move or delete it to start a fresh "
    "list."
)

# --- the redesigned screens' words (PLAN-0027 phase 1) ----------------
# Projects and one project say these and nothing else of their own: each
# is a line of `reports/ui-redesign-2026-11/wording.md`, where a change
# to a word is made first. A client's label, an agent's label and a
# skill's name still arrive from their tables, so every line that names
# one is a template with a slot (PLAN-0014 constraint 5).

# The glyphs and the shared words are the setup page's own: Add a project
# is drawn there with the same look, so each is defined once.
GLYPH_CHECK = setup_web.GLYPH_CHECK
GLYPH_MINUS = setup_web.GLYPH_MINUS
GLYPH_UP = setup_web.GLYPH_UP
GLYPH_STOP = setup_web.GLYPH_STOP
GLYPH_BACK = setup_web.GLYPH_BACK
MIDDOT = setup_web.MIDDOT

DETAILS_LABEL = setup_web.DETAILS_LABEL
COPY_LABEL = setup_web.COPY_LABEL
SHOW_COMMANDS = setup_web.SHOW_COMMANDS

# A project's status. The four a project can be in, in the order the
# list sorts them, and a fifth for a listed folder that is gone.
NEEDS_REPAIR = setup_web.NEEDS_REPAIR
UPDATE_READY = setup_web.UPDATE_READY
NOT_SET_UP = setup_web.NOT_SET_UP
WORKING = setup_web.WORKING
GONE = setup_web.GONE
STATUS_ORDER = (NEEDS_REPAIR, UPDATE_READY, NOT_SET_UP, WORKING)

# Each status's mark: its glyph, and the outline the stylesheet draws
# round it. Shape tells them apart; no status has a color of its own.
MARKS = setup_web.MARKS

# On a row of Projects: the status as a word.
STATUS_WORDS = {
    NEEDS_REPAIR: "Needs repair",
    UPDATE_READY: "Update ready",
    NOT_SET_UP: "Not set up",
    WORKING: "Working",
}
STATUS_GONE = "Folder gone"

# On the project's own screen: the status as a sentence.
STATUS_SAID = {
    NEEDS_REPAIR: "{label} lost its link to this project.",
    UPDATE_READY: "An update is ready for this project.",
    NOT_SET_UP: "RadialRig is not set up in this project.",
    WORKING: "RadialRig is working in this project.",
}

# A row's one line of facts: the connection, a comma, the skills. The
# skills half is `SKILLS_FACT` and its two siblings below.
FACT_CONNECTED = "{label} connected"
FACT_LOST = "{label} link lost"
FACT_NOT_CONNECTED = "{label} not connected"
FACT_NOTHING = "No connections or skills"
FACT_GONE = "This folder isn't on this computer now, so it's off the project list."

# The one button a row or a status carries, and the name a row's button
# is read by, since a list holds several of each.
REPAIR_BUTTON = "Repair"
UPDATE_BUTTON = "Update"
SETUP_BUTTON = "Set up"
CLEAN_UP_BUTTON = "Clean up"
ROW_BUTTON_NAME = "{action} {project}"

# Above the list while any project is behind.
UPDATE_ONE = "1 project has an update."
UPDATE_MANY = "{count} projects have an update."
UPDATE_ALL_BUTTON = "Update all"

# An empty list.
EMPTY_HEAD = "No projects yet"
EMPTY_LEDE = "Choose a folder to set up your first project."

# Entries a client still holds for a project that is gone (PLAN-0027
# phase 3 prescription 5). A gone folder's entries are one row of the
# list, with Clean up: a connected folder leaves one entry per server,
# two today, so the row's facts are mostly plural. Entries that name no
# project folder can't be a row, and stand behind one line under the
# list with the engine's sentence and no press.
LEFTOVER_FACT_ONE = "Folder gone, {label} entry left"
LEFTOVER_FACT_MANY = "Folder gone, {label} entries left"
LEFTOVER_LOOSE_ONE = "{label} has 1 RadialRig entry that names no project folder."
LEFTOVER_LOOSE_MANY = (
    "{label} has {count} RadialRig entries that name no project folder.")
LEFTOVER_SHOW = "Show"

# The foot of Projects.
FOOT_VERSION = "RadialRig {version}"
LOG_LABEL = "Activity log"
REMOVE_ALL_LABEL = "Remove RadialRig"

# Where Repair is withheld, the status says why in one of these.
REPAIR_NO_RECORD = (
    "RadialRig can't read this project's connection record, so it can't "
    "repair the link from here."
)
RECORD_AT = "The connection record is at {path}"
REPAIR_NO_CLIENT = (
    "This project is connected to {label}, but {label} isn't on this computer."
)
REPAIR_NO_PROGRAM = (
    "{label} lost its link to this project. RadialRig can't repair it "
    "because it can't find its own rule lookup program."
)
REPAIR_BLOCKED = (
    "{label} lost its link to this project. RadialRig can't repair it right "
    "now."
)
REPAIR_SEE_DETAILS = "Check Details to see what's in the way."
# The same, in a few words, on the project's row of Projects.
ROW_WHY_NO_RECORD = "RadialRig can't read its connection record."
ROW_WHY_NO_CLIENT = "{label} isn't on this computer."
ROW_WHY_NO_PROGRAM = "RadialRig can't find its own rule lookup program."
ROW_WHY_BLOCKED = "RadialRig can't repair it right now. Open it to see why."
REPAIR_BLOCKERS_HEAD = "What's in the way of a repair"

# The skill grid. Its words are the setup page's, since Add a project
# draws the same grid; the cell kinds On, Off and Update are this
# screen's, where a cell is a press.
ADD_ALL_LABEL = setup_web.ADD_ALL_LABEL
ADD_ALL_NAME = setup_web.ADD_ALL_NAME
CELL_ON = "on"
CELL_OFF = "off"
CELL_UPDATE = "update"
CELL_FOREIGN = setup_web.CELL_FOREIGN
CELL_FILE = setup_web.CELL_FILE
CELL_ON_LABEL = setup_web.CELL_ON_LABEL
CELL_OFF_LABEL = setup_web.CELL_OFF_LABEL
CELL_UPDATE_LABEL = setup_web.CELL_UPDATE_LABEL
CELL_NAME = setup_web.CELL_NAME
CELL_UPDATE_NAME = setup_web.CELL_UPDATE_NAME
# A cell the engine won't touch: its reason, and no button.
CELL_REASONS = setup_web.CELL_REASONS
# A skill a record still lists that the package no longer ships.
RETIRED_SAID = "RadialRig no longer includes this skill."
RETIRED_REMOVE = "Remove"
RETIRED_NAME = "Remove {skill} for {agent}"

# Connections.
LINK_LOST_STATE = "Link lost"
NOT_FOUND_STATE = "Not found on this computer"
CONNECT_BUTTON = "Connect"
DISCONNECT_BUTTON = "Disconnect"

# Details on a project, and the quiet way out at its foot.
VERSIONS_HEAD = "Skill versions"
VERSION_ITEM = "{skill} " + MIDDOT + " {version}"
VERSION_AGENT = ", {agent} has {version}"
REMOVE_PROJECT_LABEL = "Remove RadialRig from this project"

# The result line's three tones, and the mark each takes.
RESULT_DONE = setup_web.RESULT_DONE
RESULT_PARTLY = setup_web.RESULT_PARTLY
RESULT_FAILED = setup_web.RESULT_FAILED
RESULT_MARKS = setup_web.RESULT_MARKS

# The result lines a one-press change answers with (PLAN-0027 phase 1,
# prescriptions 6 to 8). Each is a line of `wording.md`'s "Result lines"
# sections. A line saying nothing was changed stands only where the
# engine refused, or failed and put back everything it had written
# (`_untouched`); a press that changed something and then stopped, or
# changed less than asked, takes one of the lines that say what it did.
SKILL_ADDED = "Added {skill} for {agent}. Start a new agent session to pick it up."
SKILL_UPDATED = "Updated {skill} for {agent}. Start a new agent session to pick it up."
SKILL_REMOVED = (
    "Removed {skill} for {agent}. Start a new agent session to pick up the change.")
SKILL_REMOVED_KEPT = "Removed unedited files for {skill} in {agent}. Kept your edited file."
SKILL_REMOVED_KEPT_MANY = (
    "Removed unedited files for {skill} in {agent}. Kept {count} edited files.")
SKILL_KEPT_UNTRACKED = (
    "Kept your edited file for {skill} in {agent}. RadialRig no longer tracks "
    "the skill there. Details names the file.")
SKILL_KEPT_UNTRACKED_MANY = (
    "Kept your {count} edited files for {skill} in {agent}. RadialRig no longer "
    "tracks the skill there. Details lists them.")
SKILL_REMOVED_FOLDER = (
    "Removed {skill} for {agent}, but couldn't delete its folder. Details names it.")
ALL_ADDED = "Added all skills for {agent}. Start a new agent session to pick them up."
ALL_ADDED_KEPT = (
    "Added the missing skills for {agent} and kept your edited {skill}. Start a "
    "new agent session to pick them up.")
ALL_ADDED_KEPT_MANY = (
    "Added the missing skills for {agent} and kept your {count} edited skills. "
    "Start a new agent session to pick them up.")
SKILL_ADD_FAILED = "Couldn't add {skill}. Nothing was changed."
SKILL_REMOVE_FAILED = "Couldn't remove {skill}. Nothing was changed."
SKILL_UPDATE_FAILED = "Couldn't update {skill}. Nothing was changed."
ALL_FAILED = "Couldn't add all skills for {agent}. Nothing was changed."
SKILL_ADD_STOPPED = (
    "Couldn't finish adding {skill} for {agent}. Some of its files may be left "
    "changed. Check Details for the list.")
SKILL_REMOVE_STOPPED = (
    "Couldn't finish removing {skill} for {agent}. Some of its files may be "
    "left changed. Check Details for the list.")
SKILL_UPDATE_STOPPED = (
    "Couldn't finish updating {skill} for {agent}. Some of its files may be "
    "left changed. Check Details for the list.")
ALL_STOPPED = (
    "Couldn't finish adding all skills for {agent}. Some of their files may be "
    "left changed. Check Details for the list.")
SKILL_UPDATE_EDITED = (
    "Kept your edited copy of {skill} for {agent}, so it wasn't updated. Nothing "
    "was written.")
ALREADY_ON = "{skill} is already on for {agent}."
ALREADY_OFF = "{skill} is already off for {agent}."
ALL_ALREADY_ON = "Every skill is already on for {agent}."

CONNECTED_SAID = setup_web.CONNECTED_SAID
DISCONNECTED_SAID = (
    "Disconnected {label}. Fully quit and reopen {label} to apply the change.")
RECONNECTED_SAID = (
    "Reconnected {label}. Fully quit and reopen {label} to use the connection.")
DISCONNECTED_HELD = (
    "Disconnected {label}, but one settings file still holds this project's "
    "entry. Check Details to see which one.")
DISCONNECTED_HELD_MANY = (
    "Disconnected {label}, but {count} settings files still hold this project's "
    "entry. Check Details to see which ones.")
DISCONNECTED_RECORD = (
    "Removed this project from {label}'s settings, but couldn't delete its "
    "connection record. The link will show as lost. Check Details for the "
    "record.")
CONNECT_FAILED = setup_web.CONNECT_FAILED_SAID
DISCONNECT_FAILED = "Couldn't disconnect {label}. Nothing was changed."
CONNECT_STOPPED = setup_web.CONNECT_STOPPED_SAID
DISCONNECT_STOPPED = (
    "Couldn't finish disconnecting {label}. Some of its settings may be left "
    "changed. Check Details for the list.")
ALREADY_CONNECTED = "{label} is already connected."
ALREADY_DISCONNECTED = "{label} is already disconnected."

# Repair, on its own project's screen.
REPAIR_FAILED = "Couldn't repair the {label} connection. Nothing was changed."
REPAIR_NO_RECONNECT = (
    "Removed the old {label} entry, but couldn't connect again. This project "
    "isn't connected now. Press Connect to try again.")
REPAIR_NO_RECONNECT_RECORD = (
    "Removed this project's old connection record, but couldn't connect again. "
    "This project isn't connected now. Press Connect to try again.")
REPAIR_RECORD = (
    "Removed the old entry from {label}'s settings, but couldn't delete this "
    "project's connection record. The repair stopped. Check Details for the "
    "record.")
REPAIR_STALE = (
    "Removed this project's connection record, but couldn't connect again. "
    "{label}'s settings still hold an older RadialRig entry for it. This "
    "project isn't connected now. Check Details for the entry, remove it, then "
    "press Connect.")
REPAIR_STOPPED = (
    "Couldn't finish repairing the {label} connection. Some of its settings may "
    "be left changed. Check Details for the list.")
NOTHING_TO_REPAIR_NOW = (
    "This project's {label} link no longer needs repair. Nothing was changed.")

# Repair, from a row of Projects: the same results, naming the project.
ROSTER_REPAIRED = (
    "Reconnected {project} to {label}. Fully quit and reopen {label} to use it.")
ROSTER_REPAIR_FAILED = "Couldn't repair {project}. Nothing was changed."
ROSTER_REPAIR_NO_RECONNECT = (
    "Removed the old {label} entry for {project}, but couldn't connect again. "
    "Press Connect on its screen to try again.")
ROSTER_REPAIR_NO_RECONNECT_RECORD = (
    "Removed the old connection record for {project}, but couldn't connect "
    "again. Press Connect on its screen to try again.")
ROSTER_REPAIR_RECORD = (
    "Removed {project}'s old entry from {label}'s settings, but couldn't delete "
    "its connection record. The repair stopped. Check Details for the record.")
ROSTER_REPAIR_STALE = (
    "Removed {project}'s connection record, but couldn't connect again. "
    "{label}'s settings still hold an older RadialRig entry for it. Check "
    "Details for the entry, remove it, then press Connect on its screen.")
ROSTER_REPAIR_STOPPED = (
    "Couldn't finish repairing {project}. Some of {label}'s settings may be "
    "left changed. Check Details for the list.")
ROSTER_REPAIR_WITHHELD = (
    "Couldn't repair {project}'s {label} link right now. Nothing was changed. "
    "Check Details to see why.")
ROSTER_NOTHING_TO_REPAIR = (
    "{project}'s {label} link no longer needs repair. Nothing was changed.")

# The after-press warnings (BUG-0018, Windows), for a press that wrote the
# client's settings while it was open. The engine's own after-run line
# names a command to run again; on these screens the way back is a
# button, so these say which.
RUNNING_AFTER_CONNECT = (
    "{label} is running and may undo this change. Fully quit it and reopen it, "
    "then check this project: if its link shows as lost, press Repair.")
RUNNING_AFTER_REPAIR_ROSTER = (
    "{label} is running and may undo this change. Fully quit it and reopen it, "
    "then check {project}: if its link shows as lost, press Repair.")
RUNNING_AFTER_REMOVED = (
    "{label} is running and may put back what this removed. Fully quit it, then "
    "remove {keys} from its settings file if they're back.")
# After a clean up: the same press recovers an undo, since it needs no
# record, and Projects shows the row again if the client put it back.
RUNNING_AFTER_CLEAN_UP = (
    "{label} is running and may put the leftover entry back. Fully quit it and "
    "reopen it; if the entry is back on Projects, press Clean up again.")

# --- Confirm a big change (PLAN-0027 phase 3) --------------------------
# One card for Update, Update all and Remove from this project: the
# question, one sentence of counts, only the rows that need attention, a
# button naming the action, and Cancel; every file and every command in
# order behind Details. Each line is `wording.md`'s, Screen 5.

CANCEL_LABEL = setup_web.CANCEL_LABEL

# The counts a sentence is built from.
FILES_ONE = "1 file"
FILES_MANY = "{count} files"
SKILLS_ONE = setup_web.SKILLS_ONE
SKILLS_MANY = setup_web.SKILLS_MANY
PROJECTS_ONE = "1 project"
PROJECTS_MANY = "{count} projects"
BOTH = "{files} and {skills}"

UPDATE_HEAD = "Update {project}?"
UPDATE_ALL_HEAD_ONE = "Update 1 project?"
UPDATE_ALL_HEAD_MANY = "Update {count} projects?"
UPDATE_ALL_HEAD_NONE = "Update all projects?"
WILL_UPDATE = "{what} will be updated. Your edits stay in place."
UPDATE_LABEL = "Update project"
UPDATE_ALL_LABEL = UPDATE_ALL_BUTTON
UPDATE_REFUSED = (
    "RadialRig can't update this project right now. Check Details to see "
    "what's in the way.")
UPDATE_NOT_SET_UP = (
    "RadialRig is not set up in this project, so there's nothing to update.")
UPDATE_NOTHING = "This project is already up to date."
# Nothing to write, but the engine still leaves something for review (an
# edited file kept, an unreadable skill record): the rows say what, so the
# card never calls the project up to date (phase 3 review R1).
UPDATE_KEPT_ONLY = "Nothing will be updated. RadialRig leaves what's listed below as it is."
UPDATE_ALL_NOTHING = "Every project is already up to date."
UPDATE_ALL_OTHERS = "Every other project is already up to date."
UPDATE_ALL_SKIPS = (
    "RadialRig can't update {project} right now, so Update all will skip it. "
    "Check Details to see why.")
UPDATE_ALL_STUCK = (
    "RadialRig can't update {project} right now. Check Details to see why.")

# An attention row's words: a file the update or the removal leaves, by
# why it leaves it. Only a file the person edited is "Keeping your edits".
KEEPING_EDITS = "Keeping your edits"
KEPT_WITH_PARTNER = "Kept with your edited {partner}"
KEPT_REMOVED = "You removed it, so it stays removed"
KEPT_OWN = "Your own file is already here, so it stays as it is"
KEPT_UNREAD = "RadialRig can't read it, so it stays as it is"
KEPT_OTHER = "RadialRig leaves it as it is"
KEPT_AGENT = (
    "RadialRig can't read the skill record for {agent}, so its skills won't be "
    "updated")

# Details' tags, one per file.
TAG_UP_TO_DATE = "up to date"
TAG_WILL_UPDATE = "will update"
TAG_KEEPING = "keeping your edits"
TAG_WILL_ADD = "will add"
TAG_KEPT_AS_IS = "kept as it is"
TAG_ALREADY_GONE = "already gone"         # a path a removal plan finds absent (review R9)

REMOVE_HEAD = "Remove RadialRig from {project}?"
REMOVE_LABEL = "Remove from this project"
REMOVES_ALL = (
    "Removes the project files, {skills}, and the {label} connection. Your own "
    "files stay in place.")
REMOVES_FILES_SKILLS = (
    "Removes the project files and {skills}. Your own files stay in place.")
REMOVES_FILES_CONNECTION = (
    "Removes the project files and the {label} connection. Your own files stay "
    "in place.")
REMOVES_FILES = "Removes the project files. Your own files stay in place."
REMOVES_UNMOUNTED_BOTH = (
    "Removes {skills} and the {label} connection. Your own files stay in place.")
REMOVES_UNMOUNTED_SKILLS = "Removes {skills}. Your own files stay in place."
REMOVES_UNMOUNTED_CONNECTION = "Removes the {label} connection."
# A lost link has no disconnect step, as today's removal has none: the
# removal leaves the client's settings and the project's own record.
REMOVES_LOST_TAIL = (
    "{label} already lost its link to this project, so this leaves its "
    "settings alone and keeps this project's connection record. To take the "
    "record out too, repair the link first, then remove.")
# The same where Repair is withheld: the filled disconnect is behind
# Details (phase 3 review R5).
REMOVES_LOST_RUN = (
    "{label} already lost its link to this project, so this leaves its "
    "settings alone and keeps this project's connection record. RadialRig "
    "can't repair the link from here, so to take the record out too, run the "
    "command in Details.")
KEEPS_BOTH = (
    "Removes the project files. Your skills, {label} connection, and own files "
    "stay in place.")
KEEPS_SKILLS = "Removes the project files. Your skills and own files stay in place."
KEEPS_CONNECTION = (
    "Removes the project files. Your {label} connection and own files stay in "
    "place.")
KEEP_BOTH_LABEL = "Keep the skills and {label} connection"
KEEP_SKILLS_LABEL = "Keep the skills"
KEEP_CONNECTION_LABEL = "Keep the {label} connection"
REMOVE_REFUSED = (
    "RadialRig can't remove itself from this project right now. Check Details "
    "to see what's in the way.")
REMOVE_NOTHING = "There's nothing of RadialRig left in this project."
REMOVE_RECORD_ONLY = (
    "Only this project's connection record is left. Repair the link first, "
    "then remove it.")
REMOVE_RECORD_RUN = (
    "Only this project's connection record is left, and RadialRig can't repair "
    "the link from here. To take the record out, run the command in Details.")

# A removal's `{skills}`: how many, and for which agents. The agents'
# labels arrive from the harness map.
SKILLS_FOR_BOTH = "{skills} for both agents"
SKILLS_FOR_EVERY = "{skills} for every agent"
SKILLS_FOR = "{skills} for {agent}"
SKILLS_ALSO = "{count} for {agent}"

# The results these presses answer with (wording.md, Screens 2, 3 and 6).
# A slot `{this}` reads "this project" on its own screen and the project's
# name elsewhere, `{this_s}` the same with "'s", and `{from_project}` is
# empty on the project's screen and " from {project}" elsewhere.
THIS_PROJECT = "this project"
THIS_PROJECTS = "this project's"
FROM_PROJECT = " from {project}"

UPDATED_SAID = "Updated {what}."
UPDATED_IN = "Updated {what} in {where}."
KEPT_EDIT_ONE = "Kept your edited file."
KEPT_EDIT_MANY = "Kept {count} edited files."
UPDATE_CURRENT_NOW = "{This} is already up to date. Nothing was changed."
UPDATE_KEPT_NOW = (
    "Nothing in {this} was updated. RadialRig left what Details lists as it is.")
UPDATE_UNMOUNTED_NOW = (
    "RadialRig isn't set up in {this} now, so there was nothing to update.")
UPDATE_FAILED = (
    "Couldn't update {this}. Nothing was changed. Check Details for what "
    "stopped it.")
UPDATE_STOPPED = (
    "Couldn't finish updating {this}. Some of its files may be left changed. "
    "Check Details for the list.")
UPDATE_HALF_STOPPED = (
    "Updated {files}{in_project}, but couldn't update the skills. Check Details "
    "for what stopped them.")
HALF_LEFT_CHANGED = "Some of their files may be left changed."
AGENT_UNREAD = (
    "Couldn't read the skill record for {agent}, so its skills weren't updated. "
    "Details names it.")
UPDATE_ALL_CURRENT_NOW = "Every project is already up to date. Nothing was changed."
UPDATE_ALL_KEPT_NOW = "Nothing was updated. RadialRig left what Details lists as it is."
UPDATE_ALL_SOME_ONE = (
    "Updated {what} in {projects}, but couldn't update {project}. Check Details "
    "for what stopped it.")
UPDATE_ALL_SOME_MANY = (
    "Updated {what} in {projects}, but couldn't update {count} projects. Check "
    "Details for what stopped them.")
ITS_LEFT_CHANGED = "Some of its files may be left changed."
UPDATE_ALL_FAILED_ONE = (
    "Couldn't update {project}. Nothing was changed. Check Details for what "
    "stopped it.")
UPDATE_ALL_FAILED_MANY = (
    "Couldn't update {count} projects. Nothing was changed. Check Details for "
    "what stopped them.")
UPDATE_ALL_STOPPED_ONE = (
    "Couldn't finish updating {project}. Some of its files may be left changed. "
    "Check Details for the list.")
UPDATE_ALL_STOPPED_MANY = (
    "Couldn't update {count} projects. Some of their files may be left changed. "
    "Check Details for the list.")
UPDATE_ALL_HALF = (
    "Updated {what} in {projects}, but couldn't update the skills in {project}. "
    "Check Details for what stopped them.")

REMOVED_ALL = (
    "Removed RadialRig files, skills, and the {label} connection{from_project}. "
    "Your own files stayed in place. Fully quit and reopen {label} to apply the "
    "change.")
REMOVED_FILES_SKILLS = (
    "Removed RadialRig files and skills{from_project}. Your own files stayed in "
    "place.")
REMOVED_FILES_CONNECTION = (
    "Removed RadialRig files and the {label} connection{from_project}. Your own "
    "files stayed in place. Fully quit and reopen {label} to apply the change.")
REMOVED_FILES_ONLY = "Removed RadialRig from {project}. Your own files stayed in place."
REMOVED_BOTH_UNMOUNTED = (
    "Removed the skills and the {label} connection{from_project}. Your own files "
    "stayed in place. Fully quit and reopen {label} to apply the change.")
REMOVED_SKILLS_ONLY = "Removed the skills{from_project}. Your own files stayed in place."
REMOVED_CONNECTION_ONLY = (
    "Removed the {label} connection{from_project}. Fully quit and reopen {label} "
    "to apply the change.")
KEPT_BOTH_SAID = (
    "Removed the project files{from_project}. Kept the skills, the {label} "
    "connection, and your own files.")
KEPT_SKILLS_SAID = (
    "Removed the project files{from_project}. Kept the skills and your own files.")
KEPT_CONNECTION_SAID = (
    "Removed the project files{from_project}. Kept the {label} connection and "
    "your own files.")
# On a lost link: what went, then what stayed and how to clear it. The
# remedy is the one the screen the person lands on can keep: Repair where
# it is offered, and the record's place where it is withheld.
REMOVED_FILES_AND_SKILLS = "Removed the project files and skills"
REMOVED_FILES_PART = "Removed the project files"
REMOVED_SKILLS_PART = "Removed the skills"
REMOVED_LOST = (
    "{label} had already lost its link, so RadialRig left its settings alone and "
    "kept {this_s} connection record. {remedy}")
LOST_REMEDY = "To take it out, press Repair, then remove RadialRig again."
LOST_REMEDY_ELSEWHERE = (
    "To take it out, open {project}, press Repair, then remove RadialRig again.")
LOST_NO_REMEDY = (
    "RadialRig can't repair the link from here, so the record stays. Details "
    "names it.")
# Where Repair is withheld and the record names a client this build knows,
# the filled disconnect takes the record out, as the old removal page's
# mismatch note gave it; Details holds it (phase 3 review R5).
LOST_RUN_REMEDY = (
    "RadialRig can't repair the link from here. To take the record out, run the "
    "command in Details.")
HELD_ONE = (
    "One {label} settings file still holds {this_s} entry. Check Details to see "
    "which one.")
HELD_MANY = (
    "{count} {label} settings files still hold {this_s} entry. Check Details to "
    "see which ones.")
# A disconnect that took the entry out of no settings file dropped only the
# project's record, so the connection isn't said to be removed (review R6).
HELD_RECORD_ONE = (
    "RadialRig removed {this_s} connection record but left its entry in one "
    "{label} settings file. Check Details to see which one.")
HELD_RECORD_MANY = (
    "RadialRig removed {this_s} connection record but left its entry in {count} "
    "{label} settings files. Check Details to see which ones.")
REMOVE_FAILED = (
    "Couldn't remove RadialRig from {this}. Nothing was changed. Check Details for "
    "what stopped it.")
REMOVE_STOPPED = (
    "Couldn't finish removing RadialRig from {this}. Some of it was removed and "
    "the rest is still here. Check Details for what stopped it.")
RESTART_SAID = "Fully quit and reopen {label} to apply the change."
REMOVE_WITHHELD = (
    "RadialRig can't remove itself from {this} right now. Nothing was changed. "
    "Check Details to see what's in the way.")
REMOVE_NOTHING_NOW = "There's nothing of RadialRig left in {this}. Nothing was changed."
RECORD_ONLY_NOW = (
    "Only {this_s} connection record is left, so nothing was changed. Repair the "
    "link first, then remove it.")
RECORD_RUN_NOW = (
    "Only {this_s} connection record is left, so nothing was changed. "
    + LOST_RUN_REMEDY)
FILES_FAILED = (
    "Couldn't remove the project files{from_project}. Nothing was changed. Check "
    "Details for what stopped it.")
FILES_RECORD = (
    "Removed the project files{from_project}, but couldn't delete RadialRig's "
    "record of them, so {this} still shows as set up. Check Details for the "
    "record.")
FILES_STOPPED = (
    "Couldn't finish removing the project files{from_project}. Some of them may "
    "be left changed. Check Details for the list.")
FILES_GONE_NOW = (
    "RadialRig's project files are already gone from {this}. Nothing was "
    "changed.")

# Clean up, from a row of Projects.
CLEANED = (
    "Removed the leftover {label} connection for {project}. Fully quit and "
    "reopen {label} to apply the change.")
CLEAN_FAILED = (
    "Couldn't remove the leftover connection for {project}. Nothing was changed.")
CLEAN_PARTLY = (
    "Removed the leftover entry from one {label} settings file. Another couldn't "
    "be read and may still hold it; Details names it.")
CLEAN_STOPPED = (
    "Removed some of {project}'s leftover {label} entries, but couldn't remove "
    "the rest. Fully quit and reopen {label} to apply the change. Check Details "
    "for what stopped it.")
CLEAN_BACK = (
    "{project}'s folder is back on this computer, so its {label} entries aren't "
    "leftovers now. Nothing was changed.")
CLEAN_GONE = "{project}'s leftover {label} entries are already gone. Nothing was changed."

# A changed path's tag in a result line's Details, by what the engine did
# to it. A row that changed nothing (already as wanted, already gone) is
# not listed.
TAG_ADDED = setup_web.TAG_ADDED
TAG_REMOVED = setup_web.TAG_REMOVED
TAG_UPDATED = setup_web.TAG_UPDATED
TAG_KEPT = setup_web.TAG_KEPT
TAGS = setup_web.TAGS


# --- the terminal lines this command prints ---------------------------

OPENED = "radialrig manage: the page is open in your browser."
NOT_OPENED = "radialrig manage: open the URL above to reach the page."
LOCAL_ONLY = (
    "The server is bound to 127.0.0.1, answers this one link, and stops when "
    "the page closes. Ctrl-C stops it now."
)
STOPPED_LINE = "radialrig manage: the local server has stopped."


# --- the session ------------------------------------------------------

class ManageSession(setup_web.Session):
    """One `radialrig manage` run: its tokens, its lock, its sub-sessions.

    The inherited session is built around one project; this one is built
    around the machine, so the inherited root is a placeholder and is
    never read. What it does inherit is the whole token and lifecycle
    apparatus, which is the same apparatus either page needs.
    """

    page_path = MANAGE_PATH

    def __init__(self) -> None:
        super().__init__(Path("."), "", "")
        self.outcome = report.OK
        # One consequential action at a time. The command line runs one
        # verb at a time and the engine is written for that; two tabs on
        # a threading server are not, so they queue here.
        self.actions = threading.Lock()
        # One embedded walkthrough per project root, keyed by
        # `os.path.normcase` of the resolved root (Phase 4 task 4).
        self.setups: dict[str, setup_web.Session] = {}

    def record(self, outcome: int, *, consequential: bool = False) -> None:
        """The manage page's exit code is 0 and stays 0.

        The inherited session carries the outcome of the one walkthrough
        it ran, because that walkthrough was the whole command. This page
        is a standing surface: a refused action is something the reader
        reads and answers, not a verdict on the session, and an exit code
        that changed with the last thing pressed would tell Phase 5's app
        that the page failed. The states say what happened.
        """
        self.outcome = report.OK


class _Walkthrough(setup_web.Session):
    """One `radialrig setup` walkthrough, embedded in the manage page.

    A setup session in every respect that matters — its own project, its
    own fields, its own outcome — with two things this page's rather than
    its own:

    - the token. One page, one token: the manage session's token gates
      every request already, and this session's forms carry that one, so
      there is no second scheme to reason about and nothing to scope.
    - the form target. It posts back to the manage route, naming its own
      project in the query, so a press reaches the walkthrough it was
      drawn for even where two folders are open at once. The route is the
      manage route either way; what the query adds is which of them.
    """

    def __init__(self, session: ManageSession, root: Path) -> None:
        super().__init__(root, mnt.default_scope(root), mnt.DEFAULT_OWNER)
        self.token = session.token
        self.page_path = f"{MANAGE_PATH}?{urlencode({'root': str(root)})}"


def walkthrough(session: ManageSession, root: Path) -> setup_web.Session:
    """This folder's walkthrough, made once and kept.

    Kept because a walkthrough carries what a reader typed — the scope,
    the owner, the boxes — and a reader who goes back to the listing and
    returns should find them as they were left.
    """
    key = os.path.normcase(str(root))
    sub = session.setups.get(key)
    if sub is None:
        sub = session.setups.setdefault(key, _Walkthrough(session, root))
    return sub


# --- the states -------------------------------------------------------

def _tails(rows) -> tuple[str, ...]:
    """The projects report's empty-roster tail, under its own rule.

    It prints only where it applies: where there is no row. The report's
    other tail, the two-verb remedy with its placeholders, stays on the
    command line: since PLAN-0014 phase 3 the listing row's chip and
    sentence say which client and what to do, and the project's page
    writes the remedy out with the client and the path filled in.
    """
    if not rows:
        return (report.PROJECTS_NONE,)
    return ()


def roster_state(session: ManageSession | None = None) -> setup_web.PageState:
    """What this machine carries right now.

    Writes nothing of its own: `listing` drops entries whose project is
    gone, which is the same best-effort rewrite `radialrig projects`
    makes, and a roster this build cannot read is read as empty and left
    exactly as it stands.

    The session is never read — this view is the machine's rather than one
    project's — so it may be left out, which is what lets a press that
    named no project at all answer with the listing instead.
    """
    roster = ro.load()
    states = _with_behind(ro.listing(roster=roster))
    found = ro.orphans()
    rows = ro.report_rows(states, found)
    notice = "" if roster.writable else UNREADABLE_NOTICE.format(
        path=ro.roster_path())
    return setup_web.PageState(
        ROSTER, report.OK, False,
        dispositions=rows,
        # The report's tail and not its count line. The command line's
        # head counts what it is about to print and stops; this page keeps
        # the listing on screen, where the rows are the count.
        lines=_tails(rows),
        result=(states, found),
        message=notice,
    )


def _with_behind(states) -> tuple[ro.ProjectState, ...]:
    """The same states, each knowing whether its update would write
    something (PLAN-0021 phase 1, Decided 6).

    Asked of the projects standing on this disk and mounted; the rest
    are not behind, because the upgrade does nothing there. The answer
    never raises, so a project whose records this build cannot read
    costs the listing nothing, as the other facts already cost nothing.
    """
    return tuple(
        dataclasses.replace(state, behind=update.behind(Path(state.root)))
        if state.exists and state.mounted else state
        for state in states
    )


def _named(root: str) -> bool:
    """Whether a posted field names a folder this page can act on at all.

    Absolute or nothing. A field left empty names no folder, and `Path("")`
    is `Path(".")`; a relative path names one only by way of the directory
    this server was started in, which is the one place a management page
    must never wander into. The roster holds absolute roots and every
    control on this page carries one, so a root that is not absolute
    arrived from somewhere other than this page and names nothing on it.
    """
    return bool(root) and Path(root).is_absolute()


def _resolved(root: str) -> Path | None:
    """One posted folder, resolved as `cli.main` resolves it, or None.

    The absolute check above is the load-bearing half. `Path(".")` is a
    directory, `.is_dir()` says yes, and `.resolve()` answers the directory
    this server was started in — so a request that arrived naming `.`, or
    `..`, or nothing at all would read that directory as a project, and a
    press over it would write there and then record it in the roster.

    Every root this page reads comes through here — the project view, the
    walkthrough, and every press — so there is one place that can be true.
    """
    if not _named(root):
        return None
    target = Path(root)
    if not target.is_dir():
        return None
    return target.resolve()


def _missing(root: str) -> setup_web.PageState:
    """A folder that is not on this disk, said as a navigation.

    Two things, in the order a reader meets them: this page's own sentence
    about what just happened — nothing was pressed, so the press-failure
    head would be describing a press that never was — and beneath it the
    command line's own sentence naming the path.

    A field that names no folder this page can act on answers with the
    listing instead, where a reader picks a project the roster actually
    names. That is the only true answer for a relative path as much as for
    an empty one: `.` is a directory, so the command line's sentence about
    a directory that is not there would be saying something false of it.
    """
    if not _named(root):
        return roster_state()
    return setup_web.PageState(
        FAILED, report.ERROR, False, lines=(MISSING_HEAD,),
        message=NO_DIRECTORY.format(path=root))


@dataclasses.dataclass(frozen=True)
class _Project:
    """A project root, in the shape `setup_web._connect_client` reads.

    That function asks one question — is there a client on this machine
    this project is not registered with — off the root alone. Handing it
    this rather than restating the question keeps one gate for both pages,
    so no press that writes to a client is offered on either where that
    client is not detected (PLAN-0014 constraint 7).
    """

    root: Path


def _repair_client(facts: ro.ProjectState):
    """The client the repair press would write to, or None.

    The gate `setup_web._connect_client` cannot serve: that one answers
    None whenever the record names the client, which a mismatched record
    does. So this asks the same three questions the connect offer asks,
    of the client the record claims rather than the default one — the
    name is in the client table, `detect` finds that client on this
    machine, and the read server's command can be located — and answers
    None where any fails. A client the record names but this machine
    does not carry is still named in the sentence about the record; it
    is offered no press (constraint 7, narrowed by phase 3). A profile
    directory that cannot be read fails the second question, as it does
    for the connect offer.
    """
    if facts.connected != ro.CONNECTED_MISMATCH:
        return None
    client = cn.CLIENTS.get(facts.claimed)
    if client is None:
        return None
    try:
        if not cn.detect(client):
            return None
        cn.read_command()
    except (cn.ConnectError, OSError):
        return None
    return client


def project_state(session: ManageSession, root: str) -> setup_web.PageState:
    """One project's own records, and what can still be done to it.

    The facts are `roster.verify`'s, so the row here says what the row on
    the listing says. The offers are gated on those facts, which is what
    keeps the page from offering a registration to a project that already
    carries one — but the gate is an offer and never a guard: the engine
    functions themselves refuse what they refuse, exactly as they do for
    a command line that was handed the same arguments.

    `client` is the one client this page offers a press that writes to:
    on a mismatched record, the client the repair would write to; on any
    other project, the client a registration could be made with. Both
    gates ask the same three questions of the machine, and None means
    the section offers no such press.

    The folder comes through the one guard every press uses, so the root
    this view reads its facts from and names in its offers is the root a
    press would act on — and a field naming no project falls back to the
    listing rather than opening this server's own directory as one.
    """
    target = _resolved(root)
    if target is None:
        return _missing(root)
    (facts,) = _with_behind((ro.verify(target),))
    try:
        # Every skill for every agent, agent by agent in map order: the
        # two lists are drawn from this, one per agent, with the catalog's
        # skills first and then any name that agent's record still lists
        # (PLAN-0015 phase 2). The setup page's groups of boxes read the
        # same states (phase 3); the fold that showed one state per skill
        # across agents is gone.
        catalog = sk.states_all(target)
    except sk.SkillError as exc:
        # The same wall `radialrig skill list` hits on this project.
        return setup_web.PageState(FAILED, report.ERROR, False, message=str(exc))
    if _needs_repair(facts):
        client = _repair_client(facts)
    else:
        client = setup_web._connect_client(_Project(target))
    return setup_web.PageState(
        PROJECT, report.OK, False,
        dispositions=ro.report_rows((facts,), ()),
        result=facts,
        catalog=catalog,
        client=client,
    )


# --- the full-removal flow --------------------------------------------
# One project's reversal is the three verbs that undo the three verbs the
# walkthrough runs, in the reverse order: the registration first, because
# a client pointing at a project that no longer carries the seed is the
# one state a reader cannot see; then each installed skill; then the
# mount. Nothing here is new engine — every step is one of the presses
# above, with the command it already names.


def reversal_steps(facts) -> tuple[Action, ...]:
    """Every verb that reverses this project, in the order they run.

    Gated on the project's own records, the way the project view's offers
    are: a registration is named only where `roster.verify` read the
    record and the client's own copies agreeing, so a mismatch has no
    registration step here. Confirm a big change says what that leaves,
    the project's own connection record, and that repairing the link
    first takes it out too (PLAN-0027 phase 3 prescription 2).
    """
    root = facts.root
    out: list[Action] = []
    client = cn.CLIENTS.get(facts.connected)
    if client is not None:
        out.append(Action(UNCONNECT, root=root, client=client.name))
    out.extend(Action(SKILL_REMOVE, root=root, skill=name) for name in facts.skills)
    if facts.mounted:
        out.append(Action(UNMOUNT, root=root))
    return tuple(out)


def repair_steps(facts) -> tuple[Action, ...]:
    """The two verbs that repair a mismatched record, in the order they run.

    The registration's removal first — on a mismatched record it answers
    partial, the entry being already gone, and drops the record — then a
    fresh registration with the same client, which writes the entry and
    a record that describes it. Both are presses the table above already
    carries, filled with the client the record claims and this root, so
    the command beside each is the one a reader would type. No steps at
    all where the record is not a mismatch, or names no client this
    build knows: there is nothing to repair, or no client to repair with.
    """
    client = cn.CLIENTS.get(facts.claimed)
    if facts.connected != ro.CONNECTED_MISMATCH or client is None:
        return ()
    return (
        Action(UNCONNECT, root=facts.root, client=client.name),
        Action(CONNECT, root=facts.root, client=client.name),
    )


def removal_state(session: ManageSession) -> setup_web.PageState:
    """Every listed project's reversal, and what would still stand after.

    Writes nothing, the way every view does. A project whose directory is
    gone carries no reversal — there is nothing on this disk to reverse —
    so the blocks are drawn from the projects the listing found standing.
    """
    roster = ro.load()
    states = ro.listing(roster=roster)
    found = ro.orphans()
    plans = tuple(
        (facts, reversal_steps(facts)) for facts in states if facts.exists)
    notice = "" if roster.writable else UNREADABLE_NOTICE.format(
        path=ro.roster_path())
    return setup_web.PageState(
        REMOVAL, report.OK, False,
        result=(plans, found),
        message=notice,
    )


# --- the embedded walkthrough -----------------------------------------
# A folder nothing has been done to yet is not a project this page can act
# on; it is a project `radialrig setup` walks onto the engine. So the page
# embeds that walkthrough rather than restating it: the states are
# `setup_web.compute_state`'s, the presses are `setup_web.perform_mount`
# and `perform_skills`, and the bodies are that page's own builders. What
# this module adds is where the walkthrough lives and how a press finds
# the folder it belongs to.


def _walk_state(session: ManageSession, root: str):
    """The walkthrough's state for a folder, and the session it belongs to.

    Two values, because the bodies that render this state are the setup
    page's own and they read the session they were written for. A folder
    that is not on this disk answers in the command line's own words and
    starts no walkthrough. The folder comes through the one guard every
    press uses, so no walkthrough is ever opened on a folder a press would
    refuse to act on.
    """
    target = _resolved(root)
    if target is None:
        return _not_found(session, root), None
    if _set_up(target):
        # A folder already set up opens its own project screen, whatever
        # it still lacks: that screen offers the rest (PLAN-0027 phase 2
        # prescription 3), and no walkthrough is opened on it.
        return project_state(session, str(target)), None
    sub = walkthrough(session, target)
    return setup_web.compute_state(sub), sub


def _set_up(target: Path) -> bool:
    """Whether the folder carries its setup record, as `radialrig setup`
    asks before it draws anything."""
    try:
        return mnt.is_mounted(target)
    except OSError:
        return False


# Add a project's own answer for a folder that is not there.
SETUP_MISSING = "setup-missing"


def _not_found(session: ManageSession, root: str) -> setup_web.PageState:
    """Add a project, for a path that is not a folder on this disk.

    An absolute path that is not a directory, whether nothing is there or
    a file is, gets the screen's own card, with the folder field to
    choose another. A path that names nothing this page can act on,
    relative or in the quotes a copied path arrives in, comes back to
    Projects, which says so in one line; a field left empty comes back
    with no line, since nothing was typed.
    """
    if _named(root):
        return setup_web.PageState(
            SETUP_MISSING, report.ERROR, False, lines=(NOT_FOUND_HEAD,),
            message=root)
    if not root.strip():
        return roster_state(session)
    return setup_web.PageState(
        ANSWERED, report.OK, False,
        result=_Answer(
            roster_state(session), None,
            result_line(RESULT_FAILED, PATH_UNUSABLE)))


class _Press:
    """The one attribute the setup page's POST tail reads off its handler.

    A walkthrough press runs that tail rather than a copy of it: the field
    parsing, the two performers and the order they run in are the setup
    page's own, so what this page writes for a press is what that page
    writes for the same press, and a change there needs no change here.
    """

    __slots__ = ("session",)

    def __init__(self, sub: setup_web.Session) -> None:
        self.session = sub


def perform_walkthrough(sub: setup_web.Session, fields) -> setup_web.PageState:
    """One press on an embedded walkthrough, through the setup page's own."""
    return setup_web.SetupHandler._apply(_Press(sub), fields)


# The states a setup press answers with once the folder is set up: the
# setup itself, and a set-up project's own press. Each lands on the
# project's screen with its one line.
_SET_UP_STATES = (setup_web.MOUNTED, setup_web.SKILLED, setup_web.CONNECTED,
                  setup_web.CONNECT_FAILED)


def setup_now(session: ManageSession, sub: setup_web.Session, fields) -> setup_web.PageState:
    """One press on Add a project, inside the app (PLAN-0027 phase 2).

    The press is the setup page's own (`perform_walkthrough`), unchanged,
    so what it writes is what `radialrig setup` writes for the same press.
    Where it leaves the folder set up, the app lands on the new project's
    screen with the press's one result line (prescription 3): the
    setup's, or a set-up project's own press's, which say what landed and
    what didn't (prescription 4). A folder the press found set up already
    opens its screen with no line. A refusal, or a setup that stopped
    before the folder was set up, stays on Add a project's card.

    The client's settings are read before the press, so the line carries
    the after-press warning where the press changed them while the client
    was open (prescription 5), asked as phase 1's presses ask it.
    """
    client = cn.client_for(cn.DEFAULT_CLIENT)
    before = _contents(_settings_files(client))
    press = perform_walkthrough(sub, fields)
    root = str(sub.root)
    if press.kind == setup_web.ALREADY:
        return _answered(session, PROJECT, root, press, "")
    if press.kind == setup_web.MOUNTED:
        said = setup_web.setup_said(sub, press, switch=False)
    elif press.kind in _SET_UP_STATES or (
            press.kind == setup_web.FAILED and press.missed):
        said = setup_web.added_said(sub, press, switch=False)
    else:
        return press
    label = (press.client or client).label
    warning = (RUNNING_AFTER_CONNECT.format(label=label)
               if _wrote_while_open(client, before) else "")
    line = result_line(said.tone, said.sentence, warning=warning,
                       changed=said.changed, notes=said.notes, more=said.more)
    return _answered(session, PROJECT, root, press, line)


# --- the actions ------------------------------------------------------
# Seven presses over the engine the verbs drive. Each one runs twice
# from one function: the plan press with `dry_run=True`, which is the
# engine's own rehearsal, and the confirm press without it. Writing them
# once is what makes the rehearsal the run it describes, the same reason
# `mount_plan` and `mount` share a composer.


def _reason(d: mnt.Disposition) -> str:
    """One wording for any row a press reports.

    The orphan kit's table first and the mount kit's underneath, which is
    what `orphan.reason_text` already is — and every other press reports
    rows carrying mount codes. So one resolver reads them all, and no row
    is ever rendered by its code. A code the engine leaves wordless takes
    the setup page's own table sentence, so no cell on a wrote-table is
    ever blank where the page has something true to say.
    """
    return setup_web._worded(d, orphan.reason_text)


def _drawn(line: str, dispositions) -> bool:
    """Whether one report line is a row the table already shows.

    A row is known by its shape rather than by its whole text: the verb
    padded to its nine-wide column, one space, then the path, and after
    the path either the end of the line or a space (`report.render_rows`
    writes nothing else there). The reason and the padding after the
    path are not read, because they are the two things a report and this
    page compose differently — a skill report renders its rows through
    the mount kit's reasons with one width per agent block, and the
    page draws them through its own resolver at one width — and a match
    on the whole text failed on every skill press, so every path printed
    once in the table and once beneath it (PLAN-0015 constraint 4). A
    path that merely begins with a shorter path does not match it: the
    character after the shorter one is then part of the longer, never a
    space.
    """
    for d in dispositions:
        # The path as the report writes it, record text escaped
        # (PLAN-0022 phase 3).
        head = f"{d.verb:<9} {mnt.one_line(d.path)}"
        if line.startswith(head) and (len(line) == len(head) or line[len(head)] == " "):
            return True
    return False


def _sentences(lines, dispositions) -> tuple[str, ...]:
    """The command line's report with its row block lifted out.

    The rows are drawn as a table rather than as padded text, so the lines
    that are rows are dropped here and every other line is kept exactly as
    the command line wrote it: a head, a tail, a diff, never a path the
    table shows.
    """
    return tuple(line for line in lines if line and not _drawn(line, dispositions))


def _root_of(act: Action) -> Path | None:
    """The project this press names, through the one guard above."""
    return _resolved(act.root)


def _failed(act: Action, message: str) -> setup_web.PageState:
    return setup_web.PageState(
        FAILED, report.ERROR, False, result=act, message=message)


def _stopped(act: Action, exc: Exception) -> setup_web.PageState:
    """A press a file error stopped, under the setup page's rule: a
    partial press, headed as one, when the writer's sentence names paths
    not put back; the plain failure otherwise. The skills kit's error
    carries that sentence and its cause (PLAN-0017 phase 2)."""
    if setup_web.stopped_outcome(exc) == report.PARTIAL:
        return setup_web.PageState(
            FAILED, report.PARTIAL, False, result=act,
            lines=(setup_web.PARTIAL_HEAD,), message=str(exc))
    return _failed(act, str(exc))


# A kit error as a page state, one ladder per kit. A stopped write is a
# `MountError` or a `ConnectError` too, so each ladder reads it first:
# read after them, a partial stop would show as a plain failure. A runner
# reads its own refusal before its kit's ladder.
_MOUNT_ERRORS = (mnt.MountError, cn.WriteStopped)
_CLIENT_ERRORS = (cn.ConnectError, mnt.WriteStopped)
_SKILL_ERRORS = (sk.SkillError, mnt.WriteStopped, cn.WriteStopped)


def _error_state(act: Action, exc: Exception) -> setup_web.PageState:
    """The mount kit's ladder: a stopped write under the setup page's
    rule, any other error as the plain failure."""
    if isinstance(exc, (mnt.WriteStopped, cn.WriteStopped)):
        return _stopped(act, exc)
    return _failed(act, str(exc))


def _client_error(act: Action, exc: Exception) -> setup_web.PageState:
    """The connect kit's ladder: a client this build does not know, said
    in the report's own lines, then the mount kit's rule."""
    if isinstance(exc, cn.UnknownClient):
        return _failed(act, "; ".join(
            report.connect_unknown_client_report(exc.name, exc.available)))
    return _error_state(act, exc)


def _skill_error(act: Action, exc: Exception, *, dry_run: bool) -> setup_web.PageState:
    """The skills kit's ladder: an unknown skill fails, a conflict
    refuses, and anything else stops the press under the setup page's
    rule, which reads a stopped write the kit's error carries."""
    if isinstance(exc, sk.UnknownSkill):
        return _failed(act, "; ".join(
            report.skill_unknown_report(exc.name, exc.available)))
    if isinstance(exc, sk.SkillConflict):
        return _refused(act, report.skill_conflict_report(
            exc.conflicts, dry_run=dry_run), exc.conflicts)
    return _stopped(act, exc)


def _refused(act: Action, lines, conflicts=()) -> setup_web.PageState:
    return setup_web.PageState(
        REFUSED, report.REFUSED, False,
        dispositions=tuple(conflicts),
        lines=_sentences(lines, conflicts),
        result=act,
    )


def _answer(act: Action, outcome: int, lines, dispositions=(), *,
            dry_run: bool) -> setup_web.PageState:
    return setup_web.PageState(
        PLAN if dry_run else DONE, outcome, False,
        dispositions=tuple(dispositions),
        lines=_sentences(lines, dispositions),
        result=act,
    )


def _no_directory(act: Action) -> setup_web.PageState:
    """A press whose folder is not one this disk carries.

    A press that named no folder this page can act on is not that: it
    named nothing, so it answers with the listing rather than with a
    sentence ending in a colon and stopping, or with one that says a
    directory is missing when it is standing. The rule is `_missing`'s,
    and for its reasons.
    """
    if not _named(act.root):
        return roster_state()
    return _failed(act, NO_DIRECTORY.format(path=act.root))


def _unmount(act: Action, *, dry_run: bool) -> setup_web.PageState:
    root = _root_of(act)
    if root is None:
        return _no_directory(act)
    try:
        answer = cli.unmount_step(root, dry_run=dry_run)
    except _MOUNT_ERRORS as exc:
        return _error_state(act, exc)
    return _answer(act, answer.outcome, answer.lines, answer.rows, dry_run=dry_run)


def _skill_added(act: Action, compose, render, *, dry_run: bool) -> setup_web.PageState:
    """One add press, whichever skills it names, answered in the report's
    own words.

    `compose` is the engine call — one skill for the agents the press
    names, or the whole catalog for them — and `render` the report
    builder for that press's shape; everything after it is the same:
    the refusals, the outcome and the rows are one kit's, so the single
    press and the whole-catalog press cannot answer the same state two
    ways. The agents are the press's own (`targets`): the one its list
    names, or every agent for a press naming none.
    """
    root = _root_of(act)
    if root is None:
        return _no_directory(act)
    try:
        results = compose(root, targets=act.targets)
    except _SKILL_ERRORS as exc:
        return _skill_error(act, exc, dry_run=dry_run)
    if all(r.current for r in results):
        outcome = report.NOOP
    else:
        outcome = report.PARTIAL if any(r.edited for r in results) else report.OK
    # The table draws what the press touched. A skill already current in
    # a report of several is its head line alone on the command line
    # and nothing at all on the whole-catalog page, so its rows are not
    # drawn either way; alone, it is the whole answer and keeps them.
    rows = [d for r in results
            if not (r.current and len(results) > 1) for d in r.dispositions]
    return _answer(act, outcome, render(results, dry_run=dry_run), rows,
                   dry_run=dry_run)


def _skill_add(act: Action, *, dry_run: bool) -> setup_web.PageState:
    compose = sk.add_all_plan if dry_run else sk.add_all
    return _skill_added(
        act, lambda root, *, targets: compose(root, act.skill, targets=targets),
        report.skill_add_reports, dry_run=dry_run)


def _skill_add_all(act: Action, *, dry_run: bool) -> setup_web.PageState:
    """Every packaged skill this project lacks, for the agent the press
    names: `radialrig skill add --all` narrowed (PLAN-0015 phase 2).
    One head line for the agent, not one per skill: the page has one
    heading, and it speaks for the whole press."""
    return _skill_added(
        act, sk.add_catalog_plan if dry_run else sk.add_catalog,
        report.skill_add_all_report, dry_run=dry_run)


def _skill_remove(act: Action, *, dry_run: bool) -> setup_web.PageState:
    root = _root_of(act)
    if root is None:
        return _no_directory(act)
    try:
        plans = [p for p in (sk.remove_all_plan if dry_run else sk.remove_all)(
            root, act.skill, targets=act.targets) if p.installed]
    except _SKILL_ERRORS as exc:
        return _skill_error(act, exc, dry_run=dry_run)
    if not plans:
        return _answer(act, report.NOOP,
                       report.skill_not_installed_report(root, act.skill),
                       dry_run=dry_run)
    return _answer(
        act, report.OK if all(p.complete for p in plans) else report.PARTIAL,
        report.skill_remove_reports(plans, dry_run=dry_run),
        [d for p in plans for d in p.dispositions],
        dry_run=dry_run,
    )


def _connect(act: Action, *, dry_run: bool) -> setup_web.PageState:
    root = _root_of(act)
    if root is None:
        return _no_directory(act)
    try:
        # `scope=None`, the bare verb's own argument: the engine reads the
        # name off this project's mount record rather than off the page.
        result = (cn.connect_plan if dry_run else cn.connect)(
            root, act.client, scope=None)
    except cn.ConnectRefused as exc:
        return _refused(act, report.connect_refused_report(
            exc.client, root, exc.conflicts, dry_run=dry_run), exc.conflicts)
    except _CLIENT_ERRORS as exc:
        return _client_error(act, exc)
    if not dry_run:
        ro.record_seen(root)
    return _answer(act, report.NOOP if result.current else report.OK,
                   report.connect_report(result, dry_run=dry_run),
                   result.dispositions, dry_run=dry_run)


def _unconnect(act: Action, *, dry_run: bool) -> setup_web.PageState:
    root = _root_of(act)
    if root is None:
        return _no_directory(act)
    try:
        answer = cli.unconnect_step(root, act.client, dry_run=dry_run)
    except _CLIENT_ERRORS as exc:
        return _client_error(act, exc)
    return _answer(act, answer.outcome, answer.lines, answer.rows, dry_run=dry_run)


def _orphan(act: Action, *, dry_run: bool) -> setup_web.PageState:
    """The entry whose project is gone. No root, so no side entry either."""
    try:
        answer = cli.orphan_step(act.client, act.key, dry_run=dry_run)
    except orphan.OrphanRefused as exc:
        return _refused(act, report.orphan_refused_report(
            str(exc), dry_run=dry_run))
    except _CLIENT_ERRORS as exc:
        return _client_error(act, exc)
    return _answer(act, answer.outcome, answer.lines, answer.rows, dry_run=dry_run)


def _update_rows(plan) -> list[mnt.Disposition]:
    """Both halves' rows, the seed's then each skill's, as the report
    prints them."""
    return [*plan.seed.dispositions(), *(d for r in plan.skills for d in r.dispositions)]


def _update(act: Action, *, dry_run: bool) -> setup_web.PageState:
    """One project's update (PLAN-0021 phase 1): `radialrig upgrade
    <folder>`, through the same combined step, answered in the report's
    own words and under the command line's own outcome. The state
    carries the plan, so the body draws each block of the report under
    its own head; the lines are the command line's whole report, which
    is what the log keeps."""
    root = _root_of(act)
    if root is None:
        return _no_directory(act)
    try:
        answer = cli.update_step(root, dry_run=dry_run)
    except _MOUNT_ERRORS as exc:
        return _error_state(act, exc)
    if answer.plan is None:
        return _answer(act, answer.outcome, answer.lines, dry_run=dry_run)
    return setup_web.PageState(
        UPDATE_PLAN if dry_run else UPDATED, answer.outcome, False,
        dispositions=tuple(_update_rows(answer.plan)),
        lines=answer.lines,
        result=(act, answer.plan),
    )


def _update_all(act: Action, *, dry_run: bool) -> setup_web.PageState:
    """Every listed project's update: `radialrig upgrade --all`, the
    same step over the same listing, one block per project. The state
    carries the projects as the step answered them, so the body draws
    each project's rows as a table under its folder; the lines are the
    command line's whole report, which is what the log keeps."""
    answer = cli.update_all_step(dry_run=dry_run)
    return setup_web.PageState(
        UPDATE_ALL_PLAN if dry_run else UPDATED_ALL, answer.outcome, False,
        lines=answer.lines, result=(act, answer.plan),
    )


# One runner per verb, rehearsal and confirmed press alike, told apart by
# `dry_run`. The roster side entry rides inside them as the command line
# keeps it: unmount and unconnect keep it through the step they share with
# their verbs, and connect keeps it itself, each on its real runs and on
# its already-done exits; the skill verbs and the update verbs keep none,
# because the command line keeps none.
RUNNERS = {
    UNMOUNT: _unmount,
    SKILL_ADD: _skill_add,
    SKILL_ADD_ALL: _skill_add_all,
    SKILL_REMOVE: _skill_remove,
    CONNECT: _connect,
    UNCONNECT: _unconnect,
    ORPHAN: _orphan,
    UPDATE: _update,
    UPDATE_ALL: _update_all,
}


def _sequence(root: str, kind: str, steps_of, *, dry_run: bool) -> setup_web.PageState:
    """One project's whole sequence of verbs, rehearsed or run.

    Written once for both presses of both sequences — the reversal and
    the repair — the reason `mount_plan` and `mount` share a composer:
    the rehearsal a reader reads has to be the sequence the confirm press
    runs, step for step and row for row.

    The steps are recomputed here rather than posted: a form drawn a
    minute ago names the skills that were installed then, or a record
    that has since been repaired, and what is shown — or run — has to be
    what this project carries now. Each one is the runner the single
    press uses, so a rehearsal writes nothing for the reason a plan press
    writes nothing, and a run writes what those verbs write with the
    roster side entries riding along unchanged.

    A step that refused or failed stops the sequence. On a run the steps
    before it have written, and the one that stopped may have too where
    its writer could not put everything back (PLAN-0027 phase 1's trace),
    so the answer carries every step and the page says what went and what
    is still here; the steps after it would be acting past a refusal the
    reader has not read yet. On a rehearsal the sequence would stop in the
    same place, so naming the steps beyond it would be describing a run
    that never gets there. A partial answer does not stop it: on a mismatched record the
    registration's removal finds the entry already gone, says so, and the
    fresh registration is exactly what should follow.

    The root comes through the same guard every single press uses. It has
    to: these are the presses that run several verbs at once, and a field
    that arrived empty would otherwise resolve to the directory the
    server was started in and act on that.
    """
    target = _resolved(root)
    if target is None:
        return _missing(root)
    done: list[setup_web.PageState] = []
    outcome = report.OK
    for act in steps_of(ro.verify(target)):
        state = RUNNERS[act.name](act, dry_run=dry_run)
        done.append(state)
        if state.kind in (REFUSED, FAILED):
            outcome = state.outcome
            break
    return setup_web.PageState(
        kind, outcome, False, result=(str(target), tuple(done)))


def plan_reversal(root: str) -> setup_web.PageState:
    """The first press: the whole sequence as the engine's own dry run.

    Nothing is written. What the reader gets is every step's plan rows
    under the command that runs that step alone, and one press beneath
    them that runs the sequence for real.
    """
    return _sequence(root, REHEARSAL, reversal_steps, dry_run=True)


def perform_reversal(root: str) -> setup_web.PageState:
    """The confirm press: the same sequence, run rather than rehearsed.

    `dry_run=False` is what the six performers above are, so each step
    writes exactly what its own confirmed single press writes.
    """
    return _sequence(root, REVERSED, reversal_steps, dry_run=False)


def plan_repair(root: str) -> setup_web.PageState:
    """The repair's first press: both verbs as the engine's own dry run.

    The removal's plan rows are what the record claims and the settings
    no longer carry; the registration's are what a fresh connect answers
    on the settings as they stand — the rows it would write where the
    file holds neither entry, and the engine's stale-entry refusal where
    it still holds one, which the removal takes out first (see
    `_cleared_by_removal`). Nothing is written by either.
    """
    return _sequence(root, REPAIR_PLAN, repair_steps, dry_run=True)


def perform_repair(root: str) -> setup_web.PageState:
    """The repair's confirm press: the two verbs, in that order."""
    return _sequence(root, REPAIRED, repair_steps, dry_run=False)


# The two sequence presses the handler dispatches beside the single ones:
# the plan press and the confirm press of each, told apart by the
# `confirm` field the way a single press's two halves are. Neither key is
# in `COMMANDS`, because neither is one verb: the view names every verb a
# sequence runs beside its button, each with its own row in that table.
SEQUENCES = {
    REVERSAL: (plan_reversal, perform_reversal),
    REPAIR: (plan_repair, perform_repair),
}


# --- rendering --------------------------------------------------------
# The escapes, the command block and the document shell are `setup_web`'s
# own, so the two pages carry one kit and one masthead.

_esc = setup_web._esc
_attr = setup_web._attr


def _command(text: str) -> str:
    """The shared command frame, with the Copy button named for its line.

    Every Copy button on this surface says which command it copies
    (PLAN-0014 phase 2 prescription 4): a page with several presses has
    several such buttons, and a reader by name needs each to differ. The
    frame's bytes are the setup page's own; only the name is added.
    """
    return setup_web._command(text, name=COPY_NAME.format(command=text))

# The stamp is the setup server's since PLAN-0017 phase 4, and every
# document either surface serves goes out through it; the names stand
# here for the tests that read them.
_ROOT_TAG = setup_web._ROOT_TAG
_stamped = setup_web._stamped


def _command_of(act) -> str:
    """The line beside a press, where there is a whole line to give.

    A block a reader copies has to run the thing the button ran. A press
    that arrived without one of the fields its command names would render
    that command with a hole in it, and the hole is the dangerous part:
    every one of these verbs defaults its path to the directory it is run
    in, so `radialrig unmount ` pasted into a terminal acts on wherever
    the reader happens to be. So a press with a hole in it names no
    command, and the page says what it did without offering a line that
    would do something else. A slot no quoting rule makes safe renders
    the same way (setup-web-04): `command` is "" for it, and "" is no
    block.
    """
    if act is None or not act.named:
        return ""
    line = act.command
    if not line:
        return ""
    return _command(line)


# The columns a press's rows are drawn under on a plan or result page:
# the three-column shape the setup page draws.
ACTION_COLUMNS = ("Change", "Path", "What it does")


def _table(rows, caption: str, resolve) -> str:
    """One row per line: what it is, what it names, what it means.

    The same three columns the setup page's plan table draws, read
    through the resolver the rows' own kit owns — the orphan kit's table
    first and the mount kit's beneath — so the page prints the sentence
    the command line prints for the same row. A press's rows take the
    setup page's own chip rule with them, because there a row is a path
    and `current` means a file already standing.
    """
    if not rows:
        return ""
    body = "".join(
        "<tr>"
        f'<td class="verb">{setup_web._chip(d.verb)}</td>'
        f'<td class="path">{_esc(mnt.one_line(d.path))}</td>'
        f'<td class="why">{_esc(mnt.one_line(resolve(d) or ""))}</td>'
        "</tr>"
        for d in rows
    )
    heads = "".join(f'<th scope="col">{_esc(name)}</th>' for name in ACTION_COLUMNS)
    return (
        '<div class="plan-wrap"><table class="plan">'
        f"<caption>{_esc(caption)}</caption>"
        f"<thead><tr>{heads}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _hidden(fields) -> str:
    """The fields a form carries without showing. Empty values are dropped,
    which is what the form parser does with them anyway."""
    return "".join(
        f'<input type="hidden" name="{_attr(name)}" value="{_attr(value)}">'
        for name, value in fields if value
    )


def _nav(session: ManageSession, view: str, label: str, *,
         extra=(), inner: str = "", cls: str = "ghost",
         framed: bool = False, fragment: str = "") -> str:
    """One navigation, as a form rather than a link.

    Every navigation on this page is a submission, because `page.js`
    sends the close beacon on any departure that is not one: a bare
    anchor would stop the server the moment a reader changed view. The
    session token rides in the query the same way it rides in the address
    bar, so a reload of the new view works.

    A fragment on the action lands the submission on that element of the
    view it opens: a browser keeps the fragment on the address it builds
    from a GET form and never sends it (PLAN-0014 phase 2, the probe in
    the plan's Graded claims). The way back from a project's page names
    the listing row it left, so the reader lands on it.
    """
    hidden = _hidden((("s", session.token), ("view", view), *extra))
    button = f'<button class="{cls}" type="submit">{_esc(label)}</button>'
    if framed:
        # A press that stands under something — a field, a paragraph —
        # takes the page's own action row, which is what puts air between
        # the two. A press that is the whole control needs none.
        button = f'<div class="actions">{button}</div>'
    action = session.page_path + (f"#{fragment}" if fragment else "")
    return (
        f'<form class="nav" method="get" action="{_attr(action)}">'
        + hidden + inner + button
        + "</form>"
    )


def _notice(state: setup_web.PageState) -> str:
    if not state.message:
        return ""
    return f'<p class="note">{_esc(state.message)}</p>'


# --- the redesigned screens (PLAN-0027 phase 1) -----------------------
# Projects and one project, drawn for a person: one status per project
# as a mark and a word, one button only where something needs doing, and
# every command one switch away. The words are the lines of
# `reports/ui-redesign-2026-11/wording.md`. The command line's rows are
# not redrawn here: `radialrig projects` keeps its sentences and its
# order, and only this page sorts.

def _row_id(root: str) -> str:
    """The listing row's own id for a project, stable across renders.

    Derived from the resolved root rather than from the row's position,
    so the project page's way back can name the row it left and land on
    it (phase 2 prescription 2) whatever order the listing sorts into.
    One helper for both surfaces, so the id the row carries and the one
    the fragment names cannot drift. `normcase` folds the one spelling
    difference a Windows path can carry; twelve hex digits of the digest
    keep the id short and, over a roster of dozens, distinct.
    """
    digest = hashlib.sha1(os.path.normcase(root).encode("utf-8")).hexdigest()
    return f"row-{digest[:12]}"


def _name_parts(root: str) -> tuple[str, str]:
    """The folder's final name, and the path above it.

    `pathlib` splits either spelling, Windows or POSIX, on the machine
    the roster was written on. A root with no final name — a drive root
    such as `C:\\`, or `/` — is shown whole as the name, with nothing
    above it, rather than as an empty name over a parent that is itself.
    """
    path = Path(root)
    if not path.name:
        return root, ""
    return path.name, str(path.parent)


def _needs_repair(state: ro.ProjectState) -> bool:
    return state.connected == ro.CONNECTED_MISMATCH


def _statuses(state: ro.ProjectState) -> tuple[str, ...]:
    """Every state that holds for a project, the most pressing first.

    The first is the one status its row on Projects shows; the project's
    own screen says each that holds, so a project that needs a repair
    and has an update ready keeps both presses. A project is working
    only when nothing else holds.
    """
    held = []
    if _needs_repair(state):
        held.append(NEEDS_REPAIR)
    if state.behind:
        held.append(UPDATE_READY)
    if not state.mounted:
        held.append(NOT_SET_UP)
    return tuple(held) or (WORKING,)


# A status's mark, and a status as its mark and its words: the setup
# page's, which draws Add a project with them too.
_symbol = setup_web.symbol
_state = setup_web.state_mark


def _button(label: str, *, cls: str = "", name: str = "", glyph: str = "",
            pressed: bool | None = None) -> str:
    """A submit button: its words, a glyph before them where the look
    has one, an accessible name where the words alone would not tell two
    buttons on one screen apart, and whether it reads as switched on
    where it is one of the grid's switches."""
    attrs = f' class="{cls}"' if cls else ""
    if name:
        attrs += f' aria-label="{_attr(name)}"'
    if pressed is not None:
        attrs += f' aria-pressed="{"true" if pressed else "false"}"'
    lead = f'<span aria-hidden="true">{glyph}</span>' if glyph else ""
    return f'<button{attrs} type="submit">{lead}{_esc(label)}</button>'


def _press(session: ManageSession, fields, button: str, *, origin: str = "") -> str:
    """One press as the form that names it.

    With `origin`, the press is one of the small changes that run at once
    (PLAN-0027 phase 1 prescription 6): the form carries `confirm`, and
    `from` names the screen it was made on, the roster or the project, so
    the answer can come back to that screen. Without it the form is
    today's plan press and reaches today's plan page.
    """
    if origin:
        fields = (*fields, ("confirm", "yes"), ("from", origin))
    return setup_web._form(session, _hidden(fields) + button)


def _ask(session: ManageSession, fields, button: str, *, origin: str) -> str:
    """A big change's first press (PLAN-0027 phase 3): the form names the
    screen it was made on (`from`) and carries no `confirm`, so it answers
    with the engine's dry run drawn as Confirm a big change and writes
    nothing. The card's own button is the press that writes."""
    return setup_web._form(session, _hidden((*fields, ("from", origin))) + button)


def _go(session: ManageSession, view: str, button: str, *,
        extra=(), inner: str = "", fragment: str = "") -> str:
    """`_nav` for a button drawn by `_button`: the same form, so a change
    of view is a submission and the server goes on standing."""
    action = session.page_path + (f"#{fragment}" if fragment else "")
    return (
        f'<form class="nav" method="get" action="{_attr(action)}">'
        + _hidden((("s", session.token), ("view", view), *extra))
        + inner + button + "</form>"
    )


# One line a reader may copy, with the button that copies it.
_copy_line = setup_web.copy_line


def _commands(*acts: Action, row: bool = False) -> str:
    """The commands a press runs, in the order it runs them, shown while
    the screen's Show commands switch is on.

    A press with a hole in it names no command, as `_command_of` rules.
    `row` draws them as a row of the skill grid.
    """
    lines = "".join(
        _copy_line(act.command) for act in acts
        if act is not None and act.named and act.command)
    if not lines:
        return ""
    if row:
        return f'<tr class="commands"><td colspan="{1 + len(sk.HARNESSES)}">{lines}</td></tr>'
    return f'<div class="commands">{lines}</div>'


# The one switch a screen carries for its commands, and the one line a
# press answers with: the setup page's, shared with Add a project.
_show_commands = setup_web.show_commands
result_line = setup_web.result_line


# --- when Claude Desktop is open (BUG-0018) ---------------------------

def _running(client, paths=None) -> bool:
    """Whether a press that writes this client's settings would find the
    client open: the question the setup page's connect box asks, of the
    settings files this machine holds for it, or of the ones given."""
    try:
        if paths is None:
            paths = [copy.path for copy in cn.detect(client)]
    except OSError:
        return False
    return cn.is_running(client, paths)


def _running_warning(client, paths=None) -> str:
    """The before-press sentence, beside a press that writes the client's
    settings, while the client is open; nothing otherwise."""
    if client is None or not _running(client, paths):
        return ""
    return f'<p class="warn">{_esc(report.client_running_before(client))}</p>'


# --- Repair, offered or withheld --------------------------------------

@dataclasses.dataclass(frozen=True)
class _Repair:
    """Whether Repair is offered on a project, and what to say if not."""

    offered: bool = False
    client: object = None              # the client a repair writes to
    why: str = ""                      # the status sentence where withheld
    short: str = ""                    # the same in a few words, for a row
    blockers: tuple[str, ...] = ()     # what stopped it, for Details


def _blockers(steps) -> tuple[str, ...]:
    """What the repair's rehearsal was stopped by, for Details: each
    file or entry in the way with the engine's reason for it. The
    report's head and its advice about commands are left out; a step
    that stopped naming no file gives its own sentence instead."""
    out: list[str] = []
    for step in steps:
        if step.kind not in (REFUSED, FAILED) or _cleared_by_removal(step):
            continue
        rows = [
            f"{mnt.one_line(d.path)}: {mnt.one_line(_reason(d) or '')}"
            for d in step.dispositions]
        if rows:
            out.extend(rows)
        elif step.message:
            out.append(step.message)
        else:
            out.extend(line for line in step.lines if line)
    return tuple(out)


def _repair_guard(facts: ro.ProjectState) -> _Repair:
    """Repair's guard, asked when a screen is drawn.

    The button shows only where today's repair plan would offer its
    press (PLAN-0027 phase 1 prescription 7): the client the record
    names is on this machine and the read server's command is found
    (`_repair_client`), and the two steps rehearse the way
    `_repair_offered` asks. The rehearsal is the engine's own dry run and
    writes nothing. Where the button is withheld the status says why.
    """
    claimed = cn.CLIENTS.get(facts.claimed)
    if claimed is None:
        return _Repair(
            why=REPAIR_NO_RECORD, short=ROW_WHY_NO_RECORD,
            blockers=(RECORD_AT.format(path=Path(facts.root) / cn.CONNECT_REL),))
    client = _repair_client(facts)
    if client is None:
        try:
            here = bool(cn.detect(claimed))
        except OSError:
            here = False
        why, short = ((REPAIR_NO_PROGRAM, ROW_WHY_NO_PROGRAM) if here
                      else (REPAIR_NO_CLIENT, ROW_WHY_NO_CLIENT))
        return _Repair(why=why.format(label=claimed.label),
                       short=short.format(label=claimed.label))
    try:
        plan = plan_repair(facts.root)
        steps = plan.result[1] if plan.kind == REPAIR_PLAN else ()
    except (OSError, RuntimeError, ValueError):
        steps = ()
    if _repair_offered(steps):
        return _Repair(offered=True, client=client)
    blockers = _blockers(steps)
    why = REPAIR_BLOCKED.format(label=client.label)
    if blockers:
        why = f"{why} {REPAIR_SEE_DETAILS}"
    return _Repair(client=client, why=why, short=ROW_WHY_BLOCKED, blockers=blockers)


def _lost_label(facts: ro.ProjectState) -> str:
    """The client a lost link is about: the one the record names, or the
    table's own phrase where the record names none this build knows."""
    claimed = cn.CLIENTS.get(facts.claimed)
    return claimed.label if claimed is not None else cn.label_phrase()


# --- Projects ---------------------------------------------------------

def _listing_order(state: ro.ProjectState) -> int:
    """Rows that need attention first: needs repair, update ready, not
    set up, then working, and a folder that is gone last. The sort is
    stable, so each group keeps the project list's own order."""
    if not state.exists:
        return len(STATUS_ORDER)
    return STATUS_ORDER.index(_statuses(state)[0])


def _skills_said(count: int) -> str:
    if count == 0:
        return NO_SKILLS_FACT
    if count == 1:
        return ONE_SKILL_FACT
    return SKILLS_FACT.format(count=count)


def _about(state: ro.ProjectState, here) -> str:
    """A row's one line of facts: the connection, then how many skills.

    The count is the skills either agent has; the project's own screen
    shows them agent by agent. A client is named only where one is on
    this machine (`here`), as the listing has always held, and the label
    is the client table's.
    """
    skills = _skills_said(len(state.skills))
    client = cn.CLIENTS.get(state.connected)
    if _needs_repair(state):
        head = FACT_LOST.format(label=_lost_label(state))
    elif client is not None:
        head = FACT_CONNECTED.format(label=client.label)
    elif not here:
        return skills
    elif not state.skills:
        return FACT_NOTHING
    else:
        head = FACT_NOT_CONNECTED.format(label=cn.label_phrase(here))
    return f"{head}, {skills[0].lower()}{skills[1:]}"


def _project_row(session: ManageSession, state: ro.ProjectState, guard,
                 here, *, amber: bool) -> str:
    """One project: its name over its parent folder, one status, one line
    of facts, and one button only when something needs doing.

    The name is the control that opens the project, stretched over the
    row by the stylesheet, so a click anywhere on the row opens it. A
    project whose folder is gone has no screen to open and nothing to
    press; its row says so once, on the read that takes it off the list.
    """
    name, parent = _name_parts(state.root)
    above = f'<span class="parent">{_esc(parent)}</span>' if parent else ""
    if not state.exists:
        return (
            '<li class="roster-row gone">'
            f'<div class="who"><span class="row-name">{_esc(name)}</span>{above}</div>'
            + _state(GONE, STATUS_GONE)
            + f'<div class="about"><span>{_esc(FACT_GONE)}</span></div>'
            + '<div class="fix"></div></li>'
        )
    kind = _statuses(state)[0]
    cls = "primary" if amber else ""
    fix = warn = commands = ""
    if kind == NEEDS_REPAIR and guard is not None and guard.offered:
        fix = _press(
            session, (("action", REPAIR), ("root", state.root)),
            _button(REPAIR_BUTTON, cls=cls,
                    name=ROW_BUTTON_NAME.format(action=REPAIR_BUTTON, project=name)),
            origin=ROSTER)
        warn = _running_warning(guard.client)
        commands = _commands(*repair_steps(state))
    elif kind == NEEDS_REPAIR and guard is not None and guard.short:
        # Withheld: the row says why, in the guard's few words.
        warn = f'<span class="why">{_esc(guard.short)}</span>'
    elif kind == UPDATE_READY:
        act = Action(UPDATE, root=state.root)
        fix = _ask(session, act.fields, _button(
            UPDATE_BUTTON,
            name=ROW_BUTTON_NAME.format(action=UPDATE_BUTTON, project=name)),
            origin=ROSTER)
        commands = _commands(act)
    elif kind == NOT_SET_UP:
        fix = _go(session, SETUP, _button(
            SETUP_BUTTON, cls=cls,
            name=ROW_BUTTON_NAME.format(action=SETUP_BUTTON, project=name)),
            extra=(("root", state.root),))
    opener = _go(session, PROJECT, _button(name, cls="row-name"),
                 extra=(("root", state.root),))
    return (
        f'<li class="roster-row" id="{_row_id(state.root)}">'
        f'<div class="who">{opener}{above}</div>'
        + _state(kind, STATUS_WORDS[kind])
        + f'<div class="about"><span>{_esc(_about(state, here))}</span>{warn}</div>'
        + f'<div class="fix">{fix}</div>'
        + commands + "</li>"
    )


def _add_project(session: ManageSession, *, amber: bool = False) -> str:
    """Add a project: the button, and behind it the folder field.

    One field, and the picker's button beside it. The field is the one
    the desktop shell's picker writes into (D11) and the one a typed or
    pasted path has always reached, so it stays writable: this page is
    served to a plain browser as readily as to that shell. The picker's
    button ships hidden and the shell reveals it.

    The press is rendered enabled: page.js disables it while the field is
    empty and enables it on the field's `input` event, which the shell's
    picker dispatches after writing the path. It finds the press by its
    `primary` class. Without the script a bare submit answers with the
    list, as the server has always answered it.
    """
    field = (
        f'<label class="sr" for="root">{_esc(ADD_FIELD_LABEL)}</label>'
        '<input id="root" name="root" value="" autocomplete="off" '
        f'spellcheck="false" placeholder="{_attr(ADD_FIELD_PLACEHOLDER)}">'
        f'<button type="button" id="browse" hidden>{_esc(BROWSE_LABEL)}</button>'
    )
    cls = "button primary" if amber else "button"
    return (
        f'<details class="add" id="{ADD_ID}">'
        f'<summary class="{cls}">{_esc(ADD_HEAD)}</summary>'
        + _go(session, SETUP, _button(ADD_LABEL, cls="primary"), inner=field)
        + "</details>"
    )


def _update_line(session: ManageSession, states, *, amber: bool) -> str:
    """The one line above the list while any project is behind: how many,
    and Update all, which opens Confirm a big change for every listed
    project (`radialrig upgrade --all`)."""
    count = sum(1 for state in states if state.behind)
    if not count:
        return ""
    said = UPDATE_ONE if count == 1 else UPDATE_MANY.format(count=count)
    act = Action(UPDATE_ALL)
    return (
        f'<div class="update-note"><span>{_esc(said)}</span>'
        + _ask(session, act.fields,
               _button(UPDATE_ALL_BUTTON, cls="primary" if amber else ""),
               origin=ROSTER)
        + _commands(act) + "</div>"
    )


@dataclasses.dataclass(frozen=True)
class _Leftover:
    """One gone folder's entries in one client's settings: what its row
    on Projects stands for, and what its Clean up takes out, in order."""

    client: str
    root: str
    entries: tuple            # the `roster.Orphan`s, in the scan's key order

    @property
    def acts(self) -> tuple[Action, ...]:
        return tuple(Action(ORPHAN, client=o.client, key=o.key) for o in self.entries)

    @property
    def copies(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c for o in self.entries for c in o.copies))


def _leftovers(found) -> tuple[tuple[_Leftover, ...], tuple]:
    """The scan's entries, as one leftover per gone folder and client,
    and the entries that name no project folder."""
    grouped: dict[tuple[str, str], list] = {}
    loose = []
    for entry in found:
        if not entry.root:
            loose.append(entry)
            continue
        grouped.setdefault(
            (entry.client, os.path.normcase(entry.root)), []).append(entry)
    rows = tuple(
        _Leftover(client=entries[0].client, root=entries[0].root,
                  entries=tuple(entries))
        for entries in grouped.values())
    return rows, tuple(loose)


def _leftover_row(session: ManageSession, left: _Leftover, *, amber: bool) -> str:
    """A gone folder's leftover entries, as one row of Projects: the
    folder's name over its parent, Needs repair, the facts, and Clean up,
    which takes out every entry of that folder on one press, one command
    each, in the order Show commands lists them (prescription 5). There
    is no project screen to open, so the name is words, not a press."""
    name, parent = _name_parts(left.root)
    above = f'<span class="parent">{_esc(parent)}</span>' if parent else ""
    client = cn.CLIENTS.get(left.client)
    label = client.label if client is not None else mnt.one_line(left.client)
    facts = (LEFTOVER_FACT_ONE if len(left.entries) == 1
             else LEFTOVER_FACT_MANY).format(label=label)
    fields = (("action", ORPHAN), ("client", left.client), ("root", left.root))
    fix = _press(session, fields, _button(
        CLEAN_UP_BUTTON, cls="primary" if amber else "",
        name=ROW_BUTTON_NAME.format(action=CLEAN_UP_BUTTON, project=name)),
        origin=ROSTER)
    return (
        '<li class="roster-row leftover-row">'
        f'<div class="who"><span class="row-name">{_esc(name)}</span>{above}</div>'
        + _state(NEEDS_REPAIR, STATUS_WORDS[NEEDS_REPAIR])
        + f'<div class="about"><span>{_esc(facts)}</span>'
        + _running_warning(client, left.copies) + "</div>"
        + f'<div class="fix">{fix}</div>'
        + _commands(*left.acts) + "</li>"
    )


def _leftover(session: ManageSession, loose) -> str:
    """The entries a client holds that name no project folder: one line
    under the list, which opens onto each key and the engine's sentence,
    and no press, since nothing shows such an entry is a leftover."""
    if not loose:
        return ""
    labels = []
    for entry in loose:
        client = cn.CLIENTS.get(entry.client)
        if client is not None and client not in labels:
            labels.append(client)
    said = (LEFTOVER_LOOSE_ONE if len(loose) == 1 else LEFTOVER_LOOSE_MANY).format(
        label=cn.label_phrase(labels or None), count=len(loose))
    rows = "".join(
        f"<li><code>{_esc(mnt.one_line(entry.key))}</code>"
        f"<span>{_esc(ro.ROSTER_REASONS[ro.ROSTER_UNPINNED])}</span></li>"
        for entry in loose)
    return (
        '<details class="leftover">'
        f"<summary>{_state(NEEDS_REPAIR, said)}"
        f'<span class="more">{_esc(LEFTOVER_SHOW)}</span></summary>'
        f"<ul>{rows}</ul></details>"
    )


def _footer(session: ManageSession) -> str:
    """The quiet line under the list: the version, the switch for the
    commands, where the activity log is, and the way to Remove RadialRig."""
    log = al.log_path()
    where = (
        f'<details class="log"><summary>{_esc(LOG_LABEL)}</summary>'
        f'<div class="drawer">{_copy_line(str(log))}</div></details>'
    ) if log is not None else ""
    return (
        '<footer class="foot">'
        f'<span class="build">{_esc(FOOT_VERSION.format(version=mnt.installed_version()))}</span>'
        + _show_commands() + where
        + _go(session, REMOVAL, _button(REMOVE_ALL_LABEL, cls="link"))
        + "</footer>"
    )


def _roster_body(session: ManageSession, state: setup_web.PageState,
                 result: str = "") -> str:
    """Projects: every project, whether it's working, and the one thing
    each needs.

    `result` is a drawn result line (`result_line`) for a press made on
    this screen, placed above the list; empty when the screen is opened
    plain. A gone folder's leftover entries are a row of their own among
    the rows that need repair, first (PLAN-0027 phase 3 prescription 5),
    and take the place of that folder's gone row where it has one. The
    screen's one amber goes to the first press of a row that needs
    repair, Clean up or Repair, else to Update all, else to the first
    Set up, as mockups 02-updates-done and 02-projects-repaired draw it
    (PLAN-0027 phase 5), else, on an empty list, to Add a project.
    """
    states, found = state.result
    here = cn.installed()
    leftovers, loose = _leftovers(found)
    cleaned = {os.path.normcase(left.root) for left in leftovers}
    ordered = [
        s for s in sorted(states, key=_listing_order)
        if s.exists or os.path.normcase(s.root) not in cleaned]
    guards = {
        s.root: _repair_guard(s) for s in ordered if s.exists and _needs_repair(s)}
    first_repair = "" if leftovers else next(
        (s.root for s in ordered if s.root in guards and guards[s.root].offered), "")
    behind = any(s.behind for s in states)
    first_setup = "" if (leftovers or first_repair or behind) else next(
        (s.root for s in ordered if s.exists and _statuses(s)[0] == NOT_SET_UP), "")
    amber_row = first_repair or first_setup
    rows = "".join(
        _leftover_row(session, left, amber=index == 0)
        for index, left in enumerate(leftovers))
    rows += "".join(
        _project_row(session, s, guards.get(s.root), here,
                     amber=bool(amber_row) and s.root == amber_row)
        for s in ordered)
    notice = f'<p class="notice">{_esc(state.message)}</p>' if state.message else ""
    if rows:
        head = f"<h1>{_esc(ROSTER_HEAD)}</h1>" + _add_project(session)
        listing = f'<ul class="roster">{rows}</ul>'
    else:
        head = f"<h1>{_esc(ROSTER_HEAD)}</h1>"
        listing = (
            f'<div class="empty"><h2>{_esc(EMPTY_HEAD)}</h2>'
            f"<p>{_esc(EMPTY_LEDE)}</p>"
            + _add_project(session, amber=True) + "</div>"
        )
    return (
        '<div class="screen projects">'
        f'<header class="page-head">{head}</header>'
        + result + notice
        + _update_line(session, states, amber=not (first_repair or leftovers))
        + listing
        + _leftover(session, loose)
        + _footer(session)
        + "</div>"
    )


def _act_form(session: ManageSession, act: Action, label: str, *,
              confirm: bool = False) -> str:
    """One press, as the form that names it again.

    The amber is on the confirm button and on no other press: an offer
    leads to the engine's rehearsal and writes nothing, and the button
    under that rehearsal is the one that writes.
    """
    fields = act.fields + ((("confirm", "yes"),) if confirm else ())
    cls = "primary" if confirm else "ghost"
    return setup_web._form(
        session,
        _hidden(fields)
        + '<div class="actions">'
        + f'<button class="{cls}" type="submit">{_esc(label)}</button>'
        + "</div>",
    )


# --- one project ------------------------------------------------------
# The way back, the name and the folder; the status as a sentence with
# its fix beside it; the skill grid; the connections; and, quiet at the
# foot, Details and the removal. It fits the app's window with Details
# closed (PLAN-0027 phase 1 prescription 5).

def _status_band(session: ManageSession, facts: ro.ProjectState, guard) -> str:
    """The status: one sentence for each state that holds, the most
    pressing first, each with its fix beside it.

    Nearly always one sentence. Repair shows only where its guard offers
    it, and the sentence says why where it does not; Update opens Confirm
    a big change, and Set up opens Add a project. The first fix is the
    screen's one amber.
    """
    rows = []
    amber = True
    for kind in _statuses(facts):
        said, fix, warn, steps = STATUS_SAID[kind], "", "", ()
        cls = "primary" if amber else ""
        if kind == NEEDS_REPAIR:
            said = guard.why or said.format(label=_lost_label(facts))
            if guard.offered:
                fix = _press(
                    session, (("action", REPAIR), ("root", facts.root)),
                    _button(REPAIR_BUTTON, cls=cls), origin=PROJECT)
                warn = _running_warning(guard.client)
                steps = repair_steps(facts)
        elif kind == UPDATE_READY:
            act = Action(UPDATE, root=facts.root)
            fix = _ask(session, act.fields, _button(UPDATE_BUTTON, cls=cls),
                       origin=PROJECT)
            steps = (act,)
        elif kind == NOT_SET_UP:
            fix = _go(session, SETUP, _button(SETUP_BUTTON, cls=cls),
                      extra=(("root", facts.root),))
        if fix:
            amber = False
        rows.append(
            f'<div class="status-row">{_symbol(kind)}'
            f'<div class="words"><p>{_esc(said)}</p>{warn}</div>{fix}</div>'
            + _commands(*steps)
        )
    return f'<div class="status-band">{"".join(rows)}</div>'


def _blocked_above(facts: ro.ProjectState, s: sk.SkillState) -> bool:
    """Whether a file stands where a folder on the way to the skill's own
    folder goes: the test Add a project's grid uses for the same cell."""
    return setup_web.blocked_above(facts.root, s)


def _cell_kind(facts: ro.ProjectState, s: sk.SkillState) -> str:
    """What one agent's cell for one shipped skill reads.

    The roster's reading for that agent, the one the row on Projects
    counts: on where the agent's record lists the skill and it stands,
    update where that copy is clean and the package ships a newer one
    (the test the update uses), off otherwise. A target the engine would
    refuse to add into reads as the reason, never as a switch.
    """
    if s.record_entry is None and (s.dir_is_file or _blocked_above(facts, s)):
        return CELL_FILE
    if s.record_entry is None and s.foreign:
        return CELL_FOREIGN
    listed = s.name in dict(facts.agents).get(s.harness.name, ())
    if listed and sk.behind(s):
        return CELL_UPDATE
    return CELL_ON if listed else CELL_OFF


def _cell(session: ManageSession, facts: ro.ProjectState, s: sk.SkillState,
          kind: str) -> tuple[str, Action | None]:
    """One cell of the grid, and the press it makes, where it makes one.

    On, Off and Update are buttons, and each runs on the one press: On
    removes the skill for that agent, Off adds it, and Update is the add
    for that agent, which refreshes a clean copy. A reason is words and
    no button.
    """
    name, agent = s.name, s.harness.label
    if kind in CELL_REASONS:
        return f'<span class="cell-note">{_esc(CELL_REASONS[kind])}</span>', None
    verb = SKILL_REMOVE if kind == CELL_ON else SKILL_ADD
    act = Action(verb, root=facts.root, skill=name, harness=s.harness.name)
    if kind == CELL_UPDATE:
        button = _button(
            CELL_UPDATE_LABEL, cls="cell update", glyph=GLYPH_UP,
            name=CELL_UPDATE_NAME.format(skill=name, agent=agent))
    else:
        on = kind == CELL_ON
        button = _button(
            CELL_ON_LABEL if on else CELL_OFF_LABEL,
            cls="cell on" if on else "cell off",
            glyph=GLYPH_CHECK if on else GLYPH_MINUS,
            name=CELL_NAME.format(skill=name, agent=agent), pressed=on)
    return _press(session, act.fields, button, origin=PROJECT), act


def _retired_cell(session: ManageSession, facts: ro.ProjectState,
                  s: sk.SkillState | None) -> tuple[str, Action | None]:
    """A retired skill's cell: Remove where this agent's record still
    lists it, nothing where it does not."""
    if s is None or s.record_entry is None:
        return "", None
    act = Action(SKILL_REMOVE, root=facts.root, skill=s.name, harness=s.harness.name)
    button = _button(
        RETIRED_REMOVE, cls="cell",
        name=RETIRED_NAME.format(skill=s.name, agent=s.harness.label))
    return _press(session, act.fields, button, origin=PROJECT), act


def _skill_grid(session: ManageSession, state: setup_web.PageState) -> str:
    """The skills, one row each with its description once, and a column
    per agent in the harness map's order.

    The rows are the catalog's, in catalog order, then a row for each
    skill a record still lists that the package no longer ships: it is
    still removable, so it still gets its row. Add all stands in an
    agent's column only where it has something to add and no cell in
    that column shows a reason: the whole-catalog add composes every
    skill and refuses whole, so beside such a cell it could only refuse.
    """
    facts = state.result
    harnesses = tuple(sk.HARNESSES.values())
    by = {(s.harness.name, s.name): s for s in state.catalog}
    shipped: list[str] = []
    retired: list[str] = []
    for s in state.catalog:
        into = shipped if s.packaged is not None else retired
        if s.name not in into:
            into.append(s.name)
    summaries = {s.name: s.packaged.summary for s in state.catalog
                 if s.packaged is not None}

    body = ""
    kinds: dict[str, list[str]] = {h.name: [] for h in harnesses}
    for name in shipped:
        cells, acts = "", []
        for harness in harnesses:
            s = by.get((harness.name, name))
            if s is None:
                cells += "<td></td>"
                continue
            kind = _cell_kind(facts, s)
            kinds[harness.name].append(kind)
            drawn, act = _cell(session, facts, s, kind)
            cells += f"<td>{drawn}</td>"
            acts.append(act)
        body += (
            setup_web.grid_row(name, summaries.get(name, ""), cells)
            + _commands(*acts, row=True)
        )
    for name in retired:
        cells, acts = "", []
        for harness in harnesses:
            drawn, act = _retired_cell(session, facts, by.get((harness.name, name)))
            cells += f"<td>{drawn}</td>"
            acts.append(act)
        body += (
            setup_web.grid_row(
                mnt.one_line(name), RETIRED_SAID, cells, cls="retired")
            + _commands(*acts, row=True)
        )

    heads, all_acts = "", []
    for harness in harnesses:
        press = ""
        seen = kinds[harness.name]
        if CELL_OFF in seen and not any(kind in CELL_REASONS for kind in seen):
            act = Action(SKILL_ADD_ALL, root=facts.root, harness=harness.name)
            press = _press(session, act.fields, _button(
                ADD_ALL_LABEL, cls="link",
                name=ADD_ALL_NAME.format(agent=harness.label)), origin=PROJECT)
            all_acts.append(act)
        heads += setup_web.grid_head(harness.label, press)
    if not body:
        body = (f'<tr><td colspan="{1 + len(harnesses)}" class="none">'
                f"{_esc(SKILLS_NONE)}</td></tr>")
    return setup_web.skill_grid(heads, _commands(*all_acts, row=True) + body)


def _connections(session: ManageSession, state: setup_web.PageState) -> str:
    """Connections: one row per client in the client table, its state as
    a mark and a word, and Connect or Disconnect where today's gates
    offer one.

    Connect stands only where the client is on this machine and this
    project is not connected to it (`state.client`), Disconnect only
    where the record and the client's own settings agree. A lost link
    reads as one and carries no press here: Repair is in the status
    above. Both presses run on the one press, and each carries the
    before-press sentence while the client is open.
    """
    facts = state.result
    here = cn.installed()
    lost = _needs_repair(facts)
    rows = ""
    for client in cn.CLIENTS.values():
        press = warn = ""
        act = None
        if facts.connected == client.name:
            said = _state(WORKING, CONNECTED_STATE)
            act = Action(UNCONNECT, root=facts.root, client=client.name)
            label, name = DISCONNECT_BUTTON, UNCONNECT_LABEL
        elif lost and facts.claimed in ("", client.name):
            said = _state(NEEDS_REPAIR, LINK_LOST_STATE)
        elif client not in here:
            said = f'<span class="state">{_esc(NOT_FOUND_STATE)}</span>'
        else:
            said = _state(NOT_SET_UP, NOT_CONNECTED_STATE)
            if not lost and state.client is client:
                act = Action(CONNECT, root=facts.root, client=client.name)
                label, name = CONNECT_BUTTON, CONNECT_LABEL
        if act is not None:
            press = _press(session, act.fields, _button(
                label, name=name.format(label=client.label)), origin=PROJECT)
            warn = _running_warning(client)
        rows += (
            f'<div class="connection"><strong>{_esc(client.label)}</strong>'
            f"{said}{warn}{press}</div>" + _commands(act)
        )
    return f'<h2 class="section">{_esc(CONNECTIONS_HEAD)}</h2>{rows}'


def _versions(state: setup_web.PageState) -> str:
    """Details' list of skill versions: what the package ships for each
    skill, and beside it any agent whose own copy is a different one. A
    retired skill shows the version its record holds."""
    harnesses = tuple(sk.HARNESSES.values())
    by = {(s.harness.name, s.name): s for s in state.catalog}
    names: list[str] = []
    for s in state.catalog:
        if s.name not in names:
            names.append(s.name)
    items = ""
    for name in names:
        copies = [by[(h.name, name)] for h in harnesses if (h.name, name) in by]
        packaged = next((s.packaged for s in copies if s.packaged is not None), None)
        if packaged is None:
            version = next((s.installed_version for s in copies if s.installed_version), "")
            if version:
                items += "<li>" + _esc(VERSION_ITEM.format(
                    skill=mnt.one_line(name), version=mnt.one_line(version))) + "</li>"
            continue
        said = VERSION_ITEM.format(skill=name, version=packaged.version)
        for s in copies:
            if s.installed_version and s.installed_version != packaged.version:
                said += VERSION_AGENT.format(
                    agent=s.harness.label, version=mnt.one_line(s.installed_version))
        items += f"<li>{_esc(said)}</li>"
    if not items:
        return ""
    return f"<h3>{_esc(VERSIONS_HEAD)}</h3><ul>{items}</ul>"


def _project_foot(session: ManageSession, state: setup_web.PageState, guard) -> str:
    """Details, and the quiet way out of this project.

    Details holds the screen's one Show commands switch and the skill
    versions, and what stands in a repair's way where Repair is withheld.
    The removal shows while RadialRig has anything in this project, and
    opens Confirm a big change for taking it out, the steps
    `reversal_steps` names by default; the commands under it are those.
    """
    facts = state.result
    blocked = ""
    if guard is not None and guard.blockers:
        blocked = (
            f"<h3>{_esc(REPAIR_BLOCKERS_HEAD)}</h3><ul>"
            + "".join(f"<li>{_esc(line)}</li>" for line in guard.blockers)
            + "</ul>"
        )
    details = (
        f'<details class="settings"><summary>{_esc(DETAILS_LABEL)}</summary>'
        f'<div class="drawer">{_show_commands()}{blocked}{_versions(state)}</div>'
        "</details>"
    )
    steps = reversal_steps(facts)
    removal = _ask(
        session, (("action", REVERSAL), ("root", facts.root)),
        _button(REMOVE_PROJECT_LABEL, cls="link quiet"),
        origin=PROJECT) if steps else ""
    return (
        f'<div class="project-foot">{details}{removal}</div>'
        + _commands(*steps)
    )


def _project_body(session: ManageSession, state: setup_web.PageState,
                  result: str = "") -> str:
    """One project: what it has, and the presses that change it.

    `result` is a drawn result line (`result_line`) for a press made on
    this screen, placed under the project's name; empty when the screen
    is opened plain. The way back lands on this project's row.
    """
    facts = state.result
    name, _ = _name_parts(facts.root)
    guard = _repair_guard(facts) if _needs_repair(facts) else None
    back = _go(
        session, ROSTER,
        f'<button class="link back" type="submit"><span aria-hidden="true">'
        f"{GLYPH_BACK}</span>{_esc(ROSTER_HEAD)}</button>",
        fragment=_row_id(facts.root))
    return (
        '<div class="screen project">'
        + back
        + f'<div class="project-name"><h1>{_esc(name)}</h1>'
        f'<span class="path">{_esc(facts.root)}</span></div>'
        + result
        + _status_band(session, facts, guard)
        + _skill_grid(session, state)
        + _connections(session, state)
        + _project_foot(session, state, guard)
        + "</div>"
    )


# --- one press, one result line (PLAN-0027 phase 1) -------------------
# A small change runs on its one press and brings back the screen it was
# made on, with one line at the top saying what happened (prescription
# 6). The press runs the runner its command line twin runs, unchanged, in
# the one-write queue, so the engine writes what the command writes and
# the activity log keeps the runner's own answer. What this adds is the
# sentence, picked from the press, how it ended and the rows it reported.

# The presses that run at once when their form names the screen they were
# made on. Repair is the sequence that does the same.
ONE_PRESS = (SKILL_ADD, SKILL_ADD_ALL, SKILL_REMOVE, CONNECT, UNCONNECT)
# The screens a press can come from and come back to. Remove RadialRig
# joins them in phase 3: a project's Remove there comes back there.
ORIGINS = (ROSTER, PROJECT, REMOVAL)

# The state such a press answers with: the screen, drawn fresh, carrying
# the line.
ANSWERED = "press-answered"


@dataclasses.dataclass(frozen=True)
class _Answer:
    """A one-press change's answer."""

    view: setup_web.PageState    # the screen it was made on, read after the press
    press: setup_web.PageState   # the runner's own answer, which the log keeps
    line: str                    # the drawn result line
    # Further runner answers the same press made, each logged as its own
    # event: a Clean up runs one command per entry.
    more: tuple = ()


def _answered(session: ManageSession, origin: str, root: str,
              press: setup_web.PageState, line: str, *,
              more: tuple = ()) -> setup_web.PageState:
    """The screen the press came from, read now, carrying its line. A
    project that can no longer be drawn (its folder went) answers on
    Projects, where the line still reads."""
    if origin == PROJECT:
        view = project_state(session, root)
    elif origin == REMOVAL:
        view = removal_state(session)
    else:
        view = roster_state(session)
    if view.kind not in (ROSTER, PROJECT, REMOVAL):
        view = roster_state(session)
    return setup_web.PageState(
        ANSWERED, report.OK, False, result=_Answer(view, press, line, more))


def _answered_body(session: ManageSession, state: setup_web.PageState) -> str:
    answer = state.result
    if answer.view.kind == PROJECT:
        return _project_body(session, answer.view, answer.line)
    if answer.view.kind == REMOVAL:
        return _removal_body(session, answer.view, answer.line)
    return _roster_body(session, answer.view, answer.line)


def _untouched(state: setup_web.PageState) -> bool:
    """Whether a press's answer shows the project and the client's settings
    as they were: a refusal, which writes nothing, or a failure whose
    writer put back everything it had written. Only these take a line
    saying nothing was changed (`wording.md`'s rule)."""
    return state.kind == REFUSED or (
        state.kind == FAILED and state.outcome != report.PARTIAL)


def _stopped_at_record(state: setup_web.PageState, root: str) -> bool:
    """Whether a disconnect stopped at the project's connection record,
    after the client's settings were already changed: the writer names
    the file it stopped at first in its sentence."""
    record = cn.record_path(Path(root))
    return state.message.startswith(f"file error at {record}")


def _wrote(rows) -> bool:
    return any(d.verb not in (mnt.CURRENT, mnt.ABSENT, mnt.LEFT) for d in rows)


def _changed(states) -> tuple[tuple[str, str], ...]:
    """Each path a press changed or kept, tagged with what happened to it.
    A row that changed nothing, because the thing was already as wanted
    or already gone, is not listed."""
    out = []
    for state in states:
        if state.kind not in (DONE,):
            continue
        for d in state.dispositions:
            if d.code == mnt.CONNECT_KEY_GONE:
                continue
            tag = TAG_UPDATED if d.code == mnt.CONNECT_RESERIALIZED else TAGS.get(d.verb)
            if tag:
                out.append((d.path, tag))
    return tuple(out)


def _engine_said(states, client=None) -> tuple[str, ...]:
    """The engine's own sentences for Details: each report's lines but
    its rows and the after-run warning the result line restates, each
    refusal's rows with their reasons, each failure's sentence, and where
    the log is when anything failed."""
    out: list[str] = []
    for state in states:
        out.extend(
            line for line in state.lines
            if line and not (client is not None and report.is_running_after(line, client)))
        if state.kind == REFUSED:
            out.extend(
                f"{mnt.one_line(d.path)}: {mnt.one_line(_reason(d) or '')}"
                for d in state.dispositions)
        if state.message:
            out.append(state.message)
    if any(state.kind == FAILED for state in states):
        log = setup_web.log_line()
        if log:
            out.append(log)
    return tuple(out)


def _settings_files(client, plan=None) -> list[Path]:
    """Every settings file a press over this client could change: the
    copies this machine holds for it, and any the project's record names
    (`plan`, the disconnect's own dry run)."""
    try:
        paths = [copy.path for copy in cn.detect(client)]
    except OSError:
        paths = []
    if plan is not None:
        paths += [Path(d.path) for d in plan.dispositions if d.path != cn.CONNECT_REL]
    return list(dict.fromkeys(paths))


def _contents(paths) -> dict[Path, bytes | None]:
    out: dict[Path, bytes | None] = {}
    for path in paths:
        try:
            out[path] = path.read_bytes()
        except OSError:
            out[path] = None
    return out


def _wrote_while_open(client, before: dict[Path, bytes | None]) -> bool:
    """Whether the press changed one of the client's settings files while
    the client was open: `connect.is_running` on the files whose bytes it
    changed, read off the disk, so a press that changed them and then
    stopped, before its report was written, is asked the same way."""
    if client is None:
        return False
    now = _contents(before)
    changed = [path for path, data in before.items() if now[path] != data]
    return bool(changed) and cn.is_running(client, changed)


def _agents_said(act: Action) -> str:
    """The agent a skill press names, by its label; both where it names
    none, as the bare command acts on both."""
    try:
        found = sk.harnesses(act.targets)
    except sk.SkillError:
        return mnt.one_line(act.harness)
    return " and ".join(h.label for h in found)


def _skill_of(path: str) -> str:
    """The skill a row's path is inside, read off the agent's skills
    folder, or the folder holding the file where it is not under one."""
    for harness in sk.HARNESSES.values():
        prefix = harness.skills.rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix):].split("/")[0]
    return Path(path).parent.name


def _kit_folder(path: str) -> bool:
    """Whether a folder row is an agent's skills folder or one above it,
    which stays while it holds anything else and says so."""
    folder = path.rstrip("/")
    return any(
        h.skills == folder or h.skills.startswith(folder + "/")
        for h in sk.HARNESSES.values())


def _folder_left(d: mnt.Disposition) -> bool:
    """Whether a removal's row is one of the skill's own files or folders
    left behind: not an edited file kept (said on its own), and not the
    skills folder above it staying for the other skills there."""
    if d.verb != mnt.LEFT or d.code in (mnt.SKILL_EDITED_KEPT, mnt.SKILL_DIR_SHARED):
        return False
    return not (d.code == mnt.LEFT_DIR_OCCUPIED and _kit_folder(d.path))


def _skill_said(act: Action, state: setup_web.PageState, listed: bool) -> tuple[str, str]:
    """A skill press's tone and sentence. `listed` is whether the agent's
    record listed the skill when the press arrived: an add over a listed
    skill is the grid's Update."""
    agent = _agents_said(act)
    skill = mnt.one_line(act.skill)
    said = {"skill": skill, "agent": agent}
    rows = state.dispositions
    if act.name == SKILL_ADD_ALL:
        if _untouched(state):
            return RESULT_FAILED, ALL_FAILED.format(**said)
        if state.kind == FAILED:
            return RESULT_PARTLY, ALL_STOPPED.format(**said)
        if state.outcome == report.NOOP or not _wrote(rows):
            return RESULT_DONE, ALL_ALREADY_ON.format(**said)
        edited: list[str] = []
        for d in rows:
            name = _skill_of(d.path)
            if d.code == mnt.SKILL_EDITED and name not in edited:
                edited.append(name)
        if len(edited) == 1:
            return RESULT_PARTLY, ALL_ADDED_KEPT.format(
                agent=agent, skill=mnt.one_line(edited[0]))
        if edited:
            return RESULT_PARTLY, ALL_ADDED_KEPT_MANY.format(agent=agent, count=len(edited))
        return RESULT_DONE, ALL_ADDED.format(**said)
    if act.name == SKILL_ADD:
        if _untouched(state):
            return RESULT_FAILED, (SKILL_UPDATE_FAILED if listed else SKILL_ADD_FAILED).format(**said)
        if state.kind == FAILED:
            return RESULT_PARTLY, (SKILL_UPDATE_STOPPED if listed else SKILL_ADD_STOPPED).format(**said)
        if state.outcome == report.NOOP:
            # The cell was drawn Off or Update; either way the skill is on.
            return RESULT_DONE, ALREADY_ON.format(**said)
        if any(d.code == mnt.SKILL_EDITED for d in rows):
            if listed:
                return RESULT_PARTLY, SKILL_UPDATE_EDITED.format(**said)
            return RESULT_DONE, ALREADY_ON.format(**said)
        return RESULT_DONE, (SKILL_UPDATED if listed else SKILL_ADDED).format(**said)
    if _untouched(state):
        return RESULT_FAILED, SKILL_REMOVE_FAILED.format(**said)
    if state.kind == FAILED:
        return RESULT_PARTLY, SKILL_REMOVE_STOPPED.format(**said)
    if state.outcome == report.NOOP:
        return RESULT_DONE, ALREADY_OFF.format(**said)
    kept = sum(1 for d in rows if d.code == mnt.SKILL_EDITED_KEPT)
    if kept and not any(d.code == mnt.REMOVED_FILE for d in rows):
        # Every file of the skill was edited, so none was removed; the
        # record no longer lists the skill.
        if kept == 1:
            return RESULT_PARTLY, SKILL_KEPT_UNTRACKED.format(**said)
        return RESULT_PARTLY, SKILL_KEPT_UNTRACKED_MANY.format(count=kept, **said)
    if kept == 1:
        return RESULT_PARTLY, SKILL_REMOVED_KEPT.format(**said)
    if kept:
        return RESULT_PARTLY, SKILL_REMOVED_KEPT_MANY.format(count=kept, **said)
    if any(_folder_left(d) for d in rows):
        return RESULT_PARTLY, SKILL_REMOVED_FOLDER.format(**said)
    return RESULT_DONE, SKILL_REMOVED.format(**said)


# A disconnect's row for a settings file that still holds the project's
# entry afterwards: every copy it left but one whose entry was already
# gone, or one it took the entry out of by rewriting the file.
_ENTRY_GONE = (mnt.CONNECT_KEY_GONE, mnt.CONNECT_RESERIALIZED)


def _connection_said(act: Action, state: setup_web.PageState, label: str,
                     root: str) -> tuple[str, str]:
    """Connect's or Disconnect's tone and sentence."""
    if act.name == CONNECT:
        if _untouched(state):
            return RESULT_FAILED, CONNECT_FAILED.format(label=label)
        if state.kind == FAILED:
            return RESULT_PARTLY, CONNECT_STOPPED.format(label=label)
        if state.outcome == report.NOOP:
            return RESULT_DONE, ALREADY_CONNECTED.format(label=label)
        return RESULT_DONE, CONNECTED_SAID.format(label=label)
    if _untouched(state):
        return RESULT_FAILED, DISCONNECT_FAILED.format(label=label)
    if state.kind == FAILED:
        if _stopped_at_record(state, root):
            return RESULT_PARTLY, DISCONNECTED_RECORD.format(label=label)
        return RESULT_PARTLY, DISCONNECT_STOPPED.format(label=label)
    if state.outcome == report.NOOP:
        return RESULT_DONE, ALREADY_DISCONNECTED.format(label=label)
    held = sum(
        1 for d in state.dispositions
        if d.path != cn.CONNECT_REL and d.verb == mnt.LEFT and d.code not in _ENTRY_GONE)
    if held == 1:
        return RESULT_PARTLY, DISCONNECTED_HELD.format(label=label)
    if held:
        return RESULT_PARTLY, DISCONNECTED_HELD_MANY.format(label=label, count=held)
    return RESULT_DONE, DISCONNECTED_SAID.format(label=label)


def perform_now(session: ManageSession, act: Action, origin: str) -> setup_web.PageState:
    """One small change, run on its one press, answered on the screen it
    was made on.

    The press runs the same runner the two-step press's confirm runs, so
    what the engine writes is what `COMMANDS` names. Two things are read
    before it, for the sentence alone: whether the agent's record lists
    the skill (an add over a listed skill is the grid's Update), and the
    keys a disconnect takes out, which the after-press warning names. A
    press naming no folder this page can act on answers as it always has.
    """
    target = _root_of(act)
    if target is None:
        return RUNNERS[act.name](act, dry_run=False)
    root = str(target)
    client = cn.CLIENTS.get(act.client)
    listed = False
    keys: tuple[str, ...] = ()
    before: dict[Path, bytes | None] = {}
    if act.name in SKILL_ACTIONS:
        listed = act.skill in dict(ro.verify(target).agents).get(act.harness, ())
    elif client is not None:
        plan = None
        if act.name == UNCONNECT:
            try:
                plan = cn.unconnect_plan(target, client.name)
                keys = plan.keys or ((plan.key,) if plan.key else ())
            except (cn.ConnectError, OSError):
                plan = None
        before = _contents(_settings_files(client, plan))
    press = RUNNERS[act.name](act, dry_run=False)
    warning = ""
    if act.name in SKILL_ACTIONS:
        tone, sentence = _skill_said(act, press, listed)
    else:
        label = client.label if client is not None else mnt.one_line(act.client)
        tone, sentence = _connection_said(act, press, label, root)
        if _wrote_while_open(client, before):
            if act.name == CONNECT:
                warning = RUNNING_AFTER_CONNECT.format(label=label)
            elif keys:
                warning = RUNNING_AFTER_REMOVED.format(
                    label=label, keys=" and ".join(mnt.one_line(k) for k in keys))
            else:
                warning = report.unconnect_running_after(client, keys)
    line = result_line(tone, sentence, warning=warning,
                       changed=_changed((press,)), notes=_engine_said((press,), client))
    return _answered(session, origin, root, press, line)


def _repair_said(origin: str, root: str, steps, label: str) -> tuple[str, str]:
    """A repair's tone and sentence, read off its two steps: the removal,
    then the fresh connect. The removal stopping wrote nothing unless the
    writer says otherwise; the connect failing after the removal ran
    leaves the project disconnected, and the line says so."""
    on_roster = origin == ROSTER
    said = {"label": label, "project": _name_parts(root)[0]}
    first = steps[0]
    if first.kind in (REFUSED, FAILED):
        if _untouched(first):
            return RESULT_FAILED, (ROSTER_REPAIR_FAILED if on_roster else REPAIR_FAILED).format(**said)
        if _stopped_at_record(first, root):
            return RESULT_PARTLY, (ROSTER_REPAIR_RECORD if on_roster else REPAIR_RECORD).format(**said)
        return RESULT_PARTLY, (ROSTER_REPAIR_STOPPED if on_roster else REPAIR_STOPPED).format(**said)
    second = steps[1] if len(steps) > 1 else None
    if second is None or second.kind not in (REFUSED, FAILED):
        return RESULT_DONE, (ROSTER_REPAIRED if on_roster else RECONNECTED_SAID).format(**said)
    if second.kind == FAILED and not _untouched(second):
        return RESULT_PARTLY, (ROSTER_REPAIR_STOPPED if on_roster else REPAIR_STOPPED).format(**said)
    if second.kind == REFUSED and any(
            d.code == mnt.CONNECT_KEY_HOST_OWNED for d in second.dispositions):
        return RESULT_PARTLY, (ROSTER_REPAIR_STALE if on_roster else REPAIR_STALE).format(**said)
    took_entry = any(
        d.path != cn.CONNECT_REL and (
            d.verb in (mnt.REMOVED, mnt.RESTORED, mnt.EXCISED)
            or d.code == mnt.CONNECT_RESERIALIZED)
        for d in first.dispositions)
    if took_entry:
        return RESULT_PARTLY, (
            ROSTER_REPAIR_NO_RECONNECT if on_roster else REPAIR_NO_RECONNECT).format(**said)
    return RESULT_PARTLY, (
        ROSTER_REPAIR_NO_RECONNECT_RECORD if on_roster else REPAIR_NO_RECONNECT_RECORD
    ).format(**said)


def perform_repair_now(session: ManageSession, root: str, origin: str) -> setup_web.PageState:
    """Repair on its one press, from the project's screen or a row of
    Projects (prescriptions 6 and 7).

    The guard is asked again here, inside the queue, because the screen
    the press came from was drawn earlier: where it withholds Repair now,
    nothing runs and the line says why in the words the status uses.
    Where it offers it, the two verbs run as today's confirm press runs
    them (`perform_repair`).
    """
    target = _resolved(root)
    if target is None:
        return perform_repair(root)
    root = str(target)
    facts = ro.verify(target)
    claimed = cn.CLIENTS.get(facts.claimed)
    label = claimed.label if claimed is not None else cn.label_phrase()
    said = {"label": label, "project": _name_parts(root)[0]}
    on_roster = origin == ROSTER
    if not _needs_repair(facts):
        press = setup_web.PageState(REPAIRED, report.NOOP, False, result=(root, ()))
        sentence = (ROSTER_NOTHING_TO_REPAIR if on_roster else NOTHING_TO_REPAIR_NOW)
        return _answered(session, origin, root, press,
                         result_line(RESULT_DONE, sentence.format(**said)))
    guard = _repair_guard(facts)
    if not guard.offered:
        press = setup_web.PageState(
            REFUSED, report.REFUSED, False, lines=(guard.why, *guard.blockers))
        if on_roster:
            line = result_line(RESULT_FAILED, ROSTER_REPAIR_WITHHELD.format(**said),
                               notes=(guard.why, *guard.blockers))
        else:
            line = result_line(RESULT_FAILED, guard.why, notes=guard.blockers)
        return _answered(session, origin, root, press, line)
    try:
        plan = cn.unconnect_plan(target, guard.client.name)
    except (cn.ConnectError, OSError):
        plan = None
    before = _contents(_settings_files(guard.client, plan))
    press = perform_repair(root)
    _, steps = press.result
    if not steps:
        sentence = (ROSTER_NOTHING_TO_REPAIR if on_roster else NOTHING_TO_REPAIR_NOW)
        return _answered(session, origin, root, press,
                         result_line(RESULT_DONE, sentence.format(**said)))
    client = guard.client
    tone, sentence = _repair_said(origin, root, steps, client.label)
    warning = ""
    if _wrote_while_open(client, before):
        warning = (RUNNING_AFTER_REPAIR_ROSTER if on_roster else RUNNING_AFTER_CONNECT
                   ).format(label=client.label, project=said["project"])
    line = result_line(tone, sentence, warning=warning,
                       changed=_changed(steps), notes=_engine_said(steps, client))
    return _answered(session, origin, root, press, line)


# --- Add a project, inside the app (PLAN-0027 phase 2) -----------------
# The screen is the setup page's own, drawn by its builders over this
# folder's own session, so the app and `radialrig setup` show one screen.
# What this page adds is what only it has: the way back to Projects, the
# press that opens another project's screen, and a server that goes on
# standing once a setup is done.

def _setup_frame(session: ManageSession, sub: setup_web.Session) -> setup_web.Frame:
    """What this page adds to Add a project: the way back to Projects,
    landing on the folder's own row where it has one, and the press that
    opens a project's screen."""
    action = f"{session.page_path}#{_row_id(str(sub.root))}"
    leave = (
        f'<form class="nav" id="{setup_web.LEAVE_ID}" method="get" '
        f'action="{_attr(action)}">'
        + _hidden((("s", session.token), ("view", ROSTER))) + "</form>"
    )

    def opener(folder: Path, label: str) -> str:
        return _go(session, PROJECT, _button(label, cls="primary"),
                   extra=(("root", str(folder)),))

    return setup_web.Frame(
        leave=leave, stands=True, opener=opener,
        running_after=RUNNING_AFTER_CONNECT)


def _setup_body(session: ManageSession, sub: setup_web.Session,
                state: setup_web.PageState) -> str:
    """One state of Add a project, drawn by the setup page's own builder
    over that page's session, inside this page's frame."""
    return setup_web.BODIES[state.kind](sub, state, _setup_frame(session, sub))


def _setup_missing_body(session: ManageSession, state: setup_web.PageState) -> str:
    """Add a project, for a folder that is not there (wording.md, Screen
    4, "Folder not found").

    The card's next step is the folder field itself, holding the path
    that was not found, so a slip in a typed path is mended where it
    shows. It is the field Projects carries, under the same ids: the one
    the app's folder picker writes into, with Choose a folder beside it,
    which ships hidden and which the app reveals. Continue opens Add a
    project on the folder the field names.
    """
    root = state.message
    action = session.page_path
    leave = (
        f'<form class="nav" id="{setup_web.LEAVE_ID}" method="get" '
        f'action="{_attr(action)}">'
        + _hidden((("s", session.token), ("view", ROSTER))) + "</form>"
    )
    frame = setup_web.Frame(leave=leave, stands=True)
    field = (
        f'<label class="sr" for="root">{_esc(ADD_FIELD_LABEL)}</label>'
        f'<input id="root" name="root" value="{_attr(root)}" autocomplete="off" '
        f'spellcheck="false" placeholder="{_attr(ADD_FIELD_PLACEHOLDER)}">'
        f'<button type="button" id="browse" hidden>{_esc(BROWSE_LABEL)}</button>'
    )
    press = (
        '<div class="add">'
        + _go(session, SETUP, _button(ADD_LABEL, cls="primary"), inner=field)
        + "</div>"
    )
    return setup_web.stopped_card(
        frame, head=NOT_FOUND_HEAD, path=root, said=NOT_FOUND_SAID, press=press)


CAPTIONS = {
    PLAN: PLAN_CAPTION,
    DONE: DONE_CAPTION,
    REFUSED: REFUSED_CAPTION,
}


def _back(session: ManageSession, act) -> str:
    """Where a press leads back to. Forms, so the server stands."""
    if act is not None and _root_of(act) is not None:
        return _project_backs(session, act.root)
    return _nav(session, ROSTER, ROSTER_BACK_LABEL)


def _said(state: setup_web.PageState) -> str:
    """Every line of the report but its first, as the terminal wrote it."""
    said = "".join(
        f'<p class="said">{_esc(line)}</p>' for line in list(state.lines)[1:])
    if state.message:
        said += f'<p class="said">{_esc(state.message)}</p>'
    return said


def _log_said(state: setup_web.PageState) -> str:
    """Where to look after a press that did not run (PLAN-0018 phase 2):
    the setup page's own log line, on the failed state and no other."""
    return setup_web.log_said() if state.kind == FAILED else ""


def _action_table(state: setup_web.PageState) -> str:
    """A press's rows, under the caption its own kind carries."""
    return _table(state.dispositions, CAPTIONS.get(state.kind, DONE_CAPTION),
                  _reason)


def _action_body(session: ManageSession, state: setup_web.PageState) -> str:
    """One press, in the command line's own words.

    The report's row block is drawn as a table and every other line of it
    is printed as the command line wrote it, so what the page says about
    the press is what the terminal would have said. A refusal carries no
    control that could force past it, because the command line has none.
    """
    act = state.result
    lines = list(state.lines)
    head = lines[0] if lines else ACTION_FAILED_HEAD
    said = _said(state) + _log_said(state)
    table = _action_table(state)
    command = _command_of(act)
    if state.kind == REFUSED:
        body = (
            '<div class="refusal" role="status" aria-live="polite">'
            f'<h1 class="head">{_esc(head)}</h1>'
            + table + "</div>" + said
        )
    else:
        body = f"<h1>{_esc(head)}</h1>" + table + said
    if state.kind == PLAN:
        body += command + _act_form(session, act, CONFIRM_LABEL, confirm=True)
    else:
        body += command
    return body + _back(session, act)


# --- the sequences' presses and the machine's removals -----------------

def _sequence_form(session: ManageSession, action: str, root: str, label: str,
                   *, confirm: bool = False) -> str:
    """The two presses a sequence runs on, drawn by the one builder.

    `_act_form`'s rule, kept here because this press is that press
    several times over: the offer leads to the engine's own rehearsal and
    writes nothing, and the button under that rehearsal is the one that
    writes, so the amber is on the second and on neither of the first.
    `action` is the sequence's key — the reversal's or the repair's —
    which is what the handler dispatches on.
    """
    fields = (("action", action), ("root", root))
    if confirm:
        fields += (("confirm", "yes"),)
    cls = "primary" if confirm else "ghost"
    return setup_web._form(
        session,
        _hidden(fields)
        + '<div class="actions">'
        + f'<button class="{cls}" type="submit">{_esc(label)}</button>'
        + "</div>",
    )


def _python_pin() -> str:
    """The Python this engine is running on, spelled the way uv names one.

    Read off the running interpreter rather than written down, because
    the command has to name the Python this engine is actually on: a
    version pinned in this file would go on naming an old one after an
    upgrade moved the engine onto a newer Python.
    """
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def uv_paths(row: Machine) -> tuple[str, str]:
    """The two uv programs, as absolute paths on this machine.

    The row names the directory under the home directory and the two
    filenames; the home directory is the one this process is running
    under rather than the variable's name (D2).
    """
    bin_dir = Path.home().joinpath(*row.uv_bin)
    first, second = row.uv_programs
    return str(bin_dir / first), str(bin_dir / second)


def uv_removal(row: Machine) -> str:
    """The row's delete line, with both paths written out."""
    uv, uvx = uv_paths(row)
    return row.uv_delete.format(uv=uv, uvx=uvx)


def machine_removals() -> tuple[tuple[str, str], ...]:
    """Every removal this page names and does not run, in the order to run.

    Named rather than offered, each for its own reason: the engine is the
    process serving this page, the app is the program that opened it, and
    the rest are what the app's first run put on this machine before
    either existed. A page that ran any of them would be removing the
    ground it is standing on.

    The order is the one that leaves each command something to run it —
    RadialRig first, then the Python that was installed for it, then uv
    last — and the entries with no command at all sit where the thing
    they name comes out. What varies by operating system comes from the
    `MACHINES` row. A row with no place for the app contributes no line
    saying so, but the Path slot is stated either way: on the platform
    where nothing was written, saying so is the statement (PLAN-0009
    phase 3 prescription 5), because a reader reversing everything
    should read that from the page rather than infer it from a row that
    is not there.
    """
    row = machine()
    rows: list[tuple[str, str]] = [(ENGINE_WHAT, ENGINE_REMOVAL)]
    if row and row.app_place:
        rows.append((APP_WHAT.format(place=row.app_place), ""))
    rows.append((PYTHON_WHAT, PYTHON_REMOVAL.format(version=_python_pin())))
    if row:
        rows.append((UV_CACHE_WHAT.format(cache=row.uv_cache), UV_CACHE_REMOVAL))
        rows.append((UV_WHAT, uv_removal(row)))
        if row.path_what:
            rows.append((row.path_what, ""))
    return tuple(rows)


def _step_report(state: setup_web.PageState) -> str:
    """One step of a reversal, answered the way the single press answers.

    A heading below the page's own, because the sequence is the answer
    here and each verb is a part of it; everything else is the report the
    command line printed for that verb.
    """
    act = state.result
    lines = list(state.lines)
    head = lines[0] if lines else ACTION_FAILED_HEAD
    body = _action_table(state) + _said(state) + _log_said(state)
    command = _command_of(act)
    if state.kind == REFUSED:
        return (
            '<div class="refusal" role="status" aria-live="polite">'
            f'<h2 class="head">{_esc(head)}</h2>'
            + body + "</div>" + command
        )
    return f"<h2>{_esc(head)}</h2>" + body + command


def _sequence_head(head: str, root: str) -> str:
    """A sequence page's heading, over the project it is about."""
    return (
        f"<h1>{_esc(head)}</h1>"
        '<div class="root"><span class="label">Project</span>'
        f"<code>{_esc(root)}</code></div>"
    )


def _project_backs(session: ManageSession, root: str) -> str:
    """The two ways back from a page about one project: to it, and to the
    listing. Forms, so the server stands."""
    return (
        _nav(session, PROJECT, PROJECT_BACK_LABEL, extra=(("root", root),))
        + _nav(session, ROSTER, ROSTER_BACK_LABEL)
    )


def _cleared_by_removal(step: setup_web.PageState) -> bool:
    """Whether a registration's dry run was refused only by entries the
    removal takes out.

    `CONNECT_ENTRY_STALE` is the engine's one refusal for a copy the
    record describes that still holds one of the record's keys, and its
    sentence is "run the removal first, then this command". The removal
    is the step before it in this sequence, so that refusal is the
    rehearsal reading a disk the removal has not changed yet, not a
    refusal the run would meet. Any other refusal — a key some other
    program owns, a file that will not parse — the removal leaves as it
    is, and the run would meet it again.
    """
    return (
        step.kind == REFUSED
        and bool(step.dispositions)
        and all(d.code == mnt.CONNECT_ENTRY_STALE for d in step.dispositions)
    )


def _repair_offered(steps) -> bool:
    """Whether the repair's confirm press stands under its rehearsal.

    The reversal's rule, with one allowance: the removal must have
    rehearsed clean, and every step after it must have rehearsed clean
    or been refused only by what the removal takes out. A removal that
    refused or failed would do so again, and a button over it would be a
    way past a refusal.
    """
    if not steps or steps[0].kind != PLAN:
        return False
    return all(step.kind == PLAN or _cleared_by_removal(step) for step in steps[1:])


def _repair_plan_body(session: ManageSession, state: setup_web.PageState) -> str:
    """The repair's rehearsal (PLAN-0014 phase 3): the removal's plan and
    the fresh registration's, each with its command in full, and the
    confirm press under them, leading back to the project.

    Where the registration's dry run was refused by the entry the removal
    takes out, that step is drawn as deferred rather than refused (see
    `_deferred_step`) and the press is still offered: the run itself
    meets no such refusal, because it runs the removal first.
    """
    root, steps = state.result
    if not steps:
        body = f'<p class="hint">{_esc(NOTHING_TO_REPAIR)}</p>'
    else:
        body = "".join(
            _deferred_step(step) if _cleared_by_removal(step) else _step_report(step)
            for step in steps
        )
        if _repair_offered(steps):
            body += _sequence_form(session, REPAIR, root, CONFIRM_LABEL, confirm=True)
    return (
        _sequence_head(REPAIR_PLAN_HEAD, root)
        + body
        + _project_backs(session, root)
    )


def _deferred_step(step: setup_web.PageState) -> str:
    """A registration the rehearsal could not run yet, said as such.

    The engine refused its dry run only for the entry the removal takes
    out, which is the one thing the run will have done by the time this
    step runs. Drawing the engine's refusal panel over a press that
    writes would be the page contradicting itself, so the step stands
    under its own heading with the sentence that says why and the
    command that runs it alone; the rows it would write are not shown,
    because no rehearsal can read them off this disk yet.
    """
    return (
        f"<h2>{_esc(REPAIR_AFTER_HEAD)}</h2>"
        f'<p class="note">{_esc(REPAIR_ORDER_NOTE)}</p>'
        + _command_of(step.result)
    )


def _repaired_body(session: ManageSession, state: setup_web.PageState) -> str:
    """What the repair did, verb by verb: the two reports, in their own words."""
    root, steps = state.result
    steps, warning = _one_repair_warning(steps)
    return (
        _sequence_head(REPAIRED_HEAD, root)
        + "".join(_step_report(step) for step in steps)
        + warning
        + _project_backs(session, root)
    )


def _one_repair_warning(steps) -> tuple[tuple, str]:
    """BUG-0018: the verbs' warnings, said once for the whole repair.

    The removal's warning names entries to delete if they come back, and
    the registration after it writes those same entries, so side by side
    the two would undo the repair. Each verb's after-run warning comes
    out of its report, and one sentence for the repair stands after both.
    """
    kept, client = [], None
    for step in steps:
        named = cn.CLIENTS.get(getattr(step.result, "client", ""))
        lines = tuple(
            line for line in step.lines
            if named is None or not report.is_running_after(line, named))
        if lines != tuple(step.lines):
            client = named
        kept.append(dataclasses.replace(step, lines=lines))
    if client is None:
        return tuple(steps), ""
    return tuple(kept), (
        f'<p class="closing">{_esc(report.repair_running_after(client))}</p>')


# --- Confirm a big change (PLAN-0027 phase 3) --------------------------
# Update, Update all and Remove from this project ask once before they
# run (prescription 1). The first press is the engine's own dry run,
# unchanged, drawn as one card: the question, one sentence of counts,
# only the rows that need attention, the button naming the action where
# today's plan page would offer its press (and the sentence saying why
# where it would not), and Cancel; every file and every command behind
# Details. The card's button is the confirm press, which runs inside the
# one-write queue and brings back the screen the change was asked from
# with one result line. No route is added: a post without `confirm` is
# the dry run, as it always was.

CONFIRM = "confirm-card"


@dataclasses.dataclass(frozen=True)
class _Confirm:
    """What a card is drawn from."""

    action: str                                # UPDATE, UPDATE_ALL or REVERSAL
    origin: str                                # the screen it was asked from
    root: str                                  # the project, resolved; "" for every project
    plan: setup_web.PageState                  # the dry run, as its press answers it
    keep: setup_web.PageState | None = None    # the unmount alone's, where the box shows
    facts: ro.ProjectState | None = None       # the project as the removal reads it
    warning: str = ""                          # the before-press sentence, drawn
    record: str = ""                           # a lost link's record command, where Repair can't


def _origin(origin: str, default: str) -> str:
    """The screen a press names, or the one it belongs to by default."""
    return origin if origin in ORIGINS else default


def _counted(count: int, one: str, many: str) -> str:
    return one if count == 1 else many.format(count=count)


def _files_said(count: int) -> str:
    return _counted(count, FILES_ONE, FILES_MANY)


def _skills_counted(count: int) -> str:
    return _counted(count, SKILLS_ONE, SKILLS_MANY)


def _projects_said(count: int) -> str:
    return _counted(count, PROJECTS_ONE, PROJECTS_MANY)


def _what(files: int, skills: int) -> str:
    """`{what}`: the files, the skills, or both, as the lines count them."""
    if files and skills:
        return BOTH.format(files=_files_said(files), skills=_skills_counted(skills))
    if skills:
        return _skills_counted(skills)
    return _files_said(files)


def _kept_said(count: int) -> str:
    """The suffix for files the person edited, which a change kept."""
    if not count:
        return ""
    return KEPT_EDIT_ONE if count == 1 else KEPT_EDIT_MANY.format(count=count)


def _joined(*parts: str) -> str:
    return " ".join(part for part in parts if part)


def _left_changed(sentence: str) -> bool:
    """Whether a stopped write's sentence names paths it couldn't put
    back: the writer's one wording (`mount.stopped_sentence`)."""
    return "; put back except: " in sentence


def _this(origin: str, root: str) -> dict[str, str]:
    """The project words a result line fills: "this project" on its own
    screen, its name on any other."""
    name = _name_parts(root)[0]
    here = origin == PROJECT
    return {
        "project": name,
        "this": THIS_PROJECT if here else name,
        "This": THIS_PROJECT.capitalize() if here else name,
        "this_s": THIS_PROJECTS if here else f"{name}'s",
        "from_project": "" if here else FROM_PROJECT.format(project=name),
    }


def _cancel(session: ManageSession, origin: str, root: str) -> str:
    """Cancel: back to the screen the change was asked from, unchanged."""
    extra = (("root", root),) if origin == PROJECT else ()
    return _go(session, _origin(origin, ROSTER),
               _button(CANCEL_LABEL, cls="link quiet"), extra=extra)


def _confirm_press(session: ManageSession, fields, label: str, origin: str,
                   *, part: str = "") -> str:
    """The card's one press: the change, confirmed, naming the screen it
    returns to. `part` names the half of the removal card it belongs to."""
    form = setup_web._form(
        session,
        _hidden((*fields, ("confirm", "yes"), ("from", origin)))
        + _button(label, cls="primary"))
    return f'<div class="{part}">{form}</div>' if part else form


def _attention(rows) -> str:
    """The rows that need attention: each file, and why it is left."""
    items = "".join(
        f"<li><code>{_esc(path)}</code><span>{_esc(label)}</span></li>"
        for path, label in rows)
    return f'<ul class="attention">{items}</ul>' if items else ""


def _file_list(rows) -> str:
    """Details' list: every file, tagged with what will happen to it."""
    items = "".join(
        f"<li><code>{_esc(path)}</code>"
        + (f" {MIDDOT} {_esc(tag)}" if tag else "") + "</li>"
        for path, tag in rows)
    return f'<ul class="files">{items}</ul>' if items else ""


def _paras(lines) -> str:
    return "".join(f"<p>{_esc(line)}</p>" for line in lines if line)


def _card(session: ManageSession, confirm: _Confirm, *, head: str, body: str,
          details: str, zone: str) -> str:
    """The card: the question, its body, Details, and the press beside
    Cancel. It is the whole screen, so it stands in the middle."""
    return (
        '<div class="screen confirm"><div class="stage"><section class="card">'
        f"<h1>{_esc(head)}</h1>" + body
        + f"<details><summary>{_esc(DETAILS_LABEL)}</summary>"
        f'<div class="drawer">{details}</div></details>'
        + f'<div class="action-zone">{zone}'
        + _cancel(session, confirm.origin, confirm.root) + "</div>"
        "</section></div></div>"
    )


# The seed rows an update leaves, by why it leaves them. Only a file the
# person edited is "Keeping your edits"; a file kept for another reason
# says that reason (wording.md, Screen 5).
_EDITED_UPGRADE = (
    mnt.UPGRADE_PENDING, mnt.UPGRADE_SECTION_PENDING, mnt.UPGRADE_ENTRY_PENDING,
    mnt.UPGRADE_ENTRY_NEW_PENDING, mnt.UPGRADE_SECTION_REFUSED)
_REMOVED_UPGRADE = (
    mnt.UPGRADE_ABSENT, mnt.UPGRADE_ENTRY_ABSENT, mnt.UPGRADE_ENTRY_REMOVED,
    mnt.UPGRADE_ENTRY_HELD_REMOVED)
_OWN_UPGRADE = (
    mnt.UPGRADE_NEW_LEFT, mnt.UPGRADE_NEW_SECTION_LEFT, mnt.UPGRADE_ENTRY_NEW_LEFT,
    mnt.UPGRADE_MCP_HOST_OWNED)
_UNREAD_UPGRADE = (mnt.UPGRADE_SECTION_UNREADABLE, mnt.UPGRADE_MCP_UNREADABLE)
_FOREIGN_SKILL = (mnt.SKILL_FILE_FOREIGN, mnt.SKILL_DIR_FOREIGN)


def _seed_row(item) -> tuple[str, str, str]:
    """One file of an update: its path, Details' tag, and the attention
    row's words, or "" where it needs none."""
    path = mnt.one_line(item.path)
    if item.verb == mnt.CURRENT:
        return path, TAG_UP_TO_DATE, ""
    if item.verb == mnt.CREATED:
        return path, TAG_WILL_ADD, ""
    if item.verb not in (mnt.LEFT, mnt.ABSENT):
        return path, TAG_WILL_UPDATE, ""
    if item.code in _EDITED_UPGRADE:
        return path, TAG_KEEPING, KEEPING_EDITS
    if item.code == mnt.UPGRADE_HELD:
        return path, TAG_KEPT_AS_IS, KEPT_WITH_PARTNER.format(
            partner=mnt.one_line(item.detail))
    if item.verb == mnt.ABSENT or item.code in _REMOVED_UPGRADE:
        return path, TAG_KEPT_AS_IS, KEPT_REMOVED
    if item.code in _OWN_UPGRADE:
        return path, TAG_KEPT_AS_IS, KEPT_OWN
    if item.code in _UNREAD_UPGRADE:
        return path, TAG_KEPT_AS_IS, KEPT_UNREAD
    return path, TAG_KEPT_AS_IS, KEPT_OTHER


def _skill_row(result) -> tuple[str, str, str]:
    """One agent's copy of one skill under an update, said the same way.
    An agent whose record can't be read is one row, at its record."""
    if result.error:
        return (result.harness.record, TAG_KEPT_AS_IS,
                KEPT_AGENT.format(agent=result.harness.label))
    path = f"{result.harness.skills}/{mnt.one_line(result.name)}/"
    if result.wrote:
        return path, TAG_WILL_UPDATE, ""
    if result.edited:
        return path, TAG_KEEPING, KEEPING_EDITS
    left = [d for d in result.dispositions if d.verb in (mnt.LEFT, mnt.ABSENT)]
    if not left:
        return path, TAG_UP_TO_DATE, ""
    if any(d.code in _FOREIGN_SKILL for d in left):
        return path, TAG_KEPT_AS_IS, KEPT_OWN
    if any(d.verb == mnt.ABSENT for d in left):
        return path, TAG_KEPT_AS_IS, KEPT_REMOVED
    return path, TAG_KEPT_AS_IS, KEPT_OTHER


def _update_card_rows(plan) -> list[tuple[str, str, str]]:
    """Every row of one project's update, the files then the skills."""
    return [*(_seed_row(item) for item in plan.seed.items),
            *(_skill_row(r) for r in plan.skills)]


def _skill_files(result) -> list[tuple[str, str]]:
    """Details' rows for one agent's copy of one skill: each path the
    engine names for it, so a copy the update writes lists every file it
    writes (phase 3 review R9). A copy it leaves keeps the copy's tag on
    each of its rows, and an agent whose record can't be read is its one
    row."""
    path, tag, _ = _skill_row(result)
    if result.error or not result.dispositions:
        return [(path, tag)]
    if not result.wrote:
        return [(mnt.one_line(d.path), tag) for d in result.dispositions]
    return [(mnt.one_line(d.path),
             TAG_UP_TO_DATE if d.verb == mnt.CURRENT
             else TAG_WILL_ADD if d.verb == mnt.CREATED else TAG_WILL_UPDATE)
            for d in result.dispositions]


def _update_files(plan) -> list[tuple[str, str]]:
    """Details' rows for one project's update: every file, then every
    file of each skill copy."""
    return [*((p, t) for p, t, _ in (_seed_row(item) for item in plan.seed.items)),
            *(row for r in plan.skills for row in _skill_files(r))]


def _skills_written(plan) -> int:
    """`{skills}`: each skill once, whichever agents it is updated for."""
    return len({r.name for r in plan.skills if r.wrote})


def _edits_kept(plan) -> int:
    return sum(1 for _, tag, _ in _update_card_rows(plan) if tag == TAG_KEEPING)


def _plan_notes(plan) -> tuple[str, ...]:
    """What the engine said that the rows don't: a skills half that
    stopped, and each agent whose record it couldn't read."""
    return tuple(line for line in (
        plan.skills_error, *(r.error for r in plan.unread_agents)) if line)


def confirm_update(session: ManageSession, act: Action, origin: str) -> setup_web.PageState:
    """Update one project's first press: `radialrig upgrade --dry-run`'s
    own step, drawn as the card. Writes nothing."""
    target = _root_of(act)
    if target is None:
        return _no_directory(act)
    root = str(target)
    plan = _update(Action(UPDATE, root=root), dry_run=True)
    return setup_web.PageState(
        CONFIRM, report.OK, False, result=_Confirm(UPDATE, origin, root, plan))


def confirm_update_all(session: ManageSession) -> setup_web.PageState:
    """Update all's first press: `radialrig upgrade --all --dry-run`'s
    own step over the same listing, drawn as the card."""
    plan = _update_all(Action(UPDATE_ALL), dry_run=True)
    return setup_web.PageState(
        CONFIRM, report.OK, False, result=_Confirm(UPDATE_ALL, ROSTER, "", plan))


def _update_card(session: ManageSession, confirm: _Confirm) -> str:
    """Update {project}? The press shows where the dry run would write
    something; where the engine stopped, or there is nothing to write,
    or the project isn't set up, the card says so and has no press. Today's
    page offered its press in the last two as well; the card is narrower,
    which prescription 1 allows."""
    name = _name_parts(confirm.root)[0]
    plan = confirm.plan
    act = Action(UPDATE, root=confirm.root)
    rows: list[tuple[str, str, str]] = []
    notes: tuple[str, ...] = ()
    press = ""
    if plan.kind == UPDATE_PLAN:
        update_plan = plan.result[1]
        rows = _update_card_rows(update_plan)
        notes = _plan_notes(update_plan)
        if update_plan.wrote:
            said = WILL_UPDATE.format(what=_what(
                update_plan.seed.applied, _skills_written(update_plan)))
            press = _confirm_press(session, act.fields, UPDATE_LABEL, confirm.origin)
        elif update_plan.review:
            said = UPDATE_KEPT_ONLY
        else:
            said = UPDATE_NOTHING
    elif plan.kind in (FAILED, REFUSED):
        said = UPDATE_REFUSED
        notes = _engine_said((plan,))
    else:
        said = UPDATE_NOT_SET_UP
    files = _update_files(plan.result[1]) if plan.kind == UPDATE_PLAN else []
    details = (
        (f"<h3>{_esc(name)}</h3>" + _file_list(files) if files else "")
        + _paras(notes) + _show_commands() + _commands(act))
    return _card(
        session, confirm, head=UPDATE_HEAD.format(project=name),
        body=f"<p>{_esc(said)}</p>" + _attention((p, w) for p, _, w in rows if w),
        details=details, zone=press)


def _update_all_card(session: ManageSession, confirm: _Confirm) -> str:
    """Update {count} projects? The count is the projects whose update
    writes something; the press shows where any does, and a project the
    engine can't update is an attention row that says the run skips it,
    as today's page offered the press beside it."""
    _, found = confirm.plan.result
    act = Action(UPDATE_ALL)
    writing = [item for item in found if item.plan is not None and item.plan.wrote]
    stuck = [item for item in found if item.error]
    attention: list[tuple[str, str]] = []
    details = ""
    for item in found:
        name = _name_parts(item.root)[0]
        if item.plan is not None:
            rows = _update_card_rows(item.plan)
            attention += [(f"{name}/{p}", w) for p, _, w in rows if w]
            details += (f"<h3>{_esc(name)}</h3>"
                        + _file_list(_update_files(item.plan))
                        + _paras(_plan_notes(item.plan)))
        elif item.error:
            details += f"<h3>{_esc(name)}</h3>" + _paras((item.error,))
    for item in stuck:
        name = _name_parts(item.root)[0]
        attention.append((name, (UPDATE_ALL_SKIPS if writing else UPDATE_ALL_STUCK)
                          .format(project=name)))
    press = ""
    if writing:
        head = _counted(len(writing), UPDATE_ALL_HEAD_ONE, UPDATE_ALL_HEAD_MANY)
        said = WILL_UPDATE.format(what=_what(
            sum(i.plan.seed.applied for i in writing),
            sum(_skills_written(i.plan) for i in writing)))
        press = _confirm_press(session, act.fields, UPDATE_ALL_LABEL, ROSTER)
    else:
        head = UPDATE_ALL_HEAD_NONE
        if any(item.plan is not None and item.plan.review for item in found):
            said = UPDATE_KEPT_ONLY
        else:
            said = UPDATE_ALL_OTHERS if stuck else UPDATE_ALL_NOTHING
    return _card(
        session, confirm, head=head,
        body=f"<p>{_esc(said)}</p>" + _attention(attention),
        details=details + _show_commands() + _commands(act), zone=press)


# The rows a removal leaves because the person edited them. A folder that
# stays because it holds their own files is said by "Your own files stay
# in place", and isn't one of these.
_EDITED_REMOVAL = (
    mnt.LEFT_EDITED, mnt.LEFT_SECTION_EDITED, mnt.LEFT_MCP_ENTRY_CHANGED,
    mnt.SKILL_EDITED_KEPT)


def _removal_rows(states) -> list[tuple[str, str, str]]:
    """Every path the steps name, one already gone included: its path,
    Details' tag, and the attention row's words where the person edited
    it."""
    out = []
    for state in states:
        for d in state.dispositions:
            path = mnt.one_line(d.path)
            if d.verb == mnt.ABSENT:
                out.append((path, TAG_ALREADY_GONE, ""))
            elif d.verb != mnt.LEFT:
                out.append((path, "", ""))
            elif d.code in _EDITED_REMOVAL:
                out.append((path, TAG_KEEPING, KEEPING_EDITS))
            else:
                out.append((path, TAG_KEPT_AS_IS, ""))
    return out


def _skills_summary(facts: ro.ProjectState) -> str:
    """A removal's `{skills}`: how many, and for which agents, such as
    "2 skills for both agents" or "3 skills for Claude Code and 2 for
    Codex". The labels are the harness map's."""
    agents = dict(facts.agents)
    held = [(h, tuple(agents.get(h.name, ()))) for h in sk.HARNESSES.values()]
    held = [(h, names) for h, names in held if names]
    if not held:
        return ""
    if (len(held) == len(sk.HARNESSES) > 1
            and len({frozenset(names) for _, names in held}) == 1):
        shared = SKILLS_FOR_BOTH if len(held) == 2 else SKILLS_FOR_EVERY
        return shared.format(skills=_skills_counted(len(held[0][1])))
    (first, names), *rest = held
    parts = [SKILLS_FOR.format(skills=_skills_counted(len(names)), agent=first.label)]
    parts += [SKILLS_ALSO.format(count=len(n), agent=h.label) for h, n in rest]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _removal_label(facts: ro.ProjectState) -> str:
    """The client a removal speaks of: the connected one, or the one a
    lost link's record names."""
    client = cn.CLIENTS.get(facts.connected)
    return client.label if client is not None else _lost_label(facts)


def _reversal_offered(plan: setup_web.PageState) -> bool:
    """Today's rehearsal rule (`_rehearsal_body` until phase 3): the press
    stands only where there are steps and every one rehearsed clean."""
    if plan.kind != REHEARSAL:
        return False
    _, steps = plan.result
    return bool(steps) and all(step.kind == PLAN for step in steps)


def _keep_offered(keep: setup_web.PageState | None) -> bool:
    """The unmount alone's own rule, the single press's: its dry run
    answered a plan, and there are files to take out."""
    return keep is not None and keep.kind == PLAN and keep.outcome != report.NOOP


def _unconnect_plan(target: Path, client):
    try:
        return cn.unconnect_plan(target, client.name)
    except (cn.ConnectError, OSError):
        return None


def confirm_removal(session: ManageSession, root: str, origin: str) -> setup_web.PageState:
    """Remove from this project's first press: today's whole reversal as
    the engine's dry run, and, where the keep box shows, the unmount
    alone's own dry run beside it, each with its own offer check
    (prescription 2). Writes nothing."""
    target = _resolved(root)
    if target is None:
        return _missing(root)
    root = str(target)
    facts = ro.verify(target)
    plan = plan_reversal(root)
    keep = None
    if facts.mounted and (facts.skills or facts.connected in cn.CLIENTS):
        keep = _unmount(Action(UNMOUNT, root=root), dry_run=True)
    warning = ""
    client = cn.CLIENTS.get(facts.connected)
    if client is not None and _reversal_offered(plan):
        warning = _running_warning(
            client, _settings_files(client, _unconnect_plan(target, client)))
    record = _record_line(facts, _repair_guard(facts)) if _needs_repair(facts) else ""
    return setup_web.PageState(
        CONFIRM, report.OK, False,
        result=_Confirm(REVERSAL, origin, root, plan, keep, facts, warning, record))


def _removes_said(facts: ro.ProjectState, *, keep: bool = False, stuck: bool = False) -> str:
    """What the removal takes out, in wording.md's words, for the default
    or, with `keep`, for the box ticked. `stuck` is a lost link whose
    record Repair can't clear, whose remedy is the command in Details."""
    label = _removal_label(facts)
    client = cn.CLIENTS.get(facts.connected)
    skills = _skills_summary(facts)
    lost = _needs_repair(facts)
    if keep:
        if client is not None and facts.skills:
            said = KEEPS_BOTH
        elif client is not None:
            said = KEEPS_CONNECTION
        else:
            said = KEEPS_SKILLS
    elif facts.mounted:
        if client is not None and facts.skills:
            said = REMOVES_ALL
        elif facts.skills:
            said = REMOVES_FILES_SKILLS
        elif client is not None:
            said = REMOVES_FILES_CONNECTION
        else:
            said = REMOVES_FILES
    elif client is not None and facts.skills:
        said = REMOVES_UNMOUNTED_BOTH
    elif facts.skills:
        said = REMOVES_UNMOUNTED_SKILLS
    else:
        said = REMOVES_UNMOUNTED_CONNECTION
    said = said.format(skills=skills, label=label)
    if lost:
        said = _joined(said, (REMOVES_LOST_RUN if stuck else REMOVES_LOST_TAIL)
                       .format(label=label))
    return said


def _keep_label(facts: ro.ProjectState) -> str:
    client = cn.CLIENTS.get(facts.connected)
    if client is None:
        return KEEP_SKILLS_LABEL
    if facts.skills:
        return KEEP_BOTH_LABEL.format(label=client.label)
    return KEEP_CONNECTION_LABEL.format(label=client.label)


def _removal_card(session: ManageSession, confirm: _Confirm) -> str:
    """Remove RadialRig from {project}? The default takes out what
    today's reversal does; the box, where it shows, swaps in the unmount
    alone, its sentence, its rows, its commands and its press. Each half
    shows its press only where its own plan offers one."""
    facts = confirm.facts
    root = confirm.root
    name = _name_parts(root)[0]
    _, steps = confirm.plan.result if confirm.plan.kind == REHEARSAL else ("", ())
    full = reversal_steps(facts)
    press = warn = ""
    rows: list[tuple[str, str, str]] = []
    notes: tuple[str, ...] = ()
    stuck = bool(confirm.record)
    if not full:
        said = (REMOVE_RECORD_RUN if stuck else REMOVE_RECORD_ONLY if _needs_repair(facts)
                else REMOVE_NOTHING)
    elif _reversal_offered(confirm.plan):
        said = _removes_said(facts, stuck=stuck)
        rows = _removal_rows(steps)
        warn = confirm.warning
        press = _confirm_press(
            session, (("action", REVERSAL), ("root", root)), REMOVE_LABEL,
            confirm.origin, part="remove-all" if confirm.keep is not None else "")
    else:
        said = REMOVE_REFUSED
        notes = _engine_said(steps or (confirm.plan,))
    body = (f"<p>{_esc(said)}</p>"
            + _attention((p, w) for p, _, w in rows if w) + warn)
    details = (_file_list((p, t) for p, t, _ in rows) + _paras(notes)
               + _commands(*full))
    zone = press
    if confirm.keep is not None:
        keep = confirm.keep
        keep_rows: list[tuple[str, str, str]] = []
        keep_notes: tuple[str, ...] = ()
        keep_press = ""
        if _keep_offered(keep):
            keep_said = _removes_said(facts, keep=True, stuck=stuck)
            keep_rows = _removal_rows((keep,))
            keep_press = _confirm_press(
                session, Action(UNMOUNT, root=root).fields, REMOVE_LABEL,
                confirm.origin, part="keep-only")
        else:
            keep_said = REMOVE_REFUSED
            keep_notes = _engine_said((keep,))
        body = (
            f'<div class="remove-all">{body}</div>'
            f'<div class="keep-only"><p>{_esc(keep_said)}</p>'
            + _attention((p, w) for p, _, w in keep_rows if w) + "</div>"
            + '<label class="check keep"><input type="checkbox" id="keep">'
            f"{_esc(_keep_label(facts))}</label>"
        )
        details = (
            f'<div class="remove-all">{details}</div>'
            '<div class="keep-only">'
            + _file_list((p, t) for p, t, _ in keep_rows) + _paras(keep_notes)
            + _commands(Action(UNMOUNT, root=root)) + "</div>"
        )
        zone = press + keep_press
    if stuck:
        # Where the record is and the command that takes it out, for
        # either half: the removal keeps the record both ways (review R5).
        details += _paras((RECORD_AT.format(path=Path(root) / cn.CONNECT_REL),)) + confirm.record
    return _card(
        session, confirm, head=REMOVE_HEAD.format(project=name), body=body,
        details=details + _show_commands(), zone=zone)


def _confirm_body(session: ManageSession, state: setup_web.PageState) -> str:
    confirm = state.result
    if confirm.action == UPDATE:
        return _update_card(session, confirm)
    if confirm.action == UPDATE_ALL:
        return _update_all_card(session, confirm)
    return _removal_card(session, confirm)


# --- the confirm presses, run (PLAN-0027 phase 3) ------------------------
# Each runs the runner the command line's verb runs, unchanged, inside the
# one-write queue, and answers on the screen it was asked from with the
# line from `wording.md` that is true of what the engine reports. A
# sequence asks its offer again first, since the card was drawn earlier.

def _update_changed(plan, prefix: str = "") -> tuple[tuple[str, str], ...]:
    """The files an update changed or kept, tagged, then each skill copy
    it updated or kept."""
    out = []
    for item in plan.seed.items:
        tag = TAGS.get(item.verb)
        if tag:
            out.append((prefix + item.path, tag))
    for r in plan.skills:
        if r.name and (r.wrote or r.edited):
            out.append((f"{prefix}{r.harness.skills}/{r.name}/",
                        TAG_UPDATED if r.wrote else TAG_KEPT))
    return tuple(out)


def _update_said(press: setup_web.PageState, said: dict) -> tuple:
    """One update's tone, sentence, changed files and Details notes."""
    here = not said["from_project"]
    if press.kind == UPDATED:
        plan = press.result[1]
        files = plan.seed.applied
        changed, notes = _update_changed(plan), _plan_notes(plan)
        unread = _joined(*(AGENT_UNREAD.format(agent=r.harness.label)
                           for r in plan.unread_agents))
        if plan.skills_error:
            left = _left_changed(plan.skills_error)
            if files:
                head = UPDATE_HALF_STOPPED.format(
                    files=_files_said(files),
                    in_project="" if here else f" in {said['project']}")
                return (RESULT_PARTLY, _joined(head, HALF_LEFT_CHANGED if left else ""),
                        changed, notes)
            if left:
                return RESULT_PARTLY, UPDATE_STOPPED.format(**said), changed, notes
            return RESULT_FAILED, UPDATE_FAILED.format(**said), changed, notes
        if not plan.wrote and plan.review:
            # Nothing written, but something left for review: Details
            # lists each row the card did, in its words (review R1).
            held = tuple((p, w) for p, _, w in _update_card_rows(plan) if w)
            return (RESULT_PARTLY if unread else RESULT_DONE,
                    _joined(UPDATE_KEPT_NOW.format(**said),
                            _kept_said(_edits_kept(plan)), unread), held, notes)
        if not plan.wrote:
            return (RESULT_DONE, _joined(UPDATE_CURRENT_NOW.format(**said), unread),
                    changed, notes)
        what = _what(files, _skills_written(plan))
        head = (UPDATED_SAID.format(what=what) if here
                else UPDATED_IN.format(what=what, where=said["project"]))
        return (RESULT_PARTLY if unread else RESULT_DONE,
                _joined(head, _kept_said(_edits_kept(plan)), unread), changed, notes)
    if press.kind in (FAILED, REFUSED):
        notes = _engine_said((press,))
        if _untouched(press):
            return RESULT_FAILED, UPDATE_FAILED.format(**said), (), notes
        return RESULT_PARTLY, UPDATE_STOPPED.format(**said), (), notes
    return RESULT_DONE, UPDATE_UNMOUNTED_NOW.format(**said), (), ()


def perform_update_now(session: ManageSession, act: Action, origin: str) -> setup_web.PageState:
    """Update one project's confirm press: `radialrig upgrade <folder>`'s
    own step, answered on the screen it was asked from."""
    target = _root_of(act)
    if target is None:
        return _update(act, dry_run=False)
    root = str(target)
    press = _update(Action(UPDATE, root=root), dry_run=False)
    tone, sentence, changed, notes = _update_said(press, _this(origin, root))
    return _answered(session, origin, root, press,
                     result_line(tone, sentence, changed=changed, notes=notes))


def _update_all_said(found) -> tuple:
    """Update all's tone, sentence, changed files and Details notes."""
    wrote = [i for i in found if i.plan is not None and i.plan.wrote]
    failed = [i for i in found if i.error]
    halves = [i for i in found if i.plan is not None and i.plan.skills_error]
    changed, notes = [], []
    for item in found:
        name = _name_parts(item.root)[0]
        if item.plan is not None:
            changed += _update_changed(item.plan, prefix=f"{name}/")
            notes += [f"{name}: {line}" for line in _plan_notes(item.plan)]
        elif item.error:
            notes.append(f"{name}: {item.error}")
    if failed:
        log = setup_web.log_line()
        if log:
            notes.append(log)
    what = _what(sum(i.plan.seed.applied for i in wrote),
                 sum(_skills_written(i.plan) for i in wrote))
    projects = _projects_said(len(wrote))
    if failed:
        first = _name_parts(failed[0].root)[0]
        left = any(_left_changed(i.error) for i in failed)
        count = len(failed)
        if wrote:
            head = (UPDATE_ALL_SOME_ONE if count == 1 else UPDATE_ALL_SOME_MANY).format(
                what=what, projects=projects, project=first, count=count)
            tail = (ITS_LEFT_CHANGED if count == 1 else HALF_LEFT_CHANGED) if left else ""
            return RESULT_PARTLY, _joined(head, tail), tuple(changed), tuple(notes)
        # Nothing wrote, so a project whose skills write stopped is one
        # this couldn't update too, and its stop may have left files
        # changed (phase 3 review R2).
        failed += halves
        failed.sort(key=found.index)
        first = _name_parts(failed[0].root)[0]
        left = any(_left_changed(i.error or i.plan.skills_error) for i in failed)
        count = len(failed)
        if left:
            head = (UPDATE_ALL_STOPPED_ONE if count == 1 else UPDATE_ALL_STOPPED_MANY)
            return (RESULT_PARTLY, head.format(project=first, count=count),
                    tuple(changed), tuple(notes))
        head = (UPDATE_ALL_FAILED_ONE if count == 1 else UPDATE_ALL_FAILED_MANY)
        return (RESULT_FAILED, head.format(project=first, count=count),
                tuple(changed), tuple(notes))
    if halves:
        first = halves[0]
        name = _name_parts(first.root)[0]
        left = _left_changed(first.plan.skills_error)
        if wrote:
            head = UPDATE_ALL_HALF.format(what=what, projects=projects, project=name)
            return (RESULT_PARTLY, _joined(head, HALF_LEFT_CHANGED if left else ""),
                    tuple(changed), tuple(notes))
        head = UPDATE_ALL_STOPPED_ONE if left else UPDATE_ALL_FAILED_ONE
        return (RESULT_PARTLY if left else RESULT_FAILED, head.format(project=name),
                tuple(changed), tuple(notes))
    kept = sum(_edits_kept(i.plan) for i in found if i.plan is not None)
    if not wrote:
        held = [(f"{_name_parts(i.root)[0]}/{p}", w)
                for i in found if i.plan is not None and i.plan.review
                for p, _, w in _update_card_rows(i.plan) if w]
        if held:
            # Nothing written, but something left for review (review R1).
            return (RESULT_DONE, _joined(UPDATE_ALL_KEPT_NOW, _kept_said(kept)),
                    tuple(held), tuple(notes))
        return RESULT_DONE, UPDATE_ALL_CURRENT_NOW, tuple(changed), tuple(notes)
    return (RESULT_DONE, _joined(UPDATED_IN.format(what=what, where=projects),
                                 _kept_said(kept)), tuple(changed), tuple(notes))


def perform_update_all_now(session: ManageSession) -> setup_web.PageState:
    """Update all's confirm press: `radialrig upgrade --all`'s own step,
    answered on Projects."""
    press = _update_all(Action(UPDATE_ALL), dry_run=False)
    _, found = press.result
    tone, sentence, changed, notes = _update_all_said(found)
    return _answered(session, ROSTER, "", press,
                     result_line(tone, sentence, changed=changed, notes=notes))


def _step_lines(steps) -> tuple[str, ...]:
    """Every step's own report lines and sentence, for the log: a
    sequence press is one event, and each command it ran is in it."""
    out: list[str] = []
    for step in steps:
        out.extend(line for line in step.lines if line)
        if step.message:
            out.append(step.message)
    return tuple(out)


def _ran(steps, name: str) -> bool:
    """Whether a step of this kind ran and did something."""
    return any(
        getattr(step.result, "name", "") == name and step.kind == DONE
        and step.outcome != report.NOOP for step in steps)


def _took_entry_out(steps) -> bool:
    """Whether a removal's disconnect took the project's entry out of a
    settings file: a copy it restored, cut, rewrote or removed. One that
    left every copy as it was, such as a file RadialRig created and the
    person has since edited, dropped only the project's record."""
    return any(
        d.path != cn.CONNECT_REL and (_wrote((d,)) or d.code == mnt.CONNECT_RESERIALIZED)
        for step in steps if getattr(step.result, "name", "") == UNCONNECT
        for d in step.dispositions)


def _lost_remedy(origin: str, project: str, guard, facts: ro.ProjectState) -> str:
    """How a lost link's leftover record comes out, said where the person
    lands: Repair on the project's screen where it is offered, the way to
    that screen from elsewhere, and, where Repair is withheld (a project
    named other than its folder, say), the command behind Details that
    takes it out, or the record's place where no command can be filled."""
    if guard is not None and not guard.offered:
        return LOST_RUN_REMEDY if _record_line(facts, guard) else LOST_NO_REMEDY
    if origin == PROJECT:
        return LOST_REMEDY
    return LOST_REMEDY_ELSEWHERE.format(project=project)


def _removal_said(steps, facts: ro.ProjectState, said: dict, origin: str,
                  guard) -> tuple[str, str]:
    """A removal's tone and sentence, from where its steps stopped."""
    label = _removal_label(facts)
    stopped = steps[-1] if steps and steps[-1].kind in (REFUSED, FAILED) else None
    if stopped is not None:
        if len(steps) == 1 and _untouched(stopped):
            return RESULT_FAILED, REMOVE_FAILED.format(**said)
        touched = any(
            getattr(step.result, "name", "") == UNCONNECT
            and (step.kind == DONE or (step.kind == FAILED and not _untouched(step)))
            for step in steps)
        return RESULT_PARTLY, _joined(
            REMOVE_STOPPED.format(**said),
            RESTART_SAID.format(label=label) if touched else "")
    files, skills, link = _ran(steps, UNMOUNT), _ran(steps, SKILL_REMOVE), _ran(steps, UNCONNECT)
    if not (files or skills or link):
        return RESULT_DONE, REMOVE_NOTHING_NOW.format(**said)
    kept = _kept_said(sum(1 for _, tag, _ in _removal_rows(steps) if tag == TAG_KEEPING))
    if _needs_repair(facts):
        removed = (REMOVED_FILES_AND_SKILLS if files and skills
                   else REMOVED_FILES_PART if files else REMOVED_SKILLS_PART)
        sentence = (f"{removed}{said['from_project']}. " + REMOVED_LOST.format(
            label=label, remedy=_lost_remedy(origin, said["project"], guard, facts),
            **said))
        return RESULT_DONE, _joined(sentence, kept)
    # Said from what the disconnect reported: where it took the entry out
    # of no settings file, the connection stands there (review R6).
    record_only = link and not _took_entry_out(steps)
    link = link and not record_only
    if files and skills and link:
        sentence = REMOVED_ALL
    elif files and skills:
        sentence = REMOVED_FILES_SKILLS
    elif files and link:
        sentence = REMOVED_FILES_CONNECTION
    elif files:
        sentence = REMOVED_FILES_ONLY
    elif skills and link:
        sentence = REMOVED_BOTH_UNMOUNTED
    elif skills:
        sentence = REMOVED_SKILLS_ONLY
    elif link:
        sentence = REMOVED_CONNECTION_ONLY
    else:
        sentence = ""
    held = sum(
        1 for step in steps if getattr(step.result, "name", "") == UNCONNECT
        for d in step.dispositions
        if d.path != cn.CONNECT_REL and d.verb == mnt.LEFT and d.code not in _ENTRY_GONE)
    one, many = (HELD_RECORD_ONE, HELD_RECORD_MANY) if record_only else (HELD_ONE, HELD_MANY)
    held_said = "" if not held else (one if held == 1 else many).format(
        label=label, count=held, **said)
    return (RESULT_PARTLY if held else RESULT_DONE,
            _joined(sentence.format(label=label, **said), kept, held_said))


def _after_removed(client, keys) -> str:
    """The after-press sentence for a press that took the client's
    entries out while it was open, as `perform_now` says it."""
    label = client.label
    if keys:
        return RUNNING_AFTER_REMOVED.format(
            label=label, keys=" and ".join(mnt.one_line(k) for k in keys))
    return report.unconnect_running_after(client, keys)


def _record_note(facts: ro.ProjectState, guard) -> tuple[str, ...]:
    """Where a lost link's record stays, for Details, where Repair can't
    clear it from here."""
    if guard is None or guard.offered:
        return ()
    return (RECORD_AT.format(path=Path(facts.root) / cn.CONNECT_REL),)


def _record_line(facts: ro.ProjectState, guard) -> str:
    """Where Repair can't clear a lost link's record from here, the filled
    disconnect that takes it out, as a line to copy: the first of the two
    repair commands the removal page's mismatch note gave (phase 3 review
    R5). "" where Repair is offered, or where the record names no client
    this build knows, or the command can't be filled safely."""
    if guard is None or guard.offered:
        return ""
    steps = repair_steps(facts)
    if not steps or not steps[0].named or not steps[0].command:
        return ""
    return _copy_line(steps[0].command)


def perform_removal_now(session: ManageSession, root: str, origin: str) -> setup_web.PageState:
    """Remove from this project's confirm press, the default: today's
    whole reversal (`perform_reversal`), answered on the screen it was
    asked from (prescription 2).

    The offer is asked again inside the queue, because the card was drawn
    earlier: where the rehearsal no longer runs clean, nothing runs and
    the line says so. The disconnect's keys and the client's settings are
    read before the run, so the after-press warning is asked as
    `perform_now` asks it. A removal that stops partway says some of it
    was removed and the rest is still here, because the steps before the
    stop wrote.
    """
    target = _resolved(root)
    if target is None:
        return perform_reversal(root)
    root = str(target)
    said = _this(origin, root)
    facts = ro.verify(target)
    if not reversal_steps(facts):
        press = setup_web.PageState(REVERSED, report.NOOP, False, result=(root, ()))
        guard = _repair_guard(facts) if _needs_repair(facts) else None
        stuck = _record_line(facts, guard)
        sentence = (RECORD_RUN_NOW if stuck else RECORD_ONLY_NOW if guard is not None
                    else REMOVE_NOTHING_NOW)
        return _answered(session, origin, root, press, result_line(
            RESULT_DONE, sentence.format(**said),
            notes=_record_note(facts, guard) if stuck else (), more=stuck))
    plan = plan_reversal(root)
    if not _reversal_offered(plan):
        _, steps = plan.result if plan.kind == REHEARSAL else ("", ())
        notes = _engine_said(steps or (plan,))
        press = setup_web.PageState(REFUSED, report.REFUSED, False, lines=notes)
        return _answered(session, origin, root, press, result_line(
            RESULT_FAILED, REMOVE_WITHHELD.format(**said), notes=notes))
    client = cn.CLIENTS.get(facts.connected)
    keys: tuple[str, ...] = ()
    before: dict[Path, bytes | None] = {}
    if client is not None:
        disconnect = _unconnect_plan(target, client)
        if disconnect is not None:
            keys = disconnect.keys or ((disconnect.key,) if disconnect.key else ())
        before = _contents(_settings_files(client, disconnect))
    press = perform_reversal(root)
    _, steps = press.result
    guard = _repair_guard(ro.verify(target)) if _needs_repair(facts) else None
    tone, sentence = _removal_said(steps, facts, said, origin, guard)
    warning = (_after_removed(client, keys)
               if client is not None and _wrote_while_open(client, before) else "")
    line = result_line(
        tone, sentence, warning=warning, changed=_changed(steps),
        notes=_engine_said(steps, client) + _record_note(facts, guard),
        more=_record_line(facts, guard))
    return _answered(session, origin, root,
                     dataclasses.replace(press, lines=_step_lines(steps)), line)


def _unmount_said(press: setup_web.PageState, facts: ro.ProjectState, said: dict,
                  origin: str, guard) -> tuple[str, str]:
    """The keep box's press, the unmount alone: its tone and sentence."""
    if press.kind in (REFUSED, FAILED):
        if _untouched(press):
            return RESULT_FAILED, FILES_FAILED.format(**said)
        # The writer names the record it stopped at first, root-relative
        # where it is under the project (`mount._stopped`).
        record = (mnt.MANIFEST_REL, str(Path(facts.root) / mnt.MANIFEST_REL))
        if any(press.message.startswith(f"file error at {path}:") for path in record):
            return RESULT_PARTLY, FILES_RECORD.format(**said)
        return RESULT_PARTLY, FILES_STOPPED.format(**said)
    if press.outcome == report.NOOP:
        return RESULT_DONE, FILES_GONE_NOW.format(**said)
    label = _removal_label(facts)
    client = cn.CLIENTS.get(facts.connected)
    kept = _kept_said(sum(1 for _, tag, _ in _removal_rows((press,)) if tag == TAG_KEEPING))
    if _needs_repair(facts):
        sentence = _joined(
            KEPT_SKILLS_SAID.format(**said) if facts.skills
            else f"{REMOVED_FILES_PART}{said['from_project']}.",
            REMOVED_LOST.format(
                label=label, remedy=_lost_remedy(origin, said["project"], guard, facts),
                **said))
        return RESULT_DONE, _joined(sentence, kept)
    if client is not None and facts.skills:
        sentence = KEPT_BOTH_SAID
    elif facts.skills:
        sentence = KEPT_SKILLS_SAID
    elif client is not None:
        sentence = KEPT_CONNECTION_SAID
    else:
        sentence = REMOVED_FILES_ONLY
    return RESULT_DONE, _joined(sentence.format(label=label, **said), kept)


def perform_unmount_now(session: ManageSession, act: Action, origin: str) -> setup_web.PageState:
    """Remove from this project's confirm press with the box ticked: the
    unmount alone (`radialrig unmount`), under its own offer check, asked
    again inside the queue (prescription 2). It never writes the client's
    settings, so it carries no running warning."""
    target = _root_of(act)
    if target is None:
        return _unmount(act, dry_run=False)
    root = str(target)
    said = _this(origin, root)
    facts = ro.verify(target)
    unmount = Action(UNMOUNT, root=root)
    plan = _unmount(unmount, dry_run=True)
    if plan.kind != PLAN:
        notes = _engine_said((plan,))
        return _answered(session, origin, root, plan, result_line(
            RESULT_FAILED, REMOVE_WITHHELD.format(**said), notes=notes))
    if plan.outcome == report.NOOP:
        return _answered(session, origin, root, plan, result_line(
            RESULT_DONE, FILES_GONE_NOW.format(**said)))
    press = _unmount(unmount, dry_run=False)
    guard = _repair_guard(ro.verify(target)) if _needs_repair(facts) else None
    tone, sentence = _unmount_said(press, facts, said, origin, guard)
    line = result_line(tone, sentence, changed=_changed((press,)),
                       notes=_engine_said((press,)) + _record_note(facts, guard),
                       more=_record_line(facts, guard))
    return _answered(session, origin, root, press, line)


def _cleanup_said(states, said: dict, root: str) -> tuple[str, str]:
    """Clean up's tone and sentence, from where its commands stopped."""
    stopped = states[-1] if states[-1].kind in (REFUSED, FAILED) else None
    if stopped is not None:
        if len(states) == 1:
            if stopped.kind == REFUSED and Path(root).is_dir():
                return RESULT_FAILED, CLEAN_BACK.format(**said)
            if _untouched(stopped):
                return RESULT_FAILED, CLEAN_FAILED.format(**said)
        return RESULT_PARTLY, CLEAN_STOPPED.format(**said)
    if all(state.outcome == report.NOOP for state in states):
        return RESULT_DONE, CLEAN_GONE.format(**said)
    if any(d.code == mnt.CONNECT_UNPARSED_LEFT for state in states for d in state.dispositions):
        return RESULT_PARTLY, CLEAN_PARTLY.format(**said)
    return RESULT_DONE, CLEANED.format(**said)


def perform_cleanup_now(session: ManageSession, act: Action) -> setup_web.PageState:
    """Clean up on its one press, from a row of Projects (prescription 5).

    The row stands for a gone folder, which usually leaves two entries,
    so the press reads the folder's entries again here, inside the queue,
    and runs each one's `--orphan` command in turn, the command the line
    under Show commands names, stopping at the first that stops. Each
    command leaves its own event in the activity log. A press naming one
    key, as a form drawn before phase 3 posts it, cleans that entry.
    `perform_now` can't serve it: an entry has no project folder.
    """
    client = cn.CLIENTS.get(act.client)
    label = client.label if client is not None else mnt.one_line(act.client)
    found = [o for o in ro.orphans() if o.client == act.client and o.root]
    if act.root:
        root = act.root
        entries = [o for o in found
                   if os.path.normcase(o.root) == os.path.normcase(root)]
    else:
        entries = [o for o in found if o.key == act.key]
        root = entries[0].root if entries else ""
    said = {"label": label, "project": _name_parts(root)[0] if root else mnt.one_line(act.key)}
    if not entries:
        if root and Path(root).is_dir():
            press = setup_web.PageState(REFUSED, report.REFUSED, False, result=act)
            line = result_line(RESULT_FAILED, CLEAN_BACK.format(**said))
        else:
            press = setup_web.PageState(DONE, report.NOOP, False, result=act)
            line = result_line(RESULT_DONE, CLEAN_GONE.format(**said))
        return _answered(session, ROSTER, "", press, line)
    before: dict[Path, bytes | None] = {}
    if client is not None:
        copies = [Path(c) for o in entries for c in o.copies]
        before = _contents(list(dict.fromkeys([*_settings_files(client), *copies])))
    states: list[setup_web.PageState] = []
    for entry in entries:
        state = _orphan(Action(ORPHAN, client=entry.client, key=entry.key), dry_run=False)
        states.append(state)
        if state.kind in (REFUSED, FAILED):
            break
    tone, sentence = _cleanup_said(states, said, root)
    warning = (RUNNING_AFTER_CLEAN_UP.format(label=label)
               if _wrote_while_open(client, before) else "")
    line = result_line(tone, sentence, warning=warning, changed=_changed(states),
                       notes=_engine_said(states, client))
    return _answered(session, ROSTER, "", states[0], line, more=tuple(states[1:]))


# --- Remove RadialRig (PLAN-0027 phase 3) -------------------------------
# Numbered steps (prescription 3): each listed project with its Remove,
# or the line saying nothing is left; the engine's removal commands, every
# one of them in today's order, with one Copy; and, where today's page has
# one for this system, the app's uninstall, last (prescription 4). What
# stays afterward sits behind Details.

def _step_project(session: ManageSession, facts: ro.ProjectState, steps,
                  stuck: bool = False) -> str:
    """One project's row of step 1: its name and Remove, which opens
    Confirm a big change and comes back here; or why there's no press.
    `stuck` is a lost link's record Repair can't clear, whose command What
    stays afterward gives."""
    name, _ = _name_parts(facts.root)
    # The folder's whole path is the name's tooltip: the row is short, so
    # a handful of them share a line.
    who = f'<span class="name" title="{_attr(facts.root)}">{_esc(name)}</span>'
    if steps:
        end = _ask(session, (("action", REVERSAL), ("root", facts.root)), _button(
            STEP_REMOVE, name=ROW_BUTTON_NAME.format(action=STEP_REMOVE, project=name)),
            origin=REMOVAL)
    elif _needs_repair(facts):
        end = f'<span class="facts">{_esc(STEP_RECORD_RUN if stuck else STEP_RECORD_LEFT)}</span>'
    else:
        end = f'<span class="facts">{_esc(STEP_NOTHING_LEFT)}</span>'
    return f'<li class="removal-row">{who}{end}</li>'


def _engine_commands() -> str:
    """Step 2's commands, every one `machine_removals` names, in its
    order, in one block with one Copy that copies them all."""
    lines = [line for _, line in machine_removals() if line]
    text = "\n".join(lines)
    return (
        f'<div class="command-block"><pre>{_esc(text)}</pre>'
        f'<button class="copy" type="button" data-copy="{_attr(text)}" '
        f'aria-label="{_attr(COPY_NAME.format(command=STEP_ENGINE))}">'
        f"{_esc(COPY_LABEL)}</button></div>"
    )


def _stays(found, records=()) -> str:
    """What stays afterward: on Linux first the PATH sentence, which no
    step says there; then the history; then any leftover entries; then
    each lost link's record Repair can't clear, with the command that
    takes it out (`records`, each a project and its line to copy)."""
    row = machine()
    lines = []
    if row is not None and not row.app_place and row.path_what:
        lines.append(row.path_what)
    path = ro.roster_path()
    if path is not None:
        lines.append(STAYS_SAID.format(data_dir=path.parent))
    pinned = [o for o in found if o.root]
    if pinned:
        labels = []
        for entry in pinned:
            client = cn.CLIENTS.get(entry.client)
            if client is not None and client not in labels:
                labels.append(client)
        lines.append((STAYS_LEFTOVER_ONE if len(pinned) == 1 else STAYS_LEFTOVER_MANY)
                     .format(label=cn.label_phrase(labels or None), count=len(pinned)))
    commands = "".join(
        _paras((STAYS_RECORD.format(project=_name_parts(facts.root)[0]),)) + line
        for facts, line in records)
    return (f'<details class="stays"><summary>{_esc(STAYS_LABEL)}</summary>'
            f'<div class="drawer">{_paras(lines)}{commands}</div></details>')


def _removal_body(session: ManageSession, state: setup_web.PageState,
                  result: str = "") -> str:
    """Remove RadialRig: the way back, the heading, the result line of a
    project removed from here, and the steps."""
    plans, found = state.result
    row = machine()
    back = _go(
        session, ROSTER,
        f'<button class="link back" type="submit"><span aria-hidden="true">'
        f"{GLYPH_BACK}</span>{_esc(ROSTER_HEAD)}</button>")
    records = [(facts, _record_line(facts, _repair_guard(facts)))
               for facts, steps in plans if not steps and _needs_repair(facts)]
    records = [(facts, line) for facts, line in records if line]
    stuck = {facts.root for facts, _ in records}
    rows = "".join(_step_project(session, facts, steps, facts.root in stuck)
                   for facts, steps in plans)
    projects = (f'<ul class="removal-rows">{rows}</ul>' if rows
                else f'<p class="facts">{_esc(STEP_PROJECTS_NONE)}</p>')
    shell = (STEP_ENGINE_WINDOWS if row is not None and row.platform == "win32"
             else STEP_ENGINE_TERMINAL)
    steps = (
        f"<li><h2>{_esc(STEP_PROJECTS)}</h2>{projects}</li>"
        f"<li><h2>{_esc(STEP_ENGINE)}</h2><p>{_esc(shell)}</p>{_engine_commands()}</li>"
    )
    if row is not None and row.app_place:
        steps += (f"<li><h2>{_esc(STEP_APP)}</h2>"
                  f"<p>{_esc(_joined(STEP_APP_SAID, row.path_what))}</p></li>")
    notice = f'<p class="notice">{_esc(state.message)}</p>' if state.message else ""
    return (
        '<div class="screen removal">' + back
        + f"<h1>{_esc(REMOVAL_HEAD)}</h1>" + result + notice
        + f'<ol class="numbered">{steps}</ol>'
        + _stays(found, records) + "</div>"
    )


BODIES = {
    ROSTER: _roster_body,
    PROJECT: _project_body,
    REMOVAL: _removal_body,
    REPAIR_PLAN: _repair_plan_body,
    REPAIRED: _repaired_body,
    CONFIRM: _confirm_body,
    ANSWERED: _answered_body,
    SETUP_MISSING: _setup_missing_body,
    PLAN: _action_body,
    DONE: _action_body,
    REFUSED: _action_body,
    FAILED: _action_body,
}


def _roster_view(session: ManageSession, fields) -> setup_web.PageState:
    return roster_state(session)


def _project_view(session: ManageSession, fields) -> setup_web.PageState:
    return project_state(session, (fields.get("root") or [""])[0])


def _removal_view(session: ManageSession, fields) -> setup_web.PageState:
    return removal_state(session)


VIEWS = {
    ROSTER: _roster_view,
    PROJECT: _project_view,
    REMOVAL: _removal_view,
}


# --- the server -------------------------------------------------------

class ManageHandler(setup_web.SetupHandler):
    """The setup server's handler, answering the manage route.

    Four overrides and the helpers they lean on: the state a GET
    renders, the state a POST renders, the document either arrives in,
    and the log event a one-press change leaves. The tokens, the launch redemption, the host and origin guards,
    the policy header, the close action and the error boundary are the
    same code path this package has served since PLAN-0005, not a second
    copy of it.
    """

    server_version = "radialrig-manage"
    # The refusals are inherited whole; only the two words naming the page
    # they were refused for are this surface's own.
    surface = SURFACE
    page_name = PAGE_NAME
    # The log's name for this surface, and the resolver its presses' rows
    # read through (PLAN-0018 phase 2).
    log_surface = al.MANAGE_PAGE
    _reason = staticmethod(_reason)

    def _pressed(self, fields) -> str:
        """A press names an action this page dispatches; a navigation
        that arrived as a post names none, and leaves no event. The
        confirm press says so, since it is the one that writes."""
        name = (fields.get("action") or [""])[0]
        if name not in WALK_ACTIONS and name not in SEQUENCES and name not in COMMANDS:
            return ""
        if (fields.get("confirm") or [""])[0]:
            return f"{name} confirm"
        return name

    def _folder(self, fields=None) -> str:
        """The project a press or a request is about: the walkthrough's
        where one is open, else the root the form or the query names."""
        if self._sub is not None:
            return str(self._sub.root)
        given = (fields or {}).get("root") or parse_qs(
            urlparse(self.path).query).get("root") or [""]
        return given[0]

    # The walkthrough this request is about, and whether the state it
    # answers ended one. Both are read by the renderer alone, and both are
    # set again on every request this instance serves.
    _sub: setup_web.Session | None = None
    _done: bool = False

    def _settled(self, state: setup_web.PageState) -> setup_web.PageState:
        """Any state this page renders, never terminal.

        A state that arrives terminal — a finished walkthrough does —
        would set `_stop_with` on the way out and stop a server the
        reader is still using, so the flag is cleared here for every
        state this page renders, once, rather than at each builder. What
        the flag said is kept, because a walkthrough that has ended still
        has to read as ended.
        """
        self._done = state.terminal
        return dataclasses.replace(state, terminal=False)

    def _view(self, name: str, fields) -> setup_web.PageState:
        self._sub = None
        if name == SETUP:
            # The one view that answers with a session as well as a state:
            # its bodies are the setup page's, and they read the session
            # they were written for.
            state, self._sub = _walk_state(
                self.session, (fields.get("root") or [""])[0])
            return self._settled(state)
        builder = VIEWS.get(name, _roster_view)
        return self._settled(builder(self.session, fields))

    def _press(self, fields) -> setup_web.PageState:
        """A walkthrough press, on the project its own form names.

        The form posts to the manage route carrying that project in the
        query, so a press reaches the walkthrough it was drawn for even
        where two folders are open at once. It queues with every other
        consequential press: these are the setup page's mount and skills
        presses, and those write.

        The folder comes through the one guard every other press uses. It
        has to: this press mounts, so a query naming `.` rather than a
        project would seed the directory this server was started in.
        """
        root = (parse_qs(urlparse(self.path).query).get("root") or [""])[0]
        target = _resolved(root)
        if target is None:
            return _missing(root)
        self._sub = walkthrough(self.session, target)
        with self.session.actions:
            return setup_now(self.session, self._sub, fields)

    def _survey(self) -> setup_web.PageState:
        query = parse_qs(urlparse(self.path).query)
        return self._view((query.get("view") or [ROSTER])[0], query)

    def _log_press(self, fields, state: setup_web.PageState) -> None:
        """A one-press change logs the runner's own answer, as its two-step
        twin's confirm does, not the screen it brought back; a press that
        ran several commands one after another, as Clean up does, logs
        each. Confirm a big change logs the dry run it was drawn from."""
        if state.kind == ANSWERED:
            super()._log_press(fields, state.result.press)
            for more in state.result.more:
                super()._log_press(fields, more)
            return
        if state.kind == CONFIRM:
            state = state.result.plan
        super()._log_press(fields, state)

    def _apply(self, fields) -> setup_web.PageState:
        """A press, or a navigation that arrived as one.

        The confirm press is the only thing on this page that writes, so
        it is the only thing that queues: two tabs on a threading server
        can interleave, and the engine is written for one verb at a time.
        A plan press and a view read nothing that a queue would protect.

        A confirm press whose form names the screen it was made on (`from`,
        Projects, one project or Remove RadialRig) is one of the small
        changes that run at once: it runs inside the same queue and
        answers on that screen with one result line (PLAN-0027 phase 1).
        Without `from` a single verb's confirm press lands on today's
        result page.

        The big changes (phase 3) always take the two steps of Confirm a
        big change: Update, Update all and Remove from this project answer
        a press without `confirm` with the card, and run with it, coming
        back to the screen named by `from`, or the one they belong to.
        Their keep box's press is the unmount's own confirm, named so. A
        Clean up names its folder rather than one key, and runs each of
        that folder's entries in turn.
        """
        self._sub = None
        name = (fields.get("action") or [""])[0]
        confirm = bool((fields.get("confirm") or [""])[0])
        origin = (fields.get("from") or [""])[0]
        at_once = confirm and origin in ORIGINS
        if name in WALK_ACTIONS:
            return self._settled(self._press(fields))
        if name in SEQUENCES:
            # The same two steps every press below takes. These are the
            # most consequential presses on the page — several verbs over
            # a whole project — so they are the last that should reach the
            # engine off a single post.
            plan, perform = SEQUENCES[name]
            root = (fields.get("root") or [""])[0]
            if confirm:
                with self.session.actions:
                    if name == REPAIR and at_once:
                        return self._settled(
                            perform_repair_now(self.session, root, origin))
                    if name == REVERSAL:
                        return self._settled(perform_removal_now(
                            self.session, root, _origin(origin, PROJECT)))
                    return self._settled(perform(root))
            if name == REVERSAL:
                return self._settled(
                    confirm_removal(self.session, root, _origin(origin, PROJECT)))
            return self._settled(plan(root))
        if name in COMMANDS:
            act = Action(
                name=name,
                root=(fields.get("root") or [""])[0],
                skill=(fields.get("skill") or [""])[0],
                client=(fields.get("client") or [""])[0],
                key=(fields.get("key") or [""])[0],
                harness=(fields.get("harness") or [""])[0],
            )
            if confirm:
                with self.session.actions:
                    if name in ONE_PRESS and at_once:
                        return self._settled(perform_now(self.session, act, origin))
                    if name == UNMOUNT and at_once:
                        return self._settled(perform_unmount_now(self.session, act, origin))
                    if name == ORPHAN and at_once:
                        return self._settled(perform_cleanup_now(self.session, act))
                    if name == UPDATE:
                        return self._settled(perform_update_now(
                            self.session, act, _origin(origin, PROJECT)))
                    if name == UPDATE_ALL:
                        return self._settled(perform_update_all_now(self.session))
                    return self._settled(RUNNERS[name](act, dry_run=False))
            if name == UPDATE:
                return self._settled(
                    confirm_update(self.session, act, _origin(origin, PROJECT)))
            if name == UPDATE_ALL:
                return self._settled(confirm_update_all(self.session))
            return self._settled(RUNNERS[name](act, dry_run=True))
        return self._view((fields.get("view") or [ROSTER])[0], fields)

    def _body(self, state: setup_web.PageState) -> str:
        """The body for a state, drawn by whichever kit owns its kind."""
        if state.kind in setup_web.BODIES:
            # The walkthrough's builders read the terminal flag this page
            # clears, so it is given back to them here: a walkthrough that
            # has ended is over for its project even though the server it
            # is embedded in goes on standing.
            return _setup_body(
                self.session, self._sub,
                dataclasses.replace(state, terminal=self._done))
        return BODIES[state.kind](self.session, state)

    def _render(self, state: setup_web.PageState, nonce: str) -> str:
        # The stamp and the id are the inherited send's: this composes
        # the document and nothing more.
        return setup_web.document(
            self.session,
            body=self._body(state),
            terminal=state.terminal,
            nonce=nonce,
            title=TITLE,
        )

    # --- the hang-up that never reaches handle_error (D10) ------------

    def _internal(self, exc: Exception) -> None:
        """The shared error seam, with a peer's hang-up absorbed here too.

        `absorb_hangups` binds the absorber on the server, and the server
        only sees what reaches it: an exception raised inside `do_GET` or
        `do_POST` is caught there and reported through this seam instead,
        so a hang-up during the POST body read printed a traceback line
        for something no reader did and then tried to answer a socket
        that was already gone.

        Absorbed means absorbed: nothing on stderr, and no refusal
        written to a peer that is not there to read one. The server goes
        on standing. Every other exception reports exactly as it always
        did, and the setup surface — which does not override this — keeps
        today's behavior whole.
        """
        if isinstance(exc, HANGUPS):
            return
        super()._internal(exc)


def _print_url(out, url: str) -> None:
    """The pinned line, first, in bytes where the stream has them.

    Phase 5 spawns this command and reads this line off a pipe, so it
    leaves as ASCII with one LF whatever the console is: a text stream on
    Windows answers the console codepage and translates the newline into
    two bytes, and neither is this line's to carry. A stream with no
    `buffer` — a test's `StringIO`, a redirected object — is written
    through as text.
    """
    line = f"{URL_PREFIX}{url}\n"
    buffer = getattr(out, "buffer", None)
    if buffer is None:
        out.write(line)
        out.flush()
        return
    out.flush()
    buffer.write(line.encode("ascii"))
    buffer.flush()


def absorb_hangups(httpd) -> None:
    """Let a peer that hung up hang up quietly. D10.

    A browser abandoning its close beacon and a probe closing early both
    reach the server as a dropped connection on a handler thread, and the
    default handler answers by printing a traceback — for something no
    reader did, on a run where nothing failed. Both are absorbed here,
    whenever they arrive; every other exception reaches the default
    handler it always reached, which is taken before this one is bound.

    This is one of the two places a hang-up can surface. The other is
    inside a request, where `do_GET` and `do_POST` catch it before the
    server ever sees it; `ManageHandler._internal` absorbs it there, on
    the same terms.

    Bound on the instance, never through a subclass. `serve` reads
    `ThreadingHTTPServer` off this module and a test replaces that module
    attribute to drive the bind failure; a subclass declared at import
    time would have captured the real class long before that test ran,
    and would sit there binding a real port while the test waited for a
    refusal.
    """
    default = httpd.handle_error

    def absorb(request, client_address):
        kind = sys.exc_info()[0]
        if kind is not None and issubclass(kind, HANGUPS):
            return
        default(request, client_address)

    httpd.handle_error = absorb


def serve(*, open_browser: bool = True, on_ready=None, stream=None) -> int:
    """Run the manage page until it closes. Returns the exit code.

    0 on a clean stop, whether the page closed itself or Ctrl-C stopped
    it, and 1 when the local server could not start. Nothing a reader
    does on the page moves that number: the page is a standing surface,
    and what an action did is in the page's own sentences.
    """
    out = stream if stream is not None else sys.stdout
    session = ManageSession()
    handler = type("BoundManageHandler", (ManageHandler,), {"session": session})
    try:
        httpd = ThreadingHTTPServer((setup_web.HOST, 0), handler)
    except OSError as exc:
        sys.stderr.write(f"radialrig: could not start the local server: {exc}\n")
        return report.ERROR
    httpd.daemon_threads = True
    absorb_hangups(httpd)
    session.port = httpd.server_port
    # The page's own two events (PLAN-0018 phase 2), the port and never
    # the URL, which carries the launch token.
    al.record(al.MANAGE_PAGE, f"started on {setup_web.HOST}:{session.port}")

    # The URL first, before any other write reaches this stream, so the
    # parsed line is the first line in the stream's real byte order.
    _print_url(out, session.url)
    out.write(f"{LOCAL_ONLY}\n")
    if open_browser:
        try:
            opened = webbrowser.open(session.url)
        except Exception:  # a headless box has no browser; the URL still works
            opened = False
        out.write(f"{OPENED if opened else NOT_OPENED}\n")
    out.flush()

    try:
        setup_web.run_server(httpd, session, on_ready=on_ready)
    except KeyboardInterrupt:
        out.write("\n")
    out.write(f"{STOPPED_LINE}\n")
    out.flush()
    al.record(al.MANAGE_PAGE, "stopped", outcome=f"exit {session.outcome}")
    return session.outcome
