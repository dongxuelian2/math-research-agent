from __future__ import annotations

from math_research_agent.research.project import ProjectStore
from math_research_agent.research.research_store import ResearchStoreFacade
from math_research_agent.research.routing import ModelRouter
from math_research_agent.research.route_failure import RouteContext
from math_research_agent.research.scheduler import StrategyFingerprint
from math_research_agent.research.truth_identity import domain_hash
from math_research_agent.research.truth_store import TruthStoreFacade


def _store(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "Route failures")
    project.add_theorem("T1", "Root", "P holds.")
    truth = TruthStoreFacade(project)
    snapshot = truth.capture_claim_snapshot("T1")
    store = ResearchStoreFacade(project, truth_store=truth)
    research_map = store.create_initial_map(
        research_map_id="map-T1",
        root_theorem_id="T1",
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        obligations=[
            {
                "obligation_id": oid,
                "title": oid,
                "statement": f"Resolve {oid}",
                "obligation_kind": "LEMMA",
            }
            for oid in ("O1", "O2")
        ],
        created_by="test",
    )
    return store, snapshot, research_map


def test_r10_route_failure_is_scoped_to_exact_obligation(tmp_path):
    store, snapshot, v1 = _store(tmp_path)
    failure, v2 = store.record_route_failure(
        v1.research_map_id,
        "O1",
        route_description="induction on the wrong parameter",
        method_family="INDUCTION",
        exact_failure_condition="the induction hypothesis does not control the boundary term",
        failure_domain="MATHEMATICAL",
        evidence_refs=("evidence:verifier-1",),
        reopen_conditions=("DEPENDENCY_SNAPSHOT_CHANGED", "NEW_VERIFIED_LEMMA"),
        created_by="test",
    )
    assert failure.obligation_id == "O1"
    assert failure.root_claim_snapshot_hash == snapshot.claim_snapshot_hash
    o1_decision = store.load_disposition(v2.obligation_ref("O1").disposition_hash)
    o2_decision = store.load_disposition(v2.obligation_ref("O2").disposition_hash)
    assert failure.route_failure_id in o1_decision.route_failure_refs
    assert o2_decision.route_failure_refs == ()
    assert {item.obligation_id for item in v2.obligation_refs} == {"O1", "O2"}


def test_r11_changed_dependency_can_deterministically_reopen_route(tmp_path):
    store, _, v1 = _store(tmp_path)
    failure, _ = store.record_route_failure(
        v1.research_map_id,
        "O1",
        route_description="dependency substitution",
        method_family="REDUCTION",
        exact_failure_condition="dependency D is too weak",
        failure_domain="DEPENDENCY",
        evidence_refs=(),
        reopen_conditions=("DEPENDENCY_SNAPSHOT_CHANGED",),
        created_by="test",
    )
    original = store.route_context_for_snapshot(v1.root_claim_snapshot_hash)
    assert failure.eligibility(original).status == "FAILURE_STILL_APPLIES"
    changed = RouteContext.capture(
        dependency_snapshot_hash=domain_hash("test_dependency", {"revision": 2}),
        assumption_snapshot_hash=original.assumption_snapshot_hash,
        authority_context_hash=original.authority_context_hash,
    )
    decision = failure.eligibility(changed)
    assert decision.status == "REOPENABLE"
    assert decision.changed_contexts == ("DEPENDENCY_SNAPSHOT_CHANGED",)
    assert store.load_route_failure(failure.route_failure_id) == failure


def test_r15_legacy_strategy_fingerprint_is_preserved_as_derived(tmp_path):
    store, _, v1 = _store(tmp_path)
    legacy = StrategyFingerprint(
        theorem="T1",
        branch="boundary",
        target_lemma="O1",
        method="induction",
        key_dependency="D1",
        failure_point="base case",
    ).to_dict()
    failure, v2 = store.import_legacy_strategy_fingerprint(v1.research_map_id, "O1", legacy)
    assert failure.provenance == "LEGACY_DERIVED"
    assert legacy["fingerprint"] in failure.legacy_source_ref
    assert failure.failure_domain == "UNKNOWN"
    assert failure.route_failure_id in v2.route_failure_refs


def test_r18_reverse_index_finds_affected_obligation_and_route(tmp_path):
    store, snapshot, v1 = _store(tmp_path)
    failure, _ = store.record_route_failure(
        v1.research_map_id,
        "O1",
        route_description="authority-dependent shortcut",
        method_family="EXTERNAL_AUTHORITY",
        exact_failure_condition="authority applicability was not established",
        failure_domain="AUTHORITY",
        evidence_refs=("evidence:authority-audit",),
        reopen_conditions=("AUTHORITY_CONTEXT_CHANGED",),
        created_by="test",
    )
    affected = store.affected_by_reference(snapshot.authority_binding_hash)
    assert affected["obligation_ids"] == ["O1"]
    assert affected["research_map_ids"] == ["map-T1"]
    assert affected["route_failure_ids"] == [failure.route_failure_id]


def test_model_router_route_resolution_does_not_create_research_strategy_state():
    router = ModelRouter(
        {
            "tiers": {
                "routine": {"provider": "mock", "model": "routine"},
                "research": {"provider": "mock", "model": "research"},
                "strategic": {"provider": "mock", "model": "strategic"},
            },
            "roles": {"worker": "research"},
        }
    )
    route = router.resolve("worker", obligation_id="O1", reserve=False)
    assert route.tier == "research"
    assert router.snapshot()["obligations"] == {}
