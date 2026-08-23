# F-002 — Lease Fencing Repair Report

## Finding and cause

`record_result` accepted a result when the lease token and generation still
matched even though `lease_expires_at` was already in the past. That made a
late result authoritative and eligible for a winner/effect.

## Repair

Authoritative ingestion now requires the current token, generation, compatible
attempt state, and a lease that is not expired at the ingestion boundary. If
expiry cannot prove that provider completion occurred before expiry, the result
fails closed as `STALE_FENCED`. Its artifact and result provenance remain
durable. The reconciler may review or retry it, but it cannot become the
LogicalJob winner or prepare an EffectSlot.

This deliberately does not guess between “provider completed before expiry but
the process recorded late” and “provider completed after expiry”; the local
runtime has no trustworthy completion timestamp for that distinction.

## Proof

- `test_f002_expired_result_is_retained_but_cannot_be_accepted` verifies the
  artifact, `authoritative=0`, `STALE_FENCED`, no accepted result, and no
  effect slot.
- The repaired X4 probe reports
  `ingestion_state=STALE_FENCED; authoritative=False`.
- Existing non-expired dispatch, duplicate ingestion, generation fencing, and
  cancel/complete race tests remain green.

## Status

`F_002_EXPIRED_LEASE_FENCING = CLOSED`

The policy is fail-closed at the semantic authority boundary while retaining
high-value provenance for reconciliation.
