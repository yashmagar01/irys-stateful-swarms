from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .blackboard import Blackboard
from .models import Entry


SOURCE_CUSTODY_STATUS = "source_quarantined"


def source_custody_enabled() -> bool:
    return os.getenv("SWARM_ENABLE_SOURCE_CUSTODY", "1") == "1"


def source_custody_audit_only() -> bool:
    return os.getenv("SWARM_SOURCE_CUSTODY_AUDIT_ONLY", "1") == "1"
SYNTHETIC_SOURCE_NAMES = {
    "cross_cutting", "crosscutting", "cross cutting",
    "multiple",
    "multi_document", "multidocument", "multi document",
    "user_prompt", "userprompt", "user prompt",
    "task_instruction", "taskinstruction", "task instruction",
}
_KNOWN_EXTENSIONS = frozenset({
    ".docx", ".xlsx", ".xls", ".csv", ".eml", ".pdf",
    ".pptx", ".ppt", ".txt", ".json", ".html", ".htm",
})
_SEPARATOR_RE = re.compile(r"[-_\s]+")


def enforce_source_custody(
    blackboard: Blackboard,
    stage: str,
) -> dict:
    """Quarantine active entries that claim non-existent source documents."""
    if not source_custody_enabled():
        report = _empty_custody_report(stage)
        write_source_custody_report(blackboard.output_dir, report)
        return report

    audit_only = source_custody_audit_only()
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
        if not audit_only:
            _quarantine_entry(entry)
        invalid_doc_names.update(invalid)
        quarantined_ids.add(entry.id)
        items.append(_item(entry, stage, "invalid_source_document", invalid, []))

    # Cascade through derived/cross-cutting entries that rely on invalid sources.
    active_count = sum(1 for e in blackboard.entries if e.status == "active")
    cascade_cap = max(20, int(active_count * 0.15))
    if not audit_only:
        for _ in range(3):
            if len(items) >= cascade_cap:
                break
            changed = False
            for entry in list(blackboard.entries):
                if len(items) >= cascade_cap:
                    break
                if entry.status != "active":
                    continue
                supported_bad = [
                    entry_id for entry_id in entry.supports_entries
                    if entry_id in quarantined_ids
                ]
                mentioned_bad = _mentioned_invalid_documents(entry, invalid_doc_names)
                if not supported_bad and not mentioned_bad:
                    continue
                if _has_valid_direct_source(entry, valid_docs):
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
        "audit_only": audit_only,
        "valid_documents": sorted(valid_docs),
        "items": items,
        "summary": {
            "entries_quarantined": 0 if audit_only else len(items),
            "entries_flagged": len(items) if audit_only else 0,
            "invalid_documents": _counts(
                doc for item in items for doc in item.get("invalid_documents", [])
            ),
            "reasons": _counts(item.get("reason", "") for item in items),
        },
    }
    write_source_custody_report(blackboard.output_dir, report)
    return report


def _empty_custody_report(stage: str) -> dict:
    return {
        "schema_version": 1,
        "stage": stage,
        "audit_only": False,
        "disabled": True,
        "valid_documents": [],
        "items": [],
        "summary": {
            "entries_quarantined": 0,
            "entries_flagged": 0,
            "invalid_documents": {},
            "reasons": {},
        },
    }


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
    summary["entries_quarantined"] = int(summary.get("entries_quarantined", 0)) + (
        report.get("summary", {}).get("entries_quarantined", 0)
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
    full_aliases = _document_name_aliases(document)
    if full_aliases & valid_documents:
        return True
    if allow_synthetic and _is_synthetic_source(document):
        return True
    parts = _source_document_parts(document)
    if not parts:
        return False
    return all(
        _document_name_aliases(part) & valid_documents
        or (allow_synthetic and _is_synthetic_source(part))
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
    stem = normalized
    for ext in _KNOWN_EXTENSIONS:
        if stem.endswith(ext):
            stem = stem[:-len(ext)]
            break
    if stem and stem != normalized:
        aliases.add(stem)
    for name in list(aliases):
        collapsed = _SEPARATOR_RE.sub("", name)
        if collapsed:
            aliases.add(collapsed)
    return aliases


_LEGAL_CITATION_RE = re.compile(
    r"§"
    r"|\bu\.?\s*s\.?\s*c\.?\b"
    r"|\bc\.?\s*f\.?\s*r\.?\b"
    r"|\bilcs\b"
    r"|\bgen\.?\s*stat"
    r"|\brev\.?\s*stat"
    r"|\bn\.?\s*c\.?\s*gen"
    r"|\ba\.?\s*r\.?\s*s\.?\b"
    r"|\bo\.?\s*r\.?\s*s\.?\b"
    r"|\btreas\.?\s*reg"
    r"|^\d+\s+cfr\b"
    r"|^\d+\s+u\.?\s*s\.?\s*c\.?\b",
    re.IGNORECASE,
)


def _is_synthetic_source(raw: str | None) -> bool:
    normalized = _normalize_doc_name(raw)
    if not normalized:
        return False
    if normalized in SYNTHETIC_SOURCE_NAMES:
        return True
    collapsed = _SEPARATOR_RE.sub("", normalized)
    if collapsed in SYNTHETIC_SOURCE_NAMES:
        return True
    if _LEGAL_CITATION_RE.search(normalized):
        return True
    return False


def _invalid_source_documents(entry: Entry, valid_docs: set[str]) -> list[str]:
    if not entry.source or not entry.source.document:
        return []
    full_aliases = _document_name_aliases(entry.source.document)
    if full_aliases & valid_docs or _is_synthetic_source(entry.source.document):
        return []
    invalid = []
    for part in _source_document_parts(entry.source.document):
        part_aliases = _document_name_aliases(part)
        if not part_aliases:
            continue
        if part_aliases & valid_docs or _is_synthetic_source(part):
            continue
        invalid.append(part)
    return invalid


def _source_document_parts(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    parts = re.split(r"\s*(?:;|\||\band\b)\s*|\s*\+\s*|,\s+", text, flags=re.IGNORECASE)
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
        if not doc:
            continue
        needle = doc.lower()
        if len(needle) < 4:
            continue
        pattern = r"\b" + re.escape(needle) + r"\b"
        if re.search(pattern, haystack):
            mentioned.append(doc)
    return mentioned


def _has_valid_direct_source(entry: Entry, valid_docs: set[str]) -> bool:
    if not entry.source or not entry.source.document:
        return False
    full_aliases = _document_name_aliases(entry.source.document)
    if full_aliases & valid_docs:
        return True
    parts = _source_document_parts(entry.source.document)
    if not parts:
        return False
    return any(_document_name_aliases(part) & valid_docs for part in parts)


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
