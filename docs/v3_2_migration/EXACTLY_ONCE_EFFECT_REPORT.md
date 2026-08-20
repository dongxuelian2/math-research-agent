# Exactly-Once Semantic Effect Report

## Acceptance and EffectSlot

Every physical AttemptResult is retained with provider and artifact provenance.
Ingestion is keyed and idempotent. A serialized LogicalJob acceptance CAS uses
`FIRST_VALID_ACCEPTED_RESULT`; later successful results remain billing and
provenance evidence but cannot replace the winner.

An EffectSlot has a database uniqueness constraint over LogicalJob, effect
kind, semantic target type, and semantic target ID. Only the accepted,
authoritative result can prepare it.

## Cross-store saga

SQLite and domain files do not share ACID. The explicit protocol is:

1. validate the accepted source artifact;
2. insert `PREPARED` EffectSlot;
3. outside the SQLite transaction, let TruthStoreFacade,
   ResearchStoreFacade, or GovernanceController apply its own rules;
4. persist `DOMAIN_APPLIED` with domain identity/provenance;
5. persist `ACKNOWLEDGED` and complete the LogicalJob.

On restart, a domain-specific recovery function inspects immutable receipts,
map/disposition identity, review clock identity, or patch application identity.
It completes the same slot; it does not invent a second domain mutation.
Nothing is automatically deleted.

## Domain preservation

- Research: SessionClosure replay recognizes its existing RESOLVED disposition
  and returns the same ResearchMap version.
- Truth: the accepted runtime effect invokes the snapshot-bound TruthMutation
  intent/CAS/receipt path. Prepared evidence is durable before theorem CAS; if
  the process dies between the theorem transition and receipt, replay validates
  the exact status-history entry, identity, policy, and metadata, reconstructs
  the receipt, and performs no second transition.
- Governance: review replay returns the same reset clock; authorized patch
  replay recognizes its application or exact child-map provenance.
- Runtime never writes theorem status or research disposition via SQL and never
  authorizes an ArchitecturePatch from Attempt success.

## Evidence

D8/D9 prove idempotent ingestion and one winner. D15–D18 execute real
SessionClosure, TruthMutation, and ArchitectureReview paths twice. D19 faults
after domain apply but before runtime acknowledgement and recovers one effect.
D25 proves no-crash and recovered final-state equivalence. A dedicated Truth
fault point proves the internal theorem-transition/receipt-write window.
