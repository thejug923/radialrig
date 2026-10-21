"""Tests for the package layout PLAN-0005 Phase 1 lays down.

Run:  uv run --with pytest pytest src/radialrig/tests/test_packaging.py -q

Five properties: the packaged guidance copies do not drift from the live
files that hold authority (D-32, D-07), get_guidance still answers where
no research tree exists, the answer it gives there carries no evidence
pointer the host cannot resolve (PLAN-0017 phase 6, packaged-data-01),
the five entry points are declared, and — since PLAN-0006 Phase 4 — the
packaged skill sources and the catalog that governs an install agree in
both directions.
"""

from __future__ import annotations

import hashlib
import json
import re
from importlib.metadata import entry_points

import pytest

from radialrig import doc_core
from radialrig import generate_research_index as gri
from radialrig import retrieval_server as rs
from radialrig import skills as sk
from radialrig.doc_core import load_guidance
from radialrig.paths import packaged_research, packaged_skills, resolve_root
from radialrig.tests.test_retrieval_server import guidance_text, write

EXPECTED_SCRIPTS = {
    "radialrig": "radialrig.cli:main",
    "radialrig-read": "radialrig.retrieval_server:main",
    "radialrig-write": "radialrig.authoring_server:main",
    "radialrig-validate": "radialrig.validate_docs:main",
    "radialrig-index": "radialrig.generate_research_index:main",
}

# The skill sources this package ships, restated as a literal so a wrong
# SKILL_CATALOG entry cannot agree with itself. README.txt is package
# documentation, not a skill file, and is excluded on both sides.
EXPECTED_SKILL_SOURCES = {
    "memory-keeper/SKILL.md",
    "memory-keeper/scripts/audit.py",
    "plan-review/SKILL.md",
    "promotion-gate/SKILL.md",
    "session-handoff/SKILL.md",
    "session-handoff/scripts/handoff.py",
    "toolkit-standards/SKILL.md",
    "toolkit-standards/reference/languages.md",
}
SKILLS_README = "README.txt"


def test_packaged_copies_match_the_live_files():
    # The drift check binds exactly where the live files have authority.
    # A host install carries no research tree, so there is nothing to
    # drift from and the check does not apply there.
    root = resolve_root()
    live = root / "research"
    if not live.is_dir():
        pytest.skip("no live research tree at the corpus root")
    packaged = packaged_research()
    assert packaged is not None, "the package ships no guidance corpus"

    manifest = json.loads((packaged / "MANIFEST.json").read_text(encoding="utf-8"))
    recorded = {entry["path"]: entry["sha256"] for entry in manifest["files"]}

    sources = sorted(live.rglob("guidance.md"))
    assert sources, "the live research tree holds no guidance files"
    for source in sources:
        rel = source.relative_to(live).as_posix()
        copy = packaged / rel
        assert copy.is_file(), f"research/{rel} has no packaged copy"
        data = source.read_bytes()
        assert copy.read_bytes() == data, f"packaged copy of research/{rel} has drifted"
        assert recorded.get(f"research/{rel}") == hashlib.sha256(data).hexdigest()

    shipped = {p.relative_to(packaged).as_posix() for p in packaged.rglob("guidance.md")}
    assert shipped == {p.relative_to(live).as_posix() for p in sources}


def test_guidance_falls_back_to_the_packaged_corpus(tmp_path):
    # Prescription 4: a host with no research tree is answered from the
    # copies shipped inside the installation, never with silence.
    entries, refused = load_guidance(tmp_path)
    assert refused == []
    assert sorted(g.topic for g in entries) == [
        "anti-hobbling", "app-security", "blocked-tasks", "context-rot",
        "documentation-bloat", "expectation", "memory-management",
        "untrusted-content", "visualizations",
    ]
    # The reported path stays a repo-relative pointer in both modes, so
    # the SPEC-2 result shape does not fork between mounts.
    assert all(g.rel.startswith("research/") for g in entries)


def test_a_live_tree_outranks_the_packaged_corpus(tmp_path):
    write(tmp_path, "research/agent-focus/context-rot/guidance.md", guidance_text())
    entries, refused = load_guidance(tmp_path)
    assert refused == []
    assert [g.topic for g in entries] == ["context-rot"]


def test_the_packaged_fallback_serves_no_evidence_pointer_it_cannot_resolve(
    tmp_path, monkeypatch,
):
    # packaged-data-01: the installation ships guidance.md alone, so a
    # host with no research tree used to be handed `evidence` paths that
    # exist nowhere on it. SPEC-2 section 5 makes the pointer one to
    # read, which no one can do from a path that does not resolve; the
    # fallback serves "" there now, and get_guidance passes it through.
    packaged = packaged_research()
    assert packaged is not None, "the package ships no guidance corpus"

    # The premise, pinned: what ships is guidance.md and the manifest,
    # and sync_package_data keeps it that way even when the live tree
    # holds the evidence file beside the guidance.
    shipped = {p.name for p in packaged.rglob("*") if p.is_file()}
    assert shipped == {"guidance.md", "MANIFEST.json"}
    live = tmp_path / "live"
    write(live, "research/agent-focus/context-rot/guidance.md", guidance_text())
    write(live, "research/agent-focus/context-rot/context-rot.md", "# Evidence\n")
    package_dir = tmp_path / "pkg"
    assert gri.sync_package_data(live, package_dir) == [
        "research/agent-focus/context-rot/guidance.md",
    ]
    target = package_dir / "data" / "research"
    copied = {p.relative_to(target).as_posix()
              for p in target.rglob("*") if p.is_file()}
    assert copied == {"agent-focus/context-rot/guidance.md", "MANIFEST.json"}

    # The finding's scenario: a mounted host, no research tree, the
    # packaged corpus answering.
    host = tmp_path / "host"
    host.mkdir()
    entries, refused = load_guidance(host)
    assert refused == []
    assert entries, "the packaged corpus answered nothing"
    assert [g.topic for g in entries if g.evidence] == []
    assert all(g.rel.startswith("research/") for g in entries)

    monkeypatch.setattr(rs, "ROOT", host)
    out = rs.get_guidance("context-rot")
    assert out["kind"] == "guidance"
    assert out["evidence"] == ""
    assert out["path"] == "research/agent-focus/context-rot/guidance.md"


def test_a_shipped_evidence_file_keeps_its_pointer_in_fallback(
    tmp_path, monkeypatch,
):
    # The rule is about the file, not the mode: a packaged corpus that
    # does carry an evidence file serves its pointer, and only the
    # pointer no file stands behind is served as "". A live tree is
    # outside the rule: there the pointer is served as written, present
    # or not, since the plan binds the fallback alone.
    corpus = tmp_path / "pkg" / "data" / "research"
    write(corpus, "agent-focus/context-rot/guidance.md", guidance_text())
    write(corpus, "agent-focus/context-rot/context-rot.md", "# Evidence\n")
    write(corpus, "human-focus/expectation/guidance.md",
          guidance_text("expectation", "human-focus", "Scoping a task.",
                        "./expectation.md"))
    monkeypatch.setattr(doc_core, "packaged_research", lambda: corpus)

    host = tmp_path / "host"
    host.mkdir()
    entries, refused = load_guidance(host)
    assert refused == []
    assert {g.topic: g.evidence for g in entries} == {
        "context-rot": "research/agent-focus/context-rot/context-rot.md",
        "expectation": "",
    }

    live = tmp_path / "live"
    write(live, "research/human-focus/expectation/guidance.md",
          guidance_text("expectation", "human-focus", "Scoping a task.",
                        "./expectation.md"))
    entries, _ = load_guidance(live)
    assert [g.evidence for g in entries] == [
        "research/human-focus/expectation/expectation.md",
    ]


def test_entry_points_are_declared():
    scripts = entry_points(group="console_scripts")
    found = {ep.name: ep.value for ep in scripts if ep.name in EXPECTED_SCRIPTS}
    if not found:
        pytest.skip("radialrig is not installed as a distribution")
    assert found == EXPECTED_SCRIPTS
    # A skill is a subcommand of `radialrig`, not a sixth console script.
    assert not any(ep.name.startswith("radialrig-skill") for ep in scripts)


# --- the packaged skill sources (PLAN-0006 Phase 4) -------------------


def packaged_skill_dir():
    packaged = packaged_skills()
    assert packaged is not None, "the package ships no skill sources"
    return packaged


def shipped_files(packaged):
    """Every byte the package ships under data/skills, caches aside."""
    return sorted(
        p for p in packaged.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def skill_frontmatter(path):
    """The frontmatter of a packaged SKILL.md, as a flat mapping.

    Read here rather than imported from the catalog: the point of the
    check is that the two independent statements of a skill's name and
    version agree, so neither may be derived from the other.
    """
    text = path.read_bytes().decode("utf-8")
    assert text.startswith("---\n")
    block = text[4:text.index("\n---\n", 3) + 1]
    fields = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_the_packaged_skill_tree_and_the_catalog_agree_both_ways():
    # The same oracle the mount uses for SEED_FILES: an explicit list
    # governs, and a stray file dropped into the data directory is a
    # failure rather than a silent addition to what hosts receive.
    packaged = packaged_skill_dir()
    on_disk = {
        p.relative_to(packaged).as_posix()
        for p in shipped_files(packaged)
        if p.name != SKILLS_README
    }
    declared = {
        f"{entry.name}/{rel}"
        for entry in sk.SKILL_CATALOG
        for rel in entry.files
    }
    assert on_disk == EXPECTED_SKILL_SOURCES
    assert declared == EXPECTED_SKILL_SOURCES
    assert (packaged / SKILLS_README).is_file()


def test_every_declared_skill_source_is_a_file():
    packaged = packaged_skill_dir()
    for entry in sk.SKILL_CATALOG:
        for rel in entry.files:
            source = packaged / entry.name / rel
            assert source.is_file(), f"{entry.name}/{rel} is declared but not shipped"
            assert source.read_bytes(), f"{entry.name}/{rel} ships empty"


def test_the_catalog_agrees_with_each_skill_frontmatter():
    # Three statements of one identity — the catalog entry, the directory
    # name, and the frontmatter — pinned equal so a version bump made in
    # one place fails rather than ships half-applied.
    packaged = packaged_skill_dir()
    for entry in sk.SKILL_CATALOG:
        directory = packaged / entry.name
        assert directory.is_dir()
        fields = skill_frontmatter(directory / "SKILL.md")
        assert fields["name"] == entry.name == directory.name
        assert re.fullmatch(r"\d+\.\d+\.\d+", entry.version)
        body = (directory / "SKILL.md").read_bytes().decode("utf-8")
        assert re.search(rf"^  version: {re.escape(entry.version)}$", body, re.M)


def test_the_catalog_summaries_fit_a_report_line():
    # `skill list` renders these through the shared block renderer, so a
    # long or non-ASCII summary would break the column the docs kit's
    # rows already occupy.
    for entry in sk.SKILL_CATALOG:
        assert entry.summary.isascii()
        assert 0 < len(entry.summary) <= 80
        assert "\n" not in entry.summary


def test_no_packaged_skill_source_carries_token_substitution():
    # Invariant I-4: sha256(packaged) == sha256(installed) in every host,
    # which a substitution token would break by installing as literal
    # text. The docs kit's seed is the surface that substitutes; this one
    # deliberately is not.
    for path in shipped_files(packaged_skill_dir()):
        assert "{{" not in path.read_bytes().decode("utf-8"), path.name
