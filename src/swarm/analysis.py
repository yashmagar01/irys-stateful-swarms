"""Direct analysis phase.

Reads the full blackboard and produces analysis/calculation/strategy entries
directly, outside the worker pipeline.
"""
from __future__ import annotations

import json

from .blackboard import Blackboard
from .models import (
    Entry, EntrySource, EpistemicStatus, ModelCaller, WorkerRecord,
    gen_entry_id,
)
from .seed import format_task_state_map
from .worker_dispatch import call_model


def run_direct_analysis(blackboard: Blackboard, seed: dict,
                        caller: ModelCaller) -> tuple[list[Entry], int]:
    """Read the full blackboard and produce analytical entries directly.

    Unlike the worker pipeline, this uses a dedicated prompt for analysis
    that doesn't force entries into the observation schema.

    Returns (new_entries, tokens_used).
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

    key_questions = seed.get("key_questions", [])
    questions_text = "\n".join(f"- {q}" for q in key_questions) if key_questions else "None specified."
    framework = seed.get("analytical_framework", "Analyze thoroughly.")
    completeness = seed.get("completeness_criteria", [])
    completeness_text = "\n".join(f"- {c}" for c in completeness) if completeness else "None specified."
    task_state_map = format_task_state_map(seed)

    prompt = f"""You are a senior analyst. The extraction team has gathered facts from the documents. Now YOU must do the analytical work.

TASK: {blackboard.task_instruction}

ANALYTICAL FRAMEWORK: {framework}

KEY QUESTIONS TO ANSWER:
{questions_text}

COMPLETENESS CRITERIA:
{completeness_text}

TASK STATE MAP:
{task_state_map}

ALL EXTRACTED FINDINGS ({len(active)} entries):
{findings_by_doc[:500000]}

YOUR JOB: Produce analytical outputs that the extraction workers CANNOT produce. Specifically:

1. CROSS-DOCUMENT ANALYSIS: Compare findings between documents. Identify conflicts, inconsistencies, and connections. Reference specific entry IDs.

2. CALCULATIONS: Compute derived values from extracted numbers. Show full arithmetic. Totals, percentages, ratios, deltas, financial exposure.

3. ISSUE FLAGS: Identify legal/regulatory/commercial issues. Be specific about what the issue is, why it matters, and cite the relevant provision.

4. RECOMMENDATIONS: For each flagged issue, recommend remediation. Be actionable — "add a whistleblower carve-out to Section 4.3" not "consider improving the provision."

5. ANSWERS TO KEY QUESTIONS: Address each key question above using the extracted findings. If a question cannot be answered from the available data, flag it as a gap.

6. TASK-STATE COMPLETION: Use the task state map as a temporary coverage ledger. Fill required fields, resolve relationships, perform closure checks, and flag rows where the state only has lexical facts but not the needed conclusion, calculation, classification, or artifact-specific output.

Return JSON with an "outputs" array. Each output:
{{
  "type": "analysis" | "calculation" | "strategy" | "gap",
  "content": "the analytical conclusion, calculation, issue flag, or recommendation",
  "source_document": "document name or null",
  "source_entries": ["e1", "e5"],
  "confidence": 0.0-1.0
}}

Produce 10-30 analytical outputs. Focus on quality — each output should contain a specific, verifiable conclusion that adds value beyond what the raw observations provide."""

    payload, tokens = call_model(caller, prompt, max_tokens=16384)

    outputs = payload.get("outputs", [])
    if not isinstance(outputs, list):
        outputs = []

    entries = []
    for o in outputs:
        if not isinstance(o, dict):
            continue
        content = str(o.get("content", "")).strip()
        if not content or len(content) < 20:
            continue

        entry_type = o.get("type", "analysis")
        if entry_type not in ("analysis", "calculation", "strategy", "gap"):
            entry_type = "analysis"

        source = None
        if o.get("source_document"):
            source = EntrySource(
                document=o["source_document"],
                section=None,
                evidence="",
            )

        try:
            conf = float(o.get("confidence", 0.7))
        except (ValueError, TypeError):
            conf = 0.7

        entries.append(Entry(
            id=gen_entry_id(), type=entry_type, content=content,
            source=source,
            epistemic=EpistemicStatus("inference", "unknown", ""),
            created_by=WorkerRecord("flash35_analyst", "direct_analysis", blackboard.iteration),
            confidence=conf,
            tags=[], status="active",
            supports_entries=o.get("source_entries", []) if isinstance(o.get("source_entries"), list) else [],
        ))

    return entries, tokens


def run_comparison_enrichment(blackboard: Blackboard, seed: dict,
                              caller: ModelCaller) -> tuple[list[Entry], int]:
    """Dedicated pass to produce cross-document comparisons.

    Targets the 'compare' work type deficit: many tasks require explicit
    side-by-side analysis between documents but the main extraction loop
    produces facts per-document without bridging them.
    """
    active = [e for e in blackboard.entries if e.status == "active"]
    if len(active) < 5:
        return [], 0

    docs = set()
    for e in active:
        if e.source and e.source.document:
            docs.add(e.source.document)
    if len(docs) < 2:
        return [], 0

    by_doc: dict[str, list[str]] = {}
    for e in active:
        doc = e.source.document if e.source else "cross_cutting"
        by_doc.setdefault(doc or "cross_cutting", []).append(
            f"[{e.id}] ({e.type}) {e.content[:300]}"
        )

    doc_summaries = ""
    for doc_name, items in sorted(by_doc.items()):
        doc_summaries += f"\n=== {doc_name} ({len(items)} entries) ===\n"
        for item in items[:50]:
            doc_summaries += f"  {item}\n"

    task_state_map = format_task_state_map(seed)

    prompt = f"""You are a senior analyst specializing in CROSS-DOCUMENT COMPARISON. Your job: produce explicit comparisons between documents.

TASK: {blackboard.task_instruction}

TASK STATE MAP:
{task_state_map}

DOCUMENTS AND FINDINGS ({len(docs)} documents, {len(active)} entries):
{doc_summaries[:400000]}

YOUR SOLE FOCUS: Produce COMPARISONS between documents. For each comparison:
- Name both documents being compared
- State what is being compared (a term, obligation, value, date, threshold, provision)
- State the specific difference, conflict, or alignment
- Cite entry IDs from BOTH documents

Types of comparisons to produce:
1. TERM CONFLICTS: Same concept defined differently across documents
2. NUMERICAL DELTAS: Different values for the same metric (calculate the difference)
3. OBLIGATION MISMATCHES: One document requires X, another contradicts or omits it
4. TEMPORAL CONFLICTS: Different dates, deadlines, or timelines for the same event
5. DEFINITIONAL GAPS: A term used in one document but not defined, while defined in another
6. COVERAGE GAPS: One document covers a topic the other omits entirely

Return JSON with a "comparisons" array. Each:
{{
  "type": "analysis",
  "content": "COMPARISON: [Doc A] vs [Doc B] — [specific finding with values/quotes]",
  "source_entries": ["entry_from_doc_a", "entry_from_doc_b"],
  "confidence": 0.0-1.0
}}

Produce 5-20 comparisons. Every output MUST reference at least 2 different documents. Single-document observations are REJECTED."""

    payload, tokens = call_model(caller, prompt, max_tokens=12288)

    comparisons = payload.get("comparisons", [])
    if not isinstance(comparisons, list):
        comparisons = []

    entries = []
    for c in comparisons:
        if not isinstance(c, dict):
            continue
        content = str(c.get("content", "")).strip()
        if not content or len(content) < 30:
            continue

        source_ids = c.get("source_entries", [])
        if not isinstance(source_ids, list):
            source_ids = []
        valid_sources = [s for s in source_ids if any(e.id == s for e in active)]

        source = None
        if valid_sources:
            ref = next((e for e in active if e.id == valid_sources[0]), None)
            if ref and ref.source:
                source = EntrySource(ref.source.document, ref.source.section, "")

        try:
            conf = float(c.get("confidence", 0.75))
        except (ValueError, TypeError):
            conf = 0.75

        entries.append(Entry(
            id=gen_entry_id(), type="analysis", content=content,
            source=source,
            epistemic=EpistemicStatus("inference", "unknown", ""),
            created_by=WorkerRecord("flash35_analyst", "comparison_enrichment", blackboard.iteration),
            confidence=conf,
            tags=["comparison_enrichment"], status="active",
            supports_entries=valid_sources,
        ))

    return entries, tokens
