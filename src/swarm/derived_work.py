from __future__ import annotations

import json
import os
import csv
from pathlib import Path
from typing import Any

from .blackboard import Blackboard
from .models import (
    Entry, EntrySource, ModelCaller, WorkerRecord, gen_entry_id,
)
from .prompt_audit import PromptAuditContext
from .verification import normalize_dollar, verify_calculation_expression
from .worker_dispatch import begin_call_model_usage, call_model, end_call_model_usage


CALCULATION_DEBT_SUBTYPES = {
    "missing_operation",
    "missing_population",
    "missing_assumption",
    "placement_failure",
    "not_calculable",
}


def calculation_debt_enabled() -> bool:
    return _env_on("SWARM_ENABLE_CALCULATION_DEBT") or calculation_debt_detect_only()


def calculation_debt_detect_only() -> bool:
    return _env_on("SWARM_CALCULATION_DEBT_DETECT_ONLY")


def run_calculation_debt_detection(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
) -> tuple[dict, int]:
    """Detect calculation debt, optionally execute eligible work, and write a report."""
    begin_call_model_usage()
    try:
        report, tokens = detect_calculation_debts(blackboard, seed, caller)
        if not calculation_debt_detect_only():
            execution_report, execution_tokens = execute_calculation_work_items(
                blackboard, caller, report.get("items", []),
            )
            tokens += execution_tokens
            report["items"] = execution_report["items"]
            report["summary"] = summarize_derived_work_items(report["items"])
            report["execution_summary"] = execution_report["summary"]
            report["mode"] = "execution"
        write_derived_work_report(blackboard.output_dir, report)
        return report, tokens
    finally:
        end_call_model_usage()


def detect_calculation_debts(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
) -> tuple[dict, int]:
    active = [e for e in blackboard.entries if e.status == "active"]
    prompt = _build_detection_prompt(blackboard, seed, active)
    payload, tokens = call_model(
        caller,
        prompt,
        max_tokens=8192,
        audit_context=PromptAuditContext(
            stage="calculation_debt_detection",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.seed_generated",
                "swarm.blackboard",
                "clean.professional_prior_dynamic",
            ],
            metadata={"entry_count": len(active)},
        ),
    )
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []
    items = normalize_derived_work_items(raw_items, active)
    summary = summarize_derived_work_items(items)
    return {
        "schema_version": 1,
        "mode": "detect_only" if calculation_debt_detect_only() else "detection",
        "items": items,
        "summary": summary,
    }, tokens


def normalize_derived_work_items(raw_items: list[Any], entries: list[Entry]) -> list[dict]:
    entry_by_id = {e.id: e for e in entries if e.id}
    normalized: list[dict] = []
    seen_keys: set[tuple] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        subtype = str(raw.get("subtype", "")).strip()
        if subtype not in CALCULATION_DEBT_SUBTYPES:
            subtype = "not_calculable"
        parent_ids = [
            str(v).strip() for v in raw.get("parent_entry_ids", [])
            if str(v).strip()
        ]
        parent_ids = [pid for pid in parent_ids if pid in entry_by_id]
        key = (subtype, tuple(sorted(parent_ids)), str(raw.get("reason", ""))[:120])
        if key in seen_keys:
            continue
        seen_keys.add(key)

        required_inputs = raw.get("required_inputs", [])
        if not isinstance(required_inputs, list):
            required_inputs = []
        validation_errors = []
        if not parent_ids:
            validation_errors.append("missing_parent_entry_ids")
        if not _parents_have_source(parent_ids, entry_by_id):
            validation_errors.append("missing_source_grounding")
        if subtype == "missing_operation" and not required_inputs:
            validation_errors.append("missing_required_inputs")
        expression = str(raw.get("expression", "")).strip()
        if subtype == "missing_operation" and not expression:
            validation_errors.append("missing_executable_expression")
        if subtype == "missing_operation" and _numeric_input_count(required_inputs) < 2:
            validation_errors.append("insufficient_numeric_inputs")
        if subtype == "missing_operation" and not _has_calculation_need_signal(
            parent_ids, entry_by_id,
        ):
            validation_errors.append("missing_calculation_need_signal")
        expected_result = str(raw.get("expected_result", "")).strip()
        if subtype == "missing_operation" and _result_present_in_parent(
            expected_result, parent_ids, entry_by_id,
        ):
            validation_errors.append("calculation_already_present")
        executable = subtype == "missing_operation" and not validation_errors
        status = "executable" if executable else "diagnostic_only"
        normalized.append({
            "id": f"dw_{len(normalized) + 1:03d}",
            "type": "calculate",
            "subtype": subtype,
            "status": status,
            "source": "calculation_debt_lens",
            "reason": str(raw.get("reason", "")).strip(),
            "parent_entry_ids": parent_ids,
            "required_inputs": required_inputs,
            "calculation_request": str(raw.get("calculation_request", "")).strip(),
            "expression": expression,
            "expected_result": expected_result,
            "target_deliverables": _as_str_list(raw.get("target_deliverables", [])),
            "created_entry_ids": [],
            "death_mode": None,
            "confidence": _safe_float(raw.get("confidence", 0.0)),
            "validation_errors": validation_errors,
            "execution_eligible": executable,
        })
    return normalized


def summarize_derived_work_items(items: list[dict]) -> dict:
    subtype_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    executable = 0
    for item in items:
        subtype = str(item.get("subtype", "unknown"))
        status = str(item.get("status", "unknown"))
        subtype_counts[subtype] = subtype_counts.get(subtype, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
        if item.get("execution_eligible"):
            executable += 1
    return {
        "selected": len(items),
        "executable": executable,
        "executed": sum(1 for item in items if item.get("status") == "executed"),
        "entries_created": sum(len(item.get("created_entry_ids") or []) for item in items),
        "subtype_counts": subtype_counts,
        "status_counts": status_counts,
    }


def execute_calculation_work_items(
    blackboard: Blackboard,
    caller: ModelCaller,
    items: list[dict],
) -> tuple[dict, int]:
    """Execute validated missing_operation items and append calculation entries.

    The model explains the calculation and assumptions; deterministic code
    verifies simple arithmetic when an expression/result pair is available.
    """
    updated_items = [dict(item) for item in items]
    total_tokens = 0
    created_entries: list[Entry] = []
    reserved_ids = _entry_ids(blackboard)
    for item in updated_items:
        if not item.get("execution_eligible"):
            continue
        if item.get("subtype") != "missing_operation":
            continue

        payload, tokens = _run_calculation_worker(blackboard, caller, item)
        total_tokens += tokens
        entry = _entry_from_calculation_payload(
            blackboard, item, payload, reserved_ids,
        )
        if entry is None:
            item["status"] = "execution_failed"
            item["death_mode"] = "executed_no_entry"
            item["execution_error"] = "worker_returned_no_valid_calculation"
            continue

        created_entries.append(entry)
        item["status"] = "executed"
        item["created_entry_ids"] = [entry.id]
        if payload.get("expression"):
            item["expression"] = str(payload.get("expression", "")).strip()
        if payload.get("result"):
            item["expected_result"] = str(payload.get("result", "")).strip()
        item["death_mode"] = None
        item["verification"] = {
            "verified": entry.verified,
            "source": "deterministic_arithmetic",
        }

    if created_entries:
        blackboard.add_entries_batch(created_entries)

    return {
        "items": updated_items,
        "summary": summarize_derived_work_items(updated_items),
    }, total_tokens


def _run_calculation_worker(
    blackboard: Blackboard,
    caller: ModelCaller,
    item: dict,
) -> tuple[dict, int]:
    parents = blackboard.get_entries_by_ids(item.get("parent_entry_ids", []))
    parent_text = "\n".join(_render_entry(parent) for parent in parents)
    required_inputs = json.dumps(item.get("required_inputs", []), indent=2)
    prompt = f"""Execute one source-grounded calculation work item.

TASK:
{blackboard.task_instruction}

DERIVED WORK ITEM:
{json.dumps(item, indent=2)}

PARENT BLACKBOARD ENTRIES:
{parent_text}

REQUIRED INPUTS:
{required_inputs}

Rules:
- Use only the required inputs and parent entries shown here.
- Do not infer missing inputs. If a premise is missing, return status "unsupported".
- Show the arithmetic expression and final result.
- Keep the explanation concise and source-grounded.

Return JSON:
{{
  "status": "computed|unsupported",
  "content": "calculation finding with formula, result, and source context",
  "expression": "1000000 * 0.02",
  "result": "$20,000",
  "source_document": "document name or null",
  "source_section": "section or null",
  "evidence": "short source/equation evidence",
  "confidence": 0.0
}}"""
    return call_model(
        caller,
        prompt,
        max_tokens=4096,
        audit_context=PromptAuditContext(
            stage="calculation_debt_execution",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.blackboard",
                "swarm.derived_work",
                "clean.professional_prior_dynamic",
            ],
            metadata={"derived_work_id": item.get("id")},
        ),
    )


def _entry_from_calculation_payload(
    blackboard: Blackboard,
    item: dict,
    payload: dict,
    reserved_ids: set[str] | None = None,
) -> Entry | None:
    if not isinstance(payload, dict) or payload.get("status") != "computed":
        return None
    content = str(payload.get("content", "")).strip()
    result = str(payload.get("result", "")).strip()
    expression = str(payload.get("expression", "")).strip()
    if len(content) < 20 or not result:
        return None

    verification = verify_calculation_expression(expression, result)
    parent_ids = [str(pid) for pid in item.get("parent_entry_ids", [])]
    parents = blackboard.get_entries_by_ids(parent_ids)
    source = _calculation_source(payload, parents)
    tags = [
        f"derived_work:{item.get('id')}",
        "derived_type:calculate",
        f"debt_subtype:{item.get('subtype')}",
        "lifecycle:transformed",
        "source_grounded:true",
    ]
    if expression:
        content = f"{content}\nFormula: {expression} = {result}"

    return Entry(
        id=_unique_entry_id(blackboard, reserved_ids),
        type="calculation",
        content=content,
        source=source,
        created_by=WorkerRecord(
            "calculation_debt_worker",
            f"derived_work:{item.get('id')}",
            blackboard.iteration,
        ),
        confidence=_safe_float(payload.get("confidence", 0.75)),
        verified=verification.get("verified") if expression else None,
        tags=tags,
        status="active",
        supports_entries=parent_ids,
    )


def _calculation_source(payload: dict, parents: list[Entry]) -> EntrySource | None:
    doc = payload.get("source_document")
    if isinstance(doc, str) and doc.strip():
        return EntrySource(
            document=doc.strip(),
            section=str(payload.get("source_section", "")).strip() or None,
            evidence=str(payload.get("evidence", "")).strip(),
        )
    for parent in parents:
        if parent.source and parent.source.document:
            return EntrySource(
                document=parent.source.document,
                section=parent.source.section,
                evidence=str(payload.get("evidence", "")).strip()
                or parent.source.evidence,
            )
    return None


def write_derived_work_report(output_dir: str, report: dict) -> None:
    if not output_dir:
        return
    swarm_dir = Path(output_dir) / "swarm"
    swarm_dir.mkdir(parents=True, exist_ok=True)
    (swarm_dir / "derived_work_items.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )


def _build_detection_prompt(
    blackboard: Blackboard,
    seed: dict,
    entries: list[Entry],
) -> str:
    rendered_entries = "\n".join(_render_entry(e) for e in _prioritized_entries(entries))
    key_questions = "\n".join(f"- {q}" for q in seed.get("key_questions", [])[:10])
    completeness = "\n".join(
        f"- {c}" for c in seed.get("completeness_criteria", [])[:10]
    )
    return f"""You detect calculation debt in a clean, criteria-blind document-analysis system.

TASK:
{blackboard.task_instruction}

SEED KEY QUESTIONS:
{key_questions or "None"}

SEED COMPLETENESS CRITERIA GENERATED BY THE SYSTEM:
{completeness or "None"}

BLACKBOARD ENTRIES:
{rendered_entries}

Classify potential calculation or exact-value transformation debts.

Debt subtypes:
- missing_operation: required inputs are present in the blackboard and a calculation should be performed.
- missing_population: the calculation needs a set of rows/items that is not fully known.
- missing_assumption: the calculation needs an unsupported assumption.
- placement_failure: the calculation or exact value already exists but appears not to be in the right artifact form.
- not_calculable: the source/state lacks necessary inputs or the item is not calculation debt.

Only missing_operation is executable. Be conservative.

For missing_operation, you MUST provide a concrete arithmetic expression using only source-grounded required_inputs, such as "1000000 * 0.02" or "(12500000 - 9400000)". If you cannot write that expression from visible inputs, classify the item as missing_population, missing_assumption, placement_failure, or not_calculable instead.

Do not classify missing source extraction, workbook tab placement, legal authority, severity, recommendation work, outdated valuation adjustments, future NPV estimates, unsupported current-market adjustments, or professional judgment as executable calculation debt.

Return JSON:
{{"items": [
  {{
    "subtype": "missing_operation|missing_population|missing_assumption|placement_failure|not_calculable",
    "reason": "specific reason",
    "parent_entry_ids": ["e1", "e2"],
    "required_inputs": [{{"label": "base", "value": "$100", "entry_id": "e1", "source_ref": "Doc section"}}],
    "calculation_request": "compute annual fee from base and rate",
    "expression": "100 * 0.02",
    "expected_result": "$2",
    "target_deliverables": [],
    "confidence": 0.0
  }}
]}}
"""


def _prioritized_entries(entries: list[Entry], limit: int | None = None) -> list[Entry]:
    if limit is None:
        limit = int(os.getenv("SWARM_CALCULATION_DEBT_ENTRY_LIMIT", "120"))
    def score(e: Entry) -> tuple[int, float]:
        text = e.content.lower()
        numeric = any(ch.isdigit() for ch in text)
        type_score = {
            "calculation": 5,
            "analysis": 4,
            "strategy": 3,
            "gap": 3,
            "observation": 2,
        }.get(e.type, 1)
        return (type_score + (2 if numeric else 0), e.confidence)
    return sorted(entries, key=score, reverse=True)[:limit]


def _render_entry(entry: Entry) -> str:
    source = ""
    if entry.source and entry.source.document:
        source = f" source={entry.source.document}/{entry.source.section or ''}"
    tags = ",".join(entry.tags[:5]) if entry.tags else ""
    supports = ",".join(entry.supports_entries[:5]) if entry.supports_entries else ""
    return (
        f"[{entry.id}] type={entry.type} conf={entry.confidence:.2f}"
        f"{source} tags={tags} supports={supports}\n"
        f"{entry.content[:700]}"
    )


def _parents_have_source(parent_ids: list[str], entry_by_id: dict[str, Entry]) -> bool:
    for pid in parent_ids:
        entry = entry_by_id.get(pid)
        if entry and entry.source and entry.source.document:
            return True
    return False


def _entry_ids(blackboard: Blackboard) -> set[str]:
    return {entry.id for entry in blackboard.entries if entry.id}


def _unique_entry_id(
    blackboard: Blackboard,
    reserved_ids: set[str] | None = None,
) -> str:
    if reserved_ids is None:
        reserved_ids = _entry_ids(blackboard)
    for _ in range(10_000):
        entry_id = gen_entry_id()
        if entry_id not in reserved_ids and blackboard.find_entry(entry_id) is None:
            reserved_ids.add(entry_id)
            return entry_id
    raise RuntimeError("could not allocate unique derived work entry id")


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v).strip()]


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _env_on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _numeric_input_count(required_inputs: list[Any]) -> int:
    count = 0
    for item in required_inputs:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value", ""))
        if any(ch.isdigit() for ch in value):
            count += 1
    return count


def _has_calculation_need_signal(
    parent_ids: list[str],
    entry_by_id: dict[str, Entry],
) -> bool:
    for pid in parent_ids:
        entry = entry_by_id.get(pid)
        if not entry:
            continue
        tags = entry.tags or []
        if entry.type == "gap" and any(
            tag.startswith(("missing_work:calculate", "coverage:missing"))
            for tag in tags
        ):
            return True
        if any(
            tag.startswith((
                "missing_work:calculate",
                "debt_subtype:missing_operation",
                "derived_type:calculate",
            ))
            for tag in tags
        ):
            return True
    return False


def _result_present_in_parent(
    expected_result: str,
    parent_ids: list[str],
    entry_by_id: dict[str, Entry],
) -> bool:
    if not expected_result:
        return False
    expected_dollar = normalize_dollar(expected_result)
    expected_digits = _digits_only(expected_result)
    for pid in parent_ids:
        entry = entry_by_id.get(pid)
        if not entry or not entry.content:
            continue
        text = entry.content.lower()
        if expected_result.lower() in text:
            return True
        if expected_dollar is not None:
            for raw in _dollar_like_values(text):
                if normalize_dollar(raw) == expected_dollar:
                    return True
        if expected_digits and expected_digits in _digits_only(text):
            return True
    return False


def _digits_only(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def _dollar_like_values(text: str) -> list[str]:
    import re
    return re.findall(r"\$\s*[\d,]+(?:\.\d+)?|\$\s*\d+(?:\.\d+)?\s*[kmb]", text)


def aggregate_derived_work_reports(results_dir: str | Path) -> dict:
    """Aggregate per-task derived-work reports into JSON and CSV summaries."""
    root = Path(results_dir)
    rows: list[dict] = []
    summary = {
        "schema_version": 1,
        "run": str(root),
        "feature_flags": {
            "SWARM_ENABLE_CALCULATION_DEBT": os.getenv("SWARM_ENABLE_CALCULATION_DEBT", ""),
            "SWARM_CALCULATION_DEBT_DETECT_ONLY": os.getenv("SWARM_CALCULATION_DEBT_DETECT_ONLY", ""),
        },
        "tasks": 0,
        "selected": 0,
        "executable": 0,
        "executed": 0,
        "entries_created": 0,
        "obligated": 0,
        "artifact_survived_pre_repair": 0,
        "artifact_survived_after_existing_repair": 0,
        "lost": 0,
        "false_positive": 0,
        "death_modes": {},
        "cost_delta_estimate": None,
        "contamination_audit": {
            "tasks_checked": 0,
            "forbidden_provenance_hits": 0,
        },
    }

    for report_path in root.rglob("swarm/derived_work_items.json"):
        task_dir = report_path.parent.parent
        rel_task = str(task_dir.relative_to(root)).replace("\\", "/")
        summary["tasks"] += 1
        report = _load_json(report_path)
        trace = _load_json(task_dir / "swarm" / "commitment_survival_trace.json")
        trace_by_id = {
            item.get("derived_work_id"): item
            for item in trace.get("items", [])
            if isinstance(item, dict)
        }
        for item in report.get("items", []):
            if not isinstance(item, dict):
                continue
            trace_item = trace_by_id.get(item.get("id"), {})
            death_mode = item.get("death_mode") or trace_item.get("death_mode")
            if death_mode:
                summary["death_modes"][death_mode] = (
                    summary["death_modes"].get(death_mode, 0) + 1
                )
            if item.get("execution_eligible"):
                summary["executable"] += 1
            if item.get("status") == "executed":
                summary["executed"] += 1
            created = item.get("created_entry_ids") or []
            summary["selected"] += 1
            summary["entries_created"] += len(created)
            if trace_item.get("obligated"):
                summary["obligated"] += 1
            if trace_item.get("found_in_artifact"):
                summary["artifact_survived_pre_repair"] += 1
                summary["artifact_survived_after_existing_repair"] += 1
            elif item.get("status") == "executed":
                summary["lost"] += 1
            rows.append({
                "task_id": rel_task,
                "derived_work_id": item.get("id", ""),
                "subtype": item.get("subtype", ""),
                "status": item.get("status", ""),
                "execution_eligible": item.get("execution_eligible", False),
                "parent_entry_ids": ",".join(item.get("parent_entry_ids") or []),
                "created_entry_ids": ",".join(created),
                "death_mode": death_mode or "",
                "target_files": ",".join(trace_item.get("target_files") or []),
                "found_in_artifact": trace_item.get("found_in_artifact", False),
            })

        audit = _load_json(task_dir / "swarm" / "prompt_audit.json")
        if audit:
            summary["contamination_audit"]["tasks_checked"] += 1
            summary["contamination_audit"]["forbidden_provenance_hits"] += (
                audit.get("summary", {}).get("forbidden_provenance_hits", 0)
            )

    (root / "derived_work_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    _write_summary_csv(root / "derived_work_summary.csv", rows)
    return summary


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _write_summary_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "task_id", "derived_work_id", "subtype", "status",
        "execution_eligible", "parent_entry_ids", "created_entry_ids",
        "death_mode", "target_files", "found_in_artifact",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
