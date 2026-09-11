"""Gate: the document must actually compile, headless, with zero errors.

This is the gate the whole positioning rests on. The other two are cheap static checks; this
one is the claim. If it does not build, it does not ship.

`pdflatex` is used rather than `tectonic`: it is what is installed and verified working.
`tectonic` remains the eventual choice for CI reproducibility (self-contained, fetches
packages on demand), and swapping is a change of binary, not of design.
"""

from __future__ import annotations

import re
import shutil
import os
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from .base import Failure, GateResult

GATE = "compile"
ENGINE = "pdflatex"
TIMEOUT_SECONDS = 120

_TEX_ERROR = re.compile(r"^! (?P<msg>.+)$", re.MULTILINE)
_TEX_LINE = re.compile(r"^l\.(?P<line>\d+)(?P<rest>.*)$", re.MULTILINE)


def engine_available() -> bool:
    return shutil.which(ENGINE) is not None


def check(latex: str, *, timeout: int = TIMEOUT_SECONDS,
          base_dir: Path | None = None) -> GateResult:
    """Compile in a scratch directory and report every TeX error.

    A missing engine returns `checked=False` — **not** a pass. A gate that quietly skips is
    how a suite goes green while verifying nothing, which is the exact failure mode this
    project exists to refuse.

    `base_dir` is the run's output directory, put on `TEXINPUTS` so the engine can find assets
    that sit beside the document — a cropped figure, above all. Compiling in a scratch
    directory otherwise makes every relative `\\includegraphics` unresolvable, which would have
    the gate reject the crop verdict's own correct output (DESIGN §7.2).

    It is added to the search path, not the working directory: the scratch directory still
    receives every intermediate file, so a run never writes `.aux` and `.log` litter into the
    author's output.
    """
    if not engine_available():
        return GateResult(GATE, checked=False)

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "document.tex"
        src.write_text(latex, encoding="utf-8")
        env = None
        if base_dir is not None:
            # Trailing "" preserves the engine's default search path; without it TEXINPUTS
            # replaces the distribution's own directories and nothing compiles at all.
            env = {**os.environ,
                   "TEXINPUTS": f"{Path(base_dir).resolve()}{os.pathsep}{os.environ.get('TEXINPUTS', '')}"}
        try:
            proc = subprocess.run(
                [ENGINE, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape",
                 src.name],
                cwd=tmp, capture_output=True, text=True, timeout=timeout, check=False, env=env,
            )
        except subprocess.TimeoutExpired:
            return GateResult(GATE, (Failure(detail=f"{ENGINE} timed out after {timeout}s"),))

        if proc.returncode == 0:
            return GateResult(GATE)

        log_path = Path(tmp) / "document.log"
        log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() \
            else proc.stdout

        failures = [
            Failure(detail=m.group("msg").strip(), line=line, excerpt=excerpt)
            for m, line, excerpt in _pair_errors_with_lines(log)
        ]
        if not failures:
            failures = [Failure(detail=f"{ENGINE} exited {proc.returncode} with no parsed error")]
        return GateResult(GATE, tuple(failures))


def check_fragment(fragment: str, *, preamble: str, timeout: int = TIMEOUT_SECONDS,
                   base_dir: Path | None = None) -> GateResult:
    """Compile a fragment inside the preamble the chapter will give it.

    A fragment has no preamble, so compiling it alone proves nothing -- which is why fragment
    runs used to report this gate *not checked* (ch17: 8 of 13 pages, DESIGN 11.4). The caller
    passes `normalize.chapter_preamble([page])` -- the function the master is built with, given
    this page alone -- so this proves the page builds the way the chapter builds it. It cannot
    catch what spans pages -- a macro defined on page 3 and used on page 7 -- and the master's
    declarations are derived from every page, so the two can differ at the edges; compiling the
    chapter is the backstop for both.

    Errors are reported at the fragment's own line numbers. The wrapper puts a preamble above
    the page; a line counted in the wrapped document would point the author at a line of their
    file that says something else. An error that falls outside the fragment -- in the preamble,
    or at the closing `\\end{document}` -- keeps its message and loses its line.
    """
    head = preamble + "\\begin{document}\n"
    result = check(head + fragment + "\n\\end{document}\n", timeout=timeout, base_dir=base_dir)
    if not result.failures:
        return result
    offset, length = head.count("\n"), fragment.count("\n") + 1
    mapped = tuple(
        replace(f, line=(f.line - offset if f.line and 0 < f.line - offset <= length else None))
        for f in result.failures)
    return replace(result, failures=mapped)


def _pair_errors_with_lines(log: str):
    """TeX reports `! message` and then, separately, `l.<n> <context>`.

    Pairing them by position is what turns "Missing $ inserted" into something a human can
    act on. Errors with no following line marker still surface, without a line number.
    """
    errors = list(_TEX_ERROR.finditer(log))
    markers = list(_TEX_LINE.finditer(log))
    for i, err in enumerate(errors):
        stop = errors[i + 1].start() if i + 1 < len(errors) else len(log)
        following = next((m for m in markers if err.end() <= m.start() < stop), None)
        if following:
            yield err, int(following.group("line")), following.group("rest").strip()[:80]
        else:
            yield err, None, ""
