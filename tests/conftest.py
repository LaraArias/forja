"""Shared fixtures for Forja tests."""

import os
import sys
from pathlib import Path

# Ensure src/ is at the FRONT of sys.path so the forja package is found
# before the forja.py script in the project root.
# Also remove the project root from sys.path temporarily to avoid shadowing.
_project_root = str(Path(__file__).resolve().parent.parent)
_src_dir = str(Path(__file__).resolve().parent.parent / "src")

# Remove project root if present (it shadows the package with forja.py)
while _project_root in sys.path:
    sys.path.remove(_project_root)

# Insert src at the front
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

# Re-add project root AFTER src
sys.path.append(_project_root)

# If forja was already imported as a module (from forja.py), invalidate it
if "forja" in sys.modules and not hasattr(sys.modules["forja"], "__path__"):
    del sys.modules["forja"]

import pytest


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """Provide a temporary directory and clean environment for tests.

    Sets CWD to tmp_path and clears API key env vars.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("SAPTIVA_API_KEY", raising=False)
    return tmp_path


@pytest.fixture
def env_file(tmp_path):
    """Create a temporary .env file and return its path."""
    env_path = tmp_path / ".env"

    def _write(content: str) -> Path:
        env_path.write_text(content, encoding="utf-8")
        return env_path

    return _write
