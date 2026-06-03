from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .blackboard import Blackboard
from .models import Entry, ModelCaller, WorkerRecord, gen_entry_id
from .prompt_audit import PromptAuditContext
from .worker_dispatch import begin_call_model_usage, call_model, end_call_model_usage


def debt_sensors_enabled() -> bool:
    return relation_debt_enabled() or source_object_debt_enabled()


def relation_debt_enabled() -> bool:
    return _env_on("SWARM_ENABLE_RELATION_DEBT")


def source_object_debt_enabled() -> bool:
    return _env_on("SWARM_ENABLE_SOURCE_OBJECT_DEBT")


def debt_sensors_detect_only() -> bool:
    return _env_on("SWARM_DEBT_SENSORS_DETECT_ONLY")


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

        report = {
            "schema_version": 1,
            "mode": "detect_only" if debt_sensors_detect_only() else "materialize_gaps",
            "items": normalize_debt_sensor_items(items),
        }
        report["summary"] = summarize_debt_sensor_items(report["items"])
        if not debt_sensors_detect_only():
            entries = debt_sensor_items_to_gap_entries(report["items"], blackboard)
            blackboard.add_entries_batch(entries)
            report["created_entry_ids"] = [entry.id for entry in entries]
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


def normalize_debt_sensor_items(raw_items: list[Any]) -> list[dict]:
    normalized = []
    seen = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_type = str(raw.get("type", "")).strip()
        if item_type not in {"relation", "source_object"}:
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


def debt_sensor_items_to_gap_entries(
    items: list[dict],
    blackboard: Blackboard,
) -> list[Entry]:
    entries = []
    for item in items:
        if item.get("status") != "actionable_gap":
            continue
        missing_work = "compare" if item.get("type") == "relation" else "extract_more"
        entries.append(Entry(
            id=gen_entry_id(),
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


def write_debt_sensor_report(output_dir: str, report: dict) -> None:
    if not output_dir:
        return
    swarm_dir = Path(output_dir) / "swarm"
    swarm_dir.mkdir(parents=True, exist_ok=True)
    (swarm_dir / "debt_sensors.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )


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


def _format_list(values: list) -> str:
    items = [str(value).strip() for value in values if str(value).strip()]
    return "\n".join(f"- {item}" for item in items[:12]) or "None"


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _env_on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
