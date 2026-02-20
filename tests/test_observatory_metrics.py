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
_shim.load_dotenv = lambda *a, **kw: {}
_shim.call_llm = lambda *a, **kw: ""
_shim._call_claude_code = lambda *a, **kw: ""
_shim.parse_json = lambda *a, **kw: None
sys.modules.setdefault("forja_utils", _shim)

from forja.templates.forja_observatory import (
    _compute_metrics, _esc, _ts_to_filename, _prepare_index_data,
    _build_run_navigation,
)


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

    def test_outcome_tech_coverage_excludes_deferred(self):
        """Technical coverage = met/(met+unmet), deferred items excluded."""
        outcome = {
            "coverage": 50,  # raw coverage from LLM (may include deferred)
            "met": ["auth", "api", "db"],
            "unmet": ["notifications"],
            "deferred": ["pricing", "partnerships", "marketing"],
        }
        m = _compute_metrics(
            [], None, None, [], outcome, [], [], {}, 0, 0,
        )
        # tech_coverage = 3 / (3 + 1) * 100 = 75%
        assert m["outcome_tech_coverage"] == 75
        # 75% >= 50 → warn (not pass since < 80)
        assert m["outcome_status"] == "warn"
        # Raw coverage preserved
        assert m["outcome_coverage"] == 50

    def test_outcome_tech_coverage_all_met(self):
        """When all technical requirements are met, tech_coverage = 100."""
        outcome = {
            "coverage": 60,
            "met": ["auth", "api"],
            "unmet": [],
            "deferred": ["pricing"],
        }
        m = _compute_metrics(
            [], None, None, [], outcome, [], [], {}, 0, 0,
        )
        assert m["outcome_tech_coverage"] == 100
        assert m["outcome_status"] == "pass"

    def test_outcome_tech_coverage_fallback_no_tech_reqs(self):
        """When no technical reqs (empty met+unmet), falls back to raw coverage."""
        outcome = {
            "coverage": 90,
            "met": [],
            "unmet": [],
            "deferred": ["pricing", "partnerships"],
        }
        m = _compute_metrics(
            [], None, None, [], outcome, [], [], {}, 0, 0,
        )
        # No technical reqs → fallback to outcome_coverage
        assert m["outcome_tech_coverage"] == 90
        assert m["outcome_status"] == "pass"

    def test_outcome_status_uses_tech_coverage_not_raw(self):
        """outcome_status is based on tech_coverage, not raw coverage."""
        outcome = {
            "coverage": 30,  # raw looks bad
            "met": ["auth", "api", "db", "frontend"],
            "unmet": ["search"],
            "deferred": ["pricing", "partnerships", "legal", "marketing",
                         "sales", "support", "analytics"],
        }
        m = _compute_metrics(
            [], None, None, [], outcome, [], [], {}, 0, 0,
        )
        # tech_coverage = 4 / (4 + 1) * 100 = 80% → pass
        assert m["outcome_tech_coverage"] == 80
        assert m["outcome_status"] == "pass"
        # raw coverage is still 30
        assert m["outcome_coverage"] == 30

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


# ── _ts_to_filename tests ───────────────────────────────────────────

class TestTsToFilename:
    """ISO timestamp to filename format conversion."""

    def test_iso_with_offset(self):
        assert _ts_to_filename("2025-05-01T10:30:45+00:00") == "20250501-103045"

    def test_iso_with_z(self):
        assert _ts_to_filename("2025-05-01T10:30:45Z") == "20250501-103045"

    def test_iso_with_microseconds(self):
        result = _ts_to_filename("2025-05-01T10:30:45.123456+00:00")
        assert result == "20250501-103045"

    def test_empty_string(self):
        assert _ts_to_filename("") == ""

    def test_none(self):
        assert _ts_to_filename(None) == ""

    def test_invalid_format(self):
        assert _ts_to_filename("not-a-date") == ""


# ── _prepare_index_data tests ───────────────────────────────────────

class TestPrepareIndexDataEmpty:
    """Index data with no runs."""

    def test_no_runs(self):
        data = _prepare_index_data([])
        assert data["total_runs"] == 0
        assert data["runs"] == []


class TestPrepareIndexDataSingleRun:
    """Index data with a single run."""

    def test_single_run_kpis(self):
        runs = [{
            "timestamp": "2025-05-01T10:30:45+00:00",
            "metrics": {
                "total_passed": 5, "total_features": 5,
                "total_blocked": 0, "total_failed": 0,
                "outcome_tech_coverage": 85, "avg_cycles": 1.5,
                "total_time_minutes": 30, "learnings_high": 2,
                "learnings_total": 8, "build_status": "pass",
                "num_teammates": 2,
            }
        }]
        data = _prepare_index_data(runs)
        assert data["total_runs"] == 1
        assert data["total_features_shipped"] == 5
        assert data["overall_success_rate"] == 100.0
        assert data["best_coverage"] == 85
        assert data["avg_build_time_minutes"] == 30
        assert data["total_learnings"] == 8

    def test_single_run_no_deltas(self):
        runs = [{
            "timestamp": "2025-05-01T10:30:45+00:00",
            "metrics": {"total_passed": 3, "total_features": 5,
                        "outcome_tech_coverage": 60, "avg_cycles": 2.0,
                        "total_time_minutes": 40, "build_status": "warn",
                        "learnings_high": 1, "learnings_total": 3,
                        "total_blocked": 0, "total_failed": 2}
        }]
        data = _prepare_index_data(runs)
        assert data["runs"][0]["delta_passed"] is None
        assert data["runs"][0]["delta_coverage"] is None
        assert data["runs"][0]["delta_cycles"] is None

    def test_single_run_trends(self):
        runs = [{
            "timestamp": "2025-05-01T10:30:45+00:00",
            "metrics": {"total_passed": 5, "total_features": 5,
                        "outcome_tech_coverage": 85, "avg_cycles": 1.5,
                        "total_time_minutes": 30, "build_status": "pass",
                        "learnings_high": 2, "learnings_total": 8}
        }]
        data = _prepare_index_data(runs)
        assert data["features_per_run"] == [5]
        assert data["coverage_trend"] == [85]
        assert data["cycles_trend"] == [1.5]
        assert data["learnings_high_trend"] == [2]


class TestPrepareIndexDataMultiRun:
    """Index data with multiple runs."""

    def _make_runs(self):
        return [
            {"timestamp": "2025-05-01T10:00:00+00:00",
             "metrics": {"total_passed": 3, "total_features": 5,
                         "outcome_tech_coverage": 60, "avg_cycles": 2.0,
                         "total_time_minutes": 40, "build_status": "warn",
                         "learnings_high": 3, "learnings_total": 5,
                         "total_blocked": 0, "total_failed": 2,
                         "num_teammates": 2}},
            {"timestamp": "2025-05-02T10:00:00+00:00",
             "metrics": {"total_passed": 5, "total_features": 5,
                         "outcome_tech_coverage": 90, "avg_cycles": 1.2,
                         "total_time_minutes": 25, "build_status": "pass",
                         "learnings_high": 1, "learnings_total": 3,
                         "total_blocked": 0, "total_failed": 0,
                         "num_teammates": 2}},
        ]

    def test_accumulated_features(self):
        data = _prepare_index_data(self._make_runs())
        assert data["total_features_shipped"] == 8  # 3 + 5

    def test_success_rate(self):
        data = _prepare_index_data(self._make_runs())
        assert data["overall_success_rate"] == 50.0  # 1 of 2 runs all-passed

    def test_best_coverage(self):
        data = _prepare_index_data(self._make_runs())
        assert data["best_coverage"] == 90

    def test_avg_build_time(self):
        data = _prepare_index_data(self._make_runs())
        assert data["avg_build_time_minutes"] == 32  # (40+25)/2 rounded

    def test_deltas_computed(self):
        data = _prepare_index_data(self._make_runs())
        r2 = data["runs"][1]
        assert r2["delta_passed"] == 2   # 5 - 3
        assert r2["delta_coverage"] == 30.0  # 90 - 60
        assert r2["delta_cycles"] == -0.8  # 1.2 - 2.0

    def test_trends(self):
        data = _prepare_index_data(self._make_runs())
        assert data["features_per_run"] == [3, 5]
        assert data["coverage_trend"] == [60, 90]
        assert data["cycles_trend"] == [2.0, 1.2]
        assert data["learnings_high_trend"] == [3, 1]

    def test_run_filenames(self):
        data = _prepare_index_data(self._make_runs())
        assert data["runs"][0]["filename"] == "run-20250501-100000.html"
        assert data["runs"][1]["filename"] == "run-20250502-100000.html"

    def test_run_indices_1based(self):
        data = _prepare_index_data(self._make_runs())
        assert data["runs"][0]["index"] == 1
        assert data["runs"][1]["index"] == 2


# ── _build_run_navigation tests ─────────────────────────────────────

class TestBuildRunNavigation:
    """Run navigation (prev/next) for per-run detail pages."""

    def _make_runs(self, n=3):
        return [
            {"timestamp": f"2025-05-0{i+1}T10:00:00+00:00", "metrics": {}}
            for i in range(n)
        ]

    def test_first_run_no_prev(self):
        runs = self._make_runs(3)
        nav = _build_run_navigation(runs, 0)
        assert nav["current_index"] == 1
        assert nav["prev_file"] is None
        assert nav["prev_index"] is None
        assert nav["next_file"] is not None
        assert nav["next_index"] == 2

    def test_last_run_no_next(self):
        runs = self._make_runs(3)
        nav = _build_run_navigation(runs, 2)
        assert nav["current_index"] == 3
        assert nav["prev_file"] is not None
        assert nav["prev_index"] == 2
        assert nav["next_file"] is None
        assert nav["next_index"] is None

    def test_middle_run_has_both(self):
        runs = self._make_runs(3)
        nav = _build_run_navigation(runs, 1)
        assert nav["current_index"] == 2
        assert nav["prev_file"] is not None
        assert nav["prev_index"] == 1
        assert nav["next_file"] is not None
        assert nav["next_index"] == 3

    def test_single_run(self):
        runs = self._make_runs(1)
        nav = _build_run_navigation(runs, 0)
        assert nav["current_index"] == 1
        assert nav["prev_file"] is None
        assert nav["next_file"] is None
