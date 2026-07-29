from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

NonEmptyStr = Annotated[str, Field(min_length=1)]
NonEmptyStrList = Annotated[list[NonEmptyStr], Field(min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
ARXIV_FIGURE_HOSTS = frozenset({"arxiv.org", "www.arxiv.org"})
FigureNumber = Literal[1, 2]
FigureImageList = Annotated[list[HttpUrl], Field(min_length=1)]


def normalize_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(normalize_utc)]


def validate_arxiv_https_url(url: HttpUrl) -> HttpUrl:
    if url.scheme != "https" or url.host not in ARXIV_FIGURE_HOSTS:
        raise ValueError("URL must use https and an allowed arXiv host")
    return url


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
    published_at: UtcDatetime
    updated_at: UtcDatetime
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


class FigureStatus(StrEnum):
    AVAILABLE = "available"
    HTML_UNAVAILABLE = "html_unavailable"
    NOT_FOUND = "not_found"
    FETCH_FAILED = "fetch_failed"


class FigureAsset(StrictModel):
    number: FigureNumber
    label: NonEmptyStr
    caption: NonEmptyStr
    image_urls: FigureImageList
    source_url: HttpUrl
    source: Literal["arxiv_html"] = "arxiv_html"

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, url: HttpUrl) -> HttpUrl:
        return validate_arxiv_https_url(url)

    @field_validator("image_urls")
    @classmethod
    def validate_and_deduplicate_image_urls(cls, urls: list[HttpUrl]) -> list[HttpUrl]:
        deduplicated: list[HttpUrl] = []
        seen: set[str] = set()
        for url in urls:
            validate_arxiv_https_url(url)
            if str(url) not in seen:
                seen.add(str(url))
                deduplicated.append(url)
        return deduplicated


class FigureGallery(StrictModel):
    status: FigureStatus
    html_url: HttpUrl
    figures: Annotated[list[FigureAsset], Field(max_length=2)] = Field(default_factory=list)
    checked_at: UtcDatetime

    @field_validator("html_url")
    @classmethod
    def validate_html_url(cls, url: HttpUrl) -> HttpUrl:
        return validate_arxiv_https_url(url)

    @field_validator("figures")
    @classmethod
    def validate_and_sort_figures(cls, figures: list[FigureAsset]) -> list[FigureAsset]:
        numbers = [figure.number for figure in figures]
        if len(numbers) != len(set(numbers)):
            raise ValueError("duplicate figure numbers")
        return sorted(figures, key=lambda figure: figure.number)

    @model_validator(mode="after")
    def validate_status_figures(self) -> Self:
        if self.status is FigureStatus.AVAILABLE and not self.figures:
            raise ValueError("available figure gallery requires at least one figure")
        if self.status is not FigureStatus.AVAILABLE and self.figures:
            raise ValueError("unavailable figure gallery must not contain figures")
        return self


class Provenance(StrictModel):
    analysis_scope: Literal["title_and_abstract"]
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    analyzed_at: UtcDatetime


class PaperRecord(StrictModel):
    arxiv_id: str = Field(pattern=r"^\d{4}\.\d{4,5}$")
    version: int = Field(ge=1)
    published_at: UtcDatetime
    updated_at: UtcDatetime
    title: NonEmptyStr
    title_zh: NonEmptyStr
    authors: NonEmptyStrList
    arxiv_categories: NonEmptyStrList
    abstract: NonEmptyStr
    matched_rules: NonEmptyStrList
    analysis: Analysis
    resources: Resources
    provenance: Provenance
    figure_gallery: FigureGallery | None = None


class TokenUsage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class RunStats(StrictModel):
    fetched: int = Field(default=0, ge=0)
    prefiltered: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    figure_cache_hits: int = Field(default=0, ge=0)
    figure_requests: int = Field(default=0, ge=0)
    figure_available: int = Field(default=0, ge=0)
    figure_unavailable: int = Field(default=0, ge=0)
    figure_failed: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    published: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    error_categories: dict[str, NonNegativeInt] = Field(default_factory=dict)


class DataFile(StrictModel):
    schema_version: Literal["1"] = "1"
    generated_at: UtcDatetime
    stats: RunStats
    papers: list[PaperRecord]

    @model_validator(mode="after")
    def reject_unchecked_figure_galleries(self) -> Self:
        unchecked_ids = sorted(
            paper.arxiv_id for paper in self.papers if paper.figure_gallery is None
        )
        if unchecked_ids:
            raise ValueError(
                "public data file contains papers with unchecked figure galleries: "
                + ", ".join(unchecked_ids)
            )
        return self


class CacheEntry(StrictModel):
    key: NonEmptyStr
    record: PaperRecord


class FigureCacheEntry(StrictModel):
    key: NonEmptyStr
    gallery: FigureGallery
