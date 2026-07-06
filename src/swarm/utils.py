"""Shared utilities for the swarm package."""
from __future__ import annotations

import os


def _env_on(name: str) -> bool:
    """Return True if the named environment variable is set to a truthy value."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
