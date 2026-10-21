"""Suite-wide isolation of the per-user profile variables.

PLAN-0008 Phase 3, widened by PLAN-0009 Phase 2. The roster lives at a
path built from the `STORES` row serving this platform, and the connect
kit finds a client's settings from `APPDATA`, `LOCALAPPDATA` and `HOME`.
Once the four verbs keep the roster as a side entry, any test that
drives a verb through `cli.main` would write this developer's own
roster. So the isolation is set here, once, for every test in the suite
rather than module by module.

Every variable points at a fresh directory under the test's own
`tmp_path`, which does not exist until something creates it: discovery
finds no client there, and a roster write lands inside tmp_path. The
set spans both platforms' tables on every platform — a variable no
table row reads here is harmless, and one a row does read is the leak.
A test that wants one gone, or pointed at a fake profile of its own,
sets it in its own fixture and wins — monkeypatch layers, and undoes in
reverse.

`HOME` on Linux also moves `Path.home()`, which is what keeps the
manage page's uv paths and the connect kit's config-directory lookup
inside tmp_path. On Windows `Path.home()` reads `USERPROFILE`, which
stays real: nothing in the product builds a written path from it.
"""

from __future__ import annotations

import pytest

PROFILE_VARS = ("APPDATA", "LOCALAPPDATA", "HOME", "XDG_DATA_HOME")


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    """Point the per-user profile variables inside this test's tmp_path."""
    for name in PROFILE_VARS:
        monkeypatch.setenv(name, str(tmp_path / "profile" / name.lower()))
