import json

from src.swarm.blackboard import Blackboard
from src.swarm.models import Entry, ModelResult, Signal
from src.swarm.orchestrator import run_orchestrator
from src.swarm.worker_dispatch import compose_worker_prompt, execute_workers_parallel


class FakeCaller:
    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        self.prompts.append(prompt)
        return ModelResult(
            text=self.text,
            tokens_input=len(prompt) // 4,
            tokens_output=len(self.text) // 4,
            tokens_total=(len(prompt) + len(self.text)) // 4,
            model="fake",
            latency_ms=1,
        )


def test_orchestrator_exposes_signal_ids_for_assignment():
    blackboard = Blackboard(task_instruction="Analyze the documents")
    blackboard.signals = [
        Signal(
            id="s7",
            type="question",
            content="Resolve the highest priority accounting issue.",
            priority="critical",
            status="open",
        )
    ]
    caller = FakeCaller('{"action":"converge","reasoning":"done","remaining_gaps":[]}')

    run_orchestrator(blackboard, caller)

    assert "[s7] [critical] Resolve the highest priority accounting issue." in caller.prompts[0]


def test_worker_prompt_includes_assigned_signal_ids():
    prompt = compose_worker_prompt(
        "Resolve assigned accounting gaps.",
        [],
        [],
        "Analyze the documents",
        [("s7", "critical", "Resolve the highest priority accounting issue.")],
    )

    assert "ASSIGNED OPEN SIGNALS:" in prompt
    assert "[s7] (critical) Resolve the highest priority accounting issue." in prompt
    assert "include that exact signal ID" in prompt


def test_worker_assigned_signal_fallback_marks_signal_addressed():
    blackboard = Blackboard(task_instruction="Analyze the documents")
    blackboard.iteration = 3
    blackboard.signals = [
        Signal(
            id="s7",
            type="question",
            content="Resolve the highest priority accounting issue.",
            priority="critical",
            status="open",
        )
    ]
    response = json.dumps({
        "findings": [{
            "type": "analysis",
            "content": "The accounting issue is resolved with a specific, supported conclusion.",
            "confidence": 0.91,
            "addresses_signals": [],
        }]
    })
    caller = FakeCaller(response)

    outputs = execute_workers_parallel(
        [{
            "description": "Resolve the assigned accounting issue.",
            "reads_from_blackboard": [],
            "reads_from_documents": [],
            "expected_output_type": "analysis",
            "priority": "critical",
            "addresses_signals": ["s7"],
        }],
        blackboard,
        caller,
    )

    assert outputs[0].entries[0].addresses_signals == ["s7"]
    blackboard.add_entries_batch(outputs[0].entries)
    assert blackboard.signals[0].status == "addressed"
    assert blackboard.signals[0].addressed_by == outputs[0].entries[0].id
    assert blackboard.entries[0].to_dict()["addresses_signals"] == ["s7"]


def test_worker_ignores_stale_assigned_signal_ids():
    blackboard = Blackboard(task_instruction="Analyze the documents")
    blackboard.iteration = 3
    blackboard.signals = [
        Signal(id="s7", content="Already closed.", priority="critical", status="addressed")
    ]
    response = json.dumps({
        "findings": [{
            "type": "analysis",
            "content": "A valid analysis entry with no live assigned signal.",
            "confidence": 0.91,
        }]
    })
    caller = FakeCaller(response)

    outputs = execute_workers_parallel(
        [{
            "description": "Analyze remaining issues.",
            "reads_from_blackboard": [],
            "reads_from_documents": [],
            "expected_output_type": "analysis",
            "priority": "critical",
            "addresses_signals": ["s7"],
        }],
        blackboard,
        caller,
    )

    assert outputs[0].entries[0].addresses_signals == []


def test_worker_backfills_only_missing_assigned_signal_ids():
    blackboard = Blackboard(task_instruction="Analyze the documents")
    blackboard.iteration = 3
    blackboard.signals = [
        Signal(id="s7", content="Resolve issue seven.", priority="critical", status="open"),
        Signal(id="s8", content="Resolve issue eight.", priority="high", status="open"),
    ]
    response = json.dumps({
        "findings": [{
            "type": "analysis",
            "content": "The assigned issues are resolved with specific support.",
            "confidence": 0.91,
            "addresses_signals": ["s7"],
        }]
    })
    caller = FakeCaller(response)

    outputs = execute_workers_parallel(
        [{
            "description": "Resolve assigned issues.",
            "reads_from_blackboard": [],
            "reads_from_documents": [],
            "expected_output_type": "analysis",
            "priority": "critical",
            "addresses_signals": ["s7", "s8"],
        }],
        blackboard,
        caller,
    )

    assert outputs[0].entries[0].addresses_signals == ["s7", "s8"]


def test_worker_does_not_count_rejected_entry_signal_addresses():
    blackboard = Blackboard(task_instruction="Analyze the documents")
    blackboard.iteration = 3
    blackboard.signals = [
        Signal(id="s7", content="Resolve issue seven.", priority="critical", status="open"),
    ]
    response = json.dumps({
        "findings": [
            {
                "type": "observation",
                "content": "This rejected observation has no source but claims closure.",
                "confidence": 0.91,
                "addresses_signals": ["s7"],
            },
            {
                "type": "analysis",
                "content": "The accepted analysis resolves issue seven with support.",
                "confidence": 0.91,
            },
        ]
    })
    caller = FakeCaller(response)

    outputs = execute_workers_parallel(
        [{
            "description": "Resolve assigned issue.",
            "reads_from_blackboard": [],
            "reads_from_documents": [],
            "expected_output_type": "analysis",
            "priority": "critical",
            "addresses_signals": ["s7"],
        }],
        blackboard,
        caller,
    )

    assert outputs[0].entries[0].addresses_signals == ["s7"]
    assert outputs[0].entries[1].addresses_signals == ["s7"]


def test_worker_ignores_unshown_medium_signal_ids():
    blackboard = Blackboard(task_instruction="Analyze the documents")
    blackboard.iteration = 3
    blackboard.signals = [
        Signal(id="s7", content="Medium priority issue.", priority="medium", status="open"),
    ]
    response = json.dumps({
        "findings": [{
            "type": "analysis",
            "content": "A valid analysis entry with an unshown assigned signal.",
            "confidence": 0.91,
        }]
    })
    caller = FakeCaller(response)

    outputs = execute_workers_parallel(
        [{
            "description": "Analyze remaining issues.",
            "reads_from_blackboard": [],
            "reads_from_documents": [],
            "expected_output_type": "analysis",
            "priority": "critical",
            "addresses_signals": ["s7"],
        }],
        blackboard,
        caller,
    )

    assert outputs[0].entries[0].addresses_signals == []
