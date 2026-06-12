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
from .state import Board, Target
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

Produce the target ledger: 5-14 targets. Each target is a question or obligation the answer must close. Good targets are semantic ("reconcile the share counts across documents", "determine total 10-year cost including escalations"), never formatting ("include a table").

Also state what would make the answer excellent (answer_shape) — depth, rigor, what a demanding expert reader would check first.

Return JSON:
{{"targets": [{{"need": "...", "materiality": "critical|high|medium|low"}}],
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
- read {{source_id, focus, target_ids}} — extract evidence from a source (use focus to direct attention)
- search {{query, target_ids}} — web search for external knowledge (current law, standards, public facts not in sources)
- bind {{}} — connect unbound claims to targets (dispatch when unbound count is high)
- analyze {{target_id, instruction}} — promote a target's evidence into conclusions/calculations/issues/recommendations
- verify {{claim_ids}} — adversarially check material derived claims against sources

DECISION RULES:
- Evidence sitting unanalyzed beats reading more. If a target has raw claims and no derived claims, analyze it before dispatching new reads.
- High unbound count → bind before anything else can be judged accurately.
- Close a target ONLY when its derived claims genuinely answer the need. Waive with a reason if not worth pursuing. Block with a reason if it cannot be answered from available sources/budget.
- Read unread "definite" sources early; pull "unlikely" sources in only if evidence demands it.
- Converge when every critical/high target is closed/waived/blocked and another round would not materially improve the answer.

Return JSON:
{{"reasoning": "<2-4 sentences>",
 "target_updates": [{{"target_id": "...", "status": "closed|waived|blocked", "reason": "..."}}],
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
