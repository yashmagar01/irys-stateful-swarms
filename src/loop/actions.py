"""Action executors — read, search, bind, analyze, verify.

Each executor is a bounded job for a cheap model: it receives exactly the
state slice it needs and writes claims back to the board. Workers may
propose new targets freely (discovery must never ask permission); the
ledger maintenance pass grooms proposals later.

Bind is an LLM call by design: mapping claims to targets is semantic
work, and rules would smuggle domain assumptions into the architecture.
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

from .llm import call_json
from .state import CLAIM_KINDS, Board, Claim, Source, Target

_CHUNK_CHARS = 80_000
_MAX_PARALLEL = 8
_BIND_BATCH = 60


def execute_actions(actions: list[dict], board: Board, worker_caller) -> dict:
    """Run an iteration's actions in parallel. Returns summary counts."""
    jobs = []
    for action in actions:
        kind = action.get("kind", "")
        if kind == "read":
            jobs.extend(_read_jobs(action, board))
        elif kind == "search":
            jobs.append(("search", action))
        elif kind == "bind":
            jobs.extend(_bind_jobs(action, board))
        elif kind == "analyze":
            jobs.append(("analyze", action))
        elif kind == "verify":
            jobs.append(("verify", action))

    summary = {"claims": 0, "targets_proposed": 0, "bound": 0, "verified": 0,
               "jobs": len(jobs), "failed": 0}
    if not jobs:
        return summary

    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL) as pool:
        futures = {}
        for kind, payload in jobs:
            fn = {
                "read_chunk": _run_read_chunk,
                "search": _run_search,
                "bind_batch": _run_bind_batch,
                "analyze": _run_analyze,
                "verify": _run_verify,
            }[kind]
            futures[pool.submit(fn, payload, board, worker_caller)] = kind
        for fut in as_completed(futures):
            try:
                result = fut.result()
            except Exception as e:
                summary["failed"] += 1
                board.log("action_error", f"{futures[fut]} failed: {e}")
                continue
            for k, v in (result or {}).items():
                summary[k] = summary.get(k, 0) + v
    return summary


# --- READ ---

def _read_jobs(action: dict, board: Board) -> list[tuple[str, dict]]:
    source = board.find_source(str(action.get("source_id", "")))
    if source is None:
        return []
    text = source.text()
    if not text:
        return []
    source.read_status = "read"
    focus = str(action.get("focus", ""))
    target_ids = [str(t) for t in action.get("target_ids", [])]
    jobs = []
    for i in range(0, len(text), _CHUNK_CHARS):
        jobs.append(("read_chunk", {
            "source": source,
            "chunk": text[i:i + _CHUNK_CHARS],
            "chunk_no": i // _CHUNK_CHARS + 1,
            "chunks_total": (len(text) - 1) // _CHUNK_CHARS + 1,
            "focus": focus,
            "target_ids": target_ids,
        }))
    return jobs


def _run_read_chunk(job: dict, board: Board, caller) -> dict:
    source: Source = job["source"]
    targets_text = _targets_brief(board, job["target_ids"])
    chunk_note = (
        f" (part {job['chunk_no']}/{job['chunks_total']})"
        if job["chunks_total"] > 1 else ""
    )
    focus_note = f"\nFOCUS: {job['focus']}" if job["focus"] else ""

    prompt = f"""You are extracting evidence from a document for a research task. Extract every specific, citable fact: amounts, dates, parties, defined terms, obligations, conditions, numbers, named provisions. Exact values, never paraphrased approximations.

TASK CONTEXT:
{board.instruction[:1500]}

QUESTIONS THIS READ SERVES:
{targets_text}{focus_note}

DOCUMENT: {source.name}{chunk_note}
---
{job['chunk']}
---

Return JSON:
{{"claims": [{{"kind": "observation", "content": "<the fact, specific and self-contained>", "section": "<section/heading it came from>", "evidence": "<short exact quote>", "confidence": 0.0-1.0}}],
 "proposed_targets": [{{"need": "<new question this document raises that the task must answer>", "materiality": "critical|high|medium|low"}}]}}

Rules:
- kind is usually "observation". Use "contradiction" if this text conflicts with itself, "gap" if something expected is conspicuously absent, "issue" for a clear defect/risk stated in the text.
- Be exhaustive on facts relevant to the questions; include other clearly material facts too.
- proposed_targets only for genuinely new material questions, not restatements."""

    parsed = call_json(caller, board, prompt, kind="read", max_tokens=16384)
    return _ingest_claims(parsed, board, source=source, created_by="read")


# --- SEARCH ---

def _run_search(action: dict, board: Board, caller) -> dict:
    from ..swarm.web_search import search_and_browse
    query = str(action.get("query", "")).strip()
    if not query:
        return {}
    results_text = search_and_browse(query)
    if not results_text:
        board.log("search", f"no results for: {query}")
        return {}

    src = Source(
        id=f"web_{hashlib.md5(query.encode()).hexdigest()[:8]}",
        name=f"web: {query[:60]}", kind="web",
        read_status="read", relevance="definite",
        relevance_reason="fetched for query",
        web_text=results_text[:60_000],
    )
    board.add_source(src)

    targets_text = _targets_brief(board, [str(t) for t in action.get("target_ids", [])])
    prompt = f"""You are extracting facts from web search results to answer specific questions. Only extract claims the results actually support — attribute each to its page.

QUESTIONS:
{targets_text}

SEARCH RESULTS:
---
{src.web_text}
---

Return JSON:
{{"claims": [{{"kind": "observation", "content": "<fact with attribution>", "section": "<page title or url>", "evidence": "<short quote>", "confidence": 0.0-1.0}}]}}

External claims need lower default confidence than primary documents unless from an authoritative source."""

    parsed = call_json(caller, board, prompt, kind="search", max_tokens=8192)
    out = _ingest_claims(parsed, board, source=src, created_by="search")
    # Search results serve specific targets — bind directly.
    tids = [str(t) for t in action.get("target_ids", [])]
    if tids:
        for c in board.claims:
            if c.created_by == "search" and c.source_doc == src.name and not c.target_refs:
                board.bind_claim(c.id, tids)
                out["bound"] = out.get("bound", 0) + 1
    return out


# --- BIND ---

def _bind_jobs(action: dict, board: Board) -> list[tuple[str, dict]]:
    unbound = board.unbound_claims()
    if not unbound:
        return []
    jobs = []
    for i in range(0, len(unbound), _BIND_BATCH):
        jobs.append(("bind_batch", {"claims": unbound[i:i + _BIND_BATCH]}))
    return jobs


def _run_bind_batch(job: dict, board: Board, caller) -> dict:
    claims = job["claims"]
    targets = board.open_targets()
    if not targets:
        return {}
    targets_text = "\n".join(f"{t.id} [{t.materiality}] {t.need}" for t in targets)
    claims_text = "\n".join(
        f"{c.id} [{c.kind}] {c.content[:220]}" for c in claims
    )

    prompt = f"""You are connecting extracted evidence to the questions it helps answer. A claim can serve multiple questions. A claim that serves no current question gets an empty list — do NOT force-fit.

QUESTIONS (id, materiality, need):
{targets_text}

CLAIMS (id, kind, content):
{claims_text}

Return JSON:
{{"bindings": [{{"claim_id": "...", "target_ids": ["..."]}}]}}
Include every claim id. Bind on substance, not keyword overlap."""

    parsed = call_json(caller, board, prompt, kind="bind", max_tokens=16384)
    if not isinstance(parsed, dict):
        return {}
    bound = 0
    for b in parsed.get("bindings", []):
        if not isinstance(b, dict):
            continue
        tids = [str(t) for t in b.get("target_ids", []) if t]
        if tids and board.bind_claim(str(b.get("claim_id", "")), tids):
            bound += 1
    return {"bound": bound}


# --- ANALYZE ---

def _run_analyze(action: dict, board: Board, caller) -> dict:
    target = board.find_target(str(action.get("target_id", "")))
    if target is None:
        return {}
    bound = board.claims_for_target(target)
    if not bound:
        return {}
    instruction = str(action.get("instruction", ""))
    claims_text = "\n".join(
        f"{c.id} [{c.kind}, conf {c.confidence:.2f}] {c.content}"
        + (f" | evidence: {c.evidence[:150]}" if c.evidence else "")
        + (f" | source: {c.source_doc}" if c.source_doc else "")
        for c in bound[:80]
    )

    prompt = f"""You are a top-tier expert doing the analytical work to close a specific question. Raw facts are inputs; your job is conclusions: calculations, comparisons, issue flags, recommendations, decisions. Show reasoning inside the claim content.

OVERALL TASK:
{board.instruction[:1500]}

QUESTION TO CLOSE:
[{target.materiality}] {target.need}
{f'SPECIFIC INSTRUCTION: {instruction}' if instruction else ''}

EVIDENCE BOUND TO THIS QUESTION:
{claims_text}

Return JSON:
{{"claims": [{{"kind": "analysis|calculation|comparison|issue|recommendation|decision|gap|uncertainty|contradiction", "content": "<the conclusion, with reasoning and concrete numbers where applicable>", "support_refs": ["<ids of evidence claims used>"], "confidence": 0.0-1.0}}],
 "proposed_targets": [{{"need": "...", "materiality": "critical|high|medium|low"}}],
 "recommend_close": true/false,
 "close_reason": "<if recommend_close: why this question is now answerable>"}}

Rules:
- Every derived claim MUST cite support_refs from the evidence above.
- Calculations show the arithmetic. Comparisons name both sides. Issues state impact.
- If evidence is insufficient, emit a "gap" claim saying exactly what is missing.
- recommend_close only if the question is genuinely answerable from the derived claims."""

    parsed = call_json(caller, board, prompt, kind="analyze", max_tokens=16384)
    out = _ingest_claims(
        parsed, board, source=None, created_by="analyze",
        bind_to=[target.id], valid_support={c.id for c in bound},
    )
    if isinstance(parsed, dict) and parsed.get("recommend_close"):
        board.log(
            "close_recommendation",
            f"{target.id}: {str(parsed.get('close_reason', ''))[:200]}",
            detail={"target_id": target.id},
        )
    return out


# --- VERIFY ---

def _run_verify(action: dict, board: Board, caller) -> dict:
    claim_ids = [str(c) for c in action.get("claim_ids", [])][:10]
    claims = [c for c in (board.find_claim(cid) for cid in claim_ids) if c]
    if not claims:
        return {}

    blocks = []
    for c in claims:
        evidence_context = ""
        src = next(
            (s for s in board.sources if s.name == c.source_doc), None,
        )
        if src is None and c.support_refs:
            sup = board.find_claim(c.support_refs[0])
            if sup is not None:
                src = next(
                    (s for s in board.sources if s.name == sup.source_doc), None,
                )
        if src is not None and src.kind == "document":
            from ..swarm.section_index import resolve_section_text
            section = c.source_section or ""
            evidence_context = resolve_section_text(
                src.text(), src.section_index(), section, max_chars=12_000,
            )
        support_text = "\n".join(
            f"  support {s.id}: {s.content[:200]} | evidence: {s.evidence[:150]}"
            for s in (board.find_claim(r) for r in c.support_refs[:6]) if s
        )
        blocks.append(
            f"CLAIM {c.id} [{c.kind}]: {c.content}\n{support_text}\n"
            f"SOURCE TEXT:\n{evidence_context[:10_000] if evidence_context else '(no source text located)'}"
        )

    prompt = f"""You are adversarially verifying claims against their cited sources. Try to refute each claim. A claim survives only if the source text actually supports it — including any arithmetic.

{chr(10).join(blocks)}

Return JSON:
{{"verdicts": [{{"claim_id": "...", "verified": true/false, "confidence": 0.0-1.0, "note": "<what the source shows>"}}]}}"""

    parsed = call_json(caller, board, prompt, kind="verify", max_tokens=8192)
    if not isinstance(parsed, dict):
        return {}
    verified = 0
    for v in parsed.get("verdicts", []):
        if not isinstance(v, dict):
            continue
        claim = board.find_claim(str(v.get("claim_id", "")))
        if claim is None:
            continue
        claim.verified = bool(v.get("verified"))
        try:
            claim.confidence = max(0.05, min(0.98, float(v.get("confidence", claim.confidence))))
        except (TypeError, ValueError):
            pass
        verified += 1
    return {"verified": verified}


# --- shared ingestion ---

def _ingest_claims(parsed, board: Board, *, source: Source | None,
                   created_by: str, bind_to: list[str] | None = None,
                   valid_support: set[str] | None = None) -> dict:
    if not isinstance(parsed, dict):
        return {"claims": 0}
    added = 0
    seen: set[str] = set()
    for item in parsed.get("claims", []):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        key = content[:120].lower()
        if key in seen:
            continue
        seen.add(key)
        kind = str(item.get("kind", "observation"))
        if kind not in CLAIM_KINDS:
            kind = "observation"
        support = [
            str(r) for r in item.get("support_refs", [])
            if valid_support is None or str(r) in valid_support
        ]
        try:
            conf = max(0.05, min(0.98, float(item.get("confidence", 0.6))))
        except (TypeError, ValueError):
            conf = 0.6
        claim = Claim(
            kind=kind, content=content,
            source_doc=source.name if source else None,
            source_section=str(item.get("section", "")) or None,
            evidence=str(item.get("evidence", ""))[:500],
            support_refs=support,
            target_refs=list(bind_to or []),
            confidence=conf,
            iteration=board.iteration,
            created_by=created_by,
        )
        board.add_claim(claim)
        added += 1

    proposed = 0
    for pt in parsed.get("proposed_targets", []):
        if not isinstance(pt, dict):
            continue
        need = str(pt.get("need", "")).strip()
        if not need:
            continue
        materiality = str(pt.get("materiality", "medium"))
        if materiality not in ("critical", "high", "medium", "low"):
            materiality = "medium"
        board.add_target(Target(
            need=need, materiality=materiality,
            created_iteration=board.iteration, proposed_by=created_by,
        ))
        proposed += 1

    return {"claims": added, "targets_proposed": proposed}


def _targets_brief(board: Board, target_ids: list[str]) -> str:
    targets = [t for t in (board.find_target(tid) for tid in target_ids) if t]
    if not targets:
        targets = board.material_open_targets()[:8]
    return "\n".join(f"- [{t.materiality}] {t.need}" for t in targets) or "- (general extraction)"
