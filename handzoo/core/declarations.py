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
from collections.abc import Iterable

# A `%` that is not escaped starts a comment. Same rule as `target._COMMENT`, and for the same
# reason: a line the author commented out is a line they are not loading.
_COMMENT = re.compile(r"(?<!\\)%.*")

# Control sequences provided by the standard preamble: base LaTeX + amsmath/amssymb/amsthm.
# Not exhaustive by design — anything absent gets declared rather than assumed.
KNOWN = frozenset(
    """
    begin end item documentclass usepackage newtheorem section subsection subsubsection
    paragraph noindent indent textbf textit texttt textrm textsf emph underline text mathrm
    mathbf mathit mathsf mathtt mathbb mathcal mathfrak frac dfrac tfrac sqrt sum prod int
    oint lim sup inf max min left right big Big bigg Bigg langle rangle lfloor rfloor lceil
    rceil quad qquad hspace vspace hfill vfill newline linebreak pagebreak clearpage newpage
    label ref eqref cite footnote caption centering raggedright raggedleft hline cline
    multicolumn multirow rule textwidth linewidth textheight ensuremath providecommand
    newcommand renewcommand DeclareMathOperator DeclareUnicodeCharacter textcircled circ cdot
    cdots ldots vdots ddots dots alpha beta gamma delta epsilon varepsilon zeta eta theta
    vartheta iota kappa lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi
    varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega in notin
    ni subset supset subseteq supseteq subsetneq supsetneq cup cap bigcup bigcap setminus
    emptyset varnothing forall exists nexists neg lnot land lor leq geq neq equiv approx sim
    simeq cong propto mid nmid parallel perp to gets mapsto rightarrow leftarrow
    leftrightarrow longrightarrow longleftarrow Rightarrow Leftarrow Leftrightarrow implies
    impliedby iff uparrow downarrow xrightarrow xleftarrow hookrightarrow twoheadrightarrow
    rightsquigarrow times div pm mp ast star dagger oplus otimes odot infty partial nabla
    overline overbrace underbrace widehat widetilde hat tilde bar vec dot ddot pmod bmod mod
    gcd deg det dim ker exp log ln sin cos tan arg textquotedblleft textquotedblright
    textquoteleft textquoteright S P dag ddag copyright pounds textemdash textendash
    textbackslash textasciitilde not top bot colon hrule vrule because therefore models vdash
    dashv lneq gneq ll gg prec succ preceq succeq asymp doteq triangleq binom choose over atop
    overset underset stackrel substack mathop mathbin mathrel mathpunct mathopen mathclose
    includegraphics graphicspath resizebox scalebox includepdf
    limits nolimits displaystyle textstyle scriptstyle scriptscriptstyle smallskip medskip
    bigskip par relax empty null
    """.split()  # noqa: SIM905 - 294 words read better wrapped than as a literal
)

# KNOWN exists only to keep the generated block small and readable. It is NOT a
# correctness mechanism: every emitted declaration is \ifdefined-guarded, so a name
# wrongly absent from this list costs a redundant no-op guard, never a clobbered command.
#
# **That safety argument runs in one direction only, and an earlier version of this comment
# did not say so.** It covers a name wrongly *absent*. A name wrongly *present* is not
# covered, and fails silently:
#
#     div   ->  \div    renders as  ÷    (an author writing divergence)
#     int   ->  \int    renders as  ∫    (an author writing the interior of a set)
#     top   ->  \top    renders as  ⊤    (an author writing Top, the category)
#     P     ->  \P      renders as  ¶    (an author writing a poset, or probability)
#     S     ->  \S      renders as  §    (an author writing the symmetric group)
#     star, ast                            (Hodge star; adjoint or pullback)
#
# Each is a short token that is both a standard LaTeX command and a common domain operator
# name. Because it is in KNOWN, `find_undefined` does not report it, no declaration is
# generated, and no TODO is emitted. Measured: `$\div \vec{F}$` compiles clean, is
# ASCII-clean and balanced, and typesets `÷F⃗`. Every gate passes and the meaning is wrong.
#
# **Deleting the name from this list does not fix it.** `\ifdefined\div` finds base LaTeX's
# own definition, so the guard no-ops and the output is still ÷. The `\ifdefined` mechanism
# is load-bearing everywhere else in this module and this is the one place it works against
# us. A fix has to be a check on what was *emitted*, not a change to what is declared —
# DESIGN §11.0.1e.

# Macros the recognizer has been measured to invent, and what it plainly meant.
REPAIRS = {
    "circled": r"\providecommand{\circled}[1]{\textcircled{\small #1}}",
    "boxed": r"\providecommand{\boxed}[1]{\fbox{$#1$}}",
    "underarrow": r"\providecommand{\underarrow}[1]{\underset{\downarrow}{#1}}",
}

# Characters `pylatexenc` cannot map, measured in the corpus, with the glyph the author
# plainly meant. Same pattern as REPAIRS above: what has been *seen* gets a mapping, and
# everything else keeps the marked fallback rather than a guess (DESIGN 5.7.1).
#
# Measured on the 126-page Number theory run: 8 checkmarks, 5 ballot crosses, 13 circled
# digits. A checkmark asserting an axiom holds is a term in the sentence, not decoration, so
# rendering it as a bullet is a marked substitution -- better than a silent one, worse than
# the right glyph.
CHARACTER_REPAIRS = {
    "\u2713": ("\\ensuremath{\\checkmark}", "amssymb"),
    "\u2714": ("\\ensuremath{\\checkmark}", "amssymb"),
    "\u2717": ("\\ding{55}", "pifont"),
    "\u2718": ("\\ding{55}", "pifont"),
    **{chr(0x2460 + i): (f"\\textcircled{{\\small {i + 1}}}", "base")
       for i in range(9)},
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


def repairable(chars: Iterable[str]) -> list[str]:
    """The characters still worth declaring under an engine that draws Unicode natively.

    Under LuaLaTeX a bare codepoint is usually the *correct* output -- the author's blackboard
    bold and script letters reach the page as themselves, and declaring them would renotate a
    mark (constraint #5). The exception is a character the *text* font cannot draw, which is
    where these land: measured, ✗ in body text is `Missing character: There is no ✗ (U+2717)
    in font [lmroman10-regular]`, and the page loses it silently. The macro is not a nicer
    spelling of the character, it is the only way the mark reaches the page at all.

    Note the asymmetry that makes this safe: `\\checkmark` resolves because `unicode-math`
    defines it and the *math* font carries the glyph -- so the repair reaches a drawable
    glyph by a different route than the bare character does.
    """
    return [ch for ch in chars if ch in CHARACTER_REPAIRS]


def packages_in(preamble: str) -> set[str]:
    """Every package a preamble already loads, plus what it makes unsafe to add.

    Measured 2026-11-16: `\\usepackage{amssymb}` after `unicode-math` is a hard error --
    `\\eth`, `\\smallsetminus`, `\\digamma` and `\\backepsilon` are already defined, four
    errors the compile gate would rightly refuse. It is unnecessary as well as unsafe, because
    `unicode-math` defines `\\checkmark` itself: with amssymb dropped the same page builds
    with zero errors and zero dropped characters.

    So the package a repair needs is a property of the *preamble*, not of the character.

    Comments are stripped first, for the reason `Target` already learned: the author's
    preamble is being figured out as it is used, so a commented-out `% \\usepackage{pifont}`
    is a line they are *not* loading. Counting it as supplied would withhold the package the
    repair needs and leave `\\ding` undefined.
    """
    live = _COMMENT.sub("", preamble)
    found: set[str] = set()
    for group in re.findall(r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{([^}]*)\}", live):
        found.update(name.strip() for name in group.split(","))
    if "unicode-math" in found:
        found.add("amssymb")
    return found


def declarations_for(undefined: list[str], unmapped_chars: list[str] | None = None,
                     source: str = "", *, accepts_unicode: bool = False,
                     supplied: set[str] | None = None) -> str:
    """Build the declaration block that makes an unruly document compile honestly.

    `source` is the document body, used to decide whether each macro takes an argument.
    Without it we fall back to the name-shape guess, which is known to be wrong.

    `accepts_unicode` picks the mechanism, which is not a stylistic choice:
    `\\DeclareUnicodeCharacter` is an inputenc feature and does nothing under LuaLaTeX, so a
    repair written that way is silently inert -- the character reaches the engine, no font
    carries it, and it leaves the page as a `Missing character` line in a log nobody reads.
    `\\newunicodechar` is the mechanism that works there.

    `supplied` is what the preamble already loads; a repair's package is emitted only when it
    is genuinely absent. Pass `packages_in(the preamble)`.
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

    have = supplied or set()
    wanted: list[str] = []          # packages the repairs need and the preamble lacks
    mappings: list[str] = []

    for ch in unmapped_chars or []:
        repair = CHARACTER_REPAIRS.get(ch)
        if repair:
            glyph, pkg = repair
            if pkg != "base" and pkg not in have and pkg not in wanted:
                wanted.append(pkg)
        if accepts_unicode:
            # Callers filter with `repairable` first, so an unmapped character here means the
            # font is expected to draw it. Declaring it as a bullet is the renotation the
            # Unicode target exists to stop, so nothing is emitted for it at all.
            if repair:
                mappings.append(
                    f"\\newunicodechar{{{ch}}}{{{glyph}}}"
                    f"  % measured: the text font does not draw U+{ord(ch):04X}")
        elif repair:
            # \DeclareUnicodeCharacter has no \ifdefined equivalent; redeclaring is harmless.
            mappings.append(f"\\DeclareUnicodeCharacter{{{ord(ch):04X}}}{{{glyph}}}"
                            f"  % measured in the corpus")
        else:
            mappings.append(
                f"\\DeclareUnicodeCharacter{{{ord(ch):04X}}}{{\\ensuremath{{\\bullet}}}}"
                f"  % TODO: {ch!r} has no mapping; choose a representation")

    if mappings and accepts_unicode and "newunicodechar" not in have:
        wanted.append("newunicodechar")
    # Packages first: \newunicodechar is used the moment it is written, and a package cannot
    # be loaded after it.
    lines = [f"\\usepackage{{{pkg}}}  % for the repairs below" for pkg in wanted] \
        + lines + mappings

    if not lines:
        return ""
    return ("% --- generated declarations: every one is a mark the recognizer could not\n"
            "% --- justify. Each TODO is a decision the author still owes.\n"
            + "\n".join(lines) + "\n")
