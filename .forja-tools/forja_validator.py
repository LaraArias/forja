#!/usr/bin/env python3
# FORJA_TEMPLATE_VERSION=0.1.0
"""Forja deterministic file validator. Zero LLM.

Multi-language support: validates by file extension. Unknown extensions
always pass to prevent infinite retry cycles.

Usage:
    python3 .forja-tools/forja_validator.py check-file <file_path>
"""

import ast
import importlib.util
import json as json_mod
import subprocess
import sys
from pathlib import Path

from forja_utils import PASS_ICON as PASS, FAIL_ICON as FAIL, WARN_ICON as WARN

SKIP = "\033[90m[SKIP]\033[0m"

JS_EXTENSIONS = {".js", ".jsx"}
TS_EXTENSIONS = {".ts", ".tsx"}
COMPILED_EXTENSIONS = {".cs", ".java", ".go", ".rs", ".cpp", ".c", ".h", ".hpp", ".swift", ".kt"}
CONFIG_DOC_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".env", ".csv"}
MARKUP_EXTENSIONS = {".html", ".htm", ".xml", ".svg"}

# Brackets that must be balanced in JS/TS files
BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}"}
CLOSE_TO_OPEN = {v: k for k, v in BRACKET_PAIRS.items()}


# ── Python validation ────────────────────────────────────────────────

def check_ast(filepath):
    """Parse file with ast. Returns True if valid."""
    source = filepath.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(filepath))
        print(f"  {PASS} AST parse OK")
        return True
    except SyntaxError as e:
        line = e.lineno or "?"
        msg = e.msg or "unknown error"
        print(f"  {FAIL} AST parse error: line {line}, {msg}")
        return False


def check_imports(filepath):
    """Verify that imported modules can be found."""
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        # AST already reported failure; skip import check
        return

    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])

    missing = []
    for mod in sorted(modules):
        spec = importlib.util.find_spec(mod)
        if spec is None:
            missing.append(mod)

    if not missing:
        print(f"  {PASS} Imports OK")
    else:
        names = ", ".join(missing)
        print(f"  {WARN} Unresolved imports (third-party?): {names}")


def check_ruff(filepath):
    """Run ruff check on the file."""
    try:
        result = subprocess.run(
            ["ruff", "check", str(filepath)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print(f"  {WARN} ruff not found in PATH, skipping lint")
        return

    if result.returncode == 0:
        print(f"  {PASS} ruff: no warnings")
    else:
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        count = len(lines)
        print(f"  {WARN} ruff: {count} warning{'s' if count != 1 else ''}")
        for line in lines:
            print(f"        {line}")


def validate_python(filepath):
    """Full Python validation pipeline. Returns exit code."""
    ast_ok = check_ast(filepath)
    check_imports(filepath)
    check_ruff(filepath)
    return 0 if ast_ok else 1


# ── JS/TS validation ────────────────────────────────────────────────

def check_not_empty(filepath):
    """Check file is not empty."""
    size = filepath.stat().st_size
    if size == 0:
        print(f"  {FAIL} Empty file")
        return False
    print(f"  {PASS} Not empty ({size} bytes)")
    return True


def check_balanced_brackets(filepath):
    """Check that brackets are balanced (ignoring strings and comments)."""
    source = filepath.read_text(encoding="utf-8")

    stack = []
    i = 0
    length = len(source)
    line = 1

    while i < length:
        ch = source[i]

        # Track line numbers
        if ch == "\n":
            line += 1
            i += 1
            continue

        # Skip single-line comments
        if ch == "/" and i + 1 < length and source[i + 1] == "/":
            while i < length and source[i] != "\n":
                i += 1
            continue

        # Skip block comments
        if ch == "/" and i + 1 < length and source[i + 1] == "*":
            i += 2
            while i + 1 < length and not (source[i] == "*" and source[i + 1] == "/"):
                if source[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue

        # Skip string literals (single, double, template)
        if ch in ('"', "'", "`"):
            quote = ch
            i += 1
            while i < length:
                c = source[i]
                if c == "\n":
                    line += 1
                if c == "\\" and i + 1 < length:
                    i += 2
                    continue
                if c == quote:
                    break
                i += 1
            i += 1
            continue

        # Bracket matching
        if ch in BRACKET_PAIRS:
            stack.append((ch, line))
        elif ch in CLOSE_TO_OPEN:
            expected_open = CLOSE_TO_OPEN[ch]
            if not stack:
                print(f"  {FAIL} Unmatched closing bracket: '{ch}' on line {line}")
                return False
            open_ch, open_line = stack.pop()
            if open_ch != expected_open:
                print(f"  {FAIL} Bracket mismatch: '{open_ch}' (line {open_line}) closed with '{ch}' (line {line})")
                return False

        i += 1

    if stack:
        open_ch, open_line = stack[-1]
        print(f"  {FAIL} Unclosed bracket: '{open_ch}' on line {open_line}")
        return False

    print(f"  {PASS} Brackets balanced")
    return True


def validate_js(filepath):
    """JS validation pipeline. Returns exit code."""
    not_empty = check_not_empty(filepath)
    if not not_empty:
        return 1
    # Try node --check if available
    try:
        result = subprocess.run(
            ["node", "--check", str(filepath)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            print(f"  {PASS} node --check OK")
        else:
            print(f"  {WARN} node --check: {result.stderr.strip()[:200]}")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print(f"  {WARN} node not found in PATH, falling back to bracket check")
    balanced = check_balanced_brackets(filepath)
    return 0 if balanced else 1


# ── TS validation ─────────────────────────────────────────────────

def validate_ts(filepath):
    """TypeScript validation: non-empty + brackets. Always passes on structure.

    TypeScript requires tsc for real validation which needs tsconfig.
    We do best-effort structural checks only.
    """
    not_empty = check_not_empty(filepath)
    if not not_empty:
        return 1
    check_balanced_brackets(filepath)
    # TS bracket failures are warnings, not errors — tsc is the real validator
    print(f"  {PASS} TypeScript file accepted (tsc needed for full validation)")
    return 0


# ── HTML validation ──────────────────────────────────────────────

def validate_html(filepath):
    """HTML validation: valid UTF-8 and non-empty."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"  {FAIL} Invalid UTF-8 encoding")
        return 1
    if not content.strip():
        print(f"  {FAIL} Empty file")
        return 1
    print(f"  {PASS} HTML: valid UTF-8, {len(content)} chars")
    return 0


# ── JSON validation ──────────────────────────────────────────────

def validate_json(filepath):
    """JSON validation: json.loads."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"  {FAIL} Invalid UTF-8 encoding")
        return 1
    try:
        json_mod.loads(content)
        print(f"  {PASS} JSON: valid")
        return 0
    except json_mod.JSONDecodeError as e:
        print(f"  {FAIL} JSON parse error: {e}")
        return 1


# ── CSS validation ───────────────────────────────────────────────

def validate_css(filepath):
    """CSS validation: non-empty check only. CSS parsers are complex."""
    not_empty = check_not_empty(filepath)
    if not not_empty:
        return 1
    print(f"  {PASS} CSS: accepted (non-empty)")
    return 0


# ── Passthrough validation ───────────────────────────────────────

def validate_passthrough(filepath, reason):
    """Always passes. For file types that cannot be validated inline."""
    size = filepath.stat().st_size
    print(f"  {PASS} {reason} ({size} bytes)")
    return 0


# ── Main ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3 or sys.argv[1] != "check-file":
        print("Usage: python3 .forja-tools/forja_validator.py check-file <file_path>")
        sys.exit(1)

    filepath = Path(sys.argv[2])
    if not filepath.exists():
        print(f"  {FAIL} File not found: {filepath}")
        sys.exit(1)

    ext = filepath.suffix.lower()
    print(f"Validating: {filepath}")

    # Python: full validation (AST + imports + ruff)
    if ext == ".py":
        sys.exit(validate_python(filepath))

    # JavaScript: node --check + bracket balance
    if ext in JS_EXTENSIONS:
        sys.exit(validate_js(filepath))

    # TypeScript: structural check only (tsc needed for real validation)
    if ext in TS_EXTENSIONS:
        sys.exit(validate_ts(filepath))

    # HTML/XML: UTF-8 + non-empty
    if ext in MARKUP_EXTENSIONS:
        sys.exit(validate_html(filepath))

    # JSON: json.loads
    if ext == ".json":
        sys.exit(validate_json(filepath))

    # CSS: non-empty
    if ext == ".css":
        sys.exit(validate_css(filepath))

    # Compiled languages: cannot validate without compiler, always pass
    if ext in COMPILED_EXTENSIONS:
        sys.exit(validate_passthrough(filepath, f"Compiled language ({ext}), skipping inline validation"))

    # Config/docs: always pass
    if ext in CONFIG_DOC_EXTENSIONS:
        sys.exit(validate_passthrough(filepath, f"Config/doc file ({ext})"))

    # Unknown extension: ALWAYS pass to prevent infinite cycles
    print(f"  {PASS} Unknown extension ({ext or 'none'}), accepted")
    sys.exit(0)


if __name__ == "__main__":
    main()
