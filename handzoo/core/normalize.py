"""Normalizer — turn raw recognizer markup into LaTeX that compiles.

Every rule here exists because a measured recognizer run failed a gate. The rules are
LaTeX-aware rather than regex-based: `pylatexenc.latexwalker` tells us which spans are
already math mode, which is the distinction a regex cannot reliably make.

  R1  non-ASCII in output                     ch16 p3 emitted a literal `§`; p16 `Σ`; p18 `ü`
  R2  math-only macro used in text mode       naive p2 `\\oplus`; ch16 p1 `\\uparrow` x4;
                                              ch16 p4/p25 `\\mathbb{Z}` (argument-taking)
  R3  [[DIAGRAM: ...]] emitted as body text    ch16 p3 marker contained `\\to`
  R4  fragment vs standalone inconsistency     ch16 p3 had no preamble, p1 did
  R5  bare sub/superscript in text mode        ch16 p6 `1_f, 1_g`

`\\ensuremath{}` is preferred over `$...$` for R1/R2/R5: it is a no-op inside math mode, so
the transform is idempotent and safe to apply without tracking mode perfectly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pylatexenc import latexwalker
from pylatexenc.latexencode import unicode_to_latex

# Macros that are only legal inside math mode. Seeded from measured failures, not speculation.
MATH_ONLY = frozenset(
    """
    oplus otimes odot uparrow downarrow leftarrow rightarrow longrightarrow to mapsto
    leq geq neq approx equiv in notin subset supset subseteq supseteq cup cap setminus
    forall exists nexists cdots ldots vdots times div circ pm mp cdot ast
    alpha beta gamma delta epsilon varepsilon zeta eta theta iota kappa lambda mu nu xi
    pi rho sigma tau upsilon phi varphi chi psi omega
    Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
    mathbb mathcal mathfrak mathrm mathbf mathsf sqrt frac sum prod int infty partial
    varnothing emptyset langle rangle xrightarrow xleftarrow
    Rightarrow Leftarrow Leftrightarrow leftrightarrow Longrightarrow implies iff
    hookrightarrow twoheadrightarrow rightsquigarrow simeq cong sim propto
    text quad qquad colon bmod
    """.split()
)

# Environments the recognizer emits unprompted. Good recognition the emitter must support.
THEOREM_ENVS = ("definition", "proposition", "theorem", "lemma", "corollary", "example", "remark")

PREAMBLE = (
    "\\documentclass{article}\n"
    "\\usepackage{amsmath}\n"
    "\\usepackage{amssymb}\n"
    "\\usepackage{amsthm}\n"
    "\\usepackage[T1]{fontenc}\n"
    "\\usepackage[margin=1in]{geometry}\n"
    + "".join(f"\\newtheorem{{{e}}}{{{e.capitalize()}}}\n" for e in THEOREM_ENVS)
    + "\\begin{document}\n"
)

_DIAGRAM = re.compile(r"\[\[DIAGRAM:\s*(.*?)\]\]", re.S)
_SUBSUP = re.compile(r"(?<![\\$])([A-Za-z0-9])([_^])([A-Za-z0-9])")
_FRAGILE = re.compile(r"[{}$&#_^~%\\]")


@dataclass
class Result:
    text: str
    rules: list[str] = field(default_factory=list)
    residual_non_ascii: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.residual_non_ascii


def _mask_diagrams(text: str, rules: list[str]) -> str:
    """R3 — a diagram marker is a human TODO, not typesettable body text.

    Emitting it raw breaks the build the moment a description contains LaTeX.
    """

    def repl(m: "re.Match[str]") -> str:
        rules.append("R3 diagram marker escaped")
        # Sanitize fully here: the result lands inside a macro argument, which the
        # node walker emits verbatim, so R1 never gets another chance at it.
        desc = _FRAGILE.sub("", m.group(1)).strip()
        desc = unicode_to_latex(desc, non_ascii_only=True)
        desc = _FRAGILE.sub("", desc)
        desc = "".join(c for c in desc if ord(c) < 128)
        return f"\\texttt{{[diagram: {desc}]}}% TODO: author diagram\n"

    return _DIAGRAM.sub(repl, text)


def _fix_text_span(chunk: str, rules: list[str]) -> str:
    """Apply R1 and R5 to a span already known to be outside math mode."""
    if any(ord(c) > 127 for c in chunk):
        chunk = unicode_to_latex(chunk, non_ascii_only=True)
        rules.append("R1 unicode mapped")
    if _SUBSUP.search(chunk):
        chunk = _SUBSUP.sub(lambda m: f"\\ensuremath{{{m.group(1)}{m.group(2)}{m.group(3)}}}", chunk)
        rules.append("R5 text-mode sub/superscript wrapped")
    return chunk


def _walk(nodes, rules: list[str]) -> str:
    """Rebuild LaTeX, fixing only what sits outside math mode (R2)."""
    out: list[str] = []
    for node in nodes:
        if node is None:
            continue
        cls = type(node).__name__

        if "Math" in cls:
            out.append(node.latex_verbatim())  # already math: leave entirely alone
        elif "Chars" in cls:
            out.append(_fix_text_span(node.chars, rules))
        elif "Macro" in cls and node.macroname in MATH_ONLY:
            rules.append(f"R2 \\{node.macroname} wrapped for text mode")
            out.append(f"\\ensuremath{{{node.latex_verbatim()}}}")
        elif "Environment" in cls:
            body = _walk(node.nodelist, rules)
            name = node.environmentname
            args = ""
            if node.nodeargd and node.nodeargd.argnlist:
                args = "".join(a.latex_verbatim() for a in node.nodeargd.argnlist if a)
            out.append(f"\\begin{{{name}}}{args}{body}\\end{{{name}}}")
        elif "Group" in cls:
            out.append("{" + _walk(node.nodelist, rules) + "}")
        else:
            out.append(node.latex_verbatim())
    return "".join(out)


def normalize(markup: str, standalone: bool = True) -> Result:
    rules: list[str] = []
    text = _mask_diagrams(markup, rules)

    try:
        nodes, _, _ = latexwalker.LatexWalker(text, tolerant_parsing=True).get_latex_nodes()
        text = _walk(nodes, rules)
    except Exception as exc:  # a parse failure must be loud, not silently skipped
        rules.append(f"PARSE FAILED ({type(exc).__name__}) - rules not applied")

    if standalone:
        # The emitter owns the preamble. A model-emitted one is unreliable — it declared
        # \begin{proposition} and \begin{proof} while loading neither amsthm nor any
        # \newtheorem (ch16 p7, p10). Always discard it and substitute the known-good one.
        if "\\begin{document}" in text:
            body = text.split("\\begin{document}", 1)[1]
            rules.append("R4 model preamble discarded")
        else:
            body = text
            rules.append("R4 fragment -> standalone")
        body = body.split("\\end{document}")[0]
        text = PREAMBLE + body + "\n\\end{document}\n"

    residual = sorted({c for c in text if ord(c) > 127})
    return Result(text=text, rules=rules, residual_non_ascii=residual)
