"""Deterministic hackathon replay for the Research Observatory.

The replay is deliberately not the old odd-sum smoke test.  It demonstrates a
plausible proof that is wrong outside a hidden finite boundary, records the
failure as a typed route, and then re-runs the repaired candidate through the
same audit gate.  It is local and deterministic so a fresh checkout has a
working Observatory before a Gemini key is configured.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .project import ProjectStore, utc_now
from .state_machine import AuditGate
from .schemas import AuditResultSchema


THEOREM_ID = "bounded-euler-polynomial"
PROJECT_NAME = "Research Observatory Showcase"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _audit(
    role: str,
    verdict: str,
    *,
    findings: list[str] | None = None,
    failure_reasons: list[str] | None = None,
    criteria: dict[str, bool] | None = None,
    summary: str = "",
) -> dict[str, Any]:
    value = AuditResultSchema.model_validate(
        {
            "schema_version": 3,
            "role": role,
            "domain_verdict": verdict,
            "execution_status": "OK",
            "findings": findings or [],
            "failure_reasons": failure_reasons or [],
            "cross_audit_notes": [],
            "computational_evidence": [],
            "summary": summary,
            "criteria": criteria or {},
            "authority_uses": [],
            "execution_error": "",
        }
    )
    return value.model_dump(mode="json")


def _gate(*, passed: bool, reasons: list[str] | None = None) -> dict[str, Any]:
    gate = AuditGate(
        forward_implication=passed,
        converse_if_applicable=True,
        exhaustive_cases=passed,
        parameter_ranges=passed,
        boundary_cases=passed,
        dependencies_valid=passed,
        no_counterexample=passed,
        auditors_pass=passed,
        final_auditor_pass=passed,
        computational_evidence_separated=passed,
        failure_reasons=list(reasons or []),
    )
    return {"schema_version": 3, **gate.to_dict()}


def _usage(*, input_tokens: int, output_tokens: int, calls: int, elapsed: int) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "source": "deterministic_showcase_replay",
        "provider": "replay",
        "calls": calls,
        "api_requests": 0,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": 0,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": 0.0,
        "wall_clock_seconds": elapsed,
    }


def _provenance(path: Path, claim: str) -> dict[str, str]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "claim": claim,
        "source": str(path.name),
        "sha256": f"sha256:{digest}",
        "registry": "showcase-foundation-registry-v1",
    }


def _initial_candidate() -> str:
    return """# Candidate proof (intentionally flawed)

Claim: for every integer n >= 0, n^2 + n + 41 is prime.

The first forty values are prime.  The candidate silently upgrades that finite
observation to a universal lemma and submits it as the theorem.  No proof of
the universal lemma is supplied.
"""


def _repaired_candidate() -> str:
    return """# Repaired proof

Claim: for every integer n with 0 <= n <= 39, n^2 + n + 41 is prime.

The repair restores the missing parameter boundary.  A deterministic finite
certificate checks the forty values; every value has no divisor between 2 and
its integer square root.  The certificate is computational evidence, kept
separate from the natural-language implication, and the boundary auditor
rechecks n=0 and n=39.
"""


def _event_stream() -> list[dict[str, Any]]:
    now = utc_now()
    return [
        {"at": now, "type": "RUN_STARTED", "run_id": "run-01-hidden-defect", "status": "RUNNING"},
        {
            "at": now,
            "type": "AGENT_STARTED",
            "run_id": "run-01-hidden-defect",
            "role": "planner",
            "tier": "strategic",
            "model": "gemini-3.1-pro-preview",
        },
        {
            "at": now,
            "type": "AGENT_STARTED",
            "run_id": "run-01-hidden-defect",
            "role": "constructive",
            "tier": "research",
            "model": "gemini-3.1-pro-preview",
        },
        {
            "at": now,
            "type": "AGENT_STARTED",
            "run_id": "run-01-hidden-defect",
            "role": "counterexample_hunter",
            "tier": "research",
            "model": "gemini-3.1-pro-preview",
        },
        {
            "at": now,
            "type": "AUDIT_FAIL",
            "run_id": "run-01-hidden-defect",
            "role": "counterexample_hunter",
            "finding": "n=41 gives 41^2",
            "category": "COUNTEREXAMPLE",
        },
        {
            "at": now,
            "type": "AUDIT_FAIL",
            "run_id": "run-01-hidden-defect",
            "role": "dependency_auditor",
            "finding": "unproved universal lemma",
            "category": "DEPENDENCY_GAP",
        },
        {
            "at": now,
            "type": "FAILED_ROUTE",
            "run_id": "run-01-hidden-defect",
            "route_id": "route-unbounded-primality",
            "status": "FAILED_ROUTE",
        },
        {
            "at": now,
            "type": "REPAIR_STARTED",
            "run_id": "run-02-repair",
            "parent_run_id": "run-01-hidden-defect",
            "role": "planner",
            "model": "gemini-3.1-pro-preview",
        },
        {
            "at": now,
            "type": "REPAIR_APPLIED",
            "run_id": "run-02-repair",
            "change": "restore 0 <= n <= 39 and attach finite certificate",
        },
        {
            "at": now,
            "type": "AUDIT_PASS",
            "run_id": "run-02-repair",
            "role": "counterexample_hunter",
        },
        {"at": now, "type": "AUDIT_PASS", "run_id": "run-02-repair", "role": "dependency_auditor"},
        {"at": now, "type": "AUDIT_PASS", "run_id": "run-02-repair", "role": "final_proof_auditor"},
        {"at": now, "type": "PROVED", "run_id": "run-02-repair", "status": "PROVED"},
    ]


def _write_run(
    root: Path,
    *,
    run_id: str,
    status: str,
    parent_run_id: str | None,
    candidate: str,
    audits: dict[str, dict[str, Any]],
    gate: dict[str, Any],
    failure_map: dict[str, Any],
    usage: dict[str, Any],
    route: str,
) -> None:
    run_dir = root / "runs" / run_id
    _write_text(run_dir / "CANDIDATE_PROOF.md", candidate)
    _write_json(
        run_dir / "state.json",
        {
            "schema_version": 3,
            "run_id": run_id,
            "target_id": THEOREM_ID,
            "status": status,
            "phase": "COMPLETE",
            "parent_run_id": parent_run_id,
            "route": route,
            "created_at": utc_now(),
            "completed_at": utc_now(),
            "audit_gate": gate,
        },
    )
    for role, result in audits.items():
        _write_json(run_dir / "audits" / f"{role}.json", result)
    _write_json(run_dir / "audits" / "gate.json", gate)
    _write_json(run_dir / "FAILURE_MAP.json", failure_map)
    _write_json(run_dir / "usage.json", usage)
    _write_json(
        run_dir / "pipeline.json",
        {
            "schema_version": 3,
            "nodes": [
                {"id": "planner", "role": "Planner", "status": "PASS"},
                {"id": "constructive", "role": "Constructive Worker", "status": "PASS"},
                {
                    "id": "counterexample",
                    "role": "Counterexample Hunter",
                    "status": "PASS" if status == "PROVED" else "FAIL",
                },
                {
                    "id": "dependency",
                    "role": "Dependency Auditor",
                    "status": "PASS" if status == "PROVED" else "FAIL",
                },
                {
                    "id": "final",
                    "role": "Final Proof Auditor",
                    "status": "PASS" if status == "PROVED" else "BLOCKED",
                },
            ],
            "edges": [
                ["planner", "constructive"],
                ["constructive", "counterexample"],
                ["counterexample", "dependency"],
                ["dependency", "final"],
            ],
        },
    )


def run_showcase(project_root: str | Path) -> dict[str, Any]:
    """Create the showcase project once and return its manifest."""

    root = Path(project_root).resolve()
    manifest_path = root / "showcase.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    store = ProjectStore.initialize(root, PROJECT_NAME, project_id="observatory-demo", demo=True)
    source = root / "sources" / "bounded_euler_polynomial.md"
    _write_text(source, "The showcase theorem is bounded: 0 <= n <= 39.\n")
    store.add_theorem(
        THEOREM_ID,
        "Euler polynomial on a finite boundary",
        "For every integer n with 0 <= n <= 39, n^2 + n + 41 is prime.",
        source_file="sources/bounded_euler_polynomial.md",
        tags=["showcase", "counterexample", "finite-certificate"],
        proof_type="NATURAL_LANGUAGE_WITH_CERTIFICATE",
        claim_type="implication",
    )
    store.set_current_target(THEOREM_ID)
    store.transition(
        THEOREM_ID, "IN_RESEARCH", actor="MasterPlanner", reason="showcase route started"
    )
    store.transition(
        THEOREM_ID, "CANDIDATE_PROOF", actor="MasterPlanner", reason="candidate assembled"
    )
    store.transition(
        THEOREM_ID, "AUDITING", actor="MasterPlanner", reason="candidate sent to independent gate"
    )

    failed_reasons = [
        "Counterexample Hunter: n=41 gives 41^2, so the universal claim is false.",
        "Dependency Auditor: the universal primality lemma is asserted but unproved.",
    ]
    first_audits = {
        "counterexample_hunter": _audit(
            "counterexample_hunter",
            "FAIL",
            findings=["n=41 gives 41^2, which is composite."],
            failure_reasons=[failed_reasons[0]],
        ),
        "dependency_auditor": _audit(
            "dependency_auditor",
            "FAIL",
            failure_reasons=[failed_reasons[1]],
        ),
        "exhaustiveness_auditor": _audit(
            "exhaustiveness_auditor",
            "FAIL",
            failure_reasons=["The candidate has no stated upper boundary."],
        ),
        "boundary_auditor": _audit(
            "boundary_auditor",
            "FAIL",
            failure_reasons=["The hidden boundary n=41 is not checked."],
        ),
        "final_proof_auditor": _audit(
            "final_proof_auditor",
            "FAIL",
            failure_reasons=failed_reasons,
            criteria={"forward_implication": False},
            summary="The candidate is persuasive but not admissible.",
        ),
    }
    first_gate = _gate(passed=False, reasons=failed_reasons)
    first_failure_map = {
        "schema_version": 3,
        "run_id": "run-01-hidden-defect",
        "target_id": THEOREM_ID,
        "items": [
            {
                "category": "COUNTEREXAMPLE",
                "exact_rejected_claim": failed_reasons[0],
                "auditor": "counterexample_hunter",
                "candidate_location": "CANDIDATE_PROOF.md:3",
                "blocking": True,
                "repair_suggestion": "restore the finite parameter range and attach a certificate",
            },
            {
                "category": "DEPENDENCY_GAP",
                "exact_rejected_claim": failed_reasons[1],
                "auditor": "dependency_auditor",
                "candidate_location": "CANDIDATE_PROOF.md:5",
                "blocking": True,
                "repair_suggestion": "replace the universal lemma with a finite checked claim",
            },
        ],
    }
    _write_run(
        root,
        run_id="run-01-hidden-defect",
        status="REJECTED",
        parent_run_id=None,
        candidate=_initial_candidate(),
        audits=first_audits,
        gate=first_gate,
        failure_map=first_failure_map,
        usage=_usage(input_tokens=2840, output_tokens=910, calls=5, elapsed=0),
        route="unbounded-primality",
    )
    store.record_failed_route(
        route_id="route-unbounded-primality",
        strategy="universal-primality-upgrade",
        target=THEOREM_ID,
        obtained="A finite observation was presented as a universal proof.",
        failure_point="n=41 gives 41^2",
        insufficiency="The candidate omitted the theorem's finite boundary and had no authority for the universal lemma.",
        recovery_conditions="Restore 0 <= n <= 39 and provide a replayable finite certificate.",
        theorem_ids=[THEOREM_ID],
        tags=["showcase", "counterexample", "repairable"],
    )
    store.transition(
        THEOREM_ID,
        "REJECTED",
        actor="Archivist",
        reason="independent audit gate rejected candidate",
    )
    store.transition(
        THEOREM_ID,
        "IN_RESEARCH",
        actor="MasterPlanner",
        reason="FAILED_ROUTE launched bounded repair",
    )
    store.transition(
        THEOREM_ID, "CANDIDATE_PROOF", actor="MasterPlanner", reason="repaired candidate assembled"
    )
    store.transition(
        THEOREM_ID,
        "AUDITING",
        actor="MasterPlanner",
        reason="repaired candidate sent to independent gate",
    )

    repaired_criteria = {
        "forward_implication": True,
        "converse_if_applicable": True,
        "exhaustive_cases": True,
        "parameter_ranges": True,
        "boundary_cases": True,
        "dependencies_valid": True,
        "no_counterexample": True,
        "auditors_pass": True,
        "computational_evidence_separated": True,
    }
    second_audits = {
        role: _audit(
            role,
            "PASS",
            findings=["Repaired bounded claim is internally consistent."],
            criteria=repaired_criteria if role == "final_proof_auditor" else {},
            summary="The repaired candidate passes this independent gate.",
        )
        for role in (
            "counterexample_hunter",
            "dependency_auditor",
            "exhaustiveness_auditor",
            "boundary_auditor",
            "final_proof_auditor",
        )
    }
    second_gate = _gate(passed=True)
    second_failure_map = {
        "schema_version": 3,
        "run_id": "run-02-repair",
        "target_id": THEOREM_ID,
        "items": [],
        "resolved_from": "run-01-hidden-defect",
    }
    _write_text(
        root / "certificates" / "euler-0-39-finite-check.txt",
        "certificate: euler-0-39-finite-check\nrange: 0..39\nall_values_prime: true\n",
    )
    _write_run(
        root,
        run_id="run-02-repair",
        status="PROVED",
        parent_run_id="run-01-hidden-defect",
        candidate=_repaired_candidate(),
        audits=second_audits,
        gate=second_gate,
        failure_map=second_failure_map,
        usage=_usage(input_tokens=3960, output_tokens=1520, calls=5, elapsed=0),
        route="bounded-finite-certificate-repair",
    )
    store.transition(
        THEOREM_ID,
        "PROVED",
        actor="Archivist",
        reason="repaired candidate passed all deterministic and independent audit gates",
        gate=AuditGate(
            forward_implication=True,
            converse_if_applicable=True,
            exhaustive_cases=True,
            parameter_ranges=True,
            boundary_cases=True,
            dependencies_valid=True,
            no_counterexample=True,
            auditors_pass=True,
            final_auditor_pass=True,
            computational_evidence_separated=True,
        ),
        audit_status="PASS",
    )
    _write_json(
        root / "formal_status.json",
        {
            "schema_version": 3,
            "status": "PENDING_FORMALIZATION",
            "agent": "formalization_agent",
            "tool": "lean_verify",
            "certificate": "",
            "note": "The natural-language gate is complete; the optional Lean lane is visible but not claimed as run by the local replay.",
        },
    )
    _write_json(
        root / "provenance.json",
        {
            "schema_version": 3,
            "entries": [_provenance(source, "bounded theorem statement")],
        },
    )
    events = _event_stream()
    _write_text(
        root / "events.jsonl", "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
    )
    _write_json(
        root / "observatory.json",
        {
            "schema_version": 3,
            "title": "Hidden defect → autonomous repair",
            "headline": "A persuasive proof is rejected, repaired, and re-audited.",
            "target_id": THEOREM_ID,
            "dag": {
                "nodes": [
                    {
                        "id": "theorem",
                        "label": "Bounded Euler polynomial",
                        "kind": "theorem",
                        "status": "PROVED",
                    },
                    {
                        "id": "candidate-1",
                        "label": "Persuasive universal candidate",
                        "kind": "candidate",
                        "status": "REJECTED",
                    },
                    {
                        "id": "counterexample",
                        "label": "n = 41 → 41²",
                        "kind": "counterexample",
                        "status": "FOUND",
                    },
                    {
                        "id": "failure-map",
                        "label": "FAILED_ROUTE",
                        "kind": "failure",
                        "status": "STORED",
                    },
                    {
                        "id": "candidate-2",
                        "label": "Bounded finite-certificate repair",
                        "kind": "candidate",
                        "status": "PROVED",
                    },
                ],
                "edges": [
                    ["candidate-1", "counterexample"],
                    ["counterexample", "failure-map"],
                    ["failure-map", "candidate-2"],
                    ["candidate-2", "theorem"],
                ],
            },
            "provenance": {
                "sha256": "see provenance.json",
                "registry": "showcase-foundation-registry-v1",
            },
            "formal": {"status": "PENDING_FORMALIZATION", "tool": "lean_verify"},
        },
    )
    _write_json(
        root / "campaigns" / "showcase" / "campaign.json",
        {
            "schema_version": 3,
            "campaign_id": "showcase",
            "target_id": THEOREM_ID,
            "status": "PROVED",
            "runs": ["run-01-hidden-defect", "run-02-repair"],
            "failed_route": "route-unbounded-primality",
            "parent_run_id": "run-01-hidden-defect",
            "successor_run_id": "run-02-repair",
        },
    )
    report = """# Research Observatory Showcase

The first candidate looked convincing but was false at `n=41`: `41²` is
composite.  The Counterexample Hunter and Dependency Auditor rejected it,
stored `FAILED_ROUTE`, and launched a bounded repair.  The successor restores
`0 <= n <= 39`, attaches a finite certificate, and passes the complete audit
gate.
"""
    _write_text(root / "reports" / "showcase.md", report)
    manifest = {
        "schema_version": 3,
        "project": str(root),
        "target_id": THEOREM_ID,
        "status": "PROVED",
        "headline": "Hidden defect → autonomous repair",
        "runs": [
            {"id": "run-01-hidden-defect", "status": "REJECTED", "parent_run_id": None},
            {"id": "run-02-repair", "status": "PROVED", "parent_run_id": "run-01-hidden-defect"},
        ],
        "created_at": utc_now(),
    }
    _write_json(manifest_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Create the deterministic Research Observatory replay"
    )
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run_showcase(args.project), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
