import sys

from .campaign_cli import CAMPAIGN_COMMANDS, main as campaign_main
from .cli import main as legacy_main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in CAMPAIGN_COMMANDS:
        campaign_main()
    else:
        legacy_main()
