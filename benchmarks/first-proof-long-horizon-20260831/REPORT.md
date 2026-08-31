# Long-horizon proof benchmark report

## Scope and source

This benchmark uses five problems from the **First Proof Second Batch**: computability theory (`prob-001`), discrete probability (`prob-003`), metric geometry (`prob-004`), lattice theory (`prob-006`), and algebraic combinatorics (`prob-009`). The source is pinned to commit `274625a22e4748d5f9264ba3614353461520bd20` in the [First Proof Batch 2 repository](https://github.com/1stproof/batch-2). The associated [paper](https://arxiv.org/abs/2606.18119) describes the research-problem benchmark and its public human/AI evaluation material.

The problems and human solutions were hidden from both solver tracks. The human solution was fetched only by the separate referee stage. This matters for exploratory questions: a response is rewarded for proving a valid range and isolating a genuine obstruction, not for forcing a complete classification.

## Run configuration

| Item | Value |
|---|---|
| Model | Gemini 3.7 Flash, high reasoning |
| Per-call output ceiling | 65,536 tokens |
| Solver tracks | one-shot direct; dynamic planner/worker/verifier agent |
| Parallel problem/track jobs | 2 |
| Agent workers / workflow steps | 6 / 10 |
| Retained context | 12 messages |
| Worker and verifier budgets | 120 calls each |
| Provider call timeout / wall time | 900 s / 24 h per attempt |
| Attempts | 3 |
| Referee passes | 2, A/B order reversed |

The outer run completed 7 of 10 formal jobs. `prob-003/direct` was rerun in a dedicated recovery directory. For the two long agent jobs that timed out at the wrapper boundary, the durable session state was recovered: `prob-004/agent` is explicitly **unverified** (its best candidate was internally `CRITICALLY_FLAWED`), while `prob-006/agent` is **verified in persisted workflow state**. These provenance labels remain visible to the referee and do not turn a recovered candidate into a formal result.

## Blind referee results

Scores are means over the two reversed-order passes. “Plan” is proof planning/execution (25 points); “Critical” is critical-step completeness and absence of skipped hard steps (25 points).

| Problem | Direct (total / Plan / Critical) | Agent (total / Plan / Critical) | Pairwise | Referee conclusion |
|---|---:|---:|---|---|
| `prob-001` | 38.0 / 9.5 / 8.0 | 38.5 / 9.5 / 8.0 | 1–1 | Both `REJECT`: the computability jump `B' ≤ B ⊕ ∅'` is false; neither witness satisfies the cone requirement. |
| `prob-003` | 44.5 / 11.5 / 9.0 | **88.5 / 22.5 / 21.0** | 0–2 agent | Direct forces a false iff classification. Agent proves substantial ranges and honestly isolates the remaining asymmetric range as open; verdicts `MAJOR_REVISIONS` / `ESSENTIALLY_FLAWLESS`. |
| `prob-004` | 21.0 / 5.5 / 3.0 | 20.0 / 5.5 / 3.0 | 1–1 | Both `REJECT`: the central Stokes/coarea flux argument fails, including an invalid boundary estimate and a reversed square-root inequality. |
| `prob-006` | 46.5 / 12.5 / 9.0 | **66.0 / 17.5 / 14.0** | 0–2 agent | Agent supplies useful energy identities and case structure, but both passes find an unproved/false algebraic branch inequality; `MAJOR_REVISIONS`. |
| `prob-009` | 25.5 / 6.5 / 4.0 | **88.0 / 22.0 / 20.5** | 0–2 agent | Direct formula is false from `n=4`. Agent derives the correct alternating hook formula and checks small cases, but does not provide the final sign-reversing involution; `MAJOR_REVISIONS` / `MINOR_REVISIONS`. |

### Aggregate

- Agent wins **8/10** blinded pairwise comparisons; direct wins 2/10, both on the two problems where both submissions were rejected.
- Mean total: **60.2 agent vs 35.1 direct** (+25.1).
- Mean planning/execution: **15.4 vs 9.1** (+6.3).
- Mean critical completeness: **13.3 vs 6.6** (+6.7).
- Direct received `REJECT` in all 10 submission evaluations. Agent received 1 `ESSENTIALLY_FLAWLESS`, 1 `MINOR_REVISIONS`, 4 `MAJOR_REVISIONS`, and 4 `REJECT` verdicts.

The main qualitative result is not merely that the agent writes longer proofs. On `prob-003` and `prob-009`, it preserves the proof plan through several stages, tests boundary cases, and stops at a real obstruction instead of manufacturing a theorem. On `prob-004`, the larger budget produced more attempts and more explicit self-critique, but did not repair the missing linking/isoperimetric machinery; the referee correctly kept the answer at `REJECT`. On `prob-006`, an internal persisted verifier marked a recovered candidate `CORRECT`, yet the independent blind referee still found a central algebraic gap. This is why workflow status is recorded as evidence rather than treated as mathematical ground truth.

## Reproducibility artifacts

- [manifest.json](./manifest.json) — source hash, model, budgets, problem selection, and prompt contract.
- [summary-rough.json](./summary-rough.json) — formal wrapper outcomes and workflow counters.
- [review-summary.json](./review-summary.json) — validated double-blind scores and aggregate results.
- [README.md](./README.md) — isolation, recovery, and rerun commands.
- `sessions/` — durable planner/worker/verifier state, including timed-out jobs.
- `recovered-results/` — explicitly labelled recovery records for the timed-out agent jobs.
