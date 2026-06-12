"""Synthesis — planner + per-deliverable generation.

By the time we get here the thinking is done: targets are resolved and
their claims carry the analysis. The planner is the intelligent act of
allocating closed targets to deliverables (the same target can feed a
memo as a summary and a spreadsheet as a full calculation table). Each
deliverable then gets its own synthesis call — editorial work, not
analytical work.
"""
from __future__ import annotations

import json
from pathlib import Path

from .llm import call_json, call_text
from .state import Board, Target

_CLAIMS_PER_TARGET = 14
_CONTENT_CAP = 500
_EVIDENCE_CAP = 220


def target_packet(board: Board, target: Target) -> dict:
    """Everything synthesis may use for one target — bounded."""
    bound = board.claims_for_target(target)
    derived = sorted(
        (c for c in bound if c.is_derived),
        key=lambda c: -c.confidence,
    )
    raw = sorted(
        (c for c in bound if not c.is_derived),
        key=lambda c: -c.confidence,
    )
    picked = (derived + raw)[:_CLAIMS_PER_TARGET]
    return {
        "id": target.id,
        "need": target.need,
        "materiality": target.materiality,
        "status": target.status,
        "reason": target.reason,
        "claims": [
            {
                "kind": c.kind,
                "content": c.content[:_CONTENT_CAP],
                "evidence": c.evidence[:_EVIDENCE_CAP],
                "source": c.source_doc,
                "section": c.source_section,
                "verified": c.verified,
                "confidence": round(c.confidence, 2),
            }
            for c in picked
        ],
    }


def plan_synthesis(smart_caller, board: Board) -> dict:
    """Allocate targets to deliverables — form is decided late, by judgment."""
    deliverables = board.metadata.get("deliverables", {})
    files = list(deliverables.values()) if deliverables else ["output.docx"]

    target_lines = "\n".join(
        f"{t.id} [{t.status}/{t.materiality}] {t.need[:120]}"
        f" ({len(t.claim_refs)} claims)"
        for t in board.targets
    )

    prompt = f"""You are planning the final deliverable(s) of a completed investigation. All analytical work is done — your job is allocation and structure: which resolved questions feed which file, in what order, at what depth, in what form.

REQUEST:
{board.instruction[:4000]}

ANSWER SHAPE: {board.metadata.get('answer_shape', '')[:600]}

OUTPUT FILES REQUIRED: {json.dumps(files)}

RESOLVED AND OPEN QUESTIONS:
{target_lines}

Rules:
- The same question can feed multiple files DIFFERENTLY (summary in a memo, full table in a spreadsheet, clause edits in a redline). Allocate accordingly.
- .xlsx files need data-shaped sections (tables); .docx files need prose/structured documents. Match form to file type and to what the request actually asks for.
- Closed targets carry the substance. Waived/blocked/open targets with critical/high materiality must appear in a limitations note, never silently dropped.

Return JSON:
{{"files": [{{
  "filename": "<exact filename>",
  "form": "<what kind of document this is, in plain words>",
  "sections": [{{"title": "...", "target_ids": ["..."], "guidance": "<depth/form for this section>"}}]
}}]}}
Every required file must appear."""

    parsed = call_json(smart_caller, board, prompt, kind="synthesis_plan",
                       max_tokens=8192)
    if not isinstance(parsed, dict) or not parsed.get("files"):
        # Fallback: all material targets into each file, flat.
        closed_ids = [t.id for t in board.targets if t.status == "closed"]
        parsed = {"files": [
            {"filename": f, "form": "document",
             "sections": [{"title": "Analysis", "target_ids": closed_ids,
                           "guidance": "complete answer"}]}
            for f in files
        ]}
        board.log("synthesis_plan", "planner fallback: flat allocation")
    return parsed


def synthesize(smart_caller, board: Board, plan: dict) -> dict[str, str]:
    """Generate each deliverable from its planned target packets."""
    results: dict[str, str] = {}
    deliverables = board.metadata.get("deliverables", {})
    required = list(deliverables.values()) if deliverables else ["output.docx"]

    # Guard: every required file must have a plan entry with its EXACT name.
    planned_names = {str(f.get("filename", "")) for f in plan.get("files", [])}
    closed_ids = [t.id for t in board.targets if t.status == "closed"]
    for name in required:
        if name in planned_names:
            continue
        # Try fuzzy match (planner drifted on the name) — rename in place.
        fuzzy = next(
            (f for f in plan.get("files", [])
             if str(f.get("filename", "")) not in required
             and Path(str(f.get("filename", ""))).suffix == Path(name).suffix),
            None,
        )
        if fuzzy is not None:
            board.log("synthesis_plan", f"renamed plan file {fuzzy.get('filename')} -> {name}")
            fuzzy["filename"] = name
            planned_names.add(name)
        else:
            plan.setdefault("files", []).append({
                "filename": name, "form": "document",
                "sections": [{"title": "Analysis", "target_ids": closed_ids,
                              "guidance": "complete answer"}],
            })
            board.log("synthesis_plan", f"added missing plan entry for {name}")
    # Drop hallucinated extras not in the required set.
    plan["files"] = [
        f for f in plan.get("files", [])
        if str(f.get("filename", "")) in required
    ]
    residuals = [
        t for t in board.targets
        if t.rank >= 2 and t.status in ("open", "blocked", "waived")
        and not t.reason.startswith("merged into")
    ]
    residual_note = "\n".join(
        f"- [{t.status}] {t.need[:150]}" + (f" — {t.reason[:100]}" if t.reason else "")
        for t in residuals
    )

    for file_plan in plan.get("files", []):
        filename = str(file_plan.get("filename", "output.docx"))
        is_xlsx = filename.lower().endswith(".xlsx")
        sections = file_plan.get("sections", [])

        packet_blocks = []
        for sec in sections:
            tids = [str(t) for t in sec.get("target_ids", [])]
            packets = [
                target_packet(board, t)
                for t in (board.find_target(tid) for tid in tids) if t
            ]
            packet_blocks.append({
                "section": str(sec.get("title", "")),
                "guidance": str(sec.get("guidance", "")),
                "packets": packets,
            })

        format_rules = (
            "FORMAT: Spreadsheet content. Use '## Sheet: <name>' to start each "
            "sheet, then markdown pipe tables (| col | col |). Every row of "
            "data the analysis supports — spreadsheets are for completeness, "
            "not summaries. No prose paragraphs."
            if is_xlsx else
            "FORMAT: Markdown that converts to a professional document. "
            "'#' for the title, '##'/'###' for sections, '-' for bullets, "
            "plain paragraphs for prose. Concrete numbers, exact names, "
            "citations to source documents inline like (Source: <doc>, <section>)."
        )

        prompt = f"""You are writing the final deliverable of a completed expert investigation. The analysis below is your ONLY knowledge — write from it, never invent. Where claims carry evidence quotes and sources, use them for precision and citation.

ORIGINAL REQUEST:
{board.instruction[:4000]}

FILE: {filename} — {file_plan.get('form', 'document')}
{format_rules}

ANALYSIS (per section, with resolved questions and their claims):
{json.dumps(packet_blocks, indent=1, default=str)[:120_000]}

{f'''UNRESOLVED MATERIAL QUESTIONS (disclose honestly in a final Limitations note):
{residual_note}''' if residual_note else ''}

Write the COMPLETE deliverable. Professional, specific, decision-ready. Every conclusion traceable to the analysis. No meta-commentary about the process."""

        text = call_text(
            smart_caller, board, prompt, kind="synthesize",
            max_tokens=32768, temperature=0.25,
        )
        results[filename] = text or "(synthesis produced no content)"
        board.log("synthesize", f"{filename}: {len(text)} chars")

    return results


def write_final_state(board: Board) -> None:
    """Stop-reason + residual ledger — the run must explain itself."""
    if not board.output_dir:
        return
    d = Path(board.output_dir) / "loop"
    d.mkdir(parents=True, exist_ok=True)
    summary = {
        "stop_reason": board.stop_reason,
        "iterations": board.iteration,
        "targets": {
            "total": len(board.targets),
            "closed": sum(1 for t in board.targets if t.status == "closed"),
            "waived": sum(1 for t in board.targets if t.status == "waived"),
            "blocked": sum(1 for t in board.targets if t.status == "blocked"),
            "open_at_stop": [
                {"id": t.id, "materiality": t.materiality, "need": t.need,
                 "blockers": board.target_blockers(t)}
                for t in board.open_targets()
            ],
        },
        "claims": {
            "total": len(board.claims),
            "derived": sum(1 for c in board.claims if c.is_derived),
            "unbound": len(board.unbound_claims()),
        },
        "tokens": board.total_tokens_used,
        "cost_by_model": board.cost_by_model,
    }
    (d / "final_state.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )
