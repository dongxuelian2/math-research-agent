# Attempt Lease, Heartbeat, and Fencing Report

## Ownership

Attempt leases contain owner, opaque token, acquisition/expiry/heartbeat time,
monotonic generation, and row version. Acquisition is a conditional SQLite
update from READY (or an explicitly reconciled ORPHANED state). Two contenders
cannot both own the same generation.

Heartbeat accepts only a matching token/generation on a live LEASED or RUNNING
attempt. Expired or stale generations cannot revive ownership. Lease expiry
moves execution to ORPHANED; it does not change Truth, a ResearchObligation,
ResearchMap, or governance outcome.

## Late results

A late executor may persist its artifact. Result ingestion compares the token
and generation against current ownership. A mismatch produces a retained
`STALE_FENCED` AttemptResult with `authoritative=0`; it cannot become the job
winner, apply an EffectSlot, renew a lease, or overwrite a later result.

## Cancellation race

Cancellation is a durable `CANCEL_REQUESTED` transition. The routed client also
terminates its process tree. If a result commits first or while cancellation is
pending, completion wins and the one authoritative state is COMPLETED. If
termination wins, the state is CANCELLED and the outbox is dead-lettered. No
row can be simultaneously COMPLETED and CANCELLED.

## Evidence

- D10 two-owner lease CAS: one winner.
- D11 valid heartbeat extends expiry; stale token is rejected.
- D12 expiry creates ORPHANED without research failure.
- D13 generation-1 late result retained but fenced after generation 2.
- D14 cancel/complete race has one allowed terminal state.
