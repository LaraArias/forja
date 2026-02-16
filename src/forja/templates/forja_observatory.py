#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja Observatory - full pipeline metrics and visual dashboard.

Reads all Forja artifacts (spec-review, plan, build, cross-model, outcome,
learnings, source stats) and generates a single-file HTML dashboard.

Usage:
    python3 .forja-tools/forja_observatory.py report
"""

import glob as glob_mod
import json
import os
import platform
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from forja_utils import PASS_ICON as PASS, FAIL_ICON as FAIL, WARN_ICON as WARN, Feature

OBSERVATORY_DIR = Path(".forja") / "observatory"


# ── Data collection ──────────────────────────────────────────────────

def _read_spec_review():
    """Read .forja/spec-enrichment.json."""
    p = Path(".forja") / "spec-enrichment.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_plan_transcript():
    """Read .forja/plan-transcript.json."""
    p = Path(".forja") / "plan-transcript.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_features():
    """Read all features.json from context/teammates/*/features.json."""
    teammates = []
    for fpath in sorted(glob_mod.glob("context/teammates/*/features.json")):
        p = Path(fpath)
        teammate_name = p.parent.name
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        teammates.append({
            "teammate": teammate_name,
            "features": data.get("features", []),
        })
    return teammates


def _read_crossmodel():
    """Read .forja/crossmodel/*.json or .forja/crossmodel-report.json."""
    issues = []
    cm_dir = Path(".forja") / "crossmodel"
    if cm_dir.is_dir():
        for fpath in sorted(cm_dir.glob("*.json")):
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                file_issues = data.get("issues", data.get("findings", []))
                for issue in file_issues:
                    issue.setdefault("file", fpath.stem)
                    issues.append(issue)
            except (json.JSONDecodeError, OSError) as exc:
                print(f"  could not read {fpath}: {exc}", file=sys.stderr)
    single = Path(".forja") / "crossmodel-report.json"
    if single.exists() and not issues:
        try:
            data = json.loads(single.read_text(encoding="utf-8"))
            issues = data.get("issues", data.get("findings", []))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  could not read crossmodel-report.json: {exc}", file=sys.stderr)
    return issues


def _read_outcome():
    """Read .forja/outcome-report.json."""
    p = Path(".forja") / "outcome-report.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_workflow():
    """Read .forja/workflow.json if it exists."""
    p = Path(".forja") / "workflow.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_learnings():
    """Read context/learnings/*.jsonl."""
    entries = []
    for fpath in sorted(glob_mod.glob("context/learnings/*.jsonl")):
        category = Path(fpath).stem
        try:
            for line in Path(fpath).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entry.setdefault("category", category)
                    entries.append(entry)
                except json.JSONDecodeError as exc:
                    print(f"  malformed JSONL in {fpath}: {exc}", file=sys.stderr)
        except OSError as exc:
            print(f"  could not read {fpath}: {exc}", file=sys.stderr)
    return entries


def _read_feature_events():
    """Read .forja/feature-events.jsonl timeline."""
    events = []
    p = Path(".forja") / "feature-events.jsonl"
    if not p.exists():
        return events
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"  malformed JSONL in feature-events.jsonl: {exc}", file=sys.stderr)
    except OSError as exc:
        print(f"  could not read feature-events.jsonl: {exc}", file=sys.stderr)
    return events


def _read_git_log():
    """Read git log."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H|%an|%at|%s"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    commits = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        commits.append({
            "hash": parts[0][:8],
            "author": parts[1],
            "timestamp": int(parts[2]) if parts[2].isdigit() else 0,
            "message": parts[3],
        })
    return commits


def _read_src_stats():
    """Count files and lines in src/ by extension."""
    stats = {}
    total_lines = 0
    total_files = 0
    src = Path("src")
    if not src.is_dir():
        return stats, total_files, total_lines
    for fpath in src.rglob("*"):
        if not fpath.is_file():
            continue
        ext = fpath.suffix or "no-ext"
        if ext in (".pyc", ".pyo"):
            continue
        total_files += 1
        stats.setdefault(ext, {"files": 0, "lines": 0})
        stats[ext]["files"] += 1
        try:
            line_count = len(fpath.read_text(encoding="utf-8", errors="replace").splitlines())
            stats[ext]["lines"] += line_count
            total_lines += line_count
        except OSError as exc:
            print(f"  could not count lines in {fpath}: {exc}", file=sys.stderr)
    return stats, total_files, total_lines


# ── Workflow helpers ─────────────────────────────────────────────────

def _build_workflow_phases(workflow, per_teammate):
    """Build workflow phase data for the dashboard."""
    if not workflow:
        return []
    phases = []
    for i, phase in enumerate(workflow.get("phases", [])):
        agent = phase.get("agent", "?")
        tm = per_teammate.get(agent, {})
        total = tm.get("total", 0)
        passed = tm.get("passed", 0)
        blocked = tm.get("blocked", 0)
        if total == 0:
            status = "waiting"
        elif passed == total:
            status = "done"
        elif blocked > 0:
            status = "blocked"
        elif passed > 0 or tm.get("failed", 0) > 0:
            status = "active"
        else:
            status = "waiting"
        phases.append({
            "order": i + 1,
            "agent": agent,
            "role": phase.get("role", agent),
            "output": phase.get("output", ""),
            "status": status,
        })
    return phases


# ── Metrics computation ──────────────────────────────────────────────

def _compute_metrics(teammates, spec_review, plan_transcript,
                     crossmodel_issues, outcome, learnings, commits,
                     src_stats, total_files, total_lines,
                     feature_events=None, workflow=None):
    """Compute all metrics from raw data."""

    # ── Spec Review ──
    sr_gaps = spec_review.get("gaps_count", 0) if spec_review else 0
    sr_enrichments = len(spec_review.get("enrichment", [])) if spec_review else 0
    sr_passed = spec_review.get("passed", True) if spec_review else True
    if not spec_review:
        sr_status = "skip"
    elif sr_passed:
        sr_status = "pass"
    elif sr_enrichments > 0:
        # Found gaps but resolved them via enrichment → warn, not fail
        sr_status = "warn"
    else:
        sr_status = "fail"

    # ── Plan Mode ──
    plan_experts = plan_transcript.get("experts", []) if plan_transcript else []
    plan_questions = plan_transcript.get("questions", []) if plan_transcript else []
    plan_answers = plan_transcript.get("answers", []) if plan_transcript else []
    plan_facts = sum(1 for a in plan_answers if a.get("tag") == "FACT")
    plan_decisions = sum(1 for a in plan_answers if a.get("tag") == "DECISION")
    plan_assumptions = sum(1 for a in plan_answers if a.get("tag") == "ASSUMPTION")
    plan_status = "pass" if plan_transcript else "skip"

    # ── Build / Features ──
    all_features = []
    per_teammate = {}
    for t in teammates:
        name = t["teammate"]
        feats = [Feature.from_dict(f) for f in t["features"]]
        passed = [f for f in feats if f.status == "passed"]
        blocked = [f for f in feats if f.status == "blocked"]
        failed = [f for f in feats if f.status not in ("passed", "blocked")]
        per_teammate[name] = {
            "total": len(feats),
            "passed": len(passed),
            "blocked": len(blocked),
            "failed": len(failed),
        }
        for f in feats:
            f._teammate = name
        all_features.extend(feats)

    total_features = len(all_features)
    total_passed = sum(1 for f in all_features if f.status == "passed")
    total_blocked = sum(1 for f in all_features if f.status == "blocked")
    total_failed = total_features - total_passed - total_blocked

    cycles_list = [f.cycles for f in all_features if f.cycles > 0]
    avg_cycles = round(sum(cycles_list) / len(cycles_list), 1) if cycles_list else 0

    # Per-feature cycle data
    feature_cycles = []
    for f in all_features:
        feature_cycles.append({
            "id": f.id or "?",
            "teammate": f._teammate or "?",
            "cycles": f.cycles,
            "status": f.status,
            "passed": f.status == "passed",
            "blocked": f.status == "blocked",
            "description": f.description,
            "created_at": f.created_at or "",
            "passed_at": f.passed_at or "",
        })

    # Roadmap data grouped by teammate
    roadmap = []
    for t in teammates:
        name = t["teammate"]
        feats = [Feature.from_dict(f) for f in t["features"]]
        roadmap.append({
            "teammate": name,
            "features": [
                {"id": f.id or "?", "description": f.description, "status": f.status}
                for f in feats
            ],
            "passed": sum(1 for f in feats if f.status == "passed"),
            "total": len(feats),
        })

    # Timeline data (teammate start/end)
    teammate_timeline = []
    for t in teammates:
        name = t["teammate"]
        feats = [Feature.from_dict(f) for f in t["features"]]
        created_dates = [f.created_at for f in feats if f.created_at]
        passed_dates = [f.passed_at for f in feats if f.passed_at]
        start = min(created_dates) if created_dates else ""
        end = max(passed_dates) if passed_dates else ""
        teammate_timeline.append({"name": name, "start": start, "end": end})

    # Build time estimate from git commits
    total_time_minutes = 0
    if commits:
        timestamps = [c["timestamp"] for c in commits if c["timestamp"] > 0]
        if len(timestamps) >= 2:
            total_time_minutes = round((max(timestamps) - min(timestamps)) / 60)

    if total_features == 0:
        build_status = "skip"
    elif total_passed == total_features:
        build_status = "pass"
    elif total_features > 0 and total_passed / total_features >= 0.5:
        # ≥50% passed → partial success (warn), not fail
        build_status = "warn"
    else:
        build_status = "fail"

    # ── Cross-model ──
    cm_high = sum(1 for i in crossmodel_issues if i.get("severity", "").lower() == "high")
    cm_med = sum(1 for i in crossmodel_issues if i.get("severity", "").lower() == "medium")
    cm_low = len(crossmodel_issues) - cm_high - cm_med

    # ── Outcome ──
    outcome_coverage = outcome.get("coverage", 0) if outcome else 0
    outcome_met = outcome.get("met", []) if outcome else []
    outcome_unmet = outcome.get("unmet", []) if outcome else []
    outcome_deferred = outcome.get("deferred", []) if outcome else []

    # Technical coverage: only counts technical requirements (met + unmet),
    # excluding deferred business items.
    tech_met = len(outcome_met)
    tech_unmet = len(outcome_unmet)
    tech_total = tech_met + tech_unmet
    outcome_tech_coverage = round(tech_met / tech_total * 100) if tech_total > 0 else outcome_coverage

    if not outcome:
        outcome_status = "skip"
    elif outcome_tech_coverage >= 80:
        outcome_status = "pass"
    elif outcome_tech_coverage >= 50:
        # Partial coverage → warn (not fail)
        outcome_status = "warn"
    else:
        outcome_status = "fail"

    # ── Learnings ──
    learnings_by_cat = {}
    learnings_high = 0
    learnings_med = 0
    learnings_low = 0
    for entry in learnings:
        cat = entry.get("category", "unknown")
        learnings_by_cat.setdefault(cat, [])
        learnings_by_cat[cat].append(entry)
        sev = entry.get("severity", "medium").lower()
        if sev == "high":
            learnings_high += 1
        elif sev == "medium":
            learnings_med += 1
        else:
            learnings_low += 1
    learnings_status = "pass" if learnings else "skip"

    return {
        # Pipeline phase statuses
        "sr_status": sr_status, "sr_gaps": sr_gaps, "sr_enrichments": sr_enrichments,
        "plan_status": plan_status, "plan_experts": plan_experts,
        "plan_questions": plan_questions, "plan_answers": plan_answers,
        "plan_facts": plan_facts, "plan_decisions": plan_decisions,
        "plan_assumptions": plan_assumptions,
        "build_status": build_status,
        "outcome_status": outcome_status, "outcome_coverage": outcome_coverage,
        "outcome_tech_coverage": outcome_tech_coverage,
        "outcome_met": outcome_met, "outcome_unmet": outcome_unmet,
        "outcome_deferred": outcome_deferred,
        "learnings_status": learnings_status,
        "learnings_total": len(learnings), "learnings_by_cat": learnings_by_cat,
        "learnings_high": learnings_high, "learnings_med": learnings_med,
        "learnings_low": learnings_low,
        # Build metrics
        "total_features": total_features, "total_passed": total_passed,
        "total_blocked": total_blocked, "total_failed": total_failed,
        "avg_cycles": avg_cycles,
        "per_teammate": per_teammate, "feature_cycles": feature_cycles,
        "teammate_timeline": teammate_timeline,
        "total_time_minutes": total_time_minutes,
        "num_teammates": len(teammates),
        # Cross-model
        "crossmodel_issues": crossmodel_issues,
        "cm_high": cm_high, "cm_med": cm_med, "cm_low": cm_low,
        # Code stats
        "src_stats": src_stats, "total_files": total_files,
        "total_lines": total_lines,
        # Git
        "total_commits": len(commits), "commits": commits[:20],
        # Feature events timeline
        "feature_events": feature_events or [],
        # Roadmap
        "roadmap": roadmap,
        # Workflow pipeline (when workflow.json exists)
        "workflow_phases": _build_workflow_phases(workflow, per_teammate) if workflow else [],
    }


# ── Report persistence ───────────────────────────────────────────────

def _save_run(metrics):
    """Save metrics snapshot (strip heavy fields)."""
    OBSERVATORY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_path = OBSERVATORY_DIR / f"run-{ts}.json"
    skip_keys = {"crossmodel_issues", "commits", "plan_answers",
                 "plan_experts", "plan_questions", "learnings_by_cat",
                 "feature_events"}
    save_metrics = {k: v for k, v in metrics.items() if k not in skip_keys}
    payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "metrics": save_metrics}
    run_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return run_path


def _load_all_runs():
    """Load all run-*.json files."""
    runs = []
    for fpath in sorted(glob_mod.glob(str(OBSERVATORY_DIR / "run-*.json"))):
        try:
            runs.append(json.loads(Path(fpath).read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  could not load run file {fpath}: {exc}", file=sys.stderr)
    return runs


# ── HTML helpers ─────────────────────────────────────────────────────

def _esc(s):
    """HTML-escape a string."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ── HTML dashboard ───────────────────────────────────────────────────

def _load_html_template():
    """Load observatory_template.html from the same directory as this script."""
    # Try alongside this script (works both in package and .forja-tools/)
    here = Path(__file__).parent / "observatory_template.html"
    if here.exists():
        return here.read_text(encoding="utf-8")
    # Fallback: .forja-tools/ directory (runtime context)
    tools = Path(".forja-tools") / "observatory_template.html"
    if tools.exists():
        return tools.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "observatory_template.html not found next to forja_observatory.py "
        "or in .forja-tools/. Run 'forja init --upgrade' to fix."
    )


def _prepare_dashboard_data(metrics, all_runs, live_mode=False, elapsed_seconds=0):
    """Build the JSON-serializable dict embedded into the HTML template."""
    m = metrics
    return {
        "per_teammate": m["per_teammate"],
        "feature_cycles": m["feature_cycles"],
        "teammate_timeline": m["teammate_timeline"],
        "crossmodel_issues": m["crossmodel_issues"][:30],
        "outcome_met": m["outcome_met"],
        "outcome_unmet": m["outcome_unmet"],
        "outcome_deferred": m["outcome_deferred"],
        "plan_experts": m["plan_experts"],
        "plan_answers": m["plan_answers"],
        "learnings_by_cat": {cat: [
            {"learning": e.get("learning", ""), "severity": e.get("severity", "medium"),
             "source": e.get("source", "")}
            for e in entries
        ] for cat, entries in m["learnings_by_cat"].items()},
        "src_stats": m["src_stats"],
        "all_runs": [{
            "timestamp": r.get("timestamp", ""),
            "total_passed": r.get("metrics", {}).get("total_passed", 0),
            "total_features": r.get("metrics", {}).get("total_features", 0),
            "outcome_coverage": r.get("metrics", {}).get("outcome_coverage", 0),
        } for r in all_runs],
        # Scalar metrics
        "sr_status": m["sr_status"], "sr_gaps": m["sr_gaps"],
        "sr_enrichments": m["sr_enrichments"],
        "plan_status": m["plan_status"], "plan_facts": m["plan_facts"],
        "plan_decisions": m["plan_decisions"], "plan_assumptions": m["plan_assumptions"],
        "build_status": m["build_status"],
        "total_features": m["total_features"], "total_passed": m["total_passed"],
        "total_blocked": m["total_blocked"], "total_failed": m["total_failed"],
        "num_teammates": m["num_teammates"],
        "total_time_minutes": m["total_time_minutes"],
        "outcome_status": m["outcome_status"], "outcome_coverage": m["outcome_coverage"],
        "outcome_tech_coverage": m["outcome_tech_coverage"],
        "learnings_status": m["learnings_status"], "learnings_total": m["learnings_total"],
        "learnings_high": m["learnings_high"], "learnings_med": m["learnings_med"],
        "learnings_low": m["learnings_low"],
        "cm_high": m["cm_high"], "cm_med": m["cm_med"], "cm_low": m["cm_low"],
        "total_files": m["total_files"], "total_lines": m["total_lines"],
        "total_commits": m["total_commits"], "avg_cycles": m["avg_cycles"],
        # Live mode extras
        "live_mode": live_mode, "elapsed_seconds": elapsed_seconds,
        # Feature event timeline
        "feature_events": m["feature_events"][:200],
        # Roadmap
        "roadmap": m["roadmap"],
        # Workflow pipeline
        "workflow_phases": m.get("workflow_phases", []),
    }


def _generate_html(metrics, all_runs, live_mode=False, elapsed_seconds=0):
    """Generate full pipeline dashboard HTML from external template."""
    template = _load_html_template()

    dashboard_data = _prepare_dashboard_data(metrics, all_runs, live_mode, elapsed_seconds)
    data_json = json.dumps(dashboard_data, ensure_ascii=False)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Replace placeholders
    html = template.replace("/*DATA_JSON*/null", data_json)
    html = html.replace(
        "<!--LIVE_META-->",
        '<meta http-equiv="refresh" content="5">' if live_mode else "",
    )
    html = html.replace("<!--LIVE_TITLE-->", " [LIVE]" if live_mode else "")
    html = html.replace("<!--GENERATED_AT-->", generated_at)

    # Escape </ inside <script> so browser doesn't break on </div> etc.
    # Standard practice: </ -> <\/ inside <script> (valid JS, safe HTML).
    idx1 = html.index("<script>", html.index("chart.js"))
    idx2 = html.index("</script>", idx1)
    script_body = html[idx1 + 8:idx2]
    safe_body = script_body.replace("</", "<\\/")
    html = html[:idx1 + 8] + safe_body + html[idx2:]
    return html


# ── Terminal summary ─────────────────────────────────────────────────

def _print_summary(metrics, html_path):
    """Print concise terminal summary."""
    m = metrics
    minutes = m["total_time_minutes"]
    time_display = f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"

    deferred_count = len(m.get('outcome_deferred', []))
    deferred_info = f" | Product items deferred: {deferred_count}" if deferred_count > 0 else ""

    print(f"""
Forja Observatory Report
  Pipeline:  spec-review({m['sr_status']}) plan({m['plan_status']}) build({m['build_status']}) outcome({m['outcome_status']})
  Features:  {m['total_passed']}/{m['total_features']} passed{f" ({m['total_blocked']} blocked)" if m['total_blocked'] > 0 else ""}
  Coverage:  Technical {m['outcome_tech_coverage']}%{deferred_info}
  Learnings: {m['learnings_total']} ({m['learnings_high']} high, {m['learnings_med']} med, {m['learnings_low']} low)
  Code:      {m['total_files']} files, {m['total_lines']:,} lines
  Time:      {time_display}
  Dashboard: {html_path}""")


# ── Main ─────────────────────────────────────────────────────────────

def cmd_report():
    """Collect all pipeline data, generate dashboard."""
    teammates = _read_features()
    spec_review = _read_spec_review()
    plan_transcript = _read_plan_transcript()
    crossmodel_issues = _read_crossmodel()
    outcome = _read_outcome()
    workflow = _read_workflow()
    learnings = _read_learnings()
    feature_events = _read_feature_events()
    commits = _read_git_log()
    src_stats, total_files, total_lines = _read_src_stats()

    if not teammates and not spec_review and not plan_transcript:
        print(f"{WARN} No Forja data found. Run 'forja run' first.")
        return False

    metrics = _compute_metrics(
        teammates, spec_review, plan_transcript, crossmodel_issues,
        outcome, learnings, commits, src_stats, total_files, total_lines,
        feature_events=feature_events, workflow=workflow,
    )

    run_path = _save_run(metrics)
    print(f"{PASS} Metrics saved to {run_path}")

    all_runs = _load_all_runs()

    OBSERVATORY_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OBSERVATORY_DIR / "evals.html"
    html_content = _generate_html(metrics, all_runs)
    html_path.write_text(html_content, encoding="utf-8")
    print(f"{PASS} Dashboard: {html_path}")

    _print_summary(metrics, html_path)

    try:
        if platform.system() == "Darwin":
            subprocess.run(["open", str(html_path)], timeout=5)
        elif platform.system() == "Linux":
            subprocess.run(["xdg-open", str(html_path)], timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"  could not open browser: {exc}", file=sys.stderr)

    return True


def cmd_live():
    """Live dashboard that auto-refreshes every 5 seconds during a build."""
    OBSERVATORY_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OBSERVATORY_DIR / "evals.html"
    pid_path = Path(".forja") / "observatory-live.pid"

    # Write PID so runner can kill us
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    # Graceful shutdown on SIGTERM
    stop = False

    def _handle_signal(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _handle_signal)

    start_time = time.time()
    opened_browser = False

    print(f"{PASS} Observatory live mode started (PID {os.getpid()})")
    print(f"  Dashboard: {html_path}")
    print(f"  Refreshing every 5 seconds. Ctrl+C to stop.")

    try:
        while not stop:
            elapsed = int(time.time() - start_time)

            # Collect current data
            teammates = _read_features()
            spec_review = _read_spec_review()
            plan_transcript = _read_plan_transcript()
            crossmodel_issues = _read_crossmodel()
            outcome = _read_outcome()
            workflow = _read_workflow()
            learnings = _read_learnings()
            feature_events = _read_feature_events()
            commits = _read_git_log()
            src_stats, total_files, total_lines = _read_src_stats()

            # Compute metrics (even if sparse — live mode shows partial data)
            metrics = _compute_metrics(
                teammates, spec_review, plan_transcript, crossmodel_issues,
                outcome, learnings, commits, src_stats, total_files, total_lines,
                feature_events=feature_events, workflow=workflow,
            )

            all_runs = _load_all_runs()

            # Generate HTML with live mode enabled
            html_content = _generate_html(metrics, all_runs,
                                          live_mode=True, elapsed_seconds=elapsed)
            html_path.write_text(html_content, encoding="utf-8")

            # Open browser on first generation
            if not opened_browser:
                opened_browser = True
                try:
                    if platform.system() == "Darwin":
                        subprocess.run(["open", str(html_path)], timeout=5)
                    elif platform.system() == "Linux":
                        subprocess.run(["xdg-open", str(html_path)], timeout=5)
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
                    print(f"  could not open browser: {exc}", file=sys.stderr)

            # Check if build is done (all features resolved: passed or blocked)
            total_f = metrics["total_features"]
            passed_f = metrics["total_passed"]
            blocked_f = metrics.get("total_blocked", 0)
            if total_f > 0 and (passed_f + blocked_f) == total_f:
                # Wait a couple more cycles to capture final state
                time.sleep(5)
                # Re-collect to get final outcome/learnings data
                teammates = _read_features()
                outcome = _read_outcome()
                learnings = _read_learnings()
                feature_events = _read_feature_events()
                crossmodel_issues = _read_crossmodel()
                src_stats, total_files, total_lines = _read_src_stats()
                commits = _read_git_log()
                metrics = _compute_metrics(
                    teammates, spec_review, plan_transcript, crossmodel_issues,
                    outcome, learnings, commits, src_stats, total_files, total_lines,
                    feature_events=feature_events, workflow=workflow,
                )
                # Write final HTML without auto-refresh
                html_content = _generate_html(metrics, all_runs,
                                              live_mode=False, elapsed_seconds=int(time.time() - start_time))
                html_path.write_text(html_content, encoding="utf-8")
                blocked_msg = f" ({blocked_f} blocked)" if blocked_f > 0 else ""
                print(f"\n{PASS} Build complete. {passed_f}/{total_f} features passed{blocked_msg}.")
                print(f"  Final dashboard: {html_path}")
                break

            time.sleep(5)

    except KeyboardInterrupt:
        print(f"\n{WARN} Live mode stopped by user.")

    # Write final static version (remove auto-refresh)
    try:
        teammates = _read_features()
        spec_review = _read_spec_review()
        plan_transcript = _read_plan_transcript()
        crossmodel_issues = _read_crossmodel()
        outcome = _read_outcome()
        workflow = _read_workflow()
        learnings = _read_learnings()
        feature_events = _read_feature_events()
        commits = _read_git_log()
        src_stats, total_files, total_lines = _read_src_stats()
        metrics = _compute_metrics(
            teammates, spec_review, plan_transcript, crossmodel_issues,
            outcome, learnings, commits, src_stats, total_files, total_lines,
            feature_events=feature_events, workflow=workflow,
        )
        all_runs = _load_all_runs()
        html_content = _generate_html(metrics, all_runs, live_mode=False)
        html_path.write_text(html_content, encoding="utf-8")
    except Exception as e:
        print(f"  writing final dashboard: {e}", file=sys.stderr)

    # Clean up PID file
    try:
        pid_path.unlink(missing_ok=True)
    except OSError as exc:
        print(f"  could not remove PID file: {exc}", file=sys.stderr)

    return True


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("report", "live"):
        print("Usage: python3 .forja-tools/forja_observatory.py report|live")
        sys.exit(1)
    if sys.argv[1] == "live":
        success = cmd_live()
    else:
        success = cmd_report()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
