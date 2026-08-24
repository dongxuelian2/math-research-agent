"""SQLite/WAL implementation of the durable execution control plane."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from .project import utc_now
from .runtime_model import (
    ATTEMPT_TRANSITIONS,
    RUNTIME_SCHEMA_VERSION,
    ArtifactIntegrityError,
    AttemptState,
    EffectState,
    FaultInjector,
    FaultPoint,
    InvalidRuntimeTransition,
    JobState,
    OutboxState,
    ReconciliationAction,
    RuntimeConflict,
    canonical_json,
    content_hash,
    stable_id,
)
from .runtime_bindings import CrossPlaneExecutionBinding, binding_json, coerce_binding


_MIGRATION_1 = """
CREATE TABLE runtime_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    migrated_at TEXT NOT NULL
);

CREATE TABLE logical_jobs (
    logical_job_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    job_kind TEXT NOT NULL,
    semantic_target TEXT NOT NULL,
    directive_id TEXT,
    obligation_id TEXT,
    claim_snapshot_hash TEXT,
    research_map_version INTEGER,
    governance_ref TEXT,
    payload_artifact_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state TEXT NOT NULL,
    result_policy TEXT NOT NULL,
    accepted_result_id TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    schema_version INTEGER NOT NULL
);

CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY,
    logical_job_id TEXT NOT NULL REFERENCES logical_jobs(logical_job_id),
    attempt_number INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    reasoning_tier TEXT,
    payload_hash TEXT NOT NULL,
    claim_snapshot_hash TEXT,
    directive_context_refs TEXT NOT NULL,
    retry_fallback_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state TEXT NOT NULL,
    lease_owner TEXT,
    lease_token TEXT,
    lease_acquired_at TEXT,
    lease_expires_at REAL,
    heartbeat_at TEXT,
    generation INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    schema_version INTEGER NOT NULL,
    UNIQUE(logical_job_id, attempt_number)
);

CREATE TABLE outbox (
    outbox_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(attempt_id),
    payload_ref TEXT,
    payload_hash TEXT NOT NULL,
    dispatch_kind TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    claim_owner TEXT,
    claim_token TEXT,
    claim_generation INTEGER NOT NULL DEFAULT 0,
    claim_expires_at REAL,
    dispatched_at TEXT,
    acknowledged_at TEXT,
    last_error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE transition_journal (
    journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    transition_kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    attempt_id TEXT,
    logical_job_id TEXT,
    timestamp TEXT NOT NULL,
    causal_ref TEXT,
    metadata_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE INDEX transition_journal_object_idx
ON transition_journal(object_type, object_id, journal_id);

CREATE TABLE artifact_registry (
    artifact_id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    artifact_kind TEXT NOT NULL,
    producer_attempt_id TEXT REFERENCES attempts(attempt_id),
    created_at TEXT NOT NULL,
    durability_state TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    UNIQUE(relative_path, sha256)
);

CREATE TABLE attempt_results (
    result_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
    logical_job_id TEXT NOT NULL REFERENCES logical_jobs(logical_job_id),
    artifact_id TEXT NOT NULL REFERENCES artifact_registry(artifact_id),
    artifact_sha256 TEXT NOT NULL,
    provider_metadata TEXT NOT NULL,
    completion_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ingestion_state TEXT NOT NULL,
    authoritative INTEGER NOT NULL,
    fencing_rejection TEXT,
    schema_version INTEGER NOT NULL
);

CREATE INDEX attempt_results_job_idx
ON attempt_results(logical_job_id, created_at, result_id);

CREATE TABLE effect_slots (
    effect_slot_id TEXT PRIMARY KEY,
    logical_job_id TEXT NOT NULL REFERENCES logical_jobs(logical_job_id),
    effect_kind TEXT NOT NULL,
    semantic_target_type TEXT NOT NULL,
    semantic_target_id TEXT NOT NULL,
    source_result_id TEXT NOT NULL REFERENCES attempt_results(result_id),
    claim_snapshot_hash TEXT,
    status TEXT NOT NULL,
    prepared_at TEXT NOT NULL,
    domain_applied_at TEXT,
    applied_at TEXT,
    effect_artifact_ref TEXT,
    effect_metadata TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    schema_version INTEGER NOT NULL,
    UNIQUE(logical_job_id, effect_kind, semantic_target_type, semantic_target_id)
);

CREATE TABLE reconciliation_actions (
    reconciliation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE legacy_runtime_imports (
    import_id TEXT PRIMARY KEY,
    checkpoint_ref TEXT NOT NULL UNIQUE,
    classification TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
"""

_MIGRATION_2 = """
CREATE TABLE runtime_migration_history (
    target_version INTEGER PRIMARY KEY,
    migration_name TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""

_MIGRATION_3 = """
ALTER TABLE logical_jobs ADD COLUMN research_map_id TEXT;
ALTER TABLE logical_jobs ADD COLUMN research_map_hash TEXT;
ALTER TABLE logical_jobs ADD COLUMN tactical_session_id TEXT;
ALTER TABLE logical_jobs ADD COLUMN governance_object_type TEXT;
ALTER TABLE logical_jobs ADD COLUMN governance_object_id TEXT;
ALTER TABLE logical_jobs ADD COLUMN governance_source_hash TEXT;
ALTER TABLE logical_jobs ADD COLUMN cross_plane_binding TEXT;
ALTER TABLE attempts ADD COLUMN research_map_id TEXT;
ALTER TABLE attempts ADD COLUMN research_map_version INTEGER;
ALTER TABLE attempts ADD COLUMN research_map_hash TEXT;
ALTER TABLE attempts ADD COLUMN directive_id TEXT;
ALTER TABLE attempts ADD COLUMN tactical_session_id TEXT;
ALTER TABLE attempts ADD COLUMN governance_object_type TEXT;
ALTER TABLE attempts ADD COLUMN governance_object_id TEXT;
ALTER TABLE attempts ADD COLUMN governance_source_hash TEXT;
ALTER TABLE attempts ADD COLUMN cross_plane_binding TEXT;
ALTER TABLE attempt_results ADD COLUMN cross_plane_binding TEXT;
ALTER TABLE effect_slots ADD COLUMN cross_plane_binding TEXT;
"""


def _execute_script_in_transaction(connection: sqlite3.Connection, script: str) -> None:
    """Execute a migration without sqlite3.executescript's implicit commit."""

    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                connection.execute(statement)
            statement = ""
    if statement.strip():
        connection.execute(statement)


class SQLiteRuntimeBackend:
    """Project-isolated SQLite current-state authority with an append-only journal."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        db_path: str | Path | None = None,
        busy_timeout_ms: int = 5000,
        synchronous: str = "FULL",
    ):
        self.project_root = Path(project_root).resolve()
        self.runtime_root = self.project_root / "runtime"
        self.db_path = Path(db_path).resolve() if db_path else self.runtime_root / "control.sqlite3"
        try:
            self.db_path.relative_to(self.project_root)
        except ValueError as exc:
            raise ArtifactIntegrityError("Runtime database must be inside its project") from exc
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.synchronous = str(synchronous).upper()
        if self.synchronous not in {"FULL", "NORMAL", "EXTRA"}:
            raise ValueError("production synchronous mode must be FULL, NORMAL, or EXTRA")
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=max(0.001, self.busy_timeout_ms / 1000),
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute(f"PRAGMA synchronous = {self.synchronous}")
        return connection

    @contextlib.contextmanager
    def _transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self) -> None:
        connection = self._connect()
        try:
            mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
            if mode != "wal":
                raise RuntimeError(f"SQLite refused WAL mode: {mode}")

            # Lock before inspecting the schema.  Inspecting first and then
            # calling executescript() allowed another child run to observe
            # runtime_schema between CREATE TABLE and the singleton INSERT.
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_schema'"
            ).fetchone()
            if existing is None:
                _execute_script_in_transaction(connection, _MIGRATION_1)
                connection.execute(
                    "INSERT INTO runtime_schema(singleton, schema_version, migrated_at) "
                    "VALUES(1, ?, ?)",
                    (1, utc_now()),
                )
            schema_row = connection.execute(
                "SELECT schema_version FROM runtime_schema WHERE singleton = 1"
            ).fetchone()
            if schema_row is None:
                raise ArtifactIntegrityError("Runtime schema is missing its singleton row")
            version = int(schema_row[0])
            if version > RUNTIME_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Runtime database schema {version} is newer than supported "
                    f"{RUNTIME_SCHEMA_VERSION}"
                )
            if version == 1:
                _execute_script_in_transaction(connection, _MIGRATION_2)
                connection.execute(
                    "INSERT INTO runtime_migration_history(target_version, migration_name, applied_at) "
                    "VALUES(2, 'add_runtime_migration_history', ?)",
                    (utc_now(),),
                )
                connection.execute(
                    "UPDATE runtime_schema SET schema_version = 2, migrated_at = ? "
                    "WHERE singleton = 1 AND schema_version = 1",
                    (utc_now(),),
                )
                version = 2
            if version == 2:
                _execute_script_in_transaction(connection, _MIGRATION_3)
                connection.execute(
                    "INSERT INTO runtime_migration_history(target_version, migration_name, applied_at) "
                    "VALUES(3, 'add_cross_plane_execution_bindings', ?)",
                    (utc_now(),),
                )
                connection.execute(
                    "UPDATE runtime_schema SET schema_version = ?, migrated_at = ? "
                    "WHERE singleton = 1 AND schema_version = 2",
                    (RUNTIME_SCHEMA_VERSION, utc_now()),
                )
                version = RUNTIME_SCHEMA_VERSION
            if version < RUNTIME_SCHEMA_VERSION:
                raise RuntimeError(f"No forward migration registered from schema {version}")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def check(self) -> dict[str, Any]:
        with self._connect() as connection:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            return {
                "database": str(self.db_path),
                "schema_version": int(
                    connection.execute(
                        "SELECT schema_version FROM runtime_schema WHERE singleton = 1"
                    ).fetchone()[0]
                ),
                "journal_mode": str(
                    connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).upper(),
                "foreign_keys": bool(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "synchronous": int(connection.execute("PRAGMA synchronous").fetchone()[0]),
                "integrity_check": integrity,
                "control_plane_only": True,
                "filesystem_artifact_plane": True,
            }

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _binding_from_row(row: Mapping[str, Any]) -> CrossPlaneExecutionBinding | None:
        value = dict(row)
        raw = value.get("cross_plane_binding")
        if raw:
            return CrossPlaneExecutionBinding.from_dict(json.loads(str(raw)))
        claim = value.get("claim_snapshot_hash")
        if claim:
            return CrossPlaneExecutionBinding.capture(root_claim_snapshot_hash=str(claim))
        return None

    @staticmethod
    def _binding_json(value: CrossPlaneExecutionBinding | None) -> str | None:
        return binding_json(value)

    def _journal(
        self,
        connection: sqlite3.Connection,
        *,
        object_type: str,
        object_id: str,
        from_state: str | None,
        to_state: str,
        transition_kind: str,
        actor: str,
        attempt_id: str | None = None,
        logical_job_id: str | None = None,
        causal_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        serialized = canonical_json(dict(metadata or {}))
        connection.execute(
            """INSERT INTO transition_journal(
                   object_type, object_id, from_state, to_state, transition_kind, actor,
                   attempt_id, logical_job_id, timestamp, causal_ref, metadata_hash, metadata_json
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                object_type,
                object_id,
                from_state,
                to_state,
                transition_kind,
                actor,
                attempt_id,
                logical_job_id,
                utc_now(),
                causal_ref,
                content_hash(serialized.encode("utf-8")),
                serialized,
            ),
        )

    def journal(
        self, *, object_type: str | None = None, object_id: str | None = None
    ) -> list[dict]:
        query = "SELECT * FROM transition_journal WHERE 1=1"
        params: list[Any] = []
        if object_type is not None:
            query += " AND object_type = ?"
            params.append(object_type)
        if object_id is not None:
            query += " AND object_id = ?"
            params.append(object_id)
        query += " ORDER BY journal_id"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, params)]

    def create_logical_job(
        self,
        *,
        job_kind: str,
        semantic_target: str,
        idempotency_key: str,
        logical_job_id: str | None = None,
        directive_id: str | None = None,
        obligation_id: str | None = None,
        claim_snapshot_hash: str | None = None,
        research_map_version: int | None = None,
        governance_ref: str | None = None,
        payload_artifact_ref: str | None = None,
        execution_binding: CrossPlaneExecutionBinding | Mapping[str, Any] | None = None,
        result_policy: str = "FIRST_VALID_ACCEPTED_RESULT",
        actor: str = "runtime",
    ) -> dict[str, Any]:
        binding = coerce_binding(execution_binding)
        if binding is not None:
            if (
                claim_snapshot_hash is not None
                and claim_snapshot_hash != binding.root_claim_snapshot_hash
            ):
                raise RuntimeConflict("LogicalJob claim binding does not match execution context")
            claim_snapshot_hash = binding.root_claim_snapshot_hash
            directive_id = binding.directive_id or directive_id
            obligation_id = binding.research_obligation_id or obligation_id
            research_map_id = binding.research_map_id
            research_map_version = binding.research_map_version
            research_map_hash = binding.research_map_hash
            tactical_session_id = binding.tactical_session_id
            governance_object_type = binding.governance_object_type
            governance_object_id = binding.governance_object_id
            governance_source_hash = binding.governance_source_hash
            if governance_object_type and governance_object_id:
                governance_ref = f"{governance_object_type}:{governance_object_id}"
        else:
            research_map_id = None
            research_map_hash = None
            tactical_session_id = None
            governance_object_type = None
            governance_object_id = None
            governance_source_hash = None
        serialized_binding = self._binding_json(binding)
        job_id = logical_job_id or stable_id("job", idempotency_key)
        now = utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM logical_jobs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                if binding is not None and self._binding_from_row(existing) != binding:
                    raise RuntimeConflict("LogicalJob idempotency key is bound to another context")
                return dict(existing)
            connection.execute(
                """INSERT INTO logical_jobs(
                       logical_job_id, idempotency_key, job_kind, semantic_target, directive_id,
                       obligation_id, claim_snapshot_hash, research_map_version, governance_ref,
                       payload_artifact_ref, created_at, updated_at, state, result_policy,
                       schema_version, research_map_id, research_map_hash, tactical_session_id,
                       governance_object_type, governance_object_id, governance_source_hash,
                       cross_plane_binding
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    idempotency_key,
                    job_kind,
                    semantic_target,
                    directive_id,
                    obligation_id,
                    claim_snapshot_hash,
                    research_map_version,
                    governance_ref,
                    payload_artifact_ref,
                    now,
                    now,
                    JobState.CREATED,
                    result_policy,
                    RUNTIME_SCHEMA_VERSION,
                    research_map_id,
                    research_map_hash,
                    tactical_session_id,
                    governance_object_type,
                    governance_object_id,
                    governance_source_hash,
                    serialized_binding,
                ),
            )
            self._journal(
                connection,
                object_type="LOGICAL_JOB",
                object_id=job_id,
                from_state=None,
                to_state=JobState.CREATED,
                transition_kind="CREATE_LOGICAL_JOB",
                actor=actor,
                logical_job_id=job_id,
                metadata={"idempotency_key": idempotency_key},
            )
            return dict(
                connection.execute(
                    "SELECT * FROM logical_jobs WHERE logical_job_id = ?", (job_id,)
                ).fetchone()
            )

    def get_job(self, logical_job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(
                connection.execute(
                    "SELECT * FROM logical_jobs WHERE logical_job_id = ?", (logical_job_id,)
                ).fetchone()
            )

    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(
                connection.execute(
                    "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )

    def get_outbox(self, outbox_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(
                connection.execute(
                    "SELECT * FROM outbox WHERE outbox_id = ?", (outbox_id,)
                ).fetchone()
            )

    def create_attempt_intent(
        self,
        *,
        logical_job_id: str,
        provider: str,
        payload_hash: str,
        dispatch_kind: str,
        attempt_number: int | None = None,
        attempt_id: str | None = None,
        model: str | None = None,
        reasoning_tier: str | None = None,
        claim_snapshot_hash: str | None = None,
        directive_context_refs: Sequence[str] = (),
        retry_fallback_reason: str | None = None,
        payload_ref: str | None = None,
        execution_binding: CrossPlaneExecutionBinding | Mapping[str, Any] | None = None,
        actor: str = "runtime-controller",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Commit immutable intent and outbox atomically before external execution."""

        now = utc_now()
        with self._transaction() as connection:
            job = connection.execute(
                "SELECT * FROM logical_jobs WHERE logical_job_id = ?", (logical_job_id,)
            ).fetchone()
            if job is None:
                raise RuntimeConflict(f"Unknown LogicalJob: {logical_job_id}")
            job_value = dict(job)
            binding = coerce_binding(execution_binding) or self._binding_from_row(job_value)
            if binding is not None:
                if (
                    claim_snapshot_hash is not None
                    and claim_snapshot_hash != binding.root_claim_snapshot_hash
                ):
                    raise RuntimeConflict(
                        "AttemptIntent claim binding does not match execution context"
                    )
                claim_snapshot_hash = binding.root_claim_snapshot_hash
            serialized_binding = self._binding_json(binding)
            number = attempt_number
            if number is None:
                number = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM attempts "
                        "WHERE logical_job_id = ?",
                        (logical_job_id,),
                    ).fetchone()[0]
                )
            if number < 1:
                raise ValueError("attempt_number must be positive")
            intent_id = attempt_id or stable_id("attempt", logical_job_id, number)
            existing = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?", (intent_id,)
            ).fetchone()
            if existing is not None:
                outbox = connection.execute(
                    "SELECT * FROM outbox WHERE attempt_id = ?", (intent_id,)
                ).fetchone()
                if outbox is None:
                    raise RuntimeConflict("AttemptIntent exists without its transactional outbox")
                if binding is not None and self._binding_from_row(existing) != binding:
                    raise RuntimeConflict("AttemptIntent is bound to another context")
                return dict(existing), dict(outbox)
            collision = connection.execute(
                "SELECT attempt_id FROM attempts WHERE logical_job_id = ? AND attempt_number = ?",
                (logical_job_id, number),
            ).fetchone()
            if collision is not None:
                raise RuntimeConflict(
                    f"Attempt number {number} already belongs to {collision['attempt_id']}"
                )
            refs = canonical_json(list(directive_context_refs))
            connection.execute(
                """INSERT INTO attempts(
                       attempt_id, logical_job_id, attempt_number, provider, model, reasoning_tier,
                       payload_hash, claim_snapshot_hash, directive_context_refs,
                       retry_fallback_reason, created_at, updated_at, state, schema_version,
                       research_map_id, research_map_version, research_map_hash, directive_id,
                       tactical_session_id, governance_object_type, governance_object_id,
                       governance_source_hash, cross_plane_binding
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    intent_id,
                    logical_job_id,
                    number,
                    provider,
                    model,
                    reasoning_tier,
                    payload_hash,
                    claim_snapshot_hash or job["claim_snapshot_hash"],
                    refs,
                    retry_fallback_reason,
                    now,
                    now,
                    AttemptState.CREATED,
                    RUNTIME_SCHEMA_VERSION,
                    binding.research_map_id if binding else None,
                    binding.research_map_version if binding else None,
                    binding.research_map_hash if binding else None,
                    binding.directive_id if binding else None,
                    binding.tactical_session_id if binding else None,
                    binding.governance_object_type if binding else None,
                    binding.governance_object_id if binding else None,
                    binding.governance_source_hash if binding else None,
                    serialized_binding,
                ),
            )
            self._journal(
                connection,
                object_type="ATTEMPT",
                object_id=intent_id,
                from_state=None,
                to_state=AttemptState.CREATED,
                transition_kind="CREATE_ATTEMPT_INTENT",
                actor=actor,
                attempt_id=intent_id,
                logical_job_id=logical_job_id,
                metadata={"attempt_number": number, "payload_hash": payload_hash},
            )
            connection.execute(
                "UPDATE attempts SET state = ?, version = 1 WHERE attempt_id = ?",
                (AttemptState.READY, intent_id),
            )
            self._journal(
                connection,
                object_type="ATTEMPT",
                object_id=intent_id,
                from_state=AttemptState.CREATED,
                to_state=AttemptState.READY,
                transition_kind="INTENT_COMMITTED",
                actor=actor,
                attempt_id=intent_id,
                logical_job_id=logical_job_id,
            )
            outbox_id = stable_id("outbox", intent_id)
            connection.execute(
                """INSERT INTO outbox(
                       outbox_id, attempt_id, payload_ref, payload_hash, dispatch_kind,
                       state, created_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (
                    outbox_id,
                    intent_id,
                    payload_ref,
                    payload_hash,
                    dispatch_kind,
                    OutboxState.PENDING,
                    now,
                ),
            )
            self._journal(
                connection,
                object_type="OUTBOX",
                object_id=outbox_id,
                from_state=None,
                to_state=OutboxState.PENDING,
                transition_kind="ENQUEUE_TRANSACTIONAL_OUTBOX",
                actor=actor,
                attempt_id=intent_id,
                logical_job_id=logical_job_id,
                metadata={"dispatch_kind": dispatch_kind},
            )
            if job["state"] == JobState.CREATED:
                connection.execute(
                    "UPDATE logical_jobs SET state = ?, updated_at = ?, version = version + 1 "
                    "WHERE logical_job_id = ? AND state = ?",
                    (JobState.ACTIVE, now, logical_job_id, JobState.CREATED),
                )
                self._journal(
                    connection,
                    object_type="LOGICAL_JOB",
                    object_id=logical_job_id,
                    from_state=JobState.CREATED,
                    to_state=JobState.ACTIVE,
                    transition_kind="FIRST_ATTEMPT_READY",
                    actor=actor,
                    logical_job_id=logical_job_id,
                    attempt_id=intent_id,
                )
            return (
                dict(
                    connection.execute(
                        "SELECT * FROM attempts WHERE attempt_id = ?", (intent_id,)
                    ).fetchone()
                ),
                dict(
                    connection.execute(
                        "SELECT * FROM outbox WHERE outbox_id = ?", (outbox_id,)
                    ).fetchone()
                ),
            )

    def _rejected_transition(
        self,
        *,
        attempt: Mapping[str, Any],
        requested_state: str,
        actor: str,
        reason: str,
    ) -> None:
        with self._transaction() as connection:
            self._journal(
                connection,
                object_type="ATTEMPT",
                object_id=str(attempt["attempt_id"]),
                from_state=str(attempt["state"]),
                to_state=str(attempt["state"]),
                transition_kind="REJECTED_ILLEGAL_TRANSITION",
                actor=actor,
                attempt_id=str(attempt["attempt_id"]),
                logical_job_id=str(attempt["logical_job_id"]),
                metadata={"requested_state": requested_state, "reason": reason},
            )

    def transition_attempt(
        self,
        attempt_id: str,
        to_state: str | AttemptState,
        *,
        actor: str,
        expected_states: Sequence[str | AttemptState] | None = None,
        lease_token: str | None = None,
        generation: int | None = None,
        causal_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = str(to_state)
        rejected: tuple[dict[str, Any], str] | None = None
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise RuntimeConflict(f"Unknown AttemptIntent: {attempt_id}")
            attempt = dict(row)
            current = str(attempt["state"])
            expected = {str(value) for value in expected_states} if expected_states else None
            if expected is not None and current not in expected:
                rejected = (attempt, f"expected one of {sorted(expected)}, found {current}")
            elif target not in ATTEMPT_TRANSITIONS.get(current, frozenset()):
                rejected = (attempt, f"{current} -> {target} is not legal")
            elif lease_token is not None and (
                attempt["lease_token"] != lease_token or int(attempt["generation"]) != generation
            ):
                rejected = (attempt, "stale lease fencing token")
            if rejected is None:
                clear_lease = target in {
                    AttemptState.COMPLETED,
                    AttemptState.FAILED_RETRYABLE,
                    AttemptState.FAILED_TERMINAL,
                    AttemptState.CANCELLED,
                    AttemptState.ORPHANED,
                }
                cursor = connection.execute(
                    """UPDATE attempts SET state = ?, updated_at = ?, version = version + 1,
                           lease_owner = CASE WHEN ? THEN NULL ELSE lease_owner END,
                           lease_token = CASE WHEN ? THEN NULL ELSE lease_token END,
                           lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END
                       WHERE attempt_id = ? AND version = ? AND state = ?""",
                    (
                        target,
                        utc_now(),
                        clear_lease,
                        clear_lease,
                        clear_lease,
                        attempt_id,
                        attempt["version"],
                        current,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeConflict("Attempt CAS lost")
                self._journal(
                    connection,
                    object_type="ATTEMPT",
                    object_id=attempt_id,
                    from_state=current,
                    to_state=target,
                    transition_kind="ATTEMPT_STATE_TRANSITION",
                    actor=actor,
                    attempt_id=attempt_id,
                    logical_job_id=str(attempt["logical_job_id"]),
                    causal_ref=causal_ref,
                    metadata=metadata,
                )
                return dict(
                    connection.execute(
                        "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
                    ).fetchone()
                )
        assert rejected is not None
        self._rejected_transition(
            attempt=rejected[0], requested_state=target, actor=actor, reason=rejected[1]
        )
        if "stale lease" in rejected[1]:
            raise RuntimeConflict(rejected[1])
        raise InvalidRuntimeTransition(rejected[1])

    def claim_attempt(
        self,
        attempt_id: str,
        *,
        owner: str,
        ttl_seconds: float,
        allow_orphaned: bool = False,
    ) -> dict[str, Any]:
        if ttl_seconds <= 0:
            raise ValueError("lease ttl must be positive")
        allowed = {AttemptState.READY}
        if allow_orphaned:
            allowed.add(AttemptState.ORPHANED)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise RuntimeConflict(f"Unknown AttemptIntent: {attempt_id}")
            attempt = dict(row)
            if attempt["state"] not in allowed:
                raise RuntimeConflict(f"Attempt is not claimable: {attempt['state']}")
            generation = int(attempt["generation"]) + 1
            token = uuid.uuid4().hex
            now = utc_now()
            cursor = connection.execute(
                """UPDATE attempts SET state = ?, lease_owner = ?, lease_token = ?,
                       lease_acquired_at = ?, lease_expires_at = ?, heartbeat_at = ?,
                       generation = ?, updated_at = ?, version = version + 1
                   WHERE attempt_id = ? AND state = ? AND version = ?""",
                (
                    AttemptState.LEASED,
                    owner,
                    token,
                    now,
                    time.time() + ttl_seconds,
                    now,
                    generation,
                    now,
                    attempt_id,
                    attempt["state"],
                    attempt["version"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeConflict("Attempt lease CAS lost")
            self._journal(
                connection,
                object_type="ATTEMPT",
                object_id=attempt_id,
                from_state=str(attempt["state"]),
                to_state=AttemptState.LEASED,
                transition_kind="LEASE_ACQUIRED",
                actor=owner,
                attempt_id=attempt_id,
                logical_job_id=str(attempt["logical_job_id"]),
                metadata={"generation": generation, "ttl_seconds": ttl_seconds},
            )
            return dict(
                connection.execute(
                    "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )

    def heartbeat(
        self,
        attempt_id: str,
        *,
        lease_token: str,
        generation: int,
        ttl_seconds: float,
    ) -> dict[str, Any]:
        if ttl_seconds <= 0:
            raise ValueError("heartbeat ttl must be positive")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise RuntimeConflict(f"Unknown AttemptIntent: {attempt_id}")
            attempt = dict(row)
            if attempt["state"] not in {AttemptState.LEASED, AttemptState.RUNNING}:
                raise RuntimeConflict(f"Attempt has no renewable lease: {attempt['state']}")
            if (
                attempt["lease_token"] != lease_token
                or int(attempt["generation"]) != int(generation)
                or float(attempt["lease_expires_at"] or 0) <= time.time()
            ):
                raise RuntimeConflict("stale or expired heartbeat fencing token")
            now = utc_now()
            cursor = connection.execute(
                """UPDATE attempts SET heartbeat_at = ?, lease_expires_at = ?,
                       updated_at = ?, version = version + 1
                   WHERE attempt_id = ? AND version = ? AND lease_token = ? AND generation = ?""",
                (
                    now,
                    time.time() + ttl_seconds,
                    now,
                    attempt_id,
                    attempt["version"],
                    lease_token,
                    generation,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeConflict("Heartbeat CAS lost")
            self._journal(
                connection,
                object_type="ATTEMPT",
                object_id=attempt_id,
                from_state=str(attempt["state"]),
                to_state=str(attempt["state"]),
                transition_kind="HEARTBEAT_RENEWED",
                actor=str(attempt["lease_owner"]),
                attempt_id=attempt_id,
                logical_job_id=str(attempt["logical_job_id"]),
                metadata={"generation": generation, "ttl_seconds": ttl_seconds},
            )
            return dict(
                connection.execute(
                    "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )

    def claim_outbox(
        self,
        outbox_id: str,
        *,
        owner: str,
        ttl_seconds: float,
    ) -> dict[str, Any]:
        if ttl_seconds <= 0:
            raise ValueError("outbox claim ttl must be positive")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT o.*, a.logical_job_id FROM outbox o JOIN attempts a USING(attempt_id) "
                "WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise RuntimeConflict(f"Unknown outbox record: {outbox_id}")
            record = dict(row)
            claimable = record["state"] in {
                OutboxState.PENDING,
                OutboxState.FAILED_RETRYABLE,
            } or (
                record["state"] == OutboxState.CLAIMED
                and float(record["claim_expires_at"] or 0) <= time.time()
            )
            if not claimable:
                raise RuntimeConflict(f"Outbox record is not claimable: {record['state']}")
            token = uuid.uuid4().hex
            generation = int(record["claim_generation"]) + 1
            now = utc_now()
            cursor = connection.execute(
                """UPDATE outbox SET state = ?, claimed_at = ?, claim_owner = ?, claim_token = ?,
                       claim_generation = ?, claim_expires_at = ?, version = version + 1
                   WHERE outbox_id = ? AND version = ?""",
                (
                    OutboxState.CLAIMED,
                    now,
                    owner,
                    token,
                    generation,
                    time.time() + ttl_seconds,
                    outbox_id,
                    record["version"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeConflict("Outbox claim CAS lost")
            self._journal(
                connection,
                object_type="OUTBOX",
                object_id=outbox_id,
                from_state=str(record["state"]),
                to_state=OutboxState.CLAIMED,
                transition_kind="OUTBOX_CLAIMED",
                actor=owner,
                attempt_id=str(record["attempt_id"]),
                logical_job_id=str(record["logical_job_id"]),
                metadata={"claim_generation": generation, "ttl_seconds": ttl_seconds},
            )
            return dict(
                connection.execute(
                    "SELECT * FROM outbox WHERE outbox_id = ?", (outbox_id,)
                ).fetchone()
            )

    def transition_outbox(
        self,
        outbox_id: str,
        to_state: str | OutboxState,
        *,
        claim_token: str,
        claim_generation: int,
        actor: str,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        target = str(to_state)
        allowed = {
            OutboxState.DISPATCHED,
            OutboxState.ACKNOWLEDGED,
            OutboxState.FAILED_RETRYABLE,
            OutboxState.DEAD_LETTER,
        }
        if target not in allowed:
            raise InvalidRuntimeTransition(f"Unsupported outbox target: {target}")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT o.*, a.logical_job_id FROM outbox o JOIN attempts a USING(attempt_id) "
                "WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise RuntimeConflict(f"Unknown outbox record: {outbox_id}")
            record = dict(row)
            valid_from = {
                OutboxState.DISPATCHED: {OutboxState.CLAIMED},
                OutboxState.ACKNOWLEDGED: {OutboxState.DISPATCHED},
                OutboxState.FAILED_RETRYABLE: {
                    OutboxState.CLAIMED,
                    OutboxState.DISPATCHED,
                },
                OutboxState.DEAD_LETTER: {
                    OutboxState.CLAIMED,
                    OutboxState.DISPATCHED,
                    OutboxState.FAILED_RETRYABLE,
                },
            }[target]
            if record["state"] not in valid_from:
                raise InvalidRuntimeTransition(f"Outbox {record['state']} -> {target} is not legal")
            if record["claim_token"] != claim_token or int(record["claim_generation"]) != int(
                claim_generation
            ):
                raise RuntimeConflict("stale outbox claim fencing token")
            now = utc_now()
            cursor = connection.execute(
                """UPDATE outbox SET state = ?,
                       dispatched_at = CASE WHEN ? = 'DISPATCHED' THEN ? ELSE dispatched_at END,
                       acknowledged_at = CASE WHEN ? = 'ACKNOWLEDGED' THEN ? ELSE acknowledged_at END,
                       last_error = ?, retry_count = retry_count + CASE WHEN ? = 'FAILED_RETRYABLE' THEN 1 ELSE 0 END,
                       version = version + 1
                   WHERE outbox_id = ? AND version = ?""",
                (
                    target,
                    target,
                    now,
                    target,
                    now,
                    last_error,
                    target,
                    outbox_id,
                    record["version"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeConflict("Outbox transition CAS lost")
            self._journal(
                connection,
                object_type="OUTBOX",
                object_id=outbox_id,
                from_state=str(record["state"]),
                to_state=target,
                transition_kind="OUTBOX_STATE_TRANSITION",
                actor=actor,
                attempt_id=str(record["attempt_id"]),
                logical_job_id=str(record["logical_job_id"]),
                metadata={"last_error": last_error} if last_error else None,
            )
            return dict(
                connection.execute(
                    "SELECT * FROM outbox WHERE outbox_id = ?", (outbox_id,)
                ).fetchone()
            )

    def request_cancel(self, attempt_id: str, *, actor: str) -> dict[str, Any]:
        attempt = self.get_attempt(attempt_id)
        if attempt is None:
            raise RuntimeConflict(f"Unknown AttemptIntent: {attempt_id}")
        if attempt["state"] in {
            AttemptState.RESULT_RECORDED,
            AttemptState.COMPLETED,
            AttemptState.CANCELLED,
            AttemptState.FAILED_RETRYABLE,
            AttemptState.FAILED_TERMINAL,
        }:
            return attempt
        return self.transition_attempt(
            attempt_id,
            AttemptState.CANCEL_REQUESTED,
            actor=actor,
            expected_states={AttemptState.READY, AttemptState.LEASED, AttemptState.RUNNING},
        )

    def finalize_cancel(self, attempt_id: str, *, actor: str) -> dict[str, Any]:
        attempt = self.get_attempt(attempt_id)
        if attempt is None:
            raise RuntimeConflict(f"Unknown AttemptIntent: {attempt_id}")
        if attempt["state"] in {AttemptState.RESULT_RECORDED, AttemptState.COMPLETED}:
            return attempt
        if attempt["state"] == AttemptState.CANCELLED:
            return attempt
        return self.transition_attempt(
            attempt_id,
            AttemptState.CANCELLED,
            actor=actor,
            expected_states={AttemptState.CANCEL_REQUESTED},
        )

    def orphan_expired_leases(self, *, now: float | None = None) -> list[dict[str, Any]]:
        cutoff = time.time() if now is None else float(now)
        orphaned: list[dict[str, Any]] = []
        with self._connect() as connection:
            ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT attempt_id FROM attempts WHERE state IN (?, ?) "
                    "AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
                    (AttemptState.LEASED, AttemptState.RUNNING, cutoff),
                )
            ]
        for attempt_id in ids:
            try:
                orphaned.append(
                    self.transition_attempt(
                        attempt_id,
                        AttemptState.ORPHANED,
                        actor="reconciler",
                        expected_states={AttemptState.LEASED, AttemptState.RUNNING},
                        metadata={"reason": "LEASE_EXPIRED"},
                    )
                )
            except (RuntimeConflict, InvalidRuntimeTransition):
                continue
        return orphaned

    def classify_unknown_execution(self, attempt_id: str, *, reason: str) -> dict[str, Any]:
        """Durably preserve the fact that an external request may have run."""

        attempt = self.get_attempt(attempt_id)
        if attempt is None:
            raise RuntimeConflict(f"Unknown AttemptIntent: {attempt_id}")
        if attempt["state"] == AttemptState.ORPHANED:
            attempt = self.transition_attempt(
                attempt_id,
                AttemptState.UNKNOWN_EXECUTION,
                actor="reconciler",
                expected_states={AttemptState.ORPHANED},
                metadata={"reason": reason},
            )
        elif attempt["state"] != AttemptState.UNKNOWN_EXECUTION:
            return attempt
        with self._transaction() as connection:
            job = connection.execute(
                "SELECT * FROM logical_jobs WHERE logical_job_id = ?",
                (attempt["logical_job_id"],),
            ).fetchone()
            if (
                job is not None
                and job["accepted_result_id"] is None
                and job["state"] != JobState.BLOCKED
            ):
                connection.execute(
                    "UPDATE logical_jobs SET state = ?, updated_at = ?, version = version + 1 "
                    "WHERE logical_job_id = ? AND accepted_result_id IS NULL",
                    (JobState.BLOCKED, utc_now(), attempt["logical_job_id"]),
                )
                self._journal(
                    connection,
                    object_type="LOGICAL_JOB",
                    object_id=str(attempt["logical_job_id"]),
                    from_state=str(job["state"]),
                    to_state=JobState.BLOCKED,
                    transition_kind="UNKNOWN_EXTERNAL_EXECUTION",
                    actor="reconciler",
                    attempt_id=attempt_id,
                    logical_job_id=str(attempt["logical_job_id"]),
                    metadata={"reason": reason},
                )
        return attempt

    def _resolve_project_artifact(self, relative_path: str | Path) -> Path:
        raw = Path(relative_path)
        path = raw.resolve() if raw.is_absolute() else (self.project_root / raw).resolve()
        try:
            path.relative_to(self.project_root)
        except ValueError as exc:
            raise ArtifactIntegrityError("Artifact path escapes its project") from exc
        return path

    def register_artifact(
        self,
        relative_path: str | Path,
        *,
        artifact_kind: str,
        producer_attempt_id: str | None = None,
        expected_sha256: str | None = None,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        path = self._resolve_project_artifact(relative_path)
        if not path.is_file():
            raise ArtifactIntegrityError(f"Artifact does not exist: {path}")
        digest, size = sha256_file(path)
        if expected_sha256 is not None and digest != expected_sha256:
            raise ArtifactIntegrityError(
                f"Artifact hash mismatch: expected {expected_sha256}, found {digest}"
            )
        relative = path.relative_to(self.project_root).as_posix()
        identity = artifact_id or stable_id("artifact", artifact_kind, relative, digest)
        with self._transaction() as connection:
            if producer_attempt_id is not None:
                attempt = connection.execute(
                    "SELECT attempt_id FROM attempts WHERE attempt_id = ?", (producer_attempt_id,)
                ).fetchone()
                if attempt is None:
                    raise RuntimeConflict(f"Unknown producer attempt: {producer_attempt_id}")
            existing = connection.execute(
                "SELECT * FROM artifact_registry WHERE artifact_id = ?", (identity,)
            ).fetchone()
            if existing is not None:
                record = dict(existing)
                if (
                    record["relative_path"] != relative
                    or record["sha256"] != digest
                    or int(record["size"]) != size
                ):
                    raise ArtifactIntegrityError("Artifact identity collision")
                return record
            connection.execute(
                """INSERT INTO artifact_registry(
                       artifact_id, relative_path, sha256, size, artifact_kind,
                       producer_attempt_id, created_at, durability_state, schema_version
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    identity,
                    relative,
                    digest,
                    size,
                    artifact_kind,
                    producer_attempt_id,
                    utc_now(),
                    "VERIFIED",
                    RUNTIME_SCHEMA_VERSION,
                ),
            )
            self._journal(
                connection,
                object_type="ARTIFACT",
                object_id=identity,
                from_state=None,
                to_state="VERIFIED",
                transition_kind="REGISTER_ARTIFACT",
                actor="artifact-registry",
                attempt_id=producer_attempt_id,
                metadata={"relative_path": relative, "sha256": digest, "size": size},
            )
            return dict(
                connection.execute(
                    "SELECT * FROM artifact_registry WHERE artifact_id = ?", (identity,)
                ).fetchone()
            )

    def verify_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifact_registry WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise ArtifactIntegrityError(f"Unknown artifact: {artifact_id}")
        record = dict(row)
        path = self._resolve_project_artifact(record["relative_path"])
        if not path.is_file():
            raise ArtifactIntegrityError(f"Registered artifact is missing: {path}")
        digest, size = sha256_file(path)
        if digest != record["sha256"] or size != int(record["size"]):
            raise ArtifactIntegrityError(f"Registered artifact is corrupt: {path}")
        return record

    def record_result(
        self,
        *,
        attempt_id: str,
        artifact_id: str,
        completion_status: str,
        idempotency_key: str | None = None,
        provider_metadata: Mapping[str, Any] | None = None,
        lease_token: str | None = None,
        generation: int | None = None,
        reconcile_existing: bool = False,
        execution_binding: CrossPlaneExecutionBinding | Mapping[str, Any] | None = None,
        binding_validator: Callable[[CrossPlaneExecutionBinding | None], bool | str | None]
        | None = None,
        actor: str = "result-ingestor",
    ) -> dict[str, Any]:
        artifact = self.verify_artifact(artifact_id)
        key = idempotency_key or stable_id("result-key", attempt_id, artifact_id, completion_status)
        result_id = stable_id("result", key)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM attempt_results WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                return dict(existing)
            row = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise RuntimeConflict(f"Unknown AttemptIntent: {attempt_id}")
            attempt = dict(row)
            if artifact["producer_attempt_id"] not in {None, attempt_id}:
                raise ArtifactIntegrityError("Result artifact belongs to another attempt")
            attempt_binding = self._binding_from_row(attempt)
            supplied_binding = coerce_binding(execution_binding)
            binding_mismatch = supplied_binding is not None and not supplied_binding.matches(
                attempt_binding
            )
            binding_validation = True
            if binding_validator is not None:
                try:
                    binding_validation = binding_validator(attempt_binding)
                except Exception as exc:  # validators are a fail-closed boundary
                    binding_validation = f"execution binding validator failed: {type(exc).__name__}"
            binding_rejected = binding_validation is not True and binding_validation is not None
            serialized_binding = self._binding_json(attempt_binding)
            authoritative_now = time.time()
            lease_expires_at = attempt.get("lease_expires_at")
            lease_expired = lease_expires_at is None or float(lease_expires_at) <= authoritative_now
            fenced = False
            rejection = None
            if reconcile_existing:
                job = connection.execute(
                    "SELECT accepted_result_id FROM logical_jobs WHERE logical_job_id = ?",
                    (attempt["logical_job_id"],),
                ).fetchone()
                fenced = (
                    (job is not None and job["accepted_result_id"] is not None)
                    or lease_expired
                    or binding_mismatch
                    or binding_rejected
                )
                if job is not None and job["accepted_result_id"] is not None:
                    rejection = "logical job already has an accepted result"
            else:
                fenced = (
                    lease_token is None
                    or generation is None
                    or attempt["lease_token"] != lease_token
                    or int(attempt["generation"]) != int(generation)
                    or attempt["state"] not in {AttemptState.RUNNING, AttemptState.CANCEL_REQUESTED}
                    or lease_expired
                    or binding_mismatch
                    or binding_rejected
                )
            if fenced and rejection is None:
                if lease_expired:
                    rejection = "lease expired at authoritative ingestion boundary"
                elif binding_mismatch:
                    rejection = "cross-plane execution binding mismatch"
                elif binding_rejected:
                    rejection = (
                        str(binding_validation)
                        if isinstance(binding_validation, str)
                        else "cross-plane execution binding rejected"
                    )
                else:
                    rejection = "stale lease fencing token"
            authoritative = not fenced
            ingestion_state = "STALE_FENCED" if fenced else "INGESTED"
            connection.execute(
                """INSERT INTO attempt_results(
                       result_id, idempotency_key, attempt_id, logical_job_id, artifact_id,
                       artifact_sha256, provider_metadata, completion_status, created_at,
                       ingestion_state, authoritative, fencing_rejection, schema_version,
                       cross_plane_binding
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result_id,
                    key,
                    attempt_id,
                    attempt["logical_job_id"],
                    artifact_id,
                    artifact["sha256"],
                    canonical_json(dict(provider_metadata or {})),
                    completion_status,
                    utc_now(),
                    ingestion_state,
                    authoritative,
                    rejection,
                    RUNTIME_SCHEMA_VERSION,
                    serialized_binding,
                ),
            )
            self._journal(
                connection,
                object_type="ATTEMPT_RESULT",
                object_id=result_id,
                from_state=None,
                to_state=ingestion_state,
                transition_kind="INGEST_ATTEMPT_RESULT",
                actor=actor,
                attempt_id=attempt_id,
                logical_job_id=str(attempt["logical_job_id"]),
                metadata={"authoritative": authoritative, "fencing_rejection": rejection},
            )
            if authoritative:
                current = str(attempt["state"])
                if current not in {
                    AttemptState.LEASED,
                    AttemptState.RUNNING,
                    AttemptState.CANCEL_REQUESTED,
                    AttemptState.ORPHANED,
                }:
                    raise InvalidRuntimeTransition(
                        f"Cannot record authoritative result from attempt state {current}"
                    )
                connection.execute(
                    """UPDATE attempts SET state = ?, updated_at = ?, version = version + 1,
                           lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                       WHERE attempt_id = ? AND version = ?""",
                    (
                        AttemptState.RESULT_RECORDED,
                        utc_now(),
                        attempt_id,
                        attempt["version"],
                    ),
                )
                self._journal(
                    connection,
                    object_type="ATTEMPT",
                    object_id=attempt_id,
                    from_state=current,
                    to_state=AttemptState.RESULT_RECORDED,
                    transition_kind="RESULT_DURABLY_RECORDED",
                    actor=actor,
                    attempt_id=attempt_id,
                    logical_job_id=str(attempt["logical_job_id"]),
                    causal_ref=result_id,
                )
            return dict(
                connection.execute(
                    "SELECT * FROM attempt_results WHERE result_id = ?", (result_id,)
                ).fetchone()
            )

    def accept_result(
        self,
        logical_job_id: str,
        *,
        actor: str = "result-selector",
        binding_validator: Callable[[CrossPlaneExecutionBinding | None], bool | str] | None = None,
    ) -> dict[str, Any]:
        """Serialize FIRST_VALID_ACCEPTED_RESULT selection for one LogicalJob."""

        with self._transaction() as connection:
            job_row = connection.execute(
                "SELECT * FROM logical_jobs WHERE logical_job_id = ?", (logical_job_id,)
            ).fetchone()
            if job_row is None:
                raise RuntimeConflict(f"Unknown LogicalJob: {logical_job_id}")
            job = dict(job_row)
            if job["accepted_result_id"] is not None:
                return dict(
                    connection.execute(
                        "SELECT * FROM attempt_results WHERE result_id = ?",
                        (job["accepted_result_id"],),
                    ).fetchone()
                )
            candidates = connection.execute(
                """SELECT * FROM attempt_results
                   WHERE logical_job_id = ? AND authoritative = 1
                     AND completion_status IN ('SUCCESS', 'COMPLETED', 'PASS')
                   ORDER BY created_at, result_id""",
                (logical_job_id,),
            ).fetchall()
            winner = None
            for candidate in candidates:
                binding = self._binding_from_row(candidate)
                validation = binding_validator(binding) if binding_validator is not None else True
                valid = validation is True or validation is None
                if valid:
                    winner = candidate
                    break
                reason = (
                    str(validation)
                    if isinstance(validation, str)
                    else "cross-plane binding is stale at authoritative acceptance"
                )
                connection.execute(
                    "UPDATE attempt_results SET ingestion_state = 'STALE_FENCED', "
                    "authoritative = 0, fencing_rejection = ? WHERE result_id = ?",
                    (reason, candidate["result_id"]),
                )
                self._journal(
                    connection,
                    object_type="ATTEMPT_RESULT",
                    object_id=str(candidate["result_id"]),
                    from_state="INGESTED",
                    to_state="STALE_FENCED",
                    transition_kind="FENCE_STALE_CROSS_PLANE_BINDING",
                    actor=actor,
                    attempt_id=str(candidate["attempt_id"]),
                    logical_job_id=logical_job_id,
                    metadata={"reason": reason},
                )
            if winner is None:
                # Fencing is durable provenance even when no current result
                # remains eligible.  Commit the fencing journal before
                # surfacing the selector error; otherwise the surrounding
                # transaction would roll the safety transition back.
                connection.commit()
                raise RuntimeConflict("LogicalJob has no current-compatible successful result")
            cursor = connection.execute(
                """UPDATE logical_jobs SET accepted_result_id = ?, state = ?, updated_at = ?,
                       version = version + 1
                   WHERE logical_job_id = ? AND accepted_result_id IS NULL AND version = ?""",
                (
                    winner["result_id"],
                    JobState.RESULT_ACCEPTED,
                    utc_now(),
                    logical_job_id,
                    job["version"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeConflict("LogicalJob acceptance CAS lost")
            self._journal(
                connection,
                object_type="LOGICAL_JOB",
                object_id=logical_job_id,
                from_state=str(job["state"]),
                to_state=JobState.RESULT_ACCEPTED,
                transition_kind="ACCEPT_SINGLE_RESULT",
                actor=actor,
                attempt_id=str(winner["attempt_id"]),
                logical_job_id=logical_job_id,
                causal_ref=str(winner["result_id"]),
                metadata={"result_policy": job["result_policy"]},
            )
            completed_attempts = connection.execute(
                "SELECT * FROM attempts WHERE logical_job_id = ? AND state = ?",
                (logical_job_id, AttemptState.RESULT_RECORDED),
            ).fetchall()
            for attempt in completed_attempts:
                connection.execute(
                    "UPDATE attempts SET state = ?, updated_at = ?, version = version + 1 "
                    "WHERE attempt_id = ? AND version = ?",
                    (
                        AttemptState.COMPLETED,
                        utc_now(),
                        attempt["attempt_id"],
                        attempt["version"],
                    ),
                )
                self._journal(
                    connection,
                    object_type="ATTEMPT",
                    object_id=str(attempt["attempt_id"]),
                    from_state=AttemptState.RESULT_RECORDED,
                    to_state=AttemptState.COMPLETED,
                    transition_kind="WINNING_RESULT_ACCEPTED",
                    actor=actor,
                    attempt_id=str(attempt["attempt_id"]),
                    logical_job_id=logical_job_id,
                    causal_ref=(
                        str(winner["result_id"])
                        if attempt["attempt_id"] == winner["attempt_id"]
                        else "LOSING_SUCCESS_RETAINED"
                    ),
                )
            return dict(winner)

    def record_reconciliation(
        self,
        action: str | ReconciliationAction,
        *,
        object_type: str,
        object_id: str,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO reconciliation_actions(
                       action, object_type, object_id, reason, details_json, created_at
                   ) VALUES(?, ?, ?, ?, ?, ?)""",
                (
                    str(action),
                    object_type,
                    object_id,
                    reason,
                    canonical_json(dict(details or {})),
                    utc_now(),
                ),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM reconciliation_actions WHERE reconciliation_id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
            )

    def prepare_effect(
        self,
        *,
        logical_job_id: str,
        effect_kind: str,
        semantic_target_type: str,
        semantic_target_id: str,
        source_result_id: str,
        claim_snapshot_hash: str | None = None,
        execution_binding: CrossPlaneExecutionBinding | Mapping[str, Any] | None = None,
        binding_validator: Callable[[CrossPlaneExecutionBinding | None], bool | str] | None = None,
        actor: str = "effect-controller",
    ) -> tuple[dict[str, Any], bool]:
        """Claim the unique semantic effect identity without applying domain logic."""

        slot_id = stable_id(
            "effect",
            logical_job_id,
            effect_kind,
            semantic_target_type,
            semantic_target_id,
        )
        with self._transaction() as connection:
            job = connection.execute(
                "SELECT * FROM logical_jobs WHERE logical_job_id = ?", (logical_job_id,)
            ).fetchone()
            if job is None:
                raise RuntimeConflict(f"Unknown LogicalJob: {logical_job_id}")
            if job["accepted_result_id"] != source_result_id:
                raise RuntimeConflict("Effect source is not the accepted LogicalJob result")
            result = connection.execute(
                "SELECT * FROM attempt_results WHERE result_id = ? AND authoritative = 1",
                (source_result_id,),
            ).fetchone()
            if result is None:
                raise RuntimeConflict("Effect source result is not authoritative")
            result_binding = self._binding_from_row(result) or self._binding_from_row(job)
            requested_binding = coerce_binding(execution_binding)
            if requested_binding is not None and not requested_binding.matches(result_binding):
                legacy_root_only = (
                    result_binding is not None
                    and result_binding.root_claim_snapshot_hash
                    == requested_binding.root_claim_snapshot_hash
                    and result_binding.research_map_id is None
                    and all(
                        getattr(result_binding, field) is None
                        for field in (
                            "research_obligation_id",
                            "directive_id",
                            "tactical_session_id",
                            "governance_object_type",
                            "governance_object_id",
                            "governance_source_hash",
                        )
                    )
                )
                if not legacy_root_only:
                    raise RuntimeConflict("Effect source has a different cross-plane binding")
            effective_binding = requested_binding or result_binding
            existing = connection.execute(
                """SELECT * FROM effect_slots WHERE logical_job_id = ? AND effect_kind = ?
                   AND semantic_target_type = ? AND semantic_target_id = ?""",
                (logical_job_id, effect_kind, semantic_target_type, semantic_target_id),
            ).fetchone()
            if existing is not None:
                if existing["source_result_id"] != source_result_id:
                    raise RuntimeConflict("Effect slot already belongs to another accepted result")
                existing_binding = self._binding_from_row(existing)
                if requested_binding is not None and not requested_binding.matches(
                    existing_binding
                ):
                    raise RuntimeConflict("Effect slot has a different cross-plane binding")
                # A replay may occur after the domain store committed but before
                # the runtime ACK.  The current ResearchMap/ClaimSnapshot can
                # therefore legitimately be newer than the binding captured by
                # this already-created slot.  New slots still require the
                # validator below; existing slots are recovered by identity.
                return dict(existing), False
            if binding_validator is not None:
                validation = binding_validator(effective_binding)
                if validation is not True and validation is not None:
                    reason = (
                        str(validation)
                        if isinstance(validation, str)
                        else "cross-plane binding is stale at semantic effect preparation"
                    )
                    raise RuntimeConflict(reason)
            connection.execute(
                """INSERT INTO effect_slots(
                       effect_slot_id, logical_job_id, effect_kind, semantic_target_type,
                       semantic_target_id, source_result_id, claim_snapshot_hash, status,
                       prepared_at, effect_metadata, schema_version, cross_plane_binding
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    slot_id,
                    logical_job_id,
                    effect_kind,
                    semantic_target_type,
                    semantic_target_id,
                    source_result_id,
                    claim_snapshot_hash
                    or (effective_binding.root_claim_snapshot_hash if effective_binding else None),
                    EffectState.PREPARED,
                    utc_now(),
                    "{}",
                    RUNTIME_SCHEMA_VERSION,
                    self._binding_json(effective_binding),
                ),
            )
            self._journal(
                connection,
                object_type="EFFECT_SLOT",
                object_id=slot_id,
                from_state=None,
                to_state=EffectState.PREPARED,
                transition_kind="PREPARE_UNIQUE_EFFECT",
                actor=actor,
                logical_job_id=logical_job_id,
                causal_ref=source_result_id,
                metadata={
                    "effect_kind": effect_kind,
                    "semantic_target_type": semantic_target_type,
                    "semantic_target_id": semantic_target_id,
                },
            )
            return (
                dict(
                    connection.execute(
                        "SELECT * FROM effect_slots WHERE effect_slot_id = ?", (slot_id,)
                    ).fetchone()
                ),
                True,
            )

    def mark_effect_domain_applied(
        self,
        effect_slot_id: str,
        *,
        effect_artifact_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        actor: str = "effect-controller",
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM effect_slots WHERE effect_slot_id = ?", (effect_slot_id,)
            ).fetchone()
            if row is None:
                raise RuntimeConflict(f"Unknown EffectSlot: {effect_slot_id}")
            slot = dict(row)
            if slot["status"] in {EffectState.DOMAIN_APPLIED, EffectState.ACKNOWLEDGED}:
                return slot
            if slot["status"] != EffectState.PREPARED:
                raise InvalidRuntimeTransition(
                    f"Effect {slot['status']} -> {EffectState.DOMAIN_APPLIED} is not legal"
                )
            now = utc_now()
            cursor = connection.execute(
                """UPDATE effect_slots SET status = ?, domain_applied_at = ?,
                       effect_artifact_ref = ?, effect_metadata = ?, version = version + 1
                   WHERE effect_slot_id = ? AND status = ? AND version = ?""",
                (
                    EffectState.DOMAIN_APPLIED,
                    now,
                    effect_artifact_ref,
                    canonical_json(dict(metadata or {})),
                    effect_slot_id,
                    EffectState.PREPARED,
                    slot["version"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeConflict("EffectSlot domain-applied CAS lost")
            self._journal(
                connection,
                object_type="EFFECT_SLOT",
                object_id=effect_slot_id,
                from_state=EffectState.PREPARED,
                to_state=EffectState.DOMAIN_APPLIED,
                transition_kind="DOMAIN_EFFECT_APPLIED",
                actor=actor,
                logical_job_id=str(slot["logical_job_id"]),
                causal_ref=str(slot["source_result_id"]),
                metadata=metadata,
            )
            return dict(
                connection.execute(
                    "SELECT * FROM effect_slots WHERE effect_slot_id = ?", (effect_slot_id,)
                ).fetchone()
            )

    def acknowledge_effect(
        self, effect_slot_id: str, *, actor: str = "effect-controller"
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM effect_slots WHERE effect_slot_id = ?", (effect_slot_id,)
            ).fetchone()
            if row is None:
                raise RuntimeConflict(f"Unknown EffectSlot: {effect_slot_id}")
            slot = dict(row)
            if slot["status"] == EffectState.ACKNOWLEDGED:
                return slot
            if slot["status"] != EffectState.DOMAIN_APPLIED:
                raise InvalidRuntimeTransition(
                    f"Effect {slot['status']} -> {EffectState.ACKNOWLEDGED} is not legal"
                )
            now = utc_now()
            cursor = connection.execute(
                """UPDATE effect_slots SET status = ?, applied_at = ?, version = version + 1
                   WHERE effect_slot_id = ? AND status = ? AND version = ?""",
                (
                    EffectState.ACKNOWLEDGED,
                    now,
                    effect_slot_id,
                    EffectState.DOMAIN_APPLIED,
                    slot["version"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeConflict("EffectSlot acknowledgment CAS lost")
            connection.execute(
                """UPDATE logical_jobs SET state = ?, updated_at = ?, version = version + 1
                   WHERE logical_job_id = ? AND accepted_result_id = ?""",
                (
                    JobState.COMPLETED,
                    now,
                    slot["logical_job_id"],
                    slot["source_result_id"],
                ),
            )
            self._journal(
                connection,
                object_type="EFFECT_SLOT",
                object_id=effect_slot_id,
                from_state=EffectState.DOMAIN_APPLIED,
                to_state=EffectState.ACKNOWLEDGED,
                transition_kind="ACKNOWLEDGE_SEMANTIC_EFFECT",
                actor=actor,
                logical_job_id=str(slot["logical_job_id"]),
                causal_ref=str(slot["source_result_id"]),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM effect_slots WHERE effect_slot_id = ?", (effect_slot_id,)
                ).fetchone()
            )

    def apply_effect_once(
        self,
        *,
        logical_job_id: str,
        effect_kind: str,
        semantic_target_type: str,
        semantic_target_id: str,
        source_result_id: str,
        apply,
        recover=None,
        claim_snapshot_hash: str | None = None,
        execution_binding: CrossPlaneExecutionBinding | Mapping[str, Any] | None = None,
        binding_validator: Callable[[CrossPlaneExecutionBinding | None], bool | str] | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> tuple[dict[str, Any], Any]:
        """Run a recoverable cross-store saga with one durable semantic slot."""

        self.verify_artifact(
            str(
                next(
                    row["artifact_id"]
                    for row in self.list_rows("attempt_results")
                    if row["result_id"] == source_result_id
                )
            )
        )
        if fault_injector is not None:
            fault_injector.hit(FaultPoint.BEFORE_EFFECT_SLOT_COMMIT)
        slot, created = self.prepare_effect(
            logical_job_id=logical_job_id,
            effect_kind=effect_kind,
            semantic_target_type=semantic_target_type,
            semantic_target_id=semantic_target_id,
            source_result_id=source_result_id,
            claim_snapshot_hash=claim_snapshot_hash,
            execution_binding=execution_binding,
            binding_validator=binding_validator,
        )
        if slot["status"] == EffectState.ACKNOWLEDGED:
            return slot, recover(slot["effect_slot_id"]) if recover is not None else None
        if fault_injector is not None:
            fault_injector.hit(FaultPoint.AFTER_EFFECT_SLOT_BEFORE_DOMAIN_APPLY)
        recovered = recover(slot["effect_slot_id"]) if recover is not None else None
        if recovered is None:
            if not created and recover is None:
                raise RuntimeConflict("Partial domain effect requires deterministic recovery")
            if not created and binding_validator is not None:
                stored_binding = self._binding_from_row(slot)
                validation = binding_validator(stored_binding)
                if validation is not True and validation is not None:
                    reason = (
                        str(validation)
                        if isinstance(validation, str)
                        else "cross-plane binding is stale for an unrecovered effect slot"
                    )
                    raise RuntimeConflict(reason)
            outcome = apply(slot["effect_slot_id"])
        else:
            outcome = recovered
        artifact_ref = None
        if isinstance(outcome, Mapping):
            artifact_ref = outcome.get("effect_artifact_ref")
        if slot["status"] == EffectState.PREPARED:
            slot = self.mark_effect_domain_applied(
                slot["effect_slot_id"],
                effect_artifact_ref=(str(artifact_ref) if artifact_ref else None),
                metadata={
                    "outcome_type": type(outcome).__name__,
                    "recovered": recovered is not None,
                },
            )
        if fault_injector is not None:
            fault_injector.hit(FaultPoint.AFTER_DOMAIN_APPLY_BEFORE_ACK)
        slot = self.acknowledge_effect(slot["effect_slot_id"])
        return slot, outcome

    def reconcile(
        self,
        *,
        binding_validator: Callable[[CrossPlaneExecutionBinding | None], bool | str | None]
        | None = None,
    ) -> list[dict[str, Any]]:
        from .runtime_reconciler import RuntimeReconciler

        return RuntimeReconciler(self, binding_validator=binding_validator).run()

    def list_rows(self, table: str) -> list[dict[str, Any]]:
        allowed = {
            "logical_jobs",
            "attempts",
            "outbox",
            "transition_journal",
            "artifact_registry",
            "attempt_results",
            "effect_slots",
            "reconciliation_actions",
            "legacy_runtime_imports",
            "runtime_migration_history",
        }
        if table not in allowed:
            raise ValueError(f"Unsupported runtime table: {table}")
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]

    def schema_sql(self) -> str:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
            )
            return "\n\n".join(str(row[0]) for row in rows)

    def import_legacy_checkpoint(
        self,
        checkpoint_ref: str,
        *,
        classification: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record adoption without inventing attempts, leases, or journal history."""

        import_id = stable_id("legacy", str(Path(checkpoint_ref)))
        with self._transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO legacy_runtime_imports(
                       import_id, checkpoint_ref, classification, imported_at, metadata_json
                   ) VALUES(?, ?, ?, ?, ?)""",
                (
                    import_id,
                    str(checkpoint_ref),
                    classification,
                    utc_now(),
                    canonical_json(dict(metadata or {})),
                ),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM legacy_runtime_imports WHERE import_id = ?", (import_id,)
                ).fetchone()
            )


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return "sha256:" + digest.hexdigest(), size
