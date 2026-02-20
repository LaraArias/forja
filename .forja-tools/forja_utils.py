#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja shared utilities for template tools.

This file is copied to .forja-tools/forja_utils.py during forja init.
Template scripts import from here instead of duplicating code.
"""

import json
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# ── Version ─────────────────────────────────────────────────────────

VERSION = "0.1.0"

# ── ANSI colors ─────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

PASS_ICON = f"{GREEN}[PASS]{RESET}"
FAIL_ICON = f"{RED}[FAIL]{RESET}"
WARN_ICON = f"{YELLOW}[WARN]{RESET}"

# ── LLM constants (overridden by FORJA_MODELS_* env vars) ──────────

_DEFAULT_KIMI_MODEL = "kimi-k2-0711-preview"
_DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-6"
_DEFAULT_OPENAI_MODEL = "gpt-4o"
KIMI_API_URL = "https://api.moonshot.ai/v1/chat/completions"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


def _get_model(provider):
    """Return the model name for a provider, respecting env var overrides."""
    if provider == "kimi":
        return os.environ.get("FORJA_MODELS_KIMI_MODEL", _DEFAULT_KIMI_MODEL)
    if provider == "anthropic":
        return os.environ.get("FORJA_MODELS_ANTHROPIC_MODEL", _DEFAULT_ANTHROPIC_MODEL)
    if provider == "openai":
        return os.environ.get("FORJA_MODELS_OPENAI_MODEL", os.environ.get("OPENAI_MODEL", _DEFAULT_OPENAI_MODEL))
    return ""


# Backward-compatible aliases
KIMI_MODEL = _DEFAULT_KIMI_MODEL
ANTHROPIC_MODEL = _DEFAULT_ANTHROPIC_MODEL

# ── .env loading ────────────────────────────────────────────────────

_loaded_paths = set()


def load_dotenv(paths=None):
    """Load environment variables from .env files.

    Always loads ~/.forja/config.env first (global config written by
    forja config), then processes paths.
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

    loaded = {}

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
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and value:
                loaded[key] = value
                if key not in os.environ:
                    os.environ[key] = value

    return loaded


# ── LLM client ──────────────────────────────────────────────────────

_SECRET_PATTERNS = re.compile(
    r"bearer|authorization|api[-_]?key|x-api-key|secret",
    re.IGNORECASE,
)


def _sanitize_error_body(body):
    """Truncate API error body and strip lines that may contain secrets."""
    safe_lines = []
    for line in body[:500].splitlines():
        if _SECRET_PATTERNS.search(line):
            continue
        safe_lines.append(line)
    return "\n".join(safe_lines)[:100]


def _call_kimi_raw(prompt, system, model):
    """Call Kimi API. Raises on failure for auto-fallback."""
    load_dotenv()
    api_key = os.environ.get("KIMI_API_KEY", "")
    if not api_key:
        raise RuntimeError("KIMI_API_KEY not set")

    messages = []
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
            print(f"  reading Kimi error body: {exc}", file=sys.stderr)
        raise RuntimeError(f"Kimi: HTTP {e.code} {e.reason} {_sanitize_error_body(error_body)}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Kimi timeout/network: {e}") from e
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError("Kimi: unexpected response format") from e


def _call_anthropic_raw(prompt, system, model, tools=None):
    """Call Anthropic API. Raises on failure for auto-fallback."""
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    body_dict = {
        "model": model,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body_dict["system"] = system
    if tools:
        body_dict["tools"] = tools

    payload = json.dumps(body_dict).encode("utf-8")

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
            print(f"  reading Anthropic error body: {exc}", file=sys.stderr)
        raise RuntimeError(f"Anthropic: HTTP {e.code} {e.reason} {_sanitize_error_body(error_body)}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Anthropic timeout/network: {e}") from e
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError("Anthropic: unexpected response format") from e


def _call_openai_raw(prompt, system, model):
    """Call OpenAI API. Raises on failure for auto-fallback."""
    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    messages = []
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
            print(f"  reading OpenAI error body: {exc}", file=sys.stderr)
        raise RuntimeError(f"OpenAI: HTTP {e.code} {e.reason} {_sanitize_error_body(error_body)}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"OpenAI timeout/network: {e}") from e
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError("OpenAI: unexpected response format") from e


def _call_provider(prompt, system, provider, model, tools=None):
    """Dispatch to the appropriate provider's raw function."""
    if provider == "kimi":
        return _call_kimi_raw(prompt, system, model or _get_model("kimi"))
    elif provider == "anthropic":
        return _call_anthropic_raw(prompt, system, model or _get_model("anthropic"), tools=tools)
    elif provider == "openai":
        return _call_openai_raw(prompt, system, model or _get_model("openai"))
    raise ValueError(f"Unknown provider: {provider}")


def call_llm(prompt, system="", provider="auto", model=None, max_retries=2):
    """Call an LLM provider with retry and exponential backoff.

    provider can be 'kimi', 'anthropic', 'openai', or 'auto'.
    Auto tries kimi first, falls back to anthropic, then openai.
    Each provider is retried up to max_retries times with exponential backoff.
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
                    delay = min(2 ** attempt, 8)
                    time.sleep(delay)
                    continue
                break

    if provider != "auto" and last_error is not None:
        raise last_error
    return ""


# Backward-compatible wrappers

def call_kimi(prompt, system=""):
    """Call Kimi provider. Wrapper around call_llm."""
    return call_llm(prompt, system, provider="kimi")


def call_anthropic(prompt, system=""):
    """Call Anthropic provider. Wrapper around call_llm."""
    return call_llm(prompt, system, provider="anthropic")


def _call_claude_code(prompt, system="", timeout=120):
    """Call Claude Code CLI and return text response. Fallback to call_llm()."""
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


def call_provider(provider, messages, timeout=30):
    """Call a generic OpenAI-compatible chat completion provider.

    provider is a dict with: url, model, env_key, temperature, max_tokens.
    Returns the full parsed JSON response body, or None on failure.
    """
    load_dotenv()
    api_key = os.environ.get(provider["env_key"], "")
    if not api_key:
        return None

    payload = json.dumps({
        "model": provider["model"],
        "messages": messages,
        "temperature": provider.get("temperature", 0.2),
        "max_tokens": provider.get("max_tokens", 1024),
    }).encode("utf-8")

    req = urllib.request.Request(
        provider["url"],
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
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception as exc:
            print(f"  reading provider error body: {exc}", file=sys.stderr)
        print(f"  {WARN_ICON} {provider.get('name', '?')}: HTTP {e.code} {e.reason}")
        if error_body:
            print(f"       {error_body[:200]}")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  {WARN_ICON} {provider.get('name', '?')}: {e}")
        return None
    except json.JSONDecodeError:
        print(f"  {WARN_ICON} {provider.get('name', '?')}: response is not valid JSON")
        return None


# ── JSON parsing ────────────────────────────────────────────────────


def parse_json(text):
    """Parse JSON dict from LLM output with 4-step fallback.

    Returns parsed dict or None. Never returns synthetic dicts.
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


def parse_json_array(text):
    """Parse JSON array from LLM output. Handles markdown wrapping.

    Returns list or None.
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # Strip markdown code blocks
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != end:
            inner = text[start:end]
            first_nl = inner.find("\n")
            text = inner[first_nl + 1:] if first_nl != -1 else inner[3:]

    # Find array boundaries
    bracket_start = text.find("[")
    bracket_end = text.rfind("]")
    if bracket_start == -1 or bracket_end == -1:
        return None

    try:
        result = json.loads(text[bracket_start:bracket_end + 1])
        if isinstance(result, list):
            return result
        return None
    except json.JSONDecodeError:
        return None


def extract_content(response):
    """Extract message content from chat completion response."""
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


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
    created_at: str = None
    passed_at: str = None
    blocked_at: str = None
    evidence: str = None  # Why the feature passed (probe results, test output, etc.)
    name: str = None  # Legacy fallback for description
    _teammate: str = field(default=None, repr=False)
    _extra: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d):
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

    def to_dict(self):
        """Serialize to a JSON-safe dict.

        Omits None values and internal fields (_teammate, _extra)
        to keep features.json clean.  Extra fields are re-merged so
        unknown keys survive round-trips.
        """
        d = {"id": self.id}
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
        d.update(self._extra)
        return d

    @property
    def is_terminal(self):
        """True if the feature is in a terminal state (blocked or passed)."""
        return self.status in ("blocked", "passed")

    @property
    def can_retry(self):
        """True if the feature can be attempted again."""
        return not self.is_terminal

    @property
    def display_name(self):
        """Return the best human-readable name for this feature."""
        return self.description or self.name or self.id


# ── Feature status ─────────────────────────────────────────────────


def read_feature_status(feat):
    """Canonical way to read feature status.

    Handles both the new string ``status`` field and the legacy boolean
    ``passes``/``blocked`` fields for backward compatibility.
    Also accepts a Feature instance directly.

    Returns one of: "pending", "passed", "failed", "blocked".
    """
    if isinstance(feat, Feature):
        return feat.status
    return Feature.from_dict(feat).status
