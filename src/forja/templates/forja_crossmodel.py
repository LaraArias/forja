#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja Cross-Model Validation - independent code review via external LLM.

Sends code to a DIFFERENT model than Claude for unbiased review.

Usage:
    python3 .forja-tools/forja_crossmodel.py review --file <path> [--spec <spec.json>]

Providers (fallback chain):
    1. Kimi (Moonshot AI)  (KIMI_API_KEY)
    2. Saptiva Turbo       (SAPTIVA_API_KEY)
"""

import json
import os
import sys
from pathlib import Path

from forja_utils import (
    load_dotenv, call_provider, extract_content, parse_json,
    PASS_ICON, FAIL_ICON, WARN_ICON, VERSION,
)

PASS = PASS_ICON
FAIL = FAIL_ICON
WARN = WARN_ICON

REVIEW_PROMPT = (
    "Review this code. Find logic errors, missing edge cases, and security issues. "
    'Return ONLY valid JSON, no markdown: '
    '{"pass": true/false, "issues": [{"severity": "high/medium/low", '
    '"description": "...", "line": 0, "suggestion": "..."}], "summary": "..."}'
)

PROVIDERS = [
    {
        "name": "Kimi (Moonshot AI)",
        "url": "https://api.moonshot.ai/v1/chat/completions",
        "model": "kimi-k2-0711-preview",
        "env_key": "KIMI_API_KEY",
        "temperature": 0.6,
        "max_tokens": 1024,
    },
    {
        "name": "Saptiva Turbo",
        "url": "https://api.saptiva.com/v1/chat/completions",
        "model": "Saptiva Turbo",
        "env_key": "SAPTIVA_API_KEY",
        "temperature": 0.2,
        "max_tokens": 1024,
    },
]


# ── Core ─────────────────────────────────────────────────────────────

def _build_messages(code, spec_content=None):
    """Build the chat messages for the review request."""
    user_msg = f"{REVIEW_PROMPT}\n\nCode:\n```\n{code}\n```"
    if spec_content:
        user_msg += f"\n\nVerify the code matches this spec:\n{spec_content}"

    return [
        {"role": "system", "content": "You are a senior code reviewer. Respond only with valid JSON."},
        {"role": "user", "content": user_msg},
    ]


def _get_available_providers():
    """Return providers that have API keys configured (env vars or .env)."""
    load_dotenv()
    available = []
    for p in PROVIDERS:
        if os.environ.get(p["env_key"], ""):
            available.append(p)
    return available


def cmd_review(file_path, spec_path=None):
    """Run cross-model code review."""
    # Read code file
    code_file = Path(file_path)
    if not code_file.exists():
        print(f"{FAIL} Archivo no encontrado: {file_path}")
        sys.exit(1)

    code = code_file.read_text(encoding="utf-8")
    if not code.strip():
        print(f"{WARN} Archivo vacío: {file_path}")
        sys.exit(0)

    # Read spec if provided
    spec_content = None
    if spec_path:
        spec_file = Path(spec_path)
        if spec_file.exists():
            spec_content = spec_file.read_text(encoding="utf-8")

    # Check for available providers
    available = _get_available_providers()
    if not available:
        print(f"{WARN} Cross-model review skipped: no API keys configured (set KIMI_API_KEY or SAPTIVA_API_KEY)")
        sys.exit(0)

    messages = _build_messages(code, spec_content)

    # Try providers in order
    review = None
    used_provider = None

    for provider in available:
        response = call_provider(provider, messages)
        if response is None:
            continue

        content = extract_content(response)
        if content is None:
            print(f"  {WARN} {provider['name']}: no content in response")
            continue

        review = parse_json(content)
        if review is not None:
            used_provider = provider
            break
        else:
            print(f"  {WARN} {provider['name']}: could not parse JSON from review")

    if review is None:
        print(f"{WARN} Cross-model review failed: no provider responded correctly")
        sys.exit(0)

    # Format output
    _print_review(review, used_provider["name"])

    # Exit code based on pass/fail
    if review.get("pass", True):
        sys.exit(0)
    else:
        # High severity issues cause exit 1
        issues = review.get("issues", [])
        has_high = any(i.get("severity", "").lower() == "high" for i in issues)
        sys.exit(1 if has_high else 0)


def _print_review(review, provider_name):
    """Print formatted review results."""
    passed = review.get("pass", True)
    issues = review.get("issues", [])
    summary = review.get("summary", "")

    if passed and not issues:
        print(f"{PASS} Cross-model review ({provider_name}): no issues found")
        if summary:
            print(f"  {summary}")
        return

    icon = PASS if passed else FAIL
    count = len(issues)
    print(f"{icon} Cross-model review ({provider_name}): {count} issue{'s' if count != 1 else ''}")

    severity_order = {"high": 0, "medium": 1, "low": 2}
    sorted_issues = sorted(issues, key=lambda i: severity_order.get(i.get("severity", "low"), 3))

    for issue in sorted_issues:
        sev = issue.get("severity", "?").upper()
        desc = issue.get("description", "")
        line = issue.get("line", 0)
        suggestion = issue.get("suggestion", "")

        line_part = f" in line {line}" if line else ""
        sugg_part = f" - {suggestion}" if suggestion else ""
        print(f"  {sev}: {desc}{line_part}{sugg_part}")

    if summary:
        print(f"\n  Summary: {summary}")


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] != "review":
        print("Usage: python3 .forja-tools/forja_crossmodel.py review --file <path> [--spec <spec.json>]")
        sys.exit(1)

    file_path = None
    spec_path = None
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--file" and i + 1 < len(sys.argv):
            file_path = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--spec" and i + 1 < len(sys.argv):
            spec_path = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if not file_path:
        print("ERROR: Falta --file <path>")
        sys.exit(1)

    cmd_review(file_path, spec_path)


if __name__ == "__main__":
    main()
