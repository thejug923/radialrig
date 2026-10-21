"""The mount routine: lay the seed onto a host project, and nothing else.

PLAN-0005 Phases 2 and 3. The seed is the exact file set a mount writes:
SEED_FILES below is that list, and it is the only list. `radialrig mount`,
`radialrig unmount` and `radialrig upgrade` are thin surfaces over the
functions here; Phase 4's local web page drives the same functions, so
neither surface holds behavior the other cannot reach (D-34 point 3).
That is why none of this lives in cli.py.

Four host files are merged rather than created — `AGENTS.md` gains a
fenced section, `CLAUDE.md` gains a fenced import of it (Claude Code
reads CLAUDE.md and not AGENTS.md), `.mcp.json` gains two server keys,
`.codex/config.toml` gains a fenced section holding the same two servers
for Codex (PLAN-0013) — and existing content is never overwritten. The mount registers the retrieval server and the
authoring server side by side (D-18 constraint 6, owner ruling
2026-09-04), so capture is on from the first session. The authoring
server stops at the working tree; it comes out when the owner removes
its one entry, and the seeded route map says so.

One invariant carries the whole reversal story: **a mount only ever
inserts.** Every byte a mount adds to a file the host already had lives
in one contiguous region, so unmount deletes exactly that region and the
file is the host's again, byte for byte. No field of the manifest ever
holds a byte the host wrote — a `.mcp.json` routinely carries secrets in
`env`, and the manifest is a file projects commit.

Every write is `write_bytes` on UTF-8 with LF endings. Text mode would
emit CRLF on Windows, and the manifest hash of a Windows mount would then
differ from a Linux mount of the same package version.
"""

from __future__ import annotations

import copy
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

from radialrig.paths import packaged_seed

MANIFEST_REL = ".radialrig/mount.json"
MANIFEST_SCHEMA = 2
DEFAULT_OWNER = "project-owner"
SERVER_KEY = "radialrig"
SERVER_COMMAND = "radialrig-read"
# Registered beside the read server by every mount (D-18 constraint 6).
# The seeded route map names the command too, so an owner who removes
# the entry and later wants it back registers the command that ships.
WRITE_SERVER_KEY = "radialrig-write"
WRITE_SERVER_COMMAND = "radialrig-write"
HASH_NAME = "sha256"

MCP_REL = ".mcp.json"
AGENTS_REL = "AGENTS.md"
# Claude Code loads CLAUDE.md and not AGENTS.md; the mount appends one
# fenced region importing the route map (PLAN-0013 phase 3).
CLAUDE_REL = "CLAUDE.md"
# Codex reads its project servers from this file, not from .mcp.json;
# the mount appends one fenced region holding the same two servers
# (PLAN-0013 phase 1).
CODEX_REL = ".codex/config.toml"
SPEC_REL = "docs/spec.md"
SEED_SECTION_SOURCE = "AGENTS.section.md"
CLAUDE_SECTION_SOURCE = "CLAUDE.section.md"
CODEX_SECTION_SOURCE = "codex.config.section.toml"
# The fenced section and the seeded rulebook cite each other, so an
# upgrade moves both or neither (BUG-0004).
UPGRADE_PAIR: tuple[str, str] = (AGENTS_REL, SPEC_REL)

FENCE_BEGIN = "<!-- radialrig:begin -->"
FENCE_END = "<!-- radialrig:end -->"
CODEX_FENCE_BEGIN = "# radialrig:begin"
CODEX_FENCE_END = "# radialrig:end"
# One fence pair per appended seed file, in the comment syntax of the
# file it fences. The fence machinery below reads this map and never a
# file name, so a further appended file costs one row here.
FENCES: dict[str, tuple[str, str]] = {
    AGENTS_REL: (FENCE_BEGIN, FENCE_END),
    CLAUDE_REL: (FENCE_BEGIN, FENCE_END),
    CODEX_REL: (CODEX_FENCE_BEGIN, CODEX_FENCE_END),
}

TOKEN_RE = re.compile(r"\{\{([A-Z_]+)\}\}")

# The ten closed report verbs. Both surfaces render these and nothing
# else, so a reader learns the vocabulary once.
CREATED = "created"
APPENDED = "appended"
MERGED = "merged"
UPDATED = "updated"
CURRENT = "current"
REMOVED = "removed"
RESTORED = "restored"
EXCISED = "excised"
LEFT = "left"
ABSENT = "absent"

VERBS: tuple[str, ...] = (
    CREATED, APPENDED, MERGED, UPDATED, CURRENT,
    REMOVED, RESTORED, EXCISED, LEFT, ABSENT,
)


class MountError(RuntimeError):
    """Anything that stops a mount."""


class WriteStopped(MountError):
    """A file error stopped a verb, and the tree stands as found unless
    `left` says otherwise (PLAN-0017 phase 2).

    `path` is the file the error names, `reason` the system's words for
    it, and `left` the paths whose original bytes could not be put back:
    empty when every write was undone or none had begun. The text is one
    sentence ending "nothing was written" or "put back except: ...", so
    every surface prints the same true line. Outside this module it is
    caught as the pair `(mnt.WriteStopped, cn.WriteStopped)`.
    """

    def __init__(self, path, reason: str, left=()) -> None:
        self.path = str(path) if path else ""
        self.reason = reason
        self.left = tuple(str(p) for p in left)
        super().__init__(stopped_sentence(self.path, self.reason, self.left))


def stopped_sentence(path: str, reason: str, left) -> str:
    """The one wording of a stop, for both kits' errors."""
    head = f"file error at {path}: {reason}" if path else f"file error: {reason}"
    if left:
        return head + "; put back except: " + ", ".join(left)
    return head + "; nothing was written"


def _relative(root: Path | None, target) -> str:
    """`target` as the record spells it: root-relative posix under a
    root, the path as given elsewhere."""
    if root is not None:
        try:
            return Path(target).relative_to(root).as_posix()
        except ValueError:
            pass
    return str(target)


def _stopped(exc: OSError, *, root: Path | None = None, path=None,
             left=()) -> WriteStopped:
    """The typed error for a file error, named by the file it hit."""
    where = exc.filename if exc.filename else path
    return WriteStopped(
        _relative(root, where) if where else "", exc.strerror or str(exc), left,
    )


class AlreadyMounted(MountError):
    """The host already carries a mount manifest."""

    def __init__(self, root: Path) -> None:
        super().__init__(f"{root} is already mounted ({MANIFEST_REL} present)")
        self.root = root


class NotMounted(MountError):
    """The host carries no mount manifest, and the verb needs one."""

    def __init__(self, root: Path) -> None:
        super().__init__(f"{root} carries no mount manifest at {MANIFEST_REL}")
        self.root = root


class MountConflict(MountError):
    """Targets that block an all-or-nothing write."""

    def __init__(self, conflicts: tuple[str, ...]) -> None:
        super().__init__(
            "mount would overwrite host content: " + ", ".join(conflicts)
        )
        self.conflicts = conflicts


@dataclass(frozen=True)
class SeedEntry:
    """One line of the seed table: where it lands, how, and from what."""

    rel: str
    mode: str
    source: str | None = None  # relative to the packaged seed directory


# The seed, as an explicit list. A directory walk over data/seed/ would
# silently ship a stray file dropped there; a test asserts this list and
# that directory agree in both directions.
SEED_FILES: tuple[SeedEntry, ...] = (
    SeedEntry("docs/spec.md", CREATED, "docs/spec.md"),
    SeedEntry("docs/spec-git-environment.md", CREATED, "docs/spec-git-environment.md"),
    SeedEntry("docs/templates/decision.md", CREATED, "docs/templates/decision.md"),
    SeedEntry("docs/templates/plan.md", CREATED, "docs/templates/plan.md"),
    SeedEntry("docs/templates/spec.md", CREATED, "docs/templates/spec.md"),
    SeedEntry("docs/decisions/.gitkeep", CREATED),
    SeedEntry("docs/plans/.gitkeep", CREATED),
    SeedEntry("docs/archive/.gitkeep", CREATED),
    SeedEntry(
        "docs/archive/D-00-archive-exclusion-target.md", CREATED,
        "docs/archive/D-00-archive-exclusion-target.md",
    ),
    SeedEntry(AGENTS_REL, APPENDED, SEED_SECTION_SOURCE),
    SeedEntry(CLAUDE_REL, APPENDED, CLAUDE_SECTION_SOURCE),
    SeedEntry(CODEX_REL, APPENDED, CODEX_SECTION_SOURCE),
    SeedEntry(MCP_REL, MERGED),
)

SEED_PATHS: tuple[str, ...] = tuple(e.rel for e in SEED_FILES)
# Every path a mount writes, the manifest included. Phases 3 and 4 read
# this rather than re-deriving it.
WRITTEN_PATHS: tuple[str, ...] = tuple(sorted((*SEED_PATHS, MANIFEST_REL)))

# What unmount trusts a record to claim (PLAN-0022 phase 2, MNT-003).
# The record is a file in the project, so a cloned project can ship one
# and anyone can edit one. A mount writes only the seed list's (path,
# mode) pairs, and creates only the directories strictly between the
# project root and a seed path or the record, so unmount acts on no
# other entry. The seed list has only grown, so a record from any
# release names nothing outside these.
SEED_PAIRS: frozenset[tuple[str, str]] = frozenset(
    (e.rel, e.mode) for e in SEED_FILES)
MOUNT_DIRS: frozenset[str] = frozenset(
    "/".join(parts[:i])
    for parts in (rel.split("/") for rel in WRITTEN_PATHS)
    for i in range(1, len(parts))
)

# The directories a mount creates on a bare host, deepest last. Only used
# to reverse a schema-1 manifest, which recorded no directory list.
LEGACY_DIRS: tuple[str, ...] = (
    "docs", "docs/archive", "docs/decisions", "docs/plans", "docs/templates",
    ".radialrig",
)


# --- reason codes -----------------------------------------------------
# A closed set. The codes are what tests assert on; the wording lives in
# REASONS so the command line and Phase 4's page cannot drift apart.

MOUNT_CREATED = "MOUNT_CREATED"
MOUNT_APPENDED = "MOUNT_APPENDED"
MOUNT_MERGED = "MOUNT_MERGED"
MOUNT_MERGED_RESERIALIZED = "MOUNT_MERGED_RESERIALIZED"
MOUNT_MERGED_PARTIAL = "MOUNT_MERGED_PARTIAL"
MOUNT_MCP_HOST_OWNED = "MOUNT_MCP_HOST_OWNED"
# An appended file the project already made unnecessary: a CLAUDE.md that
# imports AGENTS.md on its own. Nothing written, nothing recorded.
HOST_HOLDS = "HOST_HOLDS"

REMOVED_FILE = "REMOVED_FILE"
REMOVED_DIR = "REMOVED_DIR"
RESTORED_PREMOUNT = "RESTORED_PREMOUNT"
EXCISED_SECTION = "EXCISED_SECTION"
EXCISED_MCP_REGION = "EXCISED_MCP_REGION"
LEFT_EDITED = "LEFT_EDITED"
LEFT_NOT_A_FILE = "LEFT_NOT_A_FILE"
LEFT_DIR_OCCUPIED = "LEFT_DIR_OCCUPIED"
LEFT_DIR_LOCKED = "LEFT_DIR_LOCKED"
LEFT_SECTION_EDITED = "LEFT_SECTION_EDITED"
LEFT_FENCES_BROKEN = "LEFT_FENCES_BROKEN"
LEFT_MCP_UNPARSED = "LEFT_MCP_UNPARSED"
LEFT_MCP_ENTRY_CHANGED = "LEFT_MCP_ENTRY_CHANGED"
LEFT_MCP_KEY_GONE = "LEFT_MCP_KEY_GONE"
LEFT_MCP_RESERIALIZED = "LEFT_MCP_RESERIALIZED"
LEFT_MCP_AMBIGUOUS = "LEFT_MCP_AMBIGUOUS"
LEFT_MCP_HOST_OWNED = "LEFT_MCP_HOST_OWNED"
# A record claim a mount never makes (PLAN-0022 phase 2): named, and
# nothing at that path changed.
LEFT_NOT_SEEDED = "LEFT_NOT_SEEDED"
LEFT_DIR_NOT_SEEDED = "LEFT_DIR_NOT_SEEDED"
LEFT_MCP_UNTRUSTED = "LEFT_MCP_UNTRUSTED"
LEFT_MCP_NOT_ALL_OURS = "LEFT_MCP_NOT_ALL_OURS"
# A key written twice: re-serializing would keep one of the two
# (PLAN-0022 phase 3).
LEFT_MCP_REPEATED_KEY = "LEFT_MCP_REPEATED_KEY"
ABSENT_ALREADY_GONE = "ABSENT_ALREADY_GONE"

UPGRADE_APPLIED = "UPGRADE_APPLIED"
UPGRADE_CURRENT = "UPGRADE_CURRENT"
UPGRADE_PENDING = "UPGRADE_PENDING"
UPGRADE_ABSENT = "UPGRADE_ABSENT"
UPGRADE_NEW_WRITTEN = "UPGRADE_NEW_WRITTEN"
UPGRADE_NEW_LEFT = "UPGRADE_NEW_LEFT"
UPGRADE_NEW_SECTION_LEFT = "UPGRADE_NEW_SECTION_LEFT"
UPGRADE_SECTION_APPLIED = "UPGRADE_SECTION_APPLIED"
UPGRADE_SECTION_CURRENT = "UPGRADE_SECTION_CURRENT"
UPGRADE_SECTION_PENDING = "UPGRADE_SECTION_PENDING"
UPGRADE_SECTION_UNREADABLE = "UPGRADE_SECTION_UNREADABLE"
UPGRADE_SECTION_REFUSED = "UPGRADE_SECTION_REFUSED"
UPGRADE_HELD = "UPGRADE_HELD"
UPGRADE_ENTRY_APPLIED = "UPGRADE_ENTRY_APPLIED"
UPGRADE_ENTRY_CURRENT = "UPGRADE_ENTRY_CURRENT"
UPGRADE_ENTRY_PENDING = "UPGRADE_ENTRY_PENDING"
UPGRADE_ENTRY_ABSENT = "UPGRADE_ENTRY_ABSENT"
UPGRADE_ENTRY_REMOVED = "UPGRADE_ENTRY_REMOVED"
UPGRADE_ENTRY_NEW_WRITTEN = "UPGRADE_ENTRY_NEW_WRITTEN"
UPGRADE_ENTRY_NEW_LEFT = "UPGRADE_ENTRY_NEW_LEFT"
UPGRADE_ENTRY_NEW_PENDING = "UPGRADE_ENTRY_NEW_PENDING"
UPGRADE_ENTRY_HELD_REMOVED = "UPGRADE_ENTRY_HELD_REMOVED"
UPGRADE_MCP_UNREADABLE = "UPGRADE_MCP_UNREADABLE"
UPGRADE_MCP_HOST_OWNED = "UPGRADE_MCP_HOST_OWNED"
UPGRADE_MCP_DEGRADED = "UPGRADE_MCP_DEGRADED"

# --- skills kit codes (D-36) ------------------------------------------
# The skills kit is a separate lifecycle with a separate record; nothing
# here couples the two. What is shared is the vocabulary: one closed set
# of codes and one resolver, `reason_text`, so the command line and the
# setup page cannot word the same outcome two ways. The dependency runs
# one way only — skills.py imports this module, never the reverse.

SKILL_ADDED = "SKILL_ADDED"
SKILL_REFRESHED = "SKILL_REFRESHED"
SKILL_DROPPED = "SKILL_DROPPED"
SKILL_CURRENT = "SKILL_CURRENT"
SKILL_REFRESHABLE = "SKILL_REFRESHABLE"
SKILL_EDITED = "SKILL_EDITED"
SKILL_EDITED_KEPT = "SKILL_EDITED_KEPT"
SKILL_UNRECORDED = "SKILL_UNRECORDED"
SKILL_DIR_FOREIGN = "SKILL_DIR_FOREIGN"
SKILL_FILE_FOREIGN = "SKILL_FILE_FOREIGN"
SKILL_TARGET_NOT_A_DIR = "SKILL_TARGET_NOT_A_DIR"
SKILL_DIR_SHARED = "SKILL_DIR_SHARED"
SKILL_NOT_INSTALLED = "SKILL_NOT_INSTALLED"
SKILL_RECORD_STALE = "SKILL_RECORD_STALE"
SKILL_NOT_PACKAGED = "SKILL_NOT_PACKAGED"
SKILL_RECORD_WRITTEN = "SKILL_RECORD_WRITTEN"
SKILL_RECORD_DROPPED = "SKILL_RECORD_DROPPED"

# --- connect kit codes (PLAN-0007 Phase 2) ----------------------------
# The connect kit registers this project with an MCP client's own
# settings file. Separate lifecycle, separate record; nothing here
# couples the two. What is shared is the vocabulary: one closed set of
# codes and one resolver, so the command line and the setup page cannot
# word the same outcome two ways. The dependency runs one way only —
# connect.py imports this module, never the reverse.

CONNECT_CREATED = "CONNECT_CREATED"
CONNECT_MERGED = "CONNECT_MERGED"
CONNECT_MERGED_RESERIALIZED = "CONNECT_MERGED_RESERIALIZED"
CONNECT_CURRENT = "CONNECT_CURRENT"
CONNECT_ENTRY_STALE = "CONNECT_ENTRY_STALE"
CONNECT_KEY_HOST_OWNED = "CONNECT_KEY_HOST_OWNED"
CONNECT_UNPARSED = "CONNECT_UNPARSED"
CONNECT_RECORD_OTHER_KEY = "CONNECT_RECORD_OTHER_KEY"
CONNECT_RECORD_WRITTEN = "CONNECT_RECORD_WRITTEN"

CONNECT_RESTORED = "CONNECT_RESTORED"
CONNECT_EXCISED_REGION = "CONNECT_EXCISED_REGION"
CONNECT_FILE_EDITED = "CONNECT_FILE_EDITED"
CONNECT_LEFT_HOST_OWNED = "CONNECT_LEFT_HOST_OWNED"
CONNECT_KEY_GONE = "CONNECT_KEY_GONE"
CONNECT_ENTRY_CHANGED = "CONNECT_ENTRY_CHANGED"
CONNECT_UNPARSED_LEFT = "CONNECT_UNPARSED_LEFT"
CONNECT_AMBIGUOUS = "CONNECT_AMBIGUOUS"
CONNECT_RESERIALIZED = "CONNECT_RESERIALIZED"
CONNECT_RECORD_DROPPED = "CONNECT_RECORD_DROPPED"
CONNECT_RECORD_UNPROVEN = "CONNECT_RECORD_UNPROVEN"

# --- adopt kit codes (BUG-0001) ---------------------------------------
# The adopt flow rebuilds a lost mount record from standing files. Same
# arrangement as the kits above: the vocabulary lives here, the logic
# lives in adopt.py, and the dependency runs one way only — adopt.py
# imports this module, never the reverse.

ADOPT_MATCHED = "ADOPT_MATCHED"
ADOPT_EDITED = "ADOPT_EDITED"
ADOPT_SECTION_MATCHED = "ADOPT_SECTION_MATCHED"
ADOPT_SECTION_EDITED = "ADOPT_SECTION_EDITED"
ADOPT_SECTION_UNPROVEN = "ADOPT_SECTION_UNPROVEN"
ADOPT_DATE_UNPROVEN = "ADOPT_DATE_UNPROVEN"
ADOPT_EMPTY_UNPROVEN = "ADOPT_EMPTY_UNPROVEN"
ADOPT_NO_FENCE = "ADOPT_NO_FENCE"
ADOPT_FENCES_BROKEN = "ADOPT_FENCES_BROKEN"
ADOPT_MCP_SEED_ENTRY = "ADOPT_MCP_SEED_ENTRY"
ADOPT_MCP_UNPROVEN = "ADOPT_MCP_UNPROVEN"
ADOPT_MCP_PROJECT = "ADOPT_MCP_PROJECT"
ADOPT_MCP_NO_KEY = "ADOPT_MCP_NO_KEY"
ADOPT_MCP_UNPARSED = "ADOPT_MCP_UNPARSED"
ADOPT_ABSENT = "ADOPT_ABSENT"
ADOPT_RECORD_WRITTEN = "ADOPT_RECORD_WRITTEN"

ADOPT_CODES: tuple[str, ...] = (
    ADOPT_MATCHED, ADOPT_EDITED, ADOPT_SECTION_MATCHED, ADOPT_SECTION_EDITED,
    ADOPT_SECTION_UNPROVEN, ADOPT_DATE_UNPROVEN, ADOPT_EMPTY_UNPROVEN,
    ADOPT_NO_FENCE, ADOPT_FENCES_BROKEN, ADOPT_MCP_SEED_ENTRY,
    ADOPT_MCP_UNPROVEN, ADOPT_MCP_PROJECT, ADOPT_MCP_NO_KEY,
    ADOPT_MCP_UNPARSED, ADOPT_ABSENT, ADOPT_RECORD_WRITTEN,
)

# Every CONNECT_* code above, so a test can assert each one has a
# wording. A code missing from REASONS renders as the bare code.
CONNECT_CODES: tuple[str, ...] = (
    CONNECT_CREATED, CONNECT_MERGED, CONNECT_MERGED_RESERIALIZED,
    CONNECT_CURRENT, CONNECT_ENTRY_STALE, CONNECT_KEY_HOST_OWNED,
    CONNECT_UNPARSED, CONNECT_RECORD_OTHER_KEY, CONNECT_RECORD_WRITTEN,
    CONNECT_RESTORED, CONNECT_EXCISED_REGION, CONNECT_FILE_EDITED,
    CONNECT_LEFT_HOST_OWNED, CONNECT_KEY_GONE, CONNECT_ENTRY_CHANGED,
    CONNECT_UNPARSED_LEFT, CONNECT_AMBIGUOUS, CONNECT_RESERIALIZED,
    CONNECT_RECORD_DROPPED, CONNECT_RECORD_UNPROVEN,
)

REASONS: dict[str, str] = {
    MOUNT_CREATED: "",
    MOUNT_APPENDED: "one fenced section at the end; the rest of the file is unchanged",
    MOUNT_MERGED: "added {detail}; no other byte changed",
    MOUNT_MERGED_RESERIALIZED:
        "added {detail}; the file was re-serialized at indent 2",
    MOUNT_MERGED_PARTIAL:
        "added {detail}; the other key was already registered here and stays "
        "this project's own; no other byte changed",
    MOUNT_MCP_HOST_OWNED: "this project already registered both keys; no byte changed",
    HOST_HOLDS:
        "this project already does what the seeded section would; not "
        "touched, and not recorded",

    REMOVED_FILE: "",
    REMOVED_DIR: "empty",
    RESTORED_PREMOUNT: "to its bytes from before the mount",
    EXCISED_SECTION: "fenced section removed; the rest of the file stays",
    EXCISED_MCP_REGION:
        "merged entry removed; the rest of the file stays",
    LEFT_EDITED: "changed since the mount",
    LEFT_NOT_A_FILE: "a directory now stands at this path",
    LEFT_DIR_OCCUPIED: "holds content this project added",
    LEFT_DIR_LOCKED: "the directory could not be removed",
    LEFT_SECTION_EDITED: "the fenced section was changed; removed nothing",
    LEFT_FENCES_BROKEN:
        "the radialrig fence markers are missing or unbalanced; removed nothing",
    LEFT_MCP_UNPARSED: f"{MCP_REL} no longer parses as JSON; removed nothing",
    LEFT_MCP_ENTRY_CHANGED: "a registered entry was changed; removed nothing",
    LEFT_MCP_KEY_GONE: "every registered entry is already gone",
    LEFT_MCP_RESERIALIZED:
        "changed since the mount; removed {detail} only",
    LEFT_MCP_AMBIGUOUS: "the registered entry appears more than once; removed nothing",
    LEFT_MCP_HOST_OWNED:
        "the key is recorded as this project's own; removed nothing",
    LEFT_NOT_SEEDED:
        "the mount record claims this file, but a mount never writes it "
        "this way; not touched",
    LEFT_DIR_NOT_SEEDED:
        "the mount record claims this directory, but a mount never "
        "creates it; not touched",
    LEFT_MCP_UNTRUSTED:
        "the mount record claims {detail}; a mount never writes that, so "
        "nothing was removed",
    LEFT_MCP_NOT_ALL_OURS:
        "the mount record claims the whole file, but the file holds "
        "{detail}; a mount never writes that, so nothing was removed",
    LEFT_MCP_REPEATED_KEY:
        f"{MCP_REL} names a key twice, and taking an entry out would drop "
        "one of the two; removed nothing",
    ABSENT_ALREADY_GONE: "already gone",

    UPGRADE_APPLIED: "matched the mounted seed; now at the installed seed",
    UPGRADE_CURRENT: "already matches the installed seed",
    UPGRADE_PENDING: "changed in this project; the diff follows",
    UPGRADE_ABSENT:
        "removed from this project; upgrade does not restore a removed file",
    UPGRADE_NEW_WRITTEN:
        "this radialrig is the first to ship it; written, and the record "
        "now carries it",
    UPGRADE_NEW_LEFT:
        "this radialrig ships this path for the first time and this project "
        "already has a file there; not touched, and not recorded",
    UPGRADE_NEW_SECTION_LEFT:
        "this radialrig ships this section for the first time and this "
        "project's file cannot take it: it carries the fence already, does "
        "not parse, or defines what the section defines; not touched, and "
        "not recorded",
    UPGRADE_SECTION_APPLIED:
        "the fenced section matched the mounted seed; now at the installed seed",
    UPGRADE_SECTION_CURRENT: "the fenced section already matches the installed seed",
    UPGRADE_SECTION_PENDING:
        "the fenced section changed in this project; the diff follows",
    UPGRADE_SECTION_UNREADABLE:
        "the fenced section could not be located; nothing written",
    UPGRADE_SECTION_REFUSED:
        "the file outside the fence no longer takes the seeded section: it "
        "does not parse, or a key after the end fence fell into a seeded "
        "table; not touched",
    UPGRADE_HELD:
        "unchanged here, but {detail} changed in this project and the two "
        "upgrade together; waits for that merge; the diff follows",
    UPGRADE_ENTRY_APPLIED: "the registered entry now matches the installed seed",
    UPGRADE_ENTRY_CURRENT: "the registered entry is unchanged",
    UPGRADE_ENTRY_PENDING:
        "the registered entry changed in this project; the diff follows",
    UPGRADE_ENTRY_ABSENT:
        "every registered entry is gone; upgrade does not restore a removed entry",
    UPGRADE_ENTRY_REMOVED:
        "{detail} removed by this project, and upgrade does not restore a "
        "removed entry; the rest is unchanged",
    UPGRADE_ENTRY_NEW_WRITTEN:
        "this radialrig is the first to register {detail}; added beside the "
        "entry already here, and the record now carries it",
    UPGRADE_ENTRY_NEW_LEFT:
        "this radialrig registers {detail} for the first time and this "
        "project already has an entry under that key; not touched, and not "
        "recorded",
    UPGRADE_ENTRY_NEW_PENDING:
        "this radialrig registers {detail} for the first time, and the file "
        "changed in this project in a way the record cannot follow; not "
        "touched; the entry to add follows",
    UPGRADE_ENTRY_HELD_REMOVED:
        "{detail} stood here when this project mounted and this project "
        "removed it since; upgrade does not restore a removed entry; "
        "nothing written, nothing recorded",
    UPGRADE_MCP_UNREADABLE: f"{MCP_REL} no longer parses as JSON; nothing written",
    UPGRADE_MCP_HOST_OWNED:
        "the key is recorded as this project's own; nothing written",
    UPGRADE_MCP_DEGRADED:
        "this mount record carries no restore fields; nothing written",

    SKILL_ADDED: "",
    SKILL_REFRESHED: "matched the record; now at the packaged version",
    SKILL_DROPPED: "matched the record; this package no longer ships it",
    SKILL_CURRENT: "{detail} already matches this package",
    SKILL_REFRESHABLE: "{detail} installed; this package ships a newer release",
    SKILL_EDITED: "changed in this project; nothing was written",
    SKILL_EDITED_KEPT: "changed in this project; removed nothing",
    SKILL_UNRECORDED: "this project added it; removed nothing",
    SKILL_DIR_FOREIGN: "this project already has a directory at this path",
    SKILL_FILE_FOREIGN: "this project already has a file at this path; nothing was written",
    SKILL_TARGET_NOT_A_DIR: "a file stands where the skill directory goes",
    SKILL_DIR_SHARED: "holds another skill this package installed",
    SKILL_NOT_INSTALLED: "{detail} available in this package; not installed here",
    SKILL_RECORD_STALE: "the record names it; nothing is on disk",
    SKILL_NOT_PACKAGED: "the record names it; this package no longer ships it",
    SKILL_RECORD_WRITTEN: "",
    SKILL_RECORD_DROPPED: "the last skill this package installed is gone",

    CONNECT_CREATED: "this client had no settings file here; it now holds one entry",
    CONNECT_MERGED: "added {detail}; no other byte changed",
    CONNECT_MERGED_RESERIALIZED:
        "added {detail}; the file was re-serialized at indent 2",
    CONNECT_CURRENT: "this entry is already registered; no byte changed",
    CONNECT_ENTRY_STALE:
        "this key holds an earlier radialrig entry; nothing was written",
    CONNECT_KEY_HOST_OWNED:
        "this key is already taken by another entry; nothing was written",
    CONNECT_UNPARSED:
        "this settings file does not parse as a JSON object; nothing was written",
    CONNECT_RECORD_OTHER_KEY:
        "this project is registered under another name; nothing was written",
    CONNECT_RECORD_WRITTEN: "",

    CONNECT_RESTORED: "to its bytes from before the connect",
    CONNECT_EXCISED_REGION:
        "registered entry removed; the rest of the file changed and stays",
    CONNECT_FILE_EDITED: "changed since the connect",
    CONNECT_LEFT_HOST_OWNED:
        "this entry was present before the connect; removed nothing",
    CONNECT_KEY_GONE: "the registered entry is already gone",
    CONNECT_ENTRY_CHANGED: "the registered entry was changed; removed nothing",
    CONNECT_UNPARSED_LEFT:
        "this settings file no longer parses as JSON; removed nothing",
    CONNECT_AMBIGUOUS:
        "the registered entry appears more than once; removed nothing",
    CONNECT_RESERIALIZED: "changed since the connect; removed {detail} only",
    CONNECT_RECORD_DROPPED: "the connect record is removed",
    CONNECT_RECORD_UNPROVEN:
        "the record names no entry this kit registered in this file; removed nothing",

    ADOPT_MATCHED: "matches the installed seed; recorded as the mount's own",
    ADOPT_EDITED: "differs from the installed seed; recorded as this project's own",
    ADOPT_SECTION_MATCHED:
        "the fenced section matches the installed seed; recorded as the mount's own",
    ADOPT_SECTION_EDITED:
        "the fenced section differs from the installed seed; "
        "recorded as this project's own",
    ADOPT_SECTION_UNPROVEN:
        "the fenced section matches the installed seed, in a shape no "
        "mount writes; nothing recorded for this file",
    ADOPT_DATE_UNPROVEN:
        "matches a seed dated by its own effective line, and no second "
        "dated file proves that day; nothing recorded for this file",
    ADOPT_EMPTY_UNPROVEN:
        "matches the installed seed only because both are empty, and no "
        "other path proves a mount; nothing recorded for this file",
    ADOPT_NO_FENCE: "no radialrig fenced section here; nothing recorded for this file",
    ADOPT_FENCES_BROKEN:
        "the radialrig fence markers are unbalanced; nothing recorded for this file",
    ADOPT_MCP_SEED_ENTRY:
        "the registered entry is the seed's own; recorded as the mount's own",
    ADOPT_MCP_UNPROVEN:
        "the registered entry is the seed's, in a file with other content "
        "the hash cannot prove; recorded as this project's own",
    ADOPT_MCP_PROJECT:
        "the registered entry is not the seed's; recorded as this project's own",
    ADOPT_MCP_NO_KEY:
        f"no mcpServers.{SERVER_KEY} entry here; nothing recorded for this file",
    ADOPT_MCP_UNPARSED:
        f"{MCP_REL} does not parse as a JSON object; nothing recorded for this file",
    ADOPT_ABSENT: "nothing stands here; nothing recorded for this file",
    ADOPT_RECORD_WRITTEN: "the rebuilt mount record",
}

def _excised_paths() -> str:
    """The seed paths a record without restore fields reverses by
    excision: every merged and appended one, read off the seed table."""
    rels = [e.rel for e in SEED_FILES if e.mode in (APPENDED, MERGED)]
    return f"{', '.join(rels[:-1])} and {rels[-1]}" if len(rels) > 1 else rels[0]


DEGRADED_NOTE = (
    f"note: this mount record carries no restore fields; {_excised_paths()} "
    "are reversed by removing what the mount added, not by restoring "
    "their earlier bytes."
)

RECORD_DIRS: tuple[str, ...] = ("docs/decisions", "docs/plans", "docs/archive")

ORPHANED_RECORDS_NOTE = (
    "Note: docs/spec.md was part of the mount and was removed; the records "
    "this project added under {dirs} no longer have their rulebook beside "
    "them. Re-mount, or copy a rulebook into docs/spec.md."
)


@dataclass(frozen=True)
class Disposition:
    """One report line, before any surface renders it."""

    path: str          # root-relative posix; a directory ends with "/"
    verb: str          # one of the ten closed verbs
    code: str          # machine reason, e.g. LEFT_EDITED
    detail: str = ""   # rendered numbers, e.g. "3"


def reason_text(d: Disposition) -> str:
    """The one wording for a disposition. Both surfaces call this.

    A wording that names `{detail}` is filled from the disposition's
    detail field, which is how a version number rides into a line
    without a second renderer. No wording written before this rule
    existed carries a brace, so every one of them resolves as it did.
    """
    if d.code == LEFT_DIR_OCCUPIED:
        count = int(d.detail or "0")
        if count:
            return f"holds {count} file(s) this project added"
        return REASONS[LEFT_DIR_OCCUPIED]
    text = REASONS.get(d.code, d.code)
    if "{detail}" in text:
        return text.format(detail=d.detail)
    return text


@dataclass(frozen=True)
class WrittenFile:
    path: str  # root-relative posix
    mode: str  # CREATED | APPENDED | MERGED
    sha256: str
    file_created: bool
    section_sha256: str | None = None
    keys: tuple[str, ...] = ()
    pre_mount_sha256: str | None = None
    newline_added: bool = False
    written: bool = True
    insert: str | None = None
    entry: dict | None = None
    reserialized: bool = False
    # Every entry the merge wrote, by its short key. `entry` above keeps
    # carrying the read server's own entry, as every record before the
    # write server was registered did, so an older reader of the record
    # still finds what it expects.
    entries: dict | None = None
    # The seed keys already standing when the mount judged the file, so
    # a record that wrote nothing still says it looked: a `written: false`
    # record carrying `entries` was judged by a build that knew every
    # seed key, and upgrade reads a key absent from its file as the
    # owner's removal (mount-01). Read by nothing as a discriminator.
    held: tuple[str, ...] | None = None

    def as_json(self) -> dict:
        out: dict = {
            "path": self.path,
            "mode": self.mode,
            "sha256": self.sha256,
            "file_created": self.file_created,
        }
        if self.section_sha256 is not None:
            out["section_sha256"] = self.section_sha256
        if self.pre_mount_sha256 is not None:
            out["pre_mount_sha256"] = self.pre_mount_sha256
        if self.mode == APPENDED:
            out["newline_added"] = self.newline_added
        if self.mode == MERGED:
            out["keys"] = list(self.keys)
            out["written"] = self.written
            if self.insert is not None:
                out["insert"] = self.insert
            if self.reserialized:
                out["reserialized"] = True
            if self.entry is not None:
                out["entry"] = self.entry
            if self.entries is not None:
                out["entries"] = self.entries
            if self.held is not None:
                out["held"] = list(self.held)
        return out

    def disposition(self) -> Disposition:
        if self.mode == CREATED:
            return Disposition(self.path, CREATED, MOUNT_CREATED)
        if self.mode == APPENDED:
            if self.file_created:
                return Disposition(self.path, CREATED, MOUNT_CREATED)
            return Disposition(self.path, APPENDED, MOUNT_APPENDED)
        if self.file_created:
            return Disposition(self.path, CREATED, MOUNT_CREATED)
        if not self.written:
            return Disposition(self.path, CURRENT, MOUNT_MCP_HOST_OWNED)
        added = ", ".join(self.keys)
        if self.reserialized:
            return Disposition(self.path, MERGED, MOUNT_MERGED_RESERIALIZED, added)
        if len(self.keys) < len(_mcp_entries()):
            return Disposition(self.path, MERGED, MOUNT_MERGED_PARTIAL, added)
        return Disposition(self.path, MERGED, MOUNT_MERGED, added)


@dataclass(frozen=True)
class MountResult:
    root: Path
    scope: str
    owner: str
    mounted: str  # YYYY-MM-DD
    version: str
    files: tuple[WrittenFile, ...]
    dirs: tuple[str, ...] = ()
    # Seed paths the mount neither wrote nor recorded, with the reason:
    # a file the project already made unnecessary (HOST_HOLDS). Reported
    # so the answer names every seed path; absent from the record so
    # unmount never touches them.
    held: tuple[Disposition, ...] = ()

    def dispositions(self) -> tuple[Disposition, ...]:
        out = [f.disposition() for f in self.files]
        out.extend(self.held)
        out.append(Disposition(MANIFEST_REL, CREATED, MOUNT_CREATED))
        return tuple(sorted(out, key=lambda d: d.path))


@dataclass(frozen=True)
class MountSurvey:
    """What a mount would do here. Writes nothing; Phases 3 and 4 render it."""

    mounted: bool
    conflicts: tuple[str, ...] = ()
    merges: tuple[str, ...] = ()  # existing host files a mount would edit


@dataclass(frozen=True)
class UnmountPlan:
    root: Path
    manifest: dict
    dispositions: tuple[Disposition, ...]
    complete: bool                # nothing left behind
    degraded: bool                # schema-1 manifest
    writes: tuple[tuple[str, bytes], ...] = ()
    removals: tuple[str, ...] = ()
    orphaned: tuple[str, ...] = ()  # record directories left occupied

    @property
    def removed(self) -> int:
        return sum(1 for d in self.dispositions if d.verb in (REMOVED, RESTORED))

    @property
    def left(self) -> int:
        return sum(1 for d in self.dispositions if d.verb in (LEFT, EXCISED))


@dataclass(frozen=True)
class UpgradeItem:
    path: str
    verb: str                     # updated | current | left | absent
    code: str
    diff: str = ""                # unified diff, empty unless there is one
    detail: str = ""              # fills a wording's {detail}, e.g. a partner path

    def disposition(self) -> Disposition:
        return Disposition(self.path, self.verb, self.code, self.detail)


@dataclass(frozen=True)
class UpgradePlan:
    root: Path
    manifest: dict
    from_version: str
    to_version: str
    items: tuple[UpgradeItem, ...]
    pending: int
    writes: tuple[tuple[str, bytes], ...] = ()
    manifest_after: dict = field(default_factory=dict)
    degraded: bool = False

    @property
    def applied(self) -> int:
        # A seed path the record never carried is written under CREATED,
        # or APPENDED onto a file the project already had; it is as
        # applied as an update, and the summary line counts it.
        return sum(1 for i in self.items if i.verb in (UPDATED, CREATED, APPENDED))

    def dispositions(self) -> tuple[Disposition, ...]:
        return tuple(i.disposition() for i in self.items)


# --- helpers ----------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lf(data: bytes) -> bytes:
    """LF endings, whatever the checkout or the packaging produced.

    A seed source materialized with CRLF (git autocrlf on Windows) would
    otherwise ship CRLF to a host and fork the manifest hash by platform.
    """
    return data.replace(b"\r\n", b"\n")


def _recorded_form(data: bytes, recorded: object) -> bytes | None:
    """The bytes a record's digest names, when `data` is those bytes: as
    it stands, or with CRLF read as LF. None when it is neither.

    The record holds the sha256 of the LF bytes the mount wrote. A git
    checkout with `core.autocrlf=true` rewrites those files with CRLF
    without changing a word, so a byte-exact comparison read every
    seeded file on such a machine as a user edit (BUG-0011). The rule,
    stated once for every verb that compares: the file is unchanged when
    its bytes as they stand, or with CRLF read as LF, hash to the record.
    A real edit still differs both ways. The LF reading counts only when
    one pass removed every CRLF: a file with `\r\r\n` reads as CRLF after
    the pass and would otherwise match a CRLF record it does not equal.
    The recorded digest stays the LF digest, and a verb that restores
    the recorded bytes writes the form that hashes to them.
    """
    if not isinstance(recorded, str) or not recorded:
        return None
    if _sha256(data) == recorded:
        return data
    normalized = _lf(data)
    if (normalized != data and b"\r\n" not in normalized
            and _sha256(normalized) == recorded):
        return normalized
    return None


def matches_recorded(data: bytes, recorded: object) -> bool:
    """Whether `data` is the bytes a record's digest names (BUG-0011)."""
    return _recorded_form(data, recorded) is not None


def _held_region(data: bytes, region: bytes) -> bytes:
    """`region` as `data` holds it: as recorded, or with its line endings
    flipped when only the flipped form is present.

    The region a merge records is the text it spliced in, in the host's
    own endings; a checkout that rewrote the file's endings (BUG-0011)
    no longer holds it byte for byte, but holds it with every `\n` read
    as `\r\n`, or the reverse. The caller counts, splices and excises
    the form found in the file as it stands, so no byte outside the
    region is rewritten. Comes back as recorded when no form is present.
    """
    if region in data:
        return region
    lf = _lf(region)
    for variant in (lf.replace(b"\n", b"\r\n"), lf):
        if variant in data:
            return variant
    return region


def _with_endings_of(sample: bytes, text: bytes) -> bytes:
    """`text` in the line endings `sample` uses: CRLF when any, else LF."""
    lf = _lf(text)
    return lf.replace(b"\n", b"\r\n") if b"\r\n" in sample else lf


def installed_version() -> str:
    """The version of the installed package.

    The lazy import is load-bearing: tests monkeypatch
    radialrig.__version__ and rely on this reading it at call time.
    """
    from radialrig import __version__

    return __version__


def version_stamp() -> str:
    """`radialrig <version>`: the one stamp every surface shows.

    PLAN-0018 phase 1 (BUG-0013). `--version` prints it, the command
    line's engine line and both pages' masthead carry it, and the two
    servers announce the same version, so a stale mount record and a
    stale engine can be told apart on any of them.
    """
    return f"radialrig {installed_version()}"


def default_scope(root: Path) -> str:
    """The host project's scope: its directory name."""
    return root.resolve().name


def is_mounted(root: Path) -> bool:
    return (root / MANIFEST_REL).is_file()


def read_manifest(root: Path) -> dict:
    """The mount manifest as JSON. Raises MountError when there is none."""
    path = root / MANIFEST_REL
    if not path.is_file():
        raise NotMounted(root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _stopped(exc, root=root) from exc
    except (ValueError, RecursionError) as exc:
        # JSONDecodeError and UnicodeDecodeError are both ValueErrors:
        # JSON is UTF-8, so a record in any other encoding does not parse
        # as JSON either, and every verb answers it with the same refusal
        # rather than a traceback. So do a number past Python's integer
        # limit, a plain ValueError, and text nested past the reader's
        # depth, which it answers with RecursionError (PLAN-0022 phase 3).
        raise MountError(f"{MANIFEST_REL} does not parse as JSON: {exc}") from exc


def _readable_manifest(root: Path) -> dict:
    """The manifest, refusing a schema this build cannot act on."""
    manifest = read_manifest(root)
    if not isinstance(manifest, dict):
        raise MountError(f"{MANIFEST_REL} does not hold a JSON object")
    schema = manifest.get("schema")
    if not isinstance(schema, int):
        raise MountError(f"{MANIFEST_REL} carries no readable schema number")
    if schema > MANIFEST_SCHEMA:
        raise MountError(
            f"this mount record was written by a newer radialrig (schema {schema}); "
            f"install that version, or remove .radialrig/ and the seeded paths yourself"
        )
    rels: list[str] = []
    files = manifest.get("files")
    if isinstance(files, list):
        rels.extend(
            e.get("path") for e in files
            if isinstance(e, dict) and isinstance(e.get("path"), str)
        )
    dirs = manifest.get("dirs")
    if isinstance(dirs, list):
        rels.extend(d for d in dirs if isinstance(d, str))
    _guard_paths(root, rels, record_rel=MANIFEST_REL)
    return manifest


# Line breaks, terminal controls, invisible format characters, and lone
# surrogates, which JSON can carry and no UTF-8 stream can.
_UNSHOWN = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def one_line(text: str) -> str:
    """Text as it may stand in one line of a report or a refusal. A path,
    a key or a name can come from a record a project carries, so a
    character that would start a line of its own, steer the terminal or
    hide itself is shown as its escape (PLAN-0022 phase 3)."""
    return "".join(
        ch.encode("unicode_escape").decode("ascii")
        if unicodedata.category(ch) in _UNSHOWN else ch
        for ch in text)


def _guard_paths(root: Path, rels, *, record_rel: str) -> None:
    """Refuse a record naming a path outside the project root.

    Any on-disk record is host-writable input: an entry such as
    "../outside.txt" would otherwise steer a reversal at a file outside
    the project. `record_rel` names the record in the message, so the
    docs manifest and a kit record each answer for themselves in one
    wording rather than two.
    """
    resolved_root = root.resolve()
    for rel in rels:
        candidate = Path(rel.rstrip("/"))
        escapes = candidate.is_absolute() or bool(candidate.drive)
        if not escapes:
            try:
                (resolved_root / candidate).resolve().relative_to(resolved_root)
            except ValueError:
                escapes = True
        if escapes:
            raise MountError(
                f"{record_rel} names a path outside this project: "
                f"'{one_line(rel)}'; nothing was changed"
            )


def _seed_bytes(source: str) -> bytes:
    base = packaged_seed()
    if base is None:
        raise MountError("this installation ships no seed sources")
    path = base.joinpath(*source.split("/"))
    if not path.is_file():
        raise MountError(f"seed source '{source}' is missing from the package")
    return _lf(path.read_bytes())


def _render(source: str, context: dict[str, str]) -> bytes:
    """Substitute {{TOKEN}} in a seed source. Literal replacement, not a
    template engine: four tokens, each substituted once per occurrence.

    A leftover `{{` after substitution is a build defect, not a host
    problem, so it raises rather than shipping braces to a project.
    """
    text = _seed_bytes(source).decode("utf-8")
    out = TOKEN_RE.sub(lambda m: context.get(m.group(1), m.group(0)), text)
    left = TOKEN_RE.search(out)
    if left or "{{" in out:
        name = left.group(1) if left else "?"
        raise MountError(f"seed source '{source}' left token '{{{{{name}}}}}' unsubstituted")
    return out.encode("utf-8")


def _section_source(rel: str) -> str:
    """The seed source an appended file's region is rendered from."""
    for entry in SEED_FILES:
        if entry.rel == rel and entry.mode == APPENDED and entry.source:
            return entry.source
    raise MountError(f"no appended seed entry for '{rel}'")


def _section_bytes(context: dict[str, str], rel: str = AGENTS_REL) -> bytes:
    """One appended file's fenced block, both fence lines included: the
    route map for `AGENTS.md`, the two server tables for
    `.codex/config.toml`."""
    begin, close = FENCES[rel]
    body = _render(_section_source(rel), context).decode("utf-8").rstrip("\n")
    return f"{begin}\n{body}\n{close}\n".encode("utf-8")


def _load_toml(data: bytes) -> dict:
    """`data` as TOML. Raises ValueError: the decoder's error and
    UnicodeDecodeError are both one.

    The reader is imported here, on first use, not at module import:
    the standard library carries it from 3.11 and `tomli` stands in on
    3.10, and only the Codex file needs it, so no other verb pays for
    the import on every start.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10 carries no tomllib
        import tomli as tomllib  # type: ignore[no-redef]
    return tomllib.loads(data.decode("utf-8"))


def _codex_host_free(original: bytes | None) -> bool:
    """Whether the project's own `.codex/config.toml` can take the seed's
    region: it parses as TOML and defines neither server table. A table
    the project registered itself is the project's own, and a second
    definition would break the file for Codex."""
    if not original:
        return True
    try:
        host = _load_toml(original)
    except ValueError:
        return False
    before = host.get("mcp_servers", {}) if isinstance(host, dict) else None
    return isinstance(before, dict) and not any(k in before for k in _mcp_entries())


def _codex_result_ok(whole: bytes) -> bool:
    """Whether a composed `.codex/config.toml` parses and carries the
    seed's tables as the seed registers them: the read table present and
    exact, the write table exact where present (an owner who switched
    capture off has none). Run before any byte is written, on mount and
    on upgrade: a wrong region must never reach a file Codex reads at
    every start. A key the project added after the end fence lands in
    the write table (TOML scope runs past a comment) and fails here."""
    try:
        merged = _load_toml(whole)
    except ValueError:
        return False
    servers = merged.get("mcp_servers") if isinstance(merged, dict) else None
    if not isinstance(servers, dict) or SERVER_KEY not in servers:
        return False
    return all(servers[k] == v for k, v in _mcp_entries().items() if k in servers)


def _codex_opted_out(region: bytes) -> bool:
    """Whether a Codex region is the seed's with the write server's table
    removed, and nothing else changed: the documented way to switch
    capture off. Judged by what the region defines, not by its bytes, so
    the blank line an owner leaves or takes with the table makes no
    difference. Such a region is still the mount's: unmount takes it out
    and upgrade moves it, keeping the opt-out. The read table is judged
    against the installed seed's entry, so a release that changed the
    read server's command would read an older opted-out region as edited
    (held, never written over), the safe side."""
    try:
        parsed = _load_toml(region)
    except (ValueError, RecursionError):  # nested past the reader's depth
        return False
    return (isinstance(parsed, dict) and set(parsed) == {"mcp_servers"}
            and parsed["mcp_servers"] == {SERVER_KEY: _mcp_entry()})


def _codex_without_write(section: bytes) -> bytes:
    """The seed's Codex region with the write server's table taken out:
    what an upgrade writes over a region the owner opted out of. The
    cut runs from the table's header to the next header or the end
    fence, so a seed line placed after the write table belongs to the
    opt-out and never reaches an opted-out project; the seed keeps its
    comments above the tables for that reason."""
    header = f"[mcp_servers.{WRITE_SERVER_KEY}]".encode("utf-8")
    close = CODEX_FENCE_END.encode("utf-8")
    out: list[bytes] = []
    skipping = False
    for line in section.split(b"\n"):
        if line.strip() == header:
            skipping = True
            continue
        if skipping and (line.startswith(b"[") or line.strip() == close):
            skipping = False
        if not skipping:
            out.append(line)
    return b"\n".join(out)


# What an appended file must satisfy beyond the fence rule, by path.
# `APPEND_HOST_CHECKS`: the project's file (None where it has none) can
# take the region. `APPEND_RESULT_CHECKS`: the composed file came out
# right. `APPEND_OPT_OUTS`: (is this region the seed's opted-out form,
# the seed's region in that form). A path with no row is judged on its
# fence alone, as AGENTS.md always was.
APPEND_HOST_CHECKS = {CODEX_REL: _codex_host_free}
APPEND_RESULT_CHECKS = {CODEX_REL: _codex_result_ok}
APPEND_OPT_OUTS = {CODEX_REL: (_codex_opted_out, _codex_without_write)}


def _opted_out(rel: str, region: bytes) -> bool:
    """Whether `region` is `rel`'s seeded section in its opted-out form."""
    opt = APPEND_OPT_OUTS.get(rel)
    return opt is not None and opt[0](region)


# Claude Code follows an `@path` token anywhere in CLAUDE.md except
# inside a code span or a fenced code block, which its import parsing
# skips. The three patterns read the file the way it does.
_CODE_BLOCK_RE = re.compile(
    rb"^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL)
_CODE_SPAN_RE = re.compile(rb"`[^`\n]*`")
_IMPORT_TOKEN_RE = re.compile(rb"(?:^|(?<=\s))@(\S+)", re.MULTILINE)


def _import_targets(data: bytes) -> list[str]:
    """Every path an `@` import in `data` names, as Claude Code reads it."""
    text = _CODE_SPAN_RE.sub(b"", _CODE_BLOCK_RE.sub(b"", data))
    return [m.group(1).decode("utf-8", "replace")
            for m in _IMPORT_TOKEN_RE.finditer(text)]


def _same_file(a: Path, b: Path) -> bool:
    """Whether two paths are one file on disk, through a link of either
    kind. False for a path that does not exist."""
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def _imports_route_map(root: Path, data: bytes) -> bool:
    """Whether a CLAUDE.md already brings the route map in, so the seeded
    import would be a second import of the same file: an `@` import
    whose path is this project's AGENTS.md, in any spelling Claude Code
    follows (bare, `./`, absolute, `~/`), or the file being AGENTS.md
    itself through a link, where a second write would erase the first.

    Only a path whose last component is exactly `AGENTS.md` counts, so
    `@AGENTS.md.` (a sentence's full stop) is judged the same on every
    platform: not an import, and the mount writes its own."""
    agents = root / AGENTS_REL
    if _same_file(root / CLAUDE_REL, agents):
        return True
    for target in _import_targets(data):
        if Path(target).name != AGENTS_REL:
            continue
        candidate = Path(os.path.expanduser(target))
        if not candidate.is_absolute():
            candidate = root / candidate
        # The same file where both exist, or the same path where the
        # mount is about to create AGENTS.md.
        if _same_file(candidate, agents) or candidate.resolve() == agents.resolve():
            return True
    return False


# An appended file the project may already have made unnecessary, by
# path: `(root, host bytes) -> bool`. True means the mount writes and
# records nothing there and says so (HOST_HOLDS). Checked only on a file
# that carries no fence: a fence is a mount's own mark and stays a
# conflict.
APPEND_SATISFIED = {CLAUDE_REL: _imports_route_map}


def _host_holds(root: Path, rel: str) -> bool:
    """Whether the project's own `rel` already does the seeded section's
    job, so a mount or an upgrade leaves it unwritten and unrecorded."""
    satisfied = APPEND_SATISFIED.get(rel)
    if satisfied is None:
        return False
    target = root / rel
    if not target.is_file():
        return False
    data = target.read_bytes()
    return FENCES[rel][0].encode("utf-8") not in data and satisfied(root, data)


def _mcp_entry() -> dict:
    """The retrieval server's entry."""
    return {"command": SERVER_COMMAND, "args": []}


def _write_mcp_entry() -> dict:
    """The authoring server's entry: the same shape, the other command."""
    return {"command": WRITE_SERVER_COMMAND, "args": []}


def _mcp_entries() -> dict[str, dict]:
    """Every server entry a mount registers, by key, in the order written."""
    return {SERVER_KEY: _mcp_entry(), WRITE_SERVER_KEY: _write_mcp_entry()}


def _recorded_entries(entry: dict) -> dict[str, dict]:
    """The entries a `.mcp.json` record claims to have written, by key.

    A record written since the write server was registered carries them
    under `entries`. An older record wrote the read server alone and
    carried that one entry under `entry`; a record older still carried
    only the key list, and the entry it wrote was the read server's of
    its day, which `_mcp_entry` still renders.
    """
    listed = entry.get("entries")
    if isinstance(listed, dict):
        return {
            key: value for key, value in listed.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
    keys = entry.get("keys")
    if not isinstance(keys, list):
        keys = []
    out: dict[str, dict] = {}
    for name in keys:
        if not isinstance(name, str) or not name.startswith("mcpServers."):
            continue
        short = name[len("mcpServers."):]
        if short == SERVER_KEY:
            recorded = entry.get("entry")
            out[short] = recorded if isinstance(recorded, dict) else _mcp_entry()
        elif short == WRITE_SERVER_KEY:
            out[short] = _write_mcp_entry()
    return out


def _fresh_mcp_bytes(entries: dict[str, dict]) -> bytes:
    """The whole `.mcp.json` a mount writes onto a host that had none."""
    return (json.dumps({"mcpServers": dict(entries)}, indent=2,
                       ensure_ascii=False) + "\n").encode("utf-8")


def _locate_section(data: bytes, rel: str = AGENTS_REL) -> tuple[int, int] | None:
    """(start, end) of the fenced region, or None when the fences are not
    exactly one balanced pair at a line start. `end` covers the newline
    that closes the fence, `\n` or `\r\n` after a checkout rewrote the
    endings (BUG-0011), so the slice is the whole section as it stands
    and reads as what the mount wrote once CRLF is read as LF. `rel`
    picks the fence pair: each appended file has its own (FENCES)."""
    begin, close = (m.encode("utf-8") for m in FENCES[rel])
    if data.count(begin) != 1 or data.count(close) != 1:
        return None
    start = data.index(begin)
    if start and data[start - 1:start] != b"\n":
        return None
    stop = data.index(close)
    if stop < start:
        return None
    end = stop + len(close)
    if data[end:end + 2] == b"\r\n":
        end += 2
    elif data[end:end + 1] == b"\n":
        end += 1
    return start, end


def _separator_length(
    data: bytes, start: int, newline_added: bool, pre_mount: object = None
) -> int:
    """How many bytes of newline the mount put between the host's bytes
    and the fenced region. Counted back from the region rather than
    assumed, so a host file that was empty before the mount reverses
    too.

    The mount writes each separator as one `\n`; a `\r\n` unit exists
    only after a checkout rewrote the file's endings (BUG-0011). The
    `\r` before such a `\n` may still be the host's own last byte, as
    in a host ending in a bare `\r` (review 2026-09-05), so no reading
    that takes a `\r` is trusted on the bytes alone: every reading of
    the units as `\n` or `\r\n` is tried, and the one whose remainder
    hashes to the pre-mount record, as it stands or with CRLF read as
    LF, is the count. With no such reading, only `\n` bytes count, so a
    `\r` the mount cannot prove it wrote stays in the host's file.
    """
    limit = int(newline_added) + 1
    readings: dict[int, int] = {0: 0}  # bytes taken -> units read
    frontier = [(start, 0)]
    while frontier:
        pos, units = frontier.pop()
        if units == limit or pos < 1 or data[pos - 1:pos] != b"\n":
            continue
        for step in (1, 2):
            if step == 2 and (pos < 2 or data[pos - 2:pos] != b"\r\n"):
                continue
            taken = start - pos + step
            if taken not in readings:
                readings[taken] = units + 1
                frontier.append((pos - step, units + 1))
    for taken in sorted(readings):
        if _recorded_form(data[:start - taken], pre_mount) is not None:
            return taken
    plain = 0
    while plain < limit and data[start - plain - 1:start - plain] == b"\n":
        plain += 1
    return plain


def _agents_payload(
    root: Path, context: dict[str, str], rel: str = AGENTS_REL,
) -> tuple[bytes, bytes, bool, str | None, bool]:
    """(whole file as it will be written, the fenced block, file_created,
    the host's pre-mount hash, whether a trailing newline was added).

    One block for both cases. The heading is `##`, so it nests under a
    host's existing H1 and still reads correctly when the mount created
    the file. Two variants would be two texts to keep in step, and
    unmount would have to know which one it wrote. The same composer
    serves every appended seed file: `rel` picks the fence pair, the
    source, and any further check the file must pass (APPEND_CHECKS).
    """
    section = _section_bytes(context, rel)
    host_ok = APPEND_HOST_CHECKS.get(rel)
    result_ok = APPEND_RESULT_CHECKS.get(rel)
    path = root / rel
    if not path.exists():
        if result_ok is not None and not result_ok(section):
            raise MountConflict((rel,))
        return section, section, True, None, False
    original = path.read_bytes()
    if FENCES[rel][0].encode("utf-8") in original:
        raise MountConflict((rel,))
    if host_ok is not None and not host_ok(original):
        raise MountConflict((rel,))
    if not original:
        whole, newline_added = section, False
    else:
        # The original bytes stay verbatim. A file that does not end in a
        # newline gains one, which is a byte-level change to the host's
        # file: unavoidable, recorded, and undone by unmount.
        newline_added = not original.endswith(b"\n")
        prefix = original + b"\n" if newline_added else original
        whole = prefix + b"\n" + section
    if result_ok is not None and not result_ok(whole):
        raise MountConflict((rel,))
    return whole, section, False, _sha256(original), newline_added


def _compose_appended(root: Path, context: dict[str, str], rel: str) -> tuple[bytes, WrittenFile]:
    """One appended seed file as a mount or an upgrade writes it: the
    whole file, and the record that lets unmount take the region back
    out. Raises MountConflict for a file that cannot take the region.
    One composer for both verbs, so the record's fields cannot drift
    between a file mounted in and a file upgraded in."""
    whole, section, created, pre, newline_added = _agents_payload(root, context, rel)
    return whole, WrittenFile(
        path=rel, mode=APPENDED, sha256=_sha256(whole), file_created=created,
        section_sha256=_sha256(section), pre_mount_sha256=pre,
        newline_added=newline_added,
    )


def _survey_context() -> dict[str, str]:
    """A context that renders every appended section the way a mount
    would, for a survey that only asks whether the file takes it."""
    return {
        "SCOPE": "", "OWNER": "", "DATE": "",
        "TOOL_CMD": SERVER_COMMAND, "WRITE_TOOL_CMD": WRITE_SERVER_COMMAND,
    }


# --- the .mcp.json splice ---------------------------------------------
# Mount inserts one contiguous region and records that region's text.
# It never re-serializes the host's file, so unmount reverses the merge
# by deleting exactly those bytes, and the manifest never carries a byte
# the host wrote.

@dataclass(frozen=True)
class McpMerge:
    data: bytes | None  # bytes to write; None means write nothing at all
    file_created: bool
    keys: tuple[str, ...]
    written: bool
    insert: str | None
    entry: dict
    reserialized: bool
    sha256: str
    pre_mount: str | None = None  # hash of the host file before the merge
    entries: dict = field(default_factory=dict)  # what the merge wrote, by key
    held: tuple[str, ...] = ()  # the seed keys already standing at mount


def _match_brace(text: str, open_at: int) -> int:
    """Index of the bracket closing the one at `open_at`."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(open_at, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced brackets")


def _find_member(text: str, open_at: int, close_at: int, name: str) -> int | None:
    """Index of the first non-space byte of `name`'s value, or None."""
    i = open_at + 1
    depth = 0
    in_string = False
    escaped = False
    key_start = -1
    last_key: str | None = None
    while i < close_at:
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                if depth == 0:
                    last_key = text[key_start + 1:i]
            i += 1
            continue
        if ch == '"':
            in_string = True
            escaped = False
            key_start = i
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == ":" and depth == 0 and last_key is not None:
            j = i + 1
            while j < close_at and text[j] in " \t\r\n":
                j += 1
            if last_key == name:
                return j
            last_key = None
            i = j
            continue
        i += 1
    return None


def _line_indent(text: str, index: int) -> str:
    """The whitespace run starting the line that `index` sits on."""
    start = text.rfind("\n", 0, index) + 1
    run: list[str] = []
    for ch in text[start:index]:
        if ch in " \t":
            run.append(ch)
        else:
            break
    return "".join(run)


def _member_indent(text: str, open_at: int, close_at: int) -> str:
    """The whitespace run after the first newline inside an object."""
    newline = text.find("\n", open_at, close_at)
    if newline == -1:
        return ""
    run: list[str] = []
    i = newline + 1
    while i < close_at and text[i] in " \t":
        run.append(text[i])
        i += 1
    return "".join(run)


def _indent_unit(text: str, open_at: int, close_at: int) -> str:
    """One step of this file's indentation, two spaces when unreadable."""
    outer = _line_indent(text, open_at)
    inner = _member_indent(text, open_at, close_at)
    if inner.startswith(outer) and len(inner) > len(outer):
        return inner[len(outer):]
    return "  "


def _mcp_insert_region(
    text: str, entry: dict, *, key: str = SERVER_KEY, container: str = "mcpServers",
) -> tuple[int, str] | None:
    """(offset, region) for one entry. The one-key face of the composer
    below, kept because the connect kit and the tests call it this way."""
    return _mcp_insert_entries(text, {key: entry}, container=container)


def _mcp_insert_entries(
    text: str, entries: dict[str, dict], *, container: str = "mcpServers",
) -> tuple[int, str] | None:
    """(offset, region) for the one insertion a merge makes, or None when
    the file's shape is not one the scanner reads.

    Several entries land as one contiguous region, member after member,
    so the reversal is still the deletion of exactly one region. The
    connect kit registers other keys in the same container and reuses
    this composer rather than growing a second copy of the one rule that
    matters: the region always starts with the eol, is inserted at
    open_brace + 1, and ends with a comma when siblings exist.
    """
    if not entries:
        return None
    i = 0
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text) or text[i] != "{":
        return None
    root_open = i
    try:
        root_close = _match_brace(text, root_open)
    except ValueError:
        return None
    eol = "\r\n" if "\r\n" in text else "\n"
    unit = _indent_unit(text, root_open, root_close)
    members = [
        f'"{key}": {json.dumps(entry, ensure_ascii=False)}'
        for key, entry in entries.items()
    ]

    servers_at = _find_member(text, root_open, root_close, container)
    if servers_at is not None:
        if servers_at >= len(text) or text[servers_at] != "{":
            return None
        try:
            obj_close = _match_brace(text, servers_at)
        except ValueError:
            return None
        outer = _line_indent(text, servers_at)
        empty = not text[servers_at + 1:obj_close].strip()
        inner = outer + unit if empty else (
            _member_indent(text, servers_at, obj_close) or outer + unit
        )
        body = ("," + eol + inner).join(members)
        region = eol + inner + body + (eol + outer if empty else ",")
        return servers_at + 1, region

    outer = _line_indent(text, root_open)
    empty = not text[root_open + 1:root_close].strip()
    inner = outer + unit if empty else (
        _member_indent(text, root_open, root_close) or outer + unit
    )
    body = ("," + eol + inner + unit).join(members)
    block = f'"{container}": {{{eol}{inner}{unit}{body}{eol}{inner}}}'
    region = eol + inner + block + (eol + outer if empty else ",")
    return root_open + 1, region


def _splice_mcp(
    text: str, loaded: dict, entry: dict, *,
    key: str = SERVER_KEY, container: str = "mcpServers",
) -> tuple[str, str] | None:
    """The one-key face of `_splice_mcp_entries`."""
    return _splice_mcp_entries(text, loaded, {key: entry}, container=container)


def _splice_mcp_entries(
    text: str, loaded: dict, entries: dict[str, dict], *,
    container: str = "mcpServers",
) -> tuple[str, str] | None:
    """(new text, the inserted region) when the splice verifies, else None.

    A wrong splice must never reach a host's .mcp.json: that file is what
    makes their toolchain work. So the result is parsed and compared to
    the object the merge promised before any byte is written.
    """
    found = _mcp_insert_entries(text, entries, container=container)
    if found is None:
        return None
    offset, region = found
    if not 0 <= offset <= len(text):
        return None
    spliced = text[:offset] + region + text[offset:]
    try:
        parsed = json.loads(spliced)
    except (json.JSONDecodeError, ValueError):
        return None
    expected = copy.deepcopy(loaded)
    servers = expected.setdefault(container, {})
    if not isinstance(servers, dict):
        return None
    servers.update(entries)
    if parsed != expected:
        return None
    if spliced.count(region) != 1:
        return None
    if spliced[:offset] != text[:offset] or spliced[offset + len(region):] != text[offset:]:
        return None
    return spliced, region


def _mcp_payload(root: Path) -> McpMerge:
    """What the merge does to `.mcp.json` here. Writes nothing.

    Each key is judged on its own. One the host already registered is
    left exactly as it stands and never recorded as the mount's own; the
    others go in as one contiguous region, and no other byte of the file
    is rewritten, so unmount removes nothing it did not add.
    """
    entry = _mcp_entry()
    wanted = _mcp_entries()
    path = root / MCP_REL
    if not path.exists():
        data = _fresh_mcp_bytes(wanted)
        return McpMerge(data=data, file_created=True,
                        keys=tuple(f"mcpServers.{k}" for k in wanted), written=True,
                        insert=None, entry=entry, reserialized=False,
                        sha256=_sha256(data), entries=wanted)

    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        loaded = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise MountConflict((MCP_REL,)) from None
    if not isinstance(loaded, dict):
        raise MountConflict((MCP_REL,))
    servers = loaded.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise MountConflict((MCP_REL,))
    missing = {k: v for k, v in wanted.items() if k not in servers}
    held = tuple(k for k in wanted if k in servers)
    if not missing:
        # Every seed key stands: nothing to write, and `entries` is the
        # empty dict rather than nothing, so the record says this build
        # judged every key (mount-01).
        return McpMerge(data=None, file_created=False, keys=(), written=False,
                        insert=None, entry=entry, reserialized=False,
                        sha256=_sha256(raw), held=held)
    keys = tuple(f"mcpServers.{k}" for k in missing)

    spliced = _splice_mcp_entries(text, loaded, missing)
    if spliced is not None:
        new_text, region = spliced
        data = new_text.encode("utf-8")
        return McpMerge(data=data, file_created=False, keys=keys, written=True,
                        insert=region, entry=entry, reserialized=False,
                        sha256=_sha256(data), pre_mount=_sha256(raw),
                        entries=missing, held=held)

    # The scanner did not read this shape. Fall back to re-serialization
    # rather than risk a wrong splice, and record that the fallback ran.
    merged = copy.deepcopy(loaded)
    merged.setdefault("mcpServers", {}).update(missing)
    data = (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return McpMerge(data=data, file_created=False, keys=keys, written=True,
                    insert=None, entry=entry, reserialized=True,
                    sha256=_sha256(data), pre_mount=_sha256(raw), entries=missing,
                    held=held)


def _manifest_bytes(result_files: list[WrittenFile], *, scope: str, owner: str,
                    mounted: str, version: str, dirs: tuple[str, ...]) -> bytes:
    """The manifest does not list itself: it cannot carry its own hash,
    and unmount removes it last and unconditionally — it is the mount's
    bookkeeping, not the host's content."""
    body = {
        "schema": MANIFEST_SCHEMA,
        "radialrig": version,
        "mounted": mounted,
        "scope": scope,
        "owner": owner,
        "hash": HASH_NAME,
        "dirs": list(dirs),
        "files": [f.as_json() for f in sorted(result_files, key=lambda f: f.path)],
    }
    return (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _dump_manifest(body: dict) -> bytes:
    return (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# --- survey and mount -------------------------------------------------

def survey(root: Path) -> MountSurvey:
    """What a mount would find here. Writes nothing."""
    root = root.resolve()
    if is_mounted(root):
        return MountSurvey(mounted=True)
    conflicts: list[str] = []
    merges: list[str] = []
    for entry in SEED_FILES:
        target = root / entry.rel
        if entry.mode == CREATED:
            if target.exists():
                conflicts.append(entry.rel)
            continue
        if not target.exists():
            continue
        if entry.mode == APPENDED and _host_holds(root, entry.rel):
            continue
        merges.append(entry.rel)
        if entry.mode == APPENDED:
            # The same composition the mount runs, result check included,
            # so the survey and the mount give one answer for one file.
            try:
                _agents_payload(root, _survey_context(), entry.rel)
            except MountConflict:
                conflicts.append(entry.rel)
        elif entry.rel == MCP_REL:
            try:
                loaded = json.loads(target.read_bytes().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                conflicts.append(entry.rel)
                continue
            if not isinstance(loaded, dict) or not isinstance(
                loaded.get("mcpServers", {}), dict
            ):
                conflicts.append(entry.rel)
    return MountSurvey(
        mounted=False,
        conflicts=tuple(sorted(set(conflicts))),
        merges=tuple(sorted(merges)),
    )


def _compose_mount(
    root: Path, *, scope: str, owner: str, day: str, version: str,
) -> tuple[dict[str, bytes], MountResult]:
    """Everything a mount would write, computed against the disk as it
    stands. Nothing is written here, so `mount` and `--dry-run` render
    from one computation and cannot drift."""
    context = {
        "SCOPE": scope,
        "OWNER": owner,
        "DATE": day,
        "TOOL_CMD": SERVER_COMMAND,
        "WRITE_TOOL_CMD": WRITE_SERVER_COMMAND,
    }

    payload: dict[str, bytes] = {}
    written: list[WrittenFile] = []
    held: list[Disposition] = []
    conflicts: list[str] = []

    for entry in SEED_FILES:
        if entry.mode == CREATED:
            data = _render(entry.source, context) if entry.source else b""
            if (root / entry.rel).exists():
                conflicts.append(entry.rel)
            payload[entry.rel] = data
            written.append(WrittenFile(
                path=entry.rel, mode=CREATED, sha256=_sha256(data), file_created=True,
            ))
        elif entry.mode == APPENDED:
            if _host_holds(root, entry.rel):
                held.append(Disposition(entry.rel, CURRENT, HOST_HOLDS))
                continue
            try:
                whole, recorded = _compose_appended(root, context, entry.rel)
            except MountConflict as exc:
                conflicts.extend(exc.conflicts)
                continue
            payload[entry.rel] = whole
            written.append(recorded)
        elif entry.rel == MCP_REL:
            try:
                merge = _mcp_payload(root)
            except MountConflict as exc:
                conflicts.extend(exc.conflicts)
                continue
            if merge.data is not None:
                payload[entry.rel] = merge.data
            written.append(WrittenFile(
                path=entry.rel, mode=MERGED, sha256=merge.sha256,
                file_created=merge.file_created, keys=merge.keys,
                written=merge.written, insert=merge.insert, entry=merge.entry,
                reserialized=merge.reserialized,
                pre_mount_sha256=merge.pre_mount,
                entries=merge.entries, held=merge.held,
            ))
        else:  # pragma: no cover - SEED_FILES is closed over the cases above
            raise MountError(f"no payload rule for seed entry '{entry.rel}'")

    # Two seed paths that are one file on disk would be written twice,
    # the second write erasing the first. A CLAUDE.md linked to AGENTS.md
    # is held above; any other such pair refuses, both paths named.
    seen: dict[tuple[int, int], str] = {}
    for rel in payload:
        target = root / rel
        if not target.exists():
            continue
        try:
            stat = os.stat(target)
        except OSError:
            continue
        key = (stat.st_dev, stat.st_ino)
        if not stat.st_ino:
            continue
        if key in seen:
            conflicts.extend((seen[key], rel))
        else:
            seen[key] = rel

    if conflicts:
        raise MountConflict(tuple(sorted(set(conflicts))))

    dirs = _planned_dirs(root, (*payload, MANIFEST_REL))
    payload[MANIFEST_REL] = _manifest_bytes(
        written, scope=scope, owner=owner, mounted=day, version=version, dirs=dirs,
    )
    result = MountResult(
        root=root, scope=scope, owner=owner, mounted=day, version=version,
        files=tuple(sorted(written, key=lambda f: f.path)), dirs=dirs,
        held=tuple(held),
    )
    return payload, result


def mount_plan(
    root: Path,
    *,
    scope: str | None = None,
    owner: str = DEFAULT_OWNER,
    today: dt.date | None = None,
) -> MountResult:
    """What `mount` would write here. Writes nothing, raises the same
    refusals, and returns the same result object."""
    root = root.resolve()
    if is_mounted(root):
        raise AlreadyMounted(root)
    scope = scope or default_scope(root)
    day = (today or dt.date.today()).isoformat()
    try:
        _, result = _compose_mount(
            root, scope=scope, owner=owner, day=day, version=installed_version(),
        )
    except OSError as exc:
        raise _stopped(exc, root=root) from exc
    return result


def mount(
    root: Path,
    *,
    scope: str | None = None,
    owner: str = DEFAULT_OWNER,
    today: dt.date | None = None,
) -> MountResult:
    """Lay the seed onto `root`. Compute everything, then write.

    All-or-nothing: a conflict stops the mount before the first byte is
    written, and a file error part-way through puts back what was there
    and raises `WriteStopped` naming what could not be put back; one
    while still reading raises it with nothing left.
    """
    root = root.resolve()
    if is_mounted(root):
        raise AlreadyMounted(root)

    scope = scope or default_scope(root)
    day = (today or dt.date.today()).isoformat()
    try:
        payload, result = _compose_mount(
            root, scope=scope, owner=owner, day=day, version=installed_version(),
        )
    except OSError as exc:
        raise _stopped(exc, root=root) from exc
    _write_all(root, payload)
    return result


def _dir_order(rel: str) -> tuple[int, str]:
    """Outermost first, then by name: the one order a record's directory
    list keeps, so every reader of it agrees."""
    return (len(Path(rel).parts), rel)


def _planned_dirs(root: Path, rels) -> tuple[str, ...]:
    """The directories a write of `rels` would create, outermost first."""
    made: set[str] = set()
    for rel in sorted(rels):
        made.update(p.relative_to(root).as_posix()
                    for p in _missing_parents(root, (root / rel).parent))
    return tuple(sorted(made, key=_dir_order))


def _write_all(root: Path, payload: dict[str, bytes], removals=()) -> None:
    """Write every payload and take out every removal, or leave the tree
    as it was found.

    Mirrors SPEC-3's write rule: a partial mount is worse than no mount,
    because the host cannot tell which half landed. Whatever stops the
    loop, a file error or an interrupt, every write made so far is put
    back; a file error then becomes `WriteStopped`, naming what could
    not be put back, and anything else is re-raised as itself. The
    manifest goes after every other path: it is also a corpus-root
    marker, and a marker standing in front of a missing seed would
    resolve a corpus with no rulebook (mount-02).

    `removals` are paths taken out after the writes, in the same
    transaction and put back by the same restore (PLAN-0017 phase 5): a
    file a later release dropped goes as its replacement lands, or
    neither does. The caller keeps the two sets apart; a path in both
    would be restored to the bytes this call wrote.
    """
    order = sorted(payload, key=lambda rel: (rel == MANIFEST_REL, rel))
    made_dirs: list[Path] = []
    for rel in order:
        made_dirs.extend(_missing_parents(root, (root / rel).parent))
    made_dirs = sorted(set(made_dirs), key=lambda p: len(p.parts))

    originals: dict[Path, bytes | None] = {}
    created_dirs: list[Path] = []
    current: Path = root
    try:
        for directory in made_dirs:
            current = directory
            directory.mkdir(parents=True, exist_ok=True)
            created_dirs.append(directory)
        for rel in order:
            target = root / rel
            current = target
            originals[target] = target.read_bytes() if target.exists() else None
            target.write_bytes(payload[rel])
        for rel in sorted(removals):
            target = root / rel
            current = target
            originals[target] = target.read_bytes() if target.exists() else None
            target.unlink()
    except BaseException as exc:
        left = _put_back(originals, created_dirs, root=root)
        if isinstance(exc, OSError):
            raise _stopped(exc, root=root, path=current, left=left) from exc
        raise


def _restore_all(root: Path, writes: dict[str, bytes], removals) -> None:
    """Apply every rewrite and every removal, or leave the tree as found.

    A half-reversed tree is worse than either state, for the same reason
    a half-mount is (SPEC-3 section 1 guarantee 3, "All paths or none").
    Stops the way `_write_all` stops.
    """
    touched = sorted(set(writes) | set(removals))
    originals: dict[Path, bytes | None] = {}
    current: Path = root
    try:
        for rel in touched:
            target = root / rel
            current = target
            originals[target] = target.read_bytes() if target.is_file() else None
        for rel in sorted(writes):
            current = root / rel
            current.write_bytes(writes[rel])
        for rel in sorted(removals):
            current = root / rel
            current.unlink()
    except BaseException as exc:
        left = _put_back(originals, root=root)
        if isinstance(exc, OSError):
            raise _stopped(exc, root=root, path=current, left=left) from exc
        raise


def _stands_as(target: Path, original: bytes | None) -> bool:
    """Whether the disk already holds the original: the path absent, or
    the same bytes."""
    try:
        if original is None:
            return not target.exists()
        return target.is_file() and target.read_bytes() == original
    except OSError:
        return False


def _put_back(originals: dict[Path, bytes | None], created_dirs=(), *,
              root: Path | None = None) -> tuple[str, ...]:
    """Put every original back and take every created directory out,
    and return the paths that could not be put back.

    A restore that fails is reported, never passed over: the paths come
    back for the error's `left`, spelled the way the record spells them.
    A directory that will not go is named only when no path under it
    already is. Both kits' writers restore through this.
    """
    left: list[str] = []
    for target, original in originals.items():
        try:
            if original is None:
                if target.exists():
                    target.unlink()
            else:
                target.write_bytes(original)
        except OSError:
            if not _stands_as(target, original):
                left.append(_relative(root, target))
    for directory in reversed(list(created_dirs)):
        try:
            directory.rmdir()
        except OSError:
            shown = _relative(root, directory)
            try:
                standing = directory.exists()
            except OSError:
                # A directory that will not even say whether it stands
                # is not put back either; nothing bare leaves the
                # restore path.
                standing = True
            if standing and not any(p.startswith(shown + "/") for p in left):
                left.append(shown)
    return tuple(left)


def _missing_parents(root: Path, leaf: Path) -> list[Path]:
    """Directories between `root` and `leaf` that do not exist yet,
    outermost first, so a rollback can remove them innermost first."""
    missing: list[Path] = []
    current = leaf
    while current != root and root in current.parents:
        if not current.exists():
            missing.append(current)
        current = current.parent
    return list(reversed(missing))


# --- unmount ----------------------------------------------------------

def _unmount_created(target: Path, rel: str, entry: dict):
    if target.is_dir():
        return Disposition(rel, LEFT, LEFT_NOT_A_FILE), None
    if not target.is_file():
        return Disposition(rel, ABSENT, ABSENT_ALREADY_GONE), None
    if matches_recorded(target.read_bytes(), entry.get("sha256")):
        return Disposition(rel, REMOVED, REMOVED_FILE), ("remove", None)
    return Disposition(rel, LEFT, LEFT_EDITED), None


def _unmount_agents(target: Path, rel: str, entry: dict):
    if target.is_dir():
        return Disposition(rel, LEFT, LEFT_NOT_A_FILE), None
    if not target.is_file():
        return Disposition(rel, ABSENT, ABSENT_ALREADY_GONE), None
    data = target.read_bytes()
    found = _locate_section(data, rel)
    if found is None:
        return Disposition(rel, LEFT, LEFT_FENCES_BROKEN), None
    start, end = found
    # The section as it stands, or with CRLF read as LF after a checkout
    # rewrote the endings (BUG-0011), must be the one the record hashed,
    # or that section in its opted-out form (APPEND_OPT_OUTS), which is
    # still the mount's. The reversal below works on the file as it
    # stands: every byte outside the fence, CR included, is the
    # project's and stays.
    if not (matches_recorded(data[start:end], entry.get("section_sha256"))
            or _opted_out(rel, data[start:end])):
        return Disposition(rel, LEFT, LEFT_SECTION_EDITED), None
    # The mount writes a separator only when it appended onto standing
    # content. A record proving either other shape - the file created
    # whole, or a pre-mount file with no bytes - had no separator to
    # reverse, so a newline the project wrote in front of the fence
    # later stays the project's own.
    if entry.get("file_created") or entry.get("pre_mount_sha256") == _sha256(b""):
        sep = 0
    else:
        sep = _separator_length(data, start, bool(entry.get("newline_added")),
                                entry.get("pre_mount_sha256"))
    head = data[:start - sep]
    tail = data[end:]
    if entry.get("file_created"):
        if not head and not tail:
            return Disposition(rel, REMOVED, REMOVED_FILE), ("remove", None)
        # The remainder is bytes this project wrote. Never delete it.
        return Disposition(rel, EXCISED, EXCISED_SECTION), ("write", head + tail)
    # Restored means the bytes from before the mount: the remainder as
    # it stands, or its LF form when a checkout flipped the endings after
    # the mount and only that form hashes to the record. Anything else
    # is excised as it stands.
    restored = _recorded_form(head, entry.get("pre_mount_sha256")) if not tail else None
    if restored is not None:
        return Disposition(rel, RESTORED, RESTORED_PREMOUNT), ("write", restored)
    return Disposition(rel, EXCISED, EXCISED_SECTION), ("write", head + tail)


def _unmount_mcp(target: Path, rel: str, entry: dict):
    if target.is_dir():
        return Disposition(rel, LEFT, LEFT_NOT_A_FILE), None
    written = entry.get("written")
    if written is None:  # schema 1 recorded no flag
        written = bool(entry.get("keys")) or bool(entry.get("file_created"))
    if not target.is_file():
        return Disposition(rel, ABSENT, ABSENT_ALREADY_GONE), None
    if not written:
        return Disposition(rel, LEFT, LEFT_MCP_HOST_OWNED), None
    # Every route below removes only what the record claims, so the
    # claims are checked first, by every route's field at once: a record
    # claiming anything a mount never writes is not trusted for this
    # file at all (PLAN-0022 phase 2).
    claims = _mcp_claims_not_ours(entry)
    if claims:
        return (Disposition(rel, LEFT, LEFT_MCP_UNTRUSTED, ", ".join(claims)),
                None)
    data = target.read_bytes()
    if entry.get("file_created") and matches_recorded(data, entry.get("sha256")):
        # A digest says which bytes, not whose. A mount creates this
        # file holding its own entries and nothing else, so a file
        # holding more is not one it created, whatever the record says.
        held = _mcp_file_not_ours(data)
        if held:
            return (Disposition(rel, LEFT, LEFT_MCP_NOT_ALL_OURS, ", ".join(held)),
                    None)
        return Disposition(rel, REMOVED, REMOVED_FILE), ("remove", None)
    # A file the mount created and the project then changed — the write
    # entry removed to switch capture off, or a server of its own added —
    # takes the structural path below like any other: the entries the
    # mount wrote come out, and a file emptied of everything the mount
    # wrote is removed whole.

    insert = entry.get("insert")
    if isinstance(insert, str) and insert:
        region = _held_region(data, insert.encode("utf-8"))
        seen = data.count(region)
        if seen == 1:
            remainder = data.replace(region, b"", 1)
            # "Restored to its pre-mount bytes" is claimed only when the
            # remainder provably hashes to the pre-merge file, as it
            # stands or with CRLF read as LF (BUG-0011), and writes the
            # form that hashes; a host edit elsewhere in the file makes
            # the honest verb "excised", and excision keeps every byte
            # outside the region as it stands.
            restored = _recorded_form(remainder, entry.get("pre_mount_sha256"))
            if restored is not None:
                return (Disposition(rel, RESTORED, RESTORED_PREMOUNT),
                        ("write", restored))
            return (Disposition(rel, EXCISED, EXCISED_MCP_REGION),
                    ("write", remainder))
        if seen > 1:
            return Disposition(rel, LEFT, LEFT_MCP_AMBIGUOUS), None

    # The region is gone or the file was reformatted: drop the recorded
    # keys structurally, and say so. Every other key survives. A recorded
    # key whose value changed holds every removal back: the mount cannot
    # tell an edit it should keep from one it should not.
    # A key written twice is read here as the other two routes read it:
    # JSON keeps the last, and the file holds both.
    try:
        loaded = json.loads(data.decode("utf-8"),
                            object_pairs_hook=_no_duplicate_keys)
    except RepeatedKey:
        return Disposition(rel, LEFT, LEFT_MCP_REPEATED_KEY), None
    except (ValueError, RecursionError):  # UnicodeDecodeError is one
        return Disposition(rel, LEFT, LEFT_MCP_UNPARSED), None
    if not isinstance(loaded, dict):
        return Disposition(rel, LEFT, LEFT_MCP_UNPARSED), None
    servers = loaded.get("mcpServers")
    recorded = _recorded_entries(entry)
    if not recorded:
        recorded = {SERVER_KEY: _mcp_entry()}
    present = {k: v for k, v in recorded.items()
               if isinstance(servers, dict) and k in servers}
    if not present:
        return Disposition(rel, LEFT, LEFT_MCP_KEY_GONE), None
    if any(servers[k] != v for k, v in present.items()):
        return Disposition(rel, LEFT, LEFT_MCP_ENTRY_CHANGED), None
    for k in present:
        del servers[k]
    if entry.get("file_created") and loaded == {"mcpServers": {}}:
        return Disposition(rel, REMOVED, REMOVED_FILE), ("remove", None)
    # An emptied mcpServers object stays where the mount did not create it.
    out = (json.dumps(loaded, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    removed = ", ".join(f"mcpServers.{k}" for k in present)
    return Disposition(rel, LEFT, LEFT_MCP_RESERIALIZED, removed), ("write", out)


NOT_OUR_TEXT = "text that is not RadialRig's own server entries"


def _ours(key, value, ours: dict[str, dict]) -> bool:
    """Whether one server entry is one a mount writes: its key is one of
    RadialRig's two, carrying the value written for that key. The key is
    asked first, because a missing key and a JSON null read alike to
    `dict.get` (PLAN-0022 phase 3)."""
    return key in ours and ours[key] == value


def _claim_name(key: str, ours: dict[str, dict]) -> str:
    if key in ours:
        return f"mcpServers.{key} with another value"
    return f"mcpServers.{key}"


class RepeatedKey(ValueError):
    """JSON text that names one key twice in an object."""


def _no_duplicate_keys(pairs):
    """`object_pairs_hook` that refuses a repeated key: JSON keeps the
    last, and the text it was read from holds both."""
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise RepeatedKey("a key appears twice")
    return dict(pairs)


def _mcp_claims_not_ours(entry: dict) -> list[str]:
    """What a `.mcp.json` record claims that a mount never writes.

    A mount writes two server entries, `radialrig` and `radialrig-write`,
    each with the one value it has always had, and records them under
    `entries`, under `keys` with the read server's value in `entry`, and
    as the text it spliced in. A claim on any other key, or on one of
    these keys with another value, is not RadialRig's. Empty for every
    record a release wrote.
    """
    ours = _mcp_entries()
    claims: list[str] = []
    listed = entry.get("entries")
    if isinstance(listed, dict):
        claims.extend(_claim_name(key, ours) for key, value in listed.items()
                      if not _ours(key, value, ours))
    keys = entry.get("keys")
    if isinstance(keys, list):
        for name in keys:
            short = (name[len("mcpServers."):] if isinstance(name, str)
                     and name.startswith("mcpServers.") else None)
            if short not in ours:
                claims.append(str(name))
    recorded = entry.get("entry")
    if isinstance(recorded, dict) and recorded != ours[SERVER_KEY]:
        claims.append(_claim_name(SERVER_KEY, ours))
    insert = entry.get("insert")
    if isinstance(insert, str) and insert:
        claims.extend(_insert_claims(insert, ours))
    return list(dict.fromkeys(claims))


def _insert_claims(insert: str, ours: dict[str, dict]) -> list[str]:
    """What the recorded text claims beyond RadialRig's own entries.

    The text a merge splices in is server members, one after another,
    or a whole `mcpServers` member holding them, with its whitespace and
    at most a trailing comma (`_mcp_insert_entries`). It is read as
    JSON inside braces, so its line endings do not matter. Text that
    does not read that way is not RadialRig's as a whole.
    """
    core = insert.strip()
    if core.endswith(","):
        core = core[:-1]
    try:
        members = json.loads("{" + core + "}", object_pairs_hook=_no_duplicate_keys)
    except (ValueError, RecursionError):  # too deep for the reader
        return [NOT_OUR_TEXT]
    if "mcpServers" in members:
        inner = members["mcpServers"]
        if len(members) != 1 or not isinstance(inner, dict):
            return [NOT_OUR_TEXT]
        members = inner
    if not members:
        return [NOT_OUR_TEXT]
    return [_claim_name(key, ours) for key, value in members.items()
            if not _ours(key, value, ours)]


def _mcp_file_not_ours(data: bytes) -> list[str]:
    """What keeps a whole `.mcp.json` from being a file a mount creates.
    Empty only when it holds RadialRig's own entries, one or both, and
    nothing else: a file holding none of them is not one either."""
    ours = _mcp_entries()
    try:
        loaded = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        return ["text that does not read as JSON"]
    if not isinstance(loaded, dict):
        return ["a value that is not a JSON object"]
    held = [f"the top-level key {key}" for key in loaded if key != "mcpServers"]
    servers = loaded.get("mcpServers", {})
    if not isinstance(servers, dict):
        return [*held, "an mcpServers value that is not an object"]
    held.extend(_claim_name(key, ours) for key, value in servers.items()
                if not _ours(key, value, ours))
    if not any(key in ours for key in servers):
        held.append("no RadialRig server entry")
    return held


def _directory_dispositions(
    root: Path, dir_rels, removals: set[str],
) -> tuple[tuple[Disposition, ...], tuple[str, ...]]:
    """What happens to each directory the mount created, deepest first.

    Only directories in the manifest's list are considered: a host that
    already had an empty `docs/plans/` keeps it, which a "rmdir if empty"
    heuristic would silently take away.
    """
    gone_paths = {(root / rel).resolve() for rel in removals}
    order = sorted(dir_rels, key=lambda r: (-len(Path(r).parts), r))
    emptied: dict[Path, bool] = {}
    dispositions: list[Disposition] = []
    removable: list[str] = []
    for rel in order:
        directory = root / rel
        shown = rel + "/"
        if not directory.is_dir():
            emptied[directory.resolve()] = True
            dispositions.append(Disposition(shown, ABSENT, ABSENT_ALREADY_GONE))
            continue
        blockers = 0
        try:
            children = list(directory.iterdir())
        except OSError:
            children = []
        for child in children:
            if child.is_dir():
                if not emptied.get(child.resolve(), False):
                    blockers += 1
            elif child.resolve() not in gone_paths:
                blockers += 1
        if blockers:
            emptied[directory.resolve()] = False
            surviving = sum(
                1 for p in directory.rglob("*")
                if p.is_file() and p.resolve() not in gone_paths
            )
            dispositions.append(
                Disposition(shown, LEFT, LEFT_DIR_OCCUPIED, str(surviving))
            )
        else:
            emptied[directory.resolve()] = True
            removable.append(rel)
            dispositions.append(Disposition(shown, REMOVED, REMOVED_DIR))
    return tuple(dispositions), tuple(removable)


def unmount_plan(root: Path) -> UnmountPlan:
    """What an unmount would do here. Writes nothing. A file error while
    reading is `WriteStopped` with nothing left."""
    root = root.resolve()
    try:
        return _unmount_plan(root)
    except OSError as exc:
        raise _stopped(exc, root=root) from exc


def _unmount_plan(root: Path) -> UnmountPlan:
    manifest = _readable_manifest(root)
    # Schema 1 predates the restore fields; an adopted record was
    # rebuilt without them. Both reverse by excision, and the note
    # states it.
    degraded = (int(manifest.get("schema", 1)) < MANIFEST_SCHEMA
                or "adopted" in manifest)

    file_dispositions: list[Disposition] = []
    writes: dict[str, bytes] = {}
    removals: list[str] = []

    for entry in manifest.get("files", []):
        rel = entry.get("path")
        if not isinstance(rel, str) or rel == MANIFEST_REL:
            # The record itself has its own row below.
            continue
        target = root / rel
        mode = entry.get("mode")
        if not isinstance(mode, str) or (rel, mode) not in SEED_PAIRS:
            # No mount writes this path this way, so the claim is a
            # forged or edited record's, and the path is the project's.
            file_dispositions.append(Disposition(rel, LEFT, LEFT_NOT_SEEDED))
            continue
        if mode == APPENDED:
            disposition, action = _unmount_agents(target, rel, entry)
        elif mode == MERGED:
            disposition, action = _unmount_mcp(target, rel, entry)
        else:
            disposition, action = _unmount_created(target, rel, entry)
        file_dispositions.append(disposition)
        if action is None:
            continue
        kind, data = action
        if kind == "remove":
            removals.append(rel)
        else:
            writes[rel] = data

    # The manifest goes last and unconditionally: it is also a corpus-root
    # marker, so leaving it would keep the servers resolving a corpus that
    # is no longer mounted.
    file_dispositions.append(Disposition(MANIFEST_REL, REMOVED, REMOVED_FILE))
    file_dispositions.sort(key=lambda d: d.path)

    dir_rels = manifest.get("dirs")
    if not isinstance(dir_rels, list):
        dir_rels = [rel for rel in LEGACY_DIRS if (root / rel).is_dir()]
    dir_rels = [rel for rel in dir_rels if isinstance(rel, str)]
    dir_dispositions, removable = _directory_dispositions(
        root, [rel for rel in dir_rels if rel in MOUNT_DIRS],
        set(removals) | {MANIFEST_REL},
    )
    # A directory no mount creates is the project's, whatever the record
    # says: named, and never removed.
    dir_dispositions += tuple(
        Disposition(rel.rstrip("/") + "/", LEFT, LEFT_DIR_NOT_SEEDED)
        for rel in dir_rels if rel not in MOUNT_DIRS
    )

    dispositions = tuple(file_dispositions) + dir_dispositions
    orphaned = tuple(
        d.path for d in dir_dispositions
        if d.code == LEFT_DIR_OCCUPIED and d.path.rstrip("/") in RECORD_DIRS
        and int(d.detail or "0") > 0
    )
    complete = not any(d.verb in (LEFT, EXCISED) for d in dispositions)
    return UnmountPlan(
        root=root, manifest=manifest, dispositions=dispositions,
        complete=complete, degraded=degraded,
        writes=tuple(sorted(writes.items())), removals=tuple(sorted(removals)),
        orphaned=orphaned,
    )


def unmount(root: Path) -> UnmountPlan:
    """Reverse the mount, then report what was left and why."""
    plan = unmount_plan(root)
    root = plan.root
    _restore_all(root, dict(plan.writes), plan.removals)
    manifest_path = root / MANIFEST_REL
    try:
        manifest_path.unlink()
    except FileNotFoundError:  # pragma: no cover - the plan just read it
        pass
    except OSError as exc:
        # The reversal landed and the record would not go: what the plan
        # changed stands changed, and the error says so.
        raise _stopped(exc, root=root, path=manifest_path, left=(
            *(rel for rel, _ in plan.writes), *plan.removals)) from exc

    applied: list[Disposition] = []
    for disposition in plan.dispositions:
        if disposition.verb == REMOVED and disposition.code == REMOVED_DIR:
            try:
                (root / disposition.path.rstrip("/")).rmdir()
            except OSError:
                # Windows refuses to remove a process's working directory.
                # Best-effort and reported, never fatal.
                applied.append(replace(disposition, verb=LEFT, code=LEFT_DIR_LOCKED))
                continue
        applied.append(disposition)

    dispositions = tuple(applied)
    return replace(
        plan,
        dispositions=dispositions,
        complete=not any(d.verb in (LEFT, EXCISED) for d in dispositions),
    )


# --- upgrade ----------------------------------------------------------

def _diff(rel: str, mine: bytes, theirs: bytes, version: str) -> str:
    """A unified diff of the host's bytes against the installed seed."""
    try:
        left = mine.decode("utf-8").splitlines(keepends=True)
        right = theirs.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return "differs; the contents are not text\n"
    out = difflib.unified_diff(
        left, right, n=3,
        fromfile=f"{rel} (this project)",
        tofile=f"{rel} (radialrig {version} seed)",
    )
    text = "".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def _entry_diff(rel: str, key: str, mine, theirs, version: str) -> str:
    """One entry's diff. `mine` of None is an entry this project lacks."""
    left = b"" if mine is None else (
        json.dumps(mine, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    right = (json.dumps(theirs, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return _diff(f"{rel} :: mcpServers.{key}", left, right, version)


def _upgrade_created(root: Path, rel: str, entry: dict, theirs: bytes, version: str):
    target = root / rel
    if not target.is_file():
        return UpgradeItem(rel, ABSENT, UPGRADE_ABSENT), None, None
    mine = target.read_bytes()
    if mine == theirs or _lf(mine) == theirs:
        # Equal to the installed seed, as it stands or with CRLF read as
        # LF (BUG-0011). When this project edited its way here, the
        # recorded hash stays as it was: that field means "the bytes
        # radialrig last wrote", and radialrig did not write these.
        return UpgradeItem(rel, CURRENT, UPGRADE_CURRENT), None, None
    if matches_recorded(mine, entry.get("sha256")):
        return (UpgradeItem(rel, UPDATED, UPGRADE_APPLIED), theirs,
                {"sha256": _sha256(theirs)})
    return (UpgradeItem(rel, LEFT, UPGRADE_PENDING, _diff(rel, mine, theirs, version)),
            None, None)


def _upgrade_unrecorded(root: Path, rel: str, theirs: bytes):
    """A seed path this project's record never carried.

    `_upgrade_created` reads an absent file as one this project removed,
    which is the right reading for a path the record names. A path the
    record does not name was never written here, because this radialrig
    is the first to ship it. Then an absent file is not a removal, and
    upgrade writes it and records it, so a later unmount takes it back
    out. A file already standing at that path is this project's own:
    upgrade leaves it, says so, and records nothing, because a record of
    a file the mount never wrote would let unmount delete it.
    """
    target = root / rel
    if target.exists():
        return UpgradeItem(rel, LEFT, UPGRADE_NEW_LEFT), None, None
    recorded = WrittenFile(
        path=rel, mode=CREATED, sha256=_sha256(theirs), file_created=True,
    )
    return UpgradeItem(rel, CREATED, UPGRADE_NEW_WRITTEN), theirs, recorded


def _upgrade_unrecorded_section(root: Path, rel: str, context: dict[str, str]):
    """An appended seed path this project's record never carried:
    `.codex/config.toml` on a mount made before Codex was a harness
    (PLAN-0013 phase 1).

    The same reading as `_upgrade_unrecorded`: a path the record does
    not name was never written here, so an absent file is not a removal.
    The file is composed the way a mount composes it — created whole, or
    the region appended onto the project's own file — written, and
    recorded with the fields a mount records, so a later unmount takes
    exactly that region back out. A file the composer refuses (a fence
    already there, or a shape the file's checks reject) is left, said
    so, and not recorded, because a record of bytes the mount never
    wrote would let unmount delete the project's own.
    """
    if _host_holds(root, rel):
        return UpgradeItem(rel, CURRENT, HOST_HOLDS), None, None
    try:
        whole, recorded = _compose_appended(root, context, rel)
    except MountConflict:
        return UpgradeItem(rel, LEFT, UPGRADE_NEW_SECTION_LEFT), None, None
    verb = CREATED if recorded.file_created else APPENDED
    return UpgradeItem(rel, verb, UPGRADE_NEW_WRITTEN), whole, recorded


def _upgrade_agents(root: Path, rel: str, entry: dict, theirs: bytes, version: str):
    target = root / rel
    if not target.is_file():
        return UpgradeItem(rel, LEFT, UPGRADE_SECTION_UNREADABLE), None, None
    data = target.read_bytes()
    found = _locate_section(data, rel)
    if found is None:
        return UpgradeItem(rel, LEFT, UPGRADE_SECTION_UNREADABLE), None, None
    start, end = found
    mine = data[start:end]
    # A region the owner opted out of (the write table removed, nothing
    # else changed) moves to the installed seed in the same form: the
    # opt-out is the owner's and survives every upgrade.
    opt = APPEND_OPT_OUTS.get(rel)
    opted = opt is not None and opt[0](mine)
    if opted:
        theirs = opt[1](theirs)
    if mine == theirs or _lf(mine) == theirs:
        return UpgradeItem(rel, CURRENT, UPGRADE_SECTION_CURRENT), None, None
    # The section as it stands, or with CRLF read as LF after a checkout
    # rewrote the endings (BUG-0011), is the one the record hashed: the
    # new section is spliced into the file as it stands, LF as the mount
    # writes it, and every byte outside the fences, CR included, stays,
    # so unmount still restores the pre-mount bytes afterwards. The
    # spliced file passes the same result check a mount runs before it
    # is written: a file the project broke outside the fence, or a key
    # it let fall into a seeded table, is left and said so.
    if opted or matches_recorded(mine, entry.get("section_sha256")):
        updated = data[:start] + theirs + data[end:]
        result_ok = APPEND_RESULT_CHECKS.get(rel)
        if result_ok is not None and not result_ok(updated):
            return UpgradeItem(rel, LEFT, UPGRADE_SECTION_REFUSED), None, None
        return (UpgradeItem(rel, UPDATED, UPGRADE_SECTION_APPLIED), updated,
                {"sha256": _sha256(_lf(updated)), "section_sha256": _sha256(theirs)})
    return (UpgradeItem(rel, LEFT, UPGRADE_SECTION_PENDING,
                        _diff(rel, mine, theirs, version)), None, None)


def _upgrade_mcp(root: Path, rel: str, entry: dict, version: str):
    written = entry.get("written")
    if written is None:
        written = bool(entry.get("keys")) or bool(entry.get("file_created"))
    if not written:
        return UpgradeItem(rel, CURRENT, UPGRADE_MCP_HOST_OWNED), None, None
    target = root / rel
    if not target.is_file():
        return UpgradeItem(rel, ABSENT, UPGRADE_ABSENT), None, None
    data = target.read_bytes()
    try:
        loaded = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return UpgradeItem(rel, LEFT, UPGRADE_MCP_UNREADABLE), None, None
    if not isinstance(loaded, dict):
        return UpgradeItem(rel, LEFT, UPGRADE_MCP_UNREADABLE), None, None
    servers = loaded.get("mcpServers")
    recorded = _recorded_entries(entry) or {SERVER_KEY: _mcp_entry()}
    if not isinstance(servers, dict) or all(k not in servers for k in recorded):
        return UpgradeItem(rel, ABSENT, UPGRADE_ENTRY_ABSENT), None, None
    # An entry the project removed is its own choice — the write server,
    # for an owner who switched capture off (D-35 commitment 6) — and is
    # neither restored nor held against the project on every upgrade.
    # The entries still standing are judged on their own.
    removed = [k for k in recorded if k not in servers]
    recorded = {k: v for k, v in recorded.items() if k in servers}

    # Every key the record claims, judged on its own: unchanged since the
    # mount and different from the installed seed moves; changed by this
    # project parks with a diff. A key the seed no longer ships stays as
    # recorded.
    wanted = _mcp_entries()
    changes: dict[str, tuple[dict, dict]] = {}
    diffs: list[str] = []
    for k, base in recorded.items():
        mine = servers[k]
        theirs = wanted.get(k, base)
        if mine == theirs:
            continue
        if mine != base:
            diffs.append(_entry_diff(rel, k, mine, theirs, version))
            continue
        changes[k] = (base, theirs)
    if diffs:
        return UpgradeItem(rel, LEFT, UPGRADE_ENTRY_PENDING, "".join(diffs)), None, None
    if not changes:
        if removed:
            gone = ", ".join(f"mcpServers.{k}" for k in removed)
            return UpgradeItem(rel, CURRENT, UPGRADE_ENTRY_REMOVED, detail=gone), None, None
        return UpgradeItem(rel, CURRENT, UPGRADE_ENTRY_CURRENT), None, None

    after = {**recorded, **{k: theirs for k, (_, theirs) in changes.items()}}
    fields: dict = {"entries": after}
    if SERVER_KEY in after:
        fields["entry"] = after[SERVER_KEY]
    insert = entry.get("insert")
    # The region as the file holds it, as recorded or with its endings
    # flipped by a checkout (BUG-0011); the rewrite replaces exactly that
    # region in the file as it stands, in the same endings, and records
    # what it wrote, as the merge did. No byte outside the region moves.
    held = (_held_region(data, insert.encode("utf-8"))
            if isinstance(insert, str) and insert else b"")
    olds = {k: json.dumps(base, ensure_ascii=False) for k, (base, _) in changes.items()}
    if isinstance(insert, str) and all(
        old in insert for old in olds.values()
    ) and data.count(held) == 1:
        new_insert = insert
        for k, (_, theirs) in changes.items():
            new_insert = new_insert.replace(
                olds[k], json.dumps(theirs, ensure_ascii=False), 1)
        written = new_insert.encode("utf-8")
        if held != insert.encode("utf-8"):
            written = _with_endings_of(held, written)
        updated = data.replace(held, written, 1)
        fields.update({"sha256": _sha256(_lf(updated)),
                       "insert": written.decode("utf-8")})
    elif entry.get("file_created") and matches_recorded(data, entry.get("sha256")):
        for k, (_, theirs) in changes.items():
            servers[k] = theirs
        updated = (json.dumps(loaded, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        fields["sha256"] = _sha256(updated)
    else:
        return UpgradeItem(rel, LEFT, UPGRADE_MCP_DEGRADED), None, None

    try:
        check = json.loads(updated.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):  # pragma: no cover - guard
        return UpgradeItem(rel, LEFT, UPGRADE_MCP_DEGRADED), None, None
    if any(  # pragma: no cover - guard
        check.get("mcpServers", {}).get(k) != theirs for k, (_, theirs) in changes.items()
    ):
        return UpgradeItem(rel, LEFT, UPGRADE_MCP_DEGRADED), None, None
    return UpgradeItem(rel, UPDATED, UPGRADE_ENTRY_APPLIED), updated, fields


def _upgrade_mcp_addition(rel: str, entry: dict, data: bytes, fields, version: str):
    """A seed key this record never carried: the write server, on a mount
    made before it was registered (PLAN-0012 phase 1).

    The same shape as `_upgrade_unrecorded`, one level down: a key the
    record does not name was never written here, so its absence is not a
    removal, and upgrade adds it and records it so a later unmount takes
    it back out. A key already standing under that name is this
    project's own and is never touched. Whether it gets a row depends on
    who judged it: a record carrying `entries` was written by a build
    that knew every seed key, so a key it omits was left as the host's
    at mount and needs no second word, and one it omits that is absent
    from the file now was removed by the owner since (D-35 commitment
    6): left, said so, nothing written and nothing recorded; a record
    from before the write server that wrote nothing is already covered
    by the read step's host-owned row; a record from before the write
    server that wrote the read entry never looked at the other key, so
    it is left, said so, and not recorded, as `_upgrade_unrecorded`
    does for a file. The `entries` field is the whole discriminator:
    the manifest's version stamp is rewritten by any later upgrade and
    says nothing about which build judged this file.

    The addition follows the record the read entry left: a file the
    mount created and nobody edited is rewritten whole; a recorded
    region is replaced by one region holding every key, so the reversal
    still deletes exactly one region; a file the mount never wrote into
    gains a region of its own. Anything else parks, with the entry to
    add shown as a diff.

    `data` and `fields` are what the read entry's own upgrade left: the
    bytes it would write, or the file as it stands, and the record
    fields it changed. Returns the rows to report, the bytes to write,
    and the record fields to change.
    """
    effective = {**entry, **(fields or {})}
    recorded = _recorded_entries(effective)
    missing = {k: v for k, v in _mcp_entries().items() if k not in recorded}
    if not missing:
        return (), None, None
    insert = effective.get("insert")
    try:
        text = data.decode("utf-8")
        loaded = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return (), None, None
    if not isinstance(loaded, dict):
        return (), None, None
    servers = loaded.get("mcpServers", {})
    if not isinstance(servers, dict):
        return (), None, None
    written = effective.get("written")
    if written is None:
        written = bool(effective.get("keys")) or bool(effective.get("file_created"))

    held = {k: v for k, v in missing.items() if k in servers}
    to_add = {k: v for k, v in missing.items() if k not in servers}
    items: list[UpgradeItem] = []
    if held and written and "entries" not in effective:
        label = ", ".join(f"mcpServers.{k}" for k in held)
        items.append(UpgradeItem(f"{rel} :: {label}", LEFT, UPGRADE_ENTRY_NEW_LEFT,
                                 detail=label))
    if not to_add:
        return tuple(items), None, None

    label = ", ".join(f"mcpServers.{k}" for k in to_add)
    shown = f"{rel} :: {label}"
    pending = (
        tuple(items) + (UpgradeItem(
            shown, LEFT, UPGRADE_ENTRY_NEW_PENDING,
            "".join(_entry_diff(rel, k, None, v, version) for k, v in to_add.items()),
            detail=label,
        ),),
        None, None,
    )
    keys = [k for k in effective.get("keys", []) if isinstance(k, str)]
    keys += [f"mcpServers.{k}" for k in to_add]
    new_fields: dict = {"keys": keys, "entries": {**recorded, **to_add}}

    if effective.get("file_created"):
        if not matches_recorded(data, effective.get("sha256")):
            return pending
        loaded.setdefault("mcpServers", {}).update(to_add)
        updated = (json.dumps(loaded, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    elif isinstance(insert, str) and insert:
        # The region as the file holds it (BUG-0011); the remainder is
        # the file as it stands without it, and the new region goes in
        # the way a merge would, in the file's own endings.
        region = _held_region(data, insert.encode("utf-8"))
        if data.count(region) != 1:
            return pending
        remainder = data.replace(region, b"", 1)
        try:
            remainder_text = remainder.decode("utf-8")
            remainder_loaded = json.loads(remainder_text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return pending
        if not isinstance(remainder_loaded, dict):
            return pending
        spliced = _splice_mcp_entries(remainder_text, remainder_loaded, new_fields["entries"])
        if spliced is None:
            return pending
        new_text, new_region = spliced
        updated = new_text.encode("utf-8")
        new_fields["insert"] = new_region
    elif not written and "entries" in effective:
        # Judged: every seed key stood when this record was written, so
        # a key absent now is the owner's removal (mount-01).
        return (tuple(items) + (UpgradeItem(
            shown, LEFT, UPGRADE_ENTRY_HELD_REMOVED, detail=label),), None, None)
    elif not written:
        # Unjudged: a record from before `entries` was recorded on every
        # merged record proves nothing about this key, whatever its
        # stamp says. Added once and recorded, so a later removal is
        # honoured; the window the plan's Decided claim accepts.
        spliced = _splice_mcp_entries(text, loaded, to_add)
        if spliced is None:
            return pending
        new_text, new_region = spliced
        updated = new_text.encode("utf-8")
        new_fields.update({
            "insert": new_region, "written": True, "pre_mount_sha256": _sha256(data),
        })
    else:
        return pending

    try:
        check = json.loads(updated.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):  # pragma: no cover - guard
        return pending
    if any(  # pragma: no cover - guard
        check.get("mcpServers", {}).get(k) != v for k, v in to_add.items()
    ):
        return pending
    new_fields["sha256"] = _sha256(updated)
    items.append(UpgradeItem(shown, CREATED, UPGRADE_ENTRY_NEW_WRITTEN, detail=label))
    return tuple(items), updated, new_fields


def _hold_partner(root: Path, items: list[UpgradeItem], writes: dict[str, bytes],
                  changed_fields: dict[str, dict], context: dict, version: str) -> None:
    """Keep the fence and docs/spec.md together (BUG-0004).

    The two cite each other. When one of them parks as pending because
    this project edited it, the other is held back too: its write is
    dropped, and it reports as pending with the same diff the edited
    path gets, so the owner merges both at once. Every other seed path
    keeps its own independent outcome.
    """
    by_path = {item.path: item for item in items}
    edited = {
        rel for rel in UPGRADE_PAIR
        if rel in by_path
        and by_path[rel].code in (UPGRADE_PENDING, UPGRADE_SECTION_PENDING)
    }
    if not edited:
        return
    for rel in UPGRADE_PAIR:
        item = by_path.get(rel)
        if item is None or item.verb != UPDATED:
            continue
        partner = next(p for p in UPGRADE_PAIR if p != rel)
        data = (root / rel).read_bytes()
        if rel == AGENTS_REL:
            start, end = _locate_section(data)
            mine, theirs = data[start:end], _section_bytes(context)
        else:
            mine, theirs = data, writes[rel]
        items[items.index(item)] = UpgradeItem(
            rel, LEFT, UPGRADE_HELD, _diff(rel, mine, theirs, version), detail=partner,
        )
        writes.pop(rel, None)
        changed_fields.pop(rel, None)


def upgrade_plan(root: Path, *, version: str | None = None) -> UpgradePlan:
    """Compare this project's seeded files against the installed seed.

    The seed is rendered with the manifest's scope, owner and **mount
    date**, never today's. `{{DATE}}` fills `effective:` and
    `last-verified:`; re-dating them because a package moved is exactly
    the date-gardening SPEC-3 refuses, and it would make every file
    differ on every upgrade. A file error while reading is `WriteStopped`
    with nothing left.
    """
    root = root.resolve()
    try:
        return _upgrade_plan(root, version)
    except OSError as exc:
        raise _stopped(exc, root=root) from exc


def _upgrade_plan(root: Path, version: str | None) -> UpgradePlan:
    manifest = _readable_manifest(root)
    # Schema 1 predates the restore fields; an adopted record was
    # rebuilt without them. Both reverse by excision, and the note
    # states it.
    degraded = (int(manifest.get("schema", 1)) < MANIFEST_SCHEMA
                or "adopted" in manifest)
    to_version = version or installed_version()
    from_version = str(manifest.get("radialrig", "unknown"))
    context = {
        "SCOPE": str(manifest.get("scope", "")),
        "OWNER": str(manifest.get("owner", DEFAULT_OWNER)),
        "DATE": str(manifest.get("mounted", "")),
        "TOOL_CMD": SERVER_COMMAND,
        "WRITE_TOOL_CMD": WRITE_SERVER_COMMAND,
    }
    by_rel = {entry.rel: entry for entry in SEED_FILES}

    items: list[UpgradeItem] = []
    writes: dict[str, bytes] = {}
    changed_fields: dict[str, dict] = {}
    added: list[dict] = []
    recorded_rels = {
        entry.get("path") for entry in manifest.get("files", [])
        if isinstance(entry.get("path"), str)
    }

    for entry in manifest.get("files", []):
        rel = entry.get("path")
        seed = by_rel.get(rel)
        if seed is None:
            continue
        if seed.rel == MCP_REL:
            item, data, fields = _upgrade_mcp(root, rel, entry, to_version)
            items.append(item)
            if data is not None:
                writes[rel] = data
                changed_fields[rel] = fields or {}
            # Then the key this record never carried, on top of whatever
            # the read entry's own step left.
            current = data
            if current is None and (root / rel).is_file():
                current = (root / rel).read_bytes()
            if current is not None:
                added_items, added_data, added_fields = _upgrade_mcp_addition(
                    rel, entry, current, fields, to_version)
                items.extend(added_items)
                if added_data is not None:
                    writes[rel] = added_data
                    changed_fields[rel] = {**changed_fields.get(rel, {}), **added_fields}
            continue
        elif seed.mode == APPENDED:
            theirs = _section_bytes(context, rel)
            item, data, fields = _upgrade_agents(root, rel, entry, theirs, to_version)
        else:
            theirs = _render(seed.source, context) if seed.source else b""
            item, data, fields = _upgrade_created(root, rel, entry, theirs, to_version)
        items.append(item)
        if data is not None:
            writes[rel] = data
            changed_fields[rel] = fields or {}
    _hold_partner(root, items, writes, changed_fields, context, to_version)

    # Seed paths this record never carried. A CREATED entry is written
    # whole; an APPENDED one is composed the way a mount composes it; the
    # MERGED file is one a mount always records, so a record without it
    # is not a version gap. So is the route map: every mount writes it,
    # and a record without it is one adopt could not claim because the
    # project removed or edited the section, which upgrade never puts
    # back (and its partner docs/spec.md may be held, BUG-0004). The
    # directories such a write creates join the record's list, so
    # unmount takes them back out.
    new_rels: list[str] = []
    for seed in SEED_FILES:
        if seed.rel in recorded_rels or seed.mode == MERGED:
            continue
        if seed.mode == APPENDED and seed.rel in UPGRADE_PAIR:
            continue
        if seed.mode == APPENDED:
            item, data, recorded = _upgrade_unrecorded_section(root, seed.rel, context)
        else:
            theirs = _render(seed.source, context) if seed.source else b""
            item, data, recorded = _upgrade_unrecorded(root, seed.rel, theirs)
        items.append(item)
        if data is not None:
            writes[seed.rel] = data
            added.append(recorded.as_json())
            new_rels.append(seed.rel)

    manifest_after = copy.deepcopy(manifest)
    manifest_after["radialrig"] = to_version
    if new_rels:
        known = [d for d in manifest.get("dirs", []) if isinstance(d, str)]
        fresh = [d for d in _planned_dirs(root, new_rels) if d not in known]
        if fresh:
            manifest_after["dirs"] = sorted({*known, *fresh}, key=_dir_order)
    manifest_after["files"] = sorted(
        [
            {**entry, **changed_fields.get(entry.get("path"), {})}
            for entry in manifest.get("files", [])
        ] + added,
        key=lambda entry: str(entry.get("path", "")),
    )
    pending = sum(1 for i in items if i.verb in (LEFT, ABSENT))
    return UpgradePlan(
        root=root, manifest=manifest, from_version=from_version,
        to_version=to_version, items=tuple(items), pending=pending,
        writes=tuple(sorted(writes.items())), manifest_after=manifest_after,
        degraded=degraded,
    )


def upgrade(root: Path, *, today: dt.date | None = None) -> UpgradePlan:
    """Apply the upgrade this project's manifest allows, and report.

    Only files this project never edited are written. A pure-`current`
    run touches nothing at all, the manifest included.
    """
    plan = upgrade_plan(root)
    if not plan.writes:
        return plan
    body = copy.deepcopy(plan.manifest_after)
    body["upgraded"] = (today or dt.date.today()).isoformat()
    payload = dict(plan.writes)
    payload[MANIFEST_REL] = _dump_manifest(body)
    _write_all(plan.root, payload)
    return replace(plan, manifest_after=body)
