"""The client entry whose project is gone: `unconnect --orphan <key>`.

PLAN-0008 Phase 3. `unconnect` reverses a connect by reading the record
the connect left inside the project. When the project itself is deleted,
that record dies with it and the entry in the client's settings file has
nothing left to reverse it: the client keeps starting a server that
points at a directory nobody has. `radialrig projects` names such an
entry; this module removes exactly the one named.

The logic lives here rather than in `connect.py` because Phase 3 adds no
code to that file: a new module reaches the same internals — the client
table, the discovery, the all-or-nothing writer — through ordinary
imports, and the dependency still runs one way (this module imports
`connect`, `mount` and `report`; none of the three imports it).

Two refusals bound the verb, and one error. A key outside the client's
own prefix is not one radialrig writes, so this verb never touches it. A
key whose entry pins a project root that still stands is a plain
`unconnect` at that project, which reverses byte for byte. A key pinning
no root at all cannot be shown to be an orphan either way, so the verb
declines to act on it and says why.

What the removal claims is narrower than what `unconnect` claims, and the
row says so: this entry never had a record, so there is no pre-connect
file to restore to and no insert region to reverse. The entry is dropped
structurally and the rest of the file is re-serialized. That excise is
the whole deliverable here, which is why it counts as done rather than as
left — the opposite of `unconnect`, whose excise means it could not keep
its byte-identical promise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import report
from radialrig.paths import ROOT_ENV


# --- this module's own wordings table ---------------------------------
# The skills kit and the roster carry one each, for the same reason: the
# row is this verb's own claim, and `mount.REASONS` may not grow.

ORPHAN_EXCISED = "ORPHAN_EXCISED"

ORPHAN_REASONS: dict[str, str] = {
    ORPHAN_EXCISED: "entry removed; the rest of the file was re-serialized at indent 2",
}


def reason_text(d: mnt.Disposition) -> str:
    """The one wording for an orphan row, else the mount kit's own.

    A caller passes this into `report` rather than `report` reaching for
    it, which is what keeps the import between the two running one way.
    """
    text = ORPHAN_REASONS.get(d.code)
    if text is None:
        return mnt.reason_text(d)
    if "{detail}" in text:
        return text.format(detail=d.detail)
    return text


# --- the refusal ------------------------------------------------------

class OrphanRefused(cn.ConnectError):
    """A named entry this verb declines to remove. Nothing was written."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- what one run would do --------------------------------------------

@dataclass(frozen=True)
class OrphanPlan:
    """What removing one named entry would do here, and what it did."""

    client: cn.Client
    key: str
    found: bool                              # the key stood in one copy or more
    dispositions: tuple[mnt.Disposition, ...]
    writes: tuple[tuple[Path, bytes], ...]

    @property
    def removed(self) -> int:
        """The copies the entry left. An excise is the whole job here."""
        return sum(1 for d in self.dispositions if d.verb == mnt.EXCISED)

    @property
    def left(self) -> int:
        return sum(1 for d in self.dispositions if d.verb == mnt.LEFT)

    @property
    def complete(self) -> bool:
        return not any(d.verb == mnt.LEFT for d in self.dispositions)


# --- reading one copy -------------------------------------------------

# What a read of one settings file answered. Absence and damage are
# different rows: a file that is not there held nothing, and a file that
# does not parse might hold anything.
_ABSENT = object()
_UNREADABLE = object()


def _load(path: Path):
    """One settings file as a dict, `_ABSENT`, or `_UNREADABLE`."""
    try:
        if not path.is_file():
            return _ABSENT
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return _UNREADABLE
    try:
        loaded = json.loads(raw)
    except ValueError:
        return _UNREADABLE
    return loaded if isinstance(loaded, dict) else _UNREADABLE


def _carries(client: cn.Client, body: dict, key: str) -> bool:
    """Whether this copy's container holds an entry under `key`."""
    servers = body.get(client.container)
    return isinstance(servers, dict) and key in servers


def _pinned_root(entry) -> str:
    """The project root one settings entry pins, "" when it pins none."""
    if not isinstance(entry, dict):
        return ""
    entry_env = entry.get("env")
    if not isinstance(entry_env, dict):
        return ""
    pinned = entry_env.get(ROOT_ENV)
    return pinned if isinstance(pinned, str) else ""


def _stands(root: str) -> bool:
    try:
        return Path(root).exists()
    except (OSError, ValueError):  # pragma: no cover - a path this OS rejects
        return False


# --- the verb ---------------------------------------------------------

def remove_plan(client_name: str, key: str, *, env=None) -> OrphanPlan:
    """What removing this entry would do. Writes nothing.

    Every check runs before any write is composed, so a refusal leaves
    every copy untouched and the two copies of a client can never end a
    run disagreeing. A file error while reading is the connect kit's
    `WriteStopped` with nothing left.
    """
    try:
        return _remove_plan(client_name, key, env=env)
    except OSError as exc:
        raise cn._stopped(exc) from exc


def _remove_plan(client_name: str, key: str, *, env) -> OrphanPlan:
    client = cn.client_for(client_name)
    if not key.startswith(client.key_prefix):
        raise OrphanRefused(report.orphan_outside_prefix(client))

    copies = cn.detect(client, env=env)
    if not copies:
        raise cn.ConnectError(report.CONNECT_NO_CLIENT)

    read = [(copy, _load(copy.path)) for copy in copies]
    pinned = [
        _pinned_root(body[client.container][key])
        for _, body in read
        if isinstance(body, dict) and _carries(client, body, key)
    ]
    if not pinned:
        return OrphanPlan(
            client=client, key=key, found=False, dispositions=(), writes=(),
        )

    for root in pinned:
        if root and _stands(root):
            raise OrphanRefused(report.orphan_root_alive(client, root))
    if not all(pinned):
        raise cn.ConnectError(report.ORPHAN_NO_ROOT)

    dispositions: list[mnt.Disposition] = []
    writes: dict[Path, bytes] = {}
    for copy, body in read:
        shown = copy.path.as_posix()
        if body is _ABSENT:
            dispositions.append(
                mnt.Disposition(shown, mnt.ABSENT, mnt.ABSENT_ALREADY_GONE))
            continue
        if body is _UNREADABLE:
            dispositions.append(
                mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_UNPARSED_LEFT))
            continue
        if not _carries(client, body, key):
            dispositions.append(
                mnt.Disposition(shown, mnt.LEFT, mnt.CONNECT_KEY_GONE))
            continue
        # An emptied container object stays: this kit did not create it.
        del body[client.container][key]
        writes[copy.path] = (
            json.dumps(body, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        dispositions.append(mnt.Disposition(
            shown, mnt.EXCISED, ORPHAN_EXCISED, f"{client.container}.{key}"))

    dispositions.sort(key=lambda d: d.path)
    return OrphanPlan(
        client=client, key=key, found=True,
        dispositions=tuple(dispositions),
        writes=tuple(sorted(writes.items())),
    )


def remove(client_name: str, key: str, *, env=None) -> OrphanPlan:
    """Remove the named entry, then report what was left and why.

    The write goes through the connect kit's own all-or-nothing writer,
    so a failure part way across a client's copies puts every one of them
    back the way it was.
    """
    plan = remove_plan(client_name, key, env=env)
    if not plan.found:
        return plan
    cn._restore_copies(dict(plan.writes), ())
    return plan
