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
