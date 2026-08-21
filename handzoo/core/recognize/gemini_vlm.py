"""Gemini recognizer — the same port, over the wire.

**This sends page images off the machine.** CLAUDE.md constraint 7 is local-first, and it is
local-first because the manuscripts are unpublished. That constraint is not repealed by this
module: the provider is opt-in, never the default, and says out loud what it is doing. What
stays absolute is that no page content reaches the repository.

Why it exists: the local recognizer's characteristic failure is *over-correction* — helpfully
completing what the author wrote (§5.5, and ch17 p1's invented divisor pair). A more capable
model is not obviously better at that and may be worse, since the mechanism is helpfulness
rather than incapacity. That is a measurement, not a guess, and it needs a second provider to
make it.

The prompts are deliberately the ones the Ollama recognizer uses. They are the *contract* with
the recognizer role, not an Ollama detail, and changing them per provider would compare prompts
rather than models.
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
from .ollama_vlm import (
    INVENTORY_PROMPT,
    TRANSCRIBE_PROMPT,
    RecognitionError,
    _parse_inventory,
    _strip_fence,
)

DEFAULT_MODEL = "gemini-3-flash-preview"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
KEY_ENV = "GEMINI_API_KEY"
"""Read from the environment, never from a file in this repository."""


class AuthError(RuntimeError):
    """The credential was refused. Distinct from a recognition failure, and not retried.

    A 401 was previously caught alongside network errors, retried, and raised as "the model
    produced nothing" — but the model was never asked. On a long run with a short-lived
    credential that would record the whole tail of the document as pages the recognizer could
    not read, which is a misdiagnosis of exactly the kind this project keeps rediscovering.

    It matters most for federated auth, where the token is short-lived by design: an expiry
    mid-run must be obvious and immediate, not a slowly spreading stain of recognition errors.
    """


AUTH_STATUS = frozenset({401, 403})
"""Refused. Retrying a refused credential refuses again."""


def _http_transport(url: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
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
class GeminiRecognizer:
    """Port implementation. Same two passes, same refusals, different wire."""

    model: str = DEFAULT_MODEL
    temperature: float = 0.0
    timeout: int = 180
    attempts: int = 3
    api_key: str = field(default_factory=lambda: os.environ.get(KEY_ENV, ""))
    transport: Callable[[str, dict, int], dict] = _http_transport

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError(
                f"{KEY_ENV} is not set. This provider sends page images to Google; it is opt-in "
                "and has no local fallback.")

    def recognize(self, page: Path) -> Recognition:
        markup = self._ask(page, TRANSCRIBE_PROMPT)
        marks, failed = self._inventory(page)
        return Recognition(markup=markup, inventory=marks, inventory_failed=failed,
                           provider="gemini", model=self.model)

    def _inventory(self, page: Path) -> tuple[tuple[Mark, ...], bool]:
        """A failed inventory is reported as failed, never as an empty one.

        Same rule as the local provider: an empty inventory means "no marks found", and a
        failure means "we do not know". Collapsing them would let a dead pass read as a clean
        page (DESIGN §5.7).
        """
        try:
            return _parse_inventory(self._ask(page, INVENTORY_PROMPT)), False
        except (RecognitionError, ValueError):
            return (), True

    def _ask(self, page: Path, prompt: str) -> str:
        image = base64.b64encode(page.read_bytes()).decode()
        body = {
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/png", "data": image}},
            ]}],
            "generationConfig": {"temperature": self.temperature},
        }
        url = ENDPOINT.format(model=self.model) + f"?key={self.api_key}"
        last = ""
        for _ in range(self.attempts):
            try:
                payload = self.transport(url, body, self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code in AUTH_STATUS:
                    raise AuthError(
                        f"the credential was refused (HTTP {exc.code}). The model was not "
                        f"asked, so this is not a recognition failure. If you are using a "
                        f"short-lived or federated token it has probably expired; mint or "
                        f"refresh it and re-run with --resume.") from exc
                last = f"HTTP {exc.code}: {_why(exc)}"
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                continue
            content = _strip_fence(_first_text(payload).strip())
            if content:
                return content
            last = f"empty content ({_finish_reason(payload)})"
        raise RecognitionError(
            f"{self.model} produced nothing for {page.name} after {self.attempts} attempts "
            f"({last})")


def _first_text(payload: dict[str, Any]) -> str:
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if part.get("text"):
                return part["text"]
    return ""


def _finish_reason(payload: dict[str, Any]) -> str:
    """Why nothing came back. A safety block and a truncation are different problems, and an
    empty string tells the caller neither."""
    for candidate in payload.get("candidates", []):
        if candidate.get("finishReason"):
            return str(candidate["finishReason"])
    if "promptFeedback" in payload:
        return f"promptFeedback={payload['promptFeedback']}"
    return "no candidates"
