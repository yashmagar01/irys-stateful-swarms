from src.swarm.synthesis import _normalize_sections


def test_normalize_sections_merges_similar_names():
    """Section names with high word overlap should merge into the canonical (larger) group."""
    by_section = {
        "Coverage Gaps": [{"summary": f"gap {i}"} for i in range(5)],
        "Coverage Gap Analysis": [{"summary": f"gap analysis {i}"} for i in range(8)],
        "Gap Analysis Details": [{"summary": f"detail {i}"} for i in range(3)],
        "Policy Limits": [{"summary": f"limit {i}"} for i in range(6)],
        "Policy Limits and Retentions": [{"summary": f"retention {i}"} for i in range(4)],
        "Unrelated Section": [{"summary": "x"}],
        # Pad with filler sections to exceed MAX_SECTIONS threshold
        **{f"Filler Section {i}": [{"summary": f"f{i}"}] for i in range(20)},
    }
    result = _normalize_sections(by_section)
    # Coverage Gap Analysis (8 items) should absorb Coverage Gaps (5) and Gap Analysis Details (3)
    assert "Coverage Gap Analysis" in result
    assert len(result["Coverage Gap Analysis"]) >= 13  # 8 + 5 from Coverage Gaps at minimum
    # Policy Limits (6) should absorb Policy Limits and Retentions (4)
    assert "Policy Limits" in result
    assert len(result["Policy Limits"]) >= 10
    # Unrelated should survive
    assert "Unrelated Section" in result


def test_normalize_sections_noop_when_few_sections():
    """When section count is already under MAX_SECTIONS, no merging occurs."""
    by_section = {
        "Coverage Gaps": [{"summary": "a"}],
        "Policy Limits": [{"summary": "b"}],
    }
    result = _normalize_sections(by_section)
    assert len(result) == 2
    assert "Coverage Gaps" in result
    assert "Policy Limits" in result
