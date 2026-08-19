"""Recognizer backed by a local Ollama vision model.

Every constraint below was measured, most of them expensively. They are not tuning.

**Use the Instruct checkpoint.** `qwen3-vl:8b` is an alias for the *Thinking* checkpoint — a
reasoning model, on a task that wants none. Changing only the checkpoint, on a 26-page corpus:
blank responses 2/26 → 0/26, gate pass 81% → 96%, median latency 92s → 4s. A full day went
into diagnosing "blanks" that were reasoning loops in a model that should never have been
reasoning.

**`num_ctx` is correctness, not performance.** Ollama's default for this family is 262,144,
which allocates ~42 GB of KV cache for a 6 GB model and drives a 64 GB host into swap. The
resulting nondeterminism looks exactly like hard pages.

**Two passes, deliberately.** Transcription, then an *independent* inventory. A self-report
from the pass being audited has already decided to drop the glyph; a separate pass surfaced
exactly what transcription discarded. The inventory is trustworthy about where marks are and
untrustworthy about what they are — see `Mark`.

**Sequential only.** Concurrent requests to one Ollama instance starve each other; a competing
client hit a socket timeout despite a 30-minute deadline.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import Mark, Recognition

DEFAULT_MODEL = "qwen3-vl:8b-instruct"
DEFAULT_ENDPOINT = "http://localhost:11434/api/chat"
DEFAULT_NUM_CTX = 8192
DEFAULT_TIMEOUT = 300
"""Per attempt. Unbounded retry cannot recover from a stall of unbounded duration."""

TRANSCRIBE_PROMPT = (
    "Transcribe this handwritten page to LaTeX. Preserve headings, bullets, tables and "
    "inline math. Mark hand-drawn pictures inline as [[DIAGRAM: description]] at the exact "
    "point they occur. Never invent tikz. Output LaTeX only."
)

INVENTORY_PROMPT = (
    "Inventory every hand-drawn PICTURE on this page: stick figures, animals, houses, "
    "hands, faces, sketches, tally strokes, and annotation marks that point at or comment on "
    "other content.\n"
    "The test is LAYOUT, not symbol. A mark is a PICTURE when it occupies two dimensions on "
    "its own: a diagram whose labelled parts sit in space, curved or looping arrows between "
    "them, a sketch, a drawing. A mark is NOTATION when it sits in the flow of a line of "
    "text or an expression.\n"
    "So: a commutative diagram with arrows between labelled objects IS a picture. The arrows "
    "in `0 -> 1 -> 2 -> 3` written along one line are NOT. Greek letters, set and logic "
    "symbols, operators, braces, equals and inequality signs are never pictures.\n"
    "Do NOT transcribe the words. "
    'Return STRICT JSON only:\n{"marks":[{"description":"...","context":"...",'
    '"inline_or_block":"inline|block","count":N}]}\n'
    "A mark is 'inline' if it sits INSIDE a sentence or table cell as if it were a word.\n"
    "Keep every string under 8 words. Terse. No prose, no explanation, JSON only."
)
"""Brevity is a correctness requirement here, not a style preference.

Measured: an unconstrained inventory ran to 24,611 characters of florid description and was
truncated mid-JSON, so the whole response failed to parse and the page reported zero marks —
which silently disabled the coverage gate for that page. We do not trust the descriptions
anyway (see `Mark.description`), so asking for less costs nothing and buys the gate back."""

THINKING_ALIASES = frozenset({"qwen3-vl", "qwen3-vl:2b", "qwen3-vl:4b", "qwen3-vl:8b",
                              "qwen3-vl:30b", "qwen3-vl:32b", "qwen3-vl:235b"})
"""Bare tags that resolve to the Thinking checkpoint. Refused at construction rather than
discovered as a day of mysterious blank pages."""

Transport = Callable[[str, dict[str, Any], int], dict[str, Any]]
"""Seam for tests. CI must never call a real model: recognition is nondeterministic, and a
suite that asserts on it is asserting on a coin flip."""


class RecognitionError(RuntimeError):
    """Recognition did not produce usable output.

    Raised rather than returning an empty `Recognition`, because a blank that looks like
    success is how a page silently goes missing from a run.
    """


def _http_transport(endpoint: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def swap_pressure() -> float | None:
    """Fraction of swap in use, or None if it cannot be read.

    Part of the preflight: every measurement taken before this was diagnosed came from a host
    at 87% memory pressure with 29.7 GB of 30.7 GB swap consumed, and the symptom was not
    slowness but nondeterminism.
    """
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True,
                             check=False, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    total = used = None
    parts = out.replace("=", " ").split()
    for i, tok in enumerate(parts):
        if tok == "total" and i + 1 < len(parts):
            total = float(parts[i + 1].rstrip("M"))
        if tok == "used" and i + 1 < len(parts):
            used = float(parts[i + 1].rstrip("M"))
    if not total:
        return None
    return (used or 0.0) / total


@dataclass(frozen=True, slots=True)
class OllamaRecognizer:
    """A `Recognizer` over a local Ollama instance."""

    model: str = DEFAULT_MODEL
    endpoint: str = DEFAULT_ENDPOINT
    num_ctx: int = DEFAULT_NUM_CTX
    timeout: int = DEFAULT_TIMEOUT
    attempts: int = 3
    temperature: float = 0.1
    transport: Transport = field(default=_http_transport)

    def __post_init__(self) -> None:
        if self.model in THINKING_ALIASES:
            raise ValueError(
                f"{self.model!r} is an alias for the Thinking checkpoint, which reasoning-loops "
                "on transcription. Use an -instruct tag, e.g. qwen3-vl:8b-instruct."
            )

    # -- the port ---------------------------------------------------------------

    def recognize(self, page: Path) -> Recognition:
        """Transcribe a page, then inventory its marks in a second, independent pass."""
        markup = self._ask(page, TRANSCRIBE_PROMPT)
        inventory, failed = self._inventory(page)
        return Recognition(markup=markup, inventory=inventory, inventory_failed=failed,
                           provider="ollama", model=self.model)

    def transcribe(self, page: Path) -> str:
        return self._ask(page, TRANSCRIBE_PROMPT)

    def inventory(self, page: Path) -> tuple[Mark, ...]:
        """The independent second pass.

        Returns no marks when the response cannot be read at all. That is indistinguishable
        from a genuinely blank page *here*, which is why the coverage gate treats an empty
        inventory as `checked=False` rather than as a pass, and why `inventory_failed` exists
        for a caller that needs to tell the two apart.
        """
        return self._inventory(page)[0]

    def _inventory(self, page: Path) -> tuple[tuple[Mark, ...], bool]:
        """Marks, and whether the pass failed to be read at all."""
        try:
            return _parse_inventory(self._ask(page, INVENTORY_PROMPT)), False
        except (RecognitionError, json.JSONDecodeError):
            return (), True

    # -- internals --------------------------------------------------------------

    def _ask(self, page: Path, prompt: str) -> str:
        image = base64.b64encode(page.read_bytes()).decode()
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt, "images": [image]}],
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": -1,   # thinking shares this budget; capping it returns empty
                "num_ctx": self.num_ctx,
            },
        }
        last = ""
        for _ in range(self.attempts):
            try:
                payload = self.transport(self.endpoint, body, self.timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                continue
            content = _strip_fence((payload.get("message", {}).get("content") or "").strip())
            if content:
                return content
            last = "empty content with a successful response"
        raise RecognitionError(
            f"{self.model} produced nothing for {page.name} after {self.attempts} attempts "
            f"({last})")


def _strip_fence(text: str) -> str:
    for fence in ("```latex", "```tex", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
            break
    return text.removesuffix("```").strip()


_OBJECT = re.compile(r"\{[^{}]*\}")


def _salvage(raw: str) -> list[dict]:
    """Every complete `{...}` object in a truncated response, in order."""
    out = []
    for m in _OBJECT.finditer(raw):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "inline_or_block" in obj:
            out.append(obj)
    return out


def _parse_inventory(raw: str) -> tuple[Mark, ...]:
    """Read the inventory JSON, keeping only what was measured to be reliable.

    `description` is carried through for a human to read and is labelled untrusted on the
    type. Placement and count are what the coverage gate matches on.
    """
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return ()
    try:
        entries = json.loads(raw[start:end + 1]).get("marks", [])
    except json.JSONDecodeError:
        # A response cut off mid-object still describes the marks it managed to emit.
        # Recovering eight of ten marks is a usable coverage check; discarding all ten
        # silently turns the gate off, which is the failure this project refuses.
        entries = _salvage(raw)
        if not entries:
            raise
    marks: list[Mark] = []
    for entry in entries:
        placement = "inline" if str(entry.get("inline_or_block", "")).startswith("inline") \
            else "block"
        try:
            count = max(1, int(entry.get("count", 1)))
        except (TypeError, ValueError):
            count = 1
        marks.append(Mark(
            kind="unknown",           # the model's naming is not reliable enough to trust
            description=str(entry.get("description", "")).strip(),
            context=str(entry.get("context", "")).strip(),
            placement=placement,      # type: ignore[arg-type]
            count=count,
        ))
    return tuple(marks)
