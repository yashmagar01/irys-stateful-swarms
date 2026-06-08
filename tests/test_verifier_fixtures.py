"""Pre-Phase-0 verifier fixtures.

These tests encode the three failure modes identified in Codex R3 review:
1. Deterministic verifier false positive (intro/outline mentions)
2. Per-file augmentation blind to existing draft
3. Criterion survival trace (entry → curated → draft → scorer)
"""
from src.swarm.verification import verify_deterministic


# ---------------------------------------------------------------------------
# Fixture 1: verify_deterministic false positive on intro-only mentions
# ---------------------------------------------------------------------------

def test_verify_deterministic_rejects_intro_only_section_ref():
    """Section ref in TOC/outline but not in body should remain unresolved."""
    draft = (
        "# Pre-Notification Briefing Paper\n\n"
        "## Table of Contents\n"
        "1. Executive Summary\n"
        "2. HSR Filing Requirements\n"
        "3. Section 8.3 Analysis of Geographic Market Definition\n\n"
        "## 1. Executive Summary\n"
        "This briefing paper analyzes the proposed acquisition of TargetCo. "
        "The transaction raises significant antitrust concerns in several "
        "geographic markets.\n\n"
        "## 2. HSR Filing Requirements\n"
        "The transaction value exceeds the $111.4 million threshold. "
        "A Hart-Scott-Rodino filing is required within 30 days.\n"
    )
    must_include = [
        {"summary": "Section 8.3 geographic market overlap analysis with HHI calculations"}
    ]
    verified, unresolved = verify_deterministic(draft, must_include)
    assert 0 in unresolved, (
        "Item mentioning Section 8.3 should be unresolved when it only "
        "appears in TOC, not in substantive body content"
    )
    assert 0 not in verified


def test_verify_deterministic_rejects_dollar_in_header_only():
    """Dollar amount in heading but not discussed in body is a false positive."""
    draft = (
        "# Analysis of the $535 Million Acquisition\n\n"
        "## Executive Summary\n"
        "The proposed merger involves the acquisition of Prism Diagnostics "
        "by LabVantage. This memo addresses the antitrust implications "
        "of the transaction in the clinical laboratory market.\n\n"
        "## Competitive Analysis\n"
        "The combined entity would have a national market share of "
        "approximately 0.74 percent, far below concentration thresholds.\n"
    )
    must_include = [
        {"summary": "Transaction enterprise value is $535 million with 2.2x revenue multiple"}
    ]
    verified, unresolved = verify_deterministic(draft, must_include)
    assert 0 in unresolved, (
        "$535 million in title only — no substantive discussion of "
        "enterprise value or revenue multiple in body"
    )


def test_verify_deterministic_accepts_substantive_body_mention():
    """Dollar amount discussed in body with context should be verified."""
    draft = (
        "# Acquisition Analysis\n\n"
        "## Transaction Overview\n"
        "The proposed acquisition has an enterprise value of $535 million, "
        "representing a 2.2x multiple on Prism's trailing twelve-month "
        "revenue of $247 million. The transaction will be funded through "
        "a combination of debt and equity.\n"
    )
    must_include = [
        {"summary": "Transaction enterprise value is $535 million"}
    ]
    verified, unresolved = verify_deterministic(draft, must_include)
    assert 0 in verified, (
        "$535 million discussed substantively in body should be verified"
    )


# ---------------------------------------------------------------------------
# Fixture 2: Per-file augmentation must see the current draft
# ---------------------------------------------------------------------------

def test_append_missing_items_prompt_includes_draft():
    """_append_missing_items_for_file must pass draft content to the model.

    The current implementation builds a prompt that is blind to what the
    draft already contains, causing the model to duplicate existing content
    or contradict it. This test captures the prompt and verifies draft
    content appears in it.
    """
    from unittest.mock import MagicMock
    from src.swarm.synthesis import _append_missing_items_for_file
    from src.swarm.blackboard import Blackboard
    from src.swarm.models import ModelResult

    bb = MagicMock(spec=Blackboard)
    bb.task_instruction = "Draft an antitrust briefing paper."

    captured_prompts = []

    class FakeCaller:
        def complete(self, prompt, *, max_tokens=8192, temperature=0.05, json_mode=True):
            captured_prompts.append(prompt)
            return ModelResult(
                text='{"text": "## Supplemental\\nAdditional content."}',
                tokens_input=100, tokens_output=50,
                tokens_total=150, model="fake", latency_ms=10,
            )

    draft = (
        "## Executive Summary\n"
        "The proposed acquisition raises moderate antitrust risk.\n\n"
        "## Market Analysis\n"
        "Combined national share is 0.74%.\n"
    )
    missing = [{"summary": "Charlotte MSA combined share ~13.9%", "entry_id": "e100"}]

    _append_missing_items_for_file(
        "memo.docx", draft, missing, [], bb, FakeCaller(),
    )

    assert captured_prompts, "Model was never called"
    prompt_text = captured_prompts[0]
    assert "0.74%" in prompt_text or "Market Analysis" in prompt_text, (
        "Augmentation prompt must include current draft content so the model "
        "knows what is already written. Found prompt:\n" + prompt_text[:500]
    )


# ---------------------------------------------------------------------------
# Fixture 3: Criterion survival trace
# ---------------------------------------------------------------------------

def test_criterion_survival_deterministic_to_final():
    """A curated must_include item with an exact value must survive to the
    final draft and be verifiable by verify_deterministic.

    This is the minimal criterion survival oracle: if a value enters
    must_include, it must be findable in the output.
    """
    must_include = [
        {"summary": "Combined Charlotte MSA market share is approximately 13.9%"},
        {"summary": "Prism TTM revenue is $247 million"},
        {"summary": "Transaction enterprise value is $535 million"},
    ]

    final_draft = (
        "# Antitrust Briefing\n\n"
        "## Geographic Market Analysis\n"
        "In the Charlotte-Concord-Gastonia MSA, the combined market share "
        "of LabVantage and Prism is approximately 13.9%, calculated from "
        "Prism's $64M and LabVantage's $22M against a $620M total market.\n\n"
        "## Transaction Overview\n"
        "Prism Diagnostics reported trailing twelve-month revenue of $247 million. "
        "The acquisition enterprise value of $535 million represents a 2.2x "
        "revenue multiple.\n"
    )

    verified, unresolved = verify_deterministic(final_draft, must_include)

    assert len(verified) == 3, (
        f"All 3 must_include items should be verified in a good draft. "
        f"Verified: {verified}, Unresolved: {unresolved}"
    )
    assert len(unresolved) == 0


# ---------------------------------------------------------------------------
# Fixture 4: Numbered body paragraphs are prose (Codex R4 finding)
# ---------------------------------------------------------------------------

def test_verify_deterministic_accepts_numbered_body_paragraph():
    """A numbered body paragraph containing a dollar amount is prose, not TOC."""
    draft = (
        "# Transaction Summary\n\n"
        "## Key Terms\n"
        "1. The enterprise value is $535 million based on trailing revenue of "
        "$247 million, representing a 2.2x revenue multiple. The purchase price "
        "will be subject to customary post-closing adjustments.\n\n"
        "2. The termination fee is $15 million, payable by either party upon "
        "breach of the merger agreement under Section 7.2.\n"
    )
    must_include = [
        {"summary": "Enterprise value is $535 million"},
    ]
    verified, unresolved = verify_deterministic(draft, must_include)
    assert 0 in verified, (
        "$535 million in a numbered body paragraph should be verified — "
        "long numbered lines are prose, not TOC entries"
    )


# ---------------------------------------------------------------------------
# Fixture 5: shadow_judge_audit handles dict deliverables (Codex R4 finding)
# ---------------------------------------------------------------------------

def test_shadow_judge_audit_handles_dict_deliverable():
    """shadow_judge_audit must not crash when deliverable is a dict."""
    from src.swarm.synthesis import shadow_judge_audit
    from src.swarm.blackboard import Blackboard
    from src.swarm.models import ModelResult

    class FakeCaller:
        def complete(self, prompt, *, max_tokens=8192, temperature=0.05, json_mode=True):
            return ModelResult(
                text='{"omissions": []}',
                tokens_input=1, tokens_output=1,
                tokens_total=2, model="fake", latency_ms=0,
            )

    bb = Blackboard(task_instruction="Prepare memo and spreadsheet.")
    deliverable = {
        "memo.docx": "This is the memo content about the deal.",
        "analysis.xlsx": "Col A, Col B\n1, 2",
    }
    result, tokens = shadow_judge_audit(deliverable, bb, {}, FakeCaller())
    assert isinstance(result, dict), "Dict deliverable should return dict"
    assert set(result.keys()) == {"memo.docx", "analysis.xlsx"}
