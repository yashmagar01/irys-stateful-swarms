from __future__ import annotations

import json
import os
from pathlib import Path

from .blackboard import Blackboard
from .models import Entry
from .verification import extract_verification_targets


def artifact_commitments_enabled() -> bool:
    return _env_on("SWARM_ENABLE_ARTIFACT_COMMITMENTS")


def build_artifact_commitments(
    blackboard: Blackboard,
    deliverables_map: dict,
) -> list[dict]:
    """Build file/native-form commitments from existing source-backed state."""
    if not artifact_commitments_enabled():
        return []
    active = [entry for entry in blackboard.entries if entry.status == "active"]
    filenames = _explicit_filenames(deliverables_map)
    commitments = []
    for entry in _candidate_entries(active):
        filename = _target_file(entry, filenames)
        native_form = _native_form(filename, entry)
        commitments.append({
            "entry_id": entry.id,
            "importance": "critical" if _is_high_materiality(entry) else "high",
            "section": _section_for_entry(entry, native_form),
            "summary": _summary_for_entry(entry, filename, native_form),
            "obligation_type": "artifact_native_commitment",
            "verification_terms": _verification_terms(entry),
            "source": "artifact_commitment",
            "target_file": filename,
            "native_form": native_form,
        })
    write_artifact_commitment_report(blackboard.output_dir, commitments)
    return commitments


def write_artifact_commitment_report(output_dir: str, commitments: list[dict]) -> None:
    if not output_dir:
        return
    swarm_dir = Path(output_dir) / "swarm"
    swarm_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "items": commitments,
        "summary": {
            "selected": len(commitments),
            "targeted": sum(1 for item in commitments if item.get("target_file")),
            "native_forms": _counts(item.get("native_form", "") for item in commitments),
        },
    }
    (swarm_dir / "artifact_commitments.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )


def _candidate_entries(entries: list[Entry]) -> list[Entry]:
    selected = []
    seen = set()
    for entry in sorted(entries, key=_candidate_score, reverse=True):
        if entry.id in seen:
            continue
        if not _is_candidate(entry):
            continue
        selected.append(entry)
        seen.add(entry.id)
        if len(selected) >= int(os.getenv("SWARM_ARTIFACT_COMMITMENT_LIMIT", "30")):
            break
    return selected


def _is_candidate(entry: Entry) -> bool:
    if not entry.source or not entry.source.document:
        return False
    tags = entry.tags or []
    if any(tag.startswith(("state_conversion", "plan_coverage", "plan_coverage_repair")) for tag in tags):
        return True
    if any(tag.startswith(("missing_work:", "materiality:critical", "materiality:high")) for tag in tags):
        return True
    if entry.type == "calculation" and extract_verification_targets(entry.content):
        return True
    return False


def _candidate_score(entry: Entry) -> tuple[int, float, int]:
    tags = entry.tags or []
    materiality = 0
    if any(tag == "materiality:critical" for tag in tags):
        materiality = 3
    elif any(tag == "materiality:high" for tag in tags):
        materiality = 2
    type_score = {
        "calculation": 4,
        "analysis": 3,
        "strategy": 2,
        "observation": 1,
    }.get(entry.type, 0)
    return (materiality + type_score, entry.confidence, len(entry.content))


def _is_high_materiality(entry: Entry) -> bool:
    return any(
        tag in {"materiality:critical", "materiality:high"}
        for tag in (entry.tags or [])
    )


def _explicit_filenames(deliverables_map: dict) -> list[str]:
    if not isinstance(deliverables_map, dict):
        return []
    filenames = []
    for filename in deliverables_map.values():
        if isinstance(filename, str) and filename and filename not in filenames:
            filenames.append(filename)
    return filenames


def _target_file(entry: Entry, filenames: list[str]) -> str:
    if not filenames:
        return ""
    if len(filenames) == 1:
        return filenames[0]
    lower_content = entry.content.lower()
    if entry.type == "calculation":
        for filename in filenames:
            lower = filename.lower()
            if lower.endswith((".xlsx", ".csv")) or any(
                word in lower for word in ("model", "workbook", "schedule", "tracker")
            ):
                return filename
    if any(word in lower_content for word in ("slide", "deck", "presentation")):
        for filename in filenames:
            if filename.lower().endswith(".pptx") or "deck" in filename.lower():
                return filename
    if any(word in lower_content for word in ("clause", "redline", "markup", "revise")):
        for filename in filenames:
            if any(word in filename.lower() for word in ("redline", "markup", "rider")):
                return filename
    for preferred in ("memo", "analysis", "report", "summary"):
        for filename in filenames:
            if preferred in filename.lower():
                return filename
    return filenames[0]


def _native_form(filename: str, entry: Entry) -> str:
    lower = filename.lower()
    if lower.endswith((".xlsx", ".csv")):
        return "workbook_row"
    if lower.endswith(".pptx") or "deck" in lower:
        return "slide_bullet"
    if any(word in lower for word in ("redline", "markup", "rider")):
        return "drafting_clause"
    if entry.type == "calculation":
        return "calculation_statement"
    return "memo_statement"


def _section_for_entry(entry: Entry, native_form: str) -> str:
    if native_form == "workbook_row":
        return "Sheet: Required Calculations" if entry.type == "calculation" else "Sheet: Required Findings"
    if native_form == "slide_bullet":
        return "Required Slides"
    if native_form == "drafting_clause":
        return "Required Drafting Changes"
    if entry.type == "calculation":
        return "Required Calculations"
    return "Required Findings"


def _summary_for_entry(entry: Entry, filename: str, native_form: str) -> str:
    target = f" in {filename}" if filename else ""
    return (
        f"Represent source-backed entry {entry.id}{target} as {native_form}: "
        f"{entry.content}"
    )


def _verification_terms(entry: Entry) -> list[str]:
    terms = []
    for target in extract_verification_targets(entry.content):
        raw = target.get("raw")
        if raw and raw not in terms:
            terms.append(raw)
    if entry.source and entry.source.evidence:
        evidence = entry.source.evidence.strip()
        if evidence and evidence not in terms:
            terms.append(evidence[:200])
    return terms[:8]


def _counts(values) -> dict:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _env_on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
