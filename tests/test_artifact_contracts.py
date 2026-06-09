import json

from src.swarm.artifact_contracts import build_artifact_contracts, _topic_summary
from src.swarm.blackboard import Blackboard
from src.swarm.models import Entry, EntrySource, ModelResult


class FakeContractCaller:
    def __init__(self, contracts):
        self.prompts: list[str] = []
        self._contracts = contracts

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        self.prompts.append(prompt)
        return ModelResult(
            text=json.dumps({"contracts": self._contracts}),
            tokens_input=100,
            tokens_output=50,
            tokens_total=150,
            model="fake-model",
            latency_ms=1,
        )


def _make_blackboard(n_entries=3):
    entries = [
        Entry(
            id=f"e{i}",
            type="observation",
            content=f"Finding {i} about section {i}",
            source=EntrySource(f"doc{i}.pdf", f"Section {i}", f"evidence {i}"),
            confidence=0.9,
        )
        for i in range(n_entries)
    ]
    return Blackboard(
        task_instruction="Analyze the merger agreement and produce a deviation summary.",
        entries=entries,
    )


def test_build_artifact_contracts_returns_items():
    bb = _make_blackboard()
    fake_contracts = [
        {"section": "Liquidation Preferences", "native_form": "table",
         "summary": "Compare LP terms across docs", "importance": "critical"},
        {"section": "Anti-Dilution", "native_form": "section",
         "summary": "Describe anti-dilution provisions", "importance": "high"},
    ]
    caller = FakeContractCaller(fake_contracts)
    items, tokens = build_artifact_contracts(
        bb, {"memo": "memo.docx"}, caller,
    )
    assert len(items) == 2
    assert tokens == 150
    assert items[0]["section"] == "Liquidation Preferences"
    assert items[0]["native_form"] == "table"
    assert items[0]["target_file"] == "memo.docx"
    assert items[0]["source"] == "artifact_contract"
    assert items[1]["importance"] == "high"


def test_build_artifact_contracts_multiple_deliverables():
    bb = _make_blackboard()
    fake_contracts = [
        {"section": "Overview", "native_form": "paragraph",
         "summary": "Executive summary", "importance": "critical"},
    ]
    caller = FakeContractCaller(fake_contracts)
    items, tokens = build_artifact_contracts(
        bb, {"memo": "memo.docx", "spreadsheet": "analysis.xlsx"}, caller,
    )
    assert len(items) == 2
    assert tokens == 300
    files = {item["target_file"] for item in items}
    assert files == {"memo.docx", "analysis.xlsx"}
    assert len(caller.prompts) == 2


def test_build_artifact_contracts_deduplicates_filenames():
    bb = _make_blackboard()
    caller = FakeContractCaller([{"section": "S", "native_form": "table",
                                  "summary": "X", "importance": "high"}])
    items, tokens = build_artifact_contracts(
        bb, {"a": "same.docx", "b": "same.docx"}, caller,
    )
    assert len(items) == 1
    assert len(caller.prompts) == 1


def test_build_artifact_contracts_empty_response():
    bb = _make_blackboard()
    caller = FakeContractCaller([])
    items, tokens = build_artifact_contracts(bb, {"memo": "out.docx"}, caller)
    assert items == []
    assert tokens == 150


def test_build_artifact_contracts_malformed_entries_skipped():
    bb = _make_blackboard()
    caller = FakeContractCaller([
        "not a dict",
        {"section": "Valid", "native_form": "table", "summary": "OK", "importance": "high"},
        42,
    ])
    items, _ = build_artifact_contracts(bb, {"memo": "out.docx"}, caller)
    assert len(items) == 1
    assert items[0]["section"] == "Valid"


def test_topic_summary():
    entries = [
        Entry(id="e1", type="observation", content="A",
              source=EntrySource("doc1.pdf", "Sec 1", "ev"), confidence=0.9),
        Entry(id="e2", type="observation", content="B",
              source=EntrySource("doc1.pdf", "Sec 1", "ev"), confidence=0.9),
        Entry(id="e3", type="analysis", content="C",
              source=EntrySource("doc2.pdf", "Sec 2", "ev"), confidence=0.8),
        Entry(id="e4", type="analysis", content="D", confidence=0.7),
    ]
    summary = _topic_summary(entries)
    assert "doc1.pdf/Sec 1 (2 entries)" in summary
    assert "doc2.pdf/Sec 2 (1 entries)" in summary
    assert "cross-cutting/general (1 entries)" in summary
