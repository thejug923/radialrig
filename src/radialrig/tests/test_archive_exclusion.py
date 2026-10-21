"""The archive exclusion, measured against real archive content.

Run:  uv run --with pytest pytest src/radialrig/tests/test_archive_exclusion.py -q

D-04 rules that the archive lives under one excludable glob, and that a
glob which fails to exclude fails silently. The measurement here is
ripgrep with the form this repository records in SPEC-1 section 2.
Ripgrep is the engine under Claude Code's search, so this is a close
proxy for that harness, not the harness itself; a measurement in the
harness still dates the form (D-04).

Two runs, because one proves nothing. The first runs with the glob and
expects no hit under `docs/archive/`. The second runs without it and
expects at least one, so an archive that lost its marker fails here
instead of passing the first run on an absent target. Hits outside
`docs/archive/` are the prose that names the marker, and both runs
ignore them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

# The repository, found the way the tests' own location fixes it.
ROOT = Path(__file__).resolve().parents[3]
SPEC = ROOT / "docs" / "spec.md"
ARCHIVE = "docs/archive/"

# The phrase the seeded archived record carries. Stated here rather than
# read out of that record: a marker read from the file it is meant to
# find would agree with an empty archive.
MARKER = "radialrig-archive-exclusion-marker"

GLOB_RE = re.compile(r"`(![^`]+)`")


def recorded_glob() -> str:
    """The one backticked !-glob SPEC-1 carries, the way the evals read it.

    Section 2 records this project's exclusion form as a single line, and
    the rulebook carries no other. More than one, or none, is a defect in
    the rulebook rather than a case to guess at.
    """
    text = SPEC.read_text(encoding="utf-8")
    forms = set(GLOB_RE.findall(text))
    assert len(forms) == 1, f"docs/spec.md carries {len(forms)} !-globs: {sorted(forms)}"
    return forms.pop()


def hits(*globs: str) -> list[str]:
    """Root-relative posix paths ripgrep reports for the marker."""
    argv = ["rg", "--files-with-matches", "--fixed-strings", "--no-messages"]
    for glob in globs:
        argv += ["--glob", glob]
    argv += ["--", MARKER, "."]
    out = subprocess.run(
        argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert out.returncode in (0, 1), out.stderr
    # ripgrep prints the search path it was given, so on Windows the
    # lines come back as `.\docs\archive\...`. Separators first, then the
    # leading `./`, or the strip leaves a rooted path behind.
    return sorted(
        line.strip().replace("\\", "/").lstrip("./")
        for line in out.stdout.splitlines()
        if line.strip()
    )


needs_rg = pytest.mark.skipif(
    shutil.which("rg") is None,
    reason="ripgrep is not on the PATH, so the recorded glob cannot be measured here",
)


@needs_rg
def test_the_recorded_glob_excludes_the_archive():
    under_archive = [p for p in hits(recorded_glob()) if p.startswith(ARCHIVE)]
    assert under_archive == [], (
        "the exclusion form recorded in docs/spec.md section 2 did not exclude "
        f"{under_archive}; re-measure the form and amend section 2 under 8.1"
    )


@needs_rg
def test_the_marker_is_there_to_be_found_without_the_glob():
    under_archive = [p for p in hits() if p.startswith(ARCHIVE)]
    assert under_archive, (
        "no file under docs/archive/ carries the marker, so the exclusion "
        "test above would pass against an empty archive"
    )
