#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja Spec Review - independent PRD analysis via Kimi.

Reviews a PRD before engineering begins. Calls Kimi (external model) to
detect ambiguities, gaps, contradictions, missing edge cases, and
implicit assumptions.

Usage:
    python3 .forja-tools/forja_specreview.py --prd context/prd.md [--output json|text]
"""

import glob as glob_mod
import json
import os
import sys
from pathlib import Path

from forja_utils import (
    load_dotenv, call_kimi, parse_json,
    PASS_ICON, FAIL_ICON, WARN_ICON, RED, YELLOW, DIM, BOLD, RESET,
)

# Max approximate tokens for learnings context
LEARNINGS_MAX_CHARS = 2000  # ~500 tokens

REVIEW_PROMPT = """\
You are a senior product architect and specification reviewer.

Analyze the following PRD (Product Requirements Document) thoroughly.
Identify:

1. AMBIGUITIES - Statements that can be interpreted in multiple ways
2. GAPS - Missing requirements needed for implementation \
(error handling, validation rules, data limits, edge cases)
3. CONTRADICTIONS - Requirements that conflict with each other
4. MISSING EDGE CASES - Scenarios the PRD doesn't address \
(empty inputs, concurrent access, auth failures, data overflow, unicode)
5. ASSUMPTIONS - Implicit assumptions that should be made explicit

For each finding, assign a severity: "high" (blocks implementation), \
"medium" (causes ambiguity), "low" (nice to clarify).

Also provide "enrichment": a list of specific clarifications and defaults \
that would make the PRD implementation-ready. Write enrichment as concrete \
decisions (e.g., "Max title length: 255 chars", "Password minimum: 8 characters").

Return ONLY valid JSON, no markdown wrapping:
{
  "pass": true/false,
  "gaps": [
    {"severity": "high|medium|low", "description": "...", "suggestion": "..."}
  ],
  "assumptions": ["assumption 1", "assumption 2"],
  "enrichment": ["clarification 1", "clarification 2"],
  "summary": "One-line overall assessment"
}

IMPORTANT BUILD SYSTEM CONSTRAINT:
This PRD will be built by Claude Code (an AI coding agent running in a terminal). \
Claude Code CAN build: web apps (Python, Node.js, Go, Rust, Java), APIs, CLIs, \
static sites, scripts, data pipelines, and any code that runs in a standard \
dev environment. Claude Code CANNOT build: Unity/Unreal projects, native \
mobile apps (Swift/Kotlin requiring Xcode/Android Studio), GUI desktop apps \
(WPF, WinForms, Electron with native modules), GPU/CUDA ML training pipelines, \
or anything requiring a graphical IDE, device emulator, or specialized hardware.

If the PRD specifies a stack that Claude Code cannot build (e.g., Unity, Xcode, \
Android Studio, WPF), add a HIGH severity gap with: description explaining why \
the stack is incompatible, and suggestion with alternative stacks that achieve \
the same goal (e.g., "Use a web-based 3D framework like Three.js instead of Unity").

If the PRD uses languages whose source files cannot be validated without their \
compiler (C#, Java, Swift, Kotlin, Go, Rust, C++), add a MEDIUM severity gap \
noting that these files will pass structural validation only and recommending \
adding build/test commands to the PRD.

Set "pass" to false if there are ANY high-severity findings.
Set "pass" to true if all findings are medium or low severity.\
"""


# ── Context gathering ────────────────────────────────────────────────

def _read_context_store():
    """Read context/store/*.json for existing decisions and learnings."""
    items = []
    for fpath in sorted(glob_mod.glob("context/store/*.json")):
        try:
            data = json.loads(Path(fpath).read_text(encoding="utf-8"))
            key = data.get("key", Path(fpath).stem)
            value = data.get("value", "")
            if key and value:
                items.append(f"{key}: {value}")
        except (json.JSONDecodeError, OSError):
            pass
    return items


def _read_learnings():
    """Read context/learnings/*.jsonl. Caps output at ~500 tokens."""
    items = []
    total_chars = 0
    for fpath in sorted(glob_mod.glob("context/learnings/*.jsonl")):
        try:
            for line in Path(fpath).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    text = entry.get("learning", entry.get("text", entry.get("content", "")))
                    if text:
                        if total_chars + len(text) > LEARNINGS_MAX_CHARS:
                            return items
                        items.append(text)
                        total_chars += len(text)
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass
    return items


# ── Core ─────────────────────────────────────────────────────────────

def _build_messages(prd_content, context_items, learnings):
    """Build chat messages for PRD review."""
    user_msg = f"{REVIEW_PROMPT}\n\n---\n\nPRD:\n{prd_content}"

    if context_items:
        ctx_text = "\n".join(f"- {item}" for item in context_items[:20])
        user_msg += f"\n\n---\n\nExisting context/decisions:\n{ctx_text}"

    if learnings:
        learn_text = "\n".join(f"- {item}" for item in learnings)
        user_msg += f"\n\n---\n\nPrevious learnings:\n{learn_text}"

    return [
        {
            "role": "system",
            "content": (
                "You are a senior product architect. "
                "You review PRDs before engineering begins. "
                "Be specific and actionable. Respond only with valid JSON."
            ),
        },
        {"role": "user", "content": user_msg},
    ]


def _print_text(review):
    """Print review results in human-readable format."""
    passed = review.get("pass", True)
    gaps = review.get("gaps", [])
    assumptions = review.get("assumptions", [])
    enrichment = review.get("enrichment", [])
    summary = review.get("summary", "")

    # Header
    icon = PASS_ICON if passed else FAIL_ICON
    print(f"\n{icon} Spec Review: {'PASS' if passed else 'FAIL'}")

    if summary:
        print(f"  {DIM}{summary}{RESET}")

    # Gaps by severity
    if gaps:
        severity_order = {"high": 0, "medium": 1, "low": 2}
        sorted_gaps = sorted(
            gaps,
            key=lambda g: severity_order.get(g.get("severity", "low"), 3),
        )

        print(f"\n  {BOLD}Findings ({len(gaps)}):{RESET}")
        for gap in sorted_gaps:
            sev = gap.get("severity", "?").upper()
            desc = gap.get("description", "")
            suggestion = gap.get("suggestion", "")

            if sev == "HIGH":
                color = RED
            elif sev == "MEDIUM":
                color = YELLOW
            else:
                color = DIM

            print(f"    {color}[{sev}]{RESET} {desc}")
            if suggestion:
                print(f"      {DIM}-> {suggestion}{RESET}")

    # Assumptions
    if assumptions:
        print(f"\n  {BOLD}Assumptions ({len(assumptions)}):{RESET}")
        for a in assumptions:
            print(f"    {DIM}- {a}{RESET}")

    # Enrichment
    if enrichment:
        label = "Suggested enrichment"
        if isinstance(enrichment, str):
            print(f"\n  {BOLD}{label}:{RESET}")
            print(f"    {enrichment}")
        else:
            print(f"\n  {BOLD}{label} ({len(enrichment)}):{RESET}")
            for e in enrichment:
                print(f"    + {e}")

    print()


def _save_enrichment(review):
    """Save enrichment data to .forja/spec-enrichment.json if present."""
    enrichment = review.get("enrichment", [])
    assumptions = review.get("assumptions", [])

    if not enrichment and not assumptions:
        return None

    output = {
        "enrichment": enrichment,
        "assumptions": assumptions,
        "gaps_count": len(review.get("gaps", [])),
        "passed": review.get("pass", True),
        "summary": review.get("summary", ""),
    }

    out_path = Path(".forja") / "spec-enrichment.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def cmd_specreview(prd_path, output_format="text"):
    """Run spec review against the PRD."""
    # 1. Read PRD
    prd_file = Path(prd_path)
    if not prd_file.exists():
        print(f"{FAIL_ICON} PRD not found: {prd_path}")
        sys.exit(1)

    prd_content = prd_file.read_text(encoding="utf-8")
    if not prd_content.strip():
        print(f"{FAIL_ICON} PRD is empty: {prd_path}")
        sys.exit(1)

    print(f"Reviewing PRD: {prd_path}")

    # 2. Read existing context
    context_items = _read_context_store()
    learnings = _read_learnings()

    if context_items:
        print(f"  Context store: {len(context_items)} entries")
    if learnings:
        print(f"  Learnings: {len(learnings)} entries")

    # 3. Check API key
    load_dotenv()
    if not os.environ.get("KIMI_API_KEY", ""):
        print(f"{WARN_ICON} Spec review skipped: KIMI_API_KEY not configured")
        sys.exit(0)

    # 4. Call Kimi
    print(f"  Calling Kimi for independent review...")
    messages = _build_messages(prd_content, context_items, learnings)
    raw_content = call_kimi(messages, temperature=0.4)

    if raw_content is None:
        print(f"{WARN_ICON} Spec review skipped: Kimi did not respond")
        sys.exit(0)

    # 5. Parse response (4-step fallback)
    review = parse_json(raw_content)
    if review is None:
        print(f"  {WARN_ICON} Could not parse structured JSON from Kimi response")
        review = {
            "pass": True, "gaps": [], "assumptions": [],
            "enrichment": [], "summary": raw_content[:300],
        }

    # 6. Output results
    if output_format == "json":
        print(json.dumps(review, indent=2, ensure_ascii=False))
    else:
        _print_text(review)

    # 7. Save enrichment
    enrichment_path = _save_enrichment(review)
    if enrichment_path:
        print(f"  Enrichment saved to {enrichment_path}")

    # 8. Exit code - always 0, spec review is informational + enriches
    gaps = review.get("gaps", [])
    if gaps:
        high = sum(1 for g in gaps if g.get("severity", "").lower() == "high")
        med = sum(1 for g in gaps if g.get("severity", "").lower() == "medium")
        low = len(gaps) - high - med
        parts = []
        if high:
            parts.append(f"{high} high")
        if med:
            parts.append(f"{med} medium")
        if low:
            parts.append(f"{low} low")
        print(f"  {PASS_ICON} Found {len(gaps)} gaps ({', '.join(parts)})")
    sys.exit(0)


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
            "Usage: python3 .forja-tools/forja_specreview.py "
            "--prd context/prd.md [--output json|text]"
        )
        sys.exit(1)

    cmd_specreview(prd_path, output_format)


if __name__ == "__main__":
    main()
