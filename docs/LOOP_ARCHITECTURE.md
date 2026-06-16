# The Loop: Convergence-Driven Document Analysis

The original stateful swarm architecture processes documents in a single pass -- read everything, build state, synthesize. The **loop architecture** replaces this with iterative convergence: the system reasons about what it knows, decides what to investigate next, and stops when it can defend its answer.

## How it works

```
seed --> triage --> [controller --> execute --> maintain]* --> plan --> synthesize
```

**Seed.** Before reading any document, the system examines the task instruction and source metadata (file names, sizes, types) to produce a **target ledger** -- a set of concrete questions the investigation must close. Targets are semantic ("reconcile the share counts across the certificate and the stockholder agreement") not procedural ("read document 3"). Each target carries a materiality ranking (critical, high, medium, low) that drives prioritization throughout the loop.

**Triage.** Sources are relevance-scored using metadata alone -- file paths carry structural signal (a file named `10-K_2024.pdf` in a `sec-filings/` directory is more likely relevant to a financial analysis task than `cover-letter.docx`). No text is materialized until the controller decides to read it. This is lazy loading: the system builds a reading plan, not a reading obligation.

**The Loop.** Each iteration follows a three-step cycle:

1. **Controller decides.** A compact view of the current state -- open targets with their blockers, resolved targets with reasons, unbound claims, source catalog with read status -- goes to the controller. The controller is a scheduler, not a reasoner: it generates actions (read, analyze, verify, bind, search) based on what the investigation needs next. It is not limited to a precomputed menu.

2. **Workers execute.** Actions run in parallel. Readers extract structured claims from source text. Analyzers promote raw observations into conclusions. Verifiers adversarially check material claims against sources. Binders semantically map claims to targets. Workers can propose new targets freely -- discovery never asks permission.

3. **Maintenance grooms.** Periodically, a maintenance pass reviews the target ledger: merging duplicates, waiving low-value targets, reprioritizing based on new evidence, sharpening vague questions. This is judgment after collection, not prevention of collection.

**Convergence.** The controller explicitly decides when to stop. Convergence requires: all critical and high targets resolved (closed, waived with reason, or blocked), all mandatory obligations satisfied, and no material unbound claims. If the system hits a wall -- open targets stop shrinking -- a closeout mode forces the controller to resolve every remaining target with a defensible reason. The system never silently times out; it always explains why it stopped.

**Synthesis.** The final phase allocates targets to deliverables, builds evidence packets per target, and generates output documents. The same target can feed multiple files differently -- a summary in a memo, a detailed table in a spreadsheet. Synthesis sees the full provenance chain: every conclusion traces to the claims that support it, and every claim traces to the source text that produced it.

## Key design decisions

**Blackboard as index, not warehouse.** Claims carry pointers to source text (character-level spans), not copies of it. When the system needs to reason deeply -- during analysis or synthesis -- it resolves these pointers to pull in the original text. This means the blackboard can track thousands of findings without bloating context windows, and the system can always go back to the source.

**Defeasible closure.** When a target is closed, the system records which claims justified the closure. If later evidence contradicts that basis, the target becomes a reopen candidate. Periodic reframe passes (with the benefit of everything learned so far) decide whether to actually reopen. Closure is a defensible verdict, never a tombstone.

**Obligations from instruction language.** The system derives what the answer owes from the instruction's own words, not from task taxonomy. Coverage standards -- exhaustive ("identify all issues"), material ("key provisions"), representative ("examples"), native-complete ("draft the agreement") -- are properties of the instruction. This keeps the architecture domain-agnostic: the same loop handles "compare these contracts" and "draft this compliance memo" without special-casing either.

**Workers propose, maintenance judges.** Workers can create new targets, flag contradictions, and identify gaps without asking permission. The maintenance pass grooms the result. This separation ensures the system never misses something because a filter was too aggressive, while still preventing target sprawl.

**Model-agnostic tiered routing.** The architecture separates callers by judgment tier -- cheap models for high-volume extraction, mid-tier for analysis and control, premium for strategic decisions (seeding and synthesis). The tiers are configuration, not code. Swap any model at any tier.

## The state primitives

The loop tracks four primitives on a shared blackboard:

- **Sources** -- documents and web results. Lazy-loaded; text materialized only when the controller schedules a read. File path metadata used for triage.
- **Claims** -- observations extracted from sources, or derived conclusions. Each claim has a kind (observation, analysis, calculation, comparison, issue, recommendation, gap, uncertainty, contradiction, decision, requirement), source attribution with character-level spans, confidence scores, and a support_refs DAG linking derived claims to the evidence that produced them.
- **Targets** -- questions the investigation must close. Materiality-ranked. Blockers (needs_evidence, needs_analysis, has_contradiction, needs_verification) are computed from the bound claims, not stored. Closure is defeasible via closure_basis tracking.
- **Obligations** -- what the answer owes, derived from instruction language. Coverage standards (exhaustive, material, representative, native-complete, summary) drive how thoroughly the system must account for repeated items (provisions, issues, request categories).

Everything the system knows or does is one of these. Code does bookkeeping; LLMs make every semantic judgment.

## What we're exploring

We're actively experimenting with target density, convergence thresholds, extraction depth, and routing strategies. The loop architecture is a research platform as much as a production system -- every variant we test produces structured, comparable data because the blackboard captures the full reasoning trace.

We're releasing this because the design space is large and interesting. If you run it with different models, different target strategies, or on different domains, we want to hear what you find.

## Running with the loop architecture

```bash
export SWARM_ARCH=loop
python -m src.cli run <task_directory> --output-dir results/
```

The loop produces richer intermediate artifacts than the single-pass architecture:

- `loop/board_iter_N.json` -- full blackboard snapshot at each iteration
- `loop/events.jsonl` -- streaming event log (claims added, targets resolved, actions dispatched)
- `output/` -- final deliverables

Browse the board snapshots to trace exactly how the system built its understanding -- what it read, what it concluded, what it reopened, and why it stopped.
