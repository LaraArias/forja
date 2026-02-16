# Forja

Autonomous software factory. PRD in, tested software out.

Forja turns a Product Requirements Document into working, tested software
by orchestrating Claude Code Agent Teams through a structured pipeline.
No manual coding. No copy-paste prompts. One command.

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

# Build everything
forja run
```

`forja init` picks a skill (Landing Page, API Backend, or Custom), walks
through an interactive setup that generates business context, domain files,
and a design system, then launches Plan Mode automatically. Edit
`context/prd.md`, then run `forja run`.

```bash
# Other commands
forja plan              # Re-run expert panel on PRD
forja status            # Feature progress per teammate
forja report            # Regenerate observatory dashboard
forja init --upgrade    # Update templates without re-running setup
```

---

## How It Works

Six-phase pipeline, fully autonomous:

```
PRD.md
  |
  v
+--------------------+
| 0. Spec Review     |  Kimi analyzes PRD for gaps and ambiguities
+--------------------+  auto-appends enrichment to PRD
  |
  v
+--------------------+
| 1. Context Inject  |  Store + learnings + business context
+--------------------+  injected into CLAUDE.md for all agents
  |
  v
+--------------------+
| 2. Build           |  Claude Code spawns 2-4 teammates
+--------------------+  each owns one epic, builds in parallel
  |
  v
+--------------------+
| 3. Outcome Eval    |  PRD coverage score (met vs unmet)
+--------------------+  powered by Kimi cross-model review
  |
  v
+--------------------+
| 4. Learnings       |  Patterns extracted and saved
+--------------------+  reused on the next run
  |
  v
+--------------------+
| 5. Observatory     |  HTML dashboard with full metrics
+--------------------+

Tested software + dashboard
```

**Phase 0 -- Spec Review.**
Kimi reads the PRD and returns ambiguities, gaps, and implicit assumptions.
The runner auto-appends enrichment to the PRD so agents get a cleaner spec.

**Phase 1 -- Context Injection.**
The runner reads `context/_index.md`, the decision store, the learnings
manifest, and all business context files. Everything is injected into
CLAUDE.md so every agent starts with the same information.

**Phase 2 -- Build.**
Claude Code is spawned with the enriched CLAUDE.md. The lead agent (Head of
Product) decomposes the PRD into 2-4 epics, generates teammate instructions,
and launches Agent Teams. Each teammate owns one epic and builds independently.
A QA teammate runs integration tests after all builders finish. Progress is
monitored with a live spinner and progress bar. The build uses process group
management (`os.setsid` / `os.killpg`) to ensure no zombie processes.

**Phase 3 -- Outcome Evaluation.**
Kimi compares the PRD requirements against what was actually built.
Returns a coverage percentage and lists met vs unmet requirements.

**Phase 4 -- Extract Learnings.**
Patterns from the outcome report, cross-model reviews, and git commits
are extracted and stored as append-only JSONL in `context/learnings/`.

**Phase 5 -- Observatory.**
All artifacts are collected into a single-file HTML dashboard at
`.forja/observatory/evals.html`.

---

## Features

### Plan Mode

Interactive PRD enrichment before a single line of code is written.
Kimi assembles 3 domain experts based on your project. They ask hard
questions, challenge assumptions, and force clarity. Every answer is
classified as FACT, DECISION, or ASSUMPTION and tracked in the final
document. The output includes an Assumption Density metric so you know
exactly how solid your spec is.

```bash
forja plan
```

### Skills and Specialized Agents

Pre-built agent team configurations for common project types.
Each skill defines agents with specific roles and context-aware prompts.

**Landing Page** -- 5 agents: content strategist, frontend builder,
UX reviewer, SEO optimizer, QA. QA uses Playwright for browser testing
with screenshots saved to `.forja/screenshots/`.

**API Backend** -- 5 agents: architect, database, backend, security, QA.
QA uses httpx to test every endpoint against a live server, verifying
status codes and response bodies.

Custom skills use the default Claude Code Agent Teams without predefined roles.

### Project Configuration (forja.toml)

Per-project settings live in `forja.toml` at the project root. Controls
build timeouts, model selection, context paths, and observatory behavior.
Values can be overridden with environment variables (`FORJA_<SECTION>_<KEY>`).

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

### Multi-Model Validation

Claude never reviews its own code. After the build phase, `forja_crossmodel.py`
sends the output to Kimi or Saptiva for an independent review. This catches
blind spots that self-review misses. Providers are tried in a fallback chain:
Kimi first, then Saptiva.

Before the build even starts, `forja_specreview.py` has Kimi analyze the PRD
for ambiguities, contradictions, and missing edge cases. Findings are
automatically appended to the PRD.

### Learnings System

Forja extracts patterns from every run and reuses them in the next one.
After each build, `forja_learnings.py` reads outcomes, cross-model reviews,
and git history to find what worked and what failed. Learnings are stored
as append-only JSONL files in `context/learnings/`.

On the next run, the learnings manifest is injected into CLAUDE.md so agents
start with institutional knowledge instead of a blank slate.

### Observatory Dashboard

A single-file HTML dashboard generated after each run. Includes spec review
results, build progress, cross-model reports, outcome evaluation, learnings,
and a per-feature event timeline showing pass/fail/blocked events with
timestamps.

In live mode, the observatory runs in the background during the build and
updates in real time.

```
.forja/observatory/evals.html
```

### Structured Logging

Debug logging available with `--verbose` / `-v`. Output is TTY-aware:
compact timestamps for interactive use, structured `[LEVEL] name: message`
format for pipes and CI.

```bash
forja -v run
```

### Robustness

- **PID lock file** (`.forja/runner.pid`) prevents concurrent `forja run` executions
- **Atomic file writes** for `features.json` via temp file + rename
- **Process group management** ensures child processes are cleaned up on exit or timeout
- **Template versioning** -- each template carries a `FORJA_TEMPLATE_VERSION` marker.
  `forja init --upgrade` updates templates in-place without re-running the full setup.
  Preflight warns when installed templates are outdated.
- **Per-feature event log** at `.forja/feature-events.jsonl` tracks every attempt,
  pass, fail, and blocked event with timestamps and cycle counts
- **Build log capture** at `.forja/logs/build.log`

---

## Architecture

```
forja CLI (--verbose)
  |
  +-- config     ->  config.py         API key management (~/.forja/config.env)
  +-- init       ->  init.py           Scaffold project + interactive context setup
  +-- init --upgrade                   Update templates only
  +-- plan       ->  planner.py        Plan Mode (expert panel via Kimi)
  +-- run        ->  runner.py         6-phase pipeline orchestration
  +-- status     ->  status.py         Feature progress per teammate
  +-- report     ->  (observatory)     Dashboard generation

Configuration:
  forja.toml                           Per-project settings (timeouts, models, paths)
  config_loader.py                     TOML parser + env var overrides

Templates (copied to .forja-tools/ on init):
  forja_preflight.py       Pre/post-plan validation
  forja_validator.py       Zero-LLM deterministic file checks (AST, brackets)
  forja_features.py        Per-teammate feature tracker + event log
  forja_context.py         Context Engine (persistent decisions)
  forja_crossmodel.py      Independent code review via Kimi/Saptiva
  forja_hardening.py       AI-generated edge case testing
  forja_outcome.py         PRD coverage scoring
  forja_specreview.py      Pre-build PRD analysis
  forja_learnings.py       Cross-run learning extraction
  forja_observatory.py     Metrics dashboard + feature event timeline
  forja_qa_playwright.py   Browser testing for frontend projects
```

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

## Testing

```bash
pip install -e .
pytest
```

263 tests covering pipeline orchestration, configuration loading, feature
tracking, security validation, learnings extraction, QA skills, and more.

---

## Built With

- [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) -- Agent Teams for parallel multi-agent builds
- [Kimi API](https://platform.moonshot.cn/) -- Multi-model validation and expert panel
- Python standard library only -- zero runtime dependencies

---

## Origin

Built by [Saptiva AI](https://saptiva.com).

---

## License

MIT
