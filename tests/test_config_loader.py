"""Tests for forja.config_loader module."""

import json
import os
import pytest
from pathlib import Path

from forja.config_loader import (
    ForjaConfig, BuildConfig, ModelsConfig, ContextConfig, ObservatoryConfig,
    _parse_toml, _parse_value, load_config, reset_config,
)


class TestParseValue:
    """Unit tests for the _parse_value helper."""

    def test_positive_integer(self):
        assert _parse_value("42") == 42

    def test_negative_integer(self):
        assert _parse_value("-5") == -5

    def test_zero(self):
        assert _parse_value("0") == 0

    def test_double_quoted_string(self):
        assert _parse_value('"hello world"') == "hello world"

    def test_single_quoted_string(self):
        assert _parse_value("'hello world'") == "hello world"

    def test_hash_inside_quoted_string(self):
        assert _parse_value('"color: #FF0000"') == "color: #FF0000"

    def test_inline_comment_stripped(self):
        assert _parse_value("42 # this is a comment") == 42

    def test_hash_in_unquoted_value(self):
        # Unquoted value with # gets comment stripped
        assert _parse_value("foo # bar") == "foo"

    def test_boolean_true(self):
        assert _parse_value("true") is True

    def test_boolean_false(self):
        assert _parse_value("false") is False

    def test_boolean_case_insensitive(self):
        assert _parse_value("True") is True
        assert _parse_value("FALSE") is False

    def test_bare_string(self):
        assert _parse_value("some-model-name") == "some-model-name"

    def test_whitespace_stripped(self):
        assert _parse_value("  42  ") == 42


class TestParseToml:
    """Tests for the TOML parser."""

    def test_basic_section_and_values(self, tmp_path):
        toml = tmp_path / "test.toml"
        toml.write_text(
            "[build]\n"
            "timeout_stall_minutes = 15\n"
            "timeout_absolute_minutes = 25\n"
            "max_cycles_per_feature = 3\n",
            encoding="utf-8",
        )
        result = _parse_toml(toml)
        assert result["build"]["timeout_stall_minutes"] == 15
        assert result["build"]["timeout_absolute_minutes"] == 25
        assert result["build"]["max_cycles_per_feature"] == 3

    def test_negative_number(self, tmp_path):
        """Bryan's bug: negative numbers must parse correctly."""
        toml = tmp_path / "test.toml"
        toml.write_text(
            "[build]\noffset = -5\n",
            encoding="utf-8",
        )
        result = _parse_toml(toml)
        assert result["build"]["offset"] == -5

    def test_hash_in_quoted_string(self, tmp_path):
        toml = tmp_path / "test.toml"
        toml.write_text(
            '[models]\npattern = "color: #FF0000"\n',
            encoding="utf-8",
        )
        result = _parse_toml(toml)
        assert result["models"]["pattern"] == "color: #FF0000"

    def test_boolean_values(self, tmp_path):
        toml = tmp_path / "test.toml"
        toml.write_text(
            "[build]\nenabled = true\ndisabled = false\n",
            encoding="utf-8",
        )
        result = _parse_toml(toml)
        assert result["build"]["enabled"] is True
        assert result["build"]["disabled"] is False

    def test_comments_and_blank_lines(self, tmp_path):
        toml = tmp_path / "test.toml"
        toml.write_text(
            "# This is a comment\n"
            "\n"
            "[build]\n"
            "# Another comment\n"
            "timeout = 10\n"
            "\n",
            encoding="utf-8",
        )
        result = _parse_toml(toml)
        assert result["build"]["timeout"] == 10

    def test_inline_comment(self, tmp_path):
        toml = tmp_path / "test.toml"
        toml.write_text(
            "[build]\ntimeout = 10 # minutes\n",
            encoding="utf-8",
        )
        result = _parse_toml(toml)
        assert result["build"]["timeout"] == 10


class TestDefaults:
    """Test load_config with no toml file returns sensible defaults."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_defaults_without_toml_file(self, tmp_path):
        config = load_config(project_root=tmp_path)
        assert config.build.timeout_stall_minutes == 12
        assert config.build.timeout_absolute_minutes == 20
        assert config.build.max_cycles_per_feature == 5
        assert config.models.kimi_model == "kimi-k2-0711-preview"
        assert config.context.max_context_chars == 3000
        assert config.observatory.live_refresh_seconds == 5


class TestTomlOverride:
    """Test that TOML file values override defaults."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_toml_overrides_defaults(self, tmp_path):
        toml = tmp_path / "forja.toml"
        toml.write_text(
            "[build]\ntimeout_stall_minutes = 99\n",
            encoding="utf-8",
        )
        config = load_config(project_root=tmp_path)
        assert config.build.timeout_stall_minutes == 99
        # Other defaults remain
        assert config.build.timeout_absolute_minutes == 20


class TestEnvOverride:
    """Test that environment variables override TOML and defaults."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()
        # Clean up env vars
        for key in list(os.environ):
            if key.startswith("FORJA_"):
                del os.environ[key]

    def test_env_overrides_default(self, tmp_path):
        os.environ["FORJA_BUILD_TIMEOUT_STALL_MINUTES"] = "77"
        config = load_config(project_root=tmp_path)
        assert config.build.timeout_stall_minutes == 77

    def test_env_overrides_toml(self, tmp_path):
        toml = tmp_path / "forja.toml"
        toml.write_text(
            "[build]\ntimeout_stall_minutes = 99\n",
            encoding="utf-8",
        )
        os.environ["FORJA_BUILD_TIMEOUT_STALL_MINUTES"] = "33"
        config = load_config(project_root=tmp_path)
        assert config.build.timeout_stall_minutes == 33


class TestCacheAndReset:
    """Test caching and reset behavior."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_cache_returns_same_object(self, tmp_path):
        a = load_config(project_root=tmp_path)
        b = load_config(project_root=tmp_path)
        assert a is b

    def test_reset_clears_cache(self, tmp_path):
        a = load_config(project_root=tmp_path)
        reset_config()
        b = load_config(project_root=tmp_path)
        assert a is not b
        # But values should be equal
        assert a == b
