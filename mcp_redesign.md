**Architectural Verdict**

Replace `irys_ask` as the primary MCP surface. The current server in [src/mcp_server.py](/C:/Users/devan/OneDrive/Desktop/Projects/ant-irys/src/mcp_server.py:36) is a Gemini-gated wrapper around `run_swarm`, and it checks `GEMINI_API_KEY` / `GOOGLE_API_KEY` before any useful analysis can happen. That hides the real product: the blackboard state machine in [src/swarm/blackboard.py](/C:/Users/devan/OneDrive/Desktop/Projects/ant-irys/src/swarm/blackboard.py:35), with `Entry`, `Signal`, `DocumentStatus`, and propagation semantics from [src/swarm/models.py](/C:/Users/devan/OneDrive/Desktop/Projects/ant-irys/src/swarm/models.py:118).

The new MCP server should make Claude Code the reasoning engine and expose Irys as structured state, source custody, and convergence tooling.

**Core State Model**

Persist one workspace per analysis:

```ts
BlackboardState {
  blackboard_id: string
  task_instruction: string
  created_at: string
  updated_at: string
  output_dir: string
  documents: DocumentStatus[]
  entries: Entry[]
  signals: Signal[]
  iteration: number
  metadata: object
  next_entry_num: number
  next_signal_num: number
}
```

Do not rely on existing `save_snapshot()` as the canonical MCP store. Current snapshots omit document text, omit some token fields, and serialize `Signal.to_dict()` without `iteration_created`. Use a full-fidelity MCP store under something like:

```text
/tmp/irys-state/{blackboard_id}/state.json
/tmp/irys-state/{blackboard_id}/documents/{doc_id}.txt
/tmp/irys-state/{blackboard_id}/snapshots/{timestamp}_{label}.json
```

Every mutating tool takes `blackboard_id`, loads `state.json`, applies the `Blackboard` methods, writes atomically, and returns a compact state delta. Use a per-state lock file to avoid concurrent writes from multiple Claude Code tool calls.

**Minimal Useful Tool Set**

This is the smallest set that makes Claude Code immediately productive:

```python
irys_create_blackboard(
    task_instruction: str,
    docs_path: str | None = None,
    document_ids: list[str] | None = None,
    output_dir: str | None = None,
    metadata: dict | None = None,
) -> {
    "blackboard_id": str,
    "task_instruction": str,
    "documents": list[DocumentStatusDict],
    "summary": BlackboardSummary,
    "next_actions": list[str]
}
```

Creates a `Blackboard` using the same `DocumentStatus` shape that `run_swarm` builds in `_build_doc_statuses()` at [src/swarm/__init__.py](/C:/Users/devan/OneDrive/Desktop/Projects/ant-irys/src/swarm/__init__.py:459). If `docs_path` is provided, use existing ingestion from [src/ingestion/__init__.py](/C:/Users/devan/OneDrive/Desktop/Projects/ant-irys/src/ingestion/__init__.py:30).

```python
irys_get_context(
    blackboard_id: str,
    purpose: Literal["read", "analyze", "resolve_signal", "synthesize", "review"] = "analyze",
    signal_ids: list[str] | None = None,
    entry_ids: list[str] | None = None,
    doc_ids: list[str] | None = None,
    max_chars: int = 24000,
) -> {
    "blackboard_id": str,
    "task_instruction": str,
    "iteration": int,
    "summary": BlackboardSummary,
    "open_signals": list[SignalDict],
    "relevant_entries": list[EntryDict],
    "document_sections": list[{
        "doc_id": str,
        "document": str,
        "section": str,
        "start_char": int,
        "end_char": int,
        "text": str
    }],
    "write_contract": {
        "expected_entry_fields": object,
        "expected_signal_fields": object
    }
}
```

This is the “prompt packet” tool. Claude Code calls it before doing its own analysis. It returns source text, current state, and the exact structured fields Claude should write back.

```python
irys_add_entries(
    blackboard_id: str,
    entries: list[{
        "type": Literal["observation","analysis","calculation","strategy","gap","contradiction"],
        "content": str,
        "source": {"document": str | None, "section": str | None, "evidence": str} | None,
        "epistemic": {
            "classification": str,
            "source_credibility": str,
            "motivation": str,
            "neutral_restatement": str | None
        } | None,
        "confidence": float,
        "verified": bool | None,
        "tags": list[str],
        "opens_questions": list[str],
        "supports_entries": list[str],
        "contradicts_entries": list[str],
        "supersedes_entries": list[str],
        "addresses_signals": list[str]
    }],
    worker: {"worker_id": str, "description": str} | None = None,
) -> {
    "created_entries": list[EntryDict],
    "created_or_updated_signals": list[SignalDict],
    "status_changes": list[{"id": str, "from": str, "to": str, "reason": str}],
    "summary": BlackboardSummary
}
```

This should call `Blackboard.add_entries_batch()`, because that already extracts questions into signals and propagates support, contradiction, supersession, and addressed-signal effects at [src/swarm/blackboard.py](/C:/Users/devan/OneDrive/Desktop/Projects/ant-irys/src/swarm/blackboard.py:95).

```python
irys_add_signal(
    blackboard_id: str,
    type: Literal["question","convergence_gap","contradiction_resolution","source_gap","synthesis_obligation"],
    content: str,
    priority: Literal["low","medium","high","critical"] = "medium",
    origin_entry: str = "",
    status: Literal["open","addressed","expired","waived"] = "open",
) -> {
    "signal": SignalDict,
    "deduped_into": str | None,
    "summary": BlackboardSummary
}
```

Use `Blackboard.add_signal()`, which already dedupes similar open signals and preserves the higher priority at [src/swarm/blackboard.py](/C:/Users/devan/OneDrive/Desktop/Projects/ant-irys/src/swarm/blackboard.py:144).

```python
irys_get_state(
    blackboard_id: str,
    include_entries: bool = True,
    include_signals: bool = True,
    entry_status: list[str] = ["active","disputed"],
    signal_status: list[str] = ["open"],
    max_entries: int = 100,
) -> {
    "blackboard_id": str,
    "summary": BlackboardSummary,
    "entries": list[EntryDict],
    "signals": list[SignalDict],
    "documents": list[DocumentStatusDict]
}
```

This is the basic state inspection tool.

**Document Tools**

Large document text should not be passed through MCP parameters. Use paths and handles.

```python
irys_ingest_documents(
    path: str,
    recursive: bool = True,
) -> {
    "documents": list[{
        "doc_id": str,
        "name": str,
        "size_bytes": int,
        "extension": str,
        "path": str,
        "headings": list[str],
        "section_count": int
    }]
}
```

Backed by `ingest_file()` / `ingest_directory()` and supported formats from [src/ingestion/__init__.py](/C:/Users/devan/OneDrive/Desktop/Projects/ant-irys/src/ingestion/__init__.py:15): `.txt`, `.md`, `.json`, `.docx`, `.xlsx`, `.pptx`, `.pdf`, `.eml`.

```python
irys_get_document_text(
    blackboard_id: str,
    doc_id: str,
    section: str | None = None,
    start_char: int | None = None,
    max_chars: int = 24000,
    mark_read: bool = False,
) -> {
    "doc_id": str,
    "document": str,
    "section": str | None,
    "start_char": int,
    "end_char": int,
    "text": str,
    "truncated": bool,
    "read_status": str
}
```

If `mark_read=True`, call `DocumentStatus.mark_section_read()`, matching [src/swarm/models.py](/C:/Users/devan/OneDrive/Desktop/Projects/ant-irys/src/swarm/models.py:190).

```python
irys_search_documents(
    blackboard_id: str,
    query: str,
    doc_ids: list[str] | None = None,
    max_results: int = 20,
    context_chars: int = 1000,
) -> {
    "results": list[{
        "doc_id": str,
        "document": str,
        "section": str | None,
        "start_char": int,
        "end_char": int,
        "snippet": str
    }]
}
```

This is deterministic text search only. Claude does the semantic judgment.

**Lifecycle Tools**

Once the minimal tools work, add these:

```python
irys_update_entries(
    blackboard_id: str,
    updates: list[{
        "entry_id": str,
        "status": Literal["active","disputed","superseded"] | None,
        "confidence": float | None,
        "verified": bool | None,
        "tags_add": list[str] | None,
        "tags_remove": list[str] | None,
        "addresses_signals": list[str] | None
    }]
) -> {"updated_entries": list[EntryDict], "summary": BlackboardSummary}
```

```python
irys_set_iteration(
    blackboard_id: str,
    iteration: int | None = None,
    increment: bool = False,
    expire_old_signals: bool = True,
) -> {"iteration": int, "expired_signals": list[SignalDict], "summary": BlackboardSummary}
```

```python
irys_convergence_report(
    blackboard_id: str,
) -> {
    "converged": bool,
    "blocking_signals": list[SignalDict],
    "critical_signals": list[SignalDict],
    "disputed_entries": list[EntryDict],
    "unread_documents": list[DocumentStatusDict],
    "thin_coverage": list[{"doc_id": str, "reason": str}],
    "must_surface": list[EntryDict],
    "recommended_next_context_call": object
}
```

This should not pretend to be an LLM judge. It should compute deterministic blockers: open critical signals, unresolved contradiction signals, disputed entries, unread source sections, and must-surface facts. That matches the design doc’s “completion earned, not inferred” requirement in [docs/SWARM_INTELLIGENCE.md](/C:/Users/devan/OneDrive/Desktop/Projects/ant-irys/docs/SWARM_INTELLIGENCE.md:266).

```python
irys_synthesis_packet(
    blackboard_id: str,
    format: Literal["markdown","json"] = "markdown",
    include_open_questions: bool = True,
    include_source_evidence: bool = True,
) -> {
    "task_instruction": str,
    "must_include_entries": list[EntryDict],
    "open_signals": list[SignalDict],
    "disputed_entries": list[EntryDict],
    "source_index": list[object],
    "prompt_packet": str
}
```

Claude Code uses this to draft the final answer itself.

```python
irys_save_snapshot(
    blackboard_id: str,
    label: str = "",
) -> {"path": str, "blackboard_id": str, "summary": BlackboardSummary}
```

```python
irys_load_blackboard(
    path: str,
) -> {"blackboard_id": str, "summary": BlackboardSummary, "documents": list[DocumentStatusDict]}
```

**Optional Compatibility Tool**

Keep the old behavior, but make it clearly optional:

```python
irys_run_full_swarm(
    question: str,
    docs_path: str,
    output_format: Literal["text","json","docx"] = "text",
    worker_model: str | None = None,
    synthesis_model: str | None = None,
    token_budget: int | None = None,
    max_iterations: int | None = None,
    no_reviewer: bool = False,
) -> {
    "answer": str,
    "blackboard_id": str | None,
    "run_dir": str,
    "tokens_used": int,
    "wall_clock_seconds": float
}
```

This is where Gemini key checks belong. The no-key tools should never call `_check_api_key()`.

**Return Dict Shapes**

Use the existing model fields but normalize names:

```ts
EntryDict {
  id: string
  type: string
  content: string
  source: {document: string | null, section: string | null, evidence: string} | null
  epistemic: {classification: string, source_credibility: string, motivation: string, neutral_restatement?: string | null} | null
  created_by: {worker_id: string, description: string, iteration: number}
  confidence: number
  verified: boolean | null
  tags: string[]
  status: string
  opens_questions: string[]
  supports_entries: string[]
  contradicts_entries: string[]
  supersedes_entries: string[]
  addresses_signals: string[]
}
```

Do not use the current `Entry.to_dict()` names as the external API unchanged, because it emits `supports`, `contradicts`, and `supersedes` while the dataclass fields are `supports_entries`, `contradicts_entries`, and `supersedes_entries`.

```ts
SignalDict {
  id: string
  type: string
  content: string
  origin_entry: string
  priority: "low" | "medium" | "high" | "critical"
  status: "open" | "addressed" | "expired" | "waived"
  addressed_by: string | null
  iteration_created: number
}
```

Include `iteration_created`; current `Signal.to_dict()` does not.

**The Aha Moment**

The hook is not “ask Irys a question.” The hook is:

“Claude reads a document section, writes 20 sourced observations into Irys, and Irys immediately turns those observations into an evolving shared state: open questions, contradictions, disputed facts, unread sections, must-surface evidence, and convergence blockers.”

That is the state management pattern. Claude Code remains the intelligence. Irys becomes the memory, custody, and convergence substrate.

The first demo should be a three-call loop:

1. `irys_create_blackboard(task_instruction, docs_path)`
2. `irys_get_context(purpose="read")`
3. `irys_add_entries([...])`

Then show `irys_get_state()` returning new signals created from `opens_questions`, contradiction status changes from `contradicts_entries`, and a convergence report explaining exactly what remains unresolved. That makes the framework reusable without any extra model key.

