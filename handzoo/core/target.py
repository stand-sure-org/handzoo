"""The typesetting target: what a page is built *into*, and by which binary.

Three things used to be hardcoded in three modules -- the engine in `compile_gate`, the
preamble in `normalize`, and whether non-ASCII is a defect in `ascii_gate` and R1. They are not
three decisions. A preamble settles all three at once, and settles them *measurably*:

    pdflatex + unicode-math  ->  ! Package unicode-math Error: Cannot be run with pdftex!

So the engine is **derived, never configured**. Asking an author to name a preamble and then
name the only engine that can run it is asking them to repeat themselves and to get it wrong.

A project that names no preamble gets `Target.generated()`, which is exactly what every corpus
in the author's hands was built with. The target exists to let a project say otherwise.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path

PREAMBLE_NAME = "preamble.tex"
PDFLATEX = "pdflatex"
LUALATEX = "lualatex"

_COMMENT = re.compile(r"(?<!\\)%.*")

_UNICODE_ENGINE = re.compile(
    r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{[^}]*\b(?:unicode-math|fontspec|luacode)\b")
"""Packages that **cannot** run on pdftex -- the detection is of an impossibility, not a taste.

Deliberately *not* `\\DeclareUnicodeCharacter`, which `ascii_gate.preamble_supports_unicode`
does match. The two ask different questions: that one asks whether the document has accounted
for the codepoints it contains (a `\\DeclareUnicodeCharacter` stub does), this one asks whether
the engine can typeset arbitrary Unicode (a stub does not, and our own generated stubs would
otherwise promote a pdflatex project to a Unicode one).
"""

_DOCUMENTCLASS = re.compile(r"(?<!\\)%.*|\\documentclass")


@dataclass(frozen=True, slots=True)
class Target:
    """How this project's LaTeX is built. A value, carried rather than re-derived."""

    base: str
    """The preamble, `\\documentclass` included, before generated declarations."""
    engine: str
    accepts_unicode: bool
    """True when the engine typesets Unicode natively, so non-ASCII is correct rather than
    broken -- which turns off R1 and exempts the page from the ASCII gate."""
    origin: str = "generated"
    """Where `base` came from: `"generated"`, or the path the author pointed at."""
    origin_hash: str = ""
    """SHA-256 of the adopted text. The project builds from its **copy**, so this is what lets
    a later run notice the original has moved on. A hint, never identity (DESIGN 11.1.3)."""
    added_document_class: bool = False
    """We supplied the `\\documentclass` line because the file had none. Visible on purpose:
    the class is a guess, and a guess the author can see is a guess they can correct."""

    @classmethod
    def generated(cls) -> Target:
        """Today's behaviour, unchanged: our own preamble under pdflatex, ASCII required."""
        from .normalize import PREAMBLE
        return cls(base=PREAMBLE, engine=PDFLATEX, accepts_unicode=False)

    @classmethod
    def from_text(cls, text: str, *, origin: str = "supplied",
                  document_class: str = "article") -> Target:
        live = _COMMENT.sub("", text)
        unicode_native = _UNICODE_ENGINE.search(live) is not None
        has_class = any(m.group(0).startswith("\\") for m in _DOCUMENTCLASS.finditer(text))
        base = text if has_class else f"\\documentclass{{{document_class}}}\n{text}"
        if not base.endswith("\n"):
            base += "\n"
        return cls(base=base,
                   engine=LUALATEX if unicode_native else PDFLATEX,
                   accepts_unicode=unicode_native,
                   origin=origin,
                   origin_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   added_document_class=not has_class)

    @classmethod
    def from_file(cls, path: Path, *, document_class: str = "article") -> Target:
        return cls.from_text(path.read_text(encoding="utf-8"), origin=str(path),
                             document_class=document_class)

    def preamble_for(self, texts: list[str]) -> str:
        """`base`, plus the declarations these texts need.

        An invented macro is still invented. The author's preamble is authoritative about what
        *it* defines and says nothing about what the recognizer made up, so R8's guard stays.
        """
        from .normalize import chapter_preamble
        return chapter_preamble(texts, target=self)

    # ------------------------------------------------------------------ project storage

    def save(self, out_dir: Path) -> Path:
        """Write the project's own copy. LaTeX, with provenance in comments.

        A `.tex` file rather than a config format because the thing an author most wants to do
        with a preamble we copied is read it, and then edit it. The comments are inert to TeX
        and are stripped before engine detection, so nothing here can change how it builds.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / PREAMBLE_NAME
        header = (f"% handzoo: copied from {self.origin}\n"
                  f"% handzoo: sha256 {self.origin_hash}\n"
                  f"% handzoo: engine {self.engine} (derived from this file, not configured)\n")
        if self.added_document_class:
            header += "% handzoo: the \\documentclass line below was added -- the original had none\n"
        path.write_text(header + self.base, encoding="utf-8")
        return path

    @classmethod
    def load(cls, out_dir: Path) -> Target:
        """The project's target. A project that never named a preamble gets the generated one,
        which is what every existing corpus was built with."""
        path = out_dir / PREAMBLE_NAME
        if not path.exists():
            return cls.generated()
        text = path.read_text(encoding="utf-8")
        origin, origin_hash, added_class = "supplied", "", False
        body: list[str] = []
        for line in text.splitlines(keepends=True):
            if line.startswith("% handzoo: copied from "):
                origin = line.split("copied from ", 1)[1].strip()
            elif line.startswith("% handzoo: sha256 "):
                origin_hash = line.split("sha256 ", 1)[1].strip()
            elif line.startswith("% handzoo: the \\documentclass line below was added"):
                added_class = True
            elif not line.startswith("% handzoo: "):
                body.append(line)
        target = cls.from_text("".join(body), origin=origin)
        # The hash is of the *origin* as adopted, not of our copy -- that is what makes
        # `drifted_from` answerable.
        # `added_document_class` cannot be re-derived -- the saved copy *has* the line we
        # added -- so it is read back from the header. A value object that quietly forgets how
        # it was made is the same failure as a gate that quietly reports a pass.
        return replace(target, origin_hash=origin_hash or target.origin_hash,
                       added_document_class=added_class)

    def drifted_from(self, path: Path) -> bool:
        """Has the file we copied from changed since? The copy is what builds."""
        if not path.exists() or not self.origin_hash:
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() != self.origin_hash
