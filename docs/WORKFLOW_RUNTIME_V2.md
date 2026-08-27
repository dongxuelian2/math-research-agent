# Dynamic workflow runtime v2

The dynamic proof controller now uses the existing typed `ProofPlan` as a small, auditable workflow IR instead of requiring a new arbitrary-code sandbox.

## Autonomous DAG execution

A controller may emit one `spawn` action containing a dependency graph. Before the durable core sees the plan, `compileWorkflowPlan()` lowers that graph into topological ready-frontier actions. Independent tasks remain in the same frontier and respect `maxWorkers`; dependent frontiers become ordinary persisted action receipts. This means a graph such as `A || B -> C -> D` executes inside one controller round while retaining action-level crash resume.

The controller is still called again after the compiled plan finishes, so mathematical failures, verifier feedback, new evidence, or exhausted frontiers can trigger a fresh plan. The design therefore keeps adaptive research while removing Planner calls that existed only to cross an already-known dependency barrier.

## Dependency dataflow

`dependsOn` now carries data as well as control. Before a dependent Worker or Verifier runs, the runtime reads the latest durable `proof/research_result` for each predecessor and appends the exact predecessor output under `Runtime dependency dataflow` in `referencedMaterials`.

This removes the previous hidden dependency on the next controller turn copying or paraphrasing an upstream result into a downstream task. The durable event log remains the source of truth and survives process restart.

## Verifier isolation

Production proof roles use `createCandidateVerifierPool()`. Each candidate id is mapped to its own persistent verifier `AgentCore`/Session, while repeated verification of the same candidate resolves to the same verifier identity. Candidate audits can therefore fan out concurrently without sharing `AgentCore.activeRun` or leaking verifier conversation state across candidates.

## What remains deliberately outside v2

The workflow language is still typed and proof-specific. Arbitrary JavaScript loops and branches are intentionally not executed. Future control nodes (branch, reduce, bounded loop, adjudication) should be added as typed IR nodes so they can participate in the same persistence, budget, invalidation, and audit model.
