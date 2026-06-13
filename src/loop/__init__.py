"""The target-closing loop — one loop over one state model.

    seed → triage → [controller → execute → maintain]* → plan → synthesize

Every iteration the controller sees compact state and decides; executors
do bounded work in parallel; convergence is explicit closure of material
targets with a recorded stop reason — never a silent timer expiry.
"""
from __future__ import annotations

import os

from .actions import execute_actions
from .control import (
    controller_decide, maintain_ledger, reframe_ledger, seed_targets,
    should_maintain,
)
from .state import Board, Source
from .synthesis import plan_synthesis, synthesize, write_final_state
from .triage import triage_sources

MAX_ITERATIONS = int(os.getenv("LOOP_MAX_ITERATIONS", "12"))
# Obligations and units are always created (shadow mode) — they provide
# coverage context for controller and synthesis.  The convergence GATE
# (blocking convergence on unsatisfied mandatory obligations) is separate
# and off by default: v5/v6 showed macro regressions when it was on.
CONTRACT_GATE = os.getenv("LOOP_CONTRACT_GATE", "0").strip() in ("1", "true", "yes")
BUDGET_STOP_PCT = float(os.getenv("LOOP_BUDGET_STOP_PCT", "85"))
DIMINISHING_ROUNDS = 2
# Iterations at which the blackboard is rebuilt (reframe pass): the ledger is
# re-derived from accumulated understanding — splits, new questions, reopens.
REFRAME_ITERATIONS = tuple(
    int(x) for x in os.getenv("LOOP_REFRAME_ITERATIONS", "3,7").split(",") if x.strip()
)


def run_loop(task, worker_caller, smart_caller=None):
    """Run the loop on a task. Returns (deliverable, board).

    worker_caller: cheap tier — read/search/bind/analyze/verify executors.
    smart_caller: judgment tier — seed/triage/controller/maintenance/synthesis.
    """
    smart = smart_caller or worker_caller
    board = Board(
        instruction=task.instruction,
        metadata=dict(task.metadata or {}),
        output_dir=task.output_dir,
        token_budget=int(os.getenv("LOOP_TOKEN_BUDGET", "3000000")),
    )
    board.metadata["contract_gate"] = CONTRACT_GATE
    for doc in task.documents:
        board.add_source(Source(
            id=doc.id, name=doc.name,
            path=str(doc.metadata.get("path", "")),
            size_bytes=doc.size_bytes,
            _doc=doc,
        ))

    # Think before reading; triage before thinking about everything.
    seed_targets(smart, board)
    triage_sources(smart, board)
    board.snapshot("seed")

    last_summary: dict = {}
    quiet_rounds = 0
    open_history: list[int] = []
    closeout = False

    while True:
        board.iteration += 1

        decision = controller_decide(
            smart, board, last_summary,
            max_iterations=MAX_ITERATIONS, closeout=closeout,
        )

        # --- convergence policy ---
        material_open = board.material_open_targets()
        open_mandatory = board.open_mandatory_obligations() if CONTRACT_GATE else []
        open_history.append(len(material_open) + len(open_mandatory))
        closeout = (
            board.iteration >= MAX_ITERATIONS // 2
            and len(open_history) >= 2
            and open_history[-1] >= open_history[-2] > 0
        )
        if closeout:
            board.log(
                "closeout",
                f"{len(material_open)} material targets + "
                f"{len(open_mandatory)} mandatory obligations not shrinking",
            )
        if decision["converge"]:
            if not material_open and not open_mandatory:
                board.stop_reason = f"converged: {decision['converge_reason']}"
                break
            board.log(
                "converge_denied",
                f"{len(material_open)} material targets, "
                f"{len(open_mandatory)} mandatory obligations still open",
                detail={"targets": [t.id for t in material_open],
                        "obligations": [o.id for o in open_mandatory]},
            )
        if board.iteration >= MAX_ITERATIONS:
            board.stop_reason = (
                f"max_iterations ({MAX_ITERATIONS}); "
                f"{len(material_open)} material targets open"
            )
            break
        if board.budget_used_pct() >= BUDGET_STOP_PCT:
            board.stop_reason = (
                f"budget ({board.budget_used_pct()}%); "
                f"{len(material_open)} material targets open"
            )
            break

        if not decision["actions"]:
            quiet_rounds += 1
        else:
            derived_before = sum(1 for c in board.claims if c.is_derived)
            resolved_before = len(board.resolved_targets())
            last_summary = execute_actions(decision["actions"], board, worker_caller)
            derived_added = sum(1 for c in board.claims if c.is_derived) - derived_before
            resolved_delta = len(board.resolved_targets()) - resolved_before
            last_summary["derived_added"] = derived_added
            if derived_added == 0 and resolved_delta == 0 and last_summary.get("claims", 0) == 0:
                quiet_rounds += 1
            else:
                quiet_rounds = 0

        if quiet_rounds >= DIMINISHING_ROUNDS:
            board.stop_reason = (
                f"diminishing_returns ({quiet_rounds} quiet rounds); "
                f"{len(board.material_open_targets())} material targets open"
            )
            break

        if board.iteration in REFRAME_ITERATIONS:
            reframe_ledger(smart, board)
            # Splits/reopens legitimately grow the open count — give the
            # rebuilt ledger a fresh stagnation baseline.
            open_history.clear()
            closeout = False
        elif should_maintain(board) or closeout:
            maintain_ledger(smart, board, closeout=closeout)

        board.snapshot()

    board.log("stop", board.stop_reason)
    board.snapshot("final")

    plan = plan_synthesis(smart, board)
    deliverable = synthesize(smart, board, plan)
    write_final_state(board)
    return deliverable, board
