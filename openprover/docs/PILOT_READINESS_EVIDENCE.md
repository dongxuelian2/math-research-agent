# Pilot Readiness Evidence (2026-08-13)

This validation repairs the final integration and trust gaps without running
the C2/G project or any long mathematical campaign.

## Production query handoff

`LITERATURE_REQUEST` now reaches the production `LiteratureTaskExecutor`, whose
Lead emits structured `search_tasks`.  The scheduler validates each minimized
`public_query`, applies the campaign/operator public-search gate, and records
the approval source, timestamp, query hash, obligation id, and literature
request id in the Searcher payload.  Private-context transmission remains
disabled independently of per-task public-query approval.

## Artifact-bound external authority

Full-text extraction now emits a content-hashed theorem extraction artifact.
The Registry independently reopens the source, text, and extraction artifacts;
recomputes their hashes; validates the selected span; rereads and normalizes
that span; recomputes the theorem statement hash; and compares the authority
statement, theorem label, and location to the extraction record.  An LLM's
`exact_statement_match` assertion is no longer a promotion condition.

Source authenticity now stops at `VERIFIED_SOURCE_THEOREM`.  Applicability is
stored separately by theorem, obligation, normalized target, and authorized
assumption snapshot.  Its durable reconstruction contains per-hypothesis
mapping, notation, conclusion bridge, direction, normalization, exception,
and authorized-local-lemma evidence.  A distinct routed verifier call is
mandatory, and the Registry rehashes the reconstruction before promoting only
`APPLICABLE` to `APPLICABLE_EXTERNAL_AUTHORITY`.  Context booleans cannot
bypass this path; changed assumption snapshots require revalidation; and
DUAL_TRACK proof is not cancelled at source-verification time.

## Production Literature smoke

The public query `Pythagorean theorem` traversed:

```text
CampaignEngine
→ ResearchOrchestrator
→ AsyncDAGScheduler
→ AsynchronousPipelineRuntime
→ LiteratureTaskExecutor
→ Lead → Searcher → Reader → Synthesizer → Authority Auditor
→ ExternalAuthorityRegistry
→ Reconstruction → theorem verifier
```

The smoke used OpenAlex metadata and the public Europe PMC PDF for Kadison's
paper, selected `PROPOSITION 1`, and closed the synthetic obligation.  No
smoke-local handler replaced a production Literature or verification handler.

## Cancellation and budget

Phase A now passes only when Task A is `INTERRUPTED`, process exit follows the
interrupt dispatch, retry/fallback counts are zero, and concurrent Task B
completes.  `COMPLETED_BEFORE_CANCEL` is `INCONCLUSIVE`.

`AtomicResourceBudget` now implements atomic reserve, actual-usage reconcile,
unused-reservation release, additional commit after underestimation, and a
global hard stop when a completed call exceeds the cap.  Interrupted calls
without usage use the conservative `reserved_as_committed` policy and are
recorded as `USAGE_UNKNOWN_AFTER_INTERRUPT`.

## Evidence-driven audit

The final archive contains `READINESS_INPUTS.json`, phase A–G raw evidence,
focused and full JUnit XML, `FINAL_READINESS.json`, and
`VALIDATION_MANIFEST.json`.  The audit reads only the input whitelist, verifies
every listed SHA-256, recomputes phase conditions, parses actual JUnit XML, and
fails closed on missing, malformed, inconsistent, or hash-mismatched evidence.

Final machine-readable regression: 189 applicable tests, zero failures and
zero errors.  The known Windows-only `os.killpg` and `termios` collection
blockers remain explicitly excluded.
