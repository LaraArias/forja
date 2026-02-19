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
    forja report               # open observatory dashboard
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from forja.config import run_config
from forja.constants import PRD_PATH
from forja.init import run_init
from forja.planner import run_plan
from forja.projects import run_projects
from forja.runner import run_auto_forja, run_forja, run_iterate
from forja.status import show_status
from forja.utils import VERSION, setup_logging


# ── Help text ────────────────────────────────────────────────────────

HELP_TEXT = f"""\
Forja v{VERSION} - Autonomous Software Factory

Commands:
  forja init            Set up a new project (scaffolding + Plan Mode)
  forja run             Build the project (spec review \u2192 build \u2192 outcome \u2192 learnings \u2192 observatory)
  forja iterate         Review failures, improve PRD with feedback, re-run
  forja auto            Autonomous build-iterate loop until quality gates pass
  forja status          Show feature progress during or after a build
  forja report          Open the observatory dashboard in your browser
  forja audit           Show decision audit trail (decisions, facts, assumptions)
  forja projects        Manage and monitor multiple projects
  forja config          Configure API keys
  forja plan            Run Plan Mode standalone (expert panel + PRD generation)
  forja init --upgrade  Update templates without touching context
  forja help            Show this help text

Options:
  --verbose, -v         Enable debug logging
  --version             Show version

Quick Start:
  1. forja init         (sets up project, launches Plan Mode automatically)
  2. forja run          (builds everything)

Documentation: https://github.com/user/forja
"""


# ── Command handlers ─────────────────────────────────────────────────

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


def cmd_iterate(args: argparse.Namespace) -> None:
    """Review failures, improve PRD, re-run."""
    success = run_iterate(prd_path=args.prd_path)
    sys.exit(0 if success else 1)


def cmd_auto(args: argparse.Namespace) -> None:
    """Run autonomous build-iterate loop until quality gates pass."""
    success = run_auto_forja(
        prd_path=args.prd_path,
        max_iterations=args.max_iterations,
        coverage_target=args.coverage,
    )
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
    """Open the observatory dashboard in the browser."""
    html_path = Path(".forja/observatory/evals.html")
    if not html_path.exists():
        print("No observatory report found. Run 'forja run' first.")
        sys.exit(1)

    abs_path = html_path.resolve()
    print(f"Opening dashboard: {abs_path}")
    webbrowser.open(f"file://{abs_path}")


def cmd_audit(args: argparse.Namespace) -> None:
    """Show decision audit trail."""
    import subprocess
    tools_script = Path(".forja-tools") / "forja_context.py"
    if not tools_script.exists():
        print("No .forja-tools found. Run 'forja init' first.")
        sys.exit(1)
    cmd = [sys.executable, str(tools_script), "audit"]
    if args.type:
        cmd.extend(["--type", args.type])
    result = subprocess.run(cmd, timeout=30)
    sys.exit(result.returncode)


def cmd_projects(args: argparse.Namespace) -> None:
    """Manage and monitor Forja projects."""
    action = args.action or "ls"
    success = run_projects(
        action=action,
        path=args.path,
        name=args.name,
    )
    sys.exit(0 if success else 1)


def cmd_help(args: argparse.Namespace) -> None:
    """Show help text."""
    print(HELP_TEXT)


# ── CLI entry point ──────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="forja",
        description="Autonomous software factory. PRD in, tested software out.",
        add_help=True,
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--version", action="version", version=f"forja {VERSION}",
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

    # iterate
    p_iterate = subparsers.add_parser("iterate", help="Review failures, improve PRD, re-run")
    p_iterate.add_argument("prd_path", nargs="?", default=str(PRD_PATH), help="Path to PRD")
    p_iterate.set_defaults(func=cmd_iterate)

    # auto
    p_auto = subparsers.add_parser("auto", help="Autonomous build-iterate loop until quality gates pass")
    p_auto.add_argument("prd_path", nargs="?", default=str(PRD_PATH), help="Path to PRD")
    p_auto.add_argument("--max-iterations", "-n", type=int, default=None,
                         help="Max iterations (overrides forja.toml, default: 5)")
    p_auto.add_argument("--coverage", "-c", type=int, default=None,
                         help="Coverage target %% (overrides forja.toml, default: 80)")
    p_auto.set_defaults(func=cmd_auto)

    # status
    p_status = subparsers.add_parser("status", help="View feature status")
    p_status.set_defaults(func=cmd_status)

    # report
    p_report = subparsers.add_parser("report", help="Open observatory dashboard")
    p_report.set_defaults(func=cmd_report)

    # audit
    p_audit = subparsers.add_parser("audit", help="Show decision audit trail")
    p_audit.add_argument("--type", "-t",
                         choices=["DECISION", "FACT", "ASSUMPTION", "OBSERVATION"],
                         help="Filter by entry type")
    p_audit.set_defaults(func=cmd_audit)

    # projects
    p_projects = subparsers.add_parser("projects", help="Manage and monitor projects")
    p_projects.add_argument(
        "action", nargs="?", default=None,
        choices=["ls", "list", "add", "remove", "select", "show"],
        help="Action: ls (default), add, remove, select, show",
    )
    p_projects.add_argument(
        "path", nargs="?", default=None,
        help="Path (for add) or project name (for select/remove)",
    )
    p_projects.add_argument(
        "--name", "-n", default=None,
        help="Alias for the project (default: directory name)",
    )
    p_projects.set_defaults(func=cmd_projects)

    # help
    p_help = subparsers.add_parser("help", help="Show this help text")
    p_help.set_defaults(func=cmd_help)

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        print(HELP_TEXT)
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
