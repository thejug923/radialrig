"""Tests for `radialrig unconnect <client> --orphan <key>` — PLAN-0008 Phase 3.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_orphan.py -q

One oracle runs here: prescription 5. An entry whose project root is gone
carries no record to reverse it, so the verb is given the key by name and
removes exactly that entry, across every copy of the client's settings,
leaving every other key and value structurally as it stood. Both refusals
are pinned beside it — a key outside the client's own prefix, and a key
whose project still stands, which is plain `unconnect`'s job — and so is
the one case the verb declines outright: a key pinning no root at all,
which radialrig cannot show is an orphan either way.

Every orphan here is built by the real verbs against a fake client under
tmp_path: connect a project, then delete the project. `APPDATA` and
`LOCALAPPDATA` point inside tmp_path for every test in the suite
(conftest.py), and the fixtures below narrow them to this file's own two
copies, so nothing reaches this machine's own client.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from radialrig import cli
from radialrig import connect as cn
from radialrig import mount as mnt
from radialrig import orphan
from radialrig import report
from radialrig.paths import ROOT_ENV
from radialrig.tests.test_connect import (  # noqa: F401 - helpers, by name
    CONFIG_HOSTS,
    both,
    command,
    roaming_config,
    run,
    store_config,
)
from radialrig.tests.test_mount import _make_project

CLIENT = cn.DEFAULT_CLIENT
LABEL = cn.CLIENTS[CLIENT].label
PREFIX = cn.CLIENTS[CLIENT].key_prefix
RESTART = cn.CLIENTS[CLIENT].restart

BLOCK_LINE = re.compile(r"^[a-z]+ {2,}\S")


# --- fixtures ---------------------------------------------------------

@pytest.fixture()
def copies(both) -> tuple[Path, Path]:
    """The client's two settings files, in the shape a real one arrives in."""
    pair = (roaming_config(both), store_config(both))
    for config in pair:
        config.write_bytes(CONFIG_HOSTS["indent2"])
    return pair


def block(text: str) -> list[str]:
    return [line for line in text.splitlines() if BLOCK_LINE.match(line)]


def marks(text: str) -> dict[str, str]:
    """Each block row's path against the verb it carries."""
    return {line.split()[1]: line.split()[0] for line in block(text)}


def record_of(project: Path) -> dict:
    return json.loads(cn.record_path(project).read_text(encoding="utf-8"))


def settings_of(config: Path) -> dict:
    return json.loads(config.read_text(encoding="utf-8"))


def connected(capsys, project: Path) -> str:
    """A project registered with the fake client. Answers its entry key."""
    assert run(capsys, "connect", CLIENT, str(project))[0] == 0
    return record_of(project)["key"]


@pytest.fixture()
def planted(tmp_path, copies, command, capsys) -> str:
    """The orphan case, built by the real verbs: connect, then delete."""
    project = _make_project(tmp_path / "alpha")
    key = connected(capsys, project)
    shutil.rmtree(project)
    return key


# --- the round trip ---------------------------------------------------

def test_the_orphan_round_trip(copies, planted, capsys):
    assert all(planted in settings_of(config)["mcpServers"] for config in copies)

    _, listed, _ = run(capsys, "projects")
    assert f"radialrig unconnect {CLIENT} --orphan {planted}" in listed

    code, out, err = run(capsys, "unconnect", CLIENT, "--orphan", planted)

    assert code == 0
    assert err == ""
    assert out.splitlines()[0] == (
        f"Removed the entry {planted} from {LABEL} - "
        f"2 settings file(s) rewritten, 0 left.")
    rows = block(out)
    assert len(rows) == 2
    for line in rows:
        assert line.split()[0] == mnt.EXCISED, line
        assert line.split()[0] in mnt.VERBS, line
        assert line.endswith(orphan.ORPHAN_REASONS[orphan.ORPHAN_EXCISED])
    assert report.ORPHAN_REMOVED_TAIL in out
    # The report's own last line; the engine line ends every printed
    # report after it (PLAN-0018 phase 1).
    assert out.splitlines()[-2] == RESTART
    assert out.splitlines()[-1] == report.engine_line()

    # The entry is gone from both copies, and every other key and value
    # stood through the re-serialization. The write server's entry is an
    # orphan of its own (PLAN-0012 phase 1): one key, one removal.
    twin = planted + cn.WRITE_KEY_SUFFIX
    for config in copies:
        body = settings_of(config)
        assert planted not in body["mcpServers"]
        assert twin in body["mcpServers"]
    assert f"radialrig unconnect {CLIENT} --orphan {twin}" in listed

    # And the second run has nothing to find.
    assert run(capsys, "unconnect", CLIENT, "--orphan", planted)[0] == 4

    assert run(capsys, "unconnect", CLIENT, "--orphan", twin)[0] == 0
    for config in copies:
        assert settings_of(config) == json.loads(CONFIG_HOSTS["indent2"].decode("utf-8"))


def test_dry_run_prints_the_block_and_writes_nothing(copies, planted, capsys):
    before = [config.read_bytes() for config in copies]

    code, rehearsal, err = run(
        capsys, "unconnect", CLIENT, "--orphan", planted, "--dry-run")

    assert code == 0
    assert err == ""
    assert rehearsal.splitlines()[0].startswith("Would remove the entry ")
    assert rehearsal.splitlines()[-2] == report.DRY_RUN_TAIL
    assert rehearsal.splitlines()[-1] == report.engine_line()
    assert RESTART not in rehearsal
    assert [config.read_bytes() for config in copies] == before

    code, real, _ = run(capsys, "unconnect", CLIENT, "--orphan", planted)

    assert code == 0
    assert block(real) == block(rehearsal)


# --- the two refusals and the one error -------------------------------

def test_it_refuses_a_key_outside_the_prefix(copies, planted, capsys):
    before = [config.read_bytes() for config in copies]

    code, out, err = run(capsys, "unconnect", CLIENT, "--orphan", "some-other-key")

    assert code == 3
    assert err == ""
    assert out.splitlines()[0] == report.ORPHAN_REFUSAL_HEAD
    assert report.orphan_outside_prefix(cn.CLIENTS[CLIENT]) in out
    assert [config.read_bytes() for config in copies] == before


def test_it_refuses_a_key_whose_root_still_exists(
    tmp_path, copies, command, capsys,
):
    project = _make_project(tmp_path / "standing")
    key = connected(capsys, project)
    before = [config.read_bytes() for config in copies]

    code, out, err = run(capsys, "unconnect", CLIENT, "--orphan", key)

    assert code == 3
    assert err == ""
    assert out.splitlines()[0] == report.ORPHAN_REFUSAL_HEAD
    root = str(project.resolve())
    assert root in out
    assert f'"radialrig unconnect {CLIENT} {root}"' in out
    assert [config.read_bytes() for config in copies] == before


def test_a_key_with_no_pinned_root_is_an_error(copies, capsys):
    key = f"{PREFIX}bare"
    for config in copies:
        body = settings_of(config)
        body["mcpServers"][key] = {"command": "x", "args": []}
        config.write_text(json.dumps(body, indent=2), encoding="utf-8")
    before = [config.read_bytes() for config in copies]

    code, out, err = run(capsys, "unconnect", CLIENT, "--orphan", key)

    assert code == 1
    assert out == ""
    assert err == f"radialrig: {report.ORPHAN_NO_ROOT}\n"
    assert [config.read_bytes() for config in copies] == before


def test_an_absent_key_is_a_noop(copies, capsys):
    before = [config.read_bytes() for config in copies]

    code, out, err = run(capsys, "unconnect", CLIENT, "--orphan", f"{PREFIX}nothing")

    assert code == 4
    assert err == ""
    assert out == (
        f"Not found: {PREFIX}nothing in {LABEL}\n{report.ORPHAN_NOT_FOUND}\n"
        f"{report.engine_line()}\n"
    )
    assert [config.read_bytes() for config in copies] == before


# --- what one damaged copy costs --------------------------------------

def test_all_copies_or_none(copies, planted, capsys):
    """A copy that does not parse cannot carry a provable key.

    The all-copies contract is the write transaction, not a refusal on
    read: the copy that could be read is excised and the one that could
    not is listed as left, which is what `unconnect` itself does with a
    mixed record.
    """
    roaming, store = copies
    damaged = b"{ not JSON at all\n"
    store.write_bytes(damaged)

    code, out, err = run(capsys, "unconnect", CLIENT, "--orphan", planted)

    assert code == 5
    assert err == ""
    assert out.splitlines()[0].endswith("1 settings file(s) rewritten, 1 left.")
    assert marks(out) == {
        roaming.as_posix(): mnt.EXCISED,
        store.as_posix(): mnt.LEFT,
    }
    assert mnt.REASONS[mnt.CONNECT_UNPARSED_LEFT] in out
    # A partial run may not claim the entry is gone: it still stands in
    # the copy this run could not read.
    assert report.ORPHAN_LEFT_TAIL in out
    assert report.ORPHAN_REMOVED_TAIL not in out
    assert planted not in settings_of(roaming)["mcpServers"]
    assert store.read_bytes() == damaged


# --- the form itself --------------------------------------------------

def test_the_orphan_form_needs_a_client_and_takes_no_path(
    tmp_path, copies, planted, capsys,
):
    """The two ways this form itself can be typed wrong.

    Both cost nothing, and both name the form that does the job: the
    entry the reader came to remove still stands in every copy
    afterwards.
    """
    before = [config.read_bytes() for config in copies]

    code, out, err = run(capsys, "unconnect", "--orphan", planted)

    assert code == 1
    assert out == ""
    assert err == f"radialrig: {report.ORPHAN_NO_CLIENT}\n"

    code, out, err = run(
        capsys, "unconnect", CLIENT, str(tmp_path), "--orphan", planted)

    assert code == 3
    assert err == ""
    assert out.splitlines()[0] == report.ORPHAN_REFUSAL_HEAD
    assert report.ORPHAN_NO_PATH in out

    assert [config.read_bytes() for config in copies] == before
    assert all(planted in settings_of(config)["mcpServers"] for config in copies)


def test_a_deleted_path_reaches_the_verb_and_not_the_directory_check(
    tmp_path, copies, planted, capsys,
):
    """The one path a reader of this form has at hand is the gone one.

    Typing it used to end the run at the directory check, exit 1, with a
    sentence about a directory the reader already knew was not there.
    Now the run reaches the verb, which answers about the form instead
    and leaves every copy as it stood.
    """
    deleted = tmp_path / "alpha"
    assert not deleted.exists()

    code, out, err = run(
        capsys, "unconnect", CLIENT, str(deleted), "--orphan", planted)

    assert code == 3
    assert err == ""
    assert "no such directory" not in out
    assert report.ORPHAN_NO_PATH in out
    assert all(planted in settings_of(config)["mcpServers"] for config in copies)


def test_an_unknown_client_is_an_error(copies, capsys):
    code, out, err = run(capsys, "unconnect", "no-such-client", "--orphan", "x")

    assert code == 1
    assert out == ""
    assert "no-such-client" in err


# --- the word checks --------------------------------------------------

def test_no_orphan_output_implies_who_typed_a_change(
    tmp_path, copies, planted, command, capsys,
):
    from radialrig.tests.test_cli import FORBIDDEN_WORD

    standing = _make_project(tmp_path / "standing")
    alive = connected(capsys, standing)
    bare = f"{PREFIX}bare"
    for config in copies:
        body = settings_of(config)
        body["mcpServers"][bare] = {"command": "x", "args": [], "env": {}}
        config.write_text(json.dumps(body, indent=2), encoding="utf-8")

    collected = [
        run(capsys, "unconnect", CLIENT, "--orphan", "some-other-key"),
        run(capsys, "unconnect", CLIENT, "--orphan", alive),
        run(capsys, "unconnect", CLIENT, "--orphan", bare),
        run(capsys, "unconnect", CLIENT, "--orphan", f"{PREFIX}nothing"),
        run(capsys, "unconnect", CLIENT, str(tmp_path), "--orphan", planted),
        run(capsys, "unconnect", "--orphan", planted),
        run(capsys, "unconnect", CLIENT, "--orphan", planted, "--dry-run"),
        run(capsys, "unconnect", CLIENT, "--orphan", planted),
    ]
    assert [code for code, _, _ in collected] == [3, 3, 1, 4, 3, 1, 0, 0]

    text = "\n".join(out + err for _, out, err in collected).lower()
    hit = FORBIDDEN_WORD.search(text)
    assert hit is None, f"a report uses '{hit.group(0) if hit else ''}'"


def test_the_orphan_wordings_carry_no_banned_vocabulary():
    """The constants scan test_setup_web.py runs, over this verb's own.

    It reaches the rendered refusals too, not the constants alone: a
    refusal is assembled from a client's own label and key prefix, and
    what a reader meets is the assembled sentence.
    """
    from radialrig.tests.test_cli import FORBIDDEN_WORD

    client = cn.CLIENTS[CLIENT]
    names = [name for name in dir(report) if name.startswith("ORPHAN_")]
    assert names, "the orphan report carries no constants"
    texts = [(name, getattr(report, name)) for name in names]
    texts.extend(
        (code, wording) for code, wording in orphan.ORPHAN_REASONS.items()
    )
    texts.append(
        ("orphan_outside_prefix", report.orphan_outside_prefix(client)))
    texts.append(
        ("orphan_root_alive", report.orphan_root_alive(client, r"C:\dev\alpha")))
    for reason in (
        report.orphan_outside_prefix(client),
        report.orphan_root_alive(client, r"C:\dev\alpha"),
        report.ORPHAN_NO_PATH,
    ):
        texts.extend(
            ("orphan_refused_report", line)
            for line in report.orphan_refused_report(reason)
        )
    for name, text in texts:
        assert isinstance(text, str), name
        assert not FORBIDDEN_WORD.search(text), name
        lowered = text.lower()
        for banned in ("magic", "just works", "seamless"):
            assert banned not in lowered, name
        assert not any(
            word.strip('".,:;') == "safe" for word in lowered.split()
        ), name



# --- PLAN-0017 phase 7 (tests-05): the orphan write is all-or-nothing --

def test_a_failed_orphan_write_leaves_both_copies_as_found(
    copies, both, planted, monkeypatch,
):
    """`orphan.remove` writes through the connect kit's reversal writer.
    A second copy that will not take its bytes puts the first back, and
    the entry still stands in both. The writer removes no file, so a
    held unlink is never asked for."""
    roaming, store = copies
    before = (roaming.read_bytes(), store.read_bytes())
    assert planted in settings_of(roaming)["mcpServers"]
    assert planted in settings_of(store)["mcpServers"]

    write_bytes = Path.write_bytes
    unlink = Path.unlink
    written: list[Path] = []
    unlinked: list[Path] = []

    def flaky(self, data):
        written.append(self)
        if len(written) == 2:
            raise OSError("the second copy would not open")
        return write_bytes(self, data)

    def held(self, *args, **kwargs):
        unlinked.append(self)
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "write_bytes", flaky)
    monkeypatch.setattr(Path, "unlink", held)
    with pytest.raises(cn.WriteStopped) as caught:
        orphan.remove(CLIENT, planted, env=both)
    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    monkeypatch.setattr(Path, "unlink", unlink)

    assert set(written[:2]) == set(copies)
    assert written == written[:2] * 2
    assert unlinked == []
    assert caught.value.left == ()
    assert "the second copy would not open" in str(caught.value)
    assert (roaming.read_bytes(), store.read_bytes()) == before
    assert planted in settings_of(roaming)["mcpServers"]
    assert planted in settings_of(store)["mcpServers"]
