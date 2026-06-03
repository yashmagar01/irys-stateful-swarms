import json

from src.swarm.survival_trace import (
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
