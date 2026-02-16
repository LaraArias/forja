"""Tests for forja.runner module."""

import pytest
from pathlib import Path
from forja.runner import _prd_needs_planning


class TestRunnerImports:
    """Verify runner uses shared utilities."""

    def test_imports_from_utils(self):
        """Runner should import from forja.utils, not define its own."""
        import forja.runner as runner
        source = Path(runner.__file__).read_text(encoding="utf-8")

        # Should import from forja.utils
        assert "from forja.utils import" in source

        # Should NOT have local ANSI definitions
        assert 'RESET = "\\033' not in source
        assert 'GREEN = "\\033' not in source
        assert "def _load_env" not in source


class TestRunnerEnglish:
    """Verify runner uses English strings."""

    def test_no_spanish_context_headers(self):
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

    def test_has_english_context_headers(self):
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
                f"Missing English string '{marker}' in runner.py"
            )


class TestRunnerTypeAnnotations:
    """Verify runner has type annotations."""

    def test_has_future_annotations(self):
        import forja.runner as runner
        source = Path(runner.__file__).read_text(encoding="utf-8")
        assert "from __future__ import annotations" in source

    def test_run_forja_has_return_type(self):
        import forja.runner as runner
        source = Path(runner.__file__).read_text(encoding="utf-8")
        assert "def run_forja(" in source
        # Find the line with run_forja definition
        for line in source.splitlines():
            if "def run_forja(" in line:
                assert "-> bool" in line, "run_forja should have -> bool return type"
                break


class TestRunnerAutoInit:
    """Verify runner auto-initializes when project is not scaffolded."""

    def test_imports_project_markers(self):
        """Runner should import CLAUDE_MD and FORJA_TOOLS from constants."""
        import forja.runner as runner
        source = Path(runner.__file__).read_text(encoding="utf-8")
        assert "CLAUDE_MD" in source
        assert "FORJA_TOOLS" in source

    def test_auto_init_import(self):
        """run_forja should import run_init for auto-scaffolding."""
        import forja.runner as runner
        source = Path(runner.__file__).read_text(encoding="utf-8")
        assert "from forja.init import run_init" in source

    def test_auto_plan_import(self):
        """run_forja should import run_plan for auto-planning."""
        import forja.runner as runner
        source = Path(runner.__file__).read_text(encoding="utf-8")
        assert "from forja.planner import run_plan" in source


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
