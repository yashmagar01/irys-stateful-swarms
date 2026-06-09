from __future__ import annotations

import json
import os

from .blackboard import Blackboard
from .models import ModelCaller, Signal, gen_signal_id
from .worker_dispatch import call_model, parse_json_object


def build_artifact_contracts(
    blackboard: Blackboard,
    deliverables_map: dict,
    caller: ModelCaller,
) -> tuple[list[dict], int]:
    """Derive artifact-native structural contracts from task + blackboard state.

    Returns (contract_items, tokens_used). Each contract item specifies what
    the final artifact MUST contain to be considered complete.
    """
    filenames = []
    for filename in deliverables_map.values():
        if isinstance(filename, str) and filename not in filenames:
            filenames.append(filename)

    if not filenames:
        filenames = ["output.txt"]

    active = [e for e in blackboard.entries if e.status == "active"]
    topic_summary = _topic_summary(active)

    all_contracts: list[dict] = []
    total_tokens = 0

    for filename in filenames:
        contracts, tokens = _derive_file_contract(
            blackboard.task_instruction, filename, topic_summary, caller,
        )
        total_tokens += tokens
        all_contracts.extend(contracts)

    return all_contracts, total_tokens


def _topic_summary(active: list, max_topics: int = 40) -> str:
    """Build a compact summary of topics covered by active entries."""
    topics: dict[str, int] = {}
    for e in active:
        doc = e.source.document if e.source else "cross-cutting"
        section = e.source.section if e.source and e.source.section else "general"
        key = f"{doc}/{section}"
        topics[key] = topics.get(key, 0) + 1

    sorted_topics = sorted(topics.items(), key=lambda x: -x[1])[:max_topics]
    return "\n".join(f"- {k} ({v} entries)" for k, v in sorted_topics)


def _derive_file_contract(
    task_instruction: str,
    filename: str,
    topic_summary: str,
    caller: ModelCaller,
) -> tuple[list[dict], int]:
    prompt = f"""Derive the structural contract for one output file.

TASK INSTRUCTION:
{task_instruction[:8000]}

OUTPUT FILE: {filename}

TOPICS DISCOVERED BY ANALYSIS (entry counts):
{topic_summary[:4000]}

Your job: list 10-30 REQUIRED structural elements that this output file MUST contain to be complete. Each element is something the final document needs — a section, a table, a comparison, a clause, a calculation — not just "mention X" but "the document must contain a structured [element type] covering [topic]."

Rules:
1. Derive requirements from the TASK INSTRUCTION, not from domain knowledge.
2. Each requirement should map to a concrete part of the output document.
3. Be specific: "deviation table comparing liquidation preference terms" not "discuss liquidation."
4. Include requirements for ALL major topics in the task, not just the first few.
5. Each requirement must specify what native form it takes (table, section, clause, paragraph, list, etc).

Return JSON:
{{"contracts": [
  {{"section": "section heading",
    "native_form": "table|section|clause|list|comparison|calculation|paragraph",
    "summary": "what this element must contain",
    "importance": "critical|high|medium"}}
]}}"""

    max_tokens = int(os.getenv("SWARM_CONTRACT_MAX_TOKENS", "4096"))
    payload, tokens = call_model(caller, prompt, max_tokens=max_tokens)

    contracts = []
    raw_contracts = payload.get("contracts", [])
    if not isinstance(raw_contracts, list):
        return contracts, tokens

    for c in raw_contracts:
        if not isinstance(c, dict):
            continue
        contracts.append({
            "section": str(c.get("section", "General")),
            "native_form": str(c.get("native_form", "section")),
            "summary": str(c.get("summary", "")),
            "importance": str(c.get("importance", "high")),
            "target_file": filename,
            "source": "artifact_contract",
        })

    return contracts, tokens


def contracts_to_signals(
    contracts: list[dict],
    blackboard: Blackboard,
) -> int:
    """Register artifact_requirement signals for critical/high contracts.

    These signals can only be closed by analytical/calculation/strategy entries,
    not by plain observations. Returns count of signals added.
    """
    count = 0
    for c in contracts:
        importance = c.get("importance", "medium")
        if importance not in ("critical", "high"):
            continue
        section = c.get("section", "")
        native_form = c.get("native_form", "")
        target = c.get("target_file", "")
        summary = c.get("summary", "")
        content = (
            f"Artifact must contain {native_form} in '{section}' "
            f"for {target}: {summary}"
        )
        priority = "critical" if importance == "critical" else "high"
        blackboard.add_signal(Signal(
            id=gen_signal_id(),
            type="artifact_requirement",
            content=content,
            origin_entry="artifact_contract",
            priority=priority,
            status="open",
            iteration_created=blackboard.iteration,
        ))
        count += 1
    return count
