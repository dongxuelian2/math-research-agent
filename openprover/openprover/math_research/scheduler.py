"""Profiles, heterogeneous worker scheduling, strategy deduplication, and stop control."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from .project import ProjectError, ProjectStore, utc_now


WORKER_ROLES = (
    "constructive",
    "adversarial",
    "reconstruction",
    "alternative-proof",
    "boundary",
    "dependency",
    "computational-check",
)


@dataclass(frozen=True, slots=True)
class ResearchProfile:
    name: str
    budget_seconds: int
    initial_workers: int
    max_workers: int
    max_repair_cycles: int
    infrastructure_retries: int
    provider_retries: int
    auto_successor: bool
    auto_dependency_repair: bool
    hard_blocker: bool
    secondary_verification: bool

    def to_dict(self) -> dict:
        return asdict(self)


NORMAL_PROFILE = ResearchProfile(
    name="normal",
    budget_seconds=4 * 60 * 60,
    initial_workers=3,
    max_workers=3,
    max_repair_cycles=0,
    infrastructure_retries=0,
    provider_retries=1,
    auto_successor=False,
    auto_dependency_repair=False,
    hard_blocker=False,
    secondary_verification=False,
)

OVERNIGHT_PROFILE = ResearchProfile(
    name="overnight",
    budget_seconds=12 * 60 * 60,
    initial_workers=4,
    max_workers=6,
    max_repair_cycles=4,
    infrastructure_retries=3,
    provider_retries=2,
    auto_successor=True,
    auto_dependency_repair=True,
    hard_blocker=True,
    secondary_verification=True,
)


def resolve_profile(name: str | None) -> ResearchProfile:
    normalized = (name or "normal").strip().casefold().replace("_", "-")
    if normalized in {"normal", "default"}:
        return NORMAL_PROFILE
    if normalized in {"overnight", "long-horizon", "longhorizon"}:
        return OVERNIGHT_PROFILE
    raise ProjectError(f"Unknown research profile: {name}")


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


@dataclass(frozen=True, slots=True)
class StrategyFingerprint:
    theorem: str
    branch: str
    target_lemma: str
    method: str
    key_dependency: str
    failure_point: str

    @property
    def fingerprint(self) -> str:
        payload = "\n".join(
            _normalized(value)
            for value in (
                self.theorem,
                self.branch,
                self.target_lemma,
                self.method,
                self.key_dependency,
                self.failure_point,
            )
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict:
        value = asdict(self)
        value["fingerprint"] = self.fingerprint
        return value


class StrategyFingerprintStore:
    """Legacy execution heuristic retained for checkpoint compatibility.

    New production research failures belong to RouteFailureRecord. This store
    is readable and callable for old clients, but it is no longer the canonical
    long-term research-strategy owner.
    """

    def __init__(self, project: ProjectStore):
        self.project = project
        self.path = project.root / "strategy_fingerprints.json"

    def load(self) -> dict:
        if not self.path.exists():
            return {"schema_version": 1, "strategies": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ProjectError("Unsupported strategy fingerprint schema")
        return data

    def record_failure(self, strategy: StrategyFingerprint) -> dict:
        data = self.load()
        key = strategy.fingerprint
        record = data["strategies"].get(
            key,
            {
                **strategy.to_dict(),
                "failure_count": 0,
                "frozen": False,
                "history": [],
            },
        )
        record["failure_count"] = int(record.get("failure_count", 0)) + 1
        record["frozen"] = record["failure_count"] >= 2
        record["last_failed_at"] = utc_now()
        record["history"].append(
            {
                "event": "FAILURE",
                "failure_point": strategy.failure_point,
                "at": utc_now(),
            }
        )
        data["strategies"][key] = record
        data["last_updated"] = utc_now()
        self._write(data)
        return record

    def can_attempt(
        self,
        strategy: StrategyFingerprint,
        *,
        new_dependency: bool = False,
        new_lemma: bool = False,
        failure_condition_changed: bool = False,
    ) -> tuple[bool, str]:
        record = self.load()["strategies"].get(strategy.fingerprint)
        if not record or not record.get("frozen"):
            return True, "strategy is not frozen"
        if new_dependency or new_lemma or failure_condition_changed:
            return True, "frozen strategy reopened by new mathematical evidence"
        return False, "same strategy failed twice at the same failure point"

    def frozen_for_theorem(self, theorem_id: str) -> list[dict]:
        return [
            record
            for record in self.load()["strategies"].values()
            if record.get("theorem") == theorem_id and record.get("frozen")
        ]

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.path)


@dataclass(frozen=True, slots=True)
class WorkerAssignment:
    index: int
    role: str
    summary: str
    description: str
    branch: str
    obligation: str

    def to_dict(self) -> dict:
        return asdict(self)


_ROLE_HINTS = {
    "boundary": ("boundary", "endpoint", "range", "edge case", "n=0"),
    "dependency": ("dependency", "authority", "semantic", "foundation", "scope"),
    "adversarial": ("adversarial", "counterexample", "omitted", "attack", "falsify"),
    "reconstruction": ("reconstruct", "classification", "exhaustive", "normal form"),
    "computational-check": ("compute", "certificate", "enumerate", "numerical", "script"),
    "alternative-proof": ("alternative", "independent proof", "second proof", "telescop"),
    "constructive": ("construct", "prove", "derive", "induction", "direct"),
}


class RoleScheduler:
    """Assign distinct proof roles and expand 4→6 only for real parallelism."""

    def __init__(self, *, initial_workers: int = 4, max_workers: int = 6):
        if initial_workers < 1 or max_workers < initial_workers:
            raise ProjectError("Invalid worker scheduler limits")
        if max_workers > 6:
            raise ProjectError("Research Harness v2 caps worker parallelism at 6")
        self.initial_workers = initial_workers
        self.max_workers = max_workers

    def capacity_for(self, tasks: list[dict]) -> int:
        obligations = {
            _normalized(task.get("obligation") or task.get("summary"))
            for task in tasks
            if task.get("obligation") or task.get("summary")
        }
        independent_branches = {
            _normalized(task.get("branch"))
            for task in tasks
            if task.get("branch") and task.get("independent_branch", False)
        }
        can_expand = len(obligations) >= 5 or len(independent_branches) >= 3
        return self.max_workers if can_expand else self.initial_workers

    def assign_tasks(self, tasks: list[dict]) -> list[WorkerAssignment]:
        capacity = self.capacity_for(tasks)
        selected = tasks[:capacity]
        used_roles: set[str] = set()
        assignments = []
        for index, task in enumerate(selected):
            role = self._infer_role(task, used_roles, index)
            used_roles.add(role)
            summary = str(task.get("summary", f"Obligation {index + 1}"))
            description = str(task.get("description", ""))
            branch = str(task.get("branch_id") or task.get("branch", "main"))
            obligation = str(task.get("obligation_id") or task.get("obligation", summary))
            assignments.append(
                WorkerAssignment(
                    index=index,
                    role=role,
                    summary=summary,
                    description=self.role_prompt(
                        role,
                        description,
                        branch=branch,
                        obligation=obligation,
                    ),
                    branch=branch,
                    obligation=obligation,
                )
            )
        return assignments

    @staticmethod
    def role_prompt(
        role: str,
        description: str,
        *,
        branch: str = "main",
        obligation: str = "unspecified",
    ) -> str:
        directives = {
            "constructive": "Build the requested branch proof constructively and expose every lemma used.",
            "adversarial": "Try to falsify the obligation and search systematically for omitted cases.",
            "reconstruction": "Reconstruct the classification from upstream normal forms without assuming the target conclusion.",
            "alternative-proof": "Develop an independent proof route with different key dependencies.",
            "boundary": "Audit endpoints, empty cases, signs, parity, and every stated parameter range.",
            "dependency": "Resolve each external claim to an exact Foundation, Semantic, or Project Theorem authority ID.",
            "computational-check": "Run only bounded checks or reproducible certificates; never present them as an infinite proof.",
        }
        return (
            f"[Worker role: {role}]\n"
            f"[Obligation ID: {obligation}]\n"
            f"[Branch ID: {branch}]\n"
            f"{directives[role]}\n\n{description}"
        ).rstrip()

    @staticmethod
    def _infer_role(task: dict, used_roles: set[str], index: int) -> str:
        explicit = str(task.get("role", "")).strip().casefold()
        if explicit in WORKER_ROLES:
            return explicit
        text = _normalized(
            f"{task.get('summary', '')} {task.get('description', '')} {task.get('obligation', '')}"
        )
        candidates = [
            role for role, hints in _ROLE_HINTS.items() if any(hint in text for hint in hints)
        ]
        for role in candidates:
            if role not in used_roles:
                return role
        for offset in range(len(WORKER_ROLES)):
            role = WORKER_ROLES[(index + offset) % len(WORKER_ROLES)]
            if role not in used_roles:
                return role
        return candidates[0] if candidates else WORKER_ROLES[index % len(WORKER_ROLES)]


class StopController:
    """Cross-process graceful-stop request persisted beside the campaign."""

    def __init__(self, project: ProjectStore, campaign_id: str):
        project.validate_id(campaign_id)
        self.path = project.root / "campaigns" / f"{campaign_id}.stop.json"

    def request(self, *, reason: str) -> dict:
        if not reason.strip():
            raise ProjectError("Graceful stop reason is required")
        value = {
            "status": "REQUESTED",
            "reason": reason.strip(),
            "requested_at": utc_now(),
        }
        self._write(value)
        return value

    def requested(self) -> bool:
        return self.load().get("status") == "REQUESTED"

    def acknowledge(self, *, run_id: str, checkpoint: str) -> dict:
        value = self.load()
        value.update(
            {
                "status": "CHECKPOINTED",
                "run_id": run_id,
                "checkpoint": checkpoint,
                "acknowledged_at": utc_now(),
            }
        )
        self._write(value)
        return value

    def clear_for_resume(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def load(self) -> dict:
        if not self.path.exists():
            return {"status": "NONE"}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, value: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.path)
