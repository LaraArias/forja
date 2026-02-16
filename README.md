# Forja

**From document to working software.**

Forja is an autonomous software factory. You give it a product requirements
document, it gives you tested, validated software — not a snippet, a full
project with architecture, tests, and metrics.

---

## Why Forja Exists

When the cost of building approaches zero, the cost of building the wrong
thing becomes infinite.

AI coding tools generate code fast. But speed without direction produces
the wrong thing faster. Forja forces outcome-driven development: understand
the problem first, then build.

---

## How It Works

```bash
forja config    # Set API keys (one-time setup)
forja init      # Scaffold project, launch Plan Mode
forja run       # Build everything
forja status    # Feature details
forja report    # View metrics dashboard
```

### Plan Mode — Expert Panel (two rounds)

Before writing code, two rounds of domain experts interrogate your idea.
Round 1 (WHAT): product experts define what to build. Round 2 (HOW):
technical experts decide how to build it. Every decision is classified
as FACT, DECISION, or ASSUMPTION. High assumption density means you're
not ready to build. Experts can research topics with live web search.

### Spec Review

An independent model (Kimi) analyzes the PRD for gaps, ambiguities, and
incompatible tech stacks. System dependencies like Redis or Docker are
automatically replaced with buildable alternatives. The PRD is auto-enriched
with discovered specifications.

### Context Injection

Business context, domain knowledge, design systems, and learnings from
previous runs are injected into every agent's instructions. Claude builds
informed, not guessing.

### Parallel Build

Claude Code Agent Teams execute the build with 2-4 specialized agents
working simultaneously. Each agent tracks features with pass/fail
validation. Stalled features are automatically detected and blocked
after 8 minutes. Live progress bar shows real-time build status.

### Outcome Evaluation

A different model reviews what was built against the original requirements.
Coverage percentage is reported with met/unmet requirement lists.

### Extract Learnings

Learnings from the build are automatically extracted and stored as JSONL.
They are applied back to context files so the next run starts with
institutional knowledge.

### Observatory

A live dashboard tracks the full pipeline: features passed, cycles per
feature, expert panel insights, learnings extracted, and outcome coverage.

---

## Key Innovations

**Multi-Model Validation** — Kimi independently reviews Claude's output
at spec review and cross-model code review. You don't grade your own homework.

**Context Engineering** — Business context lives in `context/` and survives
across builds. Every run makes the next one better.

**Workflow Pipelines** — Skills define sequential agent workflows
(content-strategist -> frontend-builder -> QA) with artifact handoffs
between phases, instead of generic parallel execution.

**Iterative Learning** — Learnings from each build are extracted and stored
as JSONL. The next run starts with institutional knowledge.

**Interactive Context Setup** — `forja init` walks you through company,
domain, and design system setup, generating context files via LLM calls
so agents have business context from the start.

---

## Skills

Forja ships with two built-in skills:

**Landing Page** — 5 sequential phases: content-strategist -> design-planner
-> frontend-builder -> seo-optimizer -> qa (Playwright browser tests).

**API Backend** — 5 sequential phases: architect -> database -> backend ->
security -> qa (httpx endpoint tests).

Each phase reads artifacts from the previous one via `forja_handoff.py`.
Custom skills use the default Claude Code Agent Teams without predefined
workflows.

---

## Architecture

```
your-project/
  context/
    prd.md                          # Your requirements
    _index.md                       # Context map (agents read first)
    company/                        # Business context
    domains/                        # Industry knowledge
    design-system/                  # Visual guidelines
    store/                          # Persistent decisions (survives compaction)
    learnings/                      # Auto-extracted from builds (JSONL)
  .forja-tools/                     # Agent scripts (copied on init)
    forja_specreview.py             # Pre-build PRD analysis
    forja_crossmodel.py             # Independent code review via Kimi
    forja_outcome.py                # PRD coverage scoring
    forja_learnings.py              # Cross-run learning extraction
    forja_features.py               # Per-teammate feature tracker
    forja_context.py                # Context Engine (persistent decisions)
    forja_validator.py              # Zero-LLM deterministic file checks
    forja_handoff.py                # Inter-phase artifact passing
    forja_observatory.py            # Metrics dashboard
    forja_hardening.py              # Resilience and error handling
    forja_qa_playwright.py          # Browser testing helper
    forja_preflight.py              # Pre-build validation
    forja_utils.py                  # Shared utilities for agent scripts
    skill.json                      # Active skill agents
    workflow.json                   # Sequential pipeline definition
  .forja/
    observatory/evals.html          # Dashboard
    feature-events.jsonl            # Build timeline
    logs/                           # Build logs
    research/                       # Expert research findings
    backups/                        # Automatic backups
  artifacts/                        # Inter-agent handoffs
  forja.toml                        # Configuration
  CLAUDE.md                         # Lead agent instructions
  .env                              # API keys (not committed)
```

---

## Configuration

All settings in `forja.toml`:

```toml
[build]
timeout_stall_minutes = 12         # Stall timeout when >80% complete
timeout_absolute_minutes = 20      # Absolute stall timeout
max_cycles_per_feature = 5         # Max retries before blocking a feature

[models]
kimi_model = "kimi-k2-0711-preview"
anthropic_model = "claude-sonnet-4-20250514"
openai_model = "gpt-4o"
validation_provider = "auto"       # auto, kimi, anthropic, openai

[context]
max_context_chars = 3000           # Max chars injected into CLAUDE.md
max_learnings_chars = 2000         # Max chars from learnings

[observatory]
live_refresh_seconds = 5           # Dashboard refresh interval
```

Settings can be overridden with environment variables:
`FORJA_BUILD_TIMEOUT_STALL_MINUTES=15`, `FORJA_MODELS_ANTHROPIC_MODEL=...`, etc.

---

## Installation

```bash
pip install -e .
forja config    # Set API keys globally (~/.forja/config.env)
```

Requires:

- Python 3.9+
- Claude Code CLI (`claude`) installed and authenticated
- Kimi API key (free at [platform.moonshot.cn](https://platform.moonshot.cn))
- Anthropic API key (for deep research in Plan Mode)

---

## Testing

```bash
pytest  # 350+ tests
```

---

## Origin

Forja was born from real-world experience building AI agents for production
at Saptiva AI, where we deploy AI infrastructure for banks, government, and
healthcare in Latin America. After orchestrating specialized agents with
MCPs, context-driven workflows, and multi-model validation for enterprise
clients, the patterns became clear: the difference between AI that generates
code and AI that generates the right code is context engineering.

Created by Carlos Lara — Head of Product @ Saptiva AI

---

## License

MIT
