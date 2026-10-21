"""Tests for the two CI workflows - PLAN-0023 phase 1, prescription 1.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_workflows.py -q

The workflows run only on GitHub, so their text is what this suite can
hold. Two behaviours are pinned. Both workflows run on pull requests
into `main` as well as `master` and `staging`, so the public
repository's pull requests run the same checks as this one's. And a run
saves a cache only when GitHub's own context says the repository is
private: the public repository's runs, whose saved files anyone could
read, save none. The test reads no repository name, because the same
files serve both repositories and must decide from the context alone.

The reading below covers the subset of YAML these two files use: a
top-level `on:` block, and steps that each open with a `- key:` line.
These tests never ship, so a missing file is a failure, never a skip.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
NAMES = ("docs-gardening.yml", "release-linux.yml")

# The branches whose pull requests run the checks: this repository's two
# and the public repository's one.
BRANCHES = {"master", "staging", "main"}

# True only when GitHub reports the repository private. A payload
# without the field reads as public, so a missing value saves nothing.
PRIVATE_ONLY = "github.event.repository.private == true"

# Every action the workflows use, by whether it can save a cache. A new
# action fails `test_every_action_is_known` until it is sorted here.
SAVES_NOTHING = {"actions/checkout", "actions/upload-artifact"}
SAVES_A_CACHE = {"astral-sh/setup-uv", "actions/cache", "actions/cache/save"}


def read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def triggers(text: str) -> str:
    """The `on:` block, up to the next top-level key."""
    block = text.split("\non:\n", 1)[1]
    return re.split(r"\n(?=\S)", block, maxsplit=1)[0]


def pull_request_branches(text: str) -> set[str]:
    block = triggers(text).split("pull_request:", 1)[1]
    found = re.search(r"branches:\s*\[([^\]]*)\]", block)
    assert found, block
    return {name.strip() for name in found.group(1).split(",")}


def steps(text: str) -> list[str]:
    """Each step's own lines, from its `- ` line to the next step or the
    end of its job."""
    out: list[str] = []
    lines = text.splitlines()
    for at, line in enumerate(lines):
        # A step opens with any key: `- name:`, `- uses:`, `- if:`, `- id:`.
        start = re.match(r"(\s*)- [\w-]+:", line)
        if not start:
            continue
        indent = len(start.group(1))
        body = [line]
        for later in lines[at + 1:]:
            stripped = later.lstrip()
            if stripped and len(later) - len(stripped) <= indent:
                break
            body.append(later)
        out.append("\n".join(body))
    return out


def action(step: str) -> str | None:
    found = re.search(r"^\s*-?\s*uses:\s*([^@\s]+)@", step, re.MULTILINE)
    return found.group(1) if found else None


def field(step: str, key: str) -> str | None:
    found = re.search(rf"^\s*-?\s*{re.escape(key)}:\s*(.+)$", step, re.MULTILINE)
    return found.group(1).strip() if found else None


def expression(value: str | None) -> str:
    """The text inside `${{ }}`, or the bare value."""
    if value is None:
        return ""
    inner = re.fullmatch(r"\$\{\{\s*(.*?)\s*\}\}", value)
    return inner.group(1) if inner else value


@pytest.mark.parametrize("name", NAMES)
def test_pull_requests_into_main_run_the_checks(name):
    """Prescription 1, first half: the public repository's pull requests
    target `main`, and both workflows run on them as they do here."""
    branches = pull_request_branches(read(name))
    assert BRANCHES <= branches, f"{name} runs on pull requests into {sorted(branches)}"


@pytest.mark.parametrize("name", NAMES)
def test_a_cache_is_saved_only_when_github_says_the_repository_is_private(name):
    """Prescription 1, second half. `actions/cache` restores and saves in
    one step, so the step itself runs only in a private repository.
    setup-uv 5 has no save switch, so its cache is turned off unless
    the repository is private, and keeps its default there."""
    caching = [step for step in steps(read(name)) if action(step) in SAVES_A_CACHE]
    for step in caching:
        if action(step) == "astral-sh/setup-uv":
            setting = expression(field(step, "enable-cache"))
            assert setting == f"{PRIVATE_ONLY} && 'auto' || 'false'", step
        else:
            assert expression(field(step, "if")) == PRIVATE_ONLY, step


def test_both_workflows_have_steps_that_could_save_a_cache():
    """Guards the test above against reading nothing: each workflow has
    at least one caching step for it to judge."""
    for name in NAMES:
        assert any(action(step) in SAVES_A_CACHE for step in steps(read(name))), name


@pytest.mark.parametrize("name", NAMES)
def test_every_action_is_known(name):
    """An action this file has not sorted could save a cache of its own,
    so it fails here until someone reads its inputs and sorts it."""
    used = {action(step) for step in steps(read(name))} - {None}
    unknown = used - SAVES_NOTHING - SAVES_A_CACHE
    assert not unknown, f"{name} uses actions this test has not sorted: {sorted(unknown)}"


# The context fields that carry a repository's name or owner. A workflow
# that read one could only be comparing it with a name it spells.
NAMING_FIELDS = re.compile(
    r"github\.(repository(_owner)?\b|event\.repository\.(name|full_name|owner))"
)


def expressions(text: str) -> list[str]:
    """Every expression in a workflow: each `${{ }}`, and each `if:`,
    which is an expression with or without the braces around it."""
    return [e.strip() for e in (
        *re.findall(r"\$\{\{(.*?)\}\}", text),
        *re.findall(r"^\s*(?:-\s*)?if:\s*(.+)$", text, re.MULTILINE),
    )]


@pytest.mark.parametrize("name", NAMES)
def test_the_token_reads_the_checkout_and_nothing_more(name):
    """PLAN-0023 phase 4 review, F-10: the token's rights are declared in
    the file, not left to each repository's setting, so a setting changed
    on either repository cannot widen what a pull request's code can do
    with the token. No job widens them again."""
    text = read(name)
    parts = text.split("\npermissions:\n")
    assert len(parts) == 2, f"{name} declares no top-level permissions, or declares them twice"
    block = re.split(r"\n(?=\S)", parts[1], maxsplit=1)[0]
    assert [line.strip() for line in block.splitlines() if line.strip()] == ["contents: read"]
    assert not re.search(r"^\s+permissions:", text, re.MULTILINE), name


def jobs(text: str) -> dict[str, str]:
    """Each job's own lines, by its key under the top-level `jobs:`."""
    block = text.split("\njobs:\n", 1)[1]
    found = re.split(r"^  ([\w-]+):\s*$", block, flags=re.MULTILINE)
    return dict(zip(found[1::2], found[2::2]))


@pytest.mark.parametrize("name", NAMES)
def test_a_container_job_that_installs_the_locked_packages_names_its_python(name):
    """A job in a bare container has no Python of its own, so uv downloads
    its newest, and a new Python release that the locked packages ship no
    wheels for breaks the gate: Python 3.15 did on 2026-10-21, when
    `pydantic-core` had to build from source with no compiler there. A
    job that installs them (`uv run`, `uv sync`) names its Python. The
    app's job only builds the project's own wheel, and its engine install
    names its Python itself (`python_pin` in `app/src/bootstrap.json`)."""
    for job, body in jobs(read(name)).items():
        if "container:" in body and re.search(r"\buv (run|sync)\b", body):
            assert re.search(r"^\s+UV_PYTHON:\s*\"3\.\d+\"\s*$", body, re.MULTILINE), \
                f"{name} job {job} installs the locked packages in a container " \
                "without UV_PYTHON"


@pytest.mark.parametrize("name", NAMES)
def test_a_container_job_that_runs_the_suite_marks_the_workspace_safe_system_wide(name):
    """In a container the checkout belongs to another user, and git refuses
    it until it is marked safe. The suite's `conftest.py` points HOME at
    scratch, so a mark in the user's global settings is not read there:
    on 2026-10-21 three tests that ask git about the repository failed in
    CI's Debian leg, and the secrets test skipped itself. The system-wide
    settings are read whatever HOME says."""
    for job, body in jobs(read(name)).items():
        if "container:" in body and "pytest" in body:
            assert re.search(r"git config --system --add safe\.directory", body), \
                f"{name} job {job} runs the suite in a container without a system-wide mark"


@pytest.mark.parametrize("name", NAMES)
def test_the_workflows_decide_without_naming_a_repository(name):
    """Prescription 1: the same file serves both repositories, so it
    reads whether the repository is private and never which one it is."""
    named = [e for e in expressions(read(name)) if NAMING_FIELDS.search(e)]
    assert not named, f"{name} reads a repository's name: {named}"
