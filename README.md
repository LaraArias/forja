# Forja

**From document to working software.**

Forja is an autonomous software factory powered by Claude Code. You write a product requirements document, Forja assembles an expert panel to interrogate your idea, then builds, tests, and validates the entire project — with fresh context per feature, parallel execution, and cross-model review.

No boilerplate. No scaffolding. You describe what you want, Forja builds it.

```
forja init my-project    # Describe your idea
forja run                # Watch it build
forja report             # See the results
```

---

## What Makes Forja Different

**Expert Panel Before Code** — Before writing a single line, two rounds of domain experts (product + technical) interrogate your idea. Every decision is classified as FACT, DECISION, or ASSUMPTION. High assumption density means you're not ready to build. Experts can research topics with live web search.

**Fresh Context Per Feature** — Instead of one massive Claude session that degrades over time, Forja spawns a fresh Claude Code process per feature with its own 200k-token context window. Feature #4 gets the same quality as Feature #1.

**Parallel Execution** — Independent features run simultaneously. Dependencies are detected from validation specs and grouped into waves. Wave 1 features build in parallel, get verified, then Wave 2 starts with learnings injected.

**Multi-Model Validation** — An independent model reviews Claude's output at spec review and cross-model code review. You don't grade your own homework.

**Context Engineering** — Business context, domain knowledge, design systems, and learnings from previous builds live in `context/` and survive across runs. Every build makes the next one better.

---

## Quick Start

```bash
pip install -e .
forja init my-saas       # Interactive setup: company, audience, design, PRD
forja run                # Full pipeline: plan → build → test → evaluate
forja report             # Open observatory dashboard
```

### Requirements

- Python 3.9+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Kimi API key (free at [platform.moonshot.cn](https://platform.moonshot.cn)) — used for independent spec review

---

## How It Works

### 1. Plan Mode — Expert Panel

```bash
forja init
```

Two rounds of AI-generated domain experts ask critical questions:

- **Round 1 (WHAT)**: Product/strategy experts define what to build
- **Round 2 (HOW)**: Technical experts decide how to build it

You can research any topic mid-conversation, skip rounds, undo edits, and review the enriched PRD before building. The full transcript is saved for context.

### 2. Build Pipeline

```bash
forja run
```

| Phase | What Happens |
|-------|-------------|
| 0. Spec Review | Independent model analyzes PRD for gaps and incompatible dependencies |
| 1. Context Injection | Business context, learnings, and decisions injected into agent instructions |
| 2a. Decomposition | PRD split into independent epics with scoped context per teammate |
| 2b. Build | Fresh Claude Code process per feature, parallel waves, atomic commits |
| 3. Smoke Test | Server health + endpoint probes against validation specs |
| 4. Project Tests | Automated test execution with failure-to-learning extraction |
| 5. Visual Eval | Screenshot-based assessment against PRD (desktop + mobile) |
| 6. Outcome Eval | Coverage scoring: built features vs. original requirements |
| 7. Learnings | Auto-extracted insights stored for next iteration |
| 8. Observatory | Live dashboard with charts, metrics, and build history |

### 3. Iterate

```bash
forja iterate    # Review failures, improve PRD, re-run
```

Learnings from each build carry forward. The system gets smarter with every run.

---

## Skills

Built-in workflow pipelines with artifact handoffs between specialized agents:

**Landing Page** — content-strategist → design-planner → frontend-builder → seo-optimizer → qa (Playwright)

**API Backend** — architect → database → backend → security → qa (httpx)

**Custom** — Claude Code Agent Teams decompose the PRD into epics automatically. Keyword-based guidance adapts the expert panel to your project type (game, CLI, dashboard, bot, pipeline, etc).

---

## Project Structure

```
your-project/
  context/
    prd.md                    # Your requirements (enriched by expert panel)
    _index.md                 # Context map — agents read this first
    company/                  # Business context (from init)
    domains/                  # Industry/audience knowledge
    design-system/            # Visual guidelines
    store/                    # Persistent decisions (survives compaction)
    learnings/                # Auto-extracted from builds
    teammates/                # Generated per-feature agent directories
  .forja-tools/               # Agent scripts (18 tools)
  .forja/
    observatory/evals.html    # Dashboard
    plan-transcript.json      # Expert panel Q&A record
    feature-events.jsonl      # Build timeline
  CLAUDE.md                   # Lead agent instructions
  forja.toml                  # Configuration
```

---

## Configuration

All settings in `forja.toml`:

```toml
[build]
timeout_stall_minutes = 12
timeout_absolute_minutes = 20
max_cycles_per_feature = 5

[models]
anthropic_model = "claude-opus-4-6"
kimi_model = "kimi-k2-0711-preview"

[context]
max_context_chars = 3000
max_learnings_chars = 2000
```

Override with environment variables: `FORJA_BUILD_TIMEOUT_STALL_MINUTES=15`, etc.

---

## Testing

```bash
pytest    # 634 tests
```

---

## Philosophy

When the cost of building approaches zero, the cost of building the *wrong thing* becomes infinite.

AI coding tools generate code fast. Speed without direction produces the wrong thing faster. Forja forces outcome-driven development: understand the problem first, then build.

---

## Origin

Weekend project by [Carlos Lara](https://github.com/carloslara). I wanted to see how far you could push Claude Code with proper context engineering — turns out, pretty far.

<p align="center">
  <a href="https://saptiva.com">
    <img src="assets/saptiva-logo.png" alt="Saptiva AI" width="140">
  </a>
</p>

---

## License

MIT
