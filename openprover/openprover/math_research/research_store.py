"""Filesystem-backed Research Plane facade for single-process PHASE 4 use."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterable, Mapping

from .directive import BudgetProfile, Directive, TacticalSession
from .project import ProjectError, ProjectStore, utc_now
from .research_common import (
    digest_part,
    read_json,
    require_hash,
    require_id,
    write_immutable_json,
    write_projection_json,
)
from .research_map import (
    MapRevisionReason,
    ObligationRef,
    ResearchMap,
    ResearchMapRebase,
)
from .research_obligation import (
    ObligationDisposition,
    ObligationDispositionKind,
    ResearchObligation,
)
from .route_failure import (
    RouteContext,
    RouteFailureRecord,
    route_failure_from_legacy_fingerprint,
)
from .truth_store import TruthStoreFacade, TruthValidationError


class ResearchMapRootStale(ProjectError):
    """A Research Plane operation attempted to use a stale root claim."""

    code = "RESEARCH_MAP_ROOT_STALE"
    disposition = "REVALIDATION_REQUIRED"

    def __init__(self, operation: str, detail: str):
        self.operation = operation
        self.detail = detail
        super().__init__(f"{self.code}: {operation}: {detail}")


class ResearchStoreFacade:
    """Canonical owner of PHASE 4 maps, obligations, and their projections.

    Writes are atomic within one process/filesystem. This facade deliberately
    does not claim database transactions or cross-process reconciliation.
    """

    def __init__(
        self,
        project: ProjectStore,
        *,
        truth_store: TruthStoreFacade | None = None,
        root_validation_kwargs: Mapping[str, Any] | None = None,
    ):
        self.project = project
        self.truth_store = truth_store or TruthStoreFacade(project)
        self.root_validation_kwargs = dict(root_validation_kwargs or {})
        self.root = project.root / "research"
        self.maps_root = self.root / "maps"
        self.obligations_root = self.root / "obligations"
        self.dispositions_root = self.root / "dispositions"
        self.rebases_root = self.root / "rebases"
        self.route_failures_root = self.root / "route_failures"
        self.directives_root = self.root / "directives"
        self.sessions_root = self.root / "sessions"
        self.index_root = self.root / "indexes"

    def record_route_failure(
        self,
        research_map_id: str,
        obligation_id: str,
        *,
        route_description: str,
        method_family: str,
        exact_failure_condition: str,
        failure_domain: str,
        evidence_refs: Iterable[str],
        reopen_conditions: Iterable[str],
        created_by: str,
        context: RouteContext | None = None,
    ) -> tuple[RouteFailureRecord, ResearchMap]:
        current = self.load_current_map(research_map_id)
        self._validate_root_hash(current.root_claim_snapshot_hash, "RECORD_ROUTE_FAILURE")
        ref = current.obligation_ref(obligation_id)
        route_context = context or self.route_context_for_snapshot(
            current.root_claim_snapshot_hash
        )
        record = RouteFailureRecord.capture(
            root_claim_snapshot_hash=current.root_claim_snapshot_hash,
            research_map_id=current.research_map_id,
            research_map_version=current.version,
            research_map_hash=current.research_map_hash,
            obligation_id=obligation_id,
            obligation_hash=ref.obligation_hash,
            route_description=route_description,
            method_family=method_family,
            context=route_context,
            exact_failure_condition=exact_failure_condition,
            failure_domain=failure_domain,
            evidence_refs=tuple(evidence_refs),
            reopen_conditions=tuple(reopen_conditions),
            provenance="NATIVE",
            created_at=utc_now(),
            created_by=created_by,
        )
        self._persist_route_failure(record)
        revised = self._attach_route_failure(current, record, created_by=created_by)
        return record, revised

    def import_legacy_strategy_fingerprint(
        self,
        research_map_id: str,
        obligation_id: str,
        legacy: Mapping[str, Any],
        *,
        created_by: str = "StrategyFingerprintCompatibilityAdapter",
    ) -> tuple[RouteFailureRecord, ResearchMap]:
        current = self.load_current_map(research_map_id)
        self._validate_root_hash(current.root_claim_snapshot_hash, "IMPORT_LEGACY_ROUTE_FAILURE")
        ref = current.obligation_ref(obligation_id)
        record = route_failure_from_legacy_fingerprint(
            legacy,
            root_claim_snapshot_hash=current.root_claim_snapshot_hash,
            research_map_id=current.research_map_id,
            research_map_version=current.version,
            research_map_hash=current.research_map_hash,
            obligation_id=obligation_id,
            obligation_hash=ref.obligation_hash,
            context=self.route_context_for_snapshot(current.root_claim_snapshot_hash),
            created_at=utc_now(),
        )
        self._persist_route_failure(record)
        revised = self._attach_route_failure(current, record, created_by=created_by)
        return record, revised

    def load_route_failure(self, route_failure_id: str) -> RouteFailureRecord:
        failure_id = require_id(route_failure_id, "route_failure_id")
        path = self.route_failures_root / f"{failure_id}.json"
        result = RouteFailureRecord.from_dict(read_json(path, "RouteFailureRecord"))
        if result.route_failure_id != failure_id:
            raise ProjectError("RouteFailureRecord filename/id mismatch")
        return result

    def route_context_for_snapshot(self, claim_snapshot_hash: str) -> RouteContext:
        snapshot = self.truth_store.load_claim_snapshot(claim_snapshot_hash)
        return RouteContext.capture(
            dependency_snapshot_hash=snapshot.dependency_snapshot_hash,
            assumption_snapshot_hash=snapshot.assumption_snapshot_hash,
            authority_context_hash=snapshot.authority_binding_hash,
        )

    def create_directive(
        self,
        research_map_id: str,
        obligation_id: str,
        *,
        tactical_goal: str,
        allowed_scope: Iterable[str],
        created_by: str,
        prohibited_routes: Iterable[str] = (),
        relevant_evidence_refs: Iterable[str] = (),
        failed_route_refs: Iterable[str] = (),
        requested_worker_roles: Iterable[str] = ("constructive",),
        budget_profile: BudgetProfile | None = None,
    ) -> Directive:
        current = self.load_current_map(research_map_id)
        self._validate_root_hash(current.root_claim_snapshot_hash, "CREATE_DIRECTIVE")
        ref = current.obligation_ref(obligation_id)
        if ref.disposition not in {
            ObligationDispositionKind.OPEN.value,
            ObligationDispositionKind.BLOCKED.value,
        }:
            raise ProjectError(
                f"Directive requires an OPEN/BLOCKED obligation, got {ref.disposition}"
            )
        directive = Directive.capture(
            research_map_id=current.research_map_id,
            research_map_version=current.version,
            research_map_hash=current.research_map_hash,
            obligation_id=ref.obligation_id,
            obligation_hash=ref.obligation_hash,
            root_claim_snapshot_hash=current.root_claim_snapshot_hash,
            tactical_goal=tactical_goal,
            allowed_scope=tuple(allowed_scope),
            prohibited_routes=tuple(prohibited_routes),
            relevant_evidence_refs=tuple(relevant_evidence_refs),
            failed_route_refs=tuple(failed_route_refs),
            requested_worker_roles=tuple(requested_worker_roles),
            budget_profile=budget_profile
            or BudgetProfile.capture(
                wall_clock_seconds=900,
                max_workers=3,
                max_provider_calls=100,
                reasoning_tier="standard",
            ),
            created_at=utc_now(),
            created_by=created_by,
        )
        path = self.directives_root / f"{directive.directive_id}.json"
        if path.exists():
            return self.load_directive(directive.directive_id)
        write_immutable_json(path, directive.to_dict())
        return directive

    def bind_tactical_session(
        self,
        directive_id: str,
        *,
        execution_run_id: str,
        parent_execution_run_id: str | None = None,
        execution_status: str = "CREATED",
    ) -> TacticalSession:
        directive = self.load_directive(directive_id)
        current = self.load_current_map(directive.research_map_id)
        self._validate_root_hash(directive.root_claim_snapshot_hash, "BIND_TACTICAL_SESSION")
        if current.research_map_hash != directive.research_map_hash:
            raise ProjectError("Directive map version is no longer current")
        if current.obligation_ref(directive.obligation_id).obligation_hash != (
            directive.obligation_hash
        ):
            raise ProjectError("Directive obligation semantic revision is no longer current")
        session = TacticalSession.capture(
            directive=directive,
            execution_run_id=execution_run_id,
            parent_execution_run_id=parent_execution_run_id,
            execution_status=execution_status,
            created_at=utc_now(),
        )
        path = self.sessions_root / session.tactical_session_id / "binding.json"
        if path.exists():
            return self.load_tactical_session(session.tactical_session_id)
        write_immutable_json(path, session.to_dict())
        return session

    def load_directive(self, directive_id: str) -> Directive:
        path = self.directives_root / f"{require_id(directive_id, 'directive_id')}.json"
        result = Directive.from_dict(read_json(path, "Directive"))
        if result.directive_id != directive_id:
            raise ProjectError("Directive filename/id mismatch")
        return result

    def load_tactical_session(self, tactical_session_id: str) -> TacticalSession:
        session_id = require_id(tactical_session_id, "tactical_session_id")
        value = read_json(self.sessions_root / session_id / "binding.json", "TacticalSession")
        directive = self.load_directive(value.get("directive_id", ""))
        result = TacticalSession.from_dict(value, directive)
        if result.tactical_session_id != session_id:
            raise ProjectError("TacticalSession filename/id mismatch")
        return result

    def create_initial_map(
        self,
        *,
        research_map_id: str,
        root_theorem_id: str,
        root_claim_snapshot_hash: str,
        obligations: Iterable[Mapping[str, Any]],
        created_by: str,
        strategic_thesis: str = "",
        structural_nodes: Iterable[str] = (),
        known_invariants: Iterable[str] = (),
        open_obstructions: Iterable[str] = (),
        unbounded_parameters: Iterable[str] = (),
        termination_mechanisms: Iterable[str] = (),
    ) -> ResearchMap:
        map_id = require_id(research_map_id, "research_map_id")
        snapshot = self.truth_store.load_claim_snapshot(root_claim_snapshot_hash)
        if snapshot.theorem_id != root_theorem_id:
            raise ProjectError("Initial ResearchMap root theorem/snapshot mismatch")
        self._validate_root_hash(root_claim_snapshot_hash, "CREATE_RESEARCH_MAP")
        if self.current_index_path(map_id).exists():
            raise ProjectError(f"ResearchMap already exists: {map_id}")
        now = utc_now()
        refs: list[ObligationRef] = []
        for raw in obligations:
            if not isinstance(raw, Mapping):
                raise ProjectError("Initial obligations must be objects")
            obligation = ResearchObligation.capture(
                obligation_id=raw["obligation_id"],
                root_claim_snapshot_hash=root_claim_snapshot_hash,
                created_in_map_version=1,
                title=raw["title"],
                statement=raw["statement"],
                obligation_kind=raw.get("obligation_kind", "OTHER"),
                scope=raw.get("scope", ()),
                dependencies=raw.get("dependencies", ()),
                created_at=now,
            )
            disposition = ObligationDisposition.capture(
                obligation_id=obligation.obligation_id,
                obligation_hash=obligation.obligation_hash,
                disposition=ObligationDispositionKind.OPEN.value,
                recorded_at=now,
                recorded_by=created_by,
                reason="initial research frontier",
            )
            self._persist_obligation(obligation)
            self._persist_disposition(disposition)
            refs.append(self._ref(obligation, disposition))
        research_map = ResearchMap.capture(
            research_map_id=map_id,
            version=1,
            root_theorem_id=root_theorem_id,
            root_claim_snapshot_hash=root_claim_snapshot_hash,
            obligation_refs=refs,
            structural_nodes=tuple(structural_nodes),
            known_invariants=tuple(known_invariants),
            open_obstructions=tuple(open_obstructions),
            unbounded_parameters=tuple(unbounded_parameters),
            termination_mechanisms=tuple(termination_mechanisms),
            strategic_thesis=strategic_thesis,
            added_scope=tuple(item.obligation_id for item in refs),
            obligation_changes=tuple(f"{item.obligation_id}:CREATED_OPEN" for item in refs),
            created_at=now,
            created_by=created_by,
            revision_reason=MapRevisionReason.INITIAL.value,
        )
        self._persist_map(research_map)
        return research_map

    def revise_map(
        self,
        parent: str | ResearchMap,
        *,
        obligation_refs: Iterable[ObligationRef] | None = None,
        created_by: str,
        revision_reason: str,
        added_scope: Iterable[str] = (),
        removed_or_reframed_scope: Iterable[str] = (),
        obligation_changes: Iterable[str] = (),
        route_memory_changes: Iterable[str] = (),
        evidence_refs: Iterable[str] = (),
        route_failure_refs: Iterable[str] | None = None,
        strategic_thesis: str | None = None,
        root_claim_snapshot_hash: str | None = None,
    ) -> ResearchMap:
        prior = self.load_current_map(parent) if isinstance(parent, str) else parent
        current = self.load_current_map(prior.research_map_id)
        if current.research_map_hash != prior.research_map_hash:
            raise ProjectError("ResearchMap revision must use the current immutable version")
        new_root = root_claim_snapshot_hash or prior.root_claim_snapshot_hash
        if new_root != prior.root_claim_snapshot_hash:
            raise ProjectError("Root changes require explicit rebase_research_map")
        self._validate_root_hash(prior.root_claim_snapshot_hash, "REVISE_RESEARCH_MAP")
        refs = tuple(obligation_refs) if obligation_refs is not None else prior.obligation_refs
        prior_ids = {item.obligation_id for item in prior.obligation_refs}
        next_ids = {item.obligation_id for item in refs}
        missing = sorted(prior_ids - next_ids)
        if missing:
            raise ProjectError(
                "NO_SCOPE_LOSS: map revision omitted obligations without an explicit retained "
                f"disposition projection: {missing}"
            )
        added_ids = next_ids - prior_ids
        declared_added = set(added_scope)
        if added_ids != declared_added:
            raise ProjectError(
                f"ResearchMap added scope mismatch: actual={sorted(added_ids)}, "
                f"declared={sorted(declared_added)}"
            )
        for ref in refs:
            obligation = self.load_obligation(ref.obligation_hash)
            disposition = self.load_disposition(ref.disposition_hash)
            if obligation.obligation_id != ref.obligation_id:
                raise ProjectError("ObligationRef semantic identity mismatch")
            if disposition.obligation_hash != obligation.obligation_hash:
                raise ProjectError("ObligationRef disposition targets another semantic revision")
            if disposition.disposition != ref.disposition:
                raise ProjectError("ObligationRef disposition projection mismatch")
            if obligation.root_claim_snapshot_hash != new_root:
                raise ProjectError("ObligationRef has a different root ClaimSnapshot")
        now = utc_now()
        result = ResearchMap.capture(
            research_map_id=prior.research_map_id,
            version=prior.version + 1,
            root_theorem_id=prior.root_theorem_id,
            root_claim_snapshot_hash=new_root,
            parent_version_ref=prior.research_map_hash,
            structural_nodes=prior.structural_nodes,
            relations=prior.relations,
            known_invariants=prior.known_invariants,
            open_obstructions=prior.open_obstructions,
            unbounded_parameters=prior.unbounded_parameters,
            termination_mechanisms=prior.termination_mechanisms,
            obligation_refs=refs,
            route_failure_refs=(
                tuple(route_failure_refs)
                if route_failure_refs is not None
                else prior.route_failure_refs
            ),
            strategic_thesis=(
                prior.strategic_thesis if strategic_thesis is None else strategic_thesis
            ),
            added_scope=tuple(added_scope),
            removed_or_reframed_scope=tuple(removed_or_reframed_scope),
            obligation_changes=tuple(obligation_changes),
            route_memory_changes=tuple(route_memory_changes),
            evidence_refs=tuple(evidence_refs),
            created_at=now,
            created_by=created_by,
            revision_reason=revision_reason,
        )
        self._persist_map(result)
        return result

    def record_disposition(
        self,
        research_map_id: str,
        obligation_id: str,
        *,
        disposition: str,
        recorded_by: str,
        revision_reason: str,
        blocker_refs: Iterable[str] = (),
        evidence_refs: Iterable[str] = (),
        route_failure_refs: Iterable[str] = (),
        resolution_basis: str = "",
        superseded_by: Iterable[str] = (),
        reason: str = "",
    ) -> tuple[ObligationDisposition, ResearchMap]:
        current = self.load_current_map(research_map_id)
        self._validate_root_hash(current.root_claim_snapshot_hash, "RECORD_DISPOSITION")
        old_ref = current.obligation_ref(obligation_id)
        prior = self.load_disposition(old_ref.disposition_hash)
        now = utc_now()
        decision = ObligationDisposition.capture(
            obligation_id=obligation_id,
            obligation_hash=old_ref.obligation_hash,
            disposition=disposition,
            blocker_refs=tuple(blocker_refs),
            evidence_refs=tuple(evidence_refs),
            route_failure_refs=tuple(route_failure_refs),
            resolution_basis=resolution_basis,
            superseded_by=tuple(superseded_by),
            reason=reason,
            previous_disposition_hash=prior.disposition_hash,
            recorded_at=now,
            recorded_by=recorded_by,
        )
        self._persist_disposition(decision)
        refs = [
            self._ref(self.load_obligation(item.obligation_hash), decision)
            if item.obligation_id == obligation_id
            else item
            for item in current.obligation_refs
        ]
        revised = self.revise_map(
            current,
            obligation_refs=refs,
            created_by=recorded_by,
            revision_reason=revision_reason,
            obligation_changes=(f"{obligation_id}:{prior.disposition}->{disposition}",),
            evidence_refs=tuple(evidence_refs),
            route_memory_changes=tuple(route_failure_refs),
        )
        return decision, revised

    def add_obligation(
        self,
        research_map_id: str,
        *,
        obligation_id: str,
        title: str,
        statement: str,
        obligation_kind: str,
        created_by: str,
        scope: Iterable[str] = (),
        dependencies: Iterable[str] = (),
    ) -> tuple[ResearchObligation, ResearchMap]:
        current = self.load_current_map(research_map_id)
        self._validate_root_hash(current.root_claim_snapshot_hash, "ADD_RESEARCH_OBLIGATION")
        if obligation_id in {item.obligation_id for item in current.obligation_refs}:
            raise ProjectError(f"ResearchObligation already exists in map: {obligation_id}")
        now = utc_now()
        obligation = ResearchObligation.capture(
            obligation_id=obligation_id,
            root_claim_snapshot_hash=current.root_claim_snapshot_hash,
            created_in_map_version=current.version + 1,
            title=title,
            statement=statement,
            obligation_kind=obligation_kind,
            scope=tuple(scope),
            dependencies=tuple(dependencies),
            created_at=now,
        )
        disposition = ObligationDisposition.capture(
            obligation_id=obligation.obligation_id,
            obligation_hash=obligation.obligation_hash,
            disposition=ObligationDispositionKind.OPEN.value,
            reason="new research scope",
            recorded_at=now,
            recorded_by=created_by,
        )
        self._persist_obligation(obligation)
        self._persist_disposition(disposition)
        revised = self.revise_map(
            current,
            obligation_refs=(*current.obligation_refs, self._ref(obligation, disposition)),
            created_by=created_by,
            revision_reason=MapRevisionReason.NEW_OBLIGATION.value,
            added_scope=(obligation_id,),
            obligation_changes=(f"{obligation_id}:CREATED_OPEN",),
        )
        return obligation, revised

    def rebase_research_map(
        self,
        research_map_id: str,
        *,
        new_claim_snapshot_hash: str,
        carried_obligation_ids: Iterable[str],
        revalidation_required_obligation_ids: Iterable[str],
        invalid_obligation_ids: Iterable[str],
        reason: str,
        created_by: str,
    ) -> tuple[ResearchMapRebase, ResearchMap]:
        current = self.load_current_map(research_map_id)
        new_snapshot = self.truth_store.load_claim_snapshot(new_claim_snapshot_hash)
        if new_snapshot.theorem_id != current.root_theorem_id:
            raise ProjectError("ResearchMap rebase cannot change root theorem id")
        comparison = self.truth_store.compare_claim_snapshot(current.root_claim_snapshot_hash)
        self._validate_root_hash(new_claim_snapshot_hash, "REBASE_TARGET")
        carried = tuple(carried_obligation_ids)
        revalidate = tuple(revalidation_required_obligation_ids)
        invalid = tuple(invalid_obligation_ids)
        all_ids = {item.obligation_id for item in current.obligation_refs}
        if set(carried) | set(revalidate) | set(invalid) != all_ids:
            raise ProjectError("ResearchMap rebase must classify every existing obligation")
        now = utc_now()
        next_refs: list[ObligationRef] = []
        for old_ref in current.obligation_refs:
            old = self.load_obligation(old_ref.obligation_hash)
            revised = ResearchObligation.capture(
                obligation_id=old.obligation_id,
                semantic_revision=old.semantic_revision + 1,
                root_claim_snapshot_hash=new_claim_snapshot_hash,
                created_in_map_version=old.created_in_map_version,
                title=old.title,
                statement=old.statement,
                obligation_kind=old.obligation_kind,
                scope=old.scope,
                dependencies=old.dependencies,
                previous_revision_hash=old.obligation_hash,
                created_at=old.created_at,
                revised_at=now,
            )
            previous = self.load_disposition(old_ref.disposition_hash)
            if old.obligation_id in invalid:
                state = ObligationDispositionKind.ABANDONED_WITH_REASON.value
                disposition_reason = f"invalidated by explicit root rebase: {reason}"
                blockers = ()
            elif old.obligation_id in revalidate:
                state = ObligationDispositionKind.BLOCKED.value
                disposition_reason = f"root rebase requires evidence revalidation: {reason}"
                blockers = (f"REVALIDATION_REQUIRED:{new_claim_snapshot_hash}",)
            else:
                state = previous.disposition
                disposition_reason = f"carried by explicit root rebase: {reason}"
                blockers = previous.blocker_refs
            decision = ObligationDisposition.capture(
                obligation_id=revised.obligation_id,
                obligation_hash=revised.obligation_hash,
                disposition=state,
                blocker_refs=blockers,
                evidence_refs=previous.evidence_refs if state == "RESOLVED" else (),
                route_failure_refs=previous.route_failure_refs,
                resolution_basis=previous.resolution_basis if state == "RESOLVED" else "",
                superseded_by=previous.superseded_by if state == "SUPERSEDED" else (),
                reason=disposition_reason,
                previous_disposition_hash=previous.disposition_hash,
                recorded_at=now,
                recorded_by=created_by,
            )
            self._persist_obligation(revised)
            self._persist_disposition(decision)
            next_refs.append(self._ref(revised, decision))
        resulting = ResearchMap.capture(
            research_map_id=current.research_map_id,
            version=current.version + 1,
            root_theorem_id=current.root_theorem_id,
            root_claim_snapshot_hash=new_claim_snapshot_hash,
            parent_version_ref=current.research_map_hash,
            structural_nodes=current.structural_nodes,
            relations=current.relations,
            known_invariants=current.known_invariants,
            open_obstructions=current.open_obstructions,
            unbounded_parameters=current.unbounded_parameters,
            termination_mechanisms=current.termination_mechanisms,
            obligation_refs=next_refs,
            route_failure_refs=current.route_failure_refs,
            strategic_thesis=current.strategic_thesis,
            removed_or_reframed_scope=invalid,
            obligation_changes=tuple(
                [*(f"{item}:CARRIED" for item in carried),
                 *(f"{item}:REVALIDATION_REQUIRED" for item in revalidate),
                 *(f"{item}:INVALID" for item in invalid)]
            ),
            created_at=now,
            created_by=created_by,
            revision_reason=MapRevisionReason.ROOT_REBASE.value,
        )
        rebase = ResearchMapRebase.capture(
            research_map_id=current.research_map_id,
            source_map_hash=current.research_map_hash,
            resulting_map_hash=resulting.research_map_hash,
            old_claim_snapshot_hash=current.root_claim_snapshot_hash,
            new_claim_snapshot_hash=new_claim_snapshot_hash,
            compatibility_status=comparison.status,
            compatibility_disposition=comparison.disposition,
            carried_obligation_ids=carried,
            revalidation_required_obligation_ids=revalidate,
            invalid_obligation_ids=invalid,
            reason=reason,
            created_at=now,
            created_by=created_by,
        )
        self._persist_map(resulting)
        write_immutable_json(
            self.rebases_root / f"{digest_part(rebase.rebase_hash)}.json", rebase.to_dict()
        )
        return rebase, resulting

    def load_current_map(self, research_map_id: str) -> ResearchMap:
        map_id = require_id(research_map_id, "research_map_id")
        index = read_json(self.current_index_path(map_id), "ResearchMap current projection")
        expected = {
            "schema_version", "object_type", "research_map_id", "version",
            "research_map_hash", "root_claim_snapshot_hash", "open_obligation_ids",
        }
        if set(index) != expected or index.get("schema_version") != 1 or index.get(
            "object_type"
        ) != "RESEARCH_MAP_CURRENT_PROJECTION":
            raise ProjectError("ResearchMap current projection migration is required")
        result = self.load_map(index["research_map_hash"])
        if result.research_map_id != map_id or result.version != index["version"]:
            raise ProjectError("ResearchMap current projection mismatch")
        if list(result.open_obligation_ids) != index["open_obligation_ids"]:
            raise ProjectError("ResearchMap current frontier projection mismatch")
        return result

    def load_map(self, research_map_hash: str) -> ResearchMap:
        digest = digest_part(research_map_hash, "research_map_hash")
        matches = list(self.maps_root.glob(f"*/versions/*-{digest}.json"))
        if len(matches) != 1:
            raise ProjectError(f"ResearchMap version not found or ambiguous: {research_map_hash}")
        result = ResearchMap.from_dict(read_json(matches[0], "ResearchMap"))
        if result.research_map_hash != research_map_hash:
            raise ProjectError("ResearchMap filename/hash mismatch")
        return result

    def load_obligation(self, obligation_hash: str) -> ResearchObligation:
        path = self.obligations_root / "revisions" / f"{digest_part(obligation_hash)}.json"
        result = ResearchObligation.from_dict(read_json(path, "ResearchObligation"))
        if result.obligation_hash != obligation_hash:
            raise ProjectError("ResearchObligation filename/hash mismatch")
        return result

    def load_disposition(self, disposition_hash: str) -> ObligationDisposition:
        path = self.dispositions_root / f"{digest_part(disposition_hash)}.json"
        result = ObligationDisposition.from_dict(read_json(path, "ObligationDisposition"))
        if result.disposition_hash != disposition_hash:
            raise ProjectError("ObligationDisposition filename/hash mismatch")
        return result

    def frontier_projection(self, research_map_id: str) -> dict[str, Any]:
        current = self.load_current_map(research_map_id)
        return {
            "research_map_id": current.research_map_id,
            "research_map_version": current.version,
            "research_map_hash": current.research_map_hash,
            "root_claim_snapshot_hash": current.root_claim_snapshot_hash,
            "open_obligation_ids": list(current.open_obligation_ids),
        }

    def current_index_path(self, research_map_id: str) -> Path:
        return self.maps_root / require_id(research_map_id, "research_map_id") / "current.json"

    def _persist_map(self, value: ResearchMap) -> None:
        path = (
            self.maps_root
            / value.research_map_id
            / "versions"
            / f"{value.version:08d}-{digest_part(value.research_map_hash)}.json"
        )
        write_immutable_json(path, value.to_dict())
        write_projection_json(
            self.current_index_path(value.research_map_id),
            {
                "schema_version": 1,
                "object_type": "RESEARCH_MAP_CURRENT_PROJECTION",
                "research_map_id": value.research_map_id,
                "version": value.version,
                "research_map_hash": value.research_map_hash,
                "root_claim_snapshot_hash": value.root_claim_snapshot_hash,
                "open_obligation_ids": list(value.open_obligation_ids),
            },
        )
        for ref in value.obligation_refs:
            write_projection_json(
                self.obligations_root / "current" / f"{ref.obligation_id}.json",
                {
                    "schema_version": 1,
                    "object_type": "RESEARCH_OBLIGATION_CURRENT_PROJECTION",
                    "obligation_id": ref.obligation_id,
                    "obligation_hash": ref.obligation_hash,
                    "disposition": ref.disposition,
                    "disposition_hash": ref.disposition_hash,
                    "research_map_id": value.research_map_id,
                    "research_map_version": value.version,
                    "root_claim_snapshot_hash": value.root_claim_snapshot_hash,
                },
            )

    def _persist_obligation(self, value: ResearchObligation) -> None:
        write_immutable_json(
            self.obligations_root / "revisions" / f"{digest_part(value.obligation_hash)}.json",
            value.to_dict(),
        )

    def _persist_disposition(self, value: ObligationDisposition) -> None:
        write_immutable_json(
            self.dispositions_root / f"{digest_part(value.disposition_hash)}.json",
            value.to_dict(),
        )

    def _persist_route_failure(self, value: RouteFailureRecord) -> None:
        write_immutable_json(
            self.route_failures_root / f"{value.route_failure_id}.json", value.to_dict()
        )
        self._index_references(
            {
                value.root_claim_snapshot_hash,
                value.dependency_snapshot_hash,
                value.assumption_snapshot_hash,
                value.authority_context_hash,
                *value.evidence_refs,
            },
            obligation_id=value.obligation_id,
            research_map_id=value.research_map_id,
            route_failure_id=value.route_failure_id,
        )

    def _attach_route_failure(
        self, current: ResearchMap, record: RouteFailureRecord, *, created_by: str
    ) -> ResearchMap:
        old_ref = current.obligation_ref(record.obligation_id)
        prior = self.load_disposition(old_ref.disposition_hash)
        decision = ObligationDisposition.capture(
            obligation_id=prior.obligation_id,
            obligation_hash=prior.obligation_hash,
            disposition=prior.disposition,
            blocker_refs=prior.blocker_refs,
            evidence_refs=prior.evidence_refs,
            route_failure_refs=tuple(
                dict.fromkeys((*prior.route_failure_refs, record.route_failure_id))
            ),
            resolution_basis=prior.resolution_basis,
            superseded_by=prior.superseded_by,
            reason=prior.reason,
            previous_disposition_hash=prior.disposition_hash,
            recorded_at=utc_now(),
            recorded_by=created_by,
        )
        self._persist_disposition(decision)
        refs = [
            ObligationRef.capture(
                item.obligation_id,
                item.obligation_hash,
                decision.disposition,
                decision.disposition_hash,
            )
            if item.obligation_id == record.obligation_id
            else item
            for item in current.obligation_refs
        ]
        return self.revise_map(
            current,
            obligation_refs=refs,
            created_by=created_by,
            revision_reason=MapRevisionReason.ROUTE_FAILURE.value,
            route_failure_refs=tuple(
                dict.fromkeys((*current.route_failure_refs, record.route_failure_id))
            ),
            route_memory_changes=(f"{record.obligation_id}:{record.route_failure_id}",),
            evidence_refs=record.evidence_refs,
        )

    def _index_references(
        self,
        references: Iterable[str],
        *,
        obligation_id: str,
        research_map_id: str,
        route_failure_id: str | None = None,
    ) -> None:
        path = self.index_root / "reverse_references.json"
        value = (
            read_json(path, "Research reverse-reference projection")
            if path.exists()
            else {
                "schema_version": 1,
                "object_type": "RESEARCH_REVERSE_REFERENCE_PROJECTION",
                "references": {},
            }
        )
        if set(value) != {"schema_version", "object_type", "references"} or value.get(
            "schema_version"
        ) != 1 or value.get("object_type") != "RESEARCH_REVERSE_REFERENCE_PROJECTION":
            raise ProjectError("Research reverse-reference projection migration is required")
        for reference in references:
            if not isinstance(reference, str) or not reference.strip():
                continue
            item = value["references"].setdefault(
                reference,
                {
                    "obligation_ids": [],
                    "research_map_ids": [],
                    "route_failure_ids": [],
                },
            )
            for key, member in (
                ("obligation_ids", obligation_id),
                ("research_map_ids", research_map_id),
                ("route_failure_ids", route_failure_id),
            ):
                if member and member not in item[key]:
                    item[key].append(member)
                    item[key].sort()
        write_projection_json(path, value)

    def affected_by_reference(self, reference: str) -> dict[str, list[str]]:
        path = self.index_root / "reverse_references.json"
        if not path.exists():
            return {
                "obligation_ids": [],
                "research_map_ids": [],
                "route_failure_ids": [],
            }
        value = read_json(path, "Research reverse-reference projection")
        result = value.get("references", {}).get(reference)
        if not isinstance(result, dict):
            return {
                "obligation_ids": [],
                "research_map_ids": [],
                "route_failure_ids": [],
            }
        return copy.deepcopy(result)

    def _validate_root_hash(self, root_hash: str, operation: str) -> None:
        require_hash(root_hash, "root_claim_snapshot_hash")
        try:
            self.truth_store.validate_snapshot_for_execution(
                root_hash, **self.root_validation_kwargs
            )
        except TruthValidationError as exc:
            raise ResearchMapRootStale(
                operation,
                f"{exc.comparison.status}/{exc.comparison.disposition}: "
                f"{exc.comparison.reason}",
            ) from exc

    @staticmethod
    def _ref(
        obligation: ResearchObligation, disposition: ObligationDisposition
    ) -> ObligationRef:
        return ObligationRef.capture(
            obligation.obligation_id,
            obligation.obligation_hash,
            disposition.disposition,
            disposition.disposition_hash,
        )


def classify_legacy_checkpoint_research_frontier(state: Mapping[str, Any]) -> str:
    """Fail closed when a checkpoint predates canonical ResearchMap bindings."""

    required = {
        "research_map_id",
        "research_map_version",
        "research_map_hash",
        "open_obligation_ids",
        "root_claim_snapshot_hash",
    }
    return "DIRECT_IMPORT" if required <= set(state) else "REVALIDATION_REQUIRED"


def research_checkpoint_projection(
    store: ResearchStoreFacade,
    research_map_id: str,
    *,
    active_directive_id: str | None = None,
    tactical_session_id: str | None = None,
) -> dict[str, Any]:
    projection = copy.deepcopy(store.frontier_projection(research_map_id))
    projection["active_directive_id"] = active_directive_id
    projection["tactical_session_id"] = tactical_session_id
    return projection
