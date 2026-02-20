"""Forja project-wide constants — paths, model names, markers."""

from __future__ import annotations

from pathlib import Path

# ── Project structure paths (relative to project root) ───────────
PRD_PATH = Path("context/prd.md")
CLAUDE_MD = Path("CLAUDE.md")
FORJA_TOOLS = Path(".forja-tools")
CONTEXT_DIR = Path("context")
STORE_DIR = Path("context/store")
LEARNINGS_DIR = Path("context/learnings")
TEAMMATES_DIR = Path("context/teammates")
SPECS_DIR = Path("specs")
FORJA_DIR = Path(".forja")
WORKFLOW_PATH = FORJA_DIR / "workflow.json"

# ── LLM model identifiers ──
# Model names are managed by config_loader.py (see _DEFAULTS dict).
# Templates use their own constants since they run without forja imports.

# ── Project markers (used to detect an existing Forja project) ──
PROJECT_MARKERS = [CLAUDE_MD, FORJA_TOOLS]

# ── Build prompt (the instruction sent to Claude Code) ──────────
BUILD_PROMPT = "Read CLAUDE.md and execute Forja with the PRD in context/prd.md"

# ── Decomposition prompt (Phase 2a — plan only, no build) ──────
DECOMPOSE_PROMPT = (
    "Read CLAUDE.md and execute ONLY Steps 0-7 (preflight, read PRD, "
    "decompose into epics, generate teammate artifacts, QA teammate, "
    "teammate_map.json, post-plan validation). Do NOT execute Step 8 "
    "(Create Agent Team) — stop after generating all artifacts. "
    "The PRD is at context/prd.md."
)

# ── Feature prompt (Phase 2b — one per teammate, fresh context) ──
FEATURE_PROMPT = (
    "Read context/teammates/{name}/CLAUDE.md and implement all features. "
    "Commit after each task with structured metadata. "
    "When done, ensure all features in "
    "context/teammates/{name}/features.json have status 'passed'."
)

# ── Enrichment prompt (sent to Claude Code for PRD enrichment) ──
ENRICHMENT_PROMPT = (
    "Read .forja/enrichment-instructions.md for the full enrichment plan. "
    "Then read each spec file listed there. Apply all decisions and feedback "
    "to enrich the specs IN PLACE. Also read context/learnings/_learnings.md "
    "and .forja/outcome-report.json for additional context. "
    "If the project uses a specific tech stack, you may search the web "
    "for best practices relevant to the failed features. "
    "Do NOT create new files. Only edit existing spec files."
)

# ── Forja feature glossary (used to enrich user feedback in iterate) ──
FORJA_FEATURES_GLOSSARY = {
    "observatory": {
        "name": "Observatory Dashboard",
        "description": (
            "Real-time HTML dashboard at .forja/observatory/evals.html with Chart.js charts "
            "showing build quality, feature pass rates, iteration deltas, and run history. "
            "Auto-generated after each run. View with: forja report"
        ),
        "keywords": ["observatory", "dashboard", "metrics", "charts", "report"],
    },
    "learnings": {
        "name": "Learnings System",
        "description": (
            "Persistent knowledge extracted from build failures, stored in context/learnings/ "
            "as JSONL. Auto-injected into CLAUDE.md as 'CRITICAL: Previous Run Learnings' "
            "before each run. Includes dependencies to install, validation rules, and PRD patterns."
        ),
        "keywords": ["learnings", "knowledge", "previous run", "manifest"],
    },
    "spec-review": {
        "name": "Spec Review & Enrichment",
        "description": (
            "Phase 0 analysis detecting gaps in the PRD. Auto-enriches the PRD with missing "
            "requirements as '## Additional Specifications'. Results in .forja/spec-enrichment.json."
        ),
        "keywords": ["spec review", "spec-review", "enrichment", "gaps", "specifications"],
    },
    "smoke-test": {
        "name": "Server Health Smoke Test & Endpoint Probes",
        "description": (
            "Phase 3 verification that the built application actually starts and responds "
            "to HTTP requests. After health checks, probes ALL endpoints from validation_spec.json "
            "with proper HTTP methods and payloads. Captures immutable runtime trace to "
            ".forja/runtime-trace.json. Smoke results in .forja/smoke-test.json."
        ),
        "keywords": ["smoke", "health", "server", "probe", "endpoint", "trace", "runtime"],
    },
    "visual-eval": {
        "name": "Visual Evaluation",
        "description": (
            "Phase 5 screenshot-based evaluation using vision LLM. Captures desktop and "
            "mobile screenshots via Playwright, sends them to Claude/GPT-4o for assessment "
            "against PRD requirements. Results in .forja/visual-eval.json."
        ),
        "keywords": ["visual", "screenshot", "visual-eval", "layout", "responsive"],
    },
    "outcome": {
        "name": "Outcome Evaluation",
        "description": (
            "Phase 6 evaluation checking built features vs PRD requirements. Reports coverage % "
            "and met/unmet lists. Results in .forja/outcome-report.json."
        ),
        "keywords": ["outcome", "coverage", "requirements", "evaluation"],
    },
    "iterate": {
        "name": "Iteration Loop",
        "description": (
            "Human feedback loop (forja iterate): review failures, improve PRD with feedback, "
            "re-run pipeline. Learnings and context carry forward between iterations."
        ),
        "keywords": ["iterate", "iteration", "feedback loop", "re-run"],
    },
}
