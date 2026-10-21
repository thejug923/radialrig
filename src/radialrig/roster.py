r"""The projects roster: the one machine-level file this product keeps.

PLAN-0008 Phase 3, and PLAN-0009 Phase 2 for the Linux row. Every other
file radialrig writes lives inside a project or inside a client's own
settings. A management surface cannot manage what it cannot list, so
this module owns one per-user file — `%APPDATA%\radialrig\roster.json`
on Windows, `$XDG_DATA_HOME/radialrig/roster.json` on Linux — holding
one entry per project root a verb has touched on this machine.

The roster is a pointer cache and never authoritative (D-07). Nothing
here trusts what it remembers: `listing` re-reads every entry against the
project's own records and, where a record claims a connection, against
the client's own settings file, so an entry removed inside the client
reads as a mismatch and never as connected. Losing the file costs the
listing and nothing else — the next verb writes it back.

Two rules shape every write. The first is constraint 5: a roster this
build cannot read is never overwritten, not by a side entry and not by
the drop-gone rewrite, because radialrig cannot prove it wrote those
bytes. The file is recreated only when it is absent, which is the one
loss case the plan authorizes. The second is best effort: `record_seen`
never raises and never reports, so a roster failure cannot change a
verb's exit code or its report. `record_seen` is public for that reason —
the CLI verbs call it today and Phase 4's manage surface calls the same
function.

The dependency runs one way: this module imports `mount`, `connect` and
`skills`, and none of the three imports it. `report` does not import it
either — a row this module builds travels to `report` as a
`mount.Disposition`, and the resolver travels with it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import skills as sk
from radialrig.paths import ROOT_ENV

ROSTER_SCHEMA = 1


# --- where the file lives (the OS-to-path table) ----------------------

@dataclass(frozen=True)
class Source:
    """One environment variable a store row reads, and the literal
    directory under it the row's default names."""

    var: str                       # the environment variable the path starts from
    prefix: tuple[str, ...] = ()   # literal parts under it, before `parts`


@dataclass(frozen=True)
class Store:
    """Where one operating system keeps the per-user roster file."""

    platform: str                  # sys.platform prefix this row serves
    sources: tuple[Source, ...]    # tried in order; the first set non-empty wins
    parts: tuple[str, ...]         # path parts under the source


# A second operating system costs a row here and no branch anywhere
# else, the way CLIENTS and HARNESSES carry their rows (PLAN-0009
# constraint 3). The Linux row is the XDG rule spelled as data:
# `$XDG_DATA_HOME` when set, else the literal default under `$HOME`.
STORES: tuple[Store, ...] = (
    Store(
        platform="win32",
        sources=(Source(var="APPDATA"),),
        parts=("radialrig", "roster.json"),
    ),
    Store(
        platform="linux",
        sources=(
            Source(var="XDG_DATA_HOME"),
            Source(var="HOME", prefix=(".local", "share")),
        ),
        parts=("radialrig", "roster.json"),
    ),
)


def roster_path(*, env=None) -> Path | None:
    r"""%APPDATA%\radialrig\roster.json on Windows,
    $XDG_DATA_HOME/radialrig/roster.json on Linux with the
    $HOME/.local/share fallback, else None.

    None when no table row serves this platform, and when none of the
    row's variables is set non-empty. It means: reads answer empty,
    writes write nothing. A row's sources are tried in order and the
    first set non-empty wins. `env=None` reads `os.environ`, the
    injection seam `connect.detect` already has — which is also what
    keeps a suite that removes the profile variables from ever reaching
    a real roster.
    """
    environ = os.environ if env is None else env
    for store in STORES:
        if not sys.platform.startswith(store.platform):
            continue
        for source in store.sources:
            base = (environ.get(source.var) or "").strip()
            if not base:
                continue
            return Path(base).joinpath(*source.prefix, *store.parts)
    return None


# --- the tolerant read ------------------------------------------------

@dataclass(frozen=True)
class Roster:
    """The roster as this build reads it, and whether it may write it."""

    body: dict                 # the raw top-level: unknown fields AND
                               # unreadable entries kept verbatim
    entries: tuple[dict, ...]  # the readable entries only
    writable: bool             # False when a file exists on disk and is
                               # not this build's readable shape


def _empty_body() -> dict:
    return {"schema": ROSTER_SCHEMA, "projects": []}


def _empty(*, writable: bool) -> Roster:
    return Roster(body=_empty_body(), entries=(), writable=writable)


def _readable_entry(item) -> bool:
    """One roster entry this build can act on: a dict naming a root."""
    return isinstance(item, dict) and isinstance(item.get("root"), str) and item["root"]


def load(*, env=None) -> Roster:
    """The roster, or an empty one. Never raises.

    Empty and unwritable when a file exists and is not this build's
    shape: unreadable, not JSON, not a JSON object, carrying no integer
    `schema`, carrying a schema outside the range this build writes, or
    carrying a `projects` member that is not a list. Empty and writable
    when the path is None or the file is absent.

    A body carrying no `projects` member at all stays writable: adding a
    member destroys nothing, while replacing a member of another shape
    would destroy whatever that shape held.

    Unknown fields survive, top level and per entry, because a reader
    ignores fields it does not know and a rewriter carries them forward.
    An entry that is not a dict, or names no root, is excluded from
    `entries` and kept verbatim in `body["projects"]`, in place: file
    order always means the raw list's order.
    """
    path = roster_path(env=env)
    if path is None:
        return _empty(writable=True)
    try:
        if not path.is_file():
            return _empty(writable=True)
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return _empty(writable=False)
    try:
        body = json.loads(raw)
    except ValueError:
        return _empty(writable=False)
    if not isinstance(body, dict):
        return _empty(writable=False)
    schema = body.get("schema")
    # A bool is an int in Python and a schema number is not one here, so
    # the two tests run together rather than one standing for the other.
    if isinstance(schema, bool) or not isinstance(schema, int):
        return _empty(writable=False)
    if not 1 <= schema <= ROSTER_SCHEMA:
        return _empty(writable=False)

    projects = body.get("projects")
    if "projects" in body and not isinstance(projects, list):
        return _empty(writable=False)
    raw_entries = projects if isinstance(projects, list) else []
    # A shallow copy per readable entry: the caller reads a `seen` string
    # whatever the file carried, and the file's own entry keeps exactly
    # the fields it arrived with.
    entries = tuple(
        {**item, "seen": item["seen"] if isinstance(item.get("seen"), str) else ""}
        for item in raw_entries if _readable_entry(item)
    )
    return Roster(body=body, entries=entries, writable=True)


# --- the atomic write -------------------------------------------------

def _write(body: dict, *, env=None) -> None:
    """Replace the roster with `body`, whole or not at all. Never raises.

    The temporary file sits beside the target so `os.replace` stays on
    one volume, which is what makes the swap atomic, and its name carries
    the process id and the writing thread's, so no two writes in flight
    can ever name one file. The thread half is not decoration: the manage
    page serves its listing views unlocked by design, so a view's
    drop-gone rewrite and a press's side entry run at once inside one
    process, and one shared temporary name would leave them writing over
    each other's bytes and replacing the roster with the mixture — a file
    this build then cannot read, and constraint 5 forbids repairing. Any
    failure at any step leaves the old roster whole and the temporary
    file removed.

    The writable gate lives here rather than in the callers: every write
    path re-reads what is on disk and declines when the file is one this
    build cannot read, so no caller can walk past constraint 5.
    """
    path = roster_path(env=env)
    if path is None:
        return
    if not load(env=env).writable:
        return
    tmp = path.with_suffix(
        f".json.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# --- the side entry ---------------------------------------------------

def record_seen(root: Path, *, today: dt.date | None = None,
                dry_run: bool = False, env=None) -> None:
    """Upsert one entry for `root`, dated today. Best effort; never raises.

    Writes nothing on a dry run, when the path is None, and when the
    roster on disk is one this build cannot read. The upsert matches an
    existing readable entry by `os.path.normcase` on the resolved root
    and rewrites that entry's `seen` field alone, keeping every other
    field it carried; an unreadable raw entry travels forward verbatim,
    in place. A new entry carries exactly `root` and `seen`, and is
    appended last.

    The dry-run guard lives here so a caller stays one bare line. Both
    surfaces call this: the CLI verbs, and Phase 4's manage surface.
    """
    if dry_run:
        return
    # The verb's exit code and report are the promise here, so nothing
    # this function meets may escape it — not even a failure the layers
    # below were not written to expect.
    try:
        if roster_path(env=env) is None:
            return
        target = str(Path(root).resolve())
        roster = load(env=env)
        if not roster.writable:
            return
        projects = roster.body.get("projects")
        raw_entries = projects if isinstance(projects, list) else []
        day = (today or dt.date.today()).isoformat()
        marker = os.path.normcase(target)

        out: list = []
        found = False
        for item in raw_entries:
            if _readable_entry(item) and os.path.normcase(item["root"]) == marker:
                item = {**item, "seen": day}
                found = True
            out.append(item)
        if not found:
            out.append({"root": target, "seen": day})
        _write({**roster.body, "projects": out}, env=env)
    except Exception:
        return


# --- per-entry verification -------------------------------------------

CONNECTED_NO = "no"
CONNECTED_MISMATCH = "mismatch"

# What a read of one JSON file answered, when absence and damage mean
# different verdicts. `_ABSENT` is a project carrying no such record;
# `_UNREADABLE` is one whose record this build cannot parse.
_ABSENT = object()
_UNREADABLE = object()


@dataclass(frozen=True)
class ProjectState:
    """One roster entry, verified against the project's own files."""

    root: str                  # exactly as the roster stored it
    exists: bool               # Path(root).is_dir()
    mounted: bool              # the manifest file is a file
    connected: str             # a client table name, "no", or "mismatch"
    skills: tuple[str, ...]    # sorted names any agent has, possibly empty
    # The client table name the connect record itself claims, whatever
    # the client's settings say about it: "" where there is no record,
    # or where the record names no client this build's table carries
    # (PLAN-0014 phase 3). `connected` answers whether the claim holds;
    # this answers who it is about, so a mismatch can name the client
    # whose settings drifted.
    claimed: str = ""
    # The same names, agent by agent: one pair per harness in map order,
    # the harness's name and the sorted names its record lists that
    # still stand (PLAN-0015 phase 2). A skill installed for one agent
    # and not another is a state a person chose, so the listing row, the
    # Status fact and the project page's lists all read this one tuple
    # rather than each folding the agents for itself. `skills` above is
    # its union, which is what the machine-readable line still prints.
    agents: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Whether an update would write something here (PLAN-0021 phase 1,
    # Decided 6). `verify` leaves it False: the answer costs a read of
    # the seed and of every skill, which the command line's listing does
    # not pay; the manage page fills it from `update.behind` for the
    # projects it lists and reads it off this one state, so its listing
    # row and its project page agree with each other.
    behind: bool = False


def _read_json(path: Path):
    """One file's JSON, `_ABSENT`, or `_UNREADABLE`. Never raises.

    Deliberately not `connect._readable_record`: this module verifies and
    never writes through a record, so that function's write guards would
    refuse records this listing is meant to describe, and would surface
    their errors where a listing has no way to report one.
    """
    try:
        if not path.is_file():
            return _ABSENT
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return _UNREADABLE
    try:
        return json.loads(text)
    except ValueError:
        return _UNREADABLE


def _named_copies(body: dict) -> list[str] | None:
    """The settings copies the connect record itself names, or None.

    None means the record's own list is not this build's shape. Silently
    dropping the items that are not would let a damaged record go on
    answering a client's name off whichever items survived, which is a
    claim this listing cannot check.
    """
    entries = body.get("copies")
    if not isinstance(entries, list):
        return None
    named: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            return None
        shown = item.get("path")
        if not isinstance(shown, str) or not shown:
            return None
        named.append(shown)
    return named


def _connection(root: Path) -> tuple[str, str]:
    """Two answers off one read of the connect record: the `connected`
    facet, and the client name the record claims.

    The first is the client this project is registered with, "no", or
    "mismatch". Only the everything-agrees case answers a client name.
    The copies checked are the copies the record names, never the copies
    this machine happens to carry now: a client build installed after
    the connect is outside the record's claim and must not turn a healthy
    project into a mismatch. A record naming no checkable copy, or one
    whose list of them is not this build's shape, claims nothing this
    listing can confirm, so it reads as a mismatch.

    The second is the record's own `client` value when it names a client
    this build's table carries, and "" otherwise — filled on the same
    read, before the checks that can turn the first answer into a
    mismatch, so a mismatch still knows whose settings it is about
    (PLAN-0014 phase 3). The record is parsed once for both.
    """
    body = _read_json(root / cn.CONNECT_REL)
    if body is _ABSENT:
        return CONNECTED_NO, ""
    if not isinstance(body, dict):
        return CONNECTED_MISMATCH, ""
    name = body.get("client")
    client = cn.CLIENTS.get(name) if isinstance(name, str) else None
    if client is None:
        return CONNECTED_MISMATCH, ""
    key = body.get("key")
    entry = body.get("entry")
    if not isinstance(key, str) or not isinstance(entry, dict):
        return CONNECTED_MISMATCH, client.name
    # Every entry the record claims, both servers on a record written
    # since the write server was registered and the read server alone on
    # an older one (PLAN-0012 phase 1).
    entries = cn._recorded_entries(body)
    named = _named_copies(body)
    if not named or not entries:
        return CONNECTED_MISMATCH, client.name
    for shown in named:
        loaded = _read_json(Path(shown))
        if not isinstance(loaded, dict):
            return CONNECTED_MISMATCH, client.name
        servers = loaded.get(client.container)
        if not isinstance(servers, dict):
            return CONNECTED_MISMATCH, client.name
        if any(servers.get(k) != v for k, v in entries.items()):
            return CONNECTED_MISMATCH, client.name
    return client.name, client.name


def _installed(root: Path, entry: dict) -> bool:
    """Whether any file this skill entry records still stands on disk."""
    files = entry.get("files")
    if not isinstance(files, list):
        return False
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        try:
            if (root / item["path"]).is_file():
                return True
        except OSError:
            continue
    return False


def _skills(root: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The skills this project's own records say are installed here,
    one pair per harness in map order.

    Record-based and cheap: the byte-level audit is `radialrig skill
    list`'s job, and this listing answers what is here. An entry none of
    whose recorded files still stands is the stale-record state — nothing
    is installed, so nothing is listed. Iterating the harness map means a
    second harness costs no code here, and a record this build cannot
    read answers an empty list for that harness alone.
    """
    out: list[tuple[str, tuple[str, ...]]] = []
    for harness in sk.HARNESSES.values():
        names: set[str] = set()
        body = _read_json(root / harness.record)
        entries = body.get("skills") if isinstance(body, dict) else None
        for entry in entries if isinstance(entries, list) else ():
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            if _installed(root, entry):
                names.add(entry["name"])
        out.append((harness.name, tuple(sorted(names))))
    return tuple(out)


def agents_agree(state: ProjectState) -> bool:
    """Whether every agent has the same skills, so one count tells it.

    The rule both surfaces read (PLAN-0015 phase 2 prescription 5): the
    command line's row and the page's Status fact say one count where
    the lists match and one line per agent where they differ. A state
    built without the per-agent pairs is read as agreeing, so a row
    composed from the union alone still prints as it always has.
    """
    return len({names for _, names in state.agents}) <= 1


def verify(root: Path, *, env=None) -> ProjectState:
    """One project's state, read from its own files. Never raises.

    Any failure degrades that one facet to its negative value and leaves
    the others alone. `mounted` is `mount.is_mounted` — file presence,
    the exact gate the mount verb uses; a present-but-damaged manifest
    still reads mounted here, because the verb that acts on it is the one
    that reports the damage. `env` is accepted so every reader in this
    module carries the same injection seam.
    """
    target = Path(root)
    try:
        exists = target.is_dir()
    except OSError:
        exists = False
    try:
        mounted = mnt.is_mounted(target)
    except OSError:
        mounted = False
    connected, claimed = _connection(target)
    agents = _skills(target)
    return ProjectState(
        root=str(target),
        exists=exists,
        mounted=mounted,
        connected=connected,
        skills=tuple(sorted({name for _, names in agents for name in names})),
        claimed=claimed,
        agents=agents,
    )


# --- the listing, and the drop-gone rewrite ---------------------------

def listing(*, env=None, roster: Roster | None = None) -> tuple[ProjectState, ...]:
    """Every roster entry, verified, in roster file order.

    One state per project, not one per entry: a roster edited outside
    this build can name the same root twice, and the first entry in file
    order is the one that answers for it. Both surfaces read this, so a
    second row for one project would be a second project as far as a
    reader is concerned.

    A caller that has already loaded the roster may pass it as `roster`,
    which skips the load here: one read then answers both that caller's
    own view of the file and these states, so the two cannot disagree
    about a file edited between them.

    An entry whose root is no longer a directory comes back with
    `exists=False` and is dropped from the file by one best-effort atomic
    rewrite. The rewrite walks the raw list rather than these states, so
    a duplicate this listing collapsed is still carried forward or
    dropped by its own root. An unreadable or unwritable roster changes
    neither the states returned nor anything on disk, which is what lets
    the caller report and exit the same way either way. Nothing outside
    roster.json is written.
    """
    if roster is None:
        roster = load(env=env)
    listed: list[ProjectState] = []
    answered: set[str] = set()
    for entry in roster.entries:
        marker = os.path.normcase(entry["root"])
        if marker in answered:
            continue
        answered.add(marker)
        listed.append(
            replace(verify(Path(entry["root"]), env=env), root=entry["root"]))
    states = tuple(listed)
    gone = {
        os.path.normcase(state.root) for state in states if not state.exists
    }
    if not gone or not roster.writable:
        return states

    # The drop is the same best-effort side write the verbs make: the
    # states already computed are the report, and no failure past this
    # line may reach the caller.
    try:
        projects = roster.body.get("projects")
        raw_entries = projects if isinstance(projects, list) else []
        keep = [
            item for item in raw_entries
            if not (_readable_entry(item) and os.path.normcase(item["root"]) in gone)
        ]
        _write({**roster.body, "projects": keep}, env=env)
    except Exception:
        pass
    return states


# --- entries a client carries whose project is gone -------------------

@dataclass(frozen=True)
class Orphan:
    """One client settings entry this listing cannot tie to a project."""

    client: str               # the client table name
    key: str                  # the settings key
    root: str                 # the pinned project root, "" when it pins none
    copies: tuple[str, ...]   # posix paths of the copies carrying it


def _pinned_root(value) -> str:
    """The project root one settings entry pins, or "" when it pins none."""
    if not isinstance(value, dict):
        return ""
    entry_env = value.get("env")
    if not isinstance(entry_env, dict):
        return ""
    pinned = entry_env.get(ROOT_ENV)
    return pinned if isinstance(pinned, str) else ""


def _stands(root: str) -> bool:
    """Whether the path one settings entry pins is still on disk."""
    try:
        return Path(root).exists()
    except (OSError, ValueError):  # pragma: no cover - a path this OS rejects
        return False


def orphans(*, env=None) -> tuple[Orphan, ...]:
    """Client entries this build writes whose project root is gone.

    Read only, and it audits nothing: a copy that is absent or does not
    parse is skipped, because the listing reports what it can verify.

    One key is decided once, over every copy that carries it, and never
    copy by copy. That is what keeps the listing from advertising a
    removal the verb would refuse: the verb reads the same copies, so a
    key one copy pins to a standing project is not an orphan anywhere,
    and a key one copy pins to nothing cannot be shown to be one. A
    prefixed key that pins no root is listed with an empty root — an
    entry radialrig cannot prove is an orphan must still not be
    invisible, or nothing would ever name it to the user. Keys sort by
    key.
    """
    carried: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for client in cn.CLIENTS.values():
        try:
            copies = cn.detect(client, env=env)
        except OSError:  # pragma: no cover - an unreadable profile directory
            continue
        for copy in copies:
            loaded = _read_json(copy.path)
            if not isinstance(loaded, dict):
                continue
            servers = loaded.get(client.container)
            if not isinstance(servers, dict):
                continue
            for key, value in servers.items():
                if not isinstance(key, str) or not key.startswith(client.key_prefix):
                    continue
                carried.setdefault((client.name, key), []).append(
                    (_pinned_root(value), copy.path.as_posix()))

    found: list[Orphan] = []
    for (client_name, key), seen in carried.items():
        pins = [pinned for pinned, _ in seen]
        if any(pinned and _stands(pinned) for pinned in pins):
            continue
        found.append(Orphan(
            client=client_name, key=key,
            root=pins[0] if all(pins) else "",
            copies=tuple(shown for _, shown in seen),
        ))
    return tuple(sorted(found, key=lambda o: (o.key, o.client)))


# --- the wordings -----------------------------------------------------

ROSTER_LISTED = "ROSTER_LISTED"      # verb: current
ROSTER_MISMATCH = "ROSTER_MISMATCH"  # verb: left
ROSTER_GONE = "ROSTER_GONE"          # verb: removed
ROSTER_ORPHAN = "ROSTER_ORPHAN"      # verb: left
ROSTER_UNPINNED = "ROSTER_UNPINNED"  # verb: left

# The mismatch clause, on both surfaces (PLAN-0014 phase 3). A template
# with the client's slot, filled from the client table at render time,
# so no constant here names a client (constraint 5): the command line's
# row says it as its connection facet and the manage page says it as
# its connection fact, one sentence for one state. No period, because
# the row joins its facets with semicolons and the page sets it beside
# "Mounted" and "No skills".
MISMATCH_CLAUSE = (
    "{label}'s settings no longer carry the entry this project's record claims"
)

# The same state where the record names no client this build knows — a
# name outside the table, a record of the wrong shape, a file that will
# not parse. There is no label to fill, so the sentence names the record
# file instead, which is the one thing a reader can go and look at.
UNKNOWN_CLIENT_CLAUSE = (
    "This project's record names no client this build knows: see {file}"
)

# This module's own table, the way the skills kit carries its own. The
# two rows that read `{detail}` are filled by the builders below, so a
# row's facts are computed once and rendered once.
ROSTER_REASONS: dict[str, str] = {
    ROSTER_LISTED: "{detail}",
    ROSTER_MISMATCH: "{detail}",
    ROSTER_GONE: "no project stands at this path; the entry is dropped",
    ROSTER_ORPHAN: "this entry's project root no longer exists; remove with: {detail}",
    ROSTER_UNPINNED: ("this entry pins no project root, so radialrig cannot "
                      "show it is an orphan; remove it in the client's own "
                      "settings if you own it"),
}


def reason_text(d: mnt.Disposition) -> str:
    """The one wording for a roster row, else the mount kit's own.

    Both surfaces call this, and a caller passes it into `report` rather
    than `report` reaching for it: that is what keeps the import running
    one way.
    """
    text = ROSTER_REASONS.get(d.code)
    if text is None:
        return mnt.reason_text(d)
    if "{detail}" in text:
        return text.format(detail=d.detail)
    return text


def _mounted_fact(state: ProjectState) -> str:
    return "mounted" if state.mounted else "not mounted"


def _connected_fact(state: ProjectState) -> str:
    client = cn.CLIENTS.get(state.connected)
    if client is None:
        return f"not registered with {cn.label_phrase()}"
    return f"registered with {client.label}"


def _skills_fact(state: ProjectState) -> str:
    """One facet where the agents agree, one per agent where they differ.

    The agent's label comes from the harness map at render time, so no
    wording here names one; a listing that folded the agents into one
    facet would call a project "skills: plan-review" when only one of
    its agents can read that skill.
    """
    if agents_agree(state):
        return _skills_facet("", state.skills)
    return "; ".join(
        _skills_facet(sk.HARNESSES[name].label, names)
        for name, names in state.agents
    )


def _skills_facet(label: str, names) -> str:
    if not label:
        return "no skills" if not names else "skills: " + ", ".join(names)
    return f"{label} skills: " + (", ".join(names) if names else "none")


def mismatch_clause(state: ProjectState) -> str:
    """The mismatch sentence for this project, its slot filled.

    The client's label where the record names one this build's table
    carries, the record file's path where it does not. Public because
    both surfaces say it: the command line's row through `_facts`, and
    the manage page's connection fact through its own builder, so the
    template lives here once and neither surface restates it.
    """
    client = cn.CLIENTS.get(state.claimed)
    if client is not None:
        return MISMATCH_CLAUSE.format(label=client.label)
    return UNKNOWN_CLIENT_CLAUSE.format(file=Path(state.root) / cn.CONNECT_REL)


def _facts(state: ProjectState, connected: str) -> str:
    """One row's three facets, in the order the report reads them."""
    return "; ".join((_mounted_fact(state), connected, _skills_fact(state)))


def orphan_command(orphan: Orphan) -> str:
    """The one command that removes this entry, ready to copy."""
    return f"radialrig unconnect {orphan.client} --orphan {orphan.key}"


def report_rows(states, found) -> tuple[mnt.Disposition, ...]:
    """Every row of the projects report, built here rather than in report.

    A row is a `mount.Disposition` like every other row both surfaces
    render, so the column renderer needs nothing new. A mismatch row
    carries all three facets, not the mismatch alone, so the printed
    report and the machine-readable line say the same thing about the
    same project.
    """
    rows: list[mnt.Disposition] = []
    for state in states:
        if not state.exists:
            rows.append(mnt.Disposition(state.root, mnt.REMOVED, ROSTER_GONE))
        elif state.connected == CONNECTED_MISMATCH:
            rows.append(mnt.Disposition(
                state.root, mnt.LEFT, ROSTER_MISMATCH,
                _facts(state, mismatch_clause(state)),
            ))
        else:
            rows.append(mnt.Disposition(
                state.root, mnt.CURRENT, ROSTER_LISTED,
                _facts(state, _connected_fact(state)),
            ))
    for orphan in found:
        if orphan.root:
            rows.append(mnt.Disposition(
                orphan.key, mnt.LEFT, ROSTER_ORPHAN, orphan_command(orphan)))
        else:
            rows.append(mnt.Disposition(orphan.key, mnt.LEFT, ROSTER_UNPINNED))
    return tuple(rows)
