from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .blackboard import Blackboard
from .models import Entry, ModelCaller
from .prompt_audit import PromptAuditContext
from .source_custody import source_document_is_valid
from .synthesis import render_entry
from .worker_dispatch import begin_call_model_usage, call_model, end_call_model_usage


SUPPORT_STATUSES = {"supported", "unsupported", "overstated", "needs_source"}


def source_claim_verification_enabled() -> bool:
    return _env_on("SWARM_ENABLE_SOURCE_CLAIM_VERIFICATION")


def source_claim_quarantine_enabled() -> bool:
    return _env_on("SWARM_SOURCE_CLAIM_QUARANTINE")


def verify_source_claims(
    deliverable: str | dict[str, str],
    blackboard: Blackboard,
    caller: ModelCaller,
) -> tuple[str | dict[str, str], int, dict]:
    """Audit final deliverable claims against source-grounded blackboard state.

    This is a grounding check, not a benchmark-scoring pass. It asks the model
    to bind high-impact final claims to source-backed blackboard entries and
    reports unsupported or overstated claims for follow-up.
    """
    begin_call_model_usage()
    try:
        total_tokens = 0
        file_reports: list[dict] = []
        audited: str | dict[str, str]
        if isinstance(deliverable, dict):
            audited_outputs: dict[str, str] = {}
            for filename, text in deliverable.items():
                report, tokens = _audit_one_file(
                    str(filename), str(text), blackboard, caller,
                )
                total_tokens += tokens
                file_reports.append(report)
                audited_outputs[str(filename)] = _maybe_quarantine_claims(
                    str(text), report,
                )
            audited = audited_outputs
        else:
            report, tokens = _audit_one_file(
                "output", str(deliverable), blackboard, caller,
            )
            total_tokens += tokens
            file_reports.append(report)
            audited = _maybe_quarantine_claims(str(deliverable), report)

        full_report = {
            "schema_version": 1,
            "mode": (
                "audit_and_quarantine"
                if source_claim_quarantine_enabled()
                else "audit_only"
            ),
            "files": file_reports,
            "summary": _summarize_files(file_reports),
        }
        write_source_claim_verification_report(blackboard.output_dir, full_report)
        return audited, total_tokens, full_report
    finally:
        end_call_model_usage()


def write_source_claim_verification_report(output_dir: str, report: dict) -> None:
    if not output_dir:
        return
    swarm_dir = Path(output_dir) / "swarm"
    swarm_dir.mkdir(parents=True, exist_ok=True)
    (swarm_dir / "source_claim_verification.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )


def _audit_one_file(
    filename: str,
    text: str,
    blackboard: Blackboard,
    caller: ModelCaller,
) -> tuple[dict, int]:
    valid_documents = _valid_source_documents(blackboard)
    evidence_entries = _source_grounded_entries(
        blackboard.entries,
        valid_documents,
    )
    evidence_text = "\n".join(
        render_entry(entry, max_content=650)
        for entry in evidence_entries[:260]
    )
    prompt = f"""You are auditing final deliverable claims for source support.

TASK:
{blackboard.task_instruction}

FILE:
{filename}

FINAL DELIVERABLE EXCERPT:
{_deliverable_excerpt(text)}

SOURCE-GROUNDED BLACKBOARD EVIDENCE:
{evidence_text[:120000]}

Audit only high-impact factual claims made by the final deliverable. Focus on:
- file paths, class names, function names, modules, schemas, config fields, command flags, dates, versions, costs, security vulnerabilities, architectural components, causal claims, and recommendations that depend on a source fact.

For each claim, classify support using ONLY the source-grounded evidence above:
- supported: the evidence directly supports the claim.
- unsupported: no provided evidence supports the claim.
- overstated: some evidence exists, but the deliverable makes the claim broader, stronger, or more certain than the evidence supports.
- needs_source: the claim may be true but needs direct source support before shipping.

Do NOT use outside knowledge. Do NOT reward citations that do not actually support the claim. Prefer auditing the riskiest 25-40 claims over trivial prose.

Return JSON:
{{"claims": [
  {{
    "claim": "specific final-output claim",
    "status": "supported|unsupported|overstated|needs_source",
    "supporting_entry_ids": ["e1"],
    "source_documents": ["file.py"],
    "reason": "brief support or gap explanation",
    "severity": "critical|high|medium|low"
  }}
]}}
"""
    payload, tokens = call_model(
        caller,
        prompt,
        max_tokens=8192,
        audit_context=PromptAuditContext(
            stage="source_claim_verification",
            output_dir=blackboard.output_dir,
            provenance=[
                "user.instruction",
                "swarm.blackboard",
                "swarm.final_deliverable",
                "clean.professional_prior_dynamic",
            ],
            metadata={
                "filename": filename,
                "deliverable_chars": len(text),
                "evidence_entry_count": len(evidence_entries),
            },
        ),
    )
    claims = normalize_claim_audit_items(payload.get("claims", []))
    return {
        "filename": filename,
        "claims": claims,
        "summary": _summarize_claims(claims),
    }, tokens


def normalize_claim_audit_items(raw_claims: Any) -> list[dict]:
    if not isinstance(raw_claims, list):
        return []
    normalized: list[dict] = []
    seen: set[str] = set()
    for raw in raw_claims:
        if not isinstance(raw, dict):
            continue
        claim = str(raw.get("claim", "")).strip()
        if len(claim) < 12:
            continue
        key = claim[:180].lower()
        if key in seen:
            continue
        seen.add(key)
        status = str(raw.get("status", "")).strip().lower()
        if status not in SUPPORT_STATUSES:
            status = "needs_source"
        severity = str(raw.get("severity", "medium")).strip().lower()
        if severity not in {"critical", "high", "medium", "low"}:
            severity = "medium"
        normalized.append({
            "claim": claim,
            "status": status,
            "supporting_entry_ids": _as_str_list(raw.get("supporting_entry_ids", [])),
            "source_documents": _as_str_list(raw.get("source_documents", [])),
            "reason": str(raw.get("reason", "")).strip()[:600],
            "severity": severity,
        })
    return normalized[:50]


def _maybe_quarantine_claims(text: str, file_report: dict) -> str:
    if not source_claim_quarantine_enabled():
        return text
    risky = [
        claim for claim in file_report.get("claims", [])
        if claim.get("status") in {"unsupported", "overstated", "needs_source"}
        and claim.get("severity") in {"critical", "high", "medium"}
    ]
    if not risky:
        return text
    cleaned_text, quarantined_lines = _quarantine_risky_claim_lines(text, risky)
    lines = [
        "## Source Support Caveats",
        "",
        "The following final-output claims need stronger source support before they should be relied on:",
    ]
    if quarantined_lines:
        lines.extend([
            "",
            f"Quarantined unsupported artifact lines: {quarantined_lines}.",
        ])
    for claim in risky[:15]:
        lines.append(
            f"- [{claim.get('status')}] {claim.get('claim')} "
            f"({claim.get('reason', '').strip()})"
        )
    return cleaned_text.rstrip() + "\n\n" + "\n".join(lines)


def _quarantine_risky_claim_lines(text: str, risky_claims: list[dict]) -> tuple[str, int]:
    output_lines = []
    quarantined = 0
    for line in text.splitlines():
        claim = _matching_risky_claim(line, risky_claims)
        if claim:
            quarantined += 1
            output_lines.append(
                "[SOURCE-CHECK QUARANTINED: "
                f"{claim.get('status', 'needs_source')} "
                "final-output claim removed; see Source Support Caveats]"
            )
        else:
            output_lines.append(line)
    return "\n".join(output_lines), quarantined


def _matching_risky_claim(line: str, risky_claims: list[dict]) -> dict | None:
    clean_line = line.strip()
    if not clean_line:
        return None
    line_lower = clean_line.lower()
    for claim in risky_claims:
        claim_text = str(claim.get("claim", "") or "").strip()
        if not claim_text:
            continue
        claim_lower = claim_text.lower()
        if claim_lower in line_lower or line_lower in claim_lower:
            return claim
        numbers = _claim_numeric_markers(claim_text)
        words = _claim_word_markers(claim_text)
        if numbers and all(number.lower() in line_lower for number in numbers):
            word_hits = sum(1 for word in words if word in line_lower)
            if word_hits >= min(3, len(words)):
                return claim
    return None


def _claim_numeric_markers(text: str) -> list[str]:
    markers = []
    for match in re.finditer(
        r"\$?\b\d{1,3}(?:,\d{3})*(?:\.\d+)?%?\b|"
        r"\b(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{1,2},?\s+\d{4}\b|"
        r"\bQ[1-4]\b",
        text,
        re.IGNORECASE,
    ):
        marker = match.group(0).strip()
        if marker and marker.lower() not in [m.lower() for m in markers]:
            markers.append(marker)
    return markers[:8]


def _claim_word_markers(text: str) -> list[str]:
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "based",
        "calculated", "total", "count", "per", "is", "as", "of", "to",
        "through", "source",
    }
    markers = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text.lower()):
        if word in stop or word in markers:
            continue
        markers.append(word)
        if len(markers) >= 12:
            break
    return markers


def _source_grounded_entries(
    entries: list[Entry],
    valid_documents: set[str] | None = None,
) -> list[Entry]:
    valid_documents = valid_documents or set()
    grounded = [
        entry for entry in entries
        if entry.status == "active"
        and entry.source
        and entry.source.document
        and (
            not valid_documents
            or source_document_is_valid(
                entry.source.document,
                valid_documents,
                allow_synthetic=False,
            )
        )
        and (
            (entry.source.evidence and len(entry.source.evidence.strip()) >= 8)
            or (entry.content and len(entry.content.strip()) >= 20)
        )
    ]

    def score(entry: Entry) -> tuple[int, float, int]:
        tags = entry.tags or []
        type_score = {
            "analysis": 5,
            "calculation": 5,
            "observation": 4,
            "strategy": 2,
            "gap": 1,
        }.get(entry.type, 0)
        debt_score = 2 if any(tag.startswith("debt_type:") for tag in tags) else 0
        maintenance_score = 1 if "blackboard_maintenance" in tags else 0
        return (
            type_score + debt_score + maintenance_score,
            entry.confidence,
            min(len(entry.content), 2000),
        )

    return sorted(grounded, key=score, reverse=True)


def _valid_source_documents(blackboard: Blackboard) -> set[str]:
    names = set()
    for doc in blackboard.documents:
        for raw in (doc.name, doc.id):
            value = str(raw or "").strip().lower()
            if value:
                names.add(value)
    return names


def _deliverable_excerpt(text: str, max_chars: int = 140000) -> str:
    if len(text) <= max_chars:
        return text
    part = max_chars // 3
    midpoint = len(text) // 2
    middle_start = max(0, midpoint - part // 2)
    return (
        text[:part]
        + "\n\n[... middle excerpt ...]\n\n"
        + text[middle_start:middle_start + part]
        + "\n\n[... tail excerpt ...]\n\n"
        + text[-part:]
    )


def _summarize_files(files: list[dict]) -> dict:
    summary = {
        "files_checked": len(files),
        "claims_checked": 0,
        "status_counts": {},
        "severity_counts": {},
        "risky_claims": 0,
    }
    for file_report in files:
        file_summary = file_report.get("summary", {})
        summary["claims_checked"] += int(file_summary.get("claims_checked", 0))
        _merge_counts(summary["status_counts"], file_summary.get("status_counts", {}))
        _merge_counts(summary["severity_counts"], file_summary.get("severity_counts", {}))
        summary["risky_claims"] += int(file_summary.get("risky_claims", 0))
    return summary


def _summarize_claims(claims: list[dict]) -> dict:
    status_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for claim in claims:
        status = str(claim.get("status") or "needs_source")
        severity = str(claim.get("severity") or "medium")
        status_counts[status] = status_counts.get(status, 0) + 1
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
    risky = sum(
        1 for claim in claims
        if claim.get("status") in {"unsupported", "overstated", "needs_source"}
    )
    return {
        "claims_checked": len(claims),
        "status_counts": status_counts,
        "severity_counts": severity_counts,
        "risky_claims": risky,
    }


def _merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        try:
            amount = int(value)
        except (TypeError, ValueError):
            amount = 0
        target[str(key)] = target.get(str(key), 0) + amount


def _as_str_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(value).strip() for value in raw if str(value).strip()]
    if isinstance(raw, str) and raw.strip():
        return [part.strip() for part in raw.split(",") if part.strip()]
    return []


def _env_on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
