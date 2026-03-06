from __future__ import annotations

import argparse
from pathlib import Path

from .service import SymphonyService


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m tools.symphony",
        description="PersonaPort internal Symphony runner.",
    )
    parser.add_argument(
        "workflow",
        nargs="?",
        default="WORKFLOW.md",
        help="Path to the repo-owned workflow contract.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one poll/dispatch tick and exit.",
    )
    args = parser.parse_args()
    workflow_path = Path(args.workflow).expanduser().resolve()
    service = SymphonyService(workflow_path=workflow_path)
    service.serve(once=args.once)


if __name__ == "__main__":
    main()
