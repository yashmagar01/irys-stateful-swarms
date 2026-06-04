import json

from src.swarm.blackboard import Blackboard
from src.swarm.blackboard_maintenance import (
    consolidation_entries,
    normalize_consolidations,
    run_blackboard_maintenance,
)
from src.swarm.models import Entry, EntrySource, ModelResult


class FakeCaller:
    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        self.prompts.append(prompt)
        return ModelResult(
            text=self.text,
            tokens_input=20,
            tokens_output=10,
            tokens_total=30,
            model="fake-model",
            latency_ms=1,
        )


class SequenceCaller:
    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        self.prompts.append(prompt)
        text = self.texts.pop(0)
        return ModelResult(
            text=text,
            tokens_input=20,
            tokens_output=10,
            tokens_total=30,
            model="fake-model",
            latency_ms=1,
        )


def _source_entry(entry_id: str, content: str, document: str = "doc.pdf") -> Entry:
    return Entry(
        id=entry_id,
        type="observation",
        content=content,
        source=EntrySource(document=document, section="1", evidence=content[:40]),
        confidence=0.9,
    )


def test_normalize_consolidations_requires_two_sourced_candidates():
    candidates = [
        _source_entry("e1", "Base agreement requires payment by June 1."),
        Entry(id="e2", type="observation", content="Unsupported duplicate."),
        _source_entry("e3", "Amendment moves payment deadline to July 1."),
    ]

    items = normalize_consolidations([
        {
            "type": "analysis",
            "content": "Payment deadline analysis consolidates the June and July dates.",
            "source_entry_ids": ["e1"],
            "confidence": 0.8,
        },
        {
            "type": "analysis",
            "content": "Payment deadline analysis consolidates the June and July dates.",
            "source_entry_ids": ["e1", "e3"],
            "confidence": 0.8,
        },
        {
            "type": "observation",
            "content": "Too short.",
            "source_entry_ids": ["e1", "e3"],
        },
    ], candidates)

    assert len(items) == 1
    assert items[0]["type"] == "analysis"
    assert items[0]["source_entry_ids"] == ["e1", "e3"]


def test_consolidation_entries_preserve_lineage_without_superseding():
    blackboard = Blackboard(
        task_instruction="Compare payment terms.",
        entries=[
            _source_entry("e1", "Base agreement requires payment by June 1."),
            _source_entry("e2", "Amendment moves payment deadline to July 1."),
        ],
    )
    items = [{
        "id": "bm_001",
        "type": "analysis",
        "content": "The payment deadline changed from June 1 to July 1.",
        "source_entry_ids": ["e1", "e2"],
        "confidence": 0.86,
        "supersede_source_entries": True,
    }]

    entries = consolidation_entries(blackboard, items, supersede=False)

    assert len(entries) == 1
    assert entries[0].supports_entries == ["e1", "e2"]
    assert entries[0].supersedes_entries == []
    assert "blackboard_maintenance" in entries[0].tags
    assert "lifecycle:compacted" in entries[0].tags


def test_run_blackboard_maintenance_writes_report_and_can_supersede(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_BLACKBOARD_MAINTENANCE", "1")
    monkeypatch.setenv("SWARM_BLACKBOARD_MAINTENANCE_SUPERSEDE", "1")
    monkeypatch.setenv("SWARM_PROMPT_AUDIT", "1")
    blackboard = Blackboard(
        task_instruction="Compare payment terms.",
        output_dir=str(tmp_path),
        entries=[
            _source_entry("e1", "Base agreement requires payment by June 1."),
            _source_entry("e2", "Amendment moves payment deadline to July 1."),
            _source_entry("e3", "The borrower is Acme LLC."),
        ],
    )
    caller = FakeCaller(json.dumps({
        "consolidations": [{
            "type": "analysis",
            "content": "The operative payment deadline changed from June 1 to July 1.",
            "source_entry_ids": ["e1", "e2"],
            "reason": "The two entries describe the same deadline across documents.",
            "confidence": 0.88,
            "supersede_source_entries": True,
        }]
    }))

    report, tokens = run_blackboard_maintenance(blackboard, {}, caller)

    assert tokens == 30
    assert report["summary"]["entries_created"] == 1
    assert report["summary"]["entries_superseded"] == 2
    assert blackboard.find_entry("e1").status == "superseded"
    assert blackboard.find_entry("e2").status == "superseded"
    assert any(e for e in blackboard.entries if "blackboard_maintenance" in e.tags)
    assert (tmp_path / "swarm" / "blackboard_maintenance.json").exists()
    audit = json.loads((tmp_path / "swarm" / "prompt_audit.json").read_text())
    assert audit["summary"]["records"] == 1


def test_run_blackboard_maintenance_fallback_clusters_when_first_pass_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_BLACKBOARD_MAINTENANCE", "1")
    monkeypatch.setenv("SWARM_PROMPT_AUDIT", "1")
    blackboard = Blackboard(
        task_instruction="Assess routing implementation risks.",
        output_dir=str(tmp_path),
        entries=[
            _source_entry("e1", "chat-service.ts calls the memory coverage judge before web routing.", "chat-service.ts.txt"),
            _source_entry("e2", "chat-service.ts records source_need_decision from the judge result.", "chat-service.ts.txt"),
            _source_entry("e3", "chat-service.ts does not preserve matched memory IDs in the downstream audit packet.", "chat-service.ts.txt"),
            _source_entry("e4", "settings.ts stores an unrelated user interface preference.", "settings.ts.txt"),
        ],
    )
    caller = SequenceCaller([
        json.dumps({"consolidations": []}),
        json.dumps({
            "consolidations": [{
                "type": "analysis",
                "content": "The routing state is fragmented across judge invocation, source-need recording, and missing matched-ID preservation in chat-service.ts.",
                "source_entry_ids": ["e1", "e2", "e3"],
                "reason": "The entries describe adjacent pieces of the same routing-state flow.",
                "confidence": 0.86,
                "supersede_source_entries": False,
            }]
        }),
    ])

    report, tokens = run_blackboard_maintenance(blackboard, {}, caller)

    assert tokens == 60
    assert len(caller.prompts) == 2
    assert "SOURCE-LOCAL CLUSTERS" in caller.prompts[1]
    assert report["summary"]["fallback_used"] is True
    assert report["summary"]["fallback_cluster_count"] >= 1
    assert report["summary"]["entries_created"] == 1
    assert any(e for e in blackboard.entries if "maintenance_type:consolidation" in e.tags)
