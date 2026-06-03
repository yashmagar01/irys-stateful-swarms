import json

from src.swarm.blackboard import Blackboard
from src.swarm.models import Entry, EntrySource, ModelResult
from src.swarm.source_claims import (
    normalize_claim_audit_items,
    verify_source_claims,
)


class FakeCaller:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        self.prompts.append(prompt)
        text = self.responses.pop(0) if self.responses else "{}"
        return ModelResult(
            text=text,
            tokens_input=10,
            tokens_output=5,
            tokens_total=15,
            model="fake-model",
            latency_ms=1,
        )


def test_normalize_claim_audit_items_filters_and_defaults():
    claims = normalize_claim_audit_items([
        {"claim": "too short"},
        {
            "claim": "The system stores operational state in CONTINUUM_STATE.md.",
            "status": "SUPPORTED",
            "supporting_entry_ids": "e1,e2",
            "source_documents": ["continuum.py"],
            "severity": "urgent",
        },
        {
            "claim": "The system stores operational state in CONTINUUM_STATE.md.",
            "status": "unsupported",
        },
    ])

    assert len(claims) == 1
    assert claims[0]["status"] == "supported"
    assert claims[0]["supporting_entry_ids"] == ["e1", "e2"]
    assert claims[0]["severity"] == "medium"


def test_verify_source_claims_writes_audit_report(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_SOURCE_CLAIM_VERIFICATION", "1")
    blackboard = Blackboard(
        task_instruction="Review repo architecture.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="continuum.py writes CONTINUUM_STATE.md in the workdir.",
                source=EntrySource(
                    document="continuum.py",
                    section="main",
                    evidence="state_path = workdir / 'CONTINUUM_STATE.md'",
                ),
                confidence=0.9,
            )
        ],
    )
    caller = FakeCaller([json.dumps({
        "claims": [
            {
                "claim": "continuum.py writes CONTINUUM_STATE.md in the workdir.",
                "status": "supported",
                "supporting_entry_ids": ["e1"],
                "source_documents": ["continuum.py"],
                "reason": "Entry e1 directly supports it.",
                "severity": "high",
            },
            {
                "claim": "The project uses Kubernetes executors.",
                "status": "unsupported",
                "reason": "No evidence mentions Kubernetes.",
                "severity": "high",
            },
        ]
    })])

    deliverable, tokens, report = verify_source_claims(
        "continuum.py writes CONTINUUM_STATE.md. The project uses Kubernetes executors.",
        blackboard,
        caller,
    )

    assert tokens == 15
    assert "SOURCE-GROUNDED BLACKBOARD EVIDENCE" in caller.prompts[0]
    assert "CONTINUUM_STATE.md" in caller.prompts[0]
    assert deliverable.startswith("continuum.py writes")
    assert report["summary"]["claims_checked"] == 2
    assert report["summary"]["risky_claims"] == 1
    written = json.loads(
        (tmp_path / "swarm" / "source_claim_verification.json").read_text(encoding="utf-8")
    )
    assert written["summary"]["status_counts"]["unsupported"] == 1


def test_verify_source_claims_can_quarantine_risky_claims(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_SOURCE_CLAIM_VERIFICATION", "1")
    monkeypatch.setenv("SWARM_SOURCE_CLAIM_QUARANTINE", "1")
    blackboard = Blackboard(
        task_instruction="Review repo architecture.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="README says the state file is CONTINUUM_STATE.md.",
                source=EntrySource(document="README.md", evidence="CONTINUUM_STATE.md"),
                confidence=0.9,
            )
        ],
    )
    caller = FakeCaller([json.dumps({
        "claims": [{
            "claim": "The project uses Kubernetes executors.",
            "status": "unsupported",
            "reason": "No source evidence supports this.",
            "severity": "high",
        }]
    })])

    deliverable, _, report = verify_source_claims(
        "The project uses Kubernetes executors.",
        blackboard,
        caller,
    )

    assert report["mode"] == "audit_and_quarantine"
    assert "## Source Support Caveats" in deliverable
    assert "SOURCE-CHECK QUARANTINED" in deliverable
    assert "The project uses Kubernetes executors." in deliverable


def test_source_claim_quarantine_removes_unsupported_workbook_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_SOURCE_CLAIM_VERIFICATION", "1")
    monkeypatch.setenv("SWARM_SOURCE_CLAIM_QUARANTINE", "1")
    blackboard = Blackboard(
        task_instruction="Prepare workbook.",
        output_dir=str(tmp_path),
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="The source states 74 total incidents.",
                source=EntrySource(document="ops_report.md", evidence="74 open incidents"),
                confidence=0.9,
            )
        ],
    )
    caller = FakeCaller([json.dumps({
        "claims": [{
            "claim": "The total incident count per Q3 Incident Report is 142.",
            "status": "unsupported",
            "reason": "No source evidence supports 142 incidents.",
            "severity": "critical",
        }]
    })])

    text = (
        "# Sheet: Workbook Data\n"
        "Metric | Value | Source\n"
        "Total incident count per Q3 Incident Report | 142 | Q3 Incident Report\n"
        "Total incident count per ops_report.md | 74 | ops_report.md"
    )
    deliverable, _, report = verify_source_claims(text, blackboard, caller)

    assert report["summary"]["risky_claims"] == 1
    body, caveats = deliverable.split("## Source Support Caveats", 1)
    assert "Total incident count per Q3 Incident Report | 142" not in body
    assert "SOURCE-CHECK QUARANTINED" in body
    assert "Total incident count per ops_report.md | 74" in body
    assert "Quarantined unsupported artifact lines: 1" in caveats
