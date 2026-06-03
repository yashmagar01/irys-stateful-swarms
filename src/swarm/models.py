from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Protocol


# --- ID Generation (thread-safe, sequential) ---

_id_lock = threading.Lock()
_entry_counter = 0
_signal_counter = 0


def gen_entry_id() -> str:
    global _entry_counter
    with _id_lock:
        _entry_counter += 1
        return f"e{_entry_counter}"


def gen_signal_id() -> str:
    global _signal_counter
    with _id_lock:
        _signal_counter += 1
        return f"s{_signal_counter}"


def reset_id_counters() -> None:
    global _entry_counter, _signal_counter
    with _id_lock:
        _entry_counter = 0
        _signal_counter = 0


# --- Model Interface ---

@dataclass
class ModelResult:
    text: str
    tokens_input: int
    tokens_output: int
    tokens_total: int
    model: str
    latency_ms: int


class ModelCaller(Protocol):
    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        ...


# --- Document Model ---

@dataclass
class Document:
    id: str
    name: str
    text: str
    size_bytes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    structured: dict[str, Any] = field(default_factory=dict)


# --- Task Model ---

@dataclass
class Task:
    instruction: str
    documents: list[Document]
    metadata: dict[str, Any] = field(default_factory=dict)
    output_dir: str = ""


# --- Section Index ---

@dataclass
class SectionRange:
    name: str
    start_char: int
    end_char: int
    level: int


@dataclass
class SectionIndex:
    sections: list[SectionRange] = field(default_factory=list)


# --- Epistemic Status ---

@dataclass
class EpistemicStatus:
    classification: str = "inference"
    source_credibility: str = "unknown"
    motivation: str = ""
    neutral_restatement: str | None = None


# --- Blackboard Entry ---

@dataclass
class EntrySource:
    document: str | None = None
    section: str | None = None
    evidence: str = ""


@dataclass
class WorkerRecord:
    worker_id: str = ""
    description: str = ""
    iteration: int = 0


@dataclass
class Entry:
    id: str = ""
    type: str = "observation"
    content: str = ""
    source: EntrySource | None = None
    epistemic: EpistemicStatus | None = None
    created_by: WorkerRecord = field(default_factory=WorkerRecord)
    confidence: float = 0.5
    verified: bool | None = None
    tags: list[str] = field(default_factory=list)
    status: str = "active"
    opens_questions: list[str] = field(default_factory=list)
    supports_entries: list[str] = field(default_factory=list)
    contradicts_entries: list[str] = field(default_factory=list)
    supersedes_entries: list[str] = field(default_factory=list)
    addresses_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "content": self.content,
            "source": {"document": self.source.document, "section": self.source.section,
                        "evidence": self.source.evidence} if self.source else None,
            "epistemic": {"classification": self.epistemic.classification,
                           "credibility": self.epistemic.source_credibility,
                           "motivation": self.epistemic.motivation} if self.epistemic else None,
            "created_by": {"worker_id": self.created_by.worker_id,
                            "description": self.created_by.description,
                            "iteration": self.created_by.iteration},
            "confidence": self.confidence, "verified": self.verified,
            "tags": self.tags, "status": self.status,
            "supports": self.supports_entries, "contradicts": self.contradicts_entries,
            "supersedes": self.supersedes_entries,
            "addresses_signals": self.addresses_signals,
        }


# --- Signal ---

@dataclass
class Signal:
    id: str = ""
    type: str = "question"
    content: str = ""
    origin_entry: str = ""
    priority: str = "medium"
    status: str = "open"
    addressed_by: str | None = None
    iteration_created: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "content": self.content,
            "origin_entry": self.origin_entry, "priority": self.priority,
            "status": self.status, "addressed_by": self.addressed_by,
        }


# --- Document Status (runtime tracking) ---

@dataclass
class DocumentStatus:
    id: str = ""
    name: str = ""
    size_bytes: int = 0
    headings: list[str] = field(default_factory=list)
    structural_profile: dict | None = None
    read_status: str = "unread"
    sections_read: list[str] = field(default_factory=list)
    sections_unread: list[str] = field(default_factory=list)
    section_index: SectionIndex | None = field(default=None, repr=False)
    text: str = field(default="", repr=False)

    def mark_section_read(self, section: str) -> None:
        if section not in self.sections_read:
            self.sections_read.append(section)
        if section in self.sections_unread:
            self.sections_unread.remove(section)
        self.read_status = "fully_read" if not self.sections_unread else "partially_read"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "size_bytes": self.size_bytes,
            "headings": self.headings, "structural_profile": self.structural_profile,
            "read_status": self.read_status,
            "sections_read": self.sections_read, "sections_unread": self.sections_unread,
        }


# --- Worker Output ---

@dataclass
class WorkerOutput:
    entries: list[Entry]
    tokens_used: int
    tokens_input: int
    tokens_output: int
    model: str
    worker_id: str
    task: dict
    sections_read: list[tuple[str, str]]
