"""Tests for forja.utils.parse_json."""

import pytest
from forja.utils import parse_json


class TestDirectParse:
    """Step 1: Direct json.loads."""

    def test_valid_json_dict(self):
        assert parse_json('{"key": "value"}') == {"key": "value"}

    def test_valid_json_nested(self):
        result = parse_json('{"a": {"b": 1}, "c": [1, 2]}')
        assert result == {"a": {"b": 1}, "c": [1, 2]}

    def test_returns_none_for_array(self):
        assert parse_json('[1, 2, 3]') is None

    def test_returns_none_for_string(self):
        assert parse_json('"just a string"') is None

    def test_returns_none_for_number(self):
        assert parse_json("42") is None


class TestBraceExtraction:
    """Step 2: Extract from first { to last }."""

    def test_json_with_preamble(self):
        text = 'Here is the JSON:\n{"key": "value"}'
        assert parse_json(text) == {"key": "value"}

    def test_json_with_suffix(self):
        text = '{"key": "value"}\nThat was the response.'
        assert parse_json(text) == {"key": "value"}

    def test_json_with_both(self):
        text = 'Response:\n{"key": "value"}\nEnd.'
        assert parse_json(text) == {"key": "value"}


class TestMarkdownCodeBlocks:
    """Step 3: Extract from LAST code-fenced block."""

    def test_json_in_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        assert parse_json(text) == {"key": "value"}

    def test_json_in_plain_code_block(self):
        text = '```\n{"key": "value"}\n```'
        assert parse_json(text) == {"key": "value"}

    def test_json_in_code_block_with_surrounding_text(self):
        text = 'Here is the result:\n```json\n{"key": "value"}\n```\nDone.'
        assert parse_json(text) == {"key": "value"}

    def test_multiple_code_blocks_uses_last(self):
        """LLMs put the real answer in the last code block."""
        text = (
            'Here is a draft:\n'
            '```json\n{"draft": true}\n```\n'
            'Actually, here is the final answer:\n'
            '```json\n{"final": true}\n```\n'
        )
        assert parse_json(text) == {"final": True}

    def test_multiple_code_blocks_skips_invalid_last(self):
        """If the last block is invalid, fall back to earlier blocks."""
        text = (
            '```json\n{"valid": true}\n```\n'
            'Some text\n'
            '```\nnot valid json\n```\n'
        )
        assert parse_json(text) == {"valid": True}


class TestEdgeCases:
    """Step 4: Give up gracefully."""

    def test_none_input(self):
        assert parse_json(None) is None

    def test_empty_string(self):
        assert parse_json("") is None

    def test_whitespace_only(self):
        assert parse_json("   \n\t  ") is None

    def test_no_json_at_all(self):
        assert parse_json("This is just plain text.") is None

    def test_invalid_json(self):
        assert parse_json("{key: value}") is None

    def test_unclosed_brace(self):
        assert parse_json('{"key": "value"') is None

    def test_unicode_content(self):
        result = parse_json('{"emoji": "🎉", "text": "héllo"}')
        assert result == {"emoji": "🎉", "text": "héllo"}

    def test_nested_braces_in_strings(self):
        text = '{"code": "if (x) { return y; }"}'
        result = parse_json(text)
        assert result is not None
        assert result["code"] == "if (x) { return y; }"
