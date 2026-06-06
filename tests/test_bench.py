"""Tests for the agent-bench bridge and benchmark tier configuration."""

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

def test_all_tiers_present():
    tiers = {s.tier for s in BENCHMARK_TIERS}
    assert tiers == {"core", "product", "guardrail", "experimental"}


def test_core_benchmarks_are_legal():
    core = get_benchmarks(tiers=["core"])
    assert len(core) >= 4
    for s in core:
        assert s.category == "legal", f"{s.name} is core but not legal"


def test_product_benchmarks_count():
    product = get_benchmarks(tiers=["product"])
    assert len(product) >= 10


def test_guardrail_benchmarks_count():
    guardrail = get_benchmarks(tiers=["guardrail"])
    assert len(guardrail) >= 5


def test_experimental_includes_arc_agi_3():
    experimental = get_benchmarks(tiers=["experimental"])
    names = [s.name for s in experimental]
    assert "arc_agi_3" in names


def test_total_benchmark_count():
    assert len(BENCHMARK_TIERS) >= 25


def test_no_duplicate_benchmark_names():
    names = [s.name for s in BENCHMARK_TIERS]
    assert len(names) == len(set(names)), f"Duplicates: {[n for n in names if names.count(n) > 1]}"


def test_promoted_benchmarks_in_product():
    product = get_benchmarks(tiers=["product"])
    names = [s.name for s in product]
    assert "qasa" in names, "QASA should be promoted to product tier"
    assert "longhealth" in names, "LongHealth should be promoted to product tier"


# ---------------------------------------------------------------------------
# Filter functions
# ---------------------------------------------------------------------------

def test_filter_by_tier():
    core = get_benchmarks(tiers=["core"])
    for s in core:
        assert s.tier == "core"


def test_filter_by_category():
    financial = get_benchmarks(categories=["financial"])
    for s in financial:
        assert s.category == "financial"
    assert len(financial) >= 2


def test_filter_by_name():
    result = get_benchmarks(names=["cuad", "hotpotqa"])
    assert len(result) == 2
    names = [s.name for s in result]
    assert "cuad" in names
    assert "hotpotqa" in names


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
    """IrysSwarmBackend must satisfy the AgentBackend protocol shape."""
    backend = IrysSwarmBackend()
    assert isinstance(backend.name, str)
    assert isinstance(backend.version, str)
    import asyncio
    assert asyncio.iscoroutinefunction(backend.run)


# ---------------------------------------------------------------------------
# AgentBenchScorer
# ---------------------------------------------------------------------------

def test_agent_bench_scorer_integration():
    """Verify AgentBenchScorer can be created for known benchmarks."""
    try:
        from src.bench import AgentBenchScorer
        scorer = AgentBenchScorer("hotpotqa")
        assert scorer._benchmark == "hotpotqa"
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
    """create_scorer('agent_bench:hotpotqa') should work."""
    try:
        from src.scoring import create_scorer
        scorer = create_scorer("agent_bench:hotpotqa")
        assert scorer._benchmark == "hotpotqa"
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
}


def test_all_non_experimental_benchmarks_exist_in_agent_bench():
    """Every non-experimental benchmark must have an agent-bench adapter."""
    non_experimental = [s for s in BENCHMARK_TIERS if s.tier != "experimental"]
    for spec in non_experimental:
        assert spec.name in AGENT_BENCH_BENCHMARKS, (
            f"Benchmark '{spec.name}' is tier '{spec.tier}' but has no "
            f"known agent-bench adapter"
        )
