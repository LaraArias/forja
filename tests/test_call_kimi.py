"""Tests for forja.utils.call_kimi (backward-compat wrapper)."""

import json
import pytest
from unittest.mock import patch, MagicMock
from forja.utils import call_kimi


@pytest.fixture(autouse=True)
def _reset_config():
    from forja.config_loader import reset_config
    reset_config()
    yield
    reset_config()


class TestNoApiKey:
    """Raises when KIMI_API_KEY is not set."""

    def test_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
        # Patch load_dotenv so it doesn't reload the key from ~/.forja/config.env
        with patch("forja.utils.load_dotenv"):
            with pytest.raises(RuntimeError, match="KIMI_API_KEY not set"):
                call_kimi("hello")


class TestSuccessfulCall:
    """Returns content from a successful API response."""

    def test_returns_content(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "test-key-123")

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "Hello from Kimi!"}}]
        }).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = call_kimi("hello")

        assert result == "Hello from Kimi!"


class TestErrorHandling:
    """Raises on various failure modes."""

    def test_http_error_raises(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "test-key-123")

        import urllib.error
        error = urllib.error.HTTPError(
            url="https://api.moonshot.ai/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=MagicMock(read=lambda: b"rate limited"),
        )

        with patch("urllib.request.urlopen", side_effect=error):
            with pytest.raises(RuntimeError, match="Kimi: HTTP 429"):
                call_kimi("hello")

    def test_timeout_raises(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "test-key-123")

        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with pytest.raises(RuntimeError, match="timeout"):
                call_kimi("hello")


class TestRequestPayload:
    """Verifies the request is constructed correctly."""

    def test_sends_correct_payload(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "my-secret-key")

        captured_request = {}

        def mock_urlopen(req, **kwargs):
            captured_request["url"] = req.full_url
            captured_request["data"] = json.loads(req.data.decode("utf-8"))
            captured_request["headers"] = dict(req.headers)
            captured_request["method"] = req.method

            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": "ok"}}]
            }).encode("utf-8")
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            call_kimi("test prompt", system="be helpful")

        assert captured_request["method"] == "POST"
        assert "moonshot.ai" in captured_request["url"]
        assert captured_request["data"]["messages"] == [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "test prompt"},
        ]
        assert "Bearer my-secret-key" in captured_request["headers"].get("Authorization", "")
