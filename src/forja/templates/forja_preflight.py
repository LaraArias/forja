#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja preflight checks.

Usage:
    python .forja-tools/forja_preflight.py              # pre-launch
    python .forja-tools/forja_preflight.py --post-plan  # post-plan
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

try:
    import urllib.request
    import urllib.error
    HAS_URLLIB = True
except ImportError:
    HAS_URLLIB = False

PASS = "\033[32m✔\033[0m"
FAIL = "\033[31m✘\033[0m"
WARN = "\033[33m⚠\033[0m"

# Current package version (written by forja init)
CURRENT_VERSION = "0.1.0"


def check(condition, label):
    """Evaluate a single check. Returns True if passed."""
    if condition:
        print(f"  {PASS} {label}")
        return True
    print(f"  {FAIL} {label}")
    return False


# ── API key smoke tests ──────────────────────────────────────────

def _check_kimi_api_key():
    """Validate KIMI_API_KEY with a tiny API call. Returns True/False/None (no key)."""
    key = os.environ.get("KIMI_API_KEY", "").strip()
    if not key:
        return None  # Not set — skip

    if not HAS_URLLIB:
        return True  # Can't check, assume OK

    url = "https://api.moonshot.cn/v1/models"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
    })
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False
        return True  # Other errors (rate limit, etc.) — key is probably valid
    except (urllib.error.URLError, OSError):
        return True  # Timeout / network issue — don't block


def _check_anthropic_api_key():
    """Validate ANTHROPIC_API_KEY with a tiny API call. Returns True/False/None (no key)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None  # Not set — skip

    if not HAS_URLLIB:
        return True  # Can't check, assume OK

    # Use the messages endpoint with a minimal request to validate the key
    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False
        return True  # Other errors (rate limit, 400, etc.) — key is valid
    except (urllib.error.URLError, OSError):
        return True  # Timeout / network issue — don't block


# ── Template version check ────────────────────────────────────────

_VERSION_RE = re.compile(r"FORJA_TEMPLATE_VERSION=(\S+)")


def _check_template_versions():
    """Check if .forja-tools/ scripts match the current package version.

    Prints a warning if any template is outdated. Non-blocking.
    """
    tools_dir = Path(".forja-tools")
    if not tools_dir.is_dir():
        return

    outdated = []
    for fpath in sorted(tools_dir.iterdir()):
        if not fpath.is_file():
            continue
        try:
            # Read only the first 5 lines to find the version comment
            head = fpath.read_text(encoding="utf-8", errors="replace")[:500]
        except OSError:
            continue
        m = _VERSION_RE.search(head)
        if m:
            file_version = m.group(1)
            if file_version != CURRENT_VERSION:
                outdated.append((fpath.name, file_version))

    if outdated:
        for name, ver in outdated:
            print(f"  {WARN} {name}: v{ver} vs v{CURRENT_VERSION}")
        print(
            f"  {WARN} Templates are outdated (v{outdated[0][1]} vs v{CURRENT_VERSION}). "
            f"Run 'forja init --upgrade' to update."
        )


# ── Pre-launch checks ─────────────────────────────────────────────

def preflight_pre():
    """Validate environment before Forja starts."""
    print("=== Preflight: pre-launch ===\n")
    ok = True

    ok &= check(
        Path("CLAUDE.md").exists(),
        "CLAUDE.md exists in project root",
    )

    prd = Path("context/prd.md")
    ok &= check(
        prd.exists() and prd.stat().st_size > 10,
        "context/prd.md exists and is not empty (>10 bytes)",
    )

    ok &= check(
        Path(".forja-tools").is_dir(),
        ".forja-tools/ exists as directory",
    )

    ok &= check(
        Path(".claude/settings.local.json").exists(),
        ".claude/settings.local.json exists",
    )

    ok &= check(
        shutil.which("ruff") is not None,
        "ruff available in PATH",
    )

    ok &= check(
        shutil.which("jq") is not None,
        "jq available in PATH",
    )

    # Template version check (warning only, non-blocking)
    _check_template_versions()

    # API key smoke tests
    kimi_result = _check_kimi_api_key()
    if kimi_result is None:
        print(f"  {PASS} KIMI_API_KEY not set (skipped)")
    elif kimi_result:
        print(f"  {PASS} KIMI_API_KEY is valid")
    else:
        print(f"  {FAIL} KIMI_API_KEY is invalid. Run forja config.")
        ok = False

    anthropic_result = _check_anthropic_api_key()
    if anthropic_result is None:
        print(f"  {PASS} ANTHROPIC_API_KEY not set (skipped)")
    elif anthropic_result:
        print(f"  {PASS} ANTHROPIC_API_KEY is valid")
    else:
        print(f"  {FAIL} ANTHROPIC_API_KEY is invalid. Run forja config.")
        ok = False

    return ok


# ── Post-plan checks ────────────────────────────────────────────────

def _is_valid_json(path):
    """Return True if path is a valid JSON file."""
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (json.JSONDecodeError, OSError):
        return False


def preflight_post_plan():
    """Validate structure after planning phase."""
    print("=== Preflight: post-plan ===\n")
    ok = True

    teammates_dir = Path("context/teammates")

    # At least 1 subdirectory
    subdirs = [d for d in teammates_dir.iterdir() if d.is_dir()] if teammates_dir.is_dir() else []
    ok &= check(
        len(subdirs) >= 1,
        "context/teammates/ has at least 1 subdirectory",
    )

    # Each subdirectory has CLAUDE.md and features.json
    for subdir in subdirs:
        claude_md = subdir / "CLAUDE.md"
        features_json = subdir / "features.json"

        ok &= check(
            claude_md.exists(),
            f"{subdir.name}/CLAUDE.md exists",
        )
        ok &= check(
            features_json.exists(),
            f"{subdir.name}/features.json exists",
        )
        if features_json.exists():
            ok &= check(
                _is_valid_json(features_json),
                f"{subdir.name}/features.json is valid JSON",
            )

    # QA teammate exists
    ok &= check(
        Path("context/teammates/qa").is_dir(),
        "context/teammates/qa/ exists",
    )

    # teammate_map.json exists and is valid
    tm_map = Path("context/teammate_map.json")
    ok &= check(
        tm_map.exists(),
        "context/teammate_map.json exists",
    )
    if tm_map.exists():
        ok &= check(
            _is_valid_json(tm_map),
            "context/teammate_map.json is valid JSON",
        )

    return ok


# ── Main ─────────────────────────────────────────────────────────────

def main():
    post_plan = "--post-plan" in sys.argv

    if post_plan:
        ok = preflight_post_plan()
    else:
        ok = preflight_pre()

    print()
    if ok:
        print("All checks passed.")
        sys.exit(0)
    else:
        print("Some checks failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
