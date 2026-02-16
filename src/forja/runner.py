#!/usr/bin/env python3
"""Forja runner - full pipeline with live progress monitoring.

Pipeline: spec-review (enrich) -> context-inject -> build -> outcome -> learnings -> observatory
"""

from __future__ import annotations

import glob as glob_mod
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from forja.config_loader import load_config
from forja.constants import (
    BUILD_PROMPT, CLAUDE_MD, CONTEXT_DIR, FORJA_DIR, FORJA_TOOLS,
    PRD_PATH, STORE_DIR, TEAMMATES_DIR,
)
from forja.utils import (
    BOLD, CYAN, DIM, GREEN, RED, YELLOW, RESET,
    Feature, gather_context, load_dotenv, read_feature_status, safe_read_json,
)

# ── Placeholder detection ────────────────────────────────────────────

_PLACEHOLDER_MARKERS = (
    "Describe tu proyecto",
    "Describe your project here",
    "Describe your project",
)

MIN_REAL_CONTENT_CHARS = 50


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


def _phase_header(num, name, total=5):
    """Print phase header with timestamp."""
    ts = time.strftime("%H:%M:%S")
    print(f"\n{BOLD}{CYAN}  Phase {num}/{total}: {name}{RESET} {DIM}[{ts}]{RESET}")


def _phase_result(passed, message):
    """Print phase result."""
    icon = f"{GREEN}PASSED{RESET}" if passed else f"{RED}FAILED{RESET}"
    print(f"  {icon} {message}")


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
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
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


# ── Phase 1: Context injection into CLAUDE.md ────────────────────────

def _inject_context_into_claude_md():
    """Inject shared context (store + learnings) into CLAUDE.md."""
    claude_md = CLAUDE_MD
    if not claude_md.exists():
        return

    content = claude_md.read_text(encoding="utf-8")

    # Don't inject if already there
    if "## Shared Context (auto-generated)" in content:
        # Remove old injection to refresh
        lines = content.splitlines()
        new_lines = []
        skip = False
        for line in lines:
            if line.strip() == "## Shared Context (auto-generated)":
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
        except (OSError, UnicodeDecodeError):
            pass

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

    # Learnings manifest
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
            context_parts.append("\n### Learnings from Previous Runs\n")
            context_parts.append(manifest)

    # Business context: company, domains, design-system
    biz_text = gather_context(CONTEXT_DIR, max_chars=load_config().context.max_context_chars)
    if biz_text:
        context_parts.append("\n### Business Context\n")
        context_parts.append(biz_text)

    if not context_parts:
        print(f"  {DIM}no context to inject{RESET}")
        return

    # Insert after first heading
    context_block = "\n## Shared Context (auto-generated)\n\n"
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
    has_learnings = "Learnings" in context_block
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
        except OSError:
            pass
    # Also clean PID file
    pid_file = FORJA_DIR / "observatory-live.pid"
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass


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
                            except (subprocess.TimeoutExpired, OSError):
                                pass
                        elif stale_secs >= 300 and key not in feature_stall_warned:
                            feature_stall_warned.add(key)
                            sys.stdout.write(
                                f"\n{YELLOW}  [STALL] Feature {feat.id}"
                                f" has not progressed in 5 minutes{RESET}"
                            )
                            sys.stdout.flush()

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
                        timeout_event.set()

                    # Absolute stall timeout
                    elif stall_seconds >= timeout_absolute:
                        sys.stdout.write(
                            f"\n{RED}{BOLD}  [TIMEOUT] {passed}/{total} features "
                            f"({pct}%) - no progress for {int(stall_seconds//60)}min{RESET}\n"
                        )
                        sys.stdout.flush()
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


# ── Phase 3: Project Tests ──────────────────────────────────────────

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
        except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired):
            pass

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


# ── Phase 4: Outcome Evaluation ──────────────────────────────────────

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
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Fallback: just show exit code
    if result.returncode == 0:
        _phase_result(True, "evaluation complete")
    else:
        _phase_result(False, "evaluation found gaps (see .forja/outcome-report.json)")


# ── Phase 5: Extract Learnings ───────────────────────────────────────

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


# ── Phase 5b: Apply Learnings to Context ──────────────────────────────

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


# ── Phase 6: Observatory ─────────────────────────────────────────────

def _run_observatory():
    """Run observatory report. Informational, never blocks."""
    observatory_script = FORJA_TOOLS / "forja_observatory.py"
    if not observatory_script.exists():
        print(f"  {DIM}skipped (forja_observatory.py not found){RESET}")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(observatory_script), "report"],
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
        if "evals.html" in line:
            print(f"  {line.strip()}")
            return

    if result.returncode == 0:
        print(f"  {DIM}report generated{RESET}")
    else:
        print(f"  {DIM}report generation failed (non-blocking){RESET}")


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
    except OSError:
        pass


def run_forja(prd_path: str | None = None) -> bool:
    """Run the full Forja pipeline: init → plan → build.

    One command to rule them all.  Missing scaffolding is created
    automatically (``run_init``), a placeholder PRD triggers the
    interactive planner (``run_plan``), and then the build pipeline
    runs to completion.
    """
    # ── PID lock: prevent concurrent runs ──
    if not _acquire_pid_lock():
        return False

    try:
        return _run_forja_inner(prd_path)
    finally:
        _release_pid_lock()


def _run_forja_inner(prd_path: str | None = None) -> bool:
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

    if not prd.exists() or prd.stat().st_size == 0:
        print(f"{RED}  Error: Write your PRD in {prd} first.{RESET}")
        return False

    # Load env
    load_dotenv()

    # Check claude is installed
    if shutil.which("claude") is None:
        print(f"{RED}  Error: Claude Code not found. Install: npm install -g @anthropic-ai/claude-code{RESET}")
        return False

    # ── Banner ──
    prd_lines = prd.read_text().strip().split("\n")
    prd_title = prd_lines[0].lstrip("# ").strip() if prd_lines else "Unknown"

    print()
    print(f"{BOLD}{CYAN}  \u2500\u2500 Forja \u2500\u2500{RESET}")
    print(f"{DIM}  PRD: {prd_title}{RESET}")
    print(f"{DIM}  Pipeline: spec-review \u2192 build \u2192 tests \u2192 outcome \u2192 learnings \u2192 observatory{RESET}")

    pipeline_start = time.time()

    # ── Phase 0: Spec Review (informational + enrich) ──
    _phase_header(0, "Spec Review", 6)
    _run_spec_review(str(prd))

    # ── Phase 1: Context Injection ──
    _phase_header(1, "Context Injection", 6)
    _inject_context_into_claude_md()

    # ── Launch Observatory Live (background) ──
    observatory_proc = _start_observatory_live()

    # ── Clean artifacts ──
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
    _phase_header(2, "Build (Claude Code)", 6)

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
                    except OSError:
                        pass
                break
            time.sleep(1)

        returncode = proc.returncode or 0

    except KeyboardInterrupt:
        print(f"\n{YELLOW}  Interrupted by user.{RESET}")
        stop_event.set()
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

    # ── Phase 3: Project Tests (informational) ──
    _phase_header(3, "Project Tests", 6)
    _run_project_tests(Path.cwd())

    # ── Phase 4: Outcome Evaluation (informational) ──
    _phase_header(4, "Outcome Evaluation", 6)
    _run_outcome(str(prd))

    # ── Phase 5: Extract Learnings (informational) ──
    _phase_header(5, "Extract Learnings", 6)
    _run_learnings_extract()
    _run_learnings_apply()

    # ── Phase 6: Observatory (informational) ──
    _phase_header(6, "Observatory Report", 6)
    _run_observatory()

    # ── Final summary ──
    total_elapsed = time.time() - pipeline_start
    print()
    print(f"  {'=' * 40}")

    if build_ok:
        print(f"{GREEN}{BOLD}  \u2714 Forja complete{RESET}")
    else:
        print(f"{RED}{BOLD}  \u2718 Forja finished with errors{RESET}")

    print(f"{DIM}  Total time: {_format_duration(total_elapsed)}{RESET}")
    if total > 0:
        feat_summary = f"{DIM}  Features: {passed}/{total} passed"
        if blocked:
            feat_summary += f", {blocked} blocked"
        feat_summary += f"{RESET}"
        print(feat_summary)
    print()
    print(f"  Next steps:")
    print(f"    forja status    - feature details")
    print(f"    forja report    - metrics dashboard")
    print()

    return build_ok
