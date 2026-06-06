"""MCP server exposing irys stateful swarm as tools for Claude Code / Codex."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "irys",
    instructions="Stateful swarm document analysis — ask questions about documents",
)


def _check_api_key() -> str | None:
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.getenv(key):
            return None
    return "No API key found. Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable."


@mcp.tool()
def irys_ask(
    question: str,
    docs_path: str,
    output_format: str = "text",
    worker_model: str | None = None,
    synthesis_model: str | None = None,
) -> str:
    """Analyze documents using a stateful swarm and answer a question.

    Args:
        question: The question or instruction to answer about the documents.
        docs_path: Path to a file or directory containing documents.
            Supported formats: .txt, .md, .json, .docx, .xlsx, .pptx, .pdf, .eml
        output_format: Output format — "text" (markdown), "json" (structured), or "docx" (path to file).
        worker_model: Override the worker model (default: gemini-3.1-flash-lite).
        synthesis_model: Override the synthesis model (default: gemini-3.5-flash).
    """
    key_err = _check_api_key()
    if key_err:
        return key_err

    from .ingestion import ingest_file, ingest_directory, SUPPORTED_EXTENSIONS
    from .providers.gemini import GeminiCaller
    from .swarm import run_swarm
    from .swarm.models import Task

    path = Path(docs_path).resolve()
    if not path.exists():
        return f"Error: path does not exist: {docs_path}"

    if path.is_file():
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return (
                f"Error: unsupported file type {path.suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
        documents = [ingest_file(path)]
    else:
        documents = ingest_directory(path)

    if not documents:
        return (
            f"Error: no supported documents found in {docs_path}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    w_model = worker_model or os.getenv("SWARM_WORKER_MODEL", "gemini-3.1-flash-lite")
    s_model = synthesis_model or os.getenv("SWARM_SYNTHESIS_MODEL", "gemini-3.5-flash")
    r_model = os.getenv("SWARM_REVIEWER_MODEL", "gemini-3.5-flash")

    worker_caller = GeminiCaller(model=w_model)
    synth_caller = GeminiCaller(model=s_model) if s_model != w_model else worker_caller
    reviewer_caller = GeminiCaller(model=r_model) if r_model else None

    task = Task(
        instruction=question,
        documents=documents,
        metadata={"source": "mcp", "question": question},
        output_dir=str(Path.cwd()),
    )

    t0 = time.time()
    try:
        deliverable, blackboard = run_swarm(
            task, worker_caller,
            synthesis_caller=synth_caller,
            reviewer_caller=reviewer_caller,
        )
    except Exception as e:
        return f"Swarm error: {e}"

    elapsed = time.time() - t0
    content = deliverable if isinstance(deliverable, str) else "\n\n".join(deliverable.values())

    if output_format == "json":
        return json.dumps({
            "question": question,
            "answer": content,
            "documents": [d.name for d in documents],
            "tokens_used": blackboard.total_tokens_used,
            "wall_clock_seconds": round(elapsed, 1),
        }, indent=2)

    if output_format == "docx":
        out_dir = Path.cwd() / "irys-output"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "answer.docx"
        from .runner import _write_docx
        _write_docx(out_file, content)
        return f"Answer saved to {out_file}\n\n{content[:500]}..."

    footer = f"\n\n---\n{blackboard.total_tokens_used:,} tokens | {elapsed:.1f}s | {len(documents)} document(s)"
    return content + footer


@mcp.tool()
def irys_supported_formats() -> str:
    """List document formats supported by irys."""
    from .ingestion import SUPPORTED_EXTENSIONS
    formats = sorted(SUPPORTED_EXTENSIONS)
    return "Supported document formats: " + ", ".join(formats)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
