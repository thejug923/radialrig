"""The connect kit: register this project with an MCP client's settings.

PLAN-0007 Phase 2. `radialrig mount` writes `.mcp.json`, which some MCP
clients read and Claude Desktop does not: that client reads its own
settings file, somewhere else on the machine. This kit closes that gap.
Client-specific knowledge lives in one table, `CLIENTS` below, the way
the skills kit maps a harness to a path (D-34 rule 2). No function here
branches on a client name; a second client costs one row.

A connect registers both servers, the retrieval one and the authoring
one, as two entries in one inserted region (PLAN-0012 phase 1), the way
a mount registers both in `.mcp.json`. An unconnect removes both.

The dependency runs one way: this module imports `mount`, never the
reverse. It imports nothing from `skills`. It imports `report` for the
three sentences an error carries, so every user-facing wording still
lives in one of the two places that hold wordings.

The kit borrows `mount.py`'s vocabulary wholesale — `Disposition`, the
ten verbs, `reason_text`, the reason codes, the byte-level helpers — and
borrows its one invariant: **a connect only ever inserts**, so reversal
is the deletion of exactly the bytes it inserted.

The file this kit edits belongs to another program and sits outside every
project. `mount._guard_paths` cannot judge it — a path outside the root
is the normal case here, not the attack. The substitute guard is
`_known_location`: a recorded target is acted on only when it is one of
the paths this build's own client table can produce for the recorded
client, on this machine, now.

`connect` never returns the partial outcome. A refusal is all-or-nothing
across every copy, so there is no half-written state for it to describe.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from radialrig import mount as mnt
from radialrig import report
from radialrig.paths import ROOT_ENV

CONNECT_SCHEMA = 1
CONNECT_REL = ".radialrig/connect.json"
READ_COMMAND = "radialrig-read"
WRITE_COMMAND = "radialrig-write"
# The write server's key is the read server's key plus this suffix, so
# the two entries of one project sit together in the client's list and
# both start with the client's prefix (PLAN-0012 phase 1).
WRITE_KEY_SUFFIX = "-write"

# The scripts a console-script install lays down carry this suffix on
# Windows and no suffix elsewhere. Both are tried; the first that is a
# file wins.
COMMAND_SUFFIXES: tuple[str, ...] = ("", ".exe")


# --- the client table (PLAN-0007 constraint 2) ------------------------

@dataclass(frozen=True)
class Location:
    """One copy of one client's settings file, and how to find it."""

    label: str                # recorded and reported: "roaming" | "store" | "config"
    var: str                  # the environment variable the search starts from
    parts: tuple[str, ...]    # path parts under it; a part may hold a glob


@dataclass(frozen=True)
class Client:
    """One MCP client: where it keeps its settings, and what it is called."""

    name: str                          # the CLI argument
    label: str                         # one line for a report
    container: str                     # the settings member holding servers
    key_prefix: str                    # every key this kit writes starts here
    locations: tuple[Location, ...]
    restart: str                       # the last line of a real connect report


_DESKTOP = Client(
    name="claude-desktop",
    label="Claude Desktop",
    container="mcpServers",
    key_prefix="radialrig-",
    locations=(
        Location(
            label="roaming",
            var="APPDATA",
            parts=("Claude", "claude_desktop_config.json"),
        ),
        # PLAN-0007 Phase 2 prescription 3: the Store build keeps a second
        # copy under its package container. The package family name is
        # discovered, never spelled: one glob step, so a rename of the
        # family costs no code change.
        Location(
            label="store",
            var="LOCALAPPDATA",
            parts=("Packages", "*Claude*", "LocalCache", "Roaming",
                   "Claude", "claude_desktop_config.json"),
        ),
        # PLAN-0009 Phase 2 prescription 2: on Linux the client keeps
        # its settings under the user's config directory. Read on Arch
        # on 2026-08-23; the row closes for the first targets when the
        # same path is read on a real Ubuntu or Debian machine. No
        # platform gate here: `detect` finds a copy only where the
        # parent directory exists, and no other operating system builds
        # this path from `HOME`.
        Location(
            label="config",
            var="HOME",
            parts=(".config", "Claude", "claude_desktop_config.json"),
        ),
    ),
    restart="Fully quit and reopen Claude Desktop.",
)

# Two strings in that row are user-facing: `label` and `restart`. They
# live here rather than in report.py for the reason `SkillEntry.summary`
# does: a second client must cost one row, not one row plus a branch in
# the renderer. A test pins that no function below reads a client name.
CLIENTS: dict[str, Client] = {_DESKTOP.name: _DESKTOP}
DEFAULT_CLIENT = _DESKTOP.name


def clients() -> tuple[str, ...]:
    return tuple(CLIENTS)


def label_phrase(clients=None) -> str:
    """The clients' labels as one phrase, for the sentence that says a
    project is registered with none of them. None means every client in
    the table.

    A bare "not registered" read as though the mount's own `.mcp.json`
    entries had not landed (an outside tester, 2026-10-01); naming the
    client says what is missing. One client reads as its label alone, and
    more join with "or" at no cost here.
    """
    labels = [c.label for c in (CLIENTS.values() if clients is None else clients)]
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " or " + labels[-1]


# --- errors -----------------------------------------------------------

class ConnectError(RuntimeError):
    """Anything that stops a connect or an unconnect."""


class WriteStopped(ConnectError):
    """A file error stopped a verb, and the client's files stand as
    found unless `left` says otherwise: this kit's twin of
    `mount.WriteStopped`, with the same fields and the same one-sentence
    text (PLAN-0017 phase 2). Caught outside its module as the pair
    `(mnt.WriteStopped, cn.WriteStopped)`.
    """

    def __init__(self, path, reason: str, left=()) -> None:
        self.path = str(path) if path else ""
        self.reason = reason
        self.left = tuple(str(p) for p in left)
        super().__init__(mnt.stopped_sentence(self.path, self.reason, self.left))


def _stopped(exc: OSError, *, path=None, left=()) -> WriteStopped:
    """The typed error for a file error, named by the file it hit."""
    where = exc.filename if exc.filename else path
    return WriteStopped(where, exc.strerror or str(exc), left)


class UnknownClient(ConnectError):
    """A client name this build's table does not carry."""

    def __init__(self, name: str, available: tuple[str, ...]) -> None:
        offer = ", ".join(available) if available else "none"
        super().__init__(
            f"this build registers no client named '{name}'; it registers: {offer}"
        )
        self.name = name
        self.available = tuple(available)


class ConnectRefused(ConnectError):
    """Targets that block an all-or-nothing write. Nothing was written."""

    def __init__(self, client: Client, conflicts: tuple[mnt.Disposition, ...]) -> None:
        super().__init__(
            "connect would overwrite an entry this machine did not write: "
            + ", ".join(d.path for d in conflicts)
        )
        self.client = client
        self.conflicts = conflicts


def client_for(name: str) -> Client:
    client = CLIENTS.get(name)
    if client is None:
        raise UnknownClient(name, clients())
    return client


# --- what is on this machine ------------------------------------------

@dataclass(frozen=True)
class Copy:
    """One settings file of one client, on this machine."""

    label: str    # "roaming" | "store" | "config"
    path: Path    # absolute, resolved


def _expand(base: Path, parts: tuple[str, ...]) -> list[Path]:
    """Every path under `base` the parts describe, globs resolved."""
    current = [base]
    for part in parts:
        step: list[Path] = []
        if any(ch in part for ch in "*?["):
            for candidate in current:
                try:
                    step.extend(sorted(candidate.glob(part)))
                except OSError:  # pragma: no cover - an unreadable directory
                    continue
        else:
            step = [candidate / part for candidate in current]
        current = step
        if not current:
            return []
    return current


def detect(client: Client, *, env=None) -> tuple[Copy, ...]:
    """Every settings file of this client that this machine could hold.

    Reads the filesystem and `env`; opens nothing else and asks the
    network for nothing. A copy is a target when its parent directory
    exists: the directory is what says the client is installed there, and
    a missing file is a file this kit creates. An empty tuple means the
    client is not installed here.
    """
    environ = os.environ if env is None else env
    found: list[Copy] = []
    seen: set[str] = set()
    for location in client.locations:
        base = (environ.get(location.var) or "").strip()
        if not base:
            continue
        for candidate in _expand(Path(base), location.parts):
            if not candidate.parent.is_dir():
                continue
            resolved = candidate.resolve()
            marker = os.path.normcase(str(resolved))
            if marker in seen:
                continue
            seen.add(marker)
            found.append(Copy(label=location.label, path=resolved))
    return tuple(found)


def installed(*, env=None) -> tuple[Client, ...]:
    """The clients `detect` finds on this machine, in table order.

    Never raises. A client whose profile directory cannot be read counts
    as absent, the way the roster's orphan scan treats it: `is_dir`
    raises on a directory that denies traversal, and the manage page asks
    this once per listed row.
    """
    found = []
    for client in CLIENTS.values():
        try:
            if detect(client, env=env):
                found.append(client)
        except OSError:
            continue
    return tuple(found)


def _matches_location(base: Path, parts: tuple[str, ...], candidate: Path) -> bool:
    """Whether `candidate` has the shape this location describes.

    Shape rather than existence, so a Store copy whose package directory
    has since been removed is still a path this build knows and is
    reported absent rather than refused.
    """
    try:
        rel = candidate.relative_to(base)
    except ValueError:
        return False
    pieces = rel.parts
    if len(pieces) != len(parts):
        return False
    for piece, part in zip(pieces, parts):
        if any(ch in part for ch in "*?["):
            if not fnmatch.fnmatch(os.path.normcase(piece), os.path.normcase(part)):
                return False
        elif os.path.normcase(piece) != os.path.normcase(part):
            return False
    return True


def _known_location(client: Client, path: Path, *, env, known) -> bool:
    """The substitute for `mount._guard_paths` on paths outside the root."""
    resolved = path.resolve()
    marker = os.path.normcase(str(resolved))
    if any(os.path.normcase(str(item.path)) == marker for item in known):
        return True
    environ = os.environ if env is None else env
    for location in client.locations:
        base = (environ.get(location.var) or "").strip()
        if not base:
            continue
        if _matches_location(Path(base).resolve(), location.parts, resolved):
            return True
    return False


# --- the entry --------------------------------------------------------

_which = shutil.which  # module-level indirection, so a test can replace it


def _command_candidates(command: str | None, name: str = READ_COMMAND):
    """Where the console script `name` might be, best first.

    The client starts servers from its own directory with its own
    environment, so the entry carries a full path: a bare name is not
    guaranteed to resolve there. An explicit `command` names the read
    script; the write script ships beside it, so it is looked for there.
    """
    if command:
        given = Path(command)
        if name == READ_COMMAND:
            yield given
        else:
            for suffix in COMMAND_SUFFIXES:
                yield given.parent / (name + suffix)
    found = _which(name)
    if found:
        yield Path(found)
    # The scripts directory of the running interpreter. This is the
    # uv-tool case where the tool's bin directory is not yet on the
    # command path but this very process runs out of that environment.
    scripts = Path(sys.executable).parent
    for suffix in COMMAND_SUFFIXES:
        yield scripts / (name + suffix)
    # A console-script invocation whose interpreter sits elsewhere.
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        beside = Path(argv0)
        if beside.is_file():
            for suffix in COMMAND_SUFFIXES:
                yield beside.parent / (name + suffix)


def _located(command: str | None, name: str) -> str:
    """The full path to write into an entry.

    Writing a bare name as a fallback is refused: it would produce an
    entry that looks registered in the report and does nothing inside the
    client, which is the failure prescription 2 exists to prevent.
    """
    for candidate in _command_candidates(command, name):
        try:
            if candidate.is_file():
                return str(candidate.resolve())
        except OSError:  # pragma: no cover - an unreadable candidate
            continue
    raise ConnectError(report.connect_no_command(name))


def read_command(*, command: str | None = None) -> str:
    """The full path of the retrieval server's console script."""
    return _located(command, READ_COMMAND)


def write_command(*, command: str | None = None) -> str:
    """The full path of the authoring server's console script."""
    return _located(command, WRITE_COMMAND)


def entry_key(client: Client, scope: str) -> str:
    """The settings key this project's read entry carries."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", scope).strip("-")
    if not slug:
        raise ConnectError(report.CONNECT_EMPTY_SCOPE)
    return client.key_prefix + slug


def write_entry_key(client: Client, scope: str) -> str:
    """The settings key this project's write entry carries."""
    return entry_key(client, scope) + WRITE_KEY_SUFFIX


def _entry(root: Path, *, command: str | None) -> dict:
    """The read server's entry.

    `args` and `env` are always emitted, even when `args` is empty, so
    the recorded entry and the on-disk entry compare equal by structure
    with no special cases. The project root is pinned in the entry's
    environment because the client starts servers from its own directory.
    """
    return {
        "command": read_command(command=command),
        "args": [],
        "env": {ROOT_ENV: str(root)},
    }


def _write_entry(root: Path, *, command: str | None) -> dict:
    """The write server's entry: the same shape, the other script."""
    return {
        "command": write_command(command=command),
        "args": [],
        "env": {ROOT_ENV: str(root)},
    }


def _entries(root: Path, client: Client, scope: str, *, command: str | None) -> dict:
    """Both entries a connect registers, by key, read server first."""
    return {
        entry_key(client, scope): _entry(root, command=command),
        write_entry_key(client, scope): _write_entry(root, command=command),
    }


def _recorded_entries(body: dict) -> dict[str, dict]:
    """The entries a connect record claims, by key.

    A record written since the write server was registered carries them
    under `entries`; an older one registered the read server alone and
    carries that entry under `entry`.
    """
    listed = body.get("entries")
    if isinstance(listed, dict):
        return {k: v for k, v in listed.items()
                if isinstance(k, str) and isinstance(v, dict)}
    key = body.get("key")
    entry = body.get("entry")
    if isinstance(key, str) and isinstance(entry, dict):
        return {key: entry}
    return {}


def _resolve_scope(root: Path, scope: str | None) -> str:
    """An explicit scope, else the mount record's, else the directory name.

    The mount record is the authority over the directory name because it
    is where this project's own records say what this project is called.
    A record that exists and does not parse is an error, not a silent
    fall-through: acting on a project whose record is broken is the case
    `mount` already refuses.
    """
    if scope:
        return scope
    if mnt.is_mounted(root):
        try:
            manifest = mnt.read_manifest(root)
        except mnt.MountError as exc:
            raise ConnectError(str(exc)) from exc
        recorded = manifest.get("scope")
        if isinstance(recorded, str) and recorded.strip():
            return recorded
    return mnt.default_scope(root)


# --- the record -------------------------------------------------------

def record_path(root: Path) -> Path:
    return Path(root) / CONNECT_REL


def _record_copies(body: dict) -> list[dict]:
    entries = body.get("copies")
    if not isinstance(entries, list):
        return []
    return [
        e for e in entries
        if isinstance(e, dict) and isinstance(e.get("path"), str) and e["path"]
    ]


def read_record(
    root: Path, client_name: str = DEFAULT_CLIENT, *, env=None, paths=None,
) -> dict | None:
    """The connect record as JSON, or None when this project carries none.

    Every read goes through the guards below, because the record is a
    file the host can edit and the kit acts on the paths it names.
    """
    client = client_for(client_name)
    return _load_record(Path(root).resolve(), client, env, known=tuple(paths or ()))


def _load_record(root: Path, client: Client, env, *, known) -> dict | None:
    path = record_path(root)
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConnectError(f"{CONNECT_REL} does not parse as JSON: {exc}") from exc
    return _readable_record(client, body, env=env, known=known)


def _readable_record(client: Client, body, *, env, known) -> dict:
    """Refuse a record this build cannot act on, before any write.

    Four checks, in the order a wrong record does damage: a schema this
    build does not know, a client this build's table does not carry, a
    key outside that client's prefix — which would be a record steering a
    deletion at somebody else's server entry — and a path this build's
    table could never produce.
    """
    if not isinstance(body, dict):
        raise ConnectError(f"{CONNECT_REL} does not hold a JSON object")
    schema = body.get("schema")
    if not isinstance(schema, int):
        raise ConnectError(f"{CONNECT_REL} carries no readable schema number")
    if schema > CONNECT_SCHEMA:
        raise ConnectError(
            f"this connect record was written by a newer radialrig "
            f"(schema {schema}); install that version, or remove the entry "
            f"from the client's settings file and delete {CONNECT_REL} yourself"
        )
    name = body.get("client")
    if not isinstance(name, str) or name not in CLIENTS:
        raise ConnectError(
            f"{CONNECT_REL} names a client this build does not register: "
            f"'{name}'; nothing was changed"
        )
    recorded_client = CLIENTS[name]
    key = body.get("key")
    listed = body.get("entries")
    keys = [key] + (list(listed) if isinstance(listed, dict) else [])
    for named in keys:
        if not isinstance(named, str) or not named.startswith(recorded_client.key_prefix):
            raise ConnectError(
                f"{CONNECT_REL} names an entry key this build never writes: "
                f"'{named}'; nothing was changed"
            )
    for item in _record_copies(body):
        if not _known_location(recorded_client, Path(item["path"]),
                               env=env, known=known):
            raise ConnectError(
                f"{CONNECT_REL} names a path outside this client's settings "
                f"on this machine: '{item['path']}'; nothing was changed"
            )
    # A record naming another client is readable but not this verb's
    # business: every caller compares `body["client"]` to the client it
    # was asked about and answers "not registered" when they differ.
    return body


# --- the plans --------------------------------------------------------

@dataclass(frozen=True)
class CopyPlan:
    """What connect does to one copy. One per detected copy."""

    copy: Copy
    disposition: mnt.Disposition
    data: bytes | None            # None means write nothing at all
    file_created: bool
    written: bool
    insert: str | None            # the exact inserted region, or None
    reserialized: bool
    sha256: str                   # of the file after the write
    pre_connect: str | None       # of the file before it

    def adopted(self) -> "CopyPlan":
        """This copy as a record adopting an entry already in place.

        The entry is there and identical and no record claims it, so the
        new record claims it structurally: no insert region and no earlier
        hash, which is what makes the reversal take the structural path
        and say the file changed since the connect rather than claim a
        byte-identical restore it cannot prove.
        """
        return replace(
            self, written=True, file_created=False, insert=None,
            reserialized=False, pre_connect=None,
        )

    def as_json(self) -> dict:
        out: dict = {
            "label": self.copy.label,
            "path": self.copy.path.as_posix(),
            "file_created": self.file_created,
            "written": self.written,
            "sha256": self.sha256,
        }
        if self.insert is not None:
            out["insert"] = self.insert
        if self.reserialized:
            out["reserialized"] = True
        if self.pre_connect is not None:
            out["pre_connect_sha256"] = self.pre_connect
        return out


@dataclass(frozen=True)
class ConnectResult:
    """What a connect would write, or wrote. One computation, two callers,
    so `--dry-run` cannot drift from the run it describes."""

    root: Path
    client: Client
    scope: str
    key: str                             # the read entry's key
    entry: dict                          # the read entry
    connected: str                       # YYYY-MM-DD
    copies: tuple[CopyPlan, ...]
    record_after: dict | None
    current: bool = False                # every copy already registers this
    record_created: bool = False         # the record file did not exist yet
    entries: dict = field(default_factory=dict)  # both entries, by key

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self.entries) if self.entries else (self.key,)

    @property
    def dispositions(self) -> tuple[mnt.Disposition, ...]:
        rows = [plan.disposition for plan in self.copies]
        if not self.current:
            rows.append(mnt.Disposition(
                CONNECT_REL,
                mnt.CREATED if self.record_created else mnt.UPDATED,
                mnt.CONNECT_RECORD_WRITTEN,
            ))
        return tuple(rows)

    @property
    def wrote(self) -> bool:
        # The record is written whenever this run was not already current,
        # so a run that only adopted an entry still wrote something.
        return not self.current or any(plan.data is not None for plan in self.copies)


@dataclass(frozen=True)
class UnconnectPlan:
    """What an unconnect would do here, and what it did."""

    root: Path
    client: Client
    connected: bool                      # the record named this client
    key: str                             # the read entry's key
    dispositions: tuple[mnt.Disposition, ...]
    writes: tuple[tuple[Path, bytes], ...]
    removals: tuple[Path, ...]
    dir_created: bool = False            # the kit made .radialrig/ itself
    keys: tuple[str, ...] = ()           # every key the record claimed

    @property
    def entries(self) -> tuple[mnt.Disposition, ...]:
        """Every row but the record's own.

        What the head counts. The record is this kit's own file inside the
        project, not an entry in the client's settings file, and the row
        for it says it was removed in its own words.
        """
        return tuple(d for d in self.dispositions if d.path != CONNECT_REL)

    @property
    def removed(self) -> int:
        return sum(1 for d in self.entries if d.verb in (mnt.REMOVED, mnt.RESTORED))

    @property
    def left(self) -> int:
        return sum(1 for d in self.entries if d.verb in (mnt.LEFT, mnt.EXCISED))

    @property
    def complete(self) -> bool:
        return not any(d.verb in (mnt.LEFT, mnt.EXCISED) for d in self.dispositions)


# --- the writers ------------------------------------------------------
# `mount._write_all` joins root / rel and creates directories through
# `_missing_parents`, which requires containment. Every path here is
# absolute and outside the root, so these two are its shape without the
# join. All-or-nothing spans every copy: writing the visible one and
# refusing the hidden one would leave the two disagreeing, which is the
# exact failure prescription 3 exists to prevent.

def _write_copies(payload: dict[Path, bytes], *, make: tuple[Path, ...] = ()) -> None:
    """Write every copy, or leave them as found. Whatever stops the loop,
    every write made so far is put back through `mount._put_back`; a
    file error then becomes `WriteStopped` naming what could not be put
    back, and anything else is re-raised as itself."""
    originals: dict[Path, bytes | None] = {}
    created: list[Path] = []
    current: Path | None = None
    try:
        for directory in make:
            current = directory
            directory.mkdir(parents=True, exist_ok=True)
            created.append(directory)
        for target in sorted(payload):
            current = target
            originals[target] = target.read_bytes() if target.is_file() else None
            target.write_bytes(payload[target])
    except BaseException as exc:
        left = mnt._put_back(originals, created)
        if isinstance(exc, OSError):
            raise _stopped(exc, path=current, left=left) from exc
        raise


def _restore_copies(writes: dict[Path, bytes], removals) -> None:
    """Apply every rewrite and removal, or leave them as found. Stops the
    way `_write_copies` stops."""
    touched = sorted(set(writes) | set(removals))
    originals: dict[Path, bytes | None] = {}
    current: Path | None = None
    try:
        for target in touched:
            current = target
            originals[target] = target.read_bytes() if target.is_file() else None
        for target in sorted(writes):
            current = target
            target.write_bytes(writes[target])
        for target in sorted(removals):
            current = target
            target.unlink()
    except BaseException as exc:
        left = mnt._put_back(originals)
        if isinstance(exc, OSError):
            raise _stopped(exc, path=current, left=left) from exc
        raise


# --- the splice -------------------------------------------------------

def _text_field(item: dict, name: str) -> str | None:
    """One recorded string field, or None when it is absent or not one."""
    value = item.get(name)
    return value if isinstance(value, str) and value else None


def _copy_plan(
    target: Copy, entries: dict, container: str, *, recorded,
) -> CopyPlan:
    """What connect does to one copy. Reads; writes nothing.

    Both entries go in as one contiguous region, so the reversal is the
    deletion of one region. The no-container case is not special-cased:
    `mount`'s composer already inserts the whole container block when the
    member is absent, keeping every existing preference key byte for byte.
    """
    path = target.path
    shown = path.as_posix()
    detail = ", ".join(f"{container}.{key}" for key in entries)

    def refuse(code: str) -> CopyPlan:
        return CopyPlan(
            copy=target, disposition=mnt.Disposition(shown, mnt.LEFT, code),
            data=None, file_created=False, written=False, insert=None,
            reserialized=False, sha256="", pre_connect=None,
        )

    if path.is_dir():
        return refuse(mnt.LEFT_NOT_A_FILE)
    if not path.is_file():
        data = (json.dumps({container: dict(entries)}, indent=2,
                           ensure_ascii=False) + "\n").encode("utf-8")
        return CopyPlan(
            copy=target,
            disposition=mnt.Disposition(shown, mnt.CREATED, mnt.CONNECT_CREATED),
            data=data, file_created=True, written=True, insert=None,
            reserialized=False, sha256=mnt._sha256(data), pre_connect=None,
        )

    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        loaded = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        # A UTF-8 byte order mark lands here: decoding leaves it in place
        # and the parser refuses it. That is the treatment `mount` gives
        # an unreadable .mcp.json, and it writes nothing rather than
        # guessing at the shape.
        return refuse(mnt.CONNECT_UNPARSED)
    if not isinstance(loaded, dict):
        return refuse(mnt.CONNECT_UNPARSED)
    servers = loaded.get(container, {})
    if not isinstance(servers, dict):
        return refuse(mnt.CONNECT_UNPARSED)

    marker = os.path.normcase(str(path))
    if any(key in servers for key in entries):
        if all(servers.get(key) == entry for key, entry in entries.items()):
            # A re-connect. When this project's own record already claims
            # the entries here, the record's write facts travel into the
            # new record unchanged: they are what makes the reversal
            # possible, and dropping them would orphan an entry this kit
            # wrote. When no record claims them, the entries are left
            # alone — the same rule the mount keeps for a key it cannot
            # prove it wrote.
            prior = recorded.get(marker)
            if prior is not None and prior.get("written"):
                return CopyPlan(
                    copy=target,
                    disposition=mnt.Disposition(
                        shown, mnt.CURRENT, mnt.CONNECT_CURRENT),
                    data=None,
                    file_created=bool(prior.get("file_created")),
                    written=True,
                    insert=_text_field(prior, "insert"),
                    reserialized=bool(prior.get("reserialized")),
                    sha256=str(prior.get("sha256") or ""),
                    pre_connect=_text_field(prior, "pre_connect_sha256"),
                )
            return CopyPlan(
                copy=target,
                disposition=mnt.Disposition(shown, mnt.CURRENT, mnt.CONNECT_CURRENT),
                data=None, file_created=False, written=False, insert=None,
                reserialized=False, sha256=mnt._sha256(raw), pre_connect=None,
            )
        # There is deliberately no refresh in place: the remedy is
        # unconnect then connect, two bounded verbs instead of one verb
        # with an update path. A copy holding one of the two keys, as a
        # connect made before the write server was registered left it,
        # takes the same road: the old record reverses it whole.
        return refuse(
            mnt.CONNECT_ENTRY_STALE if marker in recorded else mnt.CONNECT_KEY_HOST_OWNED
        )

    spliced = mnt._splice_mcp_entries(text, loaded, entries, container=container)
    if spliced is not None:
        new_text, region = spliced
        data = new_text.encode("utf-8")
        return CopyPlan(
            copy=target,
            disposition=mnt.Disposition(shown, mnt.MERGED, mnt.CONNECT_MERGED, detail),
            data=data, file_created=False, written=True, insert=region,
            reserialized=False, sha256=mnt._sha256(data),
            pre_connect=mnt._sha256(raw),
        )

    # The scanner did not read this shape. Fall back to re-serialization
    # rather than risk a wrong splice, and record that the fallback ran.
    merged = json.loads(text)
    merged.setdefault(container, {}).update(entries)
    data = (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return CopyPlan(
        copy=target,
        disposition=mnt.Disposition(
            shown, mnt.MERGED, mnt.CONNECT_MERGED_RESERIALIZED, detail),
        data=data, file_created=False, written=True, insert=None,
        reserialized=True, sha256=mnt._sha256(data), pre_connect=mnt._sha256(raw),
    )


# --- connect ----------------------------------------------------------

def _record_markers(body: dict | None, key: str) -> dict[str, dict]:
    """This project's recorded copies for this exact key, by path.

    A mapping rather than a set of paths, because a re-connect needs the
    recorded facts themselves: a copy that comes back CURRENT is one this
    kit may already have written, and the record is the only place that
    says so.
    """
    if not body or body.get("key") != key:
        return {}
    return {
        os.path.normcase(str(Path(item["path"]).resolve())): item
        for item in _record_copies(body)
    }


def _record_holds(record: dict | None, key: str, entries: dict, plans) -> bool:
    """Whether this project's record already describes exactly this state.

    A record that names this key, these entries and every copy detected
    here needs no rewrite. Anything else — no record at all, or one that
    misses a copy — is a record that cannot reverse what is on disk, and
    the connect writes it rather than reporting the project done.
    """
    if not record or record.get("key") != key:
        return False
    if _recorded_entries(record) != entries:
        return False
    recorded = _record_markers(record, key)
    return all(
        os.path.normcase(str(plan.copy.path)) in recorded for plan in plans
    )


def _missing_record_dirs(root: Path) -> tuple[Path, ...]:
    """The directories the record's own write would create, outermost first."""
    missing: list[Path] = []
    current = record_path(root).parent
    while current != root and root in current.parents:
        if not current.exists():
            missing.append(current)
        current = current.parent
    return tuple(reversed(missing))


def _compose_connect(
    root: Path, client_name: str, *, scope, copies_in, env, command, today,
) -> tuple[dict[Path, bytes], tuple[Path, ...], ConnectResult]:
    root = Path(root).resolve()
    client = client_for(client_name)
    environ = os.environ if env is None else env
    copies = tuple(copies_in) if copies_in is not None else detect(client, env=environ)
    if not copies:
        raise ConnectError(report.CONNECT_NO_CLIENT)

    record = _load_record(root, client, environ, known=copies)
    if record is not None and record.get("client") != client.name:
        record = None
    resolved_scope = _resolve_scope(root, scope)
    key = entry_key(client, resolved_scope)
    if record is not None and record.get("key") != key:
        # This project is already registered under another name. Writing
        # a second entry would leave the first one with no record able to
        # remove it, so the connect refuses and names the reversal.
        raise ConnectRefused(client, (mnt.Disposition(
            CONNECT_REL, mnt.LEFT, mnt.CONNECT_RECORD_OTHER_KEY),))
    entries = _entries(root, client, resolved_scope, command=command)
    entry = entries[key]
    day = (today or dt.date.today()).isoformat()
    recorded = _record_markers(record, key)

    plans = [
        _copy_plan(target, entries, client.container, recorded=recorded)
        for target in copies
    ]
    conflicts = tuple(
        plan.disposition for plan in plans if plan.disposition.verb == mnt.LEFT
    )
    if conflicts:
        raise ConnectRefused(client, conflicts)

    if all(plan.disposition.verb == mnt.CURRENT for plan in plans):
        if _record_holds(record, key, entries, plans):
            # Nothing at all, the record included: a project that ran the
            # command twice should see no second timestamp.
            connected = day
            if isinstance(record.get("connected"), str):
                connected = record["connected"]
            return {}, (), ConnectResult(
                root=root, client=client, scope=resolved_scope, key=key, entry=entry,
                connected=connected, copies=tuple(plans), record_after=record,
                current=True, entries=entries,
            )
        # The entry is in place and this project carries no record that
        # can remove it — a project copied without its `.radialrig/`, or
        # one whose record was deleted. The record is the only thing that
        # makes the reversal possible, so this run writes it and adopts
        # the entry rather than reporting a project that is done. A copy
        # the old record does describe keeps what it said, adoption and
        # host-owned alike: only an unclaimed copy is adopted.
        plans = [
            plan if os.path.normcase(str(plan.copy.path)) in recorded
            else plan.adopted()
            for plan in plans
        ]

    existed = record_path(root).is_file()
    make = () if existed else _missing_record_dirs(root)
    record_after = {
        "schema": CONNECT_SCHEMA,
        "radialrig": mnt.installed_version(),
        "client": client.name,
        "connected": day,
        "scope": resolved_scope,
        "key": key,
        "hash": mnt.HASH_NAME,
        "dir_created": bool(make) or bool(record and record.get("dir_created")),
        # `key` and `entry` keep naming the read server, as every record
        # before the write server was registered did; `entries` carries
        # both.
        "entry": entry,
        "entries": entries,
        "copies": [plan.as_json() for plan in plans],
    }
    payload: dict[Path, bytes] = {
        plan.copy.path: plan.data for plan in plans if plan.data is not None
    }
    payload[record_path(root)] = mnt._dump_manifest(record_after)
    result = ConnectResult(
        root=root, client=client, scope=resolved_scope, key=key, entry=entry,
        connected=day, copies=tuple(plans), record_after=record_after,
        record_created=not existed, entries=entries,
    )
    return payload, make, result


def connect_plan(
    root: Path, client_name: str = DEFAULT_CLIENT, *, scope: str | None = None,
    paths=None, env=None, command: str | None = None, today: dt.date | None = None,
) -> ConnectResult:
    """What `connect` would write here. Writes nothing, raises the same
    refusals, and returns the same result object. A file error while
    reading is `WriteStopped` with nothing left."""
    try:
        _, _, result = _compose_connect(
            root, client_name, scope=scope, copies_in=paths, env=env,
            command=command, today=today,
        )
    except OSError as exc:
        raise _stopped(exc) from exc
    return result


def connect(
    root: Path, client_name: str = DEFAULT_CLIENT, *, scope: str | None = None,
    paths=None, env=None, command: str | None = None, today: dt.date | None = None,
) -> ConnectResult:
    """Register this project with one MCP client. Compute, then write.

    The record travels inside the same transaction as the copies: a
    record that disagrees with the files is worse than no record. A
    file error, while reading or writing, is `WriteStopped`.
    """
    try:
        payload, make, result = _compose_connect(
            root, client_name, scope=scope, copies_in=paths, env=env,
            command=command, today=today,
        )
    except OSError as exc:
        raise _stopped(exc) from exc
    if payload:
        _write_copies(payload, make=make)
    return result


def is_connected(root: Path, client_name: str = DEFAULT_CLIENT) -> bool:
    """Whether this project's record names this client. Never raises."""
    try:
        body = read_record(root, client_name)
    except ConnectError:
        return False
    return bool(body) and body.get("client") == client_name


# --- unconnect --------------------------------------------------------

def _held_keys(data: bytes, client: Client) -> tuple[str, ...]:
    """The keys with this kit's prefix under the client's container, as
    the file stands.

    When the file no longer parses (the client appended to it, say), the
    container cannot be told from any other object, so the test falls
    back to the text: every quoted key with the prefix that is followed
    by a colon. The region excised afterwards must name one of them, so
    a wrong match still bounds the removal to bytes that carry this
    kit's own key.
    """
    try:
        loaded = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        pattern = rb'"(' + re.escape(client.key_prefix.encode("utf-8")) + rb'[^"]*)"\s*:'
        return tuple(dict.fromkeys(
            m.decode("utf-8", errors="replace") for m in re.findall(pattern, data)
        ))
    if not isinstance(loaded, dict):
        return ()
    servers = loaded.get(client.container)
    if not isinstance(servers, dict):
        return ()
    return tuple(
        k for k in servers if isinstance(k, str) and k.startswith(client.key_prefix)
    )


def _unconnect_copy(entries: dict, client: Client, item: dict):
    """What the reversal does to one recorded copy, step for step.

    Copied from `mount._unmount_mcp` with this kit's codes, and it keeps
    the same honesty rule: "restored" is claimed only when the remainder
    provably hashes to the file from before the connect. `entries` is
    every key the record claims with what it recorded for each.

    The record is the project's file, not this kit's: its `file_created`
    and `insert` are honoured only when the standing file carries an
    entry under this kit's prefix, and the recorded region names one of
    them. A record for a file this kit really registered can always show
    its own key; one that cannot steers no removal.
    """
    path = Path(item["path"])
    shown = path.as_posix()
    container = client.container

    if path.is_dir():
        return mnt.Disposition(shown, mnt.LEFT, mnt.LEFT_NOT_A_FILE), None
    if not item.get("written", True):
        return mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_LEFT_HOST_OWNED), None
    if not path.is_file():
        return mnt.Disposition(shown, mnt.ABSENT, mnt.ABSENT_ALREADY_GONE), None

    data = path.read_bytes()
    held = _held_keys(data, client)
    if item.get("file_created"):
        if not held:
            return mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_RECORD_UNPROVEN), None
        if mnt.matches_recorded(data, item.get("sha256")):
            return mnt.Disposition(shown, mnt.REMOVED, mnt.REMOVED_FILE), ("remove", None)
        return mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_FILE_EDITED), None

    insert = item.get("insert")
    if isinstance(insert, str) and insert:
        if not any(f'"{key}"' in insert for key in held):
            return mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_RECORD_UNPROVEN), None
        # The region as the file holds it, as recorded or with its
        # endings flipped by a checkout (BUG-0011); the excision works
        # on the file as it stands, and the restore writes the form that
        # hashes to the pre-connect record.
        region = mnt._held_region(data, insert.encode("utf-8"))
        seen = data.count(region)
        if seen == 1:
            remainder = data.replace(region, b"", 1)
            restored = mnt._recorded_form(remainder, item.get("pre_connect_sha256"))
            if restored is not None:
                return (mnt.Disposition(shown, mnt.RESTORED, mnt.CONNECT_RESTORED),
                        ("write", restored))
            return (mnt.Disposition(shown, mnt.EXCISED, mnt.CONNECT_EXCISED_REGION),
                    ("write", remainder))
        if seen > 1:
            return mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_AMBIGUOUS), None

    # The region is gone or the file was reformatted: drop the key
    # structurally, and say so. Every other key survives.
    try:
        loaded = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_UNPARSED_LEFT), None
    if not isinstance(loaded, dict):
        return mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_UNPARSED_LEFT), None
    servers = loaded.get(container)
    present = {k: v for k, v in entries.items()
               if isinstance(servers, dict) and k in servers}
    if not present:
        return mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_KEY_GONE), None
    if any(v is not None and servers[k] != v for k, v in present.items()):
        return mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_ENTRY_CHANGED), None
    for k in present:
        del servers[k]
    # An emptied container object stays: this kit did not create it.
    out = (json.dumps(loaded, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    detail = ", ".join(f"{container}.{k}" for k in present)
    return (mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_RESERIALIZED, detail),
            ("write", out))


def unconnect_plan(
    root: Path, client_name: str = DEFAULT_CLIENT, *, env=None, paths=None,
) -> UnconnectPlan:
    """What an unconnect would do here. Writes nothing. A file error
    while reading is `WriteStopped` with nothing left."""
    try:
        return _unconnect_plan(root, client_name, env=env, paths=paths)
    except OSError as exc:
        raise _stopped(exc) from exc


def _unconnect_plan(root: Path, client_name: str, *, env, paths) -> UnconnectPlan:
    root = Path(root).resolve()
    client = client_for(client_name)
    environ = os.environ if env is None else env
    body = _load_record(root, client, environ, known=tuple(paths or ()))
    if body is None or body.get("client") != client.name:
        return UnconnectPlan(
            root=root, client=client, connected=False, key="",
            dispositions=(), writes=(), removals=(),
        )

    key = str(body.get("key", ""))
    entries: dict = _recorded_entries(body) or {key: None}
    dispositions: list[mnt.Disposition] = []
    writes: dict[Path, bytes] = {}
    removals: list[Path] = []
    for item in _record_copies(body):
        disposition, action = _unconnect_copy(entries, client, item)
        dispositions.append(disposition)
        if action is None:
            continue
        kind, data = action
        if kind == "remove":
            removals.append(Path(item["path"]))
        else:
            writes[Path(item["path"])] = data

    dispositions.sort(key=lambda d: d.path)
    # The record goes last and unconditionally, however many copies were
    # left: a record claiming bytes that are now the client's own would
    # be a false record.
    dispositions.append(
        mnt.Disposition(CONNECT_REL, mnt.REMOVED, mnt.CONNECT_RECORD_DROPPED)
    )
    return UnconnectPlan(
        root=root, client=client, connected=True, key=key,
        dispositions=tuple(dispositions),
        writes=tuple(sorted(writes.items())), removals=tuple(sorted(removals)),
        dir_created=bool(body.get("dir_created")), keys=tuple(entries),
    )


def unconnect(
    root: Path, client_name: str = DEFAULT_CLIENT, *, env=None, paths=None,
) -> UnconnectPlan:
    """Reverse the connect, then report what was left and why."""
    plan = unconnect_plan(root, client_name, env=env, paths=paths)
    if not plan.connected:
        return plan
    _restore_copies(dict(plan.writes), plan.removals)
    record = record_path(plan.root)
    try:
        record.unlink()
    except FileNotFoundError:  # pragma: no cover - the plan just read it
        pass
    except OSError as exc:
        # The reversal landed and the record would not go: what the plan
        # changed stands changed, and the error says so.
        raise _stopped(exc, path=record, left=(
            *(path for path, _ in plan.writes), *plan.removals)) from exc
    if plan.dir_created:
        try:
            record.parent.rmdir()
        except OSError:
            # It holds something else, or the platform will not remove
            # it. Either way it is not this kit's to take.
            pass
    return plan
