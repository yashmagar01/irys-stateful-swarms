"""Source triage — decide what to read from metadata alone.

For a 1,000-document corpus, not every document is relevant to the
question. Before reading anything, score each source against the targets
using ONLY metadata: file name, directory path, size, type, dates in
names. No text is materialized here — that is the whole point.

The result is a live reading plan, not a contract: sources marked
"unlikely" can be pulled in later if evidence demands it (the controller
sees the full catalog every iteration).
"""
from __future__ import annotations

from .llm import call_json
from .state import Board

_BATCH = 150  # catalog lines per triage call


def triage_sources(smart_caller, board: Board) -> None:
    """Score every source's relevance to the open targets. Metadata only."""
    docs = [s for s in board.sources if s.kind == "document"]
    if not docs:
        return
    # Small corpora: everything is worth reading; skip the LLM call.
    if len(docs) <= 8:
        for s in docs:
            s.relevance = "definite"
            s.relevance_reason = "small corpus — read everything"
        board.log("triage", f"{len(docs)} sources, small corpus: all definite")
        return

    targets_text = "\n".join(
        f"- [{t.materiality}] {t.need}" for t in board.open_targets()[:20]
    )

    scored = 0
    for i in range(0, len(docs), _BATCH):
        batch = docs[i:i + _BATCH]
        catalog = "\n".join(
            f"{s.id} | {s.path_hint}/{s.name} | {s.size_bytes // 1024}KB"
            for s in batch
        )
        prompt = f"""You are triaging a document corpus for a research task. You see ONLY metadata: file paths, names, and sizes. Directory names, file names, dates and form types embedded in names carry strong signal (e.g. "sec/10-K/2025" vs "ir/news-releases/2019").

TASK:
{board.instruction[:2000]}

WHAT WE NEED TO ANSWER:
{targets_text}

SOURCES (id | path/name | size):
{catalog}

For each source, judge how likely it is to contain evidence relevant to the task. Be decisive — the cost of marking everything "maybe" is reading everything.

Return JSON:
{{"sources": [{{"id": "...", "relevance": "definite|maybe|unlikely", "reason": "<10 words>"}}]}}
Include EVERY source id listed."""

        parsed = call_json(
            smart_caller, board, prompt, kind="triage", max_tokens=16384,
        )
        if not isinstance(parsed, dict):
            continue
        for item in parsed.get("sources", []):
            if not isinstance(item, dict):
                continue
            src = board.find_source(str(item.get("id", "")))
            if src is None:
                continue
            rel = str(item.get("relevance", "")).lower()
            if rel in ("definite", "maybe", "unlikely"):
                src.relevance = rel
                src.relevance_reason = str(item.get("reason", ""))[:120]
                scored += 1

    # Anything the model skipped stays readable, just deprioritized.
    for s in docs:
        if s.relevance == "unknown":
            s.relevance = "maybe"
            s.relevance_reason = "not scored — default maybe"

    counts: dict[str, int] = {}
    for s in docs:
        counts[s.relevance] = counts.get(s.relevance, 0) + 1
    board.log("triage", f"scored {scored}/{len(docs)} sources: {counts}",
              detail=counts)


def catalog_summary(board: Board, limit: int = 60) -> str:
    """Compact source catalog for the controller prompt — read state + relevance."""
    docs = sorted(
        board.sources,
        key=lambda s: (
            {"definite": 0, "maybe": 1, "unknown": 2, "unlikely": 3}.get(s.relevance, 2),
            s.read_status == "read",
        ),
    )
    lines = []
    for s in docs[:limit]:
        lines.append(
            f"{s.id} [{s.read_status}/{s.relevance}] {s.path_hint}/{s.name}"
            f" ({s.size_bytes // 1024}KB)"
        )
    if len(docs) > limit:
        unread = sum(1 for s in docs[limit:] if s.read_status == "unread")
        lines.append(f"... and {len(docs) - limit} more ({unread} unread)")
    return "\n".join(lines)
