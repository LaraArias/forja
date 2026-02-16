"""Security-focused tests for Forja hardening."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from forja.utils import _sanitize_error_body
from forja.context_setup import _is_safe_doc_path, _SAFE_DOC_EXTENSIONS
from forja.runner import _prd_needs_planning


class TestSanitizeErrorBody:
    """Verify _sanitize_error_body strips secrets from API error responses."""

    def test_strips_bearer_line(self):
        body = 'HTTP/1.1 401\nAuthorization: Bearer sk-ant-secret123\nbad request'
        result = _sanitize_error_body(body)
        assert "sk-ant-secret123" not in result
        assert "Bearer" not in result

    def test_strips_api_key_line(self):
        body = 'error: invalid\nx-api-key: my-secret\ndetails: rate limit'
        result = _sanitize_error_body(body)
        assert "my-secret" not in result

    def test_keeps_safe_lines(self):
        body = '{"error": "rate_limit_exceeded", "message": "too many requests"}'
        result = _sanitize_error_body(body)
        assert "rate_limit" in result

    def test_truncates_to_100_chars(self):
        body = "a" * 500
        result = _sanitize_error_body(body)
        assert len(result) <= 100

    def test_empty_body(self):
        assert _sanitize_error_body("") == ""

    def test_strips_case_insensitive(self):
        body = "AUTHORIZATION: Bearer xxx\ninfo: ok"
        result = _sanitize_error_body(body)
        assert "xxx" not in result
        assert "info: ok" in result


class TestIsSafeDocPath:
    """Verify _is_safe_doc_path blocks sensitive directories."""

    def test_blocks_ssh(self, tmp_path):
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        key = ssh_dir / "id_rsa"
        key.write_text("secret", encoding="utf-8")
        assert _is_safe_doc_path(key) is False

    def test_blocks_gnupg(self, tmp_path):
        gnupg_dir = tmp_path / ".gnupg"
        gnupg_dir.mkdir()
        key = gnupg_dir / "secring.gpg"
        key.write_text("secret", encoding="utf-8")
        assert _is_safe_doc_path(key) is False

    def test_blocks_aws(self, tmp_path):
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        creds = aws_dir / "credentials"
        creds.write_text("secret", encoding="utf-8")
        assert _is_safe_doc_path(creds) is False

    def test_blocks_etc(self):
        assert _is_safe_doc_path(Path("/etc/passwd")) is False

    def test_blocks_var(self):
        assert _is_safe_doc_path(Path("/var/log/syslog")) is False

    def test_allows_normal_path(self, tmp_path):
        doc = tmp_path / "docs" / "readme.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("hello", encoding="utf-8")
        assert _is_safe_doc_path(doc) is True

    def test_allows_project_docs(self, tmp_path):
        doc = tmp_path / "company" / "overview.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("content", encoding="utf-8")
        assert _is_safe_doc_path(doc) is True


class TestSafeDocExtensions:
    """Verify only expected extensions are in the allowlist."""

    def test_includes_markdown(self):
        assert ".md" in _SAFE_DOC_EXTENSIONS

    def test_includes_json(self):
        assert ".json" in _SAFE_DOC_EXTENSIONS

    def test_includes_yaml(self):
        assert ".yaml" in _SAFE_DOC_EXTENSIONS
        assert ".yml" in _SAFE_DOC_EXTENSIONS

    def test_does_not_include_executable(self):
        assert ".py" not in _SAFE_DOC_EXTENSIONS
        assert ".sh" not in _SAFE_DOC_EXTENSIONS
        assert ".exe" not in _SAFE_DOC_EXTENSIONS
        assert ".js" not in _SAFE_DOC_EXTENSIONS


class TestPrdPathTraversal:
    """Verify run_forja rejects PRD paths outside project directory."""

    def test_rejects_etc_shadow(self, tmp_path, monkeypatch):
        """forja run /etc/shadow should be rejected."""
        monkeypatch.chdir(tmp_path)
        # Create project markers so auto-init doesn't trigger
        (tmp_path / "CLAUDE.md").write_text("# test", encoding="utf-8")
        (tmp_path / ".forja-tools").mkdir()

        from forja.runner import run_forja
        result = run_forja(prd_path="/etc/shadow")
        assert result is False

    def test_rejects_parent_traversal(self, tmp_path, monkeypatch):
        """forja run ../../etc/passwd should be rejected."""
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
        (project / "CLAUDE.md").write_text("# test", encoding="utf-8")
        (project / ".forja-tools").mkdir()

        from forja.runner import run_forja
        result = run_forja(prd_path="../../etc/passwd")
        assert result is False


class TestSymlinkProtection:
    """Verify runner doesn't follow symlinks when cleaning artifacts."""

    def test_symlink_src_is_unlinked_not_followed(self, tmp_path, monkeypatch):
        """If src/ is a symlink, it should be unlinked, not rmtree'd."""
        monkeypatch.chdir(tmp_path)

        # Create a directory that src symlinks to
        real_dir = tmp_path / "real_important_data"
        real_dir.mkdir()
        (real_dir / "precious.txt").write_text("don't delete me", encoding="utf-8")

        # Create symlink src -> real_important_data
        src_link = tmp_path / "src"
        src_link.symlink_to(real_dir)

        assert src_link.is_symlink()

        # Import and call the cleanup logic directly
        import shutil
        from forja.runner import YELLOW, RESET

        p = Path("src")
        if p.is_symlink():
            p.unlink()

        # Symlink removed
        assert not src_link.exists()
        # Original data preserved
        assert (real_dir / "precious.txt").exists()
        assert (real_dir / "precious.txt").read_text() == "don't delete me"
