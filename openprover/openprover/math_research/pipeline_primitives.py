"""Small, dependency-free primitives shared by the pipeline components."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from typing import Any

from .project import ProjectError, utc_now


PIPELINES = ("proof", "literature", "verification")
QUEUE_NAMES = {
    "proof": "PROOF_QUEUE",
    "literature": "LITERATURE_QUEUE",
    "verification": "VERIFICATION_QUEUE",
    "blocked": "BLOCKED_QUEUE",
}
OBLIGATION_STATUSES = frozenset(
    {
        "PROOF_READY",
        "PROOF_ACTIVE",
        "LITERATURE_READY",
        "LITERATURE_ACTIVE",
        "LITERATURE_PENDING",
        "VERIFICATION_READY",
        "VERIFICATION_ACTIVE",
        "BLOCKED_DEPENDENCY",
        "DUAL_TRACK",
        "CLOSED",
    }
)
LITERATURE_VERDICTS = frozenset(
    {
        "EXACT_RESULT_FOUND",
        "STRONGER_RESULT_FOUND",
        "PARTIAL_RESULT_FOUND",
        "METHOD_FOUND",
        "CONFLICTING_LITERATURE",
        "INSUFFICIENT_SEARCH",
        "NO_SUFFICIENT_RESULT_FOUND",
        "LITERATURE_PROVIDER_UNAVAILABLE",
    }
)
TERMINAL_TASK_STATUSES = frozenset(
    {
        "COMPLETE",
        "CANCELLED",
        "CANCELLED_BEFORE_START",
        "REDIRECTED",
        "INTERRUPTED",
        "ERROR",
        "COMPLETED_BEFORE_CANCEL",
        "CANCEL_FAILED",
    }
)
DISPATCHABLE_TASK_STATUSES = frozenset({"READY", "RETRY_READY"})


class AtomicResourceBudget:
    """Thread-safe campaign-level provider reservation gate."""

    FIELDS = (
        "provider_calls",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "total_tokens",
    )
    TOKEN_FIELDS = ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")

    def __init__(
        self,
        limits: dict | None = None,
        *,
        state: dict | None = None,
        unknown_usage_policy: str = "reserved_as_committed",
    ):
        self.limits = {key: int((limits or {}).get(key, 10**9)) for key in self.FIELDS}
        self._lock = threading.RLock()
        raw = copy.deepcopy(state or {})
        committed_raw = raw.get("committed") if isinstance(raw.get("committed"), dict) else raw
        self.committed = {key: int((committed_raw or {}).get(key, 0) or 0) for key in self.FIELDS}
        self.reserved = {key: 0 for key in self.FIELDS}
        self.reservations: dict[str, dict] = {}
        self.reconciliations = list(raw.get("reconciliations", [])) if isinstance(raw, dict) else []
        self.violations = list(raw.get("violations", [])) if isinstance(raw, dict) else []
        self.halted = bool(raw.get("halted", False)) if isinstance(raw, dict) else False
        self.unknown_usage_policy = str(
            (raw.get("unknown_usage_policy") if isinstance(raw, dict) else None)
            or unknown_usage_policy
        )
        if self.unknown_usage_policy != "reserved_as_committed":
            raise ProjectError("unsupported unknown usage policy")

    @property
    def usage(self) -> dict:
        return copy.deepcopy(self.committed)

    def reserve(self, estimate: dict | None = None) -> dict:
        estimate = {key: max(0, int((estimate or {}).get(key, 0) or 0)) for key in self.FIELDS}
        estimate["provider_calls"] = max(1, estimate["provider_calls"])
        if not estimate["total_tokens"]:
            estimate["total_tokens"] = estimate["input_tokens"] + estimate["output_tokens"]
        with self._lock:
            if self.halted:
                raise ProjectError("global resource budget halted after hard-cap reconciliation")
            projected = {
                key: self.committed[key] + self.reserved[key] + estimate[key] for key in self.FIELDS
            }
            if any(projected[key] > self.limits[key] for key in self.FIELDS):
                raise ProjectError("global resource budget exhausted")
            reservation_id = f"reservation-{uuid.uuid4().hex}"
            for key in self.FIELDS:
                self.reserved[key] += estimate[key]
            reservation = {
                "reservation_id": reservation_id,
                "reserved": copy.deepcopy(estimate),
                "reserved_at": utc_now(),
                "status": "RESERVED",
            }
            self.reservations[reservation_id] = reservation
            return copy.deepcopy(reservation)

    def reconcile(
        self,
        reservation: dict | str,
        actual_usage: dict | None,
        *,
        usage_known: bool,
    ) -> dict:
        reservation_id = (
            str(reservation.get("reservation_id"))
            if isinstance(reservation, dict)
            else str(reservation)
        )
        with self._lock:
            active = self.reservations.pop(reservation_id, None)
            if active is None:
                raise ProjectError(f"unknown or reconciled reservation: {reservation_id}")
            reserved = active["reserved"]
            for key in self.FIELDS:
                self.reserved[key] -= reserved[key]
            if usage_known:
                actual = {
                    key: max(0, int((actual_usage or {}).get(key, 0) or 0)) for key in self.FIELDS
                }
                actual["provider_calls"] = max(1, actual["provider_calls"])
                if not actual["total_tokens"]:
                    actual["total_tokens"] = actual["input_tokens"] + actual["output_tokens"]
                classification = "USAGE_RECONCILED"
            else:
                actual = copy.deepcopy(reserved)
                actual["provider_calls"] = max(1, actual["provider_calls"])
                classification = "USAGE_UNKNOWN_AFTER_INTERRUPT"
            released = {key: max(0, reserved[key] - actual[key]) for key in self.FIELDS}
            additional = {key: max(0, actual[key] - reserved[key]) for key in self.FIELDS}
            for key in self.FIELDS:
                self.committed[key] += actual[key]
            exceeded = {
                key: self.committed[key] - self.limits[key]
                for key in self.FIELDS
                if self.committed[key] > self.limits[key]
            }
            if exceeded:
                self.halted = True
                classification = "HARD_BUDGET_EXCEEDED_BY_COMPLETED_CALL"
                self.violations.append(
                    {
                        "reservation_id": reservation_id,
                        "exceeded": copy.deepcopy(exceeded),
                        "at": utc_now(),
                    }
                )
            result = {
                "reservation_id": reservation_id,
                "reserved": copy.deepcopy(reserved),
                "actual": actual,
                "released": released,
                "additional_commit": additional,
                "usage_known": bool(usage_known),
                "unknown_usage_policy": self.unknown_usage_policy if not usage_known else None,
                "status": classification,
                "hard_cap_exceeded": bool(exceeded),
                "exceeded": exceeded,
                "reconciled_at": utc_now(),
            }
            self.reconciliations.append(copy.deepcopy(result))
            return result

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "limits": copy.deepcopy(self.limits),
                "usage": copy.deepcopy(self.committed),
                "committed": copy.deepcopy(self.committed),
                "reserved": copy.deepcopy(self.reserved),
                "active_reservations": copy.deepcopy(self.reservations),
                "reconciliations": copy.deepcopy(self.reconciliations),
                "violations": copy.deepcopy(self.violations),
                "halted": self.halted,
                "unknown_usage_policy": self.unknown_usage_policy,
                "reserved_total_tokens": self.reserved["total_tokens"],
                "committed_total_tokens": self.committed["total_tokens"],
            }


class TaskExecutionContext:
    """Task-scoped cancellation context passed to production handlers."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.cancel_event = threading.Event()
        self._handle = None
        self._lock = threading.RLock()

    def set_handle(self, handle) -> None:
        with self._lock:
            self._handle = handle
            if self.cancel_event.is_set():
                self._interrupt_handle(handle)

    def cancel(self) -> bool:
        self.cancel_event.set()
        with self._lock:
            return self._interrupt_handle(self._handle)

    @staticmethod
    def _interrupt_handle(handle) -> bool:
        if handle is None:
            return False
        interrupt = getattr(handle, "interrupt", None)
        if not callable(interrupt):
            interrupt = getattr(handle, "cancel", None)
        if not callable(interrupt):
            return False
        try:
            interrupt()
            return True
        except Exception:
            return False


def initialize_pipeline_state(raw: dict | None) -> dict:
    """Create the current pipeline state; older snapshots are not accepted."""

    value = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    if raw is not None and value.get("schema_version") != 3:
        raise ProjectError(
            "Unsupported pipeline state schema; delete the snapshot and start a new run"
        )
    value["schema_version"] = 3
    value.setdefault("next_task_number", 1)
    value.setdefault("next_event_number", 1)
    value.setdefault("obligations", {})
    value.setdefault("tasks", {})
    value.setdefault("queues", {name: [] for name in QUEUE_NAMES.values()})
    for name in QUEUE_NAMES.values():
        value["queues"].setdefault(name, [])
    value.setdefault("active", {pipeline: [] for pipeline in PIPELINES})
    for pipeline in PIPELINES:
        value["active"].setdefault(pipeline, [])
    value.setdefault("events", [])
    value.setdefault("completed_task_ids", [])
    value.setdefault("dual_tracks", {})
    value.setdefault("processed_event_ids", [])
    value.setdefault("resource_budget", {"usage": {}, "limits": {}})
    value.setdefault("runtime_id", None)
    value.setdefault("sources", {})
    value.setdefault("source_identifiers", {})
    value.setdefault("literature", {})
    for key in (
        "lead_calls",
        "searcher_calls",
        "reader_calls",
        "sources_found",
        "sources_deep_read",
        "external_theorems_extracted",
        "verified_external_authorities",
        "exact_matches",
        "partial_matches",
        "method_matches",
        "literature_guided_closures",
        "duplicate_searches_avoided",
        "proof_calls_avoided_due_to_literature",
    ):
        value["literature"].setdefault(key, 0)
    completed = set(value["completed_task_ids"])
    for name, task_ids in value["queues"].items():
        value["queues"][name] = [task_id for task_id in task_ids if task_id not in completed]
    for pipeline, task_ids in value["active"].items():
        value["active"][pipeline] = [
            task_id
            for task_id in task_ids
            if value["tasks"].get(task_id, {}).get("status") == "ACTIVE"
        ]
    return value


def applicability_assumption_snapshot(
    obligation_id: str,
    current_target: str,
    current_assumptions: list[Any] | None = None,
    authorized_local_lemmas: list[Any] | None = None,
) -> str:
    payload = {
        "obligation_id": str(obligation_id),
        "normalized_current_target": " ".join(str(current_target).strip().split()).casefold(),
        "current_assumptions": copy.deepcopy(current_assumptions or []),
        "authorized_local_lemmas": copy.deepcopy(authorized_local_lemmas or []),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
