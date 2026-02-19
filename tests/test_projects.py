"""Tests for forja.projects — multi-project registry and portfolio dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from forja.projects import (
    ACTIVE_PATH,
    GLOBAL_DIR,
    REGISTRY_PATH,
    _derive_name,
    _format_ago,
    _inspect_health,
    _is_forja_project,
    _read_active,
    _read_registry,
    _write_active,
    _write_registry,
    auto_register,
    project_add,
    project_list,
    project_remove,
    project_select,
    project_show,
    resolve_project_dir,
    run_projects,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Redirect all global state to a temp directory."""
    fake_global = tmp_path / ".forja-global"
    fake_global.mkdir()

    monkeypatch.setattr("forja.projects.GLOBAL_DIR", fake_global)
    monkeypatch.setattr("forja.projects.REGISTRY_PATH", fake_global / "projects.json")
    monkeypatch.setattr("forja.projects.ACTIVE_PATH", fake_global / "active")

    # Also ensure FORJA_PROJECT env var doesn't leak
    monkeypatch.delenv("FORJA_PROJECT", raising=False)

    return fake_global


@pytest.fixture
def forja_project(tmp_path):
    """Create a minimal Forja project directory."""
    proj = tmp_path / "my-project"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("# Project\n")
    (proj / ".forja-tools").mkdir()
    return proj


@pytest.fixture
def forja_project_with_features(forja_project):
    """Create a Forja project with feature data."""
    teammates = forja_project / "context" / "teammates" / "builder"
    teammates.mkdir(parents=True)
    features_data = {
        "features": [
            {"id": "F1", "description": "Feature 1", "status": "passed", "cycles": 2},
            {"id": "F2", "description": "Feature 2", "status": "passed", "cycles": 1},
            {"id": "F3", "description": "Feature 3", "status": "failed", "cycles": 3},
        ]
    }
    (teammates / "features.json").write_text(json.dumps(features_data))

    # Also create outcome report
    forja_dir = forja_project / ".forja"
    forja_dir.mkdir(exist_ok=True)
    outcome = {"coverage_pct": 67}
    (forja_dir / "outcome-report.json").write_text(json.dumps(outcome))

    return forja_project


# ── Registry I/O ─────────────────────────────────────────────────────


class TestRegistryIO:
    def test_read_empty_registry(self):
        assert _read_registry() == {}

    def test_write_and_read(self, isolated_registry):
        data = {"proj": {"path": "/tmp/proj", "created": "2026-01-01"}}
        _write_registry(data)
        assert _read_registry() == data

    def test_read_corrupt_json(self, isolated_registry):
        (isolated_registry / "projects.json").write_text("not json!!")
        assert _read_registry() == {}

    def test_read_non_dict(self, isolated_registry):
        (isolated_registry / "projects.json").write_text('"just a string"')
        assert _read_registry() == {}

    def test_active_empty(self):
        assert _read_active() == ""

    def test_active_write_read(self, isolated_registry):
        _write_active("my-proj")
        assert _read_active() == "my-proj"


# ── Detection helpers ────────────────────────────────────────────────


class TestHelpers:
    def test_is_forja_project_true(self, forja_project):
        assert _is_forja_project(forja_project) is True

    def test_is_forja_project_false(self, tmp_path):
        assert _is_forja_project(tmp_path) is False

    def test_derive_name(self, tmp_path):
        proj = tmp_path / "cool-app"
        proj.mkdir()
        assert _derive_name(proj) == "cool-app"

    def test_format_ago_none(self):
        assert _format_ago(None) == "—"

    def test_format_ago_recent(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(tz=timezone.utc)
        assert _format_ago(now - timedelta(seconds=30)) == "just now"
        assert "min ago" in _format_ago(now - timedelta(minutes=5))
        assert "h ago" in _format_ago(now - timedelta(hours=3))
        assert "d ago" in _format_ago(now - timedelta(days=10))


# ── project_add ──────────────────────────────────────────────────────


class TestProjectAdd:
    def test_add_forja_project(self, forja_project, capsys):
        assert project_add(str(forja_project)) is True
        reg = _read_registry()
        assert "my-project" in reg
        assert reg["my-project"]["path"] == str(forja_project)
        # Should also set as active (first project)
        assert _read_active() == "my-project"

    def test_add_with_alias(self, forja_project, capsys):
        assert project_add(str(forja_project), name="custom-name") is True
        reg = _read_registry()
        assert "custom-name" in reg

    def test_add_nonexistent_dir(self, capsys):
        assert project_add("/nonexistent/path") is False

    def test_add_duplicate_name(self, forja_project, capsys):
        project_add(str(forja_project))
        # Same path same name — idempotent
        assert project_add(str(forja_project)) is True

    def test_add_duplicate_name_different_path(self, forja_project, tmp_path, capsys):
        project_add(str(forja_project))
        other = tmp_path / "other"
        other.mkdir()
        assert project_add(str(other), name="my-project") is False

    def test_add_non_forja_warns(self, tmp_path, capsys):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert project_add(str(plain)) is True
        out = capsys.readouterr().out
        assert "Warning" in out


# ── project_remove ───────────────────────────────────────────────────


class TestProjectRemove:
    def test_remove_existing(self, forja_project, capsys):
        project_add(str(forja_project))
        assert project_remove("my-project") is True
        assert _read_registry() == {}

    def test_remove_nonexistent(self, capsys):
        assert project_remove("ghost") is False

    def test_remove_clears_active(self, forja_project, capsys):
        project_add(str(forja_project))
        assert _read_active() == "my-project"
        project_remove("my-project")
        assert _read_active() == ""


# ── project_select ───────────────────────────────────────────────────


class TestProjectSelect:
    def test_select(self, forja_project, tmp_path, capsys):
        project_add(str(forja_project))
        other = tmp_path / "other"
        other.mkdir()
        (other / "CLAUDE.md").write_text("# Other\n")
        (other / ".forja-tools").mkdir()
        project_add(str(other), name="other")

        assert project_select("other") is True
        assert _read_active() == "other"

    def test_select_nonexistent(self, capsys):
        assert project_select("nope") is False

    def test_select_already_active(self, forja_project, capsys):
        project_add(str(forja_project))
        assert project_select("my-project") is True
        out = capsys.readouterr().out
        assert "already" in out

    def test_select_dash_toggles(self, forja_project, tmp_path, capsys):
        project_add(str(forja_project))
        other = tmp_path / "other"
        other.mkdir()
        (other / "CLAUDE.md").write_text("# Other\n")
        (other / ".forja-tools").mkdir()
        project_add(str(other), name="other")

        project_select("other")
        assert _read_active() == "other"

        project_select("-")
        assert _read_active() == "my-project"

    def test_select_dash_no_previous(self, capsys):
        assert project_select("-") is False


# ── project_show ─────────────────────────────────────────────────────


class TestProjectShow:
    def test_show_no_active(self, capsys):
        assert project_show() is True
        out = capsys.readouterr().out
        assert "No active project" in out

    def test_show_active(self, forja_project, capsys):
        project_add(str(forja_project))
        assert project_show() is True
        out = capsys.readouterr().out
        assert "my-project" in out


# ── project_list ─────────────────────────────────────────────────────


class TestProjectList:
    def test_list_empty(self, capsys):
        assert project_list() is True
        out = capsys.readouterr().out
        assert "No projects registered" in out

    def test_list_with_projects(self, forja_project, capsys):
        project_add(str(forja_project))
        assert project_list() is True
        out = capsys.readouterr().out
        assert "my-project" in out
        assert "1 project" in out

    def test_list_with_features(self, forja_project_with_features, capsys):
        project_add(str(forja_project_with_features), name="featured")
        assert project_list() is True
        out = capsys.readouterr().out
        assert "featured" in out
        assert "67%" in out
        assert "2/3" in out


# ── Health inspection ────────────────────────────────────────────────


class TestInspectHealth:
    def test_missing_dir(self, tmp_path):
        health = _inspect_health(tmp_path / "nonexistent")
        assert health["exists"] is False

    def test_non_forja_dir(self, tmp_path):
        health = _inspect_health(tmp_path)
        assert health["is_forja"] is False

    def test_forja_not_built(self, forja_project):
        health = _inspect_health(forja_project)
        assert health["is_forja"] is True
        assert health["features_total"] == 0
        assert "not built" in health["status_label"]

    def test_forja_with_features(self, forja_project_with_features):
        health = _inspect_health(forja_project_with_features)
        assert health["features_total"] == 3
        assert health["features_passed"] == 2
        assert health["coverage"] == 67


# ── auto_register ────────────────────────────────────────────────────


class TestAutoRegister:
    def test_auto_register(self, forja_project):
        auto_register(forja_project)
        reg = _read_registry()
        assert "my-project" in reg

    def test_auto_register_idempotent(self, forja_project):
        auto_register(forja_project)
        auto_register(forja_project)
        reg = _read_registry()
        assert len(reg) == 1

    def test_auto_register_name_collision(self, forja_project, tmp_path):
        auto_register(forja_project)
        # Create another project with same directory name
        other_parent = tmp_path / "other"
        other_parent.mkdir()
        other = other_parent / "my-project"
        other.mkdir()
        auto_register(other)
        reg = _read_registry()
        assert len(reg) == 2
        assert "my-project-2" in reg


# ── resolve_project_dir ──────────────────────────────────────────────


class TestResolveProjectDir:
    def test_explicit_name(self, forja_project, capsys):
        project_add(str(forja_project))
        result = resolve_project_dir("my-project")
        assert result == forja_project

    def test_env_var(self, forja_project, monkeypatch, capsys):
        project_add(str(forja_project))
        monkeypatch.setenv("FORJA_PROJECT", "my-project")
        result = resolve_project_dir()
        assert result == forja_project

    def test_cwd_detection(self, forja_project, monkeypatch, capsys):
        monkeypatch.chdir(forja_project)
        result = resolve_project_dir()
        assert result == forja_project

    def test_active_project_fallback(self, forja_project, tmp_path, monkeypatch, capsys):
        project_add(str(forja_project))
        # chdir somewhere that is NOT a forja project
        not_forja = tmp_path / "empty-dir"
        not_forja.mkdir()
        monkeypatch.chdir(not_forja)
        result = resolve_project_dir()
        assert result == forja_project

    def test_nonexistent_name(self, capsys):
        result = resolve_project_dir("nope")
        assert result is None

    def test_no_project_at_all(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        result = resolve_project_dir()
        assert result is None


# ── run_projects dispatch ────────────────────────────────────────────


class TestRunProjects:
    def test_ls(self, capsys):
        assert run_projects("ls") is True

    def test_add_no_path(self, capsys):
        assert run_projects("add") is False

    def test_remove_no_name(self, capsys):
        assert run_projects("remove") is False

    def test_select_no_name(self, capsys):
        assert run_projects("select") is False

    def test_show(self, capsys):
        assert run_projects("show") is True

    def test_unknown_action(self, capsys):
        assert run_projects("foobar") is False
