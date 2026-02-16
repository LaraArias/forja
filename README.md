# Forja

Autonomous software factory. PRD in, tested software out.

Forja turns a Product Requirements Document into working, tested software
by orchestrating Claude Code Agent Teams through a structured pipeline.
No manual coding. No copy-paste prompts. One command.

---

## The Problem

Building software with AI assistants today means:

- Writing prompts over and over for each piece of the system
- Losing context when conversations get too long
- No validation that the output actually matches what you asked for
- No memory between sessions -- the AI forgets everything
- No way to catch blind spots when Claude reviews its own code

Forja solves all of these with a pipeline that plans, builds, validates,
and learns -- autonomously.

---

## How It Works

```
PRD.md
  |
  v
+------------------+
|   Spec Review    |  Kimi analyzes PRD for gaps and ambiguities
+------------------+  auto-appends enrichment
  |
  v
+------------------+
|   God Plan Mode  |  3 domain experts debate your PRD
+------------------+  facts, decisions, assumptions tracked
  |
  v
+------------------+
|  Context Inject  |  Store + learnings + business context
+------------------+  injected into CLAUDE.md
  |
  v
+------------------+
|  Agent Teams     |  Claude Code spawns 2-4 teammates
+------------------+  each owns one epic, builds in parallel
  |
  v
+------------------+
| Cross-Model QA   |  Kimi reviews code (not Claude)
+------------------+  independent, unbiased validation
  |
  v
+------------------+
| Outcome Eval     |  PRD coverage score
+------------------+  met vs unmet requirements
  |
  v
+------------------+
| Extract Learnings|  Patterns saved for next run
+------------------+
  |
  v
+------------------+
|   Observatory    |  HTML dashboard with full metrics
+------------------+

Tested software + dashboard
```

---

## Quick Start

```bash
# Install
pip install -e .

# Configure API keys
forja config

# Create a new project
forja init my-project
cd my-project

# Enrich your PRD with expert panel (optional)
forja plan context/prd.md

# Build everything
forja run context/prd.md

# Check progress
forja status
```

During `forja init`, you pick a skill (Landing Page, API Backend, or Custom)
and walk through an interactive setup that generates business context,
domain files, and a design system -- all fed to agents during the build.

---

## Features

### God Plan Mode

Interactive PRD enrichment before a single line of code is written.
Kimi assembles 3 domain experts based on your project. They ask hard
questions, challenge assumptions, and force clarity. Every answer is
classified as FACT, DECISION, or ASSUMPTION and tracked in the final
document. The output includes an Assumption Density metric so you know
exactly how solid your spec is.

```bash
forja plan context/prd.md
```

### Skills and Specialized Agents

Pre-built agent team configurations for common project types.
Each skill defines agents with specific roles and context-aware prompts.

**Landing Page** -- 5 agents: content strategist, frontend builder,
UX reviewer, SEO optimizer, QA. The content strategist reads your
domain files for messaging. The frontend builder follows your design system.

**API Backend** -- 5 agents: architect, database, backend, security, QA.
The architect reads `_index.md` first to understand the full context map.
Security checks domain files for compliance requirements.

Custom skills use the default Claude Code Agent Teams without predefined roles.

### Context Engineering

Business context lives in `context/` and is injected into every build:

```
context/
  _index.md                     # Context map (agents read this first)
  company/company-overview.md   # Who you are, what you sell
  domains/DOMAIN.md             # Audience, drivers, anti-patterns
  domains/value-props.md        # Value propositions with proof points
  domains/objections.md         # Objection handling strategies
  design-system/colors.json     # Brand color palette
  design-system/typography.json # Font stack and sizes
  design-system/DESIGN-REFERENCE.md  # Full visual guide
```

The Context Engine (`forja_context.py`) persists architecture decisions
across compactions. When Claude's context window fills up and resets,
decisions survive because they are written to `context/store/` as JSON files.

`_index.md` is auto-generated during init and serves as a map -- agents
read it first to know which file answers which question.

### Multi-Model Validation

Claude never reviews its own code. After the build phase, `forja_crossmodel.py`
sends the output to Kimi or Saptiva for an independent review. This catches
blind spots that self-review misses. The reviewer returns pass/fail with
severity-ranked issues.

Before the build even starts, `forja_specreview.py` has Kimi analyze the PRD
for ambiguities, contradictions, and missing edge cases. Findings are
automatically appended to the PRD so agents get a cleaner spec.

### Learnings System

Forja extracts patterns from every run and reuses them in the next one.
After each build, `forja_learnings.py` reads outcomes, cross-model reviews,
and git history to find what worked and what failed. Learnings are stored
as append-only JSONL files in `context/learnings/`.

On the next run, the learnings manifest is injected into CLAUDE.md so agents
start with institutional knowledge instead of a blank slate.

### Observatory Dashboard

A single-file HTML dashboard generated after each run. It reads every
artifact Forja produced -- spec review, plan, build logs, cross-model
reports, outcome evaluation, learnings -- and renders them as an
interactive report.

In live mode, the observatory runs in the background during the build
and updates in real time so you can watch progress without opening
Claude Code.

```
.forja/observatory/evals.html
```

---

## Architecture

```
forja CLI
  |
  +-- config     ->  config.py       API key management (~/.forja/config.env)
  +-- init       ->  init.py         Scaffold project + interactive context setup
  +-- plan       ->  planner.py      God Plan Mode (expert panel via Kimi)
  +-- run        ->  runner.py       Full pipeline orchestration
  +-- status     ->  status.py       Feature progress per teammate
  +-- report     ->  (observatory)   Dashboard generation

Templates (copied to .forja-tools/ on init):
  forja_preflight.py     Pre/post-plan validation
  forja_validator.py     Zero-LLM deterministic file checks (AST, brackets)
  forja_features.py      Per-teammate feature tracker
  forja_context.py       Context Engine (persistent decisions)
  forja_crossmodel.py    Independent code review via Kimi/Saptiva
  forja_hardening.py     AI-generated edge case testing
  forja_outcome.py       PRD coverage scoring
  forja_specreview.py    Pre-build PRD analysis
  forja_learnings.py     Cross-run learning extraction
  forja_observatory.py   Metrics dashboard generator
```

---

## Pipeline Detail

**Phase 0 -- Spec Review.**
Kimi reads the PRD and returns a list of ambiguities, gaps, and implicit
assumptions. The runner auto-appends the enrichment to the PRD. This phase
is informational and never blocks the build.

**Phase 1 -- Context Injection.**
The runner reads `context/_index.md`, the decision store, the learnings
manifest, and all business context files. Everything is injected into
CLAUDE.md under a shared context section so every agent has the same
information.

**Phase 2 -- Build.**
Claude Code is spawned with the enriched CLAUDE.md. The lead agent (Head of
Product) decomposes the PRD into 2-4 epics, generates teammate instructions,
and launches Agent Teams. Each teammate owns one epic and builds independently.
A QA teammate runs integration tests. Progress is monitored with a live
spinner and progress bar. QA has a 12-minute stall timeout at >80% and a
20-minute absolute timeout so it never blocks indefinitely.

**Phase 3 -- Outcome Evaluation.**
Kimi compares the PRD requirements against what was actually built.
Returns a coverage percentage and lists met vs unmet requirements.

**Phase 4 -- Extract Learnings.**
Patterns from the outcome report, cross-model reviews, and git commits
are extracted and stored in `context/learnings/` for the next run.

**Phase 5 -- Observatory.**
All artifacts are collected into a single-file HTML dashboard.

---

## Requirements

- Python 3.9+
- Claude Code CLI (`claude`) installed and authenticated
- API keys for at least one of: Anthropic, Kimi (Moonshot AI), Saptiva

---

## Configuration

```bash
forja config
```

Stores API keys in `~/.forja/config.env` with 600 permissions.
Keys are also loaded from a local `.env` file if present.

| Key                | Service              | Used For                          |
|--------------------|----------------------|-----------------------------------|
| ANTHROPIC_API_KEY  | Anthropic (Claude)   | Main build engine                 |
| KIMI_API_KEY       | Moonshot AI (Kimi)   | Expert panel, spec review, QA     |
| SAPTIVA_API_KEY    | Saptiva              | Fallback cross-model validation   |

---

## Built With

- [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) -- Agent Teams for parallel multi-agent builds
- [Kimi API](https://platform.moonshot.cn/) -- Multi-model validation and expert panel
- Python standard library only -- zero runtime dependencies

---

## Results

A typical `forja run` on a landing page PRD produces:

- 4-5 teammates working in parallel
- Full HTML/CSS/JS site matching the PRD
- Cross-model code review report
- PRD coverage score (target: >85%)
- Learnings extracted for next iteration
- Observatory dashboard at `.forja/observatory/evals.html`

---

## License

MIT
