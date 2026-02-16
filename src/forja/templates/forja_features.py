#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja feature tracker per teammate.

Usage:
    python3 .forja-tools/forja_features.py attempt <feature-id> --dir <path>
    python3 .forja-tools/forja_features.py pass <feature-id> --dir <path>
    python3 .forja-tools/forja_features.py status --dir <path>
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MAX_CYCLES = 5
VALID_STATES = ("pending", "passed", "failed", "blocked")
EVENT_LOG = Path(".forja") / "feature-events.jsonl"


def _log_event(feature_id, event, cycle=0, reason=""):
    """Append a structured event to .forja/feature-events.jsonl."""
    try:
        EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "feature": feature_id,
            "event": event,
            "cycle": cycle,
            "reason": reason,
        }
        with open(EVENT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"  warning: could not write event log: {e}", file=sys.stderr)


def load_features(dir_path):
    """Load features.json from dir_path. Exit 1 if missing or invalid."""
    fpath = Path(dir_path) / "features.json"
    if not fpath.exists():
        print(f"ERROR: Not found: {fpath}")
        sys.exit(1)
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid features.json: {e}")
        sys.exit(1)
    return data, fpath


def save_features(data, fpath):
    """Write features.json back to disk atomically."""
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(fpath.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.rename(tmp_path, str(fpath))
    except BaseException:
        os.unlink(tmp_path)
        raise


def find_feature(data, feature_id):
    """Find feature by id. Exit 1 if not found."""
    for feat in data.get("features", []):
        if feat.get("id") == feature_id:
            return feat
    print(f"ERROR: Feature '{feature_id}' not found")
    sys.exit(1)


def cmd_attempt(feature_id, dir_path):
    """Increment cycles for a feature. Blocks after MAX_CYCLES failures."""
    data, fpath = load_features(dir_path)
    feat = find_feature(data, feature_id)

    if feat.get("status") == "blocked":
        desc = feat.get("description", feature_id)
        print(f"[BLOCKED] {desc} is blocked after {feat.get('cycles', 0)} failed cycles - skipping",
              file=sys.stderr)
        return

    feat["cycles"] = feat.get("cycles", 0) + 1
    feat["status"] = "failed"
    save_features(data, fpath)
    print(f"Feature {feature_id}: cycle {feat['cycles']}")
    _log_event(feature_id, "failed", cycle=feat["cycles"])

    if feat["cycles"] >= MAX_CYCLES:
        feat["status"] = "blocked"
        feat["blocked_at"] = datetime.now(timezone.utc).isoformat()
        save_features(data, fpath)
        desc = feat.get("description", feature_id)
        print(f"[BLOCKED] {desc} after {MAX_CYCLES} failed cycles - skipping",
              file=sys.stderr)
        _log_event(feature_id, "blocked", cycle=feat["cycles"],
                   reason=f"exceeded {MAX_CYCLES} cycles")


def cmd_pass(feature_id, dir_path):
    """Mark a feature as passed."""
    data, fpath = load_features(dir_path)
    feat = find_feature(data, feature_id)

    if feat.get("status") == "blocked":
        desc = feat.get("description", feature_id)
        print(f"[WARN] Blocked feature '{desc}' cannot be re-passed",
              file=sys.stderr)
        return

    feat["status"] = "passed"
    feat["passed_at"] = datetime.now(timezone.utc).isoformat()
    save_features(data, fpath)
    print(f"Feature {feature_id}: PASSED")
    _log_event(feature_id, "passed", cycle=feat.get("cycles", 0))


def cmd_status(dir_path):
    """Print feature status table."""
    data, _ = load_features(dir_path)
    features = data.get("features", [])

    if not features:
        print("No features defined.")
        return

    # Column widths
    id_w = max(len(f.get("id", "")) for f in features)
    id_w = max(id_w, 2)
    desc_w = max(len(f.get("description", "")) for f in features)
    desc_w = max(desc_w, 11)

    header = f"{'id':<{id_w}}  {'description':<{desc_w}}  {'status':<8}  {'cycles'}"
    print(header)
    print("-" * len(header))

    passed = 0
    blocked = 0
    for feat in features:
        fid = feat.get("id", "?")
        desc = feat.get("description", "")
        cycles = feat.get("cycles", 0)
        feat_status = feat.get("status", "pending")
        if feat_status == "blocked":
            icon = "⊘"
            blocked += 1
        elif feat_status == "passed":
            icon = "✔"
            passed += 1
        else:
            icon = "✘"
        print(f"{fid:<{id_w}}  {desc:<{desc_w}}  {icon:<8}  {cycles}")

    print()
    summary = f"{passed}/{len(features)} features completed"
    if blocked:
        summary += f" ({blocked} blocked)"
    print(summary)


def parse_dir_flag(args):
    """Extract --dir value from args."""
    for i, arg in enumerate(args):
        if arg == "--dir" and i + 1 < len(args):
            return args[i + 1]
    print("ERROR: Missing --dir <path>")
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 .forja-tools/forja_features.py attempt <feature-id> --dir <path>")
        print("  python3 .forja-tools/forja_features.py pass <feature-id> --dir <path>")
        print("  python3 .forja-tools/forja_features.py status --dir <path>")
        sys.exit(1)

    command = sys.argv[1]

    if command == "attempt":
        if len(sys.argv) < 3:
            print("ERROR: Missing feature-id")
            sys.exit(1)
        feature_id = sys.argv[2]
        dir_path = parse_dir_flag(sys.argv[3:])
        cmd_attempt(feature_id, dir_path)

    elif command == "pass":
        if len(sys.argv) < 3:
            print("ERROR: Missing feature-id")
            sys.exit(1)
        feature_id = sys.argv[2]
        dir_path = parse_dir_flag(sys.argv[3:])
        cmd_pass(feature_id, dir_path)

    elif command == "status":
        dir_path = parse_dir_flag(sys.argv[2:])
        cmd_status(dir_path)

    else:
        print(f"ERROR: Unknown command '{command}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
