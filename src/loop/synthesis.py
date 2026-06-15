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
import os
from pathlib import Path

from .hydration import build_evidence_context
from .llm import call_json, call_text
from .state import Board, Claim, Target

_CLAIMS_PER_TARGET = 48
_CONTENT_CAP = 500
_EVIDENCE_CAP = 220
_REPAIR_ENABLED = os.getenv("LOOP_SYNTHESIS_REPAIR", "1").strip() in (
    "1", "true", "yes",
)
_REPAIR_PACKET_CAP = int(os.getenv("LOOP_SYNTHESIS_REPAIR_PACKET_CAP", "320000"))
_REPAIR_DRAFT_CAP = int(os.getenv("LOOP_SYNTHESIS_REPAIR_DRAFT_CAP", "120000"))

_SYNTHESIS_HYDRATE = os.getenv("LOOP_SYNTHESIS_HYDRATE", "0").strip().lower() in (
    "1", "true", "yes",
)
_SYNTHESIS_HYDRATE_MAX = int(os.getenv("LOOP_SYNTHESIS_HYDRATE_MAX_CHARS", "400000"))


def _dedup_claims(claims, cap: int) -> list:
    """Remove near-duplicate claims by content fingerprint, keep highest-confidence."""
    seen: set[str] = set()
    out = []
    for c in claims:
        key = c.content[:200].lower().strip()
        if key not in seen:
            seen.add(key)
            out.append(c)
        if len(out) >= cap:
            break
    return out


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
    picked = _dedup_claims(derived + raw, _CLAIMS_PER_TARGET)
    return {
        "id": target.id,
        "need": target.need,
        "materiality": target.materiality,
        "status": target.status,
        "reason": target.reason,
        "claims": [
            {
                "id": c.id,
                "kind": c.kind,
                "content": c.content[:_CONTENT_CAP],
                "evidence": c.evidence[:_EVIDENCE_CAP],
                "source": c.source_doc,
                "section": c.source_section,
                "verified": c.verified,
                "confidence": round(c.confidence, 2),
                "support_refs": c.support_refs,
                "source_span": list(c.source_span) if c.source_span else None,
            }
            for c in picked
        ],
        "_claim_objects": picked,
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
            picked = claims[:per_unit]
            rows.append({
                "unit": u.name,
                "anchor": u.anchor,
                "status": u.status,
                "claims": [
                    {"id": c.id, "kind": c.kind, "content": c.content,
                     "evidence": c.evidence, "source": c.source_doc,
                     "support_refs": c.support_refs,
                     "source_span": list(c.source_span) if c.source_span else None}
                    for c in picked
                ] or [{"kind": "gap", "content": "no evidence gathered for this unit"}],
                "_claim_objects": picked,
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
        f"- {c.content}" + (f" (Source: {c.source_doc})" if c.source_doc else "")
        for c in reqs
    )


_SUPPLEMENTARY_PER_TARGET = 12
_SUPPLEMENTARY_CAP = 200_000


def _supplementary_evidence(board: Board) -> str:
    """Claims from waived critical/high targets - facts the investigation
    collected but whose parent questions were not formally resolved.
    Synthesis should use these where relevant rather than leaving them
    only in a limitations footnote.
    """
    blocks = []
    for t in board.targets:
        if t.status != "waived" or t.materiality not in ("critical", "high"):
            continue
        if t.reason.startswith("merged into"):
            continue
        bound = board.claims_for_target(t)
        if not bound:
            continue
        derived = sorted(
            (c for c in bound if c.is_derived), key=lambda c: -c.confidence)
        raw = sorted(
            (c for c in bound if not c.is_derived), key=lambda c: -c.confidence)
        picked = _dedup_claims(derived + raw, _SUPPLEMENTARY_PER_TARGET)
        if not picked:
            continue
        blocks.append({
            "question": t.need,
            "status": "partially_investigated",
            "materiality": t.materiality,
            "claims": [
                {"kind": c.kind, "content": c.content,
                 "evidence": c.evidence, "source": c.source_doc}
                for c in picked
            ],
        })
    if not blocks:
        return ""
    serialized = json.dumps(blocks, indent=1, default=str)[:_SUPPLEMENTARY_CAP]
    return (
        "\n\nSUPPLEMENTARY EVIDENCE from partially investigated questions "
        "(these facts were collected but their parent questions were not fully "
        "resolved - use them where they add relevant detail, numbers, dates, "
        "parties, or terms to the deliverable):\n"
        + serialized
    )


def plan_synthesis(smart_caller, board: Board) -> dict:
    """Allocate targets to deliverables — form is decided late, by judgment."""
    deliverables = board.metadata.get("deliverables", {})
    files = list(deliverables.values()) if deliverables else ["output.docx"]

    target_lines = "\n".join(
        f"{t.id} [{t.status}/{t.materiality}] {t.need}"
        f" ({len(t.claim_refs)} claims)"
        for t in board.targets
    )
    ob_lines = "\n".join(
        f"{o.id} [{o.status}/{o.coverage}/{'mandatory' if o.mandatory else 'optional'}]"
        f" {o.text} | {len([u for u in board.units_for(o.id) if u.status != 'waived'])} units"
        for o in board.obligations
    )

    prompt = f"""You are planning the final deliverable(s) of a completed investigation. All analytical work is done — your job is allocation and structure: which resolved questions feed which file, in what order, at what depth, in what form.

REQUEST:
{board.instruction}

ANSWER SHAPE: {board.metadata.get('answer_shape', '')}

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
        f"- [{t.status}] {t.need}" + (f" — {t.reason}" if t.reason else "")
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

        # Collect Claim objects from packets before JSON serialization
        all_packet_claims: list[Claim] = []
        for pb in packet_blocks:
            for pkt in pb.get("packets", []):
                all_packet_claims.extend(pkt.pop("_claim_objects", []))

        coverage_block = ""
        upackets = []
        if coverage:
            ob_ids = [str(c.get("obligation_id", "")) for c in coverage]
            upackets = unit_packets(board, obligation_ids=ob_ids)
            for up in upackets:
                for row in up.get("units", []):
                    all_packet_claims.extend(row.pop("_claim_objects", []))
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

        supplementary_block = _supplementary_evidence(board)

        source_text_block = ""
        if _SYNTHESIS_HYDRATE and all_packet_claims:
            req_claims = [c for c in board.claims if c.active and c.kind == "requirement"]
            hydrate_claims = all_packet_claims + req_claims

            evidence_context, hydrate_stats = build_evidence_context(
                board, hydrate_claims, max_chars=_SYNTHESIS_HYDRATE_MAX,
            )
            board.log(
                "synthesis_hydrate",
                f"{filename}: {hydrate_stats['merged_windows']} windows, "
                f"{hydrate_stats['chars']} chars"
                + (f", {hydrate_stats['dropped_windows']} dropped"
                   if hydrate_stats.get('dropped_windows') else ""),
                detail={"filename": filename, **hydrate_stats},
            )
            if evidence_context:
                source_text_block = (
                    "\n\nPRIMARY SOURCE TEXT backing the claims above. "
                    "The analysis packets decide what belongs in the deliverable; "
                    "use source text for exact wording, numbers, dates, parties, "
                    "citations, and to resolve any ambiguity in claim summaries. "
                    "Do not import facts from source text that are not reflected "
                    "in the analysis packets.\n"
                    + evidence_context
                )

        prompt = f"""You are writing the final deliverable of a completed expert investigation. The analysis below is your ONLY knowledge — write from it, never invent. Where claims carry evidence quotes and sources, use them for precision and citation.

ORIGINAL REQUEST:
{board.instruction}

FILE: {filename} - {file_plan.get('form', 'document')}
{format_rules}

{f'''BINDING REQUIREMENTS discovered in the sources — satisfy EVERY one (addressees, length minimums, mandatory elements, required references, procedural requests). If a length minimum exists, meet it with substance, not padding:
{requirement_block(board)}
''' if requirement_block(board) else ''}

ANALYSIS (per section, with resolved questions and their claims):
{json.dumps(packet_blocks, indent=1, default=str)[:400_000]}
{coverage_block}
{supplementary_block}
{source_text_block}

{f'''UNRESOLVED MATERIAL QUESTIONS (disclose honestly in a final Limitations note):
{residual_note}''' if residual_note else ''}

NUMERICAL FIDELITY: Every specific number, amount, percentage, date, case count, dollar figure, ratio, or calculation from the analysis MUST appear in the deliverable. Never round, paraphrase, or omit a concrete figure — if the analysis says "75 cases" or "24% deficiency rate" or "$712,500 gap", those exact numbers must be in the output.

Write the COMPLETE deliverable. Professional, specific, decision-ready. Every conclusion traceable to the analysis. No meta-commentary about the process."""

        _dump_packets(board, filename, {
            "sections": packet_blocks,
            "coverage_plan": coverage,
            "unit_packets": upackets,
        })
        text = call_text(
            smart_caller, board, prompt, kind="synthesize",
            max_tokens=32768, temperature=0.25,
        )
        if text and _REPAIR_ENABLED:
            repaired = _repair_synthesis(
                smart_caller, board,
                filename=filename,
                file_plan=file_plan,
                format_rules=format_rules,
                packet_blocks=packet_blocks,
                coverage_block=coverage_block,
                supplementary_block=supplementary_block,
                source_text_block=source_text_block,
                residual_note=residual_note,
                draft=text,
            )
            if _usable_repair(text, repaired):
                board.log(
                    "synthesis_repair",
                    f"{filename}: {len(text)} -> {len(repaired)} chars",
                )
                text = repaired
            else:
                board.log(
                    "synthesis_repair",
                    f"{filename}: repair discarded",
                    detail={"draft_chars": len(text),
                            "repair_chars": len(repaired or "")},
                )
        results[filename] = text or "(synthesis produced no content)"
        board.log("synthesize", f"{filename}: {len(text)} chars")

    return results


def _usable_repair(draft: str, repaired: str | None) -> bool:
    """Reject parse failures and obvious truncation from the repair pass."""
    if not repaired:
        return False
    cleaned = repaired.strip()
    if not cleaned:
        return False
    if len(draft) < 1200:
        return len(cleaned) >= len(draft) * 0.5
    return len(cleaned) >= max(1200, int(len(draft) * 0.6))


def _repair_synthesis(smart_caller, board: Board, *, filename: str,
                      file_plan: dict, format_rules: str,
                      packet_blocks: list[dict], coverage_block: str,
                      supplementary_block: str, source_text_block: str = "",
                      residual_note: str, draft: str) -> str:
    """Second-pass coverage editor.

    The first synthesis call writes. This pass checks whether packet-supported
    facts, numbers, issues, conflicts, recommendations, and required rows were
    actually rendered in the final artifact shape.
    """
    prompt = f"""You are the final coverage editor for an expert work product. The draft below may be well written but incomplete. Compare it against the analysis packets and rewrite the COMPLETE file so packet-supported material survives into the deliverable.

ORIGINAL REQUEST:
{board.instruction}

FILE: {filename} - {file_plan.get('form', 'document')}
{format_rules}

EDITORIAL RULES:
- Preserve correct draft content, names, dates, amounts, citations, and structure.
- Do not invent outside the analysis packets.
- Do not demote packet-supported material into limitations merely because a target status is waived, blocked, or open. Use limitations only for genuinely missing evidence or true blockers.
- For issue, discrepancy, conflict, comparison, checklist, and due-diligence deliverables, render each material item in a scoring-friendly structure: unique identifier, exact source location(s), concrete issue/difference, severity or priority, legal/commercial impact, and recommended resolution when the packets support one.
- For drafting deliverables, make the main draft incorporate all supported deal terms and make any separate issues list capture conflicts, open questions, and bracketed drafting choices explicitly.
- Exact numbers, dates, thresholds, percentages, parties, defined terms, vote counts, deadlines, statutory/regulatory references, and document section names are high-risk facts. If they are in the packets and material, include them verbatim.
- If the draft already covers an item, keep it. If the packets contain a material item the draft missed, add it in the proper section instead of appending a generic note.

ANALYSIS PACKETS:
{json.dumps(packet_blocks, indent=1, default=str)[:_REPAIR_PACKET_CAP]}
{coverage_block[:80_000]}
{supplementary_block[:100_000]}
{source_text_block[:200_000] if source_text_block else ''}

{f'''UNRESOLVED MATERIAL QUESTIONS:
{residual_note}''' if residual_note else ''}

DRAFT TO REPAIR:
{draft[:_REPAIR_DRAFT_CAP]}

Return only the complete revised deliverable. No commentary about the repair process."""

    return call_text(
        smart_caller, board, prompt, kind="synthesis_repair",
        max_tokens=32768, temperature=0.15,
    )


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
                {"id": o.id, "text": o.text, "coverage": o.coverage}
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
