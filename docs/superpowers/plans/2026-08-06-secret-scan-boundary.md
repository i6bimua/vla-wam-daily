# Secret Scan Boundary Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the repository secret scan from misclassifying `task-state-aligned` while retaining detection of standalone API keys.

**Architecture:** Keep the safety check in `tests/test_docs.py`, centralize its two byte patterns, and make the `sk-` pattern require a non-word left boundary. No pipeline, data, model, or workflow behavior changes.

**Tech Stack:** Python `re`, pytest, GitHub Actions

---

### Task 1: Reproduce the false positive

**Files:**
- Modify: `tests/test_docs.py`

- [ ] Move the existing patterns to a module-level `SECRET_PATTERNS` tuple and add `contains_secret_like_bytes(value: bytes) -> bool`.
- [ ] Add `test_secret_patterns_require_token_boundaries` asserting that standalone `sk-0123456789abcdef` and `Bearer abcdefghijklmnop` match, while `task-state-aligned` does not.
- [ ] Run `uv run pytest tests/test_docs.py::test_secret_patterns_require_token_boundaries -q` and verify it fails only for `task-state-aligned`.

### Task 2: Apply the minimal boundary fix

**Files:**
- Modify: `tests/test_docs.py`

- [ ] Change the first pattern to `rb"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{12,}"`.
- [ ] Run the focused boundary test and repository tracked-file scan; expect both to pass.
- [ ] Run `uv run ruff check src tests && uv run mypy && uv run pytest`; expect all checks to pass.
- [ ] Commit the test-only fix.

### Task 3: Publish and recover the daily update

**Files:**
- No additional local changes.

- [ ] Push the branch, open a ready PR, wait for Python and Web CI, and squash-merge.
- [ ] Manually dispatch `Daily arXiv Update` on the repaired `main` branch with normal defaults.
- [ ] Verify the update, persisted-data validation, commit, Pages build, and deployment all succeed.
- [ ] Confirm the live cumulative homepage count matches the repository archive count.
