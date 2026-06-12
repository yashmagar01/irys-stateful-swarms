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

# Completeness-driven tasks (extractions, schedules, term sheets) are scored
# on the long tail of specifics — packets must carry it. 3.5 flash takes 1M
# input tokens; starving synthesis to save prompt space is a false economy.
_CLAIMS_PER_TARGET = 48
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


def unit_packets(board: Board, obligation_ids: list[str] | None = None) -> list[dict]:
    """Unit-preserving packets: every non-waived unit survives into
    synthesis. Within-unit summarization is allowed; unit omission is not.
    """
    packets = []
    for ob in board.obligations:
        if not ob.set_valued or ob.status == "waived":
            continue
        if obligation_ids is not None and ob.id not in obligation_ids:
            continue
        units = [u for u in board.units_for(ob.id) if u.status != "waived"]
        if not units:
            continue
        # Per-unit claim budget shrinks as unit count grows — units are
        # never dropped, their evidence is just summarized harder.
        per_unit = max(3, min(8, 240 // max(len(units), 1)))
        rows = []
        for u in units:
            claims = [
                c for c in (board.find_claim(cid) for cid in u.claim_refs)
                if c is not None and c.active
            ]
            claims.sort(key=lambda c: (not c.is_derived, -c.confidence))
            rows.append({
                "unit": u.name,
                "anchor": u.anchor,
                "status": u.status,
                "claims": [
                    {"kind": c.kind, "content": c.content[:300],
                     "evidence": c.evidence[:150], "source": c.source_doc}
                    for c in claims[:per_unit]
                ] or [{"kind": "gap", "content": "no evidence gathered for this unit"}],
            })
        packets.append({
            "obligation": ob.text,
            "obligation_id": ob.id,
            "coverage": ob.coverage,
            "units": rows,
        })
    return packets


def requirement_block(board: Board) -> str:
    """All requirement claims — deliverable constraints discovered in sources.

    These bypass packet caps: a requirement is binding regardless of which
    target it is bound to.
    """
    reqs = [c for c in board.claims if c.active and c.kind == "requirement"]
    return "\n".join(
        f"- {c.content[:300]}" + (f" (Source: {c.source_doc})" if c.source_doc else "")
        for c in reqs
    )


def plan_synthesis(smart_caller, board: Board) -> dict:
    """Allocate targets to deliverables — form is decided late, by judgment."""
    deliverables = board.metadata.get("deliverables", {})
    files = list(deliverables.values()) if deliverables else ["output.docx"]

    target_lines = "\n".join(
        f"{t.id} [{t.status}/{t.materiality}] {t.need[:120]}"
        f" ({len(t.claim_refs)} claims)"
        for t in board.targets
    )
    ob_lines = "\n".join(
        f"{o.id} [{o.status}/{o.coverage}/{'mandatory' if o.mandatory else 'optional'}]"
        f" {o.text[:120]} | {len([u for u in board.units_for(o.id) if u.status != 'waived'])} units"
        for o in board.obligations
    )

    prompt = f"""You are planning the final deliverable(s) of a completed investigation. All analytical work is done — your job is allocation and structure: which resolved questions feed which file, in what order, at what depth, in what form.

REQUEST:
{board.instruction[:4000]}

ANSWER SHAPE: {board.metadata.get('answer_shape', '')[:600]}

OUTPUT FILES REQUIRED: {json.dumps(files)}

ANSWER CONTRACT (obligations the deliverables must satisfy; set-valued ones track units):
{ob_lines or '(none)'}

RESOLVED AND OPEN QUESTIONS:
{target_lines}

{f'''DELIVERABLE REQUIREMENTS DISCOVERED IN SOURCES (binding — the plan must satisfy every one):
{requirement_block(board)}
''' if requirement_block(board) else ''}
Rules:
- The same question can feed multiple files DIFFERENTLY (summary in a memo, full table in a spreadsheet, clause edits in a redline). Allocate accordingly.
- .xlsx files need data-shaped sections (tables); .docx files need prose/structured documents. Match form to file type and to what the request actually asks for.
- Closed targets carry the substance. Waived/blocked/open targets with critical/high materiality must appear in a limitations note, never silently dropped.
- COVERAGE PLAN: every mandatory exhaustive/material/native-complete obligation MUST be placed — say where its units are rendered (one row/subsection/clause per unit, in the source's own order/numbering when one exists), and list the required slots each unit must carry IF the obligation demands repeated fields (e.g. identifier, both sources' positions, difference, severity, quantified impact, recommendation). Derive slots from what the obligation's language demands — never invent ceremony for a summary obligation.

Return JSON:
{{"files": [{{
  "filename": "<exact filename>",
  "form": "<what kind of document this is, in plain words>",
  "sections": [{{"title": "...", "target_ids": ["..."], "guidance": "<depth/form for this section>"}}],
  "coverage": [{{"obligation_id": "...", "section": "<which section renders its units>", "unit_mode": "row|subsection|clause|inline", "required_slots": ["..."]}}]
}}]}}
Every required file must appear. Every mandatory set-valued obligation must appear in some file's coverage list."""

    parsed = call_json(smart_caller, board, prompt, kind="synthesis_plan",
                       max_tokens=8192)
    # Coverage guard: a mandatory set-valued obligation with units may not be
    # left unplaced — fail loudly into the plan, never silently.
    if isinstance(parsed, dict) and parsed.get("files"):
        covered = {
            str(c.get("obligation_id", ""))
            for f in parsed["files"] if isinstance(f, dict)
            for c in f.get("coverage", []) if isinstance(c, dict)
        }
        for ob in board.obligations:
            if (ob.set_valued and ob.mandatory and ob.status != "waived"
                    and board.units_for(ob.id) and ob.id not in covered):
                first = parsed["files"][0]
                first.setdefault("coverage", []).append({
                    "obligation_id": ob.id,
                    "section": "Coverage Appendix",
                    "unit_mode": "subsection",
                    "required_slots": [],
                })
                board.log("synthesis_plan",
                          f"coverage guard: {ob.id} unplaced — appended fallback")
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
        coverage = [c for c in file_plan.get("coverage", []) if isinstance(c, dict)]

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

        coverage_block = ""
        if coverage:
            ob_ids = [str(c.get("obligation_id", "")) for c in coverage]
            upackets = unit_packets(board, obligation_ids=ob_ids)
            plan_lines = "\n".join(
                f"- {c.get('obligation_id')}: render units in section "
                f"'{c.get('section')}' as {c.get('unit_mode', 'subsection')}"
                + (f", each unit carrying: {', '.join(str(s) for s in c.get('required_slots', []))}"
                   if c.get("required_slots") else "")
                for c in coverage
            )
            coverage_block = f"""
COVERAGE PLAN (binding structure — fill it, do not reorganize it):
{plan_lines}

UNIT PACKETS (every unit below MUST appear in the deliverable exactly once, in the source's own order/numbering; a unit without evidence appears with an explicit gap note, never silently dropped):
{json.dumps(upackets, indent=1, default=str)[:200_000]}
"""

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

{f'''BINDING REQUIREMENTS discovered in the sources — satisfy EVERY one (addressees, length minimums, mandatory elements, required references, procedural requests). If a length minimum exists, meet it with substance, not padding:
{requirement_block(board)}
''' if requirement_block(board) else ''}

ANALYSIS (per section, with resolved questions and their claims):
{json.dumps(packet_blocks, indent=1, default=str)[:400_000]}
{coverage_block}

{f'''UNRESOLVED MATERIAL QUESTIONS (disclose honestly in a final Limitations note):
{residual_note}''' if residual_note else ''}

Write the COMPLETE deliverable. Professional, specific, decision-ready. Every conclusion traceable to the analysis. No meta-commentary about the process."""

        _dump_packets(board, filename, {
            "sections": packet_blocks,
            "coverage_plan": coverage,
            "unit_packets": unit_packets(
                board,
                obligation_ids=[str(c.get("obligation_id", "")) for c in coverage],
            ) if coverage else [],
        })
        text = call_text(
            smart_caller, board, prompt, kind="synthesize",
            max_tokens=32768, temperature=0.25,
        )
        results[filename] = text or "(synthesis produced no content)"
        board.log("synthesize", f"{filename}: {len(text)} chars")

    return results


def _dump_packets(board: Board, filename: str, packet_blocks) -> None:
    """Persist exactly what synthesis saw — the funnel analyzer needs this
    to answer 'did this claim survive packet selection?' without inference."""
    if not board.output_dir:
        return
    try:
        d = Path(board.output_dir) / "loop"
        d.mkdir(parents=True, exist_ok=True)
        safe = filename.replace("/", "_").replace("\\", "_")
        (d / f"packets_{safe}.json").write_text(
            json.dumps(packet_blocks, indent=1, default=str), encoding="utf-8",
        )
    except OSError:
        pass


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
        "contract": {
            "obligations": len(board.obligations),
            "satisfied": sum(1 for o in board.obligations if o.status == "satisfied"),
            "waived": sum(1 for o in board.obligations if o.status == "waived"),
            "open_mandatory_at_stop": [
                {"id": o.id, "text": o.text[:120], "coverage": o.coverage}
                for o in board.open_mandatory_obligations()
            ],
            "units": len(board.units),
            "units_evidenced": sum(
                1 for u in board.units if u.status in ("evidenced", "analyzed")
            ),
        },
        "tokens": board.total_tokens_used,
        "cost_by_model": board.cost_by_model,
    }
    (d / "final_state.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8",
    )
