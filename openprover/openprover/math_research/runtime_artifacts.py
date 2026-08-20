"""Filesystem artifact plane and its explicit SQLite registration saga."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from .project import utc_now
from .runtime_backend import SQLiteRuntimeBackend, sha256_file
from .runtime_model import FaultInjector, FaultPoint, canonical_json, content_hash, stable_id


_SAFE_KIND = re.compile(r"[^a-zA-Z0-9_.-]+")


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    with temporary.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    try:
        descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


class RuntimeArtifactStore:
    """Durably finalize bodies first, then register them in the control plane."""

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / "runtime" / "artifacts"

    def write(
        self,
        body: bytes | str | Mapping[str, Any],
        *,
        artifact_kind: str,
        producer_attempt_id: str | None = None,
        result_metadata: Mapping[str, Any] | None = None,
        suffix: str = ".json",
        fault_injector: FaultInjector | None = None,
    ) -> dict[str, Any]:
        if isinstance(body, Mapping):
            payload = (json.dumps(body, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        else:
            payload = bytes(body)
        kind = _SAFE_KIND.sub("-", artifact_kind).strip("-.") or "artifact"
        artifact_id = stable_id(
            "artifact", artifact_kind, producer_attempt_id, content_hash(payload)
        )
        path = self.root / kind / f"{artifact_id}{suffix}"
        if path.exists():
            if path.read_bytes() != payload:
                raise RuntimeError(f"Runtime artifact identity collision: {path}")
        else:
            _atomic_bytes(path, payload)
        digest, size = sha256_file(path)
        manifest = {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "artifact_kind": artifact_kind,
            "relative_path": path.relative_to(self.project_root).as_posix(),
            "sha256": digest,
            "size": size,
            "producer_attempt_id": producer_attempt_id,
            "created_at": utc_now(),
            "result_metadata": dict(result_metadata or {}),
        }
        manifest_path = path.with_suffix(path.suffix + ".artifact.json")
        encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            comparable = dict(existing)
            comparable["created_at"] = manifest["created_at"]
            if canonical_json(comparable) != canonical_json(manifest):
                raise RuntimeError(f"Runtime artifact manifest collision: {manifest_path}")
            manifest = existing
        else:
            _atomic_bytes(manifest_path, encoded)
        if fault_injector is not None:
            fault_injector.hit(FaultPoint.AFTER_ARTIFACT_WRITE)
        return {**manifest, "manifest_path": str(manifest_path)}

    def persist_and_register(
        self,
        backend: SQLiteRuntimeBackend,
        body: bytes | str | Mapping[str, Any],
        *,
        artifact_kind: str,
        producer_attempt_id: str | None = None,
        result_metadata: Mapping[str, Any] | None = None,
        suffix: str = ".json",
        fault_injector: FaultInjector | None = None,
    ) -> dict[str, Any]:
        manifest = self.write(
            body,
            artifact_kind=artifact_kind,
            producer_attempt_id=producer_attempt_id,
            result_metadata=result_metadata,
            suffix=suffix,
            fault_injector=fault_injector,
        )
        return backend.register_artifact(
            manifest["relative_path"],
            artifact_kind=artifact_kind,
            producer_attempt_id=producer_attempt_id,
            expected_sha256=manifest["sha256"],
            artifact_id=manifest["artifact_id"],
        )

    def manifests(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        values: list[dict[str, Any]] = []
        for path in sorted(self.root.rglob("*.artifact.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                values.append(
                    {
                        "manifest_path": str(path),
                        "invalid": True,
                        "reason": "manifest is unreadable or invalid JSON",
                    }
                )
                continue
            value["manifest_path"] = str(path)
            values.append(value)
        return values
