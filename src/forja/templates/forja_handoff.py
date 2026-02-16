#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja Handoff - Read and write artifacts between workflow phases.

Usage:
    python3 .forja-tools/forja_handoff.py read copy-brief.md
    python3 .forja-tools/forja_handoff.py write design-spec.md < content
    python3 .forja-tools/forja_handoff.py list
    python3 .forja-tools/forja_handoff.py validate copy-brief.md
"""

import sys
from pathlib import Path

ARTIFACTS_DIR = Path("artifacts")


def cmd_read(name):
    """Read an artifact by name. Exits 1 if not found."""
    path = ARTIFACTS_DIR / name
    if not path.exists():
        print(
            f"ERROR: Artifact '{name}' not found. "
            "Previous phase may not have completed.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(path.read_text(encoding="utf-8"), end="")


def cmd_write(name):
    """Write stdin to an artifact file."""
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    content = sys.stdin.read()
    path = ARTIFACTS_DIR / name
    path.write_text(content, encoding="utf-8")
    print(f"Artifact saved: {path} ({len(content)} bytes)")


def cmd_list():
    """List all artifacts with sizes."""
    if not ARTIFACTS_DIR.exists():
        print("No artifacts yet.")
        return
    found = False
    for f in sorted(ARTIFACTS_DIR.iterdir()):
        if f.is_file():
            size = f.stat().st_size
            print(f"  {f.name} ({size} bytes)")
            found = True
    if not found:
        print("No artifacts yet.")


def cmd_validate(name):
    """Check that an artifact exists and is non-trivial (>=10 bytes)."""
    path = ARTIFACTS_DIR / name
    if not path.exists():
        print(f"FAIL: {name} does not exist")
        sys.exit(1)
    if path.stat().st_size < 10:
        print(f"FAIL: {name} is empty or too small ({path.stat().st_size} bytes)")
        sys.exit(1)
    print(f"OK: {name} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: forja_handoff.py [read|write|list|validate] [artifact_name]")
        sys.exit(1)

    cmd = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == "read" and name:
        cmd_read(name)
    elif cmd == "write" and name:
        cmd_write(name)
    elif cmd == "list":
        cmd_list()
    elif cmd == "validate" and name:
        cmd_validate(name)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
