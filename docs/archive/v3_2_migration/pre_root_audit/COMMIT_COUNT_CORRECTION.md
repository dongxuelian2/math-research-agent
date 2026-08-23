# Phase 6 Commit-Count Correction

Authoritative command:

```text
git rev-list --count bdab5cff50227a1504208359539bfe7dba5e7bc2..6ada9282f31ac5cdafb2353dc9c19e2c4fe8aa76
12
```

The exact range contains these 12 commits:

```text
6ada928 docs: align truth recovery matrix with phase 6
5f2f7fd docs: normalize phase-6 report whitespace
044d9b4 test: certify phase-6 durable-runtime invariants
fcf02b8 fix: replay prepared truth mutation before cas
eda728b fix: close final durable-runtime recovery gaps
73ea8da fix: make interruption and cancellation runtime cross-platform
85b5a6e feat: route production execution through durable runtime
3f2bfc4 feat: enforce idempotent exactly-once semantic effects
0eb3e4a feat: reconcile durable attempts and cross-store artifacts
aff81d5 feat: add logical jobs attempts leases and transactional outbox
5a3c061 feat: introduce sqlite wal control plane and transition journal
630098d docs: audit phase-6 runtime ownership and crash boundaries
```

The current nested checkout contains no separate “13 local commits” phrase to
edit; this file is the corrected evidence record and does not alter historical
commits.
