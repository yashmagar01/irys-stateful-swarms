"""Tests for provenance architecture: source_span, quote matching, hydrated analyze."""
import json
from dataclasses import dataclass, field

import pytest

from src.loop.state import Board, Claim, Source, Target
from src.loop.actions import (
    _find_quote_span,
    _normalize_with_map,
    _source_claims_for_hydration,
    build_evidence_context,
)


# --- helpers ---

@dataclass
class _FakeDoc:
    text: str = ""


def _make_source(name: str, text: str) -> Source:
    return Source(id=name, name=name, kind="document", _doc=_FakeDoc(text=text))


def _make_board(instruction: str = "test task") -> Board:
    return Board(instruction=instruction)


# --- Claim.source_span serialization ---

def test_source_span_to_dict():
    c = Claim(content="x", source_span=(3, 9))
    d = c.to_dict()
    assert d["source_span"] == [3, 9]


def test_source_span_to_dict_none():
    c = Claim(content="x")
    d = c.to_dict()
    assert d["source_span"] is None


def test_source_span_round_trip():
    c = Claim(content="x", source_span=(100, 200))
    d = c.to_dict()
    c2 = Claim.from_dict(d)
    assert c2.source_span == (100, 200)


def test_source_span_from_dict_legacy():
    c = Claim.from_dict({"content": "legacy claim"})
    assert c.source_span is None
    assert c.content == "legacy claim"


# --- dedup with spans ---

def test_dedup_preserves_same_content_different_spans():
    board = _make_board()
    c1 = Claim(content="interest rate is 4.5%", kind="observation",
               source_doc="doc.pdf", source_span=(10, 30))
    c2 = Claim(content="interest rate is 4.5%", kind="observation",
               source_doc="doc.pdf", source_span=(500, 520))
    assert board.add_claim(c1) is True
    assert board.add_claim(c2) is True
    assert len(board.claims) == 2


def test_dedup_blocks_exact_duplicate_span():
    board = _make_board()
    c1 = Claim(content="interest rate is 4.5%", kind="observation",
               source_doc="doc.pdf", source_span=(10, 30))
    c2 = Claim(content="interest rate is 4.5%", kind="observation",
               source_doc="doc.pdf", source_span=(10, 30))
    assert board.add_claim(c1) is True
    assert board.add_claim(c2) is False


def test_dedup_derived_claims_unchanged():
    board = _make_board()
    c1 = Claim(content="conclusion A", kind="analysis")
    c2 = Claim(content="conclusion A", kind="analysis")
    assert board.add_claim(c1) is True
    assert board.add_claim(c2) is False


# --- quote matching ---

def test_find_quote_span_exact():
    text = "Alpha interest rate is 4.5% per annum. Omega"
    span = _find_quote_span(text, "interest rate is 4.5% per annum")
    assert span is not None
    start, end = span
    assert text[start:end] == "interest rate is 4.5% per annum"


def test_find_quote_span_with_offset():
    text = "Alpha interest rate is 4.5% per annum."
    span = _find_quote_span(text, "interest rate is 4.5%", base_offset=1000)
    assert span is not None
    assert span[0] >= 1000


def test_find_quote_span_whitespace_fallback():
    text = "interest rate is 4.5%\n    per annum"
    quote = "interest rate is 4.5% per annum"
    span = _find_quote_span(text, quote)
    assert span is not None
    start, end = span
    assert " ".join(text[start:end].split()) == quote


def test_find_quote_span_case_fallback():
    text = "The INTEREST RATE is 4.5%"
    quote = "the interest rate is 4.5%"
    span = _find_quote_span(text, quote)
    assert span is not None


def test_find_quote_span_missing():
    text = "nothing relevant here"
    span = _find_quote_span(text, "not present at all")
    assert span is None


def test_find_quote_span_empty():
    assert _find_quote_span("some text", "") is None
    assert _find_quote_span("", "some quote") is None


# --- recursive hydration ---

def test_hydration_direct_source_claim():
    board = _make_board()
    src = _make_source("doc.pdf", "0123456789" * 100)
    board.add_source(src)
    c1 = Claim(content="fact", source_doc="doc.pdf", source_span=(10, 50))
    board.add_claim(c1)

    context, stats = build_evidence_context(board, [c1])
    assert stats["merged_windows"] == 1
    assert c1.id in stats["hydrated_claim_ids"]
    assert "0123456789" in context


def test_hydration_derived_follows_support():
    board = _make_board()
    src = _make_source("doc.pdf", "ABCDEFGHIJKLMNOP" * 100)
    board.add_source(src)
    c1 = Claim(content="raw fact", source_doc="doc.pdf", source_span=(0, 16))
    board.add_claim(c1)
    c2 = Claim(content="derived conclusion", kind="analysis", support_refs=[c1.id])
    board.add_claim(c2)

    context, stats = build_evidence_context(board, [c2])
    assert stats["merged_windows"] == 1
    assert c1.id in stats["hydrated_claim_ids"]
    assert "ABCDEFGHIJKLMNOP" in context


def test_hydration_derived_of_derived():
    board = _make_board()
    src = _make_source("doc.pdf", "X" * 200)
    board.add_source(src)
    c1 = Claim(content="raw", source_doc="doc.pdf", source_span=(0, 50))
    board.add_claim(c1)
    c2 = Claim(content="mid", kind="analysis", support_refs=[c1.id])
    board.add_claim(c2)
    c3 = Claim(content="top", kind="analysis", support_refs=[c2.id])
    board.add_claim(c3)

    context, stats = build_evidence_context(board, [c3])
    assert stats["merged_windows"] == 1
    assert c1.id in stats["hydrated_claim_ids"]


def test_hydration_cycle_protection():
    board = _make_board()
    c1 = Claim(content="a", kind="analysis")
    board.add_claim(c1)
    c2 = Claim(content="b", kind="analysis", support_refs=[c1.id])
    board.add_claim(c2)
    c1.support_refs = [c2.id]

    context, stats = build_evidence_context(board, [c1])
    assert stats["merged_windows"] == 0


def test_hydration_overlap_merge():
    board = _make_board()
    text = "A" * 200
    src = _make_source("doc.pdf", text)
    board.add_source(src)
    c1 = Claim(content="fact1", source_doc="doc.pdf", source_span=(10, 50))
    c2 = Claim(content="fact2", source_doc="doc.pdf", source_span=(30, 80))
    board.add_claim(c1)
    board.add_claim(c2)

    context, stats = build_evidence_context(board, [c1, c2])
    assert stats["merged_windows"] == 1
    assert stats["source_windows"] == 2


def test_hydration_no_overlap_separate_blocks():
    board = _make_board()
    text = "A" * 200
    src = _make_source("doc.pdf", text)
    board.add_source(src)
    c1 = Claim(content="fact1", source_doc="doc.pdf", source_span=(10, 30))
    c2 = Claim(content="fact2", source_doc="doc.pdf", source_span=(100, 150))
    board.add_claim(c1)
    board.add_claim(c2)

    context, stats = build_evidence_context(board, [c1, c2])
    assert stats["merged_windows"] == 2


def test_hydration_multi_source():
    board = _make_board()
    src1 = _make_source("doc1.pdf", "first document " * 100)
    src2 = _make_source("doc2.pdf", "second document " * 100)
    board.add_source(src1)
    board.add_source(src2)
    c1 = Claim(content="f1", source_doc="doc1.pdf", source_span=(0, 15))
    c2 = Claim(content="f2", source_doc="doc2.pdf", source_span=(0, 16))
    board.add_claim(c1)
    board.add_claim(c2)

    context, stats = build_evidence_context(board, [c1, c2])
    assert stats["merged_windows"] == 2
    assert "first document" in context
    assert "second document" in context


def test_hydration_missing_source():
    board = _make_board()
    c1 = Claim(content="orphan", source_doc="missing.pdf", source_span=(0, 10))
    board.add_claim(c1)

    context, stats = build_evidence_context(board, [c1])
    assert stats["missing_source"] == 1
    assert stats["merged_windows"] == 0


def test_hydration_no_span_counted():
    board = _make_board()
    c1 = Claim(content="no span", kind="analysis")
    board.add_claim(c1)

    context, stats = build_evidence_context(board, [c1])
    assert stats["missing_span"] == 1
