"""Forja projects — multi-project registry and portfolio dashboard.

Global registry lives at ``~/.forja/projects.toml`` (plain-text, no
external dependencies).  Each entry maps a short *name* to a filesystem
path where a Forja project lives.

An *active* project pointer is stored in ``~/.forja/active`` (single line)
so that ``forja run --project`` can operate from any directory.

Design principles:
- Zero external deps (stdlib only — no tomllib on 3.10, so we use JSON).
- File format: ``~/.forja/projects.json`` — simple, round-trippable.
- Reads are defensive (missing/corrupt file → empty registry).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forja.constants import (
    CLAUDE_MD,
    FORJA_DIR,
    FORJA_TOOLS,
    PRD_PATH,
    TEAMMATES_DIR,
)
from forja.utils import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    PASS_ICON,
    FAIL_ICON,
    WARN_ICON,
    safe_read_json,
)


# ── Paths ────────────────────────────────────────────────────────────

GLOBAL_DIR = Path.home() / ".forja"
REGISTRY_PATH = GLOBAL_DIR / "projects.json"
ACTIVE_PATH = GLOBAL_DIR / "active"


# ── Registry I/O ─────────────────────────────────────────────────────

def _read_registry() -> dict[str, Any]:
    """Return the full registry dict.  Empty dict on any failure."""
    if not REGISTRY_PATH.exists():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_registry(data: dict[str, Any]) -> None:
    """Persist registry to disk."""
    GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_active() -> str:
    """Return name of currently active project, or empty string."""
    if not ACTIVE_PATH.exists():
        return ""
    try:
        return ACTIVE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_active(name: str) -> None:
    """Persist active project name."""
    GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_PATH.write_text(name + "\n", encoding="utf-8")


def _read_previous() -> str:
    """Return name of previously active project."""
    prev_path = GLOBAL_DIR / "previous"
    if not prev_path.exists():
        return ""
    try:
        return prev_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_previous(name: str) -> None:
    """Persist previous project name (for ``select -``)."""
    GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    (GLOBAL_DIR / "previous").write_text(name + "\n", encoding="utf-8")


# ── Project detection ────────────────────────────────────────────────

def _is_forja_project(path: Path) -> bool:
    """Check if the given path contains a Forja project."""
    return (path / str(CLAUDE_MD)).exists() or (path / str(FORJA_TOOLS)).is_dir()


def _derive_name(path: Path) -> str:
    """Derive a project name from its directory path."""
    return path.resolve().name


# ── Health inspection ────────────────────────────────────────────────

def _inspect_health(project_path: Path) -> dict[str, Any]:
    """Gather health metrics from a project directory.

    Returns dict with: exists, is_forja, last_run, coverage, features_passed,
    features_total, test_count.
    """
    info: dict[str, Any] = {
        "exists": project_path.is_dir(),
        "is_forja": False,
        "last_run": None,
        "coverage": None,
        "features_passed": 0,
        "features_total": 0,
        "status_icon": "—",
        "status_label": "not initialized",
    }

    if not info["exists"]:
        info["status_icon"] = FAIL_ICON
        info["status_label"] = "directory missing"
        return info

    forja_dir = project_path / str(FORJA_DIR)
    info["is_forja"] = _is_forja_project(project_path)

    if not info["is_forja"]:
        info["status_label"] = "not initialized"
        return info

    # ── Last run timestamp (from outcome-report or event-stream) ─────
    outcome_path = forja_dir / "outcome-report.json"
    event_stream = forja_dir / "event-stream.jsonl"
    last_mtime = None

    for artifact in [outcome_path, event_stream]:
        if artifact.exists():
            try:
                mtime = artifact.stat().st_mtime
                if last_mtime is None or mtime > last_mtime:
                    last_mtime = mtime
            except OSError:
                pass

    if last_mtime:
        info["last_run"] = datetime.fromtimestamp(last_mtime, tz=timezone.utc)

    # ── Coverage from outcome report ─────────────────────────────────
    outcome = safe_read_json(outcome_path)
    if outcome and isinstance(outcome, dict):
        cov = outcome.get("coverage_pct") or outcome.get("coverage")
        if isinstance(cov, (int, float)):
            info["coverage"] = int(cov)

    # ── Feature counts from teammates dir ────────────────────────────
    teammates_dir = project_path / str(TEAMMATES_DIR)
    if teammates_dir.is_dir():
        total = 0
        passed = 0
        try:
            for subdir in teammates_dir.iterdir():
                if not subdir.is_dir():
                    continue
                feat_path = subdir / "features.json"
                feat_data = safe_read_json(feat_path)
                if feat_data and isinstance(feat_data, dict):
                    features = feat_data.get("features", [])
                    for f in features:
                        total += 1
                        if isinstance(f, dict) and f.get("status") == "passed":
                            passed += 1
        except OSError:
            pass
        info["features_passed"] = passed
        info["features_total"] = total

    # ── Derive status ────────────────────────────────────────────────
    if info["features_total"] == 0 and info["last_run"] is None:
        info["status_icon"] = WARN_ICON
        info["status_label"] = "not built"
    elif info["features_total"] > 0 and info["features_passed"] == info["features_total"]:
        info["status_icon"] = PASS_ICON
        pct = info["coverage"] or 100
        info["status_label"] = f"{info['features_passed']}/{info['features_total']} features ({pct}%)"
    elif info["features_total"] > 0:
        info["status_icon"] = FAIL_ICON
        info["status_label"] = f"{info['features_passed']}/{info['features_total']} features"
    elif info["last_run"]:
        info["status_icon"] = WARN_ICON
        info["status_label"] = "built (no features tracked)"
    else:
        info["status_icon"] = WARN_ICON
        info["status_label"] = "not built"

    return info


def _format_ago(dt: datetime | None) -> str:
    """Format a datetime as a human-readable 'N ago' string."""
    if dt is None:
        return "—"
    now = datetime.now(tz=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    return dt.strftime("%Y-%m-%d")


# ── Commands ─────────────────────────────────────────────────────────

def project_add(path_str: str, name: str | None = None) -> bool:
    """Register a project in the global registry."""
    path = Path(path_str).resolve()

    if not path.is_dir():
        print(f"{RED}  Error: '{path}' is not a directory.{RESET}")
        return False

    if not _is_forja_project(path):
        print(f"{YELLOW}  Warning: '{path}' doesn't look like a Forja project.{RESET}")
        print(f"{DIM}  (no CLAUDE.md or .forja-tools found — registering anyway){RESET}")

    project_name = name or _derive_name(path)
    registry = _read_registry()

    # Check for duplicate name
    if project_name in registry:
        existing = registry[project_name].get("path", "?")
        if existing == str(path):
            print(f"{DIM}  '{project_name}' already registered.{RESET}")
            return True
        print(f"{YELLOW}  '{project_name}' already exists → {existing}{RESET}")
        print(f"{DIM}  Use --name to register with a different alias.{RESET}")
        return False

    registry[project_name] = {
        "path": str(path),
        "created": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
    }
    _write_registry(registry)
    print(f"{GREEN}  {PASS_ICON} Registered '{project_name}' → {path}{RESET}")

    # If no active project, set this one
    if not _read_active():
        _write_active(project_name)
        print(f"{DIM}  (set as active project){RESET}")

    return True


def project_remove(name: str) -> bool:
    """Unregister a project (does NOT delete files)."""
    registry = _read_registry()
    if name not in registry:
        print(f"{RED}  Error: '{name}' not found in registry.{RESET}")
        _suggest_similar(name, registry)
        return False

    path = registry[name].get("path", "?")
    del registry[name]
    _write_registry(registry)
    print(f"{GREEN}  {PASS_ICON} Removed '{name}' ({path}){RESET}")
    print(f"{DIM}  (project files not deleted){RESET}")

    # Clear active if it was the removed project
    if _read_active() == name:
        _write_active("")
        print(f"{DIM}  (active project cleared){RESET}")

    return True


def project_select(name: str) -> bool:
    """Set the active project."""
    registry = _read_registry()

    # Handle "select -" for toggle
    if name == "-":
        prev = _read_previous()
        if not prev:
            print(f"{YELLOW}  No previous project to switch to.{RESET}")
            return False
        name = prev

    if name not in registry:
        print(f"{RED}  Error: '{name}' not found in registry.{RESET}")
        _suggest_similar(name, registry)
        return False

    current = _read_active()
    if current == name:
        print(f"{DIM}  '{name}' is already the active project.{RESET}")
        return True

    if current:
        _write_previous(current)
    _write_active(name)

    path = registry[name].get("path", "?")
    print(f"{GREEN}  {PASS_ICON} Active project: {BOLD}{name}{RESET}{GREEN} → {path}{RESET}")
    return True


def project_show() -> bool:
    """Show the currently active project."""
    active = _read_active()
    if not active:
        print(f"{DIM}  No active project. Use 'forja projects select <name>'.{RESET}")
        return True

    registry = _read_registry()
    if active not in registry:
        print(f"{YELLOW}  Active project '{active}' not in registry (stale pointer).{RESET}")
        return False

    path = registry[active].get("path", "?")
    print(f"  {BOLD}{active}{RESET} → {path}")

    # Show quick health
    health = _inspect_health(Path(path))
    print(f"  {health['status_icon']} {health['status_label']}")
    if health["last_run"]:
        print(f"  {DIM}Last run: {_format_ago(health['last_run'])}{RESET}")

    return True


def project_list() -> bool:
    """List all registered projects with health dashboard."""
    registry = _read_registry()
    active = _read_active()

    if not registry:
        print(f"{DIM}  No projects registered.{RESET}")
        print(f"{DIM}  Run 'forja projects add <path>' or 'forja init' to register one.{RESET}")
        return True

    # Compute column widths
    names = list(registry.keys())
    max_name = max(len(n) for n in names)
    max_name = max(max_name, 4)  # minimum "NAME" header

    # Header
    print()
    print(f"  {DIM}{'':2} {'NAME':<{max_name}}   {'LAST RUN':<12}  {'COVERAGE':<10}  STATUS{RESET}")
    print(f"  {DIM}{'':2} {'─' * max_name}   {'─' * 12}  {'─' * 10}  {'─' * 20}{RESET}")

    for name in sorted(names):
        entry = registry[name]
        path = Path(entry.get("path", ""))
        health = _inspect_health(path)

        # Active marker
        marker = f"{CYAN}▸{RESET}" if name == active else " "

        # Name (bold if active)
        name_str = f"{BOLD}{name}{RESET}" if name == active else name

        # Last run
        last_run = _format_ago(health.get("last_run"))

        # Coverage
        cov = health.get("coverage")
        if cov is not None:
            cov_str = f"{cov}%"
        else:
            cov_str = "—"

        # Status
        status_icon = health.get("status_icon", "—")
        status_label = health.get("status_label", "")

        # Pad name_str for alignment (account for ANSI codes in bold name)
        if name == active:
            padding = max_name - len(name)
            print(f"  {marker} {name_str}{' ' * padding}   {last_run:<12}  {cov_str:<10}  {status_icon} {status_label}")
        else:
            print(f"  {marker} {name:<{max_name}}   {last_run:<12}  {cov_str:<10}  {status_icon} {status_label}")

    print()
    total = len(registry)
    print(f"  {DIM}{total} project{'s' if total != 1 else ''} registered{RESET}")

    return True


def _suggest_similar(name: str, registry: dict) -> None:
    """If name is close to a registered project, suggest it."""
    for key in registry:
        if name.lower() in key.lower() or key.lower() in name.lower():
            print(f"{DIM}  Did you mean '{key}'?{RESET}")
            return


# ── Auto-register (called from forja init) ───────────────────────────

def auto_register(project_path: Path, name: str | None = None) -> None:
    """Silently register a project when ``forja init`` runs.

    Called from init.py.  Does not print errors (best-effort).
    """
    try:
        resolved = project_path.resolve()
        project_name = name or _derive_name(resolved)
        registry = _read_registry()

        # Don't overwrite an existing entry with a different path
        if project_name in registry:
            existing = registry[project_name].get("path", "")
            if existing == str(resolved):
                return  # already registered at same path
            # Name collision — append suffix
            i = 2
            while f"{project_name}-{i}" in registry:
                i += 1
            project_name = f"{project_name}-{i}"

        registry[project_name] = {
            "path": str(resolved),
            "created": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
        }
        _write_registry(registry)

        # Set as active if none
        if not _read_active():
            _write_active(project_name)
    except Exception:
        pass  # best-effort, never break init


# ── Resolve active project (for --project flag) ─────────────────────

def resolve_project_dir(project_name: str | None = None) -> Path | None:
    """Resolve a project name to its directory path.

    Priority:
    1. Explicit *project_name* argument (from ``--project`` flag)
    2. ``FORJA_PROJECT`` environment variable
    3. Current working directory (if it's a Forja project)
    4. Active project from ``~/.forja/active``

    Returns None if no project can be resolved.
    """
    # 1. Explicit name
    name = project_name or os.environ.get("FORJA_PROJECT", "").strip()

    if name:
        registry = _read_registry()
        if name not in registry:
            print(f"{RED}  Error: project '{name}' not found in registry.{RESET}")
            _suggest_similar(name, registry)
            return None
        path = Path(registry[name].get("path", ""))
        if not path.is_dir():
            print(f"{RED}  Error: project path '{path}' does not exist.{RESET}")
            return None
        return path

    # 2. Current directory
    cwd = Path.cwd()
    if _is_forja_project(cwd):
        return cwd

    # 3. Active project
    active = _read_active()
    if active:
        registry = _read_registry()
        if active in registry:
            path = Path(registry[active].get("path", ""))
            if path.is_dir():
                return path

    return None


# ── Dispatch ─────────────────────────────────────────────────────────

def run_projects(
    action: str = "ls",
    path: str | None = None,
    name: str | None = None,
) -> bool:
    """Main entry point for ``forja projects`` subcommand."""
    if action in ("ls", "list"):
        return project_list()
    elif action == "add":
        if not path:
            print(f"{RED}  Error: 'forja projects add' requires a path.{RESET}")
            return False
        return project_add(path, name=name)
    elif action == "remove":
        if not name and not path:
            print(f"{RED}  Error: 'forja projects remove' requires a project name.{RESET}")
            return False
        return project_remove(name or path or "")
    elif action == "select":
        if not name and not path:
            print(f"{RED}  Error: 'forja projects select' requires a project name.{RESET}")
            return False
        return project_select(name or path or "")
    elif action == "show":
        return project_show()
    else:
        print(f"{RED}  Unknown action: '{action}'{RESET}")
        print(f"{DIM}  Available: ls, add, remove, select, show{RESET}")
        return False
