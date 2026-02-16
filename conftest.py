"""Root conftest - ensure src/ takes priority over forja.py for package imports."""

import sys
from pathlib import Path

# The project root contains forja.py which shadows the forja package in src/.
# Fix sys.path ordering so src/forja/ (the package) is found before forja.py.
_src_dir = str(Path(__file__).resolve().parent / "src")
_project_root = str(Path(__file__).resolve().parent)

# Rebuild sys.path with src first
_new_path = [_src_dir]
for p in sys.path:
    if p not in (_project_root, _src_dir, ""):
        _new_path.append(p)
_new_path.append(_project_root)
sys.path[:] = _new_path

# Clear any cached forja module that points to forja.py
if "forja" in sys.modules:
    mod = sys.modules["forja"]
    if not hasattr(mod, "__path__"):
        del sys.modules["forja"]
        # Also clear submodules
        for key in list(sys.modules):
            if key.startswith("forja."):
                del sys.modules[key]
