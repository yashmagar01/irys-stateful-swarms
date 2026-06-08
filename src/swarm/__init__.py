from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .blackboard import Blackboard
from .analysis import run_direct_analysis
from .artifact_commitments import build_artifact_commitments
from .blackboard_maintenance import (
    blackboard_maintenance_enabled,
    run_blackboard_maintenance,
)
from .convergence import check_convergence, supervisor_review
from .seed import generate_seed, seed_to_signals
from .domain_lens import (
    generate_domain_lens, lens_to_entries, lens_to_signals, format_lens_guidance,
)
from .curation import curate_entries
from .debt_sensors import debt_sensors_enabled, run_debt_sensors
from .derived_work import (
    calculation_debt_enabled,
    run_calculation_debt_detection,
)
from .obligations import build_synthesis_obligations
from .models import (
    Document, DocumentStatus, Entry, EntrySource, ModelCaller, Task,
    gen_entry_id, reset_id_counters,
)
from .orchestrator import run_orchestrator
from .section_index import build_section_index
from .signals import prioritize_signals
from .source_claims import (
    source_claim_verification_enabled,
    verify_source_claims,
)
from .source_custody import enforce_source_custody
from .state_conversion import (
    coverage_report_to_entries, run_plan_coverage_review,
    run_plan_coverage_state_repair, run_state_conversion_review,
)
from .synthesis import (
    shadow_judge_audit,
    shadow_judge_audit_enabled,
    synthesize_deliverable,
    synthesize_file_deliverables,
)
from .survival_trace import write_pending_survival_trace
from .worker_dispatch import (
    call_model, execute_workers_parallel, parse_worker_output,
    passes_quality_gate,
)


_STRUCTURED_SINGLE_FILE_EXTENSIONS = {
    ".xlsx", ".xls", ".csv", ".pptx", ".ppt",
}
_STRUCTURED_SINGLE_FILE_HINTS = (
    "matrix", "workbook", "tracker", "register", "deck", "presentation",
)
_DOCX_FILE_SCOPED_HINTS = ("redline", "redlined", "rider")
_DOCX_MARKUP_EXCLUSIONS = ("analysis", "memo", "report", "cover")


def _explicit_output_filenames(deliverables_map: dict) -> list[str]:
    filenames = []
    if not isinstance(deliverables_map, dict):
        return filenames
    for filename in deliverables_map.values():
        if isinstance(filename, str) and filename not in filenames:
            filenames.append(filename)
    return filenames


def _should_use_file_scoped_synthesis(deliverables_map: dict) -> bool:
    filenames = _explicit_output_filenames(deliverables_map)
    if len(filenames) > 1:
        return True
    if len(filenames) != 1:
        return False

    filename = filenames[0].lower()
    _, ext = os.path.splitext(filename)
    if ext in _STRUCTURED_SINGLE_FILE_EXTENSIONS:
        return True
    if ext == ".docx":
        stem = os.path.splitext(os.path.basename(filename))[0]
        if any(hint in stem for hint in _DOCX_FILE_SCOPED_HINTS):
            return True
        if "markup" in stem and not any(
            excluded in stem for excluded in _DOCX_MARKUP_EXCLUSIONS
        ):
            return True
        return False
    return any(hint in filename for hint in _STRUCTURED_SINGLE_FILE_HINTS)


def run_swarm(task: Task, caller: ModelCaller, *,
              synthesis_caller: ModelCaller | None = None,
              reviewer_caller: ModelCaller | None = None,
              token_budget: int | None = None,
              max_iterations: int | None = None,
              min_iterations: int | None = None) -> tuple[str | dict[str, str], Blackboard]:
    budget = token_budget or int(os.getenv("SWARM_TOKEN_BUDGET", "3000000"))
    max_iter = max_iterations or int(os.getenv("SWARM_MAX_ITERATIONS", "15"))
    min_iter = min_iterations or int(os.getenv("SWARM_MIN_ITERATIONS", "2"))
    synth_caller = synthesis_caller or caller
    review_caller = reviewer_caller

    reset_id_counters()

    # Phase 1: Initialize
    blackboard = Blackboard(
        task_instruction=task.instruction,
        documents=_build_doc_statuses(task.documents),
        token_budget=budget,
        started_at=datetime.now(timezone.utc).isoformat(),
        output_dir=task.output_dir,
    )

    # Phase 2: Structural profiling
    for doc in blackboard.documents:
        profile, tokens = _run_structural_profile(doc, task, caller)
        doc.structural_profile = profile
        blackboard.add_tokens_from_last_call(tokens)

    # Phase 3: seed task decomposition and analytical planning
    seed_plan = {}
    if review_caller is not None:
        seed_plan, seed_tokens = generate_seed(blackboard, review_caller)
        blackboard.add_tokens_from_last_call(seed_tokens)
        seed_to_signals(seed_plan, blackboard)
        # Put the analytical framework on the blackboard as a strategy entry
        framework = seed_plan.get("analytical_framework", "")
        if framework:
            from .models import WorkerRecord
            blackboard.add_entry(Entry(
                id=gen_entry_id(), type="strategy", content=framework,
                created_by=WorkerRecord("seed_planner", "analytical_framework", 0),
                confidence=0.9, status="active",
            ))
        for criterion in seed_plan.get("completeness_criteria", []):
            if isinstance(criterion, str) and criterion.strip():
                blackboard.add_entry(Entry(
                    id=gen_entry_id(), type="strategy",
                    content=f"COMPLETENESS CRITERION: {criterion}",
                    created_by=WorkerRecord("seed_planner", "completeness_criteria", 0),
                    confidence=0.9, status="active",
                ))
        blackboard.save_snapshot("seed")

    # Phase 3a: Domain Lens — professional-prior pseudo-criteria
    domain_lens = {}
    if review_caller is not None and seed_plan:
        domain_lens, lens_tokens = generate_domain_lens(
            blackboard, seed_plan, review_caller,
        )
        blackboard.add_tokens_from_last_call(lens_tokens)
        lens_entries = lens_to_entries(domain_lens, blackboard)
        if lens_entries:
            blackboard.add_entries_batch(lens_entries)
        lens_to_signals(domain_lens, blackboard)
        blackboard.save_snapshot("domain_lens")

    # Phase 3b: If no documents but web search is enabled, add a research signal
    from .web_search import web_search_enabled
    if not blackboard.documents and web_search_enabled():
        from .models import Signal, gen_signal_id
        blackboard.add_signal(Signal(
            id=gen_signal_id(), type="question",
            content=(
                "No source documents provided. Use web search to find "
                "information needed to answer the task. Break the question "
                "into specific search queries."
            ),
            origin_entry="bootstrap", priority="critical",
            status="open", iteration_created=0,
        ))

    # Phase 4: Initial reading (parallel per section)
    entries, tokens = _execute_initial_reading(blackboard, task, caller, seed_plan, domain_lens)
    blackboard.add_entries_batch(entries)
    blackboard.add_tokens(tokens)

    # Phase 4: Extraction depth check — auto re-extract under-covered documents
    for doc in blackboard.documents:
        if not doc.structural_profile:
            continue
        expected = doc.structural_profile.get("numbered_items", 0)
        if not isinstance(expected, (int, float)) or expected <= 0:
            continue
        actual = len([
            e for e in blackboard.entries
            if e.source and e.source.document == doc.name
            and e.status == "active"
            and e.type in ("observation", "analysis", "calculation")
        ])
        if actual < expected * 0.5:
            from .models import Signal, gen_signal_id
            blackboard.add_signal(Signal(
                id=gen_signal_id(), type="convergence_gap",
                content=(
                    f"Document '{doc.name}' has ~{int(expected)} enumerable items "
                    f"but only {actual} extracted. Need targeted re-extraction."
                ),
                origin_entry="extraction_depth_check", priority="critical",
                status="open", iteration_created=0,
            ))

    # Phase 5: Prioritize initial signals
    unp = [s for s in blackboard.signals if s.status == "open"]
    if unp:
        blackboard.add_tokens(prioritize_signals(blackboard, unp, caller))

    # Phase 6: Swarm loop
    for iteration in range(1, max_iter + 1):
        blackboard.iteration = iteration
        blackboard.expire_old_signals()
        blackboard.save_snapshot(f"pre_{iteration}")

        orch, orch_tokens = run_orchestrator(blackboard, caller)
        blackboard.add_tokens_from_last_call(orch_tokens)

        if orch.get("action") == "converge" and iteration >= min_iter:
            converged, conv_tokens = check_convergence(blackboard, orch, caller)
            blackboard.add_tokens_from_last_call(conv_tokens)
            if converged:
                blackboard.save_snapshot("converged")
                break
            orch, t = run_orchestrator(
                blackboard, caller,
                override="Convergence rejected. Address the gaps identified.",
            )
            blackboard.add_tokens_from_last_call(t)
            if orch.get("action") == "converge":
                converged2, t2 = check_convergence(blackboard, orch, caller)
                blackboard.add_tokens_from_last_call(t2)
                if converged2:
                    blackboard.save_snapshot("converged_retry")
                    break
                # Double convergence failure — force workers
                orch, t3 = run_orchestrator(
                    blackboard, caller,
                    override="You MUST produce workers. Do NOT converge. Find remaining gaps.",
                )
                blackboard.add_tokens_from_last_call(t3)

        tasks_list = orch.get("workers", [])
        if not tasks_list:
            continue

        outputs = execute_workers_parallel(tasks_list, blackboard, caller)

        new_entries = []
        for wo in outputs:
            blackboard.add_tokens(wo.tokens_used, wo.tokens_input, wo.tokens_output, wo.model)
            for e in wo.entries:
                if passes_quality_gate(e):
                    new_entries.append(e)
            for doc_name, sec_name in wo.sections_read:
                for ds in blackboard.documents:
                    if ds.name == doc_name:
                        ds.mark_section_read(sec_name)
        blackboard.add_entries_batch(new_entries)

        new_sigs = [
            s for s in blackboard.signals
            if s.status == "open" and s.iteration_created == iteration
        ]
        if new_sigs:
            blackboard.add_tokens(prioritize_signals(blackboard, new_sigs, caller))

        if blackboard.budget_used_pct() >= 85:
            blackboard.save_snapshot("budget_exhausted")
            break

        blackboard.save_snapshot(f"post_{iteration}")

    # Phase after extraction: direct analysis
    if review_caller is not None:
        analysis_entries, analysis_tokens = run_direct_analysis(
            blackboard, seed_plan, review_caller,
        )
        blackboard.add_entries_batch(analysis_entries)
        blackboard.add_tokens_from_last_call(analysis_tokens)
        blackboard.save_snapshot("post_analysis")

    # Phase 6: Supervisor review (smarter model, only if available)
    if review_caller is not None:
        for review_round in range(2):
            approved, gaps, rev_tokens = supervisor_review(blackboard, review_caller)
            blackboard.add_tokens_from_last_call(rev_tokens)
            if approved:
                blackboard.save_snapshot(f"supervisor_approved_{review_round}")
                break
            # Supervisor found gaps — add as critical signals and re-enter swarm
            from .models import Signal, gen_signal_id
            for gap in gaps:
                blackboard.add_signal(Signal(
                    id=gen_signal_id(), type="convergence_gap", content=gap,
                    origin_entry="supervisor_review", priority="critical",
                    status="open", iteration_created=blackboard.iteration,
                ))
            # Run a few more iterations to address supervisor's gaps
            for extra_iter in range(1, 4):
                blackboard.iteration += 1
                if blackboard.budget_used_pct() >= 90:
                    break
                orch, t = run_orchestrator(blackboard, caller)
                blackboard.add_tokens_from_last_call(t)
                if orch.get("action") == "converge":
                    break
                tasks_list = orch.get("workers", [])
                if not tasks_list:
                    continue
                outputs = execute_workers_parallel(tasks_list, blackboard, caller)
                new_entries = []
                for wo in outputs:
                    blackboard.add_tokens(wo.tokens_used, wo.tokens_input, wo.tokens_output, wo.model)
                    for e in wo.entries:
                        if passes_quality_gate(e):
                            new_entries.append(e)
                    for doc_name, sec_name in wo.sections_read:
                        for ds in blackboard.documents:
                            if ds.name == doc_name:
                                ds.mark_section_read(sec_name)
                                if " (part " in sec_name:
                                    parent = sec_name.split(" (part ")[0]
                                    ds.mark_section_read(parent)
                blackboard.add_entries_batch(new_entries)
            blackboard.save_snapshot(f"post_supervisor_{review_round}")

    # Phase 7a: State conversion review — convert observations into analytical state
    if review_caller is not None:
        sc_entries, sc_report, sc_tokens = run_state_conversion_review(
            blackboard, seed_plan, review_caller,
        )
        blackboard.add_entries_batch(sc_entries)
        blackboard.add_tokens_from_last_call(sc_tokens)

        # Phase 7b: Plan coverage review — adversarial seed/criteria coverage check
        cov_report, cov_tokens = run_plan_coverage_review(
            blackboard, seed_plan, review_caller,
            domain_lens=domain_lens,
        )
        blackboard.add_tokens_from_last_call(cov_tokens)

        # Materialize plan coverage as substantive blackboard entries
        active_now = [e for e in blackboard.entries if e.status == "active"]
        coverage_entries = coverage_report_to_entries(
            seed_plan, cov_report, blackboard.iteration,
            active_entries=active_now,
            domain_lens=domain_lens,
        )
        blackboard.add_entries_batch(coverage_entries)
        blackboard.save_snapshot("post_state_conversion")

        # Phase 7c: bounded pre-obligation state repair from high/critical
        # coverage gaps. This strengthens state before obligations rather than
        # patching final output.
        repair_entries, repair_report, repair_tokens = run_plan_coverage_state_repair(
            blackboard, coverage_entries, review_caller,
        )
        blackboard.add_entries_batch(repair_entries)
        if repair_tokens:
            blackboard.add_tokens_from_last_call(repair_tokens)

        # Persist reports for diagnostics
        if blackboard.output_dir:
            swarm_dir = os.path.join(blackboard.output_dir, "swarm")
            os.makedirs(swarm_dir, exist_ok=True)
            with open(os.path.join(swarm_dir, "state_conversion_review.json"), "w", encoding="utf-8") as f:
                json.dump(sc_report, f, indent=2)
            with open(os.path.join(swarm_dir, "plan_coverage_review.json"), "w", encoding="utf-8") as f:
                json.dump(cov_report, f, indent=2)
            with open(os.path.join(swarm_dir, "plan_coverage_repair.json"), "w", encoding="utf-8") as f:
                json.dump(repair_report, f, indent=2)

        blackboard.save_snapshot("post_state_repair")

    custody_report = enforce_source_custody(blackboard, "post_state_repair")
    if custody_report.get("items"):
        blackboard.save_snapshot("post_source_custody")

    if review_caller is not None and blackboard_maintenance_enabled():
        _, maintenance_tokens = run_blackboard_maintenance(
            blackboard, seed_plan, review_caller,
        )
        if maintenance_tokens:
            blackboard.add_tokens_from_last_call(maintenance_tokens)
        custody_report = enforce_source_custody(blackboard, "post_blackboard_maintenance")
        blackboard.save_snapshot("post_blackboard_maintenance")
        if custody_report.get("items"):
            blackboard.save_snapshot("post_blackboard_maintenance_source_custody")

    debt_sensor_report = None
    if review_caller is not None and debt_sensors_enabled():
        debt_sensor_report, debt_sensor_tokens = run_debt_sensors(
            blackboard, seed_plan, review_caller,
        )
        if debt_sensor_tokens:
            blackboard.add_tokens_from_last_call(debt_sensor_tokens)
        custody_report = enforce_source_custody(blackboard, "post_debt_sensors")
        blackboard.save_snapshot("post_debt_sensors")
        if custody_report.get("items"):
            blackboard.save_snapshot("post_debt_sensors_source_custody")

    derived_work_report = None
    if review_caller is not None and calculation_debt_enabled():
        derived_work_report, calc_debt_tokens = run_calculation_debt_detection(
            blackboard, seed_plan, review_caller,
        )
        if calc_debt_tokens:
            blackboard.add_tokens_from_last_call(calc_debt_tokens)
        custody_report = enforce_source_custody(blackboard, "post_calculation_debt_detection")
        blackboard.save_snapshot("post_calculation_debt_detection")
        if custody_report.get("items"):
            blackboard.save_snapshot("post_calculation_debt_source_custody")

    # Phase 7b: build synthesis obligations
    obligations = []
    if review_caller is not None:
        obligations, obl_tokens = build_synthesis_obligations(
            blackboard, seed_plan, review_caller,
        )
        blackboard.add_tokens_from_last_call(obl_tokens)
        blackboard.save_snapshot("post_obligations")

    # Phase 8: Curate + Combine obligations + Synthesize
    must_include, cur_tokens = curate_entries(blackboard, caller)
    blackboard.add_tokens_from_last_call(cur_tokens)

    # Merge obligations into must_include (obligations first, deduped)
    if obligations:
        seen = set()
        combined = []
        for o in obligations:
            key = (o.get("summary", "") if isinstance(o, dict) else str(o))[:60].lower().strip()
            if key not in seen:
                combined.append(o)
                seen.add(key)
        for m in must_include:
            key = (m.get("summary", "") if isinstance(m, dict) else str(m))[:60].lower().strip()
            if key not in seen:
                combined.append(m)
                seen.add(key)
        must_include = combined

    deliverables_map = task.metadata.get("deliverables", {})
    artifact_commitments = build_artifact_commitments(blackboard, deliverables_map)
    if artifact_commitments:
        seen = {
            (m.get("entry_id", ""), m.get("target_file", ""))
            for m in must_include if isinstance(m, dict)
        }
        for commitment in artifact_commitments:
            key = (commitment.get("entry_id", ""), commitment.get("target_file", ""))
            if key not in seen:
                must_include.insert(0, commitment)
                seen.add(key)

    if derived_work_report or debt_sensor_report:
        write_pending_survival_trace(
            blackboard.output_dir,
            derived_work_report,
            must_include,
            debt_sensor_report,
        )

    if _should_use_file_scoped_synthesis(deliverables_map):
        deliverable, synth_tokens = synthesize_file_deliverables(
            blackboard, must_include, deliverables_map, [], synth_caller,
        )
    else:
        deliverable, synth_tokens = synthesize_deliverable(
            blackboard, must_include, synth_caller,
        )
    blackboard.add_tokens_from_last_call(synth_tokens)

    if shadow_judge_audit_enabled() and review_caller is not None:
        deliverable, audit_tokens = shadow_judge_audit(
            deliverable, blackboard, seed_plan, review_caller,
        )
        blackboard.add_tokens_from_last_call(audit_tokens)

    if source_claim_verification_enabled():
        deliverable, claim_tokens, _ = verify_source_claims(
            deliverable, blackboard, synth_caller,
        )
        blackboard.add_tokens_from_last_call(claim_tokens)

    blackboard.save_snapshot("final")

    return deliverable, blackboard


def _build_doc_statuses(documents: list[Document]) -> list[DocumentStatus]:
    statuses = []
    for doc in documents:
        idx = build_section_index(doc.text)
        statuses.append(DocumentStatus(
            id=doc.id, name=doc.name, size_bytes=doc.size_bytes,
            headings=[s.name for s in idx.sections],
            sections_unread=[s.name for s in idx.sections],
            section_index=idx, text=doc.text,
        ))
    return statuses


def _run_structural_profile(doc: DocumentStatus, task: Task,
                            caller: ModelCaller) -> tuple[dict, int]:
    sample = (
        f"HEADINGS:\n{chr(10).join(doc.headings[:50])}\n\n"
        f"FIRST 2000 CHARS:\n{doc.text[:2000]}\n\n"
        f"LAST 500 CHARS:\n{doc.text[-500:]}"
    )
    prompt = f"""Examine this document's structure:
Document: {doc.name} ({doc.size_bytes} bytes)

{sample}

Report:
1. numbered_items: count of individually numbered/lettered items (e.g., clauses, requests, conditions)
2. tables: data table count
3. sections: major section count
4. document_type: contract|brief|report|filing|letter|exhibit|memo|agreement|amendment|schedule|other
5. key_entities: main parties/companies/persons (list)
6. estimated_complexity: simple|medium|complex

Return JSON with these fields."""
    payload, tokens = call_model(caller, prompt, max_tokens=1024)
    return payload, tokens


def _execute_initial_reading(blackboard: Blackboard, task: Task,
                             caller: ModelCaller,
                             seed_plan: dict | None = None,
                             domain_lens: dict | None = None) -> tuple[list[Entry], int]:
    CHUNK_SIZE = 24000
    CHUNK_OVERLAP = 2000

    read_tasks = []
    for ds in blackboard.documents:
        # Build density guidance from structural profile
        density_hint = ""
        if ds.structural_profile:
            n_items = ds.structural_profile.get("numbered_items", 0)
            if isinstance(n_items, (int, float)) and n_items > 0:
                density_hint = (
                    f"\nDENSITY GUIDANCE: This document contains approximately "
                    f"{int(n_items)} individually enumerable items. Extract ONE "
                    f"finding PER ITEM. Target at least {int(n_items)} findings "
                    f"from this document."
                )

        for section in ds.section_index.sections:
            if section.level > 2:
                continue
            text = ds.text[section.start_char:section.end_char]
            if len(text.strip()) < 50:
                continue
            if len(text) <= CHUNK_SIZE:
                read_tasks.append({
                    "doc_name": ds.name, "section_name": section.name,
                    "section_text": text, "density_hint": density_hint,
                    "seed_guidance": _initial_reading_seed_guidance(seed_plan, ds.name, domain_lens),
                })
            else:
                chunk_idx = 0
                offset = 0
                while offset < len(text):
                    chunk = text[offset:offset + CHUNK_SIZE]
                    if len(chunk.strip()) < 50:
                        break
                    chunk_idx += 1
                    read_tasks.append({
                        "doc_name": ds.name,
                        "section_name": f"{section.name} (part {chunk_idx})",
                        "section_text": chunk, "density_hint": density_hint,
                        "seed_guidance": _initial_reading_seed_guidance(seed_plan, ds.name, domain_lens),
                    })
                    offset += CHUNK_SIZE - CHUNK_OVERLAP

    all_entries: list[Entry] = []
    total_tokens = 0

    def read_one(rt):
        density_hint = rt.get("density_hint", "")
        seed_guidance = rt.get("seed_guidance", "")
        seed_block = ""
        if seed_guidance:
            seed_block = f"""
SEED-GUIDED INVESTIGATION LENS:
{seed_guidance}

Use this lens to decide which details are most material and which implicit
subquestions need evidence. Do not skip unrelated exact facts, terms, numbers,
dates, or provisions; the seed lens focuses extraction, it does not narrow it.
"""
        prompt = f"""Read this section of "{rt['doc_name']}" ({rt['section_name']}) and extract EVERY fact, term, and data point.

TASK: {task.instruction}
{density_hint}
{seed_block}

SOURCE:
{rt['section_text']}

EXTRACTION RULES — follow these EXACTLY:
1. Extract EVERY dollar amount, percentage, date, deadline, and time period
2. Extract EVERY party name with full legal entity designation (e.g., "Inc.", "AG", "LLC")
3. Extract EVERY numbered or lettered item in any list, schedule, or exhibit — EACH ONE SEPARATELY
4. Extract EVERY defined term and its definition
5. Extract EVERY obligation, condition, requirement, or restriction
6. Extract EVERY payment term: upfront amounts, milestones, royalties, equity investments
7. If a table exists, extract EACH ROW as a separate finding
8. Do NOT summarize — if the text says "$75,000,000 upfront payment due within 30 days", that is ONE finding with the exact amount and timeline
9. Aim for 20-50 findings per section. If you have fewer than 10, you are likely summarizing instead of enumerating.

For each finding:
- content: the specific fact with EXACT numbers, names, dates
- type: observation (for facts), calculation (for numbers), analysis (for implications)
- confidence: 0.9 for directly quoted facts, 0.7 for inferences
- epistemic_classification: fact | adversarial_claim | expert_opinion | strategic

Return JSON: {{"findings": [...]}}"""
        payload, tokens = call_model(caller, prompt, max_tokens=8192)
        entries = parse_worker_output(
            payload, 0, f"reader_{rt['doc_name'][:20]}", "initial_reading",
        )
        # Backfill source on entries that lack it — we KNOW the doc and section
        for e in entries:
            if not e.source or not e.source.document:
                e.source = EntrySource(
                    document=rt["doc_name"],
                    section=rt["section_name"],
                    evidence=e.source.evidence if e.source else "",
                )
        return entries, tokens, rt["doc_name"], rt["section_name"]

    max_w = min(len(read_tasks), 10)
    if max_w > 0:
        with ThreadPoolExecutor(max_workers=max_w) as pool:
            futures = [pool.submit(read_one, rt) for rt in read_tasks]
            for f in futures:
                entries, tokens, doc_name, sec_name = f.result()
                all_entries.extend(entries)
                total_tokens += tokens
                for ds in blackboard.documents:
                    if ds.name == doc_name:
                        ds.mark_section_read(sec_name)
                        # Also mark parent section for chunked reads
                        if " (part " in sec_name:
                            parent = sec_name.split(" (part ")[0]
                            ds.mark_section_read(parent)

    return all_entries, total_tokens


def _initial_reading_seed_guidance(seed_plan: dict | None, doc_name: str,
                                   lens: dict | None = None) -> str:
    """Format seed plan + domain lens as guidance for first-pass readers."""
    if not isinstance(seed_plan, dict) or not seed_plan:
        return ""

    parts: list[str] = []

    questions = [
        str(q).strip()
        for q in seed_plan.get("key_questions", [])
        if isinstance(q, str) and q.strip()
    ]
    if questions:
        parts.append("Key questions:\n" + "\n".join(f"- {q}" for q in questions))

    doc_focus = []
    for focus in seed_plan.get("extraction_focus", []):
        if not isinstance(focus, dict):
            continue
        target_doc = str(focus.get("document", "")).strip()
        text = str(focus.get("focus", "")).strip()
        if not target_doc or not text:
            continue
        target_l = target_doc.lower()
        doc_l = doc_name.lower()
        if target_l in doc_l or doc_l in target_l or target_l in {"all", "all documents"}:
            doc_focus.append(text)
    if doc_focus:
        parts.append(
            f"Document-specific focus for {doc_name}:\n"
            + "\n".join(f"- {focus}" for focus in doc_focus)
        )

    framework = str(seed_plan.get("analytical_framework", "")).strip()
    if framework:
        parts.append("Analytical framework:\n" + framework)

    context = seed_plan.get("context_enrichment", "")
    if isinstance(context, dict):
        context_text = json.dumps(context, ensure_ascii=True)
    else:
        context_text = str(context).strip()
    if context_text:
        parts.append("Context enrichment notes:\n" + context_text)

    criteria = [
        str(c).strip()
        for c in seed_plan.get("completeness_criteria", [])
        if isinstance(c, str) and c.strip()
    ]
    if criteria:
        parts.append(
            "Completeness signals:\n"
            + "\n".join(f"- {criterion}" for criterion in criteria)
        )

    if lens:
        lens_text = format_lens_guidance(lens)
        if lens_text:
            parts.append(lens_text)

    return "\n\n".join(parts)
