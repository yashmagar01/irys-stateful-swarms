"""Tests for the MCP server tool registration and input validation."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_server_registers_tools():
    from src.mcp_server import mcp
    tools = list(mcp._tool_manager._tools.keys())
    assert "irys_ask" in tools
    assert "irys_supported_formats" in tools


def test_supported_formats_returns_extensions():
    from src.mcp_server import irys_supported_formats
    result = irys_supported_formats()
    assert ".pdf" in result
    assert ".docx" in result
    assert ".xlsx" in result


def test_ask_missing_api_key():
    from src.mcp_server import irys_ask
    with patch.dict("os.environ", {}, clear=True):
        result = irys_ask("test question", "/nonexistent")
    assert "API key" in result or "GEMINI_API_KEY" in result


def test_ask_nonexistent_path():
    from src.mcp_server import irys_ask
    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}):
        result = irys_ask("test", "/path/that/does/not/exist")
    assert "does not exist" in result


def test_ask_unsupported_file(tmp_path):
    from src.mcp_server import irys_ask
    bad_file = tmp_path / "data.xyz"
    bad_file.write_text("hello")
    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}):
        result = irys_ask("test", str(bad_file))
    assert "unsupported" in result.lower()


def test_ask_empty_directory(tmp_path):
    from src.mcp_server import irys_ask
    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}):
        result = irys_ask("test", str(tmp_path))
    assert "no supported documents" in result.lower()


def test_ask_valid_file_calls_swarm(tmp_path):
    from src.mcp_server import irys_ask

    doc_file = tmp_path / "report.txt"
    doc_file.write_text("Revenue was $10M in Q3.")

    mock_bb = MagicMock()
    mock_bb.total_tokens_used = 5000

    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}), \
         patch("src.swarm.run_swarm", return_value=("Analysis complete.", mock_bb)) as mock_swarm, \
         patch("src.providers.gemini.GeminiCaller"):
        result = irys_ask("What was Q3 revenue?", str(doc_file))

    mock_swarm.assert_called_once()
    assert "Analysis complete." in result
    assert "5,000 tokens" in result


def test_ask_json_format(tmp_path):
    from src.mcp_server import irys_ask

    doc_file = tmp_path / "report.txt"
    doc_file.write_text("Revenue was $10M.")

    mock_bb = MagicMock()
    mock_bb.total_tokens_used = 3000

    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}), \
         patch("src.swarm.run_swarm", return_value=("Answer text.", mock_bb)), \
         patch("src.providers.gemini.GeminiCaller"):
        result = irys_ask("Revenue?", str(doc_file), output_format="json")

    data = json.loads(result)
    assert data["answer"] == "Answer text."
    assert data["tokens_used"] == 3000


def test_ask_swarm_error(tmp_path):
    from src.mcp_server import irys_ask

    doc_file = tmp_path / "report.txt"
    doc_file.write_text("Content.")

    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake"}), \
         patch("src.swarm.run_swarm", side_effect=RuntimeError("model crashed")), \
         patch("src.providers.gemini.GeminiCaller"):
        result = irys_ask("test", str(doc_file))

    assert "Swarm error" in result
    assert "model crashed" in result
