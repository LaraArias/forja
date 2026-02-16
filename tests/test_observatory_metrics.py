"""Tests for observatory metrics computation and HTML generation.

The template file uses ``from forja_utils import ...`` which isn't available
in the test environment.  We shim that module before importing.
"""

import sys
import types

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

from forja.templates.forja_observatory import _compute_metrics, _esc


# ── _compute_metrics tests ───────────────────────────────────────────

class TestComputeMetricsEmpty:
    """Metrics with all-None / empty inputs."""

    def test_all_none_inputs(self):
        m = _compute_metrics(
            [], None, None, [], None, [], [], {}, 0, 0,
        )
        assert m["total_features"] == 0
        assert m["total_passed"] == 0
        assert m["total_blocked"] == 0
        assert m["total_failed"] == 0
        assert m["build_status"] == "skip"
        assert m["sr_status"] == "skip"
        assert m["plan_status"] == "skip"
        assert m["outcome_status"] == "skip"
        assert m["learnings_status"] == "skip"
        assert m["avg_cycles"] == 0
        assert m["num_teammates"] == 0
        assert m["total_time_minutes"] == 0
        assert m["roadmap"] == []
        assert m["outcome_deferred"] == []


class TestComputeMetricsAllPassed:
    """Metrics when all features pass."""

    def test_single_teammate_all_passed(self):
        feats = [
            {"id": "f1", "status": "passed", "description": "Feature A", "cycles": 1},
            {"id": "f2", "status": "passed", "description": "Feature B", "cycles": 2},
        ]
        teammates = [{"teammate": "alice", "features": feats}]
        m = _compute_metrics(
            teammates, None, None, [], None, [], [], {}, 0, 0,
        )
        assert m["build_status"] == "pass"
        assert m["total_features"] == 2
        assert m["total_passed"] == 2
        assert m["total_failed"] == 0
        assert m["total_blocked"] == 0
        assert m["num_teammates"] == 1
        assert m["per_teammate"]["alice"]["passed"] == 2
        assert m["per_teammate"]["alice"]["total"] == 2

    def test_avg_cycles_calculation(self):
        feats = [
            {"id": "f1", "status": "passed", "cycles": 2},
            {"id": "f2", "status": "passed", "cycles": 4},
        ]
        teammates = [{"teammate": "bob", "features": feats}]
        m = _compute_metrics(
            teammates, None, None, [], None, [], [], {}, 0, 0,
        )
        assert m["avg_cycles"] == 3.0

    def test_multiple_teammates(self):
        tm1 = [{"id": "f1", "status": "passed", "cycles": 1}]
        tm2 = [
            {"id": "f2", "status": "passed", "cycles": 1},
            {"id": "f3", "status": "passed", "cycles": 3},
        ]
        teammates = [
            {"teammate": "alice", "features": tm1},
            {"teammate": "bob", "features": tm2},
        ]
        m = _compute_metrics(
            teammates, None, None, [], None, [], [], {}, 0, 0,
        )
        assert m["total_features"] == 3
        assert m["total_passed"] == 3
        assert m["build_status"] == "pass"
        assert m["num_teammates"] == 2


class TestComputeMetricsMixed:
    """Metrics with blocked, failed, and passed features."""

    def test_50pct_passed_is_warn(self):
        """1/2 passed = 50% → warn (partial success)."""
        feats = [
            {"id": "f1", "status": "passed", "cycles": 1},
            {"id": "f2", "status": "blocked", "cycles": 5},
        ]
        teammates = [{"teammate": "carol", "features": feats}]
        m = _compute_metrics(
            teammates, None, None, [], None, [], [], {}, 0, 0,
        )
        assert m["build_status"] == "warn"
        assert m["total_blocked"] == 1
        assert m["total_passed"] == 1
        assert m["total_failed"] == 0

    def test_50pct_with_failed_is_warn(self):
        """1/2 passed = 50% → warn even with failures (partial success)."""
        feats = [
            {"id": "f1", "status": "passed", "cycles": 1},
            {"id": "f2", "status": "failed", "cycles": 2},
        ]
        teammates = [{"teammate": "dave", "features": feats}]
        m = _compute_metrics(
            teammates, None, None, [], None, [], [], {}, 0, 0,
        )
        assert m["build_status"] == "warn"
        assert m["total_failed"] == 1

    def test_below_50pct_is_fail(self):
        """1/3 passed = 33% → fail."""
        feats = [
            {"id": "f1", "status": "passed", "cycles": 1},
            {"id": "f2", "status": "failed", "cycles": 2},
            {"id": "f3", "status": "failed", "cycles": 3},
        ]
        teammates = [{"teammate": "eve", "features": feats}]
        m = _compute_metrics(
            teammates, None, None, [], None, [], [], {}, 0, 0,
        )
        assert m["build_status"] == "fail"
        assert m["total_failed"] == 2
        assert m["total_passed"] == 1

    def test_high_pass_rate_is_warn(self):
        """15/19 passed = 78% → warn (the real-world case from your analysis)."""
        feats = [{"id": f"f{i}", "status": "passed", "cycles": 1} for i in range(15)]
        feats += [{"id": f"f{i}", "status": "failed", "cycles": 3} for i in range(15, 19)]
        teammates = [{"teammate": "team", "features": feats}]
        m = _compute_metrics(
            teammates, None, None, [], None, [], [], {}, 0, 0,
        )
        assert m["build_status"] == "warn"
        assert m["total_passed"] == 15
        assert m["total_failed"] == 4

    def test_feature_cycles_data(self):
        feats = [
            {"id": "f1", "status": "passed", "description": "Login", "cycles": 2,
             "created_at": "2025-01-01T00:00:00Z", "passed_at": "2025-01-01T01:00:00Z"},
        ]
        teammates = [{"teammate": "eve", "features": feats}]
        m = _compute_metrics(
            teammates, None, None, [], None, [], [], {}, 0, 0,
        )
        fc = m["feature_cycles"]
        assert len(fc) == 1
        assert fc[0]["id"] == "f1"
        assert fc[0]["teammate"] == "eve"
        assert fc[0]["cycles"] == 2
        assert fc[0]["passed"] is True

    def test_roadmap_data(self):
        feats = [
            {"id": "f1", "status": "passed", "description": "Auth"},
            {"id": "f2", "status": "pending", "description": "Profile"},
        ]
        teammates = [{"teammate": "frank", "features": feats}]
        m = _compute_metrics(
            teammates, None, None, [], None, [], [], {}, 0, 0,
        )
        rm = m["roadmap"]
        assert len(rm) == 1
        assert rm[0]["teammate"] == "frank"
        assert rm[0]["passed"] == 1
        assert rm[0]["total"] == 2
        assert len(rm[0]["features"]) == 2


class TestComputeMetricsPipelinePhases:
    """Spec review, outcome, learnings metric aggregation."""

    def test_spec_review_pass(self):
        sr = {"passed": True, "gaps_count": 2, "enrichment": ["a", "b", "c"]}
        m = _compute_metrics(
            [], sr, None, [], None, [], [], {}, 0, 0,
        )
        assert m["sr_status"] == "pass"
        assert m["sr_gaps"] == 2
        assert m["sr_enrichments"] == 3

    def test_spec_review_fail_no_enrichment(self):
        """Gaps found, NO enrichment → fail."""
        sr = {"passed": False, "gaps_count": 5, "enrichment": []}
        m = _compute_metrics(
            [], sr, None, [], None, [], [], {}, 0, 0,
        )
        assert m["sr_status"] == "fail"

    def test_spec_review_warn_with_enrichment(self):
        """Gaps found but resolved via enrichment → warn."""
        sr = {"passed": False, "gaps_count": 3, "enrichment": ["fix1", "fix2"]}
        m = _compute_metrics(
            [], sr, None, [], None, [], [], {}, 0, 0,
        )
        assert m["sr_status"] == "warn"
        assert m["sr_gaps"] == 3
        assert m["sr_enrichments"] == 2

    def test_outcome_pass(self):
        outcome = {"coverage": 85, "met": ["req1", "req2"], "unmet": []}
        m = _compute_metrics(
            [], None, None, [], outcome, [], [], {}, 0, 0,
        )
        assert m["outcome_status"] == "pass"
        assert m["outcome_coverage"] == 85

    def test_outcome_warn_partial(self):
        """60% coverage → warn (partial, not fail)."""
        outcome = {"coverage": 60, "met": ["req1"], "unmet": ["req2"]}
        m = _compute_metrics(
            [], None, None, [], outcome, [], [], {}, 0, 0,
        )
        assert m["outcome_status"] == "warn"

    def test_outcome_fail_low_coverage(self):
        """30% coverage → fail."""
        outcome = {"coverage": 30, "met": ["req1"], "unmet": ["req2", "req3", "req4"]}
        m = _compute_metrics(
            [], None, None, [], outcome, [], [], {}, 0, 0,
        )
        assert m["outcome_status"] == "fail"

    def test_outcome_deferred_requirements(self):
        """Deferred business requirements are tracked separately."""
        outcome = {
            "coverage": 85,
            "met": ["user auth", "API endpoints"],
            "unmet": [],
            "deferred": ["pricing model", "partner integrations"],
        }
        m = _compute_metrics(
            [], None, None, [], outcome, [], [], {}, 0, 0,
        )
        assert m["outcome_status"] == "pass"
        assert m["outcome_deferred"] == ["pricing model", "partner integrations"]
        assert len(m["outcome_deferred"]) == 2

    def test_learnings_severity_counts(self):
        learnings = [
            {"severity": "high", "category": "error-pattern"},
            {"severity": "high", "category": "spec-gap"},
            {"severity": "medium", "category": "assumption"},
            {"severity": "low", "category": "edge-case"},
        ]
        m = _compute_metrics(
            [], None, None, [], None, learnings, [], {}, 0, 0,
        )
        assert m["learnings_high"] == 2
        assert m["learnings_med"] == 1
        assert m["learnings_low"] == 1
        assert m["learnings_total"] == 4
        assert m["learnings_status"] == "pass"

    def test_crossmodel_severity_counts(self):
        issues = [
            {"severity": "high", "description": "SQL injection"},
            {"severity": "medium", "description": "Unused import"},
            {"severity": "low", "description": "Style issue"},
            {"severity": "low", "description": "Naming convention"},
        ]
        m = _compute_metrics(
            [], None, None, issues, None, [], [], {}, 0, 0,
        )
        assert m["cm_high"] == 1
        assert m["cm_med"] == 1
        assert m["cm_low"] == 2

    def test_plan_mode_counts(self):
        transcript = {
            "experts": [{"name": "Alice"}, {"name": "Bob"}],
            "questions": ["q1", "q2"],
            "answers": [
                {"tag": "FACT"},
                {"tag": "DECISION"},
                {"tag": "ASSUMPTION"},
                {"tag": "FACT"},
            ],
        }
        m = _compute_metrics(
            [], None, transcript, [], None, [], [], {}, 0, 0,
        )
        assert m["plan_status"] == "pass"
        assert m["plan_facts"] == 2
        assert m["plan_decisions"] == 1
        assert m["plan_assumptions"] == 1

    def test_build_time_from_commits(self):
        commits = [
            {"timestamp": 1000},
            {"timestamp": 2000},
            {"timestamp": 4600},  # 3600 seconds = 60 minutes
        ]
        m = _compute_metrics(
            [], None, None, [], None, [], commits, {}, 0, 0,
        )
        assert m["total_time_minutes"] == 60


# ── _esc tests ───────────────────────────────────────────────────────

class TestEsc:
    """HTML escaping helper."""

    def test_ampersand(self):
        assert _esc("a & b") == "a &amp; b"

    def test_less_than(self):
        assert _esc("<script>") == "&lt;script&gt;"

    def test_greater_than(self):
        assert _esc("a > b") == "a &gt; b"

    def test_double_quote(self):
        assert _esc('"hello"') == "&quot;hello&quot;"

    def test_combined(self):
        assert _esc('<a href="x">&') == '&lt;a href=&quot;x&quot;&gt;&amp;'

    def test_non_string_input(self):
        assert _esc(42) == "42"
        assert _esc(None) == "None"
