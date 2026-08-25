r"""The lexicon, and the expansion it must never license.

The whole point of the type's shape is that `meanings` cannot reach the recognizer. These
tests assert that structurally, not by reading the code and trusting it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from handzoo.core.lexicon import Lexicon, discover, load, prompt_fragment

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "lexicon.example.toml"


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "lexicon.toml"
    p.write_text(body, encoding="utf-8")
    return p


# ------------------------------------------------------------------ the invariant


def test_a_meaning_never_reaches_the_prompt() -> None:
    r"""The one rule. `Sps -> Suppose` in the prompt is permission to write "Suppose".

    That is constraint #5b — never silently add — and handing the model the expansion builds
    the violation into the seed rather than into a later mistake.
    """
    lex = Lexicon(tokens=("Sps",), meanings={"Sps": "Suppose"})
    fragment = prompt_fragment(lex.tokens)

    assert "Sps" in fragment
    assert "Suppose" not in fragment


def test_the_prompt_builder_cannot_reach_meanings_at_all() -> None:
    """Structural, not remembered: it takes a tuple of strings, so there is nothing to reach.

    A function handed the whole `Lexicon` would be one careless edit away from reading the
    other half. This asserts the signature that makes the mistake impossible.
    """
    import inspect

    params = list(inspect.signature(prompt_fragment).parameters)
    assert params == ["tokens"], "prompt_fragment must not accept the Lexicon object"
    with pytest.raises(TypeError):
        prompt_fragment(Lexicon(tokens=("Sps",), meanings={"Sps": "Suppose"}))  # type: ignore[arg-type]


def test_the_prompt_forbids_both_failure_modes_by_name() -> None:
    r"""`Sps` failed by becoming `\Rightarrow`, not by becoming "Suppose".

    Forbidding only expansion would leave the measured failure untouched, so the instruction
    names symbol substitution too.
    """
    fragment = prompt_fragment(("Sps",)).lower()
    assert "expand" in fragment
    assert "symbol" in fragment


# ------------------------------------------------------------------ loading


def test_an_absent_lexicon_is_empty_not_an_error(tmp_path: Path) -> None:
    """Optional by design: a user who never writes one gets today's behaviour."""
    lex = load(tmp_path / "nope.toml")
    assert not lex
    assert prompt_fragment(lex.tokens) == ""


def test_a_meaning_with_no_token_is_refused(tmp_path: Path) -> None:
    """It would be a line the prompt never mentions, and the author could not tell.

    Silence about an entry that does nothing is the same failure as a gate that did not run
    reporting clean (DESIGN §5.7).
    """
    path = _write(tmp_path, '[tokens]\nliteral = ["Sps"]\n\n[meanings]\nSps = "Suppose"\n'
                            'Defn = "Definition"\n')
    with pytest.raises(ValueError, match="Defn"):
        load(path)


def test_the_shipped_example_loads_and_carries_no_author_notation() -> None:
    """`fixtures/` is gitignored because the manuscript is unpublished IP.

    An author's abbreviation set plus their manuscript is more identifying than either alone,
    so the shipped example is ordinary mathematical shorthand.
    """
    lex = load(EXAMPLE)
    assert lex.tokens
    assert "Sps" not in lex.tokens, "the author's own notation must not ship in the repo"
    assert "Defn" not in lex.tokens


def test_discovery_prefers_the_lexicon_beside_the_run(tmp_path: Path) -> None:
    _write(tmp_path, '[tokens]\nliteral = ["BWOC"]\n')
    assert discover(tmp_path).tokens == ("BWOC",)
