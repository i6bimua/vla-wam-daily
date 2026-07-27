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
