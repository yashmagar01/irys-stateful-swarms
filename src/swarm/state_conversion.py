"""State conversion review and plan coverage.

Pass 1 (state conversion): converts raw observations into analytical entries.
Pass 2 (plan coverage): evaluates seed question/criteria coverage adversarially.
"""
from __future__ import annotations

import json

from .blackboard import Blackboard
from .models import (
    Entry, EntrySource, EpistemicStatus, ModelCaller, WorkerRecord,
    gen_entry_id,
)
from .seed import format_task_state_map
from .worker_dispatch import call_model


def run_state_conversion_review(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
    *,
    max_observation_candidates: int = 450,
    max_new_entries: int = 40,
    retry_batch_size: int = 150,
    max_retry_batches: int = 4,
) -> tuple[list[Entry], dict, int]:
    """Convert raw observations into analytical entries.

    Returns (new_entries, conversion_report, tokens_used).
    """
    active = [e for e in blackboard.entries if e.status == "active"]

    analytical = [e for e in active if e.type in ("analysis", "calculation", "strategy", "gap")]
    observations = [
        e for e in active
        if e.type == "observation" and e.confidence >= 0.7
        and e.source and e.source.document
    ]

    observations = _source_balanced_sample(observations, max_observation_candidates)

    analytical_text = "\n".join(
        f"[{e.id}] ({e.type}) {_source_tag(e)} {e.content[:400]}"
        for e in analytical
    )

    framework = seed.get("analytical_framework", "")
    task_state_map = format_task_state_map(seed)

    prompt = _build_state_conversion_prompt(
        task_instruction=blackboard.task_instruction,
        framework=framework,
        task_state_map=task_state_map,
        analytical=analytical,
        analytical_text=analytical_text,
        observations=observations,
        max_new_entries=max_new_entries,
        observations_label="source-balanced sample",
    )

    payload, tokens = call_model(caller, prompt, max_tokens=16384)

    total_tokens = tokens
    initial_parse_error = bool(payload.get("parse_error"))
    retry_batches = 0
    retry_parse_errors = 0
    if initial_parse_error:
        retry_new_entries = []
        for batch in _entry_batches(observations, retry_batch_size)[:max_retry_batches]:
            retry_batches += 1
            retry_max_new_entries = max(1, min(max_new_entries, max_new_entries // 2 or 1))
            retry_prompt = _build_state_conversion_prompt(
                task_instruction=blackboard.task_instruction,
                framework=framework,
                task_state_map=task_state_map,
                analytical=analytical,
                analytical_text=analytical_text,
                observations=batch,
                max_new_entries=retry_max_new_entries,
                observations_label="source-balanced retry batch",
            )
            retry_payload, retry_tokens = call_model(
                caller, retry_prompt, max_tokens=8192,
            )
            total_tokens += retry_tokens
            if retry_payload.get("parse_error"):
                retry_parse_errors += 1
                continue
            batch_entries = retry_payload.get("new_entries", [])
            if isinstance(batch_entries, list):
                retry_new_entries.extend(batch_entries)
            if len(retry_new_entries) >= max_new_entries:
                break
        payload = {
            "new_entries": retry_new_entries,
            "parse_error": not retry_new_entries,
        }

    new_entries_raw = payload.get("new_entries", [])
    if not isinstance(new_entries_raw, list):
        new_entries_raw = []

    active_ids = {e.id for e in active}
    entries = []
    dropped = 0

    for o in new_entries_raw[:max_new_entries]:
        if not isinstance(o, dict):
            continue
        content = str(o.get("content", "")).strip()
        if not content or len(content) < 20:
            continue

        entry_type = o.get("type", "analysis")
        if entry_type not in ("analysis", "calculation", "strategy", "gap"):
            entry_type = "analysis"

        source_ids = o.get("source_entries", [])
        if not isinstance(source_ids, list):
            source_ids = []
        valid_sources = [s for s in source_ids if s in active_ids]

        if entry_type != "gap" and not valid_sources:
            dropped += 1
            continue

        has_grounded_source = any(
            e.source and e.source.document
            for e in active if e.id in valid_sources
        )
        if entry_type != "gap" and not has_grounded_source:
            dropped += 1
            continue

        source = None
        if valid_sources:
            ref_entry = next((e for e in active if e.id == valid_sources[0]), None)
            if ref_entry and ref_entry.source and ref_entry.source.document:
                all_same_doc = all(
                    (e.source and e.source.document == ref_entry.source.document)
                    for e in active if e.id in valid_sources
                )
                if all_same_doc:
                    source = EntrySource(
                        document=ref_entry.source.document,
                        section=ref_entry.source.section,
                        evidence="",
                    )

        try:
            conf = min(max(float(o.get("confidence", 0.75)), 0.0), 1.0)
        except (ValueError, TypeError):
            conf = 0.75

        conversion_type = o.get("conversion_type", "")
        materiality = o.get("materiality", "high")

        tags = ["state_conversion", conversion_type, f"materiality:{materiality}"]

        entries.append(Entry(
            id=gen_entry_id(), type=entry_type, content=content,
            source=source,
            epistemic=EpistemicStatus("inference", "unknown", ""),
            created_by=WorkerRecord(
                "state_conversion_reviewer", "state_conversion_review",
                blackboard.iteration,
            ),
            confidence=conf,
            tags=tags, status="active",
            supports_entries=valid_sources,
        ))

    report = {
        "parse_error": bool(payload.get("parse_error")) or (initial_parse_error and not entries),
        "initial_parse_error": initial_parse_error,
        "retry_batches": retry_batches,
        "retry_parse_errors": retry_parse_errors,
        "entries_created": len(entries),
        "entries_dropped_no_source": dropped,
        "created_entry_ids": [e.id for e in entries],
        "observation_candidates": len(observations),
        "analytical_entries_before": len(analytical),
    }

    return entries, report, total_tokens


def run_plan_coverage_review(
    blackboard: Blackboard,
    seed: dict,
    caller: ModelCaller,
    *,
    max_analytical_entries: int = 250,
    analytical_char_limit: int = 80_000,
    batch_size: int = 6,
    domain_lens: dict | None = None,
) -> tuple[dict, int]:
    """Adversarial coverage review of seed questions and completeness criteria.

    Runs AFTER state conversion entries have been added to the blackboard.
    Returns (coverage_report, tokens_used).
    """
    active = [e for e in blackboard.entries if e.status == "active"]
    analytical = [
        e for e in active
        if e.type in ("analysis", "calculation", "strategy", "gap")
    ]
    analytical = _source_balanced_sample(analytical, max_analytical_entries)

    analytical_text = "\n".join(
        f"[{e.id}] ({e.type}) {_source_tag(e)} {e.content[:300]}"
        for e in analytical
    )

    key_questions = list(seed.get("key_questions", []) or [])
    completeness = list(seed.get("completeness_criteria", []) or [])
    task_state_map = format_task_state_map(seed)

    total_tokens = 0
    parse_errors: list[str] = []
    seed_rows: list[dict] = []
    criteria_rows: list[dict] = []

    for offset in range(0, len(key_questions), max(batch_size, 1)):
        rows = [
            {"id": f"q{i + 1}", "text": str(q)}
            for i, q in enumerate(key_questions[offset:offset + batch_size], offset)
        ]
        batch, tokens, error = _run_coverage_batch(
            blackboard.task_instruction, analytical_text[:analytical_char_limit],
            len(analytical), rows, "seed_question", caller, task_state_map,
        )
        total_tokens += tokens
        if error and len(rows) > 1:
            retry_rows, retry_tokens, retry_errors = _retry_coverage_rows(
                blackboard.task_instruction, analytical_text[:analytical_char_limit],
                len(analytical), rows, "seed_question", caller, task_state_map,
            )
            total_tokens += retry_tokens
            seed_rows.extend(retry_rows)
            parse_errors.extend(retry_errors)
        else:
            if error:
                parse_errors.append(error)
            seed_rows.extend(batch)

    for offset in range(0, len(completeness), max(batch_size, 1)):
        rows = [
            {"id": f"c{i + 1}", "text": str(c)}
            for i, c in enumerate(completeness[offset:offset + batch_size], offset)
        ]
        batch, tokens, error = _run_coverage_batch(
            blackboard.task_instruction, analytical_text[:analytical_char_limit],
            len(analytical), rows, "criterion", caller, task_state_map,
        )
        total_tokens += tokens
        if error and len(rows) > 1:
            retry_rows, retry_tokens, retry_errors = _retry_coverage_rows(
                blackboard.task_instruction, analytical_text[:analytical_char_limit],
                len(analytical), rows, "criterion", caller, task_state_map,
            )
            total_tokens += retry_tokens
            criteria_rows.extend(retry_rows)
            parse_errors.extend(retry_errors)
        else:
            if error:
                parse_errors.append(error)
            criteria_rows.extend(batch)

    lens_rows: list[dict] = []
    lens_items = _extract_lens_coverage_items(domain_lens)
    for offset in range(0, len(lens_items), max(batch_size, 1)):
        rows = [
            {"id": f"dl{i + 1}", "text": str(item)}
            for i, item in enumerate(lens_items[offset:offset + batch_size], offset)
        ]
        batch, tokens, error = _run_coverage_batch(
            blackboard.task_instruction, analytical_text[:analytical_char_limit],
            len(analytical), rows, "domain_lens_item", caller, task_state_map,
        )
        total_tokens += tokens
        if error and len(rows) > 1:
            retry_rows, retry_tokens, retry_errors = _retry_coverage_rows(
                blackboard.task_instruction, analytical_text[:analytical_char_limit],
                len(analytical), rows, "domain_lens_item", caller, task_state_map,
            )
            total_tokens += retry_tokens
            lens_rows.extend(retry_rows)
            parse_errors.extend(retry_errors)
        else:
            if error:
                parse_errors.append(error)
            lens_rows.extend(batch)

    return {
        "parse_error": bool(parse_errors),
        "error": "; ".join(parse_errors[:3]) if parse_errors else "",
        "errors": parse_errors,
        "seed_coverage": seed_rows,
        "criteria_coverage": criteria_rows,
        "lens_coverage": lens_rows,
    }, total_tokens


def run_plan_coverage_state_repair(
    blackboard: Blackboard,
    coverage_entries: list[Entry],
    caller: ModelCaller,
    *,
    max_gap_entries: int = 12,
    max_context_entries: int = 80,
    max_new_entries: int = 24,
) -> tuple[list[Entry], dict, int]:
    """Turn high/critical plan-coverage gaps into bounded repair state.

    This is a pre-obligation state repair pass. It does not draft final output.
    """
    selected = _select_repair_gaps(coverage_entries, max_gap_entries)
    if not selected:
        return [], {
            "selected_gaps": 0,
            "parse_error": False,
            "entries_created": 0,
            "entries_dropped": 0,
            "repaired_gap_ids": [],
            "missing_work_counts": {},
            "materiality_counts": {},
        }, 0

    active = [e for e in blackboard.entries if e.status == "active"]
    active_by_id = {e.id: e for e in active}
    active_ids = set(active_by_id)
    support_ids = []
    for gap in selected:
        for sid in gap.supports_entries:
            if sid in active_ids and sid not in support_ids:
                support_ids.append(sid)

    context = blackboard.get_entries_by_ids(support_ids)
    if len(context) < max_context_entries:
        selected_ids = {g.id for g in selected}
        for e in reversed(active):
            if e.id in selected_ids or e in context:
                continue
            if e.type in ("analysis", "calculation", "strategy", "gap"):
                context.append(e)
            if len(context) >= max_context_entries:
                break
    context = context[:max_context_entries]

    prompt = _build_plan_coverage_repair_prompt(
        task_instruction=blackboard.task_instruction,
        gaps=selected,
        context=context,
        max_new_entries=max_new_entries,
    )
    try:
        payload, tokens = call_model(caller, prompt, max_tokens=8192)
    except Exception as exc:
        return [], {
            "selected_gaps": len(selected),
            "parse_error": True,
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "entries_created": 0,
            "entries_dropped": 0,
            "repaired_gap_ids": [],
            "missing_work_counts": _tag_value_counts(selected, "missing_work:"),
            "materiality_counts": _tag_value_counts(selected, "materiality:"),
        }, 0

    raw_entries = payload.get("repair_entries", [])
    if not isinstance(raw_entries, list):
        raw_entries = []

    entries: list[Entry] = []
    dropped = 0
    repaired_gap_ids: list[str] = []
    for raw in raw_entries[:max_new_entries]:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content", "")).strip()
        if len(content) < 20:
            continue
        entry_type = str(raw.get("type", "analysis")).strip()
        if entry_type not in ("analysis", "calculation", "strategy", "gap"):
            entry_type = "analysis"

        supports = raw.get("supports_entries", [])
        if not isinstance(supports, list):
            supports = []
        valid_supports = [str(s) for s in supports if str(s) in active_ids]
        factual_supports = [
            sid for sid in valid_supports
            if active_by_id[sid].type != "gap"
        ]

        addressed = raw.get("addressed_gap_ids", [])
        if not isinstance(addressed, list):
            addressed = []
        valid_addressed = [str(s) for s in addressed if str(s) in {g.id for g in selected}]
        support_set = []
        for sid in valid_supports + valid_addressed:
            if sid not in support_set:
                support_set.append(sid)

        if entry_type != "gap" and not factual_supports:
            dropped += 1
            continue

        try:
            conf = min(max(float(raw.get("confidence", 0.78)), 0.0), 1.0)
        except (TypeError, ValueError):
            conf = 0.78

        repair_type = str(raw.get("repair_type", "state_repair")).strip()
        missing_work = str(raw.get("missing_work_type", "unknown")).strip()
        materiality = _highest_materiality_for_gaps(selected, valid_addressed)
        tags = [
            "plan_coverage_repair",
            f"repair_type:{repair_type}",
            f"missing_work:{missing_work}",
            f"materiality:{materiality}",
        ]

        for gid in valid_addressed:
            if gid not in repaired_gap_ids:
                repaired_gap_ids.append(gid)

        entries.append(Entry(
            id=gen_entry_id(), type=entry_type, content=content,
            created_by=WorkerRecord(
                "plan_coverage_repair", "pre_obligation_state_repair",
                blackboard.iteration,
            ),
            confidence=conf, tags=tags, status="active",
            supports_entries=support_set,
        ))

    report = {
        "selected_gaps": len(selected),
        "parse_error": bool(payload.get("parse_error")),
        "error": "",
        "entries_created": len(entries),
        "entries_dropped": dropped,
        "repaired_gap_ids": repaired_gap_ids,
        "missing_work_counts": _tag_value_counts(selected, "missing_work:"),
        "materiality_counts": _tag_value_counts(selected, "materiality:"),
    }
    return entries, report, tokens


def _run_coverage_batch(
    task_instruction: str,
    analytical_text: str,
    analytical_count: int,
    rows: list[dict],
    item_type: str,
    caller: ModelCaller,
    task_state_map: str = "None specified.",
) -> tuple[list[dict], int, str | None]:
    if not rows:
        return [], 0, None

    if item_type == "seed_question":
        status_values = "answered|partial|unanswered"
        satisfied_word = "answers"
    else:
        status_values = "satisfied|partial|unsatisfied"
        satisfied_word = "satisfies"

    items_block = "\n".join(f"{row['id']}. {row['text']}" for row in rows)
    prompt = f"""You are an adversarial coverage auditor. Evaluate only the listed {item_type} items against the blackboard analytical state.

Be STRICT. Mark an item covered ONLY when the blackboard contains the exact analytical conclusion, calculation, legal authority, severity rating, recommendation, or comparison required. Raw factual proximity is NOT enough.

TASK: {task_instruction}

TASK STATE MAP:
{task_state_map}

ITEMS:
{items_block}

ANALYTICAL STATE ({analytical_count} entries):
{analytical_text}

Return JSON only:
{{
  "coverage": [
    {{
      "id": "{rows[0]['id']}",
      "status": "{status_values}",
      "answer_summary": "specific state that {satisfied_word} the item, or empty",
      "evidence_summary": "short source-grounded support summary",
      "supporting_entries": ["e12", "e18"],
      "missing_reason": "what is still needed if partial/uncovered",
      "missing_work_type": "none|extract_more|calculate|compare|legal_authority|severity|recommendation|unanswerable",
      "materiality": "critical|high|medium|low"
    }}
  ]
}}

Return exactly one coverage row for every listed item. Do not include prose outside JSON."""

    try:
        payload, tokens = call_model(caller, prompt, max_tokens=4096)
    except Exception as exc:
        return [], 0, f"{type(exc).__name__}: {str(exc)[:500]}"

    if payload.get("parse_error"):
        return [], tokens, "parse_error"

    coverage = payload.get("coverage") or []
    if not isinstance(coverage, list):
        return [], tokens, "coverage_not_list"

    wanted_ids = {row["id"] for row in rows}
    clean_rows = []
    for row in coverage:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")) not in wanted_ids:
            continue
        clean_rows.append(row)
    return clean_rows, tokens, None


def _retry_coverage_rows(
    task_instruction: str,
    analytical_text: str,
    analytical_count: int,
    rows: list[dict],
    item_type: str,
    caller: ModelCaller,
    task_state_map: str = "None specified.",
) -> tuple[list[dict], int, list[str]]:
    """Retry a failed coverage batch one item at a time."""
    all_rows: list[dict] = []
    total_tokens = 0
    errors: list[str] = []
    for row in rows:
        batch, tokens, error = _run_coverage_batch(
            task_instruction, analytical_text, analytical_count,
            [row], item_type, caller, task_state_map,
        )
        total_tokens += tokens
        if error:
            errors.append(f"{row['id']}:{error}")
        all_rows.extend(batch)
    return all_rows, total_tokens, errors


def coverage_report_to_entries(
    seed: dict, report: dict, iteration: int,
    active_entries: list[Entry] | None = None,
) -> list[Entry]:
    """Materialize seed/criteria coverage judgments as substantive blackboard entries."""
    entries = []
    active_ids = {e.id for e in active_entries} if active_entries else set()

    for item in (report.get("seed_coverage") or []):
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id", ""))
        status = str(item.get("status", "unanswered"))
        supporting = [str(s) for s in item.get("supporting_entries", []) if isinstance(item.get("supporting_entries"), list)]
        if active_ids:
            supporting = [s for s in supporting if s in active_ids]
        answer_summary = str(item.get("answer_summary", ""))
        evidence_summary = str(item.get("evidence_summary", ""))
        missing_reason = str(item.get("missing_reason", ""))
        missing_work = str(item.get("missing_work_type", "none"))
        materiality = str(item.get("materiality", "high"))

        q_text = _resolve_seed_item(seed, "key_questions", qid)

        if status == "answered":
            content_parts = [f"Seed question {qid} answered: {q_text}"]
            if answer_summary:
                content_parts.append(f"Answer: {answer_summary}")
            if evidence_summary:
                content_parts.append(f"Evidence: {evidence_summary}")
            content = ". ".join(content_parts)

            if not answer_summary and not supporting:
                continue

            entries.append(Entry(
                id=gen_entry_id(), type="analysis",
                content=content,
                created_by=WorkerRecord("plan_coverage", "coverage_materialization", iteration),
                confidence=0.85, status="active",
                tags=["plan_coverage", f"seed_question:{qid}", "coverage:answered",
                      f"missing_work:{missing_work}", f"materiality:{materiality}"],
                supports_entries=supporting,
            ))
        else:
            content_parts = [f"Seed question {qid} {status}: {q_text}"]
            if missing_reason:
                content_parts.append(f"Missing: {missing_reason}")
            if evidence_summary:
                content_parts.append(f"Partial evidence: {evidence_summary}")
            content = ". ".join(content_parts)

            entries.append(Entry(
                id=gen_entry_id(), type="gap",
                content=content,
                created_by=WorkerRecord("plan_coverage", "coverage_materialization", iteration),
                confidence=0.9, status="active",
                tags=["plan_coverage", f"seed_question:{qid}", f"coverage:{status}",
                      f"missing_work:{missing_work}", f"materiality:{materiality}"],
                supports_entries=supporting,
            ))

    for item in (report.get("criteria_coverage") or []):
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id", ""))
        status = str(item.get("status", "unsatisfied"))
        supporting = [str(s) for s in item.get("supporting_entries", []) if isinstance(item.get("supporting_entries"), list)]
        if active_ids:
            supporting = [s for s in supporting if s in active_ids]
        answer_summary = str(item.get("answer_summary", ""))
        evidence_summary = str(item.get("evidence_summary", ""))
        missing_reason = str(item.get("missing_reason", ""))
        missing_work = str(item.get("missing_work_type", "none"))
        materiality = str(item.get("materiality", "high"))

        c_text = _resolve_seed_item(seed, "completeness_criteria", cid)

        if status == "satisfied":
            content_parts = [f"Completeness criterion {cid} satisfied: {c_text}"]
            if answer_summary:
                content_parts.append(f"Basis: {answer_summary}")
            if evidence_summary:
                content_parts.append(f"Evidence: {evidence_summary}")
            content = ". ".join(content_parts)

            if not answer_summary and not supporting:
                continue

            entries.append(Entry(
                id=gen_entry_id(), type="analysis",
                content=content,
                created_by=WorkerRecord("plan_coverage", "coverage_materialization", iteration),
                confidence=0.85, status="active",
                tags=["plan_coverage", f"criterion:{cid}", "coverage:satisfied",
                      f"missing_work:{missing_work}", f"materiality:{materiality}"],
                supports_entries=supporting,
            ))
        else:
            content_parts = [f"Completeness criterion {cid} {status}: {c_text}"]
            if missing_reason:
                content_parts.append(f"Missing: {missing_reason}")
            if evidence_summary:
                content_parts.append(f"Partial evidence: {evidence_summary}")
            content = ". ".join(content_parts)

            entries.append(Entry(
                id=gen_entry_id(), type="gap",
                content=content,
                created_by=WorkerRecord("plan_coverage", "coverage_materialization", iteration),
                confidence=0.9, status="active",
                tags=["plan_coverage", f"criterion:{cid}", f"coverage:{status}",
                      f"missing_work:{missing_work}", f"materiality:{materiality}"],
                supports_entries=supporting,
            ))

    for item in (report.get("lens_coverage") or []):
        if not isinstance(item, dict):
            continue
        lid = str(item.get("id", ""))
        status = str(item.get("status", "unsatisfied"))
        supporting = [str(s) for s in item.get("supporting_entries", []) if isinstance(item.get("supporting_entries"), list)]
        if active_ids:
            supporting = [s for s in supporting if s in active_ids]
        answer_summary = str(item.get("answer_summary", ""))
        evidence_summary = str(item.get("evidence_summary", ""))
        missing_reason = str(item.get("missing_reason", ""))
        missing_work = str(item.get("missing_work_type", "none"))
        materiality = str(item.get("materiality", "high"))

        if status == "satisfied":
            if not answer_summary and not supporting:
                continue
            content_parts = [f"Domain lens item {lid} satisfied"]
            if answer_summary:
                content_parts.append(f"Basis: {answer_summary}")
            entries.append(Entry(
                id=gen_entry_id(), type="analysis",
                content=". ".join(content_parts),
                created_by=WorkerRecord("plan_coverage", "lens_coverage", iteration),
                confidence=0.85, status="active",
                tags=["plan_coverage", "domain_lens", f"lens:{lid}", "coverage:satisfied"],
                supports_entries=supporting,
            ))
        else:
            content_parts = [f"Domain lens item {lid} {status}"]
            if missing_reason:
                content_parts.append(f"Missing: {missing_reason}")
            if evidence_summary:
                content_parts.append(f"Partial evidence: {evidence_summary}")
            entries.append(Entry(
                id=gen_entry_id(), type="gap",
                content=". ".join(content_parts),
                created_by=WorkerRecord("plan_coverage", "lens_coverage", iteration),
                confidence=0.9, status="active",
                tags=["plan_coverage", "domain_lens", f"lens:{lid}", f"coverage:{status}",
                      f"missing_work:{missing_work}", f"materiality:{materiality}"],
                supports_entries=supporting,
            ))

    if report.get("parse_error"):
        entries.append(Entry(
            id=gen_entry_id(), type="gap",
            content="Plan coverage review failed to parse model output. Coverage unknown.",
            created_by=WorkerRecord("plan_coverage", "coverage_materialization", iteration),
            confidence=1.0, status="active",
            tags=["plan_coverage", "coverage_parse_error"],
        ))

    return entries


def _select_repair_gaps(
    coverage_entries: list[Entry],
    max_gap_entries: int,
) -> list[Entry]:
    """Select the coverage gaps worth repairing before obligations."""
    state_repairable_work = {
        "calculate",
        "compare",
        "legal_authority",
        "severity",
        "recommendation",
    }
    selected = []
    for entry in coverage_entries:
        if entry.type != "gap":
            continue
        if "plan_coverage" not in (entry.tags or []):
            continue
        materiality = _tag_value(entry, "materiality:").lower()
        if materiality not in ("critical", "high"):
            continue
        missing_work = _tag_value(entry, "missing_work:").lower()
        if not missing_work or missing_work == "none":
            continue
        if missing_work not in state_repairable_work:
            continue
        selected.append(entry)

    materiality_rank = {"critical": 0, "high": 1}
    work_rank = {
        "calculate": 0,
        "compare": 1,
        "legal_authority": 2,
        "severity": 3,
        "recommendation": 4,
    }
    selected.sort(key=lambda e: (
        materiality_rank.get(_tag_value(e, "materiality:").lower(), 9),
        work_rank.get(_tag_value(e, "missing_work:").lower(), 9),
        e.id,
    ))
    return selected[:max(max_gap_entries, 0)]


def _highest_materiality_for_gaps(
    selected_gaps: list[Entry],
    gap_ids: list[str],
) -> str:
    by_id = {entry.id: entry for entry in selected_gaps}
    rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    best = "high"
    best_rank = rank[best]
    for gap_id in gap_ids:
        materiality = _tag_value(by_id.get(gap_id), "materiality:").lower()
        if rank.get(materiality, -1) > best_rank:
            best = materiality
            best_rank = rank[materiality]
    return best


def _tag_value_counts(entries: list[Entry], prefix: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        value = _tag_value(entry, prefix)
        if not value:
            value = "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _tag_value(entry: Entry | None, prefix: str) -> str:
    if entry is None:
        return ""
    for tag in entry.tags or []:
        if isinstance(tag, str) and tag.startswith(prefix):
            return tag[len(prefix):].strip()
    return ""


def _build_plan_coverage_repair_prompt(
    *,
    task_instruction: str,
    gaps: list[Entry],
    context: list[Entry],
    max_new_entries: int,
) -> str:
    gaps_payload = [
        {
            "id": entry.id,
            "type": entry.type,
            "content": entry.content,
            "tags": entry.tags,
            "supports_entries": entry.supports_entries,
        }
        for entry in gaps
    ]
    context_payload = [
        {
            "id": entry.id,
            "type": entry.type,
            "source": _source_tag(entry),
            "tags": entry.tags,
            "content": entry.content[:1200],
        }
        for entry in context
    ]

    return f"""You are repairing blackboard state before synthesis obligations. Do NOT write the final deliverable. Do NOT optimize wording. Your only job is to add missing analytical state that existing downstream stages can use.

TASK: {task_instruction}

SELECTED HIGH/CRITICAL COVERAGE GAPS:
{json.dumps(gaps_payload, ensure_ascii=True, indent=2)}

AVAILABLE BLACKBOARD CONTEXT:
{json.dumps(context_payload, ensure_ascii=True, indent=2)}

Rules:
- Use only the selected gaps and available blackboard context.
- Every non-gap repair entry must cite at least one factual existing support entry in supports_entries. addressed_gap_ids are useful for traceability, but they do not count as factual support.
- If the current state cannot support the missing work, create a gap entry explaining the remaining missing state instead of inventing facts.
- Prefer concrete calculations, comparisons, classifications, legal authority, severity assessments, recommendations, or artifact-form commitments.
- Do not include prose outside JSON.

Return JSON exactly in this shape:
{{
  "repair_entries": [
    {{
      "type": "analysis|calculation|strategy|gap",
      "content": "specific state repair, calculation, recommendation, or remaining gap",
      "supports_entries": ["e12", "e18"],
      "addressed_gap_ids": ["e33"],
      "repair_type": "calculation|comparison|classification|legal_authority|severity|recommendation|artifact_form|extraction_gap|unanswerable|state_repair",
      "missing_work_type": "extract_more|calculate|compare|legal_authority|severity|recommendation|unanswerable|state_repair",
      "confidence": 0.0
    }}
  ]
}}

Produce up to {max_new_entries} repair entries. Quality over quantity."""


def _resolve_seed_item(seed: dict, key: str, item_id: str) -> str:
    items = seed.get(key, [])
    try:
        idx = int(str(item_id).lstrip("qc")) - 1
        if 0 <= idx < len(items):
            return str(items[idx])
    except (ValueError, IndexError):
        pass
    return str(item_id)


def _source_tag(e: Entry) -> str:
    if e.source and e.source.document:
        tag = f"[{e.source.document}"
        if e.source.section:
            tag += f"/{e.source.section}"
        return tag + "]"
    return ""


def _build_state_conversion_prompt(
    *,
    task_instruction: str,
    framework: str,
    task_state_map: str,
    analytical: list[Entry],
    analytical_text: str,
    observations: list[Entry],
    max_new_entries: int,
    observations_label: str,
) -> str:
    obs_text = "\n".join(
        f"[{e.id}] ({e.type}) {_source_tag(e)} {e.content[:300]}"
        for e in observations
    )

    return f"""You are a senior analyst reviewing the blackboard state before synthesis. Your job is NOT to write the final deliverable — it is to ensure the blackboard contains enough analytical state for synthesis to succeed.

TASK: {task_instruction}

ANALYTICAL FRAMEWORK: {framework}

TASK STATE MAP:
{task_state_map}

EXISTING ANALYTICAL STATE ({len(analytical)} entries):
{analytical_text[:200000]}

RAW OBSERVATIONS ({len(observations)} {observations_label}):
{obs_text[:200000]}

YOUR JOB: Identify observations that should become analytical state but haven't been converted yet.

Look for these conversion opportunities:
1. UNPERFORMED CALCULATIONS: Raw numbers exist but no derived totals, percentages, deltas, or financial exposure
2. CROSS-DOCUMENT LINKS: Facts from different documents that relate but aren't connected
3. ISSUES WITHOUT RECOMMENDATIONS: Problems identified but no remediation suggested
4. RISKS WITHOUT SEVERITY: Risks noted but not rated or prioritized
5. LEGAL AUTHORITY NEEDED: Claims that need statutory/regulatory citations
6. UNANSWERED QUESTIONS: Important analytical questions the task implies but state doesn't answer
7. INCOMPLETE TASK-STATE ROWS: The task state map requires fields, relationships, or closure checks that are only partially populated
8. WRONG RELATION / CLASSIFICATION / DERIVED VALUE / ARTIFACT FORM: The blackboard has nearby facts but has not bound them into the exact relationship, classification, calculation, or deliverable shape the task requires

For each conversion, you MUST cite the source entry IDs that support it. Do NOT invent facts.

Return JSON:
{{
  "new_entries": [
    {{
      "type": "analysis|calculation|strategy|gap",
      "content": "the specific converted conclusion, calculation, recommendation, or gap",
      "source_entries": ["e12", "e18"],
      "conversion_type": "unperformed_calculation|cross_document_link|issue_without_recommendation|risk_without_severity|legal_authority_needed|seed_question_unanswered|criterion_without_obligation|task_state_row_incomplete|wrong_relation|wrong_classification|wrong_derived_value|wrong_artifact_form",
      "materiality": "low|medium|high|critical",
      "confidence": 0.0-1.0
    }}
  ]
}}

Produce up to {max_new_entries} new entries. Every non-gap entry MUST have valid source_entries. Quality over quantity."""


def _entry_batches(entries: list[Entry], batch_size: int) -> list[list[Entry]]:
    size = max(batch_size, 1)
    return [entries[i:i + size] for i in range(0, len(entries), size)]


def _extract_lens_coverage_items(lens: dict | None) -> list[str]:
    """Extract evaluable items from domain lens for coverage review."""
    if not lens:
        return []
    items: list[str] = []
    for hyp in lens.get("issue_hypotheses", []):
        if isinstance(hyp, str) and hyp.strip():
            items.append(f"Issue hypothesis: {hyp.strip()}")
    for auth in lens.get("legal_authorities", []):
        if isinstance(auth, dict):
            name = str(auth.get("authority", "")).strip()
            if name:
                items.append(f"Legal authority applicability: {name}")
    for calc in lens.get("calculation_targets", []):
        if isinstance(calc, dict):
            target = str(calc.get("target", "")).strip()
            if target:
                items.append(f"Calculation completed: {target}")
    for check in lens.get("negative_checks", []):
        if isinstance(check, str) and check.strip():
            items.append(f"Negative check verified: {check.strip()}")
    return items


def _source_balanced_sample(
    observations: list[Entry], max_count: int,
) -> list[Entry]:
    """Sample observations balanced by source document, deterministically."""
    if len(observations) <= max_count:
        return observations

    by_doc: dict[str, list[Entry]] = {}
    for e in observations:
        doc = e.source.document if e.source else "unknown"
        by_doc.setdefault(doc, []).append(e)

    per_doc = max(max_count // max(len(by_doc), 1), 10)
    sampled = []
    for doc_name in sorted(by_doc.keys()):
        doc_entries = by_doc[doc_name]
        if len(doc_entries) <= per_doc:
            sampled.extend(doc_entries)
        else:
            sampled.extend(doc_entries[:per_doc])

    if len(sampled) > max_count:
        sampled = sampled[:max_count]

    return sampled
