# Permanent Figure Archive and Home Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permanently mirror Figure 1 / Figure 2 for every published paper, backfill the existing archive, and show Figure 1 directly on home-page paper cards with a smaller title.

**Architecture:** Extend the Figure contract with versioned local panel paths, implement a focused arXiv image store, and synchronize that store against all permanent monthly archives after each successful daily run. Astro prefers the local static asset and retains the canonical arXiv URL as a display, download, and attribution fallback.

**Tech Stack:** Python 3.13, Pydantic 2, httpx, Typer, pytest/respx, Astro 6, TypeScript, Zod 4, Vitest, Playwright, GitHub Actions, GitHub Pages.

---

## File Responsibility Map

- `src/vla_wam_daily/models.py`: validate aligned local Figure panel paths.
- `src/vla_wam_daily/figure_store.py`: validate, download, and atomically install arXiv image assets.
- `src/vla_wam_daily/figure_sync.py`: synchronize all archive/latest records and the Figure cache.
- `src/vla_wam_daily/storage.py`: load monthly archives and atomically persist a Figure synchronization result.
- `src/vla_wam_daily/cli.py`: expose `sync-figures` and run it after successful non-dry daily updates.
- `tests/test_models.py`: Python Figure contract behavior.
- `tests/test_figure_store.py`: network, content, path, and atomic-file behavior.
- `tests/test_figure_sync.py`: historical retention, idempotency, and partial-failure behavior.
- `tests/test_storage.py`: archive synchronization persistence boundary.
- `tests/test_cli.py`: command and daily integration behavior.
- `tests/test_workflows.py`: generated-path staging allowlist.
- `web/src/lib/schema.ts`: mirror the optional aligned local-path contract.
- `web/src/lib/figures.ts`: resolve local-first display and download sources under the Pages base path.
- `web/src/lib/figures.test.ts`: local-first and fallback URL behavior.
- `web/src/components/FigurePreview.astro`: immediate Figure 1 card preview.
- `web/src/components/PaperCard.astro`: two-column card lead and smaller-title structure.
- `web/src/components/FigureGallery.astro`: prefer cached panels while retaining arXiv originals.
- `web/src/styles/global.css`: responsive preview layout and exact title scale.
- `web/src/lib/presentation.test.ts`: static component and style contract.
- `web/tests/site.spec.ts`: visible home preview, mobile layout, and detail gallery behavior.
- `.github/workflows/daily.yml`: synchronize, validate, stage, and commit generated image files.
- `README.md`: describe permanent archive behavior, local Figure mirroring, attribution, and fallback.

### Task 1: Add the aligned local Figure path contract

**Files:**

- Modify: `src/vla_wam_daily/models.py`
- Modify: `tests/factories.py`
- Modify: `tests/test_models.py`
- Modify: `web/src/lib/schema.ts`
- Modify: `web/src/lib/data.test.ts`

- [ ] **Step 1: Write failing Python model tests**

Add tests that construct a Figure asset with:

```python
cached_image_paths=(
    "/figures/2607.12345/v1/fig1-panel1.png",
)
```

Assert that serialization preserves the path, existing payloads without the
field load with an empty tuple, and validation rejects:

```python
[
    ("/figures/2607.99999/v1/fig1-panel1.png", "wrong paper"),
    ("/figures/2607.12345/v2/fig1-panel1.png", "wrong version"),
    ("/figures/2607.12345/v1/fig2-panel1.png", "wrong figure"),
    ("/figures/2607.12345/v1/fig1-panel2.png", "wrong panel"),
    ("/figures/../../secret.png", "path traversal"),
    ("https://example.com/image.png", "absolute URL"),
]
```

Also assert that a non-empty `cached_image_paths` tuple must contain exactly
one entry per `image_urls` entry and may contain `None` for a failed panel.

- [ ] **Step 2: Run the Python model tests and verify RED**

Run:

```bash
uv run pytest tests/test_models.py -q
```

Expected: FAIL because `FigureAsset` rejects the new
`cached_image_paths` field.

- [ ] **Step 3: Implement the Python contract**

Add this path pattern and field:

```python
CACHED_FIGURE_PATH_PATTERN = re.compile(
    r"^/figures/(?P<arxiv_id>\d{4}\.\d{4,5})/"
    r"v(?P<version>[1-9]\d*)/"
    r"fig(?P<figure>[12])-panel(?P<panel>[1-9]\d*)"
    r"\.(?:png|jpg|webp|gif|svg)$"
)


class FigureAsset(FrozenStrictModel):
    # existing fields remain unchanged
    cached_image_paths: tuple[str | None, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_cached_image_paths(self) -> Self:
        if not self.cached_image_paths:
            return self
        if len(self.cached_image_paths) != len(self.image_urls):
            raise ValueError("cached Figure paths must align with image URLs")
        arxiv_id, version = parse_arxiv_html_identity(self.source_url)
        for panel, path in enumerate(self.cached_image_paths, start=1):
            if path is None:
                continue
            match = CACHED_FIGURE_PATH_PATTERN.fullmatch(path)
            if (
                match is None
                or match.group("arxiv_id") != arxiv_id
                or int(match.group("version")) != version
                or int(match.group("figure")) != self.number
                or int(match.group("panel")) != panel
            ):
                raise ValueError("cached Figure path does not match its panel")
        return self
```

Keep `cached_image_paths` empty in `make_gallery()` by default so old fixture
payloads continue to exercise backward compatibility.

- [ ] **Step 4: Run the Python model tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Write the failing TypeScript schema tests**

Extend the valid Figure fixture with:

```ts
cached_image_paths: [
  "/figures/2607.12345/v1/fig1-panel1.png",
],
```

Assert that old JSON without the field parses to `cached_image_paths: []`, and
that wrong paper, version, Figure number, panel number, traversal, and array
length are rejected.

- [ ] **Step 6: Run the web schema tests and verify RED**

Run:

```bash
cd web
pnpm vitest run src/lib/data.test.ts
```

Expected: FAIL because the strict Zod Figure schema rejects the new field.

- [ ] **Step 7: Implement the TypeScript schema contract**

Add:

```ts
const cachedFigurePathPattern =
  /^\/figures\/(\d{4}\.\d{4,5})\/v([1-9]\d*)\/fig([12])-panel([1-9]\d*)\.(png|jpg|webp|gif|svg)$/;
```

Add `cached_image_paths` with `.default([])` to `figureAssetSchema`. In its
`superRefine`, allow an empty array for historical payloads. Otherwise require
the same length as `image_urls`, allow `null`, and compare every non-null path
to the Figure source paper/version, Figure number, and one-based panel index.

- [ ] **Step 8: Run focused Python and web tests**

Run:

```bash
uv run pytest tests/test_models.py -q
cd web
pnpm vitest run src/lib/data.test.ts
```

Expected: both commands PASS.

- [ ] **Step 9: Commit the contract**

```bash
git add src/vla_wam_daily/models.py tests/factories.py tests/test_models.py \
  web/src/lib/schema.ts web/src/lib/data.test.ts
git commit -m "feat: add local figure asset contract"
```

### Task 2: Implement the safe arXiv Figure asset store

**Files:**

- Create: `src/vla_wam_daily/figure_store.py`
- Create: `tests/test_figure_store.py`

- [ ] **Step 1: Write failing deterministic-path and cache-hit tests**

Define tests for this public interface:

```python
with ArxivFigureStore(
    public_dir=tmp_path / "public",
    user_agent="VLA-WAM-Daily/test",
    client=httpx.Client(transport=transport),
) as store:
    result = store.mirror_gallery(make_gallery())
```

Assert a PNG response creates:

```text
public/figures/2607.12345/v1/fig1-panel1.png
```

and returns:

```python
result.figures[0].cached_image_paths == (
    "/figures/2607.12345/v1/fig1-panel1.png",
)
```

Call `mirror_gallery()` a second time with a transport that would fail if
called and assert the existing non-empty file is reused.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
uv run pytest tests/test_figure_store.py -q
```

Expected: collection FAIL because `vla_wam_daily.figure_store` does not exist.

- [ ] **Step 3: Implement deterministic local paths and lifecycle**

Create `ArxivFigureStore` with these exact public methods:

```text
ArxivFigureStore(
    *,
    public_dir: Path,
    user_agent: str,
    timeout_seconds: float = 30,
    max_image_bytes: int = 15_000_000,
    max_redirects: int = 3,
    client: httpx.Client | None = None,
)
store.mirror_gallery(gallery: FigureGallery) -> FigureGallery
store.close() -> None
store.__enter__() -> ArxivFigureStore
store.__exit__(
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    traceback: object,
) -> None
```

Use the Figure source URL to derive paper identity. Derive file names only from
validated identity, Figure number, one-based panel number, and validated media
type. Never use a remote path segment as a local filename.

The module-level media-type mapping is:

```python
MEDIA_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
}
```

- [ ] **Step 4: Run the focused happy-path tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_figure_store.py -q
```

Expected: the path and cache-hit tests PASS.

- [ ] **Step 5: Write failing network and content safety tests**

Add tests for:

- `404`, `429`, and `500` produce a `None` local panel without raising from
  `mirror_gallery()`;
- declared and streamed bodies above `max_image_bytes` are rejected;
- empty bodies are rejected;
- `text/html` and unknown image media types are rejected;
- redirects leaving HTTPS/arXiv, changing the versioned paper prefix, adding
  credentials, adding a fragment, or exceeding `max_redirects` are rejected;
- supported redirect targets on `arxiv.org` or `www.arxiv.org` succeed;
- partial download files are removed after a streaming exception;
- a parent symlink that escapes `public_dir` is rejected;
- a two-panel Figure can succeed for one panel and fall back for the other.

- [ ] **Step 6: Run the safety tests and verify RED**

Run:

```bash
uv run pytest tests/test_figure_store.py -q
```

Expected: FAIL on the first unimplemented safety case.

- [ ] **Step 7: Implement bounded streaming and atomic installation**

For each response:

1. request with `follow_redirects=False` and the configured User-Agent;
2. validate every redirect with `urlsplit`, the arXiv host allowlist, default
   HTTPS port, no credentials/fragment, and the original paper path prefix;
3. normalize `Content-Type` before looking it up in `MEDIA_EXTENSIONS`;
4. reject oversized declared content before reading;
5. create the version directory only after verifying it remains below the
   resolved `public_dir`;
6. stream into `tempfile.mkstemp()` in the target directory while counting
   bytes;
7. `flush()` and `os.fsync()` the temporary file;
8. reject a zero-byte body;
9. use `os.replace()` and fsync the parent directory;
10. remove the temporary file in `finally`.

Catch per-panel HTTP, validation, and filesystem failures inside
`mirror_gallery()`, log them with the paper/Figure/panel identity, and return
`None` for that local path. Do not discard other successful panels.

- [ ] **Step 8: Run all Figure store tests and static checks**

Run:

```bash
uv run pytest tests/test_figure_store.py -q
uv run ruff check src/vla_wam_daily/figure_store.py tests/test_figure_store.py
uv run mypy
```

Expected: all PASS.

- [ ] **Step 9: Commit the store**

```bash
git add src/vla_wam_daily/figure_store.py tests/test_figure_store.py
git commit -m "feat: mirror arxiv figure assets"
```

### Task 3: Synchronize permanent archives and Figure metadata

**Files:**

- Create: `src/vla_wam_daily/figure_sync.py`
- Create: `tests/test_figure_sync.py`
- Modify: `src/vla_wam_daily/storage.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Write failing archive loading and atomic-save tests**

Add tests for:

```python
archives = load_archives(data_dir)
save_figure_sync(
    data_dir,
    latest=latest,
    archives=archives,
    figure_cache=figure_cache,
)
```

Require `load_archives()` to accept only `YYYY-MM.json` regular files and
return a filename-keyed mapping of validated `DataFile` objects.

Require `save_figure_sync()` to:

- rewrite `latest.json`, every supplied archive, and
  `cache/figures.json`;
- preserve `generated_at`, statistics, and paper order;
- reject unsafe archive names and a record placed in the wrong publication
  month;
- leave every old file unchanged when serialization fails before the first
  write.

- [ ] **Step 2: Run storage tests and verify RED**

Run:

```bash
uv run pytest tests/test_storage.py -q
```

Expected: FAIL because `load_archives` and `save_figure_sync` do not exist.

- [ ] **Step 3: Implement archive loading and synchronized persistence**

Reuse `_open_data_root`, `_open_relative_directory`, `_read_text_at`,
`_open_save_directories`, `_serialize_json`, and `_atomic_write_text_at`.
Validate all inputs and serialize all outputs before writing any path.
`save_figure_sync()` must not alter analysis cache files.

- [ ] **Step 4: Run storage tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_storage.py -q
```

Expected: PASS.

- [ ] **Step 5: Write failing synchronization tests**

Define:

```python
result = synchronize_figure_assets(
    data_dir=data_dir,
    store=fake_store,
)
```

Cover:

- the same paper in latest and its monthly archive is mirrored once;
- all copies receive the same updated gallery;
- the matching `FigureCacheEntry` receives the updated gallery;
- an older paper outside the three-day window is still synchronized;
- already mirrored galleries make a second run idempotent;
- a failed panel leaves `None`, preserves the remote URL, and does not prevent
  other papers from being saved;
- an unavailable Figure gallery makes no network/store call;
- report counters distinguish papers scanned, panels reused, panels mirrored,
  and panels failed.

- [ ] **Step 6: Run synchronization tests and verify RED**

Run:

```bash
uv run pytest tests/test_figure_sync.py -q
```

Expected: collection FAIL because `figure_sync.py` does not exist.

- [ ] **Step 7: Implement archive synchronization**

Create immutable:

```python
class FigureSyncReport(FrozenStrictModel):
    papers_scanned: int = Field(ge=0)
    panels_reused: int = Field(ge=0)
    panels_mirrored: int = Field(ge=0)
    panels_failed: int = Field(ge=0)
```

Deduplicate work by `(arxiv_id, version)`, update records with
`model_copy(update={"figure_gallery": gallery})`, replace matching cache
entries with `FigureCacheEntry(key=key, gallery=gallery)`, and persist once
through `save_figure_sync()`.

- [ ] **Step 8: Run focused and static checks**

Run:

```bash
uv run pytest tests/test_storage.py tests/test_figure_sync.py -q
uv run ruff check src/vla_wam_daily/storage.py \
  src/vla_wam_daily/figure_sync.py tests/test_storage.py tests/test_figure_sync.py
uv run mypy
```

Expected: all PASS.

- [ ] **Step 9: Commit archive synchronization**

```bash
git add src/vla_wam_daily/storage.py src/vla_wam_daily/figure_sync.py \
  tests/test_storage.py tests/test_figure_sync.py
git commit -m "feat: synchronize archived figure assets"
```

### Task 4: Add the synchronization command and daily workflow

**Files:**

- Modify: `src/vla_wam_daily/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `.github/workflows/daily.yml`
- Modify: `tests/test_workflows.py`

- [ ] **Step 1: Write failing CLI tests**

Add runner tests for:

```text
vla-wam-daily sync-figures --data-dir DATA --public-dir PUBLIC
```

Assert:

- it does not require `DEEPSEEK_API_KEY`;
- it prints the serialized `FigureSyncReport`;
- invalid/missing data paths return a user-facing parameter error;
- the daily command invokes synchronization after a successful persisted run;
- `--dry-run` never creates or synchronizes Figure files.

- [ ] **Step 2: Run CLI tests and verify RED**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: FAIL because `sync-figures` is not registered.

- [ ] **Step 3: Implement the CLI**

Add `DEFAULT_PUBLIC_DIR = Path("web/public")` and:

```python
@app.command("sync-figures")
def sync_figures(
    data_dir: Annotated[Path, typer.Option()] = DEFAULT_DATA_DIR,
    public_dir: Annotated[Path, typer.Option()] = DEFAULT_PUBLIC_DIR,
) -> None:
    user_agent = os.getenv("ARXIV_USER_AGENT", DEFAULT_USER_AGENT)
    with ArxivFigureStore(
        public_dir=public_dir,
        user_agent=user_agent,
    ) as store:
        report = synchronize_figure_assets(data_dir=data_dir, store=store)
    typer.echo(report.model_dump_json())
```

Extract the shared synchronization call so `daily()` invokes it only after
`run_daily()` returns with `dry_run=False`.

- [ ] **Step 4: Run CLI tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 5: Write failing workflow contract tests**

Require `.github/workflows/daily.yml` to:

- run `vla-wam-daily sync-figures`;
- validate archive/cache loaders after synchronization;
- stage only `data` and `web/public/figures`;
- reject any staged path outside those prefixes;
- skip synchronization and file staging during a dry run.

- [ ] **Step 6: Run workflow tests and verify RED**

Run:

```bash
uv run pytest tests/test_workflows.py -q
```

Expected: FAIL because the workflow stages only `data`.

- [ ] **Step 7: Update the daily workflow**

Run synchronization after the pipeline report validation:

```bash
uv run vla-wam-daily sync-figures \
  --data-dir data \
  --public-dir web/public
```

only when `RUN_DRY_RUN == false`. Change staging to:

```bash
git add -- data web/public/figures
```

and accept only:

```bash
data/*|web/public/figures/*
```

in the staged-path guard.

- [ ] **Step 8: Run CLI and workflow tests**

Run:

```bash
uv run pytest tests/test_cli.py tests/test_workflows.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit command and workflow integration**

```bash
git add src/vla_wam_daily/cli.py tests/test_cli.py \
  .github/workflows/daily.yml tests/test_workflows.py
git commit -m "feat: sync figure assets in daily workflow"
```

### Task 5: Prefer local files in the full Figure gallery

**Files:**

- Create: `web/src/lib/figures.ts`
- Create: `web/src/lib/figures.test.ts`
- Modify: `web/src/components/FigureGallery.astro`
- Modify: `web/src/lib/presentation.test.ts`

- [ ] **Step 1: Write failing source-resolution tests**

Test:

```ts
resolveFigurePanelSource({
  originalUrl: "https://arxiv.org/html/2607.12345v1/x1.png",
  cachedPath: "/figures/2607.12345/v1/fig1-panel1.png",
  basePath: "/vla-wam-daily/",
});
```

Expected:

```ts
{
  displayUrl:
    "/vla-wam-daily/figures/2607.12345/v1/fig1-panel1.png",
  downloadUrl:
    "/vla-wam-daily/figures/2607.12345/v1/fig1-panel1.png",
  originalUrl:
    "https://arxiv.org/html/2607.12345v1/x1.png",
  isLocal: true,
}
```

Also test missing/null cached paths, root base paths, and rejection of cached
paths that do not start with `/figures/`.

- [ ] **Step 2: Run resolver tests and verify RED**

Run:

```bash
cd web
pnpm vitest run src/lib/figures.test.ts
```

Expected: collection FAIL because `figures.ts` does not exist.

- [ ] **Step 3: Implement the resolver**

Export:

```ts
export interface FigurePanelSource {
  displayUrl: string;
  downloadUrl: string;
  originalUrl: string;
  isLocal: boolean;
}

export function resolveFigurePanelSource(input: {
  originalUrl: string;
  cachedPath: string | null | undefined;
  basePath: string;
}): FigurePanelSource;
```

Normalize the Pages base path to one leading and trailing slash. Never treat an
external or traversal path as local.

- [ ] **Step 4: Run resolver tests and verify GREEN**

Run:

```bash
cd web
pnpm vitest run src/lib/figures.test.ts
```

Expected: PASS.

- [ ] **Step 5: Write failing Figure gallery presentation tests**

Require `FigureGallery.astro` to:

- pass each aligned cached path through `resolveFigurePanelSource`;
- render `<img src={source.displayUrl}>`;
- use an ordinary download link for local assets;
- retain a separate “查看 arXiv 原图” link using `source.originalUrl`;
- retain the current remote fetch/button fallback when `source.isLocal` is
  false;
- replace the old “本站不保存图像” notice with a local-cache/source-rights
  notice.

- [ ] **Step 6: Run presentation tests and verify RED**

Run:

```bash
cd web
pnpm vitest run src/lib/presentation.test.ts
```

Expected: FAIL on the local-source contract.

- [ ] **Step 7: Update the full gallery**

For each panel:

```ts
const source = resolveFigurePanelSource({
  originalUrl: imageUrl,
  cachedPath: figure.cached_image_paths[index],
  basePath: Astro.base,
});
```

Local download controls use:

```astro
<a href={source.downloadUrl} download={filename}>下载本站缓存</a>
```

Remote controls keep the existing guarded fetch/download button. Error UI
links to `source.originalUrl` and the PDF.

- [ ] **Step 8: Run Figure web tests**

Run:

```bash
cd web
pnpm vitest run src/lib/figures.test.ts src/lib/presentation.test.ts
pnpm format:check
```

Expected: all PASS.

- [ ] **Step 9: Commit local-first gallery behavior**

```bash
git add web/src/lib/figures.ts web/src/lib/figures.test.ts \
  web/src/components/FigureGallery.astro web/src/lib/presentation.test.ts
git commit -m "feat: prefer cached figure panels"
```

### Task 6: Show Figure 1 directly on home-page cards

**Files:**

- Create: `web/src/components/FigurePreview.astro`
- Modify: `web/src/components/PaperCard.astro`
- Modify: `web/src/styles/global.css`
- Modify: `web/src/lib/explorer-presentation.test.ts`
- Modify: `web/src/lib/presentation.test.ts`
- Modify: `web/tests/site.spec.ts`

- [ ] **Step 1: Write failing static presentation tests**

Require the card source to render `FigurePreview` before the expandable
analysis area for non-compact cards. Require the preview component to select
only Figure 1, use the first panel, call `resolveFigurePanelSource`, link to the
paper detail page, and render the caption as alt text.

Require CSS to contain:

```css
.paper-card h2 {
  font-size: clamp(1.35rem, 2.4vw, 2rem);
}
```

and a desktop two-column `.paper-card__lead` that collapses to one column in
the existing mobile media query.

- [ ] **Step 2: Run static presentation tests and verify RED**

Run:

```bash
cd web
pnpm vitest run \
  src/lib/presentation.test.ts \
  src/lib/explorer-presentation.test.ts
```

Expected: FAIL because no Figure preview component or smaller title exists.

- [ ] **Step 3: Implement FigurePreview**

The component receives:

```ts
interface Props {
  paper: Paper;
}
```

It selects `paper.figure_gallery.figures.find(({ number }) => number === 1)`.
When a panel exists, render a linked `<figure data-figure-preview>` with a
lazy, async-decoded image and visually compact `Fig. 1` caption. When no panel
exists, render an accessible neutral panel containing `Fig. 1 暂不可用`.

Use the local-first resolver and mark image failures by replacing the image
with the same neutral panel.

- [ ] **Step 4: Integrate the preview and responsive layout**

Move the existing title, original title, authors, and summary into the text
side of:

```astro
<div class="paper-card__lead">
  <div class="paper-card__text">
    <h2 id={titleId}>
      <a href={`${base}papers/${paper.arxiv_id}/`}>{paper.title_zh}</a>
    </h2>
    <p class="original-title" lang="en">{paper.title}</p>
    <p class="authors">{paper.authors.join(" · ")}</p>
    <p class="summary">{paper.analysis.one_sentence_summary}</p>
  </div>
  {!compact && <FigurePreview paper={paper} />}
</div>
```

Use `grid-template-columns: minmax(0, 1.15fr) minmax(16rem, 0.85fr)`, a bounded
preview aspect ratio, `object-fit: contain`, and the exact approved title
clamp. Keep full title wrapping and existing semantic heading/link behavior.

- [ ] **Step 5: Run static tests and verify GREEN**

Run:

```bash
cd web
pnpm vitest run \
  src/lib/presentation.test.ts \
  src/lib/explorer-presentation.test.ts
```

Expected: PASS.

- [ ] **Step 6: Write failing browser tests**

In the home-page browser flow, assert before opening `<details>`:

```ts
await expect(card.locator("[data-figure-preview]")).toBeVisible();
await expect(card.locator("[data-figure-preview] img")).toHaveAttribute(
  "src",
  /\/figures\/2607\.12345\/v1\/fig1-panel1\.(png|svg)$/,
);
```

At the mobile viewport, assert the preview bounding box begins below the title
block. On the detail page, assert Figure 1 and Figure 2 still render and the
arXiv original links remain present.

- [ ] **Step 7: Run browser tests and verify RED**

Run:

```bash
cd web
pnpm build
pnpm playwright test tests/site.spec.ts
```

Expected: FAIL until the fixture exposes a valid local Figure panel and the
layout is complete.

- [ ] **Step 8: Complete fixture and browser behavior**

Update the deterministic fixture Figure 1 with:

```json
"cached_image_paths": [
  "/figures/2607.12345/v1/fig1-panel1.svg"
]
```

Add a small inert SVG fixture at the matching `web/public/figures` path with a
viewBox and plain text `Figure 1 test fixture`; it contains no script, external
resource, event handler, or embedded HTML.

- [ ] **Step 9: Run all affected web checks**

Run:

```bash
cd web
pnpm test
pnpm format:check
pnpm build
pnpm verify:figure-build
pnpm test:e2e
```

Expected: all PASS.

- [ ] **Step 10: Commit the home preview**

```bash
git add web/src/components/FigurePreview.astro \
  web/src/components/PaperCard.astro web/src/styles/global.css \
  web/src/lib/explorer-presentation.test.ts \
  web/src/lib/presentation.test.ts web/tests/site.spec.ts \
  tests/fixtures/data web/public/figures/2607.12345
git commit -m "feat: show figure one on paper cards"
```

### Task 7: Backfill production Figure files and update documentation

**Files:**

- Modify: `README.md`
- Modify: `data/archive/*.json`
- Modify: `data/latest.json`
- Modify: `data/cache/figures.json`
- Create: `web/public/figures/**`

- [ ] **Step 1: Write failing documentation assertions**

Update `tests/test_docs.py` to require documentation that:

- three days is retrieval lookback, not retention;
- all published analyses and records are permanently archived;
- available Figure 1 / Figure 2 files are locally mirrored;
- canonical arXiv links and rights remain with authors/rightsholders;
- failed local mirrors use the arXiv source and retry later.

- [ ] **Step 2: Run documentation tests and verify RED**

Run:

```bash
uv run pytest tests/test_docs.py -q
```

Expected: FAIL on the old remote-only Figure wording.

- [ ] **Step 3: Update README**

Replace statements that the site never stores image bytes. Describe the
bounded published-paper mirror, deterministic paths, source attribution,
remote fallback, and repository-growth policy.

- [ ] **Step 4: Run documentation tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_docs.py -q
```

Expected: PASS.

- [ ] **Step 5: Run the production historical backfill**

Run:

```bash
ARXIV_USER_AGENT="VLA-WAM-Daily/0.1 (https://github.com/i6bimua/vla-wam-daily)" \
uv run vla-wam-daily sync-figures \
  --data-dir data \
  --public-dir web/public
```

Expected: JSON report with all current archive papers scanned, locally
available panels counted as mirrored or reused, and failures retained as
remote fallbacks.

- [ ] **Step 6: Validate the backfill is idempotent**

Run the same command again.

Expected: no Figure file content changes; previously successful panels count
as reused.

- [ ] **Step 7: Validate generated data and files**

Run:

```bash
uv run python - <<'PY'
from pathlib import Path
from vla_wam_daily.storage import load_archives, load_data_file, load_figure_cache

data_dir = Path("data")
latest = load_data_file(data_dir / "latest.json")
assert latest is not None
assert load_archives(data_dir)
load_figure_cache(data_dir)
assert any((Path("web/public") / path.lstrip("/")).is_file()
           for paper in latest.papers
           for figure in paper.figure_gallery.figures
           for path in figure.cached_image_paths
           if path is not None)
PY
```

Expected: exit code 0.

- [ ] **Step 8: Commit documentation and generated assets**

```bash
git add README.md tests/test_docs.py data web/public/figures
git commit -m "data: backfill permanent figure assets"
```

### Task 8: Full verification and live deployment

**Files:**

- No new source files.

- [ ] **Step 1: Run the complete Python verification**

```bash
uv run ruff check src tests
uv run mypy
uv run pytest --cov=vla_wam_daily --cov-report=term-missing
```

Expected: all PASS.

- [ ] **Step 2: Run the complete web verification**

```bash
cd web
pnpm test
pnpm format:check
pnpm build
pnpm verify:figure-build
pnpm verify:information-build
pnpm verify:search-build
pnpm test:e2e
```

Expected: all PASS.

- [ ] **Step 3: Check repository scope**

```bash
git diff --check origin/main...HEAD
git status --short
git log --oneline origin/main..HEAD
```

Expected: no whitespace errors, no unintended untracked files, and only the
planned commits.

- [ ] **Step 4: Push the completed default branch**

```bash
git push origin main
```

Expected: push succeeds.

- [ ] **Step 5: Verify GitHub Actions**

Require the `CI`, `Pages`, and any triggered daily-data workflow runs for the
new head commit to complete successfully. Inspect failed logs before making
any success claim.

- [ ] **Step 6: Verify the live site**

Open:

```text
https://i6bimua.github.io/vla-wam-daily/
```

Verify on desktop and mobile:

- home cards immediately show Figure 1;
- titles use the smaller scale;
- one locally mirrored image returns HTTP 200;
- the detail page shows Figure 1 and Figure 2;
- local download and arXiv original links work;
- a paper without Figure 1 shows the neutral fallback.

- [ ] **Step 7: Report evidence**

Report commit hashes, test counts, Actions run URLs, live-site URL, number of
papers/panels mirrored, any remaining remote-only failures, and repository
size impact.
