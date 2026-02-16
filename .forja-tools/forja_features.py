#!/usr/bin/env python3
"""Forja feature tracker per teammate.

Usage:
    python3 .forja-tools/forja_features.py attempt <feature-id> --dir <path>
    python3 .forja-tools/forja_features.py pass <feature-id> --dir <path>
    python3 .forja-tools/forja_features.py status --dir <path>
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_features(dir_path):
    """Load features.json from dir_path. Exit 1 if missing or invalid."""
    fpath = Path(dir_path) / "features.json"
    if not fpath.exists():
        print(f"ERROR: No existe {fpath}")
        sys.exit(1)
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: features.json inválido: {e}")
        sys.exit(1)
    return data, fpath


def save_features(data, fpath):
    """Write features.json back to disk."""
    fpath.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_feature(data, feature_id):
    """Find feature by id. Exit 1 if not found."""
    for feat in data.get("features", []):
        if feat.get("id") == feature_id:
            return feat
    print(f"ERROR: Feature '{feature_id}' no encontrado")
    sys.exit(1)


def cmd_attempt(feature_id, dir_path):
    """Increment cycles for a feature."""
    data, fpath = load_features(dir_path)
    feat = find_feature(data, feature_id)
    feat["cycles"] = feat.get("cycles", 0) + 1
    save_features(data, fpath)
    print(f"Feature {feature_id}: ciclo {feat['cycles']}")


def cmd_pass(feature_id, dir_path):
    """Mark a feature as passed."""
    data, fpath = load_features(dir_path)
    feat = find_feature(data, feature_id)
    feat["status"] = "passed"
    feat["passed_at"] = datetime.now(timezone.utc).isoformat()
    save_features(data, fpath)
    print(f"Feature {feature_id}: PASSED")


def cmd_status(dir_path):
    """Print feature status table."""
    data, _ = load_features(dir_path)
    features = data.get("features", [])

    if not features:
        print("No hay features definidos.")
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
    for feat in features:
        fid = feat.get("id", "?")
        desc = feat.get("description", "")
        feat_status = feat.get("status", "pending")
        icon = "✔" if feat_status == "passed" else ("⊘" if feat_status == "blocked" else "✘")
        cycles = feat.get("cycles", 0)
        if feat_status == "passed":
            passed += 1
        print(f"{fid:<{id_w}}  {desc:<{desc_w}}  {icon:<8}  {cycles}")

    print()
    print(f"{passed}/{len(features)} features completados")


def parse_dir_flag(args):
    """Extract --dir value from args."""
    for i, arg in enumerate(args):
        if arg == "--dir" and i + 1 < len(args):
            return args[i + 1]
    print("ERROR: Falta --dir <path>")
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Uso:")
        print("  python3 .forja-tools/forja_features.py attempt <feature-id> --dir <path>")
        print("  python3 .forja-tools/forja_features.py pass <feature-id> --dir <path>")
        print("  python3 .forja-tools/forja_features.py status --dir <path>")
        sys.exit(1)

    command = sys.argv[1]

    if command == "attempt":
        if len(sys.argv) < 3:
            print("ERROR: Falta feature-id")
            sys.exit(1)
        feature_id = sys.argv[2]
        dir_path = parse_dir_flag(sys.argv[3:])
        cmd_attempt(feature_id, dir_path)

    elif command == "pass":
        if len(sys.argv) < 3:
            print("ERROR: Falta feature-id")
            sys.exit(1)
        feature_id = sys.argv[2]
        dir_path = parse_dir_flag(sys.argv[3:])
        cmd_pass(feature_id, dir_path)

    elif command == "status":
        dir_path = parse_dir_flag(sys.argv[2:])
        cmd_status(dir_path)

    else:
        print(f"ERROR: Comando desconocido '{command}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
