"""Tests for forja_handoff.py template."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def handoff_env(tmp_path):
    """Set up a temp dir with forja_handoff.py copied in."""
    # Copy the template
    src = Path(__file__).resolve().parent.parent / "src" / "forja" / "templates" / "forja_handoff.py"
    dest = tmp_path / "forja_handoff.py"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def _run_handoff(cwd, *args, stdin_text=None):
    """Run forja_handoff.py as a subprocess."""
    result = subprocess.run(
        [sys.executable, "forja_handoff.py", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        input=stdin_text,
    )
    return result


class TestHandoffWriteAndRead:
    """Verify write then read round-trip."""

    def test_handoff_write_and_read(self, handoff_env):
        content = "This is the architecture document.\nWith multiple lines."
        # Write
        result = _run_handoff(handoff_env, "write", "architecture.md", stdin_text=content)
        assert result.returncode == 0
        assert "Artifact saved" in result.stdout
        # Verify file exists
        assert (handoff_env / "artifacts" / "architecture.md").exists()
        # Read
        result = _run_handoff(handoff_env, "read", "architecture.md")
        assert result.returncode == 0
        assert result.stdout == content


class TestHandoffValidate:
    """Verify validate checks existence and size."""

    def test_handoff_validate_missing_file(self, handoff_env):
        result = _run_handoff(handoff_env, "validate", "nonexistent.md")
        assert result.returncode == 1
        assert "FAIL" in result.stdout
        assert "does not exist" in result.stdout

    def test_handoff_validate_empty_file(self, handoff_env):
        artifacts = handoff_env / "artifacts"
        artifacts.mkdir()
        (artifacts / "tiny.md").write_text("short")
        result = _run_handoff(handoff_env, "validate", "tiny.md")
        assert result.returncode == 1
        assert "FAIL" in result.stdout
        assert "too small" in result.stdout

    def test_handoff_validate_valid_file(self, handoff_env):
        content = "A valid artifact with enough content to pass validation."
        _run_handoff(handoff_env, "write", "good.md", stdin_text=content)
        result = _run_handoff(handoff_env, "validate", "good.md")
        assert result.returncode == 0
        assert "OK" in result.stdout


class TestHandoffList:
    """Verify list shows artifacts."""

    def test_handoff_list_empty(self, handoff_env):
        result = _run_handoff(handoff_env, "list")
        assert result.returncode == 0
        assert "No artifacts" in result.stdout

    def test_handoff_list_with_files(self, handoff_env):
        _run_handoff(handoff_env, "write", "a.md", stdin_text="content for a")
        _run_handoff(handoff_env, "write", "b.md", stdin_text="content for b")
        result = _run_handoff(handoff_env, "list")
        assert result.returncode == 0
        assert "a.md" in result.stdout
        assert "b.md" in result.stdout
