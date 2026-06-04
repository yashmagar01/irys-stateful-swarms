import json

from src.swarm.artifact_commitments import build_artifact_commitments
from src.swarm.blackboard import Blackboard
from src.swarm.models import Entry, EntrySource
from src.swarm.synthesis import _apply_target_file_pins


def test_build_artifact_commitments_targets_workbook_for_calculations(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_ARTIFACT_COMMITMENTS", "1")
    blackboard = Blackboard(
        task_instruction="Prepare memo and model.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="calculation",
                content="Net equity is $1,849,900 ($2,350,000 - $500,100).",
                source=EntrySource(document="schedule.xlsx", section="A", evidence="$1,849,900"),
                tags=["plan_coverage_repair", "missing_work:calculate", "materiality:high"],
                confidence=0.9,
            )
        ],
    )

    commitments = build_artifact_commitments(
        blackboard,
        {"memo": "analysis_memo.docx", "model": "asset_workbook.xlsx"},
    )

    assert len(commitments) == 1
    assert commitments[0]["target_file"] == "asset_workbook.xlsx"
    assert commitments[0]["native_form"] == "workbook_row"
    assert commitments[0]["artifact_function"] == "workbook_calculation"
    assert commitments[0]["evidence_entry_ids"] == ["e1"]
    assert commitments[0]["source_refs"] == [{
        "document": "schedule.xlsx",
        "section": "A",
        "evidence": "$1,849,900",
    }]
    assert any(
        "workbook row or table line" in condition
        for condition in commitments[0]["satisfaction_conditions"]
    )
    assert commitments[0]["source"] == "artifact_commitment"
    report = json.loads(
        (tmp_path / "swarm" / "artifact_commitments.json").read_text(encoding="utf-8")
    )
    assert report["summary"]["selected"] == 1
    assert report["summary"]["artifact_functions"] == {"workbook_calculation": 1}
    assert report["summary"]["satisfaction_conditions"] >= 4


def test_build_artifact_commitments_ignores_unsourced_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_ARTIFACT_COMMITMENTS", "1")
    blackboard = Blackboard(
        task_instruction="Prepare memo.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="calculation",
                content="Net equity is $1,849,900.",
                tags=["missing_work:calculate"],
            )
        ],
    )

    assert build_artifact_commitments(blackboard, {"memo": "memo.docx"}) == []


def test_memo_commitment_uses_concise_narrative_verification_terms(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_ARTIFACT_COMMITMENTS", "1")
    blackboard = Blackboard(
        task_instruction="Prepare an operational risk memo.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e22",
                type="analysis",
                content=(
                    "Operational risk is highly concentrated in the deployment pipeline. "
                    "Deployment rollback automation is missing, creating extended downtime "
                    "without automated safety nets."
                ),
                source=EntrySource(
                    document="ops_report.md",
                    section="Deployment controls",
                    evidence=(
                        "Rollback automation remains manual and recovery from failed pushes "
                        "depends on manual coordination, creating extended downtime without "
                        "automated safety nets."
                    ),
                ),
                tags=["materiality:high", "missing_work:analyze"],
                confidence=0.91,
            )
        ],
    )

    commitments = build_artifact_commitments(
        blackboard,
        {"memo": "ops_risk_memo.docx", "model": "ops_risk_workbook.xlsx"},
    )

    terms = commitments[0]["verification_terms"]
    assert "deployment rollback automation" in terms
    assert "extended downtime" in terms
    assert all(len(term.split()) <= 4 for term in terms if not term.startswith("$"))
    assert all(len(term) <= 90 for term in terms)


def test_debt_sensor_severity_commitment_targets_memo(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_ARTIFACT_COMMITMENTS", "1")
    blackboard = Blackboard(
        task_instruction="Prepare a memo and tracker.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e7",
                type="analysis",
                content=(
                    "The missed 10-day cure deadline is high severity because it "
                    "creates immediate termination exposure. Recommended action: "
                    "send a reservation-of-rights notice and escalate to counsel."
                ),
                source=EntrySource(
                    document="default_notice.pdf",
                    section="Cure rights",
                    evidence="10-day cure deadline",
                ),
                tags=[
                    "debt_sensor",
                    "debt_type:severity",
                    "debt_subtype:risk_without_severity",
                    "severity:high",
                    "lifecycle:transformed",
                    "source_grounded:true",
                ],
                confidence=0.82,
            )
        ],
    )

    commitments = build_artifact_commitments(
        blackboard,
        {"memo": "risk_memo.docx", "tracker": "risk_tracker.xlsx"},
    )

    assert len(commitments) == 1
    item = commitments[0]
    assert item["entry_id"] == "e7"
    assert item["target_file"] == "risk_memo.docx"
    assert item["native_form"] == "memo_statement"
    assert item["artifact_function"] == "memo_risk_recommendation"
    assert item["importance"] == "critical"
    assert item["section"] == "Risk and Recommendations"
    assert any(
        "severity or priority" in condition
        for condition in item["satisfaction_conditions"]
    )


def test_debt_sensor_source_object_commitment_targets_workbook(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_ARTIFACT_COMMITMENTS", "1")
    blackboard = Blackboard(
        task_instruction="Prepare a memo and source tracker.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e8",
                type="observation",
                content=(
                    "Schedule 4.2 identifies three excluded customer contracts "
                    "that must be tracked separately from assumed contracts."
                ),
                source=EntrySource(
                    document="purchase_agreement.pdf",
                    section="Schedule 4.2",
                    evidence="three excluded customer contracts",
                ),
                tags=[
                    "debt_sensor",
                    "debt_type:source_object",
                    "debt_subtype:missing_population",
                    "lifecycle:discovered",
                    "source_grounded:true",
                ],
                confidence=0.77,
            )
        ],
    )

    commitments = build_artifact_commitments(
        blackboard,
        {"memo": "coverage_memo.docx", "tracker": "source_tracker.xlsx"},
    )

    assert len(commitments) == 1
    item = commitments[0]
    assert item["entry_id"] == "e8"
    assert item["target_file"] == "source_tracker.xlsx"
    assert item["native_form"] == "workbook_row"
    assert item["artifact_function"] == "workbook_source_object"
    assert item["section"] == "Sheet: Source Coverage"
    assert any(
        "source/object coverage row" in condition
        for condition in item["satisfaction_conditions"]
    )
    assert "three excluded customer contracts" in item["verification_terms"]


def test_apply_target_file_pins_moves_artifact_commitment_to_target_file():
    item_pool = [
        (1, {"summary": "General memo point", "entry_id": "e1"}),
        (
            2,
            {
                "summary": "Workbook calculation",
                "entry_id": "e2",
                "target_file": "model.xlsx",
                "source": "artifact_commitment",
            },
        ),
    ]
    plans = {
        "memo.docx": {"numbers": [1, 2]},
        "model.xlsx": {"numbers": []},
    }

    _apply_target_file_pins(plans, item_pool, ["memo.docx", "model.xlsx"])

    assert plans["memo.docx"]["numbers"] == [1]
    assert plans["model.xlsx"]["numbers"] == [2]
