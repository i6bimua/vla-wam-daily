# Simple Fig. 1 / Fig. 2 Recovery Implementation Plan

> **For Codex:** Execute in order with test-driven development. Keep the
> implementation narrow: ordinary `overpic`, two figure numbers, a larger PDF
> crop fallback, and cache invalidation.

**Goal:** Make papers such as `2607.28590v1` display and download locally
cached Figure 1 and Figure 2.

**Architecture:** Change fallback extractors to return numbered collections.
The recovery service fills missing Figure 1/2 slots in priority order:
HTML, source, PDF. Source extraction accepts literal `overpic` background
assets. PDF caption discovery uses `pdfplumber`, with a wider
caption-anchored crop when the existing object crop fails.

**Tech stack:** Python 3.13, Pydantic, httpx, Pillow, pypdfium2, pdfplumber,
pytest, Astro, Vitest, Playwright, GitHub Actions.

---

## Task 1: Create the isolated branch and prove the baseline

**Files:** No production changes.

1. Create `.worktrees/multi-figure-recovery` on
   `codex/multi-figure-recovery`.
2. Run:

   ```bash
   uv run pytest tests/test_figure_source.py tests/test_figure_pdf.py tests/test_figure_recovery.py -q
   ```

3. Run:

   ```bash
   cd web && pnpm test
   ```

Expected: all existing tests pass.

## Task 2: Add numbered multi-figure recovery tests and contract

**Files:**

- Modify: `src/vla_wam_daily/figure_recovery_types.py`
- Modify: `src/vla_wam_daily/figure_recovery.py`
- Modify: `tests/test_figure_recovery.py`

1. Add failing tests proving:
   - source can supply Figure 1 and Figure 2 in one result;
   - HTML Figure 1 is kept while source fills Figure 2;
   - PDF only fills numbers still missing after source;
   - installation uses `fig{number}-panel1`;
   - version-2 negative entries retry immediately;
   - version-3 `not_found` and `fetch_failed` retry after 24 hours.
2. Run the focused test and confirm it fails for the expected contract reason.
3. Add `number: Literal[1, 2]` to `RecoveredFigure`; make extractor results a
   tuple; merge unique missing numbers without overwriting existing figures.
4. Increment `FIGURE_RECOVERY_VERSION` to 3 and apply the same 24-hour rule to
   both negative statuses.
5. Run:

   ```bash
   uv run pytest tests/test_figure_recovery.py tests/test_figure_sync.py tests/test_cli.py -q
   ```

6. Commit:

   ```bash
   git commit -m "feat: recover numbered figure fallbacks"
   ```

## Task 3: Recover ordinary `overpic` Figure 1 and Figure 2

**Files:**

- Modify: `src/vla_wam_daily/figure_source.py`
- Modify: `tests/test_figure_source.py`

1. Add failing in-memory source archives containing:
   - two top-level figures backed by single-page PDF `overpic` assets;
   - a traversal path and a missing asset that must be rejected;
   - an ordinary `includegraphics` Figure 1 regression case.
2. Confirm the new tests fail because only `includegraphics` in the first
   figure is currently supported.
3. Parse the first two top-level figure blocks. Accept exactly one literal
   `includegraphics` or `overpic` background path and one caption per block.
   Do not execute overlay commands.
4. Return up to two numbered `RecoveredFigure` objects in document order.
5. Run:

   ```bash
   uv run pytest tests/test_figure_source.py tests/test_figure_recovery.py -q
   ```

6. Commit:

   ```bash
   git commit -m "feat: extract overpic figures from arxiv source"
   ```

## Task 4: Add simple PDF text and larger-crop fallback

**Files:**

- Modify: `src/vla_wam_daily/figure_pdf.py`
- Modify: `tests/test_figure_pdf.py`

1. Add failing generated-PDF tests proving:
   - caption words found by `pdfplumber` recover Figure 1 and Figure 2;
   - recovery still works when the precise visual-object candidate is absent;
   - the fallback crop is bounded and non-empty.
2. Confirm failure under the existing PDFium exact-character-count rule.
3. Build caption lines from `pdfplumber.page.extract_words()`.
4. Try the existing object crop first. If it fails, render a wider region
   above and through the matching caption, clipped to the page bounds.
5. Return up to two uniquely numbered results.
6. Run:

   ```bash
   uv run pytest tests/test_figure_pdf.py tests/test_figure_recovery.py -q
   ```

7. Commit:

   ```bash
   git commit -m "feat: add wide pdf figure crop fallback"
   ```

## Task 5: Correct unavailable wording and documentation

**Files:**

- Modify: `web/src/components/FigureGallery.astro`
- Modify: `web/src/lib/presentation.test.ts`
- Modify: `web/scripts/verify-figure-build.mjs`
- Modify: `web/tests/site.spec.ts`
- Modify: `README.md`
- Modify: `tests/test_docs.py`

1. Add or update failing assertions for the neutral message:
   `暂未从官方 HTML、源码包或 PDF 中恢复出可靠的 Fig. 1 / Fig. 2 面板。`
2. Update the component and README to describe two-number fallback, `overpic`,
   wider PDF crop, and 24-hour negative retry.
3. Run:

   ```bash
   uv run pytest tests/test_docs.py -q
   cd web && pnpm test && pnpm build && pnpm verify:figure-build
   ```

4. Commit:

   ```bash
   git commit -m "docs: clarify figure recovery fallback"
   ```

## Task 6: Full verification and review

1. Run:

   ```bash
   uv run ruff check src tests
   uv run mypy
   uv run pytest
   cd web
   pnpm format:check
   pnpm test
   pnpm build
   pnpm verify:figure-build
   pnpm verify:information-build
   pnpm verify:search-build
   pnpm test:e2e
   ```

2. Inspect the complete diff for unrelated files and unsafe path handling.
3. Commit any verification-only corrections.

## Task 7: Publish, backfill, and verify the real paper

1. Push `codex/multi-figure-recovery` and open a ready PR.
2. Verify GitHub Actions, merge the PR, and confirm no implementation PR is
   left open.
3. Run the figure sync workflow for all published records.
4. Verify `2607.28590v1` has:
   - Figure 1 and Figure 2 entries in data;
   - non-empty local panel files;
   - both figures on the detail page;
   - Figure 1 on its homepage card;
   - working local download links.
