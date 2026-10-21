"""Where the corpus is and where the packaged guidance copies are.

The engine runs against a host project it was mounted onto, so the corpus
root cannot be derived from the package's own location. Resolution order:
the environment override, then the nearest ancestor of the working
directory carrying a mount marker, then the working directory itself.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

ROOT_ENV = "RADIALRIG_DOCS_ROOT"

# A directory is a corpus root when it carries the rulebook SPEC-1 names,
# or the mount manifest file PLAN-0005 Phase 2 lays down. The marker is
# the manifest file, not the bare .radialrig directory: other software
# also uses a ~/.radialrig directory, and a bare-directory marker would
# resolve the corpus root to the home directory from any cwd beneath it.
MARKERS = ("docs/spec.md", ".radialrig/mount.json")

PACKAGED_RESEARCH = ("data", "research")
PACKAGED_SEED = ("data", "seed")
PACKAGED_SKILLS = ("data", "skills")
PACKAGED_WEB = ("data", "web")


def resolve_root(start: Path | None = None) -> Path:
    """Corpus root: the env override, else the nearest marked ancestor of
    the working directory, else the working directory."""
    env = os.environ.get(ROOT_ENV, "").strip()
    if env:
        return Path(env)
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        for marker in MARKERS:
            if (candidate / marker).exists():
                return candidate
    return here


def _packaged_dir(parts: tuple[str, ...]) -> Path | None:
    """A directory shipped inside the installation, or None.

    The installed layout is a directory tree; a zip-imported installation
    is out of scope and answers None rather than guessing.
    """
    try:
        base = Path(str(files(__package__).joinpath(*parts)))
    except (ModuleNotFoundError, TypeError, NotADirectoryError):
        return None
    return base if base.is_dir() else None


def packaged_research() -> Path | None:
    """The guidance corpus shipped inside the installation, or None."""
    return _packaged_dir(PACKAGED_RESEARCH)


def packaged_seed() -> Path | None:
    """The mount seed sources shipped inside the installation, or None.

    Same resolution as packaged_research, so package data has one access
    convention rather than two (PLAN-0005 Phase 2).
    """
    return _packaged_dir(PACKAGED_SEED)


def packaged_skills() -> Path | None:
    """The skill sources shipped inside the installation, or None.

    Same resolution as packaged_seed, so package data has one access
    convention rather than two (D-36, PLAN-0006 Phase 1). Unlike the
    seed, these sources carry no token substitution: the bytes a host
    receives are the bytes the package ships.
    """
    return _packaged_dir(PACKAGED_SKILLS)


def packaged_web() -> Path | None:
    """The setup page's assets shipped inside the installation, or None.

    The page is served from these bytes and asks the network for nothing
    (PLAN-0005 Phase 4).
    """
    return _packaged_dir(PACKAGED_WEB)
