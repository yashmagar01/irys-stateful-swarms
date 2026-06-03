from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..swarm.models import Document
from .docx import read_docx
from .pdf import read_pdf
from .xlsx import read_xlsx
from .pptx import read_pptx
from .text import read_text
from .eml import read_eml

SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".json",
    ".docx", ".xlsx", ".pptx",
    ".pdf", ".eml",
}

_READERS = {
    ".docx": read_docx,
    ".xlsx": read_xlsx,
    ".pptx": read_pptx,
    ".pdf": read_pdf,
    ".eml": read_eml,
}


def ingest_file(path: Path) -> Document:
    suffix = path.suffix.lower()
    name = path.name
    size_bytes = path.stat().st_size
    doc_id = f"doc_{hashlib.md5(name.encode()).hexdigest()[:8]}"

    reader = _READERS.get(suffix, read_text)
    text, structured = reader(path)

    return Document(
        id=doc_id,
        name=name,
        text=text,
        size_bytes=size_bytes,
        metadata={"extension": suffix, "path": str(path)},
        structured=structured,
    )


def ingest_directory(directory: Path) -> list[Document]:
    docs = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            docs.append(ingest_file(path))
    return docs


def discover_documents(task_dir: Path) -> list[Document]:
    for subdir_name in ("source_documents", "input_documents", "documents", "docs"):
        subdir = task_dir / subdir_name
        if subdir.is_dir():
            return ingest_directory(subdir)
    return [
        ingest_file(p)
        for p in sorted(task_dir.iterdir())
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and p.name not in ("task.json", "instruction.md", "scores.json", "status.json")
    ]
