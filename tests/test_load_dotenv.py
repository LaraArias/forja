"""Tests for forja.utils.load_dotenv."""

import os
import pytest
from forja.utils import load_dotenv, _loaded_paths


@pytest.fixture(autouse=True)
def clean_loaded_paths():
    """Clear the loaded paths set before each test."""
    _loaded_paths.clear()
    yield
    _loaded_paths.clear()


class TestBasicLoading:
    """Core .env file loading behavior."""

    def test_loads_simple_key_value(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_KEY_1", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_KEY_1=hello\n", encoding="utf-8")

        result = load_dotenv([str(env_file)])
        assert result["TEST_KEY_1"] == "hello"
        assert os.environ["TEST_KEY_1"] == "hello"

        # Cleanup
        monkeypatch.delenv("TEST_KEY_1", raising=False)

    def test_strips_surrounding_double_quotes(self, tmp_path, monkeypatch):
        monkeypatch.delenv("QUOTED_KEY", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text('QUOTED_KEY="my value"\n', encoding="utf-8")

        result = load_dotenv([str(env_file)])
        assert result["QUOTED_KEY"] == "my value"

        monkeypatch.delenv("QUOTED_KEY", raising=False)

    def test_strips_surrounding_single_quotes(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SINGLE_Q", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("SINGLE_Q='my value'\n", encoding="utf-8")

        result = load_dotenv([str(env_file)])
        assert result["SINGLE_Q"] == "my value"

        monkeypatch.delenv("SINGLE_Q", raising=False)

    def test_skips_comments(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\nKEY=val\n", encoding="utf-8")
        monkeypatch.delenv("KEY", raising=False)

        result = load_dotenv([str(env_file)])
        assert "KEY" in result
        assert "#" not in str(result.keys())

        monkeypatch.delenv("KEY", raising=False)

    def test_skips_empty_lines(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("\n\nK=v\n\n", encoding="utf-8")
        monkeypatch.delenv("K", raising=False)

        result = load_dotenv([str(env_file)])
        assert result["K"] == "v"

        monkeypatch.delenv("K", raising=False)

    def test_skips_lines_without_equals(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("NOT_A_PAIR\nGOOD=value\n", encoding="utf-8")

        result = load_dotenv([str(env_file)])
        assert "NOT_A_PAIR" not in result
        assert "GOOD" in result


class TestNoOverwrite:
    """Does not overwrite existing environment variables."""

    def test_does_not_overwrite_existing_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EXISTING", "original")
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=from_file\n", encoding="utf-8")

        load_dotenv([str(env_file)])
        assert os.environ["EXISTING"] == "original"


class TestDoubleLoadGuard:
    """Guards against loading the same file twice."""

    def test_same_file_loaded_once(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DOUBLE_KEY", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("DOUBLE_KEY=first\n", encoding="utf-8")

        load_dotenv([str(env_file)])
        assert os.environ["DOUBLE_KEY"] == "first"

        # Modify file and load again - should be skipped
        env_file.write_text("DOUBLE_KEY=second\n", encoding="utf-8")
        monkeypatch.delenv("DOUBLE_KEY", raising=False)
        result = load_dotenv([str(env_file)])
        assert result == {}  # nothing new loaded

        monkeypatch.delenv("DOUBLE_KEY", raising=False)


class TestMissingFiles:
    """Handles missing files gracefully."""

    def test_nonexistent_file(self):
        result = load_dotenv(["/nonexistent/path/.env"])
        assert result == {}

    def test_mixed_existing_and_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FOUND_KEY", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FOUND_KEY=yes\n", encoding="utf-8")

        result = load_dotenv(["/no/such/file", str(env_file)])
        assert result["FOUND_KEY"] == "yes"

        monkeypatch.delenv("FOUND_KEY", raising=False)


class TestMultipleFiles:
    """Loads from multiple .env files."""

    def test_loads_from_two_files(self, tmp_path, monkeypatch):
        monkeypatch.delenv("A_KEY", raising=False)
        monkeypatch.delenv("B_KEY", raising=False)
        f1 = tmp_path / "first.env"
        f2 = tmp_path / "second.env"
        f1.write_text("A_KEY=from_first\n", encoding="utf-8")
        f2.write_text("B_KEY=from_second\n", encoding="utf-8")

        result = load_dotenv([str(f1), str(f2)])
        assert result["A_KEY"] == "from_first"
        assert result["B_KEY"] == "from_second"

        monkeypatch.delenv("A_KEY", raising=False)
        monkeypatch.delenv("B_KEY", raising=False)

    def test_skips_empty_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("EMPTY_KEY=\nGOOD=val\n", encoding="utf-8")

        result = load_dotenv([str(env_file)])
        assert "EMPTY_KEY" not in result
        assert "GOOD" in result
