# Math Research Backend

`backend` is the TypeScript proof core described in the repository's
`README.md` and `docs/PROOF_WORKFLOW.md`. It is intentionally independent from
the former Python proof runtime, Rust TUI, `pi`, and `dsh` checkouts.

The package currently implements this narrow path:

```text
Session -> Agent loop -> Provider -> Tool call -> Tool result -> Response -> JSONL session
```

Phase 2 now adds a typed `ProofWorkflow` without coupling it to the existing
Python runtime or Rust TUI. It is a clean-room reorganization of the
OpenProver loop described in `doc1-why-not-openprover.md`:

```text
Planner action protocol
  -> step artifacts + action dispatch
  -> parallel Workers
  -> parallel independent Verifiers
  -> merged Worker/Verifier feedback
  -> repository + WHITEBOARD.md + next Planner context
  -> hard submission gate
```

The workflow includes tagged OpenProver TOML and JSON parsing, repository
wikilinks, resumable `steps/step_NNN` artifacts, budgets, failed-route and
candidate fingerprints, and the `prove`, `prove_and_formalize`, and
`formalize_only` modes. `CommandProofFormalVerifier` is an optional adapter
whose default command is `lake env lean`; a formal proof is accepted only
after that adapter returns success.

The proof path is:

```text
Session -> Planner -> step/actions -> parallel Workers -> parallel Verifiers
         -> merged feedback -> repository + whiteboard -> submit/formal gate
```

The package also exposes `ProofApiServer` for a frontend-facing HTTP boundary:

```text
POST /v1/sessions
POST /v1/sessions/:sessionId/theorem
POST /v1/sessions/:sessionId/proof-runs
GET  /v1/sessions/:sessionId/proof-runs/:runId/result
```

The last endpoint returns `ready`, the final status, and `answer.proof` after
the asynchronous run completes. `test/proof-api.test.ts` drives this entire
sequence through `fetch`, while injecting offline mock role providers only on
the backend side.

The research API also exposes the failure-isolated long-term corpus projection
outbox:

```text
GET  /v1/research/projects/:projectId/corpus-archive
GET  /v1/research/projects/:projectId/corpus-archive/pending
GET  /v1/research/projects/:projectId/corpus-archive/intents/:intentId
POST /v1/research/projects/:projectId/corpus-archive/reconcile
POST /v1/research/projects/:projectId/corpus-archive/intents/:intentId/retry
POST /v1/research/projects/:projectId/corpus-archive/intents/:intentId/publish
```

Publishing is disabled by default. `npm run corpus -- <command> --project <id>`
provides the equivalent local status/retry/publish/reconcile surface after a
build. The normative behavior is documented in
`docs/CORPUS_ARCHIVE_PROTOCOL.md`.

```bash
npm install
npm test
```

The runtime has no third-party dependencies. Provider adapters use the native
`fetch` API and accept an injectable transport, so all tests remain offline.
