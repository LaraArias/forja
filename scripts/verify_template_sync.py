#!/usr/bin/env python3
"""Verify critical functions stay in sync between CLI utils and template utils.

Compares:  src/forja/utils.py  vs  src/forja/templates/forja_utils.py

Uses AST parsing with normalization (strips type annotations, docstrings,
and decorators) so that intentional style differences (type hints in CLI,
plain signatures in template) don't cause false positives.

Exit 0 = all synced.  Exit 1 = drift detected.
"""

import ast
import sys
from pathlib import Path

CLI_UTILS = Path("src/forja/utils.py")
TPL_UTILS = Path("src/forja/templates/forja_utils.py")

CRITICAL_FUNCTIONS = ["parse_json", "load_dotenv", "read_feature_status"]
CRITICAL_CLASSES = ["Feature"]


# ── AST normalizer ──────────────────────────────────────────────────


class _Normalize(ast.NodeTransformer):
    """Strip annotations, docstrings, and decorators so logic-only comparison works."""

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        node.returns = None
        node.decorator_list = []
        # Strip arg annotations
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            arg.annotation = None
        if node.args.vararg:
            node.args.vararg.annotation = None
        if node.args.kwarg:
            node.args.kwarg.annotation = None
        # Strip docstring (first Expr(Constant(str)))
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body = node.body[1:]
        self.generic_visit(node)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        node.decorator_list = []
        # Strip docstring
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body = node.body[1:]
        self.generic_visit(node)
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST | None:
        """Convert annotated assignments to plain assignments."""
        if node.value is not None:
            assign = ast.Assign(targets=[node.target], value=node.value)
            return ast.copy_location(assign, node)
        # Annotation-only (no default) — drop it
        return None


def _normalize(node: ast.AST) -> str:
    """Return a normalized ast.dump() string with no line numbers."""
    normalized = _Normalize().visit(node)
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, annotate_fields=True, include_attributes=False)


# ── Extraction ───────────────────────────────────────────────────────


def _extract_node(tree: ast.Module, name: str, kind: str) -> ast.AST | None:
    """Find a top-level function or class by name."""
    target_type = ast.ClassDef if kind == "class" else ast.FunctionDef
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, target_type) and node.name == name:
            return node
    return None


# ── Main ─────────────────────────────────────────────────────────────


def main() -> int:
    if not CLI_UTILS.exists():
        print(f"ERROR: {CLI_UTILS} not found")
        return 1
    if not TPL_UTILS.exists():
        print(f"ERROR: {TPL_UTILS} not found")
        return 1

    cli_tree = ast.parse(CLI_UTILS.read_text(encoding="utf-8"))
    tpl_tree = ast.parse(TPL_UTILS.read_text(encoding="utf-8"))

    drifted: list[str] = []
    ok: list[str] = []

    for name in CRITICAL_FUNCTIONS:
        cli_node = _extract_node(cli_tree, name, "function")
        tpl_node = _extract_node(tpl_tree, name, "function")

        if cli_node is None:
            drifted.append(f"  MISSING in CLI: {name}()")
            continue
        if tpl_node is None:
            drifted.append(f"  MISSING in template: {name}()")
            continue

        cli_dump = _normalize(cli_node)
        tpl_dump = _normalize(tpl_node)

        if cli_dump != tpl_dump:
            drifted.append(f"  DRIFT: {name}() — logic differs between CLI and template")
        else:
            ok.append(name + "()")

    for name in CRITICAL_CLASSES:
        cli_node = _extract_node(cli_tree, name, "class")
        tpl_node = _extract_node(tpl_tree, name, "class")

        if cli_node is None:
            drifted.append(f"  MISSING in CLI: class {name}")
            continue
        if tpl_node is None:
            drifted.append(f"  MISSING in template: class {name}")
            continue

        cli_dump = _normalize(cli_node)
        tpl_dump = _normalize(tpl_node)

        if cli_dump != tpl_dump:
            drifted.append(f"  DRIFT: class {name} — logic differs between CLI and template")
        else:
            ok.append(f"class {name}")

    # Report
    for item in ok:
        print(f"  OK  {item}")

    if drifted:
        print()
        for msg in drifted:
            print(f"  DRIFT WARNING  {msg}")
        print(f"\n  {len(drifted)} drift(s) detected. Sync the files.")
        return 1

    print(f"\n  All {len(ok)} critical symbols in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
