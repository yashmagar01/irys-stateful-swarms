import json

from src.swarm.blackboard import Blackboard
from src.swarm.models import Entry, EntrySource, ModelResult
from src.swarm.obligations import build_synthesis_obligations


class EmptyObligationCaller:
    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        self.prompts.append(prompt)
        return ModelResult(
            text=json.dumps({"obligations": []}),
            tokens_input=20,
            tokens_output=5,
            tokens_total=25,
            model="fake-model",
            latency_ms=1,
        )


def test_build_synthesis_obligations_preserves_executed_debt_entries():
    blackboard = Blackboard(
        task_instruction="Review operational and contract risks.",
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="The amendment changes the payment deadline from June 1 to July 1.",
                source=EntrySource(
                    document="agreement.docx; amendment.docx",
                    section="multiple",
                    evidence="agreement June 1; amendment July 1",
                ),
                confidence=0.9,
                tags=[
                    "debt_sensor",
                    "debt_type:relation",
                    "debt_subtype:date_alignment",
                    "lifecycle:transformed",
                    "source_grounded:true",
                ],
                supports_entries=["e10", "e11"],
            ),
            Entry(
                id="e2",
                type="analysis",
                content="The missing backup vendor is a high severity continuity risk.",
                source=EntrySource(
                    document="msa.docx",
                    section="Section 2",
                    evidence="No backup vendor",
                ),
                confidence=0.87,
                tags=[
                    "debt_sensor",
                    "debt_type:severity",
                    "debt_subtype:risk_without_severity",
                    "severity:high",
                    "lifecycle:transformed",
                    "source_grounded:true",
                ],
                supports_entries=["e12"],
            ),
            Entry(
                id="e3",
                type="analysis",
                content="The termination issue is grounded in Section 12.4 of the MSA.",
                source=EntrySource(
                    document="msa.docx",
                    section="Section 12.4",
                    evidence="Customer may terminate for convenience on 30 days notice.",
                ),
                confidence=0.9,
                tags=[
                    "debt_sensor",
                    "debt_type:authority",
                    "debt_subtype:clause_reference_needed",
                    "lifecycle:transformed",
                    "source_grounded:true",
                ],
                supports_entries=["e13"],
            ),
            Entry(
                id="e4",
                type="observation",
                content="Schedule A row 1 states Alpha LLC owes $10.",
                source=EntrySource(
                    document="schedule.md",
                    section="Schedule A",
                    evidence="Row 1: Alpha LLC owes $10.",
                ),
                confidence=0.92,
                tags=[
                    "debt_sensor",
                    "debt_type:source_object",
                    "debt_subtype:missing_population",
                    "lifecycle:discovered",
                    "source_grounded:true",
                ],
            ),
            Entry(
                id="e5",
                type="gap",
                content="relation debt: compare unresolved dates.",
                tags=["debt_sensor", "debt_type:relation"],
            ),
        ],
    )

    obligations, tokens = build_synthesis_obligations(
        blackboard,
        {},
        EmptyObligationCaller(),
    )

    assert tokens == 25
    by_entry = {item["entry_id"]: item for item in obligations}
    assert by_entry["e1"]["source"] == "debt_sensor"
    assert by_entry["e1"]["obligation_type"] == "cross_document_link"
    assert by_entry["e2"]["obligation_type"] == "risk_recommendation"
    assert by_entry["e3"]["obligation_type"] == "legal_authority"
    assert by_entry["e4"]["obligation_type"] == "task_state_field"
    assert "e5" not in by_entry
    assert "Section 12.4" in by_entry["e3"]["verification_terms"]
    assert "$10" in by_entry["e4"]["verification_terms"]
