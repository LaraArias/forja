"""Forja shared utilities - colors, constants, env loading, LLM clients, JSON parsing."""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ── Structured logging ───────────────────────────────────────────────

logger = logging.getLogger("forja")


def setup_logging(verbose: bool = False) -> None:
    """Configure the ``forja`` logger with a stderr handler.

    In interactive terminals the format is compact; in pipes / CI it
    includes severity and logger name for machine parsing.
    """
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    if sys.stderr.isatty():
        fmt = "%(asctime)s %(message)s"
    else:
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    logger.setLevel(level)
    if not logger.handlers:
        logger.addHandler(handler)

# ── Version ─────────────────────────────────────────────────────────

try:
    from importlib.metadata import version as _get_version
    VERSION = _get_version("forja")
except Exception:
    VERSION = "0.1.0"

# ── ANSI colors ─────────────────────────────────────────────────────


class Style:
    """Terminal color and icon constants.

    New code should prefer ``Style.GREEN`` over the bare ``GREEN`` alias.
    """
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    PASS = "✔"
    FAIL = "✘"
    WARN = "⚠"


# Backward-compatible aliases (existing imports keep working)
RESET = Style.RESET
BOLD = Style.BOLD
DIM = Style.DIM
RED = Style.RED
GREEN = Style.GREEN
YELLOW = Style.YELLOW
CYAN = Style.CYAN
BG_RED = Style.BG_RED
BG_GREEN = Style.BG_GREEN

PASS_ICON = f"{GREEN}{Style.PASS}{RESET}"
FAIL_ICON = f"{RED}{Style.FAIL}{RESET}"
WARN_ICON = f"{YELLOW}{Style.WARN}{RESET}"

# ── LLM constants ───────────────────────────────────────────────────

from forja.constants import ANTHROPIC_MODEL

KIMI_API_URL = "https://api.moonshot.ai/v1/chat/completions"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def _get_kimi_model() -> str:
    from forja.config_loader import load_config
    return load_config().models.kimi_model


def _get_anthropic_model() -> str:
    from forja.config_loader import load_config
    return load_config().models.anthropic_model

# ── .env loading ────────────────────────────────────────────────────

_loaded_paths: set[str] = set()


def load_dotenv(paths: list[str] | None = None) -> dict[str, str]:
    """Load environment variables from .env files.

    Reads key=value pairs from each file, strips surrounding quotes,
    and sets them in os.environ (without overwriting existing values).
    Guards against double-loading the same file path.

    Args:
        paths: List of file paths to load. Defaults to
               [".env", "~/.forja/config.env"].

    Returns:
        Dict of all key=value pairs that were loaded.
    """
    if paths is None:
        paths = [".env", str(Path.home() / ".forja" / "config.env")]

    loaded: dict[str, str] = {}

    for fp_str in paths:
        fp = Path(fp_str).expanduser()
        resolved = str(fp.resolve())
        if resolved in _loaded_paths:
            continue
        _loaded_paths.add(resolved)

        if not fp.exists():
            continue

        for raw_line in fp.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            # Strip surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and value:
                loaded[key] = value
                if key not in os.environ:
                    os.environ[key] = value

    return loaded


# ── LLM clients ─────────────────────────────────────────────────────

_SECRET_PATTERNS = re.compile(
    r"bearer|authorization|api[-_]?key|x-api-key|secret",
    re.IGNORECASE,
)


def _sanitize_error_body(body: str) -> str:
    """Truncate API error body and strip lines that may contain secrets."""
    safe_lines: list[str] = []
    for line in body[:500].splitlines():
        if _SECRET_PATTERNS.search(line):
            continue
        safe_lines.append(line)
    return "\n".join(safe_lines)[:100]


def call_kimi(
    messages: list[dict[str, str]],
    temperature: float = 0.6,
    max_tokens: int = 4096,
    timeout: int = 60,
) -> str | None:
    """Call Kimi (Moonshot AI) chat completion API.

    Args:
        messages: OpenAI-format message list [{"role": ..., "content": ...}].
        temperature: Sampling temperature.
        max_tokens: Maximum response tokens.
        timeout: Request timeout in seconds.

    Returns:
        Response text content, or None on any failure.
    """
    load_dotenv()
    api_key = os.environ.get("KIMI_API_KEY", "")
    if not api_key:
        return None

    payload = json.dumps({
        "model": _get_kimi_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        KIMI_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": f"Forja/{VERSION}",
        },
        method="POST",
    )

    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.debug("reading Kimi error body: %s", exc)
        print_error(f"Kimi: HTTP {e.code} {e.reason}")
        if error_body:
            print(f"  {DIM}{_sanitize_error_body(error_body)}{RESET}", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print_error(f"Kimi timeout/network: {e}")
        return None
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        print_error("Kimi: unexpected response format")
        return None


def call_anthropic(
    messages: list[dict[str, str]],
    system: str = "",
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    timeout: int = 90,
) -> str | None:
    """Call Anthropic (Claude) messages API.

    Args:
        messages: Message list [{"role": ..., "content": ...}].
        system: System prompt.
        tools: Tool definitions (e.g. web_search).
        temperature: Sampling temperature.
        max_tokens: Maximum response tokens.
        timeout: Request timeout in seconds.

    Returns:
        Concatenated text blocks from response, or None on failure.
    """
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    body: dict = {
        "model": _get_anthropic_model(),
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = tools

    payload = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": f"Forja/{VERSION}",
        },
        method="POST",
    )

    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content_blocks = data.get("content", [])
            text_parts = [
                block["text"]
                for block in content_blocks
                if block.get("type") == "text"
            ]
            return "\n".join(text_parts) if text_parts else None
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.debug("reading Claude error body: %s", exc)
        print_error(f"Claude: HTTP {e.code} {e.reason}")
        if error_body:
            print(f"  {DIM}{_sanitize_error_body(error_body)}{RESET}", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print_error(f"Claude timeout/network: {e}")
        return None
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        print_error("Claude: unexpected response format")
        return None


# ── JSON parsing ────────────────────────────────────────────────────


def parse_json(text: str) -> dict | None:
    """Parse JSON from LLM output with 4-step fallback.

    1. Direct json.loads
    2. Extract from first { to last }
    3. Strip markdown code fences, then extract
    4. Return None (caller decides what to do)

    Never returns synthetic success/failure dicts.

    Args:
        text: Raw LLM output that may contain JSON.

    Returns:
        Parsed dict, or None if parsing fails at all steps.
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # Step 1: direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        return None
    except (json.JSONDecodeError, ValueError):
        pass

    # Step 2: extract brace-delimited JSON
    bs = text.find("{")
    be = text.rfind("}")
    if bs != -1 and be > bs:
        try:
            result = json.loads(text[bs:be + 1])
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    # Step 3: extract from LAST code-fenced block (LLMs put the real answer last)
    if "```" in text:
        blocks = re.findall(r'```(?:\w+)?\s*\n(.*?)```', text, re.DOTALL)
        for block in reversed(blocks):
            bs2 = block.find("{")
            be2 = block.rfind("}")
            if bs2 != -1 and be2 > bs2:
                try:
                    result = json.loads(block[bs2:be2 + 1])
                    if isinstance(result, dict):
                        return result
                except (json.JSONDecodeError, ValueError):
                    continue

    # Step 4: give up
    return None


# ── JSON file reading ──────────────────────────────────────────────


def safe_read_json(path: Path, default: Any = None) -> Any:
    """Read and parse a JSON file, returning *default* on any failure.

    Replaces the repetitive try/json.loads(path.read_text())/except pattern
    that appeared across the codebase.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError):
        return default


# ── Shared context gathering ───────────────────────────────────────

_BIZ_SUBDIRS = ("company", "domains", "design-system")
_BIZ_EXTENSIONS = {".md", ".json", ".txt"}


def gather_context(context_dir: Path, max_chars: int = 3000) -> str:
    """Walk context/company, domains, design-system and return concatenated content.

    Priority: company first, domains second, design-system last.
    Skips README.md files. Truncates to *max_chars*.

    Returns:
        Formatted markdown string with ``###`` headers per file, or empty string.
    """
    parts: list[str] = []
    total_chars = 0

    for subdir in _BIZ_SUBDIRS:
        dir_path = context_dir / subdir
        if not dir_path.is_dir():
            continue
        for fpath in sorted(dir_path.rglob("*")):
            if not fpath.is_file():
                continue
            if fpath.name == "README.md":
                continue
            if fpath.suffix not in _BIZ_EXTENSIONS:
                continue
            try:
                text = fpath.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                rel = fpath.relative_to(context_dir)
                snippet = text[:500]
                entry = f"### {rel}\n{snippet}\n"
                if total_chars + len(entry) > max_chars:
                    remaining = len(list(dir_path.rglob("*"))) - len(parts)
                    if remaining > 0:
                        parts.append(f"... (truncated, {remaining} more files)")
                    break
                parts.append(entry)
                total_chars += len(entry)
            except (OSError, UnicodeDecodeError):
                pass

    return "\n".join(parts)


# ── Output helpers ──────────────────────────────────────────────────


def print_error(msg: str) -> None:
    """Print an error message to stderr in red."""
    print(f"  {RED}{msg}{RESET}", file=sys.stderr)


def print_warning(msg: str) -> None:
    """Print a warning message to stderr in yellow."""
    print(f"  {YELLOW}{msg}{RESET}", file=sys.stderr)


def print_success(msg: str) -> None:
    """Print a success message in green."""
    print(f"  {GREEN}{msg}{RESET}")


# ── Feature status ─────────────────────────────────────────────────


def read_feature_status(feat: dict) -> str:
    """Canonical way to read feature status.

    Handles both the new string ``status`` field and the legacy boolean
    ``passes``/``blocked`` fields for backward compatibility.

    Returns one of: ``"pending"``, ``"passed"``, ``"failed"``, ``"blocked"``.
    """
    status = feat.get("status")
    if status in ("pending", "passed", "failed", "blocked"):
        return status
    # Fallback: old boolean schema
    if feat.get("blocked"):
        return "blocked"
    if feat.get("passes") or feat.get("passed"):
        return "passed"
    if feat.get("cycles", 0) > 0:
        return "failed"
    return "pending"
