"""Tests for multi-provider LLM support (call_llm, _call_provider, _call_claude_code).

Covers:
1. Auto fallback - tries providers in order, falls back on failure
2. Explicit provider - calls the specified provider directly
3. Unknown provider - raises ValueError
4. Backward-compat wrappers - call_kimi/call_anthropic delegate to call_llm
5. OpenAI raw provider - _call_openai_raw sends correct payload
6. _call_claude_code - Claude Code CLI helper with fallback
"""

import json
import os
import signal
import subprocess
import urllib.error
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_config():
    from forja.config_loader import reset_config
    reset_config()
    yield
    reset_config()


class TestAutoFallback:
    """call_llm with provider='auto' tries kimi → anthropic → openai."""

    def test_falls_back_to_anthropic_when_kimi_fails(self, monkeypatch):
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from forja.utils import call_llm

        api_response = json.dumps({
            "content": [{"type": "text", "text": "from anthropic"}]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("forja.utils.load_dotenv"), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            result = call_llm("hello", provider="auto")

        assert result == "from anthropic"

    def test_returns_empty_when_all_providers_fail(self, monkeypatch):
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from forja.utils import call_llm

        with patch("forja.utils.load_dotenv"):
            result = call_llm("hello", provider="auto")

        assert result == ""


class TestExplicitProvider:
    """call_llm with explicit provider calls only that provider."""

    def test_explicit_kimi_success(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "test-key")

        from forja.utils import call_llm

        api_response = json.dumps({
            "choices": [{"message": {"content": "from kimi"}}]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = call_llm("hello", provider="kimi")

        assert result == "from kimi"

    def test_explicit_provider_raises_on_failure(self, monkeypatch):
        monkeypatch.delenv("KIMI_API_KEY", raising=False)

        from forja.utils import call_llm

        with patch("forja.utils.load_dotenv"):
            with pytest.raises(RuntimeError, match="KIMI_API_KEY not set"):
                call_llm("hello", provider="kimi")


class TestUnknownProvider:
    """call_llm raises ValueError for unknown providers."""

    def test_raises_value_error(self):
        from forja.utils import call_llm

        with pytest.raises(ValueError, match="Unknown provider: gemini"):
            call_llm("hello", provider="gemini")


class TestBackwardCompatWrappers:
    """call_kimi and call_anthropic wrap call_llm with the correct provider."""

    def test_call_kimi_delegates(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "test-key")

        from forja.utils import call_kimi

        api_response = json.dumps({
            "choices": [{"message": {"content": "kimi response"}}]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = call_kimi("test prompt", system="be helpful")

        assert result == "kimi response"

    def test_call_anthropic_delegates(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        from forja.utils import call_anthropic

        api_response = json.dumps({
            "content": [{"type": "text", "text": "claude response"}]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = api_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = call_anthropic("test prompt", system="be helpful")

        assert result == "claude response"


class TestOpenAIRaw:
    """_call_openai_raw sends correct payload to OpenAI API."""

    def test_sends_correct_payload(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

        from forja.utils import _call_openai_raw

        api_response = json.dumps({
            "choices": [{"message": {"content": "openai response"}}]
        }).encode("utf-8")

        captured_req = None

        def capture_urlopen(req, **kwargs):
            nonlocal captured_req
            captured_req = req
            mock_resp = MagicMock()
            mock_resp.read.return_value = api_response
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=capture_urlopen):
            result = _call_openai_raw("hello", "be helpful", "gpt-4o")

        assert result == "openai response"
        assert captured_req is not None
        assert "api.openai.com" in captured_req.full_url
        payload = json.loads(captured_req.data.decode("utf-8"))
        assert payload["model"] == "gpt-4o"
        assert payload["messages"] == [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hello"},
        ]
        assert "Bearer sk-openai-test" in captured_req.headers.get("Authorization", "")

    def test_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from forja.utils import _call_openai_raw

        with patch("forja.utils.load_dotenv"):
            with pytest.raises(RuntimeError, match="OPENAI_API_KEY not set"):
                _call_openai_raw("hello", "", "gpt-4o")


class TestCallClaudeCode:
    """_call_claude_code invokes Claude Code CLI with fallback to call_llm."""

    def test_uses_cli_when_available(self, monkeypatch):
        from forja.utils import _call_claude_code

        monkeypatch.setattr("forja.utils.shutil.which", lambda _: "/usr/local/bin/claude")

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"CLI response text", b"")
        mock_proc.returncode = 0
        mock_proc.pid = 12345

        with patch("forja.utils.subprocess.Popen", return_value=mock_proc) as mock_popen:
            result = _call_claude_code("test prompt")

        assert result == "CLI response text"
        args = mock_popen.call_args[0][0]
        assert args[0] == "claude"
        assert "--dangerously-skip-permissions" in args
        assert "-p" in args
        assert "--output-format" in args
        assert "text" in args

    def test_combines_system_and_user_prompt(self, monkeypatch):
        from forja.utils import _call_claude_code

        monkeypatch.setattr("forja.utils.shutil.which", lambda _: "/usr/local/bin/claude")

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"response", b"")
        mock_proc.returncode = 0
        mock_proc.pid = 12345

        with patch("forja.utils.subprocess.Popen", return_value=mock_proc) as mock_popen:
            _call_claude_code("user msg", system="system msg")

        args = mock_popen.call_args[0][0]
        prompt_arg_idx = args.index("-p") + 1
        assert args[prompt_arg_idx] == "system msg\n\nuser msg"

    def test_falls_back_when_no_cli(self, monkeypatch):
        from forja.utils import _call_claude_code

        monkeypatch.setattr("forja.utils.shutil.which", lambda _: None)

        with patch("forja.utils.call_llm", return_value="api response") as mock_llm:
            result = _call_claude_code("hello", system="sys")

        assert result == "api response"
        mock_llm.assert_called_once_with("hello", system="sys", provider="anthropic")

    def test_falls_back_on_timeout(self, monkeypatch):
        from forja.utils import _call_claude_code

        monkeypatch.setattr("forja.utils.shutil.which", lambda _: "/usr/local/bin/claude")

        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=120)
        mock_proc.pid = 12345
        mock_proc.wait.return_value = 0

        monkeypatch.setattr("forja.utils.os.getpgid", lambda pid: pid)
        monkeypatch.setattr("forja.utils.os.killpg", lambda pgid, sig: None)

        with patch("forja.utils.subprocess.Popen", return_value=mock_proc), \
             patch("forja.utils.call_llm", return_value="fallback") as mock_llm:
            result = _call_claude_code("hello")

        assert result == "fallback"
        mock_llm.assert_called_once()

    def test_falls_back_on_nonzero_exit(self, monkeypatch):
        from forja.utils import _call_claude_code

        monkeypatch.setattr("forja.utils.shutil.which", lambda _: "/usr/local/bin/claude")

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"error occurred")
        mock_proc.returncode = 1
        mock_proc.pid = 12345

        with patch("forja.utils.subprocess.Popen", return_value=mock_proc), \
             patch("forja.utils.call_llm", return_value="fallback") as mock_llm:
            result = _call_claude_code("hello")

        assert result == "fallback"
        mock_llm.assert_called_once()
