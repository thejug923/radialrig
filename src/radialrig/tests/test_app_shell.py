"""Tests for the desktop shell's own surface — PLAN-0008 Phase 5.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_app_shell.py -q

The shell renders one screen the engine cannot serve, because it runs
before the engine exists: the bootstrap screen. Prescription 2 grants it
that screen and its strings, and this file is the compensating hold —
the same word, brand, and color rules the engine's own pages pass, over
the shell's sentences, its markup, its pinned config, and its Rust.

Nothing here starts a server or drives a verb. Every check reads files
out of this repository, so the oracle is the file the shell ships, not a
copy restated somewhere else. The strings file's key set is the one
exception: it is restated here as a literal, because a test that reads
its expectations out of the file under test would pass whatever that
file happened to say.

These tests never ship, so there is no installed host that could run
them without an `app/` tree beside them. A missing tree is a failure
here, never a skip.
"""

from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from radialrig import manage_web
from radialrig import setup_web

# The repository, found the way the tests' own location fixes it, and
# proved by the file that only the repository root carries.
ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "app"
SHELL_SRC = APP / "src"
TAURI = APP / "src-tauri"
RUST_SRC = TAURI / "src"
PAGE_CSS = ROOT / "src" / "radialrig" / "data" / "web" / "page.css"

# --- the rules, as the engine's own pages carry them -------------------

# D-33 as the owner amended it on 2026-08-18: the engine's own pages get
# the whole-word reading because one skill's name legitimately carries
# the substring. The shell names no skill and never will — item 6 of this
# file forbids it outright — so the shell has no legitimate carrier, and
# the sweep here is the strict containment one: the letters anywhere in a
# word are a hit.
FORBIDDEN = ("hand", "manual", "human")

# The brand's own vocabulary. "safer" is allowed and "safe" is not; the
# short name is never used; the endorsed name is never half-said.
SAFE_WORD = re.compile(r"\bsafe\b", re.I)
SHORT_NAME = re.compile(r"\bRR\b")
HALF_BRAND = re.compile(r"\bRadial\b(?! 47)")
PUFFERY = ("magic", "just works", "seamless")

# The amber, as the packaged stylesheet defines it in both contexts.
AMBER = ("#f8bd5c", "#f0a32d", "#a95d00")

# What a reader sees on the bootstrap screen: not the paint, not the
# script, and not a comment left for whoever reads the markup next.
STYLED = re.compile(r"<(style|script)\b.*?</\1>", re.S)
COMMENT = re.compile(r"<!--.*?-->", re.S)
TAGS = re.compile(r"<[^>]+>")

# The masthead, read as one element, so the endorsement's place inside it
# can be asserted rather than inferred from document order.
MASTHEAD = re.compile(r'<header class="masthead">(.*?)</header>', re.S)

# One landing row as the markup renders it, and the two slots each one
# must carry. The slot names are the row object's own keys.
ROW_ELEMENT = re.compile(
    r'<div\b[^>]*data-block="([^"]+)"[^>]*>(.*?)</div>', re.S)
LANDING_SLOTS = frozenset({"label", "path"})

# The two landing places the gate names. AMENDED constraint 6: an
# install surface says in plain words what it sets up, where it lands,
# and how the reader takes it back out. Where it lands is a path a
# reader can open, never a command to run.
LANDING_ROWS = ("gate_programs", "gate_support")

# The two platforms the shell builds for, exactly (PLAN-0009 phase 3).
# A sentence that cannot be true on both carries one value per platform,
# and the page picks by the platform field the shell sends on every
# picture. The set is pinned: a pair with a platform missing is a
# sentence one build renders as nothing.
PLATFORM_KEYS = frozenset({"windows", "linux"})

# Exactly the flat keys whose values are such pairs. Pinned rather than
# inferred, so a key cannot quietly become per-platform without this
# hold naming it. `fail_tool_dir` left the set when PLAN-0021 phase 2
# rewrote it without the program's file name, the one part that differed.
PER_PLATFORM = frozenset({
    "progress_extract",
    "progress_path",
    "fail_foreign_exe",
})

# The exact key set of app/src/strings.json, restated rather than read.
STRING_KEYS = frozenset({
    "_comment",
    "window_title",
    "endorsement",
    "gate_head",
    "gate_lede",
    "gate_reassure",
    "gate_press_note",
    "consent_label",
    *LANDING_ROWS,
    # The two sentences that count the run: one while it is going, one
    # where it stopped.
    "step_running",
    "step_stopped",
    # One line per step that runs, in the order the steps run.
    "progress_probe",
    "progress_download",
    "progress_verify",
    "progress_extract",
    "progress_path",
    "progress_install",
    "progress_resolve",
    "progress_version",
    "progress_start",
    # One key per named stop in D1-D6 and D11-D12.
    "fail_uv_probe",
    "fail_download",
    "fail_checksum",
    "fail_extract",
    "fail_path_write",
    "fail_package_download",
    "fail_package_checksum",
    "fail_engine_install",
    "fail_recovery",
    "fail_tool_dir",
    "fail_foreign_exe",
    "fail_engine_old",
    "fail_engine_unread",
    "fail_spawn",
    "fail_line_timeout",
    "fail_line_unparsed",
    "fail_engine_stopped",
    "fail_picker_no_target",
    "fail_debug_override",
    # The PATH report's two sentences, for the platform where that step
    # reads and writes nothing (PLAN-0009 phase 3 prescription 3).
    "path_report_on",
    "path_report_off",
    # The engine swap's progress line, and the one sentence a swap that
    # gave up adds under its stop: what still holds the engine, or that
    # nothing running from its folders was found (PLAN-0021 phase 2,
    # prescription 4).
    "swap_running",
    "swap_held",
    "swap_held_none",
    # The update dialog, its two presses, and the four sentences a
    # failed update can end on, one per cause (PLAN-0021 phase 3,
    # prescription 3, and its review). Rust says them in native dialogs,
    # with no page.
    "update_ready",
    "update_now",
    "update_later",
    "update_failed_download",
    "update_failed_signature",
    "update_failed_version",
    "update_failed_install",
    # The removal flow's own seven: one outcome line per branch of U5's
    # decision, one each for the read that failed and the write that
    # refused, and the notice that says what the removal did not take.
    # These are the only sentences Rust says with no page to render them.
    "uninstall_notice",
    "uninstall_path_removed",
    "uninstall_path_kept",
    "uninstall_path_gone",
    "uninstall_path_absent",
    "uninstall_path_error",
    "uninstall_path_write_error",
    "retry_label",
    "picker_menu_label",
})

# The exact key set of app/src/bootstrap.json, restated the same way.
# The archive pins live one row per platform (PLAN-0009 phase 3
# prescription 1): each row carries its two URL templates and its own
# digest, and the archive each row's templates must name is pinned here
# beside the row. The wheel is pinned the same way the archives are
# (PLAN-0010 phase 3 prescription 1): an address, and the digest its
# bytes must answer to before the app installs them.
CONFIG_KEYS = frozenset({
    "uv_version",
    "uv_archives",
    "package_source",
    "package_sha256",
    "python_pin",
    "engine_min_version",
})
ARCHIVE_KEYS = frozenset({"url_primary", "url_fallback", "sha256"})
ARCHIVE_NAMES = {
    "windows": "uv-x86_64-pc-windows-msvc.zip",
    "linux": "uv-x86_64-unknown-linux-gnu.tar.gz",
}

# The stops a second press cannot move: the bytes that arrived are the
# bytes that will arrive again, for the uv archive and for the engine
# package alike, and a file this app did not write stays where it is
# until someone moves it.
DETERMINISTIC = ("fail_checksum", "fail_package_checksum", "fail_foreign_exe")

# The accepted gate, word for word. It is restated here rather than read
# out of the file for the same reason the key set is: a test that took
# its expectations from the file under test would pass whatever that file
# said, and this copy is the graded content of the wireframe the owner
# accepted.
GATE_COPY = {
    "gate_head": "Install RadialRig",
    "gate_lede": (
        "This sets up RadialRig, the document manager for your agents' "
        "projects, along with the tools it runs on."),
    "gate_reassure": (
        "You can take all of it back out from this app later. RadialRig "
        "never touches your own files."),
    "gate_press_note": "Nothing runs until this press.",
    "consent_label": "Install RadialRig",
}

# The two landing places, labelled, as paths and not as commands, one
# path per platform.
LANDING_COPY = {
    "gate_programs": {"label": "Programs", "path": {
        "windows": "%USERPROFILE%\\.local\\bin",
        "linux": "~/.local/bin",
    }},
    "gate_support": {"label": "Support files", "path": {
        "windows": "%APPDATA%\\uv",
        "linux": "~/.local/share/uv",
    }},
}

# What the superseded four-block gate carried and this one may not: the
# keys themselves, and the shapes a command-level line is written in.
RETIRED_KEYS = (
    "gate_uv", "gate_python", "gate_engine", "gate_path",
    "removal_order", "gate_found_uv",
)
COMMAND_SHAPES = (
    "uv ", "del ", "python", "uninstall", "cache clean", "hkcu", ".exe", "%",
)

# The two sentences that count the run, and the run's own length said in
# each of them.
STEP_SENTENCES = {
    "step_running": "Step {detail} of 9",
    "step_stopped": "Stopped at step {detail} of 9",
}

# The steps, in the order they run, as the filler reads them.
STEP_ORDER = (
    "progress_probe", "progress_download", "progress_verify",
    "progress_extract", "progress_path", "progress_install",
    "progress_resolve", "progress_version", "progress_start",
)

# The quoted detail: what the machine said, under the sentence the shell
# says, in quotation marks rather than spliced into it.
QUOTED_DETAIL = "The error was “{detail}”."

# Which stops quote a machine's own words under their sentence, and which
# say something of their own there instead: two that carry no value at
# all, one that names a file already standing, and the two that name the
# engine's versions.
QUOTING = frozenset({
    "fail_uv_probe", "fail_download", "fail_extract", "fail_package_download",
    "fail_engine_install", "fail_recovery", "fail_tool_dir", "fail_spawn",
    "fail_line_timeout", "fail_line_unparsed", "fail_engine_stopped",
})
UNQUOTED = frozenset({
    "fail_checksum", "fail_package_checksum", "fail_path_write",
    "fail_foreign_exe", "fail_engine_old", "fail_engine_unread",
})

# The two old-engine stops (BUG-0017): one names the version the engine
# on the machine answered with, the other says it answered none. Both
# name the version this build needs, the one value beyond `{detail}` a
# sentence may carry. That value is the build's, not the stop's: it
# rides on every picture as the platform does, so a stop still carries
# one value and the shell still composes nothing.
OLD_ENGINE = frozenset({"fail_engine_old", "fail_engine_unread"})
NEEDED_SLOT = "needed"

# The update dialog names two versions (PLAN-0021 phase 3, prescription
# 3): the one on offer in `{detail}`, and this build's own in `{current}`.
# Like the version an old-engine stop needs, the second belongs to the
# build rather than to what was found, so the sentence still fills one
# found value.
UPDATE_READY = "update_ready"
CURRENT_SLOT = "current"
JS_NEEDED = 'var NEEDED = "{needed}";'
JS_NEEDED_SENT = "needed = view.engine_needed;"
RUST_NEEDED = "engine_needed: config::pins().engine_min_version.as_str(),"

# The two failure sentences this panel never draws: one the picker shows
# in the window's own chrome once the window has left this screen, and
# one the debug build stands above the gate as a notice.
OFF_PANEL = frozenset({"fail_picker_no_target", "fail_debug_override"})

# The step each in-run stop stands on, restated here the way the order
# above is restated, because a stop counted onto the wrong step reads as
# a run that reached further than it did. The wireframe grades two of
# these on its own face — the download failure at "Stopped at step 2 of
# 9" and the checksum failure at step 3 — and the rest follow the same
# rule: a stop stands on the step that asks the question it failed to
# answer. A stop with no run behind it draws no count at all; that hold
# is STOP_STEP_CHOICE above, backed by Rust keeping the reached step
# across a stop.
STOPPED_AT_STEP = {
    "fail_uv_probe": "progress_probe",
    "fail_download": "progress_download",
    "fail_checksum": "progress_verify",
    "fail_extract": "progress_extract",
    "fail_path_write": "progress_path",
    "fail_package_download": "progress_install",
    "fail_package_checksum": "progress_install",
    "fail_engine_install": "progress_install",
    "fail_recovery": "progress_install",
    "fail_foreign_exe": "progress_install",
    "fail_tool_dir": "progress_resolve",
    "fail_engine_old": "progress_version",
    "fail_engine_unread": "progress_version",
    "fail_spawn": "progress_start",
    "fail_line_timeout": "progress_start",
    "fail_line_unparsed": "progress_start",
    "fail_engine_stopped": "progress_start",
}

# Every selector in the shell stylesheet that reaches for the amber, and
# what each one is. The rule is one amber element carrying meaning per
# screen, not one selector in the file: the gate's press, the running
# step, the press forward from a stop a second press can move, and the
# stopped segment on the one screen that has no press to carry it. The
# two presses share one hover rule, the same one the engine's page gives
# its primary press. The focus ring is the accent's one appearance that
# says nothing.
AMBER_SELECTORS = frozenset({
    "#consent",
    "#retry",
    "#consent:hover, #retry:hover",
    ".steps-track .active",
    ".steps-track .stopped-final",
    ":focus-visible",
})

# The one expression that decides which mark the stopped segment takes,
# and the one that decides whether the press forward is on screen. Read
# together they are the hold: the amber segment is drawn exactly when the
# press is not, so the two can never stand on one screen.
SEGMENT_CHOICE = 'running ? "active" : (view.retry ? "stopped" : "stopped-final")'
PRESS_CHOICE = 'show(at("actions"), stopped && !!view.retry);'

# The one expression that decides whether a stop draws a step count at
# all: only when the view still carries the step its run reached. Rust
# keeps that field across a stop and clears it as each fresh pass opens,
# so a stop from the opening look at the machine draws no count and no
# track — a count there would name a step that never executed.
STOP_STEP_CHOICE = (
    'stepOf(running ? key : (view.progress ? STOPPED_AT[key] : ""))')

# The per-platform pair mechanism's three legs, each pinned by the line
# that carries it. The pairs are data in the strings file; these are the
# resolution paths that turn a pair into the platform's own sentence,
# and a leg that quietly reverted would render four sentences and both
# landing paths as nothing on both platforms while every other hold
# stayed green.
PICKED_LINE = "var text = words ? pick(words[key]) : null;"
PICKED_SLOT = 'var line = pick(value[slots[s].getAttribute("data-slot")]);'
REPORT_CHOICE = "show(report, !!view.path_report);"

# The platform values the shell sends on every picture, spelled as the
# Rust writes them, and the pair keys the two validating readers pin.
# All three spell the same two names or a pair renders as nothing.
RUST_PLATFORM = (
    'pub const PLATFORM: &str = if cfg!(windows) { "windows" } else { "linux" };')
RUST_PLATFORM_KEYS = 'const PLATFORM_KEYS: [&str; 2] = ["windows", "linux"];'

# The modules D13 names, beside the builder that was already there, the
# one PLAN-0021 phase 2 adds to find and stop what holds the engine, and
# the one phase 3 adds to ask for an update and install it.
RUST_MODULES = frozenset(
    {"main", "strings", "config", "bootstrap", "attach", "picker", "removal",
     "holders", "update"})

# The one debug-only switch, and the gate that keeps it out of a release.
OVERRIDE_ENV = "RADIALRIG_PACKAGE_SOURCE"
DEBUG_GATE = "#[cfg(debug_assertions)]"

# The hidden flag the uninstaller's hook runs the app's own binary with,
# and the second flag that suppresses the box on a silent removal.
UNINSTALL_FLAG = "--uninstall-path-entry"
QUIET_FLAG = "--quiet"

# The installer surface: the file the exe is installed as, the exact set
# of NSIS keys U1 names, and the hook file the bundler includes.
MAIN_BINARY = "RadialRig"
NSIS_KEYS = frozenset({
    "installMode",
    "installerIcon",
    "uninstallerIcon",
    "languages",
    "displayLanguageSelector",
    "compression",
    "installerHooks",
})
HOOKS = TAURI / "installer" / "hooks.nsh"
HOOK_MACROS = frozenset({"NSIS_HOOK_PREUNINSTALL", "NSIS_HOOK_POSTUNINSTALL"})

# PLAN-0011 phase 1: the signing wiring, which sits under bundle.windows
# beside the nsis object rather than inside it. The bundler splits this
# one string on spaces and turns the relative script path into an
# absolute one, which is what lets the uninstaller hook call it from a
# directory of its own. So no part of the command may carry a space.
SIGN_SCRIPT = "installer/sign.ps1"
SIGN_METADATA = "signing.json"
SIGN_COMMAND = (
    f"powershell -NoProfile -ExecutionPolicy Bypass -File {SIGN_SCRIPT} %1")

# The three fields SignTool's /dmdf file holds, with the values the
# 2026-09-03 probe signed through. An endpoint, an account and a
# certificate profile: who may use that profile is decided by the Azure
# login on the build machine, so none of these three is a credential.
SIGNING_FIELDS = {
    "Endpoint": "https://eus.codesigning.azure.net",
    "CodeSigningAccountName": "Patrick-Artifact",
    "CertificateProfileName": "pwcodesigning",
}

# What neither signing file may carry. A secret in either one would ship
# in every checkout, and the whole point of the SignTool route is that
# there is no secret to ship.
SIGNING_BANNED = ("secret", "token", "password", "azure_client")

# The guard both hook bodies sit inside, spelled as the template spells
# it, and the two instructions an uninstaller says anything with.
UPDATE_GUARD = "${If} $UpdateMode <> 1"
GUARD_END = "${EndIf}"
SPEAKING = ("MessageBox", "DetailPrint")

# The template's own running-app check, pulled ahead of our ExecWait, and
# the two key defines the template builds from the publisher and the
# product name. Both are the template's spellings, restated here so a
# rename in the hook file has to be a rename here too.
RUNNING_CHECK = (
    '!insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"')
REGISTRY_DEFINES = (
    'DeleteRegKey HKCU "${MANUPRODUCTKEY}"',
    'DeleteRegKey /ifempty HKCU "${MANUKEY}"',
)

# D14: the name is set once, in the config Tauri reads before the window
# is drawn. A runtime retitle would flash the wrong name first.
RETITLE = "set_title"

# What a shell string may not name: a client, a skill, or the outcome of
# a verb the engine owns. The shell installs and starts; everything the
# engine does to a project is said on the engine's own pages.
BANNED_NAMES = (
    "claude", "cursor", "vscode", "windsurf",
    "skill", "skills",
    "mount", "unmount", "connect", "unconnect",
)


# --- reading the shell -------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _the_tree() -> None:
    """The repository, and the shell tree inside it, or a hard failure.

    Both are asserted rather than skipped past: these tests never ship,
    so a run without an `app/` tree is a run against a repository that
    lost one, which is exactly the thing worth failing on.
    """
    assert (ROOT / "pyproject.toml").is_file(), f"{ROOT} is not the repository"
    assert APP.is_dir(), "the app tree is gone"
    assert RUST_SRC.is_dir(), "the shell's Rust tree is gone"


def read(path: Path) -> str:
    """One shell file's text, with the line endings normalised."""
    assert path.is_file(), f"the shell is missing {path}"
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture(scope="module")
def strings() -> dict:
    return json.loads(read(SHELL_SRC / "strings.json"))


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(read(SHELL_SRC / "bootstrap.json"))


@pytest.fixture(scope="module")
def markup() -> str:
    return read(SHELL_SRC / "index.html")


def walk(value, where: str = "") -> list[tuple[str, str]]:
    """Every string the file carries, named by where it sits.

    One walker for all of the sweeps, because the gate blocks are objects
    and a sentence inside one reaches a reader exactly as a flat value
    does. A sweep that only read the flat keys would leave four blocks
    unswept.
    """
    if isinstance(value, str):
        return [(where, value)]
    if isinstance(value, dict):
        return [
            item
            for key, inner in value.items()
            for item in walk(inner, f"{where}.{key}" if where else str(key))
        ]
    if isinstance(value, (list, tuple)):
        return [
            item
            for index, inner in enumerate(value)
            for item in walk(inner, f"{where}[{index}]")
        ]
    return []


def forbidden_hit(text: str) -> str | None:
    """The first of D-33's three words the text carries, anywhere in it.

    Containment, not a word boundary: on the shell's surface there is no
    word that legitimately carries these letters, so a boundary reading
    would only leave room for a compound to smuggle one through.
    """
    lowered = text.lower()
    for word in FORBIDDEN:
        if word in lowered:
            return word
    return None


def reads_as(page: str) -> str:
    """Everything on the bootstrap screen a reader sees.

    The markup ships with empty slots — the sentences arrive from
    strings.json at runtime — so what is left here is the masthead. That
    is the point of the sweep: the words that are baked into the markup
    are the words this file can check.
    """
    stripped = TAGS.sub(" ", COMMENT.sub(" ", STYLED.sub(" ", page)))
    return " ".join(html.unescape(stripped).split())


def rust_files() -> list[Path]:
    return sorted(RUST_SRC.rglob("*.rs"))


def script_files() -> list[Path]:
    """Every script the shell ships beside its markup.

    The markup is asserted to carry no code of its own, so every script
    this shell runs has to be a file here, and it arrives already swept.
    """
    return sorted(SHELL_SRC.rglob("*.js"))


def rust_literals(text: str) -> list[str]:
    """Every string literal in one Rust file, comments left out.

    Comments are not user-facing, and a Rust comment legitimately says
    "safe" about code that is unsafe to write any other way. What the
    sweeps care about is what could reach a window.

    Character literals are stepped over rather than read, which matters
    for exactly one of them: `'"'` would otherwise open a string that
    swallows the rest of the file. A lifetime such as `&'a str` is not a
    character literal and is left alone.
    """
    assert 'r#"' not in text, "a raw string literal needs a better reader"
    out: list[str] = []
    index, end, depth = 0, len(text), 0
    while index < end:
        if depth:
            if text.startswith("/*", index):
                depth += 1
                index += 2
            elif text.startswith("*/", index):
                depth -= 1
                index += 2
            else:
                index += 1
            continue
        if text.startswith("/*", index):
            depth, index = 1, index + 2
            continue
        if text.startswith("//", index):
            line_end = text.find("\n", index)
            index = end if line_end < 0 else line_end + 1
            continue
        if text[index] == "'":
            char = re.compile(r"'(?:\\.|[^\\'])'").match(text, index)
            index = char.end() if char else index + 1
            continue
        if text[index] == '"':
            index += 1
            piece: list[str] = []
            while index < end and text[index] != '"':
                if text[index] == "\\":
                    piece.append(text[index:index + 2])
                    index += 2
                    continue
                piece.append(text[index])
                index += 1
            index += 1
            out.append("".join(piece))
            continue
        index += 1
    return out


def js_literals(text: str) -> list[str]:
    """Every string literal in one script file, comments left out.

    The three quoting forms JavaScript offers, read the same way the Rust
    reader reads its one form: a comment is not user-facing, and a
    literal is the thing that can reach a window.
    """
    out: list[str] = []
    index, end = 0, len(text)
    while index < end:
        if text.startswith("//", index):
            line_end = text.find("\n", index)
            index = end if line_end < 0 else line_end + 1
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = end if close < 0 else close + 2
            continue
        if text[index] in "\"'`":
            quote = text[index]
            index += 1
            piece: list[str] = []
            while index < end and text[index] != quote:
                if text[index] == "\\":
                    piece.append(text[index:index + 2])
                    index += 2
                    continue
                piece.append(text[index])
                index += 1
            index += 1
            out.append("".join(piece))
            continue
        index += 1
    return out


def sweep(checked: list[tuple[str, str]]) -> None:
    """The word sweep and the brand sweep, over one list of sentences."""
    for where, text in checked:
        hit = forbidden_hit(text)
        assert hit is None, f"{where} says '{hit}'"
        found = SAFE_WORD.search(text)
        assert found is None, f"{where} says '{found.group(0) if found else ''}'"
        assert not SHORT_NAME.search(text), f"{where} uses the short name"
        found = HALF_BRAND.search(text)
        assert found is None, f"{where} says 'Radial' without '47'"
        lowered = text.lower()
        for banned in PUFFERY:
            assert banned not in lowered, f"{where} says '{banned}'"


# --- item 1: the keys --------------------------------------------------

def test_the_strings_file_carries_exactly_the_keys_the_shell_reads(strings):
    """The set, restated here, because three readers share this file.

    Rust reads it, the bootstrap page reads it, and this test reads it. A
    key that arrives without all three knowing is a sentence one of them
    renders as nothing, so the set is pinned rather than described.
    """
    assert frozenset(strings) == STRING_KEYS, (
        frozenset(strings) ^ STRING_KEYS)
    for key, value in strings.items():
        if key in LANDING_ROWS:
            assert isinstance(value, dict), key
            continue
        if key in PER_PLATFORM:
            assert isinstance(value, dict), key
            assert frozenset(value) == PLATFORM_KEYS, key
            for said in value.values():
                assert isinstance(said, str) and said.strip(), key
            continue
        assert isinstance(value, str) and value.strip(), key

    # Every sentence names at most one slot, and the only slot is the one
    # the shell fills: an exit code, a path, a tail of stderr, or the
    # number of the step the run reached.
    #
    # One slot *name*, not one appearance of it. D5's point is that the
    # shell does not compose sentences: the sentence still says the whole
    # of itself, and the filler still fills one value from the stop. The
    # two old-engine sentences alone also name the version this build
    # needs, which comes from the picture rather than the stop.
    for where, text in walk(strings):
        assert text.count("{") == text.count("}"), where
        named = frozenset(re.findall(r"\{([^{}]*)\}", text))
        if where in OLD_ENGINE:
            assert NEEDED_SLOT in named, where
            assert named <= {"detail", NEEDED_SLOT}, where
            continue
        if where == UPDATE_READY:
            assert named == {"detail", CURRENT_SLOT}, where
            continue
        assert len(named) <= 1, where
        assert not named or named == {"detail"}, where

    # The two stops a press cannot move say what does move them instead
    # of offering the control every other failure pairs with — on every
    # platform the sentence is written for.
    for key in DETERMINISTIC:
        for where, text in walk(strings[key], key):
            assert "retry" not in text.lower(), where
            assert "changes this" in text, where
    assert strings["retry_label"].strip()


# --- item 2: who typed a change ----------------------------------------

def test_no_shell_string_says_who_typed_a_change(strings):
    """D-33, over every sentence the shell can say.

    The engine's pages carry the substring inside one skill's name. The
    shell names no skill, so there is nothing here for the whole-word
    reading to spare, and the sweep is the strict one.
    """
    checked = walk(strings)
    assert checked, "the strings file carries no sentences"
    for where, text in checked:
        hit = forbidden_hit(text)
        assert hit is None, f"{where} says '{hit}'"


# --- item 3: the brand -------------------------------------------------

def test_the_shell_words_hold(strings):
    """The brand's vocabulary, over the file the whole screen is built of."""
    checked = walk(strings)
    assert checked, "the strings file carries no sentences"
    sweep(checked)

    # The ban is on the claim, not on the comparative.
    assert SAFE_WORD.search("a safer file") is None


# --- item 4: the endorsement -------------------------------------------

def test_the_endorsement_is_said_once_on_the_shell_surface(strings, markup):
    """Once on the screen, in the masthead, beside the name.

    The literal is the engine's own constant. The markup carries none of
    it: the endorsement is a slot like every other sentence, filled from
    the strings file at runtime, so the file is the only place it lives.
    Where the slot sits is the part the markup does own, and R3 reads the
    first-mention rule the way the engine's own masthead passes it —
    name and endorsement inside one header element.
    """
    said = read(SHELL_SRC / "strings.json")
    assert said.count(setup_web.ENDORSEMENT) == 1
    assert [
        where for where, text in walk(strings)
        if setup_web.ENDORSEMENT in text
    ] == ["endorsement"]
    assert markup.count(setup_web.ENDORSEMENT) == 0

    # The first mention rule the engine's own surfaces pass: the name and
    # the endorsement stand together on the screen a reader opens.
    surface = reads_as(markup)
    assert surface, "the bootstrap screen shows nothing at all"
    assert "RadialRig" in surface
    assert "Radial 47" in strings["endorsement"]

    head = MASTHEAD.search(markup)
    assert head is not None, "the masthead is gone"
    assert 'data-key="endorsement"' in head.group(1)
    # The owner's ruling on the name this surface wears: the window is
    # RadialRig, and the screen in front of the reader is its installer.
    assert "<h1>RadialRig Installer</h1>" in head.group(1)
    assert markup.count('data-key="endorsement"') == 1


# --- item 5: the gate ---------------------------------------------------

def test_the_gate_says_what_it_sets_up_where_it_lands_and_that_it_comes_out(
    strings,
):
    """The four things the accepted gate stands on, word for word.

    AMENDED constraint 6: an install surface says in plain words what it
    sets up, where it lands, and how the reader takes it back out.
    Command-level removal detail belongs to the owner manual, never to
    the install screen. The superseded gate named four blocks and put a
    copyable removal command under each; this one names two places and
    one promise, and the commands are gone from the file entirely.

    The engine's own removal command is imported rather than restated,
    because the thing being asserted is its absence here: the manage page
    is where it is said, and this screen is not the manage page.
    """
    for key, said in GATE_COPY.items():
        assert strings[key] == said, key
    for key, row in LANDING_COPY.items():
        assert strings[key] == row, key

    # The four blocks, the removal order and the carried-uv line are gone
    # from the file, not merely unrendered: a key left behind is a key an
    # edit puts back on screen.
    for gone in RETIRED_KEYS:
        assert gone not in strings, gone
    said = read(SHELL_SRC / "strings.json")
    assert manage_web.ENGINE_REMOVAL not in said
    assert "uv tool uninstall" not in said
    assert "uv python uninstall" not in said

    # The gate's own prose carries no command, no uv or Python detail, and
    # no registry key. It is one screen of plain words.
    prose = " ".join(GATE_COPY[key] for key in GATE_COPY).lower()
    for shape in COMMAND_SHAPES:
        assert shape not in prose, shape

    # Both landing places are paths, one per platform: a root the reader
    # already has a name for, a separator, and no verb in front of
    # either. On Windows the root is an environment reference; on Linux
    # it is the home shorthand every shell prints.
    for key, row in LANDING_COPY.items():
        assert frozenset(row) == LANDING_SLOTS, key
        paths = row["path"]
        assert frozenset(paths) == PLATFORM_KEYS, key
        windows = paths["windows"]
        assert windows.startswith("%") and windows.count("%") == 2, key
        assert "\\" in windows and " " not in windows, key
        linux = paths["linux"]
        assert linux.startswith("~/") and " " not in linux, key
        assert "\\" not in linux, key
        for path in paths.values():
            assert "{" not in path, key
        assert row["label"].strip() == row["label"], key


def test_the_gate_markup_carries_slots_and_no_command_frame(markup):
    """The frame the copyable command was shown in is gone with it.

    The superseded gate rendered each removal into a `pre`, which is what
    a reader reads as something to copy and run. Nothing on this screen
    is that any more: the two paths are named inline, and the element
    that framed a command is not in the document at all.
    """
    assert "<pre" not in markup, "the gate frames something to copy"
    assert "block-removes" not in markup
    assert "blocks" not in markup, "the block list is still rendered"
    for gone in RETIRED_KEYS:
        assert gone not in markup, gone


def test_the_filler_writes_the_rows_and_draws_no_unfillable_line(markup):
    """One filler for the words, and a line it cannot fill is not drawn.

    Both landing rows go through it, so a label and the path it labels
    reach the reader together or not at all. And a line whose slot has
    nothing to put in it comes back empty rather than filled with
    nothing: a quoted detail filled with nothing reads as an error that
    said something and did not, which is worse than a line that is not
    there.
    """
    script = read(SHELL_SRC / "bootstrap.js")

    drawn = re.search(r"\n  function paint\(.*?\n  \}\n", script, re.S)
    assert drawn is not None, "the filler is gone"
    assert 'querySelectorAll("[data-key]")' in drawn.group(0)
    assert 'querySelectorAll("[data-block]")' in drawn.group(0)
    assert 'querySelectorAll("[data-slot]")' in drawn.group(0)
    assert 'querySelectorAll("[data-slot]")' not in script.replace(
        drawn.group(0), "", 1), "a second filler for the same rows"

    filler = re.search(r"\n  function tidy\(.*?\n  \}\n", script, re.S)
    assert filler is not None, "the line filler is gone"
    assert "!detail" in filler.group(0), (
        "the filler fills a slot it has nothing for")
    assert 'return "";' in filler.group(0), (
        "an unfillable line still reaches the screen")

    # The two halves of a failure sentence are split where the file
    # splits them, not composed here.
    halves = re.search(r"\n  function lines\(.*?\n  \}\n", script, re.S)
    assert halves is not None, "the two-line reader is gone"
    assert 'split("\\n")' in halves.group(0)

    # And the markup keeps the empty slots the filler writes into.
    assert markup.count('data-slot="label"') == len(LANDING_COPY)
    assert markup.count('data-slot="path"') == len(LANDING_COPY)


def test_the_platform_pairs_resolve_and_the_report_is_rendered(markup):
    """The pair mechanism's legs, pinned, so none can revert silently.

    The pairs themselves are pinned as data by PER_PLATFORM and
    LANDING_COPY above. What no data pin can hold is the resolution:
    the page has to pick a platform's half wherever a sentence or a
    landing path is read, the platform it picks by has to be the value
    the shell actually sends, and the report the Linux step produces
    has to have an element that draws it. Each leg reverted alone
    leaves every other hold green while sentences render blank, which
    is the drift this file's charter exists to stop.
    """
    script = read(SHELL_SRC / "bootstrap.js")
    assert PICKED_LINE in script, "a sentence is read without pick()"
    assert PICKED_SLOT in script, "a landing slot is read without pick()"
    assert REPORT_CHOICE in script, "the PATH report is never drawn"
    assert 'id="path-report"' in markup, "the report has no element"
    assert markup.count('id="path-report"') == 1

    # The three spellings of the two platform names are held level: the
    # value Rust sends on the view, the pair keys Rust's own validator
    # proves, and the pair keys this file pins over the strings file.
    rust = read(RUST_SRC / "bootstrap.rs")
    assert RUST_PLATFORM in rust, "the view's platform constant moved"
    validating = read(RUST_SRC / "strings.rs")
    assert RUST_PLATFORM_KEYS in validating, "the Rust pair keys moved"
    for name in sorted(PLATFORM_KEYS):
        assert f'"{name}"' in RUST_PLATFORM, name
        assert f'"{name}"' in RUST_PLATFORM_KEYS, name


def test_the_old_engine_stop_names_both_versions(strings):
    """BUG-0017: which engine this machine has, and which one this app needs.

    The stop used to name neither version. The opening look no longer
    reaches it (PLAN-0021 phase 2 replaces an old engine with no press);
    the version check after an install still can, and its press runs the
    engine swap. The installed one is the stop's own value, so
    it sits only in the sentence for an engine that answered with one.
    The needed one is the build's, sent on every picture, and every leg
    that carries it is pinned: the strings, the filler, and the view. No
    value sits in the head, which is drawn whatever the values are, and
    the press the sentence tells the reader to use is the one the panel
    draws under it.
    """
    for key, slots in (
        ("fail_engine_old", {"detail", NEEDED_SLOT}),
        ("fail_engine_unread", {NEEDED_SLOT}),
    ):
        head, _, under = strings[key].partition("\n")
        assert "{" not in head, key
        assert frozenset(re.findall(r"\{([^{}]*)\}", under)) == slots, key
        assert strings["retry_label"] in under, key

    script = read(SHELL_SRC / "bootstrap.js")
    assert JS_NEEDED in script, "the filler never fills the needed version"
    assert JS_NEEDED_SENT in script, "the filler never reads it off the view"
    rust = read(RUST_SRC / "bootstrap.rs")
    assert RUST_NEEDED in rust, "the view no longer sends the engine floor"


def test_the_panel_counts_the_steps_and_quotes_what_the_machine_said(
    strings, markup,
):
    """Step 2 of 9, a track that draws it, and the detail in quotes.

    The count is said in one sentence per state and drawn in one segment
    per step, and the length of the run is pinned to the number of steps
    the strings file names. A stop says one sentence and, under it, what
    the machine said — quoted, never spliced into the sentence, so a
    reader can tell the app's words from the error's.
    """
    steps = sorted(key for key in strings if key.startswith("progress_"))
    assert len(steps) == len(STEP_ORDER)
    assert frozenset(steps) == frozenset(STEP_ORDER)

    for key, said in STEP_SENTENCES.items():
        assert strings[key] == said, key
        assert said.count("{detail}") == 1, key
        assert f"of {len(steps)}" in said, key

    # One segment per step, in the markup, so the track cannot be built
    # to a different length than the sentence beside it says.
    track = re.search(r'<div class="steps-track"[^>]*>(.*?)</div>', markup, re.S)
    assert track is not None, "the segment track is gone"
    assert track.group(1).count("<span></span>") == len(steps)
    assert track.group(1).replace("<span></span>", "").strip() == ""

    script = read(SHELL_SRC / "bootstrap.js")
    listed = re.search(r"var STEPS = \[(.*?)\];", script, re.S)
    assert listed is not None, "the step order is gone from the filler"
    assert tuple(re.findall(r'"([^"]+)"', listed.group(1))) == STEP_ORDER

    # Every stop the panel draws belongs to a step, and the step it
    # belongs to is the one this file names for it. Naming only "a step of
    # the nine" would let two stops trade places and still pass, and the
    # number on the screen would then count a step that never ran.
    stopped = re.search(r"var STOPPED_AT = \{(.*?)\};", script, re.S)
    assert stopped is not None, "no stop knows which step it belongs to"
    placed = dict(re.findall(r'(\w+): "([^"]+)"', stopped.group(1)))
    panelled = frozenset(
        key for key in strings if key.startswith("fail_")) - OFF_PANEL
    assert panelled == QUOTING | UNQUOTED, panelled ^ (QUOTING | UNQUOTED)
    assert frozenset(placed) == panelled, frozenset(placed) ^ panelled
    assert frozenset(STOPPED_AT_STEP) == panelled, (
        frozenset(STOPPED_AT_STEP) ^ panelled)
    for key, step in STOPPED_AT_STEP.items():
        assert step in STEP_ORDER, key
        assert placed[key] == step, (
            f"{key} stands on {placed[key]}, not {step}")

    # The two the wireframe grades on its own face, counted the way it
    # counts them: step 2 for the download, step 3 for the checksum.
    assert STEP_ORDER.index(STOPPED_AT_STEP["fail_download"]) + 1 == 2
    assert STEP_ORDER.index(STOPPED_AT_STEP["fail_checksum"]) + 1 == 3

    # Two lines per stop the panel draws — on every platform the stop is
    # written for: the sentence, and what more there is to say under it.
    for key in panelled:
        for where, text in walk(strings[key], key):
            head, sep, under = text.partition("\n")
            assert sep, where
            assert head.strip() and under.strip(), where
            assert "\n" not in under, where

    # The eleven that quote a machine's own words quote them the one way.
    for key in QUOTING:
        for where, text in walk(strings[key], key):
            head, _, under = text.partition("\n")
            assert "{detail}" not in head, where
            assert under == QUOTED_DETAIL, where

    # The two the panel never draws stay one line, because Rust renders
    # one of them whole and the other stands above the gate as a notice.
    for key in OFF_PANEL:
        assert "\n" not in strings[key], key


# --- item 6: what a shell string may not name --------------------------

def test_no_shell_string_names_a_client_a_skill_or_a_verb(strings, config):
    """The shell installs and starts. It says nothing about a project.

    Every outcome a verb produces is said on the engine's own pages,
    which the engine serves and the engine's own tests sweep. A client's
    name here would be a second client's worth of edits waiting to
    happen, and a verb's name would be a promise this screen cannot keep.
    """
    checked = walk(strings) + walk(config)
    assert checked, "there is nothing to sweep"
    for where, text in checked:
        lowered = text.lower()
        for name in BANNED_NAMES:
            hit = re.search(rf"\b{re.escape(name)}\b", lowered)
            assert hit is None, f"{where} names '{name}'"


# --- item 7: the sweeps reach everything a reader can be shown ---------

def test_the_sweeps_reach_the_markup_the_config_the_rust_and_the_scripts(
    config, markup,
):
    """The same two sweeps, over the other four surfaces.

    The markup because a sentence baked into it never passes through the
    strings file; the config because its values are shown on the gate and
    in failure sentences; the Rust and any script because a literal there
    can reach a window the same way a sentence does.
    """
    surface = reads_as(markup)
    assert surface, "the bootstrap screen shows nothing at all"

    files = rust_files()
    assert frozenset(path.stem for path in files) == RUST_MODULES, [
        path.name for path in files]

    checked = [("the bootstrap screen", surface)] + walk(config)

    rust_said: list[str] = []
    for path in files:
        said = rust_literals(read(path))
        rust_said += said
        checked += [(f"{path.name} literal", text) for text in said]
    # The reader is only a hold if it reads something: a scanner that
    # silently returns nothing would pass every sweep below it.
    assert rust_said, "no Rust literal was read at all"

    script_said: list[str] = []
    for path in script_files():
        said = js_literals(read(path))
        script_said += said
        checked += [(f"{path.name} literal", text) for text in said]
    # The same hold the Rust reader gets: a scanner that read nothing
    # would wave every sentence in the file through.
    assert script_said, "no script literal was read at all"

    sweep(checked)

    # The markup keeps slots, not sentences: every slot it names is a key
    # the strings file actually carries.
    keys = frozenset(json.loads(read(SHELL_SRC / "strings.json")))
    named = re.findall(r'data-key="([^"]+)"', markup)
    assert named, "the markup carries no slots to fill"
    for key in named:
        assert key in keys, key


# --- item 7b: the markup and the landing rows answer to each other -----

def test_the_markup_rows_and_the_landing_places_line_up(markup):
    """Two ways, not one: no place goes unrendered, none is invented.

    The one-way check — every rendered row names a real key — would pass
    a markup that quietly dropped the support-files row, which is the
    place a reader is least likely to expect and most needs named.
    """
    rendered = ROW_ELEMENT.findall(markup)
    assert frozenset(key for key, _ in rendered) == frozenset(LANDING_ROWS), [
        key for key, _ in rendered]
    assert len(rendered) == len(LANDING_ROWS), "a row is rendered twice"

    for key, inner in rendered:
        slots = re.findall(r'data-slot="([^"]+)"', inner)
        assert frozenset(slots) == LANDING_SLOTS, (key, slots)
        assert len(slots) == len(LANDING_SLOTS), (key, slots)

    # Nowhere else names a slot, so a label and the path it labels cannot
    # be scattered outside the row a reader reads them in.
    assert re.findall(r'data-slot="([^"]+)"', markup) == [
        slot for _, inner in rendered
        for slot in re.findall(r'data-slot="([^"]+)"', inner)
    ]


# --- item 7c: the screen is built of the files the shell ships ---------

def test_the_bootstrap_screen_carries_no_paint_and_no_code_of_its_own(
    markup,
):
    """The markup is markup. The paint is styles.css; the code is a file.

    An inline block is a surface no sweep in this file reaches by name
    and no reviewer reads twice. Keeping both outside the document is
    what makes the sweeps above complete rather than nearly complete,
    and it is what the shell's own CSP ('self', no 'unsafe-inline')
    would enforce at runtime anyway.

    A reference to a file is a different thing from a block: the file it
    names is on this shell's own tree, item 7 sweeps its literals by name,
    and the document still carries no code. So the ban is on the block —
    a script element with anything at all between its tags, an element
    that paints or acts on its own, a `javascript:` address — and a bare
    reference to a file that is really there is allowed through.
    """
    assert re.search(r"<style\b", markup) is None, "the markup paints itself"
    assert re.search(r"<script\b[^>]*/>", markup) is None, "a self-closing script"
    referenced = re.findall(r"<script\b([^>]*)>(.*?)</script>", markup, re.S)
    assert len(referenced) == len(re.findall(r"<script\b", markup)), (
        "a script element the reader below cannot read")
    for attributes, inside in referenced:
        assert inside.strip() == "", "the markup carries code"
        source = re.search(r'src="([^"]+)"', attributes)
        assert source is not None, "a script element with no file to load"
        named = SHELL_SRC / source.group(1)
        assert named.is_file(), source.group(1)
        assert named in script_files(), source.group(1)
    assert "style=" not in markup, "an element paints itself"
    assert "javascript:" not in markup.lower()
    for handler in re.findall(r'\son([a-z]+)\s*=', markup):
        raise AssertionError(f"the markup acts on its own, at on{handler}")


# --- item 8: the pins --------------------------------------------------

def test_the_bootstrap_config_is_pinned_and_the_override_is_debug_only(config):
    """A version-pinned archive, a checksum, and a source with an address.

    A floating latest cannot be checksum-verified, a bare package name
    would reach a public index D-39 forbids, and a plain-http hop would
    put the bytes this app runs on the wire for anyone to answer.
    """
    assert frozenset(config) == CONFIG_KEYS, frozenset(config) ^ CONFIG_KEYS
    for key, value in config.items():
        if key == "uv_archives":
            continue
        assert isinstance(value, str) and value.strip(), key

    assert re.fullmatch(r"\d+\.\d+\.\d+", config["uv_version"])

    # One archive row per platform, each proved against the archive its
    # row is for, and each carrying its own digest — the two digests are
    # digests of two different files and must differ.
    archives = config["uv_archives"]
    assert frozenset(archives) == frozenset(ARCHIVE_NAMES), (
        frozenset(archives) ^ frozenset(ARCHIVE_NAMES))
    for platform, names in ARCHIVE_NAMES.items():
        row = archives[platform]
        assert frozenset(row) == ARCHIVE_KEYS, platform
        for key in ("url_primary", "url_fallback"):
            url = row[key]
            assert url.startswith("https://"), (platform, key)
            assert "<ver>" in url, (platform, key)
            assert url.endswith(names), (platform, key)
        assert re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), platform
    assert archives["windows"]["sha256"] != archives["linux"]["sha256"]

    # The package source: a URL, or the PEP 508 form that carries one.
    # Either way the address is checked, and a bare name is not a source.
    source = config["package_source"]
    assert re.fullmatch(r"[A-Za-z0-9._-]+", source) is None, "a bare name"
    address = source.split(" @ ", 1)[1] if " @ " in source else source
    assert address.startswith("https://"), address
    assert "://" not in address[len("https://"):], address

    # The wheel that address names is pinned by digest too, in the one
    # shape the archives are pinned in, and it is a third file: no
    # archive digest may stand in for it.
    wheel = config["package_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", wheel), wheel
    assert wheel != archives["windows"]["sha256"]
    assert wheel != archives["linux"]["sha256"]

    assert re.fullmatch(r"\d+\.\d+", config["python_pin"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", config["engine_min_version"])

    # BUG-0015: the engine floor is the wheel's own version. Below the
    # floor the shell replaces the engine (PLAN-0021 phase 2); at 0.1.0
    # for eleven releases, nothing was ever below it and an installed
    # engine never moved. A cut that moves
    # the wheel and forgets the floor fails here, before it ships.
    wheel_version = re.search(
        r"radialrig-(\d+\.\d+\.\d+)-py3-none-any\.whl", source)
    assert wheel_version is not None, source
    assert config["engine_min_version"] == wheel_version.group(1), (
        config["engine_min_version"], wheel_version.group(1))

    # The override exists in one place, and that place is compiled out of
    # a release build. Two copies would be two things to gate.
    found = [
        (path, text) for path in rust_files()
        if OVERRIDE_ENV in (text := read(path))
    ]
    assert len(found) == 1, [path.name for path, _ in found]
    path, text = found[0]
    assert text.count(OVERRIDE_ENV) == 1, path.name

    lines = text.split("\n")
    at = next(i for i, line in enumerate(lines) if OVERRIDE_ENV in line)
    before = [
        line.strip() for line in lines[:at]
        if line.strip() and not line.strip().startswith("//")
    ]
    assert before and before[-1] == DEBUG_GATE, before[-3:]


# --- item 9: the colors ------------------------------------------------

# Every syntax CSS offers for writing a color down. A hold that only knew
# hex and rgba() would wave an invented hue through the moment it was
# spelled oklch(), which is the same invention in newer letters.
COLOR_FUNCTIONS = (
    "color-mix", "rgba", "rgb", "hsla", "hsl", "hwb",
    "oklab", "oklch", "lab", "lch", "color",
)
COLOR_LITERAL = re.compile(
    r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b"
    r"|(?<![-\w])(?:" + "|".join(COLOR_FUNCTIONS) + r")\("
    r"[^()]*(?:\([^()]*\)[^()]*)*\)",
    re.I,
)

# A reference to a token this stylesheet defines, which is how every
# color in a rule is meant to arrive.
VAR_REF = re.compile(r"var\(\s*--[a-z0-9-]+\s*\)", re.I)
AMBER_REF = re.compile(r"var\(\s*--accent(?:-strong)?\s*\)", re.I)

# A length, a count, or a fraction — the parts of a shorthand that are
# not the color.
MEASURE = re.compile(
    r"-?\d*\.?\d+(?:px|rem|em|ex|ch|%|s|ms|deg|fr|vh|vw|pt)?", re.I)

# The only bare words a color-bearing declaration may use. None of them
# is a named color, which is the point: a named color has to fail.
ALLOWED_WORDS = frozenset({"solid", "none", "transparent", "inherit", "block"})

# Which declarations can carry a color at all. Every custom property is
# in, because a token is where the colors of this file actually live.
COLOR_PROPERTIES = frozenset({
    "background", "background-image", "border", "border-top",
    "border-right", "border-bottom", "border-left", "outline",
    "box-shadow", "text-shadow", "fill", "stroke", "column-rule",
})


def _rules(css: str) -> list[tuple[str, str]]:
    """Each innermost rule of a stylesheet: its selector and its body."""
    bare = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    return [
        (" ".join(match.group(1).split()), match.group(2))
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", bare, re.S)
    ]


def _declarations(body: str) -> list[tuple[str, str]]:
    """Each declaration of one rule body: its property and its value."""
    out = []
    for piece in body.split(";"):
        prop, sep, value = piece.partition(":")
        if not sep:
            continue
        prop, value = prop.strip(), value.strip()
        if prop and value:
            out.append((prop, value))
    return out


def _carries_color(prop: str) -> bool:
    lowered = prop.lower()
    return (
        lowered.startswith("--")
        or lowered == "color"
        or lowered.endswith("-color")
        or lowered in COLOR_PROPERTIES
    )


def _value_words(value: str) -> list[str]:
    """One declaration's value, split into its parts.

    Splitting happens outside brackets only, so `rgba(17, 19, 24, 0.06)`
    stays one word and can be matched against the packaged sheet exactly
    as it was written there.
    """
    out: list[str] = []
    piece: list[str] = []
    depth = 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0 and (char.isspace() or char == ","):
            if piece:
                out.append("".join(piece))
                piece = []
            continue
        piece.append(char)
    if piece:
        out.append("".join(piece))
    return out


def _invented(css: str, packaged: str) -> list[str]:
    """Every color in this stylesheet that the packaged one does not carry.

    Two passes, because two different things can go wrong. A written-out
    color — in any of the syntaxes CSS offers — is checked byte for byte
    against the packaged sheet. Then every word of every declaration that
    could carry a color is checked against a short list of what is
    allowed there at all, which is what catches a bare `red`: it is not a
    token, not a measure, not a literal, and not one of the words a
    color-bearing declaration is permitted to use.
    """
    bare = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    out = [
        literal for literal in COLOR_LITERAL.findall(bare)
        if literal not in packaged
    ]
    for selector, body in _rules(css):
        for prop, value in _declarations(body):
            if not _carries_color(prop):
                continue
            for word in _value_words(value):
                if VAR_REF.fullmatch(word):
                    continue
                if COLOR_LITERAL.fullmatch(word):
                    if word not in packaged:
                        out.append(f"{selector} {{ {prop}: {word} }}")
                    continue
                if MEASURE.fullmatch(word):
                    continue
                if word.lower() in ALLOWED_WORDS:
                    continue
                out.append(f"{selector} {{ {prop}: {word} }}")
    return out


def _amber_selectors(css: str) -> list[str]:
    """Every selector whose own declarations reach for the amber.

    Counted by selector rather than by declaration, because an element
    that takes the amber as a border and not a background has still taken
    it. The token definitions in `:root` are not counted: defining the
    brand's amber is not spending it.
    """
    out = []
    for selector, body in _rules(css):
        for prop, value in _declarations(body):
            if prop.strip().startswith("--"):
                continue
            lowered = value.lower()
            if AMBER_REF.search(lowered) or any(hue in lowered for hue in AMBER):
                out.append(selector)
                break
    return out


def test_the_shell_colors_are_the_packaged_ones_and_the_amber_means_one_thing(
    markup,
):
    """The window is painted in the engine's palette, and the amber is one thing.

    Two pins ported from the engine's own page: no color is invented for
    this window, and the amber says one thing per screen. On the gate it
    is the press that agrees. While the run is going it is the step in
    flight. On a stop a second press can move it is the press forward,
    and on a stop no press can move it is the segment the run stopped on,
    because there is no press on that screen to carry it.

    One element per screen, not one selector in the file: the two screens
    that could hold two are held apart by the filler, which draws the
    amber segment exactly when it does not draw the press.
    """
    css = read(SHELL_SRC / "styles.css")
    packaged = read(PAGE_CSS)

    literals = COLOR_LITERAL.findall(re.sub(r"/\*.*?\*/", " ", css, flags=re.S))
    assert literals, "the shell stylesheet names no colors at all"
    assert _invented(css, packaged) == []

    # And the hold is a hold: a color written in any syntax, and a color
    # named rather than written, both fail it.
    assert _invented("a { color: red; }", packaged)
    assert _invented("a { color: hsl(340 90% 55%); }", packaged)
    assert _invented("a { color: #abc; }", packaged)
    assert _invented("a { border-color: oklch(0.7 0.15 60); }", packaged)
    assert _invented("a { background: color-mix(in srgb, red, blue); }", packaged)
    assert _invented("a { color: var(--ink); }", packaged) == []

    # Nothing on the screen paints itself, so the stylesheet is the only
    # place the amber can arrive from.
    assert "style=" not in markup
    for hue in AMBER:
        assert hue not in markup.lower()

    reaching = _amber_selectors(css)
    assert frozenset(reaching) == AMBER_SELECTORS, reaching
    assert len(reaching) == len(AMBER_SELECTORS), "a selector takes it twice"

    # The two presses that take it are the two the screen stands on, and
    # each is written once.
    assert '<button type="button" id="consent"' in markup
    assert '<button type="button" id="retry"' in markup
    assert markup.count('<button') == markup.count('type="button"')
    assert markup.count('<button') == 2

    # The gate holds the press that agrees and nothing else that could
    # take the amber; the panel holds the other press and the track.
    gate = re.search(r'<section class="gate">(.*?)</section>', markup, re.S)
    assert gate is not None, "the gate panel is gone"
    assert 'id="consent"' in gate.group(1)
    assert 'id="retry"' not in gate.group(1)
    assert "steps-track" not in gate.group(1)

    # And the filler draws the amber segment exactly when there is no
    # press forward to carry it, so no screen ever shows both.
    script = read(SHELL_SRC / "bootstrap.js")
    assert SEGMENT_CHOICE in script, "the stopped segment picks its own mark"
    assert PRESS_CHOICE in script, "the press forward shows on its own terms"
    assert STOP_STEP_CHOICE in script, (
        "a stop counts a step only for a run that happened")
    assert script.count('"stopped-final"') == 1
    assert script.count('"active"') == 1


# --- D14: the one name, in the four places Tauri needs it --------------

def test_the_window_title_is_said_once_and_copied_everywhere(strings, markup):
    """The strings file is the source; the rest are pinned equal to it.

    Tauri needs the name in its own config and Cargo needs it in the
    package description, so the copies stay where the tools read them and
    a test holds them level. Retitling at runtime would flash the wrong
    name first, which is why there is no set_title anywhere in the shell.
    """
    title = strings["window_title"]
    assert title == "RadialRig"

    conf = json.loads(read(TAURI / "tauri.conf.json"))
    assert conf["productName"] == title
    assert [window["title"] for window in conf["app"]["windows"]] == [title]

    assert re.search(r"<title>([^<]*)</title>", markup).group(1) == title

    cargo = read(TAURI / "Cargo.toml")
    description = re.search(r'^description = "([^"]*)"', cargo, re.M).group(1)
    assert description.startswith(f"{title} ")

    # The whole source, not only its literals: a retitle written into a
    # comment today is a retitle uncommented tomorrow.
    for path in rust_files():
        assert RETITLE not in read(path), path.name


# --- U5: the one flag, and the identifier it resolves the record under --

def test_the_uninstall_flag_is_written_in_one_place():
    """One spelling of the flag, in one file, once.

    Two copies would be two things to keep level: the hook that runs the
    binary and the branch that answers it have to agree letter for
    letter, and a flag nobody answers is a window drawn mid-uninstall.
    The hook file is checked against that same one spelling rather than
    restating it here.
    """
    found = [
        (path, text) for path in rust_files()
        if UNINSTALL_FLAG in (text := read(path))
    ]
    assert len(found) == 1, [path.name for path, _ in found]
    path, text = found[0]
    assert text.count(UNINSTALL_FLAG) == 1, path.name

    hooks = read(HOOKS)
    assert hooks.count(UNINSTALL_FLAG) == 2, "the two runs the guard picks between"
    assert hooks.count(QUIET_FLAG) == 1, "the box is suppressed on one of them"

    # And the branch that answers it, ahead of the builder. Deleting it
    # would leave the hook starting a binary that draws a window and an
    # engine in the middle of an uninstall, which nothing else here would
    # catch: the flag would still be spelled once, in the module.
    opening = read(RUST_SRC / "main.rs")
    branch = opening[
        opening.index("fn main() {"):opening.index("tauri::Builder")]
    assert "removal::FLAG" in branch, "the flag branch is gone from main"
    assert "removal::run" in branch, "nothing answers the flag before the builder"


def app_files() -> list[Path]:
    """Every file of the app and its installers, as a checkout carries it.

    The three folders left out are the ones the build writes and git
    ignores (`app/src-tauri/.gitignore`): what lands there is regenerated
    from the files this walk does read.
    """
    built = {TAURI / "target", TAURI / "gen", TAURI / "permissions"}
    return sorted(
        path for path in APP.rglob("*")
        if path.is_file() and not any(root in path.parents for root in built))


def test_nothing_in_the_app_or_its_installer_spells_the_identifier():
    """The app's own folders follow the build's identifier.

    PLAN-0021 phase 2, prescription 1. A proof build carries another
    identifier, passed with `--config`, so that it can be installed,
    run and taken out beside the owner's app without touching it. Every
    place that spelled the shipped identifier out would still reach the
    owner's folders from that build: the removal flag would read his
    Path record, and the uninstaller would delete his two folders. So
    the identifier is spelled in exactly two places. One is its source,
    `tauri.conf.json`, which the overlay changes. The other is the
    sentence the .deb prints on removal, which runs after the app is
    gone and so cannot ask it; a .deb is never built under a proof
    identity. Every other file, binary ones included, is read as bytes.
    """
    conf = json.loads(read(TAURI / "tauri.conf.json"))
    identifier = conf["identifier"]
    spelled = identifier.encode("utf-8")
    files = app_files()
    postrm = TAURI / "installer" / "deb" / "postrm"
    assert TAURI / "tauri.conf.json" in files and postrm in files

    found = [path.relative_to(APP).as_posix() for path in files
             if path not in (TAURI / "tauri.conf.json", postrm)
             and spelled in path.read_bytes()]
    assert found == [], found

    # The config spells it once, as the one key the overlay replaces.
    assert read(TAURI / "tauri.conf.json").count(identifier) == 1

    # The .deb's sentence spells it on its two message lines, and nowhere
    # else in that script: a command there would be a deletion the
    # overlay never reaches.
    lines = read(postrm).splitlines()
    named = [number for number, line in enumerate(lines, start=1)
             if identifier in line]
    assert named == [24, 25], named
    assert all(lines[number - 1].lstrip().startswith('echo "')
               for number in named), named


def test_the_uninstaller_clears_the_folders_of_its_own_identifier():
    """The hook names the two folders by the template's own define.

    The NSIS template tauri-cli 2.11.4 bundles (tauri-bundler 2.9.4)
    defines `BUNDLEID` from the config's identifier, the one a
    `--config` overlay changes, and deletes `$APPDATA\\${BUNDLEID}` itself
    when its own app-data box is ticked. The hook clears the same two
    folders on every true uninstall, so a proof build's uninstaller
    clears the proof build's folders and nothing of the owner's.

    Each folder is named whole and named once. Counting the define alone
    would pass a file that cleared one root twice and the other never.
    """
    hooks = read(HOOKS)
    for root in ("$APPDATA", "$LOCALAPPDATA"):
        line = f'RMDir /r "{root}\\${{BUNDLEID}}"'
        assert hooks.count(line) == 1, line
    assert hooks.count("RMDir") == 2, "a third folder is cleared"


# --- U1: the bundle config, key by key ---------------------------------

def test_the_bundle_config_names_the_binary_the_icons_and_the_hooks():
    """The seven keys U1 pins, and nothing beside them.

    Both the root config struct and the nsis object refuse unknown
    fields, so a stray key is a build that stops rather than a setting
    that quietly does nothing. The set is restated here for the same
    reason the strings file's set is: a test that read its expectations
    out of the file under test would pass whatever that file said.
    """
    conf = json.loads(read(TAURI / "tauri.conf.json"))

    # A root key, not a bundle key. Without it the installed file keeps
    # the Cargo package name while the shortcut says the product name.
    assert conf["mainBinaryName"] == MAIN_BINARY
    assert conf["productName"] == MAIN_BINARY

    bundle = conf["bundle"]
    nsis = bundle["windows"]["nsis"]
    assert frozenset(nsis) == NSIS_KEYS, frozenset(nsis) ^ NSIS_KEYS

    # No admin prompt exists anywhere, which is what this one key decides.
    assert nsis["installMode"] == "currentUser"
    assert nsis["languages"] == ["English"]
    assert nsis["displayLanguageSelector"] is False
    assert nsis["compression"] == "lzma"

    # Every path the installer is handed is a file that is really there.
    # The bundler canonicalises these and does no validation of its own.
    for key in ("installerIcon", "uninstallerIcon", "installerHooks"):
        assert (TAURI / nsis[key]).is_file(), nsis[key]
    assert nsis["installerIcon"] == nsis["uninstallerIcon"]
    assert (TAURI / nsis["installerHooks"]) == HOOKS

    # The footer of every installer page and the exe's own version block
    # both read this string, so it is swept like any other sentence.
    footer = bundle["copyright"]
    assert footer.strip()
    sweep([("bundle.copyright", footer)])
    assert bundle["publisher"] in footer

    # The deb's Description body and its menu category. Without the
    # first the shipped package says "(none)", and without the second
    # the desktop entry's Categories= is empty and GNOME files the app
    # under nothing — both reached v0.1.4, per the release report.
    sweep([("bundle.longDescription", bundle["longDescription"])])
    assert bundle["longDescription"].strip()
    assert bundle["category"] == "DeveloperTool"


def test_the_bundle_signs_through_the_script_beside_its_metadata():
    """PLAN-0011 phase 1: the command, the script, and the three fields.

    Three links, each held to the next. The config names one command;
    that command names the script; the script reads the metadata file
    beside it; and the metadata file states an endpoint, an account and
    a certificate profile, and nothing else. A drift anywhere along that
    chain would build an installer nobody signed, which is exactly the
    state this plan ended.

    Nothing in either file is a credential. Who may sign with that
    profile is decided by the Azure login on the build machine, so the
    sweep below is a hold on that, not a reminder.
    """
    conf = json.loads(read(TAURI / "tauri.conf.json"))
    windows = conf["bundle"]["windows"]

    # signCommand sits beside the nsis object, not inside it. Both
    # structs refuse unknown fields, so a key in the wrong place is a
    # build that stops rather than a setting that quietly does nothing.
    assert frozenset(windows) == {"signCommand", "nsis"}, frozenset(windows)
    assert windows["signCommand"] == SIGN_COMMAND

    # The bundler splits the string on spaces, so an argument that
    # carried one would arrive as two.
    parts = SIGN_COMMAND.split(" ")
    assert parts[0] == "powershell"
    assert parts[-1] == "%1"
    assert SIGN_SCRIPT in parts
    assert all(part for part in parts), parts

    script = TAURI / SIGN_SCRIPT
    assert script.is_file(), SIGN_SCRIPT
    text = script.read_text(encoding="utf-8")

    # The script reads the metadata beside itself, and hands SignTool
    # the two flags that make a signature this release can check back.
    assert SIGN_METADATA in text
    assert "$PSScriptRoot" in text
    for flag in ("/fd SHA256", "/td SHA256", "/dlib", "/dmdf"):
        assert flag in text, flag
    assert "http://timestamp.acs.microsoft.com" in text

    # Windows PowerShell 5.1 runs this: neither operator below exists
    # there, and a script that used one would fail to parse on the build
    # machine. Read over the code alone, because the script's own header
    # names both operators to say it does not use them.
    code = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "&&" not in code and "||" not in code

    metadata = TAURI / "installer" / SIGN_METADATA
    assert metadata.is_file(), SIGN_METADATA
    assert json.loads(metadata.read_text(encoding="utf-8")) == SIGNING_FIELDS

    for where, said in (("sign.ps1", text),
                        (SIGN_METADATA,
                         metadata.read_text(encoding="utf-8"))):
        lowered = said.lower()
        for banned in SIGNING_BANNED:
            assert banned not in lowered, f"{where} says '{banned}'"


# --- PLAN-0009 phase 4: the deb's two maintainer scripts ---------------

def test_the_deb_scripts_stand_in_for_the_nsis_hook():
    """Prescription 1's pair: wired, present, and held to the sweep.

    dpkg runs these under /bin/sh as root. A CR in the first line reads
    as a bad interpreter and the removal fails half done, so the bytes
    are pinned beside the wiring. The prerm does nothing, stated: the
    Linux app records no Path entry, so there is nothing to take back
    before deletion. The postrm's sentences are the one place the deb
    speaks with words of its own, because the app's binary is already
    gone when postrm runs, so they take the same sweep every shipped
    sentence takes.
    """
    conf = json.loads(read(TAURI / "tauri.conf.json"))
    deb = conf["bundle"]["linux"]["deb"]
    assert frozenset(deb) == {"preRemoveScript", "postRemoveScript"}, deb

    for key in ("preRemoveScript", "postRemoveScript"):
        at = TAURI / deb[key]
        assert at.is_file(), deb[key]
        raw = at.read_bytes()
        assert b"\r" not in raw, f"{deb[key]} carries a CR"
        text = raw.decode("utf-8")
        assert text.startswith("#!/bin/sh\n"), deb[key]
        assert text.rstrip().endswith("exit 0"), deb[key]

    prerm = (TAURI / deb["preRemoveScript"]).read_text(encoding="utf-8")
    lines = [line for line in prerm.splitlines()
             if line.strip() and not line.startswith("#")]
    assert lines == ["exit 0"], lines

    # The postrm speaks only on a true removal, and it names the app's
    # own per-user directories by the one identifier everything shares.
    postrm = (TAURI / deb["postRemoveScript"]).read_text(encoding="utf-8")
    assert "remove|purge)" in postrm, "the upgrade guard is gone"
    said = " ".join(re.findall(r'echo "([^"]*)"', postrm))
    assert said, "the postrm names nothing as staying"
    sweep([("the postrm", said)])
    assert conf["identifier"] in said


# --- U4: what the uninstaller removes, and what it says ----------------

def nsis_quoted(text: str) -> list[str]:
    """Every quoted string in one NSIS file, comments left out.

    All three quoting forms, read outermost first, because the one
    command that carries an argument list quotes the whole of it with one
    form and the path inside it with the other. The backtick is the third
    form NSIS accepts and no line here uses; it is swept anyway, since a
    quoting form this reader skipped would be the one place prose could
    stand in the uninstaller unread.
    """
    out: list[str] = []
    index, end = 0, len(text)
    while index < end:
        char = text[index]
        if char in "\"'`":
            close = text.find(char, index + 1)
            if close < 0:
                break
            out.append(text[index + 1:close])
            index = close + 1
            continue
        index += 1
    return out


def path_or_flag(token: str) -> bool:
    """Whether one word of a quoted string is a path or one of the flags.

    A path fragment carries a separator or opens with an NSIS variable,
    and the two flags are named. Anything else is prose, which is the
    thing an uninstaller must not carry. There is no allowance for a bare
    word any more: the one place a publisher's own name used to split into
    one was the two registry keys, and those are written as the template's
    own defines now.
    """
    bare = token.strip('"')
    if not bare:
        return True
    if bare in (UNINSTALL_FLAG, QUIET_FLAG):
        return True
    return "\\" in bare or bare.startswith("$")


def test_the_uninstaller_hooks_are_guarded_and_say_nothing_of_their_own():
    """Two macros, both inside the guard, neither of them speaking.

    The guard is the same one the template uses for the shortcuts, the
    autostart value and the app data: an upgrade keeps the Path entry and
    both record files and shows nothing, and a true uninstall takes them
    out and says so once. The saying is the app's own binary's job, out of
    the strings file this test sweeps — a sentence typed into the `.nsh`
    would be the one sentence on this surface no sweep reads.
    """
    text = read(HOOKS)
    bare = "\n".join(
        line for line in text.split("\n") if not line.lstrip().startswith(";"))

    named = re.findall(r"^\s*!macro\s+(\S+)", bare, re.M)
    assert frozenset(named) == HOOK_MACROS, named
    assert len(named) == len(HOOK_MACROS), named
    assert bare.count("!macroend") == len(HOOK_MACROS)

    bodies = re.findall(r"!macro\s+\S+\n(.*?)!macroend", bare, re.S)
    assert len(bodies) == len(HOOK_MACROS), "a macro this reader cannot read"
    for body in bodies:
        lines = [line.strip() for line in body.split("\n") if line.strip()]
        assert lines, "an empty hook"
        assert lines[0] == UPDATE_GUARD, lines[0]
        assert lines[-1] == GUARD_END, lines[-1]

    for instruction in SPEAKING:
        assert instruction not in bare, instruction

    # The running-app check runs before our own ExecWait, not after it as
    # the template's own copy does. A reader who cancels at that box must
    # not already have lost the Path entry and the record.
    body = bodies[named.index("NSIS_HOOK_PREUNINSTALL")]
    lines = [line.strip() for line in body.split("\n") if line.strip()]
    assert lines[1] == RUNNING_CHECK, lines[1]
    assert lines.index(RUNNING_CHECK) < min(
        index for index, line in enumerate(lines) if line.startswith("ExecWait"))

    # The two registry keys are the template's own defines, so they follow
    # bundle.publisher and productName by construction. Spelled out, a
    # rename would leave this file deleting a key nothing writes.
    post = bodies[named.index("NSIS_HOOK_POSTUNINSTALL")]
    for key in REGISTRY_DEFINES:
        assert key in post, key
    publisher = json.loads(read(TAURI / "tauri.conf.json"))["bundle"]["publisher"]
    assert publisher not in text, "the hook spells a key the defines carry"

    quoted = nsis_quoted(bare)
    assert quoted, "the hooks name no paths at all"
    for said in quoted:
        for token in said.split():
            assert path_or_flag(token), said


# --- PLAN-0021 phase 2: the engine swap ---------------------------------

# The swap's own sentences: the line the screen shows while it replaces
# the engine, and the two that can stand under its give-up.
SWAP_KEYS = ("swap_running", "swap_held", "swap_held_none")

# The line that draws the give-up's added sentence, and the element it
# draws into.
HELD_CHOICE = 'show(at("held"), !!held);'


def plain_words(where: str, text: str) -> None:
    """Constraint 4: no command, and no uv or Python detail."""
    lowered = text.lower()
    assert re.search(r"\buv\b", lowered) is None, f"{where} names uv"
    assert "python" not in lowered, f"{where} names Python"
    assert ".exe" not in lowered, f"{where} names a program file"
    assert "`" not in text and " --" not in text, f"{where} reads as a command"


def test_the_swap_says_what_it_does_and_what_still_holds_the_engine(
    strings, markup,
):
    """Decided 7 and prescription 4, as the screen says them.

    While the swap runs, the screen says in plain words that programs
    using RadialRig lose it for a moment and may need to reconnect. The
    line is not one of the nine steps, so it draws no count. When the
    swap gives up, its stop carries one added sentence: the programs that
    still hold the engine, by file name, or that none was found running
    from the engine's folders and some other program may have a file open
    there. The give-up stands on the recovery's own sentence, which must
    not claim the engine was taken out, because a give-up can come from an
    uninstall that could not take it out.
    """
    running = strings["swap_running"]
    head, sep, under = running.partition("\n")
    assert sep and head.strip() and under.strip()
    assert "programs that use radialrig" in under.lower()
    assert "lose it for a moment" in under
    assert "reconnect" in under
    # A close in the middle of the swap ends uv with the window, which can
    # leave the old engine half removed (phase 2 review), so the line asks
    # the reader to keep the window open.
    assert "keep this window open" in under.lower()

    held = strings["swap_held"]
    assert "\n" not in held
    assert re.findall(r"\{([^{}]*)\}", held) == ["detail"]
    none = strings["swap_held_none"]
    assert "\n" not in none and "{" not in none
    assert "no program" in none.lower()
    assert "may have a file open" in none

    for key in SWAP_KEYS:
        plain_words(key, strings[key])
    recovery = strings["fail_recovery"].partition("\n")[0]
    assert "was taken out" not in recovery
    plain_words("fail_recovery", recovery)

    # Rust names all three, so a key that went missing is a startup stop.
    rust = read(RUST_SRC / "strings.rs")
    for key in SWAP_KEYS:
        assert f'"{key}"' in rust, key
        constant = key.upper()
        assert re.search(rf"\n    {constant},\n", rust), f"{constant} is not emitted"

    # The page draws the added sentence in its own line, under the detail,
    # and only on a stop.
    script = read(SHELL_SRC / "bootstrap.js")
    assert HELD_CHOICE in script, "the added sentence is never drawn"
    assert "stopped && view.held" in script
    assert '<div class="status">' in markup, "the status panel is gone"
    panel = markup.split('<div class="status">', 1)[1].split('id="actions"', 1)[0]
    assert markup.count('id="held"') == 1
    assert '<p class="detail" id="held" hidden></p>' in panel
    assert panel.index('id="detail"') < panel.index('id="held"')


# A sentence that tells the reader to close something, in any form.
CLOSE_WORD = re.compile(r"\bclos(?:e|es|ed|ing)\b", re.I)

# The one place a close word stands, and what it says there: the update
# dialog saying what updating does to the app itself (PLAN-0021 phase 3,
# prescription 3, whose words these are). The update closes the app; the
# reader is asked to close nothing.
UPDATE_CLOSES = "Updating closes RadialRig"


def test_no_shell_string_tells_the_reader_to_close_programs(strings):
    """Prescription 5: the app stops what holds the engine itself.

    Neither old-engine sentence asks the reader to close anything, and no
    other sentence the shell can say does either. Each of the two says
    what the app will do instead: its press replaces the engine, and
    programs that use RadialRig lose it for a moment (Decided 7).

    Phase 3's update dialog is the one sentence with a close word in it,
    and only as the plan wrote it: updating closes the app, which then
    opens again. Taken out once, in that one sentence, nothing is left
    to say close anywhere.
    """
    for where, text in walk(strings):
        if where == UPDATE_READY:
            assert text.count(UPDATE_CLOSES) == 1, where
            text = text.replace(UPDATE_CLOSES, "Updating", 1)
        found = CLOSE_WORD.search(text)
        assert found is None, f"{where} says '{found.group(0) if found else ''}'"

    for key in OLD_ENGINE:
        under = strings[key].partition("\n")[2]
        assert f"{strings['retry_label']} and this app replaces it" in under, key
        assert "programs that use RadialRig lose it for a moment" in under, key
        assert "reconnect" in under, key
        plain_words(key, strings[key])

    # The reader is a reader.
    assert CLOSE_WORD.search("Close any program first") is not None
    assert CLOSE_WORD.search("a closet of enclosed things") is None


def test_the_folder_stop_is_true_for_both_folders_and_names_no_uv(strings):
    """The stop both of uv's folders share, and the install that left none.

    It used to say uv did not name "the folder it installs commands
    into", which is the launcher folder alone, and the engine swap now
    reaches the same stop when uv cannot name its tool folder. Rewritten
    under Constraint 4, it names neither uv nor a program file, so it is
    one sentence on both platforms.
    """
    said = strings["fail_tool_dir"]
    assert isinstance(said, str), "the folder stop is still per platform"
    head = said.partition("\n")[0]
    assert "installs commands into" not in said
    assert "folders" in head
    plain_words("fail_tool_dir", said)


# --- the step counter's pacing (reports/release-0.1.4-2026-08-23) ------

def test_every_picture_routes_through_the_hold_queue():
    """A step card holds the screen long enough to be read.

    The v0.1.4 release test caught the counter skipping steps, and
    skipping different ones on each run: a step that ends in a few
    milliseconds never got a frame. The fix lives in the one file that
    draws. Pictures queue, each drawn one holds, and every arrival
    routes through the queue, because one direct draw would put a
    picture on screen out of order with the held ones.
    """
    script = read(SHELL_SRC / "bootstrap.js")
    assert "var HOLD" in script, "the hold constant is gone"
    assert "enqueue(message.payload)" in script
    assert ".then(enqueue, function () { })" in script
    assert "enqueue(latest)" in script
    assert "draw(message.payload)" not in script, "an event skips the queue"
    assert "then(draw" not in script, "a press answer skips the queue"
    assert script.count("draw(queue.shift())") == 1


# --- PLAN-0021 phase 2, prescription 6: the README ---------------------
# Prescription 7's check reads the bug notes, which stay private, so it
# runs in the workshop-only module (PLAN-0023 phase 1).

def test_the_readme_engine_half_says_the_app_replaces_the_engine_itself():
    """Prescription 6: the app's route needs no press and closes nothing.

    The README's engine half says the app replaces an older engine
    itself, and what that does to programs that use RadialRig. The
    command line's route stops nothing, so it keeps its instruction to
    close programs first (Known and out of scope).
    """
    text = read(ROOT / "README.md")
    section = text.split("## Upgrading", 1)[1].split("\n## ", 1)[0]
    half = " ".join(next(
        part for part in section.split("\n\n")
        if part.startswith("**The engine half**")).split())
    assert "offers to replace" not in half
    assert "replaces an older engine itself, with no press" in half
    # Only Windows locks a running program's files, so only there does the
    # app stop anything (Known and out of scope; phase 2 review).
    assert "On Windows it first stops the programs" in half
    assert "programs that use RadialRig lose it for a moment" in half
    assert "may need to reconnect" in half
    assert "it stops nothing" in half
    assert (
        "Close the RadialRig app and any manage page first: they hold the "
        "executables the install replaces.") in half
    assert half.index("it stops nothing") < half.index("Close the RadialRig app")


# --- PLAN-0021 phase 3, prescription 2: where the app looks for an update -

# The one address every installed app asks, restated rather than read: a
# test that took it out of the config under test would agree with any
# address the config named. It is the public repository's own "latest
# release" door, so it follows each release without a rewrite, and
# because every installed app holds it, the repository is never renamed
# or moved (Decided 12).
UPDATE_ENDPOINT = (
    "https://github.com/thejug923/radialrig/releases/latest/download/latest.json")
UPDATE_HOST = "github.com"
UPDATE_REPOSITORY = "/thejug923/radialrig/"
UPDATE_MANIFEST = "releases/latest/download/latest.json"

# The owner's public update key, `radialrig-updater.key.pub`, exactly as
# the signer wrote it. Public material: it can check a signature and can
# make none. Its key id is the one the release's signing step checks each
# signature against (prescription 4).
UPDATE_PUBKEY = (
    "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEE4MDYzRUZDMzQ1Mjcx"
    "NjYKUldSbWNWSTAvRDRHcUYwS2szV1ZHNHpBTlFQU3lOTGNYNjYzd3NBTGd5Wm4rYzV3T1RZ"
    "cUtnbW0K")
UPDATE_KEY_ID = "A8063EFC34527166"

# The crate, the one version whose source was read for what this phase
# leans on, and the one feature it builds with.
UPDATER_CRATE = "tauri-plugin-updater"
UPDATER_VERSION = "2.12.0"
UPDATER_FEATURES = ["rustls-tls"]


def dangerous_keys(value, where: str = "") -> list[str]:
    """Every key at any depth whose name opens with `dangerous`.

    The updater's three switches that loosen its transport all carry the
    prefix: plain http, any certificate, any host name. None belongs in a
    shipped config, so the walk reads every key the file has rather than
    the three this plugin version happens to know.
    """
    found: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            here = f"{where}.{key}" if where else str(key)
            if str(key).lower().startswith("dangerous"):
                found.append(here)
            found += dangerous_keys(inner, here)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            found += dangerous_keys(inner, f"{where}[{index}]")
    return found


def test_the_app_asks_one_https_address_on_the_public_repository():
    """Invariant 5: one endpoint, https, on the public repository.

    PLAN-0021 phase 3, prescription 2. The updater reads its address,
    its key and its Windows install mode from `plugins.updater`, and
    nothing else is there. An insecure-transport switch would let a
    plain-http answer name the update; the proof build turns one on
    through its own `--config` overlay and never through this file.
    `createUpdaterArtifacts` stays off, so a build needs no signing key
    (Decided 10): the release script signs the update files itself.
    """
    conf = json.loads(read(TAURI / "tauri.conf.json"))
    assert frozenset(conf.get("plugins", {})) == {"updater"}, conf.get("plugins")
    updater = conf["plugins"]["updater"]
    assert frozenset(updater) == {"pubkey", "endpoints", "windows"}, (
        frozenset(updater))

    endpoints = updater["endpoints"]
    assert endpoints == [UPDATE_ENDPOINT], endpoints
    parts = urlsplit(endpoints[0])
    assert parts.scheme == "https", endpoints[0]
    assert parts.netloc == UPDATE_HOST, endpoints[0]
    assert parts.path.startswith(UPDATE_REPOSITORY), endpoints[0]
    assert parts.path.endswith(UPDATE_MANIFEST), endpoints[0]
    assert not parts.query and not parts.fragment, endpoints[0]

    # Passive: a progress bar and no question, the plugin's own default,
    # stated so a later plugin cannot move it.
    assert updater["windows"] == {"installMode": "passive"}, updater["windows"]

    assert dangerous_keys(conf) == [], dangerous_keys(conf)
    # The walker is a hold only if it finds what it is for.
    assert dangerous_keys(
        {"plugins": {"updater": {"dangerousInsecureTransportProtocol": True}}}
    ) == ["plugins.updater.dangerousInsecureTransportProtocol"]

    assert conf["bundle"].get("createUpdaterArtifacts", False) is False


def test_the_app_checks_updates_against_the_owners_public_key():
    """The key the plugin checks every update's signature against.

    The signature check cannot be turned off (Verified 14), so this
    string decides whose updates an installed app takes. It is the
    owner's public key: base64 of minisign's two-line public key file,
    whose second line is 42 bytes, the algorithm `Ed`, the key id, and
    the key. The id the comment names is the id the bytes carry.
    """
    conf = json.loads(read(TAURI / "tauri.conf.json"))
    pubkey = conf["plugins"]["updater"]["pubkey"]
    assert pubkey == UPDATE_PUBKEY

    said = base64.b64decode(pubkey, validate=True).decode("ascii")
    comment, key, rest = said.split("\n")
    assert rest == "", "one key, two lines"
    assert comment == f"untrusted comment: minisign public key: {UPDATE_KEY_ID}"
    blob = base64.b64decode(key, validate=True)
    assert len(blob) == 42 and blob[:2] == b"Ed", len(blob)
    assert blob[2:10][::-1].hex().upper() == UPDATE_KEY_ID
    assert "secret" not in said.lower()


def test_the_updater_crate_is_pinned_and_builds_on_rustls_alone():
    """Assumed 2 in the suite: no native TLS, on any platform.

    The crate is pinned to the version whose source was read, with its
    default features off and only `rustls-tls` back on, the TLS the rest
    of the app already uses. Cargo.lock lists every platform's packages,
    so a native TLS stack that entered for either build would name
    itself there. The plugin is registered in Rust, beside the dialog
    plugin, and is called from Rust only (Decided 9).
    """
    cargo = read(TAURI / "Cargo.toml")
    line = next((text for text in cargo.splitlines()
                 if text.startswith(f"{UPDATER_CRATE} =")), None)
    assert line is not None, f"{UPDATER_CRATE} is not a dependency"
    assert f'version = "={UPDATER_VERSION}"' in line, line
    assert "default-features = false" in line, line
    features = re.search(r"features = (\[[^\]]*\])", line)
    assert features is not None, line
    assert json.loads(features.group(1)) == UPDATER_FEATURES, line
    # Read over the manifest's settings alone: its comments name the
    # native stack to say why it is left out.
    settings = "\n".join(text for text in cargo.splitlines()
                         if not text.lstrip().startswith("#"))
    assert "native-tls" not in settings

    lock = read(TAURI / "Cargo.lock")
    packages = dict(re.findall(
        r'^\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', lock, re.M))
    assert packages.get(UPDATER_CRATE) == UPDATER_VERSION, packages.get(UPDATER_CRATE)
    for native in ("native-tls", "openssl-sys", "openssl"):
        assert native not in packages, native

    main = read(RUST_SRC / "main.rs")
    assert ".plugin(tauri_plugin_updater::Builder::new().build())" in main


# --- PLAN-0021 phase 3, Invariant 6: the window gains nothing -----------

# The capability, restated: the bootstrap window, local documents only,
# the core set and the app's own two commands, and no plugin's command.
CAPABILITY = {
    "identifier": "default",
    "local": True,
    "windows": ["main"],
    "permissions": [
        "core:default", "allow-bootstrap-proceed", "allow-bootstrap-retry"],
}
APP_COMMANDS = ["bootstrap_proceed", "bootstrap_retry"]


def capability_drift(capability: dict) -> list[str]:
    """What one capability file says beyond the pinned one, or differently."""
    drift = [key for key in capability
             if key not in CAPABILITY and key not in ("$schema", "description")]
    drift += [key for key, value in CAPABILITY.items()
              if capability.get(key) != value]
    return drift


def test_the_window_gains_no_permission_and_the_app_no_command():
    """Invariant 6 (Verified 10): the update adds nothing the page can call.

    The update runs in Rust (Decided 9). A plugin's commands are callable
    from a page only through a permission a capability grants, so the
    updater's four commands stay out of reach while this file names none
    of them; calling the plugin from Rust goes through no capability. The
    app's own command list is pinned where Tauri reads it twice: the
    handler `main.rs` registers and the manifest `build.rs` declares.
    """
    capability = json.loads(read(TAURI / "capabilities" / "default.json"))
    assert capability_drift(capability) == [], capability_drift(capability)
    assert "remote" not in capability
    # The pin refuses what it exists to refuse.
    widened = dict(capability, permissions=capability["permissions"]
                   + ["updater:default"])
    assert capability_drift(widened) == ["permissions"]
    assert sorted(path.name for path in (TAURI / "capabilities").iterdir()) == [
        "default.json"]

    main = read(RUST_SRC / "main.rs")
    handler = re.search(r"generate_handler!\[(.*?)\]", main, re.S)
    assert handler is not None, "the command list is gone"
    named = [part.strip().rsplit("::", 1)[-1]
             for part in handler.group(1).split(",") if part.strip()]
    assert named == APP_COMMANDS, named
    assert main.count("generate_handler!") == 1

    build = read(TAURI / "build.rs")
    declared = re.search(r"\.commands\(&\[(.*?)\]\)", build, re.S)
    assert declared is not None, "the command manifest is gone"
    assert re.findall(r'"([^"]+)"', declared.group(1)) == APP_COMMANDS


# --- PLAN-0021 phase 3, prescription 3: what the update dialog says ------

# The four sentences a failed update ends on, one per cause: the new
# version did not arrive, it arrived without the owner's signature, its
# signature was made for another version than the one on offer (the
# owner's decision of 2026-10-03), or it arrived and could not be put in
# place.
UPDATE_FAILED = (
    "update_failed_download", "update_failed_signature",
    "update_failed_version", "update_failed_install")
UPDATE_KEYS = (UPDATE_READY, "update_now", "update_later", *UPDATE_FAILED)
NOT_INSTALLED = "The update was not installed, and nothing changed."


def test_the_update_dialog_names_both_versions_and_says_what_updating_does(
    strings,
):
    """Prescription 3, in the dialog's own words.

    The dialog says RadialRig <new> is ready, that this is <current>, and
    that updating closes RadialRig, installs the new version and opens it
    again. Its two presses are "Update now" and "Later". A failure says
    the update was not installed, that nothing changed, and why, in plain
    words: no command, no uv or Python, and no machine error quoted, since
    a network library's message names hosts and parts a reader does not
    know. Rust shows all of them in native dialogs, so Rust names every
    key and reads every sentence (no page renders them).
    """
    ready = strings[UPDATE_READY]
    head, sep, under = ready.partition("\n")
    assert sep and "\n" not in under
    assert head == "RadialRig {detail} is ready. This is RadialRig {current}."
    assert under == (
        "Updating closes RadialRig, installs the new version and opens it again.")
    assert strings["update_now"] == "Update now"
    assert strings["update_later"] == "Later"

    for key in UPDATE_FAILED:
        said = strings[key]
        head, sep, under = said.partition("\n")
        assert head == NOT_INSTALLED, key
        assert sep and under.strip() and "\n" not in under, key
        assert "{" not in said, key
        assert "error was" not in said.lower(), key
    assert "signature" in strings["update_failed_signature"]
    assert "download" in strings["update_failed_download"]
    assert "signed for" in strings["update_failed_version"]

    for key in UPDATE_KEYS:
        plain_words(key, strings[key])
    sweep([(key, strings[key]) for key in UPDATE_KEYS])

    rust = read(RUST_SRC / "strings.rs")
    for key in UPDATE_KEYS:
        assert f'"{key}"' in rust, key
        assert re.search(rf"\n    {key.upper()},\n", rust), f"{key} is not emitted"
        assert re.search(rf"\n    pub {key}: String,\n", rust), f"{key} is not read"
