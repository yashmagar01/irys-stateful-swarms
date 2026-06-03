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
    assert commitments[0]["source"] == "artifact_commitment"
    report = json.loads(
        (tmp_path / "swarm" / "artifact_commitments.json").read_text(encoding="utf-8")
    )
    assert report["summary"]["selected"] == 1


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
