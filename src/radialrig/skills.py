"""The skills kit: add and remove packaged agent skills on a host project.

D-36 and PLAN-0006 Phase 2. A skill is an instruction document a harness
reads, so an installed skill lands where its harness looks — behind the
map in `HARNESSES`, one row per harness. No function below branches on a
harness name: every one of them takes a `Harness`, and a second harness
costs one entry in that map. The row carries the plain name a page or a
report shows for it, so no sentence anywhere has to (PLAN-0015).

The kit borrows the mount's ethics wholesale and its lifecycle not at
all. Borrowed: a closed report vocabulary (`mount.VERBS`, the reason
codes, `mount.reason_text`), all-or-nothing writes, and the rule that
radialrig never overwrites or deletes a byte it did not itself write.
Not borrowed: the manifest. The skills record sits at the harness's own
path, beside the scanned skills directory rather than inside it, and
nothing in `mount.py` knows it exists — which is why a docs unmount on a
project carrying an installed skill still exits 0 with an empty tree.
The dependency runs one way: this module imports `mount`, never the
reverse.

Two rules divide the two directions of the kit. `add` reads the package:
`SKILL_CATALOG` below governs an install the way `SEED_FILES` governs a
mount. `remove` reads the record: a skill dropped from a later release
stays removable, and removal follows the paths the record names rather
than the paths today's map would compute. The record is the only bridge
between the two, and it carries hashes, never bytes — no field of it
ever holds a byte the host wrote.

No skill source carries `{{TOKEN}}` substitution. The bytes a host
receives are the bytes the package ships, modulo `mount._lf`, so
sha256(packaged) == sha256(installed) holds in every host and the record
can be checked against the package directly.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from radialrig import mount as mnt
from radialrig.paths import packaged_skills

SKILLS_SCHEMA = 1


# --- the harness map (D-36 rule 3) ------------------------------------

@dataclass(frozen=True)
class Harness:
    """Where one harness reads skills, and where the kit records what it
    wrote there.

    `record` sits beside `skills`, not inside it: a harness scans its
    skills directory, and a bookkeeping file inside that directory would
    be read as a skill. Because the record lives inside the harness's own
    tree, a second harness gets its own record for free.
    """

    name: str      # echoed into the record; the `--harness` spelling
    label: str     # the plain name a page or a report shows; never the key
    skills: str    # root-relative posix dir holding one directory per skill
    record: str    # root-relative posix path of this harness's record


_CLAUDE_CODE = Harness(
    name="claude-code",
    label="Claude Code",
    skills=".claude/skills",
    record=".claude/radialrig-skills.json",
)

# Codex reads project skills from `.agents/skills/` (PLAN-0013 phase 2);
# Claude Code reads only `.claude/skills/`, so the two trees never meet.
_CODEX = Harness(
    name="codex",
    label="Codex",
    skills=".agents/skills",
    record=".agents/radialrig-skills.json",
)

# The map is the only place a harness name is written. A test pins that:
# code that branched on a name would be harness-specific engine, which
# D-34 rule 2 forbids.
HARNESSES: dict[str, Harness] = {
    _CLAUDE_CODE.name: _CLAUDE_CODE,
    _CODEX.name: _CODEX,
}
DEFAULT_HARNESS = _CLAUDE_CODE.name


def default_harness() -> Harness:
    """The harness a single-harness call acts on when none is named.

    The surfaces do not call the single-harness functions bare: `skill
    add`, `skill remove` and `skill list` act on every harness in the
    map through the `*_all` functions below, and `--harness` narrows
    them to one. The default stands for callers that name none.
    """
    return HARNESSES[DEFAULT_HARNESS]


def harness_for(name: str) -> Harness:
    """The map's entry for `name`, or UnknownHarness."""
    found = HARNESSES.get(name)
    if found is None:
        raise UnknownHarness(name, tuple(HARNESSES))
    return found


def harnesses(names=None) -> tuple[Harness, ...]:
    """Every harness in the map, in map order, or the named ones in the
    order given. The surfaces resolve `--harness` through this."""
    if names is None:
        return tuple(HARNESSES.values())
    return tuple(harness_for(n) for n in names)


# --- the catalog ------------------------------------------------------

@dataclass(frozen=True)
class SkillEntry:
    """One line of the skill table: what it is called, what it ships."""

    name: str                  # slug; == directory name; the CLI argument
    version: str               # semver; == frontmatter radialrig.version
    summary: str               # one ASCII line, <= 80 chars, for `skill list`
    files: tuple[str, ...]     # skill-dir-relative posix paths


# The skills, as an explicit list. A directory walk over data/skills/
# would silently ship a stray file dropped there; a test asserts this
# list and that directory agree in BOTH directions (README.txt aside).
SKILL_CATALOG: tuple[SkillEntry, ...] = (
    SkillEntry(
        name="session-handoff",
        version="3.3.0",
        summary="End of session sweep, a handoff file, and a kickoff prompt.",
        files=("SKILL.md", "scripts/handoff.py"),
    ),
    SkillEntry(
        name="plan-review",
        version="2.1.0",
        summary="Reviews a plan as a plan: files its absences, never fills them.",
        files=("SKILL.md",),
    ),
    SkillEntry(
        name="promotion-gate",
        version="3.1.0",
        summary="A trunk push is a promotion: verified, then approved, each time.",
        files=("SKILL.md",),
    ),
    SkillEntry(
        name="toolkit-standards",
        version="3.2.0",
        summary="Tool precedence: repo convention, pinned default, declared gap.",
        files=("SKILL.md", "reference/languages.md"),
    ),
    SkillEntry(
        name="memory-keeper",
        version="1.0.0",
        summary="Routes each memory note to its home and keeps the store worth reading.",
        files=("SKILL.md", "scripts/audit.py"),
    ),
)


def catalog_names() -> tuple[str, ...]:
    return tuple(entry.name for entry in SKILL_CATALOG)


def catalog_entry(name: str) -> SkillEntry | None:
    for entry in SKILL_CATALOG:
        if entry.name == name:
            return entry
    return None


# --- errors -----------------------------------------------------------

class SkillError(RuntimeError):
    """Anything that stops a skill add or remove."""


class UnknownSkill(SkillError):
    """A name this package does not ship."""

    def __init__(self, name: str, available: tuple[str, ...]) -> None:
        offer = ", ".join(available) if available else "none"
        super().__init__(
            f"this package ships no skill named '{name}'; it ships: {offer}"
        )
        self.name = name
        self.available = tuple(available)


class UnknownHarness(SkillError):
    """A harness name the map does not carry."""

    def __init__(self, name: str, available: tuple[str, ...]) -> None:
        offer = ", ".join(available) if available else "none"
        super().__init__(
            f"this package knows no harness named '{name}'; it knows: {offer}"
        )
        self.name = name
        self.available = tuple(available)


class SkillConflict(SkillError):
    """A target that blocks an all-or-nothing write."""

    def __init__(self, conflicts: tuple[mnt.Disposition, ...]) -> None:
        super().__init__(
            "skill add would overwrite content this project owns: "
            + ", ".join(d.path for d in conflicts)
        )
        self.conflicts = conflicts


# --- packaged bytes ---------------------------------------------------

def _skill_bytes(name: str, rel: str) -> bytes:
    """The packaged bytes of one skill file, LF-normalized.

    Mirrors `mount._seed_bytes` and skips its rendering step: a skill
    source is shipped verbatim, so there is no substitution to leave a
    token behind.
    """
    base = packaged_skills()
    if base is None:
        raise SkillError("this installation ships no skill sources")
    path = base.joinpath(name, *rel.split("/"))
    if not path.is_file():
        raise SkillError(f"skill source '{name}/{rel}' is missing from the package")
    return mnt._lf(path.read_bytes())


def _skill_bytes_or_none(name: str, rel: str) -> bytes | None:
    """The same bytes, for a read that must not fail a report.

    `skill list` answers on any installation, including one whose package
    data did not ship; a missing source there means "does not match the
    package", not an error.
    """
    try:
        return _skill_bytes(name, rel)
    except SkillError:
        return None


# --- the record -------------------------------------------------------

def record_path(root: Path, harness: Harness) -> Path:
    return root / harness.record


def empty_record(harness: Harness) -> dict:
    return {
        "schema": SKILLS_SCHEMA,
        "radialrig": mnt.installed_version(),
        "harness": harness.name,
        "hash": mnt.HASH_NAME,
        "dirs": [],
        "skills": [],
    }


def read_record(root: Path, harness: Harness | None = None) -> dict | None:
    """The record as JSON, or None when this project carries none.

    Every read goes through the guards below, because the record is a
    file the host can edit and the kit acts on the paths it names.
    """
    harness = harness or default_harness()
    path = record_path(root, harness)
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _unreadable(harness.record, exc) from exc
    except (ValueError, RecursionError) as exc:
        # As in `mount.read_manifest`: JSONDecodeError and
        # UnicodeDecodeError are ValueErrors, so a record in any other
        # encoding, a number past Python's integer limit and text nested
        # past the reader's depth all get the same refusal rather than a
        # traceback (PLAN-0022 phase 3).
        raise SkillError(
            f"{harness.record} does not parse as JSON: {exc}; nothing was written"
        ) from exc
    return _readable_record(root, harness, body)


def _unreadable(rel: str, exc: OSError) -> SkillError:
    """The kit's error for a file that will not read, in the writer's
    own sentence: the path, the system's words, and that nothing was
    written, which is true of every read."""
    return SkillError(mnt.stopped_sentence(rel, exc.strerror or str(exc), ()))


def _read_bytes(root: Path, rel: str) -> bytes:
    """One project file's bytes, or the kit's own error. Every read the
    verbs make of a file the record names goes through here, so a lock
    on any of them is one sentence and never a trace."""
    try:
        return (root / rel).read_bytes()
    except OSError as exc:
        raise _unreadable(rel, exc) from exc


def _readable_record(root: Path, harness: Harness, body) -> dict:
    """Refuse a record this build cannot act on, before any write.

    Three guards, in the order a wrong record does damage: a schema this
    build does not know, a path that leaves the project, and a path that
    leaves the harness's own skills directory. The third is what stops a
    record naming `docs/spec.md` from turning `skill remove` into a
    deletion of the corpus. It reads the path two ways, because a spelling
    such as `.claude/skills/x/../../../docs/spec.md` starts under the
    skills directory and resolves to the rulebook: no part of a recorded
    path may be `..`, and the resolved path must lie beneath the resolved
    skills directory. A recorded `dirs` entry may name an ancestor of the
    skills directory (the kit created `.claude` on the way there), so it
    takes only the `..` test.

    A skill's name is checked too, before any path: a record that names
    no `dir` has its folder inferred from the name, so the name must be
    one folder name, and the inferred folder takes the same checks as a
    recorded one (PLAN-0022 phase 2, SKL-002). Every skill in the record
    is checked, not only the one asked for.
    """
    if not isinstance(body, dict):
        raise SkillError(f"{harness.record} does not hold a JSON object")
    schema = body.get("schema")
    if not isinstance(schema, int):
        raise SkillError(f"{harness.record} carries no readable schema number")
    if schema > SKILLS_SCHEMA:
        raise SkillError(
            f"this skills record was written by a newer radialrig "
            f"(schema {schema}); install that version, or remove "
            f"{harness.skills}/ and {harness.record} yourself"
        )

    # Every entry is checked as the record holds it: the lookups below
    # pass over an entry whose name is not text, so a check made after
    # them would never see one (PLAN-0022 phase 3).
    raw = body.get("skills")
    for entry in raw if isinstance(raw, list) else []:
        if not (isinstance(entry, dict) and isinstance(entry.get("name"), str)):
            raise SkillError(
                f"{harness.record} holds a skill entry whose name is not "
                f"text; nothing was changed"
            )

    inside: list[str] = []
    dirs = body.get("dirs")
    if isinstance(dirs, list):
        recorded_dirs = [d for d in dirs if isinstance(d, str)]
    else:
        recorded_dirs = []
    for entry in _record_skills(body):
        name = entry["name"]
        if not _single_folder_name(name):
            raise SkillError(
                f"{harness.record} names a skill '{mnt.one_line(str(name))}' "
                f"that is not a single folder name; nothing was changed"
            )
        target = entry.get("dir")
        if isinstance(target, str):
            inside.append(target)
        if not (isinstance(target, str) and target):
            # `_state` infers the folder from the name here.
            inside.append(f"{harness.skills}/{name}")
        for item in entry.get("files", []):
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                inside.append(item["path"])

    try:
        mnt._guard_paths(root, [*inside, *recorded_dirs], record_rel=harness.record)
    except mnt.MountError as exc:
        # One wording, two kits: the guard is shared, the error type is
        # each kit's own so a caller catches what it expects.
        raise SkillError(str(exc)) from exc

    for rel in inside:
        if (_climbs(rel) or not _within(rel, harness.skills)
                or _norm(rel) == _norm(harness.record)
                or not _beneath_resolved(root, rel, harness.skills)):
            raise SkillError(
                f"{harness.record} names a path outside {harness.skills}: "
                f"'{mnt.one_line(rel)}'; nothing was changed"
            )
    for rel in recorded_dirs:
        # A recorded directory is one the kit created on the way to the
        # skills directory, so it is that directory, an ancestor of it,
        # or something beneath it. Anything else is not the kit's.
        norm = _norm(rel)
        if _climbs(rel) or not (
                _within(rel, harness.skills) or _norm(harness.skills) == norm
                or _within(harness.skills, rel)):
            raise SkillError(
                f"{harness.record} names a path outside {harness.skills}: "
                f"'{mnt.one_line(rel)}'; nothing was changed"
            )
    return body


def _single_folder_name(name: str) -> bool:
    """Whether a recorded skill name names one folder and no path.

    Not empty, not `.` or `..`, no `/` or `\\`, no colon, no trailing dot
    or space (Windows drops those, so `.. ` reads as `..`), and no NUL,
    which no file system takes. A name today's catalog lacks still
    passes, so a skill an older release installed still removes.
    """
    return (bool(name) and name not in (".", "..")
            and not any(c in name for c in "/\\:\0")
            and not name.endswith((".", " ")))


def _record_skills(body: dict) -> list[dict]:
    entries = body.get("skills")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and isinstance(e.get("name"), str)]


def _recorded(body: dict | None, name: str) -> dict | None:
    if body is None:
        return None
    for entry in _record_skills(body):
        if entry["name"] == name:
            return entry
    return None


def _norm(rel: str) -> str:
    """One spelling per path: forward slashes, no empty or `.` parts.
    Every comparison below runs on this form, and so does the on-disk
    scan in `_state`, so `.claude/skills/./x` and `.claude/skills/x`
    name the same file to both."""
    parts = rel.replace("\\", "/").split("/")
    return "/".join(p for p in parts if p not in ("", "."))


def _ancestors(rel: str) -> tuple[str, ...]:
    """Every directory between the project root and `rel`, outermost
    first. Used to refuse a target whose parent is not a directory."""
    parts = _norm(rel).split("/")
    return tuple("/".join(parts[:i]) for i in range(1, len(parts)))


def _within(rel: str, parent: str) -> bool:
    """True when `rel` lies strictly beneath `parent`, posix-wise."""
    inner, outer = _norm(rel), _norm(parent)
    return inner.startswith(outer + "/")


def _climbs(rel: str) -> bool:
    """True when any part of `rel` is `..`, or a spelling Windows reads
    as `..` (`.. `, `...`: trailing dots and spaces are dropped by the
    file system, not by a string test). The string test above reads
    `.claude/skills/x/../../../docs/spec.md` as beneath the skills
    directory; the disk reads it as the rulebook."""
    return any(part.strip(" .") == "" for part in _norm(rel).split("/"))


def _beneath_resolved(root: Path, rel: str, parent: str) -> bool:
    """`_within`, as the disk would read it: the resolved form of `rel`
    lies strictly beneath the resolved form of `parent`."""
    target = (root / _norm(rel)).resolve()
    outer = (root / _norm(parent)).resolve()
    try:
        target.relative_to(outer)
    except ValueError:
        return False
    return target != outer


def _recorded_files(entry: dict | None) -> tuple[tuple[str, str], ...]:
    """(path, sha256) for each file the record names, in recorded order."""
    if not entry:
        return ()
    out: list[tuple[str, str]] = []
    for item in entry.get("files", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            out.append((item["path"], str(item.get("sha256", ""))))
    return tuple(out)


# --- the state model --------------------------------------------------

@dataclass(frozen=True)
class FileState:
    """One file of one skill, against the record and against the package."""

    path: str            # root-relative posix
    rel: str | None      # skill-dir-relative posix, None when not packaged
    on_disk: bool
    matches_record: bool
    matches_package: bool
    recorded: bool


@dataclass(frozen=True)
class SkillState:
    """Everything the three verbs need to decide, computed once.

    `list`, `add`, `remove` and the setup page all read this, so a state
    the report names is the state the write acts on.
    """

    name: str
    harness: Harness
    packaged: SkillEntry | None
    record_entry: dict | None
    dir_rel: str
    dir_exists: bool
    dir_is_file: bool
    dir_empty: bool
    files: tuple[FileState, ...]
    extra: tuple[str, ...]     # files inside dir_rel the record does not name

    @property
    def recorded_files(self) -> tuple[FileState, ...]:
        return tuple(f for f in self.files if f.recorded)

    @property
    def installed_version(self) -> str:
        if self.record_entry and isinstance(self.record_entry.get("version"), str):
            return self.record_entry["version"]
        return ""

    @property
    def dir_created(self) -> bool:
        """Whether the kit created the skill directory. A pre-existing
        empty directory is adopted, never claimed: removal leaves it."""
        if self.record_entry and isinstance(self.record_entry.get("dir_created"), bool):
            return self.record_entry["dir_created"]
        return not self.dir_exists

    @property
    def clean(self) -> bool:
        files = self.recorded_files
        return bool(files) and all(f.on_disk and f.matches_record for f in files)

    @property
    def edited(self) -> bool:
        return any(f.on_disk and not f.matches_record for f in self.recorded_files)

    @property
    def current(self) -> bool:
        if self.packaged is None or not self.clean:
            return False
        declared = {f"{self.dir_rel}/{rel}" for rel in self.packaged.files}
        if {f.path for f in self.recorded_files} != declared:
            return False
        return all(f.matches_package for f in self.recorded_files)

    @property
    def stale_record(self) -> bool:
        files = self.recorded_files
        return bool(files) and not any(f.on_disk for f in files)

    @property
    def foreign(self) -> bool:
        """A directory at the target this kit did not put there."""
        return self.record_entry is None and self.dir_exists and not self.dir_empty

    @property
    def foreign_files(self) -> tuple[str, ...]:
        """Files this project put inside the skill directory at paths this
        package ships, holding bytes that are not the packaged ones. They
        are neither recorded nor edited, so `clean` and `edited` do not
        see them, and an add refuses the whole skill rather than write
        over one: radialrig never overwrites a byte it did not write.
        Bytes that already are the packaged bytes belong to nobody in
        particular and are simply recorded."""
        return tuple(
            f.path for f in self.files
            if f.path in self.extra and not f.matches_package
        )

    def list_disposition(self) -> mnt.Disposition:
        """The one row `skill list` prints for this skill.

        The version rides in `detail`, which `mount.reason_text` fills
        into the wording, so the list needs no renderer of its own.
        """
        shown = self.dir_rel + "/"
        if self.dir_is_file:
            return mnt.Disposition(self.dir_rel, mnt.LEFT, mnt.SKILL_TARGET_NOT_A_DIR)
        if self.foreign:
            return mnt.Disposition(shown, mnt.LEFT, mnt.SKILL_DIR_FOREIGN)
        if self.record_entry is None:
            version = self.packaged.version if self.packaged else ""
            return mnt.Disposition(shown, mnt.ABSENT, mnt.SKILL_NOT_INSTALLED, version)
        if self.packaged is None:
            return mnt.Disposition(shown, mnt.LEFT, mnt.SKILL_NOT_PACKAGED)
        if self.edited:
            return mnt.Disposition(shown, mnt.LEFT, mnt.SKILL_EDITED)
        if self.stale_record:
            return mnt.Disposition(shown, mnt.ABSENT, mnt.SKILL_RECORD_STALE)
        if not self.current:
            # Installed, unedited, and the package ships different bytes:
            # saying "already matches" here would be false in the one
            # state the line exists to distinguish.
            return mnt.Disposition(
                shown, mnt.LEFT, mnt.SKILL_REFRESHABLE, self.installed_version
            )
        return mnt.Disposition(
            shown, mnt.CURRENT, mnt.SKILL_CURRENT, self.installed_version
        )


def skill_state(
    root: Path, name: str, *, harness: Harness | None = None,
    record: dict | None = None,
) -> SkillState:
    """The state of one skill on this project. Reads; writes nothing."""
    harness = harness or default_harness()
    root = Path(root).resolve()
    if record is None:
        record = read_record(root, harness)
    return _state(root, harness, name, record)


def states(root: Path, *, harness: Harness | None = None) -> tuple[SkillState, ...]:
    """Every skill this package ships, then every skill the record names
    that it no longer ships. Nothing is hidden because a release dropped
    it: state 7 is still removable."""
    harness = harness or default_harness()
    root = Path(root).resolve()
    record = read_record(root, harness)
    names = list(catalog_names())
    for entry in _record_skills(record or {}):
        if entry["name"] not in names:
            names.append(entry["name"])
    return tuple(_state(root, harness, name, record) for name in names)


def states_all(root: Path, *, targets=None) -> tuple[SkillState, ...]:
    """`states` for every harness in the map (or the given ones), harness
    after harness. `skill list` prints this: one row per skill per
    harness, each under the directory that harness reads."""
    out: list[SkillState] = []
    for harness in harnesses(targets):
        out.extend(states(root, harness=harness))
    return tuple(out)


def _state(root: Path, harness: Harness, name: str, record: dict | None) -> SkillState:
    packaged = catalog_entry(name)
    entry = _recorded(record, name)
    dir_rel = f"{harness.skills}/{name}"
    if entry and isinstance(entry.get("dir"), str) and entry["dir"]:
        dir_rel = _norm(entry["dir"])
    target = root / dir_rel

    dir_is_file = target.is_file()
    dir_exists = target.is_dir()
    dir_empty = True
    if dir_exists:
        try:
            dir_empty = not any(target.iterdir())
        except OSError:  # pragma: no cover - a directory that will not list
            dir_empty = False

    recorded = _recorded_files(entry)
    recorded_hashes = dict(recorded)
    packaged_rels = {f"{dir_rel}/{rel}": rel for rel in (packaged.files if packaged else ())}

    order = [path for path, _ in recorded]
    order.extend(path for path in packaged_rels if path not in recorded_hashes)

    files: list[FileState] = []
    for path in order:
        on_disk = (root / path).is_file()
        data = _read_bytes(root, path) if on_disk else None
        rel = packaged_rels.get(path)
        # As the file stands, or with CRLF read as LF: a git checkout
        # that rewrote the endings is not an edit (BUG-0011).
        matches_record = bool(
            on_disk and path in recorded_hashes
            and mnt.matches_recorded(data, recorded_hashes[path])
        )
        matches_package = False
        if on_disk and rel is not None:
            theirs = _skill_bytes_or_none(name, rel)
            matches_package = theirs is not None and (
                data == theirs or mnt._lf(data) == theirs
            )
        files.append(FileState(
            path=path, rel=rel, on_disk=on_disk, matches_record=matches_record,
            matches_package=matches_package, recorded=path in recorded_hashes,
        ))

    extra: tuple[str, ...] = ()
    if dir_exists:
        known = set(recorded_hashes)
        extra = tuple(sorted(
            p.relative_to(root).as_posix()
            for p in target.rglob("*")
            if p.is_file() and p.relative_to(root).as_posix() not in known
        ))

    return SkillState(
        name=name, harness=harness, packaged=packaged, record_entry=entry,
        dir_rel=dir_rel, dir_exists=dir_exists, dir_is_file=dir_is_file,
        dir_empty=dir_empty, files=tuple(files), extra=extra,
    )


# --- add --------------------------------------------------------------

@dataclass(frozen=True)
class AddResult:
    """What an add would write, or wrote. One computation, two callers,
    so `--dry-run` cannot drift from the run it describes."""

    root: Path
    harness: Harness
    name: str
    version: str
    installed: str                             # YYYY-MM-DD
    dispositions: tuple[mnt.Disposition, ...]
    payload: tuple[tuple[str, bytes], ...]
    record_after: dict
    dirs: tuple[str, ...]
    current: bool = False
    edited: bool = False
    diffs: tuple[str, ...] = ()
    removals: tuple[str, ...] = ()        # recorded paths this release dropped
    emptied_dirs: tuple[str, ...] = ()    # left holding nothing; deepest first

    @property
    def wrote(self) -> bool:
        return bool(self.payload)

    @property
    def left(self) -> int:
        return sum(1 for d in self.dispositions if d.verb == mnt.LEFT)

    @property
    def complete(self) -> bool:
        return self.left == 0


def add_plan(
    root: Path, name: str, *, harness: Harness | None = None,
    today: dt.date | None = None,
) -> AddResult:
    """What `add` would write here. Writes nothing, raises the same
    refusals, and returns the same result object."""
    return _compose_add(root, name, harness=harness, today=today)


def add(
    root: Path, name: str, *, harness: Harness | None = None,
    today: dt.date | None = None,
) -> AddResult:
    """Install one skill. Compute everything, then write.

    Skill-level all-or-nothing, on two axes. A conflict or a single
    edited file stops the add before the first byte: a v3 SKILL.md beside
    a v2 script is a broken skill, so the files are one unit, not four.
    And the record travels *inside* the payload rather than after it —
    unlike the docs manifest, which is also a corpus-root marker and so
    is written outside the transaction.
    """
    result = _compose_add(root, name, harness=harness, today=today)
    _write_adds((result,))
    return result


def add_all_plan(
    root: Path, name: str, *, targets=None, today: dt.date | None = None,
) -> tuple[AddResult, ...]:
    """What `add_all` would write, harness by harness. Writes nothing."""
    return add_plans(root, (name,), targets=targets, today=today)


def add_plans(
    root: Path, names, *, targets=None, today: dt.date | None = None,
) -> tuple[AddResult, ...]:
    """What one press naming several skills would write, harness by
    harness. Writes nothing; `write_add_plans` writes it.

    The two command-line verbs are the two ends of this: `skill add
    <name>` is one name, `skill add --all` is the catalog. The setup
    page's boxes fall between them — a group with some boxes ticked is
    neither — and they go through the same composer, so the record
    threads through the names of one harness on the page exactly as it
    does on the command line (PLAN-0015 phase 3).
    """
    return _compose_adds(root, tuple(names), targets=targets, today=today)


def _compose_adds(
    root: Path, names, *, targets, today: dt.date | None,
) -> tuple[AddResult, ...]:
    """One result per (harness, name), harness by harness in map order
    and name by name in the order given. Writes nothing.

    The record is threaded through the names of one harness: each
    composition starts from the record the one before it would leave,
    so the last payload's record names every skill of the press. Two
    compositions from the disk's record would each write a record
    naming only their own skill, and the second would erase the first.
    The record's row is reported by the first result that writes it and
    by none after: one file is written per harness, and a report that
    listed it once per skill would say four writes for one.
    """
    root = Path(root).resolve()
    out: list[AddResult] = []
    for h in harnesses(targets):
        record = read_record(root, h) or empty_record(h)
        planned = False
        for name in names:
            result = _compose_add(root, name, harness=h, today=today,
                                  record=record, record_planned=planned)
            record = result.record_after
            planned = planned or result.wrote
            out.append(result)
    return tuple(out)


def write_add_plans(results) -> None:
    """Write what `add_all_plan` composed, every harness in one write.

    One `_write_all` over the union of the payloads, not one per harness:
    the writer is all-or-nothing per call, so a fault in the second tree
    (a permission, a lock) rolls the first back too, and no press leaves
    one harness installed and the other not. A fault is reported as the
    skill kit's own error carrying the writer's own sentence, which says
    what is left, so every surface answers it in words.
    """
    _write_adds(results)


def _write_adds(results) -> None:
    """Every result's payload and removals, in one `_write_all`.

    The removals ride in the same call as the writes (skills-roster-02):
    a file a later release dropped goes as its replacement lands, or
    neither does. A subdirectory of the skill left holding nothing goes
    afterwards without a line of its own, as `remove` takes such a
    directory out: best-effort, since a directory that will not go
    holds no byte of anyone's.
    """
    results = tuple(results)
    if not results:
        return
    payload: dict[str, bytes] = {}
    removals: list[str] = []
    emptied: list[str] = []
    for result in results:
        payload.update(result.payload)
        removals.extend(result.removals)
        emptied.extend(result.emptied_dirs)
    if not payload and not removals:
        return
    root = results[0].root
    try:
        mnt._write_all(root, payload, removals)
    except mnt.WriteStopped as exc:
        raise SkillError(str(exc)) from exc
    for rel in emptied:
        try:
            (root / rel).rmdir()
        except OSError:
            pass


def add_all(
    root: Path, name: str, *, targets=None, today: dt.date | None = None,
) -> tuple[AddResult, ...]:
    """Install one skill for every harness in the map, or the given ones.

    Every harness's plan is composed before the first byte is written,
    and every tree is written in one all-or-nothing write, so a refusal
    or a fault in any tree stops the whole press with nothing changed.
    The surfaces call this, never `add` bare: a project one vendor's
    agent works in is a project the other vendor's agent works in too
    (PLAN-0013 phase 2).
    """
    results = add_all_plan(root, name, targets=targets, today=today)
    write_add_plans(results)
    return results


def add_catalog_plan(
    root: Path, *, targets=None, today: dt.date | None = None,
) -> tuple[AddResult, ...]:
    """What `add_catalog` would write. Writes nothing.

    Every packaged skill for every harness in the map, or the given
    ones. A skill already current for a harness is in the tuple as a
    current result rather than dropped from it, so the report can say
    in one line that it was left as it is.
    """
    return add_plans(root, catalog_names(), targets=targets, today=today)


def add_catalog(
    root: Path, *, targets=None, today: dt.date | None = None,
) -> tuple[AddResult, ...]:
    """Install every packaged skill this project lacks, for every harness
    in the map or the given ones: `skill add --all` (PLAN-0015).

    Composed whole before the first byte and written in one all-or-
    nothing write, as `add_all` is: a refusal for any skill in any tree
    stops the whole press with nothing written. A person who wants
    everything for one agent makes one press, and the rows still let
    anyone pick and choose.
    """
    results = add_catalog_plan(root, targets=targets, today=today)
    write_add_plans(results)
    return results


def _compose_add(
    root: Path, name: str, *, harness: Harness | None, today: dt.date | None,
    record: dict | None = None, record_planned: bool = False,
) -> AddResult:
    """One skill for one harness. `record_planned` says an earlier
    result of the same press already carries the record's row, so this
    one's payload still writes the record and its rows do not name it."""
    harness = harness or default_harness()
    root = Path(root).resolve()
    entry = catalog_entry(name)
    if entry is None:
        raise UnknownSkill(name, catalog_names())

    if record is None:
        record = read_record(root, harness) or empty_record(harness)
    state = _state(root, harness, name, record)
    version = mnt.installed_version()
    day = (today or dt.date.today()).isoformat()

    # A file standing anywhere on the way to the skill directory blocks
    # the install as surely as one standing at it. Caught here, so the
    # refusal is a report rather than an OSError out of the writer.
    blocked = [
        rel for rel in _ancestors(state.dir_rel)
        if (root / rel).exists() and not (root / rel).is_dir()
    ]
    if state.dir_is_file:
        blocked.append(state.dir_rel)
    if blocked:
        raise SkillConflict(tuple(
            mnt.Disposition(rel, mnt.LEFT, mnt.SKILL_TARGET_NOT_A_DIR)
            for rel in blocked
        ))
    if state.foreign:
        raise SkillConflict(
            (mnt.Disposition(state.dir_rel + "/", mnt.LEFT, mnt.SKILL_DIR_FOREIGN),)
        )

    if state.current:
        # Nothing at all, the record included: rewriting a record whose
        # every field would be identical is still a write, and a project
        # that ran the command twice should see no second timestamp.
        dispositions = tuple(sorted(
            (mnt.Disposition(f.path, mnt.CURRENT, mnt.SKILL_CURRENT,
                             state.installed_version)
             for f in state.recorded_files),
            key=lambda d: d.path,
        ))
        return AddResult(
            root=root, harness=harness, name=name, version=entry.version,
            installed=state.record_entry.get("installed", day),
            dispositions=dispositions, payload=(), record_after=record,
            dirs=tuple(record.get("dirs", ())), current=True,
        )

    if state.edited:
        dispositions = tuple(sorted(
            (mnt.Disposition(f.path, mnt.LEFT, mnt.SKILL_EDITED)
             for f in state.recorded_files),
            key=lambda d: d.path,
        ))
        return AddResult(
            root=root, harness=harness, name=name, version=entry.version,
            installed=state.record_entry.get("installed", day),
            dispositions=dispositions, payload=(), record_after=record,
            dirs=tuple(record.get("dirs", ())), edited=True,
            diffs=_edited_diffs(root, state, version),
        )

    # A file this project put inside the skill directory, at a path this
    # package ships, is the project's own (`SkillState.foreign_files`):
    # the two refusals above do not see it, and writing over it would
    # break the rule that radialrig never overwrites a byte it did not
    # write.
    if state.foreign_files:
        raise SkillConflict(_foreign_rows(state))

    payload: dict[str, bytes] = {}
    dispositions = []
    for rel in entry.files:
        path = f"{state.dir_rel}/{rel}"
        payload[path] = _skill_bytes(name, rel)
        if (root / path).is_file():
            dispositions.append(
                mnt.Disposition(path, mnt.UPDATED, mnt.SKILL_REFRESHED)
            )
        else:
            dispositions.append(mnt.Disposition(path, mnt.CREATED, mnt.SKILL_ADDED))

    # A path the record names that this release no longer ships
    # (skills-roster-02). Its bytes still match the record — an edited
    # one stopped the add above with the rest of the skill, since the
    # files are one unit — so it goes in the same write as its
    # replacement, and the tree never holds two versions of one skill.
    # One already gone is dropped from the record, which then matches
    # the disk.
    declared = {_norm(path) for path in payload}
    removals: list[str] = []
    for f in state.recorded_files:
        if _norm(f.path) in declared:
            continue
        if f.on_disk and f.matches_record:
            removals.append(f.path)
            dispositions.append(mnt.Disposition(f.path, mnt.REMOVED, mnt.SKILL_DROPPED))
        elif not f.on_disk:
            dispositions.append(
                mnt.Disposition(f.path, mnt.ABSENT, mnt.ABSENT_ALREADY_GONE)
            )
    emptied = _emptied_dirs(root, state.dir_rel, removals, payload)

    planned = mnt._planned_dirs(root, (*payload, harness.record))
    # The skill's own directory and everything under it belong to the
    # skill entry, which carries dir_created; the record's `dirs` list
    # holds only what the kit created on the way to the skills directory.
    kit_dirs = [
        d for d in planned
        if _norm(d) != _norm(state.dir_rel) and not _within(d, state.dir_rel)
    ]
    dirs = _merge_dirs(record.get("dirs", ()), kit_dirs)

    skill_entry = {
        "name": name,
        "version": entry.version,
        "installed": day,
        "dir": state.dir_rel,
        "dir_created": state.dir_created,
        "files": [
            {"path": path, "sha256": mnt._sha256(payload[path])}
            for path in sorted(payload)
        ],
    }
    others = [e for e in _record_skills(record) if e["name"] != name]
    record_after = {
        "schema": SKILLS_SCHEMA,
        "radialrig": version,
        "harness": harness.name,
        "hash": mnt.HASH_NAME,
        "dirs": list(dirs),
        "skills": sorted([*others, skill_entry], key=lambda e: e["name"]),
    }
    record_existed = record_path(root, harness).is_file()
    payload[harness.record] = mnt._dump_manifest(record_after)
    if not record_planned:
        dispositions.append(mnt.Disposition(
            harness.record,
            mnt.UPDATED if record_existed else mnt.CREATED,
            mnt.SKILL_RECORD_WRITTEN,
        ))

    return AddResult(
        root=root, harness=harness, name=name, version=entry.version,
        installed=day,
        dispositions=tuple(sorted(dispositions, key=lambda d: d.path)),
        payload=tuple(sorted(payload.items())), record_after=record_after,
        dirs=dirs, removals=tuple(sorted(removals)), emptied_dirs=emptied,
    )


def _emptied_dirs(root: Path, dir_rel: str, removals, keeps) -> tuple[str, ...]:
    """Directories inside the skill directory that would hold nothing
    once `removals` are gone and `keeps` are written, deepest first.

    The skill directory's own subdirectories exist because the skill's
    file list needed them; one the new list no longer needs goes with
    the file it held, the way `remove` takes such a directory out.
    """
    gone = {_norm(p) for p in removals}
    kept = {_norm(p) for p in keeps}
    candidates = {
        parent.as_posix()
        for rel in gone
        for parent in Path(rel).parents
        if _within(parent.as_posix(), dir_rel)
    }
    out: list[str] = []
    for rel in sorted(candidates, key=lambda r: (-len(Path(r).parts), r)):
        directory = root / rel
        if not directory.is_dir():
            continue
        held = {
            p.relative_to(root).as_posix() for p in directory.rglob("*") if p.is_file()
        }
        if held <= gone and not any(_within(k, rel) for k in kept):
            out.append(rel)
    return tuple(out)


def _merge_dirs(existing, fresh) -> tuple[str, ...]:
    """Union, outermost first. A second add merges rather than replaces:
    the first add's directories are still the kit's to remove."""
    seen = {_norm(d) for d in existing if isinstance(d, str)}
    seen.update(_norm(d) for d in fresh)
    return tuple(sorted(seen, key=lambda d: (len(Path(d).parts), d)))


def _foreign_rows(state: SkillState) -> tuple[mnt.Disposition, ...]:
    """The rows an add refuses a skill with, one per file this project
    owns at a path the package ships. The add raises them; the refresh
    reports them, since it is not a press that stops."""
    return tuple(
        mnt.Disposition(path, mnt.LEFT, mnt.SKILL_FILE_FOREIGN)
        for path in state.foreign_files
    )


def _edited_diffs(root: Path, state: SkillState, version: str) -> tuple[str, ...]:
    """One unified diff per edited file the package still ships: this
    project's bytes against the packaged ones. What an add and a refresh
    both print for a skill they leave alone."""
    diffs: list[str] = []
    for f in state.recorded_files:
        if f.on_disk and not f.matches_record and f.rel is not None:
            theirs = _skill_bytes_or_none(state.name, f.rel)
            if theirs is not None:
                diffs.append(mnt._diff(
                    f.path, _read_bytes(root, f.path), theirs, version,
                ))
    return tuple(diffs)


# --- refresh ----------------------------------------------------------
# The upgrade's skills half (PLAN-0021 phase 1; D-36 rule 2 as amended
# on 2026-10-02). For each agent, every skill the record lists comes up
# to the packaged version when, and only when, a plain add would write
# it without touching a byte of this project's own: all of its files in
# place, none edited. Everything else the record lists is reported as
# left as it is. The refresh reads the record, never the catalog, for
# what to act on: a skill the project lacks is never added, and one
# whose files the person removed is never put back (Invariant 2).


def behind(state: SkillState) -> bool:
    """Whether an update would write this skill (Decided 6): the package
    ships it, every recorded file is on disk matching the record, the
    bytes are not the packaged ones, and nothing holds the write back.

    A record naming a skill this package no longer ships is not behind:
    nothing can be written. Nor is a copy whose refresh the add would
    refuse because this project put its own file at a path the new
    release ships (`foreign_files`): the update would write nothing
    there either, and a listing that read it as behind would say
    "Update available" after every press. This is exactly the test an
    add passes without a refusal, so `refresh_plan` composes what it
    says and nothing it composes is refused.
    """
    return (
        state.packaged is not None and state.clean and not state.current
        and not state.foreign_files
    )


def behind_states(root: Path, *, targets=None) -> tuple[SkillState, ...]:
    """Every skill, for every harness in the map or the named ones, that
    `behind` says an update would write. Reads; writes nothing."""
    return tuple(s for s in states_all(root, targets=targets) if behind(s))


@dataclass(frozen=True)
class RefreshResult:
    """One skill of one agent under a refresh: what was composed for it,
    or the one row saying why it was left as it is.

    An agent whose record this build cannot read is one result of its
    own, with no skill name and the kit's sentence in `error`: its
    skills were not read, so none is listed under it, and the other
    agents stand alone, each record being its own file.
    """

    harness: Harness
    name: str
    refreshed: AddResult | None            # composed only for a copy behind
    dispositions: tuple[mnt.Disposition, ...]
    diffs: tuple[str, ...] = ()
    edited: bool = False
    error: str = ""

    @property
    def left(self) -> bool:
        return self.refreshed is None

    @property
    def wrote(self) -> bool:
        return self.refreshed is not None and self.refreshed.wrote

    @property
    def pending(self) -> bool:
        """Whether a person has something to read here: a row LEFT or
        ABSENT, the rule the seed half's `pending` count uses, or a
        record that could not be read. A current skill is not pending,
        and a refreshed one has no such row: it was clean, so nothing
        of it was absent, and every row it has was written."""
        return bool(self.error) or any(
            d.verb in (mnt.LEFT, mnt.ABSENT) for d in self.dispositions
        )


def refresh_plan(
    root: Path, *, targets=None, today: dt.date | None = None,
) -> tuple[RefreshResult, ...]:
    """What `refresh` would write, agent by agent in map order and skill
    by skill in list order. Writes nothing.

    Each agent stands alone: a record this build cannot read is that
    agent's one result, carrying the kit's sentence, and the next
    agent's skills still go. The skills behind for one agent go through
    `_compose_adds` as one press, so the record is threaded through them
    and written once; `behind` is exactly the test an add passes without
    a refusal, so none is raised there. A clean older copy the add would
    refuse all the same — this project put its own file where the new
    release ships one — is reported left with the refusal's own rows.
    """
    root = Path(root).resolve()
    out: list[RefreshResult] = []
    for h in harnesses(targets):
        try:
            out.extend(_refresh_agent(root, h, today))
        except SkillError as exc:
            out.append(RefreshResult(
                harness=h, name="", refreshed=None, dispositions=(),
                error=str(exc),
            ))
    return tuple(out)


def _refresh_agent(
    root: Path, harness: Harness, today: dt.date | None,
) -> list[RefreshResult]:
    """One agent's results: every skill its record lists, composed where
    `behind` says so and reported left otherwise."""
    listed = [s for s in states(root, harness=harness) if s.record_entry is not None]
    composable = [s.name for s in listed if behind(s)]
    composed = {
        r.name: r for r in _compose_adds(
            root, composable, targets=(harness.name,), today=today)
    } if composable else {}
    version = mnt.installed_version()
    out: list[RefreshResult] = []
    for state in listed:
        result = composed.get(state.name)
        if result is not None:
            out.append(RefreshResult(
                harness=harness, name=state.name, refreshed=result,
                dispositions=result.dispositions,
            ))
            continue
        dispositions, diffs = _left_rows(root, state, version)
        out.append(RefreshResult(
            harness=harness, name=state.name, refreshed=None,
            dispositions=dispositions, diffs=diffs, edited=state.edited,
        ))
    return out


def _left_rows(
    root: Path, state: SkillState, version: str,
) -> tuple[tuple[mnt.Disposition, ...], tuple[str, ...]]:
    """The rows for a listed skill the refresh leaves, and its diffs.

    A clean older copy that `behind` still refuses is held back by its
    foreign files alone, so the rows are the add's own refusal. Every
    other skill gets the one row `skill list` prints for it, an edited
    copy with the diff of each file against the packaged one.
    """
    if state.packaged is not None and state.clean and not state.current:
        return _foreign_rows(state), ()
    diffs = _edited_diffs(root, state, version) if state.edited else ()
    return (state.list_disposition(),), diffs


def refresh(
    root: Path, *, targets=None, today: dt.date | None = None,
) -> tuple[RefreshResult, ...]:
    """Bring every skill this project already has, and nobody edited, up
    to the packaged version, for every agent in the map or the named
    ones. Composed whole, then written through `write_add_plans` in one
    all-or-nothing write."""
    results = refresh_plan(root, targets=targets, today=today)
    write_add_plans(r.refreshed for r in results if r.refreshed is not None)
    return results


# --- remove -----------------------------------------------------------

@dataclass(frozen=True)
class RemovePlan:
    """What a remove would do here, and what it did."""

    root: Path
    harness: Harness
    name: str
    installed: bool                            # the record names this skill
    dispositions: tuple[mnt.Disposition, ...]
    writes: tuple[tuple[str, bytes], ...]
    removals: tuple[str, ...]
    removable_dirs: tuple[str, ...]            # deepest first
    record_after: dict | None

    @property
    def removed(self) -> int:
        return sum(1 for d in self.dispositions if d.verb == mnt.REMOVED)

    @property
    def left(self) -> int:
        return sum(1 for d in self.dispositions if d.verb == mnt.LEFT)

    @property
    def complete(self) -> bool:
        return self.left == 0


def remove_plan(
    root: Path, name: str, *, harness: Harness | None = None,
) -> RemovePlan:
    """What `remove` would do here. Writes nothing."""
    harness = harness or default_harness()
    root = Path(root).resolve()
    record = read_record(root, harness)
    entry = _recorded(record, name)
    if record is None or entry is None:
        return RemovePlan(
            root=root, harness=harness, name=name, installed=False,
            dispositions=(), writes=(), removals=(), removable_dirs=(),
            record_after=None,
        )

    state = _state(root, harness, name, record)
    dispositions: list[mnt.Disposition] = []
    removals: list[str] = []

    # The record's paths, not today's map: a skill installed by an older
    # release is removed from where that release put it.
    for path, digest in _recorded_files(entry):
        target = root / path
        if target.is_dir():
            dispositions.append(mnt.Disposition(path, mnt.LEFT, mnt.LEFT_NOT_A_FILE))
        elif not target.is_file():
            dispositions.append(
                mnt.Disposition(path, mnt.ABSENT, mnt.ABSENT_ALREADY_GONE)
            )
        elif mnt.matches_recorded(_read_bytes(root, path), digest):
            dispositions.append(mnt.Disposition(path, mnt.REMOVED, mnt.REMOVED_FILE))
            removals.append(path)
        else:
            dispositions.append(
                mnt.Disposition(path, mnt.LEFT, mnt.SKILL_EDITED_KEPT)
            )

    # Files this project put inside the skill directory. Listed, never
    # touched; the count in the directory line covers the rest.
    for path in state.extra[:5]:
        dispositions.append(mnt.Disposition(path, mnt.LEFT, mnt.SKILL_UNRECORDED))

    others = [e for e in _record_skills(record) if e["name"] != name]
    writes: dict[str, bytes] = {}
    record_after: dict | None = None
    if others:
        record_after = dict(record)
        record_after["schema"] = SKILLS_SCHEMA
        record_after["radialrig"] = mnt.installed_version()
        record_after["harness"] = harness.name
        record_after["hash"] = mnt.HASH_NAME
        record_after["skills"] = sorted(others, key=lambda e: e["name"])
        writes[harness.record] = mnt._dump_manifest(record_after)
        dispositions.append(mnt.Disposition(
            harness.record, mnt.UPDATED, mnt.SKILL_RECORD_WRITTEN,
        ))
    else:
        removals.append(harness.record)
        dispositions.append(mnt.Disposition(
            harness.record, mnt.REMOVED, mnt.SKILL_RECORD_DROPPED,
        ))

    dispositions.sort(key=lambda d: d.path)

    dir_dispositions, removable = _directory_plan(
        root, harness, state, record, others, set(removals),
    )
    return RemovePlan(
        root=root, harness=harness, name=name, installed=True,
        dispositions=tuple(dispositions) + dir_dispositions,
        writes=tuple(sorted(writes.items())), removals=tuple(sorted(removals)),
        removable_dirs=removable, record_after=record_after,
    )


def _directory_plan(
    root: Path, harness: Harness, state: SkillState, record: dict,
    others: list[dict], removals: set[str],
) -> tuple[tuple[mnt.Disposition, ...], tuple[str, ...]]:
    """What happens to each directory the kit created, deepest first.

    Three kinds, and the difference is who created them. The skill
    directory's own subdirectories exist because the skill's file list
    needed them, so they go without a line of their own. The skill
    directory goes only when `dir_created` says the kit made it — a
    project that already had an empty one keeps it. The record's `dirs`
    go innermost-first, and a directory still holding another installed
    skill says so rather than reporting a count.
    """
    sub_dirs = {
        parent.as_posix()
        for path, _ in _recorded_files(state.record_entry)
        for parent in Path(path).parents
        if _within(parent.as_posix(), state.dir_rel)
    }
    kit_dirs = [
        _norm(d) for d in record.get("dirs", []) if isinstance(d, str)
    ]
    other_dirs = [
        _norm(e["dir"]) for e in others
        if isinstance(e.get("dir"), str) and e["dir"]
    ]

    candidates = sorted(
        {*sub_dirs, _norm(state.dir_rel), *kit_dirs},
        key=lambda r: (-len(Path(r).parts), r),
    )
    gone = {(root / rel).resolve() for rel in removals}
    emptied: dict[Path, bool] = {}
    dispositions: list[mnt.Disposition] = []
    removable: list[str] = []

    for rel in candidates:
        internal = rel in sub_dirs
        is_skill_dir = rel == _norm(state.dir_rel)
        reported = not internal
        directory = root / rel
        shown = rel + "/"

        if not directory.is_dir():
            emptied[directory.resolve()] = True
            if reported and (not is_skill_dir or state.dir_created):
                dispositions.append(
                    mnt.Disposition(shown, mnt.ABSENT, mnt.ABSENT_ALREADY_GONE)
                )
            continue

        blockers = 0
        try:
            children = list(directory.iterdir())
        except OSError:  # pragma: no cover - a directory that will not list
            children = []
        for child in children:
            if child.is_dir():
                if not emptied.get(child.resolve(), False):
                    blockers += 1
            elif child.resolve() not in gone:
                blockers += 1

        if blockers:
            emptied[directory.resolve()] = False
            if reported:
                shares = any(
                    _within(other, rel) or other == rel for other in other_dirs
                )
                if shares:
                    dispositions.append(
                        mnt.Disposition(shown, mnt.LEFT, mnt.SKILL_DIR_SHARED)
                    )
                else:
                    surviving = sum(
                        1 for p in directory.rglob("*")
                        if p.is_file() and p.resolve() not in gone
                    )
                    dispositions.append(mnt.Disposition(
                        shown, mnt.LEFT, mnt.LEFT_DIR_OCCUPIED, str(surviving),
                    ))
            continue

        if is_skill_dir and not state.dir_created:
            # Empty, and the kit did not make it. It stays, and it says
            # nothing: this is the project's directory, untouched.
            emptied[directory.resolve()] = False
            continue

        emptied[directory.resolve()] = True
        removable.append(rel)
        if reported:
            dispositions.append(mnt.Disposition(shown, mnt.REMOVED, mnt.REMOVED_DIR))

    return tuple(dispositions), tuple(removable)


def remove_all_plan(root: Path, name: str, *, targets=None) -> tuple[RemovePlan, ...]:
    """What `remove_all` would do, harness by harness. Writes nothing."""
    return tuple(remove_plan(root, name, harness=h) for h in harnesses(targets))


def remove_all(root: Path, name: str, *, targets=None) -> tuple[RemovePlan, ...]:
    """Remove one skill from every harness in the map, or the given ones.

    A harness whose record does not name the skill answers `installed
    False` and is left alone; the caller decides whether that is the
    whole answer. Every harness's plan is composed before the first
    byte moves, and every tree is reversed in one all-or-nothing write
    (skills-roster-05): a record the second harness cannot read, or a
    file it holds locked, stops the whole press with every tree as
    found, and the one sentence raised is the whole report — there is
    nothing removed to describe.
    """
    return _apply_removes(remove_all_plan(root, name, targets=targets))


def remove(root: Path, name: str, *, harness: Harness | None = None) -> RemovePlan:
    """Remove one skill from one harness, then report what was left and
    why.

    Per file, not per skill: a file this project changed stays, and the
    rest go. The entry is dropped either way — leaving it would leave a
    record claiming bytes that are now the project's own.
    """
    (plan,) = _apply_removes((remove_plan(root, name, harness=harness),))
    return plan


def _apply_removes(plans) -> tuple[RemovePlan, ...]:
    """Apply every plan's writes and removals in one `_restore_all`,
    then take the directories out, and report each plan as applied.

    The directory pass comes after the one write, per plan: a directory
    that will not go is reported, never fatal (Windows refuses to remove
    a process's working directory), and the rest of the tree is already
    reversed by then.
    """
    plans = tuple(plans)
    installed = [p for p in plans if p.installed]
    if not installed:
        return plans
    root = installed[0].root
    writes: dict[str, bytes] = {}
    removals: list[str] = []
    for plan in installed:
        writes.update(plan.writes)
        removals.extend(plan.removals)
    try:
        mnt._restore_all(root, writes, removals)
    except mnt.WriteStopped as exc:
        raise SkillError(str(exc)) from exc

    out: list[RemovePlan] = []
    for plan in plans:
        if not plan.installed:
            out.append(plan)
            continue
        locked: set[str] = set()
        for rel in plan.removable_dirs:
            try:
                (root / rel).rmdir()
            except OSError:
                locked.add(rel)
        applied: list[mnt.Disposition] = []
        for disposition in plan.dispositions:
            if (disposition.code == mnt.REMOVED_DIR
                    and disposition.path.rstrip("/") in locked):
                applied.append(mnt.Disposition(
                    disposition.path, mnt.LEFT, mnt.LEFT_DIR_LOCKED,
                ))
                continue
            applied.append(disposition)
        out.append(RemovePlan(
            root=root, harness=plan.harness, name=plan.name, installed=True,
            dispositions=tuple(applied), writes=plan.writes,
            removals=plan.removals, removable_dirs=plan.removable_dirs,
            record_after=plan.record_after,
        ))
    return tuple(out)
