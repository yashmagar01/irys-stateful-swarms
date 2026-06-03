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
    return relation_debt_enabled() or source_object_debt_enabled()


def relation_debt_enabled() -> bool:
    return _env_on("SWARM_ENABLE_RELATION_DEBT")


def source_object_debt_enabled() -> bool:
    return _env_on("SWARM_ENABLE_SOURCE_OBJECT_DEBT")


def debt_sensors_detect_only() -> bool:
    return _env_on("SWARM_DEBT_SENSORS_DETECT_ONLY")


def relation_debt_execute_enabled() -> bool:
    return _env_on("SWARM_RELATION_DEBT_EXECUTE")


def source_object_debt_execute_enabled() -> bool:
    return _env_on("SWARM_SOURCE_OBJECT_DEBT_EXECUTE")


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

        normalized_items = normalize_debt_sensor_items(items)
        report = {
            "schema_version": 1,
            "mode": _debt_sensor_mode(),
            "items": normalized_items,
        }
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

            gap_entries = debt_sensor_items_to_gap_entries(report["items"], blackboard)
            if gap_entries:
                blackboard.add_entries_batch(gap_entries)
            report["created_relation_entry_ids"] = [entry.id for entry in relation_entries]
            report["created_source_object_entry_ids"] = [entry.id for entry in source_entries]
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
        if item.get("type") == "relation" and item.get("created_entry_ids"):
            continue
        if item.get("type") == "source_object" and item.get("created_entry_ids"):
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


def execute_source_object_debt_items(
    blackboard: Blackboard,
    caller: ModelCaller,
    items: list[dict],
) -> tuple[dict, int]:
    updated_items = [dict(item) for item in items]
    total_tokens = 0
    entries: list[Entry] = []
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
        created = _entries_from_source_object_payload(blackboard, item, payload)
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
            id=gen_entry_id(),
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
        entry = _entry_from_relation_payload(blackboard, item, payload, parents)
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
        id=gen_entry_id(),
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


def _source_object_excerpts(documents) -> list[dict]:
    excerpts: list[dict] = []
    max_sections = int(os.getenv("SWARM_SOURCE_OBJECT_SECTIONS_PER_DOC", "4"))
    max_chars = int(os.getenv("SWARM_SOURCE_OBJECT_SECTION_CHARS", "12000"))
    for doc in documents:
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
