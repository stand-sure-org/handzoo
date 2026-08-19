"""Declaration generation and undefined-macro defence.

Two problems, one answer: **never emit a control sequence the preamble does not define.**

The recognizer invents macros. Measured: `\\circled{2}` for a hand-drawn circled page number
(Topology p4) — nothing in LaTeX defines it, so the document fails to build. This is the
syntactic sibling of fabricating `tikz`: confident output the system cannot justify.

Rather than strip invented macros (silent loss, a D6 violation) or crash, generate a
declaration for every unknown and mark it for the author:

  operator-shaped  ->  \\DeclareMathOperator   (\\len, \\ord, \\lcm, \\Hom - the author's own)
  known confusion  ->  a repair mapping        (\\circled -> \\textcircled)
  anything else    ->  \\providecommand stub + % TODO
  unmapped glyph   ->  \\DeclareUnicodeCharacter + % TODO

`\\DeclareMathOperator` matters independently: operator names like `len` (the author's monoid
homomorphism, named in the brief) render as italic juxtaposed letters without it.
"""

from __future__ import annotations

import re

# Control sequences provided by the standard preamble: base LaTeX + amsmath/amssymb/amsthm.
# Not exhaustive by design — anything absent gets declared rather than assumed.
KNOWN = frozenset(
    ["begin", "end", "item", "documentclass", "usepackage", "newtheorem", "section", "subsection", "subsubsection", "paragraph", "noindent", "indent", "textbf", "textit", "texttt", "textrm", "textsf", "emph", "underline", "text", "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathbb", "mathcal", "mathfrak", "frac", "dfrac", "tfrac", "sqrt", "sum", "prod", "int", "oint", "lim", "sup", "inf", "max", "min", "left", "right", "big", "Big", "bigg", "Bigg", "langle", "rangle", "lfloor", "rfloor", "lceil", "rceil", "quad", "qquad", "hspace", "vspace", "hfill", "vfill", "newline", "linebreak", "pagebreak", "clearpage", "newpage", "label", "ref", "eqref", "cite", "footnote", "caption", "centering", "raggedright", "raggedleft", "hline", "cline", "multicolumn", "multirow", "rule", "textwidth", "linewidth", "textheight", "ensuremath", "providecommand", "newcommand", "renewcommand", "DeclareMathOperator", "DeclareUnicodeCharacter", "textcircled", "circ", "cdot", "cdots", "ldots", "vdots", "ddots", "dots", "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "varpi", "rho", "varrho", "sigma", "varsigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega", "in", "notin", "ni", "subset", "supset", "subseteq", "supseteq", "subsetneq", "supsetneq", "cup", "cap", "bigcup", "bigcap", "setminus", "emptyset", "varnothing", "forall", "exists", "nexists", "neg", "lnot", "land", "lor", "leq", "geq", "neq", "equiv", "approx", "sim", "simeq", "cong", "propto", "mid", "nmid", "parallel", "perp", "to", "gets", "mapsto", "rightarrow", "leftarrow", "leftrightarrow", "longrightarrow", "longleftarrow", "Rightarrow", "Leftarrow", "Leftrightarrow", "implies", "impliedby", "iff", "uparrow", "downarrow", "xrightarrow", "xleftarrow", "hookrightarrow", "twoheadrightarrow", "rightsquigarrow", "times", "div", "pm", "mp", "ast", "star", "dagger", "oplus", "otimes", "odot", "infty", "partial", "nabla", "overline", "underline", "overbrace", "underbrace", "widehat", "widetilde", "hat", "tilde", "bar", "vec", "dot", "ddot", "pmod", "bmod", "mod", "gcd", "deg", "det", "dim", "ker", "exp", "log", "ln", "sin", "cos", "tan", "arg", "textquotedblleft", "textquotedblright", "textquoteleft", "textquoteright", "ldots", "S", "P", "dag", "ddag", "copyright", "pounds", "textemdash", "textendash", "textbackslash", "textasciitilde", "not", "top", "bot", "colon", "hrule", "vrule", "because", "therefore", "models", "vdash", "dashv", "lnot", "lneq", "gneq", "ll", "gg", "prec", "succ", "preceq", "succeq", "asymp", "doteq", "triangleq", "binom", "choose", "over", "atop", "overset", "underset", "stackrel", "substack", "mathop", "mathbin", "mathrel", "mathpunct", "mathopen", "mathclose", "limits", "nolimits", "displaystyle", "textstyle", "scriptstyle", "scriptscriptstyle", "smallskip", "medskip", "bigskip", "par", "relax", "empty", "null"]
)

# KNOWN exists only to keep the generated block small and readable. It is NOT a
# correctness mechanism: every emitted declaration is \ifdefined-guarded, so a name
# wrongly absent from this list costs a redundant no-op guard, never a clobbered command.

# Macros the recognizer has been measured to invent, and what it plainly meant.
REPAIRS = {
    "circled": r"\providecommand{\circled}[1]{\textcircled{\small #1}}",
    "boxed": r"\providecommand{\boxed}[1]{\fbox{$#1$}}",
    "underarrow": r"\providecommand{\underarrow}[1]{\underset{\downarrow}{#1}}",
}

# Operator-shaped: a short all-lowercase word. `len`, `ord`, `lcm`, `im`, `coker`.
_OPERATOR_SHAPED = re.compile(r"^[a-z]{2,6}$")
_MACRO = re.compile(r"\\([a-zA-Z]+)")


def takes_argument(latex: str, name: str) -> bool:
    """Is this macro used as `\\name{...}` anywhere in the source?

    Name shape is not a reliable signal. An earlier version keyed off
    "short all-lowercase word" and so declared `\\ding` as a zero-argument
    `\\DeclareMathOperator`; `\\ding{172}` then dumped `{172}` into text mode and the
    document failed with "Missing $ inserted" (Naive Math p1, p2, p9). Usage is the
    evidence; the name is a guess.
    """
    return re.search(r"\\" + re.escape(name) + r"\s*[\[{]", latex) is not None


def find_undefined(latex: str) -> list[str]:
    """Every control sequence used but not provided by the standard preamble.

    This is the cheap deterministic check that runs *before* the compile gate. It gives a
    precise list where pdfLaTeX gives one "Undefined control sequence" and stops.

    Equivalent manual sweep:
        rg -o '\\\\[a-zA-Z]+' out.tex | sort -u
    """
    used = {m.group(1) for m in _MACRO.finditer(latex)}
    defined = {m.group(1) for m in re.finditer(r"\\(?:providecommand|newcommand|"
                                               r"DeclareMathOperator\*?)\{\\([a-zA-Z]+)\}", latex)}
    return sorted(used - KNOWN - defined)


def _guard(name: str, declaration: str, note: str) -> str:
    """Wrap a declaration so it only fires when the command is genuinely undefined.

    `\\ifdefined` is an e-TeX primitive supported by pdfTeX, XeTeX and LuaTeX. It is
    side-effect free: it does not expand or define the command it tests.

    This is what makes the whole scheme safe. A static Python list of "known" commands
    cannot be correct — it is a guess about base LaTeX *plus whatever packages load*.
    Guarding moves the decision to compile time, where the answer is authoritative.

    Measured why this matters: an earlier version emitted a bare `\\DeclareMathOperator`
    for anything its list did not recognise. `\\DeclareMathOperator` *overrides*, so
    `\\S \\not \\top \\bot \\colon \\hrule \\because \\textemdash` were all clobbered and
    ch16 p1/p20/p22 broke. Guarded, those become no-ops.

    Note: do NOT use the legacy `\\@ifundefined` for this. It defines the tested command as
    `\\relax` as a side effect, so testing a name brings it into existence.
    """
    return f"\\ifdefined\\{name}\\else{declaration}\\fi  % {note}"


def declarations_for(undefined: list[str], unmapped_chars: list[str] | None = None,
                     source: str = "") -> str:
    """Build the declaration block that makes an unruly document compile honestly.

    `source` is the document body, used to decide whether each macro takes an argument.
    Without it we fall back to the name-shape guess, which is known to be wrong.
    """
    lines: list[str] = []
    for name in undefined:
        if name in REPAIRS:
            lines.append(_guard(name, REPAIRS[name],
                                f"auto-repaired: recognizer invented \\{name}"))
        elif source and takes_argument(source, name):
            # Used as \name{...}: an operator declaration would drop the argument into
            # the surrounding mode and break the build.
            lines.append(_guard(name, f"\\providecommand{{\\{name}}}[1]{{#1}}",
                                f"TODO: recognizer invented \\{name}; define or correct"))
        elif _OPERATOR_SHAPED.match(name):
            lines.append(_guard(name, f"\\DeclareMathOperator{{\\{name}}}{{{name}}}",
                                "TODO: confirm operator name"))
        else:
            lines.append(_guard(name, f"\\providecommand{{\\{name}}}{{}}",
                                f"TODO: recognizer invented \\{name}; define or correct"))

    for ch in unmapped_chars or []:
        # \DeclareUnicodeCharacter has no \ifdefined equivalent; redeclaring is harmless.
        lines.append(f"\\DeclareUnicodeCharacter{{{ord(ch):04X}}}{{\\ensuremath{{\\bullet}}}}"
                     f"  % TODO: {ch!r} has no mapping; choose a representation")

    if not lines:
        return ""
    return ("% --- generated declarations: every one is a mark the recognizer could not\n"
            "% --- justify. Each TODO is a decision the author still owes.\n"
            + "\n".join(lines) + "\n")
