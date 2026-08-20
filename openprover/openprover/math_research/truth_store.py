"""Thin production Truth Plane facade over the existing ProjectStore.

The facade owns typed capture and stale validation.  ProjectStore remains the
filesystem/status-machine implementation; no Research Plane or runtime-control
semantics belong here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .claim_snapshot import ClaimSnapshot, SnapshotComparison, compare_claim_snapshots
from .project import ProjectError, ProjectStore, utc_now
from .state_machine import AuditGate
from .trust_kernel import TrustKernel
from .truth_identity import (
    AssertionIdentity,
    AssumptionSnapshot,
    AuthorityBinding,
    DependencyEntry,
    DependencySnapshot,
    domain_hash,
    project_record_hash,
    source_artifact_sha256,
    trust_policy_fingerprint,
)
from .truth_mutation import (
    TruthMutationIntent,
    TruthMutationReceipt,
    capture_artifact_refs,
)


IDENTITY_CRITICAL_THEOREM_FIELDS = {
    "id",
    "statement",
    "claim_type",
    "notation_scope",
}
CLAIM_SNAPSHOT_SCHEMA_VERSION = 1


class TruthValidationError(ProjectError):
    """A truth-sensitive action tried to use a stale or unknown snapshot."""

    def __init__(self, operation: str, comparison: SnapshotComparison):
        self.operation = operation
        self.comparison = comparison
        super().__init__(
            f"{operation} rejected claim snapshot: {comparison.status} [{comparison.disposition}]"
        )


class TruthMutationBlocked(ProjectError):
    """A persisted mutation intent failed its final compare-and-transition."""

    def __init__(self, message: str, *, mutation_id: str, blocked_path: Path):
        self.mutation_id = mutation_id
        self.blocked_path = blocked_path
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CurrentTruth:
    theorem: dict[str, Any]
    claim_snapshot: ClaimSnapshot


class TruthStoreFacade:
    """Only PHASE 3 entry point for new truth-sensitive production paths."""

    def __init__(self, project: ProjectStore):
        self.project = project
        self.truth_root = project.root / "truth"
        self.snapshot_dir = self.truth_root / "claim_snapshots"
        self.mutation_root = self.truth_root / "mutations"
        self.intent_dir = self.mutation_root / "intents"
        self.prepared_dir = self.mutation_root / "prepared"
        self.receipt_dir = self.mutation_root / "receipts"
        self.blocked_dir = self.mutation_root / "blocked"

    def capture_assertion_identity(self, theorem_id: str) -> AssertionIdentity:
        theorem = self.project.load_theorem(theorem_id)
        return self._assertion_from_record(theorem, assertion_kind="PROJECT_THEOREM")

    def capture_claim_snapshot(
        self,
        theorem_id: str,
        *,
        canonical_authority: Iterable[Mapping[str, Any]] = (),
        replay_policy_hash: str | None = None,
        trust_policy_context: Mapping[str, Any] | None = None,
        persist: bool = True,
    ) -> ClaimSnapshot:
        snapshot = self._build_claim_snapshot(
            theorem_id,
            canonical_authority=canonical_authority,
            replay_policy_hash=replay_policy_hash,
            trust_policy_context=trust_policy_context,
        )
        if not persist:
            return snapshot
        path = self.claim_snapshot_path(snapshot.claim_snapshot_hash)
        if path.exists():
            existing = self.load_claim_snapshot(snapshot.claim_snapshot_hash)
            if not self._same_truth_identity(existing, snapshot):
                raise ProjectError(
                    "ClaimSnapshot content-address collision across truth identity fields"
                )
            return existing
        self._write_immutable_json(path, snapshot.to_dict())
        return snapshot

    def load_current_truth(
        self,
        theorem_id: str,
        *,
        canonical_authority: Iterable[Mapping[str, Any]] = (),
        replay_policy_hash: str | None = None,
        trust_policy_context: Mapping[str, Any] | None = None,
    ) -> CurrentTruth:
        theorem = self.project.load_theorem(theorem_id)
        snapshot = self.capture_claim_snapshot(
            theorem_id,
            canonical_authority=canonical_authority,
            replay_policy_hash=replay_policy_hash,
            trust_policy_context=trust_policy_context,
            persist=False,
        )
        return CurrentTruth(theorem=theorem, claim_snapshot=snapshot)

    def load_claim_snapshot(self, claim_snapshot_hash: str) -> ClaimSnapshot:
        path = self.claim_snapshot_path(claim_snapshot_hash)
        if not path.is_file():
            raise ProjectError(f"ClaimSnapshot not found: {claim_snapshot_hash}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProjectError(f"Invalid ClaimSnapshot JSON: {path}") from exc
        if not isinstance(value, dict):
            raise ProjectError("ClaimSnapshot artifact root must be an object")
        snapshot = ClaimSnapshot.from_dict(value)
        if snapshot.claim_snapshot_hash != claim_snapshot_hash:
            raise ProjectError("ClaimSnapshot filename/hash mismatch")
        return snapshot

    def compare_claim_snapshot(
        self,
        stored: str | ClaimSnapshot,
        *,
        canonical_authority: Iterable[Mapping[str, Any]] = (),
        replay_policy_hash: str | None = None,
        trust_policy_context: Mapping[str, Any] | None = None,
    ) -> SnapshotComparison:
        snapshot = self.load_claim_snapshot(stored) if isinstance(stored, str) else stored
        if snapshot.theorem_id.strip() == "":
            return compare_claim_snapshots(snapshot, None)
        try:
            current = self.capture_claim_snapshot(
                snapshot.theorem_id,
                canonical_authority=canonical_authority,
                replay_policy_hash=replay_policy_hash,
                trust_policy_context=trust_policy_context,
                persist=False,
            )
        except (ProjectError, OSError, ValueError):
            current = None
        return compare_claim_snapshots(snapshot, current)

    def validate_snapshot_for_execution(self, *args, **kwargs) -> SnapshotComparison:
        return self._validate("EXECUTION", *args, **kwargs)

    def validate_snapshot_for_audit(self, *args, **kwargs) -> SnapshotComparison:
        return self._validate("AUDIT", *args, **kwargs)

    def validate_snapshot_for_promotion(self, *args, **kwargs) -> SnapshotComparison:
        return self._validate("PROMOTION", *args, **kwargs)

    def update_metadata(self, theorem_id: str, updates: Mapping[str, Any]) -> dict[str, Any]:
        """Compatibility metadata update that cannot change assertion identity."""

        forbidden = IDENTITY_CRITICAL_THEOREM_FIELDS.intersection(updates)
        if forbidden:
            raise ProjectError(
                "Identity-critical theorem fields require a new human-approved claim identity: "
                + ", ".join(sorted(forbidden))
            )
        theorem = self.project.load_theorem(theorem_id)
        theorem.update(dict(updates))
        self.project.update_theorem(theorem)
        return self.project.load_theorem(theorem_id)

    def transition_status(
        self,
        theorem_id: str,
        new_status: str,
        *,
        claim_snapshot: str | ClaimSnapshot,
        actor: str,
        reason: str,
        canonical_authority: Iterable[Mapping[str, Any]] = (),
        replay_policy_hash: str | None = None,
        trust_policy_context: Mapping[str, Any] | None = None,
        gate: AuditGate | None = None,
        audit_status: str | None = None,
    ) -> tuple[dict[str, Any], ClaimSnapshot]:
        """Validate the old snapshot, apply one lifecycle transition, recapture."""

        snapshot = (
            self.load_claim_snapshot(claim_snapshot)
            if isinstance(claim_snapshot, str)
            else claim_snapshot
        )
        if snapshot.theorem_id != theorem_id:
            raise ProjectError("Truth transition snapshot belongs to a different theorem")
        self.validate_snapshot_for_execution(
            snapshot,
            canonical_authority=canonical_authority,
            replay_policy_hash=replay_policy_hash,
            trust_policy_context=trust_policy_context,
        )
        theorem = self.project.transition(
            theorem_id,
            new_status,
            actor=actor,
            reason=reason,
            gate=gate,
            audit_status=audit_status,
        )
        refreshed = self.capture_claim_snapshot(
            theorem_id,
            canonical_authority=canonical_authority,
            replay_policy_hash=replay_policy_hash,
            trust_policy_context=trust_policy_context,
        )
        return theorem, refreshed

    def compare_and_transition(
        self,
        theorem_id: str,
        *,
        claim_snapshot: str | ClaimSnapshot,
        gate: AuditGate,
        actor: str,
        reason: str,
        audit_artifacts: Iterable[str | Path],
        metadata_updates: Mapping[str, Any] | None = None,
        canonical_authority: Iterable[Mapping[str, Any]] = (),
        replay_policy_hash: str | None = None,
        trust_policy_context: Mapping[str, Any] | None = None,
        before_compare_hook: Callable[[], None] | None = None,
        after_prepare_hook: Callable[[], None] | None = None,
        after_transition_hook: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ClaimSnapshot, TruthMutationIntent, TruthMutationReceipt]:
        """Promote one exactly audited snapshot through an intent-first serialized CAS."""

        snapshot = (
            self.load_claim_snapshot(claim_snapshot)
            if isinstance(claim_snapshot, str)
            else claim_snapshot
        )
        if snapshot.theorem_id != theorem_id:
            raise ProjectError("Truth mutation snapshot belongs to a different theorem")
        if not gate.passed:
            raise ProjectError("Truth mutation requires a passing audit gate")
        if gate.audited_claim_snapshot_hash != snapshot.claim_snapshot_hash:
            raise ProjectError("Final audit is not bound to the exact promotion ClaimSnapshot")
        artifact_refs = capture_artifact_refs(audit_artifacts, project_root=self.project.root)
        intent = TruthMutationIntent.capture(
            theorem_id=theorem_id,
            from_status=snapshot.captured_status,
            requested_to_status="PROVED",
            claim_snapshot_hash=snapshot.claim_snapshot_hash,
            assertion_identity_hash=snapshot.assertion_identity_hash,
            audited_claim_snapshot_hash=gate.audited_claim_snapshot_hash,
            trust_policy_fingerprint=snapshot.trust_policy_fingerprint,
            audit_artifacts=artifact_refs,
            requested_by=actor,
            reason=reason,
            created_at=utc_now(),
        )
        intent_path = self.intent_path(intent.mutation_id)
        if intent_path.exists():
            intent = self.load_mutation_intent(intent.mutation_id)
        else:
            self._write_immutable_json(intent_path, intent.to_dict())
        receipt_path = self.receipt_path(intent.mutation_id)
        if receipt_path.exists():
            receipt = self.load_mutation_receipt(intent.mutation_id)
            theorem = self.project.load_theorem(theorem_id)
            resulting_snapshot = self.load_claim_snapshot(receipt.resulting_claim_snapshot_hash)
            return theorem, resulting_snapshot, intent, receipt
        recovered = self._recover_prepared_mutation(
            intent,
            snapshot=snapshot,
            metadata_updates=metadata_updates,
            canonical_authority=canonical_authority,
            replay_policy_hash=replay_policy_hash,
            trust_policy_context=trust_policy_context,
        )
        if recovered is not None:
            return recovered
        if before_compare_hook is not None:
            before_compare_hook()

        with self.project.truth_transaction():
            comparison = self.compare_claim_snapshot(
                snapshot,
                canonical_authority=canonical_authority,
                replay_policy_hash=replay_policy_hash,
                trust_policy_context=trust_policy_context,
            )
            if not comparison.compatible:
                blocked_path = self._write_mutation_blocked(
                    intent,
                    status=comparison.status,
                    disposition=comparison.disposition,
                    reason=comparison.reason,
                    current_claim_snapshot_hash=comparison.current_claim_snapshot_hash,
                )
                raise TruthMutationBlocked(
                    f"Truth mutation blocked: {comparison.status} [{comparison.disposition}]",
                    mutation_id=intent.mutation_id,
                    blocked_path=blocked_path,
                )
            current_record = self.project.load_theorem(theorem_id)
            prepared_path = self.prepared_path(intent.mutation_id)
            if prepared_path.exists():
                prepared = self._read_truth_json(prepared_path, "TruthMutationPrepared")
                if project_record_hash(current_record) != prepared["project_record_hash_before"]:
                    raise ProjectError("Prepared truth mutation source changed before CAS")
            else:
                prepared = {
                    "schema_version": 1,
                    "object_type": "TRUTH_MUTATION_PREPARED",
                    "mutation_id": intent.mutation_id,
                    "theorem_id": theorem_id,
                    "from_status": snapshot.captured_status,
                    "requested_to_status": "PROVED",
                    "claim_snapshot_hash": snapshot.claim_snapshot_hash,
                    "assertion_identity_hash": snapshot.assertion_identity_hash,
                    "trust_policy_fingerprint": snapshot.trust_policy_fingerprint,
                    "project_record_hash_before": project_record_hash(current_record),
                    "actor": actor,
                    "reason": reason,
                    "metadata_updates": dict(metadata_updates or {}),
                    "prepared_at": utc_now(),
                }
                self._write_immutable_json(prepared_path, prepared)
            if after_prepare_hook is not None:
                after_prepare_hook()
            try:
                before, theorem = self.project.compare_and_transition(
                    theorem_id,
                    "PROVED",
                    expected_status=snapshot.captured_status,
                    expected_identity={
                        key: str(current_record.get(key) or "")
                        for key in ("id", "statement", "claim_type", "notation_scope")
                    },
                    actor=actor,
                    reason=reason,
                    gate=gate,
                    audit_status="PASS",
                    metadata_updates=dict(metadata_updates or {}),
                )
            except ProjectError as exc:
                blocked_path = self._write_mutation_blocked(
                    intent,
                    status="COMPARE_AND_TRANSITION_REJECTED",
                    disposition="BLOCKED",
                    reason=str(exc),
                    current_claim_snapshot_hash=None,
                )
                raise TruthMutationBlocked(
                    f"Truth mutation compare-and-transition failed: {exc}",
                    mutation_id=intent.mutation_id,
                    blocked_path=blocked_path,
                ) from exc
            resulting_snapshot = self.capture_claim_snapshot(
                theorem_id,
                canonical_authority=canonical_authority,
                replay_policy_hash=replay_policy_hash,
                trust_policy_context=trust_policy_context,
            )
            if after_transition_hook is not None:
                after_transition_hook()

        receipt = TruthMutationReceipt.capture(
            mutation_id=intent.mutation_id,
            theorem_id=theorem_id,
            previous_status=str(before["status"]),
            resulting_status=str(theorem["status"]),
            claim_snapshot_hash=snapshot.claim_snapshot_hash,
            resulting_claim_snapshot_hash=resulting_snapshot.claim_snapshot_hash,
            project_record_hash_before=str(prepared["project_record_hash_before"]),
            project_record_hash_after=project_record_hash(theorem),
            actor=actor,
            applied_at=utc_now(),
        )
        self._write_immutable_json(self.receipt_path(intent.mutation_id), receipt.to_dict())
        return theorem, resulting_snapshot, intent, receipt

    def claim_snapshot_path(self, claim_snapshot_hash: str) -> Path:
        digest = _digest_part(claim_snapshot_hash)
        return self.snapshot_dir / f"{digest}.json"

    def intent_path(self, mutation_id: str) -> Path:
        return self.intent_dir / f"{_digest_part(mutation_id)}.json"

    def receipt_path(self, mutation_id: str) -> Path:
        return self.receipt_dir / f"{_digest_part(mutation_id)}.json"

    def prepared_path(self, mutation_id: str) -> Path:
        return self.prepared_dir / f"{_digest_part(mutation_id)}.json"

    def load_mutation_intent(self, mutation_id: str) -> TruthMutationIntent:
        value = self._read_truth_json(self.intent_path(mutation_id), "TruthMutationIntent")
        intent = TruthMutationIntent.from_dict(value)
        if intent.mutation_id != mutation_id:
            raise ProjectError("TruthMutationIntent filename/hash mismatch")
        return intent

    def load_mutation_receipt(self, mutation_id: str) -> TruthMutationReceipt:
        value = self._read_truth_json(self.receipt_path(mutation_id), "TruthMutationReceipt")
        receipt = TruthMutationReceipt.from_dict(value)
        if receipt.mutation_id != mutation_id:
            raise ProjectError("TruthMutationReceipt filename/mutation mismatch")
        return receipt

    def _recover_prepared_mutation(
        self,
        intent: TruthMutationIntent,
        *,
        snapshot: ClaimSnapshot,
        metadata_updates: Mapping[str, Any] | None,
        canonical_authority: Iterable[Mapping[str, Any]],
        replay_policy_hash: str | None,
        trust_policy_context: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], ClaimSnapshot, TruthMutationIntent, TruthMutationReceipt] | None:
        """Close the theorem-write/receipt-write crash window from durable evidence."""

        path = self.prepared_path(intent.mutation_id)
        if not path.exists():
            return None
        prepared = self._read_truth_json(path, "TruthMutationPrepared")
        expected_keys = {
            "schema_version",
            "object_type",
            "mutation_id",
            "theorem_id",
            "from_status",
            "requested_to_status",
            "claim_snapshot_hash",
            "assertion_identity_hash",
            "trust_policy_fingerprint",
            "project_record_hash_before",
            "actor",
            "reason",
            "metadata_updates",
            "prepared_at",
        }
        if set(prepared) != expected_keys:
            raise ProjectError("TruthMutationPrepared fields do not match schema 1")
        expected = {
            "schema_version": 1,
            "object_type": "TRUTH_MUTATION_PREPARED",
            "mutation_id": intent.mutation_id,
            "theorem_id": intent.theorem_id,
            "from_status": intent.from_status,
            "requested_to_status": intent.requested_to_status,
            "claim_snapshot_hash": intent.claim_snapshot_hash,
            "assertion_identity_hash": intent.assertion_identity_hash,
            "trust_policy_fingerprint": intent.trust_policy_fingerprint,
            "actor": intent.requested_by,
            "reason": intent.reason,
            "metadata_updates": dict(metadata_updates or {}),
        }
        mismatches = [key for key, value in expected.items() if prepared.get(key) != value]
        if mismatches:
            raise ProjectError(
                "TruthMutationPrepared does not match replay request: "
                + ", ".join(sorted(mismatches))
            )
        theorem = self.project.load_theorem(intent.theorem_id)
        if theorem.get("status") == intent.from_status:
            if project_record_hash(theorem) != prepared["project_record_hash_before"]:
                raise ProjectError("Prepared truth mutation source record changed before replay")
            return None
        history = theorem.get("status_history")
        transition = history[-1] if isinstance(history, list) and history else None
        if (
            theorem.get("status") != intent.requested_to_status
            or not isinstance(transition, dict)
            or transition.get("from") != intent.from_status
            or transition.get("to") != intent.requested_to_status
            or transition.get("actor") != intent.requested_by
            or transition.get("reason") != intent.reason
            or theorem.get("last_updated") != transition.get("at")
        ):
            raise ProjectError("Prepared truth mutation cannot be recovered from current theorem")
        if any(theorem.get(key) != value for key, value in dict(metadata_updates or {}).items()):
            raise ProjectError("Prepared truth mutation metadata does not match current theorem")
        resulting_snapshot = self.capture_claim_snapshot(
            intent.theorem_id,
            canonical_authority=canonical_authority,
            replay_policy_hash=replay_policy_hash,
            trust_policy_context=trust_policy_context,
        )
        if (
            resulting_snapshot.assertion_identity_hash != intent.assertion_identity_hash
            or resulting_snapshot.trust_policy_fingerprint != intent.trust_policy_fingerprint
            or resulting_snapshot.captured_status != intent.requested_to_status
        ):
            raise ProjectError("Prepared truth mutation recovery failed truth-identity validation")
        receipt = TruthMutationReceipt.capture(
            mutation_id=intent.mutation_id,
            theorem_id=intent.theorem_id,
            previous_status=intent.from_status,
            resulting_status=intent.requested_to_status,
            claim_snapshot_hash=snapshot.claim_snapshot_hash,
            resulting_claim_snapshot_hash=resulting_snapshot.claim_snapshot_hash,
            project_record_hash_before=str(prepared["project_record_hash_before"]),
            project_record_hash_after=project_record_hash(theorem),
            actor=intent.requested_by,
            applied_at=str(transition["at"]),
        )
        self._write_immutable_json(self.receipt_path(intent.mutation_id), receipt.to_dict())
        return theorem, resulting_snapshot, intent, receipt

    def _validate(self, operation: str, *args, **kwargs) -> SnapshotComparison:
        comparison = self.compare_claim_snapshot(*args, **kwargs)
        if not comparison.compatible:
            raise TruthValidationError(operation, comparison)
        return comparison

    def _write_mutation_blocked(
        self,
        intent: TruthMutationIntent,
        *,
        status: str,
        disposition: str,
        reason: str,
        current_claim_snapshot_hash: str | None,
    ) -> Path:
        identity = {
            "mutation_id": intent.mutation_id,
            "status": status,
            "disposition": disposition,
            "reason": reason,
            "current_claim_snapshot_hash": current_claim_snapshot_hash,
        }
        block_id = domain_hash("truth_mutation_blocked", identity)
        value = {
            "schema_version": 1,
            "object_type": "TRUTH_MUTATION_BLOCKED",
            "block_id": block_id,
            **identity,
            "blocked_at": intent.created_at,
        }
        path = self.blocked_dir / f"{_digest_part(block_id)}.json"
        self._write_immutable_json(path, value)
        return path

    @staticmethod
    def _read_truth_json(path: Path, object_name: str) -> dict[str, Any]:
        if not path.is_file():
            raise ProjectError(f"{object_name} not found: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProjectError(f"Invalid {object_name} JSON: {path}") from exc
        if not isinstance(value, dict):
            raise ProjectError(f"{object_name} artifact root must be an object")
        return value

    def _build_claim_snapshot(
        self,
        theorem_id: str,
        *,
        canonical_authority: Iterable[Mapping[str, Any]],
        replay_policy_hash: str | None,
        trust_policy_context: Mapping[str, Any] | None,
    ) -> ClaimSnapshot:
        theorem = self.project.load_theorem(theorem_id)
        assertion = self._assertion_from_record(theorem, assertion_kind="PROJECT_THEOREM")
        entries, dependency_bindings, premise_assumptions = self._capture_dependencies(theorem_id)
        dependencies = DependencySnapshot.capture(
            target_assertion_hash=assertion.assertion_identity_hash,
            dependencies=entries,
        )
        trust_kernel = TrustKernel.for_project(self.project)
        trust_context = trust_kernel.context(notation_scope=assertion.notation_scope)
        assumptions = AssumptionSnapshot.capture(
            target_id=theorem_id,
            assumptions=[
                *premise_assumptions,
                *self._authorized_local_assumptions(),
            ],
            semantic_scope={
                "notation_scope": assertion.notation_scope,
                "semantic_registry": _registry_identity(trust_context.get("semantic_registry")),
            },
        )
        registry_bindings = self._registry_bindings(assertion, trust_context)
        canonical_bindings = self._canonical_bindings(assertion, canonical_authority)
        policy_value = {
            "foundation_registry": _registry_identity(trust_context["foundation_registry"]),
            "semantic_registry": _registry_identity(trust_context.get("semantic_registry")),
            "project_policy": self._project_policy_context(),
            "replay_policy_hash": replay_policy_hash,
            "explicit_context": dict(trust_policy_context or {}),
        }
        return ClaimSnapshot.capture(
            theorem_id=theorem_id,
            assertion_identity=assertion,
            dependency_snapshot=dependencies,
            assumption_snapshot=assumptions,
            authority_bindings=tuple(
                [*dependency_bindings, *registry_bindings, *canonical_bindings]
            ),
            trust_policy_fingerprint=trust_policy_fingerprint(policy_value),
            captured_status=str(theorem["status"]),
            captured_at=utc_now(),
            project_record_hash=project_record_hash(theorem),
        )

    def _capture_dependencies(
        self, theorem_id: str
    ) -> tuple[list[DependencyEntry], list[AuthorityBinding], list[dict[str, Any]]]:
        entries: list[DependencyEntry] = []
        bindings: list[AuthorityBinding] = []
        assumptions: list[dict[str, Any]] = []
        visited: set[tuple[str, str]] = set()
        active: set[str] = set()

        def visit(current_id: str) -> None:
            if current_id in active:
                raise ProjectError(f"Dependency cycle prevents ClaimSnapshot capture: {current_id}")
            active.add(current_id)
            record = self.project.load_theorem(current_id)
            for dependency_id in sorted(set(record.get("dependencies", []))):
                resolved = self.project.resolve_dependency(dependency_id)
                kind = str(resolved["kind"])
                dependency = resolved["record"]
                key = (dependency_id, kind)
                if key in visited:
                    continue
                visited.add(key)
                assertion_kind = "PREMISE" if kind == "PREMISE" else "PROJECT_THEOREM"
                identity = self._assertion_from_record(dependency, assertion_kind=assertion_kind)
                binding = self._dependency_binding(kind, dependency, identity)
                status = "ACTIVE" if kind == "PREMISE" else str(dependency["status"])
                entries.append(
                    DependencyEntry(
                        dependency_id=dependency_id,
                        kind=kind,
                        assertion_identity_hash=identity.assertion_identity_hash,
                        authority_binding_hash=binding.binding_hash,
                        captured_status=status,
                    )
                )
                bindings.append(binding)
                if kind == "PREMISE":
                    assumptions.append(
                        {
                            "assumption_id": dependency_id,
                            "assertion_identity_hash": identity.assertion_identity_hash,
                            "authority_binding_hash": binding.binding_hash,
                            "kind": "PREMISE",
                        }
                    )
                else:
                    visit(dependency_id)
            active.remove(current_id)

        visit(theorem_id)
        return entries, bindings, assumptions

    def _dependency_binding(
        self, kind: str, record: Mapping[str, Any], identity: AssertionIdentity
    ) -> AuthorityBinding:
        if kind == "PREMISE":
            provenance = []
            for item in record.get("provenance", []):
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source") or "")
                source_path = self.project.safe_source_path(source)
                provenance.append(
                    {
                        **item,
                        "source_artifact_sha256": (
                            source_artifact_sha256(source_path)
                            if source_path and source_path.is_file()
                            else None
                        ),
                    }
                )
            content = {
                "assertion_identity_hash": identity.assertion_identity_hash,
                "active": bool(record.get("active")),
                "provenance": provenance,
            }
            return AuthorityBinding.capture(
                authority_kind="PREMISE",
                authority_id=str(record["id"]),
                assertion_identity_hash=identity.assertion_identity_hash,
                authority_content_hash=domain_hash("premise_authority_content", content),
                authority_status="ACTIVE" if record.get("active") else "INACTIVE",
                provenance={"source_file": record.get("source_file"), "sources": provenance},
            )
        content = {
            "assertion_identity_hash": identity.assertion_identity_hash,
            "status": record.get("status"),
            "proof_file": record.get("proof_file"),
            "audit_status": record.get("audit_status"),
        }
        return AuthorityBinding.capture(
            authority_kind="PROJECT_THEOREM",
            authority_id=str(record["id"]),
            assertion_identity_hash=identity.assertion_identity_hash,
            authority_content_hash=domain_hash("project_theorem_authority_content", content),
            authority_status=str(record.get("status") or "UNKNOWN"),
            provenance={"theorem_id": record["id"], "proof_file": record.get("proof_file")},
        )

    @staticmethod
    def _assertion_from_record(
        record: Mapping[str, Any], *, assertion_kind: str
    ) -> AssertionIdentity:
        return AssertionIdentity.capture(
            assertion_kind=assertion_kind,
            stable_id=str(record.get("id") or ""),
            statement=str(record.get("statement") or ""),
            claim_type=str(record.get("claim_type") or "premise"),
            notation_scope=str(record.get("notation_scope") or ""),
        )

    @staticmethod
    def _registry_bindings(
        assertion: AssertionIdentity, trust_context: Mapping[str, Any]
    ) -> list[AuthorityBinding]:
        bindings = []
        for kind, key in (
            ("FOUNDATION_REGISTRY", "foundation_registry"),
            ("SEMANTIC_REGISTRY", "semantic_registry"),
        ):
            registry = trust_context.get(key)
            if not isinstance(registry, dict):
                continue
            registry_hash = registry.get("hash")
            if not isinstance(registry_hash, str):
                continue
            bindings.append(
                AuthorityBinding.capture(
                    authority_kind=kind,
                    authority_id=str(registry.get("id") or key),
                    assertion_identity_hash=assertion.assertion_identity_hash,
                    authority_content_hash=registry_hash,
                    authority_status="VALID",
                    provenance=_registry_identity(registry) or {},
                )
            )
        return bindings

    @staticmethod
    def _canonical_bindings(
        assertion: AssertionIdentity,
        canonical_authority: Iterable[Mapping[str, Any]],
    ) -> list[AuthorityBinding]:
        bindings = []
        for index, item in enumerate(canonical_authority):
            requirement = item.get("requirement") or {}
            if requirement.get("purpose") not in {
                "proof_authority",
                "replay_authority",
            }:
                continue
            status = str(item.get("resolution_status") or "MISSING_CANONICAL")
            content_hash = item.get("computed_sha256")
            if not isinstance(content_hash, str) or not content_hash.startswith("sha256:"):
                content_hash = domain_hash(
                    "unresolved_canonical_authority",
                    {
                        "status": status,
                        "expected_sha256": item.get("expected_sha256"),
                        "checkpoint_sha256": item.get("checkpoint_sha256"),
                    },
                )
            bindings.append(
                AuthorityBinding.capture(
                    authority_kind="CANONICAL_SOURCE",
                    authority_id=str(requirement.get("logical_name") or f"canonical-{index + 1}"),
                    assertion_identity_hash=assertion.assertion_identity_hash,
                    authority_content_hash=content_hash,
                    authority_status=status,
                    provenance={
                        "canonical_filename": requirement.get("canonical_filename"),
                        "purpose": requirement.get("purpose"),
                        "authority_source": item.get("authority_source"),
                        "authority_record": item.get("authority_record") or {},
                        "computed_sha256": item.get("computed_sha256"),
                        "expected_sha256": item.get("expected_sha256"),
                    },
                )
            )
        return bindings

    def _project_policy_context(self) -> dict[str, Any]:
        project = self.project.load_project()
        return {
            key: project.get(key)
            for key in (
                "truth_policy_version",
                "trust_policy",
                "foundation_registry",
                "semantic_registry",
            )
            if key in project
        }

    def _authorized_local_assumptions(self) -> list[dict[str, Any]]:
        project = self.project.load_project()
        raw = project.get("authorized_local_assumptions", [])
        if not isinstance(raw, list):
            raise ProjectError("authorized_local_assumptions must be a list")
        return [dict(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _same_truth_identity(first: ClaimSnapshot, second: ClaimSnapshot) -> bool:
        return (
            first.claim_snapshot_hash == second.claim_snapshot_hash
            and first.assertion_identity_hash == second.assertion_identity_hash
            and first.dependency_snapshot_hash == second.dependency_snapshot_hash
            and first.assumption_snapshot_hash == second.assumption_snapshot_hash
            and first.authority_binding_hash == second.authority_binding_hash
            and first.trust_policy_fingerprint == second.trust_policy_fingerprint
            and first.semantic_input_hash == second.semantic_input_hash
            and first.captured_status == second.captured_status
        )

    @staticmethod
    def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
        raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode(
            "utf-8"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != raw:
                raise ProjectError(f"Immutable truth artifact collision: {path}")
            return
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)


def _registry_identity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in ("id", "version", "hash", "notation_scope")
        if value.get(key) is not None
    }


def _digest_part(value: str) -> str:
    digest = str(value).removeprefix("sha256:").casefold()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ProjectError("claim_snapshot_hash must be a SHA-256 digest")
    return digest
