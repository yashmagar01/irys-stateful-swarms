"""Seed phase: decompose the task before extraction begins.

Produces key questions, extraction focus areas, and analytical framework
that guide extraction workers.
"""
from __future__ import annotations

import json

from .blackboard import Blackboard
from .models import ModelCaller, Signal, gen_signal_id
from .worker_dispatch import call_model


def _build_grouped_catalog(documents) -> str:
    """Group documents by parent directory for a compact catalog."""
    from collections import defaultdict
    groups = defaultdict(list)
    for d in documents:
        path = ""
        if hasattr(d, "_lazy_doc") and d._lazy_doc is not None:
            path = d._lazy_doc.metadata.get("path", "")
        if not path:
            groups["(root)"].append(d)
            continue
        parts = path.replace("\\", "/").split("/")
        if len(parts) >= 3:
            parent = "/".join(parts[-3:-1])
        elif len(parts) >= 2:
            parent = parts[-2]
        else:
            parent = "(root)"
        groups[parent].append(d)

    lines = []
    for group_name in sorted(groups.keys()):
        docs_in_group = groups[group_name]
        if len(docs_in_group) <= 10:
            for d in docs_in_group:
                lines.append(f"  [{group_name}] {d.name} ({d.size_bytes} bytes)")
        else:
            total_size = sum(d.size_bytes for d in docs_in_group)
            lines.append(
                f"  [{group_name}] {len(docs_in_group)} files, "
                f"{total_size // 1024}KB total"
            )
            for d in docs_in_group[:3]:
                lines.append(f"    e.g. {d.name}")
            lines.append(f"    ... and {len(docs_in_group) - 3} more")
    return "\n".join(lines)


def generate_seed(blackboard: Blackboard,
                  caller: ModelCaller) -> tuple[dict, int]:
    """Read task and structural profiles, then produce an analytical plan.

    Returns (seed_plan, tokens_used).
    """
    large_corpus = len(blackboard.documents) > 50
    if large_corpus:
        doc_summary = _build_grouped_catalog(blackboard.documents)
        corpus_note = (
            f"\nIMPORTANT: This is a LARGE corpus ({len(blackboard.documents)} documents). "
            f"Documents are grouped by directory. In EXTRACTION FOCUS, reference documents by their "
            f"directory group name (e.g. 'sec/10-K', 'ir/news-releases') or by example filenames "
            f"shown in the catalog. The system will load matching files automatically.\n"
        )
    else:
        doc_summary = "\n".join(
            f"- {d.name}: {d.size_bytes} bytes, {d.read_status}, "
            f"headings={d.headings[:20]}, "
            f"profile={json.dumps(d.structural_profile or {})}"
            for d in blackboard.documents
        )
        corpus_note = ""

    prompt = f"""You are a senior analyst planning an investigation. Before any documents are read in detail, you need to create an analytical plan.

TASK: {blackboard.task_instruction}
{corpus_note}
DOCUMENTS AVAILABLE (not yet read in detail — only structure/headings known):
{doc_summary}

Based on the task instruction and document structure, produce a plan:

1. KEY QUESTIONS: What specific questions must this investigation answer? Be concrete — "What is the total consideration?" not "Analyze financial terms." List 5-15 questions.

2. EXTRACTION FOCUS: For each document, what specific things should the extraction workers look for? Reference document names and section headings. Be specific — "In merger-agreement.docx, Section 4 (Representations), extract every individual representation and warranty" not "Read the agreement."

3. ANALYTICAL FRAMEWORK: What kind of analysis does this task require?
   - Is this an extraction task (pull out all terms/facts)?
   - A comparison task (compare two or more documents)?
   - A drafting task (produce a new document based on source material)?
   - An issue-flagging task (identify problems, risks, missing items)?
   - A calculation task (compute financial figures)?
   Describe the approach in 2-3 sentences.

4. CONTEXT ENRICHMENT NOTES: In plain language, enrich the task context before finalizing the plan. Do NOT build a fixed ontology, formal graph, or hard-coded entity schema. Instead, reason naturally about:
   - what domain, transaction, dispute, product, market, scientific question, software system, or other space the task appears to live in;
   - what explicit or implied people, organizations, assets, documents, rules, claims, metrics, time periods, or other objects matter;
   - what relationships or unknown slots must be resolved;
   - what subquestions workers should ask to turn unclear context into useful state.
   Fold the best of this thinking into the KEY QUESTIONS, EXTRACTION FOCUS, ANALYTICAL FRAMEWORK, and COMPLETENESS CRITERIA. Keep the notes unstructured and task-shaped; legal tasks may naturally mention parties, claims, pleadings, clauses, or authorities, while finance, science, market, software, or other tasks should use their own useful framing.

5. TASK STATE MAP: Create a temporary, task-induced coverage ledger in plain language. This is NOT a fixed ontology or formal graph. Identify the object rows that must be completed for this task and the fields/relationships needed to make each row judgeable. Examples across domains: custodians with role/status/device/deadline fields; comments with requested change/decision/rationale fields; asset-pool stats with source/calculation/table fields; clauses with counterparty edit/effect/recommendation fields. Include 3-10 rows.

6. COMPLETENESS CRITERIA: How will we know the investigation is thorough enough? What must the final deliverable contain to be considered complete? List 5-10 concrete criteria.

Return JSON:
{{"key_questions": ["question 1", "question 2"],
  "extraction_focus": [{{"document": "name", "focus": "what to look for"}}],
  "analytical_framework": "description of approach",
  "context_enrichment": "plain-language notes on task context, implied objects, relationships, unknown slots, and useful subquestions; no ontology or graph",
  "task_state_map": [
    {{
      "object_type": "task-shaped row type, e.g. custodian, comment, covenant, asset-pool statistic, markup issue",
      "required_fields": ["field workers must fill exactly"],
      "relationships": ["cross-document links or dependencies to resolve"],
      "closure_checks": ["how to know this row type is complete"],
      "worker_questions": ["targeted question workers should answer"]
    }}
  ],
  "completeness_criteria": ["criterion 1", "criterion 2"]}}"""

    payload, tokens = call_model(caller, prompt, max_tokens=4096)
    _ensure_task_state_map(payload)
    return payload, tokens


def seed_to_signals(seed: dict, blackboard: Blackboard) -> None:
    """Convert seed plan into blackboard signals that guide extraction."""
    for row in _task_state_map_rows(seed):
        blackboard.add_signal(Signal(
            id=gen_signal_id(), type="question",
            content=(
                f"Populate task-state row: {_format_task_state_map_row(row)} "
                f"fields={', '.join(row.get('required_fields', [])[:8])}; "
                f"closure={'; '.join(row.get('closure_checks', [])[:3])}"
            ),
            origin_entry="seed_plan", priority="high",
            status="open", iteration_created=0,
        ))

    for q in seed.get("key_questions", []):
        if isinstance(q, str) and q.strip():
            blackboard.add_signal(Signal(
                id=gen_signal_id(), type="question", content=q.strip(),
                origin_entry="seed_plan", priority="high",
                status="open", iteration_created=0,
            ))

    for focus in seed.get("extraction_focus", []):
        if isinstance(focus, dict):
            doc = focus.get("document", "")
            what = focus.get("focus", "")
            if doc and what:
                blackboard.add_signal(Signal(
                    id=gen_signal_id(), type="read_request",
                    content=f"In '{doc}': {what}",
                    origin_entry="seed_plan", priority="high",
                    status="open", iteration_created=0,
                ))


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _task_state_map_rows(seed: dict) -> list[dict]:
    rows = seed.get("task_state_map", [])
    if not isinstance(rows, list):
        return []
    clean = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        object_type = str(row.get("object_type", "")).strip()
        if not object_type:
            continue
        clean.append({
            "object_type": object_type,
            "required_fields": _as_str_list(row.get("required_fields", [])),
            "relationships": _as_str_list(row.get("relationships", [])),
            "closure_checks": _as_str_list(row.get("closure_checks", [])),
            "worker_questions": _as_str_list(row.get("worker_questions", [])),
        })
    return clean


def _ensure_task_state_map(seed: dict) -> None:
    if _task_state_map_rows(seed):
        seed["task_state_map"] = _task_state_map_rows(seed)
        return
    seed["task_state_map"] = [{
        "object_type": "task-required item",
        "required_fields": [
            "specific answer, extraction, comparison, calculation, or drafting element",
            "source support",
            "relationship to the user's requested deliverable",
            "artifact form needed in the final output",
        ],
        "relationships": [
            "connect each required item to the relevant source documents and key questions",
        ],
        "closure_checks": [
            "every key question and completeness criterion has a source-grounded answer, explicit gap, or required artifact element",
        ],
        "worker_questions": [
            "what rows, fields, calculations, classifications, or relationships must be completed for this task?",
        ],
    }]


def _format_task_state_map_row(row: dict) -> str:
    parts = [f"TASK STATE MAP ROW: {row.get('object_type', 'task object')}"]
    fields = row.get("required_fields") or []
    relationships = row.get("relationships") or []
    closure = row.get("closure_checks") or []
    questions = row.get("worker_questions") or []
    if fields:
        parts.append("Required fields: " + "; ".join(fields))
    if relationships:
        parts.append("Relationships to resolve: " + "; ".join(relationships))
    if closure:
        parts.append("Closure checks: " + "; ".join(closure))
    if questions:
        parts.append("Worker questions: " + "; ".join(questions))
    return ". ".join(parts)


def format_task_state_map(seed: dict) -> str:
    rows = _task_state_map_rows(seed)
    if not rows:
        return "None specified."
    return "\n".join(f"- {_format_task_state_map_row(row)}" for row in rows)
