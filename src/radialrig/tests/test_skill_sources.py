"""Conformance of the shipped skill bytes - D-37, PLAN-0006 Phase 4.

Run:  uv run --no-sync --with pytest pytest src/radialrig/tests/test_skill_sources.py -q
      uv run --no-sync --with pytest pytest -q -k session-handoff

A skill ships to a stranger's project, where nobody reads a diff before
an agent obeys it. Everything this repository would otherwise enforce by
review therefore has to ride inside a mechanical check, and this file is
that check. Three properties, in order of what they cost when they fail:

1. **The scrub** (D-37 rule 3). No shipped byte names the repository the
   rewrite was cut from, its private working directories, or any path
   that exists on one machine. The failure is silent and permanent: a
   host agent reads a path it cannot resolve and improvises.
2. **The budgets** (D-37 rules 1 and 4). The description is always-loaded
   metadata, paid in every session of every host whether or not the skill
   fires, so it is capped at 250 characters; the body is paid only when
   the skill triggers, and is capped against the ~5k-token budget the
   documentation-bloat guidance states, estimated here at bytes/4.
3. **The anti-drift oracle.** The output template in SKILL.md and
   REQUIRED_SECTIONS in scripts/handoff.py are two statements of one
   list. Left unpinned they drift, and the drift shows up as a handoff
   that passes its own check while missing a section the reader needs.

The file is in two halves, and the split is what a check is *about*:

- **Generic** (D-37 rules 1-4): the scrub, the byte rules, the closed
  frontmatter, and the budgets. These bind every shipped skill, so they
  are parametrised over the directories found under `packaged_skills()`
  rather than named one by one. A skill directory added to the package
  is swept the day it lands, with no edit here; each case carries the
  skill's own name, so `-k <skill-name>` selects one skill's checks.
- **Specific** (D-37 rules 5-8, plus the oracle): the template list, the
  version and footer pins, the consent-tier phrasings, and the behaviour
  of scripts/handoff.py. These are statements about session-handoff, and
  they stay scoped to it - generalising them would assert of a future
  skill something nobody ruled.

The behavioural half of the file exercises scripts/handoff.py directly,
because its three guards - move rather than delete, refuse a target
outside a handoffs directory, never claim a copy that failed - are the
constraints prose could not hold on its own.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from radialrig.paths import packaged_skills

SKILL = "session-handoff"
SCRIPT_REL = "scripts/handoff.py"

# What a handoff must contain, restated as a literal. The test must not
# import the list it is checking from either side of the oracle, or a
# wrong list would agree with itself in both places.
EXPECTED_SECTIONS = (
    "Standing constraints",
    "Decisions locked + what shipped",
    "Still broken",
    "Superseded this session",
    "Key files",
    "Running state",
    "Verification",
    "Deferred + open questions",
    "Pick up here",
)

# D-37 rule 3, as patterns. The drive-letter and home-relative forms are
# paths that exist on exactly one machine. The names of the source the
# skills were rewritten from, its folders and its documents, are swept
# too, by a module that stays in the private workshop: spelling them
# here would publish them (PLAN-0023 phase 1, prescription 3).
BANNED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[A-Za-z]:[\\/]", "a drive-letter absolute path"),
    (r"~/", "a home-relative path"),
    (r"(?i)testbed", "a private testbed"),
    (r"reverify_by", "a date nothing in a host enforces"),
    (r"/clear\b", "a session-start command that differs by harness"),
)

# D-37 rule 2, as tokens. A shipped byte may state conformance and may
# not state effect: nothing in evals/ measured any of this. The
# drive-letter pattern above also forbids a URL, which is the same rule
# read from the other side - an effect claim usually arrives as a
# citation of somebody else's numbers.
MEASURED_CLAIMS = ("% ", "percent", "measured", "benchmark", "outperform", "faster than")

# Pictographic ranges, checked separately from the ASCII rule below.
EMOJI_RANGES = ((0x2190, 0x2BFF), (0x1F000, 0x1FAFF), (0xFE00, 0xFE0F), (0x2600, 0x27BF))

# Frontmatter is closed (D-37 rule 1): exactly these three keys.
EXPECTED_FRONTMATTER_KEYS = {"name", "description", "radialrig"}

MAX_LINES = 500              # D-37 rule 4, the outer ceiling
MAX_DESCRIPTION = 250        # D-37 rule 4
MAX_BODY_BYTES = 20_000      # ~5k tokens at the bytes/4 estimator

# The budget every packaged skill meets, tighter than the ceilings above
# and pinned deliberately close to what session-handoff 3.x spends. It
# began as that rewrite's own budget and was adopted as the general one:
# it is the rule "every addition is paid for" made mechanical, and an
# author who wants another section has to find the room first rather
# than let the file creep. A skill that cannot fit is two skills, or an
# owner ruling that raises the number here for all of them.
BUDGET_LINES = 190
BUDGET_BODY_BYTES = 9_000


# --- which skills there are -------------------------------------------

# Discovery is a directory walk, resolved at import so the cases exist
# before collection. The catalog in radialrig/skills.py is a separate
# concern with a separate test: that one governs what installs, this one
# governs what the bytes may say. Deliberately not intersected with the
# catalog here - a directory the catalog forgot is exactly the case a
# sweep should still see.


def discover_skills() -> tuple[str, ...]:
    root = packaged_skills()
    if root is None:  # pragma: no cover - a zip-imported installation
        return ()
    return tuple(sorted(
        entry.name for entry in root.iterdir()
        if entry.is_dir() and entry.name != "__pycache__"
        and not entry.name.startswith(".")
    ))


SKILL_NAMES = discover_skills()

# Each case is named for its skill, so `-k <skill-name>` selects that
# skill's checks and a failure report names the offender in its id.
every_skill = pytest.mark.parametrize("skill", SKILL_NAMES)


# --- fixtures ---------------------------------------------------------


@pytest.fixture(scope="module")
def skills_dir() -> Path:
    root = packaged_skills()
    assert root is not None, "this installation ships no skill sources"
    return root


@pytest.fixture(scope="module")
def skill_text(skills_dir: Path) -> str:
    return (skills_dir / SKILL / "SKILL.md").read_bytes().decode("utf-8")


@pytest.fixture(scope="module")
def handoff(skills_dir: Path):
    """scripts/handoff.py, imported from the packaged bytes.

    It is package data, not a module of radialrig: a host runs it with
    whatever Python 3 it has, so it is loaded here the same way - by
    path, with nothing of this package on its import path.
    """
    source = skills_dir / SKILL / SCRIPT_REL
    spec = importlib.util.spec_from_file_location("_packaged_handoff", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Importing must not leave a byte in the packaged tree: the sweeps
    # below walk that tree, and radialrig ships what is in it.
    written = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = written
    return module


def packaged_files(directory: Path) -> list[Path]:
    """Every shipped byte beneath one directory, interpreter caches aside."""
    return sorted(
        p for p in directory.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def loose_files(skills_dir: Path) -> list[Path]:
    """Shipped bytes at the skills root, belonging to no skill.

    The per-skill sweeps below walk skill directories; a file dropped
    beside them would otherwise ship unchecked.
    """
    return sorted(p for p in skills_dir.iterdir() if p.is_file())


def frontmatter(text: str) -> tuple[dict[str, str], dict[str, str], str]:
    """(top-level keys, the radialrig block, the body).

    A hand parser rather than a yaml dependency: the frontmatter this
    rule governs is three keys deep and the test must run wherever the
    package does.
    """
    assert text.startswith("---\n"), "SKILL.md does not open with frontmatter"
    end = text.index("\n---\n", 3)
    block, body = text[4:end + 1], text[end + 5:]
    top: dict[str, str] = {}
    nested: dict[str, str] = {}
    current = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith("  "):
            key, _, value = line.strip().partition(":")
            assert current == "radialrig", f"nested key outside the radialrig block: {key}"
            nested[key.strip()] = value.strip()
            continue
        key, _, value = line.partition(":")
        current = key.strip()
        top[current] = value.strip()
    return top, nested, body


def document(skills_dir: Path, skill: str) -> str:
    return (skills_dir / skill / "SKILL.md").read_bytes().decode("utf-8")


# --- the byte rules, stated once and applied per skill ----------------

# Each returns the offences it found rather than asserting, so one
# implementation serves both the per-skill sweep and the loose files at
# the skills root.


def scrub_offences(paths: list[Path], base: Path,
                   patterns: tuple[tuple[str, str], ...] = BANNED_PATTERNS) -> list[str]:
    """D-37 rule 3, over every byte of the paths given."""
    offences: list[str] = []
    for path in paths:
        text = path.read_bytes().decode("utf-8")
        rel = path.relative_to(base).as_posix()
        for pattern, why in patterns:
            for number, line in enumerate(text.splitlines(), 1):
                if re.search(pattern, line):
                    offences.append(f"{rel} line {number} carries {why}: {line.strip()[:70]}")
    return offences


def non_ascii_offences(paths: list[Path], base: Path) -> list[str]:
    offences: list[str] = []
    for path in paths:
        text = path.read_bytes().decode("utf-8")
        rel = path.relative_to(base).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            if not line.isascii():
                offences.append(f"{rel} line {number} carries non-ASCII bytes")
    return offences


def emoji_offences(paths: list[Path], base: Path) -> list[str]:
    offences: list[str] = []
    for path in paths:
        text = path.read_bytes().decode("utf-8")
        rel = path.relative_to(base).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            for character in line:
                point = ord(character)
                if any(low <= point <= high for low, high in EMOJI_RANGES):
                    offences.append(
                        f"{rel} line {number} carries a pictographic character"
                    )
                    break
    return offences


def substitution_offences(paths: list[Path], base: Path) -> list[str]:
    offences: list[str] = []
    for path in paths:
        text = path.read_bytes().decode("utf-8")
        rel = path.relative_to(base).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            if "{{" in line:
                offences.append(f"{rel} line {number} carries a substitution token")
    return offences


def crlf_offences(paths: list[Path], base: Path) -> list[str]:
    return [
        f"{path.relative_to(base).as_posix()} carries CRLF"
        for path in paths if b"\r" in path.read_bytes()
    ]


def measured_claim_offences(paths: list[Path], base: Path) -> list[str]:
    """D-37 rule 2, over every byte rather than over SKILL.md alone.

    A claim in a script's docstring installs into the host exactly as a
    claim in the instructions does, and is read by the same agent.
    """
    offences: list[str] = []
    for path in paths:
        text = path.read_bytes().decode("utf-8")
        rel = path.relative_to(base).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            for claim in MEASURED_CLAIMS:
                if claim in lowered:
                    offences.append(f"{rel} line {number} asserts '{claim}': {line.strip()[:70]}")
    return offences


BYTE_RULES = (
    scrub_offences,
    non_ascii_offences,
    emoji_offences,
    substitution_offences,
    crlf_offences,
    measured_claim_offences,
)


# --- the sweep is over every packaged skill ---------------------------


def test_the_sweep_covers_every_packaged_skill_directory(skills_dir):
    # The one check the parametrisation cannot make about itself: an
    # empty discovery would collect zero cases and report all-clear on a
    # tree nothing swept.
    on_disk = sorted(
        entry.name for entry in skills_dir.iterdir()
        if entry.is_dir() and entry.name != "__pycache__"
    )
    assert on_disk, "the package ships no skill directory"
    assert list(SKILL_NAMES) == on_disk
    assert SKILL in SKILL_NAMES


def test_no_byte_beside_the_skills_carries_an_offence(skills_dir):
    # README.txt and anything else living at the skills root. Swept by
    # the same rules, since it ships in the same package.
    offences: list[str] = []
    for rule in BYTE_RULES:
        offences.extend(rule(loose_files(skills_dir), skills_dir))
    assert offences == []


# --- the scrub (D-37 rule 3) ------------------------------------------


@every_skill
def test_no_packaged_skill_byte_names_a_place_outside_this_package(skills_dir, skill):
    # The sweep runs over every shipped byte, not just SKILL.md: a path
    # in a script's docstring installs into the host exactly as a path in
    # the instructions does.
    files = packaged_files(skills_dir / skill)
    assert scrub_offences(files, skills_dir) == []


@every_skill
def test_packaged_skill_bytes_are_plain_ascii(skills_dir, skill):
    # ASCII covers the emoji ban and the mangling-on-the-clipboard
    # problem in one property, and it is the only form that survives
    # every host code page unchanged.
    assert non_ascii_offences(packaged_files(skills_dir / skill), skills_dir) == []


@every_skill
def test_packaged_skill_bytes_carry_no_emoji(skills_dir, skill):
    # Stated separately from the ASCII rule because it is a separate
    # requirement: were the ASCII rule ever relaxed, this one holds.
    assert emoji_offences(packaged_files(skills_dir / skill), skills_dir) == []


@every_skill
def test_no_packaged_skill_source_carries_token_substitution(skills_dir, skill):
    # Invariant I-4: packaged and installed bytes hash identically, so a
    # substitution token would install as literal text.
    assert substitution_offences(packaged_files(skills_dir / skill), skills_dir) == []


@every_skill
def test_packaged_skill_sources_are_lf_only(skills_dir, skill):
    assert crlf_offences(packaged_files(skills_dir / skill), skills_dir) == []


@every_skill
def test_no_packaged_skill_byte_makes_a_measured_claim(skills_dir, skill):
    # D-37 rule 2. Conformance may be stated as conformance; a
    # performance property may not be asserted until evals measures it.
    assert measured_claim_offences(packaged_files(skills_dir / skill), skills_dir) == []


# --- the budgets (D-37 rules 1 and 4) ---------------------------------


@every_skill
def test_every_skill_ships_a_skill_document(skills_dir, skill):
    # The one file a harness reads. Without it the directory is not a
    # skill, and the checks below would have nothing to bind.
    assert (skills_dir / skill / "SKILL.md").is_file()


@every_skill
def test_frontmatter_is_closed(skills_dir, skill):
    top, nested, _ = frontmatter(document(skills_dir, skill))
    assert set(top) == EXPECTED_FRONTMATTER_KEYS
    # The name is the directory: a harness reaches the skill by one and
    # the kit installs it by the other, and a mismatch installs a skill
    # nothing can call.
    assert top["name"] == skill
    assert top["description"]
    # D-37 rule 1: the block carries at least id and version. Nothing a
    # source carried beside them survives - a provenance list a host
    # cannot resolve, a reverification date nothing in a host enforces,
    # a tier vocabulary no consumer defines.
    assert set(nested) == {"id", "version"}
    assert nested["id"] == skill
    assert re.fullmatch(r"\d+\.\d+\.\d+", nested["version"])


@every_skill
def test_the_description_stays_inside_the_always_loaded_budget(skills_dir, skill):
    # The description is metadata every host session pays for whether or
    # not the skill fires, which is why the cap is on it and not on the
    # body it summarises.
    top, _, _ = frontmatter(document(skills_dir, skill))
    assert len(top["description"]) <= MAX_DESCRIPTION


@every_skill
def test_the_body_stays_inside_the_triggered_budget(skills_dir, skill):
    text = document(skills_dir, skill)
    _, _, body = frontmatter(text)
    assert len(text.splitlines()) < MAX_LINES
    assert len(body.encode("utf-8")) <= MAX_BODY_BYTES


@every_skill
def test_the_skill_stays_inside_the_pinned_budget(skills_dir, skill):
    # Tighter than D-37's ceilings on purpose: see BUDGET_LINES.
    text = document(skills_dir, skill)
    _, _, body = frontmatter(text)
    assert len(text.splitlines()) <= BUDGET_LINES
    assert len(body.encode("utf-8")) <= BUDGET_BODY_BYTES


# --- the mediated skills: version pins --------------------------------

# The same oracle as the template list above, applied to a version. A
# skill states its version twice - once in the frontmatter a harness
# reads, once in SKILL_CATALOG, which is what an install writes into the
# host's record - and neither statement is derived from the other, so
# left unpinned they drift and a host is told it holds a version it does
# not. Both sides are therefore compared against a literal restated
# here: agreeing with each other is not enough, because a wrong bump
# applied to both would agree with itself.
#
# session-handoff keeps its own named pin below; these are the three
# skills mediated in this release, each carrying the major bump its
# rewrite earned.
PINNED_VERSIONS: tuple[tuple[str, str], ...] = (
    ("plan-review", "2.1.0"),
    ("promotion-gate", "3.1.0"),
    ("toolkit-standards", "3.2.0"),
    ("memory-keeper", "1.0.0"),
)


@pytest.mark.parametrize(("skill", "version"), PINNED_VERSIONS)
def test_the_mediated_skill_ships_the_version_the_catalog_declares(
    skills_dir, skill, version,
):
    # Imported here and nowhere above: the sweep's discovery deliberately
    # does not consult the catalog, so that a directory the catalog
    # forgot is still swept. This check is the other direction - what the
    # catalog claims about a skill it does ship.
    from radialrig.skills import catalog_entry

    _, nested, _ = frontmatter(document(skills_dir, skill))
    assert nested["version"] == version
    entry = catalog_entry(skill)
    assert entry is not None, f"{skill} ships bytes the catalog does not declare"
    assert entry.version == version


# --- promotion-gate: the enable-only posture --------------------------

# The one substantive decision carried through the rewrite: the skill
# documents turning host branch protection back ON and never documents
# turning it off. A shipped byte is read by an agent on a host nobody is
# watching, and a disable command sitting in an instruction file is a
# ready-made way around a refusal - which is exactly the move the skill
# spends its Rules section forbidding. Stated as a pair, because either
# half alone passes trivially: the enabling direction must be present,
# and no disabling form may appear anywhere in the skill's bytes.
PROMOTION_SKILL = "promotion-gate"
PROMOTION_ENABLES = "enforcement=active"
PROMOTION_DISABLES = (
    "enforcement=disabled",
    "enforcement=evaluate",
    "enforcement=inactive",
    "--method DELETE",
    "-X DELETE",
    "allow_force_pushes",
)


def test_the_gate_skill_documents_re_enabling_host_protection(skills_dir):
    body = flowed(document(skills_dir, PROMOTION_SKILL))
    assert "documents the enabling direction only" in body
    assert PROMOTION_ENABLES in body
    assert "No disabling command appears here" in body


def test_no_gate_skill_byte_documents_disabling_host_protection(skills_dir):
    offences: list[str] = []
    for path in packaged_files(skills_dir / PROMOTION_SKILL):
        text = path.read_bytes().decode("utf-8")
        rel = path.relative_to(skills_dir).as_posix()
        lowered = text.lower()
        for form in PROMOTION_DISABLES:
            if form.lower() in lowered:
                offences.append(f"{rel} carries a disabling form: {form}")
        # Every host-API call the skill shows must be the enable
        # direction. Pinning the verbs rather than the wording catches a
        # future example that changes the method without changing the
        # prose around it.
        for verb in re.findall(r"--method\s+(\w+)", text):
            if verb != "PUT":
                offences.append(f"{rel} calls the host API with --method {verb}")
        for line in text.splitlines():
            if "gh api" in line and PROMOTION_ENABLES not in line:
                offences.append(f"{rel} shows a host-API call that is not the "
                                f"enable direction: {line.strip()[:70]}")
    assert offences == []


# --- session-handoff: the version and the footer ----------------------


def test_the_rewrite_ships_as_version_three(skill_text):
    # D-37: a rewrite-class change bumps the major, and this rewrite
    # replaces the source's 2.x text section for section. The minor
    # moved when the kickoff prompt's bytes moved into the script; the
    # patch moved when the footer literal caught up to the frontmatter,
    # and again when the retention docstring was scrubbed of an imported
    # comparison claim and the template's frozen wrap and "Re verify"
    # were repaired, and again when every command line started naming
    # the script from the project root. The minor moved again when the
    # consent table named its precedence and the done-condition became
    # the script's own check, and again when the standing constraints
    # gained a source per line, the kickoff prompt sent the reader back
    # to those sources, and the word count became information.
    _, nested, _ = frontmatter(skill_text)
    assert nested["version"] == "3.3.0"


def test_the_footer_template_names_the_version_the_frontmatter_ships(skill_text):
    # The provenance footer the skill dictates carries a version literal,
    # and 3.1.0 shipped with that literal still reading 3.0.0: every
    # by-the-book handoff mis-reported its own provenance. Pinning the
    # literal to the frontmatter closes that drift for good.
    _, nested, body = frontmatter(skill_text)
    stated = re.findall(r"Skill: session-handoff (\d+\.\d+\.\d+)", body)
    assert stated, "the footer template no longer names the skill version"
    assert stated == [nested["version"]]


def test_the_description_names_the_triggers_and_the_deliverables(skill_text):
    # Findability is the description's whole job: a skill nobody reaches
    # has no behaviour to conform to.
    top, _, _ = frontmatter(skill_text)
    description = top["description"].lower()
    assert "hand off" in description
    assert "kickoff prompt" in description


# --- session-handoff: the rewrite's structural rules ------------------


def parse_template(text: str) -> list[str]:
    """The H2 headings inside SKILL.md's output template block."""
    marker = "## Output template"
    start = text.index(marker)
    fence = text.index("```", start)
    close = text.index("```", fence + 3)
    block = text[fence + 3:close]
    return [line[3:].strip() for line in block.splitlines() if line.startswith("## ")]


def test_the_template_and_the_script_state_one_list(skill_text, handoff):
    # The anti-drift oracle. Both sides are compared against a literal
    # restated in this file, so agreeing with each other is not enough.
    assert parse_template(skill_text) == list(EXPECTED_SECTIONS)
    assert tuple(handoff.REQUIRED_SECTIONS) == EXPECTED_SECTIONS
    assert parse_template(skill_text) == list(handoff.REQUIRED_SECTIONS)


def test_the_kickoff_prompt_survives_the_clipboard(handoff):
    # A correctness control, never a security one: the prompt crosses the
    # clipboard, where non-ASCII mangles on Windows OEM code pages and a
    # hyphen standing in for a dash reads worse than a period. The rule
    # here is the script's own rule, no stricter: dash punctuation is
    # banned, a hyphen inside a word (Re-verify) or inside the
    # substituted path is prose and stays. A stricter ban once forced
    # "Re verify" into the template and shipped as a mangled paste.
    block = handoff.KICKOFF_TEMPLATE
    assert block.isascii()
    for character in ("\u2013", "\u2014"):
        assert character not in block
    assert handoff.DASH_PUNCTUATION.search(block) is None


def test_the_kickoff_prompt_carries_no_frozen_wrap(handoff):
    # Every newline in the template ships in every paste; a line that
    # stops mid-sentence is a fixed-width wrap frozen into the prompt,
    # which is the second half of the same mangled paste. One paragraph,
    # one physical line.
    for line in handoff.KICKOFF_TEMPLATE.splitlines():
        assert line == "" or line.endswith((".", "!", "?"))


def test_the_kickoff_prompt_carries_the_resume_protocol(handoff):
    # Read the notes, run the project's tests to catch an undocumented
    # regression, then choose work - in that order. The middle step is
    # the one the source omitted.
    block = flowed(handoff.KICKOFF_TEMPLATE)
    for phrase in ("Standing constraints", "Running state", "Verification",
                   "basic tests", "Pick up here"):
        assert phrase in block


def test_the_kickoff_template_lives_only_in_the_script(skill_text):
    # One home for the bytes. When the template also sat in SKILL.md,
    # each agent retyped it from prose and the user was handed a
    # differently wrapped prompt every session.
    assert "Fresh session pickup" not in skill_text
    assert "handoff.py kickoff" in flowed(skill_text)


def flowed(text: str) -> str:
    """The text with line wrapping removed, for phrase assertions.

    The shipped file is wrapped for reading; a rule that happens to
    straddle a line break is the same rule, and a test that failed on
    the rewrap would be pinning the margin rather than the behaviour.
    """
    return " ".join(text.split())


def test_the_skill_offers_the_handoff_and_never_fires_it(skill_text):
    # D-37 rule 5, in the shipped text: the human drives the handoff.
    body = flowed(skill_text).lower()
    assert "offer this once" in body
    assert "never run it unasked" in body
    assert "never read your own transcript" in body
    assert "never measure context" in body


def test_the_skill_states_all_three_consent_tiers(skill_text):
    # D-37 rule 6. Stated by action class rather than by section, which
    # is what closes the gap the source left around the ignore line.
    body = flowed(skill_text)
    assert "Consent runs in three tiers" in body
    assert "Ask first" in body
    assert "deleting a handoff; killing anything this session did not start" in body


def test_the_skill_states_retention_as_a_move(skill_text):
    # D-37 rule 7: nothing in the handoff directory is ever deleted.
    body = flowed(skill_text)
    assert "Nothing in that directory is ever deleted" in body
    assert "handoffs/archive/" in body


def test_the_skill_states_the_ignore_rule_honestly(skill_text):
    # D-37 rule 8: where the host tracks the directory, the control is
    # reported unenforced rather than written and hoped for.
    assert "reports the control as unenforced" in flowed(skill_text)


# --- scripts/handoff.py -----------------------------------------------


SECTION_BODY = "".join(f"## {name}\nnone\n\n" for name in EXPECTED_SECTIONS)
HANDOFF_TEXT = (
    "# Session Handoff - a sample\n"
    "The user asked for a sample.\n\n"
    + SECTION_BODY
    + "---\nSource: this session, 2026-08-18T09:00. Review status: 0 of 0 entries "
    "verified; nothing here was read by a second reader. Confidence: low - a "
    "sample. Skill: session-handoff 3.0.0.\n"
)


def write_handoff(root: Path, name: str = "HANDOFF_08182026_0900.md", text: str = HANDOFF_TEXT) -> Path:
    directory = root / "handoffs"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def call(handoff, capsys, *argv: str) -> tuple[int, str]:
    code = handoff.main(list(argv))
    return code, capsys.readouterr().out


def test_stamp_prints_the_directory_and_the_name(handoff, capsys, tmp_path):
    code, out = call(handoff, capsys, "stamp", str(tmp_path))
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == f"dir {tmp_path / 'handoffs'}"
    assert re.fullmatch(r"name HANDOFF_\d{8}_\d{4}\.md", lines[1])
    assert (tmp_path / "handoffs").is_dir()


def test_file_prints_four_lines_and_passes_a_conformant_handoff(handoff, capsys, tmp_path):
    target = write_handoff(tmp_path)
    code, out = call(handoff, capsys, "file", str(target))
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("retention: ")
    assert lines[1].startswith("ignore rule: ")
    assert lines[2].startswith("words: ")
    assert lines[3] == "check: pass"


def test_the_word_count_is_information_and_never_a_failure(handoff, capsys, tmp_path):
    # The documentation-bloat guidance the skill conforms to says a fixed
    # length cap in a standing instruction costs quality, so the script
    # reports the count and leaves the cut to the agent's judgement.
    long = HANDOFF_TEXT.replace("## Key files\nnone\n", "## Key files\n" + "word " * 4000 + "\n")
    target = write_handoff(tmp_path, text=long)
    code, out = call(handoff, capsys, "check", str(target))
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith("words: ")
    count = int(lines[0].removeprefix("words: ").split(";", 1)[0])
    assert count >= 4000
    assert lines[-1] == "check: pass"


def test_retention_moves_the_surplus_and_deletes_nothing(handoff, capsys, tmp_path):
    directory = tmp_path / "handoffs"
    written = []
    for minute in range(7):
        target = write_handoff(tmp_path, f"HANDOFF_08182026_09{minute:02d}.md")
        stamp = time.time() + minute
        os.utime(target, (stamp, stamp))
        written.append(target.name)
    newest = directory / written[-1]

    code, out = call(handoff, capsys, "file", str(newest))
    assert code == 0
    assert "moved 2 to handoffs/archive/" in out
    assert "nothing deleted" in out

    kept = sorted(p.name for p in directory.glob("HANDOFF_*.md"))
    archived = sorted(p.name for p in (directory / "archive").glob("HANDOFF_*.md"))
    assert len(kept) == 5
    # The two oldest moved, and every file written is still on disk.
    assert archived == written[:2]
    assert sorted(kept + archived) == sorted(written)


def test_retention_orders_by_modification_time_not_by_the_filename_stamp(handoff, capsys, tmp_path):
    # The stamps do not sort chronologically across a year boundary, so
    # a filename sort would archive the newest file.
    directory = tmp_path / "handoffs"
    names = [f"HANDOFF_1201202{year}_0900.md" for year in range(5)]
    names.append("HANDOFF_01022026_0900.md")   # newest, sorts first by name
    for index, name in enumerate(names):
        target = write_handoff(tmp_path, name)
        stamp = time.time() + index
        os.utime(target, (stamp, stamp))
    newest = directory / names[-1]

    call(handoff, capsys, "file", str(newest))
    assert (directory / names[-1]).is_file()
    assert sorted(p.name for p in (directory / "archive").glob("*.md")) == [names[0]]


def test_file_refuses_a_target_outside_a_handoffs_directory(handoff, capsys, tmp_path):
    stray = tmp_path / "notes" / "HANDOFF_08182026_0900.md"
    stray.parent.mkdir(parents=True)
    stray.write_text(HANDOFF_TEXT, encoding="utf-8")
    code, out = call(handoff, capsys, "file", str(stray))
    assert code == 2
    assert out.startswith("refused: ")
    assert stray.is_file()


def test_file_refuses_a_name_outside_the_handoff_pattern(handoff, capsys, tmp_path):
    directory = tmp_path / "handoffs"
    directory.mkdir()
    stray = directory / "notes.md"
    stray.write_text("keep me", encoding="utf-8")
    code, out = call(handoff, capsys, "file", str(stray))
    assert code == 2
    assert stray.read_text(encoding="utf-8") == "keep me"


def test_file_refuses_a_relative_path(handoff, capsys, tmp_path):
    code, out = call(handoff, capsys, "check", "handoffs/HANDOFF_08182026_0900.md")
    assert code == 2
    assert "absolute" in out


def test_check_fails_on_a_missing_section(handoff, capsys, tmp_path):
    broken = HANDOFF_TEXT.replace("## Still broken\nnone\n\n", "")
    target = write_handoff(tmp_path, text=broken)
    code, out = call(handoff, capsys, "check", str(target))
    assert code == 1
    assert "missing section: Still broken" in out


def test_check_fails_on_an_empty_section(handoff, capsys, tmp_path):
    hollow = HANDOFF_TEXT.replace("## Running state\nnone\n", "## Running state\n")
    target = write_handoff(tmp_path, text=hollow)
    code, out = call(handoff, capsys, "check", str(target))
    assert code == 1
    assert "empty section: Running state" in out


def test_check_fails_when_the_kickoff_prompt_leaked_into_the_file(handoff, capsys, tmp_path):
    leaked = HANDOFF_TEXT + "\nFresh session pickup. Read the file first.\n"
    target = write_handoff(tmp_path, text=leaked)
    code, out = call(handoff, capsys, "check", str(target))
    assert code == 1
    assert "belongs in chat" in out


def test_check_fails_without_a_provenance_footer(handoff, capsys, tmp_path):
    bare = HANDOFF_TEXT[:HANDOFF_TEXT.index("---\nSource:")]
    target = write_handoff(tmp_path, text=bare)
    code, out = call(handoff, capsys, "check", str(target))
    assert code == 1
    assert "no provenance footer" in out


# --- the ignore rule, in all three git states -------------------------


def git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=str(root), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not on this machine")
    root = tmp_path / "host"
    root.mkdir()
    git(root, "init", "-q", ".")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    return root


def test_the_ignore_line_is_skipped_where_there_is_no_repository(handoff, capsys, tmp_path):
    # tmp_path is not a work tree, and the walk upward finds no .git.
    target = write_handoff(tmp_path)
    code, out = call(handoff, capsys, "file", str(target))
    assert code == 0
    assert "not a git repository" in out
    assert not (tmp_path / ".gitignore").exists()


def test_the_ignore_line_is_added_where_the_directory_is_untracked(handoff, capsys, repo):
    target = write_handoff(repo)
    code, out = call(handoff, capsys, "file", str(target))
    assert code == 0
    assert "added handoffs/" in out
    assert "handoffs/" in (repo / ".gitignore").read_text(encoding="utf-8")

    # Idempotent: a second run reports the line rather than repeating it.
    code, out = call(handoff, capsys, "file", str(target))
    assert code == 0
    assert "already ignored" in out
    assert (repo / ".gitignore").read_text(encoding="utf-8").count("handoffs/") == 1


def test_the_ignore_line_is_withheld_where_the_directory_is_tracked(handoff, capsys, repo):
    # D-37 rule 8. Writing a line git would ignore would look like the
    # control was applied; this repository itself is the tracked case.
    target = write_handoff(repo)
    git(repo, "add", "-f", "handoffs/HANDOFF_08182026_0900.md")
    code, out = call(handoff, capsys, "file", str(target))
    assert code == 0
    assert "is tracked in this project; left .gitignore alone" in out
    assert not (repo / ".gitignore").exists()


# --- the clipboard ----------------------------------------------------


def test_copy_reports_honestly_when_no_clipboard_tool_exists(handoff, capsys, tmp_path, monkeypatch):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Fresh session pickup. Read the file first.\n", encoding="utf-8")
    monkeypatch.setattr(handoff.shutil, "which", lambda name: None)
    code, out = call(handoff, capsys, "copy", str(prompt))
    assert code == 1
    assert out.startswith("not copied: ")
    assert "no clipboard tool" in out


def test_copy_refuses_a_prompt_that_would_not_survive_the_clipboard(handoff, capsys, tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Fresh session pickup - read the file.\n", encoding="utf-8")
    code, out = call(handoff, capsys, "copy", str(prompt))
    assert code == 1
    assert "no dash punctuation" in out

    prompt.write_text("Fresh session pickup \u2014 read the file.\\n", encoding="utf-8")
    code, out = call(handoff, capsys, "copy", str(prompt))
    assert code == 1
    assert "ASCII" in out or "dash" in out


def test_copy_accepts_a_prompt_whose_absolute_path_carries_hyphens(handoff, capsys, tmp_path, monkeypatch):
    # The other half of the rule above, and the one that keeps the skill
    # satisfiable. The prompt must name the handoff by absolute path, and
    # a host path may be hyphenated, as many are. A ban on the character
    # rather than on the punctuation would make the copy step, and with it
    # the Done-when, unreachable on such a host.
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Fresh session pickup. Read /home/dev/sample-project/handoffs/"
        "HANDOFF_08182026_0900.md first. Re-verify the running state.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(handoff.shutil, "which", lambda name: "/usr/bin/" + name)

    class Copied:
        returncode = 0

    # The clipboard itself is not the subject here: the tool is stubbed so
    # the test pins the guard, not the machine it happens to run on.
    monkeypatch.setattr(handoff.subprocess, "run", lambda *a, **k: Copied())
    code, out = call(handoff, capsys, "copy", str(prompt))
    assert code == 0, out
    assert out.startswith("copied to the clipboard with ")


def test_kickoff_prints_the_same_bytes_every_run(handoff, capsys, tmp_path, monkeypatch):
    # The complaint this subcommand answers: agents rewrap the prompt
    # differently from session to session. The bytes after the status
    # line are the template with one substitution, identical on every
    # run, and nothing an agent judges.
    target = write_handoff(tmp_path)
    monkeypatch.setattr(handoff.shutil, "which", lambda name: "/usr/bin/" + name)

    class Copied:
        returncode = 0

    monkeypatch.setattr(handoff.subprocess, "run", lambda *a, **k: Copied())
    first_code, first_out = call(handoff, capsys, "kickoff", str(target))
    second_code, second_out = call(handoff, capsys, "kickoff", str(target))
    assert (first_code, first_out) == (second_code, second_out)
    assert first_code == 0
    status, prompt = first_out.split("\n\n", 1)
    assert status.startswith("copied to the clipboard with ")
    assert prompt == handoff.KICKOFF_TEMPLATE.format(path=target)
    assert prompt.startswith("Fresh session pickup. Read ")
    assert str(target) in prompt


def test_kickoff_prints_the_prompt_even_when_the_copy_fails(handoff, capsys, tmp_path, monkeypatch):
    # A failed copy is reported, never hidden - and the prompt still
    # prints, because it is the only prompt there is.
    target = write_handoff(tmp_path)
    monkeypatch.setattr(handoff.shutil, "which", lambda name: None)
    code, out = call(handoff, capsys, "kickoff", str(target))
    assert code == 1
    status, prompt = out.split("\n\n", 1)
    assert status.startswith("not copied: ")
    assert prompt == handoff.KICKOFF_TEMPLATE.format(path=target)


def test_kickoff_refuses_a_path_outside_a_handoffs_directory(handoff, capsys, tmp_path):
    stray = tmp_path / "notes.md"
    stray.write_text("not a handoff\n", encoding="utf-8")
    code, out = call(handoff, capsys, "kickoff", str(stray))
    assert code == 2
    assert out.startswith("refused: ")
    assert "Fresh session pickup" not in out


def test_kickoff_leaves_no_temporary_file_behind(handoff, capsys, tmp_path, monkeypatch):
    target = write_handoff(tmp_path)
    monkeypatch.setattr(handoff.shutil, "which", lambda name: "/usr/bin/" + name)

    class Copied:
        returncode = 0

    monkeypatch.setattr(handoff.subprocess, "run", lambda *a, **k: Copied())
    call(handoff, capsys, "kickoff", str(target))
    leftovers = [p.name for p in target.parent.iterdir() if p.name != target.name]
    assert leftovers == []


def test_copy_never_claims_a_copy_that_failed(handoff, capsys, tmp_path, monkeypatch):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Fresh session pickup. Read the file first.\n", encoding="utf-8")
    monkeypatch.setattr(handoff.shutil, "which", lambda name: "/usr/bin/" + name)

    class Failed:
        returncode = 1

    monkeypatch.setattr(handoff.subprocess, "run", lambda *a, **k: Failed())
    code, out = call(handoff, capsys, "copy", str(prompt))
    assert code == 1
    assert "not copied" in out
    assert "copied to the clipboard" not in out


# --- the command surface ----------------------------------------------


def test_bare_invocation_prints_usage(handoff, capsys):
    code, out = call(handoff, capsys)
    assert code == 0
    assert out.startswith("usage:")


def test_the_script_runs_under_the_host_python(skills_dir):
    # A host runs this with its own interpreter and nothing of radialrig
    # on the path; the subprocess proves that holds.
    source = skills_dir / SKILL / SCRIPT_REL
    done = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0
    assert done.stdout.startswith("usage:")
