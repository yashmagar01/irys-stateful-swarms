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
    assert "--no-reviewer" in result.stdout
    assert "--verbose" in result.stdout


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
        env={**_clean_env()},
    )
    assert result.returncode != 0
    assert "does not exist" in result.stderr or result.returncode == 1


def test_ask_empty_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "ask", "test", "--docs", str(tmp_path)],
        capture_output=True, text=True,
        env={**_clean_env()},
    )
    assert result.returncode != 0
    assert "no supported documents" in result.stderr.lower() or result.returncode == 1


def test_ask_unsupported_file(tmp_path):
    bad_file = tmp_path / "test.xyz"
    bad_file.write_text("test")
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "ask", "test", "--docs", str(bad_file)],
        capture_output=True, text=True,
        env={**_clean_env()},
    )
    assert result.returncode != 0


def test_ask_missing_api_key(tmp_path):
    doc = tmp_path / "test.txt"
    doc.write_text("Some content.")
    env = _clean_env()
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "ask", "summarize", "--docs", str(doc)],
        capture_output=True, text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "api key" in result.stderr.lower() or "GEMINI_API_KEY" in result.stderr


def test_ask_valid_text_file_loads(tmp_path):
    doc = tmp_path / "test.txt"
    doc.write_text("This is a test document with some content.")
    env = _clean_env()
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "ask", "summarize", "--docs", str(doc)],
        capture_output=True, text=True, timeout=10,
        env=env,
    )
    # Fails on missing API key, which proves doc path validation passed
    assert result.returncode != 0
    assert "api key" in result.stderr.lower() or "GEMINI_API_KEY" in result.stderr


def _clean_env():
    """Return a minimal env that preserves PATH and SYSTEMROOT for subprocess."""
    import os
    env = {}
    for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PATHEXT", "COMSPEC",
                "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env
