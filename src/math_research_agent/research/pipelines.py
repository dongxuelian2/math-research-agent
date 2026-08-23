"""Persistent event-driven proof, literature, and verification pipelines."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .canonical_artifacts import authority_promotion_decision
from .project import ProjectError, utc_now
from .pipeline_primitives import (
    AtomicResourceBudget,
    DISPATCHABLE_TASK_STATUSES,
    LITERATURE_VERDICTS,
    PIPELINES,
    QUEUE_NAMES,
    TERMINAL_TASK_STATUSES,
    TaskExecutionContext,
    applicability_assumption_snapshot,
    initialize_pipeline_state,
)


class AsyncDAGScheduler:
    """Coordinate three independent queues using obligation-level dependencies."""

    def __init__(
        self,
        *,
        state: dict | None = None,
        state_path: str | Path | None = None,
        config: dict | None = None,
    ):
        self.state_path = Path(state_path) if state_path else None
        loaded_from_disk = state is None and bool(self.state_path and self.state_path.exists())
        if state is None and self.state_path and self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.state = initialize_pipeline_state(state)
        self.config = copy.deepcopy(config or {})
        self._lock = threading.RLock()
        self.runtime = None
        limits = self.config.get("global_budget") or self.config.get("resource_budget") or {}
        if not limits and isinstance(self.config.get("budget"), dict):
            budget_cfg = self.config["budget"]
            limits = {
                "provider_calls": budget_cfg.get(
                    "max_provider_calls", budget_cfg.get("provider_calls", 10**9)
                ),
                "input_tokens": budget_cfg.get("max_input_tokens", 10**9),
                "output_tokens": budget_cfg.get("max_output_tokens", 10**9),
                "reasoning_tokens": budget_cfg.get("max_reasoning_tokens", 10**9),
                "cached_tokens": budget_cfg.get("max_cached_tokens", 10**9),
                "total_tokens": budget_cfg.get("max_total_tokens", 10**9),
            }
        persisted_budget = self.state.get("resource_budget", {})
        self.resource_budget = AtomicResourceBudget(
            limits if isinstance(limits, dict) else {},
            state=(persisted_budget if isinstance(persisted_budget, dict) else {}),
            unknown_usage_policy=str(
                self.config.get("resource_estimates", {}).get(
                    "unknown_usage_policy", "reserved_as_committed"
                )
                if isinstance(self.config.get("resource_estimates"), dict)
                else "reserved_as_committed"
            ),
        )
        self.state["runtime_id"] = uuid.uuid4().hex
        if loaded_from_disk or state is not None:
            self.reconcile_orphans()
            self._invalidate_stale_applicability()
        self._save()

    def bind_runtime(self, runtime) -> None:
        self.runtime = runtime

    def reconcile_orphans(self) -> list[dict]:
        """Convert ACTIVE tasks from a previous process into retryable work."""
        recovered = []
        with self._lock:
            for pipeline, task_ids in self.state.get("active", {}).items():
                for task_id in list(task_ids):
                    task = self.state["tasks"].get(task_id)
                    if not task or task.get("status") != "ACTIVE":
                        if task_id in task_ids:
                            task_ids.remove(task_id)
                        continue
                    cached = task.get("payload", {}).get("result_cache") or task.get(
                        "payload", {}
                    ).get("artifact_path")
                    task["status"] = (
                        "NEEDS_RECONCILIATION"
                        if pipeline == "literature" and cached
                        else "RETRY_READY"
                        if pipeline in {"proof", "verification"}
                        else "READY"
                    )
                    task["orphaned_from"] = "ACTIVE"
                    task["orphan_status"] = "ORPHANED_AFTER_RESTART"
                    task["orphaned_at"] = utc_now()
                    task["attempt_count"] = int(task.get("attempt_count", 0)) + 1
                    task["original_call_id"] = task.get("call_id") or task.get("original_call_id")
                    task["attempt_id"] = f"attempt-{uuid.uuid4().hex[:12]}"
                    task["recovery_status"] = task["status"]
                    task_ids.remove(task_id)
                    queue = self.state["queues"][QUEUE_NAMES[pipeline]]
                    if task["status"] in DISPATCHABLE_TASK_STATUSES and task_id not in queue:
                        queue.append(task_id)
                    recovered.append(copy.deepcopy(task))
            if recovered:
                self._event(
                    "ORPHAN_TASKS_RECONCILED",
                    "<campaign>",
                    {
                        "task_ids": [task["task_id"] for task in recovered],
                    },
                )
        return recovered

    def add_obligation(
        self,
        obligation_id: str,
        *,
        target_statement: str,
        branch_id: str = "main",
        dependencies: list[str] | None = None,
        literature_first: bool = False,
        dual_track: bool = False,
        priority: int = 0,
        context: dict | None = None,
        parent_obligation_id: str | None = None,
        created_by_call_id: str | None = None,
        created_by_role: str | None = None,
        risk_level: str | None = None,
        impact_level: str | None = None,
        current_tier: str | None = None,
        minimum_inherited_tier: str | None = None,
        fresh_independent_obligation: bool = False,
        canonical_source_requirements: list[dict] | None = None,
        canonical_authority: list[dict] | None = None,
    ) -> dict:
        if not obligation_id.strip():
            raise ProjectError("obligation_id is required")
        if dual_track and not bool(self._config("allow_dual_track", True)):
            raise ProjectError("DUAL_TRACK is disabled by routing policy")
        with self._lock:
            if obligation_id in self.state["obligations"]:
                raise ProjectError(f"Obligation already exists: {obligation_id}")
            dependencies = list(dict.fromkeys(dependencies or []))
            missing = [
                item
                for item in dependencies
                if self.state["obligations"].get(item, {}).get("status") != "CLOSED"
            ]
            parent = (
                self.state["obligations"].get(parent_obligation_id)
                if parent_obligation_id
                else None
            )
            tier_order = {"routine": 0, "research": 1, "strategic": 2}
            inherited = (parent or {}).get("minimum_inherited_tier") or (parent or {}).get(
                "current_tier"
            )
            inherited = inherited if inherited in tier_order else "routine"
            requested_tier = current_tier if current_tier in tier_order else inherited
            minimum = minimum_inherited_tier if minimum_inherited_tier in tier_order else inherited
            if fresh_independent_obligation:
                minimum = requested_tier
            effective_tier = max((requested_tier, minimum), key=lambda value: tier_order[value])
            canonical_authority = copy.deepcopy(canonical_authority or [])
            authority_ready, authority_blockers = authority_promotion_decision(canonical_authority)
            authority_block_reason = (
                authority_blockers[0]["type"]
                if not authority_ready and authority_blockers
                else None
            )
            obligation = {
                "obligation_id": obligation_id,
                "target_statement": target_statement,
                "statement": target_statement,
                "normalized_statement": " ".join(str(target_statement).strip().casefold().split()),
                "branch_id": branch_id,
                "parent_obligation_id": parent_obligation_id,
                "created_by_call_id": created_by_call_id,
                "created_by_role": created_by_role,
                "dependencies": dependencies,
                "dependents": [],
                "risk_level": risk_level or "normal",
                "impact_level": impact_level or "normal",
                "current_tier": effective_tier,
                "minimum_inherited_tier": minimum,
                "fresh_independent_obligation": bool(fresh_independent_obligation),
                "literature_status": "PENDING",
                "proof_status": "PENDING",
                "verification_status": "PENDING",
                "priority": int(priority),
                "context": copy.deepcopy(context or {}),
                "canonical_source_requirements": copy.deepcopy(canonical_source_requirements or []),
                "canonical_authority": canonical_authority,
                "authority_blockers": authority_blockers,
                "literature_first": bool(literature_first),
                "dual_track": bool(dual_track),
                "status": authority_block_reason
                or (
                    "BLOCKED_DEPENDENCY"
                    if missing
                    else (
                        "DUAL_TRACK"
                        if dual_track
                        else "LITERATURE_READY"
                        if literature_first
                        else "PROOF_READY"
                    )
                ),
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            self.state["obligations"][obligation_id] = obligation
            for dependency in dependencies:
                if dependency in self.state["obligations"]:
                    self.state["obligations"][dependency].setdefault("dependents", []).append(
                        obligation_id
                    )
            if missing or authority_block_reason:
                self.state["queues"]["BLOCKED_QUEUE"].append(obligation_id)
                self._event(
                    "OBLIGATION_BLOCKED",
                    obligation_id,
                    {
                        "dependencies": missing,
                        "authority_blockers": authority_blockers,
                    },
                )
            else:
                self._activate_initial_tracks(obligation)
            self._save()
            return copy.deepcopy(obligation)

    def add_literature_request(self, request: dict) -> dict:
        required = {
            "obligation_id",
            "requested_statement",
            "why_needed",
            "blocking_or_nonblocking",
            "expected_impact",
            "search_hints",
        }
        missing = required - set(request)
        if missing:
            raise ProjectError("LITERATURE_REQUEST missing fields: " + ", ".join(sorted(missing)))
        obligation_id = str(request["obligation_id"])
        with self._lock:
            obligation = self._obligation(obligation_id)
            request_hash = hashlib.sha256(
                json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if obligation.get("literature_request_hash") == request_hash:
                return copy.deepcopy(obligation)
            obligation["literature_request_hash"] = request_hash
            request_value = copy.deepcopy(request)
            request_value.setdefault("literature_request_id", f"litreq-{request_hash[:16]}")
            obligation["literature_request"] = request_value
            blocking = request.get("blocking_or_nonblocking") == "blocking"
            obligation["literature_status"] = "READY"
            obligation["literature_blocking"] = blocking
            if blocking:
                obligation["literature_first"] = True
            if obligation["status"] not in {"CLOSED", "BLOCKED_DEPENDENCY"} and blocking:
                obligation["status"] = "LITERATURE_READY"
                self.create_task(
                    "literature",
                    obligation_id,
                    role="literature_lead",
                    payload={"request": copy.deepcopy(request_value)},
                    priority=obligation["priority"],
                )
            elif obligation["status"] != "CLOSED":
                # A nonblocking request gets its own literature task while the
                # proof status and sibling paths remain runnable.
                self.create_task(
                    "literature",
                    obligation_id,
                    role="literature_lead",
                    payload={"request": copy.deepcopy(request_value)},
                    priority=obligation["priority"],
                )
            self._event("LITERATURE_REQUEST_ACCEPTED", obligation_id, request)
            self._save()
            return copy.deepcopy(obligation)

    def create_task(
        self,
        pipeline: str,
        obligation_id: str,
        *,
        role: str,
        payload: dict | None = None,
        priority: int = 0,
        parent_task_id: str | None = None,
        speculative: bool = False,
        citation_depth: int = 0,
    ) -> dict:
        if pipeline not in PIPELINES:
            raise ProjectError(f"Unknown pipeline: {pipeline}")
        with self._lock:
            self._obligation(obligation_id)
            self._enforce_task_budget(pipeline, role)
            number = int(self.state["next_task_number"])
            self.state["next_task_number"] = number + 1
            task_id = f"task-{number:08d}"
            task = {
                "task_id": task_id,
                "pipeline": pipeline,
                "obligation_id": obligation_id,
                "role": role,
                "payload": copy.deepcopy(payload or {}),
                "priority": int(priority),
                "parent_task_id": parent_task_id,
                "speculative": bool(speculative),
                "citation_depth": int(citation_depth),
                "status": "READY",
                "call_id": None,
                "attempt_id": f"attempt-{uuid.uuid4().hex[:12]}",
                "created_process_id": threading.get_native_id(),
                "created_at": utc_now(),
            }
            obligation = self._obligation(obligation_id)
            authority_sources = [
                item
                for item in obligation.get("canonical_authority", [])
                if item.get("resolution_status") == "RESOLVED_CANONICAL"
            ]
            if authority_sources:
                task["payload"]["canonical_authority_sources"] = copy.deepcopy(authority_sources)
            self.state["tasks"][task_id] = task
            queue = self.state["queues"][QUEUE_NAMES[pipeline]]
            queue.append(task_id)
            queue.sort(
                key=lambda item: (
                    -int(self.state["tasks"][item].get("priority", 0)),
                    self.state["tasks"][item]["created_at"],
                )
            )
            self._event("TASK_READY", obligation_id, {"task_id": task_id, "pipeline": pipeline})
            self._save()
            return copy.deepcopy(task)

    def dispatch_window(self, limits: dict[str, int] | None = None) -> dict[str, list[dict]]:
        """Claim work from all three pipelines in one scheduling window."""

        limits = limits or {"proof": 1, "literature": 1, "verification": 1}
        dispatched = {pipeline: [] for pipeline in PIPELINES}
        if self.resource_budget.halted:
            return dispatched
        with self._lock:
            for pipeline in PIPELINES:
                maximum = max(0, int(limits.get(pipeline, 0)))
                queue = self.state["queues"][QUEUE_NAMES[pipeline]]
                for _ in range(min(maximum, len(queue))):
                    task_id = queue.pop(0)
                    task = self.state["tasks"][task_id]
                    if task["status"] not in DISPATCHABLE_TASK_STATUSES:
                        continue
                    task["status"] = "ACTIVE"
                    task["started_at"] = utc_now()
                    self.state["active"][pipeline].append(task_id)
                    obligation = self._obligation(task["obligation_id"])
                    if obligation["status"] != "DUAL_TRACK":
                        obligation["status"] = {
                            "proof": "PROOF_ACTIVE",
                            "literature": "LITERATURE_ACTIVE",
                            "verification": "VERIFICATION_ACTIVE",
                        }[pipeline]
                    dispatched[pipeline].append(copy.deepcopy(task))
                    self._event("TASK_STARTED", task["obligation_id"], {"task_id": task_id})
            self._save()
        return dispatched

    def complete_task(self, task_id: str, result: dict | None = None) -> dict:
        result = copy.deepcopy(result or {})
        with self._lock:
            task = self._task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return copy.deepcopy(task)
            if task["status"] not in {"ACTIVE", "CANCEL_REQUESTED"}:
                raise ProjectError(f"Task is not ACTIVE: {task_id}")
            task["status"] = (
                "COMPLETED_BEFORE_CANCEL" if task["status"] == "CANCEL_REQUESTED" else "COMPLETE"
            )
            task["completed_at"] = utc_now()
            task["result"] = result
            active = self.state["active"][task["pipeline"]]
            if task_id in active:
                active.remove(task_id)
            if task_id not in self.state["completed_task_ids"]:
                self.state["completed_task_ids"].append(task_id)
            obligation = self._obligation(task["obligation_id"])
            self._event("TASK_COMPLETED", task["obligation_id"], {"task_id": task_id})
            if task["pipeline"] == "proof":
                self._complete_proof(task, result, obligation)
            elif task["pipeline"] == "literature":
                self._complete_literature(task, result, obligation)
            else:
                self._complete_verification(task, result, obligation)
            self._save()
            return copy.deepcopy(task)

    def fail_task(self, task_id: str, *, failure_kind: str, detail: str) -> dict:
        with self._lock:
            task = self._task(task_id)
            task["status"] = "ERROR"
            task["completed_at"] = utc_now()
            task["error"] = {"kind": failure_kind, "detail": detail}
            if task_id in self.state["active"].get(task["pipeline"], []):
                self.state["active"][task["pipeline"]].remove(task_id)
            obligation = self._obligation(task["obligation_id"])
            obligation.setdefault("failure_counters", {})[failure_kind] = (
                int(obligation.setdefault("failure_counters", {}).get(failure_kind, 0)) + 1
            )
            obligation["status"] = {
                "proof": "PROOF_READY",
                "literature": "LITERATURE_PENDING",
                "verification": "VERIFICATION_READY",
            }[task["pipeline"]]
            self._event("TASK_FAILED", task["obligation_id"], task["error"])
            self._save()
            return copy.deepcopy(task)

    def cancel_task(self, task_id: str, *, reason: str, redirect: bool = False) -> dict:
        with self._lock:
            task = self._task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return copy.deepcopy(task)
            task["status"] = "REDIRECTED" if redirect else "CANCELLED"
            task["completed_at"] = utc_now()
            task["cancellation_reason"] = reason
            for collection in (
                self.state["queues"][QUEUE_NAMES[task["pipeline"]]],
                self.state["active"][task["pipeline"]],
            ):
                if task_id in collection:
                    collection.remove(task_id)
            self._event(
                "TASK_CANCELLED",
                task["obligation_id"],
                {
                    "task_id": task_id,
                    "reason": reason,
                    "redirect": redirect,
                },
            )
            self._save()
            return copy.deepcopy(task)

    def request_cancel(self, task_id: str, *, reason: str, redirect: bool = False) -> dict:
        """Request cancellation without pretending a running provider stopped."""
        with self._lock:
            task = self._task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return copy.deepcopy(task)
            if task["status"] in DISPATCHABLE_TASK_STATUSES:
                task["status"] = "CANCELLED_BEFORE_START"
                task["completed_at"] = utc_now()
            else:
                task["status"] = "CANCEL_REQUESTED"
            task["cancellation_reason"] = reason
            task["redirect_on_cancel"] = bool(redirect)
            self._event(
                "TASK_CANCEL_REQUESTED",
                task["obligation_id"],
                {
                    "task_id": task_id,
                    "reason": reason,
                },
            )
            self._save()
            if task["status"] == "CANCELLED_BEFORE_START":
                return copy.deepcopy(task)
            runtime = self.runtime
        if runtime is not None:
            runtime.request_cancel(task_id)
        return copy.deepcopy(task)

    def interrupt_task(self, task_id: str, *, detail: str = "provider interrupted") -> dict:
        with self._lock:
            task = self._task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return copy.deepcopy(task)
            task["status"] = "REDIRECTED" if task.get("redirect_on_cancel") else "INTERRUPTED"
            task["completed_at"] = utc_now()
            task["interruption_detail"] = detail
            for collection in (
                self.state["queues"][QUEUE_NAMES[task["pipeline"]]],
                self.state["active"][task["pipeline"]],
            ):
                if task_id in collection:
                    collection.remove(task_id)
            self._event(
                "TASK_INTERRUPTED", task["obligation_id"], {"task_id": task_id, "detail": detail}
            )
            self._save()
            return copy.deepcopy(task)

    def record_resource_usage(self, task: dict, result: dict | None = None) -> None:
        """Persist call/token accounting grouped by pipeline and task metadata."""
        result = result if isinstance(result, dict) else {}
        usage = (
            result.get("usage")
            or (result.get("provider") if isinstance(result.get("provider"), dict) else {}).get(
                "usage"
            )
            or result.get("routing", {}).get("usage")
            or {}
        )
        normalized = {
            key: int(usage.get(key, 0) or 0)
            for key in AtomicResourceBudget.FIELDS
            if key != "provider_calls"
        }
        with self._lock:
            by_pipeline = self.state.setdefault("usage_by_pipeline", {})
            pipeline = by_pipeline.setdefault(
                task.get("pipeline", "unknown"), {key: 0 for key in AtomicResourceBudget.FIELDS}
            )
            pipeline["provider_calls"] = int(pipeline.get("provider_calls", 0)) + 1
            for key, value in normalized.items():
                pipeline[key] = int(pipeline.get(key, 0)) + value
            by_route = self.state.setdefault("usage_by_route", {})
            routing = result.get("routing", {}) if isinstance(result.get("routing"), dict) else {}
            route_key = "|".join(
                str(routing.get(key) or task.get(key) or "unknown")
                for key in ("tier", "role", "model", "provider")
            )
            bucket = by_route.setdefault(route_key, {key: 0 for key in AtomicResourceBudget.FIELDS})
            bucket["provider_calls"] = int(bucket.get("provider_calls", 0)) + 1
            for key, value in normalized.items():
                bucket[key] = int(bucket.get(key, 0)) + value
            self._save()

    def reconcile_resource_usage(
        self,
        task_id: str,
        reservation: dict,
        result: dict | None,
        *,
        interrupted: bool = False,
    ) -> dict:
        result = result if isinstance(result, dict) else {}
        usage = (
            result.get("usage")
            or (result.get("provider") if isinstance(result.get("provider"), dict) else {}).get(
                "usage"
            )
            or result.get("routing", {}).get("usage")
        )
        usage_known = (
            isinstance(usage, dict)
            and all(key in usage for key in AtomicResourceBudget.TOKEN_FIELDS)
            and not interrupted
        )
        actual = copy.deepcopy(usage or {})
        actual["provider_calls"] = 1
        reconciliation = self.resource_budget.reconcile(
            reservation, actual, usage_known=usage_known
        )
        with self._lock:
            task = self.state["tasks"].get(task_id)
            if task is not None:
                task["usage_reconciliation"] = copy.deepcopy(reconciliation)
            if reconciliation["hard_cap_exceeded"]:
                self.state["resource_budget_hard_stop"] = {
                    "status": "HARD_BUDGET_EXCEEDED_BY_COMPLETED_CALL",
                    "task_id": task_id,
                    "reconciliation": copy.deepcopy(reconciliation),
                    "at": utc_now(),
                }
                self._event(
                    "GLOBAL_HARD_BUDGET_STOP",
                    "<campaign>",
                    {
                        "task_id": task_id,
                        "exceeded": reconciliation["exceeded"],
                    },
                )
            self._save()
        return reconciliation

    def plan_literature_searchers(
        self,
        obligation_id: str,
        *,
        search_tasks: list[dict],
        lead_task_id: str | None = None,
    ) -> list[dict]:
        search_tasks = list(search_tasks or [])
        maximum = int(self._literature_budget("max_literature_searchers", 6))
        initial = int(self._literature_budget("initial_literature_searchers", 3))
        chosen = search_tasks[: min(initial, maximum)]
        if not chosen:
            raise ProjectError("Literature Lead returned no usable public query")
        obligation = self._obligation(obligation_id)
        request = obligation.get("literature_request") or {}
        request_id = str(
            request.get("literature_request_id") or obligation.get("literature_request_hash") or ""
        )
        literature_config = self.config.get("literature", {})
        campaign_approval = bool(
            isinstance(literature_config, dict)
            and literature_config.get("external_public_search_approved", False)
        )
        approval_source = (
            str(literature_config.get("public_search_approval_source") or "operator")
            if campaign_approval
            else None
        )
        created = []
        for proposal in chosen:
            if not isinstance(proposal, dict):
                raise ProjectError("Literature Lead search_tasks entries must be objects")
            strategy = str(proposal.get("strategy") or "").strip()
            query = self._validate_public_query(str(proposal.get("public_query") or ""), obligation)
            approved_at = utc_now() if campaign_approval else None
            payload = {
                "strategy": strategy,
                "public_query": query,
                "external_search_approved": campaign_approval,
                "approval_source": approval_source,
                "approval_timestamp": approved_at,
                "query_hash": "sha256:" + hashlib.sha256(query.encode("utf-8")).hexdigest(),
                "obligation_id": obligation_id,
                "literature_request_id": request_id,
                "reason": str(proposal.get("reason") or ""),
                "source_preferences": copy.deepcopy(proposal.get("source_preferences") or {}),
                "use_scholarly_adapter": True,
                "limit": int(proposal.get("limit", 10) or 10),
            }
            task = self.create_task(
                "literature",
                obligation_id,
                role="literature_searcher",
                payload=payload,
                priority={"HIGH": 20, "MEDIUM": 10, "LOW": 0}.get(
                    str(proposal.get("priority") or "MEDIUM").upper(), 10
                ),
                parent_task_id=lead_task_id,
            )
            created.append(task)
            self._event(
                "PUBLIC_QUERY_APPROVED" if campaign_approval else "PUBLIC_QUERY_NOT_AUTHORIZED",
                obligation_id,
                {
                    "task_id": task["task_id"],
                    "query_hash": payload["query_hash"],
                    "approval_source": approval_source,
                },
            )
        return created

    @staticmethod
    def _validate_public_query(query: str, obligation: dict) -> str:
        query = " ".join(str(query or "").strip().split())
        if not query:
            raise ProjectError("Literature Lead proposed an empty public_query")
        if len(query) > 240:
            raise ProjectError("public_query exceeds the 240-character safety bound")
        if re.search(r"(?:[A-Za-z]:[\\/]|(?:^|\s)/(?:home|Users|tmp|var)/)", query):
            raise ProjectError("public_query contains a local filesystem path")
        if re.search(r"\b(?:api[_ -]?key|password|secret|private[_ -]?key)\b", query, re.I):
            raise ProjectError("public_query contains private metadata")
        target = " ".join(str(obligation.get("target_statement") or "").split())
        if len(target) > 240 and query.casefold() == target.casefold():
            raise ProjectError("public_query contains the complete private theorem context")
        return query

    def register_source(self, source: dict, *, obligation_id: str) -> tuple[dict, bool]:
        identifier = (
            str(
                source.get("DOI_or_stable_identifier")
                or source.get("stable_identifier")
                or source.get("url")
                or ""
            )
            .strip()
            .casefold()
        )
        if not identifier:
            payload = json.dumps(source, ensure_ascii=False, sort_keys=True)
            identifier = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._lock:
            existing_id = self.state["source_identifiers"].get(identifier)
            if existing_id:
                record = self.state["sources"][existing_id]
                if obligation_id not in record.setdefault("obligations", []):
                    record["obligations"].append(obligation_id)
                self.state["literature"]["duplicate_searches_avoided"] += 1
                self._save()
                return copy.deepcopy(record), False
            source_id = f"source-{len(self.state['sources']) + 1:06d}"
            record = {
                **copy.deepcopy(source),
                "source_id": source_id,
                "canonical_identifier": identifier,
                "obligations": [obligation_id],
                "discovered_at": utc_now(),
            }
            self.state["sources"][source_id] = record
            self.state["source_identifiers"][identifier] = source_id
            self.state["literature"]["sources_found"] += 1
            self._event("LITERATURE_SOURCE_DISCOVERED", obligation_id, {"source_id": source_id})
            self._save()
            return copy.deepcopy(record), True

    def create_reader_task(
        self,
        obligation_id: str,
        source_id: str,
        *,
        deep: bool = False,
        parent_task_id: str | None = None,
    ) -> dict:
        with self._lock:
            if source_id not in self.state["sources"]:
                raise ProjectError(f"Unknown literature source: {source_id}")
            role = "literature_deep_reader" if deep else "literature_reader"
            return self.create_task(
                "literature",
                obligation_id,
                role=role,
                payload={"source_id": source_id, "deep_read": bool(deep)},
                parent_task_id=parent_task_id,
            )

    def create_citation_chain_task(
        self,
        obligation_id: str,
        source_id: str,
        *,
        depth: int,
        parent_task_id: str | None = None,
    ) -> dict:
        maximum = int(self._literature_budget("max_citation_chain_depth", 2))
        if depth > maximum:
            raise ProjectError("Citation-chain depth budget exhausted")
        return self.create_task(
            "literature",
            obligation_id,
            role="literature_searcher",
            payload={"strategy": "citation_chain", "source_id": source_id},
            parent_task_id=parent_task_id,
            citation_depth=depth,
        )

    def apply_literature_result(
        self,
        obligation_id: str,
        *,
        verdict: str,
        authority_status: str | None = None,
        synthesis_path: str | None = None,
    ) -> dict:
        if verdict not in LITERATURE_VERDICTS:
            raise ProjectError(f"Unknown literature verdict: {verdict}")
        with self._lock:
            obligation = self._obligation(obligation_id)
            obligation["literature_verdict"] = verdict
            obligation["authority_status"] = authority_status
            obligation["literature_status"] = "RESULT_AVAILABLE"
            obligation["literature_synthesis"] = synthesis_path
            obligation["updated_at"] = utc_now()
            if verdict in {"EXACT_RESULT_FOUND", "STRONGER_RESULT_FOUND"}:
                counter = "exact_matches"
                self.state["literature"][counter] += 1
                if authority_status == "VERIFIED_SOURCE_THEOREM":
                    # Source authenticity is not mathematical applicability.
                    # Keep any DUAL_TRACK proof alive until an independent
                    # applicability verifier promotes this exact snapshot.
                    obligation["status"] = "VERIFICATION_READY"
                    obligation["authority_status"] = "VERIFIED_SOURCE_THEOREM"
                    app_context = self.applicability_context(obligation_id)
                    obligation["applicability_assumption_snapshot"] = app_context[
                        "assumption_snapshot_hash"
                    ]
                    self.create_task(
                        "verification",
                        obligation_id,
                        role="reconstruction",
                        payload={
                            "authority_status": "VERIFIED_SOURCE_THEOREM",
                            "applicability_reconstruction_required": True,
                            **copy.deepcopy(app_context),
                        },
                    )
                    self._event(
                        "SOURCE_THEOREM_VERIFIED",
                        obligation_id,
                        {
                            "action": "APPLICABILITY_RECONSTRUCTION",
                            "verdict": verdict,
                        },
                    )
                else:
                    obligation["status"] = "LITERATURE_PENDING"
                    self.create_task(
                        "literature",
                        obligation_id,
                        role="literature_authority_auditor",
                        payload={
                            "minimum_tier": "research",
                            "authority_verification_required": True,
                        },
                    )
                    self._event(
                        "LITERATURE_RESULT_AVAILABLE",
                        obligation_id,
                        {
                            "action": "AUTHORITY_VERIFY",
                            "verdict": verdict,
                        },
                    )
            elif verdict == "NO_SUFFICIENT_RESULT_FOUND":
                self._cancel_literature_tasks(obligation_id)
                obligation["status"] = "PROOF_READY"
                self.create_task("proof", obligation_id, role="constructive")
                self._event(
                    "LITERATURE_RESULT_AVAILABLE",
                    obligation_id,
                    {
                        "action": "RELEASE_PROOF",
                        "verdict": verdict,
                    },
                )
            elif verdict in {"PARTIAL_RESULT_FOUND", "METHOD_FOUND"}:
                key = "partial_matches" if verdict == "PARTIAL_RESULT_FOUND" else "method_matches"
                self.state["literature"][key] += 1
                obligation["status"] = "PROOF_READY"
                self.create_task(
                    "proof",
                    obligation_id,
                    role="constructive",
                    payload={"literature_verdict": verdict},
                )
            elif verdict in {"INSUFFICIENT_SEARCH", "CONFLICTING_LITERATURE"}:
                obligation["status"] = "LITERATURE_PENDING"
                self._event(
                    "LITERATURE_RESULT_AVAILABLE",
                    obligation_id,
                    {
                        "action": "ESCALATE",
                        "verdict": verdict,
                    },
                )
            elif verdict == "LITERATURE_PROVIDER_UNAVAILABLE":
                allow = bool(
                    self._config("allow_proof_fallback_when_literature_unavailable", False)
                )
                if allow:
                    obligation["status"] = "PROOF_READY"
                    obligation["proof_without_literature_screening"] = True
                    self.create_task("proof", obligation_id, role="constructive")
                else:
                    obligation["status"] = "LITERATURE_PENDING"
            self._save()
            return copy.deepcopy(obligation)

    def reuse_verified_source_theorem(
        self,
        obligation_id: str,
        authority_record: dict,
        *,
        verdict: str = "EXACT_RESULT_FOUND",
    ) -> dict:
        """Reuse source truth while requiring fresh obligation applicability."""

        if authority_record.get("status") != "VERIFIED_SOURCE_THEOREM":
            raise ProjectError("Only a verified source theorem may be reused")
        with self._lock:
            obligation = self._obligation(obligation_id)
            obligation["authority_candidate"] = copy.deepcopy(authority_record)
            obligation["authority_candidate_verification"] = {
                "verdict": "VERIFIED_SOURCE_THEOREM",
                "reused_source_truth": True,
            }
        return self.apply_literature_result(
            obligation_id,
            verdict=verdict,
            authority_status="VERIFIED_SOURCE_THEOREM",
        )

    def handle_event(self, event: dict) -> dict | None:
        """Deterministic event bridge used by production workers and resume."""
        if not isinstance(event, dict):
            raise ProjectError("Pipeline event must be an object")
        event_id = str(event.get("event_id") or event.get("id") or "").strip()
        with self._lock:
            if event_id and event_id in self.state["processed_event_ids"]:
                return None
            if event_id:
                self.state["processed_event_ids"].append(event_id)
        event_type = str(event.get("event") or event.get("type") or "").upper()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        obligation_id = str(event.get("obligation_id") or payload.get("obligation_id") or "")
        if event_type in {"NEW_OBLIGATION", "NEW_LEMMA", "NEW_SUBOBLIGATION"}:
            child_id = str(payload.get("obligation_id") or payload.get("child_obligation_id") or "")
            if not child_id:
                raise ProjectError("NEW_OBLIGATION requires obligation_id")
            if child_id in self.state["obligations"]:
                return self.snapshot()["obligations"][child_id]
            return self.add_obligation(
                child_id,
                target_statement=str(
                    payload.get("statement") or payload.get("target_statement") or ""
                ),
                parent_obligation_id=str(payload.get("parent_obligation_id") or obligation_id or "")
                or None,
                dependencies=list(payload.get("dependencies") or []),
                branch_id=str(payload.get("branch_id") or "main"),
                current_tier=payload.get("current_tier") or payload.get("tier"),
                minimum_inherited_tier=payload.get("minimum_inherited_tier"),
                fresh_independent_obligation=bool(
                    payload.get("fresh_independent_obligation", False)
                ),
                created_by_call_id=payload.get("created_by_call_id"),
                created_by_role=payload.get("created_by_role"),
                risk_level=payload.get("risk_level"),
                impact_level=payload.get("impact_level"),
                literature_first=bool(payload.get("literature_first", False)),
                dual_track=bool(payload.get("dual_track", False)),
                context=payload.get("context") if isinstance(payload.get("context"), dict) else {},
                canonical_source_requirements=list(
                    payload.get("canonical_source_requirements") or []
                ),
                canonical_authority=list(payload.get("canonical_authority") or []),
            )
        if event_type == "NEW_DEPENDENCY":
            child_id = str(payload.get("obligation_id") or obligation_id)
            dependency_id = str(payload.get("dependency_id") or payload.get("depends_on") or "")
            if not child_id or not dependency_id:
                raise ProjectError("NEW_DEPENDENCY requires obligation_id and dependency_id")
            with self._lock:
                child = self._obligation(child_id)
                if dependency_id not in child["dependencies"]:
                    child["dependencies"].append(dependency_id)
                dependency = self._obligation(dependency_id)
                if child_id not in dependency.setdefault("dependents", []):
                    dependency["dependents"].append(child_id)
                if dependency.get("status") != "CLOSED":
                    child["status"] = "BLOCKED_DEPENDENCY"
                    if child_id not in self.state["queues"]["BLOCKED_QUEUE"]:
                        self.state["queues"]["BLOCKED_QUEUE"].append(child_id)
                self._event("DEPENDENCY_EDGE_ADDED", child_id, {"dependency_id": dependency_id})
                self._save()
                return copy.deepcopy(child)
        if event_type == "LITERATURE_REQUEST":
            return self.add_literature_request(payload)
        task_id = str(payload.get("task_id") or event.get("task_id") or "")
        if (
            event_type
            in {"WORKER_RESULT", "PROOF_RESULT", "VERIFIER_RESULT", "LITERATURE_RESULT_AVAILABLE"}
            and task_id
        ):
            return self.complete_task(task_id, payload.get("result") or payload)
        if event_type == "TASK_CANCELLED" and task_id:
            return self.cancel_task(
                task_id, reason=str(payload.get("reason") or "event cancellation")
            )
        return None

    def close_obligation(self, obligation_id: str, *, reason: str) -> dict:
        with self._lock:
            obligation = self._obligation(obligation_id)
            authority_ready, blockers = authority_promotion_decision(
                obligation.get("canonical_authority", [])
            )
            if not authority_ready:
                obligation["status"] = blockers[0]["type"]
                obligation["authority_blockers"] = blockers
                if obligation_id not in self.state["queues"]["BLOCKED_QUEUE"]:
                    self.state["queues"]["BLOCKED_QUEUE"].append(obligation_id)
                self._event("AUTHORITY_PROMOTION_BLOCKED", obligation_id, {"blockers": blockers})
                self._save()
                return copy.deepcopy(obligation)
            obligation["status"] = "CLOSED"
            obligation["closed_reason"] = reason
            obligation["closed_at"] = utc_now()
            self._event("OBLIGATION_CLOSED", obligation_id, {"reason": reason})
            self._unblock_dependents(obligation_id)
            self._save()
            return copy.deepcopy(obligation)

    def bind_canonical_authority(
        self,
        obligation_id: str,
        *,
        requirements: list[dict],
        resolutions: list[dict],
    ) -> dict:
        """Bind resolved bodies to one obligation and apply a scoped blocker."""

        with self._lock:
            obligation = self._obligation(obligation_id)
            obligation["canonical_source_requirements"] = copy.deepcopy(requirements)
            obligation["canonical_authority"] = copy.deepcopy(resolutions)
            ready, blockers = authority_promotion_decision(resolutions)
            obligation["authority_blockers"] = blockers
            blocked_queue = self.state["queues"]["BLOCKED_QUEUE"]
            if not ready:
                obligation["status"] = blockers[0]["type"]
                if obligation_id not in blocked_queue:
                    blocked_queue.append(obligation_id)
                for task in self.state["tasks"].values():
                    if (
                        task.get("obligation_id") == obligation_id
                        and task.get("status") in DISPATCHABLE_TASK_STATUSES
                    ):
                        task["status"] = "CANCELLED_BEFORE_START"
                        queue = self.state["queues"][QUEUE_NAMES[task["pipeline"]]]
                        if task["task_id"] in queue:
                            queue.remove(task["task_id"])
                self._event("AUTHORITY_RESOLUTION_BLOCKED", obligation_id, {"blockers": blockers})
            elif obligation.get("status", "").startswith("BLOCKED_AUTHORITY_"):
                if obligation_id in blocked_queue:
                    blocked_queue.remove(obligation_id)
                obligation["status"] = (
                    "DUAL_TRACK"
                    if obligation.get("dual_track")
                    else "LITERATURE_READY"
                    if obligation.get("literature_first")
                    else "PROOF_READY"
                )
                self._activate_initial_tracks(obligation)
                self._event("AUTHORITY_RESOLUTION_RESTORED", obligation_id)
            obligation["updated_at"] = utc_now()
            self._save()
            return copy.deepcopy(obligation)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.state)

    def _activate_initial_tracks(self, obligation: dict) -> None:
        obligation_id = obligation["obligation_id"]
        if obligation["literature_first"] or obligation["dual_track"]:
            self.create_task(
                "literature",
                obligation_id,
                role="literature_lead",
                payload={"target_statement": obligation["target_statement"]},
                priority=obligation["priority"],
            )
        if obligation["dual_track"]:
            proof_task = self.create_task(
                "proof",
                obligation_id,
                role="constructive",
                payload={"speculative": True},
                priority=obligation["priority"],
                speculative=True,
            )
            self.state["dual_tracks"][obligation_id] = {
                "literature_active": True,
                "speculative_proof_task_id": proof_task["task_id"],
                "approved_at": utc_now(),
            }
        elif not obligation["literature_first"]:
            self.create_task(
                "proof",
                obligation_id,
                role="constructive",
                priority=obligation["priority"],
            )

    def _complete_proof(self, task: dict, result: dict, obligation: dict) -> None:
        obligation["proof_status"] = "RESULT_AVAILABLE"
        for event_name in ("NEW_OBLIGATION", "NEW_LEMMA", "NEW_SUBOBLIGATION", "NEW_DEPENDENCY"):
            event_payload = result.get(event_name)
            if isinstance(event_payload, dict):
                event_payload.setdefault("parent_obligation_id", task["obligation_id"])
                event_payload.setdefault("created_by_role", task.get("role"))
                event_payload.setdefault("created_by_call_id", task.get("call_id"))
                self.handle_event(
                    {
                        "event": event_name,
                        "obligation_id": task["obligation_id"],
                        "payload": event_payload,
                    }
                )
        literature_request = result.get("LITERATURE_REQUEST") or result.get("literature_request")
        if isinstance(literature_request, dict):
            self.add_literature_request(literature_request)
        if result.get("proof_candidate") or result.get("success"):
            obligation["status"] = "VERIFICATION_READY"
            self.create_task(
                "verification",
                task["obligation_id"],
                role="theorem_verifier",
                payload={
                    "proof_task_id": task["task_id"],
                    "high_value": bool(result.get("high_value")),
                },
            )
            dual = self.state["dual_tracks"].get(task["obligation_id"])
            if dual:
                dual["proof_completed_first"] = True
                dual["literature_policy"] = "minimal_authority_search_or_cancel_deep_reads"
        elif not literature_request:
            obligation["status"] = "PROOF_READY"

    def _complete_literature(self, task: dict, result: dict, obligation: dict) -> None:
        obligation["literature_status"] = "RESULT_AVAILABLE"
        role = task["role"]
        metrics = self.state["literature"]
        if role == "literature_lead":
            metrics["lead_calls"] += 1
            search_tasks = result.get("search_tasks")
            if isinstance(search_tasks, list):
                self.plan_literature_searchers(
                    task["obligation_id"],
                    search_tasks=search_tasks,
                    lead_task_id=task["task_id"],
                )
            obligation["status"] = "LITERATURE_ACTIVE"
        elif role == "literature_searcher":
            metrics["searcher_calls"] += 1
            for source in result.get("sources", []):
                record, created = self.register_source(source, obligation_id=task["obligation_id"])
                if created and result.get("create_reader", True):
                    self.create_reader_task(
                        task["obligation_id"],
                        record["source_id"],
                        deep=bool(source.get("deep_read_required")),
                        parent_task_id=task["task_id"],
                    )
        elif role in {"literature_reader", "literature_deep_reader"}:
            metrics["reader_calls"] += 1
            metrics["sources_deep_read"] += 1
            metrics["external_theorems_extracted"] += len(result.get("theorems", []))
            for citation in result.get("citation_chain", []):
                source_id = str(citation.get("source_id") or task["payload"].get("source_id"))
                depth = int(task.get("citation_depth", 0)) + 1
                if depth <= int(self._literature_budget("max_citation_chain_depth", 2)):
                    self.create_citation_chain_task(
                        task["obligation_id"],
                        source_id,
                        depth=depth,
                        parent_task_id=task["task_id"],
                    )
        elif role == "literature_authority_auditor":
            verified = result.get("authority_status") == "VERIFIED_SOURCE_THEOREM"
            if verified and result.get("deterministic_verification") is True:
                self.apply_literature_result(
                    task["obligation_id"],
                    verdict=str(
                        result.get("literature_verdict")
                        or obligation.get("literature_verdict")
                        or "PARTIAL_RESULT_FOUND"
                    ),
                    authority_status="VERIFIED_SOURCE_THEOREM",
                    synthesis_path=result.get("synthesis_path")
                    or obligation.get("literature_synthesis"),
                )
            else:
                obligation["authority_status"] = (
                    result.get("authority_status") or "AUTHORITY_VERIFICATION_FAILED"
                )
                obligation["literature_status"] = "AUTHORITY_VERIFICATION_FAILED"
                obligation["status"] = "LITERATURE_PENDING"
                self._event(
                    "AUTHORITY_REJECTED",
                    task["obligation_id"],
                    {
                        "task_id": task["task_id"],
                        "errors": result.get("authority_verification_errors", []),
                    },
                )
                return
        elif role == "literature_synthesizer":
            if isinstance(result.get("authority_record"), dict):
                obligation["authority_candidate"] = copy.deepcopy(result["authority_record"])
                obligation["authority_candidate_verification"] = copy.deepcopy(
                    result.get("verification") or {}
                )
                obligation["authority_status"] = "AUTHORITY_CANDIDATE"
        verdict = result.get("literature_verdict")
        if verdict and role != "literature_authority_auditor":
            self.apply_literature_result(
                task["obligation_id"],
                verdict=verdict,
                authority_status=result.get("authority_status"),
                synthesis_path=result.get("synthesis_path"),
            )
        if role in {"literature_searcher", "literature_reader", "literature_deep_reader"}:
            self._maybe_schedule_synthesizer(task["obligation_id"])

    def _complete_verification(self, task: dict, result: dict, obligation: dict) -> None:
        obligation["verification_status"] = "RESULT_AVAILABLE"
        verdict = str(result.get("verdict", "UNCERTAIN")).upper()
        if (
            task.get("role") == "reconstruction"
            and verdict
            in {
                "RECONSTRUCTION_COMPLETE",
                "APPLICABILITY_CANDIDATE",
                "CORRECT",
            }
            and result.get("applicability_id")
        ):
            obligation["applicability_id"] = result["applicability_id"]
            obligation["applicability_status"] = "APPLICABILITY_CANDIDATE"
            obligation["applicability_assumption_snapshot"] = result.get("assumption_snapshot_hash")
            obligation["reconstruction_artifact"] = result.get("result_artifact")
            obligation["status"] = "VERIFICATION_READY"
            self.create_task(
                "verification",
                task["obligation_id"],
                role="theorem_verifier",
                payload={
                    "reconstruction_task_id": task["task_id"],
                    "applicability_id": result["applicability_id"],
                    "independent_applicability_verification": True,
                },
            )
            self._event(
                "RECONSTRUCTION_READY_FOR_SECONDARY_VERIFICATION",
                task["obligation_id"],
                {"task_id": task["task_id"]},
            )
            return
        if task.get("role") == "theorem_verifier" and task.get("payload", {}).get(
            "independent_applicability_verification"
        ):
            expected_id = str(obligation.get("applicability_id") or "")
            promoted = (
                result.get("authority_status") == "APPLICABLE_EXTERNAL_AUTHORITY"
                and str(result.get("applicability_id") or "") == expected_id
                and result.get("deterministic_applicability_promotion") is True
            )
            current_snapshot = self.applicability_context(task["obligation_id"])[
                "assumption_snapshot_hash"
            ]
            promoted = promoted and result.get("assumption_snapshot_hash") == current_snapshot
            if promoted:
                obligation["applicability_status"] = "APPLICABLE_EXTERNAL_AUTHORITY"
                obligation["authority_status"] = "APPLICABLE_EXTERNAL_AUTHORITY"
                obligation["applicability_verifier_artifact"] = result.get("result_artifact")
                self._cancel_speculative_proof(task["obligation_id"], redirect=True)
                self.close_obligation(task["obligation_id"], reason="applicable external authority")
                self.state["literature"]["literature_guided_closures"] += 1
                self._event(
                    "APPLICABLE_AUTHORITY_AVAILABLE",
                    task["obligation_id"],
                    {
                        "applicability_id": expected_id,
                    },
                )
            else:
                obligation["applicability_status"] = result.get(
                    "applicability_status", "APPLICABILITY_REJECTED"
                )
                obligation["status"] = "PROOF_READY"
                obligation.setdefault("verification_failures", []).append(
                    {
                        "verdict": verdict,
                        "detail": result.get("detail")
                        or result.get("applicability_verification_errors"),
                        "at": utc_now(),
                    }
                )
            return
        if (
            task.get("role") == "reconstruction"
            and obligation.get("authority_status") == "VERIFIED_SOURCE_THEOREM"
        ):
            verdict = "MISSING_APPLICABILITY_RECONSTRUCTION"
        if (
            obligation.get("authority_status") == "VERIFIED_SOURCE_THEOREM"
            and task.get("role") == "theorem_verifier"
        ):
            # A generic CORRECT verdict cannot bypass the dedicated
            # reconstruction + independent applicability path.
            verdict = "MISSING_APPLICABILITY_EVIDENCE"
        if verdict == "CORRECT" and result.get("all_required_gates", False):
            self.close_obligation(task["obligation_id"], reason="verified")
        else:
            obligation["status"] = "PROOF_READY"
            obligation.setdefault("verification_failures", []).append(
                {
                    "verdict": verdict,
                    "detail": result.get("detail"),
                    "at": utc_now(),
                }
            )
            self.create_task(
                "proof",
                task["obligation_id"],
                role="constructive",
                payload={"verification_feedback": result},
            )

    def applicability_context(self, obligation_id: str) -> dict:
        """Return only project-authorized assumptions and CLOSED dependencies."""

        obligation = self._obligation(obligation_id)
        assumptions = []
        raw_assumptions = obligation.get("context", {}).get("authorized_assumptions", [])
        for item in raw_assumptions if isinstance(raw_assumptions, list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "").upper() not in {
                "AUTHORIZED",
                "VERIFIED",
                "PROVED",
                "CLOSED",
            }:
                continue
            assumptions.append(copy.deepcopy(item))
        assumptions.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        lemmas = []
        for dependency_id in sorted(obligation.get("dependencies", [])):
            dependency = self.state["obligations"].get(dependency_id, {})
            if dependency.get("status") == "CLOSED":
                lemmas.append(
                    {
                        "id": dependency_id,
                        "statement": dependency.get("target_statement"),
                        "status": "CLOSED",
                    }
                )
        target = str(obligation.get("target_statement") or "")
        snapshot = applicability_assumption_snapshot(
            obligation_id,
            target,
            assumptions,
            lemmas,
        )
        return {
            "current_target": target,
            "current_assumptions": assumptions,
            "authorized_local_lemmas": lemmas,
            "assumption_snapshot_hash": snapshot,
        }

    def _invalidate_stale_applicability(self) -> None:
        for obligation_id, obligation in self.state.get("obligations", {}).items():
            stored = obligation.get("applicability_assumption_snapshot")
            if not stored:
                continue
            current = self.applicability_context(obligation_id)["assumption_snapshot_hash"]
            if stored != current:
                obligation["applicability_status"] = "NEEDS_REVALIDATION"
                if (
                    obligation.get("status") == "CLOSED"
                    and obligation.get("closed_reason") == "applicable external authority"
                ):
                    obligation["status"] = "VERIFICATION_READY"
                    obligation.pop("closed_at", None)
                    obligation.pop("closed_reason", None)

    def _cancel_speculative_proof(self, obligation_id: str, *, redirect: bool) -> None:
        dual = self.state["dual_tracks"].get(obligation_id)
        if not dual:
            return
        task_id = dual.get("speculative_proof_task_id")
        if not task_id or task_id not in self.state["tasks"]:
            return
        task = self.state["tasks"][task_id]
        if task["status"] in TERMINAL_TASK_STATUSES:
            return
        was_ready = task["status"] in DISPATCHABLE_TASK_STATUSES
        if task["status"] == "ACTIVE" and self.runtime is not None:
            dual["literature_completed_first"] = True
            dual["proof_action"] = "CANCEL_REQUESTED"
            self.request_cancel(task_id, reason="literature completed first", redirect=redirect)
            return
        task["status"] = "REDIRECTED" if redirect else "CANCELLED"
        task["completed_at"] = utc_now()
        for collection in (self.state["queues"]["PROOF_QUEUE"], self.state["active"]["proof"]):
            if task_id in collection:
                collection.remove(task_id)
        dual["literature_completed_first"] = True
        dual["proof_action"] = task["status"]
        if was_ready:
            self.state["literature"]["proof_calls_avoided_due_to_literature"] += 1
        self._event("SPECULATIVE_PROOF_REDIRECTED", obligation_id, {"task_id": task_id})

    def _maybe_schedule_synthesizer(self, obligation_id: str) -> None:
        relevant = [
            task
            for task in self.state["tasks"].values()
            if task["obligation_id"] == obligation_id and task["pipeline"] == "literature"
        ]
        if any(task["role"] == "literature_synthesizer" for task in relevant):
            return
        unfinished_research = any(
            task["role"] in {"literature_searcher", "literature_reader", "literature_deep_reader"}
            and task["status"] in {"READY", "ACTIVE"}
            for task in relevant
        )
        if not unfinished_research:
            self.create_task(
                "literature",
                obligation_id,
                role="literature_synthesizer",
                payload={"required_output": "LITERATURE_SYNTHESIS.md"},
            )

    def _cancel_literature_tasks(self, obligation_id: str) -> None:
        for task in self.state["tasks"].values():
            if (
                task["obligation_id"] == obligation_id
                and task["pipeline"] == "literature"
                and task["status"] in {"READY", "ACTIVE"}
            ):
                task["status"] = "CANCELLED"
                task["completed_at"] = utc_now()
                for collection in (
                    self.state["queues"]["LITERATURE_QUEUE"],
                    self.state["active"]["literature"],
                ):
                    if task["task_id"] in collection:
                        collection.remove(task["task_id"])

    def _unblock_dependents(self, obligation_id: str) -> None:
        for dependent_id in self._obligation(obligation_id).get("dependents", []):
            dependent = self._obligation(dependent_id)
            unresolved = [
                item
                for item in dependent["dependencies"]
                if self._obligation(item)["status"] != "CLOSED"
            ]
            if unresolved:
                continue
            if dependent_id in self.state["queues"]["BLOCKED_QUEUE"]:
                self.state["queues"]["BLOCKED_QUEUE"].remove(dependent_id)
            dependent["status"] = (
                "DUAL_TRACK"
                if dependent.get("dual_track")
                else "LITERATURE_READY"
                if dependent.get("literature_first")
                else "PROOF_READY"
            )
            self._activate_initial_tracks(dependent)
            self._event("DEPENDENCY_UNBLOCKED", dependent_id, {"dependency": obligation_id})

    def _task(self, task_id: str) -> dict:
        if task_id not in self.state["tasks"]:
            raise ProjectError(f"Unknown pipeline task: {task_id}")
        return self.state["tasks"][task_id]

    def _obligation(self, obligation_id: str) -> dict:
        if obligation_id not in self.state["obligations"]:
            raise ProjectError(f"Unknown obligation: {obligation_id}")
        return self.state["obligations"][obligation_id]

    def _event(self, event_type: str, obligation_id: str, payload: dict | None = None) -> None:
        number = int(self.state["next_event_number"])
        self.state["next_event_number"] = number + 1
        self.state["events"].append(
            {
                "event_id": f"event-{number:08d}",
                "type": event_type,
                "obligation_id": obligation_id,
                "payload": copy.deepcopy(payload or {}),
                "created_at": utc_now(),
            }
        )

    def _config(self, key: str, default: Any) -> Any:
        routing = self.config.get("routing", {})
        return routing.get(key, default) if isinstance(routing, dict) else default

    def _literature_budget(self, key: str, default: Any) -> Any:
        budgets = self.config.get("literature_budget", {})
        return budgets.get(key, default) if isinstance(budgets, dict) else default

    def _enforce_task_budget(self, pipeline: str, role: str) -> None:
        existing = [
            task for task in self.state["tasks"].values() if task.get("pipeline") == pipeline
        ]
        budgets = self.config.get("pipeline_budgets", {})
        if isinstance(budgets, dict):
            hard = budgets.get(f"{pipeline}_hard_budget")
            soft = budgets.get(f"{pipeline}_soft_budget")
            if hard is not None and len(existing) >= int(hard):
                raise ProjectError(f"{pipeline} hard budget exhausted")
            if soft is not None and len(existing) >= int(soft):
                self._event(
                    "PIPELINE_SOFT_BUDGET_EXCEEDED",
                    "<campaign>",
                    {"pipeline": pipeline, "calls": len(existing)},
                )
        if pipeline != "literature":
            return
        maximum = int(self._literature_budget("max_literature_calls", 1000000))
        if len(existing) >= maximum:
            raise ProjectError(
                "Literature hard budget exhausted; verdict must be INSUFFICIENT_SEARCH"
            )
        readers = [
            task
            for task in existing
            if task.get("role") in {"literature_reader", "literature_deep_reader"}
        ]
        if role in {"literature_reader", "literature_deep_reader"} and len(readers) >= int(
            self._literature_budget("max_reader_calls", 1000000)
        ):
            raise ProjectError("Reader budget exhausted; verdict must be INSUFFICIENT_SEARCH")
        deep_reads = [task for task in existing if task.get("role") == "literature_deep_reader"]
        if role == "literature_deep_reader" and len(deep_reads) >= int(
            self._literature_budget("max_deep_reads", 1000000)
        ):
            raise ProjectError("Deep-read budget exhausted; verdict must be INSUFFICIENT_SEARCH")

    def _save(self) -> None:
        lock = getattr(self, "_lock", None)
        context = lock if lock is not None else threading.RLock()
        with context:
            budget = self.resource_budget.snapshot() if hasattr(self, "resource_budget") else {}
            self.state["resource_budget"] = budget
            if self.state_path is None:
                return
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(self.state_path.suffix + f".{uuid.uuid4().hex}.tmp")
            temp.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for attempt in range(6):
                try:
                    temp.replace(self.state_path)
                    break
                except PermissionError:
                    if attempt == 5:
                        temp.unlink(missing_ok=True)
                        raise
                    # A reader can transiently retain the replace handle.
                    # Retry is bounded; persistent I/O failure still surfaces.
                    time.sleep(0.01 * (attempt + 1))


class AsynchronousPipelineRuntime:
    """Non-blocking executor for independently completing pipeline tasks.

    ``start_window`` returns immediately after submission.  The Planner can
    continue scheduling unrelated work and periodically call ``poll`` to turn
    completed futures into durable events.
    """

    def __init__(
        self,
        scheduler: AsyncDAGScheduler,
        handlers: dict[str, Any],
        *,
        max_workers: int = 8,
    ):
        self.scheduler = scheduler
        self.handlers = dict(handlers)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.futures: dict[str, Future] = {}
        self.contexts: dict[str, TaskExecutionContext] = {}
        self.reservations: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._closed = False
        scheduler.bind_runtime(self)

    def start_window(self, limits: dict[str, int]) -> dict[str, list[dict]]:
        if self._closed:
            raise ProjectError("Pipeline runtime is shut down")
        dispatched = self.scheduler.dispatch_window(limits)
        with self._lock:
            for pipeline, tasks in dispatched.items():
                if pipeline not in self.handlers:
                    for task in tasks:
                        self.scheduler.fail_task(
                            task["task_id"],
                            failure_kind="NO_HANDLER",
                            detail=f"No runtime handler for {pipeline}",
                        )
                    continue
                for task in tasks:
                    task_id = task["task_id"]
                    try:
                        payload = (
                            task.get("payload", {}) if isinstance(task.get("payload"), dict) else {}
                        )
                        defaults = self.scheduler.config.get("resource_estimates", {})
                        defaults = defaults if isinstance(defaults, dict) else {}
                        reservation = self.scheduler.resource_budget.reserve(
                            {
                                "provider_calls": 1,
                                "input_tokens": int(
                                    payload.get(
                                        "input_tokens_estimate", defaults.get("input_tokens", 1024)
                                    )
                                    or 0
                                ),
                                "output_tokens": int(
                                    payload.get(
                                        "output_tokens_estimate", defaults.get("output_tokens", 512)
                                    )
                                    or 0
                                ),
                                "reasoning_tokens": int(
                                    payload.get(
                                        "reasoning_tokens_estimate",
                                        defaults.get("reasoning_tokens", 512),
                                    )
                                    or 0
                                ),
                                "cached_tokens": int(
                                    payload.get(
                                        "cached_tokens_estimate", defaults.get("cached_tokens", 0)
                                    )
                                    or 0
                                ),
                            }
                        )
                    except ProjectError as exc:
                        self.scheduler.fail_task(
                            task_id, failure_kind="BUDGET_EXHAUSTED", detail=str(exc)
                        )
                        continue
                    context = TaskExecutionContext(task_id)
                    self.contexts[task_id] = context
                    self.reservations[task_id] = reservation
                    task_record = self.scheduler.state["tasks"].get(task_id)
                    if task_record is not None:
                        task_record["call_id"] = task_record.get("call_id") or (
                            f"pipeline-call-{task_id}-{task_record.get('attempt_id', 'initial')}"
                        )
                        self.scheduler._save()
                        task["call_id"] = task_record["call_id"]
                    handler = self.handlers[pipeline]
                    self.futures[task_id] = self.executor.submit(
                        self._invoke_handler, handler, copy.deepcopy(task), context
                    )
        return dispatched

    @staticmethod
    def _invoke_handler(handler, task: dict, context: TaskExecutionContext):
        try:
            signature = inspect.signature(handler)
            accepts_context = len(signature.parameters) >= 2
        except (TypeError, ValueError):
            accepts_context = False
        if accepts_context:
            return handler(task, context)
        return handler(task)

    def request_cancel(self, task_id: str) -> bool:
        with self._lock:
            context = self.contexts.get(task_id)
            if context is None:
                future = self.futures.get(task_id)
                return bool(future and future.cancel())
            cancelled = context.cancel()
            future = self.futures.get(task_id)
            if future is not None and not future.running():
                cancelled = bool(future.cancel()) or cancelled
            return cancelled

    def poll(self) -> list[dict]:
        completed = []
        with self._lock:
            for task_id, future in list(self.futures.items()):
                task = self.scheduler.snapshot()["tasks"].get(task_id, {})
                if task.get("status") in {"CANCELLED", "REDIRECTED"}:
                    self.request_cancel(task_id)
                    if future.done() or future.cancelled():
                        reservation = self.reservations.get(task_id)
                        if reservation is not None:
                            self.scheduler.reconcile_resource_usage(
                                task_id, reservation, None, interrupted=True
                            )
                        self.futures.pop(task_id, None)
                        self.contexts.pop(task_id, None)
                        self.reservations.pop(task_id, None)
                        completed.append(task)
                    continue
                if task.get("status") == "CANCEL_REQUESTED":
                    self.request_cancel(task_id)
                    if not future.done():
                        continue
                if not future.done():
                    continue
                self.futures.pop(task_id, None)
                self.contexts.pop(task_id, None)
                reservation = self.reservations.pop(task_id, None)
                try:
                    result = future.result()
                    if reservation is not None:
                        self.scheduler.reconcile_resource_usage(
                            task_id, reservation, result or {}, interrupted=False
                        )
                        reservation = None
                    self.scheduler.record_resource_usage(task, result or {})
                    current = self.scheduler.snapshot()["tasks"].get(task_id, {})
                    if current.get("status") == "CANCEL_REQUESTED":
                        completed.append(self.scheduler.complete_task(task_id, result or {}))
                    else:
                        completed.append(self.scheduler.complete_task(task_id, result or {}))
                except Exception as exc:
                    current = self.scheduler.snapshot()["tasks"].get(task_id, {})
                    if reservation is not None:
                        self.scheduler.reconcile_resource_usage(
                            task_id, reservation, None, interrupted=True
                        )
                    if current.get("status") == "CANCEL_REQUESTED":
                        completed.append(self.scheduler.interrupt_task(task_id, detail=str(exc)))
                    else:
                        completed.append(
                            self.scheduler.fail_task(
                                task_id,
                                failure_kind="HANDLER_ERROR",
                                detail=f"{type(exc).__name__}: {exc}",
                            )
                        )
        return completed

    def pending(self) -> list[str]:
        with self._lock:
            return sorted(self.futures)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
            for context in self.contexts.values():
                context.cancel()
        self.executor.shutdown(wait=wait, cancel_futures=not wait)
