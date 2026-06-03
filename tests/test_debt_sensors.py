import json

from src.swarm.blackboard import Blackboard
from src.swarm.debt_sensors import (
    debt_sensor_items_to_gap_entries,
    execute_relation_debt_items,
    normalize_debt_sensor_items,
    run_debt_sensors,
)
from src.swarm.models import Entry, EntrySource, ModelResult


class SequenceCaller:
    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        self.prompts.append(prompt)
        return ModelResult(
            text=self.texts.pop(0),
            tokens_input=20,
            tokens_output=10,
            tokens_total=30,
            model="fake-model",
            latency_ms=1,
        )


def test_normalize_debt_sensor_items_counts_actionable():
    items = normalize_debt_sensor_items([
        {
            "type": "relation",
            "subtype": "conflict",
            "reason": "Compare the payment date in the amendment against the base agreement.",
            "parent_entry_ids": ["e1", "e2"],
            "confidence": 0.82,
        },
        {
            "type": "source_object",
            "subtype": "missing_population",
            "reason": "Need all schedule rows.",
            "confidence": 0.4,
        },
    ])

    assert items[0]["status"] == "actionable_gap"
    assert items[1]["status"] == "diagnostic_only"


def test_debt_sensor_items_materialize_gap_entries():
    blackboard = Blackboard(task_instruction="Compare documents.")
    items = normalize_debt_sensor_items([{
        "type": "relation",
        "subtype": "conflict",
        "reason": "Compare the payment date in the amendment against the base agreement.",
        "parent_entry_ids": ["e1", "e2"],
        "confidence": 0.9,
    }])

    entries = debt_sensor_items_to_gap_entries(items, blackboard)

    assert len(entries) == 1
    assert entries[0].type == "gap"
    assert "missing_work:compare" in entries[0].tags
    assert entries[0].supports_entries == ["e1", "e2"]


def test_run_debt_sensors_detect_only_writes_report(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_RELATION_DEBT", "1")
    monkeypatch.setenv("SWARM_DEBT_SENSORS_DETECT_ONLY", "1")
    monkeypatch.setenv("SWARM_PROMPT_AUDIT", "1")
    blackboard = Blackboard(
        task_instruction="Compare documents.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="Agreement says payment is due June 1.",
                source=EntrySource(document="agreement.docx", section="1", evidence="June 1"),
            ),
            Entry(
                id="e2",
                type="analysis",
                content="Amendment says payment is due July 1.",
                source=EntrySource(document="amendment.docx", section="2", evidence="July 1"),
            ),
        ],
    )
    caller = SequenceCaller([json.dumps({
        "items": [{
            "type": "relation",
            "subtype": "date_alignment",
            "reason": "Compare the June 1 and July 1 payment dates.",
            "parent_entry_ids": ["e1", "e2"],
            "confidence": 0.9,
        }]
    })])

    report, tokens = run_debt_sensors(blackboard, {}, caller)

    assert tokens == 30
    assert report["summary"]["actionable"] == 1
    assert len(blackboard.entries) == 2
    assert (tmp_path / "swarm" / "debt_sensors.json").exists()
    assert (tmp_path / "swarm" / "prompt_audit.json").exists()


def test_execute_relation_debt_items_creates_analysis_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_RELATION_DEBT_EXECUTE", "1")
    blackboard = Blackboard(
        task_instruction="Compare payment terms.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="Agreement says payment is due June 1.",
                source=EntrySource(document="agreement.docx", section="1", evidence="June 1"),
            ),
            Entry(
                id="e2",
                type="analysis",
                content="Amendment says payment is due July 1.",
                source=EntrySource(document="amendment.docx", section="2", evidence="July 1"),
            ),
        ],
    )
    items = normalize_debt_sensor_items([{
        "type": "relation",
        "subtype": "date_alignment",
        "reason": "Compare the June 1 and July 1 payment dates.",
        "parent_entry_ids": ["e1", "e2"],
        "confidence": 0.9,
    }])
    caller = SequenceCaller([json.dumps({
        "status": "computed",
        "content": "The amendment changes the payment deadline from June 1 to July 1.",
        "relation_type": "date_alignment",
        "evidence": "agreement June 1; amendment July 1",
        "confidence": 0.88,
    })])

    report, tokens = execute_relation_debt_items(blackboard, caller, items)

    assert tokens == 30
    assert report["summary"]["entries_created"] == 1
    assert report["items"][0]["status"] == "relation_executed"
    entry = report["entries"][0]
    assert entry.type == "analysis"
    assert entry.supports_entries == ["e1", "e2"]
    assert "debt_type:relation" in entry.tags
    assert "lifecycle:transformed" in entry.tags
    assert entry.source.document == "agreement.docx; amendment.docx"


def test_execute_relation_debt_requires_two_source_documents():
    blackboard = Blackboard(
        task_instruction="Compare payment terms.",
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="Agreement says payment is due June 1.",
                source=EntrySource(document="agreement.docx", section="1", evidence="June 1"),
            ),
            Entry(
                id="e2",
                type="analysis",
                content="Agreement also says notice is required.",
                source=EntrySource(document="agreement.docx", section="2", evidence="notice"),
            ),
        ],
    )
    items = normalize_debt_sensor_items([{
        "type": "relation",
        "subtype": "date_alignment",
        "reason": "Compare two same-document payment provisions.",
        "parent_entry_ids": ["e1", "e2"],
        "confidence": 0.9,
    }])
    caller = SequenceCaller(['{"status":"computed","content":"Should not run."}'])

    report, tokens = execute_relation_debt_items(blackboard, caller, items)

    assert tokens == 0
    assert report["entries"] == []
    assert report["items"][0]["status"] == "diagnostic_only"
    assert report["items"][0]["execution_error"] == "relation_requires_two_source_documents"
    assert caller.prompts == []


def test_run_debt_sensors_executes_relation_without_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_RELATION_DEBT", "1")
    monkeypatch.setenv("SWARM_RELATION_DEBT_EXECUTE", "1")
    monkeypatch.setenv("SWARM_PROMPT_AUDIT", "1")
    blackboard = Blackboard(
        task_instruction="Compare documents.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="Agreement says payment is due June 1.",
                source=EntrySource(document="agreement.docx", section="1", evidence="June 1"),
            ),
            Entry(
                id="e2",
                type="analysis",
                content="Amendment says payment is due July 1.",
                source=EntrySource(document="amendment.docx", section="2", evidence="July 1"),
            ),
        ],
    )
    caller = SequenceCaller([
        json.dumps({
            "items": [{
                "type": "relation",
                "subtype": "date_alignment",
                "reason": "Compare the June 1 and July 1 payment dates.",
                "parent_entry_ids": ["e1", "e2"],
                "confidence": 0.9,
            }]
        }),
        json.dumps({
            "status": "computed",
            "content": "The amendment changes the payment deadline from June 1 to July 1.",
            "relation_type": "date_alignment",
            "evidence": "agreement June 1; amendment July 1",
            "confidence": 0.88,
        }),
    ])

    report, tokens = run_debt_sensors(blackboard, {}, caller)

    assert tokens == 60
    assert report["mode"] == "execute_relation_debt"
    assert report["created_gap_entry_ids"] == []
    assert len(report["created_relation_entry_ids"]) == 1
    assert len(blackboard.entries) == 3
    created = blackboard.find_entry(report["created_relation_entry_ids"][0])
    assert created is not None
    assert created.type == "analysis"
    written = json.loads((tmp_path / "swarm" / "debt_sensors.json").read_text())
    assert written["relation_execution_summary"]["entries_created"] == 1
