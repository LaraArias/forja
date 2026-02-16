#!/usr/bin/env python3
"""Forja status - show feature progress across all teammates.

Safe for live runs: each features.json is read independently with
try/except. Partial writes, missing files, and corrupt JSON are all
handled gracefully without crashing.
"""

from __future__ import annotations

import json
from pathlib import Path

from forja.constants import CLAUDE_MD, FORJA_TOOLS, TEAMMATES_DIR, WORKFLOW_PATH
from forja.utils import FAIL_ICON, PASS_ICON, WARN_ICON, Feature, read_feature_status


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


def _load_workflow():
    """Load workflow.json if it exists. Returns (phases_list, agent_order_dict) or (None, None)."""
    if not WORKFLOW_PATH.exists():
        return None, None
    try:
        data = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        phases = data.get("phases", [])
        if not phases:
            return None, None
        # Map agent name -> phase order (1-based)
        order = {}
        for i, phase in enumerate(phases):
            order[phase.get("agent", "")] = i + 1
        return phases, order
    except (json.JSONDecodeError, OSError):
        return None, None


def _show_workflow_status(phases, order) -> bool:
    """Show status with workflow phase ordering."""
    teammates_dir = TEAMMATES_DIR

    print("Forja Status (workflow mode)")
    print("============================\n")

    total = 0
    passed = 0

    for phase in phases:
        agent_name = phase.get("agent", "?")
        phase_num = order.get(agent_name, 0)
        output = phase.get("output", "")
        role = phase.get("role", agent_name)

        features_path = teammates_dir / agent_name / "features.json"
        file_status, features = _load_features_safe(features_path)

        # Determine phase status from features
        if file_status != "ok" or not features:
            icon = "\u25cb"  # ○ waiting
            label = "(waiting)"
        else:
            feat = Feature.from_dict(features[0])
            total += 1
            if feat.status == "passed":
                passed += 1
                icon = "\u2714"  # ✔
                label = f"({output})" if output else ""
            elif feat.status == "blocked":
                icon = "\u26a0"  # ⚠
                label = "(blocked)"
            elif feat.cycles > 0:
                icon = "\u23f3"  # ⏳
                label = f"({output})" if output else "(in progress)"
            else:
                icon = "\u25cb"  # ○
                label = "(waiting)"

        print(f"  Phase {phase_num}: {agent_name} {icon} {label}")

    print()
    if total > 0:
        pct = int(passed / total * 100)
        print(f"  Progress: {passed}/{total} phases complete ({pct}%)")
    else:
        print("  Progress: phases not started")

    return True


def show_status() -> bool:
    """Main status entrypoint."""
    if not _check_project():
        return False

    # Check for workflow mode first
    phases, order = _load_workflow()
    if phases is not None:
        return _show_workflow_status(phases, order)

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

        for feat_dict in features:
            feat = Feature.from_dict(feat_dict)
            fid = feat.id or "?"
            desc = feat.description
            feat_status = feat.status
            cycles = feat.cycles

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
