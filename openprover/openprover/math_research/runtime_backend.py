"""SQLite/WAL implementation of the durable execution control plane."""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Mapping

from .project import utc_now
from .runtime_model import (
    RUNTIME_SCHEMA_VERSION,
    ArtifactIntegrityError,
    JobState,
    canonical_json,
    content_hash,
    stable_id,
)


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
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_schema'"
            ).fetchone()
            if existing is None:
                connection.execute("BEGIN IMMEDIATE")
                connection.executescript(_MIGRATION_1)
                connection.execute(
                    "INSERT INTO runtime_schema(singleton, schema_version, migrated_at) "
                    "VALUES(1, ?, ?)",
                    (RUNTIME_SCHEMA_VERSION, utc_now()),
                )
                connection.commit()
            version = int(
                connection.execute(
                    "SELECT schema_version FROM runtime_schema WHERE singleton = 1"
                ).fetchone()[0]
            )
            if version > RUNTIME_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Runtime database schema {version} is newer than supported "
                    f"{RUNTIME_SCHEMA_VERSION}"
                )
            if version < RUNTIME_SCHEMA_VERSION:
                raise RuntimeError(f"No forward migration registered from schema {version}")
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
                "journal_mode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]).upper(),
                "foreign_keys": bool(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "synchronous": int(connection.execute("PRAGMA synchronous").fetchone()[0]),
                "integrity_check": integrity,
                "control_plane_only": True,
                "filesystem_artifact_plane": True,
            }

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

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

    def journal(self, *, object_type: str | None = None, object_id: str | None = None) -> list[dict]:
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
        result_policy: str = "FIRST_VALID_ACCEPTED_RESULT",
        actor: str = "runtime",
    ) -> dict[str, Any]:
        job_id = logical_job_id or stable_id("job", idempotency_key)
        now = utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM logical_jobs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return dict(existing)
            connection.execute(
                """INSERT INTO logical_jobs(
                       logical_job_id, idempotency_key, job_kind, semantic_target, directive_id,
                       obligation_id, claim_snapshot_hash, research_map_version, governance_ref,
                       payload_artifact_ref, created_at, updated_at, state, result_policy,
                       schema_version
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
