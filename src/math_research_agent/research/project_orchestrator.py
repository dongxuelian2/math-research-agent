"""Project-level supervisor for purpose -> subproblem -> proof orchestration.

The project purpose is the user-owned research objective.  A planner may
propose child subproblems, but only the validated structured plan is allowed
to create theorem records or schedule downstream runs.  This keeps provider
prose outside the project control plane.
"""

from __future__ import annotations

import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .file_ingestion import ProjectFileIngestor
from .project import ProjectError, ProjectStore
from .providers import create_client, load_model_config
from .routing import ModelRouter, RoutedLLMClient
from .schemas import ProjectPlanSchema, ProjectSubproblemSchema, parse_structured_response
from .ui_events import UiEventEmitter


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _slug(value: str) -> str:
    return "-".join(value.casefold().split())[:80] or "project"


class ProjectOrchestrator:
    """Own the project-level plan and hand off validated child work."""

    def __init__(
        self,
        project: ProjectStore,
        *,
        config_path: str | Path,
        worker_count: int = 3,
        max_subproblems: int = 6,
        event_sink: UiEventEmitter | None = None,
    ):
        if worker_count < 1:
            raise ProjectError("worker_count must be positive")
        if max_subproblems < 1 or max_subproblems > 12:
            raise ProjectError("max_subproblems must be between 1 and 12")
        self.project = project
        self.config_path = Path(config_path).resolve()
        self.worker_count = worker_count
        self.max_subproblems = max_subproblems
        self.events = event_sink or UiEventEmitter(
            project_id=str(project.load_project().get("id") or project.root.name),
            project_root=project.root,
        )

    def run(self, *, plan_only: bool = False) -> dict[str, Any]:
        metadata = self.project.load_project()
        purpose = str(metadata.get("purpose") or metadata.get("description") or "").strip()
        if not purpose:
            raise ProjectError("Project purpose is required before orchestration")

        reusable = self._reusable_plan(metadata)
        if reusable is not None:
            return self._resume_project_plan(
                metadata=metadata,
                purpose=purpose,
                plan_path=reusable,
                plan_only=plan_only,
            )

        run_name = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        run_dir = self.project.root / "runs" / "orchestrator" / run_name
        run_dir.mkdir(parents=True, exist_ok=False)
        self._prepare_imports(run_name)
        planning_event = self.events.start(
            action="plan_project",
            title="正在分析研究目标",
            summary="Planner 正在把核心目标拆分为可验证的子命题。",
            role="planner",
            stage="PLANNING",
            run_id=run_name,
        )
        metadata = self.project.load_project()
        metadata["orchestrator"] = {
            "status": "RUNNING",
            "purpose": purpose,
            "plan_run": run_name,
            "plan_file": str((run_dir / "project_plan.json").relative_to(self.project.root)),
            "phase": "PLANNING",
        }
        validation_event: str | None = None

        try:
            self.project.save_project(metadata)
            plan = self._plan(purpose, run_dir)
            self.events.update(
                planning_event,
                summary=f"已生成 {len(plan.subproblems)} 个候选子命题。",
            )
            if len(plan.subproblems) > self.max_subproblems:
                raise ProjectError(
                    f"Planner proposed {len(plan.subproblems)} subproblems; "
                    f"limit is {self.max_subproblems}"
                )
            validation_event = self.events.start(
                action="validate_project_plan",
                title="正在校验子命题计划",
                summary="检查标题、依赖关系和可验证边界。",
                role="planner",
                stage="PLANNING",
                run_id=run_name,
            )
            ordered = self._ordered(plan.subproblems)
            plan_path = run_dir / "project_plan.json"
            _write_json(plan_path, plan.model_dump(mode="json"))
            self.events.finish(
                validation_event,
                success=True,
                summary=f"计划校验通过，共 {len(ordered)} 个子命题。",
                artifacts=[str(plan_path.relative_to(self.project.root))],
                run_id=run_name,
            )
            validation_event = None
            created = self._materialize(ordered, plan_path, run_name)
            self.events.finish(
                planning_event,
                success=True,
                summary=f"规划完成，已校验 {len(ordered)} 个子命题。",
                artifacts=[str(plan_path.relative_to(self.project.root))],
            )
        except Exception as exc:
            if validation_event is not None:
                self.events.finish(
                    validation_event,
                    success=False,
                    summary="子命题计划校验失败。",
                    error={"message": str(exc)[:500]},
                    run_id=run_name,
                )
            self.events.finish(
                planning_event,
                success=False,
                summary="研究目标规划失败。",
                error={"message": str(exc)[:500]},
            )
            self.events.error(
                exc,
                action="plan_project",
                title="研究规划失败",
                stage="PLANNING",
                run_id=run_name,
                diagnostic_path=run_dir / "diagnostics.log",
            )
            # The stage already emitted a terminal UI error.  Let the CLI
            # avoid emitting a second generic "研究运行失败" event for the
            # same exception.
            setattr(exc, "_ui_event_emitted", True)
            failed = self.project.load_project()
            failed.setdefault("orchestrator", {}).update(
                {
                    "status": "BLOCKED_INFRASTRUCTURE",
                    "phase": "PLANNING",
                    "error": str(exc),
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self.project.save_project(failed)
            raise

        metadata = self.project.load_project()
        metadata["orchestrator"] = {
            "status": "PLANNED" if plan_only else "RUNNING",
            "purpose": purpose,
            "plan_run": run_name,
            "plan_file": str(plan_path.relative_to(self.project.root)),
            "analysis_summary": plan.analysis_summary,
            "open_questions": list(plan.open_questions),
            "subproblem_ids": [item.id for item in ordered],
            "created_subproblem_ids": created,
        }
        project_title = self._project_title(plan.project_title, metadata, purpose)
        metadata["display_title"] = project_title
        metadata["orchestrator"]["project_title"] = project_title
        self.project.save_project(metadata)
        if plan_only:
            return {
                "status": "PLANNED",
                "purpose": purpose,
                "project_title": project_title,
                "plan_run": run_name,
                "subproblem_ids": [item.id for item in ordered],
                "open_questions": list(plan.open_questions),
            }
        result = self._run_children(ordered, run_name, purpose)
        return self._summarize(result, run_name)

    def _reusable_plan(self, metadata: dict[str, Any]) -> Path | None:
        """Return an unfinished validated plan that `/run` should continue."""

        orchestrator = metadata.get("orchestrator")
        if not isinstance(orchestrator, dict) or orchestrator.get("status") not in {
            "PLANNED",
            "RUNNING",
            "PARTIAL",
        }:
            return None
        # A newly added file must be visible to the project Planner before we
        # reuse the old decomposition. Child checkpoints are still eligible
        # for resumption after the new plan is materialized.
        manifest = ProjectFileIngestor(self.project).load_manifest()
        if any(record.get("status") == "PENDING" for record in manifest.get("files", [])):
            return None
        value = str(orchestrator.get("plan_file") or "").strip()
        if not value:
            return None
        root = self.project.root.resolve()
        candidate = (root / value).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        try:
            ProjectPlanSchema.model_validate(json.loads(candidate.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return None
        return candidate

    def _prepare_imports(self, run_name: str) -> list[dict[str, Any]]:
        ingestor = ProjectFileIngestor(self.project)
        manifest = ingestor.load_manifest()
        pending = [
            record
            for record in manifest.get("files", [])
            if record.get("status") != "READY" or not ingestor.is_materialized(record)
        ]
        if not pending:
            return []
        event = self.events.start(
            action="ingest_project_files",
            title="正在整理项目文件",
            summary="提取 inbox 中的论文、Markdown、文本或 PDF，并生成项目工作文件。",
            role="system",
            stage="INGESTION",
            run_id=run_name,
        )
        try:
            records = ingestor.prepare_pending()
        except Exception as exc:
            self.events.finish(
                event,
                success=False,
                summary="项目文件整理失败。",
                error={"message": str(exc)[:500]},
                run_id=run_name,
            )
            raise
        ready = sum(record.get("status") == "READY" for record in records)
        errors = sum(record.get("status") == "ERROR" for record in records)
        self.events.finish(
            event,
            success=errors == 0,
            summary=(
                f"已处理 {len(records)} 个项目文件：{ready} 个可用，{errors} 个需要人工检查。"
                if records
                else "没有待处理的项目文件。"
            ),
            artifacts=[
                str(record.get("work_path"))
                for record in records
                if record.get("status") == "READY" and record.get("work_path")
            ],
            run_id=run_name,
        )
        return records

    def _resume_project_plan(
        self,
        *,
        metadata: dict[str, Any],
        purpose: str,
        plan_path: Path,
        plan_only: bool,
    ) -> dict[str, Any]:
        """Continue an unfinished project plan and its child checkpoints."""

        plan = ProjectPlanSchema.model_validate(json.loads(plan_path.read_text(encoding="utf-8")))
        ordered = self._ordered(plan.subproblems)
        run_name = str(metadata.get("orchestrator", {}).get("plan_run") or plan_path.parent.name)
        self._prepare_imports(run_name)
        resume_event = self.events.start(
            action="resume_project",
            title="正在恢复项目进度",
            summary="沿用之前的子命题计划和运行检查点，继续未完成的研究。",
            role="system",
            stage="RESUME",
            run_id=run_name,
        )
        try:
            created = self._materialize(ordered, plan_path, run_name)
            self.events.finish(
                resume_event,
                success=True,
                summary=f"已恢复 {len(ordered)} 个子命题，其中 {len(created)} 个是新登记项。",
                artifacts=[str(plan_path.relative_to(self.project.root))],
                run_id=run_name,
            )
        except Exception as exc:
            self.events.finish(
                resume_event,
                success=False,
                summary="项目进度恢复失败。",
                error={"message": str(exc)[:500]},
                run_id=run_name,
            )
            raise

        metadata = self.project.load_project()
        project_title = self._project_title(plan.project_title, metadata, purpose)
        metadata["display_title"] = project_title
        metadata["orchestrator"] = {
            **metadata.get("orchestrator", {}),
            "status": "PLANNED" if plan_only else "RUNNING",
            "purpose": purpose,
            "plan_run": run_name,
            "plan_file": str(plan_path.relative_to(self.project.root)),
            "analysis_summary": plan.analysis_summary,
            "open_questions": list(plan.open_questions),
            "subproblem_ids": [item.id for item in ordered],
            "created_subproblem_ids": list(
                metadata.get("orchestrator", {}).get("created_subproblem_ids", [])
            )
            + created,
            "project_title": project_title,
        }
        self.project.save_project(metadata)
        if plan_only:
            return {
                "status": "PLANNED",
                "purpose": purpose,
                "project_title": project_title,
                "plan_run": run_name,
                "subproblem_ids": [item.id for item in ordered],
                "open_questions": list(plan.open_questions),
            }
        result = self._run_children(ordered, run_name, purpose)
        return self._summarize(result, run_name)

    def _summarize(self, result: dict[str, Any], run_name: str) -> dict[str, Any]:
        summary_event = self.events.start(
            action="summarize_project",
            title="项目研究已汇总",
            summary=(
                "所有子命题均已通过。"
                if result["status"] == "COMPLETE"
                else "项目尚未完全闭合，部分子命题仍需继续研究。"
            ),
            role="system",
            stage="SUMMARY",
            run_id=run_name,
        )
        self.events.finish(
            summary_event,
            success=result["status"] == "COMPLETE",
            summary=(
                "所有子命题均已通过。"
                if result["status"] == "COMPLETE"
                else "项目尚未完全闭合，部分子命题仍需继续研究。"
            ),
            run_id=run_name,
        )
        return result

    def _plan(self, purpose: str, run_dir: Path) -> ProjectPlanSchema:
        existing = [
            {
                "id": theorem.get("id"),
                "title": theorem.get("title"),
                "statement": theorem.get("statement"),
                "status": theorem.get("status"),
            }
            for theorem in self.project.list_theorems()
        ]
        imported_sources = ProjectFileIngestor(self.project).ready_sources(max_chars=16000)
        prompt = json.dumps(
            {
                "project_purpose": purpose,
                "existing_subproblems": existing,
                "imported_work_files": [
                    {
                        "id": item["id"],
                        "title": item.get("title", ""),
                        "original_name": item.get("original_name", ""),
                        "source_file": item.get("source_file", ""),
                        "analysis": item.get("analysis", {}),
                        "content": item.get("content", ""),
                    }
                    for item in imported_sources
                ],
                "constraints": {
                    "max_subproblems": self.max_subproblems,
                    "must_be_dependency_acyclic": True,
                    "must_be_individually_verifiable": True,
                    "must_not_claim_proved": True,
                },
                "required_output": {
                    "schema_version": 2,
                    "project_title": "不超过 32 个字符的简短中文标题",
                    "analysis_summary": "...",
                    "subproblems": [
                        {
                            "id": "stable-id",
                            "title": "short title",
                            "statement": "exact child proposition",
                            "dependencies": [],
                            "tags": [],
                            "branch": "main",
                            "proof_type": "NATURAL_LANGUAGE",
                            "claim_type": "implication",
                        }
                    ],
                    "open_questions": [],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        (run_dir / "planner_request.json").write_text(prompt + "\n", encoding="utf-8")
        config = load_model_config(self.config_path)
        config["workspace_root"] = str(self.project.root.resolve())
        router = ModelRouter(
            config,
            state_path=run_dir / "routing_state.json",
            runtime_scope=run_dir.name,
            tool_event_sink=self.events.tool_event,
        )
        client = RoutedLLMClient(
            router,
            client_factory=create_client,
            default_role="planner",
            archive_dir=run_dir / "archive",
            working_dir=run_dir / "working",
        )
        response = client.call(
            prompt,
            system_prompt=(
                "You are the project supervisor. Decompose the project purpose into a small "
                "acyclic set of exact child propositions. Return one complete JSON document "
                "matching ProjectPlanSchema. Include a short project_title (no more than 32 "
                "characters) and do not claim any child is proved. For each subproblem, "
                "claim_type must be exactly one of: implication, iff, classification, "
                "equality, existence, or unclassified."
            ),
            label="project_plan",
            response_schema=ProjectPlanSchema,
            archive_path=run_dir / "archive" / "project_plan.md",
        )
        plan = parse_structured_response(response, ProjectPlanSchema)
        _write_json(run_dir / "project_plan_response.json", response)
        return plan

    @staticmethod
    def _project_title(candidate: str, metadata: dict[str, Any], purpose: str) -> str:
        value = " ".join(str(candidate or "").split()).strip()
        if not value:
            value = " ".join(
                str(metadata.get("display_title") or metadata.get("name") or "").split()
            )
        if not value:
            value = " ".join(purpose.split())
        return value[:32].rstrip() or "数学研究项目"

    def _ordered(self, items: list[ProjectSubproblemSchema]) -> list[ProjectSubproblemSchema]:
        by_id = {item.id: item for item in items}
        if len(by_id) != len(items):
            raise ProjectError("Project planner returned duplicate subproblem ids")
        known = {item.get("id") for item in self.project.list_theorems()}
        state: dict[str, int] = {}
        ordered: list[ProjectSubproblemSchema] = []

        def visit(item_id: str) -> None:
            mark = state.get(item_id, 0)
            if mark == 1:
                raise ProjectError(f"Project planner returned a dependency cycle at {item_id}")
            if mark == 2:
                return
            item = by_id[item_id]
            state[item_id] = 1
            for dependency in item.dependencies:
                if dependency in by_id:
                    visit(dependency)
                elif dependency not in known:
                    raise ProjectError(
                        f"Project planner returned unknown dependency: {item_id} -> {dependency}"
                    )
            state[item_id] = 2
            ordered.append(item)

        for item in items:
            ProjectStore.validate_id(item.id)
            if not item.statement.strip() or not item.title.strip():
                raise ProjectError(f"Project planner returned empty subproblem: {item.id}")
            visit(item.id)
        return ordered

    def _materialize(
        self,
        items: list[ProjectSubproblemSchema],
        plan_path: Path,
        run_name: str,
    ) -> list[str]:
        created: list[str] = []
        existing = {item.get("id"): item for item in self.project.list_theorems()}
        source_file = str(plan_path.relative_to(self.project.root))
        for item in items:
            prior = existing.get(item.id)
            if prior is not None:
                if str(prior.get("statement", "")).strip() != item.statement.strip():
                    raise ProjectError(f"Planner attempted to redefine subproblem: {item.id}")
                continue
            self.project.add_theorem(
                item.id,
                item.title,
                item.statement,
                status="OPEN",
                source_file=source_file,
                dependencies=list(item.dependencies),
                tags=list(item.tags),
                branch=item.branch,
                proof_type=item.proof_type,
                claim_type=item.claim_type,
            )
            created.append(item.id)
            materialized = self.events.start(
                action="materialize_subproblem",
                title=f"已登记子命题：{item.title}",
                summary="结构化计划已通过校验，子命题已写入项目。",
                role="planner",
                stage="PLANNING",
                theorem_id=item.id,
                run_id=run_name,
            )
            self.events.finish(
                materialized,
                success=True,
                summary="结构化计划已通过校验，子命题已写入项目。",
                theorem_id=item.id,
                run_id=run_name,
            )
        if items:
            self.project.set_current_target(items[0].id)
        return created

    def _run_child(
        self,
        item: ProjectSubproblemSchema,
        run_name: str,
        previous: tuple[Path, dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Run one child inside a context-local parent-event binding."""

        from .orchestrator import ResearchOrchestrator

        with self.events.parent_context(run_name):
            state = ResearchOrchestrator(
                self.project,
                item.id,
                config_path=self.config_path,
                worker_count=self.worker_count,
                resume=(previous[0] if previous else None),
                continue_partial=bool(previous),
                event_sink=self.events,
            ).run()
        return {
            "id": item.id,
            "status": state.get("status", "UNKNOWN"),
            "run_id": state.get("run_id", previous[0].name if previous else ""),
            "resumed": bool(previous),
        }

    def _run_children(
        self,
        items: list[ProjectSubproblemSchema],
        run_name: str,
        purpose: str,
    ) -> dict[str, Any]:
        """Run the ready frontier concurrently while respecting dependencies."""

        results_by_id: dict[str, dict[str, Any]] = {}
        pending: dict[str, tuple[ProjectSubproblemSchema, tuple[Path, dict[str, Any]] | None]] = {}
        succeeded: set[str] = set()
        blocked: set[str] = set()
        failed: set[str] = set()

        for item in items:
            theorem = self.project.load_theorem(item.id)
            previous = self._latest_child_checkpoint(item.id)
            previous_state = previous[1] if previous else {}
            if theorem.get("status") == "PROVED" or previous_state.get("status") == "PROVED":
                results_by_id[item.id] = {
                    "id": item.id,
                    "status": "PROVED",
                    "run_id": previous[0].name if previous else "",
                    "resumed": False,
                    "skipped": True,
                }
                succeeded.add(item.id)
            else:
                pending[item.id] = (item, previous)

        max_parallel = min(self.worker_count, len(pending)) or 1
        futures = {}
        planned_ids = {candidate.id for candidate in items}
        with ThreadPoolExecutor(
            max_workers=max_parallel,
            thread_name_prefix="math-subproblem",
        ) as executor:
            while pending or futures:
                # A failed child blocks only its transitive dependents. An
                # independent branch remains eligible for this same run.
                for item_id, (item, previous) in list(pending.items()):
                    dependencies = [
                        dependency for dependency in item.dependencies if dependency in planned_ids
                    ]
                    failed_dependencies = [
                        dependency
                        for dependency in dependencies
                        if dependency in failed or dependency in blocked
                    ]
                    if failed_dependencies:
                        results_by_id[item_id] = {
                            "id": item_id,
                            "status": "BLOCKED_DEPENDENCY",
                            "resumed": bool(previous),
                            "blocked_by": failed_dependencies,
                        }
                        blocked.add(item_id)
                        pending.pop(item_id)

                available = max_parallel - len(futures)
                if available > 0:
                    for item in items:
                        if available <= 0 or item.id not in pending:
                            continue
                        dependencies = [
                            dependency
                            for dependency in item.dependencies
                            if dependency in planned_ids
                        ]
                        if not all(dependency in succeeded for dependency in dependencies):
                            continue
                        previous = pending[item.id][1]
                        activity = self.events.start(
                            action="prove_subproblem",
                            title=f"正在研究：{item.title}",
                            summary=(
                                "正在恢复上次检查点中的证明 Worker 和审计流程。"
                                if previous
                                else "已调度证明 Worker 和审计流程。"
                            ),
                            role="worker",
                            stage="PROOF",
                            theorem_id=item.id,
                            run_id=run_name,
                        )
                        future = executor.submit(self._run_child, item, run_name, previous)
                        futures[future] = (item, activity, previous)
                        pending.pop(item.id)
                        available -= 1

                if not futures:
                    # _ordered() rejects cycles; this is a defensive fallback
                    # for a plan that references a dependency outside its
                    # materialized item set.
                    for item_id, (item, previous) in list(pending.items()):
                        results_by_id[item_id] = {
                            "id": item_id,
                            "status": "BLOCKED_DEPENDENCY",
                            "resumed": bool(previous),
                            "blocked_by": list(item.dependencies),
                        }
                        blocked.add(item_id)
                        pending.pop(item_id)
                    continue

                completed, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in completed:
                    item, activity, previous = futures.pop(future)
                    try:
                        result = future.result()
                        results_by_id[item.id] = result
                        if result["status"] == "PROVED":
                            succeeded.add(item.id)
                        else:
                            failed.add(item.id)
                        self.events.finish(
                            activity,
                            success=result["status"] == "PROVED",
                            summary=(
                                "子命题已通过证明与审计。"
                                if result["status"] == "PROVED"
                                else f"子命题结束于 {result['status']}。"
                            ),
                            theorem_id=item.id,
                            run_id=run_name,
                        )
                    except Exception as exc:  # child failures stay durable and visible
                        result = {
                            "id": item.id,
                            "status": "ERROR",
                            "error": str(exc),
                            "resumed": bool(previous),
                        }
                        results_by_id[item.id] = result
                        failed.add(item.id)
                        self.events.finish(
                            activity,
                            success=False,
                            summary="子命题运行失败。",
                            error={"message": str(exc)[:500]},
                            theorem_id=item.id,
                            run_id=run_name,
                        )
                        self.events.error(
                            exc,
                            action="prove_subproblem",
                            title=f"子命题失败：{item.title}",
                            stage="PROOF",
                            theorem_id=item.id,
                            run_id=run_name,
                            diagnostic_path=self.project.root
                            / "runs"
                            / "orchestrator"
                            / run_name
                            / f"{item.id}.diagnostics.log",
                        )

        results = [results_by_id[item.id] for item in items if item.id in results_by_id]
        status = (
            "COMPLETE"
            if results and all(item["status"] == "PROVED" for item in results)
            else "PARTIAL"
        )
        metadata = self.project.load_project()
        metadata["orchestrator"].update(
            {
                "status": status,
                "purpose": purpose,
                "plan_run": run_name,
                "children": results,
            }
        )
        persist_event = self.events.start(
            action="persist_project_result",
            title="正在保存项目结果",
            summary="写入子命题状态、运行摘要和可追溯引用。",
            role="system",
            stage="SUMMARY",
            run_id=run_name,
        )
        self.project.save_project(metadata)
        self.events.finish(
            persist_event,
            success=True,
            summary="项目结果已保存。",
            artifacts=["project.json"],
            run_id=run_name,
        )
        return {"status": status, "purpose": purpose, "plan_run": run_name, "children": results}

    def _latest_child_checkpoint(self, theorem_id: str) -> tuple[Path, dict[str, Any]] | None:
        candidates = []
        for state_path in (self.project.root / "runs").glob(f"{theorem_id}-*/state.json"):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if state.get("target_id") != theorem_id:
                continue
            candidates.append((state_path.parent, state))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)
        return candidates[0]
