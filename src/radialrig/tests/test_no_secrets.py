"""No tracked file carries a credential.

Run:  uv run --with pytest pytest src/radialrig/tests/test_no_secrets.py -q

PLAN-0021 phase 3 adds Invariant 4's marker: the update key that signs
every release's update files, in its file form and decoded.

The app-security guidance says a rule that must never be broken belongs
in a blocking check, not only in review prose. This suite runs on every
pull request into master, so a test is the check (2026-09-27
documentation review, A-12): it lists the tracked files with
`git ls-files`, reads the text ones, and fails on the credential shapes
that are unmistakable. Generic shapes such as a `password=` assignment
are left out on purpose: they hit prose and test fixtures far more often
than secrets, and a check that cries wolf gets skipped.

A failure names the file, the line and the pattern, never the matched
text, so the report itself cannot leak what it found. Where git or the
repository is absent, as in an installed copy of the package, the scan
skips with that reason rather than guessing at a file list.
"""

from __future__ import annotations

import base64
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# Pattern name -> the shape. Each one is a vendor-fixed prefix followed
# by a body long enough that prose cannot produce it by accident. The
# sources below are written so that none of them matches its own text:
# every prefix is followed by a bracket or a parenthesis here, never by
# a body.
PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(source)
    for name, source in {
        "AWS access key id": r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])",
        "GitHub token": r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b",
        "GitHub fine-grained token": r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b",
        "Anthropic API key": r"\bsk-ant-[A-Za-z0-9_-]{32,}",
        "Slack token": r"\bxox[abeoprs]-[0-9A-Za-z-]{20,}",
        "private key header": r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----",
        "Azure storage account key": r"AccountKey=[A-Za-z0-9+/]{40,}={0,2}",
    }.items()
}


def update_key_patterns() -> dict[str, re.Pattern[str]]:
    """The update key's marker, in the two forms a key can be pasted in.

    PLAN-0021 phase 3, Invariant 4: the key that signs every update must
    never be committed, and this scan is the backstop that runs with the
    suite. A Tauri updater private key file is base64 of a minisign
    secret key box, and the box opens with one header line. Read off a
    throwaway key made with `cargo tauri signer generate` (tauri-cli
    2.11.4, through minisign 0.7.3), that line is the six words below
    with "rsign" in the middle; the minisign tool writes "minisign"
    there. Each header is 45 or 48 bytes, a whole number of base64
    groups, so the key file opens with exactly the header's base64.

    Built at run time from the words, so neither form stands in this
    file for the scan to find. The public key and a signature open with
    the same two words and then differ, so neither matches.

    A third pattern catches the box's second line pasted without its
    header (review fix 5b). Read off throwaway keys, that line is base64
    of 158 bytes: 212 characters ending in exactly one '='. The bytes
    open with three algorithm names, `Ed`, the key derivation (`Sc` for
    scrypt, which every Tauri key carries, or two zero bytes for none)
    and `B2`, so the line opens with their base64, and the pattern holds
    to that opening as well as to the length, which keeps it off other
    base64 of the same size.
    """
    headers = [
        " ".join(("untrusted", "comment:", tool, "encrypted", "secret", "key"))
        for tool in ("rsign", "minisign")
    ]
    encoded = [base64.b64encode(header.encode("ascii")).decode("ascii")
               for header in headers]
    openings = [base64.b64encode(b"Ed" + kdf + b"B2").decode("ascii")
                for kdf in (b"Sc", b"\x00\x00")]
    return {
        "updater private key": re.compile("|".join(map(re.escape, encoded))),
        "updater private key, decoded": re.compile(
            "|".join(map(re.escape, headers))),
        "updater private key, second line": re.compile(
            r"(?<![A-Za-z0-9+/])(?:" + "|".join(map(re.escape, openings))
            + r")[A-Za-z0-9+/]{203}=(?![A-Za-z0-9+/=])"),
    }


PATTERNS.update(update_key_patterns())

HERE = Path(__file__).resolve()


def scan_text(text: str) -> list[tuple[int, str]]:
    """(line number, pattern name) for every credential shape in text."""
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS.items():
            if pattern.search(line):
                hits.append((lineno, name))
    return hits


def read_text_file(path: Path) -> str | None:
    """The file's text, or None when it is binary or unreadable.

    A file that opens with a UTF-16 byte-order mark is read as UTF-16
    before the zero-byte test could call it binary: Windows PowerShell
    5.1's `>` and Out-File write that, so a key saved there would
    otherwise pass unread (PLAN-0021 phase 3, review fix 5a).
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def tracked_files() -> tuple[Path, list[Path]]:
    """The repository root this file belongs to and its tracked files.

    Skips when git is absent, when this file sits outside a repository,
    or when the enclosing repository does not track it: an installed
    copy under site-packages must not scan whatever repository happens
    to contain the interpreter.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not on this machine")
    here = HERE.parent
    top = subprocess.run(
        ["git", "-C", str(here), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if top.returncode != 0:
        pytest.skip("this test file is not inside a git repository")
    tracked = subprocess.run(
        ["git", "-C", str(here), "ls-files", "--error-unmatch", "--", HERE.name],
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        pytest.skip("the enclosing repository does not track this test file")
    root = Path(top.stdout.strip()).resolve()
    return root, listed_files(root)


def listed_files(root: Path) -> list[Path]:
    """Every file `git ls-files` lists under one repository root."""
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    names = [name for name in listing.stdout.split("\0") if name]
    return [root / name for name in names]


def scan_files(root: Path, files: list[Path]) -> list[str]:
    """"file:line: pattern" for every credential shape in the text files.

    The line names the place and the pattern, never the matched text.
    """
    found: list[str] = []
    for path in files:
        text = read_text_file(path)
        if text is None:
            continue
        rel = path.relative_to(root).as_posix()
        found.extend(f"{rel}:{lineno}: {name}" for lineno, name in scan_text(text))
    return found


def test_no_tracked_file_carries_a_credential():
    root, files = tracked_files()
    found = scan_files(root, files)
    assert not found, "credential shapes in tracked files:\n" + "\n".join(found)


def test_this_file_does_not_match_its_own_patterns():
    # The scan above only sees this file once it is tracked; an installed
    # copy never sees it. So the self-check stands on its own.
    text = HERE.read_text(encoding="utf-8")
    assert scan_text(text) == []


def stand_in_updater_key(tool: str = "rsign") -> str:
    """Text shaped like a decoded update key, which is not a key at all.

    PLAN-0021 phase 3, Invariant 4. A throwaway key made in scratch with
    `cargo tauri signer generate` (tauri-cli 2.11.4) showed the shape:
    the key file is base64 of a two-line minisign secret key box, and
    the box opens with the header line below; the minisign tool's own
    keys say "minisign" where the Tauri signer's say "rsign". The second
    line here is filler, not key material. The header is joined from its
    words at run time, so it never stands whole in this file.
    """
    header = " ".join(("untrusted", "comment:", tool, "encrypted", "secret",
                       "key"))
    return header + "\n" + "A" * 212 + "\n"


def as_key_file(text: str) -> str:
    """The form the signer writes to disk: the whole box as one base64 line."""
    return base64.b64encode(text.encode("ascii")).decode("ascii")


def stand_in_second_line(kdf: bytes = b"Sc") -> str:
    """The decoded key box's second line alone, as filler that is no key.

    PLAN-0021 phase 3, review fix 5b. Read off throwaway keys made in
    scratch: that line is base64 of the box's 158 bytes, 212 characters
    ending in exactly one '='. The bytes open with three algorithm
    names: `Ed`, the key derivation (`Sc` for scrypt, which every Tauri
    key carries, or two zero bytes for none) and `B2`. That opening is
    encoded here at run time; the rest is filler.
    """
    opening = base64.b64encode(b"Ed" + kdf + b"B2").decode("ascii")
    return opening + "A" * 203 + "="


def planted_scan(tmp_path: Path, files: dict[str, bytes]) -> list[str]:
    """Plant files in a scratch git repository, track them, scan them.

    The same two steps the repository scan takes, `git ls-files` and the
    per-file reading, so a planted marker found here is one the scan of
    this repository would find.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not on this machine")
    repo = tmp_path / "planted"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True,
                   check=True)
    for name, data in files.items():
        (repo / name).write_bytes(data)
    subprocess.run(["git", "-C", str(repo), "add", *files], capture_output=True,
                   check=True)
    return sorted(scan_files(repo, listed_files(repo)))


# The samples are assembled at run time so that no credential shape
# appears literally in this file.
SAMPLES = {
    "AWS access key id": "AKIA" + "0123456789ABCDEF",
    "GitHub token": "ghp_" + "a" * 36,
    "GitHub fine-grained token": "github_pat_" + "b" * 22 + "_" + "c" * 40,
    "Anthropic API key": "sk-ant-" + "api03-" + "d" * 40,
    "Slack token": "xoxb-" + "1" * 12 + "-" + "2" * 13 + "-" + "e" * 24,
    "private key header": "-----BEGIN " + "RSA PRIVATE" + " KEY-----",
    "Azure storage account key": "AccountKey=" + "f" * 86 + "==",
    "updater private key": as_key_file(stand_in_updater_key()),
    "updater private key, decoded": stand_in_updater_key().splitlines()[0],
    "updater private key, second line": stand_in_second_line(),
}


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_each_shape_is_caught_and_named(name: str):
    sample = SAMPLES[name]
    text = "harmless first line\nexport TOKEN=" + sample + "\nlast line\n"
    assert scan_text(text) == [(2, name)]


def test_report_names_location_and_pattern_never_the_value():
    sample = SAMPLES["GitHub token"]
    text = "x = '" + sample + "'\n"
    hits = scan_text(text)
    report = "\n".join(f"file.py:{lineno}: {name}" for lineno, name in hits)
    assert report == "file.py:1: GitHub token"
    assert sample not in report


@pytest.mark.parametrize(
    "line",
    [
        "the sk-ant- prefix names an Anthropic key",
        "tokens start with ghp_ or github_pat_",
        "set AccountKey= in the connection string",
        "an access key id looks like AKIA followed by sixteen characters",
        "xoxb- is the bot token prefix",
        "-----BEGIN CERTIFICATE-----",
    ],
)
def test_prose_about_the_prefixes_is_not_a_hit(line: str):
    assert scan_text(line + "\n") == []


def test_binary_files_are_skipped(tmp_path):
    blob = tmp_path / "image.png"
    blob.write_bytes(b"\x89PNG\x00" + b"ghp_" + b"a" * 36)
    assert read_text_file(blob) is None


# --- PLAN-0021 phase 3, Invariant 4: the update key's marker -----------

def test_the_update_key_is_caught_in_its_file_form_and_decoded():
    """Both forms an update key can be pasted in, and neither public one.

    The key file is one base64 line, so a copied key file is caught by
    the base64 of its header; a key someone decoded is caught by the
    header itself. The two public things that share the opening words,
    the public key and a signature, are not hits in either form: the
    shipped config carries one and every release manifest carries the
    other.
    """
    assert "updater private key" in PATTERNS
    assert "updater private key, decoded" in PATTERNS
    for tool in ("rsign", "minisign"):
        decoded = stand_in_updater_key(tool)
        assert scan_text(as_key_file(decoded) + "\n") == [
            (1, "updater private key")], tool
        assert scan_text("pasted below\n" + decoded) == [
            (2, "updater private key, decoded")], tool

    for public in (
        "untrusted comment: minisign public key: A8063EFC34527166\n",
        "untrusted comment: signature from tauri secret key\n",
        "untrusted comment: signature from rsign secret key\n",
    ):
        assert scan_text(public) == [], public
        assert scan_text(as_key_file(public) + "\n") == [], public


def test_a_planted_update_key_in_a_tracked_file_is_found(tmp_path):
    """The scan bites: a marker planted in a tracked file is named.

    A scratch repository with one clean file and the key in each of its
    two forms. The finding names the file, the line and the pattern, and
    never carries the text it found.
    """
    key_file = as_key_file(stand_in_updater_key())
    found = planted_scan(tmp_path, {
        "clean.txt": b"nothing to find here\n",
        "updater.key": key_file.encode("ascii"),
        "notes.md": ("pasted below\n" + stand_in_updater_key()).encode("ascii"),
    })
    assert found == [
        "notes.md:2: updater private key, decoded",
        "updater.key:1: updater private key",
    ], found
    assert key_file not in "\n".join(found)


def test_an_update_key_saved_as_utf16_is_read_and_caught(tmp_path):
    """Review fix 5a: a key file saved as UTF-16 is read, not skipped.

    Windows PowerShell 5.1's `>` and Out-File write UTF-16 with a
    byte-order mark, so a key copied that way is full of zero bytes and
    read as binary. A file that opens with either byte-order mark is
    decoded as UTF-16 and scanned. A file with zero bytes and no mark is
    still skipped as binary.
    """
    key_file = as_key_file(stand_in_updater_key())
    little = b"\xff\xfe" + key_file.encode("utf-16-le")
    big = b"\xfe\xff" + key_file.encode("utf-16-be")
    for raw in (little, big):
        held = tmp_path / "held.key"
        held.write_bytes(raw)
        assert read_text_file(held) == key_file
    found = planted_scan(tmp_path, {
        "little.key": little,
        "big.key": big,
        "decoded.txt": (b"\xff\xfe"
                        + stand_in_updater_key().encode("utf-16-le")),
    })
    assert found == [
        "big.key:1: updater private key",
        "decoded.txt:1: updater private key, decoded",
        "little.key:1: updater private key",
    ], found

    bare = tmp_path / "bare.bin"
    bare.write_bytes(key_file.encode("utf-16-le"))
    assert read_text_file(bare) is None


def test_the_key_box_second_line_alone_is_caught(tmp_path):
    """Review fix 5b: the decoded box's second line, pasted on its own.

    The header line can be left behind when a key is pasted, so the
    second line is a marker of its own: the algorithm opening, then
    exactly 212 base64 characters ending in one '='. Lines one character
    short or long, with two '=', with another opening, or running on from
    other base64 are not it. The repository scan above shows it raises
    no false hit over the tracked files.
    """
    for kdf in (b"Sc", b"\x00\x00"):
        line = stand_in_second_line(kdf)
        assert len(line) == 212
        assert line.endswith("=") and not line.endswith("==")
        assert scan_text("pasted below\n  " + line + "\n") == [
            (2, "updater private key, second line")], kdf
    line = stand_in_second_line()
    for near in (line[:-2] + "=", line[:-1] + "A=", line[:-2] + "==",
                 "QUFB" + line[4:], "x" + line):
        assert scan_text(near + "\n") == [], near

    found = planted_scan(tmp_path, {
        "pasted.txt": ("the second line\n" + line + "\n").encode("ascii"),
    })
    assert found == ["pasted.txt:2: updater private key, second line"], found
