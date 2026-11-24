"""Remove from this project and Remove RadialRig, in `radialrig manage`.

PLAN-0008 Phase 4 wrote the full-removal flow; PLAN-0027 phase 3 drew it
for a person: Confirm a big change for one project, and numbered steps
for the computer.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_manage_removal.py -q

The oracle is the sequence a reader would type: `unconnect`, then
`skill remove` per installed skill, then `unmount`. One press on the card
runs those three through the same performers the single presses use, so
the bytes it leaves are the bytes those verbs leave on a twin built from
the same maker. The card's keep box runs `unmount` alone, under its own
offer check. Each press comes back to the screen it was asked from with
one result line, and leaves its event in the activity log.

The two removals the page does not make — the engine's own and the
app's — are pinned as named rather than pressed, in today's order with
the app's last (prescription 4), and each platform's Path sentence is
pinned on its own page.

Everything drives real HTTP against a real server on a real port.

Nothing here may reach this machine's own profile. `APPDATA` and
`LOCALAPPDATA` point inside tmp_path for every test in the suite
(conftest.py), and the fixtures below narrow the profile to one fake
client. The store variables are never deleted: the roster path is built
from this platform's `STORES` row, and a test running without it would
drive a removal view over a listing that can never hold anything.
"""

from __future__ import annotations

import html
import json
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
    connected_host,
    fixed_day,
    log_events,
    logged,
    press,
    screen_of,
)
from radialrig.tests.test_connect import STORE_VAR, plant_lock, roaming_config
from radialrig.tests.test_manage_web import (  # noqa: F401 - helpers, by name
    CLIENT,
    COPY_LINE,
    HIDDEN,
    PRIMARY,
    SKILL,
    client_here,
    command,
    drop_entry,
    env,
    main_of,
    manage,
    navigations,
    pressed,
    project_page,
    record_of,
    registered,
    result_of,
    run,
    status_band,
    text_of,
)
from radialrig.tests.test_mount import (  # noqa: F401 - helpers, by name
    _make_project,
    snapshot,
    tree,
)

POST_FORM = re.compile(r'<form method="post" action="[^"]+">(.*?)</form>', re.S)
ROW = re.compile(r'<li class="removal-row">(.*?)</li>', re.S)


# --- helpers ----------------------------------------------------------

def removal(run_) -> str:
    """Remove RadialRig, opened the way the foot of Projects opens it."""
    status, page, _ = run_.get(
        f"{manage_web.MANAGE_PATH}?s={run_.session.token}"
        f"&view={manage_web.REMOVAL}")
    assert status == 200, page
    return page


def forms(page: str) -> list[dict[str, str]]:
    """Every post form on a page, token dropped."""
    out = []
    for inner in POST_FORM.findall(main_of(page)):
        fields = dict(HIDDEN.findall(inner))
        fields.pop("token", None)
        out.append(fields)
    return out


def card(run_, root: Path, origin: str = manage_web.PROJECT) -> str:
    """Remove from this project's first press, as its screen posts it."""
    return pressed(run_, {"action": manage_web.REVERSAL, "root": str(root), "from": origin})


def default_press(page: str) -> dict[str, str]:
    (fields,) = [f for f in forms(page) if f.get("action") == manage_web.REVERSAL]
    return fields


def keep_press(page: str) -> dict[str, str]:
    (fields,) = [f for f in forms(page) if f.get("action") == manage_web.UNMOUNT]
    return fields


def part(page: str, name: str) -> str:
    """The card's text in one of its two halves: the default's, or the
    keep box's, which the stylesheet swaps."""
    found = re.findall(rf'<div class="{name}">(.*?)</div>', main_of(page), re.S)
    return " ".join(" ".join(html.unescape(re.sub(r"<[^>]+>", " ", f)).split()) for f in found)


def named_removals() -> list[str]:
    """Every command the removal view names and does not run, in order,
    read off the table the view renders from."""
    return [line for _, line in manage_web.machine_removals() if line]


def engine_block(page: str) -> tuple[list[str], str]:
    """Step 2's commands as shown, one a line, and what its one Copy copies."""
    found = re.search(
        r'<div class="command-block"><pre>(.*?)</pre><button class="copy" type="button" '
        r'data-copy="([^"]*)"', main_of(page), re.S)
    assert found is not None, "no command block"
    return html.unescape(found.group(1)).split("\n"), html.unescape(found.group(2))


def steps_of(page: str) -> list[str]:
    """The numbered steps' headings, in order."""
    return [html.unescape(h) for h in re.findall(
        r"<li><h2>([^<]*)</h2>", main_of(page).split('<ol class="numbered">', 1)[1])]


# --- Remove from this project -------------------------------------------

def test_a_project_removal_matches_the_cli_sequence_byte_for_byte(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """One press against three verbs on a twin.

    The page's project is walked on through the command line, so the
    roster names it; the twin is walked on through the engine those verbs
    call. Each keeps its own scope, which is what lets both register in
    the one settings file. The twin registers first and is reversed last,
    so the file comes back to its first bytes.

    The press is two, as every big change is: the card, which writes
    nothing, and its button, which runs the sequence inside the queue and
    comes back to the project's screen with one result line.
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
    steps = manage_web.reversal_steps(ro.verify(page_root))
    assert [act.name for act in steps] == [
        manage_web.UNCONNECT, manage_web.SKILL_REMOVE, manage_web.UNMOUNT]
    assert [act.command for act in steps] == [
        f"radialrig unconnect {CLIENT} {page_root}",
        f"radialrig skill remove {SKILL} {page_root}",
        f"radialrig unmount {page_root}",
    ]

    # The foot's press opens the card; nothing moves.
    foot = main_of(project_page(run_, page_root)).split('<div class="project-foot">', 1)[1]
    (asked,) = [dict(HIDDEN.findall(inner)) for inner in POST_FORM.findall(foot)]
    asked.pop("token")
    assert asked == {"action": manage_web.REVERSAL, "root": str(page_root),
                     "from": manage_web.PROJECT}
    standing = tree(page_root)
    page = pressed(run_, asked)
    assert tree(page_root) == standing, "the card wrote something"
    assert screen_of(page) == "confirm"
    # Details: every command the default runs, then the keep box's.
    assert commands_on(page) == [*(act.command for act in steps),
                                 f"radialrig unmount {page_root}"]

    before = len(log_events())
    done = pressed(run_, default_press(page))
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
    label = cn.CLIENTS[CLIENT].label
    assert screen_of(done) == "project"
    tone, sentence, warning, details = result_of(done)
    assert (tone, sentence, warning) == (
        manage_web.RESULT_DONE,
        f"Removed RadialRig files, skills, and the {label} connection. Your own files "
        f"stayed in place. Fully quit and reopen {label} to apply the change.", "")
    assert f"{cn.CONNECT_REL} {manage_web.MIDDOT} removed" in details
    assert status_band(done) == [("new", "RadialRig is not set up in this project.", "Set up")]
    # The log keeps the press once, with every command's own report in it.
    ((what, outcome, lines),) = logged(before)
    assert (what, outcome) == ("press reversal confirm",
                               f"{manage_web.REVERSED} exit {report.OK}")
    # Every line the three commands print but their rows, which the log's
    # state carries as rows rather than lines.
    said = [" ".join(line.split()) for line in lines]
    row = re.compile(r"^(created|appended|merged|updated|current|removed|restored"
                     r"|excised|left|absent)\s")
    for line in printed:
        wanted = " ".join(line.split())
        if wanted and wanted != report.engine_line() and not row.match(wanted):
            assert wanted in said, wanted
    assert run_.running


def test_the_card_says_what_goes_offers_the_keep_box_and_writes_nothing(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescription 1 and 2 on the card: the question, one sentence of
    counts in `wording.md`'s words, the keep box whose label names what it
    keeps, and two presses, each its own plan's: the default's reversal and
    the box's unmount, which the stylesheet swaps. Cancel goes back
    unchanged. A file the person edited is the one row that needs
    attention."""
    root = connected_host(tmp_path, "pit-wall")
    edited = root / "docs" / "spec.md"
    edited.write_bytes(b"# ours now\n")
    label = cn.CLIENTS[CLIENT].label
    run_ = manage()
    before = (tree(root), client_here.read_bytes())

    page = card(run_, root)
    assert (tree(root), client_here.read_bytes()) == before
    body = main_of(page)
    assert "<h1>Remove RadialRig from pit-wall?</h1>" in body
    assert (f"Removes the project files, 1 skill for both agents, and the {label} "
            "connection. Your own files stay in place.") in part(page, "remove-all")
    assert "docs/spec.md Keeping your edits" in part(page, "remove-all")
    assert (f"Removes the project files. Your skills, {label} connection, and own files "
            "stay in place.") in part(page, "keep-only")
    assert ('<label class="check keep"><input type="checkbox" id="keep">'
            f"Keep the skills and {label} connection</label>") in body
    assert default_press(page) == {"action": manage_web.REVERSAL, "root": str(root),
                                   "confirm": "yes", "from": manage_web.PROJECT}
    assert keep_press(page) == {"action": manage_web.UNMOUNT, "root": str(root),
                                "confirm": "yes", "from": manage_web.PROJECT}
    assert f'<button class="primary" type="submit">{manage_web.REMOVE_LABEL}</button>' in body
    # The client isn't open, so no warning on either half.
    assert '<p class="warn">' not in body
    # Cancel: back to the project, a navigation that writes nothing.
    (cancel,) = [t for t in navigations(page, run_.session.token)
                 if f"view={manage_web.PROJECT}" in t]
    status, back, _ = run_.get(cancel)
    assert status == 200 and screen_of(back) == "project"
    assert (tree(root), client_here.read_bytes()) == before
    # A project with nothing to keep has no box: the default is the unmount.
    bare = _make_project(tmp_path / "bare").resolve()
    assert run(capsys, "mount", str(bare))[0] == report.OK
    page = card(run_, bare)
    assert 'id="keep"' not in main_of(page)
    assert "Removes the project files. Your own files stay in place." in text_of(page)
    assert [f["action"] for f in forms(page)] == [manage_web.REVERSAL]
    assert run_.running


def test_the_removal_cards_details_list_a_path_already_gone(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Phase 3 review R9: Details holds every path the removal's plan
    names, as today's plan page did, a file the person already deleted
    included, for the default and the keep box alike. Nothing needs
    attention for it, so it is no attention row."""
    root = connected_host(tmp_path, "pit-wall")
    (root / "docs" / "spec.md").unlink()
    run_ = manage()
    page = card(run_, root)
    drawer = html.unescape(main_of(page).split("<details>", 1)[1].split("</details>", 1)[0])
    halves = re.findall(r'<div class="(remove-all|keep-only)">(.*?)</div>', drawer, re.S)
    assert [name for name, _ in halves] == ["remove-all", "keep-only"]
    for name, inner in halves:
        assert f"<code>docs/spec.md</code> {manage_web.MIDDOT} already gone" in inner, name
    assert "docs/spec.md" not in main_of(page).split("<details>", 1)[0]
    assert run_.running


def test_the_keep_box_runs_the_unmount_alone(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """actions.md row 22: with the box ticked, the press is the unmount's
    own, `radialrig unmount`, and the skills and the connection stay. It
    comes back to the project with its line, and the log keeps the
    unmount's own answer."""
    root = connected_host(tmp_path, "pit-wall")
    twin = connected_host(tmp_path, "twin-wall")
    label = cn.CLIENTS[CLIENT].label
    config = client_here.read_bytes()
    run_ = manage()
    page = card(run_, root)

    before = len(log_events())
    done = pressed(run_, keep_press(page))
    code, out, err = run(capsys, "unmount", str(twin))
    assert code in (report.OK, report.PARTIAL) and err == ""
    assert not mnt.is_mounted(root)
    assert cn.is_connected(root, CLIENT)
    assert ro.verify(root).skills == (SKILL,)
    assert client_here.read_bytes() == config
    assert files_but_record(root) == files_but_record(twin)
    assert result_of(done)[:3] == (
        manage_web.RESULT_DONE,
        f"Removed the project files. Kept the skills, the {label} connection, and your "
        "own files.", "")
    ((what, outcome, lines),) = logged(before)
    assert (what, outcome) == ("press unmount confirm",
                               f"{manage_web.DONE} exit {report.PARTIAL}")
    assert lines[0] == out.splitlines()[0].replace(str(twin), str(root))
    # The box shows only while the project is set up.
    page = card(run_, root)
    assert 'id="keep"' not in main_of(page)
    assert "Removes 1 skill for both agents and the" in text_of(page)
    assert run_.running


def files_but_record(root: Path) -> dict[str, str]:
    return {k: v for k, v in snapshot(root).items()
            if k not in (cn.CONNECT_REL, mnt.MANIFEST_REL)}


def test_a_lost_link_removal_keeps_the_record_and_says_how_to_clear_it(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescription 2 on a lost link: no disconnect step, as today, so the
    client's settings aren't written; the card and the result say the
    project's record stays and how to take it out. Where the project's
    name is its folder's, Repair is offered after, and the line says to
    press it; where it isn't, Repair is withheld after, and the line
    doesn't promise it, naming the record instead (turn 1's trace)."""
    label = cn.CLIENTS[CLIENT].label
    run_ = manage()
    for name, scope in (("pit-wall", "pit-wall"), ("brake-bias", "another-name")):
        root = _host(tmp_path / name)
        mnt.mount(root, scope=scope)
        sk.add_all(root, SKILL)
        cn.connect(root, CLIENT)
        ro.record_seen(root)
        drop_entry(client_here, record_of(root)["key"])
        assert ro.verify(root).connected == ro.CONNECTED_MISMATCH
        config = client_here.read_bytes()

        page = card(run_, root)
        assert not any(line.startswith("radialrig unconnect") for line in commands_on(page))
        assert (f"Removes the project files and 1 skill for both agents. Your own files "
                f"stay in place. {label} already lost its link to this project, so this "
                "leaves its settings alone and keeps this project's connection record. To "
                "take the record out too, repair the link first, then remove.") in part(
                    page, "remove-all")
        assert "Keep the skills</label>" in main_of(page)
        assert '<p class="warn">' not in main_of(page)

        done = pressed(run_, default_press(page))
        assert client_here.read_bytes() == config, "a lost link's removal wrote the settings"
        assert cn.record_path(root).is_file()
        tone, sentence, _, details = result_of(done)
        head = (f"Removed the project files and skills. {label} had already lost its link, "
                "so RadialRig left its settings alone and kept this project's connection "
                "record.")
        if name == "pit-wall":
            assert (tone, sentence) == (manage_web.RESULT_DONE,
                                        f"{head} To take it out, press Repair, then remove "
                                        "RadialRig again.")
            assert status_band(done)[0] == ("repair", f"{label} lost its link to this project.",
                                            "Repair")
        else:
            # Repair is withheld after, so the line says how the record
            # comes out instead: the filled disconnect, behind Details, as
            # the removal page's mismatch note gave it (phase 3 review R5).
            command = manage_web.Action(
                manage_web.UNCONNECT, root=str(root), client=CLIENT).command
            assert (tone, sentence) == (
                manage_web.RESULT_DONE,
                f"{head} RadialRig can't repair the link from here. To take the record out, "
                "run the command in Details.")
            assert manage_web.RECORD_AT.format(path=cn.record_path(root)) in details
            assert command in details
            assert status_band(done)[0][2] == ""
            stuck, stuck_command = root, command

    # Only the records are left. Where Repair is offered, the card and
    # Remove RadialRig's row say to repair; where it isn't, the card, the
    # press and the row say to run the command, which Details or What
    # stays afterward gives.
    def row_of(page: str, name: str) -> str:
        (found,) = [html.unescape(r) for r in ROW.findall(main_of(page))
                    if f">{name}</span>" in r]
        return found

    page = card(run_, tmp_path / "pit-wall")
    assert manage_web.REMOVE_RECORD_ONLY in text_of(page) and forms(page) == []
    page = card(run_, stuck)
    assert ("Only this project's connection record is left, and RadialRig can't repair the "
            "link from here. To take the record out, run the command in Details.") in text_of(page)
    assert forms(page) == []
    assert stuck_command in html.unescape(main_of(page).split("<details>", 1)[1])
    done = pressed(run_, {"action": manage_web.REVERSAL, "root": str(stuck),
                          "confirm": "yes", "from": manage_web.PROJECT})
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_DONE,
        "Only this project's connection record is left, so nothing was changed. RadialRig "
        "can't repair the link from here. To take the record out, run the command in Details.")
    assert stuck_command in details
    page = removal(run_)
    assert manage_web.STEP_RECORD_LEFT in row_of(page, "pit-wall")
    assert ("Only its connection record is left. What stays afterward has the command that "
            "takes it out.") in row_of(page, "brake-bias")
    stays = html.unescape(main_of(page).split('<details class="stays">', 1)[1])
    assert ("brake-bias's connection record stays, because RadialRig can't repair its link "
            "from here. Run this command to take it out.") in stays
    assert stuck_command in stays
    # The command does what the lines say: the record goes.
    assert run(capsys, "unconnect", CLIENT, str(stuck))[0] in (report.OK, report.PARTIAL)
    assert not cn.record_path(stuck).is_file()
    assert manage_web.STEP_NOTHING_LEFT in row_of(removal(run_), "brake-bias")
    assert run_.running


def test_a_refused_removal_offers_no_press_and_a_stale_press_runs_nothing(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Where the engine refuses the plan, the card says why in Details and
    has no press, for the default and the keep box alike; a confirm posted
    anyway asks again inside the queue, runs nothing, and says so."""
    root = connected_host(tmp_path, "pit-wall")
    path = root / mnt.MANIFEST_REL
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["schema"] = int(manifest["schema"]) + 1
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    run_ = manage()
    before = (tree(root), client_here.read_bytes())

    page = card(run_, root)
    refused = ("RadialRig can't remove itself from this project right now. Check Details "
               "to see what's in the way.")
    assert refused in part(page, "remove-all")
    assert refused in part(page, "keep-only")
    assert forms(page) == []
    assert main_of(page).count(PRIMARY) == 0
    assert "written by a newer radialrig" in text_of(page)

    for fields in ({"action": manage_web.REVERSAL, "root": str(root)},
                   {"action": manage_web.UNMOUNT, "root": str(root)}):
        done = pressed(run_, {**fields, "confirm": "yes", "from": manage_web.PROJECT})
        assert (tree(root), client_here.read_bytes()) == before, fields
        assert result_of(done)[:2] == (
            manage_web.RESULT_FAILED,
            "RadialRig can't remove itself from this project right now. Nothing was changed. "
            "Check Details to see what's in the way."), fields
        assert "written by a newer radialrig" in result_of(done)[3]
    assert run_.running


def test_a_removal_that_stops_says_what_was_removed(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    """The sequence stops at the first step that fails, after the steps
    before it wrote, so the line says some of it was removed, and to
    restart the client since its disconnect ran. A first step that stops
    before writing says nothing was changed. With the box ticked, the
    files gone and the record stuck says so."""
    label = cn.CLIENTS[CLIENT].label
    run_ = manage()

    # The unmount's record won't delete: the disconnect and the skill ran.
    root = connected_host(tmp_path, "pit-wall")
    record = root / mnt.MANIFEST_REL
    real_unlink = Path.unlink

    def stuck(self, *args, **kwargs):
        if self == record:
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *args, **kwargs)

    page = card(run_, root)
    monkeypatch.setattr(Path, "unlink", stuck)
    done = pressed(run_, default_press(page))
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert not cn.is_connected(root)
    assert result_of(done)[:2] == (
        manage_web.RESULT_PARTLY,
        "Couldn't finish removing RadialRig from this project. Some of it was removed and "
        f"the rest is still here. Check Details for what stopped it. Fully quit and reopen "
        f"{label} to apply the change.")
    assert f"file error at {mnt.MANIFEST_REL}" in result_of(done)[3]

    # The same record stuck under the box: the files went, the record stays.
    keep = connected_host(tmp_path, "brake-bias")
    record = keep / mnt.MANIFEST_REL
    page = card(run_, keep)
    monkeypatch.setattr(Path, "unlink", stuck)
    done = pressed(run_, keep_press(page))
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert result_of(done)[:2] == (
        manage_web.RESULT_PARTLY,
        "Removed the project files, but couldn't delete RadialRig's record of them, so "
        "this project still shows as set up. Check Details for the record.")

    # The first step stops before writing: nothing changed. The client's
    # settings won't take a write, as a file another program holds won't.
    fresh = connected_host(tmp_path, "fuel-map")
    page = card(run_, fresh)
    before = (tree(fresh), client_here.read_bytes())
    real_write = Path.write_bytes

    def refused(self, data):
        if self.name == client_here.name:
            raise PermissionError(13, "Permission denied", str(self))
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", refused)
    done = pressed(run_, default_press(page))
    monkeypatch.setattr(Path, "write_bytes", real_write)
    assert (tree(fresh), client_here.read_bytes()) == before
    assert result_of(done)[:2] == (
        manage_web.RESULT_FAILED,
        "Couldn't remove RadialRig from this project. Nothing was changed. Check Details "
        "for what stopped it.")
    assert run_.running


def test_a_removal_while_the_client_is_open_carries_both_warnings(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescription 6: while the client is open, the default's half of the
    card carries the before-press sentence, since its disconnect writes
    the client's settings; the keep box's half carries none, nor a lost
    link's card. The result carries the after-press sentence once."""
    client = cn.CLIENTS[CLIENT]
    root = connected_host(tmp_path, "pit-wall")
    keys = tuple(record_of(root)["entries"])
    lost = connected_host(tmp_path, "brake-bias")
    drop_entry(client_here, record_of(lost)["key"])
    plant_lock(client_here)
    ahead = report.client_running_before(client)
    run_ = manage()

    page = card(run_, root)
    assert ahead in part(page, "remove-all")
    assert ahead not in part(page, "keep-only")
    assert ahead not in text_of(card(run_, lost))

    done = pressed(run_, default_press(page))
    tone, _, warning, details = result_of(done)
    assert warning == (f"{client.label} is running and may put back what this removed. Fully "
                       f"quit it, then remove {' and '.join(keys)} from its settings file if "
                       "they're back.")
    assert report.unconnect_running_after(client, keys) not in details
    assert text_of(done).count(f"{client.label} is running and may") == 1
    assert run_.running


def test_a_removal_whose_disconnect_kept_an_edited_settings_file_says_so(
    tmp_path, env, command, capsys, manage, fixed_day,
):
    """Phase 3 review R6: Claude Desktop's settings file is one RadialRig
    created, and the person has since added a preference of their own.
    The link still reads as healthy, so the removal disconnects; the
    disconnect keeps the edited file whole, entries and all, and drops
    only the project's record. The line says that, never that the
    connection was removed, and asks for no restart: no setting changed."""
    config = roaming_config(env)
    assert not config.exists()
    root = connected_host(tmp_path, "pit-wall")
    body = json.loads(config.read_text(encoding="utf-8"))
    body["preferences"] = {"theme": "dark"}
    config.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    edited = config.read_bytes()
    assert ro.verify(root).connected == CLIENT
    label = cn.CLIENTS[CLIENT].label
    run_ = manage()

    done = pressed(run_, default_press(card(run_, root)))
    assert config.read_bytes() == edited
    assert not cn.record_path(root).is_file()
    assert not mnt.is_mounted(root)
    tone, sentence, warning, details = result_of(done)
    assert (tone, sentence, warning) == (
        manage_web.RESULT_PARTLY,
        "Removed RadialRig files and skills. Your own files stayed in place. RadialRig "
        f"removed this project's connection record but left its entry in one {label} "
        "settings file. Check Details to see which one.", "")
    assert f"{config.as_posix()} {manage_web.MIDDOT} kept" in details
    assert f"{cn.CONNECT_REL} {manage_web.MIDDOT} removed" in details
    assert run_.running


def test_reversal_respects_edited_files(
    tmp_path, env, command, capsys, manage, fixed_day,
):
    """Constraint 5: a file this project changed is never taken. The card
    names it as the one row that needs attention, the press keeps it, and
    the line says so."""
    root = _make_project(tmp_path / "host").resolve()
    assert run(capsys, "mount", str(root))[0] == report.OK
    assert run(capsys, "skill", "add", SKILL, str(root))[0] == report.OK
    edited = next(p for p in sorted((root / SKILL_DIR).rglob("*")) if p.is_file())
    ours = b"our own words, in this project's own file\n"
    edited.write_bytes(ours)
    rel = edited.relative_to(root).as_posix()

    run_ = manage()
    page = card(run_, root)
    assert edited.read_bytes() == ours, "the card wrote something"
    assert f"{rel} Keeping your edits" in part(page, "remove-all")
    done = pressed(run_, default_press(page))
    assert edited.read_bytes() == ours
    tone, sentence, _, details = result_of(done)
    assert (tone, sentence) == (
        manage_web.RESULT_DONE,
        "Removed RadialRig files and skills. Your own files stayed in place. Kept your "
        "edited file.")
    assert f"{rel} {manage_web.MIDDOT} kept" in details
    assert not mnt.is_mounted(root)


def test_a_removal_that_names_no_project_removes_nothing(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    r"""The empty field is the dangerous one, and so is the relative one.

    `Path("")` is `Path(".")`, it is a directory, and it resolves to
    wherever this server was started. The working directory here is a
    mounted project carrying a skill that the roster has never heard of,
    so a press that reached it would be removing the project nobody
    named. The card, its default press and its keep box's press go
    through the one resolver every press uses.
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
    for action in (manage_web.REVERSAL, manage_web.UNMOUNT):
        for root in ("", ".", "..", None):
            fields = {"action": action, "from": manage_web.PROJECT}
            if root is not None:
                fields["root"] = root
            for confirm in (False, True):
                status, page, _ = run_.post(press(run_, fields, confirm=confirm))
                assert status == 200, (fields, confirm)
                assert tree(here) == before, (fields, confirm)
                assert mnt.is_mounted(here), (fields, confirm)
                assert [entry["root"] for entry in ro.load().entries] == listed
                said = text_of(page)
                assert manage_web.ROSTER_HEAD in said, (fields, confirm)
                assert "no such directory" not in said, (fields, confirm)

    # The same press naming a project still removes that one.
    done = pressed(run_, default_press(card(run_, named)))
    assert result_of(done)[0] == manage_web.RESULT_DONE
    assert not mnt.is_mounted(named)
    assert tree(here) == before
    assert run_.running


# --- Remove RadialRig ----------------------------------------------------

def test_remove_radialrig_numbers_its_steps_with_the_app_last(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """Prescriptions 3 and 4: the steps are each project with Remove, or
    what is left; the engine's removal commands, every one in today's
    order, in one block with one Copy; and, where this system has an app
    row, its uninstall, last, because it takes the folder holding uv off
    the Path. Neither of the last two is a press, and this module starts
    no program."""
    alpha = connected_host(tmp_path, "alpha")
    bare = _make_project(tmp_path / "bare").resolve()
    assert run(capsys, "mount", str(bare))[0] == report.OK
    assert run(capsys, "unmount", str(bare))[0] == report.OK
    lost = connected_host(tmp_path, "lost")
    drop_entry(client_here, record_of(lost)["key"])
    for argv in (("skill", "remove", SKILL, str(lost)), ("unmount", str(lost))):
        assert run(capsys, *argv)[0] in (report.OK, report.PARTIAL), argv
    assert manage_web.reversal_steps(ro.verify(lost)) == ()

    run_ = manage()
    assert any(f"view={manage_web.REMOVAL}" in target
               for target in navigations(run_.page(), run_.session.token))
    page = removal(run_)
    assert screen_of(page) == "removal"
    assert "<h1>Remove RadialRig</h1>" in main_of(page)
    row = manage_web.machine()
    assert row is not None
    expected = ["Take RadialRig out of your projects", "Remove the engine and its tools"]
    if row.app_place:
        expected.append("Uninstall the Windows app")
    assert steps_of(page) == expected

    # Step 1: a row a project, with Remove or what's left.
    rows = {html.unescape(re.search(r'class="name"[^>]*>([^<]*)<', r).group(1)): r
            for r in ROW.findall(main_of(page))}
    assert set(rows) == {"alpha", "bare", "lost"}
    (fields,) = [dict(HIDDEN.findall(f)) for f in POST_FORM.findall(rows["alpha"])]
    fields.pop("token")
    assert fields == {"action": manage_web.REVERSAL, "root": str(alpha),
                      "from": manage_web.REMOVAL}
    assert 'aria-label="Remove alpha"' in rows["alpha"]
    assert f'<span class="facts">{manage_web.STEP_NOTHING_LEFT}</span>' in rows["bare"]
    assert html.unescape(re.search(r'<span class="facts">([^<]*)</span>', rows["lost"])
                         .group(1)) == manage_web.STEP_RECORD_LEFT

    # Step 2: every command, in today's order, one Copy for all of them.
    shown, copied = engine_block(page)
    assert shown == named_removals()
    assert copied == "\n".join(named_removals())
    assert main_of(page).count('class="copy"') == 1
    assert manage_web.ENGINE_REMOVAL not in manage_web.COMMANDS.values()
    assert {f["action"] for f in forms(page)} == {manage_web.REVERSAL}
    source = Path(manage_web.__file__).read_text(encoding="utf-8")
    for spelling in ("subprocess", "Popen", "os.system", "os.exec"):
        assert spelling not in source, spelling

    # Step 3, where it stands, is last and says why it waits for step 2.
    said = text_of(page)
    if row.app_place:
        assert f"{manage_web.STEP_APP_SAID} {row.path_what}" in said
        assert said.index(manage_web.STEP_APP_SAID) > said.index(named_removals()[-1])

    # What stays afterward, behind Details: the history and where it is.
    stays = main_of(page).split('<details class="stays">', 1)[1].split("</details>", 1)[0]
    assert f"<summary>{manage_web.STAYS_LABEL}</summary>" in stays
    assert manage_web.STAYS_SAID.format(data_dir=ro.roster_path().parent) in html.unescape(stays)
    assert run_.running


def test_the_removal_page_names_every_machine_change_the_first_run_made(
    tmp_path, env, command, capsys, manage, fixed_day,
):
    """Every machine change the app's first run made is on the page: uv,
    a Python for the engine, uv's cache, and the Path, each command in the
    order that leaves every one a uv to run it. The Path is stated either
    way, this platform's own sentence and no other's (PLAN-0009 phase 3
    prescription 5): on Windows in step 3, on Linux first in What stays
    afterward. The page no longer lists `REMAINS_PATH`: after step 3 the
    entry is gone, unless the uninstall says which to remove, so it isn't
    something that stays."""
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
        manage_web.ENGINE_REMOVAL, python_line, manage_web.UV_CACHE_REMOVAL, uv_line]
    assert engine_block(page)[0] == named_removals()

    uv_exe, uvx_exe = manage_web.uv_paths(row)
    assert uv_line == row.uv_delete.format(uv=uv_exe, uvx=uvx_exe)
    assert "%" not in uv_line and "$" not in uv_line
    for path in (uv_exe, uvx_exe):
        assert Path(path).is_absolute(), path
        assert Path(path).parent == Path.home().joinpath(*row.uv_bin)

    assert python_line == f"uv python uninstall {version}"
    assert "--all" not in page
    source = Path(manage_web.__file__).read_text(encoding="utf-8")
    assert "python uninstall --all" not in source
    assert "--all" not in manage_web.PYTHON_REMOVAL
    assert all("skill add --all" in line or "upgrade --all" in line
               for line in source.splitlines() if "--all" in line)

    # The Path, stated either way, this platform's own line and no other's.
    assert row.path_what in said
    for other in manage_web.MACHINES:
        if other.path_what != row.path_what:
            assert other.path_what not in said
    assert manage_web.REMAINS_PATH not in said
    assert {f["action"] for f in forms(page)} <= {manage_web.REVERSAL}
    assert run_.running


def test_linux_shows_two_steps_and_its_path_sentence_in_what_stays(
    tmp_path, monkeypatch, env, command, capsys, manage, fixed_day,
):
    """Linux has no app row today, so the page shows two steps; the
    commands are pasted into a terminal; and the PATH sentence, which no
    step says there, is the first of What stays afterward. A system with
    no row at all shows the two commands today's page names there."""
    linux = next(row for row in manage_web.MACHINES if row.platform == "linux")
    monkeypatch.setattr(manage_web, "machine", lambda: linux)
    root = _make_project(tmp_path / "host").resolve()
    assert run(capsys, "mount", str(root))[0] == report.OK
    run_ = manage()
    page = removal(run_)
    assert steps_of(page) == ["Take RadialRig out of your projects",
                              "Remove the engine and its tools"]
    said = text_of(page)
    assert manage_web.STEP_ENGINE_TERMINAL in said
    assert "PowerShell" not in said and "Windows" not in said
    assert engine_block(page)[0] == named_removals()
    assert named_removals()[-1].startswith('rm "')
    stays = main_of(page).split('<details class="stays">', 1)[1]
    first = re.search(r'<div class="drawer"><p>([^<]*)</p>', stays).group(1)
    assert html.unescape(first) == manage_web.LINUX_PATH_WHAT == (
        "RadialRig didn't change your PATH on this computer, so there's nothing to take "
        "off it.")

    monkeypatch.setattr(manage_web, "machine", lambda: None)
    page = removal(run_)
    assert len(steps_of(page)) == 2
    assert engine_block(page)[0] == [
        manage_web.ENGINE_REMOVAL,
        manage_web.PYTHON_REMOVAL.format(
            version=f"{sys.version_info.major}.{sys.version_info.minor}")]
    assert run_.running


def test_a_project_removed_from_remove_radialrig_comes_back_there(
    tmp_path, env, client_here, command, capsys, manage, fixed_day,
):
    """A row's Remove opens the card for that project, whose Cancel and
    press both come back to Remove RadialRig, the press with its line
    naming the project (wording.md, Screen 6)."""
    alpha = connected_host(tmp_path, "alpha")
    label = cn.CLIENTS[CLIENT].label
    run_ = manage()
    page = removal(run_)
    (fields,) = [f for f in forms(page)]
    asked = pressed(run_, fields)
    assert "<h1>Remove RadialRig from alpha?</h1>" in main_of(asked)
    (cancel,) = [t for t in navigations(asked, run_.session.token)
                 if f"view={manage_web.REMOVAL}" in t]
    assert default_press(asked)["from"] == manage_web.REMOVAL

    done = pressed(run_, default_press(asked))
    assert screen_of(done) == "removal"
    assert result_of(done)[:2] == (
        manage_web.RESULT_DONE,
        f"Removed RadialRig files, skills, and the {label} connection from alpha. Your "
        f"own files stayed in place. Fully quit and reopen {label} to apply the change.")
    body = main_of(done)
    assert body.index('<div class="result done"') < body.index('<ol class="numbered">')
    (row,) = ROW.findall(body)
    assert manage_web.STEP_NOTHING_LEFT in row
    assert not mnt.is_mounted(alpha)
    assert run_.running


def test_the_removal_page_names_no_client_where_none_is_recorded(
    tmp_path, monkeypatch, env, client_here, command, capsys, manage, fixed_day,
):
    """A project never connected names no client on Remove RadialRig, nor
    on its card. A machine with no client names none at all, and its one
    project still comes out."""
    bare = _make_project(tmp_path / "bare").resolve()
    assert run(capsys, "mount", str(bare))[0] == report.OK
    run_ = manage()
    page = removal(run_)
    (row,) = ROW.findall(main_of(page))
    for client in cn.CLIENTS.values():
        assert client.label not in row and client.name not in row
    asked = card(run_, bare, manage_web.REMOVAL)
    for client in cn.CLIENTS.values():
        assert client.label not in main_of(asked)

    no_client = tmp_path / "no-client"
    monkeypatch.setenv("APPDATA", str(no_client))
    monkeypatch.setenv(STORE_VAR, str(no_client))
    alone = _make_project(tmp_path / "alone").resolve()
    assert run(capsys, "mount", str(alone))[0] == report.OK
    assert cn.detect(cn.CLIENTS[CLIENT]) == ()

    solo = manage()
    page = removal(solo)
    for client in cn.CLIENTS.values():
        assert client.label not in main_of(page)
        assert client.name not in main_of(page)
    done = pressed(solo, default_press(card(solo, alone, manage_web.REMOVAL)))
    assert result_of(done)[:2] == (
        manage_web.RESULT_DONE, "Removed RadialRig from alone. Your own files stayed in place.")
    assert not mnt.is_mounted(alone)
    for client in cn.CLIENTS.values():
        assert client.label not in done
