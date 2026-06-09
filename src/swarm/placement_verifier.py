from __future__ import annotations

import os

from .blackboard import Blackboard
from .models import Entry, ModelCaller
from .verification import extract_verification_targets, value_survives_in_text
from .worker_dispatch import call_model


MAX_REPAIRS = int(os.getenv("SWARM_MAX_PLACEMENT_REPAIRS", "5"))


def evaluate_placements(
    packet: list[dict],
    artifact_texts: dict[str, str],
) -> list[dict]:
    """Evaluate packet items against in-memory artifact texts.

    Returns a list of placement results, one per critical/high packet item.
    Each result includes whether the item was found and its failure mode.
    """
    results = []
    for row in packet:
        importance = row.get("importance", "medium")
        if importance not in ("critical", "high"):
            continue

        target_file = row.get("target_file", "")
        verification_terms = _verification_terms_from_row(row)
        if not verification_terms:
            results.append({**row, "found": False, "failure_mode": "no_verification_terms"})
            continue

        if not target_file:
            found_anywhere = any(
                _any_term_found(verification_terms, text)
                for text in artifact_texts.values()
            )
            results.append({
                **row,
                "found": found_anywhere,
                "failure_mode": None if found_anywhere else "content_missing",
            })
            continue

        target_text = artifact_texts.get(target_file, "")
        found_in_target = _any_term_found(verification_terms, target_text)

        found_elsewhere = False
        if not found_in_target:
            for fname, text in artifact_texts.items():
                if fname != target_file and _any_term_found(verification_terms, text):
                    found_elsewhere = True
                    break

        if found_in_target:
            results.append({**row, "found": True, "failure_mode": None})
        elif found_elsewhere:
            results.append({**row, "found": False, "failure_mode": "wrong_file"})
        elif target_file not in artifact_texts:
            results.append({**row, "found": False, "failure_mode": "artifact_missing"})
        else:
            results.append({**row, "found": False, "failure_mode": "content_missing"})

    return results


def get_repair_candidates(placements: list[dict]) -> list[dict]:
    """Return top-N placement failures eligible for repair."""
    repairable_modes = {"content_missing", "wrong_file"}
    failures = [
        p for p in placements
        if not p.get("found") and p.get("failure_mode") in repairable_modes
    ]
    failures.sort(key=lambda p: (
        0 if p.get("importance") == "critical" else 1,
        p.get("summary", ""),
    ))
    return failures[:MAX_REPAIRS]


def repair_placements(
    failures: list[dict],
    artifact_texts: dict[str, str],
    blackboard: Blackboard,
    caller: ModelCaller,
) -> tuple[dict[str, str], int]:
    """Run bounded repair for placement failures.

    For each failure, asks the model to insert the missing content into the
    correct target file's draft. Returns updated artifact_texts and total tokens.
    """
    if not failures:
        return artifact_texts, 0

    by_file: dict[str, list[dict]] = {}
    for failure in failures:
        target = failure.get("target_file", "")
        if not target:
            if artifact_texts:
                target = next(iter(artifact_texts))
            else:
                continue
        by_file.setdefault(target, []).append(failure)

    total_tokens = 0
    updated = dict(artifact_texts)

    for filename, file_failures in by_file.items():
        current_text = updated.get(filename, "")
        if not current_text:
            continue

        missing_items = "\n".join(
            f"- [{f.get('importance', 'high')}] {f.get('section', 'General')}: "
            f"{f.get('summary', '')} (native form: {f.get('native_form', 'section')})"
            for f in file_failures
        )

        active = [e for e in blackboard.entries if e.status == "active"]
        evidence_text = _gather_evidence(file_failures, active)

        prompt = f"""You are repairing a draft document that is missing required content.

TASK: {blackboard.task_instruction[:4000]}

OUTPUT FILE: {filename}

MISSING REQUIRED ELEMENTS:
{missing_items}

SUPPORTING EVIDENCE FROM ANALYSIS:
{evidence_text[:8000]}

CURRENT DRAFT (insert missing content at the appropriate locations):
{current_text[:40000]}

Instructions:
1. Insert each missing element at the correct location in the document.
2. Use the specified native form (table, section, clause, list, etc.).
3. Use specific facts and numbers from the supporting evidence.
4. Do NOT remove or significantly alter existing content.
5. Return the COMPLETE updated document text."""

        max_tokens = int(os.getenv("SWARM_REPAIR_MAX_TOKENS", "16384"))
        payload, tokens = call_model(
            caller, prompt, max_tokens=max_tokens, json_mode=False,
        )
        total_tokens += tokens

        repaired = payload.get("text", "").strip()
        if repaired and len(repaired) > len(current_text) * 0.5:
            updated[filename] = repaired

    return updated, total_tokens


def _verification_terms_from_row(row: dict) -> list[str]:
    terms = []
    raw = row.get("verification_terms", "")
    if isinstance(raw, list):
        terms.extend(str(t).strip() for t in raw if str(t).strip())
    elif isinstance(raw, str) and raw.strip():
        terms.append(raw.strip())

    summary = row.get("summary", "")
    if summary:
        for target in extract_verification_targets(summary):
            val = str(target.get("raw", "")).strip()
            if val and val not in terms:
                terms.append(val)

    return terms[:10]


def _any_term_found(terms: list[str], text: str) -> bool:
    if not text or not terms:
        return False
    for term in terms:
        if value_survives_in_text(term, text, []):
            return True
    return False


def _gather_evidence(failures: list[dict], active: list[Entry]) -> str:
    by_id = {e.id: e for e in active}
    parts = []
    seen = set()
    for failure in failures:
        for eid in failure.get("entry_ids", []):
            if eid in seen:
                continue
            seen.add(eid)
            entry = by_id.get(eid)
            if entry:
                src = ""
                if entry.source:
                    src = f" [{entry.source.document}"
                    if entry.source.section:
                        src += f" / {entry.source.section}"
                    src += "]"
                parts.append(f"[{entry.id}] ({entry.type}){src}: {entry.content[:500]}")
    return "\n".join(parts)
