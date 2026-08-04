# Twice-Daily arXiv Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing GitHub Actions ingestion pipeline every day at 07:00 and 12:00 Beijing time.

**Architecture:** Add a second timezone-aware schedule entry to the existing daily workflow; both triggers continue through the same update, build, and deploy jobs. Preserve the three-day lookback, cumulative persistence, cache behavior, concurrency group, and manual dispatch interface.

**Tech Stack:** GitHub Actions YAML, Python/PyYAML workflow structure tests, pytest, Ruff, mypy

---

### Task 1: Specify the two Beijing-time schedules

**Files:**
- Modify: `tests/test_workflows.py:195`
- Modify: `tests/test_docs.py:133`

- [ ] **Step 1: Write the failing workflow assertion**

Replace the single-schedule assertion with:

```python
assert payload["on"]["schedule"] == [
    {"cron": "0 7 * * *", "timezone": "Asia/Shanghai"},
    {"cron": "0 12 * * *", "timezone": "Asia/Shanghai"},
]
```

- [ ] **Step 2: Write the failing documentation assertion**

Replace the README phrase checked by `test_readme_explains_pages_schedule_secrets_sources_and_troubleshooting`:

```python
"北京时间 07:00 和 12:00",
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
uv run pytest tests/test_workflows.py::test_daily_schedule_dispatch_defaults_and_permissions_are_bounded tests/test_docs.py::test_readme_explains_pages_schedule_secrets_sources_and_troubleshooting -q
```

Expected: both tests fail because the workflow has only the 07:00 schedule and the README does not contain the two-time phrase.

### Task 2: Add the noon catch-up trigger

**Files:**
- Modify: `.github/workflows/daily.yml:5`
- Modify: `README.md:220`
- Test: `tests/test_workflows.py`
- Test: `tests/test_docs.py`

- [ ] **Step 1: Add the second schedule entry**

Set the workflow schedule to:

```yaml
schedule:
  - cron: "0 7 * * *"
    timezone: "Asia/Shanghai"
  - cron: "0 12 * * *"
    timezone: "Asia/Shanghai"
```

- [ ] **Step 2: Document the two runs and catch-up purpose**

Change the troubleshooting entry to state:

```markdown
- **每日任务没有准点运行**：自动任务安排在北京时间 07:00 和 12:00，午间运行会补抓
  早间尚未同步的 arXiv 元数据。先确认工作流位于默认分支且 Actions 已启用；可用
  `workflow_dispatch` 手动补跑。GitHub schedule 不是实时调度器，排队延迟不代表失败。
```

- [ ] **Step 3: Run the focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_workflows.py::test_daily_schedule_dispatch_defaults_and_permissions_are_bounded tests/test_docs.py::test_readme_explains_pages_schedule_secrets_sources_and_troubleshooting -q
```

Expected: 2 passed.

- [ ] **Step 4: Commit the implementation**

```bash
git add .github/workflows/daily.yml README.md tests/test_workflows.py tests/test_docs.py
git commit -m "fix: add noon arXiv catch-up run"
```

### Task 3: Verify the repository

**Files:**
- Verify: `.github/workflows/daily.yml`
- Verify: `README.md`
- Verify: `tests/test_workflows.py`
- Verify: `tests/test_docs.py`

- [ ] **Step 1: Run static checks and the full Python suite**

Run:

```bash
uv run ruff check src tests
uv run mypy
uv run pytest
```

Expected: Ruff and mypy report success, and all pytest tests pass.

- [ ] **Step 2: Check formatting and repository cleanliness**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and no uncommitted files after the implementation commit.

### Task 4: Publish and catch up today's papers

**Files:**
- No local file changes

- [ ] **Step 1: Push the feature branch and open a ready pull request**

Push `codex/twice-daily-schedule`, open a PR against `main`, and include the focused and full verification results.

- [ ] **Step 2: Wait for CI and merge**

Require both Python and web checks to pass, then squash-merge the PR and delete the remote feature branch.

- [ ] **Step 3: Dispatch a non-dry-run daily update**

Run the merged `Daily arXiv Update` workflow on `main` with its default three-day lookback, quality profile, threshold 6, no forced ID, and `dry_run=false`.

- [ ] **Step 4: Verify catch-up and deployment**

Confirm that the update report has a nonzero fetched count, the workflow commits any new qualifying papers, the Pages deployment succeeds, and the live site displays the new cumulative paper count without losing archived papers.
