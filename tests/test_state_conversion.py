import pytest

from src.swarm.blackboard import Blackboard
from src.swarm.models import Entry, EntrySource, ModelResult
from src.swarm.state_conversion import (
    run_plan_coverage_review,
    run_plan_coverage_state_repair,
    run_state_conversion_review,
)


class FakeCaller:
    def __init__(self, text='{"coverage":[]}', exc=None):
        self.text = text
        self.exc = exc
        self.prompts = []
        self.max_tokens = []

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        self.prompts.append(prompt)
        self.max_tokens.append(max_tokens)
        if self.exc:
            raise self.exc
        return ModelResult(
            text=self.text,
            tokens_input=len(prompt) // 4,
            tokens_output=len(self.text) // 4,
            tokens_total=(len(prompt) + len(self.text)) // 4,
            model="fake",
            latency_ms=1,
        )


class SequenceCaller(FakeCaller):
    def __init__(self, texts):
        super().__init__()
        self.texts = list(texts)

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        self.text = self.texts.pop(0)
        return super().complete(prompt, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode)


def test_plan_coverage_review_bounds_prompt_size():
    entries = [
        Entry(
            id=f"e{i}",
            type="analysis",
            content="analysis detail " + ("x" * 1000),
            source=EntrySource(document=f"doc-{i % 4}", evidence=""),
        )
        for i in range(40)
    ]
    blackboard = Blackboard(task_instruction="Analyze documents", entries=entries)
    caller = FakeCaller()

    report, tokens = run_plan_coverage_review(
        blackboard,
        {"key_questions": ["What matters?"], "completeness_criteria": ["Be complete."]},
        caller,
        max_analytical_entries=5,
        analytical_char_limit=2_000,
    )

    assert report["parse_error"] is False
    assert tokens > 0
    assert caller.max_tokens == [4096, 4096]
    assert all(len(prompt) < 6_000 for prompt in caller.prompts)
    assert caller.prompts[0].count("(analysis)") <= 5


def test_plan_coverage_review_fails_soft_on_model_error():
    blackboard = Blackboard(
        task_instruction="Analyze documents",
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="A grounded analytical entry with enough detail.",
                source=EntrySource(document="doc", evidence=""),
            )
        ],
    )
    caller = FakeCaller(exc=RuntimeError("provider unavailable"))

    report, tokens = run_plan_coverage_review(
        blackboard, {"key_questions": ["What is missing?"]}, caller,
    )

    assert tokens == 0
    assert report["parse_error"] is True
    assert "RuntimeError" in report["error"]
    assert report["seed_coverage"] == []
    assert report["criteria_coverage"] == []


def test_plan_coverage_review_retries_failed_batches_individually():
    blackboard = Blackboard(
        task_instruction="Analyze documents",
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="A grounded analytical entry with enough detail.",
                source=EntrySource(document="doc", evidence=""),
            )
        ],
    )
    caller = SequenceCaller([
        "not json",
        '{"coverage":[{"id":"q1","status":"answered","answer_summary":"A","supporting_entries":["e1"]}]}',
        '{"coverage":[{"id":"q2","status":"unanswered","missing_reason":"B","supporting_entries":[]}]}',
    ])

    report, tokens = run_plan_coverage_review(
        blackboard,
        {"key_questions": ["Question 1?", "Question 2?"]},
        caller,
    )

    assert tokens > 0
    assert report["parse_error"] is False
    assert [row["id"] for row in report["seed_coverage"]] == ["q1", "q2"]
    assert len(caller.prompts) == 3


def test_state_conversion_retries_parse_error_in_smaller_batches():
    blackboard = Blackboard(
        task_instruction="Analyze documents",
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Revenue increased from $10 million to $15 million.",
                source=EntrySource(document="doc", evidence=""),
                confidence=0.9,
            ),
        ],
    )
    caller = SequenceCaller([
        "not json",
        '{"new_entries":[{"type":"calculation","content":"Revenue increased by $5 million based on the cited source observation.","source_entries":["e1"],"conversion_type":"unperformed_calculation","materiality":"high","confidence":0.86}]}',
    ])

    entries, report, tokens = run_state_conversion_review(
        blackboard,
        {"analytical_framework": "Calculate changes."},
        caller,
        max_new_entries=1,
        max_retry_batches=1,
    )

    assert tokens > 0
    assert len(entries) == 1
    assert entries[0].type == "calculation"
    assert entries[0].supports_entries == ["e1"]
    assert report["parse_error"] is False
    assert report["initial_parse_error"] is True
    assert report["retry_batches"] == 1
    assert report["retry_parse_errors"] == 0
    assert caller.max_tokens == [16384, 8192]


def test_state_conversion_reports_retry_parse_failure():
    blackboard = Blackboard(
        task_instruction="Analyze documents",
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="A sourced observation that should be analyzed.",
                source=EntrySource(document="doc", evidence=""),
                confidence=0.9,
            ),
        ],
    )
    caller = SequenceCaller(["not json", "still not json"])

    entries, report, tokens = run_state_conversion_review(
        blackboard,
        {},
        caller,
        max_retry_batches=1,
    )

    assert tokens > 0
    assert entries == []
    assert report["parse_error"] is True
    assert report["initial_parse_error"] is True
    assert report["retry_batches"] == 1
    assert report["retry_parse_errors"] == 1


def test_state_conversion_reports_failure_when_retry_entries_are_dropped():
    blackboard = Blackboard(
        task_instruction="Analyze documents",
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="A sourced observation that should be analyzed.",
                source=EntrySource(document="doc", evidence=""),
                confidence=0.9,
            ),
        ],
    )
    caller = SequenceCaller([
        "not json",
        '{"new_entries":[{"type":"analysis","content":"This retry entry cites a source that is not on the active blackboard.","source_entries":["missing"],"conversion_type":"wrong_relation","materiality":"high","confidence":0.8}]}',
    ])

    entries, report, tokens = run_state_conversion_review(
        blackboard,
        {},
        caller,
        max_retry_batches=1,
    )

    assert tokens > 0
    assert entries == []
    assert report["parse_error"] is True
    assert report["initial_parse_error"] is True
    assert report["retry_batches"] == 1
    assert report["retry_parse_errors"] == 0
    assert report["entries_created"] == 0
    assert report["entries_dropped_no_source"] == 1


def test_plan_coverage_state_repair_allows_gap_entries_without_factual_supports():
    """Gap/strategy repair entries without factual supports should pass through
    as unsupported open issues, not be silently dropped."""
    obs = Entry(
        id="e1", type="observation",
        content="The agreement requires a payment of $5 million within 30 days.",
        source=EntrySource(document="doc", evidence=""),
        confidence=0.9,
    )
    gap = Entry(
        id="g1", type="gap",
        content="Missing calculation of net present value for the deferred payment stream.",
        tags=["plan_coverage", "missing_work:calculate", "materiality:high"],
        supports_entries=["e1"],
    )
    blackboard = Blackboard(
        task_instruction="Analyze documents",
        entries=[obs, gap],
    )
    import json
    repair_response = json.dumps({"repair_entries": [
        {
            "type": "gap",
            "content": "Open issue: discount rate for NPV calculation is not specified in documents.",
            "supports_entries": ["g1"],
            "addressed_gap_ids": ["g1"],
            "confidence": 0.6,
            "repair_type": "state_repair",
            "missing_work_type": "calculate",
        },
    ]})
    caller = FakeCaller(repair_response)

    entries, report, tokens = run_plan_coverage_state_repair(
        blackboard, [gap], caller,
    )

    assert len(entries) == 1
    assert entries[0].type == "gap"
    assert "unsupported_open_issue" in entries[0].tags
    assert report["entries_dropped"] == 0


def test_plan_coverage_state_repair_noops_without_high_value_gaps():
    blackboard = Blackboard(task_instruction="Analyze documents")
    coverage_entries = [
        Entry(
            id="g1",
            type="gap",
            content="Medium gap with enough content to be realistic.",
            tags=["plan_coverage", "missing_work:calculate", "materiality:medium"],
        ),
        Entry(
            id="g2",
            type="gap",
            content="High gap already marked as requiring no missing work.",
            tags=["plan_coverage", "missing_work:none", "materiality:high"],
        ),
    ]
    caller = FakeCaller('{"repair_entries":[]}')

    entries, report, tokens = run_plan_coverage_state_repair(
        blackboard, coverage_entries, caller,
    )

    assert entries == []
    assert tokens == 0
    assert caller.prompts == []
    assert report["selected_gaps"] == 0
    assert report["parse_error"] is False


def test_plan_coverage_state_repair_skips_source_extraction_gaps():
    blackboard = Blackboard(task_instruction="Extract missing lease terms")
    coverage_entries = [
        Entry(
            id="g1",
            type="gap",
            content="High gap that requires reading more source text.",
            tags=["plan_coverage", "missing_work:extract_more", "materiality:critical"],
        ),
        Entry(
            id="g2",
            type="gap",
            content="High gap that cannot be answered from the available record.",
            tags=["plan_coverage", "missing_work:unanswerable", "materiality:high"],
        ),
    ]
    caller = FakeCaller('{"repair_entries":[]}')

    entries, report, tokens = run_plan_coverage_state_repair(
        blackboard, coverage_entries, caller,
    )

    assert entries == []
    assert tokens == 0
    assert caller.prompts == []
    assert report["selected_gaps"] == 0


def test_plan_coverage_state_repair_creates_supported_repair_entry():
    support = Entry(
        id="e1",
        type="analysis",
        content="The record contains $10 million of exposure and $4 million of reserves.",
        confidence=0.9,
    )
    gap = Entry(
        id="g1",
        type="gap",
        content="Completeness criterion c1 unsatisfied: calculate net exposure.",
        tags=["plan_coverage", "criterion:c1", "coverage:unsatisfied",
              "missing_work:calculate", "materiality:critical"],
        supports_entries=["e1"],
    )
    blackboard = Blackboard(task_instruction="Analyze exposure.", entries=[support, gap])
    caller = FakeCaller(
        '{"repair_entries":[{"type":"calculation","content":"Net exposure is $6 million, calculated as $10 million exposure minus $4 million reserves.","supports_entries":["e1"],"addressed_gap_ids":["g1"],"repair_type":"calculation","missing_work_type":"calculate","confidence":0.88}]}'
    )

    entries, report, tokens = run_plan_coverage_state_repair(
        blackboard, [gap], caller,
    )

    assert tokens > 0
    assert len(entries) == 1
    assert entries[0].type == "calculation"
    assert entries[0].supports_entries == ["e1", "g1"]
    assert "plan_coverage_repair" in entries[0].tags
    assert "repair_type:calculation" in entries[0].tags
    assert "missing_work:calculate" in entries[0].tags
    assert "materiality:critical" in entries[0].tags
    assert report["selected_gaps"] == 1
    assert report["entries_created"] == 1
    assert report["repaired_gap_ids"] == ["g1"]
    assert report["missing_work_counts"] == {"calculate": 1}
    assert "SELECTED HIGH/CRITICAL COVERAGE GAPS" in caller.prompts[0]


def test_plan_coverage_state_repair_drops_unsupported_non_gap_entries():
    gap = Entry(
        id="g1",
        type="gap",
        content="Completeness criterion c1 unsatisfied: compare two clauses.",
        tags=["plan_coverage", "criterion:c1", "coverage:unsatisfied",
              "missing_work:compare", "materiality:high"],
    )
    blackboard = Blackboard(task_instruction="Compare clauses.", entries=[gap])
    caller = FakeCaller(
        '{"repair_entries":[{"type":"analysis","content":"This unsupported comparison has no support IDs and should be dropped.","supports_entries":[],"addressed_gap_ids":[],"repair_type":"comparison","missing_work_type":"compare","confidence":0.8}]}'
    )

    entries, report, tokens = run_plan_coverage_state_repair(
        blackboard, [gap], caller,
    )

    assert tokens > 0
    assert entries == []
    assert report["selected_gaps"] == 1
    assert report["entries_created"] == 0
    assert report["entries_dropped"] == 1


def test_plan_coverage_state_repair_drops_addressed_only_non_gap_entries():
    gap = Entry(
        id="g1",
        type="gap",
        content="Completeness criterion c1 unsatisfied: calculate the amount.",
        tags=["plan_coverage", "criterion:c1", "coverage:unsatisfied",
              "missing_work:calculate", "materiality:critical"],
    )
    blackboard = Blackboard(task_instruction="Calculate amount.", entries=[gap])
    caller = FakeCaller(
        '{"repair_entries":[{"type":"calculation","content":"This calculation cites only the gap and has no factual support entry.","supports_entries":[],"addressed_gap_ids":["g1"],"repair_type":"calculation","missing_work_type":"calculate","confidence":0.8}]}'
    )

    entries, report, tokens = run_plan_coverage_state_repair(
        blackboard, [gap], caller,
    )

    assert tokens > 0
    assert entries == []
    assert report["entries_created"] == 0
    assert report["entries_dropped"] == 1


def test_plan_coverage_state_repair_drops_gap_only_support_entries():
    gap = Entry(
        id="g1",
        type="gap",
        content="Completeness criterion c1 unsatisfied: calculate the amount.",
        tags=["plan_coverage", "criterion:c1", "coverage:unsatisfied",
              "missing_work:calculate", "materiality:critical"],
    )
    blackboard = Blackboard(task_instruction="Calculate amount.", entries=[gap])
    caller = FakeCaller(
        '{"repair_entries":[{"type":"calculation","content":"This calculation incorrectly puts the gap in supports_entries as its only source.","supports_entries":["g1"],"addressed_gap_ids":[],"repair_type":"calculation","missing_work_type":"calculate","confidence":0.8}]}'
    )

    entries, report, tokens = run_plan_coverage_state_repair(
        blackboard, [gap], caller,
    )

    assert tokens > 0
    assert entries == []
    assert report["entries_created"] == 0
    assert report["entries_dropped"] == 1


def test_plan_coverage_state_repair_fails_soft_on_model_error():
    gap = Entry(
        id="g1",
        type="gap",
        content="Completeness criterion c1 unsatisfied: compare two clauses.",
        tags=["plan_coverage", "criterion:c1", "coverage:unsatisfied",
              "missing_work:compare", "materiality:high"],
    )
    blackboard = Blackboard(task_instruction="Compare clauses.", entries=[gap])
    caller = FakeCaller(exc=RuntimeError("provider unavailable"))

    entries, report, tokens = run_plan_coverage_state_repair(
        blackboard, [gap], caller,
    )

    assert entries == []
    assert tokens == 0
    assert report["selected_gaps"] == 1
    assert report["parse_error"] is True
    assert "RuntimeError" in report["error"]
    assert report["entries_created"] == 0
