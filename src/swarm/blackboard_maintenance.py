from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .blackboard import Blackboard
from .models import Entry, EntrySource, ModelCaller, WorkerRecord, gen_entry_id
from .prompt_audit import PromptAuditContext
from .worker_dispatch import begin_call_model_usage, call_model, end_call_model_usage


def blackboard_maintenance_enabled() -> bool:
    return _env_on("SWARM_ENABLE_BLACKBOARD_MAINTENANCE") or _env_on(
        "SWARM_ENABLE_BLACKBOARD_COMPACTION"
    )


def blackboard_maintenance_supersede_enabled() -> bool:
    return _env_on("SWARM_BLACKBOARD_MAINTENANCE_SUPERSEDE")


def run_blackboard_maintenance(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
) -> tuple[dict, int]:
    """Consolidate repeated active state before obligations and synthesis.

    This is a state-quality pass, not final-output repair. It creates compact,
    source-grounded analysis/calculation entries that preserve parent lineage.
    Superseding source entries is opt-in so early validation can measure the
    pass without risking source loss.
    """
    begin_call_model_usage()
    try:
        candidates = _maintenance_candidates(blackboard.entries)
        before = _active_type_counts(blackboard.entries)
        if len(candidates) < 2:
            report = _empty_report(before)
            write_blackboard_maintenance_report(blackboard.output_dir, report)
            return report, 0

        prompt = _build_maintenance_prompt(blackboard, seed, candidates)
        payload, tokens = call_model(
            caller,
            prompt,
            max_tokens=8192,
            audit_context=PromptAuditContext(
                stage="blackboard_maintenance",
                output_dir=blackboard.output_dir,
                provenance=[
                    "user.instruction",
                    "swarm.seed_generated",
                    "swarm.blackboard",
                    "clean.professional_prior_dynamic",
                ],
                metadata={"candidate_entry_count": len(candidates)},
            ),
        )
        consolidations = normalize_consolidations(
            payload.get("consolidations", []),
            candidates,
        )
        fallback_used = False
        fallback_cluster_count = 0
        if not consolidations:
            clusters = _source_local_clusters(candidates)
            fallback_cluster_count = len(clusters)
            if clusters:
                fallback_used = True
                fallback_prompt = _build_clustered_maintenance_prompt(
                    blackboard,
                    seed,
                    candidates,
                    clusters,
                )
                fallback_payload, fallback_tokens = call_model(
                    caller,
                    fallback_prompt,
                    max_tokens=8192,
                    audit_context=PromptAuditContext(
                        stage="blackboard_maintenance_fallback",
                        output_dir=blackboard.output_dir,
                        provenance=[
                            "user.instruction",
                            "swarm.seed_generated",
                            "swarm.blackboard",
                            "clean.professional_prior_dynamic",
                        ],
                        metadata={
                            "candidate_entry_count": len(candidates),
                            "fallback_cluster_count": fallback_cluster_count,
                        },
                    ),
                )
                tokens += fallback_tokens
                consolidations = normalize_consolidations(
                    fallback_payload.get("consolidations", []),
                    candidates,
                )
        entries = consolidation_entries(
            blackboard,
            consolidations,
            supersede=blackboard_maintenance_supersede_enabled(),
        )
        if entries:
            blackboard.add_entries_batch(entries)

        after = _active_type_counts(blackboard.entries)
        report = {
            "schema_version": 1,
            "mode": (
                "consolidate_and_supersede"
                if blackboard_maintenance_supersede_enabled()
                else "consolidate_only"
            ),
            "candidate_entry_count": len(candidates),
            "consolidations": consolidations,
            "created_entry_ids": [entry.id for entry in entries],
            "superseded_entry_ids": sorted({
                sid for entry in entries for sid in entry.supersedes_entries
            }),
            "summary": {
                "before_active_type_counts": before,
                "after_active_type_counts": after,
                "consolidations_selected": len(consolidations),
                "entries_created": len(entries),
                "entries_superseded": sum(len(e.supersedes_entries) for e in entries),
                "fallback_used": fallback_used,
                "fallback_cluster_count": fallback_cluster_count,
            },
        }
        write_blackboard_maintenance_report(blackboard.output_dir, report)
        return report, tokens
    finally:
        end_call_model_usage()


def normalize_consolidations(
    raw_consolidations: list[Any],
    candidates: list[Entry],
) -> list[dict]:
    by_id = {entry.id: entry for entry in candidates}
    normalized: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for raw in raw_consolidations:
        if not isinstance(raw, dict):
            continue
        source_ids = [
            str(value).strip()
            for value in raw.get("source_entry_ids", [])
            if str(value).strip() in by_id
        ]
        source_ids = _dedupe(source_ids)
        if len(source_ids) < 2:
            continue
        key = tuple(sorted(source_ids))
        if key in seen:
            continue
        seen.add(key)

        content = str(raw.get("content", "")).strip()
        if len(content) < 40:
            continue
        if not any(by_id[sid].source and by_id[sid].source.document for sid in source_ids):
            continue

        entry_type = str(raw.get("type", "analysis")).strip()
        if entry_type not in {"analysis", "calculation", "strategy"}:
            entry_type = "analysis"

        normalized.append({
            "id": f"bm_{len(normalized) + 1:03d}",
            "type": entry_type,
            "content": content,
            "source_entry_ids": source_ids,
            "reason": str(raw.get("reason", "")).strip(),
            "confidence": _safe_float(raw.get("confidence", 0.75), default=0.75),
            "supersede_source_entries": bool(raw.get("supersede_source_entries", False)),
        })
    return normalized


def consolidation_entries(
    blackboard: Blackboard,
    consolidations: list[dict],
    *,
    supersede: bool = False,
) -> list[Entry]:
    entries: list[Entry] = []
    for item in consolidations:
        source_ids = item.get("source_entry_ids") or []
        parents = blackboard.get_entries_by_ids(source_ids)
        if len(parents) < 2:
            continue
        source = _consolidated_source(parents)
        tags = [
            "blackboard_maintenance",
            "maintenance_type:consolidation",
            "lifecycle:compacted",
            "source_grounded:true",
        ]
        supersedes_entries = (
            source_ids
            if supersede and item.get("supersede_source_entries")
            else []
        )
        entries.append(Entry(
            id=gen_entry_id(),
            type=item.get("type", "analysis"),
            content=item.get("content", ""),
            source=source,
            created_by=WorkerRecord(
                "blackboard_maintenance",
                f"consolidation:{item.get('id')}",
                blackboard.iteration,
            ),
            confidence=min(0.98, max(0.1, _safe_float(item.get("confidence"), 0.75))),
            verified=None,
            tags=tags,
            status="active",
            supports_entries=source_ids,
            supersedes_entries=supersedes_entries,
        ))
    return entries


def write_blackboard_maintenance_report(output_dir: str, report: dict) -> None:
    if not output_dir:
        return
    swarm_dir = Path(output_dir) / "swarm"
    swarm_dir.mkdir(parents=True, exist_ok=True)
    (swarm_dir / "blackboard_maintenance.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )


def _build_maintenance_prompt(
    blackboard: Blackboard,
    seed: dict,
    candidates: list[Entry],
) -> str:
    key_questions = "\n".join(
        f"- {q}" for q in seed.get("key_questions", [])[:10]
    )
    return f"""You are maintaining a legal-analysis blackboard before final obligations.

TASK:
{blackboard.task_instruction}

SEED KEY QUESTIONS:
{key_questions or "None"}

ACTIVE BLACKBOARD CANDIDATES:
{_render_entries(candidates)}

Find clusters of repeated, overlapping, or fragmented entries that should be
consolidated into stronger source-grounded analytical state.

Rules:
- Do NOT write the final deliverable.
- Do NOT introduce new facts beyond the listed entries.
- Prefer consolidations that merge 2-6 entries into one clearer analysis or calculation.
- Preserve exact values, dates, party names, and source caveats.
- Only set supersede_source_entries=true when the new entry fully preserves the source entries' useful information.
- Return at most 12 consolidations. If no consolidation is safe, return an empty list.

Return JSON:
{{"consolidations": [
  {{
    "type": "analysis|calculation|strategy",
    "content": "consolidated source-grounded blackboard entry",
    "source_entry_ids": ["e1", "e2"],
    "reason": "why these entries are redundant or fragmented",
    "confidence": 0.0,
    "supersede_source_entries": false
  }}
]}}
"""


def _build_clustered_maintenance_prompt(
    blackboard: Blackboard,
    seed: dict,
    candidates: list[Entry],
    clusters: list[dict],
) -> str:
    key_questions = "\n".join(
        f"- {q}" for q in seed.get("key_questions", [])[:10]
    )
    by_id = {entry.id: entry for entry in candidates}
    rendered_clusters = []
    for cluster in clusters:
        entries = [by_id[eid] for eid in cluster["entry_ids"] if eid in by_id]
        if not entries:
            continue
        rendered_clusters.append(
            f"### {cluster['id']}: {cluster['label']}\n"
            f"Reason for review: {cluster['reason']}\n"
            f"{_render_entries(entries)}"
        )
    return f"""You are doing a second blackboard-maintenance pass.

The first pass returned no consolidations. The clusters below were grouped
mechanically by source locality to make repeated or fragmented state easier to
inspect. The grouping is only a review aid; you must decide whether any
consolidation is actually safe.

TASK:
{blackboard.task_instruction}

SEED KEY QUESTIONS:
{key_questions or "None"}

SOURCE-LOCAL CLUSTERS:
{chr(10).join(rendered_clusters)}

Rules:
- Do NOT write the final deliverable.
- Do NOT introduce new facts beyond the listed entries.
- Prefer consolidations that merge 3-6 entries into one stronger analysis, calculation, or strategy.
- Preserve exact values, dates, party names, and source caveats.
- Return an empty list if the listed entries are merely adjacent, not meaningfully redundant or fragmented.
- Return at most 8 consolidations.

Return JSON:
{{"consolidations": [
  {{
    "type": "analysis|calculation|strategy",
    "content": "consolidated source-grounded blackboard entry",
    "source_entry_ids": ["e1", "e2", "e3"],
    "reason": "why these entries should become one stronger piece of state",
    "confidence": 0.0,
    "supersede_source_entries": false
  }}
]}}
"""


def _maintenance_candidates(entries: list[Entry]) -> list[Entry]:
    active = [
        entry for entry in entries
        if entry.status == "active"
        and entry.type in {"observation", "analysis", "calculation", "strategy", "gap"}
        and len(entry.content.strip()) >= 30
    ]
    limit = int(os.getenv("SWARM_BLACKBOARD_MAINTENANCE_ENTRY_LIMIT", "180"))

    def score(entry: Entry) -> tuple[int, float, int]:
        tags = entry.tags or []
        type_score = {
            "analysis": 5,
            "calculation": 5,
            "gap": 4,
            "strategy": 3,
            "observation": 2,
        }.get(entry.type, 1)
        source_score = 2 if entry.source and entry.source.document else 0
        maintenance_score = -4 if "blackboard_maintenance" in tags else 0
        support_score = 1 if entry.supports_entries else 0
        return (
            type_score + source_score + support_score + maintenance_score,
            entry.confidence,
            min(len(entry.content), 1200),
        )

    return sorted(active, key=score, reverse=True)[:limit]


def _source_local_clusters(
    entries: list[Entry],
    *,
    max_clusters: int = 18,
    min_size: int = 3,
    max_size: int = 6,
) -> list[dict]:
    grouped: dict[tuple[str, str], list[Entry]] = {}
    for entry in entries:
        if "blackboard_maintenance" in (entry.tags or []):
            continue
        if not entry.source or not entry.source.document:
            continue
        key = (
            entry.source.document,
            entry.source.section or "",
        )
        grouped.setdefault(key, []).append(entry)

    def entry_score(entry: Entry) -> tuple[int, float, int]:
        return (
            {
                "analysis": 5,
                "calculation": 5,
                "strategy": 4,
                "gap": 3,
                "observation": 2,
            }.get(entry.type, 1),
            entry.confidence,
            min(len(entry.content), 1200),
        )

    def group_score(item: tuple[tuple[str, str], list[Entry]]) -> tuple[int, float, int]:
        _key, group = item
        analytical = sum(1 for entry in group if entry.type in {"analysis", "calculation", "strategy"})
        confidence = sum(entry.confidence for entry in group) / max(len(group), 1)
        return (analytical, confidence, len(group))

    clusters = []
    for index, ((document, section), group) in enumerate(
        sorted(grouped.items(), key=group_score, reverse=True),
        1,
    ):
        if len(group) < min_size:
            continue
        selected = sorted(group, key=entry_score, reverse=True)[:max_size]
        label = document
        if section:
            label = f"{label} / {section}"
        clusters.append({
            "id": f"cluster_{index:03d}",
            "label": label,
            "reason": "Multiple active entries share the same source location and may be fragmented.",
            "entry_ids": [entry.id for entry in selected],
        })
        if len(clusters) >= max_clusters:
            break
    return clusters


def _render_entries(entries: list[Entry]) -> str:
    rendered = []
    for entry in entries:
        source = ""
        if entry.source and entry.source.document:
            source = f" source={entry.source.document}/{entry.source.section or ''}"
        supports = ",".join((entry.supports_entries or [])[:6])
        tags = ",".join((entry.tags or [])[:5])
        rendered.append(
            f"[{entry.id}] type={entry.type} conf={entry.confidence:.2f}"
            f"{source} supports={supports} tags={tags}\n"
            f"{entry.content[:900]}"
        )
    return "\n".join(rendered)


def _consolidated_source(parents: list[Entry]) -> EntrySource | None:
    sourced = [entry for entry in parents if entry.source and entry.source.document]
    if not sourced:
        return None
    documents = _dedupe([entry.source.document for entry in sourced if entry.source])
    if len(documents) == 1:
        first = sourced[0].source
        return EntrySource(
            document=first.document,
            section=first.section,
            evidence=first.evidence,
        )
    return EntrySource(
        document="; ".join(documents[:6]),
        section="multiple",
        evidence="Consolidated from multiple source-grounded entries.",
    )


def _active_type_counts(entries: list[Entry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        if entry.status != "active":
            continue
        counts[entry.type] = counts.get(entry.type, 0) + 1
    return counts


def _empty_report(before: dict[str, int]) -> dict:
    return {
        "schema_version": 1,
        "mode": "consolidate_only",
        "candidate_entry_count": 0,
        "consolidations": [],
        "created_entry_ids": [],
        "superseded_entry_ids": [],
        "summary": {
            "before_active_type_counts": before,
            "after_active_type_counts": before,
            "consolidations_selected": 0,
            "entries_created": 0,
            "entries_superseded": 0,
        },
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
