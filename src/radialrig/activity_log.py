r"""The activity log: the one written trace of what RadialRig did here.

PLAN-0018 phase 2 (BUG-0014). Every command run, page press, server
start and tool call leaves one event in a plain-text file beside the
roster: `%APPDATA%\radialrig\radialrig.log` on Windows,
`$XDG_DATA_HOME/radialrig/radialrig.log` on Linux, through the one
table `roster.STORES` already carries. A platform with no roster row
keeps no log either.

An event is one header line and, under it, the lines a person saw: the
report text, and for an error the traceback. The header starts at
column 0 with the local time; every other line of an event is indented
by two spaces, so no report line can ever read as the start of an
event, and `radialrig log` can print the last twenty whole ones across
the current file and the earlier one.

The file is best effort the way the roster is (Invariant 1): `record`
never raises, and a failure to write changes no exit code, no report,
no page and no tool result. Several processes append at once, one pair
of servers per open project, so every append and every rotation runs
under an OS file lock on `radialrig.log.lock`; the log itself is opened
only while that lock is held and closed before it is let go, so no
process has it open when a rotation renames it (Verified 12). A writer
that cannot take the lock within a second drops that one event.

What the log never holds (Decided 3): a file's contents, a record's
text, or a tool's arguments, except each write verb's target.

The dependency runs one way: this module imports `mount` and `roster`,
and neither of them, nor `connect` nor `skills`, imports it.
"""

from __future__ import annotations

import datetime as dt
import functools
import inspect
import os
import re
import sys
import time
import traceback
from pathlib import Path

from radialrig.mount import installed_version
from radialrig.roster import roster_path

LOG_NAME = "radialrig.log"
OLDER_NAME = "radialrig.log.1"
LOCK_NAME = "radialrig.log.lock"

# Decided 2: at 1 MiB the file becomes the earlier one, so the two
# together never take much more than 2 MiB.
CAP = 1 << 20
# Prescription 1: how long a writer waits for the lock before it drops
# its one event rather than delay the run's answer any further.
LOCK_BUDGET = 1.0
LOCK_PAUSE = 0.01
# Decided 5: how many events `radialrig log` prints.
TAIL = 20

# The surfaces, in the words Constraint 3 fixes: never a client, never a
# vendor.
COMMAND = "command"
SETUP_PAGE = "setup page"
MANAGE_PAGE = "manage page"
READ_SERVER = "read server"
WRITE_SERVER = "write server"

# The shape of an event: header fields set two spaces apart, detail
# lines two spaces in. A header is told by its stamp and the word after
# it, which no indented line can start with.
INDENT = "  "
SEP = "  "
STAMP = "%Y-%m-%d %H:%M:%S"
HEADER = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}  radialrig ")


# --- where the files are ----------------------------------------------

def log_path(*, env=None) -> Path | None:
    r"""%APPDATA%\radialrig\radialrig.log on Windows,
    $XDG_DATA_HOME/radialrig/radialrig.log on Linux, else None.

    None means what it means for the roster: this platform keeps
    nothing, so a write writes nothing and a read answers nothing.
    """
    roster = roster_path(env=env)
    if roster is None:
        return None
    return roster.parent / LOG_NAME


# --- one event as text ------------------------------------------------

def format_event(surface: str, what: str, *, folder="", outcome: str = "",
                 lines=(), error: BaseException | None = None,
                 when: dt.datetime | None = None) -> str:
    """One event, header and detail, ending in a newline.

    The header carries the local time, the engine version, the surface,
    the process id, what ran, then the folder it ran against and its
    outcome where the caller gave them, and for an error its type and
    message. Every line of `lines` and of the error's traceback goes
    under it, indented, one text line per line.
    """
    fields = [
        (when or dt.datetime.now()).strftime(STAMP),
        f"radialrig {installed_version()}",
        surface,
        f"pid {os.getpid()}",
        _one_line(what),
    ]
    if folder:
        fields.append(f"folder {_one_line(str(folder))}")
    if outcome:
        fields.append(_one_line(outcome))
    if error is not None:
        fields.append(f"error {type(error).__name__}: {_one_line(str(error))}")
    out = [SEP.join(fields)]
    for text in lines:
        out.extend(_indented(str(text)))
    if error is not None:
        out.extend(_indented("".join(
            traceback.format_exception(type(error), error, error.__traceback__))))
    return "\n".join(out) + "\n"


def _one_line(text: str) -> str:
    return " ".join(str(text).splitlines()).strip()


def _indented(text: str) -> list[str]:
    return [INDENT + part for part in (text.splitlines() or [""])]


# --- the lock ---------------------------------------------------------
# Each platform's own file lock, non-blocking, tried until the budget
# is spent. A byte-range lock on Windows and flock on Linux are both
# per open file, so two threads of one process queue here the way two
# processes do.

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


def _take(lock: Path) -> int | None:
    """The lock file's descriptor with the lock held, or None once the
    budget is spent. `_release` gives it back."""
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + LOCK_BUDGET
    try:
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(LOCK_PAUSE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _release(fd: int) -> None:
    _unlock(fd)
    os.close(fd)


# --- the append -------------------------------------------------------

def record(surface: str, what: str, *, folder="", outcome: str = "",
           lines=(), error: BaseException | None = None, env=None) -> None:
    """Append one event. Best effort; never raises.

    Nothing this function meets may reach the caller: the run's exit
    code, report, page or tool result is the promise, and the log is a
    trace of it, never a condition on it (Invariant 1).
    """
    try:
        path = log_path(env=env)
        if path is None:
            return
        data = format_event(
            surface, what, folder=folder, outcome=outcome, lines=lines, error=error,
        ).encode("utf-8", "replace")
        _append(path, data)
    except Exception:
        return


def _append(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _take(path.with_name(LOCK_NAME))
    if fd is None:
        return
    try:
        _rotate(path)
        # Opened under the lock and closed before it is released, so no
        # process ever has the log open while another renames it.
        with open(path, "ab") as log:
            log.write(data)
    finally:
        _release(fd)


def _rotate(path: Path) -> None:
    """The current file becomes the earlier one once it has reached the
    cap. Called under the lock, before the append."""
    try:
        if path.stat().st_size < CAP:
            return
        os.replace(path, path.with_name(OLDER_NAME))
    except OSError:
        # No file yet, or a reader holds it open on Windows: the event
        # still lands, and the next append tries the rotation again.
        return


# --- the read ---------------------------------------------------------

def events(text: str) -> list[str]:
    """The text as whole events, each a header with its detail lines."""
    out: list[str] = []
    for line in text.splitlines():
        if HEADER.match(line) or not out:
            out.append(line)
        else:
            out[-1] = f"{out[-1]}\n{line}"
    return out


def last_events(count: int = TAIL, *, env=None) -> list[str] | None:
    """The last `count` events across the earlier file and the current
    one, oldest first; an empty list when there is no log yet, and None
    when this platform keeps none. Reads without the lock: a rotation
    that meets an open reader on Windows waits for the next append."""
    path = log_path(env=env)
    if path is None:
        return None
    found: list[str] = []
    for name in (OLDER_NAME, LOG_NAME):
        try:
            text = path.with_name(name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.extend(events(text))
    return found[-count:] if count else found


# --- the tool wrapper -------------------------------------------------

def logged(fn, surface: str, *, target: str | None = None):
    """`fn` as a tool that leaves one line per call: its name, the
    result's `kind`, the time taken, and for a write verb the argument
    `target` names (Decided 3). `functools.wraps` keeps the name, the
    docstring and the signature the SDK reads, so the tool list is byte
    for byte what the bare function gives (Verified 11). An exception is
    recorded with its traceback and re-raised unchanged."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        what = fn.__name__
        if target:
            try:
                value = signature.bind_partial(*args, **kwargs).arguments.get(target)
            except TypeError:
                value = None
            if isinstance(value, str) and value:
                what = f"{what} {value}"
        started = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            record(surface, what, outcome=f"{_elapsed(started)} ms", error=exc)
            raise
        kind = result.get("kind", "") if isinstance(result, dict) else ""
        record(surface, what, outcome=f"kind {kind}, {_elapsed(started)} ms")
        return result

    return wrapper


def _elapsed(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
