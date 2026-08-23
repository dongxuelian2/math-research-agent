"""Deterministic isolated replay of the Pass B R-02A identity attack.

The probe uses the production audit schema/parser, then applies the exact
post-parse assignment present in AuditCoordinator.run_audits. It does not call
an LLM, write a run directory, or alter a canonical theorem.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = type("OpenAI", (), {})
for _name in ("APIConnectionError", "APITimeoutError", "APIStatusError", "AuthenticationError"):
    setattr(openai_stub, _name, type(_name, (Exception,), {}))
sys.modules.setdefault("openai", openai_stub)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "openprover"))
from openprover.math_research.audit_protocol import AuditResult, parse_audit_response


CURRENT = "sha256:" + "a" * 64
WRONG = "sha256:" + "b" * 64
STALE = "sha256:" + "c" * 64
OTHER_THEOREM = "sha256:" + "d" * 64


def provider_payload(role: str, identity: Any = "__omitted__") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 3,
        "role": role,
        "domain_verdict": "PASS",
        "execution_status": "OK",
        "criteria": {},
    }
    if identity != "__omitted__":
        payload["audited_claim_snapshot_hash"] = identity
    return {"structured": payload}


def replay(role: str, label: str, provider_identity: Any) -> dict[str, Any]:
    parsed_identity: Any = None
    normalized_identity: Any = None
    parse_error = ""
    try:
        parsed = parse_audit_response(role, provider_payload(role, provider_identity))
        parsed_identity = parsed.audited_claim_snapshot_hash
        normalized_identity = parsed.to_dict()["audited_claim_snapshot_hash"]
        persisted = parsed.to_dict()
    except Exception as exc:
        parse_error = f"{type(exc).__name__}: {exc}"
        persisted = AuditResult.from_exception(role, exc).to_dict()

    # Exact current coordinator behavior: no equality check; local run state
    # is assigned after parsing (including the exception path).
    persisted["audited_claim_snapshot_hash"] = CURRENT
    classification = (
        "SAFE_NORMALIZATION"
        if provider_identity == CURRENT
        else "SCHEMA_PREVENTS_CASE"
        if parse_error
        else "IDENTITY_OVERWRITE_WITHOUT_CROSSCHECK"
    )
    return {
        "case": label,
        "provider_returned_identity": provider_identity if provider_identity != "__omitted__" else None,
        "parsed_identity": parsed_identity,
        "normalized_identity": normalized_identity,
        "persisted_audit_identity": persisted["audited_claim_snapshot_hash"],
        "audit_gate_identity": CURRENT,
        "root_synthesis_visible_identity": CURRENT,
        "parse_error": parse_error,
        "classification": classification,
    }


def main() -> None:
    specialist = replay("counterexample_hunter", "A1_CORRECT", CURRENT)
    wrong_specialist = replay("counterexample_hunter", "A2_WRONG", WRONG)
    cases = [
        specialist,
        wrong_specialist,
        replay("counterexample_hunter", "A3_OMITTED", "__omitted__"),
        replay("counterexample_hunter", "A4_STALE", STALE),
        replay("counterexample_hunter", "A5_DIFFERENT_THEOREM", OTHER_THEOREM),
        replay("counterexample_hunter", "A6_MALFORMED", 17),
        replay("final_proof_auditor", "A7_CONFLICTING_FINAL", OTHER_THEOREM),
    ]
    cases.append(
        {
            "case": "A7_CONFLICTING_SPECIALIST_VS_FINAL",
            "provider_returned_specialist_identity": wrong_specialist["provider_returned_identity"],
            "provider_returned_final_identity": cases[-1]["provider_returned_identity"],
            "persisted_specialist_identity": wrong_specialist["persisted_audit_identity"],
            "persisted_final_identity": cases[-1]["persisted_audit_identity"],
            "audit_gate_identity": CURRENT,
            "root_synthesis_visible_identity": CURRENT,
            "classification": "IDENTITY_OVERWRITE_WITHOUT_CROSSCHECK",
        }
    )
    print(json.dumps({"probe": "R02A", "canonical_state_touched": False, "cases": cases}, indent=2))


if __name__ == "__main__":
    main()
