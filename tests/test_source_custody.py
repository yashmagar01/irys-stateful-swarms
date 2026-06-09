import json

from src.swarm.blackboard import Blackboard
from src.swarm.models import DocumentStatus, Entry, EntrySource
from src.swarm.source_custody import (
    _document_name_aliases,
    _is_synthetic_source,
    _mentioned_invalid_documents,
    enforce_source_custody,
    source_document_is_valid,
)


def test_source_custody_quarantines_fake_source_documents(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SOURCE_CUSTODY_AUDIT_ONLY", "0")
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


def test_source_custody_cascades_to_dependent_cross_cutting_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SOURCE_CUSTODY_AUDIT_ONLY", "0")
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


def test_source_custody_accepts_user_prompt_as_synthetic_source(tmp_path):
    blackboard = Blackboard(
        task_instruction="Draft a proffer agreement.",
        output_dir=str(tmp_path),
        documents=[DocumentStatus(id="d1", name="template.docx")],
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="The agreement must include a carve-out.",
                source=EntrySource(document="user_prompt", evidence="carve-out"),
            ),
            Entry(
                id="e2",
                type="analysis",
                content="Based on e1, the carve-out should restrict disclosures.",
                source=EntrySource(document="cross_cutting"),
                supports_entries=["e1"],
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert blackboard.find_entry("e1").status == "active"
    assert blackboard.find_entry("e2").status == "active"
    assert report["summary"]["entries_quarantined"] == 0


def test_source_custody_fuzzy_matches_document_names(tmp_path):
    blackboard = Blackboard(
        task_instruction="Analyze the PSA.",
        output_dir=str(tmp_path),
        documents=[
            DocumentStatus(id="d1", name="purchase-and-sale-agreement.docx"),
            DocumentStatus(id="d2", name="sec-inquiry-letter.docx"),
        ],
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="The PSA contains a financing contingency.",
                source=EntrySource(
                    document="Purchase and Sale Agreement",
                    evidence="financing contingency",
                ),
            ),
            Entry(
                id="e2",
                type="observation",
                content="The SEC inquiry covers revenue recognition.",
                source=EntrySource(
                    document="SEC Inquiry Letter",
                    evidence="revenue recognition",
                ),
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert blackboard.find_entry("e1").status == "active"
    assert blackboard.find_entry("e2").status == "active"
    assert report["summary"]["entries_quarantined"] == 0


def test_document_name_aliases_strips_extensions_and_collapses():
    aliases = _document_name_aliases("purchase-and-sale-agreement.docx")
    assert "purchase-and-sale-agreement.docx" in aliases
    assert "purchase-and-sale-agreement" in aliases
    assert "purchaseandsaleagreement" in aliases

    aliases2 = _document_name_aliases("Purchase and Sale Agreement")
    assert "purchaseandsaleagreement" in aliases2
    assert aliases & aliases2


def test_source_document_is_valid_fuzzy():
    valid = _document_name_aliases("sec-inquiry-letter.docx")
    assert source_document_is_valid("SEC Inquiry Letter", valid)
    assert source_document_is_valid("sec-inquiry-letter.docx", valid)
    assert source_document_is_valid("sec_inquiry_letter", valid)
    assert not source_document_is_valid("totally-unrelated.docx", valid)


def test_synthetic_source_does_not_match_real_files():
    assert _is_synthetic_source("user_prompt")
    assert _is_synthetic_source("cross_cutting")
    assert _is_synthetic_source("User Prompt")
    assert not _is_synthetic_source("prompt.pdf")
    assert not _is_synthetic_source("task.docx")
    assert not _is_synthetic_source("user_prompt.docx")
    assert not _is_synthetic_source("instruction.xlsx")


def test_legal_citations_recognized_as_synthetic():
    assert _is_synthetic_source("ORS 659A.370")
    assert _is_synthetic_source("A.R.S. § 14-3933")
    assert _is_synthetic_source("20 CFR 656.17")
    assert _is_synthetic_source("N.C. Gen. Stat. § 66-157")
    assert _is_synthetic_source("Treas. Reg. § 1.170A-14(g)(6)(ii)")
    assert _is_synthetic_source("755 ILCS 5/18-10")
    assert _is_synthetic_source("28 U.S.C. § 1826")
    assert not _is_synthetic_source("ops_report.md")
    assert not _is_synthetic_source("Incident Report Q3")
    assert not _is_synthetic_source("USC Lease.pdf")
    assert not _is_synthetic_source("CFR Analysis.xlsx")
    assert not _is_synthetic_source("ARS aging.xlsx")
    assert not _is_synthetic_source("ORS report.md")


def test_source_custody_quarantines_fake_file_named_like_synthetic(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SOURCE_CUSTODY_AUDIT_ONLY", "0")
    blackboard = Blackboard(
        task_instruction="Analyze docs.",
        output_dir=str(tmp_path),
        documents=[DocumentStatus(id="d1", name="real-doc.docx")],
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Found something in a fake file.",
                source=EntrySource(document="prompt.pdf", evidence="fake"),
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert blackboard.entries[0].status == "source_quarantined"
    assert report["summary"]["entries_quarantined"] == 1


def test_source_custody_disabled_skips_quarantine(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ENABLE_SOURCE_CUSTODY", "0")
    blackboard = Blackboard(
        task_instruction="Analyze docs.",
        output_dir=str(tmp_path),
        documents=[DocumentStatus(id="d1", name="real-doc.docx")],
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Found something in a fake file.",
                source=EntrySource(document="fake-doc.pdf", evidence="fake"),
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert blackboard.entries[0].status == "active"
    assert report.get("disabled") is True
    assert report["summary"]["entries_quarantined"] == 0


def test_source_custody_audit_only_logs_but_preserves_status(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SOURCE_CUSTODY_AUDIT_ONLY", "1")
    blackboard = Blackboard(
        task_instruction="Analyze docs.",
        output_dir=str(tmp_path),
        documents=[DocumentStatus(id="d1", name="real-doc.docx")],
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Found something in a fake file.",
                source=EntrySource(document="fake-doc.pdf", evidence="fake"),
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert blackboard.entries[0].status == "active"
    assert report["audit_only"] is True
    assert report["summary"]["entries_flagged"] == 1
    assert report["summary"]["entries_quarantined"] == 0


def test_default_is_quarantine(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_SOURCE_CUSTODY_AUDIT_ONLY", raising=False)
    blackboard = Blackboard(
        task_instruction="Analyze docs.",
        output_dir=str(tmp_path),
        documents=[DocumentStatus(id="d1", name="real-doc.docx")],
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Found something in a fake file.",
                source=EntrySource(document="fake-doc.pdf", evidence="fake"),
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert blackboard.entries[0].status == "source_quarantined", "Default should quarantine"
    assert report["audit_only"] is False
    assert report["summary"]["entries_flagged"] == 0
    assert report["summary"]["entries_quarantined"] == 1


def test_compound_source_with_legal_citation_still_checks_parts(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SOURCE_CUSTODY_AUDIT_ONLY", "0")
    blackboard = Blackboard(
        task_instruction="Analyze compliance.",
        output_dir=str(tmp_path),
        documents=[DocumentStatus(id="d1", name="real-doc.docx")],
        entries=[
            Entry(
                id="e1",
                type="observation",
                content="Per 20 CFR 656.17 and Hallucinated Report.",
                source=EntrySource(
                    document="20 CFR 656.17; Hallucinated Report",
                    evidence="some text",
                ),
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert report["items"][0]["invalid_documents"] == ["Hallucinated Report"]


def test_mentioned_invalid_documents_requires_word_boundary():
    entry = Entry(
        id="e1", type="observation",
        content="All parties installed the software actually.",
        source=EntrySource(document="ops_report.md", evidence=""),
    )
    result = _mentioned_invalid_documents(entry, {"All"})
    assert result == [], "Short names (<4 chars) should be skipped"

    result2 = _mentioned_invalid_documents(entry, {"installed"})
    assert "installed" in result2, "Longer names matching on word boundary should hit"

    entry2 = Entry(
        id="e2", type="observation",
        content="The Formal Default Notice covers obligations.",
        source=EntrySource(document="ops_report.md", evidence=""),
    )
    result3 = _mentioned_invalid_documents(entry2, {"formal default notice"})
    assert "formal default notice" in result3


def test_cascade_does_not_quarantine_valid_direct_source_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SOURCE_CUSTODY_AUDIT_ONLY", "0")
    blackboard = Blackboard(
        task_instruction="Analyze docs.",
        output_dir=str(tmp_path),
        documents=[
            DocumentStatus(id="d1", name="ops_report.md"),
            DocumentStatus(id="d2", name="summary.md"),
        ],
        entries=[
            Entry(
                id="e1", type="observation",
                content="Fake Report says X.",
                source=EntrySource(document="Fake Report", evidence="X"),
            ),
            Entry(
                id="e2", type="analysis",
                content="Cross-ref with Fake Report shows discrepancy in ops_report.md data.",
                source=EntrySource(document="ops_report.md", evidence="discrepancy"),
            ),
        ],
    )

    report = enforce_source_custody(blackboard, "test")

    assert blackboard.find_entry("e1").status == "source_quarantined"
    assert blackboard.find_entry("e2").status == "active", \
        "Entry with valid direct source should survive cascade even if mentioning invalid doc"


def test_cascade_cap_limits_quarantine_count(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SOURCE_CUSTODY_AUDIT_ONLY", "0")
    entries = [
        Entry(
            id=f"e{i}", type="observation",
            content=f"Entry {i} references Hallucinated Document.",
            source=EntrySource(document="cross_cutting", evidence=""),
            supports_entries=["e0"],
        )
        for i in range(1, 50)
    ]
    entries.insert(0, Entry(
        id="e0", type="observation",
        content="From hallucinated source.",
        source=EntrySource(document="Hallucinated Document", evidence=""),
    ))

    blackboard = Blackboard(
        task_instruction="Test cascade cap.",
        output_dir=str(tmp_path),
        documents=[DocumentStatus(id="d1", name="real.md")],
        entries=entries,
    )

    report = enforce_source_custody(blackboard, "test")

    quarantined = sum(1 for e in blackboard.entries if e.status == "source_quarantined")
    assert quarantined <= max(20, int(50 * 0.15)) + 1, \
        f"Cascade cap should limit quarantine to ~15% of active entries, got {quarantined}"
