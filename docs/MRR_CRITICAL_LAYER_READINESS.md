# Critical Layer external Proof-as-Test readiness

The production Agent contains no Critical Layer answer, expected file list, or supervisor-specific prompt. An external supervisor can supply an arbitrary corpus directory and unresolved objective entirely through HTTP.

## 1. Configure production roles

Edit `configs/math-agent.toml` before launch:

- map `research_director`, `corpus_bootstrapper`, `planner`, `worker`, `verifier`, `secondary_auditor`, and `synthesizer` to enabled real model profiles;
- enable `literature` and `literature_researcher` only if the campaign may use network literature;
- set serious `[research].max_cycles` and `[budgets]` values;
- keep `[tools].execution_boundary = "CONTROLLED_COMMAND_RUNNER"` and limit capabilities/executables to what the campaign needs;
- enable `formalization`/`formalizer` only when the exact root is a Lean declaration and the configured Lean project is available.

Credentials stay in the environment referenced by model profiles. Do not put credentials or mathematical answers in the corpus-control request.

## 2. Launch

```powershell
pnpm install --frozen-lockfile
pnpm run build
pnpm start:api
```

The repository config currently uses port `43100`; confirm the active value with startup output. Then:

```powershell
$base = "http://127.0.0.1:43100"
$project = "critical-layer-external"
$corpus = "C:\absolute\path\to\critical-layer-corpus"
$objective = "<exact unresolved mathematical objective supplied by the external supervisor>"

Invoke-RestMethod -Method Post -Uri "$base/v1/research/projects" -ContentType application/json -Body (@{projectId=$project; name=$project} | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri "$base/v1/research/projects/$project/root" -ContentType application/json -Body (@{objective=$objective} | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri "$base/v1/research/projects/$project/corpus" -ContentType application/json -Body (@{roots=@($corpus)} | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri "$base/v1/research/projects/$project/corpus/ingest" -ContentType application/json -Body '{}'
Invoke-RestMethod -Method Post -Uri "$base/v1/research/projects/$project/bootstrap" -ContentType application/json -Body '{}'
Invoke-RestMethod -Method Post -Uri "$base/v1/research/projects/$project/start" -ContentType application/json -Body '{"maxCycles":100}'
```

No source edit or manual per-round coordination is required.

## 3. Observe without steering the Agent

```powershell
Invoke-RestMethod "$base/v1/research/projects/$project/bootstrap-report"
Invoke-RestMethod "$base/v1/research/projects/$project/frontier"
Invoke-RestMethod "$base/v1/research/projects/$project/claims"
Invoke-RestMethod "$base/v1/research/projects/$project/dependencies"
Invoke-RestMethod "$base/v1/research/projects/$project/coverage"
Invoke-RestMethod "$base/v1/research/projects/$project/routes"
Invoke-RestMethod "$base/v1/research/projects/$project/artifacts"
Invoke-RestMethod "$base/v1/research/projects/$project/checkpoints"
Invoke-RestMethod "$base/v1/research/projects/$project/events"
Invoke-RestMethod "$base/v1/research/projects/$project/audit"
Invoke-RestMethod "$base/v1/research/projects/$project/root-readiness"
Invoke-RestMethod "$base/v1/research/projects/$project/result"
```

For an exact artifact body, use `GET .../artifacts/:artifactId`; for provenance without the body, use `GET .../artifacts/:artifactId/metadata`.

The external supervisor should evaluate durable state, not model narration:

- bootstrap categories/dependencies/routes/frontier;
- `MODEL_DIRECTED` versus `FALLBACK_DIRECTED` decisions;
- immutable promoted proof bodies;
- Worker and Verifier `toolEvidenceReceipts`;
- typed route failures and reopen predicates;
- concrete strategic changes after stalls;
- task execution ledger and budgets;
- checkpoints and exact resume;
- root readiness, synthesis, and fresh audit artifacts.
- the global invariant result, current authority receipts, exact execution plans, and the persisted effective-config revision exposed by the read-only audit route.

## 4. Interruption and exact resume

Stop the API process at an arbitrary point, restart it with the same data directory, inspect the project, then resume:

```powershell
Invoke-RestMethod -Method Post -Uri "$base/v1/research/projects/$project/resume" -ContentType application/json -Body '{"maxCycles":100}'
```

Completed Planner/Worker/Verifier tasks and accepted mathematical effects must retain their stable IDs. Completed Workers must not be rerun merely because another Worker was interrupted.

## 5. Completion and audit

When the frontier is empty, inspect readiness and trigger closure if the Director has not already done so:

```powershell
Invoke-RestMethod "$base/v1/research/projects/$project/root-readiness"
Invoke-RestMethod -Method Post -Uri "$base/v1/research/projects/$project/synthesis" -ContentType application/json -Body '{}'
Invoke-RestMethod "$base/v1/research/projects/$project/result"
```

A final-audit rejection is a valid research outcome: the project must remain non-`PROVED`, gain an audit blocker, and reopen the frontier. Only a fresh accepted audit persists the final proof.

## 6. Operational caveats

- Use exactly one API server writer for a data directory.
- Run under an OS/container sandbox if model-generated computation is untrusted; the built-in boundary is controlled execution, not a security sandbox.
- Re-ingest only after intentional corpus changes. Hash changes trigger authority invalidation.
- Provider/quota/tool/literature/formal/cancellation failures are operational states and must not be interpreted as mathematical route failure.
- A real-provider campaign can consume material time/cost. Set budgets before launch.
- Deterministic `MockProvider` acceptance proves the production protocol, invariant gates, persistence, and restart wiring. It does not establish autonomous mathematical intelligence; the external Critical Layer campaign remains Proof-as-Test.
- A schema-v1 project whose original effective configuration cannot be recovered remains inspectable with its migration report, but a configured production server refuses to mix current live TOML into it. Create a new project or perform an explicit configuration migration before model execution.
