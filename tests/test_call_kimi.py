"""Tests for forja.utils.call_kimi."""

import json
import pytest
from unittest.mock import patch, MagicMock
from forja.utils import call_kimi


class TestNoApiKey:
    """Returns None when KIMI_API_KEY is not set."""

    def test_returns_none_without_key(self, monkeypatch):
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
        # Patch load_dotenv so it doesn't reload the key from ~/.forja/config.env
        with patch("forja.utils.load_dotenv"):
            result = call_kimi([{"role": "user", "content": "hello"}])
        assert result is None


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

        with patch("forja.utils.urllib.request.urlopen", return_value=mock_response):
            result = call_kimi([{"role": "user", "content": "hello"}])

        assert result == "Hello from Kimi!"


class TestErrorHandling:
    """Returns None on various failure modes."""

    def test_http_error_returns_none(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "test-key-123")

        import urllib.error
        error = urllib.error.HTTPError(
            url="https://api.moonshot.ai/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=MagicMock(read=lambda: b"rate limited"),
        )

        with patch("forja.utils.urllib.request.urlopen", side_effect=error):
            result = call_kimi([{"role": "user", "content": "hello"}])

        assert result is None

    def test_timeout_returns_none(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "test-key-123")

        with patch("forja.utils.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            result = call_kimi([{"role": "user", "content": "hello"}])

        assert result is None

    def test_invalid_json_response_returns_none(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "test-key-123")

        mock_response = MagicMock()
        mock_response.read.return_value = b"not json"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("forja.utils.urllib.request.urlopen", return_value=mock_response):
            result = call_kimi([{"role": "user", "content": "hello"}])

        assert result is None

    def test_missing_choices_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "test-key-123")

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"data": "no choices"}).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("forja.utils.urllib.request.urlopen", return_value=mock_response):
            result = call_kimi([{"role": "user", "content": "hello"}])

        assert result is None


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

        with patch("forja.utils.urllib.request.urlopen", side_effect=mock_urlopen):
            call_kimi(
                [{"role": "user", "content": "test"}],
                temperature=0.5,
                max_tokens=1000,
            )

        assert captured_request["method"] == "POST"
        assert "moonshot.ai" in captured_request["url"]
        assert captured_request["data"]["temperature"] == 0.5
        assert captured_request["data"]["max_tokens"] == 1000
        assert captured_request["data"]["messages"] == [{"role": "user", "content": "test"}]
        assert "Bearer my-secret-key" in captured_request["headers"].get("Authorization", "")
