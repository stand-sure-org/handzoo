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


def test_no_code_path_substitutes_a_lexicon_meaning_into_output() -> None:
    r""""Never auto-expand" as an enforced invariant, not a comment.

    A lexicon maps `Sps` to "Suppose". The moment any code writes the value where the key
    was, handzoo is putting words on the page that the author did not write — constraint #5b,
    and the more dangerous half of it: an expansion the author agrees with is one they stop
    checking for.

    So `meanings` may be read by a human and by a future gate, and by nothing that produces
    emitted text. This greps for the shape of the mistake rather than trusting review.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "handzoo"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # `lex.meanings[...]` or `.meanings.get(...)` anywhere outside lexicon.py itself
            if isinstance(node, ast.Attribute) and node.attr == "meanings":
                if path.name != "lexicon.py":
                    offenders.append(f"{path.name}: reads .meanings")
    assert not offenders, (
        "meanings must never be reachable from code that builds prompts or emits text: "
        f"{offenders}")


def test_the_prompt_fragment_is_the_only_lexicon_surface_the_recognizer_sees() -> None:
    """The recognizer takes tokens, never a `Lexicon`, so it has no path to the meanings."""
    from handzoo.core.recognize.ollama_vlm import OllamaRecognizer

    fields = OllamaRecognizer.__dataclass_fields__
    assert "lexicon_tokens" in fields
    assert "lexicon" not in fields, "holding the whole object would expose meanings"
