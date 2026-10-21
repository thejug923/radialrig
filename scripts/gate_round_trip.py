"""The gate's scripted round trip. PLAN-0009 Phase 2 prescription 8.

One clean pass over the whole engine surface, driven the way a user
drives it, against a throwaway project and a fixture client file:

1. mount a throwaway project
2. add a skill
3. list the projects
4. register the fixture client, and read the entry back off its file
5. serve `manage` with no browser and fetch its pages
6. unconnect, unmount, and confirm the project is byte for byte as it was

On Linux the fetched pages are also swept for Windows leakage: a page
that names a Windows path, a `.exe`, or PowerShell fails the run. That
sweep is half of the phase 2 done-condition; the suite is the other
half.

The script exits 0 when every step passed and 1 at the first that did
not, with the step named on stderr. It writes only under a temporary
directory of its own.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

SKILL = "session-handoff"

# What no page may say on Linux: a Windows path, a Windows program, or
# a Windows shell. Checked against the raw HTML of every fetched page.
WINDOWS_MARKS = (
    re.compile(r"\.exe\b", re.I),
    re.compile(r"powershell", re.I),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"%[A-Z_]+%"),
)


def fail(step: str, detail: str = "") -> None:
    line = f"round trip failed at: {step}"
    if detail:
        line += f" - {detail}"
    print(line, file=sys.stderr)
    sys.exit(1)


def run(env: dict, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, "-m", "radialrig.cli", *args],
        env=env, capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        fail(" ".join(args), f"exit {result.returncode}: {result.stderr.strip()}")
    print(f"ok: radialrig {' '.join(args)}")
    return result


def snapshot(root: str) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for base, _, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            out[os.path.relpath(path, root)] = open(path, "rb").read()
    return out


def fetch(url: str) -> tuple[str, str]:
    """(final url, page text) for one GET, redirects followed."""
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.geturl(), response.read().decode("utf-8")


def sweep(name: str, page: str) -> None:
    """The Linux leakage sweep. Windows keeps its own words."""
    if sys.platform == "win32":
        return
    for mark in WINDOWS_MARKS:
        found = mark.search(page)
        if found:
            fail(f"page sweep: {name}", f"page carries {found.group(0)!r}")
    print(f"ok: page sweep {name}")


def main() -> int:
    keep = tempfile.mkdtemp(prefix="radialrig-round-trip-")
    project = os.path.join(keep, "throwaway-project")
    os.makedirs(project)
    with open(os.path.join(project, "README.md"), "wb") as stream:
        stream.write(b"# Throwaway\n\nA project for the gate.\n")

    # The fixture profile: one directory the client-file lookup finds on
    # either platform's row, and one base for the roster's row.
    home = os.path.join(keep, "home")
    os.makedirs(os.path.join(home, ".config", "Claude"))
    appdata = os.path.join(keep, "appdata")
    os.makedirs(os.path.join(appdata, "Claude"))
    env = dict(os.environ)
    env.update({
        "HOME": home,
        "XDG_DATA_HOME": os.path.join(keep, "data"),
        "APPDATA": appdata,
        "LOCALAPPDATA": os.path.join(keep, "local"),
    })

    before = snapshot(project)

    run(env, "mount", project)
    run(env, "skill", "add", SKILL, project)

    listing = run(env, "projects").stdout
    if "mounted" not in listing:
        fail("projects", "the mounted project is not listed")

    run(env, "connect", "claude-desktop", project)
    if sys.platform == "win32":
        settings = os.path.join(appdata, "Claude", "claude_desktop_config.json")
    else:
        settings = os.path.join(home, ".config", "Claude",
                                "claude_desktop_config.json")
    try:
        with open(settings, encoding="utf-8") as stream:
            body = json.load(stream)
    except (OSError, ValueError) as exc:
        fail("connect", f"no readable fixture settings file: {exc}")
    servers = body.get("mcpServers", {})
    if not any(key.startswith("radialrig-") for key in servers):
        fail("connect", "the fixture settings file carries no radialrig entry")
    print("ok: the fixture client file carries the entry")

    listing = run(env, "projects").stdout
    if "registered with" not in listing:
        fail("projects", "the connect is not on the listing")

    # The manage server: launch from its printed line, redeem the launch
    # token for the session token, fetch the listing and the removal
    # view, and stop the process.
    manage = subprocess.Popen(
        [sys.executable, "-m", "radialrig.cli", "manage", "--no-browser"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    try:
        line = manage.stdout.readline().strip()
        if not line.startswith("manage: "):
            fail("manage", f"first line is {line!r}")
        launch_url = line[len("manage: "):]
        final, page = fetch(launch_url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(final).query)
        token = (query.get("s") or [""])[0]
        if not token:
            fail("manage", "the launch redeemed no session token")
        sweep("listing", page)
        base = final.split("?", 1)[0]
        _, removal = fetch(f"{base}?s={token}&view=removal")
        sweep("removal", removal)
        print("ok: manage served its pages")
    finally:
        manage.terminate()
        try:
            manage.wait(timeout=10)
        except subprocess.TimeoutExpired:
            manage.kill()

    run(env, "unconnect", "claude-desktop", project)
    if os.path.exists(settings):
        fail("unconnect", "the fixture settings file was not taken back out")

    # Skills carry their own removal verb; unmount deliberately leaves
    # them, so the reversal runs both.
    run(env, "skill", "remove", SKILL, project)
    run(env, "unmount", project)
    if snapshot(project) != before:
        fail("unmount", "the project is not as it was")
    print("ok: the project is byte for byte as it was")

    shutil.rmtree(keep, ignore_errors=True)
    print("round trip: every step passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
