"""Tests for forja.runner module."""

import inspect
import pytest
from pathlib import Path
from forja.runner import _prd_needs_planning


class TestRunnerSharedUtilities:
    """Verify runner uses shared utilities at runtime."""

    def test_runner_has_shared_colors(self):
        """Runner should re-export shared color constants from utils."""
        import forja.runner as runner
        # These are imported from forja.utils — verify they exist at runtime
        assert hasattr(runner, "BOLD")
        assert hasattr(runner, "RESET")
        assert hasattr(runner, "GREEN")
        assert hasattr(runner, "RED")

    def test_runner_has_read_feature_status(self):
        """Runner should have the canonical status helper."""
        import forja.runner as runner
        assert hasattr(runner, "read_feature_status")
        assert callable(runner.read_feature_status)

    def test_runner_has_safe_read_json(self):
        """Runner should use the shared safe_read_json."""
        import forja.runner as runner
        assert hasattr(runner, "safe_read_json")
        assert callable(runner.safe_read_json)


class TestRunnerEnglishHeaders:
    """Verify runner injects English context section headers."""

    def test_context_section_headers_are_english(self):
        """The context injection function should use English headers."""
        import forja.runner as runner
        source = Path(runner.__file__).read_text(encoding="utf-8")

        english_markers = [
            "Shared Context",
            "Previous Decisions",
            "Learnings from Previous Runs",
            "Business Context",
            "Additional Specifications",
        ]
        for marker in english_markers:
            assert marker in source, (
                f"Missing English context header '{marker}' in runner.py"
            )

    def test_no_spanish_context_headers(self):
        """Ensure old Spanish headers are gone."""
        import forja.runner as runner
        source = Path(runner.__file__).read_text(encoding="utf-8")

        spanish_markers = [
            "Contexto Compartido",
            "Decisiones previas",
            "Learnings de corridas anteriores",
            "Contexto del negocio",
            "Especificaciones Adicionales",
        ]
        for marker in spanish_markers:
            assert marker not in source, (
                f"Found Spanish string '{marker}' in runner.py"
            )


class TestRunnerSignatures:
    """Verify runner functions have correct signatures."""

    def test_run_forja_returns_bool(self):
        from forja.runner import run_forja
        sig = inspect.signature(run_forja)
        assert sig.return_annotation is bool or sig.return_annotation == "bool"

    def test_run_forja_accepts_prd_path(self):
        from forja.runner import run_forja
        sig = inspect.signature(run_forja)
        assert "prd_path" in sig.parameters

    def test_count_features_is_callable(self):
        from forja.runner import _count_features
        assert callable(_count_features)


class TestRunnerAutoInit:
    """Verify runner has auto-init and auto-plan capabilities."""

    def test_has_project_marker_constants(self):
        """Runner should access CLAUDE_MD and FORJA_TOOLS at runtime."""
        import forja.runner as runner
        assert hasattr(runner, "CLAUDE_MD")
        assert hasattr(runner, "FORJA_TOOLS")

    def test_prd_needs_planning_callable(self):
        """The placeholder detection function should be importable."""
        assert callable(_prd_needs_planning)


class TestPrdNeedsPlanning:
    """Verify _prd_needs_planning detects placeholders correctly."""

    def test_missing_file(self, tmp_path):
        assert _prd_needs_planning(tmp_path / "nonexistent.md") is True

    def test_empty_file(self, tmp_path):
        prd = tmp_path / "prd.md"
        prd.write_text("", encoding="utf-8")
        assert _prd_needs_planning(prd) is True

    def test_whitespace_only(self, tmp_path):
        prd = tmp_path / "prd.md"
        prd.write_text("   \n\n  \n", encoding="utf-8")
        assert _prd_needs_planning(prd) is True

    def test_default_placeholder_english(self, tmp_path):
        """The exact template written by forja init."""
        prd = tmp_path / "prd.md"
        prd.write_text("# PRD\n\nDescribe your project here.\n", encoding="utf-8")
        assert _prd_needs_planning(prd) is True

    def test_default_placeholder_spanish(self, tmp_path):
        prd = tmp_path / "prd.md"
        prd.write_text("# PRD\n\nDescribe tu proyecto aqui.\n", encoding="utf-8")
        assert _prd_needs_planning(prd) is True

    def test_heading_only_short(self, tmp_path):
        """Just a heading with a few words — under 50 chars of body."""
        prd = tmp_path / "prd.md"
        prd.write_text("# My Project\n\nShort.\n", encoding="utf-8")
        assert _prd_needs_planning(prd) is True

    def test_real_prd_passes(self, tmp_path):
        """A real PRD with >50 chars of body content skips planning."""
        prd = tmp_path / "prd.md"
        prd.write_text(
            "# Task Manager API\n\n"
            "A RESTful API for managing tasks with CRUD operations, "
            "authentication, and real-time notifications via WebSockets.\n\n"
            "## Features\n- Create tasks\n- List tasks\n- Delete tasks\n",
            encoding="utf-8",
        )
        assert _prd_needs_planning(prd) is False

    def test_enriched_prd_passes(self, tmp_path):
        """An enriched PRD from the planner should definitely pass."""
        prd = tmp_path / "prd.md"
        content = "# Task Manager\n\n" + ("x " * 100) + "\n\n## Technical Decisions\n- [FACT] auth: JWT\n"
        prd.write_text(content, encoding="utf-8")
        assert _prd_needs_planning(prd) is False
