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

from .declarations import declarations_for, find_undefined

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
)


# R6 — `\\` is only legal in horizontal mode. Measured failures: `\end{center} \\`
# (Number theory p75) and a bare `\\` after a blank line (Topology p2), both giving
# "There's no line here to end."
_VMODE_BREAK = re.compile(r"(\\end\{[^}]+\}[ \t]*)\\\\|^[ \t]*\\\\[ \t]*$", re.M)

# R7 — list environments nested inside a tabular cell blow up with "Extra }" unless the
# column is a p{} type (Team of Teams p15). Unwrap rather than drop: content survives.
_LIST_IN_TABULAR = re.compile(
    r"(\\begin\{tabular\}.*?)(\\begin\{(itemize|enumerate)\}.*?\\end\{\3\})(.*?\\end\{tabular\})",
    re.S,
)

# R9 — fabricated graphics. The recognizer is told never to invent tikz, and does it
# anyway (Naive Math p21 emitted \begin{tikzpicture}); it also invents \includegraphics
# references to files that do not exist (p5, p6: \includegraphics{pie1}). Both are the
# never-fabricate violation in the emitter rather than the recognizer. Replacing them
# with a diagram marker keeps the fact that something was there, which deleting would not.
_FAB_TIKZ = re.compile(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", re.S)
_FAB_GRAPHIC = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}")

_DIAGRAM = re.compile(r"\[\[DIAGRAM:\s*(.*?)\]\]", re.S)
_SUBSUP = re.compile(r"(?<![\\$])([A-Za-z0-9])([_^])([A-Za-z0-9])")
_FRAGILE = re.compile(r"[{}$&#_^~%\\]")


@dataclass
class Result:
    text: str
    rules: list[str] = field(default_factory=list)
    residual_non_ascii: list[str] = field(default_factory=list)
    undefined_macros: list[str] = field(default_factory=list)

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


def _fix_vertical_mode_breaks(text: str, rules: list[str]) -> str:
    """R6 — drop `\\\\` where LaTeX is in vertical mode and there is no line to end."""
    def repl(m: "re.Match[str]") -> str:
        rules.append("R6 vertical-mode line break removed")
        return m.group(1) or ""
    return _VMODE_BREAK.sub(repl, text)


def _unwrap_lists_in_tabular(text: str, rules: list[str]) -> str:
    """R7 — flatten itemize/enumerate nested in a tabular cell; keep every item."""
    def repl(m: "re.Match[str]") -> str:
        rules.append("R7 list unwrapped inside tabular")
        items = re.findall(r"\\item\s+(.*?)(?=\\item|\\end\{)", m.group(2), re.S)
        flat = "; ".join(" ".join(i.split()) for i in items)
        return f"{m.group(1)}{flat}% TODO: was a nested list{m.group(4)}"
    prev = None
    while prev != text:
        prev, text = text, _LIST_IN_TABULAR.sub(repl, text)
    return text


def _strip_fabricated_graphics(text: str, rules: list[str]) -> str:
    """R9 — a tikzpicture or an \\includegraphics the recognizer invented becomes a marker."""
    def tikz(_m):
        rules.append("R9 fabricated tikzpicture -> diagram marker")
        return "[[DIAGRAM: recognizer emitted tikz despite instruction; redraw or crop]]"

    def graphic(m):
        rules.append("R9 invented \\includegraphics -> diagram marker")
        return f"[[DIAGRAM: recognizer referenced a nonexistent file {m.group(1)}]]"

    text = _FAB_TIKZ.sub(tikz, text)
    return _FAB_GRAPHIC.sub(graphic, text)


def normalize(markup: str, standalone: bool = True) -> Result:
    rules: list[str] = []
    text = _strip_fabricated_graphics(markup, rules)
    text = _mask_diagrams(text, rules)
    text = _unwrap_lists_in_tabular(text, rules)
    text = _fix_vertical_mode_breaks(text, rules)

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
        residual = sorted({c for c in body if ord(c) > 127})
        undefined = find_undefined(body)
        decls = declarations_for(undefined, residual, source=body)
        if undefined:
            rules.append(f"R8 declared {len(undefined)} undefined macro(s): "
                         + ", ".join("\\" + u for u in undefined))
        text = PREAMBLE + decls + "\\begin{document}\n" + body + "\n\\end{document}\n"
    else:
        undefined = find_undefined(text)
        if undefined:
            rules.append("R8 undefined macros (fragment, not declared here): "
                         + ", ".join("\\" + u for u in undefined))

    residual = sorted({c for c in text if ord(c) > 127})
    return Result(text=text, rules=rules, residual_non_ascii=residual,
                  undefined_macros=undefined)
