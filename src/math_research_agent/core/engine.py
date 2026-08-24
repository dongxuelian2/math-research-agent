"""Handwritten planner/worker engine for mathematical proof candidates.

This module is deliberately self-contained. It owns candidate generation and
durable artifacts; theorem truth remains
under the research layer's independent audit gate.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .budget import Budget, BudgetExceeded
from .protocol import ProtocolError, parse_actions, response_text
from .repository import KnowledgeRepository
from .scope import submission_blocker


class _Console:
    def log(self, message: str, **_: object) -> None:
        self.last_message = message


class ResearchEngine:
    """Run a bounded planner/worker/verifier session and emit a candidate."""

    def __init__(
        self,
        *,
        work_dir: Path,
        theorem_text: str,
        planner,
        worker,
        budget: Budget,
        max_workers: int = 1,
        policy=None,
        verifier: bool = True,
        resumed: bool = False,
    ):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.theorem_text = theorem_text
        self.planner = planner
        self.worker = worker
        self.budget = budget
        self.max_workers = max(1, int(max_workers))
        self.policy = policy
        self.verifier = verifier
        self.resumed = resumed
        self.repo = KnowledgeRepository(self.work_dir / "repo")
        self.tui = _Console()
        self.steps_dir = self.work_dir / "steps"
        self.steps_dir.mkdir(exist_ok=True)
        self.metrics: dict[str, object] = {}
        self._step = max(
            (
                int(path.name.removeprefix("step_"))
                for path in self.steps_dir.iterdir()
                if path.is_dir()
                and path.name.startswith("step_")
                and path.name.removeprefix("step_").isdigit()
            ),
            default=0,
        )
        self._lock = threading.Lock()

    def run(self) -> Path | None:
        whiteboard_path = self.work_dir / "WHITEBOARD.md"
        whiteboard = whiteboard_path.read_text(encoding="utf-8") if whiteboard_path.exists() else ""
        history: list[str] = []
        while not self.budget.exhausted():
            self._step += 1
            step_dir = self.steps_dir / f"step_{self._step:03d}"
            step_dir.mkdir(parents=True, exist_ok=True)
            prompt = self._planner_prompt(whiteboard, history)
            try:
                response = self._call(
                    self.planner,
                    prompt,
                    "You coordinate a rigorous mathematical research session. Return MRA_ACTION blocks only when an action is needed.",
                    f"planner_step_{self._step}",
                    step_dir / "planner_call.md",
                )
            except BudgetExceeded:
                break
            planner_text = response_text(response)
            (step_dir / "planner_result.md").write_text(planner_text + "\n", encoding="utf-8")
            try:
                actions = parse_actions(planner_text)
            except ProtocolError as exc:
                (step_dir / "protocol_error.json").write_text(
                    json.dumps({"error": str(exc)}, indent=2) + "\n", encoding="utf-8"
                )
                break
            if not actions:
                break
            spawned = False
            submitted: Path | None = None
            for action in actions:
                kind = action["action"]
                if kind == "spawn":
                    spawned = True
                    history.extend(self._run_workers(action, step_dir))
                elif kind == "write_items":
                    self._write_items(action)
                elif kind == "write_whiteboard":
                    whiteboard = str(action.get("whiteboard", ""))
                    whiteboard_path.write_text(whiteboard.rstrip() + "\n", encoding="utf-8")
                elif kind == "submit_proof":
                    submitted = self._submit(action, step_dir)
                    if submitted:
                        self.metrics["candidate"] = True
                        return submitted
            if not spawned and submitted is None:
                break
        self.metrics["candidate"] = False
        return None

    def _call(self, client, prompt: str, system: str, label: str, archive_path: Path) -> dict:
        self.budget.reserve_call()
        response = client.call(prompt, system, label=label, archive_path=archive_path)
        self.budget.record(response)
        return response

    def _planner_prompt(self, whiteboard: str, history: list[str]) -> str:
        return (
            "# Mathematical target\n\n"
            + self.theorem_text
            + "\n\n# Whiteboard\n\n"
            + (whiteboard or "(empty)")
            + "\n\n# Knowledge index\n\n"
            + self.repo.index()
            + "\n\n# Recent worker reports\n\n"
            + ("\n\n".join(history[-self.max_workers :]) or "(none)")
            + "\n\nPlan the next bounded action. Workers must report concrete mathematics; only submit a complete self-contained candidate proof.\n"
        )

    def _run_workers(self, plan: dict, step_dir: Path) -> list[str]:
        tasks = list(plan.get("tasks", []))
        if len(tasks) > self.max_workers:
            error = f"PLAN_OVER_CAPACITY: proposed {len(tasks)} tasks but max_workers={self.max_workers}; replan a legal batch"
            (step_dir / "plan_rejected.json").write_text(
                json.dumps({"status": "rejected", "error": error}, indent=2) + "\n",
                encoding="utf-8",
            )
            self.tui.log(error)
            return [error]
        prepared = plan
        if self.policy is not None:
            result = self.policy.prepare_spawn(self, plan, step_dir)
            if isinstance(result, tuple):
                prepared, status = result
                if status == "stop":
                    return ["STOP_REQUESTED"]
            else:
                prepared = result
        tasks = list(prepared.get("tasks", []))
        workers_dir = step_dir / "workers"
        workers_dir.mkdir(parents=True, exist_ok=True)
        reports = [""] * len(tasks)

        def run_one(index: int, task: dict) -> tuple[int, dict]:
            description = str(task.get("description", task.get("summary", "")))
            prompt = f"# Target\n{self.theorem_text}\n\n# Assigned task\n{description}\n\nReturn a concrete result and one MRA worker event footer."
            response = self._call(
                self.worker,
                prompt,
                "You are an independent mathematical worker. Check every non-trivial inference.",
                f"worker_{index}",
                workers_dir / f"worker_{index}_call.md",
            )
            return index, response

        responses: list[dict | None] = [None] * len(tasks)
        with ThreadPoolExecutor(max_workers=len(tasks) or 1) as executor:
            futures = [executor.submit(run_one, i, task) for i, task in enumerate(tasks)]
            for future in as_completed(futures):
                index, response = future.result()
                responses[index] = response
                reports[index] = response_text(response)

        verifier_responses: dict[int, dict] = {}
        if self.verifier:
            for index, report in enumerate(reports):
                prompt = f"# Target\n{self.theorem_text}\n\n# Worker report\n{report}\n\nIndependently verify the report. End with VERDICT: CORRECT or VERDICT: FLAWED."
                verifier_responses[index] = self._call(
                    self.worker,
                    prompt,
                    "You are an independent verifier. Do not repair silently; identify exact gaps.",
                    f"verifier_{index}",
                    workers_dir / f"verifier_{index}_call.md",
                )
        if self.policy is not None:
            self.policy.after_worker_batch(self, prepared, step_dir, responses)
            self.policy.after_verifier_batch(self, prepared, step_dir, verifier_responses)
            self.policy.after_spawn(self, prepared, step_dir, "ok")
        return reports

    def _write_items(self, action: dict) -> None:
        for item in action.get("items", []):
            if not isinstance(item, dict) or not item.get("slug"):
                continue
            self.repo.write_item(
                str(item["slug"]),
                str(item.get("content", "")),
                fmt=str(item.get("format", "markdown")),
            )

    def _submit(self, action: dict, step_dir: Path) -> Path | None:
        slug = str(action.get("proof_slug", ""))
        content = self.repo.read_item(slug) if slug else None
        if not content:
            return None
        whiteboard = (
            (self.work_dir / "WHITEBOARD.md").read_text(encoding="utf-8")
            if (self.work_dir / "WHITEBOARD.md").exists()
            else ""
        )
        blocker = submission_blocker(whiteboard)
        if blocker:
            self.tui.log(f"submit_proof blocked: {blocker}")
            return None
        if self.policy is not None and self.policy.before_submit(self, action, step_dir):
            return None
        path = self.work_dir / "PROOF.md"
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return path
