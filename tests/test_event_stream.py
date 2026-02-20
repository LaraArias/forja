"""Tests for the unified event stream (.forja/event-stream.jsonl)."""

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
_shim.load_dotenv = lambda *a, **kw: {}
_shim.call_llm = lambda *a, **kw: ""
_shim._call_claude_code = lambda *a, **kw: ""
_shim.parse_json = lambda *a, **kw: None
sys.modules.setdefault("forja_utils", _shim)

from forja.templates.forja_features import (
    _emit_event,
    EVENT_STREAM,
    cmd_attempt,
    cmd_pass,
    MAX_CYCLES,
)


def _make_features_json(tmp_path, features):
    """Write a features.json with given features list."""
    fpath = tmp_path / "features.json"
    fpath.write_text(json.dumps({"features": features}, indent=2), encoding="utf-8")
    return fpath


def _read_event_stream(tmp_path):
    """Read event-stream.jsonl from .forja dir."""
    stream_path = tmp_path / ".forja" / "event-stream.jsonl"
    if not stream_path.exists():
        return []
    events = []
    for line in stream_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


class TestEmitEvent:
    """_emit_event writes structured events to event-stream.jsonl."""

    def test_emit_event_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import forja.templates.forja_features as mod
        monkeypatch.setattr(mod, "EVENT_STREAM", tmp_path / ".forja" / "event-stream.jsonl")

        _emit_event("test.event", {"key": "value"})

        stream_path = tmp_path / ".forja" / "event-stream.jsonl"
        assert stream_path.exists()

    def test_emit_event_schema(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import forja.templates.forja_features as mod
        monkeypatch.setattr(mod, "EVENT_STREAM", tmp_path / ".forja" / "event-stream.jsonl")

        _emit_event("test.event", {"key": "value"}, agent="test-agent")

        events = _read_event_stream(tmp_path)
        assert len(events) == 1
        evt = events[0]
        assert "id" in evt
        assert evt["id"].startswith("test.event-")
        assert "timestamp" in evt
        assert evt["type"] == "test.event"
        assert evt["agent"] == "test-agent"
        assert evt["data"] == {"key": "value"}

    def test_emit_event_appends(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import forja.templates.forja_features as mod
        monkeypatch.setattr(mod, "EVENT_STREAM", tmp_path / ".forja" / "event-stream.jsonl")

        _emit_event("event.one", {"n": 1})
        _emit_event("event.two", {"n": 2})
        _emit_event("event.three", {"n": 3})

        events = _read_event_stream(tmp_path)
        assert len(events) == 3
        assert events[0]["type"] == "event.one"
        assert events[1]["type"] == "event.two"
        assert events[2]["type"] == "event.three"

    def test_emit_event_default_agent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import forja.templates.forja_features as mod
        monkeypatch.setattr(mod, "EVENT_STREAM", tmp_path / ".forja" / "event-stream.jsonl")

        _emit_event("test.event", {})

        events = _read_event_stream(tmp_path)
        assert events[0]["agent"] == "system"


class TestFeatureEventsInStream:
    """cmd_pass and cmd_attempt emit to the unified event stream."""

    def test_feature_pass_emits_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import forja.templates.forja_features as mod
        stream_path = tmp_path / ".forja" / "event-stream.jsonl"
        monkeypatch.setattr(mod, "EVENT_STREAM", stream_path)
        monkeypatch.setattr(mod, "EVENT_LOG", tmp_path / ".forja" / "feature-events.jsonl")

        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "pending", "cycles": 0}
        ])
        cmd_pass("f-001", str(tmp_path))

        events = _read_event_stream(tmp_path)
        passed_events = [e for e in events if e["type"] == "feature.passed"]
        assert len(passed_events) == 1
        assert passed_events[0]["data"]["feature_id"] == "f-001"

    def test_feature_pass_with_evidence_emits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import forja.templates.forja_features as mod
        stream_path = tmp_path / ".forja" / "event-stream.jsonl"
        monkeypatch.setattr(mod, "EVENT_STREAM", stream_path)
        monkeypatch.setattr(mod, "EVENT_LOG", tmp_path / ".forja" / "feature-events.jsonl")

        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "pending", "cycles": 0}
        ])
        cmd_pass("f-001", str(tmp_path), evidence="all tests pass")

        events = _read_event_stream(tmp_path)
        passed_events = [e for e in events if e["type"] == "feature.passed"]
        assert passed_events[0]["data"]["evidence"] == "all tests pass"

    def test_feature_attempt_emits_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import forja.templates.forja_features as mod
        stream_path = tmp_path / ".forja" / "event-stream.jsonl"
        monkeypatch.setattr(mod, "EVENT_STREAM", stream_path)
        monkeypatch.setattr(mod, "EVENT_LOG", tmp_path / ".forja" / "feature-events.jsonl")

        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "pending", "cycles": 0}
        ])
        cmd_attempt("f-001", str(tmp_path))

        events = _read_event_stream(tmp_path)
        failed_events = [e for e in events if e["type"] == "feature.failed"]
        assert len(failed_events) == 1
        assert failed_events[0]["data"]["feature_id"] == "f-001"
        assert failed_events[0]["data"]["cycle"] == 1

    def test_feature_block_emits_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import forja.templates.forja_features as mod
        stream_path = tmp_path / ".forja" / "event-stream.jsonl"
        monkeypatch.setattr(mod, "EVENT_STREAM", stream_path)
        monkeypatch.setattr(mod, "EVENT_LOG", tmp_path / ".forja" / "feature-events.jsonl")

        _make_features_json(tmp_path, [
            {"id": "f-001", "description": "test", "status": "failed",
             "cycles": MAX_CYCLES - 1}
        ])
        cmd_attempt("f-001", str(tmp_path))

        events = _read_event_stream(tmp_path)
        blocked_events = [e for e in events if e["type"] == "feature.blocked"]
        assert len(blocked_events) == 1
        assert "exceeded" in blocked_events[0]["data"]["reason"]
