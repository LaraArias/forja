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
import sys
from datetime import datetime, timezone
from pathlib import Path

from forja_utils import PASS_ICON, FAIL_ICON, WARN_ICON, GREEN, RED, YELLOW, DIM, BOLD, RESET

LEARNINGS_DIR = Path("context/learnings")

VALID_CATEGORIES = [
    "error-pattern",
    "kimi-finding",
    "edge-case",
    "architecture-decision",
    "spec-gap",
    "assumption",
    "unmet-requirement",
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


def cmd_manifest():
    """Generate compact manifest for context injection (max 2000 chars)."""
    entries = _read_all_entries()

    if not entries:
        print(f"{WARN_ICON} No learnings found in {LEARNINGS_DIR}/")
        return

    # Sort: high severity first, then by timestamp descending
    entries.sort(
        key=lambda e: (
            SEVERITY_ORDER.get(e.get("severity", "low"), 3),
            e.get("timestamp", ""),
        ),
    )
    # Reverse timestamp within same severity (most recent first)
    # Group by severity, reverse each group's timestamps
    by_sev = {}
    for e in entries:
        sev = e.get("severity", "low")
        by_sev.setdefault(sev, []).append(e)

    sorted_entries = []
    for sev in ["high", "medium", "low"]:
        group = by_sev.get(sev, [])
        group.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        sorted_entries.extend(group)

    # Build manifest text, respecting char limit
    lines = ["# Forja Learnings Manifest", ""]
    total_chars = sum(len(l) + 1 for l in lines)

    current_sev = None
    for entry in sorted_entries:
        sev = entry.get("severity", "low").upper()
        cat = entry.get("category", "?")
        learning = entry.get("learning", "")
        source = entry.get("source", "?")

        if sev != current_sev:
            header = f"\n## {sev}"
            if total_chars + len(header) + 1 > MANIFEST_MAX_CHARS:
                break
            lines.append(header)
            total_chars += len(header) + 1
            current_sev = sev

        line = f"- [{cat}] {learning} (src: {source})"
        if total_chars + len(line) + 1 > MANIFEST_MAX_CHARS:
            lines.append(f"- ... ({len(sorted_entries) - len(lines) + 3} more entries truncated)")
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


def cmd_extract():
    """Post-run automatic extraction of learnings from ALL artifact sources."""
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
                learning = f"Spec gap (HIGH): {desc}"
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
                    learning = f"Assumption: {question} → {answer}" if question else f"Assumption: {answer}"
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
            for f in features:
                cycles = f.get("cycles", 0)
                if cycles > 2:
                    fid = f.get("id", "?")
                    desc = f.get("description", f.get("name", fid))
                    feat_status = f.get("status", "pending")
                    status = "eventually passed" if feat_status == "passed" else "still failing"
                    learning = (
                        f"Feature '{desc}' ({teammate}) took {cycles} cycles "
                        f"({status}) - investigate recurring failure pattern"
                    )
                    _try_append("error-pattern", learning,
                                f"features.json/{teammate}", "medium",
                                existing, counts)
        except (json.JSONDecodeError, OSError):
            pass

    # ── 4. outcome-report.json → unmet requirements → "unmet-requirement" ──
    outcome_path = Path(".forja") / "outcome-report.json"
    if outcome_path.exists():
        try:
            data = json.loads(outcome_path.read_text(encoding="utf-8"))
            for req in data.get("unmet", []):
                if isinstance(req, str):
                    desc = req
                elif isinstance(req, dict):
                    desc = req.get("requirement") or req.get("description") or req.get("text", "")
                else:
                    continue
                if not desc:
                    continue
                learning = f"Unmet requirement: {desc}"
                _try_append("unmet-requirement", learning,
                            "outcome-report.json", "high",
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
                learning = f"Cross-model finding: {desc}"
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


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  python3 .forja-tools/forja_learnings.py log "
            "--category X --learning 'text' --source Y --severity Z\n"
            "  python3 .forja-tools/forja_learnings.py manifest\n"
            "  python3 .forja-tools/forja_learnings.py extract"
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

    else:
        print(f"{FAIL_ICON} Unknown command: {command}")
        print("  Valid: log, manifest, extract")
        sys.exit(1)


if __name__ == "__main__":
    main()
