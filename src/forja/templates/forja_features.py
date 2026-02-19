#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja feature tracker per teammate.

Usage:
    python3 .forja-tools/forja_features.py attempt <feature-id> --dir <path>
    python3 .forja-tools/forja_features.py pass <feature-id> --dir <path> [--evidence <text>]
    python3 .forja-tools/forja_features.py status --dir <path>
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from forja_utils import Feature

MAX_CYCLES = 5
VALID_STATES = ("pending", "passed", "failed", "blocked")
EVENT_LOG = Path(".forja") / "feature-events.jsonl"
EVENT_STREAM = Path(".forja") / "event-stream.jsonl"


def _emit_event(event_type, data, agent="system"):
    """Append a structured event to the unified event stream."""
    try:
        EVENT_STREAM.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "id": f"{event_type}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "agent": agent,
            "data": data,
        }
        with open(EVENT_STREAM, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


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
    feat_dict = find_feature(data, feature_id)
    feat = Feature.from_dict(feat_dict)

    if feat.status == "blocked":
        print(f"[BLOCKED] {feat.display_name} is blocked after {feat.cycles} failed cycles - skipping",
              file=sys.stderr)
        return

    feat.cycles += 1
    feat.status = "failed"
    feat_dict.update(feat.to_dict())
    save_features(data, fpath)
    print(f"Feature {feature_id}: cycle {feat.cycles}")
    _log_event(feature_id, "failed", cycle=feat.cycles)
    _emit_event("feature.failed", {"feature_id": feature_id, "cycle": feat.cycles})

    if feat.cycles >= MAX_CYCLES:
        feat.status = "blocked"
        feat.blocked_at = datetime.now(timezone.utc).isoformat()
        feat_dict.update(feat.to_dict())
        save_features(data, fpath)
        print(f"[BLOCKED] {feat.display_name} after {MAX_CYCLES} failed cycles - skipping",
              file=sys.stderr)
        _log_event(feature_id, "blocked", cycle=feat.cycles,
                   reason=f"exceeded {MAX_CYCLES} cycles")
        _emit_event("feature.blocked", {
            "feature_id": feature_id, "cycle": feat.cycles,
            "reason": f"exceeded {MAX_CYCLES} cycles",
        })


def cmd_pass(feature_id, dir_path, evidence=None):
    """Mark a feature as passed, optionally with evidence."""
    data, fpath = load_features(dir_path)
    feat_dict = find_feature(data, feature_id)
    feat = Feature.from_dict(feat_dict)

    if feat.status == "blocked":
        print(f"[WARN] Blocked feature '{feat.display_name}' cannot be re-passed",
              file=sys.stderr)
        return

    feat.status = "passed"
    feat.passed_at = datetime.now(timezone.utc).isoformat()
    if evidence:
        feat.evidence = evidence
    feat_dict.update(feat.to_dict())
    save_features(data, fpath)
    print(f"Feature {feature_id}: PASSED")
    _log_event(feature_id, "passed", cycle=feat.cycles,
               reason=evidence or "")
    _emit_event("feature.passed", {
        "feature_id": feature_id, "cycle": feat.cycles,
        "evidence": evidence or "",
    })


def cmd_status(dir_path):
    """Print feature status table."""
    data, _ = load_features(dir_path)
    raw_features = data.get("features", [])

    if not raw_features:
        print("No features defined.")
        return

    features = [Feature.from_dict(f) for f in raw_features]
    has_evidence = any(f.evidence for f in features)

    # Column widths
    id_w = max(len(f.id) for f in features)
    id_w = max(id_w, 2)
    desc_w = max(len(f.description) for f in features)
    desc_w = max(desc_w, 11)

    header = f"{'id':<{id_w}}  {'description':<{desc_w}}  {'status':<8}  {'cycles'}"
    if has_evidence:
        header += "  evidence"
    print(header)
    print("-" * len(header))

    passed = 0
    blocked = 0
    for feat in features:
        if feat.status == "blocked":
            icon = "\u2298"
            blocked += 1
        elif feat.status == "passed":
            icon = "\u2714"
            passed += 1
        else:
            icon = "\u2718"
        line = f"{feat.id:<{id_w}}  {feat.description:<{desc_w}}  {icon:<8}  {feat.cycles}"
        if has_evidence:
            ev = (feat.evidence or "")[:40]
            line += f"  {ev}"
        print(line)

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


def parse_evidence_flag(args):
    """Extract --evidence value from args. Returns None if not provided."""
    for i, arg in enumerate(args):
        if arg == "--evidence" and i + 1 < len(args):
            return args[i + 1]
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 .forja-tools/forja_features.py attempt <feature-id> --dir <path>")
        print("  python3 .forja-tools/forja_features.py pass <feature-id> --dir <path> [--evidence <text>]")
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
        remaining = sys.argv[3:]
        dir_path = parse_dir_flag(remaining)
        evidence = parse_evidence_flag(remaining)
        cmd_pass(feature_id, dir_path, evidence=evidence)

    elif command == "status":
        dir_path = parse_dir_flag(sys.argv[2:])
        cmd_status(dir_path)

    else:
        print(f"ERROR: Unknown command '{command}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
