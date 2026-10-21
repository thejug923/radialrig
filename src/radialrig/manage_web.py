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
# The masthead and the browser tab, in the owner's Title Case naming. The
# shared document builder writes this into both, so the name a reader
# reads on the page is the name the tab carries.
TITLE = "RadialRig Project Manager"

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
# The sentences the page says in its own voice. Every sentence about a
# project's state comes out of `report` and `roster` instead, so the page
# and the terminal give one account of one machine.

ROSTER_HEAD = "Projects"
ROSTER_CAPTION = "What stands on this machine"
OPEN_LABEL = "Open"

# --- a project's state, in the page's own words (PLAN-0014 constraint 3)
# The command line keeps its words — `mounted`, `registered with`,
# `skills:` — where programs read them, and the listing says the same
# three facets through this fixed mapping, one constant per cell of it.
# The connection facts are templates: the client's label is filled from
# the client table at render time, so no constant here names a client
# (constraint 5). The negative names the client too, because a bare "Not
# connected" read as though the mount's own registration had failed. It
# names only a client installed on this machine (PLAN-0014's narrowing
# of PLAN-0008 phase 4 rule 4); where none is, the row says nothing
# about connecting at all (the owner's answer of 2026-10-01).
# The mismatch row's connection fact is the engine's own sentence,
# `roster.mismatch_clause`, which names the client the record claims
# (phase 3): one template in `roster.py`, said on both surfaces.
MOUNTED_FACT = "Mounted"
NOT_MOUNTED_FACT = "Not mounted"
CONNECTED_FACT = "Connected to {label}"
NOT_CONNECTED_FACT = "Not connected to {label}"
SKILLS_FACT = "{count} skills"
ONE_SKILL_FACT = "1 skill"
NO_SKILLS_FACT = "No skills"
# The same three spellings, one line per agent, where the agents' lists
# differ (PLAN-0015 phase 2 prescription 5). The label is the harness
# map's, filled at render time, so no constant here names an agent; the
# count reads as one fact where every agent has the same skills, which
# is the rule `roster.agents_agree` holds for both surfaces.
AGENT_SKILLS_FACT = "{label}: {count} skills"
AGENT_ONE_SKILL_FACT = "{label}: 1 skill"
AGENT_NO_SKILLS_FACT = "{label}: no skills"
# A fourth fact, on a project whose update would write something
# (PLAN-0021 phase 1 prescription 4, Decided 6): a seeded file this
# project never edited that the installed package ships differently, or
# a skill it has, unedited, that the package ships a newer release of.
# Said right after the mount fact, on the listing row and on the
# project page alike, and on no project that is current.
UPDATE_AVAILABLE_FACT = "Update available"

# The one chip the listing draws: on a row whose record does not match
# the client's settings, and on no other. A healthy row carries none.
# The glyph leads the words so the row is found by shape as readily as
# by word, and never by colour; it sits in its own element, hidden from
# the accessibility tree, so the chip is read aloud as its words alone.
REPAIR_CHIP = "Needs repair"
REPAIR_GLYPH = "!"

ORPHAN_HEAD = "Entries whose project is gone"
ORPHAN_LEDE = (
    "A client still starts a server for a project that is no longer on this "
    "disk. The project's own record went with it, so each entry is named "
    "rather than looked up."
)

ADD_HEAD = "Add a project"
# The block's own id, which the control beside the heading leads to. A
# same-document fragment is not a departure: the close beacon in page.js
# fires on `pagehide`, and a fragment navigation raises none, so this is
# the one anchor the page may carry.
ADD_ID = "add"
# The press opens the mount plan and writes nothing; the press that
# writes is the plan's own, named here by the setup page's constant so
# the two surfaces cannot drift apart on its words.
ADD_LEDE = (
    "Choose the project's folder on this disk. The mount plan opens first, "
    f"and nothing is written until you press {setup_web.MOUNT_LABEL} on it."
)
ADD_FIELD_LABEL = "Project folder"
# The field a reader types a path into and the desktop shell's own picker
# writes into (D11). The placeholder is what it reads before either has
# happened. The button beside it is the shell's picker said on the page:
# it ships hidden, because a browser cannot open a folder, and the shell
# reveals it after the page loads inside its window. A press asks the
# shell through the document's own name; page.js writes the marker and
# puts the name back the moment it is read. The button says what the
# shell's own menu item says, so the two are one thing to a reader; the
# line under the field says the picker fills it, worded around the field
# because a plain browser never shows the button.
ADD_FIELD_PLACEHOLDER = "No folder chosen yet"
BROWSE_LABEL = "Choose a folder"
ADD_PICKER_NOTE = "Choosing a folder fills this field, and so does typing its path."
ADD_LABEL = "Review the mount plan"

# The one destructive press on the listing, framed alone at the page end:
# the same words on the frame and on the button, and one sentence saying
# what a reversal takes, what it never touches, and how many projects it
# reaches (PLAN-0014 phase 2). The count is the projects standing on
# this disk, which is what the removal view lists; the slot takes one of
# the three spellings beneath it.
REMOVAL_LABEL = "Unmount All Projects"
REMOVAL_WHAT = (
    "This does not touch your own files. It removes every MCP client "
    "registration and every skill RadialRig installed, from {scope}."
)
REMOVAL_ONE = "the 1 project listed"
REMOVAL_MANY = "the {count} projects listed"
REMOVAL_NONE_LISTED = "every listed project, and none is listed right now"

# The listing's update press (PLAN-0021 phase 1 prescription 4), drawn
# while at least one listed project is behind: every mounted project in
# the listing whose folder is there, through the same step `radialrig
# upgrade --all` runs. The sentence says what it brings and what it
# never touches, in the ruling's words (D-36 rule 2).
UPDATE_ALL_LABEL = "Update all projects"
UPDATE_ALL_WHAT = (
    "This brings every listed project's RadialRig files, and every skill "
    "it already has, up to the installed version. A file you edited is "
    "left where it is and listed with its changes. No skill is added, and "
    "none you removed is put back."
)
UPDATE_ALL_PLAN_HEAD = "What updating every listed project would change."
UPDATED_ALL_HEAD = "Every listed project is checked."

# The embedded walkthrough. These three sentences are the only ones this
# page says about it: everything else on that view is the setup page's own
# copy, rendered by the setup page's own builders.
WALK_HEAD = "The same press, from the command line"
WALK_LEDE = (
    "The button above runs these verbs, in this order. Running them yourself "
    "does the same thing."
)
WALK_STANDS = (
    "That walkthrough is finished. This page is still open, so the listing is "
    "one press away."
)

# The project view (PLAN-0014 phase 2). The heading is the folder's own
# name and the path stands once beneath it, so no constant heads the page.
# Three sections, each headed by its task rather than by the machinery:
# the state as the three facts above, then one row per skill, then one
# row per client. Not one of these names a skill or a client: a skill's
# name arrives from the catalog and a client's label from the client
# table, and every label with one in it is a template with a slot
# (constraint 5).
STATUS_HEAD = "Status"
SKILLS_HEAD = "Skills"
CONNECTIONS_HEAD = "Connections"

# A skill row's state, read off this project's own records for the one
# agent whose list the row stands in: the word the Status fact counts,
# so the section and the fact agree on what stands. Nothing else: a
# skill one agent has and another lacks reads Installed in one list and
# Not installed in the other (PLAN-0015 phase 2).
SKILL_INSTALLED = "Installed"
SKILL_NOT_INSTALLED = "Not installed"
# A third word (PLAN-0021 phase 1 prescription 4): the agent's record
# lists the skill, every file of it stands as recorded, and this package
# ships a newer release of it. The press beside it is the existing add
# for that agent, which refreshes an unedited copy (Verified 2). An
# edited copy still reads Installed: the update would write nothing
# there, and the update's report lists it as left.
SKILL_UPDATE_AVAILABLE = "Update available"
# A connection row's state. The row's heading is the client's label, so
# the state says only which way it stands; the mismatched record's row
# says the engine's own repair sentence, `roster.mismatch_clause`, the
# same one the Status fact and the listing row say (phase 3).
CONNECTED_STATE = "Connected"
NOT_CONNECTED_STATE = "Not connected"

# A section with nothing to list says so rather than disappearing, so a
# reader looking for the section finds it and reads why it is empty.
SKILLS_NONE = "No skill is listed for this project."
CONNECTIONS_NONE = "Nothing to connect or disconnect on this project as it stands."

# On a project that is not mounted, the Skills and Connections sections
# say this once above their rows (PLAN-0014 phase 3 prescription 4):
# every press there is an engine verb with no mount check, so a reader
# is told the order is theirs to choose rather than left to infer one.
NO_MOUNT_NEEDED = "Each of these stands on its own and does not need the mount."

# Every press names its target. The skill and client slots are filled at
# render time from the catalog and the client table.
UNMOUNT_LABEL = "Unmount this project"
SKILL_ADD_LABEL = "Add {name}"
SKILL_REMOVE_LABEL = "Remove {name}"
SKILL_UPDATE_LABEL = "Update {name}"
# The project page's update press (PLAN-0021 phase 1 prescription 4),
# under the Status facts while the project is behind: `radialrig
# upgrade <folder>`, rehearsed first like every press on this page.
UPDATE_LABEL = "Update this project"
# The one press above an agent's list, drawn only while that list has a
# skill missing: every packaged skill this project lacks, for that
# agent alone, through the engine's own whole-catalog add (PLAN-0015
# phase 2 prescription 2). The label is the harness map's.
SKILL_ADD_ALL_LABEL = "Add all for {label}"
CONNECT_LABEL = "Connect {label}"
UNCONNECT_LABEL = "Disconnect {label}"
ORPHAN_LABEL = "Remove {key}"
ORPHAN_WHAT = "Drop this entry from the client's own settings file."

# The mount press on a project that is not mounted (phase 3 prescription
# 4): a navigation to the embedded walkthrough on this folder, which is
# the mount plan, so nothing is written by the press itself. It is that
# page's one amber, mirroring the listing's press that opens the same
# plan; a mounted project's page keeps none.
MOUNT_LABEL = "Mount this project"

# The repair press on a mismatched record (phase 3 prescription 3): the
# registration's removal, then a fresh registration, as one press whose
# label names the client from the table. The sentence under the Status
# facts says what the two commands behind the disclosure are, in the
# order they run; the sentence for a rehearsal that finds nothing left
# to repair is the one the record and the settings agreeing leaves.
REPAIR_LABEL = "Repair the {label} connection"
REPAIR_NOTE = (
    "The repair runs two commands in this order: the registration's removal, "
    "then a fresh registration."
)
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

# The unmount, framed alone at the page end the way the listing frames
# its reversal: what it removes, and what it leaves.
UNMOUNT_WHAT = (
    "This removes the files the mount seeded and leaves your own files where "
    "they are. This project's skills and its client entries stay as they are "
    "unless removed above."
)

# The command beside a press sits behind a native disclosure on this
# page, with its Copy button inside; the plan step and the report keep
# the command in full, as constraint 1 requires.
SHOW_COMMAND = "Show command"

# A Copy button's accessible name, naming the command it copies: the
# visible text stays the one word, and page.js swaps only that.
COPY_NAME = "Copy: {command}"

# The removal view. One block per listed project, naming every verb that
# reverses it, and one press that runs them in that order. The two
# removals this page cannot make are named rather than offered: a page
# should not remove the process serving it, and it should not remove the
# program that opened it either.
REMOVAL_HEAD = "Reverse everything radialrig did on this machine"
REMOVAL_LEDE = (
    "One project at a time, in the order the verbs reverse: the registration, "
    "then each installed skill, then the mount. A press runs the commands "
    "listed under its project and answers in their own words."
)
REMOVAL_NONE = "No project is listed here, so there is nothing to reverse."
REVERSAL_LABEL = "Reverse this project"
NOTHING_TO_REVERSE = "Nothing radialrig wrote is still standing in this project."
REVERSAL_PLAN_HEAD = "What reversing this project would change."
REVERSED_HEAD = "That project is reversed."
REMOVAL_BACK_LABEL = "Back to the reversals"

ENGINE_HEAD = "The removals this page names and does not run"
ENGINE_LEDE = (
    "A page cannot remove the process serving it, and it cannot remove the "
    "program that opened it. These come out the way any other program does, "
    "in the order listed: RadialRig first, then the Python that was installed "
    "for it, then uv last, so every command still has a uv to run it."
)
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
# else on it too.
PATH_WHAT = (
    "One entry on your user Path, under the registry key HKCU\\Environment. No "
    "command here removes it: the app's own removal flow does, and only while "
    "your Path value still matches what the app recorded."
)

# The same slot on Linux, stated rather than left implied (PLAN-0009
# phase 3 prescription 5): the app's first run on Linux reads PATH and
# writes nothing, so this view says there is nothing to take back out
# instead of silently dropping the row.
LINUX_PATH_WHAT = (
    "Nothing on your PATH. The app's first run on Linux reads your PATH and "
    "reports what it found; it writes no shell startup file, so there is no "
    "entry here to take back out."
)

# The remains line for the same entry, sitting here beside the line it
# pairs with because the `MACHINES` rows below carry both.
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

# What stands when every reversal above has run. Each line says what is
# left and why it is left, which is the whole promise of this view: a
# reader who runs everything here should be told what remains rather than
# left to find it.
REMAINS_HEAD = "What is left when every reversal above has run"
REMAINS_LEFT = (
    "Every path a reversal listed as left. A file this project changed is "
    "never overwritten and never taken."
)
REMAINS_ROSTER = (
    "The projects listing at {path}. Deleting it costs the listing and nothing "
    "else: the next verb writes a fresh one."
)
# PLAN-0018 phase 2: the log sits beside the listing, and its earlier
# file beside it, so a reader who takes everything out is told of both.
REMAINS_LOG = (
    "The log at {path}, and the earlier file beside it, {older}. They record "
    "what ran on this computer; deleting them costs that record and nothing "
    "else."
)
REMAINS_ENGINE = (
    f"The engine, until {ENGINE_REMOVAL} runs. This page cannot run it, because "
    f"the engine is the process serving this page."
)
REMAINS_APP = "The desktop app, until it is removed from {place}."
REMAINS_PYTHON = (
    "The Python uv installed for the engine, where uv installed one, until "
    "{command} runs."
)
REMAINS_UV = (
    "uv itself, and the download cache it keeps, until {cache} runs and the two "
    "uv programs are deleted. uv runs every removal listed above it, so it "
    "comes out last."
)

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

# The command line's own wording for a path that is not a directory, so a
# posted folder that is gone reads the same on both surfaces. It is only
# ever rendered for a path that was named: a colon with nothing after it
# tells a reader nothing.
NO_DIRECTORY = "no such directory: {path}"

# Section 2's ruling: the page names a roster file this build cannot
# read, and the command line goes on reading it as empty with no remark.
# One sentence: the path, what the listing does about it, why the file is
# not overwritten (constraint 5), and what moving it aside buys.
UNREADABLE_NOTICE = (
    "The roster file at {path} is not one this build can read, so this listing "
    "reads it as empty; radialrig never overwrites a file it cannot prove it "
    "wrote, and moving that file aside lets the next verb write a fresh one."
)

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
    registration step here. That project's block says the repair
    sentence and the two filled commands instead (`_mismatch_note`),
    which is the reader's way through a record this page cannot confirm.
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
        return _missing(root), None
    sub = walkthrough(session, target)
    return setup_web.compute_state(sub), sub


def setup_state(session: ManageSession, root: str) -> setup_web.PageState:
    """What the walkthrough shows for this folder. Writes nothing."""
    return _walk_state(session, root)[0]


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
    return orphan.reason_text(d) or setup_web.WROTE_REASONS.get(d.code, "")


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
    if not mnt.is_mounted(root):
        if not dry_run:
            ro.record_seen(root)
        return _answer(act, report.NOOP, report.not_mounted_report(root),
                       dry_run=dry_run)
    try:
        plan = mnt.unmount_plan(root) if dry_run else mnt.unmount(root)
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        return _stopped(act, exc)
    except mnt.MountError as exc:
        return _failed(act, str(exc))
    if not dry_run:
        ro.record_seen(root)
    return _answer(
        act, report.OK if plan.complete else report.PARTIAL,
        cli._unmount_report(plan, dry_run=dry_run), plan.dispositions,
        dry_run=dry_run,
    )


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
    except sk.UnknownSkill as exc:
        return _failed(act, "; ".join(
            report.skill_unknown_report(exc.name, exc.available)))
    except sk.SkillConflict as exc:
        return _refused(act, report.skill_conflict_report(
            exc.conflicts, dry_run=dry_run), exc.conflicts)
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        return _stopped(act, exc)
    except sk.SkillError as exc:
        return _stopped(act, exc)
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
    except sk.UnknownSkill as exc:
        return _failed(act, "; ".join(
            report.skill_unknown_report(exc.name, exc.available)))
    except sk.SkillConflict as exc:
        return _refused(act, report.skill_conflict_report(
            exc.conflicts, dry_run=dry_run), exc.conflicts)
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        return _stopped(act, exc)
    except sk.SkillError as exc:
        return _stopped(act, exc)
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
    except cn.UnknownClient as exc:
        return _failed(act, "; ".join(
            report.connect_unknown_client_report(exc.name, exc.available)))
    except cn.ConnectRefused as exc:
        return _refused(act, report.connect_refused_report(
            exc.client, root, exc.conflicts, dry_run=dry_run), exc.conflicts)
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        return _stopped(act, exc)
    except cn.ConnectError as exc:
        return _failed(act, str(exc))
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
        plan = (cn.unconnect_plan if dry_run else cn.unconnect)(root, act.client)
    except cn.UnknownClient as exc:
        return _failed(act, "; ".join(
            report.connect_unknown_client_report(exc.name, exc.available)))
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        return _stopped(act, exc)
    except cn.ConnectError as exc:
        return _failed(act, str(exc))
    if not dry_run:
        ro.record_seen(root)
    if not plan.connected:
        return _answer(act, report.NOOP,
                       report.connect_not_connected_report(root, plan.client),
                       dry_run=dry_run)
    return _answer(
        act, report.OK if plan.complete else report.PARTIAL,
        report.unconnect_report(plan, dry_run=dry_run), plan.dispositions,
        dry_run=dry_run,
    )


def _orphan(act: Action, *, dry_run: bool) -> setup_web.PageState:
    """The entry whose project is gone. No root, so no side entry either."""
    try:
        plan = (orphan.remove_plan if dry_run else orphan.remove)(
            act.client, act.key)
    except cn.UnknownClient as exc:
        return _failed(act, "; ".join(
            report.connect_unknown_client_report(exc.name, exc.available)))
    except orphan.OrphanRefused as exc:
        return _refused(act, report.orphan_refused_report(
            str(exc), dry_run=dry_run))
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        return _stopped(act, exc)
    except cn.ConnectError as exc:
        return _failed(act, str(exc))
    if not plan.found:
        return _answer(act, report.NOOP,
                       report.orphan_not_found_report(plan.client, plan.key),
                       dry_run=dry_run)
    return _answer(
        act, report.OK if plan.complete else report.PARTIAL,
        report.orphan_report(plan, resolve=orphan.reason_text, dry_run=dry_run),
        plan.dispositions, dry_run=dry_run,
    )


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
    if not mnt.is_mounted(root):
        return _answer(act, report.NOOP, report.not_mounted_report(root),
                       dry_run=dry_run)
    try:
        plan = update.plan(root) if dry_run else update.run(root)
    except (mnt.WriteStopped, cn.WriteStopped) as exc:
        return _stopped(act, exc)
    except mnt.MountError as exc:
        return _failed(act, str(exc))
    return setup_web.PageState(
        UPDATE_PLAN if dry_run else UPDATED, cli._update_outcome(plan), False,
        dispositions=tuple(_update_rows(plan)),
        lines=tuple(report.update_report(plan, dry_run=dry_run)),
        result=(act, plan),
    )


def _update_all(act: Action, *, dry_run: bool) -> setup_web.PageState:
    """Every listed project's update: `radialrig upgrade --all`, the
    same step over the same listing, one block per project. The state
    carries the projects as the step answered them, so the body draws
    each project's rows as a table under its folder; the lines are the
    command line's whole report, which is what the log keeps."""
    found = update.plan_all() if dry_run else update.run_all()
    if not found:
        lines: tuple[str, ...] = (report.PROJECTS_NONE,)
    else:
        lines = tuple(report.update_all_report(found, dry_run=dry_run))
    return setup_web.PageState(
        UPDATE_ALL_PLAN if dry_run else UPDATED_ALL,
        cli._update_all_outcome(found) if found else report.NOOP, False,
        lines=lines, result=(act, found),
    )


# One runner per verb, rehearsal and confirmed press alike, told apart by
# `dry_run`. The roster side entry rides inside them, mirroring `cli.py`'s
# own seams: unmount, connect and unconnect keep it on their real runs and
# on their already-done exits, and the skill verbs and the update verbs
# keep none, because the command line keeps none.
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

    A step that refused or failed stops the sequence. On a run it wrote
    nothing, so the project stands where the reader can see it, and the
    steps after it would be acting past a refusal the reader has not read
    yet; on a rehearsal the sequence would stop in the same place, so
    naming the steps beyond it would be describing a run that never gets
    there. A partial answer does not stop it: on a mismatched record the
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


# The column sets this page draws. The listing's row is a project, said
# as its name and its three facts; a press's rows are the three-column
# shape the setup page draws, so the heads differ and everything else
# about that table does not. The project page draws no table since
# PLAN-0014 phase 2: its state is the three facts under Status.
ROSTER_COLUMNS = ("Project", "Status")
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


# --- the listing's rows (PLAN-0014 phase 1) ---------------------------
# A row is one `roster.ProjectState`, said as the folder's own name with
# its parent beneath, and the three facts of constraint 3's mapping. The
# command line's rows are not redrawn here: `radialrig projects` keeps
# its sentences and its order, and only this page sorts.

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


def _skills_facts(state: ro.ProjectState) -> tuple[str, ...]:
    """The skills fact: one count, or one line per agent.

    One where every agent has the same skills, as the row always said
    it; one per agent, in map order and labelled from the map, where
    the lists differ (PLAN-0015 phase 2 prescription 5). The rule is
    the roster's, so the command line's row and this fact split on the
    same projects.
    """
    if ro.agents_agree(state):
        return (_skills_count(len(state.skills)),)
    return tuple(
        _skills_count(len(names), sk.HARNESSES[name].label)
        for name, names in state.agents
    )


def _skills_count(count: int, label: str = "") -> str:
    if count == 0:
        return AGENT_NO_SKILLS_FACT.format(label=label) if label else NO_SKILLS_FACT
    if count == 1:
        return AGENT_ONE_SKILL_FACT.format(label=label) if label else ONE_SKILL_FACT
    if label:
        return AGENT_SKILLS_FACT.format(label=label, count=count)
    return SKILLS_FACT.format(count=count)


def _facts(state: ro.ProjectState) -> tuple[tuple[str, str], ...]:
    """The three facts, each with the class that marks which it is.

    In the order the command line prints them and constraint 3 lists
    them: mounted, connection, skills. The client's label is read from
    the client table, never from a constant. A mismatched record says
    the engine's own repair sentence, filled with the client the record
    claims, or naming the record file where it claims none this build
    knows (PLAN-0014 phase 3): the same sentence the command line's row
    carries, from the same template in `roster.py`. The skills fact is
    one entry where the agents agree and one per agent where they do
    not, every one of them under the same class.

    A project registered with no client names the clients installed on
    this machine, and drops the connection fact where none is: there is
    nothing to connect, and a client the reader does not have is never
    named to them.
    """
    mounted = MOUNTED_FACT if state.mounted else NOT_MOUNTED_FACT
    client = cn.CLIENTS.get(state.connected)
    if state.connected == ro.CONNECTED_MISMATCH:
        connection = ro.mismatch_clause(state)
    elif client is None:
        here = cn.installed()
        connection = (
            NOT_CONNECTED_FACT.format(label=cn.label_phrase(here)) if here else None)
    else:
        connection = CONNECTED_FACT.format(label=client.label)
    return (
        ("mounted", mounted),
        *((("update", UPDATE_AVAILABLE_FACT),) if state.behind else ()),
        *((("connection", connection),) if connection else ()),
        *(("skills", said) for said in _skills_facts(state)),
    )


def _needs_repair(state: ro.ProjectState) -> bool:
    return state.connected == ro.CONNECTED_MISMATCH


def _repair_chip() -> str:
    """The one outlined chip, on a row whose record the client contradicts.

    The outline is the setup page's own `touches` treatment and not the
    amber: the listing's one amber is the press that opens the mount
    plan. The glyph is hidden from the accessibility tree so the chip is
    read as its words, and it is a text glyph so it is found by shape
    without a colour.
    """
    return (
        '<span class="chip touches">'
        f'<span class="glyph" aria-hidden="true">{_esc(REPAIR_GLYPH)}</span> '
        f"{_esc(REPAIR_CHIP)}</span>"
    )


def _listing_order(state: ro.ProjectState) -> int:
    """Needs repair first, then not mounted, then the rest.

    The sort is stable, so each group keeps roster order within it. A
    project both mismatched and unmounted needs repair, which is the
    first group.
    """
    if _needs_repair(state):
        return 0
    if not state.mounted:
        return 1
    return 2


def _open_control(session: ManageSession, state: ro.ProjectState) -> str:
    """The control that opens a row, for the rows that stand.

    An entry whose folder is gone is dropped from the roster on this
    very read, so its row carries the command line's sentence and no
    control: there is no view this page could stand on it.
    """
    if not state.exists:
        return ""
    return _nav(session, PROJECT, OPEN_LABEL,
                extra=(("root", state.root),), cls="ghost row-open")


def _status_cell(state: ro.ProjectState) -> str:
    if not state.exists:
        return (f'<span class="fact gone">'
                f"{_esc(ro.ROSTER_REASONS[ro.ROSTER_GONE])}</span>")
    return "".join(
        f'<span class="fact {which}">{_esc(said)}</span>'
        for which, said in _facts(state)
    )


def _listing_row(session: ManageSession, state: ro.ProjectState) -> str:
    """One project: its name, its parent, its chip if any, its facts.

    The name is one element that never wraps, and the parent beneath it
    is another that may; both are text a reader can select, so the whole
    path is on the row and the name is never split by it.
    """
    name, parent = _name_parts(state.root)
    chip = _repair_chip() if _needs_repair(state) else ""
    above = f'<span class="parent">{_esc(parent)}</span>' if parent else ""
    return (
        f'<tr id="{_row_id(state.root)}">'
        f'<td class="project"><span class="title"><span class="name">'
        f"{_esc(name)}</span>{chip}</span>{above}</td>"
        f'<td class="status">{_status_cell(state)}</td>'
        f'<td class="act">{_open_control(session, state)}</td>'
        "</tr>"
    )


def _listing(session: ManageSession, states) -> str:
    """The table: every project the roster names, in the prescribed order.

    Entries a client carries whose project is gone are not projects and
    stand in their own block beneath the table, with the command that
    removes each. The head of the control column is named for readers
    who cannot see the buttons under it.
    """
    if not states:
        return ""
    ordered = sorted(states, key=_listing_order)
    body = "".join(_listing_row(session, state) for state in ordered)
    heads = "".join(f'<th scope="col">{_esc(name)}</th>' for name in ROSTER_COLUMNS)
    heads += f'<th scope="col"><span class="sr">{_esc(OPEN_LABEL)}</span></th>'
    return (
        '<div class="plan-wrap"><table class="plan projects">'
        f"<caption>{_esc(ROSTER_CAPTION)}</caption>"
        f"<thead><tr>{heads}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _heading_row(with_control: bool) -> str:
    """The heading, and beside it the one control that leads to the add block.

    A same-document fragment link, styled as the ghost button: it changes
    no view, so it is not one of the navigations that must be a form, and
    it fires no `pagehide`, so the close beacon stays quiet. With no
    listing to scroll past, the add block stands where the listing would
    and the control is not drawn.
    """
    head = f"<h1>{_esc(ROSTER_HEAD)}</h1>"
    if not with_control:
        return head
    return (
        '<div class="head-row">' + head
        + f'<a class="ghost" href="#{ADD_ID}">{_esc(ADD_HEAD)}</a></div>'
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


def _disclosed(*acts: Action) -> str:
    """The command beside a press, behind a native disclosure.

    On the project page a command is one click away rather than in the
    way (PLAN-0014 phase 2 prescription 5): the frame, Copy button and
    all, opens under "Show command" and the rows stay compact. A press
    with a hole in it names no command, so it draws no disclosure either.
    Several presses share one disclosure where one press runs them all —
    the repair's two verbs (phase 3) — and it holds their frames in the
    order they run.
    """
    frames = "".join(_command_of(act) for act in acts)
    if not frames:
        return ""
    return (
        '<details class="command">'
        f"<summary>{_esc(SHOW_COMMAND)}</summary>{frames}</details>"
    )


def _row(heading: str, *, what: str = "", state: str = "", press: str = "",
         command: str = "", mono: bool = True) -> str:
    """One row of a section: a name, the sentences under it, one press.

    The name is a heading element and each sentence after it is a block,
    so the accessibility text keeps a word boundary between them and the
    eye finds the name first (phase 2 prescription 3). A row with no
    press stands as its name and its sentence, which is what a loose
    client entry this page cannot act on is.
    """
    cls = "name" if mono else "label"
    said = f'<h3 class="{cls}">{_esc(heading)}</h3>'
    if what:
        said += f'<p class="what">{_esc(what)}</p>'
    if state:
        said += f'<p class="state">{_esc(state)}</p>'
    return f'<li class="row"><div class="main">{said}</div>{press}{command}</li>'


def _orphan_row(session: ManageSession, loose) -> str:
    """One loose entry: its key, the sentence, and the press where one stands."""
    if not loose.root:
        return _row(loose.key, what=ro.ROSTER_REASONS[ro.ROSTER_UNPINNED])
    act = Action(ORPHAN, client=loose.client, key=loose.key)
    return _row(
        loose.key, what=ORPHAN_WHAT,
        press=_act_form(session, act, ORPHAN_LABEL.format(key=loose.key)),
        command=_command_of(act),
    )


def _orphan_list(session: ManageSession, found) -> str:
    """Each loose entry beside the one command that removes it.

    An entry pinning no project root carries no command: radialrig cannot
    show it is an orphan, so its line says so in the command line's own
    sentence and offers nothing. Both kinds stand here, because the table
    above lists projects and a client's loose entry is not one. The
    command stays in full: this block is the listing's, where the
    disclosure of the project page is not the idiom.
    """
    rows = "".join(_orphan_row(session, loose) for loose in found)
    if not rows:
        return ""
    return (
        '<fieldset class="skills">'
        f"<legend>{_esc(ORPHAN_HEAD)}</legend>"
        f'<p class="hint">{_esc(ORPHAN_LEDE)}</p>'
        f'<ul class="rows">{rows}</ul>'
        "</fieldset>"
    )


def _add_project(session: ManageSession) -> str:
    """The folder row that opens the embedded walkthrough.

    One field, and the picker's button beside it. The field is the one
    the desktop shell's picker writes into (D11) and the one a typed path
    has always reached, so it stays writable: this page is served to a
    plain browser as readily as to that shell, and a field only a picker
    could fill would leave a reader there with no way to name a folder.

    The press is rendered enabled: page.js disables it while the field is
    empty and enables it on the field's `input` event, which the shell's
    picker dispatches after writing the path. Without the script the
    press stays enabled and a bare submit answers with the listing, as
    the server has always answered it.
    """
    field = (
        '<div class="field">'
        f'<label for="root">{_esc(ADD_FIELD_LABEL)}</label>'
        '<div class="picker">'
        '<input id="root" name="root" value="" autocomplete="off" '
        f'spellcheck="false" placeholder="{_attr(ADD_FIELD_PLACEHOLDER)}">'
        f'<button class="ghost" type="button" id="browse" hidden>{_esc(BROWSE_LABEL)}</button>'
        "</div>"
        f'<p class="hint">{_esc(ADD_PICKER_NOTE)}</p>'
        "</div>"
    )
    return (
        f'<fieldset class="skills" id="{ADD_ID}">'
        f"<legend>{_esc(ADD_HEAD)}</legend>"
        f'<p class="hint">{_esc(ADD_LEDE)}</p>'
        + _nav(session, SETUP, ADD_LABEL, inner=field, cls="primary",
               framed=True)
        + "</fieldset>"
    )


def _removal_scope(count: int) -> str:
    """How many projects the listing's reversal reaches, in words."""
    if count == 0:
        return REMOVAL_NONE_LISTED
    if count == 1:
        return REMOVAL_ONE
    return REMOVAL_MANY.format(count=count)


def _reversal_frame(session: ManageSession, states) -> str:
    """The one destructive press, alone at the end of the listing.

    Framed and last, with clear space above it, because it is the only
    control on this page that leads to something taken from every project
    at once. The sentence says what that is, what it never touches, and
    how many projects it reaches: the ones standing on this disk, which
    are the ones the removal view lists a reversal for.
    """
    count = sum(1 for state in states if state.exists)
    return (
        '<section class="reversal">'
        f"<h2>{_esc(REMOVAL_LABEL)}</h2>"
        f"<p>{_esc(REMOVAL_WHAT.format(scope=_removal_scope(count)))}</p>"
        + _nav(session, REMOVAL, REMOVAL_LABEL, framed=True)
        + "</section>"
    )


def _roster_body(session: ManageSession, state: setup_web.PageState) -> str:
    """The machine, as one table a reader can act on line by line.

    One listing rather than a listing and a second copy of it: the
    project, its three facts, and the control that opens it are one row,
    in the order that puts the row needing a reader first. The report's
    own tails follow the table in the command line's words. With no
    project listed, the add block stands in the listing's place under
    the empty sentence, so the first thing a reader can do is the first
    thing on the page.
    """
    states, found = state.result
    tails = "".join(f'<p class="note">{_esc(line)}</p>' for line in state.lines)
    table = _listing(session, states)
    add = _add_project(session)
    return (
        _heading_row(bool(table))
        + _notice(state)
        + table
        + _update_all_frame(session, states)
        + tails
        + ("" if table else add)
        + _orphan_list(session, found)
        + (add if table else "")
        + _reversal_frame(session, states)
    )


def _update_all_frame(session: ManageSession, states) -> str:
    """The listing's update press, under the table while at least one
    listed project is behind (PLAN-0021 phase 1 prescription 4).

    A section like the project page's, not the reversal's frame: it
    takes nothing from anyone. The press is a ghost press to the
    engine's rehearsal, and the command stays in full, as every
    command on the listing does.
    """
    if not any(state.behind for state in states):
        return ""
    act = Action(UPDATE_ALL)
    return (
        '<section class="update">'
        f"<h2>{_esc(UPDATE_ALL_LABEL)}</h2>"
        f'<p class="hint">{_esc(UPDATE_ALL_WHAT)}</p>'
        + _act_form(session, act, UPDATE_ALL_LABEL)
        + _command_of(act)
        + "</section>"
    )


# --- the project page (PLAN-0014 phase 2) -----------------------------
# The folder's name over its path, the way back at the top and the
# bottom, and three sections headed by their task. Every press stands on
# its own row, names its target, and keeps its command one click away.

def _section(head: str, rows, none: str, *, lede: str = "") -> str:
    """One task's section: its heading, its rows, or the sentence for none.

    A lede, where one is given, stands once above the rows and only where
    there are rows to stand above: it says something about the presses
    listed, and a section with none listed has its own sentence.
    """
    if rows:
        body = f'<ul class="rows">{"".join(rows)}</ul>'
        if lede:
            body = f'<p class="hint">{_esc(lede)}</p>' + body
    else:
        body = f'<p class="hint">{_esc(none)}</p>'
    return f"<section><h2>{_esc(head)}</h2>{body}</section>"


def _remedy(facts) -> str:
    """The two-verb remedy on a mismatched record, filled in and disclosed.

    The Status section's note (PLAN-0014 phase 3 prescription 2): the
    sentence saying what the two commands are and their order, then both
    command frames, client and path filled from the record, behind the
    disclosure of phase 2 with a Copy button on each. Nothing where the
    record names no client this build knows — the fact above names the
    record file, and there is no client to fill a command with — and
    nothing on a record that is not a mismatch.
    """
    steps = repair_steps(facts)
    if not steps:
        return ""
    return f'<p class="note">{_esc(REPAIR_NOTE)}</p>' + _disclosed(*steps)


def _mount_press(session: ManageSession, facts) -> str:
    """The mount press on a project that is not mounted, and on no other.

    A navigation to the embedded walkthrough on this folder — the mount
    plan, which writes nothing until its own press — drawn as the page's
    one amber (phase 3 prescription 4), the way the listing's press that
    opens the same plan is that page's.
    """
    if facts.mounted:
        return ""
    return _nav(session, SETUP, MOUNT_LABEL, extra=(("root", facts.root),),
                cls="primary", framed=True)


def _status_section(session: ManageSession, state: setup_web.PageState) -> str:
    """The three facts of constraint 3, as blocks, under their heading.

    The same mapping the listing row draws, so the two surfaces say a
    state in the same words and the same order. Under them, what the
    state calls for: the mount press where the project is not mounted,
    and the filled remedy where the record is a mismatch (phase 3).
    """
    facts = state.result
    said = "".join(
        f'<p class="fact {which}">{_esc(line)}</p>'
        for which, line in _facts(facts)
    )
    return (
        f"<section><h2>{_esc(STATUS_HEAD)}</h2>"
        f'<div class="facts">{said}</div>'
        + _mount_press(session, facts)
        + _update_press(session, facts)
        + _remedy(facts)
        + "</section>"
    )


def _update_press(session: ManageSession, facts) -> str:
    """The update press on a project that is behind, and on no other
    (PLAN-0021 phase 1 prescription 4): a ghost press to the engine's
    rehearsal, its command one click away, the amber kept for the
    confirm press under that rehearsal."""
    if not facts.behind:
        return ""
    act = Action(UPDATE, root=facts.root)
    return _act_form(session, act, UPDATE_LABEL) + _disclosed(act)


def _skill_rows(session: ManageSession, state: setup_web.PageState,
                harness: sk.Harness) -> list[str]:
    """One agent's list: one row per skill, its name, its purpose, its
    state, one press acting on that agent alone (PLAN-0015 phase 2).

    The rows are the catalog's, in catalog order, and then any name this
    agent's record lists that the catalog no longer carries — that skill
    is still removable, so it still gets its row. The state is the
    roster's reading for this agent, the same one the Status fact
    counts, so the list and the fact agree on what stands: Installed
    where the agent's record lists the skill and it stands, Not
    installed otherwise, and nothing else. A skill the engine would
    refuse to add here — a directory at the target this kit did not put
    there, a file where the directory goes — shows the engine's own
    reason as its state and no press, as the setup page's box does. The
    purpose is the one line the packaged catalog carries for `skill
    list`, never a constant.
    """
    root = state.result.root
    rows = []
    for name, said, summary in _agent_skills(state, harness):
        if said == SKILL_INSTALLED:
            act = Action(SKILL_REMOVE, root=root, skill=name, harness=harness.name)
            label = SKILL_REMOVE_LABEL
        elif said == SKILL_NOT_INSTALLED:
            act = Action(SKILL_ADD, root=root, skill=name, harness=harness.name)
            label = SKILL_ADD_LABEL
        elif said == SKILL_UPDATE_AVAILABLE:
            # The existing add for this agent, which refreshes an
            # unedited copy (PLAN-0021 Verified 2); only the label differs.
            act = Action(SKILL_ADD, root=root, skill=name, harness=harness.name)
            label = SKILL_UPDATE_LABEL
        else:
            rows.append(_row(name, what=summary, state=said))
            continue
        rows.append(_row(
            name, what=summary, state=said,
            press=_act_form(session, act, label.format(name=name)),
            command=_disclosed(act),
        ))
    return rows


def _agent_skills(state: setup_web.PageState,
                  harness: sk.Harness) -> list[tuple[str, str, str]]:
    """One agent's rows as data: the name, the state it reads, the purpose.

    Read once, so the list's rows and the press above them agree on
    which skills are missing. The state is one of the two words, or the
    engine's own reason for a target it would refuse.
    """
    listed = dict(state.result.agents).get(harness.name, ())
    states = [s for s in state.catalog if s.harness.name == harness.name]
    names = [s.name for s in states]
    names.extend(name for name in listed if name not in names)
    summaries = {
        s.name: s.packaged.summary for s in states if s.packaged is not None
    }
    blocked = {
        s.name: mnt.reason_text(s.list_disposition())
        for s in states if s.record_entry is None and (s.foreign or s.dir_is_file)
    }
    # A clean copy the package ships a newer release of (PLAN-0021 phase
    # 1), read off the same states, by the same test the update uses.
    behind = {s.name for s in states if sk.behind(s)}
    out = []
    for name in names:
        if name in blocked:
            said = blocked[name]
        elif name in listed and name in behind:
            said = SKILL_UPDATE_AVAILABLE
        elif name in listed:
            said = SKILL_INSTALLED
        else:
            said = SKILL_NOT_INSTALLED
        out.append((name, said, summaries.get(name, "")))
    return out


def _skill_list(session: ManageSession, state: setup_web.PageState,
                harness: sk.Harness) -> str:
    """One agent's list under its label, with the whole-catalog press
    above the rows while any row reads Not installed and none is blocked.

    A captioned group rather than a heading, so a skill's own name stays
    the heading element every row on this page leads with. The press
    stands only where it has something to add: a list with nothing
    missing shows none (prescription 2). Nor does it stand while a row
    shows the engine's reason for refusing that skill: the whole-catalog
    add composes every skill and refuses whole, so beside such a row the
    press could only ever refuse, and a dead control never shows; the
    rows still add the others one by one. The press runs the engine's
    own `skill add --all` narrowed to this agent, and its command says
    so.
    """
    rows = _skill_rows(session, state, harness)
    press = ""
    states = [said for _, said, _ in _agent_skills(state, harness)]
    # A row reading Update available does not withhold the press: the
    # whole-catalog add refreshes that copy too (PLAN-0021 Verified 1).
    plain = {SKILL_INSTALLED, SKILL_NOT_INSTALLED, SKILL_UPDATE_AVAILABLE}
    if SKILL_NOT_INSTALLED in states and set(states) <= plain:
        act = Action(SKILL_ADD_ALL, root=state.result.root, harness=harness.name)
        press = (
            _act_form(session, act, SKILL_ADD_ALL_LABEL.format(label=harness.label))
            + _disclosed(act)
        )
    return (
        '<fieldset class="skills agent">'
        f"<legend>{_esc(harness.label)}</legend>"
        + press
        + f'<ul class="rows">{"".join(rows)}</ul>'
        + "</fieldset>"
    )


def _skills_section(session: ManageSession, state: setup_web.PageState,
                    *, lede: str = "") -> str:
    """The Skills section: one list per agent, in map order, each headed
    by the agent's label (PLAN-0015 phase 2 prescription 1).

    The lede, where one is given, stands once above the lists; the
    sentence for nothing to list stands only where no agent has a row,
    which is a package that ships no skill on a project whose records
    name none.
    """
    lists = "".join(
        _skill_list(session, state, harness) for harness in sk.HARNESSES.values())
    if not state.catalog and not any(names for _, names in state.result.agents):
        body = f'<p class="hint">{_esc(SKILLS_NONE)}</p>'
    else:
        body = (f'<p class="hint">{_esc(lede)}</p>' if lede else "") + lists
    return f"<section><h2>{_esc(SKILLS_HEAD)}</h2>{body}</section>"


def _connection_rows(session: ManageSession,
                     state: setup_web.PageState) -> list[str]:
    """One row per client: its label, its state, one press.

    Gated exactly as the offers are: a registration is offered only
    where the client is detected on this machine and this project is not
    registered with it (`state.client`), its removal only where the
    record and the client's own copies agree, and the repair only where
    the client the mismatched record names is detected here and the read
    server's command can be found (constraint 7, narrowed by phase 3).
    The repair row's state is the repair sentence and its press runs the
    two verbs whose filled commands stand under the Status facts, so the
    row carries no second copy of them.
    """
    facts = state.result
    root = facts.root
    rows = []
    if state.client is not None and _needs_repair(facts):
        rows.append(_row(
            state.client.label, state=ro.mismatch_clause(facts), mono=False,
            press=_sequence_form(
                session, REPAIR, root,
                REPAIR_LABEL.format(label=state.client.label)),
        ))
    elif state.client is not None:
        act = Action(CONNECT, root=root, client=state.client.name)
        rows.append(_row(
            state.client.label, state=NOT_CONNECTED_STATE, mono=False,
            press=_act_form(
                session, act, CONNECT_LABEL.format(label=state.client.label)),
            command=_disclosed(act),
        ))
    registered = cn.CLIENTS.get(facts.connected)
    if registered is not None:
        act = Action(UNCONNECT, root=root, client=registered.name)
        rows.append(_row(
            registered.label, state=CONNECTED_STATE, mono=False,
            press=_act_form(
                session, act, UNCONNECT_LABEL.format(label=registered.label)),
            command=_disclosed(act),
        ))
    return rows


def _unmount_frame(session: ManageSession, facts) -> str:
    """The unmount, framed alone at the page end, on a mounted project.

    The listing's own shape for its one destructive press: the same
    words on the frame and on the button, one sentence on what it
    removes and what it leaves, and a ghost press — the amber on this
    project's pages stays on the confirm press of a plan step.
    """
    if not facts.mounted:
        return ""
    act = Action(UNMOUNT, root=facts.root)
    return (
        '<section class="reversal">'
        f"<h2>{_esc(UNMOUNT_LABEL)}</h2>"
        f"<p>{_esc(UNMOUNT_WHAT)}</p>"
        + _act_form(session, act, UNMOUNT_LABEL)
        + _disclosed(act)
        + "</section>"
    )


def _projects_control(session: ManageSession, root: str) -> str:
    """The way back to the listing, landing on this project's own row."""
    return _nav(session, ROSTER, ROSTER_HEAD, fragment=_row_id(root))


def _project_body(session: ManageSession, state: setup_web.PageState) -> str:
    """One project: its name, its path once, its state, and its presses.

    The Projects control stands above the heading, in a bar the
    stylesheet keeps at the top while the page scrolls, and again after
    the last section, so the way back is never below a stack of
    controls. Both are navigations to the listing, landing on the row
    this page was opened from.
    """
    facts = state.result
    name, _ = _name_parts(facts.root)
    back = _projects_control(session, facts.root)
    # On a project that is not mounted, each section of presses says once
    # that its presses stand without the mount (phase 3 prescription 4).
    lede = "" if facts.mounted else NO_MOUNT_NEEDED
    return (
        f'<div class="topbar">{back}</div>'
        f"<h1>{_esc(name)}</h1>"
        '<div class="root"><span class="label">Project</span>'
        f"<code>{_esc(facts.root)}</code></div>"
        + _status_section(session, state)
        + _skills_section(session, state, lede=lede)
        + _section(CONNECTIONS_HEAD, _connection_rows(session, state),
                   CONNECTIONS_NONE, lede=lede)
        + _unmount_frame(session, facts)
        + f'<div class="bottombar">{back}</div>'
    )


def _mount_command(sub: setup_web.Session) -> str:
    """`radialrig mount`, carrying whatever the page's own fields changed.

    Scope and owner are fields a reader can edit on this view, so the
    command names them whenever they are not the ones the bare verb would
    have used. Every slot goes through `_quoted`, the way the table's own
    slots do: the scope starts as the folder's own name, so a folder with
    a space in it would otherwise put one into this line twice. A slot no
    quoting rule makes safe leaves no line at all, the way `Action.command`
    leaves none (setup-web-04).
    """
    try:
        line = f"radialrig mount {_quoted(str(sub.root))}"
        if sub.scope != mnt.default_scope(sub.root):
            line += f" --scope {_quoted(sub.scope)}"
        if sub.owner != mnt.DEFAULT_OWNER:
            line += f" --owner {_quoted(sub.owner)}"
    except setup_web.Unquotable:
        return ""
    return line


def _walk_sequence(sub: setup_web.Session,
                   state: setup_web.PageState) -> tuple[str, ...]:
    """Every verb the one press below would run, in the order it runs them.

    A state with no press names none: a finished walkthrough and a refusal
    both leave nothing for a reader to run instead — the finished page
    prints the skill lines it ran itself. The skills and the registration
    take their spelling from `COMMANDS`, so one press and one button say
    the same verb the same way; a ticked box names its agent, so its line
    carries the narrowing (PLAN-0015 phase 3).
    """
    if state.terminal or state.kind not in (setup_web.REVIEW, setup_web.ALREADY):
        return ()
    root = str(sub.root)
    out = [_mount_command(sub)] if state.kind == setup_web.REVIEW else []
    out.extend(
        Action(SKILL_ADD, root=root, skill=name, harness=harness).command
        for harness, name in sub.skills)
    if state.client is not None and sub.connect:
        out.append(Action(CONNECT, root=root, client=state.client.name).command)
    # A verb with no safe line is left out rather than drawn as an empty
    # frame (setup-web-04).
    return tuple(line for line in out if line)


def _walk_commands(sub: setup_web.Session, state: setup_web.PageState) -> str:
    lines = _walk_sequence(sub, state)
    if not lines:
        return ""
    items = "".join(f"<li>{_command(line)}</li>" for line in lines)
    return (
        '<fieldset class="skills">'
        f"<legend>{_esc(WALK_HEAD)}</legend>"
        f'<p class="hint">{_esc(WALK_LEDE)}</p>'
        f'<ul class="steps">{items}</ul>'
        "</fieldset>"
    )


def _setup_body(session: ManageSession, sub: setup_web.Session,
                state: setup_web.PageState) -> str:
    """One walkthrough state, in the setup page's own words.

    The body is that page's builder over that page's session, so the
    walkthrough embedded here is the walkthrough `radialrig setup` runs
    rather than a second telling of it. Three things are this page's: the
    verbs the press names, the sentence about the server — which has not
    stopped here, whatever a state that ends a walkthrough says — and the
    way back to the listing.
    """
    body = setup_web.BODIES[state.kind](sub, state)
    return (
        body.replace(setup_web.STOPPED, WALK_STANDS)
        + _walk_commands(sub, state)
        + _nav(session, ROSTER, ROSTER_BACK_LABEL)
    )


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


# --- the full-removal flow, drawn ---------------------------------------

# What each step of a reversal does, in the words the project page's
# press for the same verb carries. One label per verb rather than one per
# view, so the two screens name the same press the same way; the slot is
# filled from the step itself at render time.
STEP_WHAT = {
    UNCONNECT: UNCONNECT_LABEL,
    SKILL_REMOVE: SKILL_REMOVE_LABEL,
    UNMOUNT: UNMOUNT_LABEL,
}


def _step_what(act: Action) -> str:
    """The step's label, its skill or client slot filled from the step."""
    client = cn.CLIENTS.get(act.client)
    label = client.label if client is not None else act.client
    return STEP_WHAT[act.name].format(name=act.skill, label=label)


def _step_list(steps) -> str:
    """Every verb this press runs, beside the command that runs it alone."""
    items = "".join(
        "<li>"
        f'<span class="what">{_esc(_step_what(act))}</span>'
        + _command_of(act)
        + "</li>"
        for act in steps
    )
    return f'<ul class="steps">{items}</ul>'


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


def _mismatch_note(facts) -> str:
    """This view's account of a record it cannot confirm.

    A mismatch has no registration step in its reversal, so its record is
    named here rather than passed over — a project left with a record
    nobody mentioned would be the one thing this view exists to prevent.
    The sentence is the engine's own repair sentence, the one the listing
    row and the project page say (phase 3), and under it stand the two
    filled commands of the remedy where the record names a client this
    build knows, in full: this view keeps every command in full, and the
    disclosure of the project page is not its idiom.
    """
    if facts.connected != report.PROJECTS_MISMATCH:
        return ""
    return (
        f'<p class="note">{_esc(ro.mismatch_clause(facts))}</p>'
        + "".join(_command_of(act) for act in repair_steps(facts))
    )


def _reversal_block(session: ManageSession, facts, steps) -> str:
    """One project, its reversal sequence, and the press that rehearses it."""
    if steps:
        body = _step_list(steps) + _sequence_form(
            session, REVERSAL, facts.root, REVERSAL_LABEL)
    else:
        body = f'<p class="hint">{_esc(NOTHING_TO_REVERSE)}</p>'
    return (
        '<fieldset class="skills">'
        f"<legend>{_esc(facts.root)}</legend>"
        + _mismatch_note(facts)
        + body
        + "</fieldset>"
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


def _engine_block() -> str:
    """Those removals, each beside the command that makes it where one exists."""
    items = "".join(
        f'<li><span class="what">{_esc(what)}</span>'
        + (_command(line) if line else "")
        + "</li>"
        for what, line in machine_removals()
    )
    return (
        '<fieldset class="skills">'
        f"<legend>{_esc(ENGINE_HEAD)}</legend>"
        f'<p class="hint">{_esc(ENGINE_LEDE)}</p>'
        f'<ul class="steps">{items}</ul>'
        "</fieldset>"
    )


def _remains() -> str:
    """What stands after every reversal, and why each one stands.

    The roster file is named only where this machine has a path for one:
    a build with nowhere to keep the listing leaves nothing there to
    mention.
    """
    lines = [REMAINS_LEFT]
    path = ro.roster_path()
    if path is not None:
        lines.append(REMAINS_ROSTER.format(path=path))
        log = al.log_path()
        if log is not None:
            lines.append(REMAINS_LOG.format(
                path=log, older=log.with_name(al.OLDER_NAME)))
    lines.append(REMAINS_ENGINE)
    row = machine()
    if row and row.app_place:
        lines.append(REMAINS_APP.format(place=row.app_place))
    lines.append(REMAINS_PYTHON.format(
        command=PYTHON_REMOVAL.format(version=_python_pin())))
    if row:
        lines.append(REMAINS_UV.format(cache=UV_CACHE_REMOVAL))
        if row.path_remains:
            lines.append(row.path_remains)
    items = "".join(
        f'<li><span class="what">{_esc(line)}</span></li>' for line in lines)
    return (
        '<fieldset class="skills">'
        f"<legend>{_esc(REMAINS_HEAD)}</legend>"
        f'<ul class="steps">{items}</ul>'
        "</fieldset>"
    )


def _removal_body(session: ManageSession, state: setup_web.PageState) -> str:
    plans, found = state.result
    blocks = "".join(
        _reversal_block(session, facts, steps) for facts, steps in plans)
    if not blocks:
        blocks = f'<p class="hint">{_esc(REMOVAL_NONE)}</p>'
    return (
        f"<h1>{_esc(REMOVAL_HEAD)}</h1>"
        f'<p class="lede">{_esc(REMOVAL_LEDE)}</p>'
        + _notice(state)
        + blocks
        + _orphan_list(session, found)
        + _engine_block()
        + _remains()
        + _nav(session, ROSTER, ROSTER_BACK_LABEL)
    )


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


def _rehearsal_body(session: ManageSession, state: setup_web.PageState) -> str:
    """What the sequence would change, above the press that would change it.

    Every step is the plan state its own single press answers with, drawn
    by the same builder, so the rows and the commands a reader reads here
    are the rows and the commands they would read there.

    The press is offered only where every step rehearsed clean. A step
    that refused would refuse again, and a button over it would be a way
    past a refusal — which is the one control no state on this page
    carries, because the command line carries none.
    """
    root, steps = state.result
    if not steps:
        body = f'<p class="hint">{_esc(NOTHING_TO_REVERSE)}</p>'
    else:
        body = "".join(_step_report(step) for step in steps)
        if all(step.kind == PLAN for step in steps):
            body += _sequence_form(session, REVERSAL, root, CONFIRM_LABEL, confirm=True)
    return (
        _sequence_head(REVERSAL_PLAN_HEAD, root)
        + body
        + _nav(session, REMOVAL, REMOVAL_BACK_LABEL)
        + _nav(session, ROSTER, ROSTER_BACK_LABEL)
    )


def _reversal_body(session: ManageSession, state: setup_web.PageState) -> str:
    """What the sequence did, verb by verb, then what is still standing."""
    root, steps = state.result
    return (
        _sequence_head(REVERSED_HEAD, root)
        + "".join(_step_report(step) for step in steps)
        + _remains()
        + _nav(session, REMOVAL, REMOVAL_BACK_LABEL)
        + _nav(session, ROSTER, ROSTER_BACK_LABEL)
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
    return (
        _sequence_head(REPAIRED_HEAD, root)
        + "".join(_step_report(step) for step in steps)
        + _project_backs(session, root)
    )


def _said_lines(lines, rows=()) -> str:
    """Report lines as sentences, the rows a table shows lifted out."""
    return "".join(
        f'<p class="said">{_esc(line)}</p>' for line in _sentences(lines, rows))


def _update_blocks(plan, *, dry_run: bool, level: str) -> str:
    """One update's report in its blocks, each drawn under its own head
    (PLAN-0021 phase 1 review, item 5): the seed's rows as a table, with
    the sentences of its block; the sentences between the halves, where
    the report has them; each agent's own heading with that agent's
    rows as a table under it and its sentences, so an agent whose record
    could not be read shows the kit's sentence there; then the closing
    line and the diffs. Every line the command line prints is on the
    page, as a table row or as a sentence, which the parity test holds.
    `level` is the heading element an agent gets, under the page's own
    head on one project's page and under a folder's on the listing's.
    """
    parts = report.update_parts(plan, dry_run=dry_run)
    caption = PLAN_CAPTION if dry_run else DONE_CAPTION
    out = (
        _table(parts.seed.rows, caption, _reason)
        + _said_lines(parts.seed.lines[1:], parts.seed.rows)
        + _said_lines(parts.said)
    )
    for agent in parts.agents:
        out += (
            f"<{level}>{_esc(agent.head)}</{level}>"
            + _table(agent.rows, caption, _reason)
            + _said_lines(agent.lines[1:], agent.rows)
        )
    return out + _said_lines(parts.tail)


def _update_body(session: ManageSession, state: setup_web.PageState) -> str:
    """One project's update, rehearsed or run (PLAN-0021 phase 1): the
    report's head as the page's, its blocks under their own heads, the
    engine-half sentence, the command in full, and on the rehearsal the
    one press that writes."""
    act, plan = state.result
    dry_run = state.kind == UPDATE_PLAN
    head = report.update_parts(plan, dry_run=dry_run).seed.head
    body = (
        f"<h1>{_esc(head)}</h1>"
        + _update_blocks(plan, dry_run=dry_run, level="h2")
        + f'<p class="said">{_esc(report.ENGINE_HALF)}</p>'
        + (f'<p class="said">{_esc(report.DRY_RUN_TAIL)}</p>' if dry_run else "")
        + _command_of(act)
    )
    if dry_run:
        body += _act_form(session, act, CONFIRM_LABEL, confirm=True)
    return body + _back(session, act)


def _project_update_block(item, *, dry_run: bool) -> str:
    """One listed project under the update-all press: its folder as the
    heading, its report's head as a sentence, then the report's blocks
    under their own heads, or the one sentence saying why it was
    skipped."""
    if item.plan is None:
        return f'<p class="said">{_esc(report.update_skipped_line(item))}</p>'
    head = report.update_parts(item.plan, dry_run=dry_run).seed.head
    return (
        f"<h2>{_esc(item.root)}</h2>"
        f'<p class="said">{_esc(head)}</p>'
        + _update_blocks(item.plan, dry_run=dry_run, level="h3")
    )


def _update_all_body(session: ManageSession, state: setup_web.PageState) -> str:
    """Every listed project's update, rehearsed or run (PLAN-0021 phase
    1): the command line's head line, each project's block under its
    folder, the engine-half sentence once, the command in full, and on
    the rehearsal the one press that writes."""
    act, found = state.result
    dry_run = state.kind == UPDATE_ALL_PLAN
    lines = list(state.lines)
    if not found:
        body = f'<p class="hint">{_esc(lines[0])}</p>'
    else:
        body = (
            f'<p class="lede">{_esc(lines[0])}</p>'
            + "".join(_project_update_block(item, dry_run=dry_run) for item in found)
            + f'<p class="said">{_esc(report.ENGINE_HALF)}</p>'
            + (f'<p class="said">{_esc(report.DRY_RUN_TAIL)}</p>' if dry_run else "")
        )
    head = UPDATE_ALL_PLAN_HEAD if dry_run else UPDATED_ALL_HEAD
    body = f"<h1>{_esc(head)}</h1>" + body + _command_of(act)
    if dry_run and found:
        body += _act_form(session, act, CONFIRM_LABEL, confirm=True)
    return body + _nav(session, ROSTER, ROSTER_BACK_LABEL)


BODIES = {
    ROSTER: _roster_body,
    PROJECT: _project_body,
    REMOVAL: _removal_body,
    REHEARSAL: _rehearsal_body,
    REVERSED: _reversal_body,
    REPAIR_PLAN: _repair_plan_body,
    REPAIRED: _repaired_body,
    UPDATE_PLAN: _update_body,
    UPDATED: _update_body,
    UPDATE_ALL_PLAN: _update_all_body,
    UPDATED_ALL: _update_all_body,
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

    Three overrides and the helpers they lean on: the state a GET
    renders, the state a POST renders, and the document either arrives
    in. The tokens, the launch redemption, the host and origin guards,
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
            return perform_walkthrough(self._sub, fields)

    def _survey(self) -> setup_web.PageState:
        query = parse_qs(urlparse(self.path).query)
        return self._view((query.get("view") or [ROSTER])[0], query)

    def _apply(self, fields) -> setup_web.PageState:
        """A press, or a navigation that arrived as one.

        The confirm press is the only thing on this page that writes, so
        it is the only thing that queues: two tabs on a threading server
        can interleave, and the engine is written for one verb at a time.
        A plan press and a view read nothing that a queue would protect.
        """
        self._sub = None
        name = (fields.get("action") or [""])[0]
        if name in WALK_ACTIONS:
            return self._settled(self._press(fields))
        if name in SEQUENCES:
            # The same two steps every press below takes. These are the
            # most consequential presses on the page — several verbs over
            # a whole project — so they are the last that should reach the
            # engine off a single post.
            plan, perform = SEQUENCES[name]
            root = (fields.get("root") or [""])[0]
            if (fields.get("confirm") or [""])[0]:
                with self.session.actions:
                    return self._settled(perform(root))
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
            if (fields.get("confirm") or [""])[0]:
                with self.session.actions:
                    return self._settled(RUNNERS[name](act, dry_run=False))
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
