# Transactional Outbox Report

## Protocol

A LogicalJob is durable planning identity. Before every external invocation,
`create_attempt_intent` commits the immutable AttemptIntent and its PENDING
outbox record in the same SQLite transaction. The dispatcher may claim and call
the provider only after that commit.

Production call order is:

1. create/find the stable LogicalJob;
2. transactionally create AttemptIntent + PENDING outbox;
3. CAS-claim outbox and attempt lease;
4. mark attempt RUNNING and outbox DISPATCHED;
5. invoke the provider outside all database transactions;
6. durably finalize/register the result artifact;
7. ingest/fence/select the result;
8. acknowledge the outbox.

LogicalJob creation occurs as an earlier durable planning operation. Therefore
the attempt/outbox transaction never depends on an uncommitted job, and no
provider is called when either intent or outbox is absent.

## Delivery semantics

External execution is explicitly at-least-once. A crash after the remote
provider receives a request can produce another physical attempt. Transport
retries internal to one provider client remain one Attempt; exhausted provider
fallback creates a new Attempt for the same LogicalJob and records its reason.

The outbox states are PENDING, CLAIMED, DISPATCHED, ACKNOWLEDGED,
FAILED_RETRYABLE, and DEAD_LETTER. An expired CLAIMED record becomes
FAILED_RETRYABLE through a journaled reconciliation action. An unknown
DISPATCHED request is not silently treated as never sent.

## Evidence

- D3 observes intent and outbox from inside the provider callback.
- D4 faults immediately after intent/outbox commit; provider count remains zero
  and restart emits REDISPATCH.
- D5/D9 retain duplicate successes while selecting one result.
- Production RoutedLLMClient, formalization, certification, and provider-smoke
  use this dispatcher.

