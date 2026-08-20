"""Independent, typed criticism of an exact ArchitecturePatch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .architecture_patch import ArchitecturePatch, PatchClassification
from .architecture_review import ArchitectureReview, GovernanceActor
from .project import ProjectError
from .research_common import (
    RESEARCH_SCHEMA_VERSION,
    artifact_dict,
    content_id,
    require_id,
    require_text,
    stable_value,
    strict_fields,
    string_tuple,
    validate_envelope,
)
from .research_map import ResearchMap
from .structural_probe import StructuralProbe, StructuralProbeResult
from .truth_identity import domain_hash


class ArchitectureCriticVerdict(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REVISE_REQUIRED = "REVISE_REQUIRED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    STALE_REVIEW = "STALE_REVIEW"
    SCOPE_LOSS = "SCOPE_LOSS"
    TRUTH_BOUNDARY_VIOLATION = "TRUTH_BOUNDARY_VIOLATION"


@dataclass(frozen=True, slots=True)
class ArchitectureCriticIndependenceReceipt:
    review_author_id: str
    patch_author_id: str
    critic_actor_id: str
    review_provider: str
    review_model: str
    critic_provider: str
    critic_model: str
    same_provider: bool
    same_model: bool
    fresh_context: bool
    same_context: bool
    shared_evidence_refs: tuple[str, ...]
    independence_policy: str
    policy_satisfied: bool
    receipt_hash: str

    @classmethod
    def capture(
        cls,
        *,
        review_author: GovernanceActor,
        patch_author_id: str,
        critic_actor: GovernanceActor,
        shared_evidence_refs: tuple[str, ...] | list[str],
        independence_policy: str = "DIFFERENT_ACTOR_FRESH_CONTEXT",
    ) -> "ArchitectureCriticIndependenceReceipt":
        patch_author = require_id(
            patch_author_id, "ArchitectureCriticIndependenceReceipt.patch_author_id"
        )
        same_provider = bool(review_author.provider) and (
            review_author.provider == critic_actor.provider
        )
        same_model = bool(review_author.model) and review_author.model == critic_actor.model
        same_context = (
            review_author.context_hash is not None
            and review_author.context_hash == critic_actor.context_hash
        )
        policy = require_text(
            independence_policy,
            "ArchitectureCriticIndependenceReceipt.independence_policy",
        )
        satisfied = (
            critic_actor.actor_id not in {review_author.actor_id, patch_author}
            and critic_actor.fresh_context
            and not same_context
        )
        identity = {
            "review_author_id": review_author.actor_id,
            "patch_author_id": patch_author,
            "critic_actor_id": critic_actor.actor_id,
            "review_provider": review_author.provider,
            "review_model": review_author.model,
            "critic_provider": critic_actor.provider,
            "critic_model": critic_actor.model,
            "same_provider": same_provider,
            "same_model": same_model,
            "fresh_context": critic_actor.fresh_context,
            "same_context": same_context,
            "shared_evidence_refs": list(
                string_tuple(
                    shared_evidence_refs,
                    "ArchitectureCriticIndependenceReceipt.shared_evidence_refs",
                )
            ),
            "independence_policy": policy,
            "policy_satisfied": satisfied,
        }
        return cls(
            review_author_id=identity["review_author_id"],
            patch_author_id=identity["patch_author_id"],
            critic_actor_id=identity["critic_actor_id"],
            review_provider=identity["review_provider"],
            review_model=identity["review_model"],
            critic_provider=identity["critic_provider"],
            critic_model=identity["critic_model"],
            same_provider=identity["same_provider"],
            same_model=identity["same_model"],
            fresh_context=identity["fresh_context"],
            same_context=identity["same_context"],
            shared_evidence_refs=tuple(identity["shared_evidence_refs"]),
            independence_policy=identity["independence_policy"],
            policy_satisfied=identity["policy_satisfied"],
            receipt_hash=domain_hash("architecture_critic_independence", stable_value(identity)),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArchitectureCriticIndependenceReceipt":
        fields = {
            "review_author_id",
            "patch_author_id",
            "critic_actor_id",
            "review_provider",
            "review_model",
            "critic_provider",
            "critic_model",
            "same_provider",
            "same_model",
            "fresh_context",
            "same_context",
            "shared_evidence_refs",
            "independence_policy",
            "policy_satisfied",
            "receipt_hash",
        }
        strict_fields(value, fields, "ArchitectureCriticIndependenceReceipt")
        identity = {key: value[key] for key in fields - {"receipt_hash"}}
        if domain_hash("architecture_critic_independence", stable_value(identity)) != value.get(
            "receipt_hash"
        ):
            raise ProjectError("ArchitectureCritic independence receipt hash mismatch")
        return cls(
            review_author_id=value["review_author_id"],
            patch_author_id=value["patch_author_id"],
            critic_actor_id=value["critic_actor_id"],
            review_provider=value["review_provider"],
            review_model=value["review_model"],
            critic_provider=value["critic_provider"],
            critic_model=value["critic_model"],
            same_provider=value["same_provider"],
            same_model=value["same_model"],
            fresh_context=value["fresh_context"],
            same_context=value["same_context"],
            shared_evidence_refs=tuple(value["shared_evidence_refs"]),
            independence_policy=value["independence_policy"],
            policy_satisfied=value["policy_satisfied"],
            receipt_hash=value["receipt_hash"],
        )

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class ArchitectureCritic:
    schema_version: int
    object_type: str
    critic_id: str
    patch_id: str
    patch_hash: str
    review_id: str
    review_hash: str
    root_claim_snapshot_hash: str
    source_map_hash: str
    probe_ids: tuple[str, ...]
    probe_hashes: tuple[str, ...]
    open_obligation_ids: tuple[str, ...]
    route_failure_refs: tuple[str, ...]
    verdict: str
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    critic_actor: GovernanceActor
    independence_receipt: ArchitectureCriticIndependenceReceipt
    created_at: str
    critic_hash: str

    @classmethod
    def capture(
        cls,
        *,
        patch: ArchitecturePatch,
        review: ArchitectureReview,
        current_map: ResearchMap,
        probes: tuple[StructuralProbe, ...] | list[StructuralProbe],
        critic_actor: GovernanceActor,
        verdict: str,
        reasons: tuple[str, ...] | list[str],
        evidence_refs: tuple[str, ...] | list[str],
        route_failure_refs: tuple[str, ...] | list[str],
        created_at: str,
    ) -> "ArchitectureCritic":
        try:
            verdict_value = ArchitectureCriticVerdict(verdict).value
        except ValueError as exc:
            raise ProjectError(f"Unsupported ArchitectureCritic verdict: {verdict}") from exc
        if patch.review_id != review.review_id or patch.review_hash != review.review_hash:
            raise ProjectError("ArchitectureCritic review/patch mismatch")
        probe_values = tuple(probes)
        probe_ids = tuple(item.probe_id for item in probe_values)
        probe_hashes = tuple(item.probe_hash for item in probe_values)
        receipt = ArchitectureCriticIndependenceReceipt.capture(
            review_author=review.author,
            patch_author_id=patch.proposed_by,
            critic_actor=critic_actor,
            shared_evidence_refs=tuple(sorted(set((*review.evidence_refs, *patch.evidence_refs)))),
        )
        identity = {
            "patch_id": patch.patch_id,
            "patch_hash": patch.patch_hash,
            "review_id": review.review_id,
            "review_hash": review.review_hash,
            "root_claim_snapshot_hash": patch.root_claim_snapshot_hash,
            "source_map_hash": patch.source_map_hash,
            "probe_ids": list(probe_ids),
            "probe_hashes": list(probe_hashes),
            "open_obligation_ids": list(current_map.open_obligation_ids),
            "route_failure_refs": list(
                string_tuple(route_failure_refs, "ArchitectureCritic.route_failure_refs")
            ),
            "verdict": verdict_value,
            "reasons": list(string_tuple(reasons, "ArchitectureCritic.reasons", allow_empty=False)),
            "evidence_refs": list(string_tuple(evidence_refs, "ArchitectureCritic.evidence_refs")),
            "critic_actor": critic_actor.to_dict(),
            "independence_receipt": receipt.to_dict(),
        }
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="ARCHITECTURE_CRITIC",
            critic_id=content_id("critic", "architecture_critic_id", stable_value(identity)),
            patch_id=patch.patch_id,
            patch_hash=patch.patch_hash,
            review_id=review.review_id,
            review_hash=review.review_hash,
            root_claim_snapshot_hash=patch.root_claim_snapshot_hash,
            source_map_hash=patch.source_map_hash,
            probe_ids=probe_ids,
            probe_hashes=probe_hashes,
            open_obligation_ids=current_map.open_obligation_ids,
            route_failure_refs=tuple(identity["route_failure_refs"]),
            verdict=verdict_value,
            reasons=tuple(identity["reasons"]),
            evidence_refs=tuple(identity["evidence_refs"]),
            critic_actor=critic_actor,
            independence_receipt=receipt,
            created_at=require_text(created_at, "ArchitectureCritic.created_at"),
            critic_hash=domain_hash("architecture_critic", stable_value(identity)),
        )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        patch: ArchitecturePatch,
        review: ArchitectureReview,
        current_map: ResearchMap,
        probes: tuple[StructuralProbe, ...],
    ) -> "ArchitectureCritic":
        fields = {
            "schema_version",
            "object_type",
            "critic_id",
            "patch_id",
            "patch_hash",
            "review_id",
            "review_hash",
            "root_claim_snapshot_hash",
            "source_map_hash",
            "probe_ids",
            "probe_hashes",
            "open_obligation_ids",
            "route_failure_refs",
            "verdict",
            "reasons",
            "evidence_refs",
            "critic_actor",
            "independence_receipt",
            "created_at",
            "critic_hash",
        }
        strict_fields(value, fields, "ArchitectureCritic")
        validate_envelope(value, object_type="ARCHITECTURE_CRITIC", name="ArchitectureCritic")
        captured = cls.capture(
            patch=patch,
            review=review,
            current_map=current_map,
            probes=probes,
            critic_actor=GovernanceActor.from_dict(value["critic_actor"]),
            verdict=value["verdict"],
            reasons=value["reasons"],
            evidence_refs=value["evidence_refs"],
            route_failure_refs=value["route_failure_refs"],
            created_at=value["created_at"],
        )
        if captured.independence_receipt.to_dict() != value.get("independence_receipt"):
            raise ProjectError("ArchitectureCritic independence provenance mismatch")
        if captured.open_obligation_ids != tuple(value.get("open_obligation_ids", [])):
            raise ProjectError("ArchitectureCritic open-obligation projection mismatch")
        if captured.critic_id != value.get("critic_id") or captured.critic_hash != value.get(
            "critic_hash"
        ):
            raise ProjectError("ArchitectureCritic identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = artifact_dict(self)
        value["critic_actor"] = self.critic_actor.to_dict()
        value["independence_receipt"] = self.independence_receipt.to_dict()
        return value


def evaluate_patch(
    *,
    patch: ArchitecturePatch,
    review: ArchitectureReview,
    current_map: ResearchMap,
    probes: tuple[StructuralProbe, ...] | list[StructuralProbe],
    critic_actor: GovernanceActor,
    evidence_refs: tuple[str, ...] | list[str],
    created_at: str,
) -> ArchitectureCritic:
    probe_values = tuple(probes)
    verdict = ArchitectureCriticVerdict.APPROVE.value
    reasons = ["Patch is current, scope-complete, probe-supported, and independently reviewed."]
    if (
        patch.root_claim_snapshot_hash != current_map.root_claim_snapshot_hash
        or review.root_claim_snapshot_hash != current_map.root_claim_snapshot_hash
        or patch.source_map_hash != current_map.research_map_hash
        or review.research_map_hash != current_map.research_map_hash
    ):
        verdict = ArchitectureCriticVerdict.STALE_REVIEW.value
        reasons = ["Review or patch does not bind the current root/map."]
    else:
        sources = {
            source
            for transfer in patch.scope_transfers
            for source in transfer.source_obligation_ids
        }
        if patch.classification == PatchClassification.DESTRUCTIVE_PATCH.value and sources != set(
            patch.affected_obligation_ids
        ):
            verdict = ArchitectureCriticVerdict.SCOPE_LOSS.value
            reasons = ["Not every affected obligation has an explicit ScopeTransfer."]
        elif patch.probe_required and (
            not probe_values
            or set(patch.probe_ids) != {item.probe_id for item in probe_values}
            or any(
                item.result != StructuralProbeResult.SUPPORTS_PATCH.value for item in probe_values
            )
        ):
            verdict = (
                ArchitectureCriticVerdict.REJECT.value
                if any(
                    item.result == StructuralProbeResult.REJECTS_PATCH.value
                    for item in probe_values
                )
                else ArchitectureCriticVerdict.INSUFFICIENT_EVIDENCE.value
            )
            reasons = ["Required bounded probe evidence does not support this patch."]
    critic = ArchitectureCritic.capture(
        patch=patch,
        review=review,
        current_map=current_map,
        probes=probe_values,
        critic_actor=critic_actor,
        verdict=verdict,
        reasons=reasons,
        evidence_refs=evidence_refs,
        route_failure_refs=current_map.route_failure_refs,
        created_at=created_at,
    )
    if (
        critic.verdict == ArchitectureCriticVerdict.APPROVE.value
        and not critic.independence_receipt.policy_satisfied
    ):
        return ArchitectureCritic.capture(
            patch=patch,
            review=review,
            current_map=current_map,
            probes=probe_values,
            critic_actor=critic_actor,
            verdict=ArchitectureCriticVerdict.INSUFFICIENT_EVIDENCE.value,
            reasons=("ArchitectureCritic independence policy is not satisfied.",),
            evidence_refs=evidence_refs,
            route_failure_refs=current_map.route_failure_refs,
            created_at=created_at,
        )
    return critic
