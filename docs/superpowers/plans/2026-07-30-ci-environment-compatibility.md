# CI Environment Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Python CLI assertions and Playwright preview startup pass reliably on GitHub Actions without changing production behavior or adding dependencies.

**Architecture:** Normalize only captured test text at the assertion boundary with the ANSI utility in Typer's built-in Click compatibility layer. Start Playwright's already-built static site through Astro's declared CLI instead of reaching through Astro to an undeclared Vite executable.

**Tech Stack:** Python 3.13, pytest, Click/Typer, TypeScript, Vitest, Playwright, Astro, pnpm, GitHub Actions

---

## File Map

- `tests/test_cli.py`: owns CLI test invocation helpers and output assertions; add ANSI normalization and its regression test here.
- `web/src/lib/playwright-config.test.ts`: specifies the preview command, URL, isolation, and browser-selection contract.
- `web/playwright.config.ts`: constructs the actual Playwright preview process and base URL.
- `.github/workflows/ci.yml`: remains unchanged; it is the hosted acceptance environment for the fix.

### Task 1: Make CLI assertions independent of ANSI styling

**Files:**
- Modify: `tests/test_cli.py:1-17`
- Modify: `tests/test_cli.py:195-229`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing ANSI-normalization regression test**

Add this test above `assert_parameter_error` without defining
`plain_cli_text` yet:

```python
def test_plain_cli_text_removes_split_ansi_styles() -> None:
    styled = "\x1b[36m--\x1b[0m\x1b[36mprofile\x1b[0m"

    assert plain_cli_text(styled) == "--profile"
```

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```bash
uv run pytest tests/test_cli.py::test_plain_cli_text_removes_split_ansi_styles -q
```

Expected: FAIL with `NameError: name 'plain_cli_text' is not defined`.

- [ ] **Step 3: Implement the minimal normalization helper**

Add the Typer compatibility-layer import:

```python
from typer._click.utils import strip_ansi
```

Add this helper above the regression test:

```python
def plain_cli_text(value: str) -> str:
    return strip_ansi(value)
```

- [ ] **Step 4: Use normalized text for semantic assertions**

Update `assert_parameter_error` so only the semantic option lookup uses
normalized text. Keep traceback and secret checks against the original streams:

```python
def assert_parameter_error(result: Any, option: str) -> None:
    stderr = plain_cli_text(result.stderr)
    assert result.exit_code == 2
    assert option in stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr
```

Update the daily-help test to normalize its semantic output once:

```python
    result = RUNNER.invoke(cli_module.app, ["daily", "--help"])
    stdout = plain_cli_text(result.stdout)

    assert result.exit_code == 0
    for option in (
        "--profile",
        "--lookback-days",
        "--threshold",
        "--force-arxiv-id",
        "--dry-run",
        "--config-path",
        "--data-dir",
        "--prompt-path",
    ):
        assert option in stdout
```

- [ ] **Step 5: Verify GREEN for focused and complete CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_plain_cli_text_removes_split_ansi_styles -q
uv run pytest tests/test_cli.py -q
```

Expected: the regression test passes, then all CLI tests pass.

- [ ] **Step 6: Commit the CLI compatibility fix**

```bash
git add tests/test_cli.py
git commit -m "test: normalize ANSI CLI output"
```

### Task 2: Start E2E preview through Astro's declared CLI

**Files:**
- Modify: `web/src/lib/playwright-config.test.ts:76-91`
- Modify: `web/playwright.config.ts:62-67`
- Test: `web/src/lib/playwright-config.test.ts`
- Test: `web/tests/*.spec.ts`

- [ ] **Step 1: Change the Playwright contract test first**

Replace the preview-server test with:

```typescript
  it("uses the declared Astro preview CLI for one isolated URL", async () => {
    const config = await loadConfig({
      BASE_PATH: "/vla-wam-daily/",
      PLAYWRIGHT_PORT: "4567",
    });

    expect(config.use?.baseURL).toBe("http://127.0.0.1:4567/vla-wam-daily/");
    expect(config.webServer).toMatchObject({
      command:
        "pnpm exec astro preview --host 127.0.0.1 --port 4567",
      reuseExistingServer: false,
      url: config.use?.baseURL,
    });
    expect(config.webServer?.command).not.toContain("pnpm exec vite");
    expect(config.outputDir).toContain("4567");
    expect(config.outputDir).toContain("vla-wam-daily");
  });
```

- [ ] **Step 2: Run the configuration test and verify RED**

Run:

```bash
cd web
pnpm exec vitest run src/lib/playwright-config.test.ts
```

Expected: FAIL because the actual command still begins with
`pnpm exec vite preview`.

- [ ] **Step 3: Implement the minimal Astro Preview command**

In `web/playwright.config.ts`, replace only the `command` value:

```typescript
  webServer: {
    command: `pnpm exec astro preview --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 30_000,
  },
```

Keep the existing validated port, `baseURL`, temporary output directory,
browser selection, and no-reuse policy unchanged. Astro reads `BASE_PATH`
through `astro.config.mjs`.

- [ ] **Step 4: Verify GREEN for configuration and real browser flow**

Run from `web/`:

```bash
pnpm exec vitest run src/lib/playwright-config.test.ts
VLA_WAM_DATA_DIR=../tests/fixtures/data BASE_PATH=/ pnpm build
CI=true BASE_PATH=/ pnpm test:e2e
```

Expected: 18 Playwright-configuration tests pass, the fixture site builds, and
all 11 browser-flow tests pass.

- [ ] **Step 5: Verify GitHub project-base behavior locally**

Run from `web/`:

```bash
VLA_WAM_DATA_DIR=../tests/fixtures/data BASE_PATH=/vla-wam-daily/ pnpm build
CI=true BASE_PATH=/vla-wam-daily/ PLAYWRIGHT_PORT=49201 pnpm test:e2e
```

Expected: the project-base site builds and all 11 browser-flow tests pass.

- [ ] **Step 6: Commit the E2E startup fix**

```bash
git add web/playwright.config.ts web/src/lib/playwright-config.test.ts
git commit -m "test: run E2E preview through Astro"
```

### Task 3: Run complete acceptance gates

**Files:**
- Verify: `src/`
- Verify: `tests/`
- Verify: `web/`
- Verify: `.github/workflows/`

- [ ] **Step 1: Run the complete Python gate**

Run from the repository root:

```bash
uv sync --frozen
uv run ruff check src tests
uv run mypy
uv run pytest --cov=vla_wam_daily --cov-report=term-missing
```

Expected: dependency lock is unchanged, lint and type checks pass, all Python
tests pass, and total coverage remains at least 95%.

- [ ] **Step 2: Run the complete web gate**

Run:

```bash
cd web
pnpm install --frozen-lockfile
pnpm test
pnpm format:check
pnpm audit --audit-level high
VLA_WAM_DATA_DIR=../tests/fixtures/data BASE_PATH=/ pnpm build
BASE_PATH=/ pnpm verify:figure-build
BASE_PATH=/ pnpm verify:information-build
BASE_PATH=/ pnpm verify:search-build
CI=true BASE_PATH=/ PLAYWRIGHT_PORT=49200 pnpm test:e2e
```

Expected: 162 or more unit tests pass, formatting passes, there are no
high/critical audit findings, all three build verifiers pass, and all 11 E2E
tests pass.

- [ ] **Step 3: Run repository integrity checks**

Run from the repository root:

```bash
git diff --check
git status --short --branch
```

Expected: no whitespace errors, a clean working tree, and only the design,
plan, and two focused fix commits ahead of `origin/main`.

### Task 4: Publish and verify the hosted fix

**Files:**
- Publish: current `main`
- Verify: `.github/workflows/ci.yml`
- Verify: `.github/workflows/pages.yml`

- [ ] **Step 1: Push the reviewed commits**

Run:

```bash
git push origin main
```

Expected: `main` advances on `i6bimua/vla-wam-daily`.

- [ ] **Step 2: Wait for the new CI run**

Run:

```bash
ci_run_id=$(gh run list --repo i6bimua/vla-wam-daily --workflow CI --branch main --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$ci_run_id" --repo i6bimua/vla-wam-daily --exit-status
```

Expected: both `python` and `web` jobs complete successfully.

- [ ] **Step 3: Wait for Pages and check public resources**

Run:

```bash
pages_run_id=$(gh run list --repo i6bimua/vla-wam-daily --workflow "Deploy Pages" --branch main --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$pages_run_id" --repo i6bimua/vla-wam-daily --exit-status
curl --fail --location --output /dev/null https://i6bimua.github.io/vla-wam-daily/
curl --fail --location --output /dev/null https://i6bimua.github.io/vla-wam-daily/rss.xml
curl --fail --location --output /dev/null https://i6bimua.github.io/vla-wam-daily/pagefind/pagefind.js
```

Expected: the Pages run for the pushed commit succeeds and all three public
resources return HTTP 200.
