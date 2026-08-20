"""Provider dispatch controller enforcing durable intent before invocation."""

from __future__ import annotations

import copy
import json
import threading
from typing import Any, Callable, Mapping

from .runtime_artifacts import RuntimeArtifactStore
from .runtime_backend import SQLiteRuntimeBackend
from .runtime_model import (
    AttemptState,
    FaultInjector,
    FaultPoint,
    OutboxState,
    content_hash,
)
from .runtime_bindings import CrossPlaneExecutionBinding


class DurableProviderDispatcher:
    """Execute at-least-once providers behind durable attempts and result fencing."""

    def __init__(
        self,
        backend: SQLiteRuntimeBackend,
        *,
        owner: str | None = None,
        lease_ttl_seconds: float = 300.0,
    ):
        self.backend = backend
        self.artifacts = RuntimeArtifactStore(backend.project_root)
        self.owner = owner or f"dispatcher-{threading.get_native_id()}"
        self.lease_ttl_seconds = float(lease_ttl_seconds)

    def execute(
        self,
        *,
        logical_job_id: str,
        provider: str,
        model: str | None,
        reasoning_tier: str | None,
        payload: Mapping[str, Any],
        invoke: Callable[[], dict[str, Any]],
        retry_fallback_reason: str | None = None,
        claim_snapshot_hash: str | None = None,
        directive_context_refs: tuple[str, ...] = (),
        execution_binding: CrossPlaneExecutionBinding | dict[str, Any] | None = None,
        binding_validator=None,
        fault_injector: FaultInjector | None = None,
        on_started: Callable[[dict[str, Any]], None] | None = None,
        on_finished: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        durable_payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
        payload_hash = content_hash(durable_payload)
        attempt, outbox = self.backend.create_attempt_intent(
            logical_job_id=logical_job_id,
            provider=provider,
            model=model,
            reasoning_tier=reasoning_tier,
            payload_hash=payload_hash,
            dispatch_kind="PROVIDER_INVOCATION",
            claim_snapshot_hash=claim_snapshot_hash,
            directive_context_refs=directive_context_refs,
            execution_binding=execution_binding,
            retry_fallback_reason=retry_fallback_reason,
        )
        if fault_injector is not None:
            fault_injector.hit(FaultPoint.AFTER_INTENT_COMMIT)
        outbox_claim = self.backend.claim_outbox(
            outbox["outbox_id"], owner=self.owner, ttl_seconds=self.lease_ttl_seconds
        )
        lease = self.backend.claim_attempt(
            attempt["attempt_id"], owner=self.owner, ttl_seconds=self.lease_ttl_seconds
        )
        self.backend.transition_attempt(
            attempt["attempt_id"],
            AttemptState.RUNNING,
            actor=self.owner,
            expected_states={AttemptState.LEASED},
            lease_token=lease["lease_token"],
            generation=lease["generation"],
        )
        self.backend.transition_outbox(
            outbox["outbox_id"],
            OutboxState.DISPATCHED,
            claim_token=outbox_claim["claim_token"],
            claim_generation=outbox_claim["claim_generation"],
            actor=self.owner,
        )
        if on_started is not None:
            on_started({**attempt, **lease})
        if fault_injector is not None:
            fault_injector.hit(FaultPoint.BEFORE_DISPATCH)
        try:
            response = invoke()
        except BaseException as exc:
            current = self.backend.get_attempt(attempt["attempt_id"])
            if current and current["state"] == AttemptState.CANCEL_REQUESTED:
                self.backend.finalize_cancel(attempt["attempt_id"], actor=self.owner)
                outbox_target = OutboxState.DEAD_LETTER
            else:
                self.backend.transition_attempt(
                    attempt["attempt_id"],
                    AttemptState.FAILED_RETRYABLE,
                    actor=self.owner,
                    expected_states={AttemptState.RUNNING},
                    lease_token=lease["lease_token"],
                    generation=lease["generation"],
                    metadata={"error_type": type(exc).__name__},
                )
                outbox_target = OutboxState.FAILED_RETRYABLE
            self.backend.transition_outbox(
                outbox["outbox_id"],
                outbox_target,
                claim_token=outbox_claim["claim_token"],
                claim_generation=outbox_claim["claim_generation"],
                actor=self.owner,
                last_error=f"{type(exc).__name__}: {exc}",
            )
            if on_finished is not None:
                on_finished(attempt["attempt_id"])
            raise
        if fault_injector is not None:
            fault_injector.hit(FaultPoint.AFTER_PROVIDER_RESULT)
        result_key = f"{attempt['attempt_id']}:provider-result"
        artifact = self.artifacts.persist_and_register(
            self.backend,
            response,
            artifact_kind="PROVIDER_RESULT",
            producer_attempt_id=attempt["attempt_id"],
            result_metadata={
                "completion_status": "SUCCESS",
                "idempotency_key": result_key,
                "provider_metadata": {
                    "provider": provider,
                    "model": model,
                    "reasoning_tier": reasoning_tier,
                },
            },
            fault_injector=fault_injector,
        )
        if fault_injector is not None:
            fault_injector.hit(FaultPoint.BEFORE_RESULT_DB_COMMIT)
        result = self.backend.record_result(
            attempt_id=attempt["attempt_id"],
            artifact_id=artifact["artifact_id"],
            completion_status="SUCCESS",
            idempotency_key=result_key,
            provider_metadata={
                "provider": provider,
                "model": model,
                "reasoning_tier": reasoning_tier,
            },
            lease_token=lease["lease_token"],
            generation=lease["generation"],
            execution_binding=execution_binding,
            actor=self.owner,
        )
        if not result["authoritative"]:
            self.backend.transition_outbox(
                outbox["outbox_id"],
                OutboxState.FAILED_RETRYABLE,
                claim_token=outbox_claim["claim_token"],
                claim_generation=outbox_claim["claim_generation"],
                actor=self.owner,
                last_error=str(result.get("fencing_rejection") or "STALE_FENCED"),
            )
            returned = copy.deepcopy(response)
            returned["runtime"] = {
                "logical_job_id": logical_job_id,
                "attempt_id": attempt["attempt_id"],
                "outbox_id": outbox["outbox_id"],
                "result_id": result["result_id"],
                "accepted": False,
                "authoritative": False,
                "artifact_id": artifact["artifact_id"],
                "fencing_rejection": result.get("fencing_rejection"),
            }
            if on_finished is not None:
                on_finished(attempt["attempt_id"])
            return returned
        winner = self.backend.accept_result(
            logical_job_id,
            actor=self.owner,
            binding_validator=binding_validator,
        )
        self.backend.transition_outbox(
            outbox["outbox_id"],
            OutboxState.ACKNOWLEDGED,
            claim_token=outbox_claim["claim_token"],
            claim_generation=outbox_claim["claim_generation"],
            actor=self.owner,
        )
        returned = copy.deepcopy(response)
        returned["runtime"] = {
            "logical_job_id": logical_job_id,
            "attempt_id": attempt["attempt_id"],
            "outbox_id": outbox["outbox_id"],
            "result_id": result["result_id"],
            "accepted": winner["result_id"] == result["result_id"],
            "artifact_id": artifact["artifact_id"],
        }
        if on_finished is not None:
            on_finished(attempt["attempt_id"])
        return returned
