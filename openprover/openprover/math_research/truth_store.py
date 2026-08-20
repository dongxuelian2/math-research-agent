"""Thin production Truth Plane facade over the existing ProjectStore.

The facade owns typed capture and stale validation.  ProjectStore remains the
filesystem/status-machine implementation; no Research Plane or runtime-control
semantics belong here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

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

    def claim_snapshot_path(self, claim_snapshot_hash: str) -> Path:
        digest = _digest_part(claim_snapshot_hash)
        return self.snapshot_dir / f"{digest}.json"

    def _validate(self, operation: str, *args, **kwargs) -> SnapshotComparison:
        comparison = self.compare_claim_snapshot(*args, **kwargs)
        if not comparison.compatible:
            raise TruthValidationError(operation, comparison)
        return comparison

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
                        "checkpoint_sha256": item.get("checkpoint_sha256"),
                        "resolved_source_location": item.get("resolved_source_location"),
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
        temporary.write_bytes(raw)
        temporary.replace(path)


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
