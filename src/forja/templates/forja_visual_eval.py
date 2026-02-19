#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja Visual Evaluation - screenshot-based quality assessment.

Reads screenshots from .forja/screenshots/, sends them to a vision-capable
LLM alongside PRD requirements, and produces a visual quality report.

Usage:
    python3 .forja-tools/forja_visual_eval.py --prd context/prd.md [--output json|text]
"""

import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

from forja_utils import (
    load_dotenv,
    parse_json,
    ANTHROPIC_API_URL,
    OPENAI_API_URL,
    VERSION,
    PASS_ICON,
    FAIL_ICON,
    WARN_ICON,
    GREEN,
    RED,
    DIM,
    BOLD,
    RESET,
)


# ── Evaluation prompt ────────────────────────────────────────────────

VISUAL_EVAL_PROMPT = """\
You are a visual quality evaluator for web projects. You will receive:
1. Screenshots of a web page (desktop and mobile viewports)
2. The original PRD (what SHOULD be built)

Evaluate the screenshots against these dimensions:

LAYOUT (0-100):
- Are all expected sections present and in correct order?
- No overlapping elements or broken layouts?
- Proper spacing and alignment?

RESPONSIVE (0-100):
- Does the mobile view work without horizontal scrolling?
- Are elements properly stacked/reflowed for mobile?
- Text is readable at mobile size?

VISUAL_QUALITY (0-100):
- Text is readable with proper contrast?
- No broken images or missing assets?
- Professional appearance (not raw unstyled HTML)?

CONTENT_MATCH (0-100):
- Does the visible content match PRD requirements?
- Are required sections (hero, features, CTA, etc.) present?
- Does heading text match the PRD intent?

Return ONLY valid JSON, no markdown:
{
  "score": 0-100,
  "layout": {"score": 0-100, "issues": ["issue1", "issue2"]},
  "responsive": {"score": 0-100, "issues": ["issue1"]},
  "visual_quality": {"score": 0-100, "issues": []},
  "content_match": {"score": 0-100, "issues": ["missing hero section"]},
  "summary": "One-line overall visual assessment",
  "pass": true/false
}

The overall "score" is the average of the four dimension scores.
Set "pass" to true if score >= 70, false otherwise.\
"""

# ── Screenshot helpers ───────────────────────────────────────────────

MAX_SCREENSHOTS = 5
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024  # 5 MB


def _encode_screenshot(path: Path) -> tuple[str, str] | None:
    """Read a screenshot file and return (base64_data, media_type).

    Returns None if the file is too large.
    """
    size = path.stat().st_size
    if size > MAX_SCREENSHOT_BYTES:
        print(f"  {WARN_ICON} Skipping {path.name} ({size // 1024}KB > {MAX_SCREENSHOT_BYTES // 1024}KB)")
        return None

    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    suffix = path.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg"
    return b64, media_type


# ── Vision LLM callers ──────────────────────────────────────────────

def _call_vision_anthropic(content_blocks: list, system: str) -> str:
    """Call Anthropic Claude with multimodal content blocks."""
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    model = os.environ.get("FORJA_VISUAL_EVAL_MODEL",
                           os.environ.get("FORJA_MODELS_ANTHROPIC_MODEL", "claude-sonnet-4-20250514"))

    body = {
        "model": model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": content_blocks}],
    }
    if system:
        body["system"] = system

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

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        text_parts = [
            block["text"]
            for block in data.get("content", [])
            if block.get("type") == "text"
        ]
        if not text_parts:
            raise RuntimeError("Empty response from Anthropic vision")
        return "\n".join(text_parts)


def _call_vision_openai(content_blocks: list, system: str) -> str:
    """Call OpenAI GPT-4o with multimodal content blocks."""
    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    model = os.environ.get("FORJA_VISUAL_EVAL_MODEL",
                           os.environ.get("FORJA_MODELS_OPENAI_MODEL", "gpt-4o"))

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content_blocks})

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2048,
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

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]


def _call_vision_llm(screenshots: list[Path], prd_content: str) -> str | None:
    """Send screenshots + PRD to a vision-capable LLM.

    Tries Anthropic first, falls back to OpenAI. Returns raw LLM text.
    """
    system = "You are a visual quality evaluator. Be strict. Respond only with valid JSON."

    # Build content blocks for both providers
    anthropic_blocks: list[dict] = []
    openai_blocks: list[dict] = []

    for ss in screenshots[:MAX_SCREENSHOTS]:
        encoded = _encode_screenshot(ss)
        if encoded is None:
            continue
        b64, media_type = encoded

        anthropic_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        })
        openai_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{b64}"},
        })

    if not anthropic_blocks:
        print(f"  {WARN_ICON} No valid screenshots to evaluate")
        return None

    prompt_text = f"{VISUAL_EVAL_PROMPT}\n\n---\n\nPRD:\n{prd_content}"

    anthropic_blocks.append({"type": "text", "text": prompt_text})
    openai_blocks.append({"type": "text", "text": prompt_text})

    # Try Anthropic first (vision-capable)
    load_dotenv()
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _call_vision_anthropic(anthropic_blocks, system)
        except Exception as e:
            print(f"  {WARN_ICON} Anthropic vision failed: {e}", file=sys.stderr)

    # Fall back to OpenAI (vision-capable)
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return _call_vision_openai(openai_blocks, system)
        except Exception as e:
            print(f"  {WARN_ICON} OpenAI vision failed: {e}", file=sys.stderr)

    return None


# ── Output helpers ───────────────────────────────────────────────────

def _print_text(result: dict):
    """Print visual evaluation results in human-readable format."""
    score = result.get("score", 0)
    passed = result.get("pass", False)
    icon = PASS_ICON if passed else FAIL_ICON
    color = GREEN if passed else RED
    print(f"\n{icon} Visual Evaluation: {color}{score}/100{RESET}")

    summary = result.get("summary", "")
    if summary:
        print(f"  {DIM}{summary}{RESET}")

    for dim in ("layout", "responsive", "visual_quality", "content_match"):
        d = result.get(dim, {})
        s = d.get("score", "?")
        issues = d.get("issues", [])
        dim_ok = isinstance(s, (int, float)) and s >= 70
        mark = f"{GREEN}\u2714{RESET}" if dim_ok else f"{RED}\u2718{RESET}"
        dim_color = GREEN if dim_ok else RED
        print(f"    {mark} {dim.replace('_', ' ').title()}: {dim_color}{s}{RESET}")
        for issue in issues[:3]:
            print(f"      {DIM}- {issue}{RESET}")

    print()


def _save_report(result: dict) -> Path:
    """Save visual evaluation report to .forja/visual-eval.json."""
    out_path = Path(".forja") / "visual-eval.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


# ── Main command ─────────────────────────────────────────────────────

def cmd_visual_eval(prd_path: str, output_format: str = "text"):
    """Run visual evaluation."""
    # 1. Check for screenshots
    ss_dir = Path(".forja/screenshots")
    if not ss_dir.exists():
        print(f"  {DIM}skipped (no screenshots at .forja/screenshots/){RESET}")
        sys.exit(0)

    screenshots = sorted(ss_dir.glob("*.png")) + sorted(ss_dir.glob("*.jpg"))
    if not screenshots:
        print(f"  {DIM}skipped (no screenshot files found){RESET}")
        sys.exit(0)

    names = ", ".join(s.name for s in screenshots[:MAX_SCREENSHOTS])
    print(f"  Found {len(screenshots)} screenshot(s): {names}")

    # 2. Read PRD
    prd_file = Path(prd_path)
    if not prd_file.exists():
        print(f"{FAIL_ICON} PRD not found: {prd_path}")
        sys.exit(1)

    prd_content = prd_file.read_text(encoding="utf-8")
    if not prd_content.strip():
        print(f"{FAIL_ICON} PRD is empty: {prd_path}")
        sys.exit(1)

    # 3. Check for vision-capable API key
    load_dotenv()
    has_vision_key = any(
        os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
    )
    if not has_vision_key:
        print(
            f"  {WARN_ICON} Visual eval skipped: no vision-capable LLM key "
            f"(need ANTHROPIC_API_KEY or OPENAI_API_KEY)"
        )
        sys.exit(0)

    # 4. Call vision LLM
    print(f"  Calling vision LLM for visual evaluation...")
    raw = _call_vision_llm(screenshots, prd_content)

    if not raw:
        print(f"  {WARN_ICON} Visual eval skipped: vision LLM did not respond")
        sys.exit(0)

    # 5. Parse response
    result = parse_json(raw)
    if result is None:
        result = {
            "score": 0,
            "pass": False,
            "layout": {"score": 0, "issues": ["Could not parse LLM response"]},
            "responsive": {"score": 0, "issues": []},
            "visual_quality": {"score": 0, "issues": []},
            "content_match": {"score": 0, "issues": []},
            "summary": raw[:300],
        }

    # 6. Output
    if output_format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_text(result)

    # 7. Save report
    report_path = _save_report(result)
    print(f"  Report saved to {report_path}")

    # 8. Exit code: score >= 70 passes
    score = result.get("score", 0)
    sys.exit(0 if isinstance(score, (int, float)) and score >= 70 else 1)


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    prd_path = None
    output_format = "text"

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--prd" and i + 1 < len(sys.argv):
            prd_path = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--output" and i + 1 < len(sys.argv):
            output_format = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if not prd_path:
        print(
            "Usage: python3 .forja-tools/forja_visual_eval.py "
            "--prd context/prd.md [--output json|text]"
        )
        sys.exit(1)

    cmd_visual_eval(prd_path, output_format)


if __name__ == "__main__":
    main()
