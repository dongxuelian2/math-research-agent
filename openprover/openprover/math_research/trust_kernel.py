"""Versioned dependency authority registries for the research harness."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .project import ProjectError, ProjectStore


REGISTRY_SCHEMA_VERSION = 1
FOUNDATION_PREFIX = "FOUND-"
SEMANTIC_PREFIX = "SEM-"

FOUNDATIONAL_THEOREM = "FOUNDATIONAL_THEOREM"
SEMANTIC_DEFINITION = "SEMANTIC_DEFINITION"
PROJECT_THEOREM = "PROJECT_THEOREM"
LOCAL_PROOF = "LOCAL_PROOF"
COMPUTATIONAL_CERTIFICATE = "COMPUTATIONAL_CERTIFICATE"

CLAIM_CLASSES = {
    FOUNDATIONAL_THEOREM,
    SEMANTIC_DEFINITION,
    PROJECT_THEOREM,
    LOCAL_PROOF,
    COMPUTATIONAL_CERTIFICATE,
}

_FOUNDATION_LEAK_PATTERNS = (
    re.compile(r"\bGA\d+(?:-\d+)?\b", re.IGNORECASE),
    re.compile(r"\bG_prim\b", re.IGNORECASE),
    re.compile(r"\bA1\b"),
    re.compile(r"critical_G", re.IGNORECASE),
    re.compile(r"historical solution", re.IGNORECASE),
)


class RegistryError(ProjectError):
    """A registry is malformed, untrusted, or used outside its scope."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def content_hash(value: dict, *, excluded: Iterable[str] = ()) -> str:
    excluded_keys = set(excluded)
    payload = {key: item for key, item in value.items() if key not in excluded_keys}
    return "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_registry_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"Unable to load registry {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistryError(f"Registry must be a JSON object: {path}")
    if value.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise RegistryError(
            f"Unsupported registry schema in {path}: {value.get('schema_version')}"
        )
    expected = value.get("registry_hash")
    actual = content_hash(value, excluded={"registry_hash"})
    if expected != actual:
        raise RegistryError(
            f"Registry hash mismatch for {path}: expected {expected}, computed {actual}"
        )
    return value


def _validate_item_hash(item: dict, *, registry_id: str) -> None:
    expected = item.get("content_hash")
    actual = content_hash(item, excluded={"content_hash"})
    if expected != actual:
        raise RegistryError(
            f"Item hash mismatch for {item.get('id', '<missing>')} in {registry_id}"
        )


@dataclass(frozen=True, slots=True)
class FoundationItem:
    id: str
    statement: str
    conditions: tuple[str, ...]
    provenance: dict
    proof_policy: str
    version: str
    content_hash: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "statement": self.statement,
            "conditions": list(self.conditions),
            "provenance": dict(self.provenance),
            "proof_policy": self.proof_policy,
            "version": self.version,
            "content_hash": self.content_hash,
        }


class FoundationRegistry:
    """Small, fixed, project-independent classical mathematics registry."""

    def __init__(self, *, registry_id: str, version: str, registry_hash: str,
                 items: dict[str, FoundationItem], source_path: Path):
        self.registry_id = registry_id
        self.version = version
        self.registry_hash = registry_hash
        self.items = items
        self.source_path = source_path

    @classmethod
    def builtin_path(cls) -> Path:
        return Path(__file__).with_name("registries") / "foundations.v1.json"

    @classmethod
    def load_builtin(cls) -> "FoundationRegistry":
        return cls.load(cls.builtin_path())

    @classmethod
    def load(cls, path: str | Path) -> "FoundationRegistry":
        path = Path(path).resolve()
        data = _load_registry_json(path)
        if data.get("registry_type") != "FOUNDATION":
            raise RegistryError(f"Not a foundation registry: {path}")
        registry_id = str(data.get("registry_id", ""))
        version = str(data.get("version", ""))
        if not registry_id or not version:
            raise RegistryError("Foundation registry requires registry_id and version")
        items: dict[str, FoundationItem] = {}
        for raw in data.get("items", []):
            cls._validate_raw_item(raw, registry_id=registry_id)
            _validate_item_hash(raw, registry_id=registry_id)
            item = FoundationItem(
                id=raw["id"],
                statement=raw["statement"],
                conditions=tuple(raw["conditions"]),
                provenance=dict(raw["provenance"]),
                proof_policy=raw["proof_policy"],
                version=raw["version"],
                content_hash=raw["content_hash"],
            )
            if item.id in items:
                raise RegistryError(f"Duplicate foundation ID: {item.id}")
            items[item.id] = item
        if not items:
            raise RegistryError("Foundation registry cannot be empty")
        return cls(
            registry_id=registry_id,
            version=version,
            registry_hash=data["registry_hash"],
            items=items,
            source_path=path,
        )

    @staticmethod
    def _validate_raw_item(raw: dict, *, registry_id: str) -> None:
        if not isinstance(raw, dict):
            raise RegistryError(f"Foundation item in {registry_id} must be an object")
        item_id = raw.get("id", "")
        if not isinstance(item_id, str) or not item_id.startswith(FOUNDATION_PREFIX):
            raise RegistryError(f"Invalid foundation ID: {item_id!r}")
        for key in ("statement", "proof_policy", "version", "content_hash"):
            if not isinstance(raw.get(key), str) or not raw[key].strip():
                raise RegistryError(f"Foundation {item_id} requires non-empty {key}")
        if not isinstance(raw.get("conditions"), list):
            raise RegistryError(f"Foundation {item_id} conditions must be an array")
        provenance = raw.get("provenance")
        if not isinstance(provenance, dict):
            raise RegistryError(f"Foundation {item_id} provenance must be an object")
        if provenance.get("source_type") not in {"classical", "registry_proof"}:
            raise RegistryError(
                f"Foundation {item_id} provenance must be classical or registry_proof"
            )
        if not provenance.get("canonical_name") or not provenance.get("review_policy"):
            raise RegistryError(
                f"Foundation {item_id} provenance requires canonical_name and review_policy"
            )
        leak_text = json.dumps(raw, ensure_ascii=False)
        for pattern in _FOUNDATION_LEAK_PATTERNS:
            if pattern.search(leak_text):
                raise RegistryError(
                    f"Foundation {item_id} contains project-specific/replay material"
                )

    def get(self, item_id: str) -> FoundationItem:
        try:
            return self.items[item_id]
        except KeyError as exc:
            raise RegistryError(f"Unknown foundation authority: {item_id}") from exc

    def context_items(self) -> list[dict]:
        return [self.items[item_id].to_dict() for item_id in sorted(self.items)]


@dataclass(frozen=True, slots=True)
class SemanticItem:
    id: str
    statement: str
    authority_kind: str
    notation_scope: str
    notation_version: str
    provenance: dict
    version: str
    content_hash: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "statement": self.statement,
            "authority_kind": self.authority_kind,
            "notation_scope": self.notation_scope,
            "notation_version": self.notation_version,
            "provenance": dict(self.provenance),
            "version": self.version,
            "content_hash": self.content_hash,
        }


class SemanticRegistry:
    """Project-scoped definitions with source hashes and notation versions."""

    def __init__(self, *, registry_id: str, version: str, registry_hash: str,
                 items: dict[str, SemanticItem], source_path: Path,
                 source_root: Path):
        self.registry_id = registry_id
        self.version = version
        self.registry_hash = registry_hash
        self.items = items
        self.source_path = source_path
        self.source_root = source_root

    @classmethod
    def load(cls, path: str | Path, *, source_root: str | Path) -> "SemanticRegistry":
        path = Path(path).resolve()
        source_root = Path(source_root).resolve()
        data = _load_registry_json(path)
        if data.get("registry_type") != "SEMANTIC":
            raise RegistryError(f"Not a semantic registry: {path}")
        registry_id = str(data.get("registry_id", ""))
        version = str(data.get("version", ""))
        if not registry_id or not version:
            raise RegistryError("Semantic registry requires registry_id and version")
        items: dict[str, SemanticItem] = {}
        for raw in data.get("items", []):
            cls._validate_raw_item(raw, registry_id=registry_id)
            _validate_item_hash(raw, registry_id=registry_id)
            provenance = raw["provenance"]
            source_file = provenance["source_file"]
            source_path = (source_root / source_file).resolve()
            try:
                source_path.relative_to(source_root)
            except ValueError as exc:
                raise RegistryError(
                    f"Semantic source escapes source root: {source_file}"
                ) from exc
            if not source_path.is_file():
                raise RegistryError(f"Semantic source is missing: {source_path}")
            actual_source_hash = file_sha256(source_path)
            if actual_source_hash.casefold() != provenance["source_hash"].casefold():
                raise RegistryError(
                    f"Semantic source hash mismatch for {raw['id']}: {source_file}"
                )
            item = SemanticItem(
                id=raw["id"],
                statement=raw["statement"],
                authority_kind=raw["authority_kind"],
                notation_scope=raw["notation_scope"],
                notation_version=raw["notation_version"],
                provenance=dict(provenance),
                version=raw["version"],
                content_hash=raw["content_hash"],
            )
            if item.id in items:
                raise RegistryError(f"Duplicate semantic ID: {item.id}")
            items[item.id] = item
        return cls(
            registry_id=registry_id,
            version=version,
            registry_hash=data["registry_hash"],
            items=items,
            source_path=path,
            source_root=source_root,
        )

    @staticmethod
    def _validate_raw_item(raw: dict, *, registry_id: str) -> None:
        if not isinstance(raw, dict):
            raise RegistryError(f"Semantic item in {registry_id} must be an object")
        item_id = raw.get("id", "")
        if not isinstance(item_id, str) or not item_id.startswith(SEMANTIC_PREFIX):
            raise RegistryError(f"Invalid semantic ID: {item_id!r}")
        for key in (
            "statement", "authority_kind", "notation_scope", "notation_version",
            "version", "content_hash",
        ):
            if not isinstance(raw.get(key), str) or not raw[key].strip():
                raise RegistryError(f"Semantic {item_id} requires non-empty {key}")
        if raw["authority_kind"] not in {"definition", "iff", "implication"}:
            raise RegistryError(
                f"Semantic {item_id} authority_kind must be definition, iff, or implication"
            )
        provenance = raw.get("provenance")
        if not isinstance(provenance, dict):
            raise RegistryError(f"Semantic {item_id} provenance must be an object")
        for key in ("source_file", "source_hash", "source_section"):
            if not isinstance(provenance.get(key), str) or not provenance[key].strip():
                raise RegistryError(f"Semantic {item_id} provenance requires {key}")
        if provenance.get("authority_source") in {"package_metadata", "index_summary"}:
            raise RegistryError(
                f"Semantic {item_id} cannot use package metadata or index summary as authority"
            )

    def get(self, item_id: str, *, notation_scope: str) -> SemanticItem:
        try:
            item = self.items[item_id]
        except KeyError as exc:
            raise RegistryError(f"Unknown semantic authority: {item_id}") from exc
        if item.notation_scope != notation_scope:
            raise RegistryError(
                f"Semantic authority {item_id} is scoped to {item.notation_scope!r}, "
                f"not {notation_scope!r}"
            )
        return item

    def context_items(self, *, notation_scope: str | None = None) -> list[dict]:
        values = []
        for item_id in sorted(self.items):
            item = self.items[item_id]
            if notation_scope is None or item.notation_scope == notation_scope:
                values.append(item.to_dict())
        return values


@dataclass(slots=True)
class DependencyReport:
    foundations: list[str] = field(default_factory=list)
    semantics: list[str] = field(default_factory=list)
    project_theorems: list[str] = field(default_factory=list)
    local_proofs: list[str] = field(default_factory=list)
    computational_certificates: list[str] = field(default_factory=list)
    missing_authorities: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def admissible(self) -> bool:
        return not self.missing_authorities and not self.errors

    @property
    def foundation_ids_used(self) -> list[str]:
        return sorted(set(self.foundations))

    def to_dict(self) -> dict:
        return {
            "foundations": sorted(set(self.foundations)),
            "semantics": sorted(set(self.semantics)),
            "project_theorems": sorted(set(self.project_theorems)),
            "local_proofs": list(self.local_proofs),
            "computational_certificates": list(self.computational_certificates),
            "foundation_ids_used": self.foundation_ids_used,
            "missing_authorities": list(self.missing_authorities),
            "errors": list(self.errors),
            "admissible": self.admissible,
        }


class DependencyAuthorityResolver:
    """Validate externally used claims against the three authority layers."""

    def __init__(self, *, foundations: FoundationRegistry,
                 semantics: SemanticRegistry | None,
                 project: ProjectStore | None, notation_scope: str = ""):
        self.foundations = foundations
        self.semantics = semantics
        self.project = project
        self.notation_scope = notation_scope

    def resolve(self, uses: Iterable[dict]) -> DependencyReport:
        report = DependencyReport()
        for index, use in enumerate(uses):
            if not isinstance(use, dict):
                report.errors.append(f"authority use {index} must be an object")
                continue
            claim = str(use.get("claim", "")).strip() or f"claim #{index + 1}"
            claim_class = str(use.get("claim_class", "")).strip().upper()
            authority_id = str(use.get("authority_id", "")).strip()
            authority_type = str(use.get("authority_type", "")).strip().casefold()
            if claim_class not in CLAIM_CLASSES:
                report.errors.append(f"{claim}: unknown claim class {claim_class!r}")
                continue
            if authority_type in {"metadata", "package_metadata", "index_summary", "filename"}:
                report.errors.append(
                    f"{claim}: package/index/filename metadata is not proof authority"
                )
                continue
            if "metadata" in authority_id.casefold():
                report.errors.append(f"{claim}: metadata cannot be used as authority")
                continue
            try:
                if claim_class == FOUNDATIONAL_THEOREM:
                    if not authority_id:
                        raise RegistryError("missing foundation authority ID")
                    self.foundations.get(authority_id)
                    report.foundations.append(authority_id)
                elif claim_class == SEMANTIC_DEFINITION:
                    if not authority_id:
                        raise RegistryError("missing semantic authority ID")
                    if self.semantics is None:
                        raise RegistryError("no semantic registry is configured")
                    self.semantics.get(authority_id, notation_scope=self.notation_scope)
                    report.semantics.append(authority_id)
                elif claim_class == PROJECT_THEOREM:
                    if not authority_id:
                        raise RegistryError("missing project theorem authority ID")
                    if self.project is None:
                        raise RegistryError("no project theorem store is configured")
                    theorem = self.project.load_theorem(authority_id)
                    if theorem["status"] != "PROVED":
                        raise RegistryError(
                            f"project theorem {authority_id} is {theorem['status']}, not PROVED"
                        )
                    report.project_theorems.append(authority_id)
                elif claim_class == LOCAL_PROOF:
                    location = str(use.get("proof_location", "")).strip()
                    if not location:
                        raise RegistryError("LOCAL_PROOF requires proof_location")
                    report.local_proofs.append(f"{claim} @ {location}")
                elif claim_class == COMPUTATIONAL_CERTIFICATE:
                    certificate = authority_id or str(use.get("certificate_id", "")).strip()
                    if not certificate:
                        raise RegistryError(
                            "COMPUTATIONAL_CERTIFICATE requires a certificate ID"
                        )
                    report.computational_certificates.append(certificate)
            except (ProjectError, RegistryError) as exc:
                if not authority_id and claim_class in {
                    FOUNDATIONAL_THEOREM, SEMANTIC_DEFINITION, PROJECT_THEOREM,
                }:
                    report.missing_authorities.append(claim)
                report.errors.append(f"{claim}: {exc}")
        return report


@dataclass(slots=True)
class TrustKernel:
    foundations: FoundationRegistry
    semantics: SemanticRegistry | None = None

    @classmethod
    def for_project(cls, project: ProjectStore) -> "TrustKernel":
        meta = project.load_project()
        foundation_path = meta.get("foundation_registry")
        foundations = (
            FoundationRegistry.load(project.safe_source_path(foundation_path))
            if foundation_path
            else FoundationRegistry.load_builtin()
        )
        semantic_path = meta.get("semantic_registry")
        default_semantic = project.root / "semantics" / "registry.json"
        if semantic_path:
            resolved = project.safe_source_path(semantic_path)
            semantics = SemanticRegistry.load(resolved, source_root=project.root)
        elif default_semantic.is_file():
            semantics = SemanticRegistry.load(default_semantic, source_root=project.root)
        else:
            semantics = None
        return cls(foundations=foundations, semantics=semantics)

    def context(self, *, notation_scope: str = "") -> dict:
        return {
            "schema_version": 1,
            "foundation_registry": {
                "id": self.foundations.registry_id,
                "version": self.foundations.version,
                "hash": self.foundations.registry_hash,
                "items": self.foundations.context_items(),
            },
            "semantic_registry": None if self.semantics is None else {
                "id": self.semantics.registry_id,
                "version": self.semantics.version,
                "hash": self.semantics.registry_hash,
                "notation_scope": notation_scope,
                "items": self.semantics.context_items(notation_scope=notation_scope),
            },
        }
