"""Tests for the agent-bench bridge and benchmark tier configuration.

Updated 2026-06: Tiers restructured to prioritize agentic benchmarks
that test swarm architecture value (Harvey LAB, OfficeQA Pro, GAIA,
AMA-Bench). Single-call QA benchmarks dropped.
"""

from __future__ import annotations

import pytest

from src.bench import (
    BENCHMARK_TIERS,
    BenchmarkSpec,
    IrysSwarmBackend,
    get_benchmarks,
)


# ---------------------------------------------------------------------------
# Tier configuration
# ---------------------------------------------------------------------------

def test_tiers_present():
    tiers = {s.tier for s in BENCHMARK_TIERS}
    assert "primary" in tiers
    assert "experimental" in tiers


def test_primary_benchmarks_are_agentic():
    primary = get_benchmarks(tiers=["primary"])
    assert len(primary) >= 3
    names = [s.name for s in primary]
    assert "harvey_lab" in names
    assert "officeqa_pro" in names
    assert "ama_bench" in names


def test_primary_benchmarks_have_sota():
    primary = get_benchmarks(tiers=["primary"])
    for s in primary:
        assert s.sota, f"{s.name} is primary but has no SOTA reference"


def test_experimental_includes_arc_agi_3():
    experimental = get_benchmarks(tiers=["experimental"])
    names = [s.name for s in experimental]
    assert "arc_agi_3" in names


def test_ama_bench_in_primary():
    primary = get_benchmarks(tiers=["primary"])
    names = [s.name for s in primary]
    assert "ama_bench" in names


def test_no_single_call_qa_benchmarks():
    """Single-call QA benchmarks were explicitly dropped."""
    names = {s.name for s in BENCHMARK_TIERS}
    dropped = {"hotpotqa", "financebench", "cuad", "legalbench", "musique",
               "contractnli", "maud", "docfinqa"}
    for d in dropped:
        assert d not in names, f"{d} should have been dropped (single-call QA)"


def test_total_benchmark_count():
    assert len(BENCHMARK_TIERS) == 5


def test_no_duplicate_benchmark_names():
    names = [s.name for s in BENCHMARK_TIERS]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Filter functions
# ---------------------------------------------------------------------------

def test_filter_by_tier():
    primary = get_benchmarks(tiers=["primary"])
    for s in primary:
        assert s.tier == "primary"


def test_filter_by_category():
    legal = get_benchmarks(categories=["legal_document_analysis"])
    assert len(legal) == 1
    assert legal[0].name == "harvey_lab"


def test_filter_by_name():
    result = get_benchmarks(names=["gaia", "ama_bench"])
    assert len(result) == 2
    names = [s.name for s in result]
    assert "gaia" in names
    assert "ama_bench" in names


def test_filter_returns_empty_for_nonexistent():
    result = get_benchmarks(names=["nonexistent_benchmark"])
    assert result == []


# ---------------------------------------------------------------------------
# IrysSwarmBackend
# ---------------------------------------------------------------------------

def test_backend_has_required_attributes():
    backend = IrysSwarmBackend()
    assert hasattr(backend, "name")
    assert hasattr(backend, "version")
    assert hasattr(backend, "run")
    assert backend.name == "irys-swarm"


def test_backend_protocol_compliance():
    backend = IrysSwarmBackend()
    assert isinstance(backend.name, str)
    assert isinstance(backend.version, str)
    import asyncio
    assert asyncio.iscoroutinefunction(backend.run)


# ---------------------------------------------------------------------------
# AgentBenchScorer
# ---------------------------------------------------------------------------

def test_agent_bench_scorer_integration():
    try:
        from src.bench import AgentBenchScorer
        scorer = AgentBenchScorer("gaia")
        assert scorer._benchmark == "gaia"
    except ImportError:
        pytest.skip("agent-bench not importable")


def test_agent_bench_scorer_unknown_raises():
    try:
        from src.bench import AgentBenchScorer
        with pytest.raises(ValueError, match="No agent-bench scorer"):
            AgentBenchScorer("nonexistent_benchmark_xyz")
    except ImportError:
        pytest.skip("agent-bench not importable")


# ---------------------------------------------------------------------------
# Scorer factory integration
# ---------------------------------------------------------------------------

def test_create_scorer_agent_bench_prefix():
    try:
        from src.scoring import create_scorer
        scorer = create_scorer("agent_bench:gaia")
        assert scorer._benchmark == "gaia"
    except (ImportError, ValueError):
        pytest.skip("agent-bench not importable")


# ---------------------------------------------------------------------------
# Benchmark specs validation
# ---------------------------------------------------------------------------

AGENT_BENCH_BENCHMARKS = {
    "cuad", "docfinqa", "longbench_v2", "facts_grounding", "legalbench",
    "hotpotqa", "musique", "maud", "contractnli", "fanoutqa", "nolima",
    "mrcr", "counting_stars", "loong", "l_citeeval", "multihop_rag",
    "nocha", "locomo", "qasa", "qmsum", "longhealth", "repoqa",
    "long_code_arena", "financebench",
    "gaia", "officeqa_pro", "ama_bench",
    "harvey_lab", "harvey_lab_sample", "harvey_lab_full",
}


def test_all_non_experimental_benchmarks_exist_in_agent_bench():
    non_experimental = [s for s in BENCHMARK_TIERS if s.tier != "experimental"]
    for spec in non_experimental:
        assert spec.name in AGENT_BENCH_BENCHMARKS, (
            f"Benchmark '{spec.name}' is tier '{spec.tier}' but has no "
            f"known agent-bench adapter"
        )
