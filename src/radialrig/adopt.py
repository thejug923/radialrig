"""The adopt flow: rebuild a lost mount record from standing files.

BUG-0001. A project whose `.radialrig/mount.json` is gone while the
seeded files stand is outside the verb lifecycle: mount refuses on the
standing files, unmount and upgrade refuse on the missing record. This
module writes a fresh record for such a project, and it writes only the
record — no standing byte is touched.

The record is rebuilt from evidence, never from a claim. A path is
recorded as the mount's own only when its bytes match what this build's
seed would have written, proven by hash; every other adopted path is
recorded as this project's own, which unmount never deletes and upgrade
never silently rewrites. This is constraint 5's boundary for adopt, the
owner's ruling of 2026-08-22 (PLAN-0008).

The seed carries tokens — scope, owner, date — so the comparison needs
the original mount's values. Adopt recovers the date from the standing
`docs/spec.md` frontmatter and takes scope and owner from the same
defaults mount uses; a wrong guess lands on the careful side, as a
mismatch. No recovery can make adopt delete anything: adopt itself
deletes nothing, and a mismatched record entry only makes the later
reversal more careful.

One bound the record cannot recover: the fields only the original
mount could measure (the pre-mount hash, the spliced region, an added
trailing newline). An adopted AGENTS.md, .codex/config.toml or
.mcp.json therefore reverses by excision or stays host-owned; the
restored-to-pre-mount verb is out of reach.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path

from radialrig import mount as mnt

# The date the original mount stamped into the seeded records. The seed
# writes `effective: {{DATE}}` into docs/spec.md, so a standing spec
# carries the day its mount ran. An edited or absent field falls back to
# today, and a wrong day simply fails the hash comparison.
_EFFECTIVE_RE = re.compile(rb"^effective: (\d{4}-\d{2}-\d{2})\r?$", re.MULTILINE)


def _recovered_day(root: Path) -> str | None:
    spec = root / "docs" / "spec.md"
    if not spec.is_file():
        return None
    found = _EFFECTIVE_RE.search(spec.read_bytes())
    if found is None:
        return None
    day = found.group(1).decode("ascii")
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        return None
    return day


def _day_corroborated(root: Path, context: dict[str, str]) -> bool:
    """Whether a second dated file proves the recovered day.

    The day is read out of docs/spec.md itself, so a hash match on that
    one file is self-referential: a project that re-dated both of its
    date fields to one valid day matches a seed rendered with its own
    edit. docs/spec-git-environment.md carries the same {{DATE}} token,
    and only a mount stamps both files with one day - so spec.md is
    claimed on its match only when the second file matches under the
    same day. An uncorroborated match lands on the careful side.
    """
    other = root / "docs" / "spec-git-environment.md"
    if not other.is_file():
        return False
    entry = next(e for e in mnt.SEED_FILES
                 if e.rel == "docs/spec-git-environment.md")
    rendered = mnt._render(entry.source, context)
    return mnt.matches_recorded(other.read_bytes(), mnt._sha256(rendered))


@dataclass(frozen=True)
class AdoptPlan:
    """What an adopt records here, and what it says about each path."""

    root: Path
    scope: str
    owner: str
    mounted: str   # the recovered day the record carries
    adopted: str   # the day adopt ran
    version: str
    files: tuple[mnt.WrittenFile, ...]
    dirs: tuple[str, ...]
    dispositions: tuple[mnt.Disposition, ...]

    @property
    def claimed(self) -> int:
        """Paths the record claims as the mount's own, hash-proven. A
        path the project holds on its own (HOST_HOLDS) is neither
        claimed nor kept."""
        return sum(1 for d in self.dispositions
                   if d.verb == mnt.CURRENT and d.code != mnt.HOST_HOLDS)

    @property
    def kept(self) -> int:
        """Paths recorded as this project's own, or left unrecorded."""
        return sum(1 for d in self.dispositions if d.verb == mnt.LEFT)


def _adopt_created(target: Path, entry: mnt.SeedEntry, context: dict[str, str]):
    if target.is_dir():
        return None, mnt.Disposition(entry.rel, mnt.LEFT, mnt.LEFT_NOT_A_FILE)
    if not target.is_file():
        return None, mnt.Disposition(entry.rel, mnt.ABSENT, mnt.ADOPT_ABSENT)
    data = mnt._render(entry.source, context) if entry.source else b""
    seed_hash = mnt._sha256(data)
    # As it stands, or with CRLF read as LF (BUG-0011): a checkout that
    # rewrote the endings and changed no word is the mount's own file.
    matched = mnt.matches_recorded(target.read_bytes(), seed_hash)
    # The recorded hash is the seed's, never the standing file's: a file
    # that differs must read as edited to every later verb, so unmount
    # leaves it and upgrade shows the diff.
    recorded = mnt.WrittenFile(
        path=entry.rel, mode=mnt.CREATED, sha256=seed_hash, file_created=True,
    )
    code = mnt.ADOPT_MATCHED if matched else mnt.ADOPT_EDITED
    verb = mnt.CURRENT if matched else mnt.LEFT
    return recorded, mnt.Disposition(entry.rel, verb, code)


def _adopt_agents(target: Path, context: dict[str, str], rel: str = mnt.AGENTS_REL):
    """One appended seed file: the route map in AGENTS.md, or the server
    tables in .codex/config.toml. `rel` picks the fence pair and source."""
    if target.is_dir():
        return None, mnt.Disposition(rel, mnt.LEFT, mnt.LEFT_NOT_A_FILE)
    if not target.is_file():
        return None, mnt.Disposition(rel, mnt.ABSENT, mnt.ADOPT_ABSENT)
    data = target.read_bytes()
    found = mnt._locate_section(data, rel)
    if found is None:
        begin = mnt.FENCES[rel][0].encode("utf-8")
        code = mnt.ADOPT_FENCES_BROKEN if begin in data else mnt.ADOPT_NO_FENCE
        return None, mnt.Disposition(rel, mnt.LEFT, code)
    section = mnt._section_bytes(context, rel)
    start, end = found
    # The section is the seed's as it stands, or with CRLF read as LF
    # after a checkout rewrote the endings (BUG-0011). The prefix check
    # reads the same way. The recorded hash is the LF digest, as every
    # record keeps it, so a later checkout that puts LF back still
    # matches; the file's own bytes are what unmount finds standing.
    matched = mnt._lf(data[start:end]) == section
    if matched and not (start == 0
                        or mnt._lf(data[max(0, start - 4):start]).endswith(b"\n\n")):
        # The section is the seed's, but the bytes in front of the fence
        # are a shape no mount leaves: a mount puts the fence at the
        # start of the file or behind two newlines (the host's last byte
        # and its own separator). A record here would let unmount excise
        # through a prefix the section hash never proved, so nothing is
        # recorded and the file stays this project's own.
        return None, mnt.Disposition(rel, mnt.LEFT, mnt.ADOPT_SECTION_UNPROVEN)
    recorded = mnt.WrittenFile(
        path=rel, mode=mnt.APPENDED, sha256=mnt._sha256(mnt._lf(data)),
        # file_created marks a file that is nothing but the section, the
        # one shape unmount may remove whole.
        file_created=(mnt._lf(data) == section),
        section_sha256=mnt._sha256(section),
    )
    code = mnt.ADOPT_SECTION_MATCHED if matched else mnt.ADOPT_SECTION_EDITED
    verb = mnt.CURRENT if matched else mnt.LEFT
    return recorded, mnt.Disposition(rel, verb, code)


def _adopt_mcp(target: Path):
    rel = mnt.MCP_REL
    if target.is_dir():
        return None, mnt.Disposition(rel, mnt.LEFT, mnt.LEFT_NOT_A_FILE)
    if not target.is_file():
        return None, mnt.Disposition(rel, mnt.ABSENT, mnt.ADOPT_ABSENT)
    raw = target.read_bytes()
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, mnt.Disposition(rel, mnt.LEFT, mnt.ADOPT_MCP_UNPARSED)
    servers = loaded.get("mcpServers", {}) if isinstance(loaded, dict) else None
    if not isinstance(servers, dict):
        return None, mnt.Disposition(rel, mnt.LEFT, mnt.ADOPT_MCP_UNPARSED)
    if mnt.SERVER_KEY not in servers:
        return None, mnt.Disposition(rel, mnt.LEFT, mnt.ADOPT_MCP_NO_KEY)
    entry = mnt._mcp_entry()
    if servers[mnt.SERVER_KEY] == entry:
        # The keys are claimed only on the same proof every other claim
        # takes: the whole file matches a file a mount creates fresh, as
        # it stands or with CRLF read as LF (BUG-0011). Two such files
        # exist: the one written today,
        # holding both servers, and the one a mount wrote before the
        # write server was registered, holding the read server alone.
        # The record claims exactly the keys the file holds, so a later
        # upgrade adds the one it lacks. A matching entry inside any
        # other file cannot be told from one the host registered itself
        # before the lost mount (the lost record would have marked that
        # host-owned), so it lands host-owned, the careful side, and
        # unmount leaves it.
        for present in (mnt._mcp_entries(), {mnt.SERVER_KEY: entry}):
            fresh = mnt._fresh_mcp_bytes(present)
            if not mnt.matches_recorded(raw, mnt._sha256(fresh)):
                continue
            recorded = mnt.WrittenFile(
                path=rel, mode=mnt.MERGED, sha256=mnt._sha256(mnt._lf(raw)),
                file_created=True,
                keys=tuple(f"mcpServers.{k}" for k in present),
                written=True, entry=entry, entries=present,
            )
            return recorded, mnt.Disposition(rel, mnt.CURRENT,
                                             mnt.ADOPT_MCP_SEED_ENTRY)
        recorded = mnt.WrittenFile(
            path=rel, mode=mnt.MERGED, sha256=mnt._sha256(mnt._lf(raw)),
            file_created=False, keys=(), written=False,
        )
        return recorded, mnt.Disposition(rel, mnt.LEFT, mnt.ADOPT_MCP_UNPROVEN)
    # A key this shape did not write is the project's own: the record
    # marks it host-owned, and unmount leaves it standing. The hash is
    # the LF digest, as every record keeps it (BUG-0011).
    recorded = mnt.WrittenFile(
        path=rel, mode=mnt.MERGED, sha256=mnt._sha256(mnt._lf(raw)),
        file_created=False, keys=(), written=False,
    )
    return recorded, mnt.Disposition(rel, mnt.LEFT, mnt.ADOPT_MCP_PROJECT)


def adopt_plan(
    root: Path,
    *,
    scope: str | None = None,
    owner: str = mnt.DEFAULT_OWNER,
    today: dt.date | None = None,
) -> AdoptPlan:
    """What `adopt` would record here. Writes nothing. A file error while
    reading is `mount.WriteStopped` with nothing left."""
    root = root.resolve()
    try:
        return _adopt_plan(root, scope=scope, owner=owner, today=today)
    except OSError as exc:
        raise mnt._stopped(exc, root=root) from exc


def _adopt_plan(root: Path, *, scope, owner, today) -> AdoptPlan:
    if mnt.is_mounted(root):
        raise mnt.AlreadyMounted(root)
    reserved = root / ".radialrig"
    if reserved.exists() and not reserved.is_dir():
        # The rebuilt record needs .radialrig/ as a directory. Refusing
        # here, in the plan, keeps --dry-run and the real run in step.
        raise mnt.MountError(
            ".radialrig stands here as a file, and the rebuilt record "
            "needs that name for its directory; nothing was written"
        )
    scope = scope or mnt.default_scope(root)
    day = (today or dt.date.today()).isoformat()
    mounted = _recovered_day(root) or day
    context = {
        "SCOPE": scope,
        "OWNER": owner,
        "DATE": mounted,
        "TOOL_CMD": mnt.SERVER_COMMAND,
        "WRITE_TOOL_CMD": mnt.WRITE_SERVER_COMMAND,
    }
    corroborated = _day_corroborated(root, context)

    files: list[mnt.WrittenFile] = []
    rows: list[mnt.Disposition] = []
    for entry in mnt.SEED_FILES:
        target = root / entry.rel
        if entry.mode == mnt.CREATED:
            recorded, row = _adopt_created(target, entry, context)
            if (entry.rel == "docs/spec.md" and row.verb == mnt.CURRENT
                    and not corroborated):
                # The day this match holds under was read out of this
                # file's own effective line (_day_corroborated).
                recorded = None
                row = mnt.Disposition(entry.rel, mnt.LEFT,
                                      mnt.ADOPT_DATE_UNPROVEN)
        elif entry.mode == mnt.APPENDED:
            if mnt._host_holds(root, entry.rel):
                # The mount left this file alone because the project
                # already does what the section would (HOST_HOLDS), so
                # there is nothing here to claim and nothing kept.
                recorded, row = None, mnt.Disposition(
                    entry.rel, mnt.CURRENT, mnt.HOST_HOLDS)
            else:
                recorded, row = _adopt_agents(target, context, entry.rel)
        else:
            recorded, row = _adopt_mcp(target)
        if recorded is not None:
            files.append(recorded)
        rows.append(row)

    # An empty file matching an empty seed carries zero bits of
    # evidence, and mount itself refuses on the same standing file. Such
    # a claim rides only with at least one contentful claim beside it; a
    # plan whose every claim is empty-for-empty records none of them.
    empty_rels = {e.rel for e in mnt.SEED_FILES
                  if e.mode == mnt.CREATED and not e.source}
    claimed_rels = {d.path for d in rows if d.verb == mnt.CURRENT}
    if claimed_rels and claimed_rels <= empty_rels:
        files = [f for f in files if f.path not in claimed_rels]
        rows = [mnt.Disposition(d.path, mnt.LEFT, mnt.ADOPT_EMPTY_UNPROVEN)
                if d.path in claimed_rels and d.verb == mnt.CURRENT else d
                for d in rows]

    # BUG-0002 (owner ruling 2026-09-05). A record needs evidence that a
    # mount ran here: a row from a seeded doc file, which only a mount
    # writes, edited or not; the fenced route map in AGENTS.md, eighty
    # lines only a mount writes and which mount refuses to write twice,
    # edited or not; or a hash-proven claim on any path. A .mcp.json key
    # adopt cannot tie to a mount is none of these: a project that
    # registered the key itself and never mounted shows the same row.
    # Neither is a fenced CLAUDE.md or .codex/config.toml on its own:
    # those regions are a few lines a project can carry without a mount,
    # and a record built on one would let unmount delete the project's
    # own file. Standing alone, such rows write no record, and the tree
    # gets the nothing-to-adopt answer, so mount still works here. A
    # lost mount whose every seeded file was edited still records its
    # doc rows and its section, and keeps its record.
    seeded_rels = {e.rel for e in mnt.SEED_FILES if e.mode == mnt.CREATED}
    seeded_rels.add(mnt.AGENTS_REL)
    short_regions = {e.rel for e in mnt.SEED_FILES
                     if e.mode == mnt.APPENDED} - {mnt.AGENTS_REL}
    claimed_rels = {d.path for d in rows
                    if d.verb == mnt.CURRENT and d.code != mnt.HOST_HOLDS
                    and d.path not in short_regions}
    if not any(f.path in seeded_rels or f.path in claimed_rels for f in files):
        files = []

    dirs: tuple[str, ...] = ()
    if files:
        dirs = mnt._planned_dirs(root, (mnt.MANIFEST_REL,))
        rows.append(mnt.Disposition(mnt.MANIFEST_REL, mnt.CREATED,
                                    mnt.ADOPT_RECORD_WRITTEN))
    return AdoptPlan(
        root=root, scope=scope, owner=owner, mounted=mounted, adopted=day,
        version=mnt.installed_version(),
        files=tuple(sorted(files, key=lambda f: f.path)), dirs=dirs,
        dispositions=tuple(sorted(rows, key=lambda d: d.path)),
    )


def _record_bytes(plan: AdoptPlan) -> bytes:
    """The rebuilt manifest. Same shape a mount writes, plus one field:
    `adopted` names the day this record was rebuilt from evidence rather
    than written by the mount it describes."""
    body = {
        "schema": mnt.MANIFEST_SCHEMA,
        "radialrig": plan.version,
        "mounted": plan.mounted,
        "adopted": plan.adopted,
        "scope": plan.scope,
        "owner": plan.owner,
        "hash": mnt.HASH_NAME,
        "dirs": list(plan.dirs),
        "files": [f.as_json() for f in plan.files],
    }
    return (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def adopt(
    root: Path,
    *,
    scope: str | None = None,
    owner: str = mnt.DEFAULT_OWNER,
    today: dt.date | None = None,
) -> AdoptPlan:
    """Write the rebuilt record, and nothing else.

    A plan that recorded no file writes nothing at all: with no standing
    seeded content, and no proven claim, the right verb is mount, and
    the caller renders that answer from the empty plan.
    """
    plan = adopt_plan(root, scope=scope, owner=owner, today=today)
    if not plan.files:
        return plan
    # Through the mount kit's own writer, so a file error is its
    # `WriteStopped` here as everywhere else.
    mnt._write_all(plan.root, {mnt.MANIFEST_REL: _record_bytes(plan)})
    return plan
