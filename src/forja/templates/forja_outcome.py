#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja Outcome - business-level validation: does the software meet the PRD?

Not a technical validator. Crosses PRD requirements against what was actually
built (features, endpoints) and returns coverage percentage.

Usage:
    python3 .forja-tools/forja_outcome.py --prd context/prd.md [--output json|text]
"""

import glob as glob_mod
import json
import os
import sys
from pathlib import Path

from forja_utils import (
    load_dotenv, call_llm, parse_json,
    PASS_ICON, FAIL_ICON, WARN_ICON, GREEN, RED, DIM, BOLD, RESET,
    Feature,
)

OUTCOME_PROMPT = """\
You are a strict outcome evaluator. Your job is to determine whether the \
software that was built actually fulfills the PRD requirements.

You will receive:
1. The original PRD (what SHOULD be built)
2. Features data (what was attempted and whether each feature passed tests)
3. Validation specs / endpoints (what HTTP routes exist)

First, classify each PRD requirement into one of two categories:
- TECHNICAL: Can be built by code (endpoints, UI, logic, database schema, auth, etc.)
- BUSINESS: Requires human decisions, not code (pricing, partnerships, marketing, \
legal, content strategy, business model, monetization)

Then evaluate only TECHNICAL requirements:
- MET means there is a corresponding feature that passed AND an endpoint that serves it
- UNMET means no matching feature, or the feature failed, or no endpoint exists

BUSINESS requirements are DEFERRED - they cannot be evaluated as met/unmet because \
they require human decisions, not code. Do NOT count them against coverage.

Be strict. A TECHNICAL requirement is only MET if there is clear evidence it works.
Coverage = MET / (MET + UNMET) * 100. DEFERRED requirements are excluded from this \
calculation.

Return ONLY valid JSON, no markdown:
{
  "pass": true/false,
  "coverage": 0-100,
  "met": ["technical requirement 1", "technical requirement 2"],
  "unmet": ["technical requirement 3"],
  "deferred": ["business requirement 1 (reason: needs pricing decision)"],
  "summary": "One-line overall assessment"
}

Set "pass" to true if coverage >= 80, false otherwise.\
"""


# ── Data collection ──────────────────────────────────────────────────

def _read_features():
    """Read all features.json. Returns summary string for the prompt."""
    lines = []
    for fpath in sorted(glob_mod.glob("context/teammates/*/features.json")):
        teammate = Path(fpath).parent.name
        try:
            data = json.loads(Path(fpath).read_text(encoding="utf-8"))
            features = data.get("features", data) if isinstance(data, dict) else data
            if not isinstance(features, list):
                continue
            for f_dict in features:
                feat = Feature.from_dict(f_dict)
                desc = feat.display_name
                status = "PASSED" if feat.status == "passed" else f"FAILED (cycles={feat.cycles})"
                lines.append(f"  [{teammate}] {desc}: {status}")
        except (json.JSONDecodeError, OSError):
            pass
    return "\n".join(lines) if lines else ""


def _read_validation_specs():
    """Read all validation_spec.json. Returns summary string for the prompt."""
    lines = []
    for fpath in sorted(glob_mod.glob("context/teammates/*/validation_spec.json")):
        teammate = Path(fpath).parent.name
        try:
            data = json.loads(Path(fpath).read_text(encoding="utf-8"))
            endpoints = data.get("endpoints", [])
            for ep in endpoints:
                method = ep.get("method", "?")
                path = ep.get("path", "?")
                desc = ep.get("description", "")
                desc_part = f" - {desc}" if desc else ""
                lines.append(f"  [{teammate}] {method} {path}{desc_part}")
        except (json.JSONDecodeError, OSError):
            pass
    return "\n".join(lines) if lines else ""


# ── Core ─────────────────────────────────────────────────────────────

def _build_prompt(prd_content, features_text, specs_text):
    """Build prompt and system message for outcome evaluation."""
    user_msg = f"{OUTCOME_PROMPT}\n\n---\n\nPRD:\n{prd_content}"

    if features_text:
        user_msg += f"\n\n---\n\nFeatures built:\n{features_text}"
    else:
        user_msg += "\n\n---\n\nFeatures built:\n  (no features data found)"

    if specs_text:
        user_msg += f"\n\n---\n\nEndpoints / validation specs:\n{specs_text}"
    else:
        user_msg += "\n\n---\n\nEndpoints / validation specs:\n  (no specs data found)"

    system_msg = (
        "You are a strict outcome evaluator for software projects. "
        "Be precise about coverage. Respond only with valid JSON."
    )
    return user_msg, system_msg


def _print_text(result):
    """Print outcome results in human-readable format."""
    passed = result.get("pass", False)
    coverage = result.get("coverage", 0)
    met = result.get("met", [])
    unmet = result.get("unmet", [])
    deferred = result.get("deferred", [])
    summary = result.get("summary", "")

    # Header
    icon = PASS_ICON if passed else FAIL_ICON
    color = GREEN if passed else RED
    print(f"\n{icon} Outcome Evaluation: {color}{coverage}% coverage{RESET}")

    if summary:
        print(f"  {DIM}{summary}{RESET}")

    # Met requirements
    if met:
        print(f"\n  {BOLD}Requirements MET ({len(met)}):{RESET}")
        for m in met:
            print(f"    {GREEN}✔{RESET} {m}")

    # Unmet requirements
    if unmet:
        print(f"\n  {BOLD}Requirements UNMET ({len(unmet)}):{RESET}")
        for u in unmet:
            print(f"    {RED}✘{RESET} {u}")

    # Deferred (business decisions)
    if deferred:
        print(f"\n  {BOLD}Deferred — Business Decisions ({len(deferred)}):{RESET}")
        for d in deferred:
            print(f"    {WARN_ICON} {d}")

    print()


def _save_report(result):
    """Save outcome report to .forja/outcome-report.json."""
    out_path = Path(".forja") / "outcome-report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def cmd_outcome(prd_path, output_format="text"):
    """Run outcome evaluation."""
    # 1. Read PRD
    prd_file = Path(prd_path)
    if not prd_file.exists():
        print(f"{FAIL_ICON} PRD not found: {prd_path}")
        sys.exit(1)

    prd_content = prd_file.read_text(encoding="utf-8")
    if not prd_content.strip():
        print(f"{FAIL_ICON} PRD is empty: {prd_path}")
        sys.exit(1)

    print(f"Evaluating outcome: {prd_path}")

    # 2. Read features
    features_text = _read_features()
    if features_text:
        count = features_text.count("\n") + 1
        print(f"  Features: {count} entries")
    else:
        print(f"  {WARN_ICON} No features data found in context/teammates/")

    # 3. Read validation specs
    specs_text = _read_validation_specs()
    if specs_text:
        count = specs_text.count("\n") + 1
        print(f"  Endpoints: {count} entries")

    # 4. Check API key
    load_dotenv()
    has_key = any(os.environ.get(k) for k in ("KIMI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"))
    if not has_key:
        print(f"{WARN_ICON} Outcome evaluation skipped: no LLM API key configured")
        sys.exit(0)

    # 5. Call LLM
    print(f"  Calling LLM for outcome evaluation...")
    prompt, system_msg = _build_prompt(prd_content, features_text, specs_text)
    raw_content = call_llm(prompt, system=system_msg)

    if not raw_content:
        print(f"{WARN_ICON} Outcome evaluation skipped: LLM did not respond")
        sys.exit(0)

    # 6. Parse response
    result = parse_json(raw_content)
    if result is None:
        print(f"  {WARN_ICON} Could not parse structured JSON from Kimi response")
        result = {
            "pass": False, "coverage": 0, "met": [],
            "unmet": ["Could not evaluate - response parse error"],
            "summary": raw_content[:300],
        }

    # 7. Output
    if output_format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_text(result)

    # 8. Save report
    report_path = _save_report(result)
    print(f"  Report saved to {report_path}")

    # 9. Exit code: coverage >= 80 passes
    coverage = result.get("coverage", 0)
    if coverage >= 80:
        sys.exit(0)
    else:
        unmet_count = len(result.get("unmet", []))
        print(f"  {unmet_count} unmet requirement(s) - coverage below 80%")
        sys.exit(1)


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
            "Usage: python3 .forja-tools/forja_outcome.py "
            "--prd context/prd.md [--output json|text]"
        )
        sys.exit(1)

    cmd_outcome(prd_path, output_format)


if __name__ == "__main__":
    main()
