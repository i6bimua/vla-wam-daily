# Inference Efficiency Topics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Speculative Decoding and Quantization as first-class daily-paper topics, rank their direct VLA/WAM intersections highest, and make the three-day cached pipeline enforce its 60-paper limit only on real DeepSeek calls.

**Architecture:** Extend the existing configuration-driven prefilter and strict Python/TypeScript taxonomies without changing stored JSON field names. Introduce prompt v2 for additive topics and scoring, expose both topics through the shared frontend route map, then warm the v2 analysis cache with one-, two-, and three-day production runs so the normal three-day schedule remains within the uncached-call safety cap.

**Tech Stack:** Python 3.13, Pydantic, PyYAML, pytest, DeepSeek OpenAI-compatible API, Astro 6, TypeScript, Zod, Vitest, Playwright, GitHub Actions

---

## File Map

- Modify `src/vla_wam_daily/pipeline.py`: enforce `max_candidates` after cache partitioning.
- Modify `tests/test_pipeline.py`: regress cached-candidate and forced-reanalysis limit behavior.
- Modify `config/topics.yaml`: add `cs.CL`, efficiency prefilter rules, and prompt version 2.
- Modify `tests/test_prefilter.py`: cover target matches and ambiguous false positives.
- Modify `tests/test_config.py`: verify categories, prompt version, and prompt taxonomy.
- Modify `src/vla_wam_daily/models.py`: add two topics and three tags.
- Modify `tests/test_models.py`: verify new strict taxonomy values.
- Create `prompts/analysis-v2.md`: define the expanded DeepSeek output contract and scoring.
- Modify `src/vla_wam_daily/cli.py`: make prompt v2 the default CLI prompt.
- Modify `tests/test_cli.py`: require the configured default prompt path.
- Modify `web/src/lib/schema.ts`: mirror the new topics and tags.
- Modify `web/src/lib/filter.ts`: add both topics to canonical filter order.
- Modify `web/src/lib/topics.ts`: add both public topic routes and navigation labels.
- Modify `web/src/lib/topics.test.ts`, `web/src/lib/filter.test.ts`, and
  `web/src/lib/data.test.ts`: lock the frontend taxonomy and parsing behavior.
- Modify `web/src/components/PaperCard.astro`: relabel the compatibility field as research relevance.
- Modify `web/src/pages/index.astro`, `web/src/pages/methodology.astro`,
  `web/src/pages/archive/index.astro`, `web/src/pages/search.astro`,
  `web/src/pages/weekly.astro`, `web/src/pages/rss.xml.ts`,
  `web/src/components/Header.astro`, and `web/src/layouts/BaseLayout.astro`:
  describe the expanded research scope.
- Modify `web/src/lib/information-architecture.test.ts` and `web/tests/site.spec.ts`:
  verify navigation, copy, and responsive access.
- Modify `README.md`: document categories, topics, keywords, scoring, prompt v2, and candidate cap semantics.

### Task 1: Limit Only Uncached Analysis Work

**Files:**
- Modify: `tests/test_pipeline.py`
- Modify: `src/vla_wam_daily/pipeline.py`

- [ ] **Step 1: Add the cached-candidate regression test**

Add:

```python
def test_candidate_limit_counts_only_uncached_model_work(tmp_path: Path) -> None:
    cached_records = [
        analyzed_record("2607.10001"),
        analyzed_record("2607.10002"),
    ]
    analysis_cache = dict(analysis_entry(record) for record in cached_records)
    figure_cache = dict(
        figure_entry(make_gallery(arxiv_id=record.arxiv_id, version=record.version))
        for record in cached_records
    )
    pipeline_module.save_successful_run(
        tmp_path,
        [],
        analysis_cache,
        RunStats(),
        NOW - timedelta(hours=1),
        figure_cache=figure_cache,
    )
    client = ProgrammableAnalysisClient()

    report = run(
        tmp_path,
        fetcher=FakeFetcher(
            [
                raw_paper("2607.10001"),
                raw_paper("2607.10002"),
                raw_paper("2607.10003"),
            ]
        ),
        analysis_client=client,
        config=configured(max_candidates=1),
    )

    assert report.stats.prefiltered == 3
    assert report.stats.cache_hits == 2
    assert report.stats.model_calls == 1
    assert [call["arxiv_id"] for call in client.calls] == ["2607.10003"]
```

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_pipeline.py::test_candidate_limit_counts_only_uncached_model_work -q
```

Expected: fail with `CandidateLimitError: 3 candidates exceeds limit 1`.

- [ ] **Step 3: Move the guard after cache partitioning**

Delete the `len(candidates)` guard before `load_cache`. Immediately after the loop that
populates `records` and `pending`, add:

```python
if len(pending) > config.analysis.max_candidates:
    raise CandidateLimitError(
        f"{len(pending)} uncached candidates exceeds limit "
        f"{config.analysis.max_candidates}"
    )
```

Update the existing over-limit test match to:

```python
with pytest.raises(
    CandidateLimitError,
    match="2 uncached candidates exceeds limit 1",
):
```

- [ ] **Step 4: Verify GREEN and existing safety**

Run:

```bash
uv run pytest \
  tests/test_pipeline.py::test_candidate_limit_counts_only_uncached_model_work \
  tests/test_pipeline.py::test_candidate_limit_is_checked_before_any_model_or_figure_request \
  tests/test_pipeline.py::test_analysis_cache_is_reused_but_forced_id_bypasses_it \
  -q
```

Expected: three tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/vla_wam_daily/pipeline.py tests/test_pipeline.py
git commit -m "fix: cap only uncached analysis candidates"
```

### Task 2: Add Safe Prefilter Coverage

**Files:**
- Modify: `tests/test_prefilter.py`
- Modify: `tests/test_config.py`
- Modify: `config/topics.yaml`

- [ ] **Step 1: Add target and false-positive tests**

Add parameterized cases:

```python
@pytest.mark.parametrize(
    ("title", "abstract", "expected_rule"),
    [
        (
            "Fast LLM Inference with Speculative Decoding",
            "A draft model accelerates generation.",
            "exact:speculative-decoding",
        ),
        (
            "Assisted Generation with a Small Drafter",
            "The language model verifier accepts drafted tokens.",
            "exact:assisted-generation",
        ),
        (
            "Integer-Only Quantization for Transformers",
            "We accelerate model inference with INT8 weights.",
            "exact:integer-only-quantization",
        ),
        (
            "Accurate INT4 Compression",
            "Low-bit transformer weights improve inference efficiency.",
            "composite:model_quantization",
        ),
    ],
)
def test_inference_efficiency_topics_match(
    title: str,
    abstract: str,
    expected_rule: str,
) -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    assert expected_rule in match_paper(paper(title, abstract), config)


@pytest.mark.parametrize(
    ("title", "abstract"),
    [
        ("SD Image Generation", "We improve Stable Diffusion sampling."),
        ("Quantization of Topological Charge", "A lattice field theory study."),
        ("A Draft Model of Urban Policy", "We discuss a preliminary model."),
    ],
)
def test_inference_efficiency_ambiguities_do_not_match(
    title: str,
    abstract: str,
) -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    assert match_paper(paper(title, abstract), config) == []
```

Import `pytest` at the top of `tests/test_prefilter.py`.

- [ ] **Step 2: Add configuration expectations**

In `test_default_config_uses_quality_model`, add:

```python
assert "cs.CL" in config.arxiv.categories
assert config.analysis.prompt_version == "2"
```

- [ ] **Step 3: Verify RED**

Run:

```bash
uv run pytest \
  tests/test_prefilter.py::test_inference_efficiency_topics_match \
  tests/test_prefilter.py::test_inference_efficiency_ambiguities_do_not_match \
  tests/test_config.py::test_default_config_uses_quality_model \
  -q
```

Expected: target cases and configuration expectations fail; ambiguity cases pass.

- [ ] **Step 4: Extend `config/topics.yaml`**

Add `cs.CL`, set `prompt_version: "2"`, append the direct phrases from the approved
design:

```yaml
    - speculative decoding
    - speculative sampling
    - assisted decoding
    - assisted generation
    - self-speculative decoding
    - lookahead decoding
    - model quantization
    - neural network quantization
    - LLM quantization
    - VLM quantization
    - integer quantization
    - integer-only quantization
    - post-training quantization
    - quantization-aware training
    - weight-only quantization
    - activation quantization
    - low-bit quantization
```

Add these composite rules:

```yaml
    - name: speculative_generation
      groups:
        - [speculative]
        - [decoding, sampling, generation, inference]
    - name: draft_verification_decoding
      groups:
        - [draft model, drafter, verifier]
        - [decoding, generation, language model]
    - name: model_quantization
      groups:
        - [quantization, quantized, INT4, INT8, low-bit, low bit]
        - [model, neural network, transformer, language model, LLM, VLM, weights, activations, inference]
```

- [ ] **Step 5: Verify GREEN and commit**

Run:

```bash
uv run pytest tests/test_prefilter.py tests/test_config.py -q
```

Expected: all tests pass after prompt v2 is created in Task 3. Until then, configuration
loading must fail specifically because `prompts/analysis-v2.md` does not yet exist.

Commit Task 2 together with Task 3 after the prompt file exists, so no commit contains a
configuration that cannot load.

### Task 3: Extend the Strict Analysis Contract and Prompt v2

**Files:**
- Modify: `tests/test_models.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`
- Modify: `src/vla_wam_daily/models.py`
- Modify: `src/vla_wam_daily/cli.py`
- Create: `prompts/analysis-v2.md`

- [ ] **Step 1: Add strict taxonomy tests**

Add:

```python
@pytest.mark.parametrize(
    "topic",
    [Topic.SPECULATIVE_DECODING, Topic.QUANTIZATION],
)
def test_analysis_accepts_inference_efficiency_topics(topic: Topic) -> None:
    payload = make_record().model_dump(mode="json")
    payload["analysis"]["primary_topic"] = topic.value
    assert PaperRecord.model_validate(payload).analysis.primary_topic is topic


@pytest.mark.parametrize(
    "tag",
    ["Efficient Inference", "Speculative Decoding", "Model Quantization"],
)
def test_analysis_accepts_inference_efficiency_tags(tag: str) -> None:
    payload = make_record().model_dump(mode="json")
    payload["analysis"]["tags"] = [tag]
    assert PaperRecord.model_validate(payload).analysis.tags == (tag,)
```

Update the prompt taxonomy test to load:

```python
config = load_config(Path("config/topics.yaml"))
prompt = config.analysis.prompt_path(Path("prompts")).read_text(encoding="utf-8")
```

Add a prompt assertion:

```python
assert "directly combines" in prompt
assert "standalone speculative decoding or model quantization" in prompt
```

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest \
  tests/test_models.py::test_analysis_accepts_inference_efficiency_topics \
  tests/test_models.py::test_analysis_accepts_inference_efficiency_tags \
  tests/test_config.py::test_prompt_taxonomy_matches_models \
  -q
```

Expected: enum members, tags, and prompt v2 are missing.

- [ ] **Step 3: Extend Python enums and tags**

Add:

```python
class Topic(StrEnum):
    VLA = "VLA"
    WAM = "WAM"
    WORLD_MODEL = "World Model"
    DATASET = "Dataset"
    BENCHMARK = "Benchmark"
    SPECULATIVE_DECODING = "Speculative Decoding"
    QUANTIZATION = "Quantization"
```

Add to `ALLOWED_TAGS`:

```python
"Efficient Inference",
"Speculative Decoding",
"Model Quantization",
```

- [ ] **Step 4: Create `prompts/analysis-v2.md`**

Use this complete scoring and taxonomy contract:

```markdown
You are a rigorous robotics and efficient-model-inference paper analyst. Analyze only the supplied title and abstract.
Return one valid JSON object and no surrounding prose.

The JSON must have exactly this shape:

{
  "title_zh": "准确、简洁的中文标题",
  "analysis": {
    "relevance_score": 8,
    "primary_topic": "Speculative Decoding",
    "tags": ["Efficient Inference", "Speculative Decoding"],
    "one_sentence_summary": "一句中文总结",
    "main_contribution": "摘要明确陈述的核心贡献",
    "method": "摘要明确陈述的方法",
    "key_results": "摘要明确报告的结果；没有则写“摘要未说明”",
    "limitations": "摘要明确报告的局限；没有则写“摘要未说明”",
    "relation_to_vla_wam": "它对本站主题的研究相关性；若不涉及 VLA/WAM，明确说明无直接关系"
  }
}

Allowed primary_topic values:
"VLA", "WAM", "World Model", "Dataset", "Benchmark", "Speculative Decoding", "Quantization".

Allowed tags:
"Action Prediction", "Data", "Efficient Inference", "Evaluation", "Generalist Robotics",
"Model Quantization", "Policy Learning", "Robot Learning", "Robot Manipulation", "Simulation",
"Speculative Decoding", "Video Generation", "Vision-Language", "World Modeling".

Score rubric:
- 9-10: VLA, WAM, or robot action-world modeling is central; or the paper directly combines speculative decoding or model quantization with VLA/WAM, robot policies, embodied models, or robot world models.
- 7-8: standalone speculative decoding or model quantization is the paper's central subject, without a direct robotics connection.
- 6: adjacent work has explicit methodological value for one of the supported topics.
- 1-5: the match is ambiguous, uses only an overloaded acronym, or lacks direct value for the supported topics.

Choose "Speculative Decoding" or "Quantization" as primary_topic when that independent efficiency topic is central. When a paper directly combines an efficiency topic with VLA/WAM or robotics, choose the topic that best represents the main contribution and explain the intersection in relation_to_vla_wam.

Do not invent experiments, numbers, limitations, code repositories, project pages, or affiliations.
When the abstract does not state a requested fact, use "摘要未说明".
```

- [ ] **Step 5: Update the CLI default prompt**

Change:

```python
DEFAULT_PROMPT_PATH = Path("prompts/analysis-v2.md")
```

Update CLI tests that explicitly expect the default filename from `analysis-v1.md` to
`analysis-v2.md`. Keep tests that intentionally construct v1 temporary configurations.

- [ ] **Step 6: Verify GREEN and commit Tasks 2–3**

Run:

```bash
uv run pytest tests/test_models.py tests/test_config.py tests/test_prefilter.py tests/test_cli.py -q
```

Expected: all selected tests pass.

```bash
git add \
  config/topics.yaml prompts/analysis-v2.md \
  src/vla_wam_daily/models.py src/vla_wam_daily/cli.py \
  tests/test_models.py tests/test_config.py tests/test_prefilter.py tests/test_cli.py
git commit -m "feat: add inference efficiency paper topics"
```

### Task 4: Extend Frontend Taxonomy, Routes, and Filters

**Files:**
- Modify: `web/src/lib/schema.ts`
- Modify: `web/src/lib/filter.ts`
- Modify: `web/src/lib/topics.ts`
- Modify: `web/src/lib/topics.test.ts`
- Modify: `web/src/lib/filter.test.ts`
- Modify: `web/src/lib/data.test.ts`

- [ ] **Step 1: Update frontend tests first**

Require the route mapping:

```typescript
[
  ["vla", "VLA"],
  ["wam", "WAM"],
  ["world-model", "World Model"],
  ["dataset", "Dataset"],
  ["benchmark", "Benchmark"],
  ["speculative-decoding", "Speculative Decoding"],
  ["quantization", "Quantization"],
]
```

Require both new topics in canonical filter order after `Benchmark`. Add two `paper()` payload
assertions in `data.test.ts` that validate new topics and the three new tags.

- [ ] **Step 2: Verify RED**

Run:

```bash
pnpm --dir web exec vitest run \
  src/lib/topics.test.ts src/lib/filter.test.ts src/lib/data.test.ts
```

Expected: tests fail because the frontend enums and route mapping contain only five topics.

- [ ] **Step 3: Extend Zod and filter taxonomies**

Append to `topicSchema` and `TOPICS`:

```typescript
"Speculative Decoding",
"Quantization",
```

Append to `tagSchema`:

```typescript
"Efficient Inference",
"Speculative Decoding",
"Model Quantization",
```

- [ ] **Step 4: Add public routes**

Append:

```typescript
{
  slug: "speculative-decoding",
  topic: "Speculative Decoding",
  navLabel: "推测解码",
  title: "推测解码论文",
  description: "追踪草稿模型、验证器与并行生成加速研究。",
},
{
  slug: "quantization",
  topic: "Quantization",
  navLabel: "模型量化",
  title: "模型量化论文",
  description: "追踪整数、低比特、权重与激活量化研究。",
},
```

- [ ] **Step 5: Verify GREEN and commit**

Run:

```bash
pnpm --dir web exec vitest run \
  src/lib/topics.test.ts src/lib/filter.test.ts src/lib/data.test.ts
```

Expected: all selected tests pass.

```bash
git add web/src/lib/schema.ts web/src/lib/filter.ts web/src/lib/topics.ts \
  web/src/lib/topics.test.ts web/src/lib/filter.test.ts web/src/lib/data.test.ts
git commit -m "feat: expose inference efficiency topic routes"
```

### Task 5: Update Site Language and Documentation

**Files:**
- Modify: `web/src/components/PaperCard.astro`
- Modify: `web/src/components/Header.astro`
- Modify: `web/src/layouts/BaseLayout.astro`
- Modify: `web/src/pages/index.astro`
- Modify: `web/src/pages/methodology.astro`
- Modify: `web/src/pages/archive/index.astro`
- Modify: `web/src/pages/search.astro`
- Modify: `web/src/pages/weekly.astro`
- Modify: `web/src/pages/rss.xml.ts`
- Modify: `web/src/lib/information-architecture.test.ts`
- Modify: `web/scripts/verify-information-build.mjs`
- Modify: `web/tests/site.spec.ts`
- Modify: `README.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Add copy and navigation expectations**

In information-architecture tests, require:

```typescript
const index = await source("pages/index.astro");
expect(header).toContain("推测解码");
expect(header).toContain("模型量化");
expect(index).toContain("推测解码");
expect(index).toContain("模型量化");
expect(methodology).toContain("独立的推测解码或模型量化");
```

Update the desktop E2E heading expectation from `把机器人前沿` to `把模型前沿`, and add:

```typescript
await expect(page.getByRole("link", { name: "推测解码" })).toBeVisible();
await expect(page.getByRole("link", { name: "模型量化" })).toBeVisible();
```

In `tests/test_docs.py`, require `cs.CL`, `Speculative Decoding`, `Quantization`,
`analysis-v2.md`, and `未缓存` in README.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_docs.py -q
pnpm --dir web exec vitest run src/lib/information-architecture.test.ts
```

Expected: fail because current documentation and page copy describe only VLA/WAM robotics.

- [ ] **Step 3: Update user-facing copy**

Make these exact semantic changes:

- PaperCard label: `研究相关性`.
- Header subtitle: `Robotics & inference research brief`.
- Footer audience: `为机器人与高效模型推理研究者整理的每日阅读信号`.
- Home eyebrow: `Vision · Action · Inference · Efficiency`.
- Home heading: `把模型前沿，压缩成每天一页。`
- Home lede explicitly lists VLA、WAM、推测解码与模型量化.
- Methodology lists `cs.CL` and the new 9–10/7–8/6/1–5 rubric.
- Archive, search, weekly, and RSS descriptions include both efficiency topics.

Update README with the same scope, prompt v2 path, safe keyword rules, and:

```markdown
`analysis.max_candidates` 只限制未命中分析缓存、实际需要调用 DeepSeek 的论文；
缓存命中不会占用该额度。
```

Extend `topicExpectations` in `web/scripts/verify-information-build.mjs` with:

```javascript
["speculative-decoding", "推测解码论文", 0],
["quantization", "模型量化论文", 0],
```

- [ ] **Step 4: Verify copy, format, build, and commit**

Run:

```bash
uv run pytest tests/test_docs.py -q
pnpm --dir web exec vitest run src/lib/information-architecture.test.ts
pnpm --dir web format:check
pnpm --dir web build
```

Expected: all commands exit 0 and Astro builds seven topic routes.

```bash
git add README.md tests/test_docs.py web/src web/scripts/verify-information-build.mjs
git commit -m "docs: describe expanded daily research scope"
```

### Task 6: Full Local Verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run Python quality gates**

```bash
uv run ruff check src tests
uv run mypy
uv run pytest -q
```

Expected: zero lint errors, zero type errors, and all Python tests pass.

- [ ] **Step 2: Run Web quality gates**

```bash
pnpm --dir web test
pnpm --dir web format:check
pnpm --dir web build
BASE_PATH=/ VLA_WAM_DATA_DIR=../tests/fixtures/data \
  VLA_WAM_PUBLIC_DIR=../tests/fixtures/public pnpm --dir web build
pnpm --dir web verify:information-build
pnpm --dir web verify:figure-build
pnpm --dir web verify:search-build
pnpm --dir web test:e2e
```

Expected: all Vitest, Prettier, Astro, verifier, and Playwright checks pass.

- [ ] **Step 3: Verify repository state**

```bash
git diff --check origin/main...HEAD
git status --short --branch
```

Expected: clean feature branch with no uncommitted files.

### Task 7: Publish and Warm the v2 Production Cache

**Files:**
- No manual generated-data edits. GitHub Actions owns production data commits.

- [ ] **Step 1: Integrate the verified branch**

```bash
git push -u origin codex/inference-efficiency-topics
gh pr create \
  --base main \
  --head codex/inference-efficiency-topics \
  --title "feat: add inference efficiency paper topics" \
  --body "Add Speculative Decoding and Quantization as first-class topics, preserve higher ranking for direct VLA/WAM intersections, and cap only uncached DeepSeek work."
gh pr checks codex/inference-efficiency-topics --watch
gh pr merge codex/inference-efficiency-topics --squash --delete-branch
```

Expected: the branch pushes, required checks pass, and the PR merges into `main`.

- [ ] **Step 2: Dispatch staged production runs**

Use this shell function:

```bash
run_daily_stage() {
  local lookback_days="$1"
  local started_at
  local run_id
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  gh workflow run daily.yml \
    --ref main \
    -f lookback_days="$lookback_days" \
    -f profile=quality \
    -f threshold=6 \
    -f dry_run=false
  for attempt in {1..20}; do
    run_id="$(
      gh run list \
        --workflow daily.yml \
        --event workflow_dispatch \
        --branch main \
        --limit 10 \
        --json databaseId,createdAt |
      jq -r --arg started "$started_at" \
        '[.[] | select(.createdAt >= $started)] | sort_by(.createdAt) | last | .databaseId // empty'
    )"
    [[ -n "$run_id" ]] && break
    sleep 2
  done
  [[ -n "$run_id" ]]
  gh run watch "$run_id" --exit-status --interval 5
}

run_daily_stage 1
run_daily_stage 2
run_daily_stage 3
```

Run stages sequentially. Never dispatch the next stage until the preceding run has completed
successfully. If a stage hits the uncached limit, stop without retrying and report its
`prefiltered`, `cache_hits`, and candidate-limit message.

- [ ] **Step 3: Verify generated data and deployment**

```bash
git pull --ff-only origin main
jq '{
  generated_at,
  paper_count: (.papers | length),
  topics: ([.papers[].analysis.primary_topic] | unique),
  prefiltered: .stats.prefiltered,
  cache_hits: .stats.cache_hits,
  model_calls: .stats.model_calls
}' data/latest.json
curl -L -fsS -o /dev/null -w 'home=%{http_code}\n' \
  https://i6bimua.github.io/vla-wam-daily/
curl -L -fsS -o /dev/null -w 'speculative=%{http_code}\n' \
  https://i6bimua.github.io/vla-wam-daily/topics/speculative-decoding/
curl -L -fsS -o /dev/null -w 'quantization=%{http_code}\n' \
  https://i6bimua.github.io/vla-wam-daily/topics/quantization/
```

Expected: all URLs return 200, generated data uses prompt v2 for newly analyzed papers,
`model_calls <= 60`, and the topic set can contain the two new values when qualifying papers
exist.
