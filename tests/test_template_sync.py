"""Test that critical functions stay in sync between CLI and template utils.

Runs scripts/verify_template_sync.py and asserts exit 0 (no drift).
"""

import subprocess
import sys


def test_critical_functions_in_sync():
    result = subprocess.run(
        [sys.executable, "scripts/verify_template_sync.py"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Template drift detected:\n{result.stdout}"
