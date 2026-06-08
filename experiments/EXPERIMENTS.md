# Experiments Log

## exp-002: Synthesis-Fix Ablation (2026-06-08)
**Config**: `.env.phase0_synthesis_fix` | **Commit**: `1ac5dcf` | **Status**: completed

| Metric | Value |
|--------|-------|
| Criteria rate | 78.1% (2178/2789) |
| All-pass | 0/48 |
| vs baseline-match | +0.4pp (wash) |
| Improved tasks | 18 |
| Regressed tasks | 19 |

**What we learned**: Synthesis-side fixes (completeness criteria active + 2000-char summaries) are a wash. Completeness criteria presence doesn't correlate with task score (77.8% with vs 77.0% without). Strategy entry count correlates better. The 7pp gap is driven by missing domain-specific legal knowledge, not synthesis quality. Net 198 criteria lost vs criteria-aided baseline, concentrated in specific identification (95), calculation (49), and legal citation (21) categories.

**Decision**: Abandon synthesis-side ablations. Implement Domain Lens (exp-003).

---

## exp-001: Baseline-Match (2026-06-08)
**Config**: `.env.phase0_baseline_match` | **Commit**: `1ac5dcf` | **Status**: completed

| Metric | Value |
|--------|-------|
| Criteria rate | 77.9% |
| All-pass | 0/48 |
| vs cycle27 (criteria-aided) | -7.3pp |

**What we learned**: Removing rubric criteria from synthesis (criteria=[]) drops rate from 85.2% to 77.9%. This 7.3pp gap is the criteria-aided advantage. The gap is NOT due to synthesis quality — it's due to the system not knowing what domain-specific items to look for without rubric hints.

---

## exp-003: Domain Lens (2026-06-08)
**Config**: `.env.phase0_domain_lens` | **Commit**: `ae0a228` | **Status**: completed — NEGATIVE RESULT

| Metric | Value |
|--------|-------|
| Criteria rate | 75.3% (2106/2789) |
| All-pass | 0/48 |
| vs baseline-match | **-1.8pp (regression)** |
| Improved tasks | 20 |
| Regressed tasks | 23 |
| Bottom-10 avg delta | +2.1pp (need +6pp) |
| Top-10 avg delta | **-5.4pp (need <1.5pp)** |
| Lens generation success | 33/48 (69%) |
| Lens avg entries when rich | 29.3 |

**Go gate: FAILS ALL 4 CRITERIA.**

**What we learned**: Domain lens is net harmful. Even tasks with rich lens data (29+ entries) regressed by -2.0pp avg. Empty-lens tasks also regressed -1.3pp (suggesting code change confounders between commits). Catastrophic regressions on extraction tasks: extract-psa-key-terms -32pp, extract-assets -34pp, draft-subscription -28pp. The ~29 strategy entries from the lens dilute extraction focus on high-performing tasks without compensating benefit. Some tasks improved massively (+30pp draft-antitrust, +17pp draft-privacy-impact, +15pp identify-settlement-proposal) but these gains are overwhelmed by regressions elsewhere.

**Root cause hypothesis**: Injecting 29 strategy entries into the blackboard overwhelms the synthesis stage for tasks that were already performing well. The extraction workers may be spending iterations chasing lens hypotheses instead of doing careful document extraction. The lens helps some specific task types (compliance training, privacy impact) but hurts others (extraction, key-term analysis).

**Decision**: Abandon domain lens approach. Revert to SWARM_DOMAIN_LENS=0. The criteria-free gap must be closed through extraction depth and blackboard evolution quality, not by injecting domain scaffolding.
