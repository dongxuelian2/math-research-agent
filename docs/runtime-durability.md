# Runtime durability guarantees

MRR v1.2 targets a single-machine runtime. It does not claim distributed consensus or distributed exactly-once execution.

## Bootstrap

Bootstrap uses one versioned JSON Schema for the model request and the strict runtime parser. Each bounded range is checkpointed with its source hash, configuration revision, schema version, attempt/session metadata, raw response, typed failure, parsed result, deterministic fallback, and duration. Completed ranges are not rerun on resume. A dead executor's `RUNNING` range is reclaimed. Corpus, configuration, or schema identity changes mark the old run and its range records `STALE`.

Range extraction remains provisional. Partial or complete historical extraction is not mathematical authority. Provisional import is idempotent through stable entity/effect identities, and a completed bootstrap run returns its existing report.

## Provider execution

Codex prompts are sent on stdin, never as an unbounded command-line argument. A structured response schema is passed through `--output-schema`; reasoning effort is passed explicitly when configured. External model execution is at-least-once: a crash after provider completion but before the durable receipt can cause a retry.

## Project state

All `ResearchStore` instances in one process share a per-project mutation queue. A state update writes a uniquely named temp file, flushes it, closes its handle, and then replaces `state.json`. Transient Windows `EPERM`, `EBUSY`, and `EACCES` failures receive eight bounded quadratic-backoff attempts. A persistent failure is surfaced and the previous canonical file remains readable; identifiable temp files are non-authoritative and may be garbage-collected after diagnosis. There is no delete-then-rename durability window.

The filesystem's rename/replace primitive is the commit boundary. A crash after replacement but before acknowledgement may report failure even though the new canonical state is readable; retry logic must therefore remain idempotent.

## Execution ownership

`RUNNING` attempts, tactical tasks, and bootstrap ranges carry executor/process ownership and timestamps. A new runtime instance deterministically reclaims work owned by an absent instance, retains completed work, and makes unfinished work retryable. Same-process hung-executor detection is limited to the surrounding abort/timeout policy; this implementation does not provide a distributed lease service.

Canonical mathematical effects remain exactly-once by stable `AcceptedEffect` identity even though provider calls are at-least-once.

## Research corpus projection

Long-term corpus delivery has a separate, domain-owned outbox at
`projects/<project>/corpus-archive/state.json`. It is not part of
`ResearchProjectState` and cannot grant mathematical authority. The outbox uses
the same temp-write, file flush, and atomic replacement commit model as the
ResearchStore, and malformed archive state fails closed without migrating or
changing research truth.

An ordinary intent is enqueued only after the `AcceptedEffect` transaction has
returned. A strict intent is enqueued only after active `FinalProofAuthority`
has been committed. Callback, checkout, validation, commit, or push failure is
caught outside those truth transactions.

Activation bounds automatic recovery. A project whose frozen configuration
enabled publishing uses project creation as its lower bound; legacy/default-
disabled projects never activate, so a new installation does not republish old
history. Stable source identities, embedded corpus markers,
Git commit trailers, and remote containment checks recover crashes before a
commit, after a local commit, and after push but before receipt. Delivery is
exactly once at the semantic-source level under the existing single-machine
runtime assumption; no distributed consensus claim is made.
