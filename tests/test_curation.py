import json
import threading
import time

from src.swarm.blackboard import Blackboard
from src.swarm.curation import curate_entries
from src.swarm.models import Entry, EntrySource, ModelResult


class CurationFakeCaller:
    def __init__(self):
        self.lock = threading.Lock()
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        with self.lock:
            self.prompts.append(prompt)
        if '"doc-a.docx"' in prompt:
            text = '{"must_include":[{"entry_id":"a","importance":"critical","section":"A","summary":"A fact"}]}'
            tokens_in = 11
        elif '"doc-b.docx"' in prompt:
            time.sleep(0.02)
            text = '{"must_include":[{"entry_id":"b","importance":"critical","section":"B","summary":"B fact"}]}'
            tokens_in = 13
        else:
            text = '{"must_include":[{"entry_id":"c","importance":"critical","section":"C","summary":"C fact"}]}'
            tokens_in = 17
        return ModelResult(
            text=text,
            tokens_input=tokens_in,
            tokens_output=3,
            tokens_total=tokens_in + 3,
            model="fake-model",
            latency_ms=1,
        )


def test_parallel_curation_preserves_cluster_order_and_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_CURATION_WORKERS", "3")
    blackboard = Blackboard(
        task_instruction="Draft a compliance manual.",
        output_dir=str(tmp_path),
        entries=[
            Entry(id="a", content="A content", source=EntrySource(document="doc-a.docx")),
            Entry(id="b", content="B content", source=EntrySource(document="doc-b.docx")),
            Entry(id="c", content="C content", source=EntrySource(document="doc-c.docx")),
        ],
    )
    caller = CurationFakeCaller()

    items, tokens = curate_entries(blackboard, caller)
    blackboard.add_tokens_from_last_call(tokens)

    assert [item["entry_id"] for item in items] == ["a", "b", "c"]
    assert tokens == (11 + 3) + (13 + 3) + (17 + 3)
    assert blackboard.tokens_input == 11 + 13 + 17
    assert blackboard.tokens_output == 9
    assert blackboard.cost_by_model["fake-model"]["calls"] == 3

    progress_path = tmp_path / "swarm" / "curation_progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["completed_clusters"] == 3
    assert progress["total_clusters"] == 3
