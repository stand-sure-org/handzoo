r"""The author's private notation, and the one thing it must never be used for.

**Why this exists.** `Sps` — the author's "Suppose" — came back as `\Rightarrow` (DESIGN
§11.0.1a). The model met an unfamiliar token and resolved it into a familiar one from context,
which is over-correction (§5.5) with a concrete instance. Naming the token in the prompt gives
the model a correct hypothesis so it does not have to guess.

**The trap, and why the type is shaped like this.** The obvious file is `Sps -> Suppose`, and
the obvious mistake is to feed that file to the recognizer. Handing the model the expansion is
handing it permission to write "Suppose" where the page says "Sps" — constraint #5b, *never
silently add*, built directly into the seed.

So the two columns are separated at the type level rather than by discipline:

- `tokens` — what the model is told **exists**. Prompt-visible.
- `meanings` — what a **human** reads. Never prompt-visible, and `prompt_fragment()` has no
  access to it by construction.

A test in `test_architecture.py` asserts no meaning string can reach emitted text.

**What this covers, and what it does not.** The author's seed sheet holds five different
mechanisms and this file models exactly one of them:

| mechanism | example | here? |
|---|---|---|
| word abbreviation | `Sps`, `Defn`, `BWOI`, `WLOG` | **yes** |
| positional rule | `g:` = "given", only at start of a line in proofs | no — a token list cannot say *where* |
| acknowledged ambiguity | `Prop` = Proposition **or** Property; `§` = section **or** integral | no — the model must choose, and this cannot resolve it |
| mark, not token | the contradiction lightning bolt, `→←`, the ampersand glyph | no — these belong to the inventory and coverage path |
| rendering convention | "generally draw the letter 2x" for `\mathbb{A}` | no — about strokes, not strings |

Listing the four it does not cover is the point. Without that, the file accretes entries
nothing reads and everyone assumes something is checking them.

**Not a gate.** Deliberately. Measured against the six labelled pages, a lexicon gate would
have caught nothing: `Sps` → `\Rightarrow` is detectable only as an *absence*, expansions are
unverifiable because the author does sometimes write the word out, and the `IR` misreads sit
inside a `[TODO diagram: ...]` block already flagged for the author. The reference gate earned
its place on 1 true positive and 0 false positives over 44 pages; this has neither yet.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_NAME = "lexicon.toml"


@dataclass(frozen=True)
class Lexicon:
    """An author's notation. Two halves, one of which the recognizer must never see."""

    tokens: tuple[str, ...] = ()
    """Strings that appear on the page as themselves. **Prompt-visible.**"""

    meanings: Mapping[str, str] = field(default_factory=dict)
    """What each token means, for a human reader and for a gate that does not exist yet.

    **Never prompt-visible.** `prompt_fragment()` cannot reach this, and that is enforced by
    the function taking `tokens` rather than `self`.
    """

    def __bool__(self) -> bool:
        return bool(self.tokens)


def prompt_fragment(tokens: tuple[str, ...]) -> str:
    """The instruction the recognizer gets: these strings exist, copy them exactly.

    Takes `tokens` and not a `Lexicon` **on purpose**. A function handed the whole object
    could reach `meanings` in a later edit; a function handed a tuple of strings cannot. The
    guarantee is structural rather than remembered.

    Returns an empty string for an empty lexicon, so a caller concatenating it is unaffected.
    """
    if not tokens:
        return ""
    listed = ", ".join(sorted(tokens))
    return (
        "\nThe author uses these written shorthands. They appear on the page exactly as "
        f"written: {listed}.\n"
        "Copy any of them verbatim. Do NOT expand one into the words it stands for, and do "
        "NOT replace one with a mathematical symbol that means something similar — writing "
        "the expansion or a symbol puts text on the page that the author did not write.\n"
    )


def load(path: Path) -> Lexicon:
    """Read a lexicon file. A missing file is an empty lexicon, not an error.

    The lexicon is optional by design: a user who never writes one gets today's behaviour.
    """
    if not path.exists():
        return Lexicon()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw = data.get("tokens", {})
    tokens = tuple(str(t) for t in raw.get("literal", []))
    meanings = {str(k): str(v) for k, v in (data.get("meanings") or {}).items()}
    unknown = sorted(set(meanings) - set(tokens))
    if unknown:
        # A meaning with no token is a line the prompt will never mention, and the author
        # would have no way to discover that from the file.
        raise ValueError(
            f"{path.name}: {unknown} have meanings but are not in tokens.literal, so nothing "
            "will look for them. Add them to tokens.literal or remove the meanings.")
    return Lexicon(tokens=tokens, meanings=meanings)


def discover(out_dir: Path | None = None) -> Lexicon:
    """Find a lexicon beside the run, then in the user's config. Empty if neither exists."""
    for candidate in ((out_dir / DEFAULT_NAME) if out_dir else None,
                      Path.home() / ".config" / "handzoo" / DEFAULT_NAME):
        if candidate and candidate.exists():
            return load(candidate)
    return Lexicon()
