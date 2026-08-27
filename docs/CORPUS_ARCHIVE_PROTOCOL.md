# Research Corpus Archive Protocol

Status: normative

## 1. Authority boundary

The Agent's durable semantic state is authoritative. The Git research corpus is
a curated, long-term projection.

`Truth != Research != Runtime != Corpus projection`

The projector may read committed claims, routes, receipts, events, immutable
artifacts, and active final proof authority. It must not mutate theorem truth,
ResearchMap state, audit verdicts, runtime acceptance state, or proof search.
Corpus content cannot promote or invalidate a theorem.

Publishing is an external side effect. A successful mathematical transition is
never rolled back because intent storage, Git, index generation, validation, or
push fails.

## 2. Archive classification

Every source is deterministically classified as one of:

- `NO_ARCHIVE`: no reusable long-term knowledge;
- `ATTEMPT`: verified, reusable, unresolved mathematical development;
- `RESULT`: accepted scoped result or strictly closed theorem result;
- `FAILURE`: reusable negative mathematical knowledge;
- `COMPUTATION`: reusable deterministic computation/result;
- `LITERATURE`: applicability analysis of external literature;
- `STATE_UPDATE`: a durable canonical branch/global state update.

`NO_ARCHIVE` is a normal successful disposition. It creates no corpus file and
is not an error.

## 3. Artifact-to-corpus mapping

| Source | Default disposition |
| --- | --- |
| Raw provider response | `NO_ARCHIVE` |
| Planner prose | `NO_ARCHIVE` |
| Worker raw output / `WORKER_CANDIDATE` | `NO_ARCHIVE` directly |
| Verifier raw output | `NO_ARCHIVE` |
| Scratch file | `NO_ARCHIVE` |
| `CONTEXT_MANIFEST` | `NO_ARCHIVE` |
| `state.json`, routing state, usage | `NO_ARCHIVE` |
| Audit JSON / `AUDIT_RECEIPT` | `NO_ARCHIVE` as prose; provenance reference only |
| `CANDIDATE_PROOF` | `NO_ARCHIVE` directly |
| `FINAL_PROOF` without active final authority | `NO_ARCHIVE` directly |
| Verified unresolved reduction/case split | `ATTEMPT` |
| Verified local/scoped lemma | `RESULT` |
| Scope-qualified reusable route obstruction | `FAILURE` candidate |
| Reusable deterministic `COMPUTATION_RESULT` | `COMPUTATION` |
| Accepted literature applicability analysis | `LITERATURE` |
| Active `FinalProofAuthority` | strict `RESULT` candidate |
| Canonical node status change explicitly requested by a semantic event | `STATE_UPDATE` |

Raw artifacts are never published merely because their file type exists. A
publishable artifact is reached through a committed semantic effect or strict
closure receipt.

## 4. Trigger points

### Research-level trigger

The trigger runs after `ResearchStateReducer.apply` has durably committed an
`AcceptedEffect` and event. Policy may emit an intent for verified reductions,
accepted scoped results, reusable computations/literature, or qualified route
failures. Operational failures and noise emit no intent.

### Truth-promotion trigger

Strict theorem publication runs only after `RootClosureService` durably records
an active `FinalProofAuthority` tied to the current root revision and active root
authority receipt. Candidate presence, model audit prose, or final-proof file
presence is insufficient.

## 5. Lifecycle

The knowledge lifecycle is:

`runtime/scratch -> ATTEMPT -> RESULT | FAILURE | archive/Git history`

When the same semantic claim progresses, the canonical key remains stable. A
later result normally moves/updates the earlier attempt instead of creating
`v2`, `final`, or other duplicate filenames. A materially different claim must
have a different authoritative claim identity.

Local branch state belongs in the node's canonical `README.md` when a semantic
state event actually requires it. Global `STATUS.md` and theorem registries are
not rewritten on every run. The initial projector creates or moves the
classified artifact and lets the deterministic index generator update global
generated indexes.

## 6. Placement rules

The target is the configured checkout of
`dongxuelian2/three-term-decimal-concatenation-square-sum`. Canonical knowledge
lives below an existing `research/` node. Containment is mathematical, while a
local type directory expresses disposition:

- `attempts/`
- `results/`
- `failures/`
- `computations/`
- `literature/`
- `archive/`

Normal filenames are lowercase kebab case. Version words and historical
campaign/round/cycle/strict-layer/critical-layer organization are prohibited.
Git history carries versions.

## 7. Node resolution

`CorpusNodeResolver` is deterministic and fail-closed. Resolution order is:

1. an explicitly configured existing canonical node;
2. an exact entry in `provenance/corpus-node-aliases.json` keyed by theorem,
   project, obligation, or ResearchMap identity;
3. an existing exact `research/<project-id>/` node.

Every resolved path must remain inside `research/` and already exist. The
resolver never invents a top-level mathematical branch. Missing or ambiguous
resolution produces `MANUAL_REVIEW` / `BLOCKED_PLACEMENT` with no Git write.

## 8. Failure semantics

"The Agent did not prove it" is not a corpus failure.

A publishable `FAILURE` must have:

- a mathematical (not provider/tool/quota/protocol) failure kind;
- an attempted route and precise mechanism;
- a mathematical scope/failure domain;
- durable evidence;
- an explicit reopen/recovery predicate.

The generated record states the attempted route, scope, progress, failure point,
obstruction, what is ruled out, what is not ruled out, recovery condition, and
evidence identities. Missing qualification yields `NO_ARCHIVE`; the projector
never infers global impossibility from an unsuccessful run.

## 9. Intent and receipt

`CorpusArchiveIntent` uses a stable identity derived from project plus committed
source identity, never from a timestamp alone. It records source effect/event or
final authority identity, theorem/claim/obligation identities where applicable,
claim snapshot hash, ResearchMap/project version, semantic artifact and evidence
references, classification, canonical key/slug, authoritative-state flag, and
delivery status.

Statuses are:

`PENDING -> CLAIMED -> PROJECTING -> COMMITTED_LOCAL -> PUSHED -> COMPLETE`

Failures become `RETRYABLE_FAILURE`, `MANUAL_REVIEW`, or
`PERMANENT_FAILURE`. Placement ambiguity and unsafe validation require manual
review.

`ArchiveReceipt` records source identity, repository, base/result commits,
classification, node, created/updated/moved files, index status, validation and
push results, content hashes, and completion time. It answers both which files a
semantic effect produced and whether that effect is already published.

## 10. Crash, retry, and idempotency

The archive outbox is activated explicitly per project. For a project whose
frozen configuration enabled publishing at creation, its creation time is the
recovery lower bound, so a crash during the first outbox write cannot lose a
later effect. Legacy/default-disabled projects never activate and are not
silently backfilled.

- Crash before Git work: the same pending stable intent resumes.
- Crash with controlled uncommitted files: the intent marker and allowed-path
  set are validated, then the same projection is committed.
- Crash after local commit: the embedded intent marker and Git history recover
  the existing commit.
- Push succeeds before receipt: remote commit containment recovers the receipt.
- Duplicate semantic replay: stable source identity returns the existing intent
  and receipt.

Canonical files embed non-authoritative archive intent and canonical-key markers
for delivery reconciliation. These markers identify projection, not truth.

## 11. Git publishing rules

For each intent the projector:

1. resolves the existing canonical node;
2. verifies/fetches the configured checkout and branch;
3. creates, updates, or moves one canonical artifact;
4. runs `python tools/update-research-index.py` (or the configured argv);
5. runs the generator again and requires no second diff;
6. validates paths, links, content safety, identity uniqueness, and strict gate;
7. stages only reviewed projector/index paths;
8. commits with a mathematical-content message;
9. pushes only when `auto_push` is enabled;
10. durably writes `ArchiveReceipt`.

Runtime logs, credentials, absolute local/private paths, raw provider archives,
scratch, audit prose, and accidental runtime directories are rejected. Git
credentials come from existing Git/`gh` authentication and are never stored in
configuration.

## 12. Configuration and manual mode

Publishing is disabled by default. The existing corpus ingestion switch remains
separate. Publishing configuration supplies repository URL, checkout, branch,
optional canonical node, auto-push, and index argv.

Manual commands expose `status`, `pending`, `inspect`, `retry`, `publish`, and
`reconcile`. Historical backfill is not part of live reconciliation and must be
an explicit future operation, dry-run by default.

## 13. Prohibited behavior

The subsystem must not:

- let raw Planner/Worker/verifier/model prose write to Git;
- publish a candidate or final proof without strict closure;
- use Git state as theorem or ResearchMap authority;
- make Truth transactions depend on Git availability;
- duplicate one semantic source or canonical claim across files;
- guess corpus nodes or create top-level mathematical branches;
- turn operational/proof failure into mathematical impossibility;
- introduce campaign/round/cycle/version naming;
- merge tests, runtime, audits, scratch, proof artifacts, or research truth into
  one file manager;
- backfill historical accepted effects automatically.
