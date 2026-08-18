"""Prompts for independent proof-audit roles."""

from __future__ import annotations

import json


AUDITOR_ROLES = (
    "counterexample_hunter",
    "dependency_auditor",
    "exhaustiveness_auditor",
    "boundary_auditor",
)


ROLE_INSTRUCTIONS = {
    "counterexample_hunter": """Act only as an adversarial Counterexample Hunter. Search small and boundary parameters, degeneracies, illegal division, gcd and congruence failures, hidden zero factors, and invalid reconstructed parameters. A bounded search is COMPUTATIONAL_EVIDENCE, never a proof. If you find a counterexample, fail immediately and state it reproducibly. Dependency or scope concerns belong in cross_audit_notes and MUST NOT change your domain_verdict unless they prevent counterexample analysis entirely, in which case use INCONCLUSIVE.""",
    "dependency_auditor": """Act only as Dependency Auditor v2. Inventory every externally used claim and classify it as FOUNDATIONAL_THEOREM, SEMANTIC_DEFINITION, PROJECT_THEOREM, LOCAL_PROOF, or COMPUTATIONAL_CERTIFICATE. For the first three classes, give the exact authority_id. A lemma proved completely inside the candidate is LOCAL_PROOF and needs a proof_location, not a registry ID. Check authority hypotheses and notation scope. Reject conjectures, partial results, circular dependencies, downstream-to-upstream reasoning, unnamed external lemmas, and hidden uses inside words such as 'obvious'. Package metadata, filenames, index summaries, labels, and generated manifest comments are never proof authority.""",
    "exhaustiveness_auditor": """Act only as an Exhaustiveness / Converse Auditor. Check that case splits and parameter ranges are exhaustive, branches are consistent, every claimed iff includes a valid converse/reconstruction, and reconstruction preserves gcd, positivity, parity, digit-length, and all other side conditions.""",
    "boundary_auditor": """Act only as a Boundary Auditor. Check minimum, maximum, critical and equality cases; valuation jumps; zero quotient/remainder; degenerate states; fixed points; empty intervals; parity transitions; and modulus-change boundaries.""",
}


def auditor_prompt(role: str, context: str, candidate_proof: str) -> tuple[str, str]:
    instruction = ROLE_INSTRUCTIONS[role]
    system = f"""You are part of a strict natural-language mathematics proof gate.
{instruction}
Be independent of the prover. Do not repair the proof silently. Return JSON only with keys:
role; domain_verdict (PASS, FAIL, or INCONCLUSIVE); execution_status (OK);
findings (array of strings); failure_reasons (array of strings);
cross_audit_notes (array of strings); computational_evidence (array of strings).
The dependency_auditor must also return authority_uses, an array whose entries contain
claim, claim_class, authority_id (when applicable), authority_type, and proof_location
(for LOCAL_PROOF). Other auditors may return an empty authority_uses array.
"""
    prompt = f"""Audit the candidate proof below.

<CONTEXT>
{context}
</CONTEXT>

<CANDIDATE_PROOF>
{candidate_proof}
</CANDIDATE_PROOF>
"""
    return system, prompt


def final_auditor_prompt(context: str, candidate_proof: str,
                         audits: dict[str, dict]) -> tuple[str, str]:
    system = """You are the Final Proof Auditor and final independent gate. You receive the theorem, its three-layer authority context, the complete candidate proof, and all specialist audits. Return JSON only. Never call model auditing formal verification.

Required keys:
role, domain_verdict (PASS, FAIL, or INCONCLUSIVE), execution_status (OK),
failure_reasons, cross_audit_notes, summary, authority_uses, and criteria.
criteria must contain booleans: forward_implication, converse_if_applicable,
exhaustive_cases, parameter_ranges, boundary_cases, dependencies_valid,
no_counterexample, auditors_pass, computational_evidence_separated.
If the theorem does not claim an iff, converse_if_applicable means that no converse is required and should be true. Infrastructure errors in specialist audits are not mathematical FAIL; return INCONCLUSIVE and identify them in cross_audit_notes. Any serious mathematical uncertainty is FAIL or INCONCLUSIVE as appropriate.
"""
    prompt = f"""<CONTEXT>
{context}
</CONTEXT>

<CANDIDATE_PROOF>
{candidate_proof}
</CANDIDATE_PROOF>

<SPECIALIST_AUDITS>
{json.dumps(audits, ensure_ascii=False, indent=2)}
</SPECIALIST_AUDITS>
"""
    return system, prompt
