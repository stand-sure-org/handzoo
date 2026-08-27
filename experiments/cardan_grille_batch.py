"""Extend the Cardan grille measurement past n=3.

Runs every page of a document three ways -- localise, transcribe unmasked, transcribe masked --
and scores fabrication. Sequential: concurrent Ollama requests starve each other.

Fabrication proxy: LaTeX constructs that render 2-D structure. A page whose drawing was
transcribed honestly should not need them; a page where the model invented a diagram will.
This is a proxy, not ground truth, and pages are kept on disk so a claim can be checked by eye.
"""
import csv, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from grille import ask, boxes, mask, TRANSCRIBE

FAB = re.compile(r"\\begin\{(?:tikzpicture|array|tikzcd|matrix)\}|\\xrightarrow|\\xleftarrow"
                 r"|\\downarrow|\\uparrow|\\longrightarrow")


def score(t: str) -> int:
    return len(FAB.findall(t))


def words(t: str) -> set[str]:
    return {w for w in re.sub(r"\\[a-zA-Z]+|[^a-z ]", " ", t.lower()).split() if len(w) > 3}


def run(pdf: Path, out: Path, first: int, last: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with (out / "results.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["page", "boxes", "fab_unmasked", "fab_masked", "prose_kept",
                    "len_unmasked", "len_masked", "seconds"])
        for pg in range(first, last + 1):
            t0 = time.time()
            stem = out / f"p{pg:03d}"
            subprocess.run(["pdftoppm", "-png", "-r", "110", "-f", str(pg), "-l", str(pg),
                            str(pdf), str(stem)], capture_output=True, check=False)
            img = next(iter(sorted(out.glob(f"p{pg:03d}*.png"))), None)
            if img is None:
                continue
            try:
                bxs = boxes(img)
            except Exception as e:  # noqa: BLE001 - a failed page must not end the run
                print(f"  p{pg}: box error {type(e).__name__}", flush=True); continue
            if not bxs:
                w.writerow([pg, 0, "", "", "", "", "", round(time.time() - t0)])
                fh.flush()
                print(f"  p{pg}: no drawing found -> skipped", flush=True)
                continue
            masked = mask(img, bxs, img.with_name(img.stem + "-masked.png"))
            try:
                un, ma = ask(img, TRANSCRIBE), ask(masked, TRANSCRIBE)
            except Exception as e:  # noqa: BLE001
                print(f"  p{pg}: transcribe error {type(e).__name__}", flush=True); continue
            (out / f"p{pg:03d}-un.tex").write_text(un)
            (out / f"p{pg:03d}-ma.tex").write_text(ma)
            wu, wm = words(un), words(ma)
            kept = round(len(wu & wm) / max(len(wu), 1), 2)
            row = [pg, len(bxs), score(un), score(ma), kept, len(un), len(ma),
                   round(time.time() - t0)]
            w.writerow(row); fh.flush(); rows.append(row)
            print(f"  p{pg}: boxes={len(bxs)} fab {score(un)}->{score(ma)} "
                  f"prose_kept={kept} ({round(time.time()-t0)}s)", flush=True)
    print("BATCH DONE", flush=True)


if __name__ == "__main__":
    run(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
