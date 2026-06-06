Reviewed `feature/mcp-integration` at `3efeee8f5f2ef147b1b4ffcac9d4aef8837b38dc` via `git show` because the live checkout is currently `feature/simple-cli`, not `feature/mcp-integration`.

**Findings**
1. **High: MCP writes artifacts and internal state into the server process cwd.**  
   `irys_ask` sets `Task.output_dir=str(Path.cwd())` in `src/mcp_server.py:79-84`, and `Blackboard.save_snapshot` writes under `<output_dir>/swarm` in `src/swarm/blackboard.py:234-240`. In Claude Code / Codex, cwd may be the repo root or whatever directory launched the MCP server, so ordinary tool calls can pollute the repo with `swarm/` snapshots and expose prior run traces across users/tasks. `output_format="docx"` also always writes `Path.cwd()/irys-output/answer.docx` in `src/mcp_server.py:108-114`, causing overwrites and concurrency collisions. Use a per-call output directory, preferably under a configured cache/output root, with unique run IDs.

2. **High: arbitrary local path access is not bounded.**  
   `docs_path` is resolved and then ingested if it exists in `src/mcp_server.py:51-63`. For MCP, this means a client can ask the server to read any file/directory the server process can access. That may be acceptable for a local-only dev tool, but it is not production-ready without an allowlist/root constraint, explicit config, or at least refusal outside approved workspaces. Recursive directory ingestion is especially risky because `ingest_directory` walks all descendants in `src/ingestion/__init__.py:46-51`.

3. **Medium: ingestion and output errors are not caught.**  
   The only `try` block wraps `run_swarm` in `src/mcp_server.py:86-94`. Parser failures from `ingest_file` / `ingest_directory` happen before that, and `_write_docx` failures happen after it. `ingest_file` directly calls the selected reader in `src/ingestion/__init__.py:30-38`, so malformed PDFs/DOCX/XLSX or permission errors can escape as MCP exceptions instead of a clean tool result.

4. **Medium: tool parameters are under-specified for agent users.**  
   The interface has `question`, `docs_path`, `output_format`, and model overrides in `src/mcp_server.py:24-31`. That is simple, but Claude Code / Codex users need budget and execution controls: `max_iterations`, `token_budget`, `max_docs`, `max_bytes`, `output_dir`, and maybe `reviewer_model` / `quality_mode`. `run_swarm` already accepts token and iteration overrides in `src/swarm/__init__.py:96-101`, but the MCP tool does not expose them.

5. **Medium: `output_format` is not validated.**  
   Anything except `"json"` or `"docx"` silently becomes text in `src/mcp_server.py:99-117`. MCP tools should reject invalid enum values clearly. Ideally expose this as a literal/enum schema if FastMCP supports it, or validate manually.

6. **Medium: MCP bypasses runner integration semantics.**  
   `runner.run_single_task` creates task-scoped output dirs, writes deliverables, extracts artifact texts, finalizes survival traces, and writes metrics/status in `src/runner.py:73-103` and `src/runner.py:157-173`. The MCP path calls `run_swarm` directly and only optionally writes one DOCX. That loses metrics, status, survival-trace finalization, deliverable naming, and cost reporting. If MCP is meant to be a production surface, it should reuse a shared “run documents/question” service rather than hand-rolling a parallel runner.

7. **Low: tests are useful but shallow.**  
   `tests/test_mcp_server.py` covers registration, missing API key, nonexistent path, unsupported file, empty directory, happy path, JSON, and swarm error. Missing cases include invalid `output_format`, parser exceptions, DOCX output writing, directory recursion limits, model override propagation, output directory uniqueness, no path leakage, and actual entry point import/launch. Also `pytest` is in runtime dependencies in `pyproject.toml:19-22`; move it to a dev/test extra.

**A. Interface Design**
Good first cut: `irys_ask` and `irys_supported_formats` are discoverable, and the main tool maps naturally to “ask a question about these docs.” For Claude Code / Codex users, though, the tool is too opaque and too expensive by default. It needs budget knobs, a run identifier/output location, and structured status metadata. I would also split “answer inline” from “write artifact” or return a structured envelope consistently for all formats.

**B. Edge Cases**
Not fully covered. Path existence, unsupported file type, empty directory, and `run_swarm` exceptions are covered. Missing: invalid format, unreadable files, parser crashes, giant directories, symlink traversal policy, concurrent runs, write failures, arbitrary cwd behavior, empty/blank question, and output collisions.

**C. Production-Ready Gaps**
Add workspace allowlisting, per-run output isolation, structured errors, budget limits, telemetry/metrics, stable JSON response schema, cancellation/timeouts, integration tests that run the installed `irys-state` entry point, and docs for Claude/Codex MCP config. Also avoid writing internal snapshots into arbitrary cwd.

**D. Bugs / Security / Integration**
The biggest integration bug is cwd-based output/state. The biggest security issue is unrestricted filesystem reads through `docs_path`. The biggest correctness gap is bypassing runner finalization and metrics. There is also a practical bug where repeated `docx` calls overwrite `irys-output/answer.docx`.

**Top 3 Improvements**
1. Add a request-scoped run directory and return `{run_id, answer, files, metrics, errors}` for every call. Never write snapshots or DOCX output directly to `Path.cwd()`.
2. Add validation and limits: allowed roots, `output_format` enum, non-empty question, max docs/bytes, token budget, max iterations, and clean handling for ingestion/write exceptions.
3. Refactor MCP to share runner/service code so MCP, CLI, and benchmark paths use the same output, metrics, survival-trace, and deliverable handling.

I did not run the test suite because the requested files are on a different local branch than the current checkout; this was a text-only review of the branch contents.

