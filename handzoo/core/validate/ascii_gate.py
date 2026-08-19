"""Gate: the LaTeX output must be pure ASCII.

This exists because the incumbent tools emit bare Unicode with nothing defining it — one of
the two failures the whole project is aimed at. Output that renders on the author's machine
and breaks on anyone else's is exactly the silent corruption we refuse to ship.

**Do not implement this with `grep -P '[^\\x00-\\x7F]'`.** The original brief specified

    ! grep -P '[^\\x00-\\x7F]' out.tex

and that gate is broken in the worst possible direction. `-P` is a GNU/ugrep extension; on a
stock macOS `/usr/bin/grep` it errors out, and the leading `!` inverts the *error* into a
pass. It reports clean without ever having looked. Measured on this machine, where `grep` is
ugrep and therefore happens to work — which is precisely what makes it dangerous in CI.
"""

from __future__ import annotations

import re
import unicodedata

from .base import Failure, GateResult

GATE = "ascii"

_PREAMBLE_SUPPORTS_UNICODE = re.compile(
    r"\\usepackage\{(?:fontspec|unicode-math)\}"
    r"|\\DeclareUnicodeCharacter"
    r"|\\usepackage\[utf8\]\{inputenc\}",
)


def preamble_supports_unicode(latex: str) -> bool:
    """Does the document declare enough to justify the codepoints it uses?

    The rule was never "no Unicode ever" — it is "no *undefined* Unicode". A standalone
    document that loads `fontspec`/`unicode-math`, or declares characters explicitly, has
    earned the right to contain them.
    """
    return _PREAMBLE_SUPPORTS_UNICODE.search(latex) is not None


def non_ascii_chars(text: str) -> list[str]:
    """The distinct non-ASCII characters in `text`, sorted.

    The Normalizer needs the character *set* (to generate `\\DeclareUnicodeCharacter`
    stubs) while the gate needs positions. Both must agree on what counts, so the
    predicate lives here once rather than in each caller.
    """
    return sorted({c for c in text if ord(c) > 127})


def check(latex: str, *, allow_declared: bool = True) -> GateResult:
    """Fail on any non-ASCII byte the document has not accounted for.

    Args:
        latex: The emitted document.
        allow_declared: When True, a preamble that supports Unicode exempts the document.
            Set False to demand pure ASCII regardless — useful for `--fragment` output, which
            carries no preamble and will be pasted somewhere whose preamble we cannot see.
    """
    if allow_declared and preamble_supports_unicode(latex):
        return GateResult(GATE)

    failures: list[Failure] = []
    for lineno, line in enumerate(latex.splitlines(), start=1):
        for col, ch in enumerate(line, start=1):
            if ord(ch) < 128:
                continue
            try:
                name = unicodedata.name(ch)
            except ValueError:
                name = "unnamed"
            failures.append(
                Failure(
                    detail=f"non-ASCII {ch!r} (U+{ord(ch):04X}, {name})",
                    line=lineno,
                    column=col,
                    excerpt=line.strip()[:80],
                )
            )
    return GateResult(GATE, tuple(failures))
