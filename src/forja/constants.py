"""Forja project-wide constants — paths, model names, markers."""

from __future__ import annotations

from pathlib import Path

# ── Project structure paths (relative to project root) ───────────
PRD_PATH = Path("context/prd.md")
CLAUDE_MD = Path("CLAUDE.md")
FORJA_TOOLS = Path(".forja-tools")
CONTEXT_DIR = Path("context")
STORE_DIR = Path("context/store")
LEARNINGS_DIR = Path("context/learnings")
TEAMMATES_DIR = Path("context/teammates")
FORJA_DIR = Path(".forja")

# ── LLM model identifiers (defaults, overridden by config_loader) ──
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"

# ── Project markers (used to detect an existing Forja project) ──
PROJECT_MARKERS = [CLAUDE_MD, FORJA_TOOLS]

# ── Build prompt (the instruction sent to Claude Code) ──────────
BUILD_PROMPT = "Read CLAUDE.md and execute Forja with the PRD in context/prd.md"
