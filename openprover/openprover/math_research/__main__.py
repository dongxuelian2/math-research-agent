import sys


CAMPAIGN_COMMANDS = {
    "campaign-run",
    "campaign-status",
    "campaign-stop",
    "campaign-resume",
}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "observatory":
        from .observatory import main as observatory_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        observatory_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "demo":
        from .showcase_demo import main as showcase_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        showcase_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "benchmark":
        from .benchmark import main as benchmark_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        benchmark_main()
    elif len(sys.argv) > 1 and sys.argv[1] in CAMPAIGN_COMMANDS:
        from .campaign_cli import main as campaign_main

        campaign_main()
    else:
        from .cli import main as core_cli_main

        core_cli_main()
