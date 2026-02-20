#!/usr/bin/env python3
"""Forja runner - full pipeline with live progress monitoring.

Pipeline: spec-review -> context-inject -> build -> smoke-test -> tests -> visual-eval -> outcome -> learnings -> observatory
"""

from __future__ import annotations

import difflib
import glob as glob_mod
import hashlib
import json
import logging
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("forja")

from forja.config_loader import load_config
from forja.constants import (
    BUILD_PROMPT, CLAUDE_MD, CONTEXT_DIR, FORJA_DIR, FORJA_TOOLS,
    PRD_PATH, SPECS_DIR, STORE_DIR, TEAMMATES_DIR, WORKFLOW_PATH,
)
from forja.utils import (
    BOLD, CYAN, DIM, GREEN, RED, YELLOW, RESET,
    Feature, call_llm, gather_context, load_dotenv, parse_json,
    read_feature_status, safe_read_json,
)

# ── Placeholder detection ────────────────────────────────────────────

_PLACEHOLDER_MARKERS = (
    "Describe tu proyecto",  # Legacy marker from older init templates
    "Describe your project here",
    "Describe your project",
)

MIN_REAL_CONTENT_CHARS = 50

# ── Iteration expert panel fallbacks ──────────────────────────────────

FALLBACK_ITERATION_EXPERTS = [
    {
        "name": "Build Failure Analyst",
        "field": "Build Diagnostics & Root Cause Analysis",
        "perspective": "Analyzing why features failed and identifying what DETAIL is missing from the PRD.",
    },
    {
        "name": "PRD Enrichment Specialist",
        "field": "Specification Growth & Acceptance Criteria",
        "perspective": "Adding specificity, edge cases, implementation notes, and acceptance criteria to make features buildable.",
    },
    {
        "name": "Dependency & Constraints Engineer",
        "field": "Build Tooling & Runtime Constraints",
        "perspective": "Ensuring the PRD only requires tools Claude Code can actually use, and adding explicit constraints.",
    },
]

FALLBACK_ITERATION_QUESTIONS = [
    {
        "id": 1,
        "expert_name": "Build Failure Analyst",
        "question": "Which failed features need MORE DETAIL in the PRD to build correctly?",
        "why": "Most build failures come from vague descriptions. Adding specificity fixes them.",
        "default": "Add file structure, function signatures, data formats, and step-by-step implementation notes to each failed feature.",
    },
    {
        "id": 2,
        "expert_name": "Build Failure Analyst",
        "question": "What acceptance criteria are missing that would prevent false-positive passes?",
        "why": "Without clear acceptance criteria, features 'pass' but don't actually work.",
        "default": "Add testable acceptance criteria: input/output examples, expected behaviors, error cases.",
    },
    {
        "id": 3,
        "expert_name": "PRD Enrichment Specialist",
        "question": "Which features need edge cases, error handling, or validation rules documented?",
        "why": "Edge cases not described in the PRD are edge cases that will fail at runtime.",
        "default": "Document: input validation rules, error messages, boundary conditions, empty-state behaviors.",
    },
    {
        "id": 4,
        "expert_name": "PRD Enrichment Specialist",
        "question": "What data structures, file layouts, or API contracts should be explicitly defined?",
        "why": "Explicit contracts prevent the builder from guessing wrong about interfaces.",
        "default": "Define: JSON schemas, function signatures, file paths, class hierarchies, module dependencies.",
    },
    {
        "id": 5,
        "expert_name": "Dependency & Constraints Engineer",
        "question": "Does the PRD require any technology that Claude Code cannot install?",
        "why": "System-level dependencies (Redis, PostgreSQL, Docker) will always fail.",
        "default": "Replace all system dependencies: Redis -> dict/SQLite, PostgreSQL -> SQLite, Docker -> direct install.",
    },
    {
        "id": 6,
        "expert_name": "Dependency & Constraints Engineer",
        "question": "Are the build instructions clear enough for an automated agent to follow without guessing?",
        "why": "Ambiguous instructions lead to incorrect implementations that fail validation.",
        "default": "Add explicit file paths, function signatures, expected response formats, and build order to the PRD.",
    },
]

# ── Tech stack expert panel fallbacks ────────────────────────────────

FALLBACK_TECH_EXPERTS = [
    {
        "name": "Stack Diagnostician",
        "field": "Build Failure Root-Cause Analysis",
        "perspective": (
            "Analyzing which technology choices caused build failures: "
            "framework mismatches, missing system dependencies, version conflicts, "
            "and sandbox limitations."
        ),
    },
    {
        "name": "Dependency Resolver",
        "field": "Package Management & Dependency Mapping",
        "perspective": (
            "Mapping every failed or unbuildable dependency to a pip/npm-installable "
            "alternative. Identifying implicit system requirements that Claude Code "
            "cannot satisfy."
        ),
    },
    {
        "name": "App Architecture Advisor",
        "field": "Application Structure & Integration Patterns",
        "perspective": (
            "Reviewing project structure, file layout, and frontend/backend integration "
            "patterns. Ensuring conventions match the chosen framework."
        ),
    },
]

FALLBACK_TECH_QUESTIONS = [
    {
        "id": 1,
        "expert_name": "Stack Diagnostician",
        "question": "Which tech stack choices caused build failures? Group by root cause.",
        "why": "Understanding the tech root cause prevents repeating the same failures.",
        "default": "List each failure grouped by technology: e.g. 'SQLite limitation', 'missing system dep', 'framework mismatch'.",
    },
    {
        "id": 2,
        "expert_name": "Stack Diagnostician",
        "question": "Are there framework-specific assumptions that don't hold in the Claude Code sandbox?",
        "why": "Frameworks assume environments (Docker, specific OS, system services) that may not exist.",
        "default": "Document framework assumptions: e.g. 'Next.js ISR needs server runtime', 'Django needs PostgreSQL for full-text search'.",
    },
    {
        "id": 3,
        "expert_name": "Dependency Resolver",
        "question": "Which dependencies need replacement with pip/npm-installable alternatives?",
        "why": "System-level deps (Redis, PostgreSQL, Docker) always fail. Must map to alternatives.",
        "default": "Redis -> dict/shelve, PostgreSQL -> SQLite, Docker -> direct pip install, bcrypt -> passlib[bcrypt].",
    },
    {
        "id": 4,
        "expert_name": "Dependency Resolver",
        "question": "Are there implicit system requirements not declared in the PRD?",
        "why": "Undeclared deps (ffmpeg, ImageMagick, system fonts, C compilers) cause silent failures.",
        "default": "List all undeclared system requirements and their pip/npm alternatives or removal strategies.",
    },
    {
        "id": 5,
        "expert_name": "App Architecture Advisor",
        "question": "Does the project structure match the chosen framework's conventions?",
        "why": "Wrong file layout causes import errors, missing routes, and broken auto-discovery.",
        "default": "Verify: FastAPI -> src/main.py + routers/, React -> src/App.jsx + components/, Flask -> app.py + templates/.",
    },
    {
        "id": 6,
        "expert_name": "App Architecture Advisor",
        "question": "What integration patterns between components should be documented in the PRD?",
        "why": "Undocumented API contracts, CORS config, and shared types cause integration failures.",
        "default": "Document: API base URL, CORS origins, request/response schemas, shared constants, env variables.",
    },
]


def _prd_needs_planning(prd: Path) -> bool:
    """Return True if the PRD is missing, empty, or a placeholder template.

    A PRD is considered a placeholder when:
    - The file does not exist or is empty.
    - The file contains a known placeholder string (from ``forja init``).
    - The real content (stripping the ``# PRD`` heading) is < 50 chars.
    """
    if not prd.exists():
        return True

    raw = prd.read_text(encoding="utf-8").strip()
    if not raw:
        return True

    # Known placeholder strings
    for marker in _PLACEHOLDER_MARKERS:
        if marker in raw:
            return True

    # Strip leading heading to measure "real" content
    lines = raw.splitlines()
    body = "\n".join(l for l in lines if not l.startswith("# ")).strip()
    if len(body) < MIN_REAL_CONTENT_CHARS:
        return True

    return False


def _spinner_frames():
    """Yield spinner animation frames."""
    frames = ["   ", ".  ", ".. ", "..."]
    while True:
        for f in frames:
            yield f


def _format_duration(seconds):
    """Format seconds into human readable duration."""
    m, s = divmod(int(seconds), 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _detect_entry_point() -> str:
    """Detect how to run the built project. Returns a shell command or empty string."""
    cwd = Path.cwd()

    # Check package.json for scripts
    pkg_json = cwd / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            if "start" in scripts:
                return "npm start"
            if "dev" in scripts:
                return "npm run dev"
        except (json.JSONDecodeError, OSError):
            pass

    # Python entry points (order matters: main.py > app.py > manage.py > run.py)
    for name in ("main.py", "app.py", "run.py"):
        if (cwd / name).exists():
            return f"python {name}"
    if (cwd / "manage.py").exists():
        return "python manage.py runserver"

    # Check for index.html (static site)
    if (cwd / "index.html").exists():
        return "open index.html"

    # Check for src/ entry points
    for name in ("main.py", "app.py"):
        if (cwd / "src" / name).exists():
            return f"python src/{name}"

    # Check pyproject.toml for scripts
    pyproject = cwd / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            if "[project.scripts]" in text:
                # Extract first script name
                in_scripts = False
                for line in text.splitlines():
                    if "[project.scripts]" in line:
                        in_scripts = True
                        continue
                    if in_scripts and "=" in line and not line.strip().startswith("["):
                        script_name = line.split("=")[0].strip().strip('"')
                        return f"pip install -e . && {script_name}"
                    if in_scripts and line.strip().startswith("["):
                        break
        except OSError:
            pass

    # Check for Makefile with run target
    if (cwd / "Makefile").exists():
        try:
            text = (cwd / "Makefile").read_text(encoding="utf-8")
            if "run:" in text:
                return "make run"
        except OSError:
            pass

    return ""


def _phase_header(num, name, total=5):
    """Print phase header with timestamp and emit start event."""
    ts = time.strftime("%H:%M:%S")
    print(f"\n{BOLD}{CYAN}  Phase {num}/{total}: {name}{RESET} {DIM}[{ts}]{RESET}")
    _emit_event("phase.start", {"phase": name, "phase_num": num, "total_phases": total})


def _phase_result(passed, message):
    """Print phase result."""
    icon = f"{GREEN}PASSED{RESET}" if passed else f"{RED}FAILED{RESET}"
    print(f"  {icon} {message}")


# ── Unified Event Stream ────────────────────────────────────────────

_EVENT_STREAM = FORJA_DIR / "event-stream.jsonl"


def _emit_event(event_type, data, agent="system"):
    """Append a structured event to the unified event stream."""
    try:
        _EVENT_STREAM.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "id": f"{event_type}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "agent": agent,
            "data": data,
        }
        with open(_EVENT_STREAM, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── Phase 0: Spec Review (informational, always continues) ─────────

def _run_spec_review(prd_path):
    """Run spec review and auto-enrich PRD. Never blocks the pipeline."""
    specreview = FORJA_TOOLS / "forja_specreview.py"
    if not specreview.exists():
        print(f"  {DIM}skipped (forja_specreview.py not found){RESET}")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(specreview), "--prd", str(prd_path), "--output", "json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(f"  {YELLOW}Warning: spec review timed out after 120s, continuing{RESET}")
        return

    # Show gap summary from enrichment file (not individual gaps — too noisy)
    enrichment_path = FORJA_DIR / "spec-enrichment.json"
    data = safe_read_json(enrichment_path)
    if data is not None:
        gaps_count = data.get("gaps_count", 0)
        enrichment = data.get("enrichment", [])
        assumptions = data.get("assumptions", [])

        # Show gap counts
        if gaps_count:
            # Try to get severity breakdown from stdout JSON
            sev_parts = _extract_severity_counts(result.stdout)
            if sev_parts:
                print(f"  Found {gaps_count} gaps ({sev_parts})")
            else:
                print(f"  Found {gaps_count} gap(s)")

        # Auto-enrich PRD
        if enrichment or assumptions:
            _append_enrichment_to_prd(prd_path, enrichment, assumptions)
            _phase_result(True, f"Auto-enriched PRD with {len(enrichment)} specifications")
        else:
            _phase_result(True, "no enrichment needed")
    elif enrichment_path.exists():
        _phase_result(True, "review complete (no enrichment file)")
    else:
        # No enrichment file — Kimi might have been unavailable
        if "skipped" in result.stdout.lower() or "WARN" in result.stdout:
            print(f"  {DIM}skipped (Kimi unavailable){RESET}")
        else:
            _phase_result(True, "review complete")


def _extract_severity_counts(stdout):
    """Try to extract severity counts from specreview JSON output."""
    try:
        for line in reversed(stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                gaps = data.get("gaps", [])
                if not gaps:
                    return ""
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
                return ", ".join(parts)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug("Failed to parse severity counts: %s", exc)
    return ""


def _append_enrichment_to_prd(prd_path, enrichment, assumptions):
    """Append enrichment + assumptions section to PRD.

    If enrichment has >20 items, consolidate LOW severity into a summary paragraph.
    """
    prd = Path(prd_path)
    content = prd.read_text(encoding="utf-8")

    section_header = "## Additional Specifications (auto-generated by Forja)"

    # Don't append if already there
    if section_header in content:
        return

    block = f"\n\n{section_header}\n\n"

    # If >20 items, consolidate (keep all but mark as compact)
    if len(enrichment) > 20:
        # Keep first 20 as bullets, summarize the rest
        for item in enrichment[:20]:
            block += f"- {item}\n"
        remaining = enrichment[20:]
        block += f"\nAdditionally, identified {len(remaining)} minor specifications: "
        block += "; ".join(remaining) + ".\n"
    else:
        for item in enrichment:
            block += f"- {item}\n"

    if assumptions:
        block += "\n### Assumptions\n\n"
        for a in assumptions:
            block += f"- {a}\n"

    prd.write_text(content + block, encoding="utf-8")


# ── Workflow-based feature generation ─────────────────────────────

def _generate_workflow_features(workflow_path: Path, teammates_dir: Path):
    """Generate features.json and phase_prompt.md for each workflow phase.

    When a workflow.json exists, features are organized by sequential
    phases instead of independent epics.
    """
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

    for i, phase in enumerate(workflow.get("phases", [])):
        agent_name = phase["agent"]
        agent_dir = teammates_dir / agent_name
        agent_dir.mkdir(parents=True, exist_ok=True)

        features = {
            "features": [{
                "id": f"{agent_name}-001",
                "description": phase.get("validation", f"Complete {phase.get('role', agent_name)} phase"),
                "status": "pending",
                "cycles": 0,
                "phase_order": i + 1,
                "input": phase.get("input", []),
                "output": phase.get("output", ""),
            }]
        }
        (agent_dir / "features.json").write_text(
            json.dumps(features, indent=2), encoding="utf-8",
        )

        # Write workflow prompt for this agent
        prompt = phase.get("prompt", "")
        if prompt:
            (agent_dir / "phase_prompt.md").write_text(
                f"# Phase Instructions for {agent_name}\n\n{prompt}\n",
                encoding="utf-8",
            )


def _generate_agent_context(workflow_path: Path, teammates_dir: Path):
    """Generate per-agent context.md from workflow input fields.

    Each agent gets ONLY the context files declared in its workflow
    ``input`` list — bounded context, not the full dump.
    """
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

    for phase in workflow.get("phases", []):
        agent_name = phase["agent"]
        agent_dir = teammates_dir / agent_name
        agent_dir.mkdir(parents=True, exist_ok=True)

        input_paths = phase.get("input", [])
        parts: list[str] = []

        for input_path in input_paths:
            p = Path(input_path)
            if p.is_file():
                try:
                    text = p.read_text(encoding="utf-8").strip()
                    if text:
                        parts.append(f"## {p}\n\n{text[:2000]}")
                except (OSError, UnicodeDecodeError):
                    pass
            elif p.is_dir():
                for fpath in sorted(p.rglob("*")):
                    if not fpath.is_file():
                        continue
                    if fpath.suffix not in (".md", ".json", ".txt"):
                        continue
                    if fpath.name.startswith("_") or fpath.name == "README.md":
                        continue
                    try:
                        text = fpath.read_text(encoding="utf-8").strip()
                        if text:
                            parts.append(f"### {fpath}\n\n{text[:1000]}")
                    except (OSError, UnicodeDecodeError):
                        pass

        if not parts:
            continue

        header = (
            f"# Context for {phase.get('role', agent_name)}\n\n"
            f"Output: {phase.get('output', 'N/A')}\n\n---\n\n"
        )
        context_text = header + "\n\n".join(parts)
        if len(context_text) > 6000:
            context_text = context_text[:6000] + "\n\n... (truncated)"

        (agent_dir / "context.md").write_text(
            context_text + "\n", encoding="utf-8"
        )


# ── Phase 1: Context injection into CLAUDE.md ────────────────────────

def _inject_context_into_claude_md():
    """Inject shared context (store + learnings) into CLAUDE.md."""
    claude_md = CLAUDE_MD
    if not claude_md.exists():
        return

    content = claude_md.read_text(encoding="utf-8")

    # Remove old injections to refresh
    for marker in [
        "## CRITICAL: Previous Run Learnings (auto-generated)",
        "## Shared Context (auto-generated)",
    ]:
        if marker in content:
            lines = content.splitlines()
            new_lines = []
            skip = False
            for line in lines:
                if line.strip() == marker:
                    skip = True
                    continue
                if skip and line.startswith("## "):
                    skip = False
                if not skip:
                    new_lines.append(line)
            content = "\n".join(new_lines)

    # Build context section
    context_parts = []

    # _index.md: project context map (inject first, full content)
    index_path = CONTEXT_DIR / "_index.md"
    if index_path.exists():
        try:
            index_text = index_path.read_text(encoding="utf-8").strip()
            if index_text:
                context_parts.append("### Context Index\n")
                context_parts.append(index_text)
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Could not read context index: %s", exc)

    # Accumulated wisdom (_learnings.md — principled learnings)
    wisdom_path = Path("context/learnings/_learnings.md")
    if wisdom_path.exists():
        try:
            wisdom_text = wisdom_path.read_text(encoding="utf-8").strip()
            if wisdom_text:
                # Cap at 4000 chars to match synthesis output budget
                if len(wisdom_text) > 4000:
                    wisdom_text = wisdom_text[:4000] + "\n\n... (truncated, see full file)"
                context_parts.append("### Accumulated Wisdom\n")
                context_parts.append(wisdom_text)
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Could not read _learnings.md: %s", exc)

    # Context store decisions
    store_items = []
    for fpath in sorted(glob_mod.glob(str(STORE_DIR / "*.json"))):
        data = safe_read_json(Path(fpath))
        if data is None:
            continue
        key = data.get("key", Path(fpath).stem)
        value = data.get("value", "")
        if key and value:
            store_items.append(f"- {key}: {value}")

    if store_items:
        context_parts.append("### Previous Decisions\n")
        context_parts.extend(store_items[:15])

    # Learnings manifest (separate CRITICAL section, not part of Shared Context)
    learnings_block = ""
    learnings_script = FORJA_TOOLS / "forja_learnings.py"
    if learnings_script.exists():
        try:
            result = subprocess.run(
                [sys.executable, str(learnings_script), "manifest"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            print(f"  {YELLOW}Warning: learnings manifest timed out after 30s{RESET}")
            result = None
        manifest = result.stdout.strip() if result else ""
        if manifest and "No learnings found" not in manifest:
            learnings_block = manifest

    # Business context: company, domains, design-system
    biz_text = gather_context(CONTEXT_DIR, max_chars=load_config().context.max_context_chars)
    if biz_text:
        context_parts.append("\n### Business Context\n")
        context_parts.append(biz_text)

    if not context_parts and not learnings_block:
        print(f"  {DIM}no context to inject{RESET}")
        return

    # Build injection blocks (learnings get their own CRITICAL section)
    context_block = ""
    if learnings_block:
        context_block += "\n## CRITICAL: Previous Run Learnings (auto-generated)\n\n"
        context_block += learnings_block + "\n"
    if context_parts:
        context_block += "\n## Shared Context (auto-generated)\n\n"
        context_block += "\n".join(context_parts) + "\n"

    lines = content.splitlines()
    insert_idx = 1  # After first line (title)
    # Find end of first heading
    for i, line in enumerate(lines):
        if i == 0:
            continue
        if line.startswith("## "):
            insert_idx = i
            break
    else:
        insert_idx = len(lines)

    lines.insert(insert_idx, context_block)
    claude_md.write_text("\n".join(lines), encoding="utf-8")

    items_count = len(store_items)
    has_learnings = bool(learnings_block)
    has_index = index_path.exists()
    summary_parts = []
    if has_index:
        summary_parts.append("_index.md")
    if items_count:
        summary_parts.append(f"{items_count} decisions")
    if has_learnings:
        summary_parts.append("learnings manifest")
    if biz_text:
        summary_parts.append("business context")
    _phase_result(True, f"injected: {', '.join(summary_parts)}")


# ── Observatory Live Mode ─────────────────────────────────────────────

def _start_observatory_live():
    """Launch observatory in live mode as a background process.

    Returns the Popen object or None if launch fails.
    """
    observatory = FORJA_TOOLS / "forja_observatory.py"
    if not observatory.exists():
        return None
    try:
        proc = subprocess.Popen(
            [sys.executable, str(observatory), "live"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"  {DIM}Observatory live: PID {proc.pid}{RESET}")
        return proc
    except (OSError, FileNotFoundError):
        return None


def _stop_observatory_live(proc):
    """Stop the observatory live background process."""
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
        except OSError as exc:
            logger.debug("Could not kill observatory process: %s", exc)
    # Also clean PID file
    pid_file = FORJA_DIR / "observatory-live.pid"
    try:
        pid_file.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove observatory PID file: %s", exc)


# ── Phase 2: Build (Claude Code) ────────────────────────────────────


def _get_timeouts():
    """Return (stall_80pct_seconds, absolute_seconds) from config."""
    cfg = load_config()
    return cfg.build.timeout_stall_minutes * 60, cfg.build.timeout_absolute_minutes * 60


def _monitor_progress(stop_event, start_time, timeout_event=None):
    """Background thread that polls features.json and shows live progress.

    If timeout_event is provided, it will be set when a QA timeout is detected:
    - stall timeout when >80% complete (default 12 min)
    - absolute timeout with no progress (default 20 min)
    """
    timeout_stall, timeout_absolute = _get_timeouts()
    spinner = _spinner_frames()
    teammates_dir = TEAMMATES_DIR
    last_status = {}
    phase = "planning"
    last_completion_time = time.time()
    last_passed_count = 0
    # Per-feature stall tracking
    feature_last_change: dict[str, float] = {}
    feature_stall_warned: set[str] = set()
    feature_stall_blocked: set[str] = set()

    while not stop_event.is_set():
        elapsed = time.time() - start_time
        elapsed_str = _format_duration(elapsed)
        frame = next(spinner)

        teammate_map = TEAMMATES_DIR.parent / "teammate_map.json"
        features_files = list(teammates_dir.glob("*/features.json")) if teammates_dir.exists() else []

        if not teammate_map.exists() and not features_files:
            if phase != "planning":
                phase = "planning"
            sys.stdout.write(f"\r{CYAN}  [{elapsed_str}]{RESET} {DIM}Planning{frame}{RESET}    ")
            sys.stdout.flush()
        else:
            if phase == "planning":
                phase = "building"
                sys.stdout.write(f"\n{GREEN}  Plan created. Building...{RESET}\n")
                sys.stdout.flush()

            total = 0
            passed = 0
            blocked = 0
            done_list = []

            for fj in sorted(teammates_dir.glob("*/features.json")) if teammates_dir.exists() else []:
                teammate_name = fj.parent.name
                data = safe_read_json(fj)
                if data is None:
                    continue
                features = data.get("features", data) if isinstance(data, dict) else data
                if not isinstance(features, list):
                    continue
                for feat_dict in features:
                    feat = Feature.from_dict(feat_dict)
                    key = f"{teammate_name}/{feat.id}"
                    total += 1
                    if feat.status == "blocked":
                        blocked += 1
                        if key not in last_status or last_status[key] != "blocked":
                            done_list.append(
                                f"{teammate_name}/{feat.display_name} [BLOCKED]"
                            )
                        last_status[key] = "blocked"
                    elif feat.status == "passed":
                        passed += 1
                        if key not in last_status or last_status[key] != "passed":
                            done_list.append(
                                f"{teammate_name}/{feat.display_name}"
                            )
                        last_status[key] = "passed"
                    else:
                        last_status[key] = feat.status

                    # Per-feature stall tracking
                    now = time.time()
                    prev = last_status.get(key)
                    if key not in feature_last_change:
                        feature_last_change[key] = now
                    elif prev != feat.status:
                        feature_last_change[key] = now

                    # Check per-feature stall (only for non-terminal features)
                    if feat.status not in ("passed", "blocked"):
                        stale_secs = now - feature_last_change[key]
                        if stale_secs >= 480 and key not in feature_stall_blocked:
                            feature_stall_blocked.add(key)
                            sys.stdout.write(
                                f"\n{RED}{BOLD}  [STALL] Blocking feature {feat.id}"
                                f" - no progress for 8 minutes{RESET}"
                            )
                            sys.stdout.flush()
                            _emit_event("monitor.stall_block", {
                                "feature_id": feat.id, "teammate": teammate_name,
                                "stale_seconds": int(stale_secs),
                            })
                            # Auto-block via forja_features.py attempt
                            tm_dir = str(TEAMMATES_DIR / teammate_name)
                            try:
                                subprocess.run(
                                    [sys.executable,
                                     str(FORJA_TOOLS / "forja_features.py"),
                                     "attempt", feat.id,
                                     "--dir", tm_dir],
                                    capture_output=True, timeout=10,
                                )
                            except (subprocess.TimeoutExpired, OSError) as exc:
                                logger.debug("Auto-block for %s failed: %s", feat.id, exc)
                        elif stale_secs >= 300 and key not in feature_stall_warned:
                            feature_stall_warned.add(key)
                            sys.stdout.write(
                                f"\n{YELLOW}  [STALL] Feature {feat.id}"
                                f" has not progressed in 5 minutes{RESET}"
                            )
                            sys.stdout.flush()
                            _emit_event("monitor.stall_warn", {
                                "feature_id": feat.id, "teammate": teammate_name,
                                "stale_seconds": int(stale_secs),
                            })

            # Track new completions for timeout logic
            resolved = passed + blocked
            if resolved > last_passed_count:
                last_completion_time = time.time()
                last_passed_count = resolved

            for d in done_list:
                if "[BLOCKED]" in d:
                    sys.stdout.write(f"\n{YELLOW}  \u26a0 {d}{RESET}")
                else:
                    sys.stdout.write(f"\n{GREEN}  \u2714 {d}{RESET}")
                sys.stdout.flush()

            if total > 0:
                pct = int((resolved / total) * 100)
                bar_len = 20
                filled = int(bar_len * resolved / total)
                bar = "\u2588" * filled + "\u2591" * (bar_len - filled)
                status = f"{passed}/{total} features ({pct}%)"
                if blocked:
                    status += f" [{blocked} blocked]"
                sys.stdout.write(
                    f"\r{CYAN}  [{elapsed_str}]{RESET} {bar} {status}{frame}   "
                )
                sys.stdout.flush()

                if resolved == total and total > 0:
                    if phase != "finishing":
                        phase = "finishing"
                        msg = "All features resolved"
                        if blocked:
                            msg += f" ({blocked} blocked)"
                        msg += ". Finishing up..."
                        sys.stdout.write(f"\n{GREEN}  {msg}{RESET}\n")
                        sys.stdout.flush()

                # ── Timeout checks ──
                if timeout_event and not timeout_event.is_set():
                    stall_seconds = time.time() - last_completion_time

                    # Stall timeout + >80% complete
                    if pct > 80 and stall_seconds >= timeout_stall:
                        sys.stdout.write(
                            f"\n{RED}{BOLD}  [TIMEOUT] {passed}/{total} features "
                            f"({pct}%) - stalled {int(stall_seconds//60)}min with >80% complete{RESET}\n"
                        )
                        sys.stdout.flush()
                        _emit_event("monitor.timeout", {
                            "passed": passed, "total": total, "pct": pct,
                            "reason": "stall_80pct", "stall_minutes": int(stall_seconds // 60),
                        })
                        timeout_event.set()

                    # Absolute stall timeout
                    elif stall_seconds >= timeout_absolute:
                        sys.stdout.write(
                            f"\n{RED}{BOLD}  [TIMEOUT] {passed}/{total} features "
                            f"({pct}%) - no progress for {int(stall_seconds//60)}min{RESET}\n"
                        )
                        sys.stdout.flush()
                        _emit_event("monitor.timeout", {
                            "passed": passed, "total": total, "pct": pct,
                            "reason": "absolute", "stall_minutes": int(stall_seconds // 60),
                        })
                        timeout_event.set()
            else:
                sys.stdout.write(
                    f"\r{CYAN}  [{elapsed_str}]{RESET} {DIM}Setting up teammates{frame}{RESET}    "
                )
                sys.stdout.flush()

        stop_event.wait(2)


def _count_features():
    """Count total, passed, and blocked features. Returns (total, passed, blocked)."""
    teammates_dir = TEAMMATES_DIR
    total = 0
    passed = 0
    blocked = 0
    for fj in sorted(teammates_dir.glob("*/features.json")) if teammates_dir.exists() else []:
        data = safe_read_json(fj)
        if data is None:
            continue
        features = data.get("features", data) if isinstance(data, dict) else data
        if not isinstance(features, list):
            continue
        for feat_dict in features:
            total += 1
            feat = Feature.from_dict(feat_dict)
            if feat.status == "blocked":
                blocked += 1
            elif feat.status == "passed":
                passed += 1
    return total, passed, blocked


# ── Feedback loop helpers ─────────────────────────────────────────

def _persist_planning_decisions():
    """Save planning decisions to context store for build agents."""
    transcript = FORJA_DIR / "plan-transcript.json"
    if not transcript.exists():
        return
    data = safe_read_json(transcript)
    if not data:
        return

    answers = data.get("answers", [])
    if not answers:
        return

    decisions = []
    for ans in answers[:10]:
        q = ans.get("question", "")
        a = ans.get("answer", "")
        if q and a:
            decisions.append(f"Q: {q}\nA: {a}")

    if not decisions:
        return

    value = "\n---\n".join(decisions)
    if len(value) > 2000:
        value = value[:2000]

    ctx_script = FORJA_TOOLS / "forja_context.py"
    if ctx_script.exists():
        try:
            subprocess.run(
                [sys.executable, str(ctx_script), "set",
                 "planning.decisions", value,
                 "--author", "planner", "--tags", "planning,decisions"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass


def _log_test_failures_as_learnings(test_results: dict):
    """Log test failures as learnings for next iteration."""
    if not test_results or test_results.get("failed", 0) == 0:
        return

    learnings_script = FORJA_TOOLS / "forja_learnings.py"
    if not learnings_script.exists():
        return

    # Extract failure info from test output
    output = test_results.get("output", "")
    failed_count = test_results.get("failed", 0)
    framework = test_results.get("framework", "unknown")

    # Build a concise failure summary
    failure_lines = []
    for line in output.splitlines():
        if "FAILED" in line or "ERROR" in line or "AssertionError" in line:
            failure_lines.append(line.strip()[:150])
        if len(failure_lines) >= 5:
            break

    if not failure_lines:
        failure_lines = [f"{failed_count} test(s) failed in {framework}"]

    for line in failure_lines:
        learning = f"Test failure: {line}"
        try:
            subprocess.run(
                [sys.executable, str(learnings_script), "log",
                 "--category", "error-pattern",
                 "--learning", learning,
                 "--source", "project-tests",
                 "--severity", "high"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass


def _persist_outcome_gaps():
    """Save unmet requirements to context store."""
    outcome = safe_read_json(FORJA_DIR / "outcome-report.json")
    if not outcome:
        return

    unmet = outcome.get("unmet", [])
    if not unmet:
        return

    gaps = []
    for item in unmet[:10]:
        if isinstance(item, str):
            gaps.append(item)
        elif isinstance(item, dict):
            gaps.append(item.get("requirement", str(item)))

    if not gaps:
        return

    value = "\n".join(f"- {g}" for g in gaps)
    ctx_script = FORJA_TOOLS / "forja_context.py"
    if ctx_script.exists():
        try:
            subprocess.run(
                [sys.executable, str(ctx_script), "set",
                 "outcome.unmet_requirements", value,
                 "--author", "runner", "--tags", "outcome,gaps"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass


# ── Phase 4: Project Tests ──────────────────────────────────────────

def _run_project_tests(project_dir: Path) -> dict:
    """Run the generated project's own tests and capture results."""
    results: dict = {"framework": None, "exit_code": -1, "passed": 0, "failed": 0, "output": ""}

    tests_dir = project_dir / "tests"
    package_json = project_dir / "package.json"

    if tests_dir.exists() and list(tests_dir.glob("test_*.py")):
        # Python pytest
        results["framework"] = "pytest"
        try:
            r = subprocess.run(
                ["python3", "-m", "pytest", "-v", "--tb=short"],
                capture_output=True, text=True, cwd=str(project_dir), timeout=120,
            )
            results["exit_code"] = r.returncode
            results["output"] = r.stdout + r.stderr
            for line in r.stdout.splitlines():
                if "passed" in line:
                    m = re.search(r"(\d+) passed", line)
                    if m:
                        results["passed"] = int(m.group(1))
                    m = re.search(r"(\d+) failed", line)
                    if m:
                        results["failed"] = int(m.group(1))
        except subprocess.TimeoutExpired:
            results["output"] = "Test execution timed out after 120s"
        except FileNotFoundError:
            results["output"] = "pytest not found"

    elif package_json.exists():
        # Node npm test
        try:
            pkg = json.loads(package_json.read_text(encoding="utf-8"))
            if "test" in pkg.get("scripts", {}):
                results["framework"] = "npm"
                r = subprocess.run(
                    ["npm", "test"], capture_output=True, text=True,
                    cwd=str(project_dir), timeout=120,
                )
                results["exit_code"] = r.returncode
                results["output"] = r.stdout + r.stderr
        except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("npm test setup failed: %s", exc)

    # Save results
    results_path = project_dir / ".forja" / "test-results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    if results["framework"]:
        passed = results["passed"]
        failed = results["failed"]
        icon = f"{GREEN}✔{RESET}" if failed == 0 and passed > 0 else f"{RED}✘{RESET}"
        print(f"  {icon} Project tests ({results['framework']}): {passed} passed, {failed} failed")
    else:
        print(f"  {DIM}no test suite detected{RESET}")

    return results


# ── Phase 3: Server Health Smoke Test ─────────────────────────────────

def _detect_server_config() -> dict:
    """Detect how to start the built application and which port/URL to check.

    Returns dict with keys: skill, start_cmd, port, health_url, stop_after.
    If the project is a static landing page, start_cmd uses http.server.
    If it's an API backend, start_cmd uses uvicorn.
    """
    from forja.planner import _detect_skill
    skill = _detect_skill()

    if skill == "api-backend":
        # Look for common entry points
        for entry in ("src.main:app", "src.app:app", "main:app", "app:app"):
            module_path = entry.split(":")[0].replace(".", "/") + ".py"
            if Path(module_path).exists():
                return {
                    "skill": skill,
                    "start_cmd": ["python3", "-m", "uvicorn", entry, "--port", "8765", "--host", "0.0.0.0"],
                    "port": 8765,
                    "health_url": "http://localhost:8765/",
                    "health_paths": ["/health", "/docs", "/"],
                    "stop_after": True,
                }
        # Fallback: try common ports without starting
        return {
            "skill": skill,
            "start_cmd": None,
            "port": 8765,
            "health_url": "http://localhost:8765/",
            "health_paths": ["/health", "/docs", "/"],
            "stop_after": False,
        }

    elif skill == "landing-page":
        # Find the directory with index.html
        serve_dir = "."
        for candidate in (".", "dist", "build", "out", "public"):
            if (Path(candidate) / "index.html").exists():
                serve_dir = candidate
                break

        if not (Path(serve_dir) / "index.html").exists():
            return {"skill": skill, "start_cmd": None, "port": 0,
                    "health_url": "", "health_paths": [], "stop_after": False}

        return {
            "skill": skill,
            "start_cmd": ["python3", "-m", "http.server", "8080", "--directory", serve_dir],
            "port": 8080,
            "health_url": "http://localhost:8080/",
            "health_paths": ["/"],
            "stop_after": True,
        }

    else:
        # Custom skill — try to detect common patterns
        if Path("package.json").exists():
            try:
                pkg = json.loads(Path("package.json").read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {})
                if "start" in scripts:
                    return {
                        "skill": "custom-node",
                        "start_cmd": ["npm", "start"],
                        "port": 3000,
                        "health_url": "http://localhost:3000/",
                        "health_paths": ["/"],
                        "stop_after": True,
                    }
            except (json.JSONDecodeError, OSError):
                pass
        return {"skill": skill, "start_cmd": None, "port": 0,
                "health_url": "", "health_paths": [], "stop_after": False}


def _wait_for_port(port: int, timeout: float = 15.0) -> bool:
    """Wait until a TCP port is accepting connections or timeout."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("localhost", port))
            sock.close()
            if result == 0:
                return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def _http_get(url: str, timeout: float = 10.0) -> tuple[int, str]:
    """Make a simple HTTP GET request. Returns (status_code, body_preview).

    Uses urllib only (no external deps). Returns (-1, error_msg) on failure.
    """
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Forja-SmokeTest/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(2048).decode("utf-8", errors="replace")
            return resp.status, body[:500]
    except urllib.error.HTTPError as e:
        return e.code, str(e.reason)[:200]
    except Exception as e:
        return -1, str(e)[:200]


def _http_request(
    method: str, url: str, body: dict | None = None, timeout: float = 10.0,
    headers: dict | None = None,
) -> tuple[int, str]:
    """Make an HTTP request with any method. Returns (status_code, body_text).

    Uses urllib only (no external deps). Returns (-1, error_msg) on failure.
    """
    import urllib.request
    import urllib.error
    hdrs = {"User-Agent": "Forja-Probe/1.0", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(4096).decode("utf-8", errors="replace")
            return resp.status, raw[:1000]
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read(2048).decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = str(e.reason)[:200]
        return e.code, err_body
    except Exception as e:
        return -1, str(e)[:200]


# ── Probe payload generation ──────────────────────────────────────────

_TYPE_DEFAULTS = {
    "str": "test_value",
    "string": "test_value",
    "int": 1,
    "integer": 1,
    "float": 1.0,
    "number": 1.0,
    "bool": True,
    "boolean": True,
}

_FIELD_HEURISTICS = {
    "email": "test@example.com",
    "password": "TestPass123!",
    "name": "Test User",
    "title": "Test Item",
    "description": "Test description",
    "username": "testuser",
    "phone": "+1234567890",
    "url": "https://example.com",
    "address": "123 Test St",
}


def _generate_payload(schema: dict | list) -> dict:
    """Generate a minimal valid JSON payload from a response_schema.

    The schema maps field names to type strings, e.g.
    {"id": "int", "email": "str", "name": "str"}

    We skip "id" fields (auto-generated) and map the rest to test values.
    """
    if isinstance(schema, list):
        # Array schema — use first element
        schema = schema[0] if schema else {}
    if not isinstance(schema, dict):
        return {}

    payload = {}
    for field, typ in schema.items():
        # Skip auto-generated fields
        if field.lower() in ("id", "created_at", "updated_at", "timestamp"):
            continue
        # Check field-name heuristics first
        for hint, value in _FIELD_HEURISTICS.items():
            if hint in field.lower():
                payload[field] = value
                break
        else:
            # Fall back to type-based default
            payload[field] = _TYPE_DEFAULTS.get(str(typ).lower(), "test")
    return payload


# ── Endpoint probing ──────────────────────────────────────────────────

def _read_all_endpoints() -> list[dict]:
    """Read all endpoints from context/teammates/*/validation_spec.json.

    Returns flat list of endpoint dicts, each with an extra 'teammate' key.
    """
    endpoints = []
    spec_pattern = str(TEAMMATES_DIR / "*/validation_spec.json")
    for fpath in sorted(glob_mod.glob(spec_pattern)):
        teammate = Path(fpath).parent.name
        try:
            data = json.loads(Path(fpath).read_text(encoding="utf-8"))
            for ep in data.get("endpoints", []):
                ep["_teammate"] = teammate
                endpoints.append(ep)
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Could not read %s: %s", fpath, exc)
    return endpoints


def _order_probes(endpoints: list[dict]) -> list[dict]:
    """Order probes: POST first (creates resources), then GET, PUT, DELETE.

    This ensures IDs from POST responses can be reused in subsequent probes.
    """
    order = {"POST": 0, "PUT": 2, "PATCH": 2, "GET": 1, "DELETE": 3}
    return sorted(endpoints, key=lambda ep: order.get(ep.get("method", "GET").upper(), 5))


def _run_endpoint_probes(port: int) -> dict:
    """Probe all endpoints from validation_spec.json against a live server.

    Returns trace dict and saves to .forja/runtime-trace.json.
    """
    trace = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "port": port,
        "probes": [],
        "summary": {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0},
    }

    endpoints = _read_all_endpoints()
    if not endpoints:
        print(f"  {DIM}no validation_spec.json endpoints found{RESET}")
        return trace

    ordered = _order_probes(endpoints)
    created_ids: dict[str, int | str] = {}  # base_path → id from POST response
    auth_token: str | None = None
    deadline = time.time() + 60  # 60s total budget for all probes

    print(f"  Probing {len(ordered)} endpoints...")

    for ep in ordered:
        if time.time() > deadline:
            print(f"  {YELLOW}probe timeout (60s), saving partial results{RESET}")
            break

        method = ep.get("method", "GET").upper()
        path = ep.get("path", "/")
        expected_status = ep.get("expected_status", 200)
        schema = ep.get("response_schema", {})

        # Resolve path params like /users/{id}
        resolved_path = path
        if "{" in path:
            # Extract base path to look up created IDs
            base = "/" + path.split("/")[1]  # e.g. /users from /users/{id}
            stored_id = created_ids.get(base, 1)
            # Replace all {param} with the stored ID
            resolved_path = re.sub(r"\{[^}]+\}", str(stored_id), path)

        url = f"http://localhost:{port}{resolved_path}"

        # Generate payload for write methods
        body = None
        if method in ("POST", "PUT", "PATCH"):
            body = _generate_payload(schema)

        # Add auth header if we have a token
        extra_headers = {}
        if auth_token:
            extra_headers["Authorization"] = f"Bearer {auth_token}"

        # Execute probe
        t0 = time.time()
        actual_status, response_text = _http_request(
            method, url, body=body, timeout=10.0, headers=extra_headers if extra_headers else None,
        )
        elapsed_ms = int((time.time() - t0) * 1000)

        # Parse response body
        response_body = None
        try:
            response_body = json.loads(response_text)
        except (json.JSONDecodeError, TypeError):
            pass

        # Check if status matches
        status_match = actual_status == expected_status

        # Check schema match (for successful responses)
        missing_fields = []
        if status_match and response_body and isinstance(response_body, dict) and isinstance(schema, dict):
            for field in schema:
                if field.lower() not in ("id", "created_at", "updated_at", "timestamp"):
                    if field not in response_body:
                        missing_fields.append(field)

        passed = status_match and not missing_fields

        probe_result = {
            "endpoint": path,
            "method": method,
            "expected_status": expected_status,
            "actual_status": actual_status,
            "passed": passed,
            "response_time_ms": elapsed_ms,
            "response_body": response_body if response_body else response_text[:200],
            "status_match": status_match,
            "missing_fields": missing_fields,
        }
        if body:
            probe_result["request_body"] = body
        trace["probes"].append(probe_result)

        # Store created IDs from POST responses for later use
        if method == "POST" and actual_status in (200, 201) and response_body:
            base = "/" + path.strip("/").split("/")[0]  # /users
            if isinstance(response_body, dict):
                for id_field in ("id", "ID", "_id"):
                    if id_field in response_body:
                        created_ids[base] = response_body[id_field]
                        break

            # Check if this is an auth endpoint returning a token
            if isinstance(response_body, dict):
                for token_field in ("access_token", "token", "jwt"):
                    if token_field in response_body:
                        auth_token = response_body[token_field]
                        break

        # Print result
        icon = f"{GREEN}✔{RESET}" if passed else f"{RED}✘{RESET}"
        print(f"  {icon} {method} {path} → {actual_status} (expected {expected_status})")

    # Summary
    total = len(trace["probes"])
    passed_count = sum(1 for p in trace["probes"] if p["passed"])
    failed_count = total - passed_count
    pass_rate = round(passed_count / total * 100, 1) if total > 0 else 0

    trace["summary"] = {
        "total": total,
        "passed": passed_count,
        "failed": failed_count,
        "pass_rate": pass_rate,
        "failed_endpoints": [
            f"{p['method']} {p['endpoint']}" for p in trace["probes"] if not p["passed"]
        ],
    }

    # Save trace
    trace_path = FORJA_DIR / "runtime-trace.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps(trace, indent=2, default=str, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return trace


def _run_cli_qa() -> dict | None:
    """Run CLI QA tests for non-server projects (Python scripts, CLI tools).

    Uses .forja-tools/forja_qa_cli.py to run subprocess-based checks:
      - Entry point exists
      - Syntax check
      - Import check
      - Help/version flag
      - Starts without crash
      - No import errors

    Returns result dict or None if no CLI entry point detected.
    """
    qa_script = FORJA_TOOLS / "forja_qa_cli.py"
    if not qa_script.exists():
        # Try syncing from templates
        template_src = Path(__file__).parent / "templates" / "forja_qa_cli.py"
        if template_src.exists():
            FORJA_TOOLS.mkdir(parents=True, exist_ok=True)
            shutil.copy2(template_src, qa_script)
        else:
            return None

    # Detect if there's a CLI entry point
    entry_point = _detect_entry_point()
    if not entry_point:
        return None

    # Skip if entry point is clearly a web thing
    if entry_point in ("open index.html",) or entry_point.startswith("npm"):
        return None

    print(f"  Running CLI QA for: {BOLD}{entry_point}{RESET}")

    try:
        result = subprocess.run(
            [sys.executable, str(qa_script), entry_point, str(FORJA_DIR)],
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Parse the JSON report
        report_path = FORJA_DIR / "qa-cli-report.json"
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                passed_count = report.get("passed", 0)
                failed_count = report.get("failed", 0)
                total = passed_count + failed_count

                all_passed = failed_count == 0
                summary = f"CLI QA: {passed_count}/{total} checks passed"

                if all_passed:
                    _phase_result(True, f"{GREEN}{summary}{RESET}")
                else:
                    _phase_result(False, f"{RED}{summary}{RESET}")
                    # Show which tests failed
                    for test in report.get("tests", []):
                        if not test.get("passed"):
                            print(f"    {RED}✘{RESET} {test['name']}: {test.get('detail', '')[:80]}")

                return {
                    "passed": all_passed,
                    "summary": summary,
                    "tests": report.get("tests", []),
                    "entry_point": entry_point,
                }
            except (json.JSONDecodeError, OSError) as exc:
                logger.debug("Failed to parse CLI QA report: %s", exc)

        # Fallback: use exit code
        passed = result.returncode == 0
        return {
            "passed": passed,
            "summary": f"CLI QA {'passed' if passed else 'failed'} (exit {result.returncode})",
            "entry_point": entry_point,
        }

    except subprocess.TimeoutExpired:
        print(f"  {YELLOW}CLI QA timed out after 120s{RESET}")
        return {"passed": False, "summary": "CLI QA timed out", "entry_point": entry_point}
    except Exception as exc:
        logger.debug("CLI QA error: %s", exc)
        return None


def _run_smoke_test() -> dict:
    """Run server health smoke test: start the app, verify HTTP responses.

    Returns a result dict saved to .forja/smoke-test.json:
    {
        "passed": bool,
        "skill": str,
        "server_started": bool,
        "port": int,
        "checks": [{"path": "/", "status": 200, "ok": true}, ...],
        "summary": str,
        "error": str | None
    }
    """
    result = {
        "passed": False,
        "skill": "unknown",
        "server_started": False,
        "port": 0,
        "checks": [],
        "summary": "",
        "error": None,
    }

    # Detect how to start the server
    config = _detect_server_config()
    result["skill"] = config["skill"]
    result["port"] = config.get("port", 0)

    if not config.get("start_cmd") and not config.get("port"):
        # No server detected — run CLI QA instead of skipping
        cli_qa_result = _run_cli_qa()
        if cli_qa_result:
            result["passed"] = cli_qa_result.get("passed", False)
            result["summary"] = cli_qa_result.get("summary", "CLI QA ran")
            result["cli_qa"] = cli_qa_result
        else:
            result["summary"] = "No server configuration detected"
            result["passed"] = True  # Non-server, non-CLI projects pass by default
            print(f"  {DIM}skipped (no server to test for {config['skill']}){RESET}")
        _save_smoke_result(result)
        return result

    server_proc = None
    try:
        # 1. Start the server if we have a start command
        if config["start_cmd"]:
            print(f"  Starting server: {' '.join(config['start_cmd'][:4])}...")
            try:
                server_proc = subprocess.Popen(
                    config["start_cmd"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid,
                )
            except FileNotFoundError as e:
                result["error"] = f"Cannot start server: {e}"
                result["summary"] = "Server start command not found"
                _save_smoke_result(result)
                _phase_result(False, result["summary"])
                return result

        # 2. Wait for port to become available
        port = config["port"]
        print(f"  Waiting for port {port}...")
        port_ok = _wait_for_port(port, timeout=20.0)

        if not port_ok:
            # Check if process died
            if server_proc and server_proc.poll() is not None:
                stderr = ""
                try:
                    _, stderr_bytes = server_proc.communicate(timeout=2)
                    stderr = stderr_bytes.decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                result["error"] = f"Server exited with code {server_proc.returncode}: {stderr}"
                result["summary"] = f"Server crashed on startup (exit {server_proc.returncode})"
            else:
                result["error"] = f"Port {port} not responding after 20s"
                result["summary"] = f"Server did not start (port {port} timeout)"
            _save_smoke_result(result)
            _phase_result(False, result["summary"])
            return result

        result["server_started"] = True
        print(f"  {GREEN}✔{RESET} Server is listening on port {port}")

        # 3. Make HTTP health checks
        all_ok = True
        for path in config.get("health_paths", ["/"]):
            url = f"http://localhost:{port}{path}"
            status, body = _http_get(url)
            check = {"path": path, "status": status, "ok": 200 <= status < 500}
            result["checks"].append(check)

            if check["ok"]:
                print(f"  {GREEN}✔{RESET} GET {path} → {status}")
            else:
                print(f"  {RED}✘{RESET} GET {path} → {status}")
                all_ok = False

        # At least one check must return 2xx
        any_2xx = any(200 <= c["status"] < 300 for c in result["checks"])
        result["passed"] = result["server_started"] and any_2xx

        if result["passed"]:
            ok_count = sum(1 for c in result["checks"] if c["ok"])
            result["summary"] = f"Server healthy: {ok_count}/{len(result['checks'])} endpoints OK"
        else:
            fail_count = sum(1 for c in result["checks"] if not c["ok"])
            result["summary"] = f"Server responding but {fail_count} endpoint(s) failed"

        # 4. Endpoint probes — exercise all validation_spec.json endpoints
        if result["server_started"]:
            try:
                probe_trace = _run_endpoint_probes(port)
                result["probe_summary"] = probe_trace.get("summary", {})
            except Exception as exc:
                logger.debug("Endpoint probes failed: %s", exc)
                result["probe_summary"] = {"total": 0, "passed": 0, "error": str(exc)[:200]}

    except Exception as e:
        result["error"] = str(e)[:300]
        result["summary"] = f"Smoke test error: {str(e)[:100]}"

    finally:
        # 4. Stop the server if we started it
        if server_proc and config.get("stop_after"):
            try:
                os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)
                server_proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(server_proc.pid), signal.SIGKILL)
                except Exception:
                    pass

    _save_smoke_result(result)
    return result


def _save_smoke_result(result: dict) -> Path:
    """Save smoke test result to .forja/smoke-test.json."""
    out_path = FORJA_DIR / "smoke-test.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


# ── Phase 6: Outcome Evaluation ──────────────────────────────────────

def _run_outcome(prd_path):
    """Run outcome evaluation. Informational, never blocks."""
    outcome_script = FORJA_TOOLS / "forja_outcome.py"
    if not outcome_script.exists():
        print(f"  {DIM}skipped (forja_outcome.py not found){RESET}")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(outcome_script), "--prd", str(prd_path), "--output", "json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(f"  {YELLOW}Warning: outcome evaluation timed out after 120s, continuing{RESET}")
        return

    # Try to parse coverage from JSON output
    try:
        # JSON output is the last non-empty lines after "Evaluating..." header
        for line in reversed(result.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                coverage = data.get("coverage", "?")
                met = len(data.get("met", []))
                unmet = len(data.get("unmet", []))
                color = GREEN if coverage >= 80 else RED
                _phase_result(
                    coverage >= 80,
                    f"{color}{coverage}% coverage{RESET} ({met} met, {unmet} unmet)",
                )
                return
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug("Failed to parse outcome coverage: %s", exc)

    # Fallback: just show exit code
    if result.returncode == 0:
        _phase_result(True, "evaluation complete")
    else:
        _phase_result(False, "evaluation found gaps (see .forja/outcome-report.json)")


# ── Phase 5: Visual Evaluation ───────────────────────────────────────

def _run_visual_eval(prd_path):
    """Run visual evaluation against screenshots. Informational, never blocks."""
    visual_script = FORJA_TOOLS / "forja_visual_eval.py"
    if not visual_script.exists():
        print(f"  {DIM}skipped (forja_visual_eval.py not found){RESET}")
        return

    # Check if screenshots exist before invoking
    ss_dir = FORJA_DIR / "screenshots"
    if not ss_dir.exists() or not any(ss_dir.glob("*.png")):
        print(f"  {DIM}skipped (no screenshots found){RESET}")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(visual_script), "--prd", str(prd_path), "--output", "json"],
            capture_output=True,
            text=True,
            timeout=180,  # Vision API calls can take longer
        )
    except subprocess.TimeoutExpired:
        print(f"  {YELLOW}Warning: visual evaluation timed out after 180s, continuing{RESET}")
        return

    # Try to parse score from JSON output
    try:
        for line in reversed(result.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                score = data.get("score", "?")
                color = GREEN if isinstance(score, (int, float)) and score >= 70 else RED
                _phase_result(
                    isinstance(score, (int, float)) and score >= 70,
                    f"{color}{score}/100 visual score{RESET}",
                )
                return
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug("Failed to parse visual eval score: %s", exc)

    # Fallback: show exit code
    if result.returncode == 0:
        _phase_result(True, "visual evaluation complete")
    else:
        _phase_result(False, "visual issues found (see .forja/visual-eval.json)")


# ── Phase 7: Extract Learnings ───────────────────────────────────────

def _run_learnings_extract():
    """Run learnings extraction. Informational, never blocks."""
    learnings_script = FORJA_TOOLS / "forja_learnings.py"
    if not learnings_script.exists():
        print(f"  {DIM}skipped (forja_learnings.py not found){RESET}")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(learnings_script), "extract"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print(f"  {YELLOW}Warning: learnings extraction timed out after 60s, continuing{RESET}")
        return

    # Count extracted from output
    output = result.stdout.strip()
    if "Extracted" in output:
        # Extract the number
        for line in output.splitlines():
            if "Extracted" in line:
                print(f"  {line.strip()}")
                return
    elif "No learnings" in output:
        print(f"  {DIM}no new learnings to extract{RESET}")
    else:
        print(f"  {DIM}extraction complete{RESET}")


# ── Phase 7b: Apply Learnings to Context ──────────────────────────────

def _run_learnings_apply():
    """Apply extracted learnings to context files. Informational, never blocks."""
    learnings_script = FORJA_TOOLS / "forja_learnings.py"
    if not learnings_script.exists():
        return

    try:
        result = subprocess.run(
            [sys.executable, str(learnings_script), "apply"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print(f"  {YELLOW}Warning: learnings apply timed out after 30s, continuing{RESET}")
        return

    output = result.stdout.strip()
    if "Applied" in output:
        for line in output.splitlines():
            if "Applied" in line:
                print(f"  {line.strip()}")
                return
    elif output:
        print(f"  {DIM}{output.splitlines()[-1].strip()}{RESET}")


# ── Phase 7c: Synthesize Learnings into Wisdom File ──────────────────

def _run_learnings_synthesize():
    """Synthesize JSONL learnings into context/learnings/_learnings.md."""
    learnings_script = FORJA_TOOLS / "forja_learnings.py"
    if not learnings_script.exists():
        return

    try:
        result = subprocess.run(
            [sys.executable, str(learnings_script), "synthesize"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print(f"  {YELLOW}Warning: learnings synthesize timed out{RESET}")
        return

    output = result.stdout.strip()
    if output and "Synthesized" in output:
        for line in output.splitlines():
            if "Synthesized" in line or "wisdom" in line.lower():
                print(f"  {line.strip()}")
                return
    elif output:
        last = output.splitlines()[-1].strip()
        if last:
            print(f"  {DIM}{last}{RESET}")


# ── Iteration Snapshots ──────────────────────────────────────────────

ITERATIONS_DIR = FORJA_DIR / "iterations"


def _next_iteration_number() -> int:
    """Count existing v* dirs + 1."""
    if not ITERATIONS_DIR.exists():
        return 1
    existing = sorted(ITERATIONS_DIR.glob("v*"))
    return len(existing) + 1


def _save_iteration_snapshot(
    run_number: int,
    feedback_text: str,
    old_prd: str,
    new_prd: str,
) -> Path | None:
    """Save iteration metadata, feedback, and PRD diff.

    Returns the snapshot directory path or None on failure.
    """
    snapshot_dir = ITERATIONS_DIR / f"v{run_number:03d}"
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"  {YELLOW}Warning: could not create iteration snapshot: {exc}{RESET}")
        return None

    # ── manifest.json ──
    total, passed, blocked = _count_features()
    outcome_coverage = 0
    outcome_path = FORJA_DIR / "outcome-report.json"
    if outcome_path.exists():
        data = safe_read_json(outcome_path)
        if data:
            outcome_coverage = data.get("coverage", 0)

    learnings_count = 0
    for fpath in glob_mod.glob("context/learnings/*.jsonl"):
        try:
            learnings_count += sum(
                1 for line in Path(fpath).read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except OSError:
            pass

    manifest = {
        "run_number": run_number,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "features": {"total": total, "passed": passed, "blocked": blocked},
        "outcome_coverage": outcome_coverage,
        "learnings_count": learnings_count,
        "prd_hash": hashlib.md5(new_prd.encode("utf-8")).hexdigest()[:12],
    }
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # ── feedback.md ──
    (snapshot_dir / "feedback.md").write_text(
        f"# Iteration {run_number} Feedback\n\n{feedback_text}\n",
        encoding="utf-8",
    )

    # ── prd-diff.md ──
    diff_lines = list(difflib.unified_diff(
        old_prd.splitlines(keepends=True),
        new_prd.splitlines(keepends=True),
        fromfile="prd-before.md",
        tofile="prd-after.md",
    ))
    if diff_lines:
        diff_text = "".join(diff_lines)
    else:
        diff_text = "(no changes)"
    (snapshot_dir / "prd-diff.md").write_text(
        f"# PRD Changes — Iteration {run_number}\n\n```diff\n{diff_text}\n```\n",
        encoding="utf-8",
    )

    print(f"  {GREEN}Iteration snapshot saved: {snapshot_dir}{RESET}")
    return snapshot_dir


# ── Phase 8: Observatory ─────────────────────────────────────────────

def _run_observatory():
    """Run observatory report. Informational, never blocks."""
    observatory_script = FORJA_TOOLS / "forja_observatory.py"
    if not observatory_script.exists():
        print(f"  {DIM}skipped (forja_observatory.py not found){RESET}")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(observatory_script), "report", "--no-open"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print(f"  {YELLOW}Warning: observatory report timed out after 60s, continuing{RESET}")
        return

    # Show dashboard path
    output = result.stdout.strip()
    for line in output.splitlines():
        if "evals.html" in line or "index.html" in line or "Dashboard:" in line:
            print(f"  {line.strip()}")

    if result.returncode == 0:
        if not any(k in output for k in ("evals.html", "index.html", "Dashboard:")):
            print(f"  {DIM}report generated{RESET}")
    else:
        print(f"  {DIM}report generation failed (non-blocking){RESET}")


# ── Iteration changelog ──────────────────────────────────────────────

def _save_iteration_log(pipeline_start: float, total: int, passed: int,
                        blocked: int, build_elapsed: float) -> None:
    """Save a markdown changelog to .forja/iterations/run-{N}.md."""
    iterations_dir = FORJA_DIR / "iterations"
    iterations_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(iterations_dir.glob("run-*.md"))
    run_num = len(existing) + 1

    total_elapsed = time.time() - pipeline_start
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    failed = total - passed - blocked
    pct = int(passed / total * 100) if total > 0 else 0

    lines = [
        f"# Run #{run_num}",
        f"",
        f"- **Timestamp:** {ts}",
        f"- **Duration:** {_format_duration(int(total_elapsed))}",
        f"- **Build time:** {_format_duration(int(build_elapsed))}",
        f"- **Features:** {passed}/{total} passed, {failed} failed, {blocked} blocked",
        f"- **Pass rate:** {pct}%",
    ]

    # Delta vs previous run
    if existing:
        try:
            prev_text = existing[-1].read_text(encoding="utf-8")
            m = re.search(r"(\d+)/(\d+) passed", prev_text)
            if m:
                prev_passed = int(m.group(1))
                lines.append(f"- **Delta:** {passed - prev_passed:+d} features vs run #{run_num - 1}")
        except OSError:
            pass

    # Outcome coverage
    outcome_path = FORJA_DIR / "outcome-report.json"
    if outcome_path.exists():
        try:
            oc = json.loads(outcome_path.read_text(encoding="utf-8"))
            lines.append(f"- **Outcome coverage:** {oc.get('coverage', '?')}%")
        except (json.JSONDecodeError, OSError):
            pass

    # Learnings count
    learnings_dir = Path("context/learnings")
    if learnings_dir.is_dir():
        count = 0
        for lf in learnings_dir.glob("*.jsonl"):
            try:
                count += sum(1 for ln in lf.read_text(encoding="utf-8").splitlines() if ln.strip())
            except OSError:
                pass
        if count:
            lines.append(f"- **Learnings:** {count} total")

    path = iterations_dir / f"run-{run_num}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  {DIM}iteration log: {path}{RESET}")


# ── Iteration expert panel ────────────────────────────────────────────


def _read_iteration_changelogs() -> str:
    """Read iteration changelogs from .forja/iterations/run-*.md.

    Returns formatted string with changelogs (most recent first),
    truncated to ~3000 chars to stay within prompt budget.
    """
    iterations_dir = FORJA_DIR / "iterations"
    if not iterations_dir.is_dir():
        return ""

    logs = sorted(iterations_dir.glob("run-*.md"), reverse=True)
    if not logs:
        return ""

    parts: list[str] = []
    total_chars = 0
    max_chars = 3000

    for log_path in logs:
        try:
            text = log_path.read_text(encoding="utf-8").strip()
            if total_chars + len(text) > max_chars:
                remaining = len(logs) - len(parts)
                if remaining > 0:
                    parts.append(f"... ({remaining} earlier runs truncated)")
                break
            parts.append(text)
            total_chars += len(text)
        except OSError:
            continue

    return "\n\n---\n\n".join(parts) if parts else ""


def _run_iteration_expert_panel(
    prd_text: str,
    iteration_context: str,
    user_feedback: str,
) -> str:
    """Run an expert panel review for the iteration feedback loop.

    Assembles context (company info, changelogs, build results, user feedback),
    then runs a single round of expert Q&A focused on iteration improvement.

    Returns enriched feedback combining the original user feedback with
    expert panel transcript.  Falls back to *user_feedback* on failure.
    """
    from forja.planner import _run_expert_qa, _PANEL_JSON_SCHEMA

    print(f"\n{BOLD}  === Expert Panel: Iteration Review ==={RESET}")

    # 1. Gather company context
    company_context = gather_context(CONTEXT_DIR, max_chars=2000)

    # 2. Read iteration changelogs
    changelogs = _read_iteration_changelogs()

    # 3. Compose the full context for the panel
    context_parts: list[str] = []
    if company_context:
        context_parts.append(f"## Company & Business Context\n{company_context}")
    if changelogs:
        context_parts.append(f"## Iteration Changelogs (most recent first)\n{changelogs}")
    context_parts.append(f"## Build Results\n{iteration_context}")
    context_parts.append(f"## User Feedback\n{user_feedback}")

    # Include accumulated decision history so experts know what's already decided
    decision_history = _load_decision_history()
    if decision_history:
        history_text = "## Previous Decisions (already applied — do not re-litigate)\n"
        for h in decision_history[-15:]:
            history_text += (
                f"- [iter {h.get('iteration', '?')}] [{h.get('type', '?').upper()}] "
                f"{h.get('target', '?')}: {h.get('decision', '')[:100]}\n"
            )
        context_parts.append(history_text)

    full_context = "\n\n".join(context_parts)

    # 4. Build iteration-specific panel prompt
    iteration_prompt = (
        "You are a conductor of expertise bringing together ITERATION REVIEW experts "
        "to analyze a software PRD that has gone through one or more build attempts.\n\n"
        "The project has already been built at least once. Some features passed, some failed. "
        "Your experts must analyze WHY features failed and recommend concrete PRD ENRICHMENTS.\n\n"
        "Generate 2-3 experts for this iteration review. Focus on:\n"
        "- Build failure diagnosis: why did specific features fail? What detail was missing?\n"
        "- PRD enrichment: what specificity, acceptance criteria, or constraints should be ADDED?\n"
        "- Dependency issues: what build constraints should be documented?\n"
        "- Implementation detail: what file structures, function signatures, or data formats should be specified?\n\n"
        "Each expert should:\n"
        "1. Review the build results and failed features\n"
        "2. Ask ONE critical question about what DETAIL to ADD to the PRD\n"
        "3. Suggest a concrete default that ADDS specificity (not removes features)\n\n"
        "CRITICAL: The PRD should GROW with each iteration. When a feature failed, "
        "the first response is to ADD detail about HOW it should be built — not to remove it. "
        "Only recommend descoping when something is fundamentally impossible to build "
        "in this environment (e.g., requires Docker, system packages, etc.).\n\n"
        "CRITICAL: Default answers must be concrete ADDITIONS to the PRD, not vague advice.\n\n"
        + _PANEL_JSON_SCHEMA
    )

    # 5. Extract PRD title
    prd_lines = prd_text.split("\n")
    prd_title = prd_lines[0].lstrip("# ").strip() if prd_lines else "Unknown"

    iteration_guidance = (
        "This is an ITERATION REVIEW, not initial planning. The project has been built "
        "at least once. Focus on:\n"
        "- What DETAIL is missing from the PRD that caused features to fail?\n"
        "- What acceptance criteria, edge cases, or implementation notes should be ADDED?\n"
        "- What constraints or data structures should be explicitly documented?\n"
        "- What specific file paths, function signatures, or interfaces should be defined?\n"
        "Do NOT suggest adding NEW features. Focus on making EXISTING features more "
        "detailed and specific so they build correctly. The PRD should GROW in richness."
    )

    # 6. Run the expert panel (reusing planner's engine)
    try:
        _experts, _questions, qa_transcript, research_log, _assessment = _run_expert_qa(
            prompt_template=iteration_prompt,
            fallback_experts=FALLBACK_ITERATION_EXPERTS,
            fallback_questions=FALLBACK_ITERATION_QUESTIONS,
            prd_content=prd_text,
            prd_title=prd_title,
            context=full_context,
            skill_guidance=iteration_guidance,
            round_label="ITERATION",
            max_questions=6,
            ensure_tech=True,
            ensure_design=False,
        )
    except Exception as exc:
        logger.debug("Iteration expert panel failed: %s", exc)
        print(f"  {YELLOW}Expert panel error: {exc}{RESET}")
        print(f"  {DIM}Continuing with your feedback.{RESET}")
        return user_feedback

    # 7. Format transcript into enriched feedback
    if not qa_transcript:
        return user_feedback

    enriched_parts = [f"User feedback: {user_feedback}", "", "Expert panel recommendations:"]
    for entry in qa_transcript:
        enriched_parts.append(
            f"- [{entry['expert']}] Q: {entry['question']}\n"
            f"  A: {entry['answer']} (source: {entry['tag']})"
        )

    if research_log:
        enriched_parts.append("\nResearch findings:")
        for r in research_log:
            enriched_parts.append(f"- {r['topic']}: {r['findings'][:200]}")

    return "\n".join(enriched_parts)


def _run_tech_stack_panel(
    prd_text: str,
    iteration_context: str,
    user_feedback: str,
) -> str:
    """Run a tech-focused expert panel to analyze stack decisions and build failures.

    Unlike the iteration panel (product-focused), this panel focuses on:
    - Technology choices that caused failures
    - Dependency replacements (system deps → pip/npm alternatives)
    - Framework patterns and project structure

    Findings are **persisted** to ``context/company/tech-stack.md`` so they
    accumulate across iterations and are automatically injected into future builds
    via ``gather_context()``.

    Returns a tech findings string to enrich the PRD improvement prompt.
    Returns empty string on failure (non-blocking).
    """
    from forja.planner import _run_expert_qa, _PANEL_JSON_SCHEMA

    print(f"\n{BOLD}  === Expert Panel: Tech Stack Review ==={RESET}")

    # 1. Gather company context
    company_context = gather_context(CONTEXT_DIR, max_chars=2000)

    # 2. Read existing tech context files (cumulative)
    existing_stack: list[str] = []
    for fname in ("tech-stack.md", "tech-standards.md", "build-constraints.md"):
        fpath = Path("context/company") / fname
        if fpath.exists():
            try:
                content = fpath.read_text(encoding="utf-8").strip()
                if content:
                    existing_stack.append(f"### {fname}\n{content[:1500]}")
            except OSError:
                pass

    # 3. Compose full context for the panel
    context_parts: list[str] = []
    if company_context:
        context_parts.append(f"## Company & Business Context\n{company_context}")
    if existing_stack:
        context_parts.append(
            f"## Existing Tech Context (from previous builds)\n"
            + "\n\n".join(existing_stack)
        )
    context_parts.append(f"## Build Results\n{iteration_context}")
    context_parts.append(f"## User Feedback\n{user_feedback}")
    full_context = "\n\n".join(context_parts)

    # 4. Build tech-specific panel prompt
    tech_prompt = (
        "You are a conductor of TECHNICAL expertise analyzing build artifacts "
        "and stack decisions for a software project.\n\n"
        "The project has been built at least once. Some features passed, some failed. "
        "Your experts must analyze the TECHNOLOGY ROOT CAUSES of failures — not product "
        "scope or feature prioritization.\n\n"
        "Focus EXCLUSIVELY on:\n"
        "- Which technology choices caused build failures (framework, language, database)\n"
        "- Which dependencies are unbuildable and need pip/npm-installable replacements\n"
        "- Framework-specific assumptions that broke in the Claude Code sandbox\n"
        "- Project structure and file layout issues\n"
        "- Integration patterns (API contracts, CORS, env variables)\n\n"
        "DO NOT discuss:\n"
        "- Product scope, feature prioritization, or business decisions\n"
        "- PRD rewriting or requirement changes\n"
        "- User experience or design\n\n"
        "Generate 2-3 experts for this tech stack review.\n\n"
        "Each expert should:\n"
        "1. Review the build failures through a TECHNOLOGY lens\n"
        "2. Ask ONE specific question about stack/dependency/architecture issues\n"
        "3. Suggest a concrete default answer with exact package names, file paths, "
        "or configuration values\n\n"
        "CRITICAL: Be specific. Name exact packages, versions, file paths, "
        "configuration keys. Vague advice like 'use a simpler approach' is useless.\n\n"
        "CRITICAL: Your findings will be persisted as tech context for future builds. "
        "Write them as constraints and patterns, not suggestions.\n\n"
        + _PANEL_JSON_SCHEMA
    )

    # 5. Extract PRD title
    prd_lines = prd_text.split("\n")
    prd_title = prd_lines[0].lstrip("# ").strip() if prd_lines else "Unknown"

    tech_guidance = (
        "This is a TECH STACK REVIEW, not a product review. The project has been built "
        "at least once. Focus on:\n"
        "- Technology root causes of build failures\n"
        "- Dependency mapping: system deps → pip/npm alternatives\n"
        "- Framework conventions and project structure\n"
        "- Integration patterns and configuration\n"
        "Do NOT discuss product scope, feature priority, or business decisions."
    )

    # 6. Run the expert panel
    try:
        _experts, _questions, qa_transcript, research_log, _assessment = _run_expert_qa(
            prompt_template=tech_prompt,
            fallback_experts=FALLBACK_TECH_EXPERTS,
            fallback_questions=FALLBACK_TECH_QUESTIONS,
            prd_content=prd_text,
            prd_title=prd_title,
            context=full_context,
            skill_guidance=tech_guidance,
            round_label="TECH",
            max_questions=6,
            ensure_tech=True,
            ensure_design=False,
        )
    except Exception as exc:
        logger.debug("Tech stack panel failed: %s", exc)
        print(f"  {YELLOW}Tech panel error: {exc}{RESET}")
        print(f"  {DIM}Continuing without tech findings.{RESET}")
        return ""

    if not qa_transcript:
        return ""

    # 7. Persist findings to context/company/tech-stack.md
    _persist_tech_findings(qa_transcript, research_log)

    # 8. Format transcript into tech enrichment string
    tech_parts = ["Tech stack panel findings:"]
    for entry in qa_transcript:
        tech_parts.append(
            f"- [{entry['expert']}] Q: {entry['question']}\n"
            f"  A: {entry['answer']} (source: {entry['tag']})"
        )

    if research_log:
        tech_parts.append("\nTech research findings:")
        for r in research_log:
            tech_parts.append(f"- {r['topic']}: {r['findings'][:200]}")

    return "\n".join(tech_parts)


def _persist_tech_findings(
    qa_transcript: list[dict],
    research_log: list[dict],
) -> None:
    """Persist tech stack findings to context/company/tech-stack.md.

    Appends new findings with timestamps. Creates the file if it doesn't exist.
    Existing content is preserved — findings are cumulative across iterations.
    """
    tech_file = Path("context/company/tech-stack.md")

    try:
        tech_file.parent.mkdir(parents=True, exist_ok=True)

        ts = time.strftime("%Y-%m-%d")

        # Read existing content
        existing = ""
        if tech_file.exists():
            existing = tech_file.read_text(encoding="utf-8").strip()

        # Build new findings section
        new_lines: list[str] = []
        for entry in qa_transcript:
            # Classify finding by expert type
            answer = entry["answer"].strip()
            if len(answer) > 300:
                answer = answer[:300] + "..."
            new_lines.append(f"- [{ts}] ({entry['expert']}) {answer}")

        if research_log:
            for r in research_log:
                findings = r["findings"].strip()
                if len(findings) > 200:
                    findings = findings[:200] + "..."
                new_lines.append(f"- [{ts}] (research: {r['topic']}) {findings}")

        if not new_lines:
            return

        findings_block = "\n".join(new_lines)

        if existing:
            # Append to existing file
            updated = existing + f"\n\n## Iteration findings ({ts})\n{findings_block}\n"
        else:
            # Create new file with header
            updated = (
                "# Tech Stack Context\n\n"
                "Auto-generated by Forja tech stack expert panel.\n"
                "These findings are injected into future builds via context injection.\n\n"
                f"## Iteration findings ({ts})\n{findings_block}\n"
            )

        tech_file.write_text(updated, encoding="utf-8")
        print(f"  {GREEN}Tech findings saved: {tech_file}{RESET}")

    except OSError as exc:
        logger.debug("Failed to persist tech findings: %s", exc)


# ── Main pipeline ────────────────────────────────────────────────────

def _acquire_pid_lock() -> bool:
    """Write PID file and check for concurrent runs.

    Returns True if lock acquired, False if another instance is running.
    """
    pid_file = FORJA_DIR / "runner.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text(encoding="utf-8").strip())
            # Check if that PID is still alive
            os.kill(old_pid, 0)
            # Process is running
            print(f"{RED}  Another Forja build is running (PID {old_pid}). Abort.{RESET}")
            return False
        except (ValueError, OSError):
            # PID file is stale (process not running or invalid) — remove it
            pass

    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_pid_lock():
    """Remove the PID file."""
    pid_file = FORJA_DIR / "runner.pid"
    try:
        pid_file.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove runner PID file: %s", exc)


def _auto_open_output():
    """Open the observatory dashboard and project output in the browser.

    Smart detection: for CLI/Python projects, only opens the observatory
    and shows the run command in the terminal.  For web projects, opens
    the HTML output too.
    """
    import socket
    import webbrowser

    from forja.planner import _detect_skill

    # Prefer multi-run index dashboard when available, else latest run
    observatory_index = FORJA_DIR / "observatory" / "index.html"
    observatory_html = FORJA_DIR / "observatory" / "evals.html"
    if observatory_index.exists():
        webbrowser.open(f"file://{observatory_index.resolve()}")
        print(f"\n  {GREEN}Observatory dashboard opened{RESET}")
    elif observatory_html.exists():
        webbrowser.open(f"file://{observatory_html.resolve()}")
        print(f"\n  {GREEN}Observatory dashboard opened{RESET}")

    # Detect entry point to decide what to open
    entry_point = _detect_entry_point()
    skill = _detect_skill()

    if skill == "landing-page":
        # Search common landing page output paths
        candidates = [
            Path("index.html"),
            *Path(".").rglob("index.html"),
        ]
        # Filter out node_modules and hidden dirs
        for candidate in candidates:
            parts = candidate.parts
            if any(p.startswith(".") or p == "node_modules" for p in parts):
                continue
            url = f"file://{candidate.resolve()}"
            print(f"  {GREEN}Opening landing page in browser...{RESET}")
            webbrowser.open(url)
            return

    elif skill == "api-backend":
        for port in (8765, 8000, 3000):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                if sock.connect_ex(("localhost", port)) == 0:
                    url = f"http://localhost:{port}/docs"
                    print(f"  {GREEN}Opening API docs: {url}{RESET}")
                    webbrowser.open(url)
                    return
            except OSError:
                pass
            finally:
                sock.close()
        print(f"\n  {DIM}API built. Start with: python3 -m uvicorn src.main:app --port 8765{RESET}")
        print(f"  {DIM}Then visit: http://localhost:8765/docs{RESET}")
        return

    else:
        # Smart detection: only open HTML if the project IS a web project
        # For CLI apps (main.py, app.py, etc.), don't open random HTML
        has_web_output = False
        for name in ("index.html", "dist/index.html", "build/index.html",
                      "out/index.html", "public/index.html"):
            p = Path(name)
            if p.exists():
                has_web_output = True
                url = f"file://{p.resolve()}"
                print(f"  {GREEN}Opening {name} in browser...{RESET}")
                webbrowser.open(url)
                return

        # CLI project: don't try to open HTML, just show how to run
        if not has_web_output and entry_point:
            print(f"\n  {CYAN}This is a terminal project. Run it with:{RESET}")
            print(f"    {BOLD}{entry_point}{RESET}")
            return


def run_forja(prd_path: str | None = None, *, preserve_build: bool = False) -> bool:
    """Run the full Forja pipeline: init → plan → build.

    One command to rule them all.  Missing scaffolding is created
    automatically (``run_init``), a placeholder PRD triggers the
    interactive planner (``run_plan``), and then the build pipeline
    runs to completion.

    Args:
        preserve_build: If True, keep existing src/ and context/teammates/
            so Claude Code iterates on existing code instead of rebuilding
            from scratch.  Used by ``forja iterate``.
    """
    # ── PID lock: prevent concurrent runs ──
    if not _acquire_pid_lock():
        return False

    try:
        return _run_forja_inner(prd_path, preserve_build=preserve_build)
    finally:
        _release_pid_lock()


def _run_forja_inner(prd_path: str | None = None, *, preserve_build: bool = False) -> bool:
    """Inner pipeline logic, wrapped by run_forja for PID lock management."""
    # ── Auto-init if not a Forja project yet ──
    if not CLAUDE_MD.exists() or not FORJA_TOOLS.exists():
        print(f"\n{BOLD}{CYAN}  No Forja project detected — initializing...{RESET}\n")
        from forja.init import run_init  # local import to avoid circular dep

        init_ok = run_init(directory=".")
        if not init_ok:
            print(f"{RED}  Initialization failed.{RESET}")
            return False

        # Verify init actually created the project markers
        if not CLAUDE_MD.exists() or not FORJA_TOOLS.exists():
            print(f"{RED}  Error: Init ran but project markers are missing.{RESET}")
            return False

        print(f"\n{GREEN}{BOLD}  ✔ Project initialized.{RESET}\n")

    # ── Silent template sync (ensures observatory + tools are up-to-date) ──
    try:
        from forja.init import get_template, TEMPLATES
        for src_name, dest_rel in TEMPLATES:
            dest = Path(dest_rel)
            if dest.exists():
                content = get_template(src_name)
                if dest.read_text(encoding="utf-8") != content:
                    dest.write_text(content, encoding="utf-8")
    except Exception:
        pass  # Non-critical: template sync failure doesn't block pipeline

    prd = Path(prd_path) if prd_path else PRD_PATH

    # Copy alternate PRD if provided
    if prd_path and prd_path != str(PRD_PATH):
        source = Path(prd_path).resolve()
        project_root = Path.cwd().resolve()
        if not str(source).startswith(str(project_root)):
            print(f"{RED}  Error: PRD path must be inside the project directory.{RESET}")
            print(f"  {DIM}Resolved: {source}{RESET}")
            print(f"  {DIM}Project:  {project_root}{RESET}")
            return False
        if not source.exists() or source.stat().st_size == 0:
            print(f"{RED}  Error: PRD not found or empty: {prd_path}{RESET}")
            return False
        dest = PRD_PATH
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(dest))
        prd = dest

    # ── Auto-plan if PRD is placeholder/empty ──
    if _prd_needs_planning(prd):
        print(f"\n{BOLD}{CYAN}  PRD is a placeholder — launching planner...{RESET}\n")
        from forja.planner import run_plan  # local import to avoid circular dep

        plan_ok = run_plan(prd_path=str(prd), _called_from_runner=True)
        if not plan_ok:
            print(f"{RED}  Planning aborted. Cannot continue to build.{RESET}")
            return False

        # Re-check: planner should have enriched the PRD
        if _prd_needs_planning(prd):
            print(f"{RED}  Error: PRD is still a placeholder after planning. "
                  f"Write your PRD in {prd} and run again.{RESET}")
            return False

        print(f"\n{GREEN}{BOLD}  ✔ Plan complete — continuing to build pipeline...{RESET}\n")
        _persist_planning_decisions()

    if not prd.exists() or prd.stat().st_size == 0:
        print(f"{RED}  Error: Write your PRD in {prd} first.{RESET}")
        return False

    # Load env
    load_dotenv()

    # Check claude is installed
    if shutil.which("claude") is None:
        print(f"{RED}  Error: Claude Code not found. Install: npm install -g @anthropic-ai/claude-code{RESET}")
        return False

    # Check API key early to avoid wasting time on phases that will fail
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print(f"{RED}  Error: ANTHROPIC_API_KEY not set.{RESET}")
        print(f"{DIM}  Run: forja config{RESET}")
        return False

    # ── Banner ──
    prd_lines = prd.read_text().strip().split("\n")
    prd_title = prd_lines[0].lstrip("# ").strip() if prd_lines else "Unknown"

    print()
    print(f"{BOLD}{CYAN}  \u2500\u2500 Forja \u2500\u2500{RESET}")
    print(f"{DIM}  PRD: {prd_title}{RESET}")
    print(f"{DIM}  Pipeline: spec-review \u2192 build \u2192 smoke-test \u2192 tests \u2192 visual-eval \u2192 outcome \u2192 learnings \u2192 observatory{RESET}")

    pipeline_start = time.time()
    _emit_event("pipeline.start", {"prd": str(prd), "preserve_build": preserve_build})

    # ── Phase 0: Spec Review (informational + enrich) ──
    _phase_header(0, "Spec Review", 8)
    _run_spec_review(str(prd))
    _emit_event("phase.complete", {"phase": "Spec Review", "success": True})

    # ── Phase 1: Context Injection ──
    _phase_header(1, "Context Injection", 8)
    _inject_context_into_claude_md()
    _emit_event("phase.complete", {"phase": "Context Injection", "success": True})

    # ── Workflow-based features (when workflow.json exists) ──
    if WORKFLOW_PATH.exists():
        _generate_workflow_features(WORKFLOW_PATH, TEAMMATES_DIR)
        _generate_agent_context(WORKFLOW_PATH, TEAMMATES_DIR)
        wf = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        phase_count = len(wf.get("phases", []))
        print(f"  {GREEN}workflow mode{RESET}: {phase_count} phases generated")

    # ── Launch Observatory Live (background) ──
    observatory_proc = _start_observatory_live()

    # ── Clean artifacts ──
    if preserve_build:
        # Iterate mode: keep src/ and teammates so Claude Code patches existing code
        print(f"  {CYAN}Incremental mode: preserving existing build{RESET}")
    else:
        for d in [str(TEAMMATES_DIR), "src"]:
            p = Path(d)
            if p.is_symlink():
                # Never follow symlinks — just remove the link itself
                p.unlink()
                print(f"  {YELLOW}Warning: removed symlink {d} (potential attack){RESET}")
            elif p.is_dir():
                shutil.rmtree(d)
        for f in [str(TEAMMATES_DIR.parent / "teammate_map.json")]:
            p = Path(f)
            if p.is_symlink():
                p.unlink()
            elif p.is_file():
                p.unlink()

    # ── Phase 2: Build (Claude Code) ──
    _phase_header(2, "Build (Claude Code)", 8)

    env = os.environ.copy()
    env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"

    build_start = time.time()
    stop_event = threading.Event()
    timeout_event = threading.Event()
    monitor = threading.Thread(
        target=_monitor_progress,
        args=(stop_event, build_start, timeout_event),
        daemon=True,
    )
    monitor.start()

    timed_out = False
    try:
        proc = subprocess.Popen(
            [
                "claude",
                "--dangerously-skip-permissions",
                "-p", BUILD_PROMPT,
                "--output-format", "text",
            ],
            env=env,
            preexec_fn=os.setsid,
        )

        # Poll process, checking for timeout signal from monitor thread
        while proc.poll() is None:
            if timeout_event.is_set():
                timed_out = True
                # Kill the entire process group, not just the parent
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=10)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        proc.wait(timeout=5)
                    except OSError as exc:
                        logger.debug("SIGKILL failed for build process: %s", exc)
                break
            time.sleep(1)

        returncode = proc.returncode or 0

    except KeyboardInterrupt:
        print(f"\n{YELLOW}  Interrupted by user.{RESET}")
        stop_event.set()
        # Kill Claude Code process group to avoid orphans
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    pass
        monitor.join(timeout=3)
        _stop_observatory_live(observatory_proc)
        return False
    finally:
        stop_event.set()
        monitor.join(timeout=3)

    build_elapsed = time.time() - build_start
    print()

    # Save git log as build record
    try:
        project_dir = Path.cwd()
        git_log = subprocess.run(
            ["git", "log", "--oneline", "-20"],
            capture_output=True, text=True, cwd=project_dir,
            timeout=10,
        )
        log_path = Path(".forja/logs/build-commits.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(git_log.stdout)
    except Exception as e:
        from forja.utils import logger
        logger.debug("git log: %s", e)

    # Stop observatory live
    _stop_observatory_live(observatory_proc)

    total, passed, blocked = _count_features()

    blocked_note = f" [{blocked} blocked]" if blocked else ""
    if timed_out:
        _phase_result(False,
            f"[TIMEOUT] {passed}/{total} features{blocked_note} after {_format_duration(build_elapsed)} "
            f"- continuing pipeline with partial results")
        build_ok = False
    elif returncode == 0:
        build_ok = True
        _phase_result(True, f"{passed}/{total} features{blocked_note} in {_format_duration(build_elapsed)}")
    else:
        build_ok = False
        _phase_result(False, f"exit {returncode} after {_format_duration(build_elapsed)}")

    _emit_event("phase.complete", {
        "phase": "build", "success": build_ok,
        "passed": passed, "total": total, "blocked": blocked,
        "elapsed_seconds": int(build_elapsed), "timed_out": timed_out,
    })

    # ── Phase 3: Server Health Smoke Test + Endpoint Probes ──
    _phase_header(3, "Smoke Test & Endpoint Probes", 8)
    smoke_result = _run_smoke_test()
    if smoke_result["passed"]:
        _phase_result(True, smoke_result.get("summary", "healthy"))
    elif smoke_result.get("skill") == "unknown" or not smoke_result.get("port"):
        pass  # Already printed "skipped" inside _run_smoke_test
    else:
        _phase_result(False, smoke_result.get("summary", "server not healthy"))

    # Show probe summary if probes ran
    probe_sum = smoke_result.get("probe_summary", {})
    if probe_sum.get("total", 0) > 0:
        p_total = probe_sum["total"]
        p_passed = probe_sum["passed"]
        p_rate = probe_sum.get("pass_rate", 0)
        color = GREEN if p_rate >= 70 else RED
        _phase_result(
            p_rate >= 70,
            f"{color}{p_rate:.0f}% endpoints verified{RESET} ({p_passed}/{p_total} probes passed)",
        )
    _emit_event("phase.complete", {"phase": "Smoke Test", "success": smoke_result["passed"]})

    # ── Phase 4: Project Tests (informational) ──
    _phase_header(4, "Project Tests", 8)
    test_results = _run_project_tests(Path.cwd())
    _log_test_failures_as_learnings(test_results)
    _emit_event("phase.complete", {"phase": "Project Tests", "success": test_results.get("exit_code", -1) == 0})

    # ── Phase 5: Visual Evaluation (informational) ──
    _phase_header(5, "Visual Evaluation", 8)
    _run_visual_eval(str(prd))
    _emit_event("phase.complete", {"phase": "Visual Evaluation", "success": True})

    # ── Phase 6: Outcome Evaluation (informational) ──
    _phase_header(6, "Outcome Evaluation", 8)
    _run_outcome(str(prd))
    _persist_outcome_gaps()
    _emit_event("phase.complete", {"phase": "Outcome Evaluation", "success": True})

    # ── Phase 7: Extract Learnings (informational) ──
    _phase_header(7, "Extract Learnings", 8)
    _run_learnings_extract()
    _run_learnings_apply()
    _run_learnings_synthesize()
    _emit_event("phase.complete", {"phase": "Extract Learnings", "success": True})

    # ── Phase 8: Observatory (informational) ──
    _phase_header(8, "Observatory Report", 8)
    _run_observatory()
    _emit_event("phase.complete", {"phase": "Observatory Report", "success": True})

    # ── Save iteration changelog ──
    _save_iteration_log(pipeline_start, total, passed, blocked, build_elapsed)

    # ── Auto-open output ──
    cfg = load_config()
    if cfg.build.auto_open:
        _auto_open_output()

    # ── Final summary ──
    total_elapsed = time.time() - pipeline_start
    print()
    print(f"  {'=' * 40}")

    if build_ok:
        print(f"{GREEN}{BOLD}  \u2714 Forja complete{RESET}")
    else:
        print(f"{RED}{BOLD}  \u2718 Forja finished with errors{RESET}")
        # Show what went wrong
        if timed_out:
            print(f"  {RED}  \u2022 Build timed out after {_format_duration(build_elapsed)}{RESET}")
        elif returncode != 0:
            print(f"  {RED}  \u2022 Build phase failed (exit code {returncode}){RESET}")
        if total > 0 and passed < total:
            failed = total - passed - blocked
            if failed > 0:
                print(f"  {RED}  \u2022 {failed} feature{'s' if failed != 1 else ''} failed{RESET}")
            if blocked > 0:
                print(f"  {RED}  \u2022 {blocked} feature{'s' if blocked != 1 else ''} blocked (max cycles){RESET}")

    print(f"{DIM}  Total time: {_format_duration(total_elapsed)}{RESET}")
    if total > 0:
        feat_summary = f"{DIM}  Features: {passed}/{total} passed"
        if blocked:
            feat_summary += f", {blocked} blocked"
        feat_summary += f"{RESET}"
        print(feat_summary)
    print()
    # ── Detect entry point and show how to run ──
    entry_point = _detect_entry_point()
    if entry_point:
        print(f"  {BOLD}How to run:{RESET}")
        print(f"    {GREEN}{entry_point}{RESET}")
        print()

    if build_ok:
        print(f"  Next steps:")
        print(f"    forja status    - feature details")
        print(f"    forja report    - metrics dashboard")
        print(f"    forja audit     - decision audit trail")
    else:
        print(f"  Next steps:")
        print(f"    forja status    - see which features failed")
        print(f"    forja iterate   - review failures and retry")
        print(f"    forja report    - full metrics dashboard")
    print()

    _emit_event("pipeline.end", {
        "success": build_ok, "total": total, "passed": passed, "blocked": blocked,
        "elapsed_seconds": int(total_elapsed),
    })

    return build_ok


# ═══════════════════════════════════════════════════════════════════════
# forja iterate — Human Feedback Loop
# ═══════════════════════════════════════════════════════════════════════

SPEC_EXTENSIONS = {".md", ".yaml", ".yml"}
MAX_SPEC_FILE_SIZE = 15_000  # chars — skip files larger than this


def _discover_editable_specs() -> list[Path]:
    """Find editable spec files for iteration improvement.

    Checks ``specs/`` directory first (multi-spec mode), falls back to
    ``context/prd.md`` (single-file mode).

    Returns list of Path objects sorted by priority:
    1. specs/PRD.md (main PRD)
    2. specs/SITE-STRUCTURE.md (structure)
    3. specs/STYLE.md (style guide)
    4. All other spec files sorted by path
    5. context/prd.md (if it exists alongside specs/, as secondary)

    Returns ``[PRD_PATH]`` if no ``specs/`` directory exists.
    """
    specs: list[Path] = []

    if SPECS_DIR.is_dir():
        priority_names = ["PRD.md", "SITE-STRUCTURE.md", "STYLE.md"]
        priority_files: list[Path] = []
        other_files: list[Path] = []

        for fpath in sorted(SPECS_DIR.rglob("*")):
            if not fpath.is_file():
                continue
            if fpath.suffix.lower() not in SPEC_EXTENSIONS:
                continue
            if fpath.name.startswith("."):
                continue
            # Size guard
            try:
                content = fpath.read_text(encoding="utf-8")
                if len(content) > MAX_SPEC_FILE_SIZE:
                    logger.warning("Skipping large spec file %s (%d chars)", fpath, len(content))
                    continue
                if not content.strip():
                    continue
            except (OSError, UnicodeDecodeError):
                continue

            if fpath.name in priority_names and fpath.parent == SPECS_DIR:
                priority_files.append(fpath)
            else:
                other_files.append(fpath)

        # Sort priority files by defined order
        def _priority_key(p: Path) -> int:
            try:
                return priority_names.index(p.name)
            except ValueError:
                return len(priority_names)

        priority_files.sort(key=_priority_key)
        specs = priority_files + other_files

        # Also include context/prd.md if it exists (backward compat)
        if PRD_PATH.exists():
            try:
                if PRD_PATH.read_text(encoding="utf-8").strip():
                    specs.append(PRD_PATH)
            except OSError:
                pass
    else:
        # Fallback: single PRD mode
        if PRD_PATH.exists():
            try:
                if PRD_PATH.read_text(encoding="utf-8").strip():
                    specs = [PRD_PATH]
            except OSError:
                pass

    return specs


def _read_specs(spec_paths: list[Path]) -> dict[str, str]:
    """Read spec files into a dict keyed by relative path string."""
    specs: dict[str, str] = {}
    for p in spec_paths:
        try:
            text = p.read_text(encoding="utf-8")
            key = str(p)  # relative path like "specs/PRD.md"
            specs[key] = text
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Could not read spec %s: %s", p, exc)
    return specs


def _build_iteration_context() -> tuple[str, list[dict]]:
    """Read outcome report, failed features, and learnings manifest.

    Returns (formatted_context_str, failed_features_list).
    """
    sections: list[str] = []
    failed_features: list[dict] = []

    # ── Failed / blocked features ──
    if TEAMMATES_DIR.exists():
        for fj in sorted(TEAMMATES_DIR.glob("*/features.json")):
            data = safe_read_json(fj)
            if data is None:
                continue
            features = data.get("features", data) if isinstance(data, dict) else data
            if not isinstance(features, list):
                continue
            for fd in features:
                feat = Feature.from_dict(fd)
                if feat.status in ("failed", "blocked", "pending"):
                    failed_features.append({
                        "id": feat.id,
                        "description": feat.description or feat.name or feat.id,
                        "status": feat.status,
                        "cycles": feat.cycles,
                    })

    if failed_features:
        lines = [f"## Failed / Incomplete Features ({len(failed_features)})"]
        for ff in failed_features:
            lines.append(f"- [{ff['status'].upper()}] {ff['id']}: {ff['description']}")
        sections.append("\n".join(lines))

    # ── Outcome report (unmet requirements) ──
    outcome_path = FORJA_DIR / "outcome-report.json"
    if outcome_path.exists():
        try:
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
            unmet = outcome.get("unmet", [])
            coverage = outcome.get("coverage", "?")
            if unmet:
                lines = [f"## Unmet Requirements (coverage: {coverage}%)"]
                for req in unmet:
                    lines.append(f"- {req}")
                sections.append("\n".join(lines))
        except (json.JSONDecodeError, OSError):
            pass

    # ── Learnings manifest ──
    learnings_script = FORJA_TOOLS / "forja_learnings.py"
    if learnings_script.exists():
        try:
            result = subprocess.run(
                [sys.executable, str(learnings_script), "manifest"],
                capture_output=True, text=True, timeout=10,
            )
            manifest = result.stdout.strip()
            if manifest and "No learnings" not in manifest:
                sections.append(f"## Learnings from Previous Runs\n{manifest}")
        except (subprocess.TimeoutExpired, OSError):
            pass

    return "\n\n".join(sections) if sections else "No iteration data available.", failed_features


def _enrich_feedback(user_feedback: str) -> str:
    """Append Forja feature descriptions when user mentions them.

    Maps keywords like "observatory" or "learnings" to concrete descriptions
    so the LLM understands what the user is referring to.
    """
    from forja.constants import FORJA_FEATURES_GLOSSARY

    additions: list[str] = []
    lower = user_feedback.lower()
    for info in FORJA_FEATURES_GLOSSARY.values():
        if any(kw in lower for kw in info["keywords"]):
            additions.append(f"[Forja context: {info['name']}] {info['description']}")
    if additions:
        return user_feedback + "\n\n" + "\n".join(additions)
    return user_feedback


# ── Decision Synthesis & Log ──────────────────────────────────────

DECISIONS_LOG = Path("context") / "decisions.jsonl"


def _load_decision_history() -> list[dict]:
    """Load accumulated decisions from context/decisions.jsonl."""
    if not DECISIONS_LOG.exists():
        return []
    decisions = []
    try:
        for line in DECISIONS_LOG.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                decisions.append(json.loads(line))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to load decision history: %s", exc)
    return decisions


def _save_decisions(decisions: list[dict], iteration: int) -> None:
    """Append new decisions to context/decisions.jsonl."""
    DECISIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DECISIONS_LOG, "a", encoding="utf-8") as f:
        for d in decisions:
            d["iteration"] = iteration
            d["timestamp"] = datetime.now(timezone.utc).isoformat()
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


def _synthesize_decisions(
    qa_transcript: list[dict],
    user_feedback: str,
    iteration_context: str,
    tech_findings: str = "",
) -> list[dict]:
    """Synthesize structured decisions from expert panel transcript + user feedback.

    Calls the LLM to extract explicit, typed decisions from the raw
    Q&A transcript.  Each decision has a type, target section, the
    decision text, and rationale.

    Returns a list of decision dicts.  Falls back to simple extraction
    if the LLM call fails.
    """
    # Build the transcript text
    transcript_text = ""
    for entry in qa_transcript:
        transcript_text += (
            f"- [{entry.get('expert', '?')}] Q: {entry.get('question', '')}\n"
            f"  A: {entry.get('answer', '')} (source: {entry.get('tag', 'UNKNOWN')})\n"
        )

    # Load previous decisions for context
    history = _load_decision_history()
    history_text = ""
    if history:
        history_text = "\n## Previous Decisions (do not contradict without reason)\n"
        for h in history[-20:]:  # Last 20 decisions
            history_text += f"- [iter {h.get('iteration', '?')}] {h.get('type', '?')}: {h.get('decision', '')}\n"

    prompt = (
        f"You are a decision synthesizer. Analyze the expert panel transcript and "
        f"user feedback below, then extract STRUCTURED DECISIONS.\n\n"
        f"## User Feedback\n{user_feedback}\n\n"
        f"## Expert Panel Transcript\n{transcript_text}\n"
        f"{'## Tech Findings' + chr(10) + tech_findings + chr(10) if tech_findings else ''}"
        f"\n## Build Context\n{iteration_context[:2000]}\n"
        f"{history_text}\n\n"
        f"Extract decisions as a JSON array. Each decision must have:\n"
        f'- "type": one of "enrich", "constrain", "descope", "detail", "fix"\n'
        f'  - "enrich": add more detail, examples, acceptance criteria to a feature\n'
        f'  - "constrain": add a technical or scope constraint\n'
        f'  - "descope": move feature to Out of Scope (only when fundamentally unbuildable)\n'
        f'  - "detail": expand a vague description with specific implementation notes\n'
        f'  - "fix": correct something that was wrong or caused a build failure\n'
        f'- "target": the section or feature this applies to (e.g., "Feature: Combat System")\n'
        f'- "decision": the concrete change to make (2-3 sentences)\n'
        f'- "rationale": why this decision was made (1 sentence)\n\n'
        f"BIAS: Prefer 'enrich' and 'detail' over 'descope'. "
        f"Only descope when something is fundamentally impossible to build. "
        f"When a feature failed, the FIRST instinct is to add specificity about HOW "
        f"it should work — not to remove it.\n\n"
        f"Return ONLY a JSON array, no preamble."
    )

    try:
        raw = call_llm(
            prompt,
            system=(
                "You are an expert at crystallizing decisions from discussions. "
                "Extract clear, actionable decisions. Respond only with valid JSON."
            ),
            provider="anthropic",
        )
    except Exception as exc:
        logger.debug("Decision synthesis LLM call failed: %s", exc)
        raw = ""

    if raw:
        parsed = parse_json(raw)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "decisions" in parsed:
            return parsed["decisions"]

    # Fallback: extract decisions from transcript mechanically
    decisions = []
    for entry in qa_transcript:
        tag = entry.get("tag", "")
        if tag in ("FACT", "DECISION"):
            decisions.append({
                "type": "enrich",
                "target": entry.get("question", "")[:60],
                "decision": entry.get("answer", ""),
                "rationale": f"Expert {entry.get('expert', '?')} recommendation",
            })
    return decisions


def _format_decisions_for_prd_edit(decisions: list[dict]) -> str:
    """Format synthesized decisions into a structured prompt section."""
    if not decisions:
        return ""

    parts = ["## Synthesized Decisions (apply these to the PRD)\n"]
    for i, d in enumerate(decisions, 1):
        dtype = d.get("type", "enrich").upper()
        target = d.get("target", "General")
        decision = d.get("decision", "")
        rationale = d.get("rationale", "")
        parts.append(
            f"{i}. [{dtype}] Target: {target}\n"
            f"   Decision: {decision}\n"
            f"   Rationale: {rationale}"
        )
    return "\n".join(parts)


def _improve_prd_with_context(prd_text: str, iteration_context: str, user_feedback: str,
                               decisions: list[dict] | None = None) -> str:
    """Enrich the PRD using build results, user feedback, and synthesized decisions.

    Unlike a destructive rewrite, this function GROWS the PRD by adding
    detail, constraints, and acceptance criteria.  Features that failed
    get MORE description (how to build them), not less.  Only
    fundamentally unbuildable features are moved to Out of Scope.
    """
    enriched = _enrich_feedback(user_feedback)
    decisions_text = _format_decisions_for_prd_edit(decisions) if decisions else ""

    # Read README for product voice context
    readme_context = ""
    readme_path = Path("README.md")
    if readme_path.exists():
        try:
            lines = readme_path.read_text(encoding="utf-8").splitlines()[:60]
            readme_context = "\n".join(lines)
        except OSError:
            pass

    prompt = (
        f"Here is a PRD that produced a partial build:\n\n"
        f"{prd_text}\n\n"
        f"## Build Results\n{iteration_context}\n\n"
        f"## User Feedback\n{enriched}\n\n"
        f"{decisions_text}\n\n"
        f"ENRICH the PRD by applying each decision above. Rules:\n"
        f"1. For 'ENRICH' decisions: ADD paragraphs, bullets, acceptance criteria to the target section. "
        f"   Do NOT rewrite existing text — append below it.\n"
        f"2. For 'DETAIL' decisions: Expand vague descriptions with specific implementation notes, "
        f"   file paths, function signatures, data structures.\n"
        f"3. For 'CONSTRAIN' decisions: Add a '### Constraints' subsection or bullet to the target.\n"
        f"4. For 'FIX' decisions: Modify the specific incorrect statement.\n"
        f"5. For 'DESCOPE' decisions: Move the feature to an '## Out of Scope' section "
        f"   at the bottom — include the rationale. Do NOT delete.\n\n"
        f"CRITICAL: The output PRD must be LONGER and MORE DETAILED than the input. "
        f"Every existing sentence must be preserved unless directly contradicted by a decision. "
        f"When a feature failed, ADD specificity about HOW it should be built.\n"
        f"Return ONLY the enriched PRD in markdown, no preamble."
    )

    system_parts = [
        "You are a PRD enrichment editor. Your job is to make PRDs GROW in specificity "
        "and detail with each iteration. You NEVER delete content unless explicitly "
        "told to descope something. When features failed to build, you add more detail "
        "about HOW they should be implemented — specific file structures, function "
        "signatures, data formats, step-by-step instructions. "
        "You ADD acceptance criteria, edge cases, constraints, and implementation notes. "
        "The PRD should be the definitive source of truth — rich enough that a developer "
        "can build each feature without guessing. "
        "Do NOT invent features the user didn't ask for. "
        "Return ONLY the PRD in markdown.",
    ]
    if readme_context:
        system_parts.append(
            f"\nThe product is described by this README:\n{readme_context}\n\n"
            "Match the README's voice — direct, technical, for developers. "
            "No enterprise language, no marketing fluff, no buzzwords."
        )

    try:
        result = call_llm(
            prompt,
            system="\n".join(system_parts),
            provider="anthropic",
        )
    except Exception:
        result = ""
    if result:
        text = result.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            first_nl = text.find("\n")
            last_fence = text.rfind("```")
            if first_nl != -1 and last_fence > first_nl:
                text = text[first_nl + 1:last_fence].strip()
        return text
    return prd_text


def _improve_specs_with_context(
    specs: dict[str, str],
    iteration_context: str,
    user_feedback: str,
    decisions: list[dict] | None = None,
) -> dict[str, str]:
    """Enrich multiple spec files using build results, user feedback, and decisions.

    Takes all spec contents as a dict ``{relative_path: content}``.
    Returns dict ``{relative_path: improved_content}`` for files that
    actually changed.  Unchanged files are **not** included.

    Uses a single LLM call with all specs for cross-file coherence.
    The approach is ADDITIVE — specs grow richer, never thinner.
    """
    enriched = _enrich_feedback(user_feedback)
    decisions_text = _format_decisions_for_prd_edit(decisions) if decisions else ""

    # Read README for product voice context
    readme_context = ""
    readme_path = Path("README.md")
    if readme_path.exists():
        try:
            lines = readme_path.read_text(encoding="utf-8").splitlines()[:60]
            readme_context = "\n".join(lines)
        except OSError:
            pass

    # Build spec listing for the prompt
    spec_listing = []
    for path, content in specs.items():
        spec_listing.append(f"### File: {path}\n```markdown\n{content}\n```")
    all_specs_text = "\n\n".join(spec_listing)

    prompt = (
        f"Here are the editable spec files for a project that produced a partial build.\n"
        f"There are {len(specs)} files.\n\n"
        f"{all_specs_text}\n\n"
        f"## Build Results\n{iteration_context}\n\n"
        f"## User Feedback\n{enriched}\n\n"
        f"{decisions_text}\n\n"
        f"ENRICH the spec files by applying the decisions above. Rules:\n"
        f"1. PRESERVE all existing text. Only ADD, never delete (unless descoping).\n"
        f"2. For 'ENRICH'/'DETAIL' decisions: add paragraphs, bullets, acceptance criteria.\n"
        f"3. For 'FIX' decisions: modify the specific incorrect statement.\n"
        f"4. For 'DESCOPE' decisions: move to '## Out of Scope' with rationale.\n"
        f"5. Make changes in the most specific file possible.\n"
        f"6. Each changed file must be LONGER and MORE DETAILED than its input.\n\n"
        f"Return a JSON object where:\n"
        f"- Keys are the file paths (exactly as listed above)\n"
        f"- Values are the COMPLETE enriched markdown content for that file\n"
        f"- Only include files that actually changed\n"
        f"- Do NOT include unchanged files\n\n"
        f"Return ONLY the JSON object, no preamble or explanation.\n"
        f'Example: {{"specs/PRD.md": "# PRD\\n\\nEnriched content...", '
        f'"specs/verticals/home/sections.md": "# Home Sections\\n\\nEnriched..."}}'
    )

    system_parts = [
        "You are a spec enrichment editor. Your job is to make specs GROW richer "
        "and more detailed with each iteration. You NEVER delete content unless "
        "explicitly told to descope something. "
        "When features failed to build, you add MORE detail about HOW they should "
        "be implemented — specific file structures, function signatures, data formats. "
        "The project may have multiple spec files: a main PRD for global rules, "
        "structure files, style guides, and per-vertical configs. "
        "Make changes in the most specific file possible (e.g., fix a vertical issue "
        "in that vertical's spec, not in the global PRD). "
        "Do NOT invent features the user didn't ask for. "
        "Return ONLY a JSON object mapping changed file paths to their enriched content.",
    ]
    if readme_context:
        system_parts.append(
            f"\nThe product is described by this README:\n{readme_context}\n\n"
            "Match the README's voice — direct, technical, for developers. "
            "No enterprise language, no marketing fluff, no buzzwords."
        )

    try:
        result = call_llm(
            prompt,
            system="\n".join(system_parts),
            provider="anthropic",
        )
    except Exception as exc:
        logger.debug("Multi-spec improvement LLM call failed: %s", exc)
        result = ""

    if not result:
        return {}

    # Parse JSON response
    parsed = parse_json(result)
    if parsed is None:
        # Fallback: if LLM returned plain markdown and there's only 1 spec,
        # treat the whole response as that file's content
        if len(specs) == 1:
            path = list(specs.keys())[0]
            text = result.strip()
            if text.startswith("```"):
                first_nl = text.find("\n")
                last_fence = text.rfind("```")
                if first_nl != -1 and last_fence > first_nl:
                    text = text[first_nl + 1:last_fence].strip()
            return {path: text}
        logger.warning("Failed to parse multi-spec LLM response as JSON")
        return {}

    # Validate: only accept keys that match known spec paths
    improved: dict[str, str] = {}
    for path, content in parsed.items():
        if path in specs and isinstance(content, str) and content.strip():
            improved[path] = content.strip()
        else:
            logger.warning("LLM returned unknown spec path: %s", path)

    return improved


def _save_multi_spec_snapshot(
    run_number: int,
    feedback_text: str,
    old_specs: dict[str, str],
    new_specs: dict[str, str],
) -> Path | None:
    """Save iteration snapshot for multi-spec changes.

    Creates:
    - manifest.json (with spec file counts)
    - feedback.md
    - specs-diff.md (combined unified diff for all changed files)
    - prd-diff.md (backward-compatible diff of main PRD)

    Returns the snapshot directory path or None on failure.
    """
    snapshot_dir = ITERATIONS_DIR / f"v{run_number:03d}"
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"  {YELLOW}Warning: could not create iteration snapshot: {exc}{RESET}")
        return None

    # ── manifest.json ──
    total, passed, blocked = _count_features()
    outcome_coverage = 0
    outcome_path = FORJA_DIR / "outcome-report.json"
    if outcome_path.exists():
        data = safe_read_json(outcome_path)
        if data:
            outcome_coverage = data.get("coverage", 0)

    learnings_count = 0
    for fpath in glob_mod.glob("context/learnings/*.jsonl"):
        try:
            learnings_count += sum(
                1 for line in Path(fpath).read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except OSError:
            pass

    # Compute combined hash of all new specs
    combined = "".join(new_specs.get(k, old_specs.get(k, "")) for k in sorted(old_specs.keys()))
    changed_files = [k for k in new_specs if k in old_specs and new_specs[k] != old_specs[k]]

    manifest = {
        "run_number": run_number,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "features": {"total": total, "passed": passed, "blocked": blocked},
        "outcome_coverage": outcome_coverage,
        "learnings_count": learnings_count,
        "specs_hash": hashlib.md5(combined.encode("utf-8")).hexdigest()[:12],
        "spec_files_total": len(old_specs),
        "spec_files_changed": len(changed_files),
        "changed_files": changed_files,
    }
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # ── feedback.md ──
    (snapshot_dir / "feedback.md").write_text(
        f"# Iteration {run_number} Feedback\n\n{feedback_text}\n",
        encoding="utf-8",
    )

    # ── specs-diff.md (combined diff for all files) ──
    all_diffs: list[str] = []
    for path in sorted(old_specs.keys()):
        old_content = old_specs.get(path, "")
        new_content = new_specs.get(path, old_content)
        if old_content == new_content:
            continue
        diff_lines = list(difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        ))
        if diff_lines:
            all_diffs.append(f"### {path}\n```diff\n{''.join(diff_lines)}\n```")

    diff_text = "\n\n".join(all_diffs) if all_diffs else "(no changes)"
    (snapshot_dir / "specs-diff.md").write_text(
        f"# Spec Changes — Iteration {run_number}\n\n{diff_text}\n",
        encoding="utf-8",
    )

    # Also write prd-diff.md for backward compat (main PRD if present)
    main_prd_path = None
    for p in old_specs:
        if "PRD" in Path(p).name.upper():
            main_prd_path = p
            break
    if main_prd_path is None and old_specs:
        main_prd_path = list(old_specs.keys())[0]

    if main_prd_path:
        old_prd = old_specs.get(main_prd_path, "")
        new_prd = new_specs.get(main_prd_path, old_prd)
        prd_diff_lines = list(difflib.unified_diff(
            old_prd.splitlines(keepends=True),
            new_prd.splitlines(keepends=True),
            fromfile="prd-before.md",
            tofile="prd-after.md",
        ))
        prd_diff_text = "".join(prd_diff_lines) if prd_diff_lines else "(no changes)"
        (snapshot_dir / "prd-diff.md").write_text(
            f"# PRD Changes — Iteration {run_number}\n\n```diff\n{prd_diff_text}\n```\n",
            encoding="utf-8",
        )

    print(f"  {GREEN}Iteration snapshot saved: {snapshot_dir}{RESET}")
    return snapshot_dir


# ── Autonomous iteration loop ─────────────────────────────────────


def _resolve_prd_path() -> Path:
    """Find the PRD path, checking specs/ first then context/prd.md."""
    specs_prd = Path(SPECS_DIR) / "PRD.md"
    if specs_prd.exists():
        return specs_prd
    return Path(PRD_PATH)


def _evaluate_quality_gates(
    cfg,
    coverage_target: int | None = None,
    features_target: int = 80,
) -> dict:
    """Evaluate all quality gates against config thresholds.

    Parameters
    ----------
    cfg : ForjaConfig
        Loaded configuration.
    coverage_target : int | None
        Override for coverage threshold (CLI ``--coverage`` flag).
        Falls back to ``cfg.build.quality_coverage``.
    features_target : int
        Minimum feature pass-rate percentage (default 80).

    Returns dict with per-gate results and an overall ``all_pass`` flag.
    """
    cov_threshold = coverage_target if coverage_target is not None else cfg.build.quality_coverage

    # Read outcome report
    outcome = safe_read_json(FORJA_DIR / "outcome-report.json") or {}
    coverage_val = outcome.get("coverage", 0)
    # Coerce to number (defence against string values)
    try:
        coverage_val = float(coverage_val)
    except (TypeError, ValueError):
        coverage_val = 0
    unmet = outcome.get("unmet", [])

    # Read test results
    test_results = safe_read_json(FORJA_DIR / "test-results.json") or {}
    test_passed = test_results.get("passed", 0)
    test_failed = test_results.get("failed", 0)
    test_exit_code = test_results.get("exit_code", -1)
    test_framework = test_results.get("framework")

    # Count features
    total, passed, blocked = _count_features()
    feat_pct = int(passed / total * 100) if total > 0 else 0

    # Evaluate gates
    coverage_ok = coverage_val >= cov_threshold

    # Tests gate: if framework detected, require exit_code == 0 AND no failures.
    # This catches timeouts (exit_code != 0 but failed == 0).
    if cfg.build.quality_tests_pass and test_framework:
        tests_ok = test_failed == 0 and test_exit_code == 0
    else:
        tests_ok = True

    features_ok = feat_pct >= features_target if total > 0 else True

    # Read visual evaluation report
    visual_report = safe_read_json(FORJA_DIR / "visual-eval.json") or {}
    visual_score = visual_report.get("score", -1)  # -1 means no report
    try:
        visual_score = float(visual_score)
    except (TypeError, ValueError):
        visual_score = -1
    visual_issues: list[str] = []
    for dim in ("layout", "responsive", "visual_quality", "content_match"):
        dim_data = visual_report.get(dim, {})
        if isinstance(dim_data, dict):
            visual_issues.extend(dim_data.get("issues", []))

    # Visual gate: only applies if a visual report exists (score >= 0)
    visual_threshold = getattr(cfg.build, "quality_visual_score", 70)
    if visual_score >= 0:
        visual_ok = visual_score >= visual_threshold
    else:
        visual_ok = True  # No visual report = gate does not apply

    # Read smoke test report
    smoke_report = safe_read_json(FORJA_DIR / "smoke-test.json") or {}
    smoke_passed = smoke_report.get("passed", True)  # Default True (no report = pass)
    smoke_has_report = bool(smoke_report)
    smoke_error = smoke_report.get("error", "")
    smoke_checks = smoke_report.get("checks", [])
    smoke_server_started = smoke_report.get("server_started", False)

    # Smoke gate: if a report exists and the test wasn't skipped, it must pass
    if smoke_has_report and smoke_report.get("port", 0) > 0:
        smoke_ok = smoke_passed
    else:
        smoke_ok = True  # No server to test = gate does not apply

    # Read endpoint probe trace
    probe_trace = safe_read_json(FORJA_DIR / "runtime-trace.json") or {}
    probe_summary = probe_trace.get("summary", {})
    probe_total = probe_summary.get("total", 0)
    probe_passed_count = probe_summary.get("passed", 0)
    probe_rate = probe_summary.get("pass_rate", -1)
    probe_failed_endpoints = probe_summary.get("failed_endpoints", [])

    # Probe gate: if probes ran, require >= configured pass rate
    probe_threshold = getattr(cfg.build, "quality_probe_pass_rate", 70)
    if probe_total > 0:
        try:
            probe_rate = float(probe_rate)
        except (TypeError, ValueError):
            probe_rate = 0
        probes_ok = probe_rate >= probe_threshold
    else:
        probes_ok = True  # No probes = gate does not apply

    # ── Verification Completeness ──
    # Track which gates were actually verified vs skipped
    smoke_skipped = not (smoke_has_report and smoke_report.get("port", 0) > 0)
    tests_skipped = not (cfg.build.quality_tests_pass and test_framework)
    visual_skipped = visual_score < 0
    probes_skipped = probe_total == 0

    total_gates = 6  # smoke, coverage, tests, features, visual, probes
    skipped_gates = sum([smoke_skipped, tests_skipped, visual_skipped, probes_skipped])
    verified_gates = total_gates - skipped_gates
    verification_pct = int(verified_gates / total_gates * 100) if total_gates > 0 else 0

    # all_pass is TRUE only when verified gates pass AND at least 2 gates were actually tested
    verified_all_pass = coverage_ok and tests_ok and features_ok and visual_ok and smoke_ok and probes_ok

    return {
        "all_pass": verified_all_pass,
        "verification_completeness": {
            "total_gates": total_gates,
            "verified": verified_gates,
            "skipped": skipped_gates,
            "pct": verification_pct,
            "skipped_names": [
                name for name, skipped in [
                    ("smoke", smoke_skipped), ("tests", tests_skipped),
                    ("visual", visual_skipped), ("probes", probes_skipped),
                ] if skipped
            ],
        },
        "coverage": {
            "value": coverage_val,
            "target": cov_threshold,
            "passed": coverage_ok,
        },
        "tests": {
            "passed": test_passed,
            "failed": test_failed,
            "exit_code": test_exit_code,
            "framework": test_framework,
            "required": cfg.build.quality_tests_pass,
            "gate_passed": tests_ok,
            "skipped": tests_skipped,
        },
        "features": {
            "total": total,
            "passed": passed,
            "blocked": blocked,
            "pct": feat_pct,
            "target": features_target,
            "gate_passed": features_ok,
        },
        "visual": {
            "score": visual_score,
            "target": visual_threshold,
            "passed": visual_ok,
            "has_report": visual_score >= 0,
            "issues": visual_issues[:10],
            "skipped": visual_skipped,
        },
        "smoke": {
            "passed": smoke_ok,
            "has_report": smoke_has_report,
            "server_started": smoke_server_started,
            "checks": smoke_checks,
            "error": smoke_error,
            "skipped": smoke_skipped,
        },
        "probes": {
            "total": probe_total,
            "passed": probe_passed_count,
            "pass_rate": probe_rate,
            "target": probe_threshold,
            "gate_passed": probes_ok,
            "failed_endpoints": probe_failed_endpoints[:10],
            "skipped": probes_skipped,
        },
        "unmet": unmet,
    }


def _print_gate_results(gates: dict, iteration: int) -> None:
    """Print quality gate results in a formatted table."""
    print(f"\n  {BOLD}Quality Gates (iteration {iteration}):{RESET}")

    # Smoke test gate
    smoke = gates.get("smoke", {})
    if smoke.get("skipped", True):
        print(f"    {DIM}⊖ Smoke: not evaluated (no server detected){RESET}")
    elif smoke.get("passed"):
        ok_checks = sum(1 for c in smoke.get("checks", []) if c.get("ok"))
        total_checks = len(smoke.get("checks", []))
        print(f"    {GREEN}✓{RESET} Smoke: server healthy ({ok_checks}/{total_checks} endpoints OK)")
    else:
        err = smoke.get("error", "server not healthy")[:60]
        print(f"    {RED}✗{RESET} Smoke: {err}")

    cov = gates["coverage"]
    mark = f"{GREEN}✓{RESET}" if cov["passed"] else f"{RED}✗{RESET}"
    print(f"    {mark} Coverage: {cov['value']}% (target: {cov['target']}%)")

    tst = gates["tests"]
    if tst.get("skipped", False):
        print(f"    {DIM}⊖ Tests: not evaluated (no test suite detected){RESET}")
    elif tst["gate_passed"]:
        print(f"    {GREEN}✓{RESET} Tests: {tst['passed']} passed, {tst['failed']} failed ({tst['framework']})")
    else:
        print(f"    {RED}✗{RESET} Tests: {tst['passed']} passed, {tst['failed']} failed ({tst['framework']})")

    feat = gates["features"]
    if feat["total"] > 0:
        mark = f"{GREEN}✓{RESET}" if feat["gate_passed"] else f"{RED}✗{RESET}"
        print(f"    {mark} Features: {feat['passed']}/{feat['total']} ({feat['pct']}%)")
    else:
        print(f"    {DIM}⊖ Features: none tracked{RESET}")

    vis = gates.get("visual", {})
    if vis.get("skipped", False):
        print(f"    {DIM}⊖ Visual: not evaluated (no screenshots){RESET}")
    elif vis["passed"]:
        print(f"    {GREEN}✓{RESET} Visual: {vis['score']}/100 (target: {vis['target']})")
    else:
        print(f"    {RED}✗{RESET} Visual: {vis['score']}/100 (target: {vis['target']})")

    probes = gates.get("probes", {})
    if probes.get("skipped", False):
        print(f"    {DIM}⊖ Probes: not evaluated (no endpoints detected){RESET}")
    elif probes["gate_passed"]:
        print(f"    {GREEN}✓{RESET} Probes: {probes['pass_rate']:.0f}% ({probes['passed']}/{probes['total']} endpoints, target: {probes['target']}%)")
    else:
        print(f"    {RED}✗{RESET} Probes: {probes['pass_rate']:.0f}% ({probes['passed']}/{probes['total']} endpoints, target: {probes['target']}%)")
        if probes.get("failed_endpoints"):
            for ep in probes["failed_endpoints"][:3]:
                print(f"      {RED}└ {ep}{RESET}")

    # Verification completeness
    vc = gates.get("verification_completeness", {})
    if vc.get("skipped", 0) > 0:
        skipped_names = ", ".join(vc.get("skipped_names", []))
        print(f"    {YELLOW}⚠ Verification: {vc['verified']}/{vc['total_gates']} gates verified ({vc['pct']}%) — skipped: {skipped_names}{RESET}")

    if gates["unmet"]:
        preview = ", ".join(str(r)[:40] for r in gates["unmet"][:3])
        suffix = "..." if len(gates["unmet"]) > 3 else ""
        print(f"    {YELLOW}Unmet: {preview}{suffix}{RESET}")


def _generate_auto_feedback(gates: dict, prd_path: Path | None = None) -> str:
    """Generate iteration feedback from quality gate failures using LLM.

    Synthesises: unmet requirements + test failures + feature status
    into concrete spec improvement suggestions.  Includes project
    context (PRD title / first lines) so the LLM understands *what*
    is being built.
    """
    parts: list[str] = []

    # Project context — first 3 lines of PRD
    prd_intro = ""
    if prd_path and prd_path.exists():
        try:
            lines = prd_path.read_text(encoding="utf-8").strip().splitlines()[:3]
            prd_intro = "\n".join(lines)
        except Exception:
            pass

    # Smoke test failure — most critical: app doesn't even start
    smoke_gate = gates.get("smoke", {})
    if smoke_gate.get("has_report") and not smoke_gate.get("passed"):
        error_msg = smoke_gate.get("error", "server not healthy")
        parts.append(
            f"CRITICAL - Server smoke test FAILED: {error_msg}\n"
            f"The built application does not start or respond to HTTP requests. "
            f"This must be fixed before anything else can be evaluated."
        )
        failed_checks = [
            c for c in smoke_gate.get("checks", []) if not c.get("ok")
        ]
        if failed_checks:
            items = "\n".join(
                f"  - GET {c['path']} → HTTP {c['status']}" for c in failed_checks
            )
            parts.append(f"Failed endpoint checks:\n{items}")

    if not gates["coverage"]["passed"]:
        parts.append(
            f"Coverage: {gates['coverage']['value']}% "
            f"(target: {gates['coverage']['target']}%)"
        )
        if gates["unmet"]:
            items = "\n".join(f"  - {r}" for r in gates["unmet"][:10])
            parts.append(f"Unmet requirements:\n{items}")

    if not gates["tests"]["gate_passed"]:
        tst = gates["tests"]
        parts.append(
            f"Test failures: {tst['failed']} failed, "
            f"{tst['passed']} passed (exit code {tst.get('exit_code', '?')})"
        )
        test_results = safe_read_json(FORJA_DIR / "test-results.json") or {}
        output = test_results.get("output", "")
        if output:
            parts.append(f"Test output (last 2000 chars):\n{output[-2000:]}")

    if not gates["features"]["gate_passed"]:
        parts.append(
            f"Features: {gates['features']['passed']}/{gates['features']['total']} "
            f"passed ({gates['features']['blocked']} blocked)"
        )

    # Visual quality issues
    visual_gate = gates.get("visual", {})
    if visual_gate.get("has_report") and not visual_gate.get("passed"):
        parts.append(
            f"Visual quality: {visual_gate['score']}/100 "
            f"(target: {visual_gate['target']})"
        )
        issues = visual_gate.get("issues", [])
        if issues:
            items = "\n".join(f"  - {i}" for i in issues[:8])
            parts.append(f"Visual issues:\n{items}")

    # Endpoint probe failures — runtime evidence of broken endpoints
    probes_gate = gates.get("probes", {})
    if probes_gate.get("total", 0) > 0 and not probes_gate.get("gate_passed", True):
        parts.append(
            f"Endpoint probes: {probes_gate['pass_rate']:.0f}% pass rate "
            f"({probes_gate['passed']}/{probes_gate['total']}, target: {probes_gate['target']}%)"
        )
        failed_eps = probes_gate.get("failed_endpoints", [])
        if failed_eps:
            items = "\n".join(f"  - {ep}" for ep in failed_eps[:8])
            parts.append(f"Failed endpoints (from actual HTTP probes):\n{items}")
        # Include trace details for the LLM
        trace_data = safe_read_json(FORJA_DIR / "runtime-trace.json") or {}
        failed_probes = [p for p in trace_data.get("probes", []) if not p.get("passed")]
        if failed_probes:
            details = []
            for p in failed_probes[:5]:
                detail = f"  - {p.get('method')} {p.get('endpoint')}: got {p.get('actual_status')} (expected {p.get('expected_status')})"
                if p.get("missing_fields"):
                    detail += f", missing fields: {p['missing_fields']}"
                details.append(detail)
            parts.append(f"Probe failure details:\n" + "\n".join(details))

    # Read learnings manifest for additional context
    learnings_text = ""
    learnings_script = FORJA_TOOLS / "forja_learnings.py"
    if learnings_script.exists():
        try:
            lr = subprocess.run(
                ["python3", str(learnings_script), "manifest"],
                capture_output=True, text=True, timeout=30,
            )
            if lr.returncode == 0 and lr.stdout.strip():
                learnings_text = lr.stdout.strip()[:2000]
        except Exception:
            pass

    failure_summary = "\n\n".join(parts) if parts else "No specific failures identified."

    prompt = "You are an iteration advisor for an automated software build system.\n"
    if prd_intro:
        prompt += f"Project:\n{prd_intro}\n\n"
    prompt += (
        f"The last build had these quality gate failures:\n\n"
        f"{failure_summary}\n\n"
    )
    if learnings_text:
        prompt += f"Learnings from this run:\n{learnings_text}\n\n"

    prompt += (
        "Generate a concise feedback paragraph (3-5 sentences) that tells the spec "
        "improvement system:\n"
        "1. What specific requirements are not being met\n"
        "2. What technical issues need fixing (from test failures)\n"
        "3. What the specs should change to address these gaps\n\n"
        "Be specific and actionable. Focus on WHAT should change in the specs, "
        "not HOW the code should change.\n"
        "Do NOT suggest adding new features — focus on fixing what is broken."
    )

    try:
        return call_llm(
            prompt,
            system="You are a build iteration advisor. Be concise and actionable.",
        )
    except Exception:
        # Fallback: mechanical summary without LLM
        unmet = gates.get("unmet", [])
        unmet_preview = "; ".join(str(r)[:50] for r in unmet[:3]) if unmet else "none"
        test_fails = gates.get("tests", {}).get("failed", 0)
        feat_total = gates["features"]["total"]
        feat_passed = gates["features"]["passed"]
        return (
            f"Fix {len(unmet)} unmet requirements ({unmet_preview}). "
            f"Resolve {test_fails} test failures. "
            f"Complete {feat_total - feat_passed} incomplete features."
        )


def run_auto_forja(
    prd_path: str | None = None,
    max_iterations: int | None = None,
    coverage_target: int | None = None,
) -> bool:
    """Run Forja autonomously, iterating until quality gates pass.

    Called by ``forja auto``.  On each iteration the loop:

    1. Runs the full pipeline via :func:`run_forja`.
    2. Evaluates quality gates (coverage, tests, features).
    3. If all gates pass → returns ``True``.
    4. Otherwise generates feedback, improves specs, and loops.
    5. Detects stagnation (no coverage improvement) and exits early.

    Returns ``True`` if quality gates eventually pass,
    ``False`` if *max_iterations* is exhausted or stagnation detected.
    """
    cfg = load_config()
    max_iters = max_iterations if max_iterations is not None else cfg.build.max_auto_iterations
    cov_target = coverage_target if coverage_target is not None else cfg.build.quality_coverage

    prd = Path(prd_path) if prd_path else _resolve_prd_path()

    print(f"\n  {'=' * 50}")
    print(f"  {BOLD}{CYAN}FORJA AUTO — Autonomous Build Loop{RESET}")
    print(f"  {'=' * 50}")
    print(f"  Max iterations:  {max_iters}")
    print(f"  Coverage target: {cov_target}%")
    print(f"  Tests required:  {cfg.build.quality_tests_pass}")
    print(f"  Expert panels:   {'ON' if cfg.build.auto_expert_panels else 'OFF'}")
    print()

    prev_coverage: float = -1  # Track coverage across iterations for stagnation detection
    stagnant_count = 0         # Consecutive iterations with no coverage improvement

    for iteration in range(1, max_iters + 1):
        print(f"\n  {'─' * 50}")
        print(f"  {BOLD}Iteration {iteration}/{max_iters}{RESET}")
        print(f"  {'─' * 50}\n")

        # ── Run build pipeline ──
        preserve = iteration > 1
        try:
            build_ok = run_forja(prd_path=str(prd), preserve_build=preserve)
        except Exception as exc:
            print(f"\n  {RED}Build failed with error: {exc}{RESET}")
            print(f"  {DIM}Run 'forja status' to see which features failed{RESET}")
            build_ok = False

        if not build_ok and iteration == 1:
            # First run failed entirely — still evaluate gates to give useful feedback
            print(f"  {YELLOW}Build did not complete successfully, evaluating partial results...{RESET}")

        # ── Evaluate quality gates ──
        gates = _evaluate_quality_gates(cfg, coverage_target=cov_target)
        _print_gate_results(gates, iteration)

        current_coverage = gates["coverage"]["value"]

        if gates["all_pass"]:
            print(
                f"\n  {GREEN}{BOLD}✓ All quality gates passed on "
                f"iteration {iteration}!{RESET}\n"
            )
            return True

        if iteration >= max_iters:
            print(
                f"\n  {YELLOW}{BOLD}Max iterations ({max_iters}) reached. "
                f"Best coverage: {current_coverage}%{RESET}\n"
            )
            return False

        # ── Stagnation detection ──
        if current_coverage <= prev_coverage and prev_coverage >= 0:
            stagnant_count += 1
            if stagnant_count >= 2:
                print(
                    f"\n  {YELLOW}{BOLD}Stagnation detected: coverage has not improved "
                    f"for {stagnant_count} iterations ({current_coverage}%). "
                    f"Stopping to avoid wasted cycles.{RESET}\n"
                )
                return False
            print(f"  {YELLOW}No coverage improvement ({current_coverage}%), "
                  f"will try {2 - stagnant_count} more time(s) before stopping{RESET}")
        else:
            stagnant_count = 0
        prev_coverage = current_coverage

        # ── Generate feedback from failures ──
        print(f"\n  {DIM}Generating feedback from failures...{RESET}")
        auto_feedback = _generate_auto_feedback(gates, prd_path=prd)
        preview = auto_feedback[:200].replace("\n", " ")
        print(f"  {DIM}Feedback: {preview}...{RESET}")

        # ── Extract & apply learnings ──
        _run_learnings_extract()
        _run_learnings_apply()
        _run_learnings_synthesize()

        # ── Improve specs ──
        spec_paths = _discover_editable_specs()
        specs = _read_specs(spec_paths)

        if not specs:
            print(f"  {YELLOW}No editable specs found, re-running with learnings only{RESET}")
            _run_learnings_apply()
            print(f"\n  {CYAN}Starting iteration {iteration + 1}...{RESET}")
            continue

        context = gather_context(CONTEXT_DIR, max_chars=cfg.context.max_context_chars)
        iteration_context, _ = _build_iteration_context()

        # Optional: expert panels for richer feedback
        enriched_feedback = auto_feedback
        if cfg.build.auto_expert_panels:
            combined_specs = "\n\n---\n\n".join(
                f"### {path}\n{content}" for path, content in specs.items()
            )
            try:
                expert_feedback = _run_iteration_expert_panel(
                    combined_specs, auto_feedback, context,
                )
                if expert_feedback:
                    enriched_feedback = (
                        f"{auto_feedback}\n\nExpert panel:\n{expert_feedback}"
                    )
            except Exception:
                pass  # Expert panels are optional, never block

        # Improve specs (multi-spec or single-file)
        if len(specs) > 1:
            improved = _improve_specs_with_context(
                specs, iteration_context, enriched_feedback,
            )
            if improved:
                run_num = _next_iteration_number()
                _save_multi_spec_snapshot(
                    run_num, enriched_feedback, specs, improved,
                )
                for rel_path, new_content in improved.items():
                    Path(rel_path).write_text(
                        new_content + "\n", encoding="utf-8",
                    )
                print(f"  {GREEN}Updated {len(improved)} spec file(s){RESET}")
            else:
                print(f"  {YELLOW}No spec changes generated, re-running with learnings{RESET}")
        else:
            # Single-file mode
            prd_text = prd.read_text(encoding="utf-8")
            improved_prd = _improve_prd_with_context(
                prd_text, iteration_context, enriched_feedback,
            )
            if improved_prd and improved_prd.strip() != prd_text.strip():
                run_num = _next_iteration_number()
                _save_iteration_snapshot(
                    run_num, enriched_feedback, prd_text, improved_prd,
                )
                prd.write_text(improved_prd + "\n", encoding="utf-8")
                print(f"  {GREEN}PRD updated{RESET}")
            else:
                print(f"  {YELLOW}No PRD changes, re-running with learnings{RESET}")

        # Apply learnings before next iteration
        _run_learnings_apply()

        print(f"\n  {CYAN}Starting iteration {iteration + 1}...{RESET}")

    return False


def run_iterate(prd_path: str | None = None) -> bool:
    """Human feedback loop: review gaps, improve PRD, re-run.

    Called by ``forja iterate``.
    """
    from forja.context_setup import _flush_stdin
    from forja.planner import _interactive_prd_edit

    prd = Path(prd_path) if prd_path else PRD_PATH
    if not prd.exists() or not prd.read_text(encoding="utf-8").strip():
        print(f"\n  {RED}No PRD found. Run 'forja run' first to generate one.{RESET}\n")
        return False

    # Check if there's a previous run
    has_outcome = (FORJA_DIR / "outcome-report.json").exists()
    has_features = TEAMMATES_DIR.exists() and any(TEAMMATES_DIR.glob("*/features.json"))
    if not has_outcome and not has_features:
        print(f"\n  {RED}No previous run found. Run 'forja run' first.{RESET}\n")
        return False

    # ── Build iteration context ──
    iteration_context, failed_features = _build_iteration_context()

    total, passed, blocked = _count_features()
    failed_count = len(failed_features)

    # ── Print summary ──
    print()
    print(f"  {'=' * 40}")
    print(f"  {BOLD}Iteration Review{RESET}")
    print(f"  {'=' * 40}")
    print()

    if total > 0:
        pct = int(passed / total * 100) if total else 0
        color = GREEN if pct >= 80 else YELLOW if pct >= 50 else RED
        print(f"  Last run: {color}{passed}/{total} features ({pct}%){RESET}")
        print()

    if failed_features:
        print(f"  {RED}✘ Failed / Incomplete ({failed_count}):{RESET}")
        for ff in failed_features[:10]:  # Show max 10
            print(f"    - {ff['id']}: {ff['description'][:60]} ({ff['status'].upper()})")
        if failed_count > 10:
            print(f"    ... and {failed_count - 10} more")
        print()

    # Show unmet requirements
    outcome_path = FORJA_DIR / "outcome-report.json"
    if outcome_path.exists():
        try:
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
            unmet = outcome.get("unmet", [])
            if unmet:
                print(f"  {YELLOW}Unmet requirements ({len(unmet)}):{RESET}")
                for req in unmet[:5]:
                    print(f"    - {req[:70]}")
                if len(unmet) > 5:
                    print(f"    ... and {len(unmet) - 5} more")
                print()
        except (json.JSONDecodeError, OSError):
            pass

    # Show learnings that will carry forward
    learnings_script = FORJA_TOOLS / "forja_learnings.py"
    if learnings_script.exists():
        try:
            lr = subprocess.run(
                [sys.executable, str(learnings_script), "manifest"],
                capture_output=True, text=True, timeout=10,
            )
            manifest = lr.stdout.strip()
            if manifest and "No learnings" not in manifest:
                print(f"  {CYAN}Learnings stacked for next run:{RESET}")
                for line in manifest.splitlines()[:8]:
                    print(f"    {line}")
                if len(manifest.splitlines()) > 8:
                    print(f"    {DIM}...{RESET}")
                print()
        except (subprocess.TimeoutExpired, OSError):
            pass

    # ── Options menu ──
    print(f"  {BOLD}Options:{RESET}")
    print(f"    {GREEN}(1){RESET} Improve PRD and re-run")
    print(f"    {CYAN}(2){RESET} Re-run as-is (learnings already applied)")
    print(f"    {DIM}(3){RESET} Abort")
    print()

    _flush_stdin()
    try:
        choice = input(f"  {BOLD}>{RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if choice == "3" or not choice:
        print(f"\n  {DIM}Aborted.{RESET}\n")
        return False

    if choice == "1":
        # ── Collect feedback ──
        _flush_stdin()
        try:
            feedback = input(f"\n  {BOLD}What should change?{RESET} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return False

        if not feedback:
            print(f"  {DIM}No feedback provided, aborting.{RESET}")
            return False

        # ── Discover editable spec files ──
        spec_paths = _discover_editable_specs()
        specs = _read_specs(spec_paths)
        is_multi_spec = len(specs) > 1

        if is_multi_spec:
            print(f"\n  {CYAN}Multi-spec mode: {len(specs)} editable files found{RESET}")
            for sp in spec_paths:
                print(f"    - {sp}")
            print()

        # ── Build combined text for expert panels ──
        if is_multi_spec:
            combined_spec_text = "\n\n---\n\n".join(
                f"## {path}\n{content}" for path, content in specs.items()
            )
        else:
            combined_spec_text = (
                list(specs.values())[0] if specs
                else prd.read_text(encoding="utf-8")
            )

        # ── Expert panel review ──
        enriched_feedback = _run_iteration_expert_panel(
            combined_spec_text, iteration_context, feedback,
        )

        # ── Tech stack panel ──
        tech_findings = _run_tech_stack_panel(
            combined_spec_text, iteration_context, feedback,
        )

        # ── SYNTHESIZE DECISIONS ──
        # Extract the qa_transcript from enriched_feedback for synthesis
        print(f"\n  {DIM}Synthesizing decisions...{RESET}")
        qa_lines = []
        for line in enriched_feedback.splitlines():
            if line.startswith("- [") and "] Q:" in line:
                qa_lines.append(line)
        # Build a minimal transcript for synthesis
        synth_transcript = []
        for line in enriched_feedback.splitlines():
            if line.startswith("- ["):
                # Parse "- [Expert] Q: question\n  A: answer (source: TAG)"
                parts = line.split("] Q:", 1)
                if len(parts) == 2:
                    expert = parts[0].lstrip("- [")
                    rest = parts[1].strip()
                    synth_transcript.append({
                        "expert": expert,
                        "question": rest,
                        "answer": rest,
                        "tag": "DECISION",
                    })
            elif line.strip().startswith("A:"):
                if synth_transcript:
                    answer_text = line.strip().lstrip("A:").strip()
                    # Extract tag if present
                    tag = "DECISION"
                    if "(source:" in answer_text:
                        tag_part = answer_text.split("(source:")[-1].rstrip(")")
                        tag = tag_part.strip()
                        answer_text = answer_text.split("(source:")[0].strip()
                    synth_transcript[-1]["answer"] = answer_text
                    synth_transcript[-1]["tag"] = tag

        decisions = _synthesize_decisions(
            synth_transcript, feedback, iteration_context,
            tech_findings=tech_findings or "",
        )

        # ── Print decisions ──
        if decisions:
            iter_num = _next_iteration_number()
            print(f"\n  {BOLD}── Synthesized Decisions ({len(decisions)}) ──{RESET}")
            enrich_count = sum(1 for d in decisions if d.get("type") in ("enrich", "detail"))
            fix_count = sum(1 for d in decisions if d.get("type") == "fix")
            constrain_count = sum(1 for d in decisions if d.get("type") == "constrain")
            descope_count = sum(1 for d in decisions if d.get("type") == "descope")
            print(f"    {GREEN}+{enrich_count} enrich/detail{RESET}  "
                  f"{CYAN}+{fix_count} fix{RESET}  "
                  f"{YELLOW}+{constrain_count} constrain{RESET}  "
                  f"{RED}-{descope_count} descope{RESET}")
            for d in decisions[:8]:
                dtype = d.get("type", "?").upper()
                target = d.get("target", "?")[:50]
                decision = d.get("decision", "")[:80]
                print(f"    [{dtype}] {target}: {decision}")
            if len(decisions) > 8:
                print(f"    {DIM}... +{len(decisions) - 8} more{RESET}")
            print()

            # ── Persist decisions to log ──
            _save_decisions(decisions, iter_num)
            print(f"  {DIM}Decisions saved to {DECISIONS_LOG}{RESET}")
        else:
            decisions = []

        if tech_findings:
            enriched_feedback += "\n\n" + tech_findings

        if is_multi_spec:
            # ── Multi-spec enrichment ──
            print(f"\n  {DIM}Enriching {len(specs)} spec files...{RESET}")
            improved_specs = _improve_specs_with_context(
                specs, iteration_context, enriched_feedback,
                decisions=decisions,
            )

            if not improved_specs:
                print(f"  {YELLOW}No spec changes generated. Aborting.{RESET}")
                return False

            # ── Show per-file change summary ──
            print(f"\n  {BOLD}── Enrichment Summary ──{RESET}")
            total_delta = 0
            for path, new_content in improved_specs.items():
                old_content = specs.get(path, "")
                old_lines = len(old_content.splitlines())
                new_lines = len(new_content.splitlines())
                delta = new_lines - old_lines
                total_delta += delta
                delta_str = f"+{delta}" if delta >= 0 else str(delta)
                color = GREEN if delta >= 0 else RED
                print(f"    {color}M{RESET} {path} ({old_lines} → {new_lines} lines, {delta_str})")

            unchanged = len(specs) - len(improved_specs)
            if unchanged > 0:
                print(f"    {DIM}{unchanged} file(s) unchanged{RESET}")

            # ── Richness guard ──
            if total_delta < 0 and descope_count < 3:
                print(f"    {YELLOW}⚠ PRD shrank by {abs(total_delta)} lines without major descoping!{RESET}")
            elif total_delta > 0:
                print(f"    {GREEN}✓ PRD grew by +{total_delta} lines (richer){RESET}")
            print()

            # ── Preview first changed file ──
            first_path = list(improved_specs.keys())[0]
            preview = improved_specs[first_path][:600]
            if len(improved_specs[first_path]) > 600:
                preview += "\n..."
            print(f"  {BOLD}── Preview: {first_path} ──{RESET}")
            for line in preview.splitlines():
                print(f"  {line}")

            # ── Accept or abort ──
            print(f"\n  {BOLD}Options:{RESET}")
            print(f"    {GREEN}(1){RESET} Accept enriched specs and re-run")
            print(f"    {DIM}(2){RESET} Abort")
            print()

            _flush_stdin()
            try:
                sub_choice = input(f"  {BOLD}>{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return False

            if sub_choice != "1":
                print(f"\n  {DIM}Aborted.{RESET}\n")
                return False

            # ── Save multi-spec snapshot ──
            new_specs = dict(specs)
            new_specs.update(improved_specs)

            _save_multi_spec_snapshot(
                run_number=_next_iteration_number(),
                feedback_text=feedback,
                old_specs=specs,
                new_specs=new_specs,
            )

            # ── Write changed files ──
            for path, content in improved_specs.items():
                p = Path(path)
                p.write_text(content + "\n", encoding="utf-8")
                print(f"  {GREEN}Saved: {path}{RESET}")

        else:
            # ── Single-file mode (backward compatible) ──
            prd_text = (
                list(specs.values())[0] if specs
                else prd.read_text(encoding="utf-8")
            )

            print(f"\n  {DIM}Enriching PRD...{RESET}")
            improved = _improve_prd_with_context(
                prd_text, iteration_context, enriched_feedback,
                decisions=decisions,
            )

            # ── Richness guard ──
            old_lines = len(prd_text.splitlines())
            new_lines = len(improved.splitlines())
            delta = new_lines - old_lines
            if delta < 0 and descope_count < 3:
                print(f"  {YELLOW}⚠ PRD shrank by {abs(delta)} lines — may have lost detail{RESET}")
            elif delta > 0:
                print(f"  {GREEN}✓ PRD grew by +{delta} lines (richer){RESET}")

            # ── Show what changed ──
            print(f"\n  {BOLD}── Enriched PRD (preview) ──{RESET}")
            preview = improved[:800]
            if len(improved) > 800:
                preview += "\n..."
            for line in preview.splitlines():
                print(f"  {line}")

            # ── Interactive review (reuse planner's edit loop) ──
            improved = _interactive_prd_edit(improved)

            # ── Save iteration snapshot ──
            _save_iteration_snapshot(
                run_number=_next_iteration_number(),
                feedback_text=feedback,
                old_prd=prd_text,
                new_prd=improved,
            )

            # ── Save ──
            prd.write_text(improved + "\n", encoding="utf-8")
            print(f"\n  {GREEN}PRD saved to {prd}{RESET}")

    elif choice == "2":
        prd_text = prd.read_text(encoding="utf-8")
        _save_iteration_snapshot(
            run_number=_next_iteration_number(),
            feedback_text="(re-run with learnings, no PRD change)",
            old_prd=prd_text,
            new_prd=prd_text,
        )
        print(f"\n  {DIM}Re-running with current PRD (learnings applied)...{RESET}")
    else:
        print(f"\n  {DIM}Invalid option, aborting.{RESET}\n")
        return False

    # ── Consolidate learnings before re-run ──
    _run_learnings_apply()

    # ── Re-run pipeline (incremental: keep existing code) ──
    print(f"\n  {BOLD}Starting new run (incremental)...{RESET}\n")
    return run_forja(prd_path=str(prd), preserve_build=True)
