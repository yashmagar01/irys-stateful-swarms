from __future__ import annotations

import json

from .blackboard import Blackboard
from .models import ModelCaller
from .worker_dispatch import call_model

ORCHESTRATOR_PROMPT = """\
You are the analytical orchestrator for a document analysis system.
Examine the current state and decide what work to do next.

TASK: {task_instruction}

DOCUMENTS:
{documents}

STATE: iteration={iteration}, entries={entry_counts}, budget={budget_pct}% used

OPEN SIGNALS:
{signals}

RECENT ENTRIES:
{recent}

DISPUTED:
{disputed}

Create 1-5 workers. For each:
{{"description": "specific task — be precise about what to extract or analyze", "reads_from_blackboard": ["e1"],
  "reads_from_documents": [{{"document": "name", "sections": ["Sec 4"]}}],
  "expected_output_type": "observation|analysis|calculation|strategy",
  "priority": "critical|high|medium", "addresses_signals": ["s2"],
  "search_queries": ["optional web search queries — use when facts need external verification"]}}

Return: {{"workers": [...]}}
OR: {{"action": "converge", "reasoning": "why", "remaining_gaps": [...]}}

GUIDELINES:
- CRITICAL signals first
- Unread sections → reader worker. Tell reader workers to ENUMERATE every individual item, not summarize.
- Facts without analysis → analysis worker that cross-references findings and identifies legal implications
- Numbers without arithmetic → calculation worker that shows full computation steps
- Budget >70%? Focus on critical only.
- Each worker description must be SPECIFIC: "Extract every numbered FTC request from Section 4" not "Read Section 4"
- Workers should extract ATOMIC facts — one per finding, with exact numbers, dates, party names
- TASK-STATE MAP signals define row types, required fields, relationships, and closure checks. Dispatch workers to populate those rows and bind facts into exact fields, not merely gather related snippets.
- ANALYSIS IS AS IMPORTANT AS EXTRACTION: After extracting facts, dispatch workers to:
  (a) Cross-reference findings between documents — identify conflicts, gaps, and implications
  (b) Flag legal/regulatory issues with specific statutory or regulatory citations
  (c) Calculate revenue impacts, percentages, and financial exposure from the raw numbers
  (d) Identify what is MISSING from the documents that should be present
  (e) For comparison tasks: systematically compare each item across sources
- Do NOT converge until analysis workers have reviewed the extracted facts
- EXTRACTION GAPS: If a document has many more items than we've extracted, dispatch targeted re-extraction
"""


def run_orchestrator(blackboard: Blackboard, caller: ModelCaller,
                     override: str = "") -> tuple[dict, int]:
    summary = blackboard.get_summary()

    # Build document info with extraction depth analysis
    doc_lines = []
    extraction_gaps = []
    for d in summary["documents"]:
        profile = d.get("structural_profile", {})
        expected = profile.get("numbered_items", 0)
        if not isinstance(expected, (int, float)):
            expected = 0
        doc_name = d["name"]
        actual = len([
            e for e in blackboard.entries
            if e.source and e.source.document == doc_name
            and e.status == "active"
            and e.type in ("observation", "analysis", "calculation")
        ])
        coverage_pct = round(actual / max(expected, 1) * 100) if expected > 0 else 0
        doc_lines.append(
            f"- {doc_name}: {d['read_status']}, "
            f"extracted={actual} entries"
            + (f" (expected ~{int(expected)}, coverage={coverage_pct}%)" if expected > 0 else "")
        )
        if expected > 0 and actual < expected * 0.7:
            extraction_gaps.append(
                f"EXTRACTION GAP: {doc_name} has ~{int(expected)} enumerable items "
                f"but only {actual} extracted ({coverage_pct}%). "
                f"Need {int(expected - actual)} more."
            )
    docs = "\n".join(doc_lines) or "None"

    if extraction_gaps:
        docs += "\n\nEXTRACTION DEPTH WARNINGS:\n" + "\n".join(extraction_gaps)

    sigs = "\n".join(
        f"- [{s.id}] [{s.priority}] {s.content}"
        for s in (summary["critical_signals"] + summary["high_signals"])[:15]
    ) or "None"

    recent = "\n".join(
        f"- [{e.type}] {e.content[:200]}"
        for e in summary["entries_this_iteration"][:15]
    ) or "None"

    disputed = "\n".join(
        f"- [{e.type}] (conf={e.confidence:.1f}) {e.content[:200]}"
        for e in summary["disputed_entries"][:10]
    ) or "None"

    prompt = ORCHESTRATOR_PROMPT.format(
        task_instruction=blackboard.task_instruction, documents=docs,
        iteration=summary["iteration"],
        entry_counts=json.dumps(summary["entry_counts"]),
        budget_pct=summary["budget_used_pct"], signals=sigs,
        recent=recent, disputed=disputed,
    )

    from .web_search import web_search_enabled
    if web_search_enabled():
        prompt += """
WEB SEARCH AVAILABLE: You can add "search_queries": ["query1", "query2"] to any worker.
Use this to:
- Verify case law citations, statutes, and regulations
- Look up current facts, dates, events, or entity information
- Find definitions of technical terms or industry standards
- Cross-check numerical claims against public sources
- Answer questions that require knowledge beyond the provided documents
Workers with search_queries will have web results injected into their context."""
    if override:
        prompt += f"\n\nIMPORTANT: {override}"

    payload, tokens = call_model(caller, prompt, max_tokens=4096)

    if "workers" not in payload and payload.get("action") != "converge":
        unread = []
        for d in summary["documents"]:
            if d["read_status"] != "fully_read":
                unread_secs = d.get("sections_unread", [])
                if unread_secs:
                    unread.append({
                        "description": (
                            f"Read and enumerate every individual item in "
                            f"'{unread_secs[0]}' of '{d['name']}'"
                        ),
                        "reads_from_blackboard": [],
                        "reads_from_documents": [
                            {"document": d["name"], "sections": [unread_secs[0]]}
                        ],
                        "expected_output_type": "observation",
                        "priority": "high",
                        "addresses_signals": [],
                    })
        if not unread:
            unread = [{
                "description": "Analyze all findings and identify gaps",
                "reads_from_blackboard": [],
                "reads_from_documents": [],
                "expected_output_type": "analysis",
                "priority": "high",
                "addresses_signals": [],
            }]
        payload = {"workers": unread[:5]}

    return payload, tokens
