#!/usr/bin/env python3
"""The mechanical half of the session-handoff skill.

SKILL.md carries the judgement: what to sweep, what to write, what to
say. This file carries the parts that must not be re-derived by a model
each time, because each of them has exactly one right answer and a
silent failure mode:

    stamp   [ROOT]      the handoff directory and the timestamped name
    file    <PATH>      retention, the ignore rule, the word count, the check
    kickoff <PATH>      render the kickoff prompt, copy it, print it
    copy    <PATH>      an already-written prompt onto the clipboard
    check   <PATH>      the word count and the pass condition, for a re-run

The word count is information, never a failure: SKILL.md says to cut
what a cold reader does not need, and the number tells the agent where
the file stands without fixing a length in advance.

The kickoff prompt's bytes live here, in KICKOFF_TEMPLATE, and nowhere
else. Agents rewrap prose differently from one session to the next;
the prompt the user pastes must not vary, so `kickoff` renders it with
one substitution (the handoff's absolute path) and the skill forbids
retyping it.

Three constraints are enforced here rather than described, because
prose is followed probabilistically and a guard is not. Retention
**moves** surplus handoffs into the archive and never deletes one.
`file` refuses any target that is not a HANDOFF_*.md inside a handoffs
directory, so no other path is ever touched. `copy` refuses text that
is not ASCII, or that carries a dash as punctuation, because the prompt
crosses the clipboard, where non-ASCII mangles on Windows OEM code
pages. A hyphen inside a word or a path is not punctuation and is left
alone: the prompt has to name the handoff by absolute path, and a host
path may be hyphenated, so a blanket ban would make the copy step
unsatisfiable on those hosts. `copy` reports what happened and never
claims a copy that failed.

REQUIRED_SECTIONS below is the same list as the output template in
SKILL.md, in the same order. The two are pinned equal by a test in the
package that ships this file: a section added to one and not the other
is a drift the next agent would pay for.

Standard library only, Python 3 as it is found on the host.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# The output template's H2 sections, in order. Pinned equal to the
# template in SKILL.md by test_skill_sources.py.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "Standing constraints",
    "Decisions locked + what shipped",
    "Still broken",
    "Superseded this session",
    "Key files",
    "Running state",
    "Verification",
    "Deferred + open questions",
    "Pick up here",
)

HANDOFF_DIR = "handoffs"
ARCHIVE_DIR = "archive"
KEEP = 5
NAME_RE = re.compile(r"^HANDOFF_[0-9]{8}_[0-9]{4}\.md$")
STAMP_FORMAT = "%m%d%Y_%H%M"
IGNORE_FORMS = {HANDOFF_DIR, HANDOFF_DIR + "/", "/" + HANDOFF_DIR, "/" + HANDOFF_DIR + "/"}
FOOTER_MARK = "Skill: session-handoff"
KICKOFF_MARK = "Fresh session pickup"

# The one prompt the user pastes into the fresh session. One
# substitution, no other variation: these bytes are the format. ASCII
# only, no dash punctuation, and each paragraph is one physical line:
# any newline in this string ships in every paste, so a wrap frozen
# here reads as broken sentences in the fresh session. A hyphen inside
# a word (Re-verify) is prose, not punctuation, and the guard below
# permits it.
KICKOFF_TEMPLATE = """Fresh session pickup. Read {path} first. It is the handoff from the previous session.

"Standing constraints" quotes the project's rule files and records: confirm each line against the source it names before relying on it, and treat a line with no source, or one drawn from agent memory, as a lead to confirm rather than an order. "Decisions locked" holds the person's decisions as the previous session recorded them; where a line names a governed record, the record wins over the note. Re-verify "Running state" rather than trusting it, and run the commands under "Verification" plus the project's basic tests before choosing any work; report anything that does not match. Then continue with "Pick up here", stopping for confirmation before anything hard to undo.
"""

# A dash standing as punctuation: an en or em dash anywhere, or a run of
# hyphens standing alone between spaces. A hyphen inside a word or inside
# an absolute path is not punctuation and does not match, because the
# prompt must name the handoff by absolute path and a host path may be
# hyphenated. A blanket ban on the character would make the copy step
# unsatisfiable on those hosts, which is the one thing this guard may
# never do.
DASH_PUNCTUATION = re.compile(r"[\u2013\u2014]|(?<![^\s])-+(?![^\s])")


class Refused(Exception):
    """A target this script will not touch."""


# --- locating things --------------------------------------------------


def _handoff_dir(root: Path) -> Path:
    return root / HANDOFF_DIR


def _target(raw: str) -> Path:
    """The handoff file named on the command line, or a refusal.

    The guard is the whole point of routing the write through a script:
    only a HANDOFF_*.md sitting directly inside a handoffs directory is
    ever read, moved, or counted, so a wrong argument cannot reach the
    rest of the project.
    """
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise Refused("the path must be absolute")
    path = Path(os.path.normpath(str(path)))
    if path.parent.name != HANDOFF_DIR:
        raise Refused("the file must sit directly inside a " + HANDOFF_DIR + " directory")
    if not NAME_RE.match(path.name):
        raise Refused("the file must be named HANDOFF_<mmddyyyy_hhmm>.md")
    return path


def _stamped(directory: Path) -> list[Path]:
    """Every handoff in the directory, newest modification time first.

    Modification time, not the filename stamp: the stamps do not sort
    chronologically across a year boundary.
    """
    found = [p for p in directory.glob("HANDOFF_*.md") if p.is_file()]
    return sorted(found, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


# --- retention --------------------------------------------------------


def _free_name(archive: Path, name: str) -> Path:
    """A destination inside the archive that overwrites nothing."""
    candidate = archive / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    n = 2
    while (archive / f"{stem}_{n}{suffix}").exists():
        n += 1
    return archive / f"{stem}_{n}{suffix}"


def _retain(directory: Path) -> str:
    """Move all but the newest KEEP handoffs into the archive.

    Nothing is deleted, here or anywhere else in this file. A move is
    reversible and a delete is not, so the surplus goes to the archive
    and the caller reports where it went.
    """
    stamped = _stamped(directory)
    surplus = stamped[KEEP:]
    moved = 0
    if surplus:
        archive = directory / ARCHIVE_DIR
        archive.mkdir(parents=True, exist_ok=True)
        for path in surplus:
            shutil.move(str(path), str(_free_name(archive, path.name)))
            moved += 1
    kept = len(stamped) - moved
    tail = f"moved {moved} to {HANDOFF_DIR}/{ARCHIVE_DIR}/" if moved else "moved nothing"
    return f"retention: {kept} handoff file(s) kept; {tail}; nothing deleted"


# --- the ignore rule --------------------------------------------------


def _git_root(root: Path) -> Path | None:
    if shutil.which("git") is None:
        return None
    for candidate in [root, *root.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _tracks_handoffs(git_root: Path) -> bool:
    try:
        done = subprocess.run(
            ["git", "ls-files", "--", HANDOFF_DIR],
            cwd=str(git_root), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and bool(done.stdout.strip())


def _already_ignored(git_root: Path) -> bool:
    ignore = git_root / ".gitignore"
    if not ignore.is_file():
        return False
    text = ignore.read_text(encoding="utf-8", errors="replace")
    return any(line.strip() in IGNORE_FORMS for line in text.splitlines())


def _ignore_rule(root: Path, *, write: bool) -> str:
    """Report the ignore rule's state, and apply it where it can hold.

    Three states, three honest answers. Writing the line into a project
    that already tracks the directory would look like the control was
    applied while git ignored it, so that case writes nothing and says
    so.
    """
    git_root = _git_root(root)
    if git_root is None:
        return "ignore rule: not a git repository; nothing written"
    if _tracks_handoffs(git_root):
        return (
            "ignore rule: " + HANDOFF_DIR + "/ is tracked in this project; "
            "left .gitignore alone"
        )
    if _already_ignored(git_root):
        return "ignore rule: " + HANDOFF_DIR + "/ is already ignored"
    if not write:
        return "ignore rule: " + HANDOFF_DIR + "/ is not ignored yet"
    ignore = git_root / ".gitignore"
    existing = ignore.read_text(encoding="utf-8", errors="replace") if ignore.is_file() else ""
    prefix = "" if (not existing or existing.endswith("\n")) else "\n"
    with ignore.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(prefix + HANDOFF_DIR + "/\n")
    return "ignore rule: added " + HANDOFF_DIR + "/ to " + str(ignore)


# --- the pass condition -----------------------------------------------


def _sections(text: str) -> list[tuple[str, str]]:
    """Every H2 in the file, paired with the body under it."""
    parts: list[tuple[str, str]] = []
    heading, body = None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                parts.append((heading, "\n".join(body)))
            heading, body = line[3:].strip(), []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        parts.append((heading, "\n".join(body)))
    return parts


def _problems(path: Path) -> list[str]:
    """Everything wrong with this handoff, in reading order.

    The oracle is stated in reader's terms in SKILL.md; this is the
    checkable half of it. An empty list is the verdict "pass".
    """
    if not path.is_file():
        return ["no file at that path"]
    text = path.read_text(encoding="utf-8", errors="replace")
    found = _problems_in_text(text)
    found.extend(_problems_around(path))
    return found


def _problems_in_text(text: str) -> list[str]:
    found: list[str] = []
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# Session Handoff"):
        found.append("the first line is not a '# Session Handoff' title")
    present = _sections(text)
    names = [name for name, _ in present]
    for wanted in REQUIRED_SECTIONS:
        if wanted not in names:
            found.append(f"missing section: {wanted}")
    order = [n for n in names if n in REQUIRED_SECTIONS]
    if order and order != [s for s in REQUIRED_SECTIONS if s in order]:
        found.append("the sections are out of template order")
    for name, body in present:
        if name in REQUIRED_SECTIONS and not body.strip():
            found.append(f"empty section: {name} (write 'none' rather than nothing)")
    if FOOTER_MARK not in text:
        found.append("no provenance footer")
    if KICKOFF_MARK in text:
        found.append("the kickoff prompt belongs in chat, not in this file")
    if not text.isascii():
        found.append("the file is not ASCII")
    return found


def _word_count(path: Path) -> str:
    """The information line: how long the file is, with no verdict in it."""
    if not path.is_file():
        return "words: no file at that path"
    text = path.read_text(encoding="utf-8", errors="replace")
    return f"words: {len(text.split())}; cut resolved detours first, then background"


def _problems_around(path: Path) -> list[str]:
    found: list[str] = []
    directory = path.parent
    kept = len(_stamped(directory))
    if kept > KEEP:
        found.append(f"{kept} handoff files kept; retention allows {KEEP}")
    state = _ignore_rule(directory.parent, write=False)
    if "is not ignored yet" in state:
        found.append(state)
    return found


# --- subcommands ------------------------------------------------------


def cmd_stamp(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    directory = _handoff_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    name = "HANDOFF_" + dt.datetime.now().strftime(STAMP_FORMAT) + ".md"
    sys.stdout.write(f"dir {directory}\n")
    sys.stdout.write(f"name {name}\n")
    return 0


def cmd_file(args: argparse.Namespace) -> int:
    path = _target(args.path)
    if not path.is_file():
        raise Refused("no file at that path; write it first")
    sys.stdout.write(_retain(path.parent) + "\n")
    sys.stdout.write(_ignore_rule(path.parent.parent, write=True) + "\n")
    return _verdict(path)


def cmd_check(args: argparse.Namespace) -> int:
    return _verdict(_target(args.path))


def _verdict(path: Path) -> int:
    sys.stdout.write(_word_count(path) + "\n")
    found = _problems(path)
    if not found:
        sys.stdout.write("check: pass\n")
        return 0
    sys.stdout.write("check: FAILED - " + "; ".join(found) + "\n")
    return 1


def _clipboard_attempts() -> list[tuple[str, list[str], bool]]:
    """(tool, argv, feed_stdin) for this platform, in preference order."""
    if os.name == "nt":
        command = "Get-Content -Raw -Encoding UTF8 -LiteralPath $env:HANDOFF_PROMPT | Set-Clipboard"
        return [("Set-Clipboard", ["powershell", "-NoProfile", "-NonInteractive",
                                   "-Command", command], False)]
    if sys.platform == "darwin":
        return [("pbcopy", ["pbcopy"], True)]
    return [
        ("wl-copy", ["wl-copy"], True),
        ("xclip", ["xclip", "-selection", "clipboard"], True),
        ("xsel", ["xsel", "--input", "--clipboard"], True),
    ]


def _prompt_defect(text: str) -> str | None:
    """Why this text may not cross the clipboard, or None."""
    if not text.isascii():
        return "the prompt must be ASCII"
    if DASH_PUNCTUATION.search(text):
        return "the prompt must carry no dash punctuation"
    return None


def _copy_text(text: str, source: Path) -> tuple[str, int]:
    """(status line, exit code) for putting `text` on the clipboard.

    `source` is a file already holding exactly `text`; the Windows path
    reads the file rather than the pipe, because a pipe is decoded as
    the console code page and mangles.
    """
    environment = dict(os.environ, HANDOFF_PROMPT=str(source))
    tried: list[str] = []
    for tool, argv, feed in _clipboard_attempts():
        if shutil.which(argv[0]) is None:
            continue
        tried.append(tool)
        try:
            done = subprocess.run(
                argv, input=text.encode("utf-8") if feed else None,
                capture_output=True, env=environment, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return f"copied to the clipboard with {tool}", 0
    reason = "every tool failed: " + ", ".join(tried) if tried else "no clipboard tool on this machine"
    return f"not copied: {reason}", 1


def cmd_kickoff(args: argparse.Namespace) -> int:
    """Render the kickoff prompt, copy it, and print it verbatim.

    The status line comes first; everything after the blank line IS the
    prompt, byte for byte. The caller pastes that block unchanged. A
    failed copy still prints the prompt, because it is the only prompt
    there is; the status line says plainly what did not happen.
    """
    path = _target(args.path)
    if not path.is_file():
        raise Refused("no file at that path; write the handoff first")
    text = KICKOFF_TEMPLATE.format(path=path)
    defect = _prompt_defect(text)
    if defect is None:
        rendered = path.parent / (path.stem + ".kickoff.tmp")
        rendered.write_text(text, encoding="utf-8", newline="\n")
        try:
            status, code = _copy_text(text, rendered)
        finally:
            rendered.unlink(missing_ok=True)
    else:
        status, code = "not copied: " + defect, 1
    sys.stdout.write(status + "\n\n")
    sys.stdout.write(text)
    return code


def cmd_copy(args: argparse.Namespace) -> int:
    """Put an already-written prompt on the clipboard, and say what happened.

    The fallback when `kickoff` cannot be used. The failure path prints
    "not copied" and exits non-zero, because the only thing worse than a
    prompt that did not reach the clipboard is a report that says it did.
    """
    path = Path(args.path).expanduser()
    if not path.is_file():
        sys.stdout.write("not copied: no file at that path\n")
        return 1
    text = path.read_text(encoding="utf-8", errors="replace")
    defect = _prompt_defect(text)
    if defect is not None:
        sys.stdout.write("not copied: " + defect + "\n")
        return 1
    status, code = _copy_text(text, path)
    sys.stdout.write(status + "\n")
    return code


# --- entry point ------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="handoff.py", description="The mechanical steps of the session-handoff skill.",
    )
    subcommands = parser.add_subparsers(dest="command")

    stamp = subcommands.add_parser("stamp", help="print the handoff directory and filename")
    stamp.add_argument("root", nargs="?", default=".", help="project root; the default is the working directory")
    stamp.set_defaults(run=cmd_stamp)

    write = subcommands.add_parser("file", help="retention, the ignore rule, the word count, then the check")
    write.add_argument("path", help="absolute path of the handoff just written")
    write.set_defaults(run=cmd_file)

    kickoff = subcommands.add_parser(
        "kickoff", help="render the kickoff prompt, copy it, print it verbatim"
    )
    kickoff.add_argument("path", help="absolute path of the handoff just written")
    kickoff.set_defaults(run=cmd_kickoff)

    copy = subcommands.add_parser("copy", help="put an already-written prompt on the clipboard")
    copy.add_argument("path", help="path of the file holding the prompt")
    copy.set_defaults(run=cmd_copy)

    check = subcommands.add_parser("check", help="re-run the word count and the pass condition")
    check.add_argument("path", help="absolute path of the handoff")
    check.set_defaults(run=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if getattr(args, "run", None) is None:
        parser.print_usage(sys.stdout)
        return 0
    try:
        return args.run(args)
    except Refused as refusal:
        sys.stdout.write(f"refused: {refusal}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
