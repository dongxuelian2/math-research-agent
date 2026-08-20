"""Deterministic recovery of runtime/artifact split states."""

from __future__ import annotations

import time
from typing import Any

from .runtime_artifacts import RuntimeArtifactStore
from .runtime_backend import SQLiteRuntimeBackend
from .runtime_model import (
    ArtifactIntegrityError,
    AttemptState,
    JobState,
    OutboxState,
    ReconciliationAction,
    RuntimeConflict,
)


class RuntimeReconciler:
    def __init__(self, backend: SQLiteRuntimeBackend):
        self.backend = backend
        self.artifacts = RuntimeArtifactStore(backend.project_root)
        self.actions: list[dict[str, Any]] = []

    def _record(self, action: str, object_type: str, object_id: str, reason: str, **details):
        value = self.backend.record_reconciliation(
            action,
            object_type=object_type,
            object_id=object_id,
            reason=reason,
            details=details,
        )
        self.actions.append(value)

    def run(self) -> list[dict[str, Any]]:
        for attempt in self.backend.orphan_expired_leases():
            self._record(
                ReconciliationAction.MARK_ORPHANED,
                "ATTEMPT",
                attempt["attempt_id"],
                "lease expired; semantic domains were not mutated",
            )
        self._reconcile_outbox()
        self._reconcile_manifests()
        self._verify_registered_results()
        self._accept_pending_results()
        return self.actions

    def _reconcile_outbox(self) -> None:
        for record in self.backend.list_rows("outbox"):
            if (
                record["state"] == OutboxState.CLAIMED
                and float(record["claim_expires_at"] or 0) <= time.time()
            ):
                with self.backend._transaction() as connection:
                    cursor = connection.execute(
                        """UPDATE outbox SET state = ?, last_error = ?, retry_count = retry_count + 1,
                               version = version + 1
                           WHERE outbox_id = ? AND state = ? AND version = ?""",
                        (
                            OutboxState.FAILED_RETRYABLE,
                            "stale dispatcher claim",
                            record["outbox_id"],
                            OutboxState.CLAIMED,
                            record["version"],
                        ),
                    )
                    changed = cursor.rowcount == 1
                    if changed:
                        attempt = connection.execute(
                            "SELECT logical_job_id FROM attempts WHERE attempt_id = ?",
                            (record["attempt_id"],),
                        ).fetchone()
                        self.backend._journal(
                            connection,
                            object_type="OUTBOX",
                            object_id=record["outbox_id"],
                            from_state=OutboxState.CLAIMED,
                            to_state=OutboxState.FAILED_RETRYABLE,
                            transition_kind="STALE_OUTBOX_RECLAIMED",
                            actor="reconciler",
                            attempt_id=record["attempt_id"],
                            logical_job_id=(attempt["logical_job_id"] if attempt else None),
                        )
                if changed:
                    self._record(
                        ReconciliationAction.REDISPATCH,
                        "OUTBOX",
                        record["outbox_id"],
                        "expired outbox claim is dispatchable again",
                    )
            elif record["state"] in {OutboxState.PENDING, OutboxState.FAILED_RETRYABLE}:
                self._record(
                    ReconciliationAction.REDISPATCH,
                    "OUTBOX",
                    record["outbox_id"],
                    "durable dispatch command is pending",
                )
            elif record["state"] == OutboxState.DISPATCHED:
                attempt = self.backend.get_attempt(record["attempt_id"])
                has_result = any(
                    item["attempt_id"] == record["attempt_id"]
                    for item in self.backend.list_rows("attempt_results")
                )
                if (
                    attempt is not None
                    and attempt["state"] in {AttemptState.ORPHANED, AttemptState.UNKNOWN_EXECUTION}
                    and not has_result
                ):
                    reason = (
                        "provider request may have been accepted, but no durable result or "
                        "acknowledgement exists"
                    )
                    self.backend.classify_unknown_execution(record["attempt_id"], reason=reason)
                    self._record(
                        ReconciliationAction.UNKNOWN_EXECUTION,
                        "ATTEMPT",
                        record["attempt_id"],
                        reason,
                        outbox_id=record["outbox_id"],
                    )
                    try:
                        self.backend.transition_outbox(
                            record["outbox_id"],
                            OutboxState.DEAD_LETTER,
                            claim_token=str(record["claim_token"] or ""),
                            claim_generation=int(record["claim_generation"]),
                            actor="reconciler",
                            last_error="UNKNOWN_EXECUTION: manual review required",
                        )
                    except RuntimeConflict:
                        # A concurrent dispatcher may have acknowledged or replaced
                        # the outbox; the durable UNKNOWN_EXECUTION state remains.
                        continue
                    self._record(
                        ReconciliationAction.MANUAL_REVIEW_REQUIRED,
                        "OUTBOX",
                        record["outbox_id"],
                        "unknown execution moved out of DISPATCHED; manual review required",
                        attempt_id=record["attempt_id"],
                    )

    def _reconcile_manifests(self) -> None:
        registered = {
            row["artifact_id"]: row for row in self.backend.list_rows("artifact_registry")
        }
        for manifest in self.artifacts.manifests():
            if manifest.get("invalid"):
                self._record(
                    ReconciliationAction.MANUAL_REVIEW_REQUIRED,
                    "ARTIFACT_MANIFEST",
                    manifest["manifest_path"],
                    manifest["reason"],
                )
                continue
            artifact_id = str(manifest.get("artifact_id") or "")
            artifact = registered.get(artifact_id)
            if artifact is None:
                try:
                    artifact = self.backend.register_artifact(
                        str(manifest["relative_path"]),
                        artifact_kind=str(manifest["artifact_kind"]),
                        producer_attempt_id=manifest.get("producer_attempt_id"),
                        expected_sha256=str(manifest["sha256"]),
                        artifact_id=artifact_id,
                    )
                except (ArtifactIntegrityError, RuntimeConflict, KeyError) as exc:
                    self._record(
                        ReconciliationAction.MANUAL_REVIEW_REQUIRED,
                        "ARTIFACT",
                        artifact_id or manifest["manifest_path"],
                        f"orphan artifact cannot be registered: {exc}",
                    )
                    continue
                self._record(
                    ReconciliationAction.REPAIR_PROJECTION,
                    "ARTIFACT",
                    artifact_id,
                    "registered a verified orphan filesystem artifact",
                )
            result = manifest.get("result_metadata")
            if not isinstance(result, dict) or not result.get("completion_status"):
                continue
            try:
                recorded = self.backend.record_result(
                    attempt_id=str(manifest["producer_attempt_id"]),
                    artifact_id=artifact["artifact_id"],
                    completion_status=str(result["completion_status"]),
                    idempotency_key=result.get("idempotency_key"),
                    provider_metadata=result.get("provider_metadata") or {},
                    reconcile_existing=True,
                    actor="reconciler",
                )
            except (ArtifactIntegrityError, RuntimeConflict) as exc:
                self._record(
                    ReconciliationAction.MANUAL_REVIEW_REQUIRED,
                    "ATTEMPT_RESULT",
                    artifact_id,
                    f"orphan result cannot be ingested: {exc}",
                )
                continue
            action = (
                ReconciliationAction.INGEST_EXISTING_RESULT
                if recorded["authoritative"]
                else ReconciliationAction.NO_ACTION
            )
            self._record(
                action,
                "ATTEMPT_RESULT",
                recorded["result_id"],
                "ingested existing result without another provider call",
            )

    def _verify_registered_results(self) -> None:
        for result in self.backend.list_rows("attempt_results"):
            try:
                self.backend.verify_artifact(result["artifact_id"])
            except ArtifactIntegrityError as exc:
                with self.backend._transaction() as connection:
                    connection.execute(
                        "UPDATE attempt_results SET ingestion_state = 'MISSING_ARTIFACT', "
                        "authoritative = 0 WHERE result_id = ?",
                        (result["result_id"],),
                    )
                    attempt = connection.execute(
                        "SELECT * FROM attempts WHERE attempt_id = ?", (result["attempt_id"],)
                    ).fetchone()
                    if attempt is not None and attempt["state"] in {
                        AttemptState.RESULT_RECORDED,
                        AttemptState.ORPHANED,
                    }:
                        connection.execute(
                            "UPDATE attempts SET state = ?, version = version + 1 WHERE attempt_id = ?",
                            (AttemptState.BLOCKED_MISSING_ARTIFACT, result["attempt_id"]),
                        )
                    connection.execute(
                        "UPDATE logical_jobs SET state = ?, version = version + 1 "
                        "WHERE logical_job_id = ? AND accepted_result_id = ?",
                        (JobState.BLOCKED, result["logical_job_id"], result["result_id"]),
                    )
                self._record(
                    ReconciliationAction.BLOCK_MISSING_ARTIFACT,
                    "ATTEMPT_RESULT",
                    result["result_id"],
                    str(exc),
                )

    def _accept_pending_results(self) -> None:
        for job in self.backend.list_rows("logical_jobs"):
            if job["accepted_result_id"] is not None:
                continue
            try:
                winner = self.backend.accept_result(job["logical_job_id"], actor="reconciler")
            except RuntimeConflict:
                continue
            self._record(
                ReconciliationAction.REPAIR_PROJECTION,
                "LOGICAL_JOB",
                job["logical_job_id"],
                "selected the durable first valid result",
                result_id=winner["result_id"],
            )
