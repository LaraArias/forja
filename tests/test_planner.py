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

    def test_detect_skill_dict_landing_page(self, tmp_path, monkeypatch):
        """skill.json as dict with 'skill' key → 'landing-page'."""
        from forja.planner import _detect_skill
        monkeypatch.chdir(tmp_path)
        tools_dir = tmp_path / ".forja-tools"
        tools_dir.mkdir(parents=True)
        skill_data = {
            "skill": "landing-page",
            "description": "Build a landing page",
            "agents": [
                {"name": "content-strategist", "prompt": "Write copy"},
                {"name": "frontend-builder", "prompt": "Build HTML"},
                {"name": "seo-optimizer", "prompt": "Optimize SEO"},
            ],
        }
        (tools_dir / "skill.json").write_text(json.dumps(skill_data))
        assert _detect_skill() == "landing-page"

    def test_detect_skill_dict_api_backend(self, tmp_path, monkeypatch):
        """skill.json as dict with 'skill': 'api-backend' → 'api-backend'."""
        from forja.planner import _detect_skill
        monkeypatch.chdir(tmp_path)
        tools_dir = tmp_path / ".forja-tools"
        tools_dir.mkdir(parents=True)
        skill_data = {
            "skill": "api-backend",
            "description": "Build API backend",
            "agents": [
                {"name": "architect", "prompt": "Design"},
                {"name": "database", "prompt": "Models"},
                {"name": "backend", "prompt": "Endpoints"},
                {"name": "security", "prompt": "Review"},
                {"name": "qa", "prompt": "Test"},
            ],
        }
        (tools_dir / "skill.json").write_text(json.dumps(skill_data))
        assert _detect_skill() == "api-backend"

    def test_detect_skill_dict_fallback_to_agent_names(self, tmp_path, monkeypatch):
        """Dict without 'skill' key falls back to agent name detection."""
        from forja.planner import _detect_skill
        monkeypatch.chdir(tmp_path)
        tools_dir = tmp_path / ".forja-tools"
        tools_dir.mkdir(parents=True)
        # Dict with agents but no explicit "skill" field
        skill_data = {
            "description": "Custom landing page skill",
            "agents": [
                {"name": "frontend-builder", "prompt": "Build HTML"},
                {"name": "seo-optimizer", "prompt": "Optimize SEO"},
            ],
        }
        (tools_dir / "skill.json").write_text(json.dumps(skill_data))
        assert _detect_skill() == "landing-page"

    def test_detect_skill_dict_unknown_skill_field(self, tmp_path, monkeypatch):
        """Dict with unknown 'skill' value falls back to agent name detection."""
        from forja.planner import _detect_skill
        monkeypatch.chdir(tmp_path)
        tools_dir = tmp_path / ".forja-tools"
        tools_dir.mkdir(parents=True)
        skill_data = {
            "skill": "unknown-skill",
            "agents": [
                {"name": "database", "prompt": "Models"},
                {"name": "security", "prompt": "Auth"},
            ],
        }
        (tools_dir / "skill.json").write_text(json.dumps(skill_data))
        assert _detect_skill() == "api-backend"

    def test_detect_skill_prefers_forja_skill_dir(self, tmp_path, monkeypatch):
        """.forja/skill/agents.json is checked before .forja-tools/skill.json."""
        from forja.planner import _detect_skill
        monkeypatch.chdir(tmp_path)
        # Create both paths with different skills
        skill_dir = tmp_path / ".forja" / "skill"
        skill_dir.mkdir(parents=True)
        tools_dir = tmp_path / ".forja-tools"
        tools_dir.mkdir(parents=True)
        # .forja/skill/agents.json → landing-page
        (skill_dir / "agents.json").write_text(json.dumps(
            [{"name": "frontend-builder"}]
        ))
        # .forja-tools/skill.json → api-backend
        (tools_dir / "skill.json").write_text(json.dumps(
            {"skill": "api-backend", "agents": [{"name": "database"}]}
        ))
        assert _detect_skill() == "landing-page"

    def test_detect_skill_invalid_json(self, tmp_path, monkeypatch):
        """Corrupt skill.json → 'custom' (no crash)."""
        from forja.planner import _detect_skill
        monkeypatch.chdir(tmp_path)
        tools_dir = tmp_path / ".forja-tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "skill.json").write_text("not valid json {{{")
        assert _detect_skill() == "custom"


class TestSkillPrdConstraints:
    """Verify SKILL_PRD_CONSTRAINTS are applied to PRD generation."""

    def test_landing_page_constraint_prepended(self):
        """skill='landing-page' prepends constraint to prompt."""
        from forja.planner import _generate_prd_from_idea, SKILL_PRD_CONSTRAINTS
        with patch("forja.planner.call_llm", return_value=None) as mock_llm:
            _generate_prd_from_idea("A landing page for my SaaS", skill="landing-page")
        prompt_sent = mock_llm.call_args[0][0]
        assert prompt_sent.startswith("CRITICAL CONSTRAINT")
        assert "SINGLE index.html" in prompt_sent
        assert "NO backend" in prompt_sent
        assert "NO Kubernetes" in prompt_sent

    def test_api_backend_constraint_prepended(self):
        """skill='api-backend' prepends FastAPI constraint to prompt."""
        from forja.planner import _generate_prd_from_idea, SKILL_PRD_CONSTRAINTS
        with patch("forja.planner.call_llm", return_value=None) as mock_llm:
            _generate_prd_from_idea("Notes API", skill="api-backend")
        prompt_sent = mock_llm.call_args[0][0]
        assert "CRITICAL CONSTRAINT" in prompt_sent
        assert "FastAPI" in prompt_sent
        assert "SQLite" in prompt_sent

    def test_custom_no_constraint(self):
        """skill='custom' does NOT prepend any constraint."""
        from forja.planner import _generate_prd_from_idea, SKILL_PRD_CONSTRAINTS
        with patch("forja.planner.call_llm", return_value=None) as mock_llm:
            _generate_prd_from_idea("A todo app", skill="custom")
        prompt_sent = mock_llm.call_args[0][0]
        assert not prompt_sent.startswith("CRITICAL CONSTRAINT")

    def test_default_skill_is_custom(self):
        """No skill parameter defaults to 'custom' (no constraint)."""
        from forja.planner import _generate_prd_from_idea
        with patch("forja.planner.call_llm", return_value=None) as mock_llm:
            _generate_prd_from_idea("A todo app")
        prompt_sent = mock_llm.call_args[0][0]
        assert "CRITICAL CONSTRAINT" not in prompt_sent

    def test_constraint_dict_has_all_skills(self):
        """SKILL_PRD_CONSTRAINTS covers all expected skills."""
        from forja.planner import SKILL_PRD_CONSTRAINTS
        assert "landing-page" in SKILL_PRD_CONSTRAINTS
        assert "api-backend" in SKILL_PRD_CONSTRAINTS
        assert "custom" in SKILL_PRD_CONSTRAINTS
        assert SKILL_PRD_CONSTRAINTS["custom"] == ""

    def test_landing_page_prd_with_valid_llm_response(self):
        """Full round-trip: skill constraint → LLM → structured PRD."""
        from forja.planner import _generate_prd_from_idea
        mock_response = json.dumps({
            "title": "Forja Landing Page",
            "problem": "Developers need to understand what Forja does",
            "features": ["Hero section", "How It Works", "CTA"],
            "stack": {"language": "HTML", "framework": "CSS + JS"},
            "out_of_scope": ["Backend API", "Database"],
        })
        with patch("forja.planner.call_llm", return_value=mock_response) as mock_llm:
            prd, title = _generate_prd_from_idea(
                "Landing page for Forja", skill="landing-page"
            )
        assert title == "Forja Landing Page"
        assert "Hero section" in prd
        assert "HTML" in prd
        # Verify constraint was in the prompt
        prompt_sent = mock_llm.call_args[0][0]
        assert "SINGLE index.html" in prompt_sent


class TestScratchFlowSkill:
    """Verify _scratch_flow passes skill to _generate_prd_from_idea."""

    def test_scratch_flow_passes_skill(self):
        """_scratch_flow(skill='landing-page') forwards to _generate_prd_from_idea."""
        from forja.planner import _scratch_flow
        mock_prd = "# Landing\n## Problem\nNeed a page"
        inputs = iter(["A landing page for my product", "1"])
        with patch("builtins.input", side_effect=inputs), \
             patch("forja.planner._generate_prd_from_idea",
                   return_value=(mock_prd, "Landing")) as mock_gen, \
             patch("forja.planner._interactive_prd_edit",
                   return_value=mock_prd), \
             patch("forja.planner.PRD_PATH") as mock_prd_path:
            mock_prd_path.exists.return_value = False
            mock_prd_path.__str__ = lambda s: "context/prd.md"
            mock_prd_path.write_text = MagicMock()
            _scratch_flow(skill="landing-page")
        # Verify skill was passed
        mock_gen.assert_called_once()
        _, kwargs = mock_gen.call_args
        assert kwargs.get("skill") == "landing-page"

    def test_scratch_flow_default_skill_is_custom(self):
        """_scratch_flow() without skill defaults to 'custom'."""
        from forja.planner import _scratch_flow
        mock_prd = "# App\n## Problem\nNeed an app"
        inputs = iter(["A todo app", "1"])
        with patch("builtins.input", side_effect=inputs), \
             patch("forja.planner._generate_prd_from_idea",
                   return_value=(mock_prd, "App")) as mock_gen, \
             patch("forja.planner._interactive_prd_edit",
                   return_value=mock_prd), \
             patch("forja.planner.PRD_PATH") as mock_prd_path:
            mock_prd_path.exists.return_value = False
            mock_prd_path.__str__ = lambda s: "context/prd.md"
            mock_prd_path.write_text = MagicMock()
            _scratch_flow()
        mock_gen.assert_called_once()
        _, kwargs = mock_gen.call_args
        assert kwargs.get("skill") == "custom"


class TestRunExpertQa:
    """Verify _run_expert_qa orchestrates a full Q&A round."""

    def test_fallback_when_llm_returns_none(self):
        """When LLM returns None, uses fallback experts and questions."""
        from forja.planner import (
            _run_expert_qa, WHAT_PANEL_PROMPT,
            FALLBACK_WHAT_EXPERTS, FALLBACK_WHAT_QUESTIONS,
        )
        # Simulate user pressing Enter (accept default) for all questions
        with patch("forja.planner.call_llm", return_value=None), \
             patch("builtins.input", return_value=""):
            experts, questions, transcript, research, assessment = _run_expert_qa(
                prompt_template=WHAT_PANEL_PROMPT,
                fallback_experts=FALLBACK_WHAT_EXPERTS,
                fallback_questions=FALLBACK_WHAT_QUESTIONS,
                prd_content="# Test PRD",
                prd_title="Test",
                context="",
                skill_guidance="",
                round_label="WHAT",
                max_questions=6,
            )
        assert len(experts) >= 2
        assert len(transcript) == len(questions)
        # All answers are DECISION (accepted defaults)
        assert all(a["tag"] == "DECISION" for a in transcript)

    def test_done_fills_remaining_with_assumption(self):
        """Typing 'done' fills remaining questions with ASSUMPTION defaults."""
        from forja.planner import (
            _run_expert_qa, HOW_PANEL_PROMPT,
            FALLBACK_HOW_EXPERTS, FALLBACK_HOW_QUESTIONS,
        )
        # First answer: custom, second: "done"
        inputs = iter(["My custom answer", "done"])
        with patch("forja.planner.call_llm", return_value=None), \
             patch("builtins.input", side_effect=inputs):
            experts, questions, transcript, research, _ = _run_expert_qa(
                prompt_template=HOW_PANEL_PROMPT,
                fallback_experts=FALLBACK_HOW_EXPERTS,
                fallback_questions=FALLBACK_HOW_QUESTIONS,
                prd_content="# Test PRD",
                prd_title="Test",
                context="",
                skill_guidance="",
                round_label="HOW",
                max_questions=7,
            )
        # First answer is FACT, rest are ASSUMPTION
        assert transcript[0]["tag"] == "FACT"
        assert transcript[0]["answer"] == "My custom answer"
        assert all(a["tag"] == "ASSUMPTION" for a in transcript[1:])
        assert len(transcript) == len(questions)

    def test_returns_five_tuple(self):
        """Return type is (experts, questions, transcript, research, assessment)."""
        from forja.planner import (
            _run_expert_qa, WHAT_PANEL_PROMPT,
            FALLBACK_WHAT_EXPERTS, FALLBACK_WHAT_QUESTIONS,
        )
        with patch("forja.planner.call_llm", return_value=None), \
             patch("builtins.input", return_value=""):
            result = _run_expert_qa(
                prompt_template=WHAT_PANEL_PROMPT,
                fallback_experts=FALLBACK_WHAT_EXPERTS,
                fallback_questions=FALLBACK_WHAT_QUESTIONS,
                prd_content="# PRD",
                prd_title="T",
                context="",
                skill_guidance="",
                round_label="WHAT",
            )
        assert len(result) == 5
        experts, questions, transcript, research, assessment = result
        assert isinstance(experts, list)
        assert isinstance(questions, list)
        assert isinstance(transcript, list)
        assert isinstance(research, list)
        assert isinstance(assessment, str)


class TestSkillGuidance:
    """Verify skill-specific guidance for WHAT and HOW rounds."""

    def test_what_landing_page(self):
        from forja.planner import _get_skill_what_guidance
        g = _get_skill_what_guidance("landing-page")
        assert "CTA" in g
        assert "tone" in g.lower() or "copy" in g.lower()

    def test_what_api_backend(self):
        from forja.planner import _get_skill_what_guidance
        g = _get_skill_what_guidance("api-backend")
        assert "endpoint" in g.lower()
        assert "data model" in g.lower()

    def test_how_landing_page(self):
        from forja.planner import _get_skill_how_guidance
        g = _get_skill_how_guidance("landing-page")
        assert "HTML" in g or "html" in g.lower()

    def test_how_api_backend(self):
        from forja.planner import _get_skill_how_guidance
        g = _get_skill_how_guidance("api-backend")
        assert "FastAPI" in g or "Flask" in g
        assert "SQLite" in g

    def test_custom_returns_empty(self):
        from forja.planner import _get_skill_what_guidance, _get_skill_how_guidance
        assert _get_skill_what_guidance("custom") == ""
        assert _get_skill_how_guidance("custom") == ""


class TestFallbackConstants:
    """Verify WHAT/HOW fallback experts and questions structure."""

    def test_what_experts_are_product_focused(self):
        from forja.planner import FALLBACK_WHAT_EXPERTS
        assert len(FALLBACK_WHAT_EXPERTS) == 3
        names = [e["name"] for e in FALLBACK_WHAT_EXPERTS]
        assert "Product Strategist" in names
        assert "Target Audience Expert" in names

    def test_what_questions_count(self):
        from forja.planner import FALLBACK_WHAT_QUESTIONS
        assert len(FALLBACK_WHAT_QUESTIONS) == 6
        for q in FALLBACK_WHAT_QUESTIONS:
            assert "question" in q
            assert "default" in q

    def test_how_experts_have_build_feasibility(self):
        from forja.planner import FALLBACK_HOW_EXPERTS
        assert len(FALLBACK_HOW_EXPERTS) == 3
        assert FALLBACK_HOW_EXPERTS[0]["name"] == "Build Feasibility Engineer"

    def test_how_questions_count(self):
        from forja.planner import FALLBACK_HOW_QUESTIONS
        assert len(FALLBACK_HOW_QUESTIONS) == 7
        # First question should be stack override
        assert "STACK OVERRIDE" in FALLBACK_HOW_QUESTIONS[0]["question"]


class TestSaveTranscriptRounds:
    """Verify _save_transcript writes round-based JSON."""

    def test_saves_with_rounds_key(self, tmp_path, monkeypatch):
        from forja.planner import _save_transcript
        monkeypatch.setattr("forja.planner.FORJA_DIR", tmp_path / ".forja")

        round_data = [
            {"round": "WHAT", "experts": [{"name": "PM"}],
             "questions": [], "answers": [{"tag": "FACT", "answer": "yes"}]},
            {"round": "HOW", "experts": [{"name": "Eng"}],
             "questions": [], "answers": [{"tag": "DECISION", "answer": "FastAPI"}]},
        ]
        path = _save_transcript(round_data, "# Final PRD")
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "rounds" in data
        assert len(data["rounds"]) == 2
        assert data["rounds"][0]["round"] == "WHAT"
        assert data["rounds"][1]["round"] == "HOW"
        assert data["enriched_prd_length"] == len("# Final PRD")

    def test_saves_research_log(self, tmp_path, monkeypatch):
        from forja.planner import _save_transcript
        monkeypatch.setattr("forja.planner.FORJA_DIR", tmp_path / ".forja")

        research = [{"topic": "auth", "findings": "Use JWT"}]
        path = _save_transcript([], "# PRD", research)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["research"] == research


class TestPromptContent:
    """Verify WHAT and HOW prompts have the correct focus."""

    def test_what_prompt_is_product_focused(self):
        from forja.planner import WHAT_PANEL_PROMPT
        prompt = WHAT_PANEL_PROMPT.lower()
        assert "product experts" in prompt
        assert "target audience" in prompt
        assert "messaging" in prompt
        # Should NOT ask about technical details
        assert "database" not in prompt or "do not ask about" in prompt

    def test_how_prompt_is_technical_focused(self):
        from forja.planner import HOW_PANEL_PROMPT
        prompt = HOW_PANEL_PROMPT.lower()
        assert "technical experts" in prompt
        assert "veto power" in prompt
        assert "build feasibility engineer" in prompt
        assert "pip" in prompt or "npm" in prompt
