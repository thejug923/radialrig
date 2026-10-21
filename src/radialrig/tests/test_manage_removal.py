"""The full-removal flow of `radialrig manage` — PLAN-0008 Phase 4.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_manage_removal.py -q

The oracle is the sequence a reader would type: `unconnect`, then
`skill remove` per installed skill, then `unmount`. One press on the page
runs those three through the same performers the single presses use, so
the bytes it leaves are the bytes those verbs leave on a twin built from
the same maker, and the words it prints are their own report lines.

The two removals the flow does not make — the engine's own and the app's
— are pinned as named rather than pressed, and the page's account of what
is still standing is pinned against what is actually still standing.

Everything drives real HTTP against a real server on a real port.

Nothing here may reach this machine's own profile. `APPDATA` and
`LOCALAPPDATA` point inside tmp_path for every test in the suite
(conftest.py), and the fixtures below narrow the profile to one fake
client. The store variables are never deleted: the roster path is built
from this platform's `STORES` row, and a test running without it would
drive a removal view over a listing that can never hold anything.

What the machine-level rows say varies by operating system, so every
assertion about them reads the `MACHINES` row this platform serves and
asserts exactly what that row allows (PLAN-0009 Phase 2 prescription 5).
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from radialrig import connect as cn
from radialrig import manage_web
from radialrig import mount as mnt
from radialrig import report
from radialrig import roster as ro
from radialrig import skills as sk
from radialrig.tests.test_manage_actions import (  # noqa: F401 - helpers, by name
    SKILL_DIR,
    _host,
    commands_on,
    fixed_day,
    press,
    says,
)
from radialrig.tests.test_connect import STORE_VAR
from radialrig.tests.test_manage_web import (  # noqa: F401 - helpers, by name
    CLIENT,
    HIDDEN,
    SKILL,
    client_here,
    command,
    drop_entry,
    env,
    manage,
    navigations,
    record_of,
    registered,
    roster_file,
    rows,
    run,
    text_of,
)
from radialrig.tests.test_mount import (  # noqa: F401 - helpers, by name
    _make_project,
    snapshot,
    tree,
)

POST_FORM = re.compile(r'<form method="post" action="[^"]+">(.*?)</form>', re.S)
BUTTON_ACTION = re.compile(r'<button[^>]*name="action" value="([^"]+)"')


# --- helpers ----------------------------------------------------------

def removal(run_) -> str:
    """The removal view, opened the way the roster's own button opens it."""
    status, page, _ = run_.get(
        f"{manage_web.MANAGE_PATH}?s={run_.session.token}"
        f"&view={manage_web.REMOVAL}")
    assert status == 200, page
    return page


def posted_actions(page: str) -> set[str]:
    """Every action a POST form on this page could carry."""
    out: set[str] = set()
    for inner in POST_FORM.findall(page):
        out.update(
            value for name, value in HIDDEN.findall(inner) if name == "action")
        out.update(BUTTON_ACTION.findall(inner))
    return out


def left_paths(page: str) -> list[str]:
    return [path for verb, path, _ in rows(page) if verb == mnt.LEFT]


def named_removals() -> list[str]:
    """Every command the removal view names and does not run, in order.

    Read off the table the view renders from rather than listed again
    here: a removal that grows a command belongs in every one of these
    assertions, and one that carries none — the app's row, the Path
    entry — belongs in none of them.
    """
    return [line for _, line in manage_web.machine_removals() if line]


# --- the sequence -----------------------------------------------------

def test_a_project_reversal_matches_the_cli_sequence_byte_for_byte(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """One press against three verbs on a twin.

    The page's project is walked on through the command line, so the
    roster names it; the twin is walked on through the engine those verbs
    call, so the listing this view reads holds one project rather than
    two. Each keeps its own scope, which is what lets both register in the
    one settings file this machine has.

    The twin registers first and reverses last. An unconnect restores its
    copy to the bytes from before its own connect, so two registrations
    in one file come out in the order they went in, last one first —
    which is the order this test runs them and the order a reader gets by
    reversing one project at a time.

    The press is two presses, the rhythm every single press on this page
    keeps: the rehearsal, which writes nothing, and the confirm, which is
    the one the byte oracle is about.
    """
    page_root = _host(tmp_path / "page-host")
    twin = _host(tmp_path / "twin-host")
    assert tree(page_root) == tree(twin)
    pristine = client_here.read_bytes()

    mnt.mount(twin)
    sk.add(twin, SKILL)
    cn.connect(twin, CLIENT)
    for argv in (("mount", str(page_root)),
                 ("skill", "add", SKILL, str(page_root)),
                 ("connect", CLIENT, str(page_root))):
        assert run(capsys, *argv)[0] == report.OK, argv
    assert client_here.read_bytes() != pristine

    run_ = manage()
    page = removal(run_)

    # The view names every verb the press runs, in the order it runs them,
    # and each one is the command that runs it alone.
    steps = manage_web.reversal_steps(ro.verify(page_root))
    assert [act.name for act in steps] == [
        manage_web.UNCONNECT, manage_web.SKILL_REMOVE, manage_web.UNMOUNT]
    assert commands_on(page) == [
        *(act.command for act in steps), *named_removals()]
    assert [act.command for act in steps] == [
        f"radialrig unconnect {CLIENT} {page_root}",
        f"radialrig skill remove {SKILL} {page_root}",
        f"radialrig unmount {page_root}",
    ]

    # Two presses, the rhythm every single press keeps. The first writes
    # nothing — compared against this project's own bytes, because the
    # twin carries its own scope and the two only read alike once both
    # reversals have run.
    standing = tree(page_root)
    fields = {"action": manage_web.REVERSAL, "root": str(page_root)}
    status, rehearsal, _ = run_.post(press(run_, fields))
    assert status == 200
    assert tree(page_root) == standing, "the rehearsal wrote something"
    assert commands_on(rehearsal) == [act.command for act in steps]

    status, done, _ = run_.post(press(run_, fields, confirm=True))
    assert status == 200

    # The same three verbs, from the command line, on the twin.
    printed: list[str] = []
    for argv in (("unconnect", CLIENT, str(twin)),
                 ("skill", "remove", SKILL, str(twin)),
                 ("unmount", str(twin))):
        code, out, err = run(capsys, *argv)
        assert (code, err) == (report.OK, ""), argv
        printed.extend(out.replace(str(twin), str(page_root)).splitlines())

    assert tree(page_root) == tree(twin)
    assert snapshot(page_root) == snapshot(twin)
    assert client_here.read_bytes() == pristine
    says(done, printed)

    # And the page carries all three reports, each under its own command.
    assert commands_on(done) == [act.command for act in steps]
    assert not mnt.is_mounted(page_root)
    assert manage_web.REMAINS_LEFT in text_of(done)
    assert run_.running


def test_an_unconfirmed_reversal_rehearses_and_writes_nothing(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """The most consequential press on the page takes two, like the rest.

    Three verbs over a whole project, so a single post that reached the
    engine would be the one press here with no rehearsal in front of it.
    The first press answers the dry-run rows each of those verbs answers
    with alone, under each one's own command, and the button beneath them
    is the only thing on the state that writes.
    """
    root = _host(tmp_path / "host")
    for argv in (("mount", str(root)),
                 ("skill", "add", SKILL, str(root)),
                 ("connect", CLIENT, str(root))):
        assert run(capsys, *argv)[0] == report.OK, argv
    before = tree(root)
    config = client_here.read_bytes()
    listed = [entry["root"] for entry in ro.load().entries]

    run_ = manage()
    steps = manage_web.reversal_steps(ro.verify(root))
    status, page, _ = run_.post(press(
        run_, {"action": manage_web.REVERSAL, "root": str(root)}))
    assert status == 200

    # Nothing moved: the tree, the mount, the client's own file, the
    # listing this page reads its rows back from.
    assert tree(root) == before
    assert mnt.is_mounted(root)
    assert client_here.read_bytes() == config
    assert [entry["root"] for entry in ro.load().entries] == listed

    # And the state is the rehearsal: every step's command, every step's
    # own dry-run rows, and one press that would run them for real.
    said = text_of(page)
    assert manage_web.REVERSAL_PLAN_HEAD in said
    assert manage_web.REVERSED_HEAD not in said
    assert commands_on(page) == [act.command for act in steps]
    assert manage_web.PLAN_CAPTION in said
    assert 'name="confirm"' in page
    assert posted_actions(page) == {manage_web.REVERSAL}
    drawn = rows(page)
    for act in steps:
        rehearsed = manage_web.RUNNERS[act.name](act, dry_run=True)
        assert rehearsed.kind == manage_web.PLAN, act.name
        says(page, rehearsed.lines)
        for d in rehearsed.dispositions:
            assert (d.verb, d.path, manage_web._reason(d)) in drawn, d.path

    # The confirm press is the one that reverses.
    status, done, _ = run_.post(press(
        run_, {"action": manage_web.REVERSAL, "root": str(root)}, confirm=True))
    assert status == 200
    assert manage_web.REVERSED_HEAD in text_of(done)
    assert not mnt.is_mounted(root)
    assert tree(root) != before
    assert run_.running


def test_a_reversal_that_names_no_project_reverses_nothing(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    r"""The empty field is the dangerous one, and so is the relative one.

    `Path("")` is `Path(".")`, it is a directory, and it resolves to
    wherever this server was started — so a reversal that arrived naming
    no project ran its three verbs against the directory the process is
    standing in, and then wrote that directory into the roster as a
    project. `.` and `..` reach the same directory by the same road, which
    is why the resolver takes only an absolute path.

    The working directory here is a mounted project carrying a skill, and
    it is the one project on this machine the roster has never heard of —
    so a press that reached it would be reversing the project nobody
    named. Every single press guards through one resolver; this press goes
    through the same one, so the only thing it can reverse is a project
    someone named by its full path.
    """
    here = _make_project(tmp_path / "cwd-project").resolve()
    mnt.mount(here)
    sk.add(here, SKILL)
    named = _make_project(tmp_path / "named").resolve()
    assert run(capsys, "mount", str(named))[0] == report.OK

    monkeypatch.chdir(here)
    before = tree(here)
    listed = [entry["root"] for entry in ro.load().entries]
    assert listed == [str(named)], "the working directory is already listed"

    run_ = manage()
    for fields in ({"action": manage_web.REVERSAL, "root": ""},
                   {"action": manage_web.REVERSAL, "root": "."},
                   {"action": manage_web.REVERSAL, "root": ".."},
                   {"action": manage_web.REVERSAL}):
        # Both presses, because the guard is the resolver both go through.
        for confirm in (False, True):
            status, page, _ = run_.post(press(run_, fields, confirm=confirm))
            assert status == 200, (fields, confirm)
            assert tree(here) == before, (fields, confirm)
            assert mnt.is_mounted(here), (fields, confirm)
            assert [entry["root"] for entry in ro.load().entries] == listed, (
                fields, confirm)
            # The listing, where a reader picks a project by name.
            said = text_of(page)
            assert manage_web.ROSTER_HEAD in said, (fields, confirm)
            assert manage_web.REVERSED_HEAD not in said, (fields, confirm)
            assert manage_web.REVERSAL_PLAN_HEAD not in said, (fields, confirm)
            assert "no such directory" not in said, (fields, confirm)

    # The same press naming a project still reverses that one.
    named_fields = {"action": manage_web.REVERSAL, "root": str(named)}
    assert run_.post(press(run_, named_fields))[0] == 200
    status, done, _ = run_.post(press(run_, named_fields, confirm=True))
    assert status == 200
    assert manage_web.REVERSED_HEAD in text_of(done)
    assert not mnt.is_mounted(named)
    assert tree(here) == before
    assert run_.running


# --- the two removals this page will not make -------------------------

def test_the_removal_page_names_the_engine_and_app_removals_and_performs_neither(
    tmp_path, env, command, capsys, manage, fixed_day,
):
    root = _make_project(tmp_path / "host").resolve()
    assert run(capsys, "mount", str(root))[0] == report.OK

    run_ = manage()
    # The roster's own button reaches this view; nothing else does.
    assert any(f"view={manage_web.REMOVAL}" in target
               for target in navigations(run_.page(), run_.session.token))
    page = removal(run_)
    said = text_of(page)

    # Both are named where this operating system has both. The engine's
    # is a command a reader can copy; the app's is the place the OS
    # itself lists the app, where this OS has one.
    assert f'data-copy="{manage_web.ENGINE_REMOVAL}"' in page
    assert manage_web.ENGINE_REMOVAL in said
    row = manage_web.machine()
    assert row is not None
    if row.app_place:
        assert row.app_place in said
    else:
        assert "Windows Settings" not in said

    # Neither is a press. No page action names either one, and this
    # module starts no program at all.
    assert manage_web.ENGINE_REMOVAL not in manage_web.COMMANDS.values()
    assert posted_actions(page) <= set(manage_web.COMMANDS) | {manage_web.REVERSAL}
    source = Path(manage_web.__file__).read_text(encoding="utf-8")
    for spelling in ("subprocess", "Popen", "os.system", "os.exec"):
        assert spelling not in source, spelling

    # What is left, and why each thing is left.
    assert manage_web.REMAINS_LEFT in said
    assert manage_web.REMAINS_ROSTER.format(path=roster_file(env)) in said
    assert str(roster_file(env)) in said
    assert manage_web.REMAINS_ENGINE in said
    if row.app_place:
        assert manage_web.REMAINS_APP.format(place=row.app_place) in said
    else:
        assert "desktop app" not in said

    # The press this view does carry reverses the project and nothing
    # else: the engine's own commands are still installed after it.
    fields = {"action": manage_web.REVERSAL, "root": str(root)}
    assert run_.post(press(run_, fields))[0] == 200
    status, done, _ = run_.post(press(run_, fields, confirm=True))
    assert status == 200
    assert not mnt.is_mounted(root)
    assert manage_web.ENGINE_REMOVAL in text_of(done)
    assert run_.running


def test_the_removal_page_names_every_machine_change_the_first_run_made(
    tmp_path, env, command, capsys, manage, fixed_day,
):
    """The rest of the list, which used to go unsaid.

    The flow named two removals and stopped: the engine's own and the
    app's. The app's first run puts more than those on a machine — uv
    itself, a Python for the engine where the machine had none, uv's
    download cache, and one entry on the user Path — and a page that
    promises to say what is still standing has to say all of it.

    Each is named with the command that makes it where a command exists,
    in the order that leaves every command a uv to run it, and the Path
    entry is named as the one thing no command here takes out.
    """
    root = _make_project(tmp_path / "host").resolve()
    assert run(capsys, "mount", str(root))[0] == report.OK

    run_ = manage()
    page = removal(run_)
    said = text_of(page)

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    python_line = manage_web.PYTHON_REMOVAL.format(version=version)
    row = manage_web.machine()
    assert row is not None
    uv_line = manage_web.uv_removal(row)
    assert named_removals() == [
        manage_web.ENGINE_REMOVAL,
        python_line,
        manage_web.UV_CACHE_REMOVAL,
        uv_line,
    ]
    # The cache row names where this operating system keeps it.
    assert manage_web.UV_CACHE_WHAT.format(cache=row.uv_cache) in said

    # Every one of them is a block a reader can copy, in that order, and
    # they come after the project's own reversal commands.
    assert commands_on(page)[-len(named_removals()):] == named_removals()
    for line in named_removals():
        assert f'data-copy="{html.escape(line, quote=True)}"' in page, line
        assert line in said, line

    # The uv line names both programs by the path they are actually at,
    # in this operating system's own delete form, with no variable a
    # shell would have to expand. Both paths stay quoted — a profile
    # directory with a space in it puts one in both of them.
    uv_exe, uvx_exe = manage_web.uv_paths(row)
    assert uv_line == row.uv_delete.format(uv=uv_exe, uvx=uvx_exe)
    assert "%" not in uv_line
    assert "$" not in uv_line
    for path in (uv_exe, uvx_exe):
        assert Path(path).is_absolute(), path
        assert Path(path).parent == Path.home().joinpath(*row.uv_bin)

    # The Python is named at the version this engine is running on, one
    # version at a time. The switch that would take every Python uv
    # manages is nowhere on the page or in the module that draws it.
    assert python_line == f"uv python uninstall {version}"
    assert "--all" not in page
    # The `--all` the module spells is the skills kit's whole-catalog add
    # (PLAN-0015) or the update of every listed project (PLAN-0021 phase
    # 1), never uv's switch over every Python it manages.
    source = Path(manage_web.__file__).read_text(encoding="utf-8")
    assert "python uninstall --all" not in source
    assert "--all" not in manage_web.PYTHON_REMOVAL
    assert all("skill add --all" in line or "upgrade --all" in line
               for line in source.splitlines() if "--all" in line)

    # The rows with no command, where this operating system has them,
    # name themselves rather than offering a line that would do
    # something else.
    if row.app_place:
        assert row.app_place in said
        assert f'data-copy="{row.app_place}"' not in page
    # The Path slot is stated either way (PLAN-0009 phase 3
    # prescription 5): the row this platform serves puts its own line on
    # the page, and no other row's line appears beside it.
    assert row.path_what in said
    for other in manage_web.MACHINES:
        if other.path_what != row.path_what:
            assert other.path_what not in said

    # And what is left after all of it says the same things, so a reader
    # who runs everything is told what is still standing.
    assert manage_web.REMAINS_ENGINE in said
    if row.app_place:
        assert manage_web.REMAINS_APP.format(place=row.app_place) in said
    assert manage_web.REMAINS_PYTHON.format(command=python_line) in said
    assert manage_web.REMAINS_UV.format(
        cache=manage_web.UV_CACHE_REMOVAL) in said
    if row.path_remains:
        assert row.path_remains in said
    else:
        assert manage_web.REMAINS_PATH not in said

    # Not one of them is a press: the view's forms carry this page's own
    # actions and nothing else.
    assert posted_actions(page) <= set(manage_web.COMMANDS) | {manage_web.REVERSAL}
    assert run_.running


# --- constraint 5: a file this project changed is never taken ---------

def test_reversal_respects_edited_files(
    tmp_path, env, command, capsys, manage, fixed_day,
):
    root = _make_project(tmp_path / "host").resolve()
    assert run(capsys, "mount", str(root))[0] == report.OK
    assert run(capsys, "skill", "add", SKILL, str(root))[0] == report.OK

    edited = next(p for p in sorted((root / SKILL_DIR).rglob("*")) if p.is_file())
    ours = b"our own words, in this project's own file\n"
    edited.write_bytes(ours)
    rel = edited.relative_to(root).as_posix()

    run_ = manage()
    fields = {"action": manage_web.REVERSAL, "root": str(root)}
    assert run_.post(press(run_, fields))[0] == 200
    assert edited.read_bytes() == ours, "the rehearsal wrote something"
    status, done, _ = run_.post(press(run_, fields, confirm=True))
    assert status == 200

    # The file stands, holding what this project wrote, and the page says
    # so in the engine's own row rather than in a sentence of its own.
    assert edited.read_bytes() == ours
    assert rel in left_paths(done)
    said = text_of(done)
    assert report.SKILL_LEFT_TAIL in said
    assert manage_web.REMAINS_LEFT in said
    # And the rest of the reversal ran: the mount is gone.
    assert not mnt.is_mounted(root)


# --- no client is named where none is recorded ------------------------

def test_the_removal_page_names_no_client_where_none_is_recorded(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    """Two projects, one of which names a client and one of which does not.

    One was never registered, and no sentence about it names a client.
    The other carries a record claiming an entry this client no longer
    holds, which `roster.verify` reads as a mismatch: the record names a
    client the listing cannot confirm, so the reversal offers no
    registration step for it — and the block says the repair sentence
    with the client's label in it and the two filled commands under it,
    because the record is the fact being described (PLAN-0014
    constraint 7, narrowed by phase 3). No press for that client stands
    on this page: the repair press is the project page's, and the
    reversal's steps carry no `unconnect` for a record they cannot
    confirm.
    """
    bare = _make_project(tmp_path / "bare").resolve()
    assert run(capsys, "mount", str(bare))[0] == report.OK
    crossed = registered(capsys, _make_project(tmp_path / "crossed")).resolve()
    drop_entry(client_here, record_of(crossed)["key"])
    facts = ro.verify(crossed)
    assert facts.connected == report.PROJECTS_MISMATCH
    assert facts.claimed == CLIENT

    run_ = manage()
    page = removal(run_)
    said = text_of(page)
    label = cn.CLIENTS[CLIENT].label

    # The commands in full, in the order the blocks stand: the bare
    # project's reversal, the mismatched project's remedy — retire, then
    # register — and its reversal, then the machine's own removals.
    assert commands_on(page) == [
        f"radialrig unmount {bare}",
        f"radialrig unconnect {CLIENT} {crossed}",
        f"radialrig connect {CLIENT} {crossed}",
        f"radialrig unmount {crossed}",
        *named_removals(),
    ]
    # The record this page cannot confirm is named rather than passed
    # over, in the one sentence every surface says about it, with the
    # client filled in; the placeholder tail is gone with the phase.
    assert ro.MISMATCH_CLAUSE.format(label=label) in said
    assert f"{label}'s settings no longer carry" in said
    assert report.PROJECTS_MISMATCH_TAIL not in said
    assert "<client>" not in said and "<PATH>" not in said
    # No press for that client: the reversal's steps name no registration
    # on a mismatched record, and the page carries no connect, unconnect
    # or repair form at all.
    assert posted_actions(page) == {manage_web.REVERSAL}
    assert f'value="{manage_web.UNCONNECT}"' not in page
    assert f'value="{manage_web.CONNECT}"' not in page
    assert f'value="{manage_web.REPAIR}"' not in page
    assert f'<span class="what">Disconnect {label}</span>' not in page
    # The bare project's block names no client anywhere in it.
    blocks = page.split("<fieldset")
    bare_block = next(block for block in blocks if f"<legend>{html.escape(str(bare))}" in block)
    assert label not in bare_block and CLIENT not in bare_block

    # A machine with no client at all names none either, and the one
    # project on it still reverses. The roster moves with the client:
    # this platform's store variable points at the same bare base, so
    # the fresh listing holds only what is mounted after the move.
    no_client = tmp_path / "no-client"
    monkeypatch.setenv("APPDATA", str(no_client))
    monkeypatch.setenv(STORE_VAR, str(no_client))
    alone = _make_project(tmp_path / "alone").resolve()
    assert run(capsys, "mount", str(alone))[0] == report.OK
    assert cn.detect(cn.CLIENTS[CLIENT]) == ()

    solo = manage()
    page = removal(solo)
    for client in cn.CLIENTS.values():
        assert client.label not in page
        assert client.name not in page
    assert commands_on(page) == [
        f"radialrig unmount {alone}", *named_removals()]

    fields = {"action": manage_web.REVERSAL, "root": str(alone)}
    assert solo.post(press(solo, fields))[0] == 200
    status, done, _ = solo.post(press(solo, fields, confirm=True))
    assert status == 200
    assert not mnt.is_mounted(alone)
    for client in cn.CLIENTS.values():
        assert client.label not in done
