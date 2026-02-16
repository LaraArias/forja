#!/usr/bin/env python3
"""Forja status - show feature progress across all teammates.

Safe for live runs: each features.json is read independently with
try/except. Partial writes, missing files, and corrupt JSON are all
handled gracefully without crashing.
"""

from __future__ import annotations

import json
from pathlib import Path

from forja.constants import CLAUDE_MD, FORJA_TOOLS, TEAMMATES_DIR
from forja.utils import FAIL_ICON, PASS_ICON, WARN_ICON


def _check_project() -> bool:
    """Verify we're inside a Forja project."""
    if not CLAUDE_MD.exists() or not FORJA_TOOLS.is_dir():
        print(f"{FAIL_ICON} Error: Not a Forja project. Run 'forja init' first.")
        return False
    return True


def _load_features_safe(path: Path) -> tuple[str, list[dict]]:
    """Load features.json safely for live runs.

    Returns (status, features_list) where status is one of:
      "ok"       - parsed successfully
      "waiting"  - file does not exist yet
      "reading"  - file exists but JSON is invalid (mid-write?)
    """
    if not path.exists():
        return "waiting", []
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        return "ok", data.get("features", [])
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return "reading", []


def show_status() -> bool:
    """Main status entrypoint."""
    if not _check_project():
        return False

    teammates_dir = TEAMMATES_DIR

    if not teammates_dir.is_dir():
        print("Build not started. Run 'forja run' first.")
        return True

    try:
        subdirs = sorted(d for d in teammates_dir.iterdir() if d.is_dir())
    except OSError:
        print("Build not started. Run 'forja run' first.")
        return True

    if not subdirs:
        print("Build not started. Run 'forja run' first.")
        return True

    print("Forja Status")
    print("============\n")

    total = 0
    passed = 0
    blocked = 0
    failed = 0

    for subdir in subdirs:
        name = subdir.name
        features_path = subdir / "features.json"

        status, features = _load_features_safe(features_path)

        if status == "waiting":
            print(f"  {name}: waiting...")
            print()
            continue

        if status == "reading":
            print(f"  {name}: reading...")
            print()
            continue

        if not features:
            print(f"  {name}: (no features)")
            print()
            continue

        # Count per-teammate stats
        tm_passed = 0
        tm_blocked = 0
        tm_failed = 0

        for feat in features:
            fid = feat.get("id", "?")
            desc = feat.get("description", "")
            feat_status = feat.get("status", "pending")
            cycles = feat.get("cycles", 0)

            total += 1
            cycle_label = "cycle" if cycles == 1 else "cycles"

            if feat_status == "blocked":
                blocked += 1
                tm_blocked += 1
                icon = WARN_ICON
                suffix = " BLOCKED"
            elif feat_status == "passed":
                passed += 1
                tm_passed += 1
                icon = PASS_ICON
                suffix = ""
            else:
                failed += 1
                tm_failed += 1
                icon = FAIL_ICON
                suffix = " in-progress" if cycles == 0 else ""

            print(f"    [{icon}] {fid}: {desc}  ({cycles} {cycle_label}){suffix}")

        # Teammate summary line
        tm_total = len(features)
        parts = [f"{tm_passed}/{tm_total} passed"]
        if tm_blocked:
            parts.append(f"{tm_blocked} blocked")
        if tm_failed:
            parts.append(f"{tm_failed} remaining")
        print(f"  {name}: {', '.join(parts)}")
        print()

    if total > 0:
        resolved = passed + blocked
        pct = int(resolved / total * 100)
        summary = f"  Progress: {passed}/{total} features passed ({pct}% resolved)"
        if blocked:
            summary += f" | {blocked} blocked"
        if failed:
            summary += f" | {failed} remaining"
        print(summary)
    else:
        print("  Progress: no features defined")

    return True
