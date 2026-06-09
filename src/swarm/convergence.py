from __future__ import annotations

import json

from .blackboard import Blackboard
from .models import Entry, ModelCaller, Signal, gen_signal_id
from .worker_dispatch import call_model


def check_convergence(blackboard: Blackboard, convergence_output: dict,
                      caller: ModelCaller) -> tuple[bool, int]:
    """Convergence check that runs every iteration."""
    # 1. Structural coverage (deterministic)
    for doc in blackboard.documents:
        if doc.structural_profile:
            expected = doc.structural_profile.get("numbered_items", 0)
            if not isinstance(expected, (int, float)):
                continue
            actual = len([
                e for e in blackboard.entries
                if e.source and e.source.document == doc.name
                and e.status == "active"
                and e.type in ("observation", "analysis", "calculation")
            ])
            if expected > 0 and actual < expected * 0.5:
                return False, 0

    # 2. Critical signals (deterministic)
    if any(s.status == "open" and s.priority == "critical" for s in blackboard.signals):
        return False, 0

    # 3. Adversarial model check with full blackboard visibility
    summary = blackboard.get_summary()
    active = [e for e in blackboard.entries if e.status == "active"]

    # Full entries grouped by document
    by_doc: dict[str, list[str]] = {}
    for e in active:
        doc = e.source.document if e.source else "cross_cutting"
        by_doc.setdefault(doc or "cross_cutting", []).append(
            f"[{e.id}] ({e.type}) {e.content[:300]}"
        )
    findings_by_doc = ""
    for doc_name, items in sorted(by_doc.items()):
        findings_by_doc += f"\n=== {doc_name} ({len(items)} entries) ===\n"
        for item in items:
            findings_by_doc += f"  {item}\n"

    loaded_docs = [
        d for d in summary["documents"]
        if d.get("read_status") != "unread" or d.get("structural_profile")
    ]
    unloaded_count = len(summary["documents"]) - len(loaded_docs)
    doc_profiles = "\n".join(
        f"- {d['name']}: {d['read_status']}, "
        f"profile={json.dumps(d.get('structural_profile') or {})}"
        for d in loaded_docs
    )
    if unloaded_count > 0:
        doc_profiles += f"\n(+ {unloaded_count} unloaded documents in corpus)"

    prompt = f"""The orchestrator says analysis is COMPLETE. Find reasons it is NOT.

TASK: {blackboard.task_instruction}

DOCUMENTS:
{doc_profiles}

ALL FINDINGS ({len(active)} entries):
{findings_by_doc[:200000]}

ENTRY TYPE DISTRIBUTION: {json.dumps(summary['entry_counts'])}
Open signals: {len(summary['open_signals'])}
Orchestrator reasoning: {convergence_output.get('reasoning', '')}
Gaps acknowledged: {json.dumps(convergence_output.get('remaining_gaps', []))}

Return: {{"verdict": "approve"|"reject", "reasoning": "why", "missing_work": [...]}}"""

    payload, tokens = call_model(caller, prompt, max_tokens=2048)

    verdict = payload.get("verdict", "reject")

    if verdict == "reject":
        for item in payload.get("missing_work", []):
            if isinstance(item, str) and item.strip():
                blackboard.add_signal(Signal(
                    id=gen_signal_id(), type="convergence_gap", content=item,
                    origin_entry="convergence_check", priority="high",
                    status="open", iteration_created=blackboard.iteration,
                ))

    return verdict == "approve", tokens


def supervisor_review(blackboard: Blackboard,
                      reviewer: ModelCaller) -> tuple[bool, list[str], int]:
    """Supervisor review after convergence approval.

    Returns (approved, gap_descriptions, tokens_used).
    """
    active = [e for e in blackboard.entries if e.status == "active"]
    summary = blackboard.get_summary()

    # Group ALL entries by document for full visibility
    by_doc: dict[str, list[str]] = {}
    for e in active:
        doc = e.source.document if e.source else "cross_cutting"
        by_doc.setdefault(doc or "cross_cutting", []).append(
            f"[{e.id}] ({e.type}) {e.content[:400]}"
        )

    findings_by_doc = ""
    for doc_name, items in sorted(by_doc.items()):
        findings_by_doc += f"\n=== {doc_name} ({len(items)} entries) ===\n"
        for item in items:
            findings_by_doc += f"  {item}\n"

    # Entry type counts
    type_counts = {}
    for e in active:
        type_counts[e.type] = type_counts.get(e.type, 0) + 1

    _loaded = [
        d for d in summary["documents"]
        if d.get("read_status") != "unread" or d.get("structural_profile")
    ]
    _unloaded_count = len(summary["documents"]) - len(_loaded)
    doc_summary = "\n".join(
        f"- {d['name']}: {d['read_status']}, "
        f"profile={json.dumps(d.get('structural_profile') or {})}"
        for d in _loaded
    )
    if _unloaded_count > 0:
        doc_summary += f"\n(+ {_unloaded_count} unloaded documents in corpus)"

    prompt = f"""You are a senior reviewer. The analysis team has completed their investigation and believes they are ready for synthesis.

HERE IS EXACTLY WHAT WE ARE TRYING TO ACCOMPLISH:
{blackboard.task_instruction}

Read the task instruction above carefully. Think about what a complete, high-quality deliverable for THIS SPECIFIC task requires.

DOCUMENTS ANALYZED:
{doc_summary}

ALL FINDINGS ({len(active)} entries):
{findings_by_doc[:500000]}

ENTRY TYPE DISTRIBUTION:
{json.dumps(type_counts)}
(observation = extracted facts, analysis = conclusions/implications, calculation = arithmetic, strategy = recommendations, gap = identified unknowns)

YOUR JOB: Determine if these findings are sufficient to produce a high-quality deliverable for the task above.

YOUR DEFAULT SHOULD BE APPROVE. Only say REVIEW if you are confident the gaps you identify will materially improve the deliverable. Do NOT find fault for the sake of finding fault.

APPROVE if the findings would produce a useful, professional deliverable that addresses the task instruction.

REVIEW only if you can identify gaps that are ALL THREE of:
1. MATERIAL — would significantly impact deliverable quality if missing
2. SPECIFIC — you can name exactly what to look for
3. ACTIONABLE — a worker can execute this as a concrete task

If REVIEW, each gap MUST be a concrete worker instruction that a junior analyst could execute, like:
- "Calculate the combined HHI from market shares in entries e45, e47, e52 (multiply each firm's share squared, then sum)"
- "Compare the cure period in Section 8.3 of the Credit Agreement against Section 4.1 of the Intercreditor Agreement — flag any conflict"
- "Check if the non-compete in Section 4.2 has a whistleblower/regulatory carve-out (SEC, FTC, OSHA) — flag if missing"
NOT like:
- "More analysis of financial terms needed" (too vague — which terms? what analysis?)
- "Consider regulatory implications" (not actionable — which regulations? what to check?)

Return JSON:
{{"verdict": "approve"|"review",
  "reasoning": "brief explanation",
  "gaps": ["concrete worker instruction 1", "concrete worker instruction 2"]}}

Only include gaps you have high confidence will improve the deliverable. Quality over quantity — 1 excellent gap beats 3 mediocre ones."""

    payload, tokens = call_model(reviewer, prompt, max_tokens=4096)

    verdict = payload.get("verdict", "approve")
    gaps = payload.get("gaps", [])
    if not isinstance(gaps, list):
        gaps = []
    gaps = [g for g in gaps if isinstance(g, str) and g.strip()]

    return verdict == "approve", gaps, tokens


def analytical_steering(blackboard: Blackboard,
                        steerer: ModelCaller) -> tuple[list[dict], int]:
    """Analytical steering that runs every 4th iteration.

    Reads the full blackboard and produces concrete worker tasks that
    workers should execute. Focuses on analytical work: cross-referencing,
    calculations, issue flagging, recommendations.

    Returns (worker_tasks, tokens_used).
    """
    active = [e for e in blackboard.entries if e.status == "active"]

    by_doc: dict[str, list[str]] = {}
    for e in active:
        doc = e.source.document if e.source else "cross_cutting"
        by_doc.setdefault(doc or "cross_cutting", []).append(
            f"[{e.id}] ({e.type}) {e.content[:400]}"
        )

    findings_by_doc = ""
    for doc_name, items in sorted(by_doc.items()):
        findings_by_doc += f"\n=== {doc_name} ({len(items)} entries) ===\n"
        for item in items:
            findings_by_doc += f"  {item}\n"

    type_counts = {}
    for e in active:
        type_counts[e.type] = type_counts.get(e.type, 0) + 1

    _steer_all_docs = blackboard.get_summary()["documents"]
    _steer_loaded = [
        d for d in _steer_all_docs
        if d.get("read_status") != "unread" or d.get("structural_profile")
    ]
    _steer_unloaded = len(_steer_all_docs) - len(_steer_loaded)
    doc_summary = "\n".join(
        f"- {d['name']}: {d['read_status']}, "
        f"profile={json.dumps(d.get('structural_profile') or {})}"
        for d in _steer_loaded
    )
    if _steer_unloaded > 0:
        doc_summary += f"\n(+ {_steer_unloaded} unloaded documents in corpus)"

    prompt = f"""You are a senior analyst directing a team of junior workers. Review their work so far and decide what analytical work they should do next.

TASK: {blackboard.task_instruction}

DOCUMENTS:
{doc_summary}

ALL FINDINGS SO FAR ({len(active)} entries):
{findings_by_doc[:500000]}

ENTRY TYPE DISTRIBUTION: {json.dumps(type_counts)}

The team has been extracting facts from the documents. Now you need to direct them to do ANALYTICAL work. Look at what they've found and identify:

1. CALCULATIONS that should be performed from the extracted numbers (totals, percentages, ratios, deltas, HHI, financial exposure)
2. CROSS-DOCUMENT COMPARISONS that should be made (same concept in different documents, conflicts, inconsistencies)
3. ISSUES that should be FLAGGED (legal risks, missing provisions, problematic clauses, regulatory concerns)
4. RECOMMENDATIONS that should be made (remediation for flagged issues, suggested changes, risk mitigation)

For each task, specify:
- "description": what the worker should do (be very specific — reference entry IDs and document sections)
- "reads_from_blackboard": entry IDs the worker needs to see
- "reads_from_documents": documents/sections to reference
- "expected_output_type": "analysis" or "calculation" or "strategy"

Return JSON: {{"workers": [...]}}

Produce 3-5 high-value analytical tasks. Each must be concrete enough for a junior analyst to execute without further guidance."""

    payload, tokens = call_model(steerer, prompt, max_tokens=4096)

    workers = payload.get("workers", [])
    if not isinstance(workers, list):
        workers = []

    return workers, tokens
