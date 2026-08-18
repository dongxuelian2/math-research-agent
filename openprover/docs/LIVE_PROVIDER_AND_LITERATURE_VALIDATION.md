# Live Provider and Literature Validation (2026-08-13)

This report is the bounded A→G validation for branch
`codex/research-harness-v2`.  It deliberately did not start the main theorem,
the C2/G proof path, or a long campaign.

## Results

| Phase | Result | Evidence |
|---|---|---|
| A Sol task-scoped cancellation | `PASS` | `E:\tool\math\logs\live-validation\phase-a-3\phase-A.json` |
| B Luna Max account smoke | `PASS` | `E:\tool\math\logs\live-validation\phase-b\phase-B.json` |
| C heterogeneous production mini-smoke | `PASS` | `E:\tool\math\logs\live-validation\phase-c-2\phase-C.json` |
| D scholarly metadata/search | `PASS` | `E:\tool\math\validation-evidence\applicability-repair-20260813\phase-D-E-F-production-5\phase-D\result.json` |
| E public full-text/PDF retrieval | `PASS` | `E:\tool\math\validation-evidence\applicability-repair-20260813\phase-D-E-F-production-5\phase-E\result.json` |
| F applicability end-to-end smoke | `PASS` | `E:\tool\math\validation-evidence\applicability-repair-20260813\phase-D-E-F-production-5\phase-F\result.json` |
| G readiness audit | `PASS` | this document plus the applicable regression run |

## A — Sol cancellation isolation

Two real `codex_cli` Sol calls ran concurrently through the asynchronous
pipeline.  Task A was cancelled while in a synthetic 30-second wait; task B
returned `B_DONE` normally.  A's stable dispatch id was
`pipeline-call-task-00000001-attempt-46c848088c20`, its process PID was 8568,
and its structured provider error was `cancelled`, status 1, retry count 0,
`retryable=false`, billing mode `chatgpt_codex_subscription`.  B's PID was
32308, exit code 0, with 13,255 input / 6 output / 0 reasoning / 6,912 cached
tokens.  No fallback relaunch occurred.  Server-side post-interrupt token
behaviour is not directly observable from the CLI and is reported as such.

## B — Luna account/catalog

The refreshed local Codex catalog (`%USERPROFILE%\.codex\models_cache.json`,
CLI `0.147.0`) lists `gpt-5.6-luna` as API-visible with efforts
`low,medium,high,xhigh,max`.  The authenticated one-call smoke used
`gpt-5.6-luna/max` and returned `LIVE_PROVIDER_OK` with no fallback: 11,802
input, 58 output, 49 reasoning, 0 cached tokens; PID 14852, exit code 0.

## C — production mini-smoke

`CampaignEngine` constructed a real `ResearchOrchestrator`, which constructed
the real `AsyncDAGScheduler` and `AsynchronousPipelineRuntime`.  Four synthetic
obligations exercised routine, research, strategic, and literature-first work;
proof/literature/verification handlers were local deterministic handlers, so no
provider request was made.  The campaign stopped at a durable checkpoint and
then resumed to `COMPLETE_PROVED_REPLAY`.  The route records show routine
Luna Max, research Sol High, and strategic Sol Max, each with routing call ids.

## D/E/F — scholarly authority path

The public query `Pythagorean theorem` was sent to OpenAlex only.  The selected
record is Richard V. Kadison, “The Pythagorean Theorem: I. The finite case,”
DOI `10.1073/pnas.032677199` (2002).  The public Europe PMC PDF render for
PMCID `PMC123622` was retrieved, verified by `%PDF-`, hashed, parsed with
`pdftotext`, and Proposition 1 was extracted.  The separate external authority
registry matched source identity, metadata, exact statement/span, and artifact
hashes, producing `VERIFIED_SOURCE_THEOREM` only.  A research-tier
applicability reconstructor then mapped the exact current target, authorized
assumptions, source hypotheses/conclusion, notation, direction, normalization,
and exceptions.  A distinct research-tier theorem verifier independently
checked that durable artifact.  Only its `APPLICABLE` verdict and the
Registry's deterministic promotion to `APPLICABLE_EXTERNAL_AUTHORITY` closed
the positive synthetic obligation.  Same-source hypothesis-mismatch and
wrong-direction obligations remained open.  The registry is evidence only and
did not bypass the project Archivist/truth state.

The config enables the scholarly metadata/full-text/PDF capabilities but keeps
`external_transmission_approved=false`.  A campaign must still provide a
minimized public query and per-task `external_search_approved=true`; private
project context is never sent to scholarly providers.

## G — readiness and known limitations

Verdict: `READY_FOR_PILOT_CAMPAIGN` under the explicit public-query approval
gate.  The pilot must remain bounded and should begin with a small campaign
budget.  The following are intentionally not claimed: server-side token
accounting after an interrupt, a proof of the cited mathematical result, or a
long-horizon campaign success.  The two known platform blockers in the full
regression remain the pre-existing Windows-only `os.killpg` and `termios`
tests; they are listed in the final test command output rather than hidden.
