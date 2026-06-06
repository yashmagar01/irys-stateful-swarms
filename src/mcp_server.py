"""MCP server: irys blackboard state primitives for Claude Code / Codex.

Exposes the stateful swarm's blackboard pattern as tools. Claude Code
provides the intelligence; irys provides structured state management,
signal propagation, contradiction detection, and convergence tracking.

No additional API keys required for core tools.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "irys",
    instructions=(
        "Stateful document analysis framework. Use irys_create_blackboard to start, "
        "irys_get_context to get source text and state, irys_add_entries to record "
        "findings with provenance. The blackboard automatically tracks signals, "
        "contradictions, and convergence. No API keys needed for core tools."
    ),
)

_STORE_ROOT = Path(tempfile.gettempdir()) / "irys-mcp"
_blackboards: dict[str, Any] = {}
_locks: dict[str, threading.Lock] = {}


def _get_lock(bb_id: str) -> threading.Lock:
    if bb_id not in _locks:
        _locks[bb_id] = threading.Lock()
    return _locks[bb_id]


def _state_dir(bb_id: str) -> Path:
    d = _STORE_ROOT / bb_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_state(bb_id: str, bb: Any) -> None:
    d = _state_dir(bb_id)
    state = {
        "blackboard_id": bb_id,
        "task_instruction": bb.task_instruction,
        "iteration": bb.iteration,
        "documents": [_doc_status_dict(ds) for ds in bb.documents],
        "entries": [_entry_dict(e) for e in bb.entries],
        "signals": [_signal_dict(s) for s in bb.signals],
        "metadata": getattr(bb, "_mcp_metadata", {}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = d / "state.tmp"
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    tmp.replace(d / "state.json")

    doc_dir = d / "documents"
    for ds in bb.documents:
        if ds.text:
            doc_dir.mkdir(exist_ok=True)
            (doc_dir / f"{ds.id}.txt").write_text(ds.text, encoding="utf-8")


def _load_state(bb_id: str) -> Any:
    if bb_id in _blackboards:
        return _blackboards[bb_id]

    d = _state_dir(bb_id)
    state_file = d / "state.json"
    if not state_file.exists():
        raise FileNotFoundError(f"No blackboard found: {bb_id}")

    from .swarm.blackboard import Blackboard
    from .swarm.models import (
        DocumentStatus, Entry, EntrySource, EpistemicStatus, Signal,
        SectionIndex, WorkerRecord,
    )
    from .swarm.section_index import build_section_index

    data = json.loads(state_file.read_text(encoding="utf-8"))

    docs = []
    for dd in data.get("documents", []):
        doc_text_file = d / "documents" / f"{dd['id']}.txt"
        text = doc_text_file.read_text(encoding="utf-8") if doc_text_file.exists() else ""
        idx = build_section_index(text) if text else SectionIndex()
        docs.append(DocumentStatus(
            id=dd["id"], name=dd["name"], size_bytes=dd.get("size_bytes", 0),
            headings=dd.get("headings", []),
            structural_profile=dd.get("structural_profile"),
            read_status=dd.get("read_status", "unread"),
            sections_read=dd.get("sections_read", []),
            sections_unread=dd.get("sections_unread", []),
            section_index=idx, text=text,
        ))

    entries = []
    for ed in data.get("entries", []):
        src = None
        if ed.get("source"):
            src = EntrySource(
                document=ed["source"].get("document"),
                section=ed["source"].get("section"),
                evidence=ed["source"].get("evidence", ""),
            )
        epist = None
        if ed.get("epistemic"):
            epist = EpistemicStatus(
                classification=ed["epistemic"].get("classification", "inference"),
                source_credibility=ed["epistemic"].get("source_credibility", "unknown"),
                motivation=ed["epistemic"].get("motivation", ""),
            )
        cb = ed.get("created_by", {})
        entries.append(Entry(
            id=ed["id"], type=ed.get("type", "observation"),
            content=ed.get("content", ""),
            source=src, epistemic=epist,
            created_by=WorkerRecord(
                cb.get("worker_id", ""), cb.get("description", ""),
                cb.get("iteration", 0),
            ),
            confidence=ed.get("confidence", 0.5),
            verified=ed.get("verified"),
            tags=ed.get("tags", []),
            status=ed.get("status", "active"),
            opens_questions=ed.get("opens_questions", []),
            supports_entries=ed.get("supports_entries", []),
            contradicts_entries=ed.get("contradicts_entries", []),
            supersedes_entries=ed.get("supersedes_entries", []),
            addresses_signals=ed.get("addresses_signals", []),
        ))

    signals = []
    for sd in data.get("signals", []):
        signals.append(Signal(
            id=sd["id"], type=sd.get("type", "question"),
            content=sd.get("content", ""),
            origin_entry=sd.get("origin_entry", ""),
            priority=sd.get("priority", "medium"),
            status=sd.get("status", "open"),
            addressed_by=sd.get("addressed_by"),
            iteration_created=sd.get("iteration_created", 0),
        ))

    bb = Blackboard(
        task_instruction=data.get("task_instruction", ""),
        documents=docs, entries=[], signals=signals,
        iteration=data.get("iteration", 0),
    )
    for e in entries:
        bb.entries.append(e)
        bb._index_entry(e)
    bb._mcp_metadata = data.get("metadata", {})
    _blackboards[bb_id] = bb
    return bb


def _bb_summary(bb: Any) -> dict:
    active = [e for e in bb.entries if e.status == "active"]
    type_counts: dict[str, int] = {}
    for e in active:
        type_counts[e.type] = type_counts.get(e.type, 0) + 1
    open_sigs = [s for s in bb.signals if s.status == "open"]
    return {
        "iteration": bb.iteration,
        "total_entries": len(bb.entries),
        "active_entries": len(active),
        "entry_types": type_counts,
        "open_signals": len(open_sigs),
        "critical_signals": len([s for s in open_sigs if s.priority == "critical"]),
        "documents": len(bb.documents),
        "documents_unread": len([d for d in bb.documents if d.read_status == "unread"]),
    }


def _entry_dict(e: Any) -> dict:
    d: dict[str, Any] = {
        "id": e.id, "type": e.type, "content": e.content,
        "confidence": e.confidence, "status": e.status,
        "tags": e.tags,
    }
    if e.source:
        d["source"] = {
            "document": e.source.document,
            "section": e.source.section,
            "evidence": e.source.evidence,
        }
    if e.epistemic:
        d["epistemic"] = {
            "classification": e.epistemic.classification,
            "source_credibility": e.epistemic.source_credibility,
            "motivation": e.epistemic.motivation,
        }
    d["created_by"] = {
        "worker_id": e.created_by.worker_id,
        "description": e.created_by.description,
        "iteration": e.created_by.iteration,
    }
    if e.verified is not None:
        d["verified"] = e.verified
    if e.opens_questions:
        d["opens_questions"] = e.opens_questions
    if e.supports_entries:
        d["supports_entries"] = e.supports_entries
    if e.contradicts_entries:
        d["contradicts_entries"] = e.contradicts_entries
    if e.supersedes_entries:
        d["supersedes_entries"] = e.supersedes_entries
    if e.addresses_signals:
        d["addresses_signals"] = e.addresses_signals
    return d


def _signal_dict(s: Any) -> dict:
    return {
        "id": s.id, "type": s.type, "content": s.content,
        "origin_entry": s.origin_entry, "priority": s.priority,
        "status": s.status, "addressed_by": s.addressed_by,
        "iteration_created": s.iteration_created,
    }


def _doc_status_dict(ds: Any) -> dict:
    return {
        "id": ds.id, "name": ds.name, "size_bytes": ds.size_bytes,
        "headings": ds.headings, "read_status": ds.read_status,
        "sections_read": ds.sections_read,
        "sections_unread": ds.sections_unread,
    }


# ── Phase 1: Core Tools ──────────────────────────────────────────────


@mcp.tool()
def irys_create_blackboard(
    task_instruction: str,
    docs_path: str | None = None,
    metadata: str | None = None,
) -> str:
    """Create a new blackboard for document analysis.

    Args:
        task_instruction: What you want to analyze or answer about the documents.
        docs_path: Path to file or directory of documents to ingest.
        metadata: Optional JSON string of metadata to attach.
    """
    from .swarm.blackboard import Blackboard
    from .swarm.models import DocumentStatus
    from .swarm.section_index import build_section_index

    bb_id = str(uuid.uuid4())[:8]
    docs: list[DocumentStatus] = []

    if docs_path:
        from .ingestion import ingest_file, ingest_directory, SUPPORTED_EXTENSIONS
        path = Path(docs_path).resolve()
        if not path.exists():
            return json.dumps({"error": f"Path does not exist: {docs_path}"})
        try:
            if path.is_file():
                if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    return json.dumps({"error": f"Unsupported file type: {path.suffix}"})
                raw_docs = [ingest_file(path)]
            else:
                raw_docs = ingest_directory(path)
        except Exception as e:
            return json.dumps({"error": f"Ingestion error: {e}"})

        if not raw_docs:
            return json.dumps({"error": f"No supported documents found in {docs_path}"})

        for doc in raw_docs:
            idx = build_section_index(doc.text)
            docs.append(DocumentStatus(
                id=doc.id, name=doc.name, size_bytes=doc.size_bytes,
                headings=[s.name for s in idx.sections],
                sections_unread=[s.name for s in idx.sections],
                section_index=idx, text=doc.text,
            ))

    bb = Blackboard(
        task_instruction=task_instruction,
        documents=docs,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    meta: dict[str, Any] = {}
    if metadata:
        try:
            meta = json.loads(metadata)
        except json.JSONDecodeError:
            pass
    bb._mcp_metadata = meta  # type: ignore[attr-defined]
    _blackboards[bb_id] = bb
    _save_state(bb_id, bb)

    return json.dumps({
        "blackboard_id": bb_id,
        "task_instruction": task_instruction,
        "documents": [_doc_status_dict(d) for d in docs],
        "summary": _bb_summary(bb),
    })


@mcp.tool()
def irys_get_context(
    blackboard_id: str,
    doc_ids: str | None = None,
    signal_ids: str | None = None,
    max_chars: int = 24000,
) -> str:
    """Get context for analysis: source text, current state, and open signals.

    Call this before doing your own analysis. Returns document text,
    current entries, and open signals so you know what to work on.

    Args:
        blackboard_id: The blackboard to get context from.
        doc_ids: Comma-separated doc IDs to focus on (default: all unread).
        signal_ids: Comma-separated signal IDs to focus on.
        max_chars: Max characters of document text to return (default: 24000).
    """
    with _get_lock(blackboard_id):
        try:
            bb = _load_state(blackboard_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Blackboard not found: {blackboard_id}"})

    target_doc_ids = doc_ids.split(",") if doc_ids else None
    target_signal_ids = signal_ids.split(",") if signal_ids else None

    if target_doc_ids:
        target_docs = [d for d in bb.documents if d.id in target_doc_ids]
    else:
        unread = [d for d in bb.documents if d.read_status != "fully_read"]
        target_docs = unread if unread else bb.documents[:3]

    doc_sections = []
    chars_used = 0
    for ds in target_docs:
        if chars_used >= max_chars:
            break
        remaining = max_chars - chars_used
        text = ds.text[:remaining] if ds.text else ""
        doc_sections.append({
            "doc_id": ds.id,
            "name": ds.name,
            "text": text,
            "truncated": len(ds.text) > remaining if ds.text else False,
            "headings": ds.headings,
            "read_status": ds.read_status,
        })
        chars_used += len(text)

    open_sigs = [s for s in bb.signals if s.status == "open"]
    if target_signal_ids:
        open_sigs = [s for s in open_sigs if s.id in target_signal_ids]

    active = [e for e in bb.entries if e.status == "active"]
    relevant = active[-50:]

    return json.dumps({
        "blackboard_id": blackboard_id,
        "task_instruction": bb.task_instruction,
        "iteration": bb.iteration,
        "summary": _bb_summary(bb),
        "open_signals": [_signal_dict(s) for s in open_sigs],
        "recent_entries": [_entry_dict(e) for e in relevant],
        "document_sections": doc_sections,
        "write_contract": {
            "entry_types": ["observation", "analysis", "calculation", "strategy", "gap"],
            "signal_types": ["question", "convergence_gap", "contradiction_resolution", "source_gap"],
            "signal_priorities": ["low", "medium", "high", "critical"],
        },
    })


@mcp.tool()
def irys_add_entries(
    blackboard_id: str,
    entries: str,
    worker_id: str = "claude_code",
    worker_description: str = "",
) -> str:
    """Add structured findings to the blackboard.

    The blackboard automatically: creates signals from opens_questions,
    propagates confidence from supports/contradicts, marks contradictions
    as disputed, and tracks signal resolution.

    Args:
        blackboard_id: The blackboard to add entries to.
        entries: JSON array of entry objects. Each entry has:
            type: "observation"|"analysis"|"calculation"|"strategy"|"gap"
            content: The finding text.
            source: {document, section, evidence} or null.
            confidence: 0.0-1.0 (default 0.7).
            tags: string array.
            opens_questions: questions this finding raises (become signals).
            supports_entries: entry IDs this supports.
            contradicts_entries: entry IDs this contradicts.
            addresses_signals: signal IDs this resolves.
        worker_id: Identifier for who created these entries.
        worker_description: Description of the analysis pass.
    """
    with _get_lock(blackboard_id):
        try:
            bb = _load_state(blackboard_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Blackboard not found: {blackboard_id}"})

        from .swarm.models import Entry, EntrySource, EpistemicStatus, WorkerRecord, gen_entry_id

        try:
            raw_entries = json.loads(entries)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid entries JSON: {e}"})

        if not isinstance(raw_entries, list):
            return json.dumps({"error": "entries must be a JSON array"})

        new_entries = []
        for ed in raw_entries:
            src = None
            if ed.get("source"):
                s = ed["source"]
                src = EntrySource(
                    document=s.get("document"),
                    section=s.get("section"),
                    evidence=s.get("evidence", ""),
                )
            epist = None
            if ed.get("epistemic"):
                ep = ed["epistemic"]
                epist = EpistemicStatus(
                    classification=ep.get("classification", "inference"),
                    source_credibility=ep.get("source_credibility", "unknown"),
                    motivation=ep.get("motivation", ""),
                )
            new_entries.append(Entry(
                id=gen_entry_id(),
                type=ed.get("type", "observation"),
                content=ed.get("content", ""),
                source=src, epistemic=epist,
                created_by=WorkerRecord(worker_id, worker_description, bb.iteration),
                confidence=ed.get("confidence", 0.7),
                tags=ed.get("tags", []),
                opens_questions=ed.get("opens_questions", []),
                supports_entries=ed.get("supports_entries", []),
                contradicts_entries=ed.get("contradicts_entries", []),
                supersedes_entries=ed.get("supersedes_entries", []),
                addresses_signals=ed.get("addresses_signals", []),
            ))

        signals_before = len(bb.signals)
        bb.add_entries_batch(new_entries)
        new_signals = bb.signals[signals_before:]

        _save_state(blackboard_id, bb)

    return json.dumps({
        "created_entries": [_entry_dict(e) for e in new_entries],
        "new_signals": [_signal_dict(s) for s in new_signals],
        "summary": _bb_summary(bb),
    })


@mcp.tool()
def irys_add_signal(
    blackboard_id: str,
    signal_type: str,
    content: str,
    priority: str = "medium",
    origin_entry: str = "",
) -> str:
    """Add a signal (question, gap, or issue) to the blackboard.

    Signals are automatically deduplicated against existing open signals.

    Args:
        blackboard_id: The blackboard to add the signal to.
        signal_type: "question"|"convergence_gap"|"contradiction_resolution"|"source_gap"
        content: Description of the question or gap.
        priority: "low"|"medium"|"high"|"critical"
        origin_entry: Entry ID that raised this signal (optional).
    """
    with _get_lock(blackboard_id):
        try:
            bb = _load_state(blackboard_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Blackboard not found: {blackboard_id}"})

        from .swarm.models import Signal, gen_signal_id

        sig = Signal(
            id=gen_signal_id(), type=signal_type, content=content,
            origin_entry=origin_entry, priority=priority,
            status="open", iteration_created=bb.iteration,
        )
        count_before = len(bb.signals)
        bb.add_signal(sig)
        deduped = len(bb.signals) == count_before

        _save_state(blackboard_id, bb)

    return json.dumps({
        "signal": _signal_dict(sig),
        "deduped": deduped,
        "summary": _bb_summary(bb),
    })


@mcp.tool()
def irys_get_state(
    blackboard_id: str,
    entry_status: str = "active,disputed",
    signal_status: str = "open",
    max_entries: int = 100,
) -> str:
    """Inspect the current blackboard state.

    Args:
        blackboard_id: The blackboard to inspect.
        entry_status: Comma-separated entry statuses to include (default: active,disputed).
        signal_status: Comma-separated signal statuses to include (default: open).
        max_entries: Maximum entries to return (default: 100).
    """
    with _get_lock(blackboard_id):
        try:
            bb = _load_state(blackboard_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Blackboard not found: {blackboard_id}"})

    statuses = set(entry_status.split(","))
    sig_statuses = set(signal_status.split(","))

    filtered_entries = [e for e in bb.entries if e.status in statuses][:max_entries]
    filtered_signals = [s for s in bb.signals if s.status in sig_statuses]

    return json.dumps({
        "blackboard_id": blackboard_id,
        "task_instruction": bb.task_instruction,
        "summary": _bb_summary(bb),
        "entries": [_entry_dict(e) for e in filtered_entries],
        "signals": [_signal_dict(s) for s in filtered_signals],
        "documents": [_doc_status_dict(d) for d in bb.documents],
    })


@mcp.tool()
def irys_get_document_text(
    blackboard_id: str,
    doc_id: str,
    start_char: int = 0,
    max_chars: int = 24000,
    mark_read: bool = False,
) -> str:
    """Read document text from the blackboard.

    Args:
        blackboard_id: The blackboard containing the document.
        doc_id: Document ID to read.
        start_char: Character offset to start reading from.
        max_chars: Maximum characters to return (default: 24000).
        mark_read: Mark sections covered as read.
    """
    with _get_lock(blackboard_id):
        try:
            bb = _load_state(blackboard_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Blackboard not found: {blackboard_id}"})

        doc = next((d for d in bb.documents if d.id == doc_id), None)
        if not doc:
            return json.dumps({"error": f"Document not found: {doc_id}"})

        text = doc.text[start_char:start_char + max_chars]
        end_char = start_char + len(text)
        truncated = end_char < len(doc.text)

        if mark_read and doc.section_index:
            for sec in doc.section_index.sections:
                if sec.start_char < end_char and sec.end_char > start_char:
                    doc.mark_section_read(sec.name)
            _save_state(blackboard_id, bb)

    return json.dumps({
        "doc_id": doc.id, "name": doc.name,
        "start_char": start_char, "end_char": end_char,
        "text": text, "truncated": truncated,
        "total_chars": len(doc.text),
        "read_status": doc.read_status,
    })


@mcp.tool()
def irys_search_documents(
    blackboard_id: str,
    query: str,
    max_results: int = 20,
    context_chars: int = 500,
) -> str:
    """Search document text for a pattern (case-insensitive).

    Args:
        blackboard_id: The blackboard containing documents.
        query: Text pattern to search for.
        max_results: Maximum results to return.
        context_chars: Characters of context around each match.
    """
    with _get_lock(blackboard_id):
        try:
            bb = _load_state(blackboard_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Blackboard not found: {blackboard_id}"})

    results = []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    for doc in bb.documents:
        for m in pattern.finditer(doc.text):
            if len(results) >= max_results:
                break
            start = max(0, m.start() - context_chars // 2)
            end = min(len(doc.text), m.end() + context_chars // 2)
            results.append({
                "doc_id": doc.id, "name": doc.name,
                "start_char": start, "end_char": end,
                "snippet": doc.text[start:end],
            })

    return json.dumps({"query": query, "results": results, "total": len(results)})


# ── Phase 2: Lifecycle & Convergence ─────────────────────────────────


@mcp.tool()
def irys_set_iteration(
    blackboard_id: str,
    increment: bool = True,
    expire_old_signals: bool = True,
) -> str:
    """Advance the blackboard iteration and optionally expire old signals.

    Args:
        blackboard_id: The blackboard to advance.
        increment: Increment iteration by 1 (default: true).
        expire_old_signals: Expire stale low/medium signals (default: true).
    """
    with _get_lock(blackboard_id):
        try:
            bb = _load_state(blackboard_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Blackboard not found: {blackboard_id}"})

        if increment:
            bb.iteration += 1
        expired = []
        if expire_old_signals:
            before_ids = {s.id for s in bb.signals if s.status == "expired"}
            bb.expire_old_signals()
            expired = [s for s in bb.signals if s.status == "expired" and s.id not in before_ids]

        _save_state(blackboard_id, bb)

    return json.dumps({
        "iteration": bb.iteration,
        "expired_signals": [_signal_dict(s) for s in expired],
        "summary": _bb_summary(bb),
    })


@mcp.tool()
def irys_convergence_report(blackboard_id: str) -> str:
    """Check if the blackboard analysis is complete.

    Returns deterministic convergence blockers: open critical signals,
    unresolved contradictions, unread documents, and thin coverage.
    """
    with _get_lock(blackboard_id):
        try:
            bb = _load_state(blackboard_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Blackboard not found: {blackboard_id}"})

    open_sigs = [s for s in bb.signals if s.status == "open"]
    critical = [s for s in open_sigs if s.priority == "critical"]
    high = [s for s in open_sigs if s.priority == "high"]
    disputed = [e for e in bb.entries if e.status == "disputed"]
    unread = [d for d in bb.documents if d.read_status == "unread"]
    partial = [d for d in bb.documents if d.read_status == "partially_read"]

    blockers = []
    if critical:
        blockers.append(f"{len(critical)} critical signal(s) unresolved")
    if disputed:
        blockers.append(f"{len(disputed)} disputed entry/entries")
    if unread:
        blockers.append(f"{len(unread)} document(s) completely unread")

    return json.dumps({
        "converged": len(blockers) == 0,
        "blockers": blockers,
        "critical_signals": [_signal_dict(s) for s in critical],
        "high_signals": [_signal_dict(s) for s in high],
        "disputed_entries": [_entry_dict(e) for e in disputed],
        "unread_documents": [_doc_status_dict(d) for d in unread],
        "partially_read_documents": [_doc_status_dict(d) for d in partial],
        "summary": _bb_summary(bb),
    })


@mcp.tool()
def irys_synthesis_packet(blackboard_id: str) -> str:
    """Get everything needed to synthesize a final answer.

    Returns the task instruction, must-include entries, open signals,
    and disputed entries for Claude Code to draft the deliverable.
    """
    with _get_lock(blackboard_id):
        try:
            bb = _load_state(blackboard_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Blackboard not found: {blackboard_id}"})

    active = [e for e in bb.entries if e.status == "active"]
    high_conf = [e for e in active if e.confidence >= 0.6]
    disputed = [e for e in bb.entries if e.status == "disputed"]
    open_sigs = [s for s in bb.signals if s.status == "open"]

    return json.dumps({
        "task_instruction": bb.task_instruction,
        "must_include_entries": [_entry_dict(e) for e in high_conf],
        "disputed_entries": [_entry_dict(e) for e in disputed],
        "open_signals": [_signal_dict(s) for s in open_sigs],
        "documents": [{"name": d.name, "read_status": d.read_status} for d in bb.documents],
        "summary": _bb_summary(bb),
    })


@mcp.tool()
def irys_save_snapshot(blackboard_id: str, label: str = "") -> str:
    """Save a named snapshot of the current blackboard state.

    Args:
        blackboard_id: The blackboard to snapshot.
        label: Optional label for the snapshot.
    """
    with _get_lock(blackboard_id):
        try:
            bb = _load_state(blackboard_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Blackboard not found: {blackboard_id}"})

        d = _state_dir(blackboard_id) / "snapshots"
        d.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        suffix = f"_{label}" if label else ""
        path = d / f"{ts}{suffix}.json"
        state = json.loads((_state_dir(blackboard_id) / "state.json").read_text(encoding="utf-8"))
        path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

    return json.dumps({"path": str(path), "summary": _bb_summary(bb)})


@mcp.tool()
def irys_list_blackboards() -> str:
    """List all active blackboards in this session."""
    results = []
    if _STORE_ROOT.exists():
        for d in sorted(_STORE_ROOT.iterdir()):
            state_file = d / "state.json"
            if state_file.exists():
                try:
                    data = json.loads(state_file.read_text(encoding="utf-8"))
                    results.append({
                        "blackboard_id": d.name,
                        "task_instruction": data.get("task_instruction", "")[:100],
                        "documents": len(data.get("documents", [])),
                        "entries": len(data.get("entries", [])),
                        "updated_at": data.get("updated_at", ""),
                    })
                except Exception:
                    pass
    return json.dumps({"blackboards": results})


@mcp.tool()
def irys_supported_formats() -> str:
    """List document formats supported by irys for ingestion."""
    from .ingestion import SUPPORTED_EXTENSIONS
    return json.dumps({"formats": sorted(SUPPORTED_EXTENSIONS)})


def main():
    mcp.run()


if __name__ == "__main__":
    main()
