# Daily Persistence and arXiv Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow valid generated Figure caches to pass repository validation and expand the three-day arXiv fetch capacity to 2000 results per category.

**Architecture:** Keep deterministic browser fixture files immutable while validating the production Figure cache as mutable typed JSON. Preserve the existing paginator, three-day lookback, truncation guard, and 60-candidate DeepSeek ceiling; change only the configured and schema-default arXiv capacity.

**Tech Stack:** Python 3.13, pytest, Pydantic, YAML, GitHub Actions, arXiv API, DeepSeek API

---

## File Map

- `tests/test_storage.py`: separates the immutable fixture cache contract from
  the mutable production cache contract.
- `tests/test_config.py`: specifies the checked-in and schema-default arXiv
  capacity.
- `src/vla_wam_daily/config.py`: owns the schema default and validation range.
- `config/topics.yaml`: owns the production fetch capacity.
- `.github/workflows/daily.yml`: remains unchanged; its full post-generation
  validation is the hosted acceptance test.

### Task 1: Correct the production Figure cache contract

**Files:**
- Modify: `tests/test_storage.py:434-437`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Preserve the hosted RED evidence**

Run:

```bash
gh run view 30507057770 --repo i6bimua/vla-wam-daily --log-failed |
  rg "test_seed_figure_caches_are_valid_empty_objects|Left contains 14 more items|1 failed, 678 passed"
```

Expected: output proves the valid generated production cache failed only because
the test expected `{}`.

- [ ] **Step 2: Separate production validation from fixture seed validation**

Replace the parametrized empty-cache test with:

```python
def test_repository_figure_cache_is_a_valid_json_object() -> None:
    data_dir = Path("data")
    payload = json.loads((data_dir / "cache/figures.json").read_text(encoding="utf-8"))
    cache = load_figure_cache(data_dir)

    assert isinstance(payload, dict)
    assert set(cache) == set(payload)


def test_browser_fixture_figure_cache_is_a_valid_empty_seed() -> None:
    data_dir = Path("tests/fixtures/data")

    assert load_figure_cache(data_dir) == {}
    assert (data_dir / "cache/figures.json").read_bytes() == b"{}\n"
```

The loader validates every non-empty production entry against
`FigureCacheEntry`. The key comparison also proves the typed loader consumed
the complete JSON object.

- [ ] **Step 3: Run the focused storage tests**

Run:

```bash
uv run pytest \
  tests/test_storage.py::test_repository_figure_cache_is_a_valid_json_object \
  tests/test_storage.py::test_browser_fixture_figure_cache_is_a_valid_empty_seed \
  -q
```

Expected: 2 tests pass against the current empty production seed. The decisive
non-empty GREEN check occurs when the persisted workflow reruns in Task 4.

- [ ] **Step 4: Commit the cache-contract correction**

```bash
git add tests/test_storage.py
git commit -m "test: allow generated Figure cache entries"
```

### Task 2: Expand the arXiv category capacity

**Files:**
- Modify: `tests/test_config.py:8`
- Modify: `tests/test_config.py:29-35`
- Modify: `src/vla_wam_daily/config.py:43-47`
- Modify: `config/topics.yaml:1-9`
- Test: `tests/test_config.py`

- [ ] **Step 1: Add failing capacity assertions**

Import `ArxivConfig`:

```python
from vla_wam_daily.config import ArxivConfig, load_config
```

Extend the checked-in configuration test:

```python
def test_default_config_uses_quality_model() -> None:
    config = load_config(Path("config/topics.yaml"))
    assert config.arxiv.max_results_per_category == 2000
    assert config.analysis.model_for("quality") == "deepseek-v4-pro"
    assert config.analysis.model_for("economy") == "deepseek-v4-flash"
    assert config.analysis.threshold == 6
    assert config.analysis.max_candidates == 60
```

Add a schema-default test:

```python
def test_arxiv_schema_default_supports_three_day_catchup_capacity() -> None:
    config = ArxivConfig(categories=["cs.RO"])

    assert config.max_results_per_category == 2000
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
uv run pytest \
  tests/test_config.py::test_default_config_uses_quality_model \
  tests/test_config.py::test_arxiv_schema_default_supports_three_day_catchup_capacity \
  -q
```

Expected: both tests fail because the current value is 500.

- [ ] **Step 3: Implement the 2000-result capacity**

Change the Pydantic field while keeping its validation ceiling:

```python
class ArxivConfig(StrictModel):
    categories: ConfigStringList
    lookback_days: int = Field(default=3, ge=1, le=31)
    max_results_per_category: int = Field(default=2000, ge=1, le=2000)
    request_delay_seconds: float = Field(default=3.0, ge=0)
```

Change the checked-in YAML:

```yaml
arxiv:
  categories:
    - cs.RO
    - cs.CV
    - cs.AI
    - cs.LG
  lookback_days: 3
  max_results_per_category: 2000
  request_delay_seconds: 3.0
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_config.py tests/test_storage.py -q
```

Expected: all configuration and storage tests pass.

- [ ] **Step 5: Commit the capacity change**

```bash
git add tests/test_config.py src/vla_wam_daily/config.py config/topics.yaml
git commit -m "fix: expand arXiv catchup capacity"
```

### Task 3: Run complete local acceptance

**Files:**
- Verify: `src/`
- Verify: `tests/`
- Verify: `web/`
- Verify: `.github/workflows/`

- [ ] **Step 1: Run the Python gate**

Run:

```bash
uv sync --frozen
uv run ruff check src tests
uv run mypy
uv run pytest --cov=vla_wam_daily --cov-report=term-missing
```

Expected: lint and type checks pass, 680 or more tests pass, and total coverage
remains at least 95%.

- [ ] **Step 2: Run the web gate**

Run:

```bash
cd web
pnpm install --frozen-lockfile
pnpm test
pnpm format:check
VLA_WAM_DATA_DIR=../tests/fixtures/data BASE_PATH=/vla-wam-daily/ pnpm build
BASE_PATH=/vla-wam-daily/ pnpm verify:figure-build
BASE_PATH=/vla-wam-daily/ pnpm verify:information-build
BASE_PATH=/vla-wam-daily/ pnpm verify:search-build
```

Expected: 162 unit tests pass, formatting passes, the project-base fixture site
builds, and all three build verifiers pass.

- [ ] **Step 3: Run repository integrity checks**

Run from the repository root:

```bash
git diff --check
git status --short --branch
```

Expected: no whitespace errors and a clean feature branch.

### Task 4: Publish and complete the first persisted update

**Files:**
- Publish: current implementation branch through the selected finishing flow
- Generate: `data/latest.json`
- Generate: `data/archive/*.json`
- Generate: `data/cache/analyses.json`
- Generate: `data/cache/figures.json`

- [ ] **Step 1: Merge and push through the approved finishing flow**

Use `superpowers:finishing-a-development-branch`, merge the verified
implementation into `main`, re-run the Python and web unit suites on the merged
commit, clean the owned worktree, and push `main`.

Expected: GitHub CI and Pages both succeed for the fix commit.

- [ ] **Step 2: Start the persisted one-day quality run**

Run:

```bash
gh workflow run daily.yml \
  --repo i6bimua/vla-wam-daily \
  --ref main \
  -f dry_run=false \
  -f profile=quality \
  -f lookback_days=1 \
  -f threshold=6
```

Expected: the update job generates data, all 680 or more tests pass with a
non-empty production Figure cache, the bot commits only `data/**`, and the build
and deploy jobs succeed.

- [ ] **Step 3: Synchronize and validate generated data**

Run after the workflow succeeds:

```bash
git pull --ff-only
jq -e '.papers | length >= 1' data/latest.json
jq -e 'length >= 1' data/cache/figures.json
uv run pytest -q
git status --short --branch
```

Expected: at least one published paper, at least one Figure cache entry, all
tests pass, and local `main` is synchronized and clean. Report the actual paper
and Figure counts from the workflow output.

- [ ] **Step 4: Verify the deployed site and resources**

Run:

```bash
curl --fail --location --output /dev/null https://i6bimua.github.io/vla-wam-daily/
curl --fail --location --output /dev/null https://i6bimua.github.io/vla-wam-daily/rss.xml
curl --fail --location --output /dev/null https://i6bimua.github.io/vla-wam-daily/pagefind/pagefind.js
```

Open the live homepage and one generated paper detail page. Confirm that paper
cards are present, Figure 1/Figure 2 display when available, Figure download
actions are present, and the mobile layout has no horizontal overflow.

Expected: all resource requests return HTTP 200 and the live UI exposes the
generated paper and Figure metadata.
