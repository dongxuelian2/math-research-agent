# Long-Horizon Campaigns

## Run versus campaign

A run is an immutable evidence directory after its state reaches `COMPLETE`.
It is never reopened or overwritten. A campaign is the durable record above
runs. It links each child through `campaign_id`, `run_id`, and
`parent_run_id`.

A mathematical audit failure produces `FAILURE_MAP.json` and
`FAILURE_MAP.md`. In an opt-in campaign, the engine may create a new repair
run. The child receives only the theorem statement, previous candidate,
failure map, changed dependencies, relevant failed-route memory, verified
local lemmas if present, usage summary, and the exact inherited trust/replay
policy. Live subprocess state, forbidden historical sources, and unrelated
project files are explicitly excluded.

## Profiles

`normal` preserves conservative behavior:

- 4-hour time budget;
- 3 Workers;
- no automatic successor;
- no automatic dependency repair;
- no secondary-verification phase;
- no new hard-gate requirement beyond the established normal path.

`overnight` is explicit opt-in:

- 12-hour campaign budget;
- 4 initial Worker roles and a maximum of 6;
- up to 4 repair cycles;
- up to 3 infrastructure retries, plus provider-local bounded retries;
- automatic successors;
- provenance-driven dependency repair when a replay policy and catalog exist;
- code-level hard pre-submit gate;
- bounded secondary verification.

Time expiry never relaxes the submission gate. If no admissible candidate has
been accepted, the run records `CHECKPOINT / TIME_BUDGET_EXHAUSTED` and remains
resumable.

## Failure map and repair

Each failure-map item records the exact rejected claim, auditor, candidate
location, expected authority, blocking flag, repair suggestion, and affected
branch. Categories are:

- `MATHEMATICAL_GAP`
- `EXHAUSTIVENESS_GAP`
- `BOUNDARY_GAP`
- `CONVERSE_GAP`
- `DEPENDENCY_GAP`
- `SEMANTIC_GAP`
- `FOUNDATION_GAP`
- `SCOPE_GAP`
- `COUNTEREXAMPLE`
- `INFRASTRUCTURE_ERROR`
- `PROVIDER_ERROR`
- `UNKNOWN`

Repair does not reopen the entire theorem. A successor is bounded to the
recorded obligations. The same strategy/failure fingerprint is frozen after
two repetitions; it may reopen only after a new dependency, new lemma, or
changed failure condition.

## Hard pre-submit gate

Overnight candidates must include an `OPENPROVER_AUTHORITY_MANIFEST`. The
deterministic gate validates every declared authority through the Trust Kernel
before the core prover may write `PROOF.md`. Any unresolved `SCOPE_GAP`,
`DEPENDENCY_GAP`, `MISSING_AUTHORITY`, `ANSWER_LEAK_RISK`,
`UNRESOLVED_BRANCH`, `BLOCKED_DEPENDENCY`, or
`REQUIRED_DEPENDENCY_EXPANSION` forbids submission.

## Replay inheritance and dependency repair

The runtime replay manifest is normalized into an exact `ReplayPolicy` hash.
Every successor inherits that policy without widening it. Source
materialization is denied unless the source:

1. belongs to the approved historical dependency graph;
2. predates the target cutoff;
3. is not forbidden;
4. has an exact semantic or theorem identity;
5. passes the leak audit.

The engine never searches the whole project for a convenient answer-bearing
file. Foundation items are allowed independently and are recorded through
`foundation_ids_used`.

## Worker roles and 4→6 expansion

Supported roles are `constructive`, `adversarial`, `reconstruction`,
`alternative-proof`, `boundary`, `dependency`, and `computational-check`.
The scheduler annotates each Worker with a distinct role-specific directive.

The normal overnight capacity is four. Expansion is permitted only when the
planner supplies at least five distinct obligations or the task has at least
three explicitly independent branches. The hard ceiling is six; it is not a
quota-consumption target.

## Provider and infrastructure checkpoints

Provider quota or usage-cap exhaustion becomes `BLOCKED_PROVIDER_QUOTA`.
Transport, encoding, subprocess, malformed-output, timeout, or filesystem
failures use `execution_status=ERROR`; after bounded retries they become
`BLOCKED_INFRASTRUCTURE`. Neither state changes a theorem to mathematical
`REJECTED`.

## Graceful stop

`campaign-stop` writes a cross-process stop request. The harness completes the
current critical write, refuses to start another Worker, and records
`STOPPED_AT_CHECKPOINT`. `campaign-resume` clears the acknowledged stop file
and continues the same non-complete run.

## Secondary verification

After the primary specialist audits and final audit first pass, overnight mode
runs five bounded checks:

1. independent proof reconstruction;
2. adversarial review;
3. certificate rerun;
4. deterministic dependency coverage;
5. theorem-statement and notation-scope reconstruction.

A secondary mathematical failure creates a failure map and a repair
successor. An execution error checkpoints infrastructure. Only a full pass
lets the campaign reach `COMPLETE_PROVED_REPLAY`.

## Commands

```powershell
python -m openprover.math_research campaign-run --project <project> --target <theorem> --config <models.json> --profile overnight
python -m openprover.math_research campaign-status --project <project> --campaign <campaign-id>
python -m openprover.math_research campaign-stop --project <project> --campaign <campaign-id> --reason "operator maintenance"
python -m openprover.math_research campaign-resume --project <project> --campaign <campaign-id> --config <models.json>
```

On Windows, `scripts/run_math_agent.ps1` exposes the same commands and sets
`PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, and UTF-8 console encodings before
starting Python.
