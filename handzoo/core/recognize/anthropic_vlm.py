"""Anthropic recognizer — the third opinion, and why a third was worth having.

**Not for accuracy. For breaking ties.** Two providers disagreeing localizes substitution
(§5.5.6) but cannot say which reading is right, so every flag needs a human. Measured across
ch17, qwen3-vl and Gemini agree on 79% of content and produce **5.3 disagreements per page** —
they fail differently, which is what makes the detector work, and it leaves a review queue with
no way to shorten it. A third independent reading turns a disagreement into a vote: where two
agree and one differs, the odd one out is the suspect.

**This sends page images off the machine**, exactly as the Gemini provider does, and the same
rules hold — opt-in, never the default, announced on every run, key from the environment only,
no page content in the repository (constraint 7).

**One caveat that is not technical.** When Claude is used to *adjudicate* a comparison — as it
has been throughout this project's measurement — putting Claude in the pool of things being
compared removes that independence. Contested pages involving this provider should be called by
the author, not by an assistant of the same family.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .base import Mark, Recognition
from .gemini_vlm import AUTH_STATUS, AuthError
from .ollama_vlm import (
    INVENTORY_PROMPT,
    TRANSCRIBE_PROMPT,
    RecognitionError,
    _parse_inventory,
    _strip_fence,
)

DEFAULT_MODEL = "claude-sonnet-5"
"""Sonnet rather than Haiku, deliberately. For a *detector* the cheapest model is the wrong
choice: a weaker reading contributes its own errors as false flags, and with 5.3 disagreements
per page already in play, precision matters more than price."""

ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
KEY_ENV = "ANTHROPIC_API_KEY"
MAX_TOKENS = 8192
"""A page of dense handwriting transcribes long. Truncation would read as a short page."""


def _http_transport(url: str, body: dict[str, Any], timeout: int,
                    key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": API_VERSION})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode())


def _why(exc) -> str:
    """What the server actually said. A status without a reason is not a diagnosis.

    Measured: a 400 reported as "Bad Request" while the body read *"`temperature` is deprecated
    for this model"* — the whole answer, discarded one line from where it was needed.
    """
    try:
        detail = json.loads(exc.read().decode())
    except Exception:  # noqa: BLE001 - a body we cannot parse is still better named than hidden
        return str(getattr(exc, "reason", "")) or "no detail"
    if isinstance(detail, dict):
        err = detail.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
    return str(detail)[:200]


@dataclass
class AnthropicRecognizer:
    """Port implementation. Same two passes, same prompts, same refusals."""

    model: str = DEFAULT_MODEL
    timeout: int = 180
    attempts: int = 3
    api_key: str = field(default_factory=lambda: os.environ.get(KEY_ENV, ""))
    transport: Callable[..., dict] = _http_transport

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError(
                f"{KEY_ENV} is not set. This provider sends page images to Anthropic; it is "
                "opt-in and has no local fallback.")

    def recognize(self, page: Path) -> Recognition:
        markup = self._ask(page, TRANSCRIBE_PROMPT)
        marks, failed = self._inventory(page)
        return Recognition(markup=markup, inventory=marks, inventory_failed=failed,
                           provider="anthropic", model=self.model)

    def _inventory(self, page: Path) -> tuple[tuple[Mark, ...], bool]:
        """A failed inventory is reported as failed, never as an empty one (§5.7)."""
        try:
            return _parse_inventory(self._ask(page, INVENTORY_PROMPT)), False
        except (RecognitionError, ValueError):
            return (), True

    def _ask(self, page: Path, prompt: str) -> str:
        image = base64.b64encode(page.read_bytes()).decode()
        body = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            # No `temperature`: claude-sonnet-5 rejects it outright — "`temperature` is
            # deprecated for this model" — and the models that accept it default to what we
            # would ask for anyway.

            "messages": [{"role": "user", "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": image}},
                {"type": "text", "text": prompt},
            ]}],
        }
        last = ""
        for _ in range(self.attempts):
            try:
                payload = self.transport(ENDPOINT, body, self.timeout, self.api_key)
            except urllib.error.HTTPError as exc:
                if exc.code in AUTH_STATUS:
                    raise AuthError(
                        f"the credential was refused (HTTP {exc.code}). The model was not "
                        "asked, so this is not a recognition failure. Check "
                        f"`fnox get {KEY_ENV}` and re-run with --resume.") from exc
                last = f"HTTP {exc.code}: {_why(exc)}"
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                continue
            content = _strip_fence(_first_text(payload).strip())
            if content:
                return content
            last = f"empty content (stop_reason={payload.get('stop_reason')})"
        raise RecognitionError(
            f"{self.model} produced nothing for {page.name} after {self.attempts} attempts "
            f"({last})")


def _first_text(payload: dict[str, Any]) -> str:
    for block in payload.get("content", []):
        if block.get("type") == "text" and block.get("text"):
            return block["text"]
    return ""
