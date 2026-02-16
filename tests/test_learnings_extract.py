"""Tests for forja_learnings actionable extract logic.

The template file uses ``from forja_utils import ...`` which isn't available
in the test environment.  We shim that module before importing.
"""

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

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

from forja.templates.forja_learnings import (
    _infer_error_pattern_action,
    _classify_action_type,
    cmd_extract,
    _read_all_entries,
    LEARNINGS_DIR,
)


# ── _infer_error_pattern_action tests ─────────────────────────────────

class TestInferErrorPatternAction:
    """Actionable learnings inferred from feature descriptions."""

    def test_auth_keyword_suggests_dependencies(self):
        result = _infer_error_pattern_action("JWT token validation", "auth", 4)
        assert "bcrypt" in result
        assert "python-jose" in result
        assert "requirements.txt" in result
        assert "4 cycles" in result

    def test_database_keyword_suggests_init(self):
        result = _infer_error_pattern_action("Create user schema", "models", 3)
        assert "database initialization" in result
        assert "create tables" in result

    def test_test_keyword_suggests_fixtures(self):
        result = _infer_error_pattern_action("all-endpoints-pass", "qa", 5)
        assert "pytest" in result or "httpx" in result
        assert "test" in result.lower()

    def test_frontend_keyword_suggests_node(self):
        result = _infer_error_pattern_action("Render HTML dashboard", "frontend", 3)
        assert "node" in result.lower() or "frontend" in result.lower()

    def test_generic_fallback_suggests_prd_criteria(self):
        result = _infer_error_pattern_action("Process payments", "billing", 4)
        assert "acceptance criteria" in result
        assert "input/output" in result
        assert "4 cycles" in result


# ── _classify_action_type tests ───────────────────────────────────────

class TestClassifyActionType:

    def test_dependencies_action(self):
        text = "Auto-add authentication dependencies (bcrypt, python-jose) to requirements.txt"
        assert _classify_action_type(text) == "Dependencies to auto-install"

    def test_validation_action(self):
        text = "Code issue found by reviewer: SQL injection in auth.py. Action: add validation rule to prevent this pattern."
        assert _classify_action_type(text) == "Validation rules to enforce"

    def test_prd_action(self):
        text = "PRD gap found: missing error handling spec. Auto-fix: add error codes."
        assert _classify_action_type(text) == "PRD patterns to include"

    def test_other_fallback(self):
        text = "Something totally unrelated to any known pattern"
        assert _classify_action_type(text) == "Other actions"


# ── cmd_extract integration tests ─────────────────────────────────────

class TestCmdExtractIntegration:
    """End-to-end extract with real filesystem fixtures."""

    def test_extracts_auth_feature_as_dependency_action(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        # Create features.json with an auth feature that took 4 cycles
        teammates = tmp_path / "context" / "teammates" / "auth"
        teammates.mkdir(parents=True)
        features = {
            "features": [
                {
                    "id": "auth-003",
                    "description": "JWT login endpoint",
                    "status": "passed",
                    "cycles": 4,
                }
            ]
        }
        (teammates / "features.json").write_text(
            json.dumps(features), encoding="utf-8"
        )

        # Create learnings dir
        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        cmd_extract()

        # Read back what was extracted
        entries = []
        for fpath in sorted(learnings_dir.glob("*.jsonl")):
            for line in fpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))

        assert len(entries) == 1
        learning = entries[0]["learning"]
        assert "bcrypt" in learning
        assert "python-jose" in learning
        assert "4 cycles" in learning

    def test_extracts_unmet_requirement_as_actionable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        # Create outcome report
        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        outcome = {
            "pass": False,
            "coverage": 60,
            "met": ["User registration"],
            "unmet": ["Email notifications"],
        }
        (forja_dir / "outcome-report.json").write_text(
            json.dumps(outcome), encoding="utf-8"
        )

        # Create learnings dir
        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        cmd_extract()

        entries = []
        for fpath in sorted(learnings_dir.glob("*.jsonl")):
            for line in fpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))

        assert len(entries) == 1
        learning = entries[0]["learning"]
        assert "Requirement not met: Email notifications" in learning
        assert "acceptance criteria" in learning
        assert "[input] -> [expected output]" in learning

    def test_extracts_spec_gap_with_suggestion(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        enrichment = {
            "gaps": [
                {
                    "severity": "high",
                    "description": "No error handling specification",
                    "suggestion": "define error codes for each endpoint",
                }
            ]
        }
        (forja_dir / "spec-enrichment.json").write_text(
            json.dumps(enrichment), encoding="utf-8"
        )

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        cmd_extract()

        entries = []
        for fpath in sorted(learnings_dir.glob("*.jsonl")):
            for line in fpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))

        assert len(entries) == 1
        learning = entries[0]["learning"]
        assert "PRD gap found:" in learning
        assert "define error codes" in learning
        assert "PRD template" in learning

    def test_extracts_crossmodel_finding_with_file_ref(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        cm_dir = tmp_path / ".forja" / "crossmodel"
        cm_dir.mkdir(parents=True)
        report = {
            "issues": [
                {
                    "severity": "high",
                    "description": "SQL injection in query builder",
                    "file": "src/db/queries.py",
                }
            ]
        }
        (cm_dir / "db.json").write_text(
            json.dumps(report), encoding="utf-8"
        )

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        cmd_extract()

        entries = []
        for fpath in sorted(learnings_dir.glob("*.jsonl")):
            for line in fpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))

        assert len(entries) == 1
        learning = entries[0]["learning"]
        assert "Code issue found by reviewer:" in learning
        assert "SQL injection" in learning
        assert "src/db/queries.py" in learning
        assert "validation rule" in learning

    def test_extracts_business_unmet_as_product_backlog(self, tmp_path, monkeypatch):
        """Unmet items with type 'business' → product-backlog, LOW severity."""
        monkeypatch.chdir(tmp_path)

        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        outcome = {
            "pass": False,
            "coverage": 60,
            "met": ["User registration"],
            "unmet": [
                {"requirement": "Pricing model", "type": "business"},
                "Email notifications",  # string → technical by default
            ],
        }
        (forja_dir / "outcome-report.json").write_text(
            json.dumps(outcome), encoding="utf-8"
        )

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        cmd_extract()

        entries = []
        for fpath in sorted(learnings_dir.glob("*.jsonl")):
            for line in fpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))

        assert len(entries) == 2

        # Find the product-backlog entry
        product = [e for e in entries if e["category"] == "product-backlog"]
        technical = [e for e in entries if e["category"] == "unmet-requirement"]

        assert len(product) == 1
        assert product[0]["severity"] == "low"
        assert "Product decision needed: Pricing model" in product[0]["learning"]
        assert "not a code issue" in product[0]["learning"]

        assert len(technical) == 1
        assert technical[0]["severity"] == "high"
        assert "Requirement not met: Email notifications" in technical[0]["learning"]

    def test_extracts_deferred_as_product_backlog(self, tmp_path, monkeypatch):
        """Deferred items from outcome → product-backlog, LOW severity."""
        monkeypatch.chdir(tmp_path)

        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        outcome = {
            "pass": True,
            "coverage": 90,
            "met": ["Auth", "API"],
            "unmet": [],
            "deferred": [
                "Partner integrations (reason: needs business agreement)",
                {"requirement": "Pricing tiers", "type": "business"},
            ],
        }
        (forja_dir / "outcome-report.json").write_text(
            json.dumps(outcome), encoding="utf-8"
        )

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        cmd_extract()

        entries = []
        for fpath in sorted(learnings_dir.glob("*.jsonl")):
            for line in fpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))

        assert len(entries) == 2
        assert all(e["category"] == "product-backlog" for e in entries)
        assert all(e["severity"] == "low" for e in entries)
        assert all("Product decision needed" in e["learning"] for e in entries)

    def test_extracts_assumption_as_actionable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        transcript = {
            "answers": [
                {
                    "question": "What database to use?",
                    "answer": "SQLite for simplicity",
                    "tags": ["ASSUMPTION"],
                }
            ]
        }
        (forja_dir / "plan-transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        cmd_extract()

        entries = []
        for fpath in sorted(learnings_dir.glob("*.jsonl")):
            for line in fpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))

        assert len(entries) == 1
        learning = entries[0]["learning"]
        assert "Unvalidated assumption:" in learning
        assert "What database to use?" in learning
        assert "SQLite for simplicity" in learning
        assert "validate with stakeholder" in learning
