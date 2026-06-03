from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_xlsx(path: Path) -> tuple[str, dict]:
    try:
        sheets = pd.read_excel(path, sheet_name=None)
    except Exception as e:
        return f"(error reading {path.name}: {e})", {}

    parts = []
    structured: dict = {"sheets": {}}
    for sheet_name, df in sheets.items():
        parts.append(f"=== Sheet: {sheet_name} ===")
        parts.append(df.to_string(index=False))
        structured["sheets"][sheet_name] = {
            "columns": list(df.columns),
            "row_count": len(df),
        }
    return "\n".join(parts), structured
