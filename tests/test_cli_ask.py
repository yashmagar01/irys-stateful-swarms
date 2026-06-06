from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_ask_help_output():
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "ask", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "question" in result.stdout
    assert "--docs" in result.stdout
    assert "--format" in result.stdout


def test_ask_missing_docs_flag():
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "ask", "test question"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "required" in result.stderr.lower() or "docs" in result.stderr.lower()


def test_ask_nonexistent_path():
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "ask", "test", "--docs", "/nonexistent/path"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "does not exist" in result.stdout or result.returncode == 1


def test_ask_empty_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "ask", "test", "--docs", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "no supported documents" in result.stdout.lower() or result.returncode == 1


def test_ask_unsupported_file(tmp_path):
    bad_file = tmp_path / "test.xyz"
    bad_file.write_text("test")
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "ask", "test", "--docs", str(bad_file)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_ask_valid_text_file_loads(tmp_path):
    doc = tmp_path / "test.txt"
    doc.write_text("This is a test document with some content.")
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "ask", "summarize", "--docs", str(doc)],
        capture_output=True, text=True, timeout=10,
    )
    # Will print "Loaded 1 document" before attempting API calls
    assert "Loaded 1 document" in result.stdout
