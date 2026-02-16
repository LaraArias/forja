# Overnight Maintenance Log

**Date:** 2026-02-16
**Engineer:** Ralph (overnight maintenance)

## Summary

7 known bugs scanned, analyzed, and fixed. All 359 tests passing throughout.

---

## Bug 1: `forja init` input() prompts skipped (stdin consumed by run_plan)

**Root cause:** `init.py` and `planner.py` called `input()` without draining buffered stdin first. When LLM API calls return, buffered newlines auto-accept prompts.

**Fix:** Imported `_flush_stdin()` from `context_setup.py` and added calls before every `input()` in both files.

**Files changed:**
- `src/forja/init.py` - Added `_flush_stdin` import, calls before `input()` in `_prompt_overwrite()` and `_ask_skill()`
- `src/forja/planner.py` - Added `_flush_stdin` import, calls before all 10 `input()` calls

---

## Bug 2: Spanish strings in codebase

**Root cause:** `.forja-tools/` contained old Spanish-language copies of template scripts that hadn't been updated when templates were translated.

**Fix:** Replaced `.forja-tools/forja_preflight.py`, `.forja-tools/forja_validator.py`, and `.forja-tools/forja_features.py` with current English template versions from `src/forja/templates/`.

**Files changed:**
- `.forja-tools/forja_preflight.py` - Full replacement (was Spanish: "pre-arranque", "Todos los checks pasaron", etc.)
- `.forja-tools/forja_validator.py` - Full replacement (was Spanish: "linea", "Archivo vacio", etc.)
- `.forja-tools/forja_features.py` - Full replacement (was Spanish: "features completados", "invalido", etc.)

---

## Bug 3: `except: pass` blocks silently swallow errors

**Root cause:** ~49 bare `except: pass` blocks across the codebase swallowed errors with no logging, making debugging impossible.

**Fix:** Two categories:
- **Package files** (have `logging` module): Changed to `logger.debug(...)` or `logger.warning(...)`
- **Template files** (standalone, no `logging`): Changed to `print(f"  ...: {exc}", file=sys.stderr)`

**Files changed (package):**
- `src/forja/config_loader.py` - 2 blocks: `_parse_value()` ValueError, `_apply_env_overrides()` ValueError
- `src/forja/planner.py` - 2 blocks: `_detect_skill()` file read, `_gather_context()` learnings read
- `src/forja/runner.py` - 8 blocks: JSON parse, context read, process kill, PID cleanup, auto-block, npm setup, coverage parse, SIGKILL
- `src/forja/utils.py` - 1 block: `gather_context()` file read

**Files changed (templates - print to stderr):**
- `src/forja/templates/forja_utils.py` - 3 blocks: HTTP error body reading
- `src/forja/templates/forja_learnings.py` - 7 blocks: JSONL reading, artifact extraction
- `src/forja/templates/forja_observatory.py` - ~8 blocks: data reading, browser open, PID cleanup
- `src/forja/templates/forja_hardening.py` - 3 blocks: process kill, spec reading
- `src/forja/templates/forja_outcome.py` - 2 blocks: feature/spec reading
- `src/forja/templates/forja_specreview.py` - 3 blocks: store/learnings reading
- `src/forja/templates/forja_context.py` - 1 block: history file reading

---

## Bug 4: Hardcoded model names outside config_loader

**Root cause:** `constants.py` had unused `ANTHROPIC_MODEL`. Template files had hardcoded model names with no way to override them.

**Fix:**
1. Removed unused `ANTHROPIC_MODEL` from `constants.py`
2. Added `_get_model()` function to template `forja_utils.py` that checks `FORJA_MODELS_*` env vars with fallback to defaults
3. Updated all template files to use `_get_model()` or env var overrides instead of hardcoded strings

**Files changed:**
- `src/forja/constants.py` - Removed `ANTHROPIC_MODEL`, added config_loader comment
- `src/forja/templates/forja_utils.py` - Added `_get_model()`, renamed constants to `_DEFAULT_*`, updated `_call_provider()`
- `src/forja/templates/forja_crossmodel.py` - Uses `_get_model("kimi")` and `KIMI_API_URL` from forja_utils
- `src/forja/templates/forja_hardening.py` - Uses `_get_model("kimi")` and `KIMI_API_URL` from forja_utils
- `src/forja/templates/forja_preflight.py` - Uses `FORJA_MODELS_ANTHROPIC_MODEL` env var with default fallback

---

## Bug 5: README.md outdated and wrong

**Root cause:** README didn't reflect current pipeline phases, commands, or configuration options.

**Fix:** Complete rewrite with accurate pipeline description, all CLI commands (`init`, `run`, `plan`, `config`, `status`, `report`), full `forja.toml` config reference, environment variable overrides, and expanded architecture tree.

**Files changed:**
- `README.md` - Full rewrite

---

## Bug 6: `.env.example` may not exist

**Status:** Already exists. No fix needed.

---

## Bug 7: Template drift between `forja_utils.py` and `utils.py`

**Root cause:** Security and resilience improvements made to `src/forja/utils.py` were not backported to the template `src/forja/templates/forja_utils.py`. Key gaps: no `_sanitize_error_body()`, no retry/backoff, no `tools` param, narrow exception catches.

**Fix:** Backported four improvements from `utils.py` to the template:
1. Added `_sanitize_error_body()` - strips secrets from API error output
2. Added `tools` parameter to `_call_anthropic_raw()` and `_call_provider()`
3. Added retry with exponential backoff to `call_llm()`
4. Added broader exception catches (`json.JSONDecodeError`, `KeyError`, `IndexError`, `TypeError`) to all LLM call functions

Intentional differences preserved (template is self-contained, no forja imports):
- Template uses hardcoded model defaults + env var overrides instead of config_loader
- Template has `call_provider()`, `parse_json_array()`, `extract_content()` used by other templates
- Template lacks `Style` class, `setup_logging()`, `gather_context()`, `safe_read_json()` (package-only)

**Files changed:**
- `src/forja/templates/forja_utils.py` - Added `time` import, `_sanitize_error_body()`, `tools` param, retry/backoff, broader catches, `e.reason` in error messages

---

## Test Results

All 359 tests passed consistently after each batch of fixes (6 pytest runs total).
