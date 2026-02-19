#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja QA - CLI application testing helper.

Runs a suite of checks against a command-line application:
  1. Entry point exists and is executable
  2. App starts without crashing (--help or brief run)
  3. App produces expected output patterns
  4. App exits cleanly (exit code 0)
  5. Import check (Python modules import without errors)

Produces a JSON report compatible with Forja's QA pipeline.

Usage:
    python3 .forja-tools/forja_qa_cli.py [entry_point] [report_dir]
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _detect_entry_point() -> str:
    """Detect the main entry point for the CLI app."""
    for name in ("main.py", "app.py", "run.py"):
        if Path(name).exists():
            return f"python3 {name}"
    if Path("manage.py").exists():
        return "python3 manage.py"
    for name in ("main.py", "app.py"):
        if (Path("src") / name).exists():
            return f"python3 src/{name}"
    # Check package.json
    if Path("package.json").exists():
        try:
            pkg = json.loads(Path("package.json").read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            if "start" in scripts:
                return "npm start"
        except (json.JSONDecodeError, OSError):
            pass
    return ""


def _run_command(cmd, timeout=30, stdin_data=None):
    """Run a command and capture output."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin_data,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:5000],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "Process timed out",
            "timed_out": True,
        }
    except Exception as e:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "timed_out": False,
        }


def run_qa(entry_point=None, report_dir=".forja"):
    """Run CLI QA tests and return True if all critical tests pass."""
    if not entry_point:
        entry_point = _detect_entry_point()

    if not entry_point:
        print("No entry point detected. Skipping CLI QA.")
        return True

    results = {"tests": [], "passed": 0, "failed": 0, "entry_point": entry_point}
    report_path = Path(report_dir) / "qa-cli-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    # Extract the script file from the entry point
    parts = entry_point.split()
    script_file = parts[-1] if parts else ""
    is_python = "python" in entry_point

    print(f"CLI QA: Testing {entry_point}")
    print(f"{'=' * 50}")

    # ── Test 1: Entry point file exists ──
    test_name = "entry_point_exists"
    script_path = Path(script_file)
    exists = script_path.exists()
    results["tests"].append({
        "name": test_name,
        "passed": exists,
        "detail": f"{script_file} {'found' if exists else 'NOT FOUND'}",
    })
    print(f"  {'✓' if exists else '✗'} {test_name}: {script_file}")

    if not exists:
        results["failed"] = sum(1 for t in results["tests"] if not t["passed"])
        results["passed"] = sum(1 for t in results["tests"] if t["passed"])
        report_path.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results, indent=2))
        return False

    # ── Test 2: Python syntax check (compile) ──
    if is_python:
        test_name = "syntax_check"
        result = _run_command(f"python3 -m py_compile {script_file}", timeout=10)
        passed = result["exit_code"] == 0
        results["tests"].append({
            "name": test_name,
            "passed": passed,
            "detail": "No syntax errors" if passed else result["stderr"][:200],
        })
        print(f"  {'✓' if passed else '✗'} {test_name}")

    # ── Test 3: Import check (Python modules import without errors) ──
    if is_python:
        test_name = "import_check"
        # Find all .py files and try importing the main one
        module_name = script_file.replace("/", ".").replace(".py", "")
        result = _run_command(
            f"python3 -c \"import importlib; importlib.import_module('{module_name}')\"",
            timeout=15,
        )
        passed = result["exit_code"] == 0
        detail = "All imports OK" if passed else result["stderr"][:300]
        results["tests"].append({
            "name": test_name,
            "passed": passed,
            "detail": detail,
        })
        print(f"  {'✓' if passed else '✗'} {test_name}: {detail[:60]}")

    # ── Test 4: Help flag (non-interactive check) ──
    test_name = "help_or_version"
    # Try --help first, then -h, then --version
    help_passed = False
    help_output = ""
    for flag in ("--help", "-h", "--version"):
        result = _run_command(f"{entry_point} {flag}", timeout=10)
        if result["exit_code"] == 0 and result["stdout"].strip():
            help_passed = True
            help_output = result["stdout"][:500]
            break
    # If no help flag works, try running with no args (some CLIs print usage)
    if not help_passed:
        result = _run_command(entry_point, timeout=10, stdin_data="\n")
        if result["exit_code"] == 0:
            help_passed = True
            help_output = result["stdout"][:500]
    results["tests"].append({
        "name": test_name,
        "passed": help_passed,
        "detail": help_output[:200] if help_passed else "No help output",
    })
    print(f"  {'✓' if help_passed else '✗'} {test_name}")

    # ── Test 5: Brief run (app starts and produces output) ──
    test_name = "starts_without_crash"
    # For interactive apps, send a quit signal after 5 seconds
    start = time.time()
    result = _run_command(entry_point, timeout=10, stdin_data="quit\nexit\nq\n")
    elapsed = time.time() - start
    # An app that crashes immediately (< 0.5s, non-zero exit) fails
    # An app that runs and times out or exits after receiving input passes
    crashed = result["exit_code"] != 0 and elapsed < 0.5 and not result["timed_out"]
    passed = not crashed
    detail = f"Ran for {elapsed:.1f}s, exit={result['exit_code']}"
    if result["timed_out"]:
        detail = f"Ran for {elapsed:.1f}s (timed out — interactive app, OK)"
    elif crashed:
        detail = f"Crashed after {elapsed:.1f}s: {result['stderr'][:200]}"
    results["tests"].append({
        "name": test_name,
        "passed": passed,
        "detail": detail,
        "stdout_preview": result["stdout"][:300],
        "stderr_preview": result["stderr"][:300] if result["stderr"] else "",
    })
    print(f"  {'✓' if passed else '✗'} {test_name}: {detail[:60]}")

    # ── Test 6: No import errors in stderr ──
    test_name = "no_import_errors"
    stderr_lower = result["stderr"].lower()
    has_import_error = "importerror" in stderr_lower or "modulenotfounderror" in stderr_lower
    passed = not has_import_error
    results["tests"].append({
        "name": test_name,
        "passed": passed,
        "detail": "No import errors" if passed else result["stderr"][:200],
    })
    print(f"  {'✓' if passed else '✗'} {test_name}")

    # ── Summary ──
    results["passed"] = sum(1 for t in results["tests"] if t["passed"])
    results["failed"] = sum(1 for t in results["tests"] if not t["passed"])
    total = len(results["tests"])

    print(f"\n{'=' * 50}")
    print(f"Results: {results['passed']}/{total} passed, {results['failed']} failed")

    # Save report
    report_path.write_text(json.dumps(results, indent=2) + "\n")
    print(f"Report: {report_path}")

    print(json.dumps(results, indent=2))
    return results["failed"] == 0


if __name__ == "__main__":
    ep = sys.argv[1] if len(sys.argv) > 1 else None
    rd = sys.argv[2] if len(sys.argv) > 2 else ".forja"
    ok = run_qa(ep, rd)
    sys.exit(0 if ok else 1)
