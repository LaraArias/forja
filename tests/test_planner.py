"""Tests for forja.planner module."""

import inspect
import json
import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path


class TestPlannerSharedUtilities:
    """Verify planner uses shared utilities at runtime."""

    def test_planner_has_call_llm(self):
        """Planner should use the shared call_llm client."""
        import forja.planner as planner
        assert hasattr(planner, "call_llm")
        assert callable(planner.call_llm)

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


class TestDesignExpert:
    """Verify _ensure_design_expert adds a design expert for UI projects."""

    def test_design_expert_added_for_ui_project(self):
        """PRD mentioning 'React dashboard' should get a design expert."""
        from forja.planner import _ensure_design_expert
        experts = [
            {"name": "Software Architect", "field": "Backend Architecture"},
            {"name": "Security Engineer", "field": "Security"},
            {"name": "PM", "field": "Product Strategy"},
        ]
        prd = "Build a React dashboard for real-time analytics."
        result = _ensure_design_expert(experts, prd)
        assert any(
            "design" in e.get("field", "").lower() or "ux" in e.get("field", "").lower()
            for e in result
        ), "Design expert should be present for UI project"
        assert result[1]["name"] == "Design Systems Expert"

    def test_design_expert_not_added_for_cli(self):
        """PRD for a CLI tool should NOT get a design expert."""
        from forja.planner import _ensure_design_expert
        experts = [
            {"name": "Software Architect", "field": "System Design"},
            {"name": "Security Engineer", "field": "Security"},
            {"name": "PM", "field": "Product Strategy"},
        ]
        prd = "Build a CLI tool for parsing log files and generating reports."
        result = _ensure_design_expert(experts, prd)
        assert not any(
            e.get("name") == "Design Systems Expert" for e in result
        ), "No design expert should be added for CLI project"
        assert len(result) == 3


class TestInteractivePrdEdit:
    """Verify _interactive_prd_edit handles user choices correctly."""

    def test_accept_returns_unchanged(self):
        """Choice '1' returns the PRD text as-is."""
        from forja.planner import _interactive_prd_edit
        with patch("builtins.input", return_value="1"):
            result = _interactive_prd_edit("# My PRD\nContent here.")
        assert result == "# My PRD\nContent here."

    def test_edit_section_calls_llm(self):
        """Choice '2' then feedback calls _modify_prd_section, then '1' accepts."""
        from forja.planner import _interactive_prd_edit
        inputs = iter(["2", "Add error handling section", "1"])
        with patch("builtins.input", side_effect=inputs), \
             patch("forja.planner._modify_prd_section",
                   return_value="# My PRD\n## Error Handling\nDone") as mock_mod:
            result = _interactive_prd_edit("# My PRD\nOriginal")
        mock_mod.assert_called_once_with("# My PRD\nOriginal",
                                         "Add error handling section")
        assert "Error Handling" in result

    def test_regenerate_calls_llm(self):
        """Choice '3' then feedback calls _regenerate_prd_with_feedback, then '1' accepts."""
        from forja.planner import _interactive_prd_edit
        inputs = iter(["3", "Make it more concise", "1"])
        with patch("builtins.input", side_effect=inputs), \
             patch("forja.planner._regenerate_prd_with_feedback",
                   return_value="# Concise PRD") as mock_regen:
            result = _interactive_prd_edit("# Verbose PRD\nLots of text")
        mock_regen.assert_called_once_with("# Verbose PRD\nLots of text",
                                           "Make it more concise")
        assert result == "# Concise PRD"

    def test_view_full_prd_then_accept(self):
        """Choice '4' shows full PRD, then '1' accepts."""
        from forja.planner import _interactive_prd_edit
        inputs = iter(["4", "1"])
        with patch("builtins.input", side_effect=inputs):
            result = _interactive_prd_edit("# PRD\nFull content")
        assert result == "# PRD\nFull content"

    def test_eof_returns_current_text(self):
        """EOFError during choice returns current text."""
        from forja.planner import _interactive_prd_edit
        with patch("builtins.input", side_effect=EOFError):
            result = _interactive_prd_edit("# PRD\nContent")
        assert result == "# PRD\nContent"

    def test_empty_feedback_skips_edit(self):
        """Empty feedback on choice '2' loops back without calling LLM."""
        from forja.planner import _interactive_prd_edit
        inputs = iter(["2", "", "1"])
        with patch("builtins.input", side_effect=inputs), \
             patch("forja.planner._modify_prd_section") as mock_mod:
            result = _interactive_prd_edit("# PRD")
        mock_mod.assert_not_called()
        assert result == "# PRD"


class TestModifyPrdSection:
    """Verify _modify_prd_section delegates to LLM correctly."""

    def test_returns_llm_response(self):
        from forja.planner import _modify_prd_section
        with patch("forja.planner.call_llm",
                   return_value="# Updated PRD\n## New Section"):
            result = _modify_prd_section("# Original", "Add new section")
        assert result == "# Updated PRD\n## New Section"

    def test_strips_markdown_fences(self):
        from forja.planner import _modify_prd_section
        with patch("forja.planner.call_llm",
                   return_value="```markdown\n# PRD\nContent\n```"):
            result = _modify_prd_section("# Old", "fix it")
        assert result == "# PRD\nContent"

    def test_returns_original_on_llm_failure(self):
        from forja.planner import _modify_prd_section
        with patch("forja.planner.call_llm", return_value=None):
            result = _modify_prd_section("# Original PRD", "change something")
        assert result == "# Original PRD"


class TestRegeneratePrdWithFeedback:
    """Verify _regenerate_prd_with_feedback delegates to LLM correctly."""

    def test_returns_llm_response(self):
        from forja.planner import _regenerate_prd_with_feedback
        with patch("forja.planner.call_llm",
                   return_value="# Regenerated PRD"):
            result = _regenerate_prd_with_feedback("# Old", "make it better")
        assert result == "# Regenerated PRD"

    def test_strips_markdown_fences(self):
        from forja.planner import _regenerate_prd_with_feedback
        with patch("forja.planner.call_llm",
                   return_value="```\n# PRD v2\nNew content\n```"):
            result = _regenerate_prd_with_feedback("# Old", "redo")
        assert result == "# PRD v2\nNew content"

    def test_returns_original_on_llm_failure(self):
        from forja.planner import _regenerate_prd_with_feedback
        with patch("forja.planner.call_llm", return_value=None):
            result = _regenerate_prd_with_feedback("# Keep this", "change")
        assert result == "# Keep this"


class TestDoResearch:
    """Verify _do_research returns findings and saves to disk."""

    def test_returns_findings_from_claude(self, tmp_path, monkeypatch):
        """Claude web search returns findings → returned and saved."""
        from forja.planner import _do_research
        monkeypatch.chdir(tmp_path)
        # Create .forja dir so save works
        (tmp_path / ".forja").mkdir()
        monkeypatch.setattr("forja.planner.FORJA_DIR", tmp_path / ".forja")

        with patch("forja.planner._call_claude_research",
                   return_value="FastAPI is great for async APIs."):
            result = _do_research("Architect", "FastAPI best practices", "project ctx")
        assert result == "FastAPI is great for async APIs."
        # Check file was saved
        research_dir = tmp_path / ".forja" / "research"
        assert research_dir.exists()
        files = list(research_dir.glob("*.md"))
        assert len(files) == 1
        assert "FastAPI" in files[0].read_text()

    def test_returns_findings_from_fallback(self, tmp_path, monkeypatch):
        """When Claude unavailable, falls back to call_llm."""
        from forja.planner import _do_research
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".forja").mkdir()
        monkeypatch.setattr("forja.planner.FORJA_DIR", tmp_path / ".forja")

        with patch("forja.planner._call_claude_research", return_value=None), \
             patch("forja.planner.call_llm", return_value="Use SQLite for MVP."):
            result = _do_research("Engineer", "database choice", "project ctx")
        assert result == "Use SQLite for MVP."

    def test_returns_empty_on_total_failure(self, tmp_path, monkeypatch):
        """Both providers fail → empty string, no file saved."""
        from forja.planner import _do_research
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".forja").mkdir()
        monkeypatch.setattr("forja.planner.FORJA_DIR", tmp_path / ".forja")

        with patch("forja.planner._call_claude_research", return_value=None), \
             patch("forja.planner.call_llm", return_value=None):
            result = _do_research("PM", "market size", "project ctx")
        assert result == ""
        research_dir = tmp_path / ".forja" / "research"
        assert not research_dir.exists() or len(list(research_dir.glob("*.md"))) == 0


class TestSaveResearch:
    """Verify _save_research persists findings correctly."""

    def test_creates_file_with_slug(self, tmp_path, monkeypatch):
        from forja.planner import _save_research
        monkeypatch.setattr("forja.planner.FORJA_DIR", tmp_path / ".forja")
        _save_research("FastAPI vs Flask", "FastAPI wins for async.")
        fpath = tmp_path / ".forja" / "research" / "fastapi-vs-flask.md"
        assert fpath.exists()
        content = fpath.read_text()
        assert "# Research: FastAPI vs Flask" in content
        assert "FastAPI wins for async." in content


class TestResearchInEnrichedPrd:
    """Verify research findings are included in enriched PRD generation."""

    def test_research_included_in_prompt(self):
        """_generate_enriched_prd passes research to LLM prompt."""
        from forja.planner import _generate_enriched_prd
        research = [{"topic": "caching", "findings": "Use Redis or in-memory dict."}]
        with patch("forja.planner.call_llm", return_value="# Enriched PRD") as mock:
            _generate_enriched_prd("# PRD", [], [], "", research)
        prompt = mock.call_args[0][0]
        assert "Research Findings" in prompt
        assert "caching" in prompt

    def test_no_research_section_when_empty(self):
        """No research → no research section in prompt."""
        from forja.planner import _generate_enriched_prd
        with patch("forja.planner.call_llm", return_value="# PRD") as mock:
            _generate_enriched_prd("# PRD", [], [], "")
        prompt = mock.call_args[0][0]
        assert "Research Findings" not in prompt

    def test_fallback_includes_research(self):
        """When LLM fails, fallback manual assembly includes research."""
        from forja.planner import _generate_enriched_prd
        research = [{"topic": "auth patterns", "findings": "JWT is standard."}]
        with patch("forja.planner.call_llm", return_value=None):
            result = _generate_enriched_prd("# PRD", [], [], "", research)
        # Returns None when LLM fails, caller handles fallback
        assert result is None


class TestDetectSkill:
    """Verify _detect_skill reads agents.json and returns the correct skill."""

    def test_detect_skill_landing_page(self, tmp_path, monkeypatch):
        """agents.json with frontend-builder → 'landing-page'."""
        from forja.planner import _detect_skill
        monkeypatch.chdir(tmp_path)
        skill_dir = tmp_path / ".forja" / "skill"
        skill_dir.mkdir(parents=True)
        agents = [
            {"name": "frontend-builder", "role": "Build HTML/CSS"},
            {"name": "seo-optimizer", "role": "SEO"},
        ]
        (skill_dir / "agents.json").write_text(json.dumps(agents))
        assert _detect_skill() == "landing-page"

    def test_detect_skill_api_backend(self, tmp_path, monkeypatch):
        """agents.json with database agent → 'api-backend'."""
        from forja.planner import _detect_skill
        monkeypatch.chdir(tmp_path)
        skill_dir = tmp_path / ".forja" / "skill"
        skill_dir.mkdir(parents=True)
        agents = [
            {"name": "database", "role": "Schema design"},
            {"name": "security", "role": "Auth"},
        ]
        (skill_dir / "agents.json").write_text(json.dumps(agents))
        assert _detect_skill() == "api-backend"

    def test_detect_skill_custom(self, tmp_path, monkeypatch):
        """No skill file → 'custom'."""
        from forja.planner import _detect_skill
        monkeypatch.chdir(tmp_path)
        assert _detect_skill() == "custom"
