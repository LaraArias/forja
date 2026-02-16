"""Tests for forja.status module."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from forja.status import _load_features_safe, show_status


class TestLoadFeaturesSafe:
    """Test _load_features_safe handles edge cases."""

    def test_missing_file(self, tmp_path):
        status, features = _load_features_safe(tmp_path / "nonexistent.json")
        assert status == "waiting"
        assert features == []

    def test_corrupt_json(self, tmp_path):
        bad = tmp_path / "features.json"
        bad.write_text("{invalid json", encoding="utf-8")
        status, features = _load_features_safe(bad)
        assert status == "reading"
        assert features == []

    def test_empty_file(self, tmp_path):
        empty = tmp_path / "features.json"
        empty.write_text("", encoding="utf-8")
        status, features = _load_features_safe(empty)
        assert status == "reading"
        assert features == []

    def test_valid_json(self, tmp_path):
        valid = tmp_path / "features.json"
        data = {"features": [
            {"id": "f1", "description": "Login", "status": "passed", "cycles": 1},
            {"id": "f2", "description": "Logout", "status": "pending", "cycles": 0},
        ]}
        valid.write_text(json.dumps(data), encoding="utf-8")
        status, features = _load_features_safe(valid)
        assert status == "ok"
        assert len(features) == 2
        assert features[0]["id"] == "f1"

    def test_valid_json_no_features_key(self, tmp_path):
        """JSON with no 'features' key returns empty list."""
        valid = tmp_path / "features.json"
        valid.write_text('{"other": "data"}', encoding="utf-8")
        status, features = _load_features_safe(valid)
        assert status == "ok"
        assert features == []


class TestShowStatus:
    """Test show_status output."""

    @patch("forja.status.TEAMMATES_DIR")
    @patch("forja.status._check_project", return_value=True)
    def test_no_teammates_dir(self, mock_check, mock_dir, tmp_path):
        mock_dir.__class__ = type(tmp_path / "nonexistent")
        # Use a path that doesn't exist
        nonexistent = tmp_path / "nonexistent"
        mock_dir.is_dir.return_value = False
        result = show_status()
        assert result is True

    @patch("forja.status.TEAMMATES_DIR")
    @patch("forja.status._check_project", return_value=True)
    def test_with_teammates(self, mock_check, mock_dir, tmp_path, capsys):
        teammates = tmp_path / "teammates"
        teammates.mkdir()

        # Create a teammate with features
        t1 = teammates / "team-1"
        t1.mkdir()
        data = {"features": [
            {"id": "f1", "description": "Auth", "status": "passed", "cycles": 2},
            {"id": "f2", "description": "API", "status": "blocked", "cycles": 3},
            {"id": "f3", "description": "UI", "status": "failed", "cycles": 1},
        ]}
        (t1 / "features.json").write_text(json.dumps(data), encoding="utf-8")

        mock_dir.__class__ = type(teammates)
        mock_dir.is_dir.return_value = True
        mock_dir.iterdir.return_value = [t1]

        result = show_status()
        assert result is True

        output = capsys.readouterr().out
        assert "Forja Status" in output
        assert "Auth" in output

    @patch("forja.status._check_project", return_value=False)
    def test_not_forja_project(self, mock_check):
        result = show_status()
        assert result is False


class TestWorkflowStatus:
    """Verify status shows phases in order when workflow.json exists."""

    def test_status_shows_phases_in_order(self, tmp_path, capsys, monkeypatch):
        """Workflow mode shows 'Phase N: agent' with status icons."""
        from forja.status import show_status

        # Create workflow.json
        workflow = {
            "phases": [
                {"agent": "content-strategist", "role": "Content", "output": "copy-brief.md"},
                {"agent": "frontend-builder", "role": "Frontend", "output": "index.html"},
                {"agent": "qa", "role": "QA", "output": "qa-report.json"},
            ]
        }

        teammates = tmp_path / "teammates"
        teammates.mkdir()

        # content-strategist: passed
        cs = teammates / "content-strategist"
        cs.mkdir()
        (cs / "features.json").write_text(json.dumps({
            "features": [{"id": "content-strategist-001", "description": "Generate copy",
                          "status": "passed", "cycles": 1}]
        }), encoding="utf-8")

        # frontend-builder: in progress
        fb = teammates / "frontend-builder"
        fb.mkdir()
        (fb / "features.json").write_text(json.dumps({
            "features": [{"id": "frontend-builder-001", "description": "Build HTML",
                          "status": "pending", "cycles": 1}]
        }), encoding="utf-8")

        wf_path = tmp_path / "workflow.json"
        wf_path.write_text(json.dumps(workflow), encoding="utf-8")

        monkeypatch.setattr("forja.status.TEAMMATES_DIR", teammates)
        monkeypatch.setattr("forja.status.WORKFLOW_PATH", wf_path)
        monkeypatch.setattr("forja.status._check_project", lambda: True)

        result = show_status()

        assert result is True
        output = capsys.readouterr().out
        assert "workflow mode" in output
        assert "Phase 1" in output
        assert "Phase 2" in output
        assert "content-strategist" in output

    def test_no_workflow_uses_epic_mode(self, tmp_path, capsys, monkeypatch):
        """Without workflow.json, status falls back to classic epic mode."""
        from forja.status import show_status

        teammates = tmp_path / "teammates"
        teammates.mkdir()
        t1 = teammates / "backend"
        t1.mkdir()
        (t1 / "features.json").write_text(json.dumps({
            "features": [
                {"id": "b-001", "description": "Auth", "status": "passed", "cycles": 1},
            ]
        }), encoding="utf-8")

        # No workflow.json → classic mode
        monkeypatch.setattr("forja.status.TEAMMATES_DIR", teammates)
        monkeypatch.setattr("forja.status.WORKFLOW_PATH", tmp_path / "nonexistent.json")
        monkeypatch.setattr("forja.status._check_project", lambda: True)

        result = show_status()

        assert result is True
        output = capsys.readouterr().out
        assert "Forja Status" in output
        assert "workflow mode" not in output
        assert "Auth" in output
