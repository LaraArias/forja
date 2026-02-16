"""Forja init - scaffold a new Forja project."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from importlib import resources
from pathlib import Path

from forja.constants import CLAUDE_MD, FORJA_TOOLS, PRD_PATH
from forja.utils import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RESET,
    PASS_ICON,
    FAIL_ICON,
    WARN_ICON,
    load_dotenv,
    safe_read_json,
)
from forja.context_setup import interactive_context_setup, _flush_stdin

TEMPLATES = [
    # (source in templates/, target relative to project root)
    ("CLAUDE.md", "CLAUDE.md"),
    ("settings.local.json", ".claude/settings.local.json"),
    ("forja_utils.py", ".forja-tools/forja_utils.py"),
    ("forja_preflight.py", ".forja-tools/forja_preflight.py"),
    ("forja_validator.py", ".forja-tools/forja_validator.py"),
    ("forja_hook_filter.sh", ".forja-tools/forja_hook_filter.sh"),
    ("forja_features.py", ".forja-tools/forja_features.py"),
    ("forja_context.py", ".forja-tools/forja_context.py"),
    ("forja_crossmodel.py", ".forja-tools/forja_crossmodel.py"),
    ("forja_hardening.py", ".forja-tools/forja_hardening.py"),
    ("forja_observatory.py", ".forja-tools/forja_observatory.py"),
    ("observatory_template.html", ".forja-tools/observatory_template.html"),
    ("forja_specreview.py", ".forja-tools/forja_specreview.py"),
    ("forja_outcome.py", ".forja-tools/forja_outcome.py"),
    ("forja_learnings.py", ".forja-tools/forja_learnings.py"),
    ("forja_qa_playwright.py", ".forja-tools/forja_qa_playwright.py"),
    ("forja_handoff.py", ".forja-tools/forja_handoff.py"),
    ("forja.toml.default", "forja.toml"),
]

DIRS_TO_CREATE = [
    "context",
    "context/learnings",
    "context/company",
    "context/domains",
    "context/design-system",
    ".forja/logs",
    ".forja/backups",
    ".forja-tools",
    ".claude",
]

CONTEXT_READMES: dict[str, str] = {
    "context/company/README.md": (
        "# Company Context\n\n"
        "Add files here that describe your company, team, coding standards, and tech stack.\n\n"
        "Examples: company-overview.md, coding-standards.md, tech-stack.md\n\n"
        "Forja will use this context to make better architectural decisions.\n"
    ),
    "context/domains/README.md": (
        "# Domain Context\n\n"
        "Add folders here for each domain your project operates in.\n\n"
        "Examples: domains/fintech/regulations.md, domains/healthcare/hipaa.md\n\n"
        "Forja will use this context to ensure compliance and best practices.\n"
    ),
    "context/design-system/README.md": (
        "# Design System\n\n"
        "Add your design tokens, component library docs, and branding guidelines here.\n\n"
        "Examples: tokens.json, components.md, brand-guidelines.md\n\n"
        "Forja will use this to generate consistent UI.\n"
    ),
}

EXECUTABLE_TEMPLATES = {"forja_hook_filter.sh"}

EXISTING_MARKERS = [str(CLAUDE_MD), str(FORJA_TOOLS)]

AVAILABLE_SKILLS: dict[str, str] = {
    "landing-page": "Landing Page (conversion-optimized single page)",
    "api-backend": "API Backend (REST API with database)",
}

# Permissions scoped to project (written to .claude/settings.local.json)
PROJECT_PERMISSIONS = [
    "Bash(cd:*)",
    "Bash(python3:*)",
    "Bash(pip:*)",
    "Bash(git:*)",
    "Bash(cat:*)",
    "Bash(ls:*)",
    "Bash(mkdir:*)",
    "Bash(cp:*)",
    "Bash(mv:*)",
    "Bash(rm:*)",
    "Bash(find:*)",
    "Bash(grep:*)",
    "Bash(wc:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(pkill:*)",
    "Read(*)",
    "Write(*)",
    "Edit(*)",
]


def get_template(name: str) -> str:
    """Read a template file from the package's templates/ directory."""
    tmpl = resources.files("forja") / "templates" / name
    return tmpl.read_text(encoding="utf-8")


def _check_existing(target: Path) -> list[str]:
    """Check if target already has Forja files. Returns list of found markers."""
    return [m for m in EXISTING_MARKERS if (target / m).exists()]


def _prompt_overwrite(found: list[str]) -> bool:
    """Ask user if they want to overwrite existing files."""
    print(f"  {WARN_ICON} Existing Forja project detected:")
    for f in found:
        print(f"      - {f}")
    _flush_stdin()
    try:
        answer = input("\n  Overwrite existing files? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def _create_dirs(target: Path) -> None:
    """Create the Forja directory structure."""
    for d in DIRS_TO_CREATE:
        (target / d).mkdir(parents=True, exist_ok=True)


def _copy_templates(target: Path, overwrite: bool = False) -> None:
    """Copy template files to the target project."""
    for src_name, dest_rel in TEMPLATES:
        dest = target / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists() and not overwrite:
            print(f"  SKIP     {dest_rel} (already exists)")
            continue

        content = get_template(src_name)
        dest.write_text(content, encoding="utf-8")

        if src_name in EXECUTABLE_TEMPLATES:
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        action = "REPLACE" if dest.exists() and overwrite else "CREATE"
        print(f"  {action}  {dest_rel}")

    # Create context READMEs if they don't exist
    for readme_rel, readme_content in CONTEXT_READMES.items():
        readme_path = target / readme_rel
        if not readme_path.exists():
            readme_path.write_text(readme_content, encoding="utf-8")
            print(f"  CREATE   {readme_rel}")

    # Create empty prd.md if it doesn't exist
    prd = target / PRD_PATH
    if not prd.exists():
        prd.write_text("# PRD\n\nDescribe your project here.\n", encoding="utf-8")
        print(f"  CREATE   {PRD_PATH}")

    # Create .env from global config or placeholder
    dotenv = target / ".env"
    if not dotenv.exists():
        global_config = Path.home() / ".forja" / "config.env"
        if global_config.exists():
            import shutil
            shutil.copy2(str(global_config), str(dotenv))
            print("  CREATE   .env (copied from ~/.forja/config.env)")
        else:
            dotenv.write_text(
                "# Forja API Keys\nANTHROPIC_API_KEY=\nKIMI_API_KEY=\nSAPTIVA_API_KEY=\n",
                encoding="utf-8",
            )
            print("  CREATE   .env")
            print(f"  {WARN_ICON} Tip: run 'forja config' to set up your API keys globally")
        # Restrict .env to owner-only (contains API keys)
        dotenv.chmod(0o600)

    # Create .gitignore if it doesn't exist
    gitignore = target / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".env\n.forja/\n__pycache__/\n*.pyc\nartifacts/\n", encoding="utf-8")
        print("  CREATE   .gitignore")


def _configure_project_permissions(target: Path) -> None:
    """Write scoped permissions to project .claude/settings.local.json."""
    settings_path = target / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = safe_read_json(settings_path, default={}) or {}

    if "permissions" not in settings:
        settings["permissions"] = {}
    if "allow" not in settings["permissions"]:
        settings["permissions"]["allow"] = []

    existing = set(settings["permissions"]["allow"])
    added: list[str] = []
    for perm in PROJECT_PERMISSIONS:
        if perm not in existing:
            settings["permissions"]["allow"].append(perm)
            added.append(perm)

    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if added:
        print(f"  {PASS_ICON} Claude Code permissions configured for autonomous execution")
        for p in added:
            print(f"      + {p}")
    else:
        print(f"  {PASS_ICON} Claude Code permissions already configured")


def _init_git(target: Path) -> None:
    """Initialize git repo if .git/ doesn't exist."""
    if (target / ".git").exists():
        print(f"  {PASS_ICON} Git already initialized")
        return

    result = subprocess.run(
        ["git", "init"],
        cwd=str(target),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"  {PASS_ICON} Git initialized")
    else:
        print(f"  {FAIL_ICON} Error initializing git: {result.stderr.strip()}")


def _run_preflight(target: Path) -> bool | None:
    """Run preflight checks."""
    preflight = target / FORJA_TOOLS / "forja_preflight.py"
    if not preflight.exists():
        print(f"  {FAIL_ICON} forja_preflight.py not found")
        return None

    print()
    result = subprocess.run(
        [sys.executable, str(preflight)],
        cwd=str(target),
    )
    return result.returncode == 0


def _ask_skill() -> str | None:
    """Ask user which skill to use. Returns skill key or None."""
    print(f"\n{BOLD}── Project Type ──{RESET}")
    options = [
        ("Landing Page", "Conversion-optimized single page"),
        ("API Backend", "REST API with database"),
        ("Custom / General", "Generic Forja pipeline (no skill)"),
    ]

    print(f"\n  {BOLD}What are you building?{RESET}")
    for i, (label, _desc) in enumerate(options, 1):
        marker = f"{GREEN}({i}){RESET}" if i == 1 else f"  ({i})"
        print(f"    {marker} {label}")
    _flush_stdin()
    try:
        raw = input(f"  {BOLD}>{RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raw = ""
    idx = int(raw) if raw.isdigit() and 1 <= int(raw) <= 3 else 1
    skill_map = {1: "landing-page", 2: "api-backend", 3: None}
    return skill_map.get(idx)


def _copy_skill(target: Path, skill_name: str) -> None:
    """Copy skill agents.json and workflow.json to the project."""
    skill_dir = resources.files("forja") / "templates" / "skills" / skill_name
    dest_dir = target / FORJA_TOOLS

    # Copy agents.json → skill.json
    try:
        agents_src = skill_dir / "agents.json"
        content = agents_src.read_text(encoding="utf-8")
        (dest_dir / "skill.json").write_text(content, encoding="utf-8")
        print(f"  {PASS_ICON} Skill '{skill_name}' configured")
    except (OSError, TypeError):
        print(f"  {WARN_ICON} Skill template not found: {skill_name}")
        return

    # Copy workflow.json if it exists
    try:
        workflow_src = skill_dir / "workflow.json"
        content = workflow_src.read_text(encoding="utf-8")
        (dest_dir / "workflow.json").write_text(content, encoding="utf-8")
        print(f"  {PASS_ICON} Workflow pipeline configured")
    except (OSError, TypeError):
        pass  # workflow.json is optional for skills without pipelines


# ── Main entry point ─────────────────────────────────────────────────


_FORBIDDEN_INIT_DIRS = {
    "/", "/etc", "/var", "/usr", "/tmp", "/bin", "/sbin", "/lib",
    # macOS symlinks these under /private
    "/private/etc", "/private/var", "/private/tmp",
}


def run_init(directory: str = ".", force: bool = False, upgrade: bool = False) -> bool:
    """Main init entrypoint.

    Args:
        directory: Target project directory.
        force: Overwrite existing files without asking.
        upgrade: Only copy templates (skip context, git, skills, preflight).
    """
    target = Path(directory).resolve()
    home = Path.home().resolve()

    # Reject system directories and home root
    if str(target) in _FORBIDDEN_INIT_DIRS or target == home:
        print(f"{FAIL_ICON} Refusing to init in {target} — pick a project subdirectory.")
        return False

    if upgrade:
        return _run_upgrade(target)

    print(f"Initializing Forja in: {target}\n")

    # Check for existing project
    overwrite = force
    if not force:
        found = _check_existing(target)
        if found:
            overwrite = _prompt_overwrite(found)
            if not overwrite:
                print("\nInit cancelled.")
                return False
            print()

    # Step 1: Create directories
    print("── Structure ──")
    _create_dirs(target)

    # Step 2: Copy templates
    _copy_templates(target, overwrite=overwrite)

    # Step 3: Configure project permissions
    print("\n── Permissions ──")
    _configure_project_permissions(target)

    # Step 4: Initialize git
    print("\n── Git ──")
    _init_git(target)

    # Load .env so context_setup LLM calls can find API keys
    load_dotenv()

    # Step 5: Skill selection + context setup
    skill_name = _ask_skill()
    if skill_name:
        _copy_skill(target, skill_name)
        # Create artifacts/ for inter-agent handoffs
        (target / "artifacts").mkdir(exist_ok=True)

    # Step 6: Interactive context setup
    interactive_context_setup(target, skill_name)

    # Step 7: Run preflight
    print("\n── Preflight ──")
    _run_preflight(target)

    # Step 8: Scaffold complete
    print()
    print(f"{PASS_ICON} Forja initialized.")

    # Step 9: Auto-launch planning
    from forja.planner import run_plan  # local import to avoid circular dep

    print(f"\n{BOLD}── Starting Plan Mode ──{RESET}\n")
    plan_ok = run_plan(prd_path=str(target / PRD_PATH), _called_from_runner=False)
    if plan_ok:
        print(f"\n{PASS_ICON} Project initialized and PRD ready.")
        print(f"  Run {BOLD}forja run{RESET} to start the build.\n")
    else:
        print(f"\n{WARN_ICON} Planning skipped or cancelled.")
        print(f"  Write your PRD in context/prd.md, then run {BOLD}forja run{RESET}.\n")

    return True


def _run_upgrade(target: Path) -> bool:
    """Upgrade templates only — does not touch context/, .env, or git."""
    print(f"Upgrading Forja templates in: {target}\n")

    # Ensure .forja-tools exists
    (target / ".forja-tools").mkdir(parents=True, exist_ok=True)
    (target / ".claude").mkdir(parents=True, exist_ok=True)

    _copy_templates(target, overwrite=True)

    print(f"\n{PASS_ICON} Templates upgraded.")
    return True
