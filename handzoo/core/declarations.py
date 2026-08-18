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
    """
    begin end item documentclass usepackage newtheorem section subsection subsubsection
    paragraph noindent indent textbf textit texttt textrm textsf emph underline
    text mathrm mathbf mathit mathsf mathtt mathbb mathcal mathfrak
    frac dfrac tfrac sqrt sum prod int oint lim sup inf max min
    left right big Big bigg Bigg langle rangle lfloor rfloor lceil rceil
    quad qquad hspace vspace hfill vfill newline linebreak pagebreak clearpage newpage
    label ref eqref cite footnote caption centering raggedright raggedleft
    hline cline multicolumn multirow rule textwidth linewidth textheight
    ensuremath providecommand newcommand renewcommand DeclareMathOperator
    DeclareUnicodeCharacter textcircled circ cdot cdots ldots vdots ddots dots
    alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda
    mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega
    Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
    in notin ni subset supset subseteq supseteq subsetneq supsetneq cup cap bigcup bigcap
    setminus emptyset varnothing forall exists nexists neg lnot land lor
    leq geq neq equiv approx sim simeq cong propto mid nmid parallel perp
    to gets mapsto rightarrow leftarrow leftrightarrow longrightarrow longleftarrow
    Rightarrow Leftarrow Leftrightarrow implies impliedby iff uparrow downarrow
    xrightarrow xleftarrow hookrightarrow twoheadrightarrow rightsquigarrow
    times div pm mp ast star dagger oplus otimes odot infty partial nabla
    overline underline overbrace underbrace widehat widetilde hat tilde bar vec dot ddot
    pmod bmod mod gcd deg det dim ker exp log ln sin cos tan arg
    textquotedblleft textquotedblright textquoteleft textquoteright ldots
    S P dag ddag copyright pounds textemdash textendash textbackslash textasciitilde
    not top bot colon hrule vrule because therefore models vdash dashv
    lnot lneq gneq ll gg prec succ preceq succeq asymp doteq triangleq
    binom choose over atop overset underset stackrel substack
    mathop mathbin mathrel mathpunct mathopen mathclose limits nolimits
    displaystyle textstyle scriptstyle scriptscriptstyle
    smallskip medskip bigskip par relax empty null
    """.split()
)

# Redefining a real command is worse than leaving it alone. Anything whose name could
# plausibly be a builtin is never turned into an operator; it is reported instead.
# (Measured: \S \not \top \bot \colon \hrule \because \textemdash were all flagged as
# "undefined" by a thinner list, and \DeclareMathOperator then broke ch16 p1, p20, p22.)
NEVER_DECLARE = frozenset(KNOWN)

# Macros the recognizer has been measured to invent, and what it plainly meant.
REPAIRS = {
    "circled": r"\providecommand{\circled}[1]{\textcircled{\small #1}}",
    "boxed": r"\providecommand{\boxed}[1]{\fbox{$#1$}}",
    "underarrow": r"\providecommand{\underarrow}[1]{\underset{\downarrow}{#1}}",
}

# Operator-shaped: a short all-lowercase word. `len`, `ord`, `lcm`, `im`, `coker`.
_OPERATOR_SHAPED = re.compile(r"^[a-z]{2,6}$")
_MACRO = re.compile(r"\\([a-zA-Z]+)")


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


def declarations_for(undefined: list[str], unmapped_chars: list[str] | None = None) -> str:
    """Build the declaration block that makes an unruly document compile honestly."""
    lines: list[str] = []
    for name in undefined:
        if name in REPAIRS:
            lines.append(REPAIRS[name] + f"  % auto-repaired: recognizer invented \\{name}")
        elif name in NEVER_DECLARE:
            continue  # a real command; declaring it would clobber the real definition
        elif _OPERATOR_SHAPED.match(name):
            # \DeclareMathOperator overrides, so it is only safe for names we are confident
            # LaTeX does not already own.
            lines.append(f"\\DeclareMathOperator{{\\{name}}}{{{name}}}"
                         f"  % TODO: confirm operator name")
        else:
            lines.append(f"\\providecommand{{\\{name}}}[1]{{#1}}"
                         f"  % TODO: recognizer invented \\{name}; define or correct")

    for ch in unmapped_chars or []:
        lines.append(f"\\DeclareUnicodeCharacter{{{ord(ch):04X}}}{{\\ensuremath{{\\bullet}}}}"
                     f"  % TODO: {ch!r} has no mapping; choose a representation")

    if not lines:
        return ""
    return ("% --- generated declarations: every one is a mark the recognizer could not\n"
            "% --- justify. Each TODO is a decision the author still owes.\n"
            + "\n".join(lines) + "\n")
