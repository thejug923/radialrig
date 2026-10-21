"""Tests for the `radialrig` command surface — PLAN-0005 Phase 3.

Run:  uv run --with pytest pytest src/radialrig/tests/test_cli.py -q

The surface owns layout only. Every verb, path and reason comes from
`radialrig.mount`, which is what makes Phase 4's page able to show the
same content without copying this text. So the assertions here are about
exit codes, refusals that write nothing, and the closed report vocabulary.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from radialrig import cli
from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import report
from radialrig import roster as ro
from radialrig import skills as sk
from radialrig import validate_docs
from radialrig.tests.test_mount import (  # noqa: F401 - fixtures are used by name
    EXPECTED,
    MOUNT_DAY,
    _make_project,
    frontmatter,
    tree,
)

# D-33: nothing this phase writes implies who typed a change. A project's
# change is "changed" or "edited"; the vocabulary below never appears.
FORBIDDEN = ("hand", "manual", "human")

# D-33 as amended by the owner on 2026-08-18: the OUTPUT checks match
# whole words. A skill's name is data, not vocabulary — `skill list`
# prints the directory a skill lands in, and the "hand" inside
# session-handoff says nothing about who typed a change. The SOURCE ban
# below is untouched and stays a strict substring test, which is what
# keeps cli.py and mount.py free of any literal skill name.
FORBIDDEN_WORD = re.compile(r"\b(?:" + "|".join(FORBIDDEN) + r")\b")

# D-33's strict substring ban, over every source that carries no literal
# name of its own. connect.py joins the list for PLAN-0007 Phase 2: it
# names no skill and no client outside its table, so the same reasoning
# reaches it.
NEW_SOURCES = ("cli.py", "mount.py", "connect.py", "adopt.py")

CLIENT = cn.DEFAULT_CLIENT

BLOCK_LINE = re.compile(r"^[a-z]+ {2,}\S")

# The one packaged skill, restated rather than imported: a test that read
# the catalog it is checking would agree with a wrong catalog.
SKILL = "session-handoff"
SKILL_DIR = ".claude/skills/session-handoff"
RECORD_REL = ".claude/radialrig-skills.json"


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    """No test here may reach this machine's own client configuration.

    The connect verbs read these two variables. Removing them by default
    means a test that forgets to inject a fake profile finds nothing and
    errors, rather than editing a real settings file.
    """
    for name in ("APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def client_here(tmp_path, monkeypatch) -> Path:
    """One fake client on this machine, and one locatable read command."""
    roaming = tmp_path / "profile" / "Roaming"
    (roaming / "Claude").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("APPDATA", str(roaming))
    exe = tmp_path / "bin" / "radialrig-read.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(cn, "_which", lambda name: str(exe))
    return roaming / "Claude" / "claude_desktop_config.json"


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path / "sample-project")


def block(text: str) -> list[str]:
    return [line for line in text.splitlines() if BLOCK_LINE.match(line)]


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- A1-A7: the three outcomes of mount -------------------------------

def test_a_fresh_project_mounts(project, capsys):
    code, out, err = run(capsys, "mount", str(project))
    assert code == 0
    assert err == ""
    lines = block(out)
    assert len(lines) == len(EXPECTED)
    assert {line.split()[1] for line in lines} == EXPECTED
    assert "Next: run radialrig-validate" in out
    assert set(tree(project)[0]) >= EXPECTED


def test_an_already_mounted_project_changes_nothing(project, capsys):
    run(capsys, "mount", str(project))
    before = tree(project)
    code, out, err = run(capsys, "mount", str(project))
    assert code == 4
    assert "Already mounted" in out
    assert "Nothing changed" in out
    assert tree(project) == before


def test_an_existing_seed_path_refuses_before_any_write(project, capsys):
    (project / "docs").mkdir()
    (project / "docs" / "spec.md").write_bytes(b"our own rulebook\n")
    before = tree(project)
    code, out, err = run(capsys, "mount", str(project))
    assert code == 3
    assert "Refused: 1 path(s) would be overwritten" in out
    assert "this project already has a file at this path" in out
    assert tree(project) == before


def test_a_fenced_agents_file_refuses(project, capsys):
    (project / "AGENTS.md").write_bytes(
        f"# Ours\n\n{mnt.FENCE_BEGIN}\nsomething\n{mnt.FENCE_END}\n".encode("utf-8")
    )
    before = tree(project)
    code, out, err = run(capsys, "mount", str(project))
    assert code == 3
    assert "already carries a radialrig fenced section" in out
    assert tree(project) == before


def test_dry_run_prints_the_same_body_and_writes_nothing(project, capsys):
    before = tree(project)
    code, dry, _ = run(capsys, "mount", str(project), "--dry-run")
    assert code == 0
    assert tree(project) == before
    assert cli.DRY_RUN_TAIL in dry

    code, real, _ = run(capsys, "mount", str(project))
    assert code == 0
    assert block(dry) == block(real)
    assert cli.DRY_RUN_TAIL not in real


def test_dry_run_on_a_conflicted_project_still_refuses(project, capsys):
    (project / "docs").mkdir()
    (project / "docs" / "spec.md").write_bytes(b"ours\n")
    before = tree(project)
    code, out, _ = run(capsys, "mount", str(project), "--dry-run")
    assert code == 3
    assert tree(project) == before


def test_scope_and_owner_reach_the_seed_and_the_record(project, capsys):
    code, _, _ = run(capsys, "mount", str(project),
                     "--scope", "Other Scope", "--owner", "the-team")
    assert code == 0
    for rel in ("docs/spec.md", "docs/spec-git-environment.md"):
        fm = frontmatter(project / rel)
        assert fm["scope"] == "Other Scope"
        assert fm["owner"] == "the-team"
    manifest = mnt.read_manifest(project)
    assert manifest["scope"] == "Other Scope"
    assert manifest["owner"] == "the-team"


# --- A8-A12: arguments -------------------------------------------------

@pytest.mark.parametrize("command", ["mount", "unmount", "upgrade"])
def test_a_path_that_is_not_a_directory_stops_at_exit_one(project, capsys, command):
    code, out, err = run(capsys, command, str(project / "nowhere"))
    assert code == 1
    assert out == ""
    assert "no such directory" in err

    code, out, err = run(capsys, command, str(project / "README.md"))
    assert code == 1
    assert "no such directory" in err


def test_a_bare_invocation_prints_usage(capsys):
    code, out, err = run(capsys)
    assert code == 0
    assert out == cli.USAGE
    assert err == ""


def test_version_prints_the_installed_version(capsys):
    from radialrig import __version__

    code, out, err = run(capsys, "--version")
    assert code == 0
    assert out == f"radialrig {__version__}\n"


def test_an_unknown_subcommand_is_argparse_s_exit_two(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["bogus"])
    assert exc.value.code == 2


# --- A13-A14: the two unreadable .mcp.json shapes ----------------------

@pytest.mark.parametrize("payload", [b"\xef\xbb\xbf{}\n", b""])
def test_an_unreadable_mcp_json_refuses(project, capsys, payload):
    (project / ".mcp.json").write_bytes(payload)
    before = tree(project)
    code, out, _ = run(capsys, "mount", str(project))
    assert code == 3
    assert "does not parse as a JSON object" in out
    assert tree(project) == before


# --- A15-A16: the report vocabulary ------------------------------------

def reports(project: Path, capsys) -> str:
    """Every report the verbs of both kits produce on one project."""
    text = []
    for argv in (
        ["mount", str(project), "--dry-run"],
        ["mount", str(project)],
        ["mount", str(project)],
        ["upgrade", str(project)],
        ["skill", "list", str(project)],
        ["skill", "add", SKILL, str(project), "--dry-run"],
        ["skill", "add", SKILL, str(project)],
        ["skill", "add", SKILL, str(project)],
        ["skill", "list", str(project)],
        ["skill", "add", "no-such-skill", str(project)],
        ["skill", "remove", SKILL, str(project), "--dry-run"],
        ["skill", "remove", SKILL, str(project)],
        ["skill", "remove", SKILL, str(project)],
        ["connect", CLIENT, str(project), "--dry-run"],
        ["connect", CLIENT, str(project)],
        ["connect", CLIENT, str(project)],
        ["connect", "no-such-client", str(project)],
        ["unconnect", CLIENT, str(project), "--dry-run"],
        ["unconnect", CLIENT, str(project)],
        ["unconnect", CLIENT, str(project)],
        ["unmount", str(project)],
        ["unmount", str(project)],
    ):
        cli.main(argv)
        captured = capsys.readouterr()
        text.append(captured.out)
        text.append(captured.err)
    return "\n".join(text)


def test_no_report_implies_who_typed_a_change(project, capsys, client_here):
    (project / "AGENTS.md").write_bytes(b"# Ours\n")
    (project / ".mcp.json").write_bytes(b'{\n  "mcpServers": {}\n}\n')
    client_here.write_bytes(b'{\n  "theme": "dark"\n}\n')
    text = reports(project, capsys).lower()
    hit = FORBIDDEN_WORD.search(text)
    assert hit is None, f"a report uses '{hit.group(0) if hit else ''}'"
    # And the amendment is a narrowing, not an amnesty: the substring is
    # still there to be found, inside a name and nowhere else.
    assert "hand" in text


def test_no_new_source_implies_who_typed_a_change():
    base = Path(mnt.__file__).parent
    for name in NEW_SOURCES:
        text = (base / name).read_bytes().decode("utf-8").lower()
        for word in FORBIDDEN:
            assert word not in text, f"{name} uses '{word}'"


def test_every_report_line_starts_with_a_closed_verb(project, capsys, client_here):
    (project / "AGENTS.md").write_bytes(b"# Ours\n")
    (project / ".mcp.json").write_bytes(b'{\n  "other": 1\n}\n')
    client_here.write_bytes(b'{\n  "mcpServers": {}\n}\n')
    lines = block(reports(project, capsys))
    assert lines, "no report lines were produced"
    for line in lines:
        assert line.split()[0] in mnt.VERBS, line


def test_a_refusal_line_also_carries_a_verb(project, capsys):
    (project / "docs").mkdir()
    (project / "docs" / "spec.md").write_bytes(b"ours\n")
    _, out, _ = run(capsys, "mount", str(project))
    lines = block(out)
    assert lines == [
        f"{mnt.LEFT:<9} docs/spec.md  this project already has a file at this path"
    ]


# --- the skill family: the seven states, by exit code ------------------
# D-36 and PLAN-0006 Phase 3. The engine's own behavior is pinned in
# test_skills.py; what is pinned here is the surface contract — which
# number each state exits with, and that a rehearsal writes nothing.


def record_of(project: Path) -> dict:
    return json.loads((project / RECORD_REL).read_text(encoding="utf-8"))


def rewrite_record(project: Path, body: dict) -> None:
    (project / RECORD_REL).write_bytes(mnt._dump_manifest(body))


def test_a_bare_skill_invocation_prints_usage(capsys):
    """`radialrig skill` runs none of the three verbs. Without defaults on
    the family's own parser this raises rather than answers, because
    main() reads args.path before it dispatches."""
    code, out, err = run(capsys, "skill")
    assert code == 0
    assert out == cli.USAGE
    assert err == ""


def test_a_skill_path_that_is_not_a_directory_stops_at_exit_one(project, capsys):
    for argv in (
        ["skill", "list", str(project / "nowhere")],
        ["skill", "add", SKILL, str(project / "nowhere")],
        ["skill", "remove", SKILL, str(project / "nowhere")],
    ):
        code, out, err = run(capsys, *argv)
        assert code == 1, argv
        assert out == ""
        assert "no such directory" in err


def test_state_1_and_2_a_skill_installs_then_says_it_is_current(project, capsys):
    code, out, _ = run(capsys, "skill", "remove", SKILL, str(project))
    assert code == 4
    assert "Not installed" in out

    code, out, err = run(capsys, "skill", "add", SKILL, str(project))
    assert code == 0
    assert err == ""
    assert report.SKILL_NEXT_STEP in out
    assert (project / SKILL_DIR / "SKILL.md").is_file()

    code, out, _ = run(capsys, "skill", "add", SKILL, str(project))
    assert code == 4
    assert "Already installed" in out

    code, out, _ = run(capsys, "skill", "remove", SKILL, str(project))
    assert code == 0
    assert report.SKILL_CLEAN_TAIL in out
    assert not (project / ".claude").exists()


def test_state_3_a_skill_that_matches_the_record_but_not_the_package(project, capsys):
    """The newer-release case, arranged from the record rather than from a
    second package: clean against what radialrig last wrote, different
    from what the package now ships."""
    run(capsys, "skill", "add", SKILL, str(project))
    target = project / SKILL_DIR / "SKILL.md"
    target.write_bytes(b"# what an older release shipped\n")
    body = record_of(project)
    for item in body["skills"][0]["files"]:
        item["sha256"] = mnt._sha256((project / item["path"]).read_bytes())
    rewrite_record(project, body)

    code, out, _ = run(capsys, "skill", "add", SKILL, str(project))
    assert code == 0
    assert mnt.UPDATED in {line.split()[0] for line in block(out)}
    assert target.read_bytes() != b"# what an older release shipped\n"


def test_state_4_an_edited_skill_writes_nothing_and_exits_five(project, capsys):
    run(capsys, "skill", "add", SKILL, str(project))
    (project / SKILL_DIR / "SKILL.md").write_bytes(b"# ours now\n")
    before = tree(project)

    code, out, _ = run(capsys, "skill", "add", SKILL, str(project))
    assert code == 5
    assert tree(project) == before
    assert "# ours now" in out, "the diff of the changed file is missing"
    assert report.SKILL_EDIT_TAIL in out

    code, out, _ = run(capsys, "skill", "remove", SKILL, str(project))
    assert code == 5
    assert report.SKILL_LEFT_TAIL in out
    assert (project / SKILL_DIR / "SKILL.md").read_bytes() == b"# ours now\n"


def test_state_5_a_stale_record_is_repaired_by_the_next_add(project, capsys):
    run(capsys, "skill", "add", SKILL, str(project))
    for rel in ("SKILL.md", "scripts/handoff.py"):
        (project / SKILL_DIR / rel).unlink()
    code, _, _ = run(capsys, "skill", "add", SKILL, str(project))
    assert code == 0
    assert (project / SKILL_DIR / "SKILL.md").is_file()


def test_state_6_a_directory_this_project_owns_refuses_at_exit_three(project, capsys):
    (project / SKILL_DIR).mkdir(parents=True)
    (project / SKILL_DIR / "ours.md").write_bytes(b"our own skill\n")
    before = tree(project)
    code, out, _ = run(capsys, "skill", "add", SKILL, str(project))
    assert code == 3
    assert report.refusal_head(1) in out
    assert report.SKILL_FOREIGN_TAIL in out
    assert tree(project) == before


def test_state_7_a_skill_no_longer_packaged_still_lists_and_removes(project, capsys):
    run(capsys, "skill", "add", SKILL, str(project))
    body = record_of(project)
    body["skills"][0]["name"] = "their-skill"
    rewrite_record(project, body)

    code, out, err = run(capsys, "skill", "add", "their-skill", str(project))
    assert code == 1
    assert out == ""
    assert "ships no skill named" in err
    assert SKILL in err, "the refusal does not say what the package does ship"

    code, out, _ = run(capsys, "skill", "list", str(project))
    assert code == 0
    assert mnt.REASONS[mnt.SKILL_NOT_PACKAGED] in out

    code, _, _ = run(capsys, "skill", "remove", "their-skill", str(project))
    assert code == 0
    assert not (project / ".claude").exists()


def test_skill_list_answers_in_words_on_a_project_with_nothing_installed(
    project, capsys,
):
    before = tree(project)
    code, out, err = run(capsys, "skill", "list", str(project))
    assert code == 0
    assert err == ""
    assert report.SKILL_NONE_TAIL in out
    assert block(out), "the list printed no rows"
    # list is the one verb with no --dry-run, so its no-write property is
    # pinned here rather than by a parity test: the snapshot is taken
    # before the run, or the comparison holds trivially.
    assert tree(project) == before


def test_a_skill_dry_run_prints_the_same_body_and_writes_nothing(project, capsys):
    before = tree(project)
    code, dry, _ = run(capsys, "skill", "add", SKILL, str(project), "--dry-run")
    assert code == 0
    assert tree(project) == before
    assert cli.DRY_RUN_TAIL in dry

    code, real, _ = run(capsys, "skill", "add", SKILL, str(project))
    assert code == 0
    assert block(dry) == block(real)
    assert cli.DRY_RUN_TAIL not in real

    installed = tree(project)
    code, dry, _ = run(capsys, "skill", "remove", SKILL, str(project), "--dry-run")
    assert code == 0
    assert tree(project) == installed
    assert cli.DRY_RUN_TAIL in dry

    code, real, _ = run(capsys, "skill", "remove", SKILL, str(project))
    assert code == 0
    assert block(dry) == block(real)
    assert cli.DRY_RUN_TAIL not in real


def test_a_dry_run_on_a_refused_add_still_refuses(project, capsys):
    (project / SKILL_DIR).mkdir(parents=True)
    (project / SKILL_DIR / "ours.md").write_bytes(b"ours\n")
    before = tree(project)
    code, out, _ = run(capsys, "skill", "add", SKILL, str(project), "--dry-run")
    assert code == 3
    assert cli.DRY_RUN_TAIL in out
    assert tree(project) == before


def test_every_skills_report_line_starts_with_a_closed_verb(project, capsys):
    """The same property the docs kit's reports carry, over a project the
    skills kit has left something on."""
    cli.main(["skill", "add", SKILL, str(project)])
    capsys.readouterr()
    (project / SKILL_DIR / "notes.md").write_bytes(b"mine\n")
    lines = []
    for argv in (
        ["skill", "list", str(project)],
        ["skill", "remove", SKILL, str(project)],
    ):
        cli.main(argv)
        lines.extend(block(capsys.readouterr().out))
    assert lines, "no report lines were produced"
    for line in lines:
        assert line.split()[0] in mnt.VERBS, line


def test_the_skills_kit_does_not_disturb_the_docs_kit_through_the_command(
    project, capsys,
):
    """Both kits, one project, one command surface: the docs round trip is
    unchanged by an installed skill, and exits 0."""
    before = tree(project)
    assert cli.main(["mount", str(project)]) == 0
    assert cli.main(["skill", "add", SKILL, str(project)]) == 0
    capsys.readouterr()
    assert cli.main(["unmount", str(project)]) == 0
    assert cli.main(["skill", "remove", SKILL, str(project)]) == 0
    capsys.readouterr()
    assert tree(project) == before


# --- the installed entry point ----------------------------------------

def test_the_entry_point_runs_the_three_verbs(project):
    """The module as a script, so the console-script path is exercised
    rather than only the in-process function."""
    def call(*argv) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "radialrig.cli", *argv],
            capture_output=True, text=True, encoding="utf-8",
        )

    out = call("mount", str(project))
    assert out.returncode == 0, out.stderr
    assert "Mounted radialrig" in out.stdout

    out = call("upgrade", str(project))
    assert out.returncode == 0, out.stderr
    assert "seed check for" in out.stdout

    out = call("unmount", str(project))
    assert out.returncode == 0, out.stderr
    assert "0 left" in out.stdout
    assert not (project / mnt.MANIFEST_REL).exists()


def test_the_entry_point_runs_the_skill_verbs(project):
    def call(*argv) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "radialrig.cli", *argv],
            capture_output=True, text=True, encoding="utf-8",
        )

    out = call("skill", "list", str(project))
    assert out.returncode == 0, out.stderr
    assert "Skills for" in out.stdout

    out = call("skill", "add", SKILL, str(project))
    assert out.returncode == 0, out.stderr
    assert (project / SKILL_DIR / "SKILL.md").is_file()

    out = call("skill", "remove", SKILL, str(project))
    assert out.returncode == 0, out.stderr
    assert "0 left" in out.stdout
    assert not (project / ".claude").exists()


def test_the_seeded_project_validates_through_the_command(project):
    assert cli.main(["mount", str(project)]) == 0
    out = subprocess.run(
        [sys.executable, "-m", "radialrig.validate_docs", "--root", str(project),
         "--no-baseline", "--today", MOUNT_DAY.isoformat()],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "all checks passed" in out.stdout
    assert json.loads((project / mnt.MANIFEST_REL).read_bytes().decode("utf-8"))


# --- PLAN-0017 phase 2 (cli-connect-02): a file error is one line ------
# The writers put back what they can and raise their kit's own error,
# which names what is left; every runner prints it as `radialrig: ...`.

def _refusing(monkeypatch, method: str, name: str) -> list[bool]:
    """`Path.<method>` refuses the file named `name` while armed."""
    real = getattr(Path, method)
    armed = [True]

    def refuse(self, *args, **kwargs):
        if armed[0] and self.name == name:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, refuse)
    return armed


def one_line(err: str) -> str:
    """The one diagnostic line a stop prints, and nothing else."""
    lines = err.splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("radialrig: "), err
    assert "Traceback" not in err
    return lines[0]


def test_a_read_only_client_file_under_connect_prints_one_line(
    project, capsys, client_here, monkeypatch,
):
    assert run(capsys, "mount", str(project))[0] == 0
    original = b'{\n  "mcpServers": {}\n}\n'
    client_here.write_bytes(original)
    _refusing(monkeypatch, "write_bytes", client_here.name)

    code, out, err = run(capsys, "connect", CLIENT, str(project))
    assert code == cli.ERROR
    line = one_line(err)
    assert "Permission denied" in line
    assert line.endswith("nothing was written")
    assert out == ""
    assert client_here.read_bytes() == original
    assert not cn.record_path(project).exists()


def test_a_locked_file_under_skill_remove_prints_one_line(project, capsys, monkeypatch):
    assert run(capsys, "skill", "add", SKILL, str(project))[0] == 0
    before = tree(project)
    _refusing(monkeypatch, "unlink", "SKILL.md")

    code, out, err = run(capsys, "skill", "remove", SKILL, str(project))
    assert code == cli.ERROR
    line = one_line(err)
    assert "Permission denied" in line
    assert line.endswith("nothing was written")
    assert out == ""
    assert tree(project) == before


def test_an_adopt_on_a_read_only_tree_prints_one_line(project, capsys, monkeypatch):
    assert run(capsys, "mount", str(project))[0] == 0
    (project / mnt.MANIFEST_REL).unlink()
    before = tree(project)
    _refusing(monkeypatch, "write_bytes", "mount.json")

    code, out, err = run(capsys, "adopt", str(project))
    assert code == cli.ERROR
    line = one_line(err)
    assert "Permission denied" in line
    assert line.endswith("nothing was written")
    assert out == ""
    assert tree(project) == before
    assert not mnt.is_mounted(project)


def test_a_file_error_while_planning_the_mount_prints_one_line(
    project, capsys, monkeypatch,
):
    # The error comes while the mount is still reading, so nothing was
    # written and the line says so; the rehearsal and the run agree.
    (project / ".mcp.json").write_bytes(b'{\n  "mcpServers": {}\n}\n')
    before = tree(project)
    armed = _refusing(monkeypatch, "read_bytes", ".mcp.json")

    for argv in (["mount", str(project), "--dry-run"], ["mount", str(project)]):
        code, out, err = run(capsys, *argv)
        assert code == cli.ERROR, argv
        line = one_line(err)
        assert "Permission denied" in line, argv
        assert line.endswith("nothing was written"), argv
        assert out == "", argv
    armed[0] = False
    assert tree(project) == before


# --- PLAN-0017 phase 5: the skills kit refuses in words ------------------

def test_a_utf16_skills_record_prints_one_line_from_every_skill_verb(project, capsys):
    # skills-roster-04: the record in another encoding is one line on
    # stderr from list, add and remove, never a trace, and nothing moves.
    assert run(capsys, "skill", "add", SKILL, str(project))[0] == 0
    record = project / RECORD_REL
    record.write_bytes(record.read_text(encoding="utf-8").encode("utf-16"))
    before = tree(project)
    for argv in (
        ["skill", "list", str(project)],
        ["skill", "add", SKILL, str(project)],
        ["skill", "remove", SKILL, str(project)],
    ):
        code, out, err = run(capsys, *argv)
        assert code == cli.ERROR, argv
        line = one_line(err)
        assert f"{RECORD_REL} does not parse as JSON" in line, argv
        assert out == "", argv
    assert tree(project) == before


def test_a_skill_remove_whose_second_record_is_unreadable_removes_nothing(
    project, capsys,
):
    # skills-roster-05: both trees stand as found and the one line names
    # the record that stopped the press.
    assert run(capsys, "skill", "add", SKILL, str(project))[0] == 0
    second = sk.harnesses()[1]
    assert (project / second.record).is_file()
    (project / second.record).write_bytes(b"{ not json\n")
    before = tree(project)
    code, out, err = run(capsys, "skill", "remove", SKILL, str(project))
    assert code == cli.ERROR
    line = one_line(err)
    assert f"{second.record} does not parse as JSON" in line
    assert out == ""
    assert tree(project) == before


def test_a_skill_remove_stopped_by_a_second_record_says_nothing_was_written(
    project, capsys,
):
    # The clause of PLAN-0017 phase 5 prescription 3: a refusal while
    # planning writes nothing, and the one line says so, the way the
    # kit's other refusals end.
    assert run(capsys, "skill", "add", SKILL, str(project))[0] == 0
    second = sk.harnesses()[1]
    (project / second.record).write_bytes(b"{ not json\n")
    before = tree(project)
    code, out, err = run(capsys, "skill", "remove", SKILL, str(project))
    assert code == cli.ERROR
    line = one_line(err)
    assert f"{second.record} does not parse as JSON: " in line
    assert line.endswith("; nothing was written")
    assert out == ""
    assert tree(project) == before


def test_a_utf16_skills_record_under_skill_list_says_nothing_was_written(
    project, capsys,
):
    assert run(capsys, "skill", "add", SKILL, str(project))[0] == 0
    record = project / RECORD_REL
    record.write_bytes(record.read_text(encoding="utf-8").encode("utf-16"))
    before = tree(project)
    code, out, err = run(capsys, "skill", "list", str(project))
    assert code == cli.ERROR
    line = one_line(err)
    assert f"{RECORD_REL} does not parse as JSON: " in line
    assert line.endswith("; nothing was written")
    assert out == ""
    assert tree(project) == before


# --- PLAN-0018 phase 1: the engine line ---------------------------------
# BUG-0013. A printed report ends on the installed engine's version, so a
# stale mount record and a stale engine stop looking the same. The line
# stays off what a program reads — `--version` and the porcelain listing
# (Invariants 10 and 11) — off a page's own stream, and off a run that
# stopped before any report (Decided 1).

def test_every_printed_report_ends_on_the_engine_line(
    tmp_path, project, capsys, client_here,
):
    """Every runner that prints through `_emit`, over the exit codes a
    report can carry: the done reports, the refusal (3) and the no-op
    (4) alike end on the line, once."""
    client_here.write_bytes(b'{\n  "mcpServers": {}\n}\n')
    blocked = _make_project(tmp_path / "blocked")
    (blocked / "docs").mkdir()
    (blocked / "docs" / "spec.md").write_bytes(b"ours\n")
    assert mnt.version_stamp() == f"radialrig {mnt.installed_version()}"
    assert report.engine_line() == f"Engine on this machine: {mnt.version_stamp()}"

    def ends(expected: int, *argv: str) -> None:
        code, out, err = run(capsys, *argv)
        assert (code, err) == (expected, ""), argv
        assert out.endswith("\n"), argv
        assert out.splitlines()[-1] == report.engine_line(), argv
        assert out.count(report.engine_line()) == 1, argv

    ends(report.OK, "mount", str(project))
    ends(report.NOOP, "mount", str(project))            # the no-op report
    ends(report.REFUSED, "mount", str(blocked))          # the refusal report
    ends(report.OK, "upgrade", str(project))
    ends(report.OK, "skill", "list", str(project))
    ends(report.OK, "skill", "add", SKILL, str(project))
    ends(report.OK, "skill", "remove", SKILL, str(project))
    ends(report.OK, "connect", CLIENT, str(project), "--dry-run")
    (project / mnt.MANIFEST_REL).unlink()
    ends(report.OK, "adopt", str(project))
    ends(report.OK, "unmount", str(project))
    ends(report.OK, "projects")


def test_the_engine_line_stays_off_what_a_program_reads(
    tmp_path, project, capsys, client_here, monkeypatch,
):
    """`--version` stays the one stamp and a newline, the porcelain
    listing stays the bytes its builder returns, a page prints through
    its own stream, and a run that stopped before any report prints its
    one stderr line and nothing else."""
    from radialrig import manage_web, setup_web

    code, out, err = run(capsys, "--version")
    assert (code, err) == (0, "")
    assert out == f"{mnt.version_stamp()}\n"

    assert run(capsys, "mount", str(project))[0] == 0
    code, out, err = run(capsys, "projects", "--porcelain")
    assert (code, err) == (0, "")
    assert out == report.projects_porcelain(ro.listing())
    assert report.engine_line() not in out

    code, out, err = run(capsys, "mount", str(tmp_path / "nowhere"))
    assert code == cli.ERROR
    assert out == ""
    one_line(err)

    def page_serve(*args, **kwargs) -> int:
        sys.stdout.write("Open: the page's own line\n")
        return report.OK

    monkeypatch.setattr(setup_web, "serve", page_serve)
    monkeypatch.setattr(manage_web, "serve", page_serve)
    for argv in (["setup", str(project), "--no-browser"], ["manage", "--no-browser"]):
        code, out, err = run(capsys, *argv)
        assert (code, err) == (0, ""), argv
        assert out == "Open: the page's own line\n", argv


def test_a_report_then_a_file_error_still_ends_on_the_engine_line(
    project, capsys, monkeypatch,
):
    """The last net in `main` (cli-connect-02) after a printed report:
    the report and the engine line on stdout, the one line on stderr,
    and the exit code the net returns. The stop is injected at the
    roster's side entry, the seam every mount reaches after its report;
    it stands for any file error a runner raises once it has printed."""
    def stopped(*args, **kwargs):
        raise OSError("the roster's disk went away")

    monkeypatch.setattr(ro, "record_seen", stopped)
    code, out, err = run(capsys, "mount", str(project))
    assert code == cli.ERROR
    assert one_line(err) == "radialrig: the roster's disk went away"
    assert report.next_step(project)[0] in out
    assert out.splitlines()[-1] == report.engine_line()
    assert out.count(report.engine_line()) == 1


def test_the_mount_names_a_validate_command_that_runs_here(
    tmp_path, capsys, monkeypatch,
):
    """Outside git the validator stops and asks for `--no-baseline`, so
    the mount's closing line names the flag there and only there, and
    the command it names passes on the fresh mount (an outside tester,
    2026-10-01). A repository with no commit yet needs no flag.

    The ceiling keeps git from finding a repository above the temporary
    folder, so the plain project is outside git wherever this runs.
    """
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    plain = _make_project(tmp_path / "plain")
    tracked = _make_project(tmp_path / "tracked")
    subprocess.run(["git", "init", "-q", "."], cwd=tracked, check=True)
    for root, said, other in (
        (plain, report.NEXT_STEP_NO_GIT, report.NEXT_STEP),
        (tracked, report.NEXT_STEP, report.NEXT_STEP_NO_GIT),
    ):
        code, out, _ = run(capsys, "mount", str(root))
        assert code == report.OK
        assert said in out
        assert other not in out
        _, command = report.next_step(root)
        assert command in said
        monkeypatch.setattr(sys, "argv", [*command.split(), "--root", str(root)])
        assert validate_docs.main() == 0
    # Without the flag the plain project stops, which is why the line
    # names it.
    monkeypatch.setattr(sys, "argv", ["radialrig-validate", "--root", str(plain)])
    assert validate_docs.main() == 1
