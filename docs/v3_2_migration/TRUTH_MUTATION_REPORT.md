# PHASE 3 Truth Mutation Report

## Result

`PROMOTION_COMPARE_AND_TRANSITION = PASS`

The production `AUDITING → PROVED` path is now:

```text
Final Audit PASS bound to exact ClaimSnapshot
        ↓
immutable TruthMutationIntent
        ↓
reload and reconstruct current truth
        ↓
typed snapshot comparison
        ↓
serialized root identity/status compare-and-transition
        ↓
new PROVED ClaimSnapshot
        ↓
immutable TruthMutationReceipt
```

The orchestrator no longer writes proof metadata and then directly calls
`ProjectStore.transition(PROVED)`.

## Durable artifacts

- `truth/mutations/intents/<mutation_id>.json` records theorem, from/requested
  status, exact claim/assertion/trust hashes, exact audit-artifact byte hashes,
  requester, reason, and creation time.
- `truth/mutations/receipts/<mutation_id>.json` exists only after transition and
  records previous/result status, input/result snapshot hashes, before/after
  project-record hashes, actor, and application time.
- `truth/mutations/blocked/<block_id>.json` records deterministic comparison
  status/disposition/reason. A failed transition never receives a receipt.

Schemas are strict and versioned; mutation ids are semantic/content-addressed,
not timestamp-only. Audit artifact paths are locators while their exact byte
hashes are mutation evidence.

## Race guards

- T10 mutates the root statement after audit and before comparison: hard block,
  target remains `AUDITING`, intent retained, no receipt.
- T11 changes a dependency status: revalidation block, no receipt.
- T12 changes canonical authority hash: authority-change block, no receipt.
- Trust-policy P1→P2: revalidation block unless a future explicit compatibility
  mechanism exists; none is silently inferred.
- Presentation-only theorem metadata change does not stale the snapshot and the
  transition may proceed.
- T13 exercises Planner → Workers → WorkerVerifier → Candidate → Audits → Final
  Gate → Intent → compare-and-transition → Receipt → PROVED.

## Serialization and atomicity boundary

`ProjectStore` supplies a narrow in-process reentrant truth lock and an exact
status/root-record compare-and-transition. Existing `save_project`,
`update_theorem`, and lifecycle transition writes participate in that lock.
Filesystem replacement remains atomic for each JSON file.

This is intentionally not claimed as a cross-process transaction. External or
multi-process writers that bypass the process lock remain migration debt for
the later SQLite/WAL phase. PHASE 3 did not introduce a database, outbox, lease,
heartbeat, or Attempt runtime.

## Remaining direct truth writes bypassing the facade

- `cli.py` retains explicit human lifecycle transition compatibility.
- `showcase_demo.py` retains isolated demonstration-fixture transitions.
- `ProjectStore.add_theorem/update_theorem/transition` remain public legacy
  primitives for compatibility and tests.

New orchestrator promotion, lifecycle, campaign/resume, audit, and
formalization truth-sensitive paths enter through `TruthStoreFacade` first.
The facade itself delegates storage/status mechanics to ProjectStore by design.

## Remaining direct reads

CLI/status, benchmark, certification, retrieval projection, trust resolution,
and compatibility setup still read ProjectStore directly. These reads are not
allowed to self-authorize promotion. Orchestrator has a few status/display
reads, while its assertion/dependency/authority capture and all promotion
validation are facade-owned.

