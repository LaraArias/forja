#!/usr/bin/env python3
"""Forja preflight checks.

Usage:
    python .forja-tools/forja_preflight.py              # pre-arranque
    python .forja-tools/forja_preflight.py --post-plan  # post-planificación
"""

import json
import shutil
import sys
from pathlib import Path

PASS = "\033[32m✔\033[0m"
FAIL = "\033[31m✘\033[0m"


def check(condition, label):
    """Evaluate a single check. Returns True if passed."""
    if condition:
        print(f"  {PASS} {label}")
        return True
    print(f"  {FAIL} {label}")
    return False


# ── Pre-arranque checks ─────────────────────────────────────────────

def preflight_pre():
    """Validate environment before Forja starts."""
    print("=== Preflight: pre-arranque ===\n")
    ok = True

    ok &= check(
        Path("CLAUDE.md").exists(),
        "CLAUDE.md existe en la raíz",
    )

    prd = Path("context/prd.md")
    ok &= check(
        prd.exists() and prd.stat().st_size > 10,
        "context/prd.md existe y no está vacío (>10 bytes)",
    )

    ok &= check(
        Path(".forja-tools").is_dir(),
        ".forja-tools/ existe como directorio",
    )

    ok &= check(
        Path(".claude/settings.local.json").exists(),
        ".claude/settings.local.json existe",
    )

    ok &= check(
        shutil.which("ruff") is not None,
        "ruff está disponible en PATH",
    )

    ok &= check(
        shutil.which("jq") is not None,
        "jq está disponible en PATH",
    )

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
    """Validate structure after HoP planning phase."""
    print("=== Preflight: post-plan ===\n")
    ok = True

    teammates_dir = Path("context/teammates")

    # At least 1 subdirectory
    subdirs = [d for d in teammates_dir.iterdir() if d.is_dir()] if teammates_dir.is_dir() else []
    ok &= check(
        len(subdirs) >= 1,
        "context/teammates/ tiene al menos 1 subdirectorio",
    )

    # Each subdirectory has CLAUDE.md and features.json
    for subdir in subdirs:
        claude_md = subdir / "CLAUDE.md"
        features_json = subdir / "features.json"

        ok &= check(
            claude_md.exists(),
            f"{subdir.name}/CLAUDE.md existe",
        )
        ok &= check(
            features_json.exists(),
            f"{subdir.name}/features.json existe",
        )
        if features_json.exists():
            ok &= check(
                _is_valid_json(features_json),
                f"{subdir.name}/features.json es JSON válido",
            )

    # QA teammate exists
    ok &= check(
        Path("context/teammates/qa").is_dir(),
        "context/teammates/qa/ existe",
    )

    # teammate_map.json exists and is valid
    tm_map = Path("context/teammate_map.json")
    ok &= check(
        tm_map.exists(),
        "context/teammate_map.json existe",
    )
    if tm_map.exists():
        ok &= check(
            _is_valid_json(tm_map),
            "context/teammate_map.json es JSON válido",
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
        print("Todos los checks pasaron.")
        sys.exit(0)
    else:
        print("Algunos checks fallaron.")
        sys.exit(1)


if __name__ == "__main__":
    main()
