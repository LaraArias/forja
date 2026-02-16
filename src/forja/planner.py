#!/usr/bin/env python3
"""Forja Plan - interactive PRD enrichment via expert panel.

Calls Kimi to assemble a panel of 3 real-world experts relevant to the
project, who ask critical questions. The user answers interactively and
the final output is an enriched PRD ready for `forja run`.

Usage:
    forja plan [context/prd.md]
"""

from __future__ import annotations

import glob as glob_mod
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("forja")

from forja.constants import (
    CONTEXT_DIR, FORJA_DIR, LEARNINGS_DIR, PRD_PATH, STORE_DIR,
)
from forja.context_setup import _flush_stdin
from forja.utils import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    YELLOW,
    RESET,
    _call_anthropic_raw,
    call_llm,
    gather_context,
    load_dotenv,
    parse_json,
    safe_read_json,
)

EXPERT_COLORS = [CYAN, YELLOW, RED]

# ── Fallback questions if Kimi unavailable ──────────────────────────

FALLBACK_EXPERTS = [
    {"name": "Software Architect", "field": "System Design", "perspective": "Evaluating architecture and technical trade-offs."},
    {"name": "Product Strategist", "field": "Product Strategy", "perspective": "Evaluating viability and user experience."},
    {"name": "Security Engineer", "field": "Security & Edge Cases", "perspective": "Evaluating attack surface and edge cases."},
]

# Mandatory technical expert — injected when Kimi's panel has no tech voice.
TECHNICAL_EXPERT = {
    "name": "Build Feasibility Engineer",
    "field": "Build Tooling & Runtime Constraints",
    "perspective": (
        "You have VETO POWER over the tech stack. If the PRD specifies ANY technology "
        "that Claude Code cannot install (Redis, PostgreSQL, Docker, system services), "
        "you MUST override it. Your suggestion is not optional - it is a hard constraint."
    ),
}

FALLBACK_QUESTIONS = [
    {"id": 1, "expert_name": "Software Architect", "question": "What is the expected data volume and concurrent users?", "why": "Determines if current architecture scales or needs changes from the start.", "default": "MVP: <1000 users, <100K records. SQLite is sufficient."},
    {"id": 2, "expert_name": "Software Architect", "question": "How are errors and data validation handled?", "why": "Without clear validation rules, each developer invents their own.", "default": "Validation at API layer. Errors as JSON {detail: msg}. 400 for invalid input."},
    {"id": 3, "expert_name": "Product Strategist", "question": "Who is the main user and what is their critical flow?", "why": "Without this, the team optimizes for the wrong user.", "default": "Individual developer, flow: create -> list -> search -> edit."},
    {"id": 4, "expert_name": "Product Strategist", "question": "Is there search or filtering functionality?", "why": "Listing everything does not scale. Users expect to be able to search.", "default": "Basic search by title. Pagination with limit/offset."},
    {"id": 5, "expert_name": "Security Engineer", "question": "Is authentication or rate limiting required?", "why": "A public API without rate limiting is an abuse vector.", "default": "No auth for MVP. Basic rate limit: 100 req/min per IP."},
    {"id": 6, "expert_name": "Security Engineer", "question": "What are the input size limits?", "why": "Without limits, someone uploads 1GB in a text field.", "default": "Title: max 255 chars. Content: max 50KB. Body: max 100KB."},
    {"id": 7, "expert_name": "Software Architect", "question": "Can all dependencies be installed via pip/npm without system packages?", "why": "Claude Code builds autonomously and cannot run apt-get or brew.", "default": "All deps via pip. SQLite (built-in), no Docker, no external services."},
    {"id": 8, "expert_name": "Product Strategist", "question": "What is the expected response format?", "why": "Without a clear API contract, the frontend breaks with every change.", "default": "JSON with snake_case fields. Timestamps ISO-8601 UTC. IDs as UUID4 string."},
]

TECHNICAL_QUESTIONS = [
    {
        "expert_name": "Build Feasibility Engineer",
        "question": (
            "STACK OVERRIDE CHECK: Does the PRD specify any technology Claude Code cannot install? "
            "I must rewrite the Stack section to ONLY include pip/npm packages, SQLite, and built-in features."
        ),
        "why": (
            "Claude Code can only install packages via pip/npm. It CANNOT install Redis, PostgreSQL, "
            "MySQL, Docker, Kafka, RabbitMQ, Nginx, or any system service. If found, I will override: "
            "Redis -> Python dict + SQLite, PostgreSQL/MySQL -> SQLite via SQLAlchemy, "
            "Docker -> direct pip install, Socket.io+Redis -> in-memory adapter, "
            "Next.js ISR -> plain HTML + vanilla JS or Flask templates."
        ),
        "default": "Python + FastAPI + SQLite (single-process, pip-installable, no Docker needed).",
    },
    {
        "expert_name": "Build Feasibility Engineer",
        "question": "What external dependencies are needed and are they all installable via pip or npm?",
        "why": (
            "Claude Code cannot install system-level packages (apt, brew). Every dependency must be "
            "pip/npm installable. When I detect an incompatible stack: "
            "'STACK OVERRIDE: Replacing {original} with {alternative}. Reason: {reason}.'"
        ),
        "default": "All deps via pip. No system packages, no Docker, no external databases.",
    },
    {
        "expert_name": "Build Feasibility Engineer",
        "question": "What scope limitations should we set so Claude Code can finish in one session?",
        "why": "Claude Code works in a single session with context limits. Overly ambitious PRDs result in half-built projects.",
        "default": "MVP only: 3-5 endpoints or pages, no auth for v1, no CI/CD, no deployment config.",
    },
]


# ── Skill detection ──────────────────────────────────────────────

SKILL_EXPERT_GUIDANCE = {
    "landing-page": """The project is a LANDING PAGE. Experts should focus on:
- Copy, messaging, and tone of voice
- Page sections: hero, features, social proof, CTA, footer
- Visual design: colors, typography, imagery, whitespace
- Audience targeting: who visits, what convinces them to act
- CTA strategy: what action, what button text, what urgency
Do NOT ask about databases, APIs, deployment, or backend architecture.""",

    "api-backend": """The project is an API/BACKEND. Experts should focus on:
- Endpoint design and REST conventions
- Data model and relationships
- Business rules and validation
- Error handling and edge cases
- Authentication and security requirements
Do NOT ask about visual design, CSS, or frontend layout.""",

    "custom": """The project type is general. Experts should focus on the most relevant aspects based on the PRD content.""",
}

SKILL_PRD_CONSTRAINTS = {
    "landing-page": (
        "CRITICAL CONSTRAINT: The user selected 'Landing Page' as the project type.\n"
        "This means:\n"
        "- Output is a SINGLE index.html file with inline CSS and JS\n"
        "- NO backend, NO database, NO API, NO server\n"
        "- NO Kubernetes, NO Docker, NO Terraform, NO Helm\n"
        "- NO Next.js, NO NestJS, NO PostgreSQL, NO Redis\n"
        "- Stack is: HTML + CSS + JavaScript. That's it.\n"
        "- The page is a MARKETING SITE that explains and sells the product\n"
        "- Sections should be: Hero, How It Works, Features, Social Proof, CTA, Footer\n"
        "- The PRD describes WHAT THE PAGE SHOWS, not what the product does internally\n"
        "Generate a PRD for a static marketing landing page, NOT for the product being marketed."
    ),
    "api-backend": (
        "CRITICAL CONSTRAINT: The user selected 'API Backend' as the project type.\n"
        "Stack must be: Python + FastAPI + SQLite.\n"
        "No Docker, no Kubernetes, no external databases that need installation.\n"
        "Focus on endpoints, data models, validation, and error handling."
    ),
    "custom": "",
}


def _detect_skill() -> str:
    """Detect which skill is active. Returns 'landing-page', 'api-backend', or 'custom'."""
    for path in [Path(".forja/skill/agents.json"), Path(".forja-tools/skill.json")]:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # Handle dict format: {"skill": "landing-page", "agents": [...]}
                if isinstance(data, dict):
                    skill_field = data.get("skill", "")
                    if skill_field in ("landing-page", "api-backend"):
                        return skill_field
                    agents = data.get("agents", [])
                else:
                    agents = data if isinstance(data, list) else []
                agent_names = [a.get("name", "") for a in agents]
                if "frontend-builder" in agent_names or "seo-optimizer" in agent_names:
                    return "landing-page"
                if "database" in agent_names or "security" in agent_names:
                    return "api-backend"
            except Exception as exc:
                logger.debug("Failed to read skill file %s: %s", path, exc)
    return "custom"


def _call_claude_research(expert_name, expert_field, topic, prd_summary):
    """Call Claude API with web search tool for expert research."""
    from forja.config_loader import load_config
    user_content = (
        f"You are {expert_name}, {expert_field}. "
        f"Research this topic for a software project: {topic}\n\n"
        f"Project context: {prd_summary}\n\n"
        f"Search the web for concrete data, benchmarks, best practices, "
        f"and documentation. Then synthesize your findings as {expert_name} "
        f"would - with specific recommendations for this project. "
        f"Be concise, max 3 paragraphs."
    )
    try:
        return _call_anthropic_raw(
            user_content,
            system="",
            model=load_config().models.anthropic_model,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
        )
    except Exception:
        return None


# ── PRD from scratch ───────────────────────────────────────────────

PRD_FROM_IDEA_PROMPT = """\
You are a senior product manager. A developer has this idea for a software project:

'{user_idea}'

Generate a structured PRD draft with:
1. Title
2. Problem statement (what pain does this solve, for whom)
3. Core features (5-8 bullet points, each one sentence)
4. Suggested stack (based on the requirements)
5. Out of scope (3-4 things this is NOT)

Return JSON:
{{
  "title": "string",
  "problem": "string",
  "features": ["string"],
  "stack": {{"language": "string", "framework": "string", "database": "string", "extras": ["string"]}},
  "out_of_scope": ["string"]
}}\
"""


def _read_existing_context() -> str | None:
    """Read context files created by context_setup and build a description.

    Returns a project description string, or None if no context exists.
    """
    parts: list[str] = []

    # Company overview
    overview_path = CONTEXT_DIR / "company" / "company-overview.md"
    if overview_path.exists():
        text = overview_path.read_text(encoding="utf-8").strip()
        # Strip auto-generated header comment
        lines = [l for l in text.splitlines()
                 if not l.startswith("<!--") and not l.startswith("-->")]
        text = "\n".join(lines).strip()
        if text:
            parts.append(text)

    # Domain files: audience, value-props, objections
    domains_dir = CONTEXT_DIR / "domains"
    if domains_dir.is_dir():
        for domain in sorted(domains_dir.iterdir()):
            if not domain.is_dir():
                continue
            for fname in ("DOMAIN.md", "value-props.md", "objections.md"):
                fpath = domain / fname
                if fpath.exists():
                    text = fpath.read_text(encoding="utf-8").strip()
                    lines = [l for l in text.splitlines()
                             if not l.startswith("<!--") and not l.startswith("-->")]
                    text = "\n".join(lines).strip()
                    if text:
                        parts.append(text)

    if not parts:
        return None

    return "\n\n".join(parts)


def _generate_prd_from_idea(user_idea, skill="custom"):
    """Call Kimi to generate a structured PRD from a project idea.

    When *skill* is ``'landing-page'`` or ``'api-backend'``, a constraint
    preamble is prepended so the LLM doesn't hallucinate an enterprise
    platform when the user asked for a static HTML page.

    Returns (prd_markdown, title) or (None, None) on failure.
    """
    prompt = PRD_FROM_IDEA_PROMPT.format(user_idea=user_idea)
    skill_constraint = SKILL_PRD_CONSTRAINTS.get(skill, "")
    if skill_constraint:
        prompt = skill_constraint + "\n\n" + prompt
    raw = call_llm(
        prompt,
        system="You are a senior product manager. Respond only with valid JSON.",
    )
    if not raw:
        return None, None

    data = parse_json(raw)
    if not data or not isinstance(data.get("title"), str):
        return None, None

    title = data["title"]
    problem = data.get("problem", "")
    features = data.get("features", [])
    stack = data.get("stack", {})
    out_of_scope = data.get("out_of_scope", [])

    # Build markdown
    md = f"# {title}\n\n"
    md += f"## Problem\n{problem}\n\n"
    md += "## Features\n"
    for f in features:
        md += f"- {f}\n"
    md += "\n## Stack\n"
    lang = stack.get("language", "")
    fw = stack.get("framework", "")
    db = stack.get("database", "")
    extras = stack.get("extras", [])
    if lang and fw:
        md += f"- {lang} + {fw}\n"
    elif lang:
        md += f"- {lang}\n"
    if db:
        md += f"- {db}\n"
    for ex in extras:
        md += f"- {ex}\n"
    md += "\n## Out of Scope\n"
    for item in out_of_scope:
        md += f"- {item}\n"

    return md.strip(), title


def _scratch_flow(existing_context: str | None = None, skill: str = "custom"):
    """Interactive flow to create a PRD from scratch (or from existing context).

    When *existing_context* is provided (from context_setup), the "Describe
    your project idea" prompt is skipped and the context is used directly.

    *skill* is forwarded to ``_generate_prd_from_idea`` so the LLM prompt
    includes project-type constraints (e.g. "Landing Page = HTML only").

    Returns (prd_content, should_continue_to_expert_panel) or (None, False) on abort.
    """
    prd_file = PRD_PATH

    print()
    print(f"{BOLD}  ── Forja Plan Mode ──{RESET}")
    print(f"  Let's enrich your PRD with expert review.")
    print()

    # If context already exists from context_setup, use it directly
    if existing_context:
        idea = existing_context
        print(f"  {DIM}Using project context from setup...{RESET}")
    else:
        idea = None

    while True:
        if idea is None:
            print(f"  {BOLD}Describe your project idea (2-3 sentences):{RESET}")
            _flush_stdin()
            try:
                idea = input(f"  {BOLD}>{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return None, False

            if not idea:
                print(f"  {RED}Please enter a project description.{RESET}")
                continue

        # Call Kimi to generate PRD
        print(f"\n  {DIM}Generating PRD draft...{RESET}")
        prd_content, title = _generate_prd_from_idea(idea, skill=skill)

        if not prd_content:
            print(f"  {RED}Could not generate PRD (Kimi unavailable or invalid response).{RESET}")
            print(f"  {DIM}Check KIMI_API_KEY in .env or ~/.forja/config.env{RESET}")
            return None, False

        # Show draft
        print()
        print(f"  {BOLD}Here's your initial PRD:{RESET}")
        print(f"  {'─' * 50}")
        for line in prd_content.splitlines():
            print(f"  {line}")
        print(f"  {'─' * 50}")
        print()

        # Options
        print(f"  {BOLD}Options:{RESET}")
        print(f"    {GREEN}(1){RESET} Continue to expert review")
        print(f"    {YELLOW}(2){RESET} Edit description and regenerate")
        print(f"    {CYAN}(3){RESET} Save and exit")
        print()

        _flush_stdin()
        try:
            choice = input(f"  {BOLD}>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            choice = "3"

        if choice == "1":
            # Save and continue to expert panel
            prd_file.parent.mkdir(parents=True, exist_ok=True)
            prd_file.write_text(prd_content + "\n", encoding="utf-8")
            print(f"\n  {GREEN}✔ PRD saved to {prd_file}{RESET}")
            return prd_content, True

        elif choice == "2":
            # Loop back to ask for description again (reset idea so prompt shows)
            idea = None
            print()
            continue

        else:
            # Save and skip expert review
            prd_file.parent.mkdir(parents=True, exist_ok=True)
            prd_file.write_text(prd_content + "\n", encoding="utf-8")
            print(f"\n  {GREEN}✔ PRD saved to {prd_file}{RESET}")
            print(f"  {DIM}Skipped expert review.{RESET}")
            print()
            return prd_content, False


# ── Context gathering ───────────────────────────────────────────────

def _gather_context() -> str:
    """Read all available context from context/ directory."""
    parts: list[str] = []

    # context/store/*.json
    for fpath in sorted(glob_mod.glob(str(STORE_DIR / "*.json"))):
        data = safe_read_json(Path(fpath))
        if data is None:
            continue
        key = data.get("key", Path(fpath).stem)
        value = data.get("value", "")
        if key and value:
            parts.append(f"[decision] {key}: {value}")

    # context/learnings/*.jsonl
    total_chars = 0
    for fpath in sorted(glob_mod.glob(str(LEARNINGS_DIR / "*.jsonl"))):
        try:
            for line in Path(fpath).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    text = entry.get("learning", entry.get("text", entry.get("content", "")))
                    if text:
                        if total_chars + len(text) > 2000:
                            break
                        parts.append(f"[learning] {text}")
                        total_chars += len(text)
                except json.JSONDecodeError as exc:
                    logger.debug("Malformed JSONL line in %s: %s", fpath, exc)
        except OSError as exc:
            logger.debug("Could not read learnings file %s: %s", fpath, exc)

    # Business context: company, domains, design-system (shared utility)
    biz = gather_context(CONTEXT_DIR, max_chars=3000)
    if biz:
        parts.append(biz)

    return "\n".join(parts) if parts else "No prior context available."


# ── Expert panel prompts (Round 1: WHAT, Round 2: HOW) ─────────────

_PANEL_JSON_SCHEMA = """\
Return ONLY valid JSON, no markdown wrapping:
{
  "experts": [
    {"name": "real name or specific type", "field": "their specific expertise", "perspective": "their initial opinion of the PRD in 2 lines"}
  ],
  "questions": [
    {
      "id": 1,
      "expert_name": "who is asking",
      "question": "the question",
      "why": "why it matters according to this expert",
      "default": "what this expert would recommend"
    }
  ],
  "initial_assessment": "2-line assessment of the PRD with the experts' voices"
}"""

WHAT_PANEL_PROMPT = (
    "You are a conductor of expertise bringing together PRODUCT experts to analyze "
    "a software PRD. This is Round 1: deciding WHAT to build.\n\n"
    "Generate 2-3 PRODUCT experts for this project. Focus on: target audience, "
    "messaging, user experience, competitive positioning, content strategy. "
    "Do NOT ask about architecture, databases, or deployment. Ask about: who is "
    "the user, what problem does this solve, what does the user see/feel, what "
    "are the key messages, what sections or features matter most.\n\n"
    "For each expert:\n"
    "1. Speak in their authentic voice about what concerns them\n"
    "2. Ask ONE critical question that must be answered before building\n"
    "3. Suggest a default answer based on their experience\n\n"
    "Synthesize into 6 total questions ordered by impact.\n\n"
    + _PANEL_JSON_SCHEMA
)

HOW_PANEL_PROMPT = (
    "You are a conductor of expertise bringing together TECHNICAL experts to "
    "analyze a software PRD. This is Round 2: deciding HOW to build it. "
    "The product content (what to build) is already decided - only discuss "
    "HOW to build it.\n\n"
    "Generate 2-3 TECHNICAL experts. One MUST be the Build Feasibility Engineer "
    "with VETO POWER over the stack. Focus on: tech stack, dependencies, build "
    "constraints, architecture, performance, security.\n\n"
    "CRITICAL: Claude Code can only install packages via pip or npm. It CANNOT "
    "install Redis, PostgreSQL, Docker, Kafka, or any system service. The Build "
    "Feasibility Engineer must OVERRIDE any incompatible stack.\n\n"
    "For each expert:\n"
    "1. Evaluate the technical feasibility of the PRD\n"
    "2. Ask ONE critical question about HOW to build it\n"
    "3. Suggest a concrete default\n\n"
    "Synthesize into 7 total questions ordered by impact.\n\n"
    + _PANEL_JSON_SCHEMA
)

FALLBACK_WHAT_EXPERTS = [
    {"name": "Product Strategist", "field": "Product Strategy", "perspective": "Evaluating viability, user flows, and product-market fit."},
    {"name": "Target Audience Expert", "field": "User Research", "perspective": "Evaluating who the user is and what they actually need."},
    {"name": "Domain Expert", "field": "Industry Context", "perspective": "Evaluating competitive positioning and domain best practices."},
]

FALLBACK_WHAT_QUESTIONS = [
    {"id": 1, "expert_name": "Target Audience Expert", "question": "Who is the primary user and what problem are they trying to solve?", "why": "Without this, the team optimizes for the wrong user.", "default": "Individual developer, solving productivity pain."},
    {"id": 2, "expert_name": "Target Audience Expert", "question": "What does the user see and feel when they first use this?", "why": "First impression determines retention.", "default": "Clean, fast, no-signup-required first interaction."},
    {"id": 3, "expert_name": "Product Strategist", "question": "What are the key sections, pages, or endpoints?", "why": "Defines MVP scope and build order.", "default": "3-5 core pages/endpoints that deliver the main value."},
    {"id": 4, "expert_name": "Product Strategist", "question": "What does success look like? What metric or outcome?", "why": "Without a goal, there is no way to measure if it works.", "default": "User completes the core flow end-to-end in under 2 minutes."},
    {"id": 5, "expert_name": "Domain Expert", "question": "What are the key messages or value propositions?", "why": "Messaging drives conversion and retention.", "default": "Simple, fast, focused on one thing done well."},
    {"id": 6, "expert_name": "Domain Expert", "question": "What competitive alternatives exist and how is this different?", "why": "Positioning determines feature priority.", "default": "Simpler and more focused than existing tools."},
]

FALLBACK_HOW_EXPERTS = [
    dict(TECHNICAL_EXPERT),
    {"name": "Stack Specialist", "field": "Framework & Library Selection", "perspective": "Choosing the right tools for the job within build constraints."},
    {"name": "Security & Performance Engineer", "field": "Security & Performance", "perspective": "Evaluating attack surface, data handling, and runtime performance."},
]

FALLBACK_HOW_QUESTIONS = [
    {"id": 1, "expert_name": "Build Feasibility Engineer", "question": "STACK OVERRIDE CHECK: Does the PRD specify any technology Claude Code cannot install?", "why": "Claude Code can only install via pip/npm. Redis, PostgreSQL, Docker are impossible.", "default": "Python + FastAPI + SQLite (single-process, pip-installable, no Docker)."},
    {"id": 2, "expert_name": "Build Feasibility Engineer", "question": "What external dependencies are needed and are they all pip/npm installable?", "why": "Any system-level dependency will break the autonomous build.", "default": "All deps via pip. No system packages, no Docker, no external databases."},
    {"id": 3, "expert_name": "Build Feasibility Engineer", "question": "What scope limitations should we set so Claude Code can finish in one session?", "why": "Overly ambitious PRDs result in half-built projects.", "default": "MVP only: 3-5 endpoints or pages, no auth for v1, no CI/CD."},
    {"id": 4, "expert_name": "Stack Specialist", "question": "What framework best fits this project's requirements?", "why": "Framework choice affects build speed, maintainability, and scope.", "default": "FastAPI for APIs, Flask for web apps, vanilla HTML/CSS/JS for landing pages."},
    {"id": 5, "expert_name": "Stack Specialist", "question": "What is the expected data volume and storage approach?", "why": "Determines if SQLite is sufficient or if we need creative alternatives.", "default": "MVP: <1000 users, <100K records. SQLite is sufficient."},
    {"id": 6, "expert_name": "Security & Performance Engineer", "question": "What authentication and authorization model is needed?", "why": "Auth affects every endpoint and must be designed upfront.", "default": "JWT tokens for API auth. No auth for MVP landing pages."},
    {"id": 7, "expert_name": "Security & Performance Engineer", "question": "What are the input validation and size limits?", "why": "Without limits, someone uploads 1GB in a text field.", "default": "Title: max 255 chars. Content: max 50KB. Body: max 100KB."},
]


# ── Technical expert guard ──────────────────────────────────────────

_TECH_KEYWORDS = frozenset({
    "architect", "engineer", "infrastructure", "backend", "build",
    "devops", "system", "technical", "stack", "feasibility", "runtime",
})


def _ensure_technical_expert(
    experts: list[dict], questions: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Guarantee the panel includes at least one build-feasibility expert.

    If none of the experts' *name* or *field* contains a technical keyword,
    the last expert is replaced with :data:`TECHNICAL_EXPERT` and the
    :data:`TECHNICAL_QUESTIONS` are appended with sequential IDs.
    """
    has_tech = any(
        _TECH_KEYWORDS & set(e.get("field", "").lower().split())
        or _TECH_KEYWORDS & set(e.get("name", "").lower().split())
        for e in experts
    )
    if has_tech:
        return experts, questions

    # Replace last expert, append technical questions
    if len(experts) >= 3:
        experts[2] = TECHNICAL_EXPERT
    else:
        experts.append(TECHNICAL_EXPERT)

    next_id = max((q.get("id", 0) for q in questions), default=0) + 1
    for i, tq in enumerate(TECHNICAL_QUESTIONS):
        questions.append({**tq, "id": next_id + i})

    return experts, questions


def _ensure_design_expert(experts: list, prd_text: str) -> list:
    """Add a design expert when the PRD describes a UI project."""
    ui_keywords = [
        "frontend", "ui", "web", "landing", "dashboard", "game", "mobile",
        "react", "html", "css", "canvas", "drag", "drop", "theme", "responsive",
    ]
    has_ui = any(kw in prd_text.lower() for kw in ui_keywords)
    has_design = any(
        "design" in e.get("field", "").lower() or "ux" in e.get("field", "").lower()
        for e in experts
    )
    if has_ui and not has_design:
        experts.insert(1, {
            "name": "Design Systems Expert",
            "field": "UX Design & Visual Systems",
            "perspective": "Accessible, consistent, performant interfaces",
        })
    return experts


# ── Core flow ───────────────────────────────────────────────────────

def _get_expert_color(expert_name, experts):
    """Get consistent color for an expert."""
    for i, exp in enumerate(experts):
        if exp.get("name") == expert_name:
            return EXPERT_COLORS[i % len(EXPERT_COLORS)]
    return DIM


def _print_header(prd_title, experts, assessment):
    """Print the plan mode header with expert panel."""
    print()
    print(f"{BOLD}  ── Forja Plan Mode ──{RESET}")
    print(f"  {DIM}PRD: {prd_title}{RESET}")
    print()
    print(f"  {BOLD}Expert Panel:{RESET}")
    for i, exp in enumerate(experts):
        color = EXPERT_COLORS[i % len(EXPERT_COLORS)]
        print(f"    {color}{exp['name']}{RESET} — {exp['field']}")
    print()
    print(f"  {DIM}{assessment}{RESET}")
    print()


def _ask_question(q, experts, prd_summary, research_log=None, total=8):
    """Present a question and get user response. Returns (answer, tag).

    *research_log*, when provided, accumulates ``{"topic": ..., "findings": ...}``
    dicts for every successful research call made during this question.
    *total* is the total number of questions in this round (for display).
    """
    expert = q["expert_name"]
    qid = q["id"]
    color = _get_expert_color(expert, experts)
    question = q["question"]
    why = q["why"]
    default = q["default"]

    print(f"  {color}{BOLD}[{expert} — {qid}/{total}]{RESET} {question}")
    print(f"  {DIM}Why it matters: \"{why}\"{RESET}")
    print(f"  {DIM}Suggestion: {default}{RESET}")
    print()

    while True:
        _flush_stdin()
        try:
            answer = input(f"  {BOLD}>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default, "ASSUMPTION"

        if not answer:
            # Enter = accept suggestion
            print(f"  {GREEN}✔ Accepted{RESET}")
            return default, "DECISION"

        if answer.lower() == "skip":
            print(f"  {DIM}→ Using default{RESET}")
            return default, "ASSUMPTION"

        if answer.lower() == "done":
            return None, "DONE"

        if answer.lower().startswith("research "):
            topic = answer[9:].strip()
            if topic:
                findings = _do_research(expert, topic, prd_summary, experts)
                if findings and research_log is not None:
                    research_log.append({"topic": topic, "findings": findings})
                # Re-show question
                print()
                print(f"  {color}{BOLD}[{expert} — {qid}/{total}]{RESET} {question}")
                print(f"  {DIM}Suggestion: {default}{RESET}")
                print()
                continue

        # User typed a real answer
        print(f"  {GREEN}✔ Saved{RESET}")
        return answer, "FACT"


def _do_research(expert_name, topic, prd_summary, experts=None):
    """Research a topic using Claude with web search (primary) or Kimi (fallback).

    Returns the research findings as a string, or empty string on failure.
    Saves findings to ``.forja/research/`` for future reference.
    """
    color = _get_expert_color(expert_name, experts) if experts else DIM
    # Find expert field
    expert_field = ""
    if experts:
        for exp in experts:
            if exp.get("name") == expert_name:
                expert_field = exp.get("field", "")
                break

    print(f"\n  {DIM}Researching: {topic}...{RESET}")

    findings = ""

    # Primary: Claude with web search
    raw = _call_claude_research(expert_name, expert_field, topic, prd_summary)
    if raw:
        findings = raw.strip()
        print(f"  {DIM}(web search via Claude){RESET}")
        print()
        for line in findings.splitlines():
            print(f"  {color}  {line}{RESET}")
        print()
    else:
        # Fallback: any provider without web search
        print(f"  {YELLOW}Web search unavailable, using expert knowledge only{RESET}")
        raw = call_llm(
            f"The project context: {prd_summary}\n\n"
            f"Research topic: {topic}\n\n"
            f"Respond as {expert_name} would: with specific data, benchmarks, "
            f"and a concrete recommendation. Keep it under 200 words.",
            system=f"You are {expert_name}, a domain expert. Answer concisely with concrete data and a clear recommendation.",
        )
        if raw:
            findings = raw.strip()
            print()
            for line in findings.splitlines():
                print(f"  {color}  {line}{RESET}")
            print()
        else:
            print(f"  {RED}Could not research (no model available){RESET}")

    # Save to .forja/research/ for future reference
    if findings:
        _save_research(topic, findings)

    return findings


def _save_research(topic: str, findings: str) -> None:
    """Persist research findings to .forja/research/ directory."""
    research_dir = FORJA_DIR / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in topic.lower())[:80]
    fpath = research_dir / f"{slug}.md"
    fpath.write_text(f"# Research: {topic}\n\n{findings}\n", encoding="utf-8")
    print(f"  {DIM}Saved: {fpath}{RESET}")


def _collect_design_context() -> str:
    """Ask the user 3 optional design questions and write context files.

    Returns a design context string for inclusion in the enriched PRD prompt,
    or empty string if nothing was collected.
    """
    design_dir = CONTEXT_DIR / "design-system"
    design_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{BOLD}── Design Context (optional, Enter to skip) ──{RESET}\n")

    _flush_stdin()
    try:
        ref = input("  Reference URL or screenshot path (Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        ref = ""
    if ref:
        (design_dir / "references.md").write_text(
            f"# Visual References\n\n- {ref}\n", encoding="utf-8",
        )

    _flush_stdin()
    try:
        colors = input("  Brand colors - primary, secondary, accent (Enter for auto): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        colors = ""
    if colors:
        (design_dir / "colors.md").write_text(
            f"# Color Palette\n\n{colors}\n", encoding="utf-8",
        )
    else:
        (design_dir / "colors.md").write_text(
            "# Color Palette\n\n"
            "- Primary: #2563eb (blue)\n"
            "- Secondary: #1e293b (dark slate)\n"
            "- Accent: #22c55e (green)\n"
            "- Background: #f8fafc (light) / #0f172a (dark)\n"
            "- Text: #1e293b (light mode) / #f1f5f9 (dark mode)\n",
            encoding="utf-8",
        )

    _flush_stdin()
    try:
        style = input("  Style preference - minimal/playful/corporate/brutal (Enter for minimal): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        style = ""
    if not style:
        style = "minimal"
    (design_dir / "style.md").write_text(
        f"# Design Style\n\nStyle: {style}\n\n"
        f"## Guidelines\n"
        f"- Clean spacing, generous whitespace\n"
        f"- Consistent border-radius (8px default)\n"
        f"- System font stack for performance\n"
        f"- Responsive: mobile-first, breakpoints at 640px, 768px, 1024px\n",
        encoding="utf-8",
    )

    # Read back all design files to build context string
    parts: list[str] = []
    for fname in ("references.md", "colors.md", "style.md"):
        fpath = design_dir / fname
        if fpath.exists():
            text = fpath.read_text(encoding="utf-8").strip()
            if text:
                parts.append(text)

    return "\n\n".join(parts) if parts else ""


def _generate_enriched_prd(prd_content, qa_transcript, experts, design_context="", research_log=None):
    """Call Kimi to generate the enriched PRD."""
    # Format Q&A transcript
    transcript_text = ""
    for item in qa_transcript:
        tag = item["tag"]
        transcript_text += (
            f"[{tag}] {item['expert']}: {item['question']}\n"
            f"  Answer: {item['answer']}\n\n"
        )

    experts_text = ", ".join(f"{e['name']} ({e['field']})" for e in experts)

    design_section = ""
    if design_context:
        design_section = (
            f"\n6. Add section '## Design System' incorporating the following "
            f"design context:\n{design_context}\n"
        )

    research_section = ""
    if research_log:
        research_text = "\n".join(
            f"- **{r['topic']}**: {r['findings'][:300]}" for r in research_log
        )
        next_num = 7 if design_context else 6
        research_section = (
            f"\n{next_num}. Add section '## Research Findings' incorporating these "
            f"research results gathered during planning:\n{research_text}\n"
        )

    raw = call_llm(
        f"The experts ({experts_text}) have received the user's answers. "
        f"Generate the enriched PRD.\n\n"
        f"Experts and their questions/answers:\n{transcript_text}\n"
        f"Original PRD:\n{prd_content}\n\n"
        f"Generate a complete PRD that incorporates all answers. Structure:\n"
        f"1. Keep the original PRD intact at the beginning\n"
        f"2. Add section '## Technical Decisions' with architecture answers, "
        f"marked [FACT], [DECISION], or [ASSUMPTION]\n"
        f"3. Add section '## Product Strategy' with product answers\n"
        f"4. Add section '## Security and Edge Cases' with security answers\n"
        f"5. Add section '## Assumption Density: X/{len(qa_transcript)}' "
        f"with assumption count"
        f"{design_section}"
        f"{research_section}\n\n"
        f"Respond ONLY with the complete PRD in markdown.",
        system=(
            "You are a senior technical writer. Generate a complete enriched PRD "
            "in markdown. Respond ONLY with the PRD, no JSON, no preamble."
        ),
    )
    if raw:
        # Strip markdown wrappers if present
        text = raw.strip()
        if text.startswith("```"):
            first_nl = text.find("\n")
            last_fence = text.rfind("```")
            if first_nl != -1 and last_fence > first_nl:
                text = text[first_nl + 1:last_fence].strip()
        return text
    return None


def _modify_prd_section(prd_text: str, feedback: str) -> str:
    """Use LLM to modify a specific section of the PRD based on user feedback."""
    prompt = (
        f"Here is a PRD:\n\n{prd_text}\n\n"
        f"The user wants this change: \"{feedback}\"\n\n"
        f"Modify ONLY the relevant section. Keep everything else unchanged. "
        f"Return the full updated PRD."
    )
    result = call_llm(
        prompt,
        system=(
            "You are a PRD editor. Make minimal targeted changes based on the "
            "user's feedback. Do not rewrite sections that don't need changes. "
            "Return ONLY the PRD in markdown, no preamble."
        ),
    )
    if result:
        text = result.strip()
        if text.startswith("```"):
            first_nl = text.find("\n")
            last_fence = text.rfind("```")
            if first_nl != -1 and last_fence > first_nl:
                text = text[first_nl + 1:last_fence].strip()
        return text
    return prd_text


def _regenerate_prd_with_feedback(prd_text: str, feedback: str) -> str:
    """Regenerate the entire PRD incorporating user feedback."""
    prompt = (
        f"Here is a PRD that needs revision:\n\n{prd_text}\n\n"
        f"The user's feedback: \"{feedback}\"\n\n"
        f"Regenerate the PRD incorporating this feedback. Keep the same "
        f"structure but improve based on the feedback."
    )
    result = call_llm(
        prompt,
        system=(
            "You are a PRD writer. Regenerate the PRD incorporating the "
            "user's feedback while maintaining professional structure. "
            "Return ONLY the PRD in markdown, no preamble."
        ),
    )
    if result:
        text = result.strip()
        if text.startswith("```"):
            first_nl = text.find("\n")
            last_fence = text.rfind("```")
            if first_nl != -1 and last_fence > first_nl:
                text = text[first_nl + 1:last_fence].strip()
        return text
    return prd_text


def _interactive_prd_edit(prd_text: str) -> str:
    """Let user review and edit the enriched PRD interactively."""
    while True:
        print(f"\n  {BOLD}Options:{RESET}")
        print(f"    {GREEN}(1){RESET} Accept and save")
        print(f"    {YELLOW}(2){RESET} Edit a section (tell me what to change)")
        print(f"    {CYAN}(3){RESET} Regenerate with feedback")
        print(f"    {DIM}(4){RESET} View full PRD")
        print()

        _flush_stdin()
        try:
            choice = input(f"  {BOLD}>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return prd_text

        if choice == "1":
            return prd_text
        elif choice == "2":
            _flush_stdin()
            try:
                feedback = input(f"  {BOLD}What would you change?{RESET} > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                continue
            if feedback:
                print(f"\n  {DIM}Updating PRD...{RESET}")
                prd_text = _modify_prd_section(prd_text, feedback)
                print(f"\n  {BOLD}── Updated PRD (preview) ──{RESET}")
                preview = prd_text[:500]
                if len(prd_text) > 500:
                    preview += "..."
                for line in preview.splitlines():
                    print(f"  {line}")
        elif choice == "3":
            _flush_stdin()
            try:
                feedback = input(
                    f"  {BOLD}Describe what you want differently{RESET} > "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                continue
            if feedback:
                print(f"\n  {DIM}Regenerating PRD...{RESET}")
                prd_text = _regenerate_prd_with_feedback(prd_text, feedback)
                print(f"\n  {BOLD}── Regenerated PRD (preview) ──{RESET}")
                preview = prd_text[:500]
                if len(prd_text) > 500:
                    preview += "..."
                for line in preview.splitlines():
                    print(f"  {line}")
        elif choice == "4":
            print()
            for line in prd_text.splitlines():
                print(f"  {line}")


def _save_transcript(round_data, enriched_prd, research_log=None):
    """Save full transcript to .forja/plan-transcript.json.

    *round_data* is a list of dicts, one per round, each containing:
    ``{"round": str, "experts": list, "questions": list, "answers": list}``.
    """
    transcript = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rounds": round_data,
        "research": research_log or [],
        "enriched_prd_length": len(enriched_prd) if enriched_prd else 0,
    }

    out_path = FORJA_DIR / "plan-transcript.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(transcript, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


# ── Expert Q&A helper ────────────────────────────────────────────────

def _run_expert_qa(
    prompt_template: str,
    fallback_experts: list[dict],
    fallback_questions: list[dict],
    prd_content: str,
    prd_title: str,
    context: str,
    skill_guidance: str,
    round_label: str,
    max_questions: int = 8,
    ensure_tech: bool = False,
    ensure_design: bool = False,
) -> tuple[list[dict], list[dict], list[dict], list[dict], str]:
    """Run one round of expert panel Q&A.

    Returns ``(experts, questions, qa_transcript, research_log, assessment)``.
    """
    prd_summary = prd_content[:500]

    print(f"\n  {DIM}Assembling {round_label} expert panel...{RESET}")

    raw = call_llm(
        f"{prompt_template}\n\n"
        f"IMPORTANT CONTEXT:\n{skill_guidance}\n\n"
        f"PRD:\n{prd_content}\n\n"
        f"Available context:\n{context}",
        system="You are a conductor of expertise. Respond only with valid JSON.",
    )
    panel = None
    if raw:
        panel = parse_json(raw)

    # Validate panel structure or use fallback
    if (
        not panel
        or not isinstance(panel.get("experts"), list)
        or not isinstance(panel.get("questions"), list)
        or len(panel["experts"]) < 2
        or len(panel["questions"]) < 3
    ):
        print(f"  {DIM}Using generic {round_label} panel{RESET}")
        experts = list(fallback_experts)
        questions = list(fallback_questions)
        assessment = "PRD needs clarification before building."
    else:
        experts = panel["experts"][:3]
        questions = panel["questions"][:max_questions]
        assessment = panel.get("initial_assessment", "")

    if ensure_tech:
        experts, questions = _ensure_technical_expert(experts, questions)
    if ensure_design:
        experts = _ensure_design_expert(experts, prd_content)

    # Ensure each question has an id
    for i, q in enumerate(questions):
        if "id" not in q:
            q["id"] = i + 1

    total = len(questions)

    _print_header(prd_title, experts, assessment)

    qa_transcript: list[dict] = []
    research_log: list[dict] = []
    print(f"  {DIM}Enter=accept default | skip | research [topic] to investigate | done to finish{RESET}")
    print()

    for q in questions:
        answer, tag = _ask_question(q, experts, prd_summary, research_log, total=total)

        if tag == "DONE":
            qa_transcript.append({
                "expert": q["expert_name"],
                "question": q["question"],
                "answer": q["default"],
                "tag": "ASSUMPTION",
            })
            remaining_qs = questions[questions.index(q) + 1:]
            for rq in remaining_qs:
                qa_transcript.append({
                    "expert": rq["expert_name"],
                    "question": rq["question"],
                    "answer": rq["default"],
                    "tag": "ASSUMPTION",
                })
            print(f"\n  {DIM}Using defaults for {len(remaining_qs) + 1} remaining questions{RESET}")
            break

        qa_transcript.append({
            "expert": q["expert_name"],
            "question": q["question"],
            "answer": answer,
            "tag": tag,
        })
        print()

    # Summary
    facts = sum(1 for a in qa_transcript if a["tag"] == "FACT")
    decisions = sum(1 for a in qa_transcript if a["tag"] == "DECISION")
    assumptions = sum(1 for a in qa_transcript if a["tag"] == "ASSUMPTION")
    print()
    print(f"  {BOLD}{round_label} Summary:{RESET} {GREEN}{facts} facts{RESET}, "
          f"{CYAN}{decisions} decisions{RESET}, "
          f"{YELLOW}{assumptions} assumptions{RESET}")

    return experts, questions, qa_transcript, research_log, assessment


def _get_skill_what_guidance(skill: str) -> str:
    """Get skill-specific guidance for WHAT round."""
    if skill == "landing-page":
        return (
            "Focus WHAT questions on: copy and messaging, page sections and flow, "
            "CTA strategy, audience targeting, visual hierarchy, tone of voice. "
            "Do NOT ask about databases, APIs, or deployment."
        )
    if skill == "api-backend":
        return (
            "Focus WHAT questions on: API design and endpoints, data model, "
            "business rules, user flows, input/output contracts. "
            "Do NOT ask about visual design, CSS, or frontend layout."
        )
    return ""


def _get_skill_how_guidance(skill: str) -> str:
    """Get skill-specific guidance for HOW round."""
    if skill == "landing-page":
        return (
            "Focus HOW questions on: HTML/CSS framework, build tooling, "
            "hosting constraints, asset pipeline, responsive strategy. "
            "Keep it simple — vanilla HTML/CSS/JS is preferred."
        )
    if skill == "api-backend":
        return (
            "Focus HOW questions on: framework choice (FastAPI/Flask), "
            "database (SQLite only), auth mechanism, error handling, "
            "deployment (uvicorn). All deps must be pip-installable."
        )
    return ""


# ── Main entry point ────────────────────────────────────────────────

def run_plan(prd_path=None, *, _called_from_runner: bool = False) -> bool:
    """Run Forja plan mode interactively with two expert rounds.

    Round 1 (WHAT): Product/Strategy experts decide what to build.
    Round 2 (HOW): Technical experts decide how to build it.

    Returns True if the PRD was saved successfully, False otherwise.
    When *_called_from_runner* is True, skips messages that tell the
    user to run additional commands (since the runner continues
    automatically).
    """
    prd_file = Path(prd_path) if prd_path else PRD_PATH

    # Detect skill early so scratch flow can constrain PRD generation
    skill = _detect_skill()

    # Check if PRD is missing or empty/placeholder → scratch flow
    prd_missing = not prd_file.exists()
    prd_empty = False
    if not prd_missing:
        content = prd_file.read_text(encoding="utf-8").strip()
        prd_empty = not content or content in (
            "# PRD\n\nDescribe your project here.",
            "# PRD\nDescribe your project here.",
        )

    if prd_missing or prd_empty:
        load_dotenv()
        existing_context = _read_existing_context()
        prd_content, continue_to_panel = _scratch_flow(existing_context, skill=skill)
        if not prd_content:
            return False
        if not continue_to_panel:
            return True
        # prd_content is set, prd_file was written by _scratch_flow()
    else:
        prd_content = prd_file.read_text(encoding="utf-8").strip()

    # Extract title
    prd_lines = prd_content.split("\n")
    prd_title = prd_lines[0].lstrip("# ").strip() if prd_lines else "Unknown"

    # Load env
    load_dotenv()

    # Gather context
    context = _gather_context()

    base_guidance = SKILL_EXPERT_GUIDANCE.get(skill, SKILL_EXPERT_GUIDANCE["custom"])

    round_data: list[dict] = []
    all_research: list[dict] = []

    # ════════════════════════════════════════════════════════════════
    #  ROUND 1 — WHAT (Product / Strategy)
    # ════════════════════════════════════════════════════════════════
    print(f"\n{BOLD}  ═══ Round 1: WHAT to build ═══{RESET}")

    what_guidance = base_guidance + "\n" + _get_skill_what_guidance(skill)

    what_experts, what_qs, what_transcript, what_research, _ = _run_expert_qa(
        prompt_template=WHAT_PANEL_PROMPT,
        fallback_experts=FALLBACK_WHAT_EXPERTS,
        fallback_questions=FALLBACK_WHAT_QUESTIONS,
        prd_content=prd_content,
        prd_title=prd_title,
        context=context,
        skill_guidance=what_guidance,
        round_label="WHAT",
        max_questions=6,
        ensure_tech=False,
        ensure_design=True,
    )

    round_data.append({
        "round": "WHAT",
        "experts": what_experts,
        "questions": what_qs,
        "answers": what_transcript,
    })
    all_research.extend(what_research)

    # ── Generate intermediate PRD with WHAT decisions ──
    print(f"\n  {DIM}Incorporating product decisions into PRD...{RESET}")

    what_enriched = _generate_enriched_prd(
        prd_content, what_transcript, what_experts,
    )
    if not what_enriched:
        # Manual fallback
        what_enriched = prd_content + "\n\n## Product Decisions\n\n"
        for a in what_transcript:
            what_enriched += f"- [{a['tag']}] {a['question']}: {a['answer']}\n"

    # ── User can edit between rounds ──
    print()
    print(f"  {BOLD}── PRD after Round 1 (preview) ──{RESET}")
    print()
    preview_lines = what_enriched.strip().splitlines()
    for line in preview_lines[:40]:
        print(f"  {line}")
    if len(preview_lines) > 40:
        print(f"  {DIM}... ({len(preview_lines) - 40} more lines){RESET}")
    print()

    what_enriched = _interactive_prd_edit(what_enriched)

    # ════════════════════════════════════════════════════════════════
    #  ROUND 2 — HOW (Technical / Feasibility)
    # ════════════════════════════════════════════════════════════════
    print(f"\n{BOLD}  ═══ Round 2: HOW to build it ═══{RESET}")

    how_guidance = base_guidance + "\n" + _get_skill_how_guidance(skill)

    how_experts, how_qs, how_transcript, how_research, _ = _run_expert_qa(
        prompt_template=HOW_PANEL_PROMPT,
        fallback_experts=FALLBACK_HOW_EXPERTS,
        fallback_questions=FALLBACK_HOW_QUESTIONS,
        prd_content=what_enriched,
        prd_title=prd_title,
        context=context,
        skill_guidance=how_guidance,
        round_label="HOW",
        max_questions=7,
        ensure_tech=True,
        ensure_design=False,
    )

    round_data.append({
        "round": "HOW",
        "experts": how_experts,
        "questions": how_qs,
        "answers": how_transcript,
    })
    all_research.extend(how_research)

    # ── Design Context (optional) ──
    design_context = _collect_design_context()

    # ── Generate final enriched PRD with both rounds ──
    all_transcript = what_transcript + how_transcript
    all_experts = what_experts + how_experts
    # Deduplicate experts by name
    seen_names: set[str] = set()
    unique_experts: list[dict] = []
    for e in all_experts:
        if e["name"] not in seen_names:
            seen_names.add(e["name"])
            unique_experts.append(e)

    print(f"\n  {DIM}Generating final enriched PRD...{RESET}")

    enriched_prd = _generate_enriched_prd(
        what_enriched, how_transcript, unique_experts, design_context, all_research,
    )

    if not enriched_prd:
        # Fallback: manual assembly
        assumptions = sum(1 for a in all_transcript if a["tag"] == "ASSUMPTION")
        print(f"  {YELLOW}LLM did not respond. Generating PRD manually.{RESET}")
        enriched_prd = what_enriched + "\n"
        enriched_prd += "\n## Technical Decisions\n\n"
        for a in how_transcript:
            enriched_prd += f"- [{a['tag']}] {a['question']}: {a['answer']}\n"
        enriched_prd += f"\n## Assumption Density: {assumptions}/{len(all_transcript)}\n"
        if design_context:
            enriched_prd += f"\n## Design System\n\n{design_context}\n"
        if all_research:
            enriched_prd += "\n## Research Findings\n\n"
            for r in all_research:
                enriched_prd += f"### {r['topic']}\n{r['findings']}\n\n"

    # ── Final preview ──
    print()
    print(f"  {BOLD}── Final Enriched PRD (preview) ──{RESET}")
    print()
    preview_lines = enriched_prd.strip().splitlines()
    for line in preview_lines[:60]:
        print(f"  {line}")
    if len(preview_lines) > 60:
        print(f"  {DIM}... ({len(preview_lines) - 60} more lines){RESET}")
    print()

    # ── Final interactive edit / confirm ──
    enriched_prd = _interactive_prd_edit(enriched_prd)
    prd_file.write_text(enriched_prd + "\n", encoding="utf-8")
    print(f"\n  {GREEN}✔ PRD saved to {prd_file}{RESET}")

    # ── Save transcript (both rounds) ──
    transcript_path = _save_transcript(round_data, enriched_prd, all_research)
    print(f"  {DIM}Transcript: {transcript_path}{RESET}")

    if not _called_from_runner:
        print(f"\n  {BOLD}PRD ready. Run 'forja run' to build.{RESET}")
    print()
    return True
