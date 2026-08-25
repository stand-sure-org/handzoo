"""`handzoo convert` — the CLI adapter. All logic lives in `handzoo.core`.

The output contract here is deliberate: **never print an unqualified PASS.** A tool whose
promise is "refuses to hand you broken LaTeX" trains its reader to relax exactly where it is
weakest, and this one is weakest at semantics — output that is well-formed and false. Every
verdict therefore names what was *not* checked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core import pipeline
from ..core.assemble import assemble
from ..core.pipeline import PageOutcome
from ..core.recognize.ollama_vlm import DEFAULT_MODEL, OllamaRecognizer, swap_pressure

SWAP_WARNING = 0.85


def _preflight(stream) -> None:
    """Report host conditions that have previously produced nondeterminism.

    A host deep in swap does not fail loudly; it produces blank pages and stalls that look
    like hard content. Saying so up front is cheaper than diagnosing it later.
    """
    swap = swap_pressure()
    if swap is not None and swap >= SWAP_WARNING:
        print(f"warning: swap at {swap:.0%}. Recognition on a swapping host has produced "
              "blank pages and multi-minute stalls that look like page problems.",
              file=stream)


def _format(outcome: PageOutcome) -> str:
    if not outcome.done:
        return f"page {outcome.page:>4}  ERROR  {outcome.error}"
    marks = "  ".join(f"{name}={state}" for name, state in outcome.gates.items())
    label = {"pass": "ok  ", "unverified": "?   ", "fail": "FAIL"}[outcome.verdict]
    return f"page {outcome.page:>4}  {label}  {marks}"


def main(argv: list[str] | None = None, *, stream=None) -> int:
    stream = stream or sys.stdout
    parser = argparse.ArgumentParser(
        prog="handzoo",
        description="Convert handwritten pages to LaTeX, refusing output that does not build.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--out", type=Path, default=Path("out"))
    parser.add_argument("--pages", help="page range, e.g. 3-9 or 4")
    parser.add_argument("--standalone", action="store_const", const="standalone",
                        dest="mode", default="fragment",
                        help="emit a complete document; the default is a fragment for "
                             "assembly with \\input")
    parser.add_argument("--resume", action="store_true",
                        help="skip pages already recorded in the manifest")
    parser.add_argument("--provider", choices=("ollama", "gemini", "anthropic"),
                        default="ollama",
                        help="ollama runs locally; gemini and anthropic SEND PAGE IMAGES "
                             "OFF THIS MACHINE. "
                             "Local is the default because the manuscripts are unpublished "
                             "(constraint 7); the cloud provider is opt-in and says so.")
    parser.add_argument("--model", default=None,
                        help="defaults to the provider's own default")
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args(argv)

    first, last = _parse_range(args.pages)
    _preflight(stream)

    from ..core.lexicon import discover as _discover_lexicon
    lexicon = _discover_lexicon(args.out)

    try:
        if args.provider == "gemini":
            from ..core.recognize.gemini_vlm import DEFAULT_MODEL as GEMINI_DEFAULT
            from ..core.recognize.gemini_vlm import GeminiRecognizer
            recognizer = GeminiRecognizer(model=args.model or GEMINI_DEFAULT)
            print(f"provider: gemini/{recognizer.model} — page images are being sent to "
                  "Google.\n           Local-first is the default for a reason; this run is "
                  "not local.", file=stream)
        elif args.provider == "anthropic":
            from ..core.recognize.anthropic_vlm import DEFAULT_MODEL as CLAUDE_DEFAULT
            from ..core.recognize.anthropic_vlm import AnthropicRecognizer
            recognizer = AnthropicRecognizer(model=args.model or CLAUDE_DEFAULT)
            print(f"provider: anthropic/{recognizer.model} — page images are being sent to "
                  "Anthropic.\n           Local-first is the default for a reason; this run is "
                  "not local.", file=stream)
        else:
            recognizer = OllamaRecognizer(model=args.model or DEFAULT_MODEL,
                                          lexicon_tokens=lexicon.tokens)
            if lexicon:
                # Announced, because it changes the prompt and therefore the output. A silent
                # prompt change makes two runs incomparable with nothing to say why.
                print(f"lexicon: {len(lexicon.tokens)} author shorthand(s) named to the "
                      "recognizer — tokens only, never their meanings.", file=stream)
    except ValueError as exc:
        print(f"error: {exc}", file=stream)
        return 2

    failed = unverified = errored = 0
    done: list[pipeline.PageOutcome] = []
    for outcome in pipeline.convert(args.pdf, args.out, recognizer, first=first, last=last,
                                    mode=args.mode, resume=args.resume, dpi=args.dpi):
        print(_format(outcome), file=stream, flush=True)
        done.append(outcome)
        errored += not outcome.done
        if outcome.done:
            failed += outcome.verdict == "fail"
            unverified += outcome.verdict == "unverified"

    if done:
        master = assemble(args.out, done)
        print(f"\nassembled -> {master.name}  (pages that failed appear as placeholders, "
              "never silently omitted)", file=stream)

    print("\nGates prove these documents build. They do not prove the transcription is "
          "correct:\nsilent substitution — a mark replaced rather than dropped — is not "
          "checked by anything here.", file=stream)
    if unverified:
        print(f"{unverified} page(s) unverified: a gate could not run, which is neither a "
              "pass nor a failure. Fragments cannot be compiled in isolation — use "
              "--standalone to check one.", file=stream)
    if failed or errored:
        print(f"{failed} page(s) failed a gate, {errored} could not be recognized. "
              "Failing pages are written as .fail.tex so a build cannot consume them.",
              file=stream)
    return 1 if (failed or errored) else 0


def _parse_range(spec: str | None) -> tuple[int, int | None]:
    if not spec:
        return 1, None
    if "-" in spec:
        lo, _, hi = spec.partition("-")
        return int(lo), int(hi)
    return int(spec), int(spec)


if __name__ == "__main__":
    raise SystemExit(main())
