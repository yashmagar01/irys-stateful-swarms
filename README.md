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

A complete example is included in [`examples/compare-credit-agreement-to-commitment-letter/`](examples/compare-credit-agreement-to-commitment-letter/) — a banking task that scored **40/40 (perfect)**. You can browse every blackboard snapshot to see exactly how the system builds its understanding.

### Iteration 0 — The system plans before it reads

Before reading any document in detail, the seed planner scans document structure and produces a strategy with targeted questions:

```json
{
  "id": "e564",
  "type": "strategy",
  "content": "This is a comparison and issue-flagging task supported by targeted extraction. The approach involves extracting the baseline terms from the term sheet, commitment letter, and no-flex confirmation, extracting the corresponding drafted terms from the draft credit agreement, and performing a side-by-side gap analysis to identify any deviations, unauthorized changes, or missing provisions.",
  "created_by": {
    "worker_id": "seed_planner",
    "description": "analytical_framework",
    "iteration": 0
  }
}
```

The system then generates **signals** — specific questions that workers must answer:

```json
{
  "id": "s341",
  "type": "question",
  "content": "What are the exact interest rate margins, SOFR floors, and OID for Term Loan B in the draft credit agreement, and do they match the term sheet and commitment letter?",
  "origin_entry": "seed_plan",
  "priority": "high",
  "status": "open"
}
```

```json
{
  "id": "s346",
  "type": "question",
  "content": "What are the Asset Sale Prepayment terms (net proceeds percentage, annual threshold, reinvestment periods, cash consideration requirement) in the draft credit agreement, and do they deviate from the term sheet?",
  "origin_entry": "seed_plan",
  "priority": "high",
  "status": "open"
}
```

**7 entries, 12 open signals.** The swarm knows what it's looking for before reading a single page.

### Iteration 5 — Workers extract grounded evidence

Parallel workers read both documents and write structured findings to the blackboard. Each observation links to its source document, section, and evidence:

```json
{
  "id": "e716",
  "type": "observation",
  "content": "Northbrook Capital Markets, LLC commits to provide a first lien senior secured term loan B facility in an aggregate principal amount of $350,000,000.",
  "source": {
    "document": "commitment-letter.docx",
    "section": "Full Document (part 1)",
    "evidence": ""
  },
  "created_by": {
    "worker_id": "reader_commitment-letter.do",
    "description": "initial_reading",
    "iteration": 0
  },
  "confidence": 0.9
}
```

Workers also identify what's missing. Gap entries flag incomplete extraction and link back to the signals they're trying to answer:

```json
{
  "id": "e1977",
  "type": "gap",
  "content": "Rows 1.0 through 5.0 and 7.0 through 50.0 are currently unextracted from comparison-template.xlsx.",
  "source": {
    "document": "comparison-template.xlsx",
    "evidence": "Document 'comparison-template.xlsx' has ~50 enumerable items but only 0 extracted."
  },
  "created_by": {
    "worker_id": "w1_72fa",
    "description": "Enumerate all 50 row headers and baseline financial terms from comparison-template.xlsx to establish the comparison framework.",
    "iteration": 1
  },
  "addresses_signals": ["s483"]
}
```

**2,203 entries:** 2,023 observations, 78 calculations, 54 analyses, 41 gaps. The blackboard is dense with source-grounded facts.

### Iteration 12 — Cross-document analysis reveals deviations

By the final iteration, the system has built enough state for a stronger model to perform cross-document analysis. It finds specific deviations between the commitment letter and the draft credit agreement:

```json
{
  "id": "e156",
  "type": "analysis",
  "content": "The term sheet requires a 0.50% SOFR floor for the TLB, but the draft credit agreement fails to include this value, creating a potential financial impact where the interest rate could be lower than intended if SOFR drops below 0.50%.",
  "source": {
    "document": "term-sheet.docx",
    "section": "IV.A. First Lien Term Loan B",
    "evidence": "Term Sheet: 'SOFR Floor: 0.50% per annum.' Draft Credit Agreement: 'Adjusted Term SOFR... greater of (a) Term SOFR... and (b) the Floor.' Floor is not defined."
  },
  "created_by": {
    "worker_id": "w5_da99",
    "description": "Calculate the financial impact of the 0.50% TLB floor and verify if it matches the term-sheet.docx requirements",
    "iteration": 5
  },
  "confidence": 0.98,
  "addresses_signals": ["s663"]
}
```

```json
{
  "id": "e865",
  "type": "analysis",
  "content": "Section 2.06 of the draft credit agreement specifies an annual agency fee of $50,000. This is a deviation from the Commitment Letter, which requires an Administrative Agent Fee of $150,000 per annum, payable annually in advance.",
  "source": {
    "document": "draft-credit-agreement.docx"
  },
  "created_by": {
    "worker_id": "flash35_analyst",
    "description": "direct_analysis",
    "iteration": 12
  },
  "confidence": 0.98,
  "supports": ["e260", "e261", "e35"]
}
```

**Final state: 2,400 entries** (2,044 observations, 113 analyses, 87 calculations, 135 gaps, 21 strategies). **210 signals** with 127 addressed and 45 still open. The system found 10+ material deviations — unauthorized margin increases, missing fee definitions, tightened covenant triggers, restricted reinvestment periods — each grounded in specific clauses from specific documents.

From 7 strategy entries to 2,400 grounded findings — a **340x expansion** of structured analytical state over 12 iterations.

### When it doesn't get a perfect score, you can see exactly why

Not every task scores perfectly — but the blackboard makes failures **auditable**. You can trace exactly what the system knew, what it missed, and where the reasoning fell short.

**Example: International Sanctions Entity Extraction** ([`examples/extract-transaction-entity-details/`](examples/extract-transaction-entity-details/)) — scored **80/85**.

The system was asked to extract entity details from a complex sanctions transaction. Here's what happened on the five missed criteria:

**Missed: "Identify Haverford National Bank as OCC-chartered national bank"** — the system found the bank:

```json
{
  "id": "e266",
  "type": "observation",
  "content": "LC Issuing Bank: Haverford National Bank, 1200 Chestnut Street, Philadelphia, PA 19107, USA (SWIFT: HAVNUS33)"
}
```

It got the name, the exact street address, the city, the SWIFT code — but didn't identify the charter type. The fact is *there*, the classification step is what's missing.

**Missed: "Isabelle M. Renard — confirm Swiss/French dual nationality"** — the system found her:

```json
{
  "id": "e219",
  "type": "observation",
  "content": "Screening ID 9: Isabelle M. Renard (DOB: Not provided), Direct Shareholder of Crestmoor (27%), Switzerland / France"
}
```

It even extracted "Switzerland / France" — but didn't explicitly flag this as *dual nationality* in a way the scorer recognized.

**Missed: "Beneficiary name inconsistency"** — the system found *both* name variants in separate entries:

```json
{"id": "e95", "content": "The exporter is Zenith Petrochem Industries LLC, located in Jebel Ali Free Zone, UAE."}
```
```json
{"id": "e48", "content": "Zenith Petrochemical Industries LLC, Jebel Ali Free Zone, Dubai, UAE"}
```

Both "Zenith Petrochem" and "Zenith Petrochemical" are in the blackboard — the discrepancy is *visible in the state* — but no worker explicitly flagged the inconsistency.

**Missed: "OFAC 50% rule aggregation principle"** — the system got close:

```json
{
  "id": "e676",
  "type": "analysis",
  "content": "Orion Gulf's 49% stake in Zenith is 1% below the OFAC 50% rule threshold, but aggregate ownership by blocked persons could trigger a violation."
}
```

It identified the 49% threshold proximity and even mentioned aggregation — but didn't elaborate on the aggregation *principle* with enough specificity.

---

**Example: UCC Lien Extraction** ([`examples/extract-lien-and-debt-information/`](examples/extract-lien-and-debt-information/)) — scored **54/59**.

**Missed: "Debtor name discrepancy between filings"** — the system extracted both name variants:

```json
{"id": "e773", "content": "Debtor: Pinnacle Industrial Solutions, Inc., a corporation organized in Ohio, Charter No. 2187650"}
```
```json
{"id": "e885", "content": "Filing OH-2019-0178443 (Tristate Capital Equipment Corp.) against Pinnacle Industrial Solutions is LAPSED as of May 15, 2024."}
```

"Pinnacle Industrial Solutions, Inc." in one entry, "Pinnacle Industrial Solutions" (without Inc.) in another. The variance exists in the blackboard but wasn't flagged.

**Missed: "PMSI super-priority under UCC §9-324(a)"** — the system identified the concept:

```json
{
  "id": "e1062",
  "type": "observation",
  "content": "Allegheny Equipment Finance LLC holds a purchase-money security interest (PMSI) in five specific pieces of equipment, which may have super-priority status over the proposed senior secured credit facility regarding those specific assets."
}
```

It found the PMSI, recognized it *may have super-priority*, but didn't cite the specific UCC section.

---

**The pattern:** In every near-miss, the raw information was in the blackboard. The system read the right documents, extracted the right facts, and even flagged related concerns. What's missing is the final verification step — the explicit cross-reference, the legal citation, the formal classification. These are the kinds of failures that are **fixable through better state processing**, not fundamental architectural limitations.

This is what auditability means: instead of a black-box answer, you get a complete reasoning trace you can inspect, debug, and improve.

### Explore the examples

| Example | Domain | Score | What it shows |
|---|---|---:|---|
| [`compare-credit-agreement-to-commitment-letter/`](examples/compare-credit-agreement-to-commitment-letter/) | Banking | 40/40 | Perfect cross-document deviation analysis |
| [`draft-safe-agreement/`](examples/draft-safe-agreement/) | Venture Capital | 69/69 | Perfect generative drafting |
| [`extract-transaction-entity-details/`](examples/extract-transaction-entity-details/) | Sanctions | 80/85 | Near-miss: entities found, specifics missed |
| [`extract-lien-and-debt-information/`](examples/extract-lien-and-debt-information/) | Banking/UCC | 54/59 | Near-miss: facts extracted, cross-references missed |
| [`compare-merger-remedies/`](examples/compare-merger-remedies/) | Antitrust | 56/61 | Near-miss: complex multi-jurisdiction comparison |

Browse any task's `swarm/blackboard_iter_*.json` files to trace the full reasoning evolution. The complete outputs for all 1,251 tasks are available in the [GitHub Releases](../../releases).

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
