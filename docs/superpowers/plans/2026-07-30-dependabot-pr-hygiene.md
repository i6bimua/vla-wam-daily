# Dependabot Pull-Request Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current flood of individual failing Dependabot pull requests with one controlled compatible-update group per ecosystem and close the obsolete bot PRs.

**Architecture:** Keep monthly updates, group minor/patch changes by ecosystem, cap each ecosystem at one open PR, and suppress automatic Python/frontend major upgrades. Validate the policy as repository configuration before closing only the nine known Dependabot-authored PRs.

**Tech Stack:** Dependabot v2 configuration, pytest, GitHub pull-request API.

---

## File Responsibility Map

- `.github/dependabot.yml`: grouped update policy and open-PR limits.
- `tests/test_workflows.py`: parsed configuration contract.
- GitHub PRs `#1`–`#9`: obsolete individual Dependabot proposals to close.

### Task 1: Configure grouped compatible updates

**Files:**

- Modify: `.github/dependabot.yml`
- Modify: `tests/test_workflows.py`

- [ ] **Step 1: Write the failing policy test**

Parse `.github/dependabot.yml` and require:

```python
expected = {
    ("uv", "/"): {
        "limit": 1,
        "group": "python-compatible",
        "ignore_major": True,
    },
    ("npm", "/web"): {
        "limit": 1,
        "group": "frontend-compatible",
        "ignore_major": True,
    },
    ("github-actions", "/"): {
        "limit": 1,
        "group": "actions-compatible",
        "ignore_major": False,
    },
}
```

Each group must use `patterns: ["*"]` and
`update-types: ["minor", "patch"]`. The uv and npm entries must ignore
`version-update:semver-major`.

- [ ] **Step 2: Run the policy test and verify RED**

Run:

```bash
uv run pytest tests/test_workflows.py -q
```

Expected: FAIL because the current file has neither groups nor limits.

- [ ] **Step 3: Implement the Dependabot policy**

Use this structure:

```yaml
version: 2
updates:
  - package-ecosystem: uv
    directory: /
    schedule:
      interval: monthly
    open-pull-requests-limit: 1
    groups:
      python-compatible:
        patterns: ["*"]
        update-types: [minor, patch]
    ignore:
      - dependency-name: "*"
        update-types: ["version-update:semver-major"]

  - package-ecosystem: npm
    directory: /web
    schedule:
      interval: monthly
    open-pull-requests-limit: 1
    groups:
      frontend-compatible:
        patterns: ["*"]
        update-types: [minor, patch]
    ignore:
      - dependency-name: "*"
        update-types: ["version-update:semver-major"]

  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: monthly
    open-pull-requests-limit: 1
    groups:
      actions-compatible:
        patterns: ["*"]
        update-types: [minor, patch]
```

- [ ] **Step 4: Run policy and formatting checks**

Run:

```bash
uv run pytest tests/test_workflows.py -q
git diff --check
```

Expected: PASS.

- [ ] **Step 5: Commit and push the policy**

```bash
git add .github/dependabot.yml tests/test_workflows.py
git commit -m "chore: group dependabot updates"
git push origin main
```

Expected: commit and push succeed before any PR is closed.

### Task 2: Close only the obsolete Dependabot PRs

**Files:**

- No local file changes.

- [ ] **Step 1: Re-read the open PR set**

List all open pull requests for `i6bimua/vla-wam-daily` and verify PRs
`#1`–`#9` are still open, authored by `app/dependabot`, and target `main`.
Stop if any number belongs to a human author or changed purpose.

- [ ] **Step 2: Close the verified bot PRs**

Set the state of each verified Dependabot PR `#1` through `#9` to `closed`.
Do not close any newly created grouped PR and do not delete branches.

- [ ] **Step 3: Verify the final PR state**

List open pull requests again.

Expected: none of PRs `#1`–`#9` remains open; any newly opened PR follows the
grouped policy and is reported rather than automatically closed.

- [ ] **Step 4: Report the policy**

Report that routine compatible dependency PRs will be tested and handled
without user intervention, while intentional major upgrades remain manual.
