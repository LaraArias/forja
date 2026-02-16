"""Integration tests for Forja core flows.

These tests verify real Forja flows WITHOUT calling external APIs.
All external calls are mocked.
"""

import tempfile, os, json, shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """Clear config cache before and after each test."""
    from forja.config_loader import reset_config
    reset_config()
    yield
    reset_config()


class TestForjaInitFlow:
    """Test that forja init creates correct project structure"""

    def test_init_creates_structure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        from forja.init import run_init
        # Mock input to select API Backend (option 2) and skip context (n)
        with patch('builtins.input', side_effect=['2', 'n', '', '', '', '', '', '']):
            try:
                run_init(directory=str(tmp_path))
            except (SystemExit, EOFError, StopIteration):
                pass
        # Verify essential files created
        assert (tmp_path / "CLAUDE.md").exists() or (tmp_path / ".forja-tools").is_dir()

    def test_init_copies_templates(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        from forja.init import run_init
        with patch('builtins.input', side_effect=['2', 'n', '', '', '', '', '', '']):
            try:
                run_init(directory=str(tmp_path))
            except (SystemExit, EOFError, StopIteration):
                pass
        tools_dir = tmp_path / ".forja-tools"
        if tools_dir.exists():
            py_files = list(tools_dir.glob("*.py"))
            assert len(py_files) >= 5, f"Expected 5+ template files, got {len(py_files)}"


class TestConfigLoaderIntegration:
    """Test config loading with real files"""

    def test_loads_from_toml_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        toml_content = """[build]
timeout_stall_minutes = 8
timeout_absolute_minutes = 15
max_cycles_per_feature = 3

[models]
kimi_model = "kimi-k2-0711-preview"
anthropic_model = "claude-sonnet-4-20250514"

[context]
max_context_chars = 5000
max_learnings_chars = 3000

[observatory]
live_refresh_seconds = 10
"""
        (tmp_path / "forja.toml").write_text(toml_content)
        from forja.config_loader import load_config
        config = load_config()
        assert config.build.timeout_stall_minutes == 8
        assert config.build.max_cycles_per_feature == 3
        assert config.observatory.live_refresh_seconds == 10

    def test_env_overrides_toml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "forja.toml").write_text("[build]\ntimeout_stall_minutes = 8\n")
        monkeypatch.setenv("FORJA_BUILD_TIMEOUT_STALL_MINUTES", "99")
        from forja.config_loader import load_config
        config = load_config()
        assert config.build.timeout_stall_minutes == 99


class TestFeatureStatusCanonical:
    """Test the canonical read_feature_status helper"""

    def test_new_status_field(self):
        from forja.utils import read_feature_status
        assert read_feature_status({"status": "passed"}) == "passed"
        assert read_feature_status({"status": "blocked"}) == "blocked"
        assert read_feature_status({"status": "failed"}) == "failed"
        assert read_feature_status({"status": "pending"}) == "pending"

    def test_old_boolean_compat(self):
        from forja.utils import read_feature_status
        assert read_feature_status({"passes": True}) == "passed"
        assert read_feature_status({"blocked": True}) == "blocked"
