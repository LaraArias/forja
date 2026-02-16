"""Tests for previously-untested critical functions.

Covers the 5 most critical functions with zero test coverage:
1. call_anthropic  (utils.py)  - Claude LLM client
2. gather_context  (utils.py)  - business context injection
3. _extract_severity_counts  (runner.py) - severity parsing
4. _append_enrichment_to_prd  (runner.py) - PRD auto-enrichment
5. _format_duration  (runner.py) - progress monitoring display
"""

import json
import os
import shutil
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_config():
    from forja.config_loader import reset_config
    reset_config()
    yield
    reset_config()


# ── 1. _call_anthropic_raw ────────────────────────────────────────────


class TestCallAnthropicRawNoKey:
    """_call_anthropic_raw raises when ANTHROPIC_API_KEY is missing."""

    def test_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from forja.utils import _call_anthropic_raw
        with patch("forja.utils.load_dotenv"):
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY not set"):
                _call_anthropic_raw("hi", "", "claude-sonnet-4-20250514")


class TestCallAnthropicRawSuccess:
    """_call_anthropic_raw returns text content on successful API call."""

    def test_returns_content(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from forja.utils import _call_anthropic_raw

        api_response = json.dumps({
            "content": [
                {"type": "text", "text": "Hello from Claude"}
            ]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _call_anthropic_raw("hi", "", "claude-sonnet-4-20250514")

        assert result == "Hello from Claude"

    def test_joins_multiple_text_blocks(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from forja.utils import _call_anthropic_raw

        api_response = json.dumps({
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _call_anthropic_raw("hi", "", "claude-sonnet-4-20250514")

        assert result == "Part 1\nPart 2"

    def test_raises_for_empty_content(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from forja.utils import _call_anthropic_raw

        api_response = json.dumps({"content": []}).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Empty response"):
                _call_anthropic_raw("hi", "", "claude-sonnet-4-20250514")


class TestCallAnthropicRawPayload:
    """_call_anthropic_raw sends correct payload structure."""

    def test_sends_correct_payload(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from forja.utils import _call_anthropic_raw

        api_response = json.dumps({
            "content": [{"type": "text", "text": "ok"}]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        captured_req = None

        def capture_urlopen(req, **kwargs):
            nonlocal captured_req
            captured_req = req
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=capture_urlopen):
            _call_anthropic_raw("test", "You are helpful", "claude-sonnet-4-20250514")

        assert captured_req is not None
        payload = json.loads(captured_req.data.decode("utf-8"))
        assert payload["messages"] == [{"role": "user", "content": "test"}]
        assert payload["system"] == "You are helpful"
        # urllib title-cases header keys
        assert "Anthropic-version" in captured_req.headers
        assert captured_req.headers["X-api-key"] == "sk-test-key"

    def test_omits_system_when_empty(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from forja.utils import _call_anthropic_raw

        api_response = json.dumps({
            "content": [{"type": "text", "text": "ok"}]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        captured_req = None

        def capture_urlopen(req, **kwargs):
            nonlocal captured_req
            captured_req = req
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=capture_urlopen):
            _call_anthropic_raw("test", "", "claude-sonnet-4-20250514")

        payload = json.loads(captured_req.data.decode("utf-8"))
        assert "system" not in payload

    def test_includes_tools_when_provided(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from forja.utils import _call_anthropic_raw

        api_response = json.dumps({
            "content": [{"type": "text", "text": "ok"}]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        captured_req = None

        def capture_urlopen(req, **kwargs):
            nonlocal captured_req
            captured_req = req
            return mock_resp

        tools = [{"name": "web_search", "type": "tool"}]
        with patch("urllib.request.urlopen", side_effect=capture_urlopen):
            _call_anthropic_raw("test", "", "claude-sonnet-4-20250514", tools=tools)

        payload = json.loads(captured_req.data.decode("utf-8"))
        assert payload["tools"] == tools


class TestCallAnthropicRawErrors:
    """_call_anthropic_raw raises on errors."""

    def test_http_error_raises(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from forja.utils import _call_anthropic_raw

        err = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=429,
            msg="Rate limited",
            hdrs=MagicMock(),
            fp=MagicMock(),
        )
        err.read = MagicMock(return_value=b"rate limited")

        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="Claude: HTTP 429"):
                _call_anthropic_raw("hi", "", "claude-sonnet-4-20250514")

    def test_timeout_raises(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from forja.utils import _call_anthropic_raw

        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with pytest.raises(RuntimeError, match="timeout"):
                _call_anthropic_raw("hi", "", "claude-sonnet-4-20250514")

    def test_invalid_json_raises(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from forja.utils import _call_anthropic_raw

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json at all"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="unexpected response"):
                _call_anthropic_raw("hi", "", "claude-sonnet-4-20250514")


# ── 2. gather_context ──────────────────────────────────────────────────


class TestGatherContext:
    """gather_context reads business context from company/domains/design-system."""

    def test_reads_company_files(self, tmp_path):
        from forja.utils import gather_context

        company = tmp_path / "company"
        company.mkdir()
        (company / "stack.md").write_text("# Stack\nWe use FastAPI and PostgreSQL.")

        result = gather_context(tmp_path)
        assert "FastAPI" in result
        assert "### company" in result

    def test_reads_domains_files(self, tmp_path):
        from forja.utils import gather_context

        domains = tmp_path / "domains"
        domains.mkdir()
        (domains / "fintech.md").write_text("# Fintech\nPCI-DSS compliance required.")

        result = gather_context(tmp_path)
        assert "PCI-DSS" in result

    def test_reads_design_system_files(self, tmp_path):
        from forja.utils import gather_context

        ds = tmp_path / "design-system"
        ds.mkdir()
        (ds / "tokens.json").write_text('{"primary": "#0066FF"}')

        result = gather_context(tmp_path)
        assert "#0066FF" in result

    def test_skips_readme_files(self, tmp_path):
        from forja.utils import gather_context

        company = tmp_path / "company"
        company.mkdir()
        (company / "README.md").write_text("This is a README and should be skipped.")
        (company / "stack.md").write_text("We use Python.")

        result = gather_context(tmp_path)
        assert "should be skipped" not in result
        assert "Python" in result

    def test_skips_non_doc_extensions(self, tmp_path):
        from forja.utils import gather_context

        company = tmp_path / "company"
        company.mkdir()
        (company / "binary.exe").write_text("fake binary")
        (company / "script.py").write_text("print('hello')")

        result = gather_context(tmp_path)
        assert "fake binary" not in result
        assert "hello" not in result

    def test_empty_dirs_return_empty_string(self, tmp_path):
        from forja.utils import gather_context

        result = gather_context(tmp_path)
        assert result == ""

    def test_respects_max_chars(self, tmp_path):
        from forja.utils import gather_context

        company = tmp_path / "company"
        company.mkdir()
        for i in range(50):
            (company / f"doc_{i:02d}.md").write_text(f"# Doc {i}\n{'x' * 200}")

        result = gather_context(tmp_path, max_chars=500)
        assert len(result) <= 600  # allows some slack for truncation message

    def test_truncation_message_included(self, tmp_path):
        from forja.utils import gather_context

        company = tmp_path / "company"
        company.mkdir()
        for i in range(50):
            (company / f"doc_{i:02d}.md").write_text(f"# Doc {i}\n{'x' * 200}")

        result = gather_context(tmp_path, max_chars=500)
        assert "truncated" in result

    def test_priority_order_company_first(self, tmp_path):
        from forja.utils import gather_context

        (tmp_path / "company").mkdir()
        (tmp_path / "company" / "info.md").write_text("COMPANY_CONTENT")
        (tmp_path / "domains").mkdir()
        (tmp_path / "domains" / "info.md").write_text("DOMAINS_CONTENT")

        result = gather_context(tmp_path)
        company_idx = result.index("COMPANY_CONTENT")
        domains_idx = result.index("DOMAINS_CONTENT")
        assert company_idx < domains_idx

    def test_reads_nested_subdirectories(self, tmp_path):
        from forja.utils import gather_context

        nested = tmp_path / "domains" / "fintech" / "sub"
        nested.mkdir(parents=True)
        (nested / "rules.md").write_text("Nested compliance rules")

        result = gather_context(tmp_path)
        assert "Nested compliance rules" in result

    def test_skips_empty_files(self, tmp_path):
        from forja.utils import gather_context

        company = tmp_path / "company"
        company.mkdir()
        (company / "empty.md").write_text("")
        (company / "real.md").write_text("Real content")

        result = gather_context(tmp_path)
        assert "Real content" in result
        # empty file should not produce a header
        lines = [l for l in result.splitlines() if "empty" in l.lower()]
        assert len(lines) == 0


# ── 3. _extract_severity_counts ────────────────────────────────────────


class TestExtractSeverityCounts:
    """_extract_severity_counts parses specreview JSON output."""

    def test_all_severities(self):
        from forja.runner import _extract_severity_counts

        stdout = json.dumps({
            "gaps": [
                {"severity": "high", "description": "a"},
                {"severity": "high", "description": "b"},
                {"severity": "medium", "description": "c"},
                {"severity": "low", "description": "d"},
            ]
        })
        result = _extract_severity_counts(stdout)
        assert "2 high" in result
        assert "1 medium" in result
        assert "1 low" in result

    def test_high_only(self):
        from forja.runner import _extract_severity_counts

        stdout = json.dumps({
            "gaps": [{"severity": "high", "description": "x"}]
        })
        result = _extract_severity_counts(stdout)
        assert result == "1 high"

    def test_medium_only(self):
        from forja.runner import _extract_severity_counts

        stdout = json.dumps({
            "gaps": [{"severity": "medium", "description": "x"}]
        })
        result = _extract_severity_counts(stdout)
        assert result == "1 medium"

    def test_empty_gaps(self):
        from forja.runner import _extract_severity_counts

        stdout = json.dumps({"gaps": []})
        result = _extract_severity_counts(stdout)
        assert result == ""

    def test_no_json(self):
        from forja.runner import _extract_severity_counts

        result = _extract_severity_counts("just some text output")
        assert result == ""

    def test_invalid_json(self):
        from forja.runner import _extract_severity_counts

        result = _extract_severity_counts("{bad json")
        assert result == ""

    def test_empty_string(self):
        from forja.runner import _extract_severity_counts

        result = _extract_severity_counts("")
        assert result == ""

    def test_json_on_last_line(self):
        from forja.runner import _extract_severity_counts

        stdout = "Some text\nMore text\n" + json.dumps({
            "gaps": [
                {"severity": "high", "description": "a"},
                {"severity": "low", "description": "b"},
            ]
        })
        result = _extract_severity_counts(stdout)
        assert "1 high" in result
        assert "1 low" in result

    def test_case_insensitive_severity(self):
        from forja.runner import _extract_severity_counts

        stdout = json.dumps({
            "gaps": [{"severity": "HIGH", "description": "x"}]
        })
        result = _extract_severity_counts(stdout)
        assert "1 high" in result


# ── 4. _append_enrichment_to_prd ───────────────────────────────────────


class TestAppendEnrichmentToPrd:
    """_append_enrichment_to_prd writes enrichment section to PRD."""

    def test_appends_enrichment(self, tmp_path):
        from forja.runner import _append_enrichment_to_prd

        prd = tmp_path / "prd.md"
        prd.write_text("# My PRD\n\nBuild a task API.\n")

        enrichment = ["Max title: 255 chars", "Password min: 8 chars"]
        _append_enrichment_to_prd(str(prd), enrichment, [])

        content = prd.read_text()
        assert "## Additional Specifications (auto-generated by Forja)" in content
        assert "- Max title: 255 chars" in content
        assert "- Password min: 8 chars" in content

    def test_appends_assumptions(self, tmp_path):
        from forja.runner import _append_enrichment_to_prd

        prd = tmp_path / "prd.md"
        prd.write_text("# My PRD\n\nBuild a task API.\n")

        _append_enrichment_to_prd(
            str(prd),
            ["Max title: 255 chars"],
            ["SQLite is sufficient for MVP"],
        )

        content = prd.read_text()
        assert "### Assumptions" in content
        assert "- SQLite is sufficient for MVP" in content

    def test_idempotent_does_not_duplicate(self, tmp_path):
        from forja.runner import _append_enrichment_to_prd

        prd = tmp_path / "prd.md"
        prd.write_text("# My PRD\n\nBuild a task API.\n")

        enrichment = ["Max title: 255 chars"]
        _append_enrichment_to_prd(str(prd), enrichment, [])
        _append_enrichment_to_prd(str(prd), enrichment, [])

        content = prd.read_text()
        count = content.count("## Additional Specifications")
        assert count == 1

    def test_preserves_original_content(self, tmp_path):
        from forja.runner import _append_enrichment_to_prd

        original = "# My PRD\n\nBuild a task API with auth.\n"
        prd = tmp_path / "prd.md"
        prd.write_text(original)

        _append_enrichment_to_prd(str(prd), ["spec 1"], [])

        content = prd.read_text()
        assert content.startswith(original)

    def test_consolidates_large_enrichments(self, tmp_path):
        from forja.runner import _append_enrichment_to_prd

        prd = tmp_path / "prd.md"
        prd.write_text("# PRD\n\nBuild something.\n")

        enrichment = [f"Spec item {i}" for i in range(25)]
        _append_enrichment_to_prd(str(prd), enrichment, [])

        content = prd.read_text()
        # First 20 should be bullets
        assert "- Spec item 0" in content
        assert "- Spec item 19" in content
        # Remaining 5 should be consolidated
        assert "5 minor specifications" in content

    def test_empty_enrichment_and_assumptions(self, tmp_path):
        from forja.runner import _append_enrichment_to_prd

        original = "# PRD\n\nContent.\n"
        prd = tmp_path / "prd.md"
        prd.write_text(original)

        _append_enrichment_to_prd(str(prd), [], [])

        content = prd.read_text()
        # Section header is added even if empty (just with no bullets)
        assert "## Additional Specifications" in content


# ── 5. _format_duration ────────────────────────────────────────────────


class TestFormatDuration:
    """_format_duration converts seconds to human-readable string."""

    def test_seconds_only(self):
        from forja.runner import _format_duration
        assert _format_duration(45) == "45s"

    def test_zero_seconds(self):
        from forja.runner import _format_duration
        assert _format_duration(0) == "0s"

    def test_exact_minute(self):
        from forja.runner import _format_duration
        assert _format_duration(60) == "1m 0s"

    def test_minutes_and_seconds(self):
        from forja.runner import _format_duration
        assert _format_duration(125) == "2m 5s"

    def test_large_duration(self):
        from forja.runner import _format_duration
        assert _format_duration(3661) == "61m 1s"

    def test_float_truncates(self):
        from forja.runner import _format_duration
        assert _format_duration(45.9) == "45s"


# ── 6. _run_project_tests ────────────────────────────────────────────


class TestRunProjectTests:
    """_run_project_tests detects and runs the project's own test suite."""

    def test_no_tests_returns_framework_none(self, tmp_path):
        """Empty project dir with no tests returns framework=None."""
        from forja.runner import _run_project_tests

        results = _run_project_tests(tmp_path)
        assert results["framework"] is None
        assert results["exit_code"] == -1
        # Results should be saved to .forja/test-results.json
        results_file = tmp_path / ".forja" / "test-results.json"
        assert results_file.exists()
        saved = json.loads(results_file.read_text())
        assert saved["framework"] is None

    def test_detects_pytest(self, tmp_path):
        """Detects Python pytest when tests/test_*.py files exist."""
        from forja.runner import _run_project_tests

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_example.py").write_text(
            "def test_one(): assert 1 + 1 == 2\n"
            "def test_two(): assert True\n"
        )

        results = _run_project_tests(tmp_path)
        assert results["framework"] == "pytest"
        assert results["passed"] == 2
        assert results["failed"] == 0
        assert results["exit_code"] == 0

    @pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")
    def test_detects_npm(self, tmp_path):
        """Detects npm test when package.json has a test script."""
        from forja.runner import _run_project_tests

        pkg = {"name": "test-project", "scripts": {"test": "echo ok"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        results = _run_project_tests(tmp_path)
        assert results["framework"] == "npm"
        # npm test with "echo ok" should exit 0
        assert results["exit_code"] == 0
