"""The combined update: a project's seeded files and the skills it has.

PLAN-0021 phase 1, prescription 2. `radialrig upgrade` and the manage
page's two update presses run this one step: the docs kit's upgrade
first, then the skills kit's refresh. The two kits keep their own
records and their own writes; this module orders them and reports them
as one, so a press and the command line cannot disagree about what an
update did.

`plan` composes both halves and writes nothing; `run` writes the seed
half through `mount.upgrade` and then the skills half through
`skills.refresh`, each in its own all-or-nothing write. A skills step
that fails after the seeded files were written is reported, not raised:
the seed block says what was written, and the skills half says what
failed. Each agent stands alone inside that half: a record this build
cannot read is reported under that agent's own head, and the other
agent's skills still come up. `behind` answers Decided 6: a project is
behind when its update would write something — `upgrade_plan` would
write, or some skill it has is clean, not current and not held back,
for some agent (`skills.behind`).

Exit 5 follows the rule the seed half already uses: every row LEFT or
ABSENT is left for review, so a skill with such a row counts — an
edited copy, one whose folder was removed, one this package no longer
ships, one refused because this project owns a file at a path the new
release ships — and a current one does not. The closing line counts
the skills by the same rule.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from radialrig import mount as mnt
from radialrig import roster as ro
from radialrig import skills as sk

# Why a listed project was skipped by `--all`. Codes, not sentences: the
# words are `report`'s, so both surfaces say them from one place.
SKIP_GONE = "gone"              # the folder is no longer on this disk
SKIP_NOT_MOUNTED = "not-mounted"  # no mount record, so the upgrade does nothing


@dataclass(frozen=True)
class UpdatePlan:
    """Both halves of one update, composed or applied."""

    root: Path
    seed: mnt.UpgradePlan
    skills: tuple[sk.RefreshResult, ...]
    # The skills kit's one sentence where its half could not run as a
    # whole: a write that stopped. Empty when the half ran; an agent
    # whose record could not be read is that agent's own result.
    skills_error: str = ""

    @property
    def refreshed(self) -> int:
        return sum(1 for r in self.skills if r.wrote)

    @property
    def pending_skills(self) -> int:
        """Skills left for a person to read, by the rule the seed half's
        `pending` uses: a row LEFT or ABSENT. A current skill is not one.
        An agent's unread record is not a skill, so it is not counted
        here; `review` still carries it."""
        return sum(1 for r in self.skills if r.name and r.pending)

    @property
    def unread_agents(self) -> tuple[sk.RefreshResult, ...]:
        """The agents whose record this build could not read, each with
        the kit's sentence."""
        return tuple(r for r in self.skills if r.error)

    @property
    def review(self) -> bool:
        """Whether anything was left for a person to read: a seeded file
        left or absent, a skill left or absent, an agent's record this
        build could not read, or a skills half that did not run. The
        command line's exit 5."""
        return (
            self.seed.pending > 0 or self.pending_skills > 0
            or bool(self.unread_agents) or bool(self.skills_error)
        )

    @property
    def wrote(self) -> bool:
        return bool(self.seed.writes) or any(r.wrote for r in self.skills)


def _skills_half(compose, root: Path) -> tuple[tuple[sk.RefreshResult, ...], str]:
    """The skills half, or the sentence that stopped it.

    The skills kit's own error is caught here and nowhere above: the seed
    half may already be written by the time this runs, and a report that
    said nothing about it would be false. What reaches here is a write
    stopped part way, the kit's `SkillError` carrying the writer's
    sentence, which names what was put back and what was not; a record
    one agent's build cannot read never does, since `refresh_plan`
    answers it as that agent's own result.
    """
    try:
        return compose(root), ""
    except sk.SkillError as exc:
        return (), str(exc)


def plan(root: Path) -> UpdatePlan:
    """What `run` would write here, both halves. Writes nothing.

    The seed half's errors are the mount kit's own and go on to the
    caller, as `upgrade_plan`'s do: a manifest this build cannot read
    stops the whole update before anything is composed.
    """
    root = Path(root).resolve()
    seed = mnt.upgrade_plan(root)
    skills, error = _skills_half(sk.refresh_plan, root)
    return UpdatePlan(root=root, seed=seed, skills=skills, skills_error=error)


def run(root: Path, *, today: dt.date | None = None) -> UpdatePlan:
    """The seed upgrade, then the skills refresh, each all-or-nothing."""
    root = Path(root).resolve()
    seed = mnt.upgrade(root, today=today)
    skills, error = _skills_half(
        lambda r: sk.refresh(r, today=today), root)
    return UpdatePlan(root=root, seed=seed, skills=skills, skills_error=error)


@dataclass(frozen=True)
class ProjectUpdate:
    """One listed project under `--all`: its update, or why it had none.

    Exactly one of the three is filled. `skipped` carries a code from
    the two above; `error` carries the mount kit's own sentence for an
    update that stopped before it composed, which `--all` reports in
    the project's place rather than stopping the run.
    """

    root: str                      # as the listing stored it
    plan: UpdatePlan | None = None
    skipped: str = ""
    error: str = ""


def _every_listed(step) -> tuple[ProjectUpdate, ...]:
    """`step` over every mounted project in the listing whose folder is
    there, in listing order. A folder that is gone and a project with
    no mount record are named as skipped; a project whose update stops
    is named with the sentence that stopped it, and the rest still run."""
    out: list[ProjectUpdate] = []
    for state in ro.listing():
        if not state.exists:
            out.append(ProjectUpdate(state.root, skipped=SKIP_GONE))
            continue
        if not state.mounted:
            out.append(ProjectUpdate(state.root, skipped=SKIP_NOT_MOUNTED))
            continue
        try:
            out.append(ProjectUpdate(state.root, plan=step(Path(state.root))))
        except (mnt.MountError, OSError) as exc:
            out.append(ProjectUpdate(state.root, error=str(exc)))
    return tuple(out)


def plan_all() -> tuple[ProjectUpdate, ...]:
    """What `run_all` would write, project by project. Writes nothing
    of its own; the listing's own drop of folders that are gone is the
    one write it shares with `radialrig projects`."""
    return _every_listed(plan)


def run_all(*, today: dt.date | None = None) -> tuple[ProjectUpdate, ...]:
    """`run` on every mounted project in the listing whose folder is there."""
    return _every_listed(lambda root: run(root, today=today))


def behind(root: Path) -> bool:
    """Whether an update would write something here (Decided 6).

    Never raises: the listing asks this of every project, and a project
    whose records this build cannot read is one whose update would
    write nothing, so it reads as not behind. An unmounted project is
    not behind for the same reason — the upgrade does nothing there.
    """
    root = Path(root).resolve()
    try:
        if not mnt.is_mounted(root):
            return False
    except OSError:
        return False
    try:
        if mnt.upgrade_plan(root).writes:
            return True
    except (mnt.MountError, OSError):
        pass
    # Agent by agent, so one agent's unreadable record costs that agent's
    # answer alone, the way the listing reads the records (`roster._skills`).
    for harness in sk.harnesses():
        try:
            if any(sk.behind(s) for s in sk.states(root, harness=harness)):
                return True
        except (sk.SkillError, OSError):
            continue
    return False
