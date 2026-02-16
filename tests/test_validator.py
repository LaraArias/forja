"""Tests for the bracket-matching validator in forja_validator.py.

Note: check_balanced_brackets takes a Path object and reads the file itself.
Tests must write temp files.
"""

import sys
import pytest
from pathlib import Path

# The validator template lives at src/forja/templates/forja_validator.py.
# We import from there since it's not a package — add its dir to sys.path.
_templates_dir = str(Path(__file__).resolve().parent.parent / "src" / "forja" / "templates")
if _templates_dir not in sys.path:
    sys.path.insert(0, _templates_dir)

from forja_validator import check_balanced_brackets


def _write_js(tmp_path, content):
    """Write JS content to a temp file and return the Path."""
    f = tmp_path / "test.js"
    f.write_text(content, encoding="utf-8")
    return f


class TestBracketBalance:
    """Tests for the JS/TS bracket matcher."""

    def test_simple_function(self, tmp_path):
        f = _write_js(tmp_path, "function f() { return 1; }")
        assert check_balanced_brackets(f) is True

    def test_string_with_braces(self, tmp_path):
        f = _write_js(tmp_path, 'let s = "{ not a brace }";')
        assert check_balanced_brackets(f) is True

    def test_template_literal(self, tmp_path):
        f = _write_js(tmp_path, "let s = `${x} text`;")
        assert check_balanced_brackets(f) is True

    def test_single_line_comment(self, tmp_path):
        f = _write_js(tmp_path, "// { not counted\nfunction f() {}")
        assert check_balanced_brackets(f) is True

    def test_block_comment(self, tmp_path):
        f = _write_js(tmp_path, "/* { */ function f() {}")
        assert check_balanced_brackets(f) is True

    def test_unbalanced_open(self, tmp_path):
        f = _write_js(tmp_path, "function f() {")
        assert check_balanced_brackets(f) is False

    def test_unbalanced_close(self, tmp_path):
        f = _write_js(tmp_path, "function f() }}")
        assert check_balanced_brackets(f) is False

    def test_nested(self, tmp_path):
        f = _write_js(tmp_path, "if (x) { if (y) { z(); } }")
        assert check_balanced_brackets(f) is True

    def test_empty_file(self, tmp_path):
        f = _write_js(tmp_path, "")
        assert check_balanced_brackets(f) is True

    def test_single_quoted_string_with_braces(self, tmp_path):
        f = _write_js(tmp_path, "let s = '{ brace }';")
        assert check_balanced_brackets(f) is True

    def test_multiline_function(self, tmp_path):
        code = (
            "function greet(name) {\n"
            "  if (name) {\n"
            '    console.log("Hello, " + name);\n'
            "  } else {\n"
            '    console.log("Hello!");\n'
            "  }\n"
            "}\n"
        )
        f = _write_js(tmp_path, code)
        assert check_balanced_brackets(f) is True

    def test_escaped_quote_in_string(self, tmp_path):
        """Escaped quotes inside strings should not break parsing."""
        f = _write_js(tmp_path, r'let s = "a\"b"; function f() {}')
        assert check_balanced_brackets(f) is True

    def test_mixed_brackets(self, tmp_path):
        f = _write_js(tmp_path, "let a = [1, {x: (2 + 3)}];")
        assert check_balanced_brackets(f) is True

    def test_mismatched_brackets(self, tmp_path):
        f = _write_js(tmp_path, "let a = [1, {x: 2]);")
        assert check_balanced_brackets(f) is False
