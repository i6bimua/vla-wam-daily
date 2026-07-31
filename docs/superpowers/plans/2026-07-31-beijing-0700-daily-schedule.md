# Beijing 07:00 Daily Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the production daily refresh at 07:00 Asia/Shanghai, retain manual dispatch, and trigger one production refresh for 2026-07-31 after deployment.

**Architecture:** Keep the existing `Daily arXiv Update` workflow and change only its schedule trigger to a timezone-aware GitHub Actions cron entry. Protect the trigger contract with existing workflow tests, synchronize current user-facing documentation, then merge before manually dispatching the production workflow so the refresh runs from the updated default branch.

**Tech Stack:** GitHub Actions YAML, PyYAML workflow tests, pytest, GitHub CLI

---

## File Map

- Modify `.github/workflows/daily.yml`: set the sole daily schedule to 07:00 Asia/Shanghai.
- Modify `tests/test_workflows.py`: assert the exact cron and timezone while retaining existing dispatch and permission assertions.
- Modify `tests/test_docs.py`: require the current README to state Beijing 07:00.
- Modify `README.md`: document the timezone-aware schedule and normal GitHub queue delay.
- Modify `docs/superpowers/specs/2026-07-27-vla-wam-daily-design.md`: synchronize the current product design's success criterion and workflow section.

### Task 1: Add Failing Schedule and Documentation Tests

**Files:**
- Modify: `tests/test_workflows.py:195`
- Modify: `tests/test_docs.py:133`

- [ ] **Step 1: Change the workflow contract assertion**

Replace the schedule assertion in
`test_daily_schedule_dispatch_defaults_and_permissions_are_bounded` with:

```python
assert payload["on"]["schedule"] == [
    {"cron": "0 7 * * *", "timezone": "Asia/Shanghai"}
]
```

Keep all existing assertions for `workflow_dispatch`, input defaults, branch guards, and
permissions unchanged.

- [ ] **Step 2: Change the README schedule assertion**

In `test_readme_explains_pages_schedule_secrets_sources_and_troubleshooting`, replace:

```python
"北京时间 10:30",
```

with:

```python
"北京时间 07:00",
"Asia/Shanghai",
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
uv run pytest \
  tests/test_workflows.py::test_daily_schedule_dispatch_defaults_and_permissions_are_bounded \
  tests/test_docs.py::test_readme_explains_pages_schedule_secrets_sources_and_troubleshooting \
  -q
```

Expected: two assertion failures because the workflow still contains
`{"cron": "30 2 * * *"}` and README still contains `北京时间 10:30`.

- [ ] **Step 4: Commit the failing tests**

```bash
git add tests/test_workflows.py tests/test_docs.py
git commit -m "test: require 07:00 Beijing daily refresh"
```

### Task 2: Implement the Timezone-Aware Schedule

**Files:**
- Modify: `.github/workflows/daily.yml:5`

- [ ] **Step 1: Replace the schedule entry**

Set the trigger block to:

```yaml
on:
  schedule:
    - cron: "0 7 * * *"
      timezone: "Asia/Shanghai"
  workflow_dispatch:
```

Do not change the existing `workflow_dispatch.inputs`, permissions, concurrency, jobs,
or command arguments.

- [ ] **Step 2: Run the workflow contract test and verify GREEN**

Run:

```bash
uv run pytest \
  tests/test_workflows.py::test_daily_schedule_dispatch_defaults_and_permissions_are_bounded \
  -q
```

Expected: one test passes.

- [ ] **Step 3: Commit the workflow change**

```bash
git add .github/workflows/daily.yml
git commit -m "ci: refresh daily at 07:00 Beijing time"
```

### Task 3: Synchronize Current Documentation

**Files:**
- Modify: `README.md:139`
- Modify: `docs/superpowers/specs/2026-07-27-vla-wam-daily-design.md:14`
- Modify: `docs/superpowers/specs/2026-07-27-vla-wam-daily-design.md:319`

- [ ] **Step 1: Update README**

Replace the current schedule paragraph with:

```markdown
`.github/workflows/daily.yml` 使用 `0 7 * * *` 和 `Asia/Shanghai` 时区，即每天
北京时间 07:00。GitHub 的定时任务可能因平台排队稍晚开始。非 dry-run 成功后，工作流
```

Leave the remainder of the paragraph unchanged.

- [ ] **Step 2: Update the current product design**

Change the success criterion to:

```markdown
- 每天北京时间 07:00 自动运行，也能手动运行。
```

Change the `daily.yml` section to:

```markdown
在 `0 7 * * *`、`Asia/Shanghai` 时区（北京时间 07:00）运行，也支持
`workflow_dispatch`。
```

- [ ] **Step 3: Run the focused documentation test and verify GREEN**

Run:

```bash
uv run pytest \
  tests/test_docs.py::test_readme_explains_pages_schedule_secrets_sources_and_troubleshooting \
  -q
```

Expected: one test passes.

- [ ] **Step 4: Check current documentation for stale production time**

Run:

```bash
rg -n "北京时间 10:30|30 2 \\* \\* \\*" \
  README.md docs/superpowers/specs/2026-07-27-vla-wam-daily-design.md \
  .github/workflows/daily.yml
```

Expected: no matches.

- [ ] **Step 5: Commit the documentation**

```bash
git add README.md docs/superpowers/specs/2026-07-27-vla-wam-daily-design.md
git commit -m "docs: document 07:00 Beijing refresh"
```

### Task 4: Verify and Publish the Schedule Change

**Files:**
- Verify all modified files from Tasks 1–3.

- [ ] **Step 1: Run workflow and documentation tests**

```bash
uv run pytest tests/test_workflows.py tests/test_docs.py -q
```

Expected: all selected tests pass with zero failures.

- [ ] **Step 2: Run formatting and static checks**

```bash
uv run ruff check tests/test_workflows.py tests/test_docs.py
git diff --check origin/main...HEAD
```

Expected: every command exits 0 with no errors.

- [ ] **Step 3: Confirm the final trigger contract**

```bash
uv run python - <<'PY'
from pathlib import Path
import yaml

payload = yaml.load(
    Path(".github/workflows/daily.yml").read_text(encoding="utf-8"),
    Loader=yaml.BaseLoader,
)
assert payload["on"]["schedule"] == [
    {"cron": "0 7 * * *", "timezone": "Asia/Shanghai"}
]
assert "workflow_dispatch" in payload["on"]
print("daily schedule: 07:00 Asia/Shanghai; manual dispatch retained")
PY
```

Expected:

```text
daily schedule: 07:00 Asia/Shanghai; manual dispatch retained
```

- [ ] **Step 4: Push, open a PR, wait for CI, and merge**

```bash
git push -u origin codex/beijing-0700-schedule
gh pr create \
  --base main \
  --head codex/beijing-0700-schedule \
  --title "ci: refresh daily at 07:00 Beijing time" \
  --body "Change the production daily schedule to 07:00 Asia/Shanghai while retaining manual dispatch."
gh pr checks codex/beijing-0700-schedule --watch
gh pr merge codex/beijing-0700-schedule --squash --delete-branch
merged_oid="$(
  gh pr view codex/beijing-0700-schedule --json mergeCommit --jq '.mergeCommit.oid'
)"
git fetch origin main
git merge-base --is-ancestor "$merged_oid" origin/main
```

Expected: the push, PR checks, squash merge, fetch, and ancestry check all exit 0.

### Task 5: Trigger and Verify the 2026-07-31 Production Refresh

**Files:**
- No repository file changes expected; this task operates the deployed GitHub workflow.

- [ ] **Step 1: Dispatch the production workflow from `main`**

```bash
dispatch_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
gh workflow run daily.yml \
  --ref main \
  -f lookback_days=3 \
  -f profile=quality \
  -f threshold=6 \
  -f dry_run=false
```

Expected: GitHub accepts one `workflow_dispatch` event using the configured DeepSeek
quality profile and the default three-day lookback.

- [ ] **Step 2: Identify only the newly dispatched run**

```bash
daily_run_id="$(
  gh run list \
  --workflow daily.yml \
  --event workflow_dispatch \
  --branch main \
  --limit 5 \
  --json databaseId,createdAt,status,conclusion,url |
  jq -r --arg started "$dispatch_started_at" \
    '[.[] | select(.createdAt >= $started)] | sort_by(.createdAt) | last | .databaseId'
)"
test -n "$daily_run_id"
test "$daily_run_id" != "null"
printf 'daily_run_id=%s\n' "$daily_run_id"
```

Select the newest run created after the recorded dispatch time. Do not rerun if the first
dispatch was accepted but is queued.

- [ ] **Step 3: Wait for the daily workflow**

```bash
gh run watch "$daily_run_id" --exit-status
```

Expected: the `update`, `build`, and `deploy` jobs complete successfully. If GitHub reports
an actual failure, inspect that run's failed job logs before deciding whether a retry is
safe.

- [ ] **Step 4: Verify deployment and current data**

```bash
curl -L -fsS -o /dev/null -w "home=%{http_code}\n" \
  https://i6bimua.github.io/vla-wam-daily/
gh run view "$daily_run_id" --json status,conclusion,url
```

Expected: homepage HTTP 200 and the selected run reports `completed` with `success`.
