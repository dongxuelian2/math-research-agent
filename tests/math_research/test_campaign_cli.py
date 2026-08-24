from math_research_agent.research.campaign_cli import build_parser


def test_campaign_cli_parses_overnight_profile():
    args = build_parser().parse_args(
        [
            "campaign-run",
            "--project",
            "fixture",
            "--target",
            "T1",
            "--config",
            "models.toml",
            "--profile",
            "overnight",
            "--workers",
            "6",
            "--stop-after-checkpoint",
        ]
    )
    assert args.command == "campaign-run"
    assert args.profile == "overnight"
    assert args.workers == 6
    assert args.stop_after_checkpoint is True


def test_campaign_cli_preserves_normal_as_default():
    args = build_parser().parse_args(
        [
            "campaign-run",
            "--project",
            "fixture",
            "--target",
            "T1",
            "--config",
            "models.toml",
        ]
    )
    assert args.profile == "normal"
