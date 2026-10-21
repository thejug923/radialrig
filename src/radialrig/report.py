"""One renderer for both installer surfaces.

PLAN-0005 Phase 4. The command line printed these lines from `cli.py`
until this module existed; the local setup page needs the same words, so
the lines moved here and `cli.py` now calls them. Nothing about what the
command line prints changed in the move — the tests that pin its output
are the proof.

`radialrig.mount` still owns the vocabulary: the verbs, the reason codes
and `reason_text`. This module owns only the sentences around them and
the column layout the terminal needs. A surface that wants its own layout
(the page renders a real table) still takes its strings from here, so the
two surfaces cannot drift apart in wording.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from radialrig import mount as mnt
from radialrig.validate_docs import under_git

# --- outcomes ---------------------------------------------------------
# Both surfaces answer with these. `radialrig setup` maps the state the
# user ends on to the same number `radialrig mount` would have returned.

OK = 0
ERROR = 1
REFUSED = 3
NOOP = 4
PARTIAL = 5

# --- fixed sentences --------------------------------------------------

NEXT_STEP = "Next: run radialrig-validate in this project."
# Where git cannot answer for the folder, the validator has no committed
# state to compare a record with, so it stops and asks for
# `--no-baseline` (D-23). Such a project is told the flag here, rather
# than sent into that stop by the line meant to guide it (an outside
# tester, 2026-10-01). The sentence claims only what the probe saw: a
# repository on a machine without git reads the same as no repository.
NEXT_STEP_NO_GIT = (
    "Next: run radialrig-validate --no-baseline in this project. It has no "
    "git history radialrig can read, so the lifecycle check reports that it "
    "did not run."
)
VALIDATE_COMMAND = "radialrig-validate"
VALIDATE_COMMAND_NO_GIT = "radialrig-validate --no-baseline"
DRY_RUN_TAIL = "Dry run: nothing was written."
REFUSAL_REMEDY = "Clear what blocks these paths and run the command again."
NOTHING_CHANGED = (
    'Nothing changed. Run "radialrig upgrade" to move the seed to the '
    "installed version."
)
CLOSE_HOLDERS = (
    "Close the RadialRig app and any manage page before that install: they "
    "hold the executables it replaces."
)
ENGINE_HALF = (
    "The engine half of an upgrade is the package version itself: install a "
    "newer radialrig from the address its release page names, for example "
    'uv tool install "radialrig @ <that wheel address>", then run this '
    "command. " + CLOSE_HOLDERS
)


def engine_line() -> str:
    """The last line of every printed command report (PLAN-0018 phase 1).

    BUG-0013: which engine is installed on this computer, so a stale
    mount record and a stale engine stop looking the same. The pages
    carry the same stamp in their masthead, from the same function.
    """
    return f"Engine on this machine: {mnt.version_stamp()}"


# --- the activity log's sentences -------------------------------------
# PLAN-0018 phase 2 (BUG-0014). What `radialrig log` says: where the log
# is, that nothing has been written yet, and that this platform keeps
# none. The pages carry their own sentences about the log, because a
# page names it beside a stop rather than as its whole answer.

LOG_HEAD = "Log: {path}"
LOG_EMPTY = (
    "No events yet. The first command run, page press or server tool call "
    "on this computer writes one."
)
LOG_NO_FOLDER = "this platform has no folder for the log, so none is kept"


# --- the skills kit's sentences ---------------------------------------
# D-36 and PLAN-0006 Phase 3. Not one of these names a skill: a skill's
# name arrives as a parameter, from the catalog or from the command line,
# so a second skill costs no sentence here and no test edit anywhere.
# Nor does one name an agent: the head of every block takes the label
# the harness map carries, the way the connect reports take the client
# table's, so a second agent costs one row there (PLAN-0015).

# "Agent", never "harness": this line ends every add on every page, and
# the word a page uses for the thing that reads a skill is the label's
# word (PLAN-0015 objective).
SKILL_NEXT_STEP = "Next: start a fresh agent session so the agent reads the skill."
SKILL_LEFT_TAIL = (
    "The record no longer names this skill. Every path listed as left is "
    "now this project's own."
)
SKILL_CLEAN_TAIL = "The record no longer names this skill. Nothing was left behind."
SKILL_NONE_TAIL = "No skill from this package is installed in this project."
SKILL_EDIT_TAIL = (
    "Nothing was written. Move your copy aside, or keep it: radialrig never "
    "overwrites a skill this project changed."
)
SKILL_EDIT_SOME_TAIL = (
    "A tree this project changed was left alone. Move your copy aside, or "
    "keep it: radialrig never overwrites a skill this project changed."
)
SKILL_FOREIGN_TAIL = "Move or rename what stands there and run the command again."
SKILL_CURRENT_TAIL = (
    "Nothing changed. This project already has the version this package ships."
)
SKILL_LIST_TAIL = (
    "Add one with: radialrig skill add <name>. Add every one this project "
    "lacks with: radialrig skill add --all. Remove one with: "
    "radialrig skill remove <name>."
)


# --- the connect kit's sentences --------------------------------------
# PLAN-0007 Phase 2. Not one of these names a client: the client's label
# and its restart line are data in `connect.CLIENTS`, so a second client
# costs one row there and no sentence here.

CONNECT_CURRENT_TAIL = (
    "Nothing changed. This project is already registered with this client."
)
CONNECT_LEFT_TAIL = (
    "The connect record is removed. Every settings file listed as left is "
    "now the client's own."
)
CONNECT_CLEAN_TAIL = "The connect record is removed. Nothing was left behind."
CONNECT_NOT_CONNECTED = (
    "This project carries no connect record for this client. Nothing changed."
)
CONNECT_NO_COMMAND_TEMPLATE = (
    "the {command} command could not be located; install radialrig so "
    "its commands are on the command path, for example "
    '"uv tool install radialrig", then run this command again; close the '
    "RadialRig app and any manage page before that install, they hold the "
    "executables it replaces"
)
CONNECT_NO_COMMAND = CONNECT_NO_COMMAND_TEMPLATE.format(command="radialrig-read")


def connect_no_command(command: str) -> str:
    """Why a connect stopped: the named console script is not here."""
    return CONNECT_NO_COMMAND_TEMPLATE.format(command=command)
CONNECT_NO_CLIENT = (
    "no settings file for this client was found on this machine; install the "
    "client, then run this command again"
)
CONNECT_EMPTY_SCOPE = (
    "this project's scope holds no character an entry key can carry; pass "
    "--scope with a name of letters, digits, dots, dashes or underscores"
)
CONNECT_SCOPE_REMEDY = (
    "To register this project under another name, pass --scope."
)
CONNECT_REPAIR_REMEDY = (
    "Repair the settings file named here, then run this command again."
)
CONNECT_WITHHELD = "The registration is not offered on this page:"

# The refusal codes that say this project already carries a connect
# record. They are what decides which remedy a refusal names: a project
# with a record can run the reversal, and a project without one cannot.
CONNECT_RECORD_CODES: tuple[str, ...] = (
    mnt.CONNECT_ENTRY_STALE, mnt.CONNECT_RECORD_OTHER_KEY,
)


def connect_refusal_remedy(client_name: str, root, conflicts) -> str:
    """What a reader does about a refused connect, by what refused it.

    One command per case, never two: a project that carries a record
    reverses it first, and a project that carries none cannot — the
    reversal answers "not registered" there — so it is offered the name
    it can still register under instead.
    """
    codes = {d.code for d in conflicts}
    if codes & set(CONNECT_RECORD_CODES):
        return f'Run "radialrig unconnect {client_name} {root}" first, then this command.'
    if mnt.CONNECT_KEY_HOST_OWNED in codes:
        return CONNECT_SCOPE_REMEDY
    return CONNECT_REPAIR_REMEDY


def connect_refusal_detail(client_name: str, root, conflicts) -> str:
    """One sentence of refusal: what blocked it, and what clears it.

    For the surfaces that carry no table of their own. The reasons are
    the rows' own words, so a page and the terminal say the same thing.
    """
    reasons = "; ".join(dict.fromkeys(
        text for text in (mnt.reason_text(d) for d in conflicts) if text
    ))
    remedy = connect_refusal_remedy(client_name, root, conflicts)
    return f"{reasons}. {remedy}" if reasons else remedy


def connect_withheld_notice(client_name: str, root, conflicts) -> str:
    """Why the page is not offering the registration. The mount stands."""
    return f"{CONNECT_WITHHELD} {connect_refusal_detail(client_name, root, conflicts)}"


def connect_refusal_head(count: int) -> str:
    """The connect's own refusal head: it inserts, so it overwrites nothing."""
    return f"Refused: {count} path(s) block the connect. Nothing was written."


def refusal_head(count: int) -> str:
    return f"Refused: {count} path(s) would be overwritten. Nothing was written."


def conflict_reason(rel: str) -> str:
    """Why this path blocks a mount. One wording for both surfaces."""
    if rel == mnt.CODEX_REL:
        return ("this project's file already carries a radialrig fenced section, "
                "does not parse as TOML, already defines a radialrig server "
                "table, or is one file with another seed path")
    if rel in mnt.FENCES:
        return ("this project already carries a radialrig fenced section, or "
                "this path is one file with another seed path")
    if rel == mnt.MCP_REL:
        return f"this project's {mnt.MCP_REL} does not parse as a JSON object"
    return "this project already has a file at this path"


def conflict_dispositions(conflicts) -> tuple[mnt.Disposition, ...]:
    """The refusal as report lines, before any surface renders it."""
    return tuple(mnt.Disposition(rel, mnt.LEFT, "CONFLICT") for rel in conflicts)


# --- terminal layout --------------------------------------------------

def render_rows(dispositions, resolve=mnt.reason_text) -> list[str]:
    """One line per disposition: verb, path, reason.

    The verb column is padded to 9 and the path column to the longest
    path in the block, so a reader scans one column rather than three.

    `resolve` is how a row becomes a sentence. It defaults to the mount
    kit's table, which is the only one that existed while every row came
    from that kit. A kit carrying a table of its own passes its own
    resolver in, so this module renders that kit's rows without importing
    it and the dependency keeps running one way.
    """
    if not dispositions:
        return []
    # A row's path and reason can carry record text, so each stands as
    # one line (PLAN-0022 phase 3).
    rows = [(d.verb, mnt.one_line(d.path), mnt.one_line(resolve(d) or ""))
            for d in dispositions]
    width = max(len(path) for _, path, _ in rows)
    lines: list[str] = []
    for verb, path, reason in rows:
        line = f"{verb:<9} {path:<{width}}"
        if reason:
            line = f"{line}  {reason}"
        lines.append(line.rstrip())
    return lines


def render_block(dispositions) -> list[str]:
    """The same block, read through the mount kit's own table."""
    return render_rows(dispositions)


def conflict_report(conflicts, *, dry_run: bool = False) -> list[str]:
    """The whole exit-3 answer: head, the blocked paths, the remedy."""
    lines = [refusal_head(len(conflicts)), ""]
    width = max(len(rel) for rel in conflicts)
    for rel in conflicts:
        lines.append(
            f"{mnt.LEFT:<9} {rel:<{width}}  {conflict_reason(rel)}"
        )
    lines.extend(["", REFUSAL_REMEDY])
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    return lines


def next_step(root: Path) -> tuple[str, str]:
    """The line that closes a mount report, and the command it names.

    Both surfaces close on it: the command line prints the sentence, and
    the setup page shows the sentence over the command.
    """
    if under_git(root):
        return NEXT_STEP, VALIDATE_COMMAND
    return NEXT_STEP_NO_GIT, VALIDATE_COMMAND_NO_GIT


def mount_report(result: mnt.MountResult, *, dry_run: bool = False) -> list[str]:
    head = "Would mount" if dry_run else "Mounted"
    lines = [
        f"{head} radialrig {result.version} onto {result.root}",
        f"scope: {result.scope}    owner: {result.owner}",
        "",
    ]
    lines.extend(render_block(result.dispositions()))
    lines.append("")
    lines.append(next_step(result.root)[0])
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    return lines


def already_mounted_report(root: Path, manifest: dict) -> list[str]:
    return [
        f"Already mounted: {root}",
        f"The mount record says: radialrig {manifest.get('radialrig', '?')}, "
        f"mounted {manifest.get('mounted', '?')}.",
        NOTHING_CHANGED,
    ]


def not_mounted_report(root: Path) -> list[str]:
    return [
        f"Not mounted: {root}",
        "This project carries no mount record. Nothing changed.",
    ]


# --- the adopt flow ---------------------------------------------------
# BUG-0001. One report for the rebuilt record, one for the project with
# nothing to rebuild from. Adopt writes the record and no other byte, so
# every row is a statement about what the record says, not about a write.

ADOPT_CLEAN_TAIL = (
    "The mount record is rebuilt. Every path it claims matches the "
    "installed seed, and unmount and upgrade work here again."
)
ADOPT_KEPT_TAIL = (
    "The mount record is rebuilt. A path recorded as, or left as, this "
    "project's own is never removed by unmount and never rewritten by "
    "upgrade."
)
NOTHING_TO_ADOPT = (
    'No seeded content stands here. Run "radialrig mount" to lay the seed.'
)


def adopt_report(plan, *, dry_run: bool = False) -> list[str]:
    head = "Would rebuild" if dry_run else "Rebuilt"
    lines = [
        f"{head} the mount record for {plan.root} - {plan.claimed} path(s) "
        f"recorded as the mount's own, {plan.kept} left as this project's own.",
        f"scope: {plan.scope}    owner: {plan.owner}    mounted: {plan.mounted}",
        "",
    ]
    lines.extend(render_block(plan.dispositions))
    lines.append("")
    lines.append(ADOPT_CLEAN_TAIL if plan.kept == 0 else ADOPT_KEPT_TAIL)
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    return lines


def nothing_to_adopt_report(root: Path, *, dry_run: bool = False) -> list[str]:
    lines = [f"Nothing to adopt: {root}", NOTHING_TO_ADOPT]
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    return lines


# --- the skills kit ---------------------------------------------------
# Six builders, one per answer the three skill verbs can give. They take
# the engine's own result objects, so what a surface prints is what the
# engine computed and nothing a surface decided for itself.


def skill_add_report(result, *, dry_run: bool = False) -> list[str]:
    """The whole answer of one `skill add`, in any of its three outcomes.

    One builder rather than three, because the install, the no-op and the
    refusal to touch an edited skill differ by their head and their tail
    and by nothing a reader scans: the block between them is rendered the
    same way in all three.
    """
    body, tails = _skill_add_parts(result, dry_run=dry_run)
    return _skill_report(body, tails, dry_run=dry_run)


def _skill_add_parts(
    result, *, dry_run: bool, head_only: bool = False,
) -> tuple[list[str], list[str]]:
    """One harness's head, block and diffs, and the tail its outcome
    earns. The multi-harness report composes from these parts rather
    than from a rendered report, so no line count is assumed.

    The head names the agent, from the map's label: two blocks that
    differ only in the tree they list would otherwise read as a repeat.
    `head_only` is the multi-agent report's form for a skill already
    current there: one line that it stands, and none of its rows.
    """
    label = result.harness.label
    if result.current:
        head = (f"Already installed for {label}: {result.name} "
                f"{result.version} in {result.root}")
        tail = SKILL_CURRENT_TAIL
    elif result.edited:
        head = f"Left alone for {label}: {result.name} in {result.root}"
        tail = SKILL_EDIT_TAIL
    else:
        verb = "Would add" if dry_run else "Added"
        head = f"{verb} {result.name} {result.version} for {label} to {result.root}"
        tail = SKILL_NEXT_STEP
    if head_only:
        return [head], [tail]
    body = [head, ""]
    body.extend(render_block(result.dispositions))
    for text in result.diffs:
        body.append("")
        body.extend(text.splitlines())
    return body, [tail]


def _skill_report(body: list[str], tails: list[str], *, dry_run: bool) -> list[str]:
    """Body, a blank line, the tail lines, and the rehearsal line."""
    lines = list(body)
    lines.append("")
    lines.extend(tails)
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    return lines


def skill_add_reports(results, *, dry_run: bool = False) -> list[str]:
    """Every harness's answer to one `skill add`, as one report.

    One result renders exactly as `skill_add_report` does. Several
    render head and block each — a block already current is its head
    alone, so a whole-catalog add on a project with most of it says
    so in one line per skill — then the tails the set earns: a line
    for a tree left alone because this project changed it, the next
    step for a tree that was written — both when both happened, since
    a tail saying nothing was written would be false — and the current
    tail when nothing moved anywhere.
    """
    results = tuple(results)
    if len(results) == 1:
        return skill_add_report(results[0], dry_run=dry_run)
    body: list[str] = []
    for result in results:
        part, _ = _skill_add_parts(result, dry_run=dry_run, head_only=result.current)
        if body:
            body.append("")
        body.extend(part)
    edited = any(r.edited for r in results)
    wrote = any(not r.current and not r.edited for r in results)
    tails: list[str] = []
    if edited:
        tails.append(SKILL_EDIT_SOME_TAIL if wrote else SKILL_EDIT_TAIL)
    if wrote:
        tails.append(SKILL_NEXT_STEP)
    if not tails:
        tails.append(SKILL_CURRENT_TAIL)
    return _skill_report(body, tails, dry_run=dry_run)


def _joined(parts) -> str:
    """Several names in one sentence: "a, b and c"."""
    parts = list(parts)
    if len(parts) < 2:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def skill_add_all_report(results, *, dry_run: bool = False) -> list[str]:
    """One agent's whole-catalog add as one block: a head naming every
    skill it writes, its rows once, then the tails the set earns.

    The page's form of `skill add --all --harness <name>` (PLAN-0015
    phase 2 prescription 4). The command line prints one block per
    skill, which a terminal reader scrolls; a page has one heading, and
    a heading that named one skill of four, or led with "Already
    installed" because the first catalog skill happened to stand, would
    say the wrong thing about the press. A skill already current is
    nothing the press wrote, so it is not in the head and none of its
    rows are drawn: the list above already reads Installed. Only when
    nothing at all was written does the head say so, naming what stood.
    A press naming no agent renders one such block per agent, in the
    order the results came.
    """
    results = tuple(results)
    body: list[str] = []
    for label in dict.fromkeys(r.harness.label for r in results):
        group = [r for r in results if r.harness.label == label]
        root = group[0].root
        written = [r for r in group if not r.current and not r.edited]
        edited = [r for r in group if r.edited]
        if written:
            verb = "Would add" if dry_run else "Added"
            named = _joined(f"{r.name} {r.version}" for r in written)
            head = f"{verb} {named} for {label} to {root}"
        elif edited:
            head = f"Left alone for {label}: {_joined(r.name for r in edited)} in {root}"
        else:
            named = _joined(f"{r.name} {r.version}" for r in group)
            head = f"Already installed for {label}: {named} in {root}"
        if body:
            body.append("")
        body.append(head)
        rows = list(dict.fromkeys(d for r in written + edited for d in r.dispositions))
        if rows:
            body.append("")
            body.extend(render_block(rows))
        for r in edited:
            for text in r.diffs:
                body.append("")
                body.extend(text.splitlines())
    edited_any = any(r.edited for r in results)
    wrote_any = any(not r.current and not r.edited for r in results)
    tails: list[str] = []
    if edited_any:
        tails.append(SKILL_EDIT_SOME_TAIL if wrote_any else SKILL_EDIT_TAIL)
    if wrote_any:
        tails.append(SKILL_NEXT_STEP)
    if not tails:
        tails.append(SKILL_CURRENT_TAIL)
    return _skill_report(body, tails, dry_run=dry_run)


def _skill_remove_parts(plan, *, dry_run: bool) -> tuple[list[str], list[str]]:
    head = "Would remove" if dry_run else "Removed"
    body = [
        f"{head} {plan.name} for {plan.harness.label} from {plan.root} - "
        f"{plan.removed} path(s) removed, {plan.left} left.",
        "",
    ]
    body.extend(render_block(plan.dispositions))
    return body, [SKILL_CLEAN_TAIL if plan.complete else SKILL_LEFT_TAIL]


def skill_remove_reports(plans, *, dry_run: bool = False) -> list[str]:
    """Every harness's answer to one `skill remove`, as one report; the
    same arrangement as `skill_add_reports`."""
    plans = tuple(plans)
    if len(plans) == 1:
        return skill_remove_report(plans[0], dry_run=dry_run)
    body: list[str] = []
    for plan in plans:
        part, _ = _skill_remove_parts(plan, dry_run=dry_run)
        if body:
            body.append("")
        body.extend(part)
    tail = SKILL_CLEAN_TAIL if all(p.complete for p in plans) else SKILL_LEFT_TAIL
    return _skill_report(body, [tail], dry_run=dry_run)


def skill_remove_report(plan, *, dry_run: bool = False) -> list[str]:
    """What the removal did, and what it would not touch.

    The entry is dropped even when a file stays, so both tails say the
    record no longer names the skill: a record claiming bytes that are
    now the project's own would be a false record.
    """
    body, tails = _skill_remove_parts(plan, dry_run=dry_run)
    return _skill_report(body, tails, dry_run=dry_run)


def skill_list_report(root: Path, states) -> list[str]:
    """One row per skill per harness: what this package ships, and what
    is here, under the directory each harness reads.

    An empty catalog and a project with nothing installed both still
    answer in words. Silence would read as a failure. A skill one
    harness has and another lacks is two rows that differ, not a
    sentence: either state is one a person can choose (D-36).
    """
    rows = [state.list_disposition() for state in states]
    lines = [f"Skills for {root}"]
    if rows:
        lines.append("")
        lines.extend(render_block(rows))
    lines.append("")
    if not any(state.record_entry for state in states):
        lines.append(SKILL_NONE_TAIL)
    lines.append(SKILL_LIST_TAIL)
    return lines


def skill_conflict_report(conflicts, *, dry_run: bool = False) -> list[str]:
    """The exit-3 answer: what stood in the way, and what to do about it."""
    lines = [refusal_head(len(conflicts)), ""]
    lines.extend(render_block(conflicts))
    lines.extend(["", SKILL_FOREIGN_TAIL])
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    return lines


def skill_not_installed_report(root: Path, name: str) -> list[str]:
    return [
        f"Not installed: {name} in {root}",
        "This project's skills record does not name it. Nothing changed.",
    ]


def skill_unknown_report(name: str, available) -> list[str]:
    """For stderr: the name is not the package's, so nothing was read."""
    offer = ", ".join(available) if available else "none"
    return [
        f"this package ships no skill named '{name}'",
        f"it ships: {offer}",
    ]


# --- the connect kit --------------------------------------------------
# Five builders, one per answer the two connect verbs can give. Each one
# takes the engine's own result object, so a surface prints what the
# engine computed. The client's label and its restart line arrive from
# the client table, never from a constant here.


def connect_report(result, *, dry_run: bool = False) -> list[str]:
    """The whole answer of one `connect`, in any of its three outcomes.

    One builder rather than three, for the reason `skill_add_report` is
    one: the already-registered answer, the rehearsal and the real run
    differ by their head and their tail and by nothing a reader scans.
    The restart line is last on a run that changed something, and absent
    on one that did not.
    """
    client = result.client
    named = " and ".join(result.keys)
    if result.current:
        lines = [f"Already registered: {named} with {client.label}", ""]
        lines.extend(render_block(result.dispositions))
        lines.extend(["", CONNECT_CURRENT_TAIL])
        return lines

    verb = "Would register" if dry_run else "Registered"
    lines = [f"{verb} {named} with {client.label}", ""]
    lines.extend(render_block(result.dispositions))
    lines.append("")
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    else:
        lines.append(client.restart)
    return lines


def connect_refused_report(client, root, conflicts, *, dry_run: bool = False) -> list[str]:
    """The exit-3 answer: what stood in the way, and the command that
    clears it. Nothing was written, to any copy."""
    lines = [connect_refusal_head(len(conflicts)), ""]
    lines.extend(render_block(conflicts))
    lines.extend(["", connect_refusal_remedy(client.name, root, conflicts)])
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    return lines


def unconnect_report(plan, *, dry_run: bool = False) -> list[str]:
    """What the reversal removed, and what it would not touch.

    The record is dropped even when an entry stays, so both tails say so:
    a record claiming bytes that are now the client's own would be a
    false record. The restart line rides on a real run because a client
    that is still running keeps the removed server alive.

    The head counts the client's settings files and not the record row:
    the record is this kit's own file, and the row for it says its own
    piece. It counts files, not entries, because an excised file lost its
    entry yet stays listed as left — a count of entries would call that
    row both at once.
    """
    head = "Would remove" if dry_run else "Removed"
    noun = "entry" if len(plan.keys) == 1 else "entries"
    lines = [
        f"{head} the radialrig {noun} from {plan.client.label} - "
        f"{plan.removed} settings file(s) reversed, {plan.left} left.",
        "",
    ]
    lines.extend(render_block(plan.dispositions))
    lines.append("")
    lines.append(CONNECT_CLEAN_TAIL if plan.complete else CONNECT_LEFT_TAIL)
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    else:
        lines.append(plan.client.restart)
    return lines


def connect_not_connected_report(root, client) -> list[str]:
    return [
        f"Not registered: {client.label} for {root}",
        CONNECT_NOT_CONNECTED,
    ]


def connect_unknown_client_report(name: str, available) -> list[str]:
    """For stderr: the name is not one this build knows, so nothing was read."""
    offer = ", ".join(available) if available else "none"
    return [
        f"this build registers no client named '{name}'",
        f"it registers: {offer}",
    ]


# --- the entry whose project is gone ----------------------------------
# PLAN-0008 Phase 3. The same kit's sentences, for the case the reversal
# cannot reach: the record died with the project, so the entry is named
# rather than looked up. The rows arrive already built and the resolver
# that turns one into a sentence arrives with them, so this module still
# reaches for nothing that owns them.

ORPHAN_REFUSAL_HEAD = "Refused: the named entry was not removed. Nothing was written."
ORPHAN_NO_ROOT = (
    "this entry pins no project root, so radialrig cannot show it is an "
    "orphan; nothing was changed"
)
ORPHAN_NOT_FOUND = (
    "No copy of this client's settings carries this key. Nothing changed."
)
ORPHAN_REMOVED_TAIL = (
    "The entry is removed. This project carried no record; nothing else was held."
)
ORPHAN_LEFT_TAIL = (
    "The entry still stands in every settings file listed as left."
)
ORPHAN_NO_CLIENT = (
    'the orphan form needs a client name; "radialrig connect" names them'
)
ORPHAN_NO_PATH = (
    "the orphan form takes no project path - the entry's project is gone; run "
    "it as radialrig unconnect <client> --orphan <key>; nothing was changed"
)


def orphan_outside_prefix(client) -> str:
    """Why a key this kit never writes is not this verb's to remove."""
    return (
        f"this key is not one radialrig writes for {client.label}; radialrig "
        f"removes only entries starting with '{client.key_prefix}'. Nothing "
        f"was changed. Remove it in the client's own settings if you own it."
    )


def orphan_root_alive(client, root) -> str:
    """Why a standing project takes the reversal that restores byte for byte."""
    return (
        f"this entry's project root still exists: {root}. Run "
        f'"radialrig unconnect {client.name} {root}" at the project instead. '
        f"Nothing was changed."
    )


def orphan_report(plan, *, resolve, dry_run: bool = False) -> list[str]:
    """What the removal took out of each copy, and what it would not touch.

    `resolve` is required rather than defaulted: every row here comes
    from the kit that owns this verb, so a default reaching for another
    kit's table would render half of them by their code.

    The head counts an excise as done, which is the opposite of what
    `unconnect_report` counts. That verb promised a byte-identical
    restore and says so when it could not keep it; this one never had a
    record to promise one, so dropping the entry is the whole job. It
    says the files were rewritten rather than reversed, because that is
    all the rows claim: the entry went, and the rest was serialized
    again.
    """
    head = "Would remove" if dry_run else "Removed"
    lines = [
        f"{head} the entry {plan.key} from {plan.client.label} - "
        f"{plan.removed} settings file(s) rewritten, {plan.left} left.",
        "",
    ]
    lines.extend(render_rows(plan.dispositions, resolve))
    lines.append("")
    lines.append(ORPHAN_REMOVED_TAIL if plan.complete else ORPHAN_LEFT_TAIL)
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    else:
        lines.append(plan.client.restart)
    return lines


def orphan_refused_report(reason: str, *, dry_run: bool = False) -> list[str]:
    """The exit-3 answer: one head, and the one sentence that refused."""
    lines = [ORPHAN_REFUSAL_HEAD, "", reason]
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    return lines


def orphan_not_found_report(client, key: str) -> list[str]:
    return [
        f"Not found: {key} in {client.label}",
        ORPHAN_NOT_FOUND,
    ]


# --- the projects roster ----------------------------------------------
# PLAN-0008 Phase 3. Two builders over one listing: the sentences a
# reader gets, and the line a program parses. Neither reaches for the
# module that owns the roster — the rows arrive already built, and the
# resolver that turns a row into its sentence arrives with them, which is
# what keeps the import between the two running one way.

PROJECTS_HEAD = "Projects on this machine"
PROJECTS_NONE = (
    "No project is listed yet. A mount or a connect on a project adds it."
)
PROJECTS_MISMATCH_TAIL = (
    'A mismatch has a two-verb remedy: "radialrig unconnect <client> <PATH>" '
    'retires the record, "radialrig connect <client> <PATH>" registers again.'
)

# The one connected value that has a remedy, and the spelling the
# machine-readable line carries for it. This module owns that line's
# vocabulary — `mounted=`, `skills=`, and this — so the word stands once
# on each side of a boundary that runs one way.
PROJECTS_MISMATCH = "mismatch"


def projects_head(states, found) -> str:
    """The count line: what is listed, what was dropped, what is loose.

    A count of nought is not printed. A reader scanning a healthy machine
    should read "3 listed." and stop, rather than read two zeroes first.
    """
    listed = sum(1 for state in states if state.exists)
    dropped = len(states) - listed
    loose = sum(1 for orphan in found if orphan.root)
    counts = f"{listed} listed"
    if dropped:
        counts = f"{counts}, {dropped} dropped"
    if loose:
        counts = f"{counts}, {loose} orphan {'entry' if loose == 1 else 'entries'}"
    return f"{PROJECTS_HEAD} - {counts}."


def projects_report(states, found, rows, *, resolve) -> list[str]:
    """The whole answer of one `projects`: the counts, the rows, the remedy.

    Each tail prints only where it applies: the remedy where a mismatch
    stands, and the empty-roster sentence where the block is empty. A
    report that offered both at once would be saying something about a
    machine other than this one, and one that said nothing is listed
    above a row a reader can see would be contradicting itself.
    """
    lines = [projects_head(states, found)]
    if rows:
        lines.append("")
        lines.extend(render_rows(rows, resolve))
    tails: list[str] = []
    if not rows:
        tails.append(PROJECTS_NONE)
    if any(state.connected == PROJECTS_MISMATCH for state in states):
        tails.append(PROJECTS_MISMATCH_TAIL)
    if tails:
        lines.append("")
        lines.extend(tails)
    return lines


def projects_porcelain(states) -> str:
    """One tab-separated line per listed project. Finished text.

    Finished text rather than a list of lines, because an empty roster
    has to print zero bytes: a program reads this by counting lines, and
    one bare newline would tell it a project stands here. A project whose
    root is gone is dropped from the listing, so it is not printed
    either. The fields are `name=value` in a pinned order, and a later
    field appends at the end.
    """
    lines: list[str] = []
    for state in states:
        if not state.exists:
            continue
        lines.append("\t".join((
            f"root={state.root}",
            "mounted=yes" if state.mounted else "mounted=no",
            f"connected={state.connected}",
            "skills=" + (",".join(state.skills) if state.skills else "none"),
        )) + "\n")
    return "".join(lines)


# --- the update ----------------------------------------------------------
# PLAN-0021 phase 1. One report for `radialrig upgrade` and the two update
# presses: the seed block the upgrade always printed, then the skills a
# project already has, agent by agent, then one closing line that counts
# both halves. The agent's label comes off the harness map at render time,
# as every skill report's does, so no sentence here names one.

UPDATE_AGENT_HEAD = "Skills for {label}"
# An agent whose record this build could not read: the kit's own
# sentence under that agent's head, in place of its rows. It is left for
# review, which is what the exit code says of it; the closing line's
# count is of skills, and this is an agent's whole record.
UPDATE_AGENT_UNREAD = "Left for review: {error}"
UPDATE_SKILLS_FAILED = "The skills half of this update did not run: {error}"
# Both counts by one rule, the seed half's: a row LEFT or ABSENT is
# left for review. A current skill is not counted on either side.
UPDATE_CLOSING = (
    "{applied} file(s) applied, {pending} left for review. "
    "{refreshed} skill(s) refreshed, {left} left for review."
)
UPDATE_CLOSING_SEED = "{applied} file(s) applied, {pending} left for review."

# `--all`: the head counts, one line per project it skipped and why, then
# one report per project in listing order. The skip codes are the update
# module's own (`update.SKIP_GONE`, `update.SKIP_NOT_MOUNTED`), spelled
# here as the keys of this table so this module imports nothing above it;
# a test holds the two spellings together.
UPDATE_ALL_HEAD = "{verb} {count} listed project(s); {skipped} skipped."
UPDATE_SKIPPED = "Skipped {root}: {why}."
UPDATE_SKIP_WORDS: dict[str, str] = {
    "gone": "no project stands at this path",
    "not-mounted": "not mounted",
}


def _seed_block(seed, *, dry_run: bool) -> list[str]:
    """The upgrade's seed half as it has always printed: the head, the
    rows, and the note for a record without restore fields."""
    head = "Would check" if dry_run else "radialrig"
    lines = [
        f"{head} {seed.to_version} - seed check for {seed.root} "
        f"(mounted {seed.manifest.get('mounted', '?')} at {seed.from_version})",
        "",
    ]
    lines.extend(render_block(seed.dispositions()))
    lines.append("")
    if seed.degraded:
        lines.append(mnt.DEGRADED_NOTE)
    return lines


@dataclass(frozen=True)
class UpdateSection:
    """One block of an update's report: its head line, the rows inside
    it, and every line of it as the command line prints them, the head
    and the rows included. The manage page draws the rows as a table
    under the head and keeps the other lines as sentences, so what it
    says is what the terminal said, block by block."""

    head: str
    rows: tuple[mnt.Disposition, ...]
    lines: tuple[str, ...]


@dataclass(frozen=True)
class UpdateParts:
    """An update's report in the pieces the page draws: the seed block;
    the sentences between it and the agents, where the report has them
    (the kit's none line, or the sentence for a skills half that did not
    run); one block per agent; and the tail, the closing line and the
    diffs. `lines` is the report itself, before the engine-half tail."""

    seed: UpdateSection
    said: tuple[str, ...]
    agents: tuple[UpdateSection, ...]
    tail: tuple[str, ...]

    def lines(self) -> list[str]:
        out = list(self.seed.lines)
        out.extend(self.said)
        for agent in self.agents:
            out.extend(agent.lines)
        out.extend(self.tail)
        return out


def update_parts(plan, *, dry_run: bool = False) -> UpdateParts:
    """One update's report, in parts.

    The skills half is one block per agent whose record lists a skill,
    the refreshed ones as the rows the add writes and the rest as the
    one row `skill list` prints for them; an agent with no record gets
    no block and no sentence about it, and an agent whose record could
    not be read gets its block with the kit's sentence in place of rows.
    Where no agent has one, the skills kit's own sentence says so. A
    skills half that did not run says what stopped it in the kit's own
    words, under the seed block that says what was written, and the
    closing line then counts the seed alone.
    """
    seed = plan.seed
    seed_lines = _seed_block(seed, dry_run=dry_run)
    said: list[str] = []
    agents: list[UpdateSection] = []
    tail: list[str] = []
    if plan.skills_error:
        said = [UPDATE_SKILLS_FAILED.format(error=plan.skills_error), ""]
        tail.append(UPDATE_CLOSING_SEED.format(
            applied=seed.applied, pending=seed.pending))
    else:
        if not plan.skills:
            said = [SKILL_NONE_TAIL, ""]
        for label in dict.fromkeys(r.harness.label for r in plan.skills):
            group = [r for r in plan.skills if r.harness.label == label]
            head = UPDATE_AGENT_HEAD.format(label=label)
            unread = [r for r in group if r.error]
            rows = () if unread else tuple(d for r in group for d in r.dispositions)
            lines = [head, ""]
            if unread:
                lines.append(UPDATE_AGENT_UNREAD.format(error=unread[0].error))
            else:
                lines.extend(render_block(rows))
            lines.append("")
            agents.append(UpdateSection(head, rows, tuple(lines)))
        tail.append(UPDATE_CLOSING.format(
            applied=seed.applied, pending=seed.pending,
            refreshed=plan.refreshed, left=plan.pending_skills))
    diffs = [i.diff for i in seed.items if i.diff]
    diffs.extend(text for r in plan.skills for text in r.diffs)
    for text in diffs:
        tail.append("")
        tail.extend(text.splitlines())
    return UpdateParts(
        UpdateSection(seed_lines[0], tuple(seed.dispositions()), tuple(seed_lines)),
        tuple(said), tuple(agents), tuple(tail),
    )


def update_report(plan, *, dry_run: bool = False, closing: bool = True) -> list[str]:
    """The whole answer of one update: both halves, the counts, the
    diffs, as `update_parts` composes them, then the engine-half tail.

    `closing=False` leaves off the engine-half tail and the rehearsal
    line, for a report of several projects that prints them once.
    """
    lines = update_parts(plan, dry_run=dry_run).lines()
    if closing:
        lines.append("")
        lines.append(ENGINE_HALF)
        if dry_run:
            lines.append(DRY_RUN_TAIL)
    return lines


def update_skipped_line(item) -> str:
    """The one sentence for a listed project `--all` did not update:
    the folder, and why in the table's words or the engine's own."""
    why = UPDATE_SKIP_WORDS.get(item.skipped, item.skipped) if item.skipped else item.error
    return UPDATE_SKIPPED.format(root=item.root, why=why)


def update_all_report(found, *, dry_run: bool = False) -> list[str]:
    """`radialrig upgrade --all`: the counts, the skipped projects and
    why, then one report per project headed by its folder, and the
    engine-half tail once at the end."""
    found = tuple(found)
    checked = [item for item in found if item.plan is not None]
    lines = [UPDATE_ALL_HEAD.format(
        verb="Would check" if dry_run else "Checked",
        count=len(checked), skipped=len(found) - len(checked))]
    for item in found:
        if item.plan is None:
            lines.append(update_skipped_line(item))
    for item in checked:
        lines.append("")
        lines.extend(update_report(item.plan, dry_run=dry_run, closing=False))
    lines.append("")
    lines.append(ENGINE_HALF)
    if dry_run:
        lines.append(DRY_RUN_TAIL)
    return lines
