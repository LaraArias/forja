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
import os
import time
from pathlib import Path

from forja.constants import (
    CONTEXT_DIR, FORJA_DIR, LEARNINGS_DIR, PRD_PATH, STORE_DIR,
)
from forja.utils import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    YELLOW,
    RESET,
    call_kimi,
    call_anthropic,
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
    "perspective": "Evaluating whether this project can actually be built autonomously by Claude Code with pip/npm dependencies.",
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
        "question": "What tech stack is viable given that Claude Code builds this autonomously in a single session?",
        "why": "Claude Code has context limits and can only install packages via pip/npm. Exotic stacks or multi-service architectures will fail.",
        "default": "Python + FastAPI + SQLite (single-process, pip-installable, no Docker needed).",
    },
    {
        "expert_name": "Build Feasibility Engineer",
        "question": "What external dependencies are needed and are they all installable via pip or npm?",
        "why": "Claude Code cannot install system-level packages (apt, brew). Every dependency must be pip/npm installable.",
        "default": "All deps via pip. No system packages, no Docker, no external databases.",
    },
    {
        "expert_name": "Build Feasibility Engineer",
        "question": "What scope limitations should we set so Claude Code can finish in one session?",
        "why": "Claude Code works in a single session with context limits. Overly ambitious PRDs result in half-built projects.",
        "default": "MVP only: 3-5 endpoints or pages, no auth for v1, no CI/CD, no deployment config.",
    },
]



def _call_claude_research(expert_name, expert_field, topic, prd_summary):
    """Call Claude API with web search tool for expert research."""
    user_content = (
        f"You are {expert_name}, {expert_field}. "
        f"Research this topic for a software project: {topic}\n\n"
        f"Project context: {prd_summary}\n\n"
        f"Search the web for concrete data, benchmarks, best practices, "
        f"and documentation. Then synthesize your findings as {expert_name} "
        f"would - with specific recommendations for this project. "
        f"Be concise, max 3 paragraphs."
    )
    return call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        max_tokens=1500,
        timeout=90,
    )


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


def _generate_prd_from_idea(user_idea):
    """Call Kimi to generate a structured PRD from a project idea.

    Returns (prd_markdown, title) or (None, None) on failure.
    """
    messages = [
        {
            "role": "system",
            "content": "You are a senior product manager. Respond only with valid JSON.",
        },
        {
            "role": "user",
            "content": PRD_FROM_IDEA_PROMPT.format(user_idea=user_idea),
        },
    ]

    raw = call_kimi(messages, temperature=0.6)
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


def _scratch_flow():
    """Interactive flow to create a PRD from scratch.

    Returns (prd_content, should_continue_to_expert_panel) or (None, False) on abort.
    """
    prd_file = PRD_PATH

    print()
    print(f"{BOLD}  ── Forja Plan Mode ──{RESET}")
    print(f"  No PRD found. Let's create one from scratch.")
    print()

    while True:
        print(f"  {BOLD}Describe your project idea (2-3 sentences):{RESET}")
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
        prd_content, title = _generate_prd_from_idea(idea)

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
            # Loop back to ask for description again
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
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass

    # Business context: company, domains, design-system (shared utility)
    biz = gather_context(CONTEXT_DIR, max_chars=3000)
    if biz:
        parts.append(biz)

    return "\n".join(parts) if parts else "No prior context available."


# ── Expert panel prompt ─────────────────────────────────────────────

EXPERT_PANEL_PROMPT = """\
You are a conductor of expertise bringing together real-world experts to analyze a software PRD before it gets built.

Choose 3 specific, real experts relevant to THIS project (not generic roles). For example:
- If it's a fintech API: maybe Stripe's API design lead, a banking compliance expert, and a distributed systems architect
- If it's a notes app: maybe a UX researcher who studied note-taking, a SQLite expert, and a security engineer from a productivity company

IMPORTANT: At least one of the 3 experts MUST focus on implementation feasibility —
someone who can evaluate whether the tech stack is realistic, whether dependencies
are pip/npm installable, and whether the scope fits a single autonomous build session.

For each expert:
1. Have them speak in their authentic voice about what concerns them about this PRD
2. Have them ask ONE critical question that must be answered before building
3. Have them suggest a default answer based on their experience

Then synthesize into 8 total questions across the 3 experts, ordered by impact.

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
}\
"""


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


def _ask_question(q, experts, prd_summary):
    """Present a question and get user response. Returns (answer, tag)."""
    expert = q["expert_name"]
    qid = q["id"]
    total = 8
    color = _get_expert_color(expert, experts)
    question = q["question"]
    why = q["why"]
    default = q["default"]

    print(f"  {color}{BOLD}[{expert} — {qid}/{total}]{RESET} {question}")
    print(f"  {DIM}Why it matters: \"{why}\"{RESET}")
    print(f"  {DIM}Suggestion: {default}{RESET}")
    print()

    while True:
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
                _do_research(expert, topic, prd_summary, experts)
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
    """Research a topic using Claude with web search (primary) or Kimi (fallback)."""
    color = _get_expert_color(expert_name, experts) if experts else DIM
    # Find expert field
    expert_field = ""
    if experts:
        for exp in experts:
            if exp.get("name") == expert_name:
                expert_field = exp.get("field", "")
                break

    print(f"\n  {DIM}Researching: {topic}...{RESET}")

    # Primary: Claude with web search
    raw = _call_claude_research(expert_name, expert_field, topic, prd_summary)
    if raw:
        print(f"  {DIM}(web search via Claude){RESET}")
        print()
        for line in raw.strip().splitlines():
            print(f"  {color}  {line}{RESET}")
        print()
        return

    # Fallback: Kimi without web search
    print(f"  {YELLOW}Web search unavailable, using expert knowledge only{RESET}")
    messages = [
        {
            "role": "system",
            "content": f"You are {expert_name}, a domain expert. Answer concisely with concrete data and a clear recommendation.",
        },
        {
            "role": "user",
            "content": (
                f"The project context: {prd_summary}\n\n"
                f"Research topic: {topic}\n\n"
                f"Respond as {expert_name} would: with specific data, benchmarks, "
                f"and a concrete recommendation. Keep it under 200 words."
            ),
        },
    ]

    raw = call_kimi(messages, temperature=0.5)
    if raw:
        print()
        for line in raw.strip().splitlines():
            print(f"  {color}  {line}{RESET}")
        print()
    else:
        print(f"  {RED}Could not research (no model available){RESET}")


def _generate_enriched_prd(prd_content, qa_transcript, experts):
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

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior technical writer. Generate a complete enriched PRD "
                "in markdown. Respond ONLY with the PRD, no JSON, no preamble."
            ),
        },
        {
            "role": "user",
            "content": (
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
                f"with assumption count\n\n"
                f"Respond ONLY with the complete PRD in markdown."
            ),
        },
    ]

    raw = call_kimi(messages, temperature=0.4)
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


def _save_transcript(experts, questions, qa_transcript, enriched_prd):
    """Save full transcript to .forja/plan-transcript.json."""
    transcript = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "experts": experts,
        "questions": questions,
        "answers": qa_transcript,
        "enriched_prd_length": len(enriched_prd) if enriched_prd else 0,
    }

    out_path = FORJA_DIR / "plan-transcript.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(transcript, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


# ── Main entry point ────────────────────────────────────────────────

def run_plan(prd_path=None, *, _called_from_runner: bool = False) -> bool:
    """Run Forja plan mode interactively.

    Returns True if the PRD was saved successfully, False otherwise.
    When *_called_from_runner* is True, skips messages that tell the
    user to run additional commands (since the runner continues
    automatically).
    """
    prd_file = Path(prd_path) if prd_path else PRD_PATH

    # Check if PRD is missing or empty/placeholder → scratch flow
    prd_missing = not prd_file.exists()
    prd_empty = False
    if not prd_missing:
        content = prd_file.read_text(encoding="utf-8").strip()
        # Detect empty or default init template
        prd_empty = not content or content in (
            "# PRD\n\nDescribe your project here.",
            "# PRD\nDescribe your project here.",
        )

    if prd_missing or prd_empty:
        load_dotenv()
        prd_content, continue_to_panel = _scratch_flow()
        if not prd_content:
            return False
        if not continue_to_panel:
            # PRD was saved but user skipped expert review.
            # If called from runner, it will continue to build with this PRD.
            return True
        # prd_content is set, prd_file was written by _scratch_flow()
    else:
        prd_content = prd_file.read_text(encoding="utf-8").strip()

    # Extract title
    prd_lines = prd_content.split("\n")
    prd_title = prd_lines[0].lstrip("# ").strip() if prd_lines else "Unknown"
    # Short summary for research calls
    prd_summary = prd_content[:500]

    # Load env
    load_dotenv()

    # Gather context
    context = _gather_context()

    # ── Step 1: Get expert panel from Kimi ──
    print(f"\n  {DIM}Assembling expert panel...{RESET}")

    messages = [
        {
            "role": "system",
            "content": "You are a conductor of expertise. Respond only with valid JSON.",
        },
        {
            "role": "user",
            "content": (
                f"{EXPERT_PANEL_PROMPT}\n\n"
                f"PRD:\n{prd_content}\n\n"
                f"Available context:\n{context}"
            ),
        },
    ]

    raw = call_kimi(messages)
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
        print(f"  {DIM}Using generic expert panel{RESET}")
        experts = FALLBACK_EXPERTS
        questions = FALLBACK_QUESTIONS
        assessment = "PRD needs clarification before building."
    else:
        experts = panel["experts"][:3]
        questions = panel["questions"][:8]
        assessment = panel.get("initial_assessment", "")

    # Guarantee a technical / build-feasibility expert is present
    experts, questions = _ensure_technical_expert(experts, questions)

    # Ensure each question has an id
    for i, q in enumerate(questions):
        if "id" not in q:
            q["id"] = i + 1

    # ── Step 2: Print header ──
    _print_header(prd_title, experts, assessment)

    # ── Step 3: Interactive Q&A ──
    qa_transcript = []
    print(f"  {DIM}Answer each question. Enter=accept, skip=default, research [topic]=investigate, done=finish{RESET}")
    print()

    for q in questions:
        answer, tag = _ask_question(q, experts, prd_summary)

        if tag == "DONE":
            # Fill remaining with defaults
            qa_transcript.append({
                "expert": q["expert_name"],
                "question": q["question"],
                "answer": q["default"],
                "tag": "ASSUMPTION",
            })
            # Fill the rest
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

    # ── Step 4: Count tags ──
    facts = sum(1 for a in qa_transcript if a["tag"] == "FACT")
    decisions = sum(1 for a in qa_transcript if a["tag"] == "DECISION")
    assumptions = sum(1 for a in qa_transcript if a["tag"] == "ASSUMPTION")

    print()
    print(f"  {BOLD}Summary:{RESET} {GREEN}{facts} facts{RESET}, "
          f"{CYAN}{decisions} decisions{RESET}, "
          f"{YELLOW}{assumptions} assumptions{RESET}")

    # ── Step 5: Generate enriched PRD ──
    print(f"\n  {DIM}Generating enriched PRD...{RESET}")

    enriched_prd = _generate_enriched_prd(prd_content, qa_transcript, experts)

    if not enriched_prd:
        # Fallback: manual assembly
        print(f"  {YELLOW}Kimi did not respond. Generating PRD manually.{RESET}")
        enriched_prd = prd_content + "\n"
        enriched_prd += "\n## Technical Decisions\n\n"
        for a in qa_transcript:
            enriched_prd += f"- [{a['tag']}] {a['question']}: {a['answer']}\n"
        enriched_prd += f"\n## Assumption Density: {assumptions}/{len(qa_transcript)}\n"

    # ── Step 6: Preview ──
    print()
    print(f"  {BOLD}── Enriched PRD (preview) ──{RESET}")
    print()
    # Show first 60 lines
    preview_lines = enriched_prd.strip().splitlines()
    for line in preview_lines[:60]:
        print(f"  {line}")
    if len(preview_lines) > 60:
        print(f"  {DIM}... ({len(preview_lines) - 60} more lines){RESET}")
    print()

    # ── Step 7: Confirm save ──
    try:
        confirm = input(f"  {BOLD}Save enriched PRD? (y/n):{RESET} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        confirm = "n"

    if confirm in ("y", "yes"):
        prd_file.write_text(enriched_prd + "\n", encoding="utf-8")
        print(f"\n  {GREEN}✔ PRD saved to {prd_file}{RESET}")
    else:
        print(f"\n  {DIM}PRD not modified.{RESET}")

    # ── Step 8: Save transcript ──
    transcript_path = _save_transcript(experts, questions, qa_transcript, enriched_prd)
    print(f"  {DIM}Transcript: {transcript_path}{RESET}")

    if not _called_from_runner:
        print(f"\n  {BOLD}PRD ready. Run 'forja run' to build.{RESET}")
    print()
    return True
