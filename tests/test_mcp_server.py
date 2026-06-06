"""Tests for the state-centric MCP server tools."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_mcp_state():
    """Reset in-memory blackboards and use isolated temp dir between tests."""
    from src import mcp_server
    original_root = mcp_server._STORE_ROOT
    tmp = Path(tempfile.mkdtemp(prefix="irys-test-"))
    mcp_server._STORE_ROOT = tmp
    mcp_server._blackboards.clear()
    mcp_server._locks.clear()
    yield tmp
    mcp_server._STORE_ROOT = original_root
    mcp_server._blackboards.clear()
    mcp_server._locks.clear()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def sample_doc(tmp_path):
    """Create a sample text document for ingestion."""
    doc = tmp_path / "report.txt"
    doc.write_text("# Summary\nRevenue was $10M in Q3 2024.\n\n# Details\nCosts were $5M.\n")
    return doc


@pytest.fixture
def sample_dir(tmp_path):
    """Create a directory with multiple documents."""
    (tmp_path / "a.txt").write_text("Document A content about contracts.")
    (tmp_path / "b.txt").write_text("Document B content about revenue.")
    return tmp_path


# ── Tool Registration ─────────────────────────────────────────────────


def test_server_registers_all_tools():
    from src.mcp_server import mcp
    tools = list(mcp._tool_manager._tools.keys())
    expected = [
        "irys_start_analysis",
        "irys_create_blackboard", "irys_get_context", "irys_add_entries",
        "irys_add_signal", "irys_get_state", "irys_get_document_text",
        "irys_search_documents", "irys_set_iteration", "irys_convergence_report",
        "irys_synthesis_packet", "irys_save_snapshot", "irys_list_blackboards",
        "irys_supported_formats",
    ]
    for name in expected:
        assert name in tools, f"Missing tool: {name}"


def test_supported_formats():
    from src.mcp_server import irys_supported_formats
    result = json.loads(irys_supported_formats())
    assert ".pdf" in result["formats"]
    assert ".docx" in result["formats"]


# ── Create Blackboard ─────────────────────────────────────────────────


def test_create_blackboard_no_docs():
    from src.mcp_server import irys_create_blackboard
    result = json.loads(irys_create_blackboard("Analyze the report"))
    assert "blackboard_id" in result
    assert result["task_instruction"] == "Analyze the report"
    assert result["summary"]["documents"] == 0


def test_create_blackboard_with_file(sample_doc):
    from src.mcp_server import irys_create_blackboard
    result = json.loads(irys_create_blackboard("Revenue question", str(sample_doc)))
    assert result["summary"]["documents"] == 1
    assert result["documents"][0]["name"] == "report.txt"


def test_create_blackboard_with_directory(sample_dir):
    from src.mcp_server import irys_create_blackboard
    result = json.loads(irys_create_blackboard("Compare docs", str(sample_dir)))
    assert result["summary"]["documents"] == 2


def test_create_blackboard_nonexistent_path():
    from src.mcp_server import irys_create_blackboard
    result = json.loads(irys_create_blackboard("test", "/no/such/path"))
    assert "error" in result


def test_create_blackboard_unsupported_file(tmp_path):
    from src.mcp_server import irys_create_blackboard
    bad = tmp_path / "data.xyz"
    bad.write_text("hello")
    result = json.loads(irys_create_blackboard("test", str(bad)))
    assert "error" in result


def test_create_blackboard_with_metadata():
    from src.mcp_server import irys_create_blackboard
    meta = json.dumps({"source": "test", "priority": "high"})
    result = json.loads(irys_create_blackboard("test", metadata=meta))
    assert "blackboard_id" in result


# ── Get Context ───────────────────────────────────────────────────────


def test_get_context_returns_docs_and_contract(sample_doc):
    from src.mcp_server import irys_create_blackboard, irys_get_context
    bb = json.loads(irys_create_blackboard("Revenue?", str(sample_doc)))
    ctx = json.loads(irys_get_context(bb["blackboard_id"]))

    assert ctx["task_instruction"] == "Revenue?"
    assert len(ctx["document_sections"]) == 1
    assert "Revenue" in ctx["document_sections"][0]["text"]
    assert "write_contract" in ctx
    assert "observation" in ctx["write_contract"]["entry_types"]


def test_get_context_nonexistent_bb():
    from src.mcp_server import irys_get_context
    result = json.loads(irys_get_context("nonexistent"))
    assert "error" in result


def test_get_context_respects_max_chars(sample_doc):
    from src.mcp_server import irys_create_blackboard, irys_get_context
    bb = json.loads(irys_create_blackboard("test", str(sample_doc)))
    ctx = json.loads(irys_get_context(bb["blackboard_id"], max_chars=10))
    assert len(ctx["document_sections"][0]["text"]) <= 10


# ── Add Entries ───────────────────────────────────────────────────────


def _create_bb(instruction="test"):
    from src.mcp_server import irys_create_blackboard
    return json.loads(irys_create_blackboard(instruction))["blackboard_id"]


def test_add_entries_basic():
    from src.mcp_server import irys_add_entries
    bb_id = _create_bb()
    entries = json.dumps([
        {"type": "observation", "content": "Revenue is $10M", "confidence": 0.8},
    ])
    result = json.loads(irys_add_entries(bb_id, entries))
    assert len(result["created_entries"]) == 1
    assert result["created_entries"][0]["type"] == "observation"
    assert result["summary"]["active_entries"] == 1


def test_add_entries_with_source_and_signals():
    from src.mcp_server import irys_add_entries
    bb_id = _create_bb()
    entries = json.dumps([{
        "type": "analysis",
        "content": "Revenue grew 15% YoY",
        "source": {"document": "report.txt", "section": "Summary", "evidence": "Q3 vs Q3"},
        "opens_questions": ["What drove the growth?", "Is this sustainable?"],
        "confidence": 0.75,
    }])
    result = json.loads(irys_add_entries(bb_id, entries))
    assert len(result["new_signals"]) == 2
    assert result["new_signals"][0]["type"] == "question"


def test_add_entries_invalid_json():
    from src.mcp_server import irys_add_entries
    bb_id = _create_bb()
    result = json.loads(irys_add_entries(bb_id, "not json"))
    assert "error" in result


def test_add_entries_not_array():
    from src.mcp_server import irys_add_entries
    bb_id = _create_bb()
    result = json.loads(irys_add_entries(bb_id, '{"type": "observation"}'))
    assert "error" in result


def test_add_entries_contradiction_detection():
    from src.mcp_server import irys_add_entries, irys_get_state
    bb_id = _create_bb()

    first = json.dumps([{"type": "observation", "content": "Revenue is $10M"}])
    r1 = json.loads(irys_add_entries(bb_id, first))
    entry_id = r1["created_entries"][0]["id"]

    second = json.dumps([{
        "type": "observation",
        "content": "Revenue is $8M",
        "contradicts_entries": [entry_id],
    }])
    r2 = json.loads(irys_add_entries(bb_id, second))
    assert any(s["type"] == "contradiction_resolution" for s in r2["new_signals"])

    state = json.loads(irys_get_state(bb_id))
    disputed = [e for e in state["entries"] if e["status"] == "disputed"]
    assert len(disputed) >= 2


def test_add_entries_nonexistent_bb():
    from src.mcp_server import irys_add_entries
    result = json.loads(irys_add_entries("fake", "[]"))
    assert "error" in result


# ── Add Signal ────────────────────────────────────────────────────────


def test_add_signal():
    from src.mcp_server import irys_add_signal
    bb_id = _create_bb()
    result = json.loads(irys_add_signal(bb_id, "question", "What is the margin?"))
    assert result["signal"]["type"] == "question"
    assert not result["deduped"]


def test_add_signal_dedup():
    from src.mcp_server import irys_add_signal
    bb_id = _create_bb()
    irys_add_signal(bb_id, "question", "What is the margin?")
    r2 = json.loads(irys_add_signal(bb_id, "question", "What is the margin?"))
    assert r2["deduped"]


def test_add_signal_nonexistent_bb():
    from src.mcp_server import irys_add_signal
    result = json.loads(irys_add_signal("fake", "question", "test"))
    assert "error" in result


# ── Get State ─────────────────────────────────────────────────────────


def test_get_state_filters():
    from src.mcp_server import irys_add_entries, irys_get_state
    bb_id = _create_bb()
    entries = json.dumps([
        {"type": "observation", "content": "Fact A"},
        {"type": "observation", "content": "Fact B"},
    ])
    irys_add_entries(bb_id, entries)
    state = json.loads(irys_get_state(bb_id, max_entries=1))
    assert len(state["entries"]) == 1


def test_get_state_nonexistent_bb():
    from src.mcp_server import irys_get_state
    result = json.loads(irys_get_state("fake"))
    assert "error" in result


# ── Get Document Text ─────────────────────────────────────────────────


def test_get_document_text(sample_doc):
    from src.mcp_server import irys_create_blackboard, irys_get_document_text
    bb = json.loads(irys_create_blackboard("test", str(sample_doc)))
    doc_id = bb["documents"][0]["id"]
    result = json.loads(irys_get_document_text(bb["blackboard_id"], doc_id))
    assert "Revenue" in result["text"]
    assert result["total_chars"] > 0


def test_get_document_text_pagination(sample_doc):
    from src.mcp_server import irys_create_blackboard, irys_get_document_text
    bb = json.loads(irys_create_blackboard("test", str(sample_doc)))
    doc_id = bb["documents"][0]["id"]
    r1 = json.loads(irys_get_document_text(bb["blackboard_id"], doc_id, max_chars=10))
    assert len(r1["text"]) == 10
    assert r1["truncated"]


def test_get_document_text_mark_read(sample_doc):
    from src.mcp_server import irys_create_blackboard, irys_get_document_text
    bb = json.loads(irys_create_blackboard("test", str(sample_doc)))
    doc_id = bb["documents"][0]["id"]
    r = json.loads(irys_get_document_text(
        bb["blackboard_id"], doc_id, max_chars=99999, mark_read=True,
    ))
    assert r["read_status"] in ("partially_read", "fully_read")


def test_get_document_text_nonexistent_doc(sample_doc):
    from src.mcp_server import irys_create_blackboard, irys_get_document_text
    bb = json.loads(irys_create_blackboard("test", str(sample_doc)))
    result = json.loads(irys_get_document_text(bb["blackboard_id"], "no-such-doc"))
    assert "error" in result


# ── Search Documents ──────────────────────────────────────────────────


def test_search_documents(sample_doc):
    from src.mcp_server import irys_create_blackboard, irys_search_documents
    bb = json.loads(irys_create_blackboard("test", str(sample_doc)))
    result = json.loads(irys_search_documents(bb["blackboard_id"], "Revenue"))
    assert result["total"] >= 1
    assert "Revenue" in result["results"][0]["snippet"]


def test_search_documents_no_match(sample_doc):
    from src.mcp_server import irys_create_blackboard, irys_search_documents
    bb = json.loads(irys_create_blackboard("test", str(sample_doc)))
    result = json.loads(irys_search_documents(bb["blackboard_id"], "ZZZZNOTFOUND"))
    assert result["total"] == 0


# ── Iteration & Convergence ──────────────────────────────────────────


def test_set_iteration():
    from src.mcp_server import irys_set_iteration
    bb_id = _create_bb()
    result = json.loads(irys_set_iteration(bb_id))
    assert result["iteration"] == 1


def test_set_iteration_expires_signals():
    from src.mcp_server import irys_add_signal, irys_set_iteration
    bb_id = _create_bb()
    irys_add_signal(bb_id, "question", "Low prio question", priority="low")
    for _ in range(4):
        irys_set_iteration(bb_id, expire_old_signals=False)
    result = json.loads(irys_set_iteration(bb_id))
    assert len(result["expired_signals"]) >= 1


def test_convergence_report_empty():
    from src.mcp_server import irys_convergence_report
    bb_id = _create_bb()
    result = json.loads(irys_convergence_report(bb_id))
    assert result["converged"]
    assert len(result["blockers"]) == 0


def test_convergence_report_blocked(sample_doc):
    from src.mcp_server import (
        irys_create_blackboard, irys_add_signal, irys_convergence_report,
    )
    bb = json.loads(irys_create_blackboard("test", str(sample_doc)))
    bb_id = bb["blackboard_id"]
    irys_add_signal(bb_id, "question", "Critical issue", priority="critical")
    result = json.loads(irys_convergence_report(bb_id))
    assert not result["converged"]
    assert any("critical" in b for b in result["blockers"])
    assert any("unread" in b for b in result["blockers"])


# ── Synthesis Packet ──────────────────────────────────────────────────


def test_synthesis_packet():
    from src.mcp_server import irys_add_entries, irys_synthesis_packet
    bb_id = _create_bb("Summarize the report")
    entries = json.dumps([
        {"type": "observation", "content": "Revenue is $10M", "confidence": 0.9},
        {"type": "observation", "content": "Maybe $8M", "confidence": 0.3},
    ])
    irys_add_entries(bb_id, entries)
    result = json.loads(irys_synthesis_packet(bb_id))
    assert result["task_instruction"] == "Summarize the report"
    must_include = [e["content"] for e in result["must_include_entries"]]
    assert "Revenue is $10M" in must_include
    assert "Maybe $8M" not in must_include


# ── Snapshot & List ───────────────────────────────────────────────────


def test_save_snapshot():
    from src.mcp_server import irys_save_snapshot
    bb_id = _create_bb()
    result = json.loads(irys_save_snapshot(bb_id, "test_label"))
    assert "path" in result
    assert Path(result["path"]).exists()


def test_list_blackboards():
    from src.mcp_server import irys_list_blackboards
    _create_bb("First")
    _create_bb("Second")
    result = json.loads(irys_list_blackboards())
    assert len(result["blackboards"]) == 2


# ── State Persistence ─────────────────────────────────────────────────


def test_state_persists_across_memory_clear(clean_mcp_state):
    from src import mcp_server
    from src.mcp_server import irys_create_blackboard, irys_add_entries, irys_get_state

    bb = json.loads(irys_create_blackboard("persist test"))
    bb_id = bb["blackboard_id"]
    entries = json.dumps([{"type": "observation", "content": "Persisted fact"}])
    irys_add_entries(bb_id, entries)

    mcp_server._blackboards.clear()

    state = json.loads(irys_get_state(bb_id))
    assert state["summary"]["active_entries"] == 1
    assert state["entries"][0]["content"] == "Persisted fact"


# ── Full Workflow Integration ─────────────────────────────────────────


def test_full_workflow(sample_doc):
    """End-to-end: create → read → add entries → check convergence → synthesize."""
    from src.mcp_server import (
        irys_create_blackboard, irys_get_context, irys_add_entries,
        irys_get_document_text, irys_convergence_report,
        irys_set_iteration, irys_synthesis_packet,
    )

    bb = json.loads(irys_create_blackboard("What was Q3 revenue?", str(sample_doc)))
    bb_id = bb["blackboard_id"]

    ctx = json.loads(irys_get_context(bb_id))
    assert len(ctx["document_sections"]) == 1

    doc_id = ctx["document_sections"][0]["doc_id"]
    text = json.loads(irys_get_document_text(bb_id, doc_id, mark_read=True))
    assert "Revenue" in text["text"]

    entries = json.dumps([{
        "type": "observation",
        "content": "Q3 2024 revenue was $10M",
        "source": {"document": "report.txt", "section": "Summary", "evidence": "$10M in Q3"},
        "confidence": 0.9,
    }])
    irys_add_entries(bb_id, entries)

    irys_set_iteration(bb_id)

    conv = json.loads(irys_convergence_report(bb_id))
    assert conv["converged"]

    packet = json.loads(irys_synthesis_packet(bb_id))
    assert len(packet["must_include_entries"]) == 1
    assert "$10M" in packet["must_include_entries"][0]["content"]


# ── ID Collision Regression ───────────────────────────────────────────


def test_ids_advance_after_reload(clean_mcp_state):
    """After reload from disk, new entries must not reuse loaded IDs."""
    from src import mcp_server
    from src.mcp_server import irys_create_blackboard, irys_add_entries, irys_get_state
    from src.swarm.models import reset_id_counters

    reset_id_counters()
    bb = json.loads(irys_create_blackboard("id test"))
    bb_id = bb["blackboard_id"]
    entries = json.dumps([
        {"type": "observation", "content": "First"},
        {"type": "observation", "content": "Second"},
    ])
    r1 = json.loads(irys_add_entries(bb_id, entries))
    ids_before = {e["id"] for e in r1["created_entries"]}

    mcp_server._blackboards.clear()
    reset_id_counters()

    r2 = json.loads(irys_add_entries(bb_id, json.dumps([
        {"type": "observation", "content": "After reload"},
    ])))
    ids_after = {e["id"] for e in r2["created_entries"]}
    assert not ids_before & ids_after, f"ID collision: {ids_before & ids_after}"


# ── Malformed Entry Validation ────────────────────────────────────────


def test_add_entries_non_dict_element():
    from src.mcp_server import irys_add_entries
    bb_id = _create_bb()
    result = json.loads(irys_add_entries(bb_id, '["bad"]'))
    assert "error" in result


def test_add_entries_invalid_confidence():
    from src.mcp_server import irys_add_entries
    bb_id = _create_bb()
    entries = json.dumps([{"type": "observation", "content": "test", "confidence": "high"}])
    result = json.loads(irys_add_entries(bb_id, entries))
    assert result["created_entries"][0]["confidence"] == 0.7


def test_add_entries_clamps_confidence():
    from src.mcp_server import irys_add_entries
    bb_id = _create_bb()
    entries = json.dumps([{"type": "observation", "content": "test", "confidence": 5.0}])
    result = json.loads(irys_add_entries(bb_id, entries))
    assert result["created_entries"][0]["confidence"] == 1.0


def test_add_entries_unknown_type_defaults():
    from src.mcp_server import irys_add_entries
    bb_id = _create_bb()
    entries = json.dumps([{"type": "unknown_type", "content": "test"}])
    result = json.loads(irys_add_entries(bb_id, entries))
    assert result["created_entries"][0]["type"] == "observation"


def test_search_empty_query(sample_doc):
    from src.mcp_server import irys_create_blackboard, irys_search_documents
    bb = json.loads(irys_create_blackboard("test", str(sample_doc)))
    result = json.loads(irys_search_documents(bb["blackboard_id"], ""))
    assert result["total"] == 0


# ── Bootstrap Tool ───────────────────────────────────────────────────


def test_start_analysis_returns_context_and_next_steps(sample_doc):
    from src.mcp_server import irys_start_analysis
    result = json.loads(irys_start_analysis("Analyze revenue", str(sample_doc)))
    assert "blackboard_id" in result
    assert result["task_instruction"] == "Analyze revenue"
    assert len(result["documents"]) == 1
    assert "initial_context" in result
    assert "document_sections" in result["initial_context"]
    assert len(result["next_steps"]) >= 5
    assert "entry_template" in result
    assert result["entry_template"]["type"] == "observation"


def test_start_analysis_with_directory(sample_dir):
    from src.mcp_server import irys_start_analysis
    result = json.loads(irys_start_analysis("Compare docs", str(sample_dir)))
    assert len(result["documents"]) == 2
    ctx = result["initial_context"]
    assert len(ctx["document_sections"]) >= 1


def test_start_analysis_no_docs():
    from src.mcp_server import irys_start_analysis
    result = json.loads(irys_start_analysis("General analysis"))
    assert "blackboard_id" in result
    assert result["documents"] == []


def test_start_analysis_bad_path():
    from src.mcp_server import irys_start_analysis
    result = json.loads(irys_start_analysis("test", "/nonexistent/path"))
    assert "error" in result


def test_start_analysis_context_has_write_contract(sample_doc):
    from src.mcp_server import irys_start_analysis
    result = json.loads(irys_start_analysis("test", str(sample_doc)))
    wc = result["initial_context"]["write_contract"]
    assert "observation" in wc["entry_types"]
    assert "gap" in wc["entry_types"]
    assert "question" in wc["signal_types"]


def test_start_analysis_marks_returned_docs_as_read(sample_doc):
    from src.mcp_server import irys_start_analysis, irys_convergence_report
    result = json.loads(irys_start_analysis("test", str(sample_doc)))
    bb_id = result["blackboard_id"]
    conv = json.loads(irys_convergence_report(bb_id))
    unread_names = [d["name"] for d in conv.get("unread_documents", [])]
    assert "report.txt" not in unread_names


# ── MCP Prompts ──────────────────────────────────────────────────────


def test_prompts_registered():
    from src.mcp_server import mcp
    prompts = list(mcp._prompt_manager._prompts.keys())
    assert "analyze-documents" in prompts
    assert "resume-blackboard" in prompts


def test_prompt_analyze_documents():
    from src.mcp_server import prompt_analyze_documents
    result = prompt_analyze_documents("Find all obligations", "/tmp/docs")
    assert "Find all obligations" in result
    assert "/tmp/docs" in result
    assert "irys_start_analysis" in result
    assert "irys_convergence_report" in result


def test_prompt_resume_blackboard():
    from src.mcp_server import prompt_resume_blackboard
    result = prompt_resume_blackboard("abc123")
    assert "abc123" in result
    assert "irys_get_state" in result
    assert "irys_synthesis_packet" in result


# ── Server Instructions ──────────────────────────────────────────────


def test_instructions_contain_workflow():
    from src.mcp_server import _INSTRUCTIONS
    assert "irys_start_analysis" in _INSTRUCTIONS
    assert "irys_add_entries" in _INSTRUCTIONS
    assert "irys_convergence_report" in _INSTRUCTIONS
    assert "irys_synthesis_packet" in _INSTRUCTIONS
    assert "observation" in _INSTRUCTIONS
    assert "source-grounded" in _INSTRUCTIONS
