"""Adapters that keep semantic ownership in the Phase 3/4/5 domain stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .runtime_backend import SQLiteRuntimeBackend
from .runtime_bindings import CrossPlaneExecutionBinding
from .runtime_dispatch import DurableProviderDispatcher
from .runtime_model import FaultInjector


class RuntimeEffectCoordinator:
    """Bind accepted runtime results to recoverable, exactly-once domain sagas."""

    def __init__(self, backend: SQLiteRuntimeBackend):
        self.backend = backend

    def register_semantic_result(
        self,
        *,
        idempotency_key: str,
        semantic_target: str,
        payload: Mapping[str, Any],
        binding: CrossPlaneExecutionBinding,
        binding_validator=None,
    ) -> dict[str, Any]:
        """Create one durable accepted internal result for a domain effect."""

        job = self.backend.create_logical_job(
            job_kind="SEMANTIC_EFFECT",
            semantic_target=semantic_target,
            idempotency_key=idempotency_key,
            execution_binding=binding,
            actor="runtime-effect-coordinator",
        )
        if job["accepted_result_id"] is not None:
            return next(
                row
                for row in self.backend.list_rows("attempt_results")
                if row["result_id"] == job["accepted_result_id"]
            )
        response = DurableProviderDispatcher(
            self.backend, owner="runtime-effect-coordinator"
        ).execute(
            logical_job_id=job["logical_job_id"],
            provider="internal",
            model="semantic-effect",
            reasoning_tier="routine",
            payload=dict(payload),
            invoke=lambda: dict(payload),
            execution_binding=binding,
            binding_validator=binding_validator,
        )
        return next(
            row
            for row in self.backend.list_rows("attempt_results")
            if row["result_id"] == response["runtime"]["result_id"]
        )

    def apply_domain_effect(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        effect_kind: str,
        semantic_target_type: str,
        semantic_target_id: str,
        apply: Callable[[str], Any],
        recover: Callable[[str], Any | None] | None = None,
        binding: CrossPlaneExecutionBinding | None = None,
        binding_validator=None,
        fault_injector: FaultInjector | None = None,
    ):
        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind=effect_kind,
            semantic_target_type=semantic_target_type,
            semantic_target_id=semantic_target_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            execution_binding=binding,
            binding_validator=binding_validator,
            fault_injector=fault_injector,
        )

    def apply_structural_effect(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        governance_controller,
        effect,
        fault_injector: FaultInjector | None = None,
    ):
        """Commit one typed StructuralEffect through an exactly-once slot."""

        binding = CrossPlaneExecutionBinding.capture(
            root_claim_snapshot_hash=effect.root_claim_snapshot_hash,
            research_map_id=effect.research_map_id,
            research_map_version=effect.research_map_version,
            research_map_hash=effect.research_map_hash,
            governance_object_type="STRUCTURAL_EFFECT",
            governance_object_id=effect.structural_effect_id,
            governance_source_hash=effect.structural_effect_hash,
        )

        def recover(_slot_id: str):
            path = governance_controller.effects_root / f"{effect.structural_effect_id}.json"
            if not path.is_file():
                return None
            existing = governance_controller.load_effect(effect.structural_effect_id)
            if existing != effect:
                raise RuntimeError("StructuralEffect recovery identity mismatch")
            clock = governance_controller.ensure_clock(effect.research_map_id)
            return {
                "structural_effect": existing,
                "clock": clock,
                "clock_hash": clock.clock_hash,
                "effect_artifact_ref": existing.structural_effect_id,
            }

        def apply(_slot_id: str):
            clock = governance_controller.record_effect(effect)
            return {
                "structural_effect": effect,
                "clock": clock,
                "clock_hash": clock.clock_hash,
                "effect_artifact_ref": effect.structural_effect_id,
            }

        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind="COMMIT_STRUCTURAL_EFFECT",
            semantic_target_type="RESEARCH_MAP",
            semantic_target_id=effect.research_map_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            claim_snapshot_hash=effect.root_claim_snapshot_hash,
            execution_binding=binding,
            binding_validator=lambda current: self._map_binding_is_current(
                governance_controller.research_store, current
            ),
            fault_injector=fault_injector,
        )

    def record_governance_session(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        governance_controller,
        research_map_id: str,
        successor_execution: bool,
        root_obstruction_unchanged: bool,
        blocked_obligation_ids: tuple[str, ...],
        tactical_session_id: str,
        root_claim_snapshot_hash: str,
        fault_injector: FaultInjector | None = None,
    ):
        current = governance_controller.research_store.load_current_map(research_map_id)
        binding = CrossPlaneExecutionBinding.capture(
            root_claim_snapshot_hash=root_claim_snapshot_hash,
            research_map_id=current.research_map_id,
            research_map_version=current.version,
            research_map_hash=current.research_map_hash,
            tactical_session_id=tactical_session_id,
        )

        def recover(_slot_id: str):
            path = governance_controller.sessions_root / f"{tactical_session_id}.json"
            if not path.is_file():
                return None
            clock = governance_controller.load_clock(research_map_id)
            return {"clock": clock, "clock_hash": clock.clock_hash}

        def apply(_slot_id: str):
            clock = governance_controller.record_session(
                research_map_id,
                successor_execution=successor_execution,
                root_obstruction_unchanged=root_obstruction_unchanged,
                blocked_obligation_ids=blocked_obligation_ids,
                tactical_session_id=tactical_session_id,
            )
            return {"clock": clock, "clock_hash": clock.clock_hash}

        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind="RECORD_GOVERNANCE_SESSION",
            semantic_target_type="TACTICAL_SESSION",
            semantic_target_id=tactical_session_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            claim_snapshot_hash=root_claim_snapshot_hash,
            execution_binding=binding,
            binding_validator=lambda current: self._map_binding_is_current(
                governance_controller.research_store, current
            ),
            fault_injector=fault_injector,
        )

    def signal_governance_review(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        governance_controller,
        research_map_id: str,
        trigger: str,
        root_claim_snapshot_hash: str,
        fault_injector: FaultInjector | None = None,
    ):
        current = governance_controller.research_store.load_current_map(research_map_id)
        binding = CrossPlaneExecutionBinding.capture(
            root_claim_snapshot_hash=root_claim_snapshot_hash,
            research_map_id=current.research_map_id,
            research_map_version=current.version,
            research_map_hash=current.research_map_hash,
        )

        def recover(_slot_id: str):
            clock = governance_controller.load_clock(research_map_id)
            if trigger not in clock.explicit_signals:
                return None
            return {"clock": clock, "clock_hash": clock.clock_hash}

        def apply(_slot_id: str):
            clock = governance_controller.signal_review(research_map_id, trigger)
            return {"clock": clock, "clock_hash": clock.clock_hash}

        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind="SIGNAL_GOVERNANCE_REVIEW",
            semantic_target_type="RESEARCH_MAP",
            semantic_target_id=research_map_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            claim_snapshot_hash=root_claim_snapshot_hash,
            execution_binding=binding,
            binding_validator=lambda current: self._map_binding_is_current(
                governance_controller.research_store, current
            ),
            fault_injector=fault_injector,
        )

    def record_governance_route_failure(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        governance_controller,
        research_map_id: str,
        obligation_id: str,
        root_claim_snapshot_hash: str,
        fault_injector: FaultInjector | None = None,
    ):
        current = governance_controller.research_store.load_current_map(research_map_id)
        binding = CrossPlaneExecutionBinding.capture(
            root_claim_snapshot_hash=root_claim_snapshot_hash,
            research_map_id=current.research_map_id,
            research_map_version=current.version,
            research_map_hash=current.research_map_hash,
        )

        def recover(_slot_id: str):
            current_map = governance_controller.research_store.load_current_map(research_map_id)
            if not current_map.route_failure_refs:
                return None
            clock = governance_controller.load_clock(research_map_id)
            return {"clock": clock, "clock_hash": clock.clock_hash}

        def apply(_slot_id: str):
            clock = governance_controller.record_route_failure(
                research_map_id, obligation_id=obligation_id
            )
            return {"clock": clock, "clock_hash": clock.clock_hash}

        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind="RECORD_GOVERNANCE_ROUTE_FAILURE",
            semantic_target_type="RESEARCH_OBLIGATION",
            semantic_target_id=obligation_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            claim_snapshot_hash=root_claim_snapshot_hash,
            execution_binding=binding,
            binding_validator=lambda current: self._map_binding_is_current(
                governance_controller.research_store, current
            ),
            fault_injector=fault_injector,
        )

    @staticmethod
    def _map_binding_is_current(
        research_store, binding: CrossPlaneExecutionBinding | None
    ) -> bool | str:
        if binding is None or binding.research_map_id is None:
            return "REVALIDATION_REQUIRED: ResearchMap binding is missing"
        try:
            current = research_store.load_current_map(binding.research_map_id)
        except Exception as exc:
            return f"REVALIDATION_REQUIRED: current ResearchMap unavailable: {exc}"
        if (
            current.research_map_id != binding.research_map_id
            or current.version != binding.research_map_version
            or current.research_map_hash != binding.research_map_hash
            or current.root_claim_snapshot_hash != binding.root_claim_snapshot_hash
        ):
            return "STALE_RESEARCH_MAP: exact current map identity is required"
        return True

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
        binding = CrossPlaneExecutionBinding.capture(
            root_claim_snapshot_hash=closure.root_claim_snapshot_hash,
            research_map_id=closure.research_map_id,
            research_map_version=closure.research_map_version,
            research_map_hash=closure.research_map_hash,
            research_obligation_id=closure.obligation_id,
            directive_id=closure.directive_id,
            tactical_session_id=closure.tactical_session_id,
        )

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
            execution_binding=binding,
            binding_validator=lambda current: self._map_binding_is_current(research_store, current),
            fault_injector=fault_injector,
        )

    def apply_research_route_failure(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        research_store,
        research_map_id: str,
        obligation_id: str,
        route_description: str,
        method_family: str,
        exact_failure_condition: str,
        failure_domain: str,
        evidence_refs: tuple[str, ...],
        reopen_conditions: tuple[str, ...],
        created_by: str,
        binding: CrossPlaneExecutionBinding,
        fault_injector: FaultInjector | None = None,
    ):
        expected_version = binding.research_map_version
        expected_hash = binding.research_map_hash

        def recover(_slot_id: str):
            for path in sorted(research_store.route_failures_root.glob("*.json")):
                try:
                    record = research_store.load_route_failure(path.stem)
                except Exception:
                    continue
                if (
                    record.research_map_id == research_map_id
                    and record.research_map_version == expected_version
                    and record.research_map_hash == expected_hash
                    and record.obligation_id == obligation_id
                    and record.route_description == route_description
                    and record.method_family == method_family
                    and record.exact_failure_condition == exact_failure_condition
                    and record.failure_domain == failure_domain
                    and record.evidence_refs == evidence_refs
                    and record.reopen_conditions == reopen_conditions
                    and record.created_by == created_by
                ):
                    revised = research_store.load_current_map(research_map_id)
                    return {"route_failure": record, "research_map": revised}
            return None

        def apply(_slot_id: str):
            record, revised = research_store.record_route_failure(
                research_map_id,
                obligation_id,
                route_description=route_description,
                method_family=method_family,
                exact_failure_condition=exact_failure_condition,
                failure_domain=failure_domain,
                evidence_refs=evidence_refs,
                reopen_conditions=reopen_conditions,
                created_by=created_by,
            )
            return {"route_failure": record, "research_map": revised}

        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind="RECORD_RESEARCH_ROUTE_FAILURE",
            semantic_target_type="RESEARCH_OBLIGATION",
            semantic_target_id=obligation_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            claim_snapshot_hash=binding.root_claim_snapshot_hash,
            execution_binding=binding,
            binding_validator=lambda current_binding: self._map_binding_is_current(
                research_store, current_binding
            ),
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
        binding = CrossPlaneExecutionBinding.capture(
            root_claim_snapshot_hash=review.root_claim_snapshot_hash,
            research_map_id=review.research_map_id,
            research_map_version=review.research_map_version,
            research_map_hash=review.research_map_hash,
            governance_object_type="ARCHITECTURE_REVIEW",
            governance_object_id=review.review_id,
            governance_source_hash=review.review_hash,
        )

        def recover(_slot_id: str):
            clock = governance_controller.ensure_clock(review.research_map_id)
            if (
                clock.last_review_id != review.review_id
                or clock.last_review_hash != review.review_hash
            ):
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
            execution_binding=binding,
            binding_validator=lambda current: self._map_binding_is_current(
                governance_controller.research_store, current
            ),
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
        binding = CrossPlaneExecutionBinding.capture(
            root_claim_snapshot_hash=patch.root_claim_snapshot_hash,
            research_map_id=patch.source_map_id,
            research_map_version=patch.source_map_version,
            research_map_hash=patch.source_map_hash,
            governance_object_type="ARCHITECTURE_PATCH",
            governance_object_id=patch.patch_id,
            governance_source_hash=patch.patch_hash,
        )

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
            execution_binding=binding,
            binding_validator=lambda current: self._map_binding_is_current(
                governance_controller.research_store, current
            ),
            fault_injector=fault_injector,
        )

    def apply_truth_transition(
        self,
        *,
        logical_job_id: str,
        source_result_id: str,
        truth_store,
        theorem_id: str,
        claim_snapshot,
        gate,
        actor: str,
        reason: str,
        audit_artifacts,
        metadata_updates: Mapping[str, Any] | None = None,
        canonical_authority=(),
        replay_policy_hash: str | None = None,
        trust_policy_context: Mapping[str, Any] | None = None,
        fault_injector: FaultInjector | None = None,
    ):
        intent = truth_store.build_mutation_intent(
            theorem_id=theorem_id,
            snapshot=claim_snapshot,
            gate=gate,
            actor=actor,
            reason=reason,
            audit_artifacts=audit_artifacts,
        )
        binding = CrossPlaneExecutionBinding.capture(
            root_claim_snapshot_hash=claim_snapshot.claim_snapshot_hash,
        )

        def recover(_slot_id: str):
            if not truth_store.receipt_path(intent.mutation_id).is_file():
                return None
            receipt = truth_store.load_mutation_receipt(intent.mutation_id)
            return {
                "theorem": truth_store.project.load_theorem(theorem_id),
                "snapshot": truth_store.load_claim_snapshot(receipt.resulting_claim_snapshot_hash),
                "intent": truth_store.load_mutation_intent(intent.mutation_id),
                "receipt": receipt,
                "mutation_id": intent.mutation_id,
            }

        def apply(_slot_id: str):
            theorem, snapshot, applied_intent, receipt = truth_store.compare_and_transition(
                theorem_id,
                claim_snapshot=claim_snapshot,
                gate=gate,
                actor=actor,
                reason=reason,
                audit_artifacts=audit_artifacts,
                metadata_updates=metadata_updates,
                canonical_authority=canonical_authority,
                replay_policy_hash=replay_policy_hash,
                trust_policy_context=trust_policy_context,
            )
            return {
                "theorem": theorem,
                "snapshot": snapshot,
                "intent": applied_intent,
                "receipt": receipt,
                "mutation_id": applied_intent.mutation_id,
            }

        return self.backend.apply_effect_once(
            logical_job_id=logical_job_id,
            effect_kind="APPLY_TRUTH_MUTATION",
            semantic_target_type="THEOREM",
            semantic_target_id=theorem_id,
            source_result_id=source_result_id,
            apply=apply,
            recover=recover,
            claim_snapshot_hash=claim_snapshot.claim_snapshot_hash,
            execution_binding=binding,
            binding_validator=lambda current: (
                True
                if current is not None
                and current.root_claim_snapshot_hash == claim_snapshot.claim_snapshot_hash
                else "STALE_CLAIM_SNAPSHOT: Truth mutation requires exact audited snapshot"
            ),
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
