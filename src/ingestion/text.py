from __future__ import annotations

import json
from pathlib import Path


def read_text(path: Path) -> tuple[str, dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="latin-1")
        except Exception:
            return f"(binary file: {path.name})", {}

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
            pretty = json.dumps(data, indent=2)
            return pretty, {"json_data": data}
        except json.JSONDecodeError:
            pass

    return text, {}
