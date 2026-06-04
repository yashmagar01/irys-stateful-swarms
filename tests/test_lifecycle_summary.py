import json

from src.swarm.lifecycle_summary import aggregate_lifecycle_reports


def test_aggregate_lifecycle_reports_writes_run_summary(tmp_path):
    task_dir = tmp_path / "repo_alpha" / "hard_question"
    swarm_dir = task_dir / "swarm"
    swarm_dir.mkdir(parents=True)

    (swarm_dir / "debt_sensors.json").write_text(json.dumps({
        "mode": "execute_relation_and_severity_debt",
        "items": [
            {
                "id": "ds_001",
                "type": "relation",
                "status": "relation_executed",
                "created_entry_ids": ["e10"],
            },
            {
                "id": "ds_002",
                "type": "severity",
                "status": "actionable_gap",
            },
        ],
        "summary": {
            "selected": 2,
            "actionable": 1,
            "type_counts": {"relation": 1, "severity": 1},
            "status_counts": {"relation_executed": 1, "actionable_gap": 1},
        },
        "relation_execution_summary": {"entries_created": 1},
        "severity_execution_summary": {"entries_created": 0},
        "created_gap_entry_ids": ["e11"],
        "lens_coordinator": {
            "selected_actionable": 1,
            "deferred": 1,
        },
    }), encoding="utf-8")
    (swarm_dir / "derived_work_items.json").write_text(json.dumps({
        "items": [{
            "id": "dw_001",
            "status": "executed",
            "execution_eligible": True,
            "created_entry_ids": ["e12"],
        }]
    }), encoding="utf-8")
    (swarm_dir / "commitment_survival_trace.json").write_text(json.dumps({
        "items": [{
            "derived_work_id": "dw_001",
            "obligated": True,
            "found_in_artifact": False,
            "death_mode": "artifact_missing",
        }]
    }), encoding="utf-8")
    (swarm_dir / "artifact_placement_trace.json").write_text(json.dumps({
        "summary": {
            "selected": 2,
            "targeted": 2,
            "traceable": 2,
            "found_in_target_file": 1,
            "native_form_satisfied": 1,
            "found_elsewhere": 1,
            "lost": 1,
            "death_modes": {"wrong_file": 1},
            "native_forms": {"workbook_row": 1, "drafting_clause": 1},
        }
    }), encoding="utf-8")
    (swarm_dir / "source_custody.json").write_text(json.dumps({
        "audits": [
            {"stage": "post_state_repair"},
            {"stage": "post_debt_sensors"},
        ],
        "summary": {
            "entries_quarantined": 3,
            "invalid_documents": {"Incident Report Q3": 2},
            "reasons": {
                "invalid_source_document": 2,
                "depends_on_invalid_source_state": 1,
            },
        },
    }), encoding="utf-8")
    (swarm_dir / "prompt_audit.json").write_text(json.dumps({
        "summary": {
            "records": 3,
            "forbidden_provenance_hits": 0,
            "forbidden_text_hits": 1,
            "stages": {"debt_sensor": 2, "blackboard_maintenance": 1},
        }
    }), encoding="utf-8")
    (swarm_dir / "blackboard_maintenance.json").write_text(json.dumps({
        "mode": "consolidate_only",
        "candidate_entry_count": 20,
        "created_entry_ids": ["e13", "e14"],
        "summary": {
            "consolidations_selected": 2,
            "entries_created": 2,
            "entries_superseded": 0,
            "fallback_used": True,
            "fallback_cluster_count": 5,
            "state_quality": {
                "before": {
                    "active_total": 20,
                    "state_mix_score": 25.0,
                    "reasoning_density": 0.3,
                    "gap_density": 0.05,
                },
                "after": {
                    "active_total": 22,
                    "state_mix_score": 31.0,
                    "reasoning_density": 0.36,
                    "gap_density": 0.045,
                },
                "delta": {
                    "active_total": 2,
                    "reasoning_density": 0.06,
                    "observation_density": -0.04,
                    "gap_density": -0.005,
                    "state_mix_score": 6.0,
                },
                "compaction_ratio": 0.0,
                "creation_ratio": 0.1,
            },
        },
    }), encoding="utf-8")
    (swarm_dir / "source_claim_verification.json").write_text(json.dumps({
        "mode": "audit_only",
        "files": [{
            "filename": "memo.docx",
            "fallback_used": True,
            "fallback_candidate_count": 12,
            "evidence_entry_count": 31,
        }],
        "summary": {
            "files_checked": 1,
            "claims_checked": 4,
            "risky_claims": 2,
            "status_counts": {"supported": 2, "unsupported": 1, "overstated": 1},
            "severity_counts": {"high": 2, "medium": 2},
        },
    }), encoding="utf-8")

    summary = aggregate_lifecycle_reports(tmp_path)

    assert summary["tasks"] == 1
    assert summary["reports"]["debt_sensors"]["selected"] == 2
    assert summary["reports"]["debt_sensors"]["actionable"] == 1
    assert summary["reports"]["debt_sensors"]["execution_entries_created"]["relation"] == 1
    assert summary["reports"]["debt_sensors"]["unresolved_actionable"] == 1
    assert summary["reports"]["debt_sensors"]["coordinator_selected"] == 1
    assert summary["reports"]["debt_sensors"]["coordinator_deferred"] == 1
    assert summary["reports"]["derived_work"]["lost"] == 1
    assert summary["reports"]["derived_work"]["death_modes"]["artifact_missing"] == 1
    assert summary["reports"]["artifact_placement"]["found_in_target_file"] == 1
    assert summary["reports"]["artifact_placement"]["traceable"] == 2
    assert summary["reports"]["artifact_placement"]["untraceable"] == 0
    assert summary["reports"]["artifact_placement"]["native_form_satisfied"] == 1
    assert summary["reports"]["artifact_placement"]["lost"] == 1
    assert summary["reports"]["source_custody"]["audits"] == 2
    assert summary["reports"]["source_custody"]["entries_quarantined"] == 3
    assert summary["reports"]["source_custody"]["invalid_documents"] == {"Incident Report Q3": 2}
    assert summary["reports"]["prompt_audit"]["forbidden_text_hits"] == 1
    assert summary["reports"]["blackboard_maintenance"]["entries_created"] == 2
    assert summary["reports"]["blackboard_maintenance"]["fallback_tasks"] == 1
    assert summary["reports"]["blackboard_maintenance"]["fallback_cluster_count"] == 5
    assert summary["reports"]["blackboard_maintenance"]["avg_state_mix_score_before"] == 25.0
    assert summary["reports"]["blackboard_maintenance"]["avg_state_mix_score_after"] == 31.0
    assert summary["reports"]["blackboard_maintenance"]["avg_state_mix_score_delta"] == 6.0
    assert summary["reports"]["blackboard_maintenance"]["avg_reasoning_density_delta"] == 0.06
    assert summary["reports"]["blackboard_maintenance"]["avg_gap_density_delta"] == -0.005
    assert summary["reports"]["blackboard_maintenance"]["positive_state_score_tasks"] == 1
    assert summary["reports"]["source_claim_verification"]["claims_checked"] == 4
    assert summary["reports"]["source_claim_verification"]["risky_claims"] == 2
    assert summary["reports"]["source_claim_verification"]["fallback_files"] == 1
    assert summary["reports"]["source_claim_verification"]["fallback_candidate_count"] == 12
    assert summary["reports"]["source_claim_verification"]["evidence_entry_count"] == 31
    assert (tmp_path / "lifecycle_summary.json").exists()
    assert (tmp_path / "lifecycle_summary.csv").exists()


def test_aggregate_lifecycle_reports_counts_artifact_fallback_items(tmp_path):
    swarm_dir = tmp_path / "task" / "swarm"
    swarm_dir.mkdir(parents=True)
    (swarm_dir / "artifact_placement_trace.json").write_text(json.dumps({
        "items": [
            {
                "target_file": "memo.docx",
                "native_form": "drafting_clause",
                "placement_traceable": True,
                "found_in_target_file": False,
                "native_form_satisfied": False,
                "found_elsewhere": True,
                "death_mode": "wrong_file",
            },
            {
                "target_file": "model.xlsx",
                "native_form": "workbook_row",
                "placement_traceable": False,
                "found_in_target_file": False,
                "native_form_satisfied": False,
                "found_elsewhere": False,
                "death_mode": "artifact_missing",
            },
        ]
    }), encoding="utf-8")

    summary = aggregate_lifecycle_reports(tmp_path)

    placement = summary["reports"]["artifact_placement"]
    assert placement["selected"] == 2
    assert placement["targeted"] == 2
    assert placement["traceable"] == 1
    assert placement["untraceable"] == 1
    assert placement["native_form_satisfied"] == 0
    assert placement["lost"] == 2
    assert placement["death_modes"] == {"wrong_file": 1, "artifact_missing": 1}
    assert placement["native_forms"] == {"drafting_clause": 1, "workbook_row": 1}
