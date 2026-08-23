"""Opt-in CLI for Research Harness v2 campaigns.

The established ``math-research`` commands remain owned by ``cli.py``.  These
commands are routed by ``python -m math_research_agent.research`` so campaign
features do not alter normal-run defaults.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .campaign import CampaignEngine, CampaignStore, ReplayPolicy
from .project import ProjectError, ProjectStore
from .scheduler import StopController, resolve_profile


CAMPAIGN_COMMANDS = {
    "campaign-run",
    "campaign-status",
    "campaign-stop",
    "campaign-resume",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m math_research_agent.research",
        description="Math Research Agent Research Harness v2 campaign commands",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("campaign-run", help="create and run an opt-in campaign")
    run.add_argument("--project", required=True)
    run.add_argument("--target", required=True)
    run.add_argument("--config", required=True)
    run.add_argument("--profile", choices=["normal", "overnight"], default="normal")
    run.add_argument("--campaign-id")
    run.add_argument("--workers", type=int)
    run.add_argument("--max-repair-cycles", type=int)
    run.add_argument("--replay-manifest")
    run.add_argument("--dependency-repair-catalog")
    run.add_argument("--stop-after-checkpoint", action="store_true")

    status = sub.add_parser("campaign-status", help="show one durable campaign record")
    status.add_argument("--project", required=True)
    status.add_argument("--campaign", required=True)

    stop = sub.add_parser("campaign-stop", help="request a graceful stop at the next checkpoint")
    stop.add_argument("--project", required=True)
    stop.add_argument("--campaign", required=True)
    stop.add_argument("--reason", required=True)

    resume = sub.add_parser("campaign-resume", help="resume a checkpointed campaign")
    resume.add_argument("--project", required=True)
    resume.add_argument("--campaign", required=True)
    resume.add_argument("--config", required=True)
    resume.add_argument("--workers", type=int)
    resume.add_argument("--stop-after-checkpoint", action="store_true")
    return parser


def dispatch(args: argparse.Namespace) -> dict:
    project = ProjectStore(args.project)
    campaigns = CampaignStore(project)
    if args.command == "campaign-status":
        return campaigns.load(args.campaign)
    if args.command == "campaign-stop":
        campaigns.load(args.campaign)
        return StopController(project, args.campaign).request(reason=args.reason)
    if args.command == "campaign-resume":
        record = campaigns.resume(args.campaign)
        workers = args.workers or int(record.get("max_workers") or 3)
        return CampaignEngine(
            project,
            config_path=args.config,
            worker_count=workers,
        ).run(
            args.campaign,
            stop_after_checkpoint=args.stop_after_checkpoint,
        )
    if args.command == "campaign-run":
        profile = resolve_profile(args.profile)
        campaign_id = args.campaign_id or _campaign_id(args.target)
        replay_policy = (
            ReplayPolicy.from_manifest(args.replay_manifest) if args.replay_manifest else None
        )
        repair_catalog = {}
        repair_source_root = None
        if args.dependency_repair_catalog:
            catalog_path = Path(args.dependency_repair_catalog).resolve()
            catalog_data = json.loads(catalog_path.read_text(encoding="utf-8"))
            repair_catalog = catalog_data.get("authorities", {})
            repair_source_root = catalog_data.get("source_root")
            if not isinstance(repair_catalog, dict) or not repair_source_root:
                raise ProjectError("dependency repair catalog requires source_root and authorities")
        max_repair_cycles = (
            args.max_repair_cycles
            if args.max_repair_cycles is not None
            else profile.max_repair_cycles
        )
        campaigns.create(
            campaign_id,
            target_id=args.target,
            profile=profile.name,
            max_repair_cycles=max_repair_cycles,
            infrastructure_retries=profile.infrastructure_retries,
            auto_successor=profile.auto_successor,
            auto_dependency_repair=(
                profile.auto_dependency_repair
                and replay_policy is not None
                and bool(repair_catalog)
            ),
            hard_blocker=profile.hard_blocker,
            replay_policy=replay_policy,
            dependency_repair_catalog=repair_catalog,
            dependency_repair_source_root=repair_source_root,
            budget_seconds=profile.budget_seconds,
            initial_workers=profile.initial_workers,
            max_workers=profile.max_workers,
            secondary_verification=profile.secondary_verification,
        )
        workers = args.workers or profile.max_workers
        if not profile.initial_workers <= workers <= profile.max_workers:
            raise ProjectError(
                f"{profile.name} workers must be between "
                f"{profile.initial_workers} and {profile.max_workers}"
            )
        return CampaignEngine(
            project,
            config_path=args.config,
            worker_count=workers,
        ).run(
            campaign_id,
            stop_after_checkpoint=args.stop_after_checkpoint,
        )
    raise ProjectError(f"Unhandled campaign command: {args.command}")


def _campaign_id(target_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{target_id}-campaign-{stamp}"


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = dispatch(args)
    except (ProjectError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(result, ensure_ascii=False, indent=2))
