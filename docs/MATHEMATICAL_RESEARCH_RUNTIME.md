# Mathematical Research Runtime v1

MRR v1.1 is the durable strategic layer above the existing TypeScript `ProofRuntime`. It does not restore OpenProver or introduce a second proof engine. `ResearchRuntime` selects and persists strategic work; `ProofRuntime` is the bounded tactical execution kernel whose default `dynamic` mode lets the model choose the task/agent workflow each round (with a `legacy` prompt mode for rollback); `ResearchStateReducer` is the only mathematical truth writer.

## Production chain

```text
HTTP research API
  -> durable ResearchProject
  -> semantic corpus bootstrap
  -> model ResearchDirector (validated; deterministic fallback on protocol failure)
  -> durable TacticalDirective + exact ResearchObligation
  -> relevance-bounded ContextManifest
  -> ProofRuntime / configured AgentCore roles
  -> automatic artifact-access receipts
  -> TacticalResearchResult
       verified contributions OR exact target submission
  -> ResearchStateReducer
  -> immutable bodies + atomic AcceptedEffect/AuthorityReceipt/claim revision
  -> later-cycle unified retrieval
  -> root readiness -> synthesis -> primary and fresh final audit
```

The concrete production path is:

- `ProofApiServer.handleResearchRoute` in `backend/src/api/server.ts`: public lifecycle and inspection API.
- `CorpusService.bootstrap` in `backend/src/research/corpus.ts`: per-file semantic analysis, merge, dependency/route/coverage reconstruction, review, and durable report.
- `AgentResearchDirector` plus `ResearchDirector.decide` in `backend/src/research/agent-role.ts` and `runtime.ts`: model strategy, strict validation, and fallback.
- `ProofApiServer.runResearchProof`: bridge from a selected obligation and exact `TacticalDirective` into `ProofWorkflow`, with the target gate, project config snapshot, and research tools.
- `ProofRuntime.run` in `backend/src/proof/runtime.ts`: persisted step plans, model-authored task graph, ready-frontier queue, logical-agent factory, durable Worker/partial results, independent verification, exact same-step resume, dependency invalidation, and exact submission action.
- `ResearchEvidenceRecorder` and `createResearchTools`: automatic role/task-scoped artifact access receipts.
- `ResearchStateReducer.applyTactical`: centralized kind validation, truth-gate validation, typed contribution conversion, graph/coverage/route effects, and one atomic claim/effect/authority/event commit.
- `ResearchRetrievalService` and `ResearchContextBuilder`: unified cross-cycle memory and bounded context selection.
- `RootClosureService.synthesizeAndAudit`: dependency/coverage readiness, exact-body synthesis, fresh audits, final promotion, or blocker creation.

## Truth and contribution protocol

`TacticalResearchResult` separates target status from intermediate value. A correct Worker subtask is never enough to prove its parent obligation.

`TARGET_PROVED` is accepted only when all of the following agree:

- the active obligation ID;
- the target claim ID;
- a Planner `submit_target_proof` action;
- a candidate whose scope is `TARGET`;
- a `CORRECT` independent verifier receipt for the exact candidate body;
- for a direct root-target proof, a fresh configured secondary audit.

Verified intermediate results use `VerifiedResearchContribution`. Supported kinds include lemma, reduction, case split/closure, counterexample, construction, bound, obstruction, structural observation, and literature application. Kind-specific validation rejects empty/self/duplicate decompositions, unscoped case closure/refutation, unresolved proof bodies, missing receipts, and lost assumptions before state mutation. The reducer converts accepted results deterministically into claim revisions, support/dependency edges, obligations, coverage records, route entries, immutable bodies, and canonical authority receipts.

An `AuthorityReceipt` also snapshots exact ordinary dependency revisions and assumption-discharge dependencies. Each discharge names the dependent revision, the exact witness revision/receipt/effect, and the witness proof artifact/hash. Receipts remain historical facts; a separate validation record marks current authority `ACTIVE`, `SUPERSEDED`, `STALE`, or `INVALIDATED`. Reverse invalidation walks both edge classes, reopens idempotent obligations, reconciles project truth, and makes a prior final proof historical/stale without deleting its immutable body.

Model prose is never truth authority. Historical text is never current proof authority merely because it says “proved.”

## Durable mathematical memory and evidence

`ResearchStore` records immutable artifact metadata and exact bodies for corpus sources, Worker candidates, promoted proofs, counterexamples, literature, computations, manifests, receipts, checkpoints, synthesis, and final proofs. `resolveArtifact` requires the registered ID, expected SHA-256, present body, and matching body hash.

Agents can use:

- `artifact_search`, `artifact_read`, `artifact_metadata` over unified memory;
- `corpus_search`, `corpus_read` for initial sources;
- `scratch_write`, `scratch_read` within the current attempt;
- `controlled_computation` for configured non-shell executables.

Unified retrieval searches corpus, promoted proof, counterexample, literature, computation, and route evidence rather than a fixed first-N corpus prefix. A later Worker can discover a promoted lemma without that lemma being manually injected into its model response.

Every search/read/metadata operation creates an automatic `ToolEvidenceReceipt` with role, logical task, artifact ID/hash, range, tool-call ID, time, and classification. Search produces `DISCOVERED` evidence only; exact body access produces `BODY_READ`. Candidate reliance is accepted only when `reliedOnArtifactIds` is a subset of actual body reads. Trust receipts separately retain context availability, discovery, Worker reads, declared reliance, Verifier reads, and secondary-auditor reads.

If a Worker relies on an artifact, the independent Verifier must actually read the same exact artifact before that candidate can be converted into a verified contribution or target submission. A context manifest is an audit object, not evidence of use.

## Semantic bootstrap and authority

The configured `corpus_bootstrapper` is constructed through the same production model/provider factory as other roles. Bootstrap runs these durable stages:

```text
MANIFEST_INDEX
PER_FILE_SEMANTIC_ANALYSIS
ENTITY_MERGE
DEPENDENCY_RECONSTRUCTION
FAILED_ROUTE_RECONSTRUCTION
FRONTIER_PROPOSAL
CONSISTENCY_REVIEW
PROVISIONAL_IMPORT
```

It reconstructs definitions, claims, open problems, reductions, case splits, computation notes, dependencies, and historical failed routes. Historical case splits create explicit open coverage. Failed strategies enter `RouteLedger`, not the claim graph. Model failure falls back per file to deterministic candidate extraction and records warnings; it does not silently turn prose into truth.

Import authority is explicit. Default `PROVISIONAL_IMPORTED` input remains open/provisional until current verification. Source hash changes invalidate affected trust and propagate `NEEDS_REVALIDATION` downstream.

## Strategy, routes, and anti-stall

The Director receives a compact semantic snapshot: frontier, root blockers, verified/recent/blocked claims, route mechanisms and failures, coverage gaps, stall counter, budget, and config revision. Its structured decision is validated against durable IDs and active frontier state. Invalid output becomes a recorded `FALLBACK_DIRECTED` decision. A durable `TacticalDirective` carries the validated action, target, route/mechanism, desired contribution kind, failed-route memory, action-specific intent, and budget allocation into the real Planner context.

Failed routes store family, mechanism, strategy, evidence, failure mechanism/domain, and typed reopen predicates such as `CLAIM_PROVED(claimId)`. Reopening is machine-evaluated; no substring matching is used.

A structural-probe observation does not reset the stall counter. Structural progress means a verified claim/contribution, graph or coverage change, counterexample, or concrete strategic state change. At the threshold, fallback creates a distinct active route; a free-text observation is insufficient. Probe count, active-frontier size, cycles, and checkpoint cadence are configured limits.

## Execution, resume, and storage

The durable execution ledger records Planner steps, Workers, Verifiers, merge, target submission, and result conversion with stable identities and statuses. Worker results and task status transitions are persisted as they complete. On restart, completed tasks are hydrated and not rerun; partial/blocked tasks remain visible to the controller for continuation or replacement, and missing Verifiers execute.

Research orchestration uses stable IDs for cycles, decisions, directives, jobs, attempts, plans, execution tasks, effects, authority receipts, and events. Execution is at least once; mathematical effects are exactly once. A plan is written before Worker dispatch. Crashes resume the current step without asking the Planner to recreate task identities; completed Workers are hydrated, interrupted Workers and missing Verifiers run, and only then can the next plan be requested. A changed/missing/hash-invalid or no-longer-current authority dependency makes the old plan `STALE` and records the replan reason.

Canonical `state.json` publication is write-then-rename, and mutations are serialized by the server's single `ResearchStore`. Artifact bodies are immutable. Claim transition, `AcceptedEffect`, `AuthorityReceipt`, and canonical event share one transaction, so retry yields either no authority or the complete effect exactly once. This is correct for one server writer per data directory. It is not an inter-process database: running multiple independent writers against one project directory is unsupported.

## Literature, closure, and formalization

The production literature path uses an injectable `LiteratureProvider`; the default is the credential-free OpenAlex discovery/acquisition adapter. Authority stages distinguish discovery, exact source acquisition, applicability proposal/verification, and acceptance. Search snippets never become theorem authority. Accepted exact sources enter unified retrieval, and Worker/Verifier reads are receipted.

Root readiness runs the global project invariant checker and reports dependency blockers, coverage blockers, missing or hash-invalid artifacts, stale/missing authority receipts, unresolved assumptions, and missing final audit. Verified reductions may close recursively only when every dependency, authority receipt, coverage assertion, and assumption-discharge condition is valid.

The synthesis manifest includes every discharge witness and its proof body. A synthesizer must declare each required witness artifact in `usedArtifactIds`, and the fresh final auditor must record an actual read of each discharge proof. `/result` returns a final proof as current only while its root authority remains active; stale historical proof availability is reported separately.

Plan/action recovery persists intent and stable logical action IDs before execution and stores completed action results. Completed terminal actions such as `STOP` rehydrate their terminal effect on resume. External provider, literature, and computation calls are at-least-once across the crash window between external success and a persisted completion receipt; idempotency keys and reusable result artifacts are used where supported. Canonical mathematical `AcceptedEffect` application remains exactly once.

The configured Synthesizer receives exact transitive authoritative bodies and a synthesis manifest, and must return `usedArtifactIds` drawn from that manifest. Both the primary auditor and fresh `secondary_auditor` receive the exact used bodies and must actually read every used source/dependency before acceptance. Rejection leaves the root unproved, persists the candidate and precise audit findings/receipts, and creates a repair obligation; acceptance persists exact final provenance and grants root authority through the reducer.

Research formalization supports `informal_only`, `formalize_existing`, and `prove_and_formalize` through `POST .../formalization`. The configured model Formalizer only drafts `LEAN_SOURCE`. `CommandProofFormalVerifier` runs the configured executable with `shell: false`; only an exit-successful process creates `FORMAL_PROOF`/`FORMAL_CERTIFICATE` authority. Missing Lean yields `BLOCKED_FORMAL`. To prevent unrelated certificates, the root objective must include the exact Lean theorem/lemma/def declaration.

## Controlled command boundary

`controlled_computation` is a `CONTROLLED_COMMAND_RUNNER`, not a hardened sandbox. It uses `spawn(executable,args,{shell:false})`, a configured executable allowlist, attempt scratch as CWD, timeout, stdout/stderr caps, and a filtered environment without inherited credential variables. Scratch traversal and symlink escape are rejected.

An allowlisted general-purpose interpreter can still access operating-system paths available to the server process. For hostile model/code isolation, deploy the server in an OS/container sandbox with a restricted account and filesystem mounts. The runtime deliberately does not claim stronger isolation.

## Public research API

Base: `/v1/research/projects`.

- create/list/open projects;
- set root objective;
- attach/list/ingest/reindex/search/read corpus;
- run bootstrap and inspect bootstrap report;
- start/resume/cancel/checkpoint/invalidate;
- inspect frontier, claims/claim detail, dependencies, coverage, routes/route detail;
- inspect artifacts, exact body, and metadata;
- run/list literature;
- inspect events and checkpoints;
- inspect root readiness, trigger synthesis, and retrieve final result;
- inspect or trigger formalization.

Start/resume is asynchronous. Durable project state is rediscovered after server restart. `GET /v1/research/projects/:id/audit` is read-only and exposes the effective config revision, migration reports, plans/tasks, evidence/trust/authority receipts, accepted effects, bootstrap reports, global invariant result, and root-readiness blockers.

## Configuration authority

`configs/math-agent.toml` controls research cycles, checkpoint/stall/probe/frontier limits, proof Worker concurrency/steps, Planner/Worker/Verifier/secondary/literature/tool/wall-time budgets, corpus roots/import policy, literature availability, tool capabilities/executables/boundary, formal process, and every model-role mapping. Role `max_turns` and `timeout_seconds` are enforced by production role construction.

The effective configuration is snapshotted into a project when it is created. Director, roles, ProofRuntime, tools, budgets, bootstrap, literature, synthesis, and formalization read that snapshot. Editing config affects subsequently created projects only. A migrated legacy project with no valid snapshot is never silently run with current live TOML; in a configured production server it fails closed until an explicit configuration migration/new project is chosen.

## Verification

```powershell
pnpm run typecheck
pnpm run test:proof
pnpm run build
```

The production acceptance suite is `backend/test/research-production-protocol.test.ts`; adversarial v1.1 coverage is in `research-hardening.test.ts` and `proof-queue.test.ts`. Together they cover the target truth gate, AgentCore protocol wiring, cross-cycle memory and evidence receipts, semantic bootstrap, literature tiers, snapshotted config, restart, root synthesis/final-audit rejection, process-gated formalization, atomic authority fault injection, exact plan resume/invalidation, and the full multi-cycle production E2E.

## Remaining boundaries

- One server writer per data directory; there is no inter-process lock or database transaction coordinator.
- `CONTROLLED_COMMAND_RUNNER` is defense-in-depth, not hostile-code isolation.
- OpenAlex acquisition depends on source accessibility and intentionally refuses inaccessible/PDF-only bodies rather than treating metadata as theorem authority.
- Lean is an external dependency. The deterministic suite proves success/failure process semantics, but this repository does not bundle Lean.
- Model quality ultimately bounds discovery and proof progress; protocol validation prevents unsupported promotion but cannot guarantee theorem discovery.
- Deterministic `MockProvider` E2E proves production protocol and persistence behavior; it does not prove autonomous mathematical intelligence or real-provider reliability.
