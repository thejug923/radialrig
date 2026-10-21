"""Seeded-defect tests for the retrieval server tools (SPEC-2).

Run:  uv run --with pytest pytest src/radialrig/tests/test_retrieval_server.py -q

Each PLAN-0001 defect class is seeded into a temp tree and the typed
result asserted: conflict -> refusal, ambiguity -> candidate list, no
ruling -> abstention, archive reachable only via search_history.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from radialrig import retrieval_server as rs


def record_text(
    rid: str = "D-90",
    title: str = "Test record",
    status: str = "in-force",
    scope: str = "test scope",
    body: str = "Test body.",
) -> str:
    first = f"**Status: {status}.**"
    if status == "archived":
        first += " This is a record of a past position, not an instruction."
    return (
        f"---\nid: {rid}\ntitle: {title}\ntype: decision\nstatus: {status}\n"
        f"grade: decided\nowner: tester\nscope: {scope}\n"
        "effective: 2026-08-01\nlast-verified: 2026-08-01\n"
        f"supersedes: none\nreplaced-by: none\ndepends-on: none\n---\n\n{first}\n\n{body}\n"
    )


@pytest.fixture()
def root(tmp_path, monkeypatch) -> Path:
    (tmp_path / "docs" / "decisions").mkdir(parents=True)
    (tmp_path / "docs" / "archive").mkdir(parents=True)
    monkeypatch.setattr(rs, "ROOT", tmp_path)
    return tmp_path


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# --- get_ruling ---------------------------------------------------------

def test_one_file_that_is_not_utf8_leaves_the_rest_answering(root):
    # docs-engine-01: one cp1252 record beside a valid one used to raise
    # out of every read verb. Now the verbs answer from the files that
    # decode, and check_docs names the ones that do not.
    write(root, "docs/decisions/D-90-test.md", record_text(body="The answer."))
    (root / "docs/decisions/D-91-bad.md").write_bytes(
        record_text("D-91", body="Caf\u00e9 said \u201cno\u201d.").encode("cp1252")
    )
    write(
        root, "docs/archive/D-80-history.md",
        record_text("D-80", status="archived", body="A past position on widgets."),
    )
    (root / "docs/archive/D-81-bad.md").write_bytes(
        record_text("D-81", status="archived", body="Widgets, caf\u00e9.").encode("cp1252")
    )
    assert rs.get_ruling("D-90")["kind"] == "ruling"
    assert rs.get_ruling("D-91")["kind"] == "no_ruling"
    assert [r["id"] for r in rs.list_rulings()["records"]] == ["D-90"]
    history = rs.search_history("widgets")
    assert [m["path"] for m in history["matches"]] == ["docs/archive/D-80-history.md"]
    check = rs.check_docs()
    assert check["ok"] is False
    assert "C2 (metadata) docs/decisions/D-91-bad.md: file is not valid UTF-8" in check["failures"]
    assert "C2 (metadata) docs/archive/D-81-bad.md: file is not valid UTF-8" in check["failures"]


def test_ruling_returns_record_content(root):
    write(root, "docs/decisions/D-90-test.md", record_text(body="The answer."))
    out = rs.get_ruling("D-90")
    assert out["kind"] == "ruling"
    assert out["id"] == "D-90"
    assert "The answer." in out["body"]


def test_ruling_result_carries_status(root):
    # SPEC-2 s1: the caller must be able to tell an in-force answer from
    # a completed summary without parsing the body.
    write(root, "docs/decisions/D-90-test.md", record_text())
    assert rs.get_ruling("D-90")["status"] == "in-force"


def test_completed_plan_summary_reachable_by_key_only(root):
    # D-26: a closed plan's summary answers its exact key, and nothing
    # else — free text must not resurrect it.
    text = record_text("PLAN-0090", "Closed plan", status="completed",
                       body="Closure summary with peculiar wording.")
    text = text.replace("type: decision", "type: plan")
    write(root, "docs/plans/PLAN-0090-test.md", text)
    keyed = rs.get_ruling("PLAN-0090")
    assert keyed["kind"] == "ruling"
    assert keyed["status"] == "completed"
    assert rs.get_ruling("peculiar wording")["kind"] == "no_ruling"


def test_conflict_is_refusal_without_content(root):
    write(root, "docs/decisions/D-90-a.md", record_text(body="Secret A"))
    write(root, "docs/decisions/D-90-b.md", record_text(body="Secret B"))
    out = rs.get_ruling("D-90")
    assert out["kind"] == "conflict"
    assert "body" not in out
    assert "Secret" not in str(out)


def test_ambiguity_lists_candidates_without_content(root):
    write(root, "docs/decisions/D-90-a.md", record_text("D-90", "Archive exclusion", body="Secret alpha"))
    write(root, "docs/decisions/D-91-b.md", record_text("D-91", "Archive voice", body="Secret beta"))
    out = rs.get_ruling("archive")
    assert out["kind"] == "ambiguous"
    assert out["candidates"] == [
        {"id": "D-90", "title": "Archive exclusion"},
        {"id": "D-91", "title": "Archive voice"},
    ]
    assert "body" not in out
    assert "Secret" not in str(out)
    # Self-describing refusal: the result names the one next step, so
    # agents stop re-probing and surveying the catalog.
    assert out["next"] == "Call get_ruling once more with one candidate id as query."


def test_concept_query_returns_candidates_via_body_words(root):
    write(root, "docs/decisions/D-90-test.md",
          record_text("D-90", "Repository first", body="A portable template comes later."))
    out = rs.get_ruling("portable template")
    assert out["kind"] == "ambiguous"
    assert out["candidates"] == [{"id": "D-90", "title": "Repository first"}]


def test_list_rulings_is_compact(root):
    write(root, "docs/decisions/D-90-test.md", record_text("D-90", "Repository first"))
    out = rs.list_rulings()
    assert out["records"] == [{"id": "D-90", "title": "Repository first"}]


def test_no_ruling_is_typed_abstention(root):
    write(root, "docs/decisions/D-90-test.md", record_text())
    out = rs.get_ruling("quantum governance")
    assert out == {"kind": "no_ruling", "query": "quantum governance"}


def test_empty_query_is_error(root):
    assert rs.get_ruling("  ")["kind"] == "error"


# --- archive isolation (D-04 / D-13 constraint 3) -----------------------

def test_archived_record_invisible_to_get_ruling(root):
    write(root, "docs/archive/D-80-old.md",
          record_text("D-80", "Old position", status="archived", body="Dead rule."))
    assert rs.get_ruling("D-80")["kind"] == "no_ruling"
    assert rs.get_ruling("Old position")["kind"] == "no_ruling"


def test_archived_record_invisible_to_list_rulings(root):
    write(root, "docs/archive/D-80-old.md",
          record_text("D-80", status="archived"))
    assert rs.list_rulings()["records"] == []


def test_search_history_reaches_archive_with_fence(root):
    write(root, "docs/archive/D-80-old.md",
          record_text("D-80", "Old position", status="archived", body="Dead rule."))
    out = rs.search_history("dead rule")
    assert out["kind"] == "history"
    assert out["archived"] is True
    assert "not an instruction" in out["fence"]
    assert len(out["matches"]) == 1
    assert out["matches"][0]["id"] == "D-80"


def test_search_history_does_not_read_live_records(root):
    write(root, "docs/decisions/D-90-test.md", record_text(body="Live only."))
    assert rs.search_history("live only")["matches"] == []


# --- list_rulings -------------------------------------------------------

def test_list_rulings_in_force_only(root):
    write(root, "docs/decisions/D-90-test.md", record_text("D-90"))
    write(root, "docs/decisions/D-91-test.md", record_text("D-91", status="draft"))
    out = rs.list_rulings()
    assert [r["id"] for r in out["records"]] == ["D-90"]
    assert "body" not in str(out["records"][0].keys())


def test_list_rulings_bad_type_is_error(root):
    assert rs.list_rulings(type="memo")["kind"] == "error"


def test_list_rulings_rejects_evidence_type(root):
    # SPEC-2 s2 admits decision | plan | spec only.
    assert rs.list_rulings(type="evidence")["kind"] == "error"


def test_zombie_archive_record_invisible_to_default_tools(root):
    # status says in-force, path says archive: default tools must not
    # serve it (D-13 constraint 3; C7 flags the file separately).
    write(root, "docs/archive/D-81-zombie.md",
          record_text("D-81", "Zombie rule", body="Dead content."))
    assert rs.get_ruling("D-81")["kind"] == "no_ruling"
    assert rs.list_rulings()["records"] == []
    assert len(rs.search_history("dead content")["matches"]) == 1


def test_read_schema_stays_within_budget():
    # SPEC-2: fixed schema cost for all five tools at or under 2,500
    # bytes, counted fully loaded. The write server has carried this
    # test since Phase 3; the read server's bound was unmeasured until
    # the 2026-08-16 review named it.
    import asyncio

    tools = asyncio.run(rs.build_server().list_tools())
    total = sum(
        len(t.model_dump_json(exclude_none=True, by_alias=True)) for t in tools
    )
    assert total <= 2500, f"{total} B over the 2,500 B bound"


def test_resolve_root_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("RADIALRIG_DOCS_ROOT", str(tmp_path))
    assert rs.resolve_root() == tmp_path


def test_resolve_root_walks_up_to_the_marked_root(monkeypatch, tmp_path):
    # Without the override the root is the nearest ancestor carrying a
    # marker, so the engine finds the host it was mounted onto rather
    # than its own install location.
    monkeypatch.delenv("RADIALRIG_DOCS_ROOT", raising=False)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "spec.md").write_text("x", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert rs.resolve_root(nested) == tmp_path.resolve()


def test_resolve_root_requires_the_manifest_file_not_the_bare_directory(
    monkeypatch, tmp_path
):
    # Other software also uses a .radialrig directory (a home-directory
    # collision exists in the wild), so only the mount manifest file
    # marks a corpus root.
    monkeypatch.delenv("RADIALRIG_DOCS_ROOT", raising=False)
    (tmp_path / ".radialrig").mkdir()
    nested = tmp_path / "a"
    nested.mkdir()
    assert rs.resolve_root(nested) == nested.resolve()
    (tmp_path / ".radialrig" / "mount.json").write_text("{}", encoding="utf-8")
    assert rs.resolve_root(nested) == tmp_path.resolve()


def test_build_server_registers_exactly_five_tools():
    pytest.importorskip("mcp")
    import asyncio

    tools = asyncio.run(rs.build_server().list_tools())
    assert sorted(t.name for t in tools) == [
        "check_docs", "get_guidance", "get_ruling", "list_rulings",
        "search_history",
    ]


# --- get_guidance (D-32, SPEC-2 s5) -------------------------------------

def guidance_text(
    topic: str = "context-rot",
    pillar: str = "agent-focus",
    load_when: str | None = "Work runs long.",
    evidence: str = "./context-rot.md",
    body: str = "- Rule one.",
) -> str:
    fm = [f"topic: {topic}", f"pillar: {pillar}", "audience: agent"]
    if load_when is not None:
        fm.append(f"load-when: {load_when}")
    if evidence:
        fm.append(f"evidence: {evidence}")
    fm += ["date: 2026-08-17", "status: final"]
    return "---\n" + "\n".join(fm) + "\n---\n\n# Rules\n\n" + body + "\n"


def test_guidance_catalog_is_built_from_the_files(root):
    write(root, "research/agent-focus/context-rot/guidance.md", guidance_text())
    write(root, "research/human-focus/expectation/guidance.md",
          guidance_text("expectation", "human-focus", "Scoping a task.", "./expectation.md"))
    out = rs.get_guidance()
    assert out["kind"] == "guidance_catalog"
    assert out["topics"] == [
        {"topic": "context-rot", "pillar": "agent-focus", "load_when": "Work runs long."},
        {"topic": "expectation", "pillar": "human-focus", "load_when": "Scoping a task."},
    ]
    assert out["refused"] == []


def test_guidance_topic_returns_body_and_evidence_pointer(root):
    write(root, "research/agent-focus/context-rot/guidance.md", guidance_text())
    out = rs.get_guidance("context-rot")
    assert out["kind"] == "guidance"
    assert "Rule one." in out["body"]
    # The pointer is repo-relative; the caller reads the evidence file
    # itself, and only to verify or extend a rule.
    assert out["evidence"] == "research/agent-focus/context-rot/context-rot.md"
    assert out["path"] == "research/agent-focus/context-rot/guidance.md"


def test_unknown_topic_is_typed_abstention_with_the_topic_list(root):
    write(root, "research/agent-focus/context-rot/guidance.md", guidance_text())
    out = rs.get_guidance("quantum")
    assert out["kind"] == "no_guidance"
    assert out["topics"] == ["context-rot"]


def test_a_directory_named_guidance_is_not_served(root):
    # rglob also matches directories; only files are guidance. A
    # directory named guidance.md must not crash the catalog read.
    write(root, "research/agent-focus/context-rot/guidance.md", guidance_text())
    (root / "research" / "human-focus" / "guidance.md").mkdir(parents=True)
    out = rs.get_guidance()
    assert out["kind"] == "guidance_catalog"
    assert [t["topic"] for t in out["topics"]] == ["context-rot"]


def test_oversized_guidance_is_refused_not_discovered(root):
    # D-32: the size bound is checked where the catalog is built.
    from radialrig.doc_core import GUIDANCE_LINE_BOUND

    big = "- A rule.\n" * (GUIDANCE_LINE_BOUND + 1)
    write(root, "research/agent-focus/context-rot/guidance.md",
          guidance_text(body=big))
    catalog = rs.get_guidance()
    assert catalog["topics"] == []
    assert len(catalog["refused"]) == 1
    assert catalog["refused"][0]["topic"] == "context-rot"
    assert "bound" in catalog["refused"][0]["reason"]
    out = rs.get_guidance("context-rot")
    assert out["kind"] == "refused"
    assert "body" not in out
    assert "A rule." not in str(out)


def test_guidance_without_trigger_is_refused(root):
    # D-32: the file describes itself; one without a trigger cannot.
    write(root, "research/agent-focus/context-rot/guidance.md",
          guidance_text(load_when=None))
    catalog = rs.get_guidance()
    assert catalog["topics"] == []
    assert "load-when" in catalog["refused"][0]["reason"]
    assert rs.get_guidance("context-rot")["kind"] == "refused"


def test_duplicate_topic_is_conflict_without_content(root):
    write(root, "research/agent-focus/context-rot/guidance.md",
          guidance_text(body="Secret A"))
    write(root, "research/human-focus/context-rot/guidance.md",
          guidance_text(pillar="human-focus", body="Secret B"))
    out = rs.get_guidance("context-rot")
    assert out["kind"] == "conflict"
    assert "Secret" not in str(out)


def test_guidance_never_reads_the_index(root):
    # D-32: the files are the only source. A stale or fabricated INDEX.md
    # row must change nothing.
    write(root, "research/INDEX.md",
          "| phantom-topic | Always | `phantom/guidance.md` |\n")
    write(root, "research/agent-focus/context-rot/guidance.md", guidance_text())
    out = rs.get_guidance()
    assert [t["topic"] for t in out["topics"]] == ["context-rot"]
    assert rs.get_guidance("phantom-topic")["kind"] == "no_guidance"


# --- check_docs ---------------------------------------------------------

# --- Wire declarations (D-13 constraint 5, D-18 constraint 6) ----------

def read_tools():
    import asyncio

    return asyncio.run(rs.build_server().list_tools())


def test_every_read_tool_declares_itself_read_only():
    """A client must be able to tell these five from the three write verbs
    without reading the source."""
    tools = read_tools()
    assert len(tools) == 5
    for tool in tools:
        assert tool.annotations is not None, f"{tool.name} declares no annotations"
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name


def test_a_required_query_may_not_be_empty(root):
    for tool in read_tools():
        for name, spec in tool.input_schema["properties"].items():
            if name in tool.input_schema.get("required", []):
                assert spec.get("minLength") == 1, f"{tool.name}.{name}"


def test_check_docs_clean_tree(root):
    """A clean corpus inside git passes, and C5 stays silent because it
    actually ran."""
    write(root, "docs/decisions/D-90-test.md", record_text())
    for args in (
        ["init", "-q", "-b", "master"], ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    out = rs.check_docs()
    assert out["kind"] == "check"
    assert out["ok"] is True
    assert out["failures"] == []


def test_check_docs_says_when_c5_could_not_run(root):
    """Outside git there is no prior state, so C5 cannot run. It reports
    that rather than passing in silence, and the other checks still run."""
    write(root, "docs/decisions/D-90-test.md", record_text())
    out = rs.check_docs()
    assert out["ok"] is False
    assert len(out["failures"]) == 1
    assert out["failures"][0].startswith("C5 ")
    assert "did not run" in out["failures"][0]


def test_check_docs_reports_seeded_defect(root):
    write(root, "docs/decisions/D-90-a.md", record_text())
    write(root, "docs/decisions/D-90-b.md", record_text())
    out = rs.check_docs()
    assert out["ok"] is False
    assert any(f.startswith("C1") for f in out["failures"])


# --- the handshake (PLAN-0018 phase 1) ---------------------------------

def handshake(server):
    """The `serverInfo` a client is given by `initialize`, over the SDK's
    in-memory streams: the block the stdio transport sends, reached the
    way a client reaches it rather than read off the server object."""
    import asyncio

    import anyio
    from mcp.client.session import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    low = server._lowlevel_server

    async def go():
        async with create_client_server_memory_streams() as (client_end, server_end):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(
                    low.run, server_end[0], server_end[1],
                    low.create_initialization_options())
                async with ClientSession(*client_end) as session:
                    result = await session.initialize()
                tasks.cancel_scope.cancel()
        return result.server_info

    return asyncio.run(go())


def test_the_handshake_names_the_installed_engine():
    # BUG-0013: an unversioned server tells a client nothing about which
    # engine answers. The version is the installed package's, read
    # through the one function every surface reads (Invariant 4).
    from radialrig import mount as mnt

    info = handshake(rs.build_server())
    assert info.name == "radialrig"
    assert info.version == mnt.installed_version()
