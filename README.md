# ant-irys

ant-irys is a swarm-based document analysis system that coordinates multiple AI agents to solve long-context, knowledge-intensive tasks. It reads source documents, builds structured analytical state, and produces grounded deliverables — entirely through API calls to frontier language models.

## Benchmark Results

ant-irys completed the full public [Harvey Legal Agent Benchmark (LAB)](https://github.com/Harvey-AI/harvey-labs) — 1,251 tasks across 24 legal practice areas.

| Metric | Result |
|---|---|
| Tasks completed | `1,251 / 1,251` |
| Criteria pass rate | `62,800 / 74,990 = 83.74%` |
| Strict all-pass | `222 / 1,251 = 17.75%` |
| Success gate (<=2 misses or >=95%) | `336 / 1,251 = 26.86%` |
| Total cost | `$1,626.08` |
| Cost per task | `$1.30` |

### Verification

The complete outputs from the full benchmark run are available as downloadable archives in the [GitHub Releases](../../releases) section. You can score these outputs yourself using the [Harvey LAB scorer](https://github.com/Harvey-AI/harvey-labs) to independently verify these numbers.

### Context

Harvey's published LAB results use a private holdout set that mirrors the public benchmark distribution, so they are useful context but not a direct leaderboard comparison. Harvey reported that its strongest published private-holdout all-pass result reached `10.4%`, with earlier initial results at `7.1%` all-pass at about `$50.90/task`.

On a per-task cost basis, ant-irys is roughly **39x cheaper** than that `$50.90/task` figure. This is a cost comparison only; the evaluation sets differ.

### Performance by task type

| Task verb | Tasks | Criteria % |
|---|---:|---:|
| map | 5 | 92.52 |
| draft | 427 | 90.22 |
| analyze | 89 | 86.42 |
| scenario | 119 | 83.91 |
| assess | 22 | 83.57 |
| research | 10 | 83.43 |
| summarize | 8 | 82.67 |
| review | 58 | 80.86 |
| compare | 148 | 77.14 |
| extract | 138 | 75.67 |
| identify | 195 | 74.26 |

## How the swarm reasons

The best way to understand ant-irys is to look at how it actually thinks. Each task produces a **blackboard** — a structured state that evolves over multiple iterations as workers read documents, extract evidence, cross-reference findings, and build toward a complete answer.

Here's a real example from the benchmark: **Compare Credit Agreement to Commitment Letter** (scored 40/40 — perfect).

### Iteration 0 — Planning

The system reads document structure (headings, tables, complexity) without reading the full text. It produces a seed plan:

> *"What are the interest rate margins, SOFR floors, and OID terms in the commitment letter versus the credit agreement draft? Are the financial covenants (leverage ratio, interest coverage) consistent? What fee structures are specified and do they match?"*

It generates 13 targeted signals — specific questions that workers must answer — and a completeness checklist before any document is read in detail.

**7 entries.** The swarm knows what it's looking for.

### Iteration 5 — Evidence building

Workers have read both documents in parallel. The blackboard now contains **2,203 entries** — observations with exact source provenance, calculations, and early gap detection:

> *"Term Loan B: $350M at 4.00% SOFR margin + 0.50% floor, 2.00% OID ($7M), 7-year maturity"*
>
> *"ECF Sweep: 50% if leverage > 3.75x; 25% if > 3.25x; 0% if ≤ 3.25x"*
>
> *"Gap: Missing 6-month soft call protection definition in draft"*

Each finding links back to the specific document, section, and evidence quote. Workers build on each other's findings — an observation about a fee amount triggers a calculation entry cross-referencing the commitment letter's percentage.

### Iteration 12 — Cross-document analysis complete

The final blackboard has **2,400 entries** including 113 analysis entries and 87 calculations. The system has identified critical deviations between the two documents:

> *"Margin Violation: Draft shows 4.25% SOFR margin vs. agreed 4.00% — 25 bps unauthorized increase violating 'no-flex' waiver"*
>
> *"Arrangement Fee Discrepancy: Draft shows $250K vs. commitment letter mandates 1.75% of $350M = $6.125M — under-draft by $5.875M"*
>
> *"Revolver Floor Breach: Draft imposes 0.50% SOFR floor vs. agreed 0.00% — 50 bps unauthorized increase = $375K annual excess cost if SOFR drops to 0%"*
>
> *"Asset Sale Reinvestment: Draft limits to 270+90 days (360 total) vs. agreed 365+180 days (545 total) — severe capital deployment restriction"*

From 7 strategy entries to 2,400 grounded findings — a **340x expansion** of structured analytical state. The system found 10+ specific deviations with exact dollar amounts, basis point calculations, and clause references.

**You can explore the full blackboard state for any of the 1,251 tasks** in the downloadable outputs from [GitHub Releases](../../releases). Look in `<task>/swarm/blackboard_iter_*.json` to trace how the system reasons for each task.

## Why open source this?

ant-irys achieves strong results using only API calls to standard language models — no fine-tuning, no custom embeddings, no latent space reasoning. The entire system is prompt engineering and coordination logic.

We believe long-context reasoning over complex documents is an important unsolved problem. By open-sourcing this baseline, we want to open a discussion about how swarm coordination, structured state-building, and multi-agent decomposition can push the boundaries of what's possible with off-the-shelf models.

There are promising directions we haven't explored here — hierarchical embeddings, latent space reasoning, hybrid retrieval-generation architectures — that could take this further. We're interested in what the community builds on top of this foundation.

## Installation

```bash
pip install -e .
```

Requires Python 3.12+.

### Environment variables

```bash
# Required: at least one LLM provider
GEMINI_API_KEY=...              # Google Gemini (primary provider)
GEMINI_API_KEYS=k1,k2,k3       # Multiple keys for load distribution (optional)
OPENAI_API_KEY=...              # OpenAI (optional)
ANTHROPIC_API_KEY=...           # Anthropic (optional)

# Optional: model overrides
SWARM_WORKER_MODEL=gemini-3.1-flash-lite
SWARM_SYNTHESIS_MODEL=gemini-3.5-flash
SWARM_REVIEWER_MODEL=gemini-3.5-flash
```

## Usage

### Run a single task

```bash
python -m src.cli run <task_directory> --output-dir results/
```

The task directory should contain:
- A `task.json` with an `instructions` field (or an `instruction.md` file)
- Source documents in a `source_documents/` subdirectory (or alongside task.json)

Supported document formats: PDF, DOCX, XLSX, PPTX, TXT, MD, JSON, EML.

### Run from a manifest

```bash
python -m src.cli run-manifest <manifest.json> --output-dir results/
```

### Score outputs (requires Harvey LAB)

```bash
python -m src.cli score <results_dir> --bench-root /path/to/harvey-labs
```

## Sources

- Harvey LAB repository: <https://github.com/Harvey-AI/harvey-labs>
- Harvey initial LAB results: <https://www.harvey.ai/blog/legal-agent-benchmark-initial-results>
- Harvey published 10.4% LAB update: <https://www.harvey.ai/blog/opus-4-8-now-live-in-harvey>

## License

MIT — see [LICENSE](LICENSE).
