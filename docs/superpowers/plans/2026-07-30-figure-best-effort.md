# Figure 1 Best-Effort Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover real Figure 1 assets from common arXiv source/PDF layouts that the current conservative parser rejects.

**Architecture:** Keep the existing strict recovery first. Add a bounded best-effort source pass that relaxes only pre-figure semantic ambiguity while retaining literal single-image, archive, path and asset validation; make PDF text extraction tolerate geometry-less whitespace and caption punctuation variants. Bump the recovery rule version so persisted misses are retried, then backfill and verify the two real papers.

**Tech Stack:** Python 3.13, httpx, Pillow, pypdfium2, Pydantic, pytest, Astro, TypeScript, Playwright.

---

### Task 1: Add bounded best-effort source recovery

**Files:**

- Modify: `src/vla_wam_daily/figure_source.py`
- Modify: `tests/test_figure_source.py`

- [ ] **Step 1: Write failing tests**

Add a source archive whose main document contains harmless preamble macros and
a bundled `.cls`, then directly includes exactly one image and one caption in
the first `figure`. Assert `ArxivSourceFigureExtractor.extract()` returns that
asset. Retain tests proving multiple images, unsafe controls inside the figure,
path traversal, missing assets and ambiguous first figures return `None`.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_figure_source.py -q
```

Expected: the harmless macro/`.cls` case fails because the extractor returns
`None`.

- [ ] **Step 3: Implement the minimal fallback**

Add an `allow_preamble_ambiguity: bool = False` argument to `_extract_figure`.
The best-effort call may skip `_has_ambiguous_semantic_control()` before the
first figure, but must retain `_UNSAFE_FIGURE_RE`, literal one-image/one-caption
checks, safe local path resolution and all asset bounds. Try strict extraction
first, then this bounded fallback.

- [ ] **Step 4: Verify GREEN and commit**

```bash
uv run pytest tests/test_figure_source.py -q
uv run ruff check src tests
uv run mypy src
git add src/vla_wam_daily/figure_source.py tests/test_figure_source.py
git commit -m "fix: recover literal Figure 1 from common sources"
```

### Task 2: Make PDF Figure text tolerant without accepting random images

**Files:**

- Modify: `src/vla_wam_daily/figure_pdf.py`
- Modify: `tests/test_figure_pdf.py`

- [ ] **Step 1: Write failing tests**

Add one text-page fake where a normal space has a zero-area character box and
assert later caption characters are still parsed. Add a Figure 1 caption
without a colon or period and assert it can produce a unique crop. Keep the
existing ambiguity and full-page rejection tests.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/test_figure_pdf.py -q
```

Expected: zero-area whitespace empties the page lines and the punctuation-free
caption is not recognized.

- [ ] **Step 3: Implement minimal parsing changes**

When `_box(get_charbox())` is `None`, retain whitespace in the text buffer and
continue; still reject a geometry-less non-whitespace character. Permit either
caption punctuation or at least one separating whitespace after `Figure 1` /
`Fig. 1`. Do not change crop bounds, uniqueness, page-size or output limits.

- [ ] **Step 4: Verify GREEN and commit**

```bash
uv run pytest tests/test_figure_pdf.py -q
uv run ruff check src tests
uv run mypy src
git add src/vla_wam_daily/figure_pdf.py tests/test_figure_pdf.py
git commit -m "fix: tolerate common PDF Figure captions"
```

### Task 3: Retry old misses, backfill and publish

**Files:**

- Modify: `src/vla_wam_daily/figure_recovery.py`
- Modify: `tests/test_figure_recovery.py`
- Modify: `web/src/components/FigureGallery.astro`
- Modify: `web/src/lib/presentation.test.ts`
- Modify: `data/latest.json`
- Modify: `data/archive/2026-07.json`
- Modify: `data/cache/figures.json`
- Create: `web/public/figures/2607.26769/v1/fig1-panel1.*`
- Create: `web/public/figures/2607.26567/v1/fig1-panel1.*`

- [ ] **Step 1: Write failing recovery/message tests**

Assert the recovery rule version is `2`, an old version-1 `not_found` gallery is
retried, and the HTML-unavailable message says source/PDF recovery was also
attempted rather than claiming extraction is impossible.

- [ ] **Step 2: Verify RED, implement and verify GREEN**

Increment `FIGURE_RECOVERY_VERSION` to `2` and update the fallback wording.

```bash
uv run pytest tests/test_figure_recovery.py -q
cd web && pnpm vitest run src/lib/presentation.test.ts
```

- [ ] **Step 3: Backfill twice**

```bash
uv run vla-wam-daily sync-figures
uv run vla-wam-daily sync-figures
```

Require `2607.26769v1` and `2607.26567v1` to have real cached Figure 1 assets.
The second run must report zero recovery/mirroring changes and preserve hashes.

- [ ] **Step 4: Run all gates**

```bash
uv run pytest --cov=vla_wam_daily --cov-report=term-missing
uv run ruff check src tests
uv run mypy src
cd web
pnpm format:check
pnpm test
pnpm build
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" pnpm test:e2e
```

- [ ] **Step 5: Commit, push, merge and verify Pages**

```bash
git add src tests web data docs/superpowers
git commit -m "data: backfill best-effort Figure 1 assets"
git push -u origin codex/figure-best-effort
```

Create one ready PR, wait for CI, merge it, wait for Pages deployment, then
verify both paper pages and both cached image URLs return HTTP 200.
