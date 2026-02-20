"""Tests for the decision log (typed context entries + audit command)."""

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

from forja.templates import forja_context as ctx_mod


def _setup_context(tmp_path, monkeypatch):
    """Set up context store dirs in tmp_path."""
    store_dir = tmp_path / "context" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    history_dir = store_dir / ".history"
    history_dir.mkdir(parents=True, exist_ok=True)
    ontology_file = tmp_path / "context" / "ontology.md"
    forja_dir = tmp_path / ".forja"
    forja_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ctx_mod, "STORE_DIR", store_dir)
    monkeypatch.setattr(ctx_mod, "HISTORY_DIR", history_dir)
    monkeypatch.setattr(ctx_mod, "LOCK_FILE", store_dir / ".lock")
    monkeypatch.setattr(ctx_mod, "ONTOLOGY_FILE", ontology_file)
    monkeypatch.setattr(ctx_mod, "EVENT_STREAM", forja_dir / "event-stream.jsonl")
    monkeypatch.chdir(tmp_path)
    return store_dir, forja_dir


def _read_event_stream(forja_dir):
    """Read event-stream.jsonl."""
    stream_path = forja_dir / "event-stream.jsonl"
    if not stream_path.exists():
        return []
    events = []
    for line in stream_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


class TestSetWithType:
    """cmd_set with --type stores the type field."""

    def test_set_with_type_decision(self, tmp_path, monkeypatch):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["arch.database", "PostgreSQL", "--author", "lead", "--type", "DECISION"])

        data = ctx_mod._load_var("arch.database")
        assert data is not None
        assert data["value"] == "PostgreSQL"
        assert data["type"] == "DECISION"

    def test_set_with_type_fact(self, tmp_path, monkeypatch):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["runtime.python", "3.12", "--author", "lead", "--type", "FACT"])

        data = ctx_mod._load_var("runtime.python")
        assert data["type"] == "FACT"

    def test_set_with_type_assumption(self, tmp_path, monkeypatch):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["deploy.target", "AWS", "--author", "lead", "--type", "ASSUMPTION"])

        data = ctx_mod._load_var("deploy.target")
        assert data["type"] == "ASSUMPTION"

    def test_set_with_type_observation(self, tmp_path, monkeypatch):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["perf.latency", "200ms", "--author", "bot", "--type", "OBSERVATION"])

        data = ctx_mod._load_var("perf.latency")
        assert data["type"] == "OBSERVATION"

    def test_set_invalid_type_rejected(self, tmp_path, monkeypatch):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        with pytest.raises(SystemExit):
            ctx_mod.cmd_set(["key", "val", "--author", "x", "--type", "INVALID"])

    def test_set_without_type_backward_compat(self, tmp_path, monkeypatch):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["key", "val", "--author", "lead"])

        data = ctx_mod._load_var("key")
        assert data is not None
        assert data["value"] == "val"
        assert "type" not in data  # No type when not provided

    def test_type_preserved_on_update(self, tmp_path, monkeypatch):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["arch.database", "PostgreSQL", "--author", "lead", "--type", "DECISION"])
        ctx_mod.cmd_set(["arch.database", "SQLite", "--author", "lead"])

        data = ctx_mod._load_var("arch.database")
        assert data["value"] == "SQLite"
        assert data["type"] == "DECISION"  # Preserved from original


class TestDecisionEventStream:
    """Decision-typed context entries emit to the event stream."""

    def test_context_set_emits_event(self, tmp_path, monkeypatch):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["key", "val", "--author", "lead"])

        events = _read_event_stream(forja_dir)
        ctx_events = [e for e in events if e["type"] == "context.set"]
        assert len(ctx_events) == 1
        assert ctx_events[0]["data"]["key"] == "key"
        assert ctx_events[0]["data"]["value"] == "val"

    def test_decision_emits_to_event_stream(self, tmp_path, monkeypatch):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["arch.db", "PostgreSQL", "--author", "lead", "--type", "DECISION"])

        events = _read_event_stream(forja_dir)
        decision_events = [e for e in events if e["type"] == "decision.logged"]
        assert len(decision_events) == 1
        assert decision_events[0]["data"]["key"] == "arch.db"
        assert decision_events[0]["data"]["decision_type"] == "DECISION"

    def test_non_decision_type_no_decision_event(self, tmp_path, monkeypatch):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["key", "val", "--author", "lead"])

        events = _read_event_stream(forja_dir)
        decision_events = [e for e in events if e["type"] == "decision.logged"]
        assert len(decision_events) == 0


class TestCmdAudit:
    """cmd_audit shows decision timeline."""

    def test_audit_shows_typed_entries(self, tmp_path, monkeypatch, capsys):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["arch.db", "PostgreSQL", "--author", "lead", "--type", "DECISION"])
        ctx_mod.cmd_set(["runtime", "3.12", "--author", "bot", "--type", "FACT"])

        ctx_mod.cmd_audit([])

        output = capsys.readouterr().out
        assert "Decision Audit" in output
        assert "arch.db" in output
        assert "runtime" in output
        assert "DECISION" in output
        assert "FACT" in output

    def test_audit_filters_by_type(self, tmp_path, monkeypatch, capsys):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["arch.db", "PostgreSQL", "--author", "lead", "--type", "DECISION"])
        ctx_mod.cmd_set(["runtime", "3.12", "--author", "bot", "--type", "FACT"])
        # Discard cmd_set output
        capsys.readouterr()

        ctx_mod.cmd_audit(["--type", "DECISION"])

        output = capsys.readouterr().out
        assert "arch.db" in output
        assert "runtime" not in output

    def test_audit_empty_shows_help(self, tmp_path, monkeypatch, capsys):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_audit([])

        output = capsys.readouterr().out
        assert "No decisions or typed entries found" in output

    def test_audit_includes_event_stream_decisions(self, tmp_path, monkeypatch, capsys):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        # Write a decision event directly to event stream
        (forja_dir / "event-stream.jsonl").write_text(
            json.dumps({
                "id": "decision.logged-test",
                "timestamp": "2024-01-15T10:00:00+00:00",
                "type": "decision.logged",
                "agent": "lead",
                "data": {
                    "key": "stream.decision",
                    "value": "test-value",
                    "decision_type": "DECISION",
                    "author": "lead",
                },
            }) + "\n"
        )

        ctx_mod.cmd_audit([])

        output = capsys.readouterr().out
        assert "stream.decision" in output


class TestListShowsType:
    """cmd_list shows type tags."""

    def test_list_shows_type_tag(self, tmp_path, monkeypatch, capsys):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["arch.db", "PostgreSQL", "--author", "lead", "--type", "DECISION"])

        ctx_mod.cmd_list([])

        output = capsys.readouterr().out
        assert "[DECISION]" in output
        assert "arch.db" in output

    def test_list_no_type_no_tag(self, tmp_path, monkeypatch, capsys):
        store_dir, forja_dir = _setup_context(tmp_path, monkeypatch)

        ctx_mod.cmd_set(["key", "val", "--author", "lead"])

        ctx_mod.cmd_list([])

        output = capsys.readouterr().out
        assert "[DECISION]" not in output
        assert "key" in output
