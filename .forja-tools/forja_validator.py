#!/usr/bin/env python3
"""Forja deterministic file validator. Zero LLM.

Usage:
    python3 .forja-tools/forja_validator.py check-file <ruta_archivo>
"""

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
WARN = "\033[33m[WARN]\033[0m"
SKIP = "\033[90m[SKIP]\033[0m"

JS_EXTENSIONS = {".js", ".ts", ".jsx", ".tsx"}

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
        print(f"  {FAIL} AST parse error: línea {line}, {msg}")
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
        print(f"  {WARN} Imports no resueltos (third-party?): {names}")


def check_ruff(filepath):
    """Run ruff check on the file."""
    try:
        result = subprocess.run(
            ["ruff", "check", str(filepath)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print(f"  {WARN} ruff no encontrado en PATH, saltando lint")
        return

    if result.returncode == 0:
        print(f"  {PASS} ruff: sin warnings")
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
        print(f"  {FAIL} Archivo vacío")
        return False
    print(f"  {PASS} No vacío ({size} bytes)")
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
                print(f"  {FAIL} Bracket sin abrir: '{ch}' en línea {line}")
                return False
            open_ch, open_line = stack.pop()
            if open_ch != expected_open:
                print(f"  {FAIL} Bracket mismatch: '{open_ch}' (línea {open_line}) cerrado con '{ch}' (línea {line})")
                return False

        i += 1

    if stack:
        open_ch, open_line = stack[-1]
        print(f"  {FAIL} Bracket sin cerrar: '{open_ch}' en línea {open_line}")
        return False

    print(f"  {PASS} Brackets balanceados")
    return True


def validate_js(filepath):
    """JS/TS validation pipeline. Returns exit code."""
    not_empty = check_not_empty(filepath)
    if not not_empty:
        return 1
    balanced = check_balanced_brackets(filepath)
    return 0 if balanced else 1


# ── Main ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3 or sys.argv[1] != "check-file":
        print("Uso: python3 .forja-tools/forja_validator.py check-file <ruta_archivo>")
        sys.exit(1)

    filepath = Path(sys.argv[2])
    if not filepath.exists():
        print(f"  {FAIL} Archivo no encontrado: {filepath}")
        sys.exit(1)

    ext = filepath.suffix.lower()
    print(f"Validando: {filepath}")

    if ext == ".py":
        sys.exit(validate_python(filepath))
    elif ext in JS_EXTENSIONS:
        sys.exit(validate_js(filepath))
    else:
        print(f"  {SKIP} Tipo {ext or 'sin extensión'} no requiere validación")
        sys.exit(0)


if __name__ == "__main__":
    main()
