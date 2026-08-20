"""Deterministic Phase 7 root synthesis, consolidation, and promotion closure."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .claim_snapshot import ClaimSnapshot, SnapshotComparison
from .project import ProjectError, ProjectStore, utc_now
from .research_common import (
    artifact_dict,
    content_id,
    digest_part,
    read_json,
    require_hash,
    require_id,
    require_text,
    strict_fields,
    string_tuple,
    validate_envelope,
    write_immutable_bytes,
    write_immutable_json,
)
from .research_evidence import SessionClosure
from .research_map import ResearchMap
from .state_machine import AuditGate
from .truth_identity import canonical_json_bytes, domain_hash, source_artifact_sha256
from .truth_mutation import TruthMutationIntent, TruthMutationReceipt


PHASE7_SCHEMA_VERSION = 1
_TERMINAL_DISPOSITIONS = {"RESOLVED", "SUPERSEDED", "ABANDONED_WITH_REASON"}


class Phase7Error(ProjectError):
    """A Phase 7 input or immutable artifact is not valid for continuation."""


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _artifact_refs(paths: Iterable[str | Path], project_root: Path) -> tuple[dict[str, str], ...]:
    root = project_root.resolve()
    refs: list[dict[str, str]] = []
    for value in paths:
        path = Path(value).resolve()
        if not path.is_file():
            raise Phase7Error(f"Phase 7 artifact is missing: {path}")
        try:
            locator = path.relative_to(root).as_posix()
        except ValueError:
            locator = str(path)
        refs.append(
            {
                "path": locator,
                "source_artifact_sha256": source_artifact_sha256(path),
            }
        )
    refs.sort(key=lambda item: canonical_json_bytes(item))
    return tuple(refs)


def _normalize_artifact_refs(value: Any, field: str) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise Phase7Error(f"{field} must be a list")
    refs = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"path", "source_artifact_sha256"}:
            raise Phase7Error(f"{field} contains an invalid artifact reference")
        refs.append(
            {
                "path": require_text(item["path"], f"{field}.path"),
                "source_artifact_sha256": require_hash(
                    item["source_artifact_sha256"], f"{field}.source_artifact_sha256"
                ),
            }
        )
    refs.sort(key=lambda item: canonical_json_bytes(item))
    if len({canonical_json_bytes(item) for item in refs}) != len(refs):
        raise Phase7Error(f"{field} contains duplicate references")
    return tuple(refs)


def _relative_path(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


@dataclass(frozen=True, slots=True)
class RootSynthesis:
    schema_version: int
    object_type: str
    synthesis_id: str
    theorem_id: str
    root_claim_snapshot_hash: str
    audited_claim_snapshot_hash: str
    research_map_id: str
    research_map_version: int
    research_map_hash: str
    session_closure_id: str
    session_closure_hash: str
    obligation_ids: tuple[str, ...]
    closed_obligation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    audit_artifact_refs: tuple[dict[str, str], ...]
    candidate_artifact_sha256: str
    audit_gate_hash: str
    synthesis_body_sha256: str
    created_at: str
    created_by: str
    synthesis_hash: str

    @classmethod
    def capture(
        cls,
        *,
        theorem_id: str,
        root_claim_snapshot_hash: str,
        audited_claim_snapshot_hash: str,
        research_map_id: str,
        research_map_version: int,
        research_map_hash: str,
        session_closure_id: str,
        session_closure_hash: str,
        obligation_ids: Iterable[str],
        closed_obligation_ids: Iterable[str],
        evidence_ids: Iterable[str],
        audit_artifact_refs: Iterable[Mapping[str, str]],
        candidate_artifact_sha256: str,
        audit_gate_hash: str,
        synthesis_body_sha256: str,
        created_at: str,
        created_by: str,
    ) -> "RootSynthesis":
        obligations = tuple(
            sorted(string_tuple(tuple(obligation_ids), "obligation_ids", allow_empty=False))
        )
        closed = tuple(
            sorted(
                string_tuple(
                    tuple(closed_obligation_ids), "closed_obligation_ids", allow_empty=False
                )
            )
        )
        if obligations != closed:
            raise Phase7Error("RootSynthesis requires every current obligation to be closed")
        evidence = tuple(
            sorted(string_tuple(tuple(evidence_ids), "evidence_ids", allow_empty=False))
        )
        refs = _normalize_artifact_refs(list(audit_artifact_refs), "audit_artifact_refs")
        identity = {
            "theorem_id": require_id(theorem_id, "RootSynthesis.theorem_id"),
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash, "RootSynthesis.root_claim_snapshot_hash"
            ),
            "audited_claim_snapshot_hash": require_hash(
                audited_claim_snapshot_hash, "RootSynthesis.audited_claim_snapshot_hash"
            ),
            "research_map_id": require_id(research_map_id, "RootSynthesis.research_map_id"),
            "research_map_version": int(research_map_version),
            "research_map_hash": require_hash(research_map_hash, "RootSynthesis.research_map_hash"),
            "session_closure_id": require_id(
                session_closure_id, "RootSynthesis.session_closure_id"
            ),
            "session_closure_hash": require_hash(
                session_closure_hash, "RootSynthesis.session_closure_hash"
            ),
            "obligation_ids": list(obligations),
            "closed_obligation_ids": list(closed),
            "evidence_ids": list(evidence),
            "audit_artifact_refs": list(refs),
            "candidate_artifact_sha256": require_hash(
                candidate_artifact_sha256, "RootSynthesis.candidate_artifact_sha256"
            ),
            "audit_gate_hash": require_hash(audit_gate_hash, "RootSynthesis.audit_gate_hash"),
            "synthesis_body_sha256": require_hash(
                synthesis_body_sha256, "RootSynthesis.synthesis_body_sha256"
            ),
            "created_by": require_text(created_by, "RootSynthesis.created_by"),
        }
        synthesis_hash = domain_hash("phase7_root_synthesis", identity)
        return cls(
            schema_version=PHASE7_SCHEMA_VERSION,
            object_type="ROOT_SYNTHESIS",
            synthesis_id=content_id(
                "root-synthesis", "phase7_root_synthesis_id", {"synthesis_hash": synthesis_hash}
            ),
            theorem_id=identity["theorem_id"],
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            audited_claim_snapshot_hash=identity["audited_claim_snapshot_hash"],
            research_map_id=identity["research_map_id"],
            research_map_version=identity["research_map_version"],
            research_map_hash=identity["research_map_hash"],
            session_closure_id=identity["session_closure_id"],
            session_closure_hash=identity["session_closure_hash"],
            obligation_ids=obligations,
            closed_obligation_ids=closed,
            evidence_ids=evidence,
            audit_artifact_refs=refs,
            candidate_artifact_sha256=identity["candidate_artifact_sha256"],
            audit_gate_hash=identity["audit_gate_hash"],
            synthesis_body_sha256=identity["synthesis_body_sha256"],
            created_at=require_text(created_at, "RootSynthesis.created_at"),
            created_by=identity["created_by"],
            synthesis_hash=synthesis_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RootSynthesis":
        expected = {
            "schema_version",
            "object_type",
            "synthesis_id",
            "theorem_id",
            "root_claim_snapshot_hash",
            "audited_claim_snapshot_hash",
            "research_map_id",
            "research_map_version",
            "research_map_hash",
            "session_closure_id",
            "session_closure_hash",
            "obligation_ids",
            "closed_obligation_ids",
            "evidence_ids",
            "audit_artifact_refs",
            "candidate_artifact_sha256",
            "audit_gate_hash",
            "synthesis_body_sha256",
            "created_at",
            "created_by",
            "synthesis_hash",
        }
        strict_fields(value, expected, "RootSynthesis")
        validate_envelope(value, object_type="ROOT_SYNTHESIS", name="RootSynthesis")
        captured = cls.capture(
            theorem_id=value["theorem_id"],
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            audited_claim_snapshot_hash=value["audited_claim_snapshot_hash"],
            research_map_id=value["research_map_id"],
            research_map_version=value["research_map_version"],
            research_map_hash=value["research_map_hash"],
            session_closure_id=value["session_closure_id"],
            session_closure_hash=value["session_closure_hash"],
            obligation_ids=value["obligation_ids"],
            closed_obligation_ids=value["closed_obligation_ids"],
            evidence_ids=value["evidence_ids"],
            audit_artifact_refs=value["audit_artifact_refs"],
            candidate_artifact_sha256=value["candidate_artifact_sha256"],
            audit_gate_hash=value["audit_gate_hash"],
            synthesis_body_sha256=value["synthesis_body_sha256"],
            created_at=value["created_at"],
            created_by=value["created_by"],
        )
        if (
            captured.synthesis_id != value["synthesis_id"]
            or captured.synthesis_hash != value["synthesis_hash"]
        ):
            raise Phase7Error("RootSynthesis identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class FinalConsolidation:
    schema_version: int
    object_type: str
    consolidation_id: str
    theorem_id: str
    root_claim_snapshot_hash: str
    root_synthesis_id: str
    root_synthesis_hash: str
    candidate_artifact_sha256: str
    consolidated_proof_sha256: str
    audit_gate_hash: str
    consolidation_reaudit_hash: str
    consolidation_reaudit_passed: bool
    created_at: str
    created_by: str
    consolidation_hash: str

    @classmethod
    def capture(
        cls,
        *,
        theorem_id: str,
        root_claim_snapshot_hash: str,
        root_synthesis_id: str,
        root_synthesis_hash: str,
        candidate_artifact_sha256: str,
        consolidated_proof_sha256: str,
        audit_gate_hash: str,
        consolidation_reaudit_hash: str,
        consolidation_reaudit_passed: bool,
        created_at: str,
        created_by: str,
    ) -> "FinalConsolidation":
        if consolidation_reaudit_passed is not True:
            raise Phase7Error("FinalConsolidation requires a passing consolidation re-audit")
        identity = {
            "theorem_id": require_id(theorem_id, "FinalConsolidation.theorem_id"),
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash, "FinalConsolidation.root_claim_snapshot_hash"
            ),
            "root_synthesis_id": require_id(
                root_synthesis_id, "FinalConsolidation.root_synthesis_id"
            ),
            "root_synthesis_hash": require_hash(
                root_synthesis_hash, "FinalConsolidation.root_synthesis_hash"
            ),
            "candidate_artifact_sha256": require_hash(
                candidate_artifact_sha256, "FinalConsolidation.candidate_artifact_sha256"
            ),
            "consolidated_proof_sha256": require_hash(
                consolidated_proof_sha256, "FinalConsolidation.consolidated_proof_sha256"
            ),
            "audit_gate_hash": require_hash(audit_gate_hash, "FinalConsolidation.audit_gate_hash"),
            "consolidation_reaudit_hash": require_hash(
                consolidation_reaudit_hash, "FinalConsolidation.consolidation_reaudit_hash"
            ),
            "consolidation_reaudit_passed": True,
            "created_by": require_text(created_by, "FinalConsolidation.created_by"),
        }
        consolidation_hash = domain_hash("phase7_final_consolidation", identity)
        return cls(
            schema_version=PHASE7_SCHEMA_VERSION,
            object_type="FINAL_CONSOLIDATION",
            consolidation_id=content_id(
                "final-consolidation",
                "phase7_final_consolidation_id",
                {"consolidation_hash": consolidation_hash},
            ),
            theorem_id=identity["theorem_id"],
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            root_synthesis_id=identity["root_synthesis_id"],
            root_synthesis_hash=identity["root_synthesis_hash"],
            candidate_artifact_sha256=identity["candidate_artifact_sha256"],
            consolidated_proof_sha256=identity["consolidated_proof_sha256"],
            audit_gate_hash=identity["audit_gate_hash"],
            consolidation_reaudit_hash=identity["consolidation_reaudit_hash"],
            consolidation_reaudit_passed=True,
            created_at=require_text(created_at, "FinalConsolidation.created_at"),
            created_by=identity["created_by"],
            consolidation_hash=consolidation_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FinalConsolidation":
        expected = {
            "schema_version",
            "object_type",
            "consolidation_id",
            "theorem_id",
            "root_claim_snapshot_hash",
            "root_synthesis_id",
            "root_synthesis_hash",
            "candidate_artifact_sha256",
            "consolidated_proof_sha256",
            "audit_gate_hash",
            "consolidation_reaudit_hash",
            "consolidation_reaudit_passed",
            "created_at",
            "created_by",
            "consolidation_hash",
        }
        strict_fields(value, expected, "FinalConsolidation")
        validate_envelope(value, object_type="FINAL_CONSOLIDATION", name="FinalConsolidation")
        captured = cls.capture(
            theorem_id=value["theorem_id"],
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            root_synthesis_id=value["root_synthesis_id"],
            root_synthesis_hash=value["root_synthesis_hash"],
            candidate_artifact_sha256=value["candidate_artifact_sha256"],
            consolidated_proof_sha256=value["consolidated_proof_sha256"],
            audit_gate_hash=value["audit_gate_hash"],
            consolidation_reaudit_hash=value["consolidation_reaudit_hash"],
            consolidation_reaudit_passed=value["consolidation_reaudit_passed"],
            created_at=value["created_at"],
            created_by=value["created_by"],
        )
        if (
            captured.consolidation_id != value["consolidation_id"]
            or captured.consolidation_hash != value["consolidation_hash"]
        ):
            raise Phase7Error("FinalConsolidation identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class PromotionClosure:
    schema_version: int
    object_type: str
    closure_id: str
    theorem_id: str
    audited_claim_snapshot_hash: str
    resulting_claim_snapshot_hash: str
    root_synthesis_id: str
    root_synthesis_hash: str
    final_consolidation_id: str
    final_consolidation_hash: str
    consolidation_reaudit_hash: str
    truth_mutation_id: str
    truth_mutation_receipt_hash: str
    resulting_status: str
    closed_at: str
    closed_by: str
    closure_hash: str

    @classmethod
    def capture(
        cls,
        *,
        theorem_id: str,
        audited_claim_snapshot_hash: str,
        resulting_claim_snapshot_hash: str,
        root_synthesis_id: str,
        root_synthesis_hash: str,
        final_consolidation_id: str,
        final_consolidation_hash: str,
        consolidation_reaudit_hash: str,
        truth_mutation_id: str,
        truth_mutation_receipt_hash: str,
        resulting_status: str,
        closed_at: str,
        closed_by: str,
    ) -> "PromotionClosure":
        if resulting_status != "PROVED":
            raise Phase7Error("PromotionClosure requires a PROVED TruthMutation result")
        identity = {
            "theorem_id": require_id(theorem_id, "PromotionClosure.theorem_id"),
            "audited_claim_snapshot_hash": require_hash(
                audited_claim_snapshot_hash, "PromotionClosure.audited_claim_snapshot_hash"
            ),
            "resulting_claim_snapshot_hash": require_hash(
                resulting_claim_snapshot_hash, "PromotionClosure.resulting_claim_snapshot_hash"
            ),
            "root_synthesis_id": require_id(
                root_synthesis_id, "PromotionClosure.root_synthesis_id"
            ),
            "root_synthesis_hash": require_hash(
                root_synthesis_hash, "PromotionClosure.root_synthesis_hash"
            ),
            "final_consolidation_id": require_id(
                final_consolidation_id, "PromotionClosure.final_consolidation_id"
            ),
            "final_consolidation_hash": require_hash(
                final_consolidation_hash, "PromotionClosure.final_consolidation_hash"
            ),
            "consolidation_reaudit_hash": require_hash(
                consolidation_reaudit_hash, "PromotionClosure.consolidation_reaudit_hash"
            ),
            "truth_mutation_id": require_hash(
                truth_mutation_id, "PromotionClosure.truth_mutation_id"
            ),
            "truth_mutation_receipt_hash": require_hash(
                truth_mutation_receipt_hash, "PromotionClosure.truth_mutation_receipt_hash"
            ),
            "resulting_status": require_text(resulting_status, "PromotionClosure.resulting_status"),
            "closed_by": require_text(closed_by, "PromotionClosure.closed_by"),
        }
        closure_hash = domain_hash("phase7_promotion_closure", identity)
        return cls(
            schema_version=PHASE7_SCHEMA_VERSION,
            object_type="PROMOTION_CLOSURE",
            closure_id=content_id(
                "promotion-closure", "phase7_promotion_closure_id", {"closure_hash": closure_hash}
            ),
            audited_claim_snapshot_hash=identity["audited_claim_snapshot_hash"],
            resulting_claim_snapshot_hash=identity["resulting_claim_snapshot_hash"],
            root_synthesis_id=identity["root_synthesis_id"],
            root_synthesis_hash=identity["root_synthesis_hash"],
            final_consolidation_id=identity["final_consolidation_id"],
            final_consolidation_hash=identity["final_consolidation_hash"],
            consolidation_reaudit_hash=identity["consolidation_reaudit_hash"],
            truth_mutation_id=identity["truth_mutation_id"],
            truth_mutation_receipt_hash=identity["truth_mutation_receipt_hash"],
            resulting_status=identity["resulting_status"],
            theorem_id=identity["theorem_id"],
            closed_at=require_text(closed_at, "PromotionClosure.closed_at"),
            closed_by=identity["closed_by"],
            closure_hash=closure_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PromotionClosure":
        expected = {
            "schema_version",
            "object_type",
            "closure_id",
            "theorem_id",
            "audited_claim_snapshot_hash",
            "resulting_claim_snapshot_hash",
            "root_synthesis_id",
            "root_synthesis_hash",
            "final_consolidation_id",
            "final_consolidation_hash",
            "consolidation_reaudit_hash",
            "truth_mutation_id",
            "truth_mutation_receipt_hash",
            "resulting_status",
            "closed_at",
            "closed_by",
            "closure_hash",
        }
        strict_fields(value, expected, "PromotionClosure")
        validate_envelope(value, object_type="PROMOTION_CLOSURE", name="PromotionClosure")
        captured = cls.capture(
            theorem_id=value["theorem_id"],
            audited_claim_snapshot_hash=value["audited_claim_snapshot_hash"],
            resulting_claim_snapshot_hash=value["resulting_claim_snapshot_hash"],
            root_synthesis_id=value["root_synthesis_id"],
            root_synthesis_hash=value["root_synthesis_hash"],
            final_consolidation_id=value["final_consolidation_id"],
            final_consolidation_hash=value["final_consolidation_hash"],
            consolidation_reaudit_hash=value["consolidation_reaudit_hash"],
            truth_mutation_id=value["truth_mutation_id"],
            truth_mutation_receipt_hash=value["truth_mutation_receipt_hash"],
            resulting_status=value["resulting_status"],
            closed_at=value["closed_at"],
            closed_by=value["closed_by"],
        )
        if (
            captured.closure_id != value["closure_id"]
            or captured.closure_hash != value["closure_hash"]
        ):
            raise Phase7Error("PromotionClosure identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


class Phase7Store:
    """Project-local immutable storage and deterministic Phase 7 coordinator."""

    def __init__(self, project: ProjectStore):
        self.project = project
        self.root = project.root / "phase7"
        self.root_synthesis_dir = self.root / "root_synthesis"
        self.root_synthesis_body_dir = self.root / "root_synthesis_bodies"
        self.final_consolidation_dir = self.root / "final_consolidations"
        self.final_proof_dir = self.root / "final_proofs"
        self.consolidation_reaudit_dir = self.root / "consolidation_reaudits"
        self.promotion_closure_dir = self.root / "promotion_closures"
        for path in (
            self.root_synthesis_dir,
            self.root_synthesis_body_dir,
            self.final_consolidation_dir,
            self.final_proof_dir,
            self.consolidation_reaudit_dir,
            self.promotion_closure_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def root_synthesis_path(self, synthesis_hash: str) -> Path:
        return self.root_synthesis_dir / f"{digest_part(synthesis_hash, 'synthesis_hash')}.json"

    def root_synthesis_body_path(self, synthesis_hash: str) -> Path:
        return self.root_synthesis_body_dir / f"{digest_part(synthesis_hash, 'synthesis_hash')}.md"

    def final_consolidation_path(self, consolidation_hash: str) -> Path:
        return (
            self.final_consolidation_dir
            / f"{digest_part(consolidation_hash, 'consolidation_hash')}.json"
        )

    def final_proof_path(self, consolidation_hash: str) -> Path:
        return self.final_proof_dir / f"{digest_part(consolidation_hash, 'consolidation_hash')}.md"

    def consolidation_reaudit_path(self, consolidation_hash: str) -> Path:
        return (
            self.consolidation_reaudit_dir
            / f"{digest_part(consolidation_hash, 'consolidation_hash')}.json"
        )

    def promotion_closure_path(self, closure_hash: str) -> Path:
        return self.promotion_closure_dir / f"{digest_part(closure_hash, 'closure_hash')}.json"

    def synthesize_root(
        self,
        *,
        theorem_id: str,
        claim_snapshot: ClaimSnapshot,
        snapshot_comparison: SnapshotComparison,
        research_map: ResearchMap,
        session_closure: SessionClosure,
        gate: AuditGate,
        candidate_path: str | Path,
        audit_artifacts: Iterable[str | Path],
        created_by: str = "Phase7RootSynthesizer",
    ) -> RootSynthesis:
        if not snapshot_comparison.compatible:
            raise Phase7Error(
                f"ROOT_SYNTHESIS requires a current ClaimSnapshot: {snapshot_comparison.status}"
            )
        if snapshot_comparison.stored_claim_snapshot_hash != claim_snapshot.claim_snapshot_hash:
            raise Phase7Error("ROOT_SYNTHESIS comparison is bound to a different ClaimSnapshot")
        if not gate.passed:
            raise Phase7Error("ROOT_SYNTHESIS requires a passing AuditGate")
        if gate.audited_claim_snapshot_hash != claim_snapshot.claim_snapshot_hash:
            raise Phase7Error("ROOT_SYNTHESIS audit gate is not bound to the exact ClaimSnapshot")
        if theorem_id != claim_snapshot.theorem_id or research_map.root_theorem_id != theorem_id:
            raise Phase7Error("ROOT_SYNTHESIS theorem identity is inconsistent")
        if research_map.root_claim_snapshot_hash != session_closure.root_claim_snapshot_hash:
            raise Phase7Error("ROOT_SYNTHESIS map and SessionClosure have different roots")
        if session_closure.research_map_id != research_map.research_map_id:
            raise Phase7Error("ROOT_SYNTHESIS SessionClosure targets a different ResearchMap")
        if (
            session_closure.research_map_version == research_map.version
            and session_closure.research_map_hash == research_map.research_map_hash
        ):
            pass
        elif (
            research_map.version == session_closure.research_map_version + 1
            and research_map.parent_version_ref == session_closure.research_map_hash
            and research_map.revision_reason == "OBLIGATION_RESOLVED"
        ):
            pass
        else:
            raise Phase7Error(
                "ROOT_SYNTHESIS SessionClosure is not bound to the current map or its "
                "authorized resolution successor"
            )
        current_refs = {item.obligation_id: item for item in research_map.obligation_refs}
        current_obligation = current_refs.get(session_closure.obligation_id)
        if (
            current_obligation is None
            or current_obligation.obligation_hash != session_closure.obligation_hash
        ):
            raise Phase7Error("ROOT_SYNTHESIS SessionClosure obligation is outside the current map")
        if session_closure.execution_status != "COMPLETED":
            raise Phase7Error("ROOT_SYNTHESIS requires a completed SessionClosure")
        if not session_closure.validated_evidence:
            raise Phase7Error("ROOT_SYNTHESIS requires typed validated evidence")
        if any(
            item.root_claim_snapshot_hash != research_map.root_claim_snapshot_hash
            or item.obligation_id != session_closure.obligation_id
            or item.obligation_hash != session_closure.obligation_hash
            or session_closure.obligation_id not in item.scope_obligation_ids
            for item in session_closure.validated_evidence
        ):
            raise Phase7Error("ROOT_SYNTHESIS evidence is not bound to the exact closure scope")
        if research_map.open_obligation_ids:
            raise Phase7Error(
                "ROOT_SYNTHESIS requires a closed ResearchMap frontier: "
                + ", ".join(research_map.open_obligation_ids)
            )
        if any(
            item.disposition not in _TERMINAL_DISPOSITIONS for item in research_map.obligation_refs
        ):
            raise Phase7Error("ROOT_SYNTHESIS found a non-terminal obligation disposition")

        candidate = Path(candidate_path).resolve()
        if not candidate.is_file():
            raise Phase7Error(f"ROOT_SYNTHESIS candidate is missing: {candidate}")
        candidate_hash = source_artifact_sha256(candidate)
        refs = _artifact_refs(audit_artifacts, self.project.root)
        if not refs:
            raise Phase7Error("ROOT_SYNTHESIS requires immutable audit artifact references")
        evidence_ids = tuple(
            sorted(item.evidence_id for item in session_closure.validated_evidence)
        )
        if not evidence_ids:
            raise Phase7Error("ROOT_SYNTHESIS requires at least one evidence identity")
        audit_gate_hash = domain_hash("phase7_audit_gate", gate.to_dict())
        obligation_ids = tuple(item.obligation_id for item in research_map.obligation_refs)
        evidence_payload = [item.to_dict() for item in session_closure.validated_evidence]
        body = (
            "# Root Synthesis\n\n"
            f"Theorem: `{theorem_id}`\n\n"
            f"Statement: {self.project.load_theorem(theorem_id)['statement']}\n\n"
            "## Exact root identity\n\n"
            f"- Research root ClaimSnapshot: `{research_map.root_claim_snapshot_hash}`\n"
            f"- Audited ClaimSnapshot: `{claim_snapshot.claim_snapshot_hash}`\n"
            f"- ResearchMap: `{research_map.research_map_hash}` (v{research_map.version})\n"
            f"- SessionClosure: `{session_closure.closure_hash}`\n"
            f"- AuditGate: `{audit_gate_hash}`\n"
            f"- Candidate bytes: `{candidate_hash}`\n\n"
            "## Closed research frontier\n\n"
            + "\n".join(
                f"- `{item.obligation_id}` `{item.disposition}` `{item.obligation_hash}`"
                for item in research_map.obligation_refs
            )
            + "\n\n## Validated evidence\n\n"
            + "```json\n"
            + json.dumps(evidence_payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n```\n"
        ).encode("utf-8")
        synthesis = RootSynthesis.capture(
            theorem_id=theorem_id,
            root_claim_snapshot_hash=research_map.root_claim_snapshot_hash,
            audited_claim_snapshot_hash=claim_snapshot.claim_snapshot_hash,
            research_map_id=research_map.research_map_id,
            research_map_version=research_map.version,
            research_map_hash=research_map.research_map_hash,
            session_closure_id=session_closure.session_closure_id,
            session_closure_hash=session_closure.closure_hash,
            obligation_ids=obligation_ids,
            closed_obligation_ids=obligation_ids,
            evidence_ids=evidence_ids,
            audit_artifact_refs=refs,
            candidate_artifact_sha256=candidate_hash,
            audit_gate_hash=audit_gate_hash,
            synthesis_body_sha256=_sha256_bytes(body),
            created_at=utc_now(),
            created_by=created_by,
        )
        if self.root_synthesis_path(synthesis.synthesis_hash).is_file():
            return self.load_root_synthesis(synthesis.synthesis_hash)
        write_immutable_bytes(self.root_synthesis_body_path(synthesis.synthesis_hash), body)
        write_immutable_json(
            self.root_synthesis_path(synthesis.synthesis_hash), synthesis.to_dict()
        )
        return synthesis

    def load_root_synthesis(self, synthesis_hash: str) -> RootSynthesis:
        synthesis = RootSynthesis.from_dict(
            read_json(self.root_synthesis_path(synthesis_hash), "RootSynthesis")
        )
        if synthesis.synthesis_hash != synthesis_hash:
            raise Phase7Error("RootSynthesis filename/hash mismatch")
        body_path = self.root_synthesis_body_path(synthesis.synthesis_hash)
        if (
            not body_path.is_file()
            or _sha256_bytes(body_path.read_bytes()) != synthesis.synthesis_body_sha256
        ):
            raise Phase7Error("RootSynthesis body is missing or has changed")
        return synthesis

    def consolidate(
        self,
        *,
        root_synthesis: RootSynthesis,
        gate: AuditGate,
        candidate_path: str | Path,
        created_by: str = "Phase7FinalConsolidator",
    ) -> FinalConsolidation:
        stored_synthesis = self.load_root_synthesis(root_synthesis.synthesis_hash)
        if stored_synthesis != root_synthesis:
            raise Phase7Error("FINAL_CONSOLIDATION received a different RootSynthesis artifact")
        if not gate.passed:
            raise Phase7Error("FINAL_CONSOLIDATION requires a passing AuditGate")
        gate_hash = domain_hash("phase7_audit_gate", gate.to_dict())
        if gate_hash != root_synthesis.audit_gate_hash:
            raise Phase7Error("FINAL_CONSOLIDATION audit gate changed after root synthesis")
        candidate = Path(candidate_path).resolve()
        if not candidate.is_file():
            raise Phase7Error(f"FINAL_CONSOLIDATION candidate is missing: {candidate}")
        candidate_bytes = candidate.read_bytes()
        candidate_hash = _sha256_bytes(candidate_bytes)
        if candidate_hash != root_synthesis.candidate_artifact_sha256:
            raise Phase7Error("FINAL_CONSOLIDATION candidate bytes changed after root synthesis")
        manifest = {
            "schema_version": PHASE7_SCHEMA_VERSION,
            "object_type": "PHASE7_FINAL_CONSOLIDATION",
            "theorem_id": root_synthesis.theorem_id,
            "root_claim_snapshot_hash": root_synthesis.root_claim_snapshot_hash,
            "audited_claim_snapshot_hash": root_synthesis.audited_claim_snapshot_hash,
            "root_synthesis_id": root_synthesis.synthesis_id,
            "root_synthesis_hash": root_synthesis.synthesis_hash,
            "source_candidate_sha256": candidate_hash,
        }
        body = (
            b"<!-- OPENPROVER_PHASE7_FINAL_CONSOLIDATION\n"
            + json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            + b"\n-->\n\n"
            + candidate_bytes
        )
        proof_hash = _sha256_bytes(body)
        reaudit_identity = {
            "theorem_id": root_synthesis.theorem_id,
            "root_claim_snapshot_hash": root_synthesis.root_claim_snapshot_hash,
            "audited_claim_snapshot_hash": root_synthesis.audited_claim_snapshot_hash,
            "root_synthesis_hash": root_synthesis.synthesis_hash,
            "candidate_artifact_sha256": candidate_hash,
            "consolidated_proof_sha256": proof_hash,
            "audit_gate_hash": gate_hash,
            "candidate_path": _relative_path(self.project.root, candidate),
        }
        reaudit_hash = domain_hash("phase7_consolidation_reaudit", reaudit_identity)
        consolidation = FinalConsolidation.capture(
            theorem_id=root_synthesis.theorem_id,
            root_claim_snapshot_hash=root_synthesis.root_claim_snapshot_hash,
            root_synthesis_id=root_synthesis.synthesis_id,
            root_synthesis_hash=root_synthesis.synthesis_hash,
            candidate_artifact_sha256=candidate_hash,
            consolidated_proof_sha256=proof_hash,
            audit_gate_hash=gate_hash,
            consolidation_reaudit_hash=reaudit_hash,
            consolidation_reaudit_passed=True,
            created_at=utc_now(),
            created_by=created_by,
        )
        if self.final_consolidation_path(consolidation.consolidation_hash).is_file():
            return self.load_final_consolidation(consolidation.consolidation_hash)
        write_immutable_bytes(self.final_proof_path(consolidation.consolidation_hash), body)
        write_immutable_json(
            self.consolidation_reaudit_path(consolidation.consolidation_hash),
            {
                "schema_version": PHASE7_SCHEMA_VERSION,
                "object_type": "CONSOLIDATION_REAUDIT",
                "reaudit_hash": reaudit_hash,
                "passed": True,
                "identity": reaudit_identity,
                "checked_at": utc_now(),
            },
        )
        write_immutable_json(
            self.final_consolidation_path(consolidation.consolidation_hash), consolidation.to_dict()
        )
        return consolidation

    def load_final_consolidation(self, consolidation_hash: str) -> FinalConsolidation:
        consolidation = FinalConsolidation.from_dict(
            read_json(self.final_consolidation_path(consolidation_hash), "FinalConsolidation")
        )
        if consolidation.consolidation_hash != consolidation_hash:
            raise Phase7Error("FinalConsolidation filename/hash mismatch")
        proof_path = self.final_proof_path(consolidation.consolidation_hash)
        if (
            not proof_path.is_file()
            or _sha256_bytes(proof_path.read_bytes()) != consolidation.consolidated_proof_sha256
        ):
            raise Phase7Error("FinalConsolidation proof body is missing or has changed")
        reaudit = read_json(
            self.consolidation_reaudit_path(consolidation.consolidation_hash),
            "Consolidation re-audit",
        )
        identity = reaudit.get("identity")
        if not isinstance(identity, Mapping):
            raise Phase7Error("FinalConsolidation re-audit identity is missing")
        if (
            reaudit.get("passed") is not True
            or reaudit.get("reaudit_hash") != consolidation.consolidation_reaudit_hash
            or domain_hash("phase7_consolidation_reaudit", identity)
            != consolidation.consolidation_reaudit_hash
            or identity.get("theorem_id") != consolidation.theorem_id
            or identity.get("root_claim_snapshot_hash")
            != consolidation.root_claim_snapshot_hash
            or identity.get("root_synthesis_hash") != consolidation.root_synthesis_hash
            or identity.get("candidate_artifact_sha256") != consolidation.candidate_artifact_sha256
            or identity.get("consolidated_proof_sha256") != consolidation.consolidated_proof_sha256
            or identity.get("audit_gate_hash") != consolidation.audit_gate_hash
        ):
            raise Phase7Error("FinalConsolidation re-audit is missing or failed")
        return consolidation

    def close_promotion(
        self,
        *,
        root_synthesis: RootSynthesis,
        final_consolidation: FinalConsolidation,
        intent: TruthMutationIntent,
        receipt: TruthMutationReceipt,
        closed_by: str = "Phase7PromotionCloser",
    ) -> PromotionClosure:
        stored_synthesis = self.load_root_synthesis(root_synthesis.synthesis_hash)
        stored_consolidation = self.load_final_consolidation(final_consolidation.consolidation_hash)
        if stored_synthesis != root_synthesis or stored_consolidation != final_consolidation:
            raise Phase7Error("PROMOTION_CLOSURE received an unpersisted Phase 7 artifact")
        if (
            intent.theorem_id != root_synthesis.theorem_id
            or receipt.theorem_id != intent.theorem_id
        ):
            raise Phase7Error("PROMOTION_CLOSURE theorem identity does not match TruthMutation")
        if intent.claim_snapshot_hash != root_synthesis.audited_claim_snapshot_hash:
            raise Phase7Error("PROMOTION_CLOSURE audited root does not match TruthMutation intent")
        if intent.audited_claim_snapshot_hash != intent.claim_snapshot_hash:
            raise Phase7Error("PROMOTION_CLOSURE intent is not bound to its audited root")
        if receipt.mutation_id != intent.mutation_id or receipt.resulting_status != "PROVED":
            raise Phase7Error("PROMOTION_CLOSURE requires a durable PROVED TruthMutation receipt")
        if final_consolidation.root_synthesis_hash != root_synthesis.synthesis_hash:
            raise Phase7Error("PROMOTION_CLOSURE consolidation is not rooted in the synthesis")
        closure = PromotionClosure.capture(
            theorem_id=intent.theorem_id,
            audited_claim_snapshot_hash=root_synthesis.audited_claim_snapshot_hash,
            resulting_claim_snapshot_hash=receipt.resulting_claim_snapshot_hash,
            root_synthesis_id=root_synthesis.synthesis_id,
            root_synthesis_hash=root_synthesis.synthesis_hash,
            final_consolidation_id=final_consolidation.consolidation_id,
            final_consolidation_hash=final_consolidation.consolidation_hash,
            consolidation_reaudit_hash=final_consolidation.consolidation_reaudit_hash,
            truth_mutation_id=intent.mutation_id,
            truth_mutation_receipt_hash=receipt.receipt_hash,
            resulting_status=receipt.resulting_status,
            closed_at=utc_now(),
            closed_by=closed_by,
        )
        if self.promotion_closure_path(closure.closure_hash).is_file():
            return self.load_promotion_closure(closure.closure_hash)
        write_immutable_json(self.promotion_closure_path(closure.closure_hash), closure.to_dict())
        return closure

    def load_promotion_closure(self, closure_hash: str) -> PromotionClosure:
        closure = PromotionClosure.from_dict(
            read_json(self.promotion_closure_path(closure_hash), "PromotionClosure")
        )
        if closure.closure_hash != closure_hash:
            raise Phase7Error("PromotionClosure filename/hash mismatch")
        return closure

    def verify_promotion_closure(
        self,
        closure_hash: str,
        *,
        truth_store: Any | None = None,
    ) -> PromotionClosure:
        closure = self.load_promotion_closure(closure_hash)
        synthesis = self.load_root_synthesis(closure.root_synthesis_hash)
        consolidation = self.load_final_consolidation(closure.final_consolidation_hash)
        if (
            closure.root_synthesis_id != synthesis.synthesis_id
            or closure.final_consolidation_id != consolidation.consolidation_id
            or consolidation.root_synthesis_hash != synthesis.synthesis_hash
            or closure.audited_claim_snapshot_hash != synthesis.audited_claim_snapshot_hash
            or closure.consolidation_reaudit_hash != consolidation.consolidation_reaudit_hash
        ):
            raise Phase7Error("PROMOTION_CLOSURE references inconsistent Phase 7 artifacts")
        if truth_store is not None:
            intent = truth_store.load_mutation_intent(closure.truth_mutation_id)
            receipt = truth_store.load_mutation_receipt(closure.truth_mutation_id)
            if (
                intent.claim_snapshot_hash != closure.audited_claim_snapshot_hash
                or receipt.receipt_hash != closure.truth_mutation_receipt_hash
                or receipt.resulting_claim_snapshot_hash != closure.resulting_claim_snapshot_hash
                or receipt.resulting_status != closure.resulting_status
            ):
                raise Phase7Error("PROMOTION_CLOSURE TruthMutation receipt does not match")
        return closure
