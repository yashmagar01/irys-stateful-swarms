import json

from src.swarm.blackboard import Blackboard
from src.swarm.debt_sensors import (
    debt_sensor_items_to_gap_entries,
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
