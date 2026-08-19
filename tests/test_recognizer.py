"""The recognizer, with the model replaced by a stub.

**No test here calls a real model.** Recognition is nondeterministic and model-versioned;
asserting on it would be asserting on a coin flip. Accuracy is measured and recorded in
`baseline/`, never asserted in CI.

What *is* asserted is everything around the model — the constraints that were expensive to
learn and are cheap to regress.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from handzoo.core.recognize.base import Recognition, Recognizer
from handzoo.core.recognize.ollama_vlm import (
    DEFAULT_MODEL,
    THINKING_ALIASES,
    OllamaRecognizer,
    RecognitionError,
)


@pytest.fixture
def page(tmp_path: Path) -> Path:
    p = tmp_path / "p-0001.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n fake bytes; the transport is stubbed")
    return p


def _replies(*payloads):
    """A transport that returns the given payloads in order, recording each request."""
    calls: list[dict] = []
    queue = list(payloads)

    def transport(endpoint, body, timeout):
        calls.append({"endpoint": endpoint, "body": body, "timeout": timeout})
        return queue.pop(0) if queue else {"message": {"content": ""}}

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def _content(text: str) -> dict:
    return {"message": {"content": text}, "done_reason": "stop"}


# --------------------------------------------------------------- the checkpoint trap


@pytest.mark.parametrize("alias", sorted(THINKING_ALIASES))
def test_bare_aliases_are_refused_at_construction(alias: str) -> None:
    """`qwen3-vl:8b` is the *Thinking* checkpoint.

    It reasoning-loops on transcription: 2/26 blank pages, 92s median, versus 0/26 and 4s for
    the instruct tag. That cost a day to diagnose. Refuse it where it is cheap to notice
    rather than where it is expensive.
    """
    with pytest.raises(ValueError, match="Thinking checkpoint"):
        OllamaRecognizer(model=alias)


def test_the_default_model_is_an_instruct_tag() -> None:
    assert "instruct" in DEFAULT_MODEL
    assert DEFAULT_MODEL not in THINKING_ALIASES


# --------------------------------------------------------------- request shape


def test_num_ctx_is_bounded_and_generation_is_not(page: Path) -> None:
    """Both directions matter, and for opposite reasons.

    Ollama's default context (262,144) allocates ~42 GB for a 6 GB model and swaps the host.
    Capping `num_predict`, meanwhile, returns empty content because thinking shares that
    budget. So: bound the context, never the generation.
    """
    t = _replies(_content("x"))
    OllamaRecognizer(transport=t).transcribe(page)

    options = t.calls[0]["body"]["options"]
    assert options["num_ctx"] == 8192
    assert options["num_predict"] == -1


def test_uses_the_chat_endpoint_and_carries_a_timeout(page: Path) -> None:
    """`/api/generate` returned empty more often, and an attempt without a deadline cannot
    be recovered from — one stall ran past ten minutes."""
    t = _replies(_content("x"))
    OllamaRecognizer(transport=t, timeout=42).transcribe(page)
    assert t.calls[0]["endpoint"].endswith("/api/chat")
    assert t.calls[0]["timeout"] == 42


# --------------------------------------------------------------- empty is not success


def test_empty_content_is_retried(page: Path) -> None:
    t = _replies(_content(""), _content(""), _content("recovered"))
    assert OllamaRecognizer(transport=t).transcribe(page) == "recovered"
    assert len(t.calls) == 3


def test_exhausted_retries_raise_rather_than_return_empty(page: Path) -> None:
    """A blank that looks like success is how a page silently goes missing from a run.

    The failure this guards against arrived with `done_reason: "stop"` and a populated
    thinking field — indistinguishable from a clean response unless you check.
    """
    t = _replies(_content(""), _content(""), _content(""))
    with pytest.raises(RecognitionError, match="produced nothing"):
        OllamaRecognizer(transport=t).transcribe(page)


def test_transport_errors_are_retried_then_surfaced(page: Path) -> None:
    def failing(endpoint, body, timeout):
        raise TimeoutError("stalled")

    with pytest.raises(RecognitionError, match="TimeoutError"):
        OllamaRecognizer(transport=failing, attempts=2).transcribe(page)


@pytest.mark.parametrize("fenced,expected", [
    ("```latex\n\\section{x}\n```", "\\section{x}"),
    ("```tex\nbody\n```", "body"),
    ("```\nbody\n```", "body"),
    ("no fence", "no fence"),
])
def test_code_fences_are_stripped(page: Path, fenced: str, expected: str) -> None:
    t = _replies(_content(fenced))
    assert OllamaRecognizer(transport=t).transcribe(page) == expected


# --------------------------------------------------------------- the two passes


def test_recognize_runs_two_independent_passes(page: Path) -> None:
    """The inventory must be a separate call, not a field of the transcription response.

    A self-report from the pass being audited has already decided to drop the glyph. Measured:
    an independent pass surfaced exactly what transcription discarded.
    """
    inventory = json.dumps({"marks": [
        {"description": "stick figure", "context": "HAS MORE ... THAN",
         "inline_or_block": "inline", "count": 3}]})
    t = _replies(_content("\\section{x}"), _content(inventory))

    result = OllamaRecognizer(transport=t).recognize(page)

    assert len(t.calls) == 2, "inventory must be its own request"
    assert t.calls[0]["body"]["messages"][0]["content"] != \
        t.calls[1]["body"]["messages"][0]["content"], "the two passes must ask different things"
    assert result.markup == "\\section{x}"
    assert len(result.inventory) == 1


def test_inventory_keeps_placement_and_count_and_labels_kind_unknown(page: Path) -> None:
    """The model's *naming* of a mark is not reliable — it called a dog "a stick figure lying
    down" and tally marks "two people". Its placement and count were correct. So `kind` is
    never inferred from the description."""
    inventory = json.dumps({"marks": [
        {"description": "two people", "context": "Greater Than >",
         "inline_or_block": "block", "count": 4}]})
    t = _replies(_content("markup"), _content(inventory))

    (mark,) = OllamaRecognizer(transport=t).recognize(page).inventory

    assert mark.placement == "block"
    assert mark.count == 4
    assert mark.kind == "unknown"
    assert mark.description == "two people"


def test_unreadable_inventory_degrades_rather_than_failing_the_page(page: Path) -> None:
    """A page whose inventory will not parse still has usable markup."""
    t = _replies(_content("good markup"), _content("not json at all"))
    result = OllamaRecognizer(transport=t).recognize(page)
    assert result.markup == "good markup"
    assert result.inventory == ()


def test_inventory_survives_prose_around_the_json(page: Path) -> None:
    wrapped = 'Sure! Here you go:\n{"marks":[{"description":"d","context":"c",' \
              '"inline_or_block":"inline","count":1}]}\nHope that helps.'
    t = _replies(_content("markup"), _content(wrapped))
    assert len(OllamaRecognizer(transport=t).recognize(page).inventory) == 1


# --------------------------------------------------------------- the port


def test_satisfies_the_recognizer_protocol() -> None:
    assert isinstance(OllamaRecognizer(transport=_replies()), Recognizer)


def test_returns_a_recognition_tagged_with_its_provenance(page: Path) -> None:
    """The corpus is the long-term asset; a pair with no record of what produced it is worth
    much less later."""
    t = _replies(_content("markup"), _content("{}"))
    result = OllamaRecognizer(transport=t).recognize(page)
    assert isinstance(result, Recognition)
    assert result.provider == "ollama"
    assert result.model == DEFAULT_MODEL
