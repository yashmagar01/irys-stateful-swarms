import json

from src.swarm.blackboard import Blackboard
from src.swarm.derived_work import (
    aggregate_derived_work_reports,
    detect_calculation_debts,
    execute_calculation_work_items,
    normalize_derived_work_items,
    _prioritized_entries,
    run_calculation_debt_detection,
    summarize_derived_work_items,
)
from src.swarm.models import Entry, EntrySource, ModelResult, reset_id_counters


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


def test_normalize_requires_parent_source_and_inputs_for_executable():
    entries = [
        Entry(
            id="e1",
            type="observation",
            content="Principal is $100.",
            source=EntrySource(document="doc.pdf", section="1", evidence="$100"),
        ),
        Entry(id="e2", type="observation", content="No source."),
        Entry(
            id="e3",
            type="gap",
            content="Need to calculate the 10 percent interest.",
            tags=["missing_work:calculate"],
        ),
    ]
    raw = [
        {
            "subtype": "missing_operation",
            "reason": "Compute 10 percent of principal.",
            "parent_entry_ids": ["e1", "e3"],
            "required_inputs": [
                {"label": "principal", "value": "$100", "entry_id": "e1"},
                {"label": "rate", "value": "10%", "entry_id": "e3"},
            ],
            "expression": "100 * 0.10",
            "confidence": 0.8,
        },
        {
            "subtype": "missing_operation",
            "reason": "No parent.",
            "parent_entry_ids": [],
            "required_inputs": [{"label": "principal", "value": "$100"}],
            "expression": "100 * 0.10",
        },
        {
            "subtype": "missing_operation",
            "reason": "No source grounding.",
            "parent_entry_ids": ["e2"],
            "required_inputs": [{"label": "principal", "value": "$100", "entry_id": "e2"}],
            "expression": "100 * 0.10",
        },
        {
            "subtype": "placement_failure",
            "reason": "Already calculated but wrong file.",
            "parent_entry_ids": ["e1"],
        },
    ]

    items = normalize_derived_work_items(raw, entries)

    assert items[0]["execution_eligible"] is True
    assert items[0]["status"] == "executable"
    assert items[1]["execution_eligible"] is False
    assert "missing_parent_entry_ids" in items[1]["validation_errors"]
    assert items[2]["execution_eligible"] is False
    assert "missing_source_grounding" in items[2]["validation_errors"]
    assert items[3]["execution_eligible"] is False
    assert items[3]["subtype"] == "placement_failure"


def test_summarize_derived_work_items_counts_subtypes():
    summary = summarize_derived_work_items([
        {"subtype": "missing_operation", "status": "executable", "execution_eligible": True},
        {"subtype": "placement_failure", "status": "diagnostic_only", "execution_eligible": False},
    ])

    assert summary["selected"] == 2
    assert summary["executable"] == 1
    assert summary["subtype_counts"]["missing_operation"] == 1
    assert summary["status_counts"]["diagnostic_only"] == 1


def test_detect_calculation_debts_writes_detect_only_report(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_CALCULATION_DEBT_DETECT_ONLY", "1")
    monkeypatch.setenv("SWARM_PROMPT_AUDIT", "1")
    blackboard = Blackboard(
        task_instruction="Compute fee exposure.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Commitment amount is $1,000,000.",
                source=EntrySource(document="lpa.pdf", section="Fees", evidence="$1,000,000"),
            ),
            Entry(
                id="e2",
                type="observation",
                content="Fee rate is 2%.",
                source=EntrySource(document="lpa.pdf", section="Fees", evidence="2%"),
            ),
            Entry(
                id="e3",
                type="gap",
                content="Need to calculate annual fee exposure.",
                tags=["missing_work:calculate"],
            ),
        ],
    )
    caller = FakeCaller(json.dumps({
        "items": [{
            "subtype": "missing_operation",
            "reason": "Compute annual fee from commitment and rate.",
            "parent_entry_ids": ["e1", "e2", "e3"],
            "required_inputs": [
                {"label": "commitment", "value": "$1,000,000", "entry_id": "e1"},
                {"label": "rate", "value": "2%", "entry_id": "e2"},
            ],
            "expression": "1000000 * 0.02",
            "confidence": 0.9,
        }]
    }))

    report, tokens = run_calculation_debt_detection(blackboard, {}, caller)

    assert tokens == 30
    assert report["summary"]["executable"] == 1
    assert report["mode"] == "detect_only"
    report_path = tmp_path / "swarm" / "derived_work_items.json"
    assert json.loads(report_path.read_text(encoding="utf-8"))["summary"]["selected"] == 1
    audit_path = tmp_path / "swarm" / "prompt_audit.json"
    assert json.loads(audit_path.read_text(encoding="utf-8"))["summary"]["records"] == 1


def test_detect_calculation_debts_prompt_is_conservative():
    blackboard = Blackboard(
        task_instruction="Review workbook.",
        entries=[Entry(id="e1", type="analysis", content="Calculation exists but wrong tab.")],
    )
    caller = FakeCaller('{"items": []}')

    detect_calculation_debts(blackboard, {}, caller)

    prompt = caller.prompts[0]
    assert "Only missing_operation is executable" in prompt
    assert "workbook tab placement" in prompt


def test_prioritized_entries_respects_env_limit(monkeypatch):
    monkeypatch.setenv("SWARM_CALCULATION_DEBT_ENTRY_LIMIT", "3")
    entries = [
        Entry(id=f"e{i}", type="observation", content=f"Value {i} is ${i}.")
        for i in range(10)
    ]

    assert len(_prioritized_entries(entries)) == 3


def test_missing_operation_without_expression_is_diagnostic_only():
    entries = [
        Entry(
            id="e1",
            type="observation",
            content="Principal is $100.",
            source=EntrySource(document="doc.pdf", section="1", evidence="$100"),
        ),
        Entry(
            id="e2",
            type="observation",
            content="Rate is 10%.",
            source=EntrySource(document="doc.pdf", section="1", evidence="10%"),
        ),
    ]

    items = normalize_derived_work_items([{
        "subtype": "missing_operation",
        "reason": "Compute interest.",
        "parent_entry_ids": ["e1", "e2"],
        "required_inputs": [
            {"label": "principal", "value": "$100", "entry_id": "e1"},
            {"label": "rate", "value": "10%", "entry_id": "e2"},
        ],
    }], entries)

    assert items[0]["status"] == "diagnostic_only"
    assert "missing_executable_expression" in items[0]["validation_errors"]


def test_missing_operation_without_need_signal_is_diagnostic_only():
    entries = [
        Entry(
            id="e1",
            type="observation",
            content="Principal is $100.",
            source=EntrySource(document="doc.pdf", section="1", evidence="$100"),
        ),
        Entry(
            id="e2",
            type="observation",
            content="Rate is 10%.",
            source=EntrySource(document="doc.pdf", section="1", evidence="10%"),
        ),
    ]

    items = normalize_derived_work_items([{
        "subtype": "missing_operation",
        "reason": "Compute interest.",
        "parent_entry_ids": ["e1", "e2"],
        "required_inputs": [
            {"label": "principal", "value": "$100", "entry_id": "e1"},
            {"label": "rate", "value": "10%", "entry_id": "e2"},
        ],
        "expression": "100 * 0.10",
    }], entries)

    assert items[0]["status"] == "diagnostic_only"
    assert "missing_calculation_need_signal" in items[0]["validation_errors"]


def test_missing_operation_with_result_already_in_parent_is_diagnostic_only():
    entries = [
        Entry(
            id="e1",
            type="calculation",
            content="Net equity equals $1,849,900 after subtracting liabilities.",
            source=EntrySource(document="schedule.xlsx", section="A", evidence="$1,849,900"),
            tags=["missing_work:calculate"],
        ),
        Entry(
            id="e2",
            type="observation",
            content="FMV is $2,350,000.",
            source=EntrySource(document="schedule.xlsx", section="A", evidence="$2,350,000"),
        ),
    ]

    items = normalize_derived_work_items([{
        "subtype": "missing_operation",
        "reason": "Verify net equity calculation.",
        "parent_entry_ids": ["e1", "e2"],
        "required_inputs": [
            {"label": "fmv", "value": "$2,350,000", "entry_id": "e2"},
            {"label": "debt", "value": "$500,100", "entry_id": "e1"},
        ],
        "expression": "2350000 - 500100",
        "expected_result": "$1,849,900",
    }], entries)

    assert items[0]["status"] == "diagnostic_only"
    assert "calculation_already_present" in items[0]["validation_errors"]


def test_execute_calculation_work_items_creates_verified_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_CALCULATION_DEBT", "1")
    blackboard = Blackboard(
        task_instruction="Compute fee exposure.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Commitment amount is $1,000,000.",
                source=EntrySource(document="lpa.pdf", section="Fees", evidence="$1,000,000"),
            ),
            Entry(
                id="e2",
                type="observation",
                content="Fee rate is 2%.",
                source=EntrySource(document="lpa.pdf", section="Fees", evidence="2%"),
            ),
            Entry(
                id="e3",
                type="gap",
                content="Need to calculate annual fee exposure.",
                tags=["missing_work:calculate"],
            ),
        ],
    )
    items = normalize_derived_work_items([{
        "subtype": "missing_operation",
        "reason": "Compute annual fee from commitment and rate.",
        "parent_entry_ids": ["e1", "e2", "e3"],
        "required_inputs": [
            {"label": "commitment", "value": "$1,000,000", "entry_id": "e1"},
            {"label": "rate", "value": "2%", "entry_id": "e2"},
        ],
        "expression": "1000000 * 0.02",
    }], blackboard.entries)
    caller = FakeCaller(json.dumps({
        "status": "computed",
        "content": "The annual fee is $20,000 based on $1,000,000 times 2%.",
        "expression": "1000000 * 2%",
        "result": "$20,000",
        "source_document": "lpa.pdf",
        "source_section": "Fees",
        "evidence": "$1,000,000 * 2%",
        "confidence": 0.86,
    }))

    report, tokens = execute_calculation_work_items(blackboard, caller, items)

    assert tokens == 30
    assert report["summary"]["executed"] == 1
    created_id = report["items"][0]["created_entry_ids"][0]
    created = blackboard.find_entry(created_id)
    assert created is not None
    assert created.type == "calculation"
    assert created.verified is True
    assert "derived_work:dw_001" in created.tags
    assert created.supports_entries == ["e1", "e2", "e3"]


def test_execute_calculation_work_items_does_not_reuse_existing_ids(tmp_path, monkeypatch):
    reset_id_counters()
    monkeypatch.setenv("SWARM_ENABLE_CALCULATION_DEBT", "1")
    blackboard = Blackboard(
        task_instruction="Compute fee exposure.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Commitment amount is $1,000,000.",
                source=EntrySource(document="lpa.pdf", section="Fees", evidence="$1,000,000"),
            ),
            Entry(
                id="e2",
                type="observation",
                content="Fee rate is 2%.",
                source=EntrySource(document="lpa.pdf", section="Fees", evidence="2%"),
            ),
            Entry(
                id="e3",
                type="gap",
                content="Need to calculate annual fee exposure.",
                tags=["missing_work:calculate"],
            ),
        ],
    )
    items = normalize_derived_work_items([{
        "subtype": "missing_operation",
        "reason": "Compute annual fee from commitment and rate.",
        "parent_entry_ids": ["e1", "e2", "e3"],
        "required_inputs": [
            {"label": "commitment", "value": "$1,000,000", "entry_id": "e1"},
            {"label": "rate", "value": "2%", "entry_id": "e2"},
        ],
        "expression": "1000000 * 0.02",
    }], blackboard.entries)
    caller = FakeCaller(json.dumps({
        "status": "computed",
        "content": "The annual fee is $20,000 based on $1,000,000 times 2%.",
        "expression": "1000000 * 2%",
        "result": "$20,000",
        "source_document": "lpa.pdf",
        "source_section": "Fees",
        "evidence": "$1,000,000 * 2%",
        "confidence": 0.86,
    }))

    report, _tokens = execute_calculation_work_items(blackboard, caller, items)

    assert report["items"][0]["created_entry_ids"] == ["e4"]
    assert blackboard.find_entry("e1").content == "Commitment amount is $1,000,000."


def test_execute_multiple_calculation_work_items_have_unique_ids(tmp_path, monkeypatch):
    reset_id_counters()
    monkeypatch.setenv("SWARM_ENABLE_CALCULATION_DEBT", "1")
    blackboard = Blackboard(
        task_instruction="Compute fee exposures.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Base amount is $1,000,000.",
                source=EntrySource(document="lpa.pdf", section="Fees", evidence="$1,000,000"),
            ),
            Entry(
                id="e2",
                type="observation",
                content="Fee rate is 2%.",
                source=EntrySource(document="lpa.pdf", section="Fees", evidence="2%"),
            ),
            Entry(
                id="e3",
                type="observation",
                content="Reserve rate is 5%.",
                source=EntrySource(document="lpa.pdf", section="Reserve", evidence="5%"),
            ),
            Entry(
                id="e4",
                type="gap",
                content="Need to calculate fee and reserve.",
                tags=["missing_work:calculate"],
            ),
        ],
    )
    items = normalize_derived_work_items([
        {
            "subtype": "missing_operation",
            "reason": "Compute fee.",
            "parent_entry_ids": ["e1", "e2", "e4"],
            "required_inputs": [
                {"label": "base", "value": "$1,000,000", "entry_id": "e1"},
                {"label": "fee_rate", "value": "2%", "entry_id": "e2"},
            ],
            "expression": "1000000 * 0.02",
        },
        {
            "subtype": "missing_operation",
            "reason": "Compute reserve.",
            "parent_entry_ids": ["e1", "e3", "e4"],
            "required_inputs": [
                {"label": "base", "value": "$1,000,000", "entry_id": "e1"},
                {"label": "reserve_rate", "value": "5%", "entry_id": "e3"},
            ],
            "expression": "1000000 * 0.05",
        },
    ], blackboard.entries)
    caller = SequenceCaller([
        json.dumps({
            "status": "computed",
            "content": "The fee amount is $20,000.",
            "expression": "1000000 * 2%",
            "result": "$20,000",
            "source_document": "lpa.pdf",
            "source_section": "Fees",
            "evidence": "$1,000,000 * 2%",
            "confidence": 0.86,
        }),
        json.dumps({
            "status": "computed",
            "content": "The reserve amount is $50,000.",
            "expression": "1000000 * 5%",
            "result": "$50,000",
            "source_document": "lpa.pdf",
            "source_section": "Reserve",
            "evidence": "$1,000,000 * 5%",
            "confidence": 0.86,
        }),
    ])

    report, _tokens = execute_calculation_work_items(blackboard, caller, items)

    created_ids = [
        item["created_entry_ids"][0]
        for item in report["items"]
        if item.get("created_entry_ids")
    ]
    assert created_ids == ["e5", "e6"]
    assert len(set(created_ids)) == 2


def test_run_calculation_debt_detection_executes_when_not_detect_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_CALCULATION_DEBT", "1")
    monkeypatch.delenv("SWARM_CALCULATION_DEBT_DETECT_ONLY", raising=False)
    blackboard = Blackboard(
        task_instruction="Compute fee exposure.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Commitment amount is $1,000,000.",
                source=EntrySource(document="lpa.pdf", section="Fees", evidence="$1,000,000"),
            ),
            Entry(
                id="e2",
                type="observation",
                content="Fee rate is 2%.",
                source=EntrySource(document="lpa.pdf", section="Fees", evidence="2%"),
            ),
            Entry(
                id="e3",
                type="gap",
                content="Need to calculate annual fee exposure.",
                tags=["missing_work:calculate"],
            ),
        ],
    )
    caller = SequenceCaller([
        json.dumps({
            "items": [{
                "subtype": "missing_operation",
                "reason": "Compute annual fee from commitment and rate.",
                "parent_entry_ids": ["e1", "e2", "e3"],
                "required_inputs": [
                    {"label": "commitment", "value": "$1,000,000", "entry_id": "e1"},
                    {"label": "rate", "value": "2%", "entry_id": "e2"},
                ],
                "expression": "1000000 * 0.02",
            }]
        }),
        json.dumps({
            "status": "computed",
            "content": "The annual fee is $20,000 based on $1,000,000 times 2%.",
            "expression": "1000000 * 2%",
            "result": "$20,000",
            "source_document": "lpa.pdf",
            "source_section": "Fees",
            "evidence": "$1,000,000 * 2%",
            "confidence": 0.86,
        }),
    ])

    report, _ = run_calculation_debt_detection(blackboard, {}, caller)

    assert report["mode"] == "execution"
    assert report["summary"]["entries_created"] == 1
    written = json.loads(
        (tmp_path / "swarm" / "derived_work_items.json").read_text(encoding="utf-8")
    )
    assert written["summary"]["executed"] == 1


def test_aggregate_derived_work_reports_writes_summary(tmp_path):
    task_dir = tmp_path / "family" / "task"
    swarm_dir = task_dir / "swarm"
    swarm_dir.mkdir(parents=True)
    (swarm_dir / "derived_work_items.json").write_text(json.dumps({
        "items": [{
            "id": "dw_001",
            "subtype": "missing_operation",
            "status": "executed",
            "execution_eligible": True,
            "parent_entry_ids": ["e1"],
            "created_entry_ids": ["e9"],
        }]
    }), encoding="utf-8")
    (swarm_dir / "commitment_survival_trace.json").write_text(json.dumps({
        "items": [{
            "derived_work_id": "dw_001",
            "obligated": True,
            "found_in_artifact": True,
            "target_files": ["memo.docx"],
        }]
    }), encoding="utf-8")
    (swarm_dir / "prompt_audit.json").write_text(json.dumps({
        "summary": {"forbidden_provenance_hits": 0}
    }), encoding="utf-8")

    summary = aggregate_derived_work_reports(tmp_path)

    assert summary["tasks"] == 1
    assert summary["selected"] == 1
    assert summary["executed"] == 1
    assert summary["artifact_survived_pre_repair"] == 1
    assert (tmp_path / "derived_work_summary.json").exists()
    assert (tmp_path / "derived_work_summary.csv").exists()
