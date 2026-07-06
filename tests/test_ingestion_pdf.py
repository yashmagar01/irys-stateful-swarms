"""Unit tests for pdf ingestion (S-13).

Uses reportlab to generate a real-text PDF (not image-only), then verifies
that read_pdf() extracts text and page count correctly.

NOTE: read_pdf() uses pdfplumber. A scanned/image-only PDF will silently
return near-empty text — this suite deliberately uses a text-based PDF
to avoid that known gap (tracked separately as I-14).
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from src.ingestion.pdf import read_pdf

# reportlab is available since python-pptx/openpyxl are already deps;
# if reportlab is not installed we skip gracefully.
reportlab = pytest.importorskip("reportlab", reason="reportlab not installed")
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas as rl_canvas


def _make_pdf(path: Path, pages: list[str]) -> None:
    """Write a text-based PDF with one text string per page."""
    c = rl_canvas.Canvas(str(path), pagesize=LETTER)
    for text in pages:
        c.drawString(72, 720, text)
        c.showPage()
    c.save()


@pytest.fixture()
def single_page_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "single.pdf"
    _make_pdf(p, ["Hello PDF world"])
    return p


@pytest.fixture()
def multi_page_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "multi.pdf"
    _make_pdf(p, ["First page content", "Second page content", "Third page content"])
    return p


class TestReadPdfText:
    def test_text_extracted_from_single_page(self, single_page_pdf):
        text, meta = read_pdf(single_page_pdf)
        assert "Hello PDF world" in text

    def test_page_count_single(self, single_page_pdf):
        _, meta = read_pdf(single_page_pdf)
        assert meta.get("page_count") == 1

    def test_text_extracted_from_all_pages(self, multi_page_pdf):
        text, _ = read_pdf(multi_page_pdf)
        assert "First page content" in text
        assert "Second page content" in text
        assert "Third page content" in text

    def test_page_count_multi(self, multi_page_pdf):
        _, meta = read_pdf(multi_page_pdf)
        assert meta.get("page_count") == 3

    def test_returns_string_and_dict(self, single_page_pdf):
        text, meta = read_pdf(single_page_pdf)
        assert isinstance(text, str)
        assert isinstance(meta, dict)


class TestReadPdfErrorHandling:
    def test_nonexistent_file_returns_error_string(self, tmp_path):
        bad = tmp_path / "missing.pdf"
        text, meta = read_pdf(bad)
        assert "error" in text.lower()
        assert meta == {}
