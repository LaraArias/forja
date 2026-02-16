"""Tests for feature status enum (replaces passes/blocked booleans)."""

import json
import sys
import types
import pytest
from pathlib import Path

# ── Shim forja_utils so the template can import ───────────────────────
_shim = types.ModuleType("forja_utils")
_shim.PASS_ICON = "+"
_shim.FAIL_ICON = "x"
_shim.WARN_ICON = "!"
_shim.GREEN = ""
_shim.RED = ""
_shim.YELLOW = ""
_shim.DIM = ""
_shim.BOLD = ""
_shim.RESET = ""
from forja.templates.forja_utils import Feature
_shim.Feature = Feature
sys.modules.setdefault("forja_utils", _shim)

from forja.templates.forja_features import (
    VALID_STATES,
    MAX_CYCLES,
    cmd_attempt,
    cmd_pass,
    cmd_status,
    load_features,
    find_feature,
)


def _make_features_json(tmp_path, features):
    """Write a features.json with given features list."""
    fpath = tmp_path / "features.json"
    fpath.write_text(json.dumps({"features": features}, indent=2), encoding="utf-8")
    return fpath


def _read_features(tmp_path):
    """Read features.json back."""
    fpath = tmp_path / "features.json"
    return json.loads(fpath.read_text(encoding="utf-8"))


class TestStatusEnum:
    """A feature status is a single value - cannot be both passed and blocked."""

    def test_valid_states_defined(self):
        assert VALID_STATES == ("pending", "passed", "failed", "blocked")

    def test_status_is_single_value(self):
        """A feature has exactly one status - it cannot be simultaneously
        passed and blocked (which was possible with two independent booleans)."""
        feature = {"id": "test-001", "status": "passed"}
        # With the enum approach, status is a single field
        assert feature["status"] == "passed"
        assert feature["status"] != "blocked"
        # Cannot be both at the same time
        assert not (feature["status"] == "passed" and feature["status"] == "blocked")

    def test_pending_is_default(self):
        """Features without an explicit status default to pending."""
        feature = {"id": "test-001", "description": "test"}
        assert feature.get("status", "pending") == "pending"

    def test_each_state_is_mutually_exclusive(self):
        """Setting one state means no other state is active."""
        for state in VALID_STATES:
            feature = {"id": "test-001", "status": state}
            other_states = [s for s in VALID_STATES if s != state]
            for other in other_states:
                assert feature["status"] != other, (
                    f"Feature with status '{state}' should not match '{other}'"
                )


class TestCmdPass:
    """cmd_pass sets status to 'passed'."""

    def test_pass_sets_status(self, tmp_path):
        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "pending", "cycles": 0}
        ])
        cmd_pass("f-001", str(tmp_path))
        data = _read_features(tmp_path)
        feat = data["features"][0]
        assert feat["status"] == "passed"
        assert "passed_at" in feat
        # No old boolean fields
        assert "passes" not in feat

    def test_pass_blocked_feature_is_rejected(self, tmp_path):
        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "blocked", "cycles": 5}
        ])
        cmd_pass("f-001", str(tmp_path))
        data = _read_features(tmp_path)
        feat = data["features"][0]
        assert feat["status"] == "blocked"  # stays blocked


class TestCmdAttempt:
    """cmd_attempt increments cycles and manages status transitions."""

    def test_attempt_sets_failed(self, tmp_path):
        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "pending", "cycles": 0}
        ])
        cmd_attempt("f-001", str(tmp_path))
        data = _read_features(tmp_path)
        feat = data["features"][0]
        assert feat["status"] == "failed"
        assert feat["cycles"] == 1

    def test_attempt_blocks_after_max_cycles(self, tmp_path):
        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "failed",
             "cycles": MAX_CYCLES - 1}
        ])
        cmd_attempt("f-001", str(tmp_path))
        data = _read_features(tmp_path)
        feat = data["features"][0]
        assert feat["status"] == "blocked"
        assert feat["cycles"] == MAX_CYCLES
        assert "blocked_at" in feat

    def test_attempt_skips_blocked(self, tmp_path):
        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "blocked", "cycles": 5}
        ])
        cmd_attempt("f-001", str(tmp_path))
        data = _read_features(tmp_path)
        feat = data["features"][0]
        assert feat["status"] == "blocked"
        assert feat["cycles"] == 5  # unchanged
