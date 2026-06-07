"""Tests for the web search module."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from src.swarm.web_search import (
    _is_safe_url,
    search_web,
    run_web_searches,
    web_search_enabled,
)


class TestSSRF:
    def test_blocks_localhost(self):
        assert not _is_safe_url("http://localhost/evil")

    def test_blocks_127(self):
        assert not _is_safe_url("http://127.0.0.1:8080/path")

    def test_blocks_metadata(self):
        assert not _is_safe_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_metadata_google(self):
        assert not _is_safe_url("http://metadata.google.internal/v1/")

    def test_blocks_private_ip(self):
        assert not _is_safe_url("http://192.168.1.1/admin")
        assert not _is_safe_url("http://10.0.0.1/")

    def test_blocks_ipv6_loopback(self):
        assert not _is_safe_url("http://[::1]/")

    def test_allows_public(self):
        assert _is_safe_url("https://www.google.com")
        assert _is_safe_url("https://en.wikipedia.org/wiki/Python")

    def test_blocks_empty(self):
        assert not _is_safe_url("")
        assert not _is_safe_url("not-a-url")


class TestWebSearchEnabled:
    def test_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            assert not web_search_enabled()

    def test_enabled_with_1(self):
        with patch.dict("os.environ", {"SWARM_WEB_SEARCH": "1"}):
            assert web_search_enabled()

    def test_enabled_with_true(self):
        with patch.dict("os.environ", {"SWARM_WEB_SEARCH": "true"}):
            assert web_search_enabled()

    def test_disabled_with_0(self):
        with patch.dict("os.environ", {"SWARM_WEB_SEARCH": "0"}):
            assert not web_search_enabled()


class TestSearchWeb:
    @patch("src.swarm.web_search._ddg_search")
    def test_returns_formatted_results(self, mock_ddg):
        mock_ddg.return_value = [
            {"title": "Test", "href": "https://example.com", "body": "snippet"},
        ]
        results = search_web("test query", max_results=1)
        assert len(results) == 1
        assert results[0]["title"] == "Test"
        assert results[0]["url"] == "https://example.com"

    @patch("src.swarm.web_search._ddg_search")
    def test_handles_failure(self, mock_ddg):
        mock_ddg.side_effect = Exception("network error")
        results = search_web("test")
        assert results == []


class TestRunWebSearches:
    @patch("src.swarm.web_search.search_and_browse")
    def test_combines_multiple_queries(self, mock_browse):
        mock_browse.side_effect = ["Result A", "Result B"]
        text = run_web_searches(["query1", "query2"])
        assert "Result A" in text
        assert "Result B" in text
        assert mock_browse.call_count == 2

    def test_empty_queries(self):
        assert run_web_searches([]) == ""

    @patch("src.swarm.web_search.search_and_browse")
    def test_caps_at_max(self, mock_browse):
        mock_browse.return_value = "result"
        run_web_searches([f"q{i}" for i in range(20)])
        assert mock_browse.call_count == 5
