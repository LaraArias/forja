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
_shim.load_dotenv = lambda *a, **kw: {}
_shim.call_llm = lambda *a, **kw: ""
_shim._call_claude_code = lambda *a, **kw: ""
_shim.parse_json = lambda *a, **kw: None
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


# ── _extract_action tests ────────────────────────────────────────────

from forja.templates.forja_learnings import (
    _extract_action,
    _extract_short_title,
    cmd_synthesize,
    cmd_apply,
)


class TestExtractAction:
    """Verify action extraction from learning text."""

    def test_extracts_autofix(self):
        text = "Something failed. Auto-fix: add bcrypt to requirements.txt"
        assert _extract_action(text) == "add bcrypt to requirements.txt"

    def test_extracts_action_marker(self):
        text = "Code issue found. Action: add validation rule"
        assert _extract_action(text) == "add validation rule"

    def test_returns_empty_when_no_marker(self):
        text = "Generic learning without action markers"
        assert _extract_action(text) == ""


class TestExtractShortTitle:
    """Verify short title extraction."""

    def test_period_separator(self):
        text = "Auth dependencies missing. Should pre-install them."
        assert _extract_short_title(text) == "Auth dependencies missing"

    def test_colon_separator(self):
        text = "PRD gap found: no error handling spec defined"
        assert _extract_short_title(text) == "PRD gap found"

    def test_dash_separator(self):
        text = "Feature auth-001 — required 4 cycles to build"
        assert _extract_short_title(text) == "Feature auth-001"

    def test_truncates_long_text(self):
        text = "A" * 100  # no separator, 100 chars
        result = _extract_short_title(text)
        assert len(result) <= 60


# ── cmd_synthesize tests ─────────────────────────────────────────────

class TestCmdSynthesize:
    """End-to-end synthesize with filesystem fixtures."""

    def _write_jsonl(self, learnings_dir, entries):
        """Helper: write entries to a JSONL file."""
        fpath = learnings_dir / "test.jsonl"
        with open(fpath, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_synthesize_creates_learnings_md(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        self._write_jsonl(learnings_dir, [
            {
                "timestamp": "2026-02-16T10:00:00+00:00",
                "category": "error-pattern",
                "learning": "Feature auth-001 required 4 cycles. Auto-fix: add bcrypt to requirements.txt",
                "source": "auth/features.json",
                "severity": "high",
            },
        ])

        cmd_synthesize()

        wisdom = learnings_dir / "_learnings.md"
        assert wisdom.exists()
        content = wisdom.read_text(encoding="utf-8")
        assert "Accumulated Wisdom" in content
        assert "2026-02-16" in content
        assert "Build failure" in content  # _CATEGORY_CONTEXT for error-pattern
        assert "**Action**:" in content
        assert "add bcrypt" in content

    def test_synthesize_high_full_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        self._write_jsonl(learnings_dir, [
            {
                "timestamp": "2026-02-16T10:00:00+00:00",
                "category": "kimi-finding",
                "learning": "SQL injection in query builder. Action: add parameterized queries",
                "source": "crossmodel/db.json",
                "severity": "high",
            },
        ])

        cmd_synthesize()

        content = (learnings_dir / "_learnings.md").read_text(encoding="utf-8")
        # HIGH severity → full format with ## header, Context, Error, Principle, Action
        assert "##" in content
        assert "**Context**:" in content
        assert "**Error**:" in content
        assert "**Principle**:" in content
        assert "**Action**:" in content
        assert "add parameterized queries" in content

    def test_synthesize_medium_compact_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        self._write_jsonl(learnings_dir, [
            {
                "timestamp": "2026-02-16T10:00:00+00:00",
                "category": "assumption",
                "learning": "Assumed SQLite was good enough",
                "source": "plan-transcript.json",
                "severity": "medium",
            },
        ])

        cmd_synthesize()

        content = (learnings_dir / "_learnings.md").read_text(encoding="utf-8")
        # MEDIUM severity → compact one-liner with - [MEDIUM]
        assert "[MEDIUM]" in content
        assert "Assumed SQLite" in content

    def test_synthesize_empty_entries(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        # No JSONL files → no _learnings.md created
        cmd_synthesize()
        assert not (learnings_dir / "_learnings.md").exists()

    def test_synthesize_caps_at_50(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        # Write 60 entries
        entries = [
            {
                "timestamp": f"2026-02-16T10:{i:02d}:00+00:00",
                "category": "error-pattern",
                "learning": f"Learning number {i}",
                "source": "test",
                "severity": "low",
            }
            for i in range(60)
        ]
        self._write_jsonl(learnings_dir, entries)

        cmd_synthesize()

        content = (learnings_dir / "_learnings.md").read_text(encoding="utf-8")
        # Should have at most 50 entries (compact lines start with "- ")
        compact_lines = [l for l in content.splitlines() if l.startswith("- **[")]
        assert len(compact_lines) <= 50


# ── Enhanced cmd_apply tests (rules 5 + 6) ───────────────────────────

class TestCmdApplyAntiPatterns:
    """Rule 5: anti-patterns → DOMAIN.md."""

    def _write_jsonl(self, learnings_dir, entries):
        fpath = learnings_dir / "test.jsonl"
        with open(fpath, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_antipattern_appended_to_domain_md(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        # Create a domain file
        domain_dir = tmp_path / "context" / "domains" / "auth"
        domain_dir.mkdir(parents=True)
        (domain_dir / "DOMAIN.md").write_text(
            "# Auth Domain\n\nHandles authentication.\n",
            encoding="utf-8",
        )

        self._write_jsonl(learnings_dir, [
            {
                "timestamp": "2026-02-16T10:00:00+00:00",
                "category": "error-pattern",
                "learning": "Should not use plain text passwords, avoid storing secrets in env vars",
                "source": "auth/features.json",
                "severity": "high",
            },
        ])

        cmd_apply()

        content = (domain_dir / "DOMAIN.md").read_text(encoding="utf-8")
        assert "## Anti-patterns" in content
        assert "[LEARNED]" in content
        assert "plain text passwords" in content

    def test_no_antipattern_without_keywords(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        domain_dir = tmp_path / "context" / "domains" / "auth"
        domain_dir.mkdir(parents=True)
        (domain_dir / "DOMAIN.md").write_text(
            "# Auth Domain\n\nHandles authentication.\n",
            encoding="utf-8",
        )

        self._write_jsonl(learnings_dir, [
            {
                "timestamp": "2026-02-16T10:00:00+00:00",
                "category": "error-pattern",
                "learning": "Feature auth-001 required 4 cycles. Auto-fix: add bcrypt to requirements.txt",
                "source": "auth/features.json",
                "severity": "high",
            },
        ])

        cmd_apply()

        content = (domain_dir / "DOMAIN.md").read_text(encoding="utf-8")
        # No anti-pattern keywords → no Anti-patterns section
        assert "## Anti-patterns" not in content


class TestCmdApplyKimiValidation:
    """Rule 6: kimi findings → validation-rules.md."""

    def _write_jsonl(self, learnings_dir, entries):
        fpath = learnings_dir / "test.jsonl"
        with open(fpath, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_kimi_finding_creates_validation_rules(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        company_dir = tmp_path / "context" / "company"
        company_dir.mkdir(parents=True)

        self._write_jsonl(learnings_dir, [
            {
                "timestamp": "2026-02-16T10:00:00+00:00",
                "category": "kimi-finding",
                "learning": "Code issue found by reviewer: SQL injection in query builder. Action: add validation rule",
                "source": "crossmodel/db.json",
                "severity": "high",
            },
        ])

        cmd_apply()

        target = company_dir / "validation-rules.md"
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert "# Validation Rules" in content
        assert "[KIMI]" in content
        assert "SQL injection" in content

    def test_kimi_finding_does_not_duplicate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        learnings_dir = tmp_path / "context" / "learnings"
        learnings_dir.mkdir(parents=True)

        company_dir = tmp_path / "context" / "company"
        company_dir.mkdir(parents=True)

        self._write_jsonl(learnings_dir, [
            {
                "timestamp": "2026-02-16T10:00:00+00:00",
                "category": "kimi-finding",
                "learning": "Missing input validation",
                "source": "crossmodel/api.json",
                "severity": "medium",
            },
        ])

        cmd_apply()
        cmd_apply()  # Run twice

        content = (company_dir / "validation-rules.md").read_text(encoding="utf-8")
        # _append_to_file checks for duplicates
        assert content.count("[KIMI] Missing input validation") == 1
