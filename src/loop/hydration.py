"""Hydration — resolve source_span pointers to source text excerpts.

Shared by analyze (actions.py) and synthesis (synthesis.py). The blackboard
is an INDEX; these helpers look up what the pointers reference.
"""
from __future__ import annotations

from .state import Board, Claim


def source_claims_for_hydration(
    board: Board,
    claim: Claim,
) -> list[tuple[Claim, str]]:
    """Walk support_refs recursively to find source-backed leaf claims."""
    leaves: list[tuple[Claim, str]] = []
    emitted: set[str] = set()
    visiting: set[str] = set()

    def visit(c: Claim) -> None:
        if c.id in visiting or c.id in emitted:
            return
        if c.source_doc and c.source_span is not None:
            leaves.append((c, claim.id))
            emitted.add(c.id)
            return
        visiting.add(c.id)
        for ref in c.support_refs:
            sup = board.find_claim(str(ref))
            if sup is not None and sup.active:
                visit(sup)
        visiting.remove(c.id)
        emitted.add(c.id)

    visit(claim)
    return leaves


def build_evidence_context(
    board: Board,
    claims: list[Claim],
    *,
    max_chars: int = 0,
    expansion: int = 500,
) -> tuple[str, dict]:
    """Resolve source spans to text excerpts, merge overlaps.

    Args:
        board: The investigation board with sources.
        claims: Claims whose source spans to resolve.
        max_chars: Cap on total source text chars. 0 = no cap.
        expansion: Chars of surrounding context on each side of span.

    Returns:
        (formatted_text, stats_dict)
    """
    candidate_windows: list[dict] = []
    hydrated_claim_ids: set[str] = set()
    missing_span = 0
    missing_source = 0
    invalid_span = 0

    for bound_claim in claims:
        source_claims = source_claims_for_hydration(board, bound_claim)
        if not source_claims:
            if not bound_claim.source_span:
                missing_span += 1
            continue

        for source_claim, via_claim_id in source_claims:
            if not source_claim.source_doc or source_claim.source_span is None:
                missing_span += 1
                continue
            src = next((s for s in board.sources if s.name == source_claim.source_doc), None)
            if src is None:
                missing_source += 1
                continue
            text = src.text()
            start, end = source_claim.source_span
            if start < 0 or end <= start or start >= len(text):
                invalid_span += 1
                continue
            start = max(0, start - expansion)
            end = min(len(text), end + expansion)
            candidate_windows.append({
                "source": src.name,
                "text": text,
                "start": start,
                "end": end,
                "source_claim_ids": [source_claim.id],
                "via_claim_ids": [via_claim_id],
            })
            hydrated_claim_ids.add(source_claim.id)

    candidate_windows.sort(key=lambda w: (w["source"], w["start"], w["end"]))

    merged: list[dict] = []
    for w in candidate_windows:
        if (
            merged
            and merged[-1]["source"] == w["source"]
            and w["start"] <= merged[-1]["end"]
        ):
            merged[-1]["end"] = max(merged[-1]["end"], w["end"])
            merged[-1]["source_claim_ids"].extend(w["source_claim_ids"])
            merged[-1]["via_claim_ids"].extend(w["via_claim_ids"])
        else:
            merged.append(dict(w))

    for w in merged:
        w["source_claim_ids"] = sorted(set(w["source_claim_ids"]))
        w["via_claim_ids"] = sorted(set(w["via_claim_ids"]))

    blocks: list[str] = []
    total_chars = 0
    dropped_windows = 0
    for idx, w in enumerate(merged, start=1):
        excerpt = w["text"][w["start"]:w["end"]]
        if max_chars > 0 and total_chars + len(excerpt) > max_chars:
            dropped_windows += len(merged) - idx + 1
            break
        total_chars += len(excerpt)
        blocks.append(
            f"SOURCE EXCERPT E{idx}\n"
            f"source: {w['source']}\n"
            f"span: {w['start']}-{w['end']}\n"
            f"source_claims: {', '.join(w['source_claim_ids'])}\n"
            f"included_for_bound_claims: {', '.join(w['via_claim_ids'])}\n"
            f"---\n"
            f"{excerpt}\n"
            f"---"
        )

    stats = {
        "bound_claims": len(claims),
        "source_windows": len(candidate_windows),
        "merged_windows": len(merged),
        "included_windows": len(blocks),
        "dropped_windows": dropped_windows,
        "hydrated_claim_ids": sorted(hydrated_claim_ids),
        "missing_span": missing_span,
        "missing_source": missing_source,
        "invalid_span": invalid_span,
        "chars": total_chars,
    }
    return "\n\n".join(blocks), stats
