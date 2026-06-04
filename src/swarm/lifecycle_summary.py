from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def aggregate_lifecycle_reports(results_dir: str | Path) -> dict:
    """Aggregate swarm lifecycle sidecars across a run directory.

    This summary is benchmark-agnostic. It reports reasoning-state health from
    files produced by the swarm itself instead of scorer or judge artifacts.
    """
    root = Path(results_dir)
    task_rows: list[dict] = []
    task_dirs: set[str] = set()
    summary = {
        "schema_version": 1,
        "run": str(root),
        "tasks": 0,
        "reports": {
            "debt_sensors": _empty_debt_summary(),
            "derived_work": _empty_derived_summary(),
            "artifact_placement": _empty_artifact_summary(),
            "source_custody": _empty_source_custody_summary(),
            "prompt_audit": _empty_prompt_audit_summary(),
            "blackboard_maintenance": _empty_maintenance_summary(),
            "source_claim_verification": _empty_source_claim_summary(),
        },
    }

    for swarm_dir in _swarm_dirs(root):
        task_dir = swarm_dir.parent
        task_id = _task_id(task_dir, root)
        task_seen = False

        debt = _load_json(swarm_dir / "debt_sensors.json")
        if debt:
            task_seen = True
            _aggregate_debt(task_id, debt, summary["reports"]["debt_sensors"], task_rows)

        derived = _load_json(swarm_dir / "derived_work_items.json")
        if derived:
            task_seen = True
            trace = _load_json(swarm_dir / "commitment_survival_trace.json")
            _aggregate_derived(
                task_id,
                derived,
                trace,
                summary["reports"]["derived_work"],
                task_rows,
            )

        placement = _load_json(swarm_dir / "artifact_placement_trace.json")
        if placement:
            task_seen = True
            _aggregate_artifact_placement(
                task_id,
                placement,
                summary["reports"]["artifact_placement"],
                task_rows,
            )

        custody = _load_json(swarm_dir / "source_custody.json")
        if custody:
            task_seen = True
            _aggregate_source_custody(
                task_id,
                custody,
                summary["reports"]["source_custody"],
                task_rows,
            )

        audit = _load_json(swarm_dir / "prompt_audit.json")
        if audit:
            task_seen = True
            _aggregate_prompt_audit(
                task_id,
                audit,
                summary["reports"]["prompt_audit"],
                task_rows,
            )

        maintenance = _load_json(swarm_dir / "blackboard_maintenance.json")
        if maintenance:
            task_seen = True
            _aggregate_maintenance(
                task_id,
                maintenance,
                summary["reports"]["blackboard_maintenance"],
                task_rows,
            )

        source_claims = _load_json(swarm_dir / "source_claim_verification.json")
        if source_claims:
            task_seen = True
            _aggregate_source_claims(
                task_id,
                source_claims,
                summary["reports"]["source_claim_verification"],
                task_rows,
            )

        if task_seen:
            task_dirs.add(task_id)

    summary["tasks"] = len(task_dirs)
    (root / "lifecycle_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    _write_lifecycle_csv(root / "lifecycle_summary.csv", task_rows)
    return summary


def _swarm_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("swarm") if path.is_dir())


def _task_id(task_dir: Path, root: Path) -> str:
    try:
        rel = task_dir.relative_to(root)
        value = str(rel).replace("\\", "/")
        return value if value and value != "." else task_dir.name
    except ValueError:
        return str(task_dir)


def _aggregate_debt(
    task_id: str,
    report: dict,
    target: dict,
    rows: list[dict],
) -> None:
    items = [item for item in report.get("items", []) if isinstance(item, dict)]
    computed = _summarize_items(items)
    reported = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    selected = _int(reported.get("selected"), computed["selected"])
    actionable = _int(reported.get("actionable"), computed["actionable"])
    type_counts = _dict_ints(reported.get("type_counts") or computed["type_counts"])
    status_counts = _dict_ints(reported.get("status_counts") or computed["status_counts"])

    target["tasks"] += 1
    target["selected"] += selected
    target["actionable"] += actionable
    _merge_counts(target["type_counts"], type_counts)
    _merge_counts(target["status_counts"], status_counts)

    entries_created = 0
    for key in ("relation", "source_object", "severity", "authority"):
        exec_summary = report.get(f"{key}_execution_summary", {})
        created = 0
        if isinstance(exec_summary, dict):
            created = _int(exec_summary.get("entries_created"), 0)
        if not created:
            created = len(report.get(f"created_{key}_entry_ids", []) or [])
        target["execution_entries_created"][key] += created
        entries_created += created

    gap_entries = len(report.get("created_gap_entry_ids", []) or [])
    target["gap_entries_created"] += gap_entries
    unresolved = sum(
        1
        for item in items
        if item.get("status") == "actionable_gap" and not item.get("created_entry_ids")
    )
    target["unresolved_actionable"] += unresolved
    coordinator = report.get("lens_coordinator", {})
    if isinstance(coordinator, dict) and coordinator:
        target["coordinator_tasks"] += 1
        target["coordinator_selected"] += _int(coordinator.get("selected_actionable"), 0)
        target["coordinator_deferred"] += _int(coordinator.get("deferred"), 0)

    rows.append(_row(
        task_id,
        "debt_sensors",
        selected=selected,
        actionable=actionable,
        entries_created=entries_created,
        unresolved=unresolved,
        type_counts=type_counts,
        status_counts=status_counts,
        notes=f"mode={report.get('mode', '')}; gap_entries={gap_entries}",
    ))


def _aggregate_derived(
    task_id: str,
    report: dict,
    trace: dict,
    target: dict,
    rows: list[dict],
) -> None:
    items = [item for item in report.get("items", []) if isinstance(item, dict)]
    trace_items = {
        item.get("derived_work_id"): item
        for item in trace.get("items", [])
        if isinstance(item, dict)
    }
    selected = len(items)
    executable = sum(1 for item in items if item.get("execution_eligible"))
    executed = sum(1 for item in items if item.get("status") == "executed")
    entries_created = sum(len(item.get("created_entry_ids") or []) for item in items)
    obligated = 0
    survived = 0
    lost = 0
    death_modes: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        trace_item = trace_items.get(item.get("id"), {})
        if trace_item.get("obligated"):
            obligated += 1
        if trace_item.get("found_in_artifact"):
            survived += 1
        elif item.get("status") == "executed":
            lost += 1
        mode = item.get("death_mode") or trace_item.get("death_mode")
        if mode:
            death_modes[str(mode)] = death_modes.get(str(mode), 0) + 1

    target["tasks"] += 1
    target["selected"] += selected
    target["executable"] += executable
    target["executed"] += executed
    target["entries_created"] += entries_created
    target["obligated"] += obligated
    target["artifact_survived"] += survived
    target["lost"] += lost
    _merge_counts(target["death_modes"], death_modes)
    _merge_counts(target["status_counts"], status_counts)

    rows.append(_row(
        task_id,
        "derived_work",
        selected=selected,
        executed=executed,
        entries_created=entries_created,
        lost=lost,
        death_modes=death_modes,
        status_counts=status_counts,
        notes=f"executable={executable}; obligated={obligated}; survived={survived}",
    ))


def _aggregate_artifact_placement(
    task_id: str,
    report: dict,
    target: dict,
    rows: list[dict],
) -> None:
    items = [item for item in report.get("items", []) if isinstance(item, dict)]
    reported = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    selected = _int(reported.get("selected"), len(items))
    targeted = _int(
        reported.get("targeted"),
        sum(1 for item in items if item.get("target_file")),
    )
    traceable = _int(
        reported.get("traceable"),
        sum(1 for item in items if item.get("placement_traceable")),
    )
    found_target = _int(
        reported.get("found_in_target_file"),
        sum(1 for item in items if item.get("found_in_target_file")),
    )
    native_satisfied = _int(
        reported.get("native_form_satisfied"),
        sum(1 for item in items if item.get("native_form_satisfied")),
    )
    found_elsewhere = _int(
        reported.get("found_elsewhere"),
        sum(1 for item in items if item.get("found_elsewhere")),
    )
    lost = _int(reported.get("lost"), selected - native_satisfied)
    death_modes = _dict_ints(reported.get("death_modes"))
    native_forms = _dict_ints(reported.get("native_forms"))
    if not death_modes:
        for item in items:
            mode = item.get("death_mode")
            if mode:
                death_modes[str(mode)] = death_modes.get(str(mode), 0) + 1
    if not native_forms:
        for item in items:
            native = str(item.get("native_form") or "unknown")
            native_forms[native] = native_forms.get(native, 0) + 1

    target["tasks"] += 1
    target["selected"] += selected
    target["targeted"] += targeted
    target["traceable"] += traceable
    target["untraceable"] += max(0, selected - traceable)
    target["found_in_target_file"] += found_target
    target["native_form_satisfied"] += native_satisfied
    target["found_elsewhere"] += found_elsewhere
    target["lost"] += lost
    _merge_counts(target["death_modes"], death_modes)
    _merge_counts(target["native_forms"], native_forms)

    rows.append(_row(
        task_id,
        "artifact_placement",
        selected=selected,
        lost=lost,
        death_modes=death_modes,
        type_counts=native_forms,
        notes=(
            f"targeted={targeted}; traceable={traceable}; found_target={found_target}; "
            f"native_satisfied={native_satisfied}; found_elsewhere={found_elsewhere}"
        ),
    ))


def _aggregate_prompt_audit(
    task_id: str,
    report: dict,
    target: dict,
    rows: list[dict],
) -> None:
    audit_summary = report.get("summary", {})
    if not isinstance(audit_summary, dict):
        audit_summary = {}
    records = _int(audit_summary.get("records"), len(report.get("records", []) or []))
    provenance_hits = _int(audit_summary.get("forbidden_provenance_hits"), 0)
    text_hits = _int(audit_summary.get("forbidden_text_hits"), 0)
    stages = _dict_ints(audit_summary.get("stages"))

    target["tasks_checked"] += 1
    target["records"] += records
    target["forbidden_provenance_hits"] += provenance_hits
    target["forbidden_text_hits"] += text_hits
    _merge_counts(target["stages"], stages)

    rows.append(_row(
        task_id,
        "prompt_audit",
        selected=records,
        unresolved=provenance_hits + text_hits,
        type_counts=stages,
        notes=f"forbidden_provenance_hits={provenance_hits}; forbidden_text_hits={text_hits}",
    ))


def _aggregate_source_custody(
    task_id: str,
    report: dict,
    target: dict,
    rows: list[dict],
) -> None:
    report_summary = report.get("summary", {})
    if not isinstance(report_summary, dict):
        report_summary = {}
    audits = report.get("audits", [])
    if not isinstance(audits, list):
        audits = []
    quarantined = _int(report_summary.get("entries_quarantined"), 0)
    invalid_documents = _dict_ints(report_summary.get("invalid_documents"))
    reasons = _dict_ints(report_summary.get("reasons"))

    target["tasks"] += 1
    target["audits"] += len(audits)
    target["entries_quarantined"] += quarantined
    _merge_counts(target["invalid_documents"], invalid_documents)
    _merge_counts(target["reasons"], reasons)

    rows.append(_row(
        task_id,
        "source_custody",
        selected=quarantined,
        unresolved=quarantined,
        type_counts=invalid_documents,
        status_counts=reasons,
        notes=f"audits={len(audits)}",
    ))


def _aggregate_maintenance(
    task_id: str,
    report: dict,
    target: dict,
    rows: list[dict],
) -> None:
    report_summary = report.get("summary", {})
    if not isinstance(report_summary, dict):
        report_summary = {}
    candidates = _int(report.get("candidate_entry_count"), 0)
    selected = _int(report_summary.get("consolidations_selected"), 0)
    created = _int(
        report_summary.get("entries_created"),
        len(report.get("created_entry_ids", []) or []),
    )
    superseded = _int(
        report_summary.get("entries_superseded"),
        len(report.get("superseded_entry_ids", []) or []),
    )

    target["tasks"] += 1
    target["candidate_entry_count"] += candidates
    target["consolidations_selected"] += selected
    target["entries_created"] += created
    target["entries_superseded"] += superseded

    rows.append(_row(
        task_id,
        "blackboard_maintenance",
        selected=selected,
        entries_created=created,
        notes=f"mode={report.get('mode', '')}; candidates={candidates}; superseded={superseded}",
    ))


def _aggregate_source_claims(
    task_id: str,
    report: dict,
    target: dict,
    rows: list[dict],
) -> None:
    report_summary = report.get("summary", {})
    if not isinstance(report_summary, dict):
        report_summary = {}
    files_checked = _int(report_summary.get("files_checked"), 0)
    claims_checked = _int(report_summary.get("claims_checked"), 0)
    risky_claims = _int(report_summary.get("risky_claims"), 0)
    status_counts = _dict_ints(report_summary.get("status_counts"))
    severity_counts = _dict_ints(report_summary.get("severity_counts"))
    fallback_files = 0
    fallback_candidate_count = 0
    evidence_entry_count = 0
    for file_report in report.get("files", []):
        if not isinstance(file_report, dict):
            continue
        if file_report.get("fallback_used"):
            fallback_files += 1
        fallback_candidate_count += _int(file_report.get("fallback_candidate_count"), 0)
        evidence_entry_count += _int(file_report.get("evidence_entry_count"), 0)

    target["tasks"] += 1
    target["files_checked"] += files_checked
    target["claims_checked"] += claims_checked
    target["risky_claims"] += risky_claims
    target["fallback_files"] += fallback_files
    target["fallback_candidate_count"] += fallback_candidate_count
    target["evidence_entry_count"] += evidence_entry_count
    _merge_counts(target["status_counts"], status_counts)
    _merge_counts(target["severity_counts"], severity_counts)

    rows.append(_row(
        task_id,
        "source_claim_verification",
        selected=claims_checked,
        unresolved=risky_claims,
        type_counts=status_counts,
        status_counts=severity_counts,
        notes=(
            f"mode={report.get('mode', '')}; files_checked={files_checked}; "
            f"fallback_files={fallback_files}; "
            f"fallback_candidates={fallback_candidate_count}"
        ),
    ))


def _empty_debt_summary() -> dict:
    return {
        "tasks": 0,
        "selected": 0,
        "actionable": 0,
        "type_counts": {},
        "status_counts": {},
        "execution_entries_created": {
            "relation": 0,
            "source_object": 0,
            "severity": 0,
            "authority": 0,
        },
        "gap_entries_created": 0,
        "unresolved_actionable": 0,
        "coordinator_tasks": 0,
        "coordinator_selected": 0,
        "coordinator_deferred": 0,
    }


def _empty_derived_summary() -> dict:
    return {
        "tasks": 0,
        "selected": 0,
        "executable": 0,
        "executed": 0,
        "entries_created": 0,
        "obligated": 0,
        "artifact_survived": 0,
        "lost": 0,
        "death_modes": {},
        "status_counts": {},
    }


def _empty_artifact_summary() -> dict:
    return {
        "tasks": 0,
        "selected": 0,
        "targeted": 0,
        "traceable": 0,
        "untraceable": 0,
        "found_in_target_file": 0,
        "native_form_satisfied": 0,
        "found_elsewhere": 0,
        "lost": 0,
        "death_modes": {},
        "native_forms": {},
    }


def _empty_source_custody_summary() -> dict:
    return {
        "tasks": 0,
        "audits": 0,
        "entries_quarantined": 0,
        "invalid_documents": {},
        "reasons": {},
    }


def _empty_prompt_audit_summary() -> dict:
    return {
        "tasks_checked": 0,
        "records": 0,
        "forbidden_provenance_hits": 0,
        "forbidden_text_hits": 0,
        "stages": {},
    }


def _empty_maintenance_summary() -> dict:
    return {
        "tasks": 0,
        "candidate_entry_count": 0,
        "consolidations_selected": 0,
        "entries_created": 0,
        "entries_superseded": 0,
    }


def _empty_source_claim_summary() -> dict:
    return {
        "tasks": 0,
        "files_checked": 0,
        "claims_checked": 0,
        "risky_claims": 0,
        "fallback_files": 0,
        "fallback_candidate_count": 0,
        "evidence_entry_count": 0,
        "status_counts": {},
        "severity_counts": {},
    }


def _summarize_items(items: list[dict]) -> dict:
    type_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for item in items:
        item_type = str(item.get("type") or "unknown")
        status = str(item.get("status") or "unknown")
        type_counts[item_type] = type_counts.get(item_type, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "selected": len(items),
        "actionable": status_counts.get("actionable_gap", 0),
        "type_counts": type_counts,
        "status_counts": status_counts,
    }


def _merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + _int(value, 0)


def _dict_ints(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): _int(value, 0) for key, value in raw.items()}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _row(task_id: str, report: str, **values: Any) -> dict:
    row = {
        "task_id": task_id,
        "report": report,
        "selected": 0,
        "actionable": 0,
        "executed": 0,
        "entries_created": 0,
        "lost": 0,
        "unresolved": 0,
        "death_modes": "",
        "type_counts": "",
        "status_counts": "",
        "notes": "",
    }
    row.update(values)
    for key in ("death_modes", "type_counts", "status_counts"):
        if isinstance(row.get(key), dict):
            row[key] = json.dumps(row[key], sort_keys=True)
    return row


def _write_lifecycle_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "task_id",
        "report",
        "selected",
        "actionable",
        "executed",
        "entries_created",
        "lost",
        "unresolved",
        "death_modes",
        "type_counts",
        "status_counts",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
