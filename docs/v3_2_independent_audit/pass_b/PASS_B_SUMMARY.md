# Agent Audit Pass B — Normative Forensic Summary

Pass B attached the external v3.2 normative specification by immutable
fingerprint, completed the requirements traceability, exercised the public
binding seam in isolated fixtures, and performed the requested R-02 attacks.
No production/test/config code was changed. No canonical root synthesis or
TruthMutation was executed.

## Required flags

```text
BASELINE_HEAD=3229aced9fa9bcae41c5ddfea6b6291a6e68d725
AUTHORITATIVE_SPEC_AVAILABLE=YES
AUTHORITATIVE_SPEC_SHA256=cd72ebf2a726419129871c893ee7c938c7aa9c3286a95e6a4f44f718793b4ccb
MAIN_UNCHANGED=YES
PRODUCTION_CODE_MODIFIED=NO
TEST_CODE_MODIFIED=NO
R01_PUBLIC_CALLER_MATRIX_COMPLETE=YES
R01_DEFECT_REPRODUCED=NO
R02_AUDIT_IDENTITY_DEFECT=YES
COVERAGE_MODEL_STATUS=NONCOMPLIANT
VERIFIER_INDEPENDENCE_STATUS=NONCOMPLIANT
ROOT_SYNTHESIS_MANIFEST_STATUS=NONCOMPLIANT
TRUTH_MUTATION_STATUS=NEEDS_DEEP_RECOVERY_AUDIT
ARCHITECTURE_DEVIATIONS_FOUND=0
CONFIRMED_NORMATIVE_GAPS=11
REPRODUCED_RUNTIME_DEFECTS=1
ROOT_SYNTHESIS_EXECUTED=NO
TRUTH_PROMOTION_EXECUTED=NO
READY_FOR_REPAIR_PLANNING=YES
READY_FOR_DEEP_RECOVERY_AUDIT=YES
```

## Findings

- **R-01:** The 14-row public caller matrix is complete for the tested seam.
  The current binding validator rejects incomplete, stale, cross-session,
  cross-map, root-only, map-only, and unbound/no-backend variants before the
  provider. The isolated oracle observed zero provider calls, accepted
  results, semantic effects, Truth mutations, and Research mutations for each
  rejection.
- **R-02A:** `AuditCoordinator.run_audits` overwrites provider-returned audit
  identity with local run state without equality cross-check. Wrong, omitted,
  stale, different-theorem, and conflicting identities were reproduced in the
  isolated parser/replay probe.
- **R-02B:** The current model can close an obligation frontier without proving
  complete root scope coverage. CampaignScopeManifest, coverage anchors,
  dispositions, transfers, and resolution manifest are absent.
- **R-02C:** The mathematical verifier independence receipt and structured
  TrustPolicyRef/TrustReceipt boundary are absent. The existing architecture
  critic independence receipt is domain-specific and not an equivalent
  mathematical verifier receipt.
- **Root/Truth:** RootSynthesis has useful hash/closure checks but lacks the
  exact v3.2 authority manifest and source-manifest boundary. Truth mutation has
  an intent/prepare/compare/receipt structure, but the required atomic
  `v3_truth_binding` marker is absent and crash recovery needs a dedicated
  deterministic audit.

## Evidence index

- [Authoritative spec reference](../AUTHORITATIVE_SPEC_REFERENCE.md)
- [Normative requirements](AUTHORITATIVE_SPEC_REQUIREMENTS.md)
- [Completed traceability](SPEC_TO_CODE_TRACEABILITY.md)
- [R-01 matrix](R01_PUBLIC_BINDING_CALLER_MATRIX.tsv) and [R-01 audit](R01_BINDING_AUDIT.md)
- [R-02A](R02_AUDIT_IDENTITY_ATTACK.md), [R-02B](R02_COVERAGE_ATTACK.md), [R-02C](R02_VERIFIER_INDEPENDENCE.md)
- [Root diff](ROOT_SYNTHESIS_NORMATIVE_DIFF.md) and [Truth diff](TRUTH_MUTATION_NORMATIVE_DIFF.md)
- [Noncompliance register](NORMATIVE_NONCOMPLIANCE_REGISTER.tsv)
- [Next attack queue](NEXT_ATTACK_QUEUE.md)

All probe scripts are under `probes/` and are read-only with respect to the
canonical project.
