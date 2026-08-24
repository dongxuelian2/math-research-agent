"""Transparent JSON-backed mathematics research project storage."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state_machine import AuditGate, THEOREM_STATUSES, validate_transition


SCHEMA_VERSION = 1
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PREMISE_NODE_TYPES = {"PROJECT_PREMISE", "ROOT_PROBLEM", "ASSUMPTION"}
_PROJECT_TRUTH_LOCK = threading.RLock()


class ProjectError(ValueError):
    """Invalid project data or unsafe project operation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise ProjectError(f"Missing required file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Invalid JSON in {path}: {exc}") from exc


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


class ProjectStore:
    """Read and update a math project without a database."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.project_file = self.root / "project.json"
        if not self.project_file.exists():
            raise ProjectError(f"Not a math research project: {self.root} (project.json missing)")

    @classmethod
    def initialize(
        cls,
        root: str | Path,
        name: str,
        *,
        project_id: str | None = None,
        purpose: str | None = None,
        demo: bool = False,
    ) -> "ProjectStore":
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        project_file = root / "project.json"
        if project_file.exists():
            raise ProjectError(f"Project already exists: {root}")
        project_id = project_id or cls.make_id(name)
        cls.validate_id(project_id)
        for rel in (
            "theorems",
            "campaigns",
            "reports",
            "runs",
            "sources",
            "inbox",
            "work",
            "evidence",
            "certificates",
            "steering",
            "logs",
            "premises",
            "semantics",
        ):
            (root / rel).mkdir(parents=True, exist_ok=True)
        now = utc_now()
        _write_json(
            project_file,
            {
                "schema_version": SCHEMA_VERSION,
                "id": project_id,
                "name": name,
                "display_title": name,
                "description": purpose or "",
                "purpose": purpose or name,
                "current_target": None,
                "demo": bool(demo),
                "created_at": now,
                "last_updated": now,
                "frozen_branches": [],
                "prohibited_routes": [],
                "allowed_scope": [],
                "branches": {},
            },
        )
        _write_json(
            root / "index.json",
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": now,
                "theorems": [],
            },
        )
        _write_json(
            root / "failed_routes.json",
            {
                "schema_version": SCHEMA_VERSION,
                "routes": [],
            },
        )
        _write_json(
            root / "premise_index.json",
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": now,
                "premises": [],
            },
        )
        _write_json(
            root / "steering" / "directives.json",
            {
                "freeze_branches": [],
                "prohibit_routes": [],
                "allowed_scope": [],
                "added_lemmas": [],
                "stop_workers": [],
                "reaudit_requested": False,
                "last_updated": now,
            },
        )
        _write_json(
            root / "inbox" / "manifest.json",
            {
                "schema_version": 1,
                "files": [],
            },
        )
        return cls(root)

    @staticmethod
    def make_id(text: str) -> str:
        value = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-.")
        return (value or "item")[:128]

    @staticmethod
    def validate_id(value: str) -> None:
        if not ID_RE.fullmatch(value):
            raise ProjectError(
                f"Invalid id {value!r}; use letters, digits, dot, underscore, or hyphen"
            )

    def load_project(self) -> dict:
        data = _read_json(self.project_file)
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ProjectError(f"Unsupported project schema: {data.get('schema_version')}")
        return data

    def save_project(self, data: dict) -> None:
        with _PROJECT_TRUTH_LOCK:
            data["last_updated"] = utc_now()
            _write_json(self.project_file, data)

    def theorem_path(self, theorem_id: str) -> Path:
        self.validate_id(theorem_id)
        return self.root / "theorems" / f"{theorem_id}.json"

    def premise_path(self, premise_id: str) -> Path:
        self.validate_id(premise_id)
        return self.root / "premises" / f"{premise_id}.json"

    def load_theorem(self, theorem_id: str) -> dict:
        theorem = _read_json(self.theorem_path(theorem_id))
        status = theorem.get("status")
        if status not in THEOREM_STATUSES:
            raise ProjectError(f"Theorem {theorem_id} has invalid status: {status}")
        return theorem

    def list_theorems(self) -> list[dict]:
        items = []
        for path in sorted((self.root / "theorems").glob("*.json")):
            items.append(_read_json(path))
        return items

    def load_premise(self, premise_id: str) -> dict:
        premise = _read_json(self.premise_path(premise_id))
        if premise.get("node_type") not in PREMISE_NODE_TYPES:
            raise ProjectError(
                f"Premise {premise_id} has invalid node_type: {premise.get('node_type')}"
            )
        if premise.get("status") is not None:
            raise ProjectError(f"Premise {premise_id} must not have theorem lifecycle status")
        if premise.get("active") is not True:
            raise ProjectError(f"Premise {premise_id} is not active")
        provenance = premise.get("provenance")
        if not isinstance(provenance, list) or not provenance:
            raise ProjectError(f"Premise {premise_id} has no provenance")
        for item in provenance:
            source = item.get("source") if isinstance(item, dict) else None
            if not source:
                raise ProjectError(f"Premise {premise_id} has invalid provenance")
            source_path = self.safe_source_path(source)
            if source_path is None or not source_path.is_file():
                raise ProjectError(f"Premise {premise_id} provenance source is missing: {source}")
        return premise

    def list_premises(self) -> list[dict]:
        items = []
        premise_dir = self.root / "premises"
        if not premise_dir.exists():
            return items
        for path in sorted(premise_dir.glob("*.json")):
            items.append(self.load_premise(path.stem))
        return items

    def add_premise(
        self,
        premise_id: str,
        title: str,
        statement: str,
        *,
        node_type: str = "PROJECT_PREMISE",
        active: bool = True,
        source_file: str,
        provenance: list[dict],
    ) -> dict:
        self.validate_id(premise_id)
        if node_type not in PREMISE_NODE_TYPES:
            raise ProjectError(f"Unknown premise node_type: {node_type}")
        if not active:
            raise ProjectError("A newly registered premise must be active")
        if self.premise_path(premise_id).exists() or self.theorem_path(premise_id).exists():
            raise ProjectError(f"Dependency node already exists: {premise_id}")
        if not title.strip() or not statement.strip():
            raise ProjectError("Premise title and statement are required")
        if not source_file:
            raise ProjectError("Premise source_file is required")
        if not provenance:
            raise ProjectError("Premise provenance is required")
        now = utc_now()
        premise = {
            "schema_version": SCHEMA_VERSION,
            "id": premise_id,
            "node_type": node_type,
            "title": title.strip(),
            "statement": statement.strip(),
            "active": True,
            "source_file": source_file,
            "provenance": provenance,
            "lifecycle_managed": False,
            "counts_as_proved_theorem": False,
            "created_at": now,
            "last_updated": now,
        }
        path = self.premise_path(premise_id)
        _write_json(path, premise)
        try:
            premise = self.load_premise(premise_id)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self.rebuild_premise_index()
        return premise

    def rebuild_premise_index(self) -> dict:
        premises = self.list_premises()
        index = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": utc_now(),
            "premises": [
                {
                    "id": item["id"],
                    "node_type": item["node_type"],
                    "title": item["title"],
                    "active": item["active"],
                    "source_file": item["source_file"],
                    "last_updated": item["last_updated"],
                }
                for item in premises
            ],
        }
        _write_json(self.root / "premise_index.json", index)
        return index

    def resolve_dependency(self, dependency_id: str) -> dict:
        self.validate_id(dependency_id)
        theorem_exists = self.theorem_path(dependency_id).exists()
        premise_exists = self.premise_path(dependency_id).exists()
        if theorem_exists and premise_exists:
            raise ProjectError(f"Ambiguous dependency node: {dependency_id}")
        if theorem_exists:
            return {"kind": "THEOREM", "record": self.load_theorem(dependency_id)}
        if premise_exists:
            return {"kind": "PREMISE", "record": self.load_premise(dependency_id)}
        raise ProjectError(f"Unknown dependency: {dependency_id}")

    def validate_proved_dependency(
        self, dependency_id: str, *, approved_theorem_ids: set[str] | None = None
    ) -> dict:
        resolved = self.resolve_dependency(dependency_id)
        if resolved["kind"] == "PREMISE":
            return resolved
        theorem = resolved["record"]
        approved = approved_theorem_ids or set()
        if theorem["status"] != "PROVED" and dependency_id not in approved:
            raise ProjectError(
                f"Theorem dependency is not PROVED: {dependency_id} [{theorem['status']}]"
            )
        return resolved

    def add_theorem(
        self,
        theorem_id: str,
        title: str,
        statement: str,
        *,
        status: str = "OPEN",
        source_file: str = "",
        dependencies: list[str] | None = None,
        tags: list[str] | None = None,
        branch: str = "main",
        proof_type: str = "NATURAL_LANGUAGE",
        claim_type: str = "implication",
        notation_scope: str = "",
    ) -> dict:
        self.validate_id(theorem_id)
        if status not in THEOREM_STATUSES:
            raise ProjectError(f"Unknown theorem status: {status}")
        path = self.theorem_path(theorem_id)
        if path.exists() or self.premise_path(theorem_id).exists():
            raise ProjectError(f"Theorem already exists: {theorem_id}")
        dependencies = list(dict.fromkeys(dependencies or []))
        for dependency in dependencies:
            try:
                self.resolve_dependency(dependency)
            except ProjectError as exc:
                raise ProjectError(f"Unknown dependency for {theorem_id}: {dependency}") from exc
        now = utc_now()
        theorem = {
            "schema_version": SCHEMA_VERSION,
            "id": theorem_id,
            "title": title.strip(),
            "status": status,
            "source_file": source_file,
            "statement": statement.strip(),
            "dependencies": dependencies,
            "downstream_dependents": [],
            "tags": sorted(set(tags or [])),
            "branch": branch,
            "proof_type": proof_type,
            "claim_type": claim_type,
            "notation_scope": notation_scope,
            "audit_status": "NOT_AUDITED",
            "created_at": now,
            "last_updated": now,
            "status_history": [
                {
                    "from": None,
                    "to": status,
                    "actor": "Human" if status != "UNCLASSIFIED" else "Importer",
                    "reason": "Theorem record created",
                    "at": now,
                }
            ],
        }
        _write_json(path, theorem)
        self.rebuild_index()
        return theorem

    def update_theorem(self, theorem: dict) -> None:
        with _PROJECT_TRUTH_LOCK:
            theorem_id = theorem.get("id", "")
            self.validate_id(theorem_id)
            theorem["last_updated"] = utc_now()
            _write_json(self.theorem_path(theorem_id), theorem)
            self.rebuild_index(update_theorem_files=False)

    def transition(
        self,
        theorem_id: str,
        new_status: str,
        *,
        actor: str,
        reason: str,
        gate: AuditGate | None = None,
        audit_status: str | None = None,
    ) -> dict:
        with _PROJECT_TRUTH_LOCK:
            return self._transition_locked(
                theorem_id,
                new_status,
                actor=actor,
                reason=reason,
                gate=gate,
                audit_status=audit_status,
            )

    @contextmanager
    def truth_transaction(self):
        """Serialize in-process truth comparison and its filesystem transition."""

        with _PROJECT_TRUTH_LOCK:
            yield

    def compare_and_transition(
        self,
        theorem_id: str,
        new_status: str,
        *,
        expected_status: str,
        expected_identity: dict[str, str],
        actor: str,
        reason: str,
        gate: AuditGate | None = None,
        audit_status: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> tuple[dict, dict]:
        """Apply one serialized status CAS after exact root-identity comparison."""

        with _PROJECT_TRUTH_LOCK:
            before = self.load_theorem(theorem_id)
            if before["status"] != expected_status:
                raise ProjectError(
                    f"Truth compare failed: expected status {expected_status}, "
                    f"found {before['status']}"
                )
            mismatches = [
                key
                for key, expected in expected_identity.items()
                if before.get(key, "") != expected
            ]
            if mismatches:
                raise ProjectError(
                    "Truth compare failed for identity fields: " + ", ".join(sorted(mismatches))
                )
            forbidden = {"id", "statement", "claim_type", "notation_scope"}.intersection(
                metadata_updates or {}
            )
            if forbidden:
                raise ProjectError(
                    "compare_and_transition metadata cannot change truth identity: "
                    + ", ".join(sorted(forbidden))
                )
            before_record = copy.deepcopy(before)
            after = copy.deepcopy(before)
            after.update(dict(metadata_updates or {}))
            return before_record, self._transition_locked(
                theorem_id,
                new_status,
                actor=actor,
                reason=reason,
                gate=gate,
                audit_status=audit_status,
                theorem=after,
            )

    def _transition_locked(
        self,
        theorem_id: str,
        new_status: str,
        *,
        actor: str,
        reason: str,
        gate: AuditGate | None,
        audit_status: str | None,
        theorem: dict | None = None,
    ) -> dict:
        theorem = theorem if theorem is not None else self.load_theorem(theorem_id)
        current = theorem["status"]
        validate_transition(current, new_status, actor=actor, gate=gate)
        if current == new_status:
            return theorem
        now = utc_now()
        if new_status == "FROZEN":
            theorem["status_before_frozen"] = current
        theorem["status"] = new_status
        if audit_status is not None:
            theorem["audit_status"] = audit_status
        theorem.setdefault("status_history", []).append(
            {
                "from": current,
                "to": new_status,
                "actor": actor,
                "reason": reason,
                "at": now,
            }
        )
        theorem["last_updated"] = now
        _write_json(self.theorem_path(theorem_id), theorem)
        self.rebuild_index()
        return theorem

    def set_current_target(self, theorem_id: str) -> None:
        self.load_theorem(theorem_id)
        project = self.load_project()
        project["current_target"] = theorem_id
        self.save_project(project)

    def rebuild_index(self, *, update_theorem_files: bool = True) -> dict:
        theorems = self.list_theorems()
        downstream = {item["id"]: [] for item in theorems}
        for theorem in theorems:
            for dependency in theorem.get("dependencies", []):
                downstream.setdefault(dependency, []).append(theorem["id"])
        if update_theorem_files:
            for theorem in theorems:
                expected = sorted(downstream.get(theorem["id"], []))
                if theorem.get("downstream_dependents", []) != expected:
                    theorem["downstream_dependents"] = expected
                    theorem["last_updated"] = utc_now()
                    _write_json(self.theorem_path(theorem["id"]), theorem)
        entries = []
        for theorem in theorems:
            entries.append(
                {
                    key: theorem.get(key)
                    for key in (
                        "id",
                        "title",
                        "status",
                        "source_file",
                        "dependencies",
                        "tags",
                        "branch",
                        "proof_type",
                        "audit_status",
                        "last_updated",
                        "notation_scope",
                    )
                }
            )
        index = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": utc_now(),
            "theorems": entries,
        }
        _write_json(self.root / "index.json", index)
        return index

    def safe_source_path(self, relative: str) -> Path | None:
        if not relative:
            return None
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ProjectError(f"source_file escapes project root: {relative}") from exc
        return candidate

    def record_failed_route(
        self,
        *,
        route_id: str,
        strategy: str,
        target: str,
        obtained: str,
        failure_point: str,
        insufficiency: str,
        recovery_conditions: str,
        theorem_ids: list[str],
        tags: list[str] | None = None,
    ) -> dict:
        self.validate_id(route_id)
        self.load_theorem(target)
        for theorem_id in theorem_ids:
            self.load_theorem(theorem_id)
        data = _read_json(self.root / "failed_routes.json", {"routes": []})
        if any(route.get("id") == route_id for route in data.get("routes", [])):
            raise ProjectError(f"Failed route already exists: {route_id}")
        route = {
            "id": route_id,
            "strategy": strategy,
            "target": target,
            "obtained": obtained,
            "failure_point": failure_point,
            "insufficiency": insufficiency,
            "recovery_conditions": recovery_conditions,
            "theorem_ids": sorted(set(theorem_ids + [target])),
            "tags": sorted(set(tags or [])),
            "status": "FAILED_ROUTE",
            "last_updated": utc_now(),
        }
        data.setdefault("schema_version", SCHEMA_VERSION)
        data.setdefault("routes", []).append(route)
        _write_json(self.root / "failed_routes.json", data)
        return route

    def relevant_failed_routes(self, theorem_ids: set[str], tags: set[str]) -> list[dict]:
        data = _read_json(self.root / "failed_routes.json", {"routes": []})
        relevant = []
        for route in data.get("routes", []):
            if theorem_ids.intersection(route.get("theorem_ids", [])) or tags.intersection(
                route.get("tags", [])
            ):
                relevant.append(route)
        return relevant

    def update_steering(
        self,
        *,
        freeze_branch: str | None = None,
        unfreeze_branch: str | None = None,
        prohibit_route: str | None = None,
        allow_scope: str | None = None,
        add_lemma: str | None = None,
        stop_worker: str | None = None,
        reauditing: bool = False,
    ) -> dict:
        path = self.root / "steering" / "directives.json"
        data = _read_json(path, {})
        for key in (
            "freeze_branches",
            "prohibit_routes",
            "allowed_scope",
            "added_lemmas",
            "stop_workers",
        ):
            data.setdefault(key, [])
        if freeze_branch and freeze_branch not in data["freeze_branches"]:
            data["freeze_branches"].append(freeze_branch)
        if unfreeze_branch:
            data["freeze_branches"] = [x for x in data["freeze_branches"] if x != unfreeze_branch]
        if prohibit_route and prohibit_route not in data["prohibit_routes"]:
            data["prohibit_routes"].append(prohibit_route)
        if allow_scope and allow_scope not in data["allowed_scope"]:
            data["allowed_scope"].append(allow_scope)
        if add_lemma and add_lemma not in data["added_lemmas"]:
            data["added_lemmas"].append(add_lemma)
        if stop_worker and stop_worker not in data["stop_workers"]:
            data["stop_workers"].append(stop_worker)
        if reauditing:
            data["reaudit_requested"] = True
        data["last_updated"] = utc_now()
        _write_json(path, data)
        return data

    def import_markdown(self, source: str | Path) -> list[dict]:
        source = Path(source).resolve()
        try:
            source.relative_to(self.root)
        except ValueError as exc:
            raise ProjectError(
                "Import source must be inside the project. Copy private Markdown into "
                f"{self.root / 'inbox'} first."
            ) from exc
        candidates = []
        existing_by_source = {
            theorem.get("source_file"): theorem for theorem in self.list_theorems()
        }
        ignored_parts = {"runs", "reports", "theorems", ".git", ".venv"}
        for path in sorted(source.rglob("*.md")):
            if path.name.casefold() == "readme.md":
                continue
            relative_path = path.relative_to(self.root)
            if ignored_parts.intersection(relative_path.parts):
                continue
            relative = relative_path.as_posix()
            if relative in existing_by_source:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            title_match = re.search(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else path.stem
            body = re.sub(r"^#{1,6}\s+.+?$", "", text, count=1, flags=re.MULTILINE).strip()
            excerpt = body[:1600].strip() or "Statement requires human extraction from source file."
            digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:8]
            base = self.make_id(path.stem)[:110]
            theorem_id = f"import-{base}-{digest}"
            theorem = self.add_theorem(
                theorem_id,
                title,
                excerpt,
                status="UNCLASSIFIED",
                source_file=relative,
                tags=["imported", "needs-human-review"],
                branch="unclassified-import",
                proof_type="UNKNOWN",
                claim_type="unclassified",
            )
            candidates.append(
                {
                    "id": theorem["id"],
                    "title": theorem["title"],
                    "source_file": relative,
                    "status": "UNCLASSIFIED",
                    "note": "Filename and wording were not used to infer proof status.",
                }
            )
        _write_json(
            self.root / "migration_candidates.json",
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": utc_now(),
                "candidates": candidates,
            },
        )
        return candidates

    def consume_reaudit_request(self) -> bool:
        path = self.root / "steering" / "directives.json"
        data = _read_json(path, {})
        requested = bool(data.get("reaudit_requested", False))
        if requested:
            data["reaudit_requested"] = False
            data["last_updated"] = utc_now()
            _write_json(path, data)
        return requested
