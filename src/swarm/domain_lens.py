"""Domain Lens: professional-prior pseudo-criteria generation.

After the seed planner produces a generic analytical plan, this module
runs a second pass that generates domain-specific issue scaffolding:
legal authority watchlist, calculation targets, output structure
requirements, and negative checks. This injects the kind of domain
expertise that a specialist lawyer would bring to the task — without
ever seeing benchmark rubric criteria.
"""
from __future__ import annotations

import json
import os

from .blackboard import Blackboard
from .models import (
    Entry, ModelCaller, Signal, WorkerRecord,
    gen_entry_id, gen_signal_id,
)
from .worker_dispatch import call_model


def generate_domain_lens(
    blackboard: Blackboard,
    seed_plan: dict,
    caller: ModelCaller,
) -> tuple[dict, int]:
    """Generate domain-specific lens rows from task + seed plan.

    Returns (lens_data, tokens_used).
    """
    if not os.getenv("SWARM_DOMAIN_LENS", "1") == "1":
        return {}, 0

    doc_summary = "\n".join(
        f"- {d.name}: {d.size_bytes} bytes, headings={d.headings[:15]}"
        for d in blackboard.documents
    )

    seed_summary = _format_seed_summary(seed_plan)

    prompt = f"""You are a senior legal specialist conducting a pre-analysis domain expertise review. Your job is to identify the SPECIFIC professional knowledge that an analyst will need to produce a thorough, defensible deliverable for this task.

TASK: {blackboard.task_instruction}

DOCUMENTS AVAILABLE:
{doc_summary}

ANALYTICAL PLAN ALREADY PRODUCED:
{seed_summary}

Based on the task type, document names, and analytical plan, generate domain-specific guidance. Think like the most experienced practitioner in the relevant legal specialty — what would they insist must be covered?

For each category below, be CONCRETE and SPECIFIC to this exact task. Do not give generic advice.

1. ISSUE HYPOTHESES: What specific issues are likely present in these documents? For a markup review, what clauses are typically contested? For a compliance review, what violations are commonly found? For a drafting task, what provisions are frequently missing? List 5-15 specific hypotheses.

2. LEGAL AUTHORITY WATCHLIST: What specific statutes, regulations, rules, case law principles, or professional standards are relevant? Include section numbers. For example: "Rule 14a-8(l)(1) — shareholder proposal reproduction", "DGCL § 108 — incorporator vs board authority", "11 U.S.C. § 1129(a)(10) — impaired class acceptance". List 5-15 specific authorities.

3. CALCULATION TARGETS: What specific numerical calculations, valuations, or quantitative analyses should the deliverable include? Be precise: "Calculate midpoint recovery rate for each creditor class", "Compute pro rata share as commitment / total fund size", "Verify filing fee against current HSR threshold schedule". List 3-10 targets.

4. OUTPUT STRUCTURE REQUIREMENTS: What specific sections, tabs, columns, or structural elements must the final deliverable contain? For a workbook: what tabs and columns? For a memo: what sections? For a marked-up agreement: what annotation format? Be explicit about the expected artifact form.

5. NEGATIVE CHECKS: What common errors, omissions, or pitfalls should be actively checked for? What would a senior reviewer flag as missing? What are the "gotchas" in this type of work? List 3-8 checks.

6. CROSS-DOCUMENT RECONCILIATION: What specific terms, definitions, or values must be consistent across the source documents? What discrepancies are commonly found? List 2-5 reconciliation points.

Return JSON:
{{"issue_hypotheses": ["hypothesis 1", "hypothesis 2"],
  "legal_authorities": [{{"authority": "Rule/statute name and section", "relevance": "why it applies to this task"}}],
  "calculation_targets": [{{"target": "what to calculate", "method": "how to calculate it"}}],
  "output_structure": [{{"element": "section/tab/column name", "requirement": "what it must contain"}}],
  "negative_checks": ["check 1", "check 2"],
  "cross_doc_reconciliation": [{{"point": "what to reconcile", "documents": ["doc1", "doc2"]}}]}}"""

    _EXPECTED_KEYS = {
        "issue_hypotheses", "legal_authorities", "calculation_targets",
        "output_structure", "negative_checks", "cross_doc_reconciliation",
    }

    total_tokens = 0
    for _attempt in range(2):
        try:
            payload, tokens = call_model(caller, prompt, max_tokens=4096)
        except Exception:
            continue
        total_tokens += tokens
        if not isinstance(payload, dict):
            continue
        if payload.keys() & _EXPECTED_KEYS:
            return payload, total_tokens
    return {}, total_tokens


def lens_to_entries(lens: dict, blackboard: Blackboard) -> list[Entry]:
    """Convert domain lens into blackboard strategy entries."""
    entries: list[Entry] = []
    worker = WorkerRecord("domain_lens", "lens_generation", 0)

    for hyp in _str_list(lens.get("issue_hypotheses", [])):
        entries.append(Entry(
            id=gen_entry_id(), type="strategy",
            content=f"DOMAIN LENS — Issue hypothesis: {hyp}",
            created_by=worker, confidence=0.8, status="active",
            tags=["domain_lens", "issue_hypothesis"],
        ))

    authorities = lens.get("legal_authorities", [])
    if not isinstance(authorities, list):
        authorities = []
    for auth in authorities:
        if not isinstance(auth, dict):
            continue
        name = str(auth.get("authority", "")).strip()
        relevance = str(auth.get("relevance", "")).strip()
        if name:
            content = f"DOMAIN LENS — Legal authority: {name}"
            if relevance:
                content += f". Relevance: {relevance}"
            entries.append(Entry(
                id=gen_entry_id(), type="strategy",
                content=content,
                created_by=worker, confidence=0.8, status="active",
                tags=["domain_lens", "legal_authority"],
            ))

    calc_targets = lens.get("calculation_targets", [])
    if not isinstance(calc_targets, list):
        calc_targets = []
    for calc in calc_targets:
        if not isinstance(calc, dict):
            continue
        target = str(calc.get("target", "")).strip()
        method = str(calc.get("method", "")).strip()
        if target:
            content = f"DOMAIN LENS — Calculation target: {target}"
            if method:
                content += f". Method: {method}"
            entries.append(Entry(
                id=gen_entry_id(), type="strategy",
                content=content,
                created_by=worker, confidence=0.8, status="active",
                tags=["domain_lens", "calculation_target"],
            ))

    out_structure = lens.get("output_structure", [])
    if not isinstance(out_structure, list):
        out_structure = []
    for elem in out_structure:
        if not isinstance(elem, dict):
            continue
        name = str(elem.get("element", "")).strip()
        req = str(elem.get("requirement", "")).strip()
        if name:
            content = f"DOMAIN LENS — Output structure: {name}"
            if req:
                content += f". Requirement: {req}"
            entries.append(Entry(
                id=gen_entry_id(), type="strategy",
                content=content,
                created_by=worker, confidence=0.8, status="active",
                tags=["domain_lens", "output_structure"],
            ))

    for check in _str_list(lens.get("negative_checks", [])):
        entries.append(Entry(
            id=gen_entry_id(), type="strategy",
            content=f"DOMAIN LENS — Negative check: {check}",
            created_by=worker, confidence=0.8, status="active",
            tags=["domain_lens", "negative_check"],
        ))

    reconciliation = lens.get("cross_doc_reconciliation", [])
    if not isinstance(reconciliation, list):
        reconciliation = []
    for rec in reconciliation:
        if not isinstance(rec, dict):
            continue
        point = str(rec.get("point", "")).strip()
        docs = rec.get("documents", [])
        if point:
            content = f"DOMAIN LENS — Cross-doc reconciliation: {point}"
            if docs:
                content += f". Documents: {', '.join(str(d) for d in docs)}"
            entries.append(Entry(
                id=gen_entry_id(), type="strategy",
                content=content,
                created_by=worker, confidence=0.8, status="active",
                tags=["domain_lens", "reconciliation"],
            ))

    return entries


def lens_to_signals(lens: dict, blackboard: Blackboard) -> None:
    """Convert domain lens items into high-priority signals."""
    for hyp in _str_list(lens.get("issue_hypotheses", []))[:10]:
        blackboard.add_signal(Signal(
            id=gen_signal_id(), type="question",
            content=f"Domain lens issue to investigate: {hyp}",
            origin_entry="domain_lens", priority="high",
            status="open", iteration_created=0,
        ))

    authorities = lens.get("legal_authorities", [])
    if not isinstance(authorities, list):
        authorities = []
    for auth in authorities[:8]:
        if isinstance(auth, dict):
            name = str(auth.get("authority", "")).strip()
            if name:
                blackboard.add_signal(Signal(
                    id=gen_signal_id(), type="question",
                    content=f"Check applicability of: {name}",
                    origin_entry="domain_lens", priority="high",
                    status="open", iteration_created=0,
                ))

    calcs = lens.get("calculation_targets", [])
    if not isinstance(calcs, list):
        calcs = []
    for calc in calcs[:5]:
        if isinstance(calc, dict):
            target = str(calc.get("target", "")).strip()
            if target:
                blackboard.add_signal(Signal(
                    id=gen_signal_id(), type="question",
                    content=f"Perform calculation: {target}",
                    origin_entry="domain_lens", priority="high",
                    status="open", iteration_created=0,
                ))


def format_lens_guidance(lens: dict) -> str:
    """Format domain lens as guidance text for extraction workers."""
    if not lens:
        return ""

    parts: list[str] = []

    hypotheses = _str_list(lens.get("issue_hypotheses", []))
    if hypotheses:
        parts.append(
            "Domain-specific issues to investigate:\n"
            + "\n".join(f"- {h}" for h in hypotheses)
        )

    authorities = lens.get("legal_authorities", [])
    if not isinstance(authorities, list):
        authorities = []
    if authorities:
        auth_lines = []
        for a in authorities:
            if isinstance(a, dict):
                name = str(a.get("authority", "")).strip()
                rel = str(a.get("relevance", "")).strip()
                if name:
                    line = name
                    if rel:
                        line += f" — {rel}"
                    auth_lines.append(line)
        if auth_lines:
            parts.append(
                "Legal authorities to check:\n"
                + "\n".join(f"- {l}" for l in auth_lines)
            )

    calcs = lens.get("calculation_targets", [])
    if not isinstance(calcs, list):
        calcs = []
    if calcs:
        calc_lines = []
        for c in calcs:
            if isinstance(c, dict):
                t = str(c.get("target", "")).strip()
                if t:
                    calc_lines.append(t)
        if calc_lines:
            parts.append(
                "Calculations to perform:\n"
                + "\n".join(f"- {c}" for c in calc_lines)
            )

    structure = lens.get("output_structure", [])
    if not isinstance(structure, list):
        structure = []
    if structure:
        struct_lines = []
        for s in structure:
            if isinstance(s, dict):
                elem = str(s.get("element", "")).strip()
                req = str(s.get("requirement", "")).strip()
                if elem:
                    line = elem
                    if req:
                        line += f": {req}"
                    struct_lines.append(line)
        if struct_lines:
            parts.append(
                "Required output structure:\n"
                + "\n".join(f"- {s}" for s in struct_lines)
            )

    checks = _str_list(lens.get("negative_checks", []))
    if checks:
        parts.append(
            "Common pitfalls to avoid:\n"
            + "\n".join(f"- {c}" for c in checks)
        )

    return "\n\n".join(parts)


def _format_seed_summary(seed_plan: dict) -> str:
    """Compact summary of the seed plan for the lens prompt."""
    if not seed_plan:
        return "No seed plan available."

    parts: list[str] = []

    questions = seed_plan.get("key_questions", [])
    if not isinstance(questions, list):
        questions = []
    if questions:
        parts.append("Key questions: " + "; ".join(
            str(q).strip() for q in questions[:8] if isinstance(q, str)
        ))

    framework = str(seed_plan.get("analytical_framework", "")).strip()
    if framework:
        parts.append(f"Framework: {framework}")

    context = seed_plan.get("context_enrichment", "")
    if isinstance(context, str) and context.strip():
        parts.append(f"Context: {context[:500]}")

    return "\n".join(parts) if parts else "Minimal seed plan."


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
