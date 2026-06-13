"""Control plane — seed, controller, ledger maintenance.

The controller is a scheduler, not a global reasoner: it sees compact
target cards plus bookkeeping facts and GENERATES actions (it is not
limited to a precomputed menu). Cost is controlled by what it sees, not
by constraining what it can decide.

All three calls here use the smart model tier — these are the judgment
calls worth paying for.
"""
from __future__ import annotations

import json

from .llm import call_json
from .state import Board, Obligation, Target, Unit
from .triage import catalog_summary

MAX_ACTIONS_PER_ITERATION = 6
MAINTENANCE_EVERY = 3
MAINTENANCE_OPEN_THRESHOLD = 25


# --- SEED ---

def seed_targets(smart_caller, board: Board) -> None:
    """Think about the question before reading anything.

    The seed produces the first version of the target ledger — hypotheses
    about what 'done' means, not a fixed plan.
    """
    deliverables = board.metadata.get("deliverables", {})
    deliverables_note = (
        f"\nEXPECTED OUTPUT FILES: {json.dumps(deliverables)}"
        if deliverables else ""
    )
    doc_lines = "\n".join(
        f"- {s.path_hint}/{s.name} ({s.size_bytes // 1024}KB)"
        for s in board.sources[:80]
    )
    more = f"\n... and {len(board.sources) - 80} more" if len(board.sources) > 80 else ""

    prompt = f"""You are a top-tier expert planning how to answer a complex request. Do NOT answer it. Think about what a complete, professional answer would have to resolve — then write that as a list of concrete questions (targets).

REQUEST:
{board.instruction[:6000]}{deliverables_note}

AVAILABLE SOURCES (metadata only — nothing has been read yet):
{doc_lines}{more}

Produce two things:

1. The target ledger: 5-14 targets. Each target is a question the investigation must close. Good targets are semantic ("reconcile the share counts across documents", "determine total 10-year cost including escalations"), never formatting ("include a table").

2. The answer contract: what the final answer OWES the user, derived from the instruction's own words. Each obligation has a coverage standard read from the language:
   - "exhaustive": the instruction demands accounting for EVERY repeated item ("compare each provision", "identify all issues", "extract the terms") — missing one item fails the user.
   - "material": every material item, with omissions explained.
   - "representative": examples suffice.
   - "native-complete": a complete work product (a full draft/agreement/letter), not an item ledger.
   - "summary": a concise synthesis is what was asked.
   An instruction can mix obligations with different standards. If an obligation covers repeated items, name the unit of account in the task's own language (provision, request category, policy term, issue, claim, section) — units themselves will be discovered during reading.
   Be honest about standards: "exhaustive" ONLY where the instruction's words demand accounting for every repeated item; drafting a complete document is "native-complete", never "exhaustive". "mandatory" means the user would reject the answer without it — most obligations beyond the core ask are optional.

Also state what would make the answer excellent (answer_shape) — depth, rigor, what a demanding expert reader would check first.

Return JSON:
{{"targets": [{{"need": "...", "materiality": "critical|high|medium|low"}}],
 "obligations": [{{"text": "<what the answer owes, in the instruction's words>", "coverage": "exhaustive|material|representative|native-complete|summary", "mandatory": true/false, "unit_kind": "<the repeated item, or empty>"}}],
 "answer_shape": "<3-5 sentences>"}}"""

    parsed = call_json(smart_caller, board, prompt, kind="seed", max_tokens=8192)
    if isinstance(parsed, dict):
        for t in parsed.get("targets", []):
            if not isinstance(t, dict):
                continue
            need = str(t.get("need", "")).strip()
            if not need:
                continue
            materiality = str(t.get("materiality", "medium"))
            if materiality not in ("critical", "high", "medium", "low"):
                materiality = "medium"
            board.add_target(Target(
                need=need, materiality=materiality,
                created_iteration=0, proposed_by="seed",
            ))
        for ob in parsed.get("obligations", []) if board.metadata.get("contract_enabled") else []:
            if not isinstance(ob, dict):
                continue
            text = str(ob.get("text", "")).strip()
            if not text:
                continue
            coverage = str(ob.get("coverage", "material"))
            if coverage not in ("exhaustive", "material", "representative",
                                "native-complete", "summary"):
                coverage = "material"
            unit_kind = str(ob.get("unit_kind", "")).strip()
            if unit_kind:
                text = f"{text} [unit: {unit_kind}]"
            board.add_obligation(Obligation(
                text=text, origin="instruction", coverage=coverage,
                mandatory=bool(ob.get("mandatory", True)),
            ))
        board.metadata["answer_shape"] = str(parsed.get("answer_shape", ""))[:2000]
    if not board.targets:
        # Fallback: one umbrella target so the loop can run.
        board.add_target(Target(
            need=f"Answer completely: {board.instruction[:300]}",
            materiality="critical", proposed_by="seed_fallback",
        ))
    board.log("seed", f"{len(board.targets)} initial targets")


# --- CONTROLLER ---

def controller_decide(smart_caller, board: Board, last_summary: dict, *,
                      max_iterations: int = 12, closeout: bool = False) -> dict:
    """One iteration's decision: target updates + actions + converge flag."""
    open_cards = [board.target_card(t) for t in board.open_targets()[:25]]
    resolved = [
        f"{t.id} [{t.status}] {t.need[:90]}" for t in board.resolved_targets()
    ]
    unbound = board.unbound_claims()
    unbound_sample = "\n".join(
        f"  {c.id} [{c.kind}] {c.content[:140]}" for c in unbound[:5]
    )
    close_recs = [
        e.detail.get("target_id", "") + ": " + e.summary
        for e in board.recent_events(board.iteration - 1)
        if e.kind == "close_recommendation"
    ]

    prompt = f"""You are the controller of an investigation. Each round you decide: which questions to resolve (close/waive/block), and what work to dispatch next. You are a scheduler — workers do the deep reasoning; you allocate effort where it moves the answer most.

TASK:
{board.instruction[:2000]}

ANSWER SHAPE: {board.metadata.get('answer_shape', '')[:600]}

ITERATION {board.iteration} of {max_iterations} | budget used {board.budget_used_pct()}%
{'''
CLOSE-OUT MODE: Open material targets have stopped shrinking and iterations are finite. This round you MUST resolve every open target: CLOSE it if its claims defensibly answer it; WAIVE it with a reason if resolving it would not materially change the answer; BLOCK it with a reason if it cannot be answered from available sources (e.g. it requires a document that is not in the corpus — "obtain document X" targets are blocked, never left open). You may keep at most 2 targets open, each with exactly one final action dispatched this round.
''' if closeout else ''}

ANSWER CONTRACT (what the final answer owes the user; units are repeated items being tracked under each obligation):
{json.dumps([board.obligation_card(o) for o in board.obligations], indent=1) if board.obligations else '(no obligations derived)'}

OPEN TARGETS (cards with computed blockers):
{json.dumps(open_cards, indent=1)}

RESOLVED: {'; '.join(resolved) if resolved else '(none yet)'}

UNBOUND CLAIMS: {len(unbound)} not yet connected to any target.
{unbound_sample}

ANALYST CLOSE RECOMMENDATIONS FROM LAST ROUND:
{chr(10).join(close_recs) if close_recs else '(none)'}

LAST ROUND RESULTS: {json.dumps(last_summary)}

SOURCES:
{catalog_summary(board)}

AVAILABLE ACTION KINDS:
- read {{source_id, focus, target_ids, depth}} — extract evidence from a source (use focus to direct attention). depth "exhaustive" adds a full-inventory pass capturing EVERY term/amount/date/exception — use it when an obligation's coverage standard requires accounting for every repeated item this source contains (exhaustive/material obligations over term-dense documents: policies, schedules, term sheets, request lists). depth "focused" (default) when key provisions matter more than every detail.
- search {{query, target_ids}} — web search for external knowledge (current law, standards, public facts not in sources)
- bind {{}} — connect unbound claims to targets (dispatch when unbound count is high)
- analyze {{target_id, instruction}} — promote a target's evidence into conclusions/calculations/issues/recommendations
- verify {{claim_ids}} — adversarially check material derived claims against sources

DECISION RULES:
- Evidence sitting unanalyzed beats reading more. If a target has raw claims and no derived claims, analyze it before dispatching new reads.
- High unbound count → bind before anything else can be judged accurately.
- Close a target ONLY when its derived claims genuinely answer the need. Waive with a reason if not worth pursuing. Block with a reason if it cannot be answered from available sources/budget.
- Read unread "definite" sources early; pull "unlikely" sources in only if evidence demands it.
- Converge when every critical/high target is closed/waived/blocked AND every mandatory obligation is satisfied or explicitly waived, and another round would not materially improve the answer. Do NOT converge while many claims remain unbound — dispatch bind first so closure judgments see all the evidence.
- Mark an obligation "satisfied" only when its units are evidenced (or it is not set-valued and its substance is covered by closed targets). Waive only with a reason the user would accept. An exhaustive obligation with unevidenced units is NOT satisfied — dispatch reads/bind for those units instead.

Return JSON:
{{"reasoning": "<2-4 sentences>",
 "target_updates": [{{"target_id": "...", "status": "closed|waived|blocked", "reason": "..."}}],
 "obligation_updates": [{{"obligation_id": "...", "status": "satisfied|waived", "reason": "..."}}],
 "actions": [{{"kind": "read|search|bind|analyze|verify", ...params}}],
 "converge": true/false,
 "converge_reason": "<if converging>"}}
Max {MAX_ACTIONS_PER_ITERATION} actions. Actions run in parallel — make them independent."""

    parsed = call_json(smart_caller, board, prompt, kind="controller", max_tokens=8192)
    if not isinstance(parsed, dict):
        return {"actions": [], "converge": False,
                "reasoning": "controller parse failure"}

    updates = 0
    for u in parsed.get("target_updates", []):
        if not isinstance(u, dict):
            continue
        status = str(u.get("status", ""))
        if status not in ("closed", "waived", "blocked"):
            continue
        if board.resolve_target(
            str(u.get("target_id", "")), status, str(u.get("reason", ""))[:300],
        ):
            updates += 1
    for u in parsed.get("obligation_updates", []):
        if not isinstance(u, dict):
            continue
        ob = board.find_obligation(str(u.get("obligation_id", "")))
        status = str(u.get("status", ""))
        if ob is None or status not in ("satisfied", "waived"):
            continue
        ob.status = status
        ob.reason = str(u.get("reason", ""))[:300]
        updates += 1

    actions = [
        a for a in parsed.get("actions", [])
        if isinstance(a, dict) and a.get("kind") in
        ("read", "search", "bind", "analyze", "verify")
    ][:MAX_ACTIONS_PER_ITERATION]

    board.log(
        "controller",
        f"iter {board.iteration}: {updates} target updates, "
        f"{len(actions)} actions, converge={bool(parsed.get('converge'))}",
        detail={"reasoning": str(parsed.get("reasoning", ""))[:500],
                "actions": [a.get("kind") for a in actions]},
    )
    return {
        "actions": actions,
        "converge": bool(parsed.get("converge")),
        "converge_reason": str(parsed.get("converge_reason", ""))[:300],
        "reasoning": str(parsed.get("reasoning", ""))[:500],
    }


# --- LEDGER MAINTENANCE ---

def maintain_ledger(smart_caller, board: Board, *, closeout: bool = False) -> None:
    """Groom the target ledger: merge duplicates, waive low-value, reprioritize.

    Workers propose targets freely; this is where sprawl gets cleaned up
    by judgment instead of prevented by gates.
    """
    open_targets = board.open_targets()
    if len(open_targets) < 2:
        return
    listing = "\n".join(
        f"{t.id} [{t.materiality}, by {t.proposed_by}, iter {t.created_iteration}, "
        f"{len(t.claim_refs)} claims] {t.need}"
        for t in open_targets
    )

    prompt = f"""You are grooming the question ledger of an investigation. Workers propose questions freely — your job is judgment: merge duplicates, waive what is not worth pursuing, fix priorities, sharpen vague questions.

TASK:
{board.instruction[:1500]}

OPEN QUESTIONS:
{listing}
{'''
The investigation is CLOSING: waive any open question that will not materially change the final answer (with a reason). Keep only what genuinely blocks a professional deliverable.
''' if closeout else ''}
Return JSON:
{{"ops": [
  {{"op": "merge", "keep": "<id>", "merge_ids": ["<ids absorbed into keep>"], "need": "<sharpened need for keep, optional>"}},
  {{"op": "waive", "target_id": "...", "reason": "..."}},
  {{"op": "reprioritize", "target_id": "...", "materiality": "critical|high|medium|low"}},
  {{"op": "rephrase", "target_id": "...", "need": "<sharper, closeable phrasing>"}}
]}}
Only include ops that genuinely improve the ledger. An empty ops list is a valid answer."""

    parsed = call_json(smart_caller, board, prompt, kind="maintenance", max_tokens=8192)
    if not isinstance(parsed, dict):
        return
    applied = 0
    for op in parsed.get("ops", []):
        if not isinstance(op, dict):
            continue
        name = op.get("op")
        if name == "merge":
            keep = board.find_target(str(op.get("keep", "")))
            if keep is None or not keep.is_open:
                continue
            for mid in op.get("merge_ids", []):
                merged = board.find_target(str(mid))
                if merged is None or merged.id == keep.id or not merged.is_open:
                    continue
                for cid in merged.claim_refs:
                    board.bind_claim(cid, [keep.id])
                board.resolve_target(merged.id, "waived", f"merged into {keep.id}")
                applied += 1
            new_need = str(op.get("need", "")).strip()
            if new_need:
                keep.need = new_need
        elif name == "waive":
            if board.resolve_target(
                str(op.get("target_id", "")), "waived",
                str(op.get("reason", "low value"))[:300],
            ):
                applied += 1
        elif name == "reprioritize":
            t = board.find_target(str(op.get("target_id", "")))
            m = str(op.get("materiality", ""))
            if t is not None and m in ("critical", "high", "medium", "low"):
                t.materiality = m
                applied += 1
        elif name == "rephrase":
            t = board.find_target(str(op.get("target_id", "")))
            need = str(op.get("need", "")).strip()
            if t is not None and need:
                t.need = need
                applied += 1
    board.log("maintenance", f"applied {applied} ledger ops")


def should_maintain(board: Board) -> bool:
    if board.iteration > 0 and board.iteration % MAINTENANCE_EVERY == 0:
        return True
    return len(board.open_targets()) > MAINTENANCE_OPEN_THRESHOLD


# --- REFRAME (the blackboard rebuild) ---

def reframe_ledger(smart_caller, board: Board) -> None:
    """Rebuild the question ledger from everything now known.

    The seed ran on metadata and zero understanding; workers propose
    questions from local discoveries. Nobody else ever asks the global
    question: knowing what we NOW know, what SHOULD the question set be?
    This pass re-derives it — opening what is newly visible, splitting
    coarse bundles into per-item questions, challenging stale closures,
    and updating the living answer_shape. Closure is defeasible here.
    """
    all_targets = "\n".join(
        f"{t.id} [{t.status}/{t.materiality}, {len(t.claim_refs)} claims]"
        f" {t.need[:110]}"
        + (f" | resolved: {t.reason[:60]}" if t.reason else "")
        for t in board.targets
        if not t.reason.startswith("merged into")
        and not t.reason.startswith("split into")
    )
    derived = [c for c in board.claims if c.active and c.is_derived]
    best_derived = "\n".join(
        f"- [{c.kind}] {c.content[:140]}"
        for c in sorted(derived, key=lambda c: -c.confidence)[:25]
    )
    sources_read = "\n".join(
        f"- {s.name} ({s.read_status})" for s in board.sources[:40]
    )
    reopen = board.reopen_candidates()
    reopen_text = "\n".join(
        f"- {r['target_id']}: {r['need']} ({r['new_claims']} new claims,"
        f" {r['disturbed_basis']} basis claims disturbed)"
        for r in reopen[:15]
    )

    requirement_claims = [
        c for c in board.claims if c.active and c.kind == "requirement"
    ]
    req_text = "\n".join(f"- [{c.id}] {c.content[:140]}" for c in requirement_claims[:20])
    ob_lines = []
    for o in board.obligations:
        units = board.units_for(o.id)
        unit_sample = ", ".join(u.name[:40] for u in units[:8])
        ob_lines.append(
            f"{o.id} [{o.status}/{o.coverage}/{'mandatory' if o.mandatory else 'optional'}]"
            f" {o.text[:110]} | {len(units)} units"
            + (f" (e.g. {unit_sample})" if unit_sample else "")
        )

    prompt = f"""You are REBUILDING the working state of an investigation mid-flight. The original questions and answer contract were written before any document was read. The investigation has since built real understanding — even what the answer NEEDS should evolve with that understanding. Your job: re-derive what the question set and the answer contract SHOULD be, knowing everything now known, and repair the gap.

TASK:
{board.instruction[:4000]}

CURRENT UNDERSTANDING OF WHAT A GREAT ANSWER NEEDS:
{board.metadata.get('answer_shape', '')[:800]}

SOURCES (read state):
{sources_read}

ANSWER CONTRACT (obligations and their tracked units):
{chr(10).join(ob_lines) or '(none)'}

DELIVERABLE REQUIREMENTS DISCOVERED IN SOURCES (not yet folded into the contract):
{req_text or '(none)'}

CURRENT QUESTION LEDGER (open and resolved):
{all_targets}

STRONGEST CONCLUSIONS SO FAR:
{best_derived}

CLOSED QUESTIONS DISTURBED BY LATER EVIDENCE (reopen candidates):
{reopen_text or '(none)'}

CLAIM BASE: {len(board.claims)} claims, {len(derived)} derived, {len(board.unbound_claims())} unbound.

Repair operations:
1. CONTRACT — what the answer owes should grow with understanding:
   - add_obligation: an obligation the corpus has revealed (including folding in discovered requirements above).
   - adjust_coverage: the corpus showed the real structure (e.g. the request asks to "review the agreement" but the source is organized as 18 numbered request categories — coverage becomes exhaustive over those units). Give the reason.
   - waive_obligation: understanding shows it does not matter; reason required.
2. UNITS — repair the coverage ledger: add units visible in source structure that reading missed, waive out-of-scope units (with reason). Units are source-native items (a numbered category, a named provision, a specific term), never speculative.
3. QUESTIONS — open new questions evidence revealed; reopen closed questions whose closure later evidence undermines; reprioritize. Split a question ONLY if its parts genuinely need separate investigation AND remaining capacity can service them — coverage of repeated items is the units' job, not the question ledger's.
4. UPDATE answer_shape to current understanding of excellence.

Return JSON:
{{"add_obligations": [{{"text": "...", "coverage": "exhaustive|material|representative|native-complete|summary", "mandatory": true/false}}],
 "adjust_coverage": [{{"obligation_id": "...", "coverage": "...", "reason": "..."}}],
 "waive_obligations": [{{"obligation_id": "...", "reason": "..."}}],
 "add_units": [{{"obligation_id": "...", "name": "...", "anchor": "<source/section>"}}],
 "waive_units": [{{"unit_id": "...", "reason": "..."}}],
 "new_targets": [{{"need": "...", "materiality": "critical|high|medium|low"}}],
 "reopens": [{{"target_id": "...", "reason": "..."}}],
 "reprioritize": [{{"target_id": "...", "materiality": "..."}}],
 "answer_shape": "<updated, 3-6 sentences>"}}
Only ops that genuinely improve the state. Empty lists are valid."""

    parsed = call_json(smart_caller, board, prompt, kind="reframe",
                       max_tokens=16384)
    if not isinstance(parsed, dict):
        board.log("reframe", "parse failure — ledger unchanged")
        return

    ob_ops = opens = reopens = unit_ops = 0
    contract_on = bool(board.metadata.get("contract_enabled"))
    for ao in parsed.get("add_obligations", []) if contract_on else []:
        if not isinstance(ao, dict):
            continue
        text = str(ao.get("text", "")).strip()
        if not text:
            continue
        coverage = str(ao.get("coverage", "material"))
        if coverage not in ("exhaustive", "material", "representative",
                            "native-complete", "summary"):
            coverage = "material"
        board.add_obligation(Obligation(
            text=text, origin=f"reframe_iter{board.iteration}",
            coverage=coverage, mandatory=bool(ao.get("mandatory", True)),
        ))
        ob_ops += 1
    for ac in parsed.get("adjust_coverage", []):
        if not isinstance(ac, dict):
            continue
        ob = board.find_obligation(str(ac.get("obligation_id", "")))
        coverage = str(ac.get("coverage", ""))
        if ob is None or coverage not in (
            "exhaustive", "material", "representative", "native-complete", "summary",
        ):
            continue
        board.log(
            "contract_change",
            f"{ob.id} coverage {ob.coverage} -> {coverage}: "
            f"{str(ac.get('reason', ''))[:150]}",
        )
        ob.coverage = coverage
        ob_ops += 1
    for wo in parsed.get("waive_obligations", []):
        if not isinstance(wo, dict):
            continue
        ob = board.find_obligation(str(wo.get("obligation_id", "")))
        if ob is None:
            continue
        ob.status = "waived"
        ob.reason = str(wo.get("reason", ""))[:300]
        ob_ops += 1
    for au in parsed.get("add_units", []):
        if not isinstance(au, dict):
            continue
        ob = board.find_obligation(str(au.get("obligation_id", "")))
        name = str(au.get("name", "")).strip()
        if ob is None or not name:
            continue
        board.add_unit(Unit(
            name=name, obligation_ref=ob.id,
            anchor=str(au.get("anchor", ""))[:120],
        ))
        unit_ops += 1
    for wu in parsed.get("waive_units", []):
        if not isinstance(wu, dict):
            continue
        unit = board.find_unit(str(wu.get("unit_id", "")))
        if unit is None:
            continue
        unit.status = "waived"
        unit.reason = str(wu.get("reason", ""))[:200]
        unit_ops += 1
    for nt in parsed.get("new_targets", []):
        if not isinstance(nt, dict):
            continue
        need = str(nt.get("need", "")).strip()
        if not need:
            continue
        m = str(nt.get("materiality", "medium"))
        if m not in ("critical", "high", "medium", "low"):
            m = "medium"
        board.add_target(Target(
            need=need, materiality=m,
            created_iteration=board.iteration, proposed_by="reframe",
        ))
        opens += 1
    for ro in parsed.get("reopens", []):
        if not isinstance(ro, dict):
            continue
        t = board.find_target(str(ro.get("target_id", "")))
        if t is not None and t.status == "closed":
            board.resolve_target(t.id, "open", "")
            board.log("reopen", f"{t.id}: {str(ro.get('reason', ''))[:150]}")
            reopens += 1
    for rp in parsed.get("reprioritize", []):
        if not isinstance(rp, dict):
            continue
        t = board.find_target(str(rp.get("target_id", "")))
        m = str(rp.get("materiality", ""))
        if t is not None and m in ("critical", "high", "medium", "low"):
            t.materiality = m
    new_shape = str(parsed.get("answer_shape", "")).strip()
    if new_shape:
        board.metadata["answer_shape"] = new_shape[:2000]

    board.log(
        "reframe",
        f"contract repair: {ob_ops} obligation ops, {unit_ops} unit ops, "
        f"{opens} new targets, {reopens} reopened",
        detail={"obligation_ops": ob_ops, "unit_ops": unit_ops,
                "new": opens, "reopens": reopens},
    )
