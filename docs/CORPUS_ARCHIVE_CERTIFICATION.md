# Research Corpus Archive Certification

Certification date: 2026-08-28
Verdict: **CERTIFIED_WITH_KNOWN_BASELINE_FAILURE**

## Revision record

- Original baseline HEAD: `2bafdf48c3db3a4836932187e4f0b231b55fc002`
- Frozen archive implementation: `099e136b9f17a168f23dc6a45265ce99cdba3460`
- Publication-invariant repair: `7842493cea9740cf1b57a6ee553e03f18205a962`
- Crash/hash/concurrency regressions: `3e823f6a7f5c77267782ef07bed53d35c2e89174`
- Windows drive-root lock compatibility: `8e313a0f236cf00a4a51a640aac52d21d16f3175`
- Final certification HEAD: the commit containing this report; resolve with
  `git rev-parse HEAD` (the terminal certification summary records the exact
  value).

The archive implementation and every certification repair are committed. The
only remaining dirty-tree item is the pre-existing, user-owned, untracked
`AUDIT_METADATA/critical-layer-proof-as-test/` fixture. It was never staged.

## Certified authority boundary

The implementation preserves:

`Truth != Research != Runtime != Corpus projection`

Ordinary publication starts only from a committed `AcceptedEffect` and its
durable event. Strict publication starts only from a current active
`FinalProofAuthority`. Archive enqueue, checkout, index, validation, commit, or
push failure is caught outside the ResearchStore transaction and cannot revoke
or manufacture mathematical authority. Raw Planner, Worker, Verifier, audit,
scratch, runtime, and candidate-proof material has no direct corpus write path.

No Planner, Worker, Verifier, ResearchMap, TruthStore, proof-search, or Phase 7
semantics were redesigned. The only Phase 7 seam remains the post-commit
`FinalProofAuthority` notification.

## Receipt content-hash verdict

**PASS.** Every `ArchiveReceipt.contentHashes[path]` value is now computed from
the exact bytes returned by Git for `corpusResultCommit:path`, not from the
working tree.

The regression deliberately gives `INDEX.md` and `TREE.md` different bytes,
crashes after the local corpus commit, mutates the projected working-tree file,
recovers the receipt, and independently compares every key/value with SHA-256
of the corresponding committed blob. This detects reused buffers, reused
hashes, wrong path bindings, working-tree hashing, and post-index working-tree
changes.

The previously observed equal `INDEX.md` and `TREE.md` hashes were **expected
fixture behavior**, not the production hash bug: the old test generator wrote
the same string to both files. The production implementation nevertheless did
contain a real audit gap because it hashed working-tree files. That gap is now
fixed and covered by deliberately non-identical index fixtures.

The real shadow receipt was independently verified:

- `INDEX.md`: `88068b27250c6822098b4f6eeb9ce48c48ea01ee7e21a10c7a4d08910b8dc6ba`
- `research/templates/g/c2/attempts/source-progression-reduction.md`:
  `2e71150c41176c6dc099893dd38b1c8816ed7b1c7c2050744c8008193db9bcc8`

Both values equal SHA-256 of the blobs at shadow result commit
`b9217a4df1bee6e58868d00bdaba2510f6849748`.

## Pre-intent crash verdict

**PASS.** The durable semantic source is the recovery authority and the intent
is projection bookkeeping.

- Ordinary recovery scans activation-bounded committed `AcceptedEffect` and
  event pairs, reconstructs the same stable intent ID, and creates it once.
- Strict recovery scans the current active `FinalProofAuthority`, reconstructs
  the same stable strict intent ID, and creates it once.
- A second reconcile creates no additional intent.
- A completed receipt suppresses reconstruction and delivery on repeated
  reconcile.

Intent IDs are `stableId("corpus-archive-intent", projectId, sourceId)`, where
`sourceId` is the durable effect ID or final-proof-authority ID. Wall-clock
timestamps do not participate in identity.

## Strict publication authority verdict

**PASS.** A final-proof file alone is insufficient. Strict publication requires
one exact current durable chain:

1. project status is `PROVED`;
2. `currentFinalProofAuthority` is `ACTIVE`, appears active in final-proof
   history, and matches the requested authority ID;
3. its root claim is the project's current root at the exact recorded revision
   and has status `PROVED`;
4. the immutable root objective contract is `VALID` and matches that root;
5. `activeAuthorityForClaim` returns the exact recorded root authority receipt;
6. the final artifact exists as `FINAL_PROOF`, matches `finalProofArtifact`, and
   matches the authority's artifact ID/hash;
7. the root authority is a `PROVED_CLAIM`, its source artifact is that exact
   final proof, and its promoted proof has the same bytes;
8. at least two exact trust receipts exist, are independent, `CORRECT`,
   non-stale, and bind the promoted proof bytes;
9. projection resolves the immutable final-proof body again before rendering.

The final-proof-authority ID binds the final artifact and root authority
receipt, so it serves as the durable closure identity. Tests also prove that a
stale audit collapses the classification to `NO_ARCHIVE`.

## Git publication concurrency policy

**Policy: one local corpus checkout has one publisher writer at a time.**

An atomic adjacent-directory lock covers checkout/clone, fetch, projection,
index generation, validation, commit, remote reconciliation, push, and receipt
completion. In-process and cross-process publishers therefore cannot race on
the same checkout. The lock records its PID and token. A dead PID is reclaimed
by atomically renaming the orphan lock before deletion, so a crash cannot cause
a permanent deadlock or delete a new owner's lock.

Tests pass for concurrent different artifacts, concurrent updates to the same
canonical key, duplicate semantic replay, and an orphaned owner. Different
semantic sources targeting the same canonical key update one artifact rather
than creating duplicates.

Remote advancement is handled without force push. A clean unpublished archive
commit is rebased onto the fetched remote, the real index generator is rerun
and checked for idempotency, generated changes are amended, validation is
repeated, and a normal push is attempted. A second remote advance after
validation becomes `RETRYABLE_FAILURE`; the next reconcile repeats the safe
alignment. Conflicts or unowned diffs fail closed. Production contains no
force-push command.

## Baseline failure comparison

Exact test:

`copied real broken Gemini cycle-5 state reclaims its one stale RUNNING task`

The test was built and run from detached clean worktrees at both required
revisions, using the same real copied fixture:

| Revision | Expected | Actual | Failure |
| --- | ---: | ---: | --- |
| `2bafdf48c3db3a4836932187e4f0b231b55fc002` | 1 provider call | 0 | `AssertionError: 0 !== 1` |
| `099e136b9f17a168f23dc6a45265ce99cdba3460` | 1 provider call | 0 | `AssertionError: 0 !== 1` |
| final certification build | 1 provider call | 0 | `AssertionError: 0 !== 1` |

The fixture begins at cycle 5 with 36 tasks: 23 completed, 12 retryable, and one
running. Its persisted budget started at `2026-08-26T09:44:58.212Z` and permits
86,400,000 ms. At certification it was already older than that limit (observed
elapsed time about 106,650,622 ms), so `ResearchRuntime.run()` records budget
exhaustion before invoking the supplied provider callback. The archive hook is
absent from the baseline revision and absent from the failure stack.

Classification: **PRE-EXISTING BASELINE FAILURE**. It is a time-dependent
legacy fixture expectation, not an archive regression. It is intentionally not
fixed in this sealing pass.

## Storage ownership audit

**PASS.** The archive layer writes only its domain-owned outbox beside the
ResearchStore project and the configured canonical Git checkout. Existing
owners remain unchanged:

- tests: `backend/test/`;
- run-local proof state: existing proof-run storage;
- attempt scratch: existing ResearchStore scratch directory;
- immutable runtime/research artifacts: existing artifact store;
- audits: existing audit store;
- candidate/final proof and Phase 7 records: existing proof/ResearchStore
  locations;
- long-term mathematical knowledge: canonical Git corpus.

The placement regression confirms that projection does not copy runtime,
scratch, audit, test, or immutable artifact-store files into the corpus.

## Real canonical shadow publication

Repository: `dongxuelian2/three-term-decimal-concatenation-square-sum`

Canonical branch: `master`

Base commit: `2cfa389f1d4ced90653101e6c92ee8dfe85b5535`

Resolved node: `research/templates/g/c2`

Classification: `ATTEMPT`

Semantic filename: `source-progression-reduction.md`

Auto-push: `false`

The controlled durable verified reduction traversed the real policy, resolver,
renderer, real `tools/update-research-index.py`, validator, Git commit, and
receipt path. The local shadow commit would have:

- created
  `research/templates/g/c2/attempts/source-progression-reduction.md`;
- added one deterministic entry to `INDEX.md`.

Review results:

- correct existing G/C2 node; no new top-level branch;
- mathematical lowercase-kebab filename; no run/campaign/round naming;
- one canonical-key marker and one intent marker;
- no duplicate artifact;
- `ATTEMPT` scope explicitly asserts no global closure;
- root/G/C2 READMEs, `STATUS.md`, theorem records, and `TREE.md` unchanged;
- no runtime, audit, scratch, local path, credential, or provider content;
- real index generator produced no diff on either of two subsequent runs;
- receipt push status `SKIPPED`;
- remote `master` remained at the base commit before and after reset.

The shadow checkout was reset to the exact base commit, verified clean, and the
temporary checkout/state directories were then removed. No shadow corpus commit
was pushed or retained as canonical content.

## Test matrix

| Check | Result |
| --- | --- |
| TypeScript typecheck | PASS |
| Backend build | PASS |
| Full application build, including GUI syntax checks | PASS |
| Focused corpus archive suite | PASS, 22/22 |
| Corpus configuration + research API tests | PASS, 4/4 |
| Receipt exact-commit hash regressions | PASS |
| Ordinary and strict crash-before-intent regressions | PASS |
| Concurrent different/same artifact and orphan-lock regressions | PASS |
| Remote advance/non-fast-forward recovery | PASS |
| Real canonical no-push shadow publication | PASS |
| Real index generator idempotency | PASS |
| Full deterministic backend suite | 120/121; sole failure proven baseline |
| `git diff --check` | PASS |

## Security and repository hygiene

The complete archive-task diff was scanned for private keys, tokens, API-key
assignments, service-account payloads, private absolute paths, generated junk,
and oversized artifacts. No secret value, credential file, private path, or
generated runtime artifact was committed. Two credential-shaped JSON files at
the repository root pre-existed, are ignored by `.gitignore`, and were not read,
staged, or modified. The untracked 2.9 MB audit fixture was preserved as user
state and excluded from every commit.

## Committed file scope

The frozen implementation contains the archive types, policy, durable outbox,
projector/reconciler/coordinator, CLI, API/config integration, reducer and
closure post-commit hooks, focused tests, package scripts, and archive protocol
documentation. Certification repairs changed only the archive policy,
projector, archive tests, and this report. Planner, Worker, Verifier, proof
strategy, ResearchMap semantics, and truth semantics are absent from the
changed-file set.

## Unresolved issues and deviations

- Known issue: the copied real Gemini fixture test has the proven pre-existing
  wall-time-dependent expectation described above.
- Architecture deviations: none.
- Automatic historical backfill: not added.
- Real corpus publication: no push occurred.

All hard archive criteria are proven. Because the repository's complete suite
retains one formally baselined failure, the correct final verdict is:

**CERTIFIED_WITH_KNOWN_BASELINE_FAILURE**
