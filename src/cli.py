from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .runner import RunResult, run_single_task


def main():
    parser = argparse.ArgumentParser(description="ant-irys: swarm document analysis")
    sub = parser.add_subparsers(dest="command")

    # Single task
    run_p = sub.add_parser("run", help="Run a single task")
    run_p.add_argument("task_dir", type=Path, help="Path to task directory")
    run_p.add_argument("--output", "-o", type=Path, default=Path("results"),
                       help="Output directory")
    run_p.add_argument("--worker-model", default=None)
    run_p.add_argument("--synthesis-model", default=None)

    # Batch from manifest
    batch_p = sub.add_parser("batch", help="Run batch from manifest")
    batch_p.add_argument("manifest", type=Path, help="Path to manifest JSON")
    batch_p.add_argument("--output", "-o", type=Path, default=Path("results"),
                         help="Output directory")
    batch_p.add_argument("--concurrency", "-j", type=int, default=48)
    batch_p.add_argument("--worker-model", default=None)
    batch_p.add_argument("--synthesis-model", default=None)

    # Generate manifest
    manifest_p = sub.add_parser("manifest", help="Generate a randomized manifest")
    manifest_p.add_argument("--bench-root", type=Path, default=None,
                            help="Harvey LAB benchmark root")
    manifest_p.add_argument("--per-family", type=int, default=1,
                            help="Tasks per practice area")
    manifest_p.add_argument("--output", "-o", type=Path, default=None)
    manifest_p.add_argument("--seed", type=int, default=None,
                            help="Random seed (default: random)")

    # Score batch
    score_p = sub.add_parser("score", help="Score a batch run")
    score_p.add_argument("results_dir", type=Path, help="Results directory")
    score_p.add_argument("--bench-root", type=Path, default=None)
    score_p.add_argument("--judge-model", default="gemini-3.1-flash-lite")
    score_p.add_argument("--concurrency", "-j", type=int, default=20,
                         help="Criteria parallelism per task")
    score_p.add_argument("--task-concurrency", type=int, default=5,
                         help="Number of tasks scored simultaneously")

    # Analyze results
    analyze_p = sub.add_parser("analyze", help="Analyze scored results")
    analyze_p.add_argument("results_dir", type=Path, help="Results directory")

    # Summarize derived-work sidecars
    derived_p = sub.add_parser(
        "summarize-derived-work",
        help="Aggregate derived-work reports for a batch run",
    )
    derived_p.add_argument("results_dir", type=Path, help="Results directory")

    # Summarize reasoning lifecycle sidecars
    lifecycle_p = sub.add_parser(
        "summarize-lifecycle",
        help="Aggregate swarm lifecycle reports for a run",
    )
    lifecycle_p.add_argument("results_dir", type=Path, help="Results directory")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        _cmd_run(args)
    elif args.command == "batch":
        _cmd_batch(args)
    elif args.command == "manifest":
        _cmd_manifest(args)
    elif args.command == "score":
        _cmd_score(args)
    elif args.command == "analyze":
        _cmd_analyze(args)
    elif args.command == "summarize-derived-work":
        _cmd_summarize_derived_work(args)
    elif args.command == "summarize-lifecycle":
        _cmd_summarize_lifecycle(args)


def _cmd_run(args):
    result = run_single_task(
        args.task_dir, args.output,
        worker_model=args.worker_model,
        synthesis_model=args.synthesis_model,
    )
    if result.error:
        print(f"FAILED: {result.error}")
        sys.exit(1)
    print(f"OK: {result.task_id} ({result.tokens_used} tokens, "
          f"{result.wall_clock_seconds:.1f}s)")
    print(f"Files: {', '.join(result.deliverable_files)}")


def _cmd_batch(args):
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    bench_root = Path(manifest.get("bench_root", ""))
    tasks = manifest.get("tasks", [])

    print(f"Batch: {len(tasks)} tasks, concurrency {args.concurrency}")

    valid = []
    for t in tasks:
        task_dir = bench_root / "tasks" / t["task_id"]
        if not (task_dir / "task.json").exists():
            print(f"  SKIP (missing): {t['task_id']}")
            continue
        valid.append((t["task_id"], task_dir))

    print(f"Valid: {len(valid)}/{len(tasks)}")
    if not valid:
        print("No valid tasks found!")
        sys.exit(1)

    # Filter out already-completed tasks (resume support)
    to_run = []
    skipped = 0
    for task_id, task_dir in valid:
        out_status = args.output / task_id.replace("/", os.sep) / "status.json"
        if out_status.exists():
            try:
                s = json.loads(out_status.read_text(encoding="utf-8"))
                if s.get("status") == "completed":
                    skipped += 1
                    continue
            except Exception:
                pass
        to_run.append((task_id, task_dir))

    if skipped:
        print(f"Skipped {skipped} already-completed tasks")
    print(f"Running {len(to_run)} tasks with concurrency {args.concurrency}")

    completed = skipped
    failed = 0
    t0 = time.time()

    def _run_one(item):
        task_id, task_dir = item
        return run_single_task(
            task_dir, args.output,
            worker_model=args.worker_model,
            synthesis_model=args.synthesis_model,
            task_id=task_id,
        )

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_to_task = {
            pool.submit(_run_one, item): item[0]
            for item in to_run
        }
        for future in as_completed(future_to_task):
            task_id = future_to_task[future]
            try:
                result = future.result()
            except Exception as e:
                print(f"  FAILED: {task_id} — {e}")
                failed += 1
                continue
            if result.error:
                print(f"  FAILED: {task_id} — {result.error}")
                failed += 1
            else:
                completed += 1
                done = completed + failed - skipped
                total = len(to_run)
                print(f"  [{done}/{total}] {task_id} — "
                      f"{result.tokens_used:,} tok, {result.wall_clock_seconds:.0f}s")

    elapsed = time.time() - t0
    print(f"\nDone: {completed} completed, {failed} failed in {elapsed:.0f}s")


def _cmd_manifest(args):
    bench_root_env = os.getenv("HARVEY_BENCH_ROOT")
    if args.bench_root:
        bench_root = args.bench_root
    elif bench_root_env:
        bench_root = Path(bench_root_env)
    else:
        print("Error: HARVEY_BENCH_ROOT environment variable or --bench-root argument required")
        sys.exit(1)
    tasks_root = bench_root / "tasks"
    if not tasks_root.is_dir():
        print(f"Tasks directory not found: {tasks_root}")
        sys.exit(1)

    families: dict[str, list[str]] = {}
    for family_dir in sorted(tasks_root.iterdir()):
        if not family_dir.is_dir() or family_dir.name.startswith("."):
            continue
        task_ids = []
        for task_dir in sorted(family_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            if (task_dir / "task.json").exists():
                task_ids.append(f"{family_dir.name}/{task_dir.name}")
            else:
                # Check for scenario subdirectories
                for scenario_dir in sorted(task_dir.iterdir()):
                    if scenario_dir.is_dir() and (scenario_dir / "task.json").exists():
                        task_ids.append(
                            f"{family_dir.name}/{task_dir.name}/{scenario_dir.name}"
                        )
        if task_ids:
            families[family_dir.name] = task_ids

    seed = args.seed if args.seed is not None else random.randint(0, 2**32 - 1)
    rng = random.Random(seed)

    selected = []
    for family, task_ids in sorted(families.items()):
        sample = rng.sample(task_ids, min(args.per_family, len(task_ids)))
        selected.extend(sample)

    manifest = {
        "bench_root": str(bench_root),
        "seed": seed,
        "per_family": args.per_family,
        "task_count": len(selected),
        "family_count": len(families),
        "tasks": [{"task_id": tid} for tid in selected],
    }

    if args.output:
        out = args.output
    else:
        out = Path(f"benchmarks/manifests/smoke_{args.per_family}x{len(families)}_{seed}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Generated: {out} ({len(selected)} tasks from {len(families)} families, seed={seed})")


def _cmd_score(args):
    bench_root_env = os.getenv("HARVEY_BENCH_ROOT")
    if args.bench_root:
        bench_root = args.bench_root
    elif bench_root_env:
        bench_root = Path(bench_root_env)
    else:
        print("Error: HARVEY_BENCH_ROOT environment variable or --bench-root argument required")
        sys.exit(1)
    sys.path.insert(0, str(bench_root))

    try:
        from evaluation.judge import Judge
        from evaluation.scoring import score_rubric
    except ImportError as e:
        print(f"Cannot import Harvey LAB evaluation: {e}")
        print("Make sure HARVEY_BENCH_ROOT points to the harvey-labs directory")
        sys.exit(1)

    judge = Judge(model=args.judge_model)

    run_dirs = []
    for root, dirs, files in os.walk(args.results_dir):
        root_path = Path(root)
        if "output" in dirs and (root_path / "status.json").exists():
            status = json.loads((root_path / "status.json").read_text(encoding="utf-8"))
            if status.get("status") == "completed":
                scores_path = root_path / "scores.json"
                if not scores_path.exists():
                    run_dirs.append(root_path)

    print(f"Scoring {len(run_dirs)} tasks with {args.judge_model}")

    def _score_one(run_dir):
        task_id = _extract_task_id(run_dir, args.results_dir)
        task_json_path = bench_root / "tasks" / task_id / "task.json"
        if not task_json_path.exists():
            return task_id, None, f"no task.json"

        task_data = json.loads(task_json_path.read_text(encoding="utf-8"))
        criteria = task_data.get("criteria", [])
        task_desc = task_data.get("title", task_id)

        try:
            result = score_rubric(
                criteria=criteria,
                run_dir=run_dir,
                judge=judge,
                task_desc=task_desc,
                parallel=args.concurrency,
            )
            scores = {
                "score": result.score,
                "max_score": result.max_score,
                "all_pass": result.score == result.max_score,
                "n_criteria": len(criteria),
                "n_passed": sum(
                    1 for cr in result.criteria_results
                    if cr.get("verdict") == "pass"
                ),
                "criteria_results": result.criteria_results,
                "run_id": task_id,
                "task": task_id,
                "judge_model": args.judge_model,
            }
            (run_dir / "scores.json").write_text(
                json.dumps(scores, indent=2, default=str), encoding="utf-8",
            )
            return task_id, scores, None
        except Exception as e:
            return task_id, None, str(e)

    scored = 0
    failed = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=args.task_concurrency) as pool:
        future_to_dir = {pool.submit(_score_one, rd): rd for rd in run_dirs}
        for future in as_completed(future_to_dir):
            task_id, scores, error = future.result()
            if error:
                print(f"  ERROR: {task_id} — {error}")
                failed += 1
            elif scores:
                scored += 1
                print(f"  [{scored}/{len(run_dirs)}] {task_id}: "
                      f"{scores['n_passed']}/{scores['n_criteria']} criteria passed")

    print(f"\nScored {scored}/{len(run_dirs)} tasks ({failed} errors)")


def _cmd_analyze(args):
    results_dir = args.results_dir
    all_scores = []
    for root, dirs, files in os.walk(results_dir):
        if "scores.json" in files:
            scores = json.loads(
                (Path(root) / "scores.json").read_text(encoding="utf-8")
            )
            all_scores.append(scores)

    if not all_scores:
        print("No scored results found")
        sys.exit(1)

    total_criteria = sum(s.get("n_criteria", 0) for s in all_scores)
    total_passed = sum(s.get("n_passed", 0) for s in all_scores)
    full_pass = sum(1 for s in all_scores if s.get("all_pass", False))

    criteria_rate = total_passed / max(total_criteria, 1) * 100
    full_pass_rate = full_pass / len(all_scores) * 100

    print(f"=== Results: {len(all_scores)} tasks ===")
    print(f"Criteria pass rate: {criteria_rate:.1f}% ({total_passed}/{total_criteria})")
    print(f"Full pass rate: {full_pass_rate:.1f}% ({full_pass}/{len(all_scores)})")
    print()

    by_family: dict[str, list] = {}
    for s in all_scores:
        task = s.get("task", "")
        family = task.split("/")[0] if "/" in task else "unknown"
        by_family.setdefault(family, []).append(s)

    print("Per-family breakdown:")
    for family in sorted(by_family):
        scores = by_family[family]
        f_criteria = sum(s.get("n_criteria", 0) for s in scores)
        f_passed = sum(s.get("n_passed", 0) for s in scores)
        f_rate = f_passed / max(f_criteria, 1) * 100
        f_full = sum(1 for s in scores if s.get("all_pass", False))
        print(f"  {family}: {f_rate:.1f}% criteria ({f_passed}/{f_criteria}), "
              f"{f_full}/{len(scores)} full pass")

    print("\nFailed tasks (criteria < 100%):")
    failures = [
        s for s in all_scores
        if not s.get("all_pass", False)
    ]
    failures.sort(key=lambda s: s.get("n_passed", 0) / max(s.get("n_criteria", 1), 1))
    for s in failures[:20]:
        task = s.get("task", "unknown")
        n_passed = s.get("n_passed", 0)
        n_criteria = s.get("n_criteria", 0)
        rate = n_passed / max(n_criteria, 1) * 100
        print(f"  {task}: {rate:.1f}% ({n_passed}/{n_criteria})")
        failed_criteria = [
            cr for cr in s.get("criteria_results", [])
            if cr.get("verdict") == "fail"
        ]
        for cr in failed_criteria[:5]:
            print(f"    FAIL: {cr.get('title', 'unknown')}")
            reasoning = cr.get("reasoning", "")
            if reasoning:
                print(f"          {reasoning[:200]}")


def _cmd_summarize_derived_work(args):
    from .swarm.derived_work import aggregate_derived_work_reports

    summary = aggregate_derived_work_reports(args.results_dir)
    print(f"Derived work tasks: {summary['tasks']}")
    print(
        "Selected/executable/executed: "
        f"{summary['selected']}/{summary['executable']}/{summary['executed']}"
    )
    print(f"Entries created: {summary['entries_created']}")
    print(
        "Forbidden provenance hits: "
        f"{summary['contamination_audit']['forbidden_provenance_hits']}"
    )


def _cmd_summarize_lifecycle(args):
    from .swarm.lifecycle_summary import aggregate_lifecycle_reports

    summary = aggregate_lifecycle_reports(args.results_dir)
    reports = summary["reports"]
    debt = reports["debt_sensors"]
    placement = reports["artifact_placement"]
    audit = reports["prompt_audit"]
    maintenance = reports["blackboard_maintenance"]
    source_claims = reports.get("source_claim_verification", {})
    print(f"Lifecycle tasks: {summary['tasks']}")
    print(
        "Debt selected/actionable/unresolved: "
        f"{debt['selected']}/{debt['actionable']}/{debt['unresolved_actionable']}"
    )
    print(
        "Lens coordinator selected/deferred: "
        f"{debt.get('coordinator_selected', 0)}/"
        f"{debt.get('coordinator_deferred', 0)}"
    )
    print(
        "Artifact placement traceable/found/native/lost: "
        f"{placement.get('traceable', 0)}/"
        f"{placement['found_in_target_file']}/"
        f"{placement.get('native_form_satisfied', 0)}/"
        f"{placement['lost']}"
    )
    print(
        "Maintenance consolidations/entries: "
        f"{maintenance['consolidations_selected']}/{maintenance['entries_created']}"
    )
    print(
        "Prompt audit records/forbidden hits: "
        f"{audit['records']}/"
        f"{audit['forbidden_provenance_hits'] + audit['forbidden_text_hits']}"
    )
    print(
        "Source claims checked/risky: "
        f"{source_claims.get('claims_checked', 0)}/"
        f"{source_claims.get('risky_claims', 0)}"
    )


def _extract_task_id(run_dir: Path, results_root: Path) -> str:
    try:
        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        if "task_id" in status:
            return status["task_id"]
    except Exception:
        pass
    rel = run_dir.relative_to(results_root)
    return str(rel).replace("\\", "/")


if __name__ == "__main__":
    main()
