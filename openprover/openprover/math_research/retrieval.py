"""Dependency-aware minimal context retrieval."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .canonical_artifacts import canonical_context_markdown
from .project import ProjectStore, utc_now
from .trust_kernel import TrustKernel
from .truth_identity import prompt_projection_hash


@dataclass(slots=True)
class ContextPackage:
    data: dict
    markdown: str


class ContextBuilder:
    """Build the smallest sufficient context for one target theorem."""

    def __init__(self, project: ProjectStore):
        self.project = project

    def _dependency_closure(self, target_id: str) -> tuple[list[str], list[str], list[list[str]]]:
        order: list[str] = []
        premise_order: list[str] = []
        cycles: list[list[str]] = []
        visited: set[str] = set()
        visited_premises: set[str] = set()
        active: list[str] = []

        def visit(theorem_id: str) -> None:
            resolved = self.project.resolve_dependency(theorem_id)
            if resolved["kind"] == "PREMISE":
                if theorem_id not in visited_premises:
                    visited_premises.add(theorem_id)
                    premise_order.append(theorem_id)
                return
            if theorem_id in active:
                start = active.index(theorem_id)
                cycles.append(active[start:] + [theorem_id])
                return
            if theorem_id in visited:
                return
            active.append(theorem_id)
            theorem = resolved["record"]
            for dependency in theorem.get("dependencies", []):
                visit(dependency)
            active.pop()
            visited.add(theorem_id)
            if theorem_id != target_id:
                order.append(theorem_id)

        visit(target_id)
        return order, premise_order, cycles

    def build(
        self,
        target_id: str,
        *,
        expand: bool = False,
        canonical_authority: list[dict] | None = None,
        claim_snapshot: dict | None = None,
    ) -> ContextPackage:
        target = self.project.load_theorem(target_id)
        dependency_ids, premise_ids, cycles = self._dependency_closure(target_id)
        dependencies = [self.project.load_theorem(item) for item in dependency_ids]
        premises = [self.project.load_premise(item) for item in premise_ids]
        allowed = [item for item in dependencies if item["status"] == "PROVED"]
        blocked = [item for item in dependencies if item["status"] != "PROVED"]
        direct_ids = set(target.get("dependencies", []))
        project_meta = self.project.load_project()
        notation_scope = str(
            target.get("notation_scope") or project_meta.get("notation_scope") or ""
        )
        trust_kernel = TrustKernel.for_project(self.project)
        trust_context = trust_kernel.context(notation_scope=notation_scope)
        steering_path = self.project.root / "steering" / "directives.json"
        steering = (
            json.loads(steering_path.read_text(encoding="utf-8")) if steering_path.exists() else {}
        )
        theorem_ids = set(dependency_ids + [target_id])
        tags = set(target.get("tags", []))
        for dependency in dependencies:
            tags.update(dependency.get("tags", []))
        failed_routes = self.project.relevant_failed_routes(theorem_ids, tags)

        sources = []
        for theorem in [target] + dependencies:
            source_file = theorem.get("source_file", "")
            source_path = self.project.safe_source_path(source_file)
            should_include = theorem["id"] == target_id or theorem["id"] in direct_ids or expand
            if source_path and source_path.is_file() and should_include:
                sources.append(
                    {
                        "theorem_id": theorem["id"],
                        "source_file": source_file,
                        "content": source_path.read_text(encoding="utf-8-sig", errors="replace"),
                    }
                )
        for premise in premises:
            source_file = premise.get("source_file", "")
            source_path = self.project.safe_source_path(source_file)
            if source_path and source_path.is_file():
                sources.append(
                    {
                        "theorem_id": premise["id"],
                        "source_file": source_file,
                        "content": source_path.read_text(encoding="utf-8-sig", errors="replace"),
                    }
                )
        semantic_registry = trust_context.get("semantic_registry") or {}
        semantic_sources: set[str] = set()
        for item in semantic_registry.get("items", []):
            provenance = item.get("provenance", {})
            source_file = provenance.get("source_file", "")
            if not source_file or source_file in semantic_sources:
                continue
            source_path = self.project.safe_source_path(source_file)
            if source_path and source_path.is_file():
                semantic_sources.add(source_file)
                sources.append(
                    {
                        "theorem_id": item["id"],
                        "source_file": source_file,
                        "source_hash": provenance.get("source_hash"),
                        "source_section": provenance.get("source_section"),
                        "authority_layer": "SEMANTIC",
                        "content": source_path.read_text(encoding="utf-8-sig", errors="replace"),
                    }
                )

        frozen = sorted(
            set(project_meta.get("frozen_branches", [])) | set(steering.get("freeze_branches", []))
        )
        prohibited = sorted(
            set(project_meta.get("prohibited_routes", []))
            | set(steering.get("prohibit_routes", []))
        )
        scope = steering.get("allowed_scope") or project_meta.get("allowed_scope", [])
        data = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "project_id": project_meta["id"],
            "target": target,
            "dependency_order": dependency_ids,
            "allowed_dependencies": allowed,
            "blocked_dependencies": blocked,
            "satisfied_premises": premises,
            "dependency_cycles": cycles,
            "failed_routes": failed_routes,
            "frozen_branches": frozen,
            "prohibited_routes": prohibited,
            "allowed_scope": scope,
            "notation_scope": notation_scope,
            "trust_kernel": trust_context,
            "added_lemmas": steering.get("added_lemmas", []),
            "sources": sources,
            "canonical_authority": list(canonical_authority or []),
            "claim_snapshot": dict(claim_snapshot or {}),
            "expanded": expand,
        }
        markdown = self._to_markdown(data)
        data["prompt_projection_hash"] = prompt_projection_hash(markdown)
        return ContextPackage(data=data, markdown=markdown)

    @staticmethod
    def _theorem_lines(items: list[dict]) -> str:
        if not items:
            return "- (none)"
        return "\n".join(
            f"- `{item['id']}` [{item['status']}]: {item['title']}\n"
            f"  Statement: {item.get('statement', '').strip()}"
            for item in items
        )

    @staticmethod
    def _premise_lines(items: list[dict]) -> str:
        if not items:
            return "- (none)"
        return "\n".join(
            f"- `{item['id']}` [{item['node_type']}, active]: {item['title']}\n"
            f"  Statement: {item.get('statement', '').strip()}"
            for item in items
        )

    @staticmethod
    def _foundation_lines(data: dict) -> str:
        registry = data["trust_kernel"]["foundation_registry"]
        items = registry.get("items", [])
        if not items:
            return "- (none)"
        return "\n".join(
            f"- `{item['id']}` (v{item['version']}, {item['content_hash']}): "
            f"{item['statement']}\n"
            f"  Conditions: {'; '.join(item.get('conditions', [])) or '(none)'}"
            for item in items
        )

    @staticmethod
    def _semantic_lines(data: dict) -> str:
        registry = data["trust_kernel"].get("semantic_registry")
        if not registry or not registry.get("items"):
            # Avoid blocker-like wording in this empty-registry message.  The
            # upstream prover still has a narrow whiteboard text guard,
            # and this text must not be mistaken for an unresolved blocker.
            return "- (none configured)"
        return "\n".join(
            f"- `{item['id']}` [{item['authority_kind']}, "
            f"scope `{item['notation_scope']}`, v{item['version']}]: "
            f"{item['statement']}\n"
            f"  Source: `{item['provenance']['source_file']}` / "
            f"{item['provenance']['source_section']} / "
            f"sha256:{item['provenance']['source_hash']}"
            for item in registry["items"]
        )

    def _to_markdown(self, data: dict) -> str:
        target = data["target"]
        failed = data["failed_routes"]
        failed_text = "- (none)"
        if failed:
            failed_text = "\n".join(
                f"- `{route['id']}` / {route['strategy']}\n"
                f"  - obtained: {route['obtained']}\n"
                f"  - exact failure: {route['failure_point']}\n"
                f"  - insufficient because: {route['insufficiency']}\n"
                f"  - recovery conditions: {route['recovery_conditions']}"
                for route in failed
            )
        sources = (
            "\n\n".join(
                f"### Source: `{source['source_file']}` ({source['theorem_id']})\n\n{source['content']}"
                for source in data["sources"]
            )
            or "(no source excerpts required)"
        )
        cycles = (
            "\n".join(f"- {' -> '.join(cycle)}" for cycle in data["dependency_cycles"])
            if data["dependency_cycles"]
            else "- (none)"
        )
        frozen = ", ".join(data["frozen_branches"]) or "(none)"
        prohibited = ", ".join(data["prohibited_routes"]) or "(none)"
        scope = ", ".join(data["allowed_scope"]) or "current target and its dependency slice only"
        canonical_authority = canonical_context_markdown(data.get("canonical_authority", []))
        return f"""# Math Research Context Package

## Scope

- Project: `{data["project_id"]}`
- Current target: `{target["id"]}`
- Allowed scope: {scope}
- Notation scope: `{data["notation_scope"] or "(none)"}`
- Expanded retrieval: `{str(data["expanded"]).lower()}`
- Treat OpenProver's output as a CANDIDATE only; it cannot self-promote to PROVED.

## Exact claim identity

- Claim snapshot: `{data.get("claim_snapshot", {}).get("claim_snapshot_hash", "UNBOUND")}`
- Assertion identity: `{data.get("claim_snapshot", {}).get("assertion_identity_hash", "UNBOUND")}`
- This prompt projection is not theorem identity or mathematical authority.

## Frozen branches

{frozen}

## Prohibited routes

{prohibited}

## Statement

### {target["title"]}

{target["statement"]}

Claim type: `{target.get("claim_type", "implication")}`

## Foundations

Registry `{data["trust_kernel"]["foundation_registry"]["id"]}` version
`{data["trust_kernel"]["foundation_registry"]["version"]}` / hash
`{data["trust_kernel"]["foundation_registry"]["hash"]}`.

{self._foundation_lines(data)}

## Semantics

{self._semantic_lines(data)}

## Project Theorems (PROVED only)

{self._theorem_lines(data["allowed_dependencies"])}

## Blocked dependencies (must not be used as theorems)

{self._theorem_lines(data["blocked_dependencies"])}

## Active project premises (satisfied roots, not PROVED theorems)

{self._premise_lines(data["satisfied_premises"])}

## Dependency cycles

{cycles}

## Relevant FAILED_ROUTE memory

{failed_text}

## Planner orchestration requirements

1. Inspect the failed-route memory before assigning work.
2. Assign genuinely different proof routes; do not duplicate a failed route unless a listed recovery condition is now satisfied and the reason is recorded.
3. Use at least three parallel workers when the configured worker budget permits it.
4. Include an adversarial counterexample or boundary-checking task before submission.
5. Keep COMPUTATIONAL_EVIDENCE separate from PROOF. A bounded search is never an infinite proof unless a finite reduction and reproducible certificate are supplied.
6. Classify every external claim as FOUNDATIONAL_THEOREM, SEMANTIC_DEFINITION, PROJECT_THEOREM, LOCAL_PROOF, or COMPUTATIONAL_CERTIFICATE and cite the exact authority ID where applicable.
7. Package metadata, filenames, index summaries, and generated manifest comments are locators only; they are never mathematical or semantic authority.
8. Write a complete proof to the OpenProver repository and submit it only as a candidate for the outer audit gate.

## Source excerpts

{sources}

## Canonical artifact authority

The JSON provenance below is the authority boundary. A filename, hash, summary,
manifest, or extract without a resolved body is not proof authority.

{canonical_authority}
"""

    @staticmethod
    def write(package: ContextPackage, directory: str | Path) -> tuple[Path, Path]:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        json_path = directory / "context.json"
        md_path = directory / "CONTEXT.md"
        json_path.write_text(
            json.dumps(package.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        md_path.write_text(package.markdown, encoding="utf-8")
        return json_path, md_path
