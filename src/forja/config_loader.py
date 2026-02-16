"""Forja configuration loader.

Reads forja.toml from the project root, falls back to built-in defaults,
and allows environment variable overrides.

Environment variables follow the pattern FORJA_<SECTION>_<KEY> in upper-case,
e.g. FORJA_BUILD_TIMEOUT_STALL_MINUTES=15.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("forja")

# ── Defaults (match forja.toml.default) ──────────────────────────────

_DEFAULTS = {
    "build": {
        "timeout_stall_minutes": 12,
        "timeout_absolute_minutes": 20,
        "max_cycles_per_feature": 5,
    },
    "models": {
        "kimi_model": "kimi-k2-0711-preview",
        "anthropic_model": "claude-sonnet-4-20250514",
        "openai_model": "gpt-4o",
        "validation_provider": "auto",
    },
    "context": {
        "max_context_chars": 3000,
        "max_learnings_chars": 2000,
    },
    "observatory": {
        "live_refresh_seconds": 5,
    },
}


# ── Frozen dataclass hierarchy ────────────────────────────────────────

@dataclass(frozen=True)
class BuildConfig:
    timeout_stall_minutes: int
    timeout_absolute_minutes: int
    max_cycles_per_feature: int


@dataclass(frozen=True)
class ModelsConfig:
    kimi_model: str
    anthropic_model: str
    openai_model: str
    validation_provider: str


@dataclass(frozen=True)
class ContextConfig:
    max_context_chars: int
    max_learnings_chars: int


@dataclass(frozen=True)
class ObservatoryConfig:
    live_refresh_seconds: int


@dataclass(frozen=True)
class ForjaConfig:
    build: BuildConfig
    models: ModelsConfig
    context: ContextConfig
    observatory: ObservatoryConfig


# ── TOML parser (minimal, stdlib-only for Python 3.9-3.10 compat) ────

def _parse_value(raw: str) -> str | int | bool:
    """Parse a TOML value with proper type detection.

    Handles quoted strings (preserving ``#`` inside), booleans,
    integers (including negative), and bare strings.
    """
    stripped = raw.strip()

    # Quoted string — return contents verbatim, no comment stripping
    if len(stripped) >= 2:
        if (stripped[0] == '"' and stripped[-1] == '"') or \
           (stripped[0] == "'" and stripped[-1] == "'"):
            return stripped[1:-1]

    # Strip inline comments for unquoted values
    if "#" in stripped:
        stripped = stripped[:stripped.index("#")].rstrip()

    # Booleans
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False

    # Integer (including negative)
    try:
        return int(stripped)
    except ValueError:
        logger.debug("Could not parse %r as integer, treating as string", stripped)

    return stripped


def _parse_toml(path: Path) -> dict:
    """Parse a simple TOML file into nested dict.

    Supports only the subset Forja uses: [section] headers and
    key = value lines (strings, integers, booleans).  Python 3.11+
    has tomllib but we need 3.9+ support.
    """
    result: dict = {}
    current_section: dict | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Section header
        if line.startswith("[") and line.endswith("]"):
            section_name = line[1:-1].strip()
            result.setdefault(section_name, {})
            current_section = result[section_name]
            continue

        # Key = value
        if "=" in line and current_section is not None:
            key, _, raw_value = line.partition("=")
            key = key.strip()
            current_section[key] = _parse_value(raw_value)

    return result


# ── Environment variable overrides ────────────────────────────────────

def _apply_env_overrides(merged: dict) -> None:
    """Apply FORJA_<SECTION>_<KEY> environment variables."""
    for section_name, section_dict in merged.items():
        for key in list(section_dict.keys()):
            env_name = f"FORJA_{section_name.upper()}_{key.upper()}"
            env_val = os.environ.get(env_name)
            if env_val is not None:
                # Coerce to same type as default
                default_val = section_dict[key]
                if isinstance(default_val, int):
                    try:
                        section_dict[key] = int(env_val)
                    except ValueError:
                        logger.warning("Cannot coerce env %s=%r to int, keeping default", env_name, env_val)
                else:
                    section_dict[key] = env_val


# ── Public API ────────────────────────────────────────────────────────

_cached_config: ForjaConfig | None = None


def load_config(project_root: Path | None = None) -> ForjaConfig:
    """Load Forja configuration.

    Priority (highest wins):
    1. Environment variables (FORJA_BUILD_TIMEOUT_STALL_MINUTES etc.)
    2. forja.toml in project root
    3. Built-in defaults
    """
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    # Start with defaults (deep copy)
    merged: dict = {}
    for section, values in _DEFAULTS.items():
        merged[section] = dict(values)

    # Read forja.toml if present
    root = project_root or Path.cwd()
    toml_path = root / "forja.toml"
    if toml_path.is_file():
        file_values = _parse_toml(toml_path)
        for section, values in file_values.items():
            if section in merged:
                merged[section].update(values)

    # Apply environment overrides
    _apply_env_overrides(merged)

    config = ForjaConfig(
        build=BuildConfig(**merged["build"]),
        models=ModelsConfig(**merged["models"]),
        context=ContextConfig(**merged["context"]),
        observatory=ObservatoryConfig(**merged["observatory"]),
    )

    _cached_config = config
    return config


def reset_config() -> None:
    """Clear cached config (useful for testing)."""
    global _cached_config
    _cached_config = None
