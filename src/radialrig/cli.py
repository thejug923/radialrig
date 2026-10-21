"""Umbrella command: `radialrig mount`, `unmount`, `upgrade`, `setup`.

PLAN-0005 Phases 3 and 4. This module owns argument parsing and nothing
else. Every verb, every path and every reason string comes from
`radialrig.mount`, and every sentence around them from
`radialrig.report`, so the local setup page renders the same content from
the same functions rather than a copy of this text (D-34 point 3).

Exit codes:
  0  did what the verb says
  1  environment or argument error (diagnostics go to stderr)
  2  reserved for argparse's own usage errors; never returned deliberately
  3  refusal: mount found conflicts and wrote nothing
  4  no-op report: already mounted, not mounted, nothing to adopt, or a
     skill already current
  5  partial: unmount left something, upgrade printed a diff it did not
     apply, a skill verb left a file this project changed, or adopt left
     paths as this project's own

Exit 5 is not an error. It is the machine-readable form of "read the
list": the command ran to completion and touched only what it was
allowed to touch.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from radialrig import activity_log as al
from radialrig import adopt as ad
from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import orphan
from radialrig import report
from radialrig import roster as ro
from radialrig import skills as sk
from radialrig import update

USAGE = """\
radialrig - governed-record tooling for a project you already have

Commands installed by this package:
  radialrig-read       retrieval server (MCP, stdio)
  radialrig-write      authoring server (MCP, stdio; stops at the working tree)
  radialrig-validate   mechanical checks C1-C7 over the corpus
  radialrig-index      regenerate the research catalogs

  radialrig mount [PATH]     lay the seed onto a project
  radialrig unmount [PATH]   reverse the mount, listing what it leaves
  radialrig upgrade [PATH]   move never-edited seeded files to the installed seed, and the skills this project has
  radialrig upgrade --all    the same for every mounted project in the listing
  radialrig adopt [PATH]     rebuild a lost mount record from the standing files
  radialrig setup [PATH]     the same mount, guided, in a local browser page

  radialrig skill list [PATH]              what this package ships, and what is here
  radialrig skill add <name> [PATH]        install one packaged skill
  radialrig skill add --all [PATH]         install every packaged skill this project lacks, and refresh the ones it has
  radialrig skill remove <name> [PATH]     remove one skill this package installed

  radialrig connect <client> [PATH]             register this project with an MCP client
  radialrig unconnect <client> [PATH]           remove the entry connect wrote
  radialrig unconnect <client> --orphan <key>   remove one entry whose project is gone

  radialrig projects [--porcelain]              list the projects this engine has touched here
  radialrig manage [--no-browser]               open, change and reverse those projects in a local page
  radialrig log                                 where the log is, and its last 20 events

Corpus root: $RADIALRIG_DOCS_ROOT when set, else the nearest ancestor of
the working directory holding docs/spec.md or .radialrig/mount.json.
"""

UPGRADE_EPILOG = """\
The engine half of an upgrade is the package version itself: install a
newer radialrig from the address its release page names, for example
uv tool install "radialrig @ <that wheel address>", then run this
command to move the seeded files this project never edited. Close the
RadialRig app and any manage page before that install: they hold the
executables it replaces.

The same run also brings up to the packaged version every skill this
project already has, for each agent, when all of its files are in place
and none was edited. It never adds a skill, never puts back one whose
files were removed, and never writes a file this project changed; an
edited skill is listed with its diff. --all does the same for every
mounted project in the listing whose folder is there, one report per
project, and names each project it skipped.

Exit code: 0 nothing left for review, 4 not mounted, 5 a seeded file or
a skill was left for review - read the list, 1 unreadable record.
"""

ADOPT_EPILOG = """\
For a project whose seeded files stand while .radialrig/mount.json is
gone - deleted by accident, or lost to a backup or clone that skipped
untracked files. Adopt writes a fresh record and no other byte. A path
is recorded as the mount's own only when its bytes match the installed
seed, proven by hash; every other path is recorded as, or left as,
this project's own, and unmount never removes it.

Exit code: 0 every standing seeded path is claimed, 4 nothing to do,
5 some paths were left as this project's own - read the list,
1 unreadable record.
"""

SETUP_EPILOG = """\
The page runs the same mount planning and mount steps the command line
runs, and prints the same reasons. It writes nothing until you press the one button.
The server binds 127.0.0.1 on a port the system picks, answers one
single-use link, and stops when the walkthrough ends or on Ctrl-C.

Any skill checked on the page is installed by the same press, through the
engine "radialrig skill add" drives. Nothing is checked to begin with.

Exit code: the outcome the page reached, over both kits. 0 done, 3 a
refusal that wrote nothing, 4 nothing to do, 5 something was left,
1 environment error.
"""

SKILL_EPILOG = """\
A skill is an instruction document an agent harness reads. Each one is
installed whole, into its own directory, and tracked by content hash: a
file this project changed is never overwritten and never deleted.

Each verb acts on every agent harness this package knows unless
--harness names one. A skill installed for one and not another is a
state either verb can change, not a fault.

The skills kit and the docs kit are separate lifecycles. Neither verb
here reads or writes the other's record.

Exit code: 0 installed or removed, 3 something already stands at the
target, 4 nothing to do, 5 something was left, 1 unreadable record.
"""

ENGINE_HALF = report.ENGINE_HALF

DRY_RUN_TAIL = report.DRY_RUN_TAIL

OK = report.OK
ERROR = report.ERROR
REFUSED = report.REFUSED
NOOP = report.NOOP
PARTIAL = report.PARTIAL

# The shared renderer. Kept as module names here because this module's
# tests and Phase 3's callers reach for them by these names.
render_block = report.render_block


# What this run has printed through `_emit`. `main` clears it on entry
# and reads it once the runner returns: a run that printed a report ends
# on the engine line, and one that printed none does not (PLAN-0018
# phase 1, Decided 1).
_printed: list[str] = []

# What this run said on stderr, through `_fail`, kept for the same event
# the printed lines go into (PLAN-0018 phase 2).
_failed: list[str] = []


def _emit(lines) -> None:
    lines = list(lines)
    sys.stdout.write("\n".join(lines) + "\n")
    _printed.extend(lines)


def _fail(message: str) -> int:
    line = f"radialrig: {message}"
    sys.stderr.write(line + "\n")
    _failed.append(line)
    return ERROR


def _record_run(args, argv: list[str], root, code: int | None = None, *,
                error: BaseException | None = None) -> None:
    """This run's one event (PLAN-0018 phase 2): the arguments as typed,
    the folder, the exit code, and every line `_emit` and `_fail` wrote.
    The log verb writes none, so reading the log never grows it."""
    if getattr(args, "command", None) == "log":
        return
    al.record(
        al.COMMAND, "radialrig " + " ".join(argv), folder=str(root),
        outcome="" if code is None else f"exit {code}",
        lines=[*_printed, *_failed], error=error,
    )


# --- mount ------------------------------------------------------------

def _cmd_mount(args, root: Path) -> int:
    if mnt.is_mounted(root):
        try:
            manifest = mnt.read_manifest(root)
        except mnt.MountError as exc:
            return _fail(str(exc))
        _emit(report.already_mounted_report(root, manifest))
        ro.record_seen(root, dry_run=args.dry_run)
        return NOOP

    build = mnt.mount_plan if args.dry_run else mnt.mount
    try:
        result = build(root, scope=args.scope, owner=args.owner)
    except mnt.MountConflict as exc:
        _emit(report.conflict_report(exc.conflicts, dry_run=args.dry_run))
        return REFUSED
    except mnt.MountError as exc:
        return _fail(str(exc))

    _emit(report.mount_report(result, dry_run=args.dry_run))
    ro.record_seen(root, dry_run=args.dry_run)
    return OK


# --- unmount ----------------------------------------------------------

def _unmount_report(plan: mnt.UnmountPlan, *, dry_run: bool) -> list[str]:
    head = "Would unmount" if dry_run else "Unmounted"
    lines = [
        f"{head} radialrig from {plan.root} - {plan.removed} path(s) removed, "
        f"{plan.left} left.",
        "",
    ]
    lines.extend(render_block(plan.dispositions))
    lines.append("")
    if plan.degraded:
        lines.append(mnt.DEGRADED_NOTE)
    if plan.complete:
        lines.append("The mount record is removed. Nothing was left behind.")
    else:
        lines.append(
            "The mount record is removed. Every path listed as left is now "
            "this project's own."
        )
    if plan.orphaned:
        lines.append("")
        lines.append(mnt.ORPHANED_RECORDS_NOTE.format(dirs=", ".join(plan.orphaned)))
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    return lines


def _cmd_unmount(args, root: Path) -> int:
    if not mnt.is_mounted(root):
        _emit(report.not_mounted_report(root))
        ro.record_seen(root, dry_run=args.dry_run)
        return NOOP
    try:
        plan = mnt.unmount_plan(root) if args.dry_run else mnt.unmount(root)
    except mnt.MountError as exc:
        return _fail(str(exc))
    _emit(_unmount_report(plan, dry_run=args.dry_run))
    ro.record_seen(root, dry_run=args.dry_run)
    return OK if plan.complete else PARTIAL


# --- upgrade ----------------------------------------------------------
# The upgrade runs both kits in one step (PLAN-0021 phase 1; D-36 rule 2
# as amended on 2026-10-02): the seeded files this project never edited,
# then every skill it already has whose files are all in place and none
# edited, for each agent. It adds no skill and puts none back: a skill
# one agent has that another lacks is a state a person chose, and the
# report lists each agent's skills under that agent's label without a
# word about the difference.

def _update_outcome(plan) -> int:
    """0, or 5 when anything was left for review: a seeded file with a
    diff, an edited skill, or a skills half that did not run."""
    return PARTIAL if plan.review else OK


def _cmd_upgrade(args, root: Path) -> int:
    if args.all:
        return _cmd_upgrade_all(args)
    if not mnt.is_mounted(root):
        _emit(report.not_mounted_report(root))
        return NOOP
    try:
        plan = update.plan(root) if args.dry_run else update.run(root)
    except mnt.MountError as exc:
        return _fail(str(exc))
    _emit(report.update_report(plan, dry_run=args.dry_run))
    return _update_outcome(plan)


# The order the worst of several projects' codes is picked in: a stop
# outranks a list to read, which outranks a clean run. A skipped project
# carries no code of its own; with nothing updated at all, the answer is
# the no-op code, as it is for one unmounted project.
_SEVERITY = {OK: 0, PARTIAL: 1, ERROR: 2}


def _update_all_outcome(found) -> int:
    """The worst of the projects' codes, or the no-op code where no
    project was updated. The page's press reads the same answer."""
    codes = [ERROR if item.error else _update_outcome(item.plan)
             for item in found if item.error or item.plan is not None]
    if not codes:
        return NOOP
    return max(codes, key=_SEVERITY.__getitem__)


def _cmd_upgrade_all(args) -> int:
    found = update.plan_all() if args.dry_run else update.run_all()
    if not found:
        _emit([report.PROJECTS_NONE])
        return NOOP
    _emit(report.update_all_report(found, dry_run=args.dry_run))
    return _update_all_outcome(found)


# --- adopt ------------------------------------------------------------

def _cmd_adopt(args, root: Path) -> int:
    if mnt.is_mounted(root):
        try:
            manifest = mnt.read_manifest(root)
        except mnt.MountError as exc:
            return _fail(str(exc))
        _emit(report.already_mounted_report(root, manifest))
        ro.record_seen(root, dry_run=args.dry_run)
        return NOOP
    build = ad.adopt_plan if args.dry_run else ad.adopt
    try:
        plan = build(root, scope=args.scope, owner=args.owner)
    except mnt.MountError as exc:
        return _fail(str(exc))
    if not plan.files:
        _emit(report.nothing_to_adopt_report(root, dry_run=args.dry_run))
        return NOOP
    _emit(report.adopt_report(plan, dry_run=args.dry_run))
    ro.record_seen(root, dry_run=args.dry_run)
    return OK if plan.kept == 0 else PARTIAL


# --- skill ------------------------------------------------------------
# Three verbs over one engine. Not one line of wording lives here, and no
# constant in this module names a skill: a name arrives as an argument or
# out of the catalog, which is what keeps the strict source ban of D-33
# satisfied while the reports themselves print a name.


def _chosen_harnesses(args):
    """None for every harness the map knows, or the one name `--harness`
    gave. The skills kit resolves the name through its map; nothing
    here reads it."""
    named = getattr(args, "harness", None)
    return (named,) if named else None


def _cmd_skill_add(args, root: Path) -> int:
    targets = _chosen_harnesses(args)
    if args.all:
        build = sk.add_catalog_plan if args.dry_run else sk.add_catalog
        results = build(root, targets=targets)
    else:
        build = sk.add_all_plan if args.dry_run else sk.add_all
        results = build(root, args.name, targets=targets)
    _emit(report.skill_add_reports(results, dry_run=args.dry_run))
    if all(r.current for r in results):
        return NOOP
    return PARTIAL if any(r.edited for r in results) else OK


def _cmd_skill_remove(args, root: Path) -> int:
    build = sk.remove_all_plan if args.dry_run else sk.remove_all
    plans = [p for p in build(root, args.name, targets=_chosen_harnesses(args))
             if p.installed]
    if not plans:
        _emit(report.skill_not_installed_report(root, args.name))
        return NOOP
    _emit(report.skill_remove_reports(plans, dry_run=args.dry_run))
    return OK if all(p.complete for p in plans) else PARTIAL


def _cmd_skill_list(args, root: Path) -> int:
    # Writes nothing, so there is no --dry-run to give it and no outcome
    # but 0: the answer is the report.
    states = sk.states_all(root, targets=_chosen_harnesses(args))
    _emit(report.skill_list_report(root, states))
    return OK


SKILL_RUNNERS = {
    "add": _cmd_skill_add,
    "remove": _cmd_skill_remove,
    "list": _cmd_skill_list,
}


def _cmd_skill(args, root: Path) -> int:
    runner = SKILL_RUNNERS.get(args.skill_command)
    if runner is None:
        # `radialrig skill` on its own is not an error; it is a reader
        # asking what the family is, and usage is the answer.
        sys.stdout.write(USAGE)
        return OK
    try:
        return runner(args, root)
    except sk.UnknownSkill as exc:
        for line in report.skill_unknown_report(exc.name, exc.available):
            _fail(line)
        return ERROR
    except sk.UnknownHarness as exc:
        return _fail(str(exc))
    except sk.SkillConflict as exc:
        _emit(report.skill_conflict_report(exc.conflicts, dry_run=args.dry_run))
        return REFUSED
    except sk.SkillError as exc:
        return _fail(str(exc))


# --- connect ----------------------------------------------------------
# Two verbs over one engine. Not one line of wording lives here, and no
# constant in this module names a client: a name arrives as an argument
# or out of the client table.


def _cmd_connect(args, root: Path) -> int:
    if args.client is None:
        sys.stdout.write(USAGE)
        return OK
    try:
        build = cn.connect_plan if args.dry_run else cn.connect
        result = build(root, args.client, scope=args.scope)
    except cn.UnknownClient as exc:
        for line in report.connect_unknown_client_report(exc.name, exc.available):
            _fail(line)
        return ERROR
    except cn.ConnectRefused as exc:
        _emit(report.connect_refused_report(
            exc.client, root, exc.conflicts, dry_run=args.dry_run))
        return REFUSED
    except cn.ConnectError as exc:
        return _fail(str(exc))
    _emit(report.connect_report(result, dry_run=args.dry_run))
    ro.record_seen(root, dry_run=args.dry_run)
    return NOOP if result.current else OK


def _cmd_unconnect_orphan(args) -> int:
    if args.client is None:
        return _fail(report.ORPHAN_NO_CLIENT)
    if args.path != ".":
        _emit(report.orphan_refused_report(
            report.ORPHAN_NO_PATH, dry_run=args.dry_run))
        return REFUSED
    build = orphan.remove_plan if args.dry_run else orphan.remove
    try:
        plan = build(args.client, args.orphan)
    except cn.UnknownClient as exc:
        for line in report.connect_unknown_client_report(exc.name, exc.available):
            _fail(line)
        return ERROR
    except orphan.OrphanRefused as exc:
        _emit(report.orphan_refused_report(str(exc), dry_run=args.dry_run))
        return REFUSED
    except cn.ConnectError as exc:
        return _fail(str(exc))
    if not plan.found:
        _emit(report.orphan_not_found_report(plan.client, plan.key))
        return NOOP
    _emit(report.orphan_report(
        plan, resolve=orphan.reason_text, dry_run=args.dry_run))
    return OK if plan.complete else PARTIAL


def _cmd_unconnect(args, root: Path) -> int:
    if args.orphan is not None:
        return _cmd_unconnect_orphan(args)
    if args.client is None:
        sys.stdout.write(USAGE)
        return OK
    try:
        build = cn.unconnect_plan if args.dry_run else cn.unconnect
        plan = build(root, args.client)
    except cn.UnknownClient as exc:
        for line in report.connect_unknown_client_report(exc.name, exc.available):
            _fail(line)
        return ERROR
    except cn.ConnectError as exc:
        return _fail(str(exc))
    if not plan.connected:
        _emit(report.connect_not_connected_report(root, plan.client))
        ro.record_seen(root, dry_run=args.dry_run)
        return NOOP
    _emit(report.unconnect_report(plan, dry_run=args.dry_run))
    ro.record_seen(root, dry_run=args.dry_run)
    return OK if plan.complete else PARTIAL


# --- setup ------------------------------------------------------------

def _cmd_setup(args, root: Path) -> int:
    from radialrig import setup_web

    return setup_web.serve(
        root,
        scope=args.scope,
        owner=args.owner,
        open_browser=not args.no_browser,
    )


def _cmd_manage(args, root: Path) -> int:
    from radialrig import manage_web

    return manage_web.serve(open_browser=not args.no_browser)


def _cmd_projects(args, root: Path) -> int:
    states = ro.listing()
    if args.porcelain:
        sys.stdout.buffer.write(report.projects_porcelain(states).encode("utf-8"))
        return OK
    found = ro.orphans()
    _emit(report.projects_report(
        states, found, ro.report_rows(states, found), resolve=ro.reason_text))
    return OK


# --- log --------------------------------------------------------------

def _cmd_log(args, root: Path) -> int:
    """Where the log is, and its last events (PLAN-0018 phase 2, Decided
    5). Exit 0 with events printed, 4 when there is no log yet, 1 when
    this platform has no folder for one. It writes no event of its own:
    `_record_run` skips this verb."""
    path = al.log_path()
    if path is None:
        return _fail(report.LOG_NO_FOLDER)
    lines = [report.LOG_HEAD.format(path=path)]
    found = al.last_events()
    if not found:
        lines.append(report.LOG_EMPTY)
        _emit(lines)
        return NOOP
    lines.append("")
    for event in found:
        lines.extend(event.splitlines())
    _emit(lines)
    return OK


# --- parser -----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radialrig",
        description="Governed-record tooling that mounts onto a project you "
                    "already have.",
    )
    parser.add_argument(
        "--version", action="store_true",
        help="print the installed radialrig version and exit",
    )
    subcommands = parser.add_subparsers(dest="command")

    # PATH is a positional because these three verbs act on a project, and
    # the project is the direct object of the verb, the way `git init
    # [directory]` takes one. `radialrig-validate --root` is a separate,
    # already-shipped entry point and is not changed by this phase.
    def add_path(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("path", nargs="?", default=".",
                         help="the project to act on (default: the working directory)")
        sub.add_argument("--dry-run", action="store_true",
                         help="print the report, write nothing, and return the "
                              "exit code the real run would return")

    mount_cmd = subcommands.add_parser("mount", help="lay the seed onto a project")
    add_path(mount_cmd)
    mount_cmd.add_argument("--scope", default=None,
                           help="the scope the seeded records carry "
                                "(default: the project directory name)")
    mount_cmd.add_argument("--owner", default=mnt.DEFAULT_OWNER,
                           help=f"the owner the seeded records carry "
                                f"(default: {mnt.DEFAULT_OWNER})")

    unmount_cmd = subcommands.add_parser(
        "unmount", help="reverse the mount, listing what it leaves and why")
    add_path(unmount_cmd)

    upgrade_cmd = subcommands.add_parser(
        "upgrade", help="move never-edited seeded files to the installed seed, "
                        "and the skills this project has to the packaged version",
        epilog=UPGRADE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_path(upgrade_cmd)
    # `--all` stands in the path's place, never beside one, "." included:
    # `_settle_upgrade` below holds the two to that rule, so this verb
    # alone tells a path left out from one given, and fills in the
    # working directory itself.
    upgrade_cmd.set_defaults(path=None)
    upgrade_cmd.add_argument(
        "--all", action="store_true",
        help="update every mounted project in the listing whose folder is "
             "there, in place of a path; a project it skips is named")

    adopt_cmd = subcommands.add_parser(
        "adopt", help="rebuild a lost mount record from the standing files",
        epilog=ADOPT_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_path(adopt_cmd)
    adopt_cmd.add_argument("--scope", default=None,
                           help="the scope the original mount used "
                                "(default: the project directory name)")
    adopt_cmd.add_argument("--owner", default=mnt.DEFAULT_OWNER,
                           help=f"the owner the original mount used "
                                f"(default: {mnt.DEFAULT_OWNER})")

    # setup takes no --dry-run: the page's first state is the dry run, and
    # it writes nothing until the one button is pressed.
    setup_cmd = subcommands.add_parser(
        "setup", help="the same mount, guided, in a local browser page",
        epilog=SETUP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    setup_cmd.add_argument("path", nargs="?", default=".",
                           help="the project to act on (default: the working directory)")
    setup_cmd.add_argument("--scope", default=None,
                           help="the scope the seeded records carry "
                                "(default: the project directory name)")
    setup_cmd.add_argument("--owner", default=mnt.DEFAULT_OWNER,
                           help=f"the owner the seeded records carry "
                                f"(default: {mnt.DEFAULT_OWNER})")
    setup_cmd.add_argument("--no-browser", action="store_true",
                           help="print the URL and open no browser")

    skill_cmd = subcommands.add_parser(
        "skill", help="install and remove packaged agent skills",
        epilog=SKILL_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # main() reads args.path, args.dry_run and args.name for every verb,
    # and `radialrig skill` on its own runs none of the three: without
    # these defaults that bare invocation is an AttributeError rather
    # than the usage text it should print. No nested parser defines
    # --version either; main() reads that off the top-level namespace.
    skill_cmd.set_defaults(path=".", skill_command=None, dry_run=False, name=None,
                           all=False)
    skill_subs = skill_cmd.add_subparsers(dest="skill_command")

    def add_name(sub: argparse.ArgumentParser, required: bool = True) -> None:
        sub.add_argument("name", nargs=None if required else "?",
                         help="the skill to act on; skill list names them")

    # Every skill verb acts on every harness the map knows unless one is
    # named. The choices come out of the map, so this file names none.
    def add_harness(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--harness", default=None, choices=sorted(sk.HARNESSES),
            help="act on one agent harness only (default: every one this "
                 "package knows)")

    # `add` takes a name or --all, never both and never neither; the
    # positional is optional here so --all can stand in its place, and
    # `_settle_skill_add` below holds the two to that rule.
    skill_add = skill_subs.add_parser(
        "add", help="install one packaged skill into this project, or every "
                    "one it lacks, for every agent harness")
    add_name(skill_add, required=False)
    add_path(skill_add)
    skill_add.add_argument(
        "--all", action="store_true",
        help="install every packaged skill this project lacks, in place "
             "of a name, and refresh the ones it has: a skill already "
             "installed with none of its files edited brings up to the "
             "packaged version, and an edited one is left with its diff")
    add_harness(skill_add)

    skill_remove = skill_subs.add_parser(
        "remove", help="remove one skill this package installed, listing what "
                       "it leaves and why")
    add_name(skill_remove)
    add_path(skill_remove)
    add_harness(skill_remove)

    # list writes nothing, so it takes no --dry-run, for the reason setup
    # takes none: there is nothing for a rehearsal to withhold.
    skill_list = skill_subs.add_parser(
        "list", help="what this package ships, and what is installed here")
    skill_list.add_argument(
        "path", nargs="?", default=".",
        help="the project to act on (default: the working directory)")
    add_harness(skill_list)

    # `client` is optional for the reason `radialrig skill` runs no verb:
    # a bare invocation is a reader asking what the family is, and usage
    # is the answer rather than argparse's exit 2.
    def add_client(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "client", nargs="?", default=None,
            help="the client to register with; an unknown name lists the ones this build knows")

    connect_cmd = subcommands.add_parser(
        "connect", help="register this project with an MCP client")
    add_client(connect_cmd)
    add_path(connect_cmd)
    connect_cmd.add_argument("--scope", default=None,
                             help="the name the entry carries "
                                  "(default: this project's scope)")

    unconnect_cmd = subcommands.add_parser(
        "unconnect", help="remove the entry connect wrote, listing what it leaves")
    add_client(unconnect_cmd)
    add_path(unconnect_cmd)
    unconnect_cmd.add_argument(
        "--orphan", default=None, metavar="KEY",
        help="remove one named entry whose project root no longer exists")
    unconnect_cmd.set_defaults(scope=None)

    projects_cmd = subcommands.add_parser(
        "projects",
        help="list the projects radialrig has mounted or connected on this machine")
    projects_cmd.add_argument(
        "--porcelain", action="store_true",
        help="one machine-readable line per project")
    projects_cmd.set_defaults(path=".")

    # manage takes no PATH: it acts on the machine, not on one project,
    # and the page reads the roster for every project it lists. The
    # default is set anyway because main() resolves args.path for every
    # verb before it dispatches.
    manage_cmd = subcommands.add_parser(
        "manage",
        help="open, change and reverse this machine's projects in a local page")
    manage_cmd.add_argument("--no-browser", action="store_true",
                            help="print the URL and open no browser")
    manage_cmd.set_defaults(path=".")

    # log takes no PATH for the reason manage takes none: the log is
    # this machine's, not one project's (PLAN-0018 phase 2).
    log_cmd = subcommands.add_parser(
        "log", help="where the log is, and its last 20 events")
    log_cmd.set_defaults(path=".")
    return parser


def _settle_skill_add(parser: argparse.ArgumentParser, args) -> None:
    """Hold `skill add` to one of a name or --all.

    argparse gives the first bare word to `name` whether or not --all
    was given, so under --all that word is the path and moves there —
    unless it is a skill's own name, which is never a path here: a
    person who typed `skill add plan-review --all` named a skill, and
    the answer is this usage error, not that no such folder exists. A
    folder that shares a skill's name is reached as `./plan-review`. A
    second bare word beside --all, or no choice at all, is the usage
    error argparse would have raised itself had the two shared one
    slot, and it is raised the way argparse raises its own.
    """
    if getattr(args, "skill_command", None) != "add":
        return
    if args.all:
        if args.name is not None:
            if args.path != "." or args.name in sk.catalog_names():
                parser.error("argument --all: not allowed with a skill name")
            args.path, args.name = args.name, None
    elif args.name is None:
        parser.error("one of the arguments name --all is required")
    elif args.name not in sk.catalog_names() and args.path == "."             and Path(args.name).is_dir():
        # A folder in the name slot is a forgotten --all or a forgotten
        # name, never a skill: say so instead of "no skill named" it.
        parser.error(
            f"'{args.name}' is a folder, not a skill: name a skill before "
            "it, or add every skill this project lacks with --all")


def _settle_upgrade(parser: argparse.ArgumentParser, args) -> None:
    """Hold `upgrade` to one of a path or --all. The listing names the
    projects `--all` acts on, so any path beside it, the working
    directory's own "." included, would be a second answer to one
    question, and the usage error is argparse's own. A path left out
    still means the working directory, as it does for every verb."""
    if getattr(args, "command", None) != "upgrade":
        return
    if args.all and args.path is not None:
        parser.error("argument --all: not allowed with a path")
    if args.path is None:
        args.path = "."


RUNNERS = {
    "mount": _cmd_mount,
    "unmount": _cmd_unmount,
    "upgrade": _cmd_upgrade,
    "adopt": _cmd_adopt,
    "setup": _cmd_setup,
    "skill": _cmd_skill,
    "connect": _cmd_connect,
    "unconnect": _cmd_unconnect,
    "projects": _cmd_projects,
    "manage": _cmd_manage,
    "log": _cmd_log,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _printed.clear()
    _failed.clear()
    if not argv:
        sys.stdout.write(USAGE)
        return OK

    parser = build_parser()
    args = parser.parse_args(argv)
    _settle_skill_add(parser, args)
    _settle_upgrade(parser, args)
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except AttributeError:
        pass
    if args.version:
        sys.stdout.write(f"{mnt.version_stamp()}\n")
        return OK
    if args.command is None:
        sys.stdout.write(USAGE)
        return OK

    # Resolve PATH first, so a mistyped scope becomes a clean refusal
    # rather than a mount into a directory nobody meant.
    root = Path(args.path)
    if getattr(args, "orphan", None) is None and not root.is_dir():
        code = _fail(f"no such directory: {args.path}")
        _record_run(args, argv, root, code)
        return code
    root = root.resolve()
    try:
        try:
            code = RUNNERS[args.command](args, root)
        except (OSError, mnt.WriteStopped, cn.WriteStopped) as exc:
            # The last net (cli-connect-02): a file error no runner caught
            # is still one line on stderr, never a trace.
            code = _fail(str(exc))
    except Exception as exc:
        # A crash no net caught: the log keeps its traceback, and it goes
        # on to the caller unchanged (PLAN-0018 phase 2).
        _record_run(args, argv, root, error=exc)
        raise
    if _printed:
        # The engine line (PLAN-0018 phase 1): after a printed report,
        # and only then. A page's own stream, the porcelain listing and
        # the usage text never pass through `_emit`, so they end as they
        # did.
        _emit([report.engine_line()])
    # The run's one event, once everything the person saw has been
    # written (PLAN-0018 phase 2).
    _record_run(args, argv, root, code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
