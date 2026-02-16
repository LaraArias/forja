#!/usr/bin/env python3
"""Forja CLI - Autonomous software factory.

One command:
    forja run  [prd_path]      # init + plan + build (all automatic)

``forja run`` handles everything: scaffolds the project if needed,
launches the interactive planner when the PRD is a placeholder,
then runs the full build pipeline.

Other commands:
    forja config               # configure API keys
    forja init [directory]     # scaffold project (standalone)
    forja plan [prd_path]      # run planner (standalone)
    forja status               # view feature status
    forja report [directory]   # metrics dashboard
"""

from __future__ import annotations

import argparse
import sys

from forja.config import run_config
from forja.constants import PRD_PATH
from forja.init import run_init
from forja.planner import run_plan
from forja.runner import run_forja
from forja.status import show_status
from forja.utils import setup_logging


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a Forja project."""
    success = run_init(directory=args.directory, force=args.force, upgrade=args.upgrade)
    sys.exit(0 if success else 1)


def cmd_plan(args: argparse.Namespace) -> None:
    """Run interactive PRD planning."""
    success = run_plan(prd_path=args.prd_path)
    sys.exit(0 if success else 1)


def cmd_run(args: argparse.Namespace) -> None:
    """Run Forja pipeline."""
    success = run_forja(prd_path=args.prd_path)
    sys.exit(0 if success else 1)


def cmd_status(args: argparse.Namespace) -> None:
    """Show Forja project status."""
    success = show_status()
    sys.exit(0 if success else 1)


def cmd_config(args: argparse.Namespace) -> None:
    """Configure global API keys."""
    success = run_config()
    sys.exit(0 if success else 1)


def cmd_report(args: argparse.Namespace) -> None:
    """Generate Forja report (coming soon)."""
    print("forja report: coming soon")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="forja",
        description="Autonomous software factory. PRD in, tested software out.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # config
    p_config = subparsers.add_parser("config", help="Configure global API keys")
    p_config.set_defaults(func=cmd_config)

    # init
    p_init = subparsers.add_parser("init", help="Initialize Forja project")
    p_init.add_argument("directory", nargs="?", default=".", help="Target directory (default: .)")
    p_init.add_argument("--force", "-f", action="store_true", help="Overwrite without asking")
    p_init.add_argument("--upgrade", action="store_true", help="Only update templates (skip context/git/skills)")
    p_init.set_defaults(func=cmd_init)

    # plan
    p_plan = subparsers.add_parser("plan", help="Plan PRD with expert panel")
    p_plan.add_argument("prd_path", nargs="?", default=str(PRD_PATH), help="Path to PRD")
    p_plan.set_defaults(func=cmd_plan)

    # run
    p_run = subparsers.add_parser("run", help="Run build pipeline")
    p_run.add_argument("prd_path", nargs="?", default=str(PRD_PATH), help="Path to PRD")
    p_run.set_defaults(func=cmd_run)

    # status
    p_status = subparsers.add_parser("status", help="View feature status")
    p_status.set_defaults(func=cmd_status)

    # report
    p_report = subparsers.add_parser("report", help="Generate metrics report")
    p_report.add_argument("directory", nargs="?", default=".", help="Project directory")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
