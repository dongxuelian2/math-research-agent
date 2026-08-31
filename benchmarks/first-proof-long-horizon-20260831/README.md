# First Proof long-horizon benchmark

This run compares a one-shot direct model call with the repository's dynamic planner/worker/verifier workflow on five research-level problems from *First Proof Second Batch*. The run deliberately uses a generous parallel budget: the goal is to expose long-range planning, revision, and proof-gap detection rather than to maximize the number of cheap questions.

## Purpose

The primary measurement is long-horizon mathematical reasoning: whether an initial proof plan identifies the real bottleneck, whether later work follows and revises that plan, and whether the final submission closes every critical dependency without hiding a gap behind phrases such as “standard” or “routine.” Exact correctness remains essential, but it is not the only ranking signal.

## Problem subset

- `prob-001`: computability theory
- `prob-003`: discrete probability
- `prob-004`: metric geometry
- `prob-006`: lattice theory
- `prob-009`: algebraic combinatorics

The source snapshot is pinned in `manifest.json`. Problem documents and human solutions are from the [First Proof Batch 2 repository](https://github.com/1stproof/batch-2), commit `274625a22e4748d5f9264ba3614353461520bd20`, and are licensed CC BY-SA 4.0 by that repository. The associated methodology and results paper is [First Proof Second Batch](https://arxiv.org/abs/2606.18119).

## Isolation and budgets

- Human solutions and prior AI submissions are not provided to either solver track.
- Both tracks use the same configured Gemini 3.7 Flash model at high reasoning effort and a 65,536-token per-call output ceiling.
- The direct track gets one model turn.
- The agent track gets up to 6 concurrent workers, 10 workflow steps, 12 retained context messages, 120 worker calls, 120 verifier calls, and 24 hours of wall time per attempt.
- External literature lookup is intentionally unavailable during solving. A planner may record a query, but receives only an explicit closed-book notice.
- Two problem/track jobs run concurrently (`--concurrency 2`); the provider-facing call timeout is 15 minutes and each job may retry three times. This is the enlarged configuration used for the recorded run.

## Review protocol

After both submissions exist, the review script fetches the pinned human solution. Each pair is judged twice with anonymous labels and reversed A/B order. The 100-point rubric is:

- mathematical correctness: 30
- proof planning and execution: 25
- critical-step completeness: 25
- insight and useful partial progress: 10
- exposition and attribution: 10

Each pass also assigns one First Proof-style verdict: `ESSENTIALLY_FLAWLESS`, `MINOR_REVISIONS`, `MAJOR_REVISIONS`, or `REJECT`. Automated review is evidence for the final audit, not a substitute for inspecting the disputed mathematical steps and persisted workflow artifacts.

## Commands

```bash
node backend/scripts/first-proof-long-horizon-benchmark.mjs \
  --concurrency 2 \
  --agent-workers 6 \
  --agent-steps 10 \
  --history-limit 12 \
  --max-tokens 65536 \
  --attempts 3 \
  --call-timeout-seconds 900 \
  --wall-time-seconds 86400

node backend/scripts/first-proof-long-horizon-review.mjs \
  --benchmark benchmarks/first-proof-long-horizon-20260831 \
  --passes 2 \
  --max-tokens 32768
```

The live run writes full submissions under `results/`, durable planner/worker/verifier artifacts under `sessions/`, and blinded reviews under `reviews/`. The outer wrapper completed 7/10 formal track jobs. The missing non-empty evidence was recovered from persisted sessions for `prob-004/agent` and `prob-006/agent`; `prob-003/direct` was rerun in a separate recovery directory. Recovery records are explicitly marked and are not silently presented as ordinary formal results.

The benchmark contains two distinct layers of evidence: the workflow's internal verifier state, and a separate double-blind referee pass against the pinned human solution. A `PROVED`/`CORRECT` workflow state is therefore not treated as ground truth by itself. In particular, the referee can downgrade a polished but centrally invalid proof, while rewarding an honest partial result that isolates an open obstruction.

## Recovery and review commands

```bash
node backend/scripts/first-proof-long-horizon-recover.mjs \
  --benchmark benchmarks/first-proof-long-horizon-20260831 \
  --problem-ids prob-004,prob-006 \
  --tracks agent

node backend/scripts/first-proof-long-horizon-benchmark.mjs \
  --out benchmarks/first-proof-long-horizon-20260831/recovery-prob-003-direct \
  --problem-ids prob-003 \
  --tracks direct \
  --resume false \
  --concurrency 1 \
  --max-tokens 65536 \
  --attempts 3 \
  --call-timeout-seconds 900 \
  --wall-time-seconds 86400

node backend/scripts/first-proof-long-horizon-review.mjs \
  --benchmark benchmarks/first-proof-long-horizon-20260831 \
  --passes 2 \
  --max-tokens 32768
```
