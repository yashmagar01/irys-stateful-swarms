from __future__ import annotations

import json
import re
from pathlib import Path

from .blackboard import Blackboard
from .models import Entry


SOURCE_CUSTODY_STATUS = "source_quarantined"
SYNTHETIC_SOURCE_NAMES = {"cross_cutting", "multiple", "multi_document"}


def enforce_source_custody(
    blackboard: Blackboard,
    stage: str,
) -> dict:
    """Quarantine active entries that claim non-existent source documents."""
    valid_docs = _valid_document_names(blackboard)
    invalid_doc_names: set[str] = set()
    quarantined_ids: set[str] = set()
    items: list[dict] = []

    for entry in list(blackboard.entries):
        if entry.status != "active":
            continue
        invalid = _invalid_source_documents(entry, valid_docs)
        if not invalid:
            continue
        _quarantine_entry(entry)
        invalid_doc_names.update(invalid)
        quarantined_ids.add(entry.id)
        items.append(_item(entry, stage, "invalid_source_document", invalid, []))

    # Cascade once through derived/cross-cutting entries that rely on invalid
    # source entries or restate fake document names without a valid direct source.
    for _ in range(3):
        changed = False
        for entry in list(blackboard.entries):
            if entry.status != "active":
                continue
            supported_bad = [
                entry_id for entry_id in entry.supports_entries
                if entry_id in quarantined_ids
            ]
            mentioned_bad = _mentioned_invalid_documents(entry, invalid_doc_names)
            if not supported_bad and not mentioned_bad:
                continue
            if _has_valid_direct_source(entry, valid_docs) and not mentioned_bad:
                continue
            _quarantine_entry(entry)
            quarantined_ids.add(entry.id)
            items.append(_item(
                entry,
                stage,
                "depends_on_invalid_source_state",
                mentioned_bad,
                supported_bad,
            ))
            changed = True
        if not changed:
            break

    report = {
        "schema_version": 1,
        "stage": stage,
        "valid_documents": sorted(valid_docs),
        "items": items,
        "summary": {
            "entries_quarantined": len(items),
            "invalid_documents": _counts(
                doc for item in items for doc in item.get("invalid_documents", [])
            ),
            "reasons": _counts(item.get("reason", "") for item in items),
        },
    }
    write_source_custody_report(blackboard.output_dir, report)
    return report


def write_source_custody_report(output_dir: str, report: dict) -> None:
    if not output_dir:
        return
    swarm_dir = Path(output_dir) / "swarm"
    swarm_dir.mkdir(parents=True, exist_ok=True)
    path = swarm_dir / "source_custody.json"
    full_report = {
        "schema_version": 1,
        "audits": [],
        "summary": {
            "entries_quarantined": 0,
            "invalid_documents": {},
            "reasons": {},
        },
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                full_report.update({
                    "schema_version": existing.get("schema_version", 1),
                    "audits": existing.get("audits", []),
                    "summary": existing.get("summary", full_report["summary"]),
                })
        except json.JSONDecodeError:
            pass
    full_report["audits"].append(report)
    summary = full_report["summary"]
    summary["entries_quarantined"] = int(summary.get("entries_quarantined", 0)) + len(
        report.get("items", [])
    )
    _merge_counts(
        summary.setdefault("invalid_documents", {}),
        report.get("summary", {}).get("invalid_documents", {}),
    )
    _merge_counts(
        summary.setdefault("reasons", {}),
        report.get("summary", {}).get("reasons", {}),
    )
    path.write_text(json.dumps(full_report, indent=2), encoding="utf-8")


def source_document_is_valid(
    document: str,
    valid_documents: set[str],
    *,
    allow_synthetic: bool = True,
) -> bool:
    parts = _source_document_parts(document)
    if not parts:
        return False
    return all(
        _normalize_doc_name(part) in valid_documents
        or (
            allow_synthetic
            and _normalize_doc_name(part) in SYNTHETIC_SOURCE_NAMES
        )
        for part in parts
    )


def _valid_document_names(blackboard: Blackboard) -> set[str]:
    names = set()
    for doc in blackboard.documents:
        for raw in (doc.name, doc.id):
            names.update(_document_name_aliases(raw))
    return names


def _document_name_aliases(raw: str | None) -> set[str]:
    normalized = _normalize_doc_name(raw)
    if not normalized:
        return set()
    aliases = {normalized}
    if normalized.endswith(".txt") and len(normalized) > 4:
        aliases.add(normalized[:-4])
    return aliases


def _invalid_source_documents(entry: Entry, valid_docs: set[str]) -> list[str]:
    if not entry.source or not entry.source.document:
        return []
    invalid = []
    for part in _source_document_parts(entry.source.document):
        normalized = _normalize_doc_name(part)
        if not normalized:
            continue
        if normalized in valid_docs or normalized in SYNTHETIC_SOURCE_NAMES:
            continue
        invalid.append(part)
    return invalid


def _source_document_parts(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    parts = re.split(r"\s*(?:;|\||\band\b)\s*", text, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


def _mentioned_invalid_documents(entry: Entry, invalid_docs: set[str]) -> list[str]:
    if not invalid_docs:
        return []
    haystack = " ".join([
        entry.content or "",
        entry.source.evidence if entry.source else "",
    ]).lower()
    mentioned = []
    for doc in invalid_docs:
        if doc and doc.lower() in haystack:
            mentioned.append(doc)
    return mentioned


def _has_valid_direct_source(entry: Entry, valid_docs: set[str]) -> bool:
    if not entry.source or not entry.source.document:
        return False
    parts = _source_document_parts(entry.source.document)
    if not parts:
        return False
    return any(_normalize_doc_name(part) in valid_docs for part in parts)


def _quarantine_entry(entry: Entry) -> None:
    entry.status = SOURCE_CUSTODY_STATUS
    if "source_custody:quarantined" not in entry.tags:
        entry.tags.append("source_custody:quarantined")


def _item(
    entry: Entry,
    stage: str,
    reason: str,
    invalid_documents: list[str],
    supported_quarantined_ids: list[str],
) -> dict:
    return {
        "entry_id": entry.id,
        "stage": stage,
        "reason": reason,
        "source_document": entry.source.document if entry.source else "",
        "invalid_documents": invalid_documents,
        "supported_quarantined_ids": supported_quarantined_ids,
        "content_excerpt": (entry.content or "")[:500],
    }


def _normalize_doc_name(raw: str | None) -> str:
    return str(raw or "").strip().lower()


def _counts(values) -> dict:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _merge_counts(target: dict, source: dict) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        try:
            amount = int(value)
        except (TypeError, ValueError):
            amount = 0
        target[str(key)] = target.get(str(key), 0) + amount
