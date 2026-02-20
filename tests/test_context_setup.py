"""Tests for forja.context_setup anti-hallucination guardrails."""

from unittest.mock import patch, MagicMock
from pathlib import Path
import json

import pytest


class TestContextSetupAntiHallucination:
    """Verify context_setup LLM calls have anti-hallucination guardrails."""

    def test_company_overview_system_msg_has_guardrail(self, tmp_path):
        """Company overview call has anti-hallucination in system message."""
        from forja.context_setup import _setup_company

        target = tmp_path / "project"
        target.mkdir()

        with (
            patch("forja.context_setup._ask", return_value="Acme Corp"),
            patch("forja.context_setup._ask_choice", return_value=(1, "None")),
            patch("forja.context_setup._call_claude_code", return_value="# Acme Corp\nA company.") as mock_llm,
        ):
            _setup_company(target)

        # The call_llm call for company overview should have anti-hallucination
        assert mock_llm.called
        system_sent = mock_llm.call_args[1].get("system", "")
        assert "do not invent" in system_sent.lower()
        assert "metrics" in system_sent.lower() or "statistics" in system_sent.lower()

    def test_domain_prompt_no_invented_benchmarks(self):
        """Domain context prompt does NOT ask for invented benchmarks."""
        # The old prompt contained "competitor benchmarks (generic for this industry)"
        # which caused the LLM to invent benchmarks. Verify it's gone.
        import forja.context_setup as cs
        import inspect
        source = inspect.getsource(cs._setup_domain)

        # Old hallucination-inducing phrases should NOT be present
        assert "competitor benchmarks (generic" not in source
        assert "proof points and quotes per audience" not in source
        assert "supporting data points" not in source.lower()

    def test_domain_prompt_has_guardrails_in_source(self):
        """Domain context prompt includes anti-hallucination instructions."""
        import forja.context_setup as cs
        import inspect
        source = inspect.getsource(cs._setup_domain)

        # Should have anti-hallucination guardrails
        assert "Do NOT invent" in source or "do not invent" in source
        assert "NEEDS EVIDENCE" in source or "NEEDS DATA" in source

    def test_domain_system_msg_has_guardrail(self, tmp_path):
        """Domain context system message includes anti-hallucination."""
        from forja.context_setup import _setup_domain

        target = tmp_path / "project"
        target.mkdir()

        domain_json = json.dumps({
            "domain_md": "# Domain\nContent",
            "value_props_md": "# Value Props\nContent",
            "objections_md": "# Objections\nContent",
        })

        with (
            patch("forja.context_setup._ask_choice", return_value=(1, "Developers")),
            patch("forja.context_setup._ask", return_value="Fast builds"),
            patch("forja.context_setup._ask_multiline", return_value=["Too expensive"]),
            patch("forja.context_setup._call_claude_code", return_value=domain_json) as mock_llm,
            patch("forja.context_setup.parse_json", return_value=json.loads(domain_json)),
        ):
            _setup_domain(target, "Acme", "A build tool")

        assert mock_llm.called
        system_sent = mock_llm.call_args[1].get("system", "")
        assert "do not invent" in system_sent.lower()

    def test_design_reference_system_msg_has_suggestion_qualifier(self):
        """Design reference system message marks content as suggestions."""
        import forja.context_setup as cs
        import inspect
        source = inspect.getsource(cs._setup_design_system)

        # Should indicate these are suggestions, not mandates
        assert "suggestion" in source.lower()
