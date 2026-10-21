"""Tests for `radialrig connect` and `unconnect` — PLAN-0007 Phase 2.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_connect.py -q

Two oracles run here. The first is the round trip: connect then unconnect
leaves the client's settings file byte-identical, over a table of the
shapes a real settings file arrives in. The second is the refusal matrix:
a connect never overwrites an entry it did not write, and it is
all-or-nothing across every copy — writing the visible copy and refusing
the hidden one would leave the two disagreeing.

Nothing here may reach this machine's own client. The autouse fixture
below removes the two environment variables discovery reads, so a test
that forgets to inject finds nothing and errors rather than editing a
real configuration.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import pathlib
import re
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from radialrig import cli
from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import report
from radialrig import roster as ro
from radialrig import setup_web
from radialrig.tests.test_mount import (  # noqa: F401 - helpers, by name
    MOUNT_DAY,
    _make_project,
    tree,
)

CONNECT_DAY = dt.date(2026, 8, 19)
CLIENT = cn.DEFAULT_CLIENT

# The package family the Store build uses, restated rather than read from
# the table it is used to check. The glob step in the table is what this
# fixture pins: a rename of the family must cost no code change.
STORE_FAMILY = "AnthropicClaude_1a2b3c4d5e6f"

# The roster's environment variable on the platform this suite runs on,
# read from the table itself so every fixture serves whichever row the
# machine serves (PLAN-0009 Phase 2 prescription 5). The first source is
# the one a fixture sets: the later sources are that row's fallbacks,
# and the table test in test_roster.py pins their order.
def store_row() -> ro.Store:
    """The `STORES` row serving the platform this suite runs on."""
    for store in ro.STORES:
        if sys.platform.startswith(store.platform):
            return store
    raise RuntimeError("no STORES row serves this platform")


STORE_VAR = store_row().sources[0].var


def clear_store_env(monkeypatch) -> None:
    """No roster path on any platform: every store variable removed."""
    for store in ro.STORES:
        for source in store.sources:
            monkeypatch.delenv(source.var, raising=False)


# The shapes a settings file arrives in. The `no-servers` row is the
# shape this machine's own configuration is in.
CONFIG_HOSTS = {
    "indent2": b'{\n  "mcpServers": {\n    "other": {"command": "x", "args": []}\n  }\n}\n',
    "indent4": b'{\n    "mcpServers": {\n        "other": {"command": "x"}\n    }\n}\n',
    "tabs": b'{\n\t"mcpServers": {\n\t\t"other": {"command": "x"}\n\t}\n}\n',
    "crlf": b'{\r\n  "mcpServers": {\r\n    "other": {"command": "x"}\r\n  }\r\n}\r\n',
    "empty-root": b"{}\n",
    "no-servers": b'{\n  "theme": "dark",\n  "locale": "en-GB"\n}\n',
    "empty-servers": b'{\n  "mcpServers": {}\n}\n',
    "one-sibling": b'{\n  "mcpServers": {\n    "another": {"command": "y", "args": []}\n  }\n}\n',
    "trailing-newline-absent": b'{\n  "mcpServers": {\n    "other": {"command": "x"}\n  }\n}',
}


# --- fixtures ---------------------------------------------------------

@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    """Belt and braces: discovery can never reach the real user profile."""
    for name in ("APPDATA", "LOCALAPPDATA", "HOME"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path / "sample-project")


@pytest.fixture()
def command(tmp_path, monkeypatch) -> Path:
    """Stand-in console scripts, so no test reads the command path.

    One per server, under the names the package installs, so the two
    entries a connect writes carry two different commands the way real
    ones do. Answers the read script's path, as it always did.
    """
    scripts = tmp_path / "bin"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in (cn.READ_COMMAND, cn.WRITE_COMMAND):
        (scripts / f"{name}.exe").write_bytes(b"")
    monkeypatch.setattr(cn, "_which", lambda name: str(scripts / f"{name}.exe"))
    return scripts / f"{cn.READ_COMMAND}.exe"


def fake_client(tmp_path: Path, *, store: bool = False) -> dict:
    """The directories a client install leaves, and the env to find them.

    The mapping also carries this platform's roster variable, pointed at
    the same base, so the roster a verb keeps as a side entry lands
    under the fixture on every platform the `STORES` table serves.
    """
    roaming = tmp_path / "profile" / "Roaming"
    (roaming / "Claude").mkdir(parents=True, exist_ok=True)
    env = {"APPDATA": str(roaming)}
    env.setdefault(STORE_VAR, str(roaming))
    if store:
        local = tmp_path / "profile" / "Local"
        store_dir(local).mkdir(parents=True, exist_ok=True)
        env["LOCALAPPDATA"] = str(local)
    return env


def store_dir(local: Path) -> Path:
    return local / "Packages" / STORE_FAMILY / "LocalCache" / "Roaming" / "Claude"


def roaming_config(env: dict) -> Path:
    return Path(env["APPDATA"]) / "Claude" / "claude_desktop_config.json"


def store_config(env: dict) -> Path:
    return store_dir(Path(env["LOCALAPPDATA"])) / "claude_desktop_config.json"


@pytest.fixture()
def env(tmp_path, monkeypatch) -> dict:
    """One fake client, injected both ways: as a mapping for the engine's
    `env=` seam and into os.environ for the command surface, which passes
    no mapping of its own."""
    mapping = fake_client(tmp_path)
    for name, value in mapping.items():
        monkeypatch.setenv(name, value)
    return mapping


@pytest.fixture()
def both(tmp_path, monkeypatch) -> dict:
    """The same, with the Store build's hidden second copy."""
    mapping = fake_client(tmp_path, store=True)
    for name, value in mapping.items():
        monkeypatch.setenv(name, value)
    return mapping


def marks(plan) -> dict[str, tuple[str, str]]:
    return {d.path: (d.verb, d.code) for d in plan.dispositions}


def record_of(project: Path) -> dict:
    return json.loads(cn.record_path(project).read_text(encoding="utf-8"))


def rewrite_record(project: Path, body: dict) -> None:
    cn.record_path(project).write_bytes(mnt._dump_manifest(body))


def servers(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["mcpServers"]


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- the lifecycle -----------------------------------------------------

def test_dry_run_and_run_agree(project, env, command):
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    before = config.read_bytes()

    planned = cn.connect_plan(project, env=env, today=CONNECT_DAY)
    assert config.read_bytes() == before, "the rehearsal wrote something"
    assert not cn.record_path(project).exists()

    real = cn.connect(project, env=env, today=CONNECT_DAY)
    assert marks(planned) == marks(real)
    assert planned.key == real.key
    assert planned.entry == real.entry
    assert config.read_bytes() != before


@pytest.mark.parametrize("name", sorted(CONFIG_HOSTS))
def test_the_round_trip_is_byte_identical(project, env, command, name):
    config = roaming_config(env)
    original = CONFIG_HOSTS[name]
    config.write_bytes(original)

    result = cn.connect(project, env=env, today=CONNECT_DAY)
    assert config.read_bytes() != original
    assert result.key in servers(config)

    plan = cn.unconnect(project, env=env)
    assert plan.complete is True
    assert config.read_bytes() == original
    assert not cn.record_path(project).exists()


def test_a_flipped_settings_file_restores_the_pre_connect_bytes(project, env, command):
    # BUG-0011, review 2026-09-05: a checkout that rewrote the file's
    # endings after the connect. The region is found in the flipped
    # form, and "restored" writes the bytes the record hashes.
    config = roaming_config(env)
    original = CONFIG_HOSTS["indent2"]
    config.write_bytes(original)
    cn.connect(project, env=env, today=CONNECT_DAY)
    config.write_bytes(config.read_bytes().replace(b"\n", b"\r\n"))

    plan = cn.unconnect(project, env=env)
    assert plan.complete is True
    assert mnt.CONNECT_RESTORED in {d.code for d in plan.dispositions}
    assert config.read_bytes() == original


def test_a_flipped_settings_file_edited_elsewhere_keeps_its_crs(project, env, command):
    # The same checkout, then an edit of the project's own outside the
    # region: the region alone comes out, every other byte stays.
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    cn.connect(project, env=env, today=CONNECT_DAY)
    flipped = config.read_bytes().replace(b"\n", b"\r\n")
    config.write_bytes(flipped.replace(b'"command": "x"', b'"command": "y"', 1))

    plan = cn.unconnect(project, env=env)
    assert mnt.CONNECT_EXCISED_REGION in {d.code for d in plan.dispositions}
    assert config.read_bytes() == CONFIG_HOSTS["indent2"].replace(
        b"\n", b"\r\n").replace(b'"command": "x"', b'"command": "y"', 1)


def test_a_file_without_an_mcp_servers_key_gets_the_object_inserted(
    project, env, command,
):
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["no-servers"])
    result = cn.connect(project, env=env, today=CONNECT_DAY)

    after = config.read_bytes()
    # Every preference key the client wrote survives byte for byte: the
    # region is inserted, nothing is re-serialized.
    assert b'"theme": "dark"' in after
    assert b'"locale": "en-GB"' in after
    loaded = json.loads(after.decode("utf-8"))
    assert loaded == {
        "theme": "dark", "locale": "en-GB",
        "mcpServers": result.entries,
    }
    # Both servers, read first, under keys that share the client's prefix.
    assert list(result.entries) == [result.key, result.key + cn.WRITE_KEY_SUFFIX]
    assert result.entries[result.key] == result.entry
    assert marks(result)[config.as_posix()] == (mnt.MERGED, mnt.CONNECT_MERGED)


def test_a_changed_file_exits_5(project, env, command, capsys):
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    result = cn.connect(project, env=env, today=CONNECT_DAY)
    config.write_bytes(config.read_bytes() + b"trailing bytes the client added")

    code, out, _ = run(capsys, "unconnect", CLIENT, str(project))
    assert code == 5
    assert report.CONNECT_LEFT_TAIL in out
    # The head's counts are files, not entries: the entry came out of
    # this file, and the file still counts as left because the client's
    # own edit stays. The excised row states the removal itself.
    assert "0 settings file(s) reversed, 1 left." in out
    after = config.read_bytes()
    assert result.key not in json.loads(
        after.decode("utf-8").replace("trailing bytes the client added", "")
    )["mcpServers"]
    assert after.endswith(b"trailing bytes the client added")
    assert not cn.record_path(project).exists()


def test_a_reformatted_file_takes_the_structural_path(project, env, command):
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    result = cn.connect(project, env=env, today=CONNECT_DAY)
    loaded = json.loads(config.read_text(encoding="utf-8"))
    loaded["other-top-level"] = {"kept": True}
    config.write_bytes((json.dumps(loaded, indent=8) + "\n").encode("utf-8"))

    plan = cn.unconnect(project, env=env)
    assert marks(plan)[config.as_posix()] == (mnt.LEFT, mnt.CONNECT_RESERIALIZED)
    assert plan.complete is False
    after = json.loads(config.read_text(encoding="utf-8"))
    assert result.key not in after["mcpServers"]
    assert after["mcpServers"]["other"] == {"command": "x", "args": []}
    assert after["other-top-level"] == {"kept": True}


def test_both_store_copies_are_written(project, both, command):
    visible = roaming_config(both)
    hidden = store_config(both)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    hidden.write_bytes(CONFIG_HOSTS["empty-servers"])

    result = cn.connect(project, env=both, today=CONNECT_DAY)
    assert len(result.copies) == 2
    assert {plan.copy.label for plan in result.copies} == {"roaming", "store"}
    assert result.key in servers(visible)
    assert result.key in servers(hidden)
    assert len(record_of(project)["copies"]) == 2

    plan = cn.unconnect(project, env=both)
    assert plan.complete is True
    assert visible.read_bytes() == CONFIG_HOSTS["indent2"]
    assert hidden.read_bytes() == CONFIG_HOSTS["empty-servers"]


def test_a_re_connect_keeps_the_entry_it_already_wrote(project, both, command):
    """The mixed run: one copy still registered, one to write again.

    The record the second connect writes carries what the first one wrote
    for the copy that came back current. Without that the reversal has no
    way to remove an entry this kit put there, and it stays forever.
    """
    visible = roaming_config(both)
    hidden = store_config(both)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    hidden.write_bytes(CONFIG_HOSTS["empty-servers"])
    first = cn.connect(project, env=both, today=CONNECT_DAY)

    # The client rewrote its hidden copy and lost the entries there. The
    # visible copy is untouched, so the next connect sees one of each.
    loaded = json.loads(hidden.read_text(encoding="utf-8"))
    for key in first.keys:
        del loaded["mcpServers"][key]
    hidden.write_bytes((json.dumps(loaded, indent=2) + "\n").encode("utf-8"))

    second = cn.connect(project, env=both, today=CONNECT_DAY)
    assert {plan.copy.label: plan.disposition.verb for plan in second.copies} == {
        "roaming": mnt.CURRENT, "store": mnt.MERGED,
    }
    recorded = {item["label"]: item for item in record_of(project)["copies"]}
    assert recorded["roaming"]["written"] is True
    assert recorded["roaming"]["insert"], "the first connect's region was dropped"
    assert recorded["roaming"]["pre_connect_sha256"]

    plan = cn.unconnect(project, env=both)
    assert plan.complete is True
    assert visible.read_bytes() == CONFIG_HOSTS["indent2"]
    assert first.key not in servers(hidden)


def test_both_servers_are_registered_and_both_come_out(project, env, command):
    """PLAN-0012 phase 1: one connect, two entries, one region."""
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    result = cn.connect(project, env=env, today=CONNECT_DAY)

    write_key = result.key + cn.WRITE_KEY_SUFFIX
    assert result.keys == (result.key, write_key)
    on_disk = servers(config)
    assert on_disk[result.key]["command"].endswith(f"{cn.READ_COMMAND}.exe")
    assert on_disk[write_key]["command"].endswith(f"{cn.WRITE_COMMAND}.exe")
    assert on_disk[write_key]["env"] == on_disk[result.key]["env"]

    body = record_of(project)
    assert body["key"] == result.key
    assert body["entry"] == result.entry
    assert body["entries"] == result.entries
    region = body["copies"][0]["insert"]
    assert region.count(f'"{result.key}":') == 1
    assert region.count(f'"{write_key}":') == 1
    row = marks(result)[config.as_posix()]
    assert row == (mnt.MERGED, mnt.CONNECT_MERGED)

    plan = cn.unconnect(project, env=env)
    assert plan.keys == result.keys
    assert plan.complete is True
    assert config.read_bytes() == CONFIG_HOSTS["indent2"]


def test_a_record_from_before_the_write_server_still_reverses(project, env, command):
    """An older record names one key and one entry, and no `entries`."""
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    result = cn.connect(project, env=env, today=CONNECT_DAY)
    write_key = result.key + cn.WRITE_KEY_SUFFIX

    # The client reformatted its file and the region is gone; the record
    # is the old shape, so only the key it names comes out structurally.
    body = record_of(project)
    body.pop("entries")
    rewrite_record(project, body)
    loaded = json.loads(config.read_text(encoding="utf-8"))
    config.write_bytes((json.dumps(loaded, indent=8) + "\n").encode("utf-8"))

    plan = cn.unconnect(project, env=env)
    assert plan.keys == (result.key,)
    assert marks(plan)[config.as_posix()] == (mnt.LEFT, mnt.CONNECT_RESERIALIZED)
    after = servers(config)
    assert result.key not in after
    assert write_key in after


def test_a_copy_holding_one_of_the_two_keys_refuses(project, env, command, capsys):
    """The state a connect made before the write server was registered
    leaves. The remedy is the reversal that record still knows."""
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    result = cn.connect(project, env=env, today=CONNECT_DAY)
    loaded = json.loads(config.read_text(encoding="utf-8"))
    del loaded["mcpServers"][result.key + cn.WRITE_KEY_SUFFIX]
    config.write_bytes((json.dumps(loaded, indent=2) + "\n").encode("utf-8"))
    before = config.read_bytes()

    code, out, _ = run(capsys, "connect", CLIENT, str(project))
    assert code == 3
    assert mnt.REASONS[mnt.CONNECT_ENTRY_STALE] in out
    assert f"radialrig unconnect {CLIENT}" in out
    assert config.read_bytes() == before


def test_the_reports_name_both_keys(project, env, command, capsys):
    roaming_config(env).write_bytes(CONFIG_HOSTS["indent2"])
    code, out, _ = run(capsys, "connect", CLIENT, str(project))
    assert code == 0
    key = record_of(project)["key"]
    assert out.splitlines()[0] == (
        f"Registered {key} and {key}{cn.WRITE_KEY_SUFFIX} with "
        f"{cn.client_for(CLIENT).label}")
    code, out, _ = run(capsys, "unconnect", CLIENT, str(project))
    assert code == 0
    assert out.splitlines()[0].startswith("Removed the radialrig entries from")


def test_a_deleted_record_is_written_again(project, env, command, capsys):
    """A project copied without its `.radialrig/`, or one a reader wiped.

    The record is the only thing that can remove the entry, so the one
    state where re-running the command should rebuild it is the state
    where it does.
    """
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    first = cn.connect(project, env=env, today=CONNECT_DAY)
    settled = config.read_bytes()
    shutil.rmtree(project / ".radialrig")

    code, out, _ = run(capsys, "connect", CLIENT, str(project))
    assert code == 0, "a missing record is not nothing to do"
    assert cn.record_path(project).is_file()
    assert config.read_bytes() == settled, "the entry was already in place"
    assert mnt.REASONS[mnt.CONNECT_CURRENT] in out
    assert cn.CONNECT_REL in out, "the record row is not reported"

    # The adopted record claims no insert region and no earlier hash, so
    # the reversal takes the structural path and says so: exit 5.
    code, out, _ = run(capsys, "unconnect", CLIENT, str(project))
    assert code == 5
    assert first.key not in servers(config)
    assert not cn.record_path(project).exists()


def test_a_second_name_refuses_rather_than_orphaning_the_first(
    project, env, command, capsys,
):
    """`--scope` on a registered project would leave the first entry with
    no record able to remove it."""
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    first = cn.connect(project, env=env, scope="alpha", today=CONNECT_DAY)
    settled = config.read_bytes()

    code, out, _ = run(capsys, "connect", CLIENT, str(project), "--scope", "beta")
    assert code == 3
    assert mnt.REASONS[mnt.CONNECT_RECORD_OTHER_KEY] in out
    assert f"radialrig unconnect {CLIENT}" in out
    assert report.CONNECT_SCOPE_REMEDY not in out
    assert config.read_bytes() == settled
    assert set(servers(config)) == {"other", *first.keys}
    assert record_of(project)["key"] == first.key


def test_one_unreadable_copy_refuses_both(project, both, command):
    visible = roaming_config(both)
    hidden = store_config(both)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    hidden.write_bytes(b"{ this is not JSON any more\n")

    with pytest.raises(cn.ConnectRefused) as exc:
        cn.connect(project, env=both, today=CONNECT_DAY)
    codes = {d.code for d in exc.value.conflicts}
    assert codes == {mnt.CONNECT_UNPARSED}
    assert visible.read_bytes() == CONFIG_HOSTS["indent2"]
    assert not cn.record_path(project).exists()


def test_a_taken_key_refuses(project, env, command, capsys):
    config = roaming_config(env)
    key = cn.entry_key(cn.client_for(CLIENT), project.name)
    theirs = {"mcpServers": {key: {"command": "their-own-server"}}}
    config.write_bytes((json.dumps(theirs, indent=2) + "\n").encode("utf-8"))
    before = config.read_bytes()

    code, out, _ = run(capsys, "connect", CLIENT, str(project))
    assert code == 3
    assert report.connect_refusal_head(1) in out
    assert mnt.REASONS[mnt.CONNECT_KEY_HOST_OWNED] in out
    # This project carries no record, so the reversal has nothing to
    # reverse and is not offered: the name is what the reader can change.
    assert report.CONNECT_SCOPE_REMEDY in out
    assert "radialrig unconnect" not in out
    assert config.read_bytes() == before
    assert not cn.record_path(project).exists()


def test_the_refusal_remedy_names_one_command_per_case(project):
    """One command per case, never two. A project with a record reverses
    it; a project without one cannot — the reversal answers "not
    registered" there — and is offered the name instead."""
    def remedy(code: str) -> str:
        return report.connect_refusal_remedy(
            CLIENT, project, (mnt.Disposition("p", mnt.LEFT, code),))

    reversal = f'Run "radialrig unconnect {CLIENT} {project}" first, then this command.'
    assert remedy(mnt.CONNECT_ENTRY_STALE) == reversal
    assert remedy(mnt.CONNECT_RECORD_OTHER_KEY) == reversal
    assert remedy(mnt.CONNECT_KEY_HOST_OWNED) == report.CONNECT_SCOPE_REMEDY
    assert remedy(mnt.CONNECT_UNPARSED) == report.CONNECT_REPAIR_REMEDY


def test_a_stale_entry_refuses_and_names_the_reversal(
    project, env, tmp_path, monkeypatch, command, capsys,
):
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    cn.connect(project, env=env, today=CONNECT_DAY)

    # The installed command moved. The key is this project's own, and the
    # remedy is unconnect then connect rather than an update path.
    moved = tmp_path / "elsewhere" / "radialrig-read.exe"
    moved.parent.mkdir(parents=True, exist_ok=True)
    moved.write_bytes(b"")
    monkeypatch.setattr(cn, "_which", lambda name: str(moved))

    before = config.read_bytes()
    code, out, _ = run(capsys, "connect", CLIENT, str(project))
    assert code == 3
    assert report.connect_refusal_head(1) in out
    assert mnt.REASONS[mnt.CONNECT_ENTRY_STALE] in out
    assert f"radialrig unconnect {CLIENT}" in out
    assert report.CONNECT_SCOPE_REMEDY not in out
    assert config.read_bytes() == before


def test_a_second_connect_is_a_no_op(project, env, command, capsys):
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    assert run(capsys, "connect", CLIENT, str(project))[0] == 0
    settled = config.read_bytes()
    recorded = record_of(project)

    code, out, _ = run(capsys, "connect", CLIENT, str(project))
    assert code == 4
    assert report.CONNECT_CURRENT_TAIL in out
    # Nothing changed, so nothing needs restarting either.
    assert cn.client_for(CLIENT).restart not in out
    assert config.read_bytes() == settled
    # No second timestamp: a project that ran the command twice is not a
    # project that was registered twice.
    assert record_of(project) == recorded


def test_the_scope_comes_from_the_mount_record(project, env, command):
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])

    bare = cn.connect_plan(project, env=env, today=CONNECT_DAY)
    assert bare.key == f"radialrig-{project.name}"

    mnt.mount(project, scope="acme", today=MOUNT_DAY)
    mounted = cn.connect_plan(project, env=env, today=CONNECT_DAY)
    assert mounted.key == "radialrig-acme"

    told = cn.connect_plan(project, env=env, scope="other", today=CONNECT_DAY)
    assert told.key == "radialrig-other"


def test_an_unreadable_mount_record_stops_the_connect(project, env, command, capsys):
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    (project / ".radialrig").mkdir()
    (project / mnt.MANIFEST_REL).write_bytes(b"{ not json\n")
    before = config.read_bytes()

    code, out, err = run(capsys, "connect", CLIENT, str(project))
    assert code == 1
    assert out == ""
    assert mnt.MANIFEST_REL in err
    assert config.read_bytes() == before
    assert not cn.record_path(project).exists()


def test_the_entry_carries_the_full_command_path_and_the_project_root(
    project, env, command,
):
    roaming_config(env).write_bytes(CONFIG_HOSTS["indent2"])
    result = cn.connect(project, env=env, today=CONNECT_DAY)
    assert result.entry["command"] == str(command.resolve())
    assert Path(result.entry["command"]).is_absolute()
    assert result.entry["args"] == []
    assert result.entry["env"][cn.ROOT_ENV] == str(project.resolve())


def test_an_unresolvable_read_command_is_an_environment_error(
    project, env, tmp_path, monkeypatch, capsys,
):
    roaming_config(env).write_bytes(CONFIG_HOSTS["indent2"])
    before = roaming_config(env).read_bytes()
    monkeypatch.setattr(cn, "_which", lambda name: None)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "nowhere" / "python.exe"))
    monkeypatch.setattr(sys, "argv", [""])

    code, out, err = run(capsys, "connect", CLIENT, str(project))
    assert code == 1
    assert out == ""
    assert "uv tool install radialrig" in err
    # BUG-0010: the install replaces executables a running app holds.
    assert (
        "close the RadialRig app and any manage page before that install, "
        "they hold the executables it replaces"
    ) in err
    assert roaming_config(env).read_bytes() == before
    assert not cn.record_path(project).exists()


def test_no_client_on_this_machine_is_an_environment_error(
    project, tmp_path, command, capsys,
):
    # An empty profile: the variable resolves, and no client directory
    # stands under it.
    (tmp_path / "empty").mkdir()
    with pytest.raises(cn.ConnectError) as exc:
        cn.connect(project, env={"APPDATA": str(tmp_path / "empty")})
    assert str(exc.value) == report.CONNECT_NO_CLIENT

    code, out, err = run(capsys, "connect", CLIENT, str(project))
    assert code == 1
    assert out == ""
    assert "no settings file for this client" in err
    assert not cn.record_path(project).exists()


def test_discovery_reads_only_the_given_environment(tmp_path):
    mapping = fake_client(tmp_path, store=True)
    client = cn.client_for(CLIENT)
    found = cn.detect(client, env=mapping)
    assert {copy.label for copy in found} == {"roaming", "store"}
    for copy in found:
        assert tmp_path.resolve() in copy.path.parents
    # The Store row's one glob step: the package family is discovered,
    # never spelled.
    hidden = next(copy for copy in found if copy.label == "store")
    assert STORE_FAMILY in hidden.path.parts
    assert hidden.path == store_config(mapping).resolve()

    # No variable, nothing found; and a variable pointing at a profile
    # with no client directory finds nothing either.
    assert cn.detect(client, env={}) == ()
    (tmp_path / "bare").mkdir()
    assert cn.detect(client, env={"APPDATA": str(tmp_path / "bare")}) == ()


def test_the_config_location_round_trips_from_home(tmp_path, project, command):
    """PLAN-0009 Phase 2 prescription 2: the Linux client's one settings
    file, found from `HOME` under `.config/Claude`. Discovery is
    table-driven and carries no platform gate, so the row is asserted on
    every platform the suite runs: a copy is found only where the
    mapping names `HOME` and the parent directory exists.
    """
    home = tmp_path / "linux-profile"
    (home / ".config" / "Claude").mkdir(parents=True)
    mapping = {"HOME": str(home)}
    client = cn.client_for(CLIENT)

    found = cn.detect(client, env=mapping)
    assert [copy.label for copy in found] == ["config"]
    config = home / ".config" / "Claude" / "claude_desktop_config.json"
    assert found[0].path == config.resolve()

    # The round trip against that one copy: connect inserts the entry
    # and records the copy under its label, unconnect returns the file
    # to its pre-connect bytes.
    config.write_bytes(CONFIG_HOSTS["no-servers"])
    before = config.read_bytes()
    cn.connect(project, env=mapping, today=CONNECT_DAY)
    body = json.loads(config.read_text(encoding="utf-8"))
    assert any(key.startswith(client.key_prefix) for key in body["mcpServers"])
    record = record_of(project)
    assert [item["label"] for item in record["copies"]] == ["config"]
    cn.unconnect(project, env=mapping)
    assert config.read_bytes() == before

    # A bare home directory is a machine with no client: nothing found.
    bare = tmp_path / "bare-home"
    bare.mkdir()
    assert cn.detect(client, env={"HOME": str(bare)}) == ()


def test_the_record_holds_no_bytes_the_client_wrote(project, env, command):
    config = roaming_config(env)
    config.write_bytes((json.dumps({
        "theme": "HOSTSECRET",
        "mcpServers": {"other": {
            "command": "x", "args": [], "env": {"SOME_API_KEY": "HOSTSECRET"},
        }},
    }, indent=2) + "\n").encode("utf-8"))

    cn.connect(project, env=env, today=CONNECT_DAY)
    assert b"HOSTSECRET" not in cn.record_path(project).read_bytes()


def test_the_record_refuses_a_path_outside_the_known_locations(
    project, env, command, capsys,
):
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    cn.connect(project, env=env, today=CONNECT_DAY)
    settled = config.read_bytes()

    body = record_of(project)
    body["copies"][0]["path"] = "C:/Windows/System32/drivers/etc/hosts"
    rewrite_record(project, body)

    code, out, err = run(capsys, "unconnect", CLIENT, str(project))
    assert code == 1
    assert out == ""
    assert "nothing was changed" in err
    assert config.read_bytes() == settled
    assert cn.record_path(project).is_file()


def test_the_record_refuses_a_newer_schema(project, env, command, capsys):
    roaming_config(env).write_bytes(CONFIG_HOSTS["indent2"])
    cn.connect(project, env=env, today=CONNECT_DAY)
    body = record_of(project)
    body["schema"] = cn.CONNECT_SCHEMA + 1
    rewrite_record(project, body)

    code, out, err = run(capsys, "unconnect", CLIENT, str(project))
    assert code == 1
    assert out == ""
    assert "newer radialrig" in err
    assert cn.record_path(project).is_file()


def test_a_failed_write_leaves_both_copies_as_found(
    project, both, command, monkeypatch,
):
    visible = roaming_config(both)
    hidden = store_config(both)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    hidden.write_bytes(CONFIG_HOSTS["empty-servers"])
    before = (visible.read_bytes(), hidden.read_bytes())

    original = pathlib.Path.write_bytes
    seen: list[Path] = []

    def flaky(self, data):
        seen.append(self)
        if len(seen) == 2:
            raise OSError("the second copy would not open")
        return original(self, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", flaky)
    with pytest.raises(cn.WriteStopped):
        cn.connect(project, env=both, today=CONNECT_DAY)
    monkeypatch.undo()

    assert (visible.read_bytes(), hidden.read_bytes()) == before
    assert not cn.record_path(project).exists()


def test_a_created_settings_file_is_removed_on_unconnect(project, env, command):
    config = roaming_config(env)
    assert not config.exists()
    before = tree(project)

    result = cn.connect(project, env=env, today=CONNECT_DAY)
    assert marks(result)[config.as_posix()] == (mnt.CREATED, mnt.CONNECT_CREATED)
    assert config.is_file()
    assert servers(config) == result.entries

    plan = cn.unconnect(project, env=env)
    assert plan.complete is True
    assert not config.exists()
    assert config.parent.is_dir(), "the client's own directory is not this kit's"
    # The project is as it was: the record went, and so did the directory
    # the record's own write created.
    assert tree(project) == before


# --- a record steers nothing this kit did not register (cli-connect-01) --

def test_a_record_naming_another_servers_region_removes_nothing(
    project, env, command,
):
    # The recorded insert is another server's entry and the file never
    # held a radialrig key: the region stays, whatever the record says.
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    cn.connect(project, env=env, today=CONNECT_DAY)
    host = b'{\n  "mcpServers": {\n    "github": {"command": "x", "args": []}\n  }\n}\n'
    config.write_bytes(host)
    body = record_of(project)
    body["copies"][0]["insert"] = '    "github": {"command": "x", "args": []}\n'
    rewrite_record(project, body)

    plan = cn.unconnect(project, env=env)
    assert marks(plan)[config.as_posix()] == (mnt.LEFT, mnt.CONNECT_RECORD_UNPROVEN)
    assert config.read_bytes() == host


def test_a_record_claiming_a_file_without_a_radialrig_key_removes_nothing(
    project, env, command,
):
    # `file_created` plus the digest of a plausible near-default file
    # would delete a settings file this kit never wrote.
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    cn.connect(project, env=env, today=CONNECT_DAY)
    plain = b'{\n  "mcpServers": {}\n}\n'
    config.write_bytes(plain)
    body = record_of(project)
    body["copies"][0]["file_created"] = True
    body["copies"][0]["sha256"] = mnt._sha256(plain)
    rewrite_record(project, body)

    plan = cn.unconnect(project, env=env)
    assert marks(plan)[config.as_posix()] == (mnt.LEFT, mnt.CONNECT_RECORD_UNPROVEN)
    assert config.read_bytes() == plain


def test_a_recorded_region_that_names_no_registered_key_removes_nothing(
    project, env, command,
):
    # The file does hold this kit's entries, but the recorded region is
    # a neighbour's: the excision would take the neighbour, not ours.
    config = roaming_config(env)
    config.write_bytes(CONFIG_HOSTS["indent2"])
    cn.connect(project, env=env, today=CONNECT_DAY)
    settled = config.read_bytes()
    body = record_of(project)
    body["copies"][0]["insert"] = '    "other": {"command": "x", "args": []}\n'
    rewrite_record(project, body)

    plan = cn.unconnect(project, env=env)
    assert marks(plan)[config.as_posix()] == (mnt.LEFT, mnt.CONNECT_RECORD_UNPROVEN)
    assert config.read_bytes() == settled
    assert "other" in servers(config)


# --- the vocabulary and the source rules -------------------------------

def test_every_connect_code_has_a_wording():
    """A code missing from REASONS renders as the bare code. This is the
    one trap the closed vocabulary has, stated once."""
    assert mnt.CONNECT_CODES
    for code in mnt.CONNECT_CODES:
        assert code in mnt.REASONS, code
        text = mnt.reason_text(mnt.Disposition("p", mnt.LEFT, code, "mcpServers.k"))
        assert text != code, code
        assert "{" not in text, code


def test_every_connect_code_is_in_the_closed_set():
    """The trap the test above cannot see: a code declared, used, and left
    out of `CONNECT_CODES` renders as the bare code and is never checked.
    The set is read out of the source rather than trusted."""
    source = Path(mnt.__file__).read_text(encoding="utf-8")
    declared = set(re.findall(r"^(CONNECT_[A-Z_]+)\s*=", source, re.M))
    assert declared == set(mnt.CONNECT_CODES)


def test_the_client_name_and_label_live_only_in_the_client_table():
    """D-34 rule 2: a second client costs one row, not one row plus a
    branch. The name and the label are data in that row; every module
    that shows them reads them from the table.

    Every string literal this module's code carries, docstrings aside: a
    literal is what a branch would compare against, and prose that names
    the first supported client is not one. The two renderers are read
    whole, because a constant naming the client would land there first.
    """
    client = cn.client_for(CLIENT)
    source = Path(cn.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    documents = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            documents.add(id(first.value))
    naming = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in documents
        and (client.name in node.value or client.label in node.value)
    ]
    # The three fields of the one table row, and nothing else anywhere.
    assert sorted(naming) == sorted([client.name, client.label, client.restart])
    for module in (report, setup_web):
        rendered = Path(module.__file__).read_text(encoding="utf-8")
        assert client.name not in rendered
        assert client.label not in rendered


def test_the_negative_names_every_client_the_table_carries(monkeypatch):
    """A project registered with no client says which it is not
    registered with: the one label today, and every label joined with
    "or" once the table grows (an outside tester, 2026-10-01)."""
    only = cn.client_for(CLIENT)
    assert cn.label_phrase() == only.label
    second = replace(only, name="second-client", label="Second App")
    third = replace(only, name="third-client", label="Third App")
    monkeypatch.setattr(cn, "CLIENTS", {only.name: only, second.name: second})
    assert cn.label_phrase() == f"{only.label} or Second App"
    monkeypatch.setattr(
        cn, "CLIENTS", {c.name: c for c in (only, second, third)})
    assert cn.label_phrase() == f"{only.label}, Second App or Third App"


def test_bare_connect_prints_usage(project, capsys):
    before = tree(project)
    for verb in ("connect", "unconnect"):
        code, out, err = run(capsys, verb)
        assert code == 0
        assert out == cli.USAGE
        assert err == ""
    assert tree(project) == before


def test_an_unknown_client_lists_the_clients(project, capsys):
    for verb in ("connect", "unconnect"):
        code, out, err = run(capsys, verb, "no-such-client", str(project))
        assert code == 1, verb
        assert out == ""
        assert "registers no client named 'no-such-client'" in err
        assert CLIENT in err


def test_an_unregistered_project_says_so(project, env, command, capsys):
    before = tree(project)
    code, out, err = run(capsys, "unconnect", CLIENT, str(project))
    assert code == 4
    assert err == ""
    assert report.CONNECT_NOT_CONNECTED in out
    assert tree(project) == before


def test_the_restart_line_is_the_last_line_of_both_real_runs(
    project, env, command, capsys,
):
    """Prescription 6 for the connect, and the symmetric line for the
    reversal: a client still running keeps the removed server alive."""
    restart = cn.client_for(CLIENT).restart
    roaming_config(env).write_bytes(CONFIG_HOSTS["indent2"])

    code, out, _ = run(capsys, "connect", CLIENT, str(project), "--dry-run")
    assert code == 0
    # The report's own last line; the engine line ends every printed
    # report after it (PLAN-0018 phase 1).
    assert out.strip().splitlines()[-2] == report.DRY_RUN_TAIL
    assert out.strip().splitlines()[-1] == report.engine_line()
    # A rehearsal wrote nothing, so there is nothing to restart for.
    assert restart not in out

    code, out, _ = run(capsys, "connect", CLIENT, str(project))
    assert code == 0
    assert out.strip().splitlines()[-2] == restart
    assert out.strip().splitlines()[-1] == report.engine_line()

    code, out, _ = run(capsys, "unconnect", CLIENT, str(project))
    assert code == 0
    assert out.strip().splitlines()[-2] == restart
    assert out.strip().splitlines()[-1] == report.engine_line()
    # One settings file reversed to its pre-connect bytes. The record's
    # own row is not a settings file, and the head does not count it.
    assert "1 settings file(s) reversed, 0 left." in out


# --- PLAN-0017 phase 2 (cli-connect-02): the writer says what is left --

def test_a_write_that_stops_names_the_copy_not_put_back(
    project, both, command, monkeypatch,
):
    visible = roaming_config(both)
    hidden = store_config(both)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    hidden.write_bytes(CONFIG_HOSTS["empty-servers"])
    before = {visible: visible.read_bytes(), hidden: hidden.read_bytes()}

    original = pathlib.Path.write_bytes
    seen: list[Path] = []

    def flaky(self, data):
        seen.append(self)
        # The first copy lands; the second refuses; the restore of the
        # first refuses too.
        if len(seen) >= 2:
            raise PermissionError(13, "Permission denied", str(self))
        return original(self, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", flaky)
    with pytest.raises(cn.WriteStopped) as caught:
        cn.connect(project, env=both, today=CONNECT_DAY)
    monkeypatch.undo()

    exc = caught.value
    assert isinstance(exc, cn.ConnectError)
    landed = seen[0]
    assert exc.left == (str(landed),)
    assert str(exc).endswith(f"put back except: {landed}")
    assert "Permission denied" in str(exc)
    changed = {path for path in before if path.read_bytes() != before[path]}
    assert changed == {landed}
    assert not cn.record_path(project).exists()



# --- PLAN-0017 phase 7 (tests-05): the reversal is all-or-nothing too --

def test_a_failed_reversal_leaves_both_copies_and_the_record_as_found(
    project, both, command, monkeypatch,
):
    """`_restore_copies`, the writer behind `unconnect`, puts every copy
    back when one of them will not take its pre-connect bytes: the two
    copies never disagree, and the record still stands for both."""
    visible = roaming_config(both)
    hidden = store_config(both)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    hidden.write_bytes(CONFIG_HOSTS["empty-servers"])
    cn.connect(project, env=both, today=CONNECT_DAY)
    record = cn.record_path(project)
    before = (visible.read_bytes(), hidden.read_bytes(), record.read_bytes())
    assert before[0] != CONFIG_HOSTS["indent2"] and before[1] != CONFIG_HOSTS["empty-servers"]

    original = pathlib.Path.write_bytes
    seen: list[Path] = []

    def flaky(self, data):
        seen.append(self)
        if len(seen) == 2:
            raise OSError("the second copy would not open")
        return original(self, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", flaky)
    with pytest.raises(cn.WriteStopped) as caught:
        cn.unconnect(project, env=both)
    monkeypatch.setattr(pathlib.Path, "write_bytes", original)

    # Two forward writes, then the same two put back.
    assert set(seen[:2]) == {visible, hidden}
    assert seen == seen[:2] * 2
    assert caught.value.left == ()
    assert "the second copy would not open" in str(caught.value)
    assert (visible.read_bytes(), hidden.read_bytes(), record.read_bytes()) == before
    assert cn.is_connected(project)


def test_a_failed_removal_leaves_both_copies_and_the_record_as_found(
    project, both, command, monkeypatch,
):
    """The hidden copy was the connect's own file, so the reversal removes
    it rather than rewriting it. A removal the client holds open stops
    the reversal the same way: the visible copy's pre-connect bytes are
    put back, and the record stands."""
    visible = roaming_config(both)
    hidden = store_config(both)
    visible.write_bytes(CONFIG_HOSTS["indent2"])
    assert not hidden.exists()
    cn.connect(project, env=both, today=CONNECT_DAY)
    assert hidden.is_file()
    assert cn.unconnect_plan(project, env=both).removals == (hidden,)
    record = cn.record_path(project)
    before = (visible.read_bytes(), hidden.read_bytes(), record.read_bytes())

    original = pathlib.Path.unlink
    seen: list[Path] = []

    def held(self, *args, **kwargs):
        seen.append(self)
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(pathlib.Path, "unlink", held)
    with pytest.raises(cn.WriteStopped) as caught:
        cn.unconnect(project, env=both)
    monkeypatch.setattr(pathlib.Path, "unlink", original)

    assert seen == [hidden]
    assert caught.value.left == ()
    assert "Permission denied" in str(caught.value)
    assert (visible.read_bytes(), hidden.read_bytes(), record.read_bytes()) == before
    assert cn.is_connected(project)
