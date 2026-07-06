"""Shared constants for the irys-stateful-swarms package."""
from __future__ import annotations

# Cost per 1M tokens (USD) — used in runner.py and bench.py
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00},
    "claude-fable-5": {"input": 10.00, "output": 50.00},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "claude-opus-4-7": {"input": 5.00, "output": 25.00},
    "claude-opus-4-6": {"input": 5.00, "output": 25.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "gpt-5.5": {"input": 2.00, "output": 12.00},
    "gpt-5.4": {"input": 1.50, "output": 8.00},
}

DEFAULT_PRICING: dict[str, float] = {"input": 0.25, "output": 1.50}
