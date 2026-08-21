"""The online provider, tested without ever calling it.

Same rule as everywhere else: CI never touches a model. The transport is injected, so what is
asserted here is the *contract* — the port, the refusals, and the distinction between a failed
inventory and an empty one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from handzoo.core.recognize.base import Recognizer
from handzoo.core.recognize.gemini_vlm import GeminiRecognizer
from handzoo.core.recognize.ollama_vlm import (
    INVENTORY_PROMPT,
    TRANSCRIBE_PROMPT,
    RecognitionError,
)


def _reply(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _page(tmp_path: Path) -> Path:
    p = tmp_path / "p.png"
    p.write_bytes(b"\x89PNG")
    return p


def test_it_satisfies_the_same_port(tmp_path: Path) -> None:
    assert isinstance(GeminiRecognizer(api_key="x"), Recognizer)


def test_it_refuses_to_run_without_a_key() -> None:
    """No silent fallback to the local model. A provider that quietly becomes a different
    provider makes every measurement taken with it meaningless."""
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiRecognizer(api_key="")


def test_the_prompts_are_the_same_ones_the_local_provider_uses(tmp_path: Path) -> None:
    """Comparing providers on different prompts compares prompts."""
    seen = []

    def transport(url, body, timeout):
        seen.append(body["contents"][0]["parts"][0]["text"])
        return _reply("markup")

    GeminiRecognizer(api_key="x", transport=transport).recognize(_page(tmp_path))
    assert TRANSCRIBE_PROMPT in seen and INVENTORY_PROMPT in seen


def test_an_empty_response_raises_rather_than_returning_nothing(tmp_path: Path) -> None:
    """The port's one hard rule: never return an empty Recognition as success. A blank that
    looks like a clean page is how a page silently goes missing from a run."""
    calls = {"n": 0}

    def transport(url, body, timeout):
        calls["n"] += 1
        return {"candidates": [{"finishReason": "SAFETY"}]}

    with pytest.raises(RecognitionError, match="SAFETY"):
        GeminiRecognizer(api_key="x", attempts=2, transport=transport).recognize(_page(tmp_path))
    assert calls["n"] == 2, "it must retry before giving up"


def test_a_failed_inventory_is_reported_as_failed_not_as_empty(tmp_path: Path) -> None:
    """An empty inventory means "no marks found"; a failure means "we do not know". Collapsing
    them lets a dead pass read as a clean page (DESIGN §5.7)."""
    def transport(url, body, timeout):
        text = body["contents"][0]["parts"][0]["text"]
        return _reply("markup") if text == TRANSCRIBE_PROMPT else {"candidates": []}

    result = GeminiRecognizer(api_key="x", attempts=1,
                              transport=transport).recognize(_page(tmp_path))
    assert result.markup == "markup"
    assert result.inventory == () and result.inventory_failed is True


def test_it_records_which_provider_and_model_produced_the_page(tmp_path: Path) -> None:
    r = GeminiRecognizer(api_key="x", model="gemini-test",
                         transport=lambda u, b, t: _reply("x")).recognize(_page(tmp_path))
    assert r.provider == "gemini" and r.model == "gemini-test"


def test_the_key_never_appears_in_the_repository() -> None:
    """It is read from the environment. A provider that can be configured from a file in the
    tree is one commit away from being configured *in* the tree."""
    from handzoo.core.recognize import gemini_vlm

    assert gemini_vlm.KEY_ENV == "GEMINI_API_KEY"
    source = Path(gemini_vlm.__file__).read_text(encoding="utf-8")
    assert "AIza" not in source
