from src.swarm.blackboard import Blackboard
from src.swarm.models import ModelResult
from src.swarm.seed import generate_seed, seed_to_signals
from src.swarm import _initial_reading_seed_guidance


class FakeCaller:
    def __init__(self, text):
        self.text = text
        self.prompts = []
        self.max_tokens = []

    def complete(self, prompt: str, *, max_tokens: int = 8192,
                 temperature: float = 0.05, json_mode: bool = True) -> ModelResult:
        self.prompts.append(prompt)
        self.max_tokens.append(max_tokens)
        return ModelResult(
            text=self.text,
            tokens_input=len(prompt) // 4,
            tokens_output=len(self.text) // 4,
            tokens_total=(len(prompt) + len(self.text)) // 4,
            model="fake",
            latency_ms=1,
        )


def test_generate_seed_requests_unstructured_context_enrichment():
    blackboard = Blackboard(task_instruction="Analyze a payment acquisition.")
    caller = FakeCaller(
        '{"key_questions":["Which company was acquired?"],'
        '"extraction_focus":[],"analytical_framework":"",'
        '"context_enrichment":"Identify acquirer, target, deal terms, and strategic impact.",'
        '"completeness_criteria":[]}'
    )

    _, tokens = generate_seed(blackboard, caller)

    assert tokens > 0
    assert caller.max_tokens == [4096]
    prompt = caller.prompts[0]
    assert "CONTEXT ENRICHMENT NOTES" in prompt
    assert "Do NOT build a fixed ontology, formal graph, or hard-coded entity schema" in prompt
    assert "Fold the best of this thinking into the KEY QUESTIONS" in prompt
    assert '"context_enrichment": "plain-language notes' in prompt


def test_seed_to_signals_does_not_materialize_context_as_blackboard_entries():
    blackboard = Blackboard(task_instruction="Draft a response.")
    seed = {
        "key_questions": ["What pleading is required?"],
        "extraction_focus": [
            {"document": "complaint.docx", "focus": "Identify asserted claims."}
        ],
        "context_enrichment": (
            "The task involves a dispute, parties, claims, procedural posture, "
            "and unknown pleading requirements."
        ),
    }

    seed_to_signals(seed, blackboard)

    assert len(blackboard.entries) == 0
    assert [signal.type for signal in blackboard.signals] == ["question", "read_request"]
    assert blackboard.signals[0].content == "What pleading is required?"


def test_initial_reading_guidance_passes_seed_context_to_matching_document():
    long_context = "Map acquirer, target, payments market, customer base, and unknown deal economics. " * 40
    seed = {
        "key_questions": [
            "Which payments company was acquired?",
            "How does the acquisition change product strategy?",
        ],
        "extraction_focus": [
            {"document": "announcement.pdf", "focus": "Extract target, price, timing, and rationale."},
            {"document": "unrelated.pdf", "focus": "This should not appear."},
        ],
        "analytical_framework": "Assess the acquisition through revenue, product, and market impact.",
        "context_enrichment": long_context,
        "completeness_criteria": ["Identify the target and deal value."],
    }

    guidance = _initial_reading_seed_guidance(seed, "announcement.pdf")

    assert "Which payments company was acquired?" in guidance
    assert "Extract target, price, timing, and rationale." in guidance
    assert "This should not appear." not in guidance
    assert long_context.strip() in guidance
    assert "Identify the target and deal value." in guidance
