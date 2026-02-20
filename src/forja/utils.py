"""Forja shared utilities - colors, constants, env loading, LLM clients, JSON parsing."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

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

KIMI_API_URL = "https://api.moonshot.ai/v1/chat/completions"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

# ── .env loading ────────────────────────────────────────────────────

_loaded_paths: set[str] = set()


def load_dotenv(paths: list[str] | None = None) -> dict[str, str]:
    """Load environment variables from .env files.

    Reads key=value pairs from each file, strips surrounding quotes,
    and sets them in os.environ (without overwriting existing values).
    Guards against double-loading the same file path.

    Always loads ``~/.forja/config.env`` first (the global config written
    by ``forja config``), then processes *paths*.

    Args:
        paths: List of file paths to load. Defaults to ``[".env"]``.

    Returns:
        Dict of all key=value pairs that were loaded.
    """
    # Load global config first
    global_config = Path.home() / ".forja" / "config.env"
    if global_config.exists() and str(global_config) not in _loaded_paths:
        _loaded_paths.add(str(global_config))
        for line in global_config.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and value:
                    os.environ.setdefault(key, value)

    if paths is None:
        paths = [".env"]

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


def _call_kimi_raw(
    prompt: str,
    system: str,
    model: str,
) -> str:
    """Call Kimi (Moonshot AI) chat completion API.

    Raises on failure so ``call_llm`` auto-fallback can try the next provider.
    """
    load_dotenv()
    api_key = os.environ.get("KIMI_API_KEY", "")
    if not api_key:
        raise RuntimeError("KIMI_API_KEY not set")

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.6,
        "max_tokens": 4096,
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
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.debug("reading Kimi error body: %s", exc)
        raise RuntimeError(f"Kimi: HTTP {e.code} {e.reason} {_sanitize_error_body(error_body)}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Kimi timeout/network: {e}") from e
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError("Kimi: unexpected response format") from e


def _call_anthropic_raw(
    prompt: str,
    system: str,
    model: str,
    tools: list[dict] | None = None,
) -> str:
    """Call Anthropic (Claude) messages API.

    Raises on failure so ``call_llm`` auto-fallback can try the next provider.
    Accepts optional *tools* for advanced use (e.g. web_search).
    """
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    body: dict = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
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
        with urllib.request.urlopen(req, timeout=90, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text_parts = [
                block["text"]
                for block in data.get("content", [])
                if block.get("type") == "text"
            ]
            if not text_parts:
                raise RuntimeError("Empty response from Anthropic")
            return "\n".join(text_parts)
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.debug("reading Claude error body: %s", exc)
        raise RuntimeError(f"Claude: HTTP {e.code} {e.reason} {_sanitize_error_body(error_body)}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Claude timeout/network: {e}") from e
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError("Claude: unexpected response format") from e


def _call_openai_raw(
    prompt: str,
    system: str,
    model: str,
) -> str:
    """Call OpenAI chat completion API.

    Raises on failure so ``call_llm`` auto-fallback can try the next provider.
    """
    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 4096,
    }).encode("utf-8")

    req = urllib.request.Request(
        OPENAI_API_URL,
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
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.debug("reading OpenAI error body: %s", exc)
        raise RuntimeError(f"OpenAI: HTTP {e.code} {e.reason} {_sanitize_error_body(error_body)}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"OpenAI timeout/network: {e}") from e
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError("OpenAI: unexpected response format") from e


def _call_provider(
    prompt: str,
    system: str,
    provider: str,
    model: str | None,
) -> str:
    """Dispatch to the appropriate provider's raw function."""
    from forja.config_loader import load_config
    cfg = load_config()
    if provider == "kimi":
        return _call_kimi_raw(prompt, system, model or cfg.models.kimi_model)
    elif provider == "anthropic":
        return _call_anthropic_raw(prompt, system, model or cfg.models.anthropic_model)
    elif provider == "openai":
        return _call_openai_raw(prompt, system, model or cfg.models.openai_model)
    raise ValueError(f"Unknown provider: {provider}")


def call_llm(
    prompt: str,
    system: str = "",
    provider: str = "auto",
    model: str | None = None,
    max_retries: int = 2,
) -> str:
    """Call an LLM provider with retry and exponential backoff.

    *provider* can be ``'kimi'``, ``'anthropic'``, ``'openai'``, or ``'auto'``.
    Auto tries kimi first, falls back to anthropic, then openai.
    Each provider is retried up to *max_retries* times with exponential backoff.
    """
    if provider == "auto":
        providers = ["kimi", "anthropic", "openai"]
    else:
        providers = [provider]

    last_error = None
    for p in providers:
        p = p.strip()
        for attempt in range(max_retries + 1):
            try:
                return _call_provider(prompt, system, p, model)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    import time
                    delay = min(2 ** attempt, 8)
                    time.sleep(delay)
                    continue
                break

    # Specific provider requested: re-raise so callers see the error
    if provider != "auto" and last_error is not None:
        raise last_error
    return ""


# Backward-compatible wrappers

def call_kimi(prompt: str, system: str = "") -> str:
    """Call Kimi provider. Wrapper around :func:`call_llm`."""
    return call_llm(prompt, system, provider="kimi")


def call_anthropic(prompt: str, system: str = "") -> str:
    """Call Anthropic provider. Wrapper around :func:`call_llm`."""
    return call_llm(prompt, system, provider="anthropic")


def _call_claude_code(prompt: str, system: str = "", timeout: int = 120) -> str:
    """Call Claude Code CLI (``claude -p``) and return text response.

    Falls back to :func:`call_llm` with ``provider="anthropic"`` when the
    ``claude`` binary is not found or the CLI invocation fails.

    Args:
        prompt:  The user prompt to send.
        system:  Optional system prompt (prepended to *prompt*).
        timeout: Seconds before the subprocess is terminated.

    Returns:
        The model's text response.
    """
    if shutil.which("claude") is None:
        return call_llm(prompt, system=system, provider="anthropic")

    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    try:
        proc = subprocess.Popen(
            ["claude", "--dangerously-skip-permissions", "-p", full_prompt,
             "--output-format", "text"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            if proc.returncode == 0 and stdout:
                return stdout.decode(errors="replace").strip()
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    pass
    except (FileNotFoundError, OSError):
        pass

    # Fallback to direct API
    return call_llm(prompt, system=system, provider="anthropic")


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
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug("Could not read context file %s: %s", fpath, exc)

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


# ── Feature dataclass ──────────────────────────────────────────────


@dataclass
class Feature:
    """Typed representation of a feature from features.json.

    Use ``Feature.from_dict(d)`` to deserialize from JSON dicts.
    Use ``feat.to_dict()`` to serialize back for JSON writing.
    """

    id: str
    description: str = ""
    status: str = "pending"  # pending | passed | failed | blocked
    cycles: int = 0
    created_at: Optional[str] = None
    passed_at: Optional[str] = None
    blocked_at: Optional[str] = None
    evidence: Optional[str] = None  # Why the feature passed (probe results, test output, etc.)
    name: Optional[str] = None  # Legacy fallback for description
    _teammate: Optional[str] = field(default=None, repr=False)
    _extra: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict) -> Feature:
        """Deserialize from a JSON dict, handling legacy boolean schema."""
        status = d.get("status")
        if status not in ("pending", "passed", "failed", "blocked"):
            if d.get("blocked"):
                status = "blocked"
            elif d.get("passes") or d.get("passed"):
                status = "passed"
            elif d.get("cycles", 0) > 0:
                status = "failed"
            else:
                status = "pending"

        known_keys = {
            "id", "description", "status", "cycles",
            "created_at", "passed_at", "blocked_at",
            "evidence", "name", "_teammate",
            # Legacy keys consumed above — not stored
            "blocked", "passes", "passed",
        }
        extra = {k: v for k, v in d.items() if k not in known_keys}

        return cls(
            id=d.get("id", ""),
            description=d.get("description", d.get("name", "")),
            status=status,
            cycles=d.get("cycles", 0),
            created_at=d.get("created_at"),
            passed_at=d.get("passed_at"),
            blocked_at=d.get("blocked_at"),
            evidence=d.get("evidence"),
            name=d.get("name"),
            _teammate=d.get("_teammate"),
            _extra=extra,
        )

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict.

        Omits None values and internal fields (``_teammate``, ``_extra``)
        to keep features.json clean.  Extra fields are re-merged so
        unknown keys survive round-trips.
        """
        d: dict = {"id": self.id}
        if self.description:
            d["description"] = self.description
        d["status"] = self.status
        d["cycles"] = self.cycles
        if self.created_at is not None:
            d["created_at"] = self.created_at
        if self.passed_at is not None:
            d["passed_at"] = self.passed_at
        if self.blocked_at is not None:
            d["blocked_at"] = self.blocked_at
        if self.evidence is not None:
            d["evidence"] = self.evidence
        # Preserve any unknown keys for forward compat
        d.update(self._extra)
        return d

    @property
    def is_terminal(self) -> bool:
        """True if the feature is in a terminal state (blocked or passed)."""
        return self.status in ("blocked", "passed")

    @property
    def can_retry(self) -> bool:
        """True if the feature can be attempted again."""
        return not self.is_terminal

    @property
    def display_name(self) -> str:
        """Return the best human-readable name for this feature."""
        return self.description or self.name or self.id


# ── Feature status ─────────────────────────────────────────────────


def read_feature_status(feat: dict | Feature) -> str:
    """Canonical way to read feature status.

    Handles both the new string ``status`` field and the legacy boolean
    ``passes``/``blocked`` fields for backward compatibility.
    Also accepts a :class:`Feature` instance directly.

    Returns one of: ``"pending"``, ``"passed"``, ``"failed"``, ``"blocked"``.
    """
    if isinstance(feat, Feature):
        return feat.status
    return Feature.from_dict(feat).status
