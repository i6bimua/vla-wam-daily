# VLA/WAM Daily Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy a serverless VLA/WAM research portal that collects arXiv papers daily, performs structured Chinese analysis with DeepSeek, and publishes a searchable Astro site on GitHub Pages.

**Architecture:** A Python 3.13 package owns fetching, deterministic prefiltering, DeepSeek analysis, validation, caching, and monthly JSON archives. An Astro 6 static site reads the validated JSON at build time and provides paper pages, filters, Pagefind search, archives, Weekly Top 5, RSS, and methodology disclosure. GitHub Actions runs CI, performs the daily update, commits generated data, and deploys only after all quality gates pass.

**Tech Stack:** Python 3.13, uv, Pydantic 2, httpx, feedparser, PyYAML, Tenacity, Typer, pytest, Ruff, mypy, Astro 6, TypeScript, Zod, Pagefind, Vitest, Playwright, pnpm, GitHub Actions, GitHub Pages.

---

## File map

The implementation will create these focused units:

- `pyproject.toml`: Python package metadata, dependencies, CLI entry point, and tooling.
- `src/vla_wam_daily/models.py`: canonical domain models and JSON contract.
- `src/vla_wam_daily/config.py`: validated YAML configuration and model-profile lookup.
- `src/vla_wam_daily/prefilter.py`: deterministic phrase and composite-rule matching.
- `src/vla_wam_daily/resources.py`: syntax-validated project/code URL extraction.
- `src/vla_wam_daily/arxiv_client.py`: throttled and retrying arXiv Atom API client.
- `src/vla_wam_daily/deepseek_client.py`: DeepSeek JSON Output transport and usage capture.
- `src/vla_wam_daily/analyzer.py`: prompt rendering and AI response-to-record conversion.
- `src/vla_wam_daily/storage.py`: cache, latest file, monthly archives, and atomic writes.
- `src/vla_wam_daily/pipeline.py`: orchestration, concurrency, quality gates, and dry-run behavior.
- `src/vla_wam_daily/cli.py`: Typer commands used locally and in Actions.
- `config/topics.yaml`: arXiv categories, prefilter rules, thresholds, and model profiles.
- `prompts/analysis-v1.md`: versioned, abstract-only analysis contract.
- `data/latest.json`: last successful published batch.
- `data/archive/*.json`: published monthly history.
- `data/cache/analyses.json`: both published and rejected AI results for cost control.
- `web/src/lib/schema.ts`: TypeScript mirror of the public JSON contract.
- `web/src/lib/data.ts`: validated build-time loading and latest-version selection.
- `web/src/lib/filter.ts`: deterministic client-side filtering and URL-state parsing.
- `web/src/lib/weekly.ts`: deterministic topic-balanced Weekly Top 5.
- `web/src/components/*`: layout, navigation, cards, explorer, and search components.
- `web/src/pages/*`: home, topic, paper detail, archive, weekly, methodology, RSS, and 404.
- `.github/workflows/ci.yml`: Python and web validation.
- `.github/workflows/daily.yml`: scheduled data update plus protected deployment.
- `.github/workflows/pages.yml`: deploy code/config edits without calling DeepSeek.

## Task 1: Scaffold the Python project

**Files:**

- Create: `.gitignore`
- Create: `.python-version`
- Create: `pyproject.toml`
- Create: `src/vla_wam_daily/__init__.py`
- Create: `src/vla_wam_daily/__main__.py`
- Create: `tests/test_package.py`

- [ ] **Step 1: Write the failing package-import test**

```python
# tests/test_package.py
from vla_wam_daily import __version__


def test_package_version() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python -m pytest tests/test_package.py -v
```

Expected: FAIL because `vla_wam_daily` does not exist.

- [ ] **Step 3: Add the package and toolchain files**

```toml
# pyproject.toml
[project]
name = "vla-wam-daily"
version = "0.1.0"
description = "Daily VLA and World Action Model research portal"
readme = "README.md"
requires-python = ">=3.13"
license = "MIT"
dependencies = [
  "feedparser>=6.0.12,<7",
  "httpx>=0.28,<1",
  "pydantic>=2.12,<3",
  "PyYAML>=6.0.2,<7",
  "tenacity>=9.1,<10",
  "typer>=0.17,<1",
]

[project.scripts]
vla-wam-daily = "vla_wam_daily.cli:app"

[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[dependency-groups]
dev = [
  "mypy>=1.17,<2",
  "pytest>=8.4,<9",
  "pytest-cov>=6.2,<7",
  "respx>=0.22,<1",
  "ruff>=0.12,<1",
  "types-PyYAML>=6.0.12.20250516",
]

[tool.hatch.build.targets.wheel]
packages = ["src/vla_wam_daily"]

[tool.pytest.ini_options]
addopts = "-q --strict-markers"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.13"
strict = true
packages = ["vla_wam_daily"]
ignore_missing_imports = true
```

```text
# .python-version
3.13
```

```gitignore
# .gitignore
.DS_Store
.env
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
__pycache__/
*.py[cod]
htmlcov/
.coverage
artifacts/
web/.astro/
web/dist/
web/node_modules/
work/
outputs/
```

```python
# src/vla_wam_daily/__init__.py
"""VLA/WAM Daily data pipeline."""

__version__ = "0.1.0"
```

```python
# src/vla_wam_daily/__main__.py
from vla_wam_daily.cli import app


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Resolve dependencies and run the package test**

Run:

```bash
uv lock
uv run pytest tests/test_package.py -v
```

Expected: lockfile generated and `1 passed`.

- [ ] **Step 5: Run static checks**

Run:

```bash
uv run ruff check src tests
uv run mypy
```

Expected: both commands exit 0.

- [ ] **Step 6: Commit the scaffold**

```bash
git add .gitignore .python-version pyproject.toml uv.lock src/vla_wam_daily tests/test_package.py
git commit -m "build: scaffold Python pipeline"
```

## Task 2: Define the canonical data models

**Files:**

- Create: `src/vla_wam_daily/models.py`
- Create: `tests/factories.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing model-validation tests**

```python
# tests/test_models.py
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tests.factories import make_record
from vla_wam_daily.models import Analysis, DataFile, Topic


def test_analysis_rejects_out_of_range_score() -> None:
    with pytest.raises(ValidationError):
        Analysis(
            relevance_score=11,
            primary_topic=Topic.VLA,
            tags=["Vision-Language"],
            one_sentence_summary="总结",
            main_contribution="贡献",
            method="方法",
            key_results="摘要未说明",
            limitations="摘要未说明",
            relation_to_vla_wam="直接相关",
        )


def test_data_file_serializes_public_contract() -> None:
    record = make_record()
    data = DataFile(
        schema_version="1",
        generated_at=datetime(2026, 7, 27, tzinfo=UTC),
        stats={"fetched": 4, "prefiltered": 1, "published": 1},
        papers=[record],
    )
    payload = data.model_dump(mode="json")
    assert payload["papers"][0]["analysis"]["primary_topic"] == "VLA"
    assert payload["papers"][0]["provenance"]["analysis_scope"] == "title_and_abstract"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest tests/test_models.py -v
```

Expected: FAIL because `models.py` and `tests.factories` do not exist.

- [ ] **Step 3: Implement strict models and a shared factory**

```python
# src/vla_wam_daily/models.py
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Topic(StrEnum):
    VLA = "VLA"
    WAM = "WAM"
    WORLD_MODEL = "World Model"
    DATASET = "Dataset"
    BENCHMARK = "Benchmark"


ALLOWED_TAGS = frozenset(
    {
        "Action Prediction",
        "Data",
        "Evaluation",
        "Generalist Robotics",
        "Policy Learning",
        "Robot Learning",
        "Robot Manipulation",
        "Simulation",
        "Video Generation",
        "Vision-Language",
        "World Modeling",
    }
)


class RawPaper(StrictModel):
    arxiv_id: str = Field(pattern=r"^\d{4}\.\d{4,5}$")
    version: int = Field(ge=1)
    published_at: datetime
    updated_at: datetime
    title: str = Field(min_length=1)
    authors: list[str] = Field(min_length=1)
    arxiv_categories: list[str] = Field(min_length=1)
    abstract: str = Field(min_length=1)
    comment: str | None = None


class Analysis(StrictModel):
    relevance_score: int = Field(ge=1, le=10)
    primary_topic: Topic
    tags: list[str]
    one_sentence_summary: str = Field(min_length=1)
    main_contribution: str = Field(min_length=1)
    method: str = Field(min_length=1)
    key_results: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    relation_to_vla_wam: str = Field(min_length=1)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: list[str]) -> list[str]:
        unknown = sorted(set(tags) - ALLOWED_TAGS)
        if unknown:
            raise ValueError(f"unsupported tags: {', '.join(unknown)}")
        return list(dict.fromkeys(tags))


class AIOutput(StrictModel):
    title_zh: str = Field(min_length=1)
    analysis: Analysis


class Resources(StrictModel):
    arxiv_url: HttpUrl
    pdf_url: HttpUrl
    project_url: HttpUrl | None = None
    code_url: HttpUrl | None = None


class Provenance(StrictModel):
    analysis_scope: str = Field(pattern=r"^title_and_abstract$")
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    analyzed_at: datetime


class PaperRecord(StrictModel):
    arxiv_id: str = Field(pattern=r"^\d{4}\.\d{4,5}$")
    version: int = Field(ge=1)
    published_at: datetime
    updated_at: datetime
    title: str
    title_zh: str
    authors: list[str]
    arxiv_categories: list[str]
    abstract: str
    matched_rules: list[str]
    analysis: Analysis
    resources: Resources
    provenance: Provenance


class TokenUsage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class RunStats(StrictModel):
    fetched: int = Field(default=0, ge=0)
    prefiltered: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    published: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    error_categories: dict[str, int] = Field(default_factory=dict)


class DataFile(StrictModel):
    schema_version: str = "1"
    generated_at: datetime
    stats: RunStats
    papers: list[PaperRecord]


class CacheEntry(StrictModel):
    key: str
    record: PaperRecord
```

```python
# tests/factories.py
from datetime import UTC, datetime

from vla_wam_daily.models import Analysis, PaperRecord, Provenance, Resources, Topic


def make_record(
    *,
    arxiv_id: str = "2607.12345",
    version: int = 1,
    score: int = 8,
    topic: Topic = Topic.VLA,
) -> PaperRecord:
    timestamp = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)
    return PaperRecord(
        arxiv_id=arxiv_id,
        version=version,
        published_at=timestamp,
        updated_at=timestamp,
        title="A Vision-Language-Action Policy for Robot Manipulation",
        title_zh="用于机器人操作的视觉语言动作策略",
        authors=["Ada Robot", "Wei Model"],
        arxiv_categories=["cs.RO", "cs.CV"],
        abstract="We introduce a vision-language-action policy for robot manipulation.",
        matched_rules=["vision language action"],
        analysis=Analysis(
            relevance_score=score,
            primary_topic=topic,
            tags=["Vision-Language", "Robot Manipulation"],
            one_sentence_summary="提出一种用于机器人操作的视觉语言动作策略。",
            main_contribution="统一视觉、语言与动作建模。",
            method="使用多模态策略学习。",
            key_results="摘要未说明",
            limitations="摘要未说明",
            relation_to_vla_wam="该方法直接属于 VLA。",
        ),
        resources=Resources(
            arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        ),
        provenance=Provenance(
            analysis_scope="title_and_abstract",
            model="deepseek-v4-pro",
            prompt_version="1",
            analyzed_at=timestamp,
        ),
    )
```

- [ ] **Step 4: Run model tests and static checks**

Run:

```bash
uv run pytest tests/test_models.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the data contract**

```bash
git add src/vla_wam_daily/models.py tests/factories.py tests/test_models.py
git commit -m "feat: define paper data contract"
```

## Task 3: Add validated configuration and the versioned prompt

**Files:**

- Create: `src/vla_wam_daily/config.py`
- Create: `config/topics.yaml`
- Create: `prompts/analysis-v1.md`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

```python
# tests/test_config.py
from pathlib import Path

from vla_wam_daily.config import load_config


def test_default_config_uses_quality_model() -> None:
    config = load_config(Path("config/topics.yaml"))
    assert config.analysis.model_for("quality") == "deepseek-v4-pro"
    assert config.analysis.model_for("economy") == "deepseek-v4-flash"
    assert config.analysis.threshold == 6
    assert config.analysis.max_candidates == 60


def test_standalone_vla_and_wam_are_not_exact_phrases() -> None:
    config = load_config(Path("config/topics.yaml"))
    normalized = {phrase.casefold() for phrase in config.prefilter.exact_phrases}
    assert "vla" not in normalized
    assert "wam" not in normalized
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest tests/test_config.py -v
```

Expected: FAIL because the configuration module and YAML file do not exist.

- [ ] **Step 3: Implement the validated configuration**

```python
# src/vla_wam_daily/config.py
from pathlib import Path

import yaml
from pydantic import Field

from vla_wam_daily.models import StrictModel


class ArxivConfig(StrictModel):
    categories: list[str] = Field(min_length=1)
    lookback_days: int = Field(default=3, ge=1, le=31)
    max_results_per_category: int = Field(default=500, ge=1, le=2000)
    request_delay_seconds: float = Field(default=3.0, ge=0)


class CompositeRule(StrictModel):
    name: str
    groups: list[list[str]] = Field(min_length=2)


class PrefilterConfig(StrictModel):
    exact_phrases: list[str]
    composite_rules: list[CompositeRule]


class AnalysisConfig(StrictModel):
    threshold: int = Field(default=6, ge=1, le=10)
    max_candidates: int = Field(default=60, ge=1)
    max_concurrency: int = Field(default=3, ge=1, le=8)
    max_failure_ratio: float = Field(default=0.30, ge=0, le=1)
    prompt_version: str = "1"
    max_output_tokens: int = Field(default=1800, ge=512)
    model_profiles: dict[str, str]

    def model_for(self, profile: str) -> str:
        try:
            return self.model_profiles[profile]
        except KeyError as exc:
            choices = ", ".join(sorted(self.model_profiles))
            raise ValueError(f"unknown profile {profile!r}; choose one of: {choices}") from exc


class AppConfig(StrictModel):
    arxiv: ArxivConfig
    prefilter: PrefilterConfig
    analysis: AnalysisConfig


def load_config(path: Path) -> AppConfig:
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return AppConfig.model_validate(payload)
```

```yaml
# config/topics.yaml
arxiv:
  categories:
    - cs.RO
    - cs.CV
    - cs.AI
    - cs.LG
  lookback_days: 3
  max_results_per_category: 500
  request_delay_seconds: 3.0

prefilter:
  exact_phrases:
    - vision-language-action
    - vision language action
    - world action model
    - world-action model
    - latent action model
    - video action model
    - action-conditioned world model
    - generalist robot policy
    - robot foundation model
    - multimodal robot policy
  composite_rules:
    - name: vision_language_robotics
      groups:
        - [vision-language, vision language, VLM, multimodal]
        - [robot, policy, action, manipulation]
    - name: world_video_robotics
      groups:
        - [world model, video model, world modeling]
        - [robot, action, manipulation, control]
    - name: generalist_robot_policy
      groups:
        - [generalist, foundation]
        - [robot policy, robotic policy]

analysis:
  threshold: 6
  max_candidates: 60
  max_concurrency: 3
  max_failure_ratio: 0.30
  prompt_version: "1"
  max_output_tokens: 1800
  model_profiles:
    quality: deepseek-v4-pro
    economy: deepseek-v4-flash
```

```markdown
<!-- prompts/analysis-v1.md -->
You are a rigorous robotics-paper analyst. Analyze only the supplied title and abstract.
Return one valid JSON object and no surrounding prose.

The JSON must have exactly this shape:

{
  "title_zh": "准确、简洁的中文标题",
  "analysis": {
    "relevance_score": 8,
    "primary_topic": "VLA",
    "tags": ["Vision-Language", "Robot Manipulation"],
    "one_sentence_summary": "一句中文总结",
    "main_contribution": "摘要明确陈述的核心贡献",
    "method": "摘要明确陈述的方法",
    "key_results": "摘要明确报告的结果；没有则写“摘要未说明”",
    "limitations": "摘要明确报告的局限；没有则写“摘要未说明”",
    "relation_to_vla_wam": "它与 VLA/WAM 的直接关系"
  }
}

Allowed primary_topic values:
"VLA", "WAM", "World Model", "Dataset", "Benchmark".

Allowed tags:
"Action Prediction", "Data", "Evaluation", "Generalist Robotics", "Policy Learning",
"Robot Learning", "Robot Manipulation", "Simulation", "Video Generation",
"Vision-Language", "World Modeling".

Score rubric:
- 9-10: VLA, WAM, or robot action-world modeling is the paper's central subject.
- 7-8: strongly related method, dataset, benchmark, or generalist robot policy.
- 6: adjacent work with direct value to VLA/WAM research.
- 1-5: too distant for publication on this portal.

Do not invent experiments, numbers, limitations, code repositories, project pages, or affiliations.
When the abstract does not state a requested fact, use "摘要未说明".
```

- [ ] **Step 4: Run tests and validate the YAML**

Run:

```bash
uv run pytest tests/test_config.py -v
uv run python -c "from pathlib import Path; from vla_wam_daily.config import load_config; print(load_config(Path('config/topics.yaml')).analysis.model_for('quality'))"
```

Expected: tests pass and the command prints `deepseek-v4-pro`.

- [ ] **Step 5: Commit configuration and prompt**

```bash
git add src/vla_wam_daily/config.py config/topics.yaml prompts/analysis-v1.md tests/test_config.py
git commit -m "feat: configure VLA WAM analysis"
```

## Task 4: Implement deterministic prefiltering

**Files:**

- Create: `src/vla_wam_daily/prefilter.py`
- Create: `tests/test_prefilter.py`

- [ ] **Step 1: Write failing prefilter tests**

```python
# tests/test_prefilter.py
from datetime import UTC, datetime
from pathlib import Path

from vla_wam_daily.config import load_config
from vla_wam_daily.models import RawPaper
from vla_wam_daily.prefilter import match_paper


def paper(title: str, abstract: str) -> RawPaper:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    return RawPaper(
        arxiv_id="2607.12345",
        version=1,
        published_at=now,
        updated_at=now,
        title=title,
        authors=["A. Researcher"],
        arxiv_categories=["cs.RO"],
        abstract=abstract,
    )


def test_exact_phrase_matches_hyphen_variation() -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    matches = match_paper(
        paper("A Vision–Language–Action Model", "We learn a robot policy."),
        config,
    )
    assert "exact:vision-language-action" in matches


def test_composite_rule_requires_both_groups() -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    matches = match_paper(
        paper("Multimodal policy learning", "A VLM controls robot manipulation."),
        config,
    )
    assert "composite:vision_language_robotics" in matches


def test_standalone_acronyms_do_not_match() -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    assert match_paper(paper("VLA transport protocol", "WAM compression."), config) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest tests/test_prefilter.py -v
```

Expected: FAIL because `prefilter.py` does not exist.

- [ ] **Step 3: Implement phrase normalization and composite matching**

```python
# src/vla_wam_daily/prefilter.py
import re
import unicodedata

from vla_wam_daily.config import PrefilterConfig
from vla_wam_daily.models import RawPaper

SEPARATOR_RE = re.compile(r"[\s\-_–—/]+")
PUNCTUATION_RE = re.compile(r"[^\w\s]")


def normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = SEPARATOR_RE.sub(" ", normalized)
    normalized = PUNCTUATION_RE.sub(" ", normalized)
    return " ".join(normalized.split())


def contains_phrase(haystack: str, phrase: str) -> bool:
    normalized_phrase = normalize(phrase)
    return re.search(rf"\b{re.escape(normalized_phrase)}\b", haystack) is not None


def match_paper(paper: RawPaper, config: PrefilterConfig) -> list[str]:
    text = normalize(f"{paper.title}\n{paper.abstract}")
    matches: list[str] = []

    for phrase in config.exact_phrases:
        if contains_phrase(text, phrase):
            matches.append(f"exact:{normalize(phrase).replace(' ', '-')}")

    for rule in config.composite_rules:
        if all(any(contains_phrase(text, phrase) for phrase in group) for group in rule.groups):
            matches.append(f"composite:{rule.name}")

    return list(dict.fromkeys(matches))
```

- [ ] **Step 4: Run focused and full Python tests**

Run:

```bash
uv run pytest tests/test_prefilter.py -v
uv run pytest
uv run ruff check src tests
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit prefiltering**

```bash
git add src/vla_wam_daily/prefilter.py tests/test_prefilter.py
git commit -m "feat: add deterministic paper prefilter"
```

## Task 5: Extract only verifiable resource URLs

**Files:**

- Create: `src/vla_wam_daily/resources.py`
- Create: `tests/test_resources.py`

- [ ] **Step 1: Write failing URL-extraction tests**

```python
# tests/test_resources.py
from vla_wam_daily.resources import extract_resources


def test_extracts_code_and_project_urls_from_metadata() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract="Code: https://github.com/example/vla-policy.",
        comment="Project page https://example.github.io/vla-policy/",
    )
    assert str(resources.code_url) == "https://github.com/example/vla-policy"
    assert str(resources.project_url) == "https://example.github.io/vla-policy/"


def test_does_not_invent_missing_resources() -> None:
    resources = extract_resources(
        arxiv_id="2607.12345",
        abstract="No external links are provided.",
        comment=None,
    )
    assert resources.code_url is None
    assert resources.project_url is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest tests/test_resources.py -v
```

Expected: FAIL because `resources.py` does not exist.

- [ ] **Step 3: Implement syntax-validated URL extraction**

```python
# src/vla_wam_daily/resources.py
import re
from urllib.parse import urlparse

from pydantic import HttpUrl, TypeAdapter, ValidationError

from vla_wam_daily.models import Resources

URL_RE = re.compile(r"https?://[^\s<>\"]+")
HTTP_URL = TypeAdapter(HttpUrl)
CODE_HOSTS = {"github.com", "www.github.com", "gitlab.com", "www.gitlab.com"}
EXCLUDED_PROJECT_HOSTS = {
    "arxiv.org",
    "www.arxiv.org",
    "doi.org",
    "dx.doi.org",
    "openreview.net",
}


def clean_url(value: str) -> str:
    return value.rstrip(".,;:!?)]}")


def validated_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.findall(text):
        candidate = clean_url(match)
        try:
            HTTP_URL.validate_python(candidate)
        except ValidationError:
            continue
        urls.append(candidate)
    return list(dict.fromkeys(urls))


def extract_resources(arxiv_id: str, abstract: str, comment: str | None) -> Resources:
    urls = validated_urls(f"{abstract}\n{comment or ''}")
    code_url: str | None = None
    project_url: str | None = None

    for url in urls:
        host = urlparse(url).hostname or ""
        if code_url is None and host in CODE_HOSTS:
            code_url = url
            continue
        if project_url is None and host not in CODE_HOSTS | EXCLUDED_PROJECT_HOSTS:
            project_url = url

    return Resources(
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        project_url=project_url,
        code_url=code_url,
    )
```

- [ ] **Step 4: Run tests and commit**

Run:

```bash
uv run pytest tests/test_resources.py -v
uv run ruff check src tests
```

Expected: all tests and checks pass.

```bash
git add src/vla_wam_daily/resources.py tests/test_resources.py
git commit -m "feat: extract verifiable paper resources"
```

## Task 6: Build the arXiv client with retry, throttling, and version parsing

**Files:**

- Create: `src/vla_wam_daily/arxiv_client.py`
- Create: `tests/fixtures/arxiv_feed.xml`
- Create: `tests/test_arxiv_client.py`

- [ ] **Step 1: Add a realistic Atom fixture**

```xml
<!-- tests/fixtures/arxiv_feed.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query: search_query=cat:cs.RO</title>
  <entry>
    <id>http://arxiv.org/abs/2607.12345v2</id>
    <updated>2026-07-27T01:00:00Z</updated>
    <published>2026-07-26T12:00:00Z</published>
    <title>A Vision-Language-Action Policy for Robot Manipulation</title>
    <summary>We introduce a vision-language-action policy for robot manipulation.</summary>
    <author><name>Ada Robot</name></author>
    <author><name>Wei Model</name></author>
    <category term="cs.RO"/>
    <category term="cs.CV"/>
    <arxiv:primary_category term="cs.RO"/>
    <arxiv:comment>Code: https://github.com/example/vla-policy</arxiv:comment>
    <link href="http://arxiv.org/abs/2607.12345v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2607.12345v2" rel="related" type="application/pdf"/>
  </entry>
</feed>
```

- [ ] **Step 2: Write failing client tests**

```python
# tests/test_arxiv_client.py
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from vla_wam_daily.arxiv_client import ArxivClient, ArxivWindowTruncatedError


@respx.mock
def test_fetch_recent_parses_id_version_and_categories() -> None:
    feed = Path("tests/fixtures/arxiv_feed.xml").read_text(encoding="utf-8")
    route = respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=feed)
    )
    client = ArxivClient(
        user_agent="VLA-WAM-Daily/0.1 (https://github.com/example/vla-wam-daily)",
        request_delay_seconds=0,
        retries=1,
    )
    papers = client.fetch_recent(
        categories=["cs.RO"],
        since=datetime(2026, 7, 24, tzinfo=UTC),
        until=datetime(2026, 7, 28, tzinfo=UTC),
        max_results_per_category=500,
    )
    assert route.called
    assert papers[0].arxiv_id == "2607.12345"
    assert papers[0].version == 2
    assert papers[0].arxiv_categories == ["cs.RO", "cs.CV"]


@respx.mock
def test_fetch_by_ids_uses_id_list() -> None:
    feed = Path("tests/fixtures/arxiv_feed.xml").read_text(encoding="utf-8")
    route = respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=feed)
    )
    client = ArxivClient(user_agent="VLA-WAM-Daily/0.1", request_delay_seconds=0, retries=1)
    papers = client.fetch_by_ids(["2607.12345"])
    assert route.calls.last.request.url.params["id_list"] == "2607.12345"
    assert len(papers) == 1


@respx.mock
def test_fetch_recent_rejects_a_truncated_time_window() -> None:
    feed = Path("tests/fixtures/arxiv_feed.xml").read_text(encoding="utf-8")
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=feed)
    )
    client = ArxivClient(user_agent="VLA-WAM-Daily/0.1", request_delay_seconds=0, retries=1)
    with pytest.raises(ArxivWindowTruncatedError):
        client.fetch_recent(
            categories=["cs.RO"],
            since=datetime(2026, 7, 24, tzinfo=UTC),
            until=datetime(2026, 7, 28, tzinfo=UTC),
            max_results_per_category=1,
        )
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_arxiv_client.py -v
```

Expected: FAIL because `arxiv_client.py` does not exist.

- [ ] **Step 4: Implement the arXiv client**

```python
# src/vla_wam_daily/arxiv_client.py
import re
import time
from collections.abc import Iterable
from datetime import UTC, datetime

import feedparser
import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from vla_wam_daily.models import RawPaper

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ID_RE = re.compile(r"(?P<id>\d{4}\.\d{4,5})(?:v(?P<version>\d+))?$")


class RetryableArxivError(RuntimeError):
    pass


class ArxivWindowTruncatedError(RuntimeError):
    pass


class ArxivClient:
    def __init__(
        self,
        *,
        user_agent: str,
        request_delay_seconds: float = 3.0,
        retries: int = 3,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.request_delay_seconds = request_delay_seconds
        self.retries = retries
        self.http = http_client or httpx.Client(
            timeout=30,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        )

    def _request(self, params: dict[str, str | int]) -> str:
        retrying = Retrying(
            stop=stop_after_attempt(self.retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(
                (httpx.TimeoutException, httpx.NetworkError, RetryableArxivError)
            ),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                response = self.http.get(ARXIV_API_URL, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableArxivError(f"arXiv returned {response.status_code}")
                response.raise_for_status()
                return response.text
        raise AssertionError("retry loop ended without returning")

    @staticmethod
    def _parse_feed(xml: str) -> list[RawPaper]:
        parsed = feedparser.parse(xml)
        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise ValueError(f"invalid arXiv feed: {parsed.bozo_exception}")
        papers: list[RawPaper] = []
        for entry in parsed.entries:
            match = ID_RE.search(entry.id)
            if match is None:
                continue
            authors = [author.name for author in entry.authors]
            categories = [tag.term for tag in entry.tags]
            papers.append(
                RawPaper(
                    arxiv_id=match.group("id"),
                    version=int(match.group("version") or "1"),
                    published_at=datetime.fromisoformat(entry.published.replace("Z", "+00:00")),
                    updated_at=datetime.fromisoformat(entry.updated.replace("Z", "+00:00")),
                    title=" ".join(entry.title.split()),
                    authors=authors,
                    arxiv_categories=list(dict.fromkeys(categories)),
                    abstract=" ".join(entry.summary.split()),
                    comment=getattr(entry, "arxiv_comment", None),
                )
            )
        return papers

    def fetch_recent(
        self,
        *,
        categories: list[str],
        since: datetime,
        until: datetime,
        max_results_per_category: int,
    ) -> list[RawPaper]:
        papers: dict[tuple[str, int], RawPaper] = {}
        since_utc = since.astimezone(UTC)
        until_utc = until.astimezone(UTC)
        page_size = 100
        for category_index, category in enumerate(categories):
            start = 0
            while start < max_results_per_category:
                requested = min(page_size, max_results_per_category - start)
                xml = self._request(
                    {
                        "search_query": f"cat:{category}",
                        "start": start,
                        "max_results": requested,
                        "sortBy": "lastUpdatedDate",
                        "sortOrder": "descending",
                    }
                )
                page = self._parse_feed(xml)
                for paper in page:
                    if since_utc <= paper.updated_at <= until_utc:
                        papers[(paper.arxiv_id, paper.version)] = paper
                page_is_complete = len(page) == requested
                oldest_update = min((paper.updated_at for paper in page), default=since_utc)
                if not page_is_complete or oldest_update < since_utc:
                    break
                if start + requested >= max_results_per_category:
                    raise ArxivWindowTruncatedError(
                        f"{category} exceeded {max_results_per_category} results in the time window"
                    )
                start += requested
                if self.request_delay_seconds:
                    time.sleep(self.request_delay_seconds)
            if category_index < len(categories) - 1 and self.request_delay_seconds:
                time.sleep(self.request_delay_seconds)
        return sorted(papers.values(), key=lambda paper: paper.updated_at, reverse=True)

    def fetch_by_ids(self, arxiv_ids: Iterable[str]) -> list[RawPaper]:
        ids = list(dict.fromkeys(arxiv_ids))
        if not ids:
            return []
        xml = self._request({"id_list": ",".join(ids), "max_results": len(ids)})
        return self._parse_feed(xml)
```

- [ ] **Step 5: Run client tests and full static checks**

Run:

```bash
uv run pytest tests/test_arxiv_client.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit the arXiv client**

```bash
git add src/vla_wam_daily/arxiv_client.py tests/fixtures/arxiv_feed.xml tests/test_arxiv_client.py
git commit -m "feat: fetch recent arXiv papers"
```

## Task 7: Implement the DeepSeek JSON Output client

**Files:**

- Create: `src/vla_wam_daily/deepseek_client.py`
- Create: `tests/test_deepseek_client.py`

- [ ] **Step 1: Write failing transport and parsing tests**

```python
# tests/test_deepseek_client.py
import httpx
import pytest
import respx

from vla_wam_daily.deepseek_client import DeepSeekClient, DeepSeekResponseError


@respx.mock
def test_client_requests_json_output_and_collects_usage() -> None:
    route = respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"title_zh":"中文标题","analysis":{"relevance_score":8}}'
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )
    )
    client = DeepSeekClient(api_key="test-key", model="deepseek-v4-pro", retries=1)
    payload, usage = client.analyze(system_prompt="Return JSON.", paper_json='{"title":"x"}')
    request = route.calls.last.request
    assert b'"response_format":{"type":"json_object"}' in request.content
    assert b'"thinking":{"type":"disabled"}' in request.content
    assert payload["title_zh"] == "中文标题"
    assert usage.total_tokens == 120


@respx.mock
def test_empty_content_is_an_error() -> None:
    respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
    )
    client = DeepSeekClient(api_key="test-key", model="deepseek-v4-pro", retries=1)
    with pytest.raises(DeepSeekResponseError, match="empty"):
        client.analyze(system_prompt="Return JSON.", paper_json='{"title":"x"}')
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_deepseek_client.py -v
```

Expected: FAIL because `deepseek_client.py` does not exist.

- [ ] **Step 3: Implement retrying JSON transport**

```python
# src/vla_wam_daily/deepseek_client.py
import json
from typing import cast

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from vla_wam_daily.models import TokenUsage


class RetryableDeepSeekError(RuntimeError):
    pass


class DeepSeekResponseError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_output_tokens: int = 1800,
        retries: int = 3,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.retries = retries
        self.http = http_client or httpx.Client(
            base_url="https://api.deepseek.com",
            timeout=90,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def analyze(self, *, system_prompt: str, paper_json: str) -> tuple[dict[str, object], TokenUsage]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": paper_json},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": self.max_output_tokens,
            "stream": False,
        }
        retrying = Retrying(
            stop=stop_after_attempt(self.retries),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            retry=retry_if_exception_type(
                (httpx.TimeoutException, httpx.NetworkError, RetryableDeepSeekError)
            ),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                response = self.http.post("/chat/completions", json=body)
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableDeepSeekError(f"DeepSeek returned {response.status_code}")
                response.raise_for_status()
                payload = response.json()
                content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not isinstance(content, str) or not content.strip():
                    raise DeepSeekResponseError("DeepSeek returned empty content")
                try:
                    decoded = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise DeepSeekResponseError("DeepSeek returned invalid JSON") from exc
                if not isinstance(decoded, dict):
                    raise DeepSeekResponseError("DeepSeek JSON root must be an object")
                usage = TokenUsage.model_validate(payload.get("usage", {}))
                return cast(dict[str, object], decoded), usage
        raise AssertionError("retry loop ended without returning")
```

- [ ] **Step 4: Run tests and security-focused checks**

Run:

```bash
uv run pytest tests/test_deepseek_client.py -v
uv run ruff check src tests
uv run mypy
rg -n "api_key.*print|Authorization.*print" src tests
```

Expected: tests and checks pass; `rg` prints no matches.

- [ ] **Step 5: Commit the DeepSeek client**

```bash
git add src/vla_wam_daily/deepseek_client.py tests/test_deepseek_client.py
git commit -m "feat: call DeepSeek with JSON output"
```

## Task 8: Convert AI responses into traceable paper records

**Files:**

- Create: `src/vla_wam_daily/analyzer.py`
- Create: `tests/test_analyzer.py`

- [ ] **Step 1: Write failing analyzer tests with a fake client**

```python
# tests/test_analyzer.py
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from vla_wam_daily.analyzer import analyze_paper
from vla_wam_daily.models import RawPaper, TokenUsage


class FakeClient:
    model = "deepseek-v4-pro"

    def __init__(self, score: int = 8) -> None:
        self.score = score

    def analyze(self, *, system_prompt: str, paper_json: str) -> tuple[dict[str, object], TokenUsage]:
        assert "Return one valid JSON object" in system_prompt
        assert "2607.12345" in paper_json
        return (
            {
                "title_zh": "用于机器人操作的视觉语言动作策略",
                "analysis": {
                    "relevance_score": self.score,
                    "primary_topic": "VLA",
                    "tags": ["Vision-Language", "Robot Manipulation"],
                    "one_sentence_summary": "提出一种机器人多模态策略。",
                    "main_contribution": "统一视觉、语言与动作。",
                    "method": "多模态策略学习。",
                    "key_results": "摘要未说明",
                    "limitations": "摘要未说明",
                    "relation_to_vla_wam": "直接属于 VLA。",
                },
            },
            TokenUsage(prompt_tokens=100, completion_tokens=30, total_tokens=130),
        )


def raw_paper() -> RawPaper:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    return RawPaper(
        arxiv_id="2607.12345",
        version=1,
        published_at=now,
        updated_at=now,
        title="A Vision-Language-Action Policy",
        authors=["Ada Robot"],
        arxiv_categories=["cs.RO"],
        abstract="We introduce a vision-language-action policy.",
    )


def test_analyzer_builds_record_and_provenance() -> None:
    record, usage = analyze_paper(
        paper=raw_paper(),
        matched_rules=["exact:vision-language-action"],
        client=FakeClient(),
        prompt="Return one valid JSON object.",
        prompt_version="1",
        analyzed_at=datetime(2026, 7, 27, 2, 0, tzinfo=UTC),
    )
    assert record.title_zh.startswith("用于")
    assert record.provenance.model == "deepseek-v4-pro"
    assert record.resources.code_url is None
    assert usage.total_tokens == 130


def test_analyzer_rejects_invalid_score() -> None:
    with pytest.raises(ValidationError):
        analyze_paper(
            paper=raw_paper(),
            matched_rules=["exact:vision-language-action"],
            client=FakeClient(score=12),
            prompt="Return one valid JSON object.",
            prompt_version="1",
            analyzed_at=datetime(2026, 7, 27, 2, 0, tzinfo=UTC),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_analyzer.py -v
```

Expected: FAIL because `analyzer.py` does not exist.

- [ ] **Step 3: Implement prompt input and record construction**

```python
# src/vla_wam_daily/analyzer.py
import json
from datetime import datetime
from typing import Protocol

from vla_wam_daily.models import AIOutput, PaperRecord, Provenance, RawPaper, TokenUsage
from vla_wam_daily.resources import extract_resources


class AnalysisClient(Protocol):
    model: str

    def analyze(self, *, system_prompt: str, paper_json: str) -> tuple[dict[str, object], TokenUsage]:
        pass


def analyze_paper(
    *,
    paper: RawPaper,
    matched_rules: list[str],
    client: AnalysisClient,
    prompt: str,
    prompt_version: str,
    analyzed_at: datetime,
) -> tuple[PaperRecord, TokenUsage]:
    input_payload = {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "abstract": paper.abstract,
        "arxiv_categories": paper.arxiv_categories,
        "matched_rules": matched_rules,
    }
    payload, usage = client.analyze(
        system_prompt=prompt,
        paper_json=json.dumps(input_payload, ensure_ascii=False),
    )
    output = AIOutput.model_validate(payload)
    record = PaperRecord(
        arxiv_id=paper.arxiv_id,
        version=paper.version,
        published_at=paper.published_at,
        updated_at=paper.updated_at,
        title=paper.title,
        title_zh=output.title_zh,
        authors=paper.authors,
        arxiv_categories=paper.arxiv_categories,
        abstract=paper.abstract,
        matched_rules=matched_rules,
        analysis=output.analysis,
        resources=extract_resources(paper.arxiv_id, paper.abstract, paper.comment),
        provenance=Provenance(
            analysis_scope="title_and_abstract",
            model=client.model,
            prompt_version=prompt_version,
            analyzed_at=analyzed_at,
        ),
    )
    return record, usage
```

- [ ] **Step 4: Run analyzer tests and all Python tests**

Run:

```bash
uv run pytest tests/test_analyzer.py -v
uv run pytest
uv run ruff check src tests
uv run mypy
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the analyzer**

```bash
git add src/vla_wam_daily/analyzer.py tests/test_analyzer.py
git commit -m "feat: create traceable AI analyses"
```

## Task 9: Add cache, latest data, monthly archives, and atomic writes

**Files:**

- Create: `src/vla_wam_daily/storage.py`
- Create: `tests/test_storage.py`
- Create: `data/latest.json`
- Create: `data/cache/analyses.json`
- Create: `data/archive/.gitkeep`
- Create: `tests/fixtures/data/latest.json`
- Create: `tests/fixtures/data/cache/analyses.json`
- Create: `tests/fixtures/data/archive/2026-07.json`

- [ ] **Step 1: Write failing storage tests**

```python
# tests/test_storage.py
import json
from datetime import UTC, datetime

from tests.factories import make_record
from vla_wam_daily.models import CacheEntry, RunStats
from vla_wam_daily.storage import cache_key, load_cache, save_successful_run


def test_cache_key_changes_with_version_model_and_prompt() -> None:
    first = cache_key("2607.12345", 1, "deepseek-v4-pro", "1")
    assert first != cache_key("2607.12345", 2, "deepseek-v4-pro", "1")
    assert first != cache_key("2607.12345", 1, "deepseek-v4-flash", "1")
    assert first != cache_key("2607.12345", 1, "deepseek-v4-pro", "2")


def test_save_is_idempotent_and_preserves_paper_versions(tmp_path) -> None:
    first = make_record(version=1)
    second = make_record(version=2)
    cache = {
        cache_key(first.arxiv_id, first.version, first.provenance.model, "1"): CacheEntry(
            key=cache_key(first.arxiv_id, first.version, first.provenance.model, "1"),
            record=first,
        )
    }
    now = datetime(2026, 7, 27, tzinfo=UTC)
    save_successful_run(tmp_path, [first], cache, RunStats(published=1), now)
    save_successful_run(tmp_path, [first, second], cache, RunStats(published=2), now)
    archive = json.loads((tmp_path / "archive/2026-07.json").read_text())
    assert [(paper["arxiv_id"], paper["version"]) for paper in archive["papers"]] == [
        ("2607.12345", 2),
        ("2607.12345", 1),
    ]
    assert load_cache(tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_storage.py -v
```

Expected: FAIL because `storage.py` does not exist.

- [ ] **Step 3: Implement atomic JSON storage**

```python
# src/vla_wam_daily/storage.py
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from vla_wam_daily.models import CacheEntry, DataFile, PaperRecord, RunStats


def cache_key(arxiv_id: str, version: int, model: str, prompt_version: str) -> str:
    return f"{arxiv_id}:v{version}:{model}:prompt-{prompt_version}"


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def load_data_file(path: Path) -> DataFile | None:
    if not path.exists():
        return None
    return DataFile.model_validate_json(path.read_text(encoding="utf-8"))


def load_cache(data_dir: Path) -> dict[str, CacheEntry]:
    path = data_dir / "cache/analyses.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {key: CacheEntry.model_validate(value) for key, value in raw.items()}


def merge_records(existing: list[PaperRecord], incoming: list[PaperRecord]) -> list[PaperRecord]:
    merged = {(paper.arxiv_id, paper.version): paper for paper in existing}
    for paper in incoming:
        merged[(paper.arxiv_id, paper.version)] = paper
    return sorted(
        merged.values(),
        key=lambda paper: (paper.published_at, paper.updated_at, paper.version),
        reverse=True,
    )


def save_successful_run(
    data_dir: Path,
    published: list[PaperRecord],
    cache: dict[str, CacheEntry],
    stats: RunStats,
    generated_at: datetime,
) -> None:
    latest = DataFile(generated_at=generated_at, stats=stats, papers=published)
    pending: dict[Path, object] = {
        data_dir / "latest.json": latest.model_dump(mode="json"),
        data_dir / "cache/analyses.json": {
            key: value.model_dump(mode="json") for key, value in sorted(cache.items())
        },
    }
    by_month: dict[str, list[PaperRecord]] = {}
    for paper in published:
        by_month.setdefault(paper.published_at.strftime("%Y-%m"), []).append(paper)
    for month, records in by_month.items():
        path = data_dir / f"archive/{month}.json"
        current = load_data_file(path)
        merged = merge_records(current.papers if current else [], records)
        pending[path] = DataFile(
            generated_at=generated_at,
            stats=stats,
            papers=merged,
        ).model_dump(mode="json")
    for path, payload in pending.items():
        atomic_write_json(path, payload)
```

- [ ] **Step 4: Seed valid empty data**

Create `data/latest.json` with this exact JSON:

```json
{
  "schema_version": "1",
  "generated_at": "2026-07-27T00:00:00Z",
  "stats": {
    "fetched": 0,
    "prefiltered": 0,
    "cache_hits": 0,
    "model_calls": 0,
    "published": 0,
    "failed": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "error_categories": {}
  },
  "papers": []
}
```

Create `data/cache/analyses.json` with this exact JSON:

```json
{}
```

Create an empty `data/archive/.gitkeep`, then generate isolated browser-test fixture data:

```bash
uv run python -c "from datetime import UTC, datetime; from pathlib import Path; from tests.factories import make_record; from vla_wam_daily.models import CacheEntry, RunStats; from vla_wam_daily.storage import cache_key, save_successful_run; r=make_record(); k=cache_key(r.arxiv_id,r.version,r.provenance.model,r.provenance.prompt_version); save_successful_run(Path('tests/fixtures/data'),[r],{k:CacheEntry(key=k,record=r)},RunStats(fetched=1,prefiltered=1,model_calls=1,published=1),datetime(2026,7,27,tzinfo=UTC))"
```

- [ ] **Step 5: Run storage tests and validate seed data**

Run:

```bash
uv run pytest tests/test_storage.py -v
uv run python -c "from pathlib import Path; from vla_wam_daily.storage import load_data_file; assert load_data_file(Path('data/latest.json'))"
uv run ruff check src tests
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit storage**

```bash
git add src/vla_wam_daily/storage.py tests/test_storage.py tests/fixtures/data data
git commit -m "feat: persist cached monthly paper data"
```

## Task 10: Orchestrate the daily run and expose the CLI

**Files:**

- Create: `src/vla_wam_daily/pipeline.py`
- Create: `src/vla_wam_daily/cli.py`
- Create: `tests/test_pipeline.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing idempotency and quality-gate tests**

```python
# tests/test_pipeline.py
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.test_analyzer import FakeClient, raw_paper
from vla_wam_daily.config import load_config
from vla_wam_daily.pipeline import CandidateLimitError, QualityGateError, run_daily


class FakeFetcher:
    def __init__(self, papers: list[object]) -> None:
        self.papers = papers

    def fetch_recent(self, **kwargs: object) -> list[object]:
        return self.papers

    def fetch_by_ids(self, arxiv_ids: list[str]) -> list[object]:
        return [paper for paper in self.papers if paper.arxiv_id in arxiv_ids]


class CountingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def analyze(self, *, system_prompt: str, paper_json: str):
        self.calls += 1
        return super().analyze(system_prompt=system_prompt, paper_json=paper_json)


class FailingClient(FakeClient):
    def analyze(self, *, system_prompt: str, paper_json: str):
        raise RuntimeError("simulated model failure")


def test_second_run_uses_cache_without_model_call(tmp_path: Path) -> None:
    config = load_config(Path("config/topics.yaml"))
    client = CountingClient()
    kwargs = {
        "config": config,
        "data_dir": tmp_path,
        "fetcher": FakeFetcher([raw_paper()]),
        "analysis_client": client,
        "prompt": Path("prompts/analysis-v1.md").read_text(),
        "lookback_days": 3,
        "threshold": 6,
        "force_ids": [],
        "dry_run": False,
        "now": datetime(2026, 7, 27, 2, 30, tzinfo=UTC),
    }
    first = run_daily(**kwargs)
    second = run_daily(**kwargs)
    assert first.stats.model_calls == 1
    assert second.stats.cache_hits == 1
    assert client.calls == 1


def test_candidate_limit_stops_before_api_calls(tmp_path: Path) -> None:
    config = load_config(Path("config/topics.yaml"))
    config.analysis.max_candidates = 1
    first = raw_paper()
    second = first.model_copy(update={"arxiv_id": "2607.54321"})
    client = CountingClient()
    with pytest.raises(CandidateLimitError):
        run_daily(
            config=config,
            data_dir=tmp_path,
            fetcher=FakeFetcher([first, second]),
            analysis_client=client,
            prompt=Path("prompts/analysis-v1.md").read_text(),
            lookback_days=3,
            threshold=6,
            force_ids=[],
            dry_run=False,
            now=datetime(2026, 7, 27, 2, 30, tzinfo=UTC),
        )
    assert client.calls == 0


def test_failure_ratio_prevents_storage_update(tmp_path: Path) -> None:
    config = load_config(Path("config/topics.yaml"))
    with pytest.raises(QualityGateError):
        run_daily(
            config=config,
            data_dir=tmp_path,
            fetcher=FakeFetcher([raw_paper()]),
            analysis_client=FailingClient(),
            prompt=Path("prompts/analysis-v1.md").read_text(),
            lookback_days=3,
            threshold=6,
            force_ids=[],
            dry_run=False,
            now=datetime(2026, 7, 27, 2, 30, tzinfo=UTC),
        )
    assert not (tmp_path / "latest.json").exists()
```

- [ ] **Step 2: Run pipeline tests to verify they fail**

Run:

```bash
uv run pytest tests/test_pipeline.py -v
```

Expected: FAIL because `pipeline.py` does not exist.

- [ ] **Step 3: Implement orchestration, cache reuse, limits, and dry-run**

```python
# src/vla_wam_daily/pipeline.py
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from vla_wam_daily.analyzer import AnalysisClient, analyze_paper
from vla_wam_daily.config import AppConfig
from vla_wam_daily.models import CacheEntry, PaperRecord, RawPaper, RunStats, TokenUsage
from vla_wam_daily.prefilter import match_paper
from vla_wam_daily.storage import cache_key, load_cache, save_successful_run


class CandidateLimitError(RuntimeError):
    pass


class QualityGateError(RuntimeError):
    pass


class Fetcher(Protocol):
    def fetch_recent(
        self,
        *,
        categories: list[str],
        since: datetime,
        until: datetime,
        max_results_per_category: int,
    ) -> list[RawPaper]:
        pass

    def fetch_by_ids(self, arxiv_ids: Iterable[str]) -> list[RawPaper]:
        pass


class RunReport:
    def __init__(self, *, stats: RunStats, published: list[PaperRecord], dry_run: bool) -> None:
        self.stats = stats
        self.published = published
        self.dry_run = dry_run


def run_daily(
    *,
    config: AppConfig,
    data_dir: Path,
    fetcher: Fetcher,
    analysis_client: AnalysisClient,
    prompt: str,
    lookback_days: int,
    threshold: int,
    force_ids: list[str],
    dry_run: bool,
    now: datetime,
) -> RunReport:
    recent = fetcher.fetch_recent(
        categories=config.arxiv.categories,
        since=now - timedelta(days=lookback_days),
        until=now,
        max_results_per_category=config.arxiv.max_results_per_category,
    )
    forced = fetcher.fetch_by_ids(force_ids)
    papers_by_key = {(paper.arxiv_id, paper.version): paper for paper in recent + forced}
    papers = list(papers_by_key.values())

    matched = [
        (paper, match_paper(paper, config.prefilter))
        for paper in papers
    ]
    candidates = [(paper, rules) for paper, rules in matched if rules or paper.arxiv_id in force_ids]
    if len(candidates) > config.analysis.max_candidates:
        raise CandidateLimitError(
            f"{len(candidates)} candidates exceeds limit {config.analysis.max_candidates}"
        )

    cache = load_cache(data_dir)
    records: list[PaperRecord] = []
    pending: list[tuple[RawPaper, list[str], str]] = []
    cache_hits = 0
    for paper, rules in candidates:
        key = cache_key(
            paper.arxiv_id,
            paper.version,
            analysis_client.model,
            config.analysis.prompt_version,
        )
        if key in cache and paper.arxiv_id not in force_ids:
            records.append(cache[key].record)
            cache_hits += 1
        else:
            pending.append((paper, rules, key))

    failures = 0
    error_categories: dict[str, int] = {}
    usage = TokenUsage()
    with ThreadPoolExecutor(max_workers=config.analysis.max_concurrency) as executor:
        futures = {
            executor.submit(
                analyze_paper,
                paper=paper,
                matched_rules=rules,
                client=analysis_client,
                prompt=prompt,
                prompt_version=config.analysis.prompt_version,
                analyzed_at=now,
            ): key
            for paper, rules, key in pending
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                record, item_usage = future.result()
            except Exception as exc:
                failures += 1
                category = type(exc).__name__
                error_categories[category] = error_categories.get(category, 0) + 1
                continue
            records.append(record)
            cache[key] = CacheEntry(key=key, record=record)
            usage = TokenUsage(
                prompt_tokens=usage.prompt_tokens + item_usage.prompt_tokens,
                completion_tokens=usage.completion_tokens + item_usage.completion_tokens,
                total_tokens=usage.total_tokens + item_usage.total_tokens,
            )

    attempted = len(pending)
    failure_ratio = failures / attempted if attempted else 0.0
    if failure_ratio > config.analysis.max_failure_ratio:
        raise QualityGateError(
            f"analysis failure ratio {failure_ratio:.1%} exceeds "
            f"{config.analysis.max_failure_ratio:.1%}"
        )

    published = sorted(
        [record for record in records if record.analysis.relevance_score >= threshold],
        key=lambda record: (
            record.published_at,
            record.analysis.relevance_score,
            record.arxiv_id,
        ),
        reverse=True,
    )
    stats = RunStats(
        fetched=len(papers),
        prefiltered=len(candidates),
        cache_hits=cache_hits,
        model_calls=attempted,
        published=len(published),
        failed=failures,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        error_categories=error_categories,
    )
    if not dry_run:
        save_successful_run(data_dir, published, cache, stats, now)
    return RunReport(stats=stats, published=published, dry_run=dry_run)
```

- [ ] **Step 4: Write failing CLI help test**

```python
# tests/test_cli.py
from typer.testing import CliRunner

from vla_wam_daily.cli import app


def test_cli_exposes_daily_options() -> None:
    result = CliRunner().invoke(app, ["daily", "--help"])
    assert result.exit_code == 0
    assert "--profile" in result.stdout
    assert "--lookback-days" in result.stdout
    assert "--force-arxiv-id" in result.stdout
    assert "--dry-run" in result.stdout
```

- [ ] **Step 5: Run the CLI test to verify it fails**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: FAIL because `cli.py` does not exist.

- [ ] **Step 6: Implement the CLI and run report**

```python
# src/vla_wam_daily/cli.py
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from vla_wam_daily.arxiv_client import ArxivClient
from vla_wam_daily.config import load_config
from vla_wam_daily.deepseek_client import DeepSeekClient
from vla_wam_daily.pipeline import run_daily

app = typer.Typer(no_args_is_help=True)


@app.command()
def daily(
    profile: Annotated[str, typer.Option()] = "quality",
    lookback_days: Annotated[int, typer.Option(min=1, max=31)] = 3,
    threshold: Annotated[int, typer.Option(min=1, max=10)] = 6,
    force_arxiv_id: Annotated[list[str] | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
    config_path: Annotated[Path, typer.Option()] = Path("config/topics.yaml"),
    data_dir: Annotated[Path, typer.Option()] = Path("data"),
    prompt_path: Annotated[Path, typer.Option()] = Path("prompts/analysis-v1.md"),
) -> None:
    config = load_config(config_path)
    model = os.getenv("DEEPSEEK_MODEL") or config.analysis.model_for(profile)
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise typer.BadParameter("DEEPSEEK_API_KEY is required")
    user_agent = os.getenv(
        "ARXIV_USER_AGENT",
        "VLA-WAM-Daily/0.1 (https://github.com/vla-wam-daily/vla-wam-daily)",
    )
    report = run_daily(
        config=config,
        data_dir=data_dir,
        fetcher=ArxivClient(
            user_agent=user_agent,
            request_delay_seconds=config.arxiv.request_delay_seconds,
        ),
        analysis_client=DeepSeekClient(
            api_key=api_key,
            model=model,
            max_output_tokens=config.analysis.max_output_tokens,
        ),
        prompt=prompt_path.read_text(encoding="utf-8"),
        lookback_days=lookback_days,
        threshold=threshold,
        force_ids=force_arxiv_id or [],
        dry_run=dry_run,
        now=datetime.now(UTC),
    )
    typer.echo(
        json.dumps(
            {
                "dry_run": report.dry_run,
                "stats": report.stats.model_dump(mode="json"),
                "published_ids": [paper.arxiv_id for paper in report.published],
            },
            ensure_ascii=False,
        )
    )
```

- [ ] **Step 7: Run orchestration tests and CLI smoke checks**

Run:

```bash
uv run pytest tests/test_pipeline.py tests/test_cli.py -v
uv run vla-wam-daily --help
uv run pytest
uv run ruff check src tests
uv run mypy
```

Expected: all commands exit 0; CLI help lists `daily`.

- [ ] **Step 8: Commit the daily pipeline**

```bash
git add src/vla_wam_daily/pipeline.py src/vla_wam_daily/cli.py tests/test_pipeline.py tests/test_cli.py
git commit -m "feat: orchestrate idempotent daily updates"
```

## Task 11: Scaffold Astro and validate Python-generated data

**Files:**

- Create: `web/package.json`
- Create: `web/pnpm-lock.yaml`
- Create: `web/astro.config.mjs`
- Create: `web/tsconfig.json`
- Create: `web/src/env.d.ts`
- Create: `web/src/lib/schema.ts`
- Create: `web/src/lib/data.ts`
- Create: `web/src/lib/data.test.ts`
- Create: `web/src/layouts/BaseLayout.astro`
- Create: `web/src/components/Header.astro`
- Create: `web/src/components/PaperCard.astro`
- Create: `web/src/pages/index.astro`
- Create: `web/src/pages/papers/[id].astro`
- Create: `web/src/styles/global.css`

- [ ] **Step 1: Create the Astro package without starting development**

Run:

```bash
pnpm create astro@latest web -- --template minimal --install=false --git=false --typescript strict
cd web
pnpm add @astrojs/rss astro zod
pnpm add -D @astrojs/check @playwright/test pagefind prettier prettier-plugin-astro typescript vitest
cd ..
```

Expected: `web/package.json` and `web/pnpm-lock.yaml` exist.

- [ ] **Step 2: Replace scripts and configure GitHub Pages base detection**

Replace `web/package.json` with:

```json
{
  "name": "vla-wam-daily-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "astro dev",
    "build": "astro check && astro build && pagefind --site dist",
    "preview": "astro preview",
    "test": "vitest run",
    "format:check": "prettier --check ."
  },
  "dependencies": {
    "@astrojs/rss": "^4.0.0",
    "astro": "^6.0.0",
    "zod": "^4.0.0"
  },
  "devDependencies": {
    "@astrojs/check": "^0.9.0",
    "@playwright/test": "^1.54.0",
    "pagefind": "^1.4.0",
    "prettier": "^3.6.0",
    "prettier-plugin-astro": "^0.14.0",
    "typescript": "^5.8.0",
    "vitest": "^3.2.0"
  },
  "packageManager": "pnpm@10.14.0"
}
```

After replacing `package.json`, run `cd web && pnpm install && cd ..` so the lockfile matches.

```javascript
// web/astro.config.mjs
import { defineConfig } from "astro/config";

const [owner, repository] = (process.env.GITHUB_REPOSITORY ?? "").split("/");
const site = process.env.SITE_URL ?? (owner ? `https://${owner}.github.io` : "http://localhost:4321");
const base =
  process.env.BASE_PATH ??
  (owner && repository && repository !== `${owner}.github.io` ? `/${repository}` : "/");

export default defineConfig({
  site,
  base,
  output: "static",
});
```

- [ ] **Step 3: Write a failing TypeScript data-loader test**

```typescript
// web/src/lib/data.test.ts
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { loadArchive } from "./data";

describe("loadArchive", () => {
  it("keeps only the newest paper version for current views", async () => {
    const root = await mkdtemp(join(tmpdir(), "vla-wam-data-"));
    await mkdir(join(root, "archive"));
    const paper = {
      arxiv_id: "2607.12345",
      version: 1,
      published_at: "2026-07-27T01:00:00Z",
      updated_at: "2026-07-27T01:00:00Z",
      title: "Paper",
      title_zh: "论文",
      authors: ["Author"],
      arxiv_categories: ["cs.RO"],
      abstract: "Abstract",
      matched_rules: ["exact:vision-language-action"],
      analysis: {
        relevance_score: 8,
        primary_topic: "VLA",
        tags: ["Vision-Language"],
        one_sentence_summary: "总结",
        main_contribution: "贡献",
        method: "方法",
        key_results: "摘要未说明",
        limitations: "摘要未说明",
        relation_to_vla_wam: "直接相关"
      },
      resources: {
        arxiv_url: "https://arxiv.org/abs/2607.12345",
        pdf_url: "https://arxiv.org/pdf/2607.12345",
        project_url: null,
        code_url: null
      },
      provenance: {
        analysis_scope: "title_and_abstract",
        model: "deepseek-v4-pro",
        prompt_version: "1",
        analyzed_at: "2026-07-27T02:00:00Z"
      }
    };
    await writeFile(
      join(root, "archive/2026-07.json"),
      JSON.stringify({
        schema_version: "1",
        generated_at: "2026-07-27T02:00:00Z",
        stats: {
          fetched: 1,
          prefiltered: 1,
          cache_hits: 0,
          model_calls: 1,
          published: 1,
          failed: 0,
          prompt_tokens: 10,
          completion_tokens: 10,
          total_tokens: 20,
          error_categories: {}
        },
        papers: [paper, { ...paper, version: 2, updated_at: "2026-07-28T01:00:00Z" }]
      })
    );
    const papers = await loadArchive(root);
    expect(papers).toHaveLength(1);
    expect(papers[0].version).toBe(2);
  });
});
```

- [ ] **Step 4: Run the test to verify it fails**

Run:

```bash
cd web
pnpm test -- src/lib/data.test.ts
```

Expected: FAIL because `schema.ts` and `data.ts` do not exist.

- [ ] **Step 5: Implement the TypeScript schema and loader**

```typescript
// web/src/lib/schema.ts
import { z } from "zod";

export const topicSchema = z.enum(["VLA", "WAM", "World Model", "Dataset", "Benchmark"]);

export const paperSchema = z.object({
  arxiv_id: z.string(),
  version: z.number().int().positive(),
  published_at: z.string().datetime(),
  updated_at: z.string().datetime(),
  title: z.string(),
  title_zh: z.string(),
  authors: z.array(z.string()),
  arxiv_categories: z.array(z.string()),
  abstract: z.string(),
  matched_rules: z.array(z.string()),
  analysis: z.object({
    relevance_score: z.number().int().min(1).max(10),
    primary_topic: topicSchema,
    tags: z.array(z.string()),
    one_sentence_summary: z.string(),
    main_contribution: z.string(),
    method: z.string(),
    key_results: z.string(),
    limitations: z.string(),
    relation_to_vla_wam: z.string()
  }),
  resources: z.object({
    arxiv_url: z.string().url(),
    pdf_url: z.string().url(),
    project_url: z.string().url().nullable(),
    code_url: z.string().url().nullable()
  }),
  provenance: z.object({
    analysis_scope: z.literal("title_and_abstract"),
    model: z.string(),
    prompt_version: z.string(),
    analyzed_at: z.string().datetime()
  })
});

export const dataFileSchema = z.object({
  schema_version: z.literal("1"),
  generated_at: z.string().datetime(),
  stats: z.object({
    fetched: z.number().int().nonnegative(),
    prefiltered: z.number().int().nonnegative(),
    cache_hits: z.number().int().nonnegative(),
    model_calls: z.number().int().nonnegative(),
    published: z.number().int().nonnegative(),
    failed: z.number().int().nonnegative(),
    prompt_tokens: z.number().int().nonnegative(),
    completion_tokens: z.number().int().nonnegative(),
    total_tokens: z.number().int().nonnegative(),
    error_categories: z.record(z.string(), z.number().int().nonnegative())
  }),
  papers: z.array(paperSchema)
});

export type Topic = z.infer<typeof topicSchema>;
export type Paper = z.infer<typeof paperSchema>;
export type DataFile = z.infer<typeof dataFileSchema>;
```

```typescript
// web/src/lib/data.ts
import { readdir, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { dataFileSchema, type Paper } from "./schema";

export const defaultDataDir = process.env.VLA_WAM_DATA_DIR
  ? resolve(process.env.VLA_WAM_DATA_DIR)
  : resolve(dirname(fileURLToPath(import.meta.url)), "../../../data");

export async function loadArchive(dataDir = defaultDataDir): Promise<Paper[]> {
  const archiveDir = join(dataDir, "archive");
  const names = (await readdir(archiveDir)).filter((name) => name.endsWith(".json")).sort();
  const all: Paper[] = [];
  for (const name of names) {
    const parsed = dataFileSchema.parse(
      JSON.parse(await readFile(join(archiveDir, name), "utf8"))
    );
    all.push(...parsed.papers);
  }
  const newest = new Map<string, Paper>();
  for (const paper of all) {
    const current = newest.get(paper.arxiv_id);
    if (!current || paper.version > current.version) {
      newest.set(paper.arxiv_id, paper);
    }
  }
  return [...newest.values()].sort(
    (left, right) =>
      Date.parse(right.published_at) - Date.parse(left.published_at) ||
      right.analysis.relevance_score - left.analysis.relevance_score
  );
}

export async function loadLatest(dataDir = defaultDataDir): Promise<Paper[]> {
  return (await loadLatestDataFile(dataDir)).papers;
}

export async function loadLatestDataFile(dataDir = defaultDataDir) {
  return dataFileSchema.parse(
    JSON.parse(await readFile(join(dataDir, "latest.json"), "utf8"))
  );
}
```

- [ ] **Step 6: Build the base layout, header, cards, home, and detail pages**

Create `web/src/components/PaperCard.astro`:

```astro
---
import type { Paper } from "../lib/schema";
interface Props { paper: Paper; compact?: boolean }
const { paper, compact = false } = Astro.props;
const base = import.meta.env.BASE_URL;
---
<article class="paper-card" data-paper-card data-topic={paper.analysis.primary_topic}
  data-score={paper.analysis.relevance_score} data-code={paper.resources.code_url ? "yes" : "no"}>
  <div class="paper-card__meta">
    <span class="topic">{paper.analysis.primary_topic}</span>
    <span class="score">{paper.analysis.relevance_score}/10</span>
    <time datetime={paper.published_at}>{paper.published_at.slice(0, 10)}</time>
  </div>
  <h2><a href={`${base}papers/${paper.arxiv_id}/`}>{paper.title}</a></h2>
  <p class="title-zh">{paper.title_zh}</p>
  <p class="authors">{paper.authors.join(", ")}</p>
  <p class="summary">{paper.analysis.one_sentence_summary}</p>
  {!compact && (
    <details>
      <summary>结构化分析</summary>
      <dl>
        <dt>核心贡献</dt><dd>{paper.analysis.main_contribution}</dd>
        <dt>方法</dt><dd>{paper.analysis.method}</dd>
        <dt>实验结果</dt><dd>{paper.analysis.key_results}</dd>
        <dt>局限</dt><dd>{paper.analysis.limitations}</dd>
        <dt>与 VLA/WAM 的关系</dt><dd>{paper.analysis.relation_to_vla_wam}</dd>
      </dl>
    </details>
  )}
  <nav class="paper-links" aria-label="论文资源">
    <a href={paper.resources.arxiv_url}>arXiv</a>
    <a href={paper.resources.pdf_url}>PDF</a>
    {paper.resources.project_url && <a href={paper.resources.project_url}>Project</a>}
    {paper.resources.code_url && <a href={paper.resources.code_url}>Code</a>}
  </nav>
  <small>AI 分析仅基于标题与摘要 · {paper.provenance.model}</small>
</article>
```

Create `web/src/layouts/BaseLayout.astro`:

```astro
---
import Header from "../components/Header.astro";
import "../styles/global.css";

interface Props {
  title?: string;
  description?: string;
}

const {
  title = "VLA/WAM Daily",
  description = "Daily Vision-Language-Action and World Action Model research"
} = Astro.props;
const canonical = new URL(Astro.url.pathname, Astro.site ?? Astro.url.origin);
const base = import.meta.env.BASE_URL;
---
<!doctype html>
<html lang="zh-CN" data-base={base}>
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width" />
    <meta name="description" content={description} />
    <meta name="color-scheme" content="light dark" />
    <link rel="canonical" href={canonical} />
    <link rel="alternate" type="application/rss+xml" title="VLA/WAM Daily RSS"
      href={`${base}rss.xml`} />
    <title>{title}</title>
    <slot name="head" />
  </head>
  <body>
    <a class="skip-link" href="#content">跳到正文</a>
    <Header />
    <main id="content"><slot /></main>
    <footer>
      <p>Paper metadata from arXiv · AI analysis is based on titles and abstracts only.</p>
    </footer>
  </body>
</html>
```

Create `web/src/components/Header.astro`:

```astro
---
const base = import.meta.env.BASE_URL;
const links = [
  ["Today", ""],
  ["VLA", "topics/vla/"],
  ["WAM", "topics/wam/"],
  ["World Models", "topics/world-model/"],
  ["Datasets", "topics/dataset/"],
  ["Benchmarks", "topics/benchmark/"],
  ["Weekly Top 5", "weekly/"],
  ["Archive", "archive/"],
  ["Search", "search/"],
  ["RSS", "rss.xml"]
];
---
<header class="site-header">
  <a class="brand" href={base}>VLA/WAM Daily</a>
  <nav aria-label="主导航">
    {links.map(([label, path]) => <a href={`${base}${path}`}>{label}</a>)}
  </nav>
</header>
```

Create `web/src/pages/index.astro`:

```astro
---
import PaperCard from "../components/PaperCard.astro";
import BaseLayout from "../layouts/BaseLayout.astro";
import { loadLatestDataFile } from "../lib/data";

const latest = await loadLatestDataFile();
const papers = latest.papers;
const updated = latest.generated_at.slice(0, 10);
---
<BaseLayout>
  <section class="hero">
    <p class="eyebrow">Daily robotics research signal</p>
    <h1>VLA/WAM Daily</h1>
    <p>Tracking Vision-Language-Action and World Action Models</p>
    <small>最近更新：{updated}</small>
  </section>
  {papers.length ? (
    <section class="paper-grid" aria-label="最新论文">
      {papers.map((paper) => <PaperCard paper={paper} />)}
    </section>
  ) : (
    <p class="empty-state">尚无符合发布阈值的论文。每日任务成功后会自动更新。</p>
  )}
</BaseLayout>
```

Create `web/src/pages/papers/[id].astro`:

```astro
---
import PaperCard from "../../components/PaperCard.astro";
import BaseLayout from "../../layouts/BaseLayout.astro";
import { loadArchive } from "../../lib/data";
import type { Paper } from "../../lib/schema";

export async function getStaticPaths() {
  const papers = await loadArchive();
  return papers.map((paper) => ({
    params: { id: paper.arxiv_id },
    props: { paper }
  }));
}

interface Props { paper: Paper }
const { paper } = Astro.props;
---
<BaseLayout
  title={`${paper.title} · VLA/WAM Daily`}
  description={paper.analysis.one_sentence_summary}
>
  <h1>{paper.title}</h1>
  <article data-pagefind-body>
    <PaperCard paper={paper} />
    <section class="abstract">
      <h2>Original abstract</h2>
      <p>{paper.abstract}</p>
    </section>
  </article>
</BaseLayout>
```

Create `global.css` with these concrete design tokens and responsive behavior:

```css
:root {
  --paper: #f5f1e8;
  --paper-raised: #fffdf7;
  --ink: #14201d;
  --muted: #60706a;
  --line: #d8d1c3;
  --accent: #c6532f;
  --teal: #1f6c64;
  --shadow: 0 18px 50px rgb(20 32 29 / 8%);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
    sans-serif;
  color: var(--ink);
  background: var(--paper);
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #101715;
    --paper-raised: #18221f;
    --ink: #edf2ec;
    --muted: #a8b5af;
    --line: #34423d;
    --accent: #ef8a62;
    --teal: #68b9ae;
  }
}
* { box-sizing: border-box; }
body { margin: 0; line-height: 1.6; }
a { color: var(--teal); text-underline-offset: 0.2em; }
main { width: min(76rem, calc(100% - 2rem)); margin: 0 auto; padding: 2rem 0 5rem; }
.skip-link { position: absolute; left: -9999px; }
.skip-link:focus { left: 1rem; top: 1rem; z-index: 10; background: var(--paper-raised); padding: 0.5rem; }
.site-header {
  width: min(76rem, calc(100% - 2rem));
  margin: 0 auto;
  padding: 1rem 0;
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: center;
}
.site-header nav { display: flex; flex-wrap: wrap; gap: 0.75rem; }
.brand { color: var(--ink); font-weight: 800; text-decoration: none; }
.hero { padding: clamp(2rem, 8vw, 7rem) 0; max-width: 55rem; }
.hero h1 { font-size: clamp(3rem, 9vw, 7rem); line-height: 0.9; letter-spacing: -0.06em; margin: 0.2em 0; }
.eyebrow { color: var(--accent); text-transform: uppercase; letter-spacing: 0.12em; font-weight: 750; }
.paper-grid { display: grid; gap: 1rem; }
.paper-card {
  border: 1px solid var(--line);
  border-radius: 1rem;
  background: var(--paper-raised);
  box-shadow: var(--shadow);
  padding: clamp(1rem, 2vw, 1.5rem);
}
.paper-card__meta, .paper-links { display: flex; flex-wrap: wrap; gap: 0.75rem; }
.topic, .score {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 0.1rem 0.6rem;
}
.title-zh { color: var(--accent); font-weight: 650; }
.authors, small { color: var(--muted); }
dl { display: grid; grid-template-columns: 7rem 1fr; gap: 0.6rem 1rem; }
dt { font-weight: 700; }
dd { margin: 0; }
footer { border-top: 1px solid var(--line); color: var(--muted); padding: 2rem; text-align: center; }
@media (max-width: 42rem) {
  main { width: min(100% - 1rem, 76rem); }
  .site-header { align-items: flex-start; flex-direction: column; }
  dl { grid-template-columns: 1fr; }
}
```

- [ ] **Step 7: Run web tests and production build**

Run:

```bash
cd web
pnpm test
VLA_WAM_DATA_DIR=../tests/fixtures/data pnpm build
pnpm format:check
```

Expected: data-loader test passes; Astro and Pagefind complete; `web/dist/index.html` exists.

- [ ] **Step 8: Commit the first static site**

```bash
git add web
git commit -m "feat: render validated paper portal"
```

## Task 12: Add URL-backed filters and Pagefind search

**Files:**

- Create: `web/src/lib/filter.ts`
- Create: `web/src/lib/filter.test.ts`
- Create: `web/src/components/PaperExplorer.astro`
- Create: `web/src/components/SearchPanel.astro`
- Create: `web/src/pages/search.astro`
- Modify: `web/src/pages/index.astro`
- Modify: `web/src/pages/papers/[id].astro`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Write failing filter-state tests**

```typescript
// web/src/lib/filter.test.ts
import { describe, expect, it } from "vitest";
import { filterPapers, parseFilterState } from "./filter";

const papers = [
  {
    arxiv_id: "2607.1",
    title: "World Action Model",
    title_zh: "世界动作模型",
    authors: ["Ada"],
    abstract: "Robot world model",
    analysis: { primary_topic: "WAM", relevance_score: 9, tags: ["World Modeling"] },
    resources: { code_url: "https://github.com/example/wam" },
    published_at: "2026-07-27T00:00:00Z"
  },
  {
    arxiv_id: "2607.2",
    title: "VLA Policy",
    title_zh: "视觉语言动作策略",
    authors: ["Wei"],
    abstract: "Manipulation",
    analysis: { primary_topic: "VLA", relevance_score: 7, tags: ["Vision-Language"] },
    resources: { code_url: null },
    published_at: "2026-07-26T00:00:00Z"
  }
] as never[];

describe("filterPapers", () => {
  it("combines query, topic, minimum score, and code status", () => {
    const result = filterPapers(papers, {
      query: "world",
      topics: ["WAM"],
      minimumScore: 8,
      code: "yes",
      date: ""
    });
    expect(result.map((paper) => paper.arxiv_id)).toEqual(["2607.1"]);
  });

  it("parses shareable URL state", () => {
    const state = parseFilterState("?q=robot&topic=VLA&score=7&code=no&date=2026-07-26");
    expect(state).toEqual({
      query: "robot",
      topics: ["VLA"],
      minimumScore: 7,
      code: "no",
      date: "2026-07-26"
    });
  });
});
```

- [ ] **Step 2: Run the filter tests to verify they fail**

Run:

```bash
cd web
pnpm test -- src/lib/filter.test.ts
```

Expected: FAIL because `filter.ts` does not exist.

- [ ] **Step 3: Implement pure filtering and query-state helpers**

```typescript
// web/src/lib/filter.ts
import type { Paper, Topic } from "./schema";

export type FilterState = {
  query: string;
  topics: Topic[];
  minimumScore: number;
  code: "" | "yes" | "no";
  date: string;
};

export function parseFilterState(search: string): FilterState {
  const params = new URLSearchParams(search);
  const topics = params.getAll("topic").filter((topic): topic is Topic =>
    ["VLA", "WAM", "World Model", "Dataset", "Benchmark"].includes(topic)
  );
  const score = Number(params.get("score") ?? "6");
  const codeValue = params.get("code");
  return {
    query: params.get("q") ?? "",
    topics,
    minimumScore: Number.isInteger(score) && score >= 1 && score <= 10 ? score : 6,
    code: codeValue === "yes" || codeValue === "no" ? codeValue : "",
    date: params.get("date") ?? ""
  };
}

export function filterPapers(papers: Paper[], state: FilterState): Paper[] {
  const query = state.query.trim().toLocaleLowerCase();
  return papers.filter((paper) => {
    const searchable = [
      paper.title,
      paper.title_zh,
      paper.abstract,
      paper.authors.join(" "),
      paper.analysis.tags.join(" "),
      paper.analysis.one_sentence_summary,
      paper.analysis.main_contribution,
      paper.analysis.method,
      paper.analysis.key_results,
      paper.analysis.limitations,
      paper.analysis.relation_to_vla_wam
    ].join(" ").toLocaleLowerCase();
    return (
      (!query || searchable.includes(query)) &&
      (!state.topics.length || state.topics.includes(paper.analysis.primary_topic)) &&
      paper.analysis.relevance_score >= state.minimumScore &&
      (!state.code || (paper.resources.code_url ? "yes" : "no") === state.code) &&
      (!state.date || paper.published_at.startsWith(state.date))
    );
  });
}
```

- [ ] **Step 4: Implement the explorer and Pagefind search UI**

Add `data-id={paper.arxiv_id}` to the `<article>` in `PaperCard.astro`, then create
`web/src/components/PaperExplorer.astro`:

```astro
---
import PaperCard from "./PaperCard.astro";
import type { Paper, Topic } from "../lib/schema";

interface Props { papers: Paper[] }
const { papers } = Astro.props;
const topics: Topic[] = ["VLA", "WAM", "World Model", "Dataset", "Benchmark"];
const serialized = JSON.stringify(papers).replaceAll("<", "\\u003c");
---
<section data-explorer>
  <form class="filters" data-filter-form method="get">
    <label>搜索
      <input type="search" name="q" autocomplete="off" placeholder="标题、作者、方法…" />
    </label>
    <fieldset>
      <legend>主题</legend>
      {topics.map((topic) => (
        <label><input type="checkbox" name="topic" value={topic} /> {topic}</label>
      ))}
    </fieldset>
    <label>最低相关性
      <select name="score">
        {[6, 7, 8, 9, 10].map((score) => <option value={score}>{score}</option>)}
      </select>
    </label>
    <label>代码
      <select name="code">
        <option value="">全部</option>
        <option value="yes">有代码</option>
        <option value="no">未发现代码</option>
      </select>
    </label>
    <label>日期 <input type="date" name="date" /></label>
    <button type="reset">重置</button>
  </form>
  <p><strong data-result-count>{papers.length}</strong> 篇论文</p>
  <div class="paper-grid">
    {papers.map((paper) => <PaperCard paper={paper} />)}
  </div>
  <p class="empty-state" data-no-results hidden>没有符合当前条件的论文。</p>
  <script type="application/json" data-paper-json set:html={serialized}></script>
</section>

<script>
  import { filterPapers, parseFilterState, type FilterState } from "../lib/filter";
  import type { Paper, Topic } from "../lib/schema";

  const root = document.querySelector<HTMLElement>("[data-explorer]");
  const form = root?.querySelector<HTMLFormElement>("[data-filter-form]");
  const source = root?.querySelector<HTMLScriptElement>("[data-paper-json]");
  const papers = source ? (JSON.parse(source.textContent ?? "[]") as Paper[]) : [];

  function readForm(target: HTMLFormElement): FilterState {
    const data = new FormData(target);
    return {
      query: String(data.get("q") ?? ""),
      topics: data.getAll("topic").map(String) as Topic[],
      minimumScore: Number(data.get("score") ?? 6),
      code: String(data.get("code") ?? "") as "" | "yes" | "no",
      date: String(data.get("date") ?? "")
    };
  }

  function syncForm(target: HTMLFormElement, state: FilterState): void {
    const query = target.elements.namedItem("q") as HTMLInputElement;
    const score = target.elements.namedItem("score") as HTMLSelectElement;
    const code = target.elements.namedItem("code") as HTMLSelectElement;
    const date = target.elements.namedItem("date") as HTMLInputElement;
    query.value = state.query;
    score.value = String(state.minimumScore);
    code.value = state.code;
    date.value = state.date;
    target.querySelectorAll<HTMLInputElement>('input[name="topic"]').forEach((input) => {
      input.checked = state.topics.includes(input.value as Topic);
    });
  }

  function render(state: FilterState): void {
    if (!root || !form) return;
    const visible = new Set(filterPapers(papers, state).map((paper) => paper.arxiv_id));
    root.querySelectorAll<HTMLElement>("[data-paper-card]").forEach((card) => {
      card.hidden = !visible.has(card.dataset.id ?? "");
    });
    const count = root.querySelector<HTMLElement>("[data-result-count]");
    const empty = root.querySelector<HTMLElement>("[data-no-results]");
    if (count) count.textContent = String(visible.size);
    if (empty) empty.hidden = visible.size !== 0;
    const params = new URLSearchParams();
    if (state.query) params.set("q", state.query);
    state.topics.forEach((topic) => params.append("topic", topic));
    if (state.minimumScore !== 6) params.set("score", String(state.minimumScore));
    if (state.code) params.set("code", state.code);
    if (state.date) params.set("date", state.date);
    history.replaceState(null, "", `${location.pathname}${params.size ? `?${params}` : ""}`);
  }

  if (root && form) {
    const initial = parseFilterState(location.search);
    syncForm(form, initial);
    render(initial);
    form.addEventListener("input", () => render(readForm(form)));
    form.addEventListener("change", () => render(readForm(form)));
    form.addEventListener("reset", () => {
      queueMicrotask(() => {
        const clean = parseFilterState("");
        syncForm(form, clean);
        render(clean);
      });
    });
  }
</script>
```

Create `web/src/components/SearchPanel.astro`:

```astro
<section data-search-panel>
  <form class="filters" data-search-form>
    <label>全文搜索 <input type="search" name="q" required autocomplete="off" /></label>
    <label>主题
      <select name="topic">
        <option value="">全部</option>
        <option value="VLA">VLA</option>
        <option value="WAM">WAM</option>
        <option value="World Model">World Model</option>
        <option value="Dataset">Dataset</option>
        <option value="Benchmark">Benchmark</option>
      </select>
    </label>
    <label>代码
      <select name="code">
        <option value="">全部</option>
        <option value="yes">有代码</option>
        <option value="no">未发现代码</option>
      </select>
    </label>
    <label>最低相关性
      <select name="score">
        <option value="">全部</option>
        <option value="6">6</option>
        <option value="7">7</option>
        <option value="8">8</option>
        <option value="9">9</option>
        <option value="10">10</option>
      </select>
    </label>
    <label>日期 <input type="date" name="date" /></label>
    <button type="submit">搜索</button>
  </form>
  <p data-search-status>输入关键词开始搜索。</p>
  <ol class="search-results" data-search-results></ol>
</section>

<script>
  type PagefindResult = {
    url: string;
    meta: Record<string, string>;
    excerpt: string;
  };
  type PagefindModule = {
    search: (
      query: string,
      options: { filters: Record<string, string | { any: string[] }> }
    ) => Promise<{ results: Array<{ data: () => Promise<PagefindResult> }> }>;
    filters: () => Promise<Record<string, Record<string, number>>>;
  };

  const panel = document.querySelector<HTMLElement>("[data-search-panel]");
  const form = panel?.querySelector<HTMLFormElement>("[data-search-form]");
  const status = panel?.querySelector<HTMLElement>("[data-search-status]");
  const list = panel?.querySelector<HTMLOListElement>("[data-search-results]");
  const base = document.documentElement.dataset.base ?? "/";

  function appendResult(result: PagefindResult): void {
    if (!list) return;
    const item = document.createElement("li");
    const link = document.createElement("a");
    const chinese = document.createElement("p");
    const summary = document.createElement("p");
    link.href = result.url;
    link.textContent = result.meta.title ?? result.url;
    chinese.textContent = result.meta.title_zh ?? "";
    summary.textContent = result.meta.summary ?? result.excerpt;
    item.append(link, chinese, summary);
    list.append(item);
  }

  async function runSearch(): Promise<void> {
    if (!form || !status || !list) return;
    const data = new FormData(form);
    const query = String(data.get("q") ?? "").trim();
    if (!query) return;
    status.textContent = "搜索中…";
    list.replaceChildren();
    const pagefind = (await import(
      /* @vite-ignore */ `${base}pagefind/pagefind.js`
    )) as PagefindModule;
    const topic = String(data.get("topic") ?? "");
    const code = String(data.get("code") ?? "");
    const score = Number(data.get("score") ?? 0);
    const date = String(data.get("date") ?? "");
    const filters: Record<string, string | { any: string[] }> = {};
    if (topic) filters.topic = topic;
    if (code) filters.code = code;
    if (score) {
      filters.score = { any: [6, 7, 8, 9, 10].filter((value) => value >= score).map(String) };
    }
    if (date) filters.date = date;
    const response = await pagefind.search(query, { filters });
    const results = await Promise.all(response.results.map((result) => result.data()));
    results.forEach(appendResult);
    const available = await pagefind.filters();
    const totalTopics = Object.values(available.topic ?? {}).reduce((sum, count) => sum + count, 0);
    status.textContent = `${results.length} 条结果 · 索引包含 ${totalTopics} 个主题标记`;
    const params = new URLSearchParams({ q: query });
    if (topic) params.set("topic", topic);
    if (code) params.set("code", code);
    if (score) params.set("score", String(score));
    if (date) params.set("date", date);
    history.replaceState(null, "", `${location.pathname}?${params}`);
  }

  if (form) {
    const initial = new URLSearchParams(location.search);
    (form.elements.namedItem("q") as HTMLInputElement).value = initial.get("q") ?? "";
    (form.elements.namedItem("topic") as HTMLSelectElement).value = initial.get("topic") ?? "";
    (form.elements.namedItem("code") as HTMLSelectElement).value = initial.get("code") ?? "";
    (form.elements.namedItem("score") as HTMLSelectElement).value = initial.get("score") ?? "";
    (form.elements.namedItem("date") as HTMLInputElement).value = initial.get("date") ?? "";
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void runSearch();
    });
    if (initial.get("q")) void runSearch();
  }
</script>
```

Create `web/src/pages/search.astro`:

```astro
---
import SearchPanel from "../components/SearchPanel.astro";
import BaseLayout from "../layouts/BaseLayout.astro";
---
<BaseLayout title="Search · VLA/WAM Daily">
  <h1>Search</h1>
  <SearchPanel />
</BaseLayout>
```

Add this import to the existing `index.astro` frontmatter:

```typescript
import PaperExplorer from "../components/PaperExplorer.astro";
```

Remove the now-unused `PaperCard` import from `index.astro`.

Then replace the paper-list section with:

```astro
{papers.length ? <PaperExplorer papers={papers} /> : (
  <p class="empty-state">尚无符合发布阈值的论文。每日任务成功后会自动更新。</p>
)}
```

Add these named-slot metadata elements inside `BaseLayout` in `papers/[id].astro`:

```astro
<Fragment slot="head">
  <meta data-pagefind-meta="title" content={paper.title} />
  <meta data-pagefind-meta="title_zh" content={paper.title_zh} />
  <meta data-pagefind-meta="summary" content={paper.analysis.one_sentence_summary} />
  <meta data-pagefind-filter="topic" content={paper.analysis.primary_topic} />
  <meta data-pagefind-filter="score" content={String(paper.analysis.relevance_score)} />
  <meta data-pagefind-filter="code" content={paper.resources.code_url ? "yes" : "no"} />
  <meta data-pagefind-filter="date" content={paper.published_at.slice(0, 10)} />
</Fragment>
```

Append these styles to `global.css`:

```css
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.8rem;
  padding: 1rem;
  margin-bottom: 1rem;
  border: 1px solid var(--line);
  border-radius: 1rem;
  background: var(--paper-raised);
}
.filters label, .filters fieldset { display: grid; gap: 0.25rem; }
.filters fieldset { display: flex; flex-wrap: wrap; border: 0; margin: 0; padding: 0; }
.filters input, .filters select, .filters button {
  min-height: 2.5rem;
  border: 1px solid var(--line);
  border-radius: 0.55rem;
  color: var(--ink);
  background: var(--paper);
  padding: 0.4rem 0.65rem;
}
[hidden] { display: none !important; }
.search-results { display: grid; gap: 1rem; padding-left: 1.5rem; }
.search-results li { border-bottom: 1px solid var(--line); padding: 0 0 1rem 0.5rem; }
```

- [ ] **Step 5: Run unit tests and build the Pagefind index**

Run:

```bash
cd web
pnpm test
VLA_WAM_DATA_DIR=../tests/fixtures/data pnpm build
test -f dist/pagefind/pagefind.js
rg -n "data-pagefind-filter=\"topic\"" dist/papers
```

Expected: tests pass, Pagefind exists, and generated paper pages contain topic filters.

- [ ] **Step 6: Commit search and filters**

```bash
git add web/src
git commit -m "feat: add shareable paper search filters"
```

## Task 13: Add topic views, archive, Weekly Top 5, RSS, and methodology

**Files:**

- Create: `web/src/lib/weekly.ts`
- Create: `web/src/lib/weekly.test.ts`
- Create: `web/src/pages/topics/[topic].astro`
- Create: `web/src/pages/archive/index.astro`
- Create: `web/src/pages/archive/[month].astro`
- Create: `web/src/pages/weekly.astro`
- Create: `web/src/pages/methodology.astro`
- Create: `web/src/pages/rss.xml.ts`
- Create: `web/src/pages/404.astro`
- Modify: `web/src/components/Header.astro`

- [ ] **Step 1: Write the failing Weekly Top 5 test**

```typescript
// web/src/lib/weekly.test.ts
import { describe, expect, it } from "vitest";
import { selectWeeklyTop } from "./weekly";

describe("selectWeeklyTop", () => {
  it("returns five papers with no more than two from one topic", () => {
    const papers = [
      ["1", "VLA", 10], ["2", "VLA", 9], ["3", "VLA", 9],
      ["4", "WAM", 9], ["5", "World Model", 8], ["6", "Dataset", 8]
    ].map(([id, topic, score]) => ({
      arxiv_id: id,
      published_at: "2026-07-27T00:00:00Z",
      analysis: { primary_topic: topic, relevance_score: score }
    })) as never[];
    const selected = selectWeeklyTop(papers, new Date("2026-07-28T00:00:00Z"));
    expect(selected).toHaveLength(5);
    expect(selected.filter((paper) => paper.analysis.primary_topic === "VLA")).toHaveLength(2);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd web
pnpm test -- src/lib/weekly.test.ts
```

Expected: FAIL because `weekly.ts` does not exist.

- [ ] **Step 3: Implement deterministic, topic-balanced selection**

```typescript
// web/src/lib/weekly.ts
import type { Paper, Topic } from "./schema";

export function selectWeeklyTop(papers: Paper[], now = new Date()): Paper[] {
  const start = new Date(now);
  start.setUTCDate(start.getUTCDate() - 7);
  const eligible = papers
    .filter((paper) => new Date(paper.published_at) >= start && new Date(paper.published_at) <= now)
    .sort(
      (left, right) =>
        right.analysis.relevance_score - left.analysis.relevance_score ||
        Date.parse(right.published_at) - Date.parse(left.published_at) ||
        left.arxiv_id.localeCompare(right.arxiv_id)
    );
  const counts = new Map<Topic, number>();
  const selected: Paper[] = [];
  for (const paper of eligible) {
    const topic = paper.analysis.primary_topic;
    if ((counts.get(topic) ?? 0) >= 2) continue;
    selected.push(paper);
    counts.set(topic, (counts.get(topic) ?? 0) + 1);
    if (selected.length === 5) break;
  }
  return selected;
}
```

- [ ] **Step 4: Implement the remaining static routes**

Create `web/src/pages/topics/[topic].astro`:

```astro
---
import PaperExplorer from "../../components/PaperExplorer.astro";
import BaseLayout from "../../layouts/BaseLayout.astro";
import { loadArchive } from "../../lib/data";
import type { Paper, Topic } from "../../lib/schema";

const routes: Array<{ slug: string; topic: Topic; label: string }> = [
  { slug: "vla", topic: "VLA", label: "Vision-Language-Action" },
  { slug: "wam", topic: "WAM", label: "World Action Models" },
  { slug: "world-model", topic: "World Model", label: "World Models" },
  { slug: "dataset", topic: "Dataset", label: "Datasets" },
  { slug: "benchmark", topic: "Benchmark", label: "Benchmarks" }
];

export async function getStaticPaths() {
  const papers = await loadArchive();
  return routes.map((route) => ({
    params: { topic: route.slug },
    props: {
      label: route.label,
      papers: papers.filter((paper) => paper.analysis.primary_topic === route.topic)
    }
  }));
}

interface Props { label: string; papers: Paper[] }
const { label, papers } = Astro.props;
---
<BaseLayout title={`${label} · VLA/WAM Daily`}>
  <h1>{label}</h1>
  <PaperExplorer papers={papers} />
</BaseLayout>
```

Create `web/src/pages/archive/index.astro`:

```astro
---
import BaseLayout from "../../layouts/BaseLayout.astro";
import { loadArchive } from "../../lib/data";

const base = import.meta.env.BASE_URL;
const papers = await loadArchive();
const counts = new Map<string, number>();
for (const paper of papers) {
  const month = paper.published_at.slice(0, 7);
  counts.set(month, (counts.get(month) ?? 0) + 1);
}
const months = [...counts.entries()].sort(([left], [right]) => right.localeCompare(left));
---
<BaseLayout title="Archive · VLA/WAM Daily">
  <h1>Archive</h1>
  <ul class="archive-list">
    {months.map(([month, count]) => (
      <li><a href={`${base}archive/${month}/`}>{month}</a><span>{count} papers</span></li>
    ))}
  </ul>
</BaseLayout>
```

Create `web/src/pages/archive/[month].astro`:

```astro
---
import PaperCard from "../../components/PaperCard.astro";
import BaseLayout from "../../layouts/BaseLayout.astro";
import { loadArchive } from "../../lib/data";
import type { Paper } from "../../lib/schema";

export async function getStaticPaths() {
  const papers = await loadArchive();
  const months = new Map<string, Paper[]>();
  for (const paper of papers) {
    const month = paper.published_at.slice(0, 7);
    months.set(month, [...(months.get(month) ?? []), paper]);
  }
  return [...months.entries()].map(([month, entries]) => ({
    params: { month },
    props: { month, papers: entries }
  }));
}

interface Props { month: string; papers: Paper[] }
const { month, papers } = Astro.props;
const days = new Map<string, Paper[]>();
for (const paper of papers) {
  const day = paper.published_at.slice(0, 10);
  days.set(day, [...(days.get(day) ?? []), paper]);
}
const sections = [...days.entries()].sort(([left], [right]) => right.localeCompare(left));
---
<BaseLayout title={`${month} Archive · VLA/WAM Daily`}>
  <h1>{month}</h1>
  {sections.map(([day, entries]) => (
    <section>
      <h2>{day}</h2>
      <div class="paper-grid">{entries.map((paper) => <PaperCard paper={paper} compact />)}</div>
    </section>
  ))}
</BaseLayout>
```

Create `web/src/pages/weekly.astro`:

```astro
---
import PaperCard from "../components/PaperCard.astro";
import BaseLayout from "../layouts/BaseLayout.astro";
import { loadArchive } from "../lib/data";
import { selectWeeklyTop } from "../lib/weekly";

const papers = selectWeeklyTop(await loadArchive());
---
<BaseLayout title="Weekly Top 5 · VLA/WAM Daily">
  <h1>Weekly Top 5</h1>
  <p>按相关性、发布日期和 arXiv ID 确定性排序；同一主分类最多入选两篇。</p>
  <div class="paper-grid">{papers.map((paper) => <PaperCard paper={paper} />)}</div>
</BaseLayout>
```

Create `web/src/pages/methodology.astro`:

```astro
---
import BaseLayout from "../layouts/BaseLayout.astro";
import { loadLatestDataFile } from "../lib/data";

const latest = await loadLatestDataFile();
const updated = latest.generated_at.slice(0, 10);
---
<BaseLayout title="Methodology · VLA/WAM Daily">
  <article class="prose">
    <h1>Methodology</h1>
    <p>数据来自 arXiv 的 cs.RO、cs.CV、cs.AI 和 cs.LG，默认每天北京时间 10:30 更新。</p>
    <p>当前数据版本生成于 {updated}。</p>
    <h2>两级筛选</h2>
    <p>本地规则先匹配完整概念或“模型概念 + 机器人动作”组合，再由 DeepSeek 评分。</p>
    <h2>评分</h2>
    <ul>
      <li>9–10：VLA、WAM 或机器人动作世界模型是论文核心。</li>
      <li>7–8：强相关方法、数据集、评测或通用机器人策略。</li>
      <li>6：对该方向有直接价值的相邻研究。</li>
      <li>1–5：不在公开页面展示。</li>
    </ul>
    <h2>模型与证据</h2>
    <p>默认模型为 deepseek-v4-pro，发布阈值为 6。分析只使用标题与摘要。</p>
    <p>摘要没有提供的信息显示“摘要未说明”；代码、项目地址和机构不会由模型猜测。</p>
    <h2>反馈</h2>
    <p>误报、漏报和数据问题请通过项目仓库的 GitHub Issues 反馈。</p>
  </article>
</BaseLayout>
```

Create `web/src/pages/404.astro`:

```astro
---
import BaseLayout from "../layouts/BaseLayout.astro";
const base = import.meta.env.BASE_URL;
---
<BaseLayout title="Not found · VLA/WAM Daily">
  <h1>404</h1>
  <p>没有找到这个页面。<a href={base}>返回首页</a></p>
</BaseLayout>
```

Create the RSS endpoint with the official helper:

```typescript
// web/src/pages/rss.xml.ts
import rss from "@astrojs/rss";
import type { APIContext } from "astro";
import { loadArchive } from "../lib/data";

export async function GET(context: APIContext) {
  const papers = (await loadArchive()).slice(0, 100);
  const base = import.meta.env.BASE_URL;
  return rss({
    title: "VLA/WAM Daily",
    description: "Daily VLA and World Action Model research",
    site: context.site!,
    customData: "<language>zh-CN</language>",
    items: papers.map((paper) => ({
      title: `${paper.title} / ${paper.title_zh}`,
      description: paper.analysis.one_sentence_summary,
      pubDate: new Date(paper.published_at),
      link: `${base}papers/${paper.arxiv_id}/`
    }))
  });
}
```

Replace the `links` array in `Header.astro` with:

```typescript
const links = [
  ["Today", ""],
  ["VLA", "topics/vla/"],
  ["WAM", "topics/wam/"],
  ["World Models", "topics/world-model/"],
  ["Datasets", "topics/dataset/"],
  ["Benchmarks", "topics/benchmark/"],
  ["Weekly Top 5", "weekly/"],
  ["Archive", "archive/"],
  ["Search", "search/"],
  ["RSS", "rss.xml"],
  ["Methodology", "methodology/"]
];
```

- [ ] **Step 5: Run all web tests and build**

Run:

```bash
cd web
pnpm test
VLA_WAM_DATA_DIR=../tests/fixtures/data pnpm build
pnpm format:check
test -f dist/rss.xml
test -f dist/weekly/index.html
test -f dist/methodology/index.html
```

Expected: every command exits 0 and all three files exist.

- [ ] **Step 6: Commit the complete information architecture**

```bash
git add web/src
git commit -m "feat: add archives weekly picks and RSS"
```

## Task 14: Add browser smoke tests

**Files:**

- Create: `web/playwright.config.ts`
- Create: `web/tests/site.spec.ts`
- Modify: `web/package.json`

- [ ] **Step 1: Write smoke tests for desktop and mobile**

```typescript
// web/tests/site.spec.ts
import { expect, test } from "@playwright/test";

test("home exposes research cards and filters", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /VLA\/WAM Daily/ })).toBeVisible();
  await expect(page.getByText("AI 分析仅基于标题与摘要").first()).toBeVisible();
  await page.getByLabel("最低相关性").selectOption("8");
  await expect(page.locator("[data-paper-card]:visible")).toHaveCount(1);
});

test("filter state survives reload", async ({ page }) => {
  await page.goto("/?topic=VLA&score=7");
  await expect(page.getByLabel("VLA")).toBeChecked();
  await page.reload();
  await expect(page.getByLabel("VLA")).toBeChecked();
});

test("Pagefind searches English and Chinese paper content", async ({ page }) => {
  await page.goto("/search/?q=vision");
  await expect(page.locator("[data-search-status]")).toContainText("1 条结果");
  await expect(page.locator("[data-search-results] a")).toContainText(
    "A Vision-Language-Action Policy"
  );
  await page.getByLabel("全文搜索").fill("视觉语言动作");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.locator("[data-search-status]")).toContainText("1 条结果");
});

test("mobile navigation and paper detail work", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.locator("[data-paper-card] h2 a").first().click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: "PDF" })).toBeVisible();
});
```

- [ ] **Step 2: Configure Playwright and add the test script**

```typescript
// web/playwright.config.ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  use: { baseURL: "http://127.0.0.1:4321" },
  webServer: {
    command: "pnpm preview --host 127.0.0.1",
    port: 4321,
    reuseExistingServer: true
  }
});
```

Add this script to `web/package.json`:

```json
"test:e2e": "playwright test"
```

- [ ] **Step 3: Run browser tests against isolated fixture data**

Run:

```bash
cd web
pnpm exec playwright install chromium
VLA_WAM_DATA_DIR=../tests/fixtures/data pnpm build
pnpm test:e2e
```

Expected: four Playwright tests pass on Chromium.

- [ ] **Step 4: Commit browser coverage**

```bash
git add web/package.json web/pnpm-lock.yaml web/playwright.config.ts web/tests
git commit -m "test: cover portal browsing flows"
```

## Task 15: Add CI and protected Pages deployment workflows

**Files:**

- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/pages.yml`
- Create: `.github/workflows/daily.yml`

- [ ] **Step 1: Add CI for both runtimes**

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
        with:
          enable-cache: true
          python-version: "3.13"
      - run: uv sync --frozen
      - run: uv run ruff check src tests
      - run: uv run mypy
      - run: uv run pytest --cov=vla_wam_daily --cov-report=term-missing

  web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: pnpm/action-setup@v4
        with:
          version: "10"
      - uses: actions/setup-node@v6
        with:
          node-version: "24"
          cache: pnpm
          cache-dependency-path: web/pnpm-lock.yaml
      - run: pnpm install --frozen-lockfile
        working-directory: web
      - run: pnpm test
        working-directory: web
      - run: pnpm format:check
        working-directory: web
      - run: pnpm build
        working-directory: web
```

- [ ] **Step 2: Add deploy-without-AI workflow**

```yaml
# .github/workflows/pages.yml
name: Deploy Pages

on:
  push:
    branches: [main]
    paths:
      - "web/**"
      - "data/**"
      - "config/**"
      - "prompts/**"
      - ".github/workflows/pages.yml"
  workflow_dispatch:

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v7
      - uses: withastro/action@v6
        with:
          path: web
          node-version: "24"
          package-manager: pnpm@10

  deploy:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy
        id: deployment
        uses: actions/deploy-pages@v5
```

- [ ] **Step 3: Add the scheduled update and same-run deployment**

```yaml
# .github/workflows/daily.yml
name: Daily arXiv Update

on:
  schedule:
    - cron: "30 2 * * *"
  workflow_dispatch:
    inputs:
      lookback_days:
        description: Days to query
        type: number
        default: 3
      profile:
        description: DeepSeek model profile
        type: choice
        options: [quality, economy]
        default: quality
      threshold:
        description: Publication threshold
        type: number
        default: 6
      force_arxiv_id:
        description: Optional arXiv ID to reanalyze
        type: string
        required: false
      dry_run:
        description: Analyze without commit or deploy
        type: boolean
        default: false

concurrency:
  group: daily-data-update
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    outputs:
      dry_run: ${{ steps.options.outputs.dry_run }}
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
        with:
          enable-cache: true
          python-version: "3.13"
      - run: uv sync --frozen
      - name: Resolve options
        id: options
        shell: bash
        run: |
          echo "lookback_days=${{ inputs.lookback_days || 3 }}" >> "$GITHUB_OUTPUT"
          echo "profile=${{ inputs.profile || 'quality' }}" >> "$GITHUB_OUTPUT"
          echo "threshold=${{ inputs.threshold || 6 }}" >> "$GITHUB_OUTPUT"
          echo "dry_run=${{ inputs.dry_run || false }}" >> "$GITHUB_OUTPUT"
      - name: Run pipeline
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
          DEEPSEEK_MODEL: ${{ vars.DEEPSEEK_MODEL }}
          ARXIV_USER_AGENT: >-
            VLA-WAM-Daily/0.1
            (https://github.com/${{ github.repository }})
        shell: bash
        run: |
          args=(
            daily
            --lookback-days "${{ steps.options.outputs.lookback_days }}"
            --profile "${{ steps.options.outputs.profile }}"
            --threshold "${{ steps.options.outputs.threshold }}"
          )
          if [[ "${{ steps.options.outputs.dry_run }}" == "true" ]]; then
            args+=(--dry-run)
          fi
          if [[ -n "${{ inputs.force_arxiv_id }}" ]]; then
            args+=(--force-arxiv-id "${{ inputs.force_arxiv_id }}")
          fi
          uv run vla-wam-daily "${args[@]}" | tee run-report.json
          {
            echo "## VLA/WAM Daily"
            echo '```json'
            cat run-report.json
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"
      - name: Validate before commit
        if: steps.options.outputs.dry_run != 'true'
        run: |
          uv run pytest
          uv run python -c "from pathlib import Path; from vla_wam_daily.storage import load_data_file; assert load_data_file(Path('data/latest.json'))"
      - name: Commit generated data
        if: steps.options.outputs.dry_run != 'true'
        shell: bash
        run: |
          git config user.name "vla-wam-daily-bot"
          git config user.email "vla-wam-daily-bot@users.noreply.github.com"
          git add data
          if git diff --cached --quiet; then
            echo "No data changes"
          else
            git commit -m "data: update daily papers"
            git pull --rebase origin main
            git push origin HEAD:main
          fi

  build:
    needs: update
    if: needs.update.outputs.dry_run != 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v7
        with:
          ref: main
      - uses: withastro/action@v6
        with:
          path: web
          node-version: "24"
          package-manager: pnpm@10

  deploy:
    needs: [update, build]
    if: needs.update.outputs.dry_run != 'true'
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    concurrency:
      group: pages
      cancel-in-progress: false
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy
        id: deployment
        uses: actions/deploy-pages@v5
```

- [ ] **Step 4: Lint workflow YAML and verify schedules and permissions**

Run:

```bash
uvx yamllint -d '{extends: default, rules: {line-length: disable, truthy: disable}}' .github/workflows
rg -n 'cron: "30 2 \\* \\* \\*"' .github/workflows/daily.yml
rg -n 'DEEPSEEK_API_KEY: \\$\\{\\{ secrets.DEEPSEEK_API_KEY \\}\\}' .github/workflows/daily.yml
```

Expected: YAML lint exits 0 and both `rg` commands find exactly one match.

- [ ] **Step 5: Commit automation**

```bash
git add .github/workflows
git commit -m "ci: automate daily Pages updates"
```

## Task 16: Add public documentation, license, and deployment checklist

**Files:**

- Create: `README.md`
- Create: `LICENSE`
- Create: `.github/dependabot.yml`
- Modify: `docs/superpowers/specs/2026-07-27-vla-wam-daily-design.md`

- [ ] **Step 1: Write the README with reproducible commands**

The README must contain these sections and exact commands:

````markdown
# VLA/WAM Daily

Daily research tracking for Vision-Language-Action and World Action Models.

## What it publishes

The site keeps original English metadata and adds Chinese, abstract-only AI analysis:
relevance, topic, one-sentence summary, contribution, method, reported results,
reported limitations, and relation to VLA/WAM.

AI text is generated from titles and abstracts only. Missing evidence is shown as
“摘要未说明”; code and project URLs are extracted only from arXiv metadata.

## Local setup

```bash
uv sync
cd web
pnpm install
```

Run Python checks:

```bash
uv run ruff check src tests
uv run mypy
uv run pytest
```

Build the site:

```bash
cd web
pnpm test
pnpm build
pnpm preview
```

Preview a real update without changing tracked data:

```bash
read -s -p "DeepSeek API key: " DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY
uv run vla-wam-daily daily --dry-run
```

## GitHub configuration

1. Create a public repository and push the `main` branch.
2. Add `DEEPSEEK_API_KEY` under Settings → Secrets and variables → Actions.
3. Optionally add the repository variable `DEEPSEEK_MODEL` to override the selected profile.
4. Under Settings → Pages, choose **GitHub Actions** as the source.
5. Run **Daily arXiv Update** once with `dry_run=true`.
6. Run it again with `dry_run=false`; the successful run publishes the site.

The default schedule is 02:30 UTC (10:30 Asia/Shanghai). The default model is
`deepseek-v4-pro`; choose the `economy` profile to use `deepseek-v4-flash`.

## Configuration

Edit `config/topics.yaml` for categories, deterministic prefilter rules, publication
threshold, concurrency, model profiles, and the 60-candidate cost guard.
Prompt changes require a new file and an incremented `prompt_version`.

## Failure behavior

arXiv and DeepSeek transient failures retry with backoff. Invalid AI output is not
published. If more than 30% of new analyses fail, data is not committed and the
previous GitHub Pages deployment remains live.

## Data and attribution

Paper metadata comes from arXiv. This independent implementation was informed by
publicly visible arXiv-daily projects including `nlp-arxiv-daily`,
`daily-arXiv-ai-enhanced`, `vlm-arxiv-daily`, and `cv-arxiv-daily`; no unlicensed
source code was copied.

## License

MIT
````

- [ ] **Step 2: Add the MIT license**

```text
MIT License

Copyright (c) 2026 VLA/WAM Daily contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 3: Add monthly dependency updates**

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: uv
    directory: /
    schedule:
      interval: monthly
  - package-ecosystem: npm
    directory: /web
    schedule:
      interval: monthly
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: monthly
```

- [ ] **Step 4: Mark the design as implemented only after acceptance**

Do not change the design status during coding. After Task 17 passes, change:

```text
状态：已实现并验收
```

- [ ] **Step 5: Run documentation and secret scans**

Run:

```bash
rg -n "DEEPSEEK_API_KEY" README.md .github src
rg -n "sk-[A-Za-z0-9_-]{12,}|Bearer [A-Za-z0-9_-]{12,}" . --glob '!work/**' --glob '!.git/**'
git diff --check
```

Expected: the first command finds only variable references; the second finds no secrets;
`git diff --check` exits 0.

- [ ] **Step 6: Commit documentation**

```bash
git add README.md LICENSE .github/dependabot.yml
git commit -m "docs: document deployment and limitations"
```

## Task 17: Run final verification and publish to GitHub Pages

**Files:**

- Modify after successful acceptance: `docs/superpowers/specs/2026-07-27-vla-wam-daily-design.md`
- No additional source files expected

- [ ] **Step 1: Run the complete local verification suite**

Run:

```bash
uv sync --frozen
uv run ruff check src tests
uv run mypy
uv run pytest --cov=vla_wam_daily --cov-report=term-missing
cd web
pnpm install --frozen-lockfile
pnpm test
pnpm format:check
pnpm build
VLA_WAM_DATA_DIR=../tests/fixtures/data pnpm build
pnpm exec playwright install chromium
pnpm test:e2e
cd ..
git diff --check
git status --short
```

Expected: every check passes. `git status --short` contains no unexpected source changes.

- [ ] **Step 2: Perform a real DeepSeek dry run**

After the user has set `DEEPSEEK_API_KEY` in their local environment, run:

```bash
uv run vla-wam-daily daily --dry-run --lookback-days 3 --profile quality --threshold 6
```

Expected: JSON report with `dry_run: true`, no Secret in output, and no tracked `data/` changes.
If arXiv returns no matching paper in the three-day window, force the verified VLA paper
“Training Vision-Language-Action Models with Dense Embodied Chain-of-Thought Supervision”:

```bash
uv run vla-wam-daily daily --dry-run --force-arxiv-id 2606.30552
```

- [ ] **Step 3: Create and push the public repository**

First verify GitHub authentication:

```bash
gh auth status
```

Then create the repository under the authenticated account:

```bash
gh repo create vla-wam-daily --public --source=. --remote=origin --push
```

Expected: `origin` points to the new repository and `main` is visible on GitHub.

- [ ] **Step 4: Configure the Secret and Pages source**

Ask the user to enter the DeepSeek key through the GitHub UI at:

`Settings → Secrets and variables → Actions → New repository secret → DEEPSEEK_API_KEY`

Enable GitHub Actions as the Pages source:

```bash
repository="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
gh api --method POST "repos/${repository}/pages" -f build_type=workflow ||
  gh api --method PUT "repos/${repository}/pages" -f build_type=workflow
```

Expected: the repository Pages configuration reports `build_type: workflow`.

- [ ] **Step 5: Trigger a dry run, then the publishing run**

```bash
gh workflow run daily.yml -f dry_run=true -f profile=quality -f lookback_days=3 -f threshold=6
gh run watch "$(gh run list --workflow daily.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
gh workflow run daily.yml -f dry_run=false -f profile=quality -f lookback_days=3 -f threshold=6
gh run watch "$(gh run list --workflow daily.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```

Expected: both runs succeed; the second run creates a Pages deployment.

- [ ] **Step 6: Verify the production site**

Derive the project URL and verify key assets:

```bash
owner="$(gh api user --jq .login)"
site_url="https://${owner}.github.io/vla-wam-daily/"
curl --fail --location "${site_url}"
curl --fail --location "${site_url}rss.xml"
curl --fail --location "${site_url}pagefind/pagefind.js"
```

Expected: all three requests return HTTP 200.

Open the production site and confirm:

- Today contains the latest successfully published papers.
- Topic, score, code, date, and text filters work.
- Reload preserves URL-backed filter state.
- Paper detail pages show provenance and resource links.
- Archive, Weekly Top 5, Methodology, RSS, dark mode, and mobile layout work.
- No page claims to use PDF full text or inferred affiliations.

- [ ] **Step 7: Mark acceptance and commit**

Change the design status to `状态：已实现并验收`, then run:

```bash
git add docs/superpowers/specs/2026-07-27-vla-wam-daily-design.md
git commit -m "docs: mark VLA WAM Daily accepted"
git push origin main
```

Expected: the final documentation commit is on `origin/main`, CI passes, and the Pages URL remains
healthy.
