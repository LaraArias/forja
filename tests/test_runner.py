"""Tests for forja.runner module."""

import inspect
import json
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


class TestGenerateWorkflowFeatures:
    """Verify _generate_workflow_features creates correct feature files."""

    def test_generate_workflow_features_creates_files(self, tmp_path):
        """Workflow phases generate one features.json per agent."""
        import json
        from forja.runner import _generate_workflow_features

        workflow_path = tmp_path / "workflow.json"
        workflow = {
            "phases": [
                {"agent": "content-strategist", "role": "Content Strategy",
                 "validation": "Generate copy-brief.md", "output": "copy-brief.md",
                 "input": []},
                {"agent": "frontend-builder", "role": "Frontend Build",
                 "validation": "Build index.html", "output": "index.html",
                 "input": ["copy-brief.md"]},
                {"agent": "qa", "role": "QA", "validation": "All tests pass",
                 "output": "qa-report.json", "input": ["index.html"]},
            ]
        }
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

        teammates_dir = tmp_path / "teammates"
        _generate_workflow_features(workflow_path, teammates_dir)

        # Check that 3 dirs and features.json were created
        for i, phase in enumerate(workflow["phases"]):
            agent = phase["agent"]
            fj = teammates_dir / agent / "features.json"
            assert fj.exists(), f"features.json missing for {agent}"
            data = json.loads(fj.read_text())
            feat = data["features"][0]
            assert feat["id"] == f"{agent}-001"
            assert feat["phase_order"] == i + 1
            assert feat["status"] == "pending"
            assert feat["output"] == phase["output"]

    def test_generate_workflow_features_creates_phase_prompt(self, tmp_path):
        """Workflow phases with prompts generate phase_prompt.md."""
        from forja.runner import _generate_workflow_features

        workflow_path = tmp_path / "workflow.json"
        workflow = {
            "phases": [
                {"agent": "architect", "role": "Software Architect",
                 "validation": "architecture.md exists",
                 "output": "artifacts/architecture.md",
                 "input": ["context/prd.md"],
                 "prompt": "You are the Architect. Read the PRD. Design the data model."},
                {"agent": "backend", "role": "Backend Developer",
                 "validation": "main.py exists",
                 "output": "src/main.py",
                 "input": ["artifacts/architecture.md"]},
            ]
        }
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

        teammates_dir = tmp_path / "teammates"
        _generate_workflow_features(workflow_path, teammates_dir)

        # Agent with prompt gets phase_prompt.md
        prompt_file = teammates_dir / "architect" / "phase_prompt.md"
        assert prompt_file.exists()
        content = prompt_file.read_text(encoding="utf-8")
        assert "Software Architect" in content or "architect" in content
        assert "Read the PRD" in content

        # Agent without prompt does NOT get phase_prompt.md
        assert not (teammates_dir / "backend" / "phase_prompt.md").exists()


# ── Agent Context tests ──────────────────────────────────────────────

from forja.runner import _generate_agent_context


class TestGenerateAgentContext:
    """Verify _generate_agent_context creates bounded context files."""

    def test_creates_context_md_from_file_inputs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        # Create input files
        (tmp_path / "context").mkdir()
        (tmp_path / "context" / "prd.md").write_text(
            "# PRD\n\nBuild a task manager API.", encoding="utf-8"
        )

        workflow_path = tmp_path / "workflow.json"
        workflow = {
            "phases": [
                {"agent": "architect", "role": "Software Architect",
                 "input": ["context/prd.md"], "output": "artifacts/architecture.md"},
            ]
        }
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

        teammates_dir = tmp_path / "teammates"
        _generate_agent_context(workflow_path, teammates_dir)

        ctx = teammates_dir / "architect" / "context.md"
        assert ctx.exists()
        content = ctx.read_text(encoding="utf-8")
        assert "Software Architect" in content
        assert "task manager API" in content

    def test_creates_context_md_from_directory_inputs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        # Create context/company/ with files
        company_dir = tmp_path / "context" / "company"
        company_dir.mkdir(parents=True)
        (company_dir / "company-overview.md").write_text(
            "# Acme Corp\n\nWe build widgets.", encoding="utf-8"
        )
        (company_dir / "tech-stack.md").write_text(
            "# Tech Stack\n\nPython, FastAPI.", encoding="utf-8"
        )

        workflow_path = tmp_path / "workflow.json"
        workflow = {
            "phases": [
                {"agent": "content-strategist", "role": "Content Strategist",
                 "input": ["context/company/"], "output": "artifacts/copy-brief.md"},
            ]
        }
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

        teammates_dir = tmp_path / "teammates"
        _generate_agent_context(workflow_path, teammates_dir)

        ctx = teammates_dir / "content-strategist" / "context.md"
        assert ctx.exists()
        content = ctx.read_text(encoding="utf-8")
        assert "Acme Corp" in content
        assert "FastAPI" in content

    def test_bounded_context_excludes_other_domains(self, tmp_path, monkeypatch):
        """Agent that only reads design-system should NOT see company context."""
        monkeypatch.chdir(tmp_path)

        # Create both company and design-system
        (tmp_path / "context" / "company").mkdir(parents=True)
        (tmp_path / "context" / "company" / "company-overview.md").write_text(
            "# Secret Corp\n\nTop secret info.", encoding="utf-8"
        )
        (tmp_path / "context" / "design-system").mkdir(parents=True)
        (tmp_path / "context" / "design-system" / "DESIGN-REFERENCE.md").write_text(
            "# Design\n\nUse blue and white.", encoding="utf-8"
        )

        workflow_path = tmp_path / "workflow.json"
        workflow = {
            "phases": [
                {"agent": "design-planner", "role": "Design Planner",
                 "input": ["context/design-system/"], "output": "artifacts/design-spec.md"},
            ]
        }
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

        teammates_dir = tmp_path / "teammates"
        _generate_agent_context(workflow_path, teammates_dir)

        ctx = teammates_dir / "design-planner" / "context.md"
        content = ctx.read_text(encoding="utf-8")
        assert "blue and white" in content
        # Should NOT contain company info
        assert "Secret Corp" not in content
        assert "Top secret" not in content

    def test_skips_missing_input_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        workflow_path = tmp_path / "workflow.json"
        workflow = {
            "phases": [
                {"agent": "frontend", "role": "Frontend",
                 "input": ["artifacts/copy-brief.md", "artifacts/design-spec.md"],
                 "output": "index.html"},
            ]
        }
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

        teammates_dir = tmp_path / "teammates"
        _generate_agent_context(workflow_path, teammates_dir)

        # No input files exist → no context.md created
        assert not (teammates_dir / "frontend" / "context.md").exists()

    def test_caps_context_at_6000_chars(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        big_dir = tmp_path / "context" / "big"
        big_dir.mkdir(parents=True)
        # Create many files to exceed 6000 chars total
        for i in range(10):
            (big_dir / f"doc{i:02d}.md").write_text(
                f"# Doc {i}\n\n" + "Y" * 900, encoding="utf-8"
            )

        workflow_path = tmp_path / "workflow.json"
        workflow = {
            "phases": [
                {"agent": "reader", "role": "Reader",
                 "input": ["context/big/"], "output": "out.md"},
            ]
        }
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

        teammates_dir = tmp_path / "teammates"
        _generate_agent_context(workflow_path, teammates_dir)

        ctx = teammates_dir / "reader" / "context.md"
        content = ctx.read_text(encoding="utf-8")
        assert len(content) <= 6200  # 6000 + truncation notice + newline
        assert "truncated" in content

    def test_skips_underscore_and_readme_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        ctx_dir = tmp_path / "context" / "learnings"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "_learnings.md").write_text("private wisdom", encoding="utf-8")
        (ctx_dir / "README.md").write_text("readme content", encoding="utf-8")
        (ctx_dir / "errors.md").write_text("public errors", encoding="utf-8")

        workflow_path = tmp_path / "workflow.json"
        workflow = {
            "phases": [
                {"agent": "analyst", "role": "Analyst",
                 "input": ["context/learnings/"], "output": "report.md"},
            ]
        }
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

        teammates_dir = tmp_path / "teammates"
        _generate_agent_context(workflow_path, teammates_dir)

        ctx = teammates_dir / "analyst" / "context.md"
        content = ctx.read_text(encoding="utf-8")
        assert "public errors" in content
        assert "private wisdom" not in content
        assert "readme content" not in content


# ── Iteration Snapshot tests ─────────────────────────────────────────

from forja.runner import (
    _next_iteration_number,
    _save_iteration_snapshot,
    ITERATIONS_DIR,
)


class TestNextIterationNumber:
    """Verify _next_iteration_number counting."""

    def test_returns_1_when_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("forja.runner.ITERATIONS_DIR", tmp_path / ".forja" / "iterations")
        assert _next_iteration_number() == 1

    def test_returns_1_when_empty_dir(self, tmp_path, monkeypatch):
        idir = tmp_path / ".forja" / "iterations"
        idir.mkdir(parents=True)
        monkeypatch.setattr("forja.runner.ITERATIONS_DIR", idir)
        assert _next_iteration_number() == 1

    def test_returns_next_number(self, tmp_path, monkeypatch):
        idir = tmp_path / ".forja" / "iterations"
        (idir / "v001").mkdir(parents=True)
        (idir / "v002").mkdir(parents=True)
        monkeypatch.setattr("forja.runner.ITERATIONS_DIR", idir)
        assert _next_iteration_number() == 3


class TestSaveIterationSnapshot:
    """Verify _save_iteration_snapshot creates correct files."""

    def test_creates_snapshot_dir_with_all_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        idir = tmp_path / ".forja" / "iterations"
        monkeypatch.setattr("forja.runner.ITERATIONS_DIR", idir)

        # Stub _count_features (no real features in test env)
        monkeypatch.setattr("forja.runner._count_features", lambda: (5, 3, 1))

        # Create fake outcome report
        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir(exist_ok=True)
        monkeypatch.setattr("forja.runner.FORJA_DIR", forja_dir)
        (forja_dir / "outcome-report.json").write_text(
            json.dumps({"coverage": 85}), encoding="utf-8"
        )

        old_prd = "# PRD\n\nOriginal content.\n"
        new_prd = "# PRD\n\nUpdated content with changes.\n"

        result = _save_iteration_snapshot(
            run_number=1,
            feedback_text="Fix auth dependencies",
            old_prd=old_prd,
            new_prd=new_prd,
        )

        assert result is not None
        snapshot = idir / "v001"
        assert snapshot.exists()

        # Check manifest.json
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["run_number"] == 1
        assert manifest["features"]["total"] == 5
        assert manifest["features"]["passed"] == 3
        assert manifest["features"]["blocked"] == 1
        assert manifest["outcome_coverage"] == 85
        assert "timestamp" in manifest
        assert "prd_hash" in manifest

        # Check feedback.md
        feedback = (snapshot / "feedback.md").read_text(encoding="utf-8")
        assert "Fix auth dependencies" in feedback

        # Check prd-diff.md
        diff = (snapshot / "prd-diff.md").read_text(encoding="utf-8")
        assert "PRD Changes" in diff
        assert "diff" in diff  # Contains ```diff block
        # Should show actual diff since content changed
        assert "(no changes)" not in diff

    def test_snapshot_no_diff_when_same_prd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        idir = tmp_path / ".forja" / "iterations"
        monkeypatch.setattr("forja.runner.ITERATIONS_DIR", idir)
        monkeypatch.setattr("forja.runner._count_features", lambda: (0, 0, 0))

        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir(exist_ok=True)
        monkeypatch.setattr("forja.runner.FORJA_DIR", forja_dir)

        prd = "# PRD\n\nSame content.\n"

        result = _save_iteration_snapshot(
            run_number=2,
            feedback_text="(re-run with learnings, no PRD change)",
            old_prd=prd,
            new_prd=prd,
        )

        assert result is not None
        diff = (idir / "v002" / "prd-diff.md").read_text(encoding="utf-8")
        assert "(no changes)" in diff

    def test_snapshot_returns_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        idir = tmp_path / ".forja" / "iterations"
        monkeypatch.setattr("forja.runner.ITERATIONS_DIR", idir)
        monkeypatch.setattr("forja.runner._count_features", lambda: (0, 0, 0))

        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir(exist_ok=True)
        monkeypatch.setattr("forja.runner.FORJA_DIR", forja_dir)

        result = _save_iteration_snapshot(1, "test", "old", "new")
        assert result == idir / "v001"


# ── Feedback Loop tests ──────────────────────────────────────────────

from forja.runner import (
    _persist_planning_decisions,
    _log_test_failures_as_learnings,
    _persist_outcome_gaps,
)


class TestPersistPlanningDecisions:
    """Verify planning decisions are saved to context store."""

    def test_persists_transcript_to_store(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        monkeypatch.setattr("forja.runner.FORJA_DIR", forja_dir)

        # Create plan-transcript.json
        transcript = {
            "answers": [
                {"question": "What database to use?", "answer": "SQLite for simplicity"},
                {"question": "Auth strategy?", "answer": "JWT with bcrypt"},
            ]
        }
        (forja_dir / "plan-transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )

        # Create a fake forja_context.py script
        tools_dir = tmp_path / ".forja-tools"
        tools_dir.mkdir()
        monkeypatch.setattr("forja.runner.FORJA_TOOLS", tools_dir)

        # Write a minimal script that saves args to a file
        script = tools_dir / "forja_context.py"
        script.write_text(
            "import sys, json\n"
            "args = sys.argv[1:]\n"
            "(open('.forja/ctx_call.json','w')).write(json.dumps(args))\n",
            encoding="utf-8",
        )

        _persist_planning_decisions()

        # Verify the context script was called with correct args
        call_file = forja_dir / "ctx_call.json"
        assert call_file.exists()
        args = json.loads(call_file.read_text(encoding="utf-8"))
        assert args[0] == "set"
        assert args[1] == "planning.decisions"
        assert "What database to use?" in args[2]
        assert "SQLite" in args[2]

    def test_skips_when_no_transcript(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        monkeypatch.setattr("forja.runner.FORJA_DIR", forja_dir)
        monkeypatch.setattr("forja.runner.FORJA_TOOLS", tmp_path / ".forja-tools")

        # No plan-transcript.json → should not crash
        _persist_planning_decisions()

    def test_skips_empty_answers(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        monkeypatch.setattr("forja.runner.FORJA_DIR", forja_dir)
        monkeypatch.setattr("forja.runner.FORJA_TOOLS", tmp_path / ".forja-tools")

        transcript = {"answers": []}
        (forja_dir / "plan-transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )

        _persist_planning_decisions()  # Should not crash


class TestLogTestFailuresAsLearnings:
    """Verify test failures are logged as learnings."""

    def test_logs_failures(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tools_dir = tmp_path / ".forja-tools"
        tools_dir.mkdir()
        monkeypatch.setattr("forja.runner.FORJA_TOOLS", tools_dir)

        # Write a script that appends args to a log file
        script = tools_dir / "forja_learnings.py"
        script.write_text(
            "import sys, json\n"
            "args = sys.argv[1:]\n"
            "with open('.forja/learn_calls.jsonl','a') as f:\n"
            "    f.write(json.dumps(args) + '\\n')\n",
            encoding="utf-8",
        )

        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir(exist_ok=True)

        test_results = {
            "framework": "pytest",
            "exit_code": 1,
            "passed": 3,
            "failed": 2,
            "output": "FAILED test_auth.py::test_login - AssertionError\nFAILED test_api.py::test_create - 404",
        }

        _log_test_failures_as_learnings(test_results)

        calls_file = forja_dir / "learn_calls.jsonl"
        assert calls_file.exists()
        lines = calls_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # 2 FAILED lines found

        first_call = json.loads(lines[0])
        assert first_call[0] == "log"
        assert "--category" in first_call
        assert "error-pattern" in first_call
        assert "--severity" in first_call
        assert "high" in first_call

    def test_skips_when_no_failures(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("forja.runner.FORJA_TOOLS", tmp_path / ".forja-tools")

        test_results = {"framework": "pytest", "exit_code": 0, "passed": 5, "failed": 0, "output": ""}
        _log_test_failures_as_learnings(test_results)
        # No crash, no calls

    def test_skips_when_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("forja.runner.FORJA_TOOLS", tmp_path / ".forja-tools")
        _log_test_failures_as_learnings(None)
        _log_test_failures_as_learnings({})


class TestPersistOutcomeGaps:
    """Verify unmet requirements are saved to context store."""

    def test_persists_unmet_requirements(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        monkeypatch.setattr("forja.runner.FORJA_DIR", forja_dir)

        tools_dir = tmp_path / ".forja-tools"
        tools_dir.mkdir()
        monkeypatch.setattr("forja.runner.FORJA_TOOLS", tools_dir)

        # Create outcome report with unmet requirements
        outcome = {
            "pass": False,
            "coverage": 60,
            "met": ["User registration"],
            "unmet": ["Email notifications", "OAuth integration"],
        }
        (forja_dir / "outcome-report.json").write_text(
            json.dumps(outcome), encoding="utf-8"
        )

        # Create a fake forja_context.py script
        script = tools_dir / "forja_context.py"
        script.write_text(
            "import sys, json\n"
            "args = sys.argv[1:]\n"
            "(open('.forja/ctx_call.json','w')).write(json.dumps(args))\n",
            encoding="utf-8",
        )

        _persist_outcome_gaps()

        call_file = forja_dir / "ctx_call.json"
        assert call_file.exists()
        args = json.loads(call_file.read_text(encoding="utf-8"))
        assert args[0] == "set"
        assert args[1] == "outcome.unmet_requirements"
        assert "Email notifications" in args[2]
        assert "OAuth integration" in args[2]

    def test_skips_when_all_met(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        monkeypatch.setattr("forja.runner.FORJA_DIR", forja_dir)
        monkeypatch.setattr("forja.runner.FORJA_TOOLS", tmp_path / ".forja-tools")

        outcome = {"pass": True, "coverage": 100, "met": ["All features"], "unmet": []}
        (forja_dir / "outcome-report.json").write_text(
            json.dumps(outcome), encoding="utf-8"
        )

        _persist_outcome_gaps()  # No crash, no calls

    def test_handles_dict_unmet_items(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        forja_dir = tmp_path / ".forja"
        forja_dir.mkdir()
        monkeypatch.setattr("forja.runner.FORJA_DIR", forja_dir)

        tools_dir = tmp_path / ".forja-tools"
        tools_dir.mkdir()
        monkeypatch.setattr("forja.runner.FORJA_TOOLS", tools_dir)

        outcome = {
            "pass": False,
            "coverage": 50,
            "met": [],
            "unmet": [
                {"requirement": "Pricing model", "type": "business"},
                "Email notifications",
            ],
        }
        (forja_dir / "outcome-report.json").write_text(
            json.dumps(outcome), encoding="utf-8"
        )

        script = tools_dir / "forja_context.py"
        script.write_text(
            "import sys, json\n"
            "args = sys.argv[1:]\n"
            "(open('.forja/ctx_call.json','w')).write(json.dumps(args))\n",
            encoding="utf-8",
        )

        _persist_outcome_gaps()

        args = json.loads((forja_dir / "ctx_call.json").read_text(encoding="utf-8"))
        assert "Pricing model" in args[2]
        assert "Email notifications" in args[2]
