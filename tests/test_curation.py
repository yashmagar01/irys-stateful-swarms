import json
import threading
import time

from src.swarm.blackboard import Blackboard
from src.swarm.curation import _coverage_safety_net, curate_entries
from src.swarm.models import Entry, EntrySource, ModelResult


class CurationFakeCaller:
    def __init__(self):
        self.lock = threading.Lock()
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        with self.lock:
            self.prompts.append(prompt)
        if '"doc-a.docx"' in prompt:
            text = '{"must_include":[{"entry_id":"a","importance":"critical","section":"A","summary":"A fact"}]}'
            tokens_in = 11
        elif '"doc-b.docx"' in prompt:
            time.sleep(0.02)
            text = '{"must_include":[{"entry_id":"b","importance":"critical","section":"B","summary":"B fact"}]}'
            tokens_in = 13
        else:
            text = '{"must_include":[{"entry_id":"c","importance":"critical","section":"C","summary":"C fact"}]}'
            tokens_in = 17
        return ModelResult(
            text=text,
            tokens_input=tokens_in,
            tokens_output=3,
            tokens_total=tokens_in + 3,
            model="fake-model",
            latency_ms=1,
        )


def test_parallel_curation_preserves_cluster_order_and_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_CURATION_WORKERS", "3")
    blackboard = Blackboard(
        task_instruction="Draft a compliance manual.",
        output_dir=str(tmp_path),
        entries=[
            Entry(id="a", content="A content", source=EntrySource(document="doc-a.docx")),
            Entry(id="b", content="B content", source=EntrySource(document="doc-b.docx")),
            Entry(id="c", content="C content", source=EntrySource(document="doc-c.docx")),
        ],
    )
    caller = CurationFakeCaller()

    items, tokens = curate_entries(blackboard, caller)
    blackboard.add_tokens_from_last_call(tokens)

    assert [item["entry_id"] for item in items] == ["a", "b", "c"]
    assert tokens == (11 + 3) + (13 + 3) + (17 + 3)
    assert blackboard.tokens_input == 11 + 13 + 17
    assert blackboard.tokens_output == 9
    assert blackboard.cost_by_model["fake-model"]["calls"] == 3

    progress_path = tmp_path / "swarm" / "curation_progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["completed_clusters"] == 3
    assert progress["total_clusters"] == 3


def test_coverage_safety_net_adds_from_uncovered_docs():
    active = [
        Entry(id="e1", content="Fact from doc-a", confidence=0.9,
              source=EntrySource(document="doc-a.docx", section="Intro")),
        Entry(id="e2", content="Fact from doc-b", confidence=0.8,
              source=EntrySource(document="doc-b.docx", section="Terms")),
        Entry(id="e3", content="Low confidence from doc-b", confidence=0.3,
              source=EntrySource(document="doc-b.docx")),
    ]
    must_include = [{"entry_id": "e1", "section": "A", "summary": "A fact"}]
    result = _coverage_safety_net(must_include, active)
    added_ids = [m["entry_id"] for m in result if m.get("source") == "coverage_safety_net"]
    assert "e2" in added_ids, "High-confidence entry from uncovered doc-b should be added"
    assert "e3" not in added_ids, "Low-confidence entry should be skipped"


def test_coverage_safety_net_handles_entry_ids_list_format():
    active = [
        Entry(id="e1", content="From doc-a", confidence=0.9,
              source=EntrySource(document="doc-a.docx")),
        Entry(id="e2", content="From doc-b", confidence=0.9,
              source=EntrySource(document="doc-b.docx")),
    ]
    must_include = [{"entry_ids": ["e1"], "section": "A", "summary": "A fact"}]
    result = _coverage_safety_net(must_include, active)
    added_ids = [m["entry_id"] for m in result if m.get("source") == "coverage_safety_net"]
    assert "e1" not in added_ids, "Already-curated entry via entry_ids should not be re-added"
    assert "e2" in added_ids, "Uncovered doc entry should be added"


def test_coverage_safety_net_caps_at_max_per_doc():
    active = [
        Entry(id=f"e{i}", content=f"Fact {i}", confidence=0.9,
              source=EntrySource(document="big-doc.docx"))
        for i in range(10)
    ]
    must_include: list[dict] = []
    result = _coverage_safety_net(must_include, active)
    added = [m for m in result if m.get("source") == "coverage_safety_net"]
    assert len(added) == 3, f"Should cap at MAX_AUTOINCLUDES_PER_DOC=3, got {len(added)}"


def test_coverage_safety_net_no_duplicates():
    active = [
        Entry(id="e1", content="Fact", confidence=0.9,
              source=EntrySource(document="doc-a.docx")),
    ]
    must_include = [{"entry_id": "e1", "section": "A", "summary": "Already included"}]
    result = _coverage_safety_net(must_include, active)
    added = [m for m in result if m.get("source") == "coverage_safety_net"]
    assert len(added) == 0, "Should not add entries that are already curated"
