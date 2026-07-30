# Fig. 1 Multistage Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover and permanently cache the real Figure 1 when arXiv HTML uses a loose layout or is unavailable, using only official arXiv HTML, source, and PDF inputs.

**Architecture:** Keep HTML metadata parsing, remote-image mirroring, and local fallback extraction as separate units. Extend the persisted Figure contract to represent local-only panels and recovery state, add bounded source/PDF extractors, then let one recovery service refresh and backfill every latest/archive/cache copy before Astro renders source-aware controls.

**Tech Stack:** Python 3.13, Pydantic 2, httpx, selectolax/Lexbor, standard-library tarfile, pdfplumber 0.11, pypdfium2/Pillow, pytest/respx/reportlab, Astro 6, TypeScript, Zod 4, Vitest, Playwright, GitHub Actions, GitHub Pages.

---

## File Responsibility Map

- `src/vla_wam_daily/figures.py`: parse standard and tightly bounded loose arXiv HTML Figure layouts.
- `src/vla_wam_daily/models.py`: validate HTML, source, and PDF Figure sources plus local-only panels and recovery metadata.
- `src/vla_wam_daily/figure_store.py`: mirror remote panels and atomically install validated recovered bytes.
- `src/vla_wam_daily/figure_source.py`: safely download/inspect e-print archives and extract an unambiguous Figure 1 asset.
- `src/vla_wam_daily/figure_pdf.py`: find an unambiguous Figure 1 caption/candidate box and render a PNG crop.
- `src/vla_wam_daily/figure_recovery.py`: run HTML refresh, source extraction, and PDF extraction in order.
- `src/vla_wam_daily/figure_sync.py`: apply recovery to every permanent record and publish recovery counters.
- `src/vla_wam_daily/cli.py`: construct and close recovery dependencies for `daily` and `sync-figures`.
- `tests/fixtures/arxiv/figures-loose.html`: deterministic arXiv loose-Figure DOM.
- `tests/test_figures.py`: HTML regression and non-crossing parser tests.
- `tests/test_models.py`: local-only/source/recovery schema tests.
- `tests/test_figure_store.py`: recovered-byte installation tests.
- `tests/test_figure_source.py`: source archive security and extraction tests.
- `tests/test_figure_pdf.py`: caption detection, confidence, and crop tests.
- `tests/test_figure_recovery.py`: ordered fallback, caching, and partial-failure tests.
- `tests/test_figure_sync.py`: permanent latest/archive/cache recovery consistency and metrics.
- `tests/test_cli.py`: lifecycle wiring for daily and manual backfill.
- `web/src/lib/schema.ts`: mirror the expanded persisted Figure contract.
- `web/src/lib/figures.ts`: resolve remote-backed and local-only panel actions.
- `web/src/components/FigurePreview.astro`: preserve the strict Figure 1-only home preview.
- `web/src/components/FigureGallery.astro`: show source-aware links and downloads.
- `web/src/lib/data.test.ts`, `web/src/lib/figures.test.ts`: frontend contract and source-resolution tests.
- `web/src/lib/presentation.test.ts`, `web/tests/site.spec.ts`: visible source labels and actions.
- `pyproject.toml`, `uv.lock`: add liberal-license PDF and test-PDF dependencies.
- `README.md`: document the fallback order, permanent cache, limits, and manual backfill.

### Task 1: Parse loose arXiv HTML Figure structures

**Files:**

- Create: `tests/fixtures/arxiv/figures-loose.html`
- Modify: `tests/test_figures.py`
- Modify: `src/vla_wam_daily/figures.py`

- [ ] **Step 1: Add the real-shape fixture and failing tests**

Add a fixture with the relevant arXiv structure:

```html
<div class="ltx_para">
  <img
    src="2607.26460v1/Figures/1.png"
    id="Sx1.p2.1.g1"
    class="ltx_graphics"
    alt="[Uncaptioned image]"
  >
</div>
<figure id="Sx1.F1" class="ltx_figure">
  <figcaption class="ltx_caption">
    <span class="ltx_tag ltx_tag_figure">Figure 1: </span>
    The Real Scene Performance of RLMM-Flow.
  </figcaption>
</figure>
```

Add tests asserting:

```python
gallery = parse_figure_gallery(loose_fixture, HTML_URL, CHECKED_AT)
assert gallery.figures[0].number == 1
assert gallery.figures[0].caption == "The Real Scene Performance of RLMM-Flow."
assert str(gallery.figures[0].image_urls[0]).endswith("/Figures/1.png")
assert str(gallery.figures[0].source_url) == f"{HTML_URL}#Sx1.F1"
```

Add separate tests proving the parser does not cross a heading, a text-bearing paragraph,
another `figure`, or a different parent container, and that an image inside the target
`figure` wins over a preceding sibling image.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
uv run pytest tests/test_figures.py -q
```

Expected: the loose-layout test returns `not_found` while all pre-existing parser tests pass.

- [ ] **Step 3: Implement the bounded sibling lookup**

Add a helper with this contract:

```python
def _figure_images(node: LexborNode, html_url: str) -> tuple[HttpUrl, ...]:
    """Return safe in-node images, or safe images from one adjacent empty wrapper."""
```

The helper must:

1. collect safe descendants from the Figure first;
2. only if empty, inspect exactly the immediately preceding element sibling;
3. reject that sibling if it is `figure`, a heading, or contains normalized visible text;
4. require at least one descendant `img`;
5. reuse `_resolve_current_paper_image()` and stable URL deduplication.

Do not broaden caption matching or URL allowlists.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_figures.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the parser fix**

```bash
git add tests/fixtures/arxiv/figures-loose.html tests/test_figures.py \
  src/vla_wam_daily/figures.py
git commit -m "fix: parse loose arXiv Figure 1 layouts"
```

### Task 2: Represent local-only recovered Figure panels

**Files:**

- Modify: `src/vla_wam_daily/models.py`
- Modify: `tests/factories.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write failing source and panel-contract tests**

Add tests for:

```python
FigureAsset(
    number=1,
    label="Figure 1",
    caption="Recovered from the paper PDF.",
    image_urls=(None,),
    cached_image_paths=(
        "/figures/2607.12345/v1/fig1-panel1.png",
    ),
    source_url="https://arxiv.org/pdf/2607.12345v1",
    source="arxiv_pdf",
)
```

Cover:

- `arxiv_source` with `https://arxiv.org/e-print/2607.12345v1`;
- source URL paper/version mismatches;
- `None` remote URL without a cached path;
- a panel with neither remote nor local source;
- array length mismatch;
- old HTML JSON without `cached_image_paths` normalizing to aligned `None` values;
- old Gallery JSON with Figure 1 normalizing recovery state to `available`;
- old Gallery JSON without Figure 1 normalizing recovery state to `not_attempted`;
- `fetch_failed` requiring `recovery_checked_at`;
- `available` requiring a real Figure 1.

- [ ] **Step 2: Run the model tests and verify RED**

Run:

```bash
uv run pytest tests/test_models.py -q
```

Expected: FAIL because `image_urls` rejects `None`, source only accepts `arxiv_html`, and recovery fields do not exist.

- [ ] **Step 3: Implement the backward-compatible contract**

Add:

```python
FigureImageTuple = Annotated[
    tuple[HttpUrl | None, ...],
    Field(min_length=1),
]


class FigureRecoveryStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    FETCH_FAILED = "fetch_failed"
```

Expand `FigureAsset.source` to:

```python
Literal["arxiv_html", "arxiv_source", "arxiv_pdf"]
```

Use a model-level validator to select the exact allowed source path:

```text
arxiv_html   /html/{id}v{version}#{anchor}
arxiv_source /e-print/{id}v{version}
arxiv_pdf    /pdf/{id}v{version}
```

Normalize missing/empty historical `cached_image_paths` to one `None` per
`image_urls` item. Require aligned non-empty arrays and require every panel to
have either a remote URL or a cached path. Only non-null HTML remote URLs pass
`validate_arxiv_image_url`.

Add these defaulted Gallery fields:

```python
recovery_status: FigureRecoveryStatus = FigureRecoveryStatus.NOT_ATTEMPTED
recovery_checked_at: UtcDatetime | None = None
```

Normalize historical Figure 1 galleries to `available`. Require
`available` only when Figure 1 exists; require a timestamp for `not_found` and
`fetch_failed`.

- [ ] **Step 4: Run the model and storage tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_models.py tests/test_storage.py -q
```

Expected: PASS, including old JSON fixture loading.

- [ ] **Step 5: Commit the data contract**

```bash
git add src/vla_wam_daily/models.py tests/factories.py tests/test_models.py
git commit -m "feat: support locally recovered Figure panels"
```

### Task 3: Safely extract an unambiguous Figure 1 from arXiv source

**Files:**

- Create: `src/vla_wam_daily/figure_source.py`
- Create: `tests/test_figure_source.py`
- Modify: `src/vla_wam_daily/figure_store.py`
- Modify: `tests/test_figure_store.py`

- [ ] **Step 1: Write failing safe-archive tests**

Create in-memory tar fixtures with `tarfile` and test:

- one main TeX file containing a first `figure` with one PNG;
- one main file using a bounded local `\input{sections/intro}`;
- path traversal, absolute paths, symlinks, hard links, devices;
- too many members, one oversized member, oversized total uncompressed bytes;
- missing `\documentclass`, missing caption, missing asset, ambiguous main files;
- TikZ/external-command Figure and multi-panel layout returning no candidate;
- HTTP redirect leaving the exact HTTPS arXiv e-print identity;
- 404 returning `None`, while 429/5xx/network failure raises a typed transient error.

Exercise this public interface:

```python
candidate = extractor.extract("2607.12345", 1)
assert candidate is not None
assert candidate.caption == "The model architecture."
assert candidate.source == "arxiv_source"
assert candidate.extension == "png"
assert candidate.content.startswith(b"\x89PNG")
```

- [ ] **Step 2: Run the source tests and verify RED**

Run:

```bash
uv run pytest tests/test_figure_source.py -q
```

Expected: collection FAIL because `vla_wam_daily.figure_source` does not exist.

- [ ] **Step 3: Implement bounded download and archive inspection**

Create immutable internal results:

```python
@dataclass(frozen=True)
class RecoveredFigure:
    caption: str
    extension: Literal["png", "jpg", "webp", "gif", "svg"]
    content: bytes
    source_url: str
    source: Literal["arxiv_source", "arxiv_pdf"]


class TransientRecoveryError(RuntimeError):
    pass
```

Create:

```python
class ArxivSourceFigureExtractor:
    def extract(self, arxiv_id: str, version: int) -> RecoveredFigure | None: ...
```

Bound compressed input, members, per-member bytes, total uncompressed bytes,
include depth, and TeX text bytes. Inspect members without extracting them to
disk. Reject every non-regular member except directories. Resolve asset names
only inside the logical archive root.

Find one main TeX file containing `\documentclass`, recursively inline only
literal local `\input`/`\include`, then identify the first unambiguous
`\begin{figure}...\end{figure}` block. Accept exactly one direct
`\includegraphics` asset and a nonblank `\caption`. Strip simple TeX commands
from the caption into plain text. Return `None` for macros, TikZ, multiple
assets, or unsupported image formats so the PDF layer owns complex layout.

- [ ] **Step 4: Add recovered-byte installation tests**

Add failing tests for:

```python
path = store.install_recovered_figure(
    arxiv_id="2607.12345",
    version=1,
    figure_number=1,
    panel=1,
    extension="png",
    content=png_bytes,
)
```

Assert deterministic paths, idempotent non-overwrite, maximum bytes, supported
extensions, atomic replacement cleanup, and symlink/path containment.

- [ ] **Step 5: Run the store tests and verify RED**

Run:

```bash
uv run pytest tests/test_figure_store.py -q
```

Expected: FAIL because `install_recovered_figure` does not exist.

- [ ] **Step 6: Implement the shared atomic installer**

Extract the existing temporary-file/fsync/replace sequence into one private
method used by both remote mirroring and recovered content. Validate
`arxiv_id`, version, Figure number, panel, extension, size, public directory,
and every target directory before writing. Existing non-empty valid targets
win and are returned unchanged.

- [ ] **Step 7: Run source and store tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_figure_source.py tests/test_figure_store.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit source recovery**

```bash
git add src/vla_wam_daily/figure_source.py src/vla_wam_daily/figure_store.py \
  tests/test_figure_source.py tests/test_figure_store.py
git commit -m "feat: recover Figure 1 from arXiv source"
```

### Task 4: Crop Figure 1 from the official PDF

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/vla_wam_daily/figure_pdf.py`
- Create: `tests/test_figure_pdf.py`

- [ ] **Step 1: Add PDF dependencies**

Add:

```toml
# project dependencies
"pdfplumber>=0.11.10,<0.12",

# dev dependencies
"reportlab>=4.4,<5",
```

Run:

```bash
uv lock
uv sync --frozen
```

Expected: Python 3.13-compatible `pdfplumber`, `pypdfium2`, Pillow, and
ReportLab packages install. `pypdfium2` is Apache-2.0/BSD-3-Clause and
pdfplumber/ReportLab use permissive licenses; do not add PyMuPDF.

- [ ] **Step 2: Write failing caption and crop-selection tests**

Use ReportLab and `BytesIO` to generate deterministic machine-readable PDFs:

```python
canvas.rect(72, 420, 468, 220, fill=1)
canvas.drawString(72, 392, "Figure 1: Deterministic fixture.")
```

Cover:

- `Figure 1:` and `Fig. 1.` caption variants;
- Figure 10 not matching Figure 1;
- unique image/rect/curve content directly above the caption;
- content below the caption, page header/footer, and a neighboring Figure 2
  excluded;
- two equally plausible regions returning `None`;
- caption without a plausible visual region returning `None`;
- a crop outside the page rejected;
- successful rendering returning a valid PNG and normalized caption;
- PDF 404 returning `None`, transient HTTP errors raising
  `TransientRecoveryError`, and unsafe redirects rejected.

Extend `tests/test_figure_source.py` with a source archive whose only
unambiguous `\includegraphics` asset is a single-page PDF. Assert that source
recovery renders that asset to PNG while retaining `source="arxiv_source"`.

- [ ] **Step 3: Run PDF tests and verify RED**

Run:

```bash
uv run pytest tests/test_figure_pdf.py -q
```

Expected: collection FAIL because `vla_wam_daily.figure_pdf` does not exist.

- [ ] **Step 4: Implement detection and rendering**

Create:

```python
class ArxivPdfFigureExtractor:
    def extract(self, arxiv_id: str, version: int) -> RecoveredFigure | None: ...
```

Bound PDF bytes, redirects, page count, and per-page object count. Use
pdfplumber words/objects to build caption lines and locate an exact Figure 1
caption. Generate visual candidates from images, rectangles, curves, and
lines that are above and horizontally overlap the caption. Merge nearby
objects, then accept only one candidate satisfying configured minimum area,
maximum caption distance, page-margin, and neighboring-caption rules.

Crop the accepted box with a small bounded padding and render at approximately
300 DPI through pdfplumber's pypdfium2-backed `to_image`. Save one PNG to
memory. Do not use OCR, full-page screenshots, or a “largest object” fallback.

Add one shared bounded PDF-to-PNG helper and use it from
`ArxivSourceFigureExtractor` when the unambiguous source asset is a
single-page PDF. Multi-page or invalid source assets return no source
candidate and proceed to full-paper PDF recovery.

- [ ] **Step 5: Run PDF tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_figure_pdf.py tests/test_figure_source.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit PDF recovery**

```bash
git add pyproject.toml uv.lock src/vla_wam_daily/figure_pdf.py \
  tests/test_figure_pdf.py
git commit -m "feat: recover Figure 1 from arXiv PDF"
```

### Task 5: Orchestrate recovery and permanent backfill

**Files:**

- Create: `src/vla_wam_daily/figure_recovery.py`
- Create: `tests/test_figure_recovery.py`
- Modify: `src/vla_wam_daily/figure_sync.py`
- Modify: `tests/test_figure_sync.py`
- Modify: `src/vla_wam_daily/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing fallback-order tests**

Use fakes for HTML, source, PDF, and store. Assert:

```text
existing Figure 1      → mirror only
missing Figure 1       → HTML refresh → mirror
HTML still missing     → source → install
source not found       → PDF → install
source transient error → PDF still attempted
all not found          → recovery_status=not_found
network/parser failure → recovery_status=fetch_failed
```

Also assert Figure 2 is preserved, recovered Figure 1 is inserted in sorted
order, HTML cached paths are preserved only when their remote URLs still
match, and a recovered asset has `(None,)` plus a valid local path.

- [ ] **Step 2: Run recovery tests and verify RED**

Run:

```bash
uv run pytest tests/test_figure_recovery.py -q
```

Expected: collection FAIL because `vla_wam_daily.figure_recovery` does not exist.

- [ ] **Step 3: Implement `FigureRecoveryService`**

Expose:

```python
class FigureRecoveryService:
    def recover_gallery(
        self,
        gallery: FigureGallery,
        *,
        checked_at: datetime,
    ) -> FigureGallery: ...
```

Determine paper identity from `gallery.html_url`. A successful local candidate
is installed through `ArxivFigureStore`, converted to a Figure 1
`FigureAsset`, merged with any Figure 2, and marked `available`. Mark a
definitive inspected miss `not_found`; mark exhausted transient/parse errors
`fetch_failed`. A failed source layer must not prevent the PDF layer.

Skip recovery when:

- Figure 1 already has a usable cached panel;
- `recovery_status` is `available` or `not_found`;
- `fetch_failed` is younger than 24 hours.

Always retain the original Gallery `checked_at` for HTML semantics and set
`recovery_checked_at` for fallback freshness.

- [ ] **Step 4: Write failing permanent-sync tests**

Extend `FigureSyncReport` expectations with:

```python
{
    "html_recovered": 0,
    "source_recovered": 0,
    "pdf_recovered": 0,
    "recovery_not_found": 0,
    "recovery_failed": 0,
}
```

Seed latest and historical records with missing Figure 1. Assert one recovery
call per `(arxiv_id, version)`, identical replacement in latest/monthly
archives/cache, source counters, partial failure isolation, and byte-for-byte
idempotency on a second run.

- [ ] **Step 5: Run sync tests and verify RED**

Run:

```bash
uv run pytest tests/test_figure_sync.py -q
```

Expected: FAIL because sync has no recovery dependency or counters.

- [ ] **Step 6: Integrate recovery into synchronization**

Change the protocol and entry point to:

```python
class FigureRecovery(Protocol):
    def recover_gallery(
        self,
        gallery: FigureGallery,
        *,
        checked_at: datetime,
    ) -> FigureGallery: ...


def synchronize_figure_assets(
    *,
    data_dir: Path,
    store: FigureStore,
    recovery: FigureRecovery,
    now: datetime,
) -> FigureSyncReport: ...
```

Recover each selected identity once, mirror any remaining remote panels, count
the final source transition, and reuse `_replace_galleries` plus
`save_figure_sync` for one atomic metadata update.

- [ ] **Step 7: Wire CLI lifecycle with failing then passing tests**

Write CLI tests proving both `daily` and `sync-figures` construct and close the
HTML client, source extractor, PDF extractor, store, and recovery service.
Then update `_run_figure_sync()` to use one `ExitStack`, pass
`datetime.now(UTC)`, and preserve current dry-run behavior.

Run:

```bash
uv run pytest tests/test_figure_recovery.py tests/test_figure_sync.py \
  tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit recovery orchestration**

```bash
git add src/vla_wam_daily/figure_recovery.py \
  src/vla_wam_daily/figure_sync.py src/vla_wam_daily/cli.py \
  tests/test_figure_recovery.py tests/test_figure_sync.py tests/test_cli.py
git commit -m "feat: orchestrate permanent Figure 1 recovery"
```

### Task 6: Render local-only and source-aware Figures

**Files:**

- Modify: `web/src/lib/schema.ts`
- Modify: `web/src/lib/data.test.ts`
- Modify: `web/src/lib/figures.ts`
- Modify: `web/src/lib/figures.test.ts`
- Modify: `web/src/components/FigurePreview.astro`
- Modify: `web/src/components/FigureGallery.astro`
- Modify: `web/src/lib/presentation.test.ts`
- Modify: `web/tests/site.spec.ts`
- Modify: `tests/fixtures/data/latest.json`
- Modify: `tests/fixtures/data/archive/2026-07.json`

- [ ] **Step 1: Write failing Zod and source-resolution tests**

Mirror the Python cases for nullable `image_urls`, aligned local paths,
source-specific URLs, recovery state defaults, and invalid paper/version
identities. Add:

```ts
resolveFigurePanelSource({
  originalUrl: null,
  cachedPath: "/figures/2607.12345/v1/fig1-panel1.png",
  basePath: "/vla-wam-daily/",
});
```

Assert it returns a local display/download URL and `originalUrl: null`. Reject
the case where both inputs are null.

- [ ] **Step 2: Run focused web tests and verify RED**

Run:

```bash
cd web
pnpm vitest run src/lib/data.test.ts src/lib/figures.test.ts
```

Expected: FAIL because the schema and resolver require a remote URL.

- [ ] **Step 3: Implement the frontend contract**

Change `image_urls` to an aligned array of trusted arXiv image URLs or `null`.
Validate `source_url` by `source`, normalize historical cached-path arrays, and
mirror all Python recovery invariants. Change:

```ts
export interface FigurePanelSource {
  displayUrl: string;
  downloadUrl: string;
  originalUrl: string | null;
  isLocal: boolean;
}
```

`resolveFigurePanelSource()` must prefer a validated cache, use the remote URL
only when present, and throw when neither exists. Local download filenames use
the cached extension without consulting a missing original.

- [ ] **Step 4: Write failing component/browser tests**

Assert:

- a source/PDF Figure 1 is visible on the home card;
- home still ignores Figure 2 when Figure 1 is absent;
- HTML shows “定位原文” and “查看 arXiv 原图”;
- source shows “来源：arXiv 源码包”;
- PDF shows “来源：PDF 自动裁剪”;
- local-only panels show “下载本站缓存” and “查看论文 PDF” but no remote-original action;
- the gallery error state can always reach the PDF.

- [ ] **Step 5: Run presentation and browser tests and verify RED**

Run:

```bash
cd web
pnpm vitest run src/lib/presentation.test.ts
pnpm test:e2e
```

Expected: FAIL on missing source-aware UI.

- [ ] **Step 6: Implement source-aware Astro rendering**

Iterate aligned panels by index, resolve local-only sources, and conditionally
render remote-original actions only when `originalUrl` is non-null. Keep the
Figure 1 lookup in `FigurePreview.astro`; do not replace it with “first
available Figure”. Add source labels and PDF/source links with existing safe
external-link attributes.

- [ ] **Step 7: Run focused web tests and verify GREEN**

Run:

```bash
cd web
pnpm test
pnpm test:e2e
```

Expected: PASS.

- [ ] **Step 8: Commit the frontend**

```bash
git add web/src tests/fixtures/data web/tests/site.spec.ts
git commit -m "feat: display recovered Figure 1 sources"
```

### Task 7: Document, backfill, and verify production behavior

**Files:**

- Modify: `README.md`
- Modify: `tests/test_docs.py`
- Modify: `data/cache/figures.json`
- Modify: `data/latest.json`
- Modify: `data/archive/*.json`
- Modify: `web/public/figures/**`

- [ ] **Step 1: Write the failing documentation test**

Require README to mention:

```text
arXiv HTML → arXiv 源码包 → arXiv PDF 自动裁剪
web/public/figures/{arxiv_id}/v{version}/
sync-figures
PDF 自动裁剪可能因置信不足而明确降级
```

- [ ] **Step 2: Run the docs test and verify RED**

Run:

```bash
uv run pytest tests/test_docs.py -q
```

Expected: FAIL because the fallback chain is undocumented.

- [ ] **Step 3: Update README and verify docs GREEN**

Document permanent storage, exact version identity, recovery retry semantics,
manual backfill, source labels, permissive PDF implementation dependencies,
and the rule that Figure 2 never impersonates Figure 1.

Run:

```bash
uv run pytest tests/test_docs.py -q
```

Expected: PASS.

- [ ] **Step 4: Run static and unit verification**

Run:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest --cov=vla_wam_daily --cov-report=term-missing
cd web
pnpm format:check
pnpm test
BASE_PATH=/ VLA_WAM_DATA_DIR=../tests/fixtures/data \
  VLA_WAM_PUBLIC_DIR=../tests/fixtures/public pnpm build
pnpm test:e2e
```

Expected: every command exits 0 with no new warnings.

- [ ] **Step 5: Backfill the real archive**

Run:

```bash
uv run vla-wam-daily sync-figures
```

Verify the report contains recovery counts and specifically inspect:

```text
2607.26460v1 → source=arxiv_html, Figure 1 cached
2606.00537v2 → source=arxiv_html, Figure 1 cached
2607.26769    → source=arxiv_source or arxiv_pdf, Figure 1 cached when unambiguous
2607.26567    → source=arxiv_source or arxiv_pdf, Figure 1 cached when unambiguous
```

If either final paper is `not_found`, inspect logs and the generated crop
candidate locally; do not lower confidence rules or commit a wrong image.

- [ ] **Step 6: Verify idempotency**

Snapshot generated file hashes, run `sync-figures` again, and assert:

```text
panels_mirrored=0
html_recovered=0
source_recovered=0
pdf_recovered=0
```

No tracked generated file may change on the second run.

- [ ] **Step 7: Inspect rendered cards and details**

Run the production build and local preview. Use the browser to verify desktop
and mobile home cards plus detail pages for the four target papers. Confirm
the crop is the real Figure 1, the label/caption/source are accurate, and
local downloads work.

- [ ] **Step 8: Commit docs and generated backfill**

```bash
git add README.md tests/test_docs.py data web/public/figures
git commit -m "data: backfill recovered Figure 1 assets"
```

- [ ] **Step 9: Final diff and clean-tree check**

Run:

```bash
git diff --check origin/main...HEAD
git status --short
```

Expected: diff check exits 0 and the working tree is clean.
