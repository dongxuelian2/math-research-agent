# Research Corpus Archive Integration Map

## Scope and starting point

This map was produced from a read-only inspection at starting commit
`2bafdf48c3db3a4836932187e4f0b231b55fc002`. The pre-existing untracked
`AUDIT_METADATA/critical-layer-proof-as-test/` tree is user-owned and is outside
this change.

The archive subsystem is a projection of already-durable semantic state. It is
not a proof-runtime repository, a corpus-ingestion replacement, or another
mathematical authority.

## Production authority seams

### Ordinary research effects

`backend/src/research/reducer.ts` owns the canonical transition:

1. `ResearchStateReducer.apply` derives a stable `effectId` with
   `effectIdentity`.
2. It validates all referenced artifacts and mathematical invariants.
3. One `ResearchStore.transaction` writes the claim/route transition,
   `AcceptedEffect`, `AuthorityReceipt` (where applicable), and `ResearchEvent`.
4. `ResearchStore.transaction` returns only after atomic replacement of
   `state.json`.

The archive hook will run after step 4. It will receive the immutable apply
request, persisted `AcceptedEffect`, and committed state. It may enqueue an
archive intent, but an enqueue or Git error will not undo or reinterpret the
committed effect.

`ResearchStateReducer.applyTactical` already funnels verified tactical results
through `apply`. Planner output, Worker output, raw verifier prose, and scratch
files therefore remain upstream of the hook.

### Strict theorem closure

`backend/src/research/closure.ts` owns the strict final chain:

1. `RootClosureService.synthesizeAndAudit` constructs a synthesis manifest.
2. It writes a `FINAL_PROOF` candidate and a fresh `AUDIT_RECEIPT`.
3. Two correct audits are converted into a root `PROVED_CLAIM` effect.
4. A second durable transaction records an active `FinalProofAuthority` tied to
   the current root revision and active `AuthorityReceipt`.

The strict archive hook will run only after step 4. The intermediate root
promotion at step 3 is deliberately classified `NO_ARCHIVE` for strict
publication. In this codebase, active `FinalProofAuthority` is the equivalent
of the requested verified PromotionClosure gate.

### Route failures

`ResearchStateReducer.applyTactical` converts non-viable `RouteObservation`
objects into stable `FAILED_ROUTE` or `ROUTE_EXHAUSTED` effects. The reducer
persists the structured route family, mechanism, strategy, scope, evidence,
failure mechanism, and reopen predicate in `ResearchRoute` and the effect event.

Archive classification will be stricter than runtime route persistence. A
route failure is publishable only when it is explicitly mathematical,
scope-qualified, evidence-backed, and has a reopen condition. An ordinary
unsuccessful proof or provider failure remains `NO_ARCHIVE`.

## Existing storage ownership

| Information | Existing owner and path | Archive change |
| --- | --- | --- |
| Source tests | `backend/test/` | None |
| Proof run state | `.math-agent/proof-runs/<run>/` and the existing `ProofRuntime` | None |
| Research attempt scratch | `<research-store>/projects/<project>/scratch/attempt-<id>/` | None |
| Immutable research artifacts | `<research-store>/projects/<project>/artifacts/` via `ResearchStore` | Read by projector only |
| Worker candidates | immutable `WORKER_CANDIDATE` artifacts | Never published directly |
| Audits | project `audits/` sessions and immutable `AUDIT_RECEIPT` artifacts | Referenced as provenance only |
| Candidate/final proof workflow | `ProofRuntime`, `ProofRepository`, and `RootClosureService` | No storage move |
| Phase-7-equivalent synthesis/final/re-audit/closure | `SYNTHESIS_MANIFEST`, `FINAL_PROOF`, `AUDIT_RECEIPT`, `AuthorityReceipt`, and `FinalProofAuthority` in the current ResearchStore layout | Strict post-closure hook only |
| Research truth and history | `ResearchProjectState` via `ResearchStore` | Unchanged authority |
| Archive delivery state | new `corpus-archive/state.json` beside, not inside, ResearchMap state | New domain-owned outbox |
| Long-term knowledge | configured canonical Git checkout under `research/` | New projector owns only this projection |

No existing location will be merged into a universal file manager.

## New components and files

### `backend/src/research/corpus-archive-types.ts`

Defines `CorpusArchiveClass`, `CorpusArchiveIntent`, intent statuses,
`ArchiveReceipt`, publishing configuration, validation results, and the narrow
post-commit sink interface.

### `backend/src/research/corpus-archive-store.ts`

Owns `<project>/corpus-archive/state.json`. It uses the same write-flush-rename
durability model as `ResearchStore`, stable identities, serialized mutations,
strict schema validation, and idempotent intent/receipt writes. It does not
write `ResearchProjectState`.

### `backend/src/research/corpus-archive-policy.ts` and `corpus-archive.ts`

Contain:

- `CorpusArchivePolicy`: typed artifact/effect classification;
- `CorpusNodeResolver`: deterministic existing-node resolution;
- `ResearchCorpusProjector`: isolated Git projection and validation;
- `CorpusArchiveReconciler`: crash recovery and pending-work delivery;
- `CorpusArchiveCoordinator`: post-commit adapter used by production.

### `backend/src/research/corpus-archive-cli.ts`

Provides `status`, `pending`, `inspect`, `retry`, `publish`, and `reconcile`
without requiring direct outbox-file inspection.

## Existing modules to touch

### `backend/src/research/reducer.ts`

Add an optional corpus-archive sink and invoke it after the canonical
transaction returns. The callback is best-effort from the truth operation's
perspective. Existing call sites without a sink retain current behavior.

### `backend/src/research/runtime.ts`

Pass the sink to its reducer and run the archive reconciler at safe resume and
post-effect boundaries. Reconciliation errors are isolated from research state.

### `backend/src/research/closure.ts`

Pass the sink to its reducer and notify it only after the active
`FinalProofAuthority` transaction commits. This is the only strict-result
trigger.

### `backend/src/api/server.ts`

Construct one coordinator from the project's frozen effective configuration,
pass it into research runtime/root closure, and expose read/retry/publish/
reconcile status routes. No route will accept mathematical truth mutations.

### `backend/src/config.ts` and `configs/math-agent.toml`

Extend the existing `[corpus]` section with publishing-specific settings. The
existing `corpus.enabled` continues to govern ingestion. Publishing has its own
`publishing_enabled = false` default so existing installations do not begin
publishing or backfilling.

### Export/package documentation

`backend/src/research/index.ts`, `backend/package.json`, root `package.json`,
`README.md`, and `docs/runtime-durability.md` will expose and document the new
manual surface and recovery guarantees.

## Compatibility seam

Some historical production material discusses bypasses of EffectSlot ownership.
The current inspected production tactical path calls
`ResearchStateReducer.applyTactical`, which in turn calls the stable
`ResearchStateReducer.apply` effect seam. This implementation binds to the
persisted `AcceptedEffect`/event rather than to direct orchestrator prose.

No EffectSlot or runtime coordinator rewrite is required. Reconciliation can
recover an effect committed after the configured recovery lower bound if the
process crashed before the post-commit enqueue. Only projects whose frozen
creation-time configuration enabled publishing receive that lower bound;
legacy/default-disabled projects are not implicitly backfilled.

## Acceptance and regression test plan

All Git tests use temporary local repositories and a deterministic index
fixture. No proof provider is required.

| ID | Test | Expected assertion |
| --- | --- | --- |
| A | `NO_ARCHIVE` | provider/operational/no-progress input creates no intent and no Git diff |
| B | `ATTEMPT` | accepted unresolved reduction lands once under `attempts/` |
| C | `FAILURE` | scope-qualified mathematical route failure renders attempted route, scope, ruled-out/not-ruled-out, and reopen condition under `failures/` |
| D | `RESULT` | verified scoped lemma lands under `results/` from promoted authority |
| E | strict result | only active final proof authority creates a strict result intent/artifact |
| F | candidate safety | `CANDIDATE_PROOF`, `FINAL_PROOF`, or audit alone cannot create strict publication |
| G | retry before commit | a crash with a controlled uncommitted projection resumes the same intent and creates one artifact/commit |
| H | crash after local commit | marker and commit recovery prevent a duplicate commit or artifact |
| I | push/receipt crash | remote containment recovers the missing receipt without a second commit/push effect |
| J | duplicate event | stable source identity creates exactly one intent and one canonical corpus effect |
| K | bad node resolution | missing/ambiguous deterministic mapping becomes `MANUAL_REVIEW` and creates no path |
| L | index idempotency | a second generator execution produces byte-identical diff state |
| M | secret/local path rejection | unsafe content blocks commit and push |
| N | failure semantics | ordinary failed proof/provider failure is `NO_ARCHIVE` |
| O | existing placement | tests, scratch, runtime artifacts, audits, and closure artifacts remain in their existing owners |
| P | truth independence | enqueue/Git failure leaves accepted effect or final proof authority committed |
| Q | resume | activation-bounded scan recreates a missing post-commit intent and reconciles pending work after restart |

Additional unit coverage will validate typed mapping defaults, malformed outbox
fail-closed behavior, lowercase-kebab filenames, duplicate canonical-key moves,
and configuration backward compatibility.
