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
        {"model.xlsx": "# Sheet: Required Calculations\nNet equity is $1,849,900."},
    )

    item = trace["items"][0]
    assert item["found_in_target_file"] is True
    assert item["death_mode"] is None
    assert item["artifact_function"] == "workbook_calculation"
    assert item["satisfaction_conditions"] == [
        "Place entry e1 in model.xlsx as a workbook row or table line.",
    ]
    assert item["required_source_refs"] == [{"document": "schedule.xlsx"}]
    assert trace["summary"]["found_in_target_file"] == 1
    assert (swarm_dir / "artifact_placement_trace.json").exists()


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
