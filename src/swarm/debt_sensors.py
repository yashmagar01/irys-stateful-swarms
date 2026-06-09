from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .blackboard import Blackboard
from .models import Entry, EntrySource, ModelCaller, WorkerRecord, gen_entry_id
from .prompt_audit import PromptAuditContext
from .section_index import resolve_section_text
from .worker_dispatch import begin_call_model_usage, call_model, end_call_model_usage


def debt_sensors_enabled() -> bool:
    return (
        relation_debt_enabled()
        or source_object_debt_enabled()
        or severity_debt_enabled()
        or authority_debt_enabled()
    )


def relation_debt_enabled() -> bool:
    return _env_on("SWARM_ENABLE_RELATION_DEBT")


def source_object_debt_enabled() -> bool:
    return _env_on("SWARM_ENABLE_SOURCE_OBJECT_DEBT")


def severity_debt_enabled() -> bool:
    return _env_on("SWARM_ENABLE_SEVERITY_DEBT")


def authority_debt_enabled() -> bool:
    return _env_on("SWARM_ENABLE_AUTHORITY_DEBT")


def debt_sensors_detect_only() -> bool:
    return _env_on("SWARM_DEBT_SENSORS_DETECT_ONLY")


def relation_debt_execute_enabled() -> bool:
    return _env_on("SWARM_RELATION_DEBT_EXECUTE")


def source_object_debt_execute_enabled() -> bool:
    return _env_on("SWARM_SOURCE_OBJECT_DEBT_EXECUTE")


def severity_debt_execute_enabled() -> bool:
    return _env_on("SWARM_SEVERITY_DEBT_EXECUTE")


def authority_debt_execute_enabled() -> bool:
    return _env_on("SWARM_AUTHORITY_DEBT_EXECUTE")


def lens_coordinator_enabled() -> bool:
    return _env_on("SWARM_ENABLE_LENS_COORDINATOR")


def lens_coordinator_max_items() -> int:
    try:
        return max(1, int(os.getenv("SWARM_LENS_COORDINATOR_MAX_ITEMS", "24")))
    except ValueError:
        return 24


def run_debt_sensors(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
) -> tuple[dict, int]:
    """Run passive debt sensors for relation/source-object failure modes."""
    begin_call_model_usage()
    try:
        items: list[dict] = []
        total_tokens = 0
        if relation_debt_enabled():
            relation_items, tokens = detect_relation_debts(blackboard, seed, caller)
            total_tokens += tokens
            items.extend(relation_items)
        if source_object_debt_enabled():
            source_items, tokens = detect_source_object_debts(blackboard, seed, caller)
            total_tokens += tokens
            items.extend(source_items)
        if severity_debt_enabled():
            severity_items, tokens = detect_severity_debts(blackboard, seed, caller)
            total_tokens += tokens
            items.extend(severity_items)
        if authority_debt_enabled():
            authority_items, tokens = detect_authority_debts(blackboard, seed, caller)
            total_tokens += tokens
            items.extend(authority_items)

        normalized_items = normalize_debt_sensor_items(items)
        report = {
            "schema_version": 1,
            "mode": _debt_sensor_mode(),
            "items": normalized_items,
        }
        report["summary"] = summarize_debt_sensor_items(report["items"])
        if (
            lens_coordinator_enabled()
            and not debt_sensors_detect_only()
            and len(report["items"]) > lens_coordinator_max_items()
        ):
            coordinated_items, coordinator_report, coordinator_tokens = (
                coordinate_debt_sensor_items(blackboard, seed, caller, report["items"])
            )
            total_tokens += coordinator_tokens
            report["items"] = coordinated_items
            report["lens_coordinator"] = coordinator_report
            report["summary"] = summarize_debt_sensor_items(report["items"])
        if not debt_sensors_detect_only():
            relation_entries: list[Entry] = []
            if relation_debt_execute_enabled():
                relation_report, relation_tokens = execute_relation_debt_items(
                    blackboard, caller, report["items"],
                )
                total_tokens += relation_tokens
                report["items"] = relation_report["items"]
                report["relation_execution_summary"] = relation_report["summary"]
                relation_entries = relation_report["entries"]
                if relation_entries:
                    blackboard.add_entries_batch(relation_entries)

            source_entries: list[Entry] = []
            if source_object_debt_execute_enabled():
                source_report, source_tokens = execute_source_object_debt_items(
                    blackboard, caller, report["items"],
                )
                total_tokens += source_tokens
                report["items"] = source_report["items"]
                report["source_object_execution_summary"] = source_report["summary"]
                source_entries = source_report["entries"]
            if source_entries:
                blackboard.add_entries_batch(source_entries)

            severity_entries: list[Entry] = []
            if severity_debt_execute_enabled():
                severity_report, severity_tokens = execute_severity_debt_items(
                    blackboard, caller, report["items"],
                )
                total_tokens += severity_tokens
                report["items"] = severity_report["items"]
                report["severity_execution_summary"] = severity_report["summary"]
                severity_entries = severity_report["entries"]
                if severity_entries:
                    blackboard.add_entries_batch(severity_entries)

            authority_entries: list[Entry] = []
            if authority_debt_execute_enabled():
                authority_report, authority_tokens = execute_authority_debt_items(
                    blackboard, caller, report["items"],
                )
                total_tokens += authority_tokens
                report["items"] = authority_report["items"]
                report["authority_execution_summary"] = authority_report["summary"]
                authority_entries = authority_report["entries"]
                if authority_entries:
                    blackboard.add_entries_batch(authority_entries)

            gap_entries = debt_sensor_items_to_gap_entries(report["items"], blackboard)
            if gap_entries:
                blackboard.add_entries_batch(gap_entries)
            report["created_relation_entry_ids"] = [entry.id for entry in relation_entries]
            report["created_source_object_entry_ids"] = [entry.id for entry in source_entries]
            report["created_severity_entry_ids"] = [entry.id for entry in severity_entries]
            report["created_authority_entry_ids"] = [entry.id for entry in authority_entries]
            report["created_gap_entry_ids"] = [entry.id for entry in gap_entries]
            report["summary"] = summarize_debt_sensor_items(report["items"])
        write_debt_sensor_report(blackboard.output_dir, report)
        return report, total_tokens
    finally:
        end_call_model_usage()


def detect_relation_debts(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
) -> tuple[list[dict], int]:
    entries = _prioritized_entries(blackboard.entries)
    prompt = f"""Detect cross-document relation debt in source-backed blackboard state.

TASK:
{blackboard.task_instruction}

SEED QUESTIONS:
{_format_list(seed.get("key_questions", []))}

BLACKBOARD ENTRIES:
{_render_entries(entries)}

Find only cases where existing entries from different documents likely need comparison, reconciliation, conflict analysis, date/provision alignment, or entity matching. Do not ask for generic extra analysis.

Return JSON:
{{"items": [
  {{
    "type": "relation",
    "subtype": "conflict|reconciliation|date_alignment|entity_alignment|provision_interplay",
    "reason": "specific relation work needed",
    "parent_entry_ids": ["e1", "e2"],
    "confidence": 0.0
  }}
]}}"""
    payload, tokens = call_model(
        caller,
        prompt,
        max_tokens=4096,
        audit_context=PromptAuditContext(
            stage="relation_debt_detection",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.seed_generated",
                "swarm.blackboard",
                "clean.professional_prior_dynamic",
            ],
        ),
    )
    raw = payload.get("items", [])
    return raw if isinstance(raw, list) else [], tokens


def detect_source_object_debts(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
) -> tuple[list[dict], int]:
    entries = _prioritized_entries(blackboard.entries)
    doc_state = "\n".join(
        f"- {doc.name}: read={doc.read_status}, unread={len(doc.sections_unread)}, headings={len(doc.headings)}"
        for doc in blackboard.documents
    )
    prompt = f"""Detect source/object discovery debt in source-backed blackboard state.

TASK:
{blackboard.task_instruction}

DOCUMENT READ STATE:
{doc_state}

SEED QUESTIONS:
{_format_list(seed.get("key_questions", []))}

BLACKBOARD ENTRIES:
{_render_entries(entries)}

Find only cases where a required object, entity, row population, schedule item, component list, or source section appears not to have been discovered well enough. This sensor must not invent answers from current state.

Return JSON:
{{"items": [
  {{
    "type": "source_object",
    "subtype": "missing_population|missing_entity|missing_component|unread_section|thin_source_coverage",
    "reason": "specific source/object work needed",
    "parent_entry_ids": ["e1"],
    "target_documents": ["document name"],
    "confidence": 0.0
  }}
]}}"""
    payload, tokens = call_model(
        caller,
        prompt,
        max_tokens=4096,
        audit_context=PromptAuditContext(
            stage="source_object_debt_detection",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.seed_generated",
                "swarm.blackboard",
                "source.documents",
                "clean.professional_prior_dynamic",
            ],
        ),
    )
    raw = payload.get("items", [])
    return raw if isinstance(raw, list) else [], tokens


def detect_severity_debts(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
) -> tuple[list[dict], int]:
    entries = _prioritized_entries(blackboard.entries)
    prompt = f"""Detect severity/recommendation debt in source-backed blackboard state.

TASK:
{blackboard.task_instruction}

SEED QUESTIONS:
{_format_list(seed.get("key_questions", []))}

BLACKBOARD ENTRIES:
{_render_entries(entries)}

Find only cases where existing source-backed entries identify an issue, conflict,
defect, exposure, missing term, or operational concern but do not yet state its
severity, consequence, priority, or concrete recommended action. Do not invent
new source facts and do not ask for generic advice.

Return JSON:
{{"items": [
  {{
    "type": "severity",
    "subtype": "risk_without_severity|issue_without_recommendation|priority_needed|consequence_needed",
    "reason": "specific severity or recommendation work needed",
    "parent_entry_ids": ["e1"],
    "confidence": 0.0
  }}
]}}"""
    payload, tokens = call_model(
        caller,
        prompt,
        max_tokens=4096,
        audit_context=PromptAuditContext(
            stage="severity_debt_detection",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.seed_generated",
                "swarm.blackboard",
                "clean.professional_prior_dynamic",
            ],
        ),
    )
    raw = payload.get("items", [])
    return raw if isinstance(raw, list) else [], tokens


def detect_authority_debts(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
) -> tuple[list[dict], int]:
    entries = _prioritized_entries(blackboard.entries)
    prompt = f"""Detect authority/evidence citation debt in source-backed blackboard state.

TASK:
{blackboard.task_instruction}

SEED QUESTIONS:
{_format_list(seed.get("key_questions", []))}

BLACKBOARD ENTRIES:
{_render_entries(entries)}

Find only cases where an existing blackboard entry makes a conclusion, issue,
standard, obligation, recommendation, or classification that should be tied to
a more exact source clause, document provision, cited standard, or evidence
anchor already visible in the parent entry or nearby blackboard state.

Do not request external research. Do not invent statutes, cases, standards, or
document sections. This lens only strengthens source custody for facts already
in the blackboard.

Return JSON:
{{"items": [
  {{
    "type": "authority",
    "subtype": "source_citation_needed|clause_reference_needed|standard_needed|evidence_anchor_needed",
    "reason": "specific authority/evidence work needed",
    "parent_entry_ids": ["e1"],
    "confidence": 0.0
  }}
]}}"""
    payload, tokens = call_model(
        caller,
        prompt,
        max_tokens=4096,
        audit_context=PromptAuditContext(
            stage="authority_debt_detection",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.seed_generated",
                "swarm.blackboard",
                "clean.professional_prior_dynamic",
            ],
        ),
    )
    raw = payload.get("items", [])
    return raw if isinstance(raw, list) else [], tokens


def normalize_debt_sensor_items(raw_items: list[Any]) -> list[dict]:
    normalized = []
    seen = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_type = str(raw.get("type", "")).strip()
        if item_type not in {"relation", "source_object", "severity", "authority"}:
            continue
        subtype = str(raw.get("subtype", "")).strip() or "unknown"
        parent_ids = [
            str(value).strip()
            for value in raw.get("parent_entry_ids", [])
            if str(value).strip()
        ]
        reason = str(raw.get("reason", "")).strip()
        key = (item_type, subtype, tuple(sorted(parent_ids)), reason[:120])
        if key in seen or len(reason) < 10:
            continue
        seen.add(key)
        confidence = _safe_float(raw.get("confidence", 0.0))
        status = "actionable_gap" if confidence >= 0.7 else "diagnostic_only"
        normalized.append({
            "id": f"ds_{len(normalized) + 1:03d}",
            "type": item_type,
            "subtype": subtype,
            "status": status,
            "reason": reason,
            "parent_entry_ids": parent_ids,
            "target_documents": _as_str_list(raw.get("target_documents", [])),
            "confidence": confidence,
        })
    return normalized


def summarize_debt_sensor_items(items: list[dict]) -> dict:
    type_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for item in items:
        type_counts[item["type"]] = type_counts.get(item["type"], 0) + 1
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    return {
        "selected": len(items),
        "type_counts": type_counts,
        "status_counts": status_counts,
        "actionable": status_counts.get("actionable_gap", 0),
    }


def coordinate_debt_sensor_items(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
    items: list[dict],
) -> tuple[list[dict], dict, int]:
    """Prioritize multiple debt-lens outputs under a bounded execution budget."""
    actionable = [
        item for item in items
        if item.get("status") == "actionable_gap"
    ]
    max_items = lens_coordinator_max_items()
    if len(actionable) <= max_items:
        report = {
            "mode": "not_needed",
            "max_items": max_items,
            "input_actionable": len(actionable),
            "selected_actionable": len(actionable),
            "deferred": 0,
            "selected_item_ids": [item.get("id", "") for item in actionable],
            "deferred_item_ids": [],
            "decisions": [],
        }
        return items, report, 0

    prompt = f"""Coordinate debt lenses for a document-analysis swarm.

TASK:
{blackboard.task_instruction}

SEED QUESTIONS:
{_format_list(seed.get("key_questions", []))}

ACTIONABLE DEBT ITEMS FROM MULTIPLE LENSES:
{_render_debt_items(actionable)}

Choose at most {max_items} item IDs to execute now. This is a state-quality budget decision, not final-output repair.

Prioritize work that:
- is source-grounded or can read specific source excerpts;
- will transform raw state into analysis/calculation/recommendation/authority state;
- affects the user's requested deliverable materially;
- avoids duplicate work across lenses;
- does not rely on benchmark criteria, task IDs, or scorer artifacts.

Defer work that is redundant, low-confidence, too speculative, or less useful than another item.

Return JSON:
{{"selected_item_ids": ["ds_001"], "decisions": [
  {{"id": "ds_001", "decision": "execute|defer", "reason": "brief rationale"}}
]}}
"""
    payload, tokens = call_model(
        caller,
        prompt,
        max_tokens=4096,
        audit_context=PromptAuditContext(
            stage="lens_coordinator",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.seed_generated",
                "swarm.blackboard",
                "swarm.debt_sensors",
                "clean.professional_prior_dynamic",
            ],
            metadata={
                "actionable_items": len(actionable),
                "max_items": max_items,
            },
        ),
    )
    selected_ids = _normalize_selected_item_ids(
        payload.get("selected_item_ids", []),
        actionable,
        max_items,
    )
    decisions = _normalize_coordinator_decisions(
        payload.get("decisions", []),
        actionable,
        selected_ids,
    )
    selected = set(selected_ids)
    updated_items = []
    deferred_ids = []
    for item in items:
        updated = dict(item)
        if item.get("status") == "actionable_gap" and item.get("id") not in selected:
            updated["status"] = "deferred_by_coordinator"
            updated["coordinator_deferred"] = True
            deferred_ids.append(str(item.get("id", "")))
        elif item.get("status") == "actionable_gap":
            updated["coordinator_selected"] = True
        updated_items.append(updated)

    report = {
        "mode": "prioritized",
        "max_items": max_items,
        "input_actionable": len(actionable),
        "selected_actionable": len(selected_ids),
        "deferred": len(deferred_ids),
        "selected_item_ids": selected_ids,
        "deferred_item_ids": deferred_ids,
        "decisions": decisions,
    }
    return updated_items, report, tokens


def _normalize_selected_item_ids(
    raw_ids: Any,
    actionable: list[dict],
    max_items: int,
) -> list[str]:
    valid_ids = [str(item.get("id", "")) for item in actionable if item.get("id")]
    valid = set(valid_ids)
    selected: list[str] = []
    if isinstance(raw_ids, list):
        for raw in raw_ids:
            item_id = str(raw).strip()
            if item_id in valid and item_id not in selected:
                selected.append(item_id)
            if len(selected) >= max_items:
                break
    if selected:
        return selected
    ranked = sorted(
        actionable,
        key=lambda item: (
            _debt_type_priority(str(item.get("type", ""))),
            _safe_float(item.get("confidence", 0.0)),
        ),
        reverse=True,
    )
    return [str(item.get("id", "")) for item in ranked[:max_items] if item.get("id")]


def _normalize_coordinator_decisions(
    raw_decisions: Any,
    actionable: list[dict],
    selected_ids: list[str],
) -> list[dict]:
    actionable_by_id = {
        str(item.get("id", "")): item for item in actionable if item.get("id")
    }
    selected = set(selected_ids)
    normalized: list[dict] = []
    seen: set[str] = set()
    if isinstance(raw_decisions, list):
        for raw in raw_decisions:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get("id", "")).strip()
            if item_id not in actionable_by_id or item_id in seen:
                continue
            decision = str(raw.get("decision", "")).strip().lower()
            if decision not in {"execute", "defer"}:
                decision = "execute" if item_id in selected else "defer"
            normalized.append({
                "id": item_id,
                "type": actionable_by_id[item_id].get("type", ""),
                "decision": decision,
                "reason": str(raw.get("reason", "")).strip()[:400],
            })
            seen.add(item_id)
    for item_id, item in actionable_by_id.items():
        if item_id in seen:
            continue
        normalized.append({
            "id": item_id,
            "type": item.get("type", ""),
            "decision": "execute" if item_id in selected else "defer",
            "reason": "defaulted from selected_item_ids",
        })
    return normalized


def _render_debt_items(items: list[dict]) -> str:
    lines = []
    for item in items[:80]:
        parent_ids = ",".join(item.get("parent_entry_ids", [])[:6])
        targets = ",".join(item.get("target_documents", [])[:4])
        lines.append(
            f"- {item.get('id')} type={item.get('type')} subtype={item.get('subtype')} "
            f"confidence={item.get('confidence')} parents={parent_ids or 'none'} "
            f"targets={targets or 'none'} reason={str(item.get('reason', ''))[:350]}"
        )
    return "\n".join(lines) or "None"


def _debt_type_priority(item_type: str) -> int:
    return {
        "source_object": 5,
        "relation": 4,
        "severity": 3,
        "authority": 2,
    }.get(item_type, 1)


def debt_sensor_items_to_gap_entries(
    items: list[dict],
    blackboard: Blackboard,
) -> list[Entry]:
    entries = []
    reserved_ids = _entry_ids(blackboard)
    for item in items:
        if item.get("status") != "actionable_gap":
            continue
        if item.get("type") == "relation" and item.get("created_entry_ids"):
            continue
        if item.get("type") == "source_object" and item.get("created_entry_ids"):
            continue
        if item.get("type") == "severity" and item.get("created_entry_ids"):
            continue
        if item.get("type") == "authority" and item.get("created_entry_ids"):
            continue
        missing_work = {
            "relation": "compare",
            "source_object": "extract_more",
            "severity": "assess_risk",
            "authority": "cite_authority",
        }.get(item.get("type"), "analyze")
        entries.append(Entry(
            id=_unique_entry_id(blackboard, reserved_ids),
            type="gap",
            content=f"{item['type']} debt: {item['reason']}",
            created_by=WorkerRecord("debt_sensor", item.get("subtype", ""), blackboard.iteration),
            confidence=item.get("confidence", 0.7),
            tags=[
                "debt_sensor",
                f"debt_type:{item.get('type')}",
                f"debt_subtype:{item.get('subtype')}",
                f"missing_work:{missing_work}",
                "materiality:high",
            ],
            supports_entries=item.get("parent_entry_ids", []),
        ))
    return entries


def execute_authority_debt_items(
    blackboard: Blackboard,
    caller: ModelCaller,
    items: list[dict],
) -> tuple[dict, int]:
    updated_items = [dict(item) for item in items]
    total_tokens = 0
    entries: list[Entry] = []
    reserved_ids = _entry_ids(blackboard)
    limit = int(os.getenv("SWARM_AUTHORITY_DEBT_EXECUTION_LIMIT", "8"))
    executed = 0

    for item in updated_items:
        if executed >= limit:
            break
        if item.get("type") != "authority" or item.get("status") != "actionable_gap":
            continue
        parents = blackboard.get_entries_by_ids(item.get("parent_entry_ids", []))
        if not _authority_item_executable(parents):
            item["status"] = "diagnostic_only"
            item["execution_error"] = "authority_requires_source_backed_parent"
            continue

        payload, tokens = _run_authority_worker(blackboard, caller, item, parents)
        total_tokens += tokens
        entry = _entry_from_authority_payload(
            blackboard, item, payload, parents, reserved_ids,
        )
        if entry is None:
            item["status"] = "execution_failed"
            item["execution_error"] = "worker_returned_no_valid_authority_analysis"
            continue

        entries.append(entry)
        item["status"] = "authority_executed"
        item["created_entry_ids"] = [entry.id]
        executed += 1

    return {
        "items": updated_items,
        "entries": entries,
        "summary": {
            "attempted": executed,
            "entries_created": len(entries),
            "execution_limit": limit,
        },
    }, total_tokens


def _run_authority_worker(
    blackboard: Blackboard,
    caller: ModelCaller,
    item: dict,
    parents: list[Entry],
) -> tuple[dict, int]:
    parent_text = "\n".join(_render_entry(parent) for parent in parents)
    prompt = f"""Execute one authority/evidence citation debt item.

TASK:
{blackboard.task_instruction}

AUTHORITY DEBT ITEM:
{json.dumps(item, indent=2)}

PARENT BLACKBOARD ENTRIES:
{parent_text}

Rules:
- Use only the parent blackboard entries shown here.
- Identify the exact source clause, document provision, cited standard, or evidence anchor available from the parent entries.
- Do not invent external authority, cases, statutes, standards, or source sections.
- If the parent entries do not contain enough source custody, return status "unsupported".
- This is blackboard state, not final deliverable prose.

Return JSON:
{{
  "status": "computed|unsupported",
  "content": "source-grounded authority/evidence analysis",
  "authority_label": "short clause/provision/standard/evidence label",
  "citation": "exact citation or source anchor",
  "evidence": "short evidence summary",
  "confidence": 0.0
}}"""
    return call_model(
        caller,
        prompt,
        max_tokens=4096,
        audit_context=PromptAuditContext(
            stage="authority_debt_execution",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.blackboard",
                "clean.professional_prior_dynamic",
            ],
            metadata={"debt_sensor_id": item.get("id")},
        ),
    )


def _entry_from_authority_payload(
    blackboard: Blackboard,
    item: dict,
    payload: dict,
    parents: list[Entry],
    reserved_ids: set[str] | None = None,
) -> Entry | None:
    if not isinstance(payload, dict) or payload.get("status") != "computed":
        return None
    content = str(payload.get("content", "")).strip()
    citation = str(payload.get("citation", "")).strip()
    if len(content) < 40 or not citation:
        return None
    label = str(payload.get("authority_label", "")).strip()
    if label:
        content = f"{content}\nAuthority/evidence anchor: {label} - {citation}"
    else:
        content = f"{content}\nAuthority/evidence anchor: {citation}"
    parent_ids = [entry.id for entry in parents]
    return Entry(
        id=_unique_entry_id(blackboard, reserved_ids),
        type="analysis",
        content=content,
        source=_combined_source(parents, str(payload.get("evidence", "")).strip()),
        created_by=WorkerRecord(
            "authority_debt_worker",
            f"debt_sensor:{item.get('id')}",
            blackboard.iteration,
        ),
        confidence=_safe_float(payload.get("confidence", item.get("confidence", 0.75))),
        verified=None,
        tags=[
            "debt_sensor",
            "debt_type:authority",
            f"debt_subtype:{item.get('subtype')}",
            "missing_work:provide_authority",
            "lifecycle:transformed",
            "source_grounded:true",
        ],
        status="active",
        supports_entries=parent_ids,
    )


def execute_severity_debt_items(
    blackboard: Blackboard,
    caller: ModelCaller,
    items: list[dict],
) -> tuple[dict, int]:
    updated_items = [dict(item) for item in items]
    total_tokens = 0
    entries: list[Entry] = []
    reserved_ids = _entry_ids(blackboard)
    limit = int(os.getenv("SWARM_SEVERITY_DEBT_EXECUTION_LIMIT", "8"))
    executed = 0

    for item in updated_items:
        if executed >= limit:
            break
        if item.get("type") != "severity" or item.get("status") != "actionable_gap":
            continue
        parents = blackboard.get_entries_by_ids(item.get("parent_entry_ids", []))
        if not _severity_item_executable(parents):
            item["status"] = "diagnostic_only"
            item["execution_error"] = "severity_requires_source_backed_parent"
            continue

        payload, tokens = _run_severity_worker(blackboard, caller, item, parents)
        total_tokens += tokens
        entry = _entry_from_severity_payload(
            blackboard, item, payload, parents, reserved_ids,
        )
        if entry is None:
            item["status"] = "execution_failed"
            item["execution_error"] = "worker_returned_no_valid_severity_analysis"
            continue

        entries.append(entry)
        item["status"] = "severity_executed"
        item["created_entry_ids"] = [entry.id]
        executed += 1

    return {
        "items": updated_items,
        "entries": entries,
        "summary": {
            "attempted": executed,
            "entries_created": len(entries),
            "execution_limit": limit,
        },
    }, total_tokens


def _run_severity_worker(
    blackboard: Blackboard,
    caller: ModelCaller,
    item: dict,
    parents: list[Entry],
) -> tuple[dict, int]:
    parent_text = "\n".join(_render_entry(parent) for parent in parents)
    prompt = f"""Execute one severity/recommendation debt item.

TASK:
{blackboard.task_instruction}

SEVERITY DEBT ITEM:
{json.dumps(item, indent=2)}

PARENT BLACKBOARD ENTRIES:
{parent_text}

Rules:
- Use only the parent blackboard entries shown here.
- Assign a defensible severity/priority and explain concrete consequence.
- Give a specific recommended action only when it follows from the parent entries.
- Do not invent missing source facts, legal standards, or business context.
- This is blackboard state, not final deliverable prose.

Return JSON:
{{
  "status": "computed|unsupported",
  "content": "source-grounded severity/recommendation analysis",
  "severity": "critical|high|medium|low",
  "recommendation": "specific recommended action or null",
  "evidence": "short evidence summary",
  "confidence": 0.0
}}"""
    return call_model(
        caller,
        prompt,
        max_tokens=4096,
        audit_context=PromptAuditContext(
            stage="severity_debt_execution",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.blackboard",
                "clean.professional_prior_dynamic",
            ],
            metadata={"debt_sensor_id": item.get("id")},
        ),
    )


def _entry_from_severity_payload(
    blackboard: Blackboard,
    item: dict,
    payload: dict,
    parents: list[Entry],
    reserved_ids: set[str] | None = None,
) -> Entry | None:
    if not isinstance(payload, dict) or payload.get("status") != "computed":
        return None
    content = str(payload.get("content", "")).strip()
    severity = str(payload.get("severity", "")).strip().lower()
    if len(content) < 40 or severity not in {"critical", "high", "medium", "low"}:
        return None
    recommendation = payload.get("recommendation")
    if isinstance(recommendation, str) and recommendation.strip():
        content = f"{content}\nRecommended action: {recommendation.strip()}"
    parent_ids = [entry.id for entry in parents]
    return Entry(
        id=_unique_entry_id(blackboard, reserved_ids),
        type="analysis",
        content=content,
        source=_combined_source(parents, str(payload.get("evidence", "")).strip()),
        created_by=WorkerRecord(
            "severity_debt_worker",
            f"debt_sensor:{item.get('id')}",
            blackboard.iteration,
        ),
        confidence=_safe_float(payload.get("confidence", item.get("confidence", 0.75))),
        verified=None,
        tags=[
            "debt_sensor",
            "debt_type:severity",
            f"debt_subtype:{item.get('subtype')}",
            f"severity:{severity}",
            "missing_work:assess_risk",
            "lifecycle:transformed",
            "source_grounded:true",
        ],
        status="active",
        supports_entries=parent_ids,
    )


def execute_source_object_debt_items(
    blackboard: Blackboard,
    caller: ModelCaller,
    items: list[dict],
) -> tuple[dict, int]:
    updated_items = [dict(item) for item in items]
    total_tokens = 0
    entries: list[Entry] = []
    reserved_ids = _entry_ids(blackboard)
    limit = int(os.getenv("SWARM_SOURCE_OBJECT_DEBT_EXECUTION_LIMIT", "6"))
    executed = 0

    for item in updated_items:
        if executed >= limit:
            break
        if item.get("type") != "source_object" or item.get("status") != "actionable_gap":
            continue
        docs = _target_documents(blackboard, item)
        if not docs:
            item["status"] = "diagnostic_only"
            item["execution_error"] = "no_target_source_document"
            continue
        excerpts = _source_object_excerpts(docs)
        if not excerpts:
            item["status"] = "diagnostic_only"
            item["execution_error"] = "no_source_text_available"
            continue

        payload, tokens = _run_source_object_worker(blackboard, caller, item, excerpts)
        total_tokens += tokens
        created = _entries_from_source_object_payload(
            blackboard, item, payload, reserved_ids,
        )
        if not created:
            item["status"] = "execution_failed"
            item["execution_error"] = "worker_returned_no_valid_findings"
            continue

        entries.extend(created)
        item["status"] = "source_object_executed"
        item["created_entry_ids"] = [entry.id for entry in created]
        executed += 1

    return {
        "items": updated_items,
        "entries": entries,
        "summary": {
            "attempted": executed,
            "entries_created": len(entries),
            "execution_limit": limit,
        },
    }, total_tokens


def _run_source_object_worker(
    blackboard: Blackboard,
    caller: ModelCaller,
    item: dict,
    excerpts: list[dict],
) -> tuple[dict, int]:
    prompt = f"""Execute one source/object discovery debt item by rereading source excerpts.

TASK:
{blackboard.task_instruction}

SOURCE/OBJECT DEBT ITEM:
{json.dumps(item, indent=2)}

SOURCE EXCERPTS:
{json.dumps(excerpts, indent=2)}

Rules:
- Use only the source excerpts shown here.
- Extract the missing population, entity, component, schedule item, row, or section facts requested by the debt item.
- Do not perform broad analysis and do not write the final deliverable.
- If the excerpts do not contain responsive source facts, return status "unsupported".
- Each finding must identify source_document, source_section, and short evidence.

Return JSON:
{{
  "status": "found|unsupported",
  "findings": [
    {{
      "type": "observation|analysis|calculation",
      "content": "specific source-grounded finding",
      "source_document": "document name",
      "source_section": "section name",
      "evidence": "short exact source evidence",
      "confidence": 0.0
    }}
  ]
}}"""
    return call_model(
        caller,
        prompt,
        max_tokens=8192,
        audit_context=PromptAuditContext(
            stage="source_object_debt_execution",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "source.documents",
                "swarm.blackboard",
                "clean.professional_prior_dynamic",
            ],
            metadata={"debt_sensor_id": item.get("id")},
        ),
    )


def _entries_from_source_object_payload(
    blackboard: Blackboard,
    item: dict,
    payload: dict,
    reserved_ids: set[str] | None = None,
) -> list[Entry]:
    if not isinstance(payload, dict) or payload.get("status") != "found":
        return []
    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        return []
    entries: list[Entry] = []
    for raw in raw_findings[:20]:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content", "")).strip()
        document = str(raw.get("source_document", "")).strip()
        if len(content) < 20 or not document:
            continue
        entry_type = str(raw.get("type", "observation")).strip()
        if entry_type not in {"observation", "analysis", "calculation"}:
            entry_type = "observation"
        entries.append(Entry(
            id=_unique_entry_id(blackboard, reserved_ids),
            type=entry_type,
            content=content,
            source=EntrySource(
                document=document,
                section=str(raw.get("source_section", "")).strip() or None,
                evidence=str(raw.get("evidence", "")).strip(),
            ),
            created_by=WorkerRecord(
                "source_object_debt_worker",
                f"debt_sensor:{item.get('id')}",
                blackboard.iteration,
            ),
            confidence=_safe_float(raw.get("confidence", item.get("confidence", 0.75))),
            verified=None,
            tags=[
                "debt_sensor",
                "debt_type:source_object",
                f"debt_subtype:{item.get('subtype')}",
                "missing_work:extract_more",
                "lifecycle:discovered",
                "source_grounded:true",
            ],
            status="active",
            supports_entries=item.get("parent_entry_ids", []),
        ))
    return entries


def execute_relation_debt_items(
    blackboard: Blackboard,
    caller: ModelCaller,
    items: list[dict],
) -> tuple[dict, int]:
    updated_items = [dict(item) for item in items]
    total_tokens = 0
    entries: list[Entry] = []
    reserved_ids = _entry_ids(blackboard)
    limit = int(os.getenv("SWARM_RELATION_DEBT_EXECUTION_LIMIT", "8"))
    executed = 0

    for item in updated_items:
        if executed >= limit:
            break
        if item.get("type") != "relation" or item.get("status") != "actionable_gap":
            continue
        parents = blackboard.get_entries_by_ids(item.get("parent_entry_ids", []))
        if not _relation_item_executable(parents):
            item["status"] = "diagnostic_only"
            item["execution_error"] = "relation_requires_two_source_documents"
            continue

        payload, tokens = _run_relation_worker(blackboard, caller, item, parents)
        total_tokens += tokens
        entry = _entry_from_relation_payload(
            blackboard, item, payload, parents, reserved_ids,
        )
        if entry is None:
            item["status"] = "execution_failed"
            item["execution_error"] = "worker_returned_no_valid_relation_analysis"
            continue

        entries.append(entry)
        item["status"] = "relation_executed"
        item["created_entry_ids"] = [entry.id]
        executed += 1

    return {
        "items": updated_items,
        "entries": entries,
        "summary": {
            "attempted": executed,
            "entries_created": len(entries),
            "execution_limit": limit,
        },
    }, total_tokens


def _run_relation_worker(
    blackboard: Blackboard,
    caller: ModelCaller,
    item: dict,
    parents: list[Entry],
) -> tuple[dict, int]:
    parent_text = "\n".join(_render_entry(parent) for parent in parents)
    prompt = f"""Execute one cross-document relation debt item.

TASK:
{blackboard.task_instruction}

RELATION DEBT ITEM:
{json.dumps(item, indent=2)}

PARENT BLACKBOARD ENTRIES:
{parent_text}

Rules:
- Use only the parent blackboard entries shown here.
- Compare, reconcile, classify conflict, align dates/entities/provisions, or explain interplay as requested.
- Preserve exact values, dates, parties, and source caveats.
- Do not infer missing source facts. If the parent entries are insufficient, return status "unsupported".
- This is blackboard state, not final deliverable prose.

Return JSON:
{{
  "status": "computed|unsupported",
  "content": "source-grounded relation analysis",
  "relation_type": "conflict|reconciliation|date_alignment|entity_alignment|provision_interplay",
  "evidence": "short evidence summary",
  "confidence": 0.0
}}"""
    return call_model(
        caller,
        prompt,
        max_tokens=4096,
        audit_context=PromptAuditContext(
            stage="relation_debt_execution",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.blackboard",
                "clean.professional_prior_dynamic",
            ],
            metadata={"debt_sensor_id": item.get("id")},
        ),
    )


def _entry_from_relation_payload(
    blackboard: Blackboard,
    item: dict,
    payload: dict,
    parents: list[Entry],
    reserved_ids: set[str] | None = None,
) -> Entry | None:
    if not isinstance(payload, dict) or payload.get("status") != "computed":
        return None
    content = str(payload.get("content", "")).strip()
    if len(content) < 40:
        return None
    parent_ids = [entry.id for entry in parents]
    subtype = str(item.get("subtype", "")).strip() or str(
        payload.get("relation_type", "")
    ).strip()
    return Entry(
        id=_unique_entry_id(blackboard, reserved_ids),
        type="analysis",
        content=content,
        source=_combined_source(parents, str(payload.get("evidence", "")).strip()),
        created_by=WorkerRecord(
            "relation_debt_worker",
            f"debt_sensor:{item.get('id')}",
            blackboard.iteration,
        ),
        confidence=_safe_float(payload.get("confidence", item.get("confidence", 0.75))),
        verified=None,
        tags=[
            "debt_sensor",
            "debt_type:relation",
            f"debt_subtype:{subtype}",
            "missing_work:compare",
            "lifecycle:transformed",
            "source_grounded:true",
        ],
        status="active",
        supports_entries=parent_ids,
    )


def write_debt_sensor_report(output_dir: str, report: dict) -> None:
    if not output_dir:
        return
    swarm_dir = Path(output_dir) / "swarm"
    swarm_dir.mkdir(parents=True, exist_ok=True)
    (swarm_dir / "debt_sensors.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )


def _debt_sensor_mode() -> str:
    if debt_sensors_detect_only():
        return "detect_only"
    modes = []
    if relation_debt_execute_enabled():
        modes.append("relation")
    if source_object_debt_execute_enabled():
        modes.append("source_object")
    if severity_debt_execute_enabled():
        modes.append("severity")
    if authority_debt_execute_enabled():
        modes.append("authority")
    if modes:
        return "execute_" + "_and_".join(modes) + "_debt"
    return "materialize_gaps"


def _prioritized_entries(entries: list[Entry]) -> list[Entry]:
    active = [entry for entry in entries if entry.status == "active"]
    limit = int(os.getenv("SWARM_DEBT_SENSOR_ENTRY_LIMIT", "140"))

    def score(entry: Entry) -> tuple[int, float, int]:
        tags = entry.tags or []
        tag_score = 2 if any(
            tag.startswith(("state_conversion", "plan_coverage", "missing_work:", "materiality:"))
            for tag in tags
        ) else 0
        type_score = {
            "analysis": 4,
            "calculation": 4,
            "gap": 3,
            "strategy": 2,
            "observation": 1,
        }.get(entry.type, 0)
        cross_doc_hint = 1 if entry.supports_entries else 0
        return (type_score + tag_score + cross_doc_hint, entry.confidence, len(entry.content))

    return sorted(active, key=score, reverse=True)[:limit]


def _render_entry(entry: Entry) -> str:
    source = ""
    if entry.source and entry.source.document:
        source = f" source={entry.source.document}/{entry.source.section or ''}"
    tags = ",".join((entry.tags or [])[:5])
    supports = ",".join((entry.supports_entries or [])[:5])
    return (
        f"[{entry.id}] type={entry.type} conf={entry.confidence:.2f}"
        f"{source} tags={tags} supports={supports}\n{entry.content[:900]}"
    )


def _render_entries(entries: list[Entry]) -> str:
    parts = []
    for entry in entries:
        source = ""
        if entry.source and entry.source.document:
            source = f" source={entry.source.document}/{entry.source.section or ''}"
        tags = ",".join((entry.tags or [])[:5])
        supports = ",".join((entry.supports_entries or [])[:5])
        parts.append(
            f"[{entry.id}] type={entry.type} conf={entry.confidence:.2f}"
            f"{source} tags={tags} supports={supports}\n{entry.content[:650]}"
        )
    return "\n".join(parts)


def _relation_item_executable(parents: list[Entry]) -> bool:
    documents = {
        entry.source.document
        for entry in parents
        if entry.source and entry.source.document
    }
    return len(parents) >= 2 and len(documents) >= 2


def _severity_item_executable(parents: list[Entry]) -> bool:
    return any(entry.source and entry.source.document for entry in parents)


def _authority_item_executable(parents: list[Entry]) -> bool:
    return any(
        entry.source and entry.source.document and entry.source.evidence
        for entry in parents
    )


def _combined_source(parents: list[Entry], evidence: str) -> EntrySource | None:
    sourced = [entry for entry in parents if entry.source and entry.source.document]
    documents = _dedupe([
        entry.source.document
        for entry in sourced
    ])
    if not documents:
        return None
    first = sourced[0].source
    return EntrySource(
        document="; ".join(documents[:6]),
        section="multiple" if len(documents) > 1 else first.section,
        evidence=evidence or "Relation analysis from source-grounded parent entries.",
    )


def _target_documents(blackboard: Blackboard, item: dict):
    requested = [name.lower() for name in item.get("target_documents", []) if name]
    parent_docs = {
        entry.source.document.lower()
        for entry in blackboard.get_entries_by_ids(item.get("parent_entry_ids", []))
        if entry.source and entry.source.document
    }
    matches = []
    for doc in blackboard.documents:
        name_l = doc.name.lower()
        if not requested and not parent_docs:
            matches.append(doc)
        elif any(req in name_l or name_l in req for req in requested):
            matches.append(doc)
        elif name_l in parent_docs:
            matches.append(doc)
    return matches[: int(os.getenv("SWARM_SOURCE_OBJECT_TARGET_DOC_LIMIT", "3"))]


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
    raise RuntimeError("could not allocate unique debt sensor entry id")


def _source_object_excerpts(documents) -> list[dict]:
    excerpts: list[dict] = []
    max_sections = int(os.getenv("SWARM_SOURCE_OBJECT_SECTIONS_PER_DOC", "4"))
    max_chars = int(os.getenv("SWARM_SOURCE_OBJECT_SECTION_CHARS", "12000"))
    for doc in documents:
        if not doc.is_loaded:
            continue
        section_names = list(doc.sections_unread[:max_sections])
        if not section_names and doc.section_index:
            section_names = [s.name for s in doc.section_index.sections[:max_sections]]
        if not section_names:
            section_names = ["Full Document"]
        for section_name in section_names[:max_sections]:
            if doc.section_index:
                text = resolve_section_text(
                    doc.text, doc.section_index, section_name, max_chars=max_chars,
                )
            else:
                text = doc.text[:max_chars]
            if not text.strip():
                continue
            excerpts.append({
                "document": doc.name,
                "section": section_name,
                "text": text,
            })
    return excerpts


def _format_list(values: list) -> str:
    items = [str(value).strip() for value in values if str(value).strip()]
    return "\n".join(f"- {item}" for item in items[:12]) or "None"


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _env_on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
