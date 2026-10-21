#!/usr/bin/env python3
"""Build one RadialRig release and stop before anything reaches outward.

Usage:  uv run --no-sync scripts/release.py --linux <dir>
                                            [--allow-dirty] [--rebuild]
        uv run --no-sync scripts/release.py bump <new-version>
        uv run --no-sync scripts/release.py pin-wheel

`bump` writes one version into the six places that state it, in one
press: pyproject.toml's version line, tauri.conf.json's version key,
both versions inside bootstrap.json's package_source address,
bootstrap.json's engine_min_version, the floor the shipped app holds
an already-installed engine to, and uv.lock's radialrig entry, which
`uv lock` rewrites once pyproject.toml has moved. It then re-reads all
six through the same readers the build uses and refuses any
disagreement, sweeps the tree for the old literal, and finishes by
running `pin-wheel`. So one press writes four files: six version
statements and the digest.

`pin-wheel` builds the wheel from this tree, takes its sha256, and
writes it into bootstrap.json's package_sha256. That is the digest the
shipped app proves the downloaded wheel against before it installs it,
so it can only come from a wheel and never from a typed value. The
build refuses when the wheel it just built does not match what is
pinned, and the refusal names this subcommand.

Reads the version from pyproject.toml and the public address from
app/src/bootstrap.json, builds the wheel and the NSIS installer, takes
the two Linux bundles from the directory --linux names, and assembles
the six assets under dist/release/v<version>/: the five files a reader
downloads, and latest.json, the update manifest every installed app
reads. Then it prints the two presses that publish and runs neither of
them: every press that reaches outward is the owner's.

The manifest carries a signature for the stamped installer and one for
the AppImage, made with the owner's update key (PLAN-0021 phase 3,
prescription 4). The key file is radialrig-updater.key in the .tauri
folder of the owner's profile, outside every repository, and its
password is the generic credential radialrig-updater-key in Windows
Credential Manager. The password reaches the signer's environment and
nothing else: no command line, no printed line, and not this script's
own environment.

The Linux bundles are not built here. This machine runs Windows, and
PLAN-0009 phase 4 prescription 2 puts their build inside a
ubuntu:22.04 container in continuous integration, where the glibc
floor is set by the container and not by whatever built them. Run the
release-linux workflow, download its linux-bundles artifact, and pass
the unpacked directory to --linux. The digests recorded there are
checked against the bytes before anything is assembled, and so is
built-from.json, the workflow's record of the commit it built from and
the engine pin that commit carries. Both must be this tree's, or the
bundles are refused (PLAN-0022 phase 1).

PLAN-0008 phase 5 unit 5, V1 and V2. Nothing under scripts/ ships: the
sdist takes src/radialrig, pyproject.toml and LICENSE.

Exit codes follow the engine's vocabulary: 0 assembled, 3 a refusal that
built nothing, 1 a build or environment failure.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

PYPROJECT = ROOT / "pyproject.toml"
# Tracked, and it records the package version: the sixth statement.
LOCKFILE = ROOT / "uv.lock"
BOOTSTRAP = ROOT / "app" / "src" / "bootstrap.json"
TAURI_DIR = ROOT / "app" / "src-tauri"
TAURI_CONF = TAURI_DIR / "tauri.conf.json"
TEMPLATE = HERE / "release-notes-template.md"

OK = 0
ERROR = 1
REFUSED = 3

# The one pattern the public address is read against. The pinned config
# stays the single place that address is written, so the release cannot
# be built at an address the shipped app never asks for.
ADDRESS = re.compile(
    r"^radialrig @ https://github\.com/([^/]+)/([^/]+)/releases/download/"
    r"v([^/]+)/radialrig-([^/]+)-py3-none-any\.whl$"
)

# The one shape a bumped version may take: three dot-separated numbers,
# the shape every release so far has worn. Checked before any file is
# rewritten, so a stray flag or a tag name cannot be written into five
# places at once.
VERSION_FORM = re.compile(r"\d+\.\d+\.\d+")

# The one shape a pinned digest may take, which is the shape the shell's
# own hex64 rule accepts: sixty-four lowercase hex digits, no prefix.
DIGEST_FORM = re.compile(r"[0-9a-f]{64}")

# The shape a full commit id takes: git's forty hex digits, or sixty-four
# in a repository on SHA-256. The Linux build's record names one.
COMMIT_FORM = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

# Every program this script is allowed to start. `gh` is deliberately
# not one of them: publishing is the owner's press, not a build step,
# and the guard below is mechanical rather than remembered. `signtool`
# is a name here rather than a path: the door resolves it to the newest
# copy the Windows SDK installed, so a call site stays a literal the
# guard can read.
PROGRAMS = ("git", "uv", "cargo", "signtool")

# git is allowed to read and nothing else. The two subcommands below are
# the two this script needs; every other one, `push` and `commit` among
# them, is refused at the same door `gh` is.
GIT_SUBCOMMANDS = ("rev-parse", "status")

# SignTool reads a signature and never writes one here. The bundler
# signs; this script only checks what the bundler left, so the one
# subcommand below is the whole of what SignTool is allowed to do.
SIGNTOOL_SUBCOMMANDS = ("verify",)

# cargo is narrowed the same way, to the two subcommands this script
# uses: the build, and the signer that signs the update files (PLAN-0021
# phase 3, prescription 4). Every other one is refused at the same door,
# `tauri signer generate` among them, which could write over the owner's
# update key.
CARGO_SUBCOMMANDS = (("tauri", "build"), ("tauri", "signer", "sign"))

# The signer, as the door reads it. It is the one program this script
# hands a secret to, the update key's password, and it takes that
# through its own environment alone.
SIGNER = ("cargo", "tauri", "signer", "sign")

# The sentence every signature refusal ends with. One constant, so a
# missing signature and an unreadable verify send the reader to the same
# two places: the script that signs, and the login it signs through.
SIGN_HELP = (
    "the bundler signs through app/src-tauri/installer/sign.ps1, which "
    "needs an 'az login' that holds the certificate profile signer role"
)

# The two lines `signtool verify /pa /v` states that this script quotes.
# The name is read as the innermost leaf of the signing chain, which is
# the last one before the timestamp line: every "Issued to:" after that
# line belongs to the timestamp authority's own chain, not to the signer.
SIGNER_LINE = re.compile(r"^\s*Issued to:\s*(.+?)\s*$", re.M)
TIMESTAMP_LINE = re.compile(
    r"^\s*The signature is timestamped:\s*(.+?)\s*$", re.M)

# The file the signer name and the timestamp are written to, beside the
# digests. The notes template carries one slot and one only, so a
# per-build fact that changes with every press goes in its own file
# rather than turning the template into a form with three.
SIGNATURE_FILE = "signature.txt"

# The stable name D-39 names beside the two version-stamped ones. It is
# a byte-identical copy of the installer, made by copying rather than
# renaming, so the built artifact stays where cargo left it.
STABLE_NAME = "RadialRig-Setup.exe"

# The checksums file the release-linux workflow writes beside the two
# bundles, in sha256sum's own format: digest, two spaces, name. The
# assembled release writes its own file under the same name, which is
# no clash: that one lives in the output directory and covers all six
# assets.
LINUX_CHECKSUMS = "checksums.txt"

# The record the release-linux workflow writes beside the two bundles
# (PLAN-0022 phase 1): the full commit it built from, and the engine pin
# that commit carries, bootstrap.json's package_sha256. The checksums
# file covers it as it covers the bundles. It is not a release asset:
# it says where the bundles came from, which only the Linux step asks.
LINUX_RECORD = "built-from.json"

# The way out of every refusal that says the bundles are not this
# commit's. An artifact cannot be repaired, only built again.
LINUX_REBUILD = (
    "run the release-linux workflow again on the commit being released "
    "and pass its linux-bundles artifact as --linux"
)

# The update manifest every installed app reads (PLAN-0021 phase 3,
# prescription 4). tauri.conf.json's one endpoint is the public
# repository's latest-release door followed by this name, so it is the
# sixth asset, and the checksums cover it like the other five.
MANIFEST = "latest.json"

# The manifest's two platform keys, in Tauri v2's static format
# `{os}-{arch}`. The updater looks for `{os}-{arch}-{installer}` first
# and then for these (tauri-plugin-updater 2.12.0, src/updater.rs:608-638).
WINDOWS_PLATFORM = "windows-x86_64"
LINUX_PLATFORM = "linux-x86_64"

# Where the owner keeps the update key: this file in the .tauri folder of
# his profile, outside every repository (Decided 11). The folder is read
# when the key is wanted, never at import, so a test can stand in for it.
UPDATE_KEY_NAME = "radialrig-updater.key"

# The Credential Manager entry the key's password sits in, and the line
# the owner runs to put it there. cmdkey asks for the password itself, so
# the password never stands on a command line, not even the owner's.
CREDENTIAL = "radialrig-updater-key"
CMDKEY_LINE = f"cmdkey /generic:{CREDENTIAL} /user:radialrig /pass"

# The one variable the signer reads the password from. It is set in the
# signer's own environment and in no other, this script's included.
PASSWORD_VARIABLE = "TAURI_SIGNING_PRIVATE_KEY_PASSWORD"

# The prefixes of every variable the signer would read a key or a
# password from, today's names and the older ones it still honours
# (tauri-cli 2.11.4, src/signer/sign.rs:16-71). None of them reaches the
# signer from this process's environment, so the key and the password it
# uses are the two this script names.
INHERITED_SIGNING = ("TAURI_SIGNING_", "TAURI_PRIVATE_KEY")

# The two Win32 values the Credential Manager read needs: the type of a
# generic credential, which is what cmdkey /generic writes, and the error
# CredReadW sets when no entry carries the name.
CRED_TYPE_GENERIC = 1
ERROR_NOT_FOUND = 1168

# What stands in printed or returned output where a secret was.
HIDDEN = "[hidden]"

# The template's only slot.
SLOT = "{version}"

# Every command below is one physical line. No line ends with a
# backslash: the owner's shell is PowerShell, where a trailing backslash
# is a stray argument rather than a continuation, and a command split
# that way would be pasted broken.
#
# No step here creates the public repository or pushes to its `main`.
# That `main` changes only through a pull request the owner merges,
# carrying an export of this repository (PLAN-0023).
PRESS = """\
Nothing above reached outward, and this script starts no program that
could. Two presses publish this release, and both are the owner's. Run
each line below from the repository root, one at a time.

The draft release:

  gh release create v{version} --repo {owner}/{repo} --draft --title "RadialRig {version}" --notes-file {out}/notes.md {out}/{stable} {out}/{stamped} {out}/{wheel} {out}/{appimage} {out}/{deb} {out}/{manifest}

The publish edit that makes the pinned address resolve and marks the release Latest, where every installed app reads latest.json:

  gh release edit v{version} --repo {owner}/{repo} --draft=false --latest
"""


class Refusal(RuntimeError):
    """A version, an address or a tree that says do not build this."""


class Failure(RuntimeError):
    """A build step or an environment that could not do its work."""


class Address(NamedTuple):
    """The public address, as bootstrap.json writes it, taken apart."""

    owner: str
    repo: str
    tag_version: str
    wheel_version: str
    source: str


class Signature(NamedTuple):
    """What `signtool verify` says about the installer the build wrote."""

    signer: str
    timestamp: str


# --- reading what is pinned -------------------------------------------

def parse_address(config: Path = BOOTSTRAP) -> Address:
    """The owner, the repository and the two versions the address names.

    Read, never restated. D-39 allows the public repository to be
    renamed, and this parse is what makes the rename reach the release
    build without a second place to edit.
    """
    data = json.loads(config.read_text(encoding="utf-8"))
    source = data.get("package_source", "")
    if not isinstance(source, str):
        raise Refusal(
            f"{config} states a package_source that is not text: "
            f"{source!r}"
        )
    found = ADDRESS.match(source)
    if found is None:
        raise Refusal(
            f"{config} does not name a release address this script can "
            f"build for: {source!r}"
        )
    return Address(found.group(1), found.group(2), found.group(3),
                   found.group(4), source)


def pinned_digest(config: Path = BOOTSTRAP) -> str:
    """The digest the shipped app proves the downloaded wheel against.

    Read the same way the address is read, and refused the same way: a
    missing or misshapen value here would ship an app that either stops
    on every install or checks nothing at all.
    """
    data = json.loads(config.read_text(encoding="utf-8"))
    said = data.get("package_sha256")
    if not isinstance(said, str) or DIGEST_FORM.fullmatch(said) is None:
        raise Refusal(
            f"{config} states no package_sha256 this script can read: "
            f"{said!r}"
        )
    return said


def pinned_engine_min(config: Path = BOOTSTRAP) -> str:
    """The engine version floor the shipped app holds an installed engine to.

    Below it, the app replaces the engine with the pinned wheel when it
    opens (PLAN-0021 phase 2). Read the way
    the digest is read and refused the same way: the shell refuses a
    config whose floor is not three dot-separated numbers, and so does
    this. BUG-0015: the floor sat at 0.1.0 for eleven releases, so the
    screen could never appear and an installed engine never moved.
    """
    data = json.loads(config.read_text(encoding="utf-8"))
    said = data.get("engine_min_version")
    if not isinstance(said, str) or VERSION_FORM.fullmatch(said) is None:
        raise Refusal(
            f"{config} states no engine_min_version this script can read: "
            f"{said!r}"
        )
    return said


def project_version(path: Path = PYPROJECT) -> str:
    """The package's own version. A typed one could disagree with it."""
    found = re.search(r'^version = "([^"]+)"', path.read_text(encoding="utf-8"),
                      re.M)
    if found is None:
        raise Failure(f"{path} names no version")
    return found.group(1)


def shell_version(path: Path = TAURI_CONF) -> str:
    """The version that names the installer file the bundler writes."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if "version" not in data:
        raise Refusal(f"{path} names no version")
    return data["version"]


def locked_version(path: Path = LOCKFILE) -> str:
    """The version uv.lock records for radialrig, the sixth statement.

    The lockfile is tracked and names every package's version; the one
    a bump must move is this package's own, so the read takes the line
    under its [[package]] block and no other. No such block is a
    failure, not a guess.
    """
    found = re.search(r'^name = "radialrig"\nversion = "([^"]+)"',
                      path.read_text(encoding="utf-8"), re.M)
    if found is None:
        raise Failure(f"{path} records no version for radialrig")
    return found.group(1)


def agreed_version(project: str, shell: str, tag: str, wheel: str) -> str:
    """The one version the four places state, or a refusal naming them.

    Pure, so the disagreement it refuses can be tested by passing four
    strings rather than by writing four files.
    """
    stated = {
        "pyproject.toml": project,
        "tauri.conf.json": shell,
        "bootstrap.json tag": tag,
        "bootstrap.json wheel": wheel,
    }
    if len(set(stated.values())) != 1:
        raise Refusal(
            "the four versions disagree: "
            + ", ".join(f"{where} {said}" for where, said in stated.items())
        )
    return project


def asset_names(version: str) -> tuple[str, str, str]:
    """The three Windows assets D-39 names, in the order they list."""
    return (
        STABLE_NAME,
        f"RadialRig_{version}_x64-setup.exe",
        f"radialrig-{version}-py3-none-any.whl",
    )


def linux_asset_names(version: str) -> tuple[str, str]:
    """The two Linux bundles, under the names the bundler gives them.

    PLAN-0009 phase 1 ruled both forms. The names are the bundler's
    own, {productName}_{version}_amd64, restated here because this
    script never runs that bundler: it takes the files from the
    workflow's artifact, and a restated name is what lets a missing or
    renamed bundle refuse here instead of publishing wrong.
    """
    return (
        f"RadialRig_{version}_amd64.AppImage",
        f"RadialRig_{version}_amd64.deb",
    )


def render_notes(version: str, template: Path = TEMPLATE) -> str:
    """The notes, with the one slot filled.

    A replace rather than a format: the body carries paths and a shell
    line, and a brace typed into one of those should not be read as a
    slot this script was asked to fill.
    """
    return template.read_text(encoding="utf-8").replace(SLOT, version)


def digest(path: Path) -> str:
    reader = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            reader.update(block)
    return reader.hexdigest()


# --- writing what is pinned: the bump ---------------------------------

def bump_project_text(text: str, version: str) -> str:
    """pyproject.toml's text with its one version line rewritten.

    The same pattern project_version() reads, so the write cannot land
    on a line the build's own reading would miss. Pure: string in,
    string out, so the rewrite is testable without touching a file.
    """
    if VERSION_FORM.fullmatch(version) is None:
        raise Refusal(f"{version!r} is not three dot-separated numbers")
    rewritten, count = re.subn(r'^version = "[^"]+"',
                               f'version = "{version}"', text, flags=re.M)
    if count != 1:
        raise Refusal(f"pyproject.toml states {count} version lines, not 1")
    return rewritten


def bump_shell_text(text: str, version: str) -> str:
    """tauri.conf.json's text with the version key rewritten.

    A line rewrite rather than a json.dumps, so every other byte of the
    config stays exactly as it was.
    """
    if VERSION_FORM.fullmatch(version) is None:
        raise Refusal(f"{version!r} is not three dot-separated numbers")
    rewritten, count = re.subn(r'^(\s*"version"\s*:\s*")[^"]+(")',
                               rf"\g<1>{version}\g<2>", text, flags=re.M)
    if count != 1:
        raise Refusal(f"tauri.conf.json states {count} version keys, not 1")
    return rewritten


def bump_address_text(text: str, version: str) -> str:
    """bootstrap.json's text with both address versions rewritten.

    Read through the same ADDRESS pattern parse_address() applies, so
    the owner and the repository stay exactly what the pinned address
    names and only the tag and the wheel move.
    """
    if VERSION_FORM.fullmatch(version) is None:
        raise Refusal(f"{version!r} is not three dot-separated numbers")
    source = json.loads(text).get("package_source", "")
    found = ADDRESS.match(source) if isinstance(source, str) else None
    if found is None:
        raise Refusal(
            f"bootstrap.json does not name a release address this script "
            f"can rewrite: {source!r}"
        )
    rewritten = (
        f"radialrig @ https://github.com/{found.group(1)}/{found.group(2)}"
        f"/releases/download/v{version}/radialrig-{version}-py3-none-any.whl"
    )
    if text.count(source) != 1:
        raise Refusal(
            f"bootstrap.json carries the address {text.count(source)} "
            f"times, not once"
        )
    return text.replace(source, rewritten)


def bump_pin_text(text: str, version: str) -> str:
    """bootstrap.json's text with the engine version floor rewritten.

    The same line-rewrite discipline pin_wheel_text keeps: one line,
    every other byte untouched, and a refusal when the line is not there
    exactly once. The floor moves to the version being cut, so an app of
    version N drives no engine below N and replaces any older one it
    finds. A newer engine than the app shipped with is
    unaffected: the shell's check is at-least.
    """
    if VERSION_FORM.fullmatch(version) is None:
        raise Refusal(f"{version!r} is not three dot-separated numbers")
    rewritten, count = re.subn(
        r'^(\s*"engine_min_version"\s*:\s*")[^"]*(")',
        rf"\g<1>{version}\g<2>", text, flags=re.M)
    if count != 1:
        raise Refusal(
            f"bootstrap.json states {count} engine_min_version lines, not 1"
        )
    return rewritten


def pin_wheel_text(text: str, sha: str) -> str:
    """bootstrap.json's text with the wheel digest rewritten.

    A line rewrite beside the other three, so every other byte of the
    pinned config stays exactly as it was. A file with no
    `package_sha256` line is a refusal, not a key this writer adds: the
    shell reads that field on startup, and a config that never carried
    it is a config from before the field existed.
    """
    if DIGEST_FORM.fullmatch(sha) is None:
        raise Refusal(f"{sha!r} is not sixty-four lowercase hex digits")
    rewritten, count = re.subn(r'^(\s*"package_sha256"\s*:\s*")[^"]*(")',
                               rf"\g<1>{sha}\g<2>", text, flags=re.M)
    if count != 1:
        raise Refusal(
            f"bootstrap.json states {count} package_sha256 lines, not 1"
        )
    return rewritten


def pin_wheel() -> str:
    """Build the wheel, write its digest into bootstrap.json, print both.

    The digest the shipped app checks the downloaded wheel against can
    only come from a wheel, so this builds one and reads it. The write
    is re-read through the build's own reader before this returns.
    """
    print("pin-wheel")
    built = build_wheel(project_version())
    sha = digest(built)

    with BOOTSTRAP.open("r", encoding="utf-8", newline="") as reading:
        text = reading.read()
    rewritten = pin_wheel_text(text, sha)
    with BOOTSTRAP.open("w", encoding="utf-8", newline="") as writing:
        writing.write(rewritten)

    read_back = pinned_digest()
    if read_back != sha:
        raise Failure(f"bootstrap.json reads back {read_back}, not {sha}")
    print(f"  {built.name}")
    print(f"  sha256 {sha}")
    print("  app/src/bootstrap.json pins that digest")
    return sha


def lock_project() -> None:
    """Rewrite uv.lock for the version pyproject.toml now states.

    uv is the only writer of its lockfile, so the bump runs it rather
    than editing the file, and reads the result back through
    locked_version like the other five statements.
    """
    run(["uv", "lock"], ROOT)


def stale_lines(text: str, old: str) -> tuple[int, ...]:
    """The 1-based lines that still carry the old version literally.

    The recorded trap: a test that states a version as a literal keeps
    passing under --no-sync after a bump and masks a version-dependent
    failure. Pure, so the reading is testable on a string.
    """
    return tuple(number for number, line in enumerate(text.splitlines(), 1)
                 if old in line)


# What the stale sweep never opens: the directories nobody edits by
# hand, plus the archive and the reports, whose prose names past
# releases by design. A single name is pruned at any depth (the shell's
# target/ sits under app/src-tauri, node_modules under app/); a path is
# pruned where it stands relative to ROOT.
STALE_SWEEP_PRUNE = (".git", ".venv", "dist", "target", "node_modules",
                     "__pycache__", "reports", "docs/archive")


def bump(version: str) -> None:
    """Write one version into the four files the build reads six from.

    Until now each place was edited on its own, with agreed_version()
    only refusing a disagreement at release time. This writes five
    statements in one press, has `uv lock` write the sixth, re-reads
    them all through the same readers the build uses, and then sweeps
    the tree for the old literal, because a stale one fails nothing
    until CI. The fifth statement is the engine floor the app holds an
    installed engine to; the app suite refuses a floor that lags the
    wheel, so a cut that forgets it fails before it opens a pull
    request. The sixth is the lockfile's own record of the package
    version: left behind, it fails the first `uv sync --locked` and,
    under --no-sync, leaves the installed metadata answering the old
    version.

    Then it runs pin-wheel, which builds the wheel out of the tree this
    version was just written into and pins that wheel's digest. So one
    press writes the version and the digest, and the two cannot drift
    apart between one press and the next.
    """
    print("bump")
    old = project_version()
    named: list[Path] = []

    for path, transform in (
        (PYPROJECT, bump_project_text),
        (TAURI_CONF, bump_shell_text),
        (BOOTSTRAP, bump_address_text),
        (BOOTSTRAP, bump_pin_text),
    ):
        # newline="" both ways, so the files keep the line endings they
        # already have on whatever host runs this.
        with path.open("r", encoding="utf-8", newline="") as reading:
            text = reading.read()
        rewritten = transform(text, version)
        with path.open("w", encoding="utf-8", newline="") as writing:
            writing.write(rewritten)
        # bootstrap.json takes two transforms and is named once.
        if path not in named:
            named.append(path)
            print(f"  {path.relative_to(ROOT).as_posix()} now states {version}")

    # The self-check is the build's own reading, not a restatement.
    address = parse_address()
    agreed = agreed_version(project_version(), shell_version(),
                            address.tag_version, address.wheel_version)
    if agreed != version:
        raise Failure(f"the files read back {agreed}, not {version}")
    floor = pinned_engine_min()
    if floor != version:
        raise Failure(f"engine_min_version reads back {floor}, not {version}")

    # The sixth statement is uv's own. Now that pyproject.toml has
    # moved, `uv lock` rewrites the lockfile's radialrig entry, and the
    # read-back is the same discipline as the other five.
    lock_project()
    locked = locked_version()
    if locked != version:
        raise Failure(f"uv.lock reads back {locked}, not {version}")
    print(f"  {LOCKFILE.relative_to(ROOT).as_posix()} now states {version}")
    print(f"  all six statements read back {version}")

    if old == version:
        print(f"  the stale sweep has nothing to find: the version was "
              f"already {old}")
    else:
        # The whole tree, less what STALE_SWEEP_PRUNE names. A warning
        # is all the sweep gives: prose under docs/ that names a past
        # release is accepted noise, and the press goes on.
        warned = False
        for folder, folders, names in os.walk(ROOT):
            here = Path(folder)
            folders[:] = sorted(
                one for one in folders
                if one not in STALE_SWEEP_PRUNE
                and (here / one).relative_to(ROOT).as_posix()
                not in STALE_SWEEP_PRUNE
            )
            for name in sorted(names):
                file = here / name
                try:
                    text = file.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for line in stale_lines(text, old):
                    warned = True
                    print(f"  warning: "
                          f"{file.relative_to(ROOT).as_posix()}"
                          f":{line} still states {old}")
        if not warned:
            print(f"  no stale {old} anywhere under the tree, outside "
                  f"{', '.join(STALE_SWEEP_PRUNE)}")

    # Last, and after the versions are on disk: the digest is taken from
    # a wheel built out of the tree this bump just wrote.
    pin_wheel()


# --- the one place a program starts -----------------------------------

def signtool_path() -> Path:
    """The newest SignTool the Windows SDK left on this machine.

    The same discovery rule `app/src-tauri/installer/sign.ps1` applies,
    kept small and in one place: every version directory under the SDK's
    bin/ is read and the highest one wins, so the tool that reads a
    signature back is the tool that made it.
    """
    base = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    kits = base / "Windows Kits" / "10" / "bin"
    found = []
    for one in kits.glob("*/x64/signtool.exe"):
        parts = one.parent.parent.name.split(".")
        if len(parts) == 4 and all(part.isdigit() for part in parts):
            found.append((tuple(int(part) for part in parts), one))
    if not found:
        raise Failure(
            f"no signtool.exe under {kits}; install the Windows SDK "
            f"signing tools"
        )
    return max(found)[1]


def conceal(text: str, hidden: tuple[str, ...]) -> str:
    """The text with every secret it carries replaced by HIDDEN."""
    for secret in hidden:
        text = text.replace(secret, HIDDEN)
    return text


def run(argv: list[str], cwd: Path, capture: bool = False,
        secret_env: dict[str, str] | None = None) -> str:
    """Start one program, from the short list this script may start.

    Every subprocess in this file comes through here, and a program
    PROGRAMS does not name never starts. That is what keeps the publish
    stage a printed command rather than a step this script could take.
    git is narrowed further, to the two subcommands that only read, so
    this script never commits or pushes. signtool is
    narrowed the same way, to the one subcommand that only reads: this
    script checks a signature and never makes one.

    cargo is narrowed to the build and the update signer (PLAN-0021
    phase 3, prescription 4). `secret_env` is the one way a secret, the
    update key's password, reaches a program: only the signer takes it,
    and only through its own environment, a copy of this one with every
    inherited signing variable dropped. It never goes on argv, which the
    line below prints, and never into os.environ, which every later
    child would inherit. A process given a secret reads no keyboard, and
    what it prints is read whole and the secret replaced before it is
    shown or returned.
    """
    if not argv or argv[0] not in PROGRAMS:
        named = argv[0] if argv else "nothing"
        raise SystemExit(
            f"this script starts {', '.join(PROGRAMS)} and nothing else; "
            f"it will not start {named!r}"
        )
    if argv[0] == "git" and (len(argv) < 2 or argv[1] not in GIT_SUBCOMMANDS):
        asked = argv[1] if len(argv) > 1 else "nothing"
        raise SystemExit(
            f"this script runs git {' and git '.join(GIT_SUBCOMMANDS)} and "
            f"nothing else; it will not run git {asked!r}"
        )
    if argv[0] == "signtool" and (len(argv) < 2
                                  or argv[1] not in SIGNTOOL_SUBCOMMANDS):
        asked = argv[1] if len(argv) > 1 else "nothing"
        raise SystemExit(
            f"this script runs signtool "
            f"{' and signtool '.join(SIGNTOOL_SUBCOMMANDS)} and nothing "
            f"else; it will not run signtool {asked!r}"
        )
    if argv[0] == "cargo" and not any(
            tuple(argv[1:1 + len(sub)]) == sub for sub in CARGO_SUBCOMMANDS):
        asked = " ".join(argv[1:4]) if len(argv) > 1 else "nothing"
        raise SystemExit(
            f"this script runs cargo "
            f"{' and cargo '.join(' '.join(sub) for sub in CARGO_SUBCOMMANDS)}"
            f" and nothing else; it will not run cargo {asked!r}"
        )
    if secret_env and tuple(argv[:len(SIGNER)]) != SIGNER:
        raise SystemExit(
            f"this script hands a secret to {' '.join(SIGNER)} and to "
            f"nothing else"
        )
    print(f"  $ {' '.join(argv)}", flush=True)
    # The name is what the guard reads and what the line above prints;
    # the SDK's own path is what actually starts.
    started = ([str(signtool_path())] + argv[1:] if argv[0] == "signtool"
               else argv)
    env = None
    hidden: tuple[str, ...] = ()
    if secret_env:
        capture = True
        hidden = tuple(value for value in secret_env.values() if value)
        env = {name: value for name, value in os.environ.items()
               if not name.upper().startswith(INHERITED_SIGNING)}
        env.update(secret_env)
    done = subprocess.run(started, cwd=str(cwd), text=True,
                          capture_output=capture, env=env,
                          stdin=subprocess.DEVNULL if secret_env else None)
    # Hidden first and stripped after: stripping first would cut the
    # blank ends off an echoed secret, and the rest would no longer match.
    if done.returncode != 0:
        if capture and done.stderr:
            print(conceal(done.stderr, hidden).rstrip(), file=sys.stderr)
        raise Failure(f"{argv[0]} exited {done.returncode}")
    return conceal(done.stdout or "", hidden).strip() if capture else ""


# --- the update key: where it lives and what opens it ------------------

def update_key_path() -> Path:
    """Where the owner keeps the update key: .tauri in his profile.

    PLAN-0021 phase 3, prescription 4, at the place Decided 11 names.
    Read from USERPROFILE when the key is wanted, the folder Windows
    names the profile, and from the home folder where that is unset.
    Only the place is worked out here; whether a key is there is
    update_key's question.
    """
    profile = os.environ.get("USERPROFILE")
    home = Path(profile) if profile else Path.home()
    return home / ".tauri" / UPDATE_KEY_NAME


def path_within(here: str, there: str, flavour=os.path) -> bool:
    """Whether the path `here` is the folder `there` or anything under it.

    Pure, over two path strings, in the path flavour given: this host's
    by default, ntpath to read Windows paths on any host. Case and
    separators are folded the way the flavour folds them. On Windows the
    extended-length prefix is taken off both sides first, `\\\\?\\UNC\\`
    back to the `\\\\` it stands for and `\\\\?\\` to nothing, because
    resolve() keeps it and commonpath reads a prefixed path and a plain
    one as two drives (PLAN-0021 phase 3, review fix 1).
    """
    def plain(text: str) -> str:
        folded = flavour.normcase(text)
        if flavour.sep == "\\":
            if folded.startswith("\\\\?\\unc\\"):
                return "\\\\" + folded[len("\\\\?\\unc\\"):]
            if folded.startswith("\\\\?\\"):
                return folded[len("\\\\?\\"):]
        return folded

    here, there = plain(here), plain(there)
    try:
        return flavour.commonpath([here, there]) == there
    except ValueError:
        # Two drives share no path, so neither lies inside the other.
        return False


def lies_inside(path: Path, folder: Path) -> bool:
    """Whether a path, resolved, is the folder or anything under it.

    Resolved first, so a link that points into the folder counts, then
    compared by path_within, which folds case and the extended-length
    prefix the way Windows reads them.
    """
    return path_within(str(path.resolve()), str(folder.resolve()))


def update_key(key: Path | None = None) -> Path:
    """The update key file, or a refusal that says why it cannot be used.

    PLAN-0021 phase 3, prescription 4. A key path that resolves inside
    this repository is refused whatever is there, because a commit could
    publish the key: that is Invariant 4's first control. A key that is
    not there is refused with where to put it back from, since a new key
    would leave every installed app unable to take an update (Verified
    14). The file is never opened here; the signer is its one reader.
    """
    named = update_key_path() if key is None else key
    resolved = named.resolve()
    if lies_inside(resolved, ROOT):
        raise Refusal(
            f"the update key path {named} lies inside this repository, "
            f"where a commit could publish the key; keep the key outside "
            f"every repository"
        )
    if not resolved.is_file():
        raise Refusal(
            f"there is no update key at {named}; put the key file back "
            f"there from its backup, because a new key would leave every "
            f"installed app unable to take an update"
        )
    return resolved


def read_credential(name: str) -> str | None:
    """The password Windows Credential Manager holds under one name, or None.

    PLAN-0021 phase 3, prescription 4. The Win32 CredReadW call through
    ctypes, for a generic credential (type 1), so no dependency enters.
    cmdkey stores the password as UTF-16 text, and that is how it is
    read. No entry under the name reads None; so does any system other
    than Windows, which has no Credential Manager. The caller refuses
    both with the cmdkey line. The value is returned to the caller and
    nowhere else: not printed, not logged, not put in an environment,
    and not carried by any exception raised here.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Credential(ctypes.Structure):
        """CREDENTIALW, the record CredReadW hands back."""

        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.c_void_p),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    cred_read = advapi32.CredReadW
    cred_read.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                          ctypes.POINTER(ctypes.POINTER(Credential)))
    cred_read.restype = wintypes.BOOL
    cred_free = advapi32.CredFree
    cred_free.argtypes = (ctypes.c_void_p,)
    cred_free.restype = None

    found = ctypes.POINTER(Credential)()
    if not cred_read(name, CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
        code = ctypes.get_last_error()
        if code == ERROR_NOT_FOUND:
            return None
        raise Failure(
            f"Credential Manager could not be read for {name} (Windows "
            f"error {code})"
        )
    try:
        size = found.contents.CredentialBlobSize
        blob = (ctypes.string_at(found.contents.CredentialBlob, size)
                if size else b"")
    finally:
        cred_free(found)
    if len(blob) % 2:
        raise Refusal(
            f"Credential Manager holds something under {name} that cmdkey "
            f"did not write; store the password again with: {CMDKEY_LINE}"
        )
    try:
        return blob.decode("utf-16-le").rstrip("\x00")
    except UnicodeDecodeError:
        # Raised from None, so the undecodable bytes travel with no error.
        raise Refusal(
            f"Credential Manager holds something under {name} that is not "
            f"text; store the password again with: {CMDKEY_LINE}"
        ) from None


def update_password(reader: Callable[[str], str | None] | None = None) -> str:
    """The update key's password, or a refusal that names the cmdkey line.

    PLAN-0021 phase 3, prescription 4. No entry and an empty one are both
    refused: Decided 11 rules the password must not be empty, because an
    empty one leaves the key's copy in cloud storage open to anyone who
    reaches it. A password of blanks alone counts as empty (review fix
    3). Any other password comes back exactly as stored, blanks and all,
    since a trimmed one would not open the key. `reader` stands in for
    read_credential; left out, the real reader is used.
    """
    read = read_credential if reader is None else reader
    said = read(CREDENTIAL)
    if said is None:
        raise Refusal(
            f"Credential Manager holds no generic credential named "
            f"{CREDENTIAL}, so the update key cannot be opened; store its "
            f"password with: {CMDKEY_LINE}"
        )
    if not said.strip():
        raise Refusal(
            f"Credential Manager holds an empty password under {CREDENTIAL}, "
            f"or one of blanks alone; store the update key's password "
            f"with: {CMDKEY_LINE}"
        )
    return said


def signing_ready() -> Path:
    """The update key and its password, asked for before anything builds.

    The signing step asks again when it signs. Asking here as well means
    a release that could not be signed refuses before the wheel and the
    installer take their minutes, so exit 3 keeps meaning a refusal that
    built nothing. The password read here is dropped at once.
    """
    key = update_key()
    update_password()
    return key


# --- the update signature and the key it was made with -----------------

def shipped_pubkey(conf: Path = TAURI_CONF) -> str:
    """The public update key tauri.conf.json ships, as plugins.updater.pubkey.

    Every installed app checks an update's signature against this key, so
    a signature by any other key is one no installed app would take.
    """
    data = json.loads(conf.read_text(encoding="utf-8"))
    plugins = data.get("plugins") if isinstance(data, dict) else None
    updater = plugins.get("updater") if isinstance(plugins, dict) else None
    said = updater.get("pubkey") if isinstance(updater, dict) else None
    if not isinstance(said, str) or not said.strip():
        raise Refusal(f"{conf} ships no plugins.updater.pubkey to check "
                      f"the update signatures against")
    return said.strip()


def minisign_key_id(encoded: str, kind: str) -> str:
    """The key id a Tauri public key or signature carries, as minisign says it.

    PLAN-0021 phase 3, prescription 4. Tauri writes each as base64 of a
    minisign file, so both are decoded rather than guessed at. A public
    key is two lines: a comment, then base64 of 42 bytes, the algorithm
    `Ed`, the eight-byte key id and the key. A signature is four lines: a
    comment, base64 of 74 bytes, the algorithm (`ED` for a signature
    over the file's hash, `Ed` for the older kind), the same eight-byte
    id and the signature, then the trusted comment and the signature
    over it. minisign prints the id as one little-endian number in hex,
    which is how the owner's .pub file names it. The comments are not
    read for the id: nothing signs the first one.

    Every line of a signature must have its shape (review fix 4): the
    third opens "trusted comment:", and the fourth is base64 of exactly
    64 bytes, so a .sig cut short or mangled refuses here. Only the
    shape and the id are read. The signatures themselves are not
    verified here, because the standard library carries no Ed25519;
    every installed app verifies each one before it installs anything.
    """
    lines_wanted, size, algorithms = {
        "public key": (2, 42, (b"Ed",)),
        "signature": (4, 74, (b"Ed", b"ED")),
    }[kind]
    try:
        lines = base64.b64decode(encoded.strip(), validate=True).decode(
            "utf-8").splitlines()
        whole = len(lines) == lines_wanted
        blob = base64.b64decode(lines[1], validate=True) if whole else b""
        tail = (base64.b64decode(lines[3], validate=True)
                if whole and kind == "signature" else b"")
    except ValueError:
        # binascii.Error and UnicodeDecodeError are both ValueErrors.
        lines, blob, tail = [], b"", b""
    if (len(lines) != lines_wanted
            or not lines[0].startswith("untrusted comment:")
            or (kind == "signature"
                and (not lines[2].startswith("trusted comment:")
                     or len(tail) != 64))
            or len(blob) != size or blob[:2] not in algorithms):
        raise Refusal(f"this is not a minisign {kind} as the Tauri signer "
                      f"writes one")
    return blob[2:10][::-1].hex().upper()


def check_key_id(name: str, signature: str, pubkey: str) -> str:
    """The key id a signature carries, refused unless it is the shipped key's.

    PLAN-0021 phase 3, prescription 4. A file signed with any other key
    is a release every installed app would refuse to install, so it
    stops here rather than on a reader's machine.
    """
    signed = minisign_key_id(signature, "signature")
    shipped = minisign_key_id(pubkey, "public key")
    if signed != shipped:
        raise Refusal(
            f"{name} carries a signature by update key {signed}, and "
            f"tauri.conf.json ships update key {shipped}; every installed "
            f"app would refuse this update"
        )
    return signed


def sign_update(file: Path, key: Path | None = None,
                reader: Callable[[str], str | None] | None = None,
                conf: Path | None = None) -> str:
    """Sign one update file with the owner's update key; return the signature.

    PLAN-0021 phase 3, prescription 4, and callable on its own, so a
    proof build is signed through the same step a release is. Every
    refusal comes before the signer starts: the key (update_key), its
    password (update_password) and the public key this repository ships
    (shipped_pubkey). Then `cargo tauri signer sign -f <key> <file>`
    runs with the password in its environment alone, and writes
    <file>.sig beside the file. That text is read, the .sig file is
    taken away so nothing is left beside an asset, and the text's key id
    is checked against the shipped key before it is returned. The text
    returned is what latest.json carries as the signature.

    The signer's trusted comment records a timestamp and the file name,
    and no version (tauri-cli 2.11.4, src/helpers/updater_signature.rs
    lines 132-136). That is why the updater's requireSignedVersion stays
    off: turned on, it would refuse every update this step signs.

    `key`, `reader` and `conf` stand in for the owner's key path, the
    Credential Manager reader and tauri.conf.json; left out, the real
    ones are used.
    """
    key_file = update_key(key)
    password = update_password(reader)
    pubkey = shipped_pubkey(TAURI_CONF if conf is None else conf)
    carrier = file.with_name(file.name + ".sig")
    try:
        run(["cargo", "tauri", "signer", "sign", "-f", str(key_file),
             str(file)], ROOT, capture=True,
            secret_env={PASSWORD_VARIABLE: password})
        if not carrier.is_file():
            raise Failure(f"the signer wrote no {carrier.name}")
        text = carrier.read_text(encoding="utf-8").strip()
    finally:
        carrier.unlink(missing_ok=True)
    signer = check_key_id(file.name, text, pubkey)
    print(f"  {file.name} signed with update key {signer}")
    return text


# --- latest.json: what installed apps read -----------------------------

def asset_url(address: Address, version: str, name: str) -> str:
    """One asset's address on the public release, as the wheel's is built.

    The same shape bootstrap.json's package_source names the wheel by,
    from the same owner and repository, so every address a release
    publishes moves together when the pinned one does.
    """
    return (f"https://github.com/{address.owner}/{address.repo}"
            f"/releases/download/v{version}/{name}")


def release_page(address: Address, version: str) -> str:
    """The public release's own page, which the manifest's notes link."""
    return (f"https://github.com/{address.owner}/{address.repo}"
            f"/releases/tag/v{version}")


def update_manifest(version: str, address: Address, windows_signature: str,
                    linux_signature: str, now: datetime) -> str:
    """latest.json's text: what an installed app reads to find this release.

    PLAN-0021 phase 3, prescription 4. Tauri v2's static manifest, its
    field names as the pinned plugin reads them (tauri-plugin-updater
    2.12.0, src/updater.rs:71-86 and 1460-1506) and as the v2 updater
    documentation's static JSON table lists them: `version`; `notes`, one
    line that links the release page; `pub_date` in RFC 3339; and
    `platforms`, one entry for each of the two keys holding `signature`,
    the .sig file's text, and `url`, the signed asset's address. Pure,
    with the date passed in, so the whole text can be pinned.
    """
    stamped = asset_names(version)[1]
    appimage = linux_asset_names(version)[0]
    manifest = {
        "version": version,
        "notes": (f"What changed in RadialRig {version}: "
                  f"{release_page(address, version)}"),
        "pub_date": now.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "platforms": {
            WINDOWS_PLATFORM: {
                "signature": windows_signature,
                "url": asset_url(address, version, stamped),
            },
            LINUX_PLATFORM: {
                "signature": linux_signature,
                "url": asset_url(address, version, appimage),
            },
        },
    }
    return json.dumps(manifest, indent=2) + "\n"


# --- step 1: preflight -------------------------------------------------

def preflight(allow_dirty: bool) -> tuple[Address, str, str]:
    """The four versions, the tree, the commit, and the update key.

    The update key and its password are asked for here, before any
    build, so a release that could not be signed stops at exit 3 with
    nothing built (PLAN-0021 phase 3, prescription 4).
    """
    print("preflight")
    version = project_version()
    address = parse_address()
    shell = shell_version()

    dirty = run(["git", "status", "--porcelain"], ROOT, capture=True)
    if dirty and not allow_dirty:
        raise Refusal(
            "the tree carries uncommitted changes; commit them or pass "
            "--allow-dirty"
        )
    commit = run(["git", "rev-parse", "HEAD"], ROOT, capture=True)

    agreed_version(version, shell, address.tag_version, address.wheel_version)

    key = signing_ready()
    floor = pinned_engine_min()
    print(f"  version           {version}")
    print(f"  commit            {commit}")
    print(f"  address           {address.owner}/{address.repo}")
    print(f"  engine floor      {floor}  (the app replaces an installed "
          "engine below it when it opens)")
    print(f"  update key        {key}  (its password is in Credential "
          "Manager)")
    if dirty:
        print("  tree              dirty, allowed by --allow-dirty")
    return address, version, commit


# --- step 2: the wheel -------------------------------------------------

def build_wheel(version: str) -> Path:
    """The wheel, under the exact name the pinned address ends with."""
    print("wheel")
    run(["uv", "build", "--wheel", "--out-dir", "dist"], ROOT)
    built = ROOT / "dist" / asset_names(version)[2]
    if not built.is_file():
        raise Failure(f"the build wrote no {built.name}")
    return built


# --- step 3: the installer ---------------------------------------------

def build_installer(version: str) -> Path:
    """The NSIS installer, under the name tauri.conf.json's version gives."""
    print("installer")
    run(["cargo", "tauri", "build", "--bundles", "nsis"], TAURI_DIR)
    built = (TAURI_DIR / "target" / "release" / "bundle" / "nsis"
             / asset_names(version)[1])
    if not built.is_file():
        raise Failure(f"the bundler wrote no {built.name}")
    return built


# --- step 3b: the signature the bundler left on it ---------------------

def read_signature(output: str) -> Signature:
    """The signer and the timestamp, out of `signtool verify /pa /v`.

    Pure, so the reading can be tested against a captured verify rather
    than against a machine. The name is the last "Issued to:" before the
    timestamp line, which is the innermost leaf of the signing chain;
    the lines after that one name the timestamp authority instead.
    """
    stamped = TIMESTAMP_LINE.search(output)
    if stamped is None:
        raise Refusal(
            "signtool verify states no timestamp for this installer, so "
            "the signature dies with the certificate; " + SIGN_HELP
        )
    names = SIGNER_LINE.findall(output[:stamped.start()])
    if not names:
        raise Refusal(
            "signtool verify names no signing certificate for this "
            "installer; " + SIGN_HELP
        )
    return Signature(names[-1], stamped.group(1))


def verify_installer(built: Path) -> Signature:
    """Read the installer's signature back, or refuse to assemble it.

    The bundler signs; this reads what it left. An unsigned installer is
    a refusal here rather than a surprise on a reader's machine, and the
    two lines this returns are what the assembled release quotes beside
    the digests.
    """
    print("signature")
    try:
        said = run(["signtool", "verify", "/pa", "/v", str(built)], ROOT,
                   capture=True)
    except Failure as failed:
        raise Refusal(
            f"{built.name} carries no signature this machine accepts "
            f"({failed}); " + SIGN_HELP
        ) from failed
    signature = read_signature(said)
    print(f"  signed by {signature.signer}")
    print(f"  timestamped {signature.timestamp}")
    return signature


# --- step 4: the two Linux bundles, already built ----------------------

def collect_linux(version: str, source: Path) -> tuple[Path, Path]:
    """The AppImage and the deb, verified as this commit's own build.

    Nothing is built here and nothing is fetched: the owner downloaded
    the artifact, and this step refuses anything that does not match
    what the workflow recorded. A missing checksums file is a refusal
    too, because a directory of unverifiable bytes is not the artifact
    this script was told to expect.

    The digests show the files arrived whole, not where they came from.
    Bundles from another commit at the same version pass them, which is
    how v0.1.7 shipped an AppImage pinned to a wheel it was not released
    with (REL-008). So the workflow's record of its commit and engine
    pin is held to the checksums as well, and both must be this tree's:
    the commit HEAD names and the pin bootstrap.json carries, with
    nothing uncommitted under app/, which is what the bundles are built
    from (PLAN-0022 phase 1). The comparison is by commit, not by
    contents: a release is cut from one master commit, and its Linux
    build is dispatched on that same commit. A record whose commit or
    pin is not in its own hex form is one this step cannot read.
    """
    print("linux bundles")
    if not source.is_dir():
        raise Refusal(f"--linux names no directory at {source}")

    recorded: dict[str, str] = {}
    sidecar = source / LINUX_CHECKSUMS
    if not sidecar.is_file():
        raise Refusal(
            f"{source} carries no {LINUX_CHECKSUMS}; download the whole "
            f"linux-bundles artifact, not the two files alone"
        )
    # sha256sum writes "digest  name" in text mode and "digest *name"
    # in binary mode, and which one depends on the coreutils build that
    # wrote the file. Both readings are accepted; the digest is what is
    # being checked, not the mode marker.
    for line in sidecar.read_text(encoding="utf-8").splitlines():
        if line.strip():
            said, _, name = line.partition(" ")
            name = name.strip().lstrip("*")
            # A digest is sixty-four hex digits; a line holding anything
            # else records none, and its text is never quoted back
            # (PLAN-0022 phase 3).
            if DIGEST_FORM.fullmatch(said.strip()):
                recorded[name] = said.strip()

    found = []
    for name in linux_asset_names(version):
        at = source / name
        if not at.is_file():
            raise Refusal(
                f"{source} carries no {name}; the workflow that built "
                f"this artifact was not at version {version}"
            )
        if name not in recorded:
            raise Refusal(f"{sidecar} records no digest for {name}")
        made = digest(at)
        if made != recorded[name]:
            raise Refusal(
                f"{name} does not match its recorded digest: the file "
                f"reads {made}, the workflow wrote {recorded[name]}"
            )
        print(f"  {name} matches its recorded digest")
        found.append(at)

    at = source / LINUX_RECORD
    if not at.is_file():
        raise Refusal(
            f"{source} carries no {LINUX_RECORD}, the workflow's record of "
            f"the commit it built from, so nothing shows these bundles "
            f"were built from the commit being released; {LINUX_REBUILD}"
        )
    if LINUX_RECORD not in recorded:
        raise Refusal(
            f"{sidecar} records no digest for {LINUX_RECORD}; "
            f"{LINUX_REBUILD}"
        )
    made = digest(at)
    if made != recorded[LINUX_RECORD]:
        raise Refusal(
            f"{LINUX_RECORD} does not match its recorded digest: the file "
            f"reads {made}, the workflow wrote {recorded[LINUX_RECORD]}; "
            f"{LINUX_REBUILD}"
        )
    try:
        said = json.loads(at.read_text(encoding="utf-8"))
    except (ValueError, RecursionError):
        # UnicodeDecodeError and JSONDecodeError are ValueErrors, and so
        # is a number past Python's integer limit.
        said = None
    built = said.get("commit") if isinstance(said, dict) else None
    carried = said.get("package_sha256") if isinstance(said, dict) else None
    # Read as a commit and a digest, never as text to echo: a refusal
    # below quotes them (PLAN-0022 phase 3).
    if (not isinstance(built, str) or COMMIT_FORM.fullmatch(built) is None
            or not isinstance(carried, str)
            or DIGEST_FORM.fullmatch(carried) is None):
        raise Refusal(
            f"{LINUX_RECORD} names no commit and engine pin this script "
            f"can read; {LINUX_REBUILD}"
        )

    commit = run(["git", "rev-parse", "HEAD"], ROOT, capture=True)
    if built != commit:
        raise Refusal(
            f"the Linux bundles were built from commit {built}, and the "
            f"commit being released is {commit}; {LINUX_REBUILD}"
        )
    changed = run(["git", "status", "--porcelain", "--", "app"], ROOT,
                  capture=True)
    if changed:
        paths = [line.split(None, 1)[-1] for line in changed.splitlines()]
        raise Refusal(
            f"uncommitted changes touch app/, which the Linux bundles are "
            f"built from: {', '.join(paths)}; commit them, then run the "
            f"release-linux workflow on the new commit and pass its "
            f"linux-bundles artifact as --linux"
        )
    pin = pinned_digest(BOOTSTRAP)
    if carried != pin:
        raise Refusal(
            f"the Linux bundles carry the engine pin {carried}, and "
            f"app/src/bootstrap.json pins {pin}; {LINUX_REBUILD}"
        )
    print(f"  {LINUX_RECORD} names this commit and its engine pin")
    return found[0], found[1]


# --- steps 5 and 6: assemble, hash, write ------------------------------

def assemble(version: str, wheel: Path, installer: Path,
             linux: tuple[Path, Path], commit: str, signature: Signature,
             address: Address, rebuild: bool = False) -> Path:
    """The six assets, their digests, the notes, and the two side files.

    The signature is printed beside each installer's digest and written
    to signature.txt beside checksums.txt, so the proof report can quote
    the name a reader will see on the Windows dialog without running
    SignTool again.

    PLAN-0021 phase 3, prescription 4: once the files are copied, the
    copies of the stamped installer and of the AppImage are signed with
    the update key, so the bytes signed are the bytes uploaded. The two
    signatures go into latest.json, the sixth asset, which the checksums
    cover like the other five. A signing step that refuses or fails takes
    the folder away again, so no half-assembled release is left for a
    later press to mistake for a whole one.
    """
    print("assemble")
    out = ROOT / "dist" / "release" / f"v{version}"
    if out.exists():
        if not rebuild:
            raise Refusal(
                f"{out} already carries an assembled release; pass "
                f"--rebuild to replace what is in it"
            )
        print("  REBUILD: every file in this folder is replaced, the "
              "assembled bytes and checksums.txt with them.")
        print("  The installer build is not byte-reproducible, so anything "
              "already uploaded from here no longer matches what is written "
              "now.")
        shutil.rmtree(out)
    out.mkdir(parents=True)

    stable, stamped, wheel_name = asset_names(version)
    appimage_name, deb_name = linux_asset_names(version)
    listed = (stable, stamped, wheel_name, appimage_name, deb_name, MANIFEST)
    shutil.copyfile(wheel, out / wheel_name)
    shutil.copyfile(installer, out / stamped)
    shutil.copyfile(installer, out / stable)
    shutil.copyfile(linux[0], out / appimage_name)
    shutil.copyfile(linux[1], out / deb_name)

    try:
        windows_signature = sign_update(out / stamped)
        linux_signature = sign_update(out / appimage_name)
    except (Refusal, Failure):
        shutil.rmtree(out, ignore_errors=True)
        raise
    (out / MANIFEST).write_text(
        update_manifest(version, address, windows_signature, linux_signature,
                        datetime.now(timezone.utc)),
        encoding="utf-8",
        newline="\n",
    )

    digests = {name: digest(out / name) for name in listed}
    if digests[stable] != digests[stamped]:
        raise Failure("the two installer names are not the same bytes")

    # newline="\n" on both: these two files are read on the release page
    # and by whoever checks a digest, so the bytes stay the same whatever
    # host wrote them.
    (out / "notes.md").write_text(render_notes(version), encoding="utf-8",
                                  newline="\n")
    (out / "checksums.txt").write_text(
        "".join(f"{digests[name]}  {name}\n" for name in listed),
        encoding="utf-8",
        newline="\n",
    )
    signed = (stable, stamped)
    (out / SIGNATURE_FILE).write_text(
        "".join(f"{name}\n"
                f"  signed by {signature.signer}\n"
                f"  timestamped {signature.timestamp}\n"
                for name in signed),
        encoding="utf-8",
        newline="\n",
    )

    print(f"  commit {commit}")
    for name in listed:
        size = (out / name).stat().st_size
        print(f"  {name}")
        print(f"    {size} bytes")
        print(f"    sha256 {digests[name]}")
        if name in signed:
            print(f"    signed by {signature.signer}")
            print(f"    timestamped {signature.timestamp}")
    print(f"  notes.md, checksums.txt and {SIGNATURE_FILE} beside them, "
          f"in {out}")
    return out


# --- step 6: stop ------------------------------------------------------

def press(address: Address, version: str, out: Path) -> None:
    """The three commands that publish. This script runs none of them.

    The draft press uploads the six assets, latest.json last (PLAN-0021
    phase 3, prescription 4).
    """
    stable, stamped, wheel_name = asset_names(version)
    appimage_name, deb_name = linux_asset_names(version)
    print()
    print(PRESS.format(
        owner=address.owner, repo=address.repo, version=version,
        out=out.relative_to(ROOT).as_posix(),
        stable=stable, stamped=stamped, wheel=wheel_name,
        appimage=appimage_name, deb=deb_name, manifest=MANIFEST,
    ))


def build_parser() -> argparse.ArgumentParser:
    """The command line, built where a test can read its prose."""
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="Build one release and stop before publishing it.",
    )
    parser.add_argument(
        "--linux", type=Path, metavar="DIR",
        help="the downloaded linux-bundles artifact, unpacked: the "
             "AppImage, the deb and their checksums file",
    )
    parser.add_argument(
        "--allow-dirty", action="store_true",
        help="build from a tree that carries uncommitted changes",
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="replace an already assembled release folder of this version",
    )
    commands = parser.add_subparsers(dest="command")
    writer = commands.add_parser(
        "bump",
        description="Write NEW_VERSION into every place the release "
                    "reads one, run uv lock, then re-read all six "
                    "statements and refuse any disagreement.",
        help="write NEW_VERSION into pyproject.toml, tauri.conf.json, "
             "bootstrap.json's address and engine floor, and uv.lock, "
             "all in one press",
    )
    writer.add_argument(
        "new_version", metavar="NEW_VERSION",
        help="the version to write: three dot-separated numbers",
    )
    commands.add_parser(
        "pin-wheel",
        description="Build the wheel from this tree, take its sha256, "
                    "and write it into bootstrap.json's package_sha256. "
                    "That digest is what the app proves the downloaded "
                    "wheel against before it installs it.",
        help="build the wheel and pin its digest in bootstrap.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "bump":
            bump(args.new_version)
            return OK
        if args.command == "pin-wheel":
            pin_wheel()
            return OK
        if args.linux is None:
            raise Refusal(
                "a release carries the Linux assets beside the Windows "
                "ones; run the release-linux workflow, download its "
                "linux-bundles artifact, and pass the directory as "
                "--linux"
            )
        address, version, commit = preflight(args.allow_dirty)
        linux = collect_linux(version, args.linux)
        wheel = build_wheel(version)
        # The installer embeds the pinned digest, so the wheel beside it
        # must be the wheel that digest is of. A wheel is reproducible
        # only from the same tree on the same host, and this is where
        # that gets checked: a mismatch here would ship an installer
        # whose every install stops on a checksum the release cannot
        # answer.
        made = digest(wheel)
        pinned = pinned_digest()
        if made != pinned:
            raise Refusal(
                f"{wheel.name} reads {made}, and app/src/bootstrap.json "
                f"pins {pinned}; run 'release.py pin-wheel' from this "
                f"tree and build again. The re-pin is a new commit, so "
                f"the Linux bundles must be built again too: run the "
                f"release-linux workflow on that commit and pass its "
                f"linux-bundles artifact as --linux"
            )
        print(f"  sha256 {made} matches the pinned package_sha256")
        installer = build_installer(version)
        # Read back before anything is assembled: an installer nobody
        # signed refuses here, and never reaches the release folder.
        signature = verify_installer(installer)
        # The update signatures are made inside the assembly, over the
        # copies it writes, and only after the installer's own signature
        # has read back above (PLAN-0021 phase 3, prescription 4).
        out = assemble(version, wheel, installer, linux, commit, signature,
                       address, args.rebuild)
        press(address, version, out)
    except Refusal as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return REFUSED
    except Failure as failed:
        print(f"error: {failed}", file=sys.stderr)
        return ERROR
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
