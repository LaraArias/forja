"""Tests for the Feature dataclass."""

import pytest
from forja.utils import Feature, read_feature_status


class TestFromDict:
    """Feature.from_dict() deserialization."""

    def test_modern_status_dict(self):
        d = {"id": "f1", "description": "Login", "status": "passed", "cycles": 2,
             "created_at": "2024-01-01T00:00:00Z", "passed_at": "2024-01-02T00:00:00Z"}
        f = Feature.from_dict(d)
        assert f.id == "f1"
        assert f.description == "Login"
        assert f.status == "passed"
        assert f.cycles == 2
        assert f.created_at == "2024-01-01T00:00:00Z"
        assert f.passed_at == "2024-01-02T00:00:00Z"

    def test_legacy_passes_boolean(self):
        d = {"id": "f1", "passes": True, "cycles": 1}
        f = Feature.from_dict(d)
        assert f.status == "passed"

    def test_legacy_passed_boolean(self):
        d = {"id": "f1", "passed": True}
        f = Feature.from_dict(d)
        assert f.status == "passed"

    def test_legacy_blocked_boolean(self):
        d = {"id": "f1", "blocked": True, "cycles": 5}
        f = Feature.from_dict(d)
        assert f.status == "blocked"

    def test_missing_status_with_cycles(self):
        """No status + cycles > 0 → 'failed'."""
        d = {"id": "f1", "cycles": 3}
        f = Feature.from_dict(d)
        assert f.status == "failed"

    def test_empty_dict_defaults_pending(self):
        f = Feature.from_dict({})
        assert f.id == ""
        assert f.status == "pending"
        assert f.cycles == 0

    def test_description_falls_back_to_name(self):
        d = {"id": "f1", "name": "Login Feature"}
        f = Feature.from_dict(d)
        assert f.description == "Login Feature"
        assert f.name == "Login Feature"

    def test_description_preferred_over_name(self):
        d = {"id": "f1", "description": "Login", "name": "legacy-name"}
        f = Feature.from_dict(d)
        assert f.description == "Login"

    def test_teammate_field(self):
        d = {"id": "f1", "_teammate": "team-auth"}
        f = Feature.from_dict(d)
        assert f._teammate == "team-auth"


class TestToDict:
    """Feature.to_dict() serialization."""

    def test_round_trip_preserves_known_fields(self):
        d = {"id": "f1", "description": "Login", "status": "passed",
             "cycles": 2, "created_at": "2024-01-01", "passed_at": "2024-01-02"}
        f = Feature.from_dict(d)
        out = f.to_dict()
        assert out["id"] == "f1"
        assert out["description"] == "Login"
        assert out["status"] == "passed"
        assert out["cycles"] == 2
        assert out["created_at"] == "2024-01-01"
        assert out["passed_at"] == "2024-01-02"

    def test_omits_teammate(self):
        f = Feature(id="f1", _teammate="team-a")
        out = f.to_dict()
        assert "_teammate" not in out

    def test_omits_extra_key(self):
        """_extra keys are NOT in the output under _extra — they are merged."""
        d = {"id": "f1", "status": "pending", "cycles": 0, "custom_note": "hello"}
        f = Feature.from_dict(d)
        out = f.to_dict()
        # custom_note is preserved via _extra merge
        assert out["custom_note"] == "hello"
        assert "_extra" not in out

    def test_preserves_unknown_keys(self):
        """Unknown keys round-trip through _extra."""
        d = {"id": "f1", "status": "pending", "cycles": 0,
             "priority": "high", "assignee": "bot"}
        f = Feature.from_dict(d)
        out = f.to_dict()
        assert out["priority"] == "high"
        assert out["assignee"] == "bot"

    def test_omits_none_timestamps(self):
        f = Feature(id="f1")
        out = f.to_dict()
        assert "created_at" not in out
        assert "passed_at" not in out
        assert "blocked_at" not in out


class TestProperties:
    """Feature computed properties."""

    def test_is_terminal_passed(self):
        assert Feature(id="f1", status="passed").is_terminal is True

    def test_is_terminal_blocked(self):
        assert Feature(id="f1", status="blocked").is_terminal is True

    def test_is_terminal_pending(self):
        assert Feature(id="f1", status="pending").is_terminal is False

    def test_is_terminal_failed(self):
        assert Feature(id="f1", status="failed").is_terminal is False

    def test_can_retry_pending(self):
        assert Feature(id="f1", status="pending").can_retry is True

    def test_can_retry_failed(self):
        assert Feature(id="f1", status="failed").can_retry is True

    def test_can_retry_blocked(self):
        assert Feature(id="f1", status="blocked").can_retry is False

    def test_can_retry_passed(self):
        assert Feature(id="f1", status="passed").can_retry is False

    def test_display_name_description(self):
        f = Feature(id="f1", description="Login")
        assert f.display_name == "Login"

    def test_display_name_falls_back_to_name(self):
        f = Feature(id="f1", name="legacy")
        assert f.display_name == "legacy"

    def test_display_name_falls_back_to_id(self):
        f = Feature(id="f1")
        assert f.display_name == "f1"


class TestMutability:
    """Feature is mutable for in-place updates in forja_features.py."""

    def test_can_mutate_status(self):
        f = Feature(id="f1", status="pending")
        f.status = "passed"
        assert f.status == "passed"

    def test_can_increment_cycles(self):
        f = Feature(id="f1", cycles=0)
        f.cycles += 1
        assert f.cycles == 1

    def test_mutation_reflected_in_to_dict(self):
        f = Feature(id="f1", status="pending", cycles=0)
        f.status = "failed"
        f.cycles = 3
        out = f.to_dict()
        assert out["status"] == "failed"
        assert out["cycles"] == 3


class TestReadFeatureStatusWithFeature:
    """read_feature_status() should accept Feature instances too."""

    def test_with_feature_object(self):
        f = Feature(id="f1", status="blocked")
        assert read_feature_status(f) == "blocked"

    def test_with_raw_dict(self):
        """Backward compat: raw dicts still work."""
        assert read_feature_status({"status": "passed"}) == "passed"

    def test_with_legacy_dict(self):
        assert read_feature_status({"passes": True}) == "passed"
