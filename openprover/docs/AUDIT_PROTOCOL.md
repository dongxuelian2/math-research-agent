# Audit Protocol v2

Every formal auditor returns two orthogonal fields:

- `domain_verdict`: `PASS`, `FAIL`, or `INCONCLUSIVE`.
- `execution_status`: `OK` or `ERROR`.

`FAIL` means the auditor completed and found a defect in its assigned mathematical domain. `ERROR` means the audit did not execute reliably because of infrastructure such as encoding, subprocess, timeout, malformed JSON, provider transport, or filesystem failure. An execution error is normalized to `domain_verdict=INCONCLUSIVE`; it is never a mathematical rejection.

Auditors also return:

- `findings`
- `failure_reasons`
- `cross_audit_notes`
- `computational_evidence`
- `authority_uses`

Legacy `verdict/pass` JSON is accepted by the normalizer for normal-run compatibility, but all newly archived audit JSON uses schema version 2 and retains compatibility fields only for old readers.

## Domain ownership

The Counterexample Hunter reports only whether it found a counterexample or completed its adversarial search. A dependency concern is written to `cross_audit_notes`; it does not turn Counterexample PASS into FAIL.

Dependency Auditor v2 classifies claims and records exact authority IDs. It owns dependency, semantic, and foundation admissibility failures.

Exhaustiveness/Converse and Boundary auditors remain confined to their mathematical domains. The Final Proof Auditor synthesizes the domain results but treats any specialist execution error as infrastructure/inconclusive rather than a theorem refutation.

## Gate outcomes

The gate exposes four outcomes:

- `PASS`
- `MATHEMATICAL_FAIL`
- `INCONCLUSIVE`
- `INFRASTRUCTURE_ERROR`

Phase 2 maps these outcomes into campaign lifecycle behavior, including bounded retry and infrastructure checkpoint states. The central invariant is already enforced by the schema: `execution_status=ERROR` cannot be represented as mathematical `FAIL`.
