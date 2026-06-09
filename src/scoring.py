"""Scorer abstraction for benchmark evaluation.

Provides a protocol-based scoring layer that decouples task evaluation
from any specific benchmark provider (Harvey LAB, LLM-as-judge, etc.).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class ScoreResult:
    score: float
    max_score: float
    all_pass: bool
    n_criteria: int
    n_passed: int
    criteria_results: list[dict]
    scorer_type: str
    judge_model: str | None = None


@runtime_checkable
class Scorer(Protocol):
    def score_task(self, task_data: dict, run_dir: Path, **kwargs) -> ScoreResult: ...


# ---------------------------------------------------------------------------
# TaskResolver — shared task/source resolution for batch + score commands
# ---------------------------------------------------------------------------

@dataclass
class TaskSource:
    name: str
    type: str  # "harvey_lab" | "local"
    root: Path
    default_scorer: str | None = None


@dataclass
class ResolvedTask:
    task_id: str
    task_dir: Path
    source: TaskSource
    scorer_name: str


class TaskResolver:
    """Resolves task IDs to directories and scorer types from a manifest."""

    def __init__(self, manifest: dict):
        self._sources: dict[str, TaskSource] = {}
        self._manifest = manifest

        if "sources" in manifest:
            for src in manifest["sources"]:
                root = Path(os.path.expandvars(src["root"]))
                self._sources[src["name"]] = TaskSource(
                    name=src["name"],
                    type=src["type"],
                    root=root,
                    default_scorer=src.get("default_scorer"),
                )
        elif "bench_root" in manifest:
            root = Path(os.path.expandvars(manifest["bench_root"]))
            self._sources["harvey_lab"] = TaskSource(
                name="harvey_lab",
                type="harvey_lab",
                root=root,
                default_scorer="harvey",
            )

    @property
    def sources(self) -> dict[str, TaskSource]:
        return dict(self._sources)

    def resolve(self, task_entry: dict) -> ResolvedTask:
        task_id = task_entry["task_id"]
        source_name = task_entry.get("source")

        if source_name:
            source = self._sources.get(source_name)
            if not source:
                raise ValueError(f"Unknown source '{source_name}' for task {task_id}")
        elif len(self._sources) == 1:
            source = next(iter(self._sources.values()))
        else:
            raise ValueError(
                f"Task {task_id} has no 'source' field and manifest has "
                f"multiple sources: {list(self._sources.keys())}"
            )

        task_dir = source.root / "tasks" / task_id
        scorer_name = self._resolve_scorer(task_entry, task_dir, source)
        return ResolvedTask(
            task_id=task_id,
            task_dir=task_dir,
            source=source,
            scorer_name=scorer_name,
        )

    def _resolve_scorer(
        self, task_entry: dict, task_dir: Path, source: TaskSource,
    ) -> str:
        task_json_path = task_dir / "task.json"
        if task_json_path.exists():
            task_data = json.loads(task_json_path.read_text(encoding="utf-8-sig"))
            if "scorer" in task_data:
                return task_data["scorer"]

        if source.default_scorer:
            return source.default_scorer

        if source.type == "harvey_lab":
            return "harvey"

        raise ValueError(
            f"Task {task_entry['task_id']}: no scorer specified in task.json, "
            f"no default_scorer on source '{source.name}', and source type "
            f"'{source.type}' has no implicit default. "
            f"Set 'scorer' in task.json or 'default_scorer' on the manifest source."
        )


# ---------------------------------------------------------------------------
# Manifest loading helpers
# ---------------------------------------------------------------------------

def load_manifest_for_scoring(
    results_dir: Path,
    manifest_override: Path | None = None,
    bench_root_override: Path | None = None,
) -> dict:
    """Load manifest with the Codex-approved priority order:
    1. --manifest CLI flag (explicit override)
    2. results_dir/manifest.json (persisted by batch)
    3. --bench-root / HARVEY_BENCH_ROOT (legacy fallback)
    4. Error
    """
    if manifest_override and manifest_override.exists():
        return json.loads(manifest_override.read_text(encoding="utf-8-sig"))

    persisted = results_dir / "manifest.json"
    if persisted.exists():
        return json.loads(persisted.read_text(encoding="utf-8-sig"))

    bench_root = bench_root_override
    if not bench_root:
        env = os.getenv("HARVEY_BENCH_ROOT")
        if env:
            bench_root = Path(env)

    if bench_root:
        return {"bench_root": str(bench_root)}

    raise RuntimeError(
        "Cannot determine scoring context. Provide --manifest, "
        "ensure results_dir has manifest.json (written by batch), "
        "or set HARVEY_BENCH_ROOT."
    )


# ---------------------------------------------------------------------------
# Scorer implementations
# ---------------------------------------------------------------------------

class HarveyLabScorer:
    """Wraps Harvey LAB's evaluation.judge.Judge + evaluation.scoring.score_rubric."""

    def __init__(self, bench_root: Path, judge_model: str = "gemini-3.1-flash-lite"):
        self._bench_root = bench_root
        self._judge_model = judge_model
        sys.path.insert(0, str(bench_root))

    def score_task(self, task_data: dict, run_dir: Path, **kwargs) -> ScoreResult:
        from evaluation.judge import Judge
        from evaluation.scoring import score_rubric

        judge = Judge(model=self._judge_model)
        criteria = task_data.get("criteria", [])
        task_desc = task_data.get("title", "")
        concurrency = kwargs.get("concurrency", 1)

        result = score_rubric(
            criteria=criteria,
            run_dir=run_dir,
            judge=judge,
            task_desc=task_desc,
            parallel=concurrency,
        )

        criteria_results = result.criteria_results
        n_passed = sum(
            1 for cr in criteria_results if cr.get("verdict") == "pass"
        )

        return ScoreResult(
            score=result.score,
            max_score=result.max_score,
            all_pass=result.score == result.max_score,
            n_criteria=len(criteria),
            n_passed=n_passed,
            criteria_results=criteria_results,
            scorer_type="harvey",
            judge_model=self._judge_model,
        )


class LLMJudgeScorer:
    """LLM-as-judge scorer using criteria from task.json."""

    def __init__(self, judge_model: str = "gemini-3.1-flash-lite"):
        self._judge_model = judge_model

    def score_task(self, task_data: dict, run_dir: Path, **kwargs) -> ScoreResult:
        criteria = task_data.get("criteria", [])
        if not criteria:
            raise ValueError(
                "LLMJudgeScorer requires 'criteria' in task.json. "
                "For ad-hoc tasks without criteria, use sub-agent evaluation instead."
            )

        output_dir = run_dir / "output"
        if not output_dir.exists():
            return ScoreResult(
                score=0, max_score=len(criteria), all_pass=False,
                n_criteria=len(criteria), n_passed=0,
                criteria_results=[
                    {"title": c.get("title", f"criterion_{i}"),
                     "verdict": "fail",
                     "reasoning": "No output directory found"}
                    for i, c in enumerate(criteria)
                ],
                scorer_type="llm_judge",
                judge_model=self._judge_model,
            )

        output_files = list(output_dir.iterdir()) if output_dir.exists() else []
        output_text = ""
        for f in output_files:
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext == ".docx":
                try:
                    from docx import Document as DocxDoc
                    doc = DocxDoc(str(f))
                    text = "\n".join(p.text for p in doc.paragraphs)
                    output_text += f"\n--- {f.name} ---\n{text}"
                except Exception:
                    output_text += f"\n[Could not read {f.name}]\n"
            elif ext in (".txt", ".md", ".json"):
                try:
                    output_text += f"\n--- {f.name} ---\n"
                    output_text += f.read_text(encoding="utf-8-sig", errors="replace")
                except Exception:
                    output_text += f"\n[Could not read {f.name}]\n"

        from .providers.gemini import GeminiCaller
        caller = GeminiCaller(model=self._judge_model)

        criteria_results = []
        n_passed = 0
        for criterion in criteria:
            title = criterion.get("title", "")
            description = criterion.get("description", "")
            prompt = (
                f"You are an evaluation judge. Assess whether the following output "
                f"meets this criterion.\n\n"
                f"CRITERION: {title}\n"
                f"DESCRIPTION: {description}\n\n"
                f"OUTPUT:\n{output_text[:50000]}\n\n"
                f"Respond with exactly one word: PASS or FAIL, "
                f"followed by a brief reasoning on a new line."
            )
            try:
                response = caller.generate(prompt)
                verdict_line = response.strip().split("\n")[0].strip().lower()
                verdict = "pass" if "pass" in verdict_line else "fail"
                reasoning = "\n".join(response.strip().split("\n")[1:]).strip()
            except Exception as e:
                verdict = "fail"
                reasoning = f"Judge error: {e}"

            if verdict == "pass":
                n_passed += 1
            criteria_results.append({
                "title": title,
                "verdict": verdict,
                "reasoning": reasoning,
            })

        return ScoreResult(
            score=n_passed,
            max_score=len(criteria),
            all_pass=n_passed == len(criteria),
            n_criteria=len(criteria),
            n_passed=n_passed,
            criteria_results=criteria_results,
            scorer_type="llm_judge",
            judge_model=self._judge_model,
        )


class FileCheckScorer:
    """Deterministic file-existence and format checks. Must be explicitly requested."""

    def score_task(self, task_data: dict, run_dir: Path, **kwargs) -> ScoreResult:
        deliverables = task_data.get("deliverables", {})
        if not deliverables:
            raise ValueError(
                "FileCheckScorer requires 'deliverables' in task.json"
            )

        output_dir = run_dir / "output"
        criteria_results = []
        n_passed = 0

        for key, filename in deliverables.items():
            expected = output_dir / filename
            if expected.exists() and expected.stat().st_size > 0:
                verdict = "pass"
                reasoning = f"{filename} exists ({expected.stat().st_size} bytes)"
                n_passed += 1
            elif expected.exists():
                verdict = "fail"
                reasoning = f"{filename} exists but is empty"
            else:
                verdict = "fail"
                reasoning = f"{filename} not found in output/"

            criteria_results.append({
                "title": f"deliverable_{key}: {filename}",
                "verdict": verdict,
                "reasoning": reasoning,
            })

        return ScoreResult(
            score=n_passed,
            max_score=len(deliverables),
            all_pass=n_passed == len(deliverables),
            n_criteria=len(deliverables),
            n_passed=n_passed,
            criteria_results=criteria_results,
            scorer_type="file_check",
        )


# ---------------------------------------------------------------------------
# Scorer factory
# ---------------------------------------------------------------------------

_SCORER_REGISTRY: dict[str, type] = {
    "harvey": HarveyLabScorer,
    "llm_judge": LLMJudgeScorer,
    "file_check": FileCheckScorer,
    # agent_bench:<name> handled dynamically in create_scorer
}


def create_scorer(
    scorer_name: str,
    bench_root: Path | None = None,
    judge_model: str = "gemini-3.1-flash-lite",
) -> Scorer:
    if scorer_name == "harvey":
        if not bench_root:
            raise ValueError("HarveyLabScorer requires bench_root")
        return HarveyLabScorer(bench_root=bench_root, judge_model=judge_model)
    elif scorer_name == "llm_judge":
        return LLMJudgeScorer(judge_model=judge_model)
    elif scorer_name == "file_check":
        return FileCheckScorer()
    elif scorer_name.startswith("agent_bench:"):
        benchmark = scorer_name.split(":", 1)[1]
        from .bench import AgentBenchScorer
        return AgentBenchScorer(benchmark=benchmark)
    else:
        raise ValueError(
            f"Unknown scorer '{scorer_name}'. "
            f"Available: {list(_SCORER_REGISTRY.keys())} + agent_bench:<name>"
        )
