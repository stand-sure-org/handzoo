"""The one architectural invariant worth enforcing: `core/` never imports `adapters/`.

The engine has to stay ignorant of who is calling it. M0 ships CLI adapters; MCP and an
HTTP/UI adapter come later against the same engine. The moment `core/` reaches back into an
adapter, that stops being true and the later adapters need a rewrite.

Parses the AST rather than importing, so the check holds for modules with heavy or missing
runtime dependencies.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "handzoo" / "core"
FORBIDDEN = "handzoo.adapters"


def _imported_modules(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            # level > 0 is a relative import; `from ..adapters import x` inside core
            # resolves outside core and must be caught too.
            found.append(("." * node.level) + node.module)
    return found


def test_core_modules_exist() -> None:
    """Guard against the test silently passing because it found nothing to check."""
    modules = list(CORE.rglob("*.py"))
    assert modules, f"no modules found under {CORE} — the check would be vacuous"


def test_core_does_not_import_adapters() -> None:
    offenders: list[str] = []
    for path in CORE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _imported_modules(tree):
            if FORBIDDEN in name or "adapters" in name.split("."):
                offenders.append(f"{path.relative_to(CORE.parent.parent)} imports {name}")
    assert not offenders, "core/ must not depend on adapters/:\n  " + "\n  ".join(offenders)
