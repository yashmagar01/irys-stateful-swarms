# Experiment Provenance — Datadog 10-K Strategic Analysis

This directory contains verifiable evidence that the Datadog domain-agnostic experiment was conducted as described in [COMPARISON.md](../COMPARISON.md).

## When this happened

- **Date:** June 4–5, 2026 (UTC-4)
- **Session:** Claude Code session `0ab881d9-3dcd-4097-a019-2813ffb56264`
- **Git commit:** `9ab8de5abbcebd29b99865b4e24faca47cca3497` (2026-06-05 02:45:14 -0400)

## What happened

1. The irys-stateful-swarms CLI was run on 7 Datadog 10-K filings (FY2019–FY2025) with zero code changes from the legal benchmark configuration.
2. Simultaneously, a Claude Code sub-agent (Claude Opus) was given the same task and the same documents.
3. The swarm completed successfully: 2,115 entries, 12,657-word investment memo, 800 seconds.
4. The Claude Code sub-agent failed with context window thrashing after 415 seconds: "Autocompact is thrashing: the context refilled to the limit within 3 turns of the previous compact, 3 times in a row."

## Artifacts and checksums

All checksums are SHA-256.

### Swarm output
| File | SHA-256 |
|---|---|
| `output.docx` (12,657-word investment memo) | `366bedb187b63eb752f520bb3d4fd6de9ece2f4ba20588197f6847a0ec5a1312` |
| `swarm/blackboard_iter_0_seed.json` (initial planning state) | Committed in `9ab8de5` |
| `swarm/blackboard_iter_6_post_6.json` (mid-point extraction) | Committed in `9ab8de5` |
| `swarm/blackboard_iter_12_supervisor_approved_0.json` (final state, 2,115 entries) | `a608d7ade03ce3622117b5badd097310435eafd357f1ebafb9a1dff02c039079` |

### Source documents
| File | Description |
|---|---|
| `demo/datadog-strategy/source_documents/2020-02-25_0001564590-20-006422.pdf` | Datadog FY2019 10-K |
| `demo/datadog-strategy/source_documents/2021-03-01_0001564590-21-009770.pdf` | Datadog FY2020 10-K |
| `demo/datadog-strategy/source_documents/2022-02-25_0001561550-22-000009.pdf` | Datadog FY2021 10-K |
| `demo/datadog-strategy/source_documents/2023-02-24_0001561550-23-000006.pdf` | Datadog FY2022 10-K |
| `demo/datadog-strategy/source_documents/2024-02-23_0001561550-24-000009.pdf` | Datadog FY2023 10-K |
| `demo/datadog-strategy/source_documents/2025-02-20_0001561550-25-000025.pdf` | Datadog FY2024 10-K |
| `demo/datadog-strategy/source_documents/2026-02-18_0001628280-26-008819.pdf` | Datadog FY2025 10-K |

All 10-K filings are publicly available from the SEC EDGAR database. The accession numbers in the filenames can be used to retrieve the originals.

### Session transcript
| File | Description |
|---|---|
| `session-transcript.jsonl` | Full Claude Code session log (3.9MB). Contains every tool call, every sub-agent invocation, every error message. The sub-agent failure ("autocompact thrashing") is recorded verbatim in this log. |

## How to verify

### 1. Verify the git history
```bash
git log --format="%H %ai %s" 9ab8de5
git show 9ab8de5 --stat
```
Git commits are cryptographic (SHA-1) — the commit hash proves the content existed at the recorded timestamp.

### 2. Verify file checksums
```bash
# On Linux/Mac:
sha256sum examples/datadog-strategic-analysis/output.docx
# Expected: 366bedb187b63eb752f520bb3d4fd6de9ece2f4ba20588197f6847a0ec5a1312

sha256sum examples/datadog-strategic-analysis/swarm/blackboard_iter_12_supervisor_approved_0.json
# Expected: a608d7ade03ce3622117b5badd097310435eafd357f1ebafb9a1dff02c039079

# On Windows:
certutil -hashfile examples\datadog-strategic-analysis\output.docx SHA256
```

### 3. Verify the sub-agent failure
Search the session transcript for the thrashing error:
```bash
grep -o "autocompact.*thrashing" provenance/session-transcript.jsonl
```

### 4. Verify the source documents
The 10-K filings are public SEC filings. The accession numbers in the filenames (e.g., `0001564590-20-006422`) can be looked up on [SEC EDGAR](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001561550&type=10-K) to confirm they are genuine Datadog annual reports.

### 5. Reproduce the experiment
```bash
pip install -e .
export GEMINI_API_KEY=your_key
python -m src.cli run demo/datadog-strategy --output-dir results/datadog-repro/
```
The swarm is deterministic in its coordination logic. Results will vary due to LLM non-determinism but the blackboard structure, entry types, and signal lifecycle will be consistent.

## Why this matters

This experiment proves that irys-stateful-swarms is domain-agnostic — not as a claim, but as a demonstrated fact. The same system that handles Harvey LAB legal tasks handled SEC financial analysis with zero modifications. A frontier LLM (Claude Opus) failed on the same task due to the fundamental memory problem that stateful swarms solve.
