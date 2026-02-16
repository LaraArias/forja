#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja Learnings - capture and query learnings between runs.

Append-only JSONL storage per category. Extracts patterns from run artifacts
and generates compact manifests for context injection.

Usage:
    python3 .forja-tools/forja_learnings.py log --category X --learning "text" --source Y --severity Z
    python3 .forja-tools/forja_learnings.py manifest
    python3 .forja-tools/forja_learnings.py extract
"""

import glob as glob_mod
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from forja_utils import PASS_ICON, FAIL_ICON, WARN_ICON, GREEN, RED, YELLOW, DIM, BOLD, RESET, Feature

LEARNINGS_DIR = Path("context/learnings")

VALID_CATEGORIES = [
    "error-pattern",
    "kimi-finding",
    "edge-case",
    "architecture-decision",
    "spec-gap",
    "assumption",
    "unmet-requirement",
    "product-backlog",
]

MANIFEST_MAX_CHARS = 2000

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


# ── Storage ──────────────────────────────────────────────────────────

def _category_file(category):
    """Return path to JSONL file for a category."""
    return LEARNINGS_DIR / f"{category}.jsonl"


def _append_entry(category, entry):
    """Append a single entry to the category JSONL file."""
    LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)
    fpath = _category_file(category)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with open(fpath, "a", encoding="utf-8") as f:
        f.write(line)
    return fpath


def _read_all_entries():
    """Read all entries across all JSONL files. Returns list of dicts."""
    entries = []
    for fpath in sorted(glob_mod.glob(str(LEARNINGS_DIR / "*.jsonl"))):
        try:
            for line in Path(fpath).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass
    return entries


def _make_entry(category, learning, source, severity):
    """Create a learning entry dict."""
    run_id = os.environ.get("FORJA_RUN_ID", "unknown")
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "learning": learning,
        "source": source,
        "severity": severity,
        "run_id": run_id,
    }


# ── Commands ─────────────────────────────────────────────────────────

def cmd_log(category, learning, source, severity):
    """Log a single learning entry."""
    if category not in VALID_CATEGORIES:
        print(f"{FAIL_ICON} Invalid category: {category}")
        print(f"  Valid: {', '.join(VALID_CATEGORIES)}")
        sys.exit(1)

    if severity not in SEVERITY_ORDER:
        print(f"{FAIL_ICON} Invalid severity: {severity}")
        print(f"  Valid: high, medium, low")
        sys.exit(1)

    entry = _make_entry(category, learning, source, severity)
    fpath = _append_entry(category, entry)

    sev_upper = severity.upper()
    if severity == "high":
        color = RED
    elif severity == "medium":
        color = YELLOW
    else:
        color = DIM

    print(f"{PASS_ICON} Learning logged: {color}[{sev_upper}]{RESET} {learning[:80]}")
    print(f"  {DIM}Category: {category} | Source: {source} | File: {fpath}{RESET}")


_ACTION_TYPE_PATTERNS = [
    ("Dependencies to auto-install", re.compile(
        r"auto-add|dependencies|requirements\.txt|install|bcrypt|python-jose|pytest|httpx|node",
        re.IGNORECASE,
    )),
    ("Validation rules to enforce", re.compile(
        r"validation rule|code issue|reviewer|prevent this pattern|acceptance criteria",
        re.IGNORECASE,
    )),
    ("PRD patterns to include", re.compile(
        r"PRD gap|PRD template|unvalidated assumption|requirement not met|explicit.*requirement",
        re.IGNORECASE,
    )),
]


def _classify_action_type(learning_text):
    """Classify a learning into an action type for the manifest."""
    for action_type, pattern in _ACTION_TYPE_PATTERNS:
        if pattern.search(learning_text):
            return action_type
    return "Other actions"


def cmd_manifest():
    """Generate compact manifest grouped by action type (max 2000 chars)."""
    entries = _read_all_entries()

    if not entries:
        print(f"{WARN_ICON} No learnings found in {LEARNINGS_DIR}/")
        return

    # Sort: high severity first, then by timestamp descending
    by_sev = {}
    for e in entries:
        sev = e.get("severity", "low")
        by_sev.setdefault(sev, []).append(e)

    sorted_entries = []
    for sev in ["high", "medium", "low"]:
        group = by_sev.get(sev, [])
        group.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        sorted_entries.extend(group)

    # Group by action type
    by_action = {}
    for entry in sorted_entries:
        learning = entry.get("learning", "")
        action_type = _classify_action_type(learning)
        by_action.setdefault(action_type, []).append(entry)

    # Build manifest text, respecting char limit
    lines = ["# Forja Learnings Manifest", ""]
    total_chars = sum(len(l) + 1 for l in lines)

    # Emit in deterministic order
    section_order = [t for t, _ in _ACTION_TYPE_PATTERNS] + ["Other actions"]
    for section in section_order:
        section_entries = by_action.get(section)
        if not section_entries:
            continue

        header = f"\n## {section}"
        if total_chars + len(header) + 1 > MANIFEST_MAX_CHARS:
            break
        lines.append(header)
        total_chars += len(header) + 1

        for entry in section_entries:
            learning = entry.get("learning", "")
            sev = entry.get("severity", "low").upper()
            line = f"- [{sev}] {learning}"
            if total_chars + len(line) + 1 > MANIFEST_MAX_CHARS:
                remaining = len(sorted_entries) - sum(
                    1 for l in lines if l.startswith("- [")
                )
                lines.append(f"- ... ({remaining} more entries truncated)")
                total_chars += 50
                break
            lines.append(line)
            total_chars += len(line) + 1

    manifest = "\n".join(lines)
    print(manifest)
    print(f"\n{DIM}({len(sorted_entries)} total entries, {total_chars} chars){RESET}")


def _existing_learning_texts():
    """Return a set of all existing learning texts for deduplication."""
    texts = set()
    for entry in _read_all_entries():
        text = entry.get("learning", "")
        if text:
            texts.add(text)
    return texts


def _try_append(category, learning, source, severity, existing, counts):
    """Append a learning if it doesn't already exist. Updates counts dict."""
    if learning in existing:
        return
    entry = _make_entry(category, learning, source, severity)
    _append_entry(category, entry)
    existing.add(learning)
    counts[category] = counts.get(category, 0) + 1

    if severity == "high":
        color = RED
    elif severity == "medium":
        color = YELLOW
    else:
        color = DIM
    print(f"  {color}[{category}]{RESET} {learning[:90]}")


_AUTH_KEYWORDS = re.compile(r"auth|login|jwt|token|password|session|oauth", re.IGNORECASE)
_DB_KEYWORDS = re.compile(r"database|model|migration|schema|table|sql|orm", re.IGNORECASE)
_TEST_KEYWORDS = re.compile(r"test|qa|integration|e2e|endpoint.?pass", re.IGNORECASE)
_FRONTEND_KEYWORDS = re.compile(r"frontend|html|css|js|react|vue|angular|webpack|vite", re.IGNORECASE)


def _infer_error_pattern_action(desc, teammate, cycles):
    """Infer an actionable learning from a feature that required many cycles."""
    text = f"{desc} {teammate}"
    if _AUTH_KEYWORDS.search(text):
        return (
            f"Auto-add authentication dependencies (bcrypt, python-jose) to "
            f"requirements.txt before auth features start. "
            f"Feature '{desc}' ({teammate}) failed {cycles} cycles due to "
            f"missing dependencies."
        )
    if _DB_KEYWORDS.search(text):
        return (
            f"Run database initialization and create tables before database "
            f"features start. Feature '{desc}' ({teammate}) failed {cycles} "
            f"cycles due to missing schema setup."
        )
    if _TEST_KEYWORDS.search(text):
        return (
            f"Install test dependencies (pytest, httpx) before QA starts. "
            f"Pre-create test fixtures. Feature '{desc}' ({teammate}) failed "
            f"{cycles} cycles due to missing test infrastructure."
        )
    if _FRONTEND_KEYWORDS.search(text):
        return (
            f"Verify node is available and install frontend tooling before "
            f"frontend features start. Feature '{desc}' ({teammate}) failed "
            f"{cycles} cycles due to missing frontend setup."
        )
    return (
        f"Feature '{desc}' ({teammate}) required {cycles} cycles. "
        f"Add explicit acceptance criteria with input/output examples to PRD."
    )


def cmd_extract():
    """Post-run automatic extraction of actionable learnings from ALL artifact sources."""
    existing = _existing_learning_texts()
    counts = {}

    # ── 1. spec-enrichment.json → HIGH severity gaps → "spec-gap" ──
    enrichment_path = Path(".forja") / "spec-enrichment.json"
    if enrichment_path.exists():
        try:
            data = json.loads(enrichment_path.read_text(encoding="utf-8"))
            for gap in data.get("gaps", []):
                sev = (gap.get("severity") or "").lower()
                if sev != "high":
                    continue
                desc = gap.get("description") or gap.get("gap") or gap.get("title", "")
                if not desc:
                    continue
                suggestion = gap.get("suggestion", "add explicit specification")
                learning = (
                    f"PRD gap found: {desc}. Auto-fix: {suggestion}. "
                    f"Add this to PRD template for future projects."
                )
                _try_append("spec-gap", learning, "spec-enrichment.json", "high",
                            existing, counts)
        except (json.JSONDecodeError, OSError):
            pass

    # ── 2. plan-transcript.json → answers tagged ASSUMPTION → "assumption" ──
    transcript_path = Path(".forja") / "plan-transcript.json"
    if transcript_path.exists():
        try:
            data = json.loads(transcript_path.read_text(encoding="utf-8"))
            answers = data.get("answers", data) if isinstance(data, dict) else data
            if isinstance(answers, list):
                for ans in answers:
                    tags = ans.get("tags", [])
                    if not isinstance(tags, list):
                        tags = [tags] if tags else []
                    tag_upper = [str(t).upper() for t in tags]
                    if "ASSUMPTION" not in tag_upper:
                        continue
                    question = ans.get("question", "")
                    answer = ans.get("answer", "")
                    if not answer:
                        continue
                    learning = (
                        f"Unvalidated assumption: {question} -> Default: {answer}. "
                        f"Action: validate with stakeholder or add to PRD as explicit requirement."
                    )
                    _try_append("assumption", learning, "plan-transcript.json", "medium",
                                existing, counts)
        except (json.JSONDecodeError, OSError):
            pass

    # ── 3. features.json → features with cycles > 2 → "error-pattern" ──
    for fpath in sorted(glob_mod.glob("context/teammates/*/features.json")):
        teammate = Path(fpath).parent.name
        try:
            data = json.loads(Path(fpath).read_text(encoding="utf-8"))
            features = data.get("features", data) if isinstance(data, dict) else data
            if not isinstance(features, list):
                continue
            for f_dict in features:
                feat = Feature.from_dict(f_dict)
                if feat.cycles > 2:
                    desc = feat.display_name
                    learning = _infer_error_pattern_action(desc, teammate, feat.cycles)
                    _try_append("error-pattern", learning,
                                f"features.json/{teammate}", "medium",
                                existing, counts)
        except (json.JSONDecodeError, OSError):
            pass

    # ── 4. outcome-report.json → unmet + deferred requirements ──
    outcome_path = Path(".forja") / "outcome-report.json"
    if outcome_path.exists():
        try:
            data = json.loads(outcome_path.read_text(encoding="utf-8"))

            # Unmet requirements: classify by type
            for req in data.get("unmet", []):
                if isinstance(req, str):
                    desc = req
                    req_type = "technical"
                elif isinstance(req, dict):
                    desc = req.get("requirement") or req.get("description") or req.get("text", "")
                    req_type = (req.get("type") or "technical").lower()
                else:
                    continue
                if not desc:
                    continue

                if req_type == "business":
                    learning = (
                        f"Product decision needed: {desc}. "
                        f"This is not a code issue."
                    )
                    _try_append("product-backlog", learning,
                                "outcome-report.json", "low",
                                existing, counts)
                else:
                    learning = (
                        f"Requirement not met: {desc}. Action: add as explicit "
                        f"feature with acceptance criteria: [input] -> [expected output]."
                    )
                    _try_append("unmet-requirement", learning,
                                "outcome-report.json", "high",
                                existing, counts)

            # Deferred items (business decisions) → product-backlog
            for req in data.get("deferred", []):
                if isinstance(req, str):
                    desc = req
                elif isinstance(req, dict):
                    desc = req.get("requirement") or req.get("description") or req.get("text", "")
                else:
                    continue
                if not desc:
                    continue
                learning = (
                    f"Product decision needed: {desc}. "
                    f"This is not a code issue."
                )
                _try_append("product-backlog", learning,
                            "outcome-report.json", "low",
                            existing, counts)
        except (json.JSONDecodeError, OSError):
            pass

    # ── 5. crossmodel/*.json → HIGH severity issues → "kimi-finding" ──
    for fpath in sorted(glob_mod.glob(".forja/crossmodel/*.json")):
        fname = Path(fpath).name
        try:
            data = json.loads(Path(fpath).read_text(encoding="utf-8"))
            issues = data.get("issues", [])
            if not isinstance(issues, list):
                continue
            for issue in issues:
                sev = (issue.get("severity") or "").lower()
                if sev != "high":
                    continue
                desc = issue.get("description") or issue.get("issue") or issue.get("title", "")
                if not desc:
                    continue
                file_ref = issue.get("file", fname)
                learning = (
                    f"Code issue found by reviewer: {desc} in {file_ref}. "
                    f"Action: add validation rule to prevent this pattern."
                )
                _try_append("kimi-finding", learning,
                            f"crossmodel/{fname}", "high",
                            existing, counts)
        except (json.JSONDecodeError, OSError):
            pass

    # ── Summary ──
    total = sum(counts.values())
    if total > 0:
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        print(f"\n{PASS_ICON} Extracted {total} learning(s): {', '.join(parts)}")
    else:
        print(f"{WARN_ICON} No learnings to extract (no issues found in artifacts)")


# ── Apply learnings to context ────────────────────────────────────────

_AUDIENCE_KEYWORDS = re.compile(
    r"audience|copy|messaging|value.?prop|positioning|brand|tone|voice|conversion",
    re.IGNORECASE,
)
_STACK_KEYWORDS = re.compile(
    r"stack|architect|infra|deploy|database|framework|runtime|dependency",
    re.IGNORECASE,
)
_DEPENDENCY_KEYWORDS = re.compile(
    r"dependency|install|import|module|package|pip|npm|apt|brew|redis|docker|postgres|kafka",
    re.IGNORECASE,
)
_BUSINESS_KEYWORDS = re.compile(
    r"business|product|pricing|legal|compliance|stakeholder|manual|human|decision",
    re.IGNORECASE,
)


def _ensure_file(path, header=""):
    """Create file with header if it doesn't exist. Returns Path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(header + "\n", encoding="utf-8")
    return p


def _append_to_file(path, line):
    """Append a line to a file, avoiding exact duplicates."""
    p = Path(path)
    existing = ""
    if p.exists():
        existing = p.read_text(encoding="utf-8")
    if line.strip() in existing:
        return False
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return True


def cmd_apply():
    """Read learnings and auto-update context files for the next run.

    Categories handled:
    - unmet-requirement (TECHNICAL) -> context/prd.md
    - spec-gap -> context/domains/ or context/company/tech-standards.md
    - error-pattern (dependency) -> context/company/build-constraints.md
    - BUSINESS/PRODUCT -> context/product-backlog.md
    """
    entries = _read_all_entries()
    if not entries:
        print(f"{WARN_ICON} No learnings to apply")
        return

    applied = 0
    prd_path = Path("context/prd.md")

    for entry in entries:
        cat = entry.get("category", "")
        learning = entry.get("learning", "")
        severity = entry.get("severity", "low")
        if not learning:
            continue

        # ── 1. unmet-requirement → PRD ──
        if cat == "unmet-requirement":
            _ensure_file(prd_path, "# PRD\n")
            content = prd_path.read_text(encoding="utf-8")
            section = "## Learnings from Previous Runs"
            if section not in content:
                with open(prd_path, "a", encoding="utf-8") as f:
                    f.write(f"\n{section}\n\n")
            # Extract the action part
            action = learning
            if "Action:" in learning:
                action = learning.split("Action:", 1)[1].strip()
            elif "Auto-fix:" in learning:
                action = learning.split("Auto-fix:", 1)[1].strip()
            line = f"- {action}"
            if _append_to_file(prd_path, line):
                applied += 1

        # ── 2. spec-gap → domains or tech-standards ──
        elif cat == "spec-gap":
            if _AUDIENCE_KEYWORDS.search(learning):
                # Try to find domain value-props files
                domain_dirs = sorted(Path("context/domains").glob("*/")) if Path("context/domains").is_dir() else []
                if domain_dirs:
                    target = domain_dirs[0] / "value-propositions.md"
                else:
                    target = Path("context/domains/default/value-propositions.md")
                _ensure_file(target, "# Value Propositions\n")
                line = f"- [SPEC-GAP] {learning[:200]}"
                if _append_to_file(target, line):
                    applied += 1
            elif _STACK_KEYWORDS.search(learning):
                target = Path("context/company/tech-standards.md")
                _ensure_file(target, "# Tech Standards\n")
                line = f"- [SPEC-GAP] {learning[:200]}"
                if _append_to_file(target, line):
                    applied += 1
            else:
                # Default: append to tech-standards
                target = Path("context/company/tech-standards.md")
                _ensure_file(target, "# Tech Standards\n")
                line = f"- [SPEC-GAP] {learning[:200]}"
                if _append_to_file(target, line):
                    applied += 1

        # ── 3. error-pattern (dependency) → build-constraints ──
        elif cat == "error-pattern":
            if _DEPENDENCY_KEYWORDS.search(learning):
                target = Path("context/company/build-constraints.md")
                _ensure_file(target, "# Build Constraints\n")
                line = f"- {learning[:200]}"
                if _append_to_file(target, line):
                    applied += 1

        # ── 4. BUSINESS/PRODUCT learnings → product-backlog ──
        if _BUSINESS_KEYWORDS.search(learning) and cat in ("unmet-requirement", "assumption"):
            target = Path("context/product-backlog.md")
            _ensure_file(
                target,
                "# Product Backlog\n\n## Product Decisions Needed\n",
            )
            line = f"- {learning[:200]}"
            if _append_to_file(target, line):
                applied += 1

    if applied > 0:
        print(f"{PASS_ICON} Applied {applied} learnings to context files")
    else:
        print(f"{DIM}No new learnings to apply (already up to date){RESET}")


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  python3 .forja-tools/forja_learnings.py log "
            "--category X --learning 'text' --source Y --severity Z\n"
            "  python3 .forja-tools/forja_learnings.py manifest\n"
            "  python3 .forja-tools/forja_learnings.py extract\n"
            "  python3 .forja-tools/forja_learnings.py apply"
        )
        sys.exit(1)

    command = sys.argv[1]

    if command == "log":
        category = None
        learning = None
        source = "unknown"
        severity = "medium"

        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--category" and i + 1 < len(sys.argv):
                category = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--learning" and i + 1 < len(sys.argv):
                learning = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--source" and i + 1 < len(sys.argv):
                source = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--severity" and i + 1 < len(sys.argv):
                severity = sys.argv[i + 1]
                i += 2
            else:
                i += 1

        if not category or not learning:
            print(f"{FAIL_ICON} Missing --category and/or --learning")
            sys.exit(1)

        cmd_log(category, learning, source, severity)

    elif command == "manifest":
        cmd_manifest()

    elif command == "extract":
        cmd_extract()

    elif command == "apply":
        cmd_apply()

    else:
        print(f"{FAIL_ICON} Unknown command: {command}")
        print("  Valid: log, manifest, extract, apply")
        sys.exit(1)


if __name__ == "__main__":
    main()
