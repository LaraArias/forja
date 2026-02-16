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

from forja_utils import PASS_ICON as PASS, FAIL_ICON as FAIL, WARN_ICON as WARN

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
            except (json.JSONDecodeError, OSError):
                pass
    single = Path(".forja") / "crossmodel-report.json"
    if single.exists() and not issues:
        try:
            data = json.loads(single.read_text(encoding="utf-8"))
            issues = data.get("issues", data.get("findings", []))
        except (json.JSONDecodeError, OSError):
            pass
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
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass
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
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
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
        except OSError:
            pass
    return stats, total_files, total_lines


# ── Metrics computation ──────────────────────────────────────────────

def _compute_metrics(teammates, spec_review, plan_transcript,
                     crossmodel_issues, outcome, learnings, commits,
                     src_stats, total_files, total_lines,
                     feature_events=None):
    """Compute all metrics from raw data."""

    # ── Spec Review ──
    sr_gaps = spec_review.get("gaps_count", 0) if spec_review else 0
    sr_enrichments = len(spec_review.get("enrichment", [])) if spec_review else 0
    sr_passed = spec_review.get("passed", True) if spec_review else True
    sr_status = ("pass" if sr_passed else "fail") if spec_review else "skip"

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
        feats = t["features"]
        passed = [f for f in feats if f.get("status") == "passed"]
        blocked = [f for f in feats if f.get("status") == "blocked"]
        failed = [f for f in feats if f.get("status") not in ("passed", "blocked")]
        per_teammate[name] = {
            "total": len(feats),
            "passed": len(passed),
            "blocked": len(blocked),
            "failed": len(failed),
        }
        all_features.extend([{**f, "_teammate": name} for f in feats])

    total_features = len(all_features)
    total_passed = sum(1 for f in all_features if f.get("status") == "passed")
    total_blocked = sum(1 for f in all_features if f.get("status") == "blocked")
    total_failed = total_features - total_passed - total_blocked

    cycles_list = [f.get("cycles", 0) for f in all_features if f.get("cycles", 0) > 0]
    avg_cycles = round(sum(cycles_list) / len(cycles_list), 1) if cycles_list else 0

    # Per-feature cycle data
    feature_cycles = []
    for f in all_features:
        feature_cycles.append({
            "id": f.get("id", "?"),
            "teammate": f.get("_teammate", "?"),
            "cycles": f.get("cycles", 0),
            "passed": f.get("status") == "passed",
            "blocked": f.get("status") == "blocked",
            "description": f.get("description", ""),
            "created_at": f.get("created_at", ""),
            "passed_at": f.get("passed_at", ""),
        })

    # Timeline data (teammate start/end)
    teammate_timeline = []
    for t in teammates:
        name = t["teammate"]
        feats = t["features"]
        created_dates = [f.get("created_at", "") for f in feats if f.get("created_at")]
        passed_dates = [f.get("passed_at", "") for f in feats if f.get("passed_at")]
        start = min(created_dates) if created_dates else ""
        end = max(passed_dates) if passed_dates else ""
        teammate_timeline.append({"name": name, "start": start, "end": end})

    # Build time estimate from git commits
    total_time_minutes = 0
    if commits:
        timestamps = [c["timestamp"] for c in commits if c["timestamp"] > 0]
        if len(timestamps) >= 2:
            total_time_minutes = round((max(timestamps) - min(timestamps)) / 60)

    build_status = "pass" if total_passed == total_features and total_features > 0 else (
        "warn" if total_blocked > 0 and total_failed == 0 and total_features > 0 else (
        "fail" if total_features > 0 else "skip"
    ))

    # ── Cross-model ──
    cm_high = sum(1 for i in crossmodel_issues if i.get("severity", "").lower() == "high")
    cm_med = sum(1 for i in crossmodel_issues if i.get("severity", "").lower() == "medium")
    cm_low = len(crossmodel_issues) - cm_high - cm_med

    # ── Outcome ──
    outcome_coverage = outcome.get("coverage", 0) if outcome else 0
    outcome_met = outcome.get("met", []) if outcome else []
    outcome_unmet = outcome.get("unmet", []) if outcome else []
    outcome_status = "pass" if outcome and outcome_coverage >= 80 else (
        "fail" if outcome else "skip"
    )

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
        "outcome_met": outcome_met, "outcome_unmet": outcome_unmet,
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
        except (json.JSONDecodeError, OSError):
            pass
    return runs


# ── HTML helpers ─────────────────────────────────────────────────────

def _esc(s):
    """HTML-escape a string."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ── HTML dashboard ───────────────────────────────────────────────────

def _generate_html(metrics, all_runs, live_mode=False, elapsed_seconds=0):
    """Generate full pipeline dashboard HTML."""
    m = metrics

    # ── Prepare JSON data for embedding ──
    dashboard_data = {
        "per_teammate": m["per_teammate"],
        "feature_cycles": m["feature_cycles"],
        "teammate_timeline": m["teammate_timeline"],
        "crossmodel_issues": m["crossmodel_issues"][:30],
        "outcome_met": m["outcome_met"],
        "outcome_unmet": m["outcome_unmet"],
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
    }
    data_json = json.dumps(dashboard_data, ensure_ascii=False)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    auto_refresh = '<meta http-equiv="refresh" content="5">' if live_mode else ''

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{auto_refresh}
<title>Forja Observatory{' [LIVE]' if live_mode else ''}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root {{
  --bg: #0f172a; --surface: #1e293b; --border: #334155;
  --text: #e2e8f0; --dim: #94a3b8;
  --accent: #00E5B0; --success: #22c55e; --warning: #eab308; --error: #ef4444;
  --blue: #60a5fa; --purple: #a78bfa; --cyan: #22d3ee;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:var(--bg); color:var(--text); padding:24px; min-height:100vh; }}
h1 {{ font-size:1.8rem; margin-bottom:4px;
  background:linear-gradient(135deg,var(--accent),var(--cyan));
  -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
.subtitle {{ color:var(--dim); margin-bottom:24px; font-size:.9rem; }}
.section {{ margin-bottom:36px; }}
.section-title {{ font-size:1.3rem; font-weight:700; margin-bottom:16px;
  padding-left:12px; border-left:3px solid var(--accent); color:var(--text); }}

/* Pipeline summary cards */
.pipeline {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:24px; }}
.pipe-card {{ background:var(--surface); border:1px solid var(--border); border-radius:10px;
  padding:16px 20px; flex:1; min-width:180px; position:relative; overflow:hidden; }}
.pipe-card::before {{ content:''; position:absolute; top:0; left:0; right:0; height:3px; }}
.pipe-card.s-pass::before {{ background:var(--success); }}
.pipe-card.s-fail::before {{ background:var(--error); }}
.pipe-card.s-skip::before {{ background:var(--border); }}
.pipe-card .pipe-name {{ font-size:.75rem; color:var(--dim); margin-bottom:6px;
  text-transform:uppercase; letter-spacing:.5px; }}
.pipe-card .pipe-badge {{ display:inline-block; font-size:.65rem; font-weight:700;
  padding:2px 8px; border-radius:4px; margin-bottom:6px; text-transform:uppercase; }}
.badge-pass {{ background:rgba(34,197,94,.15); color:var(--success); }}
.badge-fail {{ background:rgba(239,68,68,.15); color:var(--error); }}
.badge-skip {{ background:rgba(148,163,184,.1); color:var(--dim); }}
.pipe-card .pipe-detail {{ font-size:.85rem; color:var(--text); }}

/* Grid layouts */
.grid-2 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); gap:20px; }}
.grid-3 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; }}
.panel {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:20px; }}
.panel h2 {{ font-size:1rem; color:var(--dim); margin-bottom:14px; border-bottom:1px solid var(--border);
  padding-bottom:8px; }}
.panel canvas {{ max-height:300px; }}

/* Mini stat cards */
.build-stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:10px; margin-bottom:20px; }}
.mini-stat {{ background:var(--surface); border:1px solid var(--border); border-radius:8px;
  padding:12px; text-align:center; }}
.mini-stat .val {{ font-size:1.4rem; font-weight:700; }}
.mini-stat .lbl {{ color:var(--dim); font-size:.72rem; margin-top:2px; text-transform:uppercase; }}
.mini-stat.accent .val {{ color:var(--accent); }}
.mini-stat.green .val {{ color:var(--success); }}
.mini-stat.blue .val {{ color:var(--blue); }}

/* Tables */
table {{ width:100%; border-collapse:collapse; font-size:.83rem; }}
th {{ text-align:left; padding:8px; border-bottom:2px solid var(--border); color:var(--dim);
  font-size:.72rem; text-transform:uppercase; letter-spacing:.5px; }}
td {{ padding:8px; border-bottom:1px solid var(--border); }}

/* Severity badges inline */
.sev {{ display:inline-block; font-size:.65rem; font-weight:700; padding:2px 8px;
  border-radius:4px; text-transform:uppercase; min-width:55px; text-align:center; }}
.sev-high {{ background:rgba(239,68,68,.15); color:var(--error); }}
.sev-medium {{ background:rgba(234,179,8,.15); color:var(--warning); }}
.sev-low {{ background:rgba(148,163,184,.1); color:var(--dim); }}

/* Tag badges */
.tag {{ display:inline-block; font-size:.65rem; font-weight:700; padding:2px 8px;
  border-radius:4px; text-transform:uppercase; min-width:75px; text-align:center; }}
.tag-FACT {{ background:rgba(34,197,94,.15); color:var(--success); }}
.tag-DECISION {{ background:rgba(96,165,250,.15); color:var(--blue); }}
.tag-ASSUMPTION {{ background:rgba(234,179,8,.15); color:var(--warning); }}

/* Expert cards */
.experts {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px; }}
.expert-card {{ background:var(--bg); border:1px solid var(--border); border-radius:8px;
  padding:14px; flex:1; min-width:200px; }}
.expert-card .exp-name {{ font-weight:600; color:var(--accent); font-size:.9rem; }}
.expert-card .exp-field {{ color:var(--dim); font-size:.78rem; margin:4px 0; }}
.expert-card .exp-persp {{ font-size:.8rem; color:var(--text); opacity:.85; line-height:1.4; }}

/* Outcome bar */
.progress-track {{ background:var(--border); border-radius:6px; height:24px; overflow:hidden; margin:8px 0; }}
.progress-fill {{ height:100%; border-radius:6px; transition:width .3s; }}
.progress-label {{ font-size:.8rem; color:var(--dim); margin-top:4px; }}

/* Requirement lists */
.req-cols {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:12px; }}
.req-list {{ list-style:none; padding:0; }}
.req-list li {{ padding:5px 0; font-size:.82rem; display:flex; align-items:flex-start; gap:6px; }}
.req-list .icon-met {{ color:var(--success); flex-shrink:0; }}
.req-list .icon-unmet {{ color:var(--error); flex-shrink:0; }}

/* Assumption density bar */
.density-bar {{ display:flex; height:28px; border-radius:6px; overflow:hidden; margin:8px 0; }}
.density-bar .seg {{ display:flex; align-items:center; justify-content:center;
  font-size:.7rem; font-weight:600; color:#0f172a; }}

/* Learnings list */
.learning-group {{ margin-bottom:16px; }}
.learning-group h3 {{ font-size:.85rem; color:var(--accent); margin-bottom:8px;
  text-transform:capitalize; }}
.learning-entry {{ padding:6px 0; font-size:.82rem; display:flex; gap:8px; align-items:flex-start;
  border-bottom:1px solid rgba(51,65,85,.5); }}
.learning-entry:last-child {{ border-bottom:none; }}

/* No-data placeholder */
.no-data {{ color:var(--dim); font-size:.85rem; text-align:center; padding:24px;
  font-style:italic; }}
.no-data::before {{ content:'\\2014  '; }}

/* Info box */
.info-box {{ background:rgba(0,229,176,.06); border:1px solid rgba(0,229,176,.2);
  border-radius:8px; padding:14px 18px; font-size:.82rem; color:var(--dim); line-height:1.5; }}
.info-box strong {{ color:var(--accent); }}

/* Live banner */
.live-banner {{ background:rgba(0,229,176,.1); border:1px solid rgba(0,229,176,.3);
  border-radius:10px; padding:14px 20px; margin-bottom:24px;
  display:flex; align-items:center; gap:14px; flex-wrap:wrap; }}
.live-dot {{ width:10px; height:10px; border-radius:50%; background:var(--accent);
  animation:pulse 1.5s ease-in-out infinite; flex-shrink:0; }}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.3}} }}
.live-banner .live-label {{ font-weight:700; color:var(--accent); font-size:.9rem; }}
.live-banner .live-info {{ color:var(--dim); font-size:.82rem; }}
.live-banner .live-elapsed {{ font-family:monospace; color:var(--text); font-size:1rem;
  font-weight:600; margin-left:auto; }}

/* Footer */
.footer {{ text-align:center; color:var(--dim); font-size:.72rem; margin-top:40px;
  padding-top:16px; border-top:1px solid var(--border); }}

@media (max-width:600px) {{
  .grid-2 {{ grid-template-columns:1fr; }}
  .grid-3 {{ grid-template-columns:1fr; }}
  .pipeline {{ flex-direction:column; }}
  .build-stats {{ grid-template-columns:repeat(2,1fr); }}
  .req-cols {{ grid-template-columns:1fr; }}
  body {{ padding:12px; }}
}}
</style>
</head>
<body>

<h1>Forja Observatory</h1>
<p class="subtitle">Full pipeline metrics dashboard</p>

<!-- All sections rendered by JS from embedded data -->
<div id="app"></div>

<script>
const D = {data_json};

// ── Helpers ──
const esc = s => String(s).replace(/&/g,'&amp;').replace(/\\x3c/g,'&lt;').replace(/>/g,'&gt;');
const sevClass = s => ({{high:'sev-high',medium:'sev-medium',low:'sev-low'}})[String(s).toLowerCase()]||'sev-low';
const badgeCls = s => s==='pass'?'badge-pass':s==='fail'?'badge-fail':'badge-skip';
const cardCls = s => s==='pass'?'s-pass':s==='fail'?'s-fail':'s-skip';
const badgeText = s => s==='pass'?'PASS':s==='fail'?'FAIL':'SKIPPED';
const fmtTime = m => m>=60?Math.floor(m/60)+'h '+(m%60)+'m':m+'m';
const pctColor = p => p>=80?'var(--success)':p>=60?'var(--warning)':'var(--error)';
const cycleColor = c => c<=1?'#22c55e':c<=2?'#eab308':'#ef4444';

// ── Section builders ──
function buildPipeline() {{
  const phases = [
    {{name:'Spec Review', status:D.sr_status,
      detail: D.sr_status!=='skip'
        ? `${{D.sr_gaps}} gaps found, ${{D.sr_enrichments}} enrichments added`
        : 'No data'}},
    {{name:'Plan Mode', status:D.plan_status,
      detail: D.plan_status!=='skip'
        ? `${{D.plan_experts.length}} experts, ${{D.plan_answers.length}} questions, ${{D.plan_facts}} FACT / ${{D.plan_decisions}} DECISION / ${{D.plan_assumptions}} ASSUMPTION`
        : 'Skipped'}},
    {{name:'Build', status:D.build_status,
      detail: D.build_status!=='skip'
        ? `${{D.total_passed}}/${{D.total_features}} passed` + (D.total_blocked > 0 ? `, ${{D.total_blocked}} blocked` : '') + `, ${{D.num_teammates}} teams, ${{fmtTime(D.total_time_minutes)}}`
        : 'No data'}},
    {{name:'Outcome', status:D.outcome_status,
      detail: D.outcome_status!=='skip'
        ? `${{D.outcome_coverage}}% coverage`
        : 'Skipped'}},
    {{name:'Learnings', status:D.learnings_status,
      detail: D.learnings_status!=='skip'
        ? `${{D.learnings_total}} total (${{D.learnings_high}} high, ${{D.learnings_med}} medium, ${{D.learnings_low}} low)`
        : 'None'}},
  ];
  let html = '<div class="section"><div class="section-title">Pipeline Summary</div><div class="pipeline">';
  for (const p of phases) {{
    html += `<div class="pipe-card ${{cardCls(p.status)}}">
      <div class="pipe-name">${{p.name}}</div>
      <span class="pipe-badge ${{badgeCls(p.status)}}">${{badgeText(p.status)}}</span>
      <div class="pipe-detail">${{p.detail}}</div>
    </div>`;
  }}
  // Outcome progress bar inside pipeline
  if (D.outcome_status !== 'skip') {{
    const oc = D.outcome_coverage;
    phases[3].detail += '';  // already set above
  }}
  html += '</div>';
  // Outcome inline progress bar
  if (D.outcome_status !== 'skip') {{
    const oc = D.outcome_coverage;
    html += `<div style="max-width:600px;margin:0 0 20px;">
      <div class="progress-track"><div class="progress-fill" style="width:${{oc}}%;background:${{pctColor(oc)}}"></div></div>
      <div class="progress-label">${{oc}}% outcome coverage &mdash; ${{D.outcome_met.length}} met, ${{D.outcome_unmet.length}} unmet</div>
    </div>`;
  }}
  html += '</div>';
  return html;
}}

function buildBuildDetails() {{
  let html = '<div class="section"><div class="section-title">Build Details</div>';

  // Mini stat cards
  html += '<div class="build-stats">';
  html += `<div class="mini-stat green"><div class="val">${{D.total_passed}}/${{D.total_features}}</div><div class="lbl">Passed</div></div>`;
  if (D.total_blocked > 0) html += `<div class="mini-stat" style="border-color:#f59e0b"><div class="val" style="color:#f59e0b">${{D.total_blocked}}</div><div class="lbl">Blocked</div></div>`;
  html += `<div class="mini-stat accent"><div class="val">${{D.avg_cycles}}</div><div class="lbl">Avg Cycles</div></div>`;
  html += `<div class="mini-stat blue"><div class="val">${{D.total_files}}</div><div class="lbl">Total Files</div></div>`;
  html += `<div class="mini-stat blue"><div class="val">${{D.total_lines.toLocaleString()}}</div><div class="lbl">Lines of Code</div></div>`;
  html += `<div class="mini-stat accent"><div class="val">${{D.total_commits}}</div><div class="lbl">Commits</div></div>`;
  html += '</div>';

  const tmKeys = Object.keys(D.per_teammate);
  if (tmKeys.length === 0) {{
    html += '<div class="panel"><div class="no-data">No build data available</div></div>';
  }} else {{
    html += '<div class="grid-2">';
    html += '<div class="panel"><h2>Features by Teammate</h2><canvas id="chartTeammate"></canvas></div>';
    html += '<div class="panel"><h2>Cycles per Feature</h2><canvas id="chartCycles"></canvas></div>';
    html += '</div>';
  }}

  // Code Generated table
  const exts = Object.entries(D.src_stats).sort((a,b)=>b[1].lines-a[1].lines);
  if (exts.length > 0) {{
    html += '<div style="margin-top:20px"><div class="panel"><h2>Code Generated</h2><table>';
    html += '<thead><tr><th>Extension</th><th>Files</th><th>Lines</th></tr></thead><tbody>';
    for (const [ext, info] of exts) {{
      html += `<tr><td>${{esc(ext)}}</td><td>${{info.files}}</td><td>${{info.lines.toLocaleString()}}</td></tr>`;
    }}
    html += '</tbody></table></div></div>';
  }}

  html += '</div>';
  return html;
}}

function buildValidation() {{
  let html = '<div class="section"><div class="section-title">Validation</div><div class="grid-2">';

  // Cross-model findings
  html += '<div class="panel"><h2>Cross-Model Findings</h2>';
  if (D.crossmodel_issues.length === 0) {{
    html += '<div class="no-data">Cross-model review not run</div>';
  }} else {{
    html += `<p style="color:var(--dim);font-size:.8rem;margin-bottom:10px">${{D.crossmodel_issues.length}} issues (${{D.cm_high}} high, ${{D.cm_med}} medium, ${{D.cm_low}} low)</p>`;
    html += '<table><thead><tr><th>Severity</th><th>File</th><th>Description</th></tr></thead><tbody>';
    for (const issue of D.crossmodel_issues.slice(0,20)) {{
      const sev = (issue.severity||'low').toLowerCase();
      html += `<tr><td><span class="sev ${{sevClass(sev)}}">${{sev.toUpperCase()}}</span></td>`;
      html += `<td>${{esc(issue.file||'?')}}</td>`;
      html += `<td>${{esc(issue.description||issue.message||'?')}}</td></tr>`;
    }}
    html += '</tbody></table>';
  }}
  html += '</div>';

  // Outcome requirements
  html += '<div class="panel"><h2>Outcome Requirements</h2>';
  if (D.outcome_status === 'skip') {{
    html += '<div class="no-data">Outcome evaluation not run</div>';
  }} else {{
    html += '<div class="req-cols">';
    // Met
    html += '<div><h3 style="color:var(--success);font-size:.85rem;margin-bottom:8px">Met</h3><ul class="req-list">';
    if (D.outcome_met.length === 0) html += '<li style="color:var(--dim)">None</li>';
    for (const r of D.outcome_met.slice(0,15)) {{
      html += `<li><span class="icon-met">&#10003;</span>${{esc(r)}}</li>`;
    }}
    html += '</ul></div>';
    // Unmet
    html += '<div><h3 style="color:var(--error);font-size:.85rem;margin-bottom:8px">Unmet</h3><ul class="req-list">';
    if (D.outcome_unmet.length === 0) html += '<li style="color:var(--dim)">None</li>';
    for (const r of D.outcome_unmet.slice(0,15)) {{
      html += `<li><span class="icon-unmet">&#10007;</span>${{esc(r)}}</li>`;
    }}
    html += '</ul></div>';
    html += '</div>';
  }}
  html += '</div></div></div>';
  return html;
}}

function buildIntelligence() {{
  let html = '<div class="section"><div class="section-title">Intelligence</div>';

  // Plan Mode Expert Panel
  html += '<div class="panel" style="margin-bottom:20px">';
  html += '<h2>Plan Mode &mdash; Expert Panel</h2>';
  if (D.plan_status === 'skip') {{
    html += '<div class="no-data">Plan mode was not run</div>';
  }} else {{
    // Expert cards
    html += '<div class="experts">';
    for (const exp of D.plan_experts.slice(0,3)) {{
      html += `<div class="expert-card">
        <div class="exp-name">${{esc(exp.name||'?')}}</div>
        <div class="exp-field">${{esc(exp.field||'')}}</div>
        <div class="exp-persp">${{esc(exp.perspective||'')}}</div>
      </div>`;
    }}
    html += '</div>';

    // Q&A table
    html += `<h3 style="font-size:.9rem;color:var(--dim);margin:14px 0 8px">Questions &amp; Answers (${{D.plan_answers.length}})</h3>`;
    if (D.plan_answers.length > 0) {{
      html += '<table><thead><tr><th>Tag</th><th>Expert</th><th>Question</th><th>Answer</th></tr></thead><tbody>';
      for (const a of D.plan_answers) {{
        const tag = a.tag||'?';
        html += `<tr><td><span class="tag tag-${{tag}}">${{tag}}</span></td>`;
        html += `<td>${{esc(a.expert||'?')}}</td>`;
        html += `<td>${{esc(a.question||'?')}}</td>`;
        html += `<td>${{esc(String(a.answer||'').substring(0,150))}}</td></tr>`;
      }}
      html += '</tbody></table>';
    }}

    // Assumption Density bar
    const total_qa = D.plan_facts + D.plan_decisions + D.plan_assumptions;
    if (total_qa > 0) {{
      const pF = (D.plan_facts/total_qa*100).toFixed(0);
      const pD = (D.plan_decisions/total_qa*100).toFixed(0);
      const pA = (D.plan_assumptions/total_qa*100).toFixed(0);
      html += `<h3 style="font-size:.9rem;color:var(--dim);margin:14px 0 8px">Assumption Density</h3>`;
      html += `<div class="density-bar">`;
      if (D.plan_facts>0) html += `<div class="seg" style="width:${{pF}}%;background:var(--success)">${{pF}}% FACT</div>`;
      if (D.plan_decisions>0) html += `<div class="seg" style="width:${{pD}}%;background:var(--blue)">${{pD}}% DECISION</div>`;
      if (D.plan_assumptions>0) html += `<div class="seg" style="width:${{pA}}%;background:var(--warning)">${{pA}}% ASSUMPTION</div>`;
      html += '</div>';
    }}
  }}
  html += '</div>';

  // Learnings for next run
  html += '<div class="panel" style="margin-bottom:20px">';
  html += '<h2>Learnings for Next Run</h2>';
  const catKeys = Object.keys(D.learnings_by_cat);
  if (catKeys.length === 0) {{
    html += '<div class="no-data">No learnings extracted</div>';
  }} else {{
    for (const cat of catKeys) {{
      html += `<div class="learning-group"><h3>${{esc(cat)}}</h3>`;
      for (const entry of D.learnings_by_cat[cat].slice(0,10)) {{
        const sev = (entry.severity||'medium').toLowerCase();
        html += `<div class="learning-entry">
          <span class="sev ${{sevClass(sev)}}">${{sev.toUpperCase()}}</span>
          <span>${{esc(entry.learning)}}</span>
        </div>`;
      }}
      html += '</div>';
    }}
  }}
  html += '</div>';

  html += '</div>';
  return html;
}}

function buildProgress() {{
  let html = '<div class="section"><div class="section-title">Progress Tracking</div>';

  // Teammate timeline
  const tl = D.teammate_timeline.filter(t=>t.start);
  if (tl.length > 0) {{
    html += '<div class="panel" style="margin-bottom:20px"><h2>Teammate Timeline</h2>';
    html += '<canvas id="chartTimeline"></canvas></div>';
  }}

  // Feature event timeline
  const evts = D.feature_events || [];
  if (evts.length > 0) {{
    html += '<div class="panel" style="margin-bottom:20px"><h2>Feature Event Timeline</h2>';
    html += '<table><thead><tr><th>Time</th><th>Feature</th><th>Event</th><th>Cycle</th><th>Reason</th></tr></thead><tbody>';
    for (const ev of evts) {{
      const ts = ev.timestamp ? ev.timestamp.substring(11,19) : '?';
      const evtCls = ev.event==='passed'?'color:var(--success)':ev.event==='blocked'?'color:var(--warning)':'color:var(--error)';
      html += `<tr><td style="font-family:monospace;color:var(--dim)">${{ts}}</td>`;
      html += `<td>${{esc(ev.feature||'?')}}</td>`;
      html += `<td style="${{evtCls}};font-weight:600">${{esc(ev.event||'?')}}</td>`;
      html += `<td>${{ev.cycle||0}}</td>`;
      html += `<td style="color:var(--dim);font-size:.8rem">${{esc(ev.reason||'')}}</td></tr>`;
    }}
    html += '</tbody></table></div>';
  }}

  // Quality over time
  if (D.all_runs.length > 1) {{
    html += '<div class="panel" style="margin-bottom:20px"><h2>Quality Over Time</h2>';
    html += '<canvas id="chartDelta"></canvas></div>';
  }}

  // Explanation
  html += `<div class="info-box">
    <strong>How progress is measured:</strong> Progress is measured by feature completion, not time estimation.
    Each feature has a validation spec that must pass. The percentage shows
    <code>features_passed / features_total</code>. The runner polls features.json every 2 seconds
    to track completion in real time.
  </div>`;

  html += '</div>';
  return html;
}}

function buildLiveBanner() {{
  if (!D.live_mode) return '';
  const secs = D.elapsed_seconds;
  const h = Math.floor(secs/3600);
  const m = Math.floor((secs%3600)/60);
  const s = secs%60;
  const elapsed = h>0 ? `${{h}}h ${{m}}m ${{s}}s` : m>0 ? `${{m}}m ${{s}}s` : `${{s}}s`;
  const total = D.total_features;
  const passed = D.total_passed;
  const blocked = D.total_blocked || 0;
  const resolved = passed + blocked;
  const pct = total>0 ? Math.round(resolved/total*100) : 0;
  // Count active vs done teammates
  const tmKeys = Object.keys(D.per_teammate);
  const done = tmKeys.filter(k=>(D.per_teammate[k].passed+(D.per_teammate[k].blocked||0))===D.per_teammate[k].total && D.per_teammate[k].total>0);
  const active = tmKeys.filter(k=>(D.per_teammate[k].passed+(D.per_teammate[k].blocked||0))<D.per_teammate[k].total && D.per_teammate[k].total>0);
  const waiting = tmKeys.length - done.length - active.length;
  let teamInfo = '';
  if (active.length>0) teamInfo += `${{active.length}} active`;
  if (done.length>0) teamInfo += (teamInfo?', ':'')+`${{done.length}} done`;
  if (waiting>0) teamInfo += (teamInfo?', ':'')+`${{waiting}} waiting`;
  const blockedInfo = blocked > 0 ? `, ${{blocked}} blocked` : '';
  return `<div class="live-banner">
    <div class="live-dot"></div>
    <div>
      <div class="live-label">LIVE &mdash; Build in progress</div>
      <div class="live-info">${{passed}}/${{total}} passed${{blockedInfo}} (${{pct}}% resolved) &mdash; Teams: ${{teamInfo||'starting...'}}</div>
    </div>
    <div class="live-elapsed">${{elapsed}}</div>
  </div>`;
}}

// ── Render all sections ──
const app = document.getElementById('app');
app.innerHTML = buildLiveBanner() + buildPipeline() + buildBuildDetails() + buildValidation()
  + buildIntelligence() + buildProgress()
  + `<div class="footer">Generated by Forja Observatory &mdash; {generated_at}</div>`;

// ── Charts ──
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#334155';

// Features by Teammate (stacked bar)
const tmKeys = Object.keys(D.per_teammate);
if (tmKeys.length > 0 && document.getElementById('chartTeammate')) {{
  new Chart(document.getElementById('chartTeammate'), {{
    type: 'bar',
    data: {{
      labels: tmKeys,
      datasets: [
        {{ label: 'Passed', data: tmKeys.map(k=>D.per_teammate[k].passed), backgroundColor: '#22c55e' }},
        {{ label: 'Blocked', data: tmKeys.map(k=>D.per_teammate[k].blocked||0), backgroundColor: '#f59e0b' }},
        {{ label: 'Failed', data: tmKeys.map(k=>D.per_teammate[k].failed), backgroundColor: '#ef4444' }}
      ]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ position: 'top' }} }},
      scales: {{ x: {{ stacked: true }}, y: {{ stacked: true, beginAtZero: true, ticks: {{ stepSize: 1 }} }} }}
    }}
  }});
}}

// Cycles per Feature
const fc = D.feature_cycles;
if (fc.length > 0 && document.getElementById('chartCycles')) {{
  new Chart(document.getElementById('chartCycles'), {{
    type: 'bar',
    data: {{
      labels: fc.map(f=>f.id),
      datasets: [{{
        label: 'Cycles',
        data: fc.map(f=>f.cycles),
        backgroundColor: fc.map(f=>cycleColor(f.cycles))
      }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{ y: {{ beginAtZero: true, ticks: {{ stepSize: 1 }} }} }}
    }}
  }});
}}

// Teammate Timeline (horizontal bar)
const tl = D.teammate_timeline.filter(t=>t.start);
if (tl.length > 0 && document.getElementById('chartTimeline')) {{
  const parseT = s => {{ try {{ return new Date(s).getTime() }} catch {{ return null }} }};
  const allTimes = tl.flatMap(t=>[parseT(t.start),parseT(t.end)]).filter(Boolean);
  const minT = Math.min(...allTimes)||0;
  const maxT = Math.max(...allTimes)||1;
  const range = maxT - minT || 1;
  const starts = tl.map(t => {{ const s=parseT(t.start); return s?((s-minT)/range*100):0; }});
  const durations = tl.map(t => {{
    const s=parseT(t.start),e=parseT(t.end);
    return (s&&e)?((e-s)/range*100):0;
  }});
  new Chart(document.getElementById('chartTimeline'), {{
    type: 'bar',
    data: {{
      labels: tl.map(t=>t.name),
      datasets: [
        {{ label: 'offset', data: starts, backgroundColor: 'transparent', borderWidth: 0 }},
        {{ label: 'Duration', data: durations, backgroundColor: '#00E5B0' }}
      ]
    }},
    options: {{
      responsive: true, indexAxis: 'y',
      plugins: {{ legend: {{ display: false }},
        tooltip: {{ callbacks: {{ label: ctx=>ctx.datasetIndex===1?ctx.raw.toFixed(1)+'% of total':'' }} }} }},
      scales: {{ x: {{ stacked: true, display: false }}, y: {{ stacked: true }} }}
    }}
  }});
}}

// Quality Over Time (line chart)
if (D.all_runs.length > 1 && document.getElementById('chartDelta')) {{
  new Chart(document.getElementById('chartDelta'), {{
    type: 'line',
    data: {{
      labels: D.all_runs.map(r=>r.timestamp.substring(0,10)),
      datasets: [
        {{ label: 'Features Passed', data: D.all_runs.map(r=>r.total_passed),
          borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,.1)', fill: true,
          tension: 0.3, yAxisID: 'y' }},
        {{ label: 'Outcome Coverage %', data: D.all_runs.map(r=>r.outcome_coverage),
          borderColor: '#60a5fa', backgroundColor: 'rgba(96,165,250,.1)', fill: true,
          tension: 0.3, yAxisID: 'y1' }}
      ]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ position: 'top' }} }},
      scales: {{
        y: {{ beginAtZero: true, position: 'left', title: {{ display: true, text: 'Features' }} }},
        y1: {{ beginAtZero: true, position: 'right', max: 100,
          title: {{ display: true, text: '%' }}, grid: {{ drawOnChartArea: false }} }}
      }}
    }}
  }});
}}
__SCRIPT_END__
</body>
</html>"""
    # Escape </ inside script block so browser doesn't break on </div> etc.
    # Standard practice: </ -> <\/ inside <script> (valid JS, safe HTML).
    html = html.replace("__SCRIPT_END__", "</script>")
    # Find the script block (the second <script> — first is Chart.js CDN)
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

    print(f"""
Forja Observatory Report
  Pipeline:  spec-review({m['sr_status']}) plan({m['plan_status']}) build({m['build_status']}) outcome({m['outcome_status']})
  Features:  {m['total_passed']}/{m['total_features']} passed{f" ({m['total_blocked']} blocked)" if m['total_blocked'] > 0 else ""}
  Coverage:  {m['outcome_coverage']}%
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
        feature_events=feature_events,
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
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

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
            learnings = _read_learnings()
            feature_events = _read_feature_events()
            commits = _read_git_log()
            src_stats, total_files, total_lines = _read_src_stats()

            # Compute metrics (even if sparse — live mode shows partial data)
            metrics = _compute_metrics(
                teammates, spec_review, plan_transcript, crossmodel_issues,
                outcome, learnings, commits, src_stats, total_files, total_lines,
                feature_events=feature_events,
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
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                    pass

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
                    feature_events=feature_events,
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
        learnings = _read_learnings()
        feature_events = _read_feature_events()
        commits = _read_git_log()
        src_stats, total_files, total_lines = _read_src_stats()
        metrics = _compute_metrics(
            teammates, spec_review, plan_transcript, crossmodel_issues,
            outcome, learnings, commits, src_stats, total_files, total_lines,
            feature_events=feature_events,
        )
        all_runs = _load_all_runs()
        html_content = _generate_html(metrics, all_runs, live_mode=False)
        html_path.write_text(html_content, encoding="utf-8")
    except Exception as e:
        print(f"  writing final dashboard: {e}", file=sys.stderr)

    # Clean up PID file
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass

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
