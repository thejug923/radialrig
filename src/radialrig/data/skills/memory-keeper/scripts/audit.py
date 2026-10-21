"""The mechanical audit of the memory-keeper skill.

Run from anywhere with the Python 3 on this machine:

    python <skill>/scripts/audit.py <memory directory> [--transcripts <dir>]

It reads the store and prints what an upkeep pass should look at:
index lines over length or carrying status words, dates, hashes or
issue numbers; files with no index line and lines with no file; files
named after an event rather than a lesson; and, when a directory of
session transcripts is given, how many sessions opened each file.

It writes nothing, anywhere. Every line it prints is a candidate for
the pass to judge, never a verdict: a standing decision of the person
lives in its index line and is kept however rarely its file is opened.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

INDEX_NAME = "MEMORY.md"
LINE_LIMIT = 150

# Words that describe the state of work rather than a lesson. A line
# carrying one summarises its file, and the summary goes stale first.
STATUS_WORDS = (
    "done", "in progress", "pending", "blocked", "closed", "shipped",
    "todo", "wip", "merged", "resolved", "open question", "next step",
)
HASH = re.compile(r"\b[0-9a-f]{7,40}\b")
ISSUE = re.compile(r"(?<![\w-])#\d+\b")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# A file named for the event that produced it: a date, a plan, an
# issue, a release, a bug or a pull request, instead of the question
# it answers.
EVENT_NAME = re.compile(
    r"(\d{4}-\d{2}-\d{2}|\d{8}|(^|-)(plan|issue|release|bug|pr|review)-?\d|(^|-)v\d+[-.]\d)",
    re.IGNORECASE,
)
ENTRY = re.compile(r"^\s*[-*]\s+(.*)$")
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def index_entries(index: Path) -> list[tuple[int, str, str | None]]:
    """(line number, text, linked file or None) for each bullet line."""
    entries: list[tuple[int, str, str | None]] = []
    for number, raw in enumerate(index.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        match = ENTRY.match(raw)
        if match is None:
            continue
        link = LINK.search(match.group(1))
        target = link.group(1).split("#", 1)[0].strip() if link else None
        entries.append((number, raw.rstrip(), target or None))
    return entries


def audit_lines(entries) -> list[str]:
    out: list[str] = []
    for number, text, _ in entries:
        lowered = text.lower()
        if len(text) > LINE_LIMIT:
            out.append(f"line {number}: {len(text)} characters, over {LINE_LIMIT}")
        words = [w for w in STATUS_WORDS if re.search(r"\b" + re.escape(w) + r"\b", lowered)]
        if words:
            out.append(f"line {number}: status word(s) {', '.join(words)}")
        if HASH.search(text):
            out.append(f"line {number}: carries a commit hash")
        if ISSUE.search(text):
            out.append(f"line {number}: carries an issue or pull request number")
        if DATE.search(text):
            out.append(f"line {number}: carries a date")
    return out


def memory_files(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix == ".md" and p.name != INDEX_NAME
    )


def audit_reconciliation(directory: Path, entries, files) -> list[str]:
    out: list[str] = []
    linked = {t for _, _, t in entries if t}
    for number, _, target in entries:
        if target is None:
            out.append(f"line {number}: no file linked")
        elif not (directory / target).is_file():
            out.append(f"line {number}: links {target}, which does not exist")
    for path in files:
        if path.name not in linked:
            out.append(f"{path.name}: no index line")
    return out


def audit_names(files) -> list[str]:
    return [f"{p.name}: named after an event" for p in files if EVENT_NAME.search(p.stem)]


def open_rates(directory: Path, files, transcripts: Path) -> list[str]:
    """Sessions that opened each file, from the transcripts at the top of
    the given directory (helper agents keep theirs in subdirectories and
    are not counted). A file name inside a memory path counts as an open,
    whichever way the path's separators are written."""
    sessions = sorted(p for p in transcripts.iterdir() if p.is_file() and p.suffix == ".jsonl")
    if not sessions:
        return ["no session transcripts found"]
    marker = directory.name
    seps = ("/", "\\\\", "\\")
    texts = [s.read_text(encoding="utf-8", errors="replace") for s in sessions]
    out: list[str] = []
    for path in files:
        needles = [marker + sep + path.name for sep in seps]
        opened = sum(1 for text in texts if any(n in text for n in needles))
        out.append(f"{path.name}: opened in {opened} of {len(sessions)} sessions")
    return out


def report(directory: Path, transcripts: Path | None) -> int:
    write = sys.stdout.write
    if not directory.is_dir():
        write(f"refused: {directory} is not a directory\n")
        return 2
    index = directory / INDEX_NAME
    files = memory_files(directory)
    write(f"store: {directory} ({len(files)} file(s) beside the index)\n")
    if not index.is_file():
        write(f"index: no {INDEX_NAME}; every file below has no index line\n")
        entries = []
    else:
        entries = index_entries(index)
        write(f"index: {len(entries)} entr(y/ies)\n")

    def section(title: str, lines: list[str]) -> None:
        write(f"\n{title}\n")
        write("".join(f"  {line}\n" for line in lines) if lines else "  none\n")

    section("index lines to look at", audit_lines(entries))
    section("index and files out of step", audit_reconciliation(directory, entries, files))
    section("files named after an event", audit_names(files))
    if transcripts is not None:
        if not transcripts.is_dir():
            section("open rates", [f"{transcripts} is not a directory"])
        else:
            section("open rates", open_rates(directory, files, transcripts))
    write("\nnothing was written\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit.py", description="Print the memory store's audit; write nothing.",
    )
    parser.add_argument("directory", help="the memory directory holding the index")
    parser.add_argument("--transcripts", help="a directory of session transcripts (.jsonl)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    transcripts = Path(args.transcripts).expanduser() if args.transcripts else None
    return report(Path(args.directory).expanduser(), transcripts)


if __name__ == "__main__":
    raise SystemExit(main())
