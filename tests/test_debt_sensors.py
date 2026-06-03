import json

from src.swarm.blackboard import Blackboard
from src.swarm.debt_sensors import (
    coordinate_debt_sensor_items,
    debt_sensor_items_to_gap_entries,
    execute_authority_debt_items,
    execute_relation_debt_items,
    execute_severity_debt_items,
    execute_source_object_debt_items,
    normalize_debt_sensor_items,
    run_debt_sensors,
)
from src.swarm.models import DocumentStatus, Entry, EntrySource, ModelResult
from src.swarm.section_index import build_section_index


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


def test_coordinate_debt_sensor_items_defers_unselected_actionable_items(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_LENS_COORDINATOR", "1")
    monkeypatch.setenv("SWARM_LENS_COORDINATOR_MAX_ITEMS", "2")
    blackboard = Blackboard(task_instruction="Review repo architecture.", output_dir=str(tmp_path))
    items = normalize_debt_sensor_items([
        {
            "type": "relation",
            "subtype": "conflict",
            "reason": "Compare source files for a routing conflict.",
            "parent_entry_ids": ["e1", "e2"],
            "confidence": 0.91,
        },
        {
            "type": "severity",
            "subtype": "risk_without_severity",
            "reason": "Assign severity to the unsupported security claim.",
            "parent_entry_ids": ["e3"],
            "confidence": 0.9,
        },
        {
            "type": "authority",
            "subtype": "evidence_anchor_needed",
            "reason": "Anchor the configuration statement to source.",
            "parent_entry_ids": ["e4"],
            "confidence": 0.89,
        },
    ])
    caller = SequenceCaller([json.dumps({
        "selected_item_ids": ["ds_002", "ds_003"],
        "decisions": [
            {"id": "ds_001", "decision": "defer", "reason": "Lower value duplicate."},
            {"id": "ds_002", "decision": "execute", "reason": "Material risk."},
            {"id": "ds_003", "decision": "execute", "reason": "Needed source anchor."},
        ],
    })])

    updated, report, tokens = coordinate_debt_sensor_items(blackboard, {}, caller, items)

    assert tokens == 30
    assert report["mode"] == "prioritized"
    assert report["selected_item_ids"] == ["ds_002", "ds_003"]
    assert report["deferred_item_ids"] == ["ds_001"]
    assert updated[0]["status"] == "deferred_by_coordinator"
    assert updated[1]["status"] == "actionable_gap"
    assert updated[2]["status"] == "actionable_gap"
    assert "Coordinate debt lenses" in caller.prompts[0]


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


def test_run_debt_sensors_uses_lens_coordinator_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_RELATION_DEBT", "1")
    monkeypatch.setenv("SWARM_RELATION_DEBT_EXECUTE", "1")
    monkeypatch.setenv("SWARM_ENABLE_SEVERITY_DEBT", "1")
    monkeypatch.setenv("SWARM_SEVERITY_DEBT_EXECUTE", "1")
    monkeypatch.setenv("SWARM_ENABLE_LENS_COORDINATOR", "1")
    monkeypatch.setenv("SWARM_LENS_COORDINATOR_MAX_ITEMS", "2")
    monkeypatch.setenv("SWARM_PROMPT_AUDIT", "1")
    blackboard = Blackboard(
        task_instruction="Review repo architecture risks.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="README says src is a companion/reference helper, not primary runtime.",
                source=EntrySource(document="README.md", section="Scope", evidence="not the primary runtime surface"),
            ),
            Entry(
                id="e2",
                type="observation",
                content="runtime.py routes prompts through PortRuntime.",
                source=EntrySource(document="runtime.py", section="PortRuntime", evidence="def route_prompt"),
            ),
            Entry(
                id="e3",
                type="analysis",
                content="The memo overstates the Python runtime as canonical.",
                source=EntrySource(document="README.md", section="Scope", evidence="rust/ is canonical"),
            ),
        ],
    )
    caller = SequenceCaller([
        json.dumps({
            "items": [
                {
                    "type": "relation",
                    "subtype": "reconciliation",
                    "reason": "Reconcile README source-of-truth statement with runtime.py routing.",
                    "parent_entry_ids": ["e1", "e2"],
                    "confidence": 0.91,
                },
                {
                    "type": "relation",
                    "subtype": "conflict",
                    "reason": "Compare two lower-priority routing details.",
                    "parent_entry_ids": ["e1", "e2"],
                    "confidence": 0.88,
                },
            ]
        }),
        json.dumps({
            "items": [
                {
                    "type": "severity",
                    "subtype": "risk_without_severity",
                    "reason": "Assign severity to the canonical-runtime overstatement.",
                    "parent_entry_ids": ["e3"],
                    "confidence": 0.92,
                },
                {
                    "type": "severity",
                    "subtype": "recommendation_needed",
                    "reason": "Recommend a broad documentation cleanup.",
                    "parent_entry_ids": ["e3"],
                    "confidence": 0.86,
                },
            ]
        }),
        json.dumps({
            "selected_item_ids": ["ds_001", "ds_003"],
            "decisions": [
                {"id": "ds_001", "decision": "execute", "reason": "Core source-of-truth relation."},
                {"id": "ds_002", "decision": "defer", "reason": "Lower priority duplicate."},
                {"id": "ds_003", "decision": "execute", "reason": "High-impact overstatement."},
                {"id": "ds_004", "decision": "defer", "reason": "Less concrete."},
            ],
        }),
        json.dumps({
            "status": "computed",
            "content": "README limits src to companion/reference helpers while runtime.py shows only a Python helper routing surface.",
            "relation_type": "reconciliation",
            "evidence": "not the primary runtime surface; def route_prompt",
            "confidence": 0.9,
        }),
        json.dumps({
            "status": "computed",
            "content": "Treating the Python helper as canonical is a high severity source-of-truth risk.",
            "severity": "high",
            "recommendation": "Flag the canonical/runtime distinction before making architecture recommendations.",
            "evidence": "rust/ is canonical",
            "confidence": 0.9,
        }),
    ])

    report, tokens = run_debt_sensors(blackboard, {}, caller)

    assert tokens == 150
    assert report["lens_coordinator"]["selected_item_ids"] == ["ds_001", "ds_003"]
    assert report["lens_coordinator"]["deferred"] == 2
    assert report["summary"]["status_counts"]["deferred_by_coordinator"] == 2
    assert len(report["created_relation_entry_ids"]) == 1
    assert len(report["created_severity_entry_ids"]) == 1
    assert report["created_gap_entry_ids"] == []
    assert len(blackboard.entries) == 5
    assert any("Coordinate debt lenses" in prompt for prompt in caller.prompts)
    written = json.loads((tmp_path / "swarm" / "debt_sensors.json").read_text())
    assert written["lens_coordinator"]["selected_actionable"] == 2
    audit = json.loads((tmp_path / "swarm" / "prompt_audit.json").read_text())
    assert audit["summary"]["stages"]["lens_coordinator"] == 1


def test_execute_source_object_debt_items_rereads_target_document(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SOURCE_OBJECT_DEBT_EXECUTE", "1")
    text = "# Schedule A\nRow 1: Alpha LLC owes $10.\nRow 2: Beta LLC owes $20."
    blackboard = Blackboard(
        task_instruction="Extract all schedule rows.",
        output_dir=str(tmp_path),
        documents=[
            DocumentStatus(
                id="d1",
                name="schedule.md",
                headings=["Schedule A"],
                sections_unread=["Schedule A"],
                section_index=build_section_index(text),
                text=text,
            )
        ],
    )
    items = normalize_debt_sensor_items([{
        "type": "source_object",
        "subtype": "missing_population",
        "reason": "Need all rows from Schedule A.",
        "target_documents": ["schedule.md"],
        "confidence": 0.86,
    }])
    caller = SequenceCaller([json.dumps({
        "status": "found",
        "findings": [
            {
                "type": "observation",
                "content": "Schedule A row 1 states Alpha LLC owes $10.",
                "source_document": "schedule.md",
                "source_section": "Schedule A",
                "evidence": "Row 1: Alpha LLC owes $10.",
                "confidence": 0.92,
            },
            {
                "type": "observation",
                "content": "Schedule A row 2 states Beta LLC owes $20.",
                "source_document": "schedule.md",
                "source_section": "Schedule A",
                "evidence": "Row 2: Beta LLC owes $20.",
                "confidence": 0.92,
            },
        ],
    })])

    report, tokens = execute_source_object_debt_items(blackboard, caller, items)

    assert tokens == 30
    assert report["summary"]["entries_created"] == 2
    assert report["items"][0]["status"] == "source_object_executed"
    assert report["items"][0]["created_entry_ids"] == [
        report["entries"][0].id,
        report["entries"][1].id,
    ]
    assert report["entries"][0].source.document == "schedule.md"
    assert "SOURCE EXCERPTS" in caller.prompts[0]
    assert "Row 2: Beta LLC owes $20." in caller.prompts[0]


def test_execute_source_object_debt_items_requires_source_text():
    blackboard = Blackboard(task_instruction="Extract all schedule rows.")
    items = normalize_debt_sensor_items([{
        "type": "source_object",
        "subtype": "missing_population",
        "reason": "Need all rows from Schedule A.",
        "target_documents": ["missing.md"],
        "confidence": 0.86,
    }])
    caller = SequenceCaller(['{"status":"found","findings":[]}'])

    report, tokens = execute_source_object_debt_items(blackboard, caller, items)

    assert tokens == 0
    assert report["entries"] == []
    assert report["items"][0]["status"] == "diagnostic_only"
    assert report["items"][0]["execution_error"] == "no_target_source_document"
    assert caller.prompts == []


def test_run_debt_sensors_executes_source_object_without_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_SOURCE_OBJECT_DEBT", "1")
    monkeypatch.setenv("SWARM_SOURCE_OBJECT_DEBT_EXECUTE", "1")
    monkeypatch.setenv("SWARM_PROMPT_AUDIT", "1")
    text = "# Schedule A\nRow 1: Alpha LLC owes $10."
    blackboard = Blackboard(
        task_instruction="Extract all schedule rows.",
        output_dir=str(tmp_path),
        documents=[
            DocumentStatus(
                id="d1",
                name="schedule.md",
                headings=["Schedule A"],
                sections_unread=["Schedule A"],
                section_index=build_section_index(text),
                text=text,
            )
        ],
    )
    caller = SequenceCaller([
        json.dumps({
            "items": [{
                "type": "source_object",
                "subtype": "missing_population",
                "reason": "Need all rows from Schedule A.",
                "target_documents": ["schedule.md"],
                "confidence": 0.86,
            }]
        }),
        json.dumps({
            "status": "found",
            "findings": [{
                "type": "observation",
                "content": "Schedule A row 1 states Alpha LLC owes $10.",
                "source_document": "schedule.md",
                "source_section": "Schedule A",
                "evidence": "Row 1: Alpha LLC owes $10.",
                "confidence": 0.92,
            }],
        }),
    ])

    report, tokens = run_debt_sensors(blackboard, {}, caller)

    assert tokens == 60
    assert report["mode"] == "execute_source_object_debt"
    assert report["created_gap_entry_ids"] == []
    assert len(report["created_source_object_entry_ids"]) == 1
    assert len(blackboard.entries) == 1
    created = blackboard.find_entry(report["created_source_object_entry_ids"][0])
    assert created is not None
    assert created.type == "observation"
    assert "debt_type:source_object" in created.tags
    written = json.loads((tmp_path / "swarm" / "debt_sensors.json").read_text())
    assert written["source_object_execution_summary"]["entries_created"] == 1


def test_execute_severity_debt_items_creates_analysis_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SEVERITY_DEBT_EXECUTE", "1")
    blackboard = Blackboard(
        task_instruction="Review operational risks.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="The contract omits any backup vendor for critical hosting services.",
                source=EntrySource(document="msa.docx", section="2", evidence="No backup vendor"),
            ),
        ],
    )
    items = normalize_debt_sensor_items([{
        "type": "severity",
        "subtype": "risk_without_severity",
        "reason": "Assign severity and action for missing backup vendor.",
        "parent_entry_ids": ["e1"],
        "confidence": 0.88,
    }])
    caller = SequenceCaller([json.dumps({
        "status": "computed",
        "content": "The missing backup vendor is a high severity continuity risk because critical hosting has no fallback.",
        "severity": "high",
        "recommendation": "Require a named backup vendor or disaster-recovery service level.",
        "evidence": "No backup vendor",
        "confidence": 0.87,
    })])

    report, tokens = execute_severity_debt_items(blackboard, caller, items)

    assert tokens == 30
    assert report["summary"]["entries_created"] == 1
    assert report["items"][0]["status"] == "severity_executed"
    entry = report["entries"][0]
    assert entry.type == "analysis"
    assert entry.supports_entries == ["e1"]
    assert "debt_type:severity" in entry.tags
    assert "severity:high" in entry.tags
    assert "Recommended action" in entry.content


def test_execute_severity_debt_requires_source_backed_parent():
    blackboard = Blackboard(
        task_instruction="Review operational risks.",
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="Unsupported risk statement.",
            ),
        ],
    )
    items = normalize_debt_sensor_items([{
        "type": "severity",
        "subtype": "risk_without_severity",
        "reason": "Assign severity to unsupported risk.",
        "parent_entry_ids": ["e1"],
        "confidence": 0.88,
    }])
    caller = SequenceCaller(['{"status":"computed","content":"Should not run."}'])

    report, tokens = execute_severity_debt_items(blackboard, caller, items)

    assert tokens == 0
    assert report["entries"] == []
    assert report["items"][0]["status"] == "diagnostic_only"
    assert report["items"][0]["execution_error"] == "severity_requires_source_backed_parent"
    assert caller.prompts == []


def test_run_debt_sensors_executes_severity_without_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_SEVERITY_DEBT", "1")
    monkeypatch.setenv("SWARM_SEVERITY_DEBT_EXECUTE", "1")
    monkeypatch.setenv("SWARM_PROMPT_AUDIT", "1")
    blackboard = Blackboard(
        task_instruction="Review operational risks.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="The contract omits any backup vendor for critical hosting services.",
                source=EntrySource(document="msa.docx", section="2", evidence="No backup vendor"),
            ),
        ],
    )
    caller = SequenceCaller([
        json.dumps({
            "items": [{
                "type": "severity",
                "subtype": "risk_without_severity",
                "reason": "Assign severity and action for missing backup vendor.",
                "parent_entry_ids": ["e1"],
                "confidence": 0.88,
            }]
        }),
        json.dumps({
            "status": "computed",
            "content": "The missing backup vendor is a high severity continuity risk because critical hosting has no fallback.",
            "severity": "high",
            "recommendation": "Require a named backup vendor or disaster-recovery service level.",
            "evidence": "No backup vendor",
            "confidence": 0.87,
        }),
    ])

    report, tokens = run_debt_sensors(blackboard, {}, caller)

    assert tokens == 60
    assert report["mode"] == "execute_severity_debt"
    assert report["created_gap_entry_ids"] == []
    assert len(report["created_severity_entry_ids"]) == 1
    assert len(blackboard.entries) == 2
    created = blackboard.find_entry(report["created_severity_entry_ids"][0])
    assert created is not None
    assert "severity:high" in created.tags
    written = json.loads((tmp_path / "swarm" / "debt_sensors.json").read_text())
    assert written["severity_execution_summary"]["entries_created"] == 1


def test_execute_authority_debt_items_creates_analysis_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_AUTHORITY_DEBT_EXECUTE", "1")
    blackboard = Blackboard(
        task_instruction="Review source support.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="The contract creates a termination-for-convenience issue.",
                source=EntrySource(
                    document="msa.docx",
                    section="Section 12.4",
                    evidence="Customer may terminate for convenience on 30 days notice.",
                ),
            ),
        ],
    )
    items = normalize_debt_sensor_items([{
        "type": "authority",
        "subtype": "clause_reference_needed",
        "reason": "Tie the termination issue to the exact clause.",
        "parent_entry_ids": ["e1"],
        "confidence": 0.9,
    }])
    caller = SequenceCaller([json.dumps({
        "status": "computed",
        "content": "The termination issue is grounded in Section 12.4 of the MSA.",
        "authority_label": "MSA Section 12.4",
        "citation": "msa.docx Section 12.4",
        "evidence": "Customer may terminate for convenience on 30 days notice.",
        "confidence": 0.9,
    })])

    report, tokens = execute_authority_debt_items(blackboard, caller, items)

    assert tokens == 30
    assert report["summary"]["entries_created"] == 1
    assert report["items"][0]["status"] == "authority_executed"
    entry = report["entries"][0]
    assert entry.type == "analysis"
    assert entry.supports_entries == ["e1"]
    assert "debt_type:authority" in entry.tags
    assert "missing_work:provide_authority" in entry.tags
    assert "Authority/evidence anchor: MSA Section 12.4 - msa.docx Section 12.4" in entry.content


def test_execute_authority_debt_requires_source_evidence():
    blackboard = Blackboard(
        task_instruction="Review source support.",
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="The contract creates a termination issue.",
                source=EntrySource(document="msa.docx", section="Section 12.4", evidence=""),
            ),
        ],
    )
    items = normalize_debt_sensor_items([{
        "type": "authority",
        "subtype": "evidence_anchor_needed",
        "reason": "Tie the conclusion to source evidence.",
        "parent_entry_ids": ["e1"],
        "confidence": 0.9,
    }])
    caller = SequenceCaller(['{"status":"computed","content":"Should not run."}'])

    report, tokens = execute_authority_debt_items(blackboard, caller, items)

    assert tokens == 0
    assert report["entries"] == []
    assert report["items"][0]["status"] == "diagnostic_only"
    assert report["items"][0]["execution_error"] == "authority_requires_source_backed_parent"
    assert caller.prompts == []


def test_run_debt_sensors_executes_authority_without_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_AUTHORITY_DEBT", "1")
    monkeypatch.setenv("SWARM_AUTHORITY_DEBT_EXECUTE", "1")
    monkeypatch.setenv("SWARM_PROMPT_AUDIT", "1")
    blackboard = Blackboard(
        task_instruction="Review source support.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="The contract creates a termination-for-convenience issue.",
                source=EntrySource(
                    document="msa.docx",
                    section="Section 12.4",
                    evidence="Customer may terminate for convenience on 30 days notice.",
                ),
            ),
        ],
    )
    caller = SequenceCaller([
        json.dumps({
            "items": [{
                "type": "authority",
                "subtype": "clause_reference_needed",
                "reason": "Tie the termination issue to the exact clause.",
                "parent_entry_ids": ["e1"],
                "confidence": 0.9,
            }]
        }),
        json.dumps({
            "status": "computed",
            "content": "The termination issue is grounded in Section 12.4 of the MSA.",
            "authority_label": "MSA Section 12.4",
            "citation": "msa.docx Section 12.4",
            "evidence": "Customer may terminate for convenience on 30 days notice.",
            "confidence": 0.9,
        }),
    ])

    report, tokens = run_debt_sensors(blackboard, {}, caller)

    assert tokens == 60
    assert report["mode"] == "execute_authority_debt"
    assert report["created_gap_entry_ids"] == []
    assert len(report["created_authority_entry_ids"]) == 1
    assert len(blackboard.entries) == 2
    created = blackboard.find_entry(report["created_authority_entry_ids"][0])
    assert created is not None
    assert "debt_type:authority" in created.tags
    written = json.loads((tmp_path / "swarm" / "debt_sensors.json").read_text())
    assert written["authority_execution_summary"]["entries_created"] == 1
