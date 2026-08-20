"""Durable campaign lifecycle, hard submission gate, and replay policy.

The campaign layer deliberately wraps the existing research orchestrator.  A
run remains an immutable piece of evidence once it reaches ``COMPLETE``;
repair proceeds in a new successor run linked by ``parent_run_id``.
"""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .canonical_artifacts import CanonicalSourceRequirement
from .governance import GovernanceController
from .project import ProjectError, ProjectStore, utc_now
from .research_store import ResearchStoreFacade
from .scheduler import StopController
from .trust_kernel import DependencyAuthorityResolver
from .truth_store import TruthStoreFacade


CAMPAIGN_SCHEMA_VERSION = 2
FAILURE_CATEGORIES = {
    "MATHEMATICAL_GAP",
    "EXHAUSTIVENESS_GAP",
    "BOUNDARY_GAP",
    "CONVERSE_GAP",
    "DEPENDENCY_GAP",
    "SEMANTIC_GAP",
    "FOUNDATION_GAP",
    "SCOPE_GAP",
    "COUNTEREXAMPLE",
    "INFRASTRUCTURE_ERROR",
    "PROVIDER_ERROR",
    "UNKNOWN",
}
HARD_BLOCKERS = {
    "SCOPE_GAP",
    "DEPENDENCY_GAP",
    "MISSING_AUTHORITY",
    "ANSWER_LEAK_RISK",
    "UNRESOLVED_BRANCH",
    "BLOCKED_DEPENDENCY",
    "REQUIRED_DEPENDENCY_EXPANSION",
}
TERMINAL_RUN_STATUSES = {
    "PROVED",
    "COMPLETE_PROVED_REPLAY",
    "REJECTED",
    "MATHEMATICAL_EXHAUSTION",
    "HUMAN_REQUIRED",
}
QUOTA_ERROR_TYPES = {"quota_exceeded", "usage_limit_reached"}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _canonical_source(value: str) -> str:
    text = str(value).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return PurePosixPath(text).as_posix().casefold()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def classify_provider_exception(exc: BaseException) -> tuple[str, dict]:
    """Map structured provider failures to resumable campaign states."""

    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        details = {"error_type": type(exc).__name__, "message": str(exc)}
    error_type = str(details.get("error_type", "")).casefold()
    text = json.dumps(details, ensure_ascii=False).casefold()
    if error_type in QUOTA_ERROR_TYPES or any(
        marker in text
        for marker in (
            "quota exceeded",
            "usage limit",
            "usage cap",
            "credits exhausted",
            "no weighted tokens left",
        )
    ):
        return "BLOCKED_PROVIDER_QUOTA", dict(details)
    return "BLOCKED_INFRASTRUCTURE", dict(details)


@dataclass(slots=True)
class FailureItem:
    category: str
    exact_rejected_claim: str
    auditor: str
    candidate_location: str
    authority_expected: str
    blocking: bool
    repair_suggestion: str
    affected_branch: str

    def __post_init__(self) -> None:
        if self.category not in FAILURE_CATEGORIES:
            raise ProjectError(f"Unknown failure-map category: {self.category}")


def _failure_category(reason: str, auditor: str) -> str:
    value = f"{auditor} {reason}".casefold()
    if any(
        word in value
        for word in (
            "infrastructure",
            "subprocess",
            "encoding",
            "filesystem",
            "malformed json",
            "timeout",
        )
    ):
        return "INFRASTRUCTURE_ERROR"
    if any(word in value for word in ("provider", "quota", "rate limit", "transport")):
        return "PROVIDER_ERROR"
    if "counterexample" in value:
        return "COUNTEREXAMPLE"
    if any(word in value for word in ("foundation", "found-")):
        return "FOUNDATION_GAP"
    if any(word in value for word in ("semantic", "definition", "sem-")):
        return "SEMANTIC_GAP"
    if any(word in value for word in ("scope", "notation", "branch")):
        return "SCOPE_GAP"
    if any(word in value for word in ("dependency", "authority", "not proved")):
        return "DEPENDENCY_GAP"
    if any(word in value for word in ("exhaust", "omitted case", "classification")):
        return "EXHAUSTIVENESS_GAP"
    if any(word in value for word in ("boundary", "endpoint", "range")):
        return "BOUNDARY_GAP"
    if any(word in value for word in ("converse", "reverse implication")):
        return "CONVERSE_GAP"
    if reason.strip():
        return "MATHEMATICAL_GAP"
    return "UNKNOWN"


@dataclass(slots=True)
class FailureMap:
    run_id: str
    target_id: str
    items: list[FailureItem]
    created_at: str = field(default_factory=utc_now)
    schema_version: int = 1

    @classmethod
    def from_gate(
        cls,
        *,
        run_id: str,
        target_id: str,
        gate: Any,
        audits: dict[str, dict] | None = None,
        candidate_location: str = "CANDIDATE_PROOF.md",
        affected_branch: str = "main",
    ) -> "FailureMap":
        audits = audits or {}
        items: list[FailureItem] = []
        handled: set[tuple[str, str]] = set()
        for auditor, result in sorted(audits.items()):
            execution_status = result.get("execution_status", "OK")
            reasons = list(result.get("failure_reasons", []))
            if execution_status == "ERROR":
                reasons = [result.get("execution_error") or "auditor execution failed"]
            for reason in reasons:
                key = (auditor, str(reason))
                if key in handled:
                    continue
                handled.add(key)
                category = _failure_category(str(reason), auditor)
                items.append(
                    FailureItem(
                        category=category,
                        exact_rejected_claim=str(reason),
                        auditor=auditor,
                        candidate_location=candidate_location,
                        authority_expected=(
                            "exact Foundation, Semantic, or Project Theorem authority ID"
                            if category in {"FOUNDATION_GAP", "SEMANTIC_GAP", "DEPENDENCY_GAP"}
                            else ""
                        ),
                        blocking=True,
                        repair_suggestion=_repair_suggestion(category),
                        affected_branch=affected_branch,
                    )
                )
        gate_reasons = list(getattr(gate, "failure_reasons", [])) + list(
            getattr(gate, "execution_errors", [])
        )
        for reason in gate_reasons:
            if any(existing_reason == str(reason) for _, existing_reason in handled):
                continue
            category = _failure_category(str(reason), "gate")
            items.append(
                FailureItem(
                    category=category,
                    exact_rejected_claim=str(reason),
                    auditor="gate",
                    candidate_location=candidate_location,
                    authority_expected=("exact authority ID" if "GAP" in category else ""),
                    blocking=True,
                    repair_suggestion=_repair_suggestion(category),
                    affected_branch=affected_branch,
                )
            )
        if not items:
            items.append(
                FailureItem(
                    category="UNKNOWN",
                    exact_rejected_claim="Audit gate did not pass",
                    auditor="gate",
                    candidate_location=candidate_location,
                    authority_expected="",
                    blocking=True,
                    repair_suggestion="Request a human review of the audit artifacts.",
                    affected_branch=affected_branch,
                )
            )
        return cls(run_id=run_id, target_id=target_id, items=items)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "target_id": self.target_id,
            "created_at": self.created_at,
            "items": [asdict(item) for item in self.items],
        }

    def write(self, run_dir: Path) -> tuple[Path, Path]:
        json_path = run_dir / "FAILURE_MAP.json"
        md_path = run_dir / "FAILURE_MAP.md"
        _write_json(json_path, self.to_dict())
        lines = [
            "# Failure Map",
            "",
            f"Run: `{self.run_id}`",
            "",
        ]
        for index, item in enumerate(self.items, 1):
            lines.extend(
                [
                    f"## {index}. {item.category}",
                    "",
                    f"- Rejected claim: {item.exact_rejected_claim}",
                    f"- Auditor: `{item.auditor}`",
                    f"- Candidate location: `{item.candidate_location}`",
                    f"- Authority expected: {item.authority_expected or '(none)'}",
                    f"- Blocking: `{str(item.blocking).lower()}`",
                    f"- Repair: {item.repair_suggestion}",
                    f"- Branch: `{item.affected_branch}`",
                    "",
                ]
            )
        md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return json_path, md_path


def _repair_suggestion(category: str) -> str:
    suggestions = {
        "FOUNDATION_GAP": "Add the exact versioned foundation authority, or prove the claim locally.",
        "SEMANTIC_GAP": "Resolve the claim against a notation-scoped semantic source with a matching hash.",
        "DEPENDENCY_GAP": "Materialize only a manifest-approved predecessor authority or prove the lemma locally.",
        "SCOPE_GAP": "Close the named scope or branch obligation before another submission.",
        "EXHAUSTIVENESS_GAP": "Repair only the omitted case split and reconstruct exhaustiveness.",
        "BOUNDARY_GAP": "Check and prove the exact missing endpoint or parameter range.",
        "CONVERSE_GAP": "Supply the missing reverse implication without reopening unrelated branches.",
        "COUNTEREXAMPLE": "Reject or narrow the claim; do not patch around the counterexample.",
        "INFRASTRUCTURE_ERROR": "Retry the failed operation within the bounded infrastructure retry budget.",
        "PROVIDER_ERROR": "Checkpoint provider state and resume after quota or transport recovery.",
    }
    return suggestions.get(
        category, "Repair the exact recorded obligation; do not reopen the whole theorem."
    )


@dataclass(frozen=True, slots=True)
class ReplayPolicy:
    """Normalized, inheritable allow/deny policy for replay materialization."""

    allowed_sources: tuple[str, ...] = ()
    forbidden_sources: tuple[str, ...] = ()
    allowed_authority_ids: tuple[str, ...] = ()
    approved_historical_authorities: tuple[tuple[str, str], ...] = ()
    target_cutoff: str = ""
    source_manifest: str = ""
    source_manifest_hash: str = ""
    canonical_source_roots: tuple[str, ...] = ()
    canonical_source_requirements: tuple[CanonicalSourceRequirement, ...] = ()

    @classmethod
    def from_manifest(cls, path: str | Path) -> "ReplayPolicy":
        path = Path(path).resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        materialized = data.get("materialized_sources", {})
        allowed = (
            list(materialized.values())
            if isinstance(materialized, dict)
            else list(materialized or [])
        )
        allowed.extend(data.get("allowed_sources", []))
        forbidden = [
            item
            for item in data.get("excluded_later_results", [])
            if "listed sections only" not in str(item).casefold()
        ]
        forbidden.extend(
            item.get("source_file", "")
            for item in data.get("excluded_answer_leaks", [])
            if (
                isinstance(item, dict)
                and (
                    str(item.get("action", "")).casefold() in {"", "exclude file"}
                    or str(item.get("section", "")).casefold() == "entire file"
                )
            )
        )
        approved = data.get("approved_historical_authorities", {})
        if isinstance(approved, dict):
            approved_items = tuple(
                sorted((str(key), _canonical_source(value)) for key, value in approved.items())
            )
            allowed.extend(approved.values())
        else:
            approved_items = ()
        raw = path.read_bytes()
        canonical_roots = []
        for item in data.get("canonical_source_roots", []):
            root = Path(item)
            if not root.is_absolute():
                root = path.parent / root
            canonical_roots.append(str(root.resolve()))
        canonical_roots.append(str(path.parent.resolve()))
        requirements = tuple(
            CanonicalSourceRequirement.from_dict(item)
            for item in data.get("canonical_source_requirements", data.get("canonical_sources", []))
        )
        return cls(
            allowed_sources=tuple(sorted({_canonical_source(item) for item in allowed if item})),
            forbidden_sources=tuple(
                sorted({_canonical_source(str(item).split(" (")[0]) for item in forbidden if item})
            ),
            allowed_authority_ids=tuple(
                sorted(
                    set(data.get("allowed_proved_dependencies", []))
                    | set(data.get("allowed_authority_ids", []))
                )
            ),
            approved_historical_authorities=approved_items,
            target_cutoff=str(data.get("target_cutoff", "")),
            source_manifest=str(path),
            source_manifest_hash="sha256:" + hashlib.sha256(raw).hexdigest(),
            canonical_source_roots=tuple(sorted(set(canonical_roots))),
            canonical_source_requirements=requirements,
        )

    @classmethod
    def from_dict(cls, value: dict) -> "ReplayPolicy":
        approved = value.get("approved_historical_authorities", [])
        return cls(
            allowed_sources=tuple(value.get("allowed_sources", [])),
            forbidden_sources=tuple(value.get("forbidden_sources", [])),
            allowed_authority_ids=tuple(value.get("allowed_authority_ids", [])),
            approved_historical_authorities=tuple(tuple(item) for item in approved),
            target_cutoff=str(value.get("target_cutoff", "")),
            source_manifest=str(value.get("source_manifest", "")),
            source_manifest_hash=str(value.get("source_manifest_hash", "")),
            canonical_source_roots=tuple(value.get("canonical_source_roots", [])),
            canonical_source_requirements=tuple(
                CanonicalSourceRequirement.from_dict(item)
                for item in value.get("canonical_source_requirements", [])
            ),
        )

    @property
    def policy_hash(self) -> str:
        return _stable_hash(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict:
        value = {
            "allowed_sources": list(self.allowed_sources),
            "forbidden_sources": list(self.forbidden_sources),
            "allowed_authority_ids": list(self.allowed_authority_ids),
            "approved_historical_authorities": [
                list(item) for item in self.approved_historical_authorities
            ],
            "target_cutoff": self.target_cutoff,
            "source_manifest": self.source_manifest,
            "source_manifest_hash": self.source_manifest_hash,
            "canonical_source_roots": list(self.canonical_source_roots),
            "canonical_source_requirements": [
                item.to_dict() for item in self.canonical_source_requirements
            ],
        }
        if include_hash:
            value["policy_hash"] = self.policy_hash
        return value

    def audit_sources(self, sources: list[str] | tuple[str, ...]) -> tuple[bool, list[str]]:
        violations = []
        for source in sources:
            normalized = _canonical_source(source)
            if self._is_forbidden(normalized):
                violations.append(f"forbidden replay source: {source}")
                continue
            if self.allowed_sources and normalized not in self.allowed_sources:
                violations.append(f"source is not manifest-approved: {source}")
        return not violations, violations

    def audit_explicit_extension(
        self, sources: list[str] | tuple[str, ...]
    ) -> tuple[bool, list[str]]:
        """Check an explicitly authorized certification source against denies.

        This does not mutate or widen the inherited policy.  The caller must
        separately pin the extension by content hash and provenance.
        """

        violations = []
        for source in sources:
            if self._is_forbidden(_canonical_source(source)):
                violations.append(f"explicit extension is forbidden: {source}")
        return not violations, violations

    def _is_forbidden(self, source: str) -> bool:
        return any(
            fnmatch.fnmatch(source, pattern.replace("*", "*")) or source == pattern
            for pattern in self.forbidden_sources
        )

    def authorize_dependency_repair(self, record: dict) -> tuple[bool, list[str]]:
        errors: list[str] = []
        authority_id = str(record.get("authority_id", ""))
        authority_type = str(record.get("authority_type", ""))
        source_file = _canonical_source(record.get("source_file", ""))
        expected = dict(self.approved_historical_authorities).get(authority_id)
        if authority_type not in {"semantic", "project_theorem"}:
            errors.append("automatic repair is limited to semantic or project theorem authorities")
        if authority_id not in self.allowed_authority_ids and expected is None:
            errors.append(
                f"authority is outside the approved historical dependency graph: {authority_id}"
            )
        if expected is not None and source_file != expected:
            errors.append("authority source does not match the manifest identity")
        ok, source_errors = self.audit_sources([source_file])
        if not ok:
            errors.extend(source_errors)
        if not record.get("identity_verified", False):
            errors.append("semantic or theorem identity was not verified")
        if self.target_cutoff:
            source_created = str(record.get("source_created_at", ""))
            if not source_created or source_created >= self.target_cutoff:
                errors.append("historical source is not earlier than the target cutoff")
        if not record.get("leak_audit_pass", False):
            errors.append("dependency materialization leak audit did not pass")
        return not errors, errors

    def materialize_dependency(
        self,
        record: dict,
        *,
        source_root: str | Path,
        destination_root: str | Path,
    ) -> Path:
        allowed, errors = self.authorize_dependency_repair(record)
        if not allowed:
            raise ProjectError("Automatic dependency repair rejected: " + "; ".join(errors))
        source_root = Path(source_root).resolve()
        destination_root = Path(destination_root).resolve()
        source = (source_root / record["source_file"]).resolve()
        try:
            source.relative_to(source_root)
        except ValueError as exc:
            raise ProjectError("Dependency repair source escapes source_root") from exc
        destination = (destination_root / record["source_file"]).resolve()
        try:
            destination.relative_to(destination_root)
        except ValueError as exc:
            raise ProjectError("Dependency repair destination escapes destination_root") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination


_MANIFEST_RE = re.compile(
    r"<!--\s*OPENPROVER_AUTHORITY_MANIFEST\s*(\{.*?\})\s*-->",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(slots=True)
class PreSubmitDecision:
    allowed: bool
    blockers: list[dict]
    dependency_report: dict
    authority_manifest: dict
    checked_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return asdict(self)


class PreSubmitGate:
    """Deterministic code-level blocker evaluated before ``PROOF.md`` exists."""

    def __init__(
        self,
        *,
        resolver: DependencyAuthorityResolver,
        blocked_dependencies: list[str] | None = None,
        dependency_cycles: list[list[str]] | None = None,
        replay_policy: ReplayPolicy | None = None,
        require_manifest: bool = True,
    ):
        self.resolver = resolver
        self.blocked_dependencies = blocked_dependencies or []
        self.dependency_cycles = dependency_cycles or []
        self.replay_policy = replay_policy
        self.require_manifest = require_manifest

    def evaluate(self, candidate: str) -> PreSubmitDecision:
        blockers: list[dict] = []
        if self.blocked_dependencies:
            blockers.append(
                {
                    "type": "BLOCKED_DEPENDENCY",
                    "detail": ", ".join(self.blocked_dependencies),
                }
            )
        if self.dependency_cycles:
            blockers.append({"type": "DEPENDENCY_GAP", "detail": "dependency cycle detected"})
        match = _MANIFEST_RE.search(candidate)
        manifest: dict = {}
        if match:
            try:
                manifest = json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                blockers.append(
                    {
                        "type": "MISSING_AUTHORITY",
                        "detail": f"invalid authority manifest JSON: {exc}",
                    }
                )
        elif self.require_manifest:
            blockers.append(
                {"type": "MISSING_AUTHORITY", "detail": "OPENPROVER_AUTHORITY_MANIFEST is required"}
            )
        for item in manifest.get("unresolved", []):
            blocker_type = (
                str(item.get("type", "DEPENDENCY_GAP")) if isinstance(item, dict) else str(item)
            )
            if blocker_type not in HARD_BLOCKERS:
                blocker_type = "DEPENDENCY_GAP"
            blockers.append({"type": blocker_type, "detail": str(item)})
        if manifest and not manifest.get("all_external_claims_classified", False):
            blockers.append(
                {"type": "MISSING_AUTHORITY", "detail": "external claims are not fully classified"}
            )
        if manifest and not manifest.get("branches_resolved", False):
            blockers.append(
                {"type": "UNRESOLVED_BRANCH", "detail": "candidate reports unresolved branches"}
            )
        dependency_report = self.resolver.resolve(manifest.get("authority_uses", [])).to_dict()
        for error in dependency_report.get("errors", []):
            blockers.append({"type": "MISSING_AUTHORITY", "detail": error})
        if self.replay_policy is not None:
            ok, errors = self.replay_policy.audit_sources(manifest.get("source_paths", []))
            if not ok:
                blockers.extend({"type": "ANSWER_LEAK_RISK", "detail": error} for error in errors)
        # Explicit structured blocker declarations are never closable by a
        # free-form planner assertion.  They remain blocked until removed.
        for blocker_type in sorted(HARD_BLOCKERS):
            if re.search(
                rf"OPENPROVER_BLOCKER\s*:\s*{re.escape(blocker_type)}\b",
                candidate,
                re.IGNORECASE,
            ):
                blockers.append(
                    {"type": blocker_type, "detail": "candidate contains unresolved blocker token"}
                )
        unique = []
        seen = set()
        for blocker in blockers:
            key = (blocker["type"], blocker["detail"])
            if key not in seen:
                seen.add(key)
                unique.append(blocker)
        return PreSubmitDecision(
            allowed=not unique,
            blockers=unique,
            dependency_report=dependency_report,
            authority_manifest=manifest,
        )


class CampaignStore:
    """Atomic JSON campaign records stored below ``project/campaigns``."""

    def __init__(self, project: ProjectStore):
        self.project = project
        self.truth_store = TruthStoreFacade(project)
        self.root = project.root / "campaigns"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, campaign_id: str) -> Path:
        self.project.validate_id(campaign_id)
        return self.root / f"{campaign_id}.json"

    def create(
        self,
        campaign_id: str,
        *,
        target_id: str,
        profile: str = "normal",
        max_repair_cycles: int = 0,
        infrastructure_retries: int = 0,
        auto_successor: bool = False,
        auto_dependency_repair: bool = False,
        hard_blocker: bool = False,
        replay_policy: ReplayPolicy | None = None,
        dependency_repair_catalog: dict[str, dict] | None = None,
        dependency_repair_source_root: str | Path | None = None,
        budget_seconds: int | None = None,
        initial_workers: int | None = None,
        max_workers: int | None = None,
        secondary_verification: bool = False,
        routing_override: dict | None = None,
    ) -> dict:
        path = self.path(campaign_id)
        if path.exists():
            raise ProjectError(f"Campaign already exists: {campaign_id}")
        self.project.load_theorem(target_id)
        if max_repair_cycles < 0 or infrastructure_retries < 0:
            raise ProjectError("Campaign retry limits must be non-negative")
        now = utc_now()
        replay_policy_hash = replay_policy.policy_hash if replay_policy else None
        root_snapshot = self.truth_store.capture_claim_snapshot(
            target_id,
            replay_policy_hash=replay_policy_hash,
        )
        research_store = ResearchStoreFacade(
            self.project,
            truth_store=self.truth_store,
            root_validation_kwargs={"replay_policy_hash": replay_policy_hash},
        )
        research_map = research_store.create_initial_map(
            research_map_id=f"map-{campaign_id}",
            root_theorem_id=target_id,
            root_claim_snapshot_hash=root_snapshot.claim_snapshot_hash,
            obligations=[
                {
                    "obligation_id": f"ro-{target_id}-root",
                    "title": f"Resolve root research target {target_id}",
                    "statement": self.project.load_theorem(target_id)["statement"],
                    "obligation_kind": "OTHER",
                    "scope": [f"root theorem {target_id}"],
                }
            ],
            created_by="CampaignStore",
            strategic_thesis="Maintain an explicit durable frontier for the campaign root.",
        )
        record = {
            "schema_version": CAMPAIGN_SCHEMA_VERSION,
            "campaign_id": campaign_id,
            "target_id": target_id,
            "profile": profile,
            "status": "RUNNING",
            "max_repair_cycles": max_repair_cycles,
            "infrastructure_retries": infrastructure_retries,
            "auto_successor": bool(auto_successor),
            "auto_dependency_repair": bool(auto_dependency_repair),
            "hard_blocker": bool(hard_blocker),
            "repair_cycles_used": 0,
            "runs": [],
            "replay_policy": replay_policy.to_dict() if replay_policy else None,
            "dependency_repair_catalog": dependency_repair_catalog or {},
            "dependency_repair_source_root": (
                str(Path(dependency_repair_source_root).resolve())
                if dependency_repair_source_root
                else None
            ),
            "budget_seconds": budget_seconds,
            "initial_workers": initial_workers,
            "max_workers": max_workers,
            "secondary_verification": bool(secondary_verification),
            "routing_override": copy.deepcopy(routing_override or {}),
            "pipeline_state": {"schema_version": 3},
            "root_claim_snapshot_hash": root_snapshot.claim_snapshot_hash,
            "root_assertion_identity_hash": root_snapshot.assertion_identity_hash,
            "truth_replay_policy_hash": replay_policy_hash,
            "truth_canonical_authority": [],
            "research_map_id": research_map.research_map_id,
            "research_map_version": research_map.version,
            "research_map_hash": research_map.research_map_hash,
            "open_obligation_ids": list(research_map.open_obligation_ids),
            "created_at": now,
            "last_updated": now,
        }
        governance = GovernanceController(self.project, research_store=research_store)
        record.update(governance.checkpoint_projection(research_map.research_map_id))
        record["governance_checkpoint_classification"] = "DIRECT_IMPORT"
        _write_json(path, record)
        return record

    def load(self, campaign_id: str) -> dict:
        path = self.path(campaign_id)
        if not path.exists():
            raise ProjectError(f"Unknown campaign: {campaign_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != CAMPAIGN_SCHEMA_VERSION:
            raise ProjectError("Unsupported campaign schema")
        if not isinstance(data.get("routing_override"), dict):
            raise ProjectError("Campaign routing_override is missing or malformed")
        if not isinstance(data.get("pipeline_state"), dict):
            raise ProjectError("Campaign pipeline_state is missing or malformed")
        if data["pipeline_state"].get("schema_version") != 3:
            raise ProjectError("Campaign pipeline_state must use schema_version 3")
        return data

    def _save(self, record: dict) -> dict:
        record["last_updated"] = utc_now()
        _write_json(self.path(record["campaign_id"]), record)
        return record

    def register_initial(self, campaign_id: str, run_id: str) -> dict:
        record = self.load(campaign_id)
        if record["runs"]:
            raise ProjectError("Campaign already has an initial run")
        run = self._run_record(run_id, None, 0, "INITIAL")
        run["research_binding"] = self._research_binding(record)
        record["runs"].append(run)
        return self._save(record)

    def create_successor(self, campaign_id: str, *, parent_run_id: str) -> dict:
        record = self.load(campaign_id)
        parent = next((item for item in record["runs"] if item["run_id"] == parent_run_id), None)
        if parent is None:
            raise ProjectError(f"Parent run is not in campaign: {parent_run_id}")
        existing = next(
            (item for item in record["runs"] if item.get("parent_run_id") == parent_run_id), None
        )
        if existing is not None:
            raise ProjectError(
                f"Successor already exists for {parent_run_id}: {existing['run_id']}"
            )
        if parent.get("status") not in TERMINAL_RUN_STATUSES:
            raise ProjectError("Successor requires a terminal immutable parent run")
        cycle = int(parent.get("repair_cycle", 0)) + 1
        if cycle > int(record["max_repair_cycles"]):
            raise ProjectError("Maximum repair cycles reached")
        run_id = self._successor_id(record["target_id"], cycle)
        child = self._run_record(run_id, parent_run_id, cycle, "REPAIR")
        child["research_binding"] = self._research_binding(record)
        child["inheritance"] = {
            "previous_candidate": f"runs/{parent_run_id}/CANDIDATE_PROOF.md",
            "audits": f"runs/{parent_run_id}/audits",
            "failure_map": f"runs/{parent_run_id}/FAILURE_MAP.json",
            "failed_routes": "failed_routes.json",
            "verified_local_lemmas": f"runs/{parent_run_id}/verified_local_lemmas.json",
            "usage_summary": f"runs/{parent_run_id}/usage.json",
            "trust_kernel_context": f"runs/{parent_run_id}/context/context.json",
            "replay_policy_hash": (record.get("replay_policy") or {}).get("policy_hash"),
            "excluded": [
                "live_subprocess_state",
                "forbidden_historical_sources",
                "unrelated_project_files",
            ],
            "logical_async_state": "campaign.pipeline_state",
            "no_live_futures": True,
        }
        # The campaign-level snapshot is the only state successor runs may
        # inherit.  ACTIVE entries are reconciled by AsyncDAGScheduler when the
        # child process opens them; Future/thread/process objects never cross
        # this boundary.
        record["pipeline_state"] = copy.deepcopy(record.get("pipeline_state") or {})
        record.setdefault("successor_inheritance", []).append(
            {
                "from_run_id": parent_run_id,
                "to_run_id": run_id,
                "preserved": [
                    "pending_literature",
                    "blocked_dependencies",
                    "verified_authority",
                    "dag_edges",
                    "escalation_history",
                    "pending_verification",
                ],
                "at": utc_now(),
            }
        )
        record["runs"].append(child)
        record["repair_cycles_used"] = cycle
        self._save(record)
        return child

    def mark_run(
        self, campaign_id: str, run_id: str, *, status: str, phase: str = "COMPLETE"
    ) -> dict:
        record = self.load(campaign_id)
        run = next((item for item in record["runs"] if item["run_id"] == run_id), None)
        if run is None:
            raise ProjectError(f"Run is not in campaign: {run_id}")
        if run.get("status") in TERMINAL_RUN_STATUSES:
            if run["status"] != status:
                raise ProjectError("Completed campaign run is immutable")
            return record
        run["status"] = status
        run["phase"] = phase
        run["completed_at"] = utc_now() if phase == "COMPLETE" else None
        return self._save(record)

    def finish(self, campaign_id: str, status: str) -> dict:
        record = self.load(campaign_id)
        record["status"] = status
        record["completed_at"] = utc_now()
        return self._save(record)

    def checkpoint(self, campaign_id: str, status: str) -> dict:
        record = self.load(campaign_id)
        record["status"] = status
        record["checkpointed_at"] = utc_now()
        record.pop("completed_at", None)
        return self._save(record)

    def update_runtime_state(
        self,
        campaign_id: str,
        *,
        pipeline_state: dict | None = None,
        routing_state: dict | None = None,
        run_id: str | None = None,
        claim_snapshot_hash: str | None = None,
        canonical_authority: list[dict] | None = None,
        research_frontier: dict | None = None,
    ) -> dict:
        """Persist durable logical state without copying futures/process handles."""
        record = self.load(campaign_id)
        if pipeline_state is not None:
            record["pipeline_state"] = copy.deepcopy(pipeline_state)
        if routing_state is not None:
            record["routing_state"] = copy.deepcopy(routing_state)
        if run_id:
            record["runtime_state_run_id"] = run_id
        if claim_snapshot_hash:
            snapshot = self.truth_store.load_claim_snapshot(claim_snapshot_hash)
            if snapshot.theorem_id != record["target_id"]:
                raise ProjectError("Campaign ClaimSnapshot belongs to a different theorem")
            record["root_claim_snapshot_hash"] = claim_snapshot_hash
            record["root_assertion_identity_hash"] = snapshot.assertion_identity_hash
        if canonical_authority is not None:
            record["truth_canonical_authority"] = copy.deepcopy(canonical_authority)
        if research_frontier is not None:
            required = {
                "research_map_id",
                "research_map_version",
                "research_map_hash",
                "open_obligation_ids",
                "root_claim_snapshot_hash",
            }
            if not required <= set(research_frontier):
                raise ProjectError("Campaign research frontier projection is incomplete")
            for key in required:
                if key != "root_claim_snapshot_hash":
                    record[key] = copy.deepcopy(research_frontier[key])
            for key in (
                "architecture_review_clock_id",
                "architecture_review_clock_revision",
                "architecture_review_clock_hash",
                "architecture_review_due",
                "architecture_review_triggers",
                "last_architecture_review_id",
                "last_architecture_review_hash",
                "active_structural_probe_id",
                "pending_architecture_patch_id",
                "governance_checkpoint_classification",
            ):
                if key in research_frontier:
                    record[key] = copy.deepcopy(research_frontier[key])
        record["runtime_state_updated_at"] = utc_now()
        return self._save(record)

    def resume(self, campaign_id: str) -> dict:
        record = self.load(campaign_id)
        if record["status"] in {
            "COMPLETE_PROVED_REPLAY",
            "MATHEMATICAL_EXHAUSTION",
        }:
            raise ProjectError("Terminal campaign cannot be resumed")
        snapshot_hash = record.get("root_claim_snapshot_hash")
        if not snapshot_hash:
            raise ProjectError(
                "Campaign has no ClaimSnapshot; checkpoint migration/revalidation is required"
            )
        self.truth_store.validate_snapshot_for_execution(
            str(snapshot_hash),
            canonical_authority=record.get("truth_canonical_authority") or [],
            replay_policy_hash=record.get("truth_replay_policy_hash"),
        )
        research_map_id = record.get("research_map_id")
        if not research_map_id:
            raise ProjectError(
                "REVALIDATION_REQUIRED: legacy campaign has no canonical ResearchMap"
            )
        research_store = ResearchStoreFacade(
            self.project,
            truth_store=self.truth_store,
            root_validation_kwargs={
                "canonical_authority": record.get("truth_canonical_authority") or [],
                "replay_policy_hash": record.get("truth_replay_policy_hash"),
            },
        )
        current_map = research_store.load_current_map(str(research_map_id))
        if current_map.research_map_hash != record.get("research_map_hash"):
            raise ProjectError("Campaign ResearchMap projection is stale or incomplete")
        governance = GovernanceController(self.project, research_store=research_store)
        classification = governance.classify_legacy_checkpoint(record)
        if classification == "GOVERNANCE_REVIEW_REQUIRED":
            governance.ensure_clock(str(research_map_id))
            governance.signal_review(str(research_map_id), "HUMAN_REQUEST")
        else:
            clock = governance.ensure_clock(str(research_map_id))
            if record.get("architecture_review_clock_hash") != clock.clock_hash:
                raise ProjectError("Campaign governance checkpoint is stale or incomplete")
        record["governance_checkpoint_classification"] = classification
        record.update(governance.checkpoint_projection(str(research_map_id)))
        record["status"] = "RUNNING"
        record["resumed_at"] = utc_now()
        StopController(self.project, campaign_id).clear_for_resume()
        return self._save(record)

    @staticmethod
    def _run_record(run_id: str, parent_run_id: str | None, repair_cycle: int, kind: str) -> dict:
        return {
            "run_id": run_id,
            "parent_run_id": parent_run_id,
            "repair_cycle": repair_cycle,
            "kind": kind,
            "status": "PLANNED",
            "phase": "CREATED",
            "created_at": utc_now(),
        }

    @staticmethod
    def _research_binding(record: dict) -> dict:
        binding = {
            "research_map_id": record.get("research_map_id"),
            "research_map_version": record.get("research_map_version"),
            "research_map_hash": record.get("research_map_hash"),
            "open_obligation_ids": copy.deepcopy(record.get("open_obligation_ids", [])),
            "root_claim_snapshot_hash": record.get("root_claim_snapshot_hash"),
            "semantic_role": "EXECUTION_LINEAGE_ONLY",
        }
        for key in (
            "architecture_review_clock_id",
            "architecture_review_clock_revision",
            "architecture_review_clock_hash",
            "architecture_review_due",
            "architecture_review_triggers",
            "last_architecture_review_id",
            "last_architecture_review_hash",
            "active_structural_probe_id",
            "pending_architecture_patch_id",
        ):
            if key in record:
                binding[key] = copy.deepcopy(record[key])
        return binding

    def _successor_id(self, target_id: str, cycle: int) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        base = f"{target_id}-repair-{cycle}-{stamp}"
        existing = {path.name for path in (self.project.root / "runs").glob(f"{base}*")}
        candidate = base
        serial = 1
        while candidate in existing:
            serial += 1
            candidate = f"{base}-{serial}"
        return candidate


class CampaignEngine:
    """Run bounded research/repair successors without reopening old runs."""

    def __init__(
        self,
        project: ProjectStore,
        *,
        config_path: str | Path,
        worker_count: int,
        orchestrator_factory: Callable[..., Any] | None = None,
    ):
        self.project = project
        self.config_path = Path(config_path)
        self.worker_count = worker_count
        self.store = CampaignStore(project)
        self.orchestrator_factory = orchestrator_factory

    def run(self, campaign_id: str, *, stop_after_checkpoint: bool = False) -> dict:
        campaign = self.store.load(campaign_id)
        if campaign["status"] != "RUNNING":
            return campaign
        if not campaign["runs"]:
            run_id = self._initial_run_id(campaign["target_id"])
            self.store.register_initial(campaign_id, run_id)
            campaign = self.store.load(campaign_id)
        current = campaign["runs"][-1]
        while True:
            stop_controller = StopController(self.project, campaign_id)
            if stop_controller.requested():
                self.store.mark_run(
                    campaign_id,
                    current["run_id"],
                    status="STOPPED_AT_CHECKPOINT",
                    phase="CHECKPOINT",
                )
                stop_controller.acknowledge(
                    run_id=current["run_id"],
                    checkpoint="before_new_worker",
                )
                return self.store.checkpoint(campaign_id, "STOPPED_AT_CHECKPOINT")
            orchestrator = self._make_orchestrator(campaign, current)
            try:
                state = orchestrator.run()
                scheduler = getattr(orchestrator, "pipeline_scheduler", None)
                router = getattr(orchestrator, "model_router", None)
                self.store.update_runtime_state(
                    campaign_id,
                    pipeline_state=(scheduler.snapshot() if scheduler is not None else None),
                    routing_state=(router.snapshot() if router is not None else None),
                    run_id=current["run_id"],
                    claim_snapshot_hash=state.get("claim_snapshot_hash"),
                    canonical_authority=state.get("canonical_authority"),
                    research_frontier=(
                        {
                            key: state.get(key)
                            for key in (
                                "research_map_id",
                                "research_map_version",
                                "research_map_hash",
                                "open_obligation_ids",
                                "root_claim_snapshot_hash",
                                "architecture_review_clock_id",
                                "architecture_review_clock_revision",
                                "architecture_review_clock_hash",
                                "architecture_review_due",
                                "architecture_review_triggers",
                                "last_architecture_review_id",
                                "last_architecture_review_hash",
                                "active_structural_probe_id",
                                "pending_architecture_patch_id",
                                "governance_checkpoint_classification",
                            )
                        }
                        if state.get("research_map_id")
                        else None
                    ),
                )
            finally:
                close = getattr(orchestrator, "close", None)
                if callable(close):
                    close()
            status = str(state.get("status", "HUMAN_REQUIRED"))
            phase = str(state.get("phase", "COMPLETE"))
            self.store.mark_run(campaign_id, current["run_id"], status=status, phase=phase)
            if phase == "CHECKPOINT":
                if (
                    stop_after_checkpoint
                    or status.startswith("BLOCKED_AUTHORITY_")
                    or status
                    in {
                        "BLOCKED_PROVIDER_QUOTA",
                        "BLOCKED_INFRASTRUCTURE",
                        "TIME_BUDGET_EXHAUSTED",
                        "STOPPED_AT_CHECKPOINT",
                    }
                ):
                    if status == "STOPPED_AT_CHECKPOINT":
                        stop_controller.acknowledge(
                            run_id=current["run_id"],
                            checkpoint=str(state.get("checkpoint_reason", status)),
                        )
                    return self.store.checkpoint(campaign_id, status)
            if status in {"PROVED", "COMPLETE_PROVED_REPLAY"}:
                return self.store.finish(campaign_id, "COMPLETE_PROVED_REPLAY")
            campaign = self.store.load(campaign_id)
            can_repair = (
                status == "REJECTED"
                and campaign.get("auto_successor", False)
                and int(campaign["repair_cycles_used"]) < int(campaign["max_repair_cycles"])
            )
            if not can_repair:
                final = "MATHEMATICAL_EXHAUSTION" if status == "REJECTED" else "HUMAN_REQUIRED"
                return self.store.finish(campaign_id, final)
            current = self.store.create_successor(campaign_id, parent_run_id=current["run_id"])
            campaign = self.store.load(campaign_id)
            self._prepare_dependency_repair(campaign, current)

    def _make_orchestrator(self, campaign: dict, run: dict) -> Any:
        if self.orchestrator_factory is None:
            from .orchestrator import ResearchOrchestrator

            factory = ResearchOrchestrator
        else:
            factory = self.orchestrator_factory
        policy = (
            ReplayPolicy.from_dict(campaign["replay_policy"])
            if campaign.get("replay_policy")
            else None
        )
        return factory(
            self.project,
            campaign["target_id"],
            config_path=self.config_path,
            worker_count=self.worker_count,
            run_id=run["run_id"],
            campaign_id=campaign["campaign_id"],
            parent_run_id=run.get("parent_run_id"),
            repair_cycle=run.get("repair_cycle", 0),
            hard_submit_gate=bool(campaign.get("hard_blocker", False)),
            replay_policy=policy,
            infrastructure_retries=int(campaign.get("infrastructure_retries", 0)),
            budget_limit_seconds=campaign.get("budget_seconds"),
            initial_worker_count=campaign.get("initial_workers"),
            role_scheduling=(
                int(campaign.get("max_workers") or self.worker_count)
                > int(campaign.get("initial_workers") or self.worker_count)
            ),
            secondary_verification=bool(campaign.get("secondary_verification", False)),
            stop_controller=StopController(self.project, campaign["campaign_id"]),
            campaign_routing_override=campaign.get("routing_override") or {},
            pipeline_state=(
                campaign.get("pipeline_state")
                if isinstance(campaign.get("pipeline_state"), dict)
                and "queues" in campaign.get("pipeline_state", {})
                else None
            ),
            research_map_id=campaign.get("research_map_id"),
        )

    def _initial_run_id(self, target_id: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{target_id}-campaign-{stamp}"

    def _prepare_dependency_repair(self, campaign: dict, child: dict) -> None:
        """Materialize only catalogued, manifest-approved missing authorities."""

        if not campaign.get("auto_dependency_repair"):
            return
        policy_data = campaign.get("replay_policy")
        catalog = campaign.get("dependency_repair_catalog") or {}
        source_root = campaign.get("dependency_repair_source_root")
        parent_run_id = child.get("parent_run_id")
        if not policy_data or not catalog or not source_root or not parent_run_id:
            return
        failure_path = self.project.root / "runs" / parent_run_id / "FAILURE_MAP.json"
        if not failure_path.exists():
            return
        failure_map = json.loads(failure_path.read_text(encoding="utf-8"))
        missing_text = "\n".join(
            str(item.get("exact_rejected_claim", ""))
            for item in failure_map.get("items", [])
            if item.get("category")
            in {
                "DEPENDENCY_GAP",
                "SEMANTIC_GAP",
                "FOUNDATION_GAP",
            }
        )
        policy = ReplayPolicy.from_dict(policy_data)
        child_dir = self.project.root / "runs" / child["run_id"]
        child_dir.mkdir(parents=True, exist_ok=True)
        materialized = []
        rejected = []
        for authority_id, raw_record in sorted(catalog.items()):
            if authority_id not in missing_text:
                continue
            record = dict(raw_record)
            record.setdefault("authority_id", authority_id)
            allowed, errors = policy.authorize_dependency_repair(record)
            if not allowed:
                rejected.append({"authority_id": authority_id, "errors": errors})
                continue
            destination = policy.materialize_dependency(
                record,
                source_root=source_root,
                destination_root=child_dir / "inherited_sources",
            )
            materialized.append(
                {
                    "authority_id": authority_id,
                    "authority_type": record["authority_type"],
                    "source_file": record["source_file"],
                    "materialized_path": destination.relative_to(child_dir).as_posix(),
                    "policy_hash": policy.policy_hash,
                }
            )
        _write_json(
            child_dir / "dependency_repair.json",
            {
                "schema_version": 1,
                "parent_run_id": parent_run_id,
                "materialized_authorities": materialized,
                "rejected_authorities": rejected,
                "leak_audit_pass": not rejected,
                "created_at": utc_now(),
            },
        )
