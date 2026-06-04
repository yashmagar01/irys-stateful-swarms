import json

from src.swarm.survival_trace import (
    finalize_artifact_placement_trace,
    finalize_survival_trace,
    write_pending_survival_trace,
)


def test_survival_trace_marks_obligated_artifact_hit(tmp_path):
    derived_report = {
        "items": [{
            "id": "dw_001",
            "status": "executed",
            "created_entry_ids": ["e9"],
            "required_inputs": [
                {"label": "commitment", "value": "$1,000,000"},
                {"label": "rate", "value": "2%"},
            ],
            "expected_result": "$20,000",
            "reason": "Compute annual fee.",
        }]
    }
    must_include = [{
        "entry_id": "e9",
        "summary": "The annual fee is $20,000.",
    }]

    write_pending_survival_trace(str(tmp_path), derived_report, must_include)
    trace = finalize_survival_trace(
        tmp_path,
        {"memo.docx": "The annual fee is $20,000 based on the commitment."},
    )

    item = trace["items"][0]
    assert item["obligated"] is True
    assert item["found_in_artifact"] is True
    assert item["death_mode"] is None
    assert trace["summary"]["artifact_survived"] == 1
    assert (tmp_path / "swarm" / "commitment_survival_trace.json").exists()


def test_survival_trace_classifies_not_obligated_loss(tmp_path):
    derived_report = {
        "items": [{
            "id": "dw_001",
            "status": "executed",
            "created_entry_ids": ["e9"],
            "required_inputs": [],
            "expected_result": "$20,000",
            "reason": "Compute annual fee.",
        }]
    }

    write_pending_survival_trace(str(tmp_path), derived_report, [])
    trace = finalize_survival_trace(tmp_path, {"memo.docx": "No calculation here."})

    assert trace["items"][0]["death_mode"] == "not_obligated"


def test_pending_trace_file_shape(tmp_path):
    write_pending_survival_trace(
        str(tmp_path),
        {"items": [{"id": "dw_001", "status": "diagnostic_only"}]},
        [],
    )
    data = json.loads(
        (tmp_path / "swarm" / "commitment_survival_trace.pending.json")
        .read_text(encoding="utf-8")
    )
    assert data["schema_version"] == 1
    assert data["items"][0]["death_mode"] == "selected_but_not_executed"


def test_survival_trace_includes_debt_sensor_entries(tmp_path):
    debt_report = {
        "items": [{
            "id": "ds_001",
            "type": "severity",
            "subtype": "risk_without_severity",
            "status": "severity_executed",
            "created_entry_ids": ["e17"],
            "reason": "Severity should be assigned to the missing backup vendor risk.",
        }]
    }
    must_include = [{
        "entry_id": "e17",
        "summary": "Missing backup vendor is a high severity continuity risk.",
    }]
    swarm_dir = tmp_path / "swarm"
    swarm_dir.mkdir()
    (swarm_dir / "artifact_commitments.json").write_text(json.dumps({
        "items": [{
            "entry_id": "e17",
            "target_file": "memo.docx",
            "native_form": "memo_statement",
            "verification_terms": ["high severity continuity risk"],
            "summary": "Missing backup vendor is a high severity continuity risk.",
            "source": "artifact_commitment",
        }]
    }), encoding="utf-8")

    write_pending_survival_trace(str(tmp_path), None, must_include, debt_report)
    trace = finalize_survival_trace(
        tmp_path,
        {"memo.docx": "The missing backup vendor is a high severity continuity risk."},
    )

    item = trace["items"][0]
    assert item["commitment_source"] == "debt_sensor"
    assert item["debt_sensor_id"] == "ds_001"
    assert item["debt_type"] == "severity"
    assert item["obligated"] is True
    assert item["found_in_artifact"] is True
    assert item["death_mode"] is None
    assert trace["summary"]["artifact_survived"] == 1


def test_artifact_placement_trace_marks_target_file_hit(tmp_path):
    swarm_dir = tmp_path / "swarm"
    swarm_dir.mkdir()
    (swarm_dir / "artifact_commitments.json").write_text(json.dumps({
        "items": [{
            "entry_id": "e1",
            "evidence_entry_ids": ["e1"],
            "target_file": "model.xlsx",
            "native_form": "workbook_row",
            "artifact_function": "workbook_calculation",
            "satisfaction_conditions": [
                "Place entry e1 in model.xlsx as a workbook row or table line.",
            ],
            "required_source_refs": [{"document": "schedule.xlsx"}],
            "verification_terms": ["$1,849,900"],
            "summary": "Net equity calculation must appear in model.xlsx.",
            "source": "artifact_commitment",
        }]
    }), encoding="utf-8")

    trace = finalize_artifact_placement_trace(
        tmp_path,
        {"model.xlsx": "# Sheet: Required Calculations\nMetric | Result\nNet equity | $1,849,900"},
    )

    item = trace["items"][0]
    assert item["found_in_target_file"] is True
    assert item["native_form_satisfied"] is True
    assert item["death_mode"] is None
    assert item["artifact_function"] == "workbook_calculation"
    assert item["satisfaction_conditions"] == [
        "Place entry e1 in model.xlsx as a workbook row or table line.",
    ]
    assert item["required_source_refs"] == [{"document": "schedule.xlsx"}]
    assert trace["summary"]["found_in_target_file"] == 1
    assert trace["summary"]["native_form_satisfied"] == 1
    assert (swarm_dir / "artifact_placement_trace.json").exists()


def test_artifact_placement_trace_detects_native_form_missing(tmp_path):
    swarm_dir = tmp_path / "swarm"
    swarm_dir.mkdir()
    (swarm_dir / "artifact_commitments.json").write_text(json.dumps({
        "items": [{
            "entry_id": "e1",
            "target_file": "model.xlsx",
            "native_form": "workbook_row",
            "verification_terms": ["$1,849,900"],
            "summary": "Net equity calculation must appear in model.xlsx.",
            "source": "artifact_commitment",
        }]
    }), encoding="utf-8")

    trace = finalize_artifact_placement_trace(
        tmp_path,
        {"model.xlsx": "# Sheet: Required Calculations\nNet equity is $1,849,900."},
    )

    item = trace["items"][0]
    assert item["found_in_target_file"] is True
    assert item["native_form_satisfied"] is False
    assert item["death_mode"] == "native_form_missing"
    assert trace["summary"]["found_in_target_file"] == 1
    assert trace["summary"]["native_form_satisfied"] == 0
    assert trace["summary"]["lost"] == 1
    assert trace["summary"]["death_modes"] == {"native_form_missing": 1}


def test_artifact_placement_trace_marks_untraceable_commitment_ambiguous(tmp_path):
    swarm_dir = tmp_path / "swarm"
    swarm_dir.mkdir()
    (swarm_dir / "artifact_commitments.json").write_text(json.dumps({
        "items": [{
            "entry_id": "e1",
            "target_file": "memo.docx",
            "native_form": "memo_statement",
            "verification_terms": [],
            "summary": "Operational risk should be explained in the memo.",
            "source": "artifact_commitment",
        }]
    }), encoding="utf-8")

    trace = finalize_artifact_placement_trace(
        tmp_path,
        {"memo.docx": "The memo explains operational risk."},
    )

    item = trace["items"][0]
    assert item["placement_traceable"] is False
    assert item["found_in_target_file"] is False
    assert item["death_mode"] == "artifact_ambiguous"
    assert trace["summary"]["traceable"] == 0
    assert trace["summary"]["untraceable"] == 1
    assert trace["summary"]["death_modes"] == {"artifact_ambiguous": 1}


def test_artifact_placement_trace_uses_finding_context_for_memo_phrases(tmp_path):
    swarm_dir = tmp_path / "swarm"
    swarm_dir.mkdir()
    (swarm_dir / "artifact_commitments.json").write_text(json.dumps({
        "items": [{
            "entry_id": "e22",
            "target_file": "ops_risk_memo.docx",
            "native_form": "memo_statement",
            "verification_terms": [
                "deployment rollback automation",
                "extended downtime",
            ],
            "summary": (
                "Represent source-backed entry e22 in ops_risk_memo.docx as "
                "memo_statement: Operational risk is concentrated in the deployment "
                "pipeline because deployment rollback automation is missing and "
                "failed pushes create extended downtime."
            ),
            "source": "artifact_commitment",
        }]
    }), encoding="utf-8")

    trace = finalize_artifact_placement_trace(
        tmp_path,
        {
            "ops_risk_memo.docx": (
                "Deployment rollback automation remains manual, so deployment "
                "failures can produce extended downtime before operators recover."
            )
        },
    )

    item = trace["items"][0]
    assert item["placement_traceable"] is True
    assert item["found_in_target_file"] is True
    assert item["native_form_satisfied"] is True
    assert item["death_mode"] is None


def test_artifact_placement_trace_classifies_wrong_file(tmp_path):
    swarm_dir = tmp_path / "swarm"
    swarm_dir.mkdir()
    (swarm_dir / "artifact_commitments.json").write_text(json.dumps({
        "items": [{
            "entry_id": "e1",
            "target_file": "model.xlsx",
            "native_form": "workbook_row",
            "verification_terms": ["$1,849,900"],
            "summary": "Net equity calculation must appear in model.xlsx.",
            "source": "artifact_commitment",
        }]
    }), encoding="utf-8")

    trace = finalize_artifact_placement_trace(
        tmp_path,
        {
            "memo.docx": "Net equity is $1,849,900.",
            "model.xlsx": "# Sheet: Required Calculations\n",
        },
    )

    assert trace["items"][0]["found_elsewhere"] is True
    assert trace["items"][0]["death_mode"] == "wrong_file"
    assert trace["summary"]["death_modes"] == {"wrong_file": 1}


def test_artifact_placement_trace_classifies_wrong_native_form(tmp_path):
    swarm_dir = tmp_path / "swarm"
    swarm_dir.mkdir()
    (swarm_dir / "artifact_commitments.json").write_text(json.dumps({
        "items": [{
            "entry_id": "e1",
            "target_file": "memo.docx",
            "native_form": "workbook_row",
            "verification_terms": ["$1,849,900"],
            "summary": "Net equity calculation must appear as a workbook row.",
            "source": "artifact_commitment",
        }]
    }), encoding="utf-8")

    trace = finalize_artifact_placement_trace(
        tmp_path,
        {"memo.docx": "Net equity is $1,849,900."},
    )

    assert trace["items"][0]["death_mode"] == "wrong_format"


def test_finalize_survival_trace_also_finalizes_artifact_placements(tmp_path):
    swarm_dir = tmp_path / "swarm"
    swarm_dir.mkdir()
    (swarm_dir / "artifact_commitments.json").write_text(json.dumps({
        "items": [{
            "entry_id": "e1",
            "target_file": "memo.docx",
            "native_form": "memo_statement",
            "verification_terms": ["Section 12.4"],
            "summary": "Termination issue must cite Section 12.4.",
            "source": "artifact_commitment",
        }]
    }), encoding="utf-8")

    trace = finalize_survival_trace(
        tmp_path,
        {"memo.docx": "The termination issue is grounded in Section 12.4."},
    )

    assert trace == {}
    placement = json.loads(
        (swarm_dir / "artifact_placement_trace.json").read_text(encoding="utf-8")
    )
    assert placement["summary"]["found_in_target_file"] == 1
