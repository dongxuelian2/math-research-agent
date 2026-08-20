"""Typed contracts for the durable execution control plane.

The runtime owns execution authorization and current execution state.  It does
not own mathematical Truth, ResearchMap, or governance semantics.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Callable, Mapping, Protocol


RUNTIME_SCHEMA_VERSION = 1


class RuntimeErrorBase(RuntimeError):
    """Base class for fail-closed runtime errors."""


class RuntimeConflict(RuntimeErrorBase):
    """A conditional state transition or unique ownership claim lost a race."""


class InvalidRuntimeTransition(RuntimeErrorBase):
    """An object was asked to perform an illegal typed state transition."""


class ArtifactIntegrityError(RuntimeErrorBase):
    """A filesystem artifact is missing, outside the project, or hash-invalid."""


class FaultInjected(RuntimeErrorBase):
    """A deterministic test fault interrupted a cross-store saga."""


class JobState(StrEnum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    RESULT_ACCEPTED = "RESULT_ACCEPTED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class AttemptState(StrEnum):
    CREATED = "CREATED"
    READY = "READY"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    RESULT_RECORDED = "RESULT_RECORDED"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    ORPHANED = "ORPHANED"
    BLOCKED_MISSING_ARTIFACT = "BLOCKED_MISSING_ARTIFACT"


ATTEMPT_TRANSITIONS: Mapping[str, frozenset[str]] = {
    AttemptState.CREATED: frozenset({AttemptState.READY}),
    AttemptState.READY: frozenset(
        {
            AttemptState.LEASED,
            AttemptState.CANCEL_REQUESTED,
            AttemptState.FAILED_TERMINAL,
        }
    ),
    AttemptState.LEASED: frozenset(
        {
            AttemptState.RUNNING,
            AttemptState.CANCEL_REQUESTED,
            AttemptState.ORPHANED,
        }
    ),
    AttemptState.RUNNING: frozenset(
        {
            AttemptState.RESULT_RECORDED,
            AttemptState.FAILED_RETRYABLE,
            AttemptState.FAILED_TERMINAL,
            AttemptState.CANCEL_REQUESTED,
            AttemptState.ORPHANED,
        }
    ),
    AttemptState.CANCEL_REQUESTED: frozenset(
        {
            AttemptState.CANCELLED,
            AttemptState.RESULT_RECORDED,
            AttemptState.ORPHANED,
        }
    ),
    AttemptState.ORPHANED: frozenset(
        {
            AttemptState.LEASED,
            AttemptState.RESULT_RECORDED,
            AttemptState.BLOCKED_MISSING_ARTIFACT,
        }
    ),
    AttemptState.RESULT_RECORDED: frozenset(
        {AttemptState.COMPLETED, AttemptState.BLOCKED_MISSING_ARTIFACT}
    ),
    AttemptState.BLOCKED_MISSING_ARTIFACT: frozenset({AttemptState.RESULT_RECORDED}),
    AttemptState.COMPLETED: frozenset(),
    AttemptState.FAILED_RETRYABLE: frozenset(),
    AttemptState.FAILED_TERMINAL: frozenset(),
    AttemptState.CANCELLED: frozenset(),
}


class OutboxState(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    DISPATCHED = "DISPATCHED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    DEAD_LETTER = "DEAD_LETTER"


class EffectState(StrEnum):
    PREPARED = "PREPARED"
    DOMAIN_APPLIED = "DOMAIN_APPLIED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    BLOCKED = "BLOCKED"


class ReconciliationAction(StrEnum):
    NO_ACTION = "NO_ACTION"
    REDISPATCH = "REDISPATCH"
    MARK_ORPHANED = "MARK_ORPHANED"
    INGEST_EXISTING_RESULT = "INGEST_EXISTING_RESULT"
    RETRY_NEW_ATTEMPT = "RETRY_NEW_ATTEMPT"
    WAIT = "WAIT"
    BLOCK_MISSING_ARTIFACT = "BLOCK_MISSING_ARTIFACT"
    REPAIR_PROJECTION = "REPAIR_PROJECTION"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class FaultPoint(StrEnum):
    AFTER_INTENT_COMMIT = "AFTER_INTENT_COMMIT"
    BEFORE_DISPATCH = "BEFORE_DISPATCH"
    AFTER_PROVIDER_RESULT = "AFTER_PROVIDER_RESULT"
    AFTER_ARTIFACT_WRITE = "AFTER_ARTIFACT_WRITE"
    BEFORE_RESULT_DB_COMMIT = "BEFORE_RESULT_DB_COMMIT"
    BEFORE_EFFECT_SLOT_COMMIT = "BEFORE_EFFECT_SLOT_COMMIT"
    AFTER_EFFECT_SLOT_BEFORE_DOMAIN_APPLY = "AFTER_EFFECT_SLOT_BEFORE_DOMAIN_APPLY"
    AFTER_DOMAIN_APPLY_BEFORE_ACK = "AFTER_DOMAIN_APPLY_BEFORE_ACK"


class FaultInjector:
    """Deterministic, one-shot fault injection used by crash-recovery tests."""

    def __init__(self, *points: str | FaultPoint):
        self._points = {str(point) for point in points}

    def hit(self, point: str | FaultPoint) -> None:
        value = str(point)
        if value in self._points:
            self._points.remove(value)
            raise FaultInjected(value)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    raw = value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:32]}"


class RuntimeBackend(Protocol):
    """Storage seam used by execution controllers and domain effect adapters."""

    def create_logical_job(self, **values: Any) -> dict[str, Any]: ...

    def create_attempt_intent(self, **values: Any) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def claim_attempt(self, attempt_id: str, *, owner: str, ttl_seconds: float) -> dict[str, Any]: ...

    def heartbeat(
        self,
        attempt_id: str,
        *,
        lease_token: str,
        generation: int,
        ttl_seconds: float,
    ) -> dict[str, Any]: ...

    def record_result(self, **values: Any) -> dict[str, Any]: ...

    def reconcile(self) -> list[dict[str, Any]]: ...

    def apply_effect_once(
        self,
        *,
        logical_job_id: str,
        effect_kind: str,
        semantic_target_type: str,
        semantic_target_id: str,
        source_result_id: str,
        apply: Callable[[str], Any],
        recover: Callable[[str], Any | None] | None = None,
        claim_snapshot_hash: str | None = None,
    ) -> tuple[dict[str, Any], Any]: ...
