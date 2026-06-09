from src.swarm.placement_verifier import (
    evaluate_placements,
    get_repair_candidates,
)


def test_evaluate_placements_found_in_target():
    packet = [
        {"importance": "critical", "target_file": "memo.docx",
         "summary": "Revenue is $10,500,000",
         "verification_terms": ["$10,500,000"]},
    ]
    texts = {"memo.docx": "The company's revenue was $10,500,000 in FY2025."}
    results = evaluate_placements(packet, texts)
    assert len(results) == 1
    assert results[0]["found"] is True
    assert results[0]["failure_mode"] is None


def test_evaluate_placements_wrong_file():
    packet = [
        {"importance": "high", "target_file": "memo.docx",
         "summary": "Revenue $5M",
         "verification_terms": ["$5,000,000"]},
    ]
    texts = {
        "memo.docx": "No revenue data here.",
        "sheet.xlsx": "Revenue: $5,000,000",
    }
    results = evaluate_placements(packet, texts)
    assert len(results) == 1
    assert results[0]["found"] is False
    assert results[0]["failure_mode"] == "wrong_file"


def test_evaluate_placements_content_missing():
    packet = [
        {"importance": "critical", "target_file": "memo.docx",
         "summary": "Anti-dilution provisions",
         "verification_terms": ["anti-dilution"]},
    ]
    texts = {"memo.docx": "This memo covers liquidation preferences only."}
    results = evaluate_placements(packet, texts)
    assert len(results) == 1
    assert results[0]["found"] is False
    assert results[0]["failure_mode"] == "content_missing"


def test_evaluate_placements_skips_medium_importance():
    packet = [
        {"importance": "medium", "target_file": "memo.docx",
         "summary": "Minor detail",
         "verification_terms": ["minor"]},
    ]
    texts = {"memo.docx": "No minor detail here."}
    results = evaluate_placements(packet, texts)
    assert len(results) == 0


def test_get_repair_candidates_sorted_and_limited():
    placements = [
        {"found": False, "failure_mode": "content_missing",
         "importance": "high", "summary": "B"},
        {"found": False, "failure_mode": "content_missing",
         "importance": "critical", "summary": "A"},
        {"found": True, "failure_mode": None,
         "importance": "critical", "summary": "C"},
        {"found": False, "failure_mode": "no_verification_terms",
         "importance": "critical", "summary": "D"},
    ]
    candidates = get_repair_candidates(placements)
    assert len(candidates) == 2
    assert candidates[0]["importance"] == "critical"
    assert candidates[1]["importance"] == "high"


def test_evaluate_placements_no_verification_terms():
    packet = [
        {"importance": "critical", "target_file": "memo.docx",
         "summary": "", "verification_terms": []},
    ]
    texts = {"memo.docx": "Some content."}
    results = evaluate_placements(packet, texts)
    assert len(results) == 1
    assert results[0]["failure_mode"] == "no_verification_terms"
