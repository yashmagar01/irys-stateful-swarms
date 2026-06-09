"""Domain Lens: expert-prior hypothesis generation.

After the seed planner produces a generic analytical plan, this module
runs a second pass that generates domain-aware issue scaffolding:
reference framework watchlist, calculation targets, output structure
requirements, and negative checks. The lens adapts to whatever domain
the task and documents represent — legal, technical, financial, scientific,
etc. All lens outputs are HYPOTHESES (status=candidate) that must be
verified against source documents before they can inform synthesis.
"""
from __future__ import annotations

import json
import os

from .blackboard import Blackboard
from .models import (
    Entry, EpistemicStatus, ModelCaller, Signal, WorkerRecord,
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

    prompt = f"""You are a top-tier cross-functional expert conducting a pre-analysis review. Your job is to identify the SPECIFIC professional knowledge that an analyst will need to produce a thorough, well-grounded deliverable for this task.

Examine the task description, document names, and analytical plan below. Determine what domain this work falls in — it could be legal, technical, financial, scientific, operational, or any combination. Then generate guidance appropriate to THAT domain.

TASK: {blackboard.task_instruction}

DOCUMENTS AVAILABLE:
{doc_summary}

ANALYTICAL PLAN ALREADY PRODUCED:
{seed_summary}

Think like the most experienced practitioner in whatever domain these documents and this task represent. What would they insist must be covered? What would they check first?

IMPORTANT: Your outputs are HYPOTHESES to guide investigation, NOT established facts. Every item you list must be verified against the actual source documents before it can be treated as true. Do NOT invent specific citations, numbers, or claims — only suggest what to LOOK FOR.

For each category below, be CONCRETE and SPECIFIC to this exact task. Do not give generic advice.

1. ISSUE HYPOTHESES: What specific issues are likely present in these documents given the task? What problems, gaps, or findings would an expert expect to discover? List 5-15 specific hypotheses.

2. REFERENCE FRAMEWORK WATCHLIST: What specific standards, frameworks, regulations, specifications, or authoritative references are likely relevant? These could be legal statutes, technical standards, industry frameworks, scientific protocols, or professional best practices — whatever fits the domain. List 5-15 items to check for applicability.

3. CALCULATION TARGETS: What specific numerical calculations, valuations, or quantitative analyses should the deliverable include? Be precise about what to compute and how. List 3-10 targets.

4. OUTPUT STRUCTURE REQUIREMENTS: What specific sections, tabs, columns, or structural elements must the final deliverable contain? Be explicit about the expected artifact form.

5. NEGATIVE CHECKS: What common errors, omissions, or pitfalls should be actively checked for? What would a senior reviewer flag as missing? List 3-8 checks.

6. CROSS-DOCUMENT RECONCILIATION: What specific terms, definitions, or values must be consistent across the source documents? What discrepancies are commonly found? List 2-5 reconciliation points.

Return JSON:
{{"issue_hypotheses": ["hypothesis 1", "hypothesis 2"],
  "reference_frameworks": [{{"framework": "Standard/regulation/spec name", "relevance": "why it may apply to this task"}}],
  "calculation_targets": [{{"target": "what to calculate", "method": "how to calculate it"}}],
  "output_structure": [{{"element": "section/tab/column name", "requirement": "what it must contain"}}],
  "negative_checks": ["check 1", "check 2"],
  "cross_doc_reconciliation": [{{"point": "what to reconcile", "documents": ["doc1", "doc2"]}}]}}"""

    _EXPECTED_KEYS = {
        "issue_hypotheses", "reference_frameworks", "legal_authorities",
        "calculation_targets", "output_structure", "negative_checks",
        "cross_doc_reconciliation",
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
    """Convert domain lens into blackboard candidate entries (hypotheses, not facts)."""
    entries: list[Entry] = []
    worker = WorkerRecord("domain_lens", "lens_generation", 0)
    hyp_epistemic = EpistemicStatus(classification="hypothesis", source_credibility="unknown")

    for hyp in _str_list(lens.get("issue_hypotheses", [])):
        entries.append(Entry(
            id=gen_entry_id(), type="strategy",
            content=f"HYPOTHESIS — Issue to investigate: {hyp}",
            created_by=worker, confidence=0.5, status="candidate",
            epistemic=hyp_epistemic,
            tags=["domain_lens", "issue_hypothesis"],
        ))

    frameworks = lens.get("reference_frameworks", lens.get("legal_authorities", []))
    if not isinstance(frameworks, list):
        frameworks = []
    for fw in frameworks:
        if not isinstance(fw, dict):
            continue
        name = str(fw.get("framework", fw.get("authority", ""))).strip()
        relevance = str(fw.get("relevance", "")).strip()
        if name:
            content = f"HYPOTHESIS — Reference framework to check: {name}"
            if relevance:
                content += f". Relevance: {relevance}"
            entries.append(Entry(
                id=gen_entry_id(), type="strategy",
                content=content,
                created_by=worker, confidence=0.5, status="candidate",
                epistemic=hyp_epistemic,
                tags=["domain_lens", "reference_framework"],
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
            content = f"HYPOTHESIS — Calculation target: {target}"
            if method:
                content += f". Method: {method}"
            entries.append(Entry(
                id=gen_entry_id(), type="strategy",
                content=content,
                created_by=worker, confidence=0.5, status="candidate",
                epistemic=hyp_epistemic,
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
            content = f"HYPOTHESIS — Output structure: {name}"
            if req:
                content += f". Requirement: {req}"
            entries.append(Entry(
                id=gen_entry_id(), type="strategy",
                content=content,
                created_by=worker, confidence=0.5, status="candidate",
                epistemic=hyp_epistemic,
                tags=["domain_lens", "output_structure"],
            ))

    for check in _str_list(lens.get("negative_checks", [])):
        entries.append(Entry(
            id=gen_entry_id(), type="strategy",
            content=f"HYPOTHESIS — Negative check: {check}",
            created_by=worker, confidence=0.5, status="candidate",
            epistemic=hyp_epistemic,
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
            content = f"HYPOTHESIS — Cross-doc reconciliation: {point}"
            if docs:
                content += f". Documents: {', '.join(str(d) for d in docs)}"
            entries.append(Entry(
                id=gen_entry_id(), type="strategy",
                content=content,
                created_by=worker, confidence=0.5, status="candidate",
                epistemic=hyp_epistemic,
                tags=["domain_lens", "reconciliation"],
            ))

    return entries


def lens_to_signals(lens: dict, blackboard: Blackboard) -> None:
    """Convert domain lens items into medium-priority investigation signals."""
    for hyp in _str_list(lens.get("issue_hypotheses", []))[:10]:
        blackboard.add_signal(Signal(
            id=gen_signal_id(), type="question",
            content=f"Lens hypothesis to investigate: {hyp}",
            origin_entry="domain_lens", priority="medium",
            status="open", iteration_created=0,
        ))

    frameworks = lens.get("reference_frameworks", lens.get("legal_authorities", []))
    if not isinstance(frameworks, list):
        frameworks = []
    for fw in frameworks[:8]:
        if isinstance(fw, dict):
            name = str(fw.get("framework", fw.get("authority", ""))).strip()
            if name:
                blackboard.add_signal(Signal(
                    id=gen_signal_id(), type="question",
                    content=f"Check applicability of: {name}",
                    origin_entry="domain_lens", priority="medium",
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
                    origin_entry="domain_lens", priority="medium",
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
            "Hypotheses to investigate (verify against source docs):\n"
            + "\n".join(f"- {h}" for h in hypotheses)
        )

    frameworks = lens.get("reference_frameworks", lens.get("legal_authorities", []))
    if not isinstance(frameworks, list):
        frameworks = []
    if frameworks:
        fw_lines = []
        for a in frameworks:
            if isinstance(a, dict):
                name = str(a.get("framework", a.get("authority", ""))).strip()
                rel = str(a.get("relevance", "")).strip()
                if name:
                    line = name
                    if rel:
                        line += f" — {rel}"
                    fw_lines.append(line)
        if fw_lines:
            parts.append(
                "Reference frameworks to check (verify against source docs):\n"
                + "\n".join(f"- {l}" for l in fw_lines)
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
