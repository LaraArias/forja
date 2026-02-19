# Forja — Landing Page

Single-page landing that explains what Forja does, shows the pipeline visually, and gets developers to install it.

## Hero

- Headline: "From document to working software."
- Subline: "Forja is an autonomous software factory. Give it a PRD, get tested, validated software — not a snippet, a full project with architecture, tests, and metrics."
- CTA button: "Get Started" → links to #getting-started section
- Secondary CTA: "View on GitHub" → https://github.com/LaraArias/forja

## Problem Section

- Title: "Speed without direction builds the wrong thing faster."
- Body: AI coding tools generate code fast. But they don't understand what you're building or why. Forja forces outcome-driven development: understand the problem first, then build.
- Visual: simple before/after. Before: "PRD → AI → code (maybe wrong)". After: "PRD → Forja → validated software (measured)".

## Pipeline Section — "How It Works"

Visual horizontal pipeline showing 6 phases connected by arrows. Each phase is a card with icon, name, and one-liner:

1. **Plan Mode** — Expert panel interrogates your idea. Two rounds: WHAT (product) then HOW (technical). Every answer tagged FACT / DECISION / ASSUMPTION.
2. **Spec Review** — Independent model (Kimi) finds gaps and ambiguities. Auto-enriches the PRD with discovered specs.
3. **Parallel Build** — Claude Code Agent Teams: 2-4 specialized agents build simultaneously. Live progress bar. Stalled features auto-blocked after 8 minutes.
4. **Outcome Evaluation** — Different model reviews what was built against the original requirements. Coverage % with met/unmet lists.
5. **Learnings** — Failures extracted as JSONL. Next run starts with institutional knowledge. Each iteration gets smarter.
6. **Observatory** — Live dashboard tracking everything: features, cycles, expert insights, coverage, iteration deltas.

## Observatory Section

- Title: "See everything. Miss nothing."
- Show the observatory dashboard interface prominently — this is the main visual feature of the page
- Description: Real-time HTML dashboard with Chart.js charts showing:
  - Pipeline status (spec-review, plan, build, outcome, learnings — each with pass/warn/fail)
  - Iteration header: Run #N, agent activity (dots showing done/active/waiting per teammate), delta vs previous run
  - Features by teammate (stacked bar chart: passed/blocked/failed)
  - Quality over time (line chart: features passed + coverage % across runs)
  - Learnings manifest (next fixes for the upcoming run)
  - Roadmap with per-teammate feature blocks (color-coded by status)
- Dark theme with accent color #00E5B0
- This section should feel like a product screenshot — the observatory IS the proof that Forja works

## Key Features Grid

4 cards, 2x2 grid:

1. **Multi-Model Validation** — Kimi reviews Claude's output at spec review and code review. You don't grade your own homework.
2. **Context Engineering** — Business context in `context/` survives across builds. Every run makes the next one better.
3. **Iterative Learning** — `forja iterate`: review failures, give feedback, improve PRD, re-run. Learnings stack across iterations.
4. **Workflow Pipelines** — Skills define sequential agent workflows (content-strategist → frontend-builder → QA) with artifact handoffs.

## Getting Started Section

```
pip install forja
forja config    # Set API keys (Kimi + Anthropic)
forja init      # Pick a skill, describe your project
forja run       # Watch the pipeline build
forja report    # View observatory dashboard
forja iterate   # Review, improve, re-run
```

Requirements listed below: Python 3.9+, Claude Code CLI, Kimi API key (free), Anthropic API key.

## Footer

- "Created by Carlos Lara — Head of Product @ Saptiva AI"
- Links: GitHub, MIT License
- Built with Forja (meta)

## Technical Constraints

- Single HTML file, no framework — vanilla HTML + CSS + JS
- Dark theme: background #0f172a, surface #1e293b, accent #00E5B0
- Mobile responsive
- No external images — all visuals built with CSS/SVG/HTML
- Smooth scroll between sections
- Lightweight — no heavy dependencies beyond a single Chart.js CDN link if needed for the observatory mockup
