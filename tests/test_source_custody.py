import json

from src.swarm.blackboard import Blackboard
from src.swarm.models import DocumentStatus, Entry, EntrySource
from src.swarm.source_custody import enforce_source_custody


def test_source_custody_quarantines_fake_source_documents(tmp_path):
    blackboard = Blackboard(
        task_instruction="Analyze incidents.",
        output_dir=str(tmp_path),
        documents=[
            DocumentStatus(id="d1", name="ops_report.md"),
            DocumentStatus(id="d2", name="remediation_notes.md"),
        ],
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="There are 74 open incidents.",
                source=EntrySource(document="ops_report.md", evidence="74 open incidents"),
            ),
            Entry(
                id="e2",
                type="observation",
                content="There are 50 incidents in a fake Q3 report.",
                source=EntrySource(document="Incident Report Q3", evidence="50 incidents"),
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert blackboard.entries[0].status == "active"
    assert blackboard.entries[1].status == "source_quarantined"
    assert "source_custody:quarantined" in blackboard.entries[1].tags
    assert report["summary"]["entries_quarantined"] == 1
    written = json.loads(
        (tmp_path / "swarm" / "source_custody.json").read_text(encoding="utf-8")
    )
    assert written["summary"]["invalid_documents"] == {"Incident Report Q3": 1}


def test_source_custody_accepts_text_wrapped_source_file_alias(tmp_path):
    blackboard = Blackboard(
        task_instruction="Review TypeScript routing.",
        output_dir=str(tmp_path),
        documents=[DocumentStatus(id="d1", name="chat-service.ts.txt")],
        entries=[
            Entry(
                id="e1",
                type="analysis",
                content="chat-service.ts routes Special Agent memory answers.",
                source=EntrySource(
                    document="chat-service.ts",
                    evidence="memory_answer",
                ),
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert blackboard.entries[0].status == "active"
    assert report["summary"]["entries_quarantined"] == 0


def test_source_custody_cascades_to_dependent_cross_cutting_entries(tmp_path):
    blackboard = Blackboard(
        task_instruction="Analyze incidents.",
        output_dir=str(tmp_path),
        documents=[DocumentStatus(id="d1", name="ops_report.md")],
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Incident Report Summary says 12 incidents.",
                source=EntrySource(
                    document="Incident Report Summary",
                    evidence="Total incidents = 12",
                ),
            ),
            Entry(
                id="e2",
                type="analysis",
                content=(
                    "Cross-document discrepancy between ops_report.md and "
                    "Incident Report Summary creates data-integrity risk."
                ),
                source=EntrySource(document="cross_cutting", evidence=""),
                supports_entries=["e1"],
            ),
            Entry(
                id="e3",
                type="analysis",
                content="ops_report.md supports 74 open incidents.",
                source=EntrySource(document="ops_report.md", evidence="74 open incidents"),
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert blackboard.find_entry("e1").status == "source_quarantined"
    assert blackboard.find_entry("e2").status == "source_quarantined"
    assert blackboard.find_entry("e3").status == "active"
    assert report["summary"]["reasons"]["invalid_source_document"] == 1
    assert report["summary"]["reasons"]["depends_on_invalid_source_state"] == 1
