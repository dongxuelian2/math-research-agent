# ResearchObligation Report

## Result

`RESEARCH_OBLIGATION = PASS`

`ResearchObligation` now owns a durable long-horizon research question. It is
not a WorkerTask, pipeline task, CandidateAttempt, provider call, run, or
campaign successor.

## Semantic and disposition separation

The immutable semantic revision contains the title, statement, kind, scope,
dependencies, root ClaimSnapshot binding, creation map version, previous
revision hash, and obligation hash. A separate immutable
`ObligationDisposition` records the research judgment and its provenance.

Allowed research dispositions are:

```text
OPEN
BLOCKED
RESOLVED
SUPERSEDED
ABANDONED_WITH_REASON
```

Execution states such as `RUNNING`, `QUEUED`, `RETRY_PENDING`,
`PROVIDER_ERROR`, `WAITING_FOR_WORKER`, and pipeline `CLOSED` are rejected as
research dispositions. `RESOLVED` means only that the current Research Plane
does not retain the obligation as open frontier; it does not mean the root
theorem is `PROVED`.

## Provenance rules

- `BLOCKED` requires blocker references.
- `RESOLVED` requires evidence references and an explicit resolution basis.
- `SUPERSEDED` requires replacement obligation ids.
- `ABANDONED_WITH_REASON` requires a reason.
- Every new disposition links the previous disposition hash.
- Every semantic rebase links the prior obligation revision hash.

Current obligation JSON files are projections. All semantic revisions and
disposition records remain immutable and independently loadable.

## Lifecycle evidence

- Deleting/archiving a WorkerTask does not affect an obligation.
- A TacticalSession crash leaves its obligation `OPEN` or explicitly
  `BLOCKED`; execution failure is not mathematical failure.
- A successor run carries the same ResearchMap/obligation binding and is marked
  `EXECUTION_LINEAGE_ONLY`.
- O2/O3 survive a session that handles only O1.

Tests R3–R5, R8–R9, R19–R20 certify these properties. The full local-safe suite
passes with the one separately documented Windows interrupt collection block.
