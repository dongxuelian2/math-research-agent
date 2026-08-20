"""Command-line interface for the mathematics research project layer."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from .codex_cli_provider import CodexCLIProviderError
from .gemini_provider import GeminiProviderError
from .openai_provider import OpenAIProviderError
from .formalization import run_formalization
from .orchestrator import ResearchOrchestrator, build_run_preview
from .project import ProjectError, ProjectStore
from .providers import create_client, load_model_config, resolve_role_config
from .retrieval import ContextBuilder
from .state_machine import THEOREM_STATUSES


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _project(value: str) -> ProjectStore:
    return ProjectStore(Path(value))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="math-research",
        description="Strict project-state and audit layer around OpenProver",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a project without overwriting existing data")
    init.add_argument("--project", required=True)
    init.add_argument("--name", required=True)
    init.add_argument("--id")
    init.add_argument("--demo", action="store_true")

    add = sub.add_parser("add-theorem", help="add one explicit theorem record")
    add.add_argument("--project", required=True)
    add.add_argument("--id", required=True)
    add.add_argument("--title", required=True)
    statement = add.add_mutually_exclusive_group(required=True)
    statement.add_argument("--statement")
    statement.add_argument("--statement-file")
    add.add_argument("--status", choices=sorted(THEOREM_STATUSES), default="OPEN")
    add.add_argument("--source-file", default="")
    add.add_argument("--dependencies", help="comma-separated theorem ids")
    add.add_argument("--tags", help="comma-separated tags")
    add.add_argument("--branch", default="main")
    add.add_argument("--proof-type", default="NATURAL_LANGUAGE")
    add.add_argument(
        "--claim-type",
        choices=["implication", "iff", "classification", "equality", "unclassified"],
        default="implication",
    )

    imp = sub.add_parser("import", help="scan Markdown and create UNCLASSIFIED candidates")
    imp.add_argument("--project", required=True)
    imp.add_argument("--source", required=True)

    ctx = sub.add_parser("context", help="build a dependency-sliced context package")
    ctx.add_argument("--project", required=True)
    ctx.add_argument("--target", required=True)
    ctx.add_argument("--output")
    ctx.add_argument("--expand", action="store_true")

    run = sub.add_parser("run", help="run OpenProver candidate search and strict audits")
    run.add_argument("--project", required=True)
    run.add_argument("--target", required=True)
    run.add_argument("--config", required=True)
    run.add_argument("--workers", type=int, default=3)
    run.add_argument("--resume", nargs="?", const="latest")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--expand-context", action="store_true")
    run.add_argument("--stop-after", choices=["context", "candidate", "audits"])

    formalize = sub.add_parser(
        "formalize",
        help="run the explicit Gemini-to-Lean formalization lane for a candidate",
    )
    formalize.add_argument("--project", required=True)
    formalize.add_argument("--target", required=True)
    formalize.add_argument("--config", required=True)
    formalize.add_argument("--run", required=True, help="completed run directory")

    smoke = sub.add_parser(
        "provider-smoke",
        help="send exactly one minimal configured-provider request",
    )
    smoke.add_argument("--config", required=True)
    smoke.add_argument("--role", default="final_proof_auditor")
    smoke.add_argument("--output", required=True)
    smoke.add_argument("--expect", default="GEMINI_PROVIDER_OK")

    status = sub.add_parser("status", help="show project and theorem state")
    status.add_argument("--project", required=True)
    status.add_argument("--target")

    trans = sub.add_parser(
        "transition", help="perform a human lifecycle transition (never directly to PROVED)"
    )
    trans.add_argument("--project", required=True)
    trans.add_argument("--target", required=True)
    trans.add_argument("--to", required=True, choices=sorted(THEOREM_STATUSES - {"PROVED"}))
    trans.add_argument("--reason", required=True)

    failed = sub.add_parser("failed-route", help="record structured FAILED_ROUTE memory")
    failed.add_argument("--project", required=True)
    failed.add_argument("--id", required=True)
    failed.add_argument("--strategy", required=True)
    failed.add_argument("--target", required=True)
    failed.add_argument("--obtained", required=True)
    failed.add_argument("--failure-point", required=True)
    failed.add_argument("--insufficiency", required=True)
    failed.add_argument("--recovery-conditions", required=True)
    failed.add_argument("--theorems", required=True, help="comma-separated theorem ids")
    failed.add_argument("--tags")

    steer = sub.add_parser("steer", help="persist human steering directives")
    steer.add_argument("--project", required=True)
    steer.add_argument("--freeze-branch")
    steer.add_argument("--unfreeze-branch")
    steer.add_argument("--prohibit-route")
    steer.add_argument("--allow-scope")
    steer.add_argument("--add-lemma")
    steer.add_argument("--stop-worker")
    steer.add_argument("--reaudit", action="store_true")

    return parser


def dispatch(args: argparse.Namespace) -> dict | list | str:
    if args.command == "init":
        store = ProjectStore.initialize(
            args.project,
            args.name,
            project_id=args.id,
            demo=args.demo,
        )
        return {"project": str(store.root), "status": "created"}

    if args.command == "provider-smoke":
        config = load_model_config(args.config)
        role = dict(resolve_role_config(config, args.role))
        provider = role.get("provider")
        if provider not in {"gemini", "vertex_gemini", "codex_cli", "openai", "mock"}:
            raise ProjectError("provider-smoke requires a supported provider role")
        # Exactly one provider attempt, even if it fails. Unit tests exercise
        # each provider's normal bounded retry path separately.
        role["max_retries"] = 0
        role["max_output_tokens"] = min(
            32,
            int(role.get("max_output_tokens", 32)),
        )
        output_dir = Path(args.output).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prefix = provider.replace("_", "-") + "-provider-smoke"
        archive_path = output_dir / f"{prefix}-{stamp}.md"
        summary_path = output_dir / f"{prefix}-{stamp}.json"
        client = create_client(
            role,
            output_dir,
            role_name=args.role,
            working_dir=output_dir / str(provider) / args.role / stamp,
        )
        started = time.perf_counter()
        try:
            try:
                response = client.call(
                    prompt=(
                        f"Return exactly {args.expect} and nothing else. "
                        "Do not add punctuation, Markdown, or explanation."
                    ),
                    system_prompt=(
                        "This is a minimal provider connectivity check. Follow the "
                        "user's exact output constraint."
                    ),
                    label=f"{provider}_provider_smoke",
                    archive_path=archive_path,
                )
            except (GeminiProviderError, CodexCLIProviderError, OpenAIProviderError) as exc:
                failure = {
                    "provider": provider,
                    "role": args.role,
                    "model": role.get("model"),
                    "passed": False,
                    "logical_calls": client.call_count,
                    "provider_requests": client.request_count,
                    "api_requests": (0 if provider == "codex_cli" else client.request_count),
                    "codex_processes": int(getattr(client, "process_start_attempts", 0)),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "usage": None,
                    "billing_mode": getattr(client, "billing_mode", None),
                    "cost_usd": None,
                    "error": exc.to_dict(),
                    "archive": str(archive_path),
                    "summary": str(summary_path),
                }
                summary_path.write_text(
                    json.dumps(failure, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                exc.details["summary"] = str(summary_path)
                raise
            received = response.get("result", "").strip()
            summary = {
                "provider": provider,
                "role": args.role,
                "model": response.get("model") or role.get("model"),
                "requested_model": role.get("model"),
                "expected": args.expect,
                "received": received,
                "passed": received == args.expect,
                "logical_calls": client.call_count,
                "provider_requests": client.request_count,
                "api_requests": (0 if provider == "codex_cli" else client.request_count),
                "codex_processes": int(getattr(client, "process_start_attempts", 0)),
                "duration_ms": response.get(
                    "duration_ms",
                    int((time.perf_counter() - started) * 1000),
                ),
                "retry_count": response.get("retry_count", 0),
                "usage": response.get("usage"),
                "billing_mode": response.get(
                    "billing_mode",
                    getattr(client, "billing_mode", None),
                ),
                "cost_usd": None,
                "archive": str(archive_path),
                "summary": str(summary_path),
            }
            request_count = (
                int(getattr(client, "process_start_attempts", 0))
                if provider == "codex_cli"
                else int(client.request_count)
            )
            if request_count != 1:
                summary["passed"] = False
                summary["process_count_error"] = (
                    "Provider smoke must execute exactly one provider request"
                )
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if not summary["passed"]:
                raise ProjectError(
                    f"Provider smoke failed or response mismatched; see {summary_path}"
                )
            return summary
        finally:
            client.cleanup()

    store = _project(args.project)
    if args.command == "add-theorem":
        statement = args.statement
        if args.statement_file:
            statement = Path(args.statement_file).read_text(encoding="utf-8-sig")
        return store.add_theorem(
            args.id,
            args.title,
            statement,
            status=args.status,
            source_file=args.source_file,
            dependencies=_csv(args.dependencies),
            tags=_csv(args.tags),
            branch=args.branch,
            proof_type=args.proof_type,
            claim_type=args.claim_type,
        )
    if args.command == "import":
        return store.import_markdown(args.source)
    if args.command == "context":
        package = ContextBuilder(store).build(args.target, expand=args.expand)
        if args.output:
            json_path, md_path = ContextBuilder.write(package, args.output)
            return {"json": str(json_path), "markdown": str(md_path)}
        return package.markdown
    if args.command == "run":
        if args.dry_run:
            return build_run_preview(
                store,
                args.target,
                config_path=args.config,
                worker_count=args.workers,
                expand_context=args.expand_context,
            )
        orchestrator = ResearchOrchestrator(
            store,
            args.target,
            config_path=args.config,
            worker_count=args.workers,
            dry_run=False,
            resume=args.resume,
            expand_context=args.expand_context,
        )
        return orchestrator.run(stop_after=args.stop_after)
    if args.command == "formalize":
        return run_formalization(
            store,
            args.target,
            config_path=args.config,
            run_dir=args.run,
        )
    if args.command == "status":
        if args.target:
            return store.load_theorem(args.target)
        return {
            "project": store.load_project(),
            "theorems": store.rebuild_index()["theorems"],
        }
    if args.command == "transition":
        return store.transition(
            args.target,
            args.to,
            actor="Human",
            reason=args.reason,
        )
    if args.command == "failed-route":
        return store.record_failed_route(
            route_id=args.id,
            strategy=args.strategy,
            target=args.target,
            obtained=args.obtained,
            failure_point=args.failure_point,
            insufficiency=args.insufficiency,
            recovery_conditions=args.recovery_conditions,
            theorem_ids=_csv(args.theorems),
            tags=_csv(args.tags),
        )
    if args.command == "steer":
        return store.update_steering(
            freeze_branch=args.freeze_branch,
            unfreeze_branch=args.unfreeze_branch,
            prohibit_route=args.prohibit_route,
            allow_scope=args.allow_scope,
            add_lemma=args.add_lemma,
            stop_worker=args.stop_worker,
            reauditing=args.reaudit,
        )
    raise ProjectError(f"Unhandled command: {args.command}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = dispatch(args)
    except GeminiProviderError as exc:
        print(
            json.dumps({"error": exc.to_dict()}, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        raise SystemExit(3) from exc
    except (ProjectError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if isinstance(result, str):
        print(result)
    else:
        # Reconfigure stdout for the Unicode result emitted by replay runs;
        # this must not turn a completed run into a CLI-level encoding error.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        print(json.dumps(result, ensure_ascii=False, indent=2))
