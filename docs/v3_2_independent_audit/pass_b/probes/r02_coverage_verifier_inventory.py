"""Static, read-only inventory for R-02B and R-02C.

The probe reads production source text and AST field declarations only. It
does not instantiate a project or invoke Phase 7, TruthStore, or a provider.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
PRODUCTION = ROOT / "openprover" / "openprover" / "math_research"


def class_fields(path: Path, class_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            fields: list[str] = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.append(item.target.id)
            return fields
    return []


def contains(term: str) -> list[str]:
    hits = []
    for path in sorted(PRODUCTION.glob("*.py")):
        if term in path.read_text(encoding="utf-8"):
            hits.append(path.relative_to(ROOT).as_posix())
    return hits


def main() -> None:
    map_fields = class_fields(PRODUCTION / "research_map.py", "ResearchMap")
    root_fields = class_fields(PRODUCTION / "phase7.py", "RootSynthesis")
    closure_fields = class_fields(PRODUCTION / "research_evidence.py", "SessionClosure")
    intent_fields = class_fields(PRODUCTION / "truth_mutation.py", "TruthMutationIntent")
    disposition_fields = class_fields(
        PRODUCTION / "research_obligation.py", "ObligationDisposition"
    )

    coverage_terms = [
        "CampaignScopeManifest",
        "CoverageAnchorDefinition",
        "CoverageDisposition",
        "CoverageTransfer",
        "coverage_resolution_manifest",
    ]
    verifier_terms = [
        "VerifierIndependenceReceipt",
        "TrustPolicyRef",
        "ResultTrustKernel",
        "TrustReceipt",
        "independence_policy",
        "policy_satisfied",
    ]
    required_coverage_fields = {
        "ResearchMap": [
            "coverage_anchor_refs",
            "coverage_disposition_refs",
            "research_obligation_refs",
            "obligation_disposition_refs",
        ],
        "RootSynthesis": ["campaign_scope_manifest_ref", "coverage_resolution_manifest"],
    }
    missing_coverage_fields = {
        owner: [field for field in fields if field not in actual]
        for owner, fields, actual in (
            ("ResearchMap", required_coverage_fields["ResearchMap"], map_fields),
            ("RootSynthesis", required_coverage_fields["RootSynthesis"], root_fields),
        )
    }

    coverage_cases = []
    for case, attack in (
        ("C1", "two root-relevant anchors but one obligation"),
        ("C2", "closed obligation but missing anchor"),
        ("C3", "superseded obligation without transfer"),
        ("C4", "transfer missing successor"),
        ("C5", "transfer cycle"),
        ("C6", "same evidence reused for unrelated roles"),
        ("C7", "all obligations closed but campaign scope incomplete"),
        ("C8", "map frontier closed but reconstruction obligation missing"),
    ):
        coverage_cases.append(
            {
                "case": case,
                "attack": attack,
                "status": "NOT_REPRESENTABLE_BY_CURRENT_DURABLE_COVERAGE_MODEL",
                "reason": "No CampaignScope/Coverage anchor, disposition, transfer, or resolution manifest object is present.",
            }
        )

    verifier_cases = []
    for case, attack in (
        ("V1", "different provider"),
        ("V2", "same model"),
        ("V3", "same provider different model"),
        ("V4", "fallback verifier to worker client"),
        ("V5", "fresh_context=false"),
        ("V6", "shared prompt"),
        ("V7", "receipt absent"),
        ("V8", "receipt forged"),
        ("V9", "receipt for other theorem/session"),
        ("V10", "DIFFERENT_MODEL label but fallback same model"),
    ):
        verifier_cases.append(
            {
                "case": case,
                "attack": attack,
                "status": "NO_DURABLE_RECEIPT_OR_POLICY_GATE",
                "reason": "No VerifierIndependenceReceipt/TrustPolicyRef/ResultTrustKernel contract or linkage validator is present.",
            }
        )

    result = {
        "probe": "R02B_R02C",
        "canonical_state_touched": False,
        "dataclass_fields": {
            "ResearchMap": map_fields,
            "ObligationDisposition": disposition_fields,
            "RootSynthesis": root_fields,
            "SessionClosure": closure_fields,
            "TruthMutationIntent": intent_fields,
        },
        "coverage_term_hits": {term: contains(term) for term in coverage_terms},
        "verifier_term_hits": {term: contains(term) for term in verifier_terms},
        "missing_coverage_fields": missing_coverage_fields,
        "root_precondition_observation": {
            "status": "CLOSED_FRONTIER_NOT_PROVEN_COMPLETE_ROOT_COVERAGE",
            "evidence": "RootSynthesis.capture compares obligation_ids with closed_obligation_ids; no anchor/transfer/campaign manifest is an input.",
        },
        "coverage_cases": coverage_cases,
        "verifier_cases": verifier_cases,
        "truth_binding_marker_hits": contains("v3_truth_binding"),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
