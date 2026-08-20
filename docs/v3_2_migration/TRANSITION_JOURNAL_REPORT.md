# Transition Journal Report

## Boundary

`transition_journal` is append-only execution evidence. Current tables remain
the runtime authority; startup never requires journal replay. TruthMutation
receipts, ResearchMap revisions, and ArchitectureReview artifacts remain their
own domain evidence and are not replaced by generic journal rows.

Each entry records object type/id, before/after state, transition kind, actor,
Attempt/LogicalJob links, timestamp, causal reference, and a canonical metadata
hash.

## Attempt state machine

The typed states are:

`CREATED → READY → LEASED → RUNNING → RESULT_RECORDED → COMPLETED`

with explicit branches for `FAILED_RETRYABLE`, `FAILED_TERMINAL`,
`CANCEL_REQUESTED`, `CANCELLED`, `ORPHANED`, and
`BLOCKED_MISSING_ARTIFACT`. Recovery can explicitly re-lease an orphan with a
new fencing generation; the reconciler never blindly chooses that transition.

Illegal transitions fail closed. The rejected request is itself journaled as
`REJECTED_ILLEGAL_TRANSITION` without changing current state.

## Other journaled objects

- LogicalJob creation, activation, accepted-result selection;
- transactional outbox enqueue, claim, dispatch, retry, acknowledgement;
- lease acquisition and heartbeat renewal;
- artifact verification/registration and result ingestion;
- EffectSlot prepare, domain-applied, and acknowledgement.

The journal records semantic/control transitions, not streamed tokens or every
provider log event, avoiding write amplification.

## Evidence

D2, D3, D8, D10–D14, and D19 assert current rows and causal journal evidence.
The full local suite passes with no journal-replay dependency.

