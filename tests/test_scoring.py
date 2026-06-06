"""Tests for the scorer abstraction layer and benchmark integrity."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from src.scoring import (
    ScoreResult,
    TaskResolver,
    FileCheckScorer,
    create_scorer,
    load_manifest_for_scoring,
)


# ---------------------------------------------------------------------------
# ScoreResult
# ---------------------------------------------------------------------------

def test_score_result_fields():
    r = ScoreResult(
        score=3, max_score=5, all_pass=False,
        n_criteria=5, n_passed=3,
        criteria_results=[{"title": "a", "verdict": "pass"}],
        scorer_type="test",
    )
    assert r.scorer_type == "test"
    assert r.judge_model is None


# ---------------------------------------------------------------------------
# TaskResolver — legacy manifest (Harvey-only)
# ---------------------------------------------------------------------------

def test_resolver_legacy_manifest(tmp_path):
    bench = tmp_path / "bench"
    task_dir = bench / "tasks" / "area" / "task1"
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(json.dumps({
        "title": "test", "instructions": "do stuff",
    }))

    manifest = {"bench_root": str(bench), "tasks": [{"task_id": "area/task1"}]}
    resolver = TaskResolver(manifest)

    resolved = resolver.resolve({"task_id": "area/task1"})
    assert resolved.task_dir == task_dir
    assert resolved.scorer_name == "harvey"
    assert resolved.source.type == "harvey_lab"


# ---------------------------------------------------------------------------
# TaskResolver — multi-source manifest
# ---------------------------------------------------------------------------

def test_resolver_multi_source(tmp_path):
    harvey = tmp_path / "harvey"
    local = tmp_path / "local"
    (harvey / "tasks" / "h1").mkdir(parents=True)
    (harvey / "tasks" / "h1" / "task.json").write_text("{}")
    (local / "tasks" / "l1").mkdir(parents=True)
    (local / "tasks" / "l1" / "task.json").write_text(json.dumps({
        "scorer": "file_check",
    }))

    manifest = {
        "sources": [
            {"name": "harvey_lab", "type": "harvey_lab",
             "root": str(harvey), "default_scorer": "harvey"},
            {"name": "local", "type": "local",
             "root": str(local), "default_scorer": "llm_judge"},
        ],
        "tasks": [
            {"task_id": "h1", "source": "harvey_lab"},
            {"task_id": "l1", "source": "local"},
        ],
    }
    resolver = TaskResolver(manifest)

    h = resolver.resolve({"task_id": "h1", "source": "harvey_lab"})
    assert h.scorer_name == "harvey"

    l = resolver.resolve({"task_id": "l1", "source": "local"})
    assert l.scorer_name == "file_check"


def test_resolver_explicit_scorer_in_task_json_overrides_source_default(tmp_path):
    root = tmp_path / "bench"
    td = root / "tasks" / "t1"
    td.mkdir(parents=True)
    (td / "task.json").write_text(json.dumps({"scorer": "llm_judge"}))

    manifest = {
        "sources": [
            {"name": "s", "type": "local", "root": str(root),
             "default_scorer": "file_check"},
        ],
        "tasks": [{"task_id": "t1", "source": "s"}],
    }
    resolved = TaskResolver(manifest).resolve({"task_id": "t1", "source": "s"})
    assert resolved.scorer_name == "llm_judge"


def test_resolver_no_scorer_no_default_raises(tmp_path):
    root = tmp_path / "bench"
    td = root / "tasks" / "t1"
    td.mkdir(parents=True)
    (td / "task.json").write_text(json.dumps({"title": "test"}))

    manifest = {
        "sources": [
            {"name": "s", "type": "local", "root": str(root)},
        ],
        "tasks": [{"task_id": "t1", "source": "s"}],
    }
    with pytest.raises(ValueError, match="no scorer specified"):
        TaskResolver(manifest).resolve({"task_id": "t1", "source": "s"})


def test_resolver_unknown_source_raises():
    manifest = {"sources": [], "tasks": []}
    resolver = TaskResolver(manifest)
    with pytest.raises(ValueError, match="Unknown source"):
        resolver.resolve({"task_id": "t1", "source": "nope"})


# ---------------------------------------------------------------------------
# FileCheckScorer
# ---------------------------------------------------------------------------

def test_file_check_scorer_pass(tmp_path):
    run_dir = tmp_path / "run"
    output = run_dir / "output"
    output.mkdir(parents=True)
    (output / "report.docx").write_text("content")

    task_data = {"deliverables": {"report": "report.docx"}}
    result = FileCheckScorer().score_task(task_data, run_dir)

    assert result.all_pass
    assert result.n_passed == 1
    assert result.scorer_type == "file_check"
    assert result.criteria_results[0]["verdict"] == "pass"


def test_file_check_scorer_missing_file(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "output").mkdir(parents=True)

    task_data = {"deliverables": {"report": "report.docx"}}
    result = FileCheckScorer().score_task(task_data, run_dir)

    assert not result.all_pass
    assert result.n_passed == 0
    assert result.criteria_results[0]["verdict"] == "fail"


def test_file_check_scorer_empty_file(tmp_path):
    run_dir = tmp_path / "run"
    output = run_dir / "output"
    output.mkdir(parents=True)
    (output / "report.docx").write_text("")

    task_data = {"deliverables": {"report": "report.docx"}}
    result = FileCheckScorer().score_task(task_data, run_dir)

    assert not result.all_pass
    assert result.criteria_results[0]["verdict"] == "fail"


def test_file_check_scorer_no_deliverables_raises(tmp_path):
    with pytest.raises(ValueError, match="deliverables"):
        FileCheckScorer().score_task({}, tmp_path)


# ---------------------------------------------------------------------------
# create_scorer factory
# ---------------------------------------------------------------------------

def test_create_scorer_file_check():
    scorer = create_scorer("file_check")
    assert isinstance(scorer, FileCheckScorer)


def test_create_scorer_harvey_no_root_raises():
    with pytest.raises(ValueError, match="bench_root"):
        create_scorer("harvey")


def test_create_scorer_unknown_raises():
    with pytest.raises(ValueError, match="Unknown scorer"):
        create_scorer("magic_scorer")


# ---------------------------------------------------------------------------
# load_manifest_for_scoring priority chain
# ---------------------------------------------------------------------------

def test_manifest_loading_explicit_override(tmp_path):
    manifest_file = tmp_path / "m.json"
    manifest_file.write_text(json.dumps({"sources": []}))
    result = load_manifest_for_scoring(tmp_path, manifest_override=manifest_file)
    assert result == {"sources": []}


def test_manifest_loading_persisted(tmp_path):
    persisted = tmp_path / "manifest.json"
    persisted.write_text(json.dumps({"bench_root": "/test"}))
    result = load_manifest_for_scoring(tmp_path)
    assert result["bench_root"] == "/test"


def test_manifest_loading_legacy_bench_root(tmp_path, monkeypatch):
    bench = str(tmp_path / "harvey_root")
    monkeypatch.setenv("HARVEY_BENCH_ROOT", bench)
    result = load_manifest_for_scoring(tmp_path)
    assert result["bench_root"] == bench


def test_manifest_loading_nothing_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("HARVEY_BENCH_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="Cannot determine"):
        load_manifest_for_scoring(tmp_path)


# ---------------------------------------------------------------------------
# Benchmark integrity — criteria never leak into generation
# ---------------------------------------------------------------------------

def test_criteria_never_in_generation_context():
    """run_single_task only passes instructions/title/deliverables/work_type
    into generation, never criteria/match_criteria/scorer fields."""
    import ast
    runner_path = Path("src/runner.py")
    source = runner_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                key = node.slice.value
                assert key not in ("criteria", "match_criteria", "scorer", "scorer_config"), (
                    f"runner.py accesses task_data['{key}'] — "
                    f"this would leak scoring criteria into generation context"
                )


def test_document_discovery_excludes_task_json():
    """discover_documents must skip task.json to prevent criteria leakage."""
    import inspect
    from src.ingestion import discover_documents
    source = inspect.getsource(discover_documents)
    assert "task.json" in source, (
        "discover_documents does not explicitly exclude task.json — "
        "criteria could leak into generation via document ingestion"
    )


def test_scorer_without_criteria_and_no_explicit_scorer_raises(tmp_path):
    """Tasks with no criteria and no explicit scorer must error, not silently pass."""
    root = tmp_path / "bench"
    td = root / "tasks" / "t1"
    td.mkdir(parents=True)
    (td / "task.json").write_text(json.dumps({
        "title": "test", "instructions": "do stuff",
    }))

    manifest = {
        "sources": [
            {"name": "community", "type": "local", "root": str(root)},
        ],
        "tasks": [{"task_id": "t1", "source": "community"}],
    }
    with pytest.raises(ValueError, match="no scorer specified"):
        TaskResolver(manifest).resolve({"task_id": "t1", "source": "community"})
