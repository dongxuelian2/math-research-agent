# OpenProver Research Harness v2 — Implementation Report

Report date: 2026-08-09 (Asia/Shanghai)

Repository: `E:\tool\math\openprover`

Upgrade branch: `codex/research-harness-v2`

Pre-change HEAD: `a005e216fd5f79e98305a63a55abfae8b14ce779`

## 1. Pre-change architecture

The viable mathematical core was already split between upstream OpenProver
(Planner, Worker, Worker Verifier, Whiteboard/repository, and `PROOF.md`) and the
local `math_research` wrapper (project records, dependency context, four
specialist auditors, final gate, and Archivist). The wrapper had immutable
completed runs but no campaign abstraction. Dependencies were a flat Project
Theorem/Premise slice; audit exceptions became mathematical FAIL; the replay
manifest was documentary rather than executable; and submission eligibility
depended on Planner behavior plus a free-text scope marker. The complete
pre-change findings are in `RESEARCH_HARNESS_V2_ARCHITECTURE_AUDIT.md`.

## 2. Changed architecture

The Planner/Worker reasoning core remains recognizable. Research Harness v2
wraps it with five explicit services:

1. a three-layer Trust Kernel and claim-class resolver;
2. a normalized audit protocol with orthogonal domain/execution status;
3. a code-level pre-submit gate and executable replay policy;
4. an immutable-run, resumable-campaign lifecycle with bounded successors;
5. an opt-in role scheduler, checkpoint controller, and secondary verifier.

Normal `run`, `status`, `context`, resume, steering, failed-route, and audit
commands retain their previous routing. Campaign behavior is opt-in.

## 3. Trust Kernel design

Every externally used claim is classified as `FOUNDATIONAL_THEOREM`,
`SEMANTIC_DEFINITION`, `PROJECT_THEOREM`, `LOCAL_PROOF`, or
`COMPUTATIONAL_CERTIFICATE`. The deterministic resolver produces separate
Foundations, Semantics, Project Theorems, Local Proofs, and Computational
Certificates sections and records missing authorities and validation errors.
Package metadata, filenames, index summaries, and generated manifest comments
are explicitly rejected as proof authority.

## 4. Foundation Registry content

`foundations.v1.json` is project-independent and limited to the GA1-1
certification prerequisites:

- `FOUND-NT-JAC-01`: Jacobi numerator multiplicativity.
- `FOUND-NT-JAC-02`: supplementary law for `(-1/n)`.
- `FOUND-NT-JAC-03`: supplementary law for `(2/n)`.
- `FOUND-NT-QR-01`: quadratic reciprocity for odd primes.
- `FOUND-NT-QR-02`: `(5/n)=(n/5)` for positive odd `n` coprime to 5.

Each item has an exact statement, conditions, provenance, proof policy, and
versioned content hash. Registry hash:
`sha256:1c9482a24447eb593e26e52bbe2bc90881f0cfa86c306bb34de380f34d344947`.
Project-specific markers and replay/solution content are rejected.

## 5. Semantic Registry content

`ga1_certification_semantics.v1.json` contains `SEM-G-PRIM-01`, restricted to
notation scope `G-positive-remainder-content-decomposition/GP3-v1`. It binds
the primitive unit/core equivalence with the `h=1` layer to the real GP3 source
body at `critical_G_primitive_remainder_campaign.md`, whose SHA-256 is
`7918fd0a766658b1f428637ed2e57a470a3b332aba97086174caecf2eddcd732`.
Registry hash:
`sha256:90c8c3e126fec0d0daa32faa5e584a586a286064fe902c36589993d89ea89968`.
A source-hash or notation-scope mismatch is a hard failure.

## 6. Dependency Auditor changes

Dependency Auditor v2 inventories claims by class and requires exact authority
IDs for every external use. Foundations resolve only through the Foundation
Registry, semantics only through a source-hashed notation scope, and project
theorems only through a `PROVED` theorem DAG node. A complete candidate-local
lemma records a proof location and does not need registry authority. The gate
uses the deterministic dependency report in addition to model audit output.

## 7. Audit verdict schema

Every new audit stores `domain_verdict = PASS | FAIL | INCONCLUSIVE` separately
from `execution_status = OK | ERROR`. Cross-domain concerns go to
`cross_audit_notes`. Counterexample Hunter can therefore report PASS while
noting an unverified dependency, leaving the dependency decision to Dependency
Auditor. Encoding, subprocess, timeout, malformed JSON, provider, and
filesystem errors normalize to `ERROR/INCONCLUSIVE`, never mathematical FAIL.
The gate distinguishes `PASS`, `MATHEMATICAL_FAIL`, `INCONCLUSIVE`, and
`INFRASTRUCTURE_ERROR` while retaining compatibility fields for old readers.

## 8. Lifecycle and campaign state machine

A campaign durably records `campaign_id`, `run_id`, `parent_run_id`, repair
cycle, registry/replay-policy versions, usage, and status. Completed run bytes
remain immutable. A failed audit generates `FAILURE_MAP.json` and
`FAILURE_MAP.md`; when policy allows, a new successor inherits only the fixed
candidate, audits, failure map, failed routes, verified local lemmas, usage,
registry versions, and the inherited replay policy. Repair context is scoped to
the failed obligations. Duplicate successors are rejected. Terminal or
resumable campaign outcomes include `COMPLETE_PROVED_REPLAY`,
`MATHEMATICAL_EXHAUSTION`, `BLOCKED_PROVIDER_QUOTA`,
`BLOCKED_INFRASTRUCTURE`, `TIME_BUDGET_EXHAUSTED`,
`STOPPED_AT_CHECKPOINT`, and `HUMAN_REQUIRED`.

## 9. Overnight scheduler

The explicit `overnight` profile uses a 43,200-second budget, four initial
workers, a six-worker ceiling, four repair cycles, three infrastructure
retries, bounded provider retries, automatic successors, eligible automatic
dependency repair, the hard blocker, and secondary verification. Capacity
expands from four to six only for at least five distinct obligations or at
least three explicitly independent branches. The `normal` profile remains a
conservative four-hour, three-worker, no-auto-successor mode.

## 10. Worker-role design

The role scheduler supports `constructive`, `adversarial`, `reconstruction`,
`alternative-proof`, `boundary`, `dependency`, and `computational-check`.
Planner tasks receive distinct role directives and archived assignment records;
the scheduler infers roles from obligation/branch content instead of cloning a
single Worker prompt.

## 11. Quota, time, retry, and graceful-stop behavior

Time exhaustion is decoupled from proof submission. If the hard pre-submit
gate is not satisfied, exhaustion creates a resumable
`TIME_BUDGET_EXHAUSTED` checkpoint and cannot write a candidate. Transient
infrastructure/provider failures receive only the configured bounded retries.
Actual provider usage exhaustion becomes `BLOCKED_PROVIDER_QUOTA`; exhausted
infrastructure retries become `BLOCKED_INFRASTRUCTURE`. Neither rejects the
theorem. A cross-process stop request finishes the current critical write,
starts no new Worker, records `STOPPED_AT_CHECKPOINT`, and can be resumed.

## 12. Strategy deduplication

A SHA-256 fingerprint binds theorem, branch, target lemma, method, key
dependency, and failure point. The same strategy failing twice for the same
reason is frozen. It can reopen only when a new dependency, new lemma, or
changed failure condition is explicitly recorded.

## 13. Test results

- Phase 1 focused/full regression: `59 passed`.
- Phase 2 accumulated regression: `84 passed`.
- Phase 3 accumulated regression: `96 passed` before the final replay-policy
  edge-case test.
- Final Research Harness suite after the real-smoke schema hardening:
  `97 passed in 4.01s`.
- Preserved pre-existing encoding/scope suite: `4 passed in 0.10s`.
- Final applicable total: `101/101` passed.

The tests include both required mock campaign flows: dependency FAIL -> failure
map -> successor -> repaired candidate -> gate PASS, and scope blocker -> time
exhaustion -> checkpoint -> no submission. The all-repository default test
command still cannot collect two unrelated platform tests on this Windows host:
`test_tui_keys.py` imports Unix-only `termios`, and
`test_interrupt_race.py` fails to start its collection-time subprocess with
WinError 1920. No Harness assertion failed in that attempt.

## 14. GA1-1 certification result

The new `certification.py` runner is benchmark-specific but uses only generic
Trust Kernel interfaces. It is planner-free, requires exactly two bounded
Worker Verifiers, validates all candidate/source hashes, reconstructs
dependencies, runs four specialist audits plus the final proof audit, and runs
one secondary reconstruction only after the primary hard gate passes.

Deterministic `--prepare-only` validation passed with candidate source SHA-256
`50ee421c8dfaf0b9c3a641e56e6af5792bfcd780ca72b7a8f8bd8513f3a14169`
and authority-normalized candidate SHA-256
`34181462996d393d88a00cfb939f368ed4d6237a6539311f69831ff26f389b26`.
It resolved both project theorems (`CD6`, `GA1-3`), `SEM-G-PRIM-01`, and all
five Foundation IDs with no missing authority.

The full offline mock certification passed end to end: status `PASS`,
`proved_replay=true`, two Worker Verifiers, four specialist audits, final proof
audit, and secondary reconstruction all returned `PASS/OK`; `planner_calls=0`,
`proof_search_performed=false`, and no proof obligations remained. Results are
under
`C:\Users\29848\Documents\ChatGPT\math\certification_workspaces\GA1-1-harness-v2-mock-final`.

After explicit authorization, one short real-model certification was executed
with the configured `codex_cli / gpt-5.6-sol / high` roles. It finished normally
in 266.76 seconds and made seven model calls: two Worker Verifiers, four
specialist auditors, and one Final Proof Auditor. It made zero Planner calls,
performed no proof search, did not create a successor, and did not run an
overnight campaign. CLI-reported usage was 217,957 input tokens, 16,462 output
tokens, 11,341 reasoning tokens, 33,536 cached tokens, and 234,419 total tokens.

The recorded result is `BLOCKED_INFRASTRUCTURE`, not a mathematical rejection.
All four specialist auditors and the Final Proof Auditor returned `PASS/OK`;
dependency reconstruction and replay leak audit passed. Both Worker Verifiers
returned mathematical `domain_verdict=PASS` with zero failure reasons, but used
invalid execution-status synonyms (`CERTIFIED` and `COMPLETED`) instead of the
required `OK|ERROR` enum. The strict normalizer correctly converted both to
`ERROR/INCONCLUSIVE`, so the primary gate did not pass and secondary
reconstruction did not run. No real-model PASS is claimed and there is no new
mathematical proof obligation; the exact infrastructure obligations are the two
invalid enum fields. Results are under
`C:\Users\29848\Documents\ChatGPT\math\certification_workspaces\GA1-1-harness-v2-real`.

The certification-specific prompt contract was then hardened to state the exact
allowed enums and explicitly forbid `CERTIFIED/COMPLETED`; the normalizer was
not relaxed. Offline regression covers that contract. No second external-model
run was performed under the first one-smoke authorization.

After a separate explicit authorization, one second and final real-model smoke
was run against commit `9ab63f5` with the candidate, context, provider, model,
and certification scope unchanged. The strict output-contract repair worked:
both Worker Verifiers returned `domain_verdict=PASS` and
`execution_status=OK`, with zero schema or execution errors. Counterexample,
Exhaustiveness, and Boundary also returned `PASS/OK`; replay leak and
deterministic dependency reconstruction passed.

The second run nevertheless ended `MATHEMATICAL_FAIL`. Dependency Auditor and
Final Proof Auditor found that the fixed candidate explicitly invokes Euler's
criterion and Gauss's lemma as inferential dependencies, while its authority
manifest supplies neither registered Foundation IDs nor local proofs for those
two claims. Existing `FOUND-NT-JAC-02` and `FOUND-NT-JAC-03` directly authorize
the required Jacobi identities, but rewriting the submitted derivations to use
those authorities would be a candidate repair and is outside fixed-candidate
certification. The primary gate therefore failed and secondary reconstruction
correctly did not run. The run made zero Planner calls, performed no proof
search, and used seven model calls totaling 231,545 CLI-reported tokens
(218,674 input, 12,871 output, 8,003 reasoning, and 48,640 cached) in 208.297
seconds. No third external call was made. Results are under
`C:\Users\29848\Documents\ChatGPT\math\certification_workspaces\GA1-1-harness-v2-real-2`.

## 15. Replay answer-leak result

Deterministic replay isolation audit passed. The inherited repair manifest is
bound by SHA-256
`sha256:e6a040a8830fc288085e9c6294c3e593b9f8e8bbef8ccf1209641964000b87cc`
and effective policy hash
`sha256:fe801acf41ddafc7e700f467ffb4ec026c34f979300744d765d39ffefc0a7115`.
The GP3 semantic body is an explicit, hash-pinned extension checked against the
inherited deny list; it does not mutate or widen the inherited allow policy.
No forbidden source was materialized. Prior repair gates are labeled diagnostic
state and never mathematical authority. Foundation usage is recorded by exact
`foundation_ids_used`.

## 16. Main-project and historical-run invariants

Final read-only tree digests exactly match the pre-change baselines:

| Protected tree | Files | Final aggregate SHA-256 |
|---|---:|---|
| first GA1-1 replay run | 1660 | `C093BD522B4B8BBCE577CF50E157524AE7DCFEB0FE246E2FA0594EBA4B79010B` |
| GA1-1 repair workspace | 977 | `3B62C201036C791BF94BF41CF02B06231BC644E222C2BAF1D79EA759CA525162` |
| `E:\tool\math\projects\main` | 262 | `100F03C435C22373F88A8DD24406E37D860BAA0ED7CBE661DD7DAFBEE4F16940` |

No `GA1-1.json`, registry update, frontier change, migration preview, or formal
status was written to the main project. No historical run was modified.

## 17. Known limitations

- The second authorized real external-provider smoke verified the repaired
  Worker output contract, then produced a genuine `MATHEMATICAL_FAIL` for two
  explicit unregistered dependencies in the fixed candidate: Euler's criterion
  and Gauss's lemma. This is not `COMPLETE_PROVED_REPLAY`. Resolving it requires
  an explicit candidate/authority repair decision outside certification; the
  harness did not rewrite the candidate, expand the Foundation Registry, open a
  repair cycle, or make a third external call.
- Automatic dependency repair is deliberately narrow: it needs a
  provenance-driven catalog, chronological eligibility, inherited manifest
  approval, exact identity, and a passing leak audit. It does not search the
  project for convenient proofs.
- The Foundation Registry is intentionally small and must be version-reviewed
  before adding new classical facts.
- The outer launcher is outside the Git repository. Its previous version was
  backed up at
  `E:\tool\math\backups\run_math_agent.ps1.pre-harness-v2.a060688c6fa9.bak`;
  the canonical tracked copy is `scripts/run_math_agent.ps1`.
- The two unrelated all-repository Windows collection limitations described in
  section 13 remain outside this scoped upgrade.

## 18. Git diff summary

All v2 changes are isolated on `codex/research-harness-v2`. Relative to
`a005e216fd5f79e98305a63a55abfae8b14ce779`, the diff adds the audit report,
three protocol/campaign documents, registries, Trust Kernel and audit protocol,
campaign engine/CLI, scheduler, certification runner/spec, Windows launcher,
and focused tests; it makes bounded integration changes to the orchestrator,
retrieval/context, state gate, providers, package entry points, project store,
and README. The pre-existing dirty files remain outside the phase commits.

## 19. Commits and rollback

The three required architectural phases are independently reviewable:

1. `36ae70a` — `feat(math-research): add three-layer trust kernel`
2. `447125d` — `feat(math-research): add resumable campaign lifecycle`
3. `bfeb1bd` — `feat(math-research): add overnight scheduler and verification`
4. `3b73c63` — `feat(math-research): add planner-free replay certification`
5. `fix(math-research): pin certification result enums` — records the
   authorized real-smoke result and its bounded schema-contract repair.

The certification/report layer and the real-smoke follow-up are isolated from
the three architectural phases. Rollback can therefore proceed from the newest
commit backward without rewriting or reopening any protected run.

Pre-existing user changes intentionally remain uncommitted:
`openprover/llm/_base.py`, `openprover/math_research/cli.py`,
`openprover/prover.py`, `configs/models.a1_replay.json`,
`configs/models.a1_repair_replay.json`, and
`tests/test_encoding_and_scope_gate.py`.

## 20. Exact operating commands

Run these from `E:\tool\math\openprover`; replace angle-bracket placeholders.

```powershell
# Normal run (existing conservative path)
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research run --project <project-path> --target <theorem-id> --config <models-json> --workers 3

# Explicit overnight campaign (12h; starts at 4 roles, may scale to 6)
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research campaign-run --project <project-path> --target <theorem-id> --config <models-json> --profile overnight

# Campaign status
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research campaign-status --project <project-path> --campaign <campaign-id>

# Graceful stop at a checkpoint
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research campaign-stop --project <project-path> --campaign <campaign-id> --reason "operator maintenance"

# Resume a checkpointed campaign
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research campaign-resume --project <project-path> --campaign <campaign-id> --config <models-json>
```

The deployed `E:\tool\math\run_math_agent.ps1` exposes equivalent
`run`, `campaign-run`, `campaign-status`, `campaign-stop`, and
`campaign-resume` commands and automatically sets `PYTHONUTF8=1`,
`PYTHONIOENCODING=utf-8`, and UTF-8 console encodings.
