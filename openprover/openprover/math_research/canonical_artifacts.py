"""Canonical project-artifact resolution with body-bound provenance.

This module deliberately resolves only local, explicitly authorized stores.
Manifests, summaries, extracts, and matching basenames outside those stores are
locators or diagnostics; they never become proof authority by themselves.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from .project import ProjectError, ProjectStore, utc_now


class CanonicalPurpose(str, Enum):
    PROOF_AUTHORITY = "proof_authority"
    REPLAY_AUTHORITY = "replay_authority"
    CONTEXTUAL_ONLY = "contextual_only"


class CanonicalResolutionStatus(str, Enum):
    RESOLVED_CANONICAL = "RESOLVED_CANONICAL"
    MISSING_CANONICAL = "MISSING_CANONICAL"
    HASH_MISMATCH = "HASH_MISMATCH"
    AMBIGUOUS_CANONICAL = "AMBIGUOUS_CANONICAL"
    NONCANONICAL_ONLY = "NONCANONICAL_ONLY"


AUTHORITY_BLOCK_REASONS = {
    CanonicalResolutionStatus.MISSING_CANONICAL.value: "BLOCKED_AUTHORITY_SOURCE_UNAVAILABLE",
    CanonicalResolutionStatus.HASH_MISMATCH.value: "BLOCKED_AUTHORITY_HASH_MISMATCH",
    CanonicalResolutionStatus.AMBIGUOUS_CANONICAL.value: "BLOCKED_AUTHORITY_AMBIGUOUS",
    CanonicalResolutionStatus.NONCANONICAL_ONLY.value: "BLOCKED_AUTHORITY_NONCANONICAL_ONLY",
}


def _normalize_hash(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip().casefold()
    if raw.startswith("sha256:"):
        raw = raw[7:]
    if len(raw) != 64 or any(char not in "0123456789abcdef" for char in raw):
        raise ProjectError("expected_sha256 must be a 64-character SHA-256 digest")
    return f"sha256:{raw}"


@dataclass(frozen=True, slots=True)
class CanonicalSourceRequirement:
    """An upstream, explicit declaration that a canonical body is required."""

    logical_name: str
    canonical_filename: str
    purpose: str
    requesting_obligation_id: str
    canonical_path: str | None = None
    expected_sha256: str | None = None
    authority_source: str = ""
    registry_record: dict[str, Any] = field(default_factory=dict)
    requesting_task_id: str | None = None
    contextual_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.logical_name.strip():
            raise ProjectError("CanonicalSourceRequirement.logical_name is required")
        filename = Path(self.canonical_filename).name
        if not filename or filename != self.canonical_filename:
            raise ProjectError("canonical_filename must be a filename, not a path")
        if self.purpose not in {item.value for item in CanonicalPurpose}:
            raise ProjectError(f"Unsupported canonical authority purpose: {self.purpose}")
        if not self.requesting_obligation_id.strip():
            raise ProjectError("requesting_obligation_id is required")
        object.__setattr__(self, "expected_sha256", _normalize_hash(self.expected_sha256))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CanonicalSourceRequirement":
        if not isinstance(value, dict):
            raise ProjectError("canonical source requirement must be an object")
        authority = value.get("authority_source", "")
        registry = value.get("registry_record") or value.get("authority_record") or {}
        if isinstance(authority, dict):
            registry = dict(authority)
            authority = str(registry.get("registry_id") or registry.get("source") or "registry")
        if not isinstance(registry, dict):
            raise ProjectError("registry_record must be an object")
        return cls(
            logical_name=str(value.get("logical_name") or ""),
            canonical_filename=str(value.get("canonical_filename") or ""),
            canonical_path=(
                str(value["canonical_path"]) if value.get("canonical_path") is not None else None
            ),
            expected_sha256=value.get("expected_sha256"),
            authority_source=str(authority or ""),
            registry_record=dict(registry),
            purpose=str(value.get("purpose") or ""),
            requesting_obligation_id=str(value.get("requesting_obligation_id") or ""),
            requesting_task_id=(
                str(value["requesting_task_id"])
                if value.get("requesting_task_id") is not None
                else None
            ),
            contextual_paths=tuple(str(item) for item in value.get("contextual_paths", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["contextual_paths"] = list(self.contextual_paths)
        return value

    @property
    def requires_authority_body(self) -> bool:
        return self.purpose in {
            CanonicalPurpose.PROOF_AUTHORITY.value,
            CanonicalPurpose.REPLAY_AUTHORITY.value,
        }


@dataclass(slots=True)
class CanonicalResolution:
    requirement: dict[str, Any]
    resolution_status: str
    body: str | None = None
    resolved_source_location: str | None = None
    computed_sha256: str | None = None
    expected_sha256: str | None = None
    checkpoint_sha256: str | None = None
    authority_source: str = ""
    authority_record: dict[str, Any] = field(default_factory=dict)
    authority_level: str = ""
    requesting_obligation_id: str = ""
    requesting_task_id: str | None = None
    immutable_body_location: str | None = None
    candidate_locations: list[str] = field(default_factory=list)
    diagnostic_locations: list[str] = field(default_factory=list)
    resolved_at: str = field(default_factory=utc_now)
    body_revalidated: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def promotion_eligible(self) -> bool:
        purpose = self.requirement.get("purpose")
        return purpose == CanonicalPurpose.CONTEXTUAL_ONLY.value or (
            self.resolution_status == CanonicalResolutionStatus.RESOLVED_CANONICAL.value
            and bool(self.body is not None and self.computed_sha256)
        )

    @property
    def blocker_reason(self) -> str | None:
        if self.requirement.get("purpose") not in {
            CanonicalPurpose.PROOF_AUTHORITY.value,
            CanonicalPurpose.REPLAY_AUTHORITY.value,
        }:
            return None
        return AUTHORITY_BLOCK_REASONS.get(self.resolution_status)


class CanonicalArtifactResolver:
    """Resolve actual bytes from legitimate local stores and bind provenance."""

    _REGISTRY_PATH_KEYS = ("canonical_path", "source_file", "artifact_path", "path")

    def __init__(
        self,
        project: ProjectStore,
        *,
        configured_roots: Iterable[str | Path] = (),
        run_dir: str | Path | None = None,
        immutable_cache: str | Path | None = None,
    ):
        self.project = project
        self.project_root = project.root.resolve()
        self.run_dir = Path(run_dir).resolve() if run_dir else None
        self.search_roots: list[Path] = []
        configured = []
        for item in configured_roots:
            root = Path(item)
            configured.append(
                root.resolve() if root.is_absolute() else (self.project_root / root).resolve()
            )
        for root in (
            self.project_root / "sources",
            self.project_root / "inbox",
            *configured,
        ):
            self._add_root(root)
        if self.run_dir is not None:
            for name in ("inherited_sources", "attachments", "freeze", "source_bundle"):
                self._add_root(self.run_dir / name)
        self.immutable_cache = (
            Path(immutable_cache).resolve()
            if immutable_cache
            else (self.run_dir / "canonical_authority" / "objects" if self.run_dir else None)
        )

    def _add_root(self, root: Path) -> None:
        root = root.resolve()
        if root not in self.search_roots:
            self.search_roots.append(root)

    @staticmethod
    def _digest(raw: bytes) -> str:
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _inside(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            return False
        return True

    def _declared_paths(self, requirement: CanonicalSourceRequirement) -> list[str]:
        paths = []
        if requirement.canonical_path:
            paths.append(requirement.canonical_path)
        for key in self._REGISTRY_PATH_KEYS:
            value = requirement.registry_record.get(key)
            if value:
                paths.append(str(value))
        return list(dict.fromkeys(paths))

    def _authoritative_candidates(
        self,
        requirement: CanonicalSourceRequirement,
        *,
        checkpoint_sha256: str | None = None,
    ) -> list[Path]:
        candidates: list[Path] = []
        declared = self._declared_paths(requirement)
        roots = [self.project_root, *self.search_roots]
        for raw_path in declared:
            candidate = Path(raw_path)
            if candidate.is_absolute():
                resolved = candidate.resolve()
                if any(self._inside(resolved, root) for root in roots) and resolved.is_file():
                    candidates.append(resolved)
                continue
            for root in roots:
                resolved = (root / candidate).resolve()
                if self._inside(resolved, root) and resolved.is_file():
                    candidates.append(resolved)
        if not declared:
            for root in self.search_roots:
                if root.is_dir():
                    candidates.extend(
                        path.resolve() for path in root.rglob(requirement.canonical_filename)
                    )
        pinned_hash = requirement.expected_sha256 or checkpoint_sha256
        if pinned_hash and self.immutable_cache:
            object_path = self.immutable_cache / pinned_hash.removeprefix("sha256:")
            if object_path.is_file():
                candidates.append(object_path.resolve())
        return sorted(set(candidates), key=lambda item: str(item).casefold())

    def _diagnostic_candidates(self, requirement: CanonicalSourceRequirement) -> list[Path]:
        result = []
        for raw_path in requirement.contextual_paths:
            path = Path(raw_path)
            candidate = (
                path.resolve() if path.is_absolute() else (self.project_root / path).resolve()
            )
            if self._inside(candidate, self.project_root) and candidate.is_file():
                result.append(candidate)
        return sorted(set(result), key=lambda item: str(item).casefold())

    def _base_resolution(
        self,
        requirement: CanonicalSourceRequirement,
        status: CanonicalResolutionStatus,
        *,
        candidates: list[Path],
        diagnostics: list[Path],
        checkpoint_sha256: str | None = None,
    ) -> CanonicalResolution:
        return CanonicalResolution(
            requirement=requirement.to_dict(),
            resolution_status=status.value,
            expected_sha256=requirement.expected_sha256,
            checkpoint_sha256=checkpoint_sha256,
            authority_source=requirement.authority_source,
            authority_record=dict(requirement.registry_record),
            authority_level=requirement.purpose,
            requesting_obligation_id=requirement.requesting_obligation_id,
            requesting_task_id=requirement.requesting_task_id,
            candidate_locations=[str(item) for item in candidates],
            diagnostic_locations=[str(item) for item in diagnostics],
        )

    def resolve(
        self,
        requirement: CanonicalSourceRequirement,
        *,
        checkpoint_sha256: str | None = None,
    ) -> CanonicalResolution:
        checkpoint_sha256 = _normalize_hash(checkpoint_sha256)
        candidates = self._authoritative_candidates(
            requirement, checkpoint_sha256=checkpoint_sha256
        )
        diagnostics = self._diagnostic_candidates(requirement)
        if not candidates:
            status = (
                CanonicalResolutionStatus.NONCANONICAL_ONLY
                if diagnostics
                else CanonicalResolutionStatus.MISSING_CANONICAL
            )
            return self._base_resolution(
                requirement,
                status,
                candidates=candidates,
                diagnostics=diagnostics,
                checkpoint_sha256=checkpoint_sha256,
            )

        bodies = [(path, path.read_bytes()) for path in candidates]
        digests = [(path, raw, self._digest(raw)) for path, raw in bodies]
        binding_hash = requirement.expected_sha256 or checkpoint_sha256
        if binding_hash:
            matching = [item for item in digests if item[2] == binding_hash]
            if not matching:
                resolution = self._base_resolution(
                    requirement,
                    CanonicalResolutionStatus.HASH_MISMATCH,
                    candidates=candidates,
                    diagnostics=diagnostics,
                    checkpoint_sha256=checkpoint_sha256,
                )
                resolution.computed_sha256 = digests[0][2] if len(digests) == 1 else None
                return resolution
            chosen_path, raw, digest = matching[0]
        else:
            distinct = {item[2] for item in digests}
            if len(distinct) > 1:
                return self._base_resolution(
                    requirement,
                    CanonicalResolutionStatus.AMBIGUOUS_CANONICAL,
                    candidates=candidates,
                    diagnostics=diagnostics,
                    checkpoint_sha256=checkpoint_sha256,
                )
            chosen_path, raw, digest = digests[0]

        immutable_location = None
        if self.immutable_cache is not None:
            self.immutable_cache.mkdir(parents=True, exist_ok=True)
            object_path = self.immutable_cache / digest.removeprefix("sha256:")
            if not object_path.exists():
                temp = object_path.with_suffix(".tmp")
                temp.write_bytes(raw)
                temp.replace(object_path)
            elif self._digest(object_path.read_bytes()) != digest:
                raise ProjectError("Immutable canonical authority cache is corrupt")
            immutable_location = str(object_path)

        resolution = self._base_resolution(
            requirement,
            CanonicalResolutionStatus.RESOLVED_CANONICAL,
            candidates=candidates,
            diagnostics=diagnostics,
            checkpoint_sha256=checkpoint_sha256,
        )
        resolution.body = raw.decode("utf-8-sig", errors="replace")
        resolution.resolved_source_location = str(chosen_path)
        resolution.computed_sha256 = digest
        resolution.immutable_body_location = immutable_location
        return resolution

    def resolve_all(
        self,
        requirements: Iterable[CanonicalSourceRequirement],
        *,
        previous_resolutions: Iterable[dict[str, Any]] = (),
    ) -> list[CanonicalResolution]:
        previous = {
            (
                str((item.get("requirement") or {}).get("logical_name") or ""),
                str(item.get("requesting_obligation_id") or ""),
            ): item
            for item in previous_resolutions
            if item.get("resolution_status") == CanonicalResolutionStatus.RESOLVED_CANONICAL.value
            and item.get("computed_sha256")
        }
        return [
            self.resolve(
                requirement,
                checkpoint_sha256=(
                    previous.get(
                        (requirement.logical_name, requirement.requesting_obligation_id), {}
                    ).get("computed_sha256")
                ),
            )
            for requirement in requirements
        ]


def authority_promotion_decision(resolutions: Iterable[dict[str, Any]]) -> tuple[bool, list[dict]]:
    """Fail closed for declared proof/replay authority without changing math audits."""

    blockers = []
    for raw in resolutions:
        requirement = raw.get("requirement") or {}
        if requirement.get("purpose") not in {
            CanonicalPurpose.PROOF_AUTHORITY.value,
            CanonicalPurpose.REPLAY_AUTHORITY.value,
        }:
            continue
        status = str(raw.get("resolution_status") or "")
        body_bound = raw.get("body") is not None and bool(raw.get("computed_sha256"))
        if status != CanonicalResolutionStatus.RESOLVED_CANONICAL.value or not body_bound:
            blockers.append(
                {
                    "type": AUTHORITY_BLOCK_REASONS.get(
                        status, "BLOCKED_AUTHORITY_SOURCE_UNAVAILABLE"
                    ),
                    "logical_name": requirement.get("logical_name"),
                    "resolution_status": status
                    or CanonicalResolutionStatus.MISSING_CANONICAL.value,
                    "requesting_obligation_id": requirement.get("requesting_obligation_id"),
                }
            )
    return not blockers, blockers


def canonical_context_markdown(resolutions: Iterable[dict[str, Any]]) -> str:
    """Render bodies for models; the JSON provenance remains the actual boundary."""

    sections = []
    for raw in resolutions:
        requirement = raw.get("requirement") or {}
        provenance = {
            key: raw.get(key)
            for key in (
                "resolution_status",
                "resolved_source_location",
                "computed_sha256",
                "expected_sha256",
                "checkpoint_sha256",
                "authority_source",
                "authority_level",
                "requesting_obligation_id",
                "requesting_task_id",
            )
        }
        provenance["canonical_filename"] = requirement.get("canonical_filename")
        heading = (
            "CANONICAL AUTHORITY SOURCE"
            if raw.get("resolution_status") == CanonicalResolutionStatus.RESOLVED_CANONICAL.value
            and requirement.get("purpose") != CanonicalPurpose.CONTEXTUAL_ONLY.value
            else "CONTEXTUAL / NON-AUTHORITATIVE EXTRACT"
            if requirement.get("purpose") == CanonicalPurpose.CONTEXTUAL_ONLY.value
            else "UNAVAILABLE REQUIRED SOURCE"
        )
        body = raw.get("body") if heading != "UNAVAILABLE REQUIRED SOURCE" else "(body unavailable)"
        sections.append(
            f"### {heading}: `{requirement.get('logical_name', '')}`\n\n"
            f"```json\n{json.dumps(provenance, ensure_ascii=False, indent=2)}\n```\n\n{body or ''}"
        )
    return "\n\n".join(sections) or "- (none declared)"
