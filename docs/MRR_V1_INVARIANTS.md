# MRR v1.1 hard invariants

These rules are enforced at protocol/reducer boundaries and are more important than any particular model prompt.

1. **Verified subtask is not target proof.** A contribution can add a lemma, reduction, case split, counterexample, bound, construction, obstruction, observation, or literature application. It cannot close its parent unless an exact target submission separately passes the target gate.

2. **Model prose is not truth.** Only immutable candidate bodies plus authoritative verifier receipts can change claim authority. Historical “proved” labels remain provisional.

3. **Context availability is not evidence use.** A `ContextManifest` says what was available. Canonical evidence comes from automatic, role/task-scoped artifact reads. Model-declared evidence is advisory.

4. **The Verifier inspects Worker-used evidence.** A candidate relying on artifact `A@hash` cannot be accepted until the independent Verifier actually reads the same `A@hash`.

5. **Accepted proof bodies are immutable.** Artifact identity includes the body hash. Missing, changed, or mismatched bodies fail authority resolution.

6. **At-least-once execution; exactly-once mathematical effect.** Tasks may resume/retry. Stable effect slots prevent duplicate promotion, claim revision, and canonical event.

7. **Provider failure is not mathematical failure.** Provider, quota, protocol, tool, literature, formal, cancellation, and budget failures do not poison the RouteLedger as failed mathematics.

8. **Structural probe is not structural progress.** Free text and unverified observations do not reset stall state. A concrete graph/coverage/claim/counterexample/route-state change is required.

9. **The Director cannot promote truth.** It may choose obligations, routes, decomposition, literature, computation, or synthesis. Only the reducer accepts mathematical effects.

10. **Route reopening is typed.** Reopen predicates are evaluated against durable state, never matched as prose substrings.

11. **Root proof requires fresh closure.** Dependencies and coverage must close; referenced bodies must resolve; assumptions and stale receipts block readiness; a newly synthesized body requires primary and fresh final audits.

12. **Formal model output is a draft.** Only the configured non-shell Lean/process verifier can create formal authority. Unavailable or failing Lean yields `BLOCKED_FORMAL`.

13. **Canonical state has one writer.** Agents and tools never mutate `state.json` or promoted artifacts. `ResearchStateReducer` applies mathematical state changes through `ResearchStore` transactions.

14. **Controlled execution is not called a sandbox.** The runner restricts command shape, CWD, time, output, and environment, but hostile-code isolation belongs to the deployment boundary.

15. **Conditional claims stay conditional.** Assumptions, dependencies, scope, evidence, and verifier receipts survive every authoritative contribution conversion. An unconditional root is blocked until non-root assumptions are independently discharged or explicitly accepted at the root.

16. **Structural effects are non-vacuous.** A reduction has at least one unique, non-self child. A case split has at least two unique, non-self children plus a nonempty scope and explicit coverage assertion. Case closure and refutation carry an exact case/target scope and resolving proof receipt.

17. **Body identity and authority are separate.** Candidate and promoted bodies are immutable content-addressed artifacts. Mathematical authority exists only through a current `AuthorityReceipt` linked to the claim revision, `AcceptedEffect`, trust receipts, assumptions, dependencies, scope, and exact artifact hash.

18. **Authority changes are atomic.** Claim transition, `AcceptedEffect`, `AuthorityReceipt`, and canonical event are one store transaction. Fault injection at validation, preparation, transition, effect, receipt, and event boundaries must yield either no authority or one complete effect.

19. **Assumption discharge is exact authority lineage.** A discharge records the normalized proposition, dependent claim revision, witness claim revision, witness `AuthorityReceipt`, witness proof artifact/hash, and accepted effect. Claim IDs or `PROVED` status alone never discharge an assumption.

20. **Every acceptance dependency is revocable.** Reverse invalidation follows both ordinary claim dependencies and assumption-discharge authority edges. Historical receipts and proof bodies remain immutable, while active validation, project `PROVED`, and current final-proof authority are withdrawn deterministically.

21. **Terminal plan actions rehydrate.** A durable completed `STOP` action stores its terminal status/reason. Resume restores that result and finalizes the step/plan without replanning or re-executing the action.

19. **Planner/runtime owns task scope.** Worker output cannot promote `CONTRIBUTION` work to `TARGET`; mismatched scope or contribution kind is a protocol error.

20. **Plans, not regenerated prompts, resume.** The Planner output is persisted before dispatch. A restart reuses completed workers, runs missing workers/verifiers, and completes that step before another Planner call. Invalid dependencies mark the old plan `STALE` with a reason before replanning.

21. **Project configuration is historical state.** A project runs from its creation-time `effectiveConfig` and `configRevision`. Research execution never silently borrows a later live TOML snapshot.

22. **Migration never guesses truth.** Schema-v1 history remains inspectable; authority that cannot meet v1.1 receipts/invariants is downgraded to `NEEDS_REVALIDATION`, with a durable migration report and reopened work.
