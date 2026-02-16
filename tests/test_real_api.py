"""Integration tests with REAL Anthropic API calls.

3 tests, 3 API calls. Each covers one pipeline stage.

Run:   python3 -m pytest tests/test_real_api.py -v -s
Skip:  python3 -m pytest tests/ --ignore=tests/test_real_api.py
"""

import os
import re
import pytest
from pathlib import Path


# ── Load API key ─────────────────────────────────────────────────────

def _ensure_api_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    config = Path.home() / ".forja" / "config.env"
    if config.exists():
        for line in config.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


HAS_API_KEY = _ensure_api_key()
pytestmark = pytest.mark.skipif(not HAS_API_KEY, reason="No ANTHROPIC_API_KEY")


# ── Hallucination check ─────────────────────────────────────────────

HALLUCINATION_PATTERNS = [
    r"npm\s+i(nstall)?\s+(-g\s+)?forja",
    r"\b200\s+beta\s+orgs?\b",
    r"\bOWASP\s+ZAP\b",
    r"\bSBOM\b",
    r"\bzero[- ]trust\b",
    r"\bSSO\b",
    r"\bRBAC\b",
    r"\baudit\s+logs?\b",
    r"\bVPC\s+deploy\b",
    r"\bDocker\b",
    r"\bTerraform\b",
    r"\bK8s\b|\bKubernetes\b",
    r"\b\d{2,3}%\s+test\s+coverage\b",
    r"\btestimonials?\s+from\b",
    r"\bbeta\s+users?\b",
    r"\bforja\s+up\b",
]

_NEGATION = [
    "out of scope", "not include", "not supported", "excluded",
    "do not", "don't", "will not", "won't", "avoid",
    "without", "instead of", "not using", "not use",
    "limitations",
]


def _check(text, label):
    violations = []
    for pat in HALLUCINATION_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            window = text[max(0, m.start() - 500):m.start()].lower()
            if any(s in window for s in _NEGATION):
                continue
            # Also check section heading
            preceding = text[:m.start()]
            hi = preceding.rfind("\n#")
            if hi >= 0:
                heading = preceding[hi:].split("\n", 2)[1].lower()
                if any(s in heading for s in ("out of scope", "excluded", "limitations")):
                    continue
            violations.append(f"  '{m.group()}' ← r'{pat}'")
    if violations:
        pytest.fail(f"Hallucination in {label}:\n" + "\n".join(violations))


# ── Call 1: PRD generation with context (1 API call) ─────────────────

def test_prd_generation():
    """_generate_prd_from_idea → valid PRD, context reflected, no hallucinations."""
    from forja.planner import _generate_prd_from_idea

    context = (
        "## COMPANY OVERVIEW\n# BookmarkCLI\n"
        "A Python CLI for saving and searching bookmarks.\n\n"
        "## TARGET AUDIENCE & DOMAIN\nDevelopers who live in the terminal.\n\n"
        "## VALUE PROPOSITIONS\nFaster than opening a browser.\n\n"
        "## OBJECTION HANDLING\n- 'Why not use browser bookmarks?' → "
        "BookmarkCLI works offline and is scriptable.\n"
    )
    prd, title = _generate_prd_from_idea(
        "BookmarkCLI: terminal bookmark manager", skill="custom", context=context,
    )

    assert prd is not None, "PRD is None — API or parse failure"
    assert title is not None
    assert len(title) > 3
    prd_lower = prd.lower()
    assert "feature" in prd_lower
    assert "stack" in prd_lower
    assert "bookmark" in prd_lower
    _check(prd, "PRD generation")


# ── Call 2: Enrichment with hardcoded PRD + Q&A (1 API call) ─────────

def test_enrichment():
    """_generate_enriched_prd → incorporates Q&A, no hallucinations."""
    from forja.planner import _generate_enriched_prd

    original_prd = (
        "# NotesCLI\n\n## Problem\nDevelopers want terminal notes.\n\n"
        "## Features\n1. Add note\n2. Search notes\n3. Export markdown\n\n"
        "## Stack\nPython 3.9+, SQLite, Click\n\n"
        "## Out of Scope\nWeb interface, mobile app, cloud sync\n"
    )
    qa = [
        {"question": "Primary user?", "answer": "Backend devs",
         "expert": "UX", "tag": "DECISION"},
        {"question": "Database?", "answer": "SQLite, no server",
         "expert": "Eng", "tag": "FACT"},
    ]
    experts = [
        {"name": "UX", "field": "UX"},
        {"name": "Eng", "field": "Engineering"},
    ]

    enriched = _generate_enriched_prd(original_prd, qa, experts)

    assert enriched is not None, "Enrichment returned None"
    assert len(enriched) > 100
    assert "note" in enriched.lower()
    assert "sqlite" in enriched.lower()
    _check(enriched, "Enriched PRD")


# ── Call 3: Expert panel assembly (1 API call) ───────────────────────

def test_expert_panel():
    """Expert panel → valid JSON with experts + questions, no hallucinations."""
    from forja.utils import call_llm, parse_json
    from forja.planner import WHAT_PANEL_PROMPT

    prd = (
        "# TaskCLI\n## Problem\nDevs need a CLI task tracker.\n"
        "## Features\n1. Add task\n2. List tasks\n3. Done command\n"
    )
    raw = call_llm(
        f"{WHAT_PANEL_PROMPT}\n\nPRD:\n{prd}\n\nAvailable context:\n",
        system="You are a conductor of expertise. Respond only with valid JSON.",
        provider="anthropic",
    )

    assert raw, "Expert panel returned empty"
    data = parse_json(raw)
    assert data is not None, f"Invalid JSON: {raw[:200]}"

    experts = data.get("experts", [])
    questions = data.get("questions", [])
    assert len(experts) >= 2, f"Expected 2+ experts, got {len(experts)}"
    assert len(questions) >= 3, f"Expected 3+ questions, got {len(questions)}"

    for e in experts:
        assert "name" in e
        assert "field" in e
    for q in questions:
        assert "question" in q
        assert "default" in q

    all_defaults = " ".join(q.get("default", "") for q in questions)
    _check(all_defaults, "Expert panel defaults")
