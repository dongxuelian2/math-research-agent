"""Provider-neutral role routing, escalation, budgets, and call accounting."""

from __future__ import annotations

import copy
import json
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .project import ProjectError, utc_now
from .gemini_tools import build_tool_payload


TIERS = ("routine", "research", "strategic")
TIER_INDEX = {name: index for index, name in enumerate(TIERS)}
ROLE_CONFIG_ALIASES = {
    "constructive": ("worker",),
    "theorem_verifier": ("worker", "auditor"),
    "worker_verifier": ("worker", "auditor"),
    "reconstruction": ("worker",),
    "counterexample_hunter": ("counterexample", "auditor"),
    "dependency_auditor": ("auditor",),
    "exhaustiveness_auditor": ("auditor",),
    "boundary_auditor": ("auditor",),
    "literature_authority_auditor": ("auditor",),
    "secondary_verifier": ("auditor",),
    "final_proof_auditor": ("final_auditor",),
    "formalization_agent": ("worker",),
    "literature_lead": ("worker",),
    "literature_searcher": ("worker",),
    "literature_reader": ("worker",),
    "literature_deep_reader": ("worker",),
    "literature_synthesizer": ("worker",),
}


def role_config_names(role: str) -> tuple[str, ...]:
    return (role, *ROLE_CONFIG_ALIASES.get(role, ()))


# This is the single source of truth for default role classification.  Config
# layers may override it, but provider/model values never live in this map.
DEFAULT_ROLE_TIERS = {
    "planner": "strategic",
    "architecture_audit": "strategic",
    "constructive": "research",
    "worker": "research",
    "theorem_verifier": "research",
    "counterexample_hunter": "research",
    "exhaustiveness_auditor": "research",
    "boundary_auditor": "research",
    "literature_lead": "research",
    "literature_synthesizer": "research",
    "literature_deep_reader": "research",
    "reconstruction": "routine",
    "worker_verifier": "routine",
    "dependency_auditor": "routine",
    "literature_searcher": "routine",
    "literature_reader": "routine",
    "literature_authority_auditor": "routine",
    "final_proof_auditor": "strategic",
    "formalization_agent": "research",
    "secondary_verifier": "research",
}

FAILURE_KINDS = frozenset(
    {
        "NO_PROGRESS",
        "VERIFIER_REJECTION",
        "REPEATED_FAILED_ROUTE",
        "MALFORMED_RESULT",
        "AUTHORITY_FAILURE",
        "MATHEMATICAL_OBSTRUCTION",
        "PROVIDER_FAILURE",
    }
)

TIER_POLICIES = {
    "routine": (
        "Tier policy (routine): perform the bounded task precisely; check every "
        "condition; state uncertainty explicitly; do not invent unnecessary theory."
    ),
    "research": (
        "Tier policy (research): seek substantive progress, explicit local lemmas, "
        "and more than one plausible route when useful; preserve authority and "
        "reconstruction discipline."
    ),
    "strategic": (
        "Tier policy (strategic): reconsider formulation, invariant, normal form, "
        "parameterization, case split, literature framing, and proof architecture. "
        "Do not merely extend a route that already failed."
    ),
}

ROLE_POLICIES = {
    "architecture_audit": (
        "Build the current theorem/lemma dependency DAG; classify nodes as essential, "
        "subsumed, duplicated, presentation-only, or removable; identify stronger "
        "theorems, shared mechanisms, master lemmas, and better invariants/normal forms; "
        "compare current and proposed DAGs and give a minimal proof skeleton. Do not "
        "change theorem truth state. Every proposed architecture must be reverified."
    ),
    "worker_verifier": (
        "Verify the assigned Worker result independently. A model verdict is not proof "
        "truth; preserve deterministic gates and report uncertainty explicitly."
    ),
    "theorem_verifier": (
        "Perform theorem-level verification, reconstruct dependencies, and keep authority "
        "and mathematical verdicts separate from infrastructure status."
    ),
    "final_proof_auditor": (
        "Perform final synthesis only after every required deterministic and specialist "
        "gate; never promote theorem truth merely because a model says CORRECT."
    ),
    "formalization_agent": (
        "Translate only the authorized theorem scope. Use Lean tools to compile the "
        "exact source and never report VERIFIED without an observed successful compiler call."
    ),
}


def normalize_role(role: str) -> str:
    return str(role or "worker").strip().casefold().replace(" ", "_")


def normalize_tier(tier: str | None, *, default: str = "research") -> str:
    value = str(tier or default).strip().casefold()
    if value not in TIER_INDEX:
        raise ProjectError(f"Unknown model tier: {tier!r}")
    return value


def higher_tier(first: str, second: str) -> str:
    return TIERS[max(TIER_INDEX[normalize_tier(first)], TIER_INDEX[normalize_tier(second)])]


def compose_system_prompt(
    base_prompt: str,
    *,
    role: str,
    tier: str,
    obligation_context: str = "",
    failed_route_context: str = "",
    literature_context: str = "",
) -> str:
    """Compose bounded prompt layers without cloning complete prompts per tier."""

    layers = [base_prompt.rstrip(), f"Role: {role}"]
    role_policy = ROLE_POLICIES.get(normalize_role(role))
    if role_policy:
        layers.append("Role policy: " + role_policy)
    layers.append(TIER_POLICIES[tier])
    if obligation_context:
        layers.append("Obligation context:\n" + obligation_context.strip())
    if failed_route_context:
        layers.append(
            "Failed-route context (change the route materially):\n" + failed_route_context.strip()
        )
    if literature_context:
        layers.append("Verified literature context:\n" + literature_context.strip())
    return "\n\n".join(layer for layer in layers if layer)


@dataclass(frozen=True, slots=True)
class ModelRoute:
    requested_tier: str
    tier: str
    role: str
    provider: str
    model: str | None
    reasoning_effort: str | None
    config: dict[str, Any] = field(repr=False)
    escalation_level: int = 0
    escalation_reason: str | None = None
    fallback: bool = False
    fallback_reason: str | None = None
    requested_provider: str | None = None
    requested_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("config", None)
        return value


def initialize_routing_state(raw: dict | None) -> dict:
    """Create the current routing state; older snapshots are not accepted."""

    value = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    if raw is not None and value.get("schema_version") != 2:
        raise ProjectError(
            "Unsupported routing state schema; delete the snapshot and start a new run"
        )
    value["schema_version"] = 2
    value.setdefault("next_call_number", 1)
    value.setdefault("obligations", {})
    value.setdefault("calls", [])
    value.setdefault(
        "usage_by_tier",
        {
            tier: {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
            }
            for tier in TIERS
        },
    )
    for tier in TIERS:
        value["usage_by_tier"].setdefault(
            tier,
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
            },
        )
    value.setdefault("calls_by_model", {})
    value.setdefault("calls_by_role", {})
    value.setdefault("escalations", 0)
    value.setdefault("escalations_by_reason", {})
    value.setdefault("fallbacks", 0)
    value.setdefault("strategic_interventions", 0)
    value.setdefault("verifier_disagreements", 0)
    value.setdefault(
        "strategic_reservations",
        {
            "total": 0,
            "by_step": {},
            "by_obligation": {},
        },
    )
    return value


class ModelRouter:
    """Resolve provider/model/reasoning routes and compute escalation.

    The per-obligation mutation methods remain as deprecated compatibility
    adapters for schema-2 checkpoints and direct callers. Production Research
    Plane outcomes are no longer sent to them; RouteFailureRecord and
    ResearchMap own long-term strategy memory.
    """

    def __init__(
        self,
        config: dict,
        *,
        state: dict | None = None,
        state_path: str | Path | None = None,
        campaign_override: dict | None = None,
        project_override: dict | None = None,
    ):
        self.config = copy.deepcopy(config)
        self.layers = [
            self.config,
            copy.deepcopy(campaign_override or {}),
            copy.deepcopy(project_override or {}),
        ]
        self.state_path = Path(state_path) if state_path else None
        if state is None and self.state_path and self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.state = initialize_routing_state(state)
        self._lock = threading.RLock()
        self._save()

    def default_tier(self, role: str) -> str:
        role = normalize_role(role)
        tier = DEFAULT_ROLE_TIERS.get(role, "research")
        for layer in self.layers:
            mappings = []
            if isinstance(layer.get("role_tiers"), dict):
                mappings.append(layer["role_tiers"])
            if isinstance(layer.get("roles"), dict):
                mappings.append(layer["roles"])
            for mapping in mappings:
                raw = mapping.get(role)
                if isinstance(raw, str):
                    tier = normalize_tier(raw)
                elif isinstance(raw, dict) and raw.get("default_tier"):
                    tier = normalize_tier(raw["default_tier"])
        if role == "planner":
            mode = self._routing_value("planner_mode", "strategic")
            if mode == "research":
                tier = "research"
            elif mode == "adaptive":
                tier = "research"
            elif mode != "strategic":
                raise ProjectError(f"Unknown planner_mode: {mode!r}")
        return tier

    def resolve(
        self,
        role: str,
        *,
        obligation_id: str | None = None,
        requested_tier: str | None = None,
        escalation_reason: str | None = None,
        step_id: str | None = None,
        reserve: bool = True,
    ) -> ModelRoute:
        role = normalize_role(role)
        requested = normalize_tier(requested_tier, default=self.default_tier(role))
        # Merely resolving a model route must not create durable research state.
        # Entries exist only for explicit legacy compute-escalation hints.
        obligation = self.state["obligations"].get(obligation_id) if obligation_id else None
        tier = requested
        if obligation:
            tier = higher_tier(tier, obligation.get("tier", requested))
            escalation_reason = escalation_reason or obligation.get("last_escalation_reason")
        exact = self._role_config(role)
        route_config = (
            exact if exact is not None and not self._has_tiers() else self._tier_config(tier)
        )
        route_config = self._apply_role_override(role, route_config)
        requested_provider = (
            route_config.get("provider") if isinstance(route_config, dict) else None
        )
        requested_model = route_config.get("model") if isinstance(route_config, dict) else None
        fallback = False
        fallback_reason = None

        if not route_config or route_config.get("enabled", True) is False:
            fallback_tier = normalize_tier(self._routing_value("fallback_tier", "research"))
            route_config = self._tier_config(fallback_tier) or exact
            if not route_config or route_config.get("enabled", True) is False:
                raise ProjectError(
                    f"No enabled model route for {role}/{tier}, and fallback {fallback_tier} is unavailable"
                )
            fallback = True
            fallback_reason = f"requested tier {tier} is disabled or unconfigured"
            tier = fallback_tier

        if tier == "strategic" and not self._strategic_budget_available(
            step_id=step_id, obligation_id=obligation_id
        ):
            fallback_config = self._tier_config("research") or exact
            if not fallback_config:
                raise ProjectError(
                    "Strategic budget exhausted and no research fallback is configured"
                )
            route_config = self._apply_role_override(role, fallback_config)
            tier = "research"
            fallback = True
            fallback_reason = "strategic call cap reached"

        self._validate_route_shape(route_config, role=role, tier=tier)
        route = ModelRoute(
            requested_tier=requested,
            tier=tier,
            role=role,
            provider=str(route_config["provider"]),
            model=route_config.get("model"),
            reasoning_effort=route_config.get("reasoning_effort"),
            config=copy.deepcopy(route_config),
            escalation_level=TIER_INDEX[tier],
            escalation_reason=escalation_reason,
            fallback=fallback,
            fallback_reason=fallback_reason,
            requested_provider=requested_provider,
            requested_model=requested_model,
        )
        if reserve and route.tier == "strategic":
            self._reserve_strategic(step_id=step_id, obligation_id=obligation_id)
        return route

    def runtime_fallback(
        self,
        failed_route: ModelRoute,
        *,
        reason: str,
        obligation_id: str | None = None,
    ) -> ModelRoute:
        config = self._tier_config("research") or self._role_config(failed_route.role)
        if not config:
            raise ProjectError(f"No research fallback for failed route: {reason}")
        config = self._apply_role_override(failed_route.role, config)
        self._validate_route_shape(config, role=failed_route.role, tier="research")
        return ModelRoute(
            requested_tier=failed_route.requested_tier,
            tier="research",
            role=failed_route.role,
            provider=str(config["provider"]),
            model=config.get("model"),
            reasoning_effort=config.get("reasoning_effort"),
            config=copy.deepcopy(config),
            escalation_level=TIER_INDEX["research"],
            escalation_reason=failed_route.escalation_reason,
            fallback=True,
            fallback_reason=reason,
            requested_provider=failed_route.requested_provider or failed_route.provider,
            requested_model=failed_route.requested_model or failed_route.model,
        )

    def begin_call(
        self,
        route: ModelRoute,
        *,
        obligation_id: str | None,
        branch_id: str | None,
        parent_call_id: str | None = None,
    ) -> dict:
        with self._lock:
            number = int(self.state["next_call_number"])
            self.state["next_call_number"] = number + 1
            metadata = {
                "call_id": f"call-{number:08d}",
                "parent_call_id": parent_call_id,
                "obligation_id": obligation_id,
                "role": route.role,
                "requested_tier": route.requested_tier,
                "requested_provider": route.requested_provider,
                "requested_model": route.requested_model,
                "tier": route.tier,
                "provider": route.provider,
                "model": route.model,
                "actual_tier": route.tier,
                "actual_provider": route.provider,
                "actual_model": route.model,
                "reasoning_effort": route.reasoning_effort,
                "escalation_level": route.escalation_level,
                "escalation_reason": route.escalation_reason,
                "branch_id": branch_id,
                "fallback": route.fallback,
                "fallback_reason": route.fallback_reason,
                "created_at": utc_now(),
                "status": "ACTIVE",
            }
            self.state["calls"].append(metadata)
            self._save()
            return copy.deepcopy(metadata)

    def finish_call(
        self,
        call_id: str,
        *,
        response: dict | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            record = next(
                (item for item in reversed(self.state["calls"]) if item["call_id"] == call_id),
                None,
            )
            if record is None:
                raise ProjectError(f"Unknown routing call: {call_id}")
            if record.get("status") != "ACTIVE":
                return
            record["status"] = "ERROR" if error else "COMPLETE"
            record["completed_at"] = utc_now()
            if error:
                record["error"] = str(error)
            usage = (response or {}).get("usage") or {}
            normalized_usage = {
                key: int(usage.get(key, 0) or 0)
                for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")
            }
            record["usage"] = normalized_usage
            tier_usage = self.state["usage_by_tier"][record["tier"]]
            tier_usage["calls"] = int(tier_usage.get("calls", 0)) + 1
            for key, value in normalized_usage.items():
                tier_usage[key] = int(tier_usage.get(key, 0)) + value
            model_key = str(record.get("model") or "provider-default")
            self.state["calls_by_model"][model_key] = (
                int(self.state["calls_by_model"].get(model_key, 0)) + 1
            )
            role_key = record["role"]
            self.state["calls_by_role"][role_key] = (
                int(self.state["calls_by_role"].get(role_key, 0)) + 1
            )
            if record.get("fallback"):
                self.state["fallbacks"] = int(self.state.get("fallbacks", 0)) + 1
            self._save()

    def escalate(
        self,
        obligation_id: str,
        *,
        reason: str,
        minimum_tier: str | None = None,
        previous_route: str | None = None,
        failure_detail: str | None = None,
        material_change_required: str | None = None,
    ) -> dict:
        with self._lock:
            obligation = self._obligation(obligation_id)
            current = normalize_tier(obligation.get("tier"), default="routine")
            target = (
                normalize_tier(minimum_tier)
                if minimum_tier
                else TIERS[min(2, TIER_INDEX[current] + 1)]
            )
            target = higher_tier(current, target)
            changed = target != current
            obligation["tier"] = target
            obligation["last_escalation_reason"] = reason
            obligation.setdefault("escalation_history", []).append(
                {
                    "from": current,
                    "to": target,
                    "reason": reason,
                    "previous_route": previous_route,
                    "why_it_failed": failure_detail,
                    "what_must_materially_change": material_change_required,
                    "at": utc_now(),
                }
            )
            if changed:
                self.state["escalations"] = int(self.state.get("escalations", 0)) + 1
                reasons = self.state["escalations_by_reason"]
                reasons[reason] = int(reasons.get(reason, 0)) + 1
                if target == "strategic":
                    self.state["strategic_interventions"] = (
                        int(self.state.get("strategic_interventions", 0)) + 1
                    )
            self._save()
            return copy.deepcopy(obligation)

    def record_failure(self, obligation_id: str, failure_kind: str, *, detail: str = "") -> dict:
        """Deprecated compatibility compute-escalation heuristic.

        This method must not be used as Research Plane failure memory.
        """
        kind = str(failure_kind).strip().upper()
        if kind not in FAILURE_KINDS:
            raise ProjectError(f"Unknown failure kind: {failure_kind}")
        with self._lock:
            obligation = self._obligation(obligation_id)
            counters = obligation.setdefault("failure_counters", {})
            counters[kind] = int(counters.get(kind, 0)) + 1
            obligation.setdefault("failure_history", []).append(
                {"kind": kind, "detail": detail, "at": utc_now()}
            )
            current = normalize_tier(obligation.get("tier"), default="routine")
            threshold_key = (
                "routine_failure_threshold"
                if current == "routine"
                else "research_failure_threshold"
            )
            default_threshold = 2 if current == "routine" else 3
            threshold = int(self._routing_value(threshold_key, default_threshold))
            total_at_tier = sum(int(value) for value in counters.values())
            self._save()
        if current != "strategic" and total_at_tier >= threshold:
            return self.escalate(
                obligation_id,
                reason="repeated_failure",
                previous_route=kind,
                failure_detail=detail,
                material_change_required=(
                    "Change invariant, normal form, parameterization, case split, "
                    "literature framing, or proof architecture."
                ),
            )
        return copy.deepcopy(obligation)

    def record_verifier_disagreement(
        self, obligation_id: str, *, worker_verdict: str, verifier_verdict: str
    ) -> dict:
        """Deprecated compatibility compute-escalation heuristic."""
        with self._lock:
            obligation = self._obligation(obligation_id)
            obligation["verifier_disagreements"] = (
                int(obligation.get("verifier_disagreements", 0)) + 1
            )
            self.state["verifier_disagreements"] = (
                int(self.state.get("verifier_disagreements", 0)) + 1
            )
            self._save()
        return self.escalate(
            obligation_id,
            reason="worker_verifier_disagreement",
            failure_detail=f"worker={worker_verdict}; verifier={verifier_verdict}",
            material_change_required="Reconstruct the disputed inference independently.",
        )

    def record_frontier_cycle(self, frontier_id: str, *, progress: dict[str, bool]) -> dict:
        """Deprecated compatibility heuristic; not a research-stall owner."""
        meaningful = any(
            bool(progress.get(key))
            for key in (
                "branch_closure",
                "parameter_reduction",
                "stronger_invariant",
                "verified_lemma",
                "dependency_simplification",
            )
        )
        with self._lock:
            obligation = self._obligation(frontier_id)
            obligation["stalled_cycles"] = (
                0 if meaningful else int(obligation.get("stalled_cycles", 0)) + 1
            )
            stalled = obligation["stalled_cycles"]
            self._save()
        threshold = int(self._routing_value("stalled_frontier_cycles", 3))
        if stalled >= threshold:
            return self.escalate(
                frontier_id,
                reason="stalled_frontier",
                minimum_tier="strategic",
                material_change_required=(
                    "Reclassify or change invariant, parameterization, normal form, "
                    "case split, architecture, or literature framing."
                ),
            )
        return copy.deepcopy(obligation)

    def promote_high_value(
        self, obligation_id: str, *, theorem_level: bool = False, proof_candidate: bool = False
    ) -> dict:
        """Deprecated compatibility compute hint for direct legacy callers."""
        minimum = "strategic" if theorem_level or proof_candidate else "research"
        reason = "proof_candidate" if proof_candidate else "high_value_result"
        return self.escalate(obligation_id, reason=reason, minimum_tier=minimum)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.state)

    def _obligation(self, obligation_id: str | None) -> dict:
        if not obligation_id:
            raise ProjectError("obligation_id is required")
        obligations = self.state["obligations"]
        if obligation_id not in obligations:
            obligations[obligation_id] = {
                "tier": "routine",
                "failure_counters": {},
                "escalation_history": [],
                "verifier_disagreements": 0,
                "stalled_cycles": 0,
                "created_at": utc_now(),
            }
        return obligations[obligation_id]

    def _routing_value(self, key: str, default: Any) -> Any:
        value = default
        for layer in self.layers:
            routing = layer.get("routing")
            if isinstance(routing, dict) and key in routing:
                value = routing[key]
        return value

    def _has_tiers(self) -> bool:
        return any(isinstance(layer.get("tiers"), dict) and layer["tiers"] for layer in self.layers)

    def _tier_config(self, tier: str) -> dict | None:
        merged: dict[str, Any] = {}
        found = False
        for layer in self.layers:
            tiers = layer.get("tiers")
            if isinstance(tiers, dict) and isinstance(tiers.get(tier), dict):
                merged.update(copy.deepcopy(tiers[tier]))
                found = True
            if not tiers and layer.get("provider"):
                merged.update(
                    {
                        key: copy.deepcopy(value)
                        for key, value in layer.items()
                        if key not in {"roles", "routing", "budget", "tiers"}
                    }
                )
                found = True
        return merged if found else None

    def _role_config(self, role: str) -> dict | None:
        """Return the exact role entry, or an explicit legacy compatibility alias."""

        result = None
        for layer in self.layers:
            roles = layer.get("roles")
            if not isinstance(roles, dict):
                continue
            candidate = next(
                (
                    roles.get(name)
                    for name in role_config_names(role)
                    if roles.get(name) is not None
                ),
                None,
            )
            if isinstance(candidate, dict) and candidate.get("provider"):
                result = copy.deepcopy(candidate)
        return result

    def _apply_role_override(self, role: str, route: dict | None) -> dict:
        merged = copy.deepcopy(route or {})
        for layer in self.layers:
            overrides = layer.get("role_overrides")
            if isinstance(overrides, dict) and isinstance(overrides.get(role), dict):
                merged.update(copy.deepcopy(overrides[role]))
            roles = layer.get("roles")
            if isinstance(roles, dict):
                candidate = next(
                    (
                        roles.get(name)
                        for name in role_config_names(role)
                        if roles.get(name) is not None
                    ),
                    None,
                )
                if isinstance(candidate, dict) and not candidate.get("provider"):
                    merged.update(
                        {
                            key: copy.deepcopy(value)
                            for key, value in candidate.items()
                            if key != "default_tier"
                        }
                    )
            tool_map = layer.get("tools")
            if isinstance(tool_map, dict):
                tool_role = next(
                    (name for name in role_config_names(role) if name in tool_map), None
                )
                if tool_role is not None:
                    merged["tools"] = copy.deepcopy(tool_map[tool_role])
        return merged

    @staticmethod
    def _validate_route_shape(route: dict, *, role: str, tier: str) -> None:
        provider = route.get("provider")
        if not isinstance(provider, str) or not provider.strip():
            raise ProjectError(f"Route {role}/{tier} requires a provider")
        model = route.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ProjectError(f"Route {role}/{tier} model must be null or non-empty")
        effort = route.get("reasoning_effort")
        if effort is not None and not isinstance(effort, str):
            raise ProjectError(f"Route {role}/{tier} reasoning_effort must be a string")

    def _strategic_budget_available(
        self, *, step_id: str | None, obligation_id: str | None
    ) -> bool:
        reservations = self.state["strategic_reservations"]
        maximum = int(self._routing_value("max_strategic_calls", 1000000))
        per_step = int(self._routing_value("max_strategic_calls_per_step", maximum))
        per_obligation = int(self._routing_value("max_strategic_calls_per_obligation", maximum))
        return (
            int(reservations.get("total", 0)) < maximum
            and int(reservations["by_step"].get(step_id or "<none>", 0)) < per_step
            and int(reservations["by_obligation"].get(obligation_id or "<none>", 0))
            < per_obligation
        )

    def _reserve_strategic(self, *, step_id: str | None, obligation_id: str | None) -> None:
        with self._lock:
            reservations = self.state["strategic_reservations"]
            reservations["total"] = int(reservations.get("total", 0)) + 1
            step_key = step_id or "<none>"
            obligation_key = obligation_id or "<none>"
            reservations["by_step"][step_key] = int(reservations["by_step"].get(step_key, 0)) + 1
            reservations["by_obligation"][obligation_key] = (
                int(reservations["by_obligation"].get(obligation_key, 0)) + 1
            )
            self._save()

    def _save(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.state_path)


_ROLE_HEADER = re.compile(r"\[Worker role:\s*([^\]]+)\]", re.IGNORECASE)
_OBLIGATION_HEADER = re.compile(r"\[Obligation ID:\s*([^\]]+)\]", re.IGNORECASE)
_BRANCH_HEADER = re.compile(r"\[Branch ID:\s*([^\]]+)\]", re.IGNORECASE)


class RoutedLLMClient:
    """OpenProver-compatible dispatch client selecting a route per call."""

    def __init__(
        self,
        router: ModelRouter,
        *,
        client_factory: Callable[..., Any],
        default_role: str,
        archive_dir: Path,
        working_dir: Path,
    ):
        self.router = router
        self.client_factory = client_factory
        self.default_role = normalize_role(default_role)
        self.archive_dir = Path(archive_dir)
        self.working_dir = Path(working_dir)
        self._clients: dict[tuple, Any] = {}
        self._lock = threading.RLock()
        self.call_count = 0
        self.request_count = 0
        self.total_retries = 0
        self.total_cost = 0.0
        self.billing_mode = "gemini_heterogeneous"
        default_route = router.resolve(self.default_role, reserve=False)
        self.model = default_route.model or f"{default_route.provider}-default"
        self.context_length = int(default_route.config.get("context_length", 200_000))
        self.answer_reserve = int(default_route.config.get("answer_reserve", 4096))

    @property
    def total_usage(self) -> dict:
        totals: dict[str, int | bool] = {}
        for client in self._clients.values():
            usage = getattr(client, "total_usage", None)
            if not isinstance(usage, dict):
                continue
            for key, value in usage.items():
                if isinstance(value, bool):
                    totals[key] = bool(totals.get(key, False) or value)
                else:
                    totals[key] = int(totals.get(key, 0)) + int(value)
        return totals

    @property
    def api_request_count(self) -> int:
        return sum(int(getattr(client, "request_count", 0)) for client in self._clients.values())

    def call(self, prompt: str, system_prompt: str, **kwargs) -> dict:
        label = str(kwargs.get("label", ""))
        response_contract = kwargs.pop("response_schema", None)
        if response_contract is not None:
            if kwargs.get("json_schema") is not None:
                raise ProjectError("Pass only one of json_schema or response_schema")
            # Keep the typed provider contract intact. The Gemini adapter is
            # the single place that materializes its JSON Schema.
            kwargs["response_schema"] = response_contract
        role, obligation_id, branch_id = self._detect_context(prompt, label)
        step_id = self._step_id(label)
        route = self.router.resolve(
            role,
            obligation_id=obligation_id,
            step_id=step_id,
        )
        kwargs = self._with_route_tools(route, kwargs)
        layered_system = compose_system_prompt(
            system_prompt,
            role=role,
            tier=route.tier,
        )
        return self._execute_route(
            route,
            method="call",
            prompt=prompt,
            system_prompt=layered_system,
            obligation_id=obligation_id,
            branch_id=branch_id,
            kwargs=kwargs,
            raw_system_prompt=system_prompt,
        )

    def chat(self, messages: list[dict], **kwargs) -> dict:
        label = str(kwargs.get("label", ""))
        response_contract = kwargs.pop("response_schema", None)
        if response_contract is not None:
            if kwargs.get("json_schema") is not None:
                raise ProjectError("Pass only one of json_schema or response_schema")
            kwargs["response_schema"] = response_contract
        visible = "\n".join(str(item.get("content", "")) for item in messages)
        role, obligation_id, branch_id = self._detect_context(visible, label)
        route = self.router.resolve(
            role,
            obligation_id=obligation_id,
            step_id=self._step_id(label),
        )
        kwargs = self._with_route_tools(route, kwargs)
        layered = copy.deepcopy(messages)
        policy_text = "\n\n".join(
            filter(
                None,
                (
                    f"Role: {role}",
                    ROLE_POLICIES.get(role),
                    TIER_POLICIES[route.tier],
                ),
            )
        )
        policy = {"role": "developer", "content": policy_text}
        insert_at = 1 if layered and layered[0].get("role") == "system" else 0
        layered.insert(insert_at, policy)
        return self._execute_route(
            route,
            method="chat",
            messages=layered,
            obligation_id=obligation_id,
            branch_id=branch_id,
            kwargs=kwargs,
        )

    @staticmethod
    def _with_route_tools(route: ModelRoute, kwargs: dict) -> dict:
        """Materialize only the tools declared by the resolved role."""

        prepared = dict(kwargs)
        if route.config.get("tools") and "tools" not in prepared:
            from .providers import provider_capabilities

            if not provider_capabilities(route.provider).supports_native_tools:
                raise ProjectError(
                    f"Provider {route.provider} does not support configured native tools"
                )
            prepared["tools"] = build_tool_payload(route.config["tools"])
        return prepared

    def _execute_route(
        self,
        route: ModelRoute,
        *,
        method: str,
        obligation_id: str | None,
        branch_id: str | None,
        kwargs: dict,
        raw_system_prompt: str | None = None,
        **payload,
    ) -> dict:
        metadata = self.router.begin_call(route, obligation_id=obligation_id, branch_id=branch_id)
        try:
            client = self._client(route)
            response = getattr(client, method)(**payload, **kwargs)
        except Exception as exc:
            if route.tier == "routine" and self._is_route_unavailable(exc):
                self.router.finish_call(metadata["call_id"], error=str(exc))
                fallback = self.router.runtime_fallback(
                    route,
                    reason=f"routine route unavailable: {type(exc).__name__}: {exc}",
                    obligation_id=obligation_id,
                )
                fallback_meta = self.router.begin_call(
                    fallback,
                    obligation_id=obligation_id,
                    branch_id=branch_id,
                    parent_call_id=metadata["call_id"],
                )
                try:
                    client = self._client(fallback)
                    if method == "call":
                        payload["system_prompt"] = compose_system_prompt(
                            raw_system_prompt or payload["system_prompt"],
                            role=fallback.role,
                            tier=fallback.tier,
                        )
                    response = getattr(client, method)(**payload, **kwargs)
                except Exception as fallback_exc:
                    self.router.finish_call(fallback_meta["call_id"], error=str(fallback_exc))
                    raise
                metadata = fallback_meta
                route = fallback
            else:
                self.router.finish_call(metadata["call_id"], error=str(exc))
                raise
        self.router.finish_call(metadata["call_id"], response=response)
        with self._lock:
            self.call_count += 1
            self.request_count = sum(
                int(getattr(client, "request_count", 0)) for client in self._clients.values()
            )
            self.total_retries = sum(
                int(getattr(client, "total_retries", 0)) for client in self._clients.values()
            )
        response = dict(response)
        response["routing"] = {**metadata, **route.to_dict()}
        return response

    def _client(self, route: ModelRoute):
        key = (
            route.role,
            route.provider,
            route.model,
            route.reasoning_effort,
            json.dumps(route.config, sort_keys=True, default=str),
        )
        with self._lock:
            if key not in self._clients:
                safe_role = re.sub(r"[^a-zA-Z0-9_.-]+", "-", route.role)
                self._clients[key] = self.client_factory(
                    route.config,
                    self.archive_dir / safe_role,
                    role_name=route.role,
                    working_dir=self.working_dir / safe_role,
                )
            return self._clients[key]

    def _detect_context(self, prompt: str, label: str) -> tuple[str, str | None, str | None]:
        role = self.default_role
        if label.startswith("verifier_"):
            role = "worker_verifier"
        elif label.startswith("audit_"):
            role = normalize_role(label.removeprefix("audit_"))
        elif label == "final_proof_auditor":
            role = "final_proof_auditor"
        match = _ROLE_HEADER.search(prompt)
        if match:
            role = normalize_role(match.group(1))
        obligation = _OBLIGATION_HEADER.search(prompt)
        branch = _BRANCH_HEADER.search(prompt)
        return (
            role,
            obligation.group(1).strip() if obligation else None,
            branch.group(1).strip() if branch else None,
        )

    @staticmethod
    def _step_id(label: str) -> str | None:
        match = re.search(r"(?:step_|worker_|verifier_)(\d+)", label)
        return match.group(1) if match else None

    @staticmethod
    def _is_route_unavailable(exc: BaseException) -> bool:
        details = getattr(exc, "details", {})
        error_type = details.get("error_type") if isinstance(details, dict) else None
        if error_type in {
            "invalid_model",
            "unsupported_reasoning_effort",
            "gemini_unavailable",
            "not_authenticated",
            "provider_unavailable",
        }:
            return True
        message = str(exc).casefold()
        return any(
            marker in message
            for marker in (
                "model not found",
                "unsupported reasoning",
                "provider unavailable",
                "command not found",
            )
        )

    def interrupt(self):
        for client in list(self._clients.values()):
            client.interrupt()

    def soft_interrupt(self):
        for client in list(self._clients.values()):
            client.soft_interrupt()

    def clear_interrupt(self):
        for client in list(self._clients.values()):
            client.clear_interrupt()

    def clear_soft_interrupt(self):
        for client in list(self._clients.values()):
            client.clear_soft_interrupt()

    def cleanup(self):
        for client in list(self._clients.values()):
            client.cleanup()
