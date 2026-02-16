"""Tests for forja.planner module."""

import inspect
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestPlannerSharedUtilities:
    """Verify planner uses shared utilities at runtime."""

    def test_planner_has_call_kimi(self):
        """Planner should use the shared call_kimi client."""
        import forja.planner as planner
        assert hasattr(planner, "call_kimi")
        assert callable(planner.call_kimi)

    def test_planner_has_parse_json(self):
        """Planner should use shared JSON parsing."""
        import forja.planner as planner
        assert hasattr(planner, "parse_json")
        assert callable(planner.parse_json)

    def test_planner_has_load_dotenv(self):
        """Planner should use shared env loading."""
        import forja.planner as planner
        assert hasattr(planner, "load_dotenv")
        assert callable(planner.load_dotenv)


class TestPlannerSignatures:
    """Verify planner function signatures."""

    def test_run_plan_returns_bool(self):
        from forja.planner import run_plan
        sig = inspect.signature(run_plan)
        assert sig.return_annotation is bool or sig.return_annotation == "bool"

    def test_run_plan_accepts_called_from_runner(self):
        from forja.planner import run_plan
        sig = inspect.signature(run_plan)
        assert "_called_from_runner" in sig.parameters


class TestTechnicalExpert:
    """Verify _ensure_technical_expert guarantees a technical expert."""

    def test_injects_when_no_tech_expert(self):
        """Panel of pure product/UX experts gets a tech expert injected."""
        from forja.planner import _ensure_technical_expert, TECHNICAL_EXPERT
        experts = [
            {"name": "UX Researcher", "field": "User Experience"},
            {"name": "Marketing Lead", "field": "Growth Strategy"},
            {"name": "Content Designer", "field": "Content Strategy"},
        ]
        questions = [{"id": 1, "expert_name": "UX Researcher", "question": "q?", "default": "d"}]
        new_experts, new_questions = _ensure_technical_expert(experts, questions)
        assert new_experts[2] == TECHNICAL_EXPERT
        assert any(q["expert_name"] == "Build Feasibility Engineer" for q in new_questions)

    def test_keeps_existing_tech_expert(self):
        """Panel that already has a backend engineer is left alone."""
        from forja.planner import _ensure_technical_expert
        experts = [
            {"name": "Backend Engineer", "field": "Backend Architecture"},
            {"name": "UX Lead", "field": "Design"},
            {"name": "PM", "field": "Product"},
        ]
        questions = [{"id": 1}]
        new_experts, new_questions = _ensure_technical_expert(experts, questions)
        assert new_experts[0]["name"] == "Backend Engineer"
        assert len(new_questions) == 1  # no extra questions added

    def test_recognizes_architect_keyword(self):
        """'Software Architect' in name triggers tech detection."""
        from forja.planner import _ensure_technical_expert
        experts = [
            {"name": "Software Architect", "field": "System Design"},
            {"name": "Designer", "field": "UX"},
            {"name": "PM", "field": "Product"},
        ]
        questions = []
        new_experts, new_questions = _ensure_technical_expert(experts, questions)
        # Architect keyword detected in name — no injection
        assert new_experts[0]["name"] == "Software Architect"
        assert len(new_questions) == 0

    def test_handles_fewer_than_3_experts(self):
        """Panel with <3 experts appends instead of replacing."""
        from forja.planner import _ensure_technical_expert, TECHNICAL_EXPERT
        experts = [{"name": "Designer", "field": "UX"}]
        questions = []
        new_experts, _ = _ensure_technical_expert(experts, questions)
        assert len(new_experts) == 2
        assert TECHNICAL_EXPERT in new_experts

    def test_technical_questions_get_sequential_ids(self):
        """Injected questions get IDs continuing from the max existing."""
        from forja.planner import _ensure_technical_expert, TECHNICAL_QUESTIONS
        experts = [
            {"name": "A", "field": "Marketing"},
            {"name": "B", "field": "Sales"},
            {"name": "C", "field": "Design"},
        ]
        questions = [{"id": 1}, {"id": 2}, {"id": 5}]
        _, new_qs = _ensure_technical_expert(experts, questions)
        tech_qs = [q for q in new_qs if q.get("expert_name") == "Build Feasibility Engineer"]
        assert len(tech_qs) == len(TECHNICAL_QUESTIONS)
        assert tech_qs[0]["id"] == 6
        assert tech_qs[1]["id"] == 7
        assert tech_qs[2]["id"] == 8

    def test_fallback_experts_already_have_tech(self):
        """FALLBACK_EXPERTS has Software Architect — no injection needed."""
        from forja.planner import _ensure_technical_expert, FALLBACK_EXPERTS, FALLBACK_QUESTIONS
        experts = list(FALLBACK_EXPERTS)
        questions = list(FALLBACK_QUESTIONS)
        new_experts, new_questions = _ensure_technical_expert(experts, questions)
        # No changes — Software Architect matches "architect" keyword
        assert len(new_experts) == 3
        assert len(new_questions) == len(FALLBACK_QUESTIONS)
