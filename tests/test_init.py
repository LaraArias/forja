"""Tests for forja.init module."""

import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestGetTemplate:
    """Tests for get_template function."""

    def test_reads_existing_template(self):
        from forja.init import get_template
        # CLAUDE.md is a known template
        content = get_template("CLAUDE.md")
        assert len(content) > 0
        assert "Forja" in content

    def test_forja_utils_template_exists(self):
        from forja.init import get_template
        content = get_template("forja_utils.py")
        assert "load_dotenv" in content
        assert "call_kimi" in content


class TestProjectPermissions:
    """Tests for _configure_project_permissions."""

    def test_creates_settings_file(self, tmp_path):
        from forja.init import _configure_project_permissions

        _configure_project_permissions(tmp_path)

        settings_path = tmp_path / ".claude" / "settings.local.json"
        assert settings_path.exists()

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "permissions" in settings
        assert "allow" in settings["permissions"]
        perms = settings["permissions"]["allow"]
        assert "Bash(python3:*)" in perms
        assert "Read(*)" in perms
        assert "Write(*)" in perms

    def test_does_not_write_global_settings(self, tmp_path):
        """CRITICAL: Never write to ~/.claude/settings.json."""
        from forja.init import _configure_project_permissions

        _configure_project_permissions(tmp_path)

        # The function should ONLY write to project-local settings
        settings_path = tmp_path / ".claude" / "settings.local.json"
        assert settings_path.exists()

    def test_idempotent(self, tmp_path):
        """Running twice doesn't duplicate permissions."""
        from forja.init import _configure_project_permissions

        _configure_project_permissions(tmp_path)
        _configure_project_permissions(tmp_path)

        settings_path = tmp_path / ".claude" / "settings.local.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        perms = settings["permissions"]["allow"]

        # No duplicates
        assert len(perms) == len(set(perms))

    def test_preserves_existing_settings(self, tmp_path):
        """Doesn't destroy existing settings."""
        from forja.init import _configure_project_permissions

        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True)
        settings_path = settings_dir / "settings.local.json"
        settings_path.write_text(
            json.dumps({"custom_key": "preserved", "permissions": {"allow": ["Bash(npm:*)"]}}),
            encoding="utf-8",
        )

        _configure_project_permissions(tmp_path)

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert settings["custom_key"] == "preserved"
        assert "Bash(npm:*)" in settings["permissions"]["allow"]
        assert "Bash(python3:*)" in settings["permissions"]["allow"]


class TestCreateDirs:
    """Tests for _create_dirs."""

    def test_creates_all_dirs(self, tmp_path):
        from forja.init import _create_dirs, DIRS_TO_CREATE

        _create_dirs(tmp_path)

        for d in DIRS_TO_CREATE:
            assert (tmp_path / d).is_dir(), f"Missing directory: {d}"


class TestCheckExisting:
    """Tests for _check_existing."""

    def test_no_markers(self, tmp_path):
        from forja.init import _check_existing
        assert _check_existing(tmp_path) == []

    def test_finds_claude_md(self, tmp_path):
        from forja.init import _check_existing
        (tmp_path / "CLAUDE.md").write_text("# test", encoding="utf-8")
        found = _check_existing(tmp_path)
        assert "CLAUDE.md" in found

    def test_finds_forja_tools(self, tmp_path):
        from forja.init import _check_existing
        (tmp_path / ".forja-tools").mkdir()
        found = _check_existing(tmp_path)
        assert ".forja-tools" in found


class TestTemplatesList:
    """Verify TEMPLATES list includes forja_utils.py."""

    def test_forja_utils_in_templates(self):
        from forja.init import TEMPLATES
        template_sources = [src for src, _ in TEMPLATES]
        assert "forja_utils.py" in template_sources

    def test_all_templates_have_targets(self):
        from forja.init import TEMPLATES
        for src, dest in TEMPLATES:
            assert src, "Template source cannot be empty"
            assert dest, "Template destination cannot be empty"


class TestInitDirectoryValidation:
    """Verify run_init rejects dangerous directories."""

    def test_rejects_root(self):
        from forja.init import run_init
        assert run_init(directory="/", force=True) is False

    def test_rejects_etc(self):
        from forja.init import run_init
        assert run_init(directory="/etc", force=True) is False

    def test_rejects_home(self):
        from forja.init import run_init
        home = str(Path.home())
        assert run_init(directory=home, force=True) is False

    def test_rejects_tmp(self):
        from forja.init import run_init
        assert run_init(directory="/tmp", force=True) is False


class TestCopySkill:
    """Tests for _copy_skill copying agents.json and workflow.json."""

    def test_copies_agents_and_workflow(self, tmp_path):
        from forja.init import _copy_skill, FORJA_TOOLS
        tools_dir = tmp_path / FORJA_TOOLS
        tools_dir.mkdir(parents=True)

        _copy_skill(tmp_path, "landing-page")

        skill_json = tools_dir / "skill.json"
        workflow_json = tools_dir / "workflow.json"

        assert skill_json.exists()
        assert workflow_json.exists()

        skill_data = json.loads(skill_json.read_text(encoding="utf-8"))
        assert skill_data["skill"] == "landing-page"

        workflow_data = json.loads(workflow_json.read_text(encoding="utf-8"))
        assert workflow_data["name"] == "landing-page"
        assert workflow_data["execution"] == "sequential"
        assert len(workflow_data["phases"]) == 5

    def test_copies_api_backend_workflow(self, tmp_path):
        from forja.init import _copy_skill, FORJA_TOOLS
        tools_dir = tmp_path / FORJA_TOOLS
        tools_dir.mkdir(parents=True)

        _copy_skill(tmp_path, "api-backend")

        workflow_json = tools_dir / "workflow.json"
        assert workflow_json.exists()

        workflow_data = json.loads(workflow_json.read_text(encoding="utf-8"))
        assert workflow_data["name"] == "api-backend"
        assert len(workflow_data["phases"]) == 5
        agent_names = [p["agent"] for p in workflow_data["phases"]]
        assert "architect" in agent_names
        assert "qa" in agent_names

    def test_handles_missing_skill(self, tmp_path):
        from forja.init import _copy_skill, FORJA_TOOLS
        tools_dir = tmp_path / FORJA_TOOLS
        tools_dir.mkdir(parents=True)

        # Should not crash on nonexistent skill
        _copy_skill(tmp_path, "nonexistent-skill")

        assert not (tools_dir / "skill.json").exists()
        assert not (tools_dir / "workflow.json").exists()
