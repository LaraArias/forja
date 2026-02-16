#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja Hardening - AI-generated edge case testing against a live server.

Post-merge iterative loop: generates edge cases with Kimi, fires them at
a running server, and requires 3 consecutive clean rounds to pass.

Usage:
    python3 .forja-tools/forja_hardening.py --prd context/prd.md \
        --specs context/teammates/*/validation_spec.json
"""

import glob as glob_mod
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from forja_utils import (
    load_dotenv, call_provider, extract_content, parse_json_array,
    PASS_ICON, FAIL_ICON, WARN_ICON,
)

PASS = PASS_ICON
FAIL = FAIL_ICON
WARN = WARN_ICON

MAX_ROUNDS = 10
CLEAN_ROUNDS_REQUIRED = 3
CASES_PER_ROUND = 5
SERVER_WAIT = 3
SERVER_PORT = 8000
SERVER_BASE = f"http://localhost:{SERVER_PORT}"
LLM_TIMEOUT = 45
HTTP_TIMEOUT = 10

KIMI_PROVIDER = {
    "name": "Kimi (Moonshot AI)",
    "url": "https://api.moonshot.ai/v1/chat/completions",
    "model": "kimi-k2-0711-preview",
    "env_key": "KIMI_API_KEY",
    "temperature": 0.7,
    "max_tokens": 2048,
}

EDGE_CASE_PROMPT = (
    "Given this PRD and these endpoints, generate {n} edge cases that could "
    "break the system. Think about: empty inputs, wrong types, expired auth, "
    "unicode, SQL injection, oversized payloads, missing required fields, "
    "boundary values, duplicate operations, concurrent-like patterns.\n"
    "Return ONLY valid JSON array, no markdown:\n"
    '[{{"method": "POST", "path": "/auth/login", "body": {{}}, '
    '"headers": {{}}, "expected_status": 400, '
    '"description": "empty credentials"}}]'
)


# ── Server management ────────────────────────────────────────────────

def _detect_server_command(prd_content):
    """Try to extract server start command from PRD. Falls back to uvicorn."""
    # Look for common patterns in the PRD
    for line in prd_content.splitlines():
        stripped = line.strip()
        # Look for explicit run commands
        if "uvicorn" in stripped and ":" in stripped:
            # Extract the command part
            for token in stripped.split():
                if "uvicorn" in token:
                    # Found uvicorn reference, try to build command
                    idx = stripped.find("uvicorn")
                    cmd_part = stripped[idx:].split("`")[0].split("\"")[0].strip()
                    if ":" in cmd_part:
                        return f"python3 -m {cmd_part}"
    return f"python3 -m uvicorn main:app --port {SERVER_PORT}"


def _start_server(cmd):
    """Start server in background. Returns subprocess.Popen or None."""
    parts = cmd.split()
    try:
        proc = subprocess.Popen(
            parts,
            cwd="src",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )
        return proc
    except FileNotFoundError:
        # Try from project root if src/ doesn't exist
        try:
            proc = subprocess.Popen(
                parts,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid,
            )
            return proc
        except FileNotFoundError:
            return None


def _stop_server(proc):
    """Kill server process group."""
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


def _wait_for_server(port, timeout=SERVER_WAIT):
    """Wait until server responds or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"http://localhost:{port}/",
                method="GET",
                headers={"User-Agent": "Forja/0.1.0"},
            )
            urllib.request.urlopen(req, timeout=2)
            return True
        except Exception as e:
            print(f"  waiting for server on port {port}: {e}", file=sys.stderr)
            time.sleep(0.3)
    # One last check - server might return non-200 but still be up
    try:
        req = urllib.request.Request(
            f"http://localhost:{port}/",
            method="GET",
            headers={"User-Agent": "Forja/0.1.0"},
        )
        urllib.request.urlopen(req, timeout=2)
        return True
    except urllib.error.HTTPError:
        # Server is up, just returned an error status (e.g. 404)
        return True
    except Exception as e:
        print(f"  server not reachable on port {port}: {e}", file=sys.stderr)
        return False


# ── Edge case execution ──────────────────────────────────────────────

def _execute_edge_case(case):
    """Execute a single edge case against the server. Returns result dict."""
    method = case.get("method", "GET").upper()
    path = case.get("path", "/")
    body = case.get("body")
    headers = case.get("headers", {})
    expected_status = case.get("expected_status", 200)
    description = case.get("description", "")

    url = f"{SERVER_BASE}{path}"
    data = None
    if body is not None and method in ("POST", "PUT", "PATCH"):
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    headers["User-Agent"] = "Forja-Hardening/0.1.0"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    result = {
        "description": description,
        "method": method,
        "path": path,
        "expected_status": expected_status,
        "actual_status": None,
        "passed": False,
        "error": None,
    }

    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            result["actual_status"] = resp.status
    except urllib.error.HTTPError as e:
        result["actual_status"] = e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        result["error"] = str(e)
        return result

    result["passed"] = result["actual_status"] == expected_status
    return result


# ── Edge case generation ─────────────────────────────────────────────

def _generate_edge_cases(prd_content, specs_content, round_num, previous_failures):
    """Ask Kimi to generate edge cases based on PRD and specs."""
    prompt = EDGE_CASE_PROMPT.format(n=CASES_PER_ROUND)

    user_msg = f"{prompt}\n\nPRD:\n{prd_content[:3000]}"

    if specs_content:
        user_msg += f"\n\nValidation specs:\n{specs_content[:2000]}"

    if previous_failures:
        failures_text = json.dumps(previous_failures[:5], indent=2)
        user_msg += (
            f"\n\nPrevious failures (round {round_num - 1}) - "
            f"generate DIFFERENT cases that target similar weaknesses:\n{failures_text}"
        )
    else:
        user_msg += f"\n\nThis is round {round_num}. Generate diverse edge cases."

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior QA engineer specialized in API security "
                "and edge case testing. Respond only with valid JSON arrays."
            ),
        },
        {"role": "user", "content": user_msg},
    ]

    response = call_provider(KIMI_PROVIDER, messages, timeout=LLM_TIMEOUT)
    if response is None:
        return None

    content = extract_content(response)
    if content is None:
        return None

    return parse_json_array(content)


# ── Main loop ────────────────────────────────────────────────────────

def run_hardening(prd_path, spec_patterns):
    """Run the hardening loop."""
    # Read PRD
    prd_file = Path(prd_path)
    if not prd_file.exists():
        print(f"{FAIL} PRD not found: {prd_path}")
        return False

    prd_content = prd_file.read_text(encoding="utf-8")
    if not prd_content.strip():
        print(f"{FAIL} Empty PRD: {prd_path}")
        return False

    # Read specs (glob patterns)
    specs_content = ""
    for pattern in spec_patterns:
        for spec_file in sorted(glob_mod.glob(pattern)):
            try:
                specs_content += Path(spec_file).read_text(encoding="utf-8") + "\n"
            except OSError:
                pass

    # Check API key
    load_dotenv()
    if not os.environ.get("KIMI_API_KEY", ""):
        print(f"{WARN} Hardening skipped: KIMI_API_KEY not configured")
        return True

    # Detect server command
    server_cmd = _detect_server_command(prd_content)
    print(f"Server command: {server_cmd}")

    # Start server
    print(f"Starting server...")
    server_proc = _start_server(server_cmd)
    if server_proc is None:
        print(f"{FAIL} Could not start server")
        return False

    # Wait for server
    if not _wait_for_server(SERVER_PORT):
        print(f"{WARN} Server did not respond in {SERVER_WAIT}s, trying anyway...")

    print(f"Server running (PID {server_proc.pid})\n")

    # Hardening loop
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "server_command": server_cmd,
        "rounds": [],
        "result": None,
    }

    clean_rounds = 0
    previous_failures = []

    try:
        for round_num in range(1, MAX_ROUNDS + 1):
            print(f"── Round {round_num}/{MAX_ROUNDS} ──")

            # Generate edge cases
            cases = _generate_edge_cases(
                prd_content, specs_content, round_num, previous_failures,
            )

            if cases is None or not cases:
                print(f"  {WARN} Could not generate edge cases, retrying...")
                round_data = {
                    "round": round_num,
                    "status": "skip",
                    "reason": "edge case generation failed",
                    "cases": [],
                }
                report["rounds"].append(round_data)
                continue

            # Execute each case
            round_results = []
            round_failures = []

            for idx, case in enumerate(cases, 1):
                desc = case.get("description", "?")
                result = _execute_edge_case(case)
                round_results.append(result)

                if result.get("error"):
                    status_str = f"ERROR: {result['error']}"
                    round_failures.append(case)
                elif result["passed"]:
                    status_str = f"{result['actual_status']} == {result['expected_status']}"
                else:
                    status_str = f"{result['actual_status']} != {result['expected_status']}"
                    round_failures.append(case)

                icon = PASS if result["passed"] and not result.get("error") else FAIL
                print(f"  {icon} {idx}. {desc} [{status_str}]")

            # Evaluate round
            all_passed = len(round_failures) == 0

            round_data = {
                "round": round_num,
                "status": "pass" if all_passed else "fail",
                "cases_total": len(cases),
                "cases_passed": len(cases) - len(round_failures),
                "results": round_results,
            }
            report["rounds"].append(round_data)

            if all_passed:
                clean_rounds += 1
                previous_failures = []
                print(f"  Round {round_num}: {PASS} (clean {clean_rounds}/{CLEAN_ROUNDS_REQUIRED})")
            else:
                clean_rounds = 0
                previous_failures = round_failures
                fail_count = len(round_failures)
                print(f"  Round {round_num}: {FAIL} - {fail_count} case(s) failed")

            # Check exit conditions
            if clean_rounds >= CLEAN_ROUNDS_REQUIRED:
                print(f"\n{PASS} PRODUCTION READY - {CLEAN_ROUNDS_REQUIRED} consecutive clean rounds")
                report["result"] = "production_ready"
                break

            print()

        else:
            # Reached MAX_ROUNDS without enough clean rounds
            pending = CLEAN_ROUNDS_REQUIRED - clean_rounds
            print(f"\n{WARN} MAX ROUNDS ({MAX_ROUNDS}) - {pending} clean round(s) remaining")
            report["result"] = "max_rounds"

    finally:
        # Always kill server
        print(f"\nStopping server (PID {server_proc.pid})...")
        _stop_server(server_proc)

    # Save report
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report_path = Path(".forja") / "hardening-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"Report saved to {report_path}")

    return report["result"] == "production_ready"


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    prd_path = None
    spec_patterns = []

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--prd" and i + 1 < len(sys.argv):
            prd_path = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--specs" and i + 1 < len(sys.argv):
            # Collect all remaining args as spec patterns until next flag
            i += 1
            while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                spec_patterns.append(sys.argv[i])
                i += 1
        else:
            i += 1

    if not prd_path:
        print(
            "Usage: python3 .forja-tools/forja_hardening.py "
            "--prd context/prd.md "
            "--specs context/teammates/*/validation_spec.json"
        )
        sys.exit(1)

    success = run_hardening(prd_path, spec_patterns)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
