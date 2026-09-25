# Backlog — captured, not scheduled

Things the author wants remembered rather than lost between sessions. Each entry says what is
already known, so picking one up does not start with rediscovery. Nothing here is committed to a
milestone.

*Last captured: 2026-09-24.*

## Surface

**The typeset pane scales down but not up.** *Ours to fix, cause found.* `.pane img` clamps to
`max-width:100%; max-height:100%`, and the only thing that lifts the clamp is the `.zoom` class —
toggled by the **fit** button, which exists on the ink pane (`#imgwrap`) and not on the typeset
pane (`#setwrap`). So the typeset pane can shrink but never exceed the pane. Either give it the
same toggle or drop the clamp whenever `--scale > 1`.

**Related, and worth doing anyway: typeset at a larger base font.** A handwritten page carries far
less text than a typeset page holds, so the compiled page is mostly white and the readable part is
small. The author's suggestion. It is a preamble change (`normalize.PREAMBLE`), so it affects
`chapter.tex` and every emitted page, not just the preview — which is an argument for doing it
deliberately rather than as a UI tweak.

**The SVG logo should appear in the app.** `assets/brand/` already holds `handzoo-compact.svg`,
`handzoo-hero.svg`, `handzoo-og.svg`. The surface currently shows the word "HandZoo" in the header.
Note the trademark position (`TRADEMARK.md`): the SVGs are trademarked assets, not freely-licensed
source, so the file that ships them needs to say so.

**Choosing or creating a project folder from inside the app.** Today the folder is a CLI argument
(`handzoo-ui <folder>`), and the surface serves exactly one project — a deliberate scope decision
when in-app ingestion was built (in-app-ingestion D3). Allowing it in the browser means a projects
*root*, a picker, and a project identity in every request; a file-picker cannot hand a web page a
directory path, so it would be a served list of folders under a root, not a native dialog.

## Distribution

**How does anyone install this?** Console scripts already exist (`handzoo`, `handzoo-review`,
`handzoo-ui`), so `pipx install` / `uv tool install` works from a wheel today. Open questions:
PyPI, a Homebrew formula (CLI) versus a cask (an app bundle), and whether a `HandZoo.app` is worth
it at all given the product is a CLI plus a local browser surface. External binaries are the real
packaging problem, not Python: `pdftoppm`/`pdftocairo` (poppler), `pdflatex` (a full TeX
distribution), and Ollama with a 6 GB model. A Homebrew formula can declare poppler; it cannot
sanely declare MacTeX.

**Code signing — "trusted" means two different things.** *Unverified; check before relying on it.*
(1) *Gatekeeper trust* for a downloadable `.app` appears to need a paid Apple Developer ID for
notarization; ad-hoc signing does not clear Gatekeeper, and unsigned apps make users right-click →
Open or strip the quarantine attribute. (2) *Supply-chain provenance* — proving the artifact came
from this repo's CI — has free paths: Sigstore/cosign keyless signing, and PyPI Trusted Publishing
with attestations from GitHub Actions. A pip/pipx distribution sidesteps (1) entirely, which is one
more argument for shipping as a Python package rather than an app bundle.

**GitHub funding.** No `.github/FUNDING.yml` yet; the author is setting up the account side.

## Supply chain

**SBOM and provenance in CI.** Generate an SBOM (CycloneDX or SPDX) per release and attach
provenance attestations, so a consumer can tell what is inside a build and where it came from. The
repo already runs licence, SAST, secrets and vulnerability checks through the shared merge-checks
workflow.

**Dependency tracking beyond Dependabot.** The author wants defence in depth — a dependency-track
style service that watches an SBOM continuously, rather than only PR-time alerts. Pairs with the
SBOM item: the SBOM is the input.
