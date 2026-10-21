"""Tests for the release surfaces — PLAN-0008 Phase 5, unit 5.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_release_surface.py -q

The files a reader meets outside this repository, and one script that
builds what they describe. The notes template and the README, which is
also the public repository's README since PLAN-0023, are not governed — `governed_files` collects `docs/spec*.md` and three
`docs/` subtrees, and a download page is neither a decision nor a plan,
so frontmatter there would put governance metadata in front of a reader.
This file is the hold instead, the answer D14 and D15 gave for the
shell's strings: a pytest sweep over the sentences, plus the mechanical
pins that keep the notes' warnings ahead of the file to click and the
build script's hands off the publish.

The rules are imported rather than restated, the way `test_orphan`
imports `FORBIDDEN_WORD` from `test_cli`: the whole-word reading comes
from `test_cli`, the brand sweep and the containment reader from
`test_app_shell`. Nothing here starts a program or reaches a network,
except the one bump test that runs `uv lock` over scratch copies of
pyproject.toml and uv.lock, the way the owner's bump does (PLAN-0017
phase 6), and the Linux-step tests (PLAN-0022 phase 1), which run git
in a throwaway repository of their own. These tests never ship, so a
missing file is a failure, never a skip.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import ntpath
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import pytest

from radialrig import skills
from radialrig.tests.test_app_shell import (  # noqa: F401 - rules, by name
    HALF_BRAND,
    PUFFERY,
    SAFE_WORD,
    SHORT_NAME,
    forbidden_hit,
)
from radialrig.tests.test_cli import FORBIDDEN_WORD

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "release.py"
TEMPLATE = SCRIPTS / "release-notes-template.md"
README = ROOT / "README.md"
BOOTSTRAP = ROOT / "app" / "src" / "bootstrap.json"
TAURI_CONF = ROOT / "app" / "src-tauri" / "tauri.conf.json"
LOCKFILE = ROOT / "uv.lock"

# The endorsement as both release surfaces say it, mid-sentence. The
# engine's own constant is capitalised because it stands alone in a
# masthead; here the phrase closes a sentence, so the literal is written
# out and counted both ways below.
ENDORSEMENT = "a Radial 47 product"

# Prescription 5, made mechanical: six things a reader must have met
# before the paragraph that names the file to click. Two of them are
# literal phrases of the paragraphs they stand for, the download path
# and the removal text, so each marker moves with its text rather than
# outliving it. Without the download one, that paragraph could be
# deleted and every test here would still pass.
MARKERS = (
    "Both are expected.",
    "Three screens from the download to the kept file.",
    "Run anyway",
    "This installer is signed by Patrick Williams,",
    "Smart App Control",
    "the manage page's removal section",
)

# The template's one slot.
SLOT = "version"

# What `signtool verify /pa /v` printed over the installer the signed
# build of 2026-09-03 wrote, kept whole through the two lines the
# release script reads. It is here rather than described because the
# reading turns on the shape: seven "Issued to:" lines, four in the
# signing chain and three more under the timestamp line, and the one
# wanted is the innermost leaf of the first chain. Raw, because the
# path SignTool echoes carries backslashes; the blank first line is
# SignTool's own.
VERIFIED = r"""
Verifying: target\release\bundle\nsis\RadialRig_0.1.6_x64-setup.exe


Signature Index: 0 (Primary Signature)
Hash of file (sha256): 6D2C8B639FD3761F5E67184A43F8447833B8366CE71A6A410FDB9C507AD4277B

Signing Certificate Chain:
    Issued to: Microsoft Identity Verification Root Certificate Authority 2020

    Issued by: Microsoft Identity Verification Root Certificate Authority 2020

    Expires:   Sun Apr 16 11:44:40 2045

    SHA1 hash: F40042E2E5F7E8EF8189FED15519AECE42C3BFA2


        Issued to: Microsoft ID Verified Code Signing PCA 2021

        Issued by: Microsoft Identity Verification Root Certificate Authority 2020

        Expires:   Tue Apr 01 13:15:20 2036

        SHA1 hash: 8E750F459DAF9A79D6370DB747AD2226866AD818


            Issued to: Microsoft ID Verified CS AOC CA 03

            Issued by: Microsoft ID Verified Code Signing PCA 2021

            Expires:   Wed Mar 26 11:11:32 2031

            SHA1 hash: 06F826F5DDBB0A47AFC6BED6549B936461FFA7D0


                Issued to: Sample Signer

                Issued by: Microsoft ID Verified CS AOC CA 03

                Expires:   Sat Sep 05 14:39:30 2026

                SHA1 hash: C02B0DFD2C4704B91320216920A928AD0992D8E7


The signature is timestamped: Thu Sep 03 19:56:59 2026

Timestamp Verified by:
    Issued to: Microsoft Identity Verification Root Certificate Authority 2020

    Issued by: Microsoft Identity Verification Root Certificate Authority 2020

    Expires:   Sun Apr 16 11:44:40 2045

    SHA1 hash: F40042E2E5F7E8EF8189FED15519AECE42C3BFA2


        Issued to: Microsoft Public RSA Timestamping CA 2020

        Issued by: Microsoft Identity Verification Root Certificate Authority 2020

        Expires:   Mon Nov 19 13:42:31 2035

        SHA1 hash: 27F0ABAC2877BA255F62B389B43FF539A0FB598E


            Issued to: Microsoft Public RSA Time Stamping Authority

            Issued by: Microsoft Public RSA Timestamping CA 2020

            Expires:   Thu Oct 22 13:46:55 2026

            SHA1 hash: 9D64791BDBA7AB705D8EEB6BC275951F512BC45C



Successfully verified: target\release\bundle\nsis\RadialRig_0.1.6_x64-setup.exe


Number of files successfully Verified: 1

Number of warnings: 0

Number of errors: 0
"""


def read(path: Path) -> str:
    """One file's text, with the line endings normalised."""
    assert path.is_file(), f"the release surface is missing {path}"
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture(scope="module")
def version() -> str:
    found = re.search(r'^version = "([^"]+)"', read(ROOT / "pyproject.toml"),
                      re.M)
    assert found is not None, "pyproject.toml names no version"
    return found.group(1)


@pytest.fixture(scope="module")
def template() -> str:
    return read(TEMPLATE)


@pytest.fixture(scope="module")
def notes(template, version) -> str:
    """The template as the script renders it, which is what a reader sees."""
    return template.replace("{" + SLOT + "}", version)


@pytest.fixture(scope="module")
def readme() -> str:
    """README.md, the one README both repositories open with."""
    return read(README)


@pytest.fixture(scope="module")
def release():
    """The build script, loaded by path.

    `scripts/` is not a package and nothing under it ships, so the test
    loads the file the owner runs rather than an installed copy of it.
    Importing must do nothing: everything the script does sits behind
    `main`.
    """
    spec = importlib.util.spec_from_file_location(
        "radialrig_release_script", SCRIPT)
    assert spec is not None and spec.loader is not None, SCRIPT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def brand_sweep(where: str, text: str) -> None:
    """The brand's vocabulary, over one surface's whole text.

    The same four rules the shell's own sweep applies: the claim and not
    the comparative, never the short name, never half the endorsed name,
    and none of the three phrases that promise instead of describing.
    """
    found = SAFE_WORD.search(text)
    assert found is None, f"{where} says '{found.group(0) if found else ''}'"
    assert not SHORT_NAME.search(text), f"{where} uses the short name"
    found = HALF_BRAND.search(text)
    assert found is None, f"{where} says 'Radial' without '47'"
    lowered = text.lower()
    for banned in PUFFERY:
        assert banned not in lowered, f"{where} says '{banned}'"


# --- item 4: the template, and its one slot ----------------------------

def test_the_notes_template_carries_one_slot_and_renders_with_no_brace_left(
    template, notes, version,
):
    """One slot, named `version`, and nothing left unfilled after it.

    A second slot would be a second thing the script has to know how to
    fill, and a brace left standing in a rendered release note is a
    reader looking at a template.
    """
    named = frozenset(re.findall(r"\{([^{}]*)\}", template))
    assert named == {SLOT}, named
    assert template.count("{") == template.count("}")

    assert "{" not in notes and "}" not in notes, "a brace survived the render"
    assert version in notes

    # The rendered body names all five assets, under the names the
    # release lists them by.
    for name in (
        "RadialRig-Setup.exe",
        f"RadialRig_{version}_x64-setup.exe",
        f"radialrig-{version}-py3-none-any.whl",
        f"RadialRig_{version}_amd64.AppImage",
        f"RadialRig_{version}_amd64.deb",
    ):
        assert name in notes, name

    # PLAN-0009 phase 4 prescription 4: which Linux versions are
    # supported, and which are not, stated before any file is named.
    support = notes.find("Which Linux versions.")
    assert support > 0, "the notes never state the Linux support bound"
    assert support < notes.lower().find("radialrig_"), (
        "the support bound stands after a file name")
    for named in ("Ubuntu 22.04", "Debian 12", "not supported"):
        assert named in notes, named


# --- item 2: the endorsement, once per surface -------------------------

def test_each_release_surface_says_the_endorsement_once(notes, readme):
    """Once on the notes page, once on the README, and no more.

    The README took the endorsement over from the public repository's
    own README when the two became one (PLAN-0023 phase 1).
    """
    for where, text in (("the notes", notes), ("the README", readme)):
        assert text.count(ENDORSEMENT) == 1, where
        assert len(re.findall(re.escape(ENDORSEMENT), text, re.I)) == 1, where
        assert "Radial 47" in text, where


# --- item 1a: the two release surfaces take the containment sweep ------

def test_the_release_surfaces_take_the_strict_sweep_and_name_no_skill(
    notes, readme, release,
):
    """D-33's containment reading, and the condition that earns it.

    The stricter reach is allowed only where no literal skill name can
    appear: a packaged skill's name is a proper noun, and the "hand"
    inside one of them asserts nothing about who typed a change. So the
    two assertions are paired in one test, strict sweep and no packaged
    skill named, and neither can be kept without the other.

    The script's own printed prose is swept here too. The press block
    and the command line's description and help are text a reader meets,
    the same as the two pages, and neither can name a skill either.
    """
    printed = (
        ("the press block", release.PRESS),
        ("the command line", release.build_parser().format_help()),
    )
    for where, text in (("the notes", notes),
                        ("the README", readme)) + printed:
        hit = forbidden_hit(text)
        assert hit is None, f"{where} says '{hit}'"
        brand_sweep(where, text)

        named = skills.catalog_names()
        assert named, "the catalog is empty, so this pairing proves nothing"
        lowered = text.lower()
        for skill in named:
            assert skill.lower() not in lowered, f"{where} names '{skill}'"

    # And the reader is a reader: the letters anywhere in a word are a hit.
    assert forbidden_hit("a shorthand note") == "hand"


# --- item 1b: README.md takes the whole-word sweep ---------------------

def test_the_repository_readme_takes_the_whole_word_sweep():
    """D-33 as amended, over the file this repository's readers land on.

    Whole words here, not containment, because D-33 lines 46 to 54 rule
    that a document may carry a retired word inside a proper noun or a
    skill name, since a name asserts nothing about who typed a change.
    The brand rules apply in full either way.
    """
    text = read(README)
    hit = FORBIDDEN_WORD.search(text)
    assert hit is None, f"README.md says '{hit.group(0) if hit else ''}'"
    brand_sweep("README.md", text)

    # The whole-word reader is a reader too, and it is the looser one.
    assert FORBIDDEN_WORD.search("changed by hand") is not None
    assert FORBIDDEN_WORD.search("session-handoff") is None

    # The app is named beside the other two surfaces.
    assert "## The desktop app" in text
    assert 'uv tool install "radialrig @' in text
    # BUG-0010: the install replaces executables a running app holds.
    assert (
        "Close the RadialRig app and any manage page first: they hold the "
        "executables the install replaces."
    ) in " ".join(text.split())
    assert "uv tool upgrade radialrig" not in text


# --- item 3: the warnings stand before the file to click ---------------

def test_every_warning_marker_stands_before_the_file_to_click(notes, version):
    """Prescription 5, made mechanical rather than remembered.

    The body sits entirely above GitHub's asset list, so "before the
    download link" already holds by the page's structure. This pins the
    order inside the body: a reader meets both warnings, the reason for
    them, the machine that refuses outright, and where the removals
    live, before the sentence that says which file to run.

    Case is folded, because one marker is a phrase of the removal text
    that happens to open a sentence there.
    """
    lowered = notes.lower()
    click = lowered.find("radialrig-setup.exe")
    assert click > 0, "the notes never name the file to click"

    for marker in MARKERS:
        at = lowered.find(marker.lower())
        assert at >= 0, f"the notes never say {marker!r}"
        assert at < click, f"{marker!r} stands after the file to click"

    # The paragraph that names it is the one that says to run it.
    assert "Download RadialRig-Setup.exe and run it." in notes


# --- item 5: the script reads the address the app ships ----------------

def test_the_script_parses_the_pinned_address_and_names_the_five_assets(
    release, version, tmp_path,
):
    """The address oracle: one parse, four versions, five names.

    The pinned config stays the single place the public address is
    written. If the parse and the three version files ever disagree, the
    release would be built at an address the shipped app never asks for,
    which is the one failure no later test could catch.
    """
    address = release.parse_address(BOOTSTRAP)
    assert address.owner and address.repo
    assert address.tag_version == address.wheel_version == version

    assert release.project_version(ROOT / "pyproject.toml") == version
    assert json.loads(read(TAURI_CONF))["version"] == version

    assert release.asset_names(version) == (
        "RadialRig-Setup.exe",
        f"RadialRig_{version}_x64-setup.exe",
        f"radialrig-{version}-py3-none-any.whl",
    )
    assert release.STABLE_NAME == "RadialRig-Setup.exe"

    # The two Linux names are the bundler's own, restated: the script
    # never runs that bundler, so a drifted name must refuse at
    # assembly rather than publish a file the notes do not describe.
    assert release.linux_asset_names(version) == (
        f"RadialRig_{version}_amd64.AppImage",
        f"RadialRig_{version}_amd64.deb",
    )

    # The notes the script writes are the template this file sweeps, and
    # not a second copy of the same words.
    assert release.TEMPLATE == TEMPLATE
    assert release.render_notes(version, TEMPLATE) == read(TEMPLATE).replace(
        "{version}", version)

    # An address that does not match the one pattern is a refusal, not a
    # guess: a near-miss name is a wheel the pinned URL cannot resolve.
    def tmp_config(source: str) -> Path:
        where = tmp_path / f"bootstrap-{abs(hash(source))}.json"
        where.write_text(json.dumps({"package_source": source}),
                         encoding="utf-8")
        return where

    for said in (
        "radialrig",
        "radialrig @ https://example.com/radialrig-0.1.0-py3-none-any.whl",
        f"radialrig @ http://github.com/o/r/releases/download/v{version}/"
        f"radialrig-{version}-py3-none-any.whl",
        f"radialrig @ https://github.com/o/r/releases/download/v{version}/"
        f"radialrig-{version}.whl",
    ):
        bad = tmp_config(said)
        with pytest.raises(release.Refusal):
            release.parse_address(bad)

    # A package_source that is not text is a refusal too, not a crash:
    # the pinned file is edited by people, and a list or a null there
    # should exit 3 saying so rather than exit 1 with a type error.
    with pytest.raises(release.Refusal):
        release.parse_address(tmp_config(None))


def test_the_four_versions_must_agree_or_the_build_refuses(
    release, version, tmp_path,
):
    """The comparison, as a function that takes the four stated values.

    Four places state this version, and a release built where they
    disagree is a wheel at an address the shipped app never asks for.
    The check is pure, so the disagreement can be shown by passing
    values rather than by writing four files.
    """
    assert release.agreed_version("0.1.0", "0.1.0", "0.1.0", "0.1.0") == "0.1.0"

    for four in (
        ("0.1.0", "0.1.1", "0.1.0", "0.1.0"),
        ("0.2.0", "0.1.0", "0.1.0", "0.1.0"),
        ("0.1.0", "0.1.0", "0.1.0", "0.1.1"),
    ):
        with pytest.raises(release.Refusal):
            release.agreed_version(*four)

    # And the four this repository states do agree, which is the same
    # reading the address oracle above makes from the files.
    address = release.parse_address(BOOTSTRAP)
    assert release.agreed_version(
        version, release.shell_version(TAURI_CONF),
        address.tag_version, address.wheel_version,
    ) == version

    # A shell config with no version is a refusal, not a key error: the
    # same reading the address gets, so both exit 3 saying what is wrong.
    thin = tmp_path / "tauri.conf.json"
    thin.write_text(json.dumps({"productName": "RadialRig"}), encoding="utf-8")
    with pytest.raises(release.Refusal):
        release.shell_version(thin)


# --- item 6: the script never presses outward --------------------------

def test_the_script_starts_four_programs_and_gh_is_not_one_of_them(release):
    """Publishing is the owner's press, and the guard is mechanical.

    The script prints the two publish commands and starts neither of
    them. That is pinned three ways: the allowed list does not name
    `gh`; every program the source starts is on that list; and the one
    place a program starts refuses anything else when it is actually
    called. A text scan alone would be fooled by the printed commands,
    which are text on purpose.

    SignTool is the fourth program, added by PLAN-0011 phase 1: the
    build now signs the installer, and this script reads that signature
    back before it assembles anything, so an unsigned installer refuses
    here rather than reaching a reader's machine.
    """
    assert release.PROGRAMS == ("git", "uv", "cargo", "signtool")
    assert "gh" not in release.PROGRAMS

    # git is narrowed to the two subcommands that only read: this script
    # never commits, clones or pushes.
    assert release.GIT_SUBCOMMANDS == ("rev-parse", "status")
    for taken in ("push", "commit", "clone", "add"):
        assert taken not in release.GIT_SUBCOMMANDS, taken

    # signtool is narrowed the same way, to the one subcommand that only
    # reads. Signing is the bundler's step, through a script this
    # release script never starts.
    assert release.SIGNTOOL_SUBCOMMANDS == ("verify",)
    assert "sign" not in release.SIGNTOOL_SUBCOMMANDS

    source = read(SCRIPT)
    starts = re.findall(r"subprocess\.\w+\(", source)
    assert starts == ["subprocess.run("], starts

    argv_heads = re.findall(r"\brun\(\s*\[\s*\"([^\"]+)\"", source)
    assert argv_heads, "the script starts nothing at all"
    for head in argv_heads:
        assert head in release.PROGRAMS, head

    git_subs = re.findall(r"\brun\(\s*\[\s*\"git\"\s*,\s*\"([^\"]+)\"", source)
    assert git_subs, "the script runs no git at all"
    for sub in git_subs:
        assert sub in release.GIT_SUBCOMMANDS, sub

    # Called with a program off the list, or with a git that writes, the
    # one entry point stops before subprocess sees it.
    with pytest.raises(SystemExit):
        release.run(["gh", "--version"], ROOT)
    with pytest.raises(SystemExit):
        release.run([], ROOT)
    with pytest.raises(SystemExit):
        release.run(["git", "push"], ROOT)
    with pytest.raises(SystemExit):
        release.run(["git"], ROOT)
    with pytest.raises(SystemExit):
        release.run(["signtool", "sign", "somewhere"], ROOT)
    with pytest.raises(SystemExit):
        release.run(["signtool"], ROOT)

    # And the printed stage names both presses, so the owner reads what
    # the script declined to run.
    for press in ("gh release create", "gh release edit"):
        assert press in release.PRESS, press
    assert "--draft" in release.PRESS and "--draft=false" in release.PRESS

    # PLAN-0023 phase 1, prescription 7: no printed step creates the
    # public repository or pushes to its `main`. Its `main` changes only
    # through a pull request the owner merges, carrying an export.
    commands = [line.strip() for line in release.PRESS.splitlines()
                if line.startswith("  ")]
    assert commands and all(c.startswith("gh release ") for c in commands), commands
    for retired in ("gh repo create", "git ", "push", "public-repo-README"):
        assert retired not in release.PRESS, retired
    assert not hasattr(release, "CLONE"), "the public repository's clone is retired"

    # The publish edit marks the release Latest itself. Every installed
    # app reads its update manifest at the latest-release address, and
    # every public tag since v0.1.11 points at one commit with one date,
    # so the date GitHub sorts by cannot pick the newest release.
    publish = [line for line in release.PRESS.splitlines()
               if "gh release edit" in line]
    assert len(publish) == 1, publish
    assert "--draft=false" in publish[0] and "--latest" in publish[0], publish[0]

    # Every printed command is one physical line. The owner's shell is
    # PowerShell, where a trailing backslash is a stray argument rather
    # than a continuation, so a wrapped command would paste broken.
    for line in release.PRESS.splitlines():
        assert not line.rstrip("\n").endswith("\\"), line


# --- PLAN-0011: the signature the press reads back ---------------------

def stand_in_signtool(release, said: str):
    """A stand-in for `signtool verify`, over the file it is handed.

    SignTool itself is not started here. This suite runs on a host that
    may carry no Windows SDK, and it starts no program in any case, so
    the stand-in reads the temporary file the test wrote and answers the
    way SignTool answers: a file with no signature in it exits non-zero,
    which the one door raises as a Failure.
    """
    def runner(argv, cwd, capture=False):
        assert argv[:4] == ["signtool", "verify", "/pa", "/v"], argv
        assert capture is True
        if b"signature" not in Path(argv[4]).read_bytes():
            raise release.Failure("signtool exited 1")
        return said
    return runner


def test_the_press_refuses_an_installer_no_one_signed(
    release, monkeypatch, tmp_path,
):
    """An unsigned installer stops the press, and the refusal says where.

    This is the one failure the release cannot let through: a file
    nobody signed, published under a name that says it was. The refusal
    has to name the script that signs and the login it signs through,
    because those are the two things whoever reads it has to go and fix.
    """
    unsigned = tmp_path / "RadialRig_9.9.9_x64-setup.exe"
    unsigned.write_bytes(b"an installer nobody signed")

    monkeypatch.setattr(release, "run", stand_in_signtool(release, VERIFIED))
    with pytest.raises(release.Refusal) as refused:
        release.verify_installer(unsigned)

    said = str(refused.value)
    assert unsigned.name in said
    assert "sign.ps1" in said, said
    assert "az login" in said, said


def test_the_press_reads_the_signer_and_the_timestamp_back(
    release, monkeypatch, tmp_path, capsys,
):
    """A signed installer carries on, and the two lines come back named.

    The same stand-in, over a file that does carry a signature. What
    comes back is what `assemble` prints beside each installer's digest
    and writes into signature.txt, so a proof report can quote the name
    a reader will see on the Windows dialog.
    """
    signed = tmp_path / "RadialRig_9.9.9_x64-setup.exe"
    signed.write_bytes(b"an installer that carries a signature")

    monkeypatch.setattr(release, "run", stand_in_signtool(release, VERIFIED))
    signature = release.verify_installer(signed)

    assert signature == release.Signature(
        "Sample Signer", "Thu Sep 03 19:56:59 2026")
    printed = capsys.readouterr().out
    assert "Sample Signer" in printed
    assert "Thu Sep 03 19:56:59 2026" in printed
    assert release.SIGNATURE_FILE == "signature.txt"


def test_the_press_verifies_after_the_build_and_before_it_assembles(
    release, monkeypatch, tmp_path, capsys,
):
    """The order in `main`: build, read the signature back, then assemble.

    The three steps are stood in for and their order recorded. A refusal
    from the verify step exits 3 with the assembly never reached, so an
    unsigned installer cannot land in the release folder; a signature
    that reads back is the one `assemble` is handed, in the slot the
    rebuild flag must not slip into.
    """
    wheel = tmp_path / "radialrig-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"the wheel this tree builds")
    installer = tmp_path / "RadialRig_9.9.9_x64-setup.exe"
    installer.write_bytes(b"an installer that carries a signature")
    signed = release.Signature("Sample Signer", "Thu Sep 03 19:56:59 2026")

    seen: list[str] = []
    handed: list[tuple] = []

    def stand_in_build(version):
        seen.append("build")
        return installer

    def stand_in_verify(built):
        seen.append("verify")
        assert built == installer
        raise release.Refusal("unsigned; " + release.SIGN_HELP)

    def stand_in_assemble(version, wheel, installer, linux, commit, signature,
                          address, rebuild=False):
        seen.append("assemble")
        # PLAN-0021 phase 3: the address the update manifest's two asset
        # addresses are built from is the one preflight read.
        assert address == release.parse_address(BOOTSTRAP)
        handed.append((signature, rebuild))
        return tmp_path / "out"

    monkeypatch.setattr(
        release, "preflight",
        lambda allow_dirty: (release.parse_address(BOOTSTRAP), "9.9.9", "a1b2"))
    monkeypatch.setattr(
        release, "collect_linux",
        lambda version, source: (tmp_path / "one", tmp_path / "two"))
    monkeypatch.setattr(release, "build_wheel", lambda version: wheel)
    monkeypatch.setattr(release, "pinned_digest",
                        lambda: release.digest(wheel))
    monkeypatch.setattr(release, "build_installer", stand_in_build)
    monkeypatch.setattr(release, "verify_installer", stand_in_verify)
    monkeypatch.setattr(release, "assemble", stand_in_assemble)
    monkeypatch.setattr(release, "press", lambda address, version, out: None)

    # The verify refuses: exit 3, and the assembly was never reached.
    assert release.main(["--linux", str(tmp_path)]) == release.REFUSED
    assert seen == ["build", "verify"], seen
    assert "sign.ps1" in capsys.readouterr().err

    # The verify reads a signature back: assemble gets it, and the
    # rebuild flag lands in its own slot.
    seen.clear()

    def stand_in_verify_signed(built):
        seen.append("verify")
        return signed

    monkeypatch.setattr(release, "verify_installer", stand_in_verify_signed)
    assert release.main(["--linux", str(tmp_path), "--rebuild"]) == release.OK
    assert seen == ["build", "verify", "assemble"], seen
    assert handed == [(signed, True)], handed


def test_the_verify_reading_takes_the_leaf_and_not_the_timestamp_chain(
    release,
):
    """The parser, over a verify this machine really printed.

    Three certificates stand above the signer in the signing chain and
    three more stand under the timestamp line, all of them written
    "Issued to:". The name wanted is the innermost leaf of the first
    chain, which is the last one before the timestamp line. A reader
    that took the first, or the last of the whole text, would name a
    Microsoft certificate authority on the release page.
    """
    signature = release.read_signature(VERIFIED)
    assert signature.signer == "Sample Signer"
    assert signature.timestamp == "Thu Sep 03 19:56:59 2026"

    # The trap this reading avoids, spelled out: the timestamp chain
    # ends with a name of its own, and it is not the signer's.
    assert "Microsoft Public RSA Time Stamping Authority" in VERIFIED
    assert VERIFIED.rindex("Issued to:") > VERIFIED.index(
        "The signature is timestamped:")

    # A verify with no timestamp is a refusal: the certificate lives
    # three days, and only the timestamp keeps the signature good after
    # that. A verify that names no certificate at all is one too.
    for broken in (
        VERIFIED[:VERIFIED.index("The signature is timestamped:")],
        "The signature is timestamped: Thu Sep 03 19:56:59 2026\n",
    ):
        with pytest.raises(release.Refusal) as refused:
            release.read_signature(broken)
        assert "sign.ps1" in str(refused.value)


# --- the bump: one press writes the version everywhere -----------------

def test_the_bump_transforms_rewrite_exactly_what_the_readers_read(
    release, version, tmp_path,
):
    """String in, string out, and the build's own readers as the oracle.

    Each transform mirrors one reader: the rewrite lands on exactly the
    text that reader reads, and everything else in the file survives.
    The files under tmp_path exist only so the readers can be run over
    the transformed text; the transforms themselves touch no path.
    """
    lifted = "9.9.9"
    assert lifted != version, "the test's premise: a version that moves"

    project = release.bump_project_text(read(ROOT / "pyproject.toml"), lifted)
    shell = release.bump_shell_text(read(TAURI_CONF), lifted)
    # The two bootstrap.json rewrites chain, the way bump() applies them.
    boot = release.bump_pin_text(
        release.bump_address_text(read(BOOTSTRAP), lifted), lifted)

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(project, encoding="utf-8")
    tauri = tmp_path / "tauri.conf.json"
    tauri.write_text(shell, encoding="utf-8")
    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(boot, encoding="utf-8")

    address = release.parse_address(bootstrap)
    agreed = release.agreed_version(
        release.project_version(pyproject), release.shell_version(tauri),
        address.tag_version, address.wheel_version,
    )
    assert agreed == lifted
    # BUG-0015: the engine floor moves with the wheel, read back through
    # the reader the bump's own self-check uses.
    assert release.pinned_engine_min(bootstrap) == lifted

    # The address moves only where the version stands in it: the owner
    # and the repository stay what the pinned config names.
    pinned = release.parse_address(BOOTSTRAP)
    assert (address.owner, address.repo) == (pinned.owner, pinned.repo)

    # Nothing else moved: the keys around each rewrite still read.
    assert json.loads(shell)["productName"] == "RadialRig"
    before = json.loads(read(BOOTSTRAP))
    after = json.loads(boot)
    assert after["uv_version"] == before["uv_version"]
    assert after["python_pin"] == before["python_pin"]
    assert after["package_sha256"] == before["package_sha256"]

    # And the old version is gone from every rewritten text.
    assert f'version = "{version}"' not in project
    assert version not in shell
    assert version not in boot


def test_the_bump_transforms_refuse_a_text_their_reader_would_not_read(
    release,
):
    """A rewrite that cannot land exactly once is a refusal, not a guess.

    The shape gate comes first: a version that is not three
    dot-separated numbers is refused before any text is looked at, so a
    stray flag cannot be written into four places at once.
    """
    for bad in ("v9.9.9", "9.9", "9.9.9 ", "nine", ""):
        for transform in (release.bump_project_text, release.bump_shell_text,
                          release.bump_address_text, release.bump_pin_text):
            with pytest.raises(release.Refusal):
                transform("{}", bad)
    assert release.VERSION_FORM.fullmatch("0.1.6")

    # No version line, or two of them, and the project rewrite refuses.
    with pytest.raises(release.Refusal):
        release.bump_project_text('name = "radialrig"\n', "9.9.9")
    with pytest.raises(release.Refusal):
        release.bump_project_text('version = "1.0.0"\nversion = "2.0.0"\n',
                                  "9.9.9")

    # A shell config with no version key refuses the same way.
    with pytest.raises(release.Refusal):
        release.bump_shell_text("{}", "9.9.9")

    # And the address rewrite reads through the same ADDRESS pattern
    # parse_address applies: a near-miss address is a refusal here too.
    for said in (
        {},
        {"package_source": "radialrig"},
        {"package_source": None},
        {"package_source":
         "radialrig @ https://example.com/radialrig-1.0.0-py3-none-any.whl"},
    ):
        with pytest.raises(release.Refusal):
            release.bump_address_text(json.dumps(said), "9.9.9")

    # The floor rewrite lands on one line or refuses: none, or two, and
    # nothing is written. A config from before the field existed is not
    # a config this writer adds the field to.
    with pytest.raises(release.Refusal):
        release.bump_pin_text('{\n  "python_pin": "3.13"\n}\n',
                              "9.9.9")
    with pytest.raises(release.Refusal):
        release.bump_pin_text(
            '{\n  "engine_min_version": "0.1.0",\n'
            '  "engine_min_version": "0.1.1"\n}\n', "9.9.9")
    # And the reader behind the self-check refuses a floor of the wrong
    # shape, the way the shell's own config check does.
    assert release.VERSION_FORM.fullmatch("1.2.3")
    assert release.VERSION_FORM.fullmatch("v1.2.3") is None


def test_the_stale_sweep_reads_the_lines_the_old_version_stands_on(release):
    """The --no-sync trap, made a warning at the bump instead of at CI.

    A test that states a version as a literal keeps passing under
    --no-sync after a bump and masks a version-dependent failure. The
    line reading is pure, so the trap's detector is tested on a string.
    """
    text = 'expects = "0.0.1"\ncount = 2\nurl = "a/v0.0.1/b.whl"\n'
    assert release.stale_lines(text, "0.0.1") == (1, 3)
    assert release.stale_lines(text, "0.0.9") == ()
    assert release.stale_lines("", "0.0.1") == ()


def test_the_command_line_takes_the_bump_beside_the_build(release):
    """The subcommands parse, and the build's own line is unchanged.

    `bump` is a capability, not a press this suite takes: nothing here
    calls it against the repository's own files. The pure transforms
    above are where the writing is proved, and the bump below runs
    against temporary copies.
    """
    args = release.build_parser().parse_args(["bump", "9.9.9"])
    assert args.command == "bump"
    assert args.new_version == "9.9.9"

    args = release.build_parser().parse_args(["pin-wheel"])
    assert args.command == "pin-wheel"

    args = release.build_parser().parse_args(["--linux", "somewhere"])
    assert args.command is None
    assert args.linux == Path("somewhere")


# --- pin-wheel: the digest the app proves the wheel against ------------

def test_the_digest_rewrite_lands_on_the_line_the_shell_reads(
    release, tmp_path,
):
    """String in, string out, and the build's own reader as the oracle.

    The same shape the three version transforms take. The file under
    tmp_path exists only so the reader can be run over the rewritten
    text; the transform itself touches no path.
    """
    sha = "b" * 64
    rewritten = release.pin_wheel_text(read(BOOTSTRAP), sha)

    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(rewritten, encoding="utf-8")
    assert release.pinned_digest(bootstrap) == sha

    # Nothing else moved: the address and the archive pins still read.
    before = json.loads(read(BOOTSTRAP))
    after = json.loads(rewritten)
    assert after["package_source"] == before["package_source"]
    assert after["uv_archives"] == before["uv_archives"]
    assert after["python_pin"] == before["python_pin"]
    assert before["package_sha256"] not in rewritten

    # And what this repository pins is the shape the shell's own rule
    # accepts: sixty-four lowercase hex digits, with no prefix on them.
    shipped = release.pinned_digest(BOOTSTRAP)
    assert release.DIGEST_FORM.fullmatch(shipped)
    assert shipped == shipped.lower()


def test_the_digest_rewrite_refuses_a_file_that_pins_no_digest(
    release, tmp_path,
):
    """A config with no `package_sha256` line is a refusal, not an add.

    The shell reads that field on startup, so a file that never carried
    it is a file from before the field existed. Writing one in would
    make this script the author of a pin instead of the writer of one.
    """
    for bad in ("9.9.9", "sha256:" + "a" * 64, "A" * 64, "a" * 63, ""):
        with pytest.raises(release.Refusal):
            release.pin_wheel_text(read(BOOTSTRAP), bad)

    sha = "c" * 64
    for said in (
        "{}",
        json.dumps({"package_source": "radialrig"}),
        json.dumps({"package_sha256_old": "d" * 64}),
    ):
        with pytest.raises(release.Refusal):
            release.pin_wheel_text(said, sha)

    # The reader refuses the same files, so a config the writer will not
    # touch is a config the build will not read a digest out of either.
    for said in ({}, {"package_sha256": None}, {"package_sha256": "nope"},
                 {"package_sha256": "E" * 64}):
        thin = tmp_path / f"bootstrap-{abs(hash(str(said)))}.json"
        thin.write_text(json.dumps(said), encoding="utf-8")
        with pytest.raises(release.Refusal):
            release.pinned_digest(thin)


def test_the_build_refuses_a_wheel_that_is_not_the_pinned_one(
    release, monkeypatch, tmp_path, capsys,
):
    """The installer must never carry a digest the wheel beside it fails.

    A wheel is reproducible only from the same tree on the same host, so
    the built wheel is compared with the pinned digest before the
    installer that embeds it is built. Every step around the comparison
    is stood in for here: nothing is built, and nothing is published.
    """
    wheel = tmp_path / "radialrig-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"the wheel this tree builds")
    made = release.digest(wheel)

    class Reached(Exception):
        """The installer step, which stands after the comparison."""

    def stand_in_installer(version):
        raise Reached()

    monkeypatch.setattr(
        release, "preflight",
        lambda allow_dirty: (release.parse_address(BOOTSTRAP), "9.9.9", "a1b2"))
    monkeypatch.setattr(
        release, "collect_linux",
        lambda version, source: (tmp_path / "one", tmp_path / "two"))
    monkeypatch.setattr(release, "build_wheel", lambda version: wheel)
    monkeypatch.setattr(release, "build_installer", stand_in_installer)

    # The digest disagrees: the build refuses, and the refusal says
    # which press writes the pin.
    monkeypatch.setattr(release, "pinned_digest", lambda: "0" * 64)
    assert release.main(["--linux", str(tmp_path)]) == release.REFUSED
    said = capsys.readouterr()
    assert "pin-wheel" in said.err
    assert made in said.err
    # And it says the re-pin is a new commit, so the Linux bundles must
    # be built again on it (PLAN-0022 phase 1, prescription 3): v0.1.7
    # was re-pinned and then assembled with the old bundles.
    assert "release-linux" in said.err, said.err

    # The digest agrees: the build carries on to the installer, which is
    # the step that would embed the pin.
    monkeypatch.setattr(release, "pinned_digest", lambda: made)
    with pytest.raises(Reached):
        release.main(["--linux", str(tmp_path)])


def test_the_bump_writes_the_versions_first_and_pins_the_wheel_last(
    release, monkeypatch, tmp_path,
):
    """One press writes the version and the digest, in that order.

    The digest is taken from a wheel built out of the tree, so it can
    only be right once the version is on disk. This runs the real bump
    over temporary copies of the four files and stands in for the lock
    and the wheel build, the two steps that would start a program.
    """
    old = release.project_version(ROOT / "pyproject.toml")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(read(ROOT / "pyproject.toml"), encoding="utf-8")
    tauri = tmp_path / "tauri.conf.json"
    tauri.write_text(read(TAURI_CONF), encoding="utf-8")
    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(read(BOOTSTRAP), encoding="utf-8")
    lock = tmp_path / "uv.lock"
    lock.write_text(read(LOCKFILE), encoding="utf-8")

    # The module's four paths and the readers that carry them as
    # defaults, all pointed at the copies. A default is bound when the
    # function is defined, so the paths alone would not move the readers.
    monkeypatch.setattr(release, "PYPROJECT", pyproject)
    monkeypatch.setattr(release, "TAURI_CONF", tauri)
    monkeypatch.setattr(release, "BOOTSTRAP", bootstrap)
    monkeypatch.setattr(release, "LOCKFILE", lock)
    # The root the printed lines are relative to, and the tree the stale
    # sweep walks, both moved to the copies: the bump names each file it
    # wrote, and a name relative to a root the file is not under would
    # fail on the print rather than on anything this test is about.
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "TAURI_DIR", tmp_path)
    reads_project, reads_shell, reads_address, reads_floor, reads_lock = (
        release.project_version, release.shell_version,
        release.parse_address, release.pinned_engine_min,
        release.locked_version)
    monkeypatch.setattr(release, "project_version",
                        lambda path=pyproject: reads_project(path))
    monkeypatch.setattr(release, "shell_version",
                        lambda path=tauri: reads_shell(path))
    monkeypatch.setattr(release, "parse_address",
                        lambda config=bootstrap: reads_address(config))
    monkeypatch.setattr(release, "pinned_engine_min",
                        lambda config=bootstrap: reads_floor(config))
    monkeypatch.setattr(release, "locked_version",
                        lambda path=lock: reads_lock(path))
    monkeypatch.setattr(release, "lock_project",
                        stand_in_lock(lock, old, "9.9.9"))

    seen: list[tuple[str, str]] = []

    def stand_in_pin_wheel():
        seen.append((read(pyproject), read(bootstrap)))
        return "e" * 64

    monkeypatch.setattr(release, "pin_wheel", stand_in_pin_wheel)

    release.bump("9.9.9")

    # pin-wheel ran once, and by the time it ran every version statement
    # was already written: the wheel it would build is the bumped one.
    assert len(seen) == 1
    project_then, boot_then = seen[0]
    assert 'version = "9.9.9"' in project_then
    assert "/v9.9.9/radialrig-9.9.9-py3-none-any.whl" in boot_then
    assert json.loads(read(tauri))["version"] == "9.9.9"
    # BUG-0015: the engine floor moved in the same press, and it was
    # already on disk when pin-wheel ran.
    assert json.loads(boot_then)["engine_min_version"] == "9.9.9"
    assert reads_floor(bootstrap) == "9.9.9"

    # The address rewrite left the pinned digest exactly where it was;
    # moving it is pin-wheel's work and no other transform's.
    assert (release.pinned_digest(bootstrap)
            == release.pinned_digest(BOOTSTRAP))

    # And nothing reached this repository's own four files.
    assert json.loads(read(TAURI_CONF))["version"] != "9.9.9"
    assert reads_floor(BOOTSTRAP) != "9.9.9"
    assert reads_lock(LOCKFILE) != "9.9.9"


# --- PLAN-0017 phase 6, release-ci-05: the lockfile is the sixth site --

def point_the_bump_at(release, monkeypatch, root: Path) -> dict[str, Path]:
    """Scratch copies of every file the bump writes or reads, in the
    repository's own layout under `root`, and the module pointed at them.

    pyproject.toml and uv.lock at the top, the shell's config under
    app/src-tauri, bootstrap.json under app/src: the same shape as the
    tree, so the stale sweep walking `root` meets the directories the
    old two-directory sweep opened and the ones it never did. Bytes are
    copied as they are, so the copies keep the line endings the tree has.
    """
    copies = {
        "pyproject": root / "pyproject.toml",
        "lock": root / "uv.lock",
        "tauri": root / "app" / "src-tauri" / "tauri.conf.json",
        "bootstrap": root / "app" / "src" / "bootstrap.json",
    }
    for name, source in (
        ("pyproject", ROOT / "pyproject.toml"), ("lock", LOCKFILE),
        ("tauri", TAURI_CONF), ("bootstrap", BOOTSTRAP),
    ):
        copies[name].parent.mkdir(parents=True, exist_ok=True)
        copies[name].write_bytes(source.read_bytes())

    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.setattr(release, "TAURI_DIR", copies["tauri"].parent)
    monkeypatch.setattr(release, "PYPROJECT", copies["pyproject"])
    monkeypatch.setattr(release, "TAURI_CONF", copies["tauri"])
    monkeypatch.setattr(release, "BOOTSTRAP", copies["bootstrap"])
    monkeypatch.setattr(release, "LOCKFILE", copies["lock"])
    # A default is bound when the function is defined, so each reader is
    # re-bound to its copy the way the order test above does it.
    reads_project, reads_shell, reads_address, reads_floor, reads_lock = (
        release.project_version, release.shell_version,
        release.parse_address, release.pinned_engine_min,
        release.locked_version)
    monkeypatch.setattr(release, "project_version",
                        lambda path=copies["pyproject"]: reads_project(path))
    monkeypatch.setattr(release, "shell_version",
                        lambda path=copies["tauri"]: reads_shell(path))
    monkeypatch.setattr(release, "parse_address",
                        lambda config=copies["bootstrap"]: reads_address(config))
    monkeypatch.setattr(release, "pinned_engine_min",
                        lambda config=copies["bootstrap"]: reads_floor(config))
    monkeypatch.setattr(release, "locked_version",
                        lambda path=copies["lock"]: reads_lock(path))
    return copies


def stand_in_lock(lock: Path, old: str, new: str):
    """What `uv lock` does to the one line the sixth reader reads, for
    the tests that prove something other than the lock itself."""
    def lock_project() -> None:
        text = read(lock)
        moved = text.replace(f'name = "radialrig"\nversion = "{old}"',
                             f'name = "radialrig"\nversion = "{new}"')
        assert moved != text, "the stand-in found no radialrig block to move"
        lock.write_text(moved, encoding="utf-8", newline="\n")
    return lock_project


def uv_lock_check(root: Path) -> int:
    """`uv lock --check` over a scratch project: 0 when the lockfile
    agrees with pyproject.toml, 1 when it needs updating."""
    return subprocess.run(["uv", "lock", "--check"], cwd=str(root),
                          capture_output=True, text=True).returncode


def test_the_bump_locks_the_project_and_reads_the_lockfile_back(
    release, monkeypatch, tmp_path, capsys,
):
    """release-ci-05: uv.lock is a tracked version site, so the bump moves it.

    The bump used to write four files and print a clean bill while the
    lockfile still named the old version; the first `uv sync --locked`
    then failed outright. This runs the real bump, and the real `uv
    lock`, over scratch copies of the two files uv reads, with the wheel
    build stood in for. Premise, press, proof: the copies agree before,
    the lockfile follows the bump, and `uv lock --check` exits 0 after.
    """
    copies = point_the_bump_at(release, monkeypatch, tmp_path)
    real_lock = LOCKFILE.read_bytes()
    assert uv_lock_check(tmp_path) == 0, "the premise: the copies agree"

    seen: list[str] = []

    def stand_in_pin_wheel():
        seen.append(read(copies["lock"]))
        return "e" * 64

    monkeypatch.setattr(release, "pin_wheel", stand_in_pin_wheel)

    release.bump("9.9.9")

    # The lockfile followed the bump, and uv itself says so.
    assert uv_lock_check(tmp_path) == 0
    assert release.locked_version(copies["lock"]) == "9.9.9"
    # And it had already moved when pin-wheel ran: the wheel it builds
    # is locked as well as versioned.
    assert len(seen) == 1
    assert 'name = "radialrig"\nversion = "9.9.9"' in seen[0]
    said = capsys.readouterr().out
    assert "uv.lock now states 9.9.9" in said
    assert "all six statements read back 9.9.9" in said
    # The repository's own lockfile never moved.
    assert LOCKFILE.read_bytes() == real_lock


def test_the_bump_refuses_a_lockfile_that_did_not_follow(
    release, monkeypatch, tmp_path,
):
    """The sixth reader refuses, the way the other five do.

    A lock step that leaves the old version behind stops the press
    before pin-wheel, naming the file and the version it read back.
    """
    point_the_bump_at(release, monkeypatch, tmp_path)
    monkeypatch.setattr(release, "lock_project", lambda: None)
    monkeypatch.setattr(
        release, "pin_wheel",
        lambda: pytest.fail("pin-wheel ran after the lockfile disagreed"))

    with pytest.raises(release.Failure) as refused:
        release.bump("9.9.9")
    assert "uv.lock" in str(refused.value)
    assert "9.9.9" in str(refused.value)


def test_the_lockfile_reader_reads_the_radialrig_block_alone(
    release, tmp_path,
):
    """The sixth reader takes the version under the package's own block.

    A lockfile names every package's version; the one that must follow a
    bump is radialrig's. No such block is a failure, not a guess. And
    the repository's own lockfile reads what pyproject.toml states, the
    agreement the bump keeps.
    """
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname = "mcp"\nversion = "2.0.0"\n\n'
        '[[package]]\nname = "radialrig"\nversion = "1.2.3"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    assert release.locked_version(lock) == "1.2.3"

    lock.write_text('[[package]]\nname = "mcp"\nversion = "2.0.0"\n',
                    encoding="utf-8")
    with pytest.raises(release.Failure):
        release.locked_version(lock)

    assert (release.locked_version(LOCKFILE)
            == release.project_version(ROOT / "pyproject.toml"))


def test_the_stale_sweep_walks_the_whole_tree_and_prunes_what_no_one_edits(
    release, monkeypatch, tmp_path, capsys,
):
    """release-ci-05, second half: the sweep opened two directories.

    A stale literal anywhere else, the lockfile included, went unseen.
    Now the sweep walks the tree from ROOT and prunes only what nobody
    edits directly. A literal planted under docs/ or at the top is found;
    one under each pruned directory is not; and a warning is all it is:
    the bump goes on to pin the wheel.
    """
    old = release.project_version(ROOT / "pyproject.toml")
    copies = point_the_bump_at(release, monkeypatch, tmp_path)
    monkeypatch.setattr(release, "lock_project",
                        stand_in_lock(copies["lock"], old, "9.9.9"))
    pinned: list[str] = []
    monkeypatch.setattr(release, "pin_wheel",
                        lambda: pinned.append("e" * 64) or "e" * 64)

    found = (
        "docs/plans/PLAN-0099-a-note.md",
        "README.md",
        "src/radialrig/tests/test_planted.py",
    )
    pruned = (
        ".git/HEAD",
        ".venv/pyvenv.cfg",
        "dist/notes.txt",
        "app/src-tauri/target/release/build.log",
        "app/node_modules/x/package.json",
        "src/radialrig/__pycache__/x.txt",
        "reports/r.md",
        "docs/archive/old.md",
    )
    for rel in found + pruned:
        planted = tmp_path / rel
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text(f'said = "{old}"\n', encoding="utf-8")

    release.bump("9.9.9")

    said = capsys.readouterr().out
    warned = set(re.findall(r"warning: (\S+):\d+ still states", said))
    assert warned == set(found), said
    # The sweep warned and the press went on: pin-wheel ran once.
    assert pinned == ["e" * 64]


# --- PLAN-0021 phase 3, prescription 4: the update files ---------------

# Public material only, made in scratch with a throwaway key pair and
# committed: the throwaway public key, and that key's signature over a
# one-line text file. The private half never left scratch. These two can
# check a signature and can make none.
UPDATER_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "updater"
THROWAWAY_PUB = UPDATER_FIXTURES / "throwaway.key.pub"
THROWAWAY_SIG = UPDATER_FIXTURES / "signed.txt.sig"
THROWAWAY_ID = "FD16091F19107CB6"
# The owner's key id, restated; test_app_shell pins the key itself.
SHIPPED_ID = "A8063EFC34527166"

# A made-up password. It opens no key; it is here to be looked for.
STAND_IN_PASSWORD = "a-stand-in-password-Zq41"

# The line the owner runs to store the password, restated: the refusal
# must name it word for word.
CMDKEY = "cmdkey /generic:radialrig-updater-key /user:radialrig /pass"


def six_assets(version: str) -> tuple[str, ...]:
    """The six assets, restated in the order the draft press uploads them."""
    return (
        "RadialRig-Setup.exe",
        f"RadialRig_{version}_x64-setup.exe",
        f"radialrig-{version}-py3-none-any.whl",
        f"RadialRig_{version}_amd64.AppImage",
        f"RadialRig_{version}_amd64.deb",
        "latest.json",
    )


@pytest.fixture(autouse=True)
def no_real_update_key(release, monkeypatch, tmp_path):
    """No test in this file reaches the owner's update key or its password.

    Constraint 8: the key and its password stay on the owner's computer,
    and a suite run does not so much as look for them. The key's place
    moves into tmp_path, where nothing exists, and the Credential Manager
    reader fails any test that reaches it. A test that needs a key or a
    password stands in its own on top of these.
    """
    monkeypatch.setattr(
        release, "update_key_path",
        lambda: tmp_path / "no-profile" / ".tauri" / "radialrig-updater.key",
        raising=False)

    def reached(name):
        pytest.fail(f"a test reached the real Credential Manager reader "
                    f"for {name}")

    monkeypatch.setattr(release, "read_credential", reached, raising=False)


class StandInSubprocess:
    """`subprocess` as release.py sees it, with `run` stood in.

    Each call's argv and keywords are kept, with what this process's own
    environment held while the call ran. For the signer it writes the
    signature file the real one writes beside the file it signs, so the
    script reads it back the way it reads the real one.
    """

    DEVNULL = subprocess.DEVNULL

    def __init__(self, signature: str = "", returncode: int = 0,
                 stdout: str = "Your file was signed successfully",
                 stderr: str = ""):
        self.signature = signature
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict]] = []
        self.environ_during: list[dict[str, str]] = []

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        self.environ_during.append(dict(os.environ))
        if (self.returncode == 0
                and list(argv[:4]) == ["cargo", "tauri", "signer", "sign"]):
            signed = Path(argv[-1])
            signed.with_name(signed.name + ".sig").write_text(
                self.signature, encoding="utf-8")
        return subprocess.CompletedProcess(
            argv, self.returncode, stdout=self.stdout, stderr=self.stderr)


def stand_in_signing(release, monkeypatch, tmp_path: Path,
                     password: str | None = STAND_IN_PASSWORD,
                     pubkey: str | None = None) -> Path:
    """A stand-in key file, a stand-in reader, and a config to check against.

    The key file is a few bytes that are not a key: the signer that would
    read it is stood in too. The reader answers only for the one entry
    name the script asks for. The config carries the throwaway public
    key unless the test names another.
    """
    key = tmp_path / "profile" / ".tauri" / "radialrig-updater.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(b"a stand-in file, not a key")
    monkeypatch.setattr(release, "update_key_path", lambda: key)
    monkeypatch.setattr(
        release, "read_credential",
        lambda name: password if name == "radialrig-updater-key" else None)
    conf = tmp_path / "tauri.conf.json"
    conf.write_text(json.dumps(
        {"plugins": {"updater": {"pubkey": pubkey or read(THROWAWAY_PUB)}}}),
        encoding="utf-8")
    monkeypatch.setattr(release, "TAURI_CONF", conf)
    return key


def test_the_signing_step_refuses_a_missing_key_a_key_here_and_no_password(
    release, monkeypatch, tmp_path, capsys,
):
    """Prescription 4's three refusals, each exit 3 with its own sentence.

    No key where the owner keeps it. A key path that resolves inside this
    repository, where a commit could publish it. No password in
    Credential Manager, or an empty one, which names the line the owner
    runs. Each stops before the signer starts, both in the signing step
    and in the press, where preflight asks first so that no build runs
    for a release that could not be signed. The key's place and the
    credential reader are stood in; the real ones are never reached.
    """
    installer = tmp_path / "RadialRig_9.9.9_x64-setup.exe"
    installer.write_bytes(b"an installer")
    signer = StandInSubprocess(read(THROWAWAY_SIG))
    monkeypatch.setattr(release, "subprocess", signer)
    key = stand_in_signing(release, monkeypatch, tmp_path)

    def git_alone(argv, cwd, capture=False, **kwargs):
        assert argv[0] == "git", argv
        return "" if argv[1] == "status" else "a1b2c3"

    monkeypatch.setattr(release, "run", git_alone)
    monkeypatch.setattr(
        release, "collect_linux",
        lambda version, source: pytest.fail("the press went past preflight"))

    missing = tmp_path / "elsewhere" / ".tauri" / "radialrig-updater.key"
    # Never written: the refusal reads where the path resolves, not what
    # is there, so nothing lands in the tree.
    inside = ROOT / "app" / "radialrig-updater.key"
    cases = (
        ("missing", lambda: missing, STAND_IN_PASSWORD,
         ("no update key", str(missing))),
        ("inside", lambda: inside, STAND_IN_PASSWORD,
         ("inside this repository", str(inside))),
        ("no password", lambda: key, None,
         ("radialrig-updater-key", CMDKEY)),
        ("empty password", lambda: key, "",
         ("empty password", CMDKEY)),
    )
    for case, where, password, sentence in cases:
        monkeypatch.setattr(release, "update_key_path", where)
        monkeypatch.setattr(release, "read_credential",
                            lambda name, said=password: said)

        with pytest.raises(release.Refusal) as refused:
            release.sign_update(installer)
        for part in sentence:
            assert part in str(refused.value), (case, str(refused.value))

        capsys.readouterr()
        assert (release.main(["--linux", str(tmp_path)])
                == release.REFUSED), case
        said = capsys.readouterr().err
        for part in sentence:
            assert part in said, (case, said)

    assert not inside.exists()
    assert signer.calls == [], "the signer started"


def test_the_password_reaches_the_signer_through_its_environment_alone(
    release, monkeypatch, tmp_path, capsys,
):
    """The password goes into one process's environment and nowhere else.

    Constraint 8. It is not on the signer's command line, not printed,
    not in an exception, and not in this script's own environment, before,
    during or after the signer runs. A signing variable this process
    inherited does not reach the signer either, so the key and password
    it uses are the two the script names. The one door that starts
    programs hands a secret to the signer alone, and starts only the two
    cargo subcommands this script uses.
    """
    installer = tmp_path / "RadialRig_9.9.9_x64-setup.exe"
    installer.write_bytes(b"an installer")
    key = stand_in_signing(release, monkeypatch, tmp_path)
    signer = StandInSubprocess(read(THROWAWAY_SIG))
    monkeypatch.setattr(release, "subprocess", signer)
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", raising=False)
    monkeypatch.setenv("TAURI_SIGNING_PRIVATE_KEY", "an inherited key")
    monkeypatch.setenv("TAURI_PRIVATE_KEY_PASSWORD", "an inherited password")

    said = release.sign_update(installer)
    assert said == read(THROWAWAY_SIG).strip()

    (argv, kwargs), = signer.calls
    assert argv == ["cargo", "tauri", "signer", "sign", "-f", str(key),
                    str(installer)], argv
    assert not any(STAND_IN_PASSWORD in part for part in argv)
    env = kwargs["env"]
    assert env["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"] == STAND_IN_PASSWORD
    assert "TAURI_SIGNING_PRIVATE_KEY" not in env
    assert "TAURI_PRIVATE_KEY_PASSWORD" not in env
    # Nothing waits on a prompt: the signer reads no keyboard.
    assert kwargs["stdin"] == subprocess.DEVNULL
    # This process never held it, not even while the signer ran.
    assert STAND_IN_PASSWORD not in signer.environ_during[0].values()
    assert STAND_IN_PASSWORD not in os.environ.values()
    # The signer's own file is read and taken away.
    assert not installer.with_name(installer.name + ".sig").exists()
    printed = capsys.readouterr()
    assert STAND_IN_PASSWORD not in printed.out + printed.err
    assert "cargo tauri signer sign" in printed.out

    # A signer that fails and echoes what it was given: the echo is
    # hidden in what is printed and in the error raised.
    echoing = StandInSubprocess(
        returncode=1, stdout=f"tried {STAND_IN_PASSWORD}",
        stderr=f"incorrect updater private key password {STAND_IN_PASSWORD}")
    monkeypatch.setattr(release, "subprocess", echoing)
    with pytest.raises(release.Failure) as failed:
        release.sign_update(installer)
    assert STAND_IN_PASSWORD not in str(failed.value)
    printed = capsys.readouterr()
    assert STAND_IN_PASSWORD not in printed.out + printed.err
    assert "incorrect updater private key password" in printed.err
    assert STAND_IN_PASSWORD not in os.environ.values()

    # The door: a secret goes to the signer and to nothing else, and
    # cargo starts only the build and the signer.
    door = StandInSubprocess()
    monkeypatch.setattr(release, "subprocess", door)
    secret = {"TAURI_SIGNING_PRIVATE_KEY_PASSWORD": STAND_IN_PASSWORD}
    for argv in (["git", "status"], ["cargo", "tauri", "build"],
                 ["cargo", "tauri", "signer", "generate"]):
        with pytest.raises(SystemExit) as stopped:
            release.run(argv, ROOT, secret_env=secret)
        assert STAND_IN_PASSWORD not in str(stopped.value)
    for argv in (["cargo"], ["cargo", "publish"], ["cargo", "tauri"],
                 ["cargo", "tauri", "signer", "generate", "-w", "x.key"]):
        with pytest.raises(SystemExit):
            release.run(argv, ROOT)
    assert door.calls == []

    # An ordinary start hands no environment of its own: the child takes
    # this one, which holds no password.
    release.run(["git", "status", "--porcelain"], ROOT, capture=True)
    assert door.calls[-1][1].get("env") is None


def test_a_signature_counts_only_when_its_key_id_is_the_shipped_one(
    release, monkeypatch, tmp_path,
):
    """The key id each signature carries, against the shipped public key.

    Both are minisign formats wrapped in base64, decoded here rather than
    guessed at: the public key's second line is the algorithm, the
    eight-byte key id and the key; the signature's second line is the
    algorithm, the same eight-byte id and the signature. The committed
    throwaway signature matches the committed throwaway public key and
    is refused against the shipped one, and the signing step refuses it
    the same way rather than return it.
    """
    signature = read(THROWAWAY_SIG)
    public = read(THROWAWAY_PUB)
    shipped = release.shipped_pubkey(TAURI_CONF)

    assert release.minisign_key_id(public, "public key") == THROWAWAY_ID
    assert release.minisign_key_id(signature, "signature") == THROWAWAY_ID
    assert release.minisign_key_id(shipped, "public key") == SHIPPED_ID
    # The id the bytes carry is the id the key's own comment names.
    assert base64.b64decode(public).decode("ascii").splitlines()[0].endswith(
        THROWAWAY_ID)

    assert (release.check_key_id("signed.txt", signature, public)
            == THROWAWAY_ID)
    with pytest.raises(release.Refusal) as refused:
        release.check_key_id("signed.txt", signature, shipped)
    said = str(refused.value)
    for part in ("signed.txt", THROWAWAY_ID, SHIPPED_ID):
        assert part in said, said

    # Through the signing step, against the config this repository ships.
    appimage = tmp_path / "RadialRig_9.9.9_amd64.AppImage"
    appimage.write_bytes(b"an appimage")
    stand_in_signing(release, monkeypatch, tmp_path)
    monkeypatch.setattr(release, "TAURI_CONF", TAURI_CONF)
    monkeypatch.setattr(release, "subprocess", StandInSubprocess(signature))
    with pytest.raises(release.Refusal) as refused:
        release.sign_update(appimage)
    assert SHIPPED_ID in str(refused.value)
    assert not appimage.with_name(appimage.name + ".sig").exists()

    # What is not a minisign key or signature is refused, never a crash:
    # the wrong kind, base64 of something else, and no base64 at all.
    def wrapped(text: str) -> str:
        return base64.b64encode(text.encode("ascii")).decode("ascii")

    for bad in ("", "not base64 at all!", wrapped("hello"),
                wrapped("untrusted comment: x\nAAAA\n"), public):
        with pytest.raises(release.Refusal):
            release.minisign_key_id(bad, "signature")
    with pytest.raises(release.Refusal):
        release.minisign_key_id(signature, "public key")

    # A config that ships no public key is refused before anything signs.
    bare = tmp_path / "bare.conf.json"
    bare.write_text(json.dumps({"plugins": {}}), encoding="utf-8")
    with pytest.raises(release.Refusal):
        release.shipped_pubkey(bare)


def test_assemble_signs_the_uploaded_bytes_and_writes_latest_json(
    release, monkeypatch, tmp_path, capsys,
):
    """The update manifest, its two signatures, and the six assets.

    The copies in the release folder are what gets signed, so the bytes
    signed are the bytes uploaded. `latest.json` takes the shape Tauri
    v2's updater reads as a static manifest (tauri-plugin-updater 2.12.0,
    src/updater.rs): version, notes, pub_date in RFC 3339, and one
    platform entry each for windows-x86_64 and linux-x86_64 holding the
    asset's address and its signature. The address is built the way the
    pinned wheel address is. The checksums cover the manifest, and the
    draft press uploads it as the sixth asset. A signing step that
    refuses leaves no half-assembled folder behind.
    """
    monkeypatch.setattr(release, "ROOT", tmp_path)
    version = "9.9.9"
    made = tmp_path / "made"
    made.mkdir()
    wheel = made / f"radialrig-{version}-py3-none-any.whl"
    wheel.write_bytes(b"the wheel")
    installer = made / f"RadialRig_{version}_x64-setup.exe"
    installer.write_bytes(b"the installer the bundler signed")
    appimage = made / f"RadialRig_{version}_amd64.AppImage"
    appimage.write_bytes(b"the appimage")
    deb = made / f"RadialRig_{version}_amd64.deb"
    deb.write_bytes(b"the deb")

    signed: list[tuple[Path, bytes]] = []

    def stand_in_sign(file):
        signed.append((file, file.read_bytes()))
        return "signature-of-" + file.name

    monkeypatch.setattr(release, "sign_update", stand_in_sign)
    address = release.parse_address(BOOTSTRAP)
    authenticode = release.Signature("Sample Signer",
                                     "Thu Sep 03 19:56:59 2026")

    out = release.assemble(version, wheel, installer, (appimage, deb),
                           "a1b2", authenticode, address)

    six = six_assets(version)
    stable, stamped, wheel_name, appimage_name, deb_name, manifest_name = six
    assert release.MANIFEST == manifest_name
    assert signed == [(out / stamped, b"the installer the bundler signed"),
                      (out / appimage_name, b"the appimage")], signed
    assert sorted(one.name for one in out.iterdir()) == sorted(
        six + ("notes.md", "checksums.txt", "signature.txt"))

    raw = (out / manifest_name).read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    manifest = json.loads(raw.decode("utf-8"))
    assert list(manifest) == ["version", "notes", "pub_date", "platforms"]
    assert manifest["version"] == version

    page = (f"https://github.com/{address.owner}/{address.repo}"
            f"/releases/tag/v{version}")
    assert page in manifest["notes"], manifest["notes"]
    assert "\n" not in manifest["notes"]
    assert forbidden_hit(manifest["notes"]) is None
    brand_sweep("the update notes", manifest["notes"])

    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                        manifest["pub_date"]), manifest["pub_date"]
    datetime.strptime(manifest["pub_date"], "%Y-%m-%dT%H:%M:%SZ")

    base = (f"https://github.com/{address.owner}/{address.repo}"
            f"/releases/download/v{version}/")
    assert manifest["platforms"] == {
        "windows-x86_64": {"signature": "signature-of-" + stamped,
                           "url": base + stamped},
        "linux-x86_64": {"signature": "signature-of-" + appimage_name,
                         "url": base + appimage_name},
    }, manifest["platforms"]
    # Built the way the pinned wheel address is: the same helper gives
    # back bootstrap.json's own address for the wheel.
    pinned_wheel = release.asset_names(address.wheel_version)[2]
    assert (release.asset_url(address, address.tag_version, pinned_wheel)
            == address.source.split(" @ ", 1)[1])
    assert release.asset_url(address, version, stamped) == base + stamped

    sums = read(out / "checksums.txt").splitlines()
    assert sums == [f"{release.digest(out / name)}  {name}" for name in six]

    capsys.readouterr()
    release.press(address, version, out)
    printed = capsys.readouterr().out
    create = [line for line in printed.splitlines()
              if "gh release create" in line]
    assert len(create) == 1, printed
    rel = out.relative_to(tmp_path).as_posix()
    named = [part for part in create[0].split() if part.startswith(rel + "/")]
    assert named == [f"{rel}/notes.md"] + [f"{rel}/{name}" for name in six]

    # A refusal from the signing step leaves no folder a later press
    # would mistake for an assembled release.
    def refusing(file):
        raise release.Refusal("no update key at somewhere")

    monkeypatch.setattr(release, "sign_update", refusing)
    with pytest.raises(release.Refusal):
        release.assemble(version, wheel, installer, (appimage, deb), "a1b2",
                         authenticode, address, rebuild=True)
    assert not out.exists()


def test_the_update_manifest_is_one_pure_function_of_what_it_names(release):
    """The manifest's text, from fixed inputs, value for value.

    The date is passed in, so the whole file can be pinned: the field
    order Tauri's own example uses, two-space indent, and one newline at
    the end.
    """
    address = release.Address("owner", "repo", "1.2.3", "1.2.3", "unused")
    text = release.update_manifest(
        "1.2.3", address, "windows-signature", "linux-signature",
        datetime(2026, 10, 3, 12, 30, 5, tzinfo=timezone.utc))
    base = "https://github.com/owner/repo/releases/download/v1.2.3/"
    expected = {
        "version": "1.2.3",
        "notes": ("What changed in RadialRig 1.2.3: "
                  "https://github.com/owner/repo/releases/tag/v1.2.3"),
        "pub_date": "2026-10-03T12:30:05Z",
        "platforms": {
            "windows-x86_64": {"signature": "windows-signature",
                               "url": base + "RadialRig_1.2.3_x64-setup.exe"},
            "linux-x86_64": {"signature": "linux-signature",
                             "url": base + "RadialRig_1.2.3_amd64.AppImage"},
        },
    }
    assert text == json.dumps(expected, indent=2) + "\n"


# --- PLAN-0021 phase 3, prescription 5: how the app takes a new release -

# The two facts prescription 5 has the README and the notes both state,
# as the words that carry them.
UPDATE_FACTS = (
    "each time it opens",
    "asks before it installs",
    "a deb install takes a new release by installing that release's deb",
)


def test_the_readme_and_the_notes_say_how_the_app_takes_a_new_release(
    notes,
):
    """The app checks each time it opens and asks; a deb takes the deb.

    Prescription 5: the README gains a short passage and the notes one
    sentence, and both say the same two things. The app checks the
    public release page each time it opens, on Windows and as the
    AppImage, and asks before it installs a newer release; a deb install
    takes a new release by installing that release's deb. The notes'
    sentence also names latest.json, the sixth file under them, as the
    file the app reads. Both texts pass their sweeps in the tests above.
    """
    readme = " ".join(read(README).split()).lower()
    for said in UPDATE_FACTS:
        assert said in readme, said

    flat = " ".join(notes.split())
    sentences = [one for one in re.split(r"(?<=[.])\s+", flat)
                 if UPDATE_FACTS[0] in one.lower()]
    assert len(sentences) == 1, sentences
    sentence = sentences[0].lower()
    for said in UPDATE_FACTS:
        assert said in sentence, said
    assert "latest.json" in sentence, sentence
    assert "latest.json" in notes and forbidden_hit(sentence) is None


# --- PLAN-0021 phase 3, review fixes to the signing step ---------------

def test_a_key_path_in_the_extended_length_form_still_reads_as_inside(
    release,
):
    """Review fix 1: \\\\?\\ and \\\\?\\UNC\\ paths compare by what they name.

    Windows keeps the extended-length prefix through resolve(), and a
    prefixed path and a plain one share no drive as commonpath reads
    them, so a key inside this repository written that way read as
    outside it. The prefix is taken off both sides before the compare.
    On Windows the real resolver is used, on paths that need not exist;
    on every host the pure compare is run over Windows-shaped strings.
    """
    if sys.platform == "win32":
        extended = "\\\\?\\" + str(ROOT)
        assert release.lies_inside(
            Path(extended + "\\app\\radialrig-updater.key"), ROOT)
        assert release.lies_inside(ROOT / "app", Path(extended))
        assert not release.lies_inside(
            Path("\\\\?\\" + str(ROOT.parent / "elsewhere" / "x.key")), ROOT)
        with pytest.raises(release.Refusal) as refused:
            release.update_key(
                Path(extended + "\\app\\radialrig-updater.key"))
        assert "inside this repository" in str(refused.value)

    root = r"D:\work\Repo"
    share = r"\\server\share\repo"
    inside = (
        (r"\\?\D:\work\Repo\app\x.key", root),
        (r"D:\work\Repo\app\x.key", "\\\\?\\" + root),
        (r"\\?\d:\WORK\repo", root),
        (r"//?/D:/work/Repo/x.key", root),
        (r"\\?\UNC\server\share\repo\x.key", share),
        (r"\\server\share\repo\x.key", r"\\?\UNC\server\share\repo"),
        (r"\\?\unc\SERVER\share\repo", share),
    )
    for here, there in inside:
        assert release.path_within(here, there, ntpath), (here, there)
    outside = (
        (r"\\?\D:\elsewhere\x.key", root),
        (r"\\?\D:\work\Repo-private\x.key", root),
        (r"C:\Users\someone\.tauri\radialrig-updater.key", root),
        (r"\\?\C:\Users\someone\.tauri\radialrig-updater.key", root),
        (r"\\?\UNC\server\other\repo\x.key", share),
        (r"\\?\UNC\server\share\repo-two\x.key", share),
    )
    for here, there in outside:
        assert not release.path_within(here, there, ntpath), (here, there)


def test_a_secret_with_blank_ends_is_hidden_whole(
    release, monkeypatch, tmp_path, capsys,
):
    """Review fix 2: what a process given a secret prints is hidden first.

    Stripping the output before hiding the secret would cut the blank
    ends off an echoed password, and the rest of it would no longer
    match and would show. Hidden first, stripped after, on what is
    returned and on what is printed.
    """
    core = "a-stand-in-password-Zq41"
    secret = " \t" + core + " \t"
    signed = tmp_path / "RadialRig_9.9.9_x64-setup.exe"
    argv = ["cargo", "tauri", "signer", "sign", "-f", str(tmp_path / "k"),
            str(signed)]
    variables = {"TAURI_SIGNING_PRIVATE_KEY_PASSWORD": secret}

    monkeypatch.setattr(release, "subprocess", StandInSubprocess(
        stdout=secret, stderr=""))
    said = release.run(argv, ROOT, secret_env=variables)
    assert core not in said, said
    assert said == release.HIDDEN

    monkeypatch.setattr(release, "subprocess", StandInSubprocess(
        returncode=1, stdout="", stderr="could not open the key with" + secret))
    capsys.readouterr()
    with pytest.raises(release.Failure) as failed:
        release.run(argv, ROOT, secret_env=variables)
    printed = capsys.readouterr()
    assert core not in printed.out + printed.err, printed.err
    assert core not in str(failed.value)


def test_a_password_of_blanks_alone_is_refused_and_a_real_one_kept_whole(
    release,
):
    """Review fix 3: blanks alone are no password (Decided 11).

    A password of spaces or tabs alone is refused with the cmdkey line,
    the way an empty one is. A real password comes back exactly as it is
    stored, blanks and all: trimming it would sign with the wrong one.
    """
    for blank in (" ", "   ", "\t", " \t\n "):
        with pytest.raises(release.Refusal) as refused:
            release.update_password(lambda name, said=blank: said)
        assert CMDKEY in str(refused.value), repr(blank)
    for kept in (" spaced password ", "password\t", "x"):
        assert release.update_password(lambda name, said=kept: said) == kept


def test_a_truncated_or_malformed_signature_is_refused(release):
    """Review fix 4: all four lines of a signature have their shape.

    The trusted comment's line must be there, and the last line, the
    signature over the trusted comment, must be base64 of exactly 64
    bytes. A .sig cut short or mangled refuses here rather than reach
    latest.json. Only the shape is read: the signature itself is checked
    by every installed app, since the standard library has no Ed25519.
    """
    lines = base64.b64decode(read(THROWAWAY_SIG)).decode("ascii").splitlines()
    assert len(lines) == 4, lines

    def wrapped(parts: list[str]) -> str:
        return base64.b64encode("\n".join(parts).encode("ascii")).decode(
            "ascii")

    assert release.minisign_key_id(wrapped(lines), "signature") == THROWAWAY_ID

    def b64(data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")

    for last in ("", "AAAA", b64(b"\0" * 63), b64(b"\0" * 65),
                 "not base64 at all!", lines[3][:-4]):
        with pytest.raises(release.Refusal):
            release.minisign_key_id(wrapped(lines[:3] + [last]), "signature")
    for broken in (
        [lines[0], lines[1], "timestamp:1\tfile:x", lines[3]],
        lines[:3],
        lines[:2],
    ):
        with pytest.raises(release.Refusal):
            release.minisign_key_id(wrapped(broken), "signature")


# --- PLAN-0021 phase 3 review: the version the signed names carry ------

def carries_version(name: str, version: str) -> bool:
    """The app's own reading, restated: `_<version>_` inside the name.

    The installed app takes an update only when the file name in its
    signature's trusted comment carries the version on offer, delimited
    by an underscore on each side (`names_version` in
    app/src-tauri/src/update.rs), so `_0.1.1_` is never read inside
    `_0.1.18_`. The signer writes that name from the file it signs.
    """
    return bool(version) and f"_{version}_" in name


def test_both_files_the_release_signs_carry_their_version_as_the_app_reads_it(
    release, monkeypatch, tmp_path,
):
    """The owner's decision of 2026-10-03, on the release's side.

    tauri-cli 2.11.4 writes no version into a signature, so the app
    reads the version out of the signed file's name instead, which keeps
    a rewritten latest.json from offering an older signed installer
    under a newer number. That holds only while both files `assemble`
    signs carry `_<version>_` in their names: a rename that dropped it
    would have every installed app refuse every update. So the names are
    read off a real `assemble` run, with the signer stood in for, rather
    than off the two naming helpers alone.
    """
    assert carries_version("RadialRig_0.1.18_x64-setup.exe", "0.1.18")
    assert not carries_version("RadialRig_0.1.18_x64-setup.exe", "0.1.1")
    assert not carries_version("RadialRig-0.1.18-x64-setup.exe", "0.1.18")

    monkeypatch.setattr(release, "ROOT", tmp_path)
    version = "0.1.18"
    made = tmp_path / "made"
    made.mkdir()
    wheel = made / f"radialrig-{version}-py3-none-any.whl"
    wheel.write_bytes(b"the wheel")
    installer = made / "built-setup.exe"
    installer.write_bytes(b"the installer")
    appimage = made / "built.AppImage"
    appimage.write_bytes(b"the appimage")
    deb = made / "built.deb"
    deb.write_bytes(b"the deb")

    signed: list[str] = []

    def stand_in_sign(file):
        signed.append(file.name)
        return "signature-of-" + file.name

    monkeypatch.setattr(release, "sign_update", stand_in_sign)
    release.assemble(version, wheel, installer, (appimage, deb), "a1b2",
                     release.Signature("Sample Signer", "a date"),
                     release.parse_address(BOOTSTRAP))

    assert len(signed) == 2, signed
    for name in signed:
        assert carries_version(name, version), name
    assert signed == [release.asset_names(version)[1],
                      release.linux_asset_names(version)[0]], signed


# --- PLAN-0022 phase 1: the Linux bundles name the commit that built them

# The record the release-linux workflow writes beside the two bundles.
# Spelled out here rather than read from the script, so a test reads the
# same name whatever the script under test knows.
BUILT_FROM = "built-from.json"
RELEASE_LINUX = ROOT / ".github" / "workflows" / "release-linux.yml"
TREE_PIN = "1" * 64
OTHER_PIN = "2" * 64


class LinuxTree(NamedTuple):
    repo: Path
    head: str
    older: str


def git(repo: Path, *argv: str) -> str:
    """git in a throwaway repository, with an identity of its own: the
    suite points HOME at scratch, so no global configuration is read."""
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=RadialRig tests",
         "-c", "user.email=tests@example.invalid",
         "-c", "commit.gpgsign=false", *argv],
        capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture
def linux_tree(release, monkeypatch, tmp_path) -> LinuxTree:
    """A throwaway repository the release script reads as its own tree.

    Two commits at one version and one engine pin. The older stands for
    the commit a stale Linux build ran on, the later for the commit
    being released. The script's own git calls run against it as they
    are; only where the script looks is moved.
    """
    repo = tmp_path / "tree"
    bootstrap = repo / "app" / "src" / "bootstrap.json"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_bytes(
        (json.dumps({"package_sha256": TREE_PIN}) + "\n").encode())
    (repo / "app" / "src" / "strings.json").write_bytes(b"{}\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "the commit a Linux build ran on")
    older = git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_bytes(b"a later commit, same version and pin\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "the commit being released")
    head = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(release, "ROOT", repo)
    monkeypatch.setattr(release, "BOOTSTRAP", bootstrap)
    return LinuxTree(repo, head, older)


def linux_artifact(release, where: Path, version: str,
                   record: dict | None) -> Path:
    """What the workflow uploads: the two bundles, the record of the
    commit and pin they were built from, and sha256sum's lines over
    every file it wrote."""
    where.mkdir(parents=True)
    listed = []
    for name in release.linux_asset_names(version):
        (where / name).write_bytes(f"the bytes of {name}".encode())
        listed.append(name)
    if record is not None:
        (where / BUILT_FROM).write_bytes((json.dumps(record) + "\n").encode())
        listed.append(BUILT_FROM)
    (where / "checksums.txt").write_bytes("".join(
        f"{release.digest(where / name)}  {name}\n" for name in listed
    ).encode())
    return where


def refusal_of(release, source: Path) -> str:
    with pytest.raises(release.Refusal) as refused:
        release.collect_linux("9.9.9", source)
    return str(refused.value)


def test_the_linux_step_takes_bundles_built_from_the_commit_being_released(
    release, linux_tree, tmp_path,
):
    """The acceptance case, which held before PLAN-0022 and must hold
    after it: the workflow ran on the commit being released, and that
    commit carries this tree's engine pin."""
    source = linux_artifact(release, tmp_path / "artifact", "9.9.9",
                            {"commit": linux_tree.head,
                             "package_sha256": TREE_PIN})
    appimage, deb = release.collect_linux("9.9.9", source)
    assert (appimage.name, deb.name) == release.linux_asset_names("9.9.9")


def test_the_linux_step_refuses_bundles_built_from_another_commit(
    release, linux_tree, tmp_path,
):
    """REL-008 as the verification found it and as v0.1.7 shipped:
    bundles from an older commit at the same version and the same pin.
    The names and the checksums all agree, so only the commit can tell
    them apart, and the refusal names both commits and the rerun."""
    source = linux_artifact(release, tmp_path / "artifact", "9.9.9",
                            {"commit": linux_tree.older,
                             "package_sha256": TREE_PIN})
    said = refusal_of(release, source)
    assert linux_tree.older in said and linux_tree.head in said, said
    assert "release-linux" in said, said


def test_the_linux_step_refuses_bundles_that_carry_another_engine_pin(
    release, linux_tree, tmp_path,
):
    source = linux_artifact(release, tmp_path / "artifact", "9.9.9",
                            {"commit": linux_tree.head,
                             "package_sha256": OTHER_PIN})
    said = refusal_of(release, source)
    assert OTHER_PIN in said and TREE_PIN in said, said
    assert "release-linux" in said, said


def test_the_linux_step_refuses_an_artifact_with_no_record_of_its_commit(
    release, linux_tree, tmp_path,
):
    """Every artifact before PLAN-0022 is one of these, so a folder
    downloaded for an earlier release refuses here too."""
    source = linux_artifact(release, tmp_path / "artifact", "9.9.9", None)
    said = refusal_of(release, source)
    assert BUILT_FROM in said and "release-linux" in said, said


def test_the_linux_step_refuses_a_record_its_checksums_do_not_vouch_for(
    release, linux_tree, tmp_path,
):
    """The record is held to the checksums the way the bundles are: a
    record rewritten after the workflow took its digest refuses, even
    when it names the right commit and pin, and so does a checksums
    file that lists no line for it."""
    record = {"commit": linux_tree.head, "package_sha256": TREE_PIN}
    source = linux_artifact(release, tmp_path / "artifact", "9.9.9", record)
    (source / BUILT_FROM).write_bytes(json.dumps(record, indent=2).encode())
    said = refusal_of(release, source)
    assert BUILT_FROM in said and "digest" in said, said

    checksums = source / "checksums.txt"
    kept = [line for line in checksums.read_text(encoding="utf-8").splitlines()
            if not line.endswith(BUILT_FROM)]
    checksums.write_bytes(("\n".join(kept) + "\n").encode())
    said = refusal_of(release, source)
    assert BUILT_FROM in said, said


def test_the_linux_step_refuses_while_an_uncommitted_change_touches_app(
    release, linux_tree, tmp_path,
):
    """The bundles are built from app/, so the commit is their source
    only while app/ holds nothing uncommitted. A change elsewhere is
    preflight's to judge, and --allow-dirty's to allow."""
    source = linux_artifact(release, tmp_path / "artifact", "9.9.9",
                            {"commit": linux_tree.head,
                             "package_sha256": TREE_PIN})
    (linux_tree.repo / "README.md").write_bytes(b"changed, outside app/\n")
    release.collect_linux("9.9.9", source)

    changed = linux_tree.repo / "app" / "src" / "strings.json"
    changed.write_bytes(b'{"changed": true}\n')
    said = refusal_of(release, source)
    assert "app/src/strings.json" in said, said
    assert "commit" in said and "release-linux" in said, said

    git(linux_tree.repo, "checkout", "--", "app/src/strings.json")
    (linux_tree.repo / "app" / "src" / "new.json").write_bytes(b"{}\n")
    said = refusal_of(release, source)
    assert "app/src/new.json" in said, said


def test_stale_bundles_stop_the_release_at_the_linux_step_before_any_build(
    release, linux_tree, monkeypatch, tmp_path, capsys,
):
    """The release command's own main, with the real Linux step.

    Preflight is stood in for, because it asks for the update key before
    the Linux step. Every step after the Linux one is stood in too, and
    each records that it was reached. Stale bundles must stop the
    command at the Linux step, with that step's refusal, before any of
    them.
    """
    source = linux_artifact(release, tmp_path / "artifact", "9.9.9",
                            {"commit": linux_tree.older,
                             "package_sha256": TREE_PIN})
    reached: list[str] = []

    def stand_in(step: str):
        def reach(*args, **kwargs):
            reached.append(step)
            raise release.Refusal(f"the stand-in for {step} was reached")
        return reach

    monkeypatch.setattr(
        release, "preflight",
        lambda allow_dirty: (release.parse_address(BOOTSTRAP), "9.9.9",
                             linux_tree.head))
    for step in ("build_wheel", "build_installer", "verify_installer",
                 "assemble", "press"):
        monkeypatch.setattr(release, step, stand_in(step))

    code = release.main(["--linux", str(source)])
    said = capsys.readouterr().err
    assert reached == [], f"stale bundles went past the Linux step: {said}"
    assert code == release.REFUSED, said
    assert linux_tree.older in said and "release-linux" in said, said


def test_the_linux_workflow_records_its_commit_and_pin_beside_the_bundles():
    """Prescription 1, on the writer's side. The collect step writes the
    record the Linux step reads, from the commit it checked out and the
    pin that commit carries, and its checksums cover the record. The
    step runs only in CI, so its text is what this suite can hold; the
    pull request that changes it runs it once (PLAN-0022 phase 3)."""
    text = read(RELEASE_LINUX)
    step = text.split("- name: Collect the assets and their digests", 1)[1]
    step = step.split("- name:", 1)[0]
    assert BUILT_FROM in step, step
    assert "git rev-parse HEAD" in step, step
    assert "package_sha256" in step and "app/src/bootstrap.json" in step, step
    sums = [line for line in step.splitlines() if "sha256sum" in line]
    assert sums and all(BUILT_FROM in line for line in sums), sums


# PLAN-0022 phase 3, from the code review: the record's two values are
# read as a commit and a digest, never as text to echo. A value of any
# other form, or a record nested past the JSON reader's depth, is a
# record this script cannot read.
UNREADABLE = "names no commit and engine pin this script can read"


def with_record_text(release, source: Path, text: str) -> Path:
    """The artifact with its record replaced by `text`, and checksums
    over the new bytes, as a workflow that wrote it would leave them."""
    (source / BUILT_FROM).write_bytes(text.encode("utf-8"))
    listed = [*release.linux_asset_names("9.9.9"), BUILT_FROM]
    (source / "checksums.txt").write_bytes("".join(
        f"{release.digest(source / name)}  {name}\n" for name in listed
    ).encode())
    return source


def reading_of(release, source: Path) -> str:
    """The Linux step's answer, held to a refusal: a traceback is the
    finding, and so is an acceptance."""
    try:
        release.collect_linux("9.9.9", source)
    except release.Refusal as refused:
        return str(refused)
    except Exception as exc:
        pytest.fail(f"the Linux step raised {exc!r} instead of refusing")
    pytest.fail("the Linux step accepted the bundles")


@pytest.mark.parametrize("case", ["commit", "pin", "nested"])
def test_the_linux_step_reads_its_record_as_a_commit_and_a_pin(
    release, linux_tree, tmp_path, case,
):
    record = {"commit": linux_tree.head, "package_sha256": TREE_PIN}
    if case == "commit":
        record["commit"] = linux_tree.head[:12] + "\n\x1b[2J"
    elif case == "pin":
        record["package_sha256"] = "pinned\n\x1b[2J"
    source = linux_artifact(release, tmp_path / "artifact", "9.9.9", record)
    if case == "nested":
        with_record_text(release, source, "[" * 100_000 + "]" * 100_000)

    said = reading_of(release, source)
    assert UNREADABLE in said, said
    assert "\x1b" not in said, said


def test_the_linux_step_reads_a_record_holding_a_number_past_the_limit(
    release, linux_tree, tmp_path,
):
    """From the second reader: Python's integer limit raises a ValueError
    that is not a JSONDecodeError."""
    record = {"commit": linux_tree.head, "package_sha256": TREE_PIN}
    source = linux_artifact(release, tmp_path / "artifact", "9.9.9", record)
    with_record_text(release, source, '{"commit": ' + "1" * 5000 + "}")

    said = reading_of(release, source)
    assert UNREADABLE in said, said


@pytest.mark.parametrize("line", ["bundle", "record"])
def test_a_checksum_that_is_not_a_digest_is_never_echoed(
    release, linux_tree, tmp_path, line,
):
    """From the second reader: the checksums file's digest column was
    quoted in the mismatch refusal as it stood."""
    record = {"commit": linux_tree.head, "package_sha256": TREE_PIN}
    source = linux_artifact(release, tmp_path / "artifact", "9.9.9", record)
    name = (release.linux_asset_names("9.9.9")[0] if line == "bundle"
            else BUILT_FROM)
    checksums = source / "checksums.txt"
    forged = "\x1b[2J\x1b[HFORGED"
    checksums.write_bytes("".join(
        (f"{forged}  {name}\n" if entry.endswith(name) else entry + "\n")
        for entry in checksums.read_text(encoding="utf-8").splitlines()
    ).encode("utf-8"))

    said = reading_of(release, source)
    assert "\x1b" not in said and "FORGED" not in said, said
    assert name in said, said


# --- PLAN-0023 phase 1, prescription 4: one README for both repositories --

def test_one_readme_serves_both_repositories(readme, release):
    """Prescription 4. README.md is published unchanged as the public
    repository's README, so it takes in what the retired
    scripts/public-repo-README.md said: where a release is installed
    from, and how to register the write server when it is not on PATH.
    It also says the public repository is fork-only and how a stranger's
    pull request is answered, and what the MIT license offers and what
    it does not."""
    assert not (SCRIPTS / "public-repo-README.md").exists()
    assert "public-repo-README" not in readme
    flat = " ".join(readme.split())

    address = release.parse_address()
    assert f"https://github.com/{address.owner}/{address.repo}/releases" in flat
    assert "Read the release notes before you download." in flat
    assert "warnings" in flat and "how to remove RadialRig" in flat
    # Phase 4 review, G-01: off PATH, the write server is named by its
    # installed path. A bare `--with radialrig` would fetch whatever
    # package holds that name on PyPI, which D-39 rule 2 warns against.
    assert "register its full path instead" in flat and "`uv tool dir --bin`" in flat
    assert "--with radialrig" not in flat

    assert "fork-only" in flat and "takes no pull requests" in flat
    assert "closed with a pointer to forking" in flat

    assert "MIT license" in flat and "`LICENSE`" in flat
    for named in ("displayed name", "installer's name", "icons", "marks",
                  "the code's own names"):
        assert named in flat, named


# --- PLAN-0023 phase 1, prescription 6: what a fork changes -------------

FORK_SECTION = "## Make the build your own"
SIGNING = ROOT / "app" / "src-tauri" / "installer" / "signing.json"

# Where a value a fork's build carries can sit, as tracked files. The
# research corpus and the packaged skills are prose no build reads a
# value from.
FORK_SCAN_ROOTS = ("app", "scripts", ".github", "src/radialrig")
FORK_SCAN_SKIPS = ("src/radialrig/data/research/", "src/radialrig/data/skills/")
# A workshop-only test module spells the owner's handles to sweep for
# them; the public copy does not carry it.
FORK_SCAN_PRIVATE = ("src/radialrig/tests/test_workshop.py",)

# The files that show the name a user sees: in the window, the
# installer, the release assets and the pages.
NAME_SURFACES = (
    "app/src-tauri/tauri.conf.json",
    "app/src/index.html",
    "app/src/strings.json",
    "app/src-tauri/installer/deb/postrm",
    "scripts/release.py",
    "scripts/release-notes-template.md",
    ".github/workflows/release-linux.yml",
    "src/radialrig/setup_web.py",
    "src/radialrig/manage_web.py",
    "src/radialrig/data/web/favicon/site.webmanifest",
)
MARK_FOLDERS = (
    "app/src-tauri/icons/",
    "app/src/marks/",
    "src/radialrig/data/web/marks/",
    "src/radialrig/data/web/favicon/",
)


TESTS_FOLDER = "src/radialrig/tests/"


def fork_section(readme: str) -> str:
    assert FORK_SECTION in readme, "the README has no section for a fork"
    return readme.split(FORK_SECTION, 1)[1].split("\n## ", 1)[0]


def named_in(section: str, path: str) -> bool:
    """Whether the section names the file. A test may be named by its
    file name alone once the section says which folder holds the tests."""
    if path in section:
        return True
    return (path.startswith(TESTS_FOLDER) and TESTS_FOLDER in section
            and f"`{path[len(TESTS_FOLDER):]}`" in section)


def tracked(*roots: str) -> list[str]:
    """The tracked files under the roots, as posix paths."""
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--", *roots],
        capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return sorted(path for path in listed.split("\0") if path)


def fork_values(release, template: str) -> dict[str, str]:
    """Every value a fork's build would otherwise carry from this
    project, read from the file that holds it, never restated."""
    conf = json.loads(read(TAURI_CONF))
    pins = json.loads(read(BOOTSTRAP))
    signing = json.loads(read(SIGNING))
    address = release.parse_address()
    pubkey = conf["plugins"]["updater"]["pubkey"]
    key_id = base64.b64decode(pubkey).decode("utf-8").splitlines()[0].rsplit(" ", 1)[1]
    signer = re.search(r"signed by ([^,]+),", template)
    assert signer is not None, "the notes name no signer"
    return {
        "the release repository": f"{address.owner}/{address.repo}",
        "the update public key": pubkey,
        "the update key's id": key_id,
        "the engine digest": pins["package_sha256"],
        "the app identifier": conf["identifier"],
        "the publisher": conf["bundle"]["publisher"],
        "the signing endpoint": signing["Endpoint"],
        "the signing account": signing["CodeSigningAccountName"],
        "the certificate profile": signing["CertificateProfileName"],
        "the signer's name": signer.group(1),
        "the update key's file": release.UPDATE_KEY_NAME,
        "the update key's password entry": release.CREDENTIAL,
    }


def fork_scan(value: str) -> list[str]:
    """Each tracked file under the scan roots whose text carries `value`."""
    hits: list[str] = []
    for rel in tracked(*FORK_SCAN_ROOTS):
        if rel.startswith(FORK_SCAN_SKIPS) or rel in FORK_SCAN_PRIVATE:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        if value in text:
            hits.append(rel)
    return hits


def test_the_readme_names_where_each_value_a_fork_changes_lives(
    readme, release, template,
):
    """Prescription 6, built from the code. Each value is read from the
    file that holds it, a scan finds every tracked file that carries it,
    and the README's section for a fork names each of those files, the
    tests that pin a value among them. A file that starts to carry one
    fails here until the README names it."""
    section = fork_section(readme)
    missing: list[str] = []
    for what, value in fork_values(release, template).items():
        hits = fork_scan(value)
        assert hits, f"{what} was found nowhere, so the scan read nothing"
        missing += [f"{what}: {hit}" for hit in hits if not named_in(section, hit)]
    assert not missing, "\n".join(missing)


def test_the_readme_names_the_name_and_the_marks_a_fork_replaces(readme):
    """Prescription 6: the name a user sees, where it shows, and the
    marks, each by its files; and the two steps that change values
    together, the bump and the signing switch."""
    section = fork_section(readme)
    conf = json.loads(read(TAURI_CONF))
    name = conf["productName"]
    for surface in NAME_SURFACES:
        assert name in read(ROOT / surface), f"{surface} no longer shows {name}"
        assert surface in section, surface

    for icon in conf["bundle"]["icon"]:
        assert f"app/src-tauri/{icon}".startswith(MARK_FOLDERS[0]), icon
    marked = {path.rsplit("/", 1)[0] + "/" for path in tracked(*FORK_SCAN_ROOTS)
              if path.endswith(".svg") and "data-mark=" in read(ROOT / path)}
    assert marked and marked <= set(MARK_FOLDERS), marked
    for folder in MARK_FOLDERS:
        assert folder in section, folder

    assert "scripts/release.py bump" in section
    assert "signCommand" in section
