# Domain-Agnostic Proof: Datadog 10-K Strategic Analysis

## The task

Analyze how Datadog's strategic priorities have shifted over the last 5-7 years using their annual 10-K filings (2020-2026). Produce a comprehensive investment memo covering product strategy evolution, go-to-market shifts, risk factor changes, competitive positioning, and financial trajectory.

**Source documents:** 7 Datadog 10-K annual filings (FY2019 through FY2025), totaling ~31MB of PDF.

## irys-stateful-swarms vs. Claude Code

We ran the same task through both systems simultaneously.

### irys-stateful-swarms (stateful swarm)

| Metric | Result |
|---|---|
| Status | **Completed successfully** |
| Time | 800 seconds (~13 minutes) |
| Tokens used | 2,734,609 |
| Blackboard entries | 2,115 (1,511 observations, 512 calculations, 41 analyses, 41 gaps, 10 strategies) |
| Signals generated | 218 (80 addressed, 55 open, 83 expired) |
| Iterations | 12 |
| Output | 12,657-word investment memo (64KB docx) with 80+ structured sections |
| Models used | Gemini 3.1 Flash Lite (extraction) + Gemini 3.5 Flash (synthesis) |

The system read all 7 filings in parallel, built 2,115 structured findings with source provenance, identified gaps in its own analysis, dispatched targeted follow-up workers, and synthesized a comprehensive investment memo covering corporate structure, product strategy evolution (2012-2024 timeline), competitive positioning shifts, go-to-market transformation, financial performance trajectory, operational metrics, risk factor evolution, geographic expansion, capital structure, and market opportunity.

### Claude Code sub-agent (Claude Opus)

| Metric | Result |
|---|---|
| Status | **Failed — context window thrashing** |
| Time | 415 seconds (~7 minutes) before failure |
| Output | None |
| Error | "Autocompact is thrashing: the context refilled to the limit within 3 turns of the previous compact, 3 times in a row" |

The sub-agent attempted to read the 7 10-K PDFs but could not hold them in its context window. After 19 tool calls and repeated context compaction cycles, the system gave up. No investment memo was produced.

## Why this happened

This is exactly the problem described in the [white paper](../../whitepaper.pdf), Section 2: "The Memory Problem in Agentic Systems."

**Context compaction destroys information.** The sub-agent tried to read the filings, but each 10-K is hundreds of pages. After reading 2-3 filings, the context window filled up. The system compacted earlier readings to make room for new ones — but then couldn't reference the details it had summarized away. When it tried to read more filings, the cycle repeated until the system recognized it was thrashing and aborted.

**The stateful swarm doesn't have this problem.** Each worker reads a focused section of a specific document and writes structured findings to the blackboard. No single worker needs to hold all 7 filings in context. The blackboard accumulates 2,115 typed, provenance-tracked entries across 12 iterations — none of which are ever compacted or summarized away. When the synthesis phase begins, it works from curated, structured state, not from raw document text.

**This is the fundamental architectural difference.** A stateless system treats the context window as its memory — and context windows are lossy, bounded, and ephemeral. A stateful swarm treats the blackboard as its memory — and blackboards are lossless, unbounded, and persistent.

## What the output looks like

The full output is in [`output.docx`](output.docx). Key sections include:

**Product Strategy Evolution:** Traces Datadog's expansion from infrastructure monitoring (2012) through APM (2017), Log Management (2018), Synthetics and Network Monitoring (2019), Real User Monitoring and Security (2020), to CI Visibility, Database Monitoring, Cloud Cost Management, and AI-native observability products through 2024.

**Competitive Positioning Shifts:** Documents the transition from competing against legacy ITOM vendors (BMC, IBM, CA Technologies, Cisco/AppDynamics) to competing against cloud-native platforms (Elastic, Splunk, Grafana) and hyperscaler-embedded tools (AWS CloudWatch, Azure Monitor, GCP Operations).

**Go-to-Market Transformation:** Tracks the shift from high-velocity inside sales and self-service (10,500 customers in 2019) to enterprise-grade sales motion (29,200+ customers by 2025) with strategic alliances and upmarket penetration ($100K+ ARR customers growing from 858 to 3,810).

**Financial Trajectory:** Revenue from $362.8M (FY2019) to $2.68B+ (FY2025), with the strategic pivot from S&M-heavy spending to R&D-heavy investment starting in 2023 — signaling a shift from sales-led to product-led growth.

**Risk Factor Evolution:** From legacy displacement risks (2019) to hyperscaler competition and complex global operations risks (2025).

## Explore the reasoning trace

Browse the blackboard snapshots to see how the system built its understanding:

- [`swarm/blackboard_iter_0_seed.json`](swarm/blackboard_iter_0_seed.json) — Initial planning: questions generated before reading any documents
- [`swarm/blackboard_iter_6_post_6.json`](swarm/blackboard_iter_6_post_6.json) — Mid-point: extraction in progress across all 7 filings
- [`swarm/blackboard_iter_12_supervisor_approved_0.json`](swarm/blackboard_iter_12_supervisor_approved_0.json) — Final state: 2,115 entries ready for synthesis

## The domain-agnostic point

irys-stateful-swarms was built and benchmarked on legal document analysis (Harvey LAB). **Zero changes were made to run it on SEC filings for investment analysis.** The same swarm coordination, the same blackboard architecture, the same model routing — applied to a completely different domain, producing a comprehensive investment memo that a stateless system couldn't even attempt.

This is what we mean by "the stateful swarm paradigm is domain-agnostic."
