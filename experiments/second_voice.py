"""Does a second local VLM's disagreement land where the author actually corrected?

§5.5.6 measured two *providers* disagreeing on one page and found a real detector. This asks
the cheaper, local-first version of the same question -- a second model on this machine -- over
every page in a corpus where the author's own correction log says what the defects were.

**The rule was written before any number was computed** (§5.5.6a), and is repeated here because
a decision rule discovered after the fact is not one:

    "concentrates" requires recall of author-edit sites >= 2x the shifted-null recall, with a
    bootstrap CI over pages excluding 1.0. Between 1 and 2 is reported as "weak and not worth
    a gate". Anything else is "does not concentrate".

Measured 2026-09-25 against olmOCR-2-7B: ratio 1.24 at the LaTeX level and 1.68 at the content-
word level -- weak -- and at the 14 flagged defect sites the second model carried the author's
text at 4. It flags where *both* models are wrong, which a reviewer cannot act on.

Three things this gets right that the naive version does not:

- **Both arms go through the same emitter.** `Correction.before` is emitted text -- normalized,
  with declarations and provenance. Raw markup diffed against it counts pipeline artifacts as
  disagreement about the page.
- **Diagrams are bucketed separately.** Two models never agree on a free-text diagram
  description, so leaving them in manufactures a hit on every page that has one.
- **There is a null.** A detector that flags a third of the page catches a third of the defects
  by accident. The null shifts the same flags, with the same widths, to random positions.

Usage:
    uv run python experiments/second_voice.py --project ~/handzoo-out/ch22 [...] \
        --model hf.co/richardyoung/olmOCR-2-7B-1025-GGUF:latest --work /tmp/second-voice
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handzoo.core.emit import emit
from handzoo.core.recognize.base import Recognition
from handzoo.core.recognize.ollama_vlm import OllamaRecognizer

WINDOW = 3
"""Tokens of slack when asking whether a flag and a label are the same place. An omission is a
zero-width site, so exact-position matching would score every dropped mark as a miss."""

SENTINEL = "\x00DIAGRAM\x00"
_COMMENT = re.compile(r"(?<!\\)%.*")
_DIAG = re.compile(r"\\texttt\{\[TODO [^}]*\}|\\includegraphics(?:\[[^\]]*\])?\{[^}]*\}")
_LATEX_TOK = re.compile(r"\x00DIAGRAM\x00|\\[a-zA-Z]+|\\.|[A-Za-z]+|[0-9]+|[^\s]")
_WORD_TOK = re.compile(r"\x00DIAGRAM\x00|[A-Za-z]+|[0-9]+")
_ENV = re.compile(r"\\(?:begin|end)\{[^}]*\}(?:\{[^}]*\})?")
_COMMAND = re.compile(r"\\[a-zA-Z]+\s*|\\.|[{}$&_^~#]")
_MATH_DELIM = [("\\(", "$"), ("\\)", "$"), ("\\[", "$$"), ("\\]", "$$")]


# --------------------------------------------------------------------------- page selection

def choose(projects: list[Path]) -> tuple[list[dict], dict]:
    """Pages whose labels can be trusted, and why each of the others was left out.

    An `authored` row makes the diff a record of the author's taste rather than of our defects
    (§11.3.1). A chain that does not join up means the rows are not successive states of one
    page, so first-before against last-after is not the correction.
    """
    kept, dropped = [], defaultdict(list)
    for d in projects:
        rows = [json.loads(line) for line in (d/"corrections.jsonl").read_text().splitlines()
                if line.strip()]
        by_page = defaultdict(list)
        for row in rows:
            by_page[row["page"]].append(row)
        for page, rs in sorted(by_page.items()):
            verdicts = {r["verdict"] for r in rs}
            img = next((Path(r["source_image"]) for r in rs if r.get("source_image")), None)
            if img is None or not img.exists():
                dropped["no page image"].append((d.name, page))
            elif "authored" in verdicts:
                dropped["carries an authored row"].append((d.name, page))
            elif edits := [r for r in rs if r["verdict"] == "edited"]:
                if not all(a["after"].strip() == b["before"].strip()
                           for a, b in pairwise(edits)):
                    dropped["edit chain broken"].append((d.name, page))
                elif edits[0]["before"].strip() == edits[-1]["after"].strip():
                    dropped["edited but unchanged"].append((d.name, page))
                else:
                    kept.append({"project": d.name, "page": page, "arm": "corrected",
                                 "dir": str(d), "image": str(img),
                                 "ours": edits[0]["before"], "truth": edits[-1]["after"]})
            elif verdicts == {"keep-reviewed"}:
                kept.append({"project": d.name, "page": page, "arm": "accepted", "dir": str(d),
                             "image": str(img), "ours": rs[-1]["before"],
                             "truth": rs[-1]["before"]})
            else:
                dropped[f"verdicts {sorted(verdicts)}"].append((d.name, page))
    return kept, dropped


# ------------------------------------------------------------------------------- the texts

def body(text: str) -> str:
    """The transcription alone: no preamble, no generated declarations, no comments.

    Math delimiters are equalized because `$A$` and `\\( A \\)` are the same claim about the page
    in two house styles, and no two models share one.
    """
    if "\\begin{document}" in text:
        text = text.split("\\begin{document}", 1)[1]
    text = _COMMENT.sub("", text.split("\\end{document}", 1)[0])
    for old, new in _MATH_DELIM:
        text = text.replace(old, new)
    return _DIAG.sub(SENTINEL, text)


def tokens(text: str, level: str) -> list[str]:
    """*latex* keeps commands whole, so `\\mathcal` against `\\mathbb` is visible -- and so is
    every difference of markup taste. *words* strips commands, so it is about what the page
    says. If the verdict differs between the two, that difference is the finding."""
    if level == "latex":
        return _LATEX_TOK.findall(text)
    out: list[str] = []
    for i, chunk in enumerate(text.split(SENTINEL)):
        if i:
            out.append(SENTINEL)
        out += [t.lower() for t in _WORD_TOK.findall(_COMMAND.sub(" ", _ENV.sub(" ", chunk)))]
    return out


def sites(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Where a and b differ, as intervals in `a`. An insertion has i1 == i2."""
    return [(i1, i2) for tag, i1, i2, _, _ in
            SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes() if tag != "equal"]


def touches_diagram(site, toks) -> bool:
    return SENTINEL in toks[max(0, site[0] - 1):min(len(toks), site[1] + 1)]


def hit(site, flags) -> bool:
    return any(f[0] - WINDOW <= site[1] and site[0] - WINDOW <= f[1] for f in flags)


def null_recall(labels, flags, n_tokens, draws=1000, seed=7) -> float:
    """The same flags, same widths, shuffled along the page: the "flag 40% and catch 40% by
    accident" control that decides whether a recall figure means anything."""
    rng = random.Random(seed)
    if not labels:
        return 0.0
    got = 0
    for _ in range(draws):
        shifted = [(s := rng.randint(0, max(0, n_tokens - (f[1] - f[0]))), s + f[1] - f[0])
                   for f in flags]
        got += sum(hit(site, shifted) for site in labels)
    return got / (draws * len(labels))


# ------------------------------------------------------------------------------- the model

def transcribe_all(pages: list[dict], model: str, out: Path) -> None:
    """Sequential, transcription pass only. Concurrent requests to one Ollama starve each
    other, and the inventory pass is about half the time and answers a different question."""
    out.mkdir(parents=True, exist_ok=True)
    rec = OllamaRecognizer(model=model)
    for i, p in enumerate(pages, 1):
        dest = out/f"{p['project']}-{p['page']:04d}.txt"
        if dest.exists():
            continue
        try:
            text = rec.transcribe(Path(p["image"]))
        except Exception as e:                                            # noqa: BLE001
            print(f"[{i}/{len(pages)}] {dest.stem}: FAILED {type(e).__name__}: {e}", flush=True)
            text = ""
        dest.write_text(text, encoding="utf-8")
        print(f"[{i}/{len(pages)}] {dest.stem}: {len(text)} chars", flush=True)


# ------------------------------------------------------------------------------ the scoring

def score(pages: list[dict], transcripts: Path, level: str) -> list[dict]:
    rows = []
    for p in pages:
        raw = transcripts/f"{p['project']}-{p['page']:04d}.txt"
        if not raw.exists() or not raw.read_text().strip():
            continue
        mode = "standalone" if "\\documentclass" in p["ours"] else "fragment"
        theirs = emit(Recognition(markup=raw.read_text(), inventory=(), provider="ollama",
                                  model="second-voice"),
                      mode=mode, page=p["page"], base_dir=Path(p["dir"])).text
        ours = tokens(body(p["ours"]), level)
        labels = [s for s in sites(ours, tokens(body(p["truth"]), level))
                  if not touches_diagram(s, ours)]
        flags = [s for s in sites(ours, tokens(body(theirs), level))
                 if not touches_diagram(s, ours)]
        rows.append({"project": p["project"], "page": p["page"], "arm": p["arm"],
                     "n_tokens": len(ours), "n_labels": len(labels), "n_flags": len(flags),
                     "hits": sum(hit(s, flags) for s in labels),
                     "coverage": sum(f[1] - f[0] + 1 for f in flags) / max(1, len(ours)),
                     "null": null_recall(labels, flags, len(ours))})
    return rows


# ------------------------------------------------------------- the invention follow-on

def classify(ours_span: list[str], truth_span: list[str]) -> str:
    """What kind of defect the author's edit fixed.

    *invention* is text we put on the page that the page does not carry -- deleted outright, or
    replaced by something sharing almost none of its tokens. *omission* is a mark we dropped.
    *substitution* is a token we read as a different token.
    """
    if not truth_span and ours_span:
        return "invention"
    if not ours_span and truth_span:
        return "omission"
    overlap = len(set(ours_span) & set(truth_span)) / max(len(ours_span), len(truth_span))
    return ("invention" if overlap < 0.34 and max(len(ours_span), len(truth_span)) >= 3
            else "substitution")


def already_refused(text: str) -> bool:
    """Would the gates already have stopped this page? A detector's only value is on the pages
    that survive everything we have -- a find on a page `repetition_gate` refuses is not a find.
    """
    from handzoo.core.validate import ascii_gate, delimiter_gate, repetition_gate
    return any(g.failures for g in (ascii_gate.check(text, fragment=True),
                                    delimiter_gate.check(text), repetition_gate.check(text)))


def invention(pages: list[dict], transcripts: Path, threshold: int) -> None:
    """Does a long one-sided run against the second transcript mark invention specifically?

    Measured 2026-09-25 (§5.5.6b): on pages no existing gate refuses, 2 of 6 invention sites.
    It fires on none of the 9 substitution or omission sites there -- but that is largely
    circular, because an omission contributes none of *our* tokens and a substitution is short
    by `classify`'s own definition. Far too partial to gate on either way.
    """
    from handzoo.core.validate import ascii_gate  # noqa: F401  (import cost lives here)
    tally: dict = defaultdict(lambda: defaultdict(int))
    for p in pages:
        raw = transcripts/f"{p['project']}-{p['page']:04d}.txt"
        if not raw.exists() or not raw.read_text().strip():
            continue
        bucket = "already refused" if already_refused(p["ours"]) else "passes every text gate"
        mode = "standalone" if r"\documentclass" in p["ours"] else "fragment"
        theirs = tokens(body(emit(Recognition(markup=raw.read_text(), inventory=(),
                                              provider="ollama", model="second-voice"),
                                  mode=mode, page=p["page"],
                                  base_dir=Path(p["dir"])).text), "words")
        ours = tokens(body(p["ours"]), "words")
        flags = [s for s in sites(ours, theirs) if s[1] - s[0] >= threshold]
        if p["arm"] == "accepted":
            tally[bucket]["accepted pages"] += 1
            tally[bucket]["accepted pages flagged"] += bool(flags)
            continue
        truth = tokens(body(p["truth"]), "words")
        for tag, i1, i2, j1, j2 in SequenceMatcher(a=ours, b=truth,
                                                   autojunk=False).get_opcodes():
            if tag == "equal" or touches_diagram((i1, i2), ours):
                continue
            kind = classify(ours[i1:i2], truth[j1:j2])
            tally[bucket][f"{kind} sites"] += 1
            tally[bucket][f"{kind} caught"] += hit((i1, i2), flags)
    for bucket, counts in tally.items():
        print(f"\n== {bucket}")
        for k in sorted(counts):
            print(f"   {k:26} {counts[k]}")


def pooled(rows: list[dict]) -> dict:
    labels = sum(r["n_labels"] for r in rows)
    hits = sum(r["hits"] for r in rows)
    null = sum(r["null"] * r["n_labels"] for r in rows) / labels if labels else 0.0
    return {"labels": labels, "hits": hits, "recall": hits/labels if labels else 0.0,
            "null": null, "ratio": (hits/labels/null) if labels and null else float("nan"),
            "coverage": mean(r["coverage"] for r in rows) if rows else 0.0}


def bootstrap(rows: list[dict], draws: int = 2000, seed: int = 11) -> tuple[float, float]:
    """Over *pages*, not tokens: one document contributing half the pages must not read as
    independent evidence."""
    rng = random.Random(seed)
    ratios = []
    for _ in range(draws):
        p = pooled([rng.choice(rows) for _ in rows])
        if p["labels"] and p["null"]:
            ratios.append(p["ratio"])
    ratios.sort()
    return (ratios[int(0.025*len(ratios))], ratios[int(0.975*len(ratios))]) if ratios else (0, 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", action="append", required=True, type=Path)
    ap.add_argument("--model", default="hf.co/richardyoung/olmOCR-2-7B-1025-GGUF:latest")
    ap.add_argument("--work", type=Path, required=True,
                    help="where transcripts land. Page text stays here, never in the repo.")
    ap.add_argument("--skip-run", action="store_true", help="score transcripts already there")
    ap.add_argument("--invention", type=int, metavar="T", default=None,
                    help="instead, ask whether a one-sided run of >= T of our tokens marks "
                         "invention specifically (§5.5.6b)")
    args = ap.parse_args(argv)

    pages, dropped = choose([p.expanduser() for p in args.project])
    print(f"{len(pages)} pages: {sum(p['arm']=='corrected' for p in pages)} corrected, "
          f"{sum(p['arm']=='accepted' for p in pages)} accepted")
    for why, items in sorted(dropped.items(), key=lambda kv: -len(kv[1])):
        print(f"  dropped {len(items):3}  {why}")
    if not args.skip_run:
        transcribe_all(pages, args.model, args.work/"transcripts")

    if args.invention is not None:
        invention(pages, args.work/"transcripts", args.invention)
        return 0

    for level in ("latex", "words"):
        rows = score(pages, args.work/"transcripts", level)
        corrected = [r for r in rows if r["arm"] == "corrected"]
        accepted = [r for r in rows if r["arm"] == "accepted"]
        p = pooled(corrected)
        lo, hi = bootstrap(corrected)
        verdict = ("concentrates" if p["ratio"] >= 2 and lo > 1 else
                   "weak, not worth a gate" if lo > 1 else "does not concentrate")
        print(f"\n== {level}: {p['hits']}/{p['labels']} sites caught, recall {p['recall']:.3f} "
              f"vs null {p['null']:.3f} -> ratio {p['ratio']:.2f} [{lo:.2f}, {hi:.2f}]")
        print(f"   flagged share of a page: {p['coverage']:.1%}"
              + (f" (accepted pages {mean(r['coverage'] for r in accepted):.1%})"
                 if accepted else ""))
        print(f"   VERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
