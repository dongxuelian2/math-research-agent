# Heterogeneous Model Routing and Asynchronous Literature Architecture

## Status

Implementation status: `READY_FOR_PILOT_CAMPAIGN` (with explicit per-task
public-query transmission approval still required).

The proof, literature, and verification queues; dependency-local blocking;
DUAL_TRACK; per-call heterogeneous routing; escalation; authority isolation;
checkpoint migration; zero-cost mock graph; public scholarly metadata search;
hashed HTML/PDF retrieval; and deterministic authority verification are
implemented and tested.  Sol task-scoped cancellation and Luna Max account
smokes passed on 2026-08-13.  The example configuration keeps external
transmission disabled until a campaign supplies a minimized public query and a
per-task approval.  Proof fallback from `LITERATURE_PROVIDER_UNAVAILABLE` is
explicit and reportable.

No mathematical campaign or proof search was started by this upgrade.

## Implementation map

- `providers.py` and `codex_cli_provider.py`: provider validation/factory,
  installed Codex reasoning efforts, and opt-in CLI `--search` transport.
- `routing.py`: centralized role-to-tier defaults, config layering, monotone
  escalation, strategic caps, explicit fallback, prompt composition, and
  per-call metadata/usage.
- `scheduler.py` and `orchestrator.py`: Worker role/obligation/branch headers,
  routed Planner/Worker/Verifier/Auditor clients, and Worker literature-request
  and verifier-disagreement event bridges.
- `pipelines.py`: persistent proof/literature/verification/blocked queues,
  dependency DAG, events, non-blocking Future runtime, DUAL_TRACK, source
  deduplication, cancellation/redirect, and budget enforcement.
- `literature.py`: Literature request validation, synthesis format, external
  authority registry, positive and negative memory, trust gates, provider
  status, minimized-query transmission gate, and scholarly-adapter bridge.
- `scholarly.py`: OpenAlex primary and Crossref secondary metadata adapters,
  stable-identifier/version deduplication, bounded retries/rate limiting/cache,
  public HTML/PDF retrieval, content hashes, and deterministic theorem-label
  extraction.
- `campaign.py`: campaign override slots and deterministic checkpoint
  migration. Completed run evidence remains byte-immutable.
- `certification.py`: planner-free certification calls use the same router;
  the existing deterministic Trust Kernel and final gate are unchanged.

## Architecture

```text
                         Literature Lead
                      / Searchers / Readers
                     /  Synthesis / Authority audit
                    v
Strategic Planner -> obligation DAG + event queue
                    |                  |
                    v                  v
              Proof Worker pool   Verification pool
                    \                  /
                     verified local lemma flow
                              |
                    existing audits + Archivist
```

The Planner adds a new obligation to the DAG.  Literature-first is attached to
that obligation only.  Its unresolved dependents move to `BLOCKED_QUEUE`; old
proof tasks and unrelated siblings remain dispatchable.  A literature result
creates an event that can verify/reconstruct, release proof work, escalate,
or redirect the single approved speculative Worker.  A complete candidate
still traverses the existing Counterexample, Dependency, Exhaustiveness,
Boundary, Final Proof, deterministic authority, and Archivist gates.

## Routing and escalation

The centralized default tiers are:

| Role | Default tier |
|---|---|
| Planner, alternative-proof, architecture audit, final proof audit | strategic |
| constructive, adversarial, theorem verifier, Counterexample/Exhaustiveness/Boundary auditors, Literature Lead/Synthesizer | research |
| boundary, dependency, reconstruction, Worker Verifier, Dependency Auditor, Literature Searcher/Reader/Authority Auditor | routine |

The installed Codex CLI 0.147.0 bundled catalog reports:

- `gpt-5.6-sol`: `low`, `medium`, `high`, `xhigh`, `max`, `ultra`;
- `gpt-5.6-luna`: `low`, `medium`, `high`, `xhigh`, `max`;
- `gpt-5.6-terra`: `low`, `medium`, `high`, `xhigh`, `max`, `ultra`.

This architecture maps research to `gpt-5.6-sol/high` and strategic to
`gpt-5.6-sol/max`.  `max` is the concrete replacement for the informal phrase
"Extreme High".  `ultra` is not used for an individual strategic obligation
because the installed catalog describes it as automatic task delegation.

Routine is configured as the real catalog slug `gpt-5.6-luna/max`; the bounded
account-level smoke returned `LIVE_PROVIDER_OK` with no fallback.  Research and
strategic remain `gpt-5.6-sol/high` and `gpt-5.6-sol/max`.  Any route fallback
still records `fallback=true` and the reason.

Escalation is monotone over an obligation lifecycle:

```text
routine -> research -> strategic
```

Triggers include repeated typed failure, Worker/Verifier disagreement, stalled
frontier, high-value result, theorem-level closure, and proof candidate.  The
stored escalation record carries the previous route, failure reason, work
already attempted, and the material change required.  Global/per-step/per-
obligation strategic caps cause an explicit research fallback.

## Literature trust and provider status

The external authority registry never shares project theorem truth state.  It
persists two separate tables.  `source_theorems` records source identity,
bibliographic metadata, artifact/text/extraction hashes, exact span, theorem
label/location, and normalized statement; only this deterministic path can
produce `VERIFIED_SOURCE_THEOREM`.  `applicability_records` is an
obligation-specific relation keyed by theorem, obligation, normalized target,
and an authorized-assumption snapshot.  It can produce
`APPLICABLE_EXTERNAL_AUTHORITY` only after a research-tier reconstructor emits
per-hypothesis, conclusion, direction, normalization, exception, notation, and
local-lemma evidence and a distinct research-tier theorem verifier returns
`APPLICABLE`.  Missing or ambiguous evidence fails closed.

Ordinary obligation context fields such as `hypotheses_match`,
`implication_direction_match`, or `exception_check_pass` have no authority in
either promotion path.  The compatibility spelling
`VERIFIED_EXTERNAL_AUTHORITY` means source authenticity only and cannot close
an obligation.  Source discovery emits `SOURCE_THEOREM_VERIFIED`; only the
later `APPLICABLE_AUTHORITY_AVAILABLE` event may close via literature.

Codex CLI exposes an opt-in live-search path, while the example config keeps
`external_transmission_approved=false`.  The scholarly adapter itself uses
only a minimized public query and caches normalized OpenAlex/Crossref metadata;
the full-text retriever stores a content-addressed public artifact before
extracting theorem text.  A real campaign additionally needs
`external_search_approved=true` and the operator's explicit transmission
approval; the executor never sends the project context bundle.

The live applicability smoke searched OpenAlex for `Pythagorean theorem`, retrieved
the public Europe PMC PDF for Kadison's paper (DOI
`10.1073/pnas.032677199`, PMCID `PMC123622`), extracted Proposition 2 with
`pdftotext`, and passed every deterministic registry gate.  Its JSON evidence
is under `E:\tool\math\validation-evidence\applicability-repair-20260813\phase-D-E-F-production-5`;
the production chain closed only the exact-match positive obligation after deep
reading, source audit, applicability reconstruction, and independent theorem
verification.  Same-source hypothesis and direction negatives stayed open.

## Checkpoint and compatibility rules

- Old role-specific configs preserve their exact Planner/Worker/Auditor models.
- A legacy top-level `provider/model/reasoning_effort` maps all tiers to that
  same route.
- Active schema-v1 run state is deterministically migrated to schema v2.
- Completed run bytes are never changed in place.
- Completed task IDs are removed from restored queues and are not rerun.
- Obligation tier, failure/escalation state, routes, literature tasks/sources,
  source-theorem status, applicability artifacts/status/snapshot, queues, DAG,
  and DUAL_TRACK state are persisted.  A changed target or authorized
  assumption snapshot marks inherited applicability `NEEDS_REVALIDATION`.
- Existing theorem registries and all Archivist/Trust Kernel semantics remain
  unchanged.

## Tests

The focused architecture suite covers routing, mixed-model concurrency,
escalation/caps, asynchronous pipeline scheduling, dependency blocking,
DUAL_TRACK, randomized search completion, Reader/citation-chain derivation,
source deduplication, literature trust attacks, and checkpoint migration.
The full applicable regression is recorded in the delivery report for the
change that introduced this file.

## Production harness wiring

`ResearchOrchestrator` now owns one `AsynchronousPipelineRuntime` per active
run.  `CampaignEngine` owns the campaign lifecycle and persists the scheduler
and router snapshots after every orchestrator return; successor/resume runs
inherit only logical JSON state.  `AsyncDAGScheduler` is the obligation-level
owner and `OpenProver` remains the long-horizon proof executor for the target
obligation.

The runtime has concrete consumers for all three queues.  Literature tasks use
`LiteratureTaskExecutor`, verification tasks use the routed theorem verifier,
and proof tasks use the routed constructive handler for non-target DAG work.
Literature/verification windows are monitored while the target OpenProver call
is running, so a literature request does not freeze sibling proof branches.

An authority auditor can only return an authenticity candidate; the production
executor passes its complete record and deterministic evidence to
`ExternalAuthorityRegistry.verify()`.  This yields source truth only.  The
reconstructor cannot approve itself: its routed call id must differ from the
independent verifier call id, and the Registry reopens and hashes the durable
reconstruction before applicability promotion.  DUAL_TRACK speculative proof
continues after source verification and is redirected only after
`APPLICABLE_EXTERNAL_AUTHORITY` is durably recorded for the current snapshot.

Task cancellation is scoped through `TaskExecutionContext` to the runtime
future and the provider client created for that task.  Resume reconciliation
changes stale `ACTIVE` tasks to `READY`/`RETRY_READY`, preserves original call
and attempt metadata, and never restores a Future or process handle.  A shared
`AtomicResourceBudget` reserves provider calls before concurrent workers start,
preventing per-pipeline accounting from overshooting the campaign hard cap.
