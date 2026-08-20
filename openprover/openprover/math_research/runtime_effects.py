"""Adapters that keep semantic ownership in the Phase 3/4/5 domain stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .runtime_backend import SQLiteRuntimeBackend
from .runtime_model import FaultInjector


class RuntimeEffectCoordinator:
    """Bind accepted runtime results to recoverable, exactly-once domain sagas."""

    def __init__(self, backend: SQLiteRuntimeBackend):
        self.backend = backend

    def apply_research_session_closure(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        research_store,
        tactical_session_id: str,
        recorded_by: str,
        fault_injector: FaultInjector | None = None,
    ):
        closure = research_store.load_session_closure(tactical_session_id)

        def recover(_slot_id: str):
            current = research_store.load_current_map(closure.research_map_id)
            ref = current.obligation_ref(closure.obligation_id)
            if ref.disposition != "RESOLVED":
                return None
            disposition = research_store.load_disposition(ref.disposition_hash)
            if disposition.resolution_basis != f"SessionClosure {closure.session_closure_id}":
                return None
            decision, resolved_map = research_store.resolve_session_closure(
                tactical_session_id, recorded_by=recorded_by
            )
            return {
                "decision": decision,
                "research_map": resolved_map,
                "decision_hash": decision.decision_hash,
                "research_map_hash": resolved_map.research_map_hash,
            }

        def apply(_slot_id: str):
            decision, revised = research_store.resolve_session_closure(
                tactical_session_id, recorded_by=recorded_by
            )
            return {
                "decision": decision,
                "research_map": revised,
                "decision_hash": decision.decision_hash,
                "research_map_hash": revised.research_map_hash if revised else None,
            }

        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind="APPLY_SESSION_CLOSURE",
            semantic_target_type="RESEARCH_OBLIGATION",
            semantic_target_id=closure.obligation_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            claim_snapshot_hash=closure.root_claim_snapshot_hash,
            fault_injector=fault_injector,
        )

    def commit_architecture_review(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        governance_controller,
        review,
        fault_injector: FaultInjector | None = None,
    ):
        def recover(_slot_id: str):
            clock = governance_controller.ensure_clock(review.research_map_id)
            if clock.last_review_id != review.review_id or clock.last_review_hash != review.review_hash:
                return None
            return {"clock": clock, "clock_hash": clock.clock_hash, "review_id": review.review_id}

        def apply(_slot_id: str):
            clock = governance_controller.commit_review(review)
            return {"clock": clock, "clock_hash": clock.clock_hash, "review_id": review.review_id}

        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind="COMMIT_ARCHITECTURE_REVIEW",
            semantic_target_type="RESEARCH_MAP",
            semantic_target_id=review.research_map_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            claim_snapshot_hash=review.root_claim_snapshot_hash,
            fault_injector=fault_injector,
        )

    def apply_architecture_patch(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        governance_controller,
        authorization_id: str,
        applied_by: str = "GovernanceController",
        fault_injector: FaultInjector | None = None,
    ):
        authorization = governance_controller.load_authorization(authorization_id)
        patch = governance_controller.load_patch(authorization.patch_id)

        def recover(_slot_id: str):
            for path in sorted(governance_controller.applications_root.glob("*.json")):
                value = governance_controller.load_application(path.stem)
                if value.authorization_id == authorization_id:
                    target = governance_controller.research_store.load_map(value.target_map_hash)
                    return {"research_map": target, "application": value}
            return None

        def apply(_slot_id: str):
            target, application = governance_controller.apply_authorized_patch(
                authorization_id, applied_by=applied_by
            )
            return {"research_map": target, "application": application}

        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind="APPLY_ARCHITECTURE_PATCH",
            semantic_target_type="RESEARCH_MAP",
            semantic_target_id=patch.source_map_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            claim_snapshot_hash=patch.root_claim_snapshot_hash,
            fault_injector=fault_injector,
        )

    def apply_truth_mutation(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        theorem_id: str,
        claim_snapshot_hash: str,
        apply: Callable[[str], Any],
        receipt_path: Callable[[str], Path],
        mutation_id: str,
        load_receipt: Callable[[str], Any],
        fault_injector: FaultInjector | None = None,
    ):
        def recover(_slot_id: str):
            if not receipt_path(mutation_id).is_file():
                return None
            return {"receipt": load_receipt(mutation_id), "mutation_id": mutation_id}

        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind="APPLY_TRUTH_MUTATION",
            semantic_target_type="THEOREM",
            semantic_target_id=theorem_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            claim_snapshot_hash=claim_snapshot_hash,
            fault_injector=fault_injector,
        )
