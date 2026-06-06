# Swarm Intelligence: First Principles Design

**Status:** PHASE 0 READY; FULL DESIGN UNPROVEN  
**Date:** 2026-06-06  
**Origin:** Tesla deep-reasoning process + Cycle23 post-benchmark introspection cross-reading

---

## 1. Core Architectural Insight

The system is a **state-transition control system**, not a pipeline.

A pipeline transforms input linearly: read → extract → analyze → synthesize → output. When a pipeline stage fails, the error propagates forward silently. The system produces plausible output that omits critical content.

A control system continuously **measures state**, **compares it against requirements**, and **dispatches bounded actions** to close gaps. It converges when measurable conditions are satisfied, not when stages complete.

### What This Means Concretely

The current implementation (`src/swarm/__init__.py:run_swarm`) already exhibits control-system behavior:
- The orchestrator loop (Phase 6) runs up to `max_iterations`, dispatching workers based on blackboard state
- Convergence is checked adversarially (`src/swarm/convergence.py`)
- Debt sensors (`src/swarm/debt_sensors.py`) measure specific failure modes: relation debt, source-object debt, severity debt, authority debt
- Supervisor review acts as a second convergence gate

**What's missing:** The system identifies gaps but does not reliably make them **blocking**. A gap detected by debt sensors becomes an entry on the blackboard, but synthesis proceeds regardless. The introspection's central finding: "the system can identify material open work, but open work does not reliably become a blocking work queue."

---

## 2. The Blackboard

The blackboard (`src/swarm/blackboard.py`) is the shared state that all workers read from and write to.

### Current Structure

```
Blackboard
├── task_instruction: str
├── documents: list[DocumentStatus]      # source documents with read tracking
├── entries: list[Entry]                 # the accumulated knowledge
│   ├── type: observation|analysis|calculation|strategy|gap|contradiction
│   ├── source: EntrySource (document, section, evidence)
│   ├── epistemic: EpistemicStatus (classification, credibility)
│   ├── confidence: float
│   ├── supports_entries / contradicts_entries / supersedes_entries
│   └── status: active|disputed|superseded
├── signals: list[Signal]                # open questions / convergence gaps
└── iteration tracking + token accounting
```

### What the Design Needs: Typed Metadata Layer

The blackboard currently stores entries as free-form semantic content. LLMs are good at reading this. But the system cannot **measure** whether its state is complete without typed metadata that deterministic code can inspect.

Each entry should carry:
- **lifecycle**: `discovered → enriched → transformed → committed → placed` — tracks whether an observation has been turned into analysis, bound to a commitment, and placed in an artifact
- **provenance chain**: which worker created it, what it was derived from, which source text backs it
- **obligation binding**: whether this entry satisfies a synthesis obligation (and which one)

### What the Introspection Adds: Object Permanence

The introspection identifies a critical gap: the same entity appears across documents under different names ("the Borrower" in one document, "XYZ Corp" in another, "Guarantor" in a third). Currently, each mention becomes a separate Entry. There is no concept of a **persistent object** that accumulates fields across entries.

**Task-induced objects** should be first-class:
- An entity (party, contract, provision, date) that appears across multiple entries
- Fields that get populated as evidence is discovered
- Identity resolution: entries that refer to the same real-world thing should be linked

This is not implemented yet. It's a Phase 1+ concern, but the Entry model should be designed to support it.

---

## 3. The Controller

The controller decides what work to do. Currently this is the orchestrator (`src/swarm/orchestrator.py`), which is a single LLM call that reads blackboard state and produces worker dispatches.

### How It Should Work: State-Driven Dispatch

Instead of a monolithic "what should we do next?" prompt, the controller should:

1. **Compute ledgers** from blackboard state (deterministic):
   - Requirement ledger: what does the task demand?
   - Source coverage ledger: what have we read vs what exists?
   - Transformation debt ledger: observations not yet analyzed, analyses not yet committed
   - Commitment debt ledger: obligations not yet placed in artifacts

2. **Select action type** based on ledger state:
   - If source coverage < threshold → dispatch readers
   - If transformation debt high → dispatch analyzers
   - If commitment debt high → dispatch synthesis prep
   - If all ledgers satisfied → converge

3. **Dispatch typed, bounded workers** that each do one thing

### What the Introspection Adds: Operational vs Advisory Gaps

The introspection traces prove the system can identify gaps but treats them as advisory, not operational. Example from Cycle23: "Stratification tables missing" appears in plan coverage review, yet synthesis proceeds anyway.

**The distinction matters:**
- **Advisory gap**: "We could provide more detail on X" — logged, disclosed if relevant, but does not block
- **Operational gap**: "The task requires X and we have no evidence for X" — MUST be resolved or explicitly disclosed as unresolvable before synthesis

The controller must classify gaps and enforce blocking on operational ones.

---

## 4. Task-Worlds

The deepest concept from the introspection. A task does not merely ask a question — it implies an entire **world** of objects, relations, roles, and standards.

Example: "Draft an antitrust risk assessment for the proposed merger" implies:
- **Objects**: the merging parties, the relevant market, competitors, market shares, regulatory authorities
- **Relations**: acquirer/target, market position, competitive overlap, regulatory jurisdiction
- **Roles**: the parties' legal roles, the analyst's perspective (advisor to which party?)
- **Standards**: HHI thresholds, Hart-Scott-Rodino requirements, relevant precedent

The system's current `seed.py` generates analytical frameworks and key questions, which is a step toward task-world construction. But the seed plan is treated as static guidance for initial reading, not as a **revisable hypothesis** about what the task demands.

**Key principle from the introspection:** "The system's mistake has been to treat the initial world as a plan to execute. Expert reasoning treats the initial world as a hypothesis to test."

Sources must be able to change the frame. If a document reveals that the merger involves a regulated industry not anticipated in the initial task-world, the task-world must evolve.

---

## 5. The Three-Tier Model Cascade

| Tier | Model | Role | Token Cost |
|------|-------|------|------------|
| Read | Flash Lite (`gemini-3.1-flash-lite`) | Source reading, extraction, structural profiling | Lowest |
| Reason | Flash (`gemini-3.5-flash`) | Analysis, cross-reference, debt detection, synthesis | Medium |
| Construct | Pro/Opus (when available) | Supervisor review, seed planning, complex reasoning | Highest |

This is already implemented in the runner (`src/runner.py:64-71`): `worker_caller` (Flash Lite), `synthesis_caller` (Flash), `reviewer_caller` (Flash, configurable).

**Why this cascade matters:** Flash Lite at $0.25/M input can read 100 document sections for the cost of one Pro call. The system's intelligence comes from **architecture** (what to read, when to analyze, how to verify), not from throwing expensive models at every sub-task.

---

## 6. Commitment Contracts

The current system has synthesis obligations (`src/swarm/obligations.py`) and artifact commitments (`src/swarm/artifact_commitments.py`). These are steps toward what the design requires: **commitment contracts**.

A commitment contract is:
- **Function**: what this commitment achieves (e.g., "calculate combined HHI")
- **Evidence**: which blackboard entries provide the source material
- **Satisfaction conditions**: measurable criteria (e.g., "HHI value computed from all firms' market shares")
- **Target deliverable**: which output file this commitment maps to
- **Verification mode**: how to check it was fulfilled (deterministic check vs LLM verification)

The difference from the current obligations: obligations are generated by an LLM and passed to synthesis. Commitment contracts are **tracked through placement** — the system verifies that each commitment actually appears in the final artifact.

The survival trace (`src/swarm/survival_trace.py`) already does this partially — it tracks whether blackboard entries survive into deliverables. The gap is that survival is checked post-hoc rather than enforced during construction.

---

## 7. Debt Sensors: The Lens System

The debt sensor system (`src/swarm/debt_sensors.py`) is the current implementation of what the design calls "lenses." Each sensor detects a specific failure mode:

| Sensor | What It Detects | Subtype Examples |
|--------|----------------|------------------|
| Relation debt | Cross-document comparisons needed | conflict, reconciliation, date_alignment, entity_alignment, provision_interplay |
| Source-object debt | Missing entities/populations | missing_population, missing_entity, missing_component, unread_section, thin_source_coverage |
| Severity debt | Issues without risk assessment | risk_without_severity, issue_without_recommendation, priority_needed |
| Authority debt | Claims without source citation | source_citation_needed, clause_reference_needed, standard_needed |

### What the Introspection Adds: Calculation Debt Subtypes

The introspection identifies five specific subtypes of calculation failure that the current system doesn't distinguish:
1. **missing_operation**: The source numbers exist but the arithmetic wasn't performed
2. **missing_population**: Not all items in a set were included in the calculation
3. **missing_assumption**: The calculation omits a stated assumption (e.g., discount rate, time period)
4. **placement_failure**: The calculation was performed but didn't survive into the artifact
5. **not_calculable**: The task expects a number but the sources don't contain sufficient data

### Sensor Epistemology (Key Risk)

The Tesla process identified this as the system's deepest risk: **can the sensors correctly identify debts?**

If a relation sensor says "no cross-document comparison needed" when one is actually needed, the system confidently produces an incomplete deliverable. The sensor's judgment is only as good as the LLM's ability to reason about what's missing from what's present.

Mitigation: Multiple independent sensors with different prompting strategies. If any sensor flags a debt, it's treated as real until explicitly resolved. False positives waste compute; false negatives lose quality.

---

## 8. The Custody-Break Taxonomy

The Cycle23 introspection developed a 12-type taxonomy for how information gets lost between source and artifact. This is the diagnostic framework for understanding failures:

| # | Type | Description | Example |
|---|------|-------------|---------|
| 1 | absent-state | Fact never entered the blackboard | Source document section was skipped |
| 2 | wrong-world | Task-world model doesn't include the right objects | Merger involves regulated industry but task-world only models antitrust |
| 3 | wrong-object | Entity misidentified or merged with different entity | "Borrower" and "Guarantor" confused as same party |
| 4 | identity-continuity | Same entity loses identity across transformations | "$75M upfront" becomes "significant consideration" |
| 5 | wrong-relation | Relation between objects is incorrect | Condition precedent treated as covenant |
| 6 | unpromoted-fact | Observation exists but never promoted to analysis | Cure period extracted but never compared across documents |
| 7 | lost-commitment | Commitment made during analysis but dropped before placement | "Must calculate HHI" noted but never executed |
| 8 | wrong-artifact | Content placed in wrong deliverable or wrong section | Workbook total in wrong tab |
| 9 | wrong-sufficiency | Content placed but inadequately (summary instead of specifics) | "Several conditions" instead of enumerating all 7 |
| 10 | hidden-ambiguity | Ambiguity in sources not surfaced | Two documents define "Material Adverse Effect" differently |
| 11 | false-completion | System declares complete when it isn't | Convergence approved with critical gaps |
| 12 | build-process | The construction process itself introduces errors | Synthesis LLM hallucinates a clause number |

**How to use this:** Phase 0 experiments should classify every failure using this taxonomy. The taxonomy tells you WHICH mechanism to fix, not just that something went wrong.

---

## 9. The 15 Design Constraints

Any future mechanism must satisfy these constraints, derived from the introspection's analysis of what goes wrong:

1. **Revisable task-world**: The initial understanding of what the task demands must be revisable by evidence
2. **Source-driven revision**: Sources must be able to change the frame, not just fill slots
3. **Surprise distinction**: Surprise (evidence contradicting expectation) must be distinguished from noise
4. **Facts vs commitments**: The system must distinguish between "facts we know" and "commitments we've made about what to include"
5. **Safe ignoring**: If something is intentionally excluded, that decision must be justified and recorded
6. **Shared custody**: Every transformation of information must be traceable from source to artifact
7. **Transformation evidence**: Each transformation step must preserve evidence of what it consumed and produced
8. **Completion earned, not inferred**: Completion is not inferred from form (document looks done) but earned from evidence (all commitments satisfied)
9. **Recoverability diagnosis**: Before attempting repair, diagnose what type of failure occurred
10. **Domain-orienting generality**: The system must work across legal practice areas without practice-area-specific code
11. **Uncertainty classification**: Unknown quantities must be classified professionally (missing data vs conflicting data vs insufficient data)
12. **Success explanation**: The system should be able to explain WHY it succeeded, not just that it did
13. **Fragility awareness**: The system should know which parts of its output are fragile (supported by thin evidence)
14. **Cost-preserving judgment**: Using a more expensive model should not lose insights from cheaper-model analysis
15. **Build process obeys same rules**: The synthesis/construction process is subject to the same quality constraints as analysis

---

## 10. Must-Surface Policy

Certain categories of information must ALWAYS surface in deliverables, regardless of synthesis strategy:

- **Exact values at risk**: Dollar amounts, percentages, deadlines — never summarize away
- **Contradictions**: When two sources disagree, both positions must appear
- **Unresolved signals**: Open questions that couldn't be answered from available sources
- **Unanswered questions**: Task requirements that couldn't be satisfied
- **Minority sources**: If one document disagrees with several others, the dissent must surface
- **Zero-evidence requirements**: Task requirements for which no source evidence was found

The current curation system (`src/swarm/curation.py`) selects "must-include" items. The must-surface policy should be enforced as a hard constraint, not a soft preference.

---

## 11. Damping Mechanisms

Without damping, the control loop oscillates: debt sensor finds gaps → workers create entries → new entries create new gaps → infinite loop.

Required damping:
- **Action cooldowns**: Don't re-run the same sensor on the same entries within N iterations
- **Marginal-value thresholds**: Stop when the expected value of additional work falls below cost
- **Rejected-action memory**: If a debt item was classified as "not actionable," don't re-detect it
- **Budget governor**: Hard token budget prevents runaway loops (already implemented: `budget_used_pct >= 85`)

---

## 12. Artifact Construction

The current synthesis (`src/swarm/synthesis.py`) takes curated entries + obligations and produces deliverable text in a single LLM call (or per-file calls for multi-deliverable tasks).

### What the Introspection Adds: Artifact Function

Each deliverable has a **function** — not just a format. A memo's function is different from a spreadsheet's function, which is different from a redline's function.

- **Memo**: narrative analysis, recommendations, professional judgment
- **Spreadsheet**: structured data, calculations, comparisons in tabular form
- **Redline**: precise textual modifications to a source document

The current `_should_use_file_scoped_synthesis` function (`src/swarm/__init__.py:73-93`) already routes different file types to different synthesis paths. The gap is that synthesis doesn't deeply understand the **reader's expectations** for each artifact type.

### Artifact Survival Tracking

The survival trace system (`src/swarm/survival_trace.py`) already tracks whether blackboard entries appear in final deliverables. The design extends this to commitment-level tracking: did each commitment contract get fulfilled?

---

## 13. Implementation Roadmap

### Phase 0: Prove the Sensors (Current Priority)

**Goal:** Demonstrate that debt sensors can reliably detect the failure modes that cause benchmark failures.

**Method:**
1. Run 10 tasks through the current system
2. For each failed criterion, classify using the custody-break taxonomy (Section 8)
3. Check whether the debt sensors detected the corresponding gap
4. Measure sensor precision (false positive rate) and recall (false negative rate)

**Success criteria:** Sensors detect ≥70% of custody breaks that cause criterion failures.

### Phase 1: Operational Gap Enforcement

**Goal:** Make detected gaps block synthesis rather than merely advising it.

**Method:**
1. Classify each debt sensor finding as operational or advisory
2. Operational gaps create blocking obligations
3. Synthesis cannot proceed until operational gaps are resolved or explicitly waived

### Phase 2: Commitment Contracts

**Goal:** End-to-end tracking from requirement → evidence → commitment → artifact placement.

**Method:**
1. Build requirement ledger from task instruction + seed plan
2. Track which entries satisfy which requirements
3. Build commitment contracts that bind evidence to deliverable targets
4. Verify placement after synthesis

### Phase 3: Task-World Construction

**Goal:** Build revisable task-world models that evolve as evidence is discovered.

**Method:**
1. Initial task-world from seed plan (already partially exists)
2. Evidence-driven revision: new facts can modify the task-world
3. Object permanence: stable entities with fields populated across entries

### Phase 4: Enhanced Sensors

**Goal:** Add sensors for the failure modes not currently covered.

Candidates:
- Calculation debt subtypes (missing_operation, missing_population, etc.)
- Object-identity debt (same entity under different names)
- Artifact-function debt (content doesn't match deliverable type expectations)

### Phase 5: Multi-Model Orchestration

**Goal:** Route sub-tasks to optimal model tier based on complexity.

The three-tier cascade is already implemented for major phases. Phase 5 extends it to individual worker dispatches: simple extraction → Flash Lite, cross-document reasoning → Flash, complex legal judgment → Pro.

### Phase 6: Convergence Refinement

**Goal:** Convergence is provably correct, not just plausible.

The current convergence check (`src/swarm/convergence.py`) is adversarial (asks "find reasons it's NOT complete"). Phase 6 makes convergence evidence-based: all ledgers satisfied, all commitments placed, all operational gaps resolved.

---

## 14. Non-Negotiable Principles

These are load-bearing constraints, not preferences:

1. **API-first**: LLMs make ALL analytical decisions. Deterministic code only for tools (regex, arithmetic, file I/O, JSON parsing). No keyword-based routing.

2. **Never truncate**: If a source document has 200 items, extract all 200. Summarization is a quality choice, not a capacity workaround.

3. **State over output**: The highest-ROI improvement is always in state/decomposition, not in output patching. If the output is wrong, the state was wrong first.

4. **Requirements discovered, not imported**: Requirements emerge from LLM reasoning grounded in task + sources + professional priors. Never from benchmark criteria, scorer outputs, or evaluator-only metadata.

5. **Flash Lite as default worker**: The cheapest model that can follow instructions is the right model for most work. Architecture creates intelligence; expensive models are for judgment calls.

6. **The cascade thesis**: Cheap reading → moderate reasoning → expensive construction. Each tier adds value that the previous tier cannot provide.

7. **Benchmark integrity**: Generation must NEVER see rubric criteria, match_criteria, scorer outputs, task IDs, or evaluator-only metadata.

---

## 15. Confidence Ratings

Based on Tesla process + 3 Codex review rounds + adversarial audit:

| Component | Confidence | Status |
|-----------|-----------|--------|
| Control-system architecture | 9/10 | Strong theoretical basis + partially implemented |
| Blackboard as shared state | 9/10 | Implemented and working |
| Three-tier cascade | 8/10 | Implemented, pricing validated |
| Debt sensors as lenses | 7/10 | Implemented but sensor epistemology unproven |
| Commitment contracts | 7/10 | Partially implemented (obligations + survival trace) |
| Task-world construction | 6/10 | Seed plan exists, revisability not yet implemented |
| Operational gap enforcement | 6/10 | Conceptually clear, not yet implemented |
| Damping mechanisms | 7/10 | Budget governor exists, finer damping needed |
| Custody-break taxonomy | 8/10 | Empirically derived from 1251-task benchmark |
| Object permanence | 5/10 | Not implemented, clear need from introspection |
| Convergence as proof | 6/10 | Adversarial check exists, evidence-based convergence not yet |

---

## 16. Verb Analysis (Empirical)

From Cycle23 benchmark (1251 tasks), criteria pass rate by task verb:

| Verb | Criteria Pass Rate | Interpretation |
|------|-------------------|----------------|
| draft | 90.22% | Construction tasks — system's strength |
| analyze | 86.42% | Reasoning tasks — good but room for improvement |
| compare | 77.14% | Cross-document — relation debt sensors target this |
| extract | 75.67% | Precision extraction — source-object sensors target this |
| identify | 74.26% | Discovery — task-world construction targets this |

**Implication:** The biggest ROI improvements come from compare/extract/identify tasks, which are exactly the failure modes that debt sensors and task-world construction address.

---

## 17. Key Insight from Cross-Reading

The Tesla process designed a good engineering skeleton. The introspection provided the theory of **matter custody** — the system's responsibility for preserving the integrity of its understanding across every transformation from source to artifact.

The synthesis: **architecture provides the skeleton, custody provides the soul.** A well-designed control loop with debt sensors is necessary but not sufficient. The system must also maintain a deep sense of what it knows, what it doesn't know, what it committed to, and whether those commitments were fulfilled.

"The system often has lexical contact with the right material but lacks a task-specific relational model that forces exact field completion." — Cycle23 Introspection

This is the gap between 77% and 95%. Not missing information, but missing **custody** of information that was seen.
