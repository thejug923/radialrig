"""Tests for the seed PLAN-0005 Phase 2 lays down.

Run:  uv run --with pytest pytest src/radialrig/tests/test_mount.py -q

The oracle is "exactly these files and nothing else", so every write test
compares a full recursive tree snapshot taken before the mount against one
taken after. Spot checks would pass while a stray file shipped.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import textwrap
from hashlib import sha256
from pathlib import Path

import pytest

import radialrig
from radialrig import mount as mnt
from radialrig import validate_docs as vd
from radialrig.paths import packaged_seed

MOUNT_DAY = dt.date(2026, 8, 17)


def newer_than(installed: str) -> str:
    """A version above `installed`: its next major, or 1.0.0 when it
    does not start with a number.

    The upgrade, update and skill tests stand in a newer release under
    this version. It was the literal 0.2.0, so a tree bumped to 0.2.0,
    as a fork's rehearsal did, met its own version as "the next
    release" and failed. Only "different from the installed one"
    matters to the engine, which records versions and never orders
    them; the next major keeps that true on every bump.
    """
    head = re.match(r"\d+", installed)
    return f"{int(head.group()) + 1}.0.0" if head else "1.0.0"


# The newer release those tests stand in, read from the installed one.
NEXT_VERSION = newer_than(radialrig.__version__)

# The seed, restated here as a literal. The test must not import the list
# it is checking, or a wrong entry in SEED_FILES would agree with itself.
EXPECTED = {
    ".codex/config.toml",
    ".mcp.json",
    ".radialrig/mount.json",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/archive/.gitkeep",
    "docs/archive/D-00-archive-exclusion-target.md",
    "docs/decisions/.gitkeep",
    "docs/plans/.gitkeep",
    "docs/spec-git-environment.md",
    "docs/spec.md",
    "docs/templates/decision.md",
    "docs/templates/plan.md",
    "docs/templates/spec.md",
}

SEEDED_MARKDOWN = {
    "AGENTS.md",
    "CLAUDE.md",
    "docs/archive/D-00-archive-exclusion-target.md",
    "docs/spec-git-environment.md",
    "docs/spec.md",
    "docs/templates/decision.md",
    "docs/templates/plan.md",
    "docs/templates/spec.md",
}


# --- fixtures and helpers ---------------------------------------------

def _make_project(root: Path) -> Path:
    """A project the user already has: content of its own, no RadialRig."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_bytes(b"# Sample\n\nA project that already exists.\n")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_bytes(b'print("hello")\n')
    (root / ".gitignore").write_bytes(b"__pycache__/\n")
    return root


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
    )


@pytest.fixture()
def host(tmp_path) -> Path:
    """A git-backed fixture project. C5 reads a baseline from git HEAD, so
    the host a mount is validated on carries a readable HEAD."""
    root = _make_project(tmp_path / "sample-project")
    _git(root, "init", "-q", ".")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=fixture",
         "commit", "-qm", "init")
    return root


@pytest.fixture()
def bare_host(tmp_path) -> Path:
    """The same project with no git history, for the documented fallback."""
    return _make_project(tmp_path / "no-git-project")


def snapshot(root: Path) -> dict[str, str]:
    """Every file under root except .git/**, as {posix relpath: sha256}."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and ".git" not in rel.parts:
            out[rel.as_posix()] = sha256(p.read_bytes()).hexdigest()
    return out


def tree(root: Path) -> tuple[dict[str, bytes], set[str]]:
    """Every file's bytes and every directory under root, except .git/**.

    The round-trip oracle compares bytes rather than hash names, and it
    compares directory sets too: an empty directory is invisible to git
    and to snapshot(), and §3.5's whole point is a pre-existing empty
    directory that must survive an unmount.
    """
    files: dict[str, bytes] = {}
    dirs: set[str] = set()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if ".git" in rel.parts:
            continue
        if p.is_dir():
            dirs.add(rel.as_posix() + "/")
        elif p.is_file():
            files[rel.as_posix()] = p.read_bytes()
    return files, dirs


def diff(before: dict[str, str], after: dict[str, str]):
    added = set(after) - set(before)
    removed = set(before) - set(after)
    changed = {k for k in set(before) & set(after) if before[k] != after[k]}
    return added, removed, changed


def strip_code(text: str) -> str:
    """Markdown with fenced blocks and inline code spans removed — the
    same two passes C4 makes before it scans for links."""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def frontmatter(path: Path) -> dict[str, str]:
    from radialrig.doc_core import parse_frontmatter

    fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    assert fm is not None, f"{path} has no parseable frontmatter"
    return fm


# --- 1-3: exactly the seed --------------------------------------------

def test_bare_host_gets_exactly_the_seed(host):
    before = snapshot(host)
    mnt.mount(host, today=MOUNT_DAY)
    added, removed, changed = diff(before, snapshot(host))
    assert added == EXPECTED
    assert removed == set()
    assert changed == set()


def test_git_sees_exactly_the_seed_as_untracked(host):
    mnt.mount(host, today=MOUNT_DAY)
    out = _git(host, "status", "--porcelain", "-uall")
    assert out.returncode == 0, out.stderr
    seen = {line[3:].strip().strip('"') for line in out.stdout.splitlines() if line.strip()}
    assert seen == EXPECTED


def test_directories_are_held_by_empty_gitkeeps(host):
    mnt.mount(host, today=MOUNT_DAY)
    # The archive also carries the seeded record the exclusion is tested
    # against; every other seeded directory holds its placeholder alone.
    held = {
        "decisions": [".gitkeep"],
        "plans": [".gitkeep"],
        "archive": [".gitkeep", "D-00-archive-exclusion-target.md"],
    }
    for name, expected in held.items():
        d = host / "docs" / name
        assert d.is_dir()
        assert [p.name for p in sorted(d.iterdir())] == expected
        assert (d / ".gitkeep").read_bytes() == b""


# --- 4-6: the checks pass on the fresh seed ---------------------------

def test_validator_passes_on_the_fresh_seed(host, capsys, monkeypatch):
    mnt.mount(host, today=MOUNT_DAY)
    monkeypatch.setattr(sys, "argv", ["radialrig-validate", "--root", str(host),
                                      "--today", MOUNT_DAY.isoformat()])
    assert vd.main() == 0
    assert capsys.readouterr().out == "OK: 3 governed file(s), all checks passed\n"


def test_seeded_specs_renumber_contiguously(host):
    mnt.mount(host, today=MOUNT_DAY)
    one = host / "docs" / "spec.md"
    two = host / "docs" / "spec-git-environment.md"
    assert frontmatter(one)["id"] == "SPEC-1"
    assert frontmatter(two)["id"] == "SPEC-2"
    assert frontmatter(two)["depends-on"] == "SPEC-1"
    assert "[SPEC-2](spec-git-environment.md)" in one.read_text(encoding="utf-8")
    assert "[SPEC-1](spec.md)" in two.read_text(encoding="utf-8")


def test_non_git_host_reports_the_missing_baseline(bare_host, capsys, monkeypatch):
    mnt.mount(bare_host, today=MOUNT_DAY)
    monkeypatch.setattr(sys, "argv", ["radialrig-validate", "--root", str(bare_host),
                                      "--today", MOUNT_DAY.isoformat()])
    assert vd.main() == 1
    out = capsys.readouterr().out
    assert "C5 (lifecycle)" in out and "no readable git HEAD" in out
    assert "--no-baseline" in out

    monkeypatch.setattr(sys, "argv", ["radialrig-validate", "--root", str(bare_host),
                                      "--today", MOUNT_DAY.isoformat(), "--no-baseline"])
    assert vd.main() == 0
    assert "OK: 3 governed file(s), all checks passed" in capsys.readouterr().out


def test_a_host_with_no_commit_yet_validates_with_a_note(tmp_path, capsys, monkeypatch):
    # The state every brand-new project starts in: git init, no commit.
    # The unborn HEAD is a definitive empty history, not a missing one,
    # so the validator proceeds on the empty baseline and says so.
    root = _make_project(tmp_path / "new-project")
    _git(root, "init", "-q", ".")
    mnt.mount(root, today=MOUNT_DAY)
    monkeypatch.setattr(sys, "argv", ["radialrig-validate", "--root", str(root),
                                      "--today", MOUNT_DAY.isoformat()])
    assert vd.main() == 0
    out = capsys.readouterr().out
    assert "note: no commit here yet" in out
    assert "C5 (lifecycle) transition and id checks did not run" in out
    assert "OK: 3 governed file(s), all checks passed" in out


def test_guidance_answers_on_the_mounted_host(host):
    # Through the server module in a subprocess whose cwd is the mounted
    # host: this also exercises resolve_root() against the two markers the
    # mount just wrote, and proves the packaged fallback with no
    # research/ tree present.
    mnt.mount(host, today=MOUNT_DAY)
    assert not (host / "research").exists()
    program = textwrap.dedent("""
        import json
        from radialrig import retrieval_server as rs
        print(json.dumps({"root": str(rs.ROOT), "result": rs.get_guidance()}))
    """)
    out = subprocess.run([sys.executable, "-c", program], cwd=host,
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert Path(payload["root"]).resolve() == host.resolve()
    result = payload["result"]
    assert result["kind"] == "guidance_catalog"
    assert result["refused"] == []
    assert sorted(t["topic"] for t in result["topics"]) == [
        "anti-hobbling", "app-security", "blocked-tasks", "context-rot",
        "documentation-bloat", "expectation", "memory-management",
        "untrusted-content", "visualizations",
    ]


# --- 8-10: the two merge cases ----------------------------------------

def test_existing_agents_md_keeps_its_content(host):
    original = b"# Sample project\n\nHouse rules live here.\n"
    (host / "AGENTS.md").write_bytes(original)
    before = snapshot(host)

    result = mnt.mount(host, today=MOUNT_DAY)
    added, removed, changed = diff(before, snapshot(host))
    assert added == EXPECTED - {"AGENTS.md"}
    assert removed == set()
    assert changed == {"AGENTS.md"}

    data = (host / "AGENTS.md").read_bytes()
    assert data.startswith(original)
    text = data.decode("utf-8")
    assert text.count(mnt.FENCE_BEGIN) == 1
    assert text.count(mnt.FENCE_END) == 1

    start = text.index(mnt.FENCE_BEGIN)
    end = text.index(mnt.FENCE_END) + len(mnt.FENCE_END) + 1
    region = text[start:end].encode("utf-8")
    entry = next(f for f in result.files if f.path == "AGENTS.md")
    assert entry.file_created is False
    assert entry.section_sha256 == sha256(region).hexdigest()


def test_existing_mcp_json_keys_are_preserved(host):
    (host / ".mcp.json").write_bytes(
        json.dumps({"mcpServers": {"other": {"command": "other-server", "args": ["--x"]}}},
                   indent=4).encode("utf-8") + b"\n"
    )
    result = mnt.mount(host, today=MOUNT_DAY)
    data = json.loads((host / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["other"] == {"command": "other-server", "args": ["--x"]}
    assert data["mcpServers"]["radialrig"] == {"command": "radialrig-read", "args": []}
    # D-18 constraint 6: the mount registers the write server beside the
    # read server, so capture is on from the first session (PLAN-0012).
    assert data["mcpServers"]["radialrig-write"] == {
        "command": mnt.WRITE_SERVER_COMMAND, "args": []}
    entry = next(f for f in result.files if f.path == ".mcp.json")
    assert entry.keys == ("mcpServers.radialrig", "mcpServers.radialrig-write")
    assert entry.entries == {
        "radialrig": {"command": "radialrig-read", "args": []},
        "radialrig-write": {"command": "radialrig-write", "args": []},
    }
    assert entry.file_created is False
    # One region holds both keys, so unmount deletes exactly one region.
    assert entry.insert.count('"radialrig":') == 1
    assert entry.insert.count('"radialrig-write":') == 1


def test_a_host_that_registered_one_key_gets_only_the_other(host):
    theirs = {"command": "their-own-server", "args": []}
    original = (json.dumps({"mcpServers": {"radialrig-write": theirs}}, indent=2)
                + "\n").encode("utf-8")
    (host / ".mcp.json").write_bytes(original)
    result = mnt.mount(host, today=MOUNT_DAY)
    data = json.loads((host / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["radialrig-write"] == theirs
    assert data["mcpServers"]["radialrig"] == {"command": "radialrig-read", "args": []}
    entry = next(f for f in result.files if f.path == ".mcp.json")
    assert entry.keys == ("mcpServers.radialrig",)
    assert entry.disposition().code == mnt.MOUNT_MERGED_PARTIAL
    assert "mcpServers.radialrig-write" not in mnt.reason_text(entry.disposition())
    # The reversal takes out the one key the mount added and nothing else.
    mnt.unmount(host)
    assert (host / ".mcp.json").read_bytes() == original


def test_existing_radialrig_key_is_not_overwritten(host):
    theirs = {"command": "their-own-server", "args": ["--their-flag"]}
    (host / ".mcp.json").write_bytes(
        (json.dumps({"mcpServers": {"radialrig": theirs}}, indent=2) + "\n").encode("utf-8")
    )
    result = mnt.mount(host, today=MOUNT_DAY)
    data = json.loads((host / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["radialrig"] == theirs
    entry = next(f for f in result.files if f.path == ".mcp.json")
    # The write key is the mount's own; the read key stays the host's.
    assert entry.keys == ("mcpServers.radialrig-write",)
    assert entry.disposition().code == mnt.MOUNT_MERGED_PARTIAL
    assert data["mcpServers"]["radialrig-write"] == {
        "command": mnt.WRITE_SERVER_COMMAND, "args": []}


def test_a_host_that_registered_both_keys_is_left_whole(host):
    theirs = {"command": "their-own-server", "args": []}
    original = (json.dumps({"mcpServers": {
        "radialrig": theirs, "radialrig-write": theirs}}, indent=2) + "\n").encode("utf-8")
    (host / ".mcp.json").write_bytes(original)
    result = mnt.mount(host, today=MOUNT_DAY)
    assert (host / ".mcp.json").read_bytes() == original
    entry = next(f for f in result.files if f.path == ".mcp.json")
    assert entry.keys == ()
    assert entry.written is False
    assert entry.disposition().code == mnt.MOUNT_MCP_HOST_OWNED


# --- 11-12: the refusals write nothing --------------------------------

def test_mount_refuses_when_a_seed_target_exists(host):
    (host / "docs").mkdir()
    (host / "docs" / "spec.md").write_bytes(b"the host's own rulebook\n")
    before = snapshot(host)
    with pytest.raises(mnt.MountConflict) as exc:
        mnt.mount(host, today=MOUNT_DAY)
    assert "docs/spec.md" in exc.value.conflicts
    assert snapshot(host) == before


def test_mount_refuses_a_fenced_agents_section(host):
    (host / "AGENTS.md").write_bytes(
        f"# Host\n\n{mnt.FENCE_BEGIN}\nsomething else\n{mnt.FENCE_END}\n".encode("utf-8")
    )
    before = snapshot(host)
    with pytest.raises(mnt.MountConflict) as exc:
        mnt.mount(host, today=MOUNT_DAY)
    assert exc.value.conflicts == ("AGENTS.md",)
    assert snapshot(host) == before


def test_mount_refuses_a_second_time(host):
    mnt.mount(host, today=MOUNT_DAY)
    before = snapshot(host)
    with pytest.raises(mnt.AlreadyMounted):
        mnt.mount(host, today=MOUNT_DAY)
    assert snapshot(host) == before
    assert mnt.survey(host) == mnt.MountSurvey(mounted=True)


def test_mount_refuses_a_folder_inside_a_mounted_project(host):
    # Issue #65: a second mount below a mounted root started a second
    # rulebook, and sessions under it stopped seeing the root's records.
    mnt.mount(host, today=MOUNT_DAY)
    part = host / "apps" / "website"
    part.mkdir(parents=True)
    before = tree(host)
    for verb in (mnt.mount_plan, mnt.mount):
        with pytest.raises(mnt.InsideMountedProject) as exc:
            verb(part, today=MOUNT_DAY)
        assert exc.value.root == part.resolve()
        assert exc.value.parent == host.resolve()
    assert tree(host) == before


def test_a_rulebook_above_counts_without_its_mount_record(host):
    # A clone that keeps .radialrig/ out of git has the rulebook and no
    # record, and resolve_root walks up to it all the same.
    mnt.mount(host, today=MOUNT_DAY)
    (host / mnt.MANIFEST_REL).unlink()
    part = host / "tools"
    part.mkdir()
    with pytest.raises(mnt.InsideMountedProject) as exc:
        mnt.mount(part, today=MOUNT_DAY)
    assert exc.value.parent == host.resolve()

    # The release review (Fable): a rulebook saved with a byte-order mark
    # is still a rulebook, as resolve_root treats it.
    spec = host / "docs" / "spec.md"
    spec.write_bytes(b"\xef\xbb\xbf" + spec.read_bytes())
    assert mnt.mounted_ancestor(part.resolve()) == host.resolve()


def test_git_declining_another_owners_checkout_falls_back_to_the_marker(host, monkeypatch):
    # Round 4 (GPT Sol): git's safe.directory refusal says nothing about
    # whether the folder is a checkout, so the marker's shape decides.
    task = host / ".worktrees" / "task"
    added = _git(host, "worktree", "add", "-q", "--detach", str(task))
    assert added.returncode == 0, added.stderr
    refused = subprocess.CompletedProcess(
        args=[], returncode=128, stdout="",
        stderr="fatal: detected dubious ownership in repository\n"
               "To add an exception for this directory, call:\n"
               "\tgit config --global --add safe.directory ...\n",
    )
    monkeypatch.setattr(mnt.subprocess, "run", lambda *a, **k: refused)
    assert mnt._checkout_top(task) is True
    (host / "plain" / ".git").mkdir(parents=True)
    assert mnt._checkout_top(host / "plain") is False


def test_another_tools_spec_above_does_not_block_a_mount(tmp_path):
    outer = _make_project(tmp_path / "outer")
    (outer / "docs").mkdir()
    (outer / "docs" / "spec.md").write_bytes(b"# Our API spec\n\nNot a rulebook.\n")
    inner = _make_project(outer / "inner")
    assert mnt.mounted_ancestor(inner.resolve()) is None
    mnt.mount(inner, today=MOUNT_DAY)
    assert mnt.is_mounted(inner)


def test_a_checkout_of_its_own_inside_a_mounted_project_may_mount(host):
    # The 0.1.20 release review (GPT Sol): a git worktree or a nested
    # repository is a project of its own, whatever folder it sits in, and
    # Claude Code keeps its worktrees inside the project.
    task = host / ".worktrees" / "task"
    added = _git(host, "worktree", "add", "-q", "--detach", str(task))
    assert added.returncode == 0, added.stderr
    vendored = host / "vendor" / "lib"
    vendored.mkdir(parents=True)
    assert _git(vendored, "init", "-q").returncode == 0
    mnt.mount(host, today=MOUNT_DAY)

    for checkout in (task, vendored):
        assert mnt.mounted_ancestor(checkout.resolve()) is None
        mnt.mount(checkout, today=MOUNT_DAY)
        assert mnt.is_mounted(checkout)
    # A plain folder of the project is still refused, and so is one inside
    # the worktree once the worktree is mounted.
    for part in (host / "apps", task / "apps"):
        part.mkdir()
        with pytest.raises(mnt.InsideMountedProject):
            mnt.mount(part, today=MOUNT_DAY)

    # Round 2 (GPT Sol): an empty `.git` folder or a stray `.git` file is
    # not a checkout, and does not let a plain folder past the refusal.
    empty, stray = host / "empty-marker", host / "stray-marker"
    (empty / ".git").mkdir(parents=True)
    stray.mkdir()
    (stray / ".git").write_text("not a pointer\n", encoding="utf-8")
    # Round 3: a made-up HEAD, or a pointer to nowhere, is not one either;
    # git decides.
    fake_head, dangling = host / "fake-head", host / "dangling-pointer"
    (fake_head / ".git").mkdir(parents=True)
    (fake_head / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    dangling.mkdir()
    (dangling / ".git").write_text("gitdir: no-such-gitdir\n", encoding="utf-8")
    for part in (empty, stray, fake_head, dangling):
        with pytest.raises(mnt.InsideMountedProject):
            mnt.mount(part, today=MOUNT_DAY)


# --- 13-15: the manifest and the tokens -------------------------------

def test_manifest_hashes_every_written_file(host):
    mnt.mount(host, today=MOUNT_DAY)
    manifest = mnt.read_manifest(host)
    assert manifest["schema"] == mnt.MANIFEST_SCHEMA
    assert manifest["hash"] == "sha256"
    assert manifest["scope"] == "sample-project"
    assert manifest["owner"] == mnt.DEFAULT_OWNER
    assert manifest["mounted"] == MOUNT_DAY.isoformat()
    assert manifest["radialrig"]

    listed = [e["path"] for e in manifest["files"]]
    assert listed == sorted(listed), "manifest files are not sorted by path"
    assert set(listed) == EXPECTED - {mnt.MANIFEST_REL}
    for entry in manifest["files"]:
        data = (host / entry["path"]).read_bytes()
        assert entry["sha256"] == sha256(data).hexdigest(), entry["path"]


def test_scope_defaults_to_the_directory_name(host, tmp_path):
    mnt.mount(host, today=MOUNT_DAY)
    for rel in ("docs/spec.md", "docs/spec-git-environment.md"):
        assert frontmatter(host / rel)["scope"] == "sample-project"
    assert mnt.read_manifest(host)["scope"] == "sample-project"

    other = _make_project(tmp_path / "second")
    mnt.mount(other, scope="Other Scope", owner="the-team", today=MOUNT_DAY)
    for rel in ("docs/spec.md", "docs/spec-git-environment.md"):
        fm = frontmatter(other / rel)
        assert fm["scope"] == "Other Scope"
        assert fm["owner"] == "the-team"
    manifest = mnt.read_manifest(other)
    assert manifest["scope"] == "Other Scope"
    assert manifest["owner"] == "the-team"


def test_no_unsubstituted_token_ships(host):
    mnt.mount(host, today=MOUNT_DAY)
    for rel in sorted(EXPECTED):
        assert b"{{" not in (host / rel).read_bytes(), rel


def test_route_map_names_both_server_commands(host):
    # BUG-0005: the seeded section names the exact command for each
    # server, so an owner who removed the write entry and wants it back
    # registers the one that ships. PLAN-0012: the mount registers both,
    # and the section says the write server is on and how it comes out.
    mnt.mount(host, today=MOUNT_DAY)
    # Folded to single spaces, because the seed wraps at 72 columns and
    # the token sits wherever the line break falls; the rule sentence
    # lives in rule 7, a list item whose continuation lines are indented.
    text = " ".join((host / "AGENTS.md").read_text(encoding="utf-8").split())
    assert f"running `{mnt.SERVER_COMMAND}`" in text
    assert f"running `{mnt.WRITE_SERVER_COMMAND}`" in text
    assert mnt.WRITE_SERVER_COMMAND == "radialrig-write"
    assert "off by default" not in text
    assert "owner reads the diff" not in text
    # PLAN-0012 phase 2: the verb returns the sentence, the agent shows
    # it to the person, and nothing lands until the person says yes.
    assert "approves the ruling in one sentence" not in text
    assert "returns the ruling as one plain sentence" in text
    assert "shows that sentence to the person and reads the diff" in text
    assert "nothing is committed until the person says yes" in text
    assert "stops at the working tree" in text
    assert f"remove the `{mnt.WRITE_SERVER_KEY}` entry" in text
    data = json.loads((host / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["radialrig-write"] == {
        "command": mnt.WRITE_SERVER_COMMAND, "args": []}
    # The seeded rulebook's section 6 carries the same rule.
    spec = (host / "docs" / "spec-git-environment.md").read_text(
        encoding="utf-8").replace("\n", " ")
    assert "owner reads the diff" not in spec
    assert "approves the ruling in one sentence" in spec
    assert "an agent reads the diff" in spec
    # 2026-09-05: the default-branch gate matches D-35 section 6.
    assert "human reads the diff" not in spec
    assert "fresh-context reader of the diff" in spec


# --- 16-18: the seed list, the endings, and the absent records --------

def test_seed_sources_match_the_declared_list():
    base = packaged_seed()
    assert base is not None, "the package ships no seed sources"
    on_disk = {
        p.relative_to(base).as_posix()
        for p in base.rglob("*")
        if p.is_file() and p.name != "README.txt"
    }
    declared = {e.source for e in mnt.SEED_FILES if e.source}
    assert on_disk == declared
    assert tuple(sorted(mnt.WRITTEN_PATHS)) == tuple(sorted(EXPECTED))


def test_written_files_use_lf_endings(host):
    mnt.mount(host, today=MOUNT_DAY)
    for rel in sorted(EXPECTED):
        assert b"\r\n" not in (host / rel).read_bytes(), rel


def test_a_recorded_hash_matches_through_a_crlf_flip():
    # BUG-0011: the one rule every comparing verb shares. The digest
    # names the LF bytes the mount wrote and never moves; the file is
    # unchanged when it hashes to it as it stands or with CRLF read as
    # LF, and a real edit differs both ways.
    written = b"# Title\n\nA line.\n"
    flipped = written.replace(b"\n", b"\r\n")
    digest = sha256(written).hexdigest()
    assert mnt.matches_recorded(written, digest)
    assert mnt.matches_recorded(flipped, digest)
    assert not mnt.matches_recorded(flipped + b"ours\r\n", digest)
    assert not mnt.matches_recorded(written + b"ours\n", digest)
    assert not mnt.matches_recorded(written, None)
    assert not mnt.matches_recorded(written, "")
    assert not mnt.matches_recorded(written, sha256(flipped).hexdigest())
    # The form a restore writes: the bytes that hash to the record.
    assert mnt._recorded_form(written, digest) == written
    assert mnt._recorded_form(flipped, digest) == written
    assert mnt._recorded_form(written + b"ours\n", digest) is None


def test_a_double_cr_file_does_not_match_a_crlf_record():
    # Review 2026-09-05: one CRLF pass turns `\r\r\n` into `\r\n`, so a
    # file with doubled CRs read as equal to a CRLF pre-mount record it
    # does not equal. The LF reading counts only when the pass removed
    # every CRLF.
    crlf_written = b"# Title\r\n\r\nA line.\r\n"
    digest = sha256(crlf_written).hexdigest()
    assert mnt.matches_recorded(crlf_written, digest)
    assert not mnt.matches_recorded(crlf_written.replace(b"\r\n", b"\r\r\n"), digest)
    assert not mnt.matches_recorded(crlf_written.replace(b"\r\n", b"\n"), digest)


def test_the_section_locator_covers_a_crlf_closing_fence():
    # Review 2026-09-05: the raw slice of a flipped file used to stop
    # one byte short of the CR before the closing fence's newline.
    section = mnt._section_bytes({"SCOPE": "s", "OWNER": "o", "DATE": "2026-09-05",
                                  "TOOL_CMD": "t", "WRITE_TOOL_CMD": "w"})
    flipped = section.replace(b"\n", b"\r\n")
    for data, whole in ((section, section), (flipped, flipped),
                        (b"# Ours\r\n\r\n" + flipped + b"tail\r\n", flipped)):
        start, end = mnt._locate_section(data)
        assert data[start:end] == whole
    assert mnt._lf(flipped) == section


def test_a_crlf_checkout_of_the_seed_still_ships_lf(host, tmp_path, monkeypatch):
    # `* text=auto` with core.autocrlf=true materializes the seed sources
    # with CRLF on Windows. A mount from that checkout must still write LF,
    # or the manifest hash forks by the mounting machine's platform.
    base = packaged_seed()
    assert base is not None
    crlf = tmp_path / "crlf-seed"
    for src in sorted(base.rglob("*")):
        if src.is_file():
            dest = crlf / src.relative_to(base)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(mnt, "packaged_seed", lambda: crlf)

    result = mnt.mount(host, today=MOUNT_DAY)
    for rel in sorted(EXPECTED):
        assert b"\r\n" not in (host / rel).read_bytes(), rel

    other = _make_project(tmp_path / "lf-host")
    monkeypatch.undo()
    reference = mnt.mount(other, scope="sample-project", today=MOUNT_DAY)
    assert {f.path: f.sha256 for f in result.files} == {
        f.path: f.sha256 for f in reference.files
    }


def test_seeded_text_cites_no_absent_record(host):
    mnt.mount(host, today=MOUNT_DAY)
    # R1: a fresh host has an empty docs/decisions/, so a D-nn citation
    # points at nothing. Record keys survive only as examples in code
    # spans. The three templates are exempt: their placeholder ids are
    # id-shaped on purpose and cite nothing.
    for rel in ("docs/spec.md", "docs/spec-git-environment.md", "AGENTS.md"):
        prose = strip_code((host / rel).read_text(encoding="utf-8"))
        assert not re.search(r"D-\d+", prose), f"{rel} cites a record the host does not have"

    from radialrig.doc_core import LINK_RE

    for rel in sorted(SEEDED_MARKDOWN):
        path = host / rel
        for target in LINK_RE.findall(strip_code(path.read_text(encoding="utf-8"))):
            target = target.split("#")[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            assert (path.parent / target).exists(), f"{rel}: link target '{target}' is dead"


def test_route_map_section_stays_within_the_budget(host):
    mnt.mount(host, today=MOUNT_DAY)
    text = (host / "AGENTS.md").read_text(encoding="utf-8")
    start = text.index(mnt.FENCE_BEGIN)
    end = text.index(mnt.FENCE_END) + len(mnt.FENCE_END)
    lines = text[start:end].splitlines()
    # D-08 budgets the host's whole route map at 100 lines; the seeded
    # section leaves room for the host's own content.
    assert len(lines) <= 80, f"the seeded route map is {len(lines)} lines"
    assert lines[0] == mnt.FENCE_BEGIN
    assert lines[-1] == mnt.FENCE_END
    assert lines[1] == "## RadialRig route map"


def test_repository_route_map_stays_within_the_budget():
    # D-08 budgets the always-loaded route map at 100 lines. The fence
    # test above measures the seed on a fixture host; this one measures
    # this repository's own AGENTS.md, which nothing else counted.
    from radialrig.paths import resolve_root

    route_map = resolve_root() / "AGENTS.md"
    if not route_map.is_file():
        pytest.skip("no AGENTS.md at the corpus root")
    lines = route_map.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 100, f"the route map is {len(lines)} lines (D-08)"


def test_route_map_names_no_complete_spec_list(host):
    # Issue #10: a host can grow specs beyond the seeded two, so the
    # fence must say its spec list is open, as it already does for
    # record ids. Pinned word for word.
    mnt.mount(host, today=MOUNT_DAY)
    text = (host / "AGENTS.md").read_text(encoding="utf-8")
    assert ("A project can hold further specs; this file names no\n"
            "  complete list of them. `list_rulings` carries the catalog.") in text


def test_survey_reports_without_writing(host):
    (host / "AGENTS.md").write_bytes(b"# Host\n")
    before = snapshot(host)
    s = mnt.survey(host)
    assert s == mnt.MountSurvey(mounted=False, conflicts=(), merges=("AGENTS.md",))
    assert snapshot(host) == before


# --- B: the .mcp.json splice (PLAN-0005 Phase 3) ----------------------
# A mount only ever inserts. Every byte it adds to a file the host
# already had lives in one contiguous region, which is what makes an
# unmount byte-exact without the manifest ever holding host bytes.

ENTRY = {"command": "radialrig-read", "args": []}
WRITE_ENTRY = {"command": "radialrig-write", "args": []}
BOTH = {"radialrig": ENTRY, "radialrig-write": WRITE_ENTRY}


def mcp_entry(root: Path) -> dict:
    return json.loads((root / ".mcp.json").read_bytes().decode("utf-8"))


def mcp_record(result) -> mnt.WrittenFile:
    return next(f for f in result.files if f.path == ".mcp.json")


def test_mcp_json_is_created_at_indent_two_lf(host):
    result = mnt.mount(host, today=MOUNT_DAY)
    data = (host / ".mcp.json").read_bytes()
    assert b"\r\n" not in data
    assert data.decode("utf-8") == (
        json.dumps({"mcpServers": BOTH}, indent=2) + "\n"
    )
    record = mcp_record(result)
    assert record.file_created is True
    assert record.written is True
    assert record.keys == ("mcpServers.radialrig", "mcpServers.radialrig-write")


def test_indent_four_host_file_keeps_every_original_byte(host):
    original = (
        '{\n    "mcpServers": {\n'
        '        "other": {"command": "other-server", "args": ["--x"]}\n'
        "    }\n}\n"
    ).encode("utf-8")
    (host / ".mcp.json").write_bytes(original)
    result = mnt.mount(host, today=MOUNT_DAY)

    after = (host / ".mcp.json").read_bytes()
    record = mcp_record(result)
    assert record.insert is not None
    assert after.replace(record.insert.encode("utf-8"), b"", 1) == original
    data = mcp_entry(host)
    assert data["mcpServers"]["radialrig"] == ENTRY
    assert data["mcpServers"]["other"] == {"command": "other-server", "args": ["--x"]}


def test_tab_indented_host_file_keeps_its_indentation(host):
    original = (
        '{\n\t"mcpServers": {\n\t\t"other": {"command": "x", "args": []}\n\t}\n}\n'
    ).encode("utf-8")
    (host / ".mcp.json").write_bytes(original)
    result = mnt.mount(host, today=MOUNT_DAY)
    record = mcp_record(result)
    assert record.insert == (
        '\n\t\t"radialrig": {"command": "radialrig-read", "args": []},'
        '\n\t\t"radialrig-write": {"command": "radialrig-write", "args": []},'
    )
    assert (host / ".mcp.json").read_bytes().replace(
        record.insert.encode("utf-8"), b"", 1) == original


def test_crlf_host_file_gets_a_crlf_region(host):
    original = (
        '{\r\n  "mcpServers": {\r\n    "other": {"command": "x", "args": []}\r\n  }\r\n}\r\n'
    ).encode("utf-8")
    (host / ".mcp.json").write_bytes(original)
    result = mnt.mount(host, today=MOUNT_DAY)
    record = mcp_record(result)
    assert record.insert.startswith("\r\n")
    after = (host / ".mcp.json").read_bytes()
    assert after.replace(record.insert.encode("utf-8"), b"", 1) == original
    assert mcp_entry(host)["mcpServers"]["radialrig"] == ENTRY


def test_an_empty_servers_object_closes_on_its_own_line(host):
    (host / ".mcp.json").write_bytes(b'{\n  "mcpServers": {}\n}\n')
    mnt.mount(host, today=MOUNT_DAY)
    text = (host / ".mcp.json").read_bytes().decode("utf-8")
    assert text == (
        '{\n  "mcpServers": {\n'
        '    "radialrig": {"command": "radialrig-read", "args": []},\n'
        '    "radialrig-write": {"command": "radialrig-write", "args": []}\n'
        "  }\n}\n"
    )


def test_a_missing_servers_key_is_inserted_with_a_comma(host):
    original = b'{\n  "other": 1\n}\n'
    (host / ".mcp.json").write_bytes(original)
    result = mnt.mount(host, today=MOUNT_DAY)
    data = mcp_entry(host)
    assert data["other"] == 1
    assert data["mcpServers"]["radialrig"] == ENTRY
    record = mcp_record(result)
    assert record.insert.endswith(",")
    assert (host / ".mcp.json").read_bytes().replace(
        record.insert.encode("utf-8"), b"", 1) == original


def test_an_empty_root_object_is_inserted_without_a_comma(host):
    original = b"{}\n"
    (host / ".mcp.json").write_bytes(original)
    result = mnt.mount(host, today=MOUNT_DAY)
    assert mcp_entry(host) == {"mcpServers": BOTH}
    record = mcp_record(result)
    assert not record.insert.endswith(",")
    assert (host / ".mcp.json").read_bytes().replace(
        record.insert.encode("utf-8"), b"", 1) == original


def test_host_owned_keys_leave_the_file_byte_untouched(host):
    theirs = {"command": "their-own-server", "args": ["--their-flag"]}
    original = (json.dumps({"mcpServers": {
        "radialrig": theirs, "radialrig-write": theirs}}, indent=4)
        + "\n").encode("utf-8")
    (host / ".mcp.json").write_bytes(original)
    result = mnt.mount(host, today=MOUNT_DAY)
    assert (host / ".mcp.json").read_bytes() == original
    record = mcp_record(result)
    assert record.written is False
    assert record.keys == ()
    # Judged by a build that knew every seed key: nothing written, and
    # the record says which keys stood here (PLAN-0017 phase 2).
    assert record.entries == {}
    assert record.held == ("radialrig", "radialrig-write")
    assert record.sha256 == sha256(original).hexdigest()


def test_a_host_owned_read_key_gets_the_write_key_beside_it(host):
    theirs = {"command": "their-own-server", "args": ["--their-flag"]}
    original = (json.dumps({"mcpServers": {"radialrig": theirs}}, indent=4)
                + "\n").encode("utf-8")
    (host / ".mcp.json").write_bytes(original)
    result = mnt.mount(host, today=MOUNT_DAY)
    record = mcp_record(result)
    assert record.written is True
    assert record.keys == ("mcpServers.radialrig-write",)
    assert record.entries == {"radialrig-write": WRITE_ENTRY}
    after = (host / ".mcp.json").read_bytes()
    assert after.replace(record.insert.encode("utf-8"), b"", 1) == original
    data = mcp_entry(host)
    assert data["mcpServers"]["radialrig"] == theirs
    assert data["mcpServers"]["radialrig-write"] == WRITE_ENTRY


def test_a_list_shaped_mcp_json_still_refuses(host):
    for payload in (b"[]\n", b'{"mcpServers": []}\n'):
        (host / ".mcp.json").write_bytes(payload)
        before = snapshot(host)
        with pytest.raises(mnt.MountConflict) as exc:
            mnt.mount(host, today=MOUNT_DAY)
        assert exc.value.conflicts == (".mcp.json",)
        assert snapshot(host) == before


def test_the_manifest_holds_no_host_bytes(host):
    (host / ".mcp.json").write_bytes(
        (json.dumps({"mcpServers": {"other": {
            "command": "x", "args": [], "env": {"SOME_API_KEY": "HOSTSECRET"}}}},
            indent=2) + "\n").encode("utf-8")
    )
    (host / "AGENTS.md").write_bytes(b"# Host\n\nHOSTSECRET lives here.\n")
    mnt.mount(host, today=MOUNT_DAY)
    assert b"HOSTSECRET" not in (host / ".radialrig" / "mount.json").read_bytes()


def test_a_bad_offset_falls_back_to_re_serialization(host, monkeypatch):
    original = b'{\n    "mcpServers": {\n        "other": 1\n    }\n}\n'
    (host / ".mcp.json").write_bytes(original)
    # The composer takes its container as a keyword argument since
    # PLAN-0007 Phase 2; the stand-in accepts and ignores it.
    monkeypatch.setattr(mnt, "_mcp_insert_entries", lambda text, entries, **kw: (0, "XX"))
    result = mnt.mount(host, today=MOUNT_DAY)
    record = mcp_record(result)
    assert record.reserialized is True
    assert record.insert is None
    assert record.written is True
    data = mcp_entry(host)
    assert data["mcpServers"]["radialrig"] == ENTRY
    assert data["mcpServers"]["radialrig-write"] == WRITE_ENTRY
    assert data["mcpServers"]["other"] == 1
    manifest = mnt.read_manifest(host)
    entry = next(e for e in manifest["files"] if e["path"] == ".mcp.json")
    assert entry["reserialized"] is True
    assert "insert" not in entry

    from radialrig import cli

    line = next(
        line for line in cli.render_block(result.dispositions())
        if line.startswith("merged")
    )
    assert "re-serialized" in line


def test_the_insert_region_takes_a_key():
    """PLAN-0007 Phase 2's one generalization of the splice composer.

    The connect kit registers a different key in the same container, and
    it reuses this composer rather than growing a second copy of the one
    rule that matters. The defaults must still produce exactly what the
    mount has always produced.
    """
    text = '{\n  "mcpServers": {\n    "other": {"command": "x"}\n  }\n}\n'
    entry = {"command": "c", "args": []}

    offset, region = mnt._mcp_insert_region(text, entry)
    assert '"radialrig":' in region
    assert mnt._mcp_insert_region(text, entry, key=mnt.SERVER_KEY,
                                  container="mcpServers") == (offset, region)

    named = mnt._mcp_insert_region(text, entry, key="radialrig-x")
    assert named is not None
    assert '"radialrig-x":' in named[1]
    assert '"radialrig":' not in named[1]
    assert named[0] == offset

    # And the whole splice verifies against the object it promised.
    spliced = mnt._splice_mcp(text, json.loads(text), entry, key="radialrig-x")
    assert spliced is not None
    loaded = json.loads(spliced[0])
    assert loaded["mcpServers"]["radialrig-x"] == entry
    assert loaded["mcpServers"]["other"] == {"command": "x"}

    # A container this file does not carry is inserted whole.
    bare = '{\n  "other": 1\n}\n'
    found = mnt._mcp_insert_region(bare, entry, key="k", container="theirServers")
    assert found is not None
    assert '"theirServers": {' in found[1]


def test_the_recorded_region_appears_exactly_once(host):
    (host / ".mcp.json").write_bytes(
        b'{\n  "mcpServers": {\n    "other": {"command": "x", "args": []}\n  }\n}\n'
    )
    result = mnt.mount(host, today=MOUNT_DAY)
    record = mcp_record(result)
    assert (host / ".mcp.json").read_bytes().count(record.insert.encode("utf-8")) == 1


def test_the_manifest_records_the_directories_the_mount_created(host):
    mnt.mount(host, today=MOUNT_DAY)
    manifest = mnt.read_manifest(host)
    assert manifest["dirs"] == [
        ".codex", ".radialrig", "docs", "docs/archive", "docs/decisions",
        "docs/plans", "docs/templates",
    ]


def test_a_pre_existing_directory_is_not_recorded(host):
    (host / "docs" / "plans").mkdir(parents=True)
    result = mnt.mount(host, today=MOUNT_DAY)
    assert "docs" not in result.dirs
    assert "docs/plans" not in result.dirs
    assert "docs/templates" in result.dirs


def test_mount_plan_writes_nothing_and_matches_the_real_run(host):
    before = tree(host)
    planned = mnt.mount_plan(host, today=MOUNT_DAY)
    assert tree(host) == before
    real = mnt.mount(host, today=MOUNT_DAY)
    assert planned.dispositions() == real.dispositions()
    assert {f.path: f.sha256 for f in planned.files} == {
        f.path: f.sha256 for f in real.files
    }


# --- C: constraint 8 of PLAN-0009 — one platform-pure payload ----------

def test_a_mount_writes_the_same_bytes_on_every_platform(tmp_path):
    """One package version writes one set of bytes, whichever platform
    runs the mount (PLAN-0009 constraint 8).

    One run cannot compare two platforms, so the pin is three-fold: the
    payload is byte-identical across two roots with the same pinned
    inputs, no byte of it is a carriage return, and no byte of it names
    this machine. The gate runs this same test on Windows, on Ubuntu and
    on Debian, which is what turns the pins into the cross-platform
    claim: a payload that is a pure function of the packaged data and
    the pinned inputs cannot differ between machines that install the
    same package version.
    """
    first = _make_project(tmp_path / "first-root")
    second = _make_project(tmp_path / "second-root")
    mnt.mount(first, scope="pinned-scope", owner="pinned-owner", today=MOUNT_DAY)
    mnt.mount(second, scope="pinned-scope", owner="pinned-owner", today=MOUNT_DAY)

    wrote = {rel: (first / rel).read_bytes() for rel in EXPECTED}
    assert {rel: (second / rel).read_bytes() for rel in EXPECTED} == wrote

    # The manifest records posix relative paths and hashes that match
    # the bytes on disk, so the record itself carries nothing a second
    # platform would spell differently.
    manifest = json.loads(wrote[".radialrig/mount.json"].decode("utf-8"))
    assert manifest["hash"] == "sha256"
    recorded = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    for rel, digest in recorded.items():
        assert "\\" not in rel, rel
        assert digest == sha256(wrote[rel]).hexdigest(), rel

    home = str(Path.home()).encode("utf-8")
    root_mark = str(tmp_path).encode("utf-8")
    for rel, data in wrote.items():
        assert b"\r" not in data, rel
        assert root_mark not in data, rel
        assert home not in data, rel


# --- PLAN-0017 phase 2 (mount-02): all of it or none, whatever stops it --

def test_an_interrupt_during_the_write_leaves_the_tree_as_found(tmp_path, monkeypatch):
    root = _make_project(tmp_path / "host")
    before = tree(root)
    real = Path.write_bytes

    def interrupt(self, data):
        if self.name == "AGENTS.md":
            raise KeyboardInterrupt
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", interrupt)
    with pytest.raises(KeyboardInterrupt):
        mnt.mount(root, today=MOUNT_DAY)
    assert not (root / mnt.MANIFEST_REL).exists()
    assert not mnt.is_mounted(root)
    assert tree(root) == before


def test_the_manifest_is_written_after_every_other_path(tmp_path, monkeypatch):
    root = _make_project(tmp_path / "host")
    real = Path.write_bytes
    seen: list[str] = []

    def record(self, data):
        seen.append(self.name)
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", record)
    mnt.mount(root, today=MOUNT_DAY)
    assert seen[-1] == "mount.json"
    assert seen.count("mount.json") == 1


def test_a_write_that_stops_says_what_was_put_back(tmp_path, monkeypatch):
    # The restore that fails is reported, not passed over: the error
    # names the one path whose bytes could not be put back, and the
    # disk shows exactly that path changed.
    root = _make_project(tmp_path / "host")
    original = b'{\n  "mcpServers": {}\n}\n'
    (root / ".mcp.json").write_bytes(original)
    before_files, before_dirs = tree(root)
    real = Path.write_bytes

    def flaky(self, data):
        if self.name == "CLAUDE.md":
            raise OSError(28, "No space left on device", str(self))
        if self.name == ".mcp.json" and data == original:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    with pytest.raises(mnt.WriteStopped) as caught:
        mnt.mount(root, today=MOUNT_DAY)
    exc = caught.value
    assert isinstance(exc, mnt.MountError)
    assert exc.left == (".mcp.json",)
    assert "No space left on device" in str(exc)
    assert str(exc).endswith("put back except: .mcp.json")
    after_files, after_dirs = tree(root)
    assert set(after_files) == set(before_files)
    assert after_dirs == before_dirs
    changed = {rel for rel in before_files if before_files[rel] != after_files[rel]}
    assert changed == {".mcp.json"}
    assert not mnt.is_mounted(root)


def test_a_file_error_before_the_write_is_the_kits_own_error(tmp_path, monkeypatch):
    root = _make_project(tmp_path / "host")
    (root / ".mcp.json").write_bytes(b'{\n  "mcpServers": {}\n}\n')
    real = Path.read_bytes
    armed = [True]

    def refuse(self):
        if armed[0] and self.name == ".mcp.json":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    for verb in (mnt.mount_plan, mnt.mount):
        with pytest.raises(mnt.WriteStopped) as caught:
            verb(root, today=MOUNT_DAY)
        assert caught.value.left == ()
        assert "Permission denied" in str(caught.value)
        assert str(caught.value).endswith("nothing was written")
    armed[0] = False
    assert not mnt.is_mounted(root)


# --- PLAN-0017 phase 2 (mount-01): the record says what it judged -----

def _mcp_json_record(root: Path) -> dict:
    return next(e for e in mnt.read_manifest(root)["files"] if e["path"] == ".mcp.json")


def test_every_merged_record_carries_entries_and_the_keys_it_held(tmp_path):
    theirs = {"command": "their-own-server", "args": []}
    both = _make_project(tmp_path / "both")
    (both / ".mcp.json").write_bytes((json.dumps({"mcpServers": {
        "radialrig": theirs, "radialrig-write": theirs}}, indent=2) + "\n").encode("utf-8"))
    mnt.mount(both, today=MOUNT_DAY)
    entry = _mcp_json_record(both)
    assert entry["written"] is False and entry["keys"] == []
    assert entry["entries"] == {}
    assert entry["held"] == ["radialrig", "radialrig-write"]

    one = _make_project(tmp_path / "one")
    (one / ".mcp.json").write_bytes((json.dumps({"mcpServers": {
        "radialrig": theirs}}, indent=2) + "\n").encode("utf-8"))
    mnt.mount(one, today=MOUNT_DAY)
    entry = _mcp_json_record(one)
    assert entry["written"] is True
    assert entry["entries"] == {"radialrig-write": WRITE_ENTRY}
    assert entry["held"] == ["radialrig"]

    fresh = _make_project(tmp_path / "fresh")
    mnt.mount(fresh, today=MOUNT_DAY)
    entry = _mcp_json_record(fresh)
    assert entry["entries"] == mnt._mcp_entries()
    assert entry["held"] == []


def test_a_fault_inside_the_restore_is_still_the_kits_own_error(tmp_path, monkeypatch):
    # The manifest's write runs out of space; putting the tree back, the
    # created .radialrig directory refuses both its removal and the
    # question whether it stands. Nothing bare escapes: the error is
    # WriteStopped, and the directory is named as not put back.
    root = _make_project(tmp_path / "host")
    before_files, _ = tree(root)
    real_write, real_rmdir, real_exists = Path.write_bytes, Path.rmdir, Path.exists
    stopped = [False]

    def no_space(self, data):
        if self.name == "mount.json":
            stopped[0] = True
            raise OSError(28, "No space left on device", str(self))
        return real_write(self, data)

    def refuse_rmdir(self):
        if stopped[0] and self.name == ".radialrig":
            raise PermissionError(13, "Permission denied", str(self))
        return real_rmdir(self)

    def refuse_exists(self, *args, **kwargs):
        if stopped[0] and self.name == ".radialrig":
            raise PermissionError(13, "Permission denied (exists)", str(self))
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", no_space)
    monkeypatch.setattr(Path, "rmdir", refuse_rmdir)
    monkeypatch.setattr(Path, "exists", refuse_exists)
    with pytest.raises(mnt.WriteStopped) as caught:
        mnt.mount(root, today=MOUNT_DAY)
    monkeypatch.undo()
    exc = caught.value
    assert "No space left on device" in str(exc)
    assert exc.left == (".radialrig",)
    assert str(exc).endswith("put back except: .radialrig")
    after_files, after_dirs = tree(root)
    assert after_files == before_files
    assert after_dirs == {".radialrig/", "src/"}
    assert not mnt.is_mounted(root)


# --- PLAN-0017 phase 7 (tests-03): the merge gate seen failing ---------

def test_the_validator_reports_a_duplicate_in_force_id_and_exits_one(
    bare_host, capsys, monkeypatch,
):
    """AGENTS.md names `radialrig-validate` as a check before a merge, and
    its failing branch — one line per failure, the FAIL summary, exit 1 —
    was never seen by the suite. A second in-force copy of the seeded
    spec is the smallest corpus the gate must refuse."""
    mnt.mount(bare_host, today=MOUNT_DAY)
    spec = bare_host / "docs" / "spec.md"
    (bare_host / "docs" / "spec-twin.md").write_bytes(spec.read_bytes())
    monkeypatch.setattr(sys, "argv", ["radialrig-validate", "--root", str(bare_host),
                                      "--today", MOUNT_DAY.isoformat(), "--no-baseline"])
    assert vd.main() == 1
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("note: --no-baseline")
    assert [line for line in lines if line.startswith("C1 (uniqueness) (SPEC-1, ")] == [
        "C1 (uniqueness) (SPEC-1, " + frontmatter(spec)["scope"]
        + "): 2 in-force records: docs/spec-twin.md, docs/spec.md",
    ]
    assert lines[-1] == "FAIL: 1 failure(s) across 4 governed file(s)"
    assert "OK:" not in out
