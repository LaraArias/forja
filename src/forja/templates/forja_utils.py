#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja shared utilities for template tools.

This file is copied to .forja-tools/forja_utils.py during forja init.
Template scripts import from here instead of duplicating code.
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
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

# ── LLM constants ───────────────────────────────────────────────────

KIMI_MODEL = "kimi-k2-0711-preview"
KIMI_API_URL = "https://api.moonshot.ai/v1/chat/completions"

# ── .env loading ────────────────────────────────────────────────────

_loaded_paths = set()


def load_dotenv(paths=None):
    """Load environment variables from .env files.

    Reads key=value pairs, strips quotes, sets os.environ (no overwrite).
    Guards against double-loading the same path.
    """
    if paths is None:
        paths = [".env", str(Path.home() / ".forja" / "config.env")]

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


def call_kimi(messages, temperature=0.6, max_tokens=4096, timeout=60):
    """Call Kimi (Moonshot AI) chat completion API.

    Returns response text content, or None on any failure.
    """
    load_dotenv()
    api_key = os.environ.get("KIMI_API_KEY", "")
    if not api_key:
        return None

    payload = json.dumps({
        "model": KIMI_MODEL,
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
            print(f"  reading Kimi error body: {exc}", file=sys.stderr)
        print(f"  {RED}Kimi: HTTP {e.code} {e.reason}{RESET}", file=sys.stderr)
        if error_body:
            print(f"  {DIM}{error_body[:200]}{RESET}", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  {RED}Kimi timeout/network: {e}{RESET}", file=sys.stderr)
        return None
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        print(f"  {RED}Kimi: unexpected response format{RESET}", file=sys.stderr)
        return None


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

    # Step 3: strip markdown code blocks
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != end:
            inner = text[start:end]
            first_nl = inner.find("\n")
            stripped = inner[first_nl + 1:] if first_nl != -1 else inner[3:]
            bs2 = stripped.find("{")
            be2 = stripped.rfind("}")
            if bs2 != -1 and be2 > bs2:
                try:
                    result = json.loads(stripped[bs2:be2 + 1])
                    if isinstance(result, dict):
                        return result
                except (json.JSONDecodeError, ValueError):
                    pass

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
