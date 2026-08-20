# Friend Next Steps

This is the recommended order for the next debug and certification engineer.

1. Confirm the local ending HEAD, clean worktree, and the formal freeze flags
   in `CURRENT_STATE.md`.
2. Reproduce NF-003 with the existing partial current-domain binding probe.
3. Reproduce NF-004 with the existing no-backend semantic-route probe.
4. Repair NF-003 so missing required current-domain dimensions fail closed.
5. Repair NF-004 so `require_execution_binding=True` cannot bypass enforcement
   when the runtime backend is absent.
6. Run the complete F-007 binding/restart matrix, including complete, partial,
   stale, standalone, and interrupted cases.
7. Run the Phase 7 focused tests, the relevant integration tests, and the full
   local suite.
8. Resolve the historical Ruff format gate without changing unrelated
   production semantics.
9. Push only under the repository owner’s authorization and run hosted Linux
   CI.
10. Execute the POSIX interruption gate.
11. Run one fresh independent final audit over the consolidated implementation.
12. Grant or deny final certification based on that audit; do not infer it from
    this implementation handoff.

Do not edit the historical F-002/F-005 closure or the prior denied
reauthorization record. Do not treat the Phase 7 owner override as formal
authorization, and do not mark NF-003/NF-004 closed without independent
evidence.
