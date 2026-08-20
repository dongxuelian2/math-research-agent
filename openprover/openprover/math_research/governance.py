"""Single-process durable architecture-governance control state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .project import ProjectError, ProjectStore, utc_now
from .research_common import (
    RESEARCH_SCHEMA_VERSION,
    artifact_dict,
    digest_part,
    read_json,
    require_hash,
    require_id,
    stable_value,
    strict_fields,
    string_tuple,
    validate_envelope,
    write_immutable_json,
    write_projection_json,
)
from .research_map import ResearchMap
from .research_store import ResearchStoreFacade
from .structural_effect import (
    StructuralEffect,
    StructuralEffectLevel,
    StructuralEffectValidation,
)
from .truth_identity import domain_hash


class ArchitectureReviewTrigger(str, Enum):
    MANDATORY_INTERVAL = "MANDATORY_INTERVAL"
    REPEATED_ROUTE_FAILURE = "REPEATED_ROUTE_FAILURE"
    LONG_BLOCKED_OBLIGATION = "LONG_BLOCKED_OBLIGATION"
    TACTICAL_WITHOUT_STRUCTURAL_PROGRESS = "TACTICAL_WITHOUT_STRUCTURAL_PROGRESS"
    MAJOR_SCOPE_CHANGE = "MAJOR_SCOPE_CHANGE"
    ROOT_OBSTRUCTION_STALLED = "ROOT_OBSTRUCTION_STALLED"
    HUMAN_REQUEST = "HUMAN_REQUEST"
    LITERATURE_MECHANISM_CHANGE = "LITERATURE_MECHANISM_CHANGE"
    PRE_DESTRUCTIVE_REFRAME = "PRE_DESTRUCTIVE_REFRAME"


@dataclass(frozen=True, slots=True)
class GovernanceThresholds:
    mandatory_interval_sessions: int = 10
    tactical_without_structural: int = 6
    repeated_route_failures: int = 3
    long_blocked_sessions: int = 5
    map_versions_without_review: int = 5
    root_obstruction_stalled_sessions: int = 6
    candidate_repair_cycles: int = 3

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ProjectError(f"GovernanceThresholds.{name} must be a positive integer")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GovernanceThresholds":
        strict_fields(value, set(cls.__dataclass_fields__), "GovernanceThresholds")
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class ArchitectureReviewClock:
    schema_version: int
    object_type: str
    clock_id: str
    revision: int
    root_claim_snapshot_hash: str
    research_map_id: str
    observed_map_version: int
    observed_map_hash: str
    sessions_since_last_review: int
    tactical_progress_since_last_review: int
    structural_progress_since_last_review: int
    route_failure_counts: tuple[tuple[str, int], ...]
    blocked_obligation_ages: tuple[tuple[str, int], ...]
    map_versions_since_last_review: int
    candidate_repair_cycles_since_last_review: int
    root_obstruction_unchanged_sessions: int
    explicit_signals: tuple[str, ...]
    review_due: bool
    trigger_reasons: tuple[str, ...]
    last_review_id: str | None
    last_review_hash: str | None
    previous_clock_hash: str | None
    thresholds: GovernanceThresholds
    recorded_at: str
    clock_hash: str

    @classmethod
    def capture(
        cls,
        *,
        clock_id: str,
        revision: int,
        root_claim_snapshot_hash: str,
        research_map_id: str,
        observed_map_version: int,
        observed_map_hash: str,
        sessions_since_last_review: int = 0,
        tactical_progress_since_last_review: int = 0,
        structural_progress_since_last_review: int = 0,
        route_failure_counts: Mapping[str, int] | tuple[tuple[str, int], ...] = (),
        blocked_obligation_ages: Mapping[str, int] | tuple[tuple[str, int], ...] = (),
        map_versions_since_last_review: int = 0,
        candidate_repair_cycles_since_last_review: int = 0,
        root_obstruction_unchanged_sessions: int = 0,
        explicit_signals: tuple[str, ...] | list[str] = (),
        last_review_id: str | None = None,
        last_review_hash: str | None = None,
        previous_clock_hash: str | None = None,
        thresholds: GovernanceThresholds | None = None,
        recorded_at: str,
    ) -> "ArchitectureReviewClock":
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ProjectError("ArchitectureReviewClock.revision must be positive")
        if (
            not isinstance(observed_map_version, int)
            or isinstance(observed_map_version, bool)
            or observed_map_version < 1
        ):
            raise ProjectError("ArchitectureReviewClock.observed_map_version must be positive")
        counter_names = (
            "sessions_since_last_review",
            "tactical_progress_since_last_review",
            "structural_progress_since_last_review",
            "map_versions_since_last_review",
            "candidate_repair_cycles_since_last_review",
            "root_obstruction_unchanged_sessions",
        )
        counters = {name: locals()[name] for name in counter_names}
        for name, value in counters.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ProjectError(f"ArchitectureReviewClock.{name} must be non-negative")
        threshold_value = thresholds or GovernanceThresholds()
        routes = cls._count_tuple(route_failure_counts, "route_failure_counts")
        blocked = cls._count_tuple(blocked_obligation_ages, "blocked_obligation_ages")
        signals = string_tuple(explicit_signals, "ArchitectureReviewClock.explicit_signals")
        allowed_signals = {item.value for item in ArchitectureReviewTrigger}
        unknown = set(signals) - allowed_signals
        if unknown:
            raise ProjectError(f"Unsupported ArchitectureReview trigger: {sorted(unknown)}")
        reasons = cls._trigger_reasons(
            thresholds=threshold_value,
            sessions=sessions_since_last_review,
            tactical=tactical_progress_since_last_review,
            structural=structural_progress_since_last_review,
            route_counts=routes,
            blocked_ages=blocked,
            map_versions=map_versions_since_last_review,
            repair_cycles=candidate_repair_cycles_since_last_review,
            obstruction_stalled=root_obstruction_unchanged_sessions,
            signals=signals,
        )
        if (last_review_id is None) != (last_review_hash is None):
            raise ProjectError("last review id/hash must be supplied together")
        identity = {
            "clock_id": require_id(clock_id, "ArchitectureReviewClock.clock_id"),
            "revision": revision,
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash,
                "ArchitectureReviewClock.root_claim_snapshot_hash",
            ),
            "research_map_id": require_id(
                research_map_id, "ArchitectureReviewClock.research_map_id"
            ),
            "observed_map_version": observed_map_version,
            "observed_map_hash": require_hash(
                observed_map_hash, "ArchitectureReviewClock.observed_map_hash"
            ),
            **counters,
            "route_failure_counts": [[key, value] for key, value in routes],
            "blocked_obligation_ages": [[key, value] for key, value in blocked],
            "explicit_signals": list(signals),
            "review_due": bool(reasons),
            "trigger_reasons": list(reasons),
            "last_review_id": (
                require_id(last_review_id, "ArchitectureReviewClock.last_review_id")
                if last_review_id is not None
                else None
            ),
            "last_review_hash": (
                require_hash(last_review_hash, "ArchitectureReviewClock.last_review_hash")
                if last_review_hash is not None
                else None
            ),
            "previous_clock_hash": (
                require_hash(previous_clock_hash, "ArchitectureReviewClock.previous_clock_hash")
                if previous_clock_hash is not None
                else None
            ),
            "thresholds": threshold_value.to_dict(),
        }
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="ARCHITECTURE_REVIEW_CLOCK",
            clock_id=identity["clock_id"],
            revision=revision,
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            research_map_id=identity["research_map_id"],
            observed_map_version=observed_map_version,
            observed_map_hash=identity["observed_map_hash"],
            sessions_since_last_review=sessions_since_last_review,
            tactical_progress_since_last_review=tactical_progress_since_last_review,
            structural_progress_since_last_review=structural_progress_since_last_review,
            route_failure_counts=routes,
            blocked_obligation_ages=blocked,
            map_versions_since_last_review=map_versions_since_last_review,
            candidate_repair_cycles_since_last_review=(candidate_repair_cycles_since_last_review),
            root_obstruction_unchanged_sessions=root_obstruction_unchanged_sessions,
            explicit_signals=signals,
            review_due=bool(reasons),
            trigger_reasons=reasons,
            last_review_id=identity["last_review_id"],
            last_review_hash=identity["last_review_hash"],
            previous_clock_hash=identity["previous_clock_hash"],
            thresholds=threshold_value,
            recorded_at=recorded_at,
            clock_hash=domain_hash("architecture_review_clock", stable_value(identity)),
        )

    @staticmethod
    def _count_tuple(
        value: Mapping[str, int] | tuple[tuple[str, int], ...], field: str
    ) -> tuple[tuple[str, int], ...]:
        items = value.items() if isinstance(value, Mapping) else value
        result: list[tuple[str, int]] = []
        for raw_key, raw_count in items:
            key = require_id(raw_key, f"ArchitectureReviewClock.{field}.key")
            if not isinstance(raw_count, int) or isinstance(raw_count, bool) or raw_count < 0:
                raise ProjectError(f"ArchitectureReviewClock.{field} counts must be non-negative")
            result.append((key, raw_count))
        ordered = tuple(sorted(result))
        if len({item[0] for item in ordered}) != len(ordered):
            raise ProjectError(f"ArchitectureReviewClock.{field} contains duplicate ids")
        return ordered

    @staticmethod
    def _trigger_reasons(
        *,
        thresholds: GovernanceThresholds,
        sessions: int,
        tactical: int,
        structural: int,
        route_counts: tuple[tuple[str, int], ...],
        blocked_ages: tuple[tuple[str, int], ...],
        map_versions: int,
        repair_cycles: int,
        obstruction_stalled: int,
        signals: tuple[str, ...],
    ) -> tuple[str, ...]:
        reasons = set(signals)
        if sessions >= thresholds.mandatory_interval_sessions:
            reasons.add(ArchitectureReviewTrigger.MANDATORY_INTERVAL.value)
        if any(count >= thresholds.repeated_route_failures for _, count in route_counts):
            reasons.add(ArchitectureReviewTrigger.REPEATED_ROUTE_FAILURE.value)
        if any(age >= thresholds.long_blocked_sessions for _, age in blocked_ages):
            reasons.add(ArchitectureReviewTrigger.LONG_BLOCKED_OBLIGATION.value)
        if tactical >= thresholds.tactical_without_structural and structural == 0:
            reasons.add(ArchitectureReviewTrigger.TACTICAL_WITHOUT_STRUCTURAL_PROGRESS.value)
        if map_versions >= thresholds.map_versions_without_review:
            reasons.add(ArchitectureReviewTrigger.MAJOR_SCOPE_CHANGE.value)
        if (
            obstruction_stalled >= thresholds.root_obstruction_stalled_sessions
            or repair_cycles >= thresholds.candidate_repair_cycles
        ):
            reasons.add(ArchitectureReviewTrigger.ROOT_OBSTRUCTION_STALLED.value)
        return tuple(sorted(reasons))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArchitectureReviewClock":
        fields = {
            "schema_version",
            "object_type",
            "clock_id",
            "revision",
            "root_claim_snapshot_hash",
            "research_map_id",
            "observed_map_version",
            "observed_map_hash",
            "sessions_since_last_review",
            "tactical_progress_since_last_review",
            "structural_progress_since_last_review",
            "route_failure_counts",
            "blocked_obligation_ages",
            "map_versions_since_last_review",
            "candidate_repair_cycles_since_last_review",
            "root_obstruction_unchanged_sessions",
            "explicit_signals",
            "review_due",
            "trigger_reasons",
            "last_review_id",
            "last_review_hash",
            "previous_clock_hash",
            "thresholds",
            "recorded_at",
            "clock_hash",
        }
        strict_fields(value, fields, "ArchitectureReviewClock")
        validate_envelope(
            value,
            object_type="ARCHITECTURE_REVIEW_CLOCK",
            name="ArchitectureReviewClock",
        )
        captured = cls.capture(
            clock_id=value["clock_id"],
            revision=value["revision"],
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            research_map_id=value["research_map_id"],
            observed_map_version=value["observed_map_version"],
            observed_map_hash=value["observed_map_hash"],
            sessions_since_last_review=value["sessions_since_last_review"],
            tactical_progress_since_last_review=value["tactical_progress_since_last_review"],
            structural_progress_since_last_review=value["structural_progress_since_last_review"],
            route_failure_counts=tuple(tuple(item) for item in value["route_failure_counts"]),
            blocked_obligation_ages=tuple(tuple(item) for item in value["blocked_obligation_ages"]),
            map_versions_since_last_review=value["map_versions_since_last_review"],
            candidate_repair_cycles_since_last_review=value[
                "candidate_repair_cycles_since_last_review"
            ],
            root_obstruction_unchanged_sessions=value["root_obstruction_unchanged_sessions"],
            explicit_signals=value["explicit_signals"],
            last_review_id=value["last_review_id"],
            last_review_hash=value["last_review_hash"],
            previous_clock_hash=value["previous_clock_hash"],
            thresholds=GovernanceThresholds.from_dict(value["thresholds"]),
            recorded_at=value["recorded_at"],
        )
        if captured.review_due != value.get("review_due") or captured.trigger_reasons != tuple(
            value.get("trigger_reasons", [])
        ):
            raise ProjectError("ArchitectureReviewClock trigger projection mismatch")
        if captured.clock_hash != value.get("clock_hash"):
            raise ProjectError("ArchitectureReviewClock hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = artifact_dict(self)
        value["thresholds"] = self.thresholds.to_dict()
        return value


class GovernanceController:
    """Own durable review scheduling without owning mathematical strategy."""

    def __init__(
        self,
        project: ProjectStore,
        *,
        research_store: ResearchStoreFacade | None = None,
        thresholds: GovernanceThresholds | None = None,
    ):
        self.project = project
        self.research_store = research_store or ResearchStoreFacade(project)
        self.thresholds = thresholds or GovernanceThresholds()
        self.root = project.root / "research" / "governance"
        self.effects_root = self.root / "structural_effects"
        self.clocks_root = self.root / "review_clocks"
        self.reviews_root = self.root / "architecture_reviews"
        self.probe_plans_root = self.root / "structural_probes" / "plans"
        self.probes_root = self.root / "structural_probes" / "results"
        self.control_path = self.root / "control.json"

    def ensure_clock(self, research_map_id: str) -> ArchitectureReviewClock:
        current_map = self.research_store.load_current_map(research_map_id)
        path = self._clock_current_path(research_map_id)
        if path.is_file():
            current = ArchitectureReviewClock.from_dict(read_json(path, "review clock"))
            return self._sync_map(current, current_map)
        clock = ArchitectureReviewClock.capture(
            clock_id=f"clock-{research_map_id}",
            revision=1,
            root_claim_snapshot_hash=current_map.root_claim_snapshot_hash,
            research_map_id=research_map_id,
            observed_map_version=current_map.version,
            observed_map_hash=current_map.research_map_hash,
            thresholds=self.thresholds,
            recorded_at=utc_now(),
        )
        self._persist_clock(clock)
        return clock

    def load_clock(self, research_map_id: str) -> ArchitectureReviewClock:
        return ArchitectureReviewClock.from_dict(
            read_json(self._clock_current_path(research_map_id), "review clock")
        )

    def load_effect(self, structural_effect_id: str) -> StructuralEffect:
        return StructuralEffect.from_dict(
            read_json(self.effects_root / f"{structural_effect_id}.json", "StructuralEffect")
        )

    def load_review(self, review_id: str):
        from .architecture_review import ArchitectureReview

        return ArchitectureReview.from_dict(
            read_json(self.reviews_root / f"{review_id}.json", "ArchitectureReview")
        )

    def commit_review(self, review):
        """Persist one formal review and perform the only legal clock reset."""

        from .architecture_review import ArchitectureReview

        if not isinstance(review, ArchitectureReview):
            raise ProjectError("commit_review requires ArchitectureReview")
        current_map = self.research_store.load_current_map(review.research_map_id)
        self._validate_review_bindings(review, current_map)
        current_clock = self.ensure_clock(review.research_map_id)
        if not current_clock.review_due:
            raise ProjectError("ArchitectureReview commit requires a durable review trigger")
        if not set(review.trigger_reasons) <= set(current_clock.trigger_reasons):
            raise ProjectError("ArchitectureReview trigger reasons are not current")
        write_immutable_json(self.reviews_root / f"{review.review_id}.json", review.to_dict())
        reset = ArchitectureReviewClock.capture(
            clock_id=current_clock.clock_id,
            revision=current_clock.revision + 1,
            root_claim_snapshot_hash=current_map.root_claim_snapshot_hash,
            research_map_id=current_map.research_map_id,
            observed_map_version=current_map.version,
            observed_map_hash=current_map.research_map_hash,
            last_review_id=review.review_id,
            last_review_hash=review.review_hash,
            previous_clock_hash=current_clock.clock_hash,
            thresholds=current_clock.thresholds,
            recorded_at=utc_now(),
        )
        self._persist_clock(reset)
        return reset

    def persist_probe_plan(self, plan):
        from .architecture_review import ArchitectureReviewVerdict
        from .structural_probe import StructuralProbePlan

        if not isinstance(plan, StructuralProbePlan):
            raise ProjectError("persist_probe_plan requires StructuralProbePlan")
        review = self.load_review(plan.review_id)
        current_map = self.research_store.load_current_map(plan.research_map_id)
        if review.review_hash != plan.review_hash:
            raise ProjectError("StructuralProbePlan review hash mismatch")
        if review.verdict not in {
            ArchitectureReviewVerdict.STRUCTURAL_PROBE_REQUIRED.value,
            ArchitectureReviewVerdict.DESTRUCTIVE_PATCH_PROPOSED.value,
        }:
            raise ProjectError("ArchitectureReview verdict does not authorize a probe")
        if plan.root_claim_snapshot_hash != current_map.root_claim_snapshot_hash:
            raise ProjectError("STALE_REVIEW: StructuralProbePlan root changed")
        if plan.source_map_hash != current_map.research_map_hash:
            raise ProjectError("STALE_REVIEW: StructuralProbePlan map changed")
        if plan.source_map_version != current_map.version:
            raise ProjectError("STALE_REVIEW: StructuralProbePlan map version changed")
        write_immutable_json(self.probe_plans_root / f"{plan.probe_id}.json", plan.to_dict())
        control = self._control()
        control["active_structural_probe_id"] = plan.probe_id
        self._write_control(control)
        return plan

    def load_probe_plan(self, probe_id: str):
        from .structural_probe import StructuralProbePlan

        return StructuralProbePlan.from_dict(
            read_json(self.probe_plans_root / f"{probe_id}.json", "StructuralProbePlan")
        )

    def close_probe(self, probe):
        from .structural_probe import StructuralProbe

        if not isinstance(probe, StructuralProbe):
            raise ProjectError("close_probe requires StructuralProbe")
        plan = self.load_probe_plan(probe.probe_id)
        current_map = self.research_store.load_current_map(plan.research_map_id)
        if probe.plan_hash != plan.plan_hash:
            raise ProjectError("StructuralProbe plan hash mismatch")
        if current_map.root_claim_snapshot_hash != probe.root_claim_snapshot_hash:
            raise ProjectError("REVALIDATION_REQUIRED: StructuralProbe root changed")
        if current_map.research_map_hash != probe.source_map_hash:
            raise ProjectError("REVALIDATION_REQUIRED: StructuralProbe source map changed")
        write_immutable_json(self.probes_root / f"{probe.probe_id}.json", probe.to_dict())
        control = self._control()
        if control.get("active_structural_probe_id") == probe.probe_id:
            control["active_structural_probe_id"] = None
        self._write_control(control)
        return probe

    def load_probe(self, probe_id: str):
        from .structural_probe import StructuralProbe

        plan = self.load_probe_plan(probe_id)
        return StructuralProbe.from_dict(
            read_json(self.probes_root / f"{probe_id}.json", "StructuralProbe"), plan
        )

    def record_effect(self, effect: StructuralEffect) -> ArchitectureReviewClock:
        current_map = self.research_store.load_current_map(effect.research_map_id)
        self._validate_effect(effect, current_map)
        write_immutable_json(
            self.effects_root / f"{effect.structural_effect_id}.json", effect.to_dict()
        )
        current = self.ensure_clock(effect.research_map_id)
        updates: dict[str, Any] = {}
        if effect.validation_status == StructuralEffectValidation.VALIDATED.value:
            if effect.level == StructuralEffectLevel.TACTICAL_PROGRESS.value:
                updates["tactical_progress_since_last_review"] = (
                    current.tactical_progress_since_last_review + 1
                )
            elif effect.level == StructuralEffectLevel.STRUCTURAL_PROGRESS.value:
                updates["structural_progress_since_last_review"] = (
                    current.structural_progress_since_last_review + 1
                )
        return self._advance(current, current_map, **updates)

    def record_session(
        self,
        research_map_id: str,
        *,
        successor_execution: bool = False,
        root_obstruction_unchanged: bool = True,
        blocked_obligation_ids: tuple[str, ...] | list[str] = (),
    ) -> ArchitectureReviewClock:
        current_map = self.research_store.load_current_map(research_map_id)
        current = self.ensure_clock(research_map_id)
        blocked = dict(current.blocked_obligation_ages)
        active_blocked = set(
            string_tuple(blocked_obligation_ids, "record_session.blocked_obligation_ids")
        )
        for obligation_id in active_blocked:
            current_map.obligation_ref(obligation_id)
            blocked[obligation_id] = blocked.get(obligation_id, 0) + 1
        for obligation_id in set(blocked) - active_blocked:
            blocked.pop(obligation_id)
        return self._advance(
            current,
            current_map,
            sessions_since_last_review=current.sessions_since_last_review + 1,
            blocked_obligation_ages=blocked,
            candidate_repair_cycles_since_last_review=(
                current.candidate_repair_cycles_since_last_review
                + (1 if successor_execution else 0)
            ),
            root_obstruction_unchanged_sessions=(
                current.root_obstruction_unchanged_sessions
                + (1 if root_obstruction_unchanged else 0)
            ),
        )

    def record_route_failure(
        self, research_map_id: str, *, obligation_id: str
    ) -> ArchitectureReviewClock:
        current_map = self.research_store.load_current_map(research_map_id)
        current_map.obligation_ref(obligation_id)
        current = self.ensure_clock(research_map_id)
        counts = dict(current.route_failure_counts)
        counts[obligation_id] = counts.get(obligation_id, 0) + 1
        return self._advance(current, current_map, route_failure_counts=counts)

    def signal_review(self, research_map_id: str, trigger: str) -> ArchitectureReviewClock:
        try:
            reason = ArchitectureReviewTrigger(trigger).value
        except ValueError as exc:
            raise ProjectError(f"Unsupported ArchitectureReview trigger: {trigger}") from exc
        current_map = self.research_store.load_current_map(research_map_id)
        current = self.ensure_clock(research_map_id)
        signals = tuple(sorted(set((*current.explicit_signals, reason))))
        return self._advance(current, current_map, explicit_signals=signals)

    def checkpoint_projection(self, research_map_id: str) -> dict[str, Any]:
        clock = self.ensure_clock(research_map_id)
        control = self._control()
        return {
            "architecture_review_clock_id": clock.clock_id,
            "architecture_review_clock_revision": clock.revision,
            "architecture_review_clock_hash": clock.clock_hash,
            "architecture_review_due": clock.review_due,
            "architecture_review_triggers": list(clock.trigger_reasons),
            "last_architecture_review_id": clock.last_review_id,
            "last_architecture_review_hash": clock.last_review_hash,
            "active_structural_probe_id": control.get("active_structural_probe_id"),
            "pending_architecture_patch_id": control.get("pending_architecture_patch_id"),
        }

    @staticmethod
    def classify_legacy_checkpoint(state: Mapping[str, Any]) -> str:
        required = {
            "architecture_review_clock_id",
            "architecture_review_clock_revision",
            "architecture_review_clock_hash",
            "architecture_review_due",
            "last_architecture_review_id",
        }
        return "DIRECT_IMPORT" if required <= set(state) else "GOVERNANCE_REVIEW_REQUIRED"

    def _validate_effect(self, effect: StructuralEffect, current_map: ResearchMap) -> None:
        if effect.root_claim_snapshot_hash != current_map.root_claim_snapshot_hash:
            raise ProjectError("STALE_STRUCTURAL_EFFECT: root ClaimSnapshot changed")
        if effect.research_map_hash != current_map.research_map_hash:
            raise ProjectError("STALE_STRUCTURAL_EFFECT: ResearchMap changed")
        if effect.research_map_version != current_map.version:
            raise ProjectError("STALE_STRUCTURAL_EFFECT: ResearchMap version changed")
        for obligation_id in effect.obligation_refs:
            current_map.obligation_ref(obligation_id)

    def _validate_review_bindings(self, review, current_map: ResearchMap) -> None:
        if review.root_claim_snapshot_hash != current_map.root_claim_snapshot_hash:
            raise ProjectError("STALE_REVIEW: root ClaimSnapshot changed")
        if review.research_map_hash != current_map.research_map_hash:
            raise ProjectError("STALE_REVIEW: ResearchMap changed")
        if review.research_map_version != current_map.version:
            raise ProjectError("STALE_REVIEW: ResearchMap version changed")
        open_ids = {
            item.obligation_id for item in current_map.obligation_refs if item.disposition == "OPEN"
        }
        blocked_ids = {
            item.obligation_id
            for item in current_map.obligation_refs
            if item.disposition == "BLOCKED"
        }
        if set(review.open_obligation_ids) != open_ids:
            raise ProjectError("ArchitectureReview OPEN obligation summary is incomplete")
        if set(review.blocked_obligation_ids) != blocked_ids:
            raise ProjectError("ArchitectureReview BLOCKED obligation summary is incomplete")
        if not set(review.route_failure_refs) <= set(current_map.route_failure_refs):
            raise ProjectError("ArchitectureReview references an unknown route failure")
        for effect_id in review.structural_effect_refs:
            effect = self.load_effect(effect_id)
            if effect.research_map_id != current_map.research_map_id:
                raise ProjectError("ArchitectureReview StructuralEffect belongs to another map")

    def _control(self) -> dict[str, Any]:
        if not self.control_path.is_file():
            return {
                "schema_version": RESEARCH_SCHEMA_VERSION,
                "object_type": "GOVERNANCE_CONTROL_PROJECTION",
                "active_structural_probe_id": None,
                "pending_architecture_patch_id": None,
            }
        value = read_json(self.control_path, "governance control projection")
        strict_fields(
            value,
            {
                "schema_version",
                "object_type",
                "active_structural_probe_id",
                "pending_architecture_patch_id",
            },
            "GovernanceControlProjection",
        )
        validate_envelope(
            value,
            object_type="GOVERNANCE_CONTROL_PROJECTION",
            name="GovernanceControlProjection",
        )
        return value

    def _write_control(self, value: Mapping[str, Any]) -> None:
        write_projection_json(self.control_path, value)

    def _sync_map(
        self, current: ArchitectureReviewClock, current_map: ResearchMap
    ) -> ArchitectureReviewClock:
        if current.root_claim_snapshot_hash != current_map.root_claim_snapshot_hash:
            raise ProjectError("GOVERNANCE_REVALIDATION_REQUIRED: root ClaimSnapshot changed")
        if current.observed_map_version > current_map.version:
            raise ProjectError("Governance clock observes a future ResearchMap version")
        if current.observed_map_hash == current_map.research_map_hash:
            return current
        delta = current_map.version - current.observed_map_version
        if delta < 1:
            raise ProjectError("Governance clock ResearchMap lineage mismatch")
        return self._advance(
            current,
            current_map,
            map_versions_since_last_review=current.map_versions_since_last_review + delta,
        )

    def _advance(
        self,
        current: ArchitectureReviewClock,
        current_map: ResearchMap,
        **updates: Any,
    ) -> ArchitectureReviewClock:
        values = {
            "sessions_since_last_review": current.sessions_since_last_review,
            "tactical_progress_since_last_review": current.tactical_progress_since_last_review,
            "structural_progress_since_last_review": current.structural_progress_since_last_review,
            "route_failure_counts": dict(current.route_failure_counts),
            "blocked_obligation_ages": dict(current.blocked_obligation_ages),
            "map_versions_since_last_review": current.map_versions_since_last_review,
            "candidate_repair_cycles_since_last_review": (
                current.candidate_repair_cycles_since_last_review
            ),
            "root_obstruction_unchanged_sessions": (current.root_obstruction_unchanged_sessions),
            "explicit_signals": current.explicit_signals,
            "last_review_id": current.last_review_id,
            "last_review_hash": current.last_review_hash,
        }
        values.update(updates)
        result = ArchitectureReviewClock.capture(
            clock_id=current.clock_id,
            revision=current.revision + 1,
            root_claim_snapshot_hash=current_map.root_claim_snapshot_hash,
            research_map_id=current_map.research_map_id,
            observed_map_version=current_map.version,
            observed_map_hash=current_map.research_map_hash,
            previous_clock_hash=current.clock_hash,
            thresholds=current.thresholds,
            recorded_at=utc_now(),
            **values,
        )
        self._persist_clock(result)
        return result

    def _persist_clock(self, clock: ArchitectureReviewClock) -> None:
        version_path = (
            self.clocks_root
            / clock.research_map_id
            / "versions"
            / f"{clock.revision:08d}-{digest_part(clock.clock_hash)[:16]}.json"
        )
        write_immutable_json(version_path, clock.to_dict())
        write_projection_json(self._clock_current_path(clock.research_map_id), clock.to_dict())

    def _clock_current_path(self, research_map_id: str) -> Path:
        return self.clocks_root / research_map_id / "current.json"
