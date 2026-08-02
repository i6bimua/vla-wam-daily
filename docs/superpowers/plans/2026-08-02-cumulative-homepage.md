# Cumulative Homepage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the homepage display every current paper in the cumulative monthly archive while preserving the latest run timestamp and statistics.

**Architecture:** Keep the ingestion and persistence pipeline unchanged. At static-build time, `index.astro` loads `latest.json` for run metadata and the existing archive loader for the paper collection; the archive loader already deduplicates versions and applies the required newest-first ordering.

**Tech Stack:** Astro 6, TypeScript, Vitest, pnpm, existing JSON archive loader

---

### Task 1: Drive the homepage from the cumulative archive

**Files:**
- Modify: `web/src/lib/information-architecture.test.ts`
- Modify: `web/src/pages/index.astro`

- [ ] **Step 1: Write the failing homepage source contract**

Add this test to `web/src/lib/information-architecture.test.ts`:

```ts
describe("homepage collection contract", () => {
  it("renders the cumulative archive instead of the rolling latest snapshot", async () => {
    const page = await source("pages/index.astro");

    expect(page).toContain("loadLatestDataFile");
    expect(page).toContain("loadArchive");
    expect(page).toContain("const [latest, papers] = await Promise.all");
    expect(page).not.toContain("selectCurrentPapers(latest.papers)");
    expect(page).toContain("All research");
    expect(page).toContain("全部研究");
    expect(page).toContain("归档尚为空");
  });
});
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
cd web
pnpm vitest run src/lib/information-architecture.test.ts
```

Expected: FAIL because `index.astro` does not import or call `loadArchive()` and still derives `papers` from `latest.papers`.

- [ ] **Step 3: Implement the minimal cumulative homepage**

Change the homepage import and data loading to:

```ts
import { loadArchive, loadLatestDataFile } from "../lib/data";

const [latest, papers] = await Promise.all([
  loadLatestDataFile(),
  loadArchive(),
]);
```

Change the collection heading and empty state to:

```astro
<p class="eyebrow">All research</p>
<h2 id="latest-heading">全部研究</h2>
```

```astro
<p class="empty-state">
  归档尚为空；下一次每日任务发布论文后会自动更新。
</p>
```

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run:

```bash
cd web
pnpm vitest run src/lib/information-architecture.test.ts
```

Expected: all tests in `information-architecture.test.ts` pass.

- [ ] **Step 5: Verify the real-data homepage count**

Run:

```bash
cd web
BASE_PATH=/ VLA_WAM_DATA_DIR=../data VLA_WAM_PUBLIC_DIR=public pnpm build
archive_count=$(jq -s '[.[].papers[]] | group_by(.arxiv_id) | length' ../data/archive/*.json)
home_count=$(rg -o ' data-paper-card' dist/index.html | wc -l | tr -d ' ')
test "$home_count" = "$archive_count"
printf 'archive=%s homepage=%s\n' "$archive_count" "$home_count"
```

Expected: `archive=95 homepage=95` and exit status 0.

- [ ] **Step 6: Run complete frontend verification**

Run:

```bash
cd web
pnpm format:check
pnpm test
BASE_PATH=/ VLA_WAM_DATA_DIR=../tests/fixtures/data VLA_WAM_PUBLIC_DIR=../tests/fixtures/public pnpm build
BASE_PATH=/ pnpm verify:figure-build
BASE_PATH=/ pnpm verify:information-build
BASE_PATH=/ pnpm verify:search-build
BASE_PATH=/ PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' pnpm test:e2e
```

Expected: formatting succeeds, 212 Vitest tests pass, Astro reports zero errors and warnings, all static verifiers succeed, and 12 Playwright tests pass.

- [ ] **Step 7: Commit the implementation**

```bash
git add web/src/lib/information-architecture.test.ts web/src/pages/index.astro
git commit -m "fix: keep archived papers on homepage"
```

### Task 2: Publish and verify production

**Files:**
- No additional repository files

- [ ] **Step 1: Push the feature branch and open a pull request**

```bash
git push -u origin codex/cumulative-homepage
gh pr create --repo i6bimua/vla-wam-daily --base main --head codex/cumulative-homepage --title "Keep archived papers on the homepage" --body "Use the cumulative archive for homepage paper cards while retaining latest-run metadata."
```

- [ ] **Step 2: Wait for CI and merge the reviewed pull request**

```bash
gh pr checks --repo i6bimua/vla-wam-daily --watch --interval 10
gh pr merge --repo i6bimua/vla-wam-daily --squash --delete-branch
```

Expected: Python and web checks pass and the pull request reaches `MERGED` state.

- [ ] **Step 3: Verify Pages deployment and live cumulative count**

After the `Deploy Pages` workflow for the merge commit succeeds, run:

```bash
curl -fsSL https://i6bimua.github.io/vla-wam-daily/ | rg -o ' data-paper-card' | wc -l
curl -fsSL https://i6bimua.github.io/vla-wam-daily/archive/ | rg '共收录 95 篇当前版本论文'
```

Expected: the homepage prints `95` and the archive page confirms the same cumulative total.
