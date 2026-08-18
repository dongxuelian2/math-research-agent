"""Strict, read-only validation for human-approved migration batches."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .project import ProjectError, ProjectStore


@dataclass(slots=True)
class BatchValidationResult:
    batch: str
    topological_order: list[str]
    approved: list[str]
    rejected: list[dict]
    unknown_roots: list[str]
    cycle_free: bool

    @property
    def passed(self) -> bool:
        return self.cycle_free and not self.rejected

    def to_dict(self) -> dict:
        return {
            "batch": self.batch,
            "topological_order": list(self.topological_order),
            "approved": list(self.approved),
            "rejected": deepcopy(self.rejected),
            "unknown_roots": list(self.unknown_roots),
            "cycle_free": self.cycle_free,
            "passed": self.passed,
        }


def apply_dependency_repairs(records: Iterable[dict], repairs: Iterable[dict]) -> list[dict]:
    """Apply an explicit, auditable dependency rename overlay without mutating V2."""
    repaired = deepcopy(list(records))
    by_id = {item.get("id"): item for item in repaired}
    if len(by_id) != len(repaired):
        raise ProjectError("Duplicate canonical IDs in migration records")
    for repair in repairs:
        theorem_id = repair.get("canonical_id", "")
        old = repair.get("old_dependency", "")
        new = repair.get("new_dependency", "")
        ProjectStore.validate_id(theorem_id)
        ProjectStore.validate_id(old)
        ProjectStore.validate_id(new)
        if theorem_id not in by_id:
            raise ProjectError(f"Dependency repair target is absent: {theorem_id}")
        dependencies = list(by_id[theorem_id].get("dependencies", []))
        if dependencies.count(old) != 1:
            raise ProjectError(
                f"Dependency repair expected exactly one {old!r} edge on {theorem_id}"
            )
        by_id[theorem_id]["dependencies"] = [new if item == old else item for item in dependencies]
    return repaired


def validate_staged_batch(
    store: ProjectStore,
    records: Iterable[dict],
    *,
    batch: str,
    source_root: str | Path,
) -> BatchValidationResult:
    """Validate one batch; same-batch theorem dependencies must precede consumers."""
    rows = [deepcopy(item) for item in records if item.get("batch") == batch]
    ids = [item.get("id", "") for item in rows]
    if not rows:
        raise ProjectError(f"Migration batch is empty: {batch}")
    if len(ids) != len(set(ids)):
        raise ProjectError(f"Duplicate canonical IDs in batch: {batch}")
    for theorem_id in ids:
        ProjectStore.validate_id(theorem_id)

    id_set = set(ids)
    incoming = {theorem_id: 0 for theorem_id in ids}
    children = {theorem_id: [] for theorem_id in ids}
    for row in rows:
        for dependency in row.get("dependencies", []):
            if dependency in id_set:
                incoming[row["id"]] += 1
                children[dependency].append(row["id"])

    ready = sorted(theorem_id for theorem_id, count in incoming.items() if count == 0)
    order: list[str] = []
    while ready:
        theorem_id = ready.pop(0)
        order.append(theorem_id)
        for child in sorted(children[theorem_id]):
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
                ready.sort()
    cycle_free = len(order) == len(rows)
    cycle_ids = sorted(id_set.difference(order))
    order.extend(cycle_ids)

    source_root = Path(source_root).resolve()
    by_id = {item["id"]: item for item in rows}
    decisions: dict[str, str] = {}
    approved: list[str] = []
    rejected: list[dict] = []
    unknown_roots: set[str] = set()

    for theorem_id in order:
        row = by_id[theorem_id]
        reasons: list[str] = []
        if theorem_id in cycle_ids:
            reasons.append("dependency_cycle")
        if row.get("confidence") != "HIGH":
            reasons.append("confidence_not_HIGH")
        if row.get("proposed_status") != "PROPOSED_PROVED":
            reasons.append("staging_status_not_PROPOSED_PROVED")
        if row.get("approval_eligible") is False:
            reasons.append("approval_eligible_false")
        if row.get("known_conflicts"):
            reasons.append("unresolved_conflict")
        if row.get("audit_blockers"):
            reasons.append("audit_blocker")

        source = row.get("primary_source", "")
        source_path = (source_root / source).resolve() if source else None
        try:
            if source_path is None:
                raise ValueError
            source_path.relative_to(source_root)
        except ValueError:
            reasons.append("invalid_primary_source")
        else:
            if not source_path.is_file():
                reasons.append("missing_primary_source")

        for dependency in row.get("dependencies", []):
            if dependency in id_set:
                if decisions.get(dependency) != "APPROVE":
                    reasons.append(f"blocked_dependency:{dependency}")
                continue
            try:
                store.validate_proved_dependency(dependency)
            except ProjectError as exc:
                message = str(exc)
                if message.startswith("Unknown dependency:"):
                    reasons.append(f"missing_dependency:{dependency}")
                    unknown_roots.add(dependency)
                elif message.startswith("Theorem dependency is not PROVED:"):
                    reasons.append(f"dependency_not_PROVED:{dependency}")
                else:
                    reasons.append(f"invalid_dependency:{dependency}:{message}")

        decision = "APPROVE" if not reasons else "REJECT"
        decisions[theorem_id] = decision
        if decision == "APPROVE":
            approved.append(theorem_id)
        else:
            rejected.append({"id": theorem_id, "reasons": reasons})

    return BatchValidationResult(
        batch=batch,
        topological_order=order,
        approved=approved,
        rejected=rejected,
        unknown_roots=sorted(unknown_roots),
        cycle_free=cycle_free,
    )
