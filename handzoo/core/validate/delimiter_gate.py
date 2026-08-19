"""Gate: math delimiters and environments must balance.

The second failure the project is aimed at: incumbent exporters emit unbalanced `$`. An
unclosed math mode does not degrade gracefully — it swallows the rest of the document.

Imbalance is a **hard fail**, never a warning.
"""

from __future__ import annotations

import re

from .base import Failure, GateResult

GATE = "delimiters"

# Order matters: `$$` must be tried before `$`, and an escaped `\$` is not a delimiter.
_TOKEN = re.compile(
    r"(?P<comment>(?<!\\)%.*?$)"
    r"|(?P<escaped>\\[\\$%&#_{}])"
    r"|(?P<begin>\\begin\{(?P<benv>[^}]*)\})"
    r"|(?P<end>\\end\{(?P<eenv>[^}]*)\})"
    r"|(?P<open_inline>\\\()"
    r"|(?P<close_inline>\\\))"
    r"|(?P<open_display>\\\[)"
    r"|(?P<close_display>\\\])"
    r"|(?P<display_dollar>(?<!\\)\$\$)"
    r"|(?P<dollar>(?<!\\)\$)",
    re.MULTILINE,
)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def check(latex: str) -> GateResult:
    """Verify every opener has its closer, and nothing is left open at the end."""
    failures: list[Failure] = []
    stack: list[tuple[str, int, str]] = []  # (kind, line, detail)

    for m in _TOKEN.finditer(latex):
        kind = m.lastgroup
        line = _line_of(latex, m.start())

        if kind in ("comment", "escaped"):
            continue

        if kind == "begin":
            stack.append(("env:" + m.group("benv"), line, m.group("benv")))
        elif kind == "end":
            env = m.group("eenv")
            want = "env:" + env
            if stack and stack[-1][0] == want:
                stack.pop()
            else:
                open_env = stack[-1][2] if stack else "nothing"
                failures.append(Failure(
                    detail=f"\\end{{{env}}} closes {open_env}, which is not what is open",
                    line=line))
        elif kind in ("open_inline", "open_display"):
            stack.append((kind, line, r"\(" if kind == "open_inline" else r"\["))
        elif kind == "close_inline":
            if stack and stack[-1][0] == "open_inline":
                stack.pop()
            else:
                failures.append(Failure(detail=r"\) with no matching \(", line=line))
        elif kind == "close_display":
            if stack and stack[-1][0] == "open_display":
                stack.pop()
            else:
                failures.append(Failure(detail=r"\] with no matching \[", line=line))
        elif kind in ("dollar", "display_dollar"):
            # `$` and `$$` toggle rather than nest, so they pair with themselves.
            if stack and stack[-1][0] == kind:
                stack.pop()
            else:
                stack.append((kind, line, "$" if kind == "dollar" else "$$"))

    for kind, line, detail in stack:
        noun = "environment" if kind.startswith("env:") else "math mode"
        failures.append(Failure(
            detail=f"{noun} opened by {detail!r} is never closed", line=line))

    return GateResult(GATE, tuple(sorted(failures, key=lambda f: (f.line or 0, f.detail))))
