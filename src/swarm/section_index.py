from __future__ import annotations

import re

from .models import SectionIndex, SectionRange


def build_section_index(text: str) -> SectionIndex:
    sections: list[SectionRange] = []
    lines = text.split("\n")
    current_offset = 0
    patterns = [
        (1, r"^#{1}\s+(.+)$"),
        (1, r"^(ARTICLE|Article)\s+[IVXLCDM\d]+"),
        (2, r"^(SECTION|Section)\s+[\d.]+"),
        (2, r"^#{2}\s+(.+)$"),
        (3, r"^#{3}\s+(.+)$"),
        (3, r"^\s*\d+\.\d+\s+[A-Z]"),
        (1, r"^(EXHIBIT|Exhibit|SCHEDULE|Schedule)\s+[A-Z\d]"),
        (1, r"^#\s*Page\s+\d+"),
    ]
    for line in lines:
        stripped = line.strip()
        for level, pattern in patterns:
            if re.match(pattern, stripped):
                if sections:
                    sections[-1] = SectionRange(
                        sections[-1].name, sections[-1].start_char,
                        current_offset, sections[-1].level,
                    )
                sections.append(SectionRange(
                    name=stripped.lstrip("#").strip()[:120],
                    start_char=current_offset,
                    end_char=len(text), level=level,
                ))
                break
        current_offset += len(line) + 1
    if not sections:
        sections.append(SectionRange("Full Document", 0, len(text), 1))
    return SectionIndex(sections=sections)


def resolve_section_text(text: str, index: SectionIndex, section_name: str,
                         max_chars: int = 24000) -> str:
    name_lower = section_name.lower().strip()
    for s in index.sections:
        if s.name.lower().strip() == name_lower:
            return text[s.start_char:s.end_char][:max_chars]
    for s in index.sections:
        if name_lower in s.name.lower() or s.name.lower() in name_lower:
            return text[s.start_char:s.end_char][:max_chars]
    return text[:max_chars]
