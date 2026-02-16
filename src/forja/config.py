#!/usr/bin/env python3
"""Forja config - manage global API keys in ~/.forja/config.env."""

from __future__ import annotations

from pathlib import Path

from forja.utils import PASS_ICON

CONFIG_DIR = Path.home() / ".forja"
CONFIG_FILE = CONFIG_DIR / "config.env"

API_KEYS = [
    ("ANTHROPIC_API_KEY", "Anthropic (Claude)"),
    ("KIMI_API_KEY", "Kimi (Moonshot AI)"),
    ("SAPTIVA_API_KEY", "Saptiva"),
]


def run_config() -> bool:
    """Prompt for API keys and save to ~/.forja/config.env."""
    print("Forja Config - API Keys\n")
    print("Keys are stored in ~/.forja/config.env\n")

    # Load existing values
    existing: dict[str, str] = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and value:
                existing[key] = value

    lines = ["# Forja API Keys"]

    for env_key, label in API_KEYS:
        current = existing.get(env_key, "")
        hint = f" (current: {current[:8]}...)" if current else ""
        try:
            val = input(f"  {label} API key{hint} (Enter to {'keep' if current else 'skip'}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            val = ""
        # Keep existing if user pressed Enter
        if not val and current:
            val = current
        lines.append(f"{env_key}={val}")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    CONFIG_FILE.chmod(0o600)

    print(f"\n{PASS_ICON} Configuration saved to ~/.forja/config.env")
    return True
