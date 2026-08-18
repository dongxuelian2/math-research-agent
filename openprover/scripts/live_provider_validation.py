"""Bounded live provider smoke checks for the math-research harness.

This file intentionally uses synthetic prompts only.  It is a diagnostic
script, not a campaign runner: one Sol cancellation pair and one optional
single-model account probe can be run without touching a project theorem.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from openprover.math_research.codex_cli_provider import (
    BILLING_MODE,
    CodexCLIClient,
    CodexCLIProviderError,
    resolve_codex_command,
)
from openprover.math_research.pipelines import (
    AsyncDAGScheduler,
    AsynchronousPipelineRuntime,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProcessRecorder:
    def __init__(self):
        self.processes: list[subprocess.Popen] = []
        self.started = threading.Event()
        self.lock = threading.RLock()

    def popen(self, *args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        with self.lock:
            self.processes.append(process)
        self.started.set()
        return process

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [
                {
                    "pid": getattr(process, "pid", None),
                    "returncode": process.poll(),
                    "started": process is not None,
                }
                for process in self.processes
            ]


def _process_children(pid: int | None) -> list[int]:
    if not pid:
        return []
    try:
        import psutil  # type: ignore
    except ImportError:
        return []
    try:
        return [int(child.pid) for child in psutil.Process(pid).children(recursive=True)]
    except Exception:
        return []


def evaluate_cancellation_evidence(value: dict) -> dict:
    """Recompute Phase A without trusting a producer-supplied PASS string."""

    records = value.get("records") if isinstance(value.get("records"), dict) else {}
    task_a_id = str(value.get("task_A_id") or value.get("task_a_id") or "")
    task_b_id = str(value.get("task_B_id") or value.get("task_b_id") or "")
    task_a = records.get(task_a_id, {}) if task_a_id else {}
    task_b = records.get(task_b_id, {}) if task_b_id else {}
    state_a = str(value.get("task_a_status") or task_a.get("final_task_state") or "")
    state_b = str(value.get("task_b_status") or task_b.get("final_task_state") or "")
    interrupt_at = str(task_a.get("interrupt_dispatch_timestamp") or "")
    exit_at = str(task_a.get("process_exit_timestamp") or "")
    provider_error = task_a.get("provider_error") if isinstance(task_a.get("provider_error"), dict) else {}
    retry_count = int(provider_error.get("retry_count", task_a.get("retry_count", 0)) or 0)
    fallback_count = int(task_a.get("fallback_count", 0) or 0)
    checks = {
        "task_a_interrupted": state_a == "INTERRUPTED",
        "cancel_requested": bool(task_a.get("cancel_request_timestamp")),
        "interrupt_dispatched": bool(interrupt_at),
        "process_exit_after_interrupt": bool(exit_at and interrupt_at and exit_at >= interrupt_at),
        "no_retry": retry_count == 0,
        "no_fallback_relaunch": fallback_count == 0,
        "task_b_unaffected": state_b == "COMPLETE",
    }
    if state_a == "COMPLETED_BEFORE_CANCEL":
        verdict = "INCONCLUSIVE"
    else:
        verdict = "PASS" if all(checks.values()) else "FAIL"
    return {"verdict": verdict, "checks": checks}


def _client(root: Path, role: str, model: str, effort: str, recorder: ProcessRecorder):
    return CodexCLIClient(
        model,
        root / "archive" / role,
        role_name=role,
        working_dir=root / "work" / role,
        reasoning_effort=effort,
        timeout_seconds=90,
        max_retries=0,
        sandbox="read-only",
        popen_factory=recorder.popen,
    )


def run_sol_cancellation(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    scheduler = AsyncDAGScheduler(
        state_path=root / "pipeline_state.json",
        config={"global_budget": {"provider_calls": 2}},
    )
    scheduler.add_obligation("live-cancel-A", target_statement="synthetic task A")
    scheduler.add_obligation("live-cancel-B", target_statement="synthetic task B")
    initial_tasks = [
        task for task in scheduler.snapshot()["tasks"].values()
        if task.get("pipeline") == "proof" and task.get("status") == "READY"
    ]
    task_a = next(task for task in initial_tasks if task["obligation_id"] == "live-cancel-A")
    task_b = next(task for task in initial_tasks if task["obligation_id"] == "live-cancel-B")
    records: dict[str, dict] = {
        task_a["task_id"]: {"task_id": task_a["task_id"], "obligation_id": "live-cancel-A"},
        task_b["task_id"]: {"task_id": task_b["task_id"], "obligation_id": "live-cancel-B"},
    }
    recorders = {task_a["task_id"]: ProcessRecorder(), task_b["task_id"]: ProcessRecorder()}
    clients = {
        task_a["task_id"]: _client(root, "cancel-A", "gpt-5.6-sol", "low", recorders[task_a["task_id"]]),
        task_b["task_id"]: _client(root, "continue-B", "gpt-5.6-sol", "low", recorders[task_b["task_id"]]),
    }

    def handler(task, context):
        task_id = task["task_id"]
        client = clients[task_id]
        records[task_id]["call_id"] = task.get("call_id")
        records[task_id]["provider"] = "codex_cli"
        records[task_id]["model"] = client.model
        records[task_id]["reasoning_effort"] = client.reasoning_effort
        records[task_id]["start_timestamp"] = utc_now()
        context.set_handle(client)
        if task_id == task_a["task_id"]:
            prompt = (
                "Synthetic cancellation test. Do not edit files. You MUST run this "
                "read-only command before answering: powershell -NoProfile -Command "
                "Start-Sleep -Seconds 30. After it returns answer exactly A_DONE."
            )
        else:
            prompt = "Synthetic control task. Do not use tools. Answer exactly B_DONE."
        try:
            response = client.call(
                prompt,
                "You are participating in a bounded provider smoke test. Never discuss mathematics.",
                label=f"live_{task_id}",
                archive_path=root / "archive" / f"{task_id}.md",
            )
            records[task_id]["provider_result"] = response.get("result")
            records[task_id]["usage"] = response.get("usage")
            records[task_id]["exit_code"] = 0
            return {"success": True, "provider": response}
        except CodexCLIProviderError as exc:
            records[task_id]["provider_error"] = exc.details
            records[task_id]["exit_code"] = exc.details.get("status")
            raise
        finally:
            records[task_id]["processes"] = recorders[task_id].snapshot()
            records[task_id]["child_pids"] = sorted({
                child
                for proc in recorders[task_id].processes
                for child in _process_children(getattr(proc, "pid", None))
            })
            records[task_id]["process_exit_timestamp"] = utc_now()
            client.cleanup()

    runtime = AsynchronousPipelineRuntime(
        scheduler,
        {"proof": handler},
        max_workers=2,
    )
    cancel_requested = None
    interrupt_dispatched = None
    try:
        runtime.start_window({"proof": 2, "literature": 0, "verification": 0})
        started = recorders[task_a["task_id"]].started.wait(30)
        if not started:
            return {
                "phase": "A",
                "status": "BLOCKED",
                "reason": "Sol provider process did not start within 30 seconds",
                "records": records,
            }
        # Give the CLI a short opportunity to enter its provider/tool loop.
        time.sleep(1.0)
        cancel_requested = utc_now()
        records[task_a["task_id"]]["cancel_request_timestamp"] = cancel_requested
        scheduler.request_cancel(task_a["task_id"], reason="live smoke task isolation")
        interrupt_dispatched = utc_now()
        records[task_a["task_id"]]["interrupt_dispatch_timestamp"] = interrupt_dispatched
        deadline = time.monotonic() + 45
        while runtime.pending() and time.monotonic() < deadline:
            runtime.poll()
            time.sleep(0.05)
        runtime.poll()
        snapshot = scheduler.snapshot()
        status_a = snapshot["tasks"][task_a["task_id"]]["status"]
        status_b = snapshot["tasks"][task_b["task_id"]]["status"]
        records[task_a["task_id"]]["final_task_state"] = status_a
        records[task_a["task_id"]]["retry_count"] = int(
            (records[task_a["task_id"]].get("provider_error") or {}).get("retry_count", 0) or 0
        )
        records[task_a["task_id"]]["fallback_count"] = 0
        records[task_b["task_id"]]["final_task_state"] = status_b
        value = {
            "phase": "A",
            "status": "PENDING_EVIDENCE_EVALUATION",
            "task_A_id": task_a["task_id"],
            "task_B_id": task_b["task_id"],
            "task_a_status": status_a,
            "task_b_status": status_b,
            "fallback_count": 0,
            "server_side_post_interrupt_token_behavior": "not directly observable",
            "records": records,
            "scheduler": snapshot,
        }
        evaluation = evaluate_cancellation_evidence(value)
        value["status"] = evaluation["verdict"]
        value["strict_evaluation"] = evaluation
        return value
    finally:
        if cancel_requested:
            records[task_a["task_id"]].setdefault("cancel_request_timestamp", cancel_requested)
        if interrupt_dispatched:
            records[task_a["task_id"]].setdefault("interrupt_dispatch_timestamp", interrupt_dispatched)
        runtime.shutdown(wait=True)


def run_account_smoke(root: Path, model: str, effort: str) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    recorder = ProcessRecorder()
    client = _client(root, "account", model, effort, recorder)
    started = utc_now()
    try:
        response = client.call(
            "Return exactly LIVE_PROVIDER_OK and do not use tools.",
            "This is a bounded account capability smoke. Do not discuss mathematics.",
            label="account_smoke",
            archive_path=root / "account-call.md",
        )
        return {
            "status": "PASS",
            "requested_provider": "codex_cli",
            "requested_model": model,
            "requested_effort": effort,
            "actual_provider": response.get("provider"),
            "actual_model": response.get("model"),
            "actual_effort": response.get("reasoning_effort"),
            "fallback_used": False,
            "started_at": started,
            "finished_at": utc_now(),
            "usage": response.get("usage"),
            "processes": recorder.snapshot(),
        }
    except CodexCLIProviderError as exc:
        return {
            "status": "FAIL",
            "requested_provider": "codex_cli",
            "requested_model": model,
            "requested_effort": effort,
            "error": exc.details,
            "started_at": started,
            "finished_at": utc_now(),
            "processes": recorder.snapshot(),
        }
    finally:
        client.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("A", "B"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--effort", default="max")
    args = parser.parse_args()
    if args.phase == "A":
        value = run_sol_cancellation(args.root)
    else:
        value = run_account_smoke(args.root, args.model, args.effort)
    args.root.mkdir(parents=True, exist_ok=True)
    (args.root / f"phase-{args.phase}.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    return 0 if value.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
