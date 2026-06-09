from src.swarm.blackboard import Blackboard
from src.swarm.models import Entry, EntrySource, Signal, gen_signal_id
from src.swarm.artifact_contracts import contracts_to_signals


def test_observation_cannot_close_artifact_requirement():
    bb = Blackboard(task_instruction="Test", entries=[])
    sig = Signal(
        id="s1", type="artifact_requirement",
        content="Artifact must contain table in 'LP Terms'",
        origin_entry="artifact_contract", priority="critical",
        status="open", iteration_created=0,
    )
    bb.signals.append(sig)

    obs = Entry(
        id="e1", type="observation",
        content="Section 2 mentions liquidation preferences.",
        source=EntrySource("doc.pdf", "Section 2", "LP mentioned"),
        confidence=0.9,
        addresses_signals=["s1"],
    )
    bb.add_entry(obs)

    assert sig.status == "open"
    assert sig.addressed_by is None


def test_analysis_can_close_artifact_requirement():
    bb = Blackboard(task_instruction="Test", entries=[])
    sig = Signal(
        id="s2", type="artifact_requirement",
        content="Artifact must contain comparison table",
        origin_entry="artifact_contract", priority="high",
        status="open", iteration_created=0,
    )
    bb.signals.append(sig)

    analysis = Entry(
        id="e2", type="analysis",
        content="LP deviation table comparing 3 scenarios across all docs.",
        source=EntrySource("doc.pdf", "Analysis", "Comparison table"),
        confidence=0.85,
        addresses_signals=["s2"],
    )
    bb.add_entry(analysis)

    assert sig.status == "addressed"
    assert sig.addressed_by == "e2"


def test_calculation_can_close_artifact_requirement():
    bb = Blackboard(task_instruction="Test", entries=[])
    sig = Signal(
        id="s3", type="artifact_requirement",
        content="Artifact must contain calculation",
        origin_entry="artifact_contract", priority="critical",
        status="open", iteration_created=0,
    )
    bb.signals.append(sig)

    calc = Entry(
        id="e3", type="calculation",
        content="35% × $7,261,428 = $2,541,500",
        confidence=0.95,
        addresses_signals=["s3"],
    )
    bb.add_entry(calc)

    assert sig.status == "addressed"


def test_regular_signal_still_closeable_by_observation():
    bb = Blackboard(task_instruction="Test", entries=[])
    sig = Signal(
        id="s4", type="question",
        content="What is the revenue?",
        origin_entry="seed_plan", priority="high",
        status="open", iteration_created=0,
    )
    bb.signals.append(sig)

    obs = Entry(
        id="e4", type="observation",
        content="Revenue is $10M as stated in 10-K.",
        source=EntrySource("10k.pdf", "Financials", "$10M"),
        confidence=0.9,
        addresses_signals=["s4"],
    )
    bb.add_entry(obs)

    assert sig.status == "addressed"
    assert sig.addressed_by == "e4"


def test_contracts_to_signals_creates_artifact_requirement():
    bb = Blackboard(task_instruction="Test", entries=[])
    contracts = [
        {"section": "LP Terms", "native_form": "table",
         "summary": "Compare LP across docs", "importance": "critical",
         "target_file": "memo.docx", "source": "artifact_contract"},
        {"section": "Minor Detail", "native_form": "paragraph",
         "summary": "Background info", "importance": "medium",
         "target_file": "memo.docx", "source": "artifact_contract"},
    ]
    count = contracts_to_signals(contracts, bb)
    assert count == 1
    assert len(bb.signals) == 1
    assert bb.signals[0].type == "artifact_requirement"
    assert bb.signals[0].priority == "critical"
    assert "table" in bb.signals[0].content
    assert "LP Terms" in bb.signals[0].content
