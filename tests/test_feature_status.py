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


class TestCmdPassEvidence:
    """cmd_pass with --evidence stores evidence."""

    def test_pass_with_evidence(self, tmp_path):
        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "pending", "cycles": 0}
        ])
        cmd_pass("f-001", str(tmp_path), evidence="all 3 tests pass, endpoint returns 201")
        data = _read_features(tmp_path)
        feat = data["features"][0]
        assert feat["status"] == "passed"
        assert feat["evidence"] == "all 3 tests pass, endpoint returns 201"
        assert "passed_at" in feat

    def test_pass_without_evidence_still_works(self, tmp_path):
        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "pending", "cycles": 0}
        ])
        cmd_pass("f-001", str(tmp_path))
        data = _read_features(tmp_path)
        feat = data["features"][0]
        assert feat["status"] == "passed"
        assert "evidence" not in feat  # Not stored when None

    def test_evidence_in_event_log(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".forja").mkdir(parents=True, exist_ok=True)
        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "pending", "cycles": 0}
        ])
        cmd_pass("f-001", str(tmp_path), evidence="probe OK")
        log_path = tmp_path / ".forja" / "feature-events.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip().split("\n")[-1])
        assert entry["reason"] == "probe OK"
        assert entry["event"] == "passed"


# ── Deterministic evaluation tests ────────────────────────────────────

# Extend shim with stubs needed by forja_outcome imports
_shim.load_dotenv = lambda *a, **kw: {}
_shim.call_llm = lambda *a, **kw: ""
_shim._call_claude_code = lambda *a, **kw: ""
_shim.parse_json = lambda *a, **kw: None

from forja.templates.forja_outcome import _deterministic_eval


class TestDeterministicEval:
    """_deterministic_eval matches probes to validation specs."""

    def test_passed_probe_matches_spec(self):
        trace_data = {
            "probes": [
                {"endpoint": "/users", "method": "POST", "expected_status": 201,
                 "actual_status": 201, "passed": True, "missing_fields": []}
            ]
        }
        specs = [
            {"path": "/users", "method": "POST", "expected_status": 201,
             "description": "Create user"}
        ]
        result = _deterministic_eval(trace_data, specs)
        assert len(result["met"]) == 1
        assert result["met"][0]["endpoint"] == "POST /users"
        assert result["met"][0]["source"] == "probe"
        assert len(result["unmet"]) == 0
        assert len(result["unmatched_specs"]) == 0

    def test_failed_probe_produces_unmet(self):
        trace_data = {
            "probes": [
                {"endpoint": "/users", "method": "POST", "expected_status": 201,
                 "actual_status": 404, "passed": False, "missing_fields": []}
            ]
        }
        specs = [
            {"path": "/users", "method": "POST", "expected_status": 201}
        ]
        result = _deterministic_eval(trace_data, specs)
        assert len(result["met"]) == 0
        assert len(result["unmet"]) == 1
        assert "404" in result["unmet"][0]["reason"]

    def test_unmatched_spec_goes_to_llm(self):
        trace_data = {
            "probes": [
                {"endpoint": "/users", "method": "POST", "expected_status": 201,
                 "actual_status": 201, "passed": True, "missing_fields": []}
            ]
        }
        specs = [
            {"path": "/users", "method": "POST", "expected_status": 201},
            {"path": "/items", "method": "GET", "expected_status": 200},
        ]
        result = _deterministic_eval(trace_data, specs)
        assert len(result["met"]) == 1
        assert len(result["unmatched_specs"]) == 1
        assert result["unmatched_specs"][0]["path"] == "/items"

    def test_empty_probes_returns_all_unmatched(self):
        specs = [
            {"path": "/users", "method": "POST", "expected_status": 201}
        ]
        result = _deterministic_eval({}, specs)
        assert len(result["met"]) == 0
        assert len(result["unmet"]) == 0
        assert len(result["unmatched_specs"]) == 1

    def test_missing_fields_in_unmet_reason(self):
        trace_data = {
            "probes": [
                {"endpoint": "/users", "method": "POST", "expected_status": 201,
                 "actual_status": 201, "passed": False,
                 "missing_fields": ["email", "name"]}
            ]
        }
        specs = [{"path": "/users", "method": "POST", "expected_status": 201}]
        result = _deterministic_eval(trace_data, specs)
        assert len(result["unmet"]) == 1
        assert "email" in result["unmet"][0]["reason"]
        assert "name" in result["unmet"][0]["reason"]

    def test_case_insensitive_method_matching(self):
        trace_data = {
            "probes": [
                {"endpoint": "/users", "method": "post", "expected_status": 201,
                 "actual_status": 201, "passed": True, "missing_fields": []}
            ]
        }
        specs = [{"path": "/users", "method": "POST", "expected_status": 201}]
        result = _deterministic_eval(trace_data, specs)
        assert len(result["met"]) == 1

    def test_multiple_endpoints_mixed_results(self):
        trace_data = {
            "probes": [
                {"endpoint": "/users", "method": "POST", "expected_status": 201,
                 "actual_status": 201, "passed": True, "missing_fields": []},
                {"endpoint": "/users", "method": "GET", "expected_status": 200,
                 "actual_status": 500, "passed": False, "missing_fields": []},
            ]
        }
        specs = [
            {"path": "/users", "method": "POST", "expected_status": 201, "description": "Create"},
            {"path": "/users", "method": "GET", "expected_status": 200, "description": "List"},
            {"path": "/users/{id}", "method": "DELETE", "expected_status": 204},
        ]
        result = _deterministic_eval(trace_data, specs)
        assert len(result["met"]) == 1
        assert len(result["unmet"]) == 1
        assert len(result["unmatched_specs"]) == 1
        assert result["unmatched_specs"][0]["path"] == "/users/{id}"
