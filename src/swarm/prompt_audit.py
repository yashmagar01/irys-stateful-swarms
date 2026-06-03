from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


FORBIDDEN_PROVENANCE = {
    "benchmark.scoring_criteria",
    "benchmark.match_criteria",
    "benchmark.scorer_output",
    "benchmark.prior_score",
    "benchmark.expected_answer",
    "benchmark.criteria_derived_deliverables",
    "task_id_routing",
}

FORBIDDEN_TEXT_MARKERS = (
    "match_criteria",
    "scorer_output",
    "prior failed criteria",
    "prior passed criteria",
)

_audit_lock = threading.Lock()


@dataclass
class PromptAuditContext:
    stage: str
    output_dir: str = ""
    provenance: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def audit_enabled() -> bool:
    return os.getenv("SWARM_PROMPT_AUDIT", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def audit_prompt(prompt: str, context: PromptAuditContext | dict | None) -> dict:
    """Record provenance-aware prompt audit metadata.

    This intentionally audits forbidden provenance and a few high-risk literal
    markers. It does not ban clean generated words like "criteria".
    """
    ctx = _normalize_context(context)
    provenance = list(ctx.provenance)
    forbidden_provenance = sorted(set(provenance) & FORBIDDEN_PROVENANCE)
    text_lower = prompt.lower()
    forbidden_text = [
        marker for marker in FORBIDDEN_TEXT_MARKERS
        if marker.lower() in text_lower
    ]
    record = {
        "stage": ctx.stage,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_chars": len(prompt),
        "provenance": provenance,
        "forbidden_provenance_hits": forbidden_provenance,
        "forbidden_text_hits": forbidden_text,
        "metadata": ctx.metadata,
        "redacted_excerpt": prompt[:500],
    }
    if audit_enabled() and ctx.output_dir:
        _append_record(ctx.output_dir, record)
    return record


def _normalize_context(context: PromptAuditContext | dict | None) -> PromptAuditContext:
    if isinstance(context, PromptAuditContext):
        return context
    if isinstance(context, dict):
        provenance = context.get("provenance", [])
        if not isinstance(provenance, list):
            provenance = [str(provenance)]
        metadata = context.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {"value": metadata}
        return PromptAuditContext(
            stage=str(context.get("stage", "")),
            output_dir=str(context.get("output_dir", "")),
            provenance=[str(p) for p in provenance],
            metadata=metadata,
        )
    return PromptAuditContext(stage="")


def _append_record(output_dir: str, record: dict) -> None:
    swarm_dir = Path(output_dir) / "swarm"
    swarm_dir.mkdir(parents=True, exist_ok=True)
    path = swarm_dir / "prompt_audit.json"
    with _audit_lock:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {"schema_version": 1, "records": []}
        else:
            data = {"schema_version": 1, "records": []}
        records = data.setdefault("records", [])
        records.append(record)
        data["summary"] = summarize_records(records)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def summarize_records(records: list[dict]) -> dict:
    forbidden_provenance_hits = 0
    forbidden_text_hits = 0
    stages: dict[str, int] = {}
    for record in records:
        forbidden_provenance_hits += len(record.get("forbidden_provenance_hits") or [])
        forbidden_text_hits += len(record.get("forbidden_text_hits") or [])
        stage = str(record.get("stage") or "unknown")
        stages[stage] = stages.get(stage, 0) + 1
    return {
        "records": len(records),
        "forbidden_provenance_hits": forbidden_provenance_hits,
        "forbidden_text_hits": forbidden_text_hits,
        "stages": stages,
    }
