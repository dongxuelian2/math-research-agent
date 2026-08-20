"""Explicit Gemini-to-Lean formalization lane."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .project import ProjectError, ProjectStore, utc_now
from .providers import create_client, load_model_config
from .routing import ModelRouter, RoutedLLMClient
from .runtime_backend import SQLiteRuntimeBackend
from .runtime_bindings import CrossPlaneExecutionBinding
from .schemas import (
    FormalizationResultSchema,
    parse_structured_response,
)
from .truth_store import TruthStoreFacade


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_formalization(
    project: ProjectStore,
    target_id: str,
    *,
    config_path: str | Path,
    run_dir: str | Path,
) -> dict:
    """Run one typed formalization attempt without changing theorem truth."""

    run_path = Path(run_dir).resolve()
    candidate_path = run_path / "CANDIDATE_PROOF.md"
    context_path = run_path / "context" / "CONTEXT.md"
    if not candidate_path.is_file():
        raise ProjectError(f"Formalization requires a candidate: {candidate_path}")
    if not context_path.is_file():
        raise ProjectError(f"Formalization requires research context: {context_path}")

    theorem = project.load_theorem(target_id)
    truth_store = TruthStoreFacade(project)
    run_state_path = run_path / "state.json"
    run_state = (
        json.loads(run_state_path.read_text(encoding="utf-8")) if run_state_path.is_file() else {}
    )
    stored_snapshot_hash = run_state.get("claim_snapshot_hash")
    if stored_snapshot_hash:
        truth_store.validate_snapshot_for_execution(
            str(stored_snapshot_hash),
            canonical_authority=run_state.get("canonical_authority") or [],
            replay_policy_hash=run_state.get("replay_policy_hash"),
        )
        claim_snapshot = truth_store.load_claim_snapshot(str(stored_snapshot_hash))
    else:
        claim_snapshot = truth_store.capture_claim_snapshot(target_id)
    candidate = candidate_path.read_text(encoding="utf-8")
    context = context_path.read_text(encoding="utf-8")
    formal_dir = run_path / "formalization"
    formal_dir.mkdir(parents=True, exist_ok=True)
    config = load_model_config(config_path)
    binding = CrossPlaneExecutionBinding.capture(
        root_claim_snapshot_hash=claim_snapshot.claim_snapshot_hash
    )

    def validate_binding(current):
        if current is None:
            return "REVALIDATION_REQUIRED: formalization execution binding is missing"
        if current != binding:
            return "STALE_CLAIM_SNAPSHOT: formalization binding is not current"
        try:
            truth_store.validate_snapshot_for_execution(
                claim_snapshot,
                canonical_authority=run_state.get("canonical_authority") or [],
                replay_policy_hash=run_state.get("replay_policy_hash"),
            )
        except Exception as exc:
            return f"STALE_CLAIM_SNAPSHOT: {exc}"
        return True

    router = ModelRouter(
        config,
        state_path=run_path / "routing_state.json",
        runtime_backend=SQLiteRuntimeBackend(project.root),
        runtime_scope=f"{run_path.name}:formalization",
        execution_binding=binding,
        execution_binding_validator=validate_binding,
        require_execution_binding=True,
    )
    client = RoutedLLMClient(
        router,
        client_factory=create_client,
        default_role="formalization_agent",
        archive_dir=run_path / "archive" / "formalization_agent",
        working_dir=run_path / "gemini" / "formalization_agent",
    )
    prompt = f"""[Worker role: formalization_agent]
[Obligation ID: {target_id}]

Formalize the candidate below in Lean 4. Use lean_search before guessing names
when useful, then call lean_verify on the complete source. Return exactly one
JSON object matching the supplied response schema.

Set status to VERIFIED only after lean_verify returns OK for the exact
lean_code. Set status to FAILED when the formal statement or proof is
mathematically blocked. Set status to PENDING_FORMALIZATION when a Lean
project or required declaration is unavailable. Never use sorry, axiom,
unsafe, set_option, or native_decide.

# Theorem
{json.dumps(theorem, ensure_ascii=False, indent=2)}

# Exact truth identity
ClaimSnapshot: {claim_snapshot.claim_snapshot_hash}
AssertionIdentity: {claim_snapshot.assertion_identity_hash}

# Authorized context
{context}

# Natural-language candidate
{candidate}
"""
    system = (
        "You are the compiler-backed formalization agent. The compiler result "
        "is authoritative for compilation, while the research audit remains "
        "authoritative for theorem scope. Return only the typed JSON object."
    )
    try:
        response = client.call(
            prompt,
            system,
            label="formalization_agent",
            archive_path=formal_dir / "formalization_call.md",
            response_schema=FormalizationResultSchema,
        )
        typed = parse_structured_response(response, FormalizationResultSchema).model_dump(
            mode="python"
        )
    except Exception as exc:
        result = {
            "schema_version": 3,
            "status": "PENDING_FORMALIZATION",
            "theorem_id": target_id,
            "claim_snapshot_hash": claim_snapshot.claim_snapshot_hash,
            "assertion_identity_hash": claim_snapshot.assertion_identity_hash,
            "summary": "Formalization did not produce a typed result.",
            "error": str(exc),
            "created_at": utc_now(),
        }
        _write_json(formal_dir / "formal_status.json", result)
        _write_json(project.root / "formal_status.json", result)
        return result
    finally:
        client.cleanup()

    trace = response.get("tool_trace", [])
    verified = next(
        (
            item
            for item in reversed(trace)
            if item.get("tool_name") == "lean_verify"
            and isinstance(item.get("result"), dict)
            and item["result"].get("status") == "OK"
        ),
        None,
    )
    if verified:
        lean_code = str(verified.get("args", {}).get("code", ""))
        if lean_code and "sorry" not in lean_code.casefold():
            proof_path = formal_dir / "PROOF.lean"
            proof_path.write_text(lean_code, encoding="utf-8")
            digest = hashlib.sha256(lean_code.encode("utf-8")).hexdigest()
            result = {
                **typed,
                "schema_version": 3,
                "status": "VERIFIED",
                "theorem_id": target_id,
                "claim_snapshot_hash": claim_snapshot.claim_snapshot_hash,
                "assertion_identity_hash": claim_snapshot.assertion_identity_hash,
                "lean_code": lean_code,
                "certificate_path": str(proof_path.relative_to(run_path)),
                "certificate_sha256": digest,
                "compiler_output": str(verified.get("result", {}).get("output", "")),
                "created_at": utc_now(),
            }
        else:
            result = {
                **typed,
                "schema_version": 3,
                "status": "FAILED",
                "theorem_id": target_id,
                "claim_snapshot_hash": claim_snapshot.claim_snapshot_hash,
                "assertion_identity_hash": claim_snapshot.assertion_identity_hash,
                "error": ("Compiler tool returned OK for an empty or forbidden source"),
                "created_at": utc_now(),
            }
    elif typed["status"] == "VERIFIED":
        result = {
            **typed,
            "schema_version": 3,
            "status": "PENDING_FORMALIZATION",
            "theorem_id": target_id,
            "claim_snapshot_hash": claim_snapshot.claim_snapshot_hash,
            "assertion_identity_hash": claim_snapshot.assertion_identity_hash,
            "error": ("Model claimed VERIFIED without an observed successful lean_verify call"),
            "created_at": utc_now(),
        }
    else:
        result = {
            **typed,
            "schema_version": 3,
            "theorem_id": target_id,
            "claim_snapshot_hash": claim_snapshot.claim_snapshot_hash,
            "assertion_identity_hash": claim_snapshot.assertion_identity_hash,
            "created_at": utc_now(),
        }
    _write_json(formal_dir / "formal_status.json", result)
    _write_json(project.root / "formal_status.json", result)
    return result
