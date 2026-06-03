from __future__ import annotations

from pathlib import Path

import pdfplumber


def read_pdf(path: Path) -> tuple[str, dict]:
    parts = []
    page_count = 0
    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
                for table in page.extract_tables():
                    for row in table:
                        parts.append("\t".join(cell if cell else "" for cell in row))
                    parts.append("")
    except Exception as e:
        return f"(error reading {path.name}: {e})", {}

    return "\n".join(parts), {"page_count": page_count}
